"""
Configuration for the Polymarket → Gemini auto-trader.

Copy this file and fill in your real API keys before running in live mode.
All dollar amounts are in USD.
"""

# ---------------------------------------------------------------------------
# Gemini API credentials
# Get these from: https://exchange.gemini.com/settings/api
# Required scopes: "Fund Management" + "Trading" (read + create orders)
# ---------------------------------------------------------------------------
GEMINI_API_KEY = ""       # e.g. "account-xxxxxxxxxxxxxxxxxx"
GEMINI_API_SECRET = ""    # e.g. "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Set to True to use Gemini's sandbox (no real money)
# Sandbox URL: https://exchange.sandbox.gemini.com
# Get sandbox keys from: https://exchange.sandbox.gemini.com/settings/api
PAPER_TRADING = True

# ---------------------------------------------------------------------------
# Polymarket signal settings
# ---------------------------------------------------------------------------

# Minimum single-trade size (USD) on Polymarket to trigger a signal
WHALE_THRESHOLD_USD = 5_000

# How often to poll Polymarket for new trades (seconds)
# Lower = faster signals but more API calls. Recommended: 5–15s
POLL_INTERVAL_SECONDS = 10

# Ignore signals from trades older than this many seconds (prevents acting
# on historical data after a restart)
MAX_SIGNAL_AGE_SECONDS = 30

# ---------------------------------------------------------------------------
# Trade execution settings
# ---------------------------------------------------------------------------

# USD value of BTC to buy/sell per signal
TRADE_SIZE_USD = 100.0

# Maximum total open position (USD). Bot will not open new trades if
# current exposure exceeds this.
MAX_OPEN_POSITION_USD = 500.0

# Auto-close position after N seconds (align with 15-min market = 900s)
# Set to 0 to disable auto-close (manage positions manually)
AUTO_CLOSE_AFTER_SECONDS = 900   # 15 minutes

# Minimum seconds between trades (cooldown). Prevents rapid-fire signals
# from stacking too many positions.
COOLDOWN_SECONDS = 60

# If the position moves against you by this % of trade value, close it
STOP_LOSS_PCT = 0.03     # 3% stop-loss

# If the position is up by this %, take profit and close
TAKE_PROFIT_PCT = 0.05   # 5% take-profit

# ---------------------------------------------------------------------------
# Signal filtering
# ---------------------------------------------------------------------------

# Only act on signals where the whale is betting in the same direction as
# the current market majority (YES > 0.5 for UP, NO > 0.5 for DOWN).
# Reduces contrarian noise.
REQUIRE_MARKET_AGREEMENT = False

# Minimum number of distinct whale trades in the same direction within the
# last CONFIRMATION_WINDOW_SECONDS before executing. Set to 1 to act on
# every signal immediately.
SIGNAL_CONFIRMATION_COUNT = 1

# Window in seconds for counting confirming signals
CONFIRMATION_WINDOW_SECONDS = 120

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = "whale_trader.log"
LOG_LEVEL = "INFO"    # DEBUG | INFO | WARNING | ERROR
