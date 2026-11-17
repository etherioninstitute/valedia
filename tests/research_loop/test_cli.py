import argparse
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


# ---- tax notes are notes: a missing or damaged rules file never stops paper trading (T3 rev 2)

def test_the_tax_rules_helper_is_none_without_a_file_rules_with_a_good_one_and_says_so_for_a_bad_one(tmp_path, capsys):
    from valedia_trader.execution_and_risk.tax import TaxRules
    from valedia_trader.research_loop import LoopPaths
    paths = LoopPaths(root=tmp_path)
    paths.config.mkdir(parents=True)
    assert cli._tax_rules(paths) is None and capsys.readouterr().err == ""          # no file: tax notes simply off
    good = (LoopPaths().config / "tax_rules.json").read_text()
    (paths.config / "tax_rules.json").write_text(good)
    rules = cli._tax_rules(paths)
    assert isinstance(rules, TaxRules) and rules.window_days == 30 and capsys.readouterr().err == ""
    (paths.config / "tax_rules.json").write_text("{ this is not json")
    assert cli._tax_rules(paths) is None
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("tax notes are off:") and len(err[0]) > len("tax notes are off:")
    (paths.config / "tax_rules.json").write_text(json.dumps({**json.loads(good), "lot_method": {"method": "LIFO"}}))
    assert cli._tax_rules(paths) is None and "FIFO" in capsys.readouterr().err   # loads as JSON, refused by the rules
    assert cli.paper_trader(paths).tax_rules is None                                  # the paper trader still builds
    assert capsys.readouterr().err.startswith("tax notes are off:")



def test_a_rules_file_missing_a_key_turns_tax_notes_off_with_one_line(tmp_path, capsys):
    from valedia_trader.research_loop import LoopPaths
    paths = LoopPaths(root=tmp_path)
    paths.config.mkdir(parents=True)
    raw = json.loads((LoopPaths().config / "tax_rules.json").read_text())
    raw["wash_sale"].pop("window_days")
    (paths.config / "tax_rules.json").write_text(json.dumps(raw))
    assert cli._tax_rules(paths) is None
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("tax notes are off:") and "wash_sale.window_days" in err[0]


# ---- the order step on the command line; paper-step no longer sends to Webull (T13)

class _Venue:
    """A stand-in for Webull's test environment: no network."""
    environment = "sandbox"
    name = "standin-sandbox"

    def __init__(self):
        self.calls, self.book = [], {}

    def positions(self):
        self.calls.append("positions")
        return {}

    def cash(self):
        self.calls.append("cash")
        return {"cash": 1_000_000.0}

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        self.calls.append("submit")
        self.book[tag] = {"symbol": symbol, "units": units, "limit": limit_price, "state": "open"}
        return {"status": "accepted"}

    def order_status(self, tag):
        self.calls.append("order_status")
        if tag not in self.book:
            return None
        return {"state": self.book[tag]["state"], "filled_units": 0.0, "avg_price": None, "fee": None, "venue_order_id": None,
                "raw_status": None}

    def cancel(self, tag):
        self.calls.append("cancel")
        self.book[tag]["state"] = "cancelled"
        return {"status": "cancel requested"}


REAL_ORDER_STEP_ENVIRONMENT = cli._order_step_environment   # before any fixture replaces it
REAL_ORDER_STEP_VENUE = cli._order_step_venue


@pytest.fixture
def order_step_project(tmp_path, monkeypatch):
    from valedia_trader.execution_and_risk import order_step
    from valedia_trader.research_loop import risk_gate
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "risk.json").write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0,
                                                               "max_position_pct": 0.6, "max_open_positions": 5}))
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "paper_state.json").write_text(json.dumps({
        "starting_cash": 100_000.0, "cash": 98_000.0, "positions": {"a": {"SPY": {"units": 4.0, "cost": 500.0}}},
        "pending": [{"strategy": "a", "version": "1", "symbol": "SPY", "units": 1.5, "decided_on": "2026-11-12", "reduces_risk": False,
                     "reasoning": "r", "conditions": None}],
        "history": [{"day": "2026-11-12", "equity": 100_000.0, "capital_per_strategy": 100_000.0}], "peak_equity": 100_000.0,
        "last_day": "2026-11-12", "realized": {}, "panel_cases": []}))
    venue = _Venue()
    now = pd.Timestamp("2026-11-13 10:00", tz="America/New_York").timestamp()
    monkeypatch.setattr(cli, "_order_step_venue", lambda: venue)
    monkeypatch.setattr(cli, "_order_step_environment", lambda environ=None: "sandbox", raising=False)   # never the secrets file
    monkeypatch.setattr(order_step, "wall_clock", lambda: now)
    monkeypatch.setattr(order_step, "minute_quotes", lambda symbols: {s: (500.0, now - 60) for s in symbols})
    monkeypatch.setattr(risk_gate, "HALT_FILE", tmp_path / "no-halt.json")
    return tmp_path, venue


def test_order_step_sends_the_paper_books_difference_to_the_test_environment(order_step_project, capsys):
    root, venue = order_step_project
    assert cli.main(["--root", str(root), "order-step"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "done" and out["orders_sent"] == 1 and out["sent"][0]["units"] == 5.0   # 4 held + 1.5 pending, whole shares
    assert [o["units"] for o in venue.book.values()] == [5.0] and list(venue.book.values())[0]["limit"] == 505.0
    assert (root / "memory" / "orders-standin-sandbox.sqlite").is_file() and (root / "memory" / "order_step-standin-sandbox.sqlite").is_file()
    journal = [json.loads(line) for line in (root / "journal" / "2026-11-13.jsonl").read_text().splitlines()]
    assert [e["event"] for e in journal if e["type"] == "order_step"] == ["baseline", "order", "summary"]
    assert cli.main(["--root", str(root), "order-step", "--fractional", "--capital", "200000"]) == 0   # the same day again
    out = json.loads(capsys.readouterr().out)
    assert out["sent"][0]["units"] == 11.0 and out["cancels"][0]["answer"] == "cancel requested"


def test_order_step_refuses_production_before_any_venue_call(order_step_project, capsys):
    root, venue = order_step_project
    venue.environment = "production"
    assert cli.main(["--root", str(root), "order-step"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "stopped" and out["stage"] == "environment" and venue.calls == []


@pytest.mark.parametrize("configured", ["production", "live", ""])
def test_order_step_refuses_a_configured_environment_other_than_the_sandbox_before_building_anything(
        order_step_project, monkeypatch, capsys, configured):
    root, venue = order_step_project
    monkeypatch.setattr(cli, "_order_step_environment", lambda environ=None: configured)
    monkeypatch.setattr(cli, "_order_step_venue", lambda: pytest.fail("a venue was built"))
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert cli.main(["--root", str(root), "order-step"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and len(captured.err.strip().splitlines()) == 1 and "sandbox" in captured.err
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before        # no ledger, no state file, nothing


def test_order_step_reads_the_configured_environment_as_the_webull_adapter_does():
    assert cli._order_step_environment({"WEBULL_ENV": " Production "}) == "production"
    assert cli._order_step_environment({}) == "sandbox" and cli._order_step_environment({"WEBULL_ENV": ""}) == "sandbox"


@pytest.mark.parametrize("error", ["refused", "unreadable"])
def test_order_step_ends_with_one_plain_line_when_the_secrets_file_cannot_be_loaded(order_step_project, monkeypatch, capsys, error):
    from valedia_trader.core import secrets
    root, venue = order_step_project

    def load_env(*a, **k):                                              # never the real file
        if error == "refused":
            raise secrets.SecretError("/somewhere/trading.env is readable by group or others; WEBULL_APP_SECRET=Zq-from-the-file")
        raise UnicodeDecodeError("utf-8", b"Zq-from-the-file", 0, 1, "invalid start byte")
    monkeypatch.setattr(secrets, "load_env", load_env)
    monkeypatch.setattr(cli, "_order_step_environment", REAL_ORDER_STEP_ENVIRONMENT)
    monkeypatch.setattr(cli, "_order_step_venue", lambda: pytest.fail("a venue was built"))
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert cli.main(["--root", str(root), "order-step"]) == 2
    captured = capsys.readouterr()
    lines = captured.err.strip().splitlines()
    assert captured.out == "" and len(lines) == 1 and lines[0].startswith("refused:")
    assert "Zq" not in captured.err and "trading.env" not in captured.err and "Traceback" not in captured.err
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before


@pytest.mark.parametrize("error", ["missing credential", "webull", "os"])
def test_order_step_ends_with_one_plain_line_and_creates_nothing_when_the_venue_cannot_be_built(order_step_project, monkeypatch,
                                                                                                capsys, error):
    from valedia_trader.core import secrets
    from valedia_trader.venues.webull import WebullError
    root, venue = order_step_project

    def build():
        if error == "missing credential":
            raise secrets.SecretError("credential WEBULL_APP_KEY is not set (expected in /somewhere/trading.env) Zq-file")
        if error == "webull":
            raise WebullError("could not open a session for account Zq-file in /somewhere/trading.env")
        raise PermissionError(13, "Permission denied", "/somewhere/trading.env")
    monkeypatch.setattr(cli, "_order_step_venue", build)
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert cli.main(["--root", str(root), "order-step"]) == 2
    captured = capsys.readouterr()
    lines = captured.err.strip().splitlines()
    assert captured.out == "" and len(lines) == 1 and lines[0].startswith("refused:")
    assert "Zq" not in captured.err and "trading.env" not in captured.err and "somewhere" not in captured.err
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before        # no folder, no ledger file


def test_order_step_lets_a_programming_error_in_building_the_venue_through(order_step_project, monkeypatch):
    root, venue = order_step_project
    monkeypatch.setattr(cli, "_order_step_venue", lambda: (_ for _ in ()).throw(TypeError("a bug")))
    with pytest.raises(TypeError):
        cli.main(["--root", str(root), "order-step"])


@pytest.mark.parametrize("error", [RuntimeError, ValueError, KeyError, LookupError, ZeroDivisionError])
def test_order_step_lets_every_error_but_webull_secrets_and_os_errors_through(order_step_project, monkeypatch, capsys, error):
    root, venue = order_step_project                                     # rev 6 outcome 4
    monkeypatch.setattr(cli, "_order_step_venue", lambda: (_ for _ in ()).throw(error("a bug")))
    with pytest.raises(error):
        cli.main(["--root", str(root), "order-step"])
    assert "refused:" not in capsys.readouterr().err


# ---- T13 rev 8 (cli): an Events venue is refused before the paper trader, the ledger or any file

def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize("how", ["name", "class", "getattr"])
def test_order_step_refuses_an_events_venue_with_one_plain_line_and_creates_nothing(order_step_project, monkeypatch, capsys,
                                                                                    how):
    root, venue = order_step_project
    if how == "name":
        venue.name = "webull-sandbox-events"
    elif how == "class":
        venue.account_class = "EVENTS_CASH"
    else:
        monkeypatch.setattr(type(venue), "__getattr__", lambda self, n: "EVENTS_CASH" if n == "account_class" else
                            (_ for _ in ()).throw(AttributeError(n)), raising=False)
    before = _tree(root)
    assert cli.main(["--root", str(root), "order-step"]) == 2
    captured = capsys.readouterr()
    lines = captured.err.strip().splitlines()
    assert captured.out == "" and len(lines) == 1 and lines[0].startswith("refused:") and "Events" in lines[0]
    assert _tree(root) == before and venue.calls == []


def test_order_step_command_given_an_events_venue_creates_no_ledger(tmp_path, capsys):
    venue = _Venue()
    venue.name = "webull-sandbox-events"
    paths = cli.LoopPaths(tmp_path)
    before = _tree(tmp_path)
    assert cli.order_step_command(argparse.Namespace(capital=None, fractional=False), paths, venue=venue) == 2
    assert _tree(tmp_path) == before and capsys.readouterr().err.startswith("refused:")


def test_order_step_reads_a_property_account_class_only_where_the_step_does(order_step_project, monkeypatch, capsys):
    root, venue = order_step_project
    reads = []
    monkeypatch.setattr(type(venue), "account_class", property(lambda self: reads.append(1) or "INDIVIDUAL_CASH"), raising=False)
    assert cli.main(["--root", str(root), "order-step"]) == 0 and reads == [1]       # once, by the step, in the session


def test_paper_step_webull_no_longer_sends_and_names_the_order_step(tmp_path, monkeypatch, capsys):
    from valedia_trader.venues import webull
    monkeypatch.setattr(webull.WebullVenue, "from_secrets", classmethod(lambda cls, *a, **k: pytest.fail("a Webull venue was built")))
    monkeypatch.setattr(cli, "_market", lambda args: pytest.fail("paper-step --webull went on to trade"))
    assert cli.main(["--root", str(tmp_path), "paper-step", "--webull"]) == 2
    err = capsys.readouterr().err
    assert "order-step" in err and "no longer" in err


def test_paper_step_alpaca_still_mirrors_to_the_alpaca_paper_account(tmp_path, monkeypatch, capsys):
    from valedia_trader.core import secrets
    built = []
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: [])
    monkeypatch.setattr(secrets, "secret", lambda name, *a: {"ALPACA_API_KEY": "PKTEST", "ALPACA_SECRET_KEY": "s"}[name])
    monkeypatch.setattr(cli, "AlpacaPaperVenue", lambda key, secret: built.append((key, secret)) or "venue")
    monkeypatch.setattr(cli, "_market", lambda args: (prices(), None, None))
    assert cli.main(["--root", str(tmp_path), "paper-step", "--alpaca"]) == 0
    assert built == [("PKTEST", "s")] and "nothing to paper trade" in capsys.readouterr().out


# ---- T20: the order step needs the account class to be named, before the venue or any file

def _class_run(root, monkeypatch, capsys, value, built):
    """The real `_order_step_venue` with the Webull adapter replaced, WEBULL_ACCOUNT_CLASS set to `value` (None: unset)."""
    from valedia_trader.venues import webull
    monkeypatch.setenv("WEBULL_ACCOUNT_CLASS", "x")                       # so the undo restores whatever it was
    if value is None:
        monkeypatch.delenv("WEBULL_ACCOUNT_CLASS")
    else:
        monkeypatch.setenv("WEBULL_ACCOUNT_CLASS", value)
    monkeypatch.setattr(cli, "_order_step_venue", REAL_ORDER_STEP_VENUE)
    monkeypatch.setattr(webull.WebullVenue, "from_secrets", classmethod(lambda cls, *a, **k: built.append(1) or _Venue()))
    before = _tree(root)
    code = cli.main(["--root", str(root), "order-step"])
    return code, capsys.readouterr(), before


@pytest.mark.parametrize("value", [None, "", "   ", "EVENTS_CASH", "CRYPTO", "FUTURES", "INDIVIDUAL", "Zq-not-a-class-but-secret-looking"])
def test_order_step_refuses_without_a_named_stock_account_class_before_building_anything(order_step_project, monkeypatch, capsys,
                                                                                          value):
    root, venue = order_step_project
    built = []
    code, captured, before = _class_run(root, monkeypatch, capsys, value, built)
    lines = captured.err.strip().splitlines()
    assert code == 2 and built == [] and captured.out == ""
    assert len(lines) == 1 and lines[0].startswith("refused:") and "WEBULL_ACCOUNT_CLASS" in lines[0]
    assert "INDIVIDUAL_CASH" in lines[0] and "INDIVIDUAL_MARGIN" in lines[0]
    assert "Zq" not in captured.err and "Traceback" not in captured.err
    assert _tree(root) == before and venue.calls == []                    # no folder, no ledger, no state file


@pytest.mark.parametrize("value", ["INDIVIDUAL_CASH", "INDIVIDUAL_MARGIN", " individual_cash "])
def test_order_step_goes_on_when_the_class_is_a_stock_account_class(order_step_project, monkeypatch, capsys, value):
    root, venue = order_step_project
    built = []
    code, captured, _ = _class_run(root, monkeypatch, capsys, value, built)
    assert built == [1] and "WEBULL_ACCOUNT_CLASS" not in captured.err


def test_order_step_reads_the_class_from_the_loaded_secrets_by_name(order_step_project, monkeypatch, capsys, tmp_path):
    root, venue = order_step_project
    env_file = tmp_path / "elsewhere" / "trading.env"
    env_file.parent.mkdir()
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(env_file))
    env_file.write_text("WEBULL_ACCOUNT_ID=Zq-an-account-id-value\n")
    env_file.chmod(0o600)
    built = []
    code, captured, before = _class_run(root, monkeypatch, capsys, None, built)       # an id alone does not name the class
    assert code == 2 and built == [] and "WEBULL_ACCOUNT_CLASS" in captured.err and "Zq" not in captured.err
    assert _tree(root) == before
    env_file.write_text("WEBULL_ACCOUNT_ID=Zq-an-account-id-value\nWEBULL_ACCOUNT_CLASS=INDIVIDUAL_MARGIN\n")
    code, captured, _ = _class_run(root, monkeypatch, capsys, None, built)
    assert built == [1] and "WEBULL_ACCOUNT_CLASS" not in captured.err


def test_order_step_given_a_venue_directly_is_not_asked_for_the_class(order_step_project, monkeypatch, capsys):
    root, venue = order_step_project
    monkeypatch.delenv("WEBULL_ACCOUNT_CLASS", raising=False)
    assert cli.main(["--root", str(root), "order-step"]) == 0               # the fixture's venue stands in for `_order_step_venue`


# ---- T20 (second round): reset-kill-switch on a latch that cannot be removed ends with one plain line

def test_reset_kill_switch_that_cannot_remove_the_latch_refuses_with_one_plain_line(tmp_path, capsys):
    latch = tmp_path / "journal" / "kill_switch.json"
    latch.mkdir(parents=True)
    (latch / "inside").write_text("x")
    assert cli.main(["--root", str(tmp_path), "reset-kill-switch", "--who", "burton"]) == 2
    captured = capsys.readouterr()
    lines = captured.err.strip().splitlines()
    assert captured.out == "" and len(lines) == 1 and lines[0].startswith("refused:") and str(latch) in lines[0]
    assert "Traceback" not in captured.err and latch.is_dir() and (latch / "inside").exists()


def test_reset_kill_switch_still_clears_a_latch_that_is_a_dangling_link(tmp_path, capsys):
    latch = tmp_path / "journal" / "kill_switch.json"
    latch.parent.mkdir(parents=True)
    latch.symlink_to(tmp_path / "nowhere.json")
    assert cli.main(["--root", str(tmp_path), "reset-kill-switch", "--who", "burton"]) == 0
    assert "latch cleared" in capsys.readouterr().out and not latch.is_symlink() and not (tmp_path / "nowhere.json").exists()


# ---- T22a: the order step's ticket flow on the command line (propose, approve), in the test environment

CLI_ACCOUNT = "ZqCLIACCOUNT0042"                                       # made up; never printed


def _ticket_out(text):
    lines = text.rstrip("\n").splitlines()
    number_lines = [ln for ln in lines if ln.startswith("ticket number: ")]
    body = "\n".join(ln for ln in lines if not ln.startswith("ticket number: "))
    return json.loads(body), number_lines


def test_order_step_propose_then_approve_on_the_command_line(order_step_project, capsys):
    root, venue = order_step_project
    venue.account_id = CLI_ACCOUNT
    assert cli.main(["--root", str(root), "order-step", "--propose"]) == 0
    captured = capsys.readouterr()
    out, number_lines = _ticket_out(captured.out)
    assert out["status"] == "done" and out["mode"] == "propose" and "submit" not in venue.calls and venue.book == {}
    assert len(number_lines) == 1
    number = number_lines[0].split()[2]
    assert len(number) == 6 and number.isdigit() and captured.out.count(number) == 1   # printed once, beside the summary
    assert "--approve NUMBER" in number_lines[0] and out["ticket"]["number_ends"] == number[-2:]
    assert [(o["fund"], o["side"], o["units"]) for o in out["ticket"]["orders"]] == [("SPY", "buy", 5.0)]
    journal_text = (root / "journal" / "2026-11-13.jsonl").read_text()
    assert number not in journal_text and CLI_ACCOUNT not in journal_text + captured.out + captured.err
    assert (root / "memory" / "order_step-standin-sandbox.tickets.sqlite").is_file()

    assert cli.main(["--root", str(root), "order-step", "--approve", number]) == 0
    captured = capsys.readouterr()
    out, number_lines = _ticket_out(captured.out)
    assert number_lines == [] and out["orders_sent"] == 1 and venue.calls.count("submit") == 1
    assert [(o["fund"], o["outcome"]) for o in out["ticket"]["orders"]] == [("SPY", "sent")]

    assert cli.main(["--root", str(root), "order-step", "--approve", number]) == 2        # used once
    out, _ = _ticket_out(capsys.readouterr().out)
    assert out["stage"] == "ticket" and "used" in out["reason"] and venue.calls.count("submit") == 1
    wrong = f"{(int(number) + 1) % 1_000_000:06d}"
    assert cli.main(["--root", str(root), "order-step", "--approve", wrong]) == 2         # a wrong number
    out, _ = _ticket_out(capsys.readouterr().out)
    assert out["stage"] == "ticket" and "no ticket" in out["reason"] and venue.calls.count("submit") == 1


def test_order_step_propose_and_approve_together_are_refused_before_anything_is_made(order_step_project, capsys):
    root, venue = order_step_project
    with pytest.raises(SystemExit) as exc:
        cli.main(["--root", str(root), "order-step", "--propose", "--approve", "123456"])
    assert exc.value.code == 2 and venue.calls == [] and not (root / "memory").exists()


def test_order_step_without_the_new_flags_calls_the_step_exactly_as_before(order_step_project, monkeypatch, capsys):
    from valedia_trader.execution_and_risk import order_step
    root, venue = order_step_project
    seen = []
    monkeypatch.setattr(order_step, "run_order_step", lambda **kw: seen.append(sorted(kw)) or {"status": "done"})
    assert cli.main(["--root", str(root), "order-step"]) == 0
    assert seen == [["capital", "clock", "fractional", "halt_file", "journal", "latch_file", "ledger", "limits_file", "paper",
                     "quotes", "state", "venue"]]                    # the base's keywords, nothing more
    assert capsys.readouterr().out == json.dumps({"status": "done"}, indent=2) + "\n"
    assert cli.order_step_command(argparse.Namespace(capital=None, fractional=False), cli.LoopPaths(root), venue=venue) == 0
    assert "propose" not in seen[-1] and "approve" not in seen[-1]


# ---- T22b: live mode on the command line (order-step --live), every venue a stand-in, every owner's file in tmp

LIVE_ACCOUNT = "ZqLIVEACCOUNT0077"                                     # made up; only its last 4 may ever be printed
LIVE_KEYS = {"ALPACA_API_KEY": "a", "ALPACA_SECRET_KEY": "b", "FINNHUB_API_KEY": "c", "WEBULL_ENV": "sandbox",
             "WEBULL_ACCOUNT_CLASS": "INDIVIDUAL_CASH"}          # T50: the ordinary file stays a practice file


class _LiveVenue(_Venue):
    """A stand-in for Webull's production environment: no network."""
    environment = "production"
    host = "api.webull.com"
    name = "webull-production"
    account_class = "INDIVIDUAL_CASH"
    account_id = LIVE_ACCOUNT

    def cash(self):                                              # T22d-1 (A6): live mode needs the settled-cash figure
        return {**super().cash(), "settled_cash": super().cash()["cash"]}


REAL_LIVE_VENUE = cli._order_step_live_venue                    # before any fixture replaces it (T32 rev 2)


def _strategy_file(root, hid, name, market=("SPY", "QQQ"), direction="long", entry=None):
    """T38-1b: long-only by default (a strategy that can hold a short is neither stepped nor followed); `direction`
    "both" keeps the old form; `entry` replaces the entry rule whole."""
    base = strat().to_spec()
    spec = {**base, "name": name, "market": list(market), "entry": entry or {**base["entry"], "direction": direction}}
    (root / "strategies").mkdir(exist_ok=True)
    path = root / "strategies" / f"{name}_h{hid}.json"
    path.write_text(json.dumps(spec))
    return path


def _owner_file(path, value, mode=0o644):
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(mode)
    return path


def _followed_book(root):
    """T32: the order step project's paper book under a real strategy's name (hypothesis 1, mom_paper, in the ledger
    and the strategy files; its latest day lists it), and config/broker_book.json following it."""
    state = json.loads((root / "journal" / "paper_state.json").read_text())
    state["positions"] = {"mom_paper": state["positions"]["a"]}
    state["pending"] = [{**o, "strategy": "mom_paper"} for o in state["pending"]]
    state["history"][-1]["strategy_values"] = {"mom_paper": 2_000.0}
    (root / "journal" / "paper_state.json").write_text(json.dumps(state))
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        hid = led.propose("Assets that rose over ten days keep rising", "test", cli.MARKET)
        led.set_status(hid, "passed paper")                 # rev 2: a hypothesis the paper command steps
    finally:
        led.close()
    _strategy_file(root, hid, "mom_paper")
    _owner_file(root / "config" / "broker_book.json", {"follow": [hid]})
    return hid


@pytest.fixture
def live_project(order_step_project, monkeypatch):
    from datetime import date

    from valedia_trader.data_and_fees import second_quote
    from valedia_trader.venues import webull
    root, _ = order_step_project
    monkeypatch.setattr(webull, "LIVE_REPO", root, raising=False)       # T22b rev 2: this project is the repository
    cfg = root / "config"
    (cfg / "risk.json").write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.6,
                                               "max_open_positions": 5, "max_orders_per_day": 10}))
    (cfg / "webull_live.json").write_text(json.dumps({
        "account_class": "INDIVIDUAL_CASH", "account_ends": LIVE_ACCOUNT[-4:], "confirmed_by": "Burton",
        "max_order_value": 3000, "max_capital": 100000, "valid_until": "2026-11-20",
        "i_understand_this_trades_real_money": True}))
    (cfg / "go_live.json").write_text(json.dumps({
        "min_paper_broker_days": 1, "min_reconciled_fills": 1, "min_days_since_a_stopped_run": 1,
        "fills_checked_by_hand": 1, "min_fills_checked_by_hand": 1, "confirmed_by": "Burton"}))
    for p in cfg.iterdir():
        p.chmod(0o644)
    with (root / "journal" / "2026-11-12.jsonl").open("a") as fh:
        for e in ({"event": "fill", "fill_id": "f1"}, {"event": "summary", "status": "done", "stage": None}):
            fh.write(json.dumps({"timestamp": "2026-11-12T15:00:00+00:00", "day": "2026-11-12", "type": "order_step",
                                 "venue": "webull-sandbox", "batch": "paper-1", **e}) + "\n")
    _followed_book(root)                                    # T32: live mode follows only what broker_book.json names
    venue = _LiveVenue()
    built, asked = [], []
    monkeypatch.setattr(cli, "_live_environ", lambda: dict(LIVE_KEYS))             # never the secrets file
    monkeypatch.setattr(cli, "_order_step_live_venue", lambda files, environ: built.append(files) or venue)
    monkeypatch.setattr(cli, "_attended", lambda: True)                              # T50: a person at a terminal
    monkeypatch.setattr(second_quote, "second_opinion",
                        lambda price, symbol, **kw: asked.append(symbol) or {"verdict": "agree"})
    monkeypatch.setattr(webull, "ny_date", lambda t=None: date(2026, 11, 13))
    return root, venue, built, asked


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize("argv", [["--live"], ["--live", "--propose"], ["--live", "--approve", "123456"],
                                  ["--live", "--propose", "--capital", "100000", "--fractional"],
                                  ["--live", "--approve", "123456", "--capital", "100000", "--fractional"],
                                  ["--live", "--propose", "--capital", "nan"], ["--live", "--propose", "--capital", "-5"],
                                  ["--check"], ["--check", "--capital", "5"]])
def test_live_flags_that_cannot_run_are_refused_before_anything_is_read(live_project, monkeypatch, capsys, argv):
    root, venue, built, asked = live_project
    monkeypatch.setattr(cli, "_live_environ", lambda: pytest.fail("the environment was loaded"))
    before = _tree(root)
    assert cli.main(["--root", str(root), "order-step", *argv]) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and len(err.strip().splitlines()) == 1
    assert built == [] and venue.calls == [] and asked == [] and _tree(root) == before


def test_live_propose_and_approve_on_the_command_line(live_project, capsys):
    root, venue, built, asked = live_project
    assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 0
    captured = capsys.readouterr()
    out, number_lines = _ticket_out(captured.out)
    assert out["status"] == "done" and out["live"] is True and out["mode"] == "propose" and "submit" not in venue.calls
    assert [(o["fund"], o["side"], o["units"]) for o in out["ticket"]["orders"]] == [("SPY", "buy", 5.0)]
    (line,) = number_lines
    number = line.split()[2]
    assert "order-step --live --approve NUMBER --capital 100000.0" in line and asked == ["SPY"]
    assert len(built) == 1 and built[0].confirmation == root / "config" / "webull_live.json"
    assert built[0].checklist == root / "config" / "go_live.json" and built[0].limits == root / "config" / "risk.json"
    assert cli.main(["--root", str(root), "order-step", "--live", "--approve", number, "--capital", "100000"]) == 0
    out, number_lines = _ticket_out(capsys.readouterr().out)
    assert number_lines == [] and out["orders_sent"] == 1 and venue.calls.count("submit") == 1 and asked == ["SPY", "SPY"]
    text = captured.out + captured.err + "".join(p.read_text() for p in (root / "journal").glob("*.jsonl"))
    assert LIVE_ACCOUNT[:-4] not in text


def test_live_check_prints_each_lock_and_the_account_only_when_locks_one_to_three_are_met(live_project, capsys):
    root, venue, built, asked = live_project
    before = _tree(root)
    assert cli.main(["--root", str(root), "order-step", "--live", "--check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [lock["lock"] for lock in report["locks"]] == [1, 2, 3, 4] and all(lock["met"] for lock in report["locks"])
    assert report["locks"][1]["figures"]["record"]["paper_broker_days"] == 1
    assert report["locks"][1]["figures"]["owner"]["min_paper_broker_days"] == 1
    assert report["locks"][1]["figures"]["owner"]["min_days_since_a_stopped_run"] == 1
    assert report["account"] == {"account_class": "INDIVIDUAL_CASH", "account_ends": LIVE_ACCOUNT[-4:],
                                 "matches_the_confirmation": True} and report["all_met"] is True
    assert "submit" not in venue.calls and asked == [] and _tree(root) == before          # read-only
    (root / "config" / "go_live.json").unlink()
    built.clear()
    assert cli.main(["--root", str(root), "order-step", "--live", "--check", "--capital", "200000"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert [lock["met"] for lock in report["locks"]] == [False, False, True, True]
    assert "(by 100,000.00)" in report["locks"][0]["reason"] and "is missing" in report["locks"][1]["reason"]
    assert report["account"] == "not read: locks 1 to 3 are not all met" and built == [] and report["all_met"] is False


def test_live_check_says_when_the_account_is_not_the_confirmations(live_project, capsys):
    root, venue, built, asked = live_project
    venue.account_id = "ZqLIVEACCOUNT9999"
    assert cli.main(["--root", str(root), "order-step", "--live", "--check"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["account"]["matches_the_confirmation"] is False and report["account"]["account_ends"] == "9999"
    assert "ZqLIVEACCOUNT" not in json.dumps(report)


@pytest.mark.parametrize("missing", ["webull_live.json", "go_live.json"])
def test_a_live_propose_with_a_lock_unmet_says_which_builds_nothing_and_creates_nothing(live_project, capsys, missing):
    root, venue, built, asked = live_project
    (root / "config" / missing).unlink()
    before = _tree(root)
    assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: lock ") and "is missing" in err and "nothing was read from the account" in err
    assert built == [] and venue.calls == [] and asked == [] and _tree(root) == before
    assert not (root / "config" / missing).exists()


def _verdicts(root):
    con = sqlite3.connect(root / "memory" / "order_step-webull-production.tickets.sqlite")
    try:
        return [r[0] for r in con.execute("SELECT verdict FROM verdicts ORDER BY rowid")]
    finally:
        con.close()


def test_a_live_approve_after_a_lock_stops_being_met_is_refused_and_marked_without_any_account_read(live_project, capsys):
    root, venue, built, asked = live_project
    assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 0
    _, (line,) = _ticket_out(capsys.readouterr().out)
    number = line.split()[2]
    conf = (root / "config" / "webull_live.json").read_text()
    (root / "config" / "webull_live.json").unlink()                 # the confirmation removed: lock 1 unmet
    calls_before = list(venue.calls)
    built.clear()
    assert cli.main(["--root", str(root), "order-step", "--live", "--approve", number, "--capital", "100000"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("refused: lock 1 (the confirmation)") and venue.calls == calls_before and built == []
    out = json.loads(captured.out)
    assert out["stage"] == "lock" and out["live"] is True and _verdicts(root) == ["refused"]
    (root / "config" / "webull_live.json").write_text(json.dumps({**json.loads(conf), "max_order_value": 2900}))
    (root / "config" / "webull_live.json").chmod(0o644)             # back, with a smaller cap: every lock met again
    assert cli.main(["--root", str(root), "order-step", "--live", "--approve", number, "--capital", "100000"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["stage"] == "ticket" and "changed since that ticket was made" in out["reason"]
    assert "submit" not in venue.calls and _verdicts(root) == ["refused", "refused"]


def test_the_live_venue_is_built_only_for_production_and_the_cash_account_with_the_owners_file(monkeypatch, tmp_path):
    """T50 (rewritten): the live venue is built from the live file only, with the account class of the owner's
    confirmation and no account id; nothing the environment holds changes what is built."""
    from valedia_trader.venues import webull
    monkeypatch.setattr(webull, "LIVE_REPO", tmp_path, raising=False)   # T22b rev 2: the repository, in-process
    monkeypatch.setattr(webull, "ny_date", lambda t=None: __import__("datetime").date(2026, 11, 13))
    files = cli._live_files(cli.LoopPaths(tmp_path), {})
    seen = []
    monkeypatch.setattr(webull.WebullVenue, "from_secrets", classmethod(lambda cls, *a, **kw: pytest.fail("from_secrets")))
    monkeypatch.setattr(webull.WebullVenue, "from_live_file",
                        classmethod(lambda cls, path=None, **kw: seen.append((path, kw)) or "v"))
    live = tmp_path / "secrets" / "webull-live.env"
    with pytest.raises(cli._LiveRefused, match="confirmation"):          # no confirmation: nothing is built
        cli._order_step_live_venue(files, dict(LIVE_KEYS), live_file=live)
    assert seen == []
    (tmp_path / "config").mkdir()
    conf = tmp_path / "config" / "webull_live.json"
    conf.write_text(json.dumps({"account_class": "INDIVIDUAL_CASH", "account_ends": LIVE_ACCOUNT[-4:],
                                "confirmed_by": "Burton", "max_order_value": 3000, "max_capital": 100000,
                                "valid_until": "2026-11-20", "i_understand_this_trades_real_money": True}))
    conf.chmod(0o644)
    for env in (dict(LIVE_KEYS), {**LIVE_KEYS, "WEBULL_ENV": " Production ", "WEBULL_ACCOUNT_ID": "Zq-practice-id"},
                {**LIVE_KEYS, "WEBULL_ACCOUNT_CLASS": "INDIVIDUAL_MARGIN"}, {"WEBULL_ACCOUNT_CLASS": "EVENTS_CASH"}, {}):
        assert cli._order_step_live_venue(files, env, live_file=live) == "v"
    assert seen == [(live, {"account_class": "INDIVIDUAL_CASH", "confirmation_file": conf})] * 5


def test_a_paper_order_step_never_enters_live_mode(order_step_project, monkeypatch):
    root, venue = order_step_project
    monkeypatch.setattr(cli, "live_order_step_command", lambda *a, **k: pytest.fail("live mode was entered"))
    assert cli.main(["--root", str(root), "order-step"]) == 0



# ---- T22b rev 2, outcomes 1, 2 and 4: live mode exists only for the repository (webull.LIVE_REPO); a --root, an
# environment variable, a working folder or an argument naming another folder opens nothing

def _no_reads(monkeypatch):
    from valedia_trader.execution_and_risk import go_live
    monkeypatch.setattr(cli, "_live_environ", lambda: pytest.fail("the environment was loaded"))
    monkeypatch.setattr(go_live, "judge_locks", lambda *a, **k: pytest.fail("an owner's file or the journal was read"))
    monkeypatch.setattr(cli, "_order_step_live_venue", lambda *a, **k: pytest.fail("a venue was built"))


@pytest.mark.parametrize("argv", [["--live", "--propose", "--capital", "100000"], ["--live", "--approve", "123456",
                                  "--capital", "100000"], ["--live", "--check"], ["--live", "--check", "--capital", "5"]])
def test_a_live_run_whose_root_is_another_folder_is_refused_at_the_flags_step(live_project, monkeypatch, capsys, argv,
                                                                                 tmp_path_factory):
    from valedia_trader.venues import webull
    root, venue, built, asked = live_project                     # every lock met under root
    repo = tmp_path_factory.mktemp("repo")
    monkeypatch.setattr(webull, "LIVE_REPO", repo, raising=False)                # the repository is another (empty) folder
    _no_reads(monkeypatch)
    before = _tree(root), _tree(repo)
    assert cli.main(["--root", str(root), "order-step", *argv]) == 2
    captured = capsys.readouterr()
    assert captured.err == "refused: live mode runs only from the repository itself (--root names another folder)\n"
    assert captured.out == "" and built == [] and venue.calls == [] and asked == [] and (_tree(root), _tree(repo)) == before


def test_a_live_root_that_is_the_repository_through_a_link_or_no_root_at_all_runs_as_before(live_project, capsys, tmp_path_factory):
    root, venue, built, asked = live_project
    link = tmp_path_factory.mktemp("links") / "repo"
    link.symlink_to(root, target_is_directory=True)
    for argv in (["--root", str(link)], ["--root", str(root) + "/."], []):
        built.clear()
        assert cli.main([*argv, "order-step", "--live", "--check"]) == 0, argv
        report = json.loads(capsys.readouterr().out)
        assert report["all_met"] is True and len(built) == 1 and built[0].confirmation == root / "config" / "webull_live.json"


def test_without_live_a_root_elsewhere_is_untouched(order_step_project, monkeypatch, capsys, tmp_path_factory):
    from valedia_trader.venues import webull
    root, venue = order_step_project
    monkeypatch.setattr(webull, "LIVE_REPO", tmp_path_factory.mktemp("repo"), raising=False)
    monkeypatch.setattr(cli, "live_order_step_command", lambda *a, **k: pytest.fail("live mode was entered"))
    assert cli.main(["--root", str(root), "order-step"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "done" and venue.book


_REAL_LIVE_VENUE = cli._order_step_live_venue                          # before live_project replaces it

EVERY_NAME = ["VALEDIA_ROOT", "VALEDIA_REPO", "VALEDIA_TRADER_ROOT", "VALEDIA_HOME", "REPO", "REPO_ROOT", "ROOT", "HOME",
              "PWD", "WEBULL_LIVE", "WEBULL_LIVE_FILE", "WEBULL_LIVE_CONFIRMATION", "WEBULL_CONFIRMATION",
              "WEBULL_CONFIRMATION_FILE", "LIVE_CONFIRMATION", "CONFIRMATION_FILE", "GO_LIVE", "GO_LIVE_FILE",
              "GO_LIVE_CHECKLIST", "CHECKLIST", "RISK", "RISK_FILE", "RISK_LIMITS", "LIMITS_FILE", "JOURNAL",
              "JOURNAL_DIR", "PAPER_JOURNAL", "CONFIG", "CONFIG_DIR", "LIVE_REPO", "VALEDIA_LIVE", "LIVE"]


def test_nothing_a_caller_passes_opens_live_mode_with_every_owner_file_met_in_another_folder(live_project, monkeypatch,
                                                                                               capsys, tmp_path_factory):
    import argparse as ap
    from types import SimpleNamespace

    from valedia_trader.execution_and_risk import go_live
    from valedia_trader.execution_and_risk import order_step as osx
    from valedia_trader.research_loop.paper import Journal
    from valedia_trader.venues import webull
    other, venue, built, asked = live_project                    # every lock met in this folder: the made-up one
    repo = tmp_path_factory.mktemp("repo")                        # the repository: no owner's file at all
    (repo / "config").mkdir()
    monkeypatch.setattr(webull, "LIVE_REPO", repo, raising=False)
    cfg = other / "config"
    every = {name: str(other) for name in EVERY_NAME}
    every.update({n: str(cfg / "webull_live.json") for n in EVERY_NAME if "CONFIRM" in n or n.startswith("WEBULL_LIVE")})
    every.update({n: str(cfg / "go_live.json") for n in EVERY_NAME if "GO_LIVE" in n or n == "CHECKLIST"})
    every.update({n: str(cfg / "risk.json") for n in EVERY_NAME if "RISK" in n or "LIMITS" in n})
    every.update({n: str(other / "journal") for n in EVERY_NAME if "JOURNAL" in n})
    for name, value in every.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(other)
    env = {**LIVE_KEYS, **every}
    monkeypatch.setattr(cli, "_live_environ", lambda: env)
    before = _tree(other), _tree(repo)
    sent = []

    class Recorder:                                               # a client that records any use of itself
        def __getattr__(self, name):
            sent.append(name)
            raise AssertionError(f"the client was used: {name}")

    # the command line: --root the other folder, every flag, every environment variable
    for argv in (["--live", "--propose", "--capital", "100000"], ["--live", "--approve", "123456", "--capital", "100000"],
                 ["--live", "--check"]):
        assert cli.main(["--root", str(other), "order-step", *argv]) == 2
        assert "runs only from the repository itself" in capsys.readouterr().err
    # the command line without --root: the repository's files, none written, so nothing opens
    assert cli.main(["order-step", "--live", "--propose", "--capital", "100000"]) == 2
    err = capsys.readouterr().err
    assert "lock 1 (the confirmation): config/webull_live.json is missing" in err and "lock 2 (" in err
    assert cli.main(["order-step", "--live", "--check"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert [lock["met"] for lock in report["locks"]][:2] == [False, False] and report["all_met"] is False
    assert "is missing" in report["locks"][0]["reason"] and "is missing" in report["locks"][1]["reason"]
    # the command function given the other folder's paths, the environment and a venue builder
    args = ap.Namespace(live=True, check=False, propose=True, approve=None, capital=100000.0, fractional=False)
    assert cli.live_order_step_command(args, cli.LoopPaths(other), environ=env,
                                       build_venue=lambda *a: pytest.fail("a venue was built"),
                                       second_opinion=lambda *a, **k: pytest.fail("a second source was asked")) == 2
    assert "runs only from the repository itself" in capsys.readouterr().err
    # the step itself, given the other folder's limits and journal and an object naming its files
    pointing = SimpleNamespace(confirmation=cfg / "webull_live.json", checklist=cfg / "go_live.json",
                               limits=cfg / "risk.json", journal=other / "journal", root=other,
                               paper_venue=go_live.PAPER_VENUE, environ=env, calendar=None)
    led = osx.open_ledger(tmp_path_factory.mktemp("led") / "orders.sqlite", clock=lambda: 0.0)
    try:
        for live in (pointing, go_live.LiveFiles(environ=env)):
            out = osx.run_order_step(ledger=led, venue=venue, paper=lambda: pytest.fail("the paper book was read"),
                                     quotes=lambda s: pytest.fail("a quote was asked"), journal=Journal(other / "journal"),
                                     limits_file=cfg / "risk.json", state=tmp_path_factory.mktemp("st") / "s.sqlite",
                                     clock=lambda: 0.0, halt_file=None, latch_file=None, capital=100000.0,
                                     fractional=False, live=live, second_opinion=lambda *a, **k: pytest.fail("asked"),
                                     propose=True)
            assert out["status"] == "stopped" and out["stage"] == "lock", out
    finally:
        led.close()
    # the locks module given that object
    state = go_live.judge_locks(pointing, webull.ny_date(), 100000.0)
    assert [lock["met"] for lock in state.locks[:2]] == [False, False] and state.confirmation is None
    # the function that builds the real production venue, given that object: only the repository's own path
    seen = []                                                     # T50 (rewritten): the live file's builder
    with monkeypatch.context() as m:
        m.setattr(webull, "live_confirmation", lambda path=None, *a, **k: (seen.append(path) or None, "is missing"))
        m.setattr(webull.WebullVenue, "from_live_file", classmethod(lambda cls, *a, **kw: pytest.fail("built")))
        with pytest.raises(cli._LiveRefused):
            _REAL_LIVE_VENUE(pointing, env)
    assert seen == [repo / "config" / "webull_live.json"]
    # the adapter given the other folder's confirmation
    v = webull.WebullVenue("k", "s", environment="production", client=Recorder(), confirmation_file=cfg / "webull_live.json")
    with pytest.raises(webull.LiveTradingNotConfirmed):
        v.submit("SPY", 1.0, "t", price=100.0)
    assert webull.live_confirmation(cfg / "webull_live.json") == (None, webull.NOT_OWN_CONFIRMATION)
    assert webull.load_live_confirmation(cfg / "webull_live.json") is None
    assert built == [] and venue.calls == [] and asked == [] and sent == []
    assert (_tree(other), _tree(repo)) == before
    assert not list(repo.rglob("webull_live.json")) and not list(repo.rglob("go_live.json"))


# ---- T22c item 5: live mode runs with the repository's own folders (cli.py's LoopPaths(webull.LIVE_REPO)), and the
# root is compared with links resolved on both sides (os.path.realpath)

def _capture_run_live(monkeypatch):
    seen = []
    real = cli._run_live

    def capture(args, paths, venue, files, second_opinion):
        seen.append(paths)
        return real(args, paths, venue, files, second_opinion)

    monkeypatch.setattr(cli, "_run_live", capture)
    return seen


def test_live_mode_runs_with_the_repositorys_own_folders_whatever_paths_it_is_given(live_project, monkeypatch, capsys,
                                                                                   tmp_path_factory):
    import os
    from pathlib import Path
    root, venue, built, asked = live_project
    elsewhere = tmp_path_factory.mktemp("elsewhere")

    class Pointing(cli.LoopPaths):                               # the repository's root, every folder somewhere else
        journal = property(lambda self: elsewhere / "journal")
        memory = property(lambda self: elsewhere / "memory")
        config = property(lambda self: elsewhere / "config")

    link = tmp_path_factory.mktemp("links") / "repo"
    link.symlink_to(root, target_is_directory=True)
    seen = _capture_run_live(monkeypatch)
    args = argparse.Namespace(live=True, check=False, propose=True, approve=None, capital=100000.0, fractional=False)
    for given in (Pointing(root), cli.LoopPaths(link), cli.LoopPaths(Path(str(root) + "/."))):
        assert cli.live_order_step_command(args, given, environ=dict(LIVE_KEYS)) == 0, given
        capsys.readouterr()
        paths = seen[-1]
        assert type(paths) is cli.LoopPaths and paths.root == Path(os.path.abspath(root)), paths
    assert list(elsewhere.rglob("*")) == []                      # nothing written through the given object's folders
    assert (root / "memory" / "order_step-webull-production.tickets.sqlite").is_file()


def test_a_live_root_is_compared_with_links_resolved_on_both_sides(live_project, monkeypatch, tmp_path_factory):
    from valedia_trader.venues import webull
    root, venue, built, asked = live_project
    links = tmp_path_factory.mktemp("links")
    (links / "repo").symlink_to(root, target_is_directory=True)
    (links / "other").symlink_to(tmp_path_factory.mktemp("other"), target_is_directory=True)
    assert cli._live_root_refusal(cli.LoopPaths(links / "repo")) is None           # the root given through a link
    monkeypatch.setattr(webull, "LIVE_REPO", links / "repo", raising=False)
    assert cli._live_root_refusal(cli.LoopPaths(root)) is None                      # the repository's name is the link
    assert cli._live_root_refusal(cli.LoopPaths(links / "repo")) is None
    assert cli._live_root_refusal(cli.LoopPaths(links / "other")) is not None       # a link to another folder
    monkeypatch.setattr(webull, "LIVE_REPO", root, raising=False)
    args = argparse.Namespace(live=True, check=True, propose=False, approve=None, capital=None, fractional=False)
    assert cli.live_order_step_command(args, cli.LoopPaths(links / "repo"), environ=dict(LIVE_KEYS)) == 0
    assert cli.live_order_step_command(args, cli.LoopPaths(links / "other"), environ=dict(LIVE_KEYS)) == 2


# ---- T32: the broker book follows the strategies config/broker_book.json names (hypothesis ids, named through the
# ledger and the strategy files as the paper command names them, and found in the paper book's latest day)

H23, H26 = "trend_blend_etf_1999_2011", "hold_rebalance_etf_2006_2026"


@pytest.fixture
def follow_project(order_step_project):
    """The order step project with the paper book of today: hypotheses 23 and 26 in the ledger with their strategy
    files, each stepped on half the paper equity; 24 in the ledger without a strategy file; 25 with a strategy that
    is not in the paper book's latest day."""
    root, venue = order_step_project
    state = json.loads((root / "journal" / "paper_state.json").read_text())
    state["positions"] = {H23: {"SPY": {"units": 4.0, "cost": 500.0}}, H26: {"QQQ": {"units": 6.0, "cost": 400.0}}}
    state["pending"] = []
    state["history"] = [{"day": "2026-11-12", "equity": 100_000.0, "capital_per_strategy": 50_000.0,
                         "strategy_values": {H23: 2_000.0, H26: 2_400.0}}]
    (root / "journal" / "paper_state.json").write_text(json.dumps(state))
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        ids = [led.propose(f"Idea number {i} about a different market effect entirely", "test", cli.MARKET)
               for i in range(26)]
        for hid in (23, 24, 25, 26):                        # rev 2: hypotheses the paper command steps
            led.set_status(hid, "passed backtest only" if hid == 23 else "passed paper")
    finally:
        led.close()
    assert ids[22] == 23 and ids[25] == 26
    _strategy_file(root, 23, H23, ("SPY",))
    _strategy_file(root, 26, H26, ("QQQ",))
    _strategy_file(root, 25, "elsewhere_strategy", ("SPY",))
    return root, venue


def _book_file(root, value, mode=0o644):
    return _owner_file(root / "config" / "broker_book.json", value, mode)


def test_t32_without_the_file_the_order_step_follows_the_whole_book_as_before(follow_project, capsys):
    root, venue = follow_project
    assert cli.main(["--root", str(root), "order-step"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted((s["symbol"], s["units"]) for s in out["sent"]) == [("QQQ", 6.0), ("SPY", 4.0)] and "follow" not in out
    assert not (root / "config" / "broker_book.json").exists()


@pytest.mark.parametrize("capital, units", [(None, 12.0), (50_000.0, 6.0), (20_000.0, 2.0)])
def test_t32_following_26_sends_only_its_units_on_its_own_share(follow_project, capsys, capital, units):
    root, venue = follow_project
    _book_file(root, {"follow": [26], "note": "the owner's choice of 2026-11-14"})       # other keys are ignored
    argv = ["--root", str(root), "order-step"] + ([] if capital is None else ["--capital", str(capital)])
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert [(s["symbol"], s["units"]) for s in out["sent"]] == [("QQQ", units)]          # nothing of 23's SPY
    assert out["gaps"]["QQQ"]["target"] == 6.0 * ((100_000.0 if capital is None else capital) / 50_000.0)
    assert out["follow"] == {"ids": [26], "names": [H26]}
    journal = [json.loads(ln) for ln in (root / "journal" / "2026-11-13.jsonl").read_text().splitlines()]
    (summary,) = [e for e in journal if e.get("event") == "summary"]
    assert summary["follow"] == {"ids": [26], "names": [H26]}


def test_t32_following_both_ids_sends_what_the_whole_book_sends(follow_project, capsys):
    root, venue = follow_project
    _book_file(root, {"follow": [26, 23]})
    assert cli.main(["--root", str(root), "order-step"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted((s["symbol"], s["units"]) for s in out["sent"]) == [("QQQ", 6.0), ("SPY", 4.0)]
    assert out["follow"] == {"ids": [26, 23], "names": [H26, H23]}


BAD_BOOK_FILES = {
    "not an object": ([26], "must be a JSON object"),
    "follow missing": ({"strategies": [26]}, 'has no "follow" list'),
    "empty": ({"follow": []}, "at least one id is needed"),
    "not a list": ({"follow": 26}, 'must be a list of hypothesis ids'),
    "a bool": ({"follow": [True]}, "true or false"),
    "text": ({"follow": ["26"]}, 'the text "26"'),
    "a float": ({"follow": [26.0]}, "the number 26.0"),
    "a negative": ({"follow": [-26]}, "holds -26; a hypothesis id is a whole number from 1"),
    "zero": ({"follow": [0]}, "holds 0;"),
    "huge": ({"follow": [10**30]}, "a whole number from 1"),
    "a repeat": ({"follow": [26, 26]}, "names hypothesis 26 more than once"),
    "null": ({"follow": None}, "not null"),
    "an id unknown to the ledger": ({"follow": [26, 999]}, "hypothesis 999, which the research ledger does not hold"),
    "an id whose strategy file is missing": ({"follow": [24]}, "hypothesis 24, whose strategy cannot be loaded"),
    "an id not in the paper book's latest day": ({"follow": [26, 25]}, "not among the strategies of the paper book's "
                                                                       "latest day (2026-11-12"),
    "a repeated key": ('{"follow": [26], "follow": [23]}', "repeats a key"),
    "NaN": ('{"follow": [NaN]}', "NaN"),
    "not JSON": ("follow: [26]", "is not plain JSON"),
    "not UTF-8": (b"\xff\xfe{}", "not UTF-8"),
}


@pytest.mark.parametrize("how", sorted(BAD_BOOK_FILES) + ["a link", "a link above it", "a second name",
                                                         "writable by others", "writable by its group", "a folder"])
def test_t32_a_bad_broker_book_file_or_id_stops_the_order_step_with_nothing_sent_changed_or_cancelled(
        follow_project, capsys, tmp_path_factory, how):
    import os
    root, venue = follow_project
    assert cli.main(["--root", str(root), "order-step"]) == 0                             # open orders, as before
    capsys.readouterr()
    book_before = json.dumps(venue.book, sort_keys=True)
    venue.calls.clear()
    path = root / "config" / "broker_book.json"
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    words = {"a link": "symbolic link", "a link above it": "symbolic link", "a second name": "more than one name",
             "writable by others": "writable by its group or by others",
             "writable by its group": "writable by its group or by others", "a folder": "not a regular file"}
    if how in BAD_BOOK_FILES:
        value, words = BAD_BOOK_FILES[how]
        if isinstance(value, bytes):
            path.write_bytes(value)
            path.chmod(0o644)
        else:
            _book_file(root, value)
    else:
        words = words[how]
        real = _owner_file(elsewhere / "broker_book.json", {"follow": [26]})
        if how == "a link":
            path.symlink_to(real)
        elif how == "a link above it":                               # config/ itself a link to a folder elsewhere
            (root / "config").rename(elsewhere / "config")
            (root / "config").symlink_to(elsewhere / "config", target_is_directory=True)
            _owner_file(elsewhere / "config" / "broker_book.json", {"follow": [26]})
        elif how == "a second name":
            os.link(real, path)
        elif how == "a folder":
            path.mkdir()
        else:
            _book_file(root, {"follow": [26]}, 0o646 if how == "writable by others" else 0o664)
    code = cli.main(["--root", str(root), "order-step"])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert code == 2 and out["status"] == "stopped" and out["stage"] == "follow", out
    assert "config/broker_book.json" in out["reason"] and words in out["reason"], out["reason"]
    assert "nothing is sent" in out["reason"] and "follow" not in out
    assert [c for c in venue.calls if c in ("submit", "cancel", "positions", "cash", "order_status")] == []
    assert json.dumps(venue.book, sort_keys=True) == book_before                         # nothing sent, changed or cancelled
    journal = [json.loads(ln) for ln in (root / "journal" / "2026-11-13.jsonl").read_text().splitlines()]
    assert journal[-1]["event"] == "summary" and journal[-1]["stage"] == "follow"


def test_t32_the_reading_function_names_ids_through_the_ledger_and_the_strategy_files(follow_project):
    from valedia_trader.execution_and_risk import order_step
    root, venue = follow_project
    paths = cli.LoopPaths(root)
    assert cli.broker_book_follow(paths) is None                                           # no file: the whole book
    _book_file(root, {"follow": [26]})
    f = cli.broker_book_follow(paths)
    assert type(f) is order_step.Follow and f.ids == (26,) and f.names == (H26,) and f.refused is None
    assert len(f.digest) == 64
    _book_file(root, {"follow": [26], "note": "changed"})
    assert cli.broker_book_follow(paths).digest != f.digest                                # the file is in the digest
    (root / "config" / "broker_book.json").unlink()
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        assert cli.broker_book_follow(paths, led) is None
        _book_file(root, {"follow": [23]})
        assert cli.broker_book_follow(paths, led).names == (H23,)
    finally:
        led.close()


# ---- T32 in live mode: no file, or a refused one, stops before any venue, quote or second source; the proposal
# shows what is followed; a change of the file between propose and approve stops the approve

@pytest.mark.parametrize("how", ["missing", "empty list", "unknown id", "a link"])
def test_t32_a_live_run_without_a_usable_broker_book_file_is_refused_before_any_venue(live_project, capsys,
                                                                                       tmp_path_factory, how):
    root, venue, built, asked = live_project
    path = root / "config" / "broker_book.json"
    path.unlink()
    if how == "empty list":
        _book_file(root, {"follow": []})
    elif how == "unknown id":
        _book_file(root, {"follow": [7]})
    elif how == "a link":
        path.symlink_to(_owner_file(tmp_path_factory.mktemp("elsewhere") / "b.json", {"follow": [1]}))
    before = _tree(root)
    assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("refused: config/broker_book.json ") and len(captured.err.strip().splitlines()) == 1
    assert {"missing": "never follows the whole book", "empty list": "at least one id", "unknown id": "hypothesis 7",
            "a link": "symbolic link"}[how] in captured.err
    assert "nothing was read from the account" in captured.err and captured.out == ""
    assert built == [] and venue.calls == [] and asked == [] and _tree(root) == before


def test_t32_a_live_proposal_shows_what_it_follows(live_project, capsys):
    root, venue, built, asked = live_project
    assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 0
    out, _ = _ticket_out(capsys.readouterr().out)
    assert out["follow"] == {"ids": [1], "names": ["mom_paper"]} and out["ticket"]["follow"] == out["follow"]
    journal = [json.loads(ln) for ln in (root / "journal" / "2026-11-13.jsonl").read_text().splitlines()]
    (ticket,) = [e for e in journal if e.get("event") == "ticket"]
    assert ticket["follow"] == {"ids": [1], "names": ["mom_paper"]}


@pytest.mark.parametrize("change", ["removed", "another key", "refused"])
def test_t32_a_live_approve_after_the_broker_book_file_changed_is_refused_and_marked(live_project, capsys, change):
    root, venue, built, asked = live_project
    assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 0
    _, (line,) = _ticket_out(capsys.readouterr().out)
    number = line.split()[2]
    path = root / "config" / "broker_book.json"
    if change == "removed":
        path.unlink()
    elif change == "another key":
        _book_file(root, {"follow": [1], "note": "the same list, another file"})
    else:
        _book_file(root, {"follow": [1, 1]})
    calls_before = list(venue.calls)
    built.clear()
    assert cli.main(["--root", str(root), "order-step", "--live", "--approve", number, "--capital", "100000"]) == 2
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    if change == "another key":                                    # still a usable file: the ticket's check refuses it
        assert out["stage"] == "ticket" and "broker book" in out["reason"] and "changed since" in out["reason"]
    else:                                                          # no usable file: refused before any venue is built
        assert captured.err.startswith("refused: config/broker_book.json ") and built == []
        assert out["stage"] == "lock" and venue.calls == calls_before
    assert "submit" not in venue.calls and _verdicts(root) == ["refused"]


# ==== T32 rev 2

def _set_status(root, hid, status):
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        led.set_status(hid, status)
    finally:
        led.close()


# ---- outcome 2: a followed id must be a hypothesis the paper command steps (cli.PAPER_STATUSES)

@pytest.mark.parametrize("status", ["paused", "failed", "untested", "retired"])
def test_t32_rev2_a_followed_hypothesis_the_paper_command_does_not_step_stops_the_order_step(follow_project, capsys,
                                                                                               status):
    root, venue = follow_project
    assert cli.main(["--root", str(root), "order-step"]) == 0                             # open orders, as before
    capsys.readouterr()
    book_before = json.dumps(venue.book, sort_keys=True)
    venue.calls.clear()
    _set_status(root, 23, status)
    _book_file(root, {"follow": [26, 23]})
    assert cli.main(["--root", str(root), "order-step"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "stopped" and out["stage"] == "follow", out
    assert f"hypothesis 23, whose status in the research ledger is {status}" in out["reason"], out
    assert [c for c in venue.calls if c[0] in ("submit", "cancel")] == [] and json.dumps(venue.book, sort_keys=True) == book_before
    f = cli.broker_book_follow(cli.LoopPaths(root))                                        # without the paper book too
    assert f.refused is not None and f"whose status in the research ledger is {status}" in f.refused


def test_t32_rev2_each_status_the_paper_command_trades_is_followed(follow_project):
    root, venue = follow_project
    _book_file(root, {"follow": [23, 26]})
    for status in cli.PAPER_STATUSES:
        _set_status(root, 23, status)
        f = cli.broker_book_follow(cli.LoopPaths(root))
        assert f.refused is None and f.names == (H23, H26), (status, f)


# ---- outcome 1: in live mode every refusal of the follow list comes before the production venue is built

def _secrets_watch(monkeypatch):
    from valedia_trader.core import secrets
    from valedia_trader.venues import webull
    seen = {"secret": [], "load_env": [], "from_secrets": [], "client": []}
    monkeypatch.setattr(cli, "_order_step_live_venue", REAL_LIVE_VENUE)               # the command's own builder
    monkeypatch.setattr(secrets, "secret", lambda name, *a, **k: seen["secret"].append(name) or "x" * 16)
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: seen["load_env"].append(1) or [])
    monkeypatch.setattr(secrets, "read_live_pair", lambda *a, **k: seen["secret"].append("live file") or ("x" * 16,) * 2)
    real = webull.WebullVenue.from_secrets
    monkeypatch.setattr(webull.WebullVenue, "from_secrets",
                        classmethod(lambda cls, *a, **k: seen["from_secrets"].append(1) or real.__func__(cls, *a, **k)))
    monkeypatch.setattr(webull.WebullVenue, "__init__", lambda self, *a, **k: seen["client"].append(1)
                        or (_ for _ in ()).throw(RuntimeError("no client in this test")))
    return seen


def _second_book(root, name="mom_two", status="passed paper", in_latest=True):
    """A second hypothesis (2) with its strategy file; its strategy in the paper book's latest day, or not."""
    state = json.loads((root / "journal" / "paper_state.json").read_text())
    if in_latest:
        state["positions"][name] = {"QQQ": {"units": 2.0, "cost": 100.0}}
        state["history"][-1]["strategy_values"][name] = 200.0
    (root / "journal" / "paper_state.json").write_text(json.dumps(state))
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        hid = led.propose("Funds that fell for a week bounce back the next one", "test", cli.MARKET)
        led.set_status(hid, status)
    finally:
        led.close()
    _strategy_file(root, hid, name, ("QQQ",))
    return hid


@pytest.mark.parametrize("how", ["not in the latest day", "renamed out of it", "paused", "retired", "unknown id"])
@pytest.mark.parametrize("mode", ["propose", "approve"])
def test_t32_rev2_a_live_follow_refusal_comes_before_the_venue_is_built(live_project, capsys, monkeypatch, how, mode):
    root, venue, built, asked = live_project
    number = None
    if mode == "approve":
        assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 0
        _, (line,) = _ticket_out(capsys.readouterr().out)
        number = line.split()[2]
    seen = _secrets_watch(monkeypatch)
    built.clear()
    calls, quotes = len(venue.calls), len(asked)
    if how == "not in the latest day":
        _book_file(root, {"follow": [1, _second_book(root, in_latest=False)]})
    elif how == "renamed out of it":
        spec_path = root / "strategies" / "mom_paper_h1.json"
        spec = json.loads(spec_path.read_text())
        spec_path.write_text(json.dumps({**spec, "name": "renamed_away"}))
    elif how in ("paused", "retired"):
        _set_status(root, 1, how)
    else:
        _book_file(root, {"follow": [1, 99]})
    argv = ["--live", "--propose"] if mode == "propose" else ["--live", "--approve", number]
    assert cli.main(["--root", str(root), "order-step", *argv, "--capital", "100000"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and "nothing was read from the account" in err, err
    words = {"not in the latest day": "not among the strategies of the paper book's latest day",
             "renamed out of it": "(renamed_away), which is not among the strategies",
             "paused": "whose status in the research ledger is paused",
             "retired": "whose status in the research ledger is retired",
             "unknown id": "hypothesis 99, which the research ledger does not hold"}[how]
    assert words in err, err
    assert seen == {"secret": [], "load_env": [], "from_secrets": [], "client": []}    # no secret, no client object
    assert built == [] and len(venue.calls) == calls and len(asked) == quotes        # nothing asked of any venue


# ---- outcome 5a: the ticket's digest holds the names (a strategy file renamed to another strategy of the book)

def test_t32_rev2_a_live_approve_after_a_followed_strategy_was_renamed_to_another_of_the_book_is_refused(live_project,
                                                                                                     capsys):
    root, venue, built, asked = live_project
    _second_book(root)                                             # mom_two is in the latest day, not followed
    assert cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"]) == 0
    out, (line,) = _ticket_out(capsys.readouterr().out)
    assert out["follow"] == {"ids": [1], "names": ["mom_paper"]}
    number = line.split()[2]
    spec_path = root / "strategies" / "mom_paper_h1.json"
    spec = json.loads(spec_path.read_text())
    spec_path.write_text(json.dumps({**spec, "name": "mom_two", "market": ["QQQ"]}))   # the same file, the same ids
    calls_before = len([c for c in venue.calls if c[0] == "submit"])
    assert cli.main(["--root", str(root), "order-step", "--live", "--approve", number, "--capital", "100000"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "stopped" and "broker book's follow list" in out["reason"] and "changed" in out["reason"], out
    assert len([c for c in venue.calls if c[0] == "submit"]) == calls_before


# ---- outcome 5b: the reading function refuses a strategy with no plain name

@pytest.mark.parametrize("name", ["two words", "semi;colon", "x" * 65, "dollar$", "", 26, None])
def test_t32_rev2_the_reading_function_refuses_a_strategy_without_a_plain_name(follow_project, monkeypatch, name):
    # a strategy file's spec allows only lower_snake_case names, so the stand-in is a strategy loaded otherwise (as a
    # strategy module can be: _strategy_for returns what it loads)
    root, venue = follow_project
    real = cli._strategy_for

    class Odd:
        pass

    def loaded(paths, ledger, hid, override):
        s, f = real(paths, ledger, hid, override)
        if hid != 26:
            return s, f
        odd = Odd()
        odd.name = name
        return odd, f
    monkeypatch.setattr(cli, "_strategy_for", loaded)
    _book_file(root, {"follow": [26]})
    f = cli.broker_book_follow(cli.LoopPaths(root))
    assert f.refused == "config/broker_book.json names hypothesis 26, whose strategy has no plain name", f
    assert f.names == () and f.digest is None


# ---- outcome 3, at the command line: `every` is set when the list names the whole latest day, and only then

def test_t32_rev2_the_reading_function_says_when_every_strategy_of_the_latest_day_is_followed(follow_project):
    root, venue = follow_project
    paths = cli.LoopPaths(root)
    trader = cli.PaperTrader(paths.journal, paths.risk_limits)
    for ids, every in (([26, 23], True), ([23, 26], True), ([26], False), ([23], False)):
        _book_file(root, {"follow": ids})
        assert cli.broker_book_follow(paths, trader=trader).every is every, ids
        assert cli.broker_book_follow(paths).every is False                       # without the paper book: never


# ---- T29 rev 7, outcome 3: a night the paper book cannot price stops every command that steps it, in one plain line

def _no_price_project(tmp_path, monkeypatch):
    """A project whose paper book (hypothesis 1, mom_paper, passed paper) was caught up by `paper-step` to bar 22 of
    `prices()` and holds both funds, its state written by a revision that keeps no closes (as a book from before rev 6
    is). `run(argv, frame)` runs the command line on that price frame and returns (status, out, err)."""
    import io
    import contextlib
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "config" / "risk.json").write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0,
                                                           "max_position_pct": 0.6, "max_open_positions": 5}))
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        hid = led.propose("Assets that rose over ten days keep rising", "test", cli.MARKET)
        led.set_status(hid, "passed paper")
    finally:
        led.close()
    _strategy_file(root, hid, "mom_paper", ("UP", "DOWN"), entry={"rule": "hold", "enter": 1, "exit": 0})   # T38-1b: DOWN held long
    frame = {}
    monkeypatch.setattr(cli, "_market", lambda args: (frame["prices"], None, None))

    def run(argv, prices):
        frame["prices"] = prices
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(["--root", str(root), *argv])
        return status, out.getvalue(), err.getvalue()

    px = prices()
    assert run(["paper-step"], px.iloc[:20])[0] == 0                       # a first run: bar 19 only
    assert run(["paper-step"], px.iloc[:23])[0] == 0                       # bars 20 to 22, the fills on bar 20
    state_file = root / "journal" / "paper_state.json"
    state = json.loads(state_file.read_text())
    assert state["last_day"] == str(px.index[22].date())
    assert {sym for book in state["positions"].values() for sym, lot in book.items() if lot["units"]} == {"UP", "DOWN"}
    for key in ("last_valid_prices", "last_valid_prices_as_of"):
        state.pop(key)
    state_file.write_text(json.dumps(state))
    bad = px.iloc[:26].astype(object)
    bad["DOWN"] = "n/a"                                                    # the held fund has no close at all
    return root, run, px, bad


def _book_files(root):
    journal = root / "journal"
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(journal.iterdir()) if p.name != "paper_state.lock"}


def _refused_plainly(status, out, err, day):
    assert status == 2 and out == "" and "Traceback" not in err
    assert err.endswith("\n") and err.count("\n") == 1, err                # one line
    assert err.startswith(f"refused: the paper book was not run for {day}:")
    assert "DOWN" in err and err.rstrip("\n").endswith("restore the price data for DOWN and run it again")


def test_paper_step_on_a_night_it_cannot_price_says_so_in_one_line_exits_2_and_keeps_the_nights_before(tmp_path, monkeypatch):
    """The paper command's catch-up (no venue): the refused night and those after it are not run, the book keeps the
    nights caught up before (every file as it was, bytes and times); with the data back it runs them all."""
    root, run, px, bad = _no_price_project(tmp_path, monkeypatch)
    before = _book_files(root)
    _refused_plainly(*run(["paper-step"], bad), px.index[23].date())
    assert _book_files(root) == before
    assert PaperTrader(root / "journal", root / "config" / "risk.json").state["last_day"] == str(px.index[22].date())
    status, out, _ = run(["paper-step"], px.iloc[:26])
    assert status == 0 and json.loads(out)["days_processed"] == 3 and json.loads(out)["last"]["day"] == str(px.index[25].date())


def test_paper_step_with_the_alpaca_mirror_on_a_night_it_cannot_price_says_so_and_sends_nothing(tmp_path, monkeypatch):
    """The paper command with a broker mirror steps the latest day only: refused in one line, status 2, nothing sent
    to the mirror, the book as it was."""
    from valedia_trader.core import secrets
    root, run, px, bad = _no_price_project(tmp_path, monkeypatch)

    class Mirror:
        name, sent = "alpaca-standin", []

        def submit(self, *a, **k):
            self.sent.append(a)
            return {"status": "accepted"}

    mirror = Mirror()
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: [])
    monkeypatch.setattr(secrets, "secret", lambda name, *a: "PKTEST")
    monkeypatch.setattr(cli, "AlpacaPaperVenue", lambda key, secret: mirror)
    before = _book_files(root)
    _refused_plainly(*run(["paper-step", "--alpaca"], bad), px.index[25].date())
    assert mirror.sent == [] and _book_files(root) == before


def test_the_cycle_on_a_night_it_cannot_price_says_so_in_one_line_and_runs_no_review_after_it(tmp_path, monkeypatch):
    """The cycle's paper step is refused: one line, status 2, the earlier nights kept, and nothing after the paper
    step runs on the book that stopped short: no weekly review (asked for with --critic), no review file."""
    root, run, px, bad = _no_price_project(tmp_path, monkeypatch)
    reviews = []
    monkeypatch.setattr(cli, "Gauntlet", lambda *a, **k: object())         # nothing untested: the tests never run
    monkeypatch.setattr(cli.critic_mod, "weekly_review", lambda *a, **k: reviews.append(a) or {"path": "review"})
    before = _book_files(root)
    _refused_plainly(*run(["cycle", "--no-research", "--critic"], bad), px.index[23].date())
    assert reviews == [] and _book_files(root) == before and not list((root / "journal").glob("review-*"))
    status, out, _ = run(["cycle", "--no-research", "--critic"], px.iloc[:26])   # the data back: the whole cycle runs
    assert status == 0 and json.loads(out)["paper"]["days_processed"] == 3 and len(reviews) == 1


# ---- T38-1b: a strategy that can hold a short is not stepped in paper and cannot be followed at the broker

NOT_STEPPED = "can hold a short position: the paper book and its tax ledger do not handle shorts yet"
LONG_RULE = {"name": "mom_long", "version": "1.0.0", "hypothesis": "Assets that rose over ten days keep rising",
             "market": ["UP", "DOWN"],
             "entry": {"rule": "momentum", "lookback": 10, "enter": 0.03, "exit": 0.0, "direction": "long"},
             "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.5, "max_open_positions": 2, "stop_loss_pct": None}}
BOTH_RULE = {**LONG_RULE, "name": "mom_both", "entry": {**LONG_RULE["entry"], "direction": "both"}}
PAIRS_BOTH = {"kind": "pairs", "name": "pairs_both", "version": "1.0.0",
              "hypothesis": "Two related prices drift back together after they part", "pairs": [["UP", "DOWN"]],
              "signal": {"hedge": "ratio", "window": 5, "enter": 1.0, "exit": 0.2, "stop_z": None}, "legs": "both",
              "sizing": {"pair_weight": 0.10, "max_open_pairs": 1},
              "risk": {"max_position_pct": 0.10, "max_gross": 1.0, "stop_loss_pct": None}}
PAIRS_LONG = {**PAIRS_BOTH, "name": "pairs_long", "legs": "long"}
T38_TAX_RULES = {"jurisdiction": "made-up", "long_term": {"after_years": 1, "verified_on": None},          # made up: notes only
                 "wash_sale": {"window_days": 30, "applies_to": {"security": True, "crypto": False}, "verified_on": None},
                 "wash_sale_same_purchase": {"remainder_is_replacement": True, "verified_on": None},
                 "lot_method": {"method": "FIFO", "verified_on": None}}
NOTHING_TO_TRADE = "no strategy has passed its backtest and sealed test; nothing to paper trade\n"


def _t38_project(root, specs, statuses=None):
    """A project with risk limits, made-up tax rules and one idea per spec (in order, ids from 1), each at status
    "passed backtest only" unless `statuses` names another for its strategy's name, with its strategy file."""
    (root / "config").mkdir(parents=True)
    (root / "config" / "risk.json").write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0,
                                                           "max_position_pct": 0.6, "max_open_positions": 5}))
    (root / "config" / "tax_rules.json").write_text(json.dumps(T38_TAX_RULES))
    (root / "strategies").mkdir()
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    ids = []
    try:
        for spec in specs:
            hid = led.propose(f"{spec['hypothesis']} ({spec['name']})", "test", cli.MARKET)
            led.set_status(hid, (statuses or {}).get(spec["name"], "passed backtest only"))
            (root / "strategies" / f"{spec['name']}_h{hid}.json").write_text(json.dumps(spec))
            ids.append(hid)
    finally:
        led.close()
    return ids


def _book_bytes(root):
    return {p.name: p.read_bytes() for p in sorted((root / "journal").iterdir()) if p.name != "paper_state.lock"}


def _journal_entries(root):
    return [json.loads(line) for f in sorted((root / "journal").glob("*.jsonl")) for line in f.read_text().splitlines()]


@pytest.fixture
def t38(tmp_path, monkeypatch):
    """`run(root, argv, prices)` runs the command line on that price frame and returns (status, out, err); journal
    lines carry a fixed clock so that two books compare byte for byte. `reviews` collects the names each weekly
    review is given (the review itself is a stand-in)."""
    import contextlib
    import io
    from datetime import datetime

    from valedia_trader.research_loop import paper, risk_gate

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 3, 2, 21, 0, tzinfo=tz)

    monkeypatch.setattr(paper, "datetime", FixedClock)
    monkeypatch.setattr(risk_gate, "HALT_FILE", tmp_path / "no-halt.json")
    frame, reviews = {}, []
    monkeypatch.setattr(cli, "_market", lambda args: (frame["prices"], None, None))
    monkeypatch.setattr(cli, "Gauntlet", lambda *a, **k: object())                 # nothing untested: no tests run
    monkeypatch.setattr(cli.critic_mod, "weekly_review",
                        lambda ledger, journal, last, names, *a, **k: reviews.append(dict(names)) or {"path": "review",
                                                                                                    "findings": []})

    def run(root, argv, prices):
        frame["prices"] = prices
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(["--root", str(root), *argv])
        return status, out.getvalue(), err.getvalue()

    return run, reviews


CYCLE = ["cycle", "--no-research", "--critic"]


@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_only_the_long_only_idea_is_stepped_the_others_are_named_every_run_and_its_book_is_byte_identical(
        tmp_path, t38, argv):
    run, reviews = t38
    import shutil
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE])
    alone = [run(root, argv, px.iloc[:k]) for k in (20, 32)]
    assert [s for s, _, _ in alone] == [0, 0], alone
    assert [json.loads(out)["not_stepped"] for _, out, _ in alone] == [[], []]
    book_alone = _book_bytes(root)
    shutil.rmtree(root)
    assert _t38_project(root, [LONG_RULE, BOTH_RULE, PAIRS_BOTH]) == [1, 2, 3]       # the long-only idea keeps id 1
    reviews.clear()
    for k in (20, 32):                                                               # a first run and a second one
        status, out, err = run(root, argv, px.iloc[:k])
        assert status == 0, err
        got = json.loads(out)
        assert sorted(got["not_stepped"], key=lambda e: e["id"]) == [
            {"id": 2, "name": "mom_both", "reason": NOT_STEPPED}, {"id": 3, "name": "pairs_both", "reason": NOT_STEPPED}]
        assert (got if argv == ["paper-step"] else got["paper"])["capital_shared_by"] == 1
    assert _book_bytes(root) == book_alone                                           # journal and state, byte for byte
    state = json.loads((root / "journal" / "paper_state.json").read_text())
    assert set(state["positions"]) == {"mom_long"} and set(state["history"][-1]["strategy_values"]) == {"mom_long"}
    entries = _journal_entries(root)
    assert any(e.get("type") == "fill" for e in entries)                             # the long-only book did trade
    assert not any("unavailable" in json.dumps(e.get("tax")) for e in entries if "tax" in e)   # tax notes stay on
    if argv == CYCLE:                                                                # the review names what it did before
        assert reviews == [{1: "mom_long", 2: "mom_both", 3: "pairs_both"}] * 2


def test_t38_1b_a_long_leg_pairs_idea_is_stepped(tmp_path, t38):
    run, _ = t38
    root = tmp_path / "project"
    _t38_project(root, [PAIRS_LONG])
    status, out, err = run(root, ["paper-step"], prices().iloc[:20])
    assert status == 0, err
    got = json.loads(out)
    assert got["not_stepped"] == [] and got["capital_shared_by"] == 1 and got["days_processed"] == 1
    state = json.loads((root / "journal" / "paper_state.json").read_text())
    assert set(state["history"][-1]["strategy_values"]) == {"pairs_long"}


def test_t38_1b_with_nothing_stepped_the_sentence_is_unchanged_and_what_was_left_out_follows_it(tmp_path, t38):
    run, _ = t38
    px = prices().iloc[:20]
    empty = tmp_path / "empty"
    _t38_project(empty, [])
    assert run(empty, ["paper-step"], px) == (0, NOTHING_TO_TRADE, "")              # nothing left out: as before
    root = tmp_path / "project"
    ids = _t38_project(root, [BOTH_RULE])
    status, out, err = run(root, ["paper-step"], px)
    said = "nothing is stepped: 1 idea can hold a short position\n"                  # addendum 2, ruling F
    assert status == 0 and err == "" and out.startswith(said), (status, out, err)
    assert json.loads(out[len(said):]) == {"not_stepped": [{"id": ids[0], "name": "mom_both", "reason": NOT_STEPPED}]}
    status, out, err = run(root, CYCLE, px)
    got = json.loads(out)
    assert status == 0 and got["paper"] == "nothing is stepped: 1 idea can hold a short position", (status, out, err)
    assert got["not_stepped"] == [{"id": ids[0], "name": "mom_both", "reason": NOT_STEPPED}]
    assert not (root / "journal" / "paper_state.json").exists()


def test_t38_1b_the_alpaca_mirror_run_names_what_was_left_out(tmp_path, t38, monkeypatch):
    from valedia_trader.core import secrets
    run, _ = t38

    class Mirror:
        name, sent = "alpaca-standin", []

        def submit(self, *a, **k):
            self.sent.append(a)
            return {"status": "accepted"}

    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: [])
    monkeypatch.setattr(secrets, "secret", lambda name, *a: "PKTEST")
    monkeypatch.setattr(cli, "AlpacaPaperVenue", lambda key, secret: Mirror())
    root = tmp_path / "project"
    ids = _t38_project(root, [LONG_RULE, BOTH_RULE])
    status, out, err = run(root, ["paper-step", "--alpaca"], prices().iloc[:20])
    assert status == 0, err
    got = json.loads(out)
    assert got["not_stepped"] == [{"id": ids[1], "name": "mom_both", "reason": NOT_STEPPED}] and got["capital_shared_by"] == 1


def _base_paper_step(root, px):
    """The paper step as the base commit ran it: every idea with a paper status stepped, whatever it can hold (the
    paper trader is unchanged; only the command's guard is new)."""
    paths = cli.LoopPaths(root)
    led = lg.HypothesisLedger(paths.ledger)
    try:
        every = {h["id"]: cli._strategy_for(paths, led, h["id"], None)[0] for s in cli.PAPER_STATUSES
                 for h in led.search(status=s)}
    finally:
        led.close()
    return cli.paper_catch_up(cli.paper_trader(paths), px, every)


def _state(root):
    return json.loads((root / "journal" / "paper_state.json").read_text())


def _held_lot_refusal(root, name):
    sym = next(s for s, lot in _state(root)["positions"][name].items() if lot["units"])
    return (f"refused: the paper book holds a lot of {name} ({sym}), which can hold a short and is no longer stepped; "
            "nothing was stepped\n")


@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_a_book_already_holding_a_left_out_strategy_stops_the_run_and_steps_nothing(tmp_path, t38, monkeypatch,
                                                                                            argv):
    run, reviews = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE, BOTH_RULE])
    _base_paper_step(root, px.iloc[:20])                                            # a book stepped before the guard
    _base_paper_step(root, px.iloc[:24])
    held = _state(root)["positions"]["mom_both"]
    assert any(lot["units"] < 0 for lot in held.values()), held                    # it holds a short
    before = _book_files(root)
    reviews.clear()
    for _ in range(2):
        assert run(root, argv, px.iloc[:30]) == (2, "", _held_lot_refusal(root, "mom_both"))
    assert _book_files(root) == before and reviews == []                           # bytes and times; no review after it


@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_a_book_with_only_zero_lots_under_a_left_out_name_runs(tmp_path, t38, argv):
    run, _ = t38
    px, root = prices(), tmp_path / "project"
    ids = _t38_project(root, [LONG_RULE, BOTH_RULE])
    assert run(root, ["paper-step"], px.iloc[:20])[0] == 0
    state_file = root / "journal" / "paper_state.json"
    state = json.loads(state_file.read_text())
    state["positions"]["mom_both"] = {"UP": {"units": 0.0, "cost": 0.0}, "DOWN": {"units": -0.0, "cost": 0.0}}
    state_file.write_text(json.dumps(state))
    status, out, err = run(root, argv, px.iloc[:24])
    assert status == 0, err
    assert json.loads(out)["not_stepped"] == [{"id": ids[1], "name": "mom_both", "reason": NOT_STEPPED}]
    assert json.loads(state_file.read_text())["last_day"] == str(px.index[23].date())


@pytest.mark.parametrize("argv", [["paper-step"], ["cycle", "--no-research"]], ids=["paper-step", "cycle"])
def test_t38_1b_a_strategy_file_that_cannot_be_loaded_refuses_the_run_as_before(tmp_path, t38, argv):
    run, _ = t38
    root = tmp_path / "project"
    ids = _t38_project(root, [BOTH_RULE, LONG_RULE])
    (root / "strategies" / f"mom_long_h{ids[1]}.json").unlink()
    assert run(root, argv, prices().iloc[:20]) == (2, "", f"refused: no strategy file found for hypothesis {ids[1]}; "
                                                         "pass --file\n")
    assert not (root / "journal" / "paper_state.json").exists()


def test_t38_1b_the_critic_command_names_what_it_named_before_through_the_same_builder(tmp_path, t38, monkeypatch):
    run, reviews = t38
    root = tmp_path / "project"
    ids = _t38_project(root, [LONG_RULE, BOTH_RULE, PAIRS_BOTH, PAIRS_LONG],
                       statuses={"mom_long": "passed paper", "pairs_both": "paused", "pairs_long": "paused"})
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        led.record_paper(ids[2], {"days": 5})                                       # paused with a paper record
        before = {h["id"]: cli._strategy_for(cli.LoopPaths(root), led, h["id"], None)[0].name   # the base commit's reader
                  for s in (*cli.PAPER_STATUSES, "paused") for h in led.search(status=s)
                  if h["paper_trading_result"] or s != "paused"}
    finally:
        led.close()
    calls = []
    real = cli.strategies_to_step
    monkeypatch.setattr(cli, "strategies_to_step", lambda *a, **k: calls.append(1) or real(*a, **k))
    status, out, err = run(root, ["critic"], prices())
    assert status == 0, err
    assert list(reviews[-1].items()) == list(before.items()) and set(before) == set(ids[:3]), (reviews, before)
    assert calls == [1]


T38_REFUSAL = ("config/broker_book.json names hypothesis 26, whose strategy can hold a short position; the order step "
               "sells only what the broker book owns, so it cannot be followed until shorts are built")


@pytest.mark.parametrize("live", [False, True], ids=["paper", "live"])
@pytest.mark.parametrize("kind", ["rule both", "rule short", "pairs both"])
def test_t38_1b_the_follow_list_refuses_a_strategy_that_can_hold_a_short(follow_project, monkeypatch, kind, live):
    from valedia_trader.execution_and_risk import order_step
    from valedia_trader.venues import webull
    root, _ = follow_project
    monkeypatch.setattr(webull, "LIVE_REPO", root, raising=False)
    if kind == "pairs both":
        (root / "strategies" / f"{H26}_h26.json").write_text(json.dumps({**PAIRS_BOTH, "name": H26,
                                                                         "pairs": [["QQQ", "SPY"]]}))
    else:
        _strategy_file(root, 26, H26, ("QQQ",), direction=kind.split()[1])
    paths = cli.LoopPaths(root)
    for ids in ([23, 26], [26]):
        _book_file(root, {"follow": ids})
        for trader in (None, cli.PaperTrader(paths.journal, paths.risk_limits)):
            assert cli.broker_book_follow(paths, live=live, trader=trader) == order_step.Follow(refused=T38_REFUSAL)


def test_t38_1b_the_order_step_stops_at_the_follow_stage_for_a_strategy_that_can_hold_a_short(follow_project, capsys):
    root, venue = follow_project
    _strategy_file(root, 24, "idea_twenty_four", ("SPY",))      # addendum 2 E: the whole book is judged, 24 included
    assert cli.main(["--root", str(root), "order-step"]) == 0                             # open orders, as before
    capsys.readouterr()
    book_before = json.dumps(venue.book, sort_keys=True)
    venue.calls.clear()
    _strategy_file(root, 26, H26, ("QQQ",), direction="both")                            # the old form of the file
    _book_file(root, {"follow": [23, 26]})
    assert cli.main(["--root", str(root), "order-step"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "stopped" and out["stage"] == "follow" and T38_REFUSAL in out["reason"], out
    assert [c for c in venue.calls if c in ("submit", "cancel")] == [] and json.dumps(venue.book, sort_keys=True) == book_before


# built with the base commit 09e3be3 (before T38-1b) on this same project, long-only strategy files
T38_BASE_DIGESTS = {(23, 26): "c9e3c8bc27a31e49fe3de362d07b0c40f05ce3afd66cfefaa7ec866a0ecb2fc2",
                    (26,): "8c87d49c7641fcc4f3628c91bfb0023a6b1871c1cc94f8006022717d21ecb167"}


def test_t38_1b_a_long_only_follow_list_is_accepted_exactly_as_on_the_base_commit(follow_project, monkeypatch):
    from valedia_trader.execution_and_risk import order_step
    from valedia_trader.venues import webull
    root, _ = follow_project
    monkeypatch.setattr(webull, "LIVE_REPO", root, raising=False)
    paths = cli.LoopPaths(root)
    names = {23: H23, 26: H26}
    for ids, digest in T38_BASE_DIGESTS.items():
        _book_file(root, {"follow": list(ids)})
        trader = cli.PaperTrader(paths.journal, paths.risk_limits)
        for live in (False, True):
            want = order_step.Follow(ids=ids, names=tuple(names[i] for i in ids), digest=digest, every=False)
            assert cli.broker_book_follow(paths, live=live) == want
            assert cli.broker_book_follow(paths, live=live, trader=trader) == order_step.Follow(
                ids=want.ids, names=want.names, digest=digest, every=ids == (23, 26))


# ---- T38-1b ruling 2 of the lead's check: the cycle refuses a held left-out book before it spends anything

def _spend_watch(monkeypatch):
    """Counts what a cycle spends before its paper step: price refreshes, prices read, research askers built, research
    rounds and gauntlets (the tests). The research round, when reached, records one idea in the ledger."""
    import types

    from valedia_trader.data_and_fees import prices as price_data
    spent = {"refresh": 0, "market": 0, "asker": 0, "research": 0, "gauntlet": 0}
    real_market = cli._market

    def count(key, value=None):
        spent[key] += 1
        return value

    def research(ledger, *a, **k):
        count("research")
        ledger.propose("A research round reached in this cycle proposed this idea", "test", cli.MARKET)
        return {"roles": {}}

    monkeypatch.setattr(price_data, "refresh", lambda data: count("refresh", {"refreshed": 0}))
    monkeypatch.setattr(cli, "_market", lambda args: count("market") or real_market(args))
    monkeypatch.setattr(cli, "_asker", lambda *a, **k: count("asker", lambda prompt: pytest.fail("an agent was asked")))
    monkeypatch.setattr(cli.research_mod, "run_research", research)
    monkeypatch.setattr(cli, "_reading", lambda paths: [])
    monkeypatch.setattr(cli, "Gauntlet", lambda *a, **k: count("gauntlet", types.SimpleNamespace(dev_prices=None,
                                                                                                  dev_dollar_volume=None)))
    return spent


def _memory_bytes(root):
    return {p.name: p.read_bytes() for p in sorted((root / "memory").iterdir())}


FULL_CYCLE = ["cycle", "--refresh", "--critic", "--backend", "chat:standin@http://127.0.0.1:9", "--skeptic", "chat:x@y"]


def test_t38_1b_a_held_left_out_book_stops_the_cycle_before_any_refresh_research_or_test(tmp_path, t38, monkeypatch):
    run, reviews = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE, BOTH_RULE])
    _base_paper_step(root, px.iloc[:20])                                            # a book stepped before the guard
    _base_paper_step(root, px.iloc[:24])
    spent = _spend_watch(monkeypatch)
    book, memory = _book_files(root), _memory_bytes(root)
    reviews.clear()
    assert run(root, FULL_CYCLE, px.iloc[:30]) == (2, "", _held_lot_refusal(root, "mom_both"))
    assert spent == {"refresh": 0, "market": 0, "asker": 0, "research": 0, "gauntlet": 0}, spent
    assert _memory_bytes(root) == memory and _book_files(root) == book and reviews == []


def test_t38_1b_an_unloadable_strategy_file_still_surfaces_where_it_did_after_the_research_round(tmp_path, t38,
                                                                                                    monkeypatch):
    """No held book: the early check says nothing, and the missing file is refused after the research round and the
    tests, as on fb5d2c6 (the idea the round recorded is kept; nothing reaches the paper book)."""
    run, reviews = t38
    root = tmp_path / "project"
    ids = _t38_project(root, [BOTH_RULE, LONG_RULE])
    (root / "strategies" / f"mom_long_h{ids[1]}.json").unlink()
    spent = _spend_watch(monkeypatch)
    assert run(root, FULL_CYCLE, prices().iloc[:20]) == (2, "", f"refused: no strategy file found for hypothesis "
                                                                f"{ids[1]}; pass --file\n")
    assert spent == {"refresh": 1, "market": 1, "asker": 4, "research": 1, "gauntlet": 1}, spent
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        rows = sorted((h["id"], h["status"]) for h in led.search())
    finally:
        led.close()
    assert rows == [(1, "passed backtest only"), (2, "passed backtest only"), (3, "untested")]
    assert not (root / "journal" / "paper_state.json").exists() and reviews == []


# ---- T38-1b addendum 2: one invariant over the paper book's durable state; only the object False means "cannot"

def _pending(name, sym, units):
    return {"strategy": name, "version": "1.0.0", "symbol": sym, "units": units, "decided_on": "2026-01-29",
            "reduces_risk": units < 0, "reasoning": "hand-written", "conditions": None}


def _write_state(root, state):
    (root / "journal" / "paper_state.json").write_text(json.dumps(state))


def _short_refusal(name, sym, tail="nothing was stepped"):
    return (f"the paper book holds a short position (or an order that would open one) under {name}, {sym}; the paper "
            f"book and its tax ledger do not handle shorts; {tail}")


@pytest.mark.parametrize("buys_only", [False, True], ids=["the checker's case", "a pending buy only"])
@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_addendum2_a_pending_order_under_a_left_out_name_stops_the_run_with_every_lot_at_zero(tmp_path, t38,
                                                                                                  argv, buys_only):
    run, reviews = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE, BOTH_RULE])
    _base_paper_step(root, px.iloc[:20])                     # the base: both ideas stepped once, orders not yet filled
    state = _state(root)
    assert all(lot["units"] == 0.0 for lot in state["positions"]["mom_both"].values())
    assert any(o["strategy"] == "mom_both" and o["units"] < 0 for o in state["pending"])    # a pending short (DOWN)
    if buys_only:
        state["pending"] = [o for o in state["pending"] if o["units"] > 0]
        assert any(o["strategy"] == "mom_both" for o in state["pending"])
        _write_state(root, state)
    sym = next(o["symbol"] for o in _state(root)["pending"] if o["strategy"] == "mom_both")
    before = _book_files(root)
    reviews.clear()
    for _ in range(2):
        assert run(root, argv, px.iloc[:24]) == (
            2, "", f"refused: the paper book holds a pending order of mom_both ({sym}), which can hold a short and is "
                   "no longer stepped; nothing was stepped\n")
    assert _book_files(root) == before and reviews == []                           # journal and state: bytes and times


def test_t38_1b_addendum2_the_paper_command_checks_before_refresh_secrets_or_a_venue(tmp_path, t38, monkeypatch):
    from valedia_trader.core import secrets
    from valedia_trader.data_and_fees import prices as price_data
    run, _ = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE, BOTH_RULE])
    _base_paper_step(root, px.iloc[:20])
    spent = []
    monkeypatch.setattr(price_data, "refresh", lambda data: spent.append("refresh"))
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: spent.append("secrets"))
    monkeypatch.setattr(secrets, "secret", lambda name, *a: spent.append("secret") or "PKTEST")
    monkeypatch.setattr(cli, "AlpacaPaperVenue", lambda key, secret: spent.append("venue"))
    real_market = cli._market
    monkeypatch.setattr(cli, "_market", lambda args: spent.append("market") or real_market(args))
    status, out, err = run(root, ["paper-step", "--refresh", "--alpaca"], px.iloc[:24])
    assert status == 2 and out == "" and "pending order of mom_both" in err and spent == [], (status, err, spent)


# ruling A through the commands: a hand-written strategy whose holds_shorts is each answer; only False is stepped

_PY_STRATEGY = '''"""A hand-written strategy for the T38-1b tests.

version: 1.0.0
hypothesis: Assets that rose over ten days keep rising
"""
import numpy

from valedia_trader.research_loop.strategy import RuleStrategy


class Falsy:
    def __bool__(self):
        return False


class Said(RuleStrategy):
    holds_shorts = {holds}


STRATEGY = Said.from_spec({spec!r})
'''
ANSWERS = ["None", "0", '""', "numpy.False_", "Falsy()", "False", "True"]


def _py_strategy(path, holds, spec):
    path.write_text(_PY_STRATEGY.format(holds=holds, spec=spec))
    return path


@pytest.mark.parametrize("holds", ANSWERS)
@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_addendum2_only_a_strategy_that_answers_false_is_stepped(tmp_path, t38, argv, holds):
    run, _ = t38
    root = tmp_path / "project"
    ids = _t38_project(root, [LONG_RULE])
    (root / "strategies" / f"mom_long_h{ids[0]}.json").unlink()
    _py_strategy(root / "strategies" / f"mom_said_h{ids[0]}.py", holds, {**LONG_RULE, "name": "mom_said"})
    status, out, err = run(root, argv, prices().iloc[:20])
    assert status == 0, err
    if holds == "False":
        got = json.loads(out)
        assert got["not_stepped"] == [] and (got if argv == ["paper-step"] else got["paper"])["capital_shared_by"] == 1
        assert set(_state(root)["history"][-1]["strategy_values"]) == {"mom_said"}
    else:
        got = json.loads(out.split("\n", 1)[1]) if argv == ["paper-step"] else json.loads(out)
        assert got["not_stepped"] == [{"id": ids[0], "name": "mom_said", "reason": NOT_STEPPED}], out
        assert not (root / "journal" / "paper_state.json").exists()


@pytest.mark.parametrize("holds", ANSWERS)
def test_t38_1b_addendum2_only_a_strategy_that_answers_false_is_followed(follow_project, holds):
    from valedia_trader.execution_and_risk import order_step
    root, _ = follow_project
    (root / "strategies" / f"{H26}_h26.json").unlink()
    _py_strategy(root / "strategies" / f"{H26}_h26.py", holds, {**LONG_RULE, "name": H26, "market": ["QQQ"]})
    _book_file(root, {"follow": [26]})
    f = cli.broker_book_follow(cli.LoopPaths(root))
    if holds == "False":
        assert f.refused is None and f.names == (H26,), f
    else:
        assert f == order_step.Follow(refused=T38_REFUSAL), f


# ruling C: no short under any name, whoever says what

@pytest.mark.parametrize("case", ["a negative lot under the stepped name", "a pending sale past the stepped name's lot",
                                  "a negative lot under an unknown name", "a pending sale under an unknown name"])
@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_addendum2_a_short_under_any_name_stops_the_run(tmp_path, t38, argv, case):
    run, _ = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE])
    assert run(root, ["paper-step"], px.iloc[:20])[0] == 0
    state = _state(root)
    if case == "a negative lot under the stepped name":
        state["positions"]["mom_long"]["UP"] = {"units": -5.0, "cost": 100.0}
        name, sym = "mom_long", "UP"
    elif case == "a pending sale past the stepped name's lot":
        state["positions"]["mom_long"]["DOWN"] = {"units": 2.0, "cost": 100.0}
        state["pending"].append(_pending("mom_long", "DOWN", -3.0))
        name, sym = "mom_long", "DOWN"
    elif case == "a negative lot under an unknown name":
        state["positions"]["ghost_book"] = {"UP": {"units": -1.0, "cost": 100.0}}
        name, sym = "ghost_book", "UP"
    else:
        state["pending"].append(_pending("ghost_book", "DOWN", -1.0))
        name, sym = "ghost_book", "DOWN"
    _write_state(root, state)
    before = _book_files(root)
    assert run(root, argv, px.iloc[:24]) == (2, "", f"refused: {_short_refusal(name, sym)}\n")
    assert _book_files(root) == before


@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_addendum2_a_strategy_that_says_false_and_shorts_is_caught_after_the_step_and_stops_the_next(tmp_path,
                                                                                                         t38, argv):
    run, _ = t38
    px, root = prices(), tmp_path / "project"
    ids = _t38_project(root, [LONG_RULE])
    (root / "strategies" / f"mom_long_h{ids[0]}.json").unlink()
    _py_strategy(root / "strategies" / f"liar_both_h{ids[0]}.py", "False", {**BOTH_RULE, "name": "liar_both"})
    status, out, err = run(root, argv, px.iloc[:20])
    assert status == 2 and err == "a strategy that says it cannot hold a short now has one: liar_both, DOWN\n", (status, err)
    assert any(o["strategy"] == "liar_both" and o["units"] < 0 for o in _state(root)["pending"])   # the day is journalled
    before = _book_files(root)
    assert run(root, argv, px.iloc[:24]) == (2, "", f"refused: {_short_refusal('liar_both', 'DOWN')}\n")
    assert _book_files(root) == before


# ruling E: the order step without a follow file does not mirror a book the paper step refuses

@pytest.mark.parametrize("case", ["a lot under a left-out name", "a short under any name"])
def test_t38_1b_addendum2_the_whole_book_order_step_stops_at_the_follow_stage_on_a_book_the_paper_step_refuses(
        order_step_project, capsys, case):
    root, venue = order_step_project
    state = json.loads((root / "journal" / "paper_state.json").read_text())
    if case == "a lot under a left-out name":
        state["positions"] = {"mom_both": state["positions"]["a"]}
        state["pending"] = [{**o, "strategy": "mom_both"} for o in state["pending"]]
        led = lg.HypothesisLedger(root / "memory" / "ledger.db")
        try:
            hid = led.propose("Assets that rose over ten days keep rising", "test", cli.MARKET)
            led.set_status(hid, "passed paper")
        finally:
            led.close()
        _strategy_file(root, hid, "mom_both", direction="both")
        reason = ("the paper book holds a lot of mom_both (SPY), which can hold a short and is no longer stepped; the "
                  "order step does not mirror such a book")
    else:
        state["positions"]["a"]["SPY"]["units"] = -4.0
        reason = _short_refusal("a", "SPY", "the order step does not mirror such a book")
    (root / "journal" / "paper_state.json").write_text(json.dumps(state))
    assert not (root / "config" / "broker_book.json").exists()
    assert cli.main(["--root", str(root), "order-step"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "stopped" and out["stage"] == "follow" and reason in out["reason"], out
    assert [c for c in venue.calls if c in ("submit", "cancel")] == [] and venue.book == {}


def test_t38_1b_addendum2_a_follow_file_stops_on_a_short_under_a_name_it_follows_only(follow_project):
    root, _ = follow_project
    state = json.loads((root / "journal" / "paper_state.json").read_text())
    state["positions"]["ghost_book"] = {"SPY": {"units": -1.0, "cost": 1.0}}       # not followed: not the order step's
    (root / "journal" / "paper_state.json").write_text(json.dumps(state))
    _book_file(root, {"follow": [23, 26]})
    assert cli.broker_book_follow(cli.LoopPaths(root)).refused is None
    state["pending"] = [_pending(H26, "QQQ", -7.0)]                                # QQQ 6 held: would go to -1
    (root / "journal" / "paper_state.json").write_text(json.dumps(state))
    assert cli.broker_book_follow(cli.LoopPaths(root)).refused == _short_refusal(H26, "QQQ",
                                                                                 "the order step does not mirror such a book")


# the invariant as a property over seeded random states

def _invariant_says_refuse(state, left_out):
    """The rules of addendum 2 in their own words, written apart from cli.judge_paper_book."""
    import math

    def number(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    pos, pend = state["positions"], state["pending"]
    for name in left_out:                                              # B: a left-out name holds nothing at all
        if any(not (number(lot["units"]) and lot["units"] == 0) for lot in pos.get(name, {}).values()):
            return True
        if any(o["strategy"] == name for o in pend):
            return True
    for book in pos.values():                                          # C: no short lot, and none it cannot read
        if any(not number(lot["units"]) or lot["units"] < 0 for lot in book.values()):
            return True
    run = {}
    for o in pend:                                                     # C: no pending order that would open a short
        if not number(o["units"]):
            return True
        key = (o["strategy"], o["symbol"])
        run[key] = run.get(key, pos.get(o["strategy"], {}).get(o["symbol"], {"units": 0.0})["units"]) + o["units"]
        if run[key] < -1e-12:
            return True
    return False


def test_t38_1b_addendum2_the_paper_book_invariant_holds_over_seeded_random_states(tmp_path, capsys):
    rng = np.random.default_rng(38)
    px = prices()
    limits = tmp_path / "risk.json"
    limits.write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.6,
                                  "max_open_positions": 5}))
    long_only = st.RuleStrategy.from_spec(LONG_RULE)
    names, syms, left_out = ["mom_long", "mom_both", "ghost_book"], ["UP", "DOWN"], ["mom_both"]
    kinds = ["positive", "zero", "minus zero", "negative", "nan", "text"]
    generated = refused = stepped = filled = 0
    wrong = []
    for k in range(400):
        positions = {}
        for name in names:
            if rng.random() < 0.6:
                book = {}
                for sym in syms:
                    if rng.random() < 0.7:
                        kind = rng.choice(kinds, p=[0.55, 0.3, 0.05, 0.04, 0.03, 0.03])
                        units = {"positive": float(rng.uniform(1, 50)), "zero": 0.0, "minus zero": -0.0,
                                 "negative": -float(rng.uniform(1, 50)), "nan": float("nan"), "text": "ten"}[kind]
                        book[sym] = {"units": units, "cost": 100.0}
                positions[name] = book
        pending = []
        for _ in range(int(rng.integers(0, 4))):
            name, sym = str(rng.choice(names)), str(rng.choice(syms))
            held = positions.get(name, {}).get(sym, {}).get("units", 0.0)
            pick = rng.random()
            units = (-held if pick < 0.2 and isinstance(held, float) and held == held
                     else float(rng.uniform(1, 30)) * (1 if pick < 0.6 else -1))
            pending.append(_pending(name, sym, units if units != 0 else 1.0))
        state = {"starting_cash": 100_000.0, "cash": 100_000.0, "positions": positions, "pending": pending, "history": [],
                 "peak_equity": 100_000.0, "last_day": str(px.index[18].date()), "realized": {}, "panel_cases": []}
        generated += 1
        found = cli.judge_paper_book(state, left_out)
        if (found is not None) != _invariant_says_refuse(state, left_out):
            wrong.append((k, found, state))
            continue
        if found is not None:
            refused += 1
            continue
        book_dir = tmp_path / f"book{k}"
        book_dir.mkdir()
        (book_dir / "paper_state.json").write_text(json.dumps(state))
        trader = PaperTrader(book_dir, limits, halt_file=None)
        trader.step(px.iloc[:20], {1: long_only})                                  # ONE day, long-only strategies
        stepped += 1
        assert trader.state["last_day"] == str(px.index[19].date())                 # the day really ran
        filled += sum(1 for o in pending if o not in trader.state["pending"])       # its orders were filled
        after = trader.state["positions"]
        if any(lot["units"] < 0 for book in after.values() for lot in book.values()) or any(
                lot["units"] for lot in after.get("mom_both", {}).values()):
            wrong.append((k, "after the step", after))
    with capsys.disabled():
        print(f"\ninvariant: {generated} states generated, {refused} refused, {stepped} stepped ({filled} pending orders "
              f"filled), {len(wrong)} counter-examples")
    assert wrong == [] and generated == 400 and refused >= 40 and stepped >= 40 and filled >= 20, (refused, stepped, filled)


# ---- T38-1b addendum 3: the notes of the second blind check

def _add_idea(root, spec, status="passed backtest only", py_holds=None):
    """One more idea in an existing project, with its strategy file (a hand-written .py one when `py_holds` is given,
    else the JSON spec; None spec: no file at all). Returns its id."""
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        name = spec["name"] if spec else "no_file_idea"
        hid = led.propose(f"An idea added later to this book ({name})", "test", cli.MARKET)
        led.set_status(hid, status)
    finally:
        led.close()
    if spec is not None and py_holds is not None:
        _py_strategy(root / "strategies" / f"{spec['name']}_h{hid}.py", py_holds, spec)
    elif spec is not None:
        (root / "strategies" / f"{spec['name']}_h{hid}.json").write_text(json.dumps(spec))
    return hid


def _days_naming(root, name):
    return sorted({e["day"] for e in _journal_entries(root) if e.get("strategy") == name})


LIAR = {**BOTH_RULE, "name": "liar_both"}
AFTER_STEP = "a strategy that says it cannot hold a short now has one: liar_both, DOWN\n"


@pytest.mark.parametrize("argv", [["paper-step"], CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_addendum3_a_lying_strategy_stops_the_catch_up_after_its_first_day(tmp_path, t38, argv):
    run, _ = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE])
    assert run(root, ["paper-step"], px.iloc[:20])[0] == 0 and run(root, ["paper-step"], px.iloc[:21])[0] == 0
    _add_idea(root, LIAR, py_holds="False")                               # says it cannot short; its rules short
    status, out, err = run(root, argv, px.iloc[:31])                      # ten days to catch up in one run
    assert (status, err) == (2, AFTER_STEP), (status, out, err)
    if argv == ["paper-step"]:
        assert json.loads(out)["days_processed"] == 1                     # what the day already stepped printed
    first = str(px.index[21].date())
    state = _state(root)
    assert state["last_day"] == first                                     # no further day was stepped
    assert _days_naming(root, "liar_both") == [first]                     # exactly ONE day of its orders
    assert {o["decided_on"] for o in state["pending"] if o["strategy"] == "liar_both"} == {first}
    assert all(lot["units"] == 0.0 for lot in state["positions"].get("liar_both", {}).values())   # none of it filled
    before = _book_files(root)
    assert run(root, argv, px.iloc[:31]) == (2, "", f"refused: {_short_refusal('liar_both', 'DOWN')}\n")
    assert _book_files(root) == before


def test_t38_1b_addendum3_pending_orders_are_added_up():
    book = {"positions": {"mom_long": {"UP": {"units": 5.0, "cost": 100.0}}}}
    two_of_three = {**book, "pending": [_pending("mom_long", "UP", -3.0), _pending("mom_long", "UP", -3.0)]}
    three_and_two = {**book, "pending": [_pending("mom_long", "UP", -3.0), _pending("mom_long", "UP", -2.0)]}
    assert cli.judge_paper_book(two_of_three) == ("short", "mom_long", "UP")
    assert cli.judge_paper_book(three_and_two) is None


def test_t38_1b_addendum3_the_broker_mirror_branch_checks_after_its_step(tmp_path, t38, monkeypatch):
    from valedia_trader.core import secrets
    run, _ = t38

    class Mirror:
        name, sent = "alpaca-standin", []

        def submit(self, *a, **k):
            self.sent.append(a)
            return {"status": "accepted"}

    mirror = Mirror()
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: [])
    monkeypatch.setattr(secrets, "secret", lambda name, *a: "PKTEST")
    monkeypatch.setattr(cli, "AlpacaPaperVenue", lambda key, secret: mirror)
    px, root = prices(), tmp_path / "project"
    ids = _t38_project(root, [LONG_RULE])
    (root / "strategies" / f"mom_long_h{ids[0]}.json").unlink()
    _py_strategy(root / "strategies" / f"liar_both_h{ids[0]}.py", "False", LIAR)
    status, out, err = run(root, ["paper-step", "--alpaca"], px.iloc[:20])
    assert (status, err) == (2, AFTER_STEP), (status, out, err)
    assert json.loads(out)["capital_shared_by"] == 1                      # the step's own output came first
    before = _book_files(root)
    assert run(root, ["paper-step", "--alpaca"], px.iloc[:21]) == (2, "", f"refused: {_short_refusal('liar_both', 'DOWN')}\n")
    assert _book_files(root) == before


def test_t38_1b_addendum3_the_cycles_late_check_catches_an_idea_that_passes_in_this_very_cycle(tmp_path, t38,
                                                                                                monkeypatch):
    run, reviews = t38
    px, root = prices(), tmp_path / "project"
    ids = _t38_project(root, [LONG_RULE, BOTH_RULE], statuses={"mom_both": "untested"})
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    try:
        led.register_prediction(ids[1], "It rises after it rose and falls after it fell", {"sharpe": 0.5})
    finally:
        led.close()
    assert run(root, ["paper-step"], px.iloc[:20])[0] == 0
    state = _state(root)
    state["pending"].append(_pending("mom_both", "UP", 4.0))              # a buy of the idea not yet passed
    _write_state(root, state)

    class Passes:
        def __init__(self, prices, dollar, cost, ledger, paths, criteria=None):
            self.ledger = ledger

        def develop(self, hid, strategy, file):
            return {"verdict": "pass"}

        def seal(self, hid, strategy, file):
            self.ledger.set_status(hid, "passed backtest only")          # it passes inside this cycle
            return {"verdict": "pass"}

    monkeypatch.setattr(cli, "Gauntlet", Passes)
    before = _book_files(root)
    reviews.clear()
    assert run(root, CYCLE, px.iloc[:24]) == (2, "", "refused: the paper book holds a pending order of mom_both (UP), "
                                                      "which can hold a short and is no longer stepped; nothing was "
                                                      "stepped\n")
    assert _book_files(root) == before and reviews == []


def _fill_state(lot, orders, day):
    return {"starting_cash": 100_000.0, "cash": 100_000.0, "positions": {"mom_long": {"UP": {"units": lot, "cost": 100.0}}},
            "pending": [_pending("mom_long", "UP", u) for u in orders], "history": [], "peak_equity": 100_000.0,
            "last_day": day, "realized": {}, "panel_cases": []}


def test_t38_1b_addendum3_rule_c_rounds_pending_orders_as_the_paper_fill_does(tmp_path, capsys):
    """For seeded random lots (zero, tiny, whole) and orders around the fill's rounding threshold, judge_paper_book
    accepts exactly when stepping the state with the real paper trader leaves no negative lot, after any of its fills
    (a fill that leaves the lot short is a short fill, which stops the tax ledger, even when a later one closes it)."""
    assert cli.judge_paper_book(_fill_state(0.0, [9e-13, -1.5e-12], "x")) == ("short", "mom_long", "UP")   # the checker's
    px = prices()
    limits = tmp_path / "risk.json"
    limits.write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.6,
                                  "max_open_positions": 5}))
    rng = np.random.default_rng(1238)
    tiny = [k * 1e-13 for k in range(1, 25)]
    states = accepted = 0
    wrong = []
    for k in range(300):
        lot = float(rng.choice([0.0, 0.0, 5e-13, 9e-13, 1e-12, 2.0, 3.0]))
        orders = []
        for _ in range(int(rng.integers(1, 5))):
            size = float(rng.choice(tiny)) if rng.random() < 0.6 else float(rng.choice([1.0, 2.0, 3.0, lot or 1.0]))
            orders.append(size if rng.random() < 0.45 else -size)
        state = _fill_state(lot, orders, str(px.index[18].date()))
        found = cli.judge_paper_book(state)
        book_dir = tmp_path / f"fill{k}"
        book_dir.mkdir()
        (book_dir / "paper_state.json").write_text(json.dumps(state))
        trader = PaperTrader(book_dir, limits, halt_file=None)
        seen, take = [], trader._tax_take                                 # the lot as each real fill leaves it

        def after_fill(day, sym, *a, trader=trader, seen=seen, take=take):
            seen.append(trader.state["positions"]["mom_long"][sym]["units"])
            return take(day, sym, *a)

        trader._tax_take = after_fill
        trader.step(px.iloc[:20], {})                                     # the fill alone: no strategy decides
        assert trader.state["pending"] == [] and len(seen) == len(orders)
        negative = any(u < 0 for u in seen) or any(
            l["units"] < 0 for book in trader.state["positions"].values() for l in book.values())
        states += 1
        accepted += found is None
        if (found is None) == negative:
            wrong.append((lot, orders, found, trader.state["positions"]))
    with capsys.disabled():
        print(f"\nfill rounding: {states} states, {accepted} accepted, {states - accepted} refused, "
              f"{len(wrong)} counter-examples")
    assert wrong == [] and 30 <= accepted <= 270, (accepted, wrong[:3])


@pytest.mark.parametrize("argv", [["paper-step", "--refresh", "--alpaca"], FULL_CYCLE], ids=["paper-step", "cycle"])
def test_t38_1b_addendum3_an_unloadable_file_no_longer_hides_a_left_out_pending_order_until_after_spending(
        tmp_path, t38, monkeypatch, argv):
    from valedia_trader.core import secrets
    run, _ = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE, BOTH_RULE])
    _base_paper_step(root, px.iloc[:20])
    state = _state(root)
    state["pending"] = [o for o in state["pending"] if o["units"] > 0]        # only buys, mom_both's among them
    _write_state(root, state)
    _add_idea(root, None)                                                     # an idea whose strategy file is missing
    spent = _spend_watch(monkeypatch)
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: spent.update(secrets=1))
    monkeypatch.setattr(secrets, "secret", lambda name, *a: spent.update(secret=1) or "PKTEST")
    monkeypatch.setattr(cli, "AlpacaPaperVenue", lambda key, secret: spent.update(venue=1))
    before = _book_files(root)
    sym = next(o["symbol"] for o in state["pending"] if o["strategy"] == "mom_both")
    assert run(root, argv, px.iloc[:24]) == (2, "", f"refused: the paper book holds a pending order of mom_both ({sym}), "
                                                    "which can hold a short and is no longer stepped; nothing was "
                                                    "stepped\n")
    assert spent == {"refresh": 0, "market": 0, "asker": 0, "research": 0, "gauntlet": 0}, spent
    assert _book_files(root) == before


UNREADABLE = [
    ({"positions": [], "pending": []}, "its positions are a list where a mapping of strategies to their lots belongs"),
    ({"positions": None, "pending": []}, "its positions are null where a mapping of strategies to their lots belongs"),
    ({"positions": {}, "pending": None}, "its pending orders are null where a list of orders belongs"),
    ({"positions": {}, "pending": {}}, "its pending orders are a mapping where a list of orders belongs"),
    ({"positions": {"mom_long": [1]}, "pending": []},
     "its lots under mom_long are a list where a mapping of symbols to lots belongs"),
    ({"positions": {"mom_long": {"UP": {"units": "ten", "cost": 1.0}}}, "pending": []},
     "it holds a lot of mom_long (UP) whose units are not a number"),
    ({"positions": {}, "pending": [5]}, "it holds a pending entry that is not an order"),
    ({"positions": {}, "pending": [{"symbol": "UP", "units": 1.0}]}, "it holds a pending order with no strategy name"),
    ({"positions": {}, "pending": [{"strategy": "mom_long", "units": 1.0}]}, "it holds a pending order with no symbol"),
    ({"positions": {}, "pending": [{"strategy": "mom_long", "symbol": "UP", "units": None}]},
     "it holds a pending order of mom_long for UP whose units are not a number"),
    ([], "it is a list where a mapping belongs"),
]


@pytest.mark.parametrize("state, words", UNREADABLE, ids=[w[:40] for _, w in UNREADABLE])
def test_t38_1b_addendum3_a_state_it_cannot_read_is_refused_in_plain_words(state, words):
    found = cli.judge_paper_book(state)
    assert found is not None and found[0] == "unreadable", found
    assert cli.paper_book_words(found, "nothing was stepped") == (f"the paper book's state cannot be read: {words}; "
                                                                  "nothing was stepped")


@pytest.mark.parametrize("positions", [[], None], ids=["a list", "null"])
def test_t38_1b_addendum3_the_paper_command_refuses_positions_that_are_not_a_mapping(tmp_path, t38, positions):
    run, _ = t38
    px, root = prices(), tmp_path / "project"
    _t38_project(root, [LONG_RULE])
    assert run(root, ["paper-step"], px.iloc[:20])[0] == 0
    state = _state(root)
    state["positions"] = positions
    _write_state(root, state)
    before = _book_files(root)
    shown = "a list" if positions == [] else "null"
    assert run(root, ["paper-step"], px.iloc[:22]) == (
        2, "", f"refused: the paper book's state cannot be read: its positions are {shown} where a mapping of "
               "strategies to their lots belongs; nothing was stepped\n")
    assert _book_files(root) == before


def test_ideas_judged_before_the_split_get_one_finding_and_nothing_else_changes(tmp_path, capsys):
    """T39-2b, R8: a dry run by default; --apply adds the finding through the ledger's own writer to every idea whose
    development or sealed result has no claim; a second --apply adds nothing; no other column changes."""
    root = tmp_path / "repo"
    assert cli.main(["--root", str(root), "init"]) == 0
    capsys.readouterr()
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    old_dev, old_sealed, new, untested = (led.propose(f"Idea number {k} about funds and their drift", "price-analyst", "m")
                                          for k in range(4))
    old = {"verdict": "fail", "checks": {"deflated_sharpe": {"passed": False, "detail": "0.1"}}}
    led.record_backtest(old_dev, old, "trending up", "", "1.0.0", "failed")
    led.record_backtest(old_sealed, {**old, "claim": "edge", "verdict": "pass (development); sealed test pending"}, "trending up", "",
                        "1.0.0", "untested")
    led.record_sealed(old_sealed, {"verdict": "pass", "checks": {}}, "passed backtest only")
    led.record_backtest(new, {**old, "claim": "exposure"}, "trending up", "", "1.0.0", "failed")
    before = {h["id"]: h for h in led.search()}
    led.close()

    def run(*extra):
        assert cli.main(["--root", str(root), "mark-pre-split", *extra]) == 0
        return json.loads(capsys.readouterr().out)
    dry = run()
    assert dry == {"ideas_judged_before_the_split": 2, "already_marked": 0, "to_mark": 2, "added": 0, "applied": False}
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    assert led.findings() == [] and {h["id"]: h for h in led.search()} == before                  # the dry run wrote nothing
    led.close()
    assert run("--apply")["added"] == 2
    again = run("--apply")
    assert again["added"] == 0 and again["already_marked"] == 2 and again["to_mark"] == 0
    led = lg.HypothesisLedger(root / "memory" / "ledger.db")
    marked = [f for f in led.findings() if f["finding"] == cli.PRE_SPLIT_FINDING and f["source"] == cli.PRE_SPLIT_SOURCE]
    assert sorted(f["hypothesis_id"] for f in marked) == [old_dev, old_sealed] and len(led.findings()) == 2
    after = {h["id"]: h for h in led.search()}
    for hid in before:
        changed = {k for k in before[hid] if before[hid][k] != after[hid][k]}
        assert changed == ({"lessons_learned"} if hid in (old_dev, old_sealed) else set()), (hid, changed)
    assert cli.PRE_SPLIT_FINDING in after[old_dev]["lessons_learned"] and after[untested]["lessons_learned"] == ""


# ---- T50: the live pair lives apart; live mode needs a terminal and a practice-only ordinary file; every practice run
# refuses a live name in reach and never opens the live file. Every value below is made up here.

T50_KEY, T50_SECRET = "ZqLiveKey7Rt4Kw9PmXv2Bn", "ZqLiveSecret3LbNHs4GdYp8"
T50_NAMES = ("WEBULL_LIVE_APP_KEY", "WEBULL_LIVE_APP_SECRET")
REAL_LIVE_ENVIRON = cli._live_environ                                  # before live_project replaces it
REAL_ATTENDED = getattr(cli, "_attended", lambda: pytest.fail("no _attended"))
LIVE_RUNS = (["--live", "--propose", "--capital", "100000"], ["--live", "--approve", "123456", "--capital", "100000"],
             ["--live", "--check"])


def _t50_clean(text):
    return T50_KEY not in text and T50_SECRET not in text


def _t50_live_file(folder, mode=0o600):
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    p = folder / "webull-live.env"
    p.write_text(f"WEBULL_LIVE_APP_KEY={T50_KEY}\nWEBULL_LIVE_APP_SECRET={T50_SECRET}\n")
    p.chmod(mode)
    return p


def _t50_no_live_file_read(monkeypatch, tmp_path):
    """The live file present at its place but unreadable, and its reader failing the test if anything calls it."""
    from valedia_trader.core import secrets
    p = _t50_live_file(tmp_path / "live-secrets", mode=0o000)
    monkeypatch.setattr(secrets, "LIVE_ENV_FILE", p)
    monkeypatch.setattr(secrets, "read_live_pair", lambda *a, **k: pytest.fail("the live file was read"))
    return p


def _t50_ordinary_file(tmp_path, monkeypatch, text, names=()):
    """The ordinary secrets file at a temporary place; each name it may load is removed again by the undo."""
    f = tmp_path / "ordinary" / "trading.env"
    f.parent.mkdir(exist_ok=True)
    f.write_text(text)
    f.chmod(0o600)
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(f))
    for name in ("WEBULL_ENV", "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "WEBULL_ACCOUNT_CLASS", "ALPACA_API_KEY",
                 "ALPACA_SECRET_KEY", "FINNHUB_API_KEY", *T50_NAMES, *names):
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    return f


def test_t50_attended_means_a_terminal_on_standard_input(monkeypatch):
    import io
    import os
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert REAL_ATTENDED() is False
    monkeypatch.setattr(sys, "stdin", None)
    assert REAL_ATTENDED() is False
    r, w = os.pipe()
    try:
        with os.fdopen(r, "r", closefd=False) as pipe:
            monkeypatch.setattr(sys, "stdin", pipe)
            assert REAL_ATTENDED() is False
    finally:
        os.close(r)
        os.close(w)
    master, slave = os.openpty()
    try:
        with os.fdopen(slave, "r", closefd=False) as tty:
            monkeypatch.setattr(sys, "stdin", tty)
            assert REAL_ATTENDED() is True
    finally:
        os.close(master)
        os.close(slave)


def test_t50_live_mode_needs_a_terminal_for_every_live_command_check_included(live_project, monkeypatch, capsys):
    import argparse as ap
    root, venue, built, asked = live_project
    monkeypatch.setattr(cli, "_attended", lambda: False)
    monkeypatch.setattr(cli, "_live_environ", lambda: pytest.fail("the environment was loaded"))
    before = _tree(root)
    for argv in LIVE_RUNS:
        assert cli.main(["--root", str(root), "order-step", *argv]) == 2
        err = capsys.readouterr().err
        assert err.startswith("refused: ") and "terminal" in err and len(err.strip().splitlines()) == 1, err
    args = ap.Namespace(live=True, check=True, propose=False, approve=None, capital=None, fractional=False)
    assert cli.live_order_step_command(args, cli.LoopPaths(root), environ=dict(LIVE_KEYS),
                                       build_venue=lambda *a: pytest.fail("a venue was built")) == 2
    assert cli.live_order_step_command(args, cli.LoopPaths(root), environ=dict(LIVE_KEYS), attended=False,
                                       build_venue=lambda *a: pytest.fail("a venue was built")) == 2
    assert "terminal" in capsys.readouterr().err
    assert built == [] and venue.calls == [] and asked == [] and _tree(root) == before


@pytest.mark.parametrize("word", ["production", " PRODUCTION ", "live", "sandbox2"])
def test_t50_live_mode_refuses_an_ordinary_environment_value_other_than_the_practice_one(live_project, monkeypatch, capsys,
                                                                                         word):
    root, venue, built, asked = live_project
    monkeypatch.setattr(cli, "_live_environ", lambda: {**LIVE_KEYS, "WEBULL_ENV": word})
    before = _tree(root)
    for argv in LIVE_RUNS:
        assert cli.main(["--root", str(root), "order-step", *argv]) == 2
        err = capsys.readouterr().err
        assert err.startswith("refused: ") and "WEBULL_ENV" in err and "sandbox" in err, err
    assert built == [] and venue.calls == [] and asked == [] and _tree(root) == before


@pytest.mark.parametrize("env", [{k: v for k, v in LIVE_KEYS.items() if k != "WEBULL_ENV"}, {**LIVE_KEYS, "WEBULL_ENV": ""},
                                 {**LIVE_KEYS, "WEBULL_ENV": " Sandbox "}])
def test_t50_live_mode_takes_a_missing_environment_value_as_the_practice_one(live_project, monkeypatch, capsys, env):
    root, venue, built, asked = live_project
    monkeypatch.setattr(cli, "_live_environ", lambda: dict(env))
    assert cli.main(["--root", str(root), "order-step", "--live", "--check"]) == 0
    assert len(built) == 1


@pytest.mark.parametrize("name", T50_NAMES)
def test_t50_live_mode_refuses_a_live_name_in_the_ordinary_file_or_the_process_environment(live_project, monkeypatch, capsys,
                                                                                           name):
    root, venue, built, asked = live_project
    monkeypatch.setattr(cli, "_live_environ", lambda: {**LIVE_KEYS, name: T50_SECRET})
    before = _tree(root)
    for argv in LIVE_RUNS:
        assert cli.main(["--root", str(root), "order-step", *argv]) == 2
        captured = capsys.readouterr()
        clean = _t50_clean(captured.out + captured.err)
        assert clean and captured.err.startswith("refused: ") and name in captured.err and "webull-live.env" in captured.err
    assert built == [] and venue.calls == [] and asked == [] and _tree(root) == before


def test_t50_live_mode_loads_the_ordinary_file_and_refuses_production_or_a_live_name_found_there(live_project, monkeypatch,
                                                                                                capsys, tmp_path):
    root, venue, built, asked = live_project
    monkeypatch.setattr(cli, "_live_environ", REAL_LIVE_ENVIRON)
    base = "ALPACA_API_KEY=a\nALPACA_SECRET_KEY=b\nFINNHUB_API_KEY=c\n"
    _t50_ordinary_file(tmp_path, monkeypatch, base + "WEBULL_ENV=production\n")
    assert cli.main(["--root", str(root), "order-step", "--live", "--check"]) == 2
    err = capsys.readouterr().err
    assert "WEBULL_ENV" in err and "sandbox" in err, err
    _t50_ordinary_file(tmp_path, monkeypatch, base + f"WEBULL_ENV=sandbox\nWEBULL_LIVE_APP_SECRET={T50_SECRET}\n")
    assert cli.main(["--root", str(root), "order-step", "--live", "--check"]) == 2
    captured = capsys.readouterr()
    clean = _t50_clean(captured.out + captured.err)
    assert clean and "WEBULL_LIVE_APP_SECRET" in captured.err
    _t50_ordinary_file(tmp_path, monkeypatch, base)                       # the process says production, the file nothing
    monkeypatch.setenv("WEBULL_ENV", "production")
    assert cli.main(["--root", str(root), "order-step", "--live", "--check"]) == 2
    assert "WEBULL_ENV" in capsys.readouterr().err
    assert built == [] and venue.calls == []
    _t50_ordinary_file(tmp_path, monkeypatch, base + "WEBULL_ENV=sandbox\n")    # a practice file: live mode goes on
    assert cli.main(["--root", str(root), "order-step", "--live", "--check"]) == 0
    assert len(built) == 1


def test_t50_a_live_value_never_reaches_stdout_stderr_the_journal_an_exception_or_a_ticket(live_project, monkeypatch, capsys,
                                                                                          tmp_path_factory):
    from valedia_trader.core import secrets
    root, venue, built, asked = live_project
    live = _t50_live_file(tmp_path_factory.mktemp("live") / "secrets")      # outside the project folder scanned below
    leak = f"x-app-key={T50_KEY} x-app-secret={T50_SECRET}"

    class Leaky(_LiveVenue):
        """A production stand-in whose every account read fails with the live pair in its text."""
        def positions(self):
            self.calls.append("positions")
            raise RuntimeError(leak)

        def cash(self):
            raise RuntimeError(leak)

    leaky, seen = Leaky(), []

    def build(files, environ):
        seen.append(secrets.read_live_pair(live) == (T50_KEY, T50_SECRET))
        return leaky

    monkeypatch.setattr(cli, "_order_step_live_venue", build)
    codes = [cli.main(["--root", str(root), "order-step", "--live", "--check"]),
             cli.main(["--root", str(root), "order-step", "--live", "--propose", "--capital", "100000"])]
    captured = capsys.readouterr()
    stored = b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())
    printed_clean = _t50_clean(captured.out + captured.err)
    stored_clean = T50_KEY.encode() not in stored and T50_SECRET.encode() not in stored
    assert printed_clean
    assert stored_clean
    assert seen == [True, True] and codes == [0, 2]           # check reads the class and last 4 only; propose stops
    masked, hits = secrets.redact(leak)
    assert _t50_clean(masked) and hits >= 2
    try:
        raise RuntimeError(leak)
    except RuntimeError as exc:
        from valedia_trader.execution_and_risk.order_step import _venue_error
        assert _t50_clean(_venue_error(exc))


@pytest.mark.parametrize("name", T50_NAMES)
@pytest.mark.parametrize("argv", [[], ["--propose"]])
def test_t50_the_practice_order_step_refuses_a_live_name_in_the_process_environment(order_step_project, monkeypatch, capsys,
                                                                                    tmp_path, name, argv):
    root, venue = order_step_project
    _t50_no_live_file_read(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_order_step_venue", lambda: pytest.fail("a venue was built"))
    monkeypatch.setenv(name, T50_SECRET)
    before = _tree(root)
    assert cli.main(["--root", str(root), "order-step", *argv]) == 2
    captured = capsys.readouterr()
    clean = _t50_clean(captured.out + captured.err)
    assert clean and captured.err.startswith("refused: ") and name in captured.err and captured.out == ""
    assert _tree(root) == before and venue.calls == []


@pytest.mark.parametrize("name", T50_NAMES)
def test_t50_the_practice_order_step_refuses_a_live_name_in_the_ordinary_file(order_step_project, monkeypatch, capsys,
                                                                              tmp_path, name):
    root, venue = order_step_project
    _t50_no_live_file_read(monkeypatch, tmp_path)
    _t50_ordinary_file(tmp_path, monkeypatch, f"WEBULL_ACCOUNT_CLASS=INDIVIDUAL_CASH\n{name}={T50_SECRET}\n")
    built = []
    monkeypatch.setattr(cli, "_order_step_venue", REAL_ORDER_STEP_VENUE)
    from valedia_trader.venues import webull
    monkeypatch.setattr(webull.WebullVenue, "from_secrets", classmethod(lambda cls, *a, **k: built.append(1) or _Venue()))
    before = _tree(root)
    assert cli.main(["--root", str(root), "order-step"]) == 2
    captured = capsys.readouterr()
    clean = _t50_clean(captured.out + captured.err)
    assert clean and name in captured.err and built == [] and _tree(root) == before


def test_t50_the_practice_order_step_runs_beside_a_live_file_it_never_opens(order_step_project, monkeypatch, capsys, tmp_path):
    root, venue = order_step_project
    _t50_no_live_file_read(monkeypatch, tmp_path)
    for name in T50_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["--root", str(root), "order-step"]) == 0
