"""
Gemini exchange trader using ccxt.

Supports:
  - Paper trading mode (simulates orders, tracks P&L locally)
  - Live trading mode via Gemini REST API
  - Spot BTC/USD only (simple buy/sell, no leverage)

Gemini API docs: https://docs.gemini.com/rest-api/
ccxt Gemini docs: https://docs.ccxt.com/#/?id=gemini
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
import ccxt
import config

log = logging.getLogger(__name__)

SYMBOL = "BTC/USD"


@dataclass
class Position:
    """Tracks a single open trade."""
    side: str           # "buy" or "sell"
    entry_price: float
    size_btc: float
    size_usd: float
    opened_at: float    # Unix timestamp
    order_id: str = ""
    signal_direction: str = ""    # "UP" or "DOWN"
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0

    @property
    def age_seconds(self) -> float:
        return time.time() - self.opened_at

    def pnl(self, current_price: float) -> float:
        """Unrealized P&L in USD."""
        if self.side == "buy":
            return (current_price - self.entry_price) * self.size_btc
        else:
            return (self.entry_price - current_price) * self.size_btc

    def pnl_pct(self, current_price: float) -> float:
        pnl = self.pnl(current_price)
        return pnl / self.size_usd if self.size_usd else 0.0


class PaperExchange:
    """
    Simulates Gemini exchange behaviour locally.
    Fetches real BTC/USD prices from Gemini public API but doesn't place
    real orders.
    """

    def __init__(self):
        # Use public Gemini API for real price data even in paper mode
        self._public = ccxt.gemini({"options": {"fetchTickerMethod": "fetchTickerV2"}})
        self._balance_usd = 10_000.0   # simulated starting balance
        self._balance_btc = 0.0
        self._trade_log: list[dict] = []

    def fetch_ticker(self) -> dict:
        return self._public.fetch_ticker(SYMBOL)

    def fetch_btc_price(self) -> float:
        ticker = self.fetch_ticker()
        return float(ticker["last"])

    def create_market_order(self, side: str, amount_btc: float) -> dict:
        price = self.fetch_btc_price()
        cost = amount_btc * price

        if side == "buy":
            if cost > self._balance_usd:
                raise ValueError(f"Insufficient paper USD balance: have ${self._balance_usd:.2f}, need ${cost:.2f}")
            self._balance_usd -= cost
            self._balance_btc += amount_btc
        else:
            if amount_btc > self._balance_btc:
                raise ValueError(f"Insufficient paper BTC balance: have {self._balance_btc:.6f}, need {amount_btc:.6f}")
            self._balance_btc -= amount_btc
            self._balance_usd += cost

        order_id = f"paper-{int(time.time() * 1000)}"
        order = {
            "id": order_id,
            "side": side,
            "amount": amount_btc,
            "price": price,
            "cost": cost,
            "status": "closed",
            "filled": amount_btc,
            "timestamp": time.time(),
        }
        self._trade_log.append(order)
        log.info("[PAPER] %s %.6f BTC @ $%.2f = $%.2f | Balance: $%.2f USD / %.6f BTC",
                 side.upper(), amount_btc, price, cost, self._balance_usd, self._balance_btc)
        return order

    def fetch_balance(self) -> dict:
        price = self.fetch_btc_price()
        return {
            "USD": {"free": self._balance_usd, "total": self._balance_usd},
            "BTC": {"free": self._balance_btc, "total": self._balance_btc},
            "total_usd_value": self._balance_usd + self._balance_btc * price,
        }

    @property
    def trade_log(self) -> list[dict]:
        return self._trade_log


class LiveExchange:
    """
    Real Gemini exchange via ccxt.
    Requires valid API key + secret in config.py.
    """

    def __init__(self):
        sandbox = config.PAPER_TRADING  # shouldn't reach here in paper mode but safety check
        self._exchange = ccxt.gemini({
            "apiKey": config.GEMINI_API_KEY,
            "secret": config.GEMINI_API_SECRET,
            "sandbox": False,   # Gemini sandbox uses a different URL, handled below
            "options": {
                "fetchTickerMethod": "fetchTickerV2",
            },
        })

        if sandbox:
            # Point to Gemini sandbox
            self._exchange.urls["api"] = {
                "public": "https://api.sandbox.gemini.com",
                "private": "https://api.sandbox.gemini.com",
            }

        # Test connectivity
        self._exchange.load_markets()
        log.info("Connected to Gemini %s", "sandbox" if sandbox else "production")

    def fetch_ticker(self) -> dict:
        return self._exchange.fetch_ticker(SYMBOL)

    def fetch_btc_price(self) -> float:
        ticker = self.fetch_ticker()
        return float(ticker["last"])

    def create_market_order(self, side: str, amount_btc: float) -> dict:
        order = self._exchange.create_market_order(SYMBOL, side, amount_btc)
        log.info("[LIVE] %s %.6f BTC @ ~$%.2f (order %s)",
                 side.upper(), amount_btc,
                 float(order.get("price") or self.fetch_btc_price()),
                 order.get("id", ""))
        return order

    def fetch_balance(self) -> dict:
        return self._exchange.fetch_balance()


class GeminiTrader:
    """
    High-level interface for buying and selling BTC on Gemini.
    Handles position tracking, stop-loss, take-profit, and auto-close.
    Delegates actual order execution to PaperExchange or LiveExchange.
    """

    def __init__(self):
        if config.PAPER_TRADING:
            log.info("GeminiTrader: PAPER TRADING mode (no real orders)")
            self._exchange = PaperExchange()
        else:
            if not config.GEMINI_API_KEY or not config.GEMINI_API_SECRET:
                raise ValueError(
                    "GEMINI_API_KEY and GEMINI_API_SECRET must be set in config.py "
                    "to use live trading mode."
                )
            log.info("GeminiTrader: LIVE mode — real orders will be placed on Gemini")
            self._exchange = LiveExchange()

        self._positions: list[Position] = []
        self._closed_positions: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_position(self, direction: str, size_usd: float) -> Optional[Position]:
        """
        Open a BTC position based on signal direction.
          direction="UP"   → buy BTC
          direction="DOWN" → sell BTC (only if we hold BTC, else skips)

        Returns the Position or None if skipped.
        """
        # Check max exposure
        total_open = sum(p.size_usd for p in self._positions)
        if total_open + size_usd > config.MAX_OPEN_POSITION_USD:
            log.warning(
                "Max open position limit reached ($%.0f / $%.0f) — skipping trade",
                total_open, config.MAX_OPEN_POSITION_USD,
            )
            return None

        side = "buy" if direction == "UP" else "sell"
        price = self._exchange.fetch_btc_price()
        amount_btc = size_usd / price

        try:
            order = self._exchange.create_market_order(side, amount_btc)
        except Exception as e:
            log.error("Order failed: %s", e)
            return None

        fill_price = float(order.get("price") or order.get("average") or price)

        # Calculate stop/take-profit levels
        if side == "buy":
            sl = fill_price * (1 - config.STOP_LOSS_PCT)
            tp = fill_price * (1 + config.TAKE_PROFIT_PCT)
        else:
            sl = fill_price * (1 + config.STOP_LOSS_PCT)
            tp = fill_price * (1 - config.TAKE_PROFIT_PCT)

        pos = Position(
            side=side,
            entry_price=fill_price,
            size_btc=amount_btc,
            size_usd=size_usd,
            opened_at=time.time(),
            order_id=order.get("id", ""),
            signal_direction=direction,
            stop_loss_price=sl,
            take_profit_price=tp,
        )
        self._positions.append(pos)
        log.info(
            "Opened %s position: %.6f BTC @ $%.2f | SL=$%.2f | TP=$%.2f",
            side.upper(), amount_btc, fill_price, sl, tp,
        )
        return pos

    def close_position(self, pos: Position, reason: str = "manual") -> float:
        """
        Close an open position. Returns realized P&L.
        """
        if pos not in self._positions:
            log.warning("Position not in open list, may already be closed")
            return 0.0

        price = self._exchange.fetch_btc_price()
        close_side = "sell" if pos.side == "buy" else "buy"

        try:
            self._exchange.create_market_order(close_side, pos.size_btc)
        except Exception as e:
            log.error("Close order failed: %s", e)
            return 0.0

        pnl = pos.pnl(price)
        duration = pos.age_seconds

        log.info(
            "Closed %s position @ $%.2f | P&L: $%.2f (%.2f%%) | held %.0fs | reason: %s",
            pos.side.upper(), price, pnl, pos.pnl_pct(price) * 100, duration, reason,
        )

        self._positions.remove(pos)
        self._closed_positions.append({
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": price,
            "size_btc": pos.size_btc,
            "size_usd": pos.size_usd,
            "pnl": pnl,
            "pnl_pct": pos.pnl_pct(price),
            "duration_seconds": duration,
            "reason": reason,
            "opened_at": pos.opened_at,
            "closed_at": time.time(),
        })
        return pnl

    def close_all_positions(self, reason: str = "close_all") -> float:
        """Close all open positions. Returns total realized P&L."""
        total_pnl = 0.0
        for pos in list(self._positions):
            total_pnl += self.close_position(pos, reason=reason)
        return total_pnl

    def check_exit_conditions(self) -> list[tuple[Position, str]]:
        """
        Check all open positions for stop-loss, take-profit, or auto-close.
        Returns list of (position, reason) that should be closed.
        """
        to_close: list[tuple[Position, str]] = []
        if not self._positions:
            return to_close

        try:
            price = self._exchange.fetch_btc_price()
        except Exception as e:
            log.warning("Could not fetch price for exit check: %s", e)
            return to_close

        for pos in self._positions:
            # Auto-close by time
            if config.AUTO_CLOSE_AFTER_SECONDS > 0:
                if pos.age_seconds >= config.AUTO_CLOSE_AFTER_SECONDS:
                    to_close.append((pos, "auto_close_timeout"))
                    continue

            # Stop-loss
            if pos.side == "buy" and price <= pos.stop_loss_price:
                to_close.append((pos, "stop_loss"))
                continue
            if pos.side == "sell" and price >= pos.stop_loss_price:
                to_close.append((pos, "stop_loss"))
                continue

            # Take-profit
            if pos.side == "buy" and price >= pos.take_profit_price:
                to_close.append((pos, "take_profit"))
                continue
            if pos.side == "sell" and price <= pos.take_profit_price:
                to_close.append((pos, "take_profit"))
                continue

        return to_close

    # ------------------------------------------------------------------
    # Status / reporting
    # ------------------------------------------------------------------

    def get_btc_price(self) -> float:
        return self._exchange.fetch_btc_price()

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions)

    @property
    def closed_positions(self) -> list[dict]:
        return list(self._closed_positions)

    def total_realized_pnl(self) -> float:
        return sum(p["pnl"] for p in self._closed_positions)

    def total_unrealized_pnl(self) -> float:
        try:
            price = self._exchange.fetch_btc_price()
            return sum(p.pnl(price) for p in self._positions)
        except Exception:
            return 0.0

    def status_dict(self) -> dict:
        """Return a dict summary for display purposes."""
        try:
            price = self._exchange.fetch_btc_price()
        except Exception:
            price = 0.0

        return {
            "btc_price": price,
            "open_positions": len(self._positions),
            "total_open_usd": sum(p.size_usd for p in self._positions),
            "unrealized_pnl": self.total_unrealized_pnl(),
            "realized_pnl": self.total_realized_pnl(),
            "total_trades": len(self._closed_positions),
            "paper_mode": config.PAPER_TRADING,
        }
