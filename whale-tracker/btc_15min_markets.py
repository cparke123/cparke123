"""
Finds and tracks active BTC 15-minute resolution markets on Polymarket.

15-minute markets ask questions like:
  "Will BTC be higher in 15 minutes?"
  "Will BTC price increase in the next 15 minutes?"

These markets open and close rapidly (every 15 minutes), so this module
also handles refreshing the active market list automatically.
"""

from __future__ import annotations
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
import polymarket_client as pm

log = logging.getLogger(__name__)

# Patterns that identify a 15-minute BTC market
FIFTEEN_MIN_PATTERNS = [
    r"15.?min",
    r"15.?minute",
    r"next 15",
    r"in 15",
    r"fifteen.?min",
]

BTC_PATTERNS = ["bitcoin", "btc"]

# How long (seconds) before we re-fetch the active market list
MARKET_CACHE_TTL = 120


@dataclass
class BTC15MinMarket:
    """A single active BTC 15-minute market."""
    condition_id: str
    question: str
    slug: str
    yes_token_id: str
    no_token_id: str
    yes_price: float     # probability BTC goes up (0–1)
    no_price: float      # probability BTC goes down (0–1)
    volume: float
    volume_24h: float
    end_date: str        # ISO8601 string, when the market resolves
    active: bool = True

    @property
    def implied_direction(self) -> str:
        """Market's implied direction based on current prices."""
        return "UP" if self.yes_price >= 0.5 else "DOWN"

    @property
    def polymarket_url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}"


def _is_btc_15min_market(market: dict) -> bool:
    text = " ".join([
        market.get("question", ""),
        market.get("title", ""),
        market.get("description", ""),
        market.get("slug", ""),
    ]).lower()

    has_btc = any(p in text for p in BTC_PATTERNS)
    if not has_btc:
        return False

    has_15min = any(re.search(p, text) for p in FIFTEEN_MIN_PATTERNS)
    return has_15min


def _parse_tokens(market: dict) -> tuple[str, str]:
    """Extract (yes_token_id, no_token_id) from a market dict."""
    import json

    clob_ids = market.get("clobTokenIds", [])
    if isinstance(clob_ids, str):
        try:
            clob_ids = json.loads(clob_ids)
        except Exception:
            clob_ids = []

    outcomes = market.get("outcomes", ["YES", "NO"])
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = ["YES", "NO"]

    yes_id = ""
    no_id = ""
    for i, token_id in enumerate(clob_ids):
        outcome = outcomes[i].upper() if i < len(outcomes) else ""
        if outcome == "YES":
            yes_id = token_id
        elif outcome == "NO":
            no_id = token_id

    return yes_id, no_id


def _parse_prices(market: dict) -> tuple[float, float]:
    """Extract (yes_price, no_price) from a market dict."""
    import json

    prices = market.get("outcomePrices", [])
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = []

    outcomes = market.get("outcomes", ["YES", "NO"])
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = ["YES", "NO"]

    yes_price = 0.5
    no_price = 0.5
    for i, price_str in enumerate(prices):
        outcome = outcomes[i].upper() if i < len(outcomes) else ""
        try:
            p = float(price_str)
        except (ValueError, TypeError):
            p = 0.5
        if outcome == "YES":
            yes_price = p
        elif outcome == "NO":
            no_price = p

    return yes_price, no_price


def parse_market(raw: dict) -> Optional[BTC15MinMarket]:
    """Convert a raw Gamma API market dict into a BTC15MinMarket."""
    condition_id = raw.get("conditionId") or raw.get("id", "")
    if not condition_id:
        return None

    yes_token, no_token = _parse_tokens(raw)
    yes_price, no_price = _parse_prices(raw)

    try:
        volume = float(raw.get("volume", 0) or 0)
        volume_24h = float(raw.get("volume24hr", 0) or 0)
    except (TypeError, ValueError):
        volume = volume_24h = 0.0

    return BTC15MinMarket(
        condition_id=condition_id,
        question=raw.get("question") or raw.get("title", ""),
        slug=raw.get("slug", ""),
        yes_token_id=yes_token,
        no_token_id=no_token,
        yes_price=yes_price,
        no_price=no_price,
        volume=volume,
        volume_24h=volume_24h,
        end_date=raw.get("endDate", ""),
        active=raw.get("active", True),
    )


class BTC15MinMarketFinder:
    """
    Maintains a cached list of active BTC 15-minute markets.
    Call .get_markets() to get the current list; it auto-refreshes
    when the cache expires (every MARKET_CACHE_TTL seconds).
    """

    def __init__(self, cache_ttl: int = MARKET_CACHE_TTL):
        self._cache: list[BTC15MinMarket] = []
        self._last_fetch: float = 0.0
        self._cache_ttl = cache_ttl

    def get_markets(self, force_refresh: bool = False) -> list[BTC15MinMarket]:
        """Return active BTC 15-min markets, refreshing cache if stale."""
        age = time.time() - self._last_fetch
        if force_refresh or age > self._cache_ttl or not self._cache:
            self._cache = self._fetch()
            self._last_fetch = time.time()
        return self._cache

    def _fetch(self) -> list[BTC15MinMarket]:
        markets: list[BTC15MinMarket] = []
        seen: set[str] = set()

        queries = [
            "bitcoin 15 minute",
            "btc 15 min",
            "bitcoin higher next 15",
            "btc price 15",
        ]

        for query in queries:
            try:
                raw_list = pm.search_markets(query=query, limit=100)
                if not isinstance(raw_list, list):
                    continue
                for raw in raw_list:
                    if not _is_btc_15min_market(raw):
                        continue
                    m = parse_market(raw)
                    if m and m.condition_id not in seen:
                        seen.add(m.condition_id)
                        markets.append(m)
            except Exception as e:
                log.warning("Market search query '%s' failed: %s", query, e)

        # Also search via events endpoint
        try:
            events = pm.get_events(query="bitcoin 15 minute", limit=50)
            if isinstance(events, list):
                for event in events:
                    for raw in event.get("markets", []):
                        merged = {**event, **raw}
                        merged.pop("markets", None)
                        if not _is_btc_15min_market(merged):
                            continue
                        m = parse_market(merged)
                        if m and m.condition_id not in seen:
                            seen.add(m.condition_id)
                            markets.append(m)
        except Exception as e:
            log.warning("Events search failed: %s", e)

        markets.sort(key=lambda m: m.volume_24h, reverse=True)
        log.info("Found %d active BTC 15-min markets", len(markets))
        return markets

    def get_condition_ids(self) -> list[str]:
        return [m.condition_id for m in self.get_markets()]

    def get_by_condition_id(self, condition_id: str) -> Optional[BTC15MinMarket]:
        return next(
            (m for m in self.get_markets() if m.condition_id == condition_id),
            None,
        )
