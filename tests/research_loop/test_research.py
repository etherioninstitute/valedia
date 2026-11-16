import dataclasses
import io
import json
import math
import os
import re

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop import research as rs
from valedia_trader.research_loop import strategy as st

SYMBOLS = ["SPY", "TLT", "IEF", "LQD", "XLK", "XLU", "GLD", "DBC"]


def dev_prices(n=600):
    rng = np.random.default_rng(0)
    return pd.DataFrame(100 * np.exp(np.cumsum(0.0003 + 0.01 * rng.standard_normal((n, len(SYMBOLS))), axis=0)),
                        index=pd.bdate_range("2018-01-01", periods=n), columns=SYMBOLS)


def idea(text="Sector funds that broke to a new yearly high keep rising", **over):
    base = {"idea": text, "why": "Trends in fund flows persist for months", "conditions": "A market that is trending, not range bound",
            "falsifier": "A walk-forward Sharpe below 0.2 after costs", "symbols": ["XLK", "XLU", "NOPE"],
            "strategy": {"entry": {"rule": "breakout", "lookback": 200, "enter": 1.0, "exit": 0.0, "direction": "long"},
                         "sizing": {"rule": "inverse_vol"}, "risk": {"max_position_pct": 0.2, "stop_loss_pct": 0.12}},
            "prediction": {"annual_return": 0.03, "win_rate": 0.45, "sharpe": 0.5, "fail_if_sharpe_below": 0.2, "fail_if_drawdown_above": 0.15}}
    base.update(over)
    return base


@pytest.fixture
def env(tmp_path):
    paths = LoopPaths(tmp_path).ensure()
    return paths, lg.HypothesisLedger(paths.ledger), dev_prices()


def test_agents_work_separately_and_only_from_their_own_angle(env):
    paths, ledger, px = env
    prompts = {}

    def agent(key, reply):
        def ask(prompt):
            assert list(paths.research.glob("*.md")) == []               # no memo exists while anyone is still thinking
            prompts[key] = prompt
            return reply
        return ask

    out = rs.run_research(ledger, paths, px, None, "us-etf", {
        "price-analyst": agent("price-analyst", "Here you go:\n```json\n" + json.dumps({"ideas": [idea()]}) + "\n```"),
        "macro-watcher": agent("macro-watcher", json.dumps({"ideas": [idea("Long bonds rally when credit weakens against treasuries",
                               symbols=["TLT"], strategy={"entry": {"rule": "momentum", "lookback": 60, "enter": 0.02, "exit": 0.0}})]})),
    }, today="2026-11-10")
    assert "You are the Price Analyst. Look only at price and volume patterns" in prompts["price-analyst"]
    assert "ret_12m" in prompts["price-analyst"] and "credit vs treasuries" not in prompts["price-analyst"]
    assert "credit vs treasuries" in prompts["macro-watcher"] and "ret_12m" not in prompts["macro-watcher"]
    assert "new yearly high" not in prompts["macro-watcher"]             # neither saw the other's idea
    assert f"as of {px.index[-1].date()}" in prompts["price-analyst"] and "at most 3 testable trading ideas" in prompts["price-analyst"]
    assert all(k in prompts["price-analyst"] for k in ("momentum", "breakout", "reversion", "ma_cross", "drift", "inverse_vol"))
    assert [r["accepted"] for r in out["roles"].values()] == [1, 1] and len(out["accepted"]) == 2
    assert sorted(p.name for p in paths.research.glob("*.md")) == ["macro-watcher-2026-11-10.md", "price-analyst-2026-11-10.md"]


def test_an_accepted_idea_gets_a_ledger_row_a_strategy_file_and_a_locked_prediction(env):
    paths, ledger, px = env
    out = rs.run_research(ledger, paths, px, None, "us-etf", {"price-analyst": lambda p: json.dumps({"ideas": [idea()]})}, today="2026-11-10")
    r = out["accepted"][0]
    h = ledger.get(r["hypothesis_id"])
    assert h["status"] == "untested" and h["source_agent"] == "price-analyst" and h["market"] == "us-etf"
    assert h["rationale"].startswith("Trends in fund flows") and h["falsifier"].startswith("A walk-forward")
    s = st.load_strategy(paths.root / r["strategy_file"])
    assert s.market == ("XLK", "XLU") and s.version == "1.0.0" and s.hypothesis == r["idea"]      # unknown symbol dropped
    assert s.name == f"pa_sector_funds_that_h{r['hypothesis_id']}"
    pred = paths.backtests / f"{s.name}-prediction.md"
    assert pred.is_file() and not os.access(pred, os.W_OK) and ledger.prediction(r["hypothesis_id"])["expected"]["sharpe"] == 0.5
    memo = (paths.research / "price-analyst-2026-11-10.md").read_text()
    assert "Ledger check: 0 ideas" in memo and f"Recorded as hypothesis #{r['hypothesis_id']}" in memo and "Prediction (now locked)" in memo
    assert "What would prove it wrong: A walk-forward Sharpe below 0.2" in memo


def test_the_ledger_is_checked_first_and_failed_ideas_are_not_repeated(env):
    paths, ledger, px = env
    old = ledger.propose("Sector funds that broke to a new yearly high keep rising", "price-analyst", "us-etf")
    ledger.set_status(old, "failed", "no edge after costs")
    seen = []
    ask = lambda p: seen.append(p) or json.dumps({"ideas": [idea(), idea("Gold rises when long bonds fall for three months", symbols=["GLD"])]})
    out = rs.run_research(ledger, paths, px, None, "us-etf", {"price-analyst": ask}, today="2026-11-10")
    assert f"#{old} [failed] Sector funds that broke" in seen[0] and "no edge after costs" in seen[0]
    role = out["roles"]["price-analyst"]
    assert role["accepted"] == 1 and f"already tested and failed as hypothesis {old}" in role["rejected"][0]
    again = rs.run_research(ledger, paths, px, None, "us-etf",
                            {"price-analyst": lambda p: json.dumps({"ideas": [idea(conditions_changed=True)]})}, today="2026-11-11")
    assert again["roles"]["price-analyst"]["accepted"] == 1 and ledger.get(old)["status"] == "failed"


def test_untestable_or_incomplete_ideas_are_turned_away_and_the_cap_is_three(env):
    paths, ledger, px = env
    ideas = [idea("Buy when the moon is full and sell at new moon", strategy={"entry": {"rule": "lunar", "enter": 1, "exit": 0}}),
             idea("Vague", falsifier=""),
             idea("Energy funds revert after sharp five day falls", prediction={"annual_return": 0.03}),
             idea("A fourth idea that is perfectly fine but one too many")]
    out = rs.run_research(ledger, paths, px, None, "us-etf", {"price-analyst": lambda p: json.dumps({"ideas": ideas})}, today="2026-11-10")
    role = out["roles"]["price-analyst"]
    assert role["proposed"] == 3 and role["accepted"] == 0 and "only the first 3" in role["note"]
    assert "not testable as written" in role["rejected"][0] and "incomplete" in role["rejected"][1] and "not testable" in role["rejected"][2]
    assert ledger.summary()["untested"] == 0 and list(paths.strategies.glob("*.json")) == []
    garbled = rs.run_research(ledger, paths, px, None, "us-etf", {"price-analyst": lambda p: "I have no ideas today."}, today="2026-11-12")
    assert "could not be read" in garbled["roles"]["price-analyst"]["note"]
    down = rs.run_research(ledger, paths, px, None, "us-etf", {"price-analyst": lambda p: 1 / 0}, today="2026-11-13")
    assert "did not answer" in down["roles"]["price-analyst"]["note"]
    with pytest.raises(ValueError):
        rs.run_research(ledger, paths, px, None, "us-etf", {"astrologer": lambda p: ""})


def test_fundamentals_reader_is_skipped_honestly_and_the_skeptic_argues_last(env):
    paths, ledger, px = env
    asked = []
    skeptic_prompt = []

    def skeptic(prompt):
        skeptic_prompt.append(prompt)
        hid = int(prompt.split("#")[1].split(":")[0])
        return json.dumps({"objections": [{"hypothesis_id": hid, "why_it_fails": "Breakouts in funds are crowded and reverse.",
                                           "kill_test": "Run it at double costs through 2015."},
                                          {"hypothesis_id": 999, "why_it_fails": "not an idea on the table"}]})

    out = rs.run_research(ledger, paths, px, None, "us-etf", {
        "fundamentals-reader": lambda p: asked.append(p) or "{}", "price-analyst": lambda p: json.dumps({"ideas": [idea()]})},
        skeptic=skeptic, today="2026-11-10")
    assert asked == [] and "no earnings or filings source" in out["roles"]["fundamentals-reader"]["note"]
    assert "Skipped" in (paths.research / "fundamentals-reader-2026-11-10.md").read_text()
    hid = out["accepted"][0]["hypothesis_id"]
    assert out["roles"]["skeptic"]["objections"] == 1 and "argue why each idea below will fail" in skeptic_prompt[0]
    assert "Breakouts in funds are crowded" in ledger.get(hid)["lessons_learned"] and ledger.findings(hid)[0]["source"] == "skeptic"
    assert "Run it at double costs" in (paths.research / "skeptic-2026-11-10.md").read_text()


def test_tables_and_askers():
    px = dev_prices()
    table = rs.price_table(px, px * 1e6)
    assert "volume_20d_vs_1y" in table and _csv_block(table).count("\n") == len(SYMBOLS) + 1
    macro = rs.macro_table(px)
    assert "TLT/IEF long bonds vs intermediate" in macro and "SPY-TLT correlation of daily returns, last 60 sessions on which both have a price: " in macro
    assert rs.parse_ideas('noise {"ideas": [{"idea": "x"}, 5]} trailing') == [{"idea": "x"}]
    for bad in ("nothing", '{"ideas": 3}'):
        with pytest.raises(ValueError):
            rs.parse_ideas(bad)
    with pytest.raises(ValueError):
        rs.hermes_asker("bad profile; rm -rf")
    with pytest.raises(ValueError):
        rs.chat_asker("http://example.com/v1", "m")


def test_strategies_are_held_to_the_accounts_kill_switch_limits(env):
    paths, ledger, px = env
    limits = {"max_daily_loss_pct": 2.0, "max_drawdown_pct": 8.0, "max_position_pct": 0.10, "max_open_positions": 5}
    seen = []
    greedy = idea(strategy={"entry": {"rule": "breakout", "lookback": 200, "enter": 1.0, "exit": 0.0},
                            "risk": {"max_position_pct": 0.4, "max_open_positions": 8, "stop_loss_pct": 0.1}})
    modest = idea("Gold rises when long bonds fall for three months", symbols=["GLD"],
                  strategy={"entry": {"rule": "momentum", "lookback": 60, "enter": 0.02, "exit": 0.0}, "risk": {"max_position_pct": 0.05}})
    out = rs.run_research(ledger, paths, px, None, "us-etf", {"price-analyst": lambda p: seen.append(p) or json.dumps({"ideas": [greedy, modest]})},
                          today="2026-11-10", account_limits=limits)
    assert "refuses any position above 10% of capital and any new position once 5 are open" in seen[0]
    first, second = out["accepted"]
    assert first["spec"]["risk"]["max_position_pct"] == 0.10 and first["spec"]["risk"]["max_open_positions"] == 5
    assert first["cut_to_account_limits"] == ["max_position_pct 0.4 -> 0.1", "max_open_positions 8 -> 5"]
    assert second["spec"]["risk"]["max_position_pct"] == 0.05 and "cut_to_account_limits" not in second     # already inside the limits
    assert st.load_strategy(paths.root / first["strategy_file"]).risk.max_position_pct == 0.10               # the file is the cut version
    assert "Cut to the account's limits: max_position_pct 0.4 -> 0.1" in (paths.research / "price-analyst-2026-11-10.md").read_text()
    unbounded = rs.run_research(ledger, paths, px, None, "us-etf",
                                {"price-analyst": lambda p: json.dumps({"ideas": [idea("Utilities lead when rates fall for a quarter", strategy=greedy["strategy"])]})},
                                today="2026-11-11")
    assert unbounded["accepted"][0]["spec"]["risk"]["max_position_pct"] == 0.4                               # no limits given: nothing is cut


def test_research_agents_are_called_without_tools(monkeypatch):
    """Tool-free by code: an agent with file tools could read the sealed prices or another agent's memo."""
    from types import SimpleNamespace
    seen = {}
    monkeypatch.setattr(rs.subprocess, "run", lambda argv, **k: seen.update(argv=argv) or SimpleNamespace(returncode=0, stdout="ok"))
    assert rs.hermes_asker("valedia-trading-decider")("PROMPT") == "ok"
    assert seen["argv"] == ["hermes", "-p", "valedia-trading-decider", "-z", "PROMPT", "-t", "clarify"]
    with pytest.raises(ValueError):
        rs.hermes_asker("valedia-trading-decider", toolsets="file; rm")


# ---------------------------------------------------------------------------------------------------------------------
# T40-B1: the research tables say what their numbers are
# ---------------------------------------------------------------------------------------------------------------------

def dollar_volume(px):
    rng = np.random.default_rng(5)
    return px * 1e6 * np.exp(0.4 * rng.standard_normal(px.shape))


def _csv_block(text):
    """The CSV part of a table string: from the header line (it starts with a comma) to the first line that is not a row."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(","))
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("SPY-TLT correlation")), len(lines))
    return "\n".join(lines[start:end]) + "\n"


def _parse(text):
    return pd.read_csv(io.StringIO(_csv_block(text)), index_col=0)


def _legend_part(text):
    """Everything written above the CSV header."""
    lines = text.splitlines()
    return "\n".join(lines[:next(i for i, ln in enumerate(lines) if ln.startswith(","))])


# Each column recomputed from numpy arrays and from the definition's own window, not from the module's code.
def _ret(p, v, w, b):
    return p[-1] / p[-1 - w] - 1


def _vol(p, v, w, b):
    r = p[1:] / p[:-1] - 1
    return np.std(r[-w:], ddof=1) * math.sqrt(252)


def _below_high(p, v, w, b):
    return p[-1] / p[-w:].max() - 1


def _vs_avg(p, v, w, b):
    return p[-1] / p[-w:].mean() - 1


def _move_in_sd(p, v, w, b):
    r = p[1:] / p[:-1] - 1
    return (p[-1] / p[-1 - w] - 1) / (np.std(r[-b:], ddof=1) * math.sqrt(w))


def _volume_ratio(p, v, w, b):
    return v[-w:].mean() / v[-b:].mean() - 1


PRICE_RECOMPUTE = {"ret_1m": _ret, "ret_3m": _ret, "ret_12m": _ret, "vol_20d": _vol, "vol_1y": _vol, "below_1y_high": _below_high,
                   "vs_200d_avg": _vs_avg, "move_5d_in_sd": _move_in_sd, "volume_20d_vs_1y": _volume_ratio}
UNITS = {"fraction", "standard deviations", "correlation"}


def test_every_price_column_is_defined_and_recomputes_from_its_definition():
    px = dev_prices()
    dv = dollar_volume(px)
    defs = rs.PRICE_COLUMNS
    table = _parse(rs.price_table(px, dv))
    assert set(table.columns) == set(defs) == set(PRICE_RECOMPUTE)       # no unlabelled column, no definition without a column
    assert list(table.index) == SYMBOLS
    for name, d in defs.items():
        assert d.unit in UNITS and d.text.strip() and isinstance(d.window, int) and d.window > 0, name
        for sym in SYMBOLS:
            want = PRICE_RECOMPUTE[name](px[sym].to_numpy(), dv[sym].to_numpy(), d.window, d.baseline_window)
            assert table.loc[sym, name] == pytest.approx(want, abs=5.01e-4), (name, sym)
    assert defs["move_5d_in_sd"].unit == "standard deviations"
    assert all(defs[k].unit == "fraction" for k in defs if k != "move_5d_in_sd")
    assert [defs[k].window for k in ("ret_1m", "ret_3m", "ret_12m", "vol_1y")] == [21, 63, 252, 252]


def test_without_dollar_volume_the_volume_column_and_its_legend_line_are_absent():
    px = dev_prices()
    text = rs.price_table(px)
    assert "volume_20d_vs_1y" not in text and "ret_1m" in text


MACRO_RECOMPUTE = {"chg_3m": _ret, "chg_12m": _ret, "vs_200d_avg": _vs_avg}


def test_every_macro_column_is_defined_and_recomputes_from_its_definition():
    px = dev_prices()
    defs = rs.MACRO_COLUMNS
    table = _parse(rs.macro_table(px))
    assert set(table.columns) == set(defs) == set(MACRO_RECOMPUTE)
    present = [(a, b, label) for a, b, label in rs._PAIRS if a in px and b in px]
    assert len(present) == 4 and len(table) == len(present)               # one row per pair, and nothing else
    for a, b, label in present:
        ratio = (px[a] / px[b]).to_numpy()
        for name, d in defs.items():
            assert d.unit == "fraction" and d.text.strip() and d.window > 0
            want = MACRO_RECOMPUTE[name](ratio, None, d.window, d.baseline_window)
            assert table.loc[f"{a}/{b} {label}", name] == pytest.approx(want, abs=5.01e-4), (a, b, name)
    assert [defs[k].window for k in ("chg_3m", "chg_12m", "vs_200d_avg")] == [63, 252, 200]


def test_a_column_with_no_definition_is_refused_by_the_legend_generator():
    with pytest.raises(ValueError, match="mystery"):
        rs._legend(rs.PRICE_COLUMNS, ["ret_1m", "mystery"])
    assert rs._legend(rs.PRICE_COLUMNS, []) == ""


def test_the_legend_is_generated_from_the_definitions_and_sits_above_each_table_in_the_prompt():
    px = dev_prices()
    dv = dollar_volume(px)
    for key, defs in (("price-analyst", rs.PRICE_COLUMNS), ("macro-watcher", rs.MACRO_COLUMNS)):
        role = rs.ROLES[key]
        table = role.table(px, dv)
        prompt = rs.build_prompt(role, "us-etf", SYMBOLS, px.index[-1], table, [])
        legend = _legend_part(table)
        header = next(ln for ln in table.splitlines() if ln.startswith(","))
        assert legend and legend in prompt and prompt.index(legend) < prompt.index(header)
        assert prompt.index("Your data, as of") < prompt.index(legend)
        for name, d in defs.items():
            line = next(ln for ln in legend.splitlines() if ln.lstrip("- ").startswith(name + " "))
            assert d.unit in line and f"{d.window} sessions" in line and d.text in line, name
            if d.baseline_window:
                assert f"{d.baseline_window} sessions" in line
        for sentence in ("fractions", "0.052 means 5.2%", "1m = 21", "3m = 63", "12m and 1y = 252", "252 sessions a year", "3 decimals",
                         "A blank cell means the symbol does not have a price on every session the column needs."):
            assert sentence in legend, sentence
    other = rs.build_prompt(rs.ROLES["price-analyst"], "us-etf", SYMBOLS, px.index[-1], rs.price_table(px), [])
    assert "ret_12m" in other and "chg_12m" not in other and "credit vs treasuries" not in other


def test_the_legend_names_only_the_columns_the_table_has():
    px = dev_prices()
    with_volume, without = _legend_part(rs.price_table(px, dollar_volume(px))), _legend_part(rs.price_table(px))
    assert "volume_20d_vs_1y" in with_volume and "volume_20d_vs_1y" not in without


def test_the_correlation_is_its_own_line_after_the_macro_table_and_in_no_row():
    px = dev_prices()
    text = rs.macro_table(px)
    table = _parse(text)
    assert not any("correlation" in str(i).lower() for i in table.index) and "chg_3m" in table.columns
    r = px.pct_change().iloc[-60:]
    want = np.corrcoef(r["SPY"].to_numpy(), r["TLT"].to_numpy())[0, 1]
    line = f"SPY-TLT correlation of daily returns, last 60 sessions on which both have a price: {want:.3f}"
    assert text.splitlines()[-1] == line and text.count("SPY-TLT correlation") == 1
    assert text.index(line) > text.index(_csv_block(text).strip().splitlines()[-1])
    for gone in ("SPY", "TLT"):
        assert not any(ln.startswith("SPY-TLT correlation") for ln in rs.macro_table(px.drop(columns=gone)).splitlines())


def _prices_on(idx, seed=3):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(100 * np.exp(np.cumsum(0.0003 + 0.01 * rng.standard_normal((len(idx), len(SYMBOLS))), axis=0)),
                        index=idx, columns=SYMBOLS)


def _frame(rate, n):
    """n rows on a seven-day calendar of about `rate` rows a year: day offsets rate-spaced, to the day."""
    days = np.floor(np.arange(n) * 365.25 / rate).astype(int)
    return _prices_on(pd.Timestamp("2018-01-01") + pd.to_timedelta(days, unit="D"))


def _weekday_index(n, d0="2018-01-02", d1="2020-12-31"):
    """n strictly increasing timestamps, all on weekdays, the first on d0 and the last on d1's last weekday: rows are spread evenly
    over the weekdays and, when there are more rows than weekdays, share a day an hour apart. The span is fixed, so the rows a year
    are exactly proportional to n."""
    days = pd.bdate_range(d0, d1)
    pos = np.linspace(0, len(days) - 1, n)
    day = np.floor(pos + 1e-9).astype(int)
    first_of_day = {d: i for i, d in reversed(list(enumerate(day)))}
    hour = np.array([i - first_of_day[d] for i, d in enumerate(day)])
    return pd.DatetimeIndex(days[day]) + pd.to_timedelta(hour, unit="h")


def _weekday_frame(rate, years=3):
    return _prices_on(_weekday_index(int(rate * years)))


@pytest.mark.parametrize("fn", ["price_table", "macro_table"])
def test_calendar_guard_refuses_a_frame_that_is_not_252_sessions_a_year(fn):
    daily = _frame(365.25, 800)                                          # a seven-days-a-week frame
    measured = f"{800 / ((daily.index[-1] - daily.index[0]).days / 365.25):.1f}"
    with pytest.raises(ValueError, match=re.escape(measured)):
        getattr(rs, fn)(daily)
    for rate in (200, 230, 275, 300):
        with pytest.raises(ValueError, match="rows a year"):
            getattr(rs, fn)(_weekday_frame(rate))
    for rate in (245, 252, 258):                                         # within 5% of 252
        assert getattr(rs, fn)(_weekday_frame(rate))
    assert getattr(rs, fn)(dev_prices())                                 # the weekday calendar (about 262 a year, holidays kept in)


def test_calendar_guard_boundary_is_252_minus_and_plus_five_percent_within_one_row():
    idx = {n: _weekday_index(n) for n in range(600, 1000)}
    years = (idx[600][-1] - idx[600][0]).days / 365.25
    assert all((i[-1] - i[0]).days / 365.25 == years for i in idx.values())      # one span for every row count

    def refused(n):
        try:
            rs._check_calendar(pd.DataFrame({"SPY": 1.0}, index=idx[n]))
        except ValueError:
            return True
        return False
    mid = int(252 * years)
    below = max(n for n in range(600, mid) if refused(n))                # the largest count refused below 252 a year
    above = min(n for n in range(mid, 1000) if refused(n))               # the smallest count refused above it
    assert not refused(mid) and abs(below - 0.95 * 252 * years) <= 1 and abs(above - 1.05 * 252 * years) <= 1
    assert not any(refused(n) for n in range(below + 1, above))


@pytest.mark.parametrize("fn", ["price_table", "macro_table"])
def test_calendar_guard_does_not_judge_the_rate_of_a_frame_shorter_than_a_year(fn):
    short = _prices_on(_weekday_index(150, "2018-01-02", "2018-11-30"))  # 150 weekday rows over 0.91 years: about 165 a year
    assert (short.index[-1] - short.index[0]).days < 365
    assert isinstance(getattr(rs, fn)(short), str)


@pytest.mark.parametrize("fn", ["price_table", "macro_table"])
def test_calendar_guard_refuses_a_row_dated_on_a_weekend_whatever_the_length(fn):
    seven_day_short = _frame(365.25, 360)                                # 360 seven-day rows: under a year
    assert (seven_day_short.index[-1] - seven_day_short.index[0]).days < 365
    with pytest.raises(ValueError, match="Saturday or Sunday"):
        getattr(rs, fn)(seven_day_short)
    one = dev_prices().copy()                                            # a single Saturday in an otherwise clean frame
    friday = next(k for k in range(100, 200) if one.index[k].dayofweek == 4)
    one.index = one.index.where(one.index != one.index[friday], one.index[friday] + pd.Timedelta(days=1))
    assert one.index[friday].dayofweek == 5 and one.index.is_monotonic_increasing
    with pytest.raises(ValueError, match="Saturday or Sunday"):
        getattr(rs, fn)(one)
    mixed = pd.concat([dev_prices(2600), _prices_on(pd.date_range(dev_prices(2600).index[-1] + pd.Timedelta(days=1), periods=100))])
    assert abs(len(mixed) / ((mixed.index[-1] - mixed.index[0]).days / 365.25) - 252) < 0.05 * 252   # the rate rule alone passes it
    with pytest.raises(ValueError, match="Saturday or Sunday"):
        getattr(rs, fn)(mixed)


@pytest.mark.parametrize("fn", ["price_table", "macro_table"])
def test_calendar_guard_refuses_an_index_that_is_not_strictly_increasing(fn):
    px = dev_prices()
    with pytest.raises(ValueError, match="strictly increasing"):
        getattr(rs, fn)(px.iloc[::-1])
    swapped = px.copy()
    swapped.index = swapped.index[:300].tolist() + [swapped.index[301], swapped.index[300]] + swapped.index[302:].tolist()
    with pytest.raises(ValueError, match="strictly increasing"):
        getattr(rs, fn)(swapped)
    dup = px.copy()
    dup.index = px.index[:50].tolist() + [px.index[49]] + px.index[51:].tolist()
    with pytest.raises(ValueError, match="strictly increasing"):
        getattr(rs, fn)(dup)


def test_a_refused_frame_is_reported_by_the_run_and_no_prompt_is_sent(env):
    paths, ledger, _ = env
    asked = []
    out = rs.run_research(ledger, paths, _frame(365.25, 800), None, "us-etf",
                          {"price-analyst": lambda p: asked.append(p) or "{}"}, today="2026-11-10")
    assert asked == [] and "rows a year" in out["roles"]["price-analyst"]["note"]


def test_the_macro_legend_says_what_a_session_is_for_a_pair_and_the_price_legend_does_not():
    px = dev_prices()
    sentence = "For a pair, a session is a day on which both funds have a price."
    assert sentence in _legend_part(rs.macro_table(px))
    assert sentence not in _legend_part(rs.price_table(px, dollar_volume(px)))


# ---- addendum 1

def _ragged(n_prices, gap=None, symbol="TLT"):
    """dev_prices with `symbol` priced only on its last `n_prices` sessions (or, with `gap`, missing on the sessions in `gap`)."""
    px = dev_prices()
    if gap is not None:
        px.loc[px.index[gap], symbol] = np.nan
    else:
        px.loc[px.index[: len(px) - n_prices], symbol] = np.nan
    return px


# the number of prices a column needs, independent of the module: a change needs the last price and the one `window` back; a
# standard deviation of `window` returns needs window + 1 consecutive prices; a high or an average needs `window` prices; the
# move needs its change and its baseline's returns; the volume ratio needs its baseline window of volumes
def _needs(name, d):
    if name.startswith("ret_"):
        return "ends", d.window
    if name.startswith("vol_"):
        return "run", d.window + 1
    if name in ("below_1y_high", "vs_200d_avg"):
        return "run", d.window
    if name == "move_5d_in_sd":
        return "both", (d.window, d.baseline_window + 1)
    if name == "volume_20d_vs_1y":
        return "run", d.baseline_window
    raise AssertionError(name)


def _present(series, name, d):
    """True when the series has a price on every session the column needs."""
    kind, k = _needs(name, d)
    ok = ~np.isnan(series)
    if kind == "ends":
        return len(series) > k and ok[-1] and ok[-1 - k]
    if kind == "run":
        return len(series) >= k and ok[-k:].all()
    return len(series) > k[0] and ok[-1] and ok[-1 - k[0]] and len(series) >= k[1] and ok[-k[1]:].all()


@pytest.mark.parametrize("make", [lambda: _ragged(30), lambda: _ragged(0, gap=[500]), lambda: _ragged(0, gap=slice(560, 575)),
                                  lambda: _ragged(0, gap=[599]), lambda: _ragged(100)])
def test_a_column_is_blank_for_a_symbol_without_a_price_on_every_session_it_needs(make):
    px = make()
    dv = dollar_volume(px)
    dv.loc[dv.index[:-15], "XLU"] = np.nan                               # XLU has 15 dollar volumes: the volume ratio needs 252
    table = _parse(rs.price_table(px, dv))
    blanks = 0
    for name, d in rs.PRICE_COLUMNS.items():
        for sym in SYMBOLS:
            series = (dv if name == "volume_20d_vs_1y" else px)[sym].to_numpy()
            if _present(series, name, d):
                want = PRICE_RECOMPUTE[name](px[sym].to_numpy(), dv[sym].to_numpy(), d.window, d.baseline_window)
                assert table.loc[sym, name] == pytest.approx(want, abs=5.01e-4), (sym, name)
            else:
                blanks += 1
                assert math.isnan(table.loc[sym, name]), (sym, name)
    assert blanks >= 1


def test_a_symbol_with_29_returns_has_no_yearly_figures_and_the_legend_says_what_blank_means():
    px = _ragged(30)                                                     # TLT: 30 prices, 29 returns
    text = rs.price_table(px, dollar_volume(px))
    row = _parse(text).loc["TLT"]
    assert row[["ret_1m", "vol_20d"]].notna().all()                      # 21 back and 20 returns are available
    assert row[["ret_3m", "ret_12m", "vol_1y", "below_1y_high", "vs_200d_avg", "move_5d_in_sd"]].isna().all()
    assert "A blank cell means the symbol does not have a price on every session the column needs." in _legend_part(text)
    assert _parse(text).drop(index="TLT").notna().all().all()            # nothing else went blank


def test_the_sentences_say_sample_standard_deviation_and_move_5d_in_sd_has_one_reading():
    defs = rs.PRICE_COLUMNS
    assert defs["move_5d_in_sd"].text == ("The 5-session change divided by the product of (the sample standard deviation of the "
                                          "last 60 daily returns) and (the square root of 5).")
    assert "sample standard deviation" in defs["vol_20d"].text and "sample standard deviation" in defs["vol_1y"].text


def test_every_number_in_a_sentence_is_formatted_from_the_definitions_own_fields():
    for table in (rs.PRICE_COLUMNS, rs.MACRO_COLUMNS):
        for name, d in table.items():
            changed = dataclasses.replace(d, window=37, baseline_window=41 if d.baseline_window else None)
            numbers = {int(x) for x in re.findall(r"\d+", changed.text)}
            assert 37 in numbers and (41 in numbers) == bool(d.baseline_window), name
            assert numbers <= {37, 41, 0, 1, 252}, (name, numbers)       # 252 is the sessions a year in "square root of 252"
            assert (252 in numbers) == name.startswith("vol_"), name
            line = rs._legend({**table, name: changed}, [name]).splitlines()[-1]
            assert changed.text in line and "window 37 sessions" in line


def _shared(px, a="SPY", b="TLT", k=61):
    both = px[[a, b]].dropna().iloc[-k:]
    r = both.to_numpy()[1:] / both.to_numpy()[:-1] - 1
    return np.corrcoef(r[:, 0], r[:, 1])[0, 1]


def test_the_correlation_uses_the_last_60_sessions_on_which_both_funds_have_a_price():
    px = dev_prices()
    px.loc[px.index[[590, 595]], "TLT"] = np.nan                         # two sessions without TLT: they are dropped first
    line = rs.macro_table(px).splitlines()[-1]
    assert line == f"SPY-TLT correlation of daily returns, last 60 sessions on which both have a price: {_shared(px):.3f}"
    plain = px.pct_change().iloc[-60:]
    assert f"{np.corrcoef(plain['SPY'].fillna(0), plain['TLT'].fillna(0))[0, 1]:.3f}" != f"{_shared(px):.3f}"


def test_the_correlation_needs_61_shared_prices():
    px = dev_prices()
    short = px.copy()
    short.loc[short.index[:-60], "TLT"] = np.nan                         # TLT has 60 prices: 59 returns
    assert not any(ln.startswith("SPY-TLT correlation") for ln in rs.macro_table(short).splitlines())
    enough = px.copy()
    enough.loc[enough.index[:-61], "TLT"] = np.nan                       # 61 prices: exactly 60 returns
    assert rs.macro_table(enough).splitlines()[-1].endswith(f"both have a price: {_shared(enough):.3f}")


def test_an_empty_macro_table_prints_no_table_and_no_legend():
    px = dev_prices()[["SPY", "TLT"]]                                    # no pair qualifies
    text = rs.macro_table(px)
    assert text == f"SPY-TLT correlation of daily returns, last 60 sessions on which both have a price: {_shared(px):.3f}\n"
    assert rs.macro_table(dev_prices()[["SPY", "XLK"]]) == ""
    assert rs.macro_table(dev_prices()[["XLK", "XLU"]].iloc[:200]) == ""   # a pair with too few sessions for the 12-month window
