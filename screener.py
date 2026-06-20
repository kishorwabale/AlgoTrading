"""
FNO Screener — uses Dhan's market quote API to fetch live prices
for all NSE FNO stocks, computes % change, shows top 20 gainers
and losers, then applies config filters to pick trading candidates.
"""
import os
import logging
import requests
import pandas as pd
from datetime import datetime

from keys import CLIENT_ID, ACCESS_TOKEN
from config import SIDE, MIN_PCT_CHANGE, MAX_PCT_CHANGE, NUM_GAINERS, NUM_LOSERS, RANK_BY

log = logging.getLogger(__name__)

DHAN_BASE           = "https://api.dhan.co/v2"
SCRIP_MASTER_URL    = "https://images.dhan.co/api-data/api-scrip-master.csv"
SCRIP_CACHE_FILE    = "scrip_master.csv"
SCRIP_MAX_AGE_HOURS = 20


def _headers() -> dict:
    return {
        "access-token": ACCESS_TOKEN,
        "client-id":    CLIENT_ID,
        "Content-Type": "application/json",
    }


# =============================================================================
# Scrip master
# =============================================================================

def load_scrip_master() -> pd.DataFrame:
    """Download Dhan scrip master if missing or older than SCRIP_MAX_AGE_HOURS."""
    refresh = True
    if os.path.exists(SCRIP_CACHE_FILE):
        age_hours = (datetime.now().timestamp() - os.path.getmtime(SCRIP_CACHE_FILE)) / 3600
        if age_hours < SCRIP_MAX_AGE_HOURS:
            refresh = False
    if refresh:
        log.info("Downloading fresh scrip master from Dhan…")
        df = pd.read_csv(SCRIP_MASTER_URL, low_memory=False)
        df.to_csv(SCRIP_CACHE_FILE, index=False)
        log.info(f"Scrip master saved ({len(df):,} rows).")
    else:
        log.info("Using cached scrip master.")
        df = pd.read_csv(SCRIP_CACHE_FILE, low_memory=False)
    return df


def get_fno_equity_map(scrip: pd.DataFrame) -> dict:
    """
    Returns {symbol -> security_id} for all NSE FNO stocks.

    OPTSTK trading symbols look like: CGPOWER-Jun2026-840-PE
    Underlying ticker = part before the first '-' → CGPOWER
    """
    optstk = scrip[
        (scrip["SEM_INSTRUMENT_NAME"] == "OPTSTK") &
        (scrip["SEM_EXM_EXCH_ID"] == "NSE")
    ].copy()

    optstk["underlying"] = optstk["SEM_TRADING_SYMBOL"].str.split("-").str[0]
    fno_symbols = set(optstk["underlying"].dropna())
    log.info(f"  FNO underlying symbols: {len(fno_symbols)}")

    eq = scrip[
        (scrip["SEM_INSTRUMENT_NAME"] == "EQUITY") &
        (scrip["SEM_EXM_EXCH_ID"] == "NSE") &
        (scrip["SEM_SERIES"].isin(["EQ", "BE"])) &
        (scrip["SEM_TRADING_SYMBOL"].isin(fno_symbols))
    ][["SEM_TRADING_SYMBOL", "SEM_SMST_SECURITY_ID"]].drop_duplicates("SEM_TRADING_SYMBOL")

    log.info(f"  Matched equity rows: {len(eq)}")
    return dict(zip(eq["SEM_TRADING_SYMBOL"], eq["SEM_SMST_SECURITY_ID"].astype(str)))


# =============================================================================
# Dhan market quotes
# =============================================================================

def fetch_quotes(security_ids: list) -> dict:
    """Batch-fetch NSE EQ quotes from Dhan in chunks of 500."""
    BATCH = 500
    result = {}
    for i in range(0, len(security_ids), BATCH):
        batch = [int(s) for s in security_ids[i : i + BATCH]]
        resp = requests.post(
            f"{DHAN_BASE}/marketfeed/quote",
            json={"NSE_EQ": batch},
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()

        # Diagnose unexpected response shapes
        top_keys = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
        data_val = raw.get("data") if isinstance(raw, dict) else None
        nse_val  = data_val.get("NSE_EQ") if isinstance(data_val, dict) else data_val
        nse_type = type(nse_val).__name__
        nse_len  = len(nse_val) if nse_val is not None else 0
        log.debug(
            f"  Batch {i//BATCH+1}: HTTP {resp.status_code} | "
            f"top-level keys={top_keys} | data type={type(data_val).__name__} | "
            f"NSE_EQ type={nse_type} len={nse_len}"
        )
        if nse_len == 0:
            log.warning(
                f"  Batch {i//BATCH+1}: NSE_EQ returned 0 records. "
                f"Raw response (first 500 chars): {str(raw)[:500]}"
            )

        data = raw.get("data", {}).get("NSE_EQ", {}) if isinstance(raw.get("data"), dict) else {}
        if isinstance(data, dict):
            result.update({str(k): v for k, v in data.items()})
        elif isinstance(data, list):
            # Handle list-of-dicts shape: [{security_id, ...}, ...]
            for item in data:
                sid = str(item.get("security_id") or item.get("securityId") or "")
                if sid:
                    result[sid] = item

    log.info(f"  Quotes fetched: {len(result)} stocks")
    return result


# =============================================================================
# Display helper
# =============================================================================

def _print_list(title: str, rows: list):
    print(f"\n{'='*65}")
    print(f"  {title}  ({len(rows)} stocks)")
    print(f"{'='*65}")
    print(f"  {'#':<4} {'SYMBOL':<15} {'LTP':>10} {'PREV CLOSE':>12} {'% CHANGE':>10} {'VOLUME':>14}")
    print(f"  {'-'*4} {'-'*15} {'-'*10} {'-'*12} {'-'*10} {'-'*14}")
    for i, r in enumerate(rows, 1):
        print(
            f"  {i:<4} {r['symbol']:<15} {r['ltp']:>10.2f} "
            f"{r['prev_close']:>12.2f} {r['change_pct']:>+10.2f}% "
            f"{r['volume']:>14,}"
        )


# =============================================================================
# Main screener
# =============================================================================

def run_screener() -> tuple[list, list]:
    """
    1. Load scrip master → extract all NSE FNO stock security IDs.
    2. Batch-fetch live quotes from Dhan.
    3. Print top 20 gainers and top 20 losers (full view).
    4. Apply MAX_PCT_CHANGE ceiling filter and pick top N from each side.
    """
    log.info("Running Dhan FNO screener…")
    scrip        = load_scrip_master()
    symbol_to_id = get_fno_equity_map(scrip)

    if not symbol_to_id:
        raise ValueError("No FNO stocks matched in scrip master — check the scrip file.")

    log.info(f"  FNO universe: {len(symbol_to_id)} stocks")

    quotes     = fetch_quotes(list(symbol_to_id.values()))
    id_to_symbol = {v: k for k, v in symbol_to_id.items()}

    records = []
    for sid_str, q in quotes.items():
        ohlc       = q.get("ohlc") or {}
        ltp        = float(q.get("last_price")  or q.get("ltp")       or 0)
        prev_close = float(q.get("close_price") or q.get("prev_close")
                           or ohlc.get("close") or q.get("close")     or 0)
        volume     = int(  q.get("volume")      or q.get("total_quantity_traded") or 0)

        if prev_close > 0:
            change_pct = round((ltp - prev_close) / prev_close * 100, 2)
        else:
            change_pct = float(q.get("change_percentage") or q.get("pChange") or 0)

        records.append({
            "symbol":      id_to_symbol.get(str(sid_str), sid_str),
            "security_id": str(sid_str),
            "ltp":         ltp,
            "prev_close":  prev_close,
            "change_pct":  change_pct,
            "volume":      volume,
        })

    df = pd.DataFrame(records)

    print_top20(df)

    # ── Apply config filters and pick trading candidates ───────────────────────
    # Within the [MIN, MAX] band, rank by proximity to the ceiling (nearest to MAX_PCT_CHANGE wins).
    gainers, losers = [], []

    if SIDE in ("gainers", "both"):
        g = df[(df["change_pct"] >= MIN_PCT_CHANGE) & (df["change_pct"] <= MAX_PCT_CHANGE)].copy()
        g["dist_to_ceiling"] = (g["change_pct"] - MAX_PCT_CHANGE).abs()
        gainers = g.sort_values("dist_to_ceiling").head(NUM_GAINERS).to_dict("records")

    if SIDE in ("losers", "both"):
        lo = df[(df["change_pct"] <= -MIN_PCT_CHANGE) & (df["change_pct"] >= -MAX_PCT_CHANGE)].copy()
        lo["dist_to_ceiling"] = (lo["change_pct"] + MAX_PCT_CHANGE).abs()
        losers = lo.sort_values("dist_to_ceiling").head(NUM_LOSERS).to_dict("records")

    log.info(f"Gainers selected : {[s['symbol'] for s in gainers]}")
    log.info(f"Losers  selected : {[s['symbol'] for s in losers]}")
    return gainers, losers


def print_top20(df: pd.DataFrame) -> None:
    """Print top 20 gainers and top 20 losers from a quotes DataFrame."""
    top20_gainers = (
        df[df["change_pct"] > 0]
        .sort_values("change_pct", ascending=False)
        .head(20)
        .to_dict("records")
    )
    top20_losers = (
        df[df["change_pct"] < 0]
        .sort_values("change_pct", ascending=True)
        .head(20)
        .to_dict("records")
    )

    _print_list("TOP 20 F&O GAINERS", top20_gainers)
    _print_list("TOP 20 F&O LOSERS",  top20_losers)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    run_screener()
