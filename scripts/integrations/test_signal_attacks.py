#!/usr/bin/env python3
"""v12 attack replay: facts / Profit / Invested pings and the other senders.

facts_attack: renamed / None / NaN / huge / non-ASCII / bool balance -> omitted; venue
timestamps garbage or in the future -> STALE; duplicate settlements counted once;
orphan or flagless settlements -> omitted. settle_attack: missing / None / NaN / inf /
garbage realized_pnl_net -> withheld; formatting via signal_verify.money everywhere.
inv_attack: renamed / NaN / garbage / out-of-range price, missing fee, duplicate fill
rows -> withheld. kalshi_alert / portfolio_monitor / btc_day_reminder audited with the
same classes. Fakes only: no network, no keys, no orders.
Run: python3 test_signal_attacks.py
"""
import os
import shutil
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_signal_senders_rest as rest  # relay booby-traps + temp signal dir
import signal_verify as sv
import claude_bridge as cb
import settle_run as sr
import pnl_run
import kalshi_alert as ka
import portfolio_monitor as pm
import btc_day_reminder as btc

NOW = datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)
t = lambda h: (NOW - timedelta(hours=h)).isoformat()  # noqa: E731
POS = lambda: {"market_positions": [{"ticker": "A", "position": 1, "market_exposure": 250}],  # noqa: E731
               "cursor": ""}


def led(pnls, won=None, extra=None, missing=()):
    rows = []
    for i, p in enumerate(pnls):
        rows.append({"type": "entry", "id": f"e{i}", "venue": "kalshi", "source": "live", "logged_at": t(5)})
        s = {"type": "settlement", "entry_id": f"e{i}", "source": "live", "logged_at": t(0.1),
             "won": (won[i] if won else (isinstance(p, (int, float)) and p > 0))}
        if i not in missing:
            s["realized_pnl_net"] = p
        rows.append(s)
    return rows + list(extra or [])


def seq(*vals):
    it, last = iter(vals), [None]

    def f(*a):
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return f


def facts(get=None, gp=None, ledger=None):
    f = cb.verified_facts(get=get or (lambda e: {"balance": 10000}), get_positions=gp or POS,
                          ledger=ledger or (lambda: led([1.0])), now=NOW)
    return cb.facts_block(f)[0][1:5]  # figure lines (mismatch line tested separately)


def test_facts_attack():
    base = facts()
    assert base == ["Cash (Kalshi, broker): $100.00", "Open-position cost (Kalshi, broker): $2.50",
                    "Open positions (Kalshi, broker): 1", "Realized P&L (all-time, ledger, all venues, fee-incl.): +$1.00 (1W/0L)"], base
    omitted = "Cash (Kalshi, broker): omitted — unavailable"
    for bal in ({"balance": 1e40}, {"balance": 1e20}, {"balance_cents": 100}, {"balance": True},
                {"balance": [1]}, {"balance": "١٢٣"}, {"balance": "NaN"}, {"balance": "1e400"}):
        assert facts(get=lambda e, b=bal: b)[0] == omitted, bal
    assert facts(get=lambda e: {"balance": "12345"})[0] == "Cash (Kalshi, broker): $123.45"
    for bal, want in ((-0.0, "$0.00"), (-0.4, "$0.00"), (0.5, "$0.01"), (-0.5, "−$0.01")):
        assert facts(get=lambda e, b=bal: {"balance": b})[0] == f"Cash (Kalshi, broker): {want}", bal
    assert facts(get=seq({"balance": 100}, {"balance": 100.4}))[0] == "Cash (Kalshi, broker): $1.00"
    r = lambda rows: facts(ledger=lambda: rows)[3]  # noqa: E731
    assert r(led([-0.005])) == "Realized P&L (all-time, ledger, all venues, fee-incl.): −$0.01 (0W/1L)"
    assert r(led([0.005])) == "Realized P&L (all-time, ledger, all venues, fee-incl.): +$0.01 (1W/0L)"
    assert r(led([-0.0049])) == r(led([-0.0])).replace("0W/1L", "0W/1L") and "-$0.00" not in r(led([-0.0049]))
    assert r(led(["-3.5"], won=[False])) == "Realized P&L (all-time, ledger, all venues, fee-incl.): −$3.50 (0W/1L)"
    assert r(led([1.005, 2.675])) == "Realized P&L (all-time, ledger, all venues, fee-incl.): +$3.68 (2W/0L)"
    assert r(led([-5.0], won=["false"])) == "Realized P&L (all-time, ledger, all venues, fee-incl.): −$5.00 (0W/1L)"
    unp = "Realized P&L (all-time, ledger, all venues, fee-incl.): omitted — 1 ledger row(s) unparseable"
    for rows in (led([float("nan")]), led([None]), led([1e30]), led([5.0], won=[None]),
                 led([5.0], won=["yes"]), led([5.0], won=[1]),
                 led([5.0], extra=[{"type": "settlement", "entry_id": "zz", "source": "live",
                                    "logged_at": t(1), "realized_pnl_net": -50.0, "won": False}])):
        assert r(rows) == unp, rows
    dup = led([5.0], extra=[{"type": "settlement", "entry_id": "e0", "source": "live", "logged_at": t(1),
                             "realized_pnl_net": 5.0, "won": True}])
    assert r(dup) == "Realized P&L (all-time, ledger, all venues, fee-incl.): +$5.00 (1W/0L)", r(dup)  # counted once
    fut = led([5.0], extra=[{"type": "entry", "id": "f", "source": "live",
                             "logged_at": (NOW + timedelta(days=1)).isoformat()}])
    assert r(fut).endswith("STALE (future-dated rows, suspect)")
    assert facts(ledger=seq(led([1.0]), led([2.0])))[3].endswith("re-read disagreed")
    assert facts(ledger=seq(led([1.0]), led([1.0, 0.0])))[3].endswith("re-read disagreed")
    p = lambda pos: facts(gp=lambda: pos)[1]  # noqa: E731
    assert p({"market_positions": [], "cursor": "abc"}).endswith("omitted — incomplete")
    for pos in ({"positions": []}, {"market_positions": [{"ticker": "A", "position": 1}]},
                {"market_positions": [{"ticker": "A", "position": 1, "market_exposure": "NaN"}]},
                {"market_positions": [{"ticker": "A", "position": 0, "market_exposure": 250}]},
                {"market_positions": [{"ticker": "A", "position": 1, "market_exposure": 1e20}]}):
        assert p(pos).endswith("omitted — unavailable"), pos
    assert p({"market_positions": [{"ticker": "A", "position": "1", "market_exposure": "250"}]}) == \
        "Open-position cost (Kalshi, broker): $2.50"
    assert p({"market_positions": [{"ticker": "A", "position_fp": "1.00",
                                    "market_exposure_dollars": "2.5"}]}) == "Open-position cost (Kalshi, broker): $2.50"
    assert p({"market_positions": [{"ticker": "A", "position": -3, "market_exposure": -250}]}) == \
        "Open-position cost (Kalshi, broker): $2.50"
    for vts in (t(5), (NOW + timedelta(days=3)).isoformat(), "garbage", 12345, "2026-13-45"):
        assert p({"market_positions": [], "ts": vts}).endswith("omitted — STALE"), vts
    assert p({"market_positions": [], "ts": t(0.01)}) == "Open-position cost (Kalshi, broker): $0.00"


P = "Profit (settled since last report): "
AT = "Realized P&L (all-time, ledger, all venues, fee-incl.): "


def _ping(recs):
    """Full settle_run.run() on these rows (fresh, existing-but-empty sent-record)."""
    from test_signal_v14 import Settle
    with Settle(fresh_record=True) as st_:
        st_.write([r for r in recs])
        return st_.run(NOW)


def test_settle_attack():
    sent = _ping(led([2.5, -1.0], won=[True, False]))
    assert sent == [f"{P}+$1.50 (1W/1L)\n{AT}+$1.50 (1W/1L)"], sent
    warn = "⚠ 1 ledger row(s) unparseable — all-time total omitted"
    for name, recs in [("missing", led([2.0, 3.0], missing=(0,))), ("None", led([None, 3.0]))]:
        assert _ping(recs) == [f"{P}+$3.00 (1W/0L)\n{warn}"], name  # bad row never blocks a valid one
    for name, recs in [("nan", led([float("nan")])), ("nan str", led(["NaN"])), ("inf", led([float("inf")])),
                       ("garbage", led(["abc"])), ("huge", led([1e30])), ("won None", led([1.0], won=[None])),
                       ("won str", led([1.0], won=["maybe"]))]:
        assert _ping(recs) == ["⚠ 1 ledger row(s) unparseable — their figures withheld until fixed"], name
    orphan = led([1.0], extra=[{"type": "settlement", "entry_id": "zz", "source": "live",
                                "logged_at": t(0.1), "realized_pnl_net": 9.0, "won": True}])
    assert _ping(orphan) == [f"{P}+$1.00 (1W/0L)\n{warn}"]
    for pnl, won, want in ((-0.004, False, f"{P}$0.00 (0W/1L)"), (0.125, True, f"{P}+$0.13 (1W/0L)"),
                           (-0.125, False, f"{P}−$0.13 (net loss) (0W/1L)"),
                           (-7.5, False, f"{P}−$7.50 (net loss) (0W/1L)")):
        sent = _ping(led([pnl], won=[won]))
        assert sent and sent[0].startswith(want), (pnl, sent)
    dup = led([5.0], extra=[{"type": "settlement", "entry_id": "e0", "source": "live", "logged_at": t(0.05),
                             "realized_pnl_net": 5.0, "won": True}])
    assert _ping(dup) == [f"{P}+$5.00 (1W/0L)\n{AT}+$5.00 (1W/0L)"]  # identical duplicate counted once


def test_pnl_run_attack():
    for recs, want in ((led([-0.004], won=[False]), "Realized P&L (all-time, ledger, all venues, fee-incl.): $0.00 · 0W/1L"),
                       (led([0.125]), "Realized P&L (all-time, ledger, all venues, fee-incl.): +$0.13 · 1W/0L"),
                       (led([-7.5], won=[False]), "Realized P&L (all-time, ledger, all venues, fee-incl.): −$7.50 (net loss) · 0W/1L")):
        rest._fresh_dir()
        sent = []
        r = pnl_run.pnl_report(get_positions=POS, ledger=lambda: recs, send_fn=lambda m: sent.append(m) or True,
                               now=NOW)
        assert r["status"] == "verified" and want in sent[0], (want, sent)
    rest._fresh_dir()
    sent = []
    r = pnl_run.pnl_report(get_positions=POS, ledger=lambda: led([1.0], missing=(0,)),
                           send_fn=lambda m: sent.append(m) or True, now=NOW)
    assert r["status"] == "verified" and "fee-incl.): omitted — 1 ledger row(s) unparseable" in sent[0], sent
    for name, gp, recs in (("huge exposure", lambda: {"market_positions": [{"ticker": "A", "position": 1,
                                                                            "market_exposure": 1e18}]}, led([1.0])),
                           ("garbage venue ts", lambda: dict(POS(), ts="garbage"), led([1.0])),
                           ("future venue ts", lambda: dict(POS(), ts=(NOW + timedelta(days=2)).isoformat()),
                            led([1.0])),
                           ("broker raises", lambda: 1 / 0, led([1.0]))):
        rest._fresh_dir()
        sent = []
        r = pnl_run.pnl_report(get_positions=gp, ledger=lambda: recs, send_fn=lambda m: sent.append(m) or True,
                               now=NOW)
        assert r["status"] == "withheld" and len(sent) == 1 and sent[0].startswith(
            "Report withheld (11pm P&L)"), (name, r, sent)


def test_invested_attack():
    ex = [{"order_id": "o1", "cost": 4.0, "fee": 0.07}, {"order_id": "o2", "cost": 4.0, "fee": 0.07}]
    e = lambda oid, **k: dict({"type": "entry", "id": oid, "order_id": oid, "source": "live",  # noqa: E731
                               "logged_at": t(0.05), "entry_price": 0.4, "contracts": 10,
                               "entry_fee": 0.07}, **k)

    def go(entries, executed=ex):
        rest._fresh_dir()
        sent = []
        r = sv.invested_ping(executed, ledger=lambda: entries, send_fn=lambda m: sent.append(m) or True, now=NOW)
        return r, sent
    r, sent = go([e("o1"), e("o2")])
    assert r["status"] == "verified" and sent[0].startswith("Sports fills cost incl. fees (ledger): $8.14 on 2 games"), sent
    for name, rows in [("renamed", [e("o1", entry_price=None, price=0.4), e("o2", entry_price=None, price=0.4)]),
                       ("nan", [e("o1", entry_price=float("nan")), e("o2")]),
                       ("garbage", [e("o1", entry_price="abc"), e("o2")]),
                       ("cents", [e("o1", entry_price=40), e("o2", entry_price=40)]),
                       ("fee missing", [e("o1", entry_fee=None), e("o2", entry_fee=None)]),
                       ("contracts zero", [e("o1", contracts=0), e("o2")]),
                       ("neg fee", [e("o1", entry_fee=-1), e("o2")]),
                       ("dup row, one fill", [e("o1"), e("o1")])]:
        r, sent = go(rows)
        assert r["status"] == "withheld" and len(sent) == 1 and "$" not in sent[0], (name, sent)
    r, sent = go([e("o1"), e("o1", entry_price=0.9), e("o2")])  # duplicate rows never double-count
    assert sent[0].startswith("Sports fills cost incl. fees (ledger): $8.14 on 2 games"), sent


def test_kalshi_alert_classes():
    def ke(sim=None, edge=0.15):
        def execute_trade(o, state, dry_run=False):
            return sim if sim is not None else {"status": "DRY_RUN", "contracts": 10, "total_cost": 8.0,
                                                "potential_profit": 1.5}
        return types.SimpleNamespace(_cfg=None, execute_trade=execute_trade)
    opp = [{"ticker": "A", "strategy": "NO_HARVEST", "price": 0.8, "edge": 0.15}]
    st = {"balance": 20.0, "max_deployable": 15.0}
    rest._fresh_dir()
    sent = []
    r = ka.brief(ke=ke(), get_positions=POS, send_fn=lambda m: sent.append(m) or True, now=NOW,
                 state=st, opps=opp)
    assert r["status"] == "verified" and "Open-position cost (Kalshi, broker): $2.50" in sent[0] and "Modeled if-all-win: +$1.50" in sent[0]
    for name, kw in [("nan cost", {"k": ke({"status": "DRY_RUN", "contracts": 10, "total_cost": float("nan"),
                                            "potential_profit": 1.5})}),
                     ("garbage upside", {"k": ke({"status": "DRY_RUN", "contracts": 10, "total_cost": 8.0,
                                                  "potential_profit": "lots"})}),
                     ("renamed cost", {"k": ke({"status": "DRY_RUN", "contracts": 10, "cost": 8.0,
                                                "potential_profit": 1.5})}),
                     ("balance missing", {"state": {"max_deployable": 15.0}}),
                     ("balance nan", {"state": {"balance": float("nan"), "max_deployable": 15.0}}),
                     ("edge nan", {"opps": [dict(opp[0], edge=float("nan"))]}),
                     ("stale venue", {"gp": lambda: dict(POS(), ts=t(3))}),
                     ("garbage venue", {"gp": lambda: dict(POS(), ts="x")}),
                     ("positions renamed", {"gp": lambda: {"positions": []}})]:
        rest._fresh_dir()
        sent = []
        r = ka.brief(ke=kw.get("k", ke()), get_positions=kw.get("gp", POS),
                     send_fn=lambda m: sent.append(m) or True, now=NOW, state=kw.get("state", st),
                     opps=kw.get("opps", opp))
        assert r["status"] == "withheld" and sent[0].startswith("Report withheld (7am brief)"), (name, r)
        assert "nan" not in sent[0].lower() and "$" not in sent[0], (name, sent)


def test_portfolio_monitor_and_btc_day():
    line = pm.fmt_alpaca({"equity": "1234.5", "cash": None, "buying_power": "NaN", "status": "ACTIVE"},
                         [{"symbol": "SPY", "qty": "1", "current_price": "abc", "unrealized_pl": "-5"}], True)
    assert line == ("Alpaca (PAPER, broker): $1,234.50 equity | n/a cash | n/a buying power | "
                    "1 positions: SPY 1@n/a (unrealized P&L:−$5.00) | Status: ACTIVE"), line
    assert pm.fmt_mercury([{"kind": "checking"}, {"kind": "savings", "availableBalance": -0.004}]) == \
        "Mercury: checking: n/a | savings: $0.00"
    assert pm.fmt_kraken({"XBT": "0.5", "ETH": "NaN", "ZUSD": "0"}) == "Kraken: XBT: 0.5 | ETH: n/a"
    assert pm.fmt_alpaca(None, None, False).startswith("Alpaca (LIVE, broker): n/a equity")
    rest._fresh_dir()
    sent = []
    btc.main(send_fn=lambda m: sent.append(m) or True, now=NOW, cron_cleanup=lambda: True)
    assert sent and sent[0].startswith("Report withheld (btc.day reminder)") and "$" not in sent[0]


if __name__ == "__main__":
    test_facts_attack()
    test_settle_attack()
    test_pnl_run_attack()
    test_invested_attack()
    test_kalshi_alert_classes()
    test_portfolio_monitor_and_btc_day()
    print("signal_attacks: PASS")
