"""The live decay monitor: pause a strategy whose paper results have fallen away from its backtest.

    python -m valedia_trader.research_loop.decay check            report only
    python -m valedia_trader.research_loop.decay check --apply    pause what should be paused, journal the check

A daily check that can only make the trader more careful. For every strategy in the paper book it computes, from the
paper record alone, the strategy's own returns, their annualised Sharpe and their worst fall from a peak, and sets them
against what its tests showed in the idea ledger.

    Rule 1, from the first paper day:   worst fall > 1.5 x the backtest's worst drawdown      -> pause
    Rule 2, from MIN_DAYS_FOR_SHARPE:   Sharpe < half the backtest Sharpe                      -> pause

The record is read from the strategies' VALUES in the paper book's day summaries (`strategy_values`: open value plus
realised result), never from the day's changes, so that no single event can blind it for good:

  - A summary is GOOD when it parses with finite numbers where numbers belong (equity, every strategy value, a capital
    per strategy above zero), is the last summary of its day (no damaged line follows it in the day's file, which could
    be a later one), and adds up against the last good summary before it: the sum over strategies of (value now less
    value then) equals the equity now less the equity then, to the larger of a cent and 1e-9 of the equity. The
    journal's first summary is the baseline: there is nothing before it to add up against. A summary that is not good
    is skipped and counted by kind; it is never the reference for a later one. Earlier summaries of a day (a paper
    trader killed after its summary and run again) are set aside and counted; the last one counts. A wrong reference
    does not blind the check for good: when the next three readable summaries of later days each fail to add up
    against it while adding up among themselves, the reference is the one set aside (named, and counted as "does not
    add up") and the first of the three becomes the reference, with a bridge between what came before and it.
  - A return observation runs from one good summary holding the strategy to the next: its value now less its value
    then, over the capital per strategy of the earlier one. A strategy's first observation is from nothing (it is
    absent from the summary before) over its own first capital. In the journal's first summary a strategy worth
    exactly nothing has held nothing, so it starts there from nothing too; one worth something has a history the
    journal does not hold: it starts there, and its first observation ends on the next good summary.
  - An observation is EXACT when no day lies between its two summaries (no trading session, no skipped summary, no
    damaged line; weekdays count too when the book has traded on a weekday the exchange did not) and the end summary's
    own `strategy_value_change` for the strategy, when it gives one, agrees with the change in its values. A first
    observation from nothing is exact only when the good summary directly before it, with no day between, does not hold
    the strategy (or it is the journal's first summary and the strategy is worth nothing in it). Every other
    observation is a BRIDGE over days the journal no longer holds, a strategy first seen after them included; the days
    it spans are counted.
  - Every rule is read twice. The PLAIN reading is the record as shown: a bridge is one observation (its change in
    value over the earlier capital per strategy). The PROVEN reading uses only what the journal proves: the capital
    inside a bridge is unknown, so a bridge proves nothing, and the record is cut at every bridge into STRETCHES of exact
    observations. Its worst fall is the largest worst fall inside one stretch, each stretch's curve starting at its own
    first summary; its Sharpe is that of the stretches' exact observations pooled, the bridges left out, and the Sharpe
    rule's 60 observations are 60 exact ones. A rule pauses when it fires on BOTH readings. When it fires on exactly
    one, the strategy is HELD: its line names both figures and the days whose record is missing (restore the journal
    for those days from a backup, or decide by hand), and every run exits 2 while that stands. On neither: keep. A
    record with no bridge has one reading. There is no margin and no floor.
  - What this guarantees and what it does not. On the worst-fall rule a lost day that the journal can see never makes
    a pause the whole history would not make, whatever happened to the capital inside it: a stretch is a run of the
    true history's own observations. (A day lost on a date that is no exchange session, and a readable summary whose
    wrong figures agree with one another, cannot be seen.) On the Sharpe rule a lost day is a missing observation:
    either reading can differ from the whole history's Sharpe by what the lost days returned, in either direction, so
    a lost day can make or hide a Sharpe pause. A strategy that fell past its limit only when its falls on both sides of
    a lost day are added together is HELD, loudly, never quietly kept or paused.

The backtest is both stored results of the idea, development and sealed: the worst fall compared against is the
larger of the two, the Sharpe the lower; an idea with only one uses that one. Each figure must be a real finite number
(the worst fall not negative). With a backtest Sharpe not above zero, or paper returns with no spread (a strategy that
has not traded), the Sharpe rule is not judged and never pauses. The Sharpe is computed exactly as the gauntlet's
headline figure (`performance_report`): mean over sample standard deviation (ddof 1), times the square root of 252.
The worst fall uses the same `max_drawdown`, from the starting capital.

Days on which a fund a strategy held had a carried price (`stale_prices`, a list or a mapping) are judged like any
other and counted. Trading days with no journal file are counted ("days missing"). A damaged journal line is ignored
and named, by file and line, on every run until a person repairs it; no text from the journal or the ledger appears in
any message.

It never un-pauses, promotes or changes anything else: the only write to the ledger is
`set_status(id, "paused", reason, "decay")`, for a strategy still trading, and the only write to the journal is one
`decay_check` entry a day. Report-only opens the ledger read-only. `--apply` runs one at a time: it holds an
operating-system lock on `journal/decay.lock`, released with the process however it ends; a second run that finds it
held stops and writes nothing. Holding that lock, `--apply` opens the ledger read-write once first, which lets SQLite
roll back a write that a killed program left unfinished; report-only never does, and says so.

Exit codes:
  0  every strategy that can be judged was judged and none is to be paused (one not yet judgeable for an ordinary
     reason is listed: fewer than two observations, no backtest figure; a flat record is judged on its worst fall; an
     idea not in the ledger that the latest good summary no longer values is a note);
  1  at least one strategy is paused or would be;
  2  a strategy is HELD; or a fault (the latest summary is not good; a strategy's stretch holds fewer than two good
     summaries; its idea's stored result is malformed; an idea the latest good summary values is not in the ledger),
     the check could not run, or strategies are trading and not one could be judged. Neither hides a pause: under
     --apply the pause is written and the code is 2.
"""
from __future__ import annotations

import argparse
import bisect
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
from valedia_trader.research_loop.paper import Journal, describe_skipped

__all__ = ["MIN_DAYS_FOR_SHARPE", "DRAWDOWN_MULTIPLE", "SHARPE_FRACTION", "CheckCannotRun", "CheckBusy", "run_lock",
           "annualised_sharpe", "worst_fall", "has_traded", "judge", "two_readings", "check", "main"]

MIN_DAYS_FOR_SHARPE = 60          # return observations before the Sharpe rule decides (proven reading: exact ones)
DRAWDOWN_MULTIPLE = 1.5           # pause when the paper fall is more than this times the backtest's worst
SHARPE_FRACTION = 0.5             # pause when the paper Sharpe is below this fraction of the backtest's
MIN_DAYS = 2                      # fewer observations than this: nothing can be said
TRADING = ("passed backtest only", "passed paper")       # the statuses the paper trader trades (cli.PAPER_STATUSES)
SOURCE = "decay"
PERIODS_PER_YEAR = 252.0
ZERO = 1e-15                      # the Sharpe convention's zero (performance_report's threshold on the sd)
CENT, RELATIVE = 0.01, 1e-9       # how closely the strategies' value changes must add up to the book's equity change
REJECTIONS = 3                    # later summaries that, agreeing among themselves, set a wrong reference aside
LOCK_NAME = "decay.lock"
KINDS = ("does not add up", "not readable", "damaged line after it")    # why a summary is skipped, in printing order
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class CheckCannotRun(RuntimeError):
    """The check itself could not run (exit 2). The message names paths, files and lines, never journal or ledger text."""


class CheckBusy(CheckCannotRun):
    """Another `--apply` run holds the lock."""


class _CannotJudge(Exception):
    def __init__(self, text: str, fault: bool = False) -> None:
        super().__init__(text)
        self.fault = fault


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
    """False when every return is zero (within ZERO): the strategy has never held anything."""
    r = np.asarray(returns, dtype=float).ravel()
    return bool(np.any(np.abs(r) > ZERO))


def _has_spread(returns) -> bool:
    r = np.asarray(returns, dtype=float).ravel()
    return bool(r.size >= 2 and float(r.std(ddof=1)) > ZERO)


def _real(x, at_least: float | None = None) -> float | None:
    """A real finite number (an int or a float, never a bool or a string), else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        v = float(x)
    except OverflowError:                               # an integer too large to be a float
        return None
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
    """Both rules on figures already computed (one reading). `decision` is pause, keep or cannot judge.

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


def two_readings(plain: dict, proven: dict, figures: dict, missing: list[str]) -> dict:
    """The decision on a record that holds a bridge, from `judge` on the plain reading and on the proven one. A rule
    pauses when it fires on both; when it fires on exactly one the strategy is held; on neither, the plain verdict
    stands (keep, or cannot judge). `figures` holds both readings' Sharpe, worst fall and observation count; `missing`
    names the days whose record is missing."""
    f = figures
    text = {"drawdown": lambda rules: (f"worst fall {f['plain_drawdown']:.2%} on the plain reading and {f['proven_drawdown']:.2%} "
                                       f"on the proven one, against a limit of {rules['drawdown']['limit']:.2%}"),
            "sharpe": lambda rules: (f"Sharpe {f['plain_sharpe']:.2f} over {f['plain_days']} paper days on the plain reading "
                                     f"and {f['proven_sharpe']:.2f} over {f['proven_days']} exact ones on the proven one, "
                                     f"against a limit of {rules['sharpe']['limit']:.2f}")}
    both = [k for k in ("drawdown", "sharpe") if plain["rules"][k]["fires"] and proven["rules"][k]["fires"]]
    one = [k for k in ("drawdown", "sharpe") if plain["rules"][k]["fires"] != proven["rules"][k]["fires"]]
    rules = {k: dict(plain["rules"][k]) for k in ("drawdown", "sharpe")}
    for k in both:
        rules[k]["state"] = "pause"
    for k in one:
        rules[k].update(fires=False, state="held")
    names = {"drawdown": "worst-fall", "sharpe": "Sharpe"}
    held = [f"held: the {names[k]} rule fires on the {'plain' if plain['rules'][k]['fires'] else 'proven'} reading only: "
            f"{text[k](rules)}" for k in one]
    restore = (f"the record for {', '.join(missing)} is missing: restore the journal for those days from a backup, "
               "or decide by hand")
    if both:
        reasons = [f"paper {text[k](rules)}: over the limit on both readings" for k in both] + held
        return {"decision": "pause", "rules": rules, "reasons": reasons, "held": one}
    if one:
        problems = [r for r in plain["reasons"] if plain["decision"] == "cannot judge"]
        return {"decision": "held", "rules": rules, "reasons": held + [restore] + problems, "held": one}
    proven_note = (f"proven reading: Sharpe {f['proven_sharpe']:.2f} over {f['proven_days']} exact observations, "
                   f"worst fall {f['proven_drawdown']:.2%}")
    return {"decision": plain["decision"], "rules": rules, "reasons": plain["reasons"] + [proven_note], "held": []}


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


# ---------------------------------------------------------------- the ledger
def _ledger_file(path) -> Path:
    p = Path(path)
    if not p.is_file():
        raise CheckCannotRun(f"there is no idea ledger at {p}")
    if p.stat().st_size == 0:
        raise CheckCannotRun(f"the idea ledger at {p} is empty")
    return p


def _unfinished_write(p: Path) -> bool:
    hot = Path(str(p) + "-journal")
    try:
        return hot.is_file() and hot.stat().st_size > 0
    except OSError:
        return False


def _recover(path) -> None:
    """For `--apply`, holding the monitor's lock and about to write: one ordinary read-write open of the ledger, which
    lets SQLite roll back a write a killed program left unfinished (a hot rollback journal). It never creates the file."""
    p = _ledger_file(path)
    db = None
    try:
        db = sqlite3.connect(p.resolve().as_uri() + "?mode=rw", uri=True)
        db.execute("SELECT count(*) FROM sqlite_master").fetchall()
    except sqlite3.Error as exc:
        raise CheckCannotRun(f"the idea ledger at {p} could not be read ({type(exc).__name__})") from None
    finally:
        if db is not None:
            db.close()


class _LedgerView:
    """The idea ledger opened read-only, read row by row so that one malformed idea cannot stop the others."""

    def __init__(self, path) -> None:
        p = _ledger_file(path)
        self._db = None
        try:
            self._db = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
            tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        except sqlite3.Error as exc:
            self.close()
            if _unfinished_write(p):
                raise CheckCannotRun(
                    f"the idea ledger at {p} holds an unfinished write, left by a program that stopped in the middle of it "
                    f"({p.name}-journal beside it); report-only never opens the ledger for writing, so it cannot read it. "
                    "Run the check with --apply (it holds its lock and lets SQLite roll the unfinished write back), or "
                    "open the ledger with any program that writes to it") from None
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

    def paused_by_this_monitor_on(self, as_of: str) -> list[int]:
        """The ideas this monitor paused for `as_of`, as the ledger records them (a run killed before its journal entry
        leaves them there)."""
        rows = self._db.execute("SELECT DISTINCT hypothesis_id FROM findings WHERE source = ? AND finding LIKE ?",
                                (SOURCE, f"status -> paused: decay monitor, {as_of}:%")).fetchall()
        return sorted(int(r[0]) for r in rows if isinstance(r[0], int))

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


def _stored_result(raw, which: str) -> dict | None:
    """The `result` of a stored test, None when there is none; a fault when what is stored cannot be read."""
    if raw is None:
        return None
    problem = _CannotJudge(f"the idea ledger's stored {which} result could not be read", fault=True)
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
    """The larger or lower of the stored figures; None when no result stores one (absent or null: no figure, an ordinary
    reason); NaN when a stored figure is there and unusable (a malformed result, a fault)."""
    stored = [r.get(key) for r in results if r.get(key) is not None]
    if not stored:
        return None
    values = [_real(v, at_least) for v in stored]
    if any(v is None for v in values):
        return float("nan")
    return pick(values)


# ---------------------------------------------------------------- the paper record
def _read(directory: Path) -> tuple[list[tuple[str, int, dict]], list[dict]]:
    """Every entry with its file's day and line number, in date order and each file in the order written; and the lines
    that are not a complete JSON object, by file and line only (read as `Journal.read` reads them)."""
    entries: list[tuple[str, int, dict]] = []
    damaged: list[dict] = []
    for path in sorted(directory.glob("????-??-??.jsonl")):
        for number, raw in enumerate(path.read_bytes().split(b"\n"), 1):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except (ValueError, RecursionError):
                entry = None
            if isinstance(entry, dict):
                entries.append((path.stem, number, entry))
            else:
                damaged.append({"file": path.name, "line": number})
    return entries, damaged


def _valuation(e: dict) -> tuple[float, dict[str, float], float] | None:
    """The summary's equity, strategy values and capital per strategy, when each is a real finite number (the capital
    above zero); None otherwise."""
    equity, capital, values = _real(e.get("equity")), _real(e.get("capital_per_strategy")), e.get("strategy_values")
    if equity is None or capital is None or capital <= 0 or not isinstance(values, dict):
        return None
    out = {}
    for name, v in values.items():
        x = _real(v)
        if x is None:
            return None
        out[name] = x
    return equity, out, capital


def _adds_up(now: tuple, then: tuple) -> bool:
    """The strategies' change in value from `then` to `now` (a strategy absent from one is worth nothing there) equals the
    book's change in equity, to the larger of a cent and 1e-9 of the equity."""
    (e1, v1, _), (e0, v0, _) = now, then
    moved = sum(v1.get(s, 0.0) - v0.get(s, 0.0) for s in sorted(set(v1) | set(v0)))
    return abs(moved - (e1 - e0)) <= max(CENT, RELATIVE * max(abs(e1), abs(e0)))


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


def _mentions(e: dict, name: str) -> bool:
    if e.get("type") == "summary":
        return any(isinstance(e.get(k), dict) and name in e[k] for k in ("strategy_values", "strategy_value_change"))
    return e.get("type") in ("decision", "fill") and e.get("strategy") == name


class _Record:
    """The paper book up to `as_of`, read once: the good summaries in order, the skipped ones by kind, the duplicates
    set aside, the days whose last readable summary may not be their last, and what is wrong with the latest one."""

    def __init__(self, entries: list[tuple[str, int, dict]], damaged: list[dict], as_of: str, sessions) -> None:
        by_day: dict[str, list[tuple[int, dict]]] = {}
        for day, line, e in entries:
            if e.get("type") == "summary" and day <= as_of:
                by_day.setdefault(day, []).append((line, e))
        last_damaged: dict[str, list[int]] = {}
        for d in damaged:
            last_damaged.setdefault(d["file"][:-len(".jsonl")], []).append(d["line"])
        self.uncertain = {day: [n for n in lines if n > max((ln for ln, _ in by_day.get(day, [])), default=0)]
                          for day, lines in last_damaged.items() if day <= as_of}
        self.uncertain = {day: lines for day, lines in self.uncertain.items() if lines}
        self.duplicates = {day: len(items) - 1 for day, items in by_day.items() if len(items) > 1}
        self.good: list[dict] = []                         # {"day", "equity", "values", "capital", "entry"}
        self.skipped: list[tuple[str, str]] = []            # (day, kind)
        self.set_aside: list[str] = []                      # references set aside by the next three summaries
        rejecting: list[dict] = []                          # readable summaries after the reference that reject it
        gathering = True                                    # its next REJECTIONS readable summaries not yet all seen
        for day in sorted(by_day):
            _, e = max(by_day[day], key=lambda item: item[0])
            val = _valuation(e) if e.get("day") == day else None
            if day in self.uncertain:
                self.skipped.append((day, "damaged line after it"))
                continue
            if val is None:
                self.skipped.append((day, "not readable"))
                continue
            item = {"day": day, "equity": val[0], "values": val[1], "capital": val[2], "entry": e}
            if not self.good or _adds_up(val, _as_valuation(self.good[-1])):
                self.good.append(item)
                rejecting, gathering = [], True
                continue
            self.skipped.append((day, "does not add up"))
            if not gathering:
                continue
            rejecting.append(item)
            if len(rejecting) < REJECTIONS:
                continue
            gathering = False
            if all(_adds_up(_as_valuation(b), _as_valuation(a)) for a, b in zip(rejecting, rejecting[1:])):
                wrong = self.good.pop()                     # the reference is the one that is wrong
                self.set_aside.append(wrong["day"])
                self.skipped.append((wrong["day"], "does not add up"))
                for r in rejecting:
                    self.skipped.remove((r["day"], "does not add up"))
                    self.good.append(r)
                rejecting, gathering = [], True
        self.skipped.sort()
        self.latest = max(set(by_day) | set(self.uncertain), default=None)
        self.latest_problem = None
        if self.latest is not None and not (self.good and self.good[-1]["day"] == self.latest):
            day = self.latest
            if day in self.uncertain:
                lines = ", ".join(f"line {n}" for n in self.uncertain[day])
                self.latest_problem = (f"the latest day, {day}, may hold a summary that cannot be read: {day}.jsonl {lines} "
                                       f"could not be read and comes after its last readable summary")
            else:
                kind = next(k for d, k in self.skipped if d == day)
                self.latest_problem = f"the latest day summary, {day}, is not good ({kind})"
        marks = set(sessions) | set(by_day) | set(self.uncertain)
        weekdays = {str(d.date()) for d in pd.bdate_range(min(marks), as_of)} if marks else set()
        if any(day in weekdays and day not in sessions for day in by_day):   # the book trades weekdays the exchange does not
            marks |= weekdays
        self._marks = sorted(marks)

    def days_between(self, lo: str, hi: str) -> int:
        """Days strictly between two summary days that are trading sessions, or have a summary or a damaged line."""
        return max(0, bisect.bisect_left(self._marks, hi) - bisect.bisect_right(self._marks, lo))

    def missing(self, lo: str, hi: str) -> list[str]:
        """The days strictly between two summary days whose record is missing; when none shows, the bridge names the
        day its end summary's own change shows unseen."""
        days = self._marks[bisect.bisect_right(self._marks, lo):bisect.bisect_left(self._marks, hi)]
        return days or [f"a day before {hi} that the journal does not show"]

    def path(self, name: str) -> list[dict]:
        """The strategy's return observations from the values, in order: for each, its return `r`, the days it spans
        (1, or more for a bridge), whether it is `exact`, the summary days at its two ends (`lo` is "" for a first
        observation with no good summary before it) and the good summary it ends on."""
        out: list[dict] = []
        prev = None
        for i, g in enumerate(self.good):
            if name not in g["values"]:
                continue
            v = g["values"][name]
            if prev is not None:
                moved, base, lo = v - prev["values"][name], prev["capital"], prev["day"]
            elif i > 0 or v == 0:                           # from nothing: absent before, or the first summary worth nothing
                moved, base, lo = v, g["capital"], self.good[i - 1]["day"] if i > 0 else ""
            else:                                           # the first summary holds a history the journal does not: start here
                prev = g
                continue
            change = g["entry"].get("strategy_value_change")
            own = _real(change.get(name)) if isinstance(change, dict) else None
            between = self.days_between(lo, g["day"])
            unseen = own is not None and abs(moved - own) > CENT    # its own day's change shows a day unseen
            r = moved / base
            if r <= -1.0:
                raise _CannotJudge(f"the book shows its whole capital lost by {g['day']}", fault=True)
            out.append({"r": r, "days": 1 + between if between else 2 if unseen else 1, "exact": not between and not unseen,
                        "lo": lo, "hi": g["day"], "end": g})
            prev = g
        return out


def _as_valuation(g: dict) -> tuple[float, dict[str, float], float]:
    return g["equity"], g["values"], g["capital"]


def _stretches(observations: list[dict]) -> list[list[float]]:
    """The exact observations, cut at every bridge into stretches (empty ones dropped)."""
    out: list[list[float]] = [[]]
    for o in observations:
        if o["exact"]:
            out[-1].append(o["r"])
        elif out[-1]:
            out.append([])
    return [s for s in out if s]


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
    """Judge every strategy in the paper book as of `as_of` (default: the last day in the journal with a summary, or
    with a damaged line after its last summary). `strategies` maps hypothesis id to strategy name; by default it is
    read from the journal's decisions, together with every idea the ledger says is trading. Report-only reads the
    ledger read-only and writes nothing. With `apply`, holds the monitor's lock, lets SQLite recover a write left
    unfinished in the ledger, pauses what should be paused and journals the check once a day. Raises CheckCannotRun
    (CheckBusy when another run holds the lock) when the check itself cannot run."""
    _ledger_file(ledger_path)                             # a missing ledger stops the check before anything is created
    if not apply:
        view = _LedgerView(ledger_path)
        try:
            return _check(view, ledger_path, journal, as_of, False, strategies)
        finally:
            view.close()
    with run_lock(journal.dir):
        _recover(ledger_path)
        view = _LedgerView(ledger_path)
        try:
            return _check(view, ledger_path, journal, as_of, True, strategies)
        finally:
            view.close()


def _check(view: _LedgerView, ledger_path, journal: Journal, as_of, apply: bool, strategies) -> dict:
    entries, damaged = _read(journal.dir)
    undated = sum(1 for _, _, e in entries if _date(e.get("day")) is None)
    if as_of is None:
        summary_days = {day for day, _, e in entries if e.get("type") == "summary"}
        found = summary_days | {d["file"][:-len(".jsonl")] for d in damaged}
        latest = max(summary_days, default=None)
        found = {d for d in found if latest is None or d >= latest}      # a damaged day after the last summary counts
        as_of = max(found) if found else str(pd.Timestamp.today().date())
    as_of = str(pd.Timestamp(as_of).date())
    readable = [e for day, _, e in entries if day <= as_of and _date(e.get("day")) is not None]
    already = any(e.get("type") == "decay_check" for day, _, e in entries if day == as_of)
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

    lo = min((e["day"] for e in readable), default=as_of)
    sessions, calendar_note = _sessions(min(lo, as_of), as_of)
    record = _Record(entries, damaged, as_of, sessions)
    held = _held_by_day(readable)
    files = {p.stem for p in journal.dir.glob("????-??-??.jsonl")}

    rows = []
    for hid in sorted(names):
        name = names[hid]
        row = {"hypothesis_id": hid, "strategy": name, "status": None, "days": 0, "days_missing": None,
               "days_with_carried_price": None, "days_bridged": 0, "stretches": 0, "summaries_skipped": {},
               "duplicates_set_aside": 0, "first_day": None, "live_sharpe": None, "live_max_drawdown": None,
               "proven_sharpe": None, "proven_max_drawdown": None, "proven_days": 0, "missing_days": [],
               "backtest_sharpe": None, "backtest_max_drawdown": None, "backtest_sources": [], "drawdown_limit": None,
               "sharpe_limit": None, "sharpe_decides": False, "traded": None, "decision": None, "reason": "",
               "fault": False}
        rows.append(row)
        idea = ideas.get(hid)
        if idea is None:                                  # a fault only while the book still values it
            trading = bool(record.good) and name in record.good[-1]["values"]
            row.update(decision="cannot judge" if trading else "not in the ledger", fault=trading,
                       reason=f"hypothesis {hid} is not in the idea ledger" + (
                           "; the latest good summary values it" if trading else
                           "; the latest good summary no longer values it (a note)"))
            continue
        row["status"] = idea["status"]
        problem = None                                    # (text, fault)
        returns: list[float] = []
        observations: list[dict] = []
        bridges: list[dict] = []
        exact: list[float] = []
        try:                                              # the paper figures first: shown even when the idea cannot be read
            first = min((e["day"] for e in readable if name and _mentions(e, name)), default=None)
            if first is None:
                raise _CannotJudge(f"0 paper days; at least {MIN_DAYS} are needed")
            row["first_day"] = first
            row["days_missing"] = sum(1 for d in sessions if first <= d and d not in files)
            skipped = [kind for day, kind in record.skipped if day >= first]
            row["summaries_skipped"] = {k: skipped.count(k) for k in KINDS if k in skipped}
            row["duplicates_set_aside"] = sum(n for day, n in record.duplicates.items() if day >= first)
            observations = record.path(name)
            returns = [o["r"] for o in observations]
            bridges = [o for o in observations if not o["exact"]]
            stretches = _stretches(observations)
            exact = [r for s in stretches for r in s]
            row["days"], row["days_bridged"] = len(returns), sum(o["days"] - 1 for o in bridges)
            row["stretches"], row["proven_days"] = len(stretches), len(exact)
            row["missing_days"] = sorted({d for o in bridges for d in record.missing(o["lo"], o["hi"])})
            row["days_with_carried_price"] = 0
            for o in observations:
                g = o["end"]
                mine, carried = held.get(g["day"], {}).get(name, set()), _carried_funds(g["entry"])
                row["days_with_carried_price"] += bool(mine) and (carried is None or bool(carried & mine))
            if len(returns) < MIN_DAYS:
                lost = len(skipped)
                text = f"{len(returns)} paper day{'s' if len(returns) != 1 else ''}; at least {MIN_DAYS} are needed"
                raise _CannotJudge(text + (f", and its stretch holds fewer than two good summaries ({lost} skipped)" if lost else ""),
                                   fault=bool(lost))
            row["live_sharpe"], row["live_max_drawdown"] = annualised_sharpe(returns), worst_fall(returns)
            row["traded"] = has_traded(returns)
            if bridges:                                   # the proven reading: the stretches only, the bridges left out
                row["proven_sharpe"] = annualised_sharpe(exact) if len(exact) >= 2 else 0.0
                row["proven_max_drawdown"] = max(worst_fall(s) for s in stretches) if stretches else 0.0
            else:                                         # one reading
                row["proven_sharpe"], row["proven_max_drawdown"] = row["live_sharpe"], row["live_max_drawdown"]
        except _CannotJudge as exc:
            problem = (str(exc), exc.fault)
        bt_sharpe = bt_drawdown = None
        try:
            stored = [(label, _stored_result(idea[col], label)) for label, col in
                      (("development", "backtest_result"), ("sealed", "sealed_test_result"))]
            results = [r for _, r in stored if r is not None]
            row["backtest_sources"] = [label for label, r in stored if r is not None]
            bt_drawdown = _combine(results, "max_drawdown", max, at_least=0.0)
            bt_sharpe = _combine(results, "sharpe", min)
            row["backtest_max_drawdown"], row["backtest_sharpe"] = _real(bt_drawdown), _real(bt_sharpe)
        except _CannotJudge as exc:
            problem = (str(exc) + (f"; {problem[0]}" if problem else ""), exc.fault or bool(problem and problem[1]))
        if idea["status"] not in TRADING:
            unrecorded = idea["status"] == "paused" and not view.pause_reason_recorded(hid)
            row.update(decision="paused, reason not recorded" if unrecorded else "left alone",
                       reason=f"its status is {idea['status']}; this monitor never changes it" + (f" ({problem[0]})" if problem else ""))
            continue
        if problem:
            row.update(decision="cannot judge", reason=problem[0], fault=problem[1])
            continue
        verdict = judge(row["live_sharpe"], row["live_max_drawdown"], row["days"], bt_sharpe, bt_drawdown,
                        traded=row["traded"], spread=_has_spread(returns))
        if bridges:                                       # two readings: a rule pauses only when it fires on both
            proven = judge(row["proven_sharpe"], row["proven_max_drawdown"], row["proven_days"], bt_sharpe, bt_drawdown,
                           traded=has_traded(exact), spread=_has_spread(exact))
            figures = {"plain_sharpe": row["live_sharpe"], "plain_drawdown": row["live_max_drawdown"], "plain_days": row["days"],
                       "proven_sharpe": row["proven_sharpe"], "proven_drawdown": row["proven_max_drawdown"],
                       "proven_days": row["proven_days"]}
            verdict = two_readings(verdict, proven, figures, row["missing_days"])
        unusable = any(v is not None and _real(v, low) is None for v, low in ((bt_drawdown, 0.0), (bt_sharpe, None)))
        row.update(decision=verdict["decision"], reason="; ".join(verdict["reasons"]),
                   drawdown_limit=verdict["rules"]["drawdown"]["limit"], sharpe_limit=verdict["rules"]["sharpe"]["limit"],
                   sharpe_decides=verdict["rules"]["sharpe"]["decides"], fault=verdict["decision"] == "cannot judge" and unusable)
        if record.latest_problem and row["decision"] == "keep":   # judged only to the last good summary: that is no keep
            upto = record.good[-1]["day"] if record.good else "no good summary"
            row.update(decision="cannot judge", fault=True, reason=f"{record.latest_problem}; up to {upto}: {row['reason']}")

    responsible = [r for r in rows if r["status"] in TRADING or (r["status"] is None and r["fault"])]
    faults = [f"h{r['hypothesis_id']}: {r['reason']}" for r in rows if r["fault"]]
    if record.latest_problem:
        faults.insert(0, record.latest_problem)
    held_ids = [r["hypothesis_id"] for r in rows if r["decision"] == "held"]
    nothing = bool(responsible) and not any(r["decision"] in ("keep", "pause", "held") for r in responsible)
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
            earlier = view.paused_by_this_monitor_on(as_of)   # a run of this day killed before its entry paused these
            journal.write(as_of, _clean({"type": "decay_check", "as_of": as_of,
                                         "rules": {"drawdown_multiple": DRAWDOWN_MULTIPLE, "sharpe_fraction": SHARPE_FRACTION,
                                                   "min_days_for_sharpe": MIN_DAYS_FOR_SHARPE},
                                         "strategies": rows, "paused": sorted(set(paused) | set(earlier)),
                                         "paused_by_this_run": paused, "held": held_ids, "faults": faults}))
            written = True
    code = 2 if (faults or nothing or held_ids) else 1 if (to_pause or paused) else 0
    return _clean({"as_of": as_of, "strategies": rows, "to_pause": to_pause, "paused": paused, "journal_entry_written": written,
                   "skipped_decision_lines": {"count": len(skipped_files), "files": sorted(set(skipped_files))},
                   "entries_without_a_date": undated, "calendar_note": calendar_note, "unreadable_lines": damaged,
                   "latest_summary": {"day": record.latest, "good": record.latest_problem is None},
                   "references_set_aside": record.set_aside, "held": held_ids,
                   "faults": faults, "nothing_judged": nothing, "exit_code": code})


# ---------------------------------------------------------------- the command
def _line(r: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x:.2%}"

    def num(x):
        return "n/a" if x is None else f"{x:.2f}"
    def both(fmt, plain, proven):                        # the proven figure only when it differs from the plain one
        return fmt(plain) + (f" (proven {fmt(proven)})" if fmt(proven) != fmt(plain) else "")
    missing = "n/a" if r["days_missing"] is None else r["days_missing"]
    carried = "n/a" if r["days_with_carried_price"] is None else r["days_with_carried_price"]
    kinds = r["summaries_skipped"]
    skipped = f"{sum(kinds.values())}" + (f" ({', '.join(f'{k}: {kinds[k]}' for k in KINDS if k in kinds)})" if kinds else "")
    return (f"h{r['hypothesis_id']} {r['strategy'] or '(no paper record)'} [{r['status']}]: {r['decision'].upper()}; "
            f"{r['days']} paper days, days missing: {missing}, days with a carried price: {carried}, "
            f"days bridged: {r['days_bridged']}, stretches: {r['stretches']}, summaries skipped: {skipped}, "
            f"duplicate summaries set aside: {r['duplicates_set_aside']}, "
            f"Sharpe {both(num, r['live_sharpe'], r['proven_sharpe'])} (backtest {num(r['backtest_sharpe'])}), "
            f"worst fall {both(pct, r['live_max_drawdown'], r['proven_max_drawdown'])} "
            f"(backtest {pct(r['backtest_max_drawdown'])}); {r['reason']}")


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
    if out["unreadable_lines"]:
        lines = out["unreadable_lines"]
        print(f"journal lines that could not be read (ignored): {len(lines)} ({describe_skipped(lines, limit=len(lines))})")
    skipped = out["skipped_decision_lines"]
    if skipped["count"]:
        print(f"decision lines skipped (idea number not a whole number): {skipped['count']} ({', '.join(skipped['files'])})")
    if out["entries_without_a_date"]:
        print(f"journal entries skipped (no valid date): {out['entries_without_a_date']}")
    if out["calendar_note"]:
        print(f"days missing are counted on {out['calendar_note']}")
    if out["references_set_aside"]:
        print("a summary set aside as a wrong reference (does not add up against the next three): "
              + ", ".join(out["references_set_aside"]))
    if not out["strategies"]:
        print("no strategy is in the paper book")
    for r in out["strategies"]:
        print(_line(r))
    if out["paused"]:
        print("paused: " + ", ".join(f"h{h}" for h in out["paused"]))
    elif out["to_pause"]:
        print("to pause (run with --apply): " + ", ".join(f"h{h}" for h in out["to_pause"]))
    for r in out["strategies"]:
        if r["decision"] == "held":
            print(f"held: h{r['hypothesis_id']}: {r['reason']}", file=sys.stderr)
    for f in out["faults"]:
        print(f"fault: {f}", file=sys.stderr)
    if out["nothing_judged"]:
        print("nothing was judged: strategies are trading and not one of them could be judged", file=sys.stderr)
    return int(out["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
