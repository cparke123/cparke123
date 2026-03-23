#!/usr/bin/env python3
"""
Polymarket BTC 15-Min Whale Copy-Trader
========================================

Monitors Polymarket BTC 15-minute prediction markets for unusually large
whale bets, then automatically places the same bet on the same market.

Signal flow:
  Whale bets YES (BTC goes up in 15 min)  → we also bet YES
  Whale bets NO  (BTC goes down in 15 min) → we also bet NO

Position management:
  - Hold to resolution (default — market resolves automatically after 15 min)
  - OR stop-loss:   sell if token price drops STOP_LOSS_PRICE_DROP from entry
  - OR take-profit: sell if token price rises TAKE_PROFIT_PRICE_RISE from entry
  - OR pre-close:   sell AUTO_SELL_BEFORE_CLOSE_SECONDS before market closes

Usage:
    python auto_trader.py                # paper mode (no real USDC)
    python auto_trader.py --live         # real betting (requires credentials)
    python auto_trader.py --derive-keys  # generate L2 API keys from private key
    python auto_trader.py --demo         # simulated signals, no network needed
    python auto_trader.py --status       # print current config and exit

Dependencies: pip install requests rich py-clob-client
"""

import argparse
import logging
import signal
import sys
import time
import threading
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout
from rich import box

import config
from btc_15min_markets import BTC15MinMarketFinder, BTC15MinMarket
from signal_monitor import SignalMonitor, TradeSignal
from polymarket_trader import PolymarketTrader, Bet, derive_and_print_keys

console = Console()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.FileHandler(config.LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )

log = logging.getLogger("auto_trader")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_usd(val: float, sign: bool = True) -> str:
    prefix = ("+" if val > 0 else "") if sign else ""
    return f"{prefix}${val:,.2f}"


def pnl_style(val: float) -> str:
    return "green" if val >= 0 else "red"


def dir_arrow(d: str) -> str:
    return "▲" if d == "UP" else "▼"


def outcome_style(outcome: str) -> str:
    return "green" if outcome.upper() == "YES" else "red"


# ---------------------------------------------------------------------------
# Dashboard panels
# ---------------------------------------------------------------------------

def build_header(trader: PolymarketTrader, markets: list[BTC15MinMarket],
                 last_signal: Optional[TradeSignal]) -> Panel:
    mode = "[yellow]PAPER[/yellow]" if trader.is_paper else "[red bold]LIVE[/red bold]"
    bal = trader.usdc_balance()
    exp = trader.total_open_exposure()
    unreal = trader.total_unrealized_pnl()
    real = trader.total_realized_pnl()

    lines = [
        f"[bold]Mode:[/bold] {mode}   "
        f"[bold]USDC Balance:[/bold] [cyan]${bal:,.2f}[/cyan]   "
        f"[bold]Open Exposure:[/bold] ${exp:,.2f}   "
        f"[bold]Unrealized:[/bold] [{pnl_style(unreal)}]{fmt_usd(unreal)}[/{pnl_style(unreal)}]   "
        f"[bold]Realized:[/bold] [{pnl_style(real)}]{fmt_usd(real)}[/{pnl_style(real)}]   "
        f"[bold]Closed bets:[/bold] {len(trader.closed_bets)}",
        "",
        f"[bold]Tracking:[/bold] {len(markets)} BTC 15-min market(s)   "
        f"[bold]Whale threshold:[/bold] ${config.WHALE_THRESHOLD_USD:,}   "
        f"[bold]Bet size:[/bold] "
        + (f"{config.BET_FRACTION_OF_WHALE:.1%} of whale"
           if config.BET_FRACTION_OF_WHALE > 0
           else f"${config.BET_SIZE_USD:,.0f} fixed"),
    ]

    if last_signal:
        age = int(last_signal.age_seconds)
        lines.append(
            f"\n[bold]Last signal:[/bold] "
            f"{dir_arrow(last_signal.direction)} [bold]{last_signal.direction}[/bold] "
            f"${last_signal.usd_size:,.0f} on '{last_signal.market.question[:45]}…'  ({age}s ago)"
        )

    return Panel(
        "\n".join(lines),
        title=f"[bold cyan]🐳 Polymarket BTC 15-Min Whale Copy-Trader[/bold cyan]  "
              f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]",
        border_style="cyan",
    )


def build_markets_table(markets: list[BTC15MinMarket]) -> Table:
    t = Table(title="BTC 15-Min Markets", box=box.SIMPLE,
              header_style="bold", expand=True)
    t.add_column("Question", ratio=3)
    t.add_column("YES", justify="right", width=7)
    t.add_column("NO", justify="right", width=7)
    t.add_column("Implied", width=10)
    t.add_column("Vol 24h", justify="right", width=10)

    if not markets:
        t.add_row("[yellow]Searching for BTC 15-min markets…[/yellow]", "", "", "", "")
    for m in markets[:6]:
        q = m.question[:65] + ("…" if len(m.question) > 65 else "")
        imp = m.implied_direction
        t.add_row(
            q,
            f"[green]{m.yes_price:.0%}[/green]",
            f"[red]{m.no_price:.0%}[/red]",
            f"[{'green' if imp == 'UP' else 'red'}]{dir_arrow(imp)} {imp}[/]",
            f"${m.volume_24h:,.0f}",
        )
    return t


def build_open_bets_table(trader: PolymarketTrader) -> Table:
    t = Table(title="Open Bets", box=box.SIMPLE,
              header_style="bold", expand=True)
    t.add_column("Outcome", width=6)
    t.add_column("Market", ratio=2)
    t.add_column("Entry", justify="right", width=8)
    t.add_column("Current", justify="right", width=8)
    t.add_column("Shares", justify="right", width=10)
    t.add_column("Cost", justify="right", width=8)
    t.add_column("Value", justify="right", width=8)
    t.add_column("P&L", justify="right", width=10)
    t.add_column("Age", justify="right", width=8)

    bets = trader.open_bets
    if not bets:
        t.add_row("[dim]No open bets[/dim]", *[""] * 8)
    else:
        for bet in bets:
            try:
                if hasattr(trader._backend, "get_token_price"):
                    cur = trader._backend.get_token_price(bet.token_id)
                else:
                    cur = bet.token_entry_price
            except Exception:
                cur = bet.token_entry_price

            pnl = bet.unrealized_pnl(cur)
            age_s = int(bet.age_seconds)
            q = bet.market_question[:40] + ("…" if len(bet.market_question) > 40 else "")
            t.add_row(
                f"[{outcome_style(bet.outcome)}]{bet.outcome}[/{outcome_style(bet.outcome)}]",
                q,
                f"${bet.token_entry_price:.3f}",
                f"${cur:.3f}",
                f"{bet.shares:.1f}",
                f"${bet.cost_usd:.2f}",
                f"${bet.shares * cur:.2f}",
                f"[{pnl_style(pnl)}]{fmt_usd(pnl)}[/{pnl_style(pnl)}]",
                f"{age_s // 60}m{age_s % 60}s",
            )
    return t


def build_signal_log(signal_log: list[str]) -> Panel:
    recent = signal_log[-14:] if signal_log else ["[dim]Waiting for whale signals…[/dim]"]
    return Panel("\n".join(recent), title="Signal & Trade Log", border_style="dim")


def build_closed_table(trader: PolymarketTrader) -> Table:
    t = Table(title="Closed Bets", box=box.SIMPLE,
              header_style="bold", expand=True)
    t.add_column("Outcome", width=6)
    t.add_column("Entry", justify="right", width=8)
    t.add_column("Exit", justify="right", width=8)
    t.add_column("Cost", justify="right", width=8)
    t.add_column("P&L", justify="right", width=10)
    t.add_column("Duration", justify="right", width=10)
    t.add_column("Reason", width=16)

    recent = list(reversed(trader.closed_bets[-8:]))
    if not recent:
        t.add_row("[dim]No closed bets yet[/dim]", *[""] * 6)
    else:
        for b in recent:
            pnl = b["pnl"]
            dur = int(b["age_seconds"])
            t.add_row(
                f"[{outcome_style(b['outcome'])}]{b['outcome']}[/{outcome_style(b['outcome'])}]",
                f"${b['entry_price']:.3f}",
                f"${b['exit_price']:.3f}",
                f"${b['cost_usd']:.2f}",
                f"[{pnl_style(pnl)}]{fmt_usd(pnl)}[/{pnl_style(pnl)}]",
                f"{dur // 60}m{dur % 60}s",
                b["reason"],
            )
    return t


# ---------------------------------------------------------------------------
# Core bot
# ---------------------------------------------------------------------------

class CopyTraderBot:
    def __init__(self, live: bool = False):
        if live:
            config.PAPER_TRADING = False

        self._trader = PolymarketTrader()
        self._market_finder = BTC15MinMarketFinder()
        self._monitor = SignalMonitor(
            on_signal=self._handle_signal,
            market_finder=self._market_finder,
        )

        self._signal_log: list[str] = []
        self._last_signal: Optional[TradeSignal] = None
        self._last_bet_time: float = 0.0
        self._lock = threading.Lock()
        self._running = False

    def _handle_signal(self, signal: TradeSignal):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            outcome = signal.outcome   # "YES" or "NO"
            color = outcome_style(outcome)

            self._signal_log.append(
                f"[dim]{ts}[/dim] {dir_arrow(signal.direction)} "
                f"[bold {color}]{outcome}[/bold {color}] "
                f"${signal.usd_size:,.0f} whale → '{signal.market.question[:40]}…'"
            )
            self._last_signal = signal

            # Cooldown
            since_last = time.time() - self._last_bet_time
            if since_last < config.COOLDOWN_SECONDS:
                remaining = int(config.COOLDOWN_SECONDS - since_last)
                self._signal_log.append(
                    f"  [dim]↳ Cooldown: {remaining}s remaining — skipped[/dim]"
                )
                return

            # Pick the right token to buy
            market = signal.market
            token_id = market.yes_token_id if outcome == "YES" else market.no_token_id
            if not token_id:
                self._signal_log.append("  [red]↳ Token ID missing for this market — skipped[/red]")
                return

            bet_size = self._trader.compute_bet_size(signal.usd_size)

            bet = self._trader.place_bet(
                outcome=outcome,
                token_id=token_id,
                cost_usd=bet_size,
                market_question=market.question,
                condition_id=market.condition_id,
                market_end_date=market.end_date,
            )

            if bet:
                self._last_bet_time = time.time()
                mode = "PAPER" if self._trader.is_paper else "LIVE"
                self._signal_log.append(
                    f"  [bold green]↳ [{mode}] BET {outcome} "
                    f"${bet.cost_usd:.2f} @ ${bet.token_entry_price:.3f} "
                    f"({bet.shares:.1f} shares)[/bold green]"
                )
            else:
                self._signal_log.append("  [yellow]↳ Bet skipped (max exposure or balance)[/yellow]")

    def _position_manager_loop(self):
        while self._running:
            try:
                exits = self._trader.check_exit_conditions()
                for bet, reason in exits:
                    pnl = self._trader.sell_bet(bet, reason)
                    with self._lock:
                        ts = datetime.now().strftime("%H:%M:%S")
                        self._signal_log.append(
                            f"[dim]{ts}[/dim] "
                            f"[{'green' if pnl >= 0 else 'red'}]"
                            f"Closed {bet.outcome} @ {reason}: {fmt_usd(pnl)}"
                            f"[/{'green' if pnl >= 0 else 'red'}]"
                        )
            except Exception as e:
                log.error("Position manager error: %s", e)
            time.sleep(10)

    def run(self):
        self._running = True
        self._monitor.start()

        pm_thread = threading.Thread(
            target=self._position_manager_loop, daemon=True, name="PositionManager"
        )
        pm_thread.start()

        def on_shutdown(sig, frame):
            console.print("\n[yellow]Shutting down — selling all open bets…[/yellow]")
            self._running = False
            self._monitor.stop()
            pnl = self._trader.sell_all(reason="shutdown")
            console.print(f"[bold]Total realized P&L on exit: {fmt_usd(pnl)}[/bold]")
            sys.exit(0)

        signal.signal(signal.SIGINT, on_shutdown)
        signal.signal(signal.SIGTERM, on_shutdown)

        with Live(console=console, refresh_per_second=0.5, screen=False) as live:
            while self._running:
                markets = self._market_finder.get_markets()
                layout = Layout()
                layout.split_column(
                    Layout(build_header(self._trader, markets, self._last_signal),
                           name="header", size=8),
                    Layout(name="middle"),
                    Layout(name="bottom"),
                )
                layout["middle"].split_row(
                    Layout(build_markets_table(markets), name="markets"),
                    Layout(build_open_bets_table(self._trader), name="bets"),
                )
                layout["bottom"].split_row(
                    Layout(build_signal_log(self._signal_log), name="log"),
                    Layout(build_closed_table(self._trader), name="closed"),
                )
                live.update(layout)
                time.sleep(2)


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------

def run_demo():
    import random

    console.print("[yellow bold]DEMO MODE — simulated signals, paper bets, no network required[/yellow bold]\n")

    # Patch config to use paper mode with a mock trader backend
    config.PAPER_TRADING = True

    class MockPaperTrader(PolymarketTrader):
        def __init__(self):
            from polymarket_trader import PaperPolymarketTrader

            class MockBackend(PaperPolymarketTrader):
                def get_token_price(self, token_id: str) -> float:
                    # Drift the price slightly each time
                    base = 0.55 if "yes" in token_id else 0.45
                    return max(0.01, min(0.99, base + random.uniform(-0.05, 0.05)))

                def __init__(self):
                    self._balance_usd = 1_000.0
                    self._bets = []
                    self._closed_bets = []

            self._backend = MockBackend()

        @property
        def is_paper(self):
            return True

    from btc_15min_markets import BTC15MinMarket
    DEMO_MARKETS = [
        BTC15MinMarket(
            condition_id="0xaaa",
            question="Will BTC be higher in the next 15 minutes?",
            slug="btc-higher-15min",
            yes_token_id="yes_token_1",
            no_token_id="no_token_1",
            yes_price=0.54, no_price=0.46,
            volume=320_000, volume_24h=48_000,
            end_date="2026-03-23T23:59:00Z",
        ),
        BTC15MinMarket(
            condition_id="0xbbb",
            question="Will BTC price increase in the next 15 minutes?",
            slug="btc-increase-15min",
            yes_token_id="yes_token_2",
            no_token_id="no_token_2",
            yes_price=0.51, no_price=0.49,
            volume=180_000, volume_24h=22_000,
            end_date="2026-03-23T23:59:00Z",
        ),
    ]

    trader = MockPaperTrader()
    signal_log: list[str] = []
    last_signal: list[Optional[TradeSignal]] = [None]
    last_bet_time = [0.0]

    def fire_demo_signal():
        direction = random.choice(["UP", "DOWN"])
        outcome = "YES" if direction == "UP" else "NO"
        market = random.choice(DEMO_MARKETS)
        size = random.uniform(5_000, 80_000)
        sig = TradeSignal(
            direction=direction, usd_size=size, market=market,
            wallet_address="0xDEMO" + "0" * 36,
            outcome=outcome, timestamp=time.time(),
            trade_id=f"demo-{time.time()}",
            market_yes_price=market.yes_price,
            market_no_price=market.no_price,
        )
        ts = datetime.now().strftime("%H:%M:%S")
        color = outcome_style(outcome)
        signal_log.append(
            f"[dim]{ts}[/dim] {dir_arrow(direction)} "
            f"[bold {color}]{outcome}[/bold {color}] "
            f"${size:,.0f} whale → '{market.question[:40]}…'"
        )
        last_signal[0] = sig

        since = time.time() - last_bet_time[0]
        if since < config.COOLDOWN_SECONDS:
            signal_log.append(f"  [dim]↳ Cooldown active — skipped[/dim]")
            return

        token_id = market.yes_token_id if outcome == "YES" else market.no_token_id
        bet = trader.place_bet(
            outcome=outcome,
            token_id=token_id,
            cost_usd=trader.compute_bet_size(size),
            market_question=market.question,
            condition_id=market.condition_id,
            market_end_date=market.end_date,
        )
        if bet:
            last_bet_time[0] = time.time()
            signal_log.append(
                f"  [bold green]↳ [PAPER] BET {outcome} "
                f"${bet.cost_usd:.2f} @ ${bet.token_entry_price:.3f} "
                f"({bet.shares:.1f} shares)[/bold green]"
            )

    def position_manager():
        while True:
            exits = trader.check_exit_conditions()
            for bet, reason in exits:
                pnl = trader.sell_bet(bet, reason)
                ts = datetime.now().strftime("%H:%M:%S")
                signal_log.append(
                    f"[dim]{ts}[/dim] "
                    f"[{'green' if pnl >= 0 else 'red'}]"
                    f"Closed {bet.outcome} @ {reason}: {fmt_usd(pnl)}"
                    f"[/{'green' if pnl >= 0 else 'red'}]"
                )
            time.sleep(10)

    pm_t = threading.Thread(target=position_manager, daemon=True)
    pm_t.start()

    tick = [0]
    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            tick[0] += 1
            if tick[0] % 8 == 0:
                fire_demo_signal()

            layout = Layout()
            layout.split_column(
                Layout(build_header(trader, DEMO_MARKETS, last_signal[0]),
                       name="header", size=8),
                Layout(name="middle"),
                Layout(name="bottom"),
            )
            layout["middle"].split_row(
                Layout(build_markets_table(DEMO_MARKETS), name="markets"),
                Layout(build_open_bets_table(trader), name="bets"),
            )
            layout["bottom"].split_row(
                Layout(build_signal_log(signal_log), name="log"),
                Layout(build_closed_table(trader), name="closed"),
            )
            live.update(layout)
            time.sleep(2)


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def print_status():
    has_pk = bool(config.POLYMARKET_PRIVATE_KEY)
    has_key = bool(config.POLYMARKET_API_KEY)
    sizing = (f"{config.BET_FRACTION_OF_WHALE:.1%} of whale trade"
              if config.BET_FRACTION_OF_WHALE > 0
              else f"${config.BET_SIZE_USD:,.0f} fixed")

    console.print(Panel(
        f"[bold]Paper trading:[/bold]        {'YES (no real USDC spent)' if config.PAPER_TRADING else 'NO — REAL USDC WILL BE BET'}\n"
        f"[bold]Private key:[/bold]          {'set ✓' if has_pk else 'NOT SET ✗'}\n"
        f"[bold]API key / creds:[/bold]      {'set ✓' if has_key else 'NOT SET — run --derive-keys'}\n"
        f"[bold]Chain:[/bold]                {'Polygon Mainnet (137)' if config.CHAIN_ID == 137 else f'Chain {config.CHAIN_ID}'}\n"
        f"\n"
        f"[bold]Whale threshold:[/bold]      ${config.WHALE_THRESHOLD_USD:,} per trade\n"
        f"[bold]Bet sizing:[/bold]           {sizing}\n"
        f"[bold]Max exposure:[/bold]         ${config.MAX_OPEN_EXPOSURE_USD:,.0f}\n"
        f"[bold]Cooldown:[/bold]             {config.COOLDOWN_SECONDS}s between bets\n"
        f"\n"
        f"[bold]Stop-loss:[/bold]            sell if price drops ${config.STOP_LOSS_PRICE_DROP:.2f} from entry\n"
        f"[bold]Take-profit:[/bold]          sell if price rises ${config.TAKE_PROFIT_PRICE_RISE:.2f} from entry\n"
        f"[bold]Auto-sell before close:[/bold] "
        + (f"{config.AUTO_SELL_BEFORE_CLOSE_SECONDS}s" if config.AUTO_SELL_BEFORE_CLOSE_SECONDS else "disabled (hold to resolution)") + "\n"
        f"\n"
        f"[bold]Poll interval:[/bold]        {config.POLL_INTERVAL_SECONDS}s\n"
        f"[bold]Signal confirmation:[/bold]  {config.SIGNAL_CONFIRMATION_COUNT} trade(s) in {config.CONFIRMATION_WINDOW_SECONDS}s\n"
        f"[bold]Log file:[/bold]             {config.LOG_FILE}",
        title="[bold]Copy-Trader Configuration[/bold]",
        border_style="blue",
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Copy whale bets on Polymarket BTC 15-min markets"
    )
    parser.add_argument("--live", action="store_true",
                        help="Use real USDC (default: paper mode)")
    parser.add_argument("--demo", action="store_true",
                        help="Simulated signals, no network needed")
    parser.add_argument("--status", action="store_true",
                        help="Print config and exit")
    parser.add_argument("--derive-keys", action="store_true",
                        help="Derive L2 API creds from POLYMARKET_PRIVATE_KEY in config.py")
    args = parser.parse_args()

    setup_logging()

    if args.status:
        print_status()
        return

    if args.derive_keys:
        derive_and_print_keys()
        return

    if args.demo:
        run_demo()
        return

    if args.live:
        config.PAPER_TRADING = False
        console.print("[red bold]LIVE MODE — real USDC bets will be placed on Polymarket[/red bold]\n")
    else:
        console.print(
            "[yellow]PAPER MODE — bets are simulated, no real USDC spent.\n"
            "Pass --live after configuring credentials to bet real money.[/yellow]\n"
        )

    bot = CopyTraderBot(live=args.live)
    bot.run()


if __name__ == "__main__":
    main()
