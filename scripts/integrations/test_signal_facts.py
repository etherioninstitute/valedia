#!/usr/bin/env python3
"""Facts block + money pings: every figure parsed strictly, read twice, fresh — or omitted.

Replays the v11 facts_attack cases: missing / None / renamed / garbage / NaN / inf
balance or positions -> "omitted — unavailable" (never $0.00); positions with a
cursor -> pagination (kalshi_auth.get_positions follows it; cap -> incomplete ->
omitted); ledger freshness counts only live rows, future-dated rows -> STALE;
NaN/garbage P&L rows -> omitted / withheld. Formatting: no "-$0.00", "−$5.00",
"Profit: −$X (net loss)", last 24h by settlement time. Bridge header/exit codes.
Fakes only: no network, no keys, no orders.
Run: python3 test_signal_facts.py
"""
import contextlib
import io
import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_signal_senders_rest as rest  # relay booby-traps + temp signal dir
import signal_verify as sv
import claude_bridge as cb
import kalshi_auth
import pnl_run

NOW = datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)
AT_ = "Realized P&L (all-time, ledger, all venues, fee-incl.): "
POS = {"market_positions": [{"ticker": "T", "position": 3, "market_exposure": 300}]}


def L(hours_ago=1, pnl=-12.34, extra=(), src="live"):
    return lambda: [{"type": "entry", "id": "e1", "venue": "kalshi", "source": src,
                     "logged_at": (NOW - timedelta(hours=hours_ago + 1)).isoformat()},
                    {"type": "settlement", "entry_id": "e1", "source": src, "realized_pnl_net": pnl,
                     "won": isinstance(pnl, float) and pnl > 0,
                     "logged_at": (NOW - timedelta(hours=hours_ago)).isoformat()}] + list(extra)


def seq(vals):
    it = iter(vals)
    return lambda *a: next(it)


def lines(**kw):
    kw.setdefault("get", lambda e: {"balance": 10000})
    kw.setdefault("get_positions", lambda: POS)
    kw.setdefault("ledger", L())
    return cb.facts_block(cb.verified_facts(now=NOW, **kw))[0][1:5]  # figure lines


def test_facts_attack_cases():
    ok = lines()
    assert ok == ["Cash (Kalshi, broker): $100.00", "Open-position cost (Kalshi, broker): $3.00",
                  "Open positions (Kalshi, broker): 1", "Realized P&L (all-time, ledger, all venues, fee-incl.): −$12.34 (0W/1L)"], ok
    for name, kw in [("missing", {"get": lambda e: {}}), ("None", {"get": lambda e: {"balance": None}}),
                     ("renamed", {"get": lambda e: {"balance_dollars": "1234.56"}}),
                     ("garbage", {"get": lambda e: {"balance": "n/a"}}),
                     ("nan", {"get": lambda e: {"balance": float("nan")}}),
                     ("inf", {"get": lambda e: {"balance": float("inf")}}),
                     ("bool", {"get": lambda e: {"balance": True}}),
                     ("not dict", {"get": lambda e: "oops"})]:
        got = lines(**kw)
        assert got[0] == "Cash (Kalshi, broker): omitted — unavailable", (name, got)
        assert "$0.00" not in "\n".join(got) and got[1:] == ok[1:], (name, got)
    got = lines(get=seq([{"balance": 100}, {"balance": 200}]))
    assert got[0] == "Cash (Kalshi, broker): omitted — re-read disagreed", got
    for name, pos, why in [("empty", {}, "unavailable"), ("error", {"error": "rate limited"}, "unavailable"),
                           ("cursor", dict(POS, cursor="abc"), "incomplete"),
                           ("incomplete", dict(POS, incomplete=True), "incomplete"),
                           ("nan exposure", {"market_positions": [{"ticker": "T", "position": 1,
                                                                   "market_exposure": float("nan")}]}, "unavailable"),
                           ("no size", {"market_positions": [{"ticker": "T", "market_exposure": 300}]}, "unavailable"),
                           ("garbage size", {"market_positions": [{"ticker": "T", "position": "x",
                                                                   "market_exposure": 300}]}, "unavailable")]:
        got = lines(get_positions=lambda pos=pos: pos)
        assert got[1:3] == [f"Open-position cost (Kalshi, broker): omitted — {why}",
                            f"Open positions (Kalshi, broker): omitted — {why}"], (name, got)
    got = lines(get_positions=seq([POS, {"market_positions": []}]))
    assert got[1] == "Open-position cost (Kalshi, broker): omitted — re-read disagreed", got
    got = lines(get_positions=lambda: dict(POS, ts=(NOW - timedelta(hours=2)).isoformat()))
    assert got[1] == "Open-position cost (Kalshi, broker): omitted — STALE", got
    for name, kw, why in [
            ("stale 40h open", {"ledger": L(40)}, "STALE (no recent live rows)"),
            ("broker down, assume open", {"ledger": L(40), "get_positions": lambda: 1 / 0},
             "STALE (no recent live rows)"),
            ("future row", {"ledger": L(720, extra=[{"type": "entry", "id": "f", "source": "live",
                                                     "logged_at": (NOW + timedelta(days=365)).isoformat()}])},
             "STALE (future-dated rows, suspect)"),
            ("fresh only via paper row", {"ledger": L(720, extra=[{"type": "entry", "id": "p", "source": "paper",
                                                                   "venue": "kalshi", "logged_at": NOW.isoformat()}])},
             "STALE (no recent live rows)"),
            ("fresh only via note row", {"ledger": L(720, extra=[{"type": "note", "logged_at": NOW.isoformat()}])},
             "STALE (no recent live rows)"),
            ("nan pnl", {"ledger": L(pnl=float("nan"))}, "unavailable (unparseable rows)"),
            ("string pnl", {"ledger": L(pnl="abc")}, "unavailable (unparseable rows)")]:
        got = lines(**kw)
        assert got[3] == f"Realized P&L (all-time, ledger, all venues, fee-incl.): omitted — {why}", (name, got)
    assert lines(ledger=L(pnl=-0.004))[3] == "Realized P&L (all-time, ledger, all venues, fee-incl.): $0.00 (0W/1L)"
    assert lines(ledger=L(pnl=0.004))[3] == "Realized P&L (all-time, ledger, all venues, fee-incl.): $0.00 (1W/0L)"
    assert lines(get=lambda e: {"balance": -500})[0] == "Cash (Kalshi, broker): −$5.00"


def test_money_formatting():
    m = sv.money
    assert (m(-0.004, True), m(0.004, True), m(-0.005, True), m(0.005, True)) == \
        ("$0.00", "$0.00", "−$0.01", "+$0.01")
    assert (m(-5), m(-5, True), m(5, True), m(1234.5), m(0)) == \
        ("−$5.00", "−$5.00", "+$5.00", "$1,234.50", "$0.00")
    assert m(2.675) == "$2.68" and m(-2.675) == "−$2.68"  # half away from zero, exact decimal
    assert sv.num("1.5") == 1.5 and sv.num(None) is None and sv.num("n/a") is None
    assert sv.num(float("nan")) is None and sv.num(True) is None and sv.num(float("-inf")) is None


def test_pnl_ping_loss_and_settlement_time():
    rest._fresh_dir()
    sent = []
    pos = lambda: {"market_positions": [{"ticker": "T", "position": 3,  # noqa: E731
                                         "market_exposure_dollars": "40.00"}]}
    rows = L(1, pnl=-12.34)()
    rows.append({"type": "entry", "id": "e2", "venue": "kalshi", "source": "live",
                 "logged_at": (NOW - timedelta(hours=1)).isoformat()})
    rows.append({"type": "settlement", "entry_id": "e2", "source": "live", "realized_pnl_net": 2.0,
                 "won": True, "settled_at": (NOW - timedelta(days=3)).isoformat(),
                 "logged_at": (NOW - timedelta(hours=1)).isoformat()})  # booked late: not last 24h
    r = pnl_run.pnl_report(get_positions=pos, ledger=lambda: rows,
                           send_fn=lambda m: sent.append(m) or True, now=NOW)
    t = sent[0]
    assert r["status"] == "verified", r
    assert "Open-position cost (Kalshi, broker): $40.00" in t, t
    assert "Realized P&L (all-time, ledger, all venues, fee-incl.): −$10.34 (net loss) · 1W/1L\nSettled last 24h (ledger): −$12.34" in t, t
    rest._fresh_dir()
    sent.clear()
    pnl_run.pnl_report(get_positions=pos, ledger=L(1, pnl=-0.004), send_fn=lambda m: sent.append(m) or True,
                       now=NOW)
    assert "Realized P&L (all-time, ledger, all venues, fee-incl.): $0.00 · 0W/1L" in sent[0] and "-$0.00" not in sent[0] and "net loss" not in sent[0]
    rest._fresh_dir()
    sent.clear()
    r = pnl_run.pnl_report(get_positions=pos, ledger=L(1, pnl=float("nan")),
                           send_fn=lambda m: sent.append(m) or True, now=NOW)
    assert r["status"] == "verified" and f"{AT_}omitted — 1 ledger row(s) unparseable" in sent[0], sent
    assert "nan" not in sent[0].lower() and "$0.00" not in sent[0].split("Open-position")[0]
    rest._fresh_dir()
    sent.clear()
    fut = L(1, extra=[{"type": "entry", "id": "f", "source": "live",
                       "logged_at": (NOW + timedelta(days=30)).isoformat()}])
    pnl_run.pnl_report(get_positions=pos, ledger=fut, send_fn=lambda m: sent.append(m) or True, now=NOW)
    assert sent[0].startswith("⚠ STALE: ledger has future-dated rows (suspect)"), sent
    for bad in ({}, {"error": "x"}, dict(POS, cursor="c")):
        rest._fresh_dir()
        sent.clear()
        r = pnl_run.pnl_report(get_positions=lambda bad=bad: bad, ledger=L(1),
                               send_fn=lambda m: sent.append(m) or True, now=NOW)
        assert r["status"] == "withheld" and "$0.00" not in sent[0], (bad, sent)


def test_get_positions_paginates_read_only():
    calls = []
    pages = {"": {"market_positions": [{"ticker": "A"}], "cursor": "c1"},
             "c1": {"market_positions": [{"ticker": "B"}], "event_positions": [{"e": 1}], "cursor": "c2"},
             "c2": {"market_positions": [{"ticker": "C"}], "cursor": ""}}

    def fake_get(ep):
        calls.append(ep)
        assert ep.startswith("/portfolio/positions?limit=")  # GET of positions only
        cur = ep.split("cursor=")[1] if "cursor=" in ep else ""
        return pages[cur]
    out = kalshi_auth.get_positions(_get=fake_get)
    assert [p["ticker"] for p in out["market_positions"]] == ["A", "B", "C"] and len(calls) == 3
    assert out["cursor"] == "" and not out.get("incomplete") and out["event_positions"] == [{"e": 1}]
    n = iter(range(100))
    loop = lambda ep: {"market_positions": [{"ticker": f"X{next(n)}"}], "cursor": "again"}  # noqa: E731
    out = kalshi_auth.get_positions(_get=loop, max_pages=3)
    assert out["incomplete"] is True and len(out["market_positions"]) == 3
    try:
        sv.broker_snapshot(lambda: out)
        raise AssertionError("incomplete positions must not be summed")
    except ValueError as e:
        assert "incomplete" in str(e)
    assert kalshi_auth.get_positions(_get=lambda ep: {"error": "x"}) == {"error": "x"}


def _args(q):
    return types.SimpleNamespace(question=q, no_data=True, model="sonnet", signal=False, router=True)


def _f():
    return {"cash": (250.0, 250.0), "positions": {"invested": (40.0, 40.0), "n": (3, 3)},
            "ledger": {"total": (-5.0, -5.0), "wins": (2, 2), "losses": (3, 3)}, "why": {}}


def test_bridge_header_and_exit_codes():
    rest._fresh_dir()
    for answer, rc in (("You made $124 profit", 0), ("Error: timeout", 4)):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = cb.run(_args(answer), ask=lambda *a, **k: answer, facts_fn=_f)
        out = buf.getvalue().rstrip("\n").split("\n")
        assert got == rc, (answer, got, out)
        if rc == 0:
            assert out[0] == cb.gate_header("verified", "\n".join(out[1:])), out
            shown = [l for l in out if not l.startswith(cb.AI_LINE)]  # saved-file path has digits
            assert "124" not in "\n".join(shown) and "profit" not in "\n".join(shown).lower()
            assert out[-1] == cb.FOOTER and out[-2].startswith(cb.AI_LINE)
        else:
            assert not out[0].startswith("VALEDIA-GATE"), out
    buf = io.StringIO()
    bad = dict(_f(), positions={"invested": (40.0, 40.0), "n": (3, 4)})
    with contextlib.redirect_stdout(buf):
        got = cb.run(_args("x"), ask=lambda *a, **k: "ok", facts_fn=lambda: bad)
    assert got == 3 and buf.getvalue().split("\n")[1].startswith("Report withheld (AI answer)")


if __name__ == "__main__":
    test_facts_attack_cases()
    test_money_formatting()
    test_pnl_ping_loss_and_settlement_time()
    test_get_positions_paginates_read_only()
    test_bridge_header_and_exit_codes()
    print("signal_facts: PASS")
