"""
Gemini Prediction Markets — Public API client.

Covers unauthenticated endpoints:
  GET /v1/prediction-markets/events           list active events
  GET /v1/prediction-markets/events/{ticker}  event detail
  GET /v1/prediction-markets/categories       available categories
  GET /v1/trades/{symbol}                     recent public trades (shared engine)
"""

from __future__ import annotations
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
class Contract:
    """A single YES or NO contract within an event."""
    instrument_symbol: str   # e.g. "GEMI-BTCJUN25-YES"
    outcome: str             # "yes" or "no"
    last_price: float        # 0–1
    bid: float
    ask: float


@dataclass
class PredictionEvent:
    """A prediction market event containing YES + NO contracts."""
    ticker: str              # e.g. "BTCJUN25"
    title: str
    status: str              # active | closed | settled | ...
    category: str
    end_date: Optional[str]
    contracts: list[Contract] = field(default_factory=list)

    @property
    def yes_contract(self) -> Optional[Contract]:
        for c in self.contracts:
            if c.outcome == "yes":
                return c
        return None

    @property
    def no_contract(self) -> Optional[Contract]:
        for c in self.contracts:
            if c.outcome == "no":
                return c
        return None


@dataclass
class PublicTrade:
    """A single executed trade from the public trades endpoint."""
    trade_id: str
    timestamp: float       # unix seconds
    price: float           # 0–1 (the contract price paid)
    amount: float          # number of contracts
    side: str              # "buy" or "sell" (aggressor side)
    symbol: str            # instrument symbol

    @property
    def usd_value(self) -> float:
        return self.price * self.amount

    @property
    def outcome(self) -> str:
        """Derive YES/NO from symbol (e.g. GEMI-BTC100K-YES → yes)."""
        sym = self.symbol.upper()
        if sym.endswith("-YES"):
            return "yes"
        if sym.endswith("-NO"):
            return "no"
        return "unknown"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GeminiPublicClient:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "whale-copytrade-bot/1.0"

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{BASE}{path}"
        resp = self._session.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Events / markets
    # ------------------------------------------------------------------

    def get_active_events(self, category: str | None = None) -> list[PredictionEvent]:
        """Return all active prediction market events."""
        params: dict = {"status": "active"}
        if category:
            params["category"] = category
        try:
            data = self._get("/v1/prediction-markets/events", params=params)
        except Exception as exc:
            log.warning("get_active_events failed: %s", exc)
            return []

        events = []
        for item in data if isinstance(data, list) else data.get("events", []):
            events.append(self._parse_event(item))
        return events

    def get_event(self, ticker: str) -> Optional[PredictionEvent]:
        """Return detailed info for a single event."""
        try:
            data = self._get(f"/v1/prediction-markets/events/{ticker}")
            return self._parse_event(data)
        except Exception as exc:
            log.warning("get_event(%s) failed: %s", ticker, exc)
            return None

    def _parse_event(self, data: dict) -> PredictionEvent:
        contracts = []
        for c in data.get("contracts", []):
            contracts.append(Contract(
                instrument_symbol=c.get("instrumentSymbol", ""),
                outcome=c.get("outcome", "").lower(),
                last_price=float(c.get("lastPrice", 0) or 0),
                bid=float(c.get("bid", 0) or 0),
                ask=float(c.get("ask", 0) or 0),
            ))
        return PredictionEvent(
            ticker=data.get("ticker", ""),
            title=data.get("title", data.get("question", "")),
            status=data.get("status", ""),
            category=data.get("category", ""),
            end_date=data.get("endDate"),
            contracts=contracts,
        )

    # ------------------------------------------------------------------
    # Public trades
    # ------------------------------------------------------------------

    def get_recent_trades(
        self,
        instrument_symbol: str,
        since_timestamp_ms: int | None = None,
    ) -> list[PublicTrade]:
        """
        Return recently executed trades for a prediction market contract.

        Uses the shared market-data trades endpoint:
          GET /v1/trades/{symbol}?timestamp=<ms>
        """
        params: dict = {"limit_trades": 500}
        if since_timestamp_ms:
            params["timestamp"] = since_timestamp_ms

        try:
            data = self._get(f"/v1/trades/{instrument_symbol}", params=params)
        except Exception as exc:
            log.debug("get_recent_trades(%s) failed: %s", instrument_symbol, exc)
            return []

        trades = []
        for t in data if isinstance(data, list) else []:
            trades.append(PublicTrade(
                trade_id=str(t.get("tid", t.get("tradeId", ""))),
                timestamp=float(t.get("timestampms", t.get("timestamp", 0))) / 1000
                          if "timestampms" in t else float(t.get("timestamp", 0)),
                price=float(t.get("price", 0)),
                amount=float(t.get("amount", 0)),
                side=t.get("type", t.get("side", "")).lower(),
                symbol=instrument_symbol,
            ))
        return trades
