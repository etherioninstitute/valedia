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


# ---- ranking, and the rescaled later-half score -------------------------------------------------------------------

def _filled(forecasters, outcomes, baseline=0.5, market_name="funds", kind="forward"):
    """A ledger with one question per week (ISO weeks, from 2024-01-01) and every forecaster's answer, all settled.
    `forecasters` maps a name to fn(outcome, k) -> probability; `outcomes` is the list of what happened."""
    led = tm.TournamentLedger()
    day0 = pd.Timestamp("2024-01-01")
    for k, y in enumerate(outcomes):
        day = str((day0 + pd.Timedelta(days=7 * k)).date())
        led.add([tm.Question(f"q{k}", market_name, kind, day, 5, "AAA", "AAA closes higher", {}, baseline)])
        for name, fn in forecasters.items():
            led.record(f"q{k}", name, fn(y, k), None, 1.0)
        led.settle(f"q{k}", y)
    return led


def _mixed(n=80, seed=3):
    return [int(v) for v in np.random.default_rng(seed).integers(0, 2, n)]


def test_ranking_is_the_share_of_pairs_the_forecaster_puts_the_right_way_round_with_ties_half():
    led = tm.TournamentLedger()
    for k, (p, y) in enumerate([(0.8, 1), (0.4, 1), (0.6, 0), (0.1, 0)]):
        led.add([tm.Question(f"q{k}", "funds", "forward", f"2024-01-{k + 1:02d}", 5, "A", "t", {}, 0.5)])
        led.record(f"q{k}", "f", p, None, 1.0)
        led.record(f"q{k}", "flat", 0.5, None, 1.0)
        led.settle(f"q{k}", y)
    s = {r["forecaster"]: r for r in tm.scores(led, min_settled=4)}
    assert s["f"]["ranking"] == pytest.approx(0.75) and s["flat"]["ranking"] == pytest.approx(0.5)
    led.close()


def test_a_forecaster_with_only_one_kind_of_outcome_has_no_ranking():
    led = _filled({"f": lambda y, k: 0.7}, [1] * 40)
    s = tm.scores(led, min_settled=30)[0]
    assert s["ranking"] is None and s["ranking_low"] is None and s["ranking_high"] is None
    assert s["rescaled_skill"] is None and s["ranking_verdict"] == "ranking not distinguishable from chance"
    led.close()


def test_a_perfect_ranker_ranks_at_one_and_better_than_chance():
    led = _filled({"sees_ahead": lambda y, k: 0.9 if y else 0.1}, _mixed())
    s = tm.scores(led, min_settled=30)[0]
    assert s["ranking"] == 1.0 and s["ranking_low"] == 1.0 and s["ranking_high"] == 1.0
    assert s["ranking_verdict"] == "ranks better than chance"
    led.close()


def test_tiny_but_ordered_forecasts_lose_on_brier_yet_rank_perfectly_and_beat_the_baseline_once_rescaled():
    led = _filled({"shy": lambda y, k: 0.10 if y else 0.05}, _mixed())
    s = tm.scores(led, min_settled=30)[0]
    assert s["brier"] > s["baseline_brier"] and s["skill"] < 0 and s["verdict"] != "beats the baseline"
    assert s["ranking"] == 1.0 and s["ranking_verdict"] == "ranks better than chance"
    assert s["rescaled_skill"] > 0.5 and s["rescaled_brier"] < s["rescaled_baseline_brier"]
    assert s["rescaled_settled"] == 40 and s["rescaled_t_by_week"] < -2
    led.close()


def test_the_rescaling_is_fitted_on_the_earlier_half_only():
    # earlier half: forecasts carry no information; later half: a perfect signal. A fit that peeked at the later half
    # would rescale it into a great score; a fit on the earlier half alone cannot.
    outcomes = _mixed()
    n = len(outcomes)
    led = _filled({"late_oracle": lambda y, k: (0.5 + 0.01 * (k % 2)) if k < n // 2 else (0.9 if y else 0.1)}, outcomes)
    s = tm.scores(led, min_settled=30)[0]
    assert s["rescaled_settled"] == n // 2 and s["rescaled_skill"] < 0.5
    led.close()


def test_a_coin_has_a_ranking_range_around_one_half_and_no_verdict_of_skill():
    rng = np.random.default_rng(11)
    led = _filled({"coin": lambda y, k: float(rng.uniform(0.05, 0.95))}, _mixed(120))
    s = tm.scores(led, min_settled=30)[0]
    assert s["ranking_low"] <= 0.5 <= s["ranking_high"] and s["ranking_low"] < s["ranking"] < s["ranking_high"]
    assert s["ranking_verdict"] == "ranking not distinguishable from chance"
    led.close()


def test_an_inverted_forecaster_ranks_worse_than_chance():
    led = _filled({"backwards": lambda y, k: 0.2 if y else 0.8}, _mixed())
    s = tm.scores(led, min_settled=30)[0]
    assert s["ranking"] == 0.0 and s["ranking_high"] == 0.0 and s["ranking_verdict"] == "ranks worse than chance"
    led.close()


def test_the_ranking_range_is_reproducible():
    rng = np.random.default_rng(2)
    led = _filled({"noisy": lambda y, k: float(np.clip(0.5 + 0.1 * (2 * y - 1) + 0.3 * rng.standard_normal(), 0.01, 0.99))}, _mixed(100))
    a, b = tm.scores(led, min_settled=30)[0], tm.scores(led, min_settled=30)[0]
    assert (a["ranking"], a["ranking_low"], a["ranking_high"]) == (b["ranking"], b["ranking_low"], b["ranking_high"])
    assert 0.0 < a["ranking_low"] < a["ranking"] < a["ranking_high"] < 1.0
    led.close()


def test_the_ranking_bootstrap_resamples_whole_weeks_with_the_stated_seed():
    # two questions a week, the true one usually forecast higher; the range must be what 500 draws of whole weeks
    # (default_rng(0), weeks in the order they first appear) give, computed here the long way
    rng = np.random.default_rng(4)
    rows, led = [], tm.TournamentLedger()
    day0 = pd.Timestamp("2024-01-01")
    for k in range(40):
        day = str((day0 + pd.Timedelta(days=7 * k)).date())
        for j, y in enumerate((1, 0)):
            p = float(np.clip(0.5 + 0.15 * (2 * y - 1) + 0.25 * rng.standard_normal(), 0.01, 0.99))
            led.add([tm.Question(f"w{k}{j}", "funds", "forward", day, 5, "A", "t", {}, 0.5)])
            led.record(f"w{k}{j}", "f", p, None, 1.0)
            led.settle(f"w{k}{j}", y)
            rows.append((k, p, y))
    def auc(pairs):
        wins = [(pp > qq) + 0.5 * (pp == qq) for pp, yy in pairs if yy == 1 for qq, yz in pairs if yz == 0]
        return sum(wins) / len(wins)
    draw = np.random.default_rng(0)
    got = []
    for _ in range(500):
        picked = [r for w in draw.integers(0, 40, 40) for r in rows if r[0] == w]
        got.append(auc([(p, y) for _, p, y in picked]))
    low, high = np.percentile(got, [2.5, 97.5])
    s = tm.scores(led, min_settled=30)[0]
    assert s["ranking"] == pytest.approx(auc([(p, y) for _, p, y in rows]))
    assert s["ranking_low"] == pytest.approx(low) and s["ranking_high"] == pytest.approx(high)
    led.close()


def test_too_few_resolved_questions_leave_the_rescaled_keys_empty_and_the_ranking_unjudged():
    led = _filled({"f": lambda y, k: 0.9 if y else 0.1}, _mixed(20))
    s = tm.scores(led, min_settled=30)[0]
    assert all(s[k] is None for k in ("rescaled_brier", "rescaled_baseline_brier", "rescaled_skill", "rescaled_t_by_week", "rescaled_settled"))
    assert s["ranking_verdict"] == "too few resolved questions to judge" and s["ranking"] == 1.0
    led.close()


def test_a_half_too_small_or_an_earlier_half_of_one_outcome_leaves_the_rescaled_keys_empty():
    keys = ("rescaled_brier", "rescaled_baseline_brier", "rescaled_skill", "rescaled_t_by_week", "rescaled_settled")
    led = _filled({"f": lambda y, k: 0.9 if y else 0.1}, _mixed(50))          # 50 rows: halves of 25 and 25, under 30
    assert all(tm.scores(led, min_settled=30)[0][k] is None for k in keys)
    led.close()
    led = _filled({"f": lambda y, k: 0.9 if y else 0.1}, [1] * 40 + _mixed(40))  # the earlier half is all one outcome
    s = tm.scores(led, min_settled=30)[0]
    assert all(s[k] is None for k in keys) and s["ranking"] is not None
    led.close()


def test_the_new_measures_leave_the_old_ones_alone(tmp_path):
    scores, led = _play(tmp_path, {"sees_ahead": lambda y, case: 0.8 if y else 0.2})
    s = scores[0]
    old = {"forecaster", "market", "kind", "settled", "weeks", "brier", "baseline_brier", "skill", "t_by_week", "mean_forecast",
           "came_true", "right_side", "verdict"}
    assert old <= set(s) and {"ranking", "ranking_low", "ranking_high", "ranking_verdict", "rescaled_brier", "rescaled_baseline_brier",
                             "rescaled_skill", "rescaled_t_by_week", "rescaled_settled"} <= set(s)
    assert s["ranking"] == 1.0 and s["settled"] == 180 and s["rescaled_settled"] == 90
    led.close()


def test_the_two_rule_forecasters_answer_from_the_fact_sheet():
    rules = {m.name: m for m in tm.reference_forecasters()}
    assert set(rules) == {"rule-momentum", "rule-above-200d"} and len(tm.reference_forecasters()) == 2
    ask = lambda name, facts: rules[name].ask({"thesis": "t", "facts": facts}).probability
    assert [ask("rule-momentum", {"momentum_20d_pct": v}) for v in (3.2, 0.0, -1.5, None)] == [0.6, 0.4, 0.4, 0.5]
    assert ask("rule-momentum", {}) == 0.5
    assert [ask("rule-above-200d", {"above_200d_average": v}) for v in (True, False, None)] == [0.6, 0.4, 0.5]
    assert ask("rule-above-200d", {}) == 0.5


def _price_folder(tmp_path, end=None, n=700):
    days = pd.bdate_range(end=end, periods=n) if end is not None else pd.bdate_range("2023-01-02", periods=n)
    px = market(n=n).set_axis(days)
    folder = tmp_path / "prices"
    folder.mkdir()
    px.to_parquet(folder / "close.parquet")
    (px * 0 + 1_000_000.0).to_parquet(folder / "volume.parquet")
    return folder


@pytest.mark.parametrize("command", ["ask-blind", "ask-forward"])
def test_with_rules_also_asks_the_two_rule_forecasters(tmp_path, monkeypatch, capsys, command):
    folder = _price_folder(tmp_path, end=pd.Timestamp.now().normalize() if command == "ask-forward" else None)
    monkeypatch.setattr(tm, "forecasters", lambda names=None, roster=None: [])        # no model is called
    extra = ["--count", "6"] if command == "ask-blind" else []
    args = ["--root", str(tmp_path), "--ledger", str(tmp_path / "t.sqlite"), command, "--data", str(folder), "--market", "funds"]
    assert tm.main(args + extra) == 0
    assert json.loads(capsys.readouterr().out)["asked"] == {}                          # without the flag: only the panel (none here)
    assert tm.main(args + extra + ["--with-rules"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["asked"]) == {"rule-momentum", "rule-above-200d"}
    assert all(v["asked"] > 0 and v["answered"] == v["asked"] for v in out["asked"].values())
    assert set(out["counts"]["answers"]) == {"rule-momentum", "rule-above-200d"}
    assert len(set(out["counts"]["answers"].values())) == 1


def test_a_wrong_way_forecaster_is_not_credited_by_the_rescaling():
    led = _filled({"backwards": lambda y, k: 0.2 if y else 0.8}, _mixed())
    s = tm.scores(led, min_settled=30)[0]
    assert s["ranking_verdict"] == "ranks worse than chance" and s["rescaled_skill"] <= 0.05
    led.close()


def test_a_perfectly_separable_earlier_half_gives_the_same_rescaled_numbers_every_time():
    led = _filled({"shy": lambda y, k: 0.10 if y else 0.05}, _mixed())
    a, b = tm.scores(led, min_settled=30)[0], tm.scores(led, min_settled=30)[0]
    keys = ("rescaled_brier", "rescaled_baseline_brier", "rescaled_skill", "rescaled_t_by_week", "rescaled_settled")
    assert [a[k] for k in keys] == [b[k] for k in keys] and a["rescaled_skill"] > 0
    led.close()


def test_the_ranking_range_does_not_depend_on_the_order_the_rows_were_stored():
    rng = np.random.default_rng(8)
    outcomes = _mixed(100)
    noise = [float(rng.standard_normal()) for _ in outcomes]
    day0 = pd.Timestamp("2024-01-01")
    def fill(order):
        led = tm.TournamentLedger()
        for k in order:
            day = str((day0 + pd.Timedelta(days=7 * (k // 2))).date())
            led.add([tm.Question(f"q{k:03d}", "funds", "forward", day, 5, "A", "t", {}, 0.5)])
            led.record(f"q{k:03d}", "f", float(np.clip(0.5 + 0.1 * (2 * outcomes[k] - 1) + 0.3 * noise[k], 0.01, 0.99)), None, 1.0)
            led.settle(f"q{k:03d}", outcomes[k])
        return led
    forward, backward = fill(range(100)), fill(reversed(range(100)))
    a, b = tm.scores(forward, min_settled=30)[0], tm.scores(backward, min_settled=30)[0]
    assert (a["ranking"], a["ranking_low"], a["ranking_high"]) == (b["ranking"], b["ranking_low"], b["ranking_high"])
    forward.close(), backward.close()
