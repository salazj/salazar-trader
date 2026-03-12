"""
Event Probability Model Strategy

ML strategy using a tabular classifier to predict short-horizon
probability changes. Uses the research pipeline's trained model artifact
which includes a fitted preprocessing pipeline + calibrated classifier.

Pipeline:
1. Extract features from current market state
2. Apply the saved preprocessing pipeline (impute, scale, etc.)
3. Predict direction probability via the calibrated classifier
4. Emit signal only when prediction confidence exceeds threshold

The model is trained offline via scripts/train_model.py and loaded
from serialized artifacts at runtime.  Falls back to the legacy
artifact format (bare classifier without pipeline) if the new
format is not available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from app.config.settings import Settings
from app.data.models import MarketFeatures, PortfolioSnapshot, Signal, SignalAction
from app.monitoring import get_logger
from app.research.feature_eng import get_ml_feature_names
from app.strategies.base import BaseStrategy, StrategyRegistry

logger = get_logger(__name__)

PREDICTION_THRESHOLD = 0.6

# Legacy bare-model feature columns (no preprocessing pipeline)
LEGACY_FEATURE_COLUMNS = [
    "spread", "microprice", "orderbook_imbalance", "bid_depth_5c",
    "ask_depth_5c", "recent_trade_flow", "volatility_1m",
    "momentum_1m", "momentum_5m", "momentum_15m", "trade_count_1m",
]


@StrategyRegistry.register
class EventProbabilityModel(BaseStrategy):
    name = "event_probability_model"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._pipeline: Any = None
        self._classifier: Any = None
        self._feature_names: list[str] = []
        self._model_loaded = False
        self._load_model()

    def _load_model(self) -> None:
        """Load the research model artifact (pipeline + classifier)."""
        research_path = self.settings.model_artifacts_dir / "research_model.joblib"
        legacy_path = self.settings.model_artifacts_dir / "baseline_model.joblib"

        if research_path.exists():
            self._load_research_artifact(research_path)
        elif legacy_path.exists():
            self._load_legacy_artifact(legacy_path)
        else:
            logger.warning(
                "ml_model_not_found",
                hint="Run: python scripts/train_model.py",
            )

    def _load_research_artifact(self, path: Path) -> None:
        try:
            import joblib
            artifact = joblib.load(path)
            self._pipeline = artifact["pipeline"]
            self._classifier = artifact["classifier"]
            self._feature_names = artifact["feature_names"]
            self._model_loaded = True
            logger.info("research_model_loaded", path=str(path),
                        model=artifact.get("name", "unknown"),
                        n_features=len(self._feature_names))
        except Exception as e:
            logger.error("research_model_load_failed", error=str(e))

    def _load_legacy_artifact(self, path: Path) -> None:
        try:
            import joblib
            self._classifier = joblib.load(path)
            self._pipeline = None
            self._feature_names = LEGACY_FEATURE_COLUMNS
            self._model_loaded = True
            logger.info("legacy_model_loaded", path=str(path))
        except Exception as e:
            logger.error("legacy_model_load_failed", error=str(e))

    def generate_signal(
        self,
        features: MarketFeatures,
        portfolio: PortfolioSnapshot,
    ) -> Signal | None:
        if not self._model_loaded or self._classifier is None:
            return None

        if features.spread is None or features.best_bid is None or features.best_ask is None:
            return None

        mid = (features.best_bid + features.best_ask) / 2.0
        if mid < 0.20 or mid > 0.80:
            return None

        x = self._extract_feature_vector(features)
        if x is None:
            return None

        try:
            if self._pipeline is not None:
                x_transformed = self._pipeline.transform(x.reshape(1, -1))
            else:
                x_transformed = x.reshape(1, -1)

            proba = self._classifier.predict_proba(x_transformed)[0]
        except Exception as e:
            logger.error("ml_prediction_error", error=str(e))
            return None

        p_up = float(proba[1]) if len(proba) > 1 else float(proba[0])
        p_down = 1.0 - p_up

        if p_up > PREDICTION_THRESHOLD:
            action = SignalAction.BUY_YES
            confidence = p_up
            price = features.best_bid
        elif p_down > PREDICTION_THRESHOLD:
            action = SignalAction.SELL_YES
            confidence = p_down
            price = features.best_ask
        else:
            return None

        return Signal(
            strategy_name=self.name,
            market_id=features.market_id,
            token_id=features.instrument_id or features.token_id,
            instrument_id=features.instrument_id or features.token_id,
            exchange=features.exchange,
            action=action,
            confidence=confidence,
            suggested_price=price,
            suggested_size=self.settings.default_order_size,
            rationale=f"p_up={p_up:.3f} p_down={p_down:.3f} threshold={PREDICTION_THRESHOLD}",
        )

    def _extract_feature_vector(self, f: MarketFeatures) -> np.ndarray | None:
        """Convert MarketFeatures to a numpy array matching the model's expected columns."""
        values = []
        for col in self._feature_names:
            val = getattr(f, col, None)
            if val is None:
                val = 0.0
            values.append(float(val))
        return np.array(values, dtype=np.float64)
