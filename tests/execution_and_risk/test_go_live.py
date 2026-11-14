"""T22b: the go-live gate's locks read and judged alone (valedia_trader/execution_and_risk/go_live.py).

Every owner's file here lives in pytest's temporary folder (never under the repository's config/), and every check
asserts that nothing was written, created or repaired by the code."""
from __future__ import annotations

import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from valedia_trader.execution_and_risk import go_live as gl
from valedia_trader.research_loop.paper import Journal
from valedia_trader.venues import webull as wb

TODAY = date(2026, 11, 13)                                    # a Friday
REPO_CONFIG = Path(gl.REPO) / "config"


class WeekdayCalendar:
    """Monday to Friday are sessions, except the days given."""

    def __init__(self, holidays=()):
        self.holidays = {date.fromisoformat(d) for d in holidays}

    def is_session(self, day):
        d = date.fromisoformat(str(day)[:10])
        return d.weekday() < 5 and d not in self.holidays


CAL = WeekdayCalendar(holidays=("2026-11-11",))


def conf_dict(**over):
    good = {"account_class": "INDIVIDUAL_CASH", "account_ends": "1234", "confirmed_by": "Burton", "max_order_value": 250,
            "max_capital": 2000, "valid_until": (TODAY + timedelta(days=10)).isoformat(),
            "i_understand_this_trades_real_money": True}
    good.update(over)
    return {k: v for k, v in good.items() if v is not DROP}


def checklist_dict(**over):
    good = {"min_paper_broker_days": 3, "min_reconciled_fills": 2, "min_days_since_a_stopped_run": 2,
            "fills_checked_by_hand": 2, "min_fills_checked_by_hand": 2, "confirmed_by": "Burton"}
    good.update(over)
    return {k: v for k, v in good.items() if v is not DROP}


LIMITS = {"max_daily_loss_pct": 2.0, "max_drawdown_pct": 8.0, "max_position_pct": 0.10, "max_open_positions": 5,
          "max_orders_per_day": 10}
ENV = {"ALPACA_API_KEY": "a", "ALPACA_SECRET_KEY": "b", "FINNHUB_API_KEY": "c"}
DROP = object()


def _write(path: Path, value, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(mode)
    return path


def paper_journal(folder: Path, done_days=("2026-11-09", "2026-11-10", "2026-11-12"), fills_per_done=1, stops=(),
                  venue=gl.PAPER_VENUE):
    """A paper record: one `done` order step a day on `done_days` with its fills, and `stops` (day, stage) runs."""
    j = Journal(folder)
    for i, day in enumerate(done_days):
        batch = f"b{i}"
        for k in range(fills_per_done):
            j.write(day, {"type": "order_step", "venue": venue, "batch": batch, "event": "fill", "fill_id": f"f{i}{k}"})
        j.write(day, {"type": "order_step", "venue": venue, "batch": batch, "event": "summary", "status": "done",
                      "stage": None})
    for i, (day, stage) in enumerate(stops):
        batch = f"s{i}"
        j.write(day, {"type": "order_step", "venue": venue, "batch": batch, "event": "fill", "fill_id": f"x{i}"})
        j.write(day, {"type": "order_step", "venue": venue, "batch": batch, "event": "stopped", "stage": stage, "reason": "r"})
        j.write(day, {"type": "order_step", "venue": venue, "batch": batch, "event": "summary", "status": "stopped",
                      "stage": stage})
    return folder


def world(tmp_path, conf=None, checklist=None, limits=None, environ=None, **journal):
    cfg = tmp_path / "repo" / "config"
    files = gl.LiveFiles(confirmation=cfg / "webull_live.json", checklist=cfg / "go_live.json", limits=cfg / "risk.json",
                         journal=paper_journal(tmp_path / "repo" / "journal", **journal), root=tmp_path / "repo",
                         environ=ENV if environ is None else environ, calendar=CAL)
    for path, value in ((files.confirmation, conf_dict() if conf is None else conf),
                        (files.checklist, checklist_dict() if checklist is None else checklist),
                        (files.limits, LIMITS if limits is None else limits)):
        if value is not DROP:
            _write(path, value)
    return files


def _bytes(p: Path):
    try:
        return p.read_bytes() if p.is_file() and not p.is_symlink() else None
    except PermissionError:
        return "unreadable"


def snapshot(folder: Path):
    return {str(p.relative_to(folder)): (p.is_symlink(), _bytes(p), p.stat(follow_symlinks=False).st_mode,
                                          p.stat(follow_symlinks=False).st_mtime_ns)
            for p in sorted(folder.rglob("*"))}


def test_all_four_locks_met_on_a_good_world(tmp_path):
    files = world(tmp_path)
    state = gl.judge_locks(files, TODAY, capital=1500)
    assert state.met and state.unmet() is None and state.digest
    assert [lock["lock"] for lock in state.locks] == [1, 2, 3, 4]
    assert state.confirmation["max_capital"] == 2000.0 and state.max_orders_per_day == 10
    two = state.locks[1]["figures"]
    assert two["record"] == {"paper_broker_days": 3, "reconciled_fills": 3, "days_since_a_stopped_run": None,
                             "last_stopped_run": None}
    assert two["owner"]["min_paper_broker_days"] == 3


# ---- invariant 2: the code never writes, creates, repairs or defaults an owner's file

@pytest.mark.parametrize("which", ["confirmation", "checklist", "limits"])
@pytest.mark.parametrize("how", ["missing", "unreadable", "malformed", "duplicate key", "file link", "folder link",
                                 "group-writable", "world-writable", "a folder", "empty"])
def test_any_unsafe_owner_file_is_an_unmet_lock_and_nothing_is_touched(tmp_path, which, how):
    files = world(tmp_path)
    path = getattr(files, which)
    good = path.read_text()
    if how == "missing":
        path.unlink()
    elif how == "unreadable":
        path.chmod(0o000)
    elif how == "malformed":
        _write(path, good[:-3])
    elif how == "duplicate key":
        _write(path, good[:-1] + ', ' + good[1:])
    elif how == "file link":
        real = _write(tmp_path / "elsewhere" / "x.json", good)
        path.unlink()
        path.symlink_to(real)
    elif how == "folder link":
        real = tmp_path / "elsewhere"
        real.mkdir()
        for p in path.parent.iterdir():
            (real / p.name).write_bytes(p.read_bytes())
            (real / p.name).chmod(0o644)
        cfg = path.parent
        for p in list(cfg.iterdir()):
            p.unlink()
        cfg.rmdir()
        cfg.symlink_to(real)
    elif how == "group-writable":
        path.chmod(0o664)
    elif how == "world-writable":
        path.chmod(0o646)
    elif how == "a folder":
        path.unlink()
        path.mkdir()
    else:
        _write(path, "")
    before = snapshot(tmp_path)
    state = gl.judge_locks(files, TODAY, capital=1000)
    after = snapshot(tmp_path)
    if how == "unreadable":
        path.chmod(0o644)
    n = {"confirmation": 1, "checklist": 2, "limits": 3}[which]
    if how == "folder link":                          # the whole config folder is a link: every file in it is unmet
        assert not any(lock["met"] for lock in state.locks[:3])
    else:
        assert not state.locks[n - 1]["met"]
        assert [lock["met"] for i, lock in enumerate(state.locks[:3]) if i != n - 1] == [True, True]
    assert not state.met and state.digest is None and f"lock {n}" in state.unmet()
    assert after == before                            # nothing written, created, repaired or defaulted


def test_no_environment_variable_stands_in_for_a_file(tmp_path, monkeypatch):
    files = world(tmp_path, conf=DROP, checklist=DROP)
    for name in ("WEBULL_LIVE", "WEBULL_LIVE_CONFIRMATION", "GO_LIVE", "VALEDIA_GO_LIVE", "LIVE_CONFIRMED"):
        monkeypatch.setenv(name, json.dumps(conf_dict()))
    state = gl.judge_locks(gl.LiveFiles(**{**files.__dict__, "environ": {**ENV, **os.environ}}), TODAY)
    assert [lock["met"] for lock in state.locks] == [False, False, True, True]
    assert "is missing" in state.locks[0]["reason"] and "there is no live mode without it" in state.locks[1]["reason"]
    assert not files.confirmation.exists() and not files.checklist.exists()


def test_the_defaults_are_the_repositorys_own_files_which_no_test_here_writes():
    assert gl.CHECKLIST == REPO_CONFIG / "go_live.json" and gl.LIMITS == REPO_CONFIG / "risk.json"
    assert wb.LIVE_CONFIRMATION == REPO_CONFIG / "webull_live.json"


# ---- invariant 3: lock 1, the confirmation in its new form

@pytest.mark.parametrize("change, words", [
    ({"account_class": "INDIVIDUAL_MARGIN"}, "exactly INDIVIDUAL_CASH"),
    ({"account_class": "individual_cash"}, "exactly INDIVIDUAL_CASH"),
    ({"account_class": "EVENTS_CASH"}, "event contracts have no live mode"),
    ({"account_ends": "123"}, "last 4"),
    ({"account_ends": "ACC-0001-1234"}, "last 4"),
    ({"account_ends": 1234}, "last 4"),
    ({"confirmed_by": ""}, "confirmed_by"),
    ({"confirmed_by": "  "}, "confirmed_by"),
    ({"confirmed_by": 7}, "confirmed_by"),
    ({"max_order_value": True}, "max_order_value"),
    ({"max_order_value": 0}, "max_order_value"),
    ({"max_order_value": -1}, "max_order_value"),
    ({"max_order_value": "250"}, "max_order_value"),
    ({"max_order_value": 1e300}, "max_order_value"),
    ({"max_capital": False}, "max_capital"),
    ({"max_capital": 100}, "above max_capital"),
    ({"valid_until": (TODAY - timedelta(days=1)).isoformat()}, "lapsed"),
    ({"valid_until": (TODAY + timedelta(days=32)).isoformat()}, "31 days"),
    ({"valid_until": "11/20/2026"}, "YYYY-MM-DD"),
    ({"valid_until": "2026-11-20T00:00"}, "YYYY-MM-DD"),
    ({"valid_until": 20261120}, "YYYY-MM-DD"),
    ({"i_understand_this_trades_real_money": "true"}, "must be true"),
    ({"i_understand_this_trades_real_money": 1}, "must be true"),
    ({"extra": 1}, "not part of a confirmation"),
    ({"confirmed_by": DROP}, "lacks confirmed_by"),
    ({"account_id": "ACC-0001-1234"}, "old form")])
def test_lock_one_refuses_each_rule_broken(tmp_path, change, words):
    state = gl.judge_locks(world(tmp_path, conf=conf_dict(**change)), TODAY)
    assert not state.locks[0]["met"] and words in state.locks[0]["reason"] and state.confirmation is None
    assert [lock["met"] for lock in state.locks[1:]] == [True, True, True]


def test_lock_one_accepts_today_and_31_days_ahead_and_a_decimal_cap(tmp_path):
    for until in (TODAY, TODAY + timedelta(days=31)):
        state = gl.judge_locks(world(tmp_path / until.isoformat(), conf=conf_dict(valid_until=until.isoformat(),
                                                                                     max_order_value=249.5)), TODAY)
        assert state.locks[0]["met"], state.locks[0]["reason"]


def test_the_old_form_is_refused_by_the_order_steps_lock(tmp_path):
    old = {"account_id": "ACC-0001-1234", "confirmed_by": "Burton", "max_order_value": 1000,
           "i_understand_this_trades_real_money": True}
    state = gl.judge_locks(world(tmp_path, conf=old), TODAY)
    assert not state.locks[0]["met"] and "old form" in state.locks[0]["reason"]
    assert "ACC-0001" not in json.dumps(state.locks)                  # invariant 10: no account id in what is said


def test_capital_above_max_capital_says_by_how_much(tmp_path):
    files = world(tmp_path)
    state = gl.judge_locks(files, TODAY, capital=2500)
    assert not state.locks[0]["met"] and "above max_capital 2,000.00 (by 500.00)" in state.locks[0]["reason"]
    for bad in (0, -5, float("nan"), float("inf"), True, "100"):
        assert not gl.judge_locks(files, TODAY, capital=bad).locks[0]["met"]
    assert gl.judge_locks(files, TODAY, capital=2000).locks[0]["met"]


_NOT_A_VALUE = [True, False, None, [], {}, [1], {"a": 1}, "", " ", "x" * 5000, "\u0000", "NaN", "1e3"]


def _bad_value(rng, key: str):
    """A value the field `key` may not hold (a confirmation's or a checklist's): wrong types, booleans as numbers,
    huge, negative, zero where 1 is the least, decimals where whole numbers are asked, dates in other formats."""
    if key in ("max_order_value", "max_capital"):
        pool = [0, -0.0, -1, -rng.random() * 1e6, rng.randint(-10 ** 9, -1), 1e10, 10 ** 400, 2 ** 63, "250", "1,000"]
    elif key in gl.CHECKLIST_NUMBERS:
        pool = [-1, rng.randint(-10 ** 9, -1), 1.5, 2.0, 0.0, gl.CHECKLIST_MAX + 1, 10 ** 400, 2 ** 63, "3", rng.random()]
        if key in gl.AT_LEAST_ONE:
            pool += [0, 0, 0]
    elif key == "valid_until":
        pool = ["20-11-2026", "2026/11/20", "Nov 20 2026", "2026-13-01", "2026-02-30", "2026-11-20T00:00", "2026-11-2",
                " 2026-11-20", 20261120, (TODAY - timedelta(days=rng.randint(1, 900))).isoformat(),
                (TODAY + timedelta(days=rng.randint(32, 900))).isoformat(), "\ufeff2026-11-20"]
    elif key == "account_ends":
        pool = ["123", "12345", "12 4", "ACC-0001-1234", 1234, "12-4", "\ufeff1234", "1234\n", "１２３４"]
    elif key == "account_class":
        pool = ["INDIVIDUAL_MARGIN", "individual_cash", "EVENTS_CASH", "INDIVIDUAL_CASH ", "CASH", 1]
    elif key == "confirmed_by":
        pool = ["", "   ", "x" * 300, "a\nb", "\u0000", 7, 1.5]
    elif key == "i_understand_this_trades_real_money":
        pool = ["true", 1, 1.0, "yes", False, "True"]
    else:
        pool = []
    generic = [v for v in _NOT_A_VALUE if key != "confirmed_by" or v in ("", " ", "x" * 5000, "\u0000") or type(v) is not str]
    return rng.choice(pool + generic)


def _mangle(rng, good: dict) -> str:
    """One malformed file's text, from a good dict: a wrong value, a key dropped, an extra key, a repeated key, a raw
    NaN or Infinity, a different shape, broken text, or several of the wrong values at once."""
    keys = list(good)
    kind = rng.randrange(8)
    d = dict(good)
    if kind == 0:
        key = rng.choice(keys)
        d[key] = _bad_value(rng, key)
    elif kind == 1:
        for key in rng.sample(keys, rng.randint(1, 3)):
            del d[key]
    elif kind == 2:
        d[rng.choice(["extra", "Account_class", "max_capital ", "note", "account_id", "MAX_CAPITAL", "min_days",
                      "confirmed by"])] = rng.choice([1, "x", True, None])
    elif kind == 3:
        key = rng.choice(keys)
        text = json.dumps(d)
        return text[:-1] + f", {json.dumps(key)}: {json.dumps(rng.choice([good[key], _bad_value(rng, key)]))}}}"
    elif kind == 4:
        key = rng.choice(keys)
        text = json.dumps(d)
        return text.replace(f'"{key}": {json.dumps(good[key])}', f'"{key}": {rng.choice(["NaN", "Infinity", "-Infinity"])}')
    elif kind == 5:
        return json.dumps(rng.choice([[d], list(d.items()), "text", rng.randint(0, 99), None, True, [], {"x": d}]))
    elif kind == 6:
        text = json.dumps(d)
        cut = rng.randrange(1, len(text))
        return rng.choice([text[:cut], text[cut:], text.replace(":", "=", 1), text + ",", "\x00" + text,
                           text.encode().hex(), text.replace('"', "'"), text.encode("utf-16").decode("latin-1")])
    else:
        for key in rng.sample(keys, rng.randint(2, len(keys))):
            d[key] = _bad_value(rng, key)
    try:
        return json.dumps(d)
    except (ValueError, OverflowError):                   # 10 ** 400 as a float: written as the number text instead
        return json.dumps({k: (str(v) if isinstance(v, int) and abs(v) > 10 ** 300 else v) for k, v in d.items()}).replace(
            json.dumps(str(10 ** 400)), str(10 ** 400))


def _many_malformed(rng, good: dict, n: int):
    """`n` distinct malformed texts, and about one in twelve a good text in a file that its group or others may write."""
    out, seen = [], set()
    while len(out) < n:
        if rng.random() < 0.08:
            item = (json.dumps(good), rng.choice([0o664, 0o646, 0o666, 0o622, 0o660, 0o602]))
        else:
            item = (_mangle(rng, good), 0o644)
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def test_two_hundred_and_fifty_malformed_confirmation_files_none_opens_lock_one(tmp_path):
    files = world(tmp_path)
    good = conf_dict()
    assert gl.judge_locks(files, TODAY).locks[0]["met"]
    opened = []
    for text, mode in _many_malformed(random.Random(20261113), good, 250):
        files.confirmation.write_text(text, errors="surrogatepass")
        files.confirmation.chmod(mode)
        state = gl.judge_locks(files, TODAY, capital=100)          # never raises
        data, why = wb.live_confirmation(files.confirmation, today=TODAY, root=files.root)
        if state.locks[0]["met"] or data is not None or state.digest is not None or not why:
            opened.append((text, oct(mode)))
    assert opened == []


def test_two_hundred_and_fifty_malformed_checklist_files_none_opens_lock_two(tmp_path):
    files = world(tmp_path)
    good = checklist_dict(min_paper_broker_days=1, min_reconciled_fills=1, min_days_since_a_stopped_run=0,
                          fills_checked_by_hand=0, min_fills_checked_by_hand=0)
    _write(files.checklist, good)
    assert gl.judge_locks(files, TODAY).locks[1]["met"]       # the easiest checklist opens it: no mangled one may
    opened = []
    for text, mode in _many_malformed(random.Random(4242), good, 250):
        files.checklist.write_text(text, errors="surrogatepass")
        files.checklist.chmod(mode)
        state = gl.judge_locks(files, TODAY)
        if state.locks[1]["met"] or state.digest is not None:
            opened.append((text, oct(mode)))
    assert opened == []


# ---- invariant 4: lock 2, the checklist judged on the paper record

@pytest.mark.parametrize("change, words", [
    ({"min_paper_broker_days": 0}, "min_paper_broker_days must be a whole number from 1"),
    ({"min_reconciled_fills": 0}, "min_reconciled_fills must be a whole number from 1"),
    ({"min_days_since_a_stopped_run": -1}, "from 0"),
    ({"fills_checked_by_hand": 2.0}, "not a decimal"),
    ({"min_fills_checked_by_hand": True}, "not true or false"),
    ({"min_paper_broker_days": "3"}, "not text"),
    ({"min_paper_broker_days": 10 ** 6}, "100,000"),
    ({"confirmed_by": ""}, "confirmed_by must be a name"),
    ({"confirmed_by": DROP}, "lacks confirmed_by"),
    ({"min_reconciled_fills": DROP}, "there is no default"),
    ({"note": "hi"}, "not part of the checklist")])
def test_lock_two_refuses_a_checklist_that_breaks_a_rule(tmp_path, change, words):
    state = gl.judge_locks(world(tmp_path, checklist=checklist_dict(**change)), TODAY)
    assert not state.locks[1]["met"] and words in state.locks[1]["reason"]


def test_lock_two_says_how_far_short_each_figure_is(tmp_path):
    files = world(tmp_path, checklist=checklist_dict(min_paper_broker_days=5, min_reconciled_fills=7,
                                                     fills_checked_by_hand=1, min_fills_checked_by_hand=4))
    reason = gl.judge_locks(files, TODAY).locks[1]["reason"]
    assert "3 paper trading days on which the order step ended done, 5 needed (2 short)" in reason
    assert "3 fills journaled in paper runs with a clean reconcile, 7 needed (4 short)" in reason
    assert "1 fills checked by hand, 4 needed (3 short)" in reason


def test_the_paper_record_counts_only_paper_done_days_on_sessions_and_fills_of_clean_runs(tmp_path):
    folder = paper_journal(tmp_path / "j", done_days=("2026-11-09", "2026-11-09", "2026-11-10", "2026-11-11",
                                                      "2026-11-08", "2026-11-14"), fills_per_done=2,
                           stops=(("2026-11-12", "session"),))
    j = Journal(folder)
    # a production run's record and another environment's are not the paper record
    for venue in (gl.LIVE_VENUE, "webull-sandbox-events", "alpaca-paper"):
        j.write("2026-11-12", {"type": "order_step", "venue": venue, "batch": "p", "event": "fill"})
        j.write("2026-11-12", {"type": "order_step", "venue": venue, "batch": "p", "event": "summary", "status": "done"})
        j.write("2026-11-12", {"type": "order_step", "venue": venue, "batch": "q", "event": "summary", "status": "stopped",
                               "stage": "reconcile"})
    j.write("2026-11-12", {"type": "paper", "venue": gl.PAPER_VENUE, "batch": "b0", "event": "fill"})   # not the order step
    record, why = gl.paper_record(folder, gl.PAPER_VENUE, TODAY, CAL)
    assert why is None
    # 11-09 (twice), 11-10 count; 11-11 is a holiday, 11-08 a Sunday, 11-14 after today
    assert record["paper_broker_days"] == 2
    assert record["reconciled_fills"] == 12              # 6 done runs x 2 fills; the session-stopped run's fill not counted
    assert record["days_since_a_stopped_run"] is None    # a stop at stage `session` is ordinary


def test_a_stopped_paper_run_counts_by_its_days(tmp_path):
    for since, needed, met in ((3, 3, True), (2, 3, False), (0, 0, True), (0, 1, False)):
        stop_day = (TODAY - timedelta(days=since)).isoformat()
        files = world(tmp_path / f"{since}-{needed}", checklist=checklist_dict(min_days_since_a_stopped_run=needed),
                      stops=((stop_day, "reconcile"),))
        lock = gl.judge_locks(files, TODAY).locks[1]
        assert lock["met"] is met, lock["reason"]
        assert lock["figures"]["record"]["days_since_a_stopped_run"] == since
        if not met:
            assert f"was {since} days ago ({stop_day})" in lock["reason"] and f"({needed - since} short)" in lock["reason"]


def test_a_damaged_journal_is_an_unmet_lock_two(tmp_path):
    files = world(tmp_path)
    day_file = sorted(files.journal.glob("*.jsonl"))[0]
    with day_file.open("ab") as fh:
        fh.write(b'{"type": "order_step", "venue": "webull-sandbox", "ev')
    state = gl.judge_locks(files, TODAY)
    assert not state.locks[1]["met"] and "the journal is damaged (1 line cannot be read)" in state.locks[1]["reason"]


def test_a_missing_journal_folder_is_no_record_and_is_not_created(tmp_path):
    files = world(tmp_path)
    for p in files.journal.iterdir():
        p.unlink()
    files.journal.rmdir()
    state = gl.judge_locks(files, TODAY)
    assert not state.locks[1]["met"] and "no paper record" in state.locks[1]["reason"]
    assert not files.journal.exists()


def test_an_undated_stopped_run_is_not_guessed(tmp_path):
    files = world(tmp_path)
    Journal(files.journal).write("2026-11-12", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "z"})
    path = files.journal / "2026-11-12.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps({"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "z", "event": "summary",
                             "status": "stopped", "stage": "send", "day": "someday"}) + "\n")
    assert "carries no date" in gl.judge_locks(files, TODAY).locks[1]["reason"]


# ---- invariant 5 (the file side): lock 3, the limits

@pytest.mark.parametrize("cap, met", [(None, False), (0, False), (201, False), (True, False), (10.0, False), ("10", False),
                                      (1, True), (200, True), (40, True)])
def test_lock_three_needs_max_orders_per_day_from_1_to_200(tmp_path, cap, met):
    limits = {k: v for k, v in LIMITS.items() if k != "max_orders_per_day"}
    if cap is not None:
        limits["max_orders_per_day"] = cap
    state = gl.judge_locks(world(tmp_path, limits=limits), TODAY)
    assert state.locks[2]["met"] is met
    if not met:
        assert "max_orders_per_day" in state.locks[2]["reason"] and state.max_orders_per_day is None


def test_lock_three_needs_the_risk_gate_to_accept_the_limits(tmp_path):
    state = gl.judge_locks(world(tmp_path, limits={**LIMITS, "max_position_pct": 2.0}), TODAY)
    assert not state.locks[2]["met"] and "risk gate accepts" in state.locks[2]["reason"]


@pytest.mark.parametrize("absent", gl.SECOND_SOURCE_KEYS)
def test_lock_three_needs_both_second_sources_keys(tmp_path, absent):
    for env in ({k: v for k, v in ENV.items() if k != absent}, {**ENV, absent: ""}, {**ENV, absent: "  "}):
        state = gl.judge_locks(world(tmp_path / str(len(env)) / repr(env[absent] if absent in env else None).strip("'"),
                                     environ=env), TODAY)
        assert not state.locks[2]["met"] and absent in state.locks[2]["reason"]


def test_the_digest_follows_each_file_and_is_kept_only_when_locks_one_to_three_are_met(tmp_path):
    files = world(tmp_path)
    first = gl.judge_locks(files, TODAY).digest
    assert first == gl.judge_locks(files, TODAY).digest
    seen = {first}
    for path, value in ((files.confirmation, conf_dict(max_capital=1999)), (files.checklist, checklist_dict(confirmed_by="B")),
                        (files.limits, {**LIMITS, "max_orders_per_day": 9})):
        _write(path, value)
        d = gl.judge_locks(files, TODAY).digest
        assert d and d not in seen
        seen.add(d)
    _write(files.limits, {**LIMITS, "max_orders_per_day": 0})
    assert gl.judge_locks(files, TODAY).digest is None


# ---- T22b rulings: the key is min_days_since_a_stopped_run; a stop at stage session or ticket is not a stopped run

def test_the_checklist_key_is_min_days_since_a_stopped_run_and_the_old_name_is_refused(tmp_path):
    good = gl.judge_locks(world(tmp_path / "new"), TODAY).locks[1]
    assert good["met"] and "min_days_since_a_stopped_run" in good["figures"]["owner"]
    assert "max_days_since_a_stopped_run" not in json.dumps(good)
    old = {**{k: v for k, v in checklist_dict().items() if k != "min_days_since_a_stopped_run"},
           "max_days_since_a_stopped_run": 2}
    lock = gl.judge_locks(world(tmp_path / "old", checklist=old), TODAY).locks[1]
    assert not lock["met"] and "not part of the checklist" in lock["reason"] and "max_days_since_a_stopped_run" in lock["reason"]
    both = {**checklist_dict(), "max_days_since_a_stopped_run": 2}
    assert not gl.judge_locks(world(tmp_path / "both", checklist=both), TODAY).locks[1]["met"]


def test_the_shortfall_text_names_min_days_since_a_stopped_run(tmp_path):
    files = world(tmp_path, checklist=checklist_dict(min_days_since_a_stopped_run=3), stops=(("2026-11-12", "send"),))
    reason = gl.judge_locks(files, TODAY).locks[1]["reason"]
    assert "min_days_since_a_stopped_run 3" in reason and "(2 short)" in reason


@pytest.mark.parametrize("stage, counts", [("session", False), ("ticket", False), ("reconcile", True), ("send", True),
                                          ("baseline", True), ("lock", True), ("environment", True), ("unexpected", True),
                                          ("prices", True), ("after", True), ("journal", True)])
def test_only_stops_at_stages_other_than_session_and_ticket_are_stopped_runs(tmp_path, stage, counts):
    record, why = gl.paper_record(paper_journal(tmp_path / "j", stops=(("2026-11-12", stage),)), gl.PAPER_VENUE, TODAY, CAL)
    assert why is None
    assert record["days_since_a_stopped_run"] == (1 if counts else None)
    assert record["last_stopped_run"] == ("2026-11-12" if counts else None)


def test_a_refused_approval_is_not_a_stopped_run_but_a_practice_run_stopped_elsewhere_is(tmp_path):
    folder = tmp_path / "j"
    j = Journal(folder)
    for batch, mode, status, stage in (("p1", "propose", "done", None), ("a1", "approve", "stopped", "ticket"),
                                       ("a2", "approve", "done", None)):
        if status == "done" and mode == "approve":
            j.write("2026-11-10", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": batch, "event": "fill",
                                   "fill_id": "fa2"})
        j.write("2026-11-10", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": batch, "event": "summary",
                               "status": status, "stage": stage, "mode": mode})
    record, _ = gl.paper_record(folder, gl.PAPER_VENUE, TODAY, CAL)
    assert record == {"paper_broker_days": 1, "reconciled_fills": 1, "days_since_a_stopped_run": None,
                      "last_stopped_run": None}               # practice runs count; a refused approval is no stop
    j.write("2026-11-12", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "a3", "event": "summary",
                           "status": "stopped", "stage": "send", "mode": "approve"})
    record, _ = gl.paper_record(folder, gl.PAPER_VENUE, TODAY, CAL)
    assert record["days_since_a_stopped_run"] == 1 and record["last_stopped_run"] == "2026-11-12"
