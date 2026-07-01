"""
Data sources for the Pre-Market Dashboard.

Each function returns a plain dict so the Flask route can just dump it into
the template. Every fetcher is wrapped in try/except and falls back to None /
cached values so one dead source never takes down the whole dashboard.

WIRING NOTES (read before running live):
- Nifty/BankNifty/Sensex spot + India VIX + PCR: pull from your existing
  Dhan MCP session (same one OC Radar uses). Swap `get_index_snapshot()`
  and `get_pcr()` to call your Dhan client instead of the stubs below.
- FII/DII derivatives stats: NSE's own site (nseindia.com/api/...) requires
  a warmed-up session (cookies from hitting the homepage first) or you'll
  get 401s. `_nse_session()` handles that.
- Gift Nifty: NOT an NSE product (it's traded on NSE IX, Gujarat GIFT City).
  There's no clean public JSON API for it. Two practical options:
    1. Scrape a site that publishes it (moneycontrol/investing.com) - fragile,
       breaks when they change markup.
    2. Enter it manually each morning (takes 5 seconds, zero maintenance).
  Default here is manual entry via a small JSON file (gift_nifty.json) you
  update before market open, since Kishor is already at his desk pre-market.
- US markets / Commodities / Asian markets: yfinance is the path of least
  resistance (no key, decent reliability for index-level data).
"""

import json
import os
from datetime import datetime, timedelta

import requests

CACHE_FILE = os.path.join(os.path.dirname(__file__), "gift_nifty.json")

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def _nse_session(referer=None):
    """
    NSE blocks bare requests; you need cookies from a homepage hit first.
    Some endpoints (equity-stockIndices, option-chain-indices) additionally
    check the Referer header and reject requests that don't look like they
    came from browsing the matching page on nseindia.com - so we optionally
    warm up on that specific page too.
    """
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    s.get("https://www.nseindia.com", timeout=5)
    if referer:
        s.headers.update({"Referer": referer})
        s.get(referer, timeout=5)
    return s


# ---------------------------------------------------------------------------
# Index spot / VIX / PCR  -> replace body with Dhan MCP calls
# ---------------------------------------------------------------------------
def get_index_snapshot(symbol="NIFTY"):
    """
    Returns spot price, % change, day low/high, 1-month trend series.
    TODO: replace with Dhan quote + historical candle call (same pattern as
    OC Radar's option chain fetch, just on the index instead of the chain).
    """
    try:
        s = _nse_session(referer="https://www.nseindia.com/market-data/live-equity-market")
        r = s.get(
            f"https://www.nseindia.com/api/equity-stockIndices?index={'NIFTY%2050' if symbol=='NIFTY' else symbol}",
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()["data"][0]
        return {
            "symbol": symbol,
            "last": data.get("lastPrice"),
            "pct_change": data.get("pChange"),
            "day_low": data.get("dayLow"),
            "day_high": data.get("dayHigh"),
            "ok": True,
        }
    except Exception as e:
        return {"symbol": symbol, "ok": False, "error": str(e)}


def get_india_vix():
    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=5)
        r.raise_for_status()
        for row in r.json().get("data", []):
            if row.get("index") == "INDIA VIX":
                return {
                    "last": row.get("last"),
                    "pct_change": row.get("percentChange"),
                    "ok": True,
                }
        return {"ok": False, "error": "VIX row not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_pcr(symbol="NIFTY"):
    """
    Put-Call ratio by OI. TODO: point this at the same option-chain object
    OC Radar already builds per cycle instead of hitting NSE directly -
    you already compute PCR there, just import/reuse it.
    """
    try:
        s = _nse_session(referer=f"https://www.nseindia.com/option-chain")
        r = s.get(
            f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()["records"]["data"]
        total_ce_oi = sum(d.get("CE", {}).get("openInterest", 0) for d in data if "CE" in d)
        total_pe_oi = sum(d.get("PE", {}).get("openInterest", 0) for d in data if "PE" in d)
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else None
        return {"pcr": pcr, "ok": pcr is not None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# FII derivative positioning - real NSE endpoint
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# FII derivative positioning - real NSE endpoint
# ---------------------------------------------------------------------------
def get_fii_positioning():
    """
    Index Futures Long vs Short % for FIIs — this is the "17% long / 83%
    short" style gauge VRD-style reports show. It is NOT the cash market
    buy/sell figure (that's a different, unrelated number).

    Source: NSE's daily "Participant wise Open Interest" archive CSV —
      https://archives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv
    Published once per day after market close, for that day's session. So
    pre-market, "today's" file doesn't exist yet — we want the most recent
    *available* file, which is yesterday's close (or the last trading day
    if today is a weekend/holiday). We walk back up to 7 calendar days to
    find the latest published file.

    CSV columns of interest for the FII row:
      Future Index Long, Future Index Short  (contracts, index futures only)
    """
    import csv
    import io

    s = _nse_session()

    for days_back in range(0, 8):
        day = datetime.now() - timedelta(days=days_back)
        url = f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{day.strftime('%d%m%Y')}.csv"
        try:
            r = s.get(url, timeout=5)
            if r.status_code != 200 or "Client Type" not in r.text:
                continue  # not published for this date (weekend/holiday/not yet available)

            reader = csv.reader(io.StringIO(r.text))
            rows = list(reader)
            header_idx = next(i for i, row in enumerate(rows) if row and row[0].strip() == "Client Type")
            header = [c.strip() for c in rows[header_idx]]
            long_col = header.index("Future Index Long")
            short_col = header.index("Future Index Short")

            fii_row = next(
                (row for row in rows[header_idx + 1:] if row and row[0].strip() == "FII"),
                None,
            )
            if not fii_row:
                continue

            long_contracts = int(fii_row[long_col])
            short_contracts = int(fii_row[short_col])
            total = long_contracts + short_contracts
            if total == 0:
                continue

            long_pct = round(100 * long_contracts / total, 1)
            short_pct = round(100 - long_pct, 1)
            return {
                "long_pct": long_pct,
                "short_pct": short_pct,
                "long_contracts": long_contracts,
                "short_contracts": short_contracts,
                "net_contracts": long_contracts - short_contracts,
                "as_of": day.strftime("%d-%b-%Y"),
                "ok": True,
            }
        except Exception:
            continue

    return {"ok": False, "error": "No participant-OI file found in last 7 days"}


# ---------------------------------------------------------------------------
# Gift Nifty - manual entry (see docstring at top)
# ---------------------------------------------------------------------------
def get_gift_nifty():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        return {"gap_points": data.get("gap_points"), "updated": data.get("updated"), "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_gift_nifty(gap_points):
    with open(CACHE_FILE, "w") as f:
        json.dump({"gap_points": gap_points, "updated": datetime.now().isoformat()}, f)


# ---------------------------------------------------------------------------
# Global markets - Stooq (no auth needed)
# ---------------------------------------------------------------------------
# NOTE: yfinance was tried first but Yahoo Finance actively rate-limits/blocks
# requests from cloud datacenter IPs (including GitHub Actions runners) with
# 429s and broken cookie/crumb auth - this is a widespread, ongoing issue,
# not something fixable with a version bump. Stooq's plain CSV endpoint has
# no such auth layer and works reliably from CI runners.
STOOQ_TICKERS = {
    "us": {"Dow Jones": "^dji", "S&P 500": "^spx", "Nasdaq": "^ndq"},
    "commodities": {"Gold": "xauusd", "Brent Oil": "brn.f", "USD/INR": "usdinr"},
    "asia": {"Nikkei": "^nkx", "Hang Seng": "^hsi", "Shanghai": "^shc"},
}


def _stooq_pct_change(symbol):
    """Fetch last ~5 sessions of daily closes from Stooq, return latest % change."""
    import csv
    import io

    end = datetime.now()
    start = end - timedelta(days=10)
    url = (
        f"https://stooq.com/q/d/l/?s={symbol}&d1={start.strftime('%Y%m%d')}"
        f"&d2={end.strftime('%Y%m%d')}&i=d"
    )
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = [row for row in reader if row.get("Close")]
    if len(rows) < 2:
        raise ValueError(f"not enough data for {symbol}")
    prev_close = float(rows[-2]["Close"])
    last_close = float(rows[-1]["Close"])
    return round(100 * (last_close - prev_close) / prev_close, 2)


def get_global_markets():
    out = {}
    any_ok = False
    for group, tickers in STOOQ_TICKERS.items():
        out[group] = {}
        for name, symbol in tickers.items():
            try:
                pct = _stooq_pct_change(symbol)
                out[group][name] = {"pct_change": pct, "ok": True}
                any_ok = True
            except Exception as e:
                out[group][name] = {"ok": False, "error": str(e)}
    return {"ok": any_ok, "groups": out}


def build_dashboard_data():
    """Single entry point the Flask route calls."""
    return {
        "generated_at": datetime.now().strftime("%A, %d %b %Y %H:%M"),
        "nifty": get_index_snapshot("NIFTY"),
        "vix": get_india_vix(),
        "pcr": get_pcr("NIFTY"),
        "gift_nifty": get_gift_nifty(),
        "fii": get_fii_positioning(),
        "global": get_global_markets(),
  }
  
