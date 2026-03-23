"""
Whale detection and position aggregation for Polymarket Bitcoin markets.

A "whale" is a wallet with:
  - A single trade >= WHALE_TRADE_THRESHOLD USD, OR
  - Total position size >= WHALE_POSITION_THRESHOLD USD across BTC markets
"""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
import polymarket_client as pm

# Thresholds (USD)
WHALE_TRADE_THRESHOLD = 1_000      # single trade >= $1,000 qualifies
WHALE_POSITION_THRESHOLD = 5_000   # or total open position >= $5,000


@dataclass
class WhalePosition:
    """Represents a whale's position in a single market."""
    market_question: str
    condition_id: str
    outcome: str          # "YES" or "NO"
    size: float           # number of shares
    avg_price: float      # average price paid (0–1)
    current_price: float  # live price (0–1)
    usd_value: float      # size * current_price
    unrealized_pnl: float # (current_price - avg_price) * size

    @property
    def direction(self) -> str:
        """Return 'UP' if YES, 'DOWN' if NO (for price markets)."""
        if self.outcome.upper() == "YES":
            return "UP  (YES)"
        elif self.outcome.upper() == "NO":
            return "DOWN (NO)"
        return self.outcome


@dataclass
class Whale:
    """Aggregated view of a single whale wallet."""
    address: str
    positions: list[WhalePosition] = field(default_factory=list)
    total_volume: float = 0.0      # lifetime trade volume on these markets
    total_usd_value: float = 0.0   # current open position value
    total_pnl: float = 0.0         # unrealized P&L

    def recompute_totals(self):
        self.total_usd_value = sum(p.usd_value for p in self.positions)
        self.total_pnl = sum(p.unrealized_pnl for p in self.positions)

    @property
    def net_direction(self) -> str:
        """Summarize whether this whale is net long YES or NO on BTC going up."""
        yes_val = sum(p.usd_value for p in self.positions if p.outcome.upper() == "YES")
        no_val = sum(p.usd_value for p in self.positions if p.outcome.upper() == "NO")
        if yes_val > no_val:
            return f"BULLISH (${yes_val:,.0f} YES vs ${no_val:,.0f} NO)"
        elif no_val > yes_val:
            return f"BEARISH (${no_val:,.0f} NO vs ${yes_val:,.0f} YES)"
        return "NEUTRAL"


def _parse_float(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def detect_whales_from_trades(
    markets: list[dict],
    threshold: float = WHALE_TRADE_THRESHOLD,
) -> dict[str, dict]:
    """
    Scan recent trades across all given markets and identify whale wallets.
    Returns dict of {address: {'volume': float, 'markets': set}}.
    """
    whale_candidates: dict[str, dict] = {}

    for market in markets:
        condition_id = market.get("condition_id", "")
        if not condition_id:
            continue

        try:
            trades = pm.get_trades(market_id=condition_id, limit=500)
        except Exception as e:
            print(f"[warn] Could not fetch trades for {condition_id[:8]}…: {e}")
            continue

        if not isinstance(trades, list):
            continue

        for trade in trades:
            usd_amount = _parse_float(trade.get("usdcSize") or trade.get("size", 0))
            if usd_amount < threshold:
                continue

            # Prefer proxy wallet; fall back to taker/maker
            for addr_key in ("takerAddress", "makerAddress"):
                addr = trade.get(addr_key, "")
                if not addr or addr == "0x0000000000000000000000000000000000000000":
                    continue

                if addr not in whale_candidates:
                    whale_candidates[addr] = {"volume": 0.0, "markets": set()}
                whale_candidates[addr]["volume"] += usd_amount
                whale_candidates[addr]["markets"].add(condition_id)

    return whale_candidates


def build_whale_profiles(
    whale_candidates: dict[str, dict],
    markets: list[dict],
    position_threshold: float = WHALE_POSITION_THRESHOLD,
) -> list[Whale]:
    """
    For each whale candidate, fetch their current positions and build a Whale object.
    Only returns whales whose total open position >= position_threshold.
    """
    # Build lookup: condition_id -> market summary
    market_lookup = {m["condition_id"]: m for m in markets if m.get("condition_id")}

    # Build token -> (market, outcome, current_price) lookup
    token_lookup: dict[str, dict] = {}
    for m in markets:
        for token in m.get("tokens", []):
            token_lookup[token["token_id"]] = {
                "market_question": m.get("question", ""),
                "condition_id": m.get("condition_id", ""),
                "outcome": token["outcome"],
                "current_price": token["price"],
            }

    whales: list[Whale] = []

    for address, candidate_info in whale_candidates.items():
        try:
            raw_positions = pm.get_positions(user_address=address, limit=200)
        except Exception as e:
            print(f"[warn] Could not fetch positions for {address[:10]}…: {e}")
            continue

        if not isinstance(raw_positions, list):
            continue

        whale_positions: list[WhalePosition] = []

        for pos in raw_positions:
            # Filter to only positions in our tracked BTC markets
            condition_id = pos.get("conditionId", "")
            token_id = pos.get("asset", pos.get("tokenId", ""))

            # Check if this position is in one of our tracked markets
            in_btc_market = (
                condition_id in market_lookup
                or token_id in token_lookup
            )
            if not in_btc_market:
                continue

            size = _parse_float(pos.get("size"))
            avg_price = _parse_float(pos.get("avgPrice") or pos.get("price", 0))

            # Get current price from our token lookup or position data
            if token_id in token_lookup:
                token_info = token_lookup[token_id]
                current_price = token_info["current_price"] or avg_price
                outcome = token_info["outcome"]
                market_question = token_info["market_question"]
            else:
                market_info = market_lookup.get(condition_id, {})
                current_price = _parse_float(pos.get("curPrice") or avg_price)
                outcome = pos.get("outcome", "YES")
                market_question = market_info.get("question", condition_id[:16] + "…")

            usd_value = size * current_price
            unrealized_pnl = (current_price - avg_price) * size

            if size < 0.01:
                continue

            whale_positions.append(WhalePosition(
                market_question=market_question,
                condition_id=condition_id,
                outcome=outcome,
                size=size,
                avg_price=avg_price,
                current_price=current_price,
                usd_value=usd_value,
                unrealized_pnl=unrealized_pnl,
            ))

        if not whale_positions:
            continue

        whale = Whale(
            address=address,
            positions=whale_positions,
            total_volume=candidate_info["volume"],
        )
        whale.recompute_totals()

        if whale.total_usd_value >= position_threshold:
            whales.append(whale)

    # Sort by total position value descending
    whales.sort(key=lambda w: w.total_usd_value, reverse=True)
    return whales


def get_top_holders(
    markets: list[dict],
    limit: int = 20,
) -> list[Whale]:
    """
    Alternative approach: directly query market positions to find largest holders.
    Useful when we want top holders without scanning all trades.
    """
    # Build token -> market info lookup
    token_lookup: dict[str, dict] = {}
    for m in markets:
        for token in m.get("tokens", []):
            token_lookup[token["token_id"]] = {
                "market_question": m.get("question", ""),
                "condition_id": m.get("condition_id", ""),
                "outcome": token["outcome"],
                "current_price": token["price"],
            }

    # Aggregate positions by address
    by_address: dict[str, Whale] = {}

    for market in markets:
        condition_id = market.get("condition_id", "")
        if not condition_id:
            continue

        try:
            positions = pm.get_market_positions(market_id=condition_id, limit=100)
        except Exception as e:
            print(f"[warn] Could not fetch positions for market {condition_id[:8]}…: {e}")
            continue

        if not isinstance(positions, list):
            continue

        for pos in positions:
            size = _parse_float(pos.get("size"))
            if size < 1.0:
                continue

            address = pos.get("proxyWallet") or pos.get("userId", "")
            if not address:
                continue

            token_id = pos.get("asset", pos.get("tokenId", ""))
            avg_price = _parse_float(pos.get("avgPrice") or pos.get("price", 0))

            if token_id in token_lookup:
                info = token_lookup[token_id]
                current_price = info["current_price"] or avg_price
                outcome = info["outcome"]
                market_question = info["market_question"]
            else:
                current_price = avg_price
                outcome = pos.get("outcome", "YES")
                market_question = market.get("question", "")

            usd_value = size * current_price
            unrealized_pnl = (current_price - avg_price) * size

            wp = WhalePosition(
                market_question=market_question,
                condition_id=condition_id,
                outcome=outcome,
                size=size,
                avg_price=avg_price,
                current_price=current_price,
                usd_value=usd_value,
                unrealized_pnl=unrealized_pnl,
            )

            if address not in by_address:
                by_address[address] = Whale(address=address)
            by_address[address].positions.append(wp)

    # Recompute totals and filter/sort
    result = []
    for whale in by_address.values():
        whale.recompute_totals()
        if whale.total_usd_value >= WHALE_POSITION_THRESHOLD:
            result.append(whale)

    result.sort(key=lambda w: w.total_usd_value, reverse=True)
    return result[:limit]
