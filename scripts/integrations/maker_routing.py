"""Maker-first routing for an ALREADY-APPROVED Kalshi allocation: decides HOW, never WHETHER.

Called by master_allocator.execute_allocations after vetting. One approved allocation is
still at most one order; this module only picks maker (resting limit at/inside the bid)
vs taker (limit at the ask). No network, no orders here.

Rule (decide):
  maker  when maker is fee-better  [fee map maker_viable, OR the legacy wide-spread
         execution_policy plan already says maker — i.e. maker strictly better by the spread]
         AND maker EV clears ITS hurdle: maker_ev.net > dome_extra(P_maker)
             (the same maker floor informed_before_create applies)
         AND a resting price exists (bid known; price = bid+1 when that is still < ask)
         AND seconds-to-close >= MAKER_MIN_SECONDS_TO_CLOSE
         AND resting orders can be managed (MakerOrderManager present, EXEC_DRY_RUN False)
         AND taker does not beat maker by the taker hurdle (kalshi_profit.route taker_clears).
  taker  when taker EV clears ITS higher hurdle: taker_ev.net >= TAKER_HURDLE + dome_extra(P)
         (on a maker_viable series also taker_ev.net >= maker_ev.net + hurdle), and maker was
         not chosen (not viable / no resting price / closes too soon / manager not live).
  none   neither clears. VALEDIA_MAKER_FIRST_ENFORCE OFF (default) → legacy action, annotated
         "legacy". ON → SKIP_NO_ROUTE (removes orders, never adds).

Taker floor (UNCONDITIONAL): whatever the route, an order placed as taker (action taker, or a
"maker" priced at/through the ask) must pass kalshi_profit.taker_clears_floor — the same rule
create_preflight applies to role=taker — else SKIP_TAKER_HURDLE. The chosen role is passed to
create_preflight (a["role"]) and ev_preflight is the chosen role's EV, so both judge the same
order. Exception: a fee-paying maker (not maker_viable) keeps create_preflight's stricter taker
floor, because judging it at the maker floor could add orders.

Time threshold: MAKER_MIN_SECONDS_TO_CLOSE = 2h, measured to the session close that
session_clocks reads (market close_time / closeTime = when the venue stops accepting
orders; fallback hours_to_resolve). It equals execution_policy's urgency=high boundary
(hours_left <= 2 → taker): a resting order needs time to fill before the book closes.

No threshold is loosened: hurdles come from kalshi_profit (TAKER_HURDLE, dome_extra).
"""
import os
from datetime import datetime, timezone

MAKER_MIN_SECONDS_TO_CLOSE = 2 * 3600
ENFORCE_FLAG = "VALEDIA_MAKER_FIRST_ENFORCE"


def enforce_on():
    """Default OFF. Env wins; config constant if one is ever added."""
    try:
        import valedia_config as vc
        return bool(vc._env_flag(ENFORCE_FLAG, getattr(vc, ENFORCE_FLAG, False)))
    except Exception:
        env = os.environ.get(ENFORCE_FLAG)
        return str(env or "").strip().lower() in ("1", "true", "yes", "on")


def cents(v):
    """Price → int cents. <1 is dollars (Kalshi *_dollars / scanner floats), >=1 is cents."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0
    if x <= 0:
        return 0
    c = int(round(x * 100)) if x < 1 else int(round(x))
    return c if 1 <= c <= 99 else 0


def book_cents(a):
    """(bid_c, ask_c) for the traded side: top-level, then raw, then informed bid/ask."""
    a = a if isinstance(a, dict) else {}
    raw = a.get("raw") if isinstance(a.get("raw"), dict) else {}
    side = "no" if str(a.get("side") or "yes").lower() in ("no", "sell") else "yes"

    def first(*vals):
        for v in vals:
            c = cents(v)
            if c:
                return c
        return 0
    bid = first(a.get(side + "_bid"), raw.get(side + "_bid"), a.get("bid"))
    ask = first(a.get(side + "_ask"), raw.get(side + "_ask"), a.get("ask"), a.get("price"))
    return bid, ask


def maker_price_cents(bid_c, ask_c):
    """Resting price: improve the bid by 1c while it stays below the ask. None = cannot rest."""
    if not bid_c:
        return None
    px = bid_c + 1 if (not ask_c or bid_c + 1 < ask_c) else bid_c
    if ask_c and px >= ask_c:
        return None
    return px if 1 <= px <= 99 else None


def seconds_to_close(a, now=None):
    """Seconds until the session close session_clocks reads; hours_to_resolve fallback."""
    a = a if isinstance(a, dict) else {}
    now = now or datetime.now(timezone.utc)
    for m in (a.get("market"), a.get("raw")):
        if not isinstance(m, dict):
            continue
        ts = m.get("close_time") or m.get("closeTime")
        if ts:
            try:
                import session_clocks as sc
                dt = sc._parse(ts)
            except Exception:
                dt = None
            if dt is not None:
                return (dt - now).total_seconds()
    h = a.get("hours_to_resolve")
    try:
        return float(h) * 3600.0 if h is not None else None
    except (TypeError, ValueError):
        return None


def maker_live(mgr, cfg):
    return mgr is not None and not getattr(cfg, "EXEC_DRY_RUN", True)


def _net(ev):
    try:
        return float((ev or {}).get("net"))
    except (TypeError, ValueError):
        return None


def decide(rt, *, legacy_maker=False, maker_px=None, taker_px=None, secs=None, live=False):
    """rt = kalshi_profit.route dict. Returns {route, why, maker_ev, taker_ev}."""
    import kalshi_profit as kp
    rt = rt or {}
    m_net, t_net = _net(rt.get("maker_ev")), _net(rt.get("taker_ev"))
    viable = bool(rt.get("maker_viable"))
    out = {"maker_ev": m_net, "taker_ev": t_net}
    m_extra = kp.dome_extra((maker_px or 0) / 100.0) if maker_px else 0.0
    maker_clears = m_net is not None and m_net > m_extra
    taker_clears = kp.taker_clears_floor(rt, (taker_px or 0) / 100.0)
    too_soon = secs is not None and secs < MAKER_MIN_SECONDS_TO_CLOSE
    blockers = []
    if not (viable or legacy_maker):
        blockers.append("maker_not_viable")
    if not maker_clears:
        blockers.append("maker_ev_below_hurdle")
    if maker_px is None:
        blockers.append("no_resting_price")
    if too_soon:
        blockers.append("closes_in_%ds<%ds" % (int(secs), MAKER_MIN_SECONDS_TO_CLOSE))
    if not live:
        blockers.append("maker_exec_off")
    if not blockers and not (viable and taker_clears):
        out.update(route="maker", why="maker_viable" if viable else "maker_better_wide_spread")
    elif taker_clears:
        out.update(route="taker", why="taker_clears_hurdle;" + (",".join(blockers) or "beats_maker"))
    else:
        out.update(route="none", why="no_route_clears;" + ",".join(blockers or ["taker_below_hurdle"]))
    return out


def _legacy_plan(a, n, price_cents, plan_fn, side_bids):
    """execution_policy plan as master_allocator ran it (fixed: dollar bids were int()'d to 0)."""
    if plan_fn is None:
        return None
    try:
        from market_state import MARKET_STATE
        yb, nb = side_bids
        book = MARKET_STATE.update_book(a["ticker"], {"orderbook": {
            "yes": [{"price": yb, "size": 1}], "no": [{"price": nb, "size": 1}]}}) if (yb and nb) else None
        return plan_fn(a["ticker"], a["side"],
                       a.get("expected_profit", 0) / (a.get("allocation", 0) + 0.01) * 100,
                       price_cents, n, a.get("hours_to_resolve", 9999), book=book)
    except Exception:
        return None


def route_allocation(a, n, price_cents, mgr=None, cfg=None, plan_fn=None, now=None):
    """(proceed, a, out). out: action, price_cents, status/error on skip, row fields.

    Fail-closed is the caller's job (any exception here → SKIP_EV_PREFLIGHT there)."""
    import kalshi_profit as kp
    out = {"action": "taker", "price_cents": price_cents, "row": {}}
    if mgr is not None and any(i.get("ticker") == a.get("ticker")
                               for i in (getattr(mgr, "active_orders", {}) or {}).values()):
        out.update(status="SKIP_MAKER_RESTING", error="resting maker order already on ticker")
        return False, a, out
    fmap = a.get("fmap") if isinstance(a.get("fmap"), dict) else None  # same map create_preflight reads
    proceed, a = kp.annotate(a, contracts=n, fmap=fmap)
    out["row"]["ev_preflight"] = a.get("ev_preflight")
    out["row"]["maker_first"] = a.get("maker_first")
    if not proceed:
        out["status"] = "SKIP_EV_PREFLIGHT"
        return False, a, out
    raw = a.get("raw") if isinstance(a.get("raw"), dict) else {}
    bid_c, ask_c = book_cents(a)
    side_bids = (cents(a.get("yes_bid") or raw.get("yes_bid")), cents(a.get("no_bid") or raw.get("no_bid")))
    plan = _legacy_plan(a, n, price_cents, plan_fn, side_bids)
    legacy_maker = getattr(plan, "action", None) == "maker"
    live = maker_live(mgr, cfg)
    mpx = maker_price_cents(bid_c, ask_c or price_cents)
    if mpx is not None:
        mpx = min(mpx, price_cents)
    mf = a.get("maker_first") or {}
    d = decide(mf, legacy_maker=legacy_maker, maker_px=mpx, taker_px=price_cents,
               secs=seconds_to_close(a, now), live=live)
    out["row"].update(route=d["route"], route_why=d["why"],
                      maker_ev=d["maker_ev"], taker_ev=d["taker_ev"])
    if d["route"] == "maker":
        out.update(action="maker", price_cents=mpx)
    elif d["route"] == "none" and enforce_on():
        out.update(status="SKIP_NO_ROUTE", error=d["why"])
        return False, a, out
    elif d["route"] == "none":  # legacy: the pre-maker_routing decision (dollar-bid fix only)
        out["row"]["route"] = "legacy"
        if legacy_maker:
            out.update(action="maker", price_cents=plan.price_cents)
        if kp.maker_first_on() and mf.get("prefer") == "maker":
            if live:
                out["action"] = "maker"
            elif kp.should_skip_taker(mf):
                out["status"] = "SKIP_MAKER_FIRST"
                return False, a, out
    # Judge the order ACTUALLY placed. A "maker" at/through the ask fills as a taker.
    role = "taker" if out["action"] == "taker" or (ask_c and out["price_cents"] >= ask_c) else "maker"
    out["row"]["role"] = role
    if role == "taker":
        # Unconditional (not behind ENFORCE): only removes orders. Same rule create_preflight
        # applies to role=taker; existing TAKER_HURDLE + dome_extra, no new threshold.
        if not kp.taker_clears_floor(mf, out["price_cents"] / 100.0):
            out.update(status="SKIP_TAKER_HURDLE",
                       error="taker_ev %s < hurdle+dome%s" % (
                           d["taker_ev"], " (or < maker_ev+hurdle)" if mf.get("maker_viable") else ""))
            return False, a, out
        a["role"] = "taker"
        ev = mf.get("taker_ev")
    else:
        if mf.get("prefer") == "maker":
            a["role"] = "maker"
        # else (fee-paying maker on a wide spread): leave role unset so create_preflight keeps
        # the stricter taker floor; judging it at the maker floor could ADD orders.
        ev = mf.get("maker_ev")
    if isinstance(ev, dict):
        a["ev_preflight"] = out["row"]["ev_preflight"] = ev
    if role == "taker" and kp.maker_first_on() and mf.get("prefer") == "maker" \
            and kp.should_skip_taker(mf):
        out["status"] = "SKIP_MAKER_FIRST"
        return False, a, out
    return True, a, out
