#!/usr/bin/env python3
"""
Polymarket Whale Copy-Trader — Weekly Runner
=============================================

Runs the copy-trader for one session per day for 7 consecutive days,
records daily PnL, then prints a full weekly report.

Usage:
    python weekly_runner.py                       # paper mode, 8h sessions
    python weekly_runner.py --session-hours 4     # 4-hour sessions each day
    python weekly_runner.py --live                # REAL money — be sure!
    python weekly_runner.py --report-only         # print saved report without running

The daily PnL data is persisted to pnl_weekly.json so the runner can
survive restarts (it resumes from day N+1 if already completed N days).
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

import config

console = Console()
REPORT_FILE = Path("pnl_weekly.json")

log = logging.getLogger("weekly_runner")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_usd(val: float, sign: bool = True) -> str:
    prefix = ("+" if val > 0 else "") if sign else ""
    color = "green" if val >= 0 else "red"
    return f"[{color}]{prefix}${val:,.2f}[/{color}]"


def fmt_pct(val: float) -> str:
    color = "green" if val >= 0 else "red"
    prefix = "+" if val >= 0 else ""
    return f"[{color}]{prefix}{val:.1f}%[/{color}]"


def load_report() -> dict:
    if REPORT_FILE.exists():
        with REPORT_FILE.open() as f:
            return json.load(f)
    return {"days": [], "start_date": None}


def save_report(data: dict) -> None:
    with REPORT_FILE.open("w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Daily session
# ---------------------------------------------------------------------------

def run_day(day_num: int, session_secs: float, live: bool) -> dict:
    """Run one day's trading session and return the day's summary."""
    # Import here so config is already patched before CopyTraderBot initialises
    from auto_trader import CopyTraderBot

    date_str = datetime.now().strftime("%Y-%m-%d")
    console.print(
        f"\n[bold cyan]Day {day_num}/7 — {date_str}[/bold cyan]  "
        f"Session: {session_secs/3600:.1f}h  "
        f"Bet size: ${config.BET_SIZE_USD:.0f}  "
        f"Mode: {'[red bold]LIVE[/red bold]' if live else '[yellow]PAPER[/yellow]'}\n"
    )

    bot = CopyTraderBot(live=live)
    start = time.time()

    try:
        result = bot.run_timed(session_secs)
    except Exception as exc:
        log.error("Day %d session error: %s", day_num, exc, exc_info=True)
        result = {
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "bets_placed": 0,
            "bets_won": 0,
            "bets_lost": 0,
            "total_wagered": 0.0,
            "error": str(exc),
        }

    elapsed = time.time() - start
    result["date"] = date_str
    result["day"] = day_num
    result["session_hours"] = round(elapsed / 3600, 2)

    roi = (result["realized_pnl"] / result["total_wagered"] * 100
           if result["total_wagered"] > 0 else 0.0)
    result["roi_pct"] = round(roi, 2)

    console.print(
        f"[bold]Day {day_num} complete[/bold] — "
        f"P&L: {fmt_usd(result['realized_pnl'])}  "
        f"Bets: {result['bets_placed']} "
        f"(W:{result['bets_won']} L:{result['bets_lost']})  "
        f"ROI: {fmt_pct(roi)}\n"
    )
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_weekly_report(data: dict) -> None:
    days = data.get("days", [])
    if not days:
        console.print("[yellow]No completed days to report.[/yellow]")
        return

    # Summary table
    table = Table(
        title="Weekly P&L Report — Polymarket Whale Copy-Trader",
        box=box.ROUNDED,
        show_lines=True,
        min_width=80,
    )
    table.add_column("Day", justify="center", style="bold", width=5)
    table.add_column("Date", justify="center", width=12)
    table.add_column("Bets", justify="right", width=6)
    table.add_column("W/L", justify="center", width=8)
    table.add_column("Wagered", justify="right", width=10)
    table.add_column("Realized P&L", justify="right", width=14)
    table.add_column("ROI", justify="right", width=8)

    total_pnl = 0.0
    total_wagered = 0.0
    total_bets = 0
    total_won = 0
    total_lost = 0

    for d in days:
        pnl = d.get("realized_pnl", 0.0)
        wagered = d.get("total_wagered", 0.0)
        roi = d.get("roi_pct", 0.0)
        won = d.get("bets_won", 0)
        lost = d.get("bets_lost", 0)
        placed = d.get("bets_placed", 0)

        total_pnl += pnl
        total_wagered += wagered
        total_bets += placed
        total_won += won
        total_lost += lost

        pnl_color = "green" if pnl >= 0 else "red"
        roi_color = "green" if roi >= 0 else "red"
        prefix = "+" if pnl >= 0 else ""

        table.add_row(
            str(d.get("day", "?")),
            d.get("date", "?"),
            str(placed),
            f"{won}W / {lost}L",
            f"${wagered:,.2f}",
            f"[{pnl_color}]{prefix}${pnl:,.2f}[/{pnl_color}]",
            f"[{roi_color}]{'+' if roi >= 0 else ''}{roi:.1f}%[/{roi_color}]",
        )

    # Totals row
    overall_roi = (total_pnl / total_wagered * 100) if total_wagered > 0 else 0.0
    pnl_color = "green" if total_pnl >= 0 else "red"
    roi_color = "green" if overall_roi >= 0 else "red"
    prefix = "+" if total_pnl >= 0 else ""

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        "",
        f"[bold]{total_bets}[/bold]",
        f"[bold]{total_won}W / {total_lost}L[/bold]",
        f"[bold]${total_wagered:,.2f}[/bold]",
        f"[bold][{pnl_color}]{prefix}${total_pnl:,.2f}[/{pnl_color}][/bold]",
        f"[bold][{roi_color}]{'+' if overall_roi >= 0 else ''}{overall_roi:.1f}%[/{roi_color}][/bold]",
    )

    console.print()
    console.print(table)

    # Win-rate summary
    win_rate = (total_won / total_bets * 100) if total_bets > 0 else 0.0
    summary_lines = [
        f"Bet size per trade:  ${config.BET_SIZE_USD:.0f}",
        f"Total days run:      {len(days)}/7",
        f"Win rate:            {win_rate:.1f}%  ({total_won}W / {total_lost}L)",
        f"Total wagered:       ${total_wagered:,.2f}",
        f"Net P&L:             {'+'if total_pnl>=0 else ''}${total_pnl:,.2f}",
        f"Overall ROI:         {'+'if overall_roi>=0 else ''}{overall_roi:.1f}%",
    ]
    console.print(Panel(
        "\n".join(summary_lines),
        title="Summary",
        border_style="cyan",
        width=50,
    ))
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run the copy-trader daily for 7 days")
    parser.add_argument("--session-hours", type=float, default=8.0,
                        help="Hours to trade each day (default: 8)")
    parser.add_argument("--live", action="store_true",
                        help="Use real USDC (default: paper mode)")
    parser.add_argument("--report-only", action="store_true",
                        help="Print the saved weekly report and exit")
    parser.add_argument("--reset", action="store_true",
                        help="Delete saved progress and start fresh")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )

    if args.reset and REPORT_FILE.exists():
        REPORT_FILE.unlink()
        console.print("[yellow]Reset: cleared saved weekly progress.[/yellow]")

    data = load_report()

    if args.report_only:
        print_weekly_report(data)
        return

    if not args.live:
        config.PAPER_TRADING = True
        console.print("[yellow]PAPER MODE — no real USDC will be spent.[/yellow]\n")
    else:
        if not config.POLYMARKET_PRIVATE_KEY:
            console.print("[red bold]ERROR: POLYMARKET_PRIVATE_KEY not set in config.py[/red bold]")
            sys.exit(1)
        config.PAPER_TRADING = False
        console.print("[red bold]LIVE MODE — real USDC will be bet.[/red bold]\n")

    if data["start_date"] is None:
        data["start_date"] = datetime.now().strftime("%Y-%m-%d")

    completed_days = {d["day"] for d in data["days"]}
    session_secs = args.session_hours * 3600

    console.print(Panel(
        f"Polymarket Whale Copy-Trader — 7-Day Run\n\n"
        f"Start date:    {data['start_date']}\n"
        f"Session length: {args.session_hours:.1f}h / day\n"
        f"Bet size:       ${config.BET_SIZE_USD:.0f} per signal\n"
        f"Mode:           {'LIVE' if args.live else 'PAPER'}\n"
        f"Days completed: {len(completed_days)}/7",
        title="Weekly Runner",
        border_style="cyan",
    ))

    for day_num in range(1, 8):
        if day_num in completed_days:
            console.print(f"[dim]Day {day_num} already completed — skipping.[/dim]")
            continue

        result = run_day(day_num, session_secs, live=args.live)
        data["days"].append(result)
        save_report(data)

        if day_num < 7:
            # Wait until the same time tomorrow
            next_run = datetime.now() + timedelta(days=1)
            wait_secs = (next_run - datetime.now()).total_seconds()
            console.print(
                f"[dim]Next session in {wait_secs/3600:.1f}h "
                f"(~{next_run.strftime('%Y-%m-%d %H:%M')}).[/dim]"
            )
            try:
                time.sleep(wait_secs)
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted — progress saved. Re-run to continue.[/yellow]")
                break

    print_weekly_report(data)


if __name__ == "__main__":
    main()
