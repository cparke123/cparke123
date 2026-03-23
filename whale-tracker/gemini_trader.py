"""
Gemini Prediction Markets — Authenticated trading client.

Covers:
  POST /v1/prediction-markets/order         place a new order
  POST /v1/prediction-markets/orders/active list open orders
  POST /v1/prediction-markets/orders/cancel cancel an order
  POST /v1/prediction-markets/positions     list current positions

Authentication: Gemini REST private API (HMAC-SHA384 over base64 payload).

Only limit orders are currently supported by Gemini prediction markets.
The trader uses aggressive pricing (near ask for buys, near bid for sells)
to maximise fill probability.
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
import requests

log = logging.getLogger(__name__)

BASE = "https://api.gemini.com"
TIMEOUT = 10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Order:
    order_id: str
    symbol: str
    outcome: str        # "yes" or "no"
    side: str           # "buy" or "sell"
    quantity: float
    price: float        # 0–1
    status: str         # "live" | "filled" | "cancelled" | "closed"
    executed_amount: float = 0.0
    avg_execution_price: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def usd_spent(self) -> float:
        return self.executed_amount * self.avg_execution_price


@dataclass
class Position:
    symbol: str
    outcome: str
    quantity: float     # contracts held
    avg_cost: float     # average cost per contract
    current_price: float

    @property
    def usd_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealised_pnl(self) -> float:
        return self.quantity * (self.current_price - self.avg_cost)


# ---------------------------------------------------------------------------
# Paper trading ledger
# ---------------------------------------------------------------------------

class PaperLedger:
    """Simulates order fills for paper-trading mode."""

    def __init__(self):
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._next_id = 1
        self._balance = 0.0  # tracks simulated spend

    def place_order(
        self,
        symbol: str,
        outcome: str,
        side: str,
        quantity: float,
        price: float,
    ) -> Order:
        oid = f"PAPER-{self._next_id:04d}"
        self._next_id += 1
        order = Order(
            order_id=oid,
            symbol=symbol,
            outcome=outcome,
            side=side,
            quantity=quantity,
            price=price,
            status="filled",
            executed_amount=quantity,
            avg_execution_price=price,
        )
        self._orders[oid] = order
        # Update simulated position
        key = f"{symbol}:{outcome}"
        if side == "buy":
            if key in self._positions:
                pos = self._positions[key]
                total_cost = pos.avg_cost * pos.quantity + price * quantity
                pos.quantity += quantity
                pos.avg_cost = total_cost / pos.quantity
                pos.current_price = price
            else:
                self._positions[key] = Position(
                    symbol=symbol,
                    outcome=outcome,
                    quantity=quantity,
                    avg_cost=price,
                    current_price=price,
                )
            self._balance += price * quantity
        else:
            if key in self._positions:
                pos = self._positions[key]
                pos.quantity = max(0, pos.quantity - quantity)
                if pos.quantity == 0:
                    del self._positions[key]
        return order

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_orders(self) -> list[Order]:
        return list(self._orders.values())

    @property
    def total_spent(self) -> float:
        return self._balance


# ---------------------------------------------------------------------------
# Live trader
# ---------------------------------------------------------------------------

class GeminiTrader:
    """
    Places and manages prediction market orders on Gemini.

    Set paper=True to simulate without spending real money.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        paper: bool = True,
    ):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.paper = paper
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "whale-copytrade-bot/1.0"
        self._ledger = PaperLedger()
        log.info("GeminiTrader initialised (paper=%s)", paper)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _auth_headers(self, endpoint: str, payload: dict) -> dict:
        payload = dict(payload)
        payload["request"] = endpoint
        payload["nonce"] = str(int(time.time() * 1000))
        encoded = base64.b64encode(
            json.dumps(payload).encode()
        ).decode()
        sig = hmac.new(
            self.api_secret,
            encoded.encode(),
            hashlib.sha384,
        ).hexdigest()
        return {
            "X-GEMINI-APIKEY": self.api_key,
            "X-GEMINI-PAYLOAD": encoded,
            "X-GEMINI-SIGNATURE": sig,
            "Content-Type": "text/plain",
            "Cache-Control": "no-cache",
        }

    def _post(self, endpoint: str, payload: dict) -> dict:
        headers = self._auth_headers(endpoint, payload)
        resp = self._session.post(
            f"{BASE}{endpoint}",
            headers=headers,
            timeout=TIMEOUT,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            body = resp.text[:400]
            log.error("POST %s failed %s: %s", endpoint, resp.status_code, body)
            raise RuntimeError(f"Gemini API error {resp.status_code}: {body}") from exc
        return resp.json()

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def buy(
        self,
        symbol: str,
        outcome: str,
        usd_amount: float,
        price_hint: float,
    ) -> Optional[Order]:
        """
        Buy `usd_amount` worth of YES or NO contracts.

        price_hint: current ask price (0–1). We place an aggressive limit
        slightly above ask to maximise fill probability.
        """
        # Clamp price to valid range and nudge above ask
        price = min(0.99, max(0.01, round(price_hint + 0.01, 4)))
        quantity = round(usd_amount / price, 2)
        if quantity < 1:
            log.warning("buy: quantity %.2f too small (usd=%.2f price=%.4f)", quantity, usd_amount, price)
            return None
        return self._place_order(symbol, outcome, "buy", quantity, price)

    def sell(
        self,
        symbol: str,
        outcome: str,
        quantity: float,
        price_hint: float,
    ) -> Optional[Order]:
        """Sell `quantity` contracts, priced slightly below bid."""
        price = min(0.99, max(0.01, round(price_hint - 0.01, 4)))
        return self._place_order(symbol, outcome, "sell", quantity, price)

    def _place_order(
        self,
        symbol: str,
        outcome: str,
        side: str,
        quantity: float,
        price: float,
    ) -> Optional[Order]:
        log.info(
            "Placing %s %s order: %s %.2f contracts @ %.4f (paper=%s)",
            side, outcome.upper(), symbol, quantity, price, self.paper,
        )

        if self.paper:
            order = self._ledger.place_order(symbol, outcome, side, quantity, price)
            log.info("[PAPER] Order filled: %s", order.order_id)
            return order

        try:
            data = self._post("/v1/prediction-markets/order", {
                "symbol": symbol,
                "orderType": "limit",
                "side": side,
                "quantity": str(round(quantity, 2)),
                "price": str(price),
                "outcome": outcome,
                "timeInForce": "good-til-cancel",
            })
            return Order(
                order_id=str(data.get("orderId", data.get("order_id", ""))),
                symbol=symbol,
                outcome=outcome,
                side=side,
                quantity=quantity,
                price=price,
                status=data.get("status", "live"),
                executed_amount=float(data.get("executedAmount", 0)),
                avg_execution_price=float(data.get("avgExecutionPrice", price)),
            )
        except Exception as exc:
            log.error("place_order failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> bool:
        if self.paper:
            log.info("[PAPER] Cancel %s", order_id)
            return True
        try:
            self._post("/v1/prediction-markets/orders/cancel", {"orderId": order_id})
            return True
        except Exception as exc:
            log.error("cancel_order(%s) failed: %s", order_id, exc)
            return False

    def get_active_orders(self) -> list[Order]:
        if self.paper:
            return [o for o in self._ledger.get_orders() if o.status == "live"]
        try:
            data = self._post("/v1/prediction-markets/orders/active", {})
            return [self._parse_order(o) for o in data if isinstance(data, list)]
        except Exception as exc:
            log.error("get_active_orders failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> list[Position]:
        if self.paper:
            return self._ledger.get_positions()
        try:
            data = self._post("/v1/prediction-markets/positions", {})
            return [self._parse_position(p) for p in data if isinstance(data, list)]
        except Exception as exc:
            log.error("get_positions failed: %s", exc)
            return []

    @property
    def paper_total_spent(self) -> float:
        return self._ledger.total_spent if self.paper else 0.0

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_order(data: dict) -> Order:
        return Order(
            order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            outcome=data.get("outcome", "").lower(),
            side=data.get("side", "").lower(),
            quantity=float(data.get("originalAmount", 0)),
            price=float(data.get("price", 0)),
            status=data.get("status", ""),
            executed_amount=float(data.get("executedAmount", 0)),
            avg_execution_price=float(data.get("avgExecutionPrice", 0)),
        )

    @staticmethod
    def _parse_position(data: dict) -> Position:
        return Position(
            symbol=data.get("symbol", ""),
            outcome=data.get("outcome", "").lower(),
            quantity=float(data.get("quantity", 0)),
            avg_cost=float(data.get("avgCost", 0)),
            current_price=float(data.get("lastPrice", 0)),
        )
