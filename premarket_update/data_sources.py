"""
Data sources for the Pre-Market Dashboard.

Each function returns a plain dict so the Flask route/Telegram script can
just use it. Every fetcher is wrapped in try/except and falls back to
None/error values so one dead source never takes down the whole report.

WIRING NOTES:
- Nifty/BankNifty/Sensex spot + India VIX + PCR go through Dhan's own
  quote/option-chain API instead of scraping NSE's website — same pattern
  oc_radar_bot.py already uses. NSE's option-chain and stock-indices
  endpoints are their most heavily bot-protected pages and block cloud IPs
  (GitHub Actions included) with disguised 404s. Dhan is a paid data feed
  with no such wall.
  Requires DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET env vars — same ones
  already set as repo secrets for oc_radar_bot.yml. A fresh access token is
  generated via TOTP on every run (no manual login, no session to manage).
- FII/DII derivatives stats: NSE's daily "Participant wise Open Interest"
  archive CSV (a different subdomain, archives.nseindia.com, with much
  weaker bot protection than the main site's API). Has held up fine from
  CI so far.
- Gift Nifty: NOT an NSE product (it's traded on NSE IX, Gujarat GIFT
  City). There's no clean public JSON API for it, so it's manual entry via
  gift_nifty.json — update it each morning before market open.
- Global markets (US/commodities/Asia): uses Yahoo's v8/finance/chart
  endpoint directly (same one oc_radar_bot.py's fetch_global_markets()
  uses), not the yfinance library — that endpoint doesn't need the
  cookie/crumb auth that's been breaking yfinance lately. Still
  best-effort; if Yahoo has a bad day, the section is just skipped.
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import struct
import time
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

DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
DHAN_PIN = os.environ.get("DHAN_PIN", "")
DHAN_TOTP_SECRET = os.environ.get("DHAN_TOTP_SECRET", "")

# Dhan IDX_I security IDs (same as oc_radar_bot.py)
DHAN_SEC_IDS = {"NIFTY": 13, "VIX": 21, "BANKNIFTY": 25, "SENSEX": 51}

_dhan_token_cache = {"token": None}


# ---------------------------------------------------------------------------
# Dhan auth — generate a fresh access token via TOTP, same as oc_radar_bot.py
# ---------------------------------------------------------------------------
def _generate_totp_code(secret):
    secret = secret.upper().replace(" ", "")
    padding = len(secret) % 8
    if padding:
        secret += "=" * (8 - padding)
    key = base64.b32decode(secret)
    t = int(time.time()) // 30
    msg = struct.pack(">Q", t)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1000000).zfill(6)


def _get_dhan_token():
    """Cached-for-this-run Dhan access token, generated via TOTP on first call."""
    if _dhan_token_cache["token"]:
        return _dhan_token_cache["token"]

    if not (DHAN_CLIENT_ID and DHAN_PIN and DHAN_TOTP_SECRET):
        raise RuntimeError("DHAN_CLIENT_ID / DHAN_PIN / DHAN_TOTP_SECRET not set")

    last_error = None
    for attempt in range(2):
        try:
            totp = _generate_totp_code(DHAN_TOTP_SECRET)
            url = (
                "https://auth.dhan.co/app/generateAccessToken"
                f"?dhanClientId={DHAN_CLIENT_ID}&pin={DHAN_PIN}&totp={totp}"
            )
            r = requests.post(url, timeout=25)
            data = r.json()
            token = data.get("accessToken", "")
            if not token:
                raise RuntimeError(f"Dhan token generation failed: {data.get('remarks', data)}")
            _dhan_token_cache["token"] = token
            return token
        except Exception as e:
            last_error = e
            if attempt == 0:
                time.sleep(31)  # TOTP window is 30s — wait for a fresh code before retrying
    raise last_error


def _dhan_quote(payload):
    token = _get_dhan_token()
    url = "https://api.dhan.co/v2/marketfeed/quote"
    headers = {
        "access-token": token,
        "client-id": DHAN_CLIENT_ID,
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def _dhan_post(url, payload):
    token = _get_dhan_token()
    headers = {
        "access-token": token,
        "client-id": DHAN_CLIENT_ID,
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def _nse_session(referer=None):
    """NSE blocks bare requests; you need cookies from a homepage hit first."""
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    s.get("https://www.nseindia.com", timeout=5)
    if referer:
        s.headers.update({"Referer": referer})
        s.get(referer, timeout=5)
    return s


_quote_cache = {}


def _get_nse_indices():
    """
    NSE's allIndices endpoint already proved reliable from CI (this is how
    VIX worked even before the Dhan swap) and gives a correct percentChange
    field directly for every listed index, Nifty 50 included. Dhan's
    ohlc.close field on IDX_I instruments appears to just mirror last_price
    rather than holding a genuine previous-day close (both Nifty and VIX
    showed exactly 0.0% simultaneously, which pointed at the field itself
    rather than an actual flat market) - so spot/VIX % change comes from
    here instead, while Dhan is still used for PCR where it's needed.
    """
    if _quote_cache:
        return _quote_cache
    s = _nse_session()
    r = s.get("https://www.nseindia.com/api/allIndices", timeout=5)
    r.raise_for_status()
    for row in r.json().get("data", []):
        _quote_cache[row.get("index", "")] = row
    return _quote_cache


# ---------------------------------------------------------------------------
# Index spot — NSE allIndices (day_low/day_high still from Dhan quote)
# ---------------------------------------------------------------------------
def get_index_snapshot(symbol="NIFTY"):
    nse_name = "NIFTY 50" if symbol == "NIFTY" else symbol
    try:
        row = _get_nse_indices().get(nse_name, {})
        if not row:
            return {"symbol": symbol, "ok": False, "error": f"'{nse_name}' not found in allIndices"}

        last = float(row.get("last", 0))
        pct_change = float(row.get("percentChange", 0))

        # day low/high: try Dhan's quote for these (separate from the
        # change calc, which NSE already gives us correctly)
        day_low, day_high = last, last
        try:
            sec_id = DHAN_SEC_IDS[symbol]
            data = _dhan_quote({"IDX_I": [sec_id]})
            d = data.get("data", {}).get("IDX_I", {}).get(str(sec_id), {})
            ohlc = d.get("ohlc", {}) or {}
            day_low = float(ohlc.get("low") or last)
            day_high = float(ohlc.get("high") or last)
        except Exception:
            pass  # non-critical, fall back to last price for both

        return {
            "symbol": symbol,
            "last": last,
            "pct_change": pct_change,
            "day_low": day_low,
            "day_high": day_high,
            "ok": True,
        }
    except Exception as e:
        return {"symbol": symbol, "ok": False, "error": str(e)}


def get_india_vix():
    try:
        row = _get_nse_indices().get("INDIA VIX", {})
        if not row:
            return {"ok": False, "error": "'INDIA VIX' not found in allIndices"}
        return {
            "last": float(row.get("last", 0)),
            "pct_change": float(row.get("percentChange", 0)),
            "ok": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _get_next_expiry(scrip, seg="IDX_I"):
    """Nearest upcoming expiry for the given underlying, via Dhan's expirylist."""
    data = _dhan_post(
        "https://api.dhan.co/v2/optionchain/expirylist",
        {"UnderlyingScrip": scrip, "UnderlyingSeg": seg},
    )
    expiries = data.get("data", [])
    today = datetime.now().strftime("%Y-%m-%d")
    upcoming = sorted(e for e in expiries if e >= today)
    if not upcoming:
        raise ValueError("no upcoming expiry returned by Dhan")
    return upcoming[0]


def get_pcr(symbol="NIFTY"):
    """
    Put-Call ratio by total OI, via Dhan's option-chain API (same one
    oc_radar_bot.py already uses for its signal engine).
    """
    try:
        time.sleep(1)  # small buffer after the quote call, avoid stacking rate limit
        scrip = DHAN_SEC_IDS[symbol]
        expiry = _get_next_expiry(scrip)
        data = _dhan_post(
            "https://api.dhan.co/v2/optionchain",
            {"UnderlyingScrip": scrip, "UnderlyingSeg": "IDX_I", "Expiry": expiry},
        )
        chains = data.get("data", {}).get("oc", {})
        if not chains:
            return {"ok": False, "error": "no option chain data returned"}

        total_ce_oi = 0
        total_pe_oi = 0
        for strike_row in chains.values():
            ce = strike_row.get("ce", {}) or {}
            pe = strike_row.get("pe", {}) or {}
            total_ce_oi += ce.get("oi", 0) or 0
            total_pe_oi += pe.get("oi", 0) or 0

        if not total_ce_oi:
            return {"ok": False, "error": "zero call OI, can't compute PCR"}
        pcr = round(total_pe_oi / total_ce_oi, 2)
        return {"pcr": pcr, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# FII derivative positioning - NSE archive CSV
# ---------------------------------------------------------------------------
def get_fii_positioning():
    """
    Index Futures Long vs Short % for FIIs — the "17% long / 83% short"
    style gauge VRD-style reports show.

    Source: NSE's daily "Participant wise Open Interest" archive CSV —
      https://archives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv
    Published once per day after market close. Pre-market, "today's" file
    doesn't exist yet, so we want the most recent *available* file — walk
    back up to 7 calendar days to find it.
    """
    s = _nse_session()

    for days_back in range(0, 8):
        day = datetime.now() - timedelta(days=days_back)
        url = f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{day.strftime('%d%m%Y')}.csv"
        try:
            r = s.get(url, timeout=5)
            if r.status_code != 200 or "Client Type" not in r.text:
                continue

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
# Gift Nifty - NSE's marketStatus endpoint (undocumented but real), with
# manual-entry fallback since Gift Nifty has no official public API and
# this endpoint's reliability from CI is untested
# ---------------------------------------------------------------------------
def _fetch_gift_nifty_from_nse():
    """
    NSE's own marketStatus endpoint carries a 'giftnifty' object alongside
    market state - not documented, but it's first-party NSE data, not a
    third-party scrape. Untested from CI so far; may hit the same
    bot-protection wall as other main-site endpoints.
    """
    s = _nse_session()
    r = s.get("https://www.nseindia.com/api/marketStatus", timeout=5)
    r.raise_for_status()
    gn = r.json().get("giftnifty", {})
    if not gn or "PERCHANGE" not in gn:
        raise ValueError("giftnifty field missing from marketStatus response")
    return {
        "gap_points": gn.get("DAYCHANGE"),
        "last_price": gn.get("LASTPRICE"),
        "pct_change": gn.get("PERCHANGE"),
        "expiry": gn.get("EXPIRYDATE"),
        "updated": datetime.now().isoformat(),
        "source": "nse",
        "ok": True,
    }


def get_gift_nifty():
    # Try NSE first — falls through to manual entry if blocked/unavailable
    try:
        return _fetch_gift_nifty_from_nse()
    except Exception:
        pass

    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        return {
            "gap_points": data.get("gap_points"),
            "updated": data.get("updated"),
            "source": "manual",
            "ok": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_gift_nifty(gap_points):
    with open(CACHE_FILE, "w") as f:
        json.dump({"gap_points": gap_points, "updated": datetime.now().isoformat()}, f)


# ---------------------------------------------------------------------------
# Global markets - Yahoo v8/finance/chart (same endpoint oc_radar_bot.py uses)
# ---------------------------------------------------------------------------
YAHOO_TICKERS = {
    "us": {"Dow Jones": "^DJI", "S&P 500": "^GSPC", "Nasdaq": "^IXIC"},
    "commodities": {"Gold": "GC=F", "Brent Oil": "BZ=F", "USD/INR": "USDINR=X"},
    "asia": {"Nikkei": "^N225", "Hang Seng": "^HSI", "Shanghai": "000001.SS"},
}


def _yahoo_pct_change(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta["previousClose"]
    return round(100 * (price - prev) / prev, 2) if prev else 0


def get_global_markets():
    out = {}
    any_ok = False
    for group, tickers in YAHOO_TICKERS.items():
        out[group] = {}
        for name, symbol in tickers.items():
            try:
                pct = _yahoo_pct_change(symbol)
                out[group][name] = {"pct_change": pct, "ok": True}
                any_ok = True
            except Exception as e:
                out[group][name] = {"ok": False, "error": str(e)}
    return {"ok": any_ok, "groups": out}


# ---------------------------------------------------------------------------
# Nifty futures pre-open (9:00-9:15 auction) — implied gap vs prev close
# ---------------------------------------------------------------------------
# UNVERIFIED as of first build: whether Dhan's REST quote endpoint reflects
# the live-updating indicative price during the 9:00-9:12 auction window,
# or only settles once it concludes. Needs a live check during actual
# market hours before trusting this as a real-time pre-open signal.
_futures_sec_id_cache = {"id": None}


def _get_nifty_futures_security_id():
    """
    Downloads Dhan's compact instrument master CSV and finds the current
    front-month NIFTY index futures contract's security ID (this changes
    every monthly expiry, so it can't be hardcoded). Column names are
    best-effort based on Dhan's documented CSV format; if nothing matches,
    the error includes the actual column names seen so it's fixable fast.
    """
    if _futures_sec_id_cache["id"]:
        return _futures_sec_id_cache["id"]

    url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    if not rows:
        raise ValueError("instrument master CSV came back empty")

    today = datetime.now().strftime("%Y-%m-%d")
    candidates = []
    for row in rows:
        instrument = (row.get("SEM_INSTRUMENT_NAME") or row.get("INSTRUMENT") or "").upper()
        symbol = (
            row.get("SEM_TRADING_SYMBOL")
            or row.get("SEM_CUSTOM_SYMBOL")
            or row.get("SYMBOL_NAME")
            or ""
        ).upper()
        expiry = row.get("SEM_EXPIRY_DATE") or row.get("EXPIRY_DATE") or ""
        sec_id = row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID")
        if (
            instrument == "FUTIDX"
            and "NIFTY" in symbol
            and "BANK" not in symbol
            and "FIN" not in symbol
            and expiry >= today
            and sec_id
        ):
            candidates.append((expiry, sec_id))

    if not candidates:
        raise ValueError(f"no NIFTY FUTIDX row matched. CSV columns were: {list(rows[0].keys())}")

    candidates.sort()
    _futures_sec_id_cache["id"] = candidates[0][1]
    return candidates[0][1]


def get_futures_preopen():
    """
    Nifty near-month futures price, meant to be checked during/after the
    9:00-9:15 pre-open auction. Compares against NIFTY 50's previous close
    (from NSE allIndices) to get an implied gap — same idea as Gift Nifty,
    but from the domestic pre-open auction rather than the overnight NSE IX
    contract.
    """
    try:
        sec_id = _get_nifty_futures_security_id()
        token = _get_dhan_token()
        headers = {
            "access-token": token,
            "client-id": DHAN_CLIENT_ID,
            "Content-Type": "application/json",
        }
        r = requests.post(
            "https://api.dhan.co/v2/marketfeed/quote",
            headers=headers,
            json={"NSE_FNO": [int(sec_id)]},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json().get("data", {}).get("NSE_FNO", {}).get(str(sec_id), {})
        if not d:
            return {"ok": False, "error": "empty response from Dhan for futures contract"}
        fut_price = float(d.get("last_price", 0))
        if not fut_price:
            return {"ok": False, "error": "futures last_price came back as 0"}

        nse_row = _get_nse_indices().get("NIFTY 50", {})
        prev_close = float(nse_row.get("previousClose", 0) or 0)
        if not prev_close:
            return {"ok": False, "error": "couldn't get NIFTY previous close for gap calc"}

        gap = round(fut_price - prev_close, 2)
        return {
            "futures_price": fut_price,
            "prev_close": prev_close,
            "gap_points": gap,
            "security_id": sec_id,
            "ok": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def build_dashboard_data():
    """Single entry point the Flask route / Telegram script calls."""
    return {
        "generated_at": datetime.now().strftime("%A, %d %b %Y %H:%M"),
        "nifty": get_index_snapshot("NIFTY"),
        "vix": get_india_vix(),
        "pcr": get_pcr("NIFTY"),
   
