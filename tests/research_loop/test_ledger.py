import sqlite3

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import ledger as lg


@pytest.fixture
def led():
    return lg.HypothesisLedger()


def test_schema_has_the_columns_the_loop_needs(led):
    cols = {r[1] for r in led._db.execute("PRAGMA table_info(hypotheses)")}
    assert {"id", "idea", "source_agent", "market", "date_tested", "strategy_file", "strategy_version", "variations_tried",
            "market_regime", "backtest_result", "sealed_test_result", "paper_trading_result", "status",
            "lessons_learned"} <= cols
    assert lg.STATUSES == ("untested", "failed", "passed backtest only", "passed paper", "paused", "retired")
    assert lg.REGIMES == ("trending up", "trending down", "choppy", "high volatility")


def test_search_by_market_regime_and_status(led):
    a = led.propose("Buy 12-month winners among sector ETFs", "price-analyst", "us-etf")
    b = led.propose("Fade 5-day losers in bond ETFs", "price-analyst", "us-etf")
    c = led.propose("Hold bitcoin above its 200-day average", "macro-watcher", "crypto")
    led.record_backtest(a, {"sharpe": 0.2}, "choppy", "strategies/a.py", "1.0.0", "failed")
    led.record_backtest(b, {"sharpe": 1.1}, "trending up", "strategies/b.py", "1.0.0", "untested")
    assert [h["id"] for h in led.search(market="us-etf")] == [b, a]
    assert [h["id"] for h in led.search(status="failed")] == [a]
    assert [h["id"] for h in led.search(regime="trending up")] == [b]
    assert [h["id"] for h in led.search(market="crypto", status="untested")] == [c]
    assert led.search(market="us-etf", regime="choppy", status="failed")[0]["backtest_result"] == {"sharpe": 0.2}
    assert [h["id"] for h in led.search(text="bitcoin")] == [c]
    with pytest.raises(ValueError):
        led.search(status="winning")
    with pytest.raises(ValueError):
        led.search(regime="sideways")


def test_a_failed_idea_is_not_proposed_again_unless_conditions_changed(led):
    a = led.propose("Buy sector ETFs with the strongest 12-month momentum", "price-analyst", "us-etf")
    led.set_status(a, "failed", "no edge after costs")
    with pytest.raises(lg.DuplicateIdea, match=f"hypothesis {a}"):
        led.propose("Buy the sector ETFs with strongest 12 month momentum", "macro-watcher", "us-etf")
    assert led.propose("Buy sector ETFs with the strongest 12-month momentum", "price-analyst", "crypto") != a   # other market
    again = led.propose("Buy the sector ETFs with strongest 12 month momentum", "macro-watcher", "us-etf", conditions_changed=True)
    assert again != a and led.get(a)["status"] == "failed"               # the failure stays on record
    assert lg.idea_similarity("Buy winners", "Short the yield curve when it inverts") == 0.0
    with pytest.raises(ValueError):
        led.propose("", "x", "y")


def test_prediction_is_written_once_before_any_backtest_and_cannot_be_changed(led):
    a = led.propose("Idea A", "price-analyst", "us-etf")
    sha = led.register_prediction(a, "I expect a Sharpe of 0.6", {"sharpe": 0.6})
    assert led.prediction(a)["sha256"] == sha and led.prediction(a)["expected"] == {"sharpe": 0.6}
    with pytest.raises(lg.PredictionExists):
        led.register_prediction(a, "Actually 0.2", {"sharpe": 0.2})
    with pytest.raises(sqlite3.IntegrityError, match="never edited"):
        led._db.execute("UPDATE predictions SET body = 'moved goalposts' WHERE hypothesis_id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        led._db.execute("DELETE FROM predictions WHERE hypothesis_id = ?", (a,))
    b = led.propose("Idea B about something else entirely", "price-analyst", "us-etf")
    led.record_attempt(b, "backtest", 0.05, 500, "1.0.0")
    with pytest.raises(lg.LedgerError, match="before the first backtest"):
        led.register_prediction(b, "after the fact", {})
    assert led.prediction(b) is None


def test_every_variation_is_counted_and_never_removed(led):
    a = led.propose("Idea A", "price-analyst", "us-etf")
    b = led.propose("Different idea B", "price-analyst", "us-etf")
    assert led.record_attempt(a, "backtest", 0.05, 500, "1.0.0", {"lookback": 126}) == 1
    assert led.record_attempt(a, "revision", 0.07, 500, "1.1.0") == 2
    assert led.record_attempt(a, "revision", float("nan"), 500, "1.2.0") == 3       # a broken run still counts
    led.record_attempt(b, "backtest", -0.01, 500, "1.0.0")
    assert led.variations(a) == 3 and led.revisions(a) == 2 and led.revisions(b) == 0
    np.testing.assert_allclose(led.attempt_sharpes(hypothesis_id=a), [0.05, 0.07])
    np.testing.assert_allclose(led.attempt_sharpes(market="us-etf"), [0.05, 0.07, -0.01])
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        led._db.execute("DELETE FROM attempts")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        led._db.execute("DELETE FROM hypotheses WHERE id = ?", (a,))
    assert led.summary()["variations"] == 4 and led.summary()["untested"] == 2


def test_the_sealed_period_is_used_once_per_idea(led):
    a = led.propose("Idea A", "price-analyst", "us-etf")
    assert not led.sealed_used(a)
    led.use_sealed(a, "1.0.0")
    assert led.sealed_used(a)
    with pytest.raises(lg.SealedPeriodUsed):
        led.use_sealed(a, "1.1.0")
    with pytest.raises(sqlite3.IntegrityError):
        led._db.execute("DELETE FROM sealed_uses")
    led.record_sealed(a, {"sharpe": 0.4}, "passed backtest only")
    assert led.get(a)["status"] == "passed backtest only" and led.get(a)["sealed_test_result"] == {"sharpe": 0.4}


def test_lessons_accumulate_and_survive_reopening(tmp_path):
    path = tmp_path / "memory" / "ledger.db"
    led = lg.HypothesisLedger(path)
    a = led.propose("Idea A", "price-analyst", "us-etf")
    led.add_lesson(a, "Loses in choppy markets", "critic")
    led.set_status(a, "paused", "live drawdown beyond backtest", "critic")
    led.record_paper(a, {"weeks": 3, "return": -0.02})
    led.add_lesson(None, "Research agents proposed two near-duplicates this week", "critic")
    led.close()
    led = lg.HypothesisLedger(path)
    h = led.get(a)
    assert h["status"] == "paused" and h["paper_trading_result"]["weeks"] == 3
    assert "Loses in choppy markets" in h["lessons_learned"] and "status -> paused" in h["lessons_learned"]
    assert len(led.findings()) == 3 and len(led.findings(a)) == 2
    with pytest.raises(lg.LedgerError):
        led.get(999)


def test_market_regime_labels():
    idx = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(0)
    noise = 0.01 * rng.standard_normal(400)
    up = pd.Series(100 * np.exp(np.cumsum(0.002 + noise)), index=idx)
    down = pd.Series(100 * np.exp(np.cumsum(-0.002 + noise)), index=idx)
    flat = pd.Series(100 * np.exp(np.cumsum(noise - noise.mean())), index=idx)
    wild = pd.Series(100 * np.exp(np.cumsum(3 * (noise - noise.mean()))), index=idx)
    assert lg.market_regime(up) == "trending up" and lg.market_regime(down) == "trending down"
    assert lg.market_regime(flat) == "choppy"
    assert lg.market_regime(wild, reference=flat) == "high volatility"
    assert lg.market_regime(pd.DataFrame({"a": up, "b": up * 2})) == "trending up"
    assert lg.market_regime(up.iloc[:5]) == "choppy"


# ---- T29: a status change and its reason are one write

def _consistent(path):
    """Every idea's status is the one its last status finding names (or the first status, with none), and its lessons
    hold one line per finding: the status and its reason were written together or not at all."""
    con = sqlite3.connect(str(path))
    try:
        for hid, status, lessons in con.execute("SELECT id, status, lessons_learned FROM hypotheses"):
            rows = [r[0] for r in con.execute("SELECT finding FROM findings WHERE hypothesis_id = ? ORDER BY id", (hid,))]
            said = [r for r in rows if r.startswith("status -> ")]
            assert status == (said[-1][len("status -> "):].split(":")[0] if said else "untested"), (hid, status, said[-1:])
            assert lessons.count("\n") == len(rows)
    finally:
        con.close()


CHILD = """
import sys
from valedia_trader.research_loop.ledger import HypothesisLedger
led = HypothesisLedger(sys.argv[1])
ids = [r[0] for r in led._db.execute("SELECT id FROM hypotheses ORDER BY id")]
print("ready", flush=True)
i = int(sys.argv[2])
while True:
    for h in ids:
        i += 1
        led.set_status(h, ("paused", "passed paper")[i % 2], f"round {i} of the critic", "critic")
"""


def test_a_kill_at_any_point_leaves_the_status_and_its_reason_both_or_neither(tmp_path):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    root = str(Path(lg.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
    rng = np.random.default_rng(29)
    chains = [tmp_path / f"ledger{c}.db" for c in range(5)]
    for path in chains:
        led = lg.HypothesisLedger(path)
        for k in range(3):
            led.propose(f"Idea number {k} about a different thing {path.stem}", "price-analyst", "us-etf")
        led.close()
    kills = 0
    for rnd in range(22):                                          # 5 chains x 22 rounds = 110 kills
        children = [subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(p), str(rnd * 100_000)], env=env, cwd=str(tmp_path),
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) for p in chains]
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        time.sleep(float(rng.uniform(0.005, 0.06)))
        for child in children:
            time.sleep(float(rng.uniform(0.0, 0.004)))
            child.kill()                                           # SIGKILL to the child this test started, by its pid
        for child in children:
            child.wait()
            child.stdout.close()
            kills += 1
        for path in chains:
            _consistent(path)
    assert kills >= 100
    led = lg.HypothesisLedger(chains[0])                           # and the ledger carries on after the last kill
    assert led.summary()["paused"] + led.summary()["passed paper"] + led.summary()["untested"] == 3


class _FailingFindings:
    """The ledger's connection, except that writing a finding fails: the status must not be left changed."""

    def __init__(self, db):
        self._real = db

    def execute(self, sql, *args):
        if sql.startswith("INSERT INTO findings"):
            raise sqlite3.OperationalError("disk I/O error")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_status_whose_reason_cannot_be_written_is_not_changed(tmp_path):
    led = lg.HypothesisLedger(tmp_path / "ledger.db")
    a = led.propose("Idea A", "price-analyst", "us-etf")
    led.set_status(a, "passed paper", "held up for 20 days", "critic")
    real, led._db = led._db, _FailingFindings(led._db)
    with pytest.raises(sqlite3.OperationalError):
        led.set_status(a, "paused", "drawdown deeper than the backtest", "critic")
    led._db = real
    led.close()
    again = lg.HypothesisLedger(tmp_path / "ledger.db")
    assert again.get(a)["status"] == "passed paper" and "status -> paused" not in again.get(a)["lessons_learned"]
    _consistent(tmp_path / "ledger.db")
    again.set_status(a, "paused", "drawdown deeper than the backtest", "critic")
    assert again.get(a)["status"] == "paused" and "status -> paused: drawdown" in again.get(a)["lessons_learned"]
    again.set_status(a, "retired")                                 # no reason: the status alone, as before
    assert again.get(a)["status"] == "retired" and len(again.findings(a)) == 2


def test_a_ledger_file_written_before_is_continued_with_the_same_layout(tmp_path):
    path = tmp_path / "ledger.db"
    led = lg.HypothesisLedger(path)
    a = led.propose("Idea A", "price-analyst", "us-etf")
    led.set_status(a, "paused", "first reason", "critic")
    schema = sorted(tuple(r) for r in led._db.execute("SELECT type, name, sql FROM sqlite_master"))
    led.close()
    led = lg.HypothesisLedger(path)
    led.set_status(a, "passed paper", "second reason", "critic")
    assert sorted(tuple(r) for r in led._db.execute("SELECT type, name, sql FROM sqlite_master")) == schema
    assert [f["finding"] for f in led.findings(a)] == ["status -> paused: first reason", "status -> passed paper: second reason"]
    _consistent(path)


def test_a_caller_that_catches_the_error_and_commits_later_saves_neither_the_status_nor_the_reason(tmp_path):
    path = tmp_path / "ledger.db"
    led = lg.HypothesisLedger(path)
    a = led.propose("Idea A", "price-analyst", "us-etf")
    led.set_status(a, "passed paper", "held up for 20 days", "critic")
    real, led._db = led._db, _FailingFindings(led._db)
    try:
        led.set_status(a, "paused", "drawdown deeper than the backtest", "critic")
    except sqlite3.OperationalError:
        pass                                                       # the caller carries on
    led._db = real
    led.add_lesson(None, "a later finding, committed", "critic")   # a later write commits whatever is open
    led._db.commit()
    led.close()
    again = lg.HypothesisLedger(path)
    assert again.get(a)["status"] == "passed paper" and "status -> paused" not in again.get(a)["lessons_learned"]
    assert [f["finding"] for f in again.findings()] == ["status -> passed paper: held up for 20 days", "a later finding, committed"]
    _consistent(path)
