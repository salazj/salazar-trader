"""Tabular ML predictor for stock features.

Designed to be small enough to train and run on a Jetson Orin Nano:

* Default backbone is scikit-learn's ``GradientBoostingClassifier``
  (calibrated). XGBoost / LightGBM are used automatically if importable.
* Inputs come from ``StockFeatures`` — a fixed feature vector keeps
  inference deterministic and inspectable.
* Output is the structured :class:`StockMLPrediction`, with
  ``probability_up`` and ``confidence`` so the L2 layer can produce a
  signed direction signal.
"""

from __future__ import annotations

import math
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from pydantic import BaseModel, Field

from app.stocks.models import StockFeatures
from app.utils.helpers import utc_now


FEATURE_NAMES: list[str] = [
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "ema_9_minus_ema_21",
    "ema_21_minus_ema_50",
    "distance_from_vwap_pct",
    "atr_14_over_price",
    "volatility_score",
    "bb_pct_b",
    "momentum_1m",
    "momentum_5m",
    "momentum_15m",
    "relative_volume",
    "volume_surge_ratio",
    "trend_strength",
]


def extract_feature_vector(features: StockFeatures) -> np.ndarray:
    """Project a ``StockFeatures`` into a fixed feature vector."""
    last = features.last_price or 1.0
    vec = [
        features.rsi_14 / 100.0,
        features.macd_line / max(last, 1e-6),
        features.macd_signal / max(last, 1e-6),
        features.macd_hist / max(last, 1e-6),
        (features.ema_9 - features.ema_21) / max(last, 1e-6),
        (features.ema_21 - features.ema_50) / max(last, 1e-6),
        features.distance_from_vwap_pct / 100.0,
        features.atr_14 / max(last, 1e-6),
        features.volatility_score,
        features.bb_pct_b,
        features.momentum_1m,
        features.momentum_5m,
        features.momentum_15m,
        features.relative_volume,
        features.volume_surge_ratio,
        features.trend_strength,
    ]
    return np.asarray(vec, dtype=float)


class StockMLPrediction(BaseModel):
    """Schema produced by ``StockMLPredictor.predict``."""

    ticker: str
    probability_up: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str = "untrained"
    features_used: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)


# ── Predictor ─────────────────────────────────────────────────────────


class StockMLPredictor:
    """Wraps a fitted classifier and exposes a structured prediction.

    When no model is loaded, :meth:`predict` returns a neutral 0.5 / 0.0
    prediction so the L2 layer contributes zero signed direction. This
    means the rest of the pipeline runs even before ML training.
    """

    def __init__(
        self,
        model: Any | None = None,
        *,
        version: str = "untrained",
        feature_names: Sequence[str] = FEATURE_NAMES,
    ) -> None:
        self._model = model
        self._version = version
        self._features = list(feature_names)

    @classmethod
    def load(cls, path: str | Path) -> "StockMLPredictor":
        p = Path(path)
        if not p.exists():
            return cls(model=None, version="missing")
        try:
            with p.open("rb") as f:
                payload = pickle.load(f)
            return cls(
                model=payload.get("model"),
                version=payload.get("version", p.stem),
                feature_names=payload.get("feature_names", FEATURE_NAMES),
            )
        except Exception:
            return cls(model=None, version="load_error")

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as f:
            pickle.dump(
                {
                    "model": self._model,
                    "version": self._version,
                    "feature_names": self._features,
                },
                f,
            )

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    @property
    def version(self) -> str:
        return self._version

    @property
    def feature_names(self) -> list[str]:
        return list(self._features)

    def predict(self, features: StockFeatures) -> StockMLPrediction:
        if self._model is None:
            return StockMLPrediction(
                ticker=features.symbol,
                probability_up=0.5,
                confidence=0.0,
                model_version=self._version,
                features_used=self._features,
            )

        vec = extract_feature_vector(features).reshape(1, -1)
        proba_up = 0.5
        try:
            if hasattr(self._model, "predict_proba"):
                proba = self._model.predict_proba(vec)[0]
                proba_up = float(proba[-1])
            elif hasattr(self._model, "decision_function"):
                d = float(self._model.decision_function(vec)[0])
                proba_up = 1.0 / (1.0 + math.exp(-d))
            elif hasattr(self._model, "predict"):
                proba_up = float(self._model.predict(vec)[0])
        except Exception:
            proba_up = 0.5

        proba_up = max(0.0, min(1.0, proba_up))
        confidence = abs(proba_up - 0.5) * 2.0
        return StockMLPrediction(
            ticker=features.symbol,
            probability_up=proba_up,
            confidence=confidence,
            model_version=self._version,
            features_used=self._features,
        )


# ── Training helper ───────────────────────────────────────────────────


def train_baseline_model(
    X: np.ndarray,
    y: np.ndarray,
    *,
    version: str = "sklearn_gbm_v1",
    n_estimators: int = 200,
) -> StockMLPredictor:
    """Train a baseline classifier on the provided feature matrix.

    Tries XGBoost / LightGBM if available, else falls back to scikit-learn.
    The returned predictor stores its version string.
    """
    if X.shape[0] != y.shape[0]:
        raise ValueError("X and y must have the same number of rows")

    try:
        from xgboost import XGBClassifier  # type: ignore[import-not-found]
        model = XGBClassifier(
            n_estimators=n_estimators,
            max_depth=4,
            learning_rate=0.05,
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            n_jobs=2,
        )
        model.fit(X, y)
        return StockMLPredictor(model=model, version=f"xgb_{version}")
    except ImportError:
        pass

    try:
        from lightgbm import LGBMClassifier  # type: ignore[import-not-found]
        model = LGBMClassifier(
            n_estimators=n_estimators,
            max_depth=-1,
            learning_rate=0.05,
            n_jobs=2,
        )
        model.fit(X, y)
        return StockMLPredictor(model=model, version=f"lgbm_{version}")
    except ImportError:
        pass

    from sklearn.ensemble import GradientBoostingClassifier
    model = GradientBoostingClassifier(
        n_estimators=n_estimators, max_depth=3, learning_rate=0.05
    )
    model.fit(X, y)
    return StockMLPredictor(model=model, version=f"sklearn_{version}")
