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
