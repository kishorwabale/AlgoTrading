"""
Sends a short Telegram confirmation of Nifty's pre-open auction result -
the futures price discovered during the 9:00-9:15 F&O pre-open call
auction, and the implied gap vs previous close.

Meant to run once, around 9:13 AM IST - after the order matching phase
(9:08-9:12) has concluded so there's an actual discovered price to report,
but before continuous trading fully takes over at 9:15. Same timing your
oc_radar_bot.py cron already uses (43 3 * * 1-5 UTC = 9:13 AM IST).

Separate from premarket_update.py (the 8:45 AM report) because at 8:45 the
auction hasn't started yet - fetching this earlier would just return
yesterday's stale futures price, not a real pre-open read.

ENV VARS REQUIRED (same ones premarket_update.py and oc_radar_bot.py use):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
  DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET
"""

import os
import sys

import requests

import data_sources as ds


def build_message():
    fp = ds.get_futures_preopen()

    if not fp.get("ok"):
        print(f"DEBUG [futures_preopen] failed: {fp.get('error')}")
        return (
            "📡 <b>NIFTY PRE-OPEN AUCTION</b>\n"
            "Source unavailable this morning — check logs."
        )

    gap = fp["gap_points"]
    sign = "+" if gap >= 0 else ""
    direction = "gap-up" if gap > 0 else "gap-down" if gap < 0 else "flat"

    lines = [
        "📡 <b>NIFTY PRE-OPEN AUCTION</b>",
        f"Discovered price: {fp['futures_price']:,.2f}",
        f"Prev close: {fp['prev_close']:,.2f}",
        f"Implied gap: {sign}{gap} pts ({direction})",
        "<i>[testing — first live run]</i>",
    ]
    return "\n".join(lines)


def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID env vars.")
        sys.exit(1)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }, timeout=10)

    if resp.status_code != 200:
        print(f"Telegram send failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    print("Sent.")


if __name__ == "__main__":
    msg = build_message()
    print(msg)
    send_telegram(msg)
  
