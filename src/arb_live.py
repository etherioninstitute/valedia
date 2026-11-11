"""Cross-venue arb (Polymarket US <-> Kalshi) LIVE promotion path. Default OFF.

vaughan_arb_paper stays the paper desk. This module is the ONLY way an arb pair can
become venue orders, and every gate below must hold BEFORE either leg is placed
(any miss → a refused row and zero orders):

  1 VALEDIA_ARB_LIVE on                        (env / ~/.hermes/secrets/trading.env; default OFF)
  2 trading halt off                           (valedia_config.trading_halted)
  3 venue_writer("kalshi") == venue_writer("polymarket") == "valedia"
  4 POLYMARKET_US_LIVE on for the Poly leg     (valedia_config.polymarket_us_live_enabled)
  5 backtest-first proof: a backtest_gate.py JSON report (`--category ARB`), verdict PASS,
    category EXACTLY ARB (or VALEDIA_ARB_CATEGORY), generated_at no older than
    VALEDIA_ARB_BACKTEST_MAX_AGE_H (168) at VALEDIA_ARB_BACKTEST_REPORT (default
    data/arb-live/backtest_arb.json). Missing / no generated_at / stale / FAIL → refuse.
    Self-comparison refused: baseline/candidate names must differ, and their policies (if the
    report carries `policies`) or else their replays must differ.
  6 allowlisted pair only (exact vaughan_arb_paper.PAIRS slug+ticker; never heuristic)
  7 Kalshi ticker family not fenced
  8 fee-true edge + paper risk limits (vaughan fee_true/_risk: min edge, daily exposure/loss)
  9 size ≤ VALEDIA_ARB_MAX_USD (default 25; can only shrink — vaughan MAX_TRADE 25 still applies)
 10 Kalshi leg: informed_before_create.create_preflight (full informed, IOC=taker role)
    + nimble_wiring.gate (prediction asset class)
 11 Poly leg: informed_before_create.poly_reasons (the US create bar)
 12 the paper desk's Ornith PASS (vaughan ask_ornith, CE-1 fail-closed) for this pair/quote

Both-legs-or-none (documented, paper-simulated in test_arb_live.py):
  leg 1 Kalshi IOC buy at the quoted price → fill read (POST-ack is never a fill).
        0 filled → cancel leg 1 (no-op for IOC) → NONE_LEG1_UNFILLED, no exposure.
  leg 2 Poly limit buy for the leg-1 filled qty at the quoted price, polled until
        VALEDIA_ARB_LEG_TIMEOUT_S (10s). Full fill → BOTH_FILLED.
        Otherwise cancel leg 2, cancel leg 1 (any remainder), then hedge the unmatched
        leg-1 contracts with hedge_fn → LEG2_FAILED_LEG1_HEDGED; with no hedge_fn (the
        default — no automatic Kalshi unwind is wired) → LEG2_FAILED_LEG1_UNHEDGED and an
        alert line in the live log (bounded by the size cap; close manually).
  Any exception after leg 1 → POST_LEG1_ERROR_*: same cancel/hedge path, alert, logged.
  Leg-1 POST exception → LEG1_ERROR (exposure unknown: counted + alert, no leg 2).
  Leg-1 ack without fill fields and an unreadable fills endpoint → LEG1_UNKNOWN (counted at
  the order size, alert, cancel attempted, no leg 2). An unknown Poly fill always alerts.
Fills are booked fee-true via forward_test_ledger (Kalshi taker fee; Poly from the venue
payload incl. partials; no payload but an order id → UNRECONCILED).
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
ARB_CATEGORY = "ARB"  # exact backtest_gate --category; VALEDIA_ARB_CATEGORY overrides
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


def backtest_proof(path=None, now=None, max_age_h=None, category=None):
    """(ok, reason). backtest_gate.py report: verdict PASS, category EXACTLY `category`
    (default ARB; backtest_gate --category ARB records it), fresh by its own generated_at.
    Missing / unparseable generated_at → stale. File mtime is never used."""
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
    if str(doc.get("category") or "") != str(category or ARB_CATEGORY):
        return False, "backtest_not_arb"
    if _self_compare(doc):
        return False, "backtest_self_compare"
    try:
        gen = datetime.fromisoformat(str(doc.get("generated_at")).replace("Z", "+00:00"))
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False, "backtest_stale"
    age = (now or time.time()) - gen.timestamp()
    if age < -300 or age > float(max_age_h or DEFAULT_BACKTEST_MAX_AGE_H) * 3600.0:
        return False, "backtest_stale"
    return True, "backtest_pass"


def _canon(x):
    return json.dumps(x, sort_keys=True, default=str)


def _self_compare(doc):
    """A candidate compared to itself proves nothing: names must differ, and the policies
    (when the report carries them) or else the replays (minus the name) must differ."""
    b = doc.get("baseline") if isinstance(doc.get("baseline"), dict) else {}
    c = doc.get("candidate") if isinstance(doc.get("candidate"), dict) else {}
    if not b or not c or str(b.get("name") or "") == str(c.get("name") or ""):
        return True
    pol = doc.get("policies") if isinstance(doc.get("policies"), dict) else {}
    if pol.get("baseline") is not None and pol.get("candidate") is not None:
        strip = lambda d: {k: v for k, v in dict(d).items() if k not in ("name", "description")}
        return _canon(strip(pol["baseline"])) == _canon(strip(pol["candidate"]))
    return _canon({k: v for k, v in b.items() if k != "name"}) == \
        _canon({k: v for k, v in c.items() if k != "name"})


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


def gates(cand, env=None, now=None, paper_row=None, ornith_post=None):
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
                                            DEFAULT_BACKTEST_MAX_AGE_H),
                             category=_env(env).get("VALEDIA_ARB_CATEGORY") or ARB_CATEGORY)
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
    # The paper desk's Ornith PASS (CE-1 fail-closed) on this quote: reuse the paper row's
    # verdict for this exact pair, else ask Ornith with the paper desk's own conditions.
    if not _paper_pass(cand, paper_row):
        verdict, _model = va.ask_ornith(va._conds(quote, limits), post_fn=ornith_post)
        if verdict != "pass":
            return False, "ornith:" + verdict, {}
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


def _paper_pass(cand, row):
    r = row if isinstance(row, dict) else {}
    want = "%s|%s" % (cand.get("poly_slug") or "", cand.get("kalshi_ticker") or "")
    return r.get("ornith") == "pass" and r.get("ticker") == want


def _wait_poly(client, ack, need, timeout, clock, sleep, fills_fn):
    """(fill, payload) — payload is the last venue read (None if every read failed)."""
    import forward_test_ledger as ftl
    oid = ack.get("id")
    get = fills_fn or (lambda: client.get_order(oid))
    deadline = clock() + timeout
    fill, last = {"qty": 0.0, "complete": False}, None
    while True:
        fill, payload = ftl.resolve_polymarket_fill(ack, get)
        last = payload if payload is not None else last
        if fill.get("complete") and float(fill.get("qty") or 0) >= need:
            return fill, last
        if clock() >= deadline:
            return fill, last
        sleep(POLL_S)


def _book_poly(ftl, cand, body, ack, qty, payload):
    """Venue payload → fee-true booking of what filled (partial included). No payload but an
    order id → UNRECONCILED row with that id. Confirmed zero fill → nothing to book."""
    if not ack.get("id") or (payload is not None and qty <= 0):
        return None
    return _safe(ftl.book_polymarket_fill, STRATEGY, cand.get("poly_slug"), body.get("intent"),
                 ack, intended_source="live", fallback_price=float(cand.get("raw_poly")),
                 fallback_size=qty if payload is not None else body.get("quantity"),
                 trades_payload=payload)


def execute(cand, ctx, kalshi=None, poly=None, hedge_fn=None, env=None,
            clock=time.monotonic, sleep=time.sleep, poly_fills_fn=None):
    """Both-legs-or-none. Only called after gates() passed. Never raises after leg 1."""
    if kalshi is None:
        import kalshi_auth as kalshi
    if poly is None:
        import polymarket_us_client as pmu
        poly = pmu.PolymarketUSClient()
    n, tk = ctx["n"], cand.get("kalshi_ticker")
    base = {"n": n, "size_usd": ctx["size"], "edge_pct": ctx["quote"].get("edge_pct"),
            "nimble": ctx.get("nimble")}
    try:
        k_ack = kalshi.create_order(tk, ctx["kopp"]["side"], n, ctx["kcents"], order_type="ioc",
                                    informed=ctx["informed"])
    except Exception as e:  # ambiguous POST: exposure unknown → counted, alerted, no leg 2
        return _row(cand, "LEG1_ERROR", orders=1, filled=True, alert=True,
                    exposure_unknown=True, error=str(e)[:120], **base)
    st = {"orders": 1, "k_oid": None, "k_qty": None, "p_ack": {}, "p_qty": 0, "err": ""}
    try:
        return _after_leg1(cand, ctx, kalshi, poly, env, clock, sleep, poly_fills_fn,
                           k_ack or {}, st, base, hedge_fn)
    except Exception as e:  # anything unexpected after leg 1: cancel both, alert, count exposure
        st["err"] = (st["err"] + " | " if st["err"] else "") + "%s: %s" % (
            type(e).__name__, str(e)[:100])
        return _unwind(cand, ctx, kalshi, poly, hedge_fn, st, base, "POST_LEG1_ERROR")


def _after_leg1(cand, ctx, kalshi, poly, env, clock, sleep, poly_fills_fn, k_ack, st, base,
                hedge_fn):
    import forward_test_ledger as ftl
    tk, kside = cand.get("kalshi_ticker"), ctx["kopp"]["side"]
    nested = k_ack.get("order") if isinstance(k_ack.get("order"), dict) else {}
    st["k_oid"] = nested.get("order_id") or k_ack.get("order_id")
    kfill, kpay = ftl.resolve_kalshi_fill(k_ack, kalshi.get_fills)
    st["k_oid"] = kfill.get("order_id") or st["k_oid"]
    st["k_qty"] = k_qty = int(float(kfill.get("qty") or 0)) if kfill.get("complete") else 0
    if k_qty < 1:
        if st["k_oid"]:
            _safe(kalshi.cancel_order, st["k_oid"], tk)
        explicit = any(k in {**k_ack, **nested} for k in ("fill_count", "filled_count",
                                                          "filled_contracts"))
        if not explicit and kpay is None:  # no fill fields AND no fills read: UNKNOWN, not 0
            return _row(cand, "LEG1_UNKNOWN", orders=1, filled=True, alert=True,
                        exposure_unknown=True, possible_contracts=ctx["n"],
                        leg1_cancelled=bool(st["k_oid"]), **base)
        return _row(cand, "NONE_LEG1_UNFILLED", orders=1, **base)
    _safe(ftl.book_kalshi_fill, STRATEGY, tk, kside, k_ack, intended_source="live",
          fallback_price=ctx["kcents"] / 100.0, fallback_contracts=k_qty, fills_payload=kpay)
    body = dict(ctx["body"], quantity=k_qty)
    deadline = clock() + _num(env, "VALEDIA_ARB_LEG_TIMEOUT_S", DEFAULT_LEG_TIMEOUT_S)
    p_pay = None
    try:
        st["p_ack"] = poly.create_order(body) or {}
        st["orders"] += 1
        pfill, p_pay = _wait_poly(poly, st["p_ack"], k_qty, max(0.0, deadline - clock()), clock,
                                  sleep, poly_fills_fn)
        st["p_qty"] = int(float(pfill.get("qty") or 0)) if pfill.get("complete") else 0
    except Exception as e:
        st["err"], st["p_unknown"] = str(e)[:120], True  # create/poll raised: Poly fill unknown
    if st["p_qty"] >= k_qty:
        _book_poly(ftl, cand, body, st["p_ack"], st["p_qty"], p_pay)
        return _row(cand, "BOTH_FILLED", orders=st["orders"], filled=True, both_filled=True,
                    k_qty=k_qty, p_qty=st["p_qty"], **base)
    if st["p_ack"].get("id"):
        _safe(poly.cancel_order, st["p_ack"]["id"])
        get = poly_fills_fn or (lambda: poly.get_order(st["p_ack"]["id"]))
        fill, pay = ftl.resolve_polymarket_fill(st["p_ack"], get)  # final read after the cancel
        if pay is not None:
            p_pay = pay
            st["p_qty"] = int(float(fill.get("qty") or 0)) if fill.get("complete") else 0
        st["p_unknown"] = p_pay is None  # every Poly read failed: qty 0 is a guess
    _book_poly(ftl, cand, body, st["p_ack"], st["p_qty"], p_pay)
    return _unwind(cand, ctx, kalshi, poly, hedge_fn, st, base, "LEG2_FAILED", cancel_poly=False)


def _unwind(cand, ctx, kalshi, poly, hedge_fn, st, base, prefix, cancel_poly=True):
    """Cancel leg 2 + leg-1 remainder, hedge unmatched leg-1 contracts, alert if unhedged."""
    tk = cand.get("kalshi_ticker")
    p_id = (st.get("p_ack") or {}).get("id")
    if cancel_poly and p_id:
        _safe(poly.cancel_order, p_id)
    if st.get("k_oid"):
        _safe(kalshi.cancel_order, st["k_oid"], tk)
    k_qty = st.get("k_qty")
    unmatched = None if k_qty is None else max(0, k_qty - int(st.get("p_qty") or 0))
    # filled=True: leg-1 exposure exists (or is unknown), so it counts toward daily exposure.
    p_unknown = bool(st.get("p_unknown")) or (bool(p_id) and prefix != "LEG2_FAILED")
    kw = dict(orders=st["orders"], filled=True, both_filled=False, k_qty=k_qty,
              p_qty=st.get("p_qty"), unmatched=unmatched, error=st.get("err", ""),
              poly_fill_unknown=p_unknown,
              leg1_cancelled=bool(st.get("k_oid")), leg2_cancelled=bool(p_id), **base)
    if unmatched == 0:
        return _row(cand, prefix + "_LEG1_MATCHED", alert=True, **kw)
    h = _safe(hedge_fn, tk, ctx["kopp"]["side"], unmatched) if (hedge_fn and unmatched) else None
    return _row(cand, prefix + ("_LEG1_HEDGED" if h is not None else "_LEG1_UNHEDGED"),
                hedge=h, alert=h is None or p_unknown, **kw)


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:
        return None


def promote(cand, env=None, now=None, paper_row=None, ornith_post=None, **exec_kw):
    """Gate, then (only if every gate passes) execute both legs. Always returns a row; every
    executed row (incl. errors after leg 1) is appended to the live log, so exposure counts."""
    try:
        ok, why, ctx = gates(cand, env=env, now=now, paper_row=paper_row, ornith_post=ornith_post)
    except Exception as e:
        ok, why, ctx = False, "gate_error:" + type(e).__name__, {}
    if not ok:
        row = _row(cand, "OFF" if why == "flag_off" else "REFUSED", reason=why)
        if ctx.get("nimble"):
            row["nimble"] = ctx["nimble"]
        return row
    try:
        row = execute(cand, ctx, env=env, **exec_kw)
    except Exception as e:  # defensive: execute itself does not raise after leg 1
        row = _row(cand, "EXECUTE_ERROR", filled=True, alert=True, exposure_unknown=True,
                   error="%s: %s" % (type(e).__name__, str(e)[:100]), size_usd=ctx.get("size"))
    row["reason"] = "pass"
    _safe(_append, ctx["log"], row)
    return row


def maybe_promote(cand, env=None, **kw):
    """vaughan_arb_paper hook. Flag OFF → None (paper path untouched, nothing imported/placed)."""
    if not enabled(env):
        return None
    return promote(cand, env=env, **kw)
