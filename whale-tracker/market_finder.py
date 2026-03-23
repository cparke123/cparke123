"""
Finds active Bitcoin up/down (directional) markets on Polymarket.
Bitcoin directional markets include things like:
  - "Will Bitcoin be above $X on date Y?"
  - "Will BTC go up this week?"
  - "Bitcoin price higher/lower than $X"
"""

from __future__ import annotations
import re
from typing import Optional
import polymarket_client as pm

# Keywords that indicate a Bitcoin directional market
BTC_KEYWORDS = [
    "bitcoin", "btc",
]

# Terms that suggest directional (up/down) betting
DIRECTIONAL_KEYWORDS = [
    "above", "below", "higher", "lower", "up", "down",
    "reach", "exceed", "over", "under", "price",
    "$", "usd", "k by", "k on",
]

# Exclude markets that are clearly not price-direction focused
EXCLUDE_KEYWORDS = [
    "etf approved", "halving", "satoshi", "dominance",
]


def _is_btc_directional(market: dict) -> bool:
    """Return True if the market is a Bitcoin price-direction market."""
    text = " ".join([
        market.get("question", ""),
        market.get("title", ""),
        market.get("description", ""),
    ]).lower()

    has_btc = any(kw in text for kw in BTC_KEYWORDS)
    if not has_btc:
        return False

    has_directional = any(kw in text for kw in DIRECTIONAL_KEYWORDS)
    if not has_directional:
        return False

    excluded = any(kw in text for kw in EXCLUDE_KEYWORDS)
    if excluded:
        return False

    return True


def find_btc_markets(limit: int = 200) -> list[dict]:
    """
    Search Polymarket for active Bitcoin directional markets.
    Returns a list of market dicts enriched with token IDs and prices.
    """
    results = []

    # Try several queries to get broad coverage
    queries = ["bitcoin price", "btc above", "btc below", "bitcoin higher", "bitcoin lower"]
    seen_ids = set()

    for query in queries:
        try:
            markets = pm.search_markets(query=query, limit=100)
            if not isinstance(markets, list):
                continue
            for m in markets:
                mid = m.get("id") or m.get("conditionId") or m.get("slug")
                if mid in seen_ids:
                    continue
                if _is_btc_directional(m):
                    seen_ids.add(mid)
                    results.append(m)
        except Exception as e:
            print(f"[warn] Query '{query}' failed: {e}")

    # Also try events endpoint
    try:
        events = pm.get_events(query="bitcoin", limit=100)
        if isinstance(events, list):
            for event in events:
                markets_in_event = event.get("markets", [])
                for m in markets_in_event:
                    mid = m.get("id") or m.get("conditionId") or m.get("slug")
                    if mid in seen_ids:
                        continue
                    # Merge event-level fields for context
                    merged = {**event, **m}
                    merged.pop("markets", None)
                    if _is_btc_directional(merged):
                        seen_ids.add(mid)
                        results.append(merged)
    except Exception as e:
        print(f"[warn] Events query failed: {e}")

    return results


def extract_tokens(market: dict) -> list[dict]:
    """
    Extract YES/NO token info from a market dict.
    Returns list of dicts with 'token_id', 'outcome', 'price'.
    """
    tokens = []

    # Markets from Gamma API store tokens in 'clobTokenIds' or 'tokens'
    clob_ids = market.get("clobTokenIds", [])
    outcomes = market.get("outcomes", ["YES", "NO"])
    outcome_prices = market.get("outcomePrices", [])

    if isinstance(clob_ids, str):
        import json
        try:
            clob_ids = json.loads(clob_ids)
        except Exception:
            clob_ids = []

    if isinstance(outcomes, str):
        import json
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = ["YES", "NO"]

    if isinstance(outcome_prices, str):
        import json
        try:
            outcome_prices = json.loads(outcome_prices)
        except Exception:
            outcome_prices = []

    for i, token_id in enumerate(clob_ids):
        outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
        price_str = outcome_prices[i] if i < len(outcome_prices) else "0"
        try:
            price = float(price_str)
        except (ValueError, TypeError):
            price = 0.0
        tokens.append({
            "token_id": token_id,
            "outcome": outcome,
            "price": price,
        })

    return tokens


def summarize_market(market: dict) -> dict:
    """Return a clean summary dict of a market."""
    tokens = extract_tokens(market)
    condition_id = market.get("conditionId") or market.get("id", "")

    yes_token = next((t for t in tokens if t["outcome"].upper() == "YES"), None)
    no_token = next((t for t in tokens if t["outcome"].upper() == "NO"), None)

    yes_price = yes_token["price"] if yes_token else 0.0
    no_price = no_token["price"] if no_token else 0.0

    return {
        "condition_id": condition_id,
        "question": market.get("question") or market.get("title", ""),
        "slug": market.get("slug", ""),
        "yes_price": yes_price,
        "no_price": no_price,
        "volume": float(market.get("volume", 0) or 0),
        "volume_24h": float(market.get("volume24hr", 0) or 0),
        "liquidity": float(market.get("liquidity", 0) or 0),
        "tokens": tokens,
        "active": market.get("active", True),
        "closed": market.get("closed", False),
        "end_date": market.get("endDate", ""),
    }
