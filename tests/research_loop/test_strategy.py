import json

import numpy as np
import pandas as pd
import pytest

from valedia_trader.quant_engine.level10_ml_research import detect_lookahead
from valedia_trader.research_loop import strategy as st


def frame(**cols):
    n = len(next(iter(cols.values())))
    return pd.DataFrame({k: np.asarray(v, dtype=float) for k, v in cols.items()}, index=pd.bdate_range("2024-01-01", periods=n))


def spec(**over):
    base = {"name": "mom_test", "version": "1.0.0", "hypothesis": "Assets that rose keep rising for a while",
            "market": ["A", "B"], "entry": {"rule": "momentum", "lookback": 5, "enter": 0.05, "exit": 0.0},
            "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.2, "max_open_positions": 2, "stop_loss_pct": None}}
    base.update(over)
    return base


def test_entry_and_exit_fire_exactly_on_the_threshold_bars():
    # A: flat, then +6% over 5 days by bar 7 (entry), then decays below its level 5 days earlier by bar 14 (exit)
    a = [100, 100, 100, 100, 100, 101, 103, 106, 107, 108, 108, 108, 107, 106, 105, 105]
    s = st.RuleStrategy.from_spec(spec(market=["A"]))
    px = frame(A=a)
    w, events = st.run_rules(s, px)
    mom = px["A"] / px["A"].shift(5) - 1
    first_entry = int(np.argmax((mom >= 0.05).to_numpy()))
    assert [e["action"] for e in events] == ["enter", "exit"]
    assert events[0]["date"] == px.index[first_entry] and first_entry == 7
    held_until = int(np.argmax(((mom <= 0.0) & (np.arange(len(a)) > first_entry)).to_numpy()))
    assert events[1]["date"] == px.index[held_until] and events[1]["reason"] == "exit rule"
    assert (w["A"].iloc[:first_entry] == 0).all() and (w["A"].iloc[first_entry:held_until] == 0.2).all()
    assert (w["A"].iloc[held_until:] == 0).all()


def test_stop_loss_take_profit_and_time_limit_close_positions():
    up_then_crash = [100] * 5 + [106, 107, 100, 95, 94, 93, 92]
    risky = st.RuleStrategy.from_spec(spec(market=["A"], risk={"max_position_pct": 0.2, "stop_loss_pct": 0.05}))
    _, ev = st.run_rules(risky, frame(A=up_then_crash))
    assert ev[0]["action"] == "enter" and ev[1]["reason"].startswith("stop loss") and ev[1]["price"] == 100.0
    rocket = [100] * 5 + [106, 110, 118, 119, 120]
    tp = st.RuleStrategy.from_spec(spec(market=["A"], risk={"max_position_pct": 0.2, "stop_loss_pct": None, "take_profit_pct": 0.10}))
    _, ev = st.run_rules(tp, frame(A=rocket))
    assert ev[1]["reason"].startswith("take profit") and ev[1]["price"] == 118.0
    timed = st.RuleStrategy.from_spec(spec(market=["A"], risk={"max_position_pct": 0.2, "stop_loss_pct": None, "max_holding_days": 2}))
    w, ev = st.run_rules(timed, frame(A=[100] * 5 + [106, 107, 108, 109, 110, 111]))
    assert ev[1]["reason"] == "time limit: held 2 days" and (ev[1]["date"] - ev[0]["date"]).days == 2
    assert w["A"].iloc[7] == 0.0                                  # closed on the bar: not reopened the same bar


def test_slots_go_to_the_strongest_signals_and_gross_is_capped():
    base = [100] * 5
    px = frame(A=base + [106, 107, 108], B=base + [110, 111, 112], C=base + [108, 109, 110])
    s = st.RuleStrategy.from_spec(spec(market=["A", "B", "C"], risk={"max_position_pct": 0.6, "max_open_positions": 2,
                                                                     "stop_loss_pct": None, "max_gross": 1.0}))
    w, ev = st.run_rules(s, px)
    assert {e["symbol"] for e in ev if e["action"] == "enter"} == {"B", "C"}       # the two largest moves
    assert w["A"].abs().sum() == 0 and w.iloc[-1].abs().sum() == pytest.approx(1.0)    # 2 x 0.6 scaled to gross 1


def test_shorts_mirror_longs_and_crypto_is_never_shorted():
    fall = [100] * 5 + [94, 93, 92, 96, 100, 101]
    both = spec(market=["A", "BTC-USD"], entry={"rule": "momentum", "lookback": 5, "enter": 0.05, "exit": 0.0, "direction": "both"})
    w, ev = st.run_rules(st.RuleStrategy.from_spec(both), frame(**{"A": fall, "BTC-USD": fall}))
    assert [(e["symbol"], e["action"], e["side"]) for e in ev][:1] == [("A", "enter", -1)]
    assert (w["BTC-USD"] >= 0).all() and (w["BTC-USD"].iloc[:10] == 0).all() and w["A"].min() == -0.2
    assert not any(e["symbol"] == "BTC-USD" and e["side"] < 0 for e in ev)
    assert any(e["action"] == "exit" and e["side"] == -1 for e in ev)                  # covered once momentum turned


def test_inverse_vol_sizing_and_missing_prices():
    rng = np.random.default_rng(0)
    calm = 100 * np.exp(np.cumsum(0.004 + 0.002 * rng.standard_normal(80)))
    wild = 100 * np.exp(np.cumsum(0.004 + 0.02 * rng.standard_normal(80)))
    px = frame(A=calm, B=wild)
    px.iloc[60:63, 0] = np.nan                                     # a data gap while the position is open
    s = st.RuleStrategy.from_spec(spec(sizing={"rule": "inverse_vol", "vol_window": 20, "position_vol": 0.02},
                                       entry={"rule": "momentum", "lookback": 20, "enter": 0.02, "exit": -0.5},
                                       risk={"max_position_pct": 0.9, "stop_loss_pct": None}))
    w, ev = st.run_rules(s, px)
    opened = {e["symbol"]: e["weight"] for e in ev if e["action"] == "enter"}
    assert opened["A"] > opened["B"] > 0                           # the calmer asset gets the larger position
    assert w["A"].iloc[60:63].eq(w["A"].iloc[59]).all()             # carried through the gap


def test_rule_strategies_are_causal():
    rng = np.random.default_rng(3)
    px = pd.DataFrame(100 * np.exp(np.cumsum(0.0004 + 0.012 * rng.standard_normal((500, 4)), axis=0)),
                      index=pd.bdate_range("2020-01-01", periods=500), columns=list("ABCD"))
    for rule, params in (("momentum", {"lookback": 60, "enter": 0.03, "exit": 0.0}), ("breakout", {"lookback": 40, "enter": 1.0, "exit": 0.0}),
                         ("reversion", {"lookback": 5, "enter": 1.5, "exit": 0.0}), ("ma_cross", {"fast": 20, "slow": 80, "enter": 0.0, "exit": 0.0}),
                         ("drift", {"window": 120, "enter": 0.1, "exit": 0.0})):
        s = st.RuleStrategy.from_spec(spec(market=list("ABCD"), entry={"rule": rule, "direction": "both", **params}))
        assert detect_lookahead(s.target_weights, px, n_checks=5) == [], rule
        w = s.target_weights(px)
        assert w.abs().sum(axis=1).max() <= 1.0 + 1e-12 and w.abs().to_numpy().sum() > 0, rule


@pytest.mark.parametrize("bad", [
    {"name": "X"}, {"version": "1.0"}, {"hypothesis": "too short"}, {"market": []}, {"extra": 1},
    {"entry": {"rule": "astrology", "enter": 1, "exit": 0}}, {"entry": {"rule": "momentum", "enter": 0.1}},
    {"entry": {"rule": "momentum", "enter": 0.0, "exit": 0.1}}, {"entry": {"rule": "momentum", "lookback": 1, "enter": 0.1, "exit": 0}},
    {"entry": {"rule": "momentum", "window": 10, "enter": 0.1, "exit": 0}}, {"entry": {"rule": "momentum", "enter": True, "exit": 0}},
    {"entry": {"rule": "momentum", "enter": 0.1, "exit": -0.5, "direction": "both"}},
    {"entry": {"rule": "momentum", "enter": 0.1, "exit": 0, "direction": "sideways"}},
    {"sizing": {"rule": "martingale"}}, {"sizing": {"rule": "inverse_vol", "position_vol": 5}},
    {"risk": {"max_position_pct": 1.5}}, {"risk": {"stop_loss_pct": 0}}, {"risk": {"leverage": 10}}, {"entry": "buy low"},
])
def test_bad_specs_are_refused(bad):
    with pytest.raises(st.SpecError):
        st.RuleStrategy.from_spec(spec(**bad))


def test_files_round_trip_and_python_strategies_must_declare_version_and_hypothesis(tmp_path):
    s = st.RuleStrategy.from_spec(spec())
    path = st.save_spec(s, tmp_path)
    assert path.name == "mom_test.json" and st.load_strategy(path).to_spec() == s.to_spec()
    assert json.loads(path.read_text())["version"] == "1.0.0" and st.bump_version("1.2.3") == "1.3.0"
    (tmp_path / "broken.json").write_text("{not json")
    with pytest.raises(st.SpecError):
        st.load_strategy(tmp_path / "broken.json")
    body = '''"""hand_written
version: 2.1.0
hypothesis: A hand written strategy still follows the common shape
"""
import pandas as pd
from valedia_trader.research_loop.strategy import Strategy, StrategyRisk


class Hand(Strategy):
    name, version, market, risk = "hand_written", "2.1.0", ("A",), StrategyRisk()
    hypothesis = "A hand written strategy still follows the common shape"

    def entry(self, prices):
        return (prices > prices.shift(1)).astype(int)

    def exit(self, prices, side):
        return prices < prices.shift(1)

    def size(self, prices):
        return pd.DataFrame(0.1, index=prices.index, columns=prices.columns)


STRATEGY = Hand()
'''
    (tmp_path / "hand_written.py").write_text(body)
    loaded = st.load_strategy(tmp_path / "hand_written.py")
    assert loaded.describe()["version"] == "2.1.0"
    w = loaded.target_weights(frame(A=[1, 2, 3, 2, 3]))
    assert w["A"].tolist() == [0.0, 0.1, 0.1, 0.0, 0.1]
    (tmp_path / "undeclared.py").write_text(body.replace("version: 2.1.0", "version: 9.9.9"))
    with pytest.raises(st.SpecError, match="docstring"):
        st.load_strategy(tmp_path / "undeclared.py")
    (tmp_path / "empty.py").write_text('"""x"""\n')
    with pytest.raises(st.SpecError, match="STRATEGY"):
        st.load_strategy(tmp_path / "empty.py")
    with pytest.raises(st.SpecError):
        st.load_strategy(tmp_path / "notes.txt")
    with pytest.raises(st.SpecError, match="price data"):
        st.run_rules(s, frame(Z=[1, 2, 3]))


def test_trend_blend_reads_trends_at_several_speeds_without_looking_ahead():
    rng = np.random.default_rng(8); n = 900
    idx = pd.bdate_range("2020-01-01", periods=n)
    up = 100 * np.exp(np.cumsum(0.0015 + 0.008 * rng.standard_normal(n)))
    down = 100 * np.exp(np.cumsum(-0.0015 + 0.008 * rng.standard_normal(n)))
    flat = 100 * np.exp(np.cumsum(0.008 * rng.standard_normal(n)))
    px = pd.DataFrame({"UP": up, "DOWN": down, "FLAT": flat}, index=idx)
    score = st.SCORES["trend_blend"](px, fast=8, speeds=3)
    tail = score.iloc[-200:]
    assert tail["UP"].mean() > 0.5 and tail["DOWN"].mean() < -0.5 and abs(tail["FLAT"].mean()) < tail["UP"].mean()
    assert score.abs().max().max() <= 2.0 + 1e-9 and score.iloc[:100].isna().all().all()      # capped; silent until it has history
    cut = st.SCORES["trend_blend"](px.iloc[:600], fast=8, speeds=3)
    pd.testing.assert_frame_equal(cut, score.iloc[:600])                                    # later prices change nothing earlier
    s = st.RuleStrategy.from_spec({"name": "blend_test", "version": "1.0.0", "hypothesis": "Trends at several speeds persist",
                                   "market": ["UP", "DOWN", "FLAT"], "entry": {"rule": "trend_blend", "fast": 8, "speeds": 3, "enter": 0.5, "exit": 0.0},
                                   "sizing": {"rule": "inverse_vol", "position_vol": 0.05}, "risk": {"max_position_pct": 0.5, "max_open_positions": 3, "stop_loss_pct": None}})
    w, events = st.run_rules(s, px)
    held = (w.iloc[200:] > 0).mean()
    assert held["UP"] > 0.5 > held["FLAT"] and held["DOWN"] < 0.1 and any(e["action"] == "enter" for e in events)
    assert (w >= 0).all().all()                                                             # long-only by default
    with pytest.raises(st.SpecError):
        st.RuleStrategy.from_spec({**s.to_spec(), "entry": {"rule": "trend_blend", "speeds": 9, "enter": 0.5, "exit": 0.0}})


# ---- the calendar rule

def nyse_frame(first, last, closed=(), **cols):
    """Prices on weekdays from `first` to `last` except the exchange holidays in `closed`."""
    days = pd.bdate_range(first, last).difference(pd.DatetimeIndex(list(closed)))
    rng = np.random.default_rng(3)
    return pd.DataFrame({k: 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days)))) for k in (cols or {"A": 0})}, index=days)


def tom_spec(**entry):
    return spec(name="tom_test", hypothesis="Shares rise around the turn of the month", market=["A", "B"],
                entry={"rule": "turn_of_month", "enter": 1, "exit": 0, **entry})


def test_turn_of_month_marks_the_close_before_each_session_of_the_window():
    # New York sessions: 2 Jan is the first of January (1 Jan is a holiday), 31 Jan the last, 1, 2 and 5 Feb the
    # first three of February. Row t is read at the close of t and earns the next session's return, so the
    # mark sits one session before each window day.
    px = nyse_frame("2023-12-27", "2024-02-08", closed=["2024-01-01", "2024-01-15"], A=0, B=0)
    score = st.SCORES["turn_of_month"](px, before=1, after=3)
    marked = {str(d.date()) for d in score.index[score["A"] == 1.0]}
    assert marked == {"2023-12-28", "2023-12-29", "2024-01-02", "2024-01-03",
                      "2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"}
    assert set(np.unique(score.to_numpy())) == {0.0, 1.0} and score["A"].equals(score["B"])


def test_turn_of_month_reads_the_exchange_calendar_past_the_last_row_of_data():
    # 29 March 2024 is Good Friday: Thursday the 28th is the last session of March, and a rule that only
    # counted weekdays, or only looked at the rows it was given, would not know it on Wednesday's close.
    px = nyse_frame("2024-03-11", "2024-03-27")
    score = st.SCORES["turn_of_month"](px, before=1, after=3)["A"]
    assert score.iloc[-1] == 1.0 and score.iloc[:-1].sum() == 0.0
    # the same close without the window before the month end: nothing to hold
    assert st.SCORES["turn_of_month"](px, before=0, after=3)["A"].sum() == 0.0


def test_turn_of_month_holds_from_the_close_before_the_window_to_its_last_close():
    px = nyse_frame("2024-01-08", "2024-02-08", closed=["2024-01-15"], A=0, B=0)
    s = st.RuleStrategy.from_spec(tom_spec(before=1, after=3) | {"risk": {"max_position_pct": 0.5, "max_open_positions": 2, "stop_loss_pct": None}})
    w, events = st.run_rules(s, px)
    held = [str(d.date()) for d in w.index[w["A"] > 0]]
    assert held == ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"]          # earns 31 Jan and 1, 2, 5 Feb
    assert w.loc["2024-01-30", ["A", "B"]].tolist() == [0.5, 0.5]
    assert [(e["action"], str(e["date"].date())) for e in events if e["symbol"] == "A"] == [("enter", "2024-01-30"), ("exit", "2024-02-05")]


def test_turn_of_month_ignores_prices_and_refuses_dates_off_the_calendar():
    a = nyse_frame("2024-01-08", "2024-02-08", closed=["2024-01-15"])
    b = a * np.linspace(3.0, 0.2, len(a))[:, None]
    assert st.SCORES["turn_of_month"](a).equals(st.SCORES["turn_of_month"](b))
    s = st.RuleStrategy.from_spec(tom_spec() | {"market": ["A"]})
    assert detect_lookahead(s.target_weights, nyse_frame("2023-10-02", "2024-03-27"), n_checks=5) == []
    with pytest.raises(st.SpecError, match="calendar"):
        st.SCORES["turn_of_month"](nyse_frame("1985-01-02", "1985-03-01"))


@pytest.mark.parametrize("entry", [{"before": 11}, {"after": -1}, {"before": True}, {"window": 3}])
def test_turn_of_month_parameters_are_checked(entry):
    with pytest.raises(st.SpecError):
        st.RuleStrategy.from_spec(tom_spec(**entry))


def test_research_agents_are_told_about_the_calendar_rule():
    from valedia_trader.research_loop import research
    assert "turn_of_month" in research.RULE_LIBRARY and "before in [0, 10]" in research.RULE_LIBRARY


# ---- holding without timing, and resizing a held position

def hold_spec(sizing=None, **risk):
    return spec(name="hold_test", hypothesis="The funds earn their return for being held", market=["A", "B"],
                entry={"rule": "hold", "enter": 1, "exit": 0}, sizing=sizing or {"rule": "equal"},
                risk={"max_position_pct": 0.5, "max_open_positions": 2, "stop_loss_pct": None, **risk})


def calm_then_wild(n=60, turn=30):
    """A: half a percent a day up and down, then three percent. B: the same the other way round."""
    step = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    a = 100.0 * np.cumprod(1.0 + step * np.where(np.arange(n) < turn, 0.005, 0.03))
    b = 100.0 * np.cumprod(1.0 + step * np.where(np.arange(n) < turn, 0.03, 0.005))
    return frame(A=a, B=b)


def test_hold_enters_every_fund_at_once_and_never_leaves():
    px = frame(A=np.linspace(100, 60, 40), B=np.linspace(100, 180, 40))
    w, events = st.run_rules(st.RuleStrategy.from_spec(hold_spec()), px)
    assert (w[["A", "B"]] == 0.5).all().all()                       # a falling fund is held like a rising one
    assert [(e["action"], e["symbol"]) for e in events] == [("enter", "A"), ("enter", "B")]


def test_a_position_keeps_its_entry_size_unless_the_strategy_asks_for_resizing():
    px = calm_then_wild()
    sizing = {"rule": "inverse_vol", "vol_window": 10, "position_vol": 0.02}
    fixed = st.RuleStrategy.from_spec(hold_spec(sizing))
    size = fixed.size(px)
    w, events = st.run_rules(fixed, px)
    assert w["A"].iloc[10:].nunique() == 1 and w["A"].iloc[10] == pytest.approx(size["A"].iloc[10])
    assert {e["action"] for e in events} == {"enter"}

    resized = st.RuleStrategy.from_spec(hold_spec({**sizing, "rebalance_days": 5}))
    w5, events5 = st.run_rules(resized, px)
    assert w5["A"].iloc[10:15].nunique() == 1                       # nothing changes between resizes
    for bar in (15, 30, 50):                                        # 5, 20 and 40 sessions after the entry on bar 10
        assert w5["A"].iloc[bar] == pytest.approx(size["A"].iloc[bar])
    assert w5["A"].iloc[52] == pytest.approx(size["A"].iloc[50])    # held at the last resize until the next one
    assert w5["A"].iloc[50] < 0.25 * w5["A"].iloc[20]               # six times the volatility: a much smaller position
    assert w5["B"].iloc[50] > w5["B"].iloc[20]                      # and the fund that calmed down grew
    moves = [e for e in events5 if e["action"] == "resize" and e["symbol"] == "A"]
    assert [px.index.get_loc(e["date"]) for e in moves] == [30, 35, 40]             # only when the size really changed
    assert moves[-1]["weight"] == pytest.approx(size["A"].iloc[40]) and "5 sessions" in moves[0]["reason"]


def test_resizing_respects_the_position_cap_the_gross_cap_and_a_day_without_a_size():
    px = calm_then_wild()
    px.iloc[38:52, px.columns.get_loc("A")] = px["A"].iloc[38]      # A stops moving: no volatility, so no size, on bars 48-51
    s = st.RuleStrategy.from_spec(hold_spec({"rule": "inverse_vol", "vol_window": 10, "position_vol": 0.2, "rebalance_days": 5},
                                            max_position_pct=0.6, max_gross=1.0))
    size = s.size(px)
    w, _ = st.run_rules(s, px)
    assert size["A"].iloc[50] != size["A"].iloc[50]                 # no size on bar 50, a resize day
    assert w["A"].iloc[50] == w["A"].iloc[49] > 0                   # so the position is left as it was
    assert (w.abs().sum(axis=1) <= 1.0 + 1e-12).all()               # two positions at the 60% cap are scaled to 100% in all
    assert w.iloc[45].tolist() == pytest.approx([0.5, 0.5])


@pytest.mark.parametrize("days", [0, 253, True, "monthly"])
def test_rebalance_days_is_checked(days):
    with pytest.raises(st.SpecError):
        st.RuleStrategy.from_spec(hold_spec({"rule": "equal", "rebalance_days": days}))


def test_resizing_survives_a_round_trip_and_is_offered_to_the_research_agents(tmp_path):
    from valedia_trader.research_loop import research
    s = st.RuleStrategy.from_spec(hold_spec({"rule": "inverse_vol", "rebalance_days": 21}))
    again = st.load_strategy(st.save_spec(s, tmp_path))
    assert again.rebalance_days == 21 and again.to_spec() == s.to_spec()
    assert st.RuleStrategy.from_spec(hold_spec()).rebalance_days is None
    assert "  hold: " in research.RULE_LIBRARY and "rebalance_days in [1, 252]" in research.RULE_LIBRARY
    assert "Parameters: ." not in research.RULE_LIBRARY


# ---- the two hooks for strategies of another kind (T38-1)


def test_run_rules_hands_a_strategy_with_its_own_walk_over_to_it():
    sentinel = (pd.DataFrame(), [{"action": "enter"}])

    class Walked(st.Strategy):
        name, version, market, risk = "walked_test", "1.0.0", ("A",), st.StrategyRisk()
        hypothesis = "A strategy that walks itself is handed over whole"
        calls = []

        def entry(self, prices):
            raise AssertionError("run_rules must not ask a walked strategy for entries")

        exit = size = entry

        def walk(self, prices):
            self.calls.append(len(prices))
            return sentinel

    s = Walked()
    assert st.run_rules(s, frame(Z=[1, 2, 3])) is sentinel and s.calls == [3]     # handed over, prices as given
    s.name = "Not A Name"
    with pytest.raises(st.SpecError, match="lower_snake_case"):
        st.run_rules(s, frame(A=[1, 2, 3]))                                       # identity is checked first
    assert s.calls == [3]
    s.name, s.walk = "walked_test", "not callable"
    with pytest.raises(AssertionError, match="must not ask"):
        st.run_rules(s, frame(A=[1, 2, 3]))                                       # only a callable walk is a walk


def test_load_strategy_builds_a_pairs_spec_and_refuses_an_unknown_kind(tmp_path):
    from valedia_trader.research_loop.pairs import PairsStrategy
    pairs = {"kind": "pairs", "name": "pairs_load", "version": "1.0.0", "hypothesis": "Two related prices drift back together",
             "pairs": [["A", "B"]], "signal": {"hedge": "ratio", "window": 20, "enter": 1.5, "exit": 0.5}, "legs": "both"}
    (tmp_path / "pairs_load.json").write_text(json.dumps(pairs))
    loaded = st.load_strategy(tmp_path / "pairs_load.json")
    assert isinstance(loaded, PairsStrategy) and loaded.to_spec()["kind"] == "pairs" and loaded.market == ("A", "B")
    (tmp_path / "rules.json").write_text(json.dumps(spec()))
    assert type(st.load_strategy(tmp_path / "rules.json")) is st.RuleStrategy        # no kind: a rule strategy, as before
    for kind in ("rule", "rules", "Pairs", None, 7):
        (tmp_path / "odd.json").write_text(json.dumps({**spec(), "kind": kind}))
        with pytest.raises(st.SpecError, match="pairs"):
            st.load_strategy(tmp_path / "odd.json")
    (tmp_path / "bad_pairs.json").write_text(json.dumps({**pairs, "market": ["A", "B"]}))
    with pytest.raises(st.SpecError, match="unknown keys"):
        st.load_strategy(tmp_path / "bad_pairs.json")
    (tmp_path / "list.json").write_text("[1, 2]")
    with pytest.raises(st.SpecError, match="JSON object"):
        st.load_strategy(tmp_path / "list.json")
