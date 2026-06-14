"""
Quick sanity-check for your Dhan Data API credentials.
Run: python test_connection.py

Tests:
  1. Auth  — confirms your CLIENT_ID + ACCESS_TOKEN are accepted
  2. Quote — fetches a live market quote for RELIANCE (NSE EQ)
  3. Scrip — downloads the instrument master CSV from Dhan
"""
import requests
from keys import CLIENT_ID, ACCESS_TOKEN

DHAN_BASE = "https://api.dhan.co/v2"

HEADERS = {
    "access-token": ACCESS_TOKEN,
    "client-id":    CLIENT_ID,
    "Content-Type": "application/json",
}

RELIANCE_ID = 2885   # Dhan security ID for RELIANCE NSE EQ

def test_quote():
    print("\n── Test 1: Market Quote (RELIANCE) ─────────────────────────")
    resp = requests.post(
        f"{DHAN_BASE}/marketfeed/quote",
        json={"NSE_EQ": [RELIANCE_ID]},
        headers=HEADERS,
        timeout=10,
    )
    print(f"  Status : {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json().get("data", {}).get("NSE_EQ", {})
        q    = data.get(str(RELIANCE_ID), {})
        ltp  = q.get("last_price") or q.get("ltp") or "—"
        print(f"  RELIANCE LTP : ₹{ltp}")
        print("  ✓ Data API is working.")
    else:
        print(f"  ✗ Error: {resp.text}")

def test_scrip_master():
    print("\n── Test 2: Scrip Master Download ───────────────────────────")
    resp = requests.get(
        "https://images.dhan.co/api-data/api-scrip-master.csv",
        timeout=30,
        stream=True,
    )
    print(f"  Status : {resp.status_code}")
    if resp.status_code == 200:
        first_line = next(resp.iter_lines()).decode()
        print(f"  Columns (first line): {first_line[:120]}…")
        print("  ✓ Scrip master reachable.")
    else:
        print(f"  ✗ Error: {resp.status_code}")

if __name__ == "__main__":
    print("=" * 55)
    print(f"  CLIENT_ID    : {CLIENT_ID}")
    print(f"  TOKEN (first 20) : {ACCESS_TOKEN[:20]}…")
    print("=" * 55)

    try:
        test_quote()
    except Exception as e:
        print(f"  ✗ Quote test failed: {e}")

    try:
        test_scrip_master()
    except Exception as e:
        print(f"  ✗ Scrip master test failed: {e}")

    print("\nDone. If both tests show ✓, run: python main.py")
