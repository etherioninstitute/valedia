#!/usr/bin/env python3
"""Sports evidence wiring: sports orders run the allocator's pre-create chain.

EV preflight → session_clocks.before_create → informed create_preflight →
create_order(informed=...). Fail-closed: missing evidence → SKIP_* with reasons,
no create_order. Attestation is read from a file, never synthesized.
No network (kalshi GETs are fakes), no keys, no --live.
Run: python3 test_sports_evidence.py
"""
import contextlib
import json
import os
import re
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import sports_engine as se
import kalshi_auth
import kalshi_fee_map as kfm
import kalshi_profit as kp
import session_clocks as sc
import informed_before_create as ibc

FAV, DOG = "KXNBAGAME-26OCT01BOSNYK-BOS", "KXNBAGAME-26OCT01BOSNYK-NYK"


def market(now=None, **over):
    now = now or datetime.now(timezone.utc)
    m = {
        "status": "active",
        "open_time": (now - timedelta(hours=1)).isoformat(),
        "close_time": (now + timedelta(hours=8)).isoformat(),
        "expected_expiration_time": (now + timedelta(hours=5)).isoformat(),
        "can_close_early": True,
        "early_close_condition": "game end",
    }
    m.update(over)
    return {k: v for k, v in m.items() if v is not None}


def book(bid_c, ask_c, depth=50):
    """Kalshi orderbook payload: YES bids + NO bids (YES ask = 100 − best NO bid)."""
    return {"orderbook": {"yes": [[bid_c - 2, 10], [bid_c, depth]],
                          "no": [[100 - ask_c - 3, 10], [100 - ask_c, depth]]}}


def model_fv(fv=0.90):
    return lambda bet, fav_book, dog_book: {
        "fv": fv, "fv_source": "model", "fv_confidence": "test_model"}


def sports_bet(ticker=FAV, price=0.80, n=5, with_market=True, dog=DOG):
    b = {'game_id': 'g', 'league': 'NBA', 'series': None, 'fav_ticker': ticker,
         'fav_title': 'Boston at New York Winner?', 'fav_price': price, 'fav_bid': price - 0.01,
         'spread': 0.01, 'dog_ticker': dog, 'dog_price': 0.20, 'exp': '2026',
         'edge_score': 30, 'edge_reasons': [], 'contracts': n, 'cost': round(n * price, 2),
         'fee': 0.02, 'profit_if_win': 0.98, 'roi_pct': 24.5, 'bet_pct': 4.0}
    if with_market:
        b['fav_market'] = market()
    return b


def write_attestation(path, **over):
    doc = {"valid": True, "geo": "pass", "attested_by": "test-fixture",
           "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
    doc.update(over)
    with open(path, "w") as f:
        json.dump(doc, f)


@contextlib.contextmanager
def admitted_sports_env(series=("KXNBAGAME",), books=None, markets=None, attestation=True,
                        fv=0.90, taker_m=1.0, maker_m=1.0):
    """Fakes every evidence source so a sports bet is honestly admitted.

    Yields execute_bets kwargs. Kalshi GETs are served from in-memory books
    (default fav 79/80, dog 19/20). A test model FV stands in for the model
    FV that does not exist yet in production (live fv_source is "market").
    """
    books = dict(books or {})
    markets = dict(markets or {})
    calls = []

    def fake_get(path):
        calls.append(path)
        if path.endswith("/orderbook"):
            t = path.split("/")[2]
            if t in books:
                if books[t] is None:
                    raise RuntimeError("no book")
                return books[t]
            return book(19, 20) if t.endswith("-NYK") else book(79, 80)
        t = path.split("/")[2]
        if t in markets:
            if markets[t] is None:
                raise RuntimeError("no market")
            return {"market": markets[t]}
        return {"market": market()}

    fd, att_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(att_path)
    if attestation:
        write_attestation(att_path)
    orig_get, orig_att = se.get, se.SPORTS_ATTESTATION_PATH
    se.get, se.SPORTS_ATTESTATION_PATH = fake_get, att_path
    try:
        yield {"bankroll": 10000.0,
               "fv_fn": model_fv(fv) if fv is not None else None,
               "fmap": {s: kfm._row(s, maker_m, taker_m, "sports") for s in series},
               "_get_calls": calls}
    finally:
        se.get, se.SPORTS_ATTESTATION_PATH = orig_get, orig_att
        try:
            os.remove(att_path)
        except OSError:
            pass


def _kw(env):
    return {k: v for k, v in env.items() if not k.startswith("_")}


class Venue:
    """Fake create_order / positions / fills / ledger booking."""

    def __init__(self, resp=None):
        self.orders, self.books = [], []
        self.resp = resp or {"order": {"order_id": "f1", "status": "executed",
                                       "fill_count": 5, "remaining_count": 0,
                                       "yes_price": 80, "count": 5}}
        self._orig = []

    def __enter__(self):
        for obj, name, val in (
                (se, "create_order", self._create),
                (kalshi_auth, "get_positions", lambda: {"market_positions": []}),
                (kalshi_auth, "get_fills", lambda: {"fills": []}),
                (se.ftl, "book_kalshi_fill", lambda *a, **k: self.books.append((a, k)) or "ft")):
            self._orig.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        return self

    def __exit__(self, *exc):
        for obj, name, val in reversed(self._orig):
            setattr(obj, name, val)

    def _create(self, *a, **k):
        self.orders.append((a, k))
        return self.resp


# ── (1) fully-evidenced sports opp → create_order(informed=) and admitted ──

def test_admitted_reaches_create_order_with_informed():
    with admitted_sports_env() as env, Venue() as v:
        res = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))
    r = res[0]
    assert r["status"] == "EXECUTED", r.get("status")
    assert len(v.orders) == 1, v.orders
    (ticker, side, n, px), kw = v.orders[0]
    assert (ticker, side, n, px) == (FAV, "yes", 5, 80)
    inf = kw.get("informed")
    assert isinstance(inf, dict), kw
    for k in ("fv", "fv_source", "fv_confidence", "edge", "slip", "bid", "ask", "depth",
              "bankroll", "market", "sports_attestation", "strategy"):
        assert k in inf, k
    assert inf["fv_source"] == "model" and inf["bid"] == 0.79 and inf["ask"] == 0.80
    assert inf["depth"] == 50 and abs(inf["edge"] - 0.10) < 1e-9
    assert inf["sports_attestation"]["valid"] is True
    # The very gate kalshi_auth.create_order re-runs admits these exact args.
    assert ibc.kalshi_reasons(ticker, side, n, px, "limit", inf) == []
    assert ibc.require_kalshi(ticker, side, n, px, "limit", inf) is True
    ev = r["evidence"]
    assert r["evidence_version"] == se.SPORTS_EVIDENCE_VERSION == ev["version"]
    assert ev["book_source"] and ev["attestation_status"] == "ok"
    assert ev["edge_fee_true"] > 0 and ev["market"]["status"] == "active"
    json.dumps(ev)  # evidence is log-serializable


# ── (2) each missing evidence → SKIP with exact reason, no create_order ──

def _one(env_kw=None, bet_kw=None, call_kw=None):
    with admitted_sports_env(**(env_kw or {})) as env, Venue() as v:
        kw = _kw(env)
        kw.update(call_kw or {})
        res = se.execute_bets([sports_bet(**(bet_kw or {}))], dry_run=False, **kw)
    assert v.orders == [], f"no create_order on refusal: {res[0].get('status')}"
    return res[0]


def test_each_missing_evidence_skips():
    now = datetime.now(timezone.utc)
    cases = [
        ("no attestation", dict(env_kw=dict(attestation=False)),
         "SKIP_INFORMED", "sports_attestation"),
        ("market FV (live default)", dict(env_kw=dict(fv=None)), "SKIP_INFORMED", "fv_source"),
        ("bankroll missing", dict(call_kw=dict(bankroll=None)), "SKIP_INFORMED", "size"),
        ("fav book GET fails", dict(env_kw=dict(books={FAV: None})), "SKIP_INFORMED", "edge_missing"),
        ("fav book GET fails → depth", dict(env_kw=dict(books={FAV: None})), "SKIP_INFORMED", "depth"),
        ("no market anywhere", dict(env_kw=dict(markets={FAV: None}), bet_kw=dict(with_market=False)),
         "SKIP_SESSION", "schedule_or_status_missing"),
        ("game closed", dict(bet_kw=dict(with_market=False), env_kw=dict(markets={FAV: market(
            close_time=(now - timedelta(minutes=5)).isoformat())})), "SKIP_SESSION", "outside_schedule"),
        ("resolve unknown", dict(bet_kw=dict(with_market=False), env_kw=dict(markets={FAV: market(
            can_close_early=None, early_close_condition=None)})), "SKIP_RESOLVE", "close_time_alone"),
        ("book ask moved", dict(env_kw=dict(books={FAV: book(80, 81)})), "SKIP_EVIDENCE", "price_moved"),
        ("fee-true edge ≤ 0 on a fee-free map", dict(env_kw=dict(fv=0.805, taker_m=0.0, maker_m=0.0)),
         "SKIP_EVIDENCE", "edge_fee_true_nonpositive"),
    ]
    for name, kw, status, reason in cases:
        r = _one(**kw)
        assert r["status"] == status, (name, r["status"], r.get("reasons"))
        assert reason in r["reasons"], (name, r["reasons"])
        assert r["error"], name
    # missing fv / fv_confidence from the FV source itself
    for fvd, reason in (({"fv": None, "fv_source": "model", "fv_confidence": "x"}, "fv_missing"),
                        ({"fv": 0.9, "fv_source": "model", "fv_confidence": None}, "fv_confidence")):
        r = _one(call_kw=dict(fv_fn=lambda b, f, d, _x=fvd: dict(_x)))
        assert r["status"] == "SKIP_INFORMED" and reason in r["reasons"], (reason, r.get("reasons"))
    # one-sided dog book → default FV cannot de-vig → fv_missing
    r = _one(env_kw=dict(fv=None, books={DOG: {"orderbook": {"yes": [[19, 5]], "no": []}}}))
    assert "fv_missing" in r["reasons"], r["reasons"]
    assert r["evidence"]["fv_detail"]["fv_why"] == "one_sided_book"


def test_default_fv_is_honest_devig():
    fv = se.fair_value_from_books({}, se.parse_orderbook(book(79, 80)), se.parse_orderbook(book(19, 20)))
    assert fv["fv_source"] == "market" and fv["fv_source"] not in ibc.ALLOWED_FV
    assert abs(fv["fv"] - 0.795 / 0.99) < 1e-4 and fv["fv_confidence"] == "two_sided_coherent"
    wide = se.fair_value_from_books({}, se.parse_orderbook(book(79, 80)), se.parse_orderbook(book(30, 31)))
    assert wide["fv_confidence"] is None and wide["fv_why"] == "books_disagree"
    # fp/dollars orderbook shape parses the same
    fp = se.parse_orderbook({"orderbook_fp": {"yes_dollars": [["0.7900", "50.00"]],
                                              "no_dollars": [["0.2000", "12.00"]]}})
    assert fp == {"bid": 0.79, "ask": 0.80, "depth": 12.0, "spread": 0.01}, fp


# ── (3) attestation is never synthesized ──

def test_attestation_never_synthesized():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "att.json")
    assert se.load_sports_attestation(p) == (None, "attestation_missing")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    for doc, why in ((True, "attestation_shape"), ({"valid": True}, "attestation_geo"),
                     ({"valid": "true", "geo": "pass"}, "attestation_invalid"),
                     ({"valid": True, "geo": "fail", "attested_by": "x"}, "attestation_geo"),
                     ({"valid": True, "geo": "pass", "expires_at": past}, "attestation_unsigned"),
                     ({"valid": True, "geo": "pass", "attested_by": "x"}, "attestation_no_expiry"),
                     ({"valid": True, "geo": "pass", "attested_by": "x", "expires_at": past},
                      "attestation_expired")):
        with open(p, "w") as f:
            json.dump(doc, f)
        assert se.load_sports_attestation(p) == (None, why), (doc, why)
    with open(p, "w") as f:
        f.write("{not json")
    assert se.load_sports_attestation(p) == (None, "attestation_unreadable")
    # With no file the opp/informed carry NO attestation key at all.
    with admitted_sports_env(attestation=False) as env:
        opp, ev = se.build_sports_opp(sports_bet(), **{k: env[k] for k in ("bankroll", "fv_fn", "fmap")})
    assert "sports_attestation" not in opp and ev["sports_attestation"] is None
    assert "sports_attestation" not in ibc.informed_from_opp(opp)
    for name in ("sports_engine.py", "sports_evidence.py"):
        src = open(os.path.join(HERE, name)).read()
        assert not re.search(r"sports_attestation['\"]?\s*[\]:=]+\s*True", src), f"attestation literal True in {name}"
        assert not re.search(r"sports_geo['\"]?\s*[\]:=]+\s*['\"]pass", src), name


# ── (4) dry_run never calls create_order (nor the book GET) ──

def test_dry_run_never_creates():
    with admitted_sports_env() as env, Venue() as v:
        res = se.execute_bets([sports_bet(), sports_bet(ticker=FAV + "2")], dry_run=True, **_kw(env))
        assert env["_get_calls"] == []
    assert [r["status"] for r in res] == ["DRY_RUN", "DRY_RUN"]
    assert v.orders == [] and v.books == []


# ── (5) fill-truth + ledger booking unchanged; log records evidence/skips ──

def test_fill_truth_and_ledger_unchanged():
    with admitted_sports_env() as env, Venue({"order": {"order_id": "ack", "status": "resting"}}) as v:
        r = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))[0]
    assert r["status"] == "UNRECONCILED" and len(v.orders) == 1
    (cat, t, side, _resp), k = v.books[0]
    assert (cat, t, side) == (se.CATEGORY, FAV, "yes")
    assert k["intended_source"] == "live" and k["fallback_price"] == 0.80
    assert k["fallback_contracts"] == 5 and "fills_payload" in k
    with admitted_sports_env() as env, Venue() as v:
        se.ftl.book_kalshi_fill = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk"))
        r = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))[0]
    assert r["status"] == "UNRECONCILED", "bookkeeping failure must never read as live"
    # create_order's own gate refusing → SKIP_INFORMED, not FAILED
    with admitted_sports_env() as env, Venue() as v:
        def refuse(*a, **k):
            raise ibc.InformedCreateRefused(["halt"])
        se.create_order = refuse
        r = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))[0]
    assert r["status"] == "SKIP_INFORMED" and r["reasons"] == ["halt"], r


def test_run_logs_evidence_and_skips():
    saved, patches = {}, []

    def patch(obj, name, val):
        patches.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)
    fake_signal = types.ModuleType("signal_send")
    fake_signal.send = lambda msg: None
    prev_signal = sys.modules.get("signal_send")
    sys.modules["signal_send"] = fake_signal
    seen = {}
    try:
        with admitted_sports_env() as env, Venue():
            real_exec = se.execute_bets

            def exec_spy(bets, dry_run=False, **k):
                seen.update(k)
                return real_exec(bets, dry_run=dry_run, **dict(k, fv_fn=env["fv_fn"], fmap=env["fmap"]))
            patch(se, "execute_bets", exec_spy)
            patch(se, "check_settlements", lambda: ([], {}))
            patch(se, "get_balance", lambda: {"balance": 1000000})
            patch(se, "get_fear_greed", lambda: 50)
            patch(se, "scan_games", lambda: [sports_bet(), sports_bet(ticker=FAV + "X", price=0.81)])
            patch(se.ftl, "should_trade_category", lambda cat: (True, "ok"))
            patch(se, "load_log", lambda: {"bets": [], "stats": {}})
            patch(se, "save_log", lambda l: saved.setdefault("log", l))
            se.run(dry_run=False)
    finally:
        for obj, name, val in reversed(patches):
            setattr(obj, name, val)
        if prev_signal is not None:
            sys.modules["signal_send"] = prev_signal
        else:
            sys.modules.pop("signal_send", None)
    assert seen.get("bankroll") == 10000.0, seen  # run() hands the real bankroll to evidence
    log = saved["log"]
    assert [b["fav_ticker"] for b in log["bets"]] == [FAV]
    assert log["bets"][0]["evidence_version"] == se.SPORTS_EVIDENCE_VERSION
    assert log["bets"][0]["evidence"]["fv_source"] == "model"
    assert [s["fav_ticker"] for s in log["skips"]] == [FAV + "X"]
    assert log["skips"][0]["status"] == "SKIP_EVIDENCE" and log["skips"][0]["reasons"] == ["price_moved"]
    # skips are never settled as bets
    calls = []
    orig = (se.load_log, se.save_log, se.get)
    se.load_log, se.save_log = (lambda: log), (lambda l: None)
    se.get = lambda path: calls.append(path) or {"market": {"status": "finalized", "result": "no"}}
    try:
        se.check_settlements()
    finally:
        se.load_log, se.save_log, se.get = orig
    assert calls == [f"/markets/{FAV}"], calls


# ── (6) session clock + EV preflight now run on sports, in allocator order ──

def test_session_clock_and_ev_preflight_run():
    order = []
    o_ann, o_bc, o_cp = kp.annotate, sc.before_create, ibc.create_preflight

    def ann(opp, contracts=1, **k):
        order.append(("ev", contracts))
        return o_ann(opp, contracts=contracts, **k)

    def bc(opp, now=None):
        order.append(("clock", opp.get("desk")))
        return o_bc(opp, now)

    def cp(opp, n, px, *a, **k):
        order.append(("informed", n, px))
        return o_cp(opp, n, px, *a, **k)
    kp.annotate, sc.before_create, ibc.create_preflight = ann, bc, cp
    try:
        with admitted_sports_env() as env, Venue() as v:
            r = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))[0]
        assert order == [("ev", 5), ("clock", "kalshi_sports"), ("informed", 5, 80)], order
        assert r["status"] == "EXECUTED" and r["ev_preflight"]["role"] in ("maker", "taker")
        assert len(v.orders) == 1
        # EV preflight refusal stops before the clock and the order
        order.clear()
        kp.annotate = lambda opp, contracts=1, **k: (order.append("ev") or (False, opp))
        with admitted_sports_env() as env, Venue() as v:
            r = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))[0]
        assert r["status"] == "SKIP_EV_PREFLIGHT" and order == ["ev"] and v.orders == []
        # EV preflight raising fails closed
        def boom(opp, contracts=1, **k):
            raise RuntimeError("fee map down")
        kp.annotate = boom
        with admitted_sports_env() as env, Venue() as v:
            r = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))[0]
        assert r["status"] == "SKIP_EV_PREFLIGHT" and v.orders == []
        # clock refusal stops before informed and the order
        kp.annotate = ann
        order.clear()
        sc.before_create = lambda opp, now=None: order.append("clock") or ("SKIP_SESSION", "outside_schedule")
        with admitted_sports_env() as env, Venue() as v:
            r = se.execute_bets([sports_bet()], dry_run=False, **_kw(env))[0]
        assert r["status"] == "SKIP_SESSION" and r["reasons"] == ["outside_schedule"]
        assert ("informed", 5, 80) not in order and v.orders == []
    finally:
        kp.annotate, sc.before_create, ibc.create_preflight = o_ann, o_bc, o_cp


if __name__ == "__main__":
    test_admitted_reaches_create_order_with_informed()
    test_each_missing_evidence_skips()
    test_default_fv_is_honest_devig()
    test_attestation_never_synthesized()
    test_dry_run_never_creates()
    test_fill_truth_and_ledger_unchanged()
    test_run_logs_evidence_and_skips()
    test_session_clock_and_ev_preflight_run()
    print("sports evidence wiring: PASS")
