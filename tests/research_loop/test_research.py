import json
import os

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
    assert "volume_20d_vs_1y" in table and table.count("\n") == len(SYMBOLS) + 1
    assert "TLT/IEF long bonds vs intermediate" in rs.macro_table(px) and "SPY-TLT 60d correlation" in rs.macro_table(px)
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
