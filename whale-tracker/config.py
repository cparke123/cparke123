"""
Configuration for the Polymarket BTC 15-min whale copy-trader.

Copy this file and fill in your credentials before running in live mode.
All dollar amounts are in USD.

HOW TO GET YOUR POLYMARKET CREDENTIALS:
  1. Go to polymarket.com and connect your wallet
  2. Your POLYMARKET_PRIVATE_KEY is the private key of the wallet you use
     to sign into Polymarket (MetaMask, etc.)
  3. Run: python auto_trader.py --derive-keys
     This will print your API key/secret/passphrase — copy them here.
  4. Make sure your wallet has USDC on Polygon for placing bets.
"""

# ---------------------------------------------------------------------------
# Polymarket trading credentials
# ---------------------------------------------------------------------------

# Your Ethereum private key (the wallet you use on polymarket.com).
# Used to derive the L2 API key and sign orders.
# NEVER share this. Start with 0x...
POLYMARKET_PRIVATE_KEY = ""

# L2 API credentials — generated from your private key.
# Run `python auto_trader.py --derive-keys` to generate these.
POLYMARKET_API_KEY = ""
POLYMARKET_API_SECRET = ""
POLYMARKET_API_PASSPHRASE = ""

# Polygon chain ID (137 = mainnet, 80002 = Amoy testnet)
# Use 80002 + PAPER_TRADING=True while testing
CHAIN_ID = 137

# Paper trading: simulate bets without spending USDC.
# Set to False only when you are ready to spend real money.
PAPER_TRADING = True

# ---------------------------------------------------------------------------
# Signal detection settings
# ---------------------------------------------------------------------------

# Minimum single-trade size (USD) on Polymarket to trigger a copy
WHALE_THRESHOLD_USD = 5_000

# How often to poll Polymarket for new trades (seconds)
POLL_INTERVAL_SECONDS = 10

# Ignore signals older than this many seconds (prevents replaying history
# after a restart)
MAX_SIGNAL_AGE_SECONDS = 30

# ---------------------------------------------------------------------------
# Bet sizing
# ---------------------------------------------------------------------------

# Fixed USDC amount to bet per signal.
BET_SIZE_USD = 50.0

# OR: bet this fraction of the whale's trade size (0 = use BET_SIZE_USD).
# Example: 0.01 = bet 1% of what the whale bet.
BET_FRACTION_OF_WHALE = 0.0

# Maximum total USDC committed across all open bets.
MAX_OPEN_EXPOSURE_USD = 300.0

# Minimum seconds between placing new bets (cooldown).
COOLDOWN_SECONDS = 60

# ---------------------------------------------------------------------------
# Exit strategy
# ---------------------------------------------------------------------------

# Most 15-min markets resolve on their own — you can just hold to resolution.
# Set AUTO_SELL_BEFORE_CLOSE_SECONDS > 0 to sell your position N seconds
# before the market closes (useful to lock in gains or cut losses early).
AUTO_SELL_BEFORE_CLOSE_SECONDS = 0   # 0 = hold to resolution

# Sell if the token price drops this much from your entry (as a fraction).
# Example: 0.30 = sell if price drops 30 cents on a token you bought at $0.60
STOP_LOSS_PRICE_DROP = 0.30

# Sell if the token price rises this much from your entry.
TAKE_PROFIT_PRICE_RISE = 0.30

# ---------------------------------------------------------------------------
# Signal filtering
# ---------------------------------------------------------------------------

# Require the market's implied probability to agree with the whale's direction.
# (YES > 50% for UP signals, NO > 50% for DOWN signals)
REQUIRE_MARKET_AGREEMENT = False

# Number of whale trades in the same direction within CONFIRMATION_WINDOW_SECONDS
# before we act. Set to 1 to copy every qualifying whale trade immediately.
SIGNAL_CONFIRMATION_COUNT = 1
CONFIRMATION_WINDOW_SECONDS = 120

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = "whale_trader.log"
LOG_LEVEL = "INFO"    # DEBUG | INFO | WARNING | ERROR
