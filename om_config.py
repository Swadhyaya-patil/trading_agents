"""
om_config.py — All configuration constants.
Single source of truth — imported by all three files.
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ── Capital & position sizing
TOTAL_CAPITAL     = float(os.getenv("TOTAL_CAPITAL",     "1000000"))
MAX_TRADES        = int(os.getenv("MAX_TRADES",           "25"))
MAX_RISK_PCT      = float(os.getenv("MAX_RISK_PCT",       "0.03"))
RR_RATIO          = float(os.getenv("RR_RATIO",          "3.0"))
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "0.05"))

# ── Entry filters
ENTRY_OFFSET_PCT = float(os.getenv("ENTRY_OFFSET_PCT", "0.0"))
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE",   "0.85"))
MAX_GAP_PCT      = float(os.getenv("MAX_GAP_PCT",      "2.0"))

# ── Timing
HEARTBEAT_SECS          = int(os.getenv("HEARTBEAT_SECS",          "30"))
TSL_INTERVAL_MINS       = int(os.getenv("TSL_INTERVAL_MINS",        "30"))
RECONCILE_INTERVAL_MINS = int(os.getenv("RECONCILE_INTERVAL_MINS",  "10"))
ORDERBOOK_INTERVAL_MINS = int(os.getenv("ORDERBOOK_INTERVAL_MINS",  "5"))
FAILURE_COOLDOWN_MINS   = int(os.getenv("FAILURE_COOLDOWN_MINS",    "15"))
MAX_CAUTIONARY_RETRIES  = int(os.getenv("MAX_CAUTIONARY_RETRIES",   "2"))
FILL_CONFIRM_SECS       = int(os.getenv("FILL_CONFIRM_SECS",        "10"))
FILL_CONFIRM_RETRIES    = int(os.getenv("FILL_CONFIRM_RETRIES",     "3"))
TOKEN_CACHE_TTL_MINS    = int(os.getenv("TOKEN_CACHE_TTL_MINS",     "240"))

# ── Broker / SL
SL_TRIGGER_PCT   = float(os.getenv("SL_TRIGGER_PCT",  "0.001"))  # 0.1% trigger buffer
DB_PATH          = os.getenv("DB_PATH",       "data/signals.db")
PRODUCT_TYPE     = os.getenv("PRODUCT_TYPE",  "DELIVERY")
DRY_RUN          = os.getenv("DRY_RUN",       "true").lower() == "true"
KILL_SWITCH_FILE = os.getenv("KILL_SWITCH_FILE", "KILL_SWITCH")

# ── NSE holidays (add each year's dates in YYYY-MM-DD format)
# Source: https://www.nseindia.com/resources/exchange-communication-holidays
NSE_HOLIDAYS = set(os.getenv("NSE_HOLIDAYS", (
    "2026-01-26,2026-02-19,2026-03-25,2026-04-14,"
    "2026-04-17,2026-05-01,2026-06-17,2026-08-15,"
    "2026-08-27,2026-10-02,2026-10-20,2026-10-21,"
    "2026-11-04,2026-11-25,2026-12-25"
)).split(","))

# ── Market hours
MARKET_OPEN_TSL   = (9,  15)
MARKET_OPEN_ENTRY = (9,  20)
MARKET_CLOSE_EOD  = (15, 20)
MARKET_EXIT       = (15, 30)