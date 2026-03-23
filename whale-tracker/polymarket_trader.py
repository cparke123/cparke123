"""
Polymarket copy-trader using the official py-clob-client SDK.

Places bets on Polymarket's CLOB directly, mirroring whale trades on BTC
15-minute markets.

Authentication:
  Polymarket uses a two-layer auth scheme:
    Level 1 (L1): Ethereum private key — used to derive the L2 key
    Level 2 (L2): API key/secret/passphrase — used to sign CLOB requests
  Run `python auto_trader.py --derive-keys` to generate your L2 creds.

Order flow:
  Whale bets YES  →  we buy the YES token in that market (Fill-or-Kill)
  Whale bets NO   →  we buy the NO  token in that market (Fill-or-Kill)

Position exit:
  - Hold to resolution (market auto-resolves and pays winners)
  - OR sell early via stop-loss / take-profit price checks
  - OR sell N seconds before close (AUTO_SELL_BEFORE_CLOSE_SECONDS)
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
import config

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    MarketOrderArgs,
    OrderType,
    BalanceAllowanceParams,
    AssetType,
)

log = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Bet:
    """A single open copy-trade bet on Polymarket."""
    market_question: str
    condition_id: str
    outcome: str            # "YES" or "NO"
    token_id: str
    token_entry_price: float  # price paid per share (0–1)
    shares: float             # number of outcome tokens bought
    cost_usd: float           # USDC spent
    placed_at: float          # Unix timestamp
    order_id: str = ""
    market_end_date: str = ""  # ISO8601 when the market resolves

    @property
    def age_seconds(self) -> float:
        return time.time() - self.placed_at

    def current_value(self, current_price: float) -> float:
        """Estimated current value in USDC."""
        return self.shares * current_price

    def unrealized_pnl(self, current_price: float) -> float:
        return self.current_value(current_price) - self.cost_usd

    def pnl_pct(self, current_price: float) -> float:
        return self.unrealized_pnl(current_price) / self.cost_usd if self.cost_usd else 0.0

    def price_change(self, current_price: float) -> float:
        """How much the token price has moved since entry."""
        return current_price - self.token_entry_price


# ---------------------------------------------------------------------------
# Paper trader (simulates bets locally, uses real public price data)
# ---------------------------------------------------------------------------

class PaperPolymarketTrader:
    """Simulates Polymarket bets without spending USDC."""

    def __init__(self):
        # Use a read-only public CLOB client for price data
        self._clob = ClobClient(host=CLOB_HOST, chain_id=config.CHAIN_ID)
        self._balance_usd = 1_000.0
        self._bets: list[Bet] = []
        self._closed_bets: list[dict] = []

    def get_token_price(self, token_id: str) -> float:
        """Fetch the current mid-price for a token."""
        try:
            result = self._clob.get_midpoint(token_id=token_id)
            return float(result.get("mid", 0.5))
        except Exception:
            return 0.5

    def place_bet(
        self,
        outcome: str,
        token_id: str,
        cost_usd: float,
        market_question: str,
        condition_id: str,
        market_end_date: str = "",
    ) -> Optional[Bet]:
        """Simulate placing a bet. Returns the Bet or None if balance insufficient."""
        if cost_usd > self._balance_usd:
            log.warning("[PAPER] Insufficient balance: have $%.2f, need $%.2f",
                        self._balance_usd, cost_usd)
            return None

        price = self.get_token_price(token_id)
        if price <= 0:
            price = 0.5  # fallback

        shares = cost_usd / price
        self._balance_usd -= cost_usd

        bet = Bet(
            market_question=market_question,
            condition_id=condition_id,
            outcome=outcome,
            token_id=token_id,
            token_entry_price=price,
            shares=shares,
            cost_usd=cost_usd,
            placed_at=time.time(),
            order_id=f"paper-{int(time.time() * 1000)}",
            market_end_date=market_end_date,
        )
        self._bets.append(bet)

        log.info(
            "[PAPER] BET %s $%.2f → %.2f shares @ $%.4f on '%s'",
            outcome, cost_usd, shares, price, market_question[:50],
        )
        return bet

    def sell_bet(self, bet: Bet, reason: str = "manual") -> float:
        """Simulate selling a bet before resolution. Returns realized P&L."""
        if bet not in self._bets:
            return 0.0

        price = self.get_token_price(bet.token_id)
        proceeds = bet.shares * price
        pnl = proceeds - bet.cost_usd
        self._balance_usd += proceeds
        self._bets.remove(bet)

        self._closed_bets.append({
            "outcome": bet.outcome,
            "market_question": bet.market_question,
            "entry_price": bet.token_entry_price,
            "exit_price": price,
            "shares": bet.shares,
            "cost_usd": bet.cost_usd,
            "proceeds_usd": proceeds,
            "pnl": pnl,
            "age_seconds": bet.age_seconds,
            "reason": reason,
        })

        log.info("[PAPER] SELL %s @ $%.4f | P&L: $%.2f | reason: %s",
                 bet.outcome, price, pnl, reason)
        return pnl

    @property
    def open_bets(self) -> list[Bet]:
        return list(self._bets)

    @property
    def closed_bets(self) -> list[dict]:
        return list(self._closed_bets)

    def usdc_balance(self) -> float:
        return self._balance_usd

    def total_open_exposure(self) -> float:
        return sum(b.cost_usd for b in self._bets)

    def total_realized_pnl(self) -> float:
        return sum(b["pnl"] for b in self._closed_bets)

    def total_unrealized_pnl(self) -> float:
        total = 0.0
        for bet in self._bets:
            try:
                price = self.get_token_price(bet.token_id)
                total += bet.unrealized_pnl(price)
            except Exception:
                pass
        return total


# ---------------------------------------------------------------------------
# Live trader (real CLOB orders)
# ---------------------------------------------------------------------------

class LivePolymarketTrader:
    """Places real Polymarket bets via the CLOB API."""

    def __init__(self):
        if not config.POLYMARKET_PRIVATE_KEY:
            raise ValueError(
                "POLYMARKET_PRIVATE_KEY not set in config.py.\n"
                "Run `python auto_trader.py --derive-keys` to generate credentials."
            )

        creds = None
        if config.POLYMARKET_API_KEY:
            creds = ApiCreds(
                api_key=config.POLYMARKET_API_KEY,
                api_secret=config.POLYMARKET_API_SECRET,
                api_passphrase=config.POLYMARKET_API_PASSPHRASE,
            )

        self._clob = ClobClient(
            host=CLOB_HOST,
            chain_id=config.CHAIN_ID,
            key=config.POLYMARKET_PRIVATE_KEY,
            creds=creds,
            signature_type=0,   # EOA (MetaMask-style wallet)
        )

        # If no creds provided, derive them now (requires a signed request)
        if not creds:
            log.info("Deriving L2 API credentials from private key…")
            derived = self._clob.create_or_derive_api_creds()
            self._clob.set_api_creds(derived)
            log.info("L2 creds derived. api_key=%s", derived.api_key)

        log.info("Connected to Polymarket CLOB as %s", self._clob.get_address())
        self._bets: list[Bet] = []
        self._closed_bets: list[dict] = []

    def get_token_price(self, token_id: str) -> float:
        try:
            result = self._clob.get_midpoint(token_id=token_id)
            return float(result.get("mid", 0.5))
        except Exception:
            return 0.5

    def usdc_balance(self) -> float:
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self._clob.get_balance_allowance(params)
            return float(result.get("balance", 0)) / 1e6   # USDC is 6 decimals
        except Exception as e:
            log.warning("Could not fetch USDC balance: %s", e)
            return 0.0

    def place_bet(
        self,
        outcome: str,
        token_id: str,
        cost_usd: float,
        market_question: str,
        condition_id: str,
        market_end_date: str = "",
    ) -> Optional[Bet]:
        """
        Buy YES or NO tokens via a Fill-or-Kill market order.
        cost_usd is the USDC amount to spend.
        """
        balance = self.usdc_balance()
        if cost_usd > balance:
            log.warning("Insufficient USDC: have $%.2f, need $%.2f", balance, cost_usd)
            return None

        # Get current price before ordering
        price = self.get_token_price(token_id)

        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=cost_usd,      # USD amount to spend (BUY side)
            side="BUY",
            price=price,
            order_type=OrderType.FOK,
        )

        try:
            signed_order = self._clob.create_market_order(order_args)
            resp = self._clob.post_order(signed_order, OrderType.FOK)
        except Exception as e:
            log.error("Order placement failed: %s", e)
            return None

        order_id = resp.get("orderID", resp.get("id", ""))
        status = resp.get("status", "")

        if status not in ("matched", "placed", "live"):
            log.warning("Order not filled: status=%s resp=%s", status, resp)
            return None

        # Estimate shares from fill (FOK fills immediately or not at all)
        filled_usd = float(resp.get("matchedAmount", cost_usd) or cost_usd)
        shares = filled_usd / price if price > 0 else 0

        bet = Bet(
            market_question=market_question,
            condition_id=condition_id,
            outcome=outcome,
            token_id=token_id,
            token_entry_price=price,
            shares=shares,
            cost_usd=filled_usd,
            placed_at=time.time(),
            order_id=order_id,
            market_end_date=market_end_date,
        )
        self._bets.append(bet)

        log.info(
            "[LIVE] BET %s $%.2f → %.2f shares @ $%.4f on '%s' (order %s)",
            outcome, filled_usd, shares, price, market_question[:50], order_id,
        )
        return bet

    def sell_bet(self, bet: Bet, reason: str = "manual") -> float:
        """Sell (close) an open bet by posting a SELL market order."""
        if bet not in self._bets:
            return 0.0

        price = self.get_token_price(bet.token_id)

        order_args = MarketOrderArgs(
            token_id=bet.token_id,
            amount=bet.shares,    # number of shares to sell
            side="SELL",
            price=price,
            order_type=OrderType.FOK,
        )

        try:
            signed_order = self._clob.create_market_order(order_args)
            resp = self._clob.post_order(signed_order, OrderType.FOK)
        except Exception as e:
            log.error("Sell order failed: %s", e)
            return 0.0

        proceeds = bet.shares * price
        pnl = proceeds - bet.cost_usd
        self._bets.remove(bet)

        self._closed_bets.append({
            "outcome": bet.outcome,
            "market_question": bet.market_question,
            "entry_price": bet.token_entry_price,
            "exit_price": price,
            "shares": bet.shares,
            "cost_usd": bet.cost_usd,
            "proceeds_usd": proceeds,
            "pnl": pnl,
            "age_seconds": bet.age_seconds,
            "reason": reason,
        })

        log.info("[LIVE] SELL %s @ $%.4f | P&L: $%.2f | reason: %s",
                 bet.outcome, price, pnl, reason)
        return pnl

    @property
    def open_bets(self) -> list[Bet]:
        return list(self._bets)

    @property
    def closed_bets(self) -> list[dict]:
        return list(self._closed_bets)

    def total_open_exposure(self) -> float:
        return sum(b.cost_usd for b in self._bets)

    def total_realized_pnl(self) -> float:
        return sum(b["pnl"] for b in self._closed_bets)

    def total_unrealized_pnl(self) -> float:
        total = 0.0
        for bet in self._bets:
            try:
                price = self.get_token_price(bet.token_id)
                total += bet.unrealized_pnl(price)
            except Exception:
                pass
        return total


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------

class PolymarketTrader:
    """
    Façade that picks PaperPolymarketTrader or LivePolymarketTrader based on
    config.PAPER_TRADING, and exposes a unified interface for the bot.
    """

    def __init__(self):
        if config.PAPER_TRADING:
            log.info("PolymarketTrader: PAPER mode — no real USDC will be spent")
            self._backend = PaperPolymarketTrader()
        else:
            log.info("PolymarketTrader: LIVE mode — real USDC bets will be placed")
            self._backend = LivePolymarketTrader()

    def compute_bet_size(self, whale_usd: float) -> float:
        """Return the USDC amount to bet based on config sizing rules."""
        if config.BET_FRACTION_OF_WHALE > 0:
            return round(whale_usd * config.BET_FRACTION_OF_WHALE, 2)
        return config.BET_SIZE_USD

    def place_bet(
        self,
        outcome: str,
        token_id: str,
        cost_usd: float,
        market_question: str,
        condition_id: str,
        market_end_date: str = "",
    ) -> Optional[Bet]:
        # Guard: check max exposure
        if self._backend.total_open_exposure() + cost_usd > config.MAX_OPEN_EXPOSURE_USD:
            log.warning("Max exposure reached ($%.0f) — skipping bet",
                        config.MAX_OPEN_EXPOSURE_USD)
            return None
        return self._backend.place_bet(
            outcome, token_id, cost_usd, market_question, condition_id, market_end_date
        )

    def sell_bet(self, bet: Bet, reason: str = "manual") -> float:
        return self._backend.sell_bet(bet, reason)

    def sell_all(self, reason: str = "shutdown") -> float:
        total = 0.0
        for bet in list(self._backend.open_bets):
            total += self._backend.sell_bet(bet, reason)
        return total

    def check_exit_conditions(self) -> list[tuple[Bet, str]]:
        """
        Returns (bet, reason) pairs for bets that should be closed.
        Checks: stop-loss, take-profit, auto-sell before close.
        """
        exits = []
        for bet in self._backend.open_bets:
            try:
                price = self._backend.get_token_price(bet.token_id)
            except Exception:
                continue

            price_change = bet.price_change(price)

            # Stop-loss: price dropped significantly from entry
            if config.STOP_LOSS_PRICE_DROP > 0:
                if price_change <= -config.STOP_LOSS_PRICE_DROP:
                    exits.append((bet, "stop_loss"))
                    continue

            # Take-profit: price rose significantly from entry
            if config.TAKE_PROFIT_PRICE_RISE > 0:
                if price_change >= config.TAKE_PROFIT_PRICE_RISE:
                    exits.append((bet, "take_profit"))
                    continue

            # Auto-sell before market close
            if config.AUTO_SELL_BEFORE_CLOSE_SECONDS > 0 and bet.market_end_date:
                import datetime
                try:
                    end = datetime.datetime.fromisoformat(
                        bet.market_end_date.replace("Z", "+00:00")
                    )
                    now = datetime.datetime.now(datetime.timezone.utc)
                    secs_to_close = (end - now).total_seconds()
                    if secs_to_close <= config.AUTO_SELL_BEFORE_CLOSE_SECONDS:
                        exits.append((bet, "pre_close_sell"))
                        continue
                except ValueError:
                    pass

        return exits

    # Delegate status properties
    @property
    def open_bets(self) -> list[Bet]:
        return self._backend.open_bets

    @property
    def closed_bets(self) -> list[dict]:
        return self._backend.closed_bets

    def usdc_balance(self) -> float:
        return self._backend.usdc_balance()

    def total_open_exposure(self) -> float:
        return self._backend.total_open_exposure()

    def total_realized_pnl(self) -> float:
        return self._backend.total_realized_pnl()

    def total_unrealized_pnl(self) -> float:
        return self._backend.total_unrealized_pnl()

    @property
    def is_paper(self) -> bool:
        return config.PAPER_TRADING


# ---------------------------------------------------------------------------
# Key derivation utility (called from auto_trader --derive-keys)
# ---------------------------------------------------------------------------

def derive_and_print_keys():
    """
    Derives L2 API credentials from the configured private key and prints
    them so the user can copy them into config.py.
    """
    if not config.POLYMARKET_PRIVATE_KEY:
        print("ERROR: Set POLYMARKET_PRIVATE_KEY in config.py first.")
        return

    print("Connecting to Polymarket CLOB to derive L2 API credentials…")
    client = ClobClient(
        host=CLOB_HOST,
        chain_id=config.CHAIN_ID,
        key=config.POLYMARKET_PRIVATE_KEY,
        signature_type=0,
    )
    creds = client.create_or_derive_api_creds()
    print("\n=== Paste these into config.py ===")
    print(f'POLYMARKET_API_KEY        = "{creds.api_key}"')
    print(f'POLYMARKET_API_SECRET     = "{creds.api_secret}"')
    print(f'POLYMARKET_API_PASSPHRASE = "{creds.api_passphrase}"')
    print("===================================\n")
