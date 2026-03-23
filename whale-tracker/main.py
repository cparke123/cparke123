#!/usr/bin/env python3
"""
Polymarket BTC Whale Tracker
============================
Tracks whale wallets betting on Bitcoin price direction on Polymarket.

Usage:
    python main.py                        # one-shot snapshot
    python main.py --loop 60              # refresh every 60 seconds
    python main.py --wallet 0xABCD...     # inspect a specific wallet
    python main.py --threshold 5000       # set whale trade threshold ($)
    python main.py --top 20               # show top 20 whales
    python main.py --mode trades          # detect via recent trades (slower, more data)
    python main.py --mode positions       # detect via top position holders (faster)

Dependencies: requests, rich  (pip install requests rich)
"""

import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box
from rich.live import Live
from rich.spinner import Spinner
from rich.layout import Layout

import polymarket_client as pm
import market_finder as mf
import whale_tracker as wt

console = Console()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def format_usd(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if abs(val) >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:.2f}"


def pnl_color(val: float) -> str:
    return "green" if val >= 0 else "red"


def direction_color(outcome: str) -> str:
    return "green" if outcome.upper() == "YES" else "red"


def price_bar(price: float, width: int = 20) -> str:
    filled = int(price * width)
    empty = width - filled
    return f"[green]{'█' * filled}[/green][dim]{'░' * empty}[/dim]"


def print_header():
    console.print(Panel.fit(
        "[bold cyan]🐳  Polymarket BTC Whale Tracker[/bold cyan]\n"
        "[dim]Tracking large wallets betting on Bitcoin price direction[/dim]",
        border_style="cyan",
    ))
    console.print(f"[dim]Refreshed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n")


def print_markets_table(markets: list[dict]):
    table = Table(
        title="[bold]Active Bitcoin Markets[/bold]",
        box=box.ROUNDED,
        border_style="blue",
        show_header=True,
        header_style="bold blue",
        expand=True,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Question", ratio=3)
    table.add_column("YES Price", justify="right", width=10)
    table.add_column("NO Price", justify="right", width=10)
    table.add_column("Volume 24h", justify="right", width=12)
    table.add_column("Total Volume", justify="right", width=14)
    table.add_column("Liquidity", justify="right", width=12)

    for i, m in enumerate(markets, 1):
        yes_p = m.get("yes_price", 0)
        no_p = m.get("no_price", 0)
        question = m.get("question", "")
        if len(question) > 58:
            question = question[:55] + "…"

        table.add_row(
            str(i),
            question,
            f"[green]{yes_p:.2%}[/green]",
            f"[red]{no_p:.2%}[/red]",
            format_usd(m.get("volume_24h", 0)),
            format_usd(m.get("volume", 0)),
            format_usd(m.get("liquidity", 0)),
        )

    console.print(table)
    console.print()


def print_whales_table(whales: list[wt.Whale], top_n: int = 15):
    if not whales:
        console.print("[yellow]No whales found meeting the threshold criteria.[/yellow]\n")
        return

    table = Table(
        title=f"[bold]Top {min(len(whales), top_n)} Whale Wallets[/bold]",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Wallet Address", width=16)
    table.add_column("Net Direction", ratio=2)
    table.add_column("Open Value", justify="right", width=12)
    table.add_column("Unrealized P&L", justify="right", width=14)
    table.add_column("Trade Volume", justify="right", width=12)
    table.add_column("Positions", justify="center", width=9)

    for i, whale in enumerate(whales[:top_n], 1):
        addr_short = whale.address[:6] + "…" + whale.address[-4:]
        link = f"[link=https://polymarket.com/profile/{whale.address}]{addr_short}[/link]"

        direction = whale.net_direction
        if "BULLISH" in direction:
            dir_text = f"[green]{direction}[/green]"
        elif "BEARISH" in direction:
            dir_text = f"[red]{direction}[/red]"
        else:
            dir_text = f"[yellow]{direction}[/yellow]"

        pnl = whale.total_pnl
        pnl_text = f"[{pnl_color(pnl)}]{format_usd(pnl)}[/{pnl_color(pnl)}]"

        table.add_row(
            str(i),
            link,
            dir_text,
            format_usd(whale.total_usd_value),
            pnl_text,
            format_usd(whale.total_volume),
            str(len(whale.positions)),
        )

    console.print(table)
    console.print()


def print_whale_detail(whale: wt.Whale):
    console.print(Panel(
        f"[bold cyan]Wallet:[/bold cyan] {whale.address}\n"
        f"[bold]Profile:[/bold] https://polymarket.com/profile/{whale.address}\n"
        f"[bold]Net Direction:[/bold] {whale.net_direction}\n"
        f"[bold]Total Open Value:[/bold] {format_usd(whale.total_usd_value)}\n"
        f"[bold]Total Unrealized P&L:[/bold] [{pnl_color(whale.total_pnl)}]{format_usd(whale.total_pnl)}[/{pnl_color(whale.total_pnl)}]\n"
        f"[bold]Lifetime Trade Volume:[/bold] {format_usd(whale.total_volume)}",
        title="[bold cyan]🐳 Whale Detail[/bold cyan]",
        border_style="cyan",
    ))

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Market", min_width=40, max_width=60)
    table.add_column("Bet", width=12)
    table.add_column("Price Bar", width=24)
    table.add_column("Avg Price", justify="right", width=10)
    table.add_column("Current", justify="right", width=10)
    table.add_column("Shares", justify="right", width=10)
    table.add_column("USD Value", justify="right", width=10)
    table.add_column("P&L", justify="right", width=10)

    for pos in sorted(whale.positions, key=lambda p: p.usd_value, reverse=True):
        q = pos.market_question
        if len(q) > 58:
            q = q[:55] + "…"

        outcome_color = direction_color(pos.outcome)
        bet_label = (
            f"[{outcome_color}]{'▲ UP   (YES)' if pos.outcome.upper() == 'YES' else '▼ DOWN (NO )'}[/{outcome_color}]"
        )

        pnl_str = f"[{pnl_color(pos.unrealized_pnl)}]{format_usd(pos.unrealized_pnl)}[/{pnl_color(pos.unrealized_pnl)}]"

        table.add_row(
            q,
            bet_label,
            price_bar(pos.current_price),
            f"{pos.avg_price:.2%}",
            f"{pos.current_price:.2%}",
            f"{pos.size:,.0f}",
            format_usd(pos.usd_value),
            pnl_str,
        )

    console.print(table)


def print_sentiment_summary(whales: list[wt.Whale]):
    """Print an overall BTC sentiment summary from whale positions."""
    total_yes = sum(
        p.usd_value for w in whales for p in w.positions if p.outcome.upper() == "YES"
    )
    total_no = sum(
        p.usd_value for w in whales for p in w.positions if p.outcome.upper() == "NO"
    )
    total = total_yes + total_no

    if total == 0:
        return

    yes_pct = total_yes / total
    no_pct = total_no / total

    bar_width = 40
    yes_blocks = int(yes_pct * bar_width)
    no_blocks = bar_width - yes_blocks

    bar = f"[green]{'█' * yes_blocks}[/green][red]{'█' * no_blocks}[/red]"
    label = f"[green]▲ BULLISH {yes_pct:.1%}[/green]  {bar}  [red]{no_pct:.1%} BEARISH ▼[/red]"

    console.print(Panel(
        f"Whale Money Flow (open positions)\n\n"
        f"  {label}\n\n"
        f"  [green]YES (Up):[/green] {format_usd(total_yes)}   "
        f"[red]NO (Down):[/red] {format_usd(total_no)}   "
        f"[dim]Total: {format_usd(total)}[/dim]",
        title="[bold]📊 Whale Sentiment — Bitcoin[/bold]",
        border_style="yellow",
    ))
    console.print()


# ---------------------------------------------------------------------------
# Core run logic
# ---------------------------------------------------------------------------

def run_demo(args):
    """Run with mock data for demo/testing purposes."""
    import demo_data
    print_header()
    console.print("[bold yellow]⚠  DEMO MODE — using mock data (no network required)[/bold yellow]\n")
    markets = demo_data.DEMO_MARKETS
    whales = demo_data.DEMO_WHALES
    print_markets_table(markets)
    print_sentiment_summary(whales)
    print_whales_table(whales, top_n=args.top)
    if args.wallet:
        whale = next((w for w in whales if w.address.lower() == args.wallet.lower()), None)
        if whale:
            print_whale_detail(whale)
        else:
            console.print(f"[yellow]Wallet {args.wallet} not found in demo whales.[/yellow]")
    return whales, markets


def run_snapshot(args):
    print_header()

    # Step 1: Find BTC markets
    try:
        with console.status("[cyan]Searching for Bitcoin markets on Polymarket…[/cyan]"):
            markets_raw = mf.find_btc_markets()
            markets = [mf.summarize_market(m) for m in markets_raw]
            markets.sort(key=lambda m: m["volume"], reverse=True)
            markets_display = markets[:20]
            markets_scan = markets[:10]
    except Exception as e:
        console.print(f"[red]Network error: {e}[/red]")
        console.print(
            "[yellow]Tip: Run with [bold]--demo[/bold] to see the UI with mock data, "
            "or ensure you have internet access to reach polymarket.com[/yellow]"
        )
        sys.exit(1)

    if not markets:
        console.print("[red]No Bitcoin markets found. Polymarket may be unavailable or no active BTC markets exist.[/red]")
        console.print("[dim]Tip: Run with --demo to use mock data.[/dim]")
        sys.exit(1)

    console.print(f"[green]Found {len(markets)} Bitcoin directional markets.[/green] "
                  f"Scanning top {len(markets_scan)} by volume.\n")
    print_markets_table(markets_display)

    # Step 2: Find whales
    whales = []
    if args.mode == "trades":
        with console.status("[cyan]Scanning recent trades for whale activity…[/cyan]"):
            candidates = wt.detect_whales_from_trades(markets_scan, threshold=args.threshold)
        console.print(f"[green]Found {len(candidates)} candidate whale wallets from trades.[/green]\n")
        with console.status("[cyan]Fetching whale positions…[/cyan]"):
            whales = wt.build_whale_profiles(candidates, markets_scan)
    else:  # positions mode (faster)
        with console.status("[cyan]Fetching top position holders…[/cyan]"):
            whales = wt.get_top_holders(markets_scan, limit=args.top)

    # Step 3: Display results
    print_sentiment_summary(whales)
    print_whales_table(whales, top_n=args.top)

    return whales, markets_scan


def inspect_wallet(address: str, markets: list[dict]):
    """Fetch and display detail for a specific wallet."""
    console.print(f"\n[cyan]Fetching positions for wallet: {address}[/cyan]\n")

    # Build token lookup
    token_lookup: dict[str, dict] = {}
    market_lookup: dict[str, dict] = {}
    for m in markets:
        market_lookup[m["condition_id"]] = m
        for token in m.get("tokens", []):
            token_lookup[token["token_id"]] = {
                "market_question": m.get("question", ""),
                "condition_id": m.get("condition_id", ""),
                "outcome": token["outcome"],
                "current_price": token["price"],
            }

    try:
        raw_positions = pm.get_positions(user_address=address, limit=200)
    except Exception as e:
        console.print(f"[red]Error fetching positions: {e}[/red]")
        return

    positions = []
    for pos in raw_positions:
        size = float(pos.get("size", 0) or 0)
        if size < 0.01:
            continue

        token_id = pos.get("asset", pos.get("tokenId", ""))
        condition_id = pos.get("conditionId", "")
        avg_price = float(pos.get("avgPrice") or pos.get("price", 0) or 0)

        if token_id in token_lookup:
            info = token_lookup[token_id]
            current_price = info["current_price"] or avg_price
            outcome = info["outcome"]
            market_question = info["market_question"]
        elif condition_id in market_lookup:
            m = market_lookup[condition_id]
            current_price = avg_price
            outcome = pos.get("outcome", "YES")
            market_question = m.get("question", condition_id[:16])
        else:
            current_price = avg_price
            outcome = pos.get("outcome", "YES")
            market_question = condition_id[:16] + "…" if condition_id else "Unknown"

        positions.append(wt.WhalePosition(
            market_question=market_question,
            condition_id=condition_id,
            outcome=outcome,
            size=size,
            avg_price=avg_price,
            current_price=current_price,
            usd_value=size * current_price,
            unrealized_pnl=(current_price - avg_price) * size,
        ))

    if not positions:
        console.print("[yellow]No open positions found for this wallet in tracked BTC markets.[/yellow]")
        # Show all positions anyway
        console.print("[dim]Tip: This wallet may not have positions in the currently active BTC markets.[/dim]")
        return

    whale = wt.Whale(address=address, positions=positions)
    whale.recompute_totals()
    print_whale_detail(whale)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Track whale wallets on Polymarket betting on Bitcoin price direction."
    )
    parser.add_argument(
        "--loop", type=int, default=0, metavar="SECONDS",
        help="Refresh interval in seconds (0 = run once, default: 0)",
    )
    parser.add_argument(
        "--wallet", type=str, default="", metavar="ADDRESS",
        help="Inspect a specific wallet proxy address",
    )
    parser.add_argument(
        "--threshold", type=float, default=wt.WHALE_TRADE_THRESHOLD, metavar="USD",
        help=f"Minimum single-trade size to qualify as whale (default: ${wt.WHALE_TRADE_THRESHOLD:,})",
    )
    parser.add_argument(
        "--top", type=int, default=15, metavar="N",
        help="Number of top whales to display (default: 15)",
    )
    parser.add_argument(
        "--mode", choices=["trades", "positions"], default="positions",
        help="Detection mode: 'positions' (faster) or 'trades' (more detail). Default: positions",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Run with mock data (no network required) to preview the UI",
    )
    args = parser.parse_args()

    if args.demo:
        run_demo(args)
        return

    if args.loop > 0:
        console.print(f"[dim]Running in loop mode, refreshing every {args.loop}s. Press Ctrl+C to stop.[/dim]\n")
        while True:
            try:
                whales, markets = run_snapshot(args)
                if args.wallet:
                    inspect_wallet(args.wallet, markets)
                console.print(f"[dim]Next refresh in {args.loop}s…[/dim]\n")
                time.sleep(args.loop)
                console.clear()
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopped.[/yellow]")
                break
    else:
        whales, markets = run_snapshot(args)
        if args.wallet:
            inspect_wallet(args.wallet, markets)


if __name__ == "__main__":
    main()
