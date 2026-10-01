#!/usr/bin/env python3
"""Checks for the backtest-first gate. Run: python3 test_backtest_gate.py
Synthetic ledgers in a tempdir only — no network, never touches the real ledger.
Covers: fee inclusion (gross-positive/fee-negative nets negative), nonlinear Kalshi
fee (not flat) ceil'd to the cent per order, thin sample -> INSUFFICIENT_DATA, better -> PASS, worse -> FAIL,
seed determinism, unsettled excluded, --live-only, malformed rows skipped."""
import os, sys, io, json, hashlib, tempfile, contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_gate as G
from forward_test_ledger import kalshi_fee

TMP = tempfile.mkdtemp(prefix="backtest_gate_test_")
TAKE_ALL = {"name": "all", "min_edge": None, "categories_allow": None,
            "categories_block": [], "size_mult": {}}


def _entry(eid, cat, price, qty, ts, side="yes", prob=None, source="live"):
    return {"type": "entry", "id": eid, "ts": ts, "strategy": cat, "venue": "kalshi",
            "ticker": "T-" + eid, "side": side, "entry_price": price, "contracts": qty,
            "entry_fee": kalshi_fee(price, qty),
            "predicted_prob": prob, "source": source}


def _settle(eid, cat, won, price, qty, source="live", recorded_fee=True, side="yes"):
    """Recorded fee -> realized is NET (reconcile() semantics); else GROSS + unverified."""
    gross = qty * (1.0 - price) if won else -qty * price
    fee = kalshi_fee(price, qty)                      # what reconcile() records
    rec = {"type": "settlement", "entry_id": eid, "ticker": "T-" + eid, "strategy": cat,
           "result": side if won else ("no" if side == "yes" else "yes"), "won": won,
           "filled_contracts": qty, "source": source}
    if recorded_fee:
        rec.update({"realized_pnl_net": round(gross - fee, 6), "fee": fee})
    else:
        rec.update({"realized_pnl_net": round(gross, 6), "fee": None,
                    "fee_status": "unverified_source"})
    return rec


def _book(rows, name):
    path = os.path.join(TMP, name)
    with open(path, "w") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return path


def _pair(eid, cat, won, i, price=0.5, qty=10, **kw):
    ts = f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z"
    src = kw.get("source", "live")
    return [_entry(eid, cat, price, qty, ts, source=src, prob=kw.get("prob")),
            _settle(eid, cat, won, price, qty, source=src,
                    recorded_fee=kw.get("recorded_fee", True))]


def _good_bad_ledger():
    rows, i = [], 0
    for k in range(40):  # GOOD: 30W/10L ; BAD: 10W/30L, interleaved in time
        rows += _pair(f"g{k}", "GOOD", k % 4 != 0, i); i += 1
        rows += _pair(f"b{k}", "BAD", k % 4 == 0, i); i += 1
    return _book(rows, "good_bad.jsonl")


def _run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = G.main(argv)
    return code, out.getvalue(), err.getvalue()


def _policy_file(obj, name):
    path = os.path.join(TMP, name)
    json.dump(obj, open(path, "w"))
    return path


def run():
    # 1. Fee inclusion: 21W/20L at P=0.5, 100 contracts -> gross +$50, fees 41*~1.75 -> net < 0.
    rows = []
    for k in range(41):
        rows += _pair(f"f{k}", "X", k < 21, k, price=0.5, qty=100, recorded_fee=False)
    trades, _ = G.build_trades(G.load_ledger(_book(rows, "fee.jsonl"))[0])
    gross = sum(r["realized_pnl_net"] for r in rows if r["type"] == "settlement")
    assert abs(gross - 50.0) < 1e-6, gross
    rep, _ = G.replay(trades, G.load_policy(TAKE_ALL))
    assert rep["net"] < 0, rep
    assert abs(rep["net"] - (50.0 - 41 * kalshi_fee(0.5, 100))) < 1e-6, rep["net"]
    assert all(t["fee_basis"] == "modeled" for t in trades)

    # 2. Nonlinear fee ceil(0.07*C*P*(1-P)) to the cent per order, never a flat 0.02.
    e = _entry("n1", "X", 0.10, 100, "2026-09-01T00:00:00Z")
    s = _settle("n1", "X", True, 0.10, 100, recorded_fee=False)
    net, fee, basis = G.fee_true_net(e, s)
    raw = 0.07 * 100 * 0.10 * 0.90
    assert basis == "modeled" and fee == kalshi_fee(0.10, 100), fee
    assert raw <= fee < raw + 0.01 + 1e-9 and abs(fee * 100 - round(fee * 100)) < 1e-9, fee
    assert abs(fee - 0.02 * 100) > 1.0, "flat fee used"
    assert abs(net - (90.0 - fee)) < 1e-6, net
    # per-order rounding: raw fee 0.0105 (P=0.5, C=0.6) is charged 0.02, not 0.0105
    e6 = _entry("r1", "X", 0.5, 0.6, "2026-09-01T00:00:00Z")
    s6 = _settle("r1", "X", False, 0.5, 0.6, recorded_fee=False)
    n6, f6, b6 = G.fee_true_net(e6, s6)
    assert abs(0.07 * 0.6 * 0.5 * 0.5 - 0.0105) < 1e-12
    assert b6 == "modeled" and abs(f6 - 0.02) < 1e-12 and abs(n6 - (-0.30 - 0.02)) < 1e-9, (f6, n6)
    # recorded fee path: realized already net, must NOT be double-charged
    s2 = _settle("n1", "X", True, 0.10, 100, recorded_fee=True)
    net2, fee2, basis2 = G.fee_true_net(e, s2)
    assert basis2 == "recorded" and abs(net2 - (90.0 - kalshi_fee(0.10, 100))) < 1e-6, (net2, basis2)
    # backfill-style row (no price/qty): won -> inferred price; lost -> 0.07*cost bound
    bw = {"type": "settlement", "realized_pnl_net": 0.34, "cost": 0.66, "won": True,
          "fee": None, "fee_status": "unverified_source", "result": "yes"}
    _, fw, bwb = G.fee_true_net({"entry_price": 0.0, "contracts": 0.0}, bw)
    assert bwb == "modeled_inferred" and abs(fw - 0.02) < 1e-9, fw   # ceil(0.0157) per order
    bl = dict(bw, realized_pnl_net=-0.77, cost=0.77, won=False, result="no")
    nl, fl, blb = G.fee_true_net({"entry_price": 0.0, "contracts": 0.0}, bl)
    assert blb == "modeled_bound" and abs(fl - 0.07 * 0.77) < 1e-9 and nl < -0.77

    # 3. Thin sample -> INSUFFICIENT_DATA (never PASS), exit 2, even when it "wins".
    rows = []
    for k in range(10):
        rows += _pair(f"t{k}", "X", True, k)
    thin = _book(rows, "thin.jsonl")
    pa = _policy_file(TAKE_ALL, "all.json")
    code, out, err = _run_main(["--baseline", pa, "--candidate", pa, "--ledger", thin])
    assert code == 2 and json.loads(out)["verdict"] == "INSUFFICIENT_DATA", (code, err)
    assert "INSUFFICIENT_DATA" in err

    # 4. Clearly better candidate (pause the losing bucket) -> PASS, exit 0.
    gb = _good_bad_ledger()
    pc = _policy_file(dict(TAKE_ALL, name="no_bad", categories_block=["BAD"]), "no_bad.json")
    code, out, _ = _run_main(["--baseline", pa, "--candidate", pc, "--ledger", gb,
                              "--n-boot", "500"])
    r = json.loads(out)
    assert code == 0 and r["verdict"] == "PASS", (code, r["reasons"])
    assert r["candidate"]["n_taken"] == 40 and r["candidate"]["net"] > r["baseline"]["net"]
    assert r["monte_carlo"]["diff"]["p_lt_0"] <= 0.35

    # 5. Clearly worse candidate (pause the winning bucket) -> FAIL, exit 1.
    pw = _policy_file(dict(TAKE_ALL, name="no_good", categories_block=["GOOD"]), "no_good.json")
    code, out, _ = _run_main(["--baseline", pa, "--candidate", pw, "--ledger", gb,
                              "--n-boot", "500"])
    r = json.loads(out)
    assert code == 1 and r["verdict"] == "FAIL" and r["reasons"], (code, r)

    # 6. Determinism by seed (same seed identical; different seed differs).
    a1 = G.run_gate(gb, pa, pc, n_boot=300, seed=11)
    a2 = G.run_gate(gb, pa, pc, n_boot=300, seed=11)
    a3 = G.run_gate(gb, pa, pc, n_boot=300, seed=12)
    assert json.dumps(a1, sort_keys=True) == json.dumps(a2, sort_keys=True)
    assert a1["monte_carlo"] != a3["monte_carlo"]

    # 7. Unsettled rows excluded and counted (open entry + pending settlement).
    rows = _pair("u1", "X", True, 0) + [
        _entry("u2", "X", 0.4, 5, "2026-09-02T00:00:00Z"),
        _entry("u3", "X", 0.4, 5, "2026-09-02T01:00:00Z"),
        {"type": "settlement", "entry_id": "u3", "result": None, "realized_pnl_net": 9.0}]
    trades, st = G.build_trades(G.load_ledger(_book(rows, "unsettled.jsonl"))[0])
    assert [t["id"] for t in trades] == ["u1"] and st["unsettled_excluded"] == 2, st

    # 8. --live-only drops backfill (reported separately); paper never counts.
    rows = (_pair("l1", "X", True, 0) + _pair("l2", "X", False, 1)
            + _pair("k1", "X", True, 2, source="backfill:pnl_tracker", recorded_fee=False)
            + _pair("p1", "X", True, 3, source="paper"))
    mixed = _book(rows, "mixed.jsonl")
    trades, st = G.build_trades(G.load_ledger(mixed)[0])
    assert st["source:live"] == 2 and st["source:backfill"] == 1 and st["nonreal_excluded"] == 1
    trades, st = G.build_trades(G.load_ledger(mixed)[0], live_only=True)
    assert {t["id"] for t in trades} == {"l1", "l2"} and st["backfill_excluded"] == 1, st
    rep = G.run_gate(mixed, TAKE_ALL, TAKE_ALL, n_boot=50)
    assert set(rep["baseline"]["by_source"]) == {"live", "backfill"}
    code, out, _ = _run_main(["--baseline", pa, "--candidate", pa, "--ledger", mixed,
                              "--live-only", "--min-n", "2", "--n-boot", "50"])
    assert json.loads(out)["ledger_stats"]["eligible"] == 2 and code in (0, 1)

    # 9. Malformed rows skipped, not crashing.
    rows = (_pair("m1", "X", True, 0) + [
        "{not json", "[1, 2, 3]", "42",
        {"type": "settlement", "entry_id": "zz", "result": "yes", "realized_pnl_net": "abc"},
        {"type": "settlement", "entry_id": ["bad"], "result": "yes", "realized_pnl_net": 1},
        {"type": "settlement"},
        {"type": "settlement", "entry_id": "m1", "result": "yes", "realized_pnl_net": 99.0}])
    bad = _book(rows, "malformed.jsonl")
    rep = G.run_gate(bad, TAKE_ALL, TAKE_ALL, n_boot=20, min_n=1)
    st = rep["ledger_stats"]
    assert st["eligible"] == 1 and st["malformed_skipped"] >= 5, st
    assert st["duplicate_settlements_ignored"] == 1, st          # m1 settled twice
    assert abs(rep["baseline"]["net"] - (5.0 - kalshi_fee(0.5, 10))) < 1e-6  # first settlement wins

    # 10. Policy validation + min_edge (unknown edge -> skipped, not taken).
    try:
        G.load_policy(dict(TAKE_ALL, min_egde=0.05))
        assert False, "typo key accepted"
    except ValueError:
        pass
    rows = (_pair("e1", "X", True, 0, price=0.5, prob=0.60)     # edge +0.10
            + _pair("e2", "X", True, 1, price=0.5, prob=0.52)   # edge +0.02
            + _pair("e3", "X", True, 2, price=0.5))             # no prob -> unknown
    trades, _ = G.build_trades(G.load_ledger(_book(rows, "edge.jsonl"))[0])
    pol = G.load_policy(dict(TAKE_ALL, name="e", min_edge=0.05, size_mult={"X": 2}))
    rep, _ = G.replay(trades, pol)
    assert rep["n_taken"] == 1 and abs(rep["net"] - 2 * (5.0 - kalshi_fee(0.5, 10))) < 1e-6, rep
    code, _, err = _run_main(["--baseline", pa, "--candidate", "/nonexistent.json",
                              "--ledger", thin])
    assert code == 3 and "input error" in err

    # 11. Read-only: the ledger file is byte-identical after a gate run.
    before = hashlib.sha256(open(gb, "rb").read()).hexdigest()
    G.run_gate(gb, pa, pc, n_boot=20)
    assert hashlib.sha256(open(gb, "rb").read()).hexdigest() == before

    print("test_backtest_gate: all checks passed")


if __name__ == "__main__":
    run()
