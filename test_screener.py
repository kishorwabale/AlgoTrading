"""
Test: print the raw NSE Top 20 F&O Gainers and Losers list.
No filters, no trading — just shows what NSE returns.
Run: python test_screener.py
"""
import requests

NSE_HOME    = "https://www.nseindia.com"
NSE_GAINERS = "https://www.nseindia.com/api/live-analysis-variations?index=gainers&limit=20&category=fo"
NSE_LOSERS  = "https://www.nseindia.com/api/live-analysis-variations?index=loosers&limit=20&category=fo"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://www.nseindia.com",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

def fetch(url, session):
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    return (
        payload.get("data")
        or payload.get("gainers")
        or payload.get("loosers")
        or (payload if isinstance(payload, list) else [])
    )

def print_table(title, rows):
    print(f"\n{'='*60}")
    print(f"  {title}  ({len(rows)} stocks)")
    print(f"{'='*60}")
    print(f"  {'#':<4} {'SYMBOL':<15} {'LTP':>10} {'PREV CLOSE':>12} {'% CHANGE':>10} {'VOLUME':>14}")
    print(f"  {'-'*4} {'-'*15} {'-'*10} {'-'*12} {'-'*10} {'-'*14}")
    for i, r in enumerate(rows, 1):
        symbol     = r.get("symbol", "")
        ltp        = r.get("lastPrice")   or r.get("ltp",      0)
        prev_close = r.get("previousClose") or r.get("prevClose", 0)
        change_pct = r.get("pChange")     or r.get("pctChange", 0)
        volume     = r.get("tradedQuantity") or r.get("volume",  0)
        print(f"  {i:<4} {symbol:<15} {ltp:>10.2f} {prev_close:>12.2f} {change_pct:>+10.2f}% {int(volume):>14,}")

if __name__ == "__main__":
    print("Connecting to NSE…")
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(NSE_HOME, timeout=15)   # prime session / get cookies
    print("Session ready.")

    try:
        gainers = fetch(NSE_GAINERS, session)
        print_table("NSE TOP 20 F&O GAINERS", gainers)
    except Exception as e:
        print(f"  Gainers fetch failed: {e}")

    try:
        losers = fetch(NSE_LOSERS, session)
        print_table("NSE TOP 20 F&O LOSERS", losers)
    except Exception as e:
        print(f"  Losers fetch failed: {e}")

    print("\nDone.")
