"""
Position monitor — watches open option positions via Dhan live WebSocket feed.

After entry orders are filled:
  - Subscribes to real-time LTP ticks for every option bought.
  - On each tick: P&L = (current_LTP - entry_price) × quantity
  - Exits (SELL MARKET) the moment P&L >= TARGET_PER_TRADE
    or P&L <= -SL_PER_TRADE.
  - Blocks until every position is exited (or Ctrl+C).
"""
import logging
import threading
import requests
from datetime import datetime
from time import sleep

from keys import CLIENT_ID, ACCESS_TOKEN
from config import (
    TARGET_PER_TRADE, SL_PER_TRADE, PRODUCT_TYPE, ORDER_TAG, DRY_RUN,
    EOD_EXIT_TIME, TRAIL_TRIGGER, TRAIL_LOCK_PCT,
)

log = logging.getLogger(__name__)

DHAN_BASE     = "https://api.dhan.co/v2"
FILL_POLL_SEC = 1    # seconds between order-status polls
FILL_RETRIES  = 30   # max seconds to wait for a fill confirmation


def _headers() -> dict:
    return {
        "access-token": ACCESS_TOKEN,
        "client-id":    CLIENT_ID,
        "Content-Type": "application/json",
    }


# =============================================================================
# Fill-price poller
# =============================================================================

def wait_for_fill(order_id: str) -> float | None:
    """
    Poll Dhan order status until TRADED, return average fill price.
    Returns None if not filled within FILL_RETRIES seconds.
    """
    log.info(f"  Waiting for fill on order {order_id}…")
    for attempt in range(FILL_RETRIES):
        resp = requests.get(
            f"{DHAN_BASE}/orders/{order_id}",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data   = resp.json()
        status = str(data.get("orderStatus", data.get("status", ""))).upper()

        if status in ("TRADED", "FILLED", "PART_TRAD"):
            price = (data.get("averageTradedPrice")
                     or data.get("averageTradePrice")
                     or data.get("tradedPrice")
                     or 0)
            if price:
                log.info(f"  Order {order_id} filled @ ₹{float(price):.2f}")
                return float(price)

        if attempt < FILL_RETRIES - 1:
            sleep(FILL_POLL_SEC)

    log.warning(f"  Order {order_id} not confirmed filled after {FILL_RETRIES}s.")
    return None


def fetch_option_quote(security_id: str) -> dict:
    """
    Fetch the full market quote for an option contract via REST.
    Returns the raw quote dict (ltp, open_interest, volume, …).
    """
    resp = requests.post(
        f"{DHAN_BASE}/marketfeed/quote",
        json={"NSE_FNO": [int(security_id)]},
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {}).get("NSE_FNO", {})
    return data.get(str(security_id), {})


def fetch_option_ltp(security_id: str) -> float:
    """
    Fetch current LTP of an option contract via REST (used for DRY_RUN entry simulation).
    """
    q = fetch_option_quote(security_id)
    return float(q.get("last_price") or q.get("ltp") or 0)


# =============================================================================
# Exit order
# =============================================================================

def _place_exit(pos: dict) -> dict:
    """Place SELL MARKET order to close one option leg."""
    payload = {
        "dhanClientId":      CLIENT_ID,
        "correlationId":     f"{ORDER_TAG}-exit",
        "transactionType":   "SELL",
        "exchangeSegment":   "NSE_FNO",
        "productType":       PRODUCT_TYPE,
        "orderType":         "MARKET",
        "validity":          "DAY",
        "tradingSymbol":     pos["trading_symbol"],
        "securityId":        pos["security_id"],
        "quantity":          pos["quantity"],
        "price":             0,
        "disclosedQuantity": 0,
        "afterMarketOrder":  False,
        "amoTime":           "OPEN",
    }
    resp = requests.post(
        f"{DHAN_BASE}/orders",
        json=payload,
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# =============================================================================
# Position monitor
# =============================================================================

class PositionMonitor:
    """
    Subscribes to Dhan live WebSocket ticks for all open option positions.
    Exits each leg individually when TARGET or SL is reached.
    Blocks the calling thread until all legs are exited.

    Each position dict must have:
        security_id, entry_price, quantity, trading_symbol, option_type
    """

    def __init__(self, positions: list[dict]):
        self._pos          : dict[str, dict]  = {p["security_id"]: dict(p) for p in positions}
        self._exited       : set[str]         = set()
        self._lock                            = threading.Lock()
        self._peak_pnl     : dict[str, float] = {p["security_id"]: 0.0 for p in positions}
        self._trail_active : set[str]         = set()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _all_done(self) -> bool:
        with self._lock:
            return len(self._exited) >= len(self._pos)

    def _try_exit(self, sid: str, pos: dict, reason: str):
        with self._lock:
            if sid in self._exited:
                return
            self._exited.add(sid)

        sym = pos.get("trading_symbol", sid)
        if DRY_RUN:
            log.info(f"  [DRY RUN] Would exit {sym} ({reason}). No real order placed.")
            return

        try:
            result = _place_exit(pos)
            log.info(f"  Exit order sent ({reason}) for {sym}: {result}")
        except Exception as e:
            log.error(f"  Exit order FAILED for {sym}: {e}")

    # ── WebSocket tick handler ─────────────────────────────────────────────────

    def _on_tick(self, data):
        """
        Called by dhanhq marketfeed on every incoming message.
        dhanhq v2 passes either (data,) or (instance, data) depending on version.
        We handle both by checking the type of the first argument.
        """
        # Normalise: some dhanhq builds pass (instance, dict), others just (dict)
        if not isinstance(data, dict):
            return

        try:
            sid = str(
                data.get("security_id")
                or data.get("SecurityId")
                or data.get("securityId")
                or ""
            )
            ltp = float(
                data.get("LTP")
                or data.get("last_price")
                or data.get("ltp")
                or 0
            )
            if not sid or not ltp:
                return

            with self._lock:
                if sid not in self._pos or sid in self._exited:
                    return
                pos = self._pos[sid]

            entry = pos["entry_price"]
            qty   = pos["quantity"]
            pnl   = round((ltp - entry) * qty, 2)
            sym   = pos.get("trading_symbol", sid)

            # ── Trailing SL logic ──────────────────────────────────────────────
            with self._lock:
                if sid in self._exited:
                    return
                if pnl > self._peak_pnl[sid]:
                    self._peak_pnl[sid] = pnl
                peak            = self._peak_pnl[sid]
                newly_activated = (
                    TRAIL_TRIGGER > 0
                    and pnl >= TRAIL_TRIGGER
                    and sid not in self._trail_active
                )
                if newly_activated:
                    self._trail_active.add(sid)
                trail_active    = sid in self._trail_active
                trail_sl_level  = round(peak * TRAIL_LOCK_PCT / 100, 2) if trail_active else None

            if newly_activated:
                log.info(
                    f"  TRAIL ARMED  {sym}  P&L=₹{pnl:+.2f}  "
                    f"Peak=₹{peak:.2f}  protecting {TRAIL_LOCK_PCT:.0f}% of peak"
                )

            trail_str = f"  TrailSL=₹{trail_sl_level:>+.2f}" if trail_sl_level is not None else ""
            log.info(
                f"  TICK  {sym:<30s}  "
                f"LTP=₹{ltp:>8.2f}  Entry=₹{entry:>8.2f}  "
                f"P&L=₹{pnl:>+10.2f}{trail_str}"
            )

            if pnl >= TARGET_PER_TRADE:
                log.info(f"  TARGET HIT    {sym}  P&L=₹{pnl:+.2f}  → Exiting")
                self._try_exit(sid, pos, "TARGET")

            elif trail_sl_level is not None and pnl < trail_sl_level:
                log.info(
                    f"  TRAIL SL HIT  {sym}  P&L=₹{pnl:+.2f}  "
                    f"TrailSL=₹{trail_sl_level:.2f}  Peak=₹{peak:.2f}  → Exiting"
                )
                self._try_exit(sid, pos, "TRAIL_SL")

            elif pnl <= -SL_PER_TRADE:
                log.info(f"  SL HIT        {sym}  P&L=₹{pnl:+.2f}  → Exiting")
                self._try_exit(sid, pos, "STOPLOSS")

        except Exception as e:
            log.error(f"  Tick handler error: {e}")

    # ── Entry point ────────────────────────────────────────────────────────────

    def run(self):
        """Start the WebSocket feed and block until all positions are exited."""
        if not self._pos:
            log.info("No positions to monitor.")
            return

        try:
            from dhanhq import marketfeed
        except ImportError:
            log.error("dhanhq library not found.  Run: pip install dhanhq")
            return

        instruments = [
            (marketfeed.NSE_FNO, sid, marketfeed.Ticker)
            for sid in self._pos
        ]

        log.info("-" * 65)
        log.info(f"WebSocket monitor — {len(instruments)} position(s)")
        log.info(f"  Target : ₹{TARGET_PER_TRADE:>8,.0f}  per trade")
        log.info(f"  SL     : ₹{SL_PER_TRADE:>8,.0f}  per trade")
        if TRAIL_TRIGGER > 0:
            log.info(
                f"  Trail  : activates at ₹{TRAIL_TRIGGER:,.0f}  "
                f"locks {TRAIL_LOCK_PCT:.0f}% of peak"
            )
        else:
            log.info(f"  Trail  : disabled")
        for pos in self._pos.values():
            log.info(
                f"  Watching: {pos['trading_symbol']:<30s} "
                f"Entry=₹{pos['entry_price']:.2f}  Qty={pos['quantity']}"
            )
        log.info("-" * 65)

        feed = marketfeed.DhanFeed(
            client_id=CLIENT_ID,
            access_token=ACCESS_TOKEN,
            instruments=instruments,
            subscription_type=marketfeed.Ticker,
            on_message=self._on_tick,
        )

        feed_thread = threading.Thread(target=feed.run_forever, daemon=True)
        feed_thread.start()

        _eod = datetime.strptime(EOD_EXIT_TIME, "%H:%M").time()
        log.info(f"Live feed running… EOD exit at {EOD_EXIT_TIME}  (Ctrl+C to stop)")
        try:
            while not self._all_done():
                if datetime.now().time() >= _eod:
                    log.info(
                        f"EOD exit time {EOD_EXIT_TIME} reached — "
                        f"force-exiting all remaining positions."
                    )
                    with self._lock:
                        remaining = [sid for sid in self._pos if sid not in self._exited]
                    for sid in remaining:
                        self._try_exit(sid, self._pos[sid], "EOD")
                    break
                sleep(1)
        except KeyboardInterrupt:
            log.info("Monitor stopped by user (Ctrl+C).")

        log.info("All positions exited. Monitor shut down.")
