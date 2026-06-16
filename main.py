"""
Options Algo — Main entry point.

Flow (runs once at RUN_TIME each day):
  1. Sleep until RUN_TIME      (skipped when WAIT_FOR_RUN_TIME=False)
  2. Run FNO screener          →  top gainers & losers
  3. Buy ATM CE for gainers, ATM PE for losers  (MARKET orders)
  4. Wait for fills, then start live WebSocket monitor
  5. Monitor exits each leg when P&L hits TARGET or SL

Run : python main.py
Stop: Ctrl+C
"""
import time
import logging
import requests
import pandas as pd
from datetime import date, datetime

from keys import CLIENT_ID, ACCESS_TOKEN
from config import (
    RUN_TIME, WAIT_FOR_RUN_TIME,
    SIDE, MAX_PCT_CHANGE, NUM_GAINERS, NUM_LOSERS, RANK_BY,
    LOTS, EXPIRY_INDEX, PRODUCT_TYPE, ORDER_TYPE, ORDER_TAG,
    DRY_RUN, TARGET_PER_TRADE, SL_PER_TRADE,
)
from screener import run_screener, load_scrip_master
from monitor import PositionMonitor, wait_for_fill, fetch_option_ltp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DHAN_BASE = "https://api.dhan.co/v2"


def _headers() -> dict:
    return {
        "access-token": ACCESS_TOKEN,
        "client-id":    CLIENT_ID,
        "Content-Type": "application/json",
    }


# =============================================================================
# Scrip master cache (loaded once per process)
# =============================================================================

_scrip: pd.DataFrame | None = None


def get_scrip() -> pd.DataFrame:
    global _scrip
    if _scrip is None:
        _scrip = load_scrip_master()
    return _scrip


# =============================================================================
# ATM option contract lookup
# =============================================================================

def find_option_contract(symbol: str, ltp: float, option_type: str) -> dict | None:
    """
    Find the ATM option contract for a stock using EXPIRY_INDEX.

    Args:
        symbol:      NSE equity symbol, e.g. "RELIANCE"
        ltp:         current market price
        option_type: "CE" (gainers) or "PE" (losers)

    Returns dict: {security_id, lot_size, expiry, strike, trading_symbol, option_type}
    Returns None if contract not found.
    """
    scrip = get_scrip()
    today = date.today()

    # Trading symbol format: CGPOWER-Jun2026-840-PE  →  underlying = part before first '-'
    all_opts = scrip[
        (scrip["SEM_INSTRUMENT_NAME"] == "OPTSTK") &
        (scrip["SEM_EXM_EXCH_ID"] == "NSE") &
        (scrip["SEM_OPTION_TYPE"] == option_type)
    ].copy()
    all_opts["underlying"] = all_opts["SEM_TRADING_SYMBOL"].str.split("-").str[0]
    opts = all_opts[all_opts["underlying"] == symbol].copy()

    if opts.empty:
        log.warning(f"No {option_type} options found for {symbol} in scrip master.")
        return None

    opts["expiry_date"] = pd.to_datetime(opts["SEM_EXPIRY_DATE"], errors="coerce").dt.date
    opts = opts[opts["expiry_date"] >= today].copy()

    if opts.empty:
        log.warning(f"No upcoming {option_type} expiries for {symbol}.")
        return None

    expiries = sorted(opts["expiry_date"].unique())

    # Pick expiry by EXPIRY_INDEX (0=nearest, 1=next, …)
    if EXPIRY_INDEX < len(expiries):
        chosen_expiry = expiries[EXPIRY_INDEX]
    else:
        log.warning(
            f"EXPIRY_INDEX={EXPIRY_INDEX} out of range for {symbol} "
            f"(only {len(expiries)} expiries). Using nearest."
        )
        chosen_expiry = expiries[0]

    chain = opts[opts["expiry_date"] == chosen_expiry].copy()
    chain["SEM_STRIKE_PRICE"] = pd.to_numeric(chain["SEM_STRIKE_PRICE"], errors="coerce")
    chain = chain.dropna(subset=["SEM_STRIKE_PRICE"])

    if chain.empty:
        log.warning(f"Empty chain for {symbol} {option_type} expiry {chosen_expiry}.")
        return None

    # ATM = strike closest to LTP
    chain = chain.copy()
    chain["dist"] = (chain["SEM_STRIKE_PRICE"] - ltp).abs()
    atm = chain.loc[chain["dist"].idxmin()]

    return {
        "security_id":    str(int(atm["SEM_SMST_SECURITY_ID"])),
        "lot_size":       int(atm["SEM_LOT_UNITS"]),
        "expiry":         str(chosen_expiry),
        "strike":         float(atm["SEM_STRIKE_PRICE"]),
        "trading_symbol": str(atm["SEM_TRADING_SYMBOL"]),
        "option_type":    option_type,
    }


# =============================================================================
# Order placement
# =============================================================================

def place_order(contract: dict, transaction_type: str = "BUY") -> dict:
    """
    Place a market order on Dhan. Logs only when DRY_RUN=True.
    """
    quantity = contract["lot_size"] * LOTS
    prefix   = "[DRY RUN] " if DRY_RUN else ""

    log.info(
        f"  {prefix}ORDER → {transaction_type} {contract['trading_symbol']} "
        f"| Strike {contract['strike']} {contract['option_type']} "
        f"| Expiry {contract['expiry']} "
        f"| Qty {quantity}  ({LOTS} lot × {contract['lot_size']}) "
        f"| Tag: {ORDER_TAG}"
    )

    if DRY_RUN:
        return {"status": "DRY_RUN", "message": "No real order placed."}

    payload = {
        "dhanClientId":      CLIENT_ID,
        "correlationId":     ORDER_TAG,
        "transactionType":   transaction_type,
        "exchangeSegment":   "NSE_FNO",
        "productType":       PRODUCT_TYPE,
        "orderType":         ORDER_TYPE,
        "validity":          "DAY",
        "tradingSymbol":     contract["trading_symbol"],
        "securityId":        contract["security_id"],
        "quantity":          quantity,
        "price":             0,
        "disclosedQuantity": 0,
        "afterMarketOrder":  False,
        "amoTime":           "OPEN",
    }
    resp = requests.post(f"{DHAN_BASE}/orders", json=payload, headers=_headers(), timeout=15)
    resp.raise_for_status()
    result = resp.json()
    log.info(f"  Order response: {result}")
    return result


# =============================================================================
# Core algo job
# =============================================================================

def _enter_leg(stock: dict, option_type: str, label: str) -> dict | None:
    """
    Find ATM contract, place entry order, return a position dict ready for the monitor.
    Returns None if contract not found or order fails.
    """
    log.info(f"{label} → {stock['symbol']}  LTP={stock['ltp']}  "
             f"Change={stock['change_pct']:+.2f}%  Vol={stock['volume']:,}")

    contract = find_option_contract(stock["symbol"], stock["ltp"], option_type)
    if not contract:
        log.warning(f"  No ATM {option_type} found for {stock['symbol']}. Skipping.")
        return None

    try:
        result = place_order(contract, "BUY")
    except requests.HTTPError as e:
        log.error(f"  Order failed: {e.response.text if e.response else e}")
        return None
    except Exception as e:
        log.error(f"  Order failed: {e}")
        return None

    quantity = contract["lot_size"] * LOTS

    # Get entry price: poll fill for live orders, REST quote for dry-run simulation
    if DRY_RUN:
        entry_price = fetch_option_ltp(contract["security_id"]) or stock["ltp"]
        log.info(f"  [DRY RUN] Simulated entry price: ₹{entry_price:.2f}")
    else:
        order_id    = (result.get("orderId")
                       or result.get("order_id")
                       or result.get("data", {}).get("orderId", ""))
        entry_price = wait_for_fill(order_id) if order_id else None
        if not entry_price:
            log.warning(f"  Could not confirm fill for {contract['trading_symbol']}. "
                        f"Skipping monitor for this leg.")
            return None

    return {
        "security_id":    contract["security_id"],
        "entry_price":    entry_price,
        "quantity":       quantity,
        "trading_symbol": contract["trading_symbol"],
        "option_type":    option_type,
        "symbol":         stock["symbol"],
    }


def run_algo():
    log.info("=" * 65)
    log.info(f"ALGO TRIGGERED  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 65)

    # ── Step 1: Screener ───────────────────────────────────────────────────────
    try:
        gainers, losers = run_screener()
    except Exception as e:
        log.error(f"Screener failed: {e}")
        return

    if not gainers and not losers:
        log.info("No stocks passed the filter. No trades placed.")
        return

    log.info(f"Selected gainers to trade: {[s['symbol'] for s in gainers]}")
    log.info(f"Selected losers to trade : {[s['symbol'] for s in losers]}")

    # ── Step 2: Place entries, collect filled positions ────────────────────────
    positions = []

    for stock in gainers:
        pos = _enter_leg(stock, "CE", "GAINER")
        if pos:
            positions.append(pos)

    for stock in losers:
        pos = _enter_leg(stock, "PE", "LOSER ")
        if pos:
            positions.append(pos)

    if not positions:
        log.info("No positions entered. Nothing to monitor.")
        return

    # ── Step 3: Live WebSocket monitor — exits on TARGET / SL ─────────────────
    log.info(f"{len(positions)} position(s) entered. Starting monitor…")
    log.info(f"  Target ₹{TARGET_PER_TRADE:,.0f}  |  SL ₹{SL_PER_TRADE:,.0f}  per trade")
    PositionMonitor(positions).run()

    log.info("Algo run complete.")


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    mode = "DRY RUN (no real orders)" if DRY_RUN else "LIVE (real Dhan orders)"
    log.info("Options Algo starting up…")
    log.info(f"  Mode         : {mode}")
    log.info(f"  Run time     : {RUN_TIME}  (wait={WAIT_FOR_RUN_TIME})")
    log.info(f"  Side         : {SIDE}")
    log.info(f"  Max % move   : {MAX_PCT_CHANGE}% ceiling")
    log.info(f"  Stocks       : {NUM_GAINERS} gainer(s), {NUM_LOSERS} loser(s)")
    log.info(f"  Rank by      : {RANK_BY}")
    log.info(f"  Lots         : {LOTS}  |  Expiry index: {EXPIRY_INDEX}")
    log.info(f"  Product      : {PRODUCT_TYPE}  |  Order: {ORDER_TYPE}  |  Tag: {ORDER_TAG}")
    log.info(f"  Target       : ₹{TARGET_PER_TRADE:,.0f} per trade")
    log.info(f"  Stop-loss    : ₹{SL_PER_TRADE:,.0f} per trade")

    if WAIT_FOR_RUN_TIME:
        now    = datetime.now()
        run_at = now.replace(
            hour=int(RUN_TIME.split(":")[0]),
            minute=int(RUN_TIME.split(":")[1]),
            second=0, microsecond=0,
        )
        if run_at > now:
            wait_sec = int((run_at - now).total_seconds())
            log.info(f"Sleeping {wait_sec // 60}m {wait_sec % 60}s until {RUN_TIME}…  (Ctrl+C to abort)")
            time.sleep(wait_sec)
        else:
            log.info(f"{RUN_TIME} already passed today — running immediately.")
    else:
        log.info("WAIT_FOR_RUN_TIME=False — running immediately.")

    run_algo()
