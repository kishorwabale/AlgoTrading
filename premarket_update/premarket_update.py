"""
Sends the pre-market snapshot (same data as the dashboard) as a formatted
Telegram message. Meant to run once each trading morning, 8:45-9:00 AM IST,
before market open.

ENV VARS REQUIRED (reuse the same bot you already use for OC Radar alerts):
  TELEGRAM_TOKEN       - your bot token from @BotFather
  TELEGRAM_CHAT_ID     - the chat/channel id to post into

Run manually:
  python telegram_report.py

Scheduled via GitHub Actions (see .github/workflows/premarket-report.yml) or
your existing GitHub Actions runner pattern used for OC Radar.
"""

import os
import sys

import requests

import data_sources as ds


def fmt_pct(val):
    if val is None:
        return "—"
    arrow = "🟢▲" if val >= 0 else "🔴▼"
    return f"{arrow} {abs(val)}%"


def build_message():
    d = ds.build_dashboard_data()
    lines = []
    lines.append("📊 <b>PRE-MARKET RADAR</b>")
    lines.append(f"<i>{d['generated_at']}</i>")
    lines.append("")

    # Nifty spot
    n = d["nifty"]
    if n.get("ok"):
        lines.append(f"<b>NIFTY 50:</b> {n['last']:,.2f}  {fmt_pct(n['pct_change'])}")
        lines.append(f"  L: {n['day_low']:,.0f}  H: {n['day_high']:,.0f}")
    else:
        lines.append("<b>NIFTY 50:</b> source offline")

    # VIX
    v = d["vix"]
    if v.get("ok"):
        lines.append(f"<b>India VIX:</b> {v['last']}  {fmt_pct(v['pct_change'])}")
    else:
        lines.append("<b>India VIX:</b> source offline")

    # PCR
    p = d["pcr"]
    if p.get("ok"):
        sentiment = "BULLISH" if p["pcr"] >= 1 else "BEARISH"
        lines.append(f"<b>PCR:</b> {p['pcr']}  ({sentiment})")
    else:
        lines.append("<b>PCR:</b> source offline")

    lines.append("")

    # Gift Nifty
    g = d["gift_nifty"]
    if g.get("ok"):
        gap = g["gap_points"]
        sign = "+" if gap >= 0 else ""
        lines.append(f"<b>Gift Nifty Gap:</b> {sign}{gap} pts")
    else:
        lines.append("<b>Gift Nifty Gap:</b> not set - update gift_nifty.json")

    # FII
    f = d["fii"]
    if f.get("ok"):
        lines.append(f"<b>FII Index Futures:</b> {f['long_pct']}% long / {f['short_pct']}% short")
        lines.append(f"  Net: {f['net_contracts']:,} contracts (as of {f['as_of']})")
    else:
        lines.append("<b>FII positioning:</b> source offline")

    # Global markets
    if d["global"].get("ok"):
        g = d["global"]["groups"]
        lines.append("")
        for group_key, group_label in [("us", "US"), ("commodities", "Commodities"), ("asia", "Asia")]:
            entries = [
                f"{k} {fmt_pct(v.get('pct_change'))}"
                for k, v in g[group_key].items() if v.get("ok")
            ]
            if entries:
                lines.append(f"<b>{group_label}:</b> " + "  ".join(entries))

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
    print(msg)  # useful for debugging in GitHub Actions logs
    send_telegram(msg)
  
