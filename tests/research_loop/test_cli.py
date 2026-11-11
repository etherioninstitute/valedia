import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import cli
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop import strategy as st
from valedia_trader.research_loop.paper import PaperTrader


def prices(n=40):
    rng = np.random.default_rng(0)
    up = 100 * np.exp(np.cumsum(0.01 + 0.002 * rng.standard_normal(n)))
    down = 100 * np.exp(np.cumsum(-0.01 + 0.002 * rng.standard_normal(n)))
    return pd.DataFrame({"UP": up, "DOWN": down}, index=pd.bdate_range("2026-01-05", periods=n))


def strat():
    return st.RuleStrategy.from_spec({
        "name": "mom_paper", "version": "1.0.0", "hypothesis": "Assets that rose over ten days keep rising", "market": ["UP", "DOWN"],
        "entry": {"rule": "momentum", "lookback": 10, "enter": 0.03, "exit": 0.0, "direction": "both"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.5, "max_open_positions": 2, "stop_loss_pct": None}})


def trader(tmp_path, name):
    limits = tmp_path / "risk.json"
    limits.write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.6, "max_open_positions": 5}))
    return PaperTrader(tmp_path / name, limits, halt_file=None)


def test_a_weekly_catch_up_builds_the_same_book_as_daily_steps(tmp_path):
    px, s = prices(), strat()
    daily, weekly = trader(tmp_path, "daily"), trader(tmp_path, "weekly")
    for k in range(11, 25):
        daily.step(px.iloc[: k + 1], {7: s})
    first = cli.paper_catch_up(weekly, px.iloc[:12], {7: s})
    assert len(first) == 1 and first[0]["day"] == str(px.index[11].date())          # a first run starts today, no backfill
    assert len(cli.paper_catch_up(weekly, px.iloc[:18], {7: s})) == 6               # then every missed day, in order
    assert len(cli.paper_catch_up(weekly, px.iloc[:25], {7: s})) == 7
    assert cli.paper_catch_up(weekly, px.iloc[:25], {7: s}) == []                    # nothing new: nothing happens
    strip = lambda t: [{k: v for k, v in e.items() if k != "timestamp"} for e in t.journal.read()]
    assert weekly.state["cash"] == pytest.approx(daily.state["cash"]) and weekly.state["positions"] == daily.state["positions"]
    assert strip(weekly) == strip(daily)                                            # the same decisions, fills and summaries


def test_earlier_trials_are_recorded_once_as_failed_ideas_with_their_look_spent(tmp_path):
    db = tmp_path / "trials.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trials (id INTEGER PRIMARY KEY, strategy TEXT, sharpe REAL, n_obs INTEGER, params TEXT)")
    con.executemany("INSERT INTO trials (strategy, sharpe, n_obs, params) VALUES (?, ?, ?, ?)", [
        ("sleeve:trend", 0.03, 3000, "{}"), ("sleeve:trend", 0.03, 3000, "{}"), ("sleeve:timesfm", -0.01, 3000, "{}"),
        ("autoresearch:xs_momentum:fraction=0.4", 0.05, 1500, '{"fraction": 0.4}'),
        ("autoresearch:xs_momentum:fraction=0.2", 0.04, 1500, '{"fraction": 0.2}'), ("something:unknown", 0.9, 10, "{}")])
    con.commit()
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"strategies": {"sleeve:trend": {"holdout": {"sharpe": 0.28, "deflated_sharpe": 0.17}}}}))
    ledger = lg.HypothesisLedger()
    assert cli.seed_prior_trials(ledger, db, report) == 3                           # trend, timesfm, one autoresearch idea
    assert cli.seed_prior_trials(ledger, db, report) == 0                           # running init again adds nothing
    rows = ledger.search(market=cli.MARKET)
    assert len(rows) == 3 and all(r["status"] == "failed" and ledger.sealed_used(r["id"]) for r in rows)
    assert ledger.summary()["variations"] == 4                                      # the re-run of trend is one variation
    trend = next(r for r in rows if "six-month return is positive" in r["idea"])
    assert "holdout Sharpe 0.28" in trend["lessons_learned"] and "no clean sealed test left" in trend["lessons_learned"]
    timesfm = next(r for r in rows if "TimesFM" in r["idea"])
    assert "no directional skill" in timesfm["lessons_learned"]
    assert cli.seed_prior_trials(lg.HypothesisLedger(), tmp_path / "missing.sqlite") == 0


def test_agent_backends_and_the_timeout_knob(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.research_mod, "hermes_asker", lambda profile, timeout=None: seen.update(profile=profile, timeout=timeout) or "h")
    monkeypatch.setattr(cli.research_mod, "chat_asker", lambda url, model, **kw: seen.update(url=url, model=model, **kw) or "c")
    assert cli._asker("hermes:valedia-trading-decider", 1800.0) == "h" and seen["timeout"] == 1800.0
    assert cli._asker("hermes:valedia-trading-decider") == "h" and seen["timeout"] is None
    assert cli._asker("chat:qwen3:8b@http://127.0.0.1:11434/v1") == "c" and seen["model"] == "qwen3:8b" and seen["url"].endswith("/v1")
    with pytest.raises(SystemExit):
        cli._asker("smoke-signals:now")


def test_status_and_init_on_an_empty_project(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    assert json.loads((tmp_path / "config" / "risk.json").read_text())["max_open_positions"] == 5
    assert all((tmp_path / d).is_dir() for d in ("research", "strategies", "backtests", "journal", "memory", "config"))
    assert cli.main(["--root", str(tmp_path), "status"]) == 0 and '"untested": 0' in capsys.readouterr().out
    (tmp_path / "config" / "risk.json").write_text("{broken")
    assert cli.main(["--root", str(tmp_path), "init"]) == 0                         # init never overwrites the limits file
    assert (tmp_path / "config" / "risk.json").read_text() == "{broken"
