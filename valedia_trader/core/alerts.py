"""The watchdog: a fixed list of conditions that mean something has stopped, each raised once.

    python -m valedia_trader.core.alerts watch [--notify]     run the checks once; write each NEW alert; --notify shows it
    python -m valedia_trader.core.alerts list                 the alerts that are open now

The checks only read the trader's files (every sqlite file is opened read-only with a short timeout); the one
thing the watch writes is `artifacts/owner/alerts.jsonl`, a line per event with `time`, `level`, `key`, `title`
and `detail`. An alert whose key is already open is not raised again. When its condition is no longer true one line
with `level` "cleared" and the same key is written. An alert is open while the last line of its key is not a
"cleared" line, so the file alone says what is open. Conditions, and the keys they raise:

    switch:halt, switch:latch   the operator halt file, or the drawdown latch, is set (the risk gate's own rule)
    orders:<ledger>             an order ledger is blocked, or holds an order whose fate is unknown (its own `blocked`)
    step-stopped:<venue>        an unattended order step of today (New York; a summary with no `mode` key, read from today's
                                journal file only) stopped at a stage other than "session" (a closed market or a holiday is
                                ordinary: no alert); it clears only when a later unattended summary of the same day and venue
                                has the status exactly "done", or when the day passes
    step-unreadable:<day>       an order-step summary today whose status is not one of the words the step writes ("done",
                                "stopped"): a warning of its own, once a day
    approval:refused:<day>      an approval was refused today (a summary with mode approve, stopped at stage ticket): a warning
                                of its own, once a New York day. Summaries with a `mode` (propose, approve) are practice
                                runs of the approval flow: the conditions about the unattended run look only at those without
    mode:unknown:<day>          a summary today whose `mode` is neither absent (the unattended run) nor exactly `propose` or
                                `approve`: neither kind of run, never counted as one, one warning a day
    practice-stopped:<m>:<day>  a propose or approve run today stopped at a stage other than ticket or session: a warning of its own
    shared-stopped:<venue>:<class>:<stage>  a SHARED run (T51c S4e; a summary with a `shared` block) of that venue stopped at that
                                stage, and no later shared summary of the same venue and class has said otherwise. The class is
                                `unattended` (a summary with no `mode` key, whatever other keys it carries: critical) or `attended`
                                (any `mode` key: propose, approve and anything else: a warning). It is the book's PRESENT state, not
                                the day's: the journal's latest such summary of the venue and class (the last 30 days of it, any
                                day) decides, so a stop nothing has fixed stays open over a weekend, and a stop first seen after
                                midnight is raised. It clears only when a later summary of the same venue and class is `done` with no
                                shortfall, or stops at another stage (which opens that stage's key; a done run that found a shortfall
                                is `shared-shortfall`); a run of the other class never clears it. Ordinary skips neither open nor
                                clear: a closed market (the unattended run), and stages ticket and session (propose and approve).
                                A stop of a shared run no longer raises `step-stopped` or `practice-stopped` (those keys merged
                                venues and stages, and a clean propose run could clear an unattended stop); an alert still open
                                under one of them, or under an earlier form of this key, is cleared by the next watch in which this
                                check ran (a watch where it failed leaves them open), and the stop is raised again under this key.
                                A run that is NOT shared keeps the two keys above, as before.
    shared-shortfall:<venue>:<class>  a SHARED run (T51c S4e) of that venue ended `done` and its last reconcile found the account
                                holding less than the book counts as its own (its summary's `shared.shortfalls` is not empty): a
                                warning, open while the latest summary of that venue and class (the unattended run, or the attended
                                ones) is such a run (it follows the same state rule as the stop above, over days), and cleared when a
                                later one is `done` with none or stops. (The order ledger's own block is `orders:<ledger>`.)
    job:<label>                 a scheduled com.valedia.* job is not loaded, or its last exit code is not 0
    collector:heartbeat         the order-book collector's newest heartbeat is older than 10 minutes
    picks:no-end                the hourly pick-list log has no "end" line in the last 3 hours
    picks:failed                the last run in that log said "failed"
    journal:damaged             a damaged journal line in the last 30 days
    paper:no-entry              the paper step wrote no summary on the last trading day (the exchange calendar)
    disk:low                    under 5 GB free on the volume that holds the repository

A check that cannot be asked (a file busy or unreadable, `launchctl` missing) is a failure of the watch: the other
checks still run, nothing is raised or cleared for what the failed check owns, the failure is told on stderr, and
the exit code is 2. Exit code: 0 when nothing is open, 1 when an alert is open, 2 when the watch itself failed.

Everything shown or written passes `clean` (its steps, in order, are in its own description): format and control characters
are dropped and whitespace collapsed, the package's output guard replaces a remembered secret by `<secret>`, an order id is
cut to its last 4 characters (a ledger tag `os-20261113-143501-a1b2c3-XLE` becomes `…b2c3-XLE`), digit groups and any run of
9 or more digits (an account number) become `<number>`, the guard runs again, and only then is the text cut to its length:
nothing that rev 2 or rev 3 of this module hid is shown, in part or whole, at any length. Before it reads
or prints anything the command registers every value of the trading file with that guard (`register_trading_file`), into a
private dictionary: nothing is put into the process environment. A trading file that exists and cannot be read is exit 2
with nothing written; a missing one is no reason to stop; so is one over 64 KB or 200 settings, or not UTF-8 text, which is
refused at once. Nothing read back from `alerts.jsonl` or `notifications.jsonl` is trusted either: every field of every line
passes the same cleaning before it is written, printed, shown or sent, and a key that changes under cleaning is its cleaned
form from then on. The output guard is put on the streams before the command binds them to print. An old line whose key held a secret
cleans to the mask, can match no check, and so could never be cleared: it is withheld (not an alert, open or cleared), counted
once in the output of `list` and `watch`, and ignored by the exit code.

Known limits of the guard that this rests on (the package's own, recorded here): a value split by other separators, halves
shorter than 8 characters, a base64 form of a recovery phrase, words of the phrase glued to other letters, and values under
ordinary setting names are not caught.

A phone sender is anything with a `send(alert)` method (and a `name`): give it to `watch` beside the Mac notifier. An
alert counts as shown to a sender only after its `send` returned; `notifications.jsonl`, beside the alert file, keeps a "trying"
line written before each send (it counts toward the 5, so a run killed during a send can never show an alert more than 5 times)
and a "shown" or "failed" line after it. An open alert that was not shown is tried again on each later `watch --notify`, 5 tries in all; after that
`list` and the page say it was not shown.

Every sqlite file is opened read-only (`mode=ro`) by this module, never through a class that can write, recover or
migrate, and waits for another process's write at most 5 seconds in all; a store that is busy, or needs its own program
to recover a journal a killed writer left, is said so and left exactly as it was. Reading a WAL store creates, when they
are absent, an empty `-wal` file and a `-shm` file of 32,768 bytes; that is the one change a read can make.
"""
from __future__ import annotations

import argparse
import bisect
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from valedia_trader.core.secrets import LiveNameInReach, child_env, env_file_path, is_live_file, load_env, redact

__all__ = ["Alert", "Env", "Sender", "MacNotifier", "WatchResult", "watch", "open_alerts", "alerts_file", "main", "clean",
           "open_readonly", "describe", "SourceMissing", "CHECKS", "last_trading_day", "exchange_sessions", "launchctl_text",
           "parse_jobs", "read_tail", "PICKS_LOG", "is_outside_market_hours", "register_trading_file", "TradingFileError",
           "hide_order_ids", "notifications_file", "read_notifications", "notification_note", "is_practice", "is_unattended",
           "has_unknown_mode", "read_alert_file", "withheld_note"]

HEARTBEAT_MAX_AGE_S = 600.0                  # the collector writes one a minute; ten minutes without one is a stop
PICKS_END_MAX_AGE_S = 3 * 3600.0             # the pick-list job runs every hour
DISK_MIN_BYTES = 5 * 10 ** 9
DAMAGE_WINDOW_DAYS = 30
LEDGER_MAX_AGE_SECONDS = 5 * 86400.0         # the order step reconciles first thing each run, so a reconcile that is a few days
                                             # old (a long weekend) is not a stop; the ledger's own default is 6 hours
PICKS_LOG = "valedia-picks-hourly.log"
WEEKLY_JOB, WEEKLY_LOG, WEEKLY_MAX_AGE_S = "com.valedia.research-loop.weekly", "valedia-research-loop.log", 8 * 86400.0
ORDERSTEP_JOB = "com.valedia.orderstep.paper"
ORDERSTEP_AFTER = (10, 30)                   # New York time: the job's runs (09:55 and 10:10) are over by then
LOG_TAIL_BYTES = 16 * 1024 * 1024            # the weekly log can hold a long agent transcript between two run lines
WAIT_BUDGET_S = 5.0                          # the longest a store is waited for, in all
NOTIFY_MAX_TRIES = 5
CLEARED = "cleared"
NEW_YORK = ZoneInfo("America/New_York")

_STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
_END_LINE = re.compile(rf"^({_STAMP}) end$")
_START_LINE = re.compile(rf"^{_STAMP} start$")
_WEEKLY_RUN = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) weekly research loop")
_FAILED = re.compile(r"\bfailed\b(?!:?\s*\d)", re.I)           # "failed: 0" and "failed 3 times" are counts, not a failed run
_LONG_DIGITS = re.compile(r"(?<!\d)(?<!\d\.)\d{9,}(?!\d)(?!\.\d)")          # a number with a decimal point is a price, not an account
_GROUPED_DIGITS = re.compile(r"(?<!\d)\d{3,}(?:[ -]\d{3,}){2,}(?!\d)")          # 3 or more groups of 3 or more digits


# ---------------------------------------------------------------------------------------------- text and files

# Our ledger tag `os-20261113-143501-a1b2c3-XLE` and batch id `os-20261113-143501-a1b2c3`, as rev 3 and 4 knew them (6 hex,
# the fund upper case, the fund left out for a batch id), and as rev 2 knew them (4 or more letters and digits, a fund always).
_OUR_TAG = re.compile(r"(?<![A-Za-z0-9])os-\d{8}-\d{6}-([0-9A-Fa-f]{6})(?:-([A-Z0-9]+(?:[.\-][A-Z0-9]+)*))?(?![A-Za-z0-9])")
_WIDE_TAG = re.compile(r"(?<![A-Za-z0-9])os-\d{8}-\d{6}-([A-Za-z0-9]{4,})-([A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*)")
_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9_.\-])[A-Za-z0-9_\-]+(?![A-Za-z0-9_\-])")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")                # clientOrderId reads as client Order Id
_ID_KEYWORD = re.compile(r"(?i)(?<![a-z])(?:orders?|ids?|refs?|tags?|client)(?![a-z])")
_CLAUSE_END = re.compile(r"(?:;|\.\s)")
_DATEISH = re.compile(r"\d{4}-\d\d-\d\d(?:T\d\d)?|\d{8}(?:T\d{0,6}Z?)?|\d{8}-\d{6}")
ID_MIN = 12
_NEAR_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.\-]*[A-Za-z0-9])?")
_NEAR_WORDS = r"(?:orders?|ids?|tags?|client)"
_NEAR_KEYWORD = re.compile(rf"(?i)(?<![a-z]){_NEAR_WORDS}(?![a-z])[^A-Za-z0-9]*(?:(?:orders?|ids?|tags?|number|no)(?![a-z])[^A-Za-z0-9]*)*$")
NEAR_MIN, NEAR_LETTERS, NEAR_WINDOW = 8, 16, 40

RAW_MIN, RAW_TIMES = 4096, 20                # step 1: at most RAW_MAX = max(20 x limit, 4096) characters are looked at
TAIL = 600                                   # step 6: what is dropped at the end of a text step 1 cut (see `clean`)
ELLIPSIS = "…"
HELD = "<held>"                              # shown instead of a text that does not clean to a stable form (see `clean`)
_KEY_CHARS = re.compile(r"[^A-Za-z0-9_.:-]+")
_KEY_HELD = "_held_"
_registered = {"longest": 0}                 # the longest value registered from the trading file (for step 6)


def _is_readable(tok: str) -> bool:
    """A date or time in an ISO form, a Kalshi ticker (upper case, beginning KX): never an id."""
    return bool(_DATEISH.fullmatch(tok)) or (tok.startswith("KX") and tok == tok.upper())


def _is_id(tok: str) -> bool:
    """A run of 12 or more letters and digits (`_` and `-` may join them) holding a digit: not a date or time, not a Kalshi ticker."""
    return len(tok) >= ID_MIN and any(c.isdigit() for c in tok) and not tok.isdigit() and not _is_readable(tok)


def _is_near_id(tok: str) -> bool:
    """What may follow an id word directly: 8 or more letters and digits (`_`, `-` inside) with a digit and not only digits, or
    16 or more letters; never a dotted name (a file, a version, a decimal, a host), a date or time, or a Kalshi ticker."""
    return (len(tok) >= NEAR_MIN and not tok.isdigit() and "." not in tok and not _is_readable(tok)
            and (any(c.isdigit() for c in tok) or (len(tok) >= NEAR_LETTERS and tok.isalpha())))


def _keyword_spans(t: str) -> list[tuple[int, int]]:
    """Where the id words stand in `t` (start, end), read with camelCase split (clientOrderId: client, Order, Id)."""
    marks = [m.start() for m in _CAMEL.finditer(t)]
    if not marks:
        return [m.span() for m in _ID_KEYWORD.finditer(t)]
    return [(_unsplit(m.start(), marks), _unsplit(m.end(), marks)) for m in _ID_KEYWORD.finditer(_CAMEL.sub(" ", t))]


def _unsplit(i: int, marks: list[int]) -> int:
    """A position in the camel-split text as a position in the text: each inserted space before it is taken off."""
    lo, hi = 0, len(marks)                                  # marks are positions in the text; the k-th lands at marks[k] + k
    while lo < hi:
        mid = (lo + hi) // 2
        if marks[mid] + mid < i:
            lo = mid + 1
        else:
            hi = mid
    return i - lo


def _tag_spans(rx, t: str) -> list[tuple[int, int, int]]:
    """Our ledger tags and batch ids: (start, where the shown part begins, end); the shown part is the last 4 of the random part
    and the fund."""
    return [(m.start(), m.end(1) - 4, m.end()) for m in rx.finditer(t)]


def _clause_id_spans(t: str, extra=frozenset(), found=None) -> list[tuple[int, int, int]]:
    """Ids of 12 or more after an id word anywhere in the same clause (rev 3's rule): (start, end - 4, end). A token in `extra`
    counts whatever stands before it; each one found is added to `found`."""
    clause_starts = [0] + [c.end() for c in _CLAUSE_END.finditer(t)]
    kw = _keyword_spans(t)
    kw_ends = [b for _, b in kw]
    spans = []
    for m in _ID_TOKEN.finditer(t):
        tok = m.group(0)
        if not _is_id(tok) or t[max(0, m.start() - 1):m.start()] == "…":
            continue
        if m.end() < len(t) and t[m.end()] == "." and m.end() + 1 < len(t) and t[m.end() + 1].isalnum():
            continue                                                     # inside a dotted name: a file, a version, a host
        if tok not in extra:
            start = clause_starts[bisect.bisect_right(clause_starts, m.start()) - 1]
            k = bisect.bisect_right(kw_ends, m.start()) - 1               # the last id word that ends before the token
            if k < 0 or kw[k][0] < start:
                continue
        if found is not None:
            found.add(tok)
        spans.append((m.start(), m.end() - 4, m.end()))
    return spans


def _near_id_spans(t: str, extra=frozenset(), found=None) -> list[tuple[int, int, int]]:
    """Ids of 8 or more right after an id word (rev 2's rule, the readable names left out): (start, end - 4, end)."""
    spans = []
    for m in _NEAR_TOKEN.finditer(t):
        tok = m.group(0)
        if not _is_near_id(tok) or t[max(0, m.start() - 1):m.start()] == "…":
            continue
        if tok not in extra and not _NEAR_KEYWORD.search(_CAMEL.sub(" ", t[max(0, m.start() - NEAR_WINDOW):m.start()])):
            continue
        if found is not None:
            found.add(tok)
        spans.append((m.start(), m.end() - 4, m.end()))
    return spans


def _digit_spans(rx, t: str) -> list[tuple[int, int, int]]:
    return [(m.start(), m.end(), m.end()) for m in rx.finditer(t) if rx is not _GROUPED_BY_SECRET or "<secret>" in m.group()]


# Digit groups of which the guard has taken one or more (it masks a whole token, so `9503-delta` with `delta` a word of a
# phrase goes, and `6236 0220` alone would no longer be 3 groups): a `<secret>` counts as a group.
_GROUPED_BY_SECRET = re.compile(r"(?<!\d)(?=(?:<secret>[ -])*\d)(?:\d{3,}|<secret>)(?:[ -](?:\d{3,}|<secret>)){2,}(?!\d)")


_BY_ID, _BY_DIGITS = 1, 2


def _replace(text: str, origin: list[int], spans, hidden: bytearray, flag: int, placeholder: str):
    """`text` with each span's hidden part replaced by `placeholder` (its shown part kept), and where each character came from
    in the text the chain began with (-1 for a placeholder). Every character hidden is marked in `hidden` with `flag`."""
    parts, where, last = [], [], 0
    for a, k, b in spans:
        parts += [text[last:a], placeholder, text[k:b]]
        where += origin[last:a]
        for o in origin[a:k]:
            if o >= 0:
                hidden[o] |= flag
        where += [-1] * len(placeholder)
        where += origin[k:b]
        last = b
    parts.append(text[last:])
    where += origin[last:]
    return "".join(parts), where


def _chain(t: str, tag_rx, id_spans, hidden: bytearray, digits: bool = True) -> None:
    """One earlier revision's way of masking, over `t`, marking what it hides: its tags, then its ids, then the digit groups
    and long digit runs, then its ids once more (a `<number>` can stand between an id word and an id)."""
    cur, origin = _replace(t, list(range(len(t))), _tag_spans(tag_rx, t), hidden, _BY_ID, ELLIPSIS)
    cur, origin = _replace(cur, origin, id_spans(cur), hidden, _BY_ID, ELLIPSIS)
    if digits:
        for rx in (_GROUPED_BY_SECRET, _GROUPED_DIGITS, _LONG_DIGITS):
            cur, origin = _replace(cur, origin, _digit_spans(rx, cur), hidden, _BY_DIGITS, "<number>")
        _replace(cur, origin, id_spans(cur), hidden, _BY_ID, ELLIPSIS)


_CHAINS = ((_WIDE_TAG, _near_id_spans), (_OUR_TAG, _clause_id_spans))       # rev 2's rules; rev 3's (and 4's)
_HIDDEN_RUN = re.compile(rb"[\x01-\x03]+")


def _mask(t: str, extra=frozenset(), digits: bool = True) -> str:
    """Step 4: every character that rev 2's rules or rev 3's rules would hide is hidden (both are run over the same text, each
    in its own order, and what either hides is hidden; an id in `extra`, found before the guard, is hidden too, all but its
    last 4, wherever it stands). A hidden run that holds a digit mask becomes `<number>`, any other `…`
    (so an id keeps its last 4 characters, and a tag the last 4 of its random part and its fund)."""
    hidden = bytearray(len(t))
    for tag_rx, id_spans in _CHAINS:
        _chain(t, tag_rx, id_spans, hidden, digits)
    if extra:                                                            # ids found before the guard, wherever they stand now
        spans = sorted({(m.start(), m.end() - 4, m.end()) for rx in (_ID_TOKEN, _NEAR_TOKEN) for m in rx.finditer(t)
                        if m.group(0) in extra})
        last = -1
        for a, k, b in spans:
            if a >= last:
                for i in range(a, k):
                    hidden[i] |= _BY_ID
                last = b
    out, last = [], 0
    for m in _HIDDEN_RUN.finditer(bytes(hidden)):
        out += [t[last:m.start()], "<number>" if any(f & _BY_DIGITS for f in m.group()) else ELLIPSIS]
        last = m.end()
    return "".join(out) + t[last:]


def hide_order_ids(text: str) -> str:
    """An order id is shown by its last 4 characters only. Our own ledger tag (`os-20261113-143501-a1b2c3-XLE`) and batch id
    (`os-20261113-143501-a1b2c3`) are shortened wherever they stand (`…b2c3-XLE`, `…b2c3`: the last 4 of the random part, then
    the fund). Any other id, a run of 12 or more letters and digits with a digit in it and no dot (`_` and `-` may join its
    parts), that follows one of the words order, orders, id, ids, ref, tag or client anywhere in the same clause (lists and
    `id is X` included) becomes `…` and its last 4 characters; so does a shorter one, of 8 or more with a digit (or of 16 or
    more letters), that follows order, id, tag or client directly (only other id words, `number` or `no` between). Dates and
    times, numbers with a decimal point, file names, version strings, Kalshi tickers (upper case, beginning KX), funds, prices
    and counts are never shortened."""
    return _mask(str(text), digits=False)


def _normalise(text: str, collapse: bool = True) -> str:
    """Step 2: format characters (Cf: direction overrides and marks, zero-width characters), control characters (Cc) and lone
    surrogates are dropped; a line break, tab or separator is a space; then every run of whitespace is one space (unless
    `collapse` is false: the text as rev 2 and rev 3 gave it to the guard)."""
    if text.isascii():
        if _ASCII_CONTROL.search(text):
            text = _ASCII_CONTROL.sub(lambda m: " " if m.group() in "\n\r\t" else "", text)
        return " ".join(text.split()) if collapse else text
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch in "\n\r\t" or cat in ("Zl", "Zp"):
            out.append(" ")
        elif cat not in ("Cf", "Cc", "Cs"):
            out.append(ch)
    return " ".join("".join(out).split()) if collapse else "".join(out)


_ASCII_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _steps(text: str) -> str:
    """Steps 2 to 5 on a text already bounded: normalise, guard, mask, guard again. The ids are first looked for twice: in the
    normalised text before the guard runs (a mask the guard puts over the words before an id never makes that id plain), and
    as rev 2 and rev 3 looked for them, after the guard over the text with its spaces still as they were (the guard masks a
    whole token, a `.` or `;` that ends a clause included). An id found either way is shortened wherever it stands."""
    t = _normalise(text)
    seen: set = set()
    for source in (t, redact(_normalise(text, collapse=False))[0]):
        for tag_rx, id_spans in _CHAINS:
            id_spans(_replace(source, list(range(len(source))), _tag_spans(tag_rx, source), bytearray(len(source)), _BY_ID,
                              ELLIPSIS)[0], found=seen)
    t, _ = redact(t)
    t = _mask(t, frozenset(seen))
    t, _ = redact(t)
    return t


def _back_to_token(t: str, k: int) -> str:
    """`t[:k]`, never ending inside a token: when the cut falls inside one, that whole token goes."""
    head = t[:k]
    if 0 < k < len(t) and t[k] != " " and head[-1:] != " ":
        head = head[:head.rfind(" ") + 1]
    return head.rstrip()


def _tail_size() -> int:
    return max(TAIL, _registered["longest"] + 1)


def _fit(t: str, limit: int, raw_cut: bool) -> str:
    """Steps 6 and 7: drop the tail of a text step 1 cut, then cut to `limit` with the ellipsis. A cut inside a token keeps the
    part only when cleaning it again changes nothing; otherwise the whole token goes."""
    if raw_cut:
        t = _back_to_token(t, max(0, len(t) - _tail_size()))
    if not raw_cut and len(t) <= limit:
        return t
    room = max(0, limit - 1)
    if len(t) <= room:
        return t + ELLIPSIS
    head = t[:room]
    if t[room] != " " and head[-1:] not in ("", " "):
        kept = head.rstrip() + ELLIPSIS
        if _steps(kept) == kept:
            return kept
        head = _back_to_token(t, room)
    return head.rstrip() + ELLIPSIS


def _pipeline(text, limit: int) -> str:
    s = text if isinstance(text, str) else str(text)
    raw_max = max(RAW_TIMES * limit, RAW_MIN)
    raw_cut = len(s) > raw_max
    if raw_cut:
        s = s[:raw_max]
    return _fit(_steps(s), limit, raw_cut)


def clean(text, limit: int = 300) -> str:
    """One line of text that is safe to show or write, made in this order:

    1. at most RAW_MAX characters are looked at (20 times `limit`, at least 4,096), and whether the text was longer is kept;
    2. format and control characters are dropped and every run of whitespace becomes one space;
    3. the package's guard replaces any remembered secret by `<secret>`, over the whole normalised text;
    4. order tags, batch ids and other ids are cut to their last 4 characters (`hide_order_ids`), and any run of 9 or more digits
       (an account number) or three or more groups of three or more digits joined by single spaces or dashes (a group the
       guard took counts as one) becomes `<number>` (a list of years in groups of three digits or more, such as
       `2024 2025 2026`, is masked too: a known cost); a number with a decimal point is a price and stays. Rev 2's rules and
       rev 3's are both run over the same text, each in its own order (tags, ids, digits, ids again), and a character either
       hides is hidden; an id either would have found before the guard, or with the text spaced as they gave it to the guard,
       is shortened wherever it stands;
    5. the guard again, over the result (a second line of defence that no known input needs: none is known on which it
       changes the output);
    6. if step 1 cut the text, its tail is dropped: at least the last TAIL (600) characters, and never less than the longest
       value registered from the trading file, back to the start of a token; every step before is one that never lengthens a
       text, so whatever came from the part near the cut is in that tail, and nothing that lay across the cut shows a part;
    7. only now the text is cut to `limit` with `…`; a cut inside a token keeps its part only when cleaning it again changes
       nothing, otherwise the whole token goes; a mask's own placeholder may be cut, a value never;
    8. cleaning is a fixed point: the result is cleaned once more (it is at most `limit` long, so this is cheap); when that
       changes it, the changed form is checked the same way, and a text that changes a third time is shown as `<held>`. No
       loop runs longer than that, and each step makes one pass over the text."""
    limit = max(1, int(limit))
    once = _pipeline(text, limit)
    twice = _pipeline(once, limit)
    if twice == once:
        return once
    thrice = _pipeline(twice, limit)
    if thrice == twice:
        return twice
    return HELD if limit >= len(HELD) else ELLIPSIS


def _key_once(text) -> str:
    return _KEY_CHARS.sub("_", clean(text, 80))


def _key_part(text) -> str:
    """A key: cleaned to 80 characters, then only letters, digits and `_.:-`. A key is its own key form: the form is made again
    from itself, and when that changes it, once more; a key that changes a third time is `_held_`."""
    once = _key_once(text)
    twice = _key_once(once)
    if twice == once:
        return once
    thrice = _key_once(twice)
    return twice if thrice == twice else _KEY_HELD


def _guard_masks(text) -> bool:
    """Whether the package's guard, on the whole text (not cut, and with format characters dropped and whitespace collapsed as
    well), masks any part of it."""
    s = str(text)
    return redact(s)[1] > 0 or redact(_normalise(s))[1] > 0


class SourceMissing(RuntimeError):
    """A file the page or the watch reads is not there."""


class CheckFailed(RuntimeError):
    """A check could not be asked; the message is already in plain words."""


def is_busy(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(w in str(exc).lower() for w in ("locked", "busy"))


def needs_recovery(exc: BaseException) -> bool:
    """A read-only connection that meets the journal a killed writer left cannot roll it back: sqlite says the database is
    read-only. Only the store's own program may recover it."""
    return isinstance(exc, sqlite3.OperationalError) and "readonly" in str(exc).lower().replace(" ", "")


def describe(exc: BaseException) -> str:
    """A failure in plain words, never more than a class and a short cleaned message."""
    if is_busy(exc):
        return "busy (another process holds the file); try again"
    if needs_recovery(exc):
        return "needs its own program to recover it (a writer was stopped part-way)"
    if isinstance(exc, (SourceMissing, CheckFailed)):
        return clean(exc, 200)
    return f"{type(exc).__name__}: {clean(exc, 160)}"


class _Waiting(sqlite3.Connection):
    """A read-only connection that waits for another process's write itself, by short sleeps it adds up, and gives up with
    sqlite's own "database is locked" once `budget` seconds of waiting are spent (in all, over every statement). Time spent
    computing is not waiting and is never cut off."""
    budget = WAIT_BUDGET_S
    waited = 0.0

    def execute(self, sql, *args):
        while True:
            try:
                return super().execute(sql, *args)
            except sqlite3.OperationalError as exc:
                if not is_busy(exc) or self.waited >= self.budget:
                    raise
                time.sleep(0.05)
                self.waited += 0.05


def open_readonly(path, timeout: float = WAIT_BUDGET_S, row_factory: bool = True) -> sqlite3.Connection:
    """A connection that can only read (`mode=ro`: the file is never written, rolled back or migrated) and waits at most
    `timeout` seconds in all for a writer. Raises SourceMissing for a missing file."""
    p = Path(path)
    if not p.is_file():
        raise SourceMissing(f"{p.name} is not there")
    conn = sqlite3.connect("file:" + urllib.parse.quote(str(p.resolve())) + "?mode=ro", uri=True, timeout=0, factory=_Waiting)
    conn.budget = float(timeout)
    if row_factory:
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def read_tail(path, size: int = 512 * 1024) -> list[str]:
    """The lines at the end of a text file (a log that only grows): the last `size` bytes, without a first line cut in two."""
    with Path(path).open("rb") as fh:
        end = fh.seek(0, 2)
        fh.seek(max(0, end - size))
        data = fh.read()
        cut = end > size
    lines = data.decode("utf-8", "replace").split("\n")
    if cut and lines:
        lines = lines[1:]
    return [ln.rstrip("\r") for ln in lines if ln.strip()]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------- what is asked

def exchange_sessions():
    """The exchange calendar the package already has (every New York Stock Exchange session)."""
    from valedia_trader.research_loop.strategy import _exchange_sessions
    return _exchange_sessions()


def last_trading_day(now: float, sessions) -> str:
    """The last exchange session that is over: the latest one before today's date in New York."""
    import pandas as pd
    today = pd.Timestamp(datetime.fromtimestamp(now, NEW_YORK).date())
    sess = sessions.tz_localize(None) if getattr(sessions, "tz", None) is not None else sessions
    earlier = sess[sess < today]
    if not len(earlier):
        raise CheckFailed("the exchange calendar has no session before today")
    return str(earlier[-1].date())


def launchctl_text() -> str:
    """What `launchctl list` prints. The one place the watch asks launchd; the program gets the reduced environment."""
    done = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15, env=child_env())
    if done.returncode != 0:
        raise OSError(f"launchctl list exited with code {done.returncode}")
    return done.stdout


def parse_jobs(text: str) -> dict[str, dict]:
    """`launchctl list` output as {label: {"pid": str, "status": str}} for the com.valedia.* jobs it reports."""
    jobs: dict[str, dict] = {}
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2].strip().startswith("com.valedia.") and parts[0] != "PID":
            jobs[parts[2].strip()] = {"pid": parts[0], "status": parts[1]}
    return jobs


def installed_jobs(agents_dir: Path) -> list[str]:
    """Labels of the com.valedia.* jobs that have a plist in the launch agents folder: the ones meant to be loaded."""
    try:
        return sorted(p.name[:-len(".plist")] for p in Path(agents_dir).glob("com.valedia.*.plist"))
    except OSError:
        return []


@dataclass
class Env:
    """Everything the checks look at, so a test can give each one: the repository, the clock (seconds since the epoch),
    the halt file, the folders of the logs and of the launch agents, and the programs and numbers that come from the
    machine (`launchctl list`, free disk space, the exchange calendar, the environment)."""
    root: Path
    now: float
    halt_file: Path | None
    logs_dir: Path
    launch_agents_dir: Path
    launchctl: Callable[[], str]
    free_bytes: Callable[[Path], int]
    sessions: Callable[[], object]
    environ: Mapping | None = None
    trading_file: Path | None = None            # default: the package's own place for it (`env_file_path`)
    _cache: dict = field(default_factory=dict, repr=False)

    @classmethod
    def default(cls, root=None) -> "Env":
        from valedia_trader.research_loop import REPO, risk_gate
        import time
        return cls(root=Path(root) if root else REPO, now=time.time(), halt_file=risk_gate.HALT_FILE,
                   logs_dir=Path.home() / "Library" / "Logs", launch_agents_dir=Path.home() / "Library" / "LaunchAgents",
                   launchctl=launchctl_text, free_bytes=lambda p: shutil.disk_usage(p).free, sessions=exchange_sessions)

    def launchctl_text(self) -> str:
        """`launchctl list`, asked once per run."""
        if "launchctl" not in self._cache:
            self._cache["launchctl"] = self.launchctl()
        return self._cache["launchctl"]

    @property
    def paths(self):
        from valedia_trader.research_loop import LoopPaths
        return LoopPaths(Path(self.root))

    def journal(self) -> tuple[list[dict], list[dict]]:
        """The journal entries of the last 30 days and the damaged lines among them; read once. Nothing is created."""
        if "journal" not in self._cache:
            import pandas as pd
            from valedia_trader.research_loop.paper import Journal
            folder = self.paths.journal
            if not folder.is_dir():
                self._cache["journal"] = ([], [])
            else:
                j = Journal(folder)
                since = pd.Timestamp(datetime.fromtimestamp(self.now, timezone.utc).date()) - pd.Timedelta(days=DAMAGE_WINDOW_DAYS)
                entries = j.read(since=since)
                self._cache["journal"] = (entries, [dict(x) for x in j.skipped_lines])
        return self._cache["journal"]


@dataclass(frozen=True)
class Alert:
    key: str
    level: str                        # "warning" or "critical"
    title: str
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _key_part(self.key))                   # a key is cleaned when it is made, always
        object.__setattr__(self, "title", clean(self.title, 120))
        object.__setattr__(self, "detail", clean(self.detail, 300))


class TradingFileError(RuntimeError):
    """The trading file exists and cannot be read. The message says so in plain words and holds nothing from the file."""


TRADING_FILE_MAX_BYTES, TRADING_FILE_MAX_SETTINGS = 64 * 1024, 200


def register_trading_file(env: Env | None = None) -> dict[str, str]:
    """Hand every value of the trading file to the package's output guard (the guard's own rule: `load_env` remembers what it
    finds worth remembering: keys, secrets, account numbers, recovery phrases), before anything is read or printed. The
    values are loaded into a private dictionary and returned (so a caller can read one setting); NOTHING is put into the
    process environment. A missing file is no reason to stop (an empty dictionary). A file that exists and cannot be read,
    that others can read, that is larger than 64 KB or holds more than 200 settings (it is not a trading file) or that is
    not UTF-8 text raises TradingFileError, in plain words and at once. Done once per Env."""
    if env is not None and "trading_values" in env._cache:
        return env._cache["trading_values"]
    path = Path((env.trading_file if env is not None and env.trading_file else None) or env_file_path()).expanduser()
    done = "nothing was done"
    values: dict[str, str] = {}
    try:
        if is_live_file(path):                         # T50: judged from names and metadata, before the file is opened
            raise TradingFileError("the file named as the trading file is the live key file (webull-live.env); it was not "
                                   f"opened and nothing was registered; {done}")
        if path.is_file():
            if path.stat().st_mode & 0o077:
                raise TradingFileError(f"the trading file is readable by others; it must be readable by its owner only (chmod 600); {done}")
            if path.stat().st_size > TRADING_FILE_MAX_BYTES:
                raise TradingFileError(f"the trading file is larger than 64 KB, so it is not a trading file; {done}")
            with path.open("rb") as fh:
                raw = fh.read(TRADING_FILE_MAX_BYTES + 1)
            if len(raw) > TRADING_FILE_MAX_BYTES:
                raise TradingFileError(f"the trading file is larger than 64 KB, so it is not a trading file; {done}")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise TradingFileError(f"the trading file is not valid UTF-8 text; {done}") from None
            if sum(1 for ln in text.splitlines() if "=" in ln and not ln.lstrip().startswith("#")) > TRADING_FILE_MAX_SETTINGS:
                raise TradingFileError(f"the trading file holds more than 200 settings, so it is not a trading file; {done}")
            try:
                load_env(path, environ=values)
            except LiveNameInReach as exc:             # T50: the loader refused the file; none of its values is registered
                raise TradingFileError(f"the trading file was refused: {exc}; nothing was registered; {done}") from None
            _registered["longest"] = max([_registered["longest"]] + [len(v) for v in values.values() if isinstance(v, str)])
    except TradingFileError:
        raise
    except Exception as exc:
        raise TradingFileError(f"the trading file exists but cannot be read ({type(exc).__name__}); {done}") from None
    if env is not None:
        env._cache["trading_values"] = values
    return values


class Sender(Protocol):
    def send(self, alert: Alert) -> None: ...


class NotifyError(RuntimeError):
    pass


def _osascript(argv: list[str]) -> subprocess.CompletedProcess:
    """The one place a notification is shown; the program gets the reduced environment."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=15, env=child_env())


class MacNotifier:
    """One macOS notification per alert. The text is an argument of the script (`item 2 of argv`), never part of its
    source, so nothing in an alert can be run as AppleScript."""
    SCRIPT = ("on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run")
    name = "mac"

    def send(self, alert: Alert) -> None:
        message = clean(f"{alert.title}: {alert.detail}", 200)
        if message.startswith("-"):
            message = " " + message                     # never read by osascript as an option
        argv = ["osascript"]
        for part in self.SCRIPT:
            argv += ["-e", part]
        argv += ["Valedia", message]
        done = _osascript(argv)
        if done.returncode != 0:
            raise NotifyError(f"osascript exited with code {done.returncode}")


# ---------------------------------------------------------------------------------------------- the checks

@dataclass
class CheckResult:
    alerts: list[Alert] = field(default_factory=list)
    held: list[str] = field(default_factory=list)          # keys that could not be asked this time: left as they are
    failures: list[str] = field(default_factory=list)


def check_switches(env: Env) -> CheckResult:
    from valedia_trader.research_loop import risk_gate
    out = CheckResult()
    if env.halt_file is not None and risk_gate.file_is_there(env.halt_file):
        out.alerts.append(Alert("switch:halt", "critical", "The operator halt file is set",
                                "All trading is stopped until the halt file is removed."))
    if risk_gate.file_is_there(env.paths.journal / "kill_switch.json"):
        out.alerts.append(Alert("switch:latch", "critical", "The drawdown latch is set",
                                "New risk is blocked until the latch is reset with reset-kill-switch."))
    return out


def open_order_ledger(path: Path, clock: Callable[[], float]):
    """An order ledger on a read-only connection, read by its own methods. The ledger's constructor writes (it takes the
    write lock and may create tables), so this does not call it."""
    from valedia_trader.execution_and_risk.orders import OrderLedger, unreadable_layout
    conn = open_readonly(path)
    try:
        why = unreadable_layout(conn)                     # T51: layouts 3 and 4 are read alike; nothing is upgraded
        if why is not None:
            raise CheckFailed(f"{path.name} is not an order ledger of a layout this code reads: {why}")
    except BaseException:
        conn.close()
        raise
    led = object.__new__(OrderLedger)
    led.not_sent, led.clock, led._path, led._db = (), clock, str(path), conn
    return led


def order_ledger_files(memory: Path) -> list[Path]:
    try:
        return sorted(Path(memory).glob("orders-*.sqlite"))
    except OSError:
        return []


def check_orders(env: Env) -> CheckResult:
    out = CheckResult()
    for path in order_ledger_files(env.paths.memory):
        key = f"orders:{_key_part(path.stem)}"
        try:
            led = open_order_ledger(path, lambda: env.now)
            try:
                reasons = led.blocked(LEDGER_MAX_AGE_SECONDS)
            finally:
                led.close()
        except Exception as exc:
            out.failures.append(f"order ledger {clean(path.stem, 60)}: {describe(exc)}")
            out.held.append(key)
            continue
        if reasons:
            more = f" (and {len(reasons) - 3} more)" if len(reasons) > 3 else ""
            out.alerts.append(Alert(key, "critical", f"The order ledger {path.stem} is blocked", "; ".join(reasons[:3]) + more))
    return out


PRACTICE_MODES = ("propose", "approve")


def is_practice(entry: dict) -> bool:
    """An order-step entry whose `mode` is exactly `propose` or `approve` is a practice run of the approval flow."""
    return "mode" in entry and isinstance(entry["mode"], str) and entry["mode"] in PRACTICE_MODES


def is_unattended(entry: dict) -> bool:
    """An order-step entry with no `mode` key at all is the unattended run."""
    return "mode" not in entry


def has_unknown_mode(entry: dict) -> bool:
    """A `mode` key that holds anything else (empty, null, a number, a boolean, a list, another letter case, blanks around a
    word, another word) is neither the unattended run nor a practice run."""
    return "mode" in entry and not is_practice(entry)


def last_step_summaries(entries: list[dict], practice: str | None = None) -> dict[str, dict]:
    """The last order-step summary of each venue (entries are in date order, each file in the order written): of the unattended
    run, that is of the summaries WITHOUT a `mode`; or, given `practice` ("propose" or "approve"), of that mode."""
    last: dict[str, dict] = {}
    for e in entries:
        if e.get("type") == "order_step" and e.get("event") == "summary" and \
                ((e.get("mode") == practice and is_practice(e)) if practice else is_unattended(e)):
            last[str(e.get("venue"))] = e
    return last


_ORDINARY_SESSION_STOPS = (
    re.compile(r"outside the regular session window \d\d:\d\d to \d\d:\d\d New York time \(it is \d\d:\d\d\)"),
    re.compile(r"\d{4}-\d\d-\d\d \([A-Za-z]+\) is not a trading day"),          # a weekend or a holiday, as the calendar says it
)


def is_outside_market_hours(summary: dict) -> bool:
    """A stop at stage "session" because the market is closed is ordinary: the stage, together with exactly one of the two
    reasons the order step gives for it (outside the regular session window; not a trading day). Anything else, at that stage
    or another (another run going, the calendar not loading or not answering), is not, whatever words its text holds."""
    reason = summary.get("reason")
    return (summary.get("status") == "stopped" and summary.get("stage") == "session" and isinstance(reason, str)
            and any(p.fullmatch(reason) for p in _ORDINARY_SESSION_STOPS))


STEP_STATUSES = ("done", "stopped")          # the words the order step writes as the status of a summary
DONE = "done"


def todays_step_summaries(env: Env) -> list[dict]:
    """The order-step summaries of today in New York, in the order written: from today's journal file only (a file dated after
    today is not read) and dated today themselves. Read once; nothing is created."""
    if "step_today" not in env._cache:
        from valedia_trader.research_loop.paper import Journal
        today = str(datetime.fromtimestamp(env.now, NEW_YORK).date())
        folder = env.paths.journal
        entries = Journal(folder).read(since=today, until=today) if folder.is_dir() else []
        env._cache["step_today"] = [e for e in entries if e.get("type") == "order_step" and e.get("event") == "summary"
                                    and str(e.get("day"))[:10] == today]
    return env._cache["step_today"]


def _has_mode_like_key(entry: dict) -> bool:
    """A key that reads as `mode` in another letter case or with blanks around it (`Mode`, `MODE`, ` mode`)."""
    return any(isinstance(k, str) and k != "mode" and k.strip().casefold() == "mode" for k in entry)


def is_shared(entry: dict) -> bool:
    """T51c S4e: a summary of a shared run (it carries a `shared` record): its stops are judged by `check_shared_stops`."""
    return isinstance(entry.get("shared"), dict)


def clears_a_stop(entry: dict) -> bool:
    """A summary that clears a stop of the unattended run: no `mode` key in any letter case, and a status that is exactly `done`."""
    return is_unattended(entry) and not _has_mode_like_key(entry) and isinstance(entry.get("status"), str) and entry["status"] == DONE


def check_order_step(env: Env) -> CheckResult:
    """For today in New York, per venue: the unattended run (a summary with no `mode` key) stopped, at a stage other than session or
    at stage session for a reason that is not one of the two ordinary ones, and no LATER unattended summary of today said `done`.
    A later stop at stage session, a status that is missing, of another type, in another letter case or padded, a summary
    with a key like `Mode`, and a summary in a journal file dated after today never clear it; the next day starts afresh. A
    summary of today whose status is not one of the order step's own words is a warning of its own, once a day."""
    out = CheckResult()
    today = str(datetime.fromtimestamp(env.now, NEW_YORK).date())
    todays = todays_step_summaries(env)
    stops: dict[str, dict] = {}
    for e in todays:
        if not is_unattended(e):
            continue
        venue = str(e.get("venue"))
        if e.get("status") == "stopped" and not is_outside_market_hours(e) and not is_shared(e):   # S4e: a shared stop has its own key
            stops[venue] = e
        elif clears_a_stop(e):
            stops.pop(venue, None)
    for venue, e in sorted(stops.items()):
        out.alerts.append(Alert(f"step-stopped:{_key_part(venue)}", "critical",
                                f"The order step stopped at stage {e.get('stage')}",
                                f"{e.get('day')} {clean(venue, 60)}: {e.get('reason') or 'no reason given'}"))
    unread = [e for e in todays if not (isinstance(e.get("status"), str) and e["status"] in STEP_STATUSES)]
    if unread:
        e = unread[-1]
        out.alerts.append(Alert(f"step-unreadable:{today}", "warning", "An order-step summary could not be read",
                                f"{today} {clean(e.get('venue'), 60)}: status {clean(repr(e.get('status')), 40)}, which is not "
                                f"one of the order step's own words ({', '.join(STEP_STATUSES)})"))
    return out


def check_approval_refused(env: Env) -> CheckResult:
    """An approval that was refused (mode approve, stopped at stage ticket) today in New York: a warning of its own, once a day."""
    out = CheckResult()
    today = str(datetime.fromtimestamp(env.now, NEW_YORK).date())
    entries, _ = env.journal()
    refused = [e for e in entries if e.get("type") == "order_step" and e.get("event") == "summary" and e.get("mode") == "approve"
               and e.get("status") == "stopped" and e.get("stage") == "ticket" and str(e.get("day"))[:10] == today]
    if refused:
        e = refused[-1]
        out.alerts.append(Alert(f"approval:refused:{today}", "warning", "An approval was refused",
                                f"{today} {clean(e.get('venue'), 60)}: {e.get('reason') or 'no reason given'}"))
    return out


def check_modes(env: Env) -> CheckResult:
    """Today in New York: a summary with an unknown mode (one warning), and a propose or approve run that stopped at a stage other
    than ticket (a refused approval has its own warning) or session (a closed market): one warning for each mode."""
    out = CheckResult()
    today = str(datetime.fromtimestamp(env.now, NEW_YORK).date())
    entries, _ = env.journal()
    todays = [e for e in entries if e.get("type") == "order_step" and e.get("event") == "summary" and str(e.get("day"))[:10] == today]
    unknown = [e for e in todays if has_unknown_mode(e)]
    if unknown:
        out.alerts.append(Alert(f"mode:unknown:{today}", "warning", "An order-step summary with an unknown mode",
                                f"{today} {clean(unknown[-1].get('venue'), 60)}: mode {clean(repr(unknown[-1].get('mode')), 40)}"))
    for mode in PRACTICE_MODES:
        stops = [e for e in todays if e.get("mode") == mode and is_practice(e) and e.get("status") == "stopped"
                 and e.get("stage") not in ("ticket", "session") and not is_shared(e)]
        if stops:
            e = stops[-1]
            out.alerts.append(Alert(f"practice-stopped:{mode}:{today}", "warning", f"A {mode} run stopped at stage {e.get('stage')}",
                                    f"{today} {clean(e.get('venue'), 60)}: {e.get('reason') or 'no reason given'}"))
    return out


_SHORT_SYMBOL = re.compile(r"[A-Za-z0-9./-]{1,24}")       # the order step's own plain symbol; anything else is not named
SHORT_FUNDS_SHOWN = 2


def _short_figure(x) -> str | None:
    return f"{x:g}" if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf") else None


SHARED_STATE_STAGES_SKIPPED = ("ticket", "session")        # an attended run's stages that are no stop (as `practice-stopped` judged)


def shared_class(entry: dict) -> str:
    """T51c S4e: the class of a shared run's summary: `attended` when it has a `mode` key at all (propose, approve, "both", anything
    else), `unattended` otherwise (a key like `Mode` is another key; the summary is the unattended run's)."""
    return "attended" if "mode" in entry else "unattended"


def shared_states(env: Env) -> dict[tuple[str, str], tuple[str, dict]]:
    """The present state of each shared book, per (venue, class), from the journal's order-step summaries of the last 30 days that
    carry a `shared` record (none dated after today in New York): ("stopped", the summary) when the latest relevant summary is a stop,
    ("clean", ...) when it is `done` with no shortfall, ("shortfall", ...) when it is `done` with `shared.shortfalls` a non-empty
    record. Ordinary skips are not relevant (the unattended run's closed market; stages ticket and session of an attended run), nor is
    a status that is neither `stopped` nor `done`: they leave the state as it was."""
    today = str(datetime.fromtimestamp(env.now, NEW_YORK).date())
    states: dict[tuple[str, str], tuple[str, dict]] = {}
    entries, _ = env.journal()
    for e in entries:
        if not (e.get("type") == "order_step" and e.get("event") == "summary" and isinstance(e.get("shared"), dict)
                and isinstance(e.get("status"), str)) or str(e.get("day"))[:10] > today:
            continue
        cls, venue = shared_class(e), str(e.get("venue"))
        if e["status"] == "stopped":
            if (cls == "unattended" and is_outside_market_hours(e)) or (cls == "attended" and e.get("stage") in SHARED_STATE_STAGES_SKIPPED):
                continue
            states[(venue, cls)] = ("stopped", e)
        elif e["status"] == DONE:
            shorts = e["shared"].get("shortfalls")
            states[(venue, cls)] = ("shortfall" if isinstance(shorts, dict) and shorts else "clean", e)
    return states


SHORT_NAMES_CHARS = 64                                     # the names listed before "and K more" (the detail is cut at 300)


def _short_names(shorts: dict) -> str:
    """The count of short funds and the fund names that fit SHORT_NAMES_CHARS, then ", and K more" for the rest ("5 funds short:
    GLD, IWM, QQQ, SPY, TLT"); a name that is not a plain symbol is counted and not named."""
    names = [sym for sym in sorted(shorts, key=str) if isinstance(sym, str) and _SHORT_SYMBOL.fullmatch(sym)]
    odd = len(shorts) - len(names)
    shown, used = [], 0
    for n in names:
        if used + len(n) + 2 > SHORT_NAMES_CHARS and shown:
            break
        shown.append(n)
        used += len(n) + 2
    text = f"{len(shorts)} fund{'' if len(shorts) == 1 else 's'} short" + (": " + ", ".join(shown) if shown else "")
    if len(names) > len(shown):
        text += f", and {len(names) - len(shown)} more"
    return text + (f" ({odd} whose name is not plain)" if odd else "")


def _short_parts(shorts: dict) -> list[str]:
    """The shortfalls of a summary's `shared.shortfalls`, one phrase a fund: what the book counts, what the account holds and how much
    is short. A figure that is not a finite number is said as unknown; a name that is not a plain symbol is not given."""
    parts = []
    for sym in sorted(shorts, key=str):
        d = shorts[sym]
        name = sym if isinstance(sym, str) and _SHORT_SYMBOL.fullmatch(sym) else "a fund whose name is not plain"
        d = d if isinstance(d, dict) else {}
        book, held, short = (_short_figure(d.get(k)) or "unknown" for k in ("book", "venue", "short"))
        parts.append(f"{name}: counted {book}, held {held} ({short} short)")
    return parts


def check_shared_shortfall(env: Env) -> CheckResult:
    """T51c S4e (R24, R33): per venue and class (see `shared_states`), a SHARED run that ended `done` whose last reconcile found a
    shortfall (`shared.shortfalls` a non-empty record) is `shared-shortfall:<venue>:<class>` (a warning), open while that is the
    latest relevant summary of the venue and class, whatever the day: a later `done` with none, or a stop, closes it (a stop has its
    own alert, `shared-stopped`). It is no stop, so nothing else is raised for the run. The words come from the summary's figures
    only, never an order's tag or an account; the count of short funds and the fund names come first (the alert's detail is cut at
    300 characters), the figures after."""
    out = CheckResult()
    for (venue, cls), (state, e) in sorted(shared_states(env).items()):
        if state != "shortfall":
            continue
        day, shorts = str(e.get("day"))[:10], e["shared"]["shortfalls"]
        head = (f"{day} {clean(venue, 60)}: found by the last reconcile (stage after). {_short_names(shorts)}. The owner sold shares "
                "the book counts as its own, or a split; clear it with the shares or a units_out or split entry.")
        figures = ""
        for part in _short_parts(shorts)[:SHORT_FUNDS_SHOWN]:            # the figures last, as many as fit the 300 characters
            if len(head) + len(figures) + len(part) + 3 > 300:
                break
            figures += ("; " if figures else " ") + part
        out.alerts.append(Alert(f"shared-shortfall:{_key_part(venue)}:{cls}", "warning",
                                "The account holds less than the book counts as its own", head + figures))
    return out


def check_shared_stops(env: Env) -> CheckResult:
    """T51c S4e (repair 2): per venue and class (see `shared_states`), a SHARED run whose latest relevant summary is a stop is
    `shared-stopped:<venue>:<class>:<stage>`: critical for the unattended class, a warning for the attended one (propose, approve and
    any other `mode`). It is the book's present state, not the day's: a stop nothing has fixed stays open over days, a stop first
    seen after midnight is raised, a later `done` with no shortfall of the same venue and class (or a stop at another stage) closes
    it, and a run of the other class never does. What counts as a stop is what `step-stopped` and `practice-stopped` judged (the
    unattended run: any stage but an ordinary closed market; propose, approve and the rest: any stage but ticket and session), and a
    shared summary is judged here whatever other keys it carries, so none falls between the checks."""
    out = CheckResult()
    for (venue, cls), (state, e) in sorted(shared_states(env).items()):
        if state != "stopped":
            continue
        stage, day = e.get("stage"), str(e.get("day"))[:10]
        who = "The unattended shared run" if cls == "unattended" else "A propose or approve shared run"
        out.alerts.append(Alert(f"shared-stopped:{_key_part(venue)}:{cls}:{_key_part(str(stage))}",
                                "critical" if cls == "unattended" else "warning", f"{who} stopped at stage {stage}",
                                f"{day} {clean(venue, 60)}: {e.get('reason') or 'no reason given'}"))
    return out


def check_jobs(env: Env) -> CheckResult:
    out = CheckResult()
    try:
        loaded = parse_jobs(env.launchctl_text())
    except Exception as exc:
        raise CheckFailed(f"could not run launchctl list: {describe(exc)}") from exc
    for label in sorted(set(installed_jobs(env.launch_agents_dir)) | set(loaded)):
        key = f"job:{_key_part(label)}"
        job = loaded.get(label)
        if job is None:
            out.alerts.append(Alert(key, "critical", f"The scheduled job {label} is not loaded",
                                    "It has a plist in the launch agents folder but launchd does not list it."))
        elif job["status"] not in ("0", "-"):
            out.alerts.append(Alert(key, "warning", f"The scheduled job {label} last exited with code {job['status']}",
                                    f"launchctl list reports exit code {job['status']}."))
    return out


def check_collector(env: Env) -> CheckResult:
    out = CheckResult()
    path = env.paths.memory / "orderbook_loop.sqlite"
    try:
        conn = open_readonly(path)
    except SourceMissing:
        out.alerts.append(Alert("collector:heartbeat", "warning", "The order-book collector has no record",
                                "There is no order-book store, so no heartbeat on record."))
        return out
    try:
        newest = conn.execute("SELECT MAX(ts) FROM heartbeats").fetchone()[0]
    finally:
        conn.close()
    if newest is None:
        out.alerts.append(Alert("collector:heartbeat", "warning", "The order-book collector has no heartbeat",
                                "The store holds no heartbeat on record."))
    elif env.now - newest > HEARTBEAT_MAX_AGE_S:
        out.alerts.append(Alert("collector:heartbeat", "warning", "The order-book collector has stopped",
                                f"The newest heartbeat is from {iso(newest)}, {(env.now - newest) / 60:.0f} minutes ago."))
    return out


def parse_picks_log(lines: list[str]) -> tuple[float | None, str | None]:
    """(time of the last "end" line, the first line of the last run that says failed or None)."""
    last_end, start_at = None, 0
    for i, line in enumerate(lines):
        m = _END_LINE.match(line)
        if m:
            last_end = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        if _START_LINE.match(line):
            start_at = i
    failed = next((ln for ln in lines[start_at:] if _FAILED.search(ln)), None)
    return last_end, failed


def check_picks(env: Env) -> CheckResult:
    out = CheckResult()
    log = Path(env.logs_dir) / PICKS_LOG
    if not log.is_file():
        out.alerts.append(Alert("picks:no-end", "warning", "The hourly pick-list log has no end line",
                                f"The log {PICKS_LOG} is missing."))
        return out
    last_end, failed = parse_picks_log(read_tail(log))
    if last_end is None or env.now - last_end > PICKS_END_MAX_AGE_S:
        seen = f"the last end line is from {iso(last_end)}" if last_end is not None else "the log has no end line"
        out.alerts.append(Alert("picks:no-end", "warning", "The hourly pick-list job has not finished in 3 hours", seen + "."))
    if failed is not None:
        out.alerts.append(Alert("picks:failed", "warning", "The last hourly pick-list run said failed", failed))
    return out


def _sessions_naive(env: Env):
    sessions = env.sessions()
    return sessions.tz_localize(None) if getattr(sessions, "tz", None) is not None else sessions


def _loaded_jobs(env: Env) -> dict[str, dict]:
    try:
        return parse_jobs(env.launchctl_text())
    except Exception as exc:
        raise CheckFailed(f"could not run launchctl list: {describe(exc)}") from exc


def check_orderstep_ran(env: Env) -> CheckResult:
    """On a New York trading day after 10:30, a loaded order-step job has journaled a run for today."""
    import pandas as pd
    out = CheckResult()
    if ORDERSTEP_JOB not in _loaded_jobs(env):
        return out
    ny = datetime.fromtimestamp(env.now, NEW_YORK)
    if ny <= ny.replace(hour=ORDERSTEP_AFTER[0], minute=ORDERSTEP_AFTER[1], second=0, microsecond=0) \
            or pd.Timestamp(ny.date()) not in _sessions_naive(env):
        return out
    today = str(ny.date())
    entries, _ = env.journal()
    if not any(e.get("type") == "order_step" and is_unattended(e) and str(e.get("day"))[:10] == today for e in entries):
        out.alerts.append(Alert("orderstep:no-run", "warning", f"The order step has not run today ({today})",
                                f"{ORDERSTEP_JOB} is loaded and the journal holds no order-step entry for {today}."))
    return out


def check_weekly_ran(env: Env) -> CheckResult:
    """A loaded weekly research loop has a run line in its log within the last 8 days."""
    out = CheckResult()
    if WEEKLY_JOB not in _loaded_jobs(env):
        return out
    log = Path(env.logs_dir) / WEEKLY_LOG
    last = None
    if log.is_file():
        for line in read_tail(log, LOG_TAIL_BYTES):
            m = _WEEKLY_RUN.match(line)
            if m:
                try:
                    last = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))          # the job writes its local time
                except (ValueError, OverflowError):
                    pass
    if last is None:                                       # never ran: counted from when the job was installed, if that is known
        plist = Path(env.launch_agents_dir) / f"{WEEKLY_JOB}.plist"
        try:
            installed = plist.stat().st_mtime
        except OSError:
            installed = None
        if installed is not None and env.now - installed <= WEEKLY_MAX_AGE_S:
            return out
        out.alerts.append(Alert("weekly:no-run", "warning", "The weekly research loop has never run",
                                f"{WEEKLY_JOB} is loaded and its log {WEEKLY_LOG} holds no run line."))
    elif env.now - last > WEEKLY_MAX_AGE_S:
        out.alerts.append(Alert("weekly:no-run", "warning", "The weekly research loop has not run for more than 8 days",
                                f"The last run line in {WEEKLY_LOG} is {(env.now - last) / 86400:.1f} days old."))
    return out


def check_journal_damage(env: Env) -> CheckResult:
    from valedia_trader.research_loop.paper import describe_skipped
    out = CheckResult()
    _, skipped = env.journal()
    if skipped:
        out.alerts.append(Alert("journal:damaged", "warning",
                                f"{len(skipped)} damaged journal line{'s' if len(skipped) != 1 else ''} in the last {DAMAGE_WINDOW_DAYS} days",
                                describe_skipped(skipped)))
    return out


def check_paper_step(env: Env) -> CheckResult:
    out = CheckResult()
    day = last_trading_day(env.now, env.sessions())
    entries, _ = env.journal()
    if not any(e.get("type") == "summary" and e.get("day") == day for e in entries):
        out.alerts.append(Alert("paper:no-entry", "warning", f"The paper step wrote no summary for {day}",
                                f"{day} is the last trading day and the journal has no day summary for it."))
    return out


def check_disk(env: Env) -> CheckResult:
    out = CheckResult()
    free = env.free_bytes(Path(env.root))
    if free < DISK_MIN_BYTES:
        out.alerts.append(Alert("disk:low", "warning", "Less than 5 GB free on the repository's volume",
                                f"{free / 1e9:.1f} GB free."))
    return out


# (what it is called, the key prefixes it owns, the check)
CHECKS: list[tuple[str, tuple[str, ...], Callable[[Env], CheckResult]]] = [
    ("switches", ("switch:",), check_switches),
    ("order ledgers", ("orders:",), check_orders),
    ("order step", ("step-stopped:", "step-unreadable:"), check_order_step),
    ("approval", ("approval:",), check_approval_refused),
    ("order-step modes", ("mode:", "practice-stopped:"), check_modes),
    ("shared book", ("shared-shortfall:",), check_shared_shortfall),
    ("shared stops", ("shared-stopped:",), check_shared_stops),
    ("scheduled jobs", ("job:",), check_jobs),
    ("order-book collector", ("collector:",), check_collector),
    ("pick-list log", ("picks:",), check_picks),
    ("order-step run", ("orderstep:",), check_orderstep_ran),
    ("weekly research loop", ("weekly:",), check_weekly_ran),
    ("journal damage", ("journal:",), check_journal_damage),
    ("paper step", ("paper:",), check_paper_step),
    ("disk space", ("disk:",), check_disk),
]


# When the check named fails, these prefixes are held too: the shared stops replaced `step-stopped` and `practice-stopped` for shared runs,
# so an alert open under an old key is cleared only in a watch where its replacement was asked (T51c S4e, repair 2).
HELD_WITH = {"shared stops": ("step-stopped:", "practice-stopped:")}


def run_checks(env: Env) -> tuple[list[Alert], list[str], list[str], list[str]]:
    """(the alerts that are true now, keys held, key prefixes held, failures). A check that raises holds its whole prefix."""
    alerts, held, held_prefixes, failures = [], [], [], []
    for name, prefixes, fn in CHECKS:
        try:
            r = fn(env)
        except Exception as exc:
            failures.append(f"{name}: {describe(exc)}")
            held_prefixes += prefixes + HELD_WITH.get(name, ())
            continue
        alerts += r.alerts
        held += r.held
        failures += r.failures
    return alerts, held, held_prefixes, failures


# ---------------------------------------------------------------------------------------------- the alert file

def alerts_file(root) -> Path:
    return Path(root) / "artifacts" / "owner" / "alerts.jsonl"


def _cleaned_line(e: dict) -> dict:
    """A line read back from the tool's own files, every field cleaned like a new text: nothing read back is trusted. A key
    that changes under cleaning is its cleaned form from here on and is never written back as it was."""
    out = {k: clean(v, 400) for k, v in e.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    out["key"] = _key_part(e.get("key"))
    out["withheld"] = _guard_masks(e.get("key"))                  # the key held a secret: the guard, on the whole key, masks a part
    out["level"] = clean(e.get("level"), 20)
    out["title"], out["detail"], out["time"] = clean(e.get("title", ""), 120), clean(e.get("detail", ""), 300), clean(e.get("time", ""), 40)
    return out


def _replay(data: bytes) -> tuple[dict[str, dict], int, int]:
    """({key: its last line, cleaned} for the keys whose last line is not a cleared line, the number of lines that are not
    alert lines, the number of old lines withheld: those whose key held a secret clean to the guard's mask, can match no check
    and so could never be cleared; they are not alerts, open or cleared)."""
    open_map: dict[str, dict] = {}
    damaged = withheld = 0
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            e = None
        if not isinstance(e, dict) or not isinstance(e.get("key"), str) or not isinstance(e.get("level"), str):
            damaged += 1
            continue
        e = _cleaned_line(e)
        if e.pop("withheld"):
            withheld += 1
            continue
        if e["level"] == CLEARED:
            open_map.pop(e["key"], None)
        else:
            open_map[e["key"]] = e
    return open_map, damaged, withheld


def read_alert_file(path) -> tuple[list[dict], int]:
    """(the alerts open now, oldest first; the number of old lines withheld) from the file; nothing when there is no file.
    Raises OSError if it cannot be read."""
    p = Path(path)
    if not p.exists():
        return [], 0
    if not p.is_file():
        raise IsADirectoryError(f"{p.name} is not a file")
    open_map, _, withheld = _replay(p.read_bytes())
    return list(open_map.values()), withheld


def open_alerts(path) -> list[dict]:
    """The alerts open now, oldest first, from the file; none when there is no file. Raises OSError if it cannot be read."""
    return read_alert_file(path)[0]


def withheld_note(n: int) -> str:
    return (f"{n} old line{'s' if n != 1 else ''} in the alert file {'were' if n != 1 else 'was'} withheld: the key held a secret, "
            "so the line can match no check; it is not an open alert") if n else ""


def notifications_file(root) -> Path:
    return Path(root) / "artifacts" / "owner" / "notifications.jsonl"


def read_notifications(path) -> dict[tuple, dict]:
    """{(sender, alert key, alert time): {"shown": bool, "tries": n}} from the notification record, every field cleaned like a
    new text. A try is a "trying" line written before the send; a "shown" or "failed" line is its answer (an answer with no
    "trying" line before it, from an earlier version, is a try of its own). A missing file is an empty record and a line that
    is not a record is skipped."""
    out: dict[tuple, dict] = {}
    p = Path(path)
    if not p.is_file():
        return out
    waiting: set = set()
    for raw in p.read_bytes().split(b"\n"):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("sender", "key", "since", "result")):
            ident = (clean(e["sender"], 40), _key_part(e["key"]), clean(e["since"], 40))
            st = out.setdefault(ident, {"shown": False, "tries": 0})
            if e["result"] == "trying":
                st["tries"] += 1
                waiting.add(ident)
                continue
            if ident not in waiting:
                st["tries"] += 1
            waiting.discard(ident)
            st["shown"] = st["shown"] or e["result"] == "shown"
    return out


def notification_note(states: dict, row: dict) -> str:
    """Whether an open alert was shown, for `list` and the page: shown, not tried, or not shown after how many tries."""
    mine = [st for (_, key, since), st in states.items() if key == row.get("key") and since == row.get("time")]
    if any(st["shown"] for st in mine):
        return "shown"
    tries = max((st["tries"] for st in mine), default=0)
    if not tries:
        return "not shown yet"
    return (f"not shown: tried {tries} times and is not tried again" if tries >= NOTIFY_MAX_TRIES
            else f"not shown yet ({tries} {'try' if tries == 1 else 'tries'} so far)")


@dataclass
class WatchResult:
    raised: list[Alert]
    cleared: list[Alert]
    open: list[dict]                   # open after this run
    failures: list[str]
    shown: list[Alert] = field(default_factory=list)       # given to a sender and acknowledged, this run
    withheld: int = 0                                      # old lines of the alert file that are not alerts (see `_replay`)


def watch(env: Env, senders=(), path=None) -> WatchResult:
    """Run every check once, write the new alerts and the cleared ones, then give each new alert to each sender."""
    path = Path(path) if path else alerts_file(env.root)
    register_trading_file(env)
    produced, held, held_prefixes, failures = run_checks(env)
    now = iso(env.now)
    by_key = {}
    for a in produced:                                     # two checks, or two labels, that clean to one key are one alert
        by_key.setdefault(a.key, a)
    produced = list(by_key.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    raised, cleared = [], []
    with path.open("a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)                     # two watches at once cannot both raise the same alert
        fh.seek(0)
        data = fh.read()
        open_map, _, withheld = _replay(data)
        raised = [a for a in produced if a.key not in open_map]
        gone = [e for k, e in open_map.items()
                if k not in by_key and k not in held and not any(k.startswith(p) for p in held_prefixes)
                and any(k.startswith(p) for _, prefixes, _ in CHECKS for p in prefixes)]
        for e in gone:
            cleared.append(Alert(e["key"], CLEARED, "Cleared: " + str(e.get("title", "")), "The condition is no longer true."))
        lines = [json.dumps({"time": now, "level": a.level, "key": a.key, "title": a.title, "detail": a.detail}) for a in raised]
        lines += [json.dumps({"time": now, "level": CLEARED, "key": a.key, "title": a.title, "detail": a.detail}) for a in cleared]
        if lines:
            text = ("\n" if data and not data.endswith(b"\n") else "") + "\n".join(lines) + "\n"
            fh.write(text.encode("utf-8"))                 # one write: a kill leaves at most one torn line
            fh.flush()
        for a in cleared:
            open_map.pop(a.key, None)
        for a in raised:
            open_map[a.key] = {"time": now, "level": a.level, "key": a.key, "title": a.title, "detail": a.detail}
        shown = _notify(open_map, senders, notifications_file(env.root) if path == alerts_file(env.root) else path.with_name("notifications.jsonl"),
                        now, failures)
    return WatchResult(raised, cleared, list(open_map.values()), failures, shown, withheld)


def _record(path: Path, row: dict) -> None:
    with path.open("ab") as fh:
        fh.write((json.dumps(row) + "\n").encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


def _notify(open_map: dict, senders, record: Path, now: str, failures: list[str]) -> list[Alert]:
    """Give each open alert that a sender has not yet acknowledged to that sender (new ones and old ones alike), at most
    NOTIFY_MAX_TRIES tries in all. A try is written to the record after the sender answered: an alert is shown only once its
    `send` has returned, so one that failed, or whose run was killed before the answer, is tried again next time. Runs inside
    the alert file's lock, so two runs cannot show the same alert twice."""
    shown: list[Alert] = []
    if not senders:
        return shown
    states = read_notifications(record)
    for e in list(open_map.values()):
        alert = Alert(e["key"], e["level"], e.get("title", ""), e.get("detail", ""))
        for sender in senders:
            name = str(getattr(sender, "name", type(sender).__name__))
            st = states.get((clean(name, 40), alert.key, clean(e["time"], 40)), {"shown": False, "tries": 0})
            if st["shown"] or st["tries"] >= NOTIFY_MAX_TRIES:
                continue
            ident = {"sender": clean(name, 40), "key": alert.key, "since": clean(e["time"], 40)}
            _record(record, {"time": now, **ident, "result": "trying"})        # on disk before the send: a kill cannot un-count it
            try:
                sender.send(alert)
                result = "shown"
            except Exception as exc:
                failures.append(f"notification for {alert.key}: {describe(exc)}")
                result = "failed"
            _record(record, {"time": now, **ident, "result": result})
            if result == "shown":
                shown.append(alert)
    return shown


# ---------------------------------------------------------------------------------------------- the command line

def _show(rows: list[dict], out, states: dict | None = None, withheld: int = 0) -> None:
    if not rows:
        print("no open alerts", file=out)
    if withheld:
        print(withheld_note(withheld), file=out)
    for e in rows:
        note = ""
        if states:
            n = notification_note(states, e)
            note = "" if n == "shown" else f"  [{n}]"
        print(f"{clean(e.get('level'), 20):<9} {clean(e.get('key'), 80)}  {clean(e.get('title', ''), 120)} - "
              f"{clean(e.get('detail', ''), 300)}  (since {clean(e.get('time', '?'), 40)}){note}", file=out)


def main(argv=None, env: Env | None = None, out=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.core.alerts", description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=None, help="the repository (default: this one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="run the checks once and write the new alerts")
    w.add_argument("--notify", action="store_true", help="show one macOS notification for each new alert")
    sub.add_parser("list", help="print the open alerts")
    args = ap.parse_args(argv)
    env = env or Env.default(args.root)
    try:
        register_trading_file(env)                         # before anything is read or printed
    except TradingFileError as exc:
        print(f"the watch could not start: {exc}", file=sys.stderr)
        return 2
    out = out or sys.stdout                                # bound only now: registering puts the output guard on the streams
    try:
        if args.cmd == "list":
            rows, withheld = read_alert_file(alerts_file(env.root))
            _show(rows, out, read_notifications(notifications_file(env.root)), withheld)
            return 1 if rows else 0
        result = watch(env, senders=[MacNotifier()] if args.notify else [])
    except Exception as exc:
        print(f"the watch failed: {describe(exc)}", file=sys.stderr)
        return 2
    for a in result.raised:
        print(f"raised   {a.level:<8} {a.key}  {a.title} - {a.detail}", file=out)
    for a in result.cleared:
        print(f"cleared  {a.key}", file=out)
    _show(result.open, out, read_notifications(notifications_file(env.root)), result.withheld)
    for failure in result.failures:
        print(f"the watch could not check: {failure}", file=sys.stderr)
    return 2 if result.failures else (1 if result.open else 0)


if __name__ == "__main__":
    sys.exit(main())
