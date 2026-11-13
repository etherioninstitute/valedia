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

Everything shown or written passes `clean`: the package's output guard (a remembered secret becomes `<secret>`),
then any run of 9 or more digits (an account number) becomes `<number>`. A phone sender is anything with a
`send(alert)` method: give it to `watch` beside the Mac notifier.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from valedia_trader.core.secrets import child_env, redact

__all__ = ["Alert", "Env", "Sender", "MacNotifier", "WatchResult", "watch", "open_alerts", "alerts_file", "main", "clean",
           "open_readonly", "describe", "SourceMissing", "CHECKS", "last_trading_day", "exchange_sessions", "launchctl_text",
           "parse_jobs", "read_tail", "PICKS_LOG", "is_outside_market_hours"]

HEARTBEAT_MAX_AGE_S = 600.0                  # the collector writes one a minute; ten minutes without one is a stop
PICKS_END_MAX_AGE_S = 3 * 3600.0             # the pick-list job runs every hour
DISK_MIN_BYTES = 5 * 10 ** 9
DAMAGE_WINDOW_DAYS = 30
LEDGER_MAX_AGE_SECONDS = 5 * 86400.0         # the order step reconciles first thing each run, so a reconcile that is a few days
                                             # old (a long weekend) is not a stop; the ledger's own default is 6 hours
PICKS_LOG = "valedia-picks-hourly.log"
CLEARED = "cleared"
NEW_YORK = ZoneInfo("America/New_York")

_STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
_END_LINE = re.compile(rf"^({_STAMP}) end$")
_START_LINE = re.compile(rf"^{_STAMP} start$")
_FAILED = re.compile(r"\bfailed\b(?!:?\s*\d)", re.I)           # "failed: 0" and "failed 3 times" are counts, not a failed run
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")
_LONG_DIGITS = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_GROUPED_DIGITS = re.compile(r"(?<!\d)\d{3,}(?:[ -]\d{3,}){2,}(?!\d)")


# ---------------------------------------------------------------------------------------------- text and files

def clean(text, limit: int = 300) -> str:
    """One line of text that is safe to show or write: a remembered secret replaced by the package's own guard, any
    run of 9 or more digits (an account number) or digit groups written together replaced, control characters turned
    into spaces, and no more than `limit` characters."""
    t, _ = redact(str(text))
    t = _GROUPED_DIGITS.sub("<number>", t)
    t = _LONG_DIGITS.sub("<number>", t)
    t = " ".join(_CONTROL.sub(" ", t).split())
    return t if len(t) <= limit else t[:limit - 1] + "…"


def _key_part(text) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", clean(text, 80))


class SourceMissing(RuntimeError):
    """A file the page or the watch reads is not there."""


class CheckFailed(RuntimeError):
    """A check could not be asked; the message is already in plain words."""


def is_busy(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(w in str(exc).lower() for w in ("locked", "busy"))


def describe(exc: BaseException) -> str:
    """A failure in plain words, never more than a class and a short cleaned message."""
    if is_busy(exc):
        return "busy (another process holds the file); try again"
    if isinstance(exc, (SourceMissing, CheckFailed)):
        return clean(exc, 200)
    return f"{type(exc).__name__}: {clean(exc, 160)}"


def open_readonly(path, timeout: float = 2.0) -> sqlite3.Connection:
    """A read-only connection with a short wait for another process's write. Raises SourceMissing for a missing file."""
    p = Path(path)
    if not p.is_file():
        raise SourceMissing(f"{p.name} is not there")
    conn = sqlite3.connect("file:" + urllib.parse.quote(str(p.resolve())) + "?mode=ro", uri=True, timeout=timeout)
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
    _cache: dict = field(default_factory=dict, repr=False)

    @classmethod
    def default(cls, root=None) -> "Env":
        from valedia_trader.research_loop import REPO, risk_gate
        import time
        return cls(root=Path(root) if root else REPO, now=time.time(), halt_file=risk_gate.HALT_FILE,
                   logs_dir=Path.home() / "Library" / "Logs", launch_agents_dir=Path.home() / "Library" / "LaunchAgents",
                   launchctl=launchctl_text, free_bytes=lambda p: shutil.disk_usage(p).free, sessions=exchange_sessions)

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
        object.__setattr__(self, "key", _key_part(self.key) if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", self.key) else self.key)
        object.__setattr__(self, "title", clean(self.title, 120))
        object.__setattr__(self, "detail", clean(self.detail, 300))


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

    def send(self, alert: Alert) -> None:
        message = f"{alert.title}: {alert.detail}"[:200]
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


def last_step_summaries(entries: list[dict]) -> dict[str, dict]:
    """The last order-step summary of each venue (entries are in date order, each file in the order written)."""
    last: dict[str, dict] = {}
    for e in entries:
        if e.get("type") == "order_step" and e.get("event") == "summary":
            last[str(e.get("venue"))] = e
    return last


def is_outside_market_hours(summary: dict) -> bool:
    """A stop at stage "session" because the market is closed (outside the regular session window, not a trading day, a
    holiday) is ordinary. Any other stop at that stage (another run going, the calendar not loading) is not."""
    reason = str(summary.get("reason") or "").lower()
    return (summary.get("status") == "stopped" and summary.get("stage") == "session"
            and (reason.startswith("outside the regular session window") or "is not a trading day" in reason or "holiday" in reason))


def check_order_step(env: Env) -> CheckResult:
    out = CheckResult()
    entries, _ = env.journal()
    for venue, e in sorted(last_step_summaries(entries).items()):
        if e.get("status") == "stopped" and not is_outside_market_hours(e):
            out.alerts.append(Alert(f"step-stopped:{_key_part(venue)}", "critical",
                                    f"The order step stopped at stage {e.get('stage')}",
                                    f"{e.get('day')} {clean(venue, 60)}: {e.get('reason') or 'no reason given'}"))
    return out


def check_jobs(env: Env) -> CheckResult:
    out = CheckResult()
    try:
        loaded = parse_jobs(env.launchctl())
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
    ("scheduled jobs", ("job:",), check_jobs),
    ("order-book collector", ("collector:",), check_collector),
    ("pick-list log", ("picks:",), check_picks),
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


def _replay(data: bytes) -> tuple[dict[str, dict], int]:
    """({key: its last line} for the keys whose last line is not a cleared line, the number of lines that are not
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
        if e["level"] == CLEARED:
            open_map.pop(e["key"], None)
        else:
            open_map[e["key"]] = e
    return open_map, damaged


def open_alerts(path) -> list[dict]:
    """The alerts open now, oldest first, from the file; none when there is no file. Raises OSError if it cannot be read."""
    p = Path(path)
    if not p.is_file():
        return []
    return list(_replay(p.read_bytes())[0].values())


@dataclass
class WatchResult:
    raised: list[Alert]
    cleared: list[Alert]
    open: list[dict]                   # open after this run
    failures: list[str]


def watch(env: Env, senders=(), path=None) -> WatchResult:
    """Run every check once, write the new alerts and the cleared ones, then give each new alert to each sender."""
    path = Path(path) if path else alerts_file(env.root)
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
    for a in raised:
        for sender in senders:
            try:
                sender.send(a)
            except Exception as exc:
                failures.append(f"notification for {a.key}: {describe(exc)}")
    return WatchResult(raised, cleared, list(open_map.values()), failures)


# ---------------------------------------------------------------------------------------------- the command line

def _show(rows: list[dict], out) -> None:
    if not rows:
        print("no open alerts", file=out)
    for e in rows:
        print(f"{e['level']:<9} {e['key']}  {e.get('title', '')} - {e.get('detail', '')}  (since {e.get('time', '?')})", file=out)


def main(argv=None, env: Env | None = None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.core.alerts", description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=None, help="the repository (default: this one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="run the checks once and write the new alerts")
    w.add_argument("--notify", action="store_true", help="show one macOS notification for each new alert")
    sub.add_parser("list", help="print the open alerts")
    args = ap.parse_args(argv)
    env = env or Env.default(args.root)
    try:
        if args.cmd == "list":
            rows = open_alerts(alerts_file(env.root))
            _show(rows, out)
            return 1 if rows else 0
        result = watch(env, senders=[MacNotifier()] if args.notify else [])
    except Exception as exc:
        print(f"the watch failed: {describe(exc)}", file=sys.stderr)
        return 2
    for a in result.raised:
        print(f"raised   {a.level:<8} {a.key}  {a.title} - {a.detail}", file=out)
    for a in result.cleared:
        print(f"cleared  {a.key}", file=out)
    _show(result.open, out)
    for failure in result.failures:
        print(f"the watch could not check: {failure}", file=sys.stderr)
    return 2 if result.failures else (1 if result.open else 0)


if __name__ == "__main__":
    sys.exit(main())
