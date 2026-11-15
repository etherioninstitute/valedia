"""The stake ladder and the working stop (docs/specs/2026-11-14-caps-ladder-design.md, D1 to D6 and D11), as plain
functions of what they are given.

This module decides how much money live mode may put to work and when it may send without the owner's ticket
approval. It only reads: it never writes or creates a file, never sends, never touches the network, never reads the
clock or the environment, and never logs what a journal entry holds. The day is always an argument (the New York date
the caller supplies). The order path calls it (a second ticket, T34b); nothing here is wired into it.

* `read_ladder_file` (D1): the owner's `config/ladder.json`, read only through `webull.read_owner_file`.
* `session_facts` (D3): one fact per session day of the live venue, with the drawdown pauses and the owner's resets.
* `position` (D4): mode, stake, clean sessions of how many, paused or not, and every change with its day.
* `working_stop_pct` (D5) and `paper_returns`: the working daily stop from the paper book's exact daily returns.
* `day_stopped` (D6, the reading half) and `fractional_proven` (D11, the reading half).
* `describe`: the lines the owner reads.

Where the design note is silent or can be read two ways, the more careful reading is taken (less money at work, a
person asked sooner); each such place is named "Reading:" in the docstring of the function it belongs to.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, localcontext
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from valedia_trader.execution_and_risk import go_live as _go_live
from valedia_trader.execution_and_risk.go_live import LIVE_VENUE, PAPER_VENUE
from valedia_trader.venues import webull as _webull
from valedia_trader.venues.webull import LIVE_DAYS_AHEAD_MAX, OWNER_TEXT_MAX, read_owner_file

__all__ = ["TICKET_SESSIONS", "UNATTENDED_SESSIONS", "DOUBLING_SESSIONS", "STOP_MULTIPLE", "STOP_WINDOW",
           "STOP_FLOOR_PCT", "MAX_DAY_ORDERS", "MAX_POSITIONS", "FAULT_STAGES", "SKIP_STAGES", "DRIFT_ACTS", "LADDER_KEYS", "LIVE_VENUE", "PAPER_VENUE",
           "LadderRefused", "LadderAbsent", "LadderFile", "read_ladder_file", "SessionFact", "SessionFacts", "session_facts",
           "Position", "position", "WorkingStop", "working_stop_pct", "paper_returns", "day_stopped", "fractional_proven",
           "describe"]

TICKET_SESSIONS = 20                 # step 0: clean sessions in ticket mode before unattended
UNATTENDED_SESSIONS = 40             # step 1: clean sessions unattended at the first stake before the first doubling
DOUBLING_SESSIONS = 60               # after that: clean sessions per doubling, up to the owner's ceiling
STOP_MULTIPLE = 2.5                  # the working stop is this many times the book's daily swing
STOP_WINDOW = 25                     # over the last this many exact daily returns
STOP_FLOOR_PCT = 1.0                 # and never under this (unless the owner's daily halt is lower)
FAULT_STAGES = frozenset({"state", "reconcile", "send", "after", "journal", "baseline", "unexpected"})
SKIP_STAGES = frozenset({"session", "ticket", "lock", "live", "clock", "environment", "targets", "follow", "prices",
                         "marks"})    # D3's ordinary and data stops: a skipped session
DRIFT_ACTS = False                   # D10: a `drift` entry in a summary's faults does not make a fault while False
DRIFT = "drift"
MAX_DAY_ORDERS = 720                 # T34b-1 addendum 2, D-A: more possible orders on one day and a person must look
MAX_POSITIONS = 64                   # D-B: more positions the record allows and a person must look
_NY = ZoneInfo("America/New_York")   # D-F: a usable time falls on its entry's own New York date
LADDER_FILE = "config/ladder.json"
LADDER_KEYS = ("confirmed_by", "first_stake", "unattended", "fractional", "valid_until")
_MISSING = "is missing"              # read_owner_file's own words for a file that is not there


# ------------------------------------------------------------------ 1. the owner's ladder file (D1)
class LadderRefused(Exception):
    """config/ladder.json is there and wrong (or cannot be judged): live mode stops. `reason` is one line."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LadderAbsent(LadderRefused):
    """There is no config/ladder.json: live mode behaves exactly as it does today (tickets, --capital by hand).

    Reading: a subclass of LadderRefused, so a caller that catches only refusals stops rather than going on."""


@dataclass(frozen=True)
class LadderFile:
    confirmed_by: str
    first_stake: float
    unattended: bool
    fractional: bool
    valid_until: str
    digest: str


def _refuse(why: str) -> LadderRefused:
    return LadderRefused(f"{LADDER_FILE} {why}")


def read_ladder_file(today: date, max_capital: Any, root: str | Path | None = None) -> LadderFile:
    """The owner's ladder file (D1), judged on the New York date `today` against the confirmation's `max_capital`.

    Read only through `webull.read_owner_file` (its owner, permission, link, folder and JSON rules), from
    `root`/config/ladder.json, `root` being the live repository (`webull.LIVE_REPO`, read now) when None. Raises
    LadderAbsent when the file is not there and LadderRefused, in one line naming the file and the fault, for anything
    else that is wrong. Exactly the five keys; `confirmed_by` a name (the confirmation's rule); `first_stake` an int or
    a float (never a boolean), finite, above 0 and at most both the confirmation's money limit and `max_capital`;
    `unattended` and `fractional` real JSON booleans; `valid_until` YYYY-MM-DD, not before `today` and at most
    LIVE_DAYS_AHEAD_MAX days after it (the confirmation's rule).

    Reading: `digest` is the sha256 of the value as read, written as canonical JSON (sorted keys, ASCII, no NaN), the
    same form the go-live locks digest their files in: read_owner_file returns the value, not the bytes, and the file
    is read only through it (a second, raw read could see other bytes than the ones judged). So an edit that changes
    only whitespace (or the order of the keys) does not change the digest; any change of a value does."""
    if type(today) is not date:
        raise _refuse("cannot be judged: the date given is not a date")
    cap = _webull._money(max_capital)
    if cap is None:
        raise _refuse("cannot be judged: the max_capital given is not a number above 0")
    top = _go_live.live_repo() if root is None else Path(root)
    data, why = read_owner_file(top / "config" / "ladder.json", top)
    if why == _MISSING:
        raise LadderAbsent(f"{LADDER_FILE} {why}: live mode works as it does today (tickets, --capital by hand)")
    if why is not None:
        raise _refuse(f"{why}; the owner corrects it")
    if not isinstance(data, dict):
        raise _refuse("is not a JSON object; the owner writes it as one object with the five keys")
    unknown = [k for k in data if k not in LADDER_KEYS]
    if unknown:
        raise _refuse(f"has keys that are not part of a ladder file: {_webull._key_list(unknown)}; the owner removes them")
    missing = [k for k in LADDER_KEYS if k not in data]
    if missing:
        raise _refuse(f"lacks {', '.join(missing)} (there is no default for any of them); the owner adds them")
    if not _webull._owner_text(data["confirmed_by"]):
        raise _refuse(f"confirmed_by must be a name (plain text, at most {OWNER_TEXT_MAX} characters)")
    stake = _webull._money(data["first_stake"])
    if stake is None:
        raise _refuse(f"first_stake must be a number above 0 and at most {_webull.LIVE_MONEY_MAX:,.0f} "
                      "(not text, not true or false)")
    if stake > cap:
        raise _refuse(f"first_stake {stake:,.2f} is above the confirmation's max_capital {cap:,.2f}; the owner lowers it")
    for key in ("unattended", "fractional"):
        if type(data[key]) is not bool:
            raise _refuse(f"{key} must be true or false (not 0 or 1, not text)")
    until = data["valid_until"]
    day = None
    if type(until) is str and _webull._ISO_DAY.fullmatch(until):
        try:
            day = date.fromisoformat(until)
        except ValueError:
            day = None
    if day is None:
        raise _refuse("valid_until must be a date written YYYY-MM-DD")
    if day < today:
        raise _refuse(f"lapsed on {day.isoformat()} (today is {today.isoformat()}, New York date): the owner renews it")
    if day > today + timedelta(days=LIVE_DAYS_AHEAD_MAX):
        raise _refuse(f"valid_until is more than {LIVE_DAYS_AHEAD_MAX} days ahead of today ({today.isoformat()})")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    return LadderFile(confirmed_by=data["confirmed_by"], first_stake=stake, unattended=data["unattended"],
                      fractional=data["fractional"], valid_until=day.isoformat(), digest=digest)


# ------------------------------------------------------------------ 2. session facts (D3)
def _strict_day(value: Any) -> date | None:
    """A journal entry's `day`, usable only as the ten characters YYYY-MM-DD (ASCII digits) of a real date (addendum
    3, F6): a week date, a basic-format date, a datetime or anything else Python's parser would also take is None.
    Every function here reads a day this one way."""
    if type(value) is not str or not _webull._ISO_DAY.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


SESSION_KINDS = ("clean", "fault", "skipped")
EVENT_KINDS = ("pause", "reset")
_FAULT_WORDS = re.compile(r"(reconcile|cap|drift|attention:[a-z0-9_-]{1,40})")


@dataclass(frozen=True)
class SessionFact:
    """One session day of the live venue (`kind` clean, fault or skipped), or one drawdown pause or owner's reset
    (`kind` pause or reset), with its reasons in words. `ceiling` (A4): the smallest usable ceiling among the day's
    summaries (the owner's max_capital in force when each run read his file), or None; always None for an event.
    `at` (T34b-1 R1): for a pause or a reset its usable time, else None; None on every session fact. `fault_times`
    (T34b-1 addendum 1, A2): for a fault session one entry per faulty summary of the day, its usable time or None
    (instants ascending, None last); empty on every other fact. A usable time is an aware datetime on the fact's own
    New York date (`_instant`)."""
    day: date
    kind: str
    reasons: tuple[str, ...]
    day_stop: bool = False
    ceiling: float | None = None
    at: datetime | None = None
    fault_times: tuple[datetime | None, ...] = ()


class SessionFacts(list):
    """The facts in day order (a list), with `unreadable`: how many entries were skipped as unusable."""

    def __init__(self, items=(), unreadable: int = 0) -> None:
        super().__init__(items)
        self.unreadable = unreadable


def _judge_summary(e: dict) -> tuple[bool, list[str], list[str]]:
    """(done, why it is a fault, other notes) for one order_step summary; a fault when the second is not empty."""
    status, stage = e.get("status"), e.get("stage")
    done, faulty, notes = status == "done", [], []
    if status == "stopped":
        if type(stage) is str and stage in FAULT_STAGES:
            faulty.append(f"a run stopped at the {stage} stage")
        elif type(stage) is str and stage in SKIP_STAGES:
            notes.append(f"a run stopped at the {stage} stage (an ordinary or data stop)")
        else:
            faulty.append("a run stopped at a stage the ladder does not know (taken as a fault)")
    elif not done:
        faulty.append("a run ended with a status the ladder does not know (taken as a fault)")
    faults = e.get("faults")
    if "faults" in e and not isinstance(faults, list):                    # null included (addendum 3, F4)
        faulty.append("a run's faults list cannot be read (taken as a fault)")
    elif faults:
        for kind in faults:
            if kind == DRIFT and not DRIFT_ACTS:
                notes.append("drift from paper was recorded: measured, not acted on")
                continue
            shown = kind if type(kind) is str and _FAULT_WORDS.fullmatch(kind) else "of a kind the ladder does not know"
            faulty.append(f"a run recorded a fault: {shown}")
    return done, faulty, notes


def _aware(x: Any) -> bool:
    """A datetime (exactly) that carries a UTC offset."""
    if type(x) is not datetime:
        return False
    try:
        return x.utcoffset() is not None
    except Exception:
        return False


def _instant(ts: Any, day: date) -> datetime | None:
    """A journal entry's `timestamp` as an instant (T34b-1 R1): a `str` that `datetime.fromisoformat` reads as a
    datetime WITH a UTC offset (as `Journal.record` stamps every record) and that falls on the entry's own `day` read
    in New York (addendum 2, D-F); anything else is an unknown time (None). Instants are compared as instants (aware
    datetimes), never as text."""
    if type(ts) is not str:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if not _aware(dt) or dt.astimezone(_NY).date() != day:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return dt


def _is_session(cal, d: date) -> bool | None:
    try:
        return bool(cal.is_session(d.isoformat()))
    except Exception:
        return None


def session_facts(entries, venue: str, calendar=None) -> SessionFacts:
    """The live venue's session facts from already-parsed journal entries in file order (D3), in day order.

    A session is a New York date that is an exchange session (`calendar.is_session`, the method paper_record uses;
    None: XNYS) on which `venue` has at least one `order_step` summary. Its kind: fault when any summary stopped at a
    stage in FAULT_STAGES or carries a non-empty `faults` list (a `drift` entry acts only when DRIFT_ACTS); else clean
    when one ended `done`; else skipped. `day_stop` is True when any summary of the day carries a `day_stop` key.
    `ceiling` (addendum 2, A4) is the smallest USABLE `ceiling` among the day's summaries: an int or a float (never a
    boolean, never text), finite and above 0; None when no summary of the day has a usable one. An unusable ceiling
    makes no entry unreadable: it is simply not a ceiling, and a day without one can never raise the stake.
    Entries of type `drawdown_pause` and `latch_reset` (with a `who` that is a name) for `venue` are their own facts
    (kind pause and reset), after the session of their day, listed in the order of their `timestamp` read as an
    instant (`_instant`; T34b-1 R1), a reset before a pause at the same instant, a reset of unknown time first and a
    pause of unknown time last. That listing claims no order: where the instants do not tell it (equal, or either
    unknown) the walk takes every order (addendum 2, D-A). `at` and `fault_times` are described on
    SessionFact: a fault session carries one time per faulty summary (addendum 1, A2). A pause or a reset is
    an event, not a session (addendum 1, A2): it counts on any calendar day it carries (a weekend, a holiday, a day
    outside the calendar's window; the calendar is not asked) and so takes effect before the next session after it.

    An entry that is not a mapping, or is one of the three kinds for `venue` but lacks a usable day (strictly
    YYYY-MM-DD, `_strict_day`), a summary whose day is not a session (or that the calendar cannot answer for), and a
    reset without a name are skipped and counted in `unreadable`; none raises or counts as clean. A pause or a reset
    whose `venue` is missing or not a name (addendum 3, F5; a string that is empty after stripping whitespace is no
    name, T34b-1 R4) is counted too: the pause still applies to `venue` (the careful side), the reset lifts nothing.
    A faults key holding anything but a list, null included, is a fault (F4). Entries of other venues and types are
    not ours: ignored.

    Readings (the careful side): a stop at a stage in neither D3 list, a status other than done or stopped, and a
    `faults` field that is not a list are faults. A time on another New York date than the entry's `day` is unknown
    (D-F). D4's "between the pause and the reset no session counts" is applied in `position`."""
    cal = _go_live._calendar(calendar)
    days: dict[date, list[dict]] = {}
    events: dict[date, list[tuple[tuple, SessionFact]]] = {}
    known: dict[date, bool | None] = {}
    unreadable = 0
    for e in entries:
        if not isinstance(e, dict):
            unreadable += 1
            continue
        kind = e.get("type")
        if kind == "order_step":
            if e.get("venue") != venue or e.get("event") != "summary":
                continue
        elif kind in ("drawdown_pause", "latch_reset"):
            named = type(e.get("venue")) is str and e["venue"].strip() != ""
            if named and e["venue"] != venue:
                continue
        else:
            continue
        d = _strict_day(e.get("day"))
        if d is None:
            unreadable += 1
            continue
        if kind in ("drawdown_pause", "latch_reset") and not named:     # addendum 3, F5: counted either way;
            unreadable += 1                                              # a pause still pauses, a reset lifts nothing
            if kind == "latch_reset":
                continue
        if kind == "order_step":                     # the session-day rule is for summaries only (addendum 1, A2)
            if d not in known:
                known[d] = _is_session(cal, d)
            if not known[d]:
                unreadable += 1
                continue
            days.setdefault(d, []).append(e)
            continue
        ts = _instant(e.get("timestamp"), d)
        if kind == "drawdown_pause":
            fact = SessionFact(d, "pause", ("the 25% drawdown pause began: no clean session counts until the owner's "
                                            "reset",), at=ts)
            key = (2, 0) if ts is None else (1, ts, 1)          # unknown: after every other event of its day
        else:
            who = e.get("who")
            if not _webull._owner_text(who):
                unreadable += 1
                continue
            fact = SessionFact(d, "reset", (f"the owner's reset of the drawdown pause, by {who}",), at=ts)
            key = (0, 0) if ts is None else (1, ts, 0)          # unknown: before every other; first at one instant
        events.setdefault(d, []).append((key + fact.reasons, fact))
    out = []
    for d in sorted(set(days) | set(events)):
        if d in days:
            judged = [_judge_summary(e) for e in days[d]]
            stopped = any("day_stop" in e for e in days[d])
            faulty = {r for _, rs, _ in judged for r in rs}
            notes = {r for _, _, rs in judged for r in rs}
            times: tuple[datetime | None, ...] = ()
            if faulty:
                kind, reasons = "fault", faulty
                runs = [_instant(e.get("timestamp"), d) for e, (_, rs, _) in zip(days[d], judged) if rs]
                times = tuple(sorted(t for t in runs if t is not None)) + tuple(t for t in runs if t is None)
            elif any(done for done, _, _ in judged):
                kind, reasons = "clean", {"a run ended done"} | notes
            else:
                kind, reasons = "skipped", notes
            usable = [c for c in (_number(e.get("ceiling")) for e in days[d]) if c is not None and c > 0]
            out.append(SessionFact(d, kind, tuple(sorted(reasons)), stopped, min(usable) if usable else None,
                                   fault_times=times))
        out.extend(fact for _, fact in sorted(events.get(d, []), key=lambda item: item[0]))
    return SessionFacts(out, unreadable)


# ------------------------------------------------------------------ 6. is the day stopped (D6, the reading half)
def _number(x: Any) -> float | None:
    if type(x) not in (int, float):
        return None
    try:
        v = float(x)
    except (OverflowError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _stop_words(v: Any) -> str:
    if isinstance(v, dict):
        pct, limit, wall = _number(v.get("pct")), _number(v.get("limit")), v.get("wall")
        if pct is not None and limit is not None and type(wall) is bool:
            what = "the owner's daily halt" if wall else "the working stop"
            return (f"the day's loss reached {pct:.2f}%, at or above {what} of {limit:.2f}%: no new buying for the rest "
                    "of the New York day (reducing orders still pass)")
    return ("a stop for the day is recorded but cannot be read, so it counts as a stop: no new buying for the rest of "
            "the New York day (reducing orders still pass)")


def day_stopped(entries, venue: str, day: date) -> str | None:
    """Why `venue` may not buy more on the New York date `day` (D6), in words, or None: the first `order_step`
    summary of that venue and day, in file order, that carries a `day_stop` key.

    Reading (the careful side): a `day_stop` that is not a mapping holding finite `pct` and `limit` and a boolean
    `wall` (None included) counts as a stop; only a summary without the key is no stop. Any summary counts, a stopped
    run's too; its day is read as `session_facts` reads it (strictly YYYY-MM-DD)."""
    if type(day) is not date:
        raise ValueError("the day given is not a date")
    for e in entries:
        if (isinstance(e, dict) and e.get("type") == "order_step" and e.get("event") == "summary"
                and e.get("venue") == venue and "day_stop" in e and _strict_day(e.get("day")) == day):
            return _stop_words(e["day_stop"])
    return None


# ------------------------------------------------------------------ 7. is a fractional fill proven (D11, the reading half)
def _fractional(units: Any) -> bool:
    u = _number(units)
    return u is not None and not u.is_integer()


def fractional_proven(entries, paper_venue: str) -> int:
    """How many journaled fills of a non-whole quantity the paper venue holds in done batches (D11): fills and done
    batches matched as paper_record matches them (an `order_step` fill whose batch is the batch of a summary of the
    same venue with status done). A quantity that is not a finite number (an int or a float, never a boolean) counts
    as nothing. Addendum 3: the batch's summary must be CLEAN by the judgement `session_facts` uses for one summary
    (done, no fault; a `drift` entry alone does not disqualify while DRIFT_ACTS is False) (F3), and the fill and the
    summary must each carry a usable day, read as `session_facts` reads it (F6). T34b-1 R2: a batch counts only when
    it has at least one summary and EVERY `order_step` summary of that venue carrying that batch is clean and carries
    a usable day (one faulty, or one whose day cannot be read, and the batch proves nothing)."""
    mine = [e for e in entries if isinstance(e, dict) and e.get("type") == "order_step" and e.get("venue") == paper_venue]
    verdict: dict[str, bool] = {}
    for e in mine:
        if e.get("event") == "summary" and type(e.get("batch")) is str:
            finished, faulty, _ = _judge_summary(e)
            good = finished and not faulty and _strict_day(e.get("day")) is not None
            verdict[e["batch"]] = verdict.get(e["batch"], True) and good
    return sum(1 for e in mine if e.get("event") == "fill" and type(e.get("batch")) is str
               and verdict.get(e["batch"], False) and _strict_day(e.get("day")) is not None
               and _fractional(e.get("units")))


# ------------------------------------------------------------------ 3. the position (D4)
@dataclass(frozen=True)
class Position:
    mode: str                                        # "ticket" or "unattended"
    stake: float
    clean: int
    needed: int
    paused: bool                                     # a drawdown pause with no reset yet
    changes: tuple[tuple[date | None, str], ...]
    earned: float                                    # A4: the stake the history earned; `stake` is min(earned, max_capital)
    ceiling: float                                   # T34b-1 R5: max_capital, the owner's ceiling at this read
    possible: int = 1                                # addendum 2, D-C: how many positions the record allows


_PAUSE_WORDS = "the 25% drawdown pause: no clean session counts until the owner's reset (a fault still halves)"
_UNTOLD_START = "the times do not tell the order of the events on "
_UNTOLD_WORDS = (_UNTOLD_START + "{days}: {count} possible; what is shown is the lowest stake, ticket mode where any "
                 "of them has it and the pause where any of them has it")
_DAYS_NAMED = 5


def _untold_line(days: list[date], n: int) -> str:
    """D-D's one undated line, in fixed words: the days (up to five named, then "and N more") and the positions."""
    named = ", ".join(d.isoformat() for d in days[:_DAYS_NAMED])
    if len(days) > _DAYS_NAMED:
        named += f" and {len(days) - _DAYS_NAMED} more"
    return _UNTOLD_WORDS.format(days=named, count=f"{n} position{' is' if n == 1 else 's are'}")


def _amount(x: float) -> str:
    """An amount in words: cents from 1 up, six significant digits under 1 (0.075 stays 0.075), rounded half up on
    the number's shortest decimal form (2.675 is 2.68), never half even. Total on finite floats (T34b-1 R3): worked
    with 400 significant digits, more than the 311 the largest float needs written to the cent."""
    d = Decimal(repr(float(x)))
    if d == 0:
        return "0.00"
    with localcontext() as ctx:
        ctx.prec = 400
        if abs(d) >= 1:
            return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
        text = format(d.quantize(Decimal(1).scaleb(d.adjusted() - 5), rounding=ROUND_HALF_UP), "f").rstrip("0")
    whole, _, cents = text.partition(".")
    return f"{whole}.{cents.ljust(2, '0')}"


def _positive(x: Any, name: str) -> float:
    v = _number(x)
    if v is None or v <= 0:
        raise ValueError(f"{name} must be a finite number above 0 (not true or false, not text)")
    return v


def _bounded(x: Any, name: str) -> float:
    """`_positive`, and at most `webull.LIVE_MONEY_MAX`, the cap the owner's ladder file already uses for the first
    stake (T34b-1 R3: an absurd number is a one-line refusal, never a Decimal error further on)."""
    v = _positive(x, name)
    if v > _webull.LIVE_MONEY_MAX:
        raise ValueError(f"{name} is above {_webull.LIVE_MONEY_MAX:,.0f}, the most the owner's ladder file may give")
    return v


def position(facts, first_stake: Any, max_capital: Any) -> Position:
    """The ladder's position (D4) from the session facts in day order, the owner's first stake and the ceiling.

    Starts at ticket mode, the first stake, 0 clean of 20. A clean session adds 1; at `needed`, ticket mode becomes
    unattended at the same stake (needs 40), and unattended doubles the stake up to the ceiling (needs 60); the count
    restarts. A fault halves the stake (no floor) and restarts the count; the mode and `needed` stay. A skipped
    session changes nothing. A pause followed by the owner's reset steps one step down and restarts the count:
    unattended above the first stake goes to max(first stake, stake / 2) (addendum 1, A1: never below the first
    stake; needs 60 when the new stake is above the first stake, else 40); unattended at or below it goes back to
    ticket mode at the same stake (needs 20); ticket mode stays.

    The ceiling (addendum 2, A4) is a fact of each day: the walk keeps the earned stake E, starting at the first stake.
    On every session fact, of every kind and whether it counts or not, E first becomes min(E, that day's recorded
    ceiling) when the day has one; then the day's rule acts on E, and a doubling makes E min(2 * E, that day's
    ceiling); on a day with no usable ceiling a doubling leaves E where it is (the count still restarts, and the
    change says why). `max_capital`, the ceiling at the read, is never used inside the walk: the returned `stake` is
    min(E, max_capital), shown as a last change with no day when it bites, and mode, clean, needed, paused and the
    dated changes come from the walk alone. So a lower ceiling at the read never gives a larger stake, and a raised
    one raises nothing until a later day records it.

    The pause rule is applied here (addendum 3, F1): between a pause and its reset no CLEAN session counts (none on a
    day that starts paused or holds a pause: the pause stands from the start of its day, and a reset applies after
    its day's session), but a FAULT session halves the earned stake and zeroes the count on any day, paused or not.
    One day for ONE given order (T34b-1 R1): (1) the day's ceiling clamp; (2) a clean or skipped session, before the
    day's pauses and resets; (3) a fault session after the first p of them (0 <= p <= k); then the events in the order
    given. The fault halves ONCE.

    The day's POSSIBLE ORDERS (addendum 2, D-A): every arrangement of its pauses and resets that their known
    DIFFERENT instants allow (equal instants, or an unknown one, may go either way; `at` on another New York date than
    the fact's day is unknown, D-F), each with every place of the fault that A2 allows in that arrangement (a place is
    possible for one faulty run's time when no event before it has a known instant later than that time and none
    after it one earlier; an unknown time allows every place; the union over the day's faulty runs, `fault_times`).
    More than MAX_DAY_ORDERS: ValueError. The walk (D-B) carries the SET of distinct states (mode, earned stake, clean,
    needed, paused) the record allows, from one: each day every member is walked through every possible order of the
    day and the new set is the distinct ends; more than MAX_POSITIONS: ValueError. Where every day has one possible
    order the set has one member and the walk is today's for that order.

    The position reported (D-C), at the end of each day and at the read, part by part over the set: the SMALLEST
    earned stake (`stake` is min of it and max_capital); ticket mode when ANY member is in ticket mode; paused when ANY
    member is; `clean` and `needed` of the member, among those in the reported mode, with the most sessions still to
    go (`needed - clean`), among equals the larger `needed`, then the smaller `clean`; `possible`, how many members.
    So for any concrete order of the record's events (D-E) the concrete position is a member on every day, and the
    reported stake is never above it, the report is ticket whenever it is, and paused whenever it is.

    The lines (D-D): a day "whose order could not be told" is one on which, for some member, the possible orders do
    not all give the same end and the same lines. Up to the first such day the set has one member and its lines are
    the changes, as today. From that day on no dated line is listed: the changes are the dated lines up to the day
    before it, then one undated line in fixed words naming every such day (up to five, then "and N more"), how many
    positions are possible and what is shown. Reading: that line is written from the first such day even if the set
    has one member again (or never had more: orders that end alike but tell their lines in another order), since
    the dated lines after it would claim an order the record does not tell.

    Stakes are halved and doubled exactly in binary floating point (dividing or multiplying by 2 loses nothing away
    from underflow), so n halvings and n doublings return exactly to the start.

    Refusals (ValueError, one line): `first_stake` or `max_capital` not a finite number above 0 or above
    `webull.LIVE_MONEY_MAX` (T34b-1 R3); a fact that is not a SessionFact, out of day order, with an unusable ceiling,
    or with an `at` that is neither None nor an aware datetime, or with an `at` on a session fact (clean, fault or
    skipped; D-F), or a `fault_times` that is not a tuple of None or aware datetimes, or is not empty on a fact that is
    not a fault; a day whose pauses and resets with known instants are not in non-decreasing order as given; a day
    with more than one session fact (session_facts gives one a day); a day with more than MAX_DAY_ORDERS possible
    orders; more than MAX_POSITIONS positions.

    Readings (the careful side): a reset with no pause before it changes nothing; a second pause before the reset
    changes nothing (one reset, one step down); the step down compares with the owner's `first_stake` as written.
    Two pauses (or two resets) with the same time, or both unknown, are interchangeable: their orders are counted
    once towards MAX_DAY_ORDERS (an order is an arrangement of distinct (kind, time) events, with a place)."""
    final, _ = _walk_all(facts, first_stake, max_capital)
    return final


def _walk(facts, first_stake: Any, max_capital: Any) -> list[tuple[date, Position]]:
    """The position at the end of each day that has a fact, in day order (the last is `position`'s answer)."""
    return _walk_all(facts, first_stake, max_capital)[1]


def _possible(events: list, fault_at: datetime | None, place: int) -> bool:
    """R1 (4): may a fault run at `fault_at` come after the first `place` of the day's pauses and resets?"""
    if fault_at is None:
        return True
    return (all(f.at is None or f.at <= fault_at for f in events[:place])
            and all(f.at is None or f.at >= fault_at for f in events[place:]))


def _places(events: list, fault_times: tuple) -> list[int]:
    """Addendum 1, A2: the union over the day's faulty runs of the places each one's time allows; no time recorded
    allows every place."""
    every = range(len(events) + 1)
    if not fault_times:
        return list(every)
    return [p for p in every if any(_possible(events, t, p) for t in fault_times)]


def _arrangements(events: list) -> list[list]:
    """D-A: every order of the day's pauses and resets that their known different instants allow, each listed once
    (events of the same kind and the same time, or both of unknown time, are interchangeable). Stops as soon as there
    are more than MAX_DAY_ORDERS (the caller refuses). Worked without recursion, so a long day cannot overflow."""
    out: list[list] = []
    stack: list[tuple[tuple, tuple]] = [((), tuple(events))]
    while stack and len(out) <= MAX_DAY_ORDERS:
        seq, rest = stack.pop()
        if not rest:
            out.append(list(seq))
            continue
        low = min((f.at for f in rest if f.at is not None), default=None)
        seen: set = set()
        grown = []
        for i, f in enumerate(rest):
            if (f.at is not None and f.at != low) or (f.kind, f.at) in seen:
                continue                     # an event with a later known instant waits for the earlier ones
            seen.add((f.kind, f.at))
            grown.append((seq + (f,), rest[:i] + rest[i + 1:]))
        stack.extend(reversed(grown))
    return out


def _orders(day: date, session: SessionFact | None, events: list) -> list[tuple[list, int]]:
    """D-A: the day's possible orders, (arrangement, place of the fault); a ValueError past MAX_DAY_ORDERS."""
    out: list[tuple[list, int]] = []
    for seq in _arrangements(events):
        places = _places(seq, session.fault_times) if session is not None and session.kind == "fault" and seq else [0]
        out.extend((seq, p) for p in places)
        if len(out) > MAX_DAY_ORDERS:
            raise ValueError(f"{day.isoformat()} has more than {MAX_DAY_ORDERS} possible orders of its fault, pauses and "
                             "resets; a person must look at that day")
    return out


def _report(members: list[tuple]) -> tuple:
    """D-C: the position reported, part by part over the set: (mode, the smallest earned stake, clean, needed of the
    slowest member in that mode, paused when any is)."""
    mode = "ticket" if any(m[0] == "ticket" for m in members) else "unattended"
    slow = max((m for m in members if m[0] == mode), key=lambda m: (m[3] - m[2], m[3], -m[2]))
    return mode, min(m[1] for m in members), slow[2], slow[3], any(m[4] for m in members)


def _one_day(state: tuple, day: date, first: float, session: SessionFact | None, events: list,
             place: int) -> tuple[tuple, list[tuple[date | None, str]]]:
    """One day's walk from `state` = (mode, earned stake, clean, needed, paused): the clamp, then a clean or skipped
    session before the events, or a fault after the first `place` events. Returns the end state and the day's lines."""
    mode, stake, clean, needed, paused = state
    lines: list[tuple[date | None, str]] = []
    counts = not paused and not any(f.kind == "pause" for f in events)
    if session is not None and session.ceiling is not None and stake > session.ceiling:
        lines.append((day, f"the owner's ceiling that day ({_amount(session.ceiling)}) lowers the stake from "
                           f"{_amount(stake)} to {_amount(session.ceiling)}"))
        stake = session.ceiling

    def event(f: SessionFact) -> None:
        nonlocal mode, stake, clean, needed, paused
        if f.kind == "pause":
            if not paused:
                paused = True
                lines.append((day, _PAUSE_WORDS))
            return
        if not paused:
            return
        paused, clean = False, 0
        if mode == "unattended" and stake > first:
            before, stake = stake, max(first, stake / 2)
            needed = DOUBLING_SESSIONS if stake > first else UNATTENDED_SESSIONS
            lines.append((day, f"the owner's reset after the pause: one step down, the stake goes from "
                               f"{_amount(before)} to {_amount(stake)} (half, never below the first stake), "
                               f"unattended, {needed} clean sessions to the next doubling"))
        elif mode == "unattended":
            mode, needed = "ticket", TICKET_SESSIONS
            lines.append((day, f"the owner's reset after the pause: one step down, back to ticket mode at "
                               f"{_amount(stake)}"))
        else:
            lines.append((day, "the owner's reset after the pause: ticket mode stays, the count restarts"))

    if session is not None and session.kind == "fault":     # addendum 3, F1: on any day, paused or not
        for f in events[:place]:
            event(f)
        before, stake, clean = stake, stake / 2, 0
        lines.append((day, f"a fault session ({session.reasons[0] if session.reasons else 'a fault'}): the stake "
                           f"halves from {_amount(before)} to {_amount(stake)} and the count restarts"))
        for f in events[place:]:
            event(f)
        return (mode, stake, clean, needed, paused), lines
    if session is not None and session.kind == "clean" and counts:
        clean += 1
        if clean >= needed:
            clean = 0
            if mode == "ticket":
                mode, needed = "unattended", UNATTENDED_SESSIONS
                lines.append((day, f"{TICKET_SESSIONS} clean sessions: unattended at the same stake {_amount(stake)}"))
            elif session.ceiling is None:
                done, needed = needed, DOUBLING_SESSIONS
                lines.append((day, f"{done} clean sessions, but no ceiling was recorded that day, so the stake "
                                   f"stays at {_amount(stake)} (no rise without the owner's ceiling); the count "
                                   "restarts"))
            else:
                done, before = needed, stake
                stake, needed = min(2 * stake, session.ceiling), DOUBLING_SESSIONS
                words = (f"the stake doubles from {_amount(before)} to {_amount(stake)}" if stake == 2 * before else
                         f"the stake rises from {_amount(before)} to that day's ceiling {_amount(stake)}"
                         if stake > before else f"the stake stays at that day's ceiling {_amount(stake)}")
                lines.append((day, f"{done} clean sessions: {words}"))
    for f in events:
        event(f)
    return (mode, stake, clean, needed, paused), lines


def _walk_all(facts, first_stake: Any, max_capital: Any) -> tuple[Position, list[tuple[date, Position]]]:
    final, walk, _ = _walk_sets(facts, first_stake, max_capital)
    return final, walk


def _on_day(t: datetime | None, day: date) -> datetime | None:
    """D-F: a time that is not on `day` in New York orders nothing (an unknown time)."""
    try:
        return t if t is not None and t.astimezone(_NY).date() == day else None
    except (OverflowError, ValueError):
        return None


def _walk_sets(facts, first_stake: Any, max_capital: Any) -> tuple[Position, list[tuple[date, Position]],
                                                                    list[tuple[date, tuple]]]:
    """The walk (D-B): the reported position at the read, at the end of each day, and the set of each day."""
    first = _bounded(first_stake, "first_stake")
    cap = _bounded(max_capital, "max_capital")
    facts = list(facts)
    for i, f in enumerate(facts):
        if not isinstance(f, SessionFact) or type(f.day) is not date or f.kind not in SESSION_KINDS + EVENT_KINDS:
            raise ValueError("the position is worked out from session facts only (session_facts gives them)")
        if f.ceiling is not None and (_number(f.ceiling) is None or f.ceiling <= 0):
            raise ValueError("a session fact's ceiling must be a finite number above 0, or None")
        if f.at is not None and not _aware(f.at):
            raise ValueError("a session fact's time (at) must be a datetime with a UTC offset, or None")
        if f.at is not None and f.kind in SESSION_KINDS:
            raise ValueError("a session fact (clean, fault or skipped) carries no time (at); only a pause or a reset does")
        if type(f.fault_times) is not tuple or any(t is not None and not _aware(t) for t in f.fault_times):
            raise ValueError("a session fact's fault_times must be a tuple of datetimes with a UTC offset or None")
        if f.fault_times and f.kind != "fault":
            raise ValueError("only a fault session carries fault_times")
        if i and f.day < facts[i - 1].day:
            raise ValueError("the session facts are not in day order")
    members: list[tuple] = [("ticket", first, 0, TICKET_SESSIONS, False)]    # the stake in each: the earned E
    changes: list[tuple[date | None, str]] = []                             # the dated lines, while told
    untold: list[date] = []                                                 # the days whose order was not told

    def at() -> Position:
        mode, stake, clean, needed, paused = _report(members)
        held = min(stake, cap)
        now = list(changes)
        if untold:
            now.append((None, _untold_line(untold, len(members))))
        if held < stake:
            earned = (f"the {_amount(stake)} the record earned" if len(members) == 1 else
                      f"{_amount(stake)}, the lowest stake of the positions the record allows")
            now.append((None, f"the owner's ceiling at this read (max_capital {_amount(cap)}) holds the stake at "
                              f"{_amount(held)}, below {earned}"))
        return Position(mode, held, clean, needed, paused, tuple(now), stake, cap, len(members))

    walk: list[tuple[date, Position]] = []
    sets: list[tuple[date, tuple]] = []
    i = 0
    while i < len(facts):
        day = facts[i].day
        j = i
        while j < len(facts) and facts[j].day == day:
            j += 1
        sessions = [f for f in facts[i:j] if f.kind in SESSION_KINDS]
        events = [f if _on_day(f.at, day) == f.at else replace(f, at=None) for f in facts[i:j] if f.kind in EVENT_KINDS]
        if len(sessions) > 1:
            raise ValueError(f"the session facts hold more than one session for {day.isoformat()} (session_facts gives "
                             "one a day)")
        known = [f.at for f in events if f.at is not None]
        if any(later < earlier for earlier, later in zip(known, known[1:])):
            raise ValueError(f"the pauses and resets of {day.isoformat()} are not in the order of their times")
        session = sessions[0] if sessions else None
        if session is not None and session.fault_times:
            session = replace(session, fault_times=tuple(_on_day(t, day) for t in session.fault_times))
        orders = _orders(day, session, events)
        ends: dict[tuple, None] = {}
        told: list | None = None
        for m in members:
            results = {}
            for seq, p in orders:
                end, lines = _one_day(m, day, first, session, seq, p)
                results.setdefault((end, tuple(lines)), None)
            if len(results) > 1:                      # this day's order is not told: no dated line from here on
                if not untold or untold[-1] != day:
                    untold.append(day)
            elif not untold:                          # one member (D-D): its lines, as today
                told = list(next(iter(results))[1])
            for end, _ in results:
                ends.setdefault(end, None)
            if len(ends) > MAX_POSITIONS:
                raise ValueError(f"the record allows more than {MAX_POSITIONS} positions by {day.isoformat()} (the times "
                                 "do not tell the order of too many events); a person must look")
        if not untold and told is not None:
            changes.extend(told)
        members = list(ends)
        sets.append((day, tuple(members)))
        walk.append((day, at()))
        i = j
    return at(), walk, sets


# ------------------------------------------------------------------ 4. the working stop (D5)
@dataclass(frozen=True)
class WorkingStop:
    pct: float                                       # the day's loss, in percent, at which the day stops
    basis: str                                       # in words: "25 returns ending D" or "fewer than 25 returns: the floor"
    n: int                                           # how many exact returns it rests on (at most STOP_WINDOW)


def working_stop_pct(returns, wall_pct: Any) -> WorkingStop:
    """The working daily stop (D5): min(wall, max(STOP_FLOOR_PCT, STOP_MULTIPLE * s)), `s` the sample standard
    deviation (n - 1), in percent, of the last STOP_WINDOW exact returns; with fewer, min(wall, STOP_FLOOR_PCT).

    `returns` is a sequence of (day, return as a fraction, exact) in day order; only exact ones count, but every one
    is checked: a day that is not a date, days not strictly ascending, a return that is not a finite number (an int or
    a float, never a boolean; NaN and infinity included, bridged or not), or an `exact` that is not true or false is a
    ValueError, never dropped. `wall_pct` (the owner's max_daily_loss_pct) must be a finite number above 0 and at
    most 100. When the wall holds the stop below the formula, the basis says so."""
    wall = _number(wall_pct)
    if wall is None or not 0 < wall <= 100:
        raise ValueError("the daily halt (wall) must be a finite number above 0 and at most 100")
    exact: list[tuple[date, float]] = []
    before = None
    for item in returns:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise ValueError("each return must be (day, return as a fraction, exact)")
        day, r, is_exact = item
        if type(day) is not date:
            raise ValueError("a return's day is not a date")
        if before is not None and day <= before:
            raise ValueError("the returns are not in day order (or a day appears twice)")
        before = day
        v = _number(r)
        if v is None or not math.isfinite(v * 100):
            raise ValueError(f"the return of {day.isoformat()} is not a finite number")
        if type(is_exact) is not bool:
            raise ValueError(f"the return of {day.isoformat()} is not marked exact or not")
        if is_exact:
            exact.append((day, v * 100))
    last = exact[-STOP_WINDOW:]
    if len(last) < STOP_WINDOW:
        formula, basis = STOP_FLOOR_PCT, f"fewer than {STOP_WINDOW} returns: the floor"
    else:
        try:
            s = statistics.stdev(p for _, p in last)
        except (OverflowError, ValueError) as exc:
            raise ValueError("the returns' spread cannot be worked out") from exc
        formula = max(STOP_FLOOR_PCT, STOP_MULTIPLE * s)
        basis = f"{STOP_WINDOW} returns ending {last[-1][0].isoformat()}"
    pct = min(wall, formula)
    if pct < formula:
        basis += "; held at the owner's daily halt"
    return WorkingStop(pct, basis, len(last))


# ------------------------------------------------------------------ 5. the paper returns the stop is set from
def paper_returns(record, followed) -> list[tuple[date, float, bool]]:
    """The followed strategies' combined daily return in the paper book, for `working_stop_pct`: per day, the sum of
    their value changes over the sum of their capitals, exact only when every followed strategy has an exact
    observation ending that day (a strategy missing that day makes it not exact).

    `record` is the decay monitor's parsed paper record (`decay._Record`, built by the caller from `decay._read` and
    the sessions; decay exposes its reader only under private names, so it is taken here as an argument and decay is
    not changed). Each strategy's observations are decay's own (`record.path(name)`: its return, its exact or bridge
    mark); the capital each return is over is the earlier summary's capital per strategy, or its own end summary's for
    a first observation from nothing (decay's rule). `followed` are strategy names, as the paper book's
    `strategy_values` keys them. Raises what decay raises for a record it cannot judge."""
    names = list(followed)
    if any(type(n) is not str or not n for n in names):
        raise ValueError("the followed strategies are named by their paper-book names (text)")
    names = sorted(set(names))
    good = {g["day"]: g for g in record.good}
    per_day: dict[str, dict[str, tuple[float, float, bool]]] = {}
    for name in names:
        for o in record.path(name):
            prev = good.get(o["lo"]) if o["lo"] else None
            base = prev["capital"] if prev is not None and name in prev["values"] else o["end"]["capital"]
            per_day.setdefault(o["hi"], {})[name] = (o["r"] * base, base, bool(o["exact"]))
    out = []
    for day in sorted(per_day):
        obs = per_day[day]
        moved, base = sum(m for m, _, _ in obs.values()), sum(b for _, b, _ in obs.values())
        out.append((date.fromisoformat(day), moved / base, len(obs) == len(names) and all(x for _, _, x in obs.values())))
    return out


# ------------------------------------------------------------------ 8. what the owner reads
RESET_COMMAND = f"reset-kill-switch --venue {LIVE_VENUE} --who NAME"


def describe(position: Position | None, working_stop: WorkingStop | None, ladder_file: LadderFile | None,
             proven: int) -> list[str]:
    """The lines `order-step --live --check` prints for the owner: the mode, the stake, the clean sessions of how
    many, what changes next and when at the earliest, the working stop and its basis, and whether fractional units
    are allowed and why. No secret, no account, no path outside the repository, no digest. With more than one
    position (addendum 2, D-D) every sentence is of the reported position: the counts are "at the earliest, for the
    slowest of the positions the record allows", no stake is named that the reported position does not hold, and the
    last change shown is the last one the record tells, with the order that is not told in its own line.

    Refuses (ValueError, one line, T34b-1 R3) a ladder file's `first_stake` or a position's `ceiling` (the
    max_capital it was read under) that is not a number above 0 and at most `webull.LIVE_MONEY_MAX`, the cap the
    owner's ladder file already uses for the first stake."""
    if type(proven) is not int or proven < 0:
        raise ValueError("the count of proven fractional fills must be a whole number, 0 or more")
    if ladder_file is not None and _webull._money(ladder_file.first_stake) is None:
        raise ValueError(f"the ladder file's first_stake must be a number above 0 and at most "
                         f"{_webull.LIVE_MONEY_MAX:,.0f}")
    if position is not None and _webull._money(position.ceiling) is None:
        raise ValueError(f"the position's max_capital must be a number above 0 and at most "
                         f"{_webull.LIVE_MONEY_MAX:,.0f}")
    lines = []
    if ladder_file is None:
        lines.append(f"ladder: no {LADDER_FILE}: live mode works as it does today (every send by ticket, --capital by "
                     "hand)")
    else:
        lines.append(f"ladder: {LADDER_FILE} confirmed by {ladder_file.confirmed_by}, valid until "
                     f"{ladder_file.valid_until}, first stake {_amount(ladder_file.first_stake)}")
    if position is not None:
        if position.mode == "ticket":
            lines.append("mode: ticket (every send needs the owner's ticket approval)")
        elif ladder_file is not None and ladder_file.unattended:
            lines.append(f"mode: unattended (sends without a ticket while every lock is met and {LADDER_FILE} says "
                         "unattended: true)")
        else:
            lines.append(f"mode: unattended by the ladder, but {LADDER_FILE} says unattended: false (or is not there), "
                         "so every send still needs a ticket")
        many = position.possible > 1                                 # addendum 2, D-D: several positions
        untold = next((w for d, w in position.changes if d is None and w.startswith(_UNTOLD_START)), None)
        if many:
            lines.append(f"positions: the record allows {position.possible} (the times do not tell the order of some "
                         "events); shown: the lowest stake, ticket mode where any of them has it, the pause where any "
                         "of them has it, and the count of the slowest")
        lines.append(f"stake at work: {_amount(position.stake)}")
        if position.stake < position.earned:
            earned = (f"the lowest stake of the positions the record allows is {_amount(position.earned)}" if many else
                      f"the record earned {_amount(position.earned)}")
            lines.append(f"held by the owner's ceiling: {earned}, and the owner's ceiling (max_capital "
                         f"{_amount(position.stake)}) holds the stake at work to {_amount(position.stake)}")
        lines.append(f"clean sessions: {position.clean} of {position.needed}"
                     + (" (the slowest of the positions the record allows)" if many else ""))
        left = position.needed - position.clean
        sessions = f"{left} more clean session{'s' if left != 1 else ''}"
        soonest = (f"at the earliest, for the slowest of the positions the record allows, after {sessions}" if many else
                   f"after {sessions} (at the earliest {left} session{'s' if left != 1 else ''} from now)")
        if position.paused:
            since = next((d for d, words in reversed(position.changes) if words == _PAUSE_WORDS), None)
            if untold is not None or many:
                since = None                         # the dated lines stop at a day whose order was not told
            lines.insert(1, f"paused: the 25% drawdown pause{' (in at least one of the positions the record allows)' if many else ''}, "
                         f"paused since {'a day the record does not show' if since is None else since.isoformat()}; "
                         f"only the owner's reset clears it ({RESET_COMMAND}); no clean session counts until then, and "
                         "a fault still halves the stake")
            lines.append(f"next: paused by the 25% drawdown pause{' (in at least one of the positions)' if many else ''}: "
                         f"no clean session counts until the owner resets it ({RESET_COMMAND}); the reset steps one "
                         "step down")
        elif position.mode == "ticket":
            lines.append(f"next: {soonest}, " + ("its ticket mode ends and it is unattended at its own stake" if many
                                                  else "unattended at the same stake")
                         + "; a fault session halves the stake and restarts the count")
        elif position.stake < position.earned:
            lines.append(f"next: no rise is possible under today's ceiling (the owner's max_capital "
                         f"{_amount(position.stake)}); "
                         + (f"{soonest}, its earned stake doubles" if many else
                            f"the record has earned {_amount(position.earned)}, and after {sessions} the earned stake "
                            "doubles")
                         + "; a fault session halves it and restarts the count")
        elif position.stake >= position.ceiling:                     # T34b-1 R5: the ceiling is already reached
            lines.append(f"next: no rise is possible at today's ceiling (the owner's max_capital "
                         f"{_amount(position.ceiling)}), which the stake has reached; a fault session halves it and "
                         "restarts the count")
        elif many:
            lines.append(f"next: {soonest}, its stake doubles, up to the ceiling; a fault session halves it and restarts "
                         "the count")
        else:
            lines.append(f"next: {soonest}, the stake doubles from {_amount(position.stake)}, up to the ceiling; a fault "
                         "session halves it and restarts the count")
        if untold is not None:
            lines.append(f"order not told: {untold}")
            dated = [(d, w) for d, w in position.changes if d is not None]
            if dated:
                lines.append(f"last change the record tells: {dated[-1][0].isoformat()}: {dated[-1][1]}")
        elif position.changes:
            day, words = position.changes[-1]
            lines.append(f"last change: {'at the read' if day is None else day.isoformat()}: {words}")
    if working_stop is not None:
        lines.append(f"working daily stop: {working_stop.pct:.2f}% ({working_stop.basis})")
    if ladder_file is None:
        lines.append(f"fractional units: not allowed (no {LADDER_FILE})")
    elif not ladder_file.fractional:
        lines.append(f"fractional units: not allowed ({LADDER_FILE} says fractional: false)")
    elif proven < 1:
        lines.append(f"fractional units: not allowed yet: no reconciled fill of a fractional quantity on the paper venue "
                     f"({PAPER_VENUE}) in a done batch")
    else:
        lines.append(f"fractional units: allowed ({LADDER_FILE} says fractional: true and the paper venue shows "
                     f"{proven} reconciled fractional fill{'s' if proven != 1 else ''})")
    return lines
