"""The go-live gate's locks (docs/specs/2026-11-13-go-live-gate-design.md, step 2): what must hold before the order
step may run in live mode, read and judged as plain functions of the owner's files, the journal folder and a date.

Nothing here writes, creates, repairs or defaults a file. Every owner's file is read by the adapter's
`read_owner_file` (a missing, unreadable, malformed, duplicate-keyed, linked or group/world-writable file is an unmet
lock), and nothing else stands in for one: no environment variable, flag or argument.

* Lock 1, the confirmation (`config/webull_live.json`, new form only): `webull.judge_live_confirmation`, plus the
  capital asked for at most its `max_capital`. Its account (the class and the last 4 characters of the account the
  venue will trade) is compared by the order step when it first reads the account, before any other account read.
* Lock 2, the checklist (`config/go_live.json`): exactly the six keys (five whole numbers from 0 to CHECKLIST_MAX,
  `min_paper_broker_days` and `min_reconciled_fills` at least 1, and `confirmed_by`), judged against figures the
  order step computes from the PAPER record only: the journal entries of the test-environment venue (`paper_venue`).
  A damaged journal (any skipped line: `Journal.read_complete`) is an unmet lock.
    - paper_broker_days: New York trading days on which a paper order step ended `done`;
    - reconciled_fills: fills journaled in paper runs that ended `done` (their reconciles were clean);
    - days_since_a_stopped_run: New York days since the last paper run (unattended or practice alike) that stopped
      at any stage but `session` (outside hours or a holiday), `ticket` (a refused approval: a wrong, expired, used
      or superseded number is the owner's slip, not a failure of the order path) and `shortfall` (a shared run found
      the account holding less than the book counts as its own: a skipped session, R24/R32; only an entry whose
      summary carries a `shared` block that is a record: the runner raises that stage in shared mode only, so one
      without is corrupt and is a stopped run like any other); met when it is at least
      `min_days_since_a_stopped_run`, or when no paper run ever stopped: no stopped run within that many days;
    A run that ended `done` but whose summary says its last reconcile found a shortfall (a `shared` block whose
    `shortfalls` is anything that is true in Python: a non-empty record, list or text, or a number but 0; `shortfalls`
    absent, null, 0, false or empty is none; or a `shared` block that is not a record at all: the safe side) is NOT clean (R33): its fills are not reconciled_fills and
    it is not a paper_broker_day; it does not reset the stopped-run clock (the owner acted; nothing of hers failed). The
    record then lists those runs' days as `unclean_shared_runs`, and the days of runs stopped at `shortfall` as
    `skipped_shortfall_runs` (a key only when there are some).
    A run that ended `done` whose summary carries `faults` (a list with anything in it; a `faults` that is not a list
    is read on the safe side, as a fault; absent or an empty list is none) is likewise NOT clean: its fills are not
    reconciled_fills and it is not a paper_broker_day; it too leaves the stopped-run clock alone. The record lists its
    day as `faulted_runs` (a key only when there are some).
    The record also shows, over the fills of clean `done` runs only, how many were `whole_fills` (units above 0 and
    leg not `remainder`: a whole share), `remainder_fills` (leg `remainder`, units above 0) and `sale_fills` (units
    below 0); a fill whose units are not a nonzero number is in none. They are shown only (no lock reads them), and
    only when at least one fill was counted into a shape.
    - fills_checked_by_hand at least min_fills_checked_by_hand (the owner's own statement).
* Lock 3, the limits (`config/risk.json`): the risk gate's own file, which in live mode must hold
  `max_orders_per_day`, a whole number from 1 to 200 (the test environment's fallback of 40 does not apply), and
  pass the risk gate's own reading; and the second price sources configured: ALPACA_API_KEY, ALPACA_SECRET_KEY and
  FINNHUB_API_KEY present in the environment the command loaded. The verdict for each order is the order step's.
* Lock 4, the ticket: live mode never sends in a run that decides (propose, then approve by number); always part
  of the design, judged by the order step's ticket checks.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from valedia_trader.research_loop import risk_gate
from valedia_trader.research_loop.paper import Journal, JournalDamaged
from valedia_trader.venues import webull as _webull
from valedia_trader.venues.webull import OWNER_TEXT_MAX, PRODUCTION_HOST, REPO, judge_live_confirmation, read_owner_file

CHECKLIST = REPO / "config" / "go_live.json"
LIMITS = REPO / "config" / "risk.json"
JOURNAL = REPO / "journal"
LIVE_HOST = PRODUCTION_HOST
PAPER_VENUE = "webull-sandbox"                    # the test-environment venue whose record lock 2 reads
LIVE_VENUE = "webull-production"
CHECKLIST_NUMBERS = ("min_paper_broker_days", "min_reconciled_fills", "min_days_since_a_stopped_run",
                     "fills_checked_by_hand", "min_fills_checked_by_hand")
CHECKLIST_KEYS = CHECKLIST_NUMBERS + ("confirmed_by",)
AT_LEAST_ONE = ("min_paper_broker_days", "min_reconciled_fills")
CHECKLIST_MAX = 100_000
ORDERS_PER_DAY_MIN, ORDERS_PER_DAY_MAX = 1, 200
SECOND_SOURCES = ("alpaca", "finnhub")
SECOND_SOURCE_KEYS = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY")
ORDINARY_STOP_STAGES = frozenset({"session", "ticket"})   # not stopped runs: outside hours or a holiday; a refused approval
# (a stop at `shortfall` is one too, R32, but only with a well-formed `shared` block: see `_ordinary_stop`)
LOCK_NAMES = {1: "the confirmation", 2: "the checklist", 3: "the limits", 4: "the ticket"}


def live_repo() -> Path:
    """The repository live mode exists for: webull.LIVE_REPO, read at each call (the package's own folder; a test
    replaces that one constant in-process, and nothing a caller passes moves it)."""
    return Path(os.path.abspath(os.fspath(_webull.LIVE_REPO)))


@dataclass(frozen=True)
class LiveFiles:
    """What live mode is run with: the environment the command loaded (None: os.environ) and an exchange calendar
    (None: XNYS). Where its locks are read is not an argument: the owner's three files in the repository's config/
    and its journal/ (the paper record), all under live_repo(), shown here as read-only properties."""
    environ: Mapping[str, str] | None = None
    calendar: Any = None

    @property
    def root(self) -> Path:
        return live_repo()

    @property
    def confirmation(self) -> Path:
        return live_repo() / "config" / "webull_live.json"

    @property
    def checklist(self) -> Path:
        return live_repo() / "config" / "go_live.json"

    @property
    def limits(self) -> Path:
        return live_repo() / "config" / "risk.json"

    @property
    def journal(self) -> Path:
        return live_repo() / "journal"

    @property
    def paper_venue(self) -> str:
        return PAPER_VENUE


@dataclass
class LockState:
    locks: list[dict]
    confirmation: dict | None                     # the judged confirmation (caps as floats), when lock 1's file is met
    max_orders_per_day: int | None
    digest: str | None                            # the three files' contents, for a ticket to be approved only unchanged

    @property
    def met(self) -> bool:
        return all(lock["met"] for lock in self.locks)

    def unmet(self) -> str | None:
        """Which locks are unmet and why, in one line; None when all are met."""
        bad = [f"lock {lock['lock']} ({lock['name']}): {lock['reason']}" for lock in self.locks if not lock["met"]]
        return "; ".join(bad) if bad else None


def _lock(n: int, met: bool, reason: str, **figures) -> dict:
    return {"lock": n, "name": LOCK_NAMES[n], "met": bool(met), "reason": reason, "figures": figures}


def _calendar(calendar):
    if calendar is not None:
        return calendar
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS")


def _day(value: Any) -> date | None:
    if type(value) is not str or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def paper_record(journal_dir: str | Path, paper_venue: str, today: date, calendar=None) -> tuple[dict | None, str | None]:
    """The paper record's three figures from the journal (see the module docstring), or (None, why). A journal folder
    that is not there is no record (it is never created here)."""
    try:
        folder = Path(journal_dir)
        if folder.is_symlink() or not folder.is_dir():
            return None, "there is no paper record: the journal folder is not there"
        entries = Journal(folder).read_complete()
    except JournalDamaged as exc:
        n = len(getattr(exc, "skipped", []) or [])
        return None, f"the journal is damaged ({n} line{'s' if n != 1 else ''} cannot be read); a person must look"
    except Exception as exc:
        return None, f"the journal cannot be read ({type(exc).__name__})"
    try:
        cal = _calendar(calendar)
        mine = [e for e in entries if isinstance(e, dict) and e.get("type") == "order_step" and e.get("venue") == paper_venue]
        summaries = [e for e in mine if e.get("event") == "summary"]
        unclean = [e for e in summaries if e.get("status") == "done" and _shortfall_seen(e)]
        faulted = [e for e in summaries if e.get("status") == "done" and _faults_seen(e)]
        done = {e.get("batch") for e in summaries if e.get("status") == "done" and type(e.get("batch")) is str
                and not _shortfall_seen(e) and not _faults_seen(e)}
        days = set()
        for e in summaries:
            d = _day(e.get("day"))
            if (e.get("status") == "done" and not _shortfall_seen(e) and not _faults_seen(e) and d is not None
                    and d <= today and bool(cal.is_session(d.isoformat()))):
                days.add(d)
        clean_fills = [e for e in mine if e.get("event") == "fill" and e.get("batch") in done]
        fills = len(clean_fills)
        shapes = [_fill_shape(e) for e in clean_fills]
        stopped = [e for e in summaries if e.get("status") == "stopped"]
        skipped = [e for e in stopped if _ordinary_stop(e) and e.get("stage") == "shortfall" and _day(e.get("day")) is not None]
        stops = [d for e in stopped if not _ordinary_stop(e) for d in [_day(e.get("day"))] if d is not None]
        undated = sum(1 for e in stopped if e.get("stage") not in ORDINARY_STOP_STAGES and _day(e.get("day")) is None)
    except Exception as exc:
        return None, f"the paper record cannot be worked out ({type(exc).__name__})"
    if undated:
        return None, "a stopped paper run in the journal carries no date; a person must look"
    last = max(stops) if stops else None
    record = {"paper_broker_days": len(days), "reconciled_fills": fills,
              "days_since_a_stopped_run": None if last is None else (today - last).days,
              "last_stopped_run": None if last is None else last.isoformat()}
    if any(shapes):
        for key, shape in (("whole_fills", "whole"), ("remainder_fills", "remainder"), ("sale_fills", "sale")):
            record[key] = shapes.count(shape)
    for key, runs in (("unclean_shared_runs", unclean), ("skipped_shortfall_runs", skipped), ("faulted_runs", faulted)):
        listed = sorted(d.isoformat() for d in (_day(e.get("day")) for e in runs) if d is not None)
        if listed:                                       # said only when there are some: a non-shared record is as it was
            record[key] = listed
    return record, None


def _ordinary_stop(summary: dict) -> bool:
    """A stopped summary that is no stopped run: stage `session` or `ticket`, or stage `shortfall` with a `shared` block
    that is a record (R32). A `shortfall` stop without one is corrupt (only a shared run raises it) and counts as a stop."""
    stage = summary.get("stage")
    return stage in ORDINARY_STOP_STAGES or (stage == "shortfall" and isinstance(summary.get("shared"), dict))


def _faults_seen(summary: dict) -> bool:
    """A summary that says the run had faults (the order step writes `faults` only when there are some). Absent or an
    empty list is none; a `faults` that is not a list (null, text, a record, a number) is read on the safe side, as a
    fault, as R33 reads `shared`."""
    if "faults" not in summary:
        return False
    faults = summary["faults"]
    return not isinstance(faults, list) or bool(faults)


def _fill_shape(fill: dict) -> str | None:
    """`whole`, `remainder` or `sale` for a fill line (units are signed like the order: a sale is below 0; `leg` is
    `remainder` for the market leg of a fractional buy and is absent on a whole-share run), or None when its units
    are not a nonzero finite number."""
    units = fill.get("units")
    if type(units) not in (int, float) or not math.isfinite(units) or units == 0:
        return None
    if units < 0:
        return "sale"
    return "remainder" if fill.get("leg") == "remainder" else "whole"


def _shortfall_seen(summary: dict) -> bool:
    """R33: a summary whose shared block says the last reconcile found a shortfall. A summary with no `shared` block
    is not shared; a `shared` block that is not a record (or whose `shortfalls` is neither empty nor absent) is read on
    the safe side, as a shortfall."""
    shared = summary.get("shared")
    if shared is None:
        return False
    if not isinstance(shared, dict):
        return True
    return bool(shared.get("shortfalls"))


def judge_checklist(data: Any, record: dict | None, record_why: str | None) -> dict:
    """Lock 2 from the checklist file's value and the paper record."""
    if not isinstance(data, dict):
        return _lock(2, False, "config/go_live.json is not a JSON object")
    unknown = sorted(k for k in data if k not in CHECKLIST_KEYS)
    if unknown:
        shown = ", ".join(repr(k[:40]) for k in unknown[:10])
        return _lock(2, False, f"config/go_live.json has keys that are not part of the checklist: {shown}")
    missing = [k for k in CHECKLIST_KEYS if k not in data]
    if missing:
        return _lock(2, False, f"config/go_live.json lacks {', '.join(missing)} (there is no default for any of them)")
    for key in CHECKLIST_NUMBERS:
        v = data[key]
        low = 1 if key in AT_LEAST_ONE else 0
        if type(v) is not int or not low <= v <= CHECKLIST_MAX:
            return _lock(2, False, f"config/go_live.json: {key} must be a whole number from {low} to {CHECKLIST_MAX:,} "
                                   "(not text, not true or false, not a decimal)")
    who = data["confirmed_by"]
    if not (type(who) is str and who.strip() and len(who) <= OWNER_TEXT_MAX and who.isprintable()):
        return _lock(2, False, "config/go_live.json: confirmed_by must be a name")
    owner = {k: data[k] for k in CHECKLIST_NUMBERS}
    if record is None:
        return _lock(2, False, record_why or "the paper record cannot be read", owner=owner)
    short = []
    if record["paper_broker_days"] < owner["min_paper_broker_days"]:
        short.append(f"{record['paper_broker_days']} paper trading days on which the order step ended done, "
                     f"{owner['min_paper_broker_days']} needed ({owner['min_paper_broker_days'] - record['paper_broker_days']} short)")
    if record["reconciled_fills"] < owner["min_reconciled_fills"]:
        short.append(f"{record['reconciled_fills']} fills journaled in paper runs with a clean reconcile, "
                     f"{owner['min_reconciled_fills']} needed ({owner['min_reconciled_fills'] - record['reconciled_fills']} short)")
    since = record["days_since_a_stopped_run"]
    need = owner["min_days_since_a_stopped_run"]
    if since is not None and since < need:
        short.append(f"the last stopped paper run was {since} days ago ({record['last_stopped_run']}); "
                     f"min_days_since_a_stopped_run {need} needs no stopped run within {need} days ({need - since} short)")
    if owner["fills_checked_by_hand"] < owner["min_fills_checked_by_hand"]:
        short.append(f"{owner['fills_checked_by_hand']} fills checked by hand, {owner['min_fills_checked_by_hand']} needed "
                     f"({owner['min_fills_checked_by_hand'] - owner['fills_checked_by_hand']} short)")
    reason = "; ".join(short) if short else "met"
    return _lock(2, not short, reason, owner=owner, record=dict(record))


def judge_limits(data: Any, path: Path, environ: Mapping[str, str]) -> tuple[dict, int | None]:
    """Lock 3 from the limits file's value (and the risk gate's own reading of it) and the environment."""
    if not isinstance(data, dict):
        return _lock(3, False, "config/risk.json is not a JSON object"), None
    cap = data.get("max_orders_per_day")
    if type(cap) is not int or not ORDERS_PER_DAY_MIN <= cap <= ORDERS_PER_DAY_MAX:
        return _lock(3, False, f"config/risk.json must hold max_orders_per_day, a whole number from {ORDERS_PER_DAY_MIN} to "
                               f"{ORDERS_PER_DAY_MAX}, in live mode (the fallback of 40 does not apply)"), None
    try:
        risk_gate.load_limits(path)
    except Exception as exc:
        return _lock(3, False, f"config/risk.json is not a limits file the risk gate accepts ({type(exc).__name__})"), None
    absent = []
    for name in SECOND_SOURCE_KEYS:
        try:
            value = environ.get(name)
        except Exception:
            value = None
        if not (type(value) is str and value.strip()):
            absent.append(name)
    if absent:
        return _lock(3, False, f"the second price sources are not configured: {', '.join(absent)} absent from the "
                               "environment", max_orders_per_day=cap), cap
    return _lock(3, True, "met", max_orders_per_day=cap, second_sources=list(SECOND_SOURCES)), cap


def judge_locks(files: LiveFiles, today: date, capital: Any = None) -> LockState:
    """All four locks on the New York date `today`, from the owner's files and the journal alone (no venue, no
    quote). `capital`, when given, is judged against max_capital as part of lock 1. The files are always the
    repository's own (LiveFiles' properties, under live_repo()): only the environment and the calendar are taken
    from `files`, whatever else the object given says."""
    given_environ, calendar = getattr(files, "environ", None), getattr(files, "calendar", None)
    environ = os.environ if given_environ is None else given_environ
    files = LiveFiles(environ=environ, calendar=calendar)
    raw_conf, why = read_owner_file(files.confirmation, files.root)
    confirmation = None
    if why is not None:
        lock1 = _lock(1, False, f"config/webull_live.json {why}")
    else:
        confirmation, why = judge_live_confirmation(raw_conf, today)
        lock1 = _lock(1, False, f"config/webull_live.json {why}") if confirmation is None else None
    if confirmation is not None:
        figures = {"account_class": confirmation["account_class"], "account_ends": confirmation["account_ends"],
                   "max_order_value": confirmation["max_order_value"], "max_capital": confirmation["max_capital"],
                   "valid_until": confirmation["valid_until"]}
        lock1 = _lock(1, True, "met", **figures)
        if capital is not None:
            c = capital if type(capital) in (int, float) else None
            if c is None or not math.isfinite(c) or c <= 0:
                lock1 = _lock(1, False, "--capital must be a number above 0", **figures)
            elif c > confirmation["max_capital"]:
                lock1 = _lock(1, False, f"--capital {c:,.2f} is above max_capital {confirmation['max_capital']:,.2f} "
                                        f"(by {c - confirmation['max_capital']:,.2f})", capital=float(c), **figures)
            else:
                lock1["figures"]["capital"] = float(c)
    raw_list, why = read_owner_file(files.checklist, files.root)
    if why is not None:
        lock2 = _lock(2, False, f"config/go_live.json {why} (there is no live mode without it)")
    else:
        record, record_why = paper_record(files.journal, files.paper_venue, today, files.calendar)
        lock2 = judge_checklist(raw_list, record, record_why)
    raw_limits, why = read_owner_file(files.limits, files.root)
    cap = None
    if why is not None:
        lock3 = _lock(3, False, f"config/risk.json {why}")
    else:
        lock3, cap = judge_limits(raw_limits, Path(files.limits), environ)
    lock4 = _lock(4, True, "every live batch is proposed in one run and sent only by its ticket number in a second run")
    digest = None
    if lock1["met"] and lock2["met"] and lock3["met"]:
        import hashlib
        digest = hashlib.sha256(json.dumps([raw_conf, raw_list, raw_limits], sort_keys=True, ensure_ascii=True,
                                           allow_nan=False).encode()).hexdigest()
    return LockState([lock1, lock2, lock3, lock4], confirmation if lock1["met"] else None, cap, digest)
