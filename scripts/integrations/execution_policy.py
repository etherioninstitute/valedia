"""Execution policy: decides HOW to enter (maker / taker / no_trade), separate from WHAT to
trade (signal fusion) and HOW MUCH (kelly_sizing). Adapted from kalshi-agent's execution.py.

SINGLE-SOURCE: fee drag uses fee_engine.kalshi_fee_per_contract (real nonlinear fee), NOT the
flat 0.07 the source used. Book microstructure from market_state.MARKET_STATE. Live maker orders
route through kalshi_auth and are gated by valedia_config.EXEC_DRY_RUN (False as of 2026-09-12 —
live maker orders enabled; flip True to paper-gate). MakerOrderManager NEVER places a resting order it can't cancel.
(c) GOATWORTHY Post LLC.
"""
import os, sys, time, logging
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fee_engine import kalshi_fee_per_contract          # canonical real Kalshi fee (single source)
from market_state import MARKET_STATE, BookState
import valedia_config as _cfg

log = logging.getLogger("valedia.execution")


@dataclass
class ExecutionPlan:
    action: str                       # "taker" | "maker" | "no_trade"
    price_cents: int
    contracts: int
    platform: str
    side: str                         # "yes" | "no"
    reason: str
    estimated_fill_prob: float = 1.0
    estimated_slippage_cents: float = 0.0
    edge_after_fees_pct: float = 0.0
    urgency: str = "normal"           # "high" (near expiry) | "normal" | "low"


def _fee_drag_pct(price_cents):
    """Round-trip fee as % of contract cost, using the REAL nonlinear Kalshi fee."""
    if price_cents <= 0:
        return 999.0
    p = price_cents / 100.0
    return (kalshi_fee_per_contract(p) * 2) / p * 100


def assess_book_quality(book: Optional[BookState]) -> dict:
    """Evaluate order-book quality for an execution decision."""
    if book is None or book.is_stale:
        return {"quality": "unknown", "spread": None, "depth_yes": 0, "depth_no": 0,
                "is_tradeable": False, "reason": "stale or missing book data"}
    spread = book.spread_cents
    yes_depth = sum(l.size for l in book.yes_bids[:3]) if book.yes_bids else 0
    no_depth = sum(l.size for l in book.no_bids[:3]) if book.no_bids else 0
    if spread is None:
        quality, tradeable, reason = "thin", False, "no two-sided market"
    elif spread > 20:
        quality, tradeable, reason = "very_wide", False, f"spread too wide ({spread}c)"
    elif spread > 10:
        quality, tradeable, reason = "wide", True, f"wide spread ({spread}c) -- maker preferred"
    elif spread > 3:
        quality, tradeable, reason = "normal", True, "normal spread"
    else:
        quality, tradeable, reason = "tight", True, "tight spread -- taker OK"
    return {"quality": quality, "spread": spread, "depth_yes": yes_depth, "depth_no": no_depth,
            "microprice": book.microprice, "imbalance": book.imbalance,
            "is_tradeable": tradeable, "reason": reason}


def build_execution_plan(ticker, side, edge_pct, price_cents, contracts, hours_left,
                         platform="kalshi", book=None) -> ExecutionPlan:
    """Decide taker / maker / no_trade from edge, fee drag, urgency, and (if available) book."""
    if book is None:
        book = MARKET_STATE.get_book_if_fresh(ticker)
    bq = assess_book_quality(book)
    urgency = "high" if hours_left <= 2 else "normal" if hours_left <= 12 else "low"

    if book is not None and not bq["is_tradeable"]:
        return ExecutionPlan("no_trade", price_cents, contracts, platform, side,
                             f"Book quality too poor: {bq['reason']}", urgency=urgency)

    edge_after = abs(edge_pct) - _fee_drag_pct(price_cents)
    if edge_after <= 0:
        return ExecutionPlan("no_trade", price_cents, contracts, platform, side,
                             f"Edge ({edge_pct:.1f}%) consumed by fees ({_fee_drag_pct(price_cents):.1f}%)",
                             edge_after_fees_pct=edge_after, urgency=urgency)

    spread = bq.get("spread")
    if spread is not None and spread > 5 and urgency != "high":
        if side.lower() == "yes" and book and book.best_yes_bid is not None:
            maker_price = book.best_yes_bid + 1
        elif side.lower() == "no" and book and book.best_no_bid is not None:
            maker_price = book.best_no_bid + 1
        else:
            maker_price = price_cents
        return ExecutionPlan("maker", min(maker_price, price_cents), contracts, platform, side,
                             f"Maker order: spread={spread}c, improving bid by 1c",
                             estimated_fill_prob=0.6, edge_after_fees_pct=edge_after, urgency=urgency)

    return ExecutionPlan("taker", price_cents, contracts, platform, side,
                         f"Taker entry: edge={edge_pct:.1f}% after_fees={edge_after:.1f}%",
                         estimated_fill_prob=0.95, edge_after_fees_pct=edge_after, urgency=urgency)


class MakerOrderManager:
    """Place/track/cancel/reprice resting maker orders + record FILLS into the ledger.

    Valedia runs per-cron (fresh process each cycle), so tracked orders PERSIST to a JSON
    file — otherwise a resting order placed one cycle would be un-managed and its fill
    unrecorded the next. Fills are recorded on ACTUAL fill (via get_fills), never at
    placement (a resting order may never fill). Live orders gated by EXEC_DRY_RUN.
    `api` defaults to kalshi_auth; inject a stub for tests. Requires place + cancel + get_fills."""

    def __init__(self, api=None, max_age_seconds=300, state_file=None):
        self.api = api
        self._max_age_seconds = max_age_seconds
        self._state_file = state_file or os.path.expanduser(
            "~/.openclaw/workspace/models/maker_orders.json")
        self._orders = self._load()

    def _load(self):
        try:
            import json
            return json.load(open(self._state_file))
        except Exception:
            return {}

    def _save(self):
        try:
            import json
            with open(self._state_file, "w") as f:
                json.dump(self._orders, f, indent=2)
        except Exception:
            pass

    def _api(self):
        if self.api is not None:
            return self.api
        import kalshi_auth as K
        return K

    def _place(self, ticker, side, contracts, price_cents, informed=None):
        _cfg.require_not_halted("execution_policy._place")
        a = self._api()
        if self.api is not None:
            try:
                return a.place_order(ticker, side, contracts, price_cents, informed=informed)
            except TypeError:
                return a.place_order(ticker, side, contracts, price_cents)
        return a.create_order(ticker, side, contracts, price_cents, informed=informed)

    def _cancel(self, order_id, ticker=None):
        fn = self._api().cancel_order
        try:
            return fn(order_id, ticker=ticker)
        except TypeError:
            return fn(order_id)

    def place_maker_order(self, ticker, side, contracts, price_cents, strategy="", max_age_s=None, informed=None):
        if getattr(_cfg, "EXEC_DRY_RUN", True):
            log.info(f"  MAKER DRY-RUN: {side} {contracts}x {ticker} @{price_cents}c")
            return None
        try:
            res = self._place(ticker, side, contracts, price_cents, informed=informed) or {}
            nested = res.get("order") if isinstance(res.get("order"), dict) else {}
            order_id = nested.get("order_id") or res.get("order_id") or ""
            if order_id:
                self._orders[order_id] = {"ticker": ticker, "side": side, "price": price_cents,
                                          "contracts": contracts, "strategy": strategy,
                                          "placed_at": time.time(),
                                          "max_age_s": max_age_s or self._max_age_seconds}
                self._save()
                log.info(f"  MAKER PLACED: {side} {contracts}x {ticker} @{price_cents}c (id={order_id[:12]})")
            return order_id
        except Exception as e:
            log.error(f"  MAKER ORDER FAILED: {e}")
            return None

    def _fills_by_order(self):
        """order_id -> total filled count (best-effort from get_fills). {} on any error."""
        try:
            raw = self._api().get_fills() or {}
            out = {}
            for f in raw.get("fills", []):
                oid = f.get("order_id", "")
                if oid:
                    out[oid] = out.get(oid, 0.0) + float(f.get("count", 0) or 0)
            return out
        except Exception:
            return {}

    def _record_fill(self, info, filled):
        log.info(f"  MAKER FILLED: {info['side']} {filled:.0f}x {info['ticker']} @{info['price']}c")
        try:
            import forward_test_ledger as _ftl
            _ftl.record_entry(info.get("strategy") or "MAKER", info["ticker"], info["side"],
                              info["price"] / 100.0, filled, venue="kalshi", source="live")
        except Exception:
            pass

    def check_and_manage(self):
        """Record fills, cancel stale, reprice on book moves. Call each cron cycle (also picks up
        orders placed in previous cycles, since state persists)."""
        if not self._orders:
            return
        fills = self._fills_by_order()
        changed = False
        for oid, info in list(self._orders.items()):
            filled = fills.get(oid, 0)
            if filled > 0:                                   # real fill -> record, stop tracking
                self._record_fill(info, filled)
                self._orders.pop(oid, None); changed = True
                continue
            if time.time() - info["placed_at"] > info["max_age_s"]:
                try:
                    self._cancel(oid, info.get("ticker"))
                    log.info(f"  MAKER CANCEL (stale): {info['ticker']} @{info['price']}c")
                except Exception as e:
                    log.debug(f"  cancel failed {oid}: {e}")
                self._orders.pop(oid, None); changed = True
                continue
            book = MARKET_STATE.get_book_if_fresh(info["ticker"])
            if book:
                if self._check_reprice(oid, info, book):
                    changed = True
        if changed:
            self._save()

    def _check_reprice(self, oid, info, book):
        """Returns True if the order set changed (repriced or cancelled)."""
        best_bid = book.best_yes_bid if info["side"] == "yes" else book.best_no_bid
        if best_bid is None:
            return False
        cur = info["price"]
        if cur < best_bid - 2:
            self._replace_order(oid, info, best_bid + 1)          # book moved up -> improve
            return True
        elif cur > best_bid + 5:                                   # book moved down -> edge likely gone
            try:
                self._cancel(oid, info.get("ticker"))
                log.info(f"  MAKER CANCEL (book moved): {info['ticker']} our={cur}c best_bid={best_bid}c")
            except Exception:
                pass
            self._orders.pop(oid, None)
            return True
        return False

    def _replace_order(self, oid, info, new_price):
        try:
            self._cancel(oid, info.get("ticker"))
            self._orders.pop(oid, None)
            if self.place_maker_order(info["ticker"], info["side"], info["contracts"], new_price,
                                      strategy=info.get("strategy", ""), max_age_s=info["max_age_s"]):
                log.info(f"  MAKER REPRICED: {info['ticker']} {info['price']}c -> {new_price}c")
        except Exception as e:
            log.debug(f"  maker replace failed: {e}")

    def cancel_all(self, ticker=None):
        for oid, info in list(self._orders.items()):
            if ticker and info["ticker"] != ticker:
                continue
            try:
                self._cancel(oid, info.get("ticker"))
            except Exception:
                pass
            self._orders.pop(oid, None)
        self._save()

    @property
    def active_orders(self):
        return dict(self._orders)


if __name__ == "__main__":
    # 1. no_trade when fees eat the edge (tiny edge on a cheap contract).
    p = build_execution_plan("KXA", "yes", edge_pct=1.0, price_cents=8, contracts=1, hours_left=24)
    assert p.action == "no_trade", p
    # 2. maker when spread is wide and no urgency.
    MARKET_STATE.update_book("KXB", {"orderbook": {"yes": [{"price": 40, "size": 50}],
                                                   "no": [{"price": 40, "size": 50}]}})  # spread 20 -> too wide
    MARKET_STATE.update_book("KXC", {"orderbook": {"yes": [{"price": 45, "size": 50}],
                                                   "no": [{"price": 47, "size": 50}]}})  # ask=53, spread 8
    p = build_execution_plan("KXC", "yes", edge_pct=15.0, price_cents=50, contracts=1, hours_left=24)
    assert p.action == "maker", p
    # 3. taker when urgent even with spread.
    p = build_execution_plan("KXC", "yes", edge_pct=15.0, price_cents=50, contracts=1, hours_left=1)
    assert p.action == "taker", p
    # 4. Maker lifecycle with an injected stub + temp state file (dry-run OFF for this check only).
    import tempfile
    _cfg.EXEC_DRY_RUN = False
    sf = tempfile.mktemp(suffix=".json")

    class _Stub:
        def __init__(s): s.cancelled = []; s._fills = []
        def place_order(s, t, side, n, p): return {"order": {"order_id": f"oid_{t}_{p}"}}
        def cancel_order(s, oid, ticker=None): s.cancelled.append(oid); return {}
        def get_fills(s): return {"fills": s._fills}

    # Capture ledger writes instead of touching the real ledger.
    import forward_test_ledger as _ftl
    _recorded = []
    _ftl.record_entry = lambda *a, **k: _recorded.append((a, k))

    stub = _Stub()
    m = MakerOrderManager(api=stub, state_file=sf)
    oid = m.place_maker_order("KXC", "yes", 7, 46, strategy="WEATHER")
    assert oid and m.active_orders, "should place + track"
    # Persistence: a fresh manager on the same file sees the resting order (cron survives).
    m2 = MakerOrderManager(api=stub, state_file=sf)
    assert oid in m2.active_orders, "order must persist across processes"
    # Fill recorded on ACTUAL fill (7 contracts, at the maker price), then untracked.
    stub._fills = [{"order_id": oid, "count": 7}]
    m2.check_and_manage()
    assert not m2.active_orders, "filled order must be recorded + untracked"
    assert _recorded and _recorded[0][0][4] == 7, f"fill must record 7 contracts: {_recorded}"
    # Stale order with no fill -> cancelled.
    m3 = MakerOrderManager(api=stub, state_file=sf, max_age_seconds=0)
    m3.place_maker_order("KXD", "no", 3, 20)
    m3.check_and_manage()
    assert not m3.active_orders and stub.cancelled, "stale order must be cancelled"
    _cfg.EXEC_DRY_RUN = True
    import os as _os
    _os.path.exists(sf) and _os.remove(sf)
    print("execution_policy self-check: PASS")
