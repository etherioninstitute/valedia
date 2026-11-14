"""The live decay monitor: pause a strategy whose paper results have fallen away from its backtest.

    python -m valedia_trader.research_loop.decay check            report only
    python -m valedia_trader.research_loop.decay check --apply    pause what should be paused, journal the check

A daily check that can only make the trader more careful. For every strategy in the paper book it computes,
from the paper record alone, the strategy's own daily returns, their annualised Sharpe and their worst fall from
a peak, and sets them against what its tests showed in the idea ledger.

    Rule 1, from the first paper day:   worst fall > 1.5 x the backtest's worst drawdown      -> pause
    Rule 2, from MIN_DAYS_FOR_SHARPE:   Sharpe < half the backtest Sharpe                      -> pause

A day's return is the strategy's change in value that day over the capital per strategy of the day before (the
capital its positions were sized on); a strategy's first day uses its own first capital. This differs from the
weekly critic, which divides by the same day's capital (overstating a day's move when strategies join the book);
the critic's convention is being repaired separately.

The backtest is both stored results of the idea, development and sealed: the worst fall compared against is the
larger of the two, the Sharpe the lower, so a strategy is paused only when its paper record is worse than everything
its tests showed. An idea with only one of the two uses that one. Each figure must be a real finite number (the worst
fall not negative), otherwise that rule cannot judge. With a backtest Sharpe not above zero, or paper returns with no
spread (a strategy that has not traded), the Sharpe rule is not judged and never pauses.

The Sharpe is computed exactly as the gauntlet's headline figure (`performance_report`): mean over sample standard
deviation (ddof 1) of the daily returns, times the square root of 252, no risk-free rate. The worst fall uses the
same `max_drawdown`, from the starting capital.

A strategy is `cannot judge` and never paused when its figures cannot be trusted: no usable backtest figure, a damaged
journal line in the stretch it needs, fewer than 2 paper days, or a day the book could not be valued: the strategies'
value changes do not add up to the book's equity change (to a cent or 1e-9 of the equity, whichever is larger). That
is the only test of a day. A day on which the paper book carried a fund's last price (`stale_prices`, a list of funds
or a mapping from fund to days carried) and which adds up is judged like any other; the days in a strategy's stretch
on which a fund it held had a carried price are counted and shown ("days with a carried price"). Trading days with no
journal file in a strategy's stretch are counted and shown ("days missing"). Neither is a refusal. A damaged line is named by file and line, never by its text, and no
text from the journal or the ledger appears in an error.

It never un-pauses, promotes or changes anything else: the only write to the ledger is
`set_status(id, "paused", reason, "decay")`, for a strategy still trading, and the only write to the journal is one
`decay_check` entry a day. Report-only opens the ledger read-only. `--apply` runs one at a time: it holds an
operating-system lock on `journal/decay.lock`, released with the process however it ends; a second run that finds it
held stops and writes nothing. A torn line in the as-of day's journal file stops the check (it cannot tell whether
it already ran that day).

Exit codes: 0 nothing to pause, 1 at least one strategy paused or to pause, 2 the check could not run.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import sqlite3
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from valedia_trader.quant_engine import level8_portfolio_risk as risk
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop.ledger import HypothesisLedger
from valedia_trader.research_loop.paper import Journal, JournalDamaged, describe_skipped

__all__ = ["MIN_DAYS_FOR_SHARPE", "DRAWDOWN_MULTIPLE", "SHARPE_FRACTION", "CheckCannotRun", "CheckBusy", "run_lock",
           "annualised_sharpe", "worst_fall", "has_traded", "judge", "check", "main"]

MIN_DAYS_FOR_SHARPE = 60          # paper trading days before the Sharpe rule decides anything
DRAWDOWN_MULTIPLE = 1.5           # pause when the paper fall is more than this times the backtest's worst
SHARPE_FRACTION = 0.5             # pause when the paper Sharpe is below this fraction of the backtest's
MIN_DAYS = 2                      # fewer paper days than this: nothing can be said
TRADING = ("passed backtest only", "passed paper")       # the statuses the paper trader trades (cli.PAPER_STATUSES)
SOURCE = "decay"
PERIODS_PER_YEAR = 252.0
ZERO = 1e-15                      # the Sharpe convention's zero (performance_report's threshold on the sd)
CENT, RELATIVE = 0.01, 1e-9       # how closely the strategies' value changes must add up to the book's equity change
LOCK_NAME = "decay.lock"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class CheckCannotRun(RuntimeError):
    """The check itself could not run (exit 2). The message names paths, files and lines, never journal or ledger text."""


class CheckBusy(CheckCannotRun):
    """Another `--apply` run holds the lock."""


class _CannotJudge(Exception):
    pass


# ---------------------------------------------------------------- figures
def annualised_sharpe(returns) -> float:
    """The gauntlet's convention (`performance_report`): mean / sample sd (ddof 1) x sqrt(252), 0 for flat returns."""
    r = np.asarray(returns, dtype=float).ravel()
    sd = float(r.std(ddof=1)) if r.size >= 2 else 0.0
    return float(r.mean() / sd * math.sqrt(PERIODS_PER_YEAR)) if sd > ZERO else 0.0


def worst_fall(returns) -> float:
    """Worst fall from a peak of the compounded curve that starts at the strategy's capital, as `performance_report`."""
    r = np.asarray(returns, dtype=float).ravel()
    return float(risk.max_drawdown(np.clip(r, -0.999999, None)).depth)


def has_traded(returns) -> bool:
    """False when every daily return is zero (within ZERO): the strategy has never held anything."""
    r = np.asarray(returns, dtype=float).ravel()
    return bool(np.any(np.abs(r) > ZERO))


def _has_spread(returns) -> bool:
    r = np.asarray(returns, dtype=float).ravel()
    return bool(r.size >= 2 and float(r.std(ddof=1)) > ZERO)


def _real(x, at_least: float | None = None) -> float | None:
    """A real finite number (an int or a float, never a bool or a string), else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    if not math.isfinite(v) or (at_least is not None and v < at_least):
        return None
    return v


def _whole(x) -> int | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and math.isfinite(x) and x.is_integer():
        return int(x)
    return None


def _date(x) -> str | None:
    if not isinstance(x, str) or not _DATE.fullmatch(x):
        return None
    try:
        pd.Timestamp(x)
    except (ValueError, OverflowError):
        return None
    return x


def judge(live_sharpe: float, live_drawdown: float, days: int, backtest_sharpe, backtest_drawdown,
          traded: bool = True, spread: bool | None = None) -> dict:
    """Both rules on figures already computed. `decision` is pause, keep or cannot judge.

    A backtest figure that is None is missing; one that is not a real finite number (or a negative worst fall) is
    unusable: either makes its rule `cannot judge`, and the strategy `cannot judge` unless the other rule fires (that
    only makes the trader more careful). A backtest Sharpe not above zero, or paper returns with no spread (`traded`
    False: every return zero; `spread` False: no variation), leave the Sharpe rule not judged: it never pauses, and the
    strategy is judged on its worst fall."""
    spread = traded if spread is None else (spread and traded)
    reasons: list[str] = []
    problems: list[str] = []
    notes: list[str] = []
    dd_rule = {"limit": None, "fires": False, "decides": False, "state": "cannot judge"}
    bd = _real(backtest_drawdown, at_least=0.0)
    if backtest_drawdown is None:
        problems.append("the idea ledger has no backtest worst drawdown for it")
    elif bd is None:
        problems.append("the stored backtest worst drawdown is not a real finite number of at least zero")
    else:
        limit = DRAWDOWN_MULTIPLE * bd
        dd_rule.update(limit=limit, decides=True, fires=bool(live_drawdown > limit), state="clear")
        if dd_rule["fires"]:
            dd_rule["state"] = "pause"
            reasons.append(f"paper worst fall {live_drawdown:.2%} after {days} paper days is more than {DRAWDOWN_MULTIPLE:g} times "
                           f"the backtest's worst drawdown {bd:.2%} (limit {limit:.2%})")
    sr_rule = {"limit": None, "fires": False, "decides": False, "state": "cannot judge"}
    bs = _real(backtest_sharpe)
    if backtest_sharpe is None:
        problems.append("the idea ledger has no backtest Sharpe for it")
    elif bs is None:
        problems.append("the stored backtest Sharpe is not a real finite number")
    elif bs <= 0:
        sr_rule["state"] = "not judged"
        notes.append("backtest Sharpe not above zero: Sharpe not judged")
    elif not traded:
        sr_rule["state"] = "not judged"
        notes.append("has not traded: Sharpe not judged")
    elif not spread:
        sr_rule["state"] = "not judged"
        notes.append("paper returns have no spread: Sharpe not judged")
    else:
        limit = SHARPE_FRACTION * bs
        sr_rule.update(limit=limit, decides=bool(days >= MIN_DAYS_FOR_SHARPE))
        sr_rule["fires"] = bool(sr_rule["decides"] and live_sharpe < limit)
        sr_rule["state"] = "pause" if sr_rule["fires"] else "clear" if sr_rule["decides"] else "not yet"
        if sr_rule["fires"]:
            reasons.append(f"paper Sharpe {live_sharpe:.2f} over {days} paper days is below half the backtest Sharpe "
                           f"{bs:.2f} (limit {limit:.2f})")
    if reasons:
        decision = "pause"
    elif problems:
        decision = "cannot judge"
        reasons = problems + notes
    else:
        decision = "keep"
        if sr_rule["state"] == "clear":
            sharpe_note = f"Sharpe {live_sharpe:.2f} against a limit of {sr_rule['limit']:.2f}"
        elif sr_rule["state"] == "not yet":
            sharpe_note = f"Sharpe {live_sharpe:.2f} reported only ({days} of {MIN_DAYS_FOR_SHARPE} days before it decides)"
        else:
            sharpe_note = f"Sharpe {live_sharpe:.2f} not judged"
        reasons = notes + [f"worst fall {live_drawdown:.2%} against a limit of {dd_rule['limit']:.2%}; {sharpe_note}"]
    return {"decision": decision, "rules": {"drawdown": dd_rule, "sharpe": sr_rule}, "reasons": reasons}


# ---------------------------------------------------------------- one run at a time
@contextmanager
def run_lock(journal_dir):
    """Hold the monitor's own lock beside the journal files. The operating system releases it with the process, so a
    killed run never blocks the next one; a run that finds it held raises CheckBusy."""
    path = Path(journal_dir) / LOCK_NAME
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except OSError as exc:
        raise CheckCannotRun(f"the lock {path} could not be opened ({type(exc).__name__})") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CheckBusy(f"another decay check is running (it holds {path}); this one wrote nothing") from None
        yield path
    finally:
        os.close(fd)                                     # closing the file releases the lock


# ---------------------------------------------------------------- the ledger, read-only
class _LedgerView:
    """The idea ledger opened read-only, read row by row so that one malformed idea cannot stop the others."""

    def __init__(self, path) -> None:
        p = Path(path)
        if not p.is_file():
            raise CheckCannotRun(f"there is no idea ledger at {p}")
        if p.stat().st_size == 0:
            raise CheckCannotRun(f"the idea ledger at {p} is empty")
        self._db = None
        try:
            self._db = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
            tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        except sqlite3.Error as exc:
            self.close()
            raise CheckCannotRun(f"the idea ledger at {p} could not be read ({type(exc).__name__})") from None
        if not {"hypotheses", "findings"} <= tables:
            self.close()
            raise CheckCannotRun(f"the idea ledger at {p} has no table of ideas")

    def ideas(self) -> dict[int, dict]:
        rows = self._db.execute("SELECT id, status, backtest_result, sealed_test_result FROM hypotheses").fetchall()
        return {int(r[0]): {"status": r[1], "backtest_result": r[2], "sealed_test_result": r[3]} for r in rows}

    def status(self, hid: int):
        rows = self._db.execute("SELECT status FROM hypotheses WHERE id = ?", (int(hid),)).fetchall()
        return rows[0][0] if rows else None

    def pause_reason_recorded(self, hid: int) -> bool:
        rows = self._db.execute("SELECT 1 FROM findings WHERE hypothesis_id = ? AND finding LIKE 'status -> paused%' LIMIT 1",
                                (int(hid),)).fetchall()
        return bool(rows)

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


def _stored_result(raw, which: str) -> dict | None:
    """The `result` of a stored test, None when there is none; _CannotJudge when what is stored cannot be read."""
    if raw is None:
        return None
    problem = _CannotJudge(f"the idea ledger's stored {which} result could not be read")
    if not isinstance(raw, str):
        raise problem
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        raise problem from None
    if not isinstance(obj, dict):
        raise problem
    result = obj.get("result")
    if result is None:
        return None
    if not isinstance(result, dict):
        raise problem
    return result


def _combine(results: list[dict], key: str, pick, at_least: float | None = None):
    """The larger or lower of the stored figures; None when no result is stored; NaN when one stored figure is unusable."""
    if not results:
        return None
    values = [_real(r.get(key), at_least) for r in results]
    if any(v is None for v in values):
        return float("nan")
    return pick(values)


# ---------------------------------------------------------------- the paper record
def _mentions(e: dict, name: str) -> bool:
    if e.get("type") == "summary":
        return isinstance(e.get("strategy_value_change"), dict) and name in e["strategy_value_change"]
    return e.get("type") in ("decision", "fill") and e.get("strategy") == name


def _book_fault(summary: dict, previous: dict | None) -> bool:
    """True when the book could not be valued on this summary's day: the strategies' value changes do not add up to the
    book's equity change. This is the only test; a day with a carried price that adds up is judged like any other."""
    changes = summary.get("strategy_value_change")
    if not isinstance(changes, dict):
        return True
    values = [_real(v) for v in changes.values()]
    equity = _real(summary.get("equity"))
    if equity is None or any(v is None for v in values):
        return True
    if previous is None:                                  # the book's first day: nothing is held before its orders fill
        delta, scale = 0.0, abs(equity)
    else:
        before = _real(previous.get("equity"))
        if before is None:
            return True
        delta, scale = equity - before, max(abs(equity), abs(before))
    return abs(sum(values) - delta) > max(CENT, RELATIVE * scale)


def _carried_funds(summary: dict) -> set | None:
    """The funds whose price the paper book carried that day (`stale_prices`, a list of funds or a mapping from fund to
    days carried); an empty set when there are none, None when the field is there but cannot be read."""
    stale = summary.get("stale_prices")
    if stale is None:
        return set()
    if isinstance(stale, list) and all(isinstance(s, str) for s in stale):
        return set(stale)
    if isinstance(stale, dict) and all(isinstance(s, str) for s in stale):
        return set(stale)
    return None


def _held_by_day(entries: list[dict]) -> dict[str, dict[str, set]]:
    """Per day and strategy, the funds the strategy holds at that day's summary, from the fills the journal shows."""
    units: dict[tuple[str, str], float] = {}
    out: dict[str, dict[str, set]] = {}
    for e in entries:
        if e.get("type") == "fill" and isinstance(e.get("strategy"), str) and isinstance(e.get("symbol"), str):
            u = _real(e.get("units"))
            if u is not None:
                key = (e["strategy"], e["symbol"])
                units[key] = units.get(key, 0.0) + u
        elif e.get("type") == "summary":
            book: dict[str, set] = {}
            for (strategy, sym), u in units.items():
                if abs(u) > 1e-12:
                    book.setdefault(strategy, set()).add(sym)
            out[e["day"]] = book
    return out


def _returns(summaries: list[dict], name: str, held: dict[str, dict[str, set]], book_first: bool) -> tuple[list[float], int]:
    """The strategy's daily returns: each day's change over the capital per strategy of the day before (its first day
    over its own capital), and the number of those days on which a fund it held had a carried price (an unreadable
    `stale_prices` counts when it held anything). `summaries` starts with the book's summary before the strategy's
    first day, unless `book_first` (the strategy began on the book's first day). Raises _CannotJudge on a day that
    cannot be used."""
    out: list[float] = []
    carried_days = 0
    for i, s in enumerate(summaries):
        if not _mentions(s, name):
            continue
        previous = summaries[i - 1] if i > 0 else None
        if previous is None and not book_first:
            raise _CannotJudge(f"the book could not be valued on {s['day']}")
        if _book_fault(s, previous):
            raise _CannotJudge(f"the book could not be valued on {s['day']}")
        mine, carried = held.get(s["day"], {}).get(name, set()), _carried_funds(s)
        carried_days += bool(mine) and (carried is None or bool(carried & mine))
        capital = _real((previous if out else s).get("capital_per_strategy"))
        if capital is None or capital <= 0:
            raise _CannotJudge(f"the day summary of {(previous if out else s)['day']} has no usable capital per strategy")
        r = float(s["strategy_value_change"][name]) / capital
        if r <= -1.0:
            raise _CannotJudge(f"the day summary of {s['day']} shows its whole capital lost")
        out.append(r)
    return out, carried_days


_CALENDAR: list = []


def _sessions(lo: str, hi: str) -> tuple[list[str], str | None]:
    """Exchange sessions from `lo` to `hi`; weekdays, with a note, when the calendar cannot answer."""
    try:
        if not _CALENDAR:
            import exchange_calendars
            _CALENDAR.append(exchange_calendars.get_calendar("XNYS"))
        return [str(d.date()) for d in _CALENDAR[0].sessions_in_range(lo, hi)], None
    except Exception:
        return [str(d.date()) for d in pd.bdate_range(lo, hi)], "weekdays (the exchange calendar could not be read)"


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


# ---------------------------------------------------------------- the check
def check(ledger_path, journal: Journal, as_of=None, apply: bool = False, strategies: dict[int, str] | None = None) -> dict:
    """Judge every strategy in the paper book as of `as_of` (default: the last day summary in the journal).
    `strategies` maps hypothesis id to strategy name; by default it is read from the journal's decisions, together
    with every idea the ledger says is trading. Report-only reads the ledger read-only and writes nothing. With
    `apply`, holds the monitor's lock, pauses what should be paused and journals the check once a day. Raises
    CheckCannotRun (CheckBusy when another run holds the lock) when the check itself cannot run."""
    view = _LedgerView(ledger_path)                       # a bad ledger stops the check before anything is created
    try:
        if not apply:
            return _check(view, ledger_path, journal, as_of, False, strategies)
        with run_lock(journal.dir):
            return _check(view, ledger_path, journal, as_of, True, strategies)
    finally:
        view.close()


def _check(view: _LedgerView, ledger_path, journal: Journal, as_of, apply: bool, strategies) -> dict:
    everything = journal.read()                           # skips damage: used only to find who traded, from when, and holdings
    undated = sum(1 for e in everything if _date(e.get("day")) is None)
    dated = [e for e in everything if _date(e.get("day")) is not None]
    if as_of is None:
        found = [e["day"] for e in dated if e.get("type") == "summary"]
        as_of = max(found) if found else str(pd.Timestamp.today().date())
    as_of = str(pd.Timestamp(as_of).date())
    try:                                                  # the as-of file holds this check's own entry: it must be whole
        today = journal.read_complete(since=as_of, until=as_of)
    except JournalDamaged as exc:
        raise CheckCannotRun(f"the journal file for {as_of} has a line that is not a complete entry ({describe_skipped(exc.skipped)}); "
                             "the check cannot tell whether it already ran that day") from None
    already = any(e.get("type") == "decay_check" for e in today)
    readable = [e for e in dated if e["day"] <= as_of]
    ideas = view.ideas()

    skipped_files: list[str] = []
    if strategies is None:
        names: dict[int, str | None] = {}
        for e in readable:
            if e.get("type") != "decision" or "hypothesis_id" not in e:
                continue
            hid = _whole(e.get("hypothesis_id"))
            if hid is None or not isinstance(e.get("strategy"), str) or not e["strategy"]:
                skipped_files.append(f"{e['day']}.jsonl")
                continue
            names[hid] = e["strategy"]
        for hid, idea in ideas.items():
            if idea["status"] in TRADING:
                names.setdefault(hid, None)
    else:
        names = {int(k): v for k, v in strategies.items()}

    summary_days = [e["day"] for e in readable if e.get("type") == "summary"]
    held = _held_by_day(readable)
    files = {p.stem for p in journal.dir.glob("????-??-??.jsonl")}
    calendar_note = None

    rows = []
    for hid in sorted(names):
        name = names[hid]
        row = {"hypothesis_id": hid, "strategy": name, "status": None, "days": 0, "days_missing": None, "days_with_carried_price": None, "first_day": None,
               "live_sharpe": None, "live_max_drawdown": None, "backtest_sharpe": None, "backtest_max_drawdown": None,
               "backtest_sources": [], "drawdown_limit": None, "sharpe_limit": None, "sharpe_decides": False, "traded": None,
               "decision": None, "reason": ""}
        rows.append(row)
        idea = ideas.get(hid)
        if idea is None:
            row.update(decision="cannot judge", reason=f"hypothesis {hid} is not in the idea ledger")
            continue
        row["status"] = idea["status"]
        problem = None
        bt_sharpe = bt_drawdown = None
        try:
            stored = [(label, _stored_result(idea[col], label)) for label, col in
                      (("development", "backtest_result"), ("sealed", "sealed_test_result"))]
            results = [r for _, r in stored if r is not None]
            row["backtest_sources"] = [label for label, r in stored if r is not None]
            bt_drawdown = _combine(results, "max_drawdown", max, at_least=0.0)
            bt_sharpe = _combine(results, "sharpe", min)
            row["backtest_max_drawdown"], row["backtest_sharpe"] = _real(bt_drawdown), _real(bt_sharpe)
            first = min((e["day"] for e in readable if name and _mentions(e, name)), default=None)
            if first is None:
                raise _CannotJudge(f"0 paper days; at least {MIN_DAYS} are needed")
            row["first_day"] = first
            sessions, calendar_note = _sessions(first, as_of)
            row["days_missing"] = sum(1 for d in sessions if d not in files)
            before = [d for d in summary_days if d < first]
            try:
                stretch = journal.read_complete(since=before[-1] if before else first, until=as_of)
            except JournalDamaged as exc:
                raise _CannotJudge(f"the journal is damaged in the stretch it needs ({describe_skipped(exc.skipped)})") from None
            summaries = [e for e in stretch if e.get("type") == "summary" and _date(e.get("day")) is not None]
            returns, row["days_with_carried_price"] = _returns(summaries, name, held, book_first=not before)
            row["days"] = len(returns)
            if len(returns) < MIN_DAYS:
                raise _CannotJudge(f"{len(returns)} paper day{'s' if len(returns) != 1 else ''}; at least {MIN_DAYS} are needed")
            row["live_sharpe"], row["live_max_drawdown"] = annualised_sharpe(returns), worst_fall(returns)
            row["traded"] = has_traded(returns)
            spread = _has_spread(returns)
        except _CannotJudge as exc:
            problem = str(exc)
        if idea["status"] not in TRADING:
            unrecorded = idea["status"] == "paused" and not view.pause_reason_recorded(hid)
            row.update(decision="paused, reason not recorded" if unrecorded else "left alone",
                       reason=f"its status is {idea['status']}; this monitor never changes it" + (f" ({problem})" if problem else ""))
            continue
        if problem:
            row.update(decision="cannot judge", reason=problem)
            continue
        verdict = judge(row["live_sharpe"], row["live_max_drawdown"], row["days"], bt_sharpe, bt_drawdown,
                        traded=row["traded"], spread=spread)
        row.update(decision=verdict["decision"], reason="; ".join(verdict["reasons"]),
                   drawdown_limit=verdict["rules"]["drawdown"]["limit"], sharpe_limit=verdict["rules"]["sharpe"]["limit"],
                   sharpe_decides=verdict["rules"]["sharpe"]["decides"])

    to_pause = [r["hypothesis_id"] for r in rows if r["decision"] == "pause"]
    paused: list[int] = []
    written = False
    if apply:
        writer = None
        try:
            for r in rows:
                if r["decision"] == "pause" and view.status(r["hypothesis_id"]) in TRADING:
                    writer = writer or HypothesisLedger(ledger_path)
                    writer.set_status(r["hypothesis_id"], "paused", f"decay monitor, {as_of}: {r['reason']}", SOURCE)
                    r.update(decision="paused", status="paused")
                    paused.append(r["hypothesis_id"])
        finally:
            if writer is not None:
                writer.close()
        if paused or not already:                         # once a day; again only if this run paused something new
            journal.write(as_of, _clean({"type": "decay_check", "as_of": as_of,
                                         "rules": {"drawdown_multiple": DRAWDOWN_MULTIPLE, "sharpe_fraction": SHARPE_FRACTION,
                                                   "min_days_for_sharpe": MIN_DAYS_FOR_SHARPE},
                                         "strategies": rows, "paused": paused}))
            written = True
    return _clean({"as_of": as_of, "strategies": rows, "to_pause": to_pause, "paused": paused, "journal_entry_written": written,
                   "skipped_decision_lines": {"count": len(skipped_files), "files": sorted(set(skipped_files))},
                   "entries_without_a_date": undated, "calendar_note": calendar_note,
                   "exit_code": 1 if (to_pause or paused) else 0})


# ---------------------------------------------------------------- the command
def _line(r: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x:.2%}"

    def num(x):
        return "n/a" if x is None else f"{x:.2f}"
    missing = "n/a" if r["days_missing"] is None else r["days_missing"]
    carried = "n/a" if r["days_with_carried_price"] is None else r["days_with_carried_price"]
    return (f"h{r['hypothesis_id']} {r['strategy'] or '(no paper record)'} [{r['status']}]: {r['decision'].upper()}; "
            f"{r['days']} paper days, days missing: {missing}, days with a carried price: {carried}, Sharpe {num(r['live_sharpe'])} (backtest {num(r['backtest_sharpe'])}), "
            f"worst fall {pct(r['live_max_drawdown'])} (backtest {pct(r['backtest_max_drawdown'])}); {r['reason']}")


def main(argv=None, paths: LoopPaths | None = None) -> int:
    ap = argparse.ArgumentParser(prog="decay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="judge every paper strategy against its backtest")
    c.add_argument("--apply", action="store_true", help="pause what should be paused and journal the check")
    args = ap.parse_args(argv)
    paths = paths or LoopPaths()
    journal_dir = Path(paths.journal)
    if not journal_dir.is_dir():
        print(f"the check could not run: there is no journal folder at {journal_dir}", file=sys.stderr)
        return 2
    try:
        out = check(Path(paths.ledger), Journal(journal_dir), apply=args.apply)
    except CheckCannotRun as exc:
        print(f"the check could not run: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # anything else that stops the check is exit 2, never a silent pass; never its text
        frames = traceback.extract_tb(exc.__traceback__)
        where = f" at {Path(frames[-1].filename).name} line {frames[-1].lineno}" if frames else ""
        print(f"the check could not run: {type(exc).__name__}{where}", file=sys.stderr)
        return 2
    print(f"decay check as of {out['as_of']}" + (" (applied)" if args.apply else " (report only)"))
    skipped = out["skipped_decision_lines"]
    if skipped["count"]:
        print(f"decision lines skipped (idea number not a whole number): {skipped['count']} ({', '.join(skipped['files'])})")
    if out["entries_without_a_date"]:
        print(f"journal entries skipped (no valid date): {out['entries_without_a_date']}")
    if out["calendar_note"]:
        print(f"days missing are counted on {out['calendar_note']}")
    if not out["strategies"]:
        print("no strategy is in the paper book")
    for r in out["strategies"]:
        print(_line(r))
    if out["paused"]:
        print("paused: " + ", ".join(f"h{h}" for h in out["paused"]))
    elif out["to_pause"]:
        print("to pause (run with --apply): " + ", ".join(f"h{h}" for h in out["to_pause"]))
    return int(out["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
