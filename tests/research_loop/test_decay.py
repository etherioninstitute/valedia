import json
import math

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import decay as dc
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop import paper as pp
from valedia_trader.research_loop import strategy as st

CAPITAL = 100_000.0


# ---------------------------------------------------------------- helpers: a ledger, a journal, a paper record
def _idea(ledger, idea, result, status="passed backtest only"):
    hid = ledger.propose(idea, "price-analyst", "us-etf")
    if result is not None:
        ledger.record_backtest(hid, {"result": result, "verdict": "pass"}, "trending up", "strategies/x.json", "1.0.0", "untested")
    ledger.record_sealed(hid, {"verdict": "pass"}, status)
    return hid


def _record(journal, hid, name, changes, start="2026-06-01", capital=CAPITAL):
    """One paper day per change: the decision line the trader writes for the strategy, then the day's summary."""
    days = pd.bdate_range(start, periods=len(changes))
    for d, c in zip(days, changes):
        journal.write(d, {"type": "decision", "hypothesis_id": hid, "strategy": name, "version": "1.0.0", "decision": "no trade",
                          "reasoning": "positions already match the strategy's targets"})
        journal.write(d, {"type": "summary", "strategy_value_change": {name: c}, "capital_per_strategy": capital})
    return days


def _returns_record(journal, hid, name, returns, **kw):
    return _record(journal, hid, name, [r * CAPITAL for r in returns], **kw)


@pytest.fixture
def desk(tmp_path):
    ledger = lg.HypothesisLedger(tmp_path / "memory" / "ledger.db")
    journal = pp.Journal(tmp_path / "journal")
    yield ledger, journal, tmp_path
    ledger.close()


def _row(report, hid):
    return next(r for r in report["strategies"] if r["hypothesis_id"] == hid)


def _decay_entries(journal):
    return [e for e in journal.read() if e.get("type") == "decay_check"]


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
    r59 = dc.check(ledger, journal, apply=True)
    row = _row(r59, hid)
    assert row["days"] == 59 and row["decision"] == "keep" and row["live_sharpe"] < 0.5
    assert row["live_sharpe"] == pytest.approx(my_sharpe(flat[:59]), rel=1e-12)
    assert ledger.get(hid)["status"] == "passed backtest only"
    _returns_record(journal, hid, "mom", flat[59:], start=days[-1] + pd.offsets.BDay(1))
    r60 = dc.check(ledger, journal, apply=True)
    row = _row(r60, hid)
    assert row["days"] == 60 and row["decision"] == "paused" and ledger.get(hid)["status"] == "paused"
    reason = [f["finding"] for f in ledger.findings(hid) if f["source"] == "decay"]
    assert len(reason) == 1 and "status -> paused" in reason[0] and "60 paper days" in reason[0] and "half" in reason[0]


def test_exactly_one_and_a_half_times_the_backtest_drawdown_end_to_end(desk):
    ledger, journal, root = desk
    at = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.25})
    over = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.25})
    _record(journal, at, "at_edge", [0.0, -37_500.0])                    # a fall of exactly 37.5% = 1.5 x 25%
    _record(journal, over, "over_edge", [0.0, -37_501.0])
    out = dc.check(ledger, journal, apply=True)
    assert _row(out, at)["live_max_drawdown"] == 0.375 and _row(out, at)["decision"] == "keep"
    assert _row(out, over)["decision"] == "paused"
    assert ledger.get(at)["status"] == "passed backtest only" and ledger.get(over)["status"] == "paused"


# ---------------------------------------------------------------- a strategy that has never held anything
@pytest.mark.parametrize("n", [59, 60, 200])
def test_a_flat_record_is_never_paused_by_the_sharpe_rule(desk, n):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    _record(journal, hid, "cash", [0.0] * n)
    out = dc.check(ledger, journal, apply=True)
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
    out = dc.check(ledger, journal, apply=True)
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
    paused = _idea(ledger, "Funds that rose keep rising", good, status="paused")
    failed = _idea(ledger, "Funds that fell keep falling", {"sharpe": 5.0, "max_drawdown": 0.001}, status="failed")
    retired = _idea(ledger, "Bonds lead stocks by a week", {"sharpe": 5.0, "max_drawdown": 0.001}, status="retired")
    healthy = _idea(ledger, "Gold rises when the dollar falls", good, status="passed backtest only")
    rng = np.random.default_rng(3)
    for hid, name in ((paused, "p"), (failed, "f"), (retired, "r"), (healthy, "h")):
        _returns_record(journal, hid, name, (0.002 + 0.001 * rng.standard_normal(70)).tolist())
    _returns_record(journal, failed, "f", [-0.3], start="2026-12-01")     # a crash for the failed one: still left alone
    before = {h: len(ledger.findings(h)) for h in (paused, failed, retired, healthy)}
    out = dc.check(ledger, journal, apply=True)
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
    out = dc.check(ledger, journal, apply=False)
    assert _row(out, hid)["decision"] == "pause" and out["to_pause"] == [hid] and out["exit_code"] == 1
    assert ledger.get(hid)["status"] == "passed backtest only" and ledger.findings(hid) == []
    assert len(journal.read()) == lines and not out["journal_entry_written"]


def test_a_second_apply_the_same_day_changes_nothing(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    keep = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.5})
    _returns_record(journal, hid, "mom", [0.0, -0.2, 0.01])
    _returns_record(journal, keep, "rev", [0.0, 0.01, 0.01])
    first = dc.check(ledger, journal, apply=True)
    assert first["paused"] == [hid] and first["exit_code"] == 1 and first["journal_entry_written"]
    rows, lines = len(ledger.findings()), journal.read()
    entries = _decay_entries(journal)
    assert len(entries) == 1 and entries[0]["day"] == "2026-06-03"
    figures = {s["hypothesis_id"]: s for s in entries[0]["strategies"]}
    assert figures[hid]["decision"] == "paused" and figures[keep]["decision"] == "keep"
    assert figures[hid]["live_max_drawdown"] == pytest.approx(0.2) and figures[hid]["backtest_max_drawdown"] == 0.05
    second = dc.check(ledger, journal, apply=True)
    assert second["paused"] == [] and second["exit_code"] == 0 and not second["journal_entry_written"]
    assert _row(second, hid)["decision"] == "left alone"
    assert len(ledger.findings()) == rows and journal.read() == lines and len(_decay_entries(journal)) == 1


def test_the_journal_entry_is_written_once_even_when_nothing_is_paused(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.5})
    _returns_record(journal, hid, "mom", [0.0, 0.01, 0.01])
    assert dc.check(ledger, journal, apply=True)["journal_entry_written"]
    assert not dc.check(ledger, journal, apply=True)["journal_entry_written"]
    assert len(_decay_entries(journal)) == 1 and ledger.findings(hid) == []


def test_the_decay_entry_itself_is_not_mistaken_for_a_paper_day(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.5})
    days = _returns_record(journal, hid, "mom", [0.0, 0.01, 0.01])
    dc.check(ledger, journal, apply=True)
    _returns_record(journal, hid, "mom", [0.01], start=days[-1] + pd.offsets.BDay(1))
    out = dc.check(ledger, journal, apply=True)
    assert _row(out, hid)["days"] == 4 and len(_decay_entries(journal)) == 2


# ---------------------------------------------------------------- what cannot be judged
def test_a_damaged_journal_line_in_the_stretch_means_cannot_judge_named_by_file_and_line(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    days = _returns_record(journal, hid, "mom", [0.0, -0.3, 0.01, 0.01])        # deep enough to pause, if it could be judged
    path = journal.dir / f"{days[1].date()}.jsonl"
    with path.open("ab") as fh:
        fh.write(b'{"type": "summary", "strategy_value_change": {"mom": SECRET-TEXT')
    out = dc.check(ledger, journal, apply=True)
    row = _row(out, hid)
    assert row["decision"] == "cannot judge" and f"{days[1].date()}.jsonl line 3" in row["reason"]
    assert "SECRET-TEXT" not in json.dumps(out) and "SECRET-TEXT" not in json.dumps(_decay_entries(journal))
    assert ledger.get(hid)["status"] == "passed backtest only" and ledger.findings(hid) == [] and out["exit_code"] == 0


def test_damage_before_a_strategys_first_paper_day_does_not_stop_it_being_judged(desk):
    ledger, journal, root = desk
    hid = _idea(ledger, "Funds that rose keep rising", {"sharpe": 1.0, "max_drawdown": 0.05})
    (journal.dir / "2026-05-01.jsonl").write_bytes(b'{"torn')
    _returns_record(journal, hid, "mom", [0.0, -0.3, 0.01])
    out = dc.check(ledger, journal, apply=True)
    assert _row(out, hid)["decision"] == "paused"


def test_a_strategy_with_no_backtest_figures_cannot_be_judged(desk):
    ledger, journal, root = desk
    none = _idea(ledger, "Funds that rose keep rising", None)
    empty = _idea(ledger, "Funds that fell keep falling", {"sharpe": None, "max_drawdown": None})
    for hid, name in ((none, "a"), (empty, "b")):
        _returns_record(journal, hid, name, [0.0, -0.5, 0.01])
    out = dc.check(ledger, journal, apply=True)
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
    out = dc.check(ledger, journal, apply=True)
    assert _row(out, one)["decision"] == "cannot judge" and "1 paper day" in _row(out, one)["reason"]
    assert _row(out, never)["decision"] == "cannot judge" and "0 paper days" in _row(out, never)["reason"]
    assert ledger.get(one)["status"] == ledger.get(never)["status"] == "passed backtest only"


# ---------------------------------------------------------------- two strategies in one fund, through the real paper trader
def _shared_fund_prices():
    n = 40
    path = np.concatenate([np.full(30, 0.01), np.full(10, -0.025)])          # thirty days up, then a fall of about 22%
    up = 100 * np.exp(np.cumsum(path))
    return pd.DataFrame({"UP": up}, index=pd.bdate_range("2026-01-05", periods=n))


def _long(name, size):
    return st.RuleStrategy.from_spec({
        "name": name, "version": "1.0.0", "hypothesis": "A fund that rose over ten days keeps rising", "market": ["UP"],
        "entry": {"rule": "momentum", "lookback": 10, "enter": 0.03, "exit": -1.0, "direction": "long"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": size, "max_open_positions": 1, "stop_loss_pct": None}})


def test_two_strategies_sharing_a_fund_are_each_judged_on_their_own_part(desk):
    ledger, journal, root = desk
    limits = root / "config" / "risk.json"
    limits.parent.mkdir()
    limits.write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.9, "max_open_positions": 5}))
    big = _idea(ledger, "Funds that rose keep rising, held large", {"sharpe": 1.0, "max_drawdown": 0.05})
    small = _idea(ledger, "Funds that rose keep rising, held small", {"sharpe": 1.0, "max_drawdown": 0.05})
    trader = pp.PaperTrader(journal.dir, limits, starting_cash=CAPITAL, halt_file=None)
    both = {big: _long("big", 0.8), small: _long("small", 0.1)}
    px = _shared_fund_prices()
    for i in range(12, len(px) + 1):
        trader.step(px.iloc[:i], both)
    held = {name: book["UP"]["units"] for name, book in trader.state["positions"].items()}
    assert held["big"] > 0 and held["small"] > 0                          # both hold the same fund
    out = dc.check(ledger, journal, apply=True)
    for hid, name in ((big, "big"), (small, "small")):
        mine = [h["strategy_value_change"][name] / h["capital_per_strategy"] for h in trader.state["history"]]
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
        ledger = lg.HypothesisLedger()
        journal = pp.Journal(tmp_path / f"book{book}")
        start = pd.Timestamp("2026-01-05")
        expected = {}
        names = {}
        lengths = rng.integers(2, 90, size=5)
        for k in range(5):
            bt = {"sharpe": float(rng.uniform(0.2, 2.5)), "max_drawdown": float(rng.uniform(0.02, 0.25))}
            hid = _idea(ledger, f"Idea number {book} {k} about fund {k}", bt)
            names[hid] = f"s{k}"
            n = int(lengths[k])
            returns = (rng.uniform(-0.002, 0.003) + rng.uniform(0.002, 0.02) * rng.standard_normal(n)).tolist()
            expected[hid] = (bt, returns)
        # every strategy's days go into the same journal, interleaved as the paper trader would write them
        for day in range(int(lengths.max())):
            d = start + pd.offsets.BDay(day)
            change = {}
            for hid, (bt, returns) in expected.items():
                offset = int(lengths.max()) - len(returns)                 # later starters join the book later
                if day >= offset:
                    journal.write(d, {"type": "decision", "hypothesis_id": hid, "strategy": names[hid], "decision": "no trade"})
                    change[names[hid]] = returns[day - offset] * CAPITAL
            journal.write(d, {"type": "summary", "strategy_value_change": change, "capital_per_strategy": CAPITAL})
        out = dc.check(ledger, journal, apply=False)
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
    _returns_record(journal, calm, "calm", [0.0, 0.01, 0.01])
    ledger.close()
    assert dc.main(["check"], paths=paths) == 0
    ledger = lg.HypothesisLedger(paths.ledger)
    deep = _idea(ledger, "Funds that fell keep falling", {"sharpe": 1.0, "max_drawdown": 0.01})
    _returns_record(journal, deep, "deep", [0.0, -0.1, 0.01])
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
