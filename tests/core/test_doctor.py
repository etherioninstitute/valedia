import json

import numpy as np
import pandas as pd
import pytest

from valedia_trader import doctor as D
from valedia_trader.research_loop import LoopPaths, risk_gate


def _paths(tmp_path):
    p = LoopPaths(tmp_path)
    for d in (p.config, p.journal, p.memory):
        d.mkdir(parents=True, exist_ok=True)
    return p


def test_an_empty_folder_fails_loudly_and_nothing_raises(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D, "_get", lambda *a, **k: (_ for _ in ()).throw(OSError("no server")))
    rows = {r["check"]: r for r in D.run(tmp_path)}
    assert set(rows) == set(D.CHECKS)
    assert rows["price history"]["status"] == D.FAIL and rows["hypothesis ledger"]["status"] == D.FAIL
    assert rows["risk limits"]["status"] == D.FAIL and "every order is blocked" in rows["risk limits"]["detail"]
    assert rows["local models"]["status"] == D.FAIL and "not reachable" in rows["local models"]["detail"]
    assert rows["quant sheet"]["status"] == D.OK                      # the engine does not depend on the folder
    assert D.main(["--root", str(tmp_path)]) == 1                     # a failing part makes the command fail
    assert "failing" in capsys.readouterr().out
    assert D.main(["--root", str(tmp_path), "--json"]) == 1
    assert {r["status"] for r in json.loads(capsys.readouterr().out)} <= {D.OK, D.ATTENTION, D.FAIL}


# ---- T44-2 ruling 3: the local-models row checks the shared Elephant-1 instance on LM Studio, never Ollama

def _entry(instances=None, **over):
    from valedia_trader.core import decision_model as dm
    inst = {"id": dm.INSTANCE, "config": {"context_length": dm.CONTEXT, "parallel": dm.SLOTS,
                                          "physical_batch_size": dm.PHYSICAL_BATCH, "eval_batch_size": dm.EVAL_BATCH}}
    return {"key": dm.KEY, "type": "decision", "decision_type": dm.DECISION_TYPE, "publisher": dm.PUBLISHER,
            "quantization": {"name": dm.QUANT}, "size_bytes": dm.LISTED_SIZES[1],
            "loaded_instances": [inst] if instances is None else instances, **over}


def _models_row(monkeypatch, tmp_path, listing=None, error=None):
    from valedia_trader.core import decision_model as dm
    seen = []

    def get(url, timeout=3.0):
        seen.append(url)
        if url == dm.URL + "/api/v1/models":
            if error is not None:
                raise error
            return listing
        if url == dm.URL + "/v1/models":                                   # the optional 27B line, unchanged
            return {"data": [{"id": "qwen/qwen3.8-27b"}]}
        raise AssertionError(f"the doctor asked for {url}")

    monkeypatch.setattr(D, "_get", get)
    status, detail = D.check_models(LoopPaths(tmp_path))
    assert seen[0] == dm.URL + "/api/v1/models" and all(u.startswith(dm.URL + "/") for u in seen)
    assert "Ollama" not in detail and "114" + "34" not in detail and "\n" not in detail
    return status, detail


def test_t44_2_the_models_row_is_ok_when_the_list_shows_one_right_instance(monkeypatch, tmp_path):
    other = {"key": "qwen/qwen3.8-27b", "type": "llm", "loaded_instances": []}
    status, detail = _models_row(monkeypatch, tmp_path, {"models": [other, _entry()]})
    assert status == D.OK and "Elephant-1" in detail and "elephant-1" in detail and "loaded" in detail
    assert "27B listed" in detail


@pytest.mark.parametrize("label, listing, error, words", [
    ("unreachable", None, OSError("refused"), "not reachable"),
    ("malformed", {"data": []}, None, "malformed"),
    ("bad instances", {"models": [_entry(instances="x")]}, None, "malformed"),
    ("no instance", {"models": [_entry(instances=[])]}, None, "not loaded"),
    ("no entry", {"models": []}, None, "not loaded"),
    ("wrong file", {"models": [_entry(publisher="someone")]}, None, "publisher 'someone', not debackerl"),
    ("wrong setting", {"models": [_entry(instances=[{"id": "elephant-1", "config": {"context_length": 8192}}])]}, None,
     "parallel None, not 1"),
    ("two instances", {"models": [_entry(), _entry()]}, None, "2 instances named elephant-1"),
])
def test_t44_2_the_models_row_fails_with_the_reason_in_one_line(monkeypatch, tmp_path, label, listing, error, words):
    status, detail = _models_row(monkeypatch, tmp_path, listing, error)
    assert status == D.FAIL and words in detail, detail


def test_risk_limits_and_kill_switch_states(tmp_path):
    p = _paths(tmp_path)
    risk_gate.write_default_limits(p.risk_limits)
    status, detail = D.check_risk_limits(p)
    assert status == D.ATTENTION and "example limits" in detail       # the guide's numbers are not the owner's
    p.risk_limits.write_text(json.dumps({**risk_gate.DEFAULT_LIMITS, "max_drawdown_pct": 6.0}))
    assert D.check_risk_limits(p)[0] == D.OK
    assert D.check_kill_switch(p) == (D.OK, "armed; an order with no limits supplied is refused")
    (p.journal / "kill_switch.json").write_text("{}")
    status, detail = D.check_kill_switch(p)
    assert status == D.ATTENTION and "latch is set" in detail


def test_data_age_and_missing_assets(tmp_path):
    from valedia_trader.data_and_fees.prices import UNIVERSE
    p = _paths(tmp_path); d = tmp_path / "artifacts" / "data"; d.mkdir(parents=True)
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=30)
    pd.DataFrame(np.ones((30, len(UNIVERSE))), index=idx, columns=list(UNIVERSE)).to_parquet(d / "close.parquet")
    assert D.check_data(p)[0] == D.OK
    old = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=20), periods=30)
    pd.DataFrame(np.ones((30, len(UNIVERSE))), index=old, columns=list(UNIVERSE)).to_parquet(d / "close.parquet")
    status, detail = D.check_data(p)
    assert status == D.ATTENTION and "refresh" in detail
    pd.DataFrame(np.ones((30, 2)), index=idx, columns=list(UNIVERSE[:2])).to_parquet(d / "close.parquet")
    assert D.check_data(p)[0] == D.FAIL


def test_a_broken_check_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setitem(D.CHECKS, "repository", lambda paths: (_ for _ in ()).throw(RuntimeError("boom")))
    row = D.run(tmp_path, only=("repository",))[0]
    assert row["status"] == D.FAIL and "the check itself failed: RuntimeError: boom" in row["detail"]


def test_schedule_check_names_each_job_and_fails_on_a_bad_exit(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace
    from valedia_trader import doctor as D
    from valedia_trader.research_loop import LoopPaths

    def launchd(listing):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listing))
        return D.check_schedule(LoopPaths(tmp_path))

    monkeypatch.setattr(D.Path, "home", classmethod(lambda cls: tmp_path))
    status, text = launchd("-\t0\tcom.valedia.research-loop.weekly\n-\t0\tcom.valedia.paper.daily\n")
    assert status == D.OK and "weekly cycle loaded" in text and "daily paper step loaded" in text and "has not run yet" in text
    status, text = launchd("-\t0\tcom.valedia.research-loop.weekly\n")
    assert status == D.ATTENTION and "daily paper step is not loaded" in text
    status, _ = launchd("-\t0\tcom.valedia.research-loop.weekly\n-\t1\tcom.valedia.paper.daily\n")
    assert status == D.FAIL
    status, _ = launchd("-\t0\tcom.example.research-loop.weekly.other\n")
    assert status == D.ATTENTION


# ---- T13 rev 8: the halt and latch looks are the risk gate's own rule

def _odd(base, how):
    import os
    p = base / f"odd-{how}"
    if how == "dangling link":
        os.symlink(base / "nowhere", p)
    elif how == "link loop":
        os.symlink(p, p)
    elif how == "under a regular file":
        (base / "plain").write_text("x")
        return base / "plain" / "halt.json"
    return p


@pytest.mark.parametrize("how", ["dangling link", "link loop", "under a regular file"])
def test_a_halt_or_latch_file_that_is_not_plainly_absent_is_seen(tmp_path, monkeypatch, how):
    p = _paths(tmp_path / "root")
    p.risk_limits.write_text(json.dumps({**risk_gate.DEFAULT_LIMITS, "max_drawdown_pct": 6.0}))
    (tmp_path / "h").mkdir()
    monkeypatch.setattr(risk_gate, "HALT_FILE", _odd(tmp_path / "h", how))
    status, detail = D.check_kill_switch(p)
    assert status == D.ATTENTION and "halt" in detail
    monkeypatch.setattr(risk_gate, "HALT_FILE", tmp_path / "no-halt.json")
    latch = p.journal / "kill_switch.json"
    if how == "under a regular file":
        return                                            # the latch's path is fixed inside the journal folder
    import os
    os.symlink(p.journal / "nowhere" if how == "dangling link" else latch, latch)
    status, detail = D.check_kill_switch(p)
    assert status == D.ATTENTION and "latch is set" in detail


# ---- T20 rev 2: damaged journal lines among the checks

def test_the_journal_check_reports_damaged_lines_of_the_last_30_days_by_file_and_line(tmp_path):
    p = _paths(tmp_path)
    assert D.check_journal(p)[0] == D.OK and "journal" in D.CHECKS
    today = pd.Timestamp.now().normalize()
    near, far = (today - pd.Timedelta(days=5)).date(), (today - pd.Timedelta(days=60)).date()
    (p.journal / f"{near}.jsonl").write_text('{"type": "note"}\n{"type": "no\n[1]\n')
    (p.journal / f"{far}.jsonl").write_text('{"type": "no\n')                       # older than 30 days: not looked at
    status, detail = D.check_journal(p)
    assert status == D.ATTENTION and "2 damaged" in detail and f"{near}.jsonl line 2" in detail and f"{near}.jsonl line 3" in detail
    assert str(far) not in detail and "type" not in detail


def test_the_journal_check_on_a_clean_journal_is_ok_and_creates_nothing(tmp_path):
    p = LoopPaths(tmp_path)
    assert D.check_journal(p)[0] == D.OK and not p.journal.exists()
    p.journal.mkdir()
    (p.journal / f"{pd.Timestamp.now().date()}.jsonl").write_text('{"type": "note"}\n')
    assert D.check_journal(p) == (D.OK, "no damaged lines in the last 30 days")


# ---- T22c item 4: the doctor says what is true about live mode, through the locks' own functions, with no network

ACCOUNT_ENDS = "Zq9X"
APP_KEY, APP_SECRET = "doctor-app-key-never-printed", "doctor-app-secret-never-printed"
SECOND = {"ALPACA_API_KEY": "alpaca-key-never-printed", "ALPACA_SECRET_KEY": "alpaca-secret-never-printed",
          "FINNHUB_API_KEY": "finnhub-key-never-printed"}


@pytest.fixture
def live_repo(tmp_path, monkeypatch):
    """A repository in tmp (webull.LIVE_REPO replaced in-process), no secrets file, no network of any kind."""
    import socket
    import urllib.request
    from datetime import date

    from valedia_trader.core import secrets
    from valedia_trader.data_and_fees import second_quote
    from valedia_trader.venues import webull
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "journal").mkdir()
    monkeypatch.setattr(webull, "LIVE_REPO", repo, raising=False)
    monkeypatch.setattr(webull, "LIVE_CONFIRMATION", repo / "config" / "webull_live.json", raising=False)   # as in the repository
    monkeypatch.setattr(webull, "ny_date", lambda t=None: date(2026, 11, 13))
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: [])
    for name in ("WEBULL_APP_KEY", "WEBULL_APP_SECRET", "WEBULL_ENV", "WEBULL_ACCOUNT_ID", "WEBULL_ACCOUNT_CLASS",
                 "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEBULL_APP_KEY", APP_KEY)
    monkeypatch.setenv("WEBULL_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("WEBULL_ENV", "production")
    no = lambda *a, **k: pytest.fail("the doctor reached for a venue, a quote or the network")   # noqa: E731
    monkeypatch.setattr(webull.WebullVenue, "__init__", no)
    monkeypatch.setattr(second_quote, "second_opinion", no)
    monkeypatch.setattr(urllib.request, "urlopen", no)
    monkeypatch.setattr(socket, "create_connection", no)
    monkeypatch.setattr(socket, "getaddrinfo", no)
    return repo


def _owner(repo, name, value, mode=0o644):
    path = repo / "config" / name
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(mode)
    return path


def _confirmation(**over):
    return {"account_class": "INDIVIDUAL_CASH", "account_ends": ACCOUNT_ENDS, "confirmed_by": "Burton",
            "max_order_value": 250, "max_capital": 2000, "valid_until": "2026-11-20",
            "i_understand_this_trades_real_money": True, **over}


CHECKLIST = {"min_paper_broker_days": 1, "min_reconciled_fills": 1, "min_days_since_a_stopped_run": 1,
             "fills_checked_by_hand": 1, "min_fills_checked_by_hand": 1, "confirmed_by": "Burton"}
LIMITS = {"max_daily_loss_pct": 2.0, "max_drawdown_pct": 8.0, "max_position_pct": 0.10, "max_open_positions": 5,
          "max_orders_per_day": 10}


def _paper_record(repo):
    with (repo / "journal" / "2026-11-12.jsonl").open("a") as fh:
        for e in ({"event": "fill", "fill_id": "f1"}, {"event": "summary", "status": "done", "stage": None}):
            fh.write(json.dumps({"timestamp": "2026-11-12T15:00:00+00:00", "day": "2026-11-12", "type": "order_step",
                                 "venue": "webull-sandbox", "batch": "paper-1", **e}) + "\n")


def _venues(repo):
    status, detail = D.check_venues(LoopPaths(repo))
    for secret in (APP_KEY, APP_SECRET, ACCOUNT_ENDS, *SECOND.values()):
        assert secret not in detail
    return status, detail


def _tree(repo):
    return sorted((str(p.relative_to(repo)), p.stat().st_mtime_ns) for p in repo.rglob("*"))


def test_the_doctor_never_says_confirmed_when_only_the_first_lock_is_met(live_repo):
    _owner(live_repo, "webull_live.json", _confirmation())
    before = _tree(live_repo)
    status, detail = _venues(live_repo)
    assert "CONFIRMED" not in detail
    assert "real-money orders off" in detail
    assert "lock 1 (the confirmation) met" in detail
    assert "lock 2 (the checklist) not met: config/go_live.json is missing" in detail
    assert "lock 3 (the limits) not met" in detail
    assert "lock 4 (the ticket)" in detail and "judged only when a batch is proposed" in detail
    assert _tree(live_repo) == before                                    # nothing written, created or repaired


def test_the_doctor_with_every_lock_it_can_judge_met_still_names_the_proposal_and_the_approval(live_repo, monkeypatch):
    for name, value in SECOND.items():
        monkeypatch.setenv(name, value)
    _owner(live_repo, "webull_live.json", _confirmation())
    _owner(live_repo, "go_live.json", CHECKLIST)
    _owner(live_repo, "risk.json", LIMITS)
    _paper_record(live_repo)
    status, detail = _venues(live_repo)
    assert "real-money orders off" not in detail and "CONFIRMED" not in detail
    for n, name in ((1, "the confirmation"), (2, "the checklist"), (3, "the limits")):
        assert f"lock {n} ({name}) met" in detail
    assert "every batch still needs a proposal and the owner's approval" in detail
    assert "judged only when a batch is proposed" in detail and "lock 4 (the ticket)" in detail
    assert "account" in detail and "second price sources" in detail


def test_the_doctor_judges_through_the_locks_own_functions(live_repo, monkeypatch):
    from valedia_trader.execution_and_risk import go_live
    from valedia_trader.venues import webull
    seen = []
    real = go_live.judge_locks
    monkeypatch.setattr(go_live, "judge_locks", lambda files, today, capital=None: seen.append(today) or real(files, today))
    reads = []
    real_read = webull.read_owner_file
    monkeypatch.setattr(webull, "read_owner_file", lambda p, root=None: reads.append(p) or real_read(p, root))
    monkeypatch.setattr(go_live, "read_owner_file", webull.read_owner_file)
    _owner(live_repo, "webull_live.json", _confirmation())
    _venues(live_repo)
    assert len(seen) == 1 and sorted(p.name for p in reads) == ["broker_book.json", "go_live.json", "risk.json",
                                                                "webull_live.json"]   # T32: and the broker book file


@pytest.mark.parametrize("how", ["missing", "not json", "duplicate key", "group-writable", "symlink", "hard link",
                                 "a folder", "old form", "lapsed", "unreadable"])
def test_the_doctor_reports_a_missing_damaged_or_refused_confirmation_and_never_raises(live_repo, tmp_path, how):
    import os
    conf = live_repo / "config" / "webull_live.json"
    if how == "not json":
        _owner(live_repo, "webull_live.json", "{not json")
    elif how == "duplicate key":
        _owner(live_repo, "webull_live.json", json.dumps(_confirmation())[:-1] + ', "max_capital": 9}')
    elif how == "group-writable":
        _owner(live_repo, "webull_live.json", _confirmation(), 0o664)
    elif how == "symlink":
        real = tmp_path / "real.json"
        real.write_text(json.dumps(_confirmation()))
        conf.symlink_to(real)
    elif how == "hard link":
        real = tmp_path / "real.json"
        real.write_text(json.dumps(_confirmation()))
        real.chmod(0o644)
        os.link(real, conf)
    elif how == "a folder":
        conf.mkdir()
    elif how == "old form":
        _owner(live_repo, "webull_live.json", {"account_id": "ACC-0001-" + ACCOUNT_ENDS, "confirmed_by": "Burton",
                                               "max_order_value": 1000, "i_understand_this_trades_real_money": True})
    elif how == "lapsed":
        _owner(live_repo, "webull_live.json", _confirmation(valid_until="2026-11-01"))
    elif how == "unreadable":
        _owner(live_repo, "webull_live.json", _confirmation(), 0o200)
    status, detail = _venues(live_repo)
    assert "real-money orders off" in detail and "lock 1 (the confirmation) not met: config/webull_live.json " in detail
    assert "CONFIRMED" not in detail
    if how == "hard link":
        assert "more than one name" in detail


def test_the_doctor_for_another_root_says_live_mode_is_the_repositorys_alone_and_reads_nothing(live_repo, tmp_path,
                                                                                                monkeypatch):
    from valedia_trader.execution_and_risk import go_live
    monkeypatch.setattr(go_live, "judge_locks", lambda *a, **k: pytest.fail("an owner's file was read"))
    other = tmp_path / "other"
    other.mkdir()
    status, detail = D.check_venues(LoopPaths(other))
    assert "real-money orders off" in detail and "runs only from the repository itself" in detail


def test_the_doctor_says_off_when_the_locks_cannot_be_judged(live_repo, monkeypatch):
    from valedia_trader.execution_and_risk import go_live
    monkeypatch.setattr(go_live, "judge_locks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(APP_KEY)))
    status, detail = _venues(live_repo)
    assert "real-money orders off" in detail and "could not be judged" in detail


# ---- T32: the live-mode note says which strategies the broker book follows, or that config/broker_book.json is
# missing or refused and that live mode would stop for that, through the reading function the order step is given

def _ledger_with(repo, name="hold_rebalance_etf_2006_2026", direction="long"):
    from valedia_trader.research_loop import ledger as lg
    led = lg.HypothesisLedger(repo / "memory" / "ledger.db")
    try:
        hid = led.propose("Hold a fixed basket and rebalance it", "test", "us-etf+crypto")
        led.set_status(hid, "passed paper")                 # T32 rev 2: a hypothesis the paper command steps
    finally:
        led.close()
    (repo / "strategies").mkdir(exist_ok=True)
    (repo / "strategies" / f"{name}_h{hid}.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "hypothesis": "Hold a fixed basket and rebalance it", "market": ["SPY", "TLT"],
        "entry": {"rule": "momentum", "lookback": 10, "enter": 0.03, "exit": 0.0, "direction": direction},   # T38-1b: long
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.5, "max_open_positions": 2, "stop_loss_pct": None}}))
    return hid


def test_t38_1b_the_doctor_says_a_follow_file_naming_a_strategy_that_can_hold_a_short_would_stop_live_mode(live_repo):
    hid = _ledger_with(live_repo, direction="both")
    _owner(live_repo, "broker_book.json", {"follow": [hid]})
    before = _tree(live_repo)
    status, detail = _venues(live_repo)
    assert ("broker book: not followed: config/broker_book.json names a hypothesis whose strategy can hold a short "
            "position, so it cannot be followed until shorts are built; live mode would stop for that") in detail
    assert f"hypothesis {hid}" not in detail                  # A1: no id from the file
    assert _tree(live_repo) == before


def test_t38_1b_the_ledger_line_counts_what_the_paper_command_steps_and_says_how_many_it_leaves_out(live_repo):
    paths = LoopPaths(live_repo)
    _ledger_with(live_repo, "mom_long_basket")
    status, text = D.check_ledger(paths)
    assert status == D.OK and "1 in paper trading)" in text and "not stepped" not in text
    _ledger_with(live_repo, "mom_both_basket", direction="both")
    status, text = D.check_ledger(paths)
    assert status == D.OK and "1 in paper trading (1 not stepped: can hold a short))" in text, text


def test_t38_1b_addendum2_the_doctor_counts_idea_by_idea_so_an_unloadable_file_hides_nothing(live_repo):
    from valedia_trader.research_loop import ledger as lg
    paths = LoopPaths(live_repo)
    _ledger_with(live_repo, "mom_long_basket")
    _ledger_with(live_repo, "mom_both_basket", direction="both")
    led = lg.HypothesisLedger(paths.ledger)
    try:
        led.set_status(led.propose("An idea whose strategy file is missing", "test", "us-etf+crypto"), "passed paper")
    finally:
        led.close()
    status, text = D.check_ledger(paths)
    assert "2 in paper trading (1 not stepped: can hold a short))" in text, text   # the missing file: counted as before
    first = live_repo / "strategies" / "mom_long_basket_h1.json"
    first.write_text(first.read_text().replace('"direction": "long"', '"direction": "both"'))
    status, text = D.check_ledger(paths)
    assert status == D.OK and "1 in paper trading (2 not stepped: can hold a short))" in text, text


def test_t38_1b_addendum3_the_doctor_does_not_run_a_code_strategy_and_says_it_did_not_check_it(live_repo):
    """A .py strategy file is code: the doctor does not import it (its import would write the marker) and counts the
    idea as not checked; .json files are read as before."""
    from valedia_trader.research_loop import ledger as lg
    paths = LoopPaths(live_repo)
    _ledger_with(live_repo, "mom_long_basket")
    led = lg.HypothesisLedger(paths.ledger)
    try:
        hid = led.propose("An idea whose strategy is code", "test", "us-etf+crypto")
        led.set_status(hid, "passed paper")
    finally:
        led.close()
    marker = live_repo.parent / "the-strategy-code-ran"
    (live_repo / "strategies" / f"coded_h{hid}.py").write_text(
        f'"""version: 1.0.0\nhypothesis: code\n"""\nimport pathlib\npathlib.Path({str(marker)!r}).write_text("ran")\n')
    status, text = D.check_ledger(paths)
    assert not marker.exists()
    assert status == D.OK and "2 in paper trading (1 not checked: code strategy))" in text, text
    _ledger_with(live_repo, "mom_both_basket", direction="both")
    status, text = D.check_ledger(paths)
    assert not marker.exists()
    assert "2 in paper trading (1 not stepped: can hold a short; 1 not checked: code strategy))" in text, text


def test_the_doctor_says_the_broker_book_file_is_missing_and_that_live_mode_would_stop(live_repo):
    before = _tree(live_repo)
    status, detail = _venues(live_repo)
    assert "broker book: config/broker_book.json is missing" in detail and "live mode would stop for that" in detail
    assert "never follows the whole book" in detail
    assert _tree(live_repo) == before


def test_the_doctor_says_which_strategies_the_broker_book_follows(live_repo):
    hid = _ledger_with(live_repo)
    _owner(live_repo, "broker_book.json", {"follow": [hid]})
    before = _tree(live_repo)
    status, detail = _venues(live_repo)
    assert "broker book: follows 1 strategy: hold_rebalance_etf_2006_2026 (" in detail
    assert f"hypothesis {hid}" not in detail                  # A1: the ids are the file's values; the names are not
    assert "live mode would stop" not in detail
    assert _tree(live_repo) == before


@pytest.mark.parametrize("value, words", [({"follow": [True]}, "true or false"), ({"follow": []}, "at least one"),
                                          ({"follow": [42]}, "names a hypothesis the research ledger does not hold"),
                                          ('{"follow": [1], "follow": [2]}', "repeats a key")])
def test_the_doctor_says_a_refused_broker_book_file_would_stop_live_mode(live_repo, value, words):
    _ledger_with(live_repo)
    _owner(live_repo, "broker_book.json", value)
    status, detail = _venues(live_repo)
    assert "broker book: not followed: config/broker_book.json " in detail and words in detail
    assert "live mode would stop for that" in detail
    assert "42" not in detail.split("broker book: ")[1]       # A1: no id from the file


def test_the_doctor_reads_the_broker_book_through_the_order_steps_reading_function(live_repo, monkeypatch):
    from valedia_trader.research_loop import cli
    seen = []
    real = cli.broker_book_follow
    monkeypatch.setattr(cli, "broker_book_follow", lambda *a, **k: seen.append(k) or real(*a, **k))
    _venues(live_repo)
    assert seen == [{"live": True}]


def test_the_doctor_never_makes_the_research_ledger_to_read_the_broker_book(live_repo):
    _owner(live_repo, "broker_book.json", {"follow": [26]})
    status, detail = _venues(live_repo)
    assert "the research ledger (memory/ledger.db) is missing" in detail and "live mode would stop" in detail
    assert not (live_repo / "memory").exists()


# ---- T32 addendum A1: the doctor prints nothing taken from inside an owner file (no key name, no value, no piece of
# 5 characters or more of either); an unknown key is counted, not named. A2: what is judged only at a proposal.

PLANTED = {"account": "U83719464851", "key": "AKIAQ7XLMNBVZ9TT4P", "token": "tok_9fQLxm7Rk4Wb8ZpHs6"}
NUMBER = 83719464851                                    # an account number typed as a number
SMALL = 83719                                           # a piece of it that a checklist number can hold (<= 100,000)


def _pieces(text):
    return {text[i:i + 5] for i in range(len(text) - 4)}


def _plants():
    """(file, value) pairs: each planted text as a key name and as a value, and the account number as a number, in
    each of the four owner files."""
    out = []
    for what, s in PLANTED.items():
        out += [("webull_live.json", _confirmation(**{s: 1})), ("webull_live.json", _confirmation(note=s)),
                ("webull_live.json", _confirmation(confirmed_by=s)), ("webull_live.json", _confirmation(account_class=s)),
                ("webull_live.json", _confirmation(valid_until=s)), ("webull_live.json", {"account_id": s}),
                ("go_live.json", {**CHECKLIST, s: 1}), ("go_live.json", {**CHECKLIST, "confirmed_by": s}),
                ("go_live.json", {**CHECKLIST, "note": s}), ("go_live.json", {s: CHECKLIST}),
                ("risk.json", {**LIMITS, s: 1}), ("risk.json", {**LIMITS, "max_drawdown_pct": s}),
                ("broker_book.json", {"follow": [1], s: 1}), ("broker_book.json", {"follow": [1], "note": s}),
                ("broker_book.json", {"follow": [s]}), ("broker_book.json", {"follow": s}), ("broker_book.json", {s: [1]}),
                ("broker_book.json", {"follow": {s: 1}})]
    out += [("webull_live.json", _confirmation(max_capital=NUMBER)), ("webull_live.json", _confirmation(max_order_value=NUMBER)),
            ("webull_live.json", _confirmation(valid_until="2026-11-02")),
            ("go_live.json", {**CHECKLIST, "min_paper_broker_days": SMALL}),
            ("go_live.json", {**CHECKLIST, "min_reconciled_fills": SMALL, "min_fills_checked_by_hand": SMALL,
                              "min_days_since_a_stopped_run": SMALL}),
            ("risk.json", {**LIMITS, "max_open_positions": NUMBER}), ("risk.json", {**LIMITS, "max_open_positions": SMALL}),
            ("risk.json", {**LIMITS, "max_daily_loss_pct": NUMBER}), ("risk.json", {**LIMITS, "max_position_pct": SMALL}),
            ("risk.json", {**LIMITS, "max_orders_per_day": NUMBER}),
            ("broker_book.json", {"follow": [837194648]}), ("broker_book.json", {"follow": [1, 837194648]}),
            ("broker_book.json", {"follow": [837194648, 837194648]}), ("broker_book.json", {"follow": [8.37194]}),
            ("broker_book.json", {"follow": [-837194648]})]
    return out


@pytest.mark.parametrize("name, value", _plants(), ids=lambda x: x if isinstance(x, str) else None)
def test_t32_a1_nothing_from_inside_an_owner_file_reaches_the_doctors_output(live_repo, monkeypatch, name, value):
    for n, v in SECOND.items():
        monkeypatch.setenv(n, v)
    _ledger_with(live_repo)
    _paper_record(live_repo)
    files = {"webull_live.json": _confirmation(), "go_live.json": CHECKLIST, "risk.json": LIMITS,
             "broker_book.json": {"follow": [1]}}
    files[name] = value
    for n, v in files.items():
        _owner(live_repo, n, v)
    rows = D.run(live_repo, only=("risk limits", "venues"))
    out = json.dumps(rows) + "\n".join(f"{r['status']} {r['check']} {r['detail']}" for r in rows)
    assert len(rows) == 2 and "broker book: " in out
    candidates = [*PLANTED.values(), str(NUMBER), str(SMALL), "837194648", "8.37194", "2026-11-02"]
    planted = [s for s in candidates if s in json.dumps(value)]
    assert planted
    for s in planted:
        hits = sorted(p for p in _pieces(s) if p in out)
        assert not hits, (s, hits)


@pytest.mark.parametrize("name, base, n", [("webull_live.json", _confirmation(), 1), ("webull_live.json", _confirmation(), 3),
                                           ("webull_live.json", _confirmation(), 12), ("go_live.json", CHECKLIST, 1),
                                           ("go_live.json", CHECKLIST, 3), ("go_live.json", CHECKLIST, 12)])
def test_t32_a1_an_unknown_key_is_counted_not_named(live_repo, name, base, n):
    keys = [f"{PLANTED['token']}{i}" for i in range(n)]
    _owner(live_repo, "webull_live.json", _confirmation())
    _owner(live_repo, name, {**base, **{k: 1 for k in keys}})
    status, detail = _venues(live_repo)
    lock = "lock 1 (the confirmation)" if name == "webull_live.json" else "lock 2 (the checklist)"
    assert f"{lock} not met: config/{name} holds {n} unknown key{'s' if n != 1 else ''} " in detail
    assert not [p for p in _pieces(PLANTED["token"]) if p in detail]


def test_t32_a1_the_lock_judges_reasons_to_the_order_step_are_unchanged(live_repo):
    from valedia_trader.execution_and_risk import go_live
    from valedia_trader.venues import webull
    s = PLANTED["token"]
    _owner(live_repo, "webull_live.json", _confirmation(**{s: 1}))
    _owner(live_repo, "go_live.json", {**CHECKLIST, s: 1})
    state = go_live.judge_locks(go_live.LiveFiles(environ={}), webull.ny_date())
    assert s in state.locks[0]["reason"] and s in state.locks[1]["reason"]     # the order step still names the key


def test_t32_a1_a_short_paper_record_names_what_is_short_not_the_owners_figures(live_repo):
    _owner(live_repo, "go_live.json", {**CHECKLIST, "min_paper_broker_days": SMALL, "min_reconciled_fills": 4,
                                       "fills_checked_by_hand": 0})
    _paper_record(live_repo)
    status, detail = _venues(live_repo)
    assert "lock 2 (the checklist) not met: the paper record is short of config/go_live.json on: " in detail
    assert "paper trading days on which the order step ended done (1 so far)" in detail
    assert "fills journaled in paper runs with a clean reconcile (1 so far)" in detail
    assert "fills checked by hand" in detail and "stopped paper run" not in detail
    assert str(SMALL) not in detail and "needed" not in detail


def _b8fcd2d_row(limits):
    """The risk-limits row as b8fcd2d writes it for a file it accepts (valedia_trader/doctor.py there)."""
    text = (f"daily loss {limits['max_daily_loss_pct']}%, drawdown {limits['max_drawdown_pct']}%, position {limits['max_position_pct']:.0%}, "
            f"{limits['max_open_positions']} positions")
    if all(limits.get(k) == v for k, v in risk_gate.DEFAULT_LIMITS.items()):
        return D.ATTENTION, text + "; these are still the guide's example limits, not yours"
    return D.OK, text


IN_RANGE = [{"max_drawdown_pct": 17.75}, {"max_daily_loss_pct": 0.125}, {"max_open_positions": 2},
            {"max_open_positions": 25}, {"max_position_pct": 0.5}, {"max_daily_loss_pct": 2}, {"max_drawdown_pct": 25},
            {"max_daily_loss_pct": 0.5}, {"max_position_pct": 0.125}, {"max_drawdown_pct": 0.125},
            {"max_drawdown_pct": 17.75, "max_daily_loss_pct": 0.125, "max_position_pct": 0.5, "max_open_positions": 25},
            {"max_orders_per_day": 25}, {"max_open_positions": 1}, {"max_open_positions": 1000}, {}]


@pytest.mark.parametrize("over", IN_RANGE)
def test_t32_rev2_the_risk_limits_row_shows_in_range_limits_as_b8fcd2d_did(tmp_path, over):
    p = _paths(tmp_path)
    limits = {**risk_gate.DEFAULT_LIMITS, **over}
    p.risk_limits.write_text(json.dumps(limits))
    assert D.check_risk_limits(p) == _b8fcd2d_row(limits)
    assert "not shown" not in D.check_risk_limits(p)[1] and "out of range" not in D.check_risk_limits(p)[1]


def test_t32_rev2_the_risk_limits_row_on_the_tracked_file_is_b8fcd2d_s():
    tracked = D.REPO / "config" / "risk.json"
    assert D.check_risk_limits(LoopPaths(D.REPO)) == _b8fcd2d_row(json.loads(tracked.read_text()))


@pytest.fixture(scope="module")
def b8fcd2d_doctor(tmp_path_factory):
    """valedia_trader/doctor.py at b8fcd2d (needs git: the lead runs this one)."""
    import importlib.util
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    src = subprocess.run(["git", "-C", str(root), "show", "b8fcd2d:valedia_trader/doctor.py"], capture_output=True,
                         check=True).stdout
    path = tmp_path_factory.mktemp("b8fcd2d_doctor") / "doctor_b8fcd2d.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("doctor_b8fcd2d", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["doctor_b8fcd2d"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("over", IN_RANGE)
def test_t32_rev2_the_risk_limits_row_is_b8fcd2d_s_byte_for_byte(tmp_path, b8fcd2d_doctor, over):
    p = _paths(tmp_path)
    p.risk_limits.write_text(json.dumps({**risk_gate.DEFAULT_LIMITS, **over}))
    assert D.check_risk_limits(p) == b8fcd2d_doctor.check_risk_limits(p)
    tracked = LoopPaths(D.REPO)
    assert D.check_risk_limits(tracked) == b8fcd2d_doctor.check_risk_limits(tracked)


@pytest.mark.parametrize("over, words", [({"max_open_positions": NUMBER}, "positions out of range"),
                                         ({"max_open_positions": 1001}, "positions out of range"),
                                         ({"max_open_positions": SMALL}, "positions out of range")])
def test_t32_rev2_a_limit_out_of_its_range_is_described_not_quoted(tmp_path, over, words):
    p = _paths(tmp_path)
    p.risk_limits.write_text(json.dumps({**risk_gate.DEFAULT_LIMITS, **over}))
    status, detail = D.check_risk_limits(p)
    assert words in detail and str(over["max_open_positions"]) not in detail
    assert "daily loss 2.0%, drawdown 8.0%, position 10%" in detail                    # the others as before


@pytest.mark.parametrize("value", ["U83719464851", None, [1], True])
def test_t32_rev2_a_limit_that_is_not_a_number_is_described_not_quoted(tmp_path, value):
    p = _paths(tmp_path)
    p.risk_limits.write_text(json.dumps({**risk_gate.DEFAULT_LIMITS, "max_drawdown_pct": value}))
    status, detail = D.check_risk_limits(p)
    assert status == D.FAIL and "max_drawdown_pct is not a number" in detail and "every order is blocked" in detail
    assert not [x for x in _pieces(json.dumps(value)) if x in detail]


@pytest.mark.parametrize("n", [1, 3])
def test_t32_rev2_an_unknown_key_of_the_limits_file_is_counted_not_named(tmp_path, n):
    p = _paths(tmp_path)
    p.risk_limits.write_text(json.dumps({**risk_gate.DEFAULT_LIMITS, **{f"{PLANTED['token']}{i}": 1 for i in range(n)}}))
    status, detail = D.check_risk_limits(p)
    assert status == D.FAIL and f"holds {n} key{'s' if n != 1 else ''} that {'is' if n == 1 else 'are'} not a known limit" in detail
    assert not [x for x in _pieces(PLANTED["token"]) if x in detail]


# ---- T32 rev 2 outcome 2, at the doctor: a followed hypothesis the paper command does not step

@pytest.mark.parametrize("status", ["paused", "retired", "untested", "failed"])
def test_t32_rev2_the_doctor_says_a_followed_hypothesis_is_not_stepped_and_live_mode_would_stop(live_repo, status):
    from valedia_trader.research_loop import ledger as lg
    hid = _ledger_with(live_repo)
    led = lg.HypothesisLedger(live_repo / "memory" / "ledger.db")
    try:
        led.set_status(hid, status)
    finally:
        led.close()
    _owner(live_repo, "broker_book.json", {"follow": [hid]})
    status_, detail = _venues(live_repo)
    assert (f"broker book: not followed: config/broker_book.json names a hypothesis whose status in the research ledger "
            f"is {status}") in detail and "live mode would stop for that" in detail
    assert f"hypothesis {hid}" not in detail


def test_t32_a2_the_proposal_only_list_names_the_capital_the_money_at_work_and_the_environment_names(live_repo):
    status, detail = _venues(live_repo)
    proposal = detail.split("judged only when a batch is proposed and again when it is approved: ")[1]
    assert "the capital given with --capital against the confirmation's max_capital" in proposal
    assert "the money at work (what the account holds plus the batch's buys) against max_capital" in proposal
    assert "the live pair is read from webull-live.env when the venue is built (T50)" in proposal   # T50 (rewritten)
    assert "WEBULL_ENV and WEBULL_ACCOUNT_CLASS are read when the venue is built" not in proposal
    for old in ("lock 4 (the ticket)", "the account the venue will trade against the confirmation's class and last 4",
                "each order's value against max_order_value", "both second price sources agreeing on each price"):
        assert old in proposal


# ---- T50: a live name in reach of the practice runs fails the venues check; no scheduled job may name live mode

T50_KEY, T50_SECRET = "ZqLiveKey7Rt4Kw9PmXv2Bn", "ZqLiveSecret3LbNHs4GdYp8"          # made up
T50_NAMES = ("WEBULL_LIVE_APP_KEY", "WEBULL_LIVE_APP_SECRET")


@pytest.mark.parametrize("name", T50_NAMES)
def test_t50_a_live_name_in_reach_of_the_practice_runs_fails_the_venues_check(live_repo, monkeypatch, name):
    monkeypatch.setenv("WEBULL_ENV", "sandbox")
    status, detail = _venues(live_repo)
    assert "live" not in detail.split(";")[0] and T50_NAMES[0] not in detail
    monkeypatch.setenv(name, T50_SECRET)
    status, detail = _venues(live_repo)
    clean = T50_SECRET not in detail and T50_KEY not in detail
    assert clean and status == D.FAIL and name in detail and "webull-live.env" in detail


def _plist(path, label, args, workdir=None):
    import plistlib
    data = {"Label": label, "ProgramArguments": [str(a) for a in args]}
    if workdir is not None:
        data["WorkingDirectory"] = str(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(data))
    return path


def _jobs(tmp_path):
    launchd, agents, elsewhere = tmp_path / "tools" / "launchd", tmp_path / "LaunchAgents", tmp_path / "elsewhere"
    for d in (launchd, agents, elsewhere):
        d.mkdir(parents=True, exist_ok=True)
    (launchd / "orderstep-paper.sh").write_text('#!/bin/bash\nOUT=$("$PY" -m valedia_trader.research_loop order-step 2>&1)\n'
                                                "# a --liveness probe and --live-ish words are not live mode\n")
    _plist(launchd / "com.valedia.orderstep.paper.plist", "com.valedia.orderstep.paper",
           ["/bin/bash", launchd / "orderstep-paper.sh"])
    _plist(agents / "com.valedia.orderstep.paper.plist", "com.valedia.orderstep.paper",
           ["/bin/bash", launchd / "orderstep-paper.sh"])
    return launchd, agents, elsewhere


def _check_jobs(tmp_path, launchd, agents):
    return D.check_jobs(LoopPaths(tmp_path), launchd_dir=launchd, agents_dir=agents)


def test_t50_clean_job_files_pass_the_jobs_check(tmp_path):
    launchd, agents, _ = _jobs(tmp_path)
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.OK, detail
    assert "jobs" in D.CHECKS and D.CHECKS["jobs"] is D.check_jobs


@pytest.mark.parametrize("text", ["order-step --live --propose --capital 100", "order-step --live=1", "x --live\n",
                                  '"--live"', "export WEBULL_LIVE_APP_KEY", f"WEBULL_LIVE_APP_SECRET={T50_SECRET}"])
def test_t50_a_job_script_in_tools_launchd_naming_live_mode_or_a_live_name_fails_naming_the_file(tmp_path, text):
    launchd, agents, _ = _jobs(tmp_path)
    (launchd / "orderstep-paper.sh").write_text(f"#!/bin/bash\n{text}\n")
    status, detail = _check_jobs(tmp_path, launchd, agents)
    clean = T50_SECRET not in detail
    assert clean and status == D.FAIL and "orderstep-paper.sh" in detail, detail


def test_t50_a_job_plist_with_live_mode_in_its_arguments_fails_naming_the_file(tmp_path):
    launchd, agents, _ = _jobs(tmp_path)
    _plist(launchd / "com.valedia.orderstep.paper.plist", "com.valedia.orderstep.paper",
           ["python", "-m", "valedia_trader.research_loop", "order-step", "--live", "--propose"])
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.FAIL and "com.valedia.orderstep.paper.plist" in detail, detail


def test_t50_an_installed_agent_and_the_files_it_names_one_level_down_are_scanned(tmp_path):
    launchd, agents, elsewhere = _jobs(tmp_path)
    run = elsewhere / "run.sh"
    run.write_text("#!/bin/bash\nexec python -m valedia_trader.research_loop order-step --live --approve 1\n")
    _plist(agents / "com.valedia.hidden.plist", "com.valedia.hidden", ["/bin/bash", run])
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.FAIL and "run.sh" in detail, detail
    run.write_text("#!/bin/bash\nexec python -m valedia_trader.research_loop order-step\n")   # clean again
    _plist(agents / "com.valedia.hidden.plist", "com.valedia.hidden", ["/bin/bash", "run.sh"], workdir=elsewhere)
    assert _check_jobs(tmp_path, launchd, agents)[0] == D.OK
    run.write_text("#!/bin/bash\nWEBULL_LIVE_APP_KEY=x ./inner.sh\n")                   # a relative name, its folder
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.FAIL and "run.sh" in detail, detail


def test_t50_the_label_decides_which_installed_agents_are_scanned_and_only_one_level_down(tmp_path):
    launchd, agents, elsewhere = _jobs(tmp_path)
    _plist(agents / "com.other.thing.plist", "com.other.thing", ["tool", "--live"])          # not one of ours
    assert _check_jobs(tmp_path, launchd, agents)[0] == D.OK
    _plist(agents / "renamed.plist", "com.valedia.renamed", ["tool", "--live"])               # ours by its label
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.FAIL and "renamed.plist" in detail, detail
    (agents / "renamed.plist").unlink()
    inner = elsewhere / "inner.sh"
    inner.write_text("order-step --live --propose\n")
    outer = elsewhere / "outer.sh"
    outer.write_text(f"#!/bin/bash\n{inner}\n")
    _plist(agents / "com.valedia.outer.plist", "com.valedia.outer", ["/bin/bash", outer])
    assert _check_jobs(tmp_path, launchd, agents)[0] == D.OK                                  # two levels down: not read


def test_t50_missing_job_folders_are_said_and_nothing_raises(tmp_path):
    status, detail = _check_jobs(tmp_path, tmp_path / "no-launchd", tmp_path / "no-agents")
    assert status == D.FAIL and "does not exist on this machine" in detail, detail       # addendum 3 (C3): never OK
    assert str(tmp_path / "no-launchd") in detail and str(tmp_path / "no-agents") in detail
    bad = tmp_path / "agents"
    bad.mkdir()
    (bad / "com.valedia.broken.plist").write_text("not a plist --live")
    status, detail = _check_jobs(tmp_path, tmp_path / "no-launchd", bad)
    assert status == D.FAIL and "com.valedia.broken.plist" in detail, detail


# ---- T50 addendum 3 (C, D): the jobs check reads what launchd reads; what it cannot read or find fails, named; no
#      test of the doctor reads the real LaunchAgents folder

import os  # noqa: E402
import plistlib  # noqa: E402
import pwd  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_REAL_AGENTS = tuple({os.path.join(pwd.getpwuid(os.geteuid()).pw_dir, "Library", "LaunchAgents"),
                      os.path.realpath(os.path.join(pwd.getpwuid(os.geteuid()).pw_dir, "Library", "LaunchAgents")),
                      str(Path.home() / "Library" / "LaunchAgents")})
_REAL_READS: list = []


def _audit_real_agents(event, args):
    if event in ("open", "os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            where = os.fsdecode(args[0])
        except Exception:
            return
        if where.startswith(_REAL_AGENTS):
            _REAL_READS.append(event)


sys.addaudithook(_audit_real_agents)


@pytest.fixture(autouse=True)
def _t50_no_real_launch_agents(tmp_path_factory, monkeypatch):
    """Every test of this module gives the jobs check a temporary LaunchAgents folder (one that does not exist), and
    fails if anything under the real one was opened or listed."""
    monkeypatch.setattr(D, "LAUNCH_AGENTS", tmp_path_factory.mktemp("no-agents") / "LaunchAgents", raising=False)
    _REAL_READS.clear()
    yield
    reads = list(_REAL_READS)
    _REAL_READS.clear()
    assert reads == [], f"the real LaunchAgents folder was read ({len(reads)} times)"


def test_t50_a3_the_whole_doctor_reads_no_real_launch_agents_and_reports_the_missing_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_get", lambda *a, **k: (_ for _ in ()).throw(OSError("no server")))
    rows = {r["check"]: r for r in D.run(tmp_path, only=("jobs",))}
    assert rows["jobs"]["status"] == D.FAIL and "does not exist on this machine" in rows["jobs"]["detail"]
    assert str(D.LAUNCH_AGENTS) in rows["jobs"]["detail"] and _REAL_READS == []


def _ours(data):
    return {"Label": "com.valedia.orderstep.paper", **data}


@pytest.mark.parametrize("fmt", [plistlib.FMT_BINARY, plistlib.FMT_XML])
@pytest.mark.parametrize("where", ["launchd", "agents"])
def test_t50_a3_a_property_list_is_read_as_launchd_reads_it_binary_or_text(tmp_path, fmt, where):
    launchd, agents, _ = _jobs(tmp_path)
    folder = launchd if where == "launchd" else agents
    target = folder / "com.valedia.orderstep.paper.plist"
    for data in (_ours({"ProgramArguments": ["/bin/zsh", "-c", "cd /dépôt && python -m valedia_trader.research_loop "
                                             "order-step --live --propose"]}),
                 _ours({"ProgramArguments": ["/bin/zsh", "-c", "cd /dépôt && run"],
                        "EnvironmentVariables": {"WEBULL_LIVE_APP_KEY": "café"}}),
                 _ours({"ProgramArguments": ["/bin/echo", "ok"], "EnvironmentVariables": {"X": "é WEBULL_LIVE_APP_SECRET"}})):
        target.write_bytes(plistlib.dumps(data, fmt=fmt))
        status, detail = _check_jobs(tmp_path, launchd, agents)
        assert status == D.FAIL and target.name in detail, detail
    target.write_bytes(plistlib.dumps(_ours({"ProgramArguments": ["/bin/bash", str(launchd / "orderstep-paper.sh")]}),
                                      fmt=fmt))
    assert _check_jobs(tmp_path, launchd, agents)[0] == D.OK


@pytest.mark.parametrize("spelled", ["&#45;-live", "-&#x2D;live", "--liv&#101;", "<![CDATA[--live]]>",
                                     "WEBULL&#95;LIVE_APP_KEY=x"])
def test_t50_a3_an_entity_or_section_spelling_in_a_text_property_list_is_seen(tmp_path, spelled):
    launchd, agents, _ = _jobs(tmp_path)
    target = launchd / "com.valedia.orderstep.paper.plist"
    target.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>Label</key>'
                      '<string>com.valedia.orderstep.paper</string><key>ProgramArguments</key><array>'
                      f'<string>python</string><string>order-step</string><string>{spelled}</string></array></dict></plist>\n')
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.FAIL and target.name in detail, detail


def test_t50_a3_a_folder_that_does_not_exist_is_a_failure_that_names_it(tmp_path):
    launchd, agents, _ = _jobs(tmp_path)
    for missing_launchd in (True, False):
        gone = tmp_path / "gone"
        status, detail = _check_jobs(tmp_path, gone if missing_launchd else launchd, agents if missing_launchd else gone)
        assert status == D.FAIL and str(gone) in detail and "does not exist on this machine" in detail, detail


def _unreadable(path):
    path.chmod(0o000)
    return path


@pytest.mark.parametrize("what", ["agents folder", "launchd folder", "launchd script", "launchd plist", "agent plist",
                                  "named script"])
def test_t50_a3_anything_the_check_cannot_read_is_a_failure_that_names_it(tmp_path, what):
    launchd, agents, elsewhere = _jobs(tmp_path)
    run = elsewhere / "run.sh"
    run.write_text("#!/bin/bash\nexec python -m valedia_trader.research_loop order-step\n")
    _plist(agents / "com.valedia.hidden.plist", "com.valedia.hidden", ["/bin/bash", run])
    assert _check_jobs(tmp_path, launchd, agents)[0] == D.OK
    target = {"agents folder": agents, "launchd folder": launchd, "launchd script": launchd / "orderstep-paper.sh",
              "launchd plist": launchd / "com.valedia.orderstep.paper.plist",
              "agent plist": agents / "com.valedia.hidden.plist", "named script": run}[what]
    _unreadable(target)
    try:
        status, detail = _check_jobs(tmp_path, launchd, agents)
    finally:
        target.chmod(0o700)
    assert status == D.FAIL and target.name in detail and "could not be read" in detail, detail


@pytest.mark.parametrize("named", ["missing.sh", "folder/missing", "./gone.py"])
def test_t50_a3_a_script_a_job_names_that_does_not_exist_is_a_failure_that_names_it(tmp_path, named):
    launchd, agents, elsewhere = _jobs(tmp_path)
    _plist(agents / "com.valedia.hidden.plist", "com.valedia.hidden", ["/bin/bash", named], workdir=elsewhere)
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.FAIL and Path(named).name in detail and "does not exist" in detail, detail
    _plist(agents / "com.valedia.hidden.plist", "com.valedia.hidden", ["/bin/bash", elsewhere / "gone.sh"])
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.FAIL and "gone.sh" in detail, detail


def test_t50_a3_plain_words_a_job_passes_are_not_taken_for_files(tmp_path):
    launchd, agents, _ = _jobs(tmp_path)
    _plist(agents / "com.valedia.words.plist", "com.valedia.words",
           ["python3", "-m", "valedia_trader.research_loop", "order-step", "--root=/nowhere/at/all", "-c",
            "cd /x && run it", "https://example.invalid/a/b"])
    status, detail = _check_jobs(tmp_path, launchd, agents)
    assert status == D.OK, detail


# ---- T50 addendum 3 (C4, F): a trading file the doctor cannot load fails the venues check and says why (no value)

T50_PRACTICE = "ZqPracticeKey1234567"


@pytest.mark.parametrize("mode, text, words", [
    (0o644, f"WEBULL_APP_KEY={T50_PRACTICE}\n", "readable by group or others"),
    (0o000, f"WEBULL_APP_KEY={T50_PRACTICE}\n", "PermissionError"),
    (0o600, f"WEBULL_APP_KEY={T50_PRACTICE}\nWEBULL_LIVE_APP_KEY={T50_KEY}\n", "WEBULL_LIVE_APP_KEY"),
    (0o600, f"WEBULL_APP_KEY={T50_PRACTICE}\nWEBULL_APP_KEY={T50_PRACTICE}\n", "more than once"),
])
def test_t50_a3_a_trading_file_that_cannot_be_loaded_fails_the_venues_check_and_says_so(tmp_path, monkeypatch, mode, text,
                                                                                        words):
    from valedia_trader.venues import webull
    for name in ("WEBULL_APP_KEY", "WEBULL_APP_SECRET", "WEBULL_ENV", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                 *T50_NAMES):
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    monkeypatch.setattr(D, "live_mode_note", lambda paths: "live mode: not judged here")
    monkeypatch.setattr(webull.WebullVenue, "__init__", lambda *a, **k: pytest.fail("a venue was built"))
    f = tmp_path / "trading.env"
    f.write_text(text)
    f.chmod(mode)
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(f))
    try:
        status, detail = D.check_venues(LoopPaths(tmp_path))
    finally:
        f.chmod(0o600)
    clean = T50_PRACTICE not in detail and T50_KEY not in detail
    assert clean and status == D.FAIL and "could not be loaded" in detail and words in detail, detail
