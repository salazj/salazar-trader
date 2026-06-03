"""
Prediction target construction for short-horizon prediction-market trading.

Two target definitions, both derived from forward-looking mid-price changes:

1. **direction** (binary): did mid_price move favourably by at least
   `min_edge_cents` within the next `horizon_rows` observations?

2. **edge** (continuous): expected profit in cents of buying at the current
   best ask (or selling at best bid) and exiting at the future mid_price,
   net of assumed round-trip fees.

ASSUMPTIONS (make explicit so reviewers can challenge them):
- The "horizon" is measured in *row counts*, not wall-clock time.  Each row
  is a feature snapshot whose cadence depends on the strategy loop interval
  (default 5 s).  That means horizon_rows=6 ≈ 30 s at 5-s cadence.
- We assume the trader can get filled at the current best ask for a BUY
  and best bid for a SELL.  In practice, fill probability and queue
  position matter — we ignore them in v1.
- Round-trip fee is a fixed constant (default 0.02 = 2 cents).  Polymarket
  currently charges 0 taker fee on most markets, but this may change.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_HORIZON_ROWS = 6      # ≈30 s at 5-s cadence
DEFAULT_MIN_EDGE_CENTS = 0.02  # 2 cents minimum favorable move
DEFAULT_FEE_ROUND_TRIP = 0.02  # 2 cents assumed round-trip cost


def add_direction_target(
    df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON_ROWS,
    min_edge: float = DEFAULT_MIN_EDGE_CENTS,
    price_col: str = "mid_price",
) -> pd.DataFrame:
    """Append binary column ``target_direction`` to *df*.

    1 if the future mid_price rises by at least ``min_edge`` within
    the next ``horizon`` rows, 0 otherwise.

    Rows where the target cannot be computed (tail of the series) are
    left as NaN — the caller should drop them before training.
    """
    df = df.copy()
    prices = df[price_col].values
    n = len(prices)
    target = np.full(n, np.nan)
    for i in range(n - horizon):
        future_max = np.max(prices[i + 1 : i + 1 + horizon])
        target[i] = 1.0 if (future_max - prices[i]) >= min_edge else 0.0
    df["target_direction"] = target
    return df


def add_edge_target(
    df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON_ROWS,
    fee: float = DEFAULT_FEE_ROUND_TRIP,
    price_col: str = "mid_price",
    ask_col: str = "best_ask",
) -> pd.DataFrame:
    """Append continuous column ``target_edge`` to *df*.

    Edge = future_mid - current_ask - fee.
    Positive means buying at the ask and marking-to-mid later is profitable
    after fees.  Useful for regression targets or thresholded binary labels.
    """
    df = df.copy()
    future_mid = df[price_col].shift(-horizon)
    entry_price = df[ask_col].fillna(df[price_col])
    df["target_edge"] = future_mid - entry_price - fee
    return df


def add_all_targets(
    df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON_ROWS,
    min_edge: float = DEFAULT_MIN_EDGE_CENTS,
    fee: float = DEFAULT_FEE_ROUND_TRIP,
) -> pd.DataFrame:
    """Convenience: add both direction and edge targets."""
    df = add_direction_target(df, horizon=horizon, min_edge=min_edge)
    df = add_edge_target(df, horizon=horizon, fee=fee)
    return df
