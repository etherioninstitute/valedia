#!/usr/bin/env python3
"""v13 replay: settle_run end-to-end (corrupt / non-object / future / numeric-time /
orphan rows -> one withheld notice, cursor never skips or passes now, pending rows sent
once valid), positions de-duplication, sports unconfirmed-order notice, facts labels
and ledger/broker mismatch line, kalshi_alert fence fail-closed, Decimal sums, bridge
docs/server "local only". Fakes only: no network, no keys, no orders.
Run: python3 test_signal_v13.py
"""
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_signal_senders_rest as rest  # relay booby-traps + temp signal dir
import signal_verify as sv
import signal_send
import forward_test_ledger as ftl
import settle_run as sr
import pnl_run
import claude_bridge as cb
import kalshi_alert as ka
import kalshi_auth
import sports_engine as se
from test_sports_engine import _game
from test_sports_engine_b1_extra import _patch, _restore, _result

NOW = datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)
t = lambda h: (NOW - timedelta(hours=h)).isoformat()  # noqa: E731
WH = "Report withheld (Profit): "


def ent(i, h=5):
    return {"type": "entry", "id": i, "venue": "kalshi", "source": "live", "logged_at": t(h),
            "order_id": "o" + i, "entry_price": 0.5, "contracts": 2, "entry_fee": 0.02}


def st(i, p, won, ts):
    return {"type": "settlement", "entry_id": i, "source": "live", "logged_at": ts,
            "realized_pnl_net": p, "won": won}


class Settle:
    """Isolated settle_run: temp ledger + models dir, no sports log, no arb module."""

    def __enter__(self):
        rest._fresh_dir()
        self.dir = tempfile.mkdtemp(prefix="settle13_")
        self.saved = (ftl.LEDGER, sr.MODELS, sr.settle_sports, sys.modules.get("latency_arb"))
        ftl.LEDGER, sr.MODELS = os.path.join(self.dir, "ledger.jsonl"), self.dir
        sr.settle_sports = lambda: ([], {})
        sys.modules["latency_arb"] = types.ModuleType("latency_arb")
        return self

    def __exit__(self, *a):
        ftl.LEDGER, sr.MODELS, sr.settle_sports = self.saved[:3]
        if self.saved[3] is None:
            sys.modules.pop("latency_arb", None)
        else:
            sys.modules["latency_arb"] = self.saved[3]

    def write(self, rows, extra=()):
        with open(ftl.LEDGER, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + "".join(l + "\n" for l in extra))

    def run(self, now=NOW):
        sent = []
        sr.run(send_fn=lambda m: sent.append(m) or True, now=now)
        return sent

    def cursor(self):
        return sv.parse_ts(open(os.path.join(self.dir, "settle_ftl_signal_cursor.txt")).read())


def _profit(sent):
    return [m for m in sent if m.startswith("Profit (settled since last report)")]


def test_settle_corrupt_and_nonobject_then_fixed():
    for extra, rows_extra, cause in (
            (['{"type": "settlement", "entry_id": "b", "realized_pnl'], [], "truncated / invalid JSON line"),
            ([], [[1, 2]], "non-object row")):
        with Settle() as s:
            good = [ent("a"), st("a", 3.0, True, t(0.1))]
            s.write(good + rows_extra, extra)
            first = s.run()
            assert first == [WH + f"ledger has a {cause}. Unverified figures not sent; see "
                             "data/signal/verify_log.jsonl."], first
            assert s.run() == []  # rate-limited: one notice per cause per 7 days
            assert s.cursor() <= NOW - timedelta(minutes=49)  # never advanced past held rows
            s.write(good)  # fixed
            sent = s.run()
            assert _profit(sent) and _profit(sent)[0].startswith(
                "Profit (settled since last report): +$3.00 (1W/0L)"), sent


def test_settle_future_row_blocks_then_pending_rows_sent():
    with Settle() as s:
        fut = st("b", 1.0, True, (NOW + timedelta(days=400)).isoformat())
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), ent("b"), fut])
        r1 = s.run()
        assert r1 == [WH + "ledger has future-dated rows. Unverified figures not sent; see "
                      "data/signal/verify_log.jsonl."], r1
        assert s.run() == []
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), ent("b"), fut, ent("c"), st("c", -9.0, False, t(0))])
        assert s.run() == []  # still withheld (deduped notice) while the future row exists
        assert s.cursor() <= NOW
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), ent("c"), st("c", -9.0, False, t(0))])  # fixed
        sent = s.run()
        assert _profit(sent)[0].startswith(
            "Profit (settled since last report): −$6.00 (net loss) (1W/1L)"), sent  # −9 sent once valid
        assert s.cursor() <= NOW and s.run() == []
        with open(os.path.join(s.dir, "settle_ftl_signal_cursor.txt"), "w") as f:
            f.write((NOW + timedelta(days=30)).isoformat())  # impossible cursor: window restarts
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), ent("c"), st("c", -9.0, False, t(0)),
                 ent("d"), st("d", 2.0, True, (NOW + timedelta(minutes=1)).isoformat())])
        sent = s.run(now=NOW + timedelta(minutes=2))  # d is not skipped; a, c never re-reported
        assert _profit(sent)[0].startswith("Profit (settled since last report): +$2.00 (1W/0L)"), sent
        assert s.cursor() <= NOW + timedelta(minutes=2)


def test_settle_sent_set_no_double_report_after_cursor_reset():
    with Settle() as s:
        rows = [ent("a"), st("a", 3.0, True, t(0.2)), ent("b"), st("b", -1.0, False, t(0.1))]
        s.write(rows)
        first = _profit(s.run())
        assert first and first[0].startswith("Profit (settled since last report): +$2.00 (1W/1L)"), first
        cur = os.path.join(s.dir, "settle_ftl_signal_cursor.txt")
        for reset in (None, "garbage", (NOW - timedelta(days=30)).isoformat(),
                      (NOW + timedelta(days=9)).isoformat()):
            if reset is None:
                os.remove(cur)
            else:
                open(cur, "w").write(reset)
            assert s.run() == [], reset  # same settlements never reported twice
        ids = sr.load_sent_ids()
        assert {"a", "b"} <= ids
        s.write(rows + [ent("c"), st("c", 4.0, True, t(0))])
        os.remove(cur)
        again = _profit(s.run())
        assert again and again[0].startswith("Profit (settled since last report): +$4.00 (1W/0L)"), again
        assert "Total (all-time realized, ledger): +$6.00 (2W/1L)" in again[0], again
        saved = sr.SENT_IDS_MAX
        try:  # bounded: only the newest ids are kept
            sr.SENT_IDS_MAX = 2
            sr.add_sent_ids(["x1", "x2", "x3"])
            assert sr.load_sent_ids() == {"x2", "x3"}
        finally:
            sr.SENT_IDS_MAX = saved


def test_settle_numeric_time_and_orphan_never_skip_valid_rows():
    with Settle() as s:
        s.write([ent("a"), st("a", 3.0, True, t(0.2))])
        assert _profit(s.run())
        bad_b = st("b", -4.0, False, 1790000000)
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), ent("b"), bad_b])
        r = s.run()
        assert r == [WH + "ledger settlement rows unparseable. Unverified figures not sent; see "
                     "data/signal/verify_log.jsonl."], r
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), ent("b"), bad_b, ent("c"), st("c", 2.0, True, t(0))])
        assert s.run() == []
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), ent("b"), st("b", -4.0, False, t(0.05)),
                 ent("c"), st("c", 2.0, True, t(0))])  # b's time fixed
        sent = s.run()
        assert _profit(sent)[0].startswith(
            "Profit (settled since last report): −$2.00 (net loss) (1W/1L)"), sent  # b and c, once
    with Settle() as s:
        orphan = {"type": "settlement", "entry_id": "zz", "source": "live", "logged_at": t(0.1),
                  "realized_pnl_net": 1, "won": True}
        s.write([ent("a"), st("a", 3.0, True, t(0.2)), orphan])
        assert s.run()[0].startswith(WH) and s.run() == []
        s.write([ent("a"), st("a", 3.0, True, t(0.2))])  # orphan removed -> pending row sent
        assert _profit(s.run())[0].startswith("Profit (settled since last report): +$3.00")


POS1 = {"ticker": "A", "side": "yes", "position": 1, "market_exposure": 250}


def test_positions_dedupe():
    snap = sv.broker_snapshot(lambda: {"market_positions": [POS1, dict(POS1)]})
    assert snap["n"] == 1 and snap["invested"] == 2.5
    conflict = {"market_positions": [POS1, dict(POS1, position=2, market_exposure=500)]}
    try:
        sv.broker_snapshot(lambda: conflict)
        raise AssertionError("conflicting duplicates must not be summed")
    except ValueError as e:
        assert "incomplete" in str(e)
    f = cb.verified_facts(get=lambda e: {"balance": 100}, get_positions=lambda: conflict,
                          ledger=lambda: [], now=NOW)
    assert "Open-position cost (Kalshi, broker): omitted — incomplete" in cb.facts_block(f)[0]
    rest._fresh_dir()
    sent = []
    r = pnl_run.pnl_report(get_positions=lambda: conflict, ledger=lambda: [ent("a"), st("a", 1.0, True, t(1))],
                           send_fn=lambda m: sent.append(m) or True, now=NOW)
    assert r["status"] == "withheld" and sent[0].startswith("Report withheld (11pm P&L)"), sent
    pages = iter([{"market_positions": [POS1], "cursor": "c1"}, {"market_positions": [dict(POS1)], "cursor": ""}])
    out = kalshi_auth.get_positions(_get=lambda ep: next(pages))
    assert len(out["market_positions"]) == 1 and not out.get("incomplete")
    pages = iter([{"market_positions": [POS1], "cursor": "c1"},
                  {"market_positions": [dict(POS1, position=3)], "cursor": ""}])
    assert kalshi_auth.get_positions(_get=lambda ep: next(pages))["incomplete"] is True
    st_ = {"balance": 20.0, "max_deployable": 15.0}
    ke = types.SimpleNamespace(_cfg=None, execute_trade=lambda o, s, dry_run=False: {
        "status": "DRY_RUN", "contracts": 10, "total_cost": 8.0, "potential_profit": 1.5})
    rest._fresh_dir()
    sent = []
    r = ka.brief(ke=ke, get_positions=lambda: conflict, send_fn=lambda m: sent.append(m) or True,
                 now=NOW, state=st_, opps=[{"ticker": "B", "strategy": "NO_HARVEST", "edge": 0.15}])
    assert r["status"] == "withheld", r


def test_facts_labels_and_open_mismatch():
    pos = lambda: {"market_positions": [dict(POS1, ticker=x) for x in "ABC"]}  # noqa: E731
    rows = [ent("a"), st("a", -5.0, False, t(1)), ent("open1", h=1)]
    f = cb.verified_facts(get=lambda e: {"balance": 25000}, get_positions=pos, ledger=lambda: rows, now=NOW)
    lines = cb.facts_block(f)[0]
    assert lines[1:] == ["Cash (Kalshi, broker): $250.00", "Open-position cost (Kalshi, broker): $7.50",
                         "Open positions (Kalshi, broker): 3",
                         "Realized P&L (ledger, all venues, fee-incl.): −$5.00 (0W/1L)",
                         "⚠ ledger/broker open-position mismatch (1 vs 3) — not reconciled"], lines
    rows.append(ent("open2", h=1))
    rows.append(ent("open3", h=1))
    f = cb.verified_facts(get=lambda e: {"balance": 25000}, get_positions=pos, ledger=lambda: rows, now=NOW)
    assert not any("mismatch" in l for l in cb.facts_block(f)[0])
    rest._fresh_dir()
    send = []
    cb.gate_answer("q", "x", cb.verified_facts(get=lambda e: {"balance": 25000}, get_positions=pos,
                                               ledger=lambda: rows[:3], now=NOW),
                   send=True, send_fn=lambda m: send.append(m) or True, now=NOW)
    assert "⚠ ledger/broker open-position mismatch (1 vs 3) — not reconciled" in send[0], send


def test_sports_unconfirmed_notice():
    rest._fresh_dir()
    patches, sent = [], []
    _patch(signal_send, "send", lambda m: sent.append(m) or True, patches)
    try:
        _patch(se, "check_settlements", lambda: ([], {}), patches)
        _patch(se, "get_balance", lambda: {"balance": 100000}, patches)
        _patch(se, "get_fear_greed", lambda: 50, patches)
        _patch(se, "get_optimal_bet_pct", lambda: 0.06, patches)
        _patch(se, "scan_games", lambda: [_game()], patches)
        _patch(se.ftl, "should_trade_category", lambda cat: (True, "ok"), patches)
        res = [dict(_result("UNRECONCILED", "T1"), order_id="oid1"), _result("FAILED", "T2")]
        _patch(se, "execute_bets", lambda bets, dry_run, **k: res, patches)
        _patch(se, "load_log", lambda: {"bets": [], "stats": {}}, patches)
        _patch(se, "save_log", lambda l: None, patches)
        se.run(dry_run=False)
        assert len(sent) == 1, sent
        assert sent[0].startswith("Kalshi sports: 2 order(s) unconfirmed — figures withheld until reconciled")
        assert "$" not in sent[0] and "4.00" not in sent[0]
        se.run(dry_run=False)
        assert len(sent) == 1, sent  # same order set -> not re-sent
        se.run(dry_run=True)
        assert len(sent) == 1
    finally:
        _restore(patches)


def test_kalshi_alert_fence_raises_fails_closed():
    class Cfg:
        def live_exec_fenced(self, strat, ticker=None):
            raise RuntimeError("fence config unreadable")
    ke = types.SimpleNamespace(_cfg=Cfg(), execute_trade=lambda o, s, dry_run=False: {
        "status": "DRY_RUN", "contracts": 10, "total_cost": 8.0, "potential_profit": 1.5})
    rest._fresh_dir()
    sent = []
    r = ka.brief(ke=ke, get_positions=lambda: {"market_positions": [POS1]},
                 send_fn=lambda m: sent.append(m) or True, now=NOW,
                 state={"balance": 20.0, "max_deployable": 15.0},
                 opps=[{"ticker": "FENCED", "strategy": "NO_HARVEST", "price": 0.8, "edge": 0.15}])
    assert r["status"] == "not_hot" and sent == [], (r, sent)


def test_decimal_sums():
    assert sv.dsum([0.1, 0.2]) == 0.3 and sv.money(sv.dsum([1.005, 2.675])) == "$3.68"
    found = None
    for a in range(1, 200):
        for b in (0.01, 0.07, 0.11):
            xs = [a / 1000 + 0.005, b, 0.1]
            if sv.money(sum(xs)) != sv.money(sv.dsum(xs)):
                found = xs
                break
        if found:
            break
    if found:  # float drift would have flipped a cent; Decimal matches the exact sum
        from decimal import Decimal, ROUND_HALF_UP
        exact = sum(Decimal(str(x)) for x in found).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert sv.money(sv.dsum(found)) == f"${exact}", found
    real = sv.ledger_realized([ent("a"), st("a", 0.1, True, t(1)), ent("b"), st("b", 0.2, True, t(1))])
    assert real["total"] == 0.3


def test_bridge_docs_and_server_local_only():
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    doc = open(os.path.join(root, "skills", "valedia", "claude-bridge.md")).read()
    assert "1400 chars" not in doc and "TODO(Burton)" in doc and "NOT covered" in doc
    assert "claude_router.sh" in doc and "--router" in doc
    srv = open(os.path.join(HERE, "claude_bridge_server.py")).read()
    assert "for Signal delivery" not in srv and "LOCAL ONLY — NOT FOR SIGNAL" in srv
    assert '"signal_safe_text"' in srv and "gate_answer" in srv and "signal_send" not in srv


if __name__ == "__main__":
    test_settle_corrupt_and_nonobject_then_fixed()
    test_settle_future_row_blocks_then_pending_rows_sent()
    test_settle_numeric_time_and_orphan_never_skip_valid_rows()
    test_settle_sent_set_no_double_report_after_cursor_reset()
    test_positions_dedupe()
    test_facts_labels_and_open_mismatch()
    test_sports_unconfirmed_notice()
    test_kalshi_alert_fence_raises_fails_closed()
    test_decimal_sums()
    test_bridge_docs_and_server_local_only()
    print("signal_v13: PASS")
