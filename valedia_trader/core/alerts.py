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
    step-stopped:<venue>        the last order-step summary of the last day says stopped, with its stage and reason
                                (a stop at stage "session" for a closed market or a holiday is ordinary: no alert)
    approval:refused:<day>      an approval was refused today (a summary with mode approve, stopped at stage ticket): a warning
                                of its own, once a New York day. Summaries with a `mode` (propose, approve) are practice
                                runs of the approval flow: the conditions about the unattended run look only at those without
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

Everything shown or written passes `clean`: format and control characters are dropped, the package's output guard
replaces a remembered secret by `<secret>`, an order id is cut to its last 4 characters (a ledger tag `os-20261113-143501-
a1b2c3-XLE` becomes `…b2c3-XLE`), and any run of 9 or more digits (an account number) becomes `<number>`. Before it reads
or prints anything the command registers every value of the trading file with that guard (`register_trading_file`), into a
private dictionary: nothing is put into the process environment. A trading file that exists and cannot be read is exit 2
with nothing written; a missing one is no reason to stop; so is one over 64 KB or 200 settings, or not UTF-8 text, which is
refused at once. Nothing read back from `alerts.jsonl` or `notifications.jsonl` is trusted either: every field of every line
passes the same cleaning before it is written, printed, shown or sent, and a key that changes under cleaning is its cleaned
form from then on. The output guard is put on the streams before the command binds them to print.

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

from valedia_trader.core.secrets import child_env, env_file_path, load_env, redact

__all__ = ["Alert", "Env", "Sender", "MacNotifier", "WatchResult", "watch", "open_alerts", "alerts_file", "main", "clean",
           "open_readonly", "describe", "SourceMissing", "CHECKS", "last_trading_day", "exchange_sessions", "launchctl_text",
           "parse_jobs", "read_tail", "PICKS_LOG", "is_outside_market_hours", "register_trading_file", "TradingFileError",
           "hide_order_ids", "notifications_file", "read_notifications", "notification_note"]

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
_GROUPED_DIGITS = re.compile(r"(?<!\d)\d{4}(?:[ -]\d{4}){2,}(?!\d)")          # card-style: 3 or more groups of exactly four


# ---------------------------------------------------------------------------------------------- text and files

_RANDOM = r"[0-9A-Fa-f]{6}"
_FUND = r"[A-Z0-9]+(?:[.\-][A-Z0-9]+)*"
_OUR_TAG = re.compile(rf"(?<![A-Za-z0-9])os-\d{{8}}-\d{{6}}-({_RANDOM})(?:-({_FUND}))?(?![A-Za-z0-9])")
_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9_.\-])[A-Za-z0-9_\-]+(?![A-Za-z0-9_\-])")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")                # clientOrderId reads as client Order Id
_ID_KEYWORD = re.compile(r"(?i)(?<![a-z])(?:orders?|ids?|refs?|tags?|client)(?![a-z])")
_CLAUSE_END = re.compile(r"(?:;|\.\s)")
_DATEISH = re.compile(r"\d{4}-\d\d-\d\d(?:T\d\d)?|\d{8}(?:T\d{0,6}Z?)?|\d{8}-\d{6}")
ID_MIN = 12


def _is_id(tok: str) -> bool:
    """A run of 12 or more letters and digits (`_` and `-` may join them) holding a digit: not a date or time, not a Kalshi ticker."""
    return (len(tok) >= ID_MIN and any(c.isdigit() for c in tok) and not tok.isdigit() and not _DATEISH.fullmatch(tok)
            and not (tok.startswith("KX") and tok == tok.upper()))


def hide_order_ids(text: str) -> str:
    """An order id is shown by its last 4 characters only. Our own ledger tag (`os-20261113-143501-a1b2c3-XLE`) and batch id
    (`os-20261113-143501-a1b2c3`) are shortened wherever they stand (`…b2c3-XLE`, `…b2c3`: the last 4 of the random part, then
    the fund). Any other id, a run of 12 or more letters and digits with a digit in it and no dot (`_` and `-` may join its
    parts), that follows one of the words order, orders, id, ids, ref, tag or client anywhere in the same clause (lists and
    `id is X` included) becomes `…` and its last 4 characters. Dates and times, numbers with a decimal point, file names, version
    strings, Kalshi tickers (upper case, beginning KX), funds, prices and counts are never shortened."""
    t = _OUR_TAG.sub(lambda m: "…" + m.group(1)[-4:] + (f"-{m.group(2)}" if m.group(2) else ""), text)
    out, last = [], 0
    for m in _ID_TOKEN.finditer(t):
        tok = m.group(0)
        if not _is_id(tok) or t[max(0, m.start() - 1):m.start()] == "…":
            continue
        if m.end() < len(t) and t[m.end()] == "." and m.end() + 1 < len(t) and t[m.end() + 1].isalnum():
            continue                                                     # inside a dotted name: a file, a version, a host
        starts = [c.end() for c in _CLAUSE_END.finditer(t, 0, m.start())]
        clause = _CAMEL.sub(" ", t[(starts[-1] if starts else 0):m.start()])
        if _ID_KEYWORD.search(clause):
            out += [t[last:m.start()], "…" + tok[-4:]]
            last = m.end()
    return "".join(out) + t[last:]


def _unseen_out(text: str) -> str:
    """Format characters (Cf: direction overrides and marks, zero-width characters) and control characters (Cc) are dropped,
    and a line break, tab or separator is a space: nothing in a text can reorder or hide what is around it."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch in "\n\r\t" or cat in ("Zl", "Zp"):
            out.append(" ")
        elif cat not in ("Cf", "Cc", "Cs"):
            out.append(ch)
    return "".join(out)


def clean(text, limit: int = 300) -> str:
    """One line of text that is safe to show or write: format and control characters dropped, a remembered secret replaced by
    the package's own guard, an order id cut to its last 4 characters, any run of 9 or more digits (an account number) or
    digit groups written together replaced, and no more than `limit` characters."""
    t, _ = redact(_unseen_out(str(text)))
    t = hide_order_ids(t)
    t = _GROUPED_DIGITS.sub("<number>", t)
    t = _LONG_DIGITS.sub("<number>", t)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit - 1] + "…"


def _key_part(text) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", clean(text, 80))


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
            load_env(path, environ=values)
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
    from valedia_trader.execution_and_risk.orders import LAYOUT_VERSION, OrderLedger
    conn = open_readonly(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'orders'").fetchone()[0]
        if not has or version != LAYOUT_VERSION:
            raise CheckFailed(f"{path.name} is not an order ledger of the layout this code reads (layout {version}, "
                              f"this code reads layout {LAYOUT_VERSION})")
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


def is_practice(entry: dict) -> bool:
    """An order-step entry with a `mode` (propose or approve) is a practice run of the approval flow, not the unattended run."""
    return entry.get("mode") not in (None, "")


def last_step_summaries(entries: list[dict], practice: str | None = None) -> dict[str, dict]:
    """The last order-step summary of each venue (entries are in date order, each file in the order written): of the unattended
    run, that is of the summaries WITHOUT a `mode`; or, given `practice` ("propose" or "approve"), of that mode."""
    last: dict[str, dict] = {}
    for e in entries:
        if e.get("type") == "order_step" and e.get("event") == "summary" and \
                ((e.get("mode") == practice) if practice else not is_practice(e)):
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


def check_order_step(env: Env) -> CheckResult:
    out = CheckResult()
    entries, _ = env.journal()
    for venue, e in sorted(last_step_summaries(entries).items()):
        if e.get("status") == "stopped" and not is_outside_market_hours(e):
            out.alerts.append(Alert(f"step-stopped:{_key_part(venue)}", "critical",
                                    f"The order step stopped at stage {e.get('stage')}",
                                    f"{e.get('day')} {clean(venue, 60)}: {e.get('reason') or 'no reason given'}"))
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
    if not any(e.get("type") == "order_step" and not is_practice(e) and str(e.get("day"))[:10] == today for e in entries):
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
    ("order step", ("step-stopped:",), check_order_step),
    ("approval", ("approval:",), check_approval_refused),
    ("scheduled jobs", ("job:",), check_jobs),
    ("order-book collector", ("collector:",), check_collector),
    ("pick-list log", ("picks:",), check_picks),
    ("order-step run", ("orderstep:",), check_orderstep_ran),
    ("weekly research loop", ("weekly:",), check_weekly_ran),
    ("journal damage", ("journal:",), check_journal_damage),
    ("paper step", ("paper:",), check_paper_step),
    ("disk space", ("disk:",), check_disk),
]


def run_checks(env: Env) -> tuple[list[Alert], list[str], list[str], list[str]]:
    """(the alerts that are true now, keys held, key prefixes held, failures). A check that raises holds its whole prefix."""
    alerts, held, held_prefixes, failures = [], [], [], []
    for name, prefixes, fn in CHECKS:
        try:
            r = fn(env)
        except Exception as exc:
            failures.append(f"{name}: {describe(exc)}")
            held_prefixes += prefixes
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
    out["level"] = clean(e.get("level"), 20)
    out["title"], out["detail"], out["time"] = clean(e.get("title", ""), 120), clean(e.get("detail", ""), 300), clean(e.get("time", ""), 40)
    return out


def _replay(data: bytes) -> tuple[dict[str, dict], int]:
    """({key: its last line, cleaned} for the keys whose last line is not a cleared line, the number of lines that are not
    alert lines)."""
    open_map: dict[str, dict] = {}
    damaged = 0
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
        if e["level"] == CLEARED:
            open_map.pop(e["key"], None)
        else:
            open_map[e["key"]] = e
    return open_map, damaged


def open_alerts(path) -> list[dict]:
    """The alerts open now, oldest first, from the file; none when there is no file. Raises OSError if it cannot be read."""
    p = Path(path)
    if not p.exists():
        return []
    if not p.is_file():
        raise IsADirectoryError(f"{p.name} is not a file")
    return list(_replay(p.read_bytes())[0].values())


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


def watch(env: Env, senders=(), path=None) -> WatchResult:
    """Run every check once, write the new alerts and the cleared ones, then give each new alert to each sender."""
    path = Path(path) if path else alerts_file(env.root)
    register_trading_file(env)
    produced, held, held_prefixes, failures = run_checks(env)
    now = iso(env.now)
    by_key = {a.key: a for a in produced}
    path.parent.mkdir(parents=True, exist_ok=True)
    raised, cleared = [], []
    with path.open("a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)                     # two watches at once cannot both raise the same alert
        fh.seek(0)
        data = fh.read()
        open_map, _ = _replay(data)
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
    return WatchResult(raised, cleared, list(open_map.values()), failures, shown)


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

def _show(rows: list[dict], out, states: dict | None = None) -> None:
    if not rows:
        print("no open alerts", file=out)
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
            rows = open_alerts(alerts_file(env.root))
            _show(rows, out, read_notifications(notifications_file(env.root)))
            return 1 if rows else 0
        result = watch(env, senders=[MacNotifier()] if args.notify else [])
    except Exception as exc:
        print(f"the watch failed: {describe(exc)}", file=sys.stderr)
        return 2
    for a in result.raised:
        print(f"raised   {a.level:<8} {a.key}  {a.title} - {a.detail}", file=out)
    for a in result.cleared:
        print(f"cleared  {a.key}", file=out)
    _show(result.open, out, read_notifications(notifications_file(env.root)))
    for failure in result.failures:
        print(f"the watch could not check: {failure}", file=sys.stderr)
    return 2 if result.failures else (1 if result.open else 0)


if __name__ == "__main__":
    sys.exit(main())
