import json

import numpy as np
import pandas as pd
import pytest

from valedia_trader.agents.panel import CallableMember
from valedia_trader.research_loop import tournament as tm


def market(n=700, seed=1):
    """Three assets on business days: one drifting up, one down, one flat."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({name: 100 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(n)))
                         for name, drift in (("UPP", 0.002), ("DWN", -0.002), ("FLT", 0.0))}, index=days)


def test_a_question_uses_only_prices_up_to_the_day_it_is_asked():
    px = market()
    as_of = px.index[500]
    asked = tm.direction_questions(px, ["UPP", "DWN"], as_of, horizon=5, market="funds")
    later = px.copy()
    later.iloc[501:] *= 3.0                                          # the future changes; the questions must not
    again = tm.direction_questions(later, ["UPP", "DWN"], as_of, horizon=5, market="funds")
    assert [q.to_row() for q in asked] == [q.to_row() for q in again]
    q = asked[0]
    assert q.id == "funds:UPP:%s:5d" % as_of.date() and q.thesis == "UPP closes higher over the next 5 trading days"
    assert q.symbol == "UPP" and q.asked_on == str(as_of.date()) and q.facts["symbol"] == "UPP" and q.kind == "forward"
    assert 0.5 < q.baseline < 0.95 and asked[1].baseline < 0.5     # the share of past 5-day rises, from earlier prices only


def test_a_blind_question_names_neither_the_asset_nor_the_date():
    px = market()
    as_of = px.index[500]
    q = tm.direction_questions(px, ["UPP"], as_of, horizon=5, market="funds", blind=True)[0]
    shown = json.dumps({"thesis": q.thesis, "facts": q.facts})
    assert "UPP" not in shown and str(as_of.date()) not in shown and str(as_of.year) not in shown
    assert "UPP" not in q.id and q.id.startswith("blind:") and q.kind == "blind history"
    assert q.symbol == "UPP" and q.asked_on == str(as_of.date())     # kept beside the question, to settle it
    assert "momentum_20d_pct" in q.facts and "data_age_days" not in q.facts


def test_too_little_history_gives_no_question():
    px = market()
    assert tm.direction_questions(px, ["UPP", "NOPE"], px.index[20], horizon=5, market="funds") == []


def test_the_ledger_settles_a_question_only_once_its_horizon_has_passed(tmp_path):
    px = market()
    as_of = px.index[500]
    led = tm.TournamentLedger(tmp_path / "t.sqlite")
    qs = tm.direction_questions(px, ["UPP", "DWN"], as_of, horizon=5, market="funds")
    assert led.add(qs) == 2 and led.add(qs) == 0                     # the same question is never added twice
    assert tm.settle_directions(led, px.iloc[:505]) == 0             # four sessions later: too early
    assert tm.settle_directions(led, px.iloc[:506]) == 2
    assert tm.settle_directions(led, px) == 0                        # and never settled twice
    got = {r["symbol"]: r["outcome"] for r in led.settled()}
    assert got == {s: int(px[s].iloc[505] > px[s].iloc[500]) for s in ("UPP", "DWN")}
    led.close()


def test_asking_records_errors_and_never_asks_the_same_forecaster_twice(tmp_path):
    px = market()
    led = tm.TournamentLedger(tmp_path / "t.sqlite")
    led.add(tm.direction_questions(px, ["UPP", "DWN", "FLT"], px.index[500], horizon=5, market="funds"))
    calls = []
    def good(case):
        calls.append(case["thesis"])
        return 0.6, None, "ok"
    def broken(case):
        raise RuntimeError("model fell over")
    out = tm.ask(led, [CallableMember("steady", "decider", good), CallableMember("broken", "decider", broken)])
    assert out == {"steady": {"asked": 3, "answered": 3}, "broken": {"asked": 3, "answered": 0}}
    assert tm.ask(led, [CallableMember("steady", "decider", good)]) == {"steady": {"asked": 0, "answered": 0}} and len(calls) == 3
    assert "symbol" in led.questions()[0]["facts"] and set(calls) == {q["thesis"] for q in led.questions()}
    led.close()


def _play(tmp_path, forecasters, weeks=60):
    """Ask on non-overlapping weeks about three assets, settle everything, return the scores."""
    px = market(n=260 + 5 * weeks + 10)
    led = tm.TournamentLedger(tmp_path / "t.sqlite")
    for k in range(weeks):
        led.add(tm.direction_questions(px, ["UPP", "DWN", "FLT"], px.index[260 + 5 * k], horizon=5, market="funds", blind=True))
    truth = {q["id"]: int(px[q["symbol"]].iloc[px.index.get_loc(pd.Timestamp(q["asked_on"])) + 5] > px[q["symbol"]].loc[q["asked_on"]])
             for q in led.questions()}
    by_thesis_facts = {json.dumps(q["facts"], sort_keys=True): q["id"] for q in led.questions()}
    members = [CallableMember(name, "decider", (lambda fn: lambda case: (fn(truth[by_thesis_facts[json.dumps(case["facts"], sort_keys=True)]], case), None, ""))(fn))
               for name, fn in forecasters.items()]
    tm.ask(led, members)
    tm.settle_directions(led, px)
    return tm.scores(led, min_settled=30), led


def test_scores_reward_skill_and_only_skill(tmp_path):
    rng = np.random.default_rng(5)
    scores, led = _play(tmp_path, {
        "sees_ahead": lambda y, case: 0.8 if y else 0.2,
        "coin": lambda y, case: float(rng.uniform(0.05, 0.95)),
        "always_up": lambda y, case: 0.99})
    s = {r["forecaster"]: r for r in scores}
    assert s["sees_ahead"]["settled"] == 180 and s["sees_ahead"]["weeks"] == 60
    assert s["sees_ahead"]["skill"] > 0.5 and s["sees_ahead"]["t_by_week"] < -5 and s["sees_ahead"]["verdict"] == "beats the baseline"
    assert s["coin"]["skill"] < 0 and s["coin"]["verdict"] == "worse than the baseline"
    assert s["always_up"]["brier"] > s["always_up"]["baseline_brier"] and s["always_up"]["verdict"] == "worse than the baseline"
    assert s["sees_ahead"]["mean_forecast"] == pytest.approx(s["sees_ahead"]["came_true"] * 0.6 + 0.2, abs=1e-9)
    led.close()


def test_no_verdict_before_enough_questions_have_resolved(tmp_path):
    scores, led = _play(tmp_path, {"sees_ahead": lambda y, case: 0.8 if y else 0.2}, weeks=6)
    assert scores[0]["settled"] == 18 and scores[0]["verdict"] == "too few resolved questions to judge (18 of 30)"
    led.close()


def test_a_forecaster_that_repeats_the_baseline_is_not_distinguishable_from_it(tmp_path):
    px = market(n=600)
    led = tm.TournamentLedger(tmp_path / "t.sqlite")
    for k in range(60):
        led.add(tm.direction_questions(px, ["UPP", "DWN", "FLT"], px.index[280 + 5 * k], horizon=5, market="funds"))
    base = {q["thesis"] + q["asked_on"]: q["baseline"] for q in led.questions()}
    tm.ask(led, [CallableMember("echo", "decider", lambda case: (base[case["thesis"] + case["facts"]["as_of"]], None, ""))])
    tm.settle_directions(led, px)
    s = tm.scores(led, min_settled=30)[0]
    assert s["skill"] == pytest.approx(0.0, abs=1e-12) and s["verdict"] == "not distinguishable from the baseline"
    led.close()


def test_universes_are_named_lists_with_their_own_price_folders():
    stocks, crypto = tm.universe("stocks"), tm.universe("crypto")
    assert len(stocks["symbols"]) >= 100 and len(set(stocks["symbols"])) == len(stocks["symbols"]) and "AAPL" in stocks["symbols"]
    assert all(s.endswith("-USD") for s in crypto["symbols"]) and {"BTC-USD", "ETH-USD", "SOL-USD"} <= set(crypto["symbols"])
    assert stocks["folder"] != crypto["folder"] and stocks["folder"].startswith("artifacts/")
    with pytest.raises(ValueError, match="stocks"):
        tm.universe("bonds")


def test_refreshing_a_universe_downloads_its_symbols_into_its_folder(tmp_path):
    seen = {}
    def fake(tickers, begin):
        seen["tickers"], seen["begin"] = list(tickers), begin
        days = pd.bdate_range("2024-01-02", periods=5)
        cols = pd.MultiIndex.from_product([["Close", "Volume"], list(tickers)])
        return pd.DataFrame(1.0, index=days, columns=cols)
    out = tm.refresh_universe("crypto", root=tmp_path, download=fake)
    assert seen["tickers"] == tm.universe("crypto")["symbols"] and seen["begin"] == tm.universe("crypto")["start"]
    assert (tmp_path / "artifacts/data_crypto/close.parquet").exists() and out["symbols"] == len(seen["tickers"])
