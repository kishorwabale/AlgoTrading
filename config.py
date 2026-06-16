# config.py — All tunable settings for the option volatility algo.
# Edit the values here, then run `python main.py`.
# Nothing else needs to be changed to alter the strategy.

# =============================================================================
# 1. WHEN TO RUN
# =============================================================================
# Time of day (24h "HH:MM", local time) at which the algo executes.
RUN_TIME = "09:18"

# If True  -> main.py sleeps until RUN_TIME, then runs once.
# If False -> main.py runs immediately (handy for testing).
WAIT_FOR_RUN_TIME = True

# =============================================================================
# 2. WHICH SIDE OF THE SCREENER
# =============================================================================
# "gainers" -> only trade top gainers (buy ATM CALLs)
# "losers"  -> only trade top losers  (buy ATM PUTs)
# "both"    -> trade both sides
SIDE = "both"

# =============================================================================
# 3. PERCENT FILTER  (CEILING — maximum move)
# =============================================================================
# Only consider stocks that have moved AT MOST this % (absolute).
# The move must be <= the threshold, NOT greater than it.
#   gainers must have  0 < %chg <=  +MAX_PCT_CHANGE
#   losers  must have  0 > %chg >= -MAX_PCT_CHANGE
# Default 2.0 (set to 5.0 to also allow moves up to 5%).
MAX_PCT_CHANGE = 2.0

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

# Order type. We place MARKET orders as specified.
ORDER_TYPE = "MARKET"

# Tag attached to every order (shows in the Dhan order book).
ORDER_TAG = "vol-algo"

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
