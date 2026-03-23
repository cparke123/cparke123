"""
Monitors Polymarket BTC 15-minute markets for unusually large trades.

Emits TradeSignal objects when a whale trade is detected.
Designed to run in a background thread.
"""

from __future__ import annotations
import logging
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional
import polymarket_client as pm
from btc_15min_markets import BTC15MinMarket, BTC15MinMarketFinder
import config

log = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """A signal emitted when a whale places a large trade."""
    direction: str          # "UP" (YES) or "DOWN" (NO)
    usd_size: float         # USD value of the trade
    market: BTC15MinMarket  # Which market the trade happened in
    wallet_address: str     # Whale's proxy wallet
    outcome: str            # "YES" or "NO"
    timestamp: float        # Unix timestamp when trade was detected
    trade_id: str           # Unique trade ID from Polymarket
    market_yes_price: float # Current YES price at time of signal
    market_no_price: float  # Current NO price at time of signal

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    @property
    def is_fresh(self) -> bool:
        return self.age_seconds <= config.MAX_SIGNAL_AGE_SECONDS

    def __str__(self) -> str:
        return (
            f"[SIGNAL] {self.direction} ${self.usd_size:,.0f} on "
            f"'{self.market.question[:50]}' "
            f"by {self.wallet_address[:10]}… "
            f"(YES={self.market_yes_price:.2%} NO={self.market_no_price:.2%})"
        )


class SignalMonitor:
    """
    Polls Polymarket trades every POLL_INTERVAL_SECONDS.
    When a trade exceeds WHALE_THRESHOLD_USD, calls the provided callback
    with a TradeSignal.

    Usage:
        monitor = SignalMonitor(on_signal=my_callback)
        monitor.start()
        ...
        monitor.stop()
    """

    def __init__(
        self,
        on_signal: Callable[[TradeSignal], None],
        market_finder: Optional[BTC15MinMarketFinder] = None,
    ):
        self._on_signal = on_signal
        self._market_finder = market_finder or BTC15MinMarketFinder()
        self._seen_trade_ids: set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Track recent signals per direction for confirmation logic
        # deque of timestamps
        self._recent_signals: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        """Start monitoring in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="SignalMonitor")
        self._thread.start()
        log.info("SignalMonitor started (poll interval: %ds, whale threshold: $%s)",
                 config.POLL_INTERVAL_SECONDS, f"{config.WHALE_THRESHOLD_USD:,}")

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("SignalMonitor stopped")

    def get_recent_signals(self, direction: str, window_seconds: int) -> int:
        """Count how many signals in the given direction occurred within window."""
        cutoff = time.time() - window_seconds
        return sum(1 for t in self._recent_signals[direction] if t >= cutoff)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _loop(self):
        log.info("Signal monitor loop running")
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                log.error("Poll error: %s", e, exc_info=True)
            time.sleep(config.POLL_INTERVAL_SECONDS)

    def _poll_once(self):
        markets = self._market_finder.get_markets()
        if not markets:
            log.debug("No active BTC 15-min markets found, skipping poll")
            return

        for market in markets:
            self._poll_market(market)

    def _poll_market(self, market: BTC15MinMarket):
        """Fetch recent trades for a market and check for whale activity."""
        try:
            trades = pm.get_trades(
                market_id=market.condition_id,
                limit=50,   # last 50 trades is plenty for a 15-min window
            )
        except Exception as e:
            log.warning("Failed to fetch trades for %s: %s", market.condition_id[:10], e)
            return

        if not isinstance(trades, list):
            return

        for trade in trades:
            self._process_trade(trade, market)

    def _process_trade(self, trade: dict, market: BTC15MinMarket):
        """Evaluate a single trade and emit a signal if it qualifies."""
        trade_id = trade.get("id") or trade.get("transactionHash", "")
        if not trade_id or trade_id in self._seen_trade_ids:
            return
        self._seen_trade_ids.add(trade_id)

        # Parse trade size
        usd_size = self._parse_usd_size(trade)
        if usd_size < config.WHALE_THRESHOLD_USD:
            return

        # Parse timestamp — Polymarket returns Unix ms or ISO string
        timestamp = self._parse_timestamp(trade)

        # Only act on fresh trades (prevents acting on old data after restart)
        age = time.time() - timestamp
        if age > config.MAX_SIGNAL_AGE_SECONDS:
            log.debug("Skipping old trade %s (age %.0fs)", trade_id[:10], age)
            return

        # Determine direction from outcome
        outcome = (trade.get("outcome") or trade.get("side") or "").upper()
        if not outcome:
            # Fall back: infer from token ID
            token_id = trade.get("assetId") or trade.get("tokenId", "")
            if token_id == market.yes_token_id:
                outcome = "YES"
            elif token_id == market.no_token_id:
                outcome = "NO"
            else:
                log.debug("Cannot determine outcome for trade %s", trade_id[:10])
                return

        direction = "UP" if outcome == "YES" else "DOWN"

        # Optional: require market to agree with whale direction
        if config.REQUIRE_MARKET_AGREEMENT:
            if direction == "UP" and market.yes_price < 0.5:
                log.debug("Skipping UP signal — market disagrees (YES=%.2f)", market.yes_price)
                return
            if direction == "DOWN" and market.no_price < 0.5:
                log.debug("Skipping DOWN signal — market disagrees (NO=%.2f)", market.no_price)
                return

        # Get wallet address (prefer taker, then maker)
        wallet = trade.get("takerAddress") or trade.get("makerAddress", "")

        signal = TradeSignal(
            direction=direction,
            usd_size=usd_size,
            market=market,
            wallet_address=wallet,
            outcome=outcome,
            timestamp=timestamp,
            trade_id=trade_id,
            market_yes_price=market.yes_price,
            market_no_price=market.no_price,
        )

        # Track for confirmation counting
        self._recent_signals[direction].append(timestamp)

        # Check confirmation threshold
        confirming = self.get_recent_signals(direction, config.CONFIRMATION_WINDOW_SECONDS)
        if confirming < config.SIGNAL_CONFIRMATION_COUNT:
            log.info(
                "Signal needs %d confirmations, have %d: %s",
                config.SIGNAL_CONFIRMATION_COUNT, confirming, signal
            )
            return

        log.info("WHALE SIGNAL: %s", signal)
        self._on_signal(signal)

    @staticmethod
    def _parse_usd_size(trade: dict) -> float:
        for key in ("usdcSize", "size", "amount", "collateralAmount"):
            val = trade.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return 0.0

    @staticmethod
    def _parse_timestamp(trade: dict) -> float:
        """Return Unix timestamp (seconds) from trade dict."""
        for key in ("timestamp", "createdAt", "blockTimestamp", "matchTime"):
            val = trade.get(key)
            if val is None:
                continue
            if isinstance(val, (int, float)):
                # Could be ms or seconds
                ts = float(val)
                return ts / 1000 if ts > 1e12 else ts
            if isinstance(val, str):
                import datetime
                try:
                    dt = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
                    return dt.timestamp()
                except ValueError:
                    try:
                        return float(val)
                    except ValueError:
                        pass
        return time.time()  # fall back to now
