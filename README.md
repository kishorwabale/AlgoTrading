# AlgoTrading — NSE F&O Options Algo

An automated intraday options trading algo built on the [Dhan API](https://dhanhq.co/).  
It screens all NSE F&O stocks for top movers, buys ATM options, and auto-exits on target or stop-loss.

## How it works

```
09:20 AM  →  Screener ranks all NSE F&O stocks by % change
          →  Picks top gainers  →  buys ATM CALL (CE)
          →  Picks top losers   →  buys ATM PUT  (PE)
          →  WebSocket monitor watches live P&L
          →  Auto-exits each leg when TARGET or SL is hit
```

## Project structure

```
AlgoTrading/
├── main.py        # Entry point — orchestrates screener → orders → monitor
├── screener.py    # Fetches live quotes for all NSE FNO stocks, ranks movers
├── monitor.py     # WebSocket position monitor; auto-exits on TARGET / SL
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

| Setting | Default | Description |
|---|---|---|
| `RUN_TIME` | `"09:20"` | Time to run (24h, local time) |
| `WAIT_FOR_RUN_TIME` | `True` | Sleep until run time; `False` to run immediately |
| `SIDE` | `"both"` | `"gainers"`, `"losers"`, or `"both"` |
| `MAX_PCT_CHANGE` | `2.0` | Max % move allowed (ceiling filter) |
| `NUM_GAINERS` | `1` | How many gainer stocks to trade |
| `NUM_LOSERS` | `1` | How many loser stocks to trade |
| `RANK_BY` | `"pct"` | Rank by `"pct"` (biggest mover) or `"volume"` |
| `LOTS` | `1` | Lots per trade |
| `EXPIRY_INDEX` | `0` | `0` = nearest expiry, `1` = next, etc. |
| `PRODUCT_TYPE` | `"INTRADAY"` | `"INTRADAY"` (MIS) or `"MARGIN"` (NRML) |
| `TARGET_PER_TRADE` | `2000` | Exit when profit reaches ₹ this amount |
| `SL_PER_TRADE` | `2000` | Exit when loss reaches ₹ this amount |
| `DRY_RUN` | `False` | `True` = simulate only, no real orders |

### 4. Run

```bash
python main.py
```

Stop at any time with `Ctrl+C`.

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
