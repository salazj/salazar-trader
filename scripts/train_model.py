#!/usr/bin/env python3
"""
Train the research ML model end-to-end.

Pipeline:
  1. Load historical features from the database (or generate synthetic data)
  2. Engineer extended features + prediction targets
  3. Temporal train/val/test split
  4. Walk-forward cross-validation for model selection
  5. Retrain best model on full train+val
  6. Calibrate probabilities
  7. Evaluate on held-out test set with profit simulation
  8. Save model artifact + training report

Usage:
  python scripts/train_model.py
  python scripts/train_model.py --db salazar-trader.db --horizon 6 --min-edge 0.02
  python scripts/train_model.py --synthetic --n-samples 3000
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
import numpy as np

from app.config.settings import get_settings
from app.monitoring import setup_logging
from app.research.dataset import (
    build_dataset,
    generate_synthetic_dataset,
    train_test_split_temporal,
    walk_forward_splits,
)
from app.research.evaluation import (
    EvaluationResult,
    evaluate_classifier,
    simulate_profit,
    walk_forward_evaluate,
)
from app.research.feature_eng import engineer_features, get_ml_feature_names
from app.research.models import (
    TrainedModel,
    calibrate_model,
    save_model_artifact,
    train_all_models,
    train_single_model,
)
from app.research.preprocessing import prepare_X
from app.research.report import (
    build_training_report,
    format_text_summary,
    save_report,
)
from app.research.targets import add_all_targets


@click.command()
@click.option("--db", default=None, help="SQLite database path (default: from settings)")
@click.option("--horizon", default=6, type=int, help="Look-ahead rows for target")
@click.option("--min-edge", default=0.02, type=float, help="Min price move in cents")
@click.option("--fee", default=0.02, type=float, help="Round-trip fee assumption")
@click.option("--synthetic", is_flag=True, help="Use synthetic data instead of DB")
@click.option("--n-samples", default=2000, type=int, help="Synthetic sample count")
@click.option("--n-folds", default=5, type=int, help="Walk-forward CV folds")
@click.option("--output-dir", default=None, help="Directory for model + report output")
def main(
    db: str | None,
    horizon: int,
    min_edge: float,
    fee: float,
    synthetic: bool,
    n_samples: int,
    n_folds: int,
    output_dir: str | None,
) -> None:
    """Train a baseline ML model for Polymarket short-horizon prediction."""
    setup_logging("INFO")
    settings = get_settings()
    settings.ensure_dirs()

    out_dir = Path(output_dir) if output_dir else settings.model_artifacts_dir
    reports_dir = settings.reports_dir

    # ── 1. Load data ──────────────────────────────────────────────────

    if synthetic:
        print(f"Generating {n_samples} synthetic samples...")
        df = generate_synthetic_dataset(n=n_samples)
        dataset_desc = f"Synthetic demo data ({n_samples} rows, seed=42)"
    else:
        db_path = db or settings.database_url
        print(f"Loading features from {db_path}...")
        df_loaded = build_dataset(
            db_path, horizon=horizon, min_edge=min_edge, fee=fee, min_rows=50
        )
        if df_loaded is not None and len(df_loaded) >= 50:
            df = df_loaded
            dataset_desc = f"Database: {db_path} ({len(df)} rows after processing)"
        else:
            rows = len(df_loaded) if df_loaded is not None else 0
            print(f"Only {rows} rows in DB. Falling back to synthetic data.")
            df = generate_synthetic_dataset(n=n_samples)
            dataset_desc = f"Synthetic fallback ({n_samples} rows, seed=42)"

    # ── 2. Feature engineering + targets ──────────────────────────────

    df = engineer_features(df)
    df = add_all_targets(df, horizon=horizon, min_edge=min_edge, fee=fee)
    df = df.dropna(subset=["target_direction"]).reset_index(drop=True)

    feature_names = get_ml_feature_names(include_external=False)
    X, feature_names = prepare_X(df, feature_names)
    y = df["target_direction"].values.astype(int)

    print(f"Dataset: {len(df)} samples, {len(feature_names)} features")
    print(f"Target class balance: 0={int((y==0).sum())}  1={int((y==1).sum())}  "
          f"({100*y.mean():.1f}% positive)")

    # ── 3. Temporal split ─────────────────────────────────────────────

    trainval_idx, test_idx = train_test_split_temporal(len(X), test_frac=0.15)
    X_trainval, y_trainval = X[trainval_idx], y[trainval_idx]
    X_test, y_test = X[test_idx], y[test_idx]

    print(f"Split: trainval={len(trainval_idx)}, test={len(test_idx)}")

    # ── 4. Walk-forward CV ────────────────────────────────────────────

    folds = walk_forward_splits(len(trainval_idx), n_folds=n_folds)
    print(f"Walk-forward: {len(folds)} folds")

    model_configs = ["logistic_regression", "gradient_boosting", "random_forest"]
    best_model_name = ""
    best_avg_loss = float("inf")
    wf_results_by_model: dict[str, list[EvaluationResult]] = {}

    for model_name in model_configs:
        fold_results: list[EvaluationResult] = []
        fold_losses: list[float] = []

        for i, fold in enumerate(folds):
            X_tr = X_trainval[fold.train_start:fold.train_end]
            y_tr = y_trainval[fold.train_start:fold.train_end]
            X_va = X_trainval[fold.val_start:fold.val_end]
            y_va = y_trainval[fold.val_start:fold.val_end]

            try:
                trained = train_single_model(
                    model_name, X_tr, y_tr, X_va, y_va, feature_names
                )
                X_va_t = trained.pipeline.transform(X_va)
                proba = trained.classifier.predict_proba(X_va_t)
                pred = trained.classifier.predict(X_va_t)
                ev = evaluate_classifier(y_va, proba, pred)
                fold_results.append(ev)
                fold_losses.append(ev.log_loss_val)
            except Exception as e:
                print(f"  WARNING: {model_name} fold {i} failed: {e}")

        if fold_losses:
            avg_loss = float(np.mean(fold_losses))
            print(f"  {model_name}: avg_log_loss={avg_loss:.4f} across {len(fold_losses)} folds")
            wf_results_by_model[model_name] = fold_results
            if avg_loss < best_avg_loss:
                best_avg_loss = avg_loss
                best_model_name = model_name

    if not best_model_name:
        print("ERROR: All models failed. Check data quality.")
        sys.exit(1)

    print(f"\nBest model by walk-forward: {best_model_name} (avg_loss={best_avg_loss:.4f})")

    # ── 5. Retrain on full trainval ───────────────────────────────────

    # Use 85% for training, last 15% of trainval for calibration
    cal_split = int(len(trainval_idx) * 0.85)
    X_train_full = X_trainval[:cal_split]
    y_train_full = y_trainval[:cal_split]
    X_cal = X_trainval[cal_split:]
    y_cal = y_trainval[cal_split:]

    final_model = train_single_model(
        best_model_name, X_train_full, y_train_full, X_cal, y_cal, feature_names
    )
    print(f"Retrained {best_model_name} on {len(X_train_full)} rows")

    # ── 6. Calibrate ─────────────────────────────────────────────────

    final_model = calibrate_model(final_model, X_cal, y_cal)
    print("Applied probability calibration (Platt scaling)")

    # ── 7. Test evaluation ────────────────────────────────────────────

    X_test_t = final_model.pipeline.transform(X_test)
    test_proba = final_model.classifier.predict_proba(X_test_t)
    test_pred = final_model.classifier.predict(X_test_t)
    eval_result = evaluate_classifier(y_test, test_proba, test_pred)

    print(f"\n{'='*60}")
    print(f"TEST SET RESULTS ({len(test_idx)} samples)")
    print(f"{'='*60}")
    print(f"  Accuracy:       {eval_result.accuracy:.4f}")
    print(f"  Log-loss:       {eval_result.log_loss_val:.4f}")
    print(f"  ROC-AUC:        {eval_result.roc_auc}")
    print(f"  Brier score:    {eval_result.brier_score:.4f}")
    print(f"  Precision @60%: {eval_result.precision_at_60:.4f}")
    print(f"  Recall @60%:    {eval_result.recall_at_60:.4f}")
    print(eval_result.classification_report_text)

    # ── 8. Profit simulation ──────────────────────────────────────────

    test_df = df.iloc[test_idx]
    entry_prices = test_df["best_ask"].fillna(test_df["mid_price"]).values
    exit_prices = test_df["mid_price"].shift(-horizon).fillna(test_df["mid_price"]).values

    profit_sim = simulate_profit(
        y_true=y_test,
        y_proba=test_proba,
        entry_prices=entry_prices,
        exit_prices=exit_prices,
        threshold=0.6,
        fee=fee,
    )

    print(f"\nProfit simulation (threshold=0.6, fee={fee}):")
    print(f"  Trades: {profit_sim['n_trades']}")
    print(f"  PnL:    {profit_sim['total_pnl']:.4f}")
    print(f"  Win%:   {profit_sim['win_rate']:.2%}")
    print(f"  Sharpe: {profit_sim['sharpe_like']:.3f}")

    # ── 9. Feature importance ─────────────────────────────────────────

    feature_importance: dict[str, float] = {}
    clf = final_model.classifier
    # Unwrap CalibratedClassifierCV to get the base estimator
    if hasattr(clf, "estimator"):
        base_clf = clf.estimator
    elif hasattr(clf, "calibrated_classifiers_"):
        base_clf = clf.calibrated_classifiers_[0].estimator
    else:
        base_clf = clf

    if hasattr(base_clf, "feature_importances_"):
        feature_importance = dict(zip(feature_names, base_clf.feature_importances_.tolist()))
    elif hasattr(base_clf, "coef_"):
        feature_importance = dict(zip(feature_names, np.abs(base_clf.coef_[0]).tolist()))

    if feature_importance:
        sorted_fi = sorted(feature_importance.items(), key=lambda x: -x[1])
        print("\nFeature importance (top 10):")
        for feat, imp in sorted_fi[:10]:
            print(f"  {feat:35s} {imp:+.4f}")

    # ── 10. Save artifacts + report ───────────────────────────────────

    model_path = save_model_artifact(final_model, feature_names, out_dir)
    print(f"\nModel saved: {model_path}")

    wf_summary = walk_forward_evaluate(wf_results_by_model.get(best_model_name, []))

    report = build_training_report(
        model_name=best_model_name,
        feature_names=feature_names,
        train_size=len(X_train_full),
        val_size=len(X_cal),
        test_size=len(test_idx),
        eval_result=eval_result,
        walk_forward_summary=wf_summary,
        feature_importance=feature_importance,
        hyperparams=final_model.hyperparams,
        profit_sim=profit_sim,
        dataset_description=dataset_desc,
    )

    report_path = reports_dir / "research_training_report.json"
    save_report(report, report_path)
    print(f"Report saved: {report_path}")

    text_report = format_text_summary(report)
    text_path = reports_dir / "research_training_report.txt"
    text_path.write_text(text_report)
    print(f"Text report: {text_path}")

    print(f"\n{text_report}")


if __name__ == "__main__":
    main()
