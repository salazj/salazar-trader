"""Resolve company names / proper nouns to stock tickers.

Used by the news-driven universe selector: when phi3 extracts entities
like ``["Tesla", "Elon Musk", "Federal Reserve"]`` from a headline we
need to turn those into actual tickers so the bot can decide what to
watch.

The mapping is intentionally **static and curated** — no live API call.
That keeps the resolver fast, deterministic, offline-capable on the
Jetson, and avoids accidentally trading random tickers a fuzzy lookup
might pick up.

Extend ``NAME_TO_TICKER`` (and ``SP_100_TICKERS``) as needed for tickers
you want the bot to be able to discover from news.
"""

from __future__ import annotations

from typing import Iterable


SP_100_TICKERS: frozenset[str] = frozenset({
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT",
    "AMZN", "AVGO", "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK.B",
    "C", "CAT", "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO",
    "CVS", "CVX", "DE", "DHR", "DIS", "DUK", "EMR", "F", "FDX", "GD", "GE",
    "GILD", "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "ISRG",
    "JNJ", "JPM", "KHC", "KO", "LIN", "LLY", "LMT", "LOW", "MA", "MCD",
    "MDLZ", "MDT", "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT", "NEE",
    "NFLX", "NKE", "NVDA", "ORCL", "PEP", "PFE", "PG", "PM", "PYPL", "QCOM",
    "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA",
    "TXN", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WBA", "WFC", "WMT", "XOM",
})

LIQUID_ETFS: frozenset[str] = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "EEM", "GLD", "SLV", "XLE",
    "XLF", "XLK", "XLV", "XLY", "XLI", "XLB", "XLU", "XLP", "XLRE", "XBI",
    "ARKK", "TLT", "TQQQ", "SQQQ", "UVXY",
})

ALL_KNOWN_TICKERS: frozenset[str] = SP_100_TICKERS | LIQUID_ETFS


_NAME_TO_TICKER_RAW: dict[str, str] = {
    "apple": "AAPL", "apple inc": "AAPL", "iphone": "AAPL", "tim cook": "AAPL",
    "microsoft": "MSFT", "msft": "MSFT", "satya nadella": "MSFT",
    "alphabet": "GOOGL", "google": "GOOGL", "youtube": "GOOGL", "sundar pichai": "GOOGL",
    "amazon": "AMZN", "aws": "AMZN", "andy jassy": "AMZN",
    "tesla": "TSLA", "elon musk": "TSLA", "cybertruck": "TSLA",
    "nvidia": "NVDA", "jensen huang": "NVDA",
    "meta": "META", "facebook": "META", "instagram": "META", "whatsapp": "META",
    "mark zuckerberg": "META",
    "amd": "AMD", "advanced micro devices": "AMD", "lisa su": "AMD",
    "netflix": "NFLX",
    "berkshire": "BRK.B", "berkshire hathaway": "BRK.B", "warren buffett": "BRK.B",
    "jpmorgan": "JPM", "jp morgan": "JPM", "jamie dimon": "JPM",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "citigroup": "C", "citi": "C",
    "visa": "V",
    "mastercard": "MA",
    "paypal": "PYPL",
    "walmart": "WMT",
    "costco": "COST",
    "target": "TGT",
    "home depot": "HD",
    "lowes": "LOW", "lowe's": "LOW",
    "starbucks": "SBUX",
    "mcdonalds": "MCD", "mcdonald's": "MCD",
    "nike": "NKE",
    "disney": "DIS",
    "comcast": "CMCSA",
    "at&t": "T", "att": "T",
    "verizon": "VZ",
    "t-mobile": "TMUS", "tmobile": "TMUS",
    "boeing": "BA",
    "lockheed": "LMT", "lockheed martin": "LMT",
    "raytheon": "RTX", "rtx": "RTX",
    "general electric": "GE",
    "general motors": "GM",
    "ford": "F", "ford motor": "F",
    "exxon": "XOM", "exxonmobil": "XOM", "exxon mobil": "XOM",
    "chevron": "CVX",
    "conocophillips": "COP",
    "pfizer": "PFE",
    "merck": "MRK",
    "johnson & johnson": "JNJ", "j&j": "JNJ", "johnson and johnson": "JNJ",
    "abbvie": "ABBV",
    "eli lilly": "LLY", "lilly": "LLY",
    "bristol-myers": "BMY", "bristol myers": "BMY",
    "gilead": "GILD",
    "amgen": "AMGN",
    "thermo fisher": "TMO",
    "danaher": "DHR",
    "abbott": "ABT",
    "intuitive surgical": "ISRG",
    "medtronic": "MDT",
    "unitedhealth": "UNH", "united health": "UNH",
    "cvs": "CVS",
    "walgreens": "WBA",
    "coca-cola": "KO", "coca cola": "KO", "coke": "KO",
    "pepsi": "PEP", "pepsico": "PEP",
    "altria": "MO",
    "philip morris": "PM",
    "procter & gamble": "PG", "procter and gamble": "PG", "p&g": "PG",
    "colgate": "CL", "colgate-palmolive": "CL",
    "kraft heinz": "KHC",
    "mondelez": "MDLZ",
    "honeywell": "HON",
    "3m": "MMM",
    "caterpillar": "CAT",
    "deere": "DE", "john deere": "DE",
    "union pacific": "UNP",
    "ups": "UPS", "united parcel": "UPS",
    "fedex": "FDX",
    "intel": "INTC",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "cisco": "CSCO",
    "oracle": "ORCL",
    "ibm": "IBM",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "accenture": "ACN",
    "texas instruments": "TXN",
    "blackrock": "BLK",
    "bank of new york mellon": "BK", "bny mellon": "BK",
    "american express": "AXP", "amex": "AXP",
    "schwab": "SCHW", "charles schwab": "SCHW",
    "us bancorp": "USB",
    "metlife": "MET",
    "aig": "AIG",
    "capital one": "COF",
    "spy": "SPY", "s&p 500": "SPY", "s&p500": "SPY",
    "qqq": "QQQ", "nasdaq 100": "QQQ", "nasdaq-100": "QQQ",
    "iwm": "IWM", "russell 2000": "IWM",
    "dia": "DIA", "dow jones": "DIA", "dow": "DIA",
    "gold": "GLD", "gld": "GLD",
    "silver": "SLV", "slv": "SLV",
    "tlt": "TLT", "20 year treasury": "TLT", "long bonds": "TLT",
    "vix": "UVXY",
}


NAME_TO_TICKER: dict[str, str] = {
    k.lower().strip(): v for k, v in _NAME_TO_TICKER_RAW.items()
}


class NameResolver:
    """Map a list of entity strings to a set of stock tickers.

    Recognises:
      * exact ticker symbols (case-insensitive) within ``ALL_KNOWN_TICKERS``
      * curated company names / brand names / executive names
    """

    def __init__(
        self,
        extra_aliases: dict[str, str] | None = None,
        allowed_tickers: Iterable[str] | None = None,
    ) -> None:
        self._aliases: dict[str, str] = dict(NAME_TO_TICKER)
        if extra_aliases:
            self._aliases.update(
                {k.lower().strip(): v.upper() for k, v in extra_aliases.items()}
            )
        self._allowed: frozenset[str] = (
            frozenset(t.upper() for t in allowed_tickers)
            if allowed_tickers is not None
            else ALL_KNOWN_TICKERS
        )

    @property
    def allowed_tickers(self) -> frozenset[str]:
        return self._allowed

    def resolve(self, entities: Iterable[str]) -> set[str]:
        """Return the set of tickers that ``entities`` map to."""
        out: set[str] = set()
        for raw in entities:
            if not raw:
                continue
            key = str(raw).strip().lower()
            if not key:
                continue
            ticker = self._aliases.get(key)
            if ticker is None:
                upper = key.upper().replace(" ", "")
                if upper in self._allowed:
                    ticker = upper
            if ticker and ticker in self._allowed:
                out.add(ticker)
        return out

    def resolve_text(self, text: str) -> set[str]:
        """Resolve any tickers mentioned anywhere in ``text``.

        Uses simple whole-word/phrase matches against the alias table.
        """
        if not text:
            return set()
        lowered = " " + text.lower() + " "
        out: set[str] = set()
        for alias, ticker in self._aliases.items():
            if not alias:
                continue
            needle = f" {alias} "
            if needle in lowered:
                out.add(ticker)
        return out
