import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from valedia_trader.agents import panel as panel_mod
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
    # these blind questions (2024 on) all come after each test model's release, so the scores are evidence
    tm.ask(led, members, roster={"members": [{"name": name, "role": "decider", "model_label": f"test-{name}", "unseen_after": "2000-01-01"}
                                             for name in forecasters]})
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


def test_a_timid_but_informative_forecaster_is_rescaled_as_far_as_it_needs():
    from scipy.optimize import minimize
    from scipy.special import expit, logit
    n = 240
    outcomes = _mixed(n, seed=6)
    rng = np.random.default_rng(9)
    wrong = rng.random(n) < 0.2                                      # one forecast in five on the wrong side: not separable
    led = _filled({"timid": lambda y, k: 0.52 if (y == 1) != bool(wrong[k]) else 0.48}, outcomes)
    s = tm.scores(led, min_settled=30)[0]
    p, y = np.array([0.52 if (o == 1) != bool(w) else 0.48 for o, w in zip(outcomes, wrong)]), np.array(outcomes, dtype=float)
    early, late = slice(0, n // 2), slice(n // 2, n)                 # one question a week: the median date splits in half
    x = logit(np.clip(p[early], 0.01, 0.99))
    loss = lambda ab: float(np.mean(np.logaddexp(0.0, ab[0] + ab[1] * x) - y[early] * (ab[0] + ab[1] * x)))
    a, b = minimize(loss, x0=np.array([0.0, 1.0]), method="BFGS", options={"gtol": 1e-10}).x
    q = expit(a + b * logit(np.clip(p[late], 0.01, 0.99)))
    assert b > 10                                                    # a slope the old cap of 10 would have refused
    assert s["rescaled_skill"] == pytest.approx(1.0 - np.mean((q - y[late]) ** 2) / np.mean((0.5 - y[late]) ** 2), abs=5e-4)
    assert s["rescaled_skill"] > 0 and s["rescaled_settled"] == n // 2
    led.close()


# ---- asking only the day's forward questions ----------------------------------------------------------------------

TODAY = pd.Timestamp("2026-11-13")


def _backlog_ledger(blind=30, forward=3, extra=()):
    """`blind` old blind-history questions (oldest first by day) and `forward` questions asked today."""
    led = tm.TournamentLedger()
    day0 = pd.Timestamp("2024-01-01")
    led.add([tm.Question(f"b{k:03d}", "funds", "blind history", str((day0 + pd.Timedelta(days=k)).date()), 5, "AAA",
                         f"blind {k}", {"n": k}, 0.5) for k in range(blind)])
    led.add([tm.Question(f"f{k}", "funds", "forward", str(TODAY.date()), 5, f"S{k}", f"forward {k}", {"n": k}, 0.5)
             for k in range(forward)])
    led.add(list(extra))
    return led


def _recorder(fail=False):
    calls = []

    def fn(case):
        calls.append(case["thesis"])
        if fail:
            raise RuntimeError("model fell over")
        return 0.6, None, "ok"
    return calls, fn


def test_with_only_forward_a_forecaster_with_a_blind_backlog_is_asked_exactly_the_days_forward_questions():
    led = _backlog_ledger()
    calls, fn = _recorder()
    out = tm.ask(led, [CallableMember("m", "decider", fn)], today=TODAY, kinds=("forward",))
    assert out == {"m": {"asked": 3, "answered": 3}} and sorted(calls) == ["forward 0", "forward 1", "forward 2"]
    calls2, fn2 = _recorder()                                                   # without it: the backlog goes first, oldest first
    out2 = tm.ask(_backlog_ledger(), [CallableMember("m", "decider", fn2)], today=TODAY)
    assert out2 == {"m": {"asked": 33, "answered": 33}}
    assert calls2[:30] == [f"blind {k}" for k in range(30)] and calls2[30:] == ["forward 0", "forward 1", "forward 2"]
    calls3, fn3 = _recorder()                                                   # and a limit then never reaches the forward ones
    tm.ask(_backlog_ledger(), [CallableMember("m", "decider", fn3)], today=TODAY, limit=5)
    assert calls3 == [f"blind {k}" for k in range(5)]
    led.close()


def test_the_limit_counts_forward_questions_only_when_the_flag_is_given():
    led = _backlog_ledger(forward=5)
    calls, fn = _recorder()
    out = tm.ask(led, [CallableMember("m", "decider", fn)], today=TODAY, limit=2, kinds=("forward",))
    assert out == {"m": {"asked": 2, "answered": 2}} and calls == ["forward 0", "forward 1"]
    calls2, fn2 = _recorder()                                                   # a second run takes the next two
    tm.ask(led, [CallableMember("m", "decider", fn2)], today=TODAY, limit=2, kinds=("forward",))
    assert calls2 == ["forward 2", "forward 3"]
    led.close()


def test_a_stale_or_settled_forward_question_is_still_not_asked_with_the_flag():
    old = tm.Question("fold", "funds", "forward", "2026-11-05", 5, "OLD", "forward stale", {}, 0.5)
    done = tm.Question("fdone", "funds", "forward", str(TODAY.date()), 5, "DON", "forward settled", {}, 0.5)
    edge = tm.Question("fedge", "funds", "forward", "2026-11-10", 5, "EDG", "forward three days old", {}, 0.5)
    led = _backlog_ledger(extra=(old, done, edge))
    led.settle("fdone", 1)
    calls, fn = _recorder()
    out = tm.ask(led, [CallableMember("m", "decider", fn)], today=TODAY, max_age_days=3, kinds=("forward",))
    assert out == {"m": {"asked": 4, "answered": 4}}
    assert sorted(calls) == ["forward 0", "forward 1", "forward 2", "forward three days old"]
    led.close()


def test_a_failed_call_with_the_flag_is_recorded_and_asked_again_next_run():
    led = _backlog_ledger()
    calls, fn = _recorder(fail=True)
    out = tm.ask(led, [CallableMember("m", "decider", fn)], today=TODAY, kinds=("forward",))
    assert out == {"m": {"asked": 3, "answered": 0}} and led.counts()["failed_calls"] == {"m": 3}
    calls2, fn2 = _recorder()
    out2 = tm.ask(led, [CallableMember("m", "decider", fn2)], today=TODAY, kinds=("forward",))
    assert out2 == {"m": {"asked": 3, "answered": 3}} and led.counts()["answers"] == {"m": 3}
    assert tm.ask(led, [CallableMember("m", "decider", fn2)], today=TODAY, kinds=("forward",)) == {"m": {"asked": 0, "answered": 0}}
    led.close()


@pytest.mark.parametrize("kinds", [("nonsense",), ("forward", "blind histroy"), "forward,blind"])
def test_an_unknown_kind_raises_before_any_forecaster_is_asked(kinds):
    led = _backlog_ledger()
    calls, fn = _recorder()
    with pytest.raises(ValueError, match="blind histroy|nonsense|forward,blind"):
        tm.ask(led, [CallableMember("m", "decider", fn), CallableMember("n", "decider", fn)], today=TODAY, kinds=kinds)
    assert calls == [] and led.counts()["answers"] == {} and led.counts()["failed_calls"] == {}
    led.close()


def test_every_known_kind_and_both_together_are_accepted():
    for kinds, n in ((tm.KINDS, 33), (("blind history",), 30), ((), 0)):
        led = _backlog_ledger()
        calls, fn = _recorder()
        assert tm.ask(led, [CallableMember("m", "decider", fn)], today=TODAY, kinds=kinds) == {"m": {"asked": n, "answered": n}}
        led.close()


def _ask_as_it_was(ledger, members, today=None, max_age_days=3, limit=None):
    """The behaviour before `kinds` was added, kept here as the reference the unchanged path is held to."""
    out = {}
    for m in members:
        have = ledger.answered(m.name)
        todo = [q for q in ledger.questions() if q["id"] not in have]
        if today is not None:
            open_ids = {q["id"] for q in ledger.questions(unsettled_only=True)}
            fresh = lambda q: q["id"] in open_ids and (pd.Timestamp(today) - pd.Timestamp(q["asked_on"])).days <= max_age_days
            todo = [q for q in todo if q["kind"] != "forward" or fresh(q)]
        todo = todo[:limit] if limit else todo
        answered = 0
        for q in todo:
            v = m.ask({"thesis": q["thesis"], "facts": q["facts"]})
            ok = v.error is None and v.probability is not None
            ledger.record(q["id"], m.name, v.probability if ok else None, None if ok else (v.error or "no probability in the reply"), v.latency_ms)
            answered += int(ok)
        out[m.name] = {"asked": len(todo), "answered": answered}
    return out


def _random_ledger(seed):
    rng = np.random.default_rng(seed)
    led = tm.TournamentLedger()
    qs = []
    for k in range(int(rng.integers(0, 25))):
        kind = KINDS_FOR_TESTS[int(rng.integers(0, 2))]
        day = str((TODAY - pd.Timedelta(days=int(rng.integers(0, 12)) if kind == "forward" else int(rng.integers(0, 900)))).date())
        qs.append(tm.Question(f"q{k:03d}", "funds", kind, day, 5, "AAA", f"t{k}", {"k": k}, 0.5))
    led.add(qs)
    for q in qs:
        if rng.random() < 0.2:
            led.settle(q.id, int(rng.integers(0, 2)))
        for name in ("a", "b"):
            if rng.random() < 0.3:
                led.record(q.id, name, 0.5 if rng.random() < 0.8 else None, None if rng.random() < 0.8 else "failed", 1.0)
    return led


KINDS_FOR_TESTS = ("forward", "blind history")


def test_without_the_flag_the_asking_is_what_it_was_on_200_generated_ledgers():
    def run(fn, seed, **kw):
        led = _random_ledger(seed)
        calls = []

        def member(name):
            return CallableMember(name, "decider", lambda case, n=name: (calls.append((n, case["thesis"])) or (0.55, None, "ok")))
        out = fn(led, [member("a"), member("b"), member("c")], **kw)
        rows = led._db.execute("SELECT question_id, forecaster, probability, error FROM forecasts ORDER BY question_id, forecaster").fetchall()
        led.close()
        return json.dumps([out, calls, [tuple(r) for r in rows]], sort_keys=True)
    rng = np.random.default_rng(20261114)
    for seed in range(200):
        kw = {"today": [None, TODAY][int(rng.integers(0, 2))], "max_age_days": int(rng.integers(0, 6)),
              "limit": [None, 0, 1, 3, 7][int(rng.integers(0, 5))]}
        assert run(tm.ask, seed, **kw) == run(_ask_as_it_was, seed, **kw), (seed, kw)
        assert run(lambda *a, **k: tm.ask(*a, **k, kinds=None), seed, **kw) == run(_ask_as_it_was, seed, **kw)


def _forward_cli(tmp_path, monkeypatch, capsys, *flags):
    folder = _price_folder(tmp_path, end=pd.Timestamp.now().normalize())
    ledger_path = tmp_path / "t.sqlite"
    led = tm.TournamentLedger(ledger_path)                                      # a blind backlog already on file
    led.add([tm.Question(f"b{k:03d}", "funds", "blind history", f"2024-02-{k + 1:02d}", 5, "AAA", f"blind {k}", {"n": k}, 0.5)
             for k in range(20)])
    led.close()
    calls, fn = _recorder()
    monkeypatch.setattr(tm, "forecasters", lambda names=None, roster=None: [CallableMember("m", "decider", fn)])
    args = ["--root", str(tmp_path), "--ledger", str(ledger_path), "ask-forward", "--data", str(folder), "--market", "funds", *flags]
    assert tm.main(args) == 0
    return calls, json.loads(capsys.readouterr().out)


def test_ask_forward_with_only_forward_asks_just_the_forward_questions_and_adds_one_key(tmp_path, monkeypatch, capsys):
    calls, out = _forward_cli(tmp_path, monkeypatch, capsys, "--only-forward", "--limit", "2")
    assert out["only_forward"] is True and out["asked"] == {"m": {"asked": 2, "answered": 2}}
    assert len(calls) == 2 and not any(t.startswith("blind") for t in calls)
    assert list(out) == ["questions_added", "settled_now", "asked", "counts", "only_forward"]


def test_ask_forward_without_the_flag_prints_what_it_printed_and_asks_the_backlog_first(tmp_path, monkeypatch, capsys):
    calls, out = _forward_cli(tmp_path, monkeypatch, capsys, "--limit", "2")
    assert list(out) == ["questions_added", "settled_now", "asked", "counts"] and "only_forward" not in out
    assert calls == ["blind 0", "blind 1"]


def test_only_forward_is_not_an_option_of_ask_blind(tmp_path):
    with pytest.raises(SystemExit):
        tm.main(["--root", str(tmp_path), "ask-blind", "--data", "x", "--market", "funds", "--only-forward"])


# ---- which model answered, and whether it may remember the dates it was asked about --------------------------------

OLD_SCHEMA = (                                     # the tables exactly as the tournament created them before T40-A1
    "CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, market TEXT, kind TEXT, asked_on TEXT, horizon INTEGER, "
    "symbol TEXT, thesis TEXT, facts TEXT, baseline REAL);"
    "CREATE TABLE IF NOT EXISTS forecasts (question_id TEXT, forecaster TEXT, probability REAL, error TEXT, latency_ms REAL, "
    "ts REAL, PRIMARY KEY (question_id, forecaster));"
    "CREATE TABLE IF NOT EXISTS outcomes (question_id TEXT PRIMARY KEY, outcome INTEGER, settled_ts REAL);")
PROMPT_SHA = hashlib.sha1(panel_mod.PROMPT.encode()).hexdigest()[:12]
NOT_EVIDENCE = "not evidence: the model may remember these dates"
SKILL_WORDS = ("beats", "better than")


def _old_file(path):
    """An old-schema tournament file: blind and forward questions, a model and a rule forecaster, all settled."""
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    day0 = pd.Timestamp("2024-01-01")
    for k in range(12):
        kind = "forward" if k % 3 == 0 else "blind history"
        db.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)",
                   (f"q{k}", "funds", kind, str((day0 + pd.Timedelta(days=7 * k)).date()), 5, "AAA", "t", "{}", 0.5))
        for who, p in (("old-model", 0.7), ("rule-momentum", 0.6)):
            db.execute("INSERT INTO forecasts VALUES (?,?,?,?,?,?)", (f"q{k}", who, p, None, 1.0, 1700000000.0 + k))
        db.execute("INSERT INTO outcomes VALUES (?,?,?)", (f"q{k}", k % 2, 1700000000.0 + k))
    db.commit()
    db.close()


def _columns(path):
    db = sqlite3.connect(path)
    try:
        return [r[1] for r in db.execute("PRAGMA table_info(forecasts)")], db.execute("PRAGMA schema_version").fetchone()[0]
    finally:
        db.close()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_an_old_file_gains_the_three_columns_once_on_a_writable_open(tmp_path):
    path = tmp_path / "t.sqlite"
    _old_file(path)
    before, _ = _columns(path)
    assert before == ["question_id", "forecaster", "probability", "error", "latency_ms", "ts"]
    tm.TournamentLedger(path).close()
    after, version = _columns(path)
    assert after == before + ["model", "unseen_after", "prompt_sha"]
    digest = _sha(path)
    led = tm.TournamentLedger(path)                                   # a second open changes nothing
    rows = led._db.execute("SELECT * FROM forecasts ORDER BY question_id, forecaster").fetchall()
    led.close()
    assert _columns(path) == (after, version) and _sha(path) == digest
    assert len(rows) == 24 and all(r["model"] is None and r["unseen_after"] is None and r["prompt_sha"] is None for r in rows)
    assert {(r["forecaster"], r["probability"]) for r in rows} == {("old-model", 0.7), ("rule-momentum", 0.6)}


def test_a_read_only_open_of_an_old_file_is_scored_without_a_schema_change(tmp_path):
    path = tmp_path / "t.sqlite"
    _old_file(path)
    digest, files = _sha(path), sorted(p.name for p in tmp_path.iterdir())
    led = object.__new__(tm.TournamentLedger)                           # as the owner page opens it
    led._db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    led._db.row_factory = sqlite3.Row
    try:
        out = tm.scores(led, min_settled=1)
        counts = led.counts()
    finally:
        led._db.close()
    assert _sha(path) == digest and sorted(p.name for p in tmp_path.iterdir()) == files
    assert counts["questions"] == 12 and counts["answers"] == {"old-model": 12, "rule-momentum": 12}
    got = {(r["forecaster"], r["kind"]): (r["model"], r["prompt_sha"], r["memory"], r["settled"]) for r in out}
    assert got == {("old-model", "forward"): (None, None, "asked live", 4), ("old-model", "blind history"): (None, None, "may be remembered", 8),
                   ("rule-momentum", "forward"): (None, None, "asked live", 4),
                   ("rule-momentum", "blind history"): (None, None, "may be remembered", 8)}   # no model recorded: not "no memory"
    assert all(r["verdict"] == NOT_EVIDENCE for r in out if r["memory"] == "may be remembered")


@pytest.mark.parametrize("kind, model, unseen_after, asked_on, memory", [
    ("forward", "m", None, "2025-07-01", "asked live"),
    ("forward", None, None, "2020-01-06", "asked live"),
    ("blind history", "rule", None, "2020-01-06", "no memory"),
    ("blind history", "m", "2025-06-30", "2025-07-01", "after release"),
    ("blind history", "m", "2025-06-30", "2025-06-30", "may be remembered"),     # the release day itself is not after it
    ("blind history", "m", "2025-06-30", "2025-06-27", "may be remembered"),
    ("blind history", "m", None, "2025-07-01", "may be remembered"),
    ("blind history", None, None, "2025-07-01", "may be remembered"),
    ("blind history", None, "2025-06-30", "2025-07-01", "after release"),
    ("blind history", "m", "not a date", "2025-07-01", "may be remembered"),
])
def test_each_scored_row_has_the_memory_class_the_rules_give(kind, model, unseen_after, asked_on, memory):
    led = tm.TournamentLedger()
    led.add([tm.Question("q", "funds", kind, asked_on, 5, "AAA", "t", {}, 0.5)])
    led.record("q", "who", 0.7, None, 1.0, model=model, unseen_after=unseen_after, prompt_sha=PROMPT_SHA)
    led.settle("q", 1)
    (row,) = tm.scores(led, min_settled=1)
    led.close()
    assert (row["model"], row["prompt_sha"], row["memory"]) == (model, PROMPT_SHA, memory)
    assert (row["verdict"] == NOT_EVIDENCE) == (memory == "may be remembered")


def _expected_memory(kind, model, unseen_after, asked_on):
    """The memory rules written out again here, independently of the code under test."""
    if kind == "forward":
        return "asked live"
    if model == "rule":
        return "no memory"
    if unseen_after is not None and pd.Timestamp(asked_on).date() > pd.Timestamp(unseen_after).date():
        return "after release"
    return "may be remembered"


def _pooling_ledger(module, seed):
    """Two models under one forecaster name, two prompts, questions on both sides of (and on) the release day,
    two markets, both kinds, a rule, failed calls and unsettled questions."""
    rng = np.random.default_rng(seed)
    led = module.TournamentLedger()
    release = "2024-07-01"
    for k in range(int(rng.integers(30, 120))):
        day = release if rng.random() < 0.1 else str((pd.Timestamp("2024-01-01") + pd.Timedelta(days=int(rng.integers(0, 366)))).date())
        led.add([module.Question(f"q{k:03d}", ["funds", "stocks"][int(rng.integers(0, 2))], KINDS_FOR_TESTS[int(rng.integers(0, 2))], day,
                                 5, "AAA", "t", {}, float(rng.uniform(0.3, 0.7)))])
        for who in ("x", "y", "rule-momentum"):
            if rng.random() < 0.15:
                continue
            if who == "rule-momentum":
                model, unseen, sha = "rule", None, PROMPT_SHA
            else:
                model = [f"{who}-model-a", f"{who}-model-b", None][int(rng.integers(0, 3))]
                unseen = [release, None][int(rng.integers(0, 2))]
                sha = ["aaaaaaaaaaaa", "bbbbbbbbbbbb", None][int(rng.integers(0, 3))]
            failed = rng.random() < 0.1
            led.record(f"q{k:03d}", who, None if failed else float(rng.uniform(0, 1)), "failed" if failed else None, 1.0,
                       model=model, unseen_after=unseen, prompt_sha=sha)
        if rng.random() < 0.85:
            led.settle(f"q{k:03d}", int(rng.integers(0, 2)))
    return led


def pooling_violations(module, seeds=range(60)) -> list:
    """Every way `module.scores` pools rows it must keep apart, over seeded random ledgers (empty when none)."""
    bad = []
    for seed in seeds:
        led = _pooling_ledger(module, seed)
        raw = led._db.execute(
            "SELECT f.forecaster, f.model, f.unseen_after, f.prompt_sha, q.market, q.kind, q.asked_on FROM forecasts f "
            "JOIN questions q ON q.id = f.question_id JOIN outcomes o ON o.question_id = q.id "
            "WHERE f.error IS NULL AND f.probability IS NOT NULL").fetchall()
        want = Counter((r[0], r[1], r[3], r[4], r[5], _expected_memory(r[5], r[1], r[2], r[6])) for r in raw)
        out = module.scores(led, min_settled=1)
        led.close()
        got = Counter()
        for r in out:
            key = (r["forecaster"], r.get("model"), r.get("prompt_sha"), r["market"], r["kind"], r.get("memory"))
            got[key] += r["settled"]
            if r["settled"] != want.get(key):
                bad.append((seed, key, r["settled"], want.get(key)))
        if got != want or sum(r["settled"] for r in out) != len(raw) or len(out) != len(want):
            bad.append((seed, "totals", sum(r["settled"] for r in out), len(raw)))
    return bad


def test_no_row_of_the_scores_pools_two_models_two_prompts_or_two_memory_classes():
    assert pooling_violations(tm) == []


def test_the_scores_come_out_in_the_same_order_whatever_order_the_rows_were_stored():
    led = _pooling_ledger(tm, 7)
    first = json.dumps(tm.scores(led, min_settled=1), sort_keys=True)
    rows = led._db.execute("SELECT * FROM forecasts").fetchall()
    led._db.execute("DELETE FROM forecasts")
    for r in reversed(rows):
        led._db.execute("INSERT INTO forecasts (question_id, forecaster, probability, error, latency_ms, ts, model, unseen_after, prompt_sha) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", tuple(r[k] for k in ("question_id", "forecaster", "probability", "error",
                                                                            "latency_ms", "ts", "model", "unseen_after", "prompt_sha")))
    again = json.dumps(tm.scores(led, min_settled=1), sort_keys=True)
    led.close()
    assert first == again and '"memory"' in first


@pytest.mark.parametrize("n", [5, 40, 120])
def test_a_perfect_forecaster_that_may_remember_the_dates_is_never_reported_as_skill(n):
    outcomes = _mixed(n, seed=n)
    def filled(unseen_after):
        led = tm.TournamentLedger()
        day0 = pd.Timestamp("2024-01-01")
        for k, y in enumerate(outcomes):
            led.add([tm.Question(f"q{k}", "funds", "blind history", str((day0 + pd.Timedelta(days=7 * k)).date()), 5, "AAA", "t", {}, 0.5)])
            led.record(f"q{k}", "oracle", float(y), None, 1.0, model="m", unseen_after=unseen_after, prompt_sha=PROMPT_SHA)
            led.settle(f"q{k}", y)
        (row,) = tm.scores(led, min_settled=30)
        led.close()
        return row
    remembered = filled(None)
    assert remembered["memory"] == "may be remembered" and remembered["verdict"] == NOT_EVIDENCE
    assert not any(w in remembered["verdict"] + remembered["ranking_verdict"] for w in SKILL_WORDS)
    assert remembered["skill"] == pytest.approx(1.0) and remembered["brier"] == 0.0 and remembered["settled"] == n
    unseen = filled("2023-12-31")                                       # the same answers, all after release
    assert unseen["memory"] == "after release" and unseen["skill"] == remembered["skill"] and unseen["t_by_week"] == remembered["t_by_week"]
    if n >= 30:
        assert unseen["verdict"] == "beats the baseline" and unseen["ranking_verdict"] == "ranks better than chance"


def test_ask_records_the_rosters_model_and_date_and_rule_for_a_rule():
    roster = {"members": [
        {"name": "chatty", "role": "decider", "backend": "chat", "base_url": "http://127.0.0.1:9/v1", "model": "qwen3:8b",
         "model_label": None, "unseen_after": None, "unseen_after_source": None},
        {"name": "labelled", "role": "decider", "backend": "chat", "base_url": "http://127.0.0.1:9/v1", "model": "qwen3:8b",
         "model_label": "qwen3:8b@500a1f067a9f", "unseen_after": "2025-04-29", "unseen_after_source": "a release note"},
        {"name": "profiled", "role": "decider", "backend": "hermes", "profile": "p", "model_label": None, "unseen_after": None,
         "unseen_after_source": None}]}
    led = tm.TournamentLedger()
    led.add([tm.Question("q", "funds", "blind history", "2025-07-01", 5, "AAA", "t", {}, 0.5)])
    members = [CallableMember(n, "decider", lambda case: (0.6, None, "")) for n in ("chatty", "labelled", "profiled", "stranger")]
    tm.ask(led, members + tm.reference_forecasters(), roster=roster)
    got = {r[0]: tuple(r[1:]) for r in led._db.execute("SELECT forecaster, model, unseen_after, prompt_sha FROM forecasts")}
    led.settle("q", 1)
    memory = {r["forecaster"]: r["memory"] for r in tm.scores(led, min_settled=1)}
    led.close()
    assert got == {"chatty": ("qwen3:8b", None, PROMPT_SHA), "labelled": ("qwen3:8b@500a1f067a9f", "2025-04-29", PROMPT_SHA),
                   "profiled": (None, None, PROMPT_SHA), "stranger": (None, None, PROMPT_SHA),
                   "rule-momentum": ("rule", None, PROMPT_SHA), "rule-above-200d": ("rule", None, PROMPT_SHA)}
    assert memory == {"chatty": "may be remembered", "labelled": "after release", "profiled": "may be remembered",
                      "stranger": "may be remembered", "rule-momentum": "no memory", "rule-above-200d": "no memory"}


def test_ask_without_a_roster_reads_the_panels_own_roster():
    led = tm.TournamentLedger()
    led.add([tm.Question("q", "funds", "forward", "2025-07-01", 5, "AAA", "t", {}, 0.5)])
    spec = {s["name"]: s for s in panel_mod.load_roster()["members"]}["qwen3-8b"]
    tm.ask(led, [CallableMember("qwen3-8b", "decider", lambda case: (0.6, None, ""))])
    got = tuple(led._db.execute("SELECT model, unseen_after, prompt_sha FROM forecasts").fetchone())
    led.close()
    assert got == (spec.get("model_label") or spec["model"], spec.get("unseen_after"), PROMPT_SHA)


def test_the_roster_carries_the_three_fields_unset_and_still_builds_a_panel():
    roster = panel_mod.load_roster()
    for spec in roster["members"]:
        assert {"model_label", "unseen_after", "unseen_after_source"} <= set(spec), spec["name"]
        assert spec["unseen_after"] is None and spec["unseen_after_source"] is None     # no date is guessed here
    assert "unseen_after" in roster["notes"] and "model_label" in roster["notes"]
    assert len(panel_mod.build_panel(roster).members) == sum(1 for s in roster["members"] if s.get("enabled", True))
    assert [m.name for m in tm.forecasters(roster=roster)] == [s["name"] for s in roster["members"]
                                                               if s["role"] == "decider" and s.get("enabled", True)]


def test_the_price_models_view_of_the_ledger_still_records_the_rules(tmp_path):
    from valedia_trader.research_loop.price_models import _Only
    led = tm.TournamentLedger(tmp_path / "t.sqlite")
    led.add([tm.Question(f"q{k}", "funds", "blind history", "2025-07-01", 5, "AAA", "t", {}, 0.5) for k in range(3)])
    out = tm.ask(_Only(led, {"q0", "q2"}), tm.reference_forecasters())
    rows = led._db.execute("SELECT question_id, forecaster, model, prompt_sha FROM forecasts ORDER BY 1, 2").fetchall()
    led.close()
    assert out == {"rule-momentum": {"asked": 2, "answered": 2}, "rule-above-200d": {"asked": 2, "answered": 2}}
    assert [tuple(r) for r in rows] == [(q, w, "rule", PROMPT_SHA) for q in ("q0", "q2") for w in ("rule-above-200d", "rule-momentum")]
