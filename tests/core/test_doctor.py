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
    assert len(seen) == 1 and sorted(p.name for p in reads) == ["go_live.json", "risk.json", "webull_live.json"]


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
