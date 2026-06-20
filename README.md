# AlgoTrading — NSE F&O Options Algo

An automated intraday options trading algo built on the [Dhan API](https://dhanhq.co/).  
It screens all NSE F&O stocks for movers in a tight % band, buys ATM options via limit orders, and auto-exits on target, stop-loss, or end of day.

## How it works

```
09:20 AM  →  Screener fetches live quotes for all NSE F&O stocks
          →  Filters stocks whose % move falls in [MIN_PCT_CHANGE, MAX_PCT_CHANGE]
          →  Selects the stock closest to the ceiling (nearest to ±2%)
          →  Prints selected GAINER and LOSER to console before any order
          →  Buys ATM CALL (CE) for gainer, ATM PUT (PE) for loser
               — skips contracts with OI below MIN_OPTION_OI (liquidity gate)
               — places LIMIT order at option LTP + LIMIT_PRICE_BUFFER_PCT
          →  WebSocket monitor watches live P&L per position
          →  Auto-exits each leg when TARGET or SL is hit
15:15 PM  →  Force-exits any remaining open positions (before broker auto-SQ at 15:20)
```

> If the script is started after `RUN_TIME` it exits immediately without placing any orders.

## Project structure

```
AlgoTrading/
├── main.py        # Entry point — orchestrates screener → orders → monitor
├── screener.py    # Fetches live quotes for all NSE FNO stocks, ranks movers
├── monitor.py     # WebSocket position monitor; auto-exits on TARGET / SL / EOD
├── config.py      # All tunable settings (edit this to change strategy)
└── keys.py        # API credentials — NOT committed (gitignored)
```

## Setup

### 1. Install dependencies

```bash
pip install requests pandas dhanhq
```

### 2. Create `keys.py`

```python
# keys.py
CLIENT_ID    = "your_dhan_client_id"
ACCESS_TOKEN = "your_dhan_access_token"
```

Get your credentials from the [Dhan developer portal](https://developer.dhan.co/).  
`keys.py` is gitignored — never commit your tokens.

### 3. Configure the strategy

Open [config.py](config.py) and adjust the settings:

#### Timing

| Setting | Default | Description |
|---|---|---|
| `RUN_TIME` | `"09:20"` | Time to run (24h, local). Script exits without trading if started after this. |
| `WAIT_FOR_RUN_TIME` | `True` | Sleep until `RUN_TIME`; set `False` to run immediately (testing only) |
| `EOD_EXIT_TIME` | `"15:15"` | Force-exit all open positions at this time to avoid broker auto-square-off |

#### Stock selection

| Setting | Default | Description |
|---|---|---|
| `SIDE` | `"both"` | `"gainers"`, `"losers"`, or `"both"` |
| `MIN_PCT_CHANGE` | `1.5` | Floor — stocks that moved less than this % are ignored |
| `MAX_PCT_CHANGE` | `2.0` | Ceiling — stocks that moved more than this % are ignored |
| `NUM_GAINERS` | `1` | How many gainer stocks to trade |
| `NUM_LOSERS` | `1` | How many loser stocks to trade |

> Within the `[MIN, MAX]` band, the stock whose % change is **closest to the ceiling** is selected.  
> Example: with band 1.5–2%, a stock at 1.98% is preferred over one at 1.7%.

#### Order settings

| Setting | Default | Description |
|---|---|---|
| `LOTS` | `1` | Lots per trade |
| `EXPIRY_INDEX` | `0` | `0` = nearest expiry, `1` = next, etc. |
| `PRODUCT_TYPE` | `"INTRADAY"` | `"INTRADAY"` (MIS) or `"MARGIN"` (NRML) |
| `ORDER_TYPE` | `"LIMIT"` | `"LIMIT"` recommended; `"MARKET"` risks wide spread at open |
| `LIMIT_PRICE_BUFFER_PCT` | `2.0` | Limit price = option LTP × (1 + this %). Ensures fill while capping slippage. |
| `MIN_OPTION_OI` | `500` | Minimum open interest on the ATM contract. Skips illiquid options. |

#### Risk

| Setting | Default | Description |
|---|---|---|
| `TARGET_PER_TRADE` | `2000` | Exit when profit reaches ₹ this amount |
| `SL_PER_TRADE` | `2000` | Exit when loss reaches ₹ this amount |
| `DRY_RUN` | `False` | `True` = simulate only, no real orders placed |

### 4. Run

```bash
python main.py
```

Stop at any time with `Ctrl+C`.

## Console output before orders

After the screener runs, the algo prints the selected stocks clearly before placing any order:

```
=================================================================
  STOCKS SELECTED FOR TRADING
=================================================================
  GAINER  PREMIERENE       +1.98%  LTP ₹312.50
  LOSER   GMRAIRPORT       -1.85%  LTP ₹87.30
=================================================================
```

## Running the screener standalone

To view top 20 F&O gainers and losers without placing any orders:

```bash
python screener.py
```

## Dry run mode

Set `DRY_RUN = True` in [config.py](config.py) to simulate the full flow — screener, order lookup, P&L monitoring — without placing any real orders on your account.

## Security

- **Never commit `keys.py`** — it is gitignored by default.
- Access tokens are short-lived JWTs. Rotate them immediately if exposed.
- The scrip master CSV is also gitignored (auto-downloaded and cached locally).
