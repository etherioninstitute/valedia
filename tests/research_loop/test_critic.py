import json
import math

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import critic as cr
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop.paper import Journal

LIMITS = {"max_daily_loss_pct": 2.0, "max_drawdown_pct": 8.0, "max_position_pct": 0.10, "max_open_positions": 5}


@pytest.fixture
def desk(tmp_path):
    limits = tmp_path / "risk.json"
    limits.write_text(json.dumps(LIMITS))
    ledger = lg.HypothesisLedger()
    hid = ledger.propose("Assets that rose keep rising for a while", "price-analyst", "us-etf")
    ledger.register_prediction(hid, "prediction", {"annual_return": 0.10, "win_rate": 0.55, "sharpe": 0.9,
                                                   "fail_if_sharpe_below": 0.3, "fail_if_drawdown_above": 0.2})
    ledger.record_backtest(hid, {"result": {"cagr": 0.10, "annual_vol": 0.08, "max_drawdown": 0.06, "sharpe": 1.2}, "verdict": "pass"},
                           "trending up", "strategies/mom.json", "1.0.0", "untested")
    ledger.record_sealed(hid, {"verdict": "pass"}, "passed backtest only")
    return ledger, Journal(tmp_path / "journal"), limits, hid


def summaries(journal, start, daily_returns, name="mom"):
    days = pd.bdate_range(start, periods=len(daily_returns))
    for d, r in zip(days, daily_returns):
        journal.write(d, {"type": "summary", "strategy_value_change": {name: r * 50_000.0}, "capital_per_strategy": 50_000.0})
    return days


def fill(journal, day, pnl, symbol="SPY", regime="choppy", reason="exit: stop loss: -10.0% against the position"):
    journal.write(day, {"type": "fill", "strategy": "mom", "version": "1.0.0", "symbol": symbol, "units": -1.0, "price": 100.0,
                        "fee": 0.1, "realized_pnl": pnl, "entry_conditions": {"regime": regime}, "reason": reason})


def test_too_few_days_is_said_plainly_and_nothing_is_paused(desk):
    ledger, journal, limits, hid = desk
    days = summaries(journal, "2026-11-02", [0.001, -0.002, 0.0005, 0.001, -0.0005])
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert any("too few to judge" in f for f in out["findings"]) and ledger.get(hid)["status"] == "passed backtest only"
    text = (journal.dir / f"review-{days[-1].date()}.md").read_text()
    assert text.startswith("# Critic's review") and "### mom (passed backtest only)" in text and "Nothing to report." in text
    assert ledger.get(hid)["paper_trading_result"]["days"] == 5 and "too few to judge" in ledger.get(hid)["lessons_learned"]


def test_a_strategy_far_below_its_backtest_is_paused_and_the_ledger_says_why(desk):
    ledger, journal, limits, hid = desk
    rng = np.random.default_rng(0)
    days = summaries(journal, "2026-09-01", (-0.002 + 0.004 * rng.standard_normal(30)).tolist())
    for d in days[-4:-1]:
        fill(journal, d, -120.0)
    fill(journal, days[-1], 40.0, reason="exit: exit rule")
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    notes = " ".join(out["findings"])
    assert "PAUSE mom" in notes and ledger.get(hid)["status"] == "paused"
    assert "worse" in notes and "the prediction said 55%" in notes
    assert "3 of mom's 3 losing trades this week were opened in a choppy market" in notes
    assert "Repeating mistake: mom lost 3 times this week on SPY" in notes
    assert "Repeating mistake: mom lost 3 times this week with the same exit exit: stop loss" in notes
    h = ledger.get(hid)
    assert "status -> paused" in h["lessons_learned"] and h["paper_trading_result"]["z_vs_backtest"] < -2
    dry = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)
    assert dry["strategies"]["mom"]["result"]["status"] == "paused"


def test_a_strategy_that_holds_up_for_weeks_passes_paper(desk):
    ledger, journal, limits, hid = desk
    rng = np.random.default_rng(1)
    days = summaries(journal, "2026-09-01", (0.0006 + 0.003 * rng.standard_normal(25)).tolist())
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "passed paper" and out["strategies"]["mom"]["result"]["days"] == 25
    assert not any("PAUSE" in f for f in out["findings"])


def test_drawdown_deeper_than_the_whole_backtest_pauses_at_once(desk):
    ledger, journal, limits, hid = desk
    days = summaries(journal, "2026-11-02", [-0.03, -0.03, -0.02])
    cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "paused" and "deeper than the worst drawdown" in ledger.get(hid)["lessons_learned"]


def test_an_order_that_got_past_the_limits_is_the_first_thing_reported(desk):
    ledger, journal, limits, hid = desk
    day = pd.Timestamp("2026-11-06")
    account = {"equity": 100_000.0, "daily_loss_pct": 0.1, "drawdown_pct": 0.5, "open_positions": 1}
    journal.write(day, {"type": "decision", "hypothesis_id": hid, "strategy": "mom", "version": "1.0.0", "symbol": "SPY",
                        "order": {"value": 40_000.0}, "account": account, "decision": "placed", "risk_check": "OK"})
    journal.write(day, {"type": "decision", "hypothesis_id": hid, "strategy": "mom", "version": "1.0.0", "symbol": "QQQ",
                        "order": {"value": 4_000.0}, "account": account, "decision": "placed", "risk_check": "OK"})
    for _ in range(3):
        journal.write(day, {"type": "decision", "hypothesis_id": hid, "strategy": "mom", "symbol": "TLT", "order": {"value": 1.0},
                            "account": account, "decision": "blocked", "risk_check": "Too many open positions."})
    journal.write(day, {"type": "kill_switch", "decision": "latched", "reasoning": "drawdown 8.20% reached the limit"})
    out = cr.weekly_review(ledger, journal, day, {hid: "mom"}, limits)
    assert out["kill_switch_failures"] == 1 and out["findings"][0].startswith("KILL SWITCH FAILURE: an order for SPY")
    text = (journal.dir / "review-2026-11-06.md").read_text()
    assert text.index("## Fix first") < text.index("## Strategies") and ledger.get(hid)["status"] == "paused"
    assert "refused 3 orders this week for the same reason: Too many open positions." in text and "Kill switch latched" in text
    assert any(f["hypothesis_id"] is None for f in ledger.findings())


def test_narrator_gets_the_facts_and_cannot_break_the_review(desk):
    ledger, journal, limits, hid = desk
    days = summaries(journal, "2026-11-02", [0.001, 0.002])
    seen = []
    cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, narrator=lambda p: seen.append(p) or "Nothing is proven yet.")
    text = (journal.dir / f"review-{days[-1].date()}.md").read_text()
    assert "Nothing is proven yet." in text and "written by a model from the numbers above" in text and "### mom" in seen[0]
    cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, narrator=lambda p: 1 / 0)
    assert "(The narrator failed: ZeroDivisionError.)" in (journal.dir / f"review-{days[-1].date()}.md").read_text()
    limits.write_text("{broken")
    out = cr.weekly_review(ledger, journal, days[-1], {}, limits)
    assert "could not be read" in out["findings"][0] and "No strategy is in paper trading." in (journal.dir / f"review-{days[-1].date()}.md").read_text()


def test_panel_scorecard_reads_verdicts_and_outcomes_from_the_journal():
    from valedia_trader.research_loop.critic import panel_scorecard
    def decision(sym, day, approve): return {"type": "decision", "strategy": "s", "symbol": sym, "day": day, "panel": {"available": True, "approve": approve}}
    def settled(sym, day, right): return {"type": "panel_settled", "case_id": f"s:{sym}:{day}", "thesis_right": right}
    entries = [decision("A", "2026-01-05", True), decision("B", "2026-01-05", False), decision("C", "2026-01-06", True),
               {"type": "decision", "strategy": "s", "symbol": "D", "day": "2026-01-06", "panel": {"available": False}},
               settled("A", "2026-01-05", True), settled("B", "2026-01-05", False), settled("C", "2026-01-06", False),
               settled("D", "2026-01-06", True), {"type": "panel_settled", "case_id": "s:A:2026-01-05", "error": "x"}]
    card = panel_scorecard(entries)
    assert card == {"settled": 3, "approved": 2, "refused": 1, "approved_came_true": 0.5, "refused_came_true": 0.0}
    assert panel_scorecard(entries[:4]) is None                       # nothing has reached its horizon yet


def test_benchmark_comparison_gives_both_numbers_and_the_day_count():
    def day(d, value, bench, capital=100_000.0):
        return {"type": "summary", "day": d, "strategy_values": {"mom": value}, "benchmark_values": {"mom": bench}, "capital_per_strategy": capital}
    entries = [day("2026-11-10", 0.0, 0.0), day("2026-11-11", -5.0, 0.0), day("2026-11-12", 112.0, 640.0),
               {"type": "summary", "day": "2026-11-09", "strategy_values": {"old": 1.0}}]            # written before the benchmark existed
    rows = cr.benchmark_comparison(entries)
    assert rows == [{"strategy": "mom", "days": 3, "strategy_value": 112.0, "benchmark_value": 640.0, "difference": -528.0,
                     "difference_pct_of_capital": pytest.approx(-0.528), "as_of": "2026-11-12"}]
    assert cr.benchmark_comparison([e for e in entries if "benchmark_values" not in e]) == []


def test_the_weekly_review_says_where_each_strategy_stands_against_holding_its_funds(desk):
    ledger, journal, limits, hid = desk
    for d, value, bench in (("2026-11-10", 0.0, 0.0), ("2026-11-11", -5.0, 0.0), ("2026-11-12", 112.0, 640.0)):
        journal.write(pd.Timestamp(d), {"type": "summary", "day": d, "equity": 100_000.0 + value, "strategy_values": {"mom": value},
                                        "strategy_value_change": {"mom": 0.0}, "benchmark_values": {"mom": bench}, "capital_per_strategy": 100_000.0})
    review = cr.weekly_review(ledger, journal, pd.Timestamp("2026-11-12"), {hid: "mom"}, limits, apply=False)
    line = next(text for text in review["findings"] if "holding its funds" in text)
    assert "mom" in line and "112" in line and "640" in line and "3 days" in line and "too few" in line


# ---- T20 rev 2: a review of a journal with a skipped line says so and decides nothing in the safer direction

def _torn(journal, day):
    with (journal.dir / f"{pd.Timestamp(day).date()}.jsonl").open("a") as fh:
        fh.write('{"type": "summary", "strategy_value_change": {"mom": -90')


def test_a_review_with_a_skipped_line_says_so_first_is_marked_incomplete_and_promotes_nothing(desk):
    ledger, journal, limits, hid = desk
    rng = np.random.default_rng(1)
    days = summaries(journal, "2026-09-01", (0.0006 + 0.003 * rng.standard_normal(25)).tolist())
    _torn(journal, days[10])
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    first = (journal.dir / f"review-{days[-1].date()}.md").read_text().splitlines()[0]
    assert "INCOMPLETE" in first and f"{days[10].date()}.jsonl line" in first
    assert out["incomplete"] is True and out["skipped_lines"] == [{"file": f"{days[10].date()}.jsonl", "line": 2}]
    assert ledger.get(hid)["status"] == "passed backtest only"                       # the same journal, undamaged, promotes (above)
    assert any("no reason to pause" in f and "cannot say" in f for f in out["findings"])
    assert out["findings"][0].startswith("INCOMPLETE")


def test_a_review_with_a_skipped_line_still_orders_the_pause_it_can_justify(desk):
    ledger, journal, limits, hid = desk
    days = summaries(journal, "2026-11-02", [-0.03, -0.03, -0.02])
    _torn(journal, days[0])
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "paused" and out["incomplete"] is True
    assert not any("cannot say" in f for f in out["findings"] if "PAUSE" in f)


def test_a_review_of_a_clean_journal_is_not_marked_incomplete(desk):
    ledger, journal, limits, hid = desk
    days = summaries(journal, "2026-11-02", [0.001, -0.002, 0.0005])
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert out["incomplete"] is False and out["skipped_lines"] == []
    assert (journal.dir / f"review-{days[-1].date()}.md").read_text().startswith("# Critic's review")


# ---- T29: a day's change is measured against the capital it started with

def test_a_days_change_is_divided_by_the_capital_of_the_day_before_and_a_first_day_by_its_own(desk):
    ledger, journal, limits, hid = desk
    late_id = ledger.propose("Bonds that fell keep falling for a while", "price-analyst", "us-etf")
    days = pd.bdate_range("2026-11-02", periods=5)
    # mom loses 1% of its capital every day. A second strategy joins on the fourth day and the capital per strategy
    # halves; mom's positions that day were still sized from the day before, so its loss that day is still 1,000.
    capital = [100_000.0, 100_000.0, 100_000.0, 50_000.0, 50_000.0]
    mom = [-1_000.0, -1_000.0, -1_000.0, -1_000.0, -500.0]
    late = [None, None, None, -500.0, -500.0]                        # 1% of its own first capital, then of the day before
    for d, cap, m, x in zip(days, capital, mom, late):
        change = {"mom": m} if x is None else {"mom": m, "late": x}
        journal.write(d, {"type": "summary", "strategy_value_change": change, "capital_per_strategy": cap})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom", late_id: "late"}, limits, apply=False)
    a, b = out["strategies"]["mom"]["result"], out["strategies"]["late"]["result"]
    assert a["total_return"] == pytest.approx(0.99 ** 5 - 1) and a["max_drawdown"] == pytest.approx(1 - 0.99 ** 5)
    assert a["week_return"] == pytest.approx(0.99 ** 5 - 1) and a["annualised_return"] == pytest.approx(-0.01 * 252)
    assert b["total_return"] == pytest.approx(0.99 ** 2 - 1) and b["days"] == 2
    assert ledger.get(hid)["paper_trading_result"]["max_drawdown"] == pytest.approx(1 - 0.99 ** 5)


def test_with_one_capital_throughout_the_returns_are_as_before(desk):
    ledger, journal, limits, hid = desk
    days = summaries(journal, "2026-11-02", [0.01, -0.02, 0.005])
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)
    assert out["strategies"]["mom"]["result"]["total_return"] == pytest.approx(1.01 * 0.98 * 1.005 - 1)


# ---- T29 rev 2: a day with a carried price is not evidence toward passing paper; the first day on its own capital

def _carried_summaries(journal, start, daily_returns, carried_on, fund="SPY", holder="mom"):
    """Summaries as `summaries`, the days at positions `carried_on` naming `fund` as carried; `holder` bought it on day 0."""
    days = pd.bdate_range(start, periods=len(daily_returns))
    journal.write(days[0], {"type": "fill", "strategy": holder, "version": "1.0.0", "symbol": fund, "units": 10.0, "price": 100.0,
                            "fee": 0.0, "realized_pnl": None})
    for k, (d, r) in enumerate(zip(days, daily_returns)):
        entry = {"type": "summary", "strategy_value_change": {"mom": r * 50_000.0}, "capital_per_strategy": 50_000.0}
        if k in carried_on:
            entry["stale_prices"] = {fund: 1}
        journal.write(d, entry)
    return days


def _rising(n, seed=1):
    return (0.0006 + 0.003 * np.random.default_rng(seed).standard_normal(n)).tolist()


def test_days_with_a_carried_price_do_not_count_toward_passing_paper_and_the_review_says_so(desk):
    ledger, journal, limits, hid = desk
    days = _carried_summaries(journal, "2026-09-01", _rising(22), carried_on={4, 9, 15})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "passed backtest only"            # 19 days that count: below the bar of 20
    result = out["strategies"]["mom"]["result"]
    assert result["days"] == 22 and result["carried_price_days"] == 3 and result["carried_price_funds"] == ["SPY"]
    assert any("3 paper days with a carried price (SPY)" in f and "left out" in f for f in out["findings"])
    assert "3 paper days with a carried price (SPY)" in (journal.dir / f"review-{days[-1].date()}.md").read_text()


def test_the_same_days_with_every_price_pass_paper(desk):
    ledger, journal, limits, hid = desk
    days = _carried_summaries(journal, "2026-09-01", _rising(22), carried_on=set())
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "passed paper" and "carried_price_days" not in out["strategies"]["mom"]["result"]
    assert not any("carried price" in f for f in out["findings"])


def test_a_carried_price_of_a_fund_another_strategy_holds_does_not_count_against_this_one(desk):
    ledger, journal, limits, hid = desk
    days = _carried_summaries(journal, "2026-09-01", _rising(22), carried_on={4, 9, 15}, holder="other")
    cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "passed paper"


def test_a_fund_sold_before_the_carried_day_does_not_count_against_the_strategy(desk):
    ledger, journal, limits, hid = desk
    days = _carried_summaries(journal, "2026-09-01", _rising(22), carried_on={4, 9, 15})
    journal.write(days[2], {"type": "fill", "strategy": "mom", "version": "1.0.0", "symbol": "SPY", "units": -10.0, "price": 101.0,
                            "fee": 0.0, "realized_pnl": 10.0})
    cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "passed paper"


def test_carried_days_still_count_for_a_pause(desk):
    ledger, journal, limits, hid = desk
    days = _carried_summaries(journal, "2026-11-02", [-0.03, -0.03, -0.02], carried_on={0, 1, 2})
    cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "paused" and "deeper than the worst drawdown" in ledger.get(hid)["lessons_learned"]


def test_a_first_day_is_measured_on_its_own_capital_when_the_next_days_capital_differs(desk):
    ledger, journal, limits, hid = desk
    days = pd.bdate_range("2026-11-02", periods=3)
    for d, cap, change in zip(days, [40_000.0, 60_000.0, 50_000.0], [-400.0, -400.0, -600.0]):
        journal.write(d, {"type": "summary", "strategy_value_change": {"mom": change}, "capital_per_strategy": cap})
    r = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)["strategies"]["mom"]["result"]
    # -400 on its first capital 40,000; -400 on 40,000 (the day before); -600 on 60,000: 1% each day
    assert r["total_return"] == pytest.approx(0.99 ** 3 - 1) and r["max_drawdown"] == pytest.approx(1 - 0.99 ** 3)


# ---- T29 rev 3: a damaged field never stops the review; the pause and promotion rules each use the right days;
# a day run twice counts once

def _damaged_summaries(journal, daily_returns, field, on=(3, 4, 5)):
    """As `_carried_summaries` (mom holds SPY from day 0), with `field` on the summaries of the days at `on`."""
    days = pd.bdate_range("2026-09-01", periods=len(daily_returns))
    journal.write(days[0], {"type": "fill", "strategy": "mom", "version": "1.0.0", "symbol": "SPY", "units": 10.0, "price": 100.0,
                            "fee": 0.0, "realized_pnl": None})
    for k, (d, r) in enumerate(zip(days, daily_returns)):
        journal.write(d, {"type": "summary", "strategy_value_change": {"mom": r * 50_000.0}, "capital_per_strategy": 50_000.0,
                          **(field if k in on else {})})
    return days


@pytest.mark.parametrize("damaged", [5, "SPY", None, [1, 2], {"SPY": 1, "QQQ": "x"}])
def test_a_damaged_carried_price_field_is_counted_as_unreadable_and_never_raises(desk, damaged):
    ledger, journal, limits, hid = desk
    days = _damaged_summaries(journal, _rising(22), {"stale_prices": damaged})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    if isinstance(damaged, dict):                                    # a mapping is readable: SPY is held, so the day is carried
        assert out["strategies"]["mom"]["result"]["carried_price_days"] == 3
    else:
        assert out["strategies"]["mom"]["result"]["unreadable_price_days"] == 3
        assert any("3 paper days whose carried-price field could not be read" in f for f in out["findings"])
    assert ledger.get(hid)["status"] == "passed backtest only"      # 19 days count: below the bar of 20


def test_a_damaged_suspect_price_field_is_counted_as_unreadable(desk):
    ledger, journal, limits, hid = desk
    days = _damaged_summaries(journal, _rising(22), {"suspect_prices": 7})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert out["strategies"]["mom"]["result"]["unreadable_price_days"] == 3 and ledger.get(hid)["status"] == "passed backtest only"


def test_carried_days_still_count_for_a_pause_on_returns_far_below_the_backtest(desk):
    ledger, journal, limits, hid = desk
    returns = [0.0004] * 25
    for k in (3, 11, 19):
        returns[k] = -0.018                                          # three losing days, each with a carried price
    days = _carried_summaries(journal, "2026-09-01", returns, carried_on={3, 11, 19})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "paused" and "standard errors below the backtest" in ledger.get(hid)["lessons_learned"]
    assert out["strategies"]["mom"]["result"]["z_vs_backtest"] <= -2.0


def test_promotion_needs_a_gain_on_the_days_that_count(desk):
    ledger, journal, limits, hid = desk
    returns = [-0.0002] * 23
    for k in (5, 12, 18):
        returns[k] = 0.01                                            # the gains all fell on days with a carried price
    days = _carried_summaries(journal, "2026-09-01", returns, carried_on={5, 12, 18})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert out["strategies"]["mom"]["result"]["total_return"] > 0
    assert ledger.get(hid)["status"] == "passed backtest only"


def test_a_day_summarised_twice_counts_once_with_its_last_summary(desk):
    ledger, journal, limits, hid = desk
    days = summaries(journal, "2026-11-02", [0.01, -0.02, 0.005])
    journal.write(days[1], {"type": "summary", "strategy_value_change": {"mom": -0.02 * 50_000.0}, "capital_per_strategy": 50_000.0})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)
    r = out["strategies"]["mom"]["result"]
    assert r["days"] == 3 and r["total_return"] == pytest.approx(1.01 * 0.98 * 1.005 - 1)
    journal.write(days[2], {"type": "summary", "strategy_value_change": {"mom": 0.03 * 50_000.0}, "capital_per_strategy": 50_000.0})
    r = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)["strategies"]["mom"]["result"]
    assert r["days"] == 3 and r["total_return"] == pytest.approx(1.01 * 0.98 * 1.03 - 1)    # the last summary of the day


def test_benchmark_comparison_counts_a_day_summarised_twice_once():
    def day(d, value, bench):
        return {"type": "summary", "day": d, "strategy_values": {"mom": value}, "benchmark_values": {"mom": bench}, "capital_per_strategy": 100_000.0}
    rows = cr.benchmark_comparison([day("2026-11-10", 0.0, 0.0), day("2026-11-11", -5.0, 0.0), day("2026-11-11", -6.0, 1.0)])
    assert rows[0]["days"] == 2 and rows[0]["strategy_value"] == -6.0


# ---- T29 rev 4: the review names the field that could not be read

def test_a_damaged_suspect_price_field_is_named_as_the_suspect_price_field(desk):
    ledger, journal, limits, hid = desk
    days = _damaged_summaries(journal, _rising(22), {"suspect_prices": "x"}, on=(3, 4))
    for d in days[6:8]:
        journal.write(d, {"type": "summary", "strategy_value_change": {"mom": 0.001 * 50_000.0}, "capital_per_strategy": 50_000.0,
                          "stale_prices": 7})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert any("2 paper days whose suspect-price field could not be read" in f for f in out["findings"])
    assert any("2 paper days whose carried-price field could not be read" in f for f in out["findings"])


# ---- T29 rev 5: the weekly review names bad-data days by fund

def test_the_review_names_every_bad_data_day_of_the_week_by_fund(desk):
    ledger, journal, limits, hid = desk
    days = pd.bdate_range("2026-11-02", periods=6)
    marks = {1: {"stale_prices": {"SPY": 2}, "carried_close_days": {"SPY": "2026-10-30"}},
             2: {"suspect_prices": {"QQQ": 612.5}, "stale_prices": {"QQQ": 1}, "carried_close_days": {"QQQ": None}},
             4: {"unreadable_prices": ["XLE", "XLF"], "stale_prices": {"XLE": 31}, "carried_close_days": {"XLE": "2026-11-05"}}}
    for k, d in enumerate(days):
        journal.write(d, {"type": "summary", "strategy_value_change": {"mom": 50.0}, "capital_per_strategy": 50_000.0, **marks.get(k, {})})
    journal.write(days[0] - pd.Timedelta(days=7), {"type": "summary", "strategy_value_change": {"mom": 50.0},
                                                   "capital_per_strategy": 50_000.0, "unreadable_prices": ["OLD"]})
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)
    named = [f for f in out["findings"] if f.startswith("Price data")]
    assert [f.split(":")[0] for f in named] == [f"Price data on {days[k].date()}" for k in (1, 2, 4)]
    assert "SPY" in named[0] and "carried" in named[0]
    assert "QQQ" in named[1] and "suspect" in named[1] and "612.5" in named[1]
    assert "XLE" in named[2] and "XLF" in named[2] and "unreadable" in named[2]
    # rev 6 (outcome 5): the close a carried fund was valued at, never a count that is the frame's length
    assert "SPY carried, valued at its close of 2026-10-30" in named[0] and "QQQ" in named[1] and "valued at its cost" in named[1]
    assert "XLE unreadable, carried, valued at its close of 2026-11-05" in named[2] and not any("31 day" in f or "2 day" in f for f in named)
    assert not any("OLD" in f for f in out["findings"])                  # a day before the week is not listed
    body = open(out["path"]).read()
    assert all(f in body for f in named)                                 # in the review a person reads


def test_the_reviews_bad_data_lines_are_findings_only_never_lessons_in_the_ledger(desk):
    """Rev 6, outcome 6: the review's bad-data lines are in its findings and its file, and never written to the
    hypothesis ledger as lessons, however often the review runs, with `apply` or without."""
    ledger, journal, limits, hid = desk
    days = pd.bdate_range("2026-11-02", periods=6)
    for k, d in enumerate(days):
        extra = {"stale_prices": {"SPY": 1}, "carried_close_days": {"SPY": str(days[k - 1].date())}} if k in (2, 3) else {}
        journal.write(d, {"type": "summary", "strategy_value_change": {"mom": 50.0}, "capital_per_strategy": 50_000.0, **extra})
    for apply in (False, True, False):
        out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=apply)
        assert len([f for f in out["findings"] if f.startswith("Price data on ")]) == 2
    assert not [r for r in ledger.findings() if "Price data" in r["finding"]]
    assert ledger.findings()                                            # the other findings are written as before


# ---------------------------------------------------------------------------------------------------------------------
# T40-B1: paper and backtest are compared on the same basis (the simple annual average, daily mean times 252)
# ---------------------------------------------------------------------------------------------------------------------

def _desk_with_backtest(tmp_path, bt):
    limits = tmp_path / "risk.json"
    limits.write_text(json.dumps(LIMITS))
    ledger = lg.HypothesisLedger()
    hid = ledger.propose("Assets that rose keep rising for a while", "price-analyst", "us-etf")
    ledger.register_prediction(hid, "prediction", {"annual_return": 0.10, "win_rate": 0.55, "sharpe": 0.9,
                                                   "fail_if_sharpe_below": 0.3, "fail_if_drawdown_above": 0.2})
    ledger.record_backtest(hid, {"result": bt, "verdict": "pass"}, "trending up", "strategies/mom.json", "1.0.0", "untested")
    ledger.record_sealed(hid, {"verdict": "pass"}, "passed backtest only")
    return ledger, Journal(tmp_path / "journal"), limits, hid


# A volatile backtest. Daily mean 0.2 / 252, annual vol 0.40: sharpe 0.5, so sharpe * annual_vol = 0.20 is its SIMPLE annual
# average. Its compound figure (cagr) is far below that, 0.11, because volatility drags compounding down by about vol^2 / 2.
VOLATILE_BACKTEST = {"cagr": 0.11, "annual_vol": 0.40, "sharpe": 0.5, "max_drawdown": 0.50}
# Paper: 25 days of +0.06% a day, a simple annual average of 0.0006 * 252 = 0.1512.
#   old comparison: 0.1512 against the compound 0.11  -> "ahead", no sentence, z = +0.03 (a wrong sign)
#   new comparison: 0.1512 against the simple 0.20    -> "behind", sentence printed, z = -0.04
LIVE_DAILY, LIVE_DAYS = 0.0006, 25


def test_paper_is_compared_with_the_backtests_simple_average_and_not_its_compound_return(tmp_path):
    ledger, journal, limits, hid = _desk_with_backtest(tmp_path, dict(VOLATILE_BACKTEST))
    days = summaries(journal, "2026-09-01", [LIVE_DAILY] * LIVE_DAYS)
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)
    result = out["strategies"]["mom"]["result"]
    notes = out["strategies"]["mom"]["notes"]
    simple = VOLATILE_BACKTEST["sharpe"] * VOLATILE_BACKTEST["annual_vol"]
    # an independent form of the z-score: standard error of the mean of n days when each day has sd annual_vol / sqrt(252)
    z = (LIVE_DAILY - simple / 252) * math.sqrt(LIVE_DAYS) * math.sqrt(252) / VOLATILE_BACKTEST["annual_vol"]
    assert result["z_vs_backtest"] == pytest.approx(z, abs=1e-12) and z < 0           # the old code gave +0.03
    assert result["annualised_return"] == pytest.approx(LIVE_DAILY * 252)             # this key keeps its meaning
    sentence = next(n for n in notes if "is running at" in n)                         # the old code printed nothing here
    assert "+15.12% a year on paper against +20.00% in its backtest, +4.88% worse" in sentence
    assert "average daily return times 252" in sentence and "25 days" in sentence and "z -0.04" in sentence
    assert "11.00%" not in sentence                                                    # the compound figure is not offered as the yardstick


def test_the_note_is_not_triggered_when_paper_is_above_the_simple_average(tmp_path):
    ledger, journal, limits, hid = _desk_with_backtest(tmp_path, dict(VOLATILE_BACKTEST))
    days = summaries(journal, "2026-09-01", [0.0010] * LIVE_DAYS)                      # 0.252 a year: above 0.20
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)
    assert not any("is running at" in n for n in out["strategies"]["mom"]["notes"])
    assert out["strategies"]["mom"]["result"]["z_vs_backtest"] > 0


@pytest.mark.parametrize("bt", [
    {"cagr": 0.11, "annual_vol": 0.40, "max_drawdown": 0.5},                           # no sharpe
    {"cagr": 0.11, "sharpe": 0.5, "max_drawdown": 0.5},                                # no annual_vol
    {"cagr": 0.11, "max_drawdown": 0.5},                                               # neither
    {"cagr": 0.11, "annual_vol": 0.40, "sharpe": float("nan"), "max_drawdown": 0.5},   # not finite
    {"cagr": 0.11, "annual_vol": float("inf"), "sharpe": 0.5, "max_drawdown": 0.5},
])
def test_without_sharpe_or_annual_vol_there_is_no_z_and_no_sentence_and_never_the_cagr(tmp_path, bt):
    ledger, journal, limits, hid = _desk_with_backtest(tmp_path, bt)
    days = summaries(journal, "2026-09-01", [-0.004] * LIVE_DAYS)                      # far below the cagr: the old code would speak
    out = cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits, apply=False)
    result, notes = out["strategies"]["mom"]["result"], out["strategies"]["mom"]["notes"]
    assert math.isnan(result["z_vs_backtest"]) and not any("is running at" in n for n in notes)
    assert not any("standard errors below the backtest" in n or "overfit" in n for n in notes)
    assert result["status"] != "paused" and result["backtest_annual_return"] == 0.11  # the stored key keeps its meaning


def test_the_promotion_z_uses_the_simple_average_too(tmp_path):
    # 25 days at +0.06%: against the simple 0.20 the z is -0.04 (above the -1.0 bar), so a strategy that held up still passes
    ledger, journal, limits, hid = _desk_with_backtest(tmp_path, dict(VOLATILE_BACKTEST))
    days = summaries(journal, "2026-09-01", [LIVE_DAILY] * LIVE_DAYS)
    cr.weekly_review(ledger, journal, days[-1], {hid: "mom"}, limits)
    assert ledger.get(hid)["status"] == "passed paper"
    assert f"z {-0.0384:.2f}" in ledger.get(hid)["lessons_learned"]
