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
