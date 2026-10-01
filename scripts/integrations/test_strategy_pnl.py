#!/usr/bin/env python3
"""strategy_pnl: exact per-strategy fee-true numbers on a synthetic ledger.

Live vs backfill separation, paper/shadow/stub exclusion, alias folding, since filter,
proven flag (n_settled >= 30), daily_lines, CLI. No network; temp files only.
Run: python3 test_strategy_pnl.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_pnl as sp  # noqa: E402

T0 = "2026-09-20T12:00:00+00:00"
OLD = "2026-01-02T00:00:00+00:00"


def E(eid, strat, source="live", ts=T0, fee=0.0, **kw):
    return {"type": "entry", "id": eid, "ts": ts, "strategy": strat, "venue": "coinbase",
            "ticker": "T", "side": "buy", "entry_price": 1.0, "contracts": 1.0,
            "entry_fee": fee, "source": source, **kw}


def S(eid, strat, net, fee=None, source="live", at=T0, **kw):
    r = {"type": "settlement", "entry_id": eid, "strategy": strat, "result": "exit",
         "realized_pnl_net": net, "won": net > 0, "source": source, "logged_at": at, **kw}
    if fee is not None:
        r["fee"] = fee
    return r


def synthetic():
    rows = [
        E("c1", "BRAIN_CRYPTO", fee=0.2), S("c1", "BRAIN_CRYPTO", 0.59, fee=0.41),
        E("c2", "BRAIN_CRYPTO", fee=0.05), S("c2", "BRAIN_CRYPTO", -0.5, fee=0.1),
        # settle_open-style row: no settlement fee -> entry fee is used
        E("c3", "BRAIN_CRYPTO", fee=0.11), S("c3", "BRAIN_CRYPTO", 0.89),
        E("c4", "BRAIN_CRYPTO"),  # open live lot
        E("u1", "BRAIN_CRYPTO", source="unreconciled", fill_status="UNRECONCILED",
          intended_source="live"),  # POST-ack, open
        E("u2", "BRAIN_CRYPTO", source="unreconciled", fill_status="UNRECONCILED",
          intended_source="live"),
        S("u2", "BRAIN_CRYPTO", 0.0, source="unreconciled", result="reconcile_close"),
        E("u3", "BRAIN_EQUITY", source="unreconciled", fill_status="UNRECONCILED",
          intended_source="paper"),  # never a live order
        E("p1", "BRAIN_EQUITY", source="paper"), S("p1", "BRAIN_EQUITY", 5.0, fee=0.0),
        E("sh", "BRAIN_EQUITY", source="intel_shadow"),
        E("k1", "SPORTS_TONIGHT", fee=0.02), S("k1", "SPORTS_TONIGHT", 0.45, fee=0.02),
        E("k2", "SPORTS", fee=0.02), S("k2", "SPORTS", -0.6, fee=0.02),
        E("k3", "SPORTS", ts=OLD, fee=0.02), S("k3", "SPORTS", 0.3, fee=0.02, at=OLD),
        E("b1", "SPORTS", source="backfill:pnl_tracker", ts="2026-03-23T00:00:00Z"),
        S("b1", "SPORTS", 1.19, source="backfill:pnl_tracker", fee_status="unverified_source"),
        E("b2", "WEATHER_ARB", source="backfill:pnl_tracker"),
        S("b2", "WEATHER_ARB", -2.0, source="backfill:pnl_tracker"),
        E("b3", "WEATHER_ARB", source="backfill:pnl_tracker"),  # backfill never settled
    ]
    for i in range(30):
        rows += [E(f"d{i}", "DCA_CRYPTO", fee=0.01), S(f"d{i}", "DCA_CRYPTO", 0.1, fee=0.02)]
    for i in range(29):
        rows += [E(f"q{i}", "DCA_EQUITY"), S(f"q{i}", "DCA_EQUITY", -0.1 if i % 2 else 0.2, fee=0.0)]
    return rows


@contextlib.contextmanager
def ledger(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.write("not json\n")  # a bad line is skipped, never fatal
    try:
        yield path
    finally:
        os.remove(path)


def close(a, b):
    return abs(a - b) < 1e-9


def test_exact_numbers_and_separation():
    with ledger(synthetic()) as path:
        rep = sp.report(ledger_path=path)
    assert set(rep) == {"BRAIN_CRYPTO", "SPORTS", "DCA_CRYPTO", "DCA_EQUITY",
                        "SPORTS [backfill]", "WEATHER_ARB [backfill]"}, sorted(rep)
    bc = rep["BRAIN_CRYPTO"]
    assert (bc["n_settled"], bc["n_open"], bc["n_unreconciled"]) == (3, 1, 1), bc
    assert (bc["wins"], bc["losses"]) == (2, 1)
    assert close(bc["net"], 0.98) and close(bc["fees"], 0.62) and close(bc["gross"], 1.6), bc
    assert bc["win_rate"] == 66.7 and close(bc["avg_net"], 0.3267)
    assert bc["last_ts"] == T0 and bc["proven"] is False and bc["backfill"] is False
    sp_ = rep["SPORTS"]  # SPORTS_TONIGHT folds into SPORTS
    assert (sp_["n_settled"], sp_["wins"], sp_["losses"]) == (3, 2, 1)
    assert close(sp_["net"], 0.15) and close(sp_["fees"], 0.06) and close(sp_["gross"], 0.21)
    bf = rep["SPORTS [backfill]"]
    assert bf["backfill"] is True and bf["proven"] is False
    assert (bf["n_settled"], bf["wins"], bf["net"], bf["fees"], bf["gross"]) == (1, 1, 1.19, 0.0, 1.19)
    wx = rep["WEATHER_ARB [backfill]"]
    assert (wx["n_settled"], wx["n_open"], wx["losses"], wx["net"]) == (1, 1, 1, -2.0)
    assert "BRAIN_EQUITY" not in rep  # paper, shadow, paper-intent stub: excluded


def test_proven_flag():
    with ledger(synthetic()) as path:
        rep = sp.report(ledger_path=path)
    dc, de = rep["DCA_CRYPTO"], rep["DCA_EQUITY"]
    assert dc["n_settled"] == 30 and dc["proven"] is True
    assert close(dc["net"], 3.0) and close(dc["fees"], 0.6) and close(dc["gross"], 3.6)
    assert dc["win_rate"] == 100.0 and close(dc["avg_net"], 0.1)
    assert de["n_settled"] == 29 and de["proven"] is False
    assert (de["wins"], de["losses"]) == (15, 14) and close(de["net"], 15 * 0.2 - 14 * 0.1)
    # 30+ backfill rows are still never proven
    rows = []
    for i in range(31):
        rows += [E(f"b{i}", "X", source="backfill:pnl_tracker"),
                 S(f"b{i}", "X", 1.0, source="backfill:pnl_tracker")]
    with ledger(rows) as path:
        assert sp.report(ledger_path=path)["X [backfill]"]["proven"] is False


def test_since_filter():
    with ledger(synthetic()) as path:
        rep = sp.report(since="2026-06-01", ledger_path=path)
    s = rep["SPORTS"]
    assert s["n_settled"] == 2 and close(s["net"], -0.15)
    assert "SPORTS [backfill]" in rep  # backfill settlement logged_at is T0 here
    assert rep["BRAIN_CRYPTO"]["n_open"] == 1  # open state is not time-filtered


def test_daily_lines_and_cli():
    with ledger(synthetic()) as path:
        lines = sp.daily_lines(ledger_path=path)
        assert lines[0].startswith("Strategy P&L (fee-true")
        txt = "\n".join(lines)
        assert "DCA_CRYPTO: 30W/0L net $+3.00" in txt and "[proven]" in txt
        assert "BRAIN_CRYPTO: 2W/1L net $+0.98 (fees $0.62), 1 open, 1 unreconciled" in txt
        assert "[unproven 3/30]" in txt
        assert "backfill (fees unverified, not edge): 2 settled, net $-0.81" in txt
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert sp.main(["--ledger", path, "--json"]) == 0
        assert json.loads(out.getvalue())["DCA_CRYPTO"]["proven"] is True
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sp.main(["--ledger", path])
        assert "SPORTS [backfill]" in out.getvalue() and "fees UNVERIFIED" in out.getvalue()
    with ledger([]) as path:
        assert sp.report(ledger_path=path) == {}
        assert "no live spot/Kalshi rows yet" in "\n".join(sp.daily_lines(ledger_path=path))
    assert sp.report(ledger_path="/nonexistent/ledger.jsonl") == {}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("strategy-pnl: PASS")
