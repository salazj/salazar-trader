"""
Ensemble aggregation with full transparency.

Every step — normalization, weighting, conflict detection, gating —
is recorded in the DecisionTrace so you can always inspect exactly
why a trade was or was not taken.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.data.models import SignalAction
from app.decision.signals import (
    DecisionTrace,
    IntelligenceLayer,
    LayerSummary,
    NormalizedSignal,
    TradeCandidate,
    Veto,
    VetoSource,
)
from app.utils.helpers import utc_now


# ── Decision mode ──────────────────────────────────────────────────────


class DecisionMode(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


_MODE_DEFAULTS: dict[DecisionMode, dict[str, Any]] = {
    DecisionMode.CONSERVATIVE: {
        "min_confidence": 0.50,
        "min_layers_agree": 1,
        "require_l1_approval": False,
        "min_evidence_signals": 1,
        "large_trade_threshold": 3.0,
        "large_trade_min_layers": 2,
        "conflict_tolerance": 0.15,
    },
    DecisionMode.BALANCED: {
        "min_confidence": 0.40,
        "min_layers_agree": 1,
        "require_l1_approval": False,
        "min_evidence_signals": 1,
        "large_trade_threshold": 5.0,
        "large_trade_min_layers": 2,
        "conflict_tolerance": 0.30,
    },
    DecisionMode.AGGRESSIVE: {
        "min_confidence": 0.30,
        "min_layers_agree": 1,
        "require_l1_approval": False,
        "min_evidence_signals": 1,
        "large_trade_threshold": 10.0,
        "large_trade_min_layers": 1,
        "conflict_tolerance": 0.50,
    },
}


# ── Configuration ──────────────────────────────────────────────────────


@dataclass
class EnsembleConfig:
    weight_l1: float = 0.30
    weight_l2: float = 0.40
    weight_l3: float = 0.30

    min_confidence: float = 0.50
    min_layers_agree: int = 1
    mode: DecisionMode = DecisionMode.CONSERVATIVE
    require_l1_approval: bool = False

    min_evidence_signals: int = 1
    large_trade_threshold: float = 3.0
    large_trade_min_layers: int = 3
    conflict_tolerance: float = 0.15

    def apply_mode_defaults(self) -> None:
        defaults = _MODE_DEFAULTS.get(self.mode, {})
        for k, v in defaults.items():
            setattr(self, k, v)


# ── Confidence normalization ───────────────────────────────────────────


def normalize_confidence(raw: float, layer: IntelligenceLayer) -> float:
    """Map raw confidence to a common [0, 1] range.

    Each layer may have different native confidence semantics:
      L1 rules:  0-1 from heuristic thresholds (often binary-ish)
      L2 ML:     calibrated probability from sklearn
      L3 NLP:    keyword-match score * relevance (often low)

    We apply a per-layer sigmoid squash so that a 0.7 from L2 and a 0.7
    from L1 mean roughly the same thing to the ensemble.
    """
    if raw <= 0.0:
        return 0.0
    if raw >= 1.0:
        return 1.0

    # Per-layer midpoint and steepness for the logistic squash
    _PARAMS: dict[IntelligenceLayer, tuple[float, float]] = {
        IntelligenceLayer.RULES: (0.50, 8.0),
        IntelligenceLayer.ML:    (0.55, 10.0),
        IntelligenceLayer.NLP:   (0.40, 6.0),
    }
    mid, k = _PARAMS.get(layer, (0.50, 8.0))
    return 1.0 / (1.0 + math.exp(-k * (raw - mid)))


# ── Layer scoring ──────────────────────────────────────────────────────


def summarize_layer(
    signals: list[NormalizedSignal],
    layer: IntelligenceLayer,
    weight: float,
) -> LayerSummary:
    """Produce a human-readable summary of one layer's signals."""
    if not signals:
        return LayerSummary(
            layer=layer,
            synopsis=f"{layer.value}: no signals",
        )

    dirs = [s.direction * s.normalized_confidence for s in signals]
    edges = [s.expected_edge for s in signals]
    n = len(signals)

    raw_score = sum(dirs) / n
    mean_conf = sum(s.normalized_confidence for s in signals) / n
    mean_edge = sum(edges) / n
    direction = 1 if raw_score > 0 else (-1 if raw_score < 0 else 0)

    actions = [s.action.value for s in signals]
    rationales = [s.rationale for s in signals if s.rationale]

    synopsis_parts = [
        f"{layer.value}: {n} signal(s)",
        f"dir={'BUY' if direction > 0 else 'SELL' if direction < 0 else 'HOLD'}",
        f"conf={mean_conf:.3f}",
        f"score={raw_score:+.3f}",
        f"weighted={raw_score * weight:+.3f}",
    ]
    if rationales:
        synopsis_parts.append(f"[{rationales[0][:60]}]")

    return LayerSummary(
        layer=layer,
        signal_count=n,
        direction=direction,
        mean_confidence=mean_conf,
        weighted_score=raw_score * weight,
        edge=mean_edge,
        signals=signals,
        synopsis=" | ".join(synopsis_parts),
    )


# ── Conflict detection ─────────────────────────────────────────────────


def detect_conflict(
    summaries: dict[str, LayerSummary],
    tolerance: float,
) -> tuple[bool, str]:
    """Return (is_conflict, description).

    A conflict exists when active layers disagree in direction and the
    net score magnitude is below *tolerance*, meaning neither side has
    a convincing majority.
    """
    active = {k: s for k, s in summaries.items() if s.signal_count > 0}
    if len(active) < 2:
        return False, "fewer than 2 active layers"

    directions = {k: s.direction for k, s in active.items() if s.direction != 0}
    if not directions:
        return False, "all layers neutral"

    unique_dirs = set(directions.values())
    if len(unique_dirs) <= 1:
        return False, "layers agree"

    # Layers disagree — check if the net score is weak
    net = sum(s.weighted_score for s in active.values())
    if abs(net) < tolerance:
        opposing = {k: d for k, d in directions.items()}
        return True, (
            f"layers disagree ({opposing}) and net score "
            f"{net:+.3f} is below tolerance {tolerance}"
        )
    return False, f"layers disagree but net score {net:+.3f} exceeds tolerance"


# ── Ensemble core ──────────────────────────────────────────────────────


def run_ensemble(
    market_id: str,
    token_id: str = "",
    l1_signals: list[NormalizedSignal] | None = None,
    l2_signals: list[NormalizedSignal] | None = None,
    l3_signals: list[NormalizedSignal] | None = None,
    config: EnsembleConfig | None = None,
    *,
    instrument_id: str = "",
    exchange: str = "",
) -> tuple[TradeCandidate, DecisionTrace]:
    """Full ensemble pipeline.  Returns (candidate, trace).

    Every step writes into the DecisionTrace so the caller can inspect
    exactly what happened and why.
    """
    l1_signals = l1_signals or []
    l2_signals = l2_signals or []
    l3_signals = l3_signals or []
    config = config or EnsembleConfig()
    iid = instrument_id or token_id
    now = utc_now()
    trace = DecisionTrace(
        market_id=market_id, token_id=iid, instrument_id=iid,
        exchange=exchange, timestamp=now,
    )
    vetoes: list[Veto] = []

    total_weight = config.weight_l1 + config.weight_l2 + config.weight_l3
    if total_weight == 0:
        total_weight = 1.0
    w1 = config.weight_l1 / total_weight
    w2 = config.weight_l2 / total_weight
    w3 = config.weight_l3 / total_weight

    # ── Step 1: Summarize each layer ───────────────────────────────
    s1 = summarize_layer(l1_signals, IntelligenceLayer.RULES, w1)
    s2 = summarize_layer(l2_signals, IntelligenceLayer.ML,    w2)
    s3 = summarize_layer(l3_signals, IntelligenceLayer.NLP,   w3)

    trace.level_1_summary = s1
    trace.level_2_summary = s2
    trace.level_3_summary = s3

    summaries = {"l1": s1, "l2": s2, "l3": s3}

    # ── Step 2: Weighted aggregation ───────────────────────────────
    weighted_scores = {"l1": s1.weighted_score, "l2": s2.weighted_score, "l3": s3.weighted_score}
    trace.weighted_scores = weighted_scores

    net_score = s1.weighted_score + s2.weighted_score + s3.weighted_score
    net_edge = w1 * s1.edge + w2 * s2.edge + w3 * s3.edge

    direction = 1 if net_score > 0 else (-1 if net_score < 0 else 0)
    confidence = min(abs(net_score), 1.0)

    trace.ensemble_direction = direction
    trace.ensemble_confidence = confidence
    trace.ensemble_edge = net_edge

    # ── Step 3: Minimum evidence threshold ─────────────────────────
    total_signals = len(l1_signals) + len(l2_signals) + len(l3_signals)
    if total_signals < config.min_evidence_signals:
        vetoes.append(Veto(
            source=VetoSource.INSUFFICIENT_EVIDENCE,
            reason=(
                f"only {total_signals} signal(s) present, "
                f"need {config.min_evidence_signals}"
            ),
            detail={"total_signals": total_signals, "required": config.min_evidence_signals},
        ))
        trace.evidence_result = f"FAIL: {total_signals} < {config.min_evidence_signals}"
    else:
        trace.evidence_result = f"PASS: {total_signals} >= {config.min_evidence_signals}"

    # ── Step 4: Confidence threshold ───────────────────────────────
    if confidence < config.min_confidence:
        vetoes.append(Veto(
            source=VetoSource.CONFIDENCE_THRESHOLD,
            reason=f"confidence {confidence:.3f} < threshold {config.min_confidence}",
            detail={"confidence": confidence, "threshold": config.min_confidence},
        ))
        trace.confidence_result = (
            f"FAIL: {confidence:.3f} < {config.min_confidence}"
        )
    else:
        trace.confidence_result = (
            f"PASS: {confidence:.3f} >= {config.min_confidence}"
        )

    # ── Step 5: Agreement gating ───────────────────────────────────
    active_layers = [k for k, s in summaries.items() if s.signal_count > 0]
    agreeing = [
        k for k in active_layers
        if summaries[k].direction == direction and summaries[k].direction != 0
    ]
    n_agreeing = len(agreeing)
    n_active = len(active_layers)

    if n_active >= config.min_layers_agree and n_agreeing < config.min_layers_agree:
        vetoes.append(Veto(
            source=VetoSource.AGREEMENT_GATE,
            reason=(
                f"only {n_agreeing}/{n_active} layers agree "
                f"(need {config.min_layers_agree})"
            ),
            detail={"agreeing": agreeing, "active": active_layers},
        ))
        trace.agreement_result = (
            f"FAIL: {n_agreeing}/{n_active} agree, need {config.min_layers_agree}"
        )
    else:
        trace.agreement_result = (
            f"PASS: {n_agreeing}/{n_active} agree "
            f"(need {config.min_layers_agree})"
        )

    # ── Step 6: Conflict detection ─────────────────────────────────
    is_conflict, conflict_desc = detect_conflict(summaries, config.conflict_tolerance)
    if is_conflict:
        vetoes.append(Veto(
            source=VetoSource.CONFLICT,
            reason=conflict_desc,
            detail={"directions": {k: s.direction for k, s in summaries.items()}},
        ))
        trace.conflict_result = f"FAIL: {conflict_desc}"
    else:
        trace.conflict_result = f"PASS: {conflict_desc}"

    # ── Step 7: L1 veto (conservative mode) ────────────────────────
    if config.require_l1_approval and s1.signal_count > 0 and s1.direction != 0:
        if s1.direction != direction:
            vetoes.append(Veto(
                source=VetoSource.L1_VETO,
                reason=(
                    f"L1 rules point {_dir_str(s1.direction)} "
                    f"but ensemble points {_dir_str(direction)}"
                ),
                detail={"l1_direction": s1.direction, "ensemble_direction": direction},
            ))

    # ── Step 8: L3 suppress ────────────────────────────────────────
    if s3.signal_count > 0 and s3.direction != 0 and s3.direction != direction:
        if s3.mean_confidence > 0.6:
            vetoes.append(Veto(
                source=VetoSource.L3_SUPPRESS,
                reason=(
                    f"L3 NLP strongly opposes "
                    f"(dir={_dir_str(s3.direction)}, conf={s3.mean_confidence:.3f})"
                ),
                detail={"l3_direction": s3.direction, "l3_confidence": s3.mean_confidence},
            ))

    # ── Step 9: Large trade alignment gate ─────────────────────────
    price, size = _best_price_size(l1_signals + l2_signals + l3_signals, direction)
    effective_size = size if size is not None else 0.0
    if effective_size >= config.large_trade_threshold:
        if n_agreeing < config.large_trade_min_layers:
            vetoes.append(Veto(
                source=VetoSource.LARGE_TRADE_ALIGNMENT,
                reason=(
                    f"trade size {effective_size:.2f} >= "
                    f"large threshold {config.large_trade_threshold:.2f} "
                    f"but only {n_agreeing} layers agree "
                    f"(need {config.large_trade_min_layers})"
                ),
                detail={
                    "size": effective_size,
                    "threshold": config.large_trade_threshold,
                    "agreeing": n_agreeing,
                    "required": config.large_trade_min_layers,
                },
            ))
            trace.large_trade_result = (
                f"FAIL: size={effective_size:.2f}, "
                f"{n_agreeing} agree, need {config.large_trade_min_layers}"
            )
        else:
            trace.large_trade_result = (
                f"PASS: size={effective_size:.2f}, "
                f"{n_agreeing} agree >= {config.large_trade_min_layers}"
            )
    else:
        trace.large_trade_result = (
            f"N/A: size={effective_size:.2f} < threshold {config.large_trade_threshold:.2f}"
        )

    # ── Resolve final action ───────────────────────────────────────
    blocked = len(vetoes) > 0
    if blocked:
        action = SignalAction.HOLD
        confidence = 0.0
    else:
        action = SignalAction.BUY_YES if direction > 0 else (
            SignalAction.SELL_YES if direction < 0 else SignalAction.HOLD
        )

    trace.vetoes = vetoes
    trace.final_action = action
    trace.final_confidence = confidence

    # Build rationale string
    rationale = _build_rationale(summaries, net_score, vetoes)

    candidate = TradeCandidate(
        market_id=market_id,
        token_id=iid,
        instrument_id=iid,
        exchange=exchange,
        action=action,
        final_confidence=confidence,
        expected_edge=net_edge,
        suggested_price=price,
        suggested_size=size,
        direction=direction,
        weighted_score=net_score,
        layer_contributions=weighted_scores,
        layers_agreeing=n_agreeing,
        layers_total=n_active,
        vetoes=vetoes,
        blocked=blocked,
        rationale=rationale,
        timestamp=now,
    )
    trace.candidate = candidate
    return candidate, trace


# ── Helpers ────────────────────────────────────────────────────────────


def _dir_str(d: int) -> str:
    return "BUY" if d > 0 else "SELL" if d < 0 else "HOLD"


def _best_price_size(
    signals: list[NormalizedSignal], direction: int
) -> tuple[float | None, float | None]:
    matching = [
        s for s in signals
        if s.direction == direction and s.suggested_price is not None
    ]
    if not matching:
        return None, None
    if direction > 0:
        price = min(s.suggested_price for s in matching)  # type: ignore[arg-type]
    else:
        price = max(s.suggested_price for s in matching)  # type: ignore[arg-type]
    sizes = [s.suggested_size for s in matching if s.suggested_size is not None]
    size = min(sizes) if sizes else None
    return price, size


def _build_rationale(
    summaries: dict[str, LayerSummary],
    net_score: float,
    vetoes: list[Veto],
) -> str:
    parts = []
    for name, s in summaries.items():
        if s.signal_count > 0:
            parts.append(f"{name}={s.weighted_score:+.3f}")
    line = f"ensemble({', '.join(parts)}) -> net={net_score:+.3f}"
    if vetoes:
        reasons = [f"{v.source.value}: {v.reason}" for v in vetoes]
        line += f" [BLOCKED: {'; '.join(reasons)}]"
    return line
