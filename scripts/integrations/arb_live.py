"""Cross-venue arb (Polymarket US <-> Kalshi) LIVE promotion path. Default OFF.

vaughan_arb_paper stays the paper desk. This module is the ONLY way an arb pair can
become venue orders, and every gate below must hold BEFORE either leg is placed
(any miss → a refused row and zero orders):

  1 VALEDIA_ARB_LIVE on                        (env / ~/.openclaw/.env; default OFF)
  2 trading halt off                           (valedia_config.trading_halted)
  3 venue_writer("kalshi") == venue_writer("polymarket") == "valedia"
  4 POLYMARKET_US_LIVE on for the Poly leg     (valedia_config.polymarket_us_live_enabled)
  5 backtest-first proof: a backtest_gate.py JSON report, verdict PASS, category ARB,
    no older than VALEDIA_ARB_BACKTEST_MAX_AGE_H (168) at VALEDIA_ARB_BACKTEST_REPORT
    (default data/arb-live/backtest_arb.json). Missing / stale / FAIL → refuse.
  6 allowlisted pair only (exact vaughan_arb_paper.PAIRS slug+ticker; never heuristic)
  7 Kalshi ticker family not fenced
  8 fee-true edge + paper risk limits (vaughan fee_true/_risk: min edge, daily exposure/loss)
  9 size ≤ VALEDIA_ARB_MAX_USD (default 25; can only shrink — vaughan MAX_TRADE 25 still applies)
 10 Kalshi leg: informed_before_create.create_preflight (full informed, IOC=taker role)
    + nimble_wiring.gate (prediction asset class)
 11 Poly leg: informed_before_create.poly_reasons (the US create bar)

Both-legs-or-none (documented, paper-simulated in test_arb_live.py):
  leg 1 Kalshi IOC buy at the quoted price → fill read (POST-ack is never a fill).
        0 filled → cancel leg 1 (no-op for IOC) → NONE_LEG1_UNFILLED, no exposure.
  leg 2 Poly limit buy for the leg-1 filled qty at the quoted price, polled until
        VALEDIA_ARB_LEG_TIMEOUT_S (10s). Full fill → BOTH_FILLED.
        Otherwise cancel leg 2, cancel leg 1 (any remainder), then hedge the unmatched
        leg-1 contracts with hedge_fn → LEG2_FAILED_LEG1_HEDGED; with no hedge_fn (the
        default — no automatic Kalshi unwind is wired) → LEG2_FAILED_LEG1_UNHEDGED and an
        alert line in the live log (bounded by the size cap; close manually).
Fills are booked fee-true via forward_test_ledger (Kalshi taker fee, Poly fill).
No flag is flipped here. No keys are read here (the venue clients own that).
"""
import json
import os
import time
from datetime import datetime, timezone

import valedia_config as vc

FLAG = "VALEDIA_ARB_LIVE"
DEFAULT_MAX_USD = 25.0
DEFAULT_LEG_TIMEOUT_S = 10.0
DEFAULT_BACKTEST_MAX_AGE_H = 168.0
POLL_S = 0.5
STRATEGY = "ARB"
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_REPORT = os.path.join(_ROOT, "data", "arb-live", "backtest_arb.json")
DEFAULT_LOG = os.path.join(_ROOT, "data", "arb-live", "legs.jsonl")


def _env(env):
    return os.environ if env is None else env


def _on(env, name):
    if env is None:
        return bool(vc._env_flag(name, False))
    return str(env.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _num(env, name, default):
    try:
        return float(_env(env).get(name) or default)
    except (TypeError, ValueError):
        return float(default)


def enabled(env=None):
    return _on(env, FLAG)


def poly_live(env=None):
    if env is None:
        return bool(vc.polymarket_us_live_enabled())
    return bool(vc.POLYMARKET_US_LIVE) or _on(env, "POLYMARKET_US_LIVE")


def backtest_proof(path=None, now=None, max_age_h=None):
    """(ok, reason). A PASS report from backtest_gate.py for category ARB, recent enough."""
    path = path or DEFAULT_REPORT
    if not os.path.isfile(path):
        return False, "backtest_missing"
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return False, "backtest_unreadable"
    if not isinstance(doc, dict) or doc.get("tool") != "backtest_gate":
        return False, "backtest_not_gate_report"
    if doc.get("verdict") != "PASS":
        return False, "backtest_not_pass"
    cand = doc.get("candidate") if isinstance(doc.get("candidate"), dict) else {}
    cats = [str(doc.get("category") or ""), str(cand.get("name") or "")]
    cats += [str(k) for k in (cand.get("by_category") or {})]
    if not any("ARB" in c.upper() for c in cats):
        return False, "backtest_not_arb"
    age = (now or time.time()) - os.path.getmtime(path)
    if age > float(max_age_h or DEFAULT_BACKTEST_MAX_AGE_H) * 3600.0:
        return False, "backtest_stale"
    return True, "backtest_pass"


def allowlisted(cand):
    import vaughan_arb_paper as va
    if cand.get("source") != "allowlist" or cand.get("requires_review"):
        return False
    key = (cand.get("poly_slug"), cand.get("kalshi_ticker"))
    return any((p["polymarket_slug"], p["kalshi_ticker"]) == key for p in va.PAIRS)


def _read_log(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _append(path, row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _row(cand, status, **kw):
    row = {"ts": datetime.now(timezone.utc).isoformat(), "source": "arb_live",
           "strategy": STRATEGY, "poly_slug": cand.get("poly_slug"),
           "kalshi_ticker": cand.get("kalshi_ticker"), "status": status,
           "orders": 0, "filled": False, "live": status not in ("OFF", "REFUSED")}
    row.update(kw)
    return row


def kalshi_leg(cand, quote, n):
    """The Kalshi leg as an allocator-shaped opp (informed fields come from the candidate)."""
    import kalshi_fee_map as kfm
    side = str(cand.get("kal_side") or quote.get("kal_side") or "NO").lower()
    px = float(cand.get("raw_kal"))
    opp = {"ticker": cand.get("kalshi_ticker"), "side": side, "price": px,
           "series": kfm.series_from_ticker(cand.get("kalshi_ticker")),
           "type": "PREDICTION_PAIR", "strategy": "PREDICTION_PAIR", "desk": "kalshi",
           "edge": round(float(quote["edge_usd"]) / max(float(quote["contracts"]), 1e-9), 6)}
    opp.update(cand.get("kalshi_informed") or {})
    return opp, max(1, min(99, int(round(px * 100))))


def poly_body(cand, quote, n):
    side = str(cand.get("poly_side") or quote.get("poly_side") or "YES").upper()
    body = {"marketSlug": cand.get("poly_slug"),
            "intent": "ORDER_INTENT_BUY_LONG" if side == "YES" else "ORDER_INTENT_BUY_SHORT",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": "%.2f" % float(cand.get("raw_poly")), "currency": "USD"},
            "quantity": int(n), "category": "ARB"}
    body.update(cand.get("poly_informed") or {})
    return body


def gates(cand, env=None, now=None):
    """Every pre-order gate. (ok, reason, ctx). Nothing is placed here."""
    import vaughan_arb_paper as va
    if not enabled(env):
        return False, "flag_off", {}
    if vc.trading_halted():
        return False, "halt", {}
    for venue in ("kalshi", "polymarket"):
        if vc.venue_writer(venue) != "valedia":
            return False, "writer_%s_%s" % (venue, vc.venue_writer(venue)), {}
    if not poly_live(env):
        return False, "poly_live_off", {}
    ok, why = backtest_proof(_env(env).get("VALEDIA_ARB_BACKTEST_REPORT") or None, now=now,
                             max_age_h=_num(env, "VALEDIA_ARB_BACKTEST_MAX_AGE_H",
                                            DEFAULT_BACKTEST_MAX_AGE_H))
    if not ok:
        return False, why, {}
    if not allowlisted(cand):
        return False, "not_allowlisted", {}
    if vc.ticker_family_fenced(cand.get("kalshi_ticker")):
        return False, "fenced_series", {}
    log_path = _env(env).get("VALEDIA_ARB_LIVE_LOG") or DEFAULT_LOG
    state = va.state_from_log(_read_log(log_path), _num(env, "VALEDIA_ARB_EQUITY", 1000.0))
    limits = va._limits()
    # VALEDIA_ARB_MAX_USD can only shrink: vaughan MAX_TRADE (25) still bounds _size_for.
    size = min(va._size_for(cand, state, limits), _num(env, "VALEDIA_ARB_MAX_USD", DEFAULT_MAX_USD))
    quote = va.fee_true(cand.get("raw_poly"), cand.get("raw_kal"), size)
    why = va._risk(quote, state, limits)
    if why:
        return False, why, {}
    quote = dict(quote, kal_side=cand.get("kal_side"), poly_side=cand.get("poly_side"))
    n = int(float(quote["contracts"]))
    if n < 1:
        return False, "size_too_small", {}
    import informed_before_create as ibc
    import nimble_wiring as nw
    kopp, kcents = kalshi_leg(cand, quote, n)
    pre = ibc.create_preflight(kopp, n, kcents, order_type="ioc")
    if not pre["ok"]:
        return False, "kalshi_preflight:" + ",".join(pre["reasons"]), {}
    body = poly_body(cand, quote, n)
    preasons = ibc.poly_reasons(body)
    if preasons:
        return False, "poly_preflight:" + ",".join(preasons), {}
    nv = nw.gate(kopp, pre["informed"], source="arb_live",
                 extra={"fee_true_edge": quote.get("edge_pct"), "clock_ok": True})
    if not nv.get("allow"):
        return False, nw.why(nv), {"nimble": nv}
    return True, "pass", {"quote": quote, "n": n, "kopp": kopp, "kcents": kcents,
                          "informed": pre["informed"], "body": body, "nimble": nv,
                          "log": log_path, "size": size}


def _wait_poly(client, ack, need, timeout, clock, sleep, fills_fn):
    import forward_test_ledger as ftl
    oid = ack.get("id")
    get = fills_fn or (lambda: client.get_order(oid))
    deadline = clock() + timeout
    fill = {"qty": 0.0, "complete": False}
    while True:
        fill, _ = ftl.resolve_polymarket_fill(ack, get)
        if fill.get("complete") and float(fill.get("qty") or 0) >= need:
            return fill
        if clock() >= deadline:
            return fill
        sleep(POLL_S)


def execute(cand, ctx, kalshi=None, poly=None, hedge_fn=None, env=None,
            clock=time.monotonic, sleep=time.sleep, poly_fills_fn=None):
    """Both-legs-or-none. Only called after gates() passed."""
    import forward_test_ledger as ftl
    if kalshi is None:
        import kalshi_auth as kalshi
    if poly is None:
        import polymarket_us_client as pmu
        poly = pmu.PolymarketUSClient()
    n, quote, tk = ctx["n"], ctx["quote"], cand.get("kalshi_ticker")
    kside = ctx["kopp"]["side"]
    timeout = _num(env, "VALEDIA_ARB_LEG_TIMEOUT_S", DEFAULT_LEG_TIMEOUT_S)
    base = {"n": n, "size_usd": ctx["size"], "edge_pct": quote.get("edge_pct"),
            "nimble": ctx.get("nimble")}
    k_ack = kalshi.create_order(tk, kside, n, ctx["kcents"], order_type="ioc",
                                informed=ctx["informed"])
    kfill, kpay = ftl.resolve_kalshi_fill(k_ack, kalshi.get_fills)
    k_oid = kfill.get("order_id")
    k_qty = int(float(kfill.get("qty") or 0)) if kfill.get("complete") else 0
    orders = 1
    if k_qty < 1:
        if k_oid:
            _safe(kalshi.cancel_order, k_oid, tk)
        return _row(cand, "NONE_LEG1_UNFILLED", orders=orders, **base)
    _safe(ftl.book_kalshi_fill, STRATEGY, tk, kside, k_ack, intended_source="live",
          fallback_price=ctx["kcents"] / 100.0, fallback_contracts=k_qty, fills_payload=kpay)
    body = dict(ctx["body"], quantity=k_qty)
    p_ack, p_qty, err = {}, 0, ""
    deadline = clock() + timeout
    try:
        p_ack = poly.create_order(body) or {}
        orders += 1
        pfill = _wait_poly(poly, p_ack, k_qty, max(0.0, deadline - clock()), clock, sleep,
                           poly_fills_fn)
        p_qty = int(float(pfill.get("qty") or 0)) if pfill.get("complete") else 0
    except Exception as e:
        err = str(e)[:120]
    if p_qty >= k_qty:
        _safe(ftl.book_polymarket_fill, STRATEGY, cand.get("poly_slug"), body["intent"], p_ack,
              intended_source="live", fallback_price=float(cand.get("raw_poly")),
              fallback_size=p_qty)
        return _row(cand, "BOTH_FILLED", orders=orders, filled=True, both_filled=True, k_qty=k_qty,
                    p_qty=p_qty, **base)
    if p_ack.get("id"):
        _safe(poly.cancel_order, p_ack.get("id"))
    if k_oid:
        _safe(kalshi.cancel_order, k_oid, tk)
    unmatched = k_qty - p_qty
    # filled=True: leg-1 exposure exists, so it counts toward the daily exposure limit.
    kw = dict(orders=orders, filled=True, both_filled=False, k_qty=k_qty, p_qty=p_qty,
              unmatched=unmatched, error=err,
              leg1_cancelled=bool(k_oid), leg2_cancelled=bool(p_ack.get("id")), **base)
    if hedge_fn is not None:
        h = _safe(hedge_fn, tk, kside, unmatched)
        return _row(cand, "LEG2_FAILED_LEG1_HEDGED" if h is not None else
                    "LEG2_FAILED_LEG1_UNHEDGED", hedge=h, alert=h is None, **kw)
    return _row(cand, "LEG2_FAILED_LEG1_UNHEDGED", alert=True, **kw)


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:
        return None


def promote(cand, env=None, now=None, **exec_kw):
    """Gate, then (only if every gate passes) execute both legs. Always returns a row."""
    try:
        ok, why, ctx = gates(cand, env=env, now=now)
    except Exception as e:
        ok, why, ctx = False, "gate_error:" + type(e).__name__, {}
    if not ok:
        row = _row(cand, "OFF" if why == "flag_off" else "REFUSED", reason=why)
        if ctx.get("nimble"):
            row["nimble"] = ctx["nimble"]
        return row
    row = execute(cand, ctx, env=env, **exec_kw)
    row["reason"] = "pass"
    _append(ctx["log"], row)
    return row


def maybe_promote(cand, env=None, **kw):
    """vaughan_arb_paper hook. Flag OFF → None (paper path untouched, nothing imported/placed)."""
    if not enabled(env):
        return None
    return promote(cand, env=env, **kw)
