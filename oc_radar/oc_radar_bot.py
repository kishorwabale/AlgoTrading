# ══════════════════════════════════════════════════════════════
# OC RADAR TELEGRAM BOT v7
# FULLY AUTOMATIC TOKEN GENERATION
# Bot uses TOTP Secret to generate token itself
# Zero manual work every morning! 🎉
# SECURE: All secrets loaded from ~/.env file
#         Never hardcoded in script!
# AUTO-LOGGING: Signals + Trades → Google Sheets
# ══════════════════════════════════════════════════════════════

import requests
import time
import hmac
import hashlib
import struct
import base64
import os
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, time as dtime, timedelta
import pytz

# ── LOAD SECRETS FROM ~/.env FILE ────────────────────────────
def load_env(filepath="~/.env"):
    """Load environment variables from ~/.env file"""
    filepath = os.path.expanduser(filepath)
    if not os.path.exists(filepath):
        print(f"⚠️ .env file not found at {filepath}")
        print("Create it with: nano ~/.env")
        return False
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()
    return True

# Load secrets
load_env()

# ── CONFIGURATION — loaded from environment ───────────────────
TELEGRAM_TOKEN        = os.getenv("TELEGRAM_TOKEN",            "")
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID",          "")
DHAN_CLIENT_ID        = os.getenv("DHAN_CLIENT_ID",            "1111888014")
DHAN_API_KEY          = ""  # Auto-filled every morning!
DHAN_PIN              = os.getenv("DHAN_PIN",                  "")
DHAN_TOTP_SECRET      = os.getenv("DHAN_TOTP_SECRET",          "")
GOOGLE_SHEET_ID       = os.getenv("GOOGLE_SHEET_ID",           "")
GOOGLE_CREDENTIALS    = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "")

# ══════════════════════════════════════════════════════════════
# GOOGLE SHEETS AUTO-LOGGING
# ══════════════════════════════════════════════════════════════
_gsheet_client  = None
_signal_sheet   = None
_trade_sheet    = None
_pnl_sheet      = None

def init_google_sheets():
    """Initialize Google Sheets connection"""
    global _gsheet_client, _signal_sheet, _trade_sheet, _pnl_sheet

    if not GOOGLE_SHEET_ID or not GOOGLE_CREDENTIALS:
        print("⚠️ Google Sheets not configured — skipping auto-logging")
        return False

    try:
        # Parse credentials from env
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds          = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _gsheet_client = gspread.authorize(creds)
        wb             = _gsheet_client.open_by_key(GOOGLE_SHEET_ID)

        # Get sheets by name — create if missing
        sheet_names = [s.title for s in wb.worksheets()]

        def get_or_create(name, headers):
            if name not in sheet_names:
                ws = wb.add_worksheet(title=name, rows=500, cols=len(headers))
                ws.append_row(headers)
                return ws
            return wb.worksheet(name)

        _signal_sheet = get_or_create("⏱ Signal Log", [
            "Date", "Ping Time IST", "Response Time IST", "Index",
            "Signal (CE/PE)", "Strike", "Score", "OICR (%)", "PCR",
            "OI Pattern", "VIX", "Futures Basis", "Composite Score",
            "Action", "Window", "Notes"
        ])

        _trade_sheet = get_or_create("📓 Trade Journal", [
            "Date", "Day", "Index", "CE/PE", "Strike",
            "Entry Time", "Entry Price", "Lots", "Capital Used",
            "Exit Time", "Exit Price", "P&L (₹)", "P&L (%)",
            "Hold Time (min)", "Score", "OICR", "Pattern",
            "Result", "Order ID", "Notes"
        ])

        _pnl_sheet = get_or_create("💰 Daily P&L", [
            "Date", "Day", "Signals Received", "Trades Taken",
            "Winners", "Losers", "Win Rate (%)", "Gross P&L (₹)",
            "Brokerage (₹)", "Net P&L (₹)", "Notes"
        ])

        print("✅ Google Sheets connected!")
        return True

    except Exception as e:
        print(f"⚠️ Google Sheets init error: {e}")
        return False

def log_signal_to_sheet(name, sig, ping_time, resp_time, window, pcr_trends):
    """Auto-log every signal to Google Sheets Signal Log"""
    global _signal_sheet
    if not _signal_sheet:
        return

    try:
        now     = datetime.now(IST)
        date    = now.strftime("%d-%b-%Y")
        pcr_trend = pcr_trends.get(name, ("", ""))[0] if pcr_trends else ""

        row = [
            date,
            ping_time,
            resp_time,
            name,
            sig.get("action", ""),
            sig.get("ce_strike", "") if "CE" in sig.get("action","") else sig.get("pe_strike",""),
            max(sig.get("ce_score", 0), sig.get("pe_score", 0)),
            sig.get("oicr", ""),
            sig.get("pcr", ""),
            "",  # OI Pattern — from buildups
            "",  # VIX
            "",  # Futures Basis
            "",  # Composite
            sig.get("action", ""),
            window,
            sig.get("mkt", ""),
        ]
        _signal_sheet.append_row(row)
        print(f"  📊 Signal logged to Sheets: {name} {sig.get('action','')}")

    except Exception as e:
        print(f"  ⚠️ Signal sheet log error: {e}")

def log_trade_to_sheet(name, side, strike, entry_price, lots,
                        exit_price, entry_time, exit_time,
                        score, oicr, pattern, order_id=""):
    """Auto-log trade to Google Sheets Trade Journal"""
    global _trade_sheet
    if not _trade_sheet:
        return

    try:
        now      = datetime.now(IST)
        date     = now.strftime("%d-%b-%Y")
        day      = now.strftime("%a")
        lot_size = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}.get(name, 30)
        capital  = round(entry_price * lots * lot_size)
        pnl_rs   = round((exit_price - entry_price) * lots * lot_size, 2)
        pnl_pct  = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0

        # Hold time in minutes
        try:
            entry_dt = datetime.strptime(entry_time, "%I:%M:%S %p")
            exit_dt  = datetime.strptime(exit_time, "%I:%M:%S %p")
            hold_min = round((exit_dt - entry_dt).seconds / 60, 1)
        except:
            hold_min = ""

        result = "WIN ✅" if pnl_rs > 0 else "LOSS ❌"

        row = [
            date, day, name, side, strike,
            entry_time, entry_price, lots, capital,
            exit_time, exit_price, pnl_rs, f"{pnl_pct}%",
            hold_min, score, oicr, pattern,
            result, order_id, ""
        ]
        _trade_sheet.append_row(row)
        print(f"  📊 Trade logged to Sheets: {name} {side} {result}")

    except Exception as e:
        print(f"  ⚠️ Trade sheet log error: {e}")

def log_eod_pnl_to_sheet(results, daily_signals, daily_trades):
    """Auto-log daily P&L summary to Google Sheets"""
    global _pnl_sheet
    if not _pnl_sheet:
        return

    try:
        now     = datetime.now(IST)
        date    = now.strftime("%d-%b-%Y")
        day     = now.strftime("%a")

        # Calculate totals
        winners   = sum(1 for t in daily_trades if t.get("pnl", 0) > 0)
        losers    = sum(1 for t in daily_trades if t.get("pnl", 0) <= 0)
        trades    = len(daily_trades)
        win_rate  = round(winners / trades * 100, 1) if trades else 0
        gross_pnl = sum(t.get("pnl", 0) for t in daily_trades)
        brokerage = trades * 70  # ₹70 per trade
        net_pnl   = gross_pnl - brokerage

        row = [
            date, day, daily_signals, trades,
            winners, losers, f"{win_rate}%",
            gross_pnl, brokerage, net_pnl, ""
        ]
        _pnl_sheet.append_row(row)
        print(f"  📊 EOD P&L logged to Sheets: Net ₹{net_pnl}")

    except Exception as e:
        print(f"  ⚠️ P&L sheet log error: {e}")

def auto_import_trades_from_dhan():
    """
    Fetch today's executed trades from Dhan API
    Auto-log to Google Sheets Trade Journal
    """
    global _trade_sheet
    if not _trade_sheet or not DHAN_API_KEY:
        return []

    try:
        url = "https://api.dhan.co/v2/trades"
        headers = {
            "access-token": DHAN_API_KEY,
            "client-id": DHAN_CLIENT_ID,
        }
        r     = requests.get(url, headers=headers, timeout=10)
        data  = r.json()
        trades = data.get("data", [])

        imported = []
        for t in trades:
            # Only process F&O trades
            seg = t.get("exchangeSegment", "")
            if "FNO" not in seg:
                continue

            symbol    = t.get("tradingSymbol", "")
            side      = t.get("transactionType", "")
            qty       = t.get("tradedQuantity", 0)
            price     = t.get("tradedPrice", 0)
            order_id  = t.get("orderId", "")
            trade_time= t.get("updateTime", "")

            imported.append({
                "symbol":   symbol,
                "side":     side,
                "qty":      qty,
                "price":    price,
                "order_id": order_id,
                "time":     trade_time,
            })

        if imported:
            print(f"  📥 Imported {len(imported)} trades from Dhan")
            send_telegram(
                f"📥 *{len(imported)} trades auto-imported from Dhan*\n"
                f"Check Google Sheets Trade Journal ✅"
            )

        return imported

    except Exception as e:
        print(f"  ⚠️ Dhan trade import error: {e}")
        return []

# ── VALIDATE SECRETS ─────────────────────────────────────────
def validate_secrets():
    """Check all required secrets are loaded"""
    missing = []
    if not TELEGRAM_TOKEN:   missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    if not DHAN_PIN:         missing.append("DHAN_PIN")
    if not DHAN_TOTP_SECRET: missing.append("DHAN_TOTP_SECRET")
    if missing:
        print(f"❌ Missing secrets in ~/.env: {', '.join(missing)}")
        return False
    # Google Sheets optional warning
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDENTIALS:
        print("⚠️ Google Sheets not configured — signals won't auto-log")
    print("✅ All secrets loaded from ~/.env")
    return True

REFRESH_MINUTES        = 5
IST                    = pytz.timezone("Asia/Kolkata")
MARKET_OPEN            = dtime(9, 15)
MARKET_CLOSE           = dtime(15, 30)
SCORE_CHANGE_THRESHOLD = 5
SPOT_CHANGE_THRESHOLD  = 0.3

# ── TOKEN STATE ───────────────────────────────────────────────
token_generated_today = False
waiting_for_totp      = False
totp_code_received    = None

# ══════════════════════════════════════════════════════════════
# TOTP CODE GENERATOR (RFC 6238)
# Bot generates 6-digit code itself from TOTP Secret!
# ══════════════════════════════════════════════════════════════
def generate_totp_code(secret=None):
    """
    Generate 6-digit TOTP code from secret key
    Same algorithm as Google Authenticator
    Works without any external library!
    """
    if not secret:
        secret = DHAN_TOTP_SECRET

    try:
        # Decode base32 secret
        secret = secret.upper().replace(" ", "")
        # Add padding if needed
        padding = len(secret) % 8
        if padding:
            secret += "=" * (8 - padding)
        key = base64.b32decode(secret)

        # Get current time step (30 second intervals)
        t = int(time.time()) // 30

        # HMAC-SHA1
        msg     = struct.pack(">Q", t)
        h       = hmac.new(key, msg, hashlib.sha1).digest()
        offset  = h[-1] & 0x0F
        code    = struct.unpack(">I", h[offset:offset+4])[0] & 0x7FFFFFFF
        totp    = str(code % 1000000).zfill(6)
        return totp
    except Exception as e:
        print(f"TOTP generation error: {e}")
        return None

def auto_generate_token():
    """
    Fully automatic token generation
    Bot generates TOTP + calls Dhan API
    Zero manual work!
    """
    global DHAN_API_KEY, token_generated_today

    print("🔄 Auto-generating Dhan token...")
    totp = generate_totp_code()

    if not totp:
        send_telegram(
            "❌ *TOTP generation failed!*\n"
            "Check DHAN_TOTP_SECRET in bot config\n"
            "Send /totp to enter code manually"
        )
        return False

    print(f"🔑 Generated TOTP: {totp}")
    success = generate_token_with_totp(totp)

    if not success:
        # Try once more with next time window
        time.sleep(31)
        totp = generate_totp_code()
        success = generate_token_with_totp(totp)

    return success

def request_totp_from_user():
    """Fallback: Send Telegram message asking for TOTP code"""
    global waiting_for_totp
    waiting_for_totp = True
    msg = (
        "🔑 *Auto TOTP failed — Manual needed*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ Open *Google Authenticator*\n"
        "2️⃣ Find *DHAN* entry\n"
        "3️⃣ Send the *6-digit code* here\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏰ Code changes every 30 sec\n"
        "Send quickly!"
    )
    send_telegram(msg)
    print("⏳ Waiting for manual TOTP...")

def generate_token_with_totp(totp_code):
    """Generate Dhan access token using PIN + TOTP"""
    global DHAN_API_KEY, token_generated_today, waiting_for_totp

    url = (
        f"https://auth.dhan.co/app/generateAccessToken"
        f"?dhanClientId={DHAN_CLIENT_ID}"
        f"&pin={DHAN_PIN}"
        f"&totp={totp_code}"
    )
    try:
        r     = requests.post(url, timeout=10)
        data  = r.json()
        token = data.get("accessToken", "")
        if token:
            DHAN_API_KEY          = token
            token_generated_today = True
            waiting_for_totp      = False
            # Format expiry date nicely
            expiry_raw = data.get("expiryTime", "")
            try:
                # Convert "2026-06-28T01:46:54.423" to "28th Jun 2026 01:46 AM"
                exp_dt  = datetime.strptime(expiry_raw[:19], "%Y-%m-%dT%H:%M:%S")
                day     = exp_dt.day
                suffix  = ("st" if day in [1,21,31] else
                           "nd" if day in [2,22] else
                           "rd" if day in [3,23] else "th")
                months  = ["Jan","Feb","Mar","Apr","May","Jun",
                           "Jul","Aug","Sep","Oct","Nov","Dec"]
                h       = exp_dt.hour
                hh      = str(h%12 or 12).zfill(2)
                ampm    = "AM" if h < 12 else "PM"
                expiry  = f"{day}{suffix} {months[exp_dt.month-1]} {exp_dt.year} {hh}:{str(exp_dt.minute).zfill(2)} {ampm} IST"
            except:
                expiry = expiry_raw or "24 hours"
            send_telegram(
                f"✅ *Token auto-generated!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Valid till: {expiry}\n"
                f"Bot running automatically 🤖\n"
                f"First signal at 9:15 AM 📡"
            )
            print(f"✅ Token generated! Valid till {expiry}")
            return True
        else:
            err = data.get("message", data.get("remarks", "Unknown error"))
            send_telegram(
                f"❌ *Token generation failed!*\n"
                f"Error: {err}\n"
                f"Please try again — send new TOTP code"
            )
            waiting_for_totp = False
            print(f"❌ Token failed: {err}")
            return False
    except Exception as e:
        send_telegram(f"❌ *Error generating token:* {e}")
        waiting_for_totp = False
        return False

def renew_token():
    """Renew existing token for 24 more hours"""
    global DHAN_API_KEY, token_generated_today
    if not DHAN_API_KEY:
        return False
    try:
        url     = "https://api.dhan.co/v2/RenewToken"
        headers = {
            "access-token": DHAN_API_KEY,
            "dhanClientId": DHAN_CLIENT_ID,
        }
        r    = requests.post(url, headers=headers, timeout=10)
        data = r.json()
        new_token = data.get("accessToken", "")
        if new_token:
            DHAN_API_KEY          = new_token
            token_generated_today = True
            print("✅ Token renewed for 24 hours!")
            return True
        return False
    except Exception as e:
        print(f"❌ Token renewal error: {e}")
        return False

# ── NSE HOLIDAYS 2026 ─────────────────────────────────────────
NSE_HOLIDAYS_2026 = {
    "2026-01-15", "2026-02-19", "2026-03-19", "2026-04-01",
    "2026-04-02", "2026-04-14", "2026-04-15", "2026-05-01",
    "2026-06-26", "2026-08-15", "2026-09-14", "2026-10-02",
    "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
}

# ── STATE STORES ──────────────────────────────────────────────
prev_oi         = {}  # OI from last refresh for change detection
prev_spot       = {}  # Spot from last refresh
prev_scores     = {}  # Scores from last message sent
prev_signals    = {}  # Last sent signals for change filter
prev_pcr        = {}  # PCR from last refresh for trend tracking (Fix 4)
active_trades   = {}  # Tracking open trades for partial booking (Fix 3)
# active_trades format: { "BANKNIFTY": { "side":"PE", "entry":306, "strike":58100, "qty":1 } }

# ══════════════════════════════════════════════════════════════
# FIX 1 — AUTO EXPIRY DETECTION
# ══════════════════════════════════════════════════════════════
def get_next_expiry(scrip, seg):
    """Auto-fetch next expiry from Dhan API"""
    url = "https://api.dhan.co/v2/optionchain/expirylist"
    headers = {
        "access-token": DHAN_API_KEY,
        "client-id": DHAN_CLIENT_ID,
        "Content-Type": "application/json",
    }
    payload = {"UnderlyingScrip": scrip, "UnderlyingSeg": seg}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        expiries = data.get("data", [])
        if expiries:
            # Return nearest upcoming expiry
            today = datetime.now(IST).strftime("%Y-%m-%d")
            upcoming = [e for e in expiries if e >= today]
            if upcoming:
                return min(upcoming)
    except Exception as e:
        print(f"Expiry fetch error for scrip {scrip}: {e}")

    # Fallback — calculate next Tuesday/Thursday
    return calculate_fallback_expiry(scrip)

def calculate_fallback_expiry(scrip):
    """
    Calculate expiry if API fails
    Nifty(13)  → next Tuesday (weekly)
    BankNifty(25) → last Tuesday of month (monthly only)
    Sensex(51) → next Thursday (weekly)
    """
    today = datetime.now(IST)

    if scrip == 13:  # Nifty — weekly Tuesday
        target_day = 1  # Tuesday
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        expiry = today + timedelta(days=days_ahead)

    elif scrip == 25:  # BankNifty — monthly only (last Tuesday)
        # Find last Tuesday of current month
        year  = today.year
        month = today.month
        # Get last day of month
        if month == 12:
            last_day = datetime(year+1, 1, 1, tzinfo=IST) - timedelta(days=1)
        else:
            last_day = datetime(year, month+1, 1, tzinfo=IST) - timedelta(days=1)
        # Find last Tuesday
        days_back = (last_day.weekday() - 1) % 7
        last_tue = last_day - timedelta(days=days_back)
        # If already past last Tuesday this month, go to next month
        if last_tue.date() <= today.date():
            month = month + 1 if month < 12 else 1
            year  = year if month > 1 else year + 1
            if month == 12:
                last_day = datetime(year+1, 1, 1, tzinfo=IST) - timedelta(days=1)
            else:
                last_day = datetime(year, month+1, 1, tzinfo=IST) - timedelta(days=1)
            days_back = (last_day.weekday() - 1) % 7
            last_tue  = last_day - timedelta(days=days_back)
        expiry = last_tue

    else:  # Sensex(51) — weekly Thursday
        target_day = 3  # Thursday
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        expiry = today + timedelta(days=days_ahead)

    return expiry.strftime("%Y-%m-%d")

def refresh_expiries():
    """Refresh expiry dates for all indices"""
    expiries = {}
    for name, cfg in INDICES_BASE.items():
        exp = get_next_expiry(cfg["scrip"], cfg["seg"])
        expiries[name] = exp
        print(f"  📅 {name} expiry: {fmt_date(exp)} ({exp})")
    return expiries

INDICES_BASE = {
    "NIFTY":     {"scrip": 13,  "seg": "IDX_I", "lot": 65,  "expiry_type": "weekly_tue"},
    "SENSEX":    {"scrip": 51,  "seg": "IDX_I", "lot": 20,  "expiry_type": "weekly_thu"},
    "BANKNIFTY": {"scrip": 25,  "seg": "IDX_I", "lot": 30,  "expiry_type": "monthly_tue"},
}

# ── PHASE 2 — ONE TAP ORDER PLACEMENT ─────────────────────────
# Uses Dhan API to place order directly
# Telegram inline button → confirms → order placed

def get_security_id(name, strike, side, expiry):
    """
    Fetch security ID for option from Dhan instrument list
    NIFTY/BANKNIFTY → NSE_FNO
    SENSEX → BSE_FNO
    """
    try:
        seg = "BSE_FNO" if name == "SENSEX" else "NSE_FNO"
        url = f"https://api.dhan.co/v2/instruments/{seg}"
        headers = {
            "access-token": DHAN_API_KEY,
            "client-id": DHAN_CLIENT_ID,
        }
        r = requests.get(url, headers=headers, timeout=10)
        instruments = r.json()
        # Search for matching instrument
        exp_fmt = datetime.strptime(expiry, "%Y-%m-%d").strftime("%d-%b-%Y").upper()
        for inst in instruments:
            sym = inst.get("tradingSymbol", "")
            if (name in sym and
                str(strike) in sym and
                side in sym and
                exp_fmt in sym):
                return inst.get("securityId", "")
    except Exception as e:
        print(f"Security ID lookup error: {e}")
    return None

def get_exchange_segment(name):
    """Get correct exchange segment per index"""
    # Sensex trades on BSE_FNO, Nifty/BankNifty on NSE_FNO
    return "BSE_FNO" if name == "SENSEX" else "NSE_FNO"

def place_super_order(name, strike, side, lot_size, entry_ltp, is_late=False):
    """
    Place Super Order on Dhan — Entry + Target + SL in ONE call
    Works for NIFTY, BANKNIFTY and SENSEX
    
    Super Order = Smart order that auto-manages:
    → Entry at market price
    → Target limit order at +30% (normal) or +20% (late session)
    → SL market order at -25% (normal) or -15% (late session)
    → If target hits → SL auto-cancelled
    → If SL hits → Target auto-cancelled
    """
    expiry       = current_expiries.get(name, "")
    security_id  = get_security_id(name, strike, side, expiry)
    exchange_seg = get_exchange_segment(name)

    if not security_id:
        return None, "Security ID not found — place manually on Dhan"

    # Calculate target and SL based on session
    if is_late:
        target_pct = 1.20  # +20% for late session
        sl_pct     = 0.85  # -15% for late session
        session    = "LATE"
    else:
        target_pct = 1.30  # +30% for normal (book 50% here)
        sl_pct     = 0.75  # -25% for normal
        session    = "NORMAL"

    target_price = round(entry_ltp * target_pct, 2)
    sl_price     = round(entry_ltp * sl_pct,     2)

    url     = "https://api.dhan.co/v2/orders/super"
    headers = {
        "access-token":  DHAN_API_KEY,
        "client-id":     DHAN_CLIENT_ID,
        "Content-Type":  "application/json",
    }
    payload = {
        "dhanClientId":      DHAN_CLIENT_ID,
        "transactionType":   "BUY",
        "exchangeSegment":   exchange_seg,
        "productType":       "INTRADAY",
        "orderType":         "MARKET",
        "validity":          "DAY",
        "securityId":        security_id,
        "quantity":          lot_size,
        "price":             0,
        "triggerPrice":      0,
        "disclosedQuantity": 0,
        "afterMarketOrder":  False,
        "correlationId":     f"ocr_{name[:2]}_{strike}{side[0]}_{session}",
        # Super Order legs
        "targetPrice":       target_price,   # Limit sell at target
        "stopLossPrice":     sl_price,        # SL-M sell at SL
        "trailingJump":      0,               # No trailing (we manage manually)
    }

    try:
        r    = requests.post(url, headers=headers, json=payload, timeout=10)
        data = r.json()
        order_id = data.get("orderId", "")
        status   = data.get("orderStatus", "PENDING")

        if order_id:
            gain_pts  = round(target_price - entry_ltp, 2)
            loss_pts  = round(entry_ltp - sl_price, 2)
            gain_amt  = round(gain_pts * lot_size)
            loss_amt  = round(loss_pts * lot_size)
            return order_id, (
                f"🚀 *ORDER PLACED SUCCESSFULLY!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*BUY {name} {side} {strike:,}*\n"
                f"Order ID: `{order_id}`\n"
                f"Status: {status}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Entry Price:  ₹{entry_ltp} (MARKET)\n"
                f"Target:       ₹{target_price} (+{round((target_pct-1)*100)}%) → *+₹{gain_amt}*\n"
                f"Stop Loss:    ₹{sl_price} (-{round((1-sl_pct)*100)}%) → *-₹{loss_amt}*\n"
                f"Lot Size:     {lot_size} qty\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Target + SL auto-set on Dhan\n"
                f"✅ Monitor in Dhan app\n"
                f"⏰ Exit before 3:05 PM"
            )
        else:
            err = data.get("remarks", data.get("message", "Unknown error"))
            return None, f"❌ Super Order failed: {err}\nPlace manually on Dhan."

    except Exception as e:
        return None, f"❌ API error: {e}\nPlace manually on Dhan."

def get_order_fill_price(order_id):
    """Get actual fill price after order execution"""
    try:
        url     = f"https://api.dhan.co/v2/orders/{order_id}"
        headers = {
            "access-token": DHAN_API_KEY,
            "client-id":    DHAN_CLIENT_ID,
        }
        r    = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        return data.get("averageTradedPrice", 0)
    except:
        return 0

# Store for pending confirmations
pending_orders = {}
# Store current expiries for order placement
current_expiries = {}

def handle_telegram_update(update):
    """
    Handle Telegram bot commands and callbacks
    Also catches 6-digit TOTP code from user
    """
    global pending_orders, current_expiries, waiting_for_totp

    msg  = update.get("message", {})
    text = msg.get("text", "").strip()

    # ── TOTP CODE HANDLER ─────────────────────────────────────
    # If bot is waiting for TOTP and user sends 6-digit number
    if waiting_for_totp and text.isdigit() and len(text) == 6:
        print(f"📲 TOTP received: {text}")
        generate_token_with_totp(text)
        return

    # ── /start command ────────────────────────────────────────
    if text == "/start":
        send_telegram(
            "👋 *OC Radar Bot v7*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Commands:\n"
            "/totp → Generate new token\n"
            "/status → Bot status\n"
            "/cancel → Cancel pending order\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Bot auto-asks for TOTP at 8:55 AM daily!"
        )
        return

    # ── /totp command — manual token request ──────────────────
    if text == "/totp":
        request_totp_from_user()
        return
    
    # Handle /buy command
    if text.startswith("/buy_"):
        parts = text.replace("/buy_", "").split("_")
        if len(parts) >= 3:
            name    = parts[0]
            side    = parts[1]
            strike  = int(parts[2])
            ltp     = int(parts[3]) / 100 if len(parts) > 3 else 0
            is_late = parts[4] == "L" if len(parts) > 4 else False
            cfg     = INDICES_BASE.get(name, {})
            lot     = cfg.get("lot", 0)

            # Calculate target and SL for display
            if is_late:
                target = round(ltp * 1.20, 2)
                sl     = round(ltp * 0.85, 2)
                tpct   = "+20%"
                slpct  = "-15%"
            else:
                target = round(ltp * 1.30, 2)
                sl     = round(ltp * 0.75, 2)
                tpct   = "+30%"
                slpct  = "-25%"

            gain_amt = round((target - ltp) * lot)
            loss_amt = round((ltp - sl) * lot)

            # Store pending order
            key = f"{name}_{side}_{strike}"
            pending_orders[key] = {
                "name": name, "side": side, "strike": strike,
                "lot": lot, "ltp": ltp, "is_late": is_late,
            }

            # Clean readable confirmation message
            lot_size_map = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}
            lot_size = lot_size_map.get(name, lot)
            capital  = round(ltp * lot)
            session_label = "⚡ Late Session" if is_late else "✅ Normal Session"
            exchange = "BSE F&O" if name == "SENSEX" else "NSE F&O"

            confirm_msg = (
                f"🔔 *ORDER CONFIRMATION*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*BUY {name} {side} {strike:,}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Entry Price:  ₹{ltp}\n"
                f"Target:       ₹{target} ({tpct}) → *+₹{gain_amt}*\n"
                f"Stop Loss:    ₹{sl} ({slpct}) → *-₹{loss_amt}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Lot Size:     {lot_size} qty\n"
                f"Lots:         {lot // lot_size if lot >= lot_size else 1}\n"
                f"Exchange:     {exchange}\n"
                f"Product:      INTRADAY · MARKET\n"
                f"Session:      {session_label}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Reply: /confirm_{name}_{side}_{strike}\n"
                f"❌ Reply: /cancel\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *Real money will be used!*"
            )
            send_telegram(confirm_msg)
    
    # Handle /confirm command
    elif text.startswith("/confirm_"):
        parts = text.replace("/confirm_", "").split("_")
        if len(parts) >= 3:
            name   = parts[0]
            side   = parts[1]
            strike = int(parts[2])
            key    = f"{name}_{side}_{strike}"
            order  = pending_orders.get(key)
            
            if order:
                name_o   = order["name"]
                side_o   = order["side"]
                strike_o = order["strike"]
                lot_o    = order["lot"]
                ltp_o    = order.get("ltp", 0)
                is_late_o= order.get("is_late", False)

                send_telegram(
                    f"⏳ Placing Super Order...\n"
                    f"*{name_o}* {side_o} {strike_o:,}\n"
                    f"Entry + Target + SL in ONE order!"
                )

                order_id, msg_txt = place_super_order(
                    name_o, strike_o, side_o, lot_o, ltp_o, is_late_o
                )

                send_telegram(msg_txt)
                pending_orders.pop(key, None)
            else:
                send_telegram("❌ No pending order found. Signal may have expired.")
    
    elif text == "/cancel":
        pending_orders.clear()
        send_telegram("❌ Order cancelled.")
    
    elif text == "/status":
        # Show current signals and positions
        send_telegram(
            f"📊 *Bot Status*\nRunning: ✅\nPending orders: {len(pending_orders)}\nLast refresh: See latest signal message"
        )

def check_telegram_updates():
    """Check for new Telegram messages (commands from user).
    Uses the poll-only blackout — never affects signal sends."""
    global _tg_poll_blocked, _tg_poll_retry_at
    if not _tg_poll_reachable():
        return   # silent skip when polling is backing off
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        params = {"timeout": 1, "limit": 5}
        if hasattr(check_telegram_updates, 'last_update_id'):
            params["offset"] = check_telegram_updates.last_update_id + 1
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        updates = data.get("result", [])
        for upd in updates:
            check_telegram_updates.last_update_id = upd["update_id"]
            handle_telegram_update(upd)
        _tg_poll_blocked = False
    except Exception as e:
        if any(k in str(e) for k in ("ConnectTimeout", "Max retries", "timed out")):
            _tg_poll_blocked  = True
            _tg_poll_retry_at = time.time() + 300
        # silent — don't spam console

# ══════════════════════════════════════════════════════════════
# FIX 5 — HOLIDAY DETECTION
# ══════════════════════════════════════════════════════════════
def is_market_open():
    """Check if market is open — weekday + not holiday"""
    now  = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        return False
    if today in NSE_HOLIDAYS_2026:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE

def is_trading_day():
    """Check if today is a trading day"""
    now   = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        return False, "Weekend"
    if today in NSE_HOLIDAYS_2026:
        return False, "NSE Holiday"
    return True, "Trading Day"

# ══════════════════════════════════════════════════════════════
# FIX 4 — GLOBAL MARKETS (Yahoo Finance — NON-CRITICAL, FAIL-FAST)
# Yahoo Finance officially disallows automated/bot access and can
# silently block or rate-limit this. This data is supplementary
# only (Global Bias line) — it never affects core trading signals.
# A circuit breaker skips Yahoo entirely for 15 min after 2
# consecutive failures, so a Yahoo block can't slow down or stall
# your main signal/Telegram loop.
# ══════════════════════════════════════════════════════════════
_global_mkt_fail_count = 0
_global_mkt_skip_until = 0

def fetch_global_markets():
    """Fetch SGX Nifty, Dow Futures, Crude, USD/INR.
    Best-effort only — failures here must never block or delay
    the core signal pipeline."""
    global _global_mkt_fail_count, _global_mkt_skip_until
    global_data = {}

    # Circuit breaker — skip Yahoo entirely if it's been failing
    if time.time() < _global_mkt_skip_until:
        return global_data  # empty dict — caller already handles this gracefully

    sources = {
        "GIFT Nifty": "https://query1.finance.yahoo.com/v8/finance/chart/GIFT50.NS",
        "Dow Fut":    "https://query1.finance.yahoo.com/v8/finance/chart/YM=F",
        "Crude":      "https://query1.finance.yahoo.com/v8/finance/chart/CL=F",
        "USD/INR":    "https://query1.finance.yahoo.com/v8/finance/chart/USDINR=X",
    }

    any_success = False
    for name, url in sources.items():
        try:
            r = requests.get(url, timeout=4,  # short timeout — fail fast, don't stall loop
                headers={"User-Agent": "Mozilla/5.0"})
            data  = r.json()
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            prev  = data["chart"]["result"][0]["meta"]["previousClose"]
            chg   = round(price - prev, 2)
            chg_p = round(chg / prev * 100, 2) if prev else 0
            if name == "GIFT Nifty":
                global_data[name] = {"price": price, "chg": chg, "chg_pct": chg_p}
            else:
                global_data[name] = {"price": round(price, 2), "chg_pct": chg_p}
            any_success = True
        except Exception:
            global_data[name] = None

    if any_success:
        _global_mkt_fail_count = 0
        _global_mkt_skip_until = 0
    else:
        _global_mkt_fail_count += 1
        if _global_mkt_fail_count >= 2:
            _global_mkt_skip_until = time.time() + 900  # back off 15 min
            print("⚠️ Global markets (Yahoo) unreachable twice — "
                  "skipping for 15 min, signals unaffected")

    return global_data

def format_global(global_data):
    """Format global market data for Telegram"""
    if not any(global_data.values()):
        return None

    lines = ["🌍 *Global Pre-Market:*"]
    icons = {
        "GIFT Nifty": "🇮🇳",
        "Dow Fut":    "🇺🇸",
        "Crude":      "🛢️",
        "USD/INR":    "💵",
    }

    overall_bias = 0
    for name, d in global_data.items():
        if not d:
            continue
        chg_p = d.get("chg_pct", 0)
        icon  = icons.get(name, "📊")
        arrow = "▲" if chg_p > 0 else "▼" if chg_p < 0 else "➡️"
        color = "🟢" if chg_p > 0.3 else "🔴" if chg_p < -0.3 else "🟡"

        if name == "USD/INR":
            # Rising USD/INR is negative for markets
            color = "🔴" if chg_p > 0.3 else "🟢" if chg_p < -0.3 else "🟡"
            overall_bias -= chg_p
        else:
            overall_bias += chg_p

        price = d.get("price", 0)
        if name == "GIFT Nifty":
            chg = d.get("chg", 0)
            lines.append(f"  {icon} {name}: {price:,.0f} ({chg:+.0f} | {chg_p:+.2f}%) {color}")
        else:
            lines.append(f"  {icon} {name}: {price:,.2f} ({chg_p:+.2f}%) {color}")

    # Overall bias
    if overall_bias > 0.5:
        bias = "🟢 BULLISH — Positive for markets"
    elif overall_bias < -0.5:
        bias = "🔴 BEARISH — Negative for markets"
    else:
        bias = "🟡 NEUTRAL — Mixed signals"
    lines.append(f"  📊 Bias: {bias}")

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
# DHAN MARKET FEED HELPER
# Uses Dhan's quote API (always reachable) instead of NSE
# IDX_I security IDs: 13=NIFTY, 21=VIX, 25=BANKNIFTY, 51=SENSEX
# ══════════════════════════════════════════════════════════════
def _dhan_quote(payload):
    """POST to Dhan v2/marketfeed/quote, return raw data dict."""
    url = "https://api.dhan.co/v2/marketfeed/quote"
    headers = {
        "access-token": DHAN_API_KEY,
        "client-id":    DHAN_CLIENT_ID,
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    return r.json()

# ══════════════════════════════════════════════════════════════
# VIX  — Dhan IDX_I security 21
# ══════════════════════════════════════════════════════════════
def fetch_vix():
    try:
        data = _dhan_quote({"IDX_I": [21]})
        d    = data.get("data", {}).get("IDX_I", {}).get("21", {})
        if not d:
            return 0, 0, 0
        ltp  = float(d.get("last_price", 0))
        prev = float(d.get("close_price", ltp) or ltp)
        chg  = round(ltp - prev, 2)
        chgp = round(chg / prev * 100, 2) if prev else 0
        return round(ltp, 2), chg, chgp
    except Exception as e:
        print(f"VIX fetch error: {e}")
    return 0, 0, 0

def analyze_vix(vix, chg, chg_pct):
    if vix == 0:
        return "—", "VIX unavailable", 0
    if vix < 11:
        level, advice, adj = "🔵 VERY LOW", "Very cheap premiums — good time to buy", 5
    elif vix < 14:
        level, advice, adj = "🟢 LOW", "Good for option buying", 3
    elif vix < 17:
        level, advice, adj = "🟡 MODERATE", "Normal premiums — standard size", 0
    elif vix < 20:
        level, advice, adj = "🟠 HIGH", "Expensive premiums — reduce size 30%", -5
    else:
        level, advice, adj = "🔴 VERY HIGH", "Very expensive — avoid buying", -10

    trend = ("📈 Rising fast" if chg_pct > 3 else
             "📈 Rising" if chg_pct > 1 else
             "📉 Falling fast" if chg_pct < -3 else
             "📉 Falling" if chg_pct < -1 else "➡️ Stable")
    return level, f"{advice} · {trend}", adj

# ══════════════════════════════════════════════════════════════
# FUTURES — Dhan IDX_I: 13=NIFTY, 25=BANKNIFTY, 51=SENSEX
# Spot prices come from Dhan quote API; futures = spot (basis≈0)
# when actual futures contracts aren't needed for core signals.
# ══════════════════════════════════════════════════════════════
def fetch_futures():
    futures = {}
    try:
        data = _dhan_quote({"IDX_I": [13, 25, 51]})
        idx  = data.get("data", {}).get("IDX_I", {})
        mapping = {"13": "NIFTY", "25": "BANKNIFTY", "51": "SENSEX"}
        for sec_id, name in mapping.items():
            d = idx.get(sec_id, {})
            if not d:
                continue
            spot = float(d.get("last_price", 0))
            prev = float(d.get("close_price", spot) or spot)
            if spot > 0:
                # Use prev-close as proxy futures price to compute basis
                futures[name] = {"fut": round(prev, 2), "spot": spot}
    except Exception as e:
        print(f"Futures fetch error: {e}")
    return futures

def analyze_futures(futures_data):
    analysis = {}
    for name, data in futures_data.items():
        fut = data.get("fut", 0); spot = data.get("spot", 0)
        if not fut or not spot:
            continue
        basis     = round(fut - spot, 2)
        basis_pct = round(basis / spot * 100, 3) if spot else 0
        if basis > 100:
            sentiment, adj = "🟢 Strong bullish", 8
        elif basis > 30:
            sentiment, adj = "🟢 Bullish", 4
        elif basis > 0:
            sentiment, adj = "🟡 Slightly bullish", 2
        elif basis > -30:
            sentiment, adj = "🟡 Slightly bearish", -2
        elif basis > -100:
            sentiment, adj = "🔴 Bearish", -4
        else:
            sentiment, adj = "🔴 Strong bearish", -8
        analysis[name] = {
            "fut": fut, "spot": spot, "basis": basis,
            "basis_pct": basis_pct, "sentiment": sentiment, "score_adj": adj,
        }
    return analysis

# ══════════════════════════════════════════════════════════════
# DHAN API
# ══════════════════════════════════════════════════════════════
def fetch_option_chain(scrip, seg, expiry):
    url = "https://api.dhan.co/v2/optionchain"
    headers = {
        "access-token": DHAN_API_KEY,
        "client-id":    DHAN_CLIENT_ID,
        "Content-Type": "application/json",
    }
    payload = {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        # Don't raise on 4xx — just parse the error
        if r.status_code == 401:
            print(f"  ⚠️ Dhan API 401 — token expired or IP not whitelisted")
            return None
        if r.status_code == 429:
            print(f"  ⚠️ Dhan API rate limit — waiting 3 seconds")
            time.sleep(3)
            return None
        if not r.text.strip():
            print(f"  ⚠️ Dhan API empty response for scrip {scrip}")
            return None
        return r.json()
    except Exception as e:
        print(f"  ⚠️ Dhan API error scrip {scrip}: {e}")
        return None

def parse_oc(raw):
    # Handle error responses
    if not raw:
        return None, []
    # If Dhan returns error string or non-dict
    if isinstance(raw, str):
        print(f"  ⚠️ OC API returned string: {raw[:100]}")
        return None, []
    if not isinstance(raw, dict):
        print(f"  ⚠️ OC API returned unexpected type: {type(raw)}")
        return None, []
    # Check for Dhan error response
    if raw.get("status") == "failure" or raw.get("errorCode"):
        print(f"  ⚠️ OC API error: {raw.get('remarks', raw.get('errorCode', 'Unknown'))}")
        return None, []
    # Dhan v2 format: {"data": {"oc": [...], "last_price": 23881.5}, "status": "success"}
    # Fallback: {"data": [...], "last_price": ...}
    d = raw.get("data", {})
    if isinstance(d, dict):
        spot   = d.get("last_price") or d.get("underlyingLastPrice") or 0
        chains = d.get("oc", d.get("optionChain", d.get("data", [])))
    else:
        spot   = raw.get("last_price") or raw.get("underlyingLastPrice") or 0
        chains = d  # d is already the list
    if not chains:
        return spot, []
    parsed = []
    for row in chains:
        if not isinstance(row, dict):
            continue
        strike = row.get("strikePrice", 0)
        ce = row.get("callOption", row.get("CE", {})) or {}
        pe = row.get("putOption",  row.get("PE", {})) or {}
        parsed.append({
            "strike": strike,
            "ceLTP":  ce.get("lastTradedPrice", ce.get("ltp", 0)),
            "ceOI":   ce.get("openInterest",    ce.get("oi",  0)),
            "ceIV":   ce.get("impliedVolatility",ce.get("iv", 0)),
            "peLTP":  pe.get("lastTradedPrice", pe.get("ltp", 0)),
            "peOI":   pe.get("openInterest",    pe.get("oi",  0)),
            "peIV":   pe.get("impliedVolatility",pe.get("iv", 0)),
        })
    return spot, [p for p in parsed if p["ceOI"] > 0 or p["peOI"] > 0]

# ══════════════════════════════════════════════════════════════
# OI CHANGE + BUILDUP
# ══════════════════════════════════════════════════════════════
def compute_oi_changes(name, spot, data):
    global prev_oi, prev_spot
    changes = {}
    if name not in prev_oi:
        prev_oi[name]   = {d["strike"]: {"ceOI": d["ceOI"], "peOI": d["peOI"]} for d in data}
        prev_spot[name] = spot
        return changes, 0, 0
    total_ce = total_pe = 0
    for d in data:
        s    = d["strike"]
        prev = prev_oi[name].get(s, {"ceOI": 0, "peOI": 0})
        cc   = d["ceOI"] - prev["ceOI"]
        pc   = d["peOI"] - prev["peOI"]
        if abs(cc) > 50000 or abs(pc) > 50000:
            changes[s] = {"ceChg": cc, "peChg": pc}
        total_ce += cc; total_pe += pc
    prev_oi[name]   = {d["strike"]: {"ceOI": d["ceOI"], "peOI": d["peOI"]} for d in data}
    prev_spot[name] = spot
    return changes, total_ce, total_pe

def detect_buildup(name, spot, ce_added, pe_added):
    prev = prev_spot.get(name, spot)
    up   = spot > prev; down = spot < prev
    ce_b = ce_added > 100000; pe_b = pe_added > 100000
    ce_r = ce_added < -100000; pe_r = pe_added < -100000
    if up   and ce_b: return "LONG BUILDUP",  "✅ Bullish — longs entering",   15
    if up   and pe_r: return "SHORT COVER",   "🔄 Bullish — shorts exiting",   10
    if down and pe_b: return "SHORT BUILDUP", "🔴 Bearish — shorts entering", -15
    if down and ce_r: return "LONG UNWIND",   "⚠️ Bearish — longs exiting",   -10
    if ce_b and pe_b: return "BOTH ADDING",   "⚠️ Uncertain — both sides",      0
    return "NEUTRAL", "Stable", 0

def top_oi_changes(changes, spot):
    near = {k: v for k, v in changes.items() if abs(k - spot) / spot <= 0.03}
    if not near:
        return []
    top = sorted(near.items(),
                 key=lambda x: abs(x[1]["ceChg"]) + abs(x[1]["peChg"]),
                 reverse=True)[:2]
    out = []
    for strike, chg in top:
        cc = chg["ceChg"]; pc = chg["peChg"]
        if abs(cc) > abs(pc):
            side = "CE"; amt = cc
            note = "resistance building" if amt > 0 else "resistance weakening"
        else:
            side = "PE"; amt = pc
            note = "support building" if amt > 0 else "support weakening"
        icon = "📈" if amt > 0 else "📉"
        out.append(f"{icon} {strike:,} {side}: {'+' if amt>0 else ''}{amt//1000}K → {note}")
    return out

# ══════════════════════════════════════════════════════════════
# PHASE 1 — DYNAMIC WEIGHT SYSTEM
# Market Regime Detection + VIX Adjustment
# ══════════════════════════════════════════════════════════════

def detect_market_regime(oicr, vix, is_expiry, pcr, mpd):
    """
    Detect current market regime from live data
    Returns regime name + description
    """
    if is_expiry:
        return "EXPIRY", "⚡ Expiry day — Max Pain dominates"
    elif oicr < 35 and vix > 14:
        return "VOLATILE_TREND", "🔥 Volatile trending — OI signals strongest"
    elif oicr < 45:
        return "TRENDING", "📈 Trending day — OI buildup leads"
    elif oicr > 65:
        if abs(pcr - 1.0) < 0.1:
            return "RANGE_NEUTRAL", "📌 Range neutral — Max Pain dominates"
        else:
            return "RANGE_BIASED", "📌 Range with bias — PCR contrarian"
    elif vix > 18:
        return "HIGH_VIX", "😱 High fear — PE signals stronger"
    else:
        return "NORMAL", "📊 Normal market — balanced weights"

# Weight multipliers per regime
# Each signal gets a multiplier (1.0 = default, 2.0 = double weight)
REGIME_WEIGHTS = {
    "EXPIRY": {
        "pcr":      0.5,   # PCR less reliable on expiry
        "max_pain": 3.0,   # Max Pain is GOD on expiry!
        "cw_hit":   2.0,   # Walls very powerful on expiry
        "oicr":     1.0,
        "oi_build": 0.8,
        "oi_ratio": 1.0,
        "iv_skew":  1.5,   # IV skew matters on expiry
        "straddle": 1.5,
    },
    "VOLATILE_TREND": {
        "pcr":      1.5,
        "max_pain": 0.3,   # MP meaningless in volatile trend
        "cw_hit":   2.0,   # Walls as targets/reversals
        "oicr":     2.5,   # OICR breakout is key
        "oi_build": 2.0,   # OI buildup confirms direction
        "oi_ratio": 1.5,
        "iv_skew":  1.0,
        "straddle": 0.5,
    },
    "TRENDING": {
        "pcr":      1.5,
        "max_pain": 0.5,   # MP less relevant in trend
        "cw_hit":   1.5,
        "oicr":     2.0,
        "oi_build": 1.5,
        "oi_ratio": 1.5,
        "iv_skew":  1.0,
        "straddle": 0.8,
    },
    "RANGE_NEUTRAL": {
        "pcr":      1.0,
        "max_pain": 2.5,   # MP most important in range
        "cw_hit":   0.5,   # Walls less reliable in range
        "oicr":     0.5,
        "oi_build": 0.5,
        "oi_ratio": 0.8,
        "iv_skew":  1.5,
        "straddle": 2.0,   # Straddle = range predictor
    },
    "RANGE_BIASED": {
        "pcr":      2.0,   # PCR contrarian very useful
        "max_pain": 2.0,
        "cw_hit":   0.8,
        "oicr":     0.5,
        "oi_build": 0.8,
        "oi_ratio": 1.0,
        "iv_skew":  1.5,
        "straddle": 1.5,
    },
    "HIGH_VIX": {
        "pcr":      1.5,
        "max_pain": 1.0,
        "cw_hit":   1.5,
        "oicr":     1.5,
        "oi_build": 1.0,
        "oi_ratio": 2.0,   # OI ratio more reliable in fear
        "iv_skew":  2.0,   # IV skew very important
        "straddle": 0.5,   # Straddle inflated by fear
    },
    "NORMAL": {
        "pcr":      1.0,
        "max_pain": 1.0,
        "cw_hit":   1.0,
        "oicr":     1.0,
        "oi_build": 1.0,
        "oi_ratio": 1.0,
        "iv_skew":  1.0,
        "straddle": 1.0,
    },
}

def get_vix_multiplier(vix):
    """
    VIX-based score adjustment
    Low VIX = less reliable signals
    High VIX = stronger PE signals
    """
    if vix == 0:
        return 1.0, 1.0, "VIX unknown"
    elif vix < 11:
        return 0.8, 0.8, "VIX very low — signals weaker"
    elif vix < 14:
        return 1.0, 1.0, "VIX normal — standard weights"
    elif vix < 17:
        return 0.9, 1.1, "VIX moderate — PE slightly favored"
    elif vix < 20:
        return 0.8, 1.3, "VIX high — PE signals stronger"
    else:
        return 0.0, 0.0, "VIX very high — SKIP all signals"

# Store current regime for Telegram display
current_regime     = {}
current_regime_desc= {}

# ══════════════════════════════════════════════════════════════
# SIGNAL ENGINE
# ══════════════════════════════════════════════════════════════
def compute_signals(name, spot, data, ce_added, pe_added, bonus,
                    vix=0, is_expiry=False):
    if not data or spot == 0:
        return None
    active = [d for d in data if abs(d["strike"] - spot) / spot <= 0.08]
    if len(active) < 3:
        return None

    # Max Pain
    ml, mp = float("inf"), active[0]["strike"]
    for exp in [d["strike"] for d in active]:
        loss = sum(max(0,exp-d["strike"])*d["ceOI"] + max(0,d["strike"]-exp)*d["peOI"] for d in active)
        if loss < ml: ml, mp = loss, exp
    mpd = (spot - mp) / spot * 100

    # PCR
    near = [d for d in active if abs(d["strike"]-spot)/spot <= 0.02]
    tc = sum(d["ceOI"] for d in near); tp = sum(d["peOI"] for d in near)
    pcr = round(tp/tc, 2) if tc else 1.0

    # OI Ratio
    cet = sum(d["ceOI"] for d in active if d["strike"] > spot)
    pet = sum(d["peOI"] for d in active if d["strike"] < spot)
    oir = pet / (cet + pet) if (cet + pet) > 0 else 0.5

    # Walls
    ces = [d for d in active if d["strike"] > spot and d["ceOI"] > 0]
    pes = [d for d in active if d["strike"] < spot and d["peOI"] > 0]
    cw  = max(ces, key=lambda x: x["ceOI"])["strike"] if ces else spot*1.03
    pw  = max(pes, key=lambda x: x["peOI"])["strike"] if pes else spot*0.97
    dcw = (cw - spot) / spot * 100
    dpw = (spot - pw) / spot * 100

    # Straddle
    atm  = min(active, key=lambda d: abs(d["strike"]-spot))
    strd = atm["ceLTP"] + atm["peLTP"]
    sp   = strd / spot * 100

    # OICR
    tot = sum(d["ceOI"]+d["peOI"] for d in active)
    nr  = sum(d["ceOI"]+d["peOI"] for d in active if abs(d["strike"]-spot)/spot <= 0.01)
    oicr = round(nr/tot*100, 1) if tot else 50

    # IV Skew
    ivs = (atm["peIV"] or 0) - (atm["ceIV"] or 0)

    # ── PHASE 1: MARKET REGIME DETECTION ─────────────────────
    regime, regime_desc = detect_market_regime(
        oicr, vix, is_expiry, pcr, mpd
    )
    w = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["NORMAL"])

    # VIX multiplier
    ce_vix_mult, pe_vix_mult, vix_note = get_vix_multiplier(vix)

    # Store for Telegram display
    current_regime[name]      = regime
    current_regime_desc[name] = regime_desc

    # If VIX too high — skip everything
    if ce_vix_mult == 0:
        return None

    # ── CW / PW HIT SIGNAL (NEW — most impactful!) ───────────
    # When spot hits Call Wall = strong resistance = PE signal
    # When spot hits Put Wall  = strong support    = CE signal
    # When spot BREAKS above CW = CE breakout signal!
    # When spot BREAKS below PW = PE breakdown signal!
    cw_hit_boost_pe = 0
    cw_hit_boost_ce = 0

    if spot >= cw:
        # Spot AT or ABOVE Call Wall
        if spot < cw * 1.005:
            # At CW — strong resistance — PE boost
            cw_hit_boost_pe = 22
            cw_hit_boost_ce = -12  # Don't buy CE at resistance!
        else:
            # BREAKOUT above CW — CE boost
            cw_hit_boost_ce = 15
            cw_hit_boost_pe = -5

    if spot <= pw:
        # Spot AT or BELOW Put Wall
        if spot > pw * 0.995:
            # At PW — strong support — CE boost
            cw_hit_boost_ce += 22
            cw_hit_boost_pe += -12  # Don't buy PE at support!
        else:
            # BREAKDOWN below PW — PE boost
            cw_hit_boost_pe += 15
            cw_hit_boost_ce += -5

    # ── CE SCORE — Dynamic weights applied ───────────────────
    ce_s = 0
    # PCR
    pcr_ce = 25 if pcr>1.35 else 20 if pcr>1.20 else 15 if pcr>1.10 else 8 if pcr>1.0 else 0
    ce_s += round(pcr_ce * w["pcr"])
    # OI Ratio
    oir_ce = 18 if oir<0.38 else 12 if oir<0.43 else 6 if oir<0.48 else 0
    ce_s += round(oir_ce * w["oi_ratio"])
    # Max Pain
    mp_ce = 20 if mpd>2.0 else 15 if mpd>1.0 else 10 if mpd>0.2 else 5 if mpd>0 else 0
    ce_s += round(mp_ce * w["max_pain"])
    # Put Wall distance
    pw_ce = 12 if dpw>3 else 8 if dpw>1.5 else 4 if dpw>0.5 else 0
    ce_s += round(pw_ce * w["cw_hit"])
    # Call Wall distance — CW far = room to rise
    cw_ce = 10 if dcw>2 else 7 if dcw>1 else 3 if dcw>0.3 else 0
    ce_s += round(cw_ce * 1.0)
    # OICR
    oicr_ce = 15 if oicr<35 else 12 if oicr<45 else 6 if oicr<55 else 0
    ce_s += round(oicr_ce * w["oicr"])
    # Straddle
    strd_ce = 8 if sp>1.5 else 6 if sp>0.8 else 4
    ce_s += round(strd_ce * w["straddle"])
    # IV Skew
    ivs_ce = 8 if ivs<-1.5 else 5 if ivs<-0.5 else 0
    ce_s += round(ivs_ce * w["iv_skew"])
    # CW/PW Hit boost
    ce_s += round(cw_hit_boost_ce * w["cw_hit"])
    # Bonus from OI buildup + VIX + Futures
    if bonus > 0: ce_s += round(min(bonus, 15) * w["oi_build"])
    elif ce_added > 500000: ce_s -= 5
    # Apply VIX multiplier
    ce_s = round(ce_s * ce_vix_mult)
    ce_s = min(100, max(0, ce_s))

    # ── PE SCORE — Dynamic weights applied ───────────────────
    pe_s = 0
    # PCR
    pcr_pe = 25 if pcr<0.65 else 20 if pcr<0.75 else 15 if pcr<0.85 else 8 if pcr<0.95 else 0
    pe_s += round(pcr_pe * w["pcr"])
    # OI Ratio
    oir_pe = 18 if oir>0.62 else 12 if oir>0.57 else 6 if oir>0.52 else 0
    pe_s += round(oir_pe * w["oi_ratio"])
    # Max Pain
    mp_pe = 20 if mpd<-2.0 else 15 if mpd<-1.0 else 10 if mpd<-0.2 else 5 if mpd<0 else 0
    pe_s += round(mp_pe * w["max_pain"])
    # Call Wall near spot = resistance = PE
    cw_pe = 12 if dcw<0.3 else 8 if dcw<0.8 else 4 if dcw<1.5 else 0
    pe_s += round(cw_pe * w["cw_hit"])
    # Put Wall distance
    pw_pe = 10 if dpw>2.5 else 12 if dpw>1.5 else 8 if dpw>0.8 else 3
    pe_s += round(pw_pe * 1.0)
    # OICR
    oicr_pe = 15 if oicr<35 else 12 if oicr<45 else 6 if oicr<55 else 0
    pe_s += round(oicr_pe * w["oicr"])
    # Straddle
    strd_pe = 8 if sp>1.5 else 6 if sp>0.8 else 4
    pe_s += round(strd_pe * w["straddle"])
    # IV Skew
    ivs_pe = 8 if ivs>2.0 else 5 if ivs>0.5 else 0
    pe_s += round(ivs_pe * w["iv_skew"])
    # CW/PW Hit boost
    pe_s += round(cw_hit_boost_pe * w["cw_hit"])
    # Bonus from OI buildup + VIX + Futures
    if bonus < 0: pe_s += round(min(abs(bonus), 15) * w["oi_build"])
    elif pe_added > 500000: pe_s -= 5
    # Apply VIX multiplier
    pe_s = round(pe_s * pe_vix_mult)
    pe_s = min(100, max(0, pe_s))

    best = max(ce_s, pe_s)

    # ── NEW 4-BAND SYSTEM ────────────────────────────────────
    # 0-49   → SKIP (silent)
    # 50-64  → WATCH (show on Telegram, no /buy)
    # 65-74  → CAUTION (show + /buy at 50% size)
    # 75-100 → TRADE (full signal + /buy full size)
    if best < 50:
        action = "SKIP"
        size   = "Skip ❌"
    elif best < 65:
        action = "BUY CE" if ce_s >= pe_s else "BUY PE"
        size   = "👀 WATCH — Observe only (no trade)"
    elif best < 75:
        action = "BUY CE" if ce_s >= pe_s else "BUY PE"
        size   = "⚠️ CAUTION — 50% size only"
    else:
        action = "BUY CE" if ce_s >= pe_s else "BUY PE"
        size   = "✅ Full size"

    mkt = "💥 BREAKOUT" if oicr < 45 else "⚠️ TRENDING" if oicr < 65 else "📌 RANGE"

    ce_st = round(spot / 100) * 100 + 100
    pe_st = round(spot / 100) * 100 - 100
    ce_en = next((d["ceLTP"] for d in active if d["strike"] == ce_st), 0)
    pe_en = next((d["peLTP"] for d in active if d["strike"] == pe_st), 0)

    # OI change summary
    ois = "Neutral"
    if ce_added > 200000 and pe_added > 200000:
        ois = "Both sides adding — uncertain"
    elif ce_added > 200000:
        ois = f"CE +{ce_added//100000:.1f}L added — resistance building"
    elif pe_added > 200000:
        ois = f"PE +{pe_added//100000:.1f}L added — support building"
    elif ce_added < -200000:
        ois = f"CE -{abs(ce_added)//100000:.1f}L removed — bulls exiting"
    elif pe_added < -200000:
        ois = f"PE -{abs(pe_added)//100000:.1f}L removed — bears exiting"

    # CW/PW hit label for message
    wall_signal = ""
    if cw_hit_boost_pe >= 20:
        wall_signal = f"⚠️ CW HIT ₹{cw:,} — Strong resistance! PE reversal likely"
    elif cw_hit_boost_ce >= 20:
        wall_signal = f"✅ PW HIT ₹{pw:,} — Strong support! CE bounce likely"
    elif cw_hit_boost_ce >= 15:
        wall_signal = f"💥 CW BREAKOUT above ₹{cw:,}! CE momentum signal"
    elif cw_hit_boost_pe >= 15:
        wall_signal = f"💥 PW BREAKDOWN below ₹{pw:,}! PE momentum signal"

    return {
        "spot": spot, "mp": mp, "mp_dist": round(mpd, 2),
        "pcr": pcr, "oicr": oicr, "mkt": mkt,
        "call_wall": cw, "put_wall": pw,
        "straddle": round(strd), "str_pct": round(sp, 2),
        "ce_score": ce_s, "pe_score": pe_s,
        "action": action, "size": size,
        "ce_strike": ce_st, "pe_strike": pe_st,
        "ce_entry": ce_en, "pe_entry": pe_en,
        "ce_added": ce_added, "pe_added": pe_added,
        "oi_signal": ois,
        "wall_signal": wall_signal,
        "regime": regime,
        "regime_desc": regime_desc,
        "exp_high": round(spot + strd),
        "exp_low":  round(spot - strd),
    }

# ══════════════════════════════════════════════════════════════
# FIX 3 — CHANGE FILTER
# ══════════════════════════════════════════════════════════════
def should_send(results):
    """Only send if something meaningful changed"""
    global prev_scores, prev_signals

    if not prev_scores:
        # First message — always send
        return True, "First signal"

    for name, sig in results.items():
        if not sig:
            continue
        prev = prev_scores.get(name, {})

        # Score changed significantly
        ce_chg = abs(sig["ce_score"] - prev.get("ce_score", 0))
        pe_chg = abs(sig["pe_score"] - prev.get("pe_score", 0))
        if ce_chg >= SCORE_CHANGE_THRESHOLD or pe_chg >= SCORE_CHANGE_THRESHOLD:
            return True, f"{name} score changed ±{max(ce_chg, pe_chg)}"

        # Spot moved significantly
        prev_sp = prev.get("spot", sig["spot"])
        spot_chg = abs(sig["spot"] - prev_sp) / prev_sp * 100
        if spot_chg >= SPOT_CHANGE_THRESHOLD:
            return True, f"{name} spot moved {spot_chg:.2f}%"

        # Action changed (CE → PE or vice versa)
        if sig["action"] != prev.get("action", sig["action"]):
            return True, f"{name} action changed to {sig['action']}"

    return False, "No significant change"

def update_prev_scores(results):
    global prev_scores
    for name, sig in results.items():
        if sig:
            prev_scores[name] = {
                "ce_score": sig["ce_score"],
                "pe_score": sig["pe_score"],
                "spot":     sig["spot"],
                "action":   sig["action"],
            }

# ══════════════════════════════════════════════════════════════
# FORMAT MESSAGE
# ══════════════════════════════════════════════════════════════
def format_message(results, buildups, top_chg, ping, resp,
                   vix_data, fut_analysis, global_data, expiries,
                   pcr_trends=None, window="BEST_WINDOW", is_late=False):
    now_ist = fmt_date()
    lines   = []

    # Window label
    window_labels = {
        "BEST_WINDOW":     "✅ BEST ENTRY WINDOW (9:30–11:00 AM)",
        "SLOW_ZONE":       "⚠️ SLOW ZONE — Score ≥80 only",
        "ACTIVE":          "✅ ACTIVE ZONE (1:00–2:30 PM)",
        "LATE_SESSION":    "⚡ LATE SESSION (2:30–3:10 PM) — Score ≥85 · Strict rules",
        "EXPIRY_CAUTION":  "⚠️ EXPIRY CAUTION — Book profits only",
        "EXPIRY_EXIT":     "🔴 EXPIRY EXIT — Exit all now",
        "EXIT_ALL":        "🔴 EXIT ALL — No new entries",
    }
    w_label = window_labels.get(window, "")

    lines.append(f"📡 *OC RADAR v7* · {now_ist}")
    lines.append(f"⏱ {ping} → {resp} IST")
    if w_label:
        lines.append(f"⏰ {w_label}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    # ── SCORE DASHBOARD (at top for quick glance) ─────────────
    lines.append("📊 *SCORE DASHBOARD*")
    best_name  = ""
    best_score = 0
    best_side  = ""
    for n, s in results.items():
        if not s:
            lines.append(f"❌ {n:<12} — No data")
            continue
        ce = s["ce_score"]; pe = s["pe_score"]
        top = max(ce, pe)
        side_lbl = "CE" if ce >= pe else "PE"
        if top >= 75:
            band = "🟢 TRADE ⭐" if top == max(
                max(r["ce_score"], r["pe_score"]) for r in results.values() if r
            ) else "🟢 TRADE"
        elif top >= 65:
            band = "🟠 CAUTION"
        elif top >= 50:
            band = "🟡 WATCH"
        else:
            band = "🔴 SKIP"
        lines.append(f"{n:<12} {side_lbl} {top:>3} {band}")
        if top > best_score:
            best_score = top
            best_name  = n
            best_side  = side_lbl

    # Show market regime from best index signal
    for n, s in results.items():
        if s and s.get("regime"):
            lines.append(f"📊 Regime: *{s['regime']}* — {s.get('regime_desc','')}")
            break

    # Composite
    comp_ce = round(
        results.get("BANKNIFTY",{}).get("ce_score",0)*0.40 +
        results.get("NIFTY",    {}).get("ce_score",0)*0.35 +
        results.get("SENSEX",   {}).get("ce_score",0)*0.25
    )
    comp_pe = round(
        results.get("BANKNIFTY",{}).get("pe_score",0)*0.40 +
        results.get("NIFTY",    {}).get("pe_score",0)*0.35 +
        results.get("SENSEX",   {}).get("pe_score",0)*0.25
    )
    comp_top  = max(comp_ce, comp_pe)
    comp_side = "CE" if comp_ce >= comp_pe else "PE"
    comp_band = "🟢" if comp_top >= 75 else "🟡" if comp_top >= 50 else "🔴"
    lines.append(f"{'Composite':<12} {comp_side} {comp_top:>3} {comp_band}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    for name, sig in results.items():
        if not sig:
            continue

        score   = (sig["ce_score"] if sig["action"]=="BUY CE" else
                   sig["pe_score"] if sig["action"]=="BUY PE" else
                   max(sig["ce_score"], sig["pe_score"]))

        if score >= 75:
            band_emoji = "🟢"; band_label = "TRADE"
        elif score >= 65:
            band_emoji = "🟠"; band_label = "CAUTION"
        elif score >= 50:
            band_emoji = "🟡"; band_label = "WATCH"
        else:
            band_emoji = "🔴"; band_label = "SKIP"

        # Skip SKIP band in detail section — already in dashboard
        if band_label == "SKIP":
            continue

        act_ico = "📗" if sig["action"]=="BUY CE" else "📕"
        side    = "CE" if sig["action"]=="BUY CE" else "PE"
        strike  = sig["ce_strike"] if side=="CE" else sig["pe_strike"]
        entry   = sig["ce_entry"]  if side=="CE" else sig["pe_entry"]
        lot_sz  = INDICES_BASE.get(name, {}).get("lot", 30)
        exp_raw = expiries.get(name, "")
        exp     = fmt_date(exp_raw) if exp_raw else "—"

        # Targets
        if is_late:
            tgt = round(entry * 1.20); sl = round(entry * 0.85)
            tgt_lbl = "+20%"; sl_lbl = "-15%"
            session_note = "⚡ Late Session · Exit by 3:05 PM"
        else:
            tgt = round(entry * 1.30); sl = round(entry * 0.75)
            tgt_lbl = "+30%"; sl_lbl = "-25%"
            session_note = ""

        gain_amt = round((tgt - entry) * lot_sz)
        loss_amt = round((entry - sl)  * lot_sz)

        lines.append(f"\n{'🎯' if score>=75 else '📋'} *{name}* ₹{sig['spot']:,.0f} {sig['mkt']} · {exp}")
        lines.append(f"{act_ico} *BUY {name} {side} {strike:,}* · Score *{score}* {band_emoji} [{band_label}]")
        lines.append(f"PCR {sig['pcr']} · OICR {sig['oicr']}% · MP ₹{sig['mp']:,}")
        lines.append(f"CW ₹{sig['call_wall']:,} ↑ · PW ₹{sig['put_wall']:,} ↓")

        # OI Buildup
        pat, meaning, _ = buildups.get(name, ("NEUTRAL", "—", 0))
        if pat not in ("NEUTRAL", "BOTH ADDING"):
            lines.append(f"{pat} — {meaning}")

        # CW/PW Hit signal — show prominently!
        wall_sig = sig.get("wall_signal", "")
        if wall_sig:
            lines.append(f"🎯 {wall_sig}")

        # PCR Trend — only significant ones
        if pcr_trends and name in pcr_trends:
            pcr_t, pcr_m = pcr_trends[name]
            if pcr_t in ("RISING", "FALLING"):
                lines.append(f"📈 PCR {pcr_t} — {pcr_m}")

        # OI Change
        if sig["oi_signal"] != "Neutral":
            lines.append(f"🔄 {sig['oi_signal']}")

        # Entry / Target / SL — clean format like Sample 4
        lines.append(f"\nEntry Price:  ₹{entry}")
        lines.append(f"Target:       ₹{tgt} ({tgt_lbl}) → *+₹{gain_amt}*")
        lines.append(f"Stop Loss:    ₹{sl} ({sl_lbl}) → *-₹{loss_amt}*")
        lines.append(f"Lot Size:     {lot_sz} qty")
        if session_note:
            lines.append(session_note)

        # Trade band indicator — no /buy command in message (manual entry only)
        if score >= 75:
            lines.append(f"\n✅ *TRADE* — Full size")
        elif score >= 65:
            lines.append(f"\n⚠️ *CAUTION* — 50% size only")
        else:
            lines.append(f"\n👀 *WATCH ONLY* — Need score ≥65 to trade")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    lines.append(
        f"\n📊 *Composite* "
        f"CE: *{comp_ce}* {'🟢' if comp_ce>=75 else '🟡' if comp_ce>=55 else '🔴'} · "
        f"PE: *{comp_pe}* {'🟢' if comp_pe>=75 else '🟡' if comp_pe>=55 else '🔴'}"
    )
    # ── BOTTOM SUMMARY — VIX + FUTURES ───────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    vix, vc, vcp = vix_data
    if vix > 0:
        lvl, _, _ = analyze_vix(vix, vc, vcp)
        lines.append(f"🌡️ VIX {vix} {lvl} ({vcp:+.1f}%)")

    if fut_analysis:
        fut_summary = " · ".join([
            f"{n}: {fa['basis']:+.0f}pts {fa['sentiment'].split()[0]}"
            for n, fa in fut_analysis.items()
        ])
        lines.append(f"📊 Futures: {fut_summary}")

    # Global bias
    g_summary = format_global(global_data)
    if g_summary:
        # Extract just the bias line
        for l in g_summary.split("\n"):
            if "Bias:" in l:
                lines.append(f"🌍 {l.strip()}")
                break

    lines.append("_OC Radar v7 · Educational only_")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
# SPECIAL MESSAGES
# ══════════════════════════════════════════════════════════════
def send_market_open_alert(expiries, global_data):
    g     = format_global(global_data) or ""
    exp_l = "\n".join([f"   {n}: {fmt_date(e)}" for n, e in expiries.items()])
    msg   = (
        "🔔 *MARKET OPENS IN 5 MIN*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 *Today's Expiries:*\n{exp_l}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{g}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏰ Wait till *9:30 AM* to enter\n"
        "❌ Do NOT trade 9:15–9:30 AM\n"
        "_OC Radar v7 · Educational only_"
    )
    send_telegram(msg)

def check_expiry_warning(expiries):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    expiring = [n for n, e in expiries.items() if e == today]
    if expiring:
        names = " + ".join(expiring)
        msg   = (
            f"⚡ *EXPIRY DAY — {names}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ Theta very high — avoid OTM buying\n"
            "⚠️ Max hold: *45 min only*\n"
            "⚠️ Reduce position size by *50%*\n"
            "⚠️ Exit all positions by *2:30 PM*\n"
            "⚠️ Max Pain pull very strong today\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "_OC Radar v7 · Educational only_"
        )
        send_telegram(msg)

def send_eod_summary(results, expiries):
    now  = fmt_date()
    best = max(
        [(n, max(s["ce_score"], s["pe_score"])) for n, s in results.items() if s],
        key=lambda x: x[1], default=("—", 0)
    )
    # Next expiries
    next_exp = "\n".join([f"   {n}: {fmt_date(e)}" for n, e in expiries.items()])
    msg = (
        f"📊 *EOD SUMMARY · {now}*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Best signal: *{best[0]}* Score {best[1]}\n"
        f"📅 *Next Expiries:*\n{next_exp}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Log your trades!\n"
        "✅ Update Dhan token tonight\n"
        "_OC Radar v7 · Educational only_"
    )
    send_telegram(msg)

# ══════════════════════════════════════════════════════════════
# TELEGRAM — FIXED v10.1
# Signal sends NEVER silently skip — they retry immediately with
# short backoff. The old 5-minute blackout is now ONLY used for
# background command polling (check_telegram_updates), which is
# non-critical and safe to delay.
# ══════════════════════════════════════════════════════════════
_tg_poll_blocked  = False   # only gates polling, never signal sends
_tg_poll_retry_at = 0

def _tg_poll_reachable():
    global _tg_poll_blocked, _tg_poll_retry_at
    if not _tg_poll_blocked:
        return True
    if time.time() >= _tg_poll_retry_at:
        _tg_poll_blocked = False
        return True
    return False

def send_telegram(msg, max_retries=3, retry_delay=3):
    """
    Send a Telegram message — used for actual trading signals.
    Retries up to max_retries times with a short delay instead of
    silently going dark for 5 minutes. If all retries fail, logs
    loudly to console AND writes the missed message to a local
    file so no signal is ever lost without a trace.
    """
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=8)
            r.raise_for_status()
            print(f"✅ Telegram sent at {datetime.now(IST).strftime('%H:%M:%S IST')}"
                  + (f" (attempt {attempt})" if attempt > 1 else ""))
            return True
        except Exception as e:
            if attempt < max_retries:
                print(f"⚠️ Telegram send failed (attempt {attempt}/{max_retries}): {e} "
                      f"— retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            else:
                print(f"❌ TELEGRAM SEND FAILED after {max_retries} attempts: {e}")
                print(f"❌ MISSED MESSAGE: {msg[:120]}...")
                try:
                    with open(os.path.expanduser("~/missed_telegram_signals.log"), "a") as f:
                        f.write(f"\n[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}] "
                                f"FAILED ({e}):\n{msg}\n{'-'*50}\n")
                except Exception:
                    pass
                return False

def ist_time():
    n  = datetime.now(IST)
    h  = n.hour; m = n.minute; s = n.second
    hh = str(h%12 or 12).zfill(2)
    return f"{hh}:{str(m).zfill(2)}:{str(s).zfill(2)} {'AM' if h<12 else 'PM'}"

def fmt_date(date_str=None):
    """Format date as 26th June 2026"""
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except:
            return date_str
    else:
        d = datetime.now(IST)
    
    day = d.day
    suffix = (
        "st" if day in [1,21,31] else
        "nd" if day in [2,22] else
        "rd" if day in [3,23] else
        "th"
    )
    months = ["Jan","Feb","Mar","Apr","May","Jun",
              "Jul","Aug","Sep","Oct","Nov","Dec"]
    return f"{day}{suffix} {months[d.month-1]} {d.year}"

def fmt_date_short(date_str=None):
    """Format date as 26/06/2026"""
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            return d.strftime("%d/%m/%Y")
        except:
            return date_str
    return datetime.now(IST).strftime("%d/%m/%Y")

# ══════════════════════════════════════════════════════════════
# FIX 2 — ERROR RECOVERY + MAIN LOOP
# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
# FIX 1 — TIME OF DAY FILTER
# ══════════════════════════════════════════════════════════════
def get_time_window(is_expiry_day=False):
    """
    Returns (window_name, signals_allowed, min_score)
    Extended to 3:10 PM for non-expiry days
    Expiry day hard stop at 2:00 PM
    """
    now = datetime.now(IST)
    t   = now.time()

    # ANALYSIS MODE — show all signals ≥50 across all windows
    MIN_SCORE = 50

    if is_expiry_day:
        if t < dtime(9, 15):    return "PRE_MARKET",    False, 0
        elif t < dtime(9, 30):  return "OPENING",        False, 0
        elif t < dtime(11, 0):  return "BEST_WINDOW",    True,  MIN_SCORE
        elif t < dtime(13, 0):  return "SLOW_ZONE",      True,  MIN_SCORE
        elif t < dtime(14, 0):  return "ACTIVE",         True,  MIN_SCORE
        elif t < dtime(14, 30): return "EXPIRY_CAUTION", False, 0
        else:                   return "EXPIRY_EXIT",    False, 0
    else:
        if t < dtime(9, 15):    return "PRE_MARKET",     False, 0
        elif t < dtime(9, 30):  return "OPENING",         False, 0
        elif t < dtime(11, 0):  return "BEST_WINDOW",     True,  MIN_SCORE
        elif t < dtime(13, 0):  return "SLOW_ZONE",       True,  MIN_SCORE
        elif t < dtime(14, 30): return "ACTIVE",          True,  MIN_SCORE
        elif t < dtime(15, 10): return "LATE_SESSION",    True,  MIN_SCORE
        else:                   return "EXIT_ALL",        False, 0

def should_send_in_window(results, window, min_score):
    """Only send if window allows and score meets minimum"""
    if min_score == 0:
        return False
    for sig in results.values():
        if sig and max(sig["ce_score"], sig["pe_score"]) >= min_score:
            return True
    return False

# ══════════════════════════════════════════════════════════════
# FIX 2 — EXIT ALERTS
# ══════════════════════════════════════════════════════════════
def send_book_profits_alert():
    """Send at 11:30 AM — book 50% profits"""
    msg = (
        "⏰ *11:30 AM — BOOK PROFITS NOW*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 Action required:\n"
        "✅ Book *50%* of any profitable position\n"
        "✅ Move SL to breakeven on remaining\n"
        "⚠️ No new entries after this\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "_OC Radar v7 · Educational only_"
    )
    send_telegram(msg)

def send_exit_all_alert(is_expiry=False):
    """Send at 2:30 PM — exit all positions"""
    if is_expiry:
        msg = (
            "🚨 *2:30 PM — EXIT ALL POSITIONS NOW*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ EXPIRY DAY — CRITICAL!\n"
            "🔴 Exit *ALL* positions immediately\n"
            "🔴 Theta destroying premiums fast\n"
            "🔴 Do NOT hold past 2:30 PM today\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "_OC Radar v7 · Educational only_"
        )
    else:
        msg = (
            "⏰ *2:30 PM — EXIT ALL POSITIONS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 Action required:\n"
            "🔴 Exit *ALL* remaining positions\n"
            "🔴 No new entries after 2:30 PM\n"
            "✅ Lock in your profits for today\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "_OC Radar v7 · Educational only_"
        )
    send_telegram(msg)

def send_closing_alert():
    """Send at 3:00 PM — final exit warning"""
    msg = (
        "🔔 *3:00 PM — FINAL EXIT WARNING*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⛔ Market closes in 30 min\n"
        "🔴 Exit everything NOW\n"
        "🔴 Last chance to avoid overnight risk\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "_OC Radar v7 · Educational only_"
    )
    send_telegram(msg)

# ══════════════════════════════════════════════════════════════
# FIX 3 — PARTIAL BOOKING ALERT
# ══════════════════════════════════════════════════════════════
def check_partial_booking(results):
    """
    Check if any active signal has hit +30% target
    Send partial booking alert
    """
    global active_trades

    alerts = []

    for name, sig in results.items():
        if not sig:
            continue

        # Get current LTP for the signal side
        if sig["action"] == "BUY CE":
            current_ltp = sig["ce_entry"]
            strike      = sig["ce_strike"]
            side        = "CE"
        elif sig["action"] == "BUY PE":
            current_ltp = sig["pe_entry"]
            strike      = sig["pe_strike"]
            side        = "PE"
        else:
            continue

        # Check if we have a tracked entry for this trade
        trade = active_trades.get(name)
        if not trade:
            # Auto-track based on current signal
            active_trades[name] = {
                "side":   side,
                "entry":  current_ltp,
                "strike": strike,
                "alerted_30": False,
                "alerted_40": False,
            }
            continue

        # Skip if side changed (new trade)
        if trade["side"] != side:
            active_trades[name] = {
                "side":   side,
                "entry":  current_ltp,
                "strike": strike,
                "alerted_30": False,
                "alerted_40": False,
            }
            continue

        entry      = trade["entry"]
        gain_pct   = (current_ltp - entry) / entry * 100 if entry else 0
        target_30  = round(entry * 1.30)
        target_40  = round(entry * 1.40)
        sl         = round(entry * 0.75)

        # +30% alert — partial booking
        if gain_pct >= 30 and not trade.get("alerted_30"):
            alerts.append({
                "name": name, "side": side, "strike": strike,
                "entry": entry, "current": current_ltp,
                "gain_pct": round(gain_pct, 1),
                "target_40": target_40, "sl": sl,
                "type": "PARTIAL",
            })
            active_trades[name]["alerted_30"] = True

        # +40% alert — full target hit
        elif gain_pct >= 40 and not trade.get("alerted_40"):
            alerts.append({
                "name": name, "side": side, "strike": strike,
                "entry": entry, "current": current_ltp,
                "gain_pct": round(gain_pct, 1),
                "type": "TARGET",
            })
            active_trades[name]["alerted_40"] = True

        # -25% SL alert
        elif gain_pct <= -25:
            alerts.append({
                "name": name, "side": side, "strike": strike,
                "entry": entry, "current": current_ltp,
                "gain_pct": round(gain_pct, 1),
                "type": "SL",
            })
            # Reset trade tracking after SL
            active_trades.pop(name, None)

    # Send alerts
    for a in alerts:
        if a["type"] == "PARTIAL":
            msg = (
                f"🎯 *+30% TARGET HIT — BOOK 50% NOW*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*{a['name']}* {a['side']} {a['strike']:,}\n"
                f"Entry ₹{a['entry']} → Now ₹{a['current']}\n"
                f"Gain: *+{a['gain_pct']}%* 🔥\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Book *50%* of position NOW\n"
                f"✅ Move SL to breakeven (₹{a['entry']})\n"
                f"🎯 Hold rest for ₹{a['target_40']} (+40%)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"_OC Radar v7 · Educational only_"
            )
        elif a["type"] == "TARGET":
            msg = (
                f"🏆 *+40% FULL TARGET HIT — EXIT ALL*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*{a['name']}* {a['side']} {a['strike']:,}\n"
                f"Entry ₹{a['entry']} → Now ₹{a['current']}\n"
                f"Gain: *+{a['gain_pct']}%* 🏆\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Exit *remaining 50%* NOW\n"
                f"✅ Lock in full profit\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"_OC Radar v7 · Educational only_"
            )
        else:  # SL
            msg = (
                f"🛑 *STOP LOSS HIT — EXIT NOW*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*{a['name']}* {a['side']} {a['strike']:,}\n"
                f"Entry ₹{a['entry']} → Now ₹{a['current']}\n"
                f"Loss: *{a['gain_pct']}%* 🔴\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔴 Exit *ALL* positions NOW\n"
                f"🔴 Do not average down\n"
                f"🔴 Wait for next fresh signal\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"_OC Radar v7 · Educational only_"
            )
        send_telegram(msg)

# ══════════════════════════════════════════════════════════════
# FIX 4 — PCR TREND TRACKING
# ══════════════════════════════════════════════════════════════
def track_pcr_trend(name, current_pcr):
    """
    Compare current PCR vs previous PCR
    Detect if PCR is rising (bullish) or falling (bearish)
    """
    global prev_pcr

    if name not in prev_pcr:
        prev_pcr[name] = current_pcr
        return "NEUTRAL", "First reading", 0

    old_pcr  = prev_pcr[name]
    chg      = round(current_pcr - old_pcr, 3)
    chg_pct  = round(chg / old_pcr * 100, 1) if old_pcr else 0

    # Update store
    prev_pcr[name] = current_pcr

    # Classify trend
    if chg >= 0.05:
        trend   = "RISING"
        meaning = f"PCR ↑ {old_pcr}→{current_pcr} (+{chg}) — Bullish strengthening"
        score   = 5   # Bonus to CE score
    elif chg <= -0.05:
        trend   = "FALLING"
        meaning = f"PCR ↓ {old_pcr}→{current_pcr} ({chg}) — Bearish strengthening"
        score   = -5  # Bonus to PE score
    elif chg > 0:
        trend   = "SLIGHTLY RISING"
        meaning = f"PCR {old_pcr}→{current_pcr} — Mildly bullish"
        score   = 2
    elif chg < 0:
        trend   = "SLIGHTLY FALLING"
        meaning = f"PCR {old_pcr}→{current_pcr} — Mildly bearish"
        score   = -2
    else:
        trend   = "STABLE"
        meaning = f"PCR {current_pcr} unchanged — No trend"
        score   = 0

    # PCR contrarian signal
    if current_pcr > 1.5:
        meaning += " ⚠️ CONTRARIAN: Oversold — bounce likely!"
        score   += 5  # Contrarian bullish
    elif current_pcr < 0.7:
        meaning += " ⚠️ CONTRARIAN: Overbought — fall likely!"
        score   -= 5  # Contrarian bearish

    return trend, meaning, score

def main():
    print("🚀 OC Radar Bot v7 started!")

    # Validate all secrets first
    if not validate_secrets():
        print("❌ Bot stopped — fix ~/.env file first")
        return

    send_telegram(
        "🚀 *OC Radar Bot v7 started!*\n"
        "✅ Auto Token via TOTP (10 sec daily!)\n"
        "✅ Time Filter · Exit Alerts · Partial Booking\n"
        "✅ Global Markets · OI Change · VIX · Futures\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Requesting TOTP now..."
    )

    open_alert_sent      = False
    eod_sent             = False
    book_profits_sent    = False
    exit_all_sent        = False
    closing_alert_sent   = False
    results              = {}
    daily_signals_count  = 0
    daily_trades         = []
    global token_generated_today, waiting_for_totp

    # Initialize Google Sheets
    print("📊 Connecting to Google Sheets...")
    sheets_ok = init_google_sheets()
    if sheets_ok:
        send_telegram(
            "📊 *Google Sheets connected!*\n"
            "Signals + Trades will auto-log ✅"
        )

    # Auto generate token on startup
    print("🔑 Auto-generating token on startup...")
    if not auto_generate_token():
        # Fallback to manual if auto fails
        request_totp_from_user()
        wc = 0
        while waiting_for_totp and wc < 24:
            check_telegram_updates()
            time.sleep(5)
            wc += 1

    # Initial expiry fetch
    print("📅 Fetching expiry dates...")
    expiries = refresh_expiries()
    current_expiries.update(expiries)

    # Auto-stop after 6 hours 8 min
    # Starts 9:05 AM → stops 3:05 PM IST
    # Pre-market 9:05-9:13 AM + Live 9:13-3:05 PM ✅
    start_time = time.time()
    MAX_RUNTIME_SECONDS = 6 * 60 * 60  # Exactly 6 hours → 9:13 AM to 3:13 PM IST

    # ── PRE-MARKET PHASE (9:05-9:13 AM) ──────────────────────
    now_ist = datetime.now(IST)
    t_now   = now_ist.time()

    if t_now < dtime(9, 15):  # Wait till market opens
        print("🌅 Pre-market phase starting...")

        # Fetch global markets
        print("  → Fetching global markets...")
        global_data = fetch_global_markets()

        # Fetch VIX early
        print("  → Fetching VIX...")
        vix, vix_chg, vix_chg_pct = fetch_vix()

        # Format pre-market message
        now_fmt  = fmt_date()
        lines    = []
        lines.append(f"🌅 *PRE-MARKET BRIEF · {now_fmt}*")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

        # Global markets
        if global_data:
            icons = {"GIFT Nifty":"🇮🇳","Dow Fut":"🇺🇸","Crude":"🛢️","USD/INR":"💵"}
            overall = 0
            for name, d in global_data.items():
                if not d: continue
                chg_p = d.get("chg_pct", 0)
                icon  = icons.get(name, "📊")
                color = "🟢" if chg_p > 0.3 else "🔴" if chg_p < -0.3 else "🟡"
                price = d.get("price", 0)
                if name == "GIFT Nifty":
                    chg = d.get("chg", 0)
                    lines.append(f"{icon} *{name}:* {price:,.0f} ({chg:+.0f} | {chg_p:+.2f}%) {color}")
                    if chg > 50:
                        lines.append(f"   → Gap UP expected at open 📈")
                    elif chg < -50:
                        lines.append(f"   → Gap DOWN expected at open 📉")
                    else:
                        lines.append(f"   → Flat opening expected ➡️")
                else:
                    lines.append(f"{icon} {name}: {price:,.2f} ({chg_p:+.2f}%) {color}")
                overall += chg_p if name != "USD/INR" else -chg_p

            bias = ("🟢 *BULLISH* — Positive for markets" if overall > 0.5 else
                    "🔴 *BEARISH* — Negative for markets" if overall < -0.5 else
                    "🟡 *NEUTRAL* — Mixed signals")
            lines.append(f"\n📊 Global Bias: {bias}")

        # VIX
        if vix > 0:
            lvl = ("🔵 VERY LOW" if vix < 11 else "🟢 LOW" if vix < 14 else
                   "🟡 MODERATE" if vix < 17 else "🟠 HIGH" if vix < 20 else "🔴 VERY HIGH")
            lines.append(f"\n🌡️ *VIX:* {vix} {lvl} ({vix_chg:+.2f} | {vix_chg_pct:+.2f}%)")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

        # Expiry warning
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        expiring  = [n for n, e in expiries.items() if e == today_str]
        if expiring:
            names = " + ".join(expiring)
            lines.append(f"⚡ *EXPIRY TODAY: {names}*")
            lines.append("⚠️ Reduce size 50% · Exit by 2:00 PM")
        else:
            # Show next expiries
            lines.append("📅 *Next Expiries:*")
            for n, e in expiries.items():
                lines.append(f"   {n}: {fmt_date(e)}")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⏰ *Wait till 9:30 AM to enter*")
        lines.append("❌ Do NOT trade 9:15–9:30 AM")
        lines.append("📡 First signal at 9:30 AM")
        lines.append("\n_OC Radar v7 · Educational only_")

        send_telegram("\n".join(lines))
        print("✅ Pre-market brief sent!")

        # Wait till 9:13 AM
        while datetime.now(IST).time() < dtime(9, 15):
            check_telegram_updates()
            time.sleep(10)

    print("🚀 Live market phase starting...")

    while True:
        # Check runtime limit
        elapsed = time.time() - start_time
        if elapsed >= MAX_RUNTIME_SECONDS:
            print("⏰ 2 hour limit reached — stopping gracefully")
            send_telegram(
                "⏸ *Session handoff*\n"
                "Next session starts in 5 min automatically\n"
                "Signals continue uninterrupted! 📡"
            )
            break
        try:
            now   = datetime.now(IST)
            today = now.strftime("%Y-%m-%d")
            t     = now.time()

            # Refresh expiries daily at midnight
            if t < dtime(0, 5):
                print("📅 Refreshing expiry dates...")
                expiries = refresh_expiries()

            # Check if trading day
            trading, reason = is_trading_day()
            if not trading:
                if t == dtime(8, 0):  # Send once at 8 AM on holidays
                    send_telegram(
                        f"🏖️ *Market Closed Today*\n"
                        f"Reason: {reason}\n"
                        f"Date: {fmt_date()}\n"
                        "_OC Radar v4_"
                    )
                print(f"⏰ {reason} — {now.strftime('%H:%M IST')} — waiting...")
                time.sleep(60)
                continue

            # Auto generate token at startup (9:13 AM daily)
            if dtime(9, 13) <= t <= dtime(9, 17) and not token_generated_today:
                print("🔑 Auto-generating daily token at 8:55 AM...")
                if not auto_generate_token():
                    # Fallback to manual
                    request_totp_from_user()
                    wc = 0
                    while waiting_for_totp and wc < 24:
                        check_telegram_updates()
                        time.sleep(5)
                        wc += 1

            # Market open alert at 9:10 AM
            if dtime(9, 10) <= t <= dtime(9, 14) and not open_alert_sent:
                print("🌍 Fetching global markets for open alert...")
                global_data = fetch_global_markets()
                send_market_open_alert(expiries, global_data)
                check_expiry_warning(expiries)
                open_alert_sent    = True
                eod_sent           = False
                book_profits_sent  = False
                exit_all_sent      = False
                closing_alert_sent = False

            # FIX 2 — Book profits alert at 11:30 AM
            if dtime(11, 30) <= t <= dtime(11, 34) and not book_profits_sent:
                send_book_profits_alert()
                book_profits_sent = True
                print("⏰ Book profits alert sent")

            # FIX 2 — Exit all alert at 2:30 PM
            today_str    = datetime.now(IST).strftime("%Y-%m-%d")
            is_expiry_day = any(e == today_str for e in expiries.values())
            # Exit alert — 2:00 PM on expiry, 3:10 PM on normal day
            exit_time = dtime(14, 30) if is_expiry_day else dtime(15, 10)
            exit_time_end = dtime(14, 34) if is_expiry_day else dtime(15, 14)
            if exit_time <= t <= exit_time_end and not exit_all_sent:
                send_exit_all_alert(is_expiry=is_expiry_day)
                exit_all_sent = True
                print("⏰ Exit all alert sent")

            # FIX 2 — Closing alert at 3:00 PM
            if dtime(15, 0) <= t <= dtime(15, 4) and not closing_alert_sent:
                send_closing_alert()
                closing_alert_sent = True
                print("⏰ Closing alert sent")

            # EOD summary at 3:31 PM
            if dtime(15, 10) <= t <= dtime(15, 14) and not eod_sent:
                if results:
                    send_eod_summary(results, expiries)
                # Auto-import trades from Dhan
                print("📥 Auto-importing trades from Dhan...")
                imported = auto_import_trades_from_dhan()
                # Log EOD P&L to Google Sheets
                log_eod_pnl_to_sheet(results, daily_signals_count, daily_trades)
                eod_sent           = True
                open_alert_sent    = False
                daily_signals_count = 0
                daily_trades        = []

            # Reset daily flags at midnight
            if t < dtime(0, 1):
                open_alert_sent    = False
                eod_sent           = False
                book_profits_sent  = False
                exit_all_sent      = False
                closing_alert_sent = False

            if not is_market_open():
                print(f"⏰ Market closed — {now.strftime('%H:%M IST')} — waiting...")
                time.sleep(60)
                continue

            # Skip if no token yet
            if not DHAN_API_KEY:
                print("⏳ Waiting for TOTP token...")
                check_telegram_updates()
                time.sleep(10)
                continue

            # Reset daily token flag at midnight
            if t < dtime(0, 1):
                token_generated_today = False

            # FIX 1 — Time of day filter
            window, window_ok, min_score = get_time_window(is_expiry_day)
            print(f"  ⏰ Window: {window}")

            # ── MAIN FETCH CYCLE ──────────────────────────────
            ping = ist_time()
            print(f"\n🔄 Fetching at {ping} IST...")

            # VIX
            print("  → VIX...")
            vix_data = fetch_vix()
            vix, vc, vcp = vix_data
            _, _, vix_adj = analyze_vix(vix, vc, vcp)

            # Futures
            print("  → Futures...")
            fut_raw      = fetch_futures()
            fut_analysis = analyze_futures(fut_raw)

            # Global (only at open + every 30 min)
            if t.minute % 30 == 0:
                print("  → Global markets...")
                global_data = fetch_global_markets()
            else:
                global_data = {}

            # Option chains
            results     = {}
            buildups    = {}
            top_chg_all = {}
            pcr_trends  = {}  # Fix 4 — PCR trend per index

            for name, cfg in INDICES_BASE.items():
                print(f"  → {name}...")
                try:
                    raw        = fetch_option_chain(cfg["scrip"], cfg["seg"], expiries.get(name,""))
                    spot, data = parse_oc(raw)
                    if not data:
                        results[name] = None; continue

                    oi_chgs, ce_a, pe_a  = compute_oi_changes(name, spot, data)
                    pat, meaning, bscore = detect_buildup(name, spot, ce_a, pe_a)
                    buildups[name]       = (pat, meaning, bscore)
                    top_chg_all[name]    = top_oi_changes(oi_chgs, spot)
                    fut_adj              = fut_analysis.get(name, {}).get("score_adj", 0)

                    # Fix 4 — PCR trend
                    near = [d for d in data if abs(d["strike"]-spot)/spot <= 0.02]
                    tc   = sum(d["ceOI"] for d in near)
                    tp   = sum(d["peOI"] for d in near)
                    curr_pcr = round(tp/tc, 2) if tc else 1.0
                    pcr_trend, pcr_meaning, pcr_adj = track_pcr_trend(name, curr_pcr)
                    pcr_trends[name] = (pcr_trend, pcr_meaning)

                    sig = compute_signals(
                        name, spot, data, ce_a, pe_a,
                        bscore + vix_adj + fut_adj + pcr_adj,
                        vix=vix, is_expiry=is_expiry_day
                    )
                    results[name] = sig

                except Exception as e:
                    print(f"  ❌ {name} error: {e}")
                    results[name] = None

            resp = ist_time()

            # Fix 3 — Check partial booking alerts first (always, regardless of window)
            check_partial_booking(results)

            # Fix 1 + Fix 3 — Time filter before sending signal
            if not window_ok:
                print(f"⏸ Signal skipped — {window} window")
            else:
                # Change filter
                should, reason = should_send(results)

                # Score threshold filter for current window
                send_ok = should_send_in_window(results, window, min_score)
                if not send_ok:
                    print(f"⏸ {window} — no signal meeting score ≥{min_score}")
                    should = False

                # Late session — override targets and size
                is_late = window == "LATE_SESSION"

                if should:
                    msg = format_message(
                        results, buildups, top_chg_all,
                        ping, resp, vix_data, fut_analysis,
                        global_data, expiries, pcr_trends, window,
                        is_late=is_late
                    )
                    # Print to console always (Telegram may be blocked)
                    print("\n" + "="*50)
                    print(msg)
                    print("="*50 + "\n")
                    send_telegram(msg)
                    update_prev_scores(results)
                    print(f"📨 Sent — {reason} — Window: {window}")

                    # Auto-log signals to Google Sheets
                    for name, sig in results.items():
                        if sig and sig.get("action") != "SKIP":
                            log_signal_to_sheet(
                                name, sig, ping, resp,
                                window, pcr_trends
                            )
                    daily_signals_count += 1
                else:
                    print(f"⏸ Skipped — {reason}")

        except Exception as e:
            # FIX 2 — Error recovery — never crash
            print(f"❌ Loop error: {e} — recovering in 60s...")
            try:
                send_telegram(f"⚠️ Bot error: {e}\nRecovering automatically...")
            except:
                pass
            time.sleep(60)
            continue

        print(f"⏳ Next check in {REFRESH_MINUTES} min...")
        # Phase 2 — Check for user commands every 10 seconds during wait
        for _ in range(REFRESH_MINUTES * 6):
            check_telegram_updates()
            time.sleep(10)

if __name__ == "__main__":
    main()