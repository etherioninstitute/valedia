import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import decay as dc
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop import paper as pp
from valedia_trader.research_loop import strategy as st

CAPITAL = 100_000.0
START_EQUITY = 1_000_000.0
PACKAGE_ROOT = str(Path(dc.__file__).resolve().parents[2])          # the tree under test, for child processes


# ---------------------------------------------------------------- helpers: a ledger, a journal, a paper book
def _idea(ledger, idea, result, status="passed backtest only", sealed=None):
    hid = ledger.propose(idea, "price-analyst", "us-etf")
    if result is not None:
        ledger.record_backtest(hid, {"result": result, "verdict": "pass"}, "trending up", "strategies/x.json", "1.0.0", "untested")
    ledger.record_sealed(hid, {"verdict": "pass", **({"result": sealed} if sealed is not None else {})}, status)
    return hid


def _last_summary(journal):
    found = [e for e in journal.read() if e.get("type") == "summary"]
    return found[-1] if found else None


def _book(journal, plan, start="2026-06-01", capital=CAPITAL, days=None):
    """One paper book, one summary a day, as the paper trader writes it. `plan` maps hypothesis id to (name, changes) or
    (name, changes, first day index): each strategy has a decision line and its value change on each of its days, and the
    day's equity is the last one plus the day's changes, so the book adds up. A new book opens with a summary the session
    before `start` (nothing held yet); a later call continues the equity of the last summary in the journal."""
    plan = {hid: (p[0], p[1], p[2] if len(p) > 2 else 0) for hid, p in plan.items()}
    length = max(off + len(ch) for _, ch, off in plan.values())
    days = list(days) if days is not None else list(pd.bdate_range(start, periods=length))
    last = _last_summary(journal)
    if last is None:
        journal.write(pd.Timestamp(days[0]) - pd.offsets.BDay(1),
                      {"type": "summary", "equity": START_EQUITY, "strategy_value_change": {}, "capital_per_strategy": capital})
        equity = START_EQUITY
    else:
        equity = float(last["equity"])
    for k, d in enumerate(days):
        change = {}
        for hid, (name, changes, off) in plan.items():
            if off <= k < off + len(changes):
                journal.write(d, {"type": "decision", "hypothesis_id": hid, "strategy": name, "version": "1.0.0",
                                  "decision": "no trade", "reasoning": "positions already match the strategy's targets"})
                change[name] = changes[k - off]
        equity += sum(change.values())
        journal.write(d, {"type": "summary", "equity": equity, "strategy_value_change": change, "capital_per_strategy": capital})
    return [pd.Timestamp(d) for d in days]


def _record(journal, hid, name, changes, start="2026-06-01", capital=CAPITAL):
    return _book(journal, {hid: (name, changes)}, start=start, capital=capital)


def _returns_record(journal, hid, name, returns, **kw):
    return _record(journal, hid, name, [r * CAPITAL for r in returns], **kw)


@pytest.fixture
def desk(tmp_path):
    ledger = lg.HypothesisLedger(tmp_path / "memory" / "ledger.db")
    journal = pp.Journal(tmp_path / "journal")
    yield ledger, journal, tmp_path
    ledger.close()


def _lp(root):
    return root / "memory" / "ledger.db"


def _row(report, hid):
    return next(r for r in report["strategies"] if r["hypothesis_id"] == hid)


def _decay_entries(journal):
    return [e for e in journal.read() if e.get("type") == "decay_check"]


def _decision(text, hid):
    """The decision word of hypothesis `hid` in the command's printed report."""
    line = next(ln for ln in text.splitlines() if ln.startswith(f"h{hid} "))
    return line.split("]: ", 1)[1].split(";", 1)[0]


# ---------------------------------------------------------------- my own calculation, written without numpy shortcuts
def my_sharpe(returns):
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    return mean / sd * math.sqrt(252.0) if sd > 1e-15 else 0.0


def my_worst_fall(returns):
    value, peak, worst = 1.0, 1.0, 0.0
    for r in returns:
        value *= 1.0 + r
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def my_returns(history, name):
    """A day's change over the capital per strategy of the day before; a strategy's first day over its own first capital."""
    out, prev = [], None
    for h in history:
        if name in h["strategy_value_change"]:
            capital = h["capital_per_strategy"] if not out else prev["capital_per_strategy"]
            out.append(h["strategy_value_change"][name] / capital)
        prev = h
    return out


# ---------------------------------------------------------------- the rules at their edges
def test_drawdown_rule_exactly_one_and_a_half_times_does_not_pause_and_just_beyond_does():
    at = dc.judge(live_sharpe=2.0, live_drawdown=0.375, days=5, backtest_sharpe=1.0, backtest_drawdown=0.25)
    assert at["decision"] == "keep" and at["rules"]["drawdown"]["fires"] is False
    beyond = dc.judge(live_sharpe=2.0, live_drawdown=0.375 + 1e-12, days=5, backtest_sharpe=1.0, backtest_drawdown=0.25)
    assert beyond["decision"] == "pause" and beyond["rules"]["drawdown"]["fires"] is True


def test_drawdown_rule_counts_from_the_first_paper_days():
    early = dc.judge(live_sharpe=-3.0, live_drawdown=0.2, days=2, backtest_sharpe=1.0, backtest_drawdown=0.1)
    assert early["decision"] == "pause" and early["rules"]["drawdown"]["fires"] is True
    assert early["rules"]["sharpe"]["fires"] is False and early["rules"]["sharpe"]["decides"] is False


def test_sharpe_rule_exactly_half_does_not_pause_and_just_below_does():
    at = dc.judge(live_sharpe=0.5, live_drawdown=0.0, days=60, backtest_sharpe=1.0, backtest_drawdown=0.1)
    assert at["decision"] == "keep" and at["rules"]["sharpe"]["fires"] is False
    below = dc.judge(live_sharpe=0.5 - 1e-12, live_drawdown=0.0, days=60, backtest_sharpe=1.0, backtest_drawdown=0.1)
    assert below["decision"] == "pause" and below["rules"]["sharpe"]["fires"] is True


def test_sharpe_rule_decides_nothing_at_59_days_and_decides_at_60():
    assert dc.MIN_DAYS_FOR_SHARPE == 60
    at59 = dc.judge(live_sharpe=0.0, live_drawdown=0.0, days=59, backtest_sharpe=1.0, backtest_drawdown=0.1)
    assert at59["decision"] == "keep" and at59["rules"]["sharpe"]["decides"] is False and at59["rules"]["sharpe"]["fires"] is False
    at60 = dc.judge(live_sharpe=0.0, live_drawdown=0.0, days=60, backtest_sharpe=1.0, backtest_drawdown=0.1)
    assert at60["decision"] == "pause" and at60["rules"]["sharpe"]["decides"] is True


def test_59_then_60_paper_days_end_to_end(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.2})
    flat = [0.001 if i % 2 == 0 else -0.001 for i in range(60)]          # a Sharpe near zero, a fall of a tenth of a percent
    days = _returns_record(journal, hid, "mom", flat[:59])
    r59 = dc.check(_lp(root), journal, apply=True)
    row = _row(r59, hid)
    assert row["days"] == 59 and row["decision"] == "keep" and row["live_sharpe"] < 0.5
    assert row["live_sharpe"] == pytest.approx(my_sharpe(flat[:59]), rel=1e-12)
    assert ledger.get(hid)["status"] == "passed backtest only"
    _returns_record(journal, hid, "mom", flat[59:], start=days[-1] + pd.offsets.BDay(1))
    r60 = dc.check(_lp(root), journal, apply=True)
    row = _row(r60, hid)
    assert row["days"] == 60 and row["decision"] == "paused" and ledger.get(hid)["status"] == "paused"
    reason = [f["finding"] for f in ledger.findings(hid) if f["source"] == "decay"]
    assert len(reason) == 1 and "status -> paused" in reason[0] and "60 paper days" in reason[0] and "half" in reason[0]


def test_exactly_one_and_a_half_times_the_backtest_drawdown_end_to_end(desk):
    ledger, journal, root = desk
    at = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.25})
    over = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.25})
    _book(journal, {at: ("at_edge", [0.0, -37_500.0]), over: ("over_edge", [0.0, -37_501.0])})   # 37.5% = 1.5 x 25%
    out = dc.check(_lp(root), journal, apply=True)
    assert _row(out, at)["live_max_drawdown"] == 0.375 and _row(out, at)["decision"] == "keep"
    assert _row(out, over)["decision"] == "paused"
    assert ledger.get(at)["status"] == "passed backtest only" and ledger.get(over)["status"] == "paused"


# ---------------------------------------------------------------- a strategy that has never held anything
@pytest.mark.parametrize("n", [59, 60, 200])
def test_a_flat_record_is_never_paused_by_the_sharpe_rule(desk, n):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    _record(journal, hid, "cash", [0.0] * n)
    out = dc.check(_lp(root), journal, apply=True)
    row = _row(out, hid)
    assert row["days"] == n and row["decision"] == "keep" and row["sharpe_decides"] is False
    assert "has not traded: Sharpe not judged" in row["reason"]
    assert out["exit_code"] == 0 and out["paused"] == [] and ledger.get(hid)["status"] == "passed backtest only"
    assert ledger.findings(hid) == []


def test_a_flat_record_through_the_command_exits_0(tmp_path, capsys):
    paths = _paths(tmp_path)
    ledger = lg.HypothesisLedger(paths.ledger)
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    _record(pp.Journal(paths.journal), hid, "cash", [0.0] * 60)
    ledger.close()
    assert dc.main(["check", "--apply"], paths=paths) == 0
    assert "has not traded: Sharpe not judged" in capsys.readouterr().out


def test_one_tiny_non_zero_return_among_60_days_is_judged(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    changes = [0.0] * 60
    changes[30] = -1e-9 * CAPITAL                                         # one return of -1e-9: it held something once
    _record(journal, hid, "almost", changes)
    out = dc.check(_lp(root), journal, apply=True)
    row = _row(out, hid)
    assert row["sharpe_decides"] is True and row["live_sharpe"] < 0.5 and row["decision"] == "paused"
    assert "has not traded" not in row["reason"] and out["exit_code"] == 1


def test_judge_with_a_flat_record_decides_nothing_on_sharpe_but_still_applies_drawdown():
    v = dc.judge(live_sharpe=0.0, live_drawdown=0.0, days=200, backtest_sharpe=1.0, backtest_drawdown=0.05, traded=False)
    assert v["decision"] == "keep" and v["rules"]["sharpe"]["decides"] is False and v["rules"]["drawdown"]["decides"] is True
    assert "has not traded: Sharpe not judged" in "; ".join(v["reasons"])


# ---------------------------------------------------------------- only ever more careful
def test_never_unpauses_promotes_or_touches_a_strategy_already_stopped(desk):
    ledger, journal, root = desk
    good = {"sharpe": 0.1, "max_drawdown": 0.5}
    paused = _idea(ledger, "Funds that rose keep rising", good)
    ledger.set_status(paused, "paused", "paused by hand", "owner")
    failed = _idea(ledger, "Funds that fell keep falling", {"sharpe": 5.0, "max_drawdown": 0.001}, status="failed")
    retired = _idea(ledger, "Bonds lead stocks by a week", {"sharpe": 5.0, "max_drawdown": 0.001}, status="retired")
    healthy = _idea(ledger, "Gold rises when the dollar falls", good, status="passed backtest only")
    rng = np.random.default_rng(3)
    _book(journal, {hid: (name, (CAPITAL * (0.002 + 0.001 * rng.standard_normal(70))).tolist())
                    for hid, name in ((paused, "p"), (failed, "f"), (retired, "r"), (healthy, "h"))})
    _book(journal, {failed: ("f", [-0.3 * CAPITAL])}, start="2026-12-01")      # a crash for the failed one: still left alone
    before = {h: len(ledger.findings(h)) for h in (paused, failed, retired, healthy)}
    out = dc.check(_lp(root), journal, apply=True)
    for hid, status in ((paused, "paused"), (failed, "failed"), (retired, "retired")):
        assert ledger.get(hid)["status"] == status and _row(out, hid)["decision"] == "left alone"
        assert _row(out, hid)["status"] == status
    assert _row(out, healthy)["decision"] == "keep" and ledger.get(healthy)["status"] == "passed backtest only"
    assert {h: len(ledger.findings(h)) for h in before} == before          # not a single status row or lesson
    assert out["paused"] == [] and out["exit_code"] == 0


def test_without_apply_it_only_reports(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    _returns_record(journal, hid, "mom", [0.0, -0.2, 0.01])
    lines = len(journal.read())
    out = dc.check(_lp(root), journal, apply=False)
    assert _row(out, hid)["decision"] == "pause" and out["to_pause"] == [hid] and out["exit_code"] == 1
    assert ledger.get(hid)["status"] == "passed backtest only" and ledger.findings(hid) == []
    assert len(journal.read()) == lines and not out["journal_entry_written"]


def test_a_second_apply_the_same_day_changes_nothing(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    keep = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.5})
    _book(journal, {hid: ("mom", [0.0, -0.2 * CAPITAL, 0.01 * CAPITAL]), keep: ("rev", [0.0, 0.01 * CAPITAL, 0.01 * CAPITAL])})
    first = dc.check(_lp(root), journal, apply=True)
    assert first["paused"] == [hid] and first["exit_code"] == 1 and first["journal_entry_written"]
    rows, lines = len(ledger.findings()), journal.read()
    entries = _decay_entries(journal)
    assert len(entries) == 1 and entries[0]["day"] == "2026-06-03"
    figures = {s["hypothesis_id"]: s for s in entries[0]["strategies"]}
    assert figures[hid]["decision"] == "paused" and figures[keep]["decision"] == "keep"
    assert figures[hid]["live_max_drawdown"] == pytest.approx(0.2) and figures[hid]["backtest_max_drawdown"] == 0.05
    second = dc.check(_lp(root), journal, apply=True)
    assert second["paused"] == [] and second["exit_code"] == 0 and not second["journal_entry_written"]
    assert _row(second, hid)["decision"] == "left alone"
    assert len(ledger.findings()) == rows and journal.read() == lines and len(_decay_entries(journal)) == 1


def test_the_journal_entry_is_written_once_even_when_nothing_is_paused(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.5})
    _returns_record(journal, hid, "mom", [0.0, 0.01, 0.01])
    assert dc.check(_lp(root), journal, apply=True)["journal_entry_written"]
    assert not dc.check(_lp(root), journal, apply=True)["journal_entry_written"]
    assert len(_decay_entries(journal)) == 1 and ledger.findings(hid) == []


def test_the_decay_entry_itself_is_not_mistaken_for_a_paper_day(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.5})
    days = _returns_record(journal, hid, "mom", [0.0, 0.01, 0.01])
    dc.check(_lp(root), journal, apply=True)
    _returns_record(journal, hid, "mom", [0.01], start=days[-1] + pd.offsets.BDay(1))
    out = dc.check(_lp(root), journal, apply=True)
    assert _row(out, hid)["days"] == 4 and len(_decay_entries(journal)) == 2


# ---------------------------------------------------------------- what cannot be judged
def test_a_damaged_journal_line_in_the_stretch_means_cannot_judge_named_by_file_and_line(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    days = _returns_record(journal, hid, "mom", [0.0, -0.3, 0.01, 0.01])        # deep enough to pause, if it could be judged
    path = journal.dir / f"{days[1].date()}.jsonl"
    with path.open("ab") as fh:
        fh.write(b'{"type": "summary", "strategy_value_change": {"mom": SECRET-TEXT')
    out = dc.check(_lp(root), journal, apply=True)
    row = _row(out, hid)
    assert row["decision"] == "cannot judge" and f"{days[1].date()}.jsonl line 3" in row["reason"]
    assert "SECRET-TEXT" not in json.dumps(out) and "SECRET-TEXT" not in json.dumps(_decay_entries(journal))
    assert ledger.get(hid)["status"] == "passed backtest only" and ledger.findings(hid) == [] and out["exit_code"] == 0


def test_damage_before_a_strategys_first_paper_day_does_not_stop_it_being_judged(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    (journal.dir / "2026-05-01.jsonl").write_bytes(b'{"torn')
    _returns_record(journal, hid, "mom", [0.0, -0.3, 0.01])
    out = dc.check(_lp(root), journal, apply=True)
    assert _row(out, hid)["decision"] == "paused"


def test_a_strategy_with_no_backtest_figures_cannot_be_judged(desk):
    ledger, journal, root = desk
    none = _idea(ledger, "Funds that rose keep rising", None)
    empty = _idea(ledger, "Funds that fell keep falling", {"sharpe": None, "max_drawdown": None})
    _book(journal, {none: ("a", [0.0, -0.5 * CAPITAL, 0.01 * CAPITAL]), empty: ("b", [0.0, -0.5 * CAPITAL, 0.01 * CAPITAL])})
    out = dc.check(_lp(root), journal, apply=True)
    for hid in (none, empty):
        row = _row(out, hid)
        assert row["decision"] == "cannot judge" and "backtest" in row["reason"]
        assert ledger.get(hid)["status"] == "passed backtest only" and ledger.findings(hid) == []
    assert out["exit_code"] == 0


def test_fewer_than_two_paper_days_cannot_be_judged(desk):
    ledger, journal, root = desk
    one = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.01})
    never = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.01})
    _returns_record(journal, one, "one", [-0.5])
    out = dc.check(_lp(root), journal, apply=True)
    assert _row(out, one)["decision"] == "cannot judge" and "1 paper day" in _row(out, one)["reason"]
    assert _row(out, never)["decision"] == "cannot judge" and "0 paper days" in _row(out, never)["reason"]
    assert ledger.get(one)["status"] == ledger.get(never)["status"] == "passed backtest only"


# ---------------------------------------------------------------- two strategies in one fund, through the real paper trader
def _shared_fund_prices():
    n = 40
    path = np.concatenate([np.full(30, 0.01), np.full(10, -0.025)])          # thirty days up, then a fall of about 22%
    up = 100 * np.exp(np.cumsum(path))
    return pd.DataFrame({"UP": up}, index=pd.bdate_range("2026-01-05", periods=n))


def _long(name, size, market=("UP",)):
    return st.RuleStrategy.from_spec({
        "name": name, "version": "1.0.0", "hypothesis": "A fund that rose over ten days keeps rising", "market": list(market),
        "entry": {"rule": "momentum", "lookback": 10, "enter": 0.03, "exit": -1.0, "direction": "long"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": size, "max_open_positions": 2, "stop_loss_pct": None}})


def _limits(root, max_position_pct=0.9):
    limits = root / "config" / "risk.json"
    limits.parent.mkdir(exist_ok=True)
    limits.write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": max_position_pct,
                                  "max_open_positions": 5}))
    return limits


def test_two_strategies_sharing_a_fund_are_each_judged_on_their_own_part(desk):
    ledger, journal, root = desk
    big = _idea(ledger, "Funds that rose keep rising, held large", {"sharpe": 1.0, "max_drawdown": 0.05})
    small = _idea(ledger, "Funds that rose keep rising, held small", {"sharpe": 1.0, "max_drawdown": 0.05})
    trader = pp.PaperTrader(journal.dir, _limits(root), starting_cash=CAPITAL, halt_file=None)
    both = {big: _long("big", 0.8), small: _long("small", 0.1)}
    px = _shared_fund_prices()
    for i in range(12, len(px) + 1):
        trader.step(px.iloc[:i], both)
    held = {name: book["UP"]["units"] for name, book in trader.state["positions"].items()}
    assert held["big"] > 0 and held["small"] > 0                          # both hold the same fund
    out = dc.check(_lp(root), journal, apply=True)
    for hid, name in ((big, "big"), (small, "small")):
        mine = my_returns(trader.state["history"], name)
        row = _row(out, hid)
        assert row["strategy"] == name and row["days"] == len(mine)
        assert row["live_max_drawdown"] == pytest.approx(my_worst_fall(mine), rel=1e-12)
        assert row["live_sharpe"] == pytest.approx(my_sharpe(mine), rel=1e-12)
    assert _row(out, big)["live_max_drawdown"] > 0.075 > _row(out, small)["live_max_drawdown"] > 0
    assert ledger.get(big)["status"] == "paused" and ledger.get(small)["status"] == "passed backtest only"


# ---------------------------------------------------------------- 200 generated paper histories against my own calculation
def test_two_hundred_generated_histories_match_my_own_sharpe_drawdown_and_decision(tmp_path):
    rng = np.random.default_rng(20261113)
    checked = 0
    for book in range(40):
        ledger = lg.HypothesisLedger(tmp_path / f"ledger{book}.db")
        journal = pp.Journal(tmp_path / f"book{book}")
        expected, plan = {}, {}
        lengths = rng.integers(2, 90, size=5)
        top = int(lengths.max())
        for k in range(5):
            bt = {"sharpe": float(rng.uniform(0.2, 2.5)), "max_drawdown": float(rng.uniform(0.02, 0.25))}
            hid = _idea(ledger, f"Idea number {book} {k} about fund {k}", bt)
            n = int(lengths[k])
            returns = (rng.uniform(-0.002, 0.003) + rng.uniform(0.002, 0.02) * rng.standard_normal(n)).tolist()
            expected[hid] = (bt, returns)
            plan[hid] = (f"s{k}", [r * CAPITAL for r in returns], top - n)          # later starters join the book later
        _book(journal, plan, start="2026-01-05")
        out = dc.check(tmp_path / f"ledger{book}.db", journal, apply=False)
        for hid, (bt, returns) in expected.items():
            row = _row(out, hid)
            s, dd, n = my_sharpe(returns), my_worst_fall(returns), len(returns)
            assert row["days"] == n
            assert row["live_sharpe"] == pytest.approx(s, rel=1e-9, abs=1e-12)
            assert row["live_max_drawdown"] == pytest.approx(dd, rel=1e-9, abs=1e-15)
            fires = dd > 1.5 * bt["max_drawdown"] or (n >= 60 and s < 0.5 * bt["sharpe"])
            assert row["decision"] == ("pause" if fires else "keep"), (book, hid, s, dd, n, bt)
            checked += 1
        ledger.close()
    assert checked == 200


def test_sharpe_matches_the_gauntlets_own_convention():
    from valedia_trader.backtest.engine import performance_report
    rng = np.random.default_rng(5)
    r = pd.Series(0.0005 + 0.01 * rng.standard_normal(80), index=pd.bdate_range("2026-01-05", periods=80))
    rep = performance_report(r)
    assert dc.annualised_sharpe(r.to_numpy()) == pytest.approx(rep["sharpe"], rel=1e-12)
    assert dc.worst_fall(r.to_numpy()) == pytest.approx(rep["max_drawdown"], rel=1e-12)


# ---------------------------------------------------------------- the command and its exit codes
def _paths(root):
    paths = LoopPaths(root)
    paths.journal.mkdir(parents=True, exist_ok=True)
    return paths


def test_the_command_exit_codes(tmp_path, capsys):
    paths = _paths(tmp_path)
    ledger = lg.HypothesisLedger(paths.ledger)
    journal = pp.Journal(paths.journal)
    calm = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.5})
    days = _book(journal, {calm: ("calm", [0.0, 1000.0, 1000.0])})
    ledger.close()
    assert dc.main(["check"], paths=paths) == 0
    ledger = lg.HypothesisLedger(paths.ledger)
    deep = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.01})
    _book(journal, {calm: ("calm", [1000.0] * 3), deep: ("deep", [0.0, -0.1 * CAPITAL, 0.01 * CAPITAL])},
          start=days[-1] + pd.offsets.BDay(1))
    ledger.close()
    assert dc.main(["check"], paths=paths) == 1                             # one to pause, nothing changed
    ledger = lg.HypothesisLedger(paths.ledger)
    assert ledger.get(deep)["status"] == "passed backtest only"
    ledger.close()
    assert dc.main(["check", "--apply"], paths=paths) == 1
    ledger = lg.HypothesisLedger(paths.ledger)
    assert ledger.get(deep)["status"] == "paused"
    ledger.close()
    assert dc.main(["check", "--apply"], paths=paths) == 0
    text = capsys.readouterr().out
    assert "deep" in text and "calm" in text


def test_the_command_cannot_run_without_a_ledger_and_creates_nothing(tmp_path, capsys):
    paths = _paths(tmp_path)
    assert dc.main(["check"], paths=paths) == 2
    assert not paths.ledger.exists() and not paths.memory.exists()
    assert "could not run" in capsys.readouterr().err


def test_the_command_cannot_run_without_a_journal_folder(tmp_path):
    paths = LoopPaths(tmp_path)
    lg.HypothesisLedger(paths.ledger).close()
    assert dc.main(["check"], paths=paths) == 2 and not paths.journal.exists()


# ================================================================ rev 2: what the blind check found
def _setup_book(root, ideas, plan, start="2026-06-01"):
    """A ledger and a journal under `root` for the command: `ideas` is [(idea text, result, sealed)], `plan` as in _book
    keyed by the position in `ideas`. Returns the paths and the hypothesis ids."""
    paths = _paths(root)
    ledger = lg.HypothesisLedger(paths.ledger)
    hids = [_idea(ledger, text, result, sealed=sealed) for text, result, sealed in ideas]
    ledger.close()
    _book(pp.Journal(paths.journal), {hids[k]: p for k, p in plan.items()}, start=start)
    return paths, hids


def _status(paths, hid):
    """The idea's status and its decay rows, read straight from the file (an idea with a broken stored result cannot be
    read through `HypothesisLedger.get`)."""
    db = sqlite3.connect(paths.ledger)
    try:
        status = db.execute("SELECT status FROM hypotheses WHERE id = ?", (hid,)).fetchone()[0]
        rows = [r[0] for r in db.execute("SELECT finding FROM findings WHERE hypothesis_id = ? AND source = 'decay'", (hid,))]
        return status, rows
    finally:
        db.close()


# ---------------------------------------------------------------- 1. one run at a time
_CHILD_RUN = """
import os, sys, time
from pathlib import Path
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import decay
root, go = Path(sys.argv[1]), sys.argv[2]
limit = time.monotonic() + 60
while not os.path.exists(go) and time.monotonic() < limit:
    time.sleep(0.0005)
raise SystemExit(decay.main(["check", "--apply"], paths=LoopPaths(root)))
"""

_CHILD_HOLD = """
import sys
from pathlib import Path
from valedia_trader.research_loop import decay
with decay.run_lock(Path(sys.argv[1])):
    print("held", flush=True)
    sys.stdin.readline()
"""


def _child_env():
    return {**os.environ, "PYTHONPATH": PACKAGE_ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")}


def test_two_runs_started_together_write_one_entry_and_at_most_one_pause(tmp_path):
    duplicates, codes = [], []
    for trial in range(30):
        root = tmp_path / f"t{trial}"
        paths, (hid,) = _setup_book(root, [("Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05}, None)],
                                    {0: ("mom", [0.0, -0.2 * CAPITAL, 0.01 * CAPITAL])})
        go = root / "go"
        kids = [subprocess.Popen([sys.executable, "-c", _CHILD_RUN, str(root), str(go)], env=_child_env(), cwd=root,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        go.write_text("")
        results = [(k.wait(timeout=120), k.stderr.read()) for k in kids]
        for k in kids:
            k.stderr.close()
        codes.append(tuple(sorted(c for c, _ in results)))
        entries = [e for e in pp.Journal(paths.journal).read() if e.get("type") == "decay_check"]
        status, rows = _status(paths, hid)
        if len(entries) != 1 or len(rows) != 1 or status != "paused":
            duplicates.append((trial, len(entries), len(rows), status))
        for code, err in results:
            if code == 2:
                assert "another decay check is running" in err
    assert duplicates == []
    assert set(codes) <= {(1, 2), (0, 1)}, codes                            # busy and refused, or second after the first


def test_a_held_lock_refuses_a_second_run_and_a_killed_holder_does_not_block_the_next(tmp_path, capsys):
    paths, (hid,) = _setup_book(tmp_path, [("Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05}, None)],
                                {0: ("mom", [0.0, -0.2 * CAPITAL, 0.01 * CAPITAL])})
    holder = subprocess.Popen([sys.executable, "-c", _CHILD_HOLD, str(paths.journal)], env=_child_env(), cwd=tmp_path,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        lines = pp.Journal(paths.journal).read()
        assert dc.main(["check", "--apply"], paths=paths) == 2
        assert "another decay check is running" in capsys.readouterr().err
        assert pp.Journal(paths.journal).read() == lines and _status(paths, hid) == ("passed backtest only", [])
    finally:
        holder.kill()                                                      # our own child, by its process id
        holder.wait(timeout=30)
        holder.stdin.close()
        holder.stdout.close()
    assert dc.main(["check", "--apply"], paths=paths) == 1                  # the killed holder's lock went with it
    assert _status(paths, hid)[0] == "paused"


# ---------------------------------------------------------------- 2. a day the book cannot be valued is never judged
def _rising_prices(n=40, gap=None):
    up = 100 * np.exp(np.cumsum(np.full(n, 0.01)))                         # the fund rises 1% every day
    px = pd.DataFrame({"UP": up, "FLAT": np.full(n, 50.0)}, index=pd.bdate_range("2026-01-05", periods=n))
    if gap is not None:
        px.iloc[gap, 0] = np.nan                                           # one missing close, one fund, one day
    return px


def _run_trader(root, px, plan):
    trader = pp.PaperTrader(root / "journal", _limits(root), starting_cash=CAPITAL, halt_file=None)
    for i in range(12, len(px) + 1):
        trader.step(px.iloc[: i], plan)
    return trader


def _append_day(journal, day, hid_names, changes, equity_change=None, **extra):
    """One more paper day by hand: a decision line per strategy and the summary; the equity moves by `equity_change`
    (by default the sum of the changes, so the book adds up)."""
    eq = float(_last_summary(journal)["equity"])
    for hid, name in hid_names.items():
        journal.write(day, {"type": "decision", "hypothesis_id": hid, "strategy": name, "decision": "no trade"})
    move = sum(changes.values()) if equity_change is None else equity_change
    journal.write(day, {"type": "summary", "equity": eq + move, "strategy_value_change": changes, "capital_per_strategy": CAPITAL,
                        **extra})


def test_a_missing_close_in_a_real_paper_history_is_never_judged(tmp_path, capsys):
    """The history as the paper book wrote it before the repair (6ae5ca3): on the day UP had no close the held position
    was left out of the equity (-110,000) while the strategy lost only its open gain (-25,000); the next day both came back.
    That day does not add up, so the strategy, which rose 1% every other day, is never judged on it."""
    paths = _paths(tmp_path)
    ledger = lg.HypothesisLedger(paths.ledger)
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    ledger.close()
    journal = pp.Journal(paths.journal)
    days = _book(journal, {hid: ("riser", [0.01 * CAPITAL] * 20)})
    journal.write(days[0], {"type": "fill", "strategy": "riser", "symbol": "UP", "units": 800.0, "price": 100.0, "fee": 0.0})
    gap, after = days[-1] + pd.offsets.BDay(1), days[-1] + pd.offsets.BDay(2)
    _append_day(journal, gap, {hid: "riser"}, {"riser": -25_000.0}, equity_change=-110_000.0, stale_prices=["UP"])
    _append_day(journal, after, {hid: "riser"}, {"riser": 26_000.0}, equity_change=111_000.0)
    assert dc.main(["check", "--apply"], paths=paths) == 0
    out = capsys.readouterr().out
    assert _decision(out, hid) == "CANNOT JUDGE" and f"the book could not be valued on {gap.date()}" in out
    assert _status(paths, hid) == ("passed backtest only", [])


def test_a_real_history_with_a_missing_close_is_judged_like_the_same_history_gap_filled(tmp_path, capsys):
    results = {}
    for label in ("gap", "filled"):
        root = tmp_path / label
        paths = _paths(root)
        ledger = lg.HypothesisLedger(paths.ledger)
        hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
        ledger.close()
        px = _rising_prices(gap=35)
        if label == "filled":
            px = px.ffill()                                                # the gap filled by the last known price
        _run_trader(root, px, {hid: _long("riser", 0.8, market=("UP",))})
        stale = [e for e in pp.Journal(paths.journal).read() if e.get("type") == "summary" and e.get("stale_prices")]
        code = dc.main(["check", "--apply"], paths=paths)
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if ln.startswith(f"h{hid} "))
        entry = [e for e in pp.Journal(paths.journal).read() if e.get("type") == "decay_check"][0]["strategies"][0]
        results[label] = (code, _decision(out, hid), entry, line, len(stale))
    (gc, gd, ge, gl, gs), (fc, fd, fe, fl, fs) = results["gap"], results["filled"]
    assert (gs, fs) == (1, 0)                                              # the repaired book named the carried price
    assert (gc, gd) == (fc, fd) == (0, "KEEP") and ge["days"] == fe["days"]
    assert ge["live_max_drawdown"] == pytest.approx(fe["live_max_drawdown"], abs=1e-12)
    # not to the last digit: on the gap day the repaired book trades nothing in UP, the gap-filled history does
    assert ge["live_sharpe"] == pytest.approx(fe["live_sharpe"], rel=1e-2)
    assert "days with a carried price: 1" in gl and "days with a carried price: 0" in fl
    assert "could not be valued" not in gl


def test_a_carried_price_that_adds_up_is_judged_and_counted_in_list_and_mapping_form(tmp_path, capsys):
    paths = _paths(tmp_path)
    ledger = lg.HypothesisLedger(paths.ledger)
    mom = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.01})
    other = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.5})
    ledger.close()
    journal = pp.Journal(paths.journal)
    days = _book(journal, {mom: ("mom", [0.0, 0.0]), other: ("other", [0.0, 0.0])})
    journal.write(days[0], {"type": "fill", "strategy": "mom", "symbol": "UP", "units": 10.0, "price": 100.0, "fee": 0.0})
    journal.write(days[0], {"type": "fill", "strategy": "other", "symbol": "DOWN", "units": 10.0, "price": 100.0, "fee": 0.0})
    both = {mom: "mom", other: "other"}
    later = [days[-1] + pd.offsets.BDay(k) for k in (1, 2, 3)]
    _append_day(journal, later[0], both, {"mom": -0.1 * CAPITAL, "other": 100.0}, stale_prices=["UP"])
    _append_day(journal, later[1], both, {"mom": 100.0, "other": 100.0}, stale_prices={"DOWN": 1, "UP": 2})
    _append_day(journal, later[2], both, {"mom": 100.0, "other": 100.0}, stale_prices=["DOWN"])
    assert dc.main(["check", "--apply"], paths=paths) == 1
    out = capsys.readouterr().out
    assert _decision(out, mom) == "PAUSED" and _decision(out, other) == "KEEP"          # judged like any other day
    lines = {hid: next(ln for ln in out.splitlines() if ln.startswith(f"h{hid} ")) for hid in (mom, other)}
    assert "days with a carried price: 2" in lines[mom] and "days with a carried price: 2" in lines[other]
    assert "could not be valued" not in out and _status(paths, mom)[0] == "paused"


# ---------------------------------------------------------------- 3. a day's return against the capital it started with
def test_a_days_return_is_measured_against_the_capital_of_the_day_before_when_strategies_join(tmp_path):
    paths = _paths(tmp_path)
    ledger = lg.HypothesisLedger(paths.ledger)
    first = _idea(ledger, "Funds that rose keep rising", {"sharpe": 0.5, "max_drawdown": 0.5})
    second = _idea(ledger, "Funds that fell keep falling", {"sharpe": 0.5, "max_drawdown": 0.5})
    third = _idea(ledger, "Bonds lead stocks by a week", {"sharpe": 0.5, "max_drawdown": 0.5})
    ledger.close()
    n = 40
    path = np.concatenate([np.full(20, 0.01), np.full(20, -0.02)])         # up, then down while the others join
    px = pd.DataFrame({"UP": 100 * np.exp(np.cumsum(path))}, index=pd.bdate_range("2026-01-05", periods=n))
    trader = pp.PaperTrader(paths.journal, _limits(tmp_path), starting_cash=CAPITAL, halt_file=None)
    strategies = {first: _long("first", 0.8), second: _long("second", 0.3), third: _long("third", 0.3)}
    for i in range(12, n + 1):
        joined = 1 + (i >= 24) + (i >= 30)                                 # a second and a third strategy join the book
        trader.step(px.iloc[:i], dict(list(strategies.items())[:joined]))
    assert dc.main(["check", "--apply"], paths=paths) in (0, 1)
    entry = [e for e in pp.Journal(paths.journal).read() if e.get("type") == "decay_check"][0]
    history = trader.state["history"]
    for hid, name in ((first, "first"), (second, "second"), (third, "third")):
        row = next(s for s in entry["strategies"] if s["hypothesis_id"] == hid)
        mine = my_returns(history, name)
        assert row["days"] == len(mine)
        assert row["live_sharpe"] == pytest.approx(my_sharpe(mine), rel=1e-12)
        assert row["live_max_drawdown"] == pytest.approx(my_worst_fall(mine), rel=1e-12)
    same_day = [h["strategy_value_change"]["first"] / h["capital_per_strategy"] for h in history]
    before = my_returns(history, "first")                                  # the two conventions really differ here
    assert abs(my_worst_fall(same_day) - my_worst_fall(before)) > 1e-3 and abs(my_sharpe(same_day) - my_sharpe(before)) > 1e-3


# ---------------------------------------------------------------- 4. what the backtest is
def _one(tmp_path, result, sealed, returns, capsys, apply=True):
    paths, (hid,) = _setup_book(tmp_path, [("Funds that rose keep rising", result, sealed)],
                                {0: ("mom", [r * CAPITAL for r in returns])})
    code = dc.main(["check"] + (["--apply"] if apply else []), paths=paths)
    out = capsys.readouterr().out
    return code, _decision(out, hid), out, paths, hid


def test_the_worst_fall_compared_against_is_the_larger_of_development_and_sealed(tmp_path, capsys):
    fall = [0.0, -0.12, 0.001]                                            # a 12% fall: over 1.5 x 5%, under 1.5 x 10%
    code, decision, _, paths, hid = _one(tmp_path / "a", {"sharpe": 1.0, "max_drawdown": 0.05},
                                         {"sharpe": 1.0, "max_drawdown": 0.10}, fall, capsys)
    assert (code, decision) == (0, "KEEP") and _status(paths, hid)[0] == "passed backtest only"
    code, decision, _, _, _ = _one(tmp_path / "b", {"sharpe": 1.0, "max_drawdown": 0.10},
                                   {"sharpe": 1.0, "max_drawdown": 0.05}, fall, capsys)
    assert (code, decision) == (0, "KEEP")
    code, decision, _, _, _ = _one(tmp_path / "c", None, {"sharpe": 1.0, "max_drawdown": 0.05}, fall, capsys)
    assert (code, decision) == (1, "PAUSED")                                # only the sealed result: that one is used


def test_the_sharpe_compared_against_is_the_lower_of_development_and_sealed(tmp_path, capsys):
    rng = np.random.default_rng(11)
    returns = (0.002 + 0.01 * rng.standard_normal(70)).tolist()
    s = my_sharpe(returns)
    assert 0.5 < s
    code, decision, _, _, _ = _one(tmp_path / "a", {"sharpe": 4 * s, "max_drawdown": 0.9},
                                   {"sharpe": 1.5 * s, "max_drawdown": 0.9}, returns, capsys)
    assert (code, decision) == (0, "KEEP")                                 # under half of the development, not of the sealed
    code, decision, _, _, _ = _one(tmp_path / "b", {"sharpe": 4 * s, "max_drawdown": 0.9}, None, returns, capsys)
    assert (code, decision) == (1, "PAUSED")


@pytest.mark.parametrize("bad", ["0.1", True, -0.1, float("nan"), [0.1]])
def test_a_backtest_worst_fall_that_is_not_a_real_number_cannot_be_judged(tmp_path, capsys, bad):
    code, decision, _, paths, hid = _one(tmp_path, {"sharpe": 1.0, "max_drawdown": bad}, None, [0.0, -0.5, 0.001], capsys)
    assert (code, decision) == (0, "CANNOT JUDGE") and _status(paths, hid) == ("passed backtest only", [])


@pytest.mark.parametrize("bad", ["2.0", True, float("inf")])
def test_a_backtest_sharpe_that_is_not_a_real_number_cannot_be_judged(tmp_path, capsys, bad):
    rng = np.random.default_rng(12)
    returns = (-0.002 + 0.01 * rng.standard_normal(70)).tolist()
    code, decision, _, paths, hid = _one(tmp_path, {"sharpe": bad, "max_drawdown": 0.9}, None, returns, capsys)
    assert (code, decision) == (0, "CANNOT JUDGE") and _status(paths, hid) == ("passed backtest only", [])


def test_a_backtest_sharpe_not_above_zero_never_pauses_on_sharpe(tmp_path, capsys):
    rng = np.random.default_rng(13)
    returns = (-0.003 + 0.01 * rng.standard_normal(70)).tolist()
    assert my_sharpe(returns) < -1.0
    for k, bt in enumerate((-0.5, 0.0)):
        code, decision, out, paths, hid = _one(tmp_path / str(k), {"sharpe": bt, "max_drawdown": 0.9}, None, returns, capsys)
        assert code == 0 and decision != "PAUSED" and _status(paths, hid)[0] == "passed backtest only"
        assert "backtest Sharpe not above zero: Sharpe not judged" in out


def test_paper_returns_with_no_spread_never_pause_on_sharpe(tmp_path, capsys):
    code, decision, out, paths, hid = _one(tmp_path, {"sharpe": 1.0, "max_drawdown": 0.9}, None, [0.001] * 70, capsys)
    assert code == 0 and decision != "PAUSED" and _status(paths, hid)[0] == "passed backtest only"
    assert "no spread: Sharpe not judged" in out


# ---------------------------------------------------------------- 5. one bad idea does not stop the check
def test_one_malformed_idea_or_decision_line_does_not_stop_the_check(tmp_path, capsys):
    good = {"sharpe": 1.0, "max_drawdown": 0.05}
    paths, (broken, listed, fine) = _setup_book(
        tmp_path, [("Funds that rose keep rising", good, None), ("Funds that fell keep falling", good, None),
                   ("Bonds lead stocks by a week", good, None)],
        {k: (name, [0.0, -0.2 * CAPITAL, 0.001 * CAPITAL]) for k, name in enumerate(("broken", "listed", "fine"))})
    db = sqlite3.connect(paths.ledger)
    db.execute("UPDATE hypotheses SET backtest_result = ? WHERE id = ?", ('{"result": {"sharpe": LEDGER-TEXT', broken))
    db.execute("UPDATE hypotheses SET sealed_test_result = ? WHERE id = ?", ('["LEDGER-TEXT"]', listed))
    db.commit()
    db.close()
    journal = pp.Journal(paths.journal)
    last = pd.Timestamp(_last_summary(journal)["day"])
    for bad in ("JOURNAL-TEXT", 1.5, True, None):
        journal.write(last, {"type": "decision", "hypothesis_id": bad, "strategy": "JOURNAL-NAME", "decision": "no trade"})
    code = dc.main(["check", "--apply"], paths=paths)
    captured = capsys.readouterr()
    assert code == 1 and _status(paths, fine)[0] == "paused"
    assert _decision(captured.out, broken) == "CANNOT JUDGE" and _decision(captured.out, listed) == "CANNOT JUDGE"
    assert _status(paths, broken)[0] == _status(paths, listed)[0] == "passed backtest only"
    assert f"decision lines skipped (idea number not a whole number): 4 ({last.date()}.jsonl)" in captured.out
    for text in ("LEDGER-TEXT", "JOURNAL-TEXT", "JOURNAL-NAME"):
        assert text not in captured.out and text not in captured.err


# ---------------------------------------------------------------- 6. reading is reading
def test_report_only_never_writes_the_ledger_and_a_bad_ledger_stops_the_check(tmp_path, capsys):
    paths = _paths(tmp_path)
    paths.memory.mkdir()
    paths.ledger.write_bytes(b"")
    assert dc.main(["check"], paths=paths) == 2 and paths.ledger.stat().st_size == 0
    paths.ledger.write_bytes(b"this is not a database at all, LEDGER-TEXT" * 50)
    before = paths.ledger.read_bytes()
    assert dc.main(["check"], paths=paths) == 2 and paths.ledger.read_bytes() == before
    assert "LEDGER-TEXT" not in capsys.readouterr().err
    paths.ledger.unlink()
    ledger = lg.HypothesisLedger(paths.ledger)
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    ledger.close()
    _book(pp.Journal(paths.journal), {hid: ("mom", [0.0, -0.2 * CAPITAL, 0.001 * CAPITAL])})
    before, listing = paths.ledger.read_bytes(), sorted(p.name for p in paths.memory.iterdir())
    os.chmod(paths.ledger, 0o444)
    try:
        assert dc.main(["check"], paths=paths) == 1
    finally:
        os.chmod(paths.ledger, 0o644)
    assert paths.ledger.read_bytes() == before and sorted(p.name for p in paths.memory.iterdir()) == listing


def test_report_only_opens_every_ledger_connection_read_only(tmp_path, monkeypatch):
    paths, (hid,) = _setup_book(tmp_path, [("Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05}, None)],
                                {0: ("mom", [0.0, -0.2 * CAPITAL, 0.001 * CAPITAL])})
    opened, real = [], sqlite3.connect

    def spy(target, *args, **kw):
        opened.append((str(target), bool(kw.get("uri"))))
        return real(target, *args, **kw)
    monkeypatch.setattr(sqlite3, "connect", spy)
    assert dc.main(["check"], paths=paths) == 1
    assert opened and all(uri and target.endswith("?mode=ro") for target, uri in opened), opened


def test_report_only_reads_a_ledger_another_writer_is_holding(tmp_path):
    paths, (hid,) = _setup_book(tmp_path, [("Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05}, None)],
                                {0: ("mom", [0.0, -0.2 * CAPITAL, 0.001 * CAPITAL])})
    writer = sqlite3.connect(paths.ledger, timeout=0.1)
    writer.execute("BEGIN IMMEDIATE")                                      # a writer holds the ledger; readers may read
    try:
        started = time.monotonic()
        assert dc.main(["check"], paths=paths) == 1
        assert time.monotonic() - started < 4.0
    finally:
        writer.rollback()
        writer.close()


# ---------------------------------------------------------------- 7. nothing silent
def test_a_torn_line_in_the_as_of_file_stops_the_check_naming_file_and_line(tmp_path, capsys):
    paths, (hid,) = _setup_book(tmp_path, [("Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05}, None)],
                                {0: ("mom", [0.0, -0.2 * CAPITAL, 0.001 * CAPITAL])})
    journal = pp.Journal(paths.journal)
    as_of = _last_summary(journal)["day"]
    with (paths.journal / f"{as_of}.jsonl").open("ab") as fh:
        fh.write(b'{"type": "decay_check", "as_of": "JOURNAL-TEXT')
    lines = (paths.journal / f"{as_of}.jsonl").read_bytes()
    for argv in (["check"], ["check", "--apply"]):
        assert dc.main(argv, paths=paths) == 2
        err = capsys.readouterr().err
        assert f"{as_of}.jsonl line 3" in err and "JOURNAL-TEXT" not in err
    assert (paths.journal / f"{as_of}.jsonl").read_bytes() == lines and _status(paths, hid) == ("passed backtest only", [])


def test_trading_days_with_no_journal_file_are_counted_and_printed(tmp_path, capsys):
    changes = [0.0, 1000.0, 0.0, 0.0, 1000.0, 1000.0]
    paths, (hid,) = _setup_book(tmp_path, [("Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.5}, None)],
                                {0: ("mom", changes)}, start="2026-06-01")
    (paths.journal / "2026-06-03.jsonl").unlink()                           # a session with nothing changing, its file gone
    (paths.journal / "2026-06-04.jsonl").unlink()
    assert dc.main(["check"], paths=paths) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith(f"h{hid} "))
    assert "days missing: 2" in line and _decision(out, hid) == "KEEP"


# ---------------------------------------------------------------- 8. a pause whose reason was never written
def test_a_strategy_paused_with_no_reason_row_is_reported_as_such(tmp_path, capsys):
    paths, (silent, said) = _setup_book(
        tmp_path, [("Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.5}, None),
                   ("Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.5}, None)],
        {0: ("silent", [0.0, 1000.0, 1000.0]), 1: ("said", [0.0, 1000.0, 1000.0])})
    ledger = lg.HypothesisLedger(paths.ledger)
    ledger.record_paper(silent, {}, status="paused")                        # a status with no reason row, as a kill can leave
    ledger.set_status(said, "paused", "paused by the critic", "critic")
    ledger.close()
    assert dc.main(["check", "--apply"], paths=paths) == 0
    out = capsys.readouterr().out
    assert _decision(out, silent) == "PAUSED, REASON NOT RECORDED" and _decision(out, said) == "LEFT ALONE"
