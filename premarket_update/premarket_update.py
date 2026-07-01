"""
Sends the pre-market snapshot (same data as the dashboard) as a formatted
Telegram message. Meant to run once each trading morning, 8:45-9:00 AM IST,
before market open.

ENV VARS REQUIRED (reuse the same bot you already use for OC Radar alerts):
  TELEGRAM_TOKEN       - your bot token from @BotFather
  TELEGRAM_CHAT_ID     - the chat/channel id to post into
  DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET - same ones oc_radar_bot uses

Run manually:
  python premarket_update.py

Scheduled via GitHub Actions (see .github/workflows/premarket_update.yml).
"""

import os
import sys

import requests

import data_sources as ds

DIVIDER = "━━━━━━━━━━━━━━━━━━━━"


def fmt_pct(val):
    if val is None:
        return "—"
    arrow = "▲" if val >= 0 else "▼"
    return f"{arrow} {abs(val)}%"


def compute_bias(d):
    """
    Simple weighted read on VIX trend + PCR + Gift Nifty gap. Each factor
    contributes -1 (bearish) / 0 (neutral) / +1 (bullish); the sum maps to
    a label. This is a rough sentiment cue, not a trading signal — no
    weighting was back-tested, it just mirrors the same "which way do the
    obvious pre-market cues point" read a human would eyeball.
    """
    score = 0
    reasons = []

    pcr = d["pcr"]
    if pcr.get("ok"):
        if pcr["pcr"] >= 1.1:
            score += 1
            reasons.append("PCR above 1.1")
        elif pcr["pcr"] <= 0.9:
            score -= 1
            reasons.append("PCR below 0.9")

    gn = d["gift_nifty"]
    if gn.get("ok") and gn.get("gap_points") is not None:
        gap = gn["gap_points"]
        if gap >= 30:
            score += 1
            reasons.append("Gift Nifty gap-up")
        elif gap <= -30:
            score -= 1
            reasons.append("Gift Nifty gap-down")

    vix = d["vix"]
    if vix.get("ok"):
        chg = vix["pct_change"]
        if chg <= -2:
            score += 1
            reasons.append("VIX cooling")
        elif chg >= 3:
            score -= 1
            reasons.append("VIX spiking")

    if score >= 2:
        label = "🟢 Bullish"
    elif score == 1:
        label = "🟢 Mildly Bullish"
    elif score == 0:
        label = "🟡 Neutral"
    elif score == -1:
        label = "🔴 Mildly Bearish"
    else:
        label = "🔴 Bearish"

    reason_text = ", ".join(reasons) if reasons else "no strong signals either way"
    return label, reason_text


def build_message():
    d = ds.build_dashboard_data()

    # Debug: raw error details for any failed source (Actions logs only)
    for key in ("nifty", "vix", "pcr", "fii", "gift_nifty"):
        if not d[key].get("ok"):
            print(f"DEBUG [{key}] failed: {d[key].get('error')}")
    if not d["global"].get("ok"):
        for grp, tickers in d["global"].get("groups", {}).items():
            for name, row in tickers.items():
                if not row.get("ok"):
                    print(f"DEBUG [global.{grp}.{name}] failed: {row.get('error')}")

    lines = []
    lines.append("📊 <b>PRE-MARKET RADAR</b>")
    lines.append(f"<i>{d['generated_at']}</i>")
    lines.append(DIVIDER)
    lines.append("")

    # ── Indices ──────────────────────────────────────────
    lines.append("🇮🇳 <b>INDICES</b>")
    n = d["nifty"]
    if n.get("ok"):
        lines.append(f"Nifty 50   {n['last']:,.2f}   {fmt_pct(n['pct_change'])}")
        lines.append(f"  L: {n['day_low']:,.0f}  H: {n['day_high']:,.0f}")
    else:
        lines.append("Nifty 50   source offline")

    v = d["vix"]
    if v.get("ok"):
        lines.append(f"India VIX  {v['last']}   {fmt_pct(v['pct_change'])}")
    else:
        lines.append("India VIX  source offline")

    p = d["pcr"]
    if p.get("ok"):
        sentiment = "Bullish" if p["pcr"] >= 1 else "Bearish"
        lines.append(f"PCR        {p['pcr']} ({sentiment})")
    else:
        lines.append("PCR        source offline")

    lines.append(DIVIDER)
    lines.append("")

    # ── Pre-market signals ───────────────────────────────
    lines.append("📡 <b>PRE-MARKET SIGNALS</b>")
    g = d["gift_nifty"]
    if g.get("ok") and g.get("gap_points") is not None:
        gap = g["gap_points"]
        sign = "+" if gap >= 0 else ""
        tag = "auto" if g.get("source") == "nse" else "manual"
        lines.append(f"Gift Nifty  {sign}{gap} pts <i>({tag})</i>")
    else:
        lines.append("Gift Nifty  not set - update gift_nifty.json")

    f = d["fii"]
    if f.get("ok"):
        lines.append(f"FII (Fut)   {f['long_pct']}% long / {f['short_pct']}% short")
        lines.append(f"Net: {f['net_contracts']:,} contracts (as of {f['as_of']})")
    else:
        lines.append("FII (Fut)   source offline")

    # ── Global cues ──────────────────────────────────────
    if d["global"].get("ok"):
        gm = d["global"]["groups"]
        lines.append(DIVIDER)
        lines.append("")
        lines.append("🌍 <b>GLOBAL CUES</b>")
        for group_key, group_label in [("us", "US"), ("commodities", "Comdty"), ("asia", "Asia")]:
            entries = [
                f"{k} {fmt_pct(v.get('pct_change'))}"
                for k, v in gm[group_key].items() if v.get("ok")
            ]
            if entries:
                lines.append(f"{group_label:<8}" + "  ".join(entries))

    # ── Bias ─────────────────────────────────────────────
    lines.append(DIVIDER)
    label, reason = compute_bias(d)
    lines.append(f"{label} — {reason}")

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
  
