#!/usr/bin/env python3
"""
Polymarket BTC Whale → Gemini Auto-Trader
==========================================

Monitors Polymarket BTC 15-minute prediction markets for unusually large
whale trades, then automatically executes a corresponding BTC buy or sell
on Gemini exchange.

Signal flow:
  Whale bets YES (BTC goes up)  → BUY  BTC on Gemini
  Whale bets NO  (BTC goes down) → SELL BTC on Gemini

Position management:
  - Stop-loss:  close if BTC moves against you by STOP_LOSS_PCT
  - Take-profit: close if BTC moves in your favor by TAKE_PROFIT_PCT
  - Auto-close: close after AUTO_CLOSE_AFTER_SECONDS (default: 900 = 15min)
  - Cooldown:   minimum COOLDOWN_SECONDS between new trades

Usage:
    python auto_trader.py              # run live (paper mode by default)
    python auto_trader.py --live       # enable real trading (requires API keys)
    python auto_trader.py --demo       # show UI with mock signals, no trading
    python auto_trader.py --status     # show current config and exit

Dependencies: pip install requests rich ccxt
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
from rich.text import Text
from rich import box

import config
from btc_15min_markets import BTC15MinMarketFinder, BTC15MinMarket
from signal_monitor import SignalMonitor, TradeSignal
from gemini_trader import GeminiTrader, Position

console = Console()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(level: str = config.LOG_LEVEL):
    numeric = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=numeric,
        format=fmt,
        handlers=[
            logging.FileHandler(config.LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )

log = logging.getLogger("auto_trader")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def fmt_usd(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}${val:,.2f}"


def pnl_style(val: float) -> str:
    return "green" if val >= 0 else "red"


def direction_emoji(d: str) -> str:
    return "▲" if d == "UP" else "▼"


def build_status_panel(
    trader: GeminiTrader,
    markets: list[BTC15MinMarket],
    signal_log: list[str],
    last_signal: Optional[TradeSignal],
) -> Panel:
    status = trader.status_dict()
    mode_label = "[yellow]PAPER[/yellow]" if status["paper_mode"] else "[red bold]LIVE[/red bold]"
    price = status["btc_price"]
    unreal = status["unrealized_pnl"]
    real = status["realized_pnl"]

    lines = [
        f"[bold]Mode:[/bold] {mode_label}   "
        f"[bold]BTC/USD:[/bold] [cyan]${price:,.2f}[/cyan]   "
        f"[bold]Open:[/bold] {status['open_positions']} (${status['total_open_usd']:,.0f})   "
        f"[bold]Unrealized P&L:[/bold] [{pnl_style(unreal)}]{fmt_usd(unreal)}[/{pnl_style(unreal)}]   "
        f"[bold]Realized P&L:[/bold] [{pnl_style(real)}]{fmt_usd(real)}[/{pnl_style(real)}]   "
        f"[bold]Trades:[/bold] {status['total_trades']}",
        "",
        f"[bold]Tracking:[/bold] {len(markets)} active BTC 15-min market(s)   "
        f"[bold]Whale threshold:[/bold] ${config.WHALE_THRESHOLD_USD:,}   "
        f"[bold]Trade size:[/bold] ${config.TRADE_SIZE_USD:,.0f}",
    ]

    if last_signal:
        age = int(last_signal.age_seconds)
        lines.append(
            f"\n[bold]Last signal:[/bold] "
            f"{direction_emoji(last_signal.direction)} [bold]{last_signal.direction}[/bold] "
            f"${last_signal.usd_size:,.0f} on '{last_signal.market.question[:45]}…'  ({age}s ago)"
        )

    return Panel(
        "\n".join(lines),
        title=f"[bold cyan]🐳 Polymarket → Gemini Auto-Trader[/bold cyan]  "
              f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]",
        border_style="cyan",
    )


def build_positions_table(trader: GeminiTrader) -> Table:
    table = Table(
        title="Open Positions",
        box=box.SIMPLE,
        header_style="bold",
        expand=True,
    )
    table.add_column("Side", width=6)
    table.add_column("Signal", width=6)
    table.add_column("Entry Price", justify="right", width=12)
    table.add_column("Size BTC", justify="right", width=10)
    table.add_column("Size USD", justify="right", width=10)
    table.add_column("Stop Loss", justify="right", width=12)
    table.add_column("Take Profit", justify="right", width=12)
    table.add_column("Age", justify="right", width=8)
    table.add_column("Unreal. P&L", justify="right", width=12)

    try:
        price = trader.get_btc_price()
    except Exception:
        price = 0.0

    if not trader.open_positions:
        table.add_row("[dim]No open positions[/dim]", *[""] * 8)
    else:
        for pos in trader.open_positions:
            pnl = pos.pnl(price)
            side_style = "green" if pos.side == "buy" else "red"
            age_s = int(pos.age_seconds)
            age_str = f"{age_s // 60}m{age_s % 60}s"
            table.add_row(
                f"[{side_style}]{pos.side.upper()}[/{side_style}]",
                direction_emoji(pos.signal_direction),
                f"${pos.entry_price:,.2f}",
                f"{pos.size_btc:.6f}",
                f"${pos.size_usd:,.0f}",
                f"[red]${pos.stop_loss_price:,.2f}[/red]",
                f"[green]${pos.take_profit_price:,.2f}[/green]",
                age_str,
                f"[{pnl_style(pnl)}]{fmt_usd(pnl)}[/{pnl_style(pnl)}]",
            )
    return table


def build_signal_log_panel(signal_log: list[str]) -> Panel:
    recent = signal_log[-12:] if signal_log else ["[dim]Waiting for whale signals…[/dim]"]
    return Panel(
        "\n".join(recent),
        title="Signal Log",
        border_style="dim",
    )


def build_markets_table(markets: list[BTC15MinMarket]) -> Table:
    table = Table(
        title="Tracked BTC 15-Min Markets",
        box=box.SIMPLE,
        header_style="bold",
        expand=True,
    )
    table.add_column("Question", ratio=3)
    table.add_column("YES", justify="right", width=7)
    table.add_column("NO", justify="right", width=7)
    table.add_column("Implied", width=8)
    table.add_column("Vol 24h", justify="right", width=10)

    if not markets:
        table.add_row("[yellow]No BTC 15-min markets found yet…[/yellow]", "", "", "", "")
    else:
        for m in markets[:8]:
            q = m.question[:65] + ("…" if len(m.question) > 65 else "")
            imp_color = "green" if m.implied_direction == "UP" else "red"
            table.add_row(
                q,
                f"[green]{m.yes_price:.0%}[/green]",
                f"[red]{m.no_price:.0%}[/red]",
                f"[{imp_color}]{direction_emoji(m.implied_direction)} {m.implied_direction}[/{imp_color}]",
                f"${m.volume_24h:,.0f}",
            )
    return table


def build_closed_table(trader: GeminiTrader) -> Table:
    table = Table(
        title="Closed Trades",
        box=box.SIMPLE,
        header_style="bold",
        expand=True,
    )
    table.add_column("Side", width=6)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("Exit", justify="right", width=10)
    table.add_column("Size USD", justify="right", width=10)
    table.add_column("P&L", justify="right", width=10)
    table.add_column("Duration", justify="right", width=10)
    table.add_column("Reason", width=18)

    recent = list(reversed(trader.closed_positions[-8:]))
    if not recent:
        table.add_row("[dim]No closed trades yet[/dim]", *[""] * 6)
    else:
        for t in recent:
            pnl = t["pnl"]
            dur = int(t["duration_seconds"])
            table.add_row(
                t["side"].upper(),
                f"${t['entry_price']:,.2f}",
                f"${t['exit_price']:,.2f}",
                f"${t['size_usd']:,.0f}",
                f"[{pnl_style(pnl)}]{fmt_usd(pnl)}[/{pnl_style(pnl)}]",
                f"{dur // 60}m{dur % 60}s",
                t["reason"],
            )
    return table


# ---------------------------------------------------------------------------
# Core bot logic
# ---------------------------------------------------------------------------

class AutoTrader:
    """
    Orchestrates the signal monitor and Gemini trader.
    """

    def __init__(self, live: bool = False):
        if live:
            config.PAPER_TRADING = False

        self._trader = GeminiTrader()
        self._market_finder = BTC15MinMarketFinder()
        self._monitor = SignalMonitor(
            on_signal=self._handle_signal,
            market_finder=self._market_finder,
        )

        self._signal_log: list[str] = []
        self._last_signal: Optional[TradeSignal] = None
        self._last_trade_time: float = 0.0
        self._lock = threading.Lock()
        self._running = False

    def _handle_signal(self, signal: TradeSignal):
        """Called from signal monitor thread when a whale trade is detected."""
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            entry = (
                f"[dim]{ts}[/dim] "
                f"{direction_emoji(signal.direction)} "
                f"[bold {'green' if signal.direction == 'UP' else 'red'}]{signal.direction}[/bold {'green' if signal.direction == 'UP' else 'red'}] "
                f"${signal.usd_size:,.0f} whale on '{signal.market.question[:40]}…'"
            )
            self._signal_log.append(entry)
            self._last_signal = signal

            # Cooldown check
            since_last = time.time() - self._last_trade_time
            if since_last < config.COOLDOWN_SECONDS:
                remaining = int(config.COOLDOWN_SECONDS - since_last)
                self._signal_log.append(
                    f"  [dim]↳ Skipped — cooldown active ({remaining}s remaining)[/dim]"
                )
                log.info("Signal skipped — cooldown %ds remaining", remaining)
                return

            # Execute trade
            pos = self._trader.open_position(signal.direction, config.TRADE_SIZE_USD)
            if pos:
                self._last_trade_time = time.time()
                mode = "PAPER" if config.PAPER_TRADING else "LIVE"
                self._signal_log.append(
                    f"  [bold green]↳ [{mode}] {pos.side.upper()} ${pos.size_usd:,.0f} BTC "
                    f"@ ${pos.entry_price:,.2f}[/bold green]"
                )
            else:
                self._signal_log.append("  [yellow]↳ Trade skipped (max exposure reached)[/yellow]")

    def _position_manager_loop(self):
        """Runs in a background thread, checks exit conditions every 5 seconds."""
        while self._running:
            try:
                exits = self._trader.check_exit_conditions()
                for pos, reason in exits:
                    pnl = self._trader.close_position(pos, reason=reason)
                    with self._lock:
                        ts = datetime.now().strftime("%H:%M:%S")
                        pnl_str = fmt_usd(pnl)
                        self._signal_log.append(
                            f"[dim]{ts}[/dim] "
                            f"[{'green' if pnl >= 0 else 'red'}]"
                            f"Closed {pos.side.upper()} @ {reason}: {pnl_str}"
                            f"[/{'green' if pnl >= 0 else 'red'}]"
                        )
            except Exception as e:
                log.error("Position manager error: %s", e)
            time.sleep(5)

    def run(self):
        """Main run loop with live Rich display."""
        self._running = True
        self._monitor.start()

        # Start position manager thread
        pm_thread = threading.Thread(
            target=self._position_manager_loop, daemon=True, name="PositionManager"
        )
        pm_thread.start()

        def on_shutdown(sig, frame):
            console.print("\n[yellow]Shutting down — closing all positions…[/yellow]")
            self._running = False
            self._monitor.stop()
            pnl = self._trader.close_all_positions(reason="shutdown")
            console.print(f"[bold]Final realized P&L: {fmt_usd(pnl)}[/bold]")
            sys.exit(0)

        signal.signal(signal.SIGINT, on_shutdown)
        signal.signal(signal.SIGTERM, on_shutdown)

        with Live(console=console, refresh_per_second=0.5, screen=False) as live:
            while self._running:
                markets = self._market_finder.get_markets()
                layout = Layout()
                layout.split_column(
                    Layout(build_status_panel(
                        self._trader, markets, self._signal_log, self._last_signal
                    ), name="status", size=8),
                    Layout(name="middle"),
                    Layout(name="bottom"),
                )
                layout["middle"].split_row(
                    Layout(build_markets_table(markets), name="markets"),
                    Layout(build_positions_table(self._trader), name="positions"),
                )
                layout["bottom"].split_row(
                    Layout(build_signal_log_panel(self._signal_log), name="signals"),
                    Layout(build_closed_table(self._trader), name="closed"),
                )
                live.update(layout)
                time.sleep(2)


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------

def run_demo():
    """Fire mock signals to demonstrate the UI without network access."""
    import random

    console.print("[yellow bold]DEMO MODE — simulated signals, paper trades on mock prices[/yellow bold]\n")

    class MockTrader(GeminiTrader):
        def __init__(self):
            config.PAPER_TRADING = True
            from gemini_trader import PaperExchange
            # Override paper exchange with a mock price feed
            class MockExchange(PaperExchange):
                def fetch_btc_price(self):
                    return 87_500 + random.uniform(-500, 500)
                def fetch_ticker(self):
                    p = self.fetch_btc_price()
                    return {"last": p, "bid": p - 10, "ask": p + 10}
            self._exchange = MockExchange()
            self._positions = []
            self._closed_positions = []

    from btc_15min_markets import BTC15MinMarket
    DEMO_MARKETS = [
        BTC15MinMarket(
            condition_id="0xaaa",
            question="Will BTC be higher in the next 15 minutes?",
            slug="btc-higher-15min",
            yes_token_id="t1", no_token_id="t2",
            yes_price=0.54, no_price=0.46,
            volume=320_000, volume_24h=48_000,
            end_date="2026-03-23T15:00:00Z",
        ),
        BTC15MinMarket(
            condition_id="0xbbb",
            question="Will BTC price increase in the next 15 minutes?",
            slug="btc-increase-15min",
            yes_token_id="t3", no_token_id="t4",
            yes_price=0.51, no_price=0.49,
            volume=180_000, volume_24h=22_000,
            end_date="2026-03-23T15:00:00Z",
        ),
    ]

    trader = MockTrader()
    signal_log: list[str] = []
    last_signal: list[Optional[TradeSignal]] = [None]
    last_trade_time = [0.0]

    def fire_demo_signal():
        directions = ["UP", "DOWN"]
        d = random.choice(directions)
        size = random.uniform(5_000, 50_000)
        market = random.choice(DEMO_MARKETS)
        sig = TradeSignal(
            direction=d, usd_size=size, market=market,
            wallet_address="0xDEMO" + "0" * 36,
            outcome="YES" if d == "UP" else "NO",
            timestamp=time.time(), trade_id=f"demo-{time.time()}",
            market_yes_price=market.yes_price,
            market_no_price=market.no_price,
        )
        ts = datetime.now().strftime("%H:%M:%S")
        signal_log.append(
            f"[dim]{ts}[/dim] {direction_emoji(d)} "
            f"[bold {'green' if d == 'UP' else 'red'}]{d}[/bold {'green' if d == 'UP' else 'red'}] "
            f"${size:,.0f} whale on '{market.question[:40]}…'"
        )
        last_signal[0] = sig

        since = time.time() - last_trade_time[0]
        if since >= config.COOLDOWN_SECONDS:
            pos = trader.open_position(d, config.TRADE_SIZE_USD)
            if pos:
                last_trade_time[0] = time.time()
                signal_log.append(
                    f"  [bold green]↳ [PAPER] {pos.side.upper()} ${pos.size_usd:,.0f} BTC "
                    f"@ ${pos.entry_price:,.2f}[/bold green]"
                )
        else:
            signal_log.append(f"  [dim]↳ Cooldown active[/dim]")

    def position_manager():
        while True:
            exits = trader.check_exit_conditions()
            for pos, reason in exits:
                pnl = trader.close_position(pos, reason=reason)
                ts = datetime.now().strftime("%H:%M:%S")
                signal_log.append(
                    f"[dim]{ts}[/dim] "
                    f"[{'green' if pnl >= 0 else 'red'}]"
                    f"Closed {pos.side.upper()} @ {reason}: {fmt_usd(pnl)}"
                    f"[/{'green' if pnl >= 0 else 'red'}]"
                )
            time.sleep(5)

    pm_t = threading.Thread(target=position_manager, daemon=True)
    pm_t.start()

    tick = [0]
    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            tick[0] += 1
            if tick[0] % 8 == 0:   # fire a signal every ~16s
                fire_demo_signal()

            markets = DEMO_MARKETS
            layout = Layout()
            layout.split_column(
                Layout(build_status_panel(trader, markets, signal_log, last_signal[0]),
                       name="status", size=8),
                Layout(name="middle"),
                Layout(name="bottom"),
            )
            layout["middle"].split_row(
                Layout(build_markets_table(markets), name="markets"),
                Layout(build_positions_table(trader), name="positions"),
            )
            layout["bottom"].split_row(
                Layout(build_signal_log_panel(signal_log), name="signals"),
                Layout(build_closed_table(trader), name="closed"),
            )
            live.update(layout)
            time.sleep(2)


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def print_status():
    console.print(Panel(
        f"[bold]Paper trading:[/bold] {'YES (safe mode)' if config.PAPER_TRADING else 'NO — REAL MONEY'}\n"
        f"[bold]Gemini API key:[/bold] {'set ✓' if config.GEMINI_API_KEY else 'NOT SET ✗'}\n"
        f"[bold]Gemini secret:[/bold]  {'set ✓' if config.GEMINI_API_SECRET else 'NOT SET ✗'}\n"
        f"\n"
        f"[bold]Whale threshold:[/bold]     ${config.WHALE_THRESHOLD_USD:,} per trade\n"
        f"[bold]Trade size:[/bold]          ${config.TRADE_SIZE_USD:,.0f} per signal\n"
        f"[bold]Max open exposure:[/bold]   ${config.MAX_OPEN_POSITION_USD:,.0f}\n"
        f"[bold]Stop-loss:[/bold]           {config.STOP_LOSS_PCT:.0%}\n"
        f"[bold]Take-profit:[/bold]         {config.TAKE_PROFIT_PCT:.0%}\n"
        f"[bold]Auto-close after:[/bold]    {config.AUTO_CLOSE_AFTER_SECONDS}s "
        f"({config.AUTO_CLOSE_AFTER_SECONDS // 60}m)\n"
        f"[bold]Cooldown:[/bold]            {config.COOLDOWN_SECONDS}s\n"
        f"[bold]Poll interval:[/bold]       {config.POLL_INTERVAL_SECONDS}s\n"
        f"[bold]Signal confirmation:[/bold] {config.SIGNAL_CONFIRMATION_COUNT} "
        f"trade(s) within {config.CONFIRMATION_WINDOW_SECONDS}s\n"
        f"[bold]Log file:[/bold]            {config.LOG_FILE}",
        title="[bold]Auto-Trader Configuration[/bold]",
        border_style="blue",
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket whale → Gemini auto-trader for BTC 15-min markets"
    )
    parser.add_argument("--live", action="store_true",
                        help="Enable real trading (default: paper mode)")
    parser.add_argument("--demo", action="store_true",
                        help="Demo mode with simulated signals (no network needed)")
    parser.add_argument("--status", action="store_true",
                        help="Print current config and exit")
    args = parser.parse_args()

    setup_logging()

    if args.status:
        print_status()
        return

    if args.demo:
        run_demo()
        return

    if args.live and config.PAPER_TRADING:
        console.print(
            "[bold red]WARNING: --live flag passed but PAPER_TRADING=True in config.py\n"
            "Set PAPER_TRADING = False in config.py to enable real trading.[/bold red]\n"
        )
        config.PAPER_TRADING = False

    if not args.live:
        console.print(
            "[yellow]Running in PAPER TRADING mode (no real orders).\n"
            "Pass --live to enable real trading after setting your API keys in config.py[/yellow]\n"
        )

    bot = AutoTrader(live=args.live)
    bot.run()


if __name__ == "__main__":
    main()
