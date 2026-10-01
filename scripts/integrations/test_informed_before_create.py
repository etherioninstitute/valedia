#!/usr/bin/env python3
"""INFORMED-BEFORE-CREATE — fail-closed create bar. No network, no live flips.

Run: python3 test_informed_before_create.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import informed_before_create as ibc
import kalshi_auth as ka
import kalshi_fee_map as kfm
import kalshi_profit as kp
import poly_loop
import polymarket_us_client as pmu
import valedia_config as vc
from test_poly_us import MockXport, _client, _restore as _poly_restore


def _env(name, val=None):
    prev = os.environ.get(name)
    if val is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = val
    return prev


def _restore_env(name, prev):
    if prev is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = prev


def pin_kelly(frac=0.08):
    import quant_engine as qe
    old = qe.dynamic_per_trade_cap
    qe.dynamic_per_trade_cap = lambda *a, **k: frac
    return qe, old


def unpin_kelly(qe, old):
    qe.dynamic_per_trade_cap = old


def good_kalshi(**over):
    d = {
        "edge": 0.20,
        "fv": 0.70,
        "fv_source": "model",
        "fv_confidence": True,
        "slip": 0.0,
        "bid": 0.50,
        "ask": 0.50,
        "depth": 20,
        "bankroll": 10000,
        "strategy": "MODEL",
    }
    d.update(over)
    return d


def good_poly(**over):
    d = {
        "marketSlug": "btc-100k",
        "intent": "ORDER_INTENT_BUY_LONG",
        "type": "ORDER_TYPE_LIMIT",
        "quantity": 1,
        "fv": 0.70,
        "fv_source": "model",
        "fv_confidence": True,
        "mid": 0.50,
        "fee_prob": 0.0,
        "exit_ok": True,
        "depth": 50,
        "bankroll": 10000,
        "acceptingOrders": True,
        "umaProposalTime": "2026-09-22T18:00:00+00:00",
        "challengePeriodSeconds": 7200,
    }
    d.update(over)
    return d


def _refused(fn):
    try:
        fn()
    except ibc.InformedCreateRefused as e:
        return e
    raise AssertionError("expected InformedCreateRefused")


def _quiet():
    _poly_restore()
    os.environ["VALEDIA_TRADING_HALT_FILE"] = os.path.join(
        "/tmp", "valedia-halt-missing-informed.json")
    vc.VALEDIA_TRADING_HALT = False


def test_hygiene_and_snapshot():
    _quiet()
    assert vc.VALEDIA_VERSION == "0.74.1"
    assert vc.INFORMED_BEFORE_CREATE is True
    assert vc.POLY_TAG_PRIOR_CREATE is False
    assert vc.POLYMARKET_US_LIVE is False
    assert vc.KALSHI_FEE_MAP_GATE is False
    assert vc.KALSHI_EV_PREFLIGHT_GATE is False
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.NAUTILUS_LIVE is False
    assert vc.VALEDIA_TREASURY_AUTO is False
    assert vc.VALEDIA_TRADING_HALT is False
    src = open(os.path.join(HERE, "informed_before_create.py")).read()
    for banned in ("POLYMARKET_US_LIVE = True", "POLYMARKET_US_LIVE=1",
                   "VENUE_WRITER[", "NAUTILUS_LIVE = True",
                   "VALEDIA_TREASURY_AUTO = True", "KALSHI_FEE_MAP_GATE = True"):
        assert banned not in src, banned
    prev = _env("INFORMED_BEFORE_CREATE", "0")
    prev_p = _env("POLY_TAG_PRIOR_CREATE", "1")
    prev_g = _env("KALSHI_FEE_MAP_GATE", None)
    try:
        snap = {r["name"]: r for r in vc.controls_snapshot()["items"]}
        assert snap["INFORMED_BEFORE_CREATE"]["value"] is True
        assert snap["INFORMED_BEFORE_CREATE"]["source"] == "const"
        assert snap["POLY_TAG_PRIOR_CREATE"]["value"] is False
        assert snap["POLY_TAG_PRIOR_CREATE"]["source"] == "const"
        assert snap["POLYMARKET_US_LIVE"]["value"] is False
        assert vc.KALSHI_FEE_MAP_GATE is False
        assert snap["KALSHI_FEE_MAP_GATE"]["value"] is False
        assert snap["KALSHI_FEE_MAP_GATE"]["source"] == "const"
    finally:
        _restore_env("INFORMED_BEFORE_CREATE", prev)
        _restore_env("POLY_TAG_PRIOR_CREATE", prev_p)
        _restore_env("KALSHI_FEE_MAP_GATE", prev_g)
    cfg = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "INFORMED_BEFORE_CREATE = True" in cfg
    assert "POLY_TAG_PRIOR_CREATE = False" in cfg
    assert "POLYMARKET_US_LIVE = False" in cfg


def test_bare_kalshi_create_does_not_post():
    _quiet()
    posted = []
    orig = ka.post
    ka.post = lambda ep, body=None: posted.append(ep) or {"order_id": "nope"}
    qe, old = pin_kelly()
    try:
        err = _refused(lambda: ka.create_order("KXBTCY-1", "yes", 1, 50))
        assert "fv_source" in err.reasons
        assert "fee_map_missing" not in err.reasons  # KXBTCY is on the PDF map
        assert posted == []
        err = _refused(lambda: ka.create_order("TICK-Y", "yes", 1, 50, informed=good_kalshi()))
        assert "fee_map_missing" in err.reasons
        assert posted == []
        err = _refused(lambda: ka.sell_position("KXBTCY-1", "yes", 1, 50))
        assert posted == []
        assert err.reasons
    finally:
        ka.post = orig
        unpin_kelly(qe, old)


def test_kalshi_admits_only_when_all_pass():
    _quiet()
    posted = []
    orig = ka.post
    ka.post = lambda ep, body=None: posted.append((ep, dict(body or {}))) or {
        "order_id": "ok-1"}
    qe, old = pin_kelly()
    try:
        out = ka.create_order("KXBTCY-1", "yes", 1, 50, informed=good_kalshi())
        assert posted and posted[-1][0] == "/portfolio/events/orders"
        assert posted[-1][1]["ticker"] == "KXBTCY-1"
        assert out["order_id"] == "ok-1"
        # Halt off is not enough: drop FV and the post does not happen.
        n = len(posted)
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(fv_source="tag_prior")))
        assert "tag_prior" in err.reasons
        assert len(posted) == n
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50,
            informed=good_kalshi(fv_source="mirofish", mirofish_score=50)))
        assert "mirofish_default_50" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50,
            informed=good_kalshi(mirofish_fusion=True, mirofish_score=50)))
        assert "mirofish_default_50" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50,
            informed=good_kalshi(mirofish_fusion=True)))
        assert "mirofish_default_50" in err.reasons
        bare_book = good_kalshi()
        bare_book.pop("bid")
        bare_book.pop("ask")
        err = _refused(lambda: ka.create_order("KXBTCY-1", "yes", 1, 50, informed=bare_book))
        assert "spread_missing" in err.reasons
        no_slip = good_kalshi()
        no_slip.pop("slip")
        err = _refused(lambda: ka.create_order("KXBTCY-1", "yes", 1, 50, informed=no_slip))
        assert "slip_missing" in err.reasons
        # Real model FV is not refused just because a side score is 50.
        ka.create_order(
            "KXBTCY-1", "yes", 1, 50,
            informed=good_kalshi(mirofish_score=50))
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(fv_confidence=False)))
        assert "fv_confidence" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, order_type="market",
            informed=good_kalshi()))
        assert "taker_hurdle" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(depth=0)))
        assert "depth" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 5, 50, informed=good_kalshi(bankroll=1)))
        assert "size" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(copy_mirror=True)))
        assert "copy_mirror" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(keyword_arb=True)))
        assert "keyword_arb" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(taker_longshot=True)))
        assert "taker_longshot" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(fee_blind=True)))
        assert "fee_blind" in err.reasons
    finally:
        ka.post = orig
        unpin_kelly(qe, old)


def test_fences_sports_oil_fee_gate_demote():
    _quiet()
    posted = []
    orig = ka.post
    ka.post = lambda ep, body=None: posted.append(1) or {"order_id": "x"}
    qe, old = pin_kelly()
    sports_map = {"KXNBAGAME": kfm._row("KXNBAGAME", 0.0, 0.0, "sports")}
    oil_map = {"KXWTI": kfm._row("KXWTI", 0.0, 0.0, "oil")}
    tax_map = {"KXTAX": kfm._row("KXTAX", 0.0, 1.5, "taxed")}
    prev_gate = _env("KALSHI_FEE_MAP_GATE", None)
    prev_dem = _env("KALSHI_FILL_LEDGER_DEMOTE", None)
    try:
        err = _refused(lambda: ka.create_order(
            "KXWTI-26MAR23-T99.99", "yes", 1, 50,
            informed=good_kalshi(strategy="NO_HARVEST", fmap=oil_map, series="KXWTI")))
        assert "oil_no_harvest" in err.reasons
        assert "live_fence" in err.reasons
        assert posted == []
        err = _refused(lambda: ka.create_order(
            "KXNBAGAME-1", "yes", 1, 50,
            informed=good_kalshi(series="KXNBAGAME", fmap=sports_map)))
        assert "sports_attestation" in err.reasons
        err = _refused(lambda: ka.create_order(
            "KXNBAGAME-1", "yes", 1, 50,
            informed=good_kalshi(
                series="KXNBAGAME", fmap=sports_map,
                sports_attestation={"valid": True, "geo": "fail"})))
        assert "sports_attestation" in err.reasons or "sports_geo" in err.reasons
        ka.create_order(
            "KXNBAGAME-1", "yes", 1, 50,
            informed=good_kalshi(
                series="KXNBAGAME", fmap=sports_map, sports_attestation=True,
                now="2026-09-22T18:00:00+00:00",
                market={
                    "status": "active",
                    "open_time": "2026-09-22T17:00:00+00:00",
                    "close_time": "2026-09-22T23:00:00+00:00",
                    "expected_expiration_time": "2026-09-22T20:30:00+00:00",
                    "can_close_early": True,
                    "early_close_condition": "game end",
                }))
        assert posted == [1]
        _env("KALSHI_FEE_MAP_GATE", "1")
        assert kfm.gate_on() is True
        err = _refused(lambda: ka.create_order(
            "KXTAX-1", "yes", 1, 50,
            informed=good_kalshi(series="KXTAX", fmap=tax_map)))
        assert "fee_map_gate" in err.reasons
        _env("KALSHI_FEE_MAP_GATE", None)
        assert kfm.gate_on() is False
        # Gate off does not drop a known maker-viable series. Still not a bypass
        # of the informed bar — this one has the rest of the evidence.
        ka.create_order(
            "KXTAX-1", "yes", 1, 50,
            informed=good_kalshi(series="KXTAX", fmap=tax_map))
        ledger = {"series": {"KXBTCY": {"n": 5, "score": 0.05}}, "strategy": {}}
        # Annotate skip stays flag-gated. Create refuses on the evidence itself.
        assert kp.demote_blocks("KXBTCY-1", ledger=ledger) is False
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(ledger=ledger)))
        assert "fill_ledger_demote" in err.reasons
        thin = {"series": {"KXBTCY": {"n": 4, "score": 0.9}}, "strategy": {}}
        ka.create_order("KXBTCY-1", "yes", 1, 50, informed=good_kalshi(ledger=thin))
        empty = {"series": {}, "strategy": {}}
        view = kp.ledger_view("KXBTCY-1", ledger=empty)
        assert view["empty"] is True and view["score"] is None and view["demote"] is False
        ka.create_order("KXBTCY-1", "yes", 1, 50, informed=good_kalshi(ledger=empty))
        missing = kp.ledger_view("KXBTCY-1", path="/tmp/no-such-kalshi-fill-ledger.json")
        assert missing["missing_file"] is True and missing["empty"] is True and missing["demote"] is False
        err = _refused(lambda: ka.create_order(
            "KXBTCY-1", "yes", 1, 50,
            informed=good_kalshi(fmap=None, fee_map_path="/tmp/no-such-kalshi-fee-map.json")))
        assert "fee_map_refresh" in err.reasons
        _env("KALSHI_FILL_LEDGER_DEMOTE", "1")
        assert kp.demote_blocks("KXBTCY-1", ledger=ledger) is True
    finally:
        ka.post = orig
        unpin_kelly(qe, old)
        _restore_env("KALSHI_FEE_MAP_GATE", prev_gate)
        _restore_env("KALSHI_FILL_LEDGER_DEMOTE", prev_dem)


def test_ev_floor_uses_preflight():
    _quiet()
    qe, old = pin_kelly()
    try:
        # Maker-viable, edge eaten by nothing on KXBTCY (M=0) — clears.
        assert ibc.kalshi_reasons("KXBTCY-1", "yes", 1, 50, informed=good_kalshi()) == []
        # Taker on a non-viable series must clear the higher hurdle after fee.
        fmap = {"KXCPI": kfm._row("KXCPI", 1.0, 1.0, "cpi")}
        thin = good_kalshi(series="KXCPI", fmap=fmap, edge=0.03, role="taker",
                           bid=0.50, ask=0.50, slip=0.0)
        reasons = ibc.kalshi_reasons("KXCPI-1", "yes", 1, 50, informed=thin)
        assert "ev_below_floor" in reasons
        fat = good_kalshi(series="KXCPI", fmap=fmap, edge=0.20, role="taker",
                          bid=0.50, ask=0.50, slip=0.0)
        assert "ev_below_floor" not in ibc.kalshi_reasons(
            "KXCPI-1", "yes", 1, 50, informed=fat)
        # 50¢ dome: maker fee is 0 on KXBTCY, but the floor is still above a 1¢ edge.
        assert "ev_below_floor" in ibc.kalshi_reasons(
            "KXBTCY-1", "yes", 1, 50, informed=good_kalshi(edge=0.01))
        assert ibc.kalshi_reasons(
            "KXBTCY-1", "yes", 1, 10, informed=good_kalshi(edge=0.05)) == []
    finally:
        unpin_kelly(qe, old)


def test_poly_stays_blocked_and_informed():
    _quiet()
    x = MockXport()
    c = _client(x, live=False)
    try:
        c.create_order(good_poly())
        raise AssertionError("live off must stay LiveWriteBlocked")
    except pmu.LiveWriteBlocked as e:
        assert "POLYMARKET_US_LIVE" in str(e)
    assert not any(call["path"] == "/v1/orders" for call in x.calls)
    live = _client(x, live=True)
    err = _refused(lambda: live.create_order({"marketSlug": "btc-100k", "quantity": 1}))
    assert "tag_prior" in err.reasons or "fv_source" in err.reasons
    assert not any(call["path"] == "/v1/orders" for call in x.calls)
    err = _refused(lambda: live.create_order(good_poly(fv_source="tag_prior")))
    assert "tag_prior" in err.reasons
    qe, old = pin_kelly()
    try:
        ack = live.create_order(good_poly())
    finally:
        unpin_kelly(qe, old)
    assert ack["kind"] == "create_ack" and ack["filled"] is False
    posted = [call for call in x.calls if call["path"] == "/v1/orders"]
    assert posted and posted[-1]["method"] == "POST"
    assert vc.POLYMARKET_US_LIVE is False
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")
    assert vc.VENUE_WRITER["polymarket"] == "none"


def test_poly_loop_tag_prior_not_eligible():
    _quiet()
    qe, old = pin_kelly()
    now = __import__("datetime").datetime(2026, 9, 20, 12, tzinfo=__import__("datetime").timezone.utc)
    end = (now + __import__("datetime").timedelta(hours=24)).isoformat()
    wx = {
        "id": "wx-2", "slug": "la-rain", "question": "Will it rain in LA?",
        "description": (
            "This market will resolve based on the National Weather Service "
            "daily climate report."),
        "active": True, "closed": False, "endDate": end,
        "liquidity": 9000, "takerFee": 0.0,
    }
    book = {"marketData": {
        "bestBid": {"value": "0.69"}, "bestAsk": {"value": "0.71"},
        "bids": [{"px": {"value": "0.69"}, "qty": "800"}],
        "offers": [{"px": {"value": "0.71"}, "qty": "800"}],
    }}
    rec = poly_loop.evaluate_one(wx, book=book, bbo=book, now=now, bankroll=100)
    assert rec is not None and rec["fv_source"] == "tag_prior"
    assert rec["create_order"] is False
    assert rec["create_eligible"] is False
    m = {
        "id": "edge-mkt", "slug": "edge-mkt", "question": "edge mkt",
        "fairValue": 0.70, "liquidity": 10000, "endDate": end,
        "resolutionSource": "https://official.example", "active": True,
        "takerFee": 0.0,
    }
    book50 = {"marketData": {
        "bestBid": {"value": "0.49"}, "bestAsk": {"value": "0.51"},
        "bids": [{"px": {"value": "0.49"}, "qty": "2000"}],
        "offers": [{"px": {"value": "0.51"}, "qty": "2000"}],
    }}
    ok = poly_loop.evaluate_one(m, book=book50, bbo=book50, now=now, bankroll=100)
    assert ok["fv_source"] == "market"
    assert ok["create_order"] is False
    assert ok["create_eligible"] is True
    assert ok["edge_pts"] >= 8
    bare = dict(m)
    bare.pop("takerFee")
    no_fee = poly_loop.evaluate_one(bare, book=book50, bbo=book50, now=now, bankroll=100)
    assert no_fee["create_eligible"] is False
    scored = dict(m)
    scored["mirofish_score"] = 50
    bad = poly_loop.evaluate_one(scored, book=book50, bbo=book50, now=now, bankroll=100)
    assert bad["create_eligible"] is False
    assert vc.POLYMARKET_US_LIVE is False
    unpin_kelly(qe, old)


def _roi_conviction():
    return {
        "ticker": "KXGDP-27APR29-T4.0",
        "strategy": "CONVICTION_YES",
        "side": "yes",
        "price": 0.17,
        "edge": 0.80,
        "roi_pct": 488,
        "predicted_prob": None,
        "rationale": "YES@0.17 → 488% ROI if right",
    }


def test_conviction_refuses_without_fv_and_skips_jev():
    _quiet()
    calls = []
    prev = _env("VALEDIA_JEV", "1")
    prev_k = _env("TYPESAFE_API_KEY", "test-key")

    def boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("jev must not run on INFORMED refuse")

    try:
        pre = ibc.create_preflight(_roi_conviction(), 1, 17, post=boom)
        assert pre["ok"] is False
        assert pre["jev"] is None
        assert "fv_missing" in pre["reasons"] or "fv_source" in pre["reasons"]
        assert calls == []
        assert "tag_prior" not in pre["informed"].get("fv_source", "")
    finally:
        _restore_env("VALEDIA_JEV", prev)
        _restore_env("TYPESAFE_API_KEY", prev_k)


def test_conviction_allows_with_fv_and_jev_fail_open():
    _quiet()
    qe, old = pin_kelly()
    prev = _env("VALEDIA_JEV", "1")
    prev_k = _env("TYPESAFE_API_KEY", "test-key")
    opp = good_kalshi(strategy="CONVICTION_YES", ticker="KXBTCY-1", side="yes", price=0.50)
    try:
        off = _env("VALEDIA_JEV", None)
        try:
            pre = ibc.create_preflight(opp, 1, 50, post=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("off")))
            assert pre["ok"] is True
            assert pre["jev"] is None
        finally:
            _restore_env("VALEDIA_JEV", off)

        def boom(*_a, **_k):
            raise TimeoutError("slow")

        pre = ibc.create_preflight(opp, 1, 50, post=boom)
        assert pre["ok"] is True
        assert pre["jev"]["fail_open"] is True
        assert pre["jev"]["enabled"] is True

        def stand(*_a, **_k):
            return {"model": "jev-1.13.0", "answers": {
                "action": {"choice": "stand_down", "confidence": 0.9},
                "stand_down": {"noul": 0.99},
                "report_usable": {"noul": 0.2},
                "we_are_the_exit": {"noul": 0.1},
            }}

        pre = ibc.create_preflight(opp, 1, 50, post=stand)
        assert pre["ok"] is True
        assert pre["jev"]["action"] == "stand_down"
        assert pre["jev"]["fail_open"] is False
    finally:
        unpin_kelly(qe, old)
        _restore_env("VALEDIA_JEV", prev)
        _restore_env("TYPESAFE_API_KEY", prev_k)


def test_executor_conviction_create_path():
    _quiet()
    import kalshi_executor as ex
    posted = []
    orig_c, orig_s = ex.create_order, ex._size_contracts
    ex._size_contracts = lambda *a, **k: 1
    ex.create_order = lambda **k: posted.append(k) or {"order_id": "nope"}
    ex.get_fills = lambda: {"fills": []}
    state = {"max_per_trade": 10, "max_deployable": 10, "balance": 100}
    qe, old = pin_kelly()
    prev = _env("VALEDIA_JEV", "1")
    prev_k = _env("TYPESAFE_API_KEY", "test-key")
    import jev_shadow as jev
    real_post = jev.http_post
    jev.http_post = lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("slow"))
    try:
        refused = ex.execute_trade(_roi_conviction(), state, dry_run=False)
        assert refused["status"] == "SKIP_INFORMED"
        assert posted == []
        opp = good_kalshi(strategy="CONVICTION_YES", ticker="KXBTCY-1", side="yes", price=0.50)
        allowed = ex.execute_trade(opp, state, dry_run=False)
        assert allowed["status"] in ("EXECUTED", "UNRECONCILED")
        assert posted and posted[-1]["informed"]["fv_source"] == "model"
        assert allowed["jev"]["fail_open"] is True
        exe = open(os.path.join(HERE, "kalshi_executor.py")).read()
        alloc = open(os.path.join(HERE, "master_allocator.py")).read()
        arb = open(os.path.join(HERE, "latency_arb.py")).read()
        assert "informed_before_create" in exe and "create_preflight" in exe
        assert "create_preflight" in alloc
        assert "jev_shadow" not in exe
        assert "jev_shadow" not in arb  # arb is speed-path (strategy LATENCY_ARB): no Jev; it now runs create_preflight (see test_latency_arb_evidence)
    finally:
        ex.create_order, ex._size_contracts = orig_c, orig_s
        jev.http_post = real_post
        unpin_kelly(qe, old)
        _restore_env("VALEDIA_JEV", prev)
        _restore_env("TYPESAFE_API_KEY", prev_k)


if __name__ == "__main__":
    test_hygiene_and_snapshot()
    test_bare_kalshi_create_does_not_post()
    test_kalshi_admits_only_when_all_pass()
    test_fences_sports_oil_fee_gate_demote()
    test_ev_floor_uses_preflight()
    test_poly_stays_blocked_and_informed()
    test_poly_loop_tag_prior_not_eligible()
    test_conviction_refuses_without_fv_and_skips_jev()
    test_conviction_allows_with_fv_and_jev_fail_open()
    test_executor_conviction_create_path()
    print("INFORMED-BEFORE-CREATE: PASS")
