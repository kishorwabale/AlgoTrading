# OC Radar Bot v7

A fully-automated Options Chain (OC) analysis and trading signal bot for Indian equity derivatives (NIFTY, BANKNIFTY, SENSEX). Sends live buy/sell signals via Telegram, places Dhan Super Orders with one tap, and auto-logs every signal and trade to Google Sheets.

---

## Features

| Feature | Detail |
|---|---|
| **Auto Token** | Generates Dhan access token itself using TOTP secret — zero manual work daily |
| **Signal Engine** | 4-band scoring system (SKIP / WATCH / CAUTION / TRADE) per index |
| **One-Tap Orders** | Places Dhan Super Orders (entry + target + SL) from a single Telegram command |
| **Google Sheets** | Auto-logs signals, trades, and daily P&L to three dedicated sheets |
| **Risk Controls** | Time-window filter, score thresholds, partial booking alerts (+30%/+40%), SL alert (-25%) |
| **Market Context** | VIX, Futures basis, GIFT Nifty, Dow Futures, Crude Oil, USD/INR pre-market brief |
| **OI Analysis** | OI change detection, buildup patterns (Long Buildup / Short Cover / Short Buildup / Long Unwind) |
| **PCR Trend** | Put-Call Ratio trend tracking with contrarian signals |
| **Holiday-aware** | NSE 2026 holiday list built-in; expiry auto-detected via Dhan API |

---

## Architecture Overview

```
main()
 ├─ Startup
 │   ├─ Load ~/.env secrets
 │   ├─ Auto-generate TOTP token (Dhan auth)
 │   ├─ Connect Google Sheets
 │   └─ Fetch option expiry dates
 │
 ├─ Pre-Market Phase (before 9:15 AM)
 │   ├─ Global markets brief (GIFT Nifty, Dow, Crude, USD/INR)
 │   ├─ VIX level
 │   └─ Expiry warning if today is expiry
 │
 └─ Live Market Loop (every 5 min)
     ├─ Scheduled alerts (open / book profits / exit all / closing)
     ├─ VIX + Futures fetch
     ├─ Option chain fetch per index (Dhan API)
     ├─ OI change + buildup detection
     ├─ PCR trend tracking
     ├─ Signal scoring (CE + PE scores)
     ├─ Time-window + score filter
     ├─ Partial booking checks (always runs)
     ├─ Telegram message send (if changed significantly)
     └─ Google Sheets signal log
```

---

## Signal Scoring

Each index gets a **CE Score** and a **PE Score** (0–100) computed from:

| Factor | Max Points | Notes |
|---|---|---|
| PCR (Put-Call Ratio) | 25 | High PCR → CE bullish; low PCR → PE bearish |
| OI Ratio | 18 | PE OI dominance → bullish; CE OI dominance → bearish |
| Max Pain distance | 20 | Spot above Max Pain → CE; spot below → PE |
| Put/Call Wall proximity | 12 | Wall far below/above spot favors that side |
| Call Wall distance | 10 | Far CW = room for upside |
| OICR (OI concentration ratio) | 15 | Low OICR → breakout mode |
| Straddle premium % | 8 | Higher premium → larger expected move |
| IV Skew | 8 | Put IV > Call IV → bearish skew |
| Bonus (OI buildup + VIX + Futures + PCR trend) | ±15 | Composite adjustment |

### 4-Band System

| Score | Band | Action |
|---|---|---|
| 0–49 | 🔴 SKIP | Silent — not shown |
| 50–64 | 🟡 WATCH | Shown on Telegram, no order button |
| 65–74 | 🟠 CAUTION | Shown + `/buy` at 50% size |
| 75–100 | 🟢 TRADE | Full signal + `/buy` full size |

---

## Time Windows

| Window | Hours (IST) | Min Score | Notes |
|---|---|---|---|
| PRE_MARKET | Before 9:15 | — | No signals |
| OPENING | 9:15–9:30 | — | No signals; avoid first 15 min |
| BEST_WINDOW | 9:30–11:00 | 75 | Best entry zone |
| SLOW_ZONE | 11:00–13:00 | 80 | Higher threshold |
| ACTIVE | 13:00–14:30 | 75 | Afternoon zone |
| LATE_SESSION | 14:30–15:10 | 85 | Tight targets: +20% / -15% |
| EXIT_ALL | After 15:10 | — | No new entries |

On expiry days, hard stop at 14:00 with `EXPIRY_CAUTION` → `EXPIRY_EXIT`.

---

## Scheduled Alerts

| Time (IST) | Alert |
|---|---|
| 9:05 AM | Pre-market brief with global markets |
| 9:10 AM | Market opens in 5 min + expiry warning |
| 9:13 AM | Daily TOTP auto-generation |
| 11:30 AM | Book 50% profits alert |
| 14:30 PM | Exit all positions (15:10 on normal days) |
| 15:00 PM | Final exit warning |
| 15:10 PM | EOD summary + auto-import trades from Dhan |

---

## Setup

### Prerequisites

```bash
pip install requests pytz gspread google-auth
```

### 1. Create `~/.env`

```
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
DHAN_CLIENT_ID=your_dhan_client_id
DHAN_PIN=your_dhan_pin
DHAN_TOTP_SECRET=your_base32_totp_secret
GOOGLE_SHEET_ID=your_spreadsheet_id
GOOGLE_SHEETS_CREDENTIALS={"type":"service_account",...}  # full JSON on one line
```

**DHAN_TOTP_SECRET** is the base32 secret from the QR code shown during Dhan 2FA setup (same secret used by Google Authenticator). The bot generates 6-digit codes using RFC 6238 — no external library required.

**GOOGLE_SHEETS_CREDENTIALS** is the service account JSON from Google Cloud Console, pasted as a single-line string.

### 2. Google Sheets Setup

1. Create a Google Sheets spreadsheet.
2. Create a Google Cloud service account with Sheets + Drive API access.
3. Share the spreadsheet with the service account email.
4. Copy the spreadsheet ID into `GOOGLE_SHEET_ID`.

The bot auto-creates three worksheets on first run:
- `⏱ Signal Log` — every signal sent
- `📓 Trade Journal` — individual trades
- `💰 Daily P&L` — end-of-day summary

### 3. Dhan API

- Enable API access in your Dhan account.
- Whitelist your server IP in the Dhan developer portal.
- The bot auto-generates the access token at startup and again at 9:13 AM daily.

### 4. Run

```bash
python oc_radar_bot.py
```

The bot runs for up to 6 hours per session (9:13 AM – 3:13 PM IST), then exits cleanly.

---

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Show welcome message and command list |
| `/status` | Show bot status and pending orders |
| `/totp` | Request manual TOTP entry (fallback if auto-generation fails) |
| `/buy_{INDEX}_{SIDE}_{STRIKE}_{PRICE}_{FLAG}` | Initiate order confirmation |
| `/confirm_{INDEX}_{SIDE}_{STRIKE}` | Confirm and place the Super Order |
| `/cancel` | Cancel all pending orders |

**Example:** `/buy_NIFTY_CE_24500_35000_N` → shows confirmation → `/confirm_NIFTY_CE_24500` → places order.

---

## Order Placement (Dhan Super Order)

Placing a Super Order sets entry, target, and stop-loss in a single API call:

| Session | Target | Stop Loss |
|---|---|---|
| Normal | +30% | -25% |
| Late (after 14:30) | +20% | -15% |

If the target hits, the SL is auto-cancelled by Dhan. If the SL hits, the target is auto-cancelled.

---

## Indices Supported

| Index | Exchange | Lot Size | Expiry |
|---|---|---|---|
| NIFTY | NSE F&O | 65 | Weekly Tuesday |
| BANKNIFTY | NSE F&O | 30 | Monthly (last Tuesday) |
| SENSEX | BSE F&O | 20 | Weekly Thursday |

Expiry dates are fetched live from the Dhan API. A fallback calculation is used if the API is unavailable.

---

## Configuration Constants

| Variable | Default | Description |
|---|---|---|
| `REFRESH_MINUTES` | 5 | Polling interval during market hours |
| `SCORE_CHANGE_THRESHOLD` | 5 | Minimum score change to re-send a signal |
| `SPOT_CHANGE_THRESHOLD` | 0.3% | Minimum spot move to re-send a signal |
| `MAX_RUNTIME_SECONDS` | 6 hours | Auto-stop after this many seconds |

---

## Data Sources

| Data | Source |
|---|---|
| Option chains | Dhan API (`/v2/optionchain`) |
| Expiry dates | Dhan API (`/v2/optionchain/expirylist`) |
| India VIX | NSE India API (`/api/allIndices`) |
| NIFTY / BANKNIFTY Futures | NSE India (`/api/quote-derivative`) |
| SENSEX Futures | BSE India API |
| GIFT Nifty, Dow, Crude, USD/INR | Yahoo Finance |

---

## Google Sheets Schema

### Signal Log
`Date · Ping Time · Response Time · Index · Signal · Strike · Score · OICR · PCR · OI Pattern · VIX · Futures Basis · Composite · Action · Window · Notes`

### Trade Journal
`Date · Day · Index · CE/PE · Strike · Entry Time · Entry Price · Lots · Capital · Exit Time · Exit Price · P&L (₹) · P&L (%) · Hold Time · Score · OICR · Pattern · Result · Order ID · Notes`

### Daily P&L
`Date · Day · Signals · Trades · Winners · Losers · Win Rate · Gross P&L · Brokerage · Net P&L · Notes`

---

## Disclaimer

*OC Radar v7 is for educational and informational purposes only. Options trading involves substantial risk of loss. Past signals do not guarantee future results. Always verify signals independently before trading.*
