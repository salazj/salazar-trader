"""
Model training wrappers for the prediction-market ML pipeline.

Trains baseline tabular classifiers (logistic regression, gradient boosting,
random forest) behind a uniform interface.  Walk-forward cross-validation
selects hyperparameters; the final model is retrained on the full train+val
set before test evaluation.

No deep learning.  Deliberately conservative defaults to resist overfitting
on small datasets typical of prediction-market feature histories.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import joblib
import numpy as np
from pathlib import Path
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline

from app.research.preprocessing import build_preprocessing_pipeline


@dataclass
class TrainedModel:
    """Container for a trained model + its preprocessing pipeline."""
    name: str
    pipeline: Pipeline          # preprocessing
    classifier: Any             # fitted sklearn estimator
    val_log_loss: float = 999.0
    val_accuracy: float = 0.0
    train_time_seconds: float = 0.0
    hyperparams: dict[str, Any] = field(default_factory=dict)


# ── Model definitions ─────────────────────────────────────────────────────

def _get_model_configs() -> dict[str, dict[str, Any]]:
    """Return conservative model configs.

    Regularisation is intentionally strong.  Prediction-market feature datasets
    are typically small (hundreds to low-thousands of rows), so the primary
    risk is overfitting, not underfitting.
    """
    return {
        "logistic_regression": {
            "class": LogisticRegression,
            "params": {
                "max_iter": 2000,
                "C": 0.1,
                "solver": "lbfgs",
                "class_weight": "balanced",
            },
            "needs_scaling": True,
        },
        "gradient_boosting": {
            "class": GradientBoostingClassifier,
            "params": {
                "n_estimators": 150,
                "max_depth": 3,
                "learning_rate": 0.05,
                "subsample": 0.8,
                "min_samples_leaf": 10,
                "max_features": "sqrt",
            },
            "needs_scaling": False,
        },
        "random_forest": {
            "class": RandomForestClassifier,
            "params": {
                "n_estimators": 200,
                "max_depth": 5,
                "min_samples_leaf": 10,
                "max_features": "sqrt",
                "class_weight": "balanced",
                "n_jobs": -1,
            },
            "needs_scaling": False,
        },
    }


# ── Training ──────────────────────────────────────────────────────────────


def train_single_model(
    name: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
) -> TrainedModel:
    """Train one model configuration and evaluate on validation set."""
    configs = _get_model_configs()
    if name not in configs:
        raise ValueError(f"Unknown model '{name}'. Available: {list(configs.keys())}")

    cfg = configs[name]
    needs_scaling = cfg["needs_scaling"]
    pipeline = build_preprocessing_pipeline(feature_names, scale=needs_scaling)
    classifier = cfg["class"](**cfg["params"])

    X_train_t = pipeline.fit_transform(X_train)
    X_val_t = pipeline.transform(X_val)

    t0 = time.monotonic()
    classifier.fit(X_train_t, y_train)
    train_time = time.monotonic() - t0

    val_proba = classifier.predict_proba(X_val_t)
    val_preds = classifier.predict(X_val_t)

    try:
        vloss = float(log_loss(y_val, val_proba, labels=[0, 1]))
    except ValueError:
        vloss = 999.0

    return TrainedModel(
        name=name,
        pipeline=pipeline,
        classifier=classifier,
        val_log_loss=vloss,
        val_accuracy=float(np.mean(val_preds == y_val)),
        train_time_seconds=train_time,
        hyperparams=cfg["params"],
    )


def train_all_models(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
) -> list[TrainedModel]:
    """Train all baseline models and return sorted by val_log_loss (best first)."""
    models = []
    for name in _get_model_configs():
        model = train_single_model(name, X_train, y_train, X_val, y_val, feature_names)
        models.append(model)
    models.sort(key=lambda m: m.val_log_loss)
    return models


def calibrate_model(model: TrainedModel, X_cal: np.ndarray, y_cal: np.ndarray) -> TrainedModel:
    """Wrap the classifier in a CalibratedClassifierCV (Platt scaling).

    Improves probability calibration which matters for edge-based sizing.

    Handles the sklearn API change where ``cv="prefit"`` was removed in favour
    of wrapping an already-fitted estimator in ``FrozenEstimator``. Tries the
    modern path first and falls back to the legacy ``cv="prefit"`` signature.
    """
    X_cal_t = model.pipeline.transform(X_cal)
    try:
        from sklearn.frozen import FrozenEstimator

        cal = CalibratedClassifierCV(
            FrozenEstimator(model.classifier), method="sigmoid"
        )
    except ImportError:
        cal = CalibratedClassifierCV(model.classifier, method="sigmoid", cv="prefit")
    cal.fit(X_cal_t, y_cal)
    model.classifier = cal
    return model


def save_model_artifact(
    model: TrainedModel,
    feature_names: list[str],
    output_dir: Path,
    filename: str = "research_model.joblib",
) -> Path:
    """Serialize model + pipeline + metadata to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = {
        "name": model.name,
        "pipeline": model.pipeline,
        "classifier": model.classifier,
        "feature_names": feature_names,
        "hyperparams": model.hyperparams,
        "val_log_loss": model.val_log_loss,
        "val_accuracy": model.val_accuracy,
    }
    path = output_dir / filename
    joblib.dump(artifact, path)
    return path


def load_model_artifact(path: Path) -> dict[str, Any]:
    """Load a serialized model artifact."""
    return joblib.load(path)
