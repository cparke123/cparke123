# 🐳 Polymarket BTC Whale Tracker

A Python CLI bot that tracks whale wallets on [Polymarket](https://polymarket.com) betting on **Bitcoin price direction** (up or down).

## What it does

- Finds all active Bitcoin directional prediction markets on Polymarket
- Identifies **whale wallets** — addresses with large trades (≥$1,000) or big open positions (≥$5,000)
- Shows each whale's **net direction** (bullish/bearish on BTC)
- Displays **unrealized P&L** for each position
- Summarizes overall **whale sentiment** as a money-flow bar
- Can drill into a **specific wallet** for full position breakdown
- Supports **loop mode** to auto-refresh on an interval

## Installation

```bash
pip install requests rich
```

## Usage

```bash
# One-shot snapshot (requires internet access to polymarket.com)
python main.py

# Preview the UI with mock data (no network needed)
python main.py --demo

# Auto-refresh every 60 seconds
python main.py --loop 60

# Inspect a specific wallet's BTC positions
python main.py --wallet 0xYourProxyWalletAddress

# Use trade-scan mode (slower but more thorough)
python main.py --mode trades

# Change the whale threshold and number of results
python main.py --threshold 5000 --top 20
```

## Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--demo` | off | Run with mock data, no network required |
| `--loop N` | 0 | Auto-refresh every N seconds (0 = run once) |
| `--wallet ADDR` | — | Inspect a specific proxy wallet address |
| `--threshold USD` | 1000 | Minimum single-trade size to flag a wallet as whale |
| `--top N` | 15 | Number of top whales to display |
| `--mode` | positions | `positions` (fast) or `trades` (thorough) |

## How it works

### APIs used (all public, no auth required)

| API | Base URL | Used for |
|-----|----------|---------|
| **Gamma API** | `gamma-api.polymarket.com` | Discover Bitcoin markets |
| **Data API** | `data-api.polymarket.com` | Trades, positions, activity |
| **CLOB API** | `clob.polymarket.com` | Live prices |

### Detection modes

**`positions` mode (default, fast)**
Queries each market's position holders and aggregates by wallet address. Shows who currently holds the largest positions.

**`trades` mode (slower, more data)**
Scans recent trades across all BTC markets to find addresses that made large single trades, then fetches their full position data.

### What counts as a whale?

- A **single trade ≥ $1,000** in a Bitcoin market (configurable with `--threshold`), OR
- A **total open position ≥ $5,000** across Bitcoin markets

### Proxy wallets

Polymarket users trade through **proxy wallets** (Gnosis Safe contracts on Polygon), not their MetaMask address. The wallet address shown is the proxy address visible in each user's Polymarket profile URL.

## File structure

```
whale-tracker/
├── main.py              # CLI entry point, display logic
├── polymarket_client.py # Thin wrapper around Polymarket APIs
├── market_finder.py     # Finds and filters BTC directional markets
├── whale_tracker.py     # Whale detection and position aggregation
├── demo_data.py         # Mock data for --demo mode
├── requirements.txt     # Python dependencies
└── README.md
```

## Example output

```
🐳  Polymarket BTC Whale Tracker

Active Bitcoin Markets
┌─────┬──────────────────────────────────────────┬───────────┬──────────┬──────────┐
│  #  │ Question                                 │ YES Price │ NO Price │  Volume  │
├─────┼──────────────────────────────────────────┼───────────┼──────────┼──────────┤
│  1  │ Will Bitcoin be above $100k on Mar 31?   │   62.00%  │  38.00%  │  $4.82M  │
│  2  │ Will Bitcoin be above $90k on Apr 1?     │   78.00%  │  22.00%  │  $2.15M  │
└─────┴──────────────────────────────────────────┴───────────┴──────────┴──────────┘

📊 Whale Sentiment — Bitcoin
▲ BULLISH 61.4%  ████████████████████████░░░░░░░░  38.6% BEARISH ▼
YES (Up): $522K   NO (Down): $328K   Total: $850K

Top Whale Wallets
┌──┬────────────────┬──────────────────────────────────┬───────────┬──────────────┐
│# │ Wallet         │ Net Direction                    │ Open Val  │ Unreal. P&L  │
├──┼────────────────┼──────────────────────────────────┼───────────┼──────────────┤
│1 │ 0xA1B2…ABCD    │ BULLISH ($247K YES vs $0 NO)     │  $247.7K  │     +$27.2K  │
│2 │ 0xDEAD…CDEF    │ BEARISH ($249K NO vs $0 YES)     │  $248.9K  │      -$2.3K  │
└──┴────────────────┴──────────────────────────────────┴───────────┴──────────────┘
```
