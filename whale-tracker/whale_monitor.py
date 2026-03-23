"""
Whale monitor for Gemini Prediction Markets.

Polls public trade feeds across all active prediction market events.
Emits WhaleSignal when a single trade exceeds WHALE_THRESHOLD_USD.

Signal flow:
  whale buys  YES contracts  →  signal direction "YES"
  whale buys  NO  contracts  →  signal direction "NO"
  whale sells YES contracts  →  signal direction "NO"  (bearish)
  whale sells NO  contracts  →  signal direction "YES" (bullish)
"""

from __future__ import annotations
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import config
from gemini_client import GeminiPublicClient, PredictionEvent, PublicTrade

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

@dataclass
class WhaleSignal:
    """Emitted when a whale-sized trade is detected."""
    direction: str           # "YES" or "NO"
    outcome: str             # "yes" or "no"  (which contract to bet on)
    usd_size: float
    trade: PublicTrade
    event: PredictionEvent
    timestamp: float = field(default_factory=time.time)

    @property
    def instrument_symbol(self) -> str:
        """Symbol of the contract to copy-trade."""
        if self.outcome == "yes":
            c = self.event.yes_contract
        else:
            c = self.event.no_contract
        return c.instrument_symbol if c else ""

    @property
    def price_hint(self) -> float:
        """Current ask price for our target contract."""
        if self.outcome == "yes":
            c = self.event.yes_contract
        else:
            c = self.event.no_contract
        return c.ask if c else 0.5

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    def __str__(self) -> str:
        return (
            f"[WHALE] {self.direction} ${self.usd_size:,.0f} on "
            f"'{self.event.title[:60]}' "
            f"(trade={self.trade.trade_id} side={self.trade.side})"
        )


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

SignalCallback = Callable[[WhaleSignal], None]


class WhaleMonitor:
    """
    Background thread that polls Gemini prediction market trades and
    calls `on_signal` whenever a whale-sized trade is detected.
    """

    def __init__(self, on_signal: SignalCallback):
        self._on_signal = on_signal
        self._client = GeminiPublicClient()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Track seen trade IDs per symbol to avoid duplicate signals
        self._seen: dict[str, set[str]] = {}
        # Cache events so we don't re-fetch on every poll
        self._events: list[PredictionEvent] = []
        self._events_fetched_at: float = 0.0
        self._events_ttl: float = 60.0  # re-fetch events every 60 s

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="WhaleMonitor")
        self._thread.start()
        log.info("WhaleMonitor started (threshold=$%s, poll=%ss)",
                 config.WHALE_THRESHOLD_USD, config.POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                log.error("WhaleMonitor poll error: %s", exc, exc_info=True)
            self._stop.wait(config.POLL_INTERVAL_SECONDS)

    def _poll_once(self) -> None:
        events = self._get_events()
        if not events:
            log.debug("No active prediction market events found")
            return

        for event in events:
            for contract in event.contracts:
                sym = contract.instrument_symbol
                if not sym:
                    continue
                self._check_contract(event, sym)

    def _check_contract(self, event: PredictionEvent, symbol: str) -> None:
        seen = self._seen.setdefault(symbol, set())
        trades = self._client.get_recent_trades(symbol)

        for trade in trades:
            if trade.trade_id in seen:
                continue
            seen.add(trade.trade_id)

            # Skip old trades (older than our max signal age)
            if trade.timestamp < time.time() - config.MAX_SIGNAL_AGE_SECONDS:
                continue

            usd = trade.usd_value
            if usd < config.WHALE_THRESHOLD_USD:
                continue

            signal = self._build_signal(trade, event)
            if signal:
                log.info("Whale detected: %s", signal)
                try:
                    self._on_signal(signal)
                except Exception as exc:
                    log.error("Signal callback error: %s", exc)

    # ------------------------------------------------------------------
    # Signal interpretation
    # ------------------------------------------------------------------

    @staticmethod
    def _build_signal(trade: PublicTrade, event: PredictionEvent) -> Optional[WhaleSignal]:
        """
        Determine the copy-trade direction from a public trade.

        Aggressor bought YES  → we bet YES (whale is bullish)
        Aggressor bought NO   → we bet NO  (whale is bearish)
        Aggressor sold  YES   → we bet NO  (whale is dumping YES = bearish)
        Aggressor sold  NO    → we bet YES (whale is dumping NO  = bullish)
        """
        contract_outcome = trade.outcome   # "yes" | "no" | "unknown"
        side = trade.side                  # "buy" | "sell"

        if contract_outcome == "unknown":
            log.debug("Unknown outcome for symbol %s — skipping", trade.symbol)
            return None

        if side == "buy":
            direction = contract_outcome.upper()   # YES or NO
            copy_outcome = contract_outcome
        else:
            # Selling YES = bearish → bet NO; selling NO = bullish → bet YES
            copy_outcome = "no" if contract_outcome == "yes" else "yes"
            direction = copy_outcome.upper()

        return WhaleSignal(
            direction=direction,
            outcome=copy_outcome,
            usd_size=trade.usd_value,
            trade=trade,
            event=event,
            timestamp=trade.timestamp,
        )

    # ------------------------------------------------------------------
    # Event cache
    # ------------------------------------------------------------------

    def _get_events(self) -> list[PredictionEvent]:
        now = time.time()
        if now - self._events_fetched_at > self._events_ttl or not self._events:
            self._events = self._client.get_active_events()
            self._events_fetched_at = now
            log.debug("Refreshed %d active events", len(self._events))
        return self._events

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def watched_contracts(self) -> int:
        return sum(len(e.contracts) for e in self._events)
