# OC Radar Bot v10

A fully-automated Options Chain (OC) analysis and trading signal bot for Indian equity derivatives (NIFTY, BANKNIFTY, SENSEX). Sends live buy/sell signals via Telegram, can place Dhan Super Orders with one tap, and auto-logs every signal and trade to Google Sheets.

---

## Features

| Feature | Detail |
|---|---|
| **Auto Token** | Generates Dhan access token itself using TOTP secret - zero manual work daily |
| **Signal Engine** | 4-band scoring system (SKIP / WATCH / CAUTION / TRADE) per index |
| **Market Regime Detection** | Classifies each cycle into one of 7 regimes (EXPIRY, VOLATILE_TREND, TRENDING, RANGE_NEUTRAL, RANGE_BIASED, HIGH_VIX, NORMAL) and adjusts signal weights accordingly |
| **Dynamic Target/SL** | Target and stop-loss are sized per regime instead of a flat percentage - see "Order Placement" below |
| **One-Tap Orders** | Places Dhan Super Orders (entry + target + SL) from a single Telegram command (requires a SEBI-whitelisted static IP - see note below) |
| **Google Sheets** | Auto-logs signals, trades, and daily P&L to three dedicated sheets |
| **Risk Controls** | Time-window filter, score thresholds, partial booking alerts, SL alerts |
| **Market Context** | VIX, Futures basis, GIFT Nifty, Dow Futures, Crude Oil, USD/INR pre-market brief |
| **OI Analysis** | OI change detection, buildup patterns (Long Buildup / Short Cover / Short Buildup / Long Unwind) |
| **PCR Trend** | Put-Call Ratio trend tracking with contrarian signals |
| **Holiday-aware** | NSE 2026 holiday list built-in; expiry auto-detected via Dhan API |

---

## Architecture Overview

```
main()
 |- Startup
 |   |- Load ~/.env secrets
 |   |- Auto-generate TOTP token (Dhan auth)
 |   |- Connect Google Sheets (optional - see GOOGLE_SHEETS_ENABLED flag)
 |   `- Fetch option expiry dates
 |
 |- Pre-Market Phase (before 9:15 AM)
 |   |- Global markets brief (GIFT Nifty, Dow, Crude, USD/INR)
 |   |- VIX level
 |   `- Expiry warning if today is expiry
 |
 `- Live Market Loop (every 5 min)
     |- Scheduled alerts (open / book profits / exit all / closing)
     |- VIX + Futures fetch
     |- Option chain fetch per index (Dhan API)
     |- OI change + buildup detection
     |- PCR trend tracking
     |- Market regime detection + VIX multiplier
     |- Signal scoring (CE + PE scores)
     |- Time-window + score filter
     |- Regime-aware target/SL calculation
     |- Telegram message send (if changed significantly)
     `- Google Sheets signal log
```

### Deployment

Runs on **GitHub Actions** (`.github/workflows/oc_radar_bot.yml`), scheduled 9:05 AM - 3:05 PM IST on weekdays, with a manual `workflow_dispatch` trigger available anytime. The workflow has a `concurrency` group with `cancel-in-progress: true` - this ensures a new run automatically cancels any previous run still active, preventing two overlapping bot instances from invalidating each other's Dhan session token (this happened once and silently blinded a run for the rest of a session - see "Known Issues Fixed" below).

**Important:** GitHub Actions runners use dynamic IPs, so live order placement is not possible from Actions runs (Dhan/SEBI requires a whitelisted static IP). The Actions deployment sends Telegram signals only. Actual order placement currently happens manually from a machine with a SEBI-whitelisted static IP.

---

## Signal Scoring

Each index gets a **CE Score** and a **PE Score** (0-100) computed from:

| Factor | Max Points | Notes |
|---|---|---|
| PCR (Put-Call Ratio) | 25 | High PCR -> CE bullish; low PCR -> PE bearish |
| OI Ratio | 18 | PE OI dominance -> bullish; CE OI dominance -> bearish |
| Max Pain distance | 20 | Spot above Max Pain -> CE; spot below -> PE |
| Put/Call Wall proximity | 12 | Wall far below/above spot favors that side |
| Call Wall distance | 10 | Far CW = room for upside |
| OICR (OI concentration ratio) | 15 | Low OICR -> breakout mode |
| Straddle premium % | 8 | Higher premium -> larger expected move |
| IV Skew | 8 | Put IV > Call IV -> bearish skew |
| Bonus (OI buildup + VIX + Futures + PCR trend) | +/-15 | Composite adjustment |

Weights above are further multiplied by the detected market regime's weight table (`REGIME_WEIGHTS`) and a VIX-based multiplier (`get_vix_multiplier`) before the final score is produced.

### 4-Band System

| Score | Band | Action |
|---|---|---|
| 0-49 | SKIP | Silent - not shown |
| 50-64 | WATCH | Shown on Telegram, no order button |
| 65-74 | CAUTION | Shown + `/buy` at 50% size |
| 75-100 | TRADE | Full signal + `/buy` full size |

---

## Market Regime Detection

Each cycle, `detect_market_regime()` classifies conditions using OICR, VIX, expiry status, PCR, and max-pain distance into one of:

| Regime | Trigger | Description |
|---|---|---|
| EXPIRY | Today is expiry | Max Pain dominates |
| VOLATILE_TREND | OICR < 35 and VIX > 14 | Strongest OI-driven trend signals |
| TRENDING | OICR < 45 | OI buildup leads |
| RANGE_NEUTRAL | OICR > 65 and PCR close to 1.0 | Max Pain dominates, tight range |
| RANGE_BIASED | OICR > 65, PCR skewed | Range with directional bias |
| HIGH_VIX | VIX > 18 | High fear, PE signals stronger |
| NORMAL | None of the above | Balanced default weights |

The regime label is shown on every alert (`Band: <REGIME> regime`) and drives both signal weighting and the target/SL band (see below).

---

## Time Windows

| Window | Hours (IST) | Min Score | Notes |
|---|---|---|---|
| PRE_MARKET | Before 9:15 | - | No signals |
| OPENING | 9:15-9:30 | - | No signals; avoid first 15 min |
| BEST_WINDOW | 9:30-11:00 | 75 | Best entry zone |
| SLOW_ZONE | 11:00-13:00 | 80 | Higher threshold |
| ACTIVE | 13:00-14:30 | 75 | Afternoon zone |
| LATE_SESSION | 14:30-15:10 | 85 | Tighter target/SL band applied |
| EXIT_ALL | After 15:10 | - | No new entries |

On expiry days, hard stop at 14:00 with `EXPIRY_CAUTION` -> `EXPIRY_EXIT`.

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

### 1. Create `~/.env` (or set as GitHub Actions secrets)

```
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
DHAN_CLIENT_ID=your_dhan_client_id
DHAN_PIN=your_dhan_pin
DHAN_TOTP_SECRET=your_base32_totp_secret
GOOGLE_SHEET_ID=your_spreadsheet_id
GOOGLE_SHEETS_CREDENTIALS_B64=base64_encoded_service_account_json
```

**DHAN_TOTP_SECRET** is the base32 secret from the QR code shown during Dhan 2FA setup (same secret used by Google Authenticator). The bot generates 6-digit codes using RFC 6238 - no external library required.

**GOOGLE_SHEETS_CREDENTIALS_B64** is the service account JSON from Google Cloud Console, base64-encoded before being stored as a secret. This is the recommended format - a raw multi-line JSON secret gets truncated when written into a single-line `.env` file, which silently breaks Sheets logging. To generate it:

```
certutil -encode credentials.json creds_b64.txt   # Windows
```

Strip the `-----BEGIN/END-----` lines from the output before pasting into the secret. The old raw `GOOGLE_SHEETS_CREDENTIALS` variable is still supported as a fallback if the base64 version isn't set, but is not recommended.

Google Sheets logging can be toggled off entirely for testing via the `GOOGLE_SHEETS_ENABLED` flag near the top of the script - useful for isolating whether an issue is signal-related or Sheets-related.

### 2. Google Sheets Setup

1. Create a Google Sheets spreadsheet.
2. Create a Google Cloud service account with Sheets + Drive API access.
3. Share the spreadsheet with the service account email.
4. Copy the spreadsheet ID into `GOOGLE_SHEET_ID`.
5. Base64-encode the service account JSON as described above.

The bot auto-creates three worksheets on first run:
- `Signal Log` - every signal sent
- `Trade Journal` - individual trades
- `Daily P&L` - end-of-day summary

### 3. Dhan API

- Enable API access in your Dhan account.
- Whitelist your server's static IP in the Dhan developer portal (required for order placement, not for reading signals).
- The bot auto-generates the access token at startup and again at 9:13 AM daily.

### 4. Run

**Locally:**
```bash
python oc_radar_bot.py
```

**Via GitHub Actions (recommended):** push `.github/workflows/oc_radar_bot.yml` and set the secrets listed above in the repo's Settings -> Secrets. The workflow runs automatically on the configured schedule, or can be triggered manually via `workflow_dispatch`.

The bot runs for up to ~6 hours per session, then exits cleanly (or is cut off by the Actions `timeout-minutes` setting, currently 355 minutes / 5h55m).

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

**Example:** `/buy_NIFTY_CE_24500_35000_N` -> shows confirmation -> `/confirm_NIFTY_CE_24500` -> places order.

---

## Order Placement (Dhan Super Order)

Placing a Super Order sets entry, target, and stop-loss in a single API call. Target and SL are now sized dynamically by the detected market regime rather than a flat percentage, since a fixed +30%/-25% band rarely triggers on typical low-volatility sessions:

| Regime | Target | Stop Loss |
|---|---|---|
| RANGE_NEUTRAL | +15% | -12% |
| RANGE_BIASED / EXPIRY | +18% | -15% |
| NORMAL | +22% | -18% |
| TRENDING | +25% | -20% |
| HIGH_VIX | +30% | -22% |
| VOLATILE_TREND | +35% | -25% |

Late session (after 14:30) further tightens whichever band applies by roughly 35-40%, since there's less time remaining for the move to play out.

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
| `GOOGLE_SHEETS_ENABLED` | True | Toggle Sheets logging on/off without touching credentials |
| `REGIME_TARGET_SL` | see table above | Per-regime (target_pct, sl_pct) lookup |

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

**Technical note on option chain format:** Dhan's actual `/v2/optionchain` response returns the `oc` field as a dictionary keyed by strike price (e.g. `{"23900.000000": {"ce": {...}, "pe": {...}}}`), not a list of row objects. The parser handles this format directly, with a fallback path for a legacy list-based format in case the API response shape changes again.

---

## Google Sheets Schema

### Signal Log
`Date | Ping Time | Response Time | Index | Signal | Strike | Score | OICR | PCR | OI Pattern | VIX | Futures Basis | Composite | Action | Window | Notes`

### Trade Journal
`Date | Day | Index | CE/PE | Strike | Entry Time | Entry Price | Lots | Capital | Exit Time | Exit Price | P&L (Rs) | P&L (%) | Hold Time | Score | OICR | Pattern | Result | Order ID | Notes`

### Daily P&L
`Date | Day | Signals | Trades | Winners | Losers | Win Rate | Gross P&L | Brokerage | Net P&L | Notes`

---

## Known Issues Fixed (1st July 2026)

- **Option chain parsing bug:** the parser previously assumed a list-based `oc` format that Dhan's API does not actually return, silently producing zero option data on every run regardless of API success. Fixed to handle Dhan's real dict-of-strikes format.
- **Dual-run token collision:** overlapping workflow runs could invalidate each other's Dhan session token mid-session with no loud error, leaving one run silently blind for the rest of its life. Fixed with a `concurrency` group in the workflow.
- **Flat target/SL band:** a fixed +30%/-25% band rarely triggers on typical intraday ranges. Replaced with the regime-based table above.
- **Google Sheets credential parsing:** a multi-line service account JSON secret got truncated by `.env` line-based parsing. Fixed by supporting a base64-encoded secret variable.

---

## Disclaimer

*OC Radar v10 is for educational and informational purposes only. Options trading involves substantial risk of loss. Past signals do not guarantee future results. Always verify signals independently before trading.*
