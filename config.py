# config.py — All tunable settings for the option volatility algo.
# Edit the values here, then run `python main.py`.
# Nothing else needs to be changed to alter the strategy.

# =============================================================================
# 1. WHEN TO RUN
# =============================================================================
# Time of day (24h "HH:MM", local time) at which the algo executes.
RUN_TIME = "09:20"

# If True  -> main.py sleeps until RUN_TIME, then runs once.
#             If started AFTER RUN_TIME the script will exit without trading.
# If False -> main.py runs immediately (handy for testing).
WAIT_FOR_RUN_TIME = True

# Hard exit time for open positions (HH:MM, 24h local).
# The monitor will force-sell all remaining legs at this time so INTRADAY
# positions are not left to the broker's auto-square-off at 3:20.
EOD_EXIT_TIME = "15:15"

# =============================================================================
# 2. WHICH SIDE OF THE SCREENER
# =============================================================================
# "gainers" -> only trade top gainers (buy ATM CALLs)
# "losers"  -> only trade top losers  (buy ATM PUTs)
# "both"    -> trade both sides
SIDE = "both"

# =============================================================================
# 3. PERCENT FILTER  (floor → ceiling band)
# =============================================================================
# Only consider stocks whose absolute % move falls inside [MIN, MAX].
#   gainers:  MIN_PCT_CHANGE <= %chg  <= MAX_PCT_CHANGE
#   losers :  MIN_PCT_CHANGE <= |%chg| <= MAX_PCT_CHANGE
MIN_PCT_CHANGE = 1.5   # floor  — ignore moves smaller than this (noise)
MAX_PCT_CHANGE = 2.0   # ceiling — ignore moves larger than this (already extended)

# =============================================================================
# 4. HOW MANY STOCKS TO TRADE
# =============================================================================
# How many qualifying stocks to pick from each side.
#   SIDE="gainers" -> uses NUM_GAINERS only
#   SIDE="losers"  -> uses NUM_LOSERS  only
#   SIDE="both"    -> uses both
NUM_GAINERS = 1
NUM_LOSERS  = 1

# =============================================================================
# 5. RANKING  (when more stocks pass the filter than we need)
# =============================================================================
# "pct"    -> keep the biggest movers
# "volume" -> keep the most actively traded
RANK_BY = "pct"

# =============================================================================
# 6. OPTION ORDER SETTINGS
# =============================================================================
# Number of lots per trade (actual qty = lot_size_of_stock * LOTS).
LOTS = 1

# Which expiry to trade: 0 = nearest, 1 = next, 2 = one after, …
EXPIRY_INDEX = 0

# Product type for the option order:
#   "INTRADAY" -> MIS (square-off same day)
#   "MARGIN"   -> NRML / carryforward
PRODUCT_TYPE = "INTRADAY"

# Order type for entry (exits always use MARKET for speed).
#   "LIMIT"  -> recommended; price = option LTP × (1 + LIMIT_PRICE_BUFFER_PCT/100)
#   "MARKET" -> immediate fill but risks wide spread slippage at open
ORDER_TYPE = "LIMIT"

# How far above the current option LTP to place the buy limit (%).
# 2 % almost always ensures a fill while capping worst-case slippage.
LIMIT_PRICE_BUFFER_PCT = 2.0

# Tag attached to every order (shows in the Dhan order book).
ORDER_TAG = "vol-algo"

# Minimum open interest required on the ATM option contract.
# Contracts below this threshold are skipped as illiquid.
MIN_OPTION_OI = 500

# =============================================================================
# 7. SAFETY SWITCH
# =============================================================================
# True  -> DRY RUN: print exactly what would be bought, place NOTHING.
# False -> LIVE: place real market orders on your Dhan account.
DRY_RUN = False

# =============================================================================
# 8. TARGET & STOP-LOSS  (per trade, in ₹)
# =============================================================================
# P&L is tracked per option position:
#   P&L = (current_option_LTP - entry_fill_price) × quantity
#
# TARGET_PER_TRADE : exit the position as soon as profit reaches this amount.
# SL_PER_TRADE     : exit the position as soon as loss  reaches this amount.
#
# Example: TARGET=2000, SL=2000
#   → Square off when you are up ₹2,000 OR down ₹2,000 on that leg.
TARGET_PER_TRADE = 2000   # ₹ profit at which to take the target exit
SL_PER_TRADE     = 2000   # ₹ loss  at which to cut the stoploss exit
