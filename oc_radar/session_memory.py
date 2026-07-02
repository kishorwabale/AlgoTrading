"""
session_memory.py — OC Radar v11 Phase 1 + 2
=============================================
Session-long scan history per index, with:
  Phase 1: direction stability % + flip count (dashboard header lines)
  Phase 2: score momentum arrows (per-index, next to score)

Drop this file in oc_radar/ and import from your main scan loop.
No external dependencies. Optional JSON persistence for crash recovery.
"""

import json
import os
from datetime import datetime

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
STABILITY_WINDOW = 6          # last N scans used for stability % (~30 min at 5-min scans)
MOMENTUM_WINDOW = 3           # last N scores used for momentum arrow
MOMENTUM_THRESHOLD = 5        # min score change over window to count as building/fading
PERSIST_FILE = "session_history.json"   # set to None to disable disk persistence


class SessionMemory:
    def __init__(self, persist_file=PERSIST_FILE):
        self.persist_file = persist_file
        self.history = {}     # {"NIFTY": [{"time","dir","score","spot"}, ...], ...}
        self._load()

    # -----------------------------------------------------------
    # Recording
    # -----------------------------------------------------------
    def record(self, index, direction, score, spot):
        """Call once per index per scan, right after scores are computed."""
        entry = {
            "time": datetime.now().strftime("%H:%M"),
            "dir": direction,          # "CE" or "PE"
            "score": int(score),
            "spot": float(spot),
        }
        self.history.setdefault(index, []).append(entry)
        self._save()

    # -----------------------------------------------------------
    # Phase 1 — Direction stability %
    # -----------------------------------------------------------
    def stability(self, index, window=STABILITY_WINDOW):
        """
        % of the last `window` scans agreeing with the most common direction.
        Returns (pct, dominant_dir, scans_used). pct=None if <2 scans.
        """
        scans = self.history.get(index, [])[-window:]
        if len(scans) < 2:
            return None, None, len(scans)
        dirs = [s["dir"] for s in scans]
        dominant = max(set(dirs), key=dirs.count)
        pct = round(100 * dirs.count(dominant) / len(dirs))
        return pct, dominant, len(dirs)

    # -----------------------------------------------------------
    # Phase 1 — Flip count since open
    # -----------------------------------------------------------
    def flip_count(self, index):
        """Number of direction changes across the whole session."""
        dirs = [s["dir"] for s in self.history.get(index, [])]
        return sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)

    # -----------------------------------------------------------
    # Phase 2 — Score momentum arrow
    # -----------------------------------------------------------
    def momentum(self, index, window=MOMENTUM_WINDOW):
        """
        Compare current score vs score `window` scans ago,
        only when direction has been consistent over that window.
        Returns one of: "↗", "↘", "→", "" (not enough data / mixed direction).
        """
        scans = self.history.get(index, [])[-(window + 1):]
        if len(scans) < window + 1:
            return ""
        dirs = {s["dir"] for s in scans}
        if len(dirs) > 1:
            return ""                      # direction flipped inside window — momentum meaningless
        delta = scans[-1]["score"] - scans[0]["score"]
        if delta >= MOMENTUM_THRESHOLD:
            return "↗"                     # building
        if delta <= -MOMENTUM_THRESHOLD:
            return "↘"                     # fading
        return "→"                         # flat

    # -----------------------------------------------------------
    # Telegram formatting helpers
    # -----------------------------------------------------------
    def header_line(self, index):
        """
        One-line session context for the dashboard header, e.g.:
        NIFTY 🔀 4 flips · stability 50% (CE) — RANGEBOUND
        NIFTY ✅ 0 flips · stability 100% (CE) — TRENDING
        """
        flips = self.flip_count(index)
        pct, dom, n = self.stability(index)
        if pct is None:
            return f"{index} · warming up ({n} scan{'s' if n != 1 else ''})"

        scans_today = len(self.history.get(index, []))
        heavy_chop = scans_today >= 4 and flips >= scans_today / 2

        if heavy_chop or pct < 60:
            tag, icon = "RANGEBOUND", "🔀"
        elif pct >= 80:
            tag, icon = "TRENDING", "✅"
        else:
            tag, icon = "LEANING", "↕️"

        return f"{index} {icon} {flips} flips · stability {pct}% ({dom}) — {tag}"

    def dashboard_block(self, indices=("NIFTY", "SENSEX", "BANKNIFTY")):
        """Multi-line block to insert under the SCORE DASHBOARD."""
        lines = ["📈 SESSION CONTEXT"]
        lines += [self.header_line(ix) for ix in indices]
        return "\n".join(lines)

    def score_with_momentum(self, index, direction, score, band_emoji, band_name):
        """
        Dashboard score line with momentum arrow, e.g.:
        NIFTY        CE  55↗ 🟡 WATCH
        """
        arrow = self.momentum(index)
        return f"{index:<12} {direction}  {score}{arrow} {band_emoji} {band_name}"

    # -----------------------------------------------------------
    # Phase 3 — Alert gating
    # -----------------------------------------------------------
    def gate(self, index, direction, score):
        """
        Decide whether to show the full BUY setup or suppress it.

        Returns (action, reason):
          action = "SHOW"  → print full setup as normal
                   "CHOP"  → suppress entry/target/SL, print chop block
                   "FADE"  → show setup but flag score as fading

        Rules (in priority order):
          1. TRADE band (score >= 75) is NEVER gated — fresh strong
             information beats stale patterns.
          2. Direction flipped vs previous scan AND both scores < 65 → CHOP.
          3. Session stability < 60% (or heavy chop) AND score < 65 → CHOP.
          4. Momentum fading (↘) AND score < 75 → FADE (shown, but flagged).
          5. Otherwise → SHOW.
        """
        if score >= 75:
            return "SHOW", "TRADE band — never gated"

        scans = self.history.get(index, [])
        prev = scans[-2] if len(scans) >= 2 else None

        # Rule 2: immediate flip with sub-TRADE scores on both sides.
        # (v1 used <65 and let a CE 66 → PE 61 flip through — real bug
        #  caught in replay of 2nd Jul 2026. Both below 75 = conflict.)
        if prev and prev["dir"] != direction and prev["score"] < 75 and score < 75:
            return "CHOP", (f"direction flipped {prev['dir']}→{direction} "
                            f"({prev['score']}→{score}), both sub-TRADE")

        # Rule 3: session-level instability
        pct, dom, n = self.stability(index)
        flips = self.flip_count(index)
        scans_today = len(scans)
        heavy_chop = scans_today >= 4 and flips >= scans_today / 2
        if score < 65 and pct is not None and (pct < 60 or heavy_chop):
            return "CHOP", (f"stability {pct}% · {flips} flips today — "
                            f"no session edge")

        # Rule 4: fading score
        if self.momentum(index) == "↘" and score < 75:
            return "FADE", "score fading over last 3 scans"

        return "SHOW", "signal consistent with session"

    def chop_block(self, index, spot, max_pain, reason):
        """Replacement block when a setup is gated. No prices, no setup."""
        return (f"📋 {index} ₹{spot:,.0f}\n"
                f"⚠️ CHOP — {reason}\n"
                f"MP ₹{max_pain:,.0f} · NO EDGE — stand aside")

    # -----------------------------------------------------------
    # Persistence (survives job restart; auto-resets next day)
    # -----------------------------------------------------------
    def _save(self):
        if not self.persist_file:
            return
        try:
            payload = {"date": datetime.now().strftime("%Y-%m-%d"),
                       "history": self.history}
            with open(self.persist_file, "w") as f:
                json.dump(payload, f)
        except Exception:
            pass    # persistence is best-effort; never break the scan loop

    def _load(self):
        if not self.persist_file or not os.path.exists(self.persist_file):
            return
        try:
            with open(self.persist_file) as f:
                payload = json.load(f)
            if payload.get("date") == datetime.now().strftime("%Y-%m-%d"):
                self.history = payload.get("history", {})
        except Exception:
            self.history = {}


# ---------------------------------------------------------------
# Integration example (in your main scan loop)
# ---------------------------------------------------------------
if __name__ == "__main__":
    mem = SessionMemory(persist_file=None)   # demo: no disk writes

    # Replay of 2nd Jul 2026 — your two real alerts (12:37, 12:43)
    # plus a plausible morning leading into them, and a hypothetical
    # afternoon breakout to prove Rule 1 (TRADE band never gated).
    replay = [
        # time     index        dir   score  spot      mp
        ("11:57", "NIFTY",     "CE", 57, 24110, 24100),
        ("11:57", "BANKNIFTY", "CE", 52, 58060, 58100),
        ("12:17", "NIFTY",     "PE", 49, 24098, 24100),
        ("12:17", "BANKNIFTY", "CE", 60, 58105, 58100),
        ("12:37", "NIFTY",     "PE", 53, 24125, 24100),   # your alert 1
        ("12:37", "BANKNIFTY", "CE", 66, 58140, 58100),   # your alert 1
        ("12:43", "NIFTY",     "CE", 55, 24129, 24100),   # your alert 2
        ("12:43", "BANKNIFTY", "PE", 61, 58119, 58200),   # your alert 2
        # hypothetical afternoon: real breakout appears
        ("14:20", "NIFTY",     "CE", 71, 24188, 24100),
        ("14:25", "NIFTY",     "CE", 82, 24215, 24100),
    ]

    for t, ix, d, sc, sp, mp in replay:
        mem.record(ix, d, sc, sp)
        action, reason = mem.gate(ix, d, sc)
        print(f"[{t}] {ix:<10} {d} {sc:>3} → {action:<4} · {reason}")
        if action == "CHOP":
            print("      " + mem.chop_block(ix, sp, mp, reason)
                  .replace("\n", "\n      "))
        print()

    print("=" * 50)
    print(mem.dashboard_block(indices=("NIFTY", "BANKNIFTY")))
