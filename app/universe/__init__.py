"""
Strategic market-universe selection system.

Replaces naive fixed-market selection with a dynamic pipeline:
  UniverseScanner → MarketFilter → OpportunityScorer → WatchlistManager

The system periodically discovers, scores, and ranks markets to build
a dynamic active watchlist of the highest-opportunity markets.
"""

from app.universe.scanner import UniverseScanner
from app.universe.filters import MarketFilter
from app.universe.scorer import OpportunityScorer
from app.universe.watchlist import WatchlistManager
from app.universe.categories import CategoryPreferences
from app.universe.manager import UniverseManager

__all__ = [
    "UniverseScanner",
    "MarketFilter",
    "OpportunityScorer",
    "WatchlistManager",
    "CategoryPreferences",
    "UniverseManager",
]
