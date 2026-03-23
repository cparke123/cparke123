"""
Polymarket API client wrapping the Gamma, Data, and CLOB APIs.
All endpoints used here are public and require no authentication.
"""

import time
import requests
from typing import Optional

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Default request timeout in seconds
TIMEOUT = 15


def _get(url: str, params: dict = None, retries: int = 3) -> dict | list:
    """GET with simple retry logic."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


# ---------------------------------------------------------------------------
# Gamma API — market discovery
# ---------------------------------------------------------------------------

def search_markets(query: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Search for markets by keyword."""
    return _get(f"{GAMMA_BASE}/markets", params={
        "q": query,
        "limit": limit,
        "offset": offset,
        "active": "true",
    })


def get_events(query: str = "", limit: int = 100, offset: int = 0,
               closed: bool = False) -> list[dict]:
    """Fetch events, optionally filtering by search query."""
    params = {
        "limit": limit,
        "offset": offset,
        "closed": str(closed).lower(),
        "active": "true",
    }
    if query:
        params["q"] = query
    return _get(f"{GAMMA_BASE}/events", params=params)


def get_market_by_slug(slug: str) -> dict:
    """Fetch a single market by its slug."""
    results = _get(f"{GAMMA_BASE}/markets", params={"slug": slug})
    if isinstance(results, list) and results:
        return results[0]
    return results


# ---------------------------------------------------------------------------
# Data API — trades, positions, activity
# ---------------------------------------------------------------------------

def get_trades(
    market_id: Optional[str] = None,
    taker_address: Optional[str] = None,
    maker_address: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """
    Fetch trades. Filter by market condition_id and/or wallet address.
    'taker_address' and 'maker_address' expect proxy wallet addresses.
    """
    params: dict = {"limit": limit, "offset": offset}
    if market_id:
        params["conditionId"] = market_id
    if taker_address:
        params["takerAddress"] = taker_address
    if maker_address:
        params["makerAddress"] = maker_address
    return _get(f"{DATA_BASE}/trades", params=params)


def get_positions(
    user_address: Optional[str] = None,
    market_id: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """Fetch open positions for a user or market."""
    params: dict = {"limit": limit, "offset": offset, "sizeThreshold": "0.01"}
    if user_address:
        params["user"] = user_address
    if market_id:
        params["market"] = market_id
    return _get(f"{DATA_BASE}/positions", params=params)


def get_activity(
    user_address: Optional[str] = None,
    market_id: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """Fetch user activity (trades + redemptions) for P&L tracking."""
    params: dict = {"limit": limit, "offset": offset}
    if user_address:
        params["user"] = user_address
    if market_id:
        params["conditionId"] = market_id
    return _get(f"{DATA_BASE}/activity", params=params)


def get_market_positions(market_id: str, limit: int = 500) -> list[dict]:
    """Get all positions in a specific market, sorted by size."""
    return get_positions(market_id=market_id, limit=limit)


# ---------------------------------------------------------------------------
# CLOB API — prices
# ---------------------------------------------------------------------------

def get_midpoint(token_id: str) -> Optional[float]:
    """Get the current mid-price for a token."""
    try:
        data = _get(f"{CLOB_BASE}/midpoint", params={"token_id": token_id})
        return float(data.get("mid", 0))
    except Exception:
        return None


def get_last_trade_price(token_id: str) -> Optional[float]:
    """Get the last traded price for a token."""
    try:
        data = _get(f"{CLOB_BASE}/last-trade-price", params={"token_id": token_id})
        return float(data.get("price", 0))
    except Exception:
        return None
