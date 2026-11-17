"""One operator command for a MiroFish native run beside the live order-book collector (TMF6).

    $PY -B tools/mirofish_operator.py check [--profile swarm8] [--model X | --setup-model A --turns-model B] [--json-out F]
    $PY -B tools/mirofish_operator.py load-test --model X --hold-minutes M --announced TEXT [--dry-run]
    $PY -B tools/mirofish_operator.py run --profile P [--model X | --setup-model A --turns-model B] --announced TEXT [--dry-run]
    $PY -B tools/mirofish_operator.py record
    $PY -B tools/mirofish_operator.py recover [--stop] [--dry-run]

`check` only reads. `load-test` and `run` switch off the hourly pick reader and every loaded model that is neither the decision
instance nor the embedding instance, restore the pick reader in a `finally` (Ctrl-C and exceptions included), watch the
collector every 30 s and stop on a breach. The tool never stops, signals, restarts, boots out or reconfigures the collector or
its launchd job, never sends a request to the decision instance and never reloads a model. Its use: docs/mirofish-operator.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import importlib.util
import json
import os
import re
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).absolute().parent
ROOT = TOOLS.parent


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


DM = _module(ROOT / 'valedia_trader/core/decision_model.py', 'operator_decision_model')   # the pure list judges
LIMITS = _module(TOOLS / 'mirofish_swarm_limits.py', 'operator_swarm_limits')
_STAGE = {}

# ---- places (each an override in Host for tests)
STORE = Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-trader/memory/orderbook_loop.sqlite')
BASE = Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space')
CREW = BASE / 'project/work/sol-foreman-qwen'
PIPELINE_PY = BASE / 'runtime/mirofish-local-20261106/venv-py311/bin/python'
PIPELINE_TMP = BASE / 'project/tmp'
WORK_VOLUME = Path('/Volumes/NIMROD_SSD')
RECORD = ROOT / 'artifacts/mirofish/runs.jsonl'
LOAD_TESTS = ROOT / 'artifacts/mirofish/load-tests'
LOGS = ROOT / 'artifacts/mirofish/operator-logs'
INTENT = ROOT / 'artifacts/mirofish/operator-intent.json'   # addendum 3, item 2: written before the first switch-off
LOCK = ROOT / 'artifacts/mirofish/operator.lock'             # addendum 4, D: held by run and load-test for their whole life
PICKS_PLIST = Path.home() / 'Library/LaunchAgents/com.valedia.picks.hourly.plist'   # addendum 4, C: the one plist recover loads
LMS = Path.home() / '.lmstudio/bin/lms'
LMSTUDIO = DM.URL

# ---- names
COLLECTOR_LABEL = 'com.valedia.orderbook-collector'
PICKS_LABEL = 'com.valedia.picks.hourly'
LAUNCHER_SCRIPT = 'mirofish_native_startup.py'      # what the launcher's command runs (launch below)
OBSERVER_SCRIPT = 'mirofish_native_run.py'          # what the observer's command runs (src/mirofish_native_startup.py:163)
ROLE_SCRIPTS = {'launcher': LAUNCHER_SCRIPT, 'observer': OBSERVER_SCRIPT}
EMBEDDING_INSTANCE = 'ym-research-embedding'
EMBEDDING_KEY = 'google/embedding-gemma-300m'          # its model key (src/mirofish_native_model.py:293, docs/mirofish-swarm.md:38)
NATIVE_PREFIX = 'mf-native-'
OUTCOME = 'VALEDIA-MIROFISH-FRESH-1'                 # mirofish_native_run.py OUTCOME, what the gate must name
GATE_NAME = 'root-one-native-attempt.json'

# ---- the collector's model-failure statuses (valedia_trader/research_loop/orderbook_loop.py): every status its model path
# writes. model_unavailable: pin check unreachable or instance not listed (:1204, :1212, :1223), unreachable in the slot
# (:2048), connection failure (:2079); tag_mismatch: the instance or file is not the right one (:1214-:1227); late: abandoned
# at the 10 s model limit (:2072) or not called for lack of time in the minute (:2050); error: HTTP error from the model
# (:2075), unreadable answer (:2091), any other call failure (:2082). Not model failures: ok, stale_data (the Kalshi book).
MODEL_FAILURE_STATUSES = ('model_unavailable', 'tag_mismatch', 'late', 'error')
# Addendum 1, ruling 1: these two can only mean the decision model is gone or wrong, a breach at once; the other two happen
# alone on an ordinary day (a per-market exception writes `error` at :2055 with the model untouched), a breach only when two
# of three consecutive decisions carry one of them.
IMMEDIATE_STATUSES = ('model_unavailable', 'tag_mismatch')
PAIRED_STATUSES = ('late', 'error')

# ---- the only statements the tool can issue against the store (read-only; never a locked column)
SQL_LAST_DECISIONS = 'SELECT status, latency_s, decision_ts FROM decisions ORDER BY decision_ts DESC LIMIT 10'
SQL_DECISIONS_SINCE = 'SELECT status, latency_s, decision_ts FROM decisions WHERE decision_ts >= ? ORDER BY decision_ts'
SQL_LAST_HEARTBEAT = 'SELECT MAX(ts) FROM heartbeats'
SQL = (SQL_LAST_DECISIONS, SQL_DECISIONS_SINCE, SQL_LAST_HEARTBEAT)

# ---- the rules' numbers
WATCH_S = 30                    # collector checked this often during load-test and run
SLOW_S = 8.0                    # two decisions in a row slower than this is a breach
HEARTBEAT_S = 150               # no heartbeat this long is a breach (addendum 1, ruling 3: one is written about every 60 s)
START_WINDOW_S = 900            # the start check judges the decisions of the last 15 minutes (ruling 2)
START_MIN_DECISIONS = 5         # fewer than this in the window: too quiet to judge, refuse
IDENTITY_WAIT_S = 60            # the observer identity file must appear this soon after launch (ruling 5)
IDENTITY_FILE = 'observer-identity.json'
LINK_DISABLED_LINE = 'You have disabled LM Link.'   # the real wording of `lms link status`, read 2026-11-17 (ruling 6)
LAUNCHER_STOP_SIGNAL = signal.SIGINT   # correction A: the launcher's KeyboardInterrupt path stops the observer group itself
PICKS_BEFORE_S = 120            # correction C: no switch-off from 2 minutes before the pick reader's scheduled start ...
PICKS_MISSED_S = 180            # ... until it is seen running and then idle, or idle this long after its start (addendum 2, 10)
LAUNCHER_PS_TRIES = 3            # addendum 3, item 1: the launcher's start time is read this often, SAMPLE_S apart
SETTLE_S = 15                   # addendum 2, item 5: after the unloads, before the free reading (see settle_after_unloads)
NEAR_NEUTRAL_REASON = 'derived report not usable evidence'   # src/mirofish_provenance.py:437, stored as receipt failure.reason
NEAR_NEUTRAL_TYPE = 'Refused'                                 # by src/mirofish_native_run.py:806 (str(exc), type name)
HARMFUL_STATUSES = ('model_unavailable', 'tag_mismatch', 'late')   # correction B: any one fails collector_unharmed
HARMFUL_ERRORS = 2              # correction B: this many `error` during the run fail it; a single one does not
SAMPLE_S = 0.5                  # load-test memory samples
BASELINE_S = 60                 # load-test baseline
STOP_FLOOR = LIMITS.STOP_FLOOR_PERCENT     # 30: the run stops itself under this much free memory
ROOM_POINTS = 10                # room_to_spare: the lowest sustained free reading at least this far above the floor
CLEANUP_S = LIMITS.CLEANUP_S    # 120: the pipeline's own cleanup headroom
STOP_WAIT_S = CLEANUP_S + 2 * WATCH_S      # after a stop signal, how long the pipeline's cleanup is waited for
PREFLIGHT_S = 30 + CLEANUP_S    # the launcher's preflight deadline (30 s, mirofish_native_startup.py) plus cleanup
GATE_S = 1800                   # gate expiry; the observer accepts at most 3600 s ahead
CMD_S = 30                      # a read-only system command (launchctl, ps, sysctl, memory_pressure, lms)
HTTP_S = 10
STREAKS = (('9', 'swarm8', 10), ('48', 'swarm48', 3))      # (participants, profile, target in a row)
SWARM_PROFILES = ('swarm8', 'swarm48')
NOT_RELOADED = 'NOT reloaded (LM Studio loads each again on demand)'
RECOVERED = 'recovered after an unfinished run'
STARTUP_DEADLINE = re.compile(r'\(30 if preflight else (\d+)\)')   # the stager's edit of mirofish_native_startup.py:145
OPERATOR_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class OperatorError(RuntimeError):
    """Something failed; the command stops and restores."""


class OperatorSignalled(BaseException):
    """Addendum 3, item 2: a TERM or a hang-up, turned into the same controlled path as a Ctrl-C."""

    def __init__(self, name):
        super().__init__(name); self.name = name


class Refused(OperatorError):
    """A start rule refused; the command stops and restores."""


# ============================================================================ the host: every outside effect, injectable
def _run(argv, cwd=None, env=None, timeout=CMD_S):
    return subprocess.run([str(a) for a in argv], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def _spawn(argv, cwd=None, env=None):
    LOGS.mkdir(parents=True, exist_ok=True)
    log = (LOGS / ('%s-launcher.log' % Path(cwd).parents[1].name)).open('ab')
    return subprocess.Popen([str(a) for a in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True)


def _proc_command(pid):
    """Addendum 4, A: one process's own command, for the proof alone. The caller only tests it and drops it: it is never
    printed, logged, stored, put in a row or in an exception text."""
    r = subprocess.run(['/bin/ps', '-ww', '-p', str(int(pid)), '-o', 'command='], capture_output=True, text=True, timeout=CMD_S)
    return r.stdout.strip() if r.returncode == 0 else None


def _proc_scan(uid):
    """Addendum 4, B: the pids of this user's processes (pids only)."""
    r = subprocess.run(['/bin/ps', '-U', str(int(uid)), '-o', 'pid='], capture_output=True, text=True, timeout=CMD_S)
    if r.returncode != 0:
        raise OperatorError('the process scan failed (exit %d)' % r.returncode)
    return [int(x) for x in r.stdout.split()]


def _say(line):
    """Addendum 7, E: one line on standard output, made safe for its encoding whatever the text holds (a lone surrogate
    read from a file or a listing, a character the encoding lacks): such a character is written as a backslash escape."""
    stream = sys.stdout
    encoding = getattr(stream, 'encoding', None) or 'ascii'
    print(str(line).encode(encoding, 'backslashreplace').decode(encoding, 'replace'), file=stream, flush=True)


def _http(method, url, body=None, timeout=HTTP_S):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            raw = reply.read(); status = reply.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw or b'null')
    except ValueError:
        return status, None


class Host:
    def __init__(self, *, run=None, spawn=None, http=None, now=None, sleep=None, send_signal=None, disk_free=None, uid=None, store=STORE, crew=CREW, record=RECORD, load_tests=LOAD_TESTS, load_path=None, out=None,
                 lms=LMS, self_pid=None, parent_pid=None, fsync=None, install_signal=None, intent=INTENT,
                 proc_command=None, proc_scan=None, picks_plist=PICKS_PLIST, lock=LOCK):
        self.run, self.spawn, self.http = run or _run, spawn or _spawn, http or _http   # _spawn: its own session and group
        self.now, self.sleep, self.send_signal = now or time.time, sleep or time.sleep, send_signal or os.kill
        self.disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self.uid = os.getuid() if uid is None else uid
        self.store, self.crew, self.record, self.load_tests = Path(store), Path(crew), Path(record), Path(load_tests)
        self.load_path = load_path or PipelineLoadPath
        self.out = out or _say
        self.lms = str(lms)
        self.self_pid = os.getpid() if self_pid is None else self_pid          # never a stop target (addendum 2, item 2)
        self.parent_pid = os.getppid() if parent_pid is None else parent_pid
        self.fsync = fsync or os.fsync
        self.install_signal = install_signal or signal.signal
        self.intent = Path(intent)
        self.proc_command = proc_command or _proc_command
        self.proc_scan = proc_scan or (lambda: _proc_scan(self.uid))
        self.picks_plist, self.lock = str(picks_plist), Path(lock)


def utc(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat(timespec='seconds')


def local(t):
    return _dt.datetime.fromtimestamp(t).astimezone().strftime('%Y-%m-%d %H:%M %Z')


# ============================================================================ the collector (read-only)
def open_store(path):
    return sqlite3.connect('file:%s?mode=ro' % urllib.parse.quote(str(path)), uri=True, timeout=5)


def query(host, sql, params=()):
    if sql not in SQL:
        raise OperatorError('refused: a statement outside the allowed tuple was asked of the store')
    db = open_store(host.store)
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def breach_of(decisions, heartbeat_age, listed):
    """The breach rule over decisions in time order; None when there is none."""
    if not listed:
        return 'the collector launchd job %s is not listed' % COLLECTOR_LABEL
    if heartbeat_age is None or heartbeat_age > HEARTBEAT_S:
        return 'no heartbeat for %s s (limit %d s)' % ('ever' if heartbeat_age is None else '%.0f' % heartbeat_age, HEARTBEAT_S)
    for d in decisions:
        if d['status'] in IMMEDIATE_STATUSES:
            return 'a decision with the model-failure status %s (at %s)' % (d['status'], d['decision_ts'])
    # Two of the last three: checked after every decision, so over every three consecutive decisions in time order.
    for i in range(len(decisions)):
        last3 = decisions[max(0, i - 2):i + 1]
        hits = [d['status'] for d in last3 if d['status'] in PAIRED_STATUSES]
        if len(hits) >= 2:
            return 'two of three consecutive decisions with a model-failure status (%s, at %s)' % (
                ', '.join(hits), decisions[i]['decision_ts'])
    previous = None
    for d in decisions:
        latency = d['latency_s']
        slow = isinstance(latency, (int, float)) and latency > SLOW_S
        if slow and previous is not None:
            return 'two decisions in a row slower than %g s (%.1f s, %.1f s)' % (SLOW_S, previous, latency)
        previous = latency if slow else None
    return None


def launchd_print(host, label):
    """`launchctl print` of a gui job: only the exit code and the `path`, `state`, `pid` and calendar `"Minute" => N` lines are
    kept (the rest carries the job's command line, which is never kept or printed)."""
    r = host.run(['/bin/launchctl', 'print', 'gui/%d/%s' % (host.uid, label)], timeout=CMD_S)
    if r.returncode != 0:
        return {'listed': False, 'plist': None, 'state': None, 'pid': None, 'minute': None}
    path = re.search(r'^\s*path = (.+?)\s*$', r.stdout or '', re.M)
    state = re.search(r'^\s*state = (.+?)\s*$', r.stdout or '', re.M)
    pid = re.search(r'^\s*pid = (\d+)\s*$', r.stdout or '', re.M)
    minute = re.search(r'^\s*"Minute" => (\d+)\s*$', r.stdout or '', re.M)
    return {'listed': True, 'plist': path.group(1) if path else None, 'state': state.group(1) if state else None,
            'pid': int(pid.group(1)) if pid else None, 'minute': int(minute.group(1)) % 60 if minute else None}


def collector_health(host, since=None):
    """The collector's job, its decisions (the last 10, or every one since `since`) and its last heartbeat, judged."""
    listed = launchd_print(host, COLLECTOR_LABEL)['listed']
    try:
        if since is None:
            rows = list(reversed(query(host, SQL_LAST_DECISIONS)))
        else:
            rows = query(host, SQL_DECISIONS_SINCE, (since,))
        beat = query(host, SQL_LAST_HEARTBEAT)[0][0]
    except sqlite3.Error as e:
        return {'job_listed': listed, 'rows': [], 'decisions': 0, 'statuses': {}, 'highest_latency_s': None, 'over_8s': 0,
                'model_failures': 0, 'heartbeat_age_s': None, 'breach': 'the collector store cannot be read: %s' % e}
    decisions = [{'status': s, 'latency_s': lat, 'decision_ts': ts} for s, lat, ts in rows]
    age = None if beat is None else round(host.now() - beat, 1)
    latencies = [d['latency_s'] for d in decisions if isinstance(d['latency_s'], (int, float))]
    return {'job_listed': listed, 'rows': decisions, 'decisions': len(decisions),
            'statuses': dict(Counter(d['status'] for d in decisions)),
            'highest_latency_s': max(latencies) if latencies else None,
            'over_8s': sum(1 for x in latencies if x > SLOW_S),
            'model_failures': sum(1 for d in decisions if d['status'] in MODEL_FAILURE_STATUSES),
            'heartbeat_age_s': age, 'breach': breach_of(decisions, age, listed)}


def collector_figures(health):
    return {k: health[k] for k in ('decisions', 'statuses', 'highest_latency_s', 'over_8s', 'model_failures', 'heartbeat_age_s',
                                   'job_listed', 'breach')}


# ============================================================================ the machine
def memory(host):
    """free reading (memory_pressure), kernel pressure level (1 normal, 2 warn, 4 critical), swap used in bytes."""
    r = host.run(['/usr/bin/memory_pressure'], timeout=CMD_S)
    free = re.search(r'System-wide memory free percentage:\s*(\d+)%', r.stdout or '')
    p = host.run(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'], timeout=CMD_S)
    level = (p.stdout or '').strip()
    s = host.run(['/usr/sbin/sysctl', '-n', 'vm.swapusage'], timeout=CMD_S)
    used = re.search(r'used = ([\d.]+)([KMG])', s.stdout or '')
    scale = {'K': 2**10, 'M': 2**20, 'G': 2**30}
    return {'free_percent': int(free.group(1)) if free and r.returncode == 0 else None,
            'pressure_level': int(level) if p.returncode == 0 and level.isdigit() else None,
            'swap_used_bytes': int(round(float(used.group(1)) * scale[used.group(2)])) if used and s.returncode == 0 else None}


def link_disabled(stdout, stderr, returncode):
    """LM Link counts as disabled only when `lms link status` exits 0 and the first non-empty line of its standard output,
    or of its standard error when standard output is empty (where the status line goes, measured 2026-11-17: 0 bytes on
    standard output), with surrounding blanks removed, begins with exactly LINK_DISABLED_LINE. Anything else is not known
    to be disabled."""
    text = stdout if (stdout or '').strip() else stderr
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    return returncode == 0 and bool(lines) and lines[0].startswith(LINK_DISABLED_LINE)


def lm_link(host):
    r = host.run([host.lms, 'link', 'status'], timeout=CMD_S)
    disabled = link_disabled(r.stdout, r.stderr, r.returncode)
    return {'disabled': disabled, 'state': 'disabled' if disabled else 'not known to be disabled', 'exit': r.returncode}


PROTECTED_REFUSAL = 'the model list cannot be judged: %s; the tool does not guess which instance is meant, so nothing is switched off'


def protected_wrong(loaded):
    """Addendum 7, D: an instance is the decision or the embedding instance only as its expected pair, id and model key
    both. Every way a listing breaks that: a protected id under another key, a protected key under another id, a loaded
    instance whose id or key is not a plain string. The tool does not guess which one is meant."""
    out = []
    for key, iid in loaded:
        if not (plain_string(key) and plain_string(iid)):
            out.append('a loaded instance has an id or a model key that is not a plain string (id %s, key %s)' % (
                repr(iid)[:60], repr(key)[:60]))
            continue
        for p_id, p_key, what in ((DM.INSTANCE, DM.KEY, 'decision'), (EMBEDDING_INSTANCE, EMBEDDING_KEY, 'embedding')):
            if iid == p_id and key != p_key:
                out.append('the %s instance id %s is loaded under the model key %s, not %s' % (what, p_id, key, p_key))
            if key == p_key and iid != p_id:
                out.append('the %s model key %s is loaded as the instance %s, not %s' % (what, p_key, iid, p_id))
    return out


def models_state(host):
    """LM Studio's list judged with decision_model's pure functions: the decision instance loaded and right, the embedding
    instance loaded, every other loaded instance (what a run unloads) and any mf-native-* instance; `protected_wrong`
    (addendum 7, D) names every way the list breaks the protected pairs; `reachable` False when it could not be read."""
    def unknown(why):
        return {'reachable': False, 'decision_instance': {'loaded_and_right': False, 'why': why}, 'embedding_loaded': False,
                'others': [], 'native_loaded': [], 'protected_wrong': [], 'unread_why': why}
    try:
        status, listing = host.http('GET', LMSTUDIO + '/api/v1/models', None, HTTP_S)
    except Exception as e:
        return unknown('LM Studio list unreachable: %s: %s' % (type(e).__name__, e))
    if status != 200:
        return unknown('LM Studio list answered HTTP %s' % status)
    try:
        entries, found = DM._instances(listing)
    except ValueError as e:
        return unknown('LM Studio list not of the expected shape: %s' % e)
    why = DM._wrong_found(found) if found else 'instance %s not loaded' % DM.INSTANCE
    loaded = [(e.get('key'), i.get('id')) for e in entries for i in e.get('loaded_instances', [])]
    # Addendum 2, item 9: any further instance of the decision model (its key, or an id that starts with its name) makes
    # it "not right" (the collector needs exactly one right instance) and is never an "other" model to unload.
    def decision_model(key, iid):
        return key == DM.KEY or (isinstance(iid, str) and iid.startswith(DM.INSTANCE))
    extra = [iid for key, iid in loaded if decision_model(key, iid) and iid != DM.INSTANCE]
    if why is None and extra:
        why = 'a second instance of the decision model is loaded: %s' % ', '.join(str(x) for x in extra)
    native = [iid for _, iid in loaded if isinstance(iid, str) and iid.startswith(NATIVE_PREFIX)]
    others = [{'instance_id': iid, 'key': key} for key, iid in loaded
              if plain_string(iid) and plain_string(key) and iid not in (DM.INSTANCE, EMBEDDING_INSTANCE)
              and key not in (DM.KEY, EMBEDDING_KEY) and not iid.startswith(NATIVE_PREFIX) and not decision_model(key, iid)]
    return {'reachable': True, 'decision_instance': {'loaded_and_right': why is None, 'why': why},
            'embedding_loaded': any(iid == EMBEDDING_INSTANCE and key == EMBEDDING_KEY for key, iid in loaded),
            'others': others, 'native_loaded': native, 'protected_wrong': protected_wrong(loaded), 'unread_why': None}


def read_identity(host, pid):
    """ps of one pid: (its pid, parent pid, process group and start (lstart), 'ok'), never its command line; (None,
    'gone') only for ps's own answer for a pid that does not exist (exit 1, nothing on either stream, measured
    2026-11-17); (None, 'unreadable') for any other answer (addendum 7, B: a start that could not be read is never
    "gone"). An exception from the read goes to the caller."""
    r = host.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,lstart='], timeout=CMD_S)
    out, err = r.stdout if isinstance(r.stdout, str) else '', r.stderr if isinstance(r.stderr, str) else ''
    if r.returncode == 1 and not out.strip() and not err.strip():
        return None, 'gone'
    fields = out.split()
    if r.returncode or len(fields) < 8 or not all(f.isdigit() for f in fields[:3]) or int(fields[0]) != pid:
        return None, 'unreadable'
    return {'pid': int(fields[0]), 'ppid': int(fields[1]), 'pgid': int(fields[2]), 'start': ' '.join(fields[3:])}, 'ok'


def observed_identity(host, pid):
    """The identity of read_identity, or None (gone or unreadable: the run's own stop paths treat both as no match)."""
    return read_identity(host, pid)[0]


def identity_mismatch(host, identity):
    """None when a recorded observer {pid, start, pgid} is well formed and the live process now; else why it is not."""
    if not (isinstance(identity, dict) and type(identity.get('pid')) is int
            and isinstance(identity.get('start'), str) and identity['start'] and type(identity.get('pgid')) is int):
        return 'the observer identity file is not {"pid": int, "start": str, "pgid": int, ...}'
    seen = observed_identity(host, identity['pid'])
    if seen is None:
        return 'pid %d is not running' % identity['pid']
    if seen['start'] != identity['start']:
        return 'pid %d started %r, not %r as the identity file says' % (identity['pid'], seen['start'], identity['start'])
    if seen['pgid'] != identity['pgid']:
        return 'pid %d is in process group %d, not %d' % (identity['pid'], seen['pgid'], identity['pgid'])
    return None


def launcher_mismatch(host, launcher):
    """None when the launcher this tool started is still that process (pid, start and group as recorded at spawn)."""
    if not (launcher and type(launcher.get('pid')) is int and launcher.get('start')):
        return 'the launcher was not recorded at spawn'
    seen = observed_identity(host, launcher['pid'])
    if seen is None or seen['start'] != launcher['start'] or seen['pgid'] != launcher['pgid']:
        return 'the launcher pid %s is no longer the process started (%s)' % (
            launcher['pid'], 'gone' if seen is None else 'start or group differs')
    return None


def observer_unusable(host, identity, launcher_pid, launcher_why):
    """Addendum 2, item 2. None only when ALL hold: the identity is well formed; its pid is alive with exactly that start
    (and group); its PARENT pid is the launcher (launcher_why None: the tool's own unreaped child, with its start matching
    when one was read; or, for `recover`, the recorded launcher with pid and start matching); the pid is not this tool's
    own, not this tool's parent, not 0 or 1. Else why not: then it is never sent TERM."""
    why = identity_mismatch(host, identity)
    if why:
        return why
    pid = identity['pid']
    if pid <= 1 or pid in (host.self_pid, host.parent_pid):         # the one guard (0, 1 and below; kill(-1) is everyone)
        return 'pid %d is this tool, its parent, or not above 1' % pid
    if launcher_why:
        return launcher_why
    seen = observed_identity(host, pid)
    if seen is None or seen['ppid'] != launcher_pid:
        return 'pid %d is not a child of the launcher %s (parent %s)' % (pid, launcher_pid, seen and seen['ppid'])
    return None


def earlier_runs(host):
    """Unexpired gate files and live observers (pid and start both matching) of earlier runs under the crew folder. An
    unreadable gate or identity file is counted as live (fail closed)."""
    gates, live = [], []
    for path in sorted(host.crew.glob('*/' + GATE_NAME)):
        try:
            expires = _dt.datetime.fromisoformat(json.loads(path.read_text()).get('expires_at_utc', ''))
            if expires.tzinfo is None or expires.timestamp() > host.now():
                gates.append(str(path))
        except (OSError, ValueError, AttributeError):
            gates.append(str(path) + ' (unreadable)')
    for path in sorted(host.crew.glob('*/builder/observer-identity.json')):
        try:
            identity = json.loads(path.read_text())
        except (OSError, ValueError):
            live.append(str(path) + ' (unreadable)'); continue
        if identity_mismatch(host, identity) is None:
            live.append(str(path))
    return {'unexpired_gates': gates, 'live_observers': live}


def pick_reader_state(host):
    """Its launchd state; when it is running, also its pid and when it was started (ps lstart; no command line)."""
    s = launchd_print(host, PICKS_LABEL)
    s['running'] = s['state'] == 'running'
    s['started'] = None
    if s['running'] and s['pid'] is not None:
        seen = observed_identity(host, s['pid'])
        s['started'] = seen['start'] if seen else None
    s['read_t'] = host.now()
    s['due'] = picks_due(host, s)
    return s


def running_readings(host):
    """The times at which earlier `run` and `load-test` invocations read the pick reader RUNNING, from the record rows (a
    damaged row is skipped). `check` writes no row, so what it saw is not among them."""
    rows, _ = read_rows(host.record)
    out = []
    for r in rows:
        so = r.get('switch_offs') if isinstance(r.get('switch_offs'), dict) else {}
        pr = so.get('pick_reader') if isinstance(so.get('pick_reader'), dict) else {}
        check = r.get('check') if isinstance(r.get('check'), dict) else {}
        for seen in (check.get('pick_reader'), pr.get('before'), pr.get('after')):
            if isinstance(seen, dict) and seen.get('running') is True and isinstance(seen.get('read_t'), (int, float)):
                out.append(seen['read_t'])
    return out


def picks_due(host, reader):
    """Corrections C and addendum 2, item 10. Why the switch-offs may not begin now, or None. The window opens PICKS_BEFORE_S
    before the reader's scheduled start (its calendar minute from launchd, the full hour when none is read; local clock,
    injected) and closes only when the reader has been read RUNNING after that start and is read idle now, or when
    PICKS_MISSED_S have passed since that start with it idle at every reading (a missed start). A reader that is not loaded
    is left alone and has no window; a reader read running now is refused by its own rule."""
    if not reader['listed']:
        return None
    minute = reader.get('minute') or 0
    t = host.now(); lt = time.localtime(t)
    since = (lt.tm_min * 60 + lt.tm_sec + (t % 1) - minute * 60) % 3600     # seconds since its last scheduled start
    if 3600 - since <= PICKS_BEFORE_S:
        return ('the hourly pick reader %s is due at :%02d in %d s; no switch-off begins from %d minutes before it until it '
                'has been seen running and then idle after it' % (PICKS_LABEL, minute, round(3600 - since), PICKS_BEFORE_S // 60))
    if since < PICKS_MISSED_S and not reader['running']:
        start = t - since
        if not any(start <= x <= t for x in running_readings(host)):
            return ('the hourly pick reader %s was due at :%02d, %d s ago, and is not yet seen running and then idle after it '
                    '(or idle for %d minutes after it: a missed start)' % (PICKS_LABEL, minute, int(since), PICKS_MISSED_S // 60))
    return None


# ============================================================================ profiles, durations, the announcement
def stage_module():
    if 'm' not in _STAGE:
        _STAGE['m'] = _module(TOOLS / 'mirofish_native_stage.py', 'operator_stage')
    return _STAGE['m']


def limits_model(plan):
    if plan is None:
        return None
    return plan['model'] if 'model' in plan else {'setup': plan['setup'], 'turns': plan['turns']}


def plan_label(plan):
    try:
        return LIMITS.model_label(limits_model(plan) or LIMITS.DEFAULT_MODEL)
    except (ValueError, TypeError, KeyError):  # addendum 7, E: a plan the limits table does not know is named, never raised
        return repr(plan)[:200]


def run_durations(profile, plan):
    try:
        p = stage_module().swarm_numbers(profile, limits_model(plan))
    except ValueError as e:               # addendum 7, E: the limits table's own refusal (an unknown model, a bad plan)
        raise Refused('the limits table tools/mirofish_swarm_limits.py refuses %s on %s: %s' % (profile, plan_label(plan), e))
    return {'expected_s': p['expected_total_s'], 'wall_s': p['wall']}


def load_test_durations(hold_minutes):
    q = format_qualify()
    expected = BASELINE_S + 60 * hold_minutes + 60
    return {'expected_s': expected, 'wall_s': BASELINE_S + q.BASELINE_S + q.LOAD_S + 60 * hold_minutes + q.UNLOAD_S}


def minutes(seconds):
    return '%d min' % round(seconds / 60)


def announcement(kind, profile, plan, durations, summary, start_t, hold_minutes=None):
    picks = summary['pick_reader']; others = summary['models']['others']
    window = durations['wall_s'] + CLEANUP_S
    if kind == 'run':
        what = 'MiroFish %s on %s' % (profile, plan_label(plan))
        how_long = 'Expected duration %s; the run stops itself at its wall clock of %s' % (
            minutes(durations['expected_s']), minutes(durations['wall_s']))
    else:
        what = 'A MiroFish load test of %s (the pipeline\'s own load path; %d s baseline, %d min hold)' % (
            plan['model'], BASELINE_S, hold_minutes)
        how_long = 'Expected duration %s; at most %s' % (minutes(durations['expected_s']), minutes(durations['wall_s']))
    picks_text = ('the hourly pick reader %s (booted out at the start, bootstrapped back from %s at the end)' % (
        PICKS_LABEL, picks['plist']) if picks['listed'] else
        'the hourly pick reader %s is not loaded now, so it is left alone' % PICKS_LABEL)
    models_text = (', '.join('%s (%s)' % (o['instance_id'], o['key']) for o in others) + ', unloaded and ' + NOT_RELOADED
                   if others else 'no LM Studio model to unload (only the decision and embedding instances are loaded)')
    return ('%s starts at %s (%s UTC). %s. Switched off for the whole window, at most %s (until about %s): %s; %s. Not '
            'switched by this tool and to be kept off by hand: the local Hermes bots (Scout, Valedia, CTO, local delegation '
            'workers) and anything else that uses LM Studio. The order-book collector stays on; it is checked every %d s and '
            'a breach stops the %s.' % (what, local(start_t), utc(start_t), how_long, minutes(window), local(start_t + window),
                                        picks_text, models_text, WATCH_S, 'run' if kind == 'run' else 'test'))


def refusals(s):
    out = []
    if not s['lm_link']['disabled']:
        out.append('LM Link is not disabled (state %s); the tool never changes it' % s['lm_link']['state'])
    d = s['models']['decision_instance']
    if not d['loaded_and_right']:
        out.append('the decision instance %s is not loaded and right: %s' % (DM.INSTANCE, d['why']))
    if s['models']['native_loaded']:
        out.append('an mf-native instance is already loaded: %s' % ', '.join(s['models']['native_loaded']))
    if s['models'].get('protected_wrong'):
        out.append(PROTECTED_REFUSAL % '; '.join(s['models']['protected_wrong']))
    if s['collector']['window_decisions'] < START_MIN_DECISIONS:
        out.append('the collector is too quiet to judge: %d decisions in the last %d minutes (at least %d needed)' % (
            s['collector']['window_decisions'], START_WINDOW_S // 60, START_MIN_DECISIONS))
    if s['collector']['breach']:
        out.append('the collector is already in breach: %s' % s['collector']['breach'])
    if s['pick_reader']['running']:
        out.append('the hourly pick reader %s is running (pid %s, started %s); it runs a few minutes at the top of the hour '
                   'and is never booted out mid-run: start after it ends' % (
                       PICKS_LABEL, s['pick_reader']['pid'], s['pick_reader']['started'] or 'at an unknown time'))
    if s['pick_reader'].get('due'):
        out.append(s['pick_reader']['due'])
    level = s['memory']['pressure_level']
    if level != 1:
        out.append('the kernel memory pressure level %s is not 1' % level)
    if s['memory']['free_percent'] is None:
        out.append('no free memory reading')
    if s['earlier_runs']['unexpired_gates']:
        out.append('an unexpired gate file of an earlier run exists: %s' % ', '.join(s['earlier_runs']['unexpired_gates']))
    if s['earlier_runs']['live_observers']:
        out.append('a running observer of an earlier run exists: %s' % ', '.join(s['earlier_runs']['live_observers']))
    pr = s['pick_reader']
    if pr['listed'] and pr['plist'] != s['picks_plist_expected']:
        out.append('the pick reader %s is loaded from %s, not from %s: it is not booted out' % (
            PICKS_LABEL, pr['plist'], s['picks_plist_expected']))
    if s['intent']['exists']:
        out.append('AN UNFINISHED RUN\'S INTENT FILE EXISTS (%s): AN EARLIER RUN OR LOAD TEST ENDED BEFORE ITS RESTORE AND ITS '
                   'ROW; RUN `recover` FIRST' % s['intent']['path'])
    return out


def do_check(host, profile, plan, kind='run', hold_minutes=None):
    t = host.now()
    s = {'utc': utc(t), 'kind': kind, 'profile': profile, 'plan': plan,
         'lm_link': lm_link(host), 'models': models_state(host)}
    last = collector_health(host)                                  # the last 10, shown
    window = collector_health(host, since=t - START_WINDOW_S)     # the last 15 minutes, judged (addendum 1, ruling 2)
    s['collector'] = {'job_listed': window['job_listed'], 'heartbeat_age_s': window['heartbeat_age_s'], 'breach': window['breach'],
                      'window_s': START_WINDOW_S, 'window_decisions': window['decisions'], 'window_statuses': window['statuses'],
                      'last_decisions': [{'status': d['status'], 'latency_s': d['latency_s'],
                                          'age_s': None if d['decision_ts'] is None else round(t - d['decision_ts'], 1)}
                                         for d in last['rows'][-10:]]}
    s['memory'] = memory(host)
    s['pick_reader'] = pick_reader_state(host)
    s['disk'] = {'path': str(WORK_VOLUME), 'free_bytes': host.disk_free(WORK_VOLUME)}
    s['earlier_runs'] = earlier_runs(host)
    s['intent'] = {'path': str(host.intent), 'exists': host.intent.exists()}
    s['picks_plist_expected'] = host.picks_plist
    s['durations'] = run_durations(profile, plan) if kind == 'run' else load_test_durations(hold_minutes)
    s['announcement'] = announcement(kind, profile, plan, s['durations'], s, t, hold_minutes)
    s['refusals'] = refusals(s)
    return s


def check_lines(s):
    m, c, d = s['memory'], s['collector'], s['models']
    lines = ['- LM Link: %s%s' % (s['lm_link']['state'], '' if s['lm_link']['disabled'] else ' (MUST BE DISABLED; the tool never changes it)'),
             '- decision instance %s: %s' % (DM.INSTANCE, 'loaded and right' if d['decision_instance']['loaded_and_right']
                                             else 'DECISION INSTANCE NOT LOADED AND RIGHT: ' + str(d['decision_instance']['why'])),
             '- embedding instance %s: %s' % (EMBEDDING_INSTANCE, 'loaded' if d['embedding_loaded'] else 'NOT loaded'),
             '- other loaded models (a run unloads these): %s' % (', '.join(o['instance_id'] for o in d['others']) or 'none'),
             '- collector: job %s; last %d decisions %s; highest latency %s s; heartbeat %s s ago; last %d min: %d decisions %s; '
             'breach: %s' % (
                 'listed' if c['job_listed'] else 'NOT listed', len(c['last_decisions']),
                 dict(Counter(x['status'] for x in c['last_decisions'])),
                 max([x['latency_s'] for x in c['last_decisions'] if isinstance(x['latency_s'], (int, float))], default=None),
                 c['heartbeat_age_s'], c['window_s'] // 60, c['window_decisions'], c['window_statuses'], c['breach'] or 'none'),
             '- memory: %s%% free, pressure level %s, swap used %s bytes' % (m['free_percent'], m['pressure_level'], m['swap_used_bytes']),
             '- pick reader %s: %s' % (PICKS_LABEL, ('loaded (%s%s), from %s' % (
                 s['pick_reader']['state'], ', started %s' % s['pick_reader']['started'] if s['pick_reader']['running'] else '',
                 s['pick_reader']['plist']))
                                       if s['pick_reader']['listed'] else 'not loaded'),
             '- free disk on %s: %.1f GB' % (s['disk']['path'], s['disk']['free_bytes'] / 1e9),
             '- earlier runs: %d unexpired gate(s), %d live observer(s)' % (len(s['earlier_runs']['unexpired_gates']),
                                                                             len(s['earlier_runs']['live_observers'])),
             ('- durations from tools/mirofish_swarm_limits.py for %s on %s: expected %d s, wall %d s; a run watches to the '
              'deadline the stager wrote into the staged folder (startup script, `(30 if preflight else N)`), read after '
              'staging and printed then' % (s['profile'], plan_label(s['plan']), s['durations']['expected_s'], s['durations']['wall_s'])
              if s['kind'] == 'run' else '- durations: expected %d s, wall %d s' % (s['durations']['expected_s'], s['durations']['wall_s'])),
             'ANNOUNCEMENT (the lead gives this to the owner before starting): ' + s['announcement']]
    lines += ['REFUSED: ' + r for r in s['refusals']] or ['start rules: none refuses']
    return lines


# ============================================================================ switch-offs and their restore
class SwitchOffs:
    """The hourly pick reader (booted out, bootstrapped back from the same plist) and every loaded model that is neither the
    decision instance nor the embedding instance (unloaded by REST, never reloaded). Each with its before-state in the row."""

    def __init__(self, host, row):
        self.host = host; self.row_alerts = row['alerts']; self.row = row
        self.rec = row['switch_offs'] = {'pick_reader': None, 'models_unloaded': [], 'models_note': NOT_RELOADED}

    def apply(self, summary):
        host = self.host
        before = pick_reader_state(host)
        pr = self.rec['pick_reader'] = {'label': PICKS_LABEL, 'before': before, 'booted_out': False, 'after': None, 'restored': None}
        if before['running']:      # it may have started since the check: never boot out a reader mid-run (ruling 4)
            raise Refused('the hourly pick reader %s is running (pid %s, started %s); it is never booted out mid-run: start '
                          'after it ends' % (PICKS_LABEL, before['pid'], before['started'] or 'at an unknown time'))
        if before['due']:          # correction C: the clock may have entered its window since the check
            raise Refused(before['due'])
        if before['listed']:
            if not before['plist']:
                raise OperatorError('the pick reader\'s plist path is unknown, so it could not be bootstrapped back; not booted out')
            if before['plist'] != host.picks_plist:     # addendum 4, C: recover loads the constant plist only
                raise Refused('the pick reader is loaded from %s, not from %s: not booted out' % (before['plist'], host.picks_plist))
            pr['restore_intent'] = True       # addendum 2, item 7: before the bootout is issued, so no interrupt can skip it
            r = host.run(['/bin/launchctl', 'bootout', 'gui/%d/%s' % (host.uid, PICKS_LABEL)], timeout=CMD_S)
            pr['booted_out'] = not pick_reader_state(host)['listed']
            if not pr['booted_out']:
                raise OperatorError('the pick reader bootout failed (exit %d); it is still loaded' % r.returncode)
        for other in summary['models']['others']:
            iid = other['instance_id']
            if (iid in (DM.INSTANCE, EMBEDDING_INSTANCE) or iid.startswith(NATIVE_PREFIX) or iid.startswith(DM.INSTANCE)
                    or other['key'] == DM.KEY):             # never, whatever the list says
                raise OperatorError('refused to unload the protected instance %s' % iid)
            item = {'instance_id': iid, 'key': other['key'], 'unloaded': False}
            self.rec['models_unloaded'].append(item)
            try:
                status, _ = host.http('POST', LMSTUDIO + '/api/v1/models/unload', {'instance_id': iid}, HTTP_S)
            except Exception as e:
                raise OperatorError('unloading %s failed: %s: %s' % (iid, type(e).__name__, e))
            if status != 200:
                raise OperatorError('unloading %s failed: HTTP %s' % (iid, status))
            item['unloaded'] = True
        if summary['models']['others']:
            announced = {o['instance_id'] for o in summary['models']['others']}
            now = read_before_spawn(host, self.row)['others']        # addendum 7, C: unreadable is a refusal here too
            still = [o['instance_id'] for o in now if o['instance_id'] in announced]
            later = ['%s (%s)' % (o['instance_id'], o['key']) for o in now if o['instance_id'] not in announced]
            if later:              # addendum 6: before the spawn a later model means the run is not launched (never unloaded)
                late_models(host, self.row, now=[o for o in now if o['instance_id'] not in announced])
                raise Refused(LATE_REFUSAL % ', '.join(later))
            if still:
                raise OperatorError('still loaded after the unload: %s' % ', '.join(still))

    def restore(self, steps):
        """True when the pick reader is as it was. Every step runs (addendum 2, item 1): a BaseException from one, Ctrl-C
        and SystemExit included, is kept by `steps` and the next step still runs; the caller raises it again only after
        the record row and the summary are written. Whether to bootstrap is decided by the job's real state (item 7)."""
        pr = self.rec['pick_reader']
        if pr is None:
            return True
        if pr.get('restore_intent'):
            now = steps.run('reading the pick reader before its restore', pick_reader_state, self.host)
            if now is None or not now['listed']:          # not listed (or unknown): bootstrap from the same plist
                r = steps.run('the pick reader bootstrap', self.host.run,
                              ['/bin/launchctl', 'bootstrap', 'gui/%d' % self.host.uid, pr['before']['plist']], timeout=CMD_S)
                pr['bootstrap_exit'] = getattr(r, 'returncode', None)
        pr['after'] = steps.run('reading the pick reader after its restore', pick_reader_state, self.host)
        pr['restored'] = pr['after'] is not None and pr['after']['listed'] == pr['before']['listed']
        return pr['restored']


class Steps:
    """Runs cleanup steps to their end: a BaseException from any step is named in the row's alerts and the following steps
    still run; the first one that is not an ordinary Exception (KeyboardInterrupt, SystemExit) is kept and `reraise` raises
    it again at the very end (addendum 2, item 1). An ordinary Exception stays an alert in capitals and exit 3, as before."""

    def __init__(self, row):
        self.row, self.kept = row, None

    def run(self, name, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BaseException as e:
            if self.kept is None and not isinstance(e, Exception):
                self.kept = e
            self.row['alerts'].append('%s DID NOT COMPLETE: %s%s' % (
                name.upper(), type(e).__name__, '' if isinstance(e, (KeyboardInterrupt, SystemExit)) else ': %s' % e))
            return None

    def reraise(self):
        if self.kept is not None:
            raise self.kept


class SignalGuard:
    """Addendum 3, item 2. Installed by `run` and `load-test` before the first switch-off: a TERM or a hang-up (a closed
    terminal, an ended session, a caller's timeout) raises OperatorSignalled, a Ctrl-C KeyboardInterrupt, each into the
    same controlled path. At most one signal ever raises: once one has, or once the cleanup has begun (`cleaning`), a
    further signal is only recorded in the row, so it never aborts the stop, the restore or the row. While `holding` (the
    one statement that spawns the launcher and binds its handle), a signal is kept and raised just after the binding."""

    def __init__(self, host, row):
        self.host, self.row, self.old = host, row, {}
        self.holding = self.cleaning = self.raised = False; self.pending = None
        self.stage = 'the run'          # addendum 4, F: the cleanup names each of its stages here
        row['signals_received'] = []

    def install(self):
        for sig in OPERATOR_SIGNALS:
            self.old[sig] = self.host.install_signal(sig, self.handler)
            if sig == signal.SIGHUP and self.old[sig] == signal.SIG_IGN:
                # started under `nohup`: the operator chose that a hang-up does not end the run; it stays ignored
                self.host.install_signal(sig, signal.SIG_IGN); self.row['hup_ignored'] = True

    def handler(self, signum, frame):
        name = signal.Signals(signum).name
        self.row['signals_received'].append({'signal': name, 'utc': utc(self.host.now()),
                                             'during': self.stage if self.cleaning or self.raised else 'the run'})
        if self.cleaning or self.raised:
            return
        exc = KeyboardInterrupt() if signum == signal.SIGINT else OperatorSignalled(name)
        if self.holding:
            self.pending = self.pending or exc; return
        self.raised = True
        raise exc

    def release(self):
        self.holding = False
        if self.pending is not None and not (self.cleaning or self.raised):
            exc, self.pending, self.raised = self.pending, None, True
            raise exc

    def restore(self):
        for sig, old in self.old.items():
            self.host.install_signal(sig, signal.SIG_DFL if old is None else old)


def _sync_folder(host, folder):
    fd = os.open(folder, os.O_RDONLY)
    try:
        host.fsync(fd)
    finally:
        os.close(fd)


class IntentFile:
    """Addendum 3, item 2: artifacts/mirofish/operator-intent.json, written and synced (a temporary file synced, renamed
    into place, the folder synced) BEFORE the first switch-off, so that what no handler can catch (a KILL, a crash, a power
    cut) is still visible: check, run and load-test refuse while it exists, and `recover` reads it. It is rewritten when the
    launcher's pid and start are known, and removed only after the restore and the row are done."""

    def __init__(self, host):
        self.host, self.path, self.data, self.written = host, host.intent, None, False

    def write(self, **fields):
        if self.data is None:
            self.data = {}
        self.data.update(fields)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(self.data, indent=2, sort_keys=True) + '\n'); f.flush(); self.host.fsync(f.fileno())
        os.replace(tmp, self.path); self.written = True
        _sync_folder(self.host, self.path.parent)

    def remove(self):
        self.path.unlink(); _sync_folder(self.host, self.path.parent)
        return True


class OperatorLock:
    """Addendum 4, D: one exclusive advisory lock (flock) on artifacts/mirofish/operator.lock, held by `run` and
    `load-test` from before their first changing action until after their row, and by `recover` while it works. It is
    taken without waiting; the kernel drops it when the holder ends, however it ends (a KILL included). The descriptor
    is not inherited by the launcher (os.open is non-inheritable and Popen closes descriptors). The file is never removed."""

    def __init__(self, host):
        self.path, self.fd = host.lock, None

    def take(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)

    def refusal(self):
        return ('ANOTHER OPERATOR COMMAND HOLDS THE LOCK %s: A RUN, A LOAD TEST OR A RECOVER IS STILL GOING ON; wait for it '
                'to end' % self.path)


# ============================================================================ the proof that a process is this run's
ROLES = ('launcher', 'observer')


def command_tokens(host, pid):
    """The words of one process's own command, read for the proof alone (never printed, logged, stored or put in an
    exception text); None when it could not be read: the read raised, or answered with no string or an empty one
    (addendum 7, B: whether the pid is gone or alive, the caller decides by its own ps read)."""
    try:
        text = host.proc_command(pid)
    except Exception:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    return text.split()


def names_role(tokens, work, role):
    """Addendum 4, A: whether the command names BOTH the staged candidate folder and the script of its role, the way
    this run started it: the launcher `... -B mirofish_native_startup.py --work <work> ...` (launch), the observer
    `... -B <work>/owned-source/observer-entry/mirofish_native_run.py` (src/mirofish_native_startup.py:163)."""
    w = str(work)
    pairs = set(zip(tokens, tokens[1:]))
    if role == 'launcher':
        return ('-B', LAUNCHER_SCRIPT) in pairs and ('--work', w) in pairs
    return ('-B', '%s/owned-source/observer-entry/%s' % (w, OBSERVER_SCRIPT)) in pairs


def command_names(host, pid, work, role):
    """True or False when the command was read and does or does not name the folder and the role's script; None when
    it could not be read. Only that leaves this function: the command's text is dropped here."""
    tokens = command_tokens(host, pid)
    return None if tokens is None else names_role(tokens, work, role)


def ancestors(host):
    """This process and every ancestor, by its parent pids, up to 1."""
    out = {host.self_pid, host.parent_pid, 0, 1}
    pid = host.parent_pid
    for _ in range(64):
        seen = observed_identity(host, pid) if type(pid) is int and pid > 1 else None
        if seen is None or seen['ppid'] in out:
            break
        out.add(seen['ppid']); pid = seen['ppid']
    return out


def job_pids(host):
    """The collector's and the pick reader's job pids, read from launchd by their fixed labels."""
    return {label: launchd_print(host, label)['pid'] for label in (COLLECTOR_LABEL, PICKS_LABEL)}


UNREADABLE = 'unreadable: '     # addendum 5: a proof that could not be made, against one made with the answer no


def proof(host, pid, start, role, work, guard):
    """(None, its identity) when the process is proven to be this run's `role`; else (which test failed, None), in
    words that never carry the command. `guard` holds the ancestors and the job pids. Every test must pass: the pid is
    above 1, not this process or an ancestor, not the collector's or the pick reader's job pid; its start was read and
    is the start held for it (when one is held); its own command was read and names the staged folder and the script of
    its role. A start or a command that could not be read is UNREADABLE (the proof could not be made), never a no
    (addendum 7, B); only ps's own "no such process" is "gone"."""
    if type(pid) is not int or pid <= 1:
        return 'pid %s is not above 1' % repr(pid)[:40], None
    if pid in guard['ancestors']:
        return 'pid %d is this tool or one of its ancestors' % pid, None
    for label, job in guard['jobs'].items():
        if job == pid:
            return 'pid %d is the pid of the job %s (%s)' % (
                pid, label, 'the collector' if label == COLLECTOR_LABEL else 'the pick reader'), None
    seen, state = read_identity(host, pid)
    if state == 'gone':
        return 'gone', None
    if seen is None:
        return UNREADABLE + 'pid %d: its start could not be read' % pid, None
    if start is not None and seen['start'] != start:
        return 'pid %d has another start time than the one recorded' % pid, None
    names = command_names(host, pid, work, role)
    if names is None:                     # ended since its start was read, or alive with a command that cannot be read
        return ('gone' if read_identity(host, pid)[1] == 'gone' else UNREADABLE + 'pid %d: its command could not be read' % pid), None
    if not names:
        return 'pid %d: its command does not name the staged folder and %s' % (pid, ROLE_SCRIPTS[role]), None
    return None, seen


def proof_failure(host, pid, start, role, work, guard):
    return proof(host, pid, start, role, work, guard)[0]


def intent_start(host, command, row, summary):
    """What is about to be switched off and its before-state, the work folder, this tool's own pid and start, the time."""
    try:
        me = observed_identity(host, host.self_pid)
    except Exception:
        me = None
    pr = summary['pick_reader']
    return dict(command=command, utc=utc(host.now()), tool={'pid': host.self_pid, 'start': me and me['start']},
                profile=row.get('profile'), scripts=dict(ROLE_SCRIPTS), launch=None,
                pick_reader={'label': PICKS_LABEL, 'before': {'listed': pr['listed'], 'plist': pr['plist'], 'state': pr['state']}},
                models_to_unload=[o['instance_id'] for o in summary['models']['others']], models_note=NOT_RELOADED,
                work=row.get('staged_folder'), twin=row.get('twin_folder'), launcher=None, record=str(host.record))


# ============================================================================ the pipeline's own steps
def pipeline_env():
    return dict(os.environ, TMPDIR=str(PIPELINE_TMP))


def fresh_folder(host, label):
    t = _dt.datetime.fromtimestamp(host.now(), _dt.timezone.utc)
    work = host.crew / ('valedia-native-%s-%s-op%s' % (t.strftime('%Y%m%d'), label, t.strftime('%H%M%S')))
    if work.exists() or Path(str(work) + '-twin').exists():
        raise OperatorError('the staged folder already exists: %s' % work)
    return work


def stage(host, profile, flags, work):
    """tools/mirofish_native_stage.py --stage, as docs/mirofish-swarm.md runs it; the candidate's hash."""
    argv = ['/usr/bin/nice', '-n', '15', str(PIPELINE_PY), '-B', str(TOOLS / 'mirofish_native_stage.py'), '--stage',
            '--profile', profile, *flags, '--work', str(work)]
    r = host.run(argv, cwd=str(TOOLS), env=pipeline_env(), timeout=None)
    if r.returncode != 0:
        raise OperatorError('staging %s refused (exit %d): %s' % (work.name, r.returncode, (r.stderr or '').strip()[-400:]))
    try:
        out = json.loads([line for line in (r.stdout or '').splitlines() if line.strip()][-1])
    except (IndexError, ValueError):
        raise OperatorError('staging %s printed no result' % work.name)
    manifest = work / 'builder/candidate-manifest.json'
    if out.get('status') != 'STAGED_NOT_AUTHORIZED_NO_SERVICES' or Path(out.get('work', '')) != work or not manifest.is_file():
        raise OperatorError('staging %s did not stage: %s' % (work.name, out.get('status')))
    if sha256(manifest) != out.get('candidate_sha256'):
        raise OperatorError('the staged candidate manifest of %s does not match its printed hash' % work.name)
    return out['candidate_sha256']


def sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def staged_threshold(work):
    """The staged folder's own free-memory start threshold (the manifest's model_profile; the reviewed 65 without a model)."""
    manifest = json.loads((work / 'builder/candidate-manifest.json').read_text())
    value = ((manifest.get('model_profile') or {}).get('start_threshold') or {}).get('percent_free')
    return value if type(value) is int else LIMITS.REVIEWED_START_THRESHOLD


LATE_REFUSAL = ('a model was loaded after the announcement: %s; the start check and what the owner was told no longer hold, '
                'so nothing is launched (it is not unloaded: only announced models are)')


UNREAD_REFUSAL = ('THE MODEL LIST COULD NOT BE READ (%s): whether a model was loaded after the announcement cannot be '
                  'told, so nothing is launched')


def late_models(host, row, now=None):
    """Addendum 6: every loaded instance that is neither the decision instance, the embedding instance nor an mf-native
    one, once the announced models were unloaded, appeared after the announcement. Each is named once in the row
    (`loaded_after_announcement`, key and id) and, after the spawn, in a capitals alert; it is never unloaded. `now` is
    a list of such instances. Returns the new ones."""
    if now is None:
        now = models_state(host)['others']
    known = {m['instance_id'] for m in row['loaded_after_announcement']}
    new = [{'instance_id': o['instance_id'], 'key': o['key']} for o in now if o['instance_id'] not in known]
    row['loaded_after_announcement'].extend(new)
    return new


def alert_late(host, row, state=None):
    """After the spawn (or the load call): one read of the list (or `state`, a models_state already read). A list that
    could not be read (addendum 7, C) is a capitals alert at the first such check of a stretch, and the row's
    `model_list_unread` stays True until a later check reads the list: the run counts only if the last check, the one at
    its end, read it."""
    state = models_state(host) if state is None else state
    if not state['reachable']:
        if not row.get('model_list_unread'):
            row['alerts'].append('THE MODEL LIST COULD NOT BE READ AT %s (%s): A MODEL LOADED NOW WOULD NOT BE SEEN; THE %s '
                                 'COUNTS ONLY IF A LATER CHECK READS IT' % (
                                     utc(host.now()), state.get('unread_why'), 'RUN' if row['command'] == 'run' else 'TEST'))
        row['model_list_unread'] = True
        row.setdefault('model_list_unread_at', []).append(utc(host.now()))
        return []
    row['model_list_unread'] = False
    new = late_models(host, row, state['others'])
    if new:
        row['alerts'].append('MODEL LOADED AFTER THE ANNOUNCEMENT, LEFT ALONE (NOT UNLOADED): %s; THE %s DOES NOT COUNT' % (
            ', '.join('%s (%s)' % (m['instance_id'], m['key']) for m in new), 'RUN' if row['command'] == 'run' else 'TEST'))
    return new


def read_before_spawn(host, row):
    """Addendum 7, C and D: a read of the model list before the spawn (or the load call). A list that could not be read,
    or that breaks a protected pair, is a refusal: nothing is launched (or loaded)."""
    state = models_state(host)
    if not state['reachable']:
        row['model_list_unread'] = True
        raise Refused(UNREAD_REFUSAL % state.get('unread_why'))
    if state['protected_wrong']:
        raise Refused(PROTECTED_REFUSAL % '; '.join(state['protected_wrong']))
    return state


def refuse_if_late(host, row):
    """Addendum 6: just before the gate and the spawn (a run) or the load call (a load test)."""
    new = late_models(host, row, read_before_spawn(host, row)['others'])
    if new:
        raise Refused(LATE_REFUSAL % ', '.join('%s (%s)' % (m['instance_id'], m['key']) for m in new))


def settle_after_unloads(host, row):
    """Addendum 2, item 5 (d): after models were unloaded, wait SETTLE_S before the free reading. LM Studio answers the
    unload once it has released the instance, but the kernel's free figure follows as the pages are reclaimed, over a few
    seconds; read at once, it under-reads and refuses a start that would pass. 15 s is a few times that lag and about 1%
    of the run's window. Nothing unloaded: nothing to wait for."""
    if any(m.get('unloaded') for m in row['switch_offs']['models_unloaded']):
        host.sleep(SETTLE_S)


def require_free(host, work, row=None):
    threshold = staged_threshold(work); free = memory(host)['free_percent']
    if free is None or free < threshold:
        unloaded = [m['instance_id'] for m in ((row or {}).get('switch_offs') or {}).get('models_unloaded', []) if m.get('unloaded')]
        raise Refused('%s%% free is under the staged start threshold %d%% (the pipeline would refuse at its start); %s' % (
            free, threshold, ('unloaded %s, %s' % (', '.join(unloaded), NOT_RELOADED)) if unloaded else 'no model was unloaded'))
    return threshold, free


def preflight(host, twin):
    argv = [str(PIPELINE_PY), '-B', 'mirofish_native_startup.py', '--work', str(twin), '--preflight']
    r = host.run(argv, cwd=str(twin / 'owned-source/observer-entry'), env=pipeline_env(), timeout=PREFLIGHT_S)
    if r.returncode != 0:
        raise OperatorError('the twin\'s preflight failed (exit %d); see %s' % (r.returncode, twin / 'builder/startup-preflight.json'))


def write_gate(host, work, candidate, announced):
    t = host.now(); gate = work / GATE_NAME
    value = {'outcome': OUTCOME, 'action': 'one-native-attempt', 'candidate_sha256': candidate,
             'expires_at_utc': utc(t + GATE_S), 'written_utc': utc(t), 'written_by': 'tools/mirofish_operator.py', 'announced': announced}
    fd = os.open(gate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(value) + '\n'); f.flush(); os.fsync(f.fileno())
    return gate


def require_stoppable(work):
    """Addendum 1, ruling 5: a run that cannot be stopped is not started. The staged observer's run module must contain the
    code that writes builder/observer-identity.json (searched by that file name)."""
    run = work / 'owned-source/observer-entry/mirofish_native_run.py'
    try:
        text = run.read_text()
    except OSError as e:
        raise Refused('the staged pipeline has no readable %s (%s): not launched' % (run, type(e).__name__))
    if IDENTITY_FILE not in text:
        raise Refused('the staged pipeline revision is too old: its mirofish_native_run.py never writes builder/%s, so the '
                      'run could not be stopped the documented way; not launched' % IDENTITY_FILE)


def staged_deadline(work):
    """Addendum 3, item 5: the watch deadline from the STAGED folder, not from the repository. The stager writes the
    profile's wall into the staged startup script, `deadline=time.monotonic()+(30 if preflight else N)`
    (src/mirofish_native_startup.py:145, edited by tools/mirofish_native_stage.py swarm_startup_bytes), and into the
    manifest's uniform_policy.wall_seconds. The startup script's N is the launcher's own deadline and is the one used; the
    manifest must say the same, or nothing is switched off."""
    startup = work / 'owned-source/observer-entry/mirofish_native_startup.py'
    try:
        values = sorted({int(v) for v in STARTUP_DEADLINE.findall(startup.read_text())})
    except OSError as e:
        raise Refused('the staged folder has no readable startup script %s (%s): no staged deadline; not launched' % (
            startup, type(e).__name__))
    if len(values) != 1:
        raise Refused('the staged startup script %s holds %s run deadline(s) `(30 if preflight else N)`, not exactly one: no '
                      'staged deadline; not launched' % (startup, values or 'no'))
    wall = values[0]
    manifest = work / 'builder/candidate-manifest.json'
    try:
        policy = json.loads(manifest.read_text()).get('uniform_policy')
        recorded = policy.get('wall_seconds') if isinstance(policy, dict) else None
    except (OSError, ValueError, AttributeError) as e:
        raise Refused('the staged manifest %s is not readable (%s): no staged deadline; not launched' % (manifest, type(e).__name__))
    if type(recorded) is not int or recorded != wall:
        raise Refused('the staged deadline %d s (%s) and the staged manifest\'s uniform_policy.wall_seconds %s differ: not launched'
                      % (wall, startup.name, recorded))
    return {'staged_wall_s': wall, 'source': '%s: `(30 if preflight else %d)`' % (startup, wall),
            'manifest_wall_seconds': recorded, 'watch_deadline_s': wall + CLEANUP_S}


def launch(host, work, gate, row, stopper, guard, intent):
    """Addendum 3, item 1. The launcher, started by this tool in its own session and process group (Host.spawn). Its handle
    is bound to the stop path's state (stopper.proc) in the very statement that spawns it, with every signal held for that
    statement, so nothing runs between the spawn and the binding: from then on the launcher is this tool's own unreaped
    child, and the stop path can always reach it through the handle. Its start and group are then read, with retries;
    a start that cannot be read leaves the handle as the stop route."""
    argv = [str(PIPELINE_PY), '-B', LAUNCHER_SCRIPT, '--work', str(work), '--gate', str(gate)]
    # addendum 4, B: synced before the spawn: the staged folder and the two scripts (already in the intent) prove a
    # survivor even if the tool dies before the launcher's pid is written
    intent.write(launch={'spawning_utc': utc(host.now())})
    guard.holding = True
    try:
        stopper.proc = host.spawn(argv, cwd=str(work / 'owned-source/observer-entry'), env=pipeline_env())
        row['launched'] = True
        row['launcher'] = {'pid': stopper.proc.pid, 'start': None, 'pgid': None, 'own_group': None, 'read_errors': []}
        intent.write(launcher={'pid': stopper.proc.pid, 'start': None, 'pgid': None})
    finally:
        guard.holding = False
    guard.release()
    seen = None
    for attempt in range(LAUNCHER_PS_TRIES):
        try:
            seen = observed_identity(host, stopper.proc.pid)
        except Exception as e:          # a KeyboardInterrupt or a signal goes on to the one stop path
            row['launcher']['read_errors'].append(type(e).__name__)
        if seen is not None:
            break
        if attempt + 1 < LAUNCHER_PS_TRIES:
            host.sleep(SAMPLE_S)
    if seen is not None:
        row['launcher'].update(start=seen['start'], pgid=seen['pgid'], own_group=seen['pgid'] == stopper.proc.pid)
    else:
        row['launcher']['start_note'] = ('start not readable after %d tries: a stop goes through the handle of this tool\'s '
                                         'own unreaped child' % LAUNCHER_PS_TRIES)
    intent.write(launcher={k: row['launcher'][k] for k in ('pid', 'start', 'pgid')})
    return stopper.proc


class Stopper:
    """At most one stop signal. TERM to the observer named in <work>/builder/observer-identity.json, only when it passes
    observer_unusable (well formed, alive with that start and group, a CHILD of the launcher this tool started, not this
    tool, its parent, 0 or 1). An identity that fails for ANY reason, or no file, is "no usable identity": from
    IDENTITY_WAIT_S after the launch, SIGINT to the launcher this tool started (correction A, addendum 2 item 3), only when it
    was started in its own group and its pid, start and group still match what was recorded at launch. The launcher's
    KeyboardInterrupt path then stops the observer group the pipeline's own way; a TERM would kill it without that cleanup.
    Never a group signal, never on a pid alone."""

    def __init__(self, host, work, row, t_launch):
        self.host, self.path, self.row, self.t_launch = host, work / 'builder' / IDENTITY_FILE, row, t_launch
        self.rec = row['stop'] = {'wanted': False, 'signalled': False, 'by': None, 'pid': None, 'pgid': None, 'utc': None,
                                  'not_signalled': None}
        self.sent_t = None
        self.proc = None            # the launcher's handle, bound by `launch` in the statement that spawns it

    def launcher_why_not(self):
        """None when the launcher can be stopped through its handle: spawned, not yet ended (an unreaped child's pid cannot
        belong to another process), and, when its start was read, still that start and group."""
        if self.proc is None:
            return 'the launcher was not spawned'
        if self.proc.poll() is not None:
            return 'the launcher has ended'
        launcher = self.row.get('launcher') or {}
        if not launcher.get('start'):
            return None
        return launcher_mismatch(self.host, launcher)

    def attempt(self):
        self.rec['wanted'] = True
        if self.rec['signalled']:
            return True
        try:
            identity = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            return self._by_launcher('no readable observer identity (%s)' % type(e).__name__)
        why = observer_unusable(self.host, identity, self.proc.pid if self.proc is not None else None, self.launcher_why_not())
        if why:
            return self._by_launcher('no usable observer identity: ' + why)
        self.host.send_signal(identity['pid'], signal.SIGTERM)
        self.sent_t = self.host.now()
        self.rec.update(signalled=True, by='observer', pid=identity['pid'], utc=utc(self.sent_t), not_signalled=None)
        return True

    def _by_launcher(self, why):
        if self.host.now() < self.t_launch + IDENTITY_WAIT_S:
            self.rec['not_signalled'] = why + '; waiting up to %d s after the launch for it' % IDENTITY_WAIT_S; return False
        moved = self.launcher_why_not()
        pid = self.proc.pid if self.proc is not None else None
        if moved or pid <= 1 or pid in (self.host.self_pid, self.host.parent_pid):
            self.rec['not_signalled'] = why + ', and ' + (moved or 'the launcher pid is this tool, its parent or not above 1')
            return False
        self.proc.send_signal(LAUNCHER_STOP_SIGNAL)      # through the handle (addendum 3, item 1): Popen polls first
        self.sent_t = self.host.now()
        self.rec.update(signalled=True, by='launcher interrupt', signal='SIGINT', pid=pid,
                        pgid=(self.row.get('launcher') or {}).get('pgid'), utc=utc(self.sent_t), not_signalled=None,
                        why_launcher=why)
        return True


def after_interrupt_check(host, stopper, row):
    """Correction A: after a launcher interrupt, the same cleanup time as for the normal stop (STOP_WAIT_S from the signal),
    then no mf-native-* instance may be loaded. One that is, is said in capitals; the tool unloads nothing it did not load."""
    while host.now() < stopper.sent_t + STOP_WAIT_S:
        host.sleep(min(WATCH_S, stopper.sent_t + STOP_WAIT_S - host.now()))
    native = models_state(host)['native_loaded']
    stopper.rec['after_check'] = {'utc': utc(host.now()), 'seconds_after_interrupt': round(host.now() - stopper.sent_t, 1),
                                  'native_loaded': native}
    if native:
        row['alerts'].append('MF-NATIVE INSTANCE STILL LOADED AFTER THE LAUNCHER INTERRUPT: %s (THE TOOL DID NOT LOAD IT AND '
                             'DOES NOT UNLOAD IT)' % ', '.join(native))


def wait_after_stop(host, proc, stopper, row):
    """After a stop is wanted: wait for the launcher to end (the pipeline's own cleanup), STOP_WAIT_S after the signal at most;
    with no signal possible, IDENTITY_WAIT_S + STOP_WAIT_S at most (never an endless wait)."""
    t0 = host.now()
    while proc.poll() is None:
        if stopper.sent_t is not None and host.now() > stopper.sent_t + STOP_WAIT_S:
            row['alerts'].append('PIPELINE CLEANUP NOT SEEN WITHIN %d S OF THE STOP SIGNAL: THE LAUNCHER WAS LEFT RUNNING' % STOP_WAIT_S)
            return None
        if stopper.sent_t is None and host.now() > t0 + IDENTITY_WAIT_S + STOP_WAIT_S:
            row['alerts'].append('THE RUN COULD NOT BE SIGNALLED: THE LAUNCHER WAS LEFT RUNNING')
            return None
        host.sleep(WATCH_S)
        if not stopper.rec['signalled']:
            stopper.attempt()
    return proc.poll()


def pipeline_outcome(work, plan, launcher_exit=None):
    out = {'receipt_found': False, 'receipt_readable': False, 'complete': None, 'failure': None, 'near_neutral': False,
           'calls': None, 'failed_calls': None, 'refused_calls': None, 'swaps': None, 'swaps_clean': None, 'cleanup_proven': None,
           'report_usable': None, 'memory_stop': None, 'launcher_exit': launcher_exit}
    path = work / 'builder/native-run-receipt.json'
    if not path.is_file():
        return out, {}
    out['receipt_found'] = True
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        out['failure'] = 'the receipt is not readable: %s' % type(e).__name__
        return out, {}
    if not isinstance(receipt, dict):
        out['failure'] = 'the receipt is not a JSON object'
        return out, {}
    wrong = receipt_type_errors(receipt)
    if wrong:                   # addendum 3, item 4: a wrongly typed field makes the receipt unreadable, never an exception
        out['failure'] = 'the receipt field%s %s ha%s the wrong type' % (
            's' if len(wrong) > 1 else '', ', '.join(wrong), 've' if len(wrong) > 1 else 's')
        return out, {}
    raw = receipt.get('failure')
    failure = raw
    # Addendum 2, item 4: the one failure that is a result, not a fault: the derived report too close to neutral
    # (src/mirofish_provenance.py:437 raises Refused("derived report not usable evidence"); mirofish_native_run.py:806
    # stores {'type': type name, 'reason': str(exc)}). Exactly that type and that reason string, nothing else.
    near = (failure is not None and failure.get('type') == NEAR_NEUTRAL_TYPE and type(failure.get('reason')) is str
            and failure['reason'] == NEAR_NEUTRAL_REASON)
    out.update(receipt_readable=True, complete=receipt.get('complete'), cleanup_proven=receipt.get('cleanup_proven'),
               failure=None if raw is None else str(raw['reason'] if raw.get('reason') is not None else raw)[:500], near_neutral=near,
               report_usable=receipt.get('paper_handoff') is True)
    reason = out['failure'] or ''
    out['memory_stop'] = bool(receipt.get('resource_failures') or receipt.get('resource_failure_samples')
                              or 'memory free threshold' in reason or 'RSS upper bound' in reason)
    ledger = work / 'builder/gateway-ledger.json'
    try:
        calls = (json.loads(ledger.read_text()) or {}).get('calls') if ledger.is_file() else None
    except (OSError, ValueError, AttributeError):
        calls = None                       # an unreadable ledger leaves the call counts null: not clean
    if isinstance(calls, list) and not all(type(c) is dict and (c.get('state') is None or type(c.get('state')) is str)
                                           for c in calls):
        out['ledger_failure'] = 'a ledger call or its state has the wrong type'   # addendum 4, E: not clean, no exception
        calls = None
    if isinstance(calls, list):
        states = Counter(c.get('state') for c in calls)
        out.update(calls=len(calls), refused_calls=states.get('refused_before_forward', 0),
                   failed_calls=sum(n for s, n in states.items() if s not in ('complete', 'refused_before_forward')))
    events = receipt.get('events') or []
    if plan is not None and 'setup' in plan:
        swapped = {e.get('boundary') for e in events if e.get('event') == 'model_swap'}
        probed = {e.get('boundary') for e in events if e.get('event') == 'post_swap_compatibility_probes_passed'}
        out['swaps'] = {'model_swap': sorted(b for b in swapped if b), 'probes_passed': sorted(b for b in probed if b)}
        out['swaps_clean'] = swapped >= {'B1', 'B2'} and probed >= {'B1', 'B2'}
    else:
        out['swaps'] = 'not applicable: one model'; out['swaps_clean'] = True
    return out, receipt


RECEIPT_TYPES = (       # every receipt field pipeline_outcome and memory_outcome walk; absent is null (not clean), never wrong
    ('complete', (bool,)), ('cleanup_proven', (bool,)), ('paper_handoff', (bool,)), ('failure', (dict,)),
    ('events', (list,)), ('resource_failures', (list,)), ('resource_failure_samples', (list,)), ('resource', (dict,)))


def receipt_type_errors(receipt):
    wrong = [k for k, types_ in RECEIPT_TYPES if k in receipt and receipt[k] is not None and type(receipt[k]) not in types_]
    if type(receipt.get('events')) is list:
        if not all(type(e) is dict for e in receipt['events']):
            wrong.append('events (an entry is not an object)')
        elif not all(e.get(k) is None or type(e.get(k)) is str for e in receipt['events'] for k in ('event', 'boundary')):
            wrong.append('events (an event or boundary is not a string)')      # addendum 4, E
    return wrong


def memory_outcome(receipt, swap_start, swap_end, own):
    res = receipt.get('resource') if isinstance(receipt.get('resource'), dict) else {}
    out = {'swap_start_bytes': swap_start, 'swap_end_bytes': swap_end, 'operator_lowest_free_percent_30s': own['lowest'],
           'operator_max_pressure_level': own['max_pressure'], 'notes': []}
    for key in ('lowest_sustained_free_percent_10s', 'max_pressure_level', 'co_resident_rss_bytes_max'):
        out[key] = res.get(key)
        if key not in res:
            out['notes'].append('receipt field resource.%s absent: null' % key)
    return out


def pipeline_clean(p):
    """Addendum 2, item 4: a readable receipt; no failed and no refused call; cleanup proven; both swaps clean; no stop on
    the memory rule; and EITHER complete with no failure and the launcher's exit 0, OR not complete for exactly the
    near-neutral reason with the launcher's exit 1 (the observer's `return 0 if complete else 1`, passed on by the
    launcher). A missing receipt, a zero exit with complete false, any other reason: not clean."""
    base = (p.get('receipt_found') is True and p.get('receipt_readable') is True and p.get('failed_calls') == 0
            and p.get('refused_calls') == 0 and p.get('memory_stop') is False and p.get('swaps_clean') is True
            and p.get('cleanup_proven') is True)
    done = p.get('complete') is True and p.get('failure') is None and p.get('launcher_exit') == 0
    neutral = p.get('complete') is False and p.get('near_neutral') is True and p.get('launcher_exit') == 1
    return bool(base and (done or neutral))


def verdicts(collector, pipeline, memory):
    low = memory.get('lowest_sustained_free_percent_10s')
    st = collector.get('statuses') or {}       # correction B: by the count of each status, which the record carries
    return {'collector_unharmed': (collector['breach'] is None and collector['over_8s'] == 0
                                   and not any(st.get(k, 0) for k in HARMFUL_STATUSES) and st.get('error', 0) < HARMFUL_ERRORS),
            'pipeline_clean': pipeline_clean(pipeline),
            'room_to_spare': None if type(low) not in (int, float) else low >= STOP_FLOOR + ROOM_POINTS}


def post_state(host, row):
    m = models_state(host)
    row['post'] = {'decision_instance_right': m['decision_instance']['loaded_and_right'], 'why': m['decision_instance']['why'],
                   'native_loaded': m['native_loaded'], 'others': m['others'], 'list_read': m['reachable'],
                   'protected_wrong': m['protected_wrong'], 'utc': utc(host.now())}
    if not m['decision_instance']['loaded_and_right']:
        row['alerts'].append('DECISION INSTANCE NOT LOADED AND RIGHT: %s (THE TOOL DOES NOT RELOAD IT; THE COLLECTOR\'S KEEPER DOES)'
                             % m['decision_instance']['why'])
    if m['native_loaded']:
        row['alerts'].append('MF-NATIVE INSTANCE STILL LOADED: %s' % ', '.join(m['native_loaded']))
    return m


# ============================================================================ the record
def append_record(path, row, fsync=os.fsync):
    """One JSON line appended and synced. A file that does not end with a newline (a torn last line) gets one first, so
    the new row is never glued to it (addendum 2, item 6)."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    torn = False
    if not new and path.stat().st_size > 0:
        with path.open('rb') as f:
            f.seek(-1, os.SEEK_END); torn = f.read(1) != b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    with os.fdopen(fd, 'a') as f:
        f.write(('\n' if torn else '') + json.dumps(row, sort_keys=True) + '\n'); f.flush(); fsync(f.fileno())
    if new:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            fsync(dfd)
        finally:
            os.close(dfd)


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(value, indent=2, sort_keys=True) + '\n'); f.flush(); os.fsync(f.fileno())


def read_rows(path):
    """(rows, damaged): every line that is a JSON object, and the count of lines that are not (skipped; never raises on
    one). A damaged line is never a run: it neither counts nor resets a streak (addendum 2, item 6)."""
    path = Path(path)
    rows, damaged = [], 0
    try:
        text = path.read_text(errors='replace') if path.is_file() else ''
    except OSError:
        return rows, damaged
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            value = None
        if isinstance(value, dict):
            rows.append(value)
        else:
            damaged += 1
    return rows, damaged


def streaks(rows):
    out = {}
    for key, profile, target in STREAKS:
        n = 0
        for r in rows:
            if r.get('command') in ('run', 'recover') and r.get('launched') is True and r.get('profile') == profile:
                n = n + 1 if r.get('counts') is True else 0
        out[key] = {'profile': profile, 'streak': n, 'target': target, 'reached': n >= target}
    return out


def streak_lines(rows):
    return ['%s participants: %d in a row (target %d)%s' % (k, v['streak'], v['target'], ', REACHED' if v['reached'] else '')
            for k, v in streaks(rows).items()]


# ============================================================================ run
def new_row(command, args, plan, t):
    return {'command': command, 'utc_start': utc(t), 'utc_end': None, 'profile': getattr(args, 'profile', None), 'plan': plan,
            'announced': args.announced, 'announced_note': 'the lead\'s statement that the owner was told; the tool cannot check it',
            'launched': False, 'refused': None, 'error': None, 'stopped': None, 'staged_folder': None, 'twin_folder': None,
            'switch_offs': None, 'deadline': None, 'pipeline': None, 'memory': None, 'collector': None, 'stop': None, 'post': None,
            'verdicts': None, 'counts': False, 'loaded_after_announcement': [], 'model_list_unread': False, 'alerts': []}


def watch(host, row, stopper, t_start, t_launch, wall_s, own):
    """Every WATCH_S: the launcher, the collector since t_start, the memory. A breach (or an overrun of the STAGED wall
    clock, addendum 3, item 5) stops the run once; the loop then waits for the pipeline's own cleanup."""
    first = None; proc = stopper.proc
    while proc.poll() is None:
        host.sleep(WATCH_S)
        if proc.poll() is not None:
            break
        alert_late(host, row)                    # addendum 6: after the spawn, an alert at once; the stop rule unchanged
        m = memory(host)
        if m['free_percent'] is not None:
            own['lowest'] = m['free_percent'] if own['lowest'] is None else min(own['lowest'], m['free_percent'])
        if m['pressure_level'] is not None:
            own['max_pressure'] = max(own['max_pressure'] or 0, m['pressure_level'])
        if (not row.get('identity_alerted') and host.now() >= t_launch + IDENTITY_WAIT_S
                and not (stopper.path.is_file() or stopper.rec['signalled'])):
            row['identity_alerted'] = True
            row['alerts'].append('OBSERVER IDENTITY FILE NOT WRITTEN WITHIN %d S OF LAUNCH: A BREACH WILL INTERRUPT THE '
                                 'LAUNCHER (SIGINT, THROUGH ITS HANDLE) INSTEAD' % IDENTITY_WAIT_S)
        deadline = t_launch + wall_s + CLEANUP_S         # the staged wall time plus the cleanup (addendum 2, 3; addendum 3, 5)
        if first is None:
            h = collector_health(host, since=t_start)
            if h['breach']:
                first = h['breach']; row['stopped'] = 'collector breach'; row['first_breach'] = first
            elif host.now() >= deadline:
                first = 'wall exceeded'; row['stopped'] = 'wall exceeded'
        if first is not None:
            if not stopper.rec['signalled']:
                stopper.attempt()
            if stopper.sent_t is not None and host.now() > stopper.sent_t + STOP_WAIT_S:
                row['alerts'].append('PIPELINE CLEANUP NOT SEEN WITHIN %d S OF THE STOP SIGNAL: THE LAUNCHER WAS LEFT RUNNING' % STOP_WAIT_S)
                return None
        # the hard end of the watch, whatever could or could not be signalled: never an endless watch
        if stopper.sent_t is None and host.now() >= max(deadline, t_launch + IDENTITY_WAIT_S) + STOP_WAIT_S:
            if row['stopped'] is None:
                row['stopped'] = 'wall exceeded'
            row['alerts'].append('THE RUN COULD NOT BE SIGNALLED: THE LAUNCHER WAS LEFT RUNNING')
            return None
    return proc.poll()


def summary_lines(row):
    p, m, c = row.get('pipeline') or {}, row.get('memory') or {}, row.get('collector') or {}
    pr = (row.get('switch_offs') or {}).get('pick_reader') or {}
    unloaded = [x['instance_id'] for x in (row.get('switch_offs') or {}).get('models_unloaded', []) if x.get('unloaded')]
    post = row.get('post') or {}
    v = row.get('verdicts') or {}
    outcome = (('REFUSED: ' + row['refused']) if row['refused'] else ('ERROR: ' + row['error']) if row['error']
               else ('STOPPED: ' + row['stopped']) if row['stopped'] else 'ran to its end')
    return ['  1 %s %s on %s: %s' % (row['command'], row['profile'], plan_label(row['plan']), outcome),
            '  2 utc %s to %s' % (row['utc_start'], row['utc_end']),
            '  3 staged folder %s (twin %s)' % (row['staged_folder'], row['twin_folder']),
            '  4 pipeline: complete %s, reason %s, calls %s (failed %s, refused %s), swaps %s, cleanup proven %s, report usable %s' % (
                p.get('complete'), p.get('failure'), p.get('calls'), p.get('failed_calls'), p.get('refused_calls'),
                p.get('swaps_clean'), p.get('cleanup_proven'), p.get('report_usable')),
            '  5 memory: lowest sustained free over 10 s %s%%, max pressure level %s, swap %s -> %s bytes' % (
                m.get('lowest_sustained_free_percent_10s'), m.get('max_pressure_level'), m.get('swap_start_bytes'), m.get('swap_end_bytes')),
            '  6 collector: %s decisions %s, highest latency %s s, over 8 s %s, breach %s' % (
                c.get('decisions'), c.get('statuses'), c.get('highest_latency_s'), c.get('over_8s'), c.get('breach')),
            '  7 switch-offs: pick reader %s -> %s (restored %s); unloaded %s, %s' % (
                (pr.get('before') or {}).get('listed'), (pr.get('after') or {}).get('listed'), pr.get('restored'),
                ', '.join(unloaded) or 'none', NOT_RELOADED),
            '  8 after: mf-native loaded %s; decision instance loaded and right %s' % (post.get('native_loaded'), post.get('decision_instance_right')),
            '  9 verdicts: collector_unharmed %s, pipeline_clean %s, room_to_spare %s; COUNTS %s' % (
                v.get('collector_unharmed'), v.get('pipeline_clean'), v.get('room_to_spare'), row['counts']),
            '  10 %s; announced (stored verbatim, the tool cannot check it): %s' % (
                '; '.join(row['alerts']) if row['alerts'] else 'no alerts', row['announced'])]


def plan_of(args):
    if args.model and (args.setup_model or args.turns_model):
        raise Refused('--model names one model; a plan is --setup-model with --turns-model and no --model')
    if args.model:
        return {'model': args.model}, ['--model', args.model]
    if args.setup_model or args.turns_model:
        if not (args.setup_model and args.turns_model):
            raise Refused('a plan needs both --setup-model and --turns-model')
        if args.setup_model == args.turns_model:          # addendum 7, E
            raise Refused('--setup-model and --turns-model name the same model %s: one model is --model' % args.setup_model)
        return {'setup': args.setup_model, 'turns': args.turns_model}, ['--setup-model', args.setup_model, '--turns-model', args.turns_model]
    return None, []


def cmd_run(host, args):
    plan, flags = plan_of(args)
    say = host.out; t_start = host.now(); row = new_row('run', args, plan, t_start)
    s = do_check(host, args.profile, plan, 'run')
    row['check'] = s
    if args.dry_run:
        for line in check_lines(s):
            say(line)
        say('dry run: would stage %s and its twin, read the staged deadline, write the intent file %s, boot out %s, unload %s, '
            'preflight the twin, write the gate, launch, watch every %d s, restore, append the record row, remove the intent '
            'file. Nothing was changed.' % (
                args.profile, host.intent, PICKS_LABEL if s['pick_reader']['listed'] else 'nothing',
                ', '.join(o['instance_id'] for o in s['models']['others']) or 'nothing', WATCH_S))
        return 2 if s['refusals'] else 0
    lock = OperatorLock(host)
    if not lock.take():           # addendum 4, D; addendum 7, E: refused by the lock, it writes nothing (no row, no file)
        say('refused: ' + lock.refusal())
        return 2
    switch = SwitchOffs(host, row); stopper = None; work = None; own = {'lowest': None, 'max_pressure': None}
    swap_start = None; kept = None
    guard = SignalGuard(host, row); intent = IntentFile(host)
    try:
        # Addendum 3, item 2: the TERM and HUP handlers before anything is switched off. Addendum 2, item 5: (a) every
        # refusal that needs no switch-off; (b) staging, which switches nothing off, the old-pipeline refusal and the
        # staged deadline (addendum 3, item 5); (c) the intent file, then the switch-offs; (d) the free reading after the
        # unloads settled; (e) the twin's preflight; (f) the gate and the launch.
        guard.install()
        if s['refusals']:
            raise Refused('; '.join(s['refusals']))
        work = fresh_folder(host, args.profile); twin = Path(str(work) + '-twin')
        row['staged_folder'], row['twin_folder'] = str(work), str(twin)
        candidate = stage(host, args.profile, flags, work)
        stage(host, args.profile, flags, twin)
        require_stoppable(work)
        deadline = row['deadline'] = staged_deadline(work)
        # addendum 4, F: the announcement at the start states the STAGED wall, not the limits table's
        row['announcement'] = announcement('run', args.profile, plan, {'expected_s': s['durations']['expected_s'],
                                                                      'wall_s': deadline['staged_wall_s']}, s, host.now())
        say('check passed and staged; expected %d s (tools/mirofish_swarm_limits.py, %s on %s), staged deadline %d s (%s), '
            'watched to %d s after the launch; switching off: %s' % (
                s['durations']['expected_s'], args.profile, plan_label(plan), deadline['staged_wall_s'], deadline['source'],
                deadline['watch_deadline_s'], row['announcement']))
        intent.write(**intent_start(host, 'run', row, s))
        switch.apply(s)
        settle_after_unloads(host, row)
        threshold, free = require_free(host, work, row)
        row['start_threshold'], row['free_at_start'] = threshold, free
        preflight(host, twin)
        swap_start = memory(host)['swap_used_bytes']
        refuse_if_late(host, row)                                 # addendum 6: before the gate and the spawn
        gate = write_gate(host, work, candidate, args.announced)
        t_launch = host.now()
        stopper = Stopper(host, work, row, t_launch)
        row['utc_launch'] = utc(t_launch)
        launch(host, work, gate, row, stopper, guard, intent)
        say('launched %s; watching every %d s' % (work.name, WATCH_S))
        watch(host, row, stopper, t_start, t_launch, deadline['staged_wall_s'], own)
    except Refused as e:
        guard.cleaning = True; row['refused'] = str(e)
    except KeyboardInterrupt:
        guard.cleaning = True; row['stopped'] = 'interrupted (Ctrl-C)'
    except OperatorSignalled as e:
        guard.cleaning = True; row['stopped'] = 'operator signalled (%s)' % e.name
    except Exception as e:
        guard.cleaning = True; row['error'] = '%s: %s' % (type(e).__name__, e)
    except BaseException as e:            # SystemExit and the like: the same path, then raised again (addendum 3, item 1)
        guard.cleaning = True; row['stopped'] = 'interrupted (%s)' % type(e).__name__; kept = e
    finally:
        # Addendum 3, item 1: from the spawn on, every way out comes here: stop the run (the observer by its identity when
        # usable, else the launcher through its handle), wait for its cleanup, check for leftover mf-native instances,
        # restore, write the row. Addendum 2, item 1: every step runs to its end; the first BaseException is kept and
        # raised again only after the record row and the summary are written. Further signals are only recorded.
        guard.cleaning = True
        steps = Steps(row); steps.kept = kept
        proc = stopper.proc if stopper is not None else None
        if proc is not None:              # the row never says `launched: false` once the spawn returned
            row['launched'] = True
            if not isinstance(row.get('launcher'), dict):
                row['launcher'] = {'pid': proc.pid, 'start': None, 'pgid': None, 'own_group': None}
        if guard.pending is not None and row['stopped'] is None and row['error'] is None:
            row['stopped'] = 'operator signalled (%s)' % getattr(guard.pending, 'name', type(guard.pending).__name__)

        def stop_if_running():
            # Still running after an interrupt, a signal or an exception (the watch returns only once the launcher has
            # ended, or after it gave up waiting, which is alerted): stop it the documented way and wait, boundedly.
            if proc is not None and proc.poll() is None and not any('LEFT RUNNING' in a for a in row['alerts']):
                stopper.attempt(); wait_after_stop(host, proc, stopper, row)
        guard.stage = 'the stop'
        steps.run('the stop', stop_if_running)
        if stopper is not None and stopper.rec['by'] == 'launcher interrupt':
            steps.run('the check after the launcher interrupt', after_interrupt_check, host, stopper, row)
        if stopper is not None and stopper.rec['wanted'] and not stopper.rec['signalled']:
            row['alerts'].append('RUN NOT STOPPED: OBSERVER NOT SIGNALLED: %s' % stopper.rec['not_signalled'])
        if proc is not None:
            row['launcher']['exit'] = steps.run('reading the launcher\'s exit', proc.poll)
        guard.stage = 'the restore'
        restored = switch.restore(steps)
        if not restored:
            row['alerts'].append('PICK READER NOT RESTORED: %s IS NOT LOADED AGAIN FROM %s; BOOTSTRAP IT BY HAND' % (
                PICKS_LABEL, (row['switch_offs']['pick_reader'] or {}).get('before', {}).get('plist')))
        guard.stage = 'finishing the row'
        try:
            finish_run_row(host, row, work, plan, t_start, swap_start, own)
        except BaseException as e:
            if isinstance(e, Exception):
                row['alerts'].append('THE RUN ROW COULD NOT BE COMPLETED: %s: %s' % (type(e).__name__, e))
            else:                  # addendum 2, item 8: interrupted while recording: a minimal row, then raised again
                steps.kept = steps.kept or e
                row = steps.row = guard.row = minimal_row(row, host, e)

        def append():
            n = len(row['signals_received'])
            append_record(host.record, row, host.fsync)
            if len(row['signals_received']) > n:       # addendum 4, F: a signal while the row itself was written
                append_record(host.record, {'command': 'signal-note', 'utc_start': row['utc_start'], 'utc': utc(host.now()),
                                            'signals_received': row['signals_received'][n:]}, host.fsync)
            return True
        guard.stage = 'the row'
        written = steps.run('the record append (RECORD NOT WRITTEN to %s)' % host.record, append) is True
        guard.stage = 'the intent file'
        alive = proc is not None and steps.run('reading the launcher', proc.poll) is None
        intent_left = finish_intent(host, intent, steps, restored, written, alive)

        def summary():
            say('RUN SUMMARY')
            for line in summary_lines(row):
                say(line)
            if intent_left:
                say(intent_left)
        guard.stage = 'the summary'
        steps.run('the summary', summary)
        steps.run('putting the signal handlers back', guard.restore)
        lock.release()
        steps.reraise()
    return run_exit(row, restored, written, intent_left)


def run_exit(row, restored, written, intent_left):
    """Addendum 4, F: 0 exactly when the run counts (its row written) and the restore succeeded, whatever arrived during
    the cleanup; else 2 refused, 3 a restore, the row or the intent file not done or an alert, 1 otherwise (stopped,
    failed, or ended without counting)."""
    if row.get('counts') is True and restored and written:
        return 0
    if row['refused']:
        return 2
    if not restored or not written or intent_left:
        return 3
    if row['error'] or row['stopped']:
        return 1
    return 3 if row['alerts'] else 1


def finish_intent(host, intent, steps, restored, written, alive=False):
    """Addendum 3, items 2 and 3, and addendum 4, F: the intent file is removed only once the restore and the row are
    done and the launcher has ended (a stop that could not be sent leaves it). Otherwise it stays, so that no path ends with
    neither a row nor the intent file; returns the line that says so, or None."""
    if not intent.written:
        return None
    if restored and written and not alive and steps.run('removing the intent file', intent.remove) is True:
        return None
    why = ('the launcher may still be running' if alive else 'the restore or the row did not complete'
           if not (restored and written) else 'its removal failed')
    return 'THE INTENT FILE %s WAS LEFT IN PLACE (%s): RUN `recover` BEFORE THE NEXT RUN' % (intent.path, why)


def minimal_row(row, host, exc):
    """Addendum 2, item 8: what is known, for a row whose finishing was interrupted; it never counts."""
    keep = ('command', 'utc_start', 'profile', 'plan', 'announced', 'announced_note', 'launched', 'refused', 'error', 'stopped',
            'staged_folder', 'twin_folder', 'utc_launch', 'launcher', 'stop', 'switch_offs', 'deadline', 'signals_received',
            'loaded_after_announcement', 'model_list_unread', 'alerts')
    out = {k: row.get(k) for k in keep}
    out.update(utc_end=utc(host.now()), recording='interrupted while recording: %s' % type(exc).__name__, counts=False,
               verdicts=None, pipeline=None, memory=None, collector=None, post=None)
    out['alerts'] = list(row.get('alerts') or []) + ['INTERRUPTED WHILE RECORDING (%s): A MINIMAL ROW WAS WRITTEN' % type(exc).__name__]
    return out


def finish_run_row(host, row, work, plan, t_start, swap_start, own):
    row['utc_end'] = utc(host.now())
    receipt = {}
    exit_code = (row.get('launcher') or {}).get('exit')
    try:
        if work is not None and row['launched']:
            row['pipeline'], receipt = pipeline_outcome(work, plan, exit_code)
        else:
            row['pipeline'] = pipeline_outcome(Path('/nonexistent'), plan, exit_code)[0]
    except Exception as e:     # addendum 4, E: whatever the receipt holds, the collector, memory and post checks still run
        row['pipeline'] = dict(pipeline_outcome(Path('/nonexistent'), plan, exit_code)[0], receipt_found=None,
                               failure='the receipt or the ledger could not be walked: %s' % type(e).__name__)
        receipt = {}
    health = collector_health(host, since=t_start)
    row['collector'] = collector_figures(health)
    if row.get('first_breach'):
        row['collector']['breach'] = row['first_breach']
    row['memory'] = memory_outcome(receipt, swap_start, memory(host)['swap_used_bytes'], own)
    final = post_state(host, row)
    if row['launched']:
        alert_late(host, row, state=final)        # the last check: the list must be read here for the run to count
        row['verdicts'] = verdicts(row['collector'], row['pipeline'], row['memory'])
        # addendum 6: a model loaded after the announcement means the run does not count, whatever else it shows;
        # addendum 7, C: nor does a run whose last check could not read the model list
        row['counts'] = (all(v is True for v in row['verdicts'].values()) and not row['loaded_after_announcement']
                         and not row['model_list_unread'])


# ============================================================================ load-test
def format_qualify():
    if 'q' not in _STAGE:
        _STAGE['q'] = _module(TOOLS / 'mirofish_format_qualify.py', 'operator_format_qualify')
    return _STAGE['q']


class PipelineLoadPath:
    """The load path of tools/mirofish_format_qualify.py, reused function by function on a freshly staged folder: its
    staged_modules (the folder's own observer-entry modules) and check_folder (manifest pins, helper, profile, tokenizer pins);
    the folder's own helper copied into a fresh output folder outside it; the SDK bridge of the staged model module; `baseline`,
    then `load` under a run identifier mf-native-<hex> with the profile's pinned checkpoint and load settings, read back with the
    staged run module's check_load_readback; and at the end `unload` with the instance and embedding references, proven by
    its unload_confirmed. The helper is never asked for a generation."""

    def __init__(self, work, out, profile, hold_s):
        q = self.q = format_qualify()
        self.work, self.out, self.profile = Path(work).absolute(), Path(out).absolute(), profile
        q.require(not self.out.is_relative_to(self.work), 'the output folder lies inside the staged folder')
        q.require(not self.out.exists(), 'a fresh output folder is required: ' + str(self.out))
        self.modules = q.staged_modules(self.work)
        q.check_folder(self.work, self.modules, profile)
        self.identifier = NATIVE_PREFIX + uuid.uuid4().hex
        self.owned = {'profile': profile} if self.modules.model.MODEL_PLAN is not None else {}
        (self.out / 'builder').mkdir(parents=True)
        shutil.copy2(self.work / 'builder/sdk_native.mjs', self.out / 'builder/sdk_native.mjs')
        self.deadline = time.monotonic() + q.BASELINE_S + q.LOAD_S + hold_s + q.UNLOAD_S + BASELINE_S + 120
        self.sdk = self.modules.model.SDKBridge(self.out, self.deadline)
        self.embedding = None; self.loaded = None

    def baseline(self):
        b = self.sdk.call('baseline', self.q.BASELINE_S)
        self.embedding = (b.get('embedding') or {}).get('instanceReference')
        return {'embedding_reference': self.embedding is not None}

    def load(self):
        self.loaded = self.sdk.call('load', self.q.LOAD_S, identifier=self.identifier, **self.owned)
        self.modules.run.check_load_readback(self.loaded, self.identifier, self.profile)
        return {'identifier': self.identifier}

    def unload(self):
        reference = ((self.loaded if type(self.loaded) is dict else {}).get('info') or {}).get('instanceReference')
        reply = self.sdk.call('unload', self.q.UNLOAD_S, identifier=self.identifier, instanceReference=reference,
                              embeddingReference=self.embedding, **self.owned)
        return self.q.unload_confirmed(reply, self.identifier), {'unloaded': (reply or {}).get('unloaded') if isinstance(reply, dict) else None}

    def close(self):
        return self.sdk.group.stop(self.deadline + 60) if hasattr(self.sdk, 'group') else None


def sample_phase(host, seconds, t_since, late_row=None):
    """Memory samples SAMPLE_S apart for `seconds`, the collector checked every WATCH_S; (samples, breach or None)."""
    samples = []; start = host.now(); next_check = start + WATCH_S
    while host.now() - start < seconds:
        t = host.now()
        samples.append(dict(memory(host), t=round(t - start, 2)))
        if t >= next_check:
            next_check += WATCH_S
            breach = collector_health(host, since=t_since)['breach']
            if late_row is not None:
                alert_late(host, late_row)               # addendum 6: during the hold, at every collector check
            if breach:
                return samples, breach
        host.sleep(max(0.0, start + len(samples) * SAMPLE_S - host.now()))
    return samples, None


def free_stats(samples):
    values = [x['free_percent'] for x in samples if x.get('free_percent') is not None]
    return {'samples': len(samples), 'median': statistics.median(values) if values else None, 'lowest': min(values) if values else None}


def load_test_figures(result, base, hold, hold_s):
    result['baseline'] = free_stats(base); result['after_load'] = free_stats(hold)
    first = [x for x in hold if x['t'] < 60]; last = [x for x in hold if x['t'] >= hold_s - 60]
    f, l = free_stats(first)['median'], free_stats(last)['median']
    b = result['baseline']['median']
    result['setup_cost_points'] = None if b is None or f is None else b - f
    result['fall_points'] = None if f is None or l is None or not hold or hold[-1]['t'] < hold_s - 60 else f - l
    levels = [x['pressure_level'] for x in base + hold if x.get('pressure_level') is not None]
    result['highest_pressure_level'] = max(levels) if levels else None
    swaps = [x['swap_used_bytes'] for x in base + hold if x.get('swap_used_bytes') is not None]
    result['swap_start_bytes'] = swaps[0] if swaps else None
    result['swap_end_bytes'] = swaps[-1] if swaps else None
    result['swap_change_bytes'] = swaps[-1] - swaps[0] if swaps else None


def cmd_load_test(host, args):
    say = host.out; model = args.model; hold_s = 60 * args.hold_minutes
    plan = {'model': model}; t0 = host.now()
    result = new_row('load-test', args, plan, t0)
    result.update(profile='swarm8', model=model, hold_minutes=args.hold_minutes, baseline=None, after_load=None,
                  load={'identifier': None, 'ok': False, 'refusal': None}, unload={'proven': None})
    s = do_check(host, 'swarm8', plan, 'load-test', args.hold_minutes)
    result['check'] = s
    if args.dry_run:
        for line in check_lines(s):
            say(line)
        say('dry run: would boot out %s, unload %s, stage swarm8 --model %s into a fresh folder, sample %d s, load %s through '
            'the pipeline\'s load path, hold %d min sampling, unload it and prove it, restore, write the result. Nothing was changed.'
            % (PICKS_LABEL if s['pick_reader']['listed'] else 'nothing', ', '.join(o['instance_id'] for o in s['models']['others']) or 'nothing',
               model, BASELINE_S, model, args.hold_minutes))
        return 2 if s['refusals'] else 0
    lock = OperatorLock(host)
    if not lock.take():           # addendum 4, D; addendum 7, E: never a result file at the holder's path, nor anything else
        say('refused: ' + lock.refusal())
        return 2
    switch = SwitchOffs(host, result); lp = None; asked = False; base, hold = [], []; kept = None
    guard = SignalGuard(host, result); intent = IntentFile(host)
    try:
        guard.install()                                          # addendum 3, item 2: before the first switch-off
        if s['refusals']:
            raise Refused('; '.join(s['refusals']))
        work = fresh_folder(host, 'loadtest-' + re.sub(r'[^a-z0-9.-]', '-', model.lower()))
        result['staged_folder'] = str(work)
        stage(host, 'swarm8', ['--model', model], work)          # addendum 2, item 5: staging before any switch-off
        say('check passed and staged; expected %d s, at most %d s; switching off: %s' % (
            s['durations']['expected_s'], s['durations']['wall_s'], s['announcement']))
        intent.write(**intent_start(host, 'load-test', result, s))
        switch.apply(s)
        settle_after_unloads(host, result)
        result['start_threshold'], result['free_at_start'] = require_free(host, work, result)
        lp = host.load_path(work, Path(str(work) + '-out'), model, hold_s)
        intent.write(load_identifier=getattr(lp, 'identifier', None))
        lp.baseline()
        base, breach = sample_phase(host, BASELINE_S, t0)
        if breach:
            result['stopped'] = 'collector breach'; result['first_breach'] = breach
        else:
            refuse_if_late(host, result)                          # addendum 6: before the load call
            asked = True
            try:
                result['load'].update(lp.load(), ok=True)
            except Exception as e:
                result['load']['refusal'] = '%s: %s' % (type(e).__name__, e)
                result['error'] = 'the load failed: ' + result['load']['refusal']
            if result['load']['ok']:
                alert_late(host, result)                          # addendum 6: after the load, an alert at once
                # addendum 2, item 11: the collector at once when the load call returns, as well as during the hold
                after = collector_health(host, since=t0)
                result['collector_after_load'] = collector_figures(after)
                breach = after['breach']
                if not breach:
                    hold, breach = sample_phase(host, hold_s, t0, result)
                if breach:
                    result['stopped'] = 'collector breach'; result['first_breach'] = breach
    except Refused as e:
        guard.cleaning = True; result['refused'] = str(e)
    except KeyboardInterrupt:
        guard.cleaning = True; result['stopped'] = 'interrupted (Ctrl-C)'
    except OperatorSignalled as e:
        guard.cleaning = True; result['stopped'] = 'operator signalled (%s)' % e.name
    except Exception as e:
        guard.cleaning = True; result['error'] = '%s: %s' % (type(e).__name__, e)
    except BaseException as e:
        guard.cleaning = True; result['stopped'] = 'interrupted (%s)' % type(e).__name__; kept = e
    finally:
        guard.cleaning = True; guard.stage = 'the unload'
        steps = Steps(result); steps.kept = kept   # addendum 2, item 1: every step to its end, a Ctrl-C kept and raised at the end
        if lp is not None and asked:
            def unload():
                proven, detail = lp.unload(); result['unload'] = {'proven': bool(proven), 'detail': detail}
            if steps.run('the test model\'s unload', unload) is None and result['unload'].get('proven') is None:
                result['unload'] = {'proven': False, 'failure': 'the unload did not complete'}
            if not result['unload']['proven']:
                result['alerts'].append('THE TEST MODEL\'S UNLOAD IS NOT PROVEN: %s' % result['unload'])
        if lp is not None:
            steps.run('the load path\'s helper stop', lp.close)
        guard.stage = 'the restore'
        restored = switch.restore(steps)
        if not restored:
            result['alerts'].append('PICK READER NOT RESTORED: %s IS NOT LOADED AGAIN; BOOTSTRAP IT BY HAND' % PICKS_LABEL)
        guard.stage = 'the result'

        def finish():
            result['utc_end'] = utc(host.now())
            load_test_figures(result, base, hold, hold_s)
            health = collector_health(host, since=t0)
            result['collector'] = collector_figures(health)
            if result.get('first_breach'):
                result['collector']['breach'] = result['first_breach']
            final = post_state(host, result)
            if asked:
                alert_late(host, result, state=final)
        steps.run('finishing the result', finish)

        def write():
            path = host.load_tests / ('%s-%s.json' % (result['utc_start'].replace(':', '').replace('+0000', 'Z'),
                                                      re.sub(r'[^A-Za-z0-9.-]', '-', model)))
            write_json(path, result); say('result: %s' % path); return True
        written = steps.run('the result write (RESULT NOT WRITTEN)', write) is True
        intent_left = finish_intent(host, intent, steps, restored, written)

        def summary():
            say('LOAD TEST %s: %s; baseline %s; after load %s; setup cost %s points; fall %s points; highest pressure %s; '
                'swap change %s; collector %s; unload proven %s; %s' % (
                    model, result['refused'] or result['error'] or result['stopped'] or 'held to its end', result.get('baseline'),
                    result.get('after_load'), result.get('setup_cost_points'), result.get('fall_points'),
                    result.get('highest_pressure_level'), result.get('swap_change_bytes'), result.get('collector'),
                    result['unload'].get('proven'), '; '.join(result['alerts']) or 'no alerts'))
            if intent_left:
                say(intent_left)
        steps.run('the summary', summary)
        steps.run('putting the signal handlers back', guard.restore)
        lock.release()
        steps.reraise()
    if result['refused']:
        return 2
    if not restored or not written or intent_left:
        return 3
    if result['refused']:
        return 2
    if result['error'] or result['stopped'] or result['signals_received']:
        return 1
    return 3 if result['alerts'] else 0


# ============================================================================ check, record, main
def plain_string(v):
    """A str that encodes as UTF-8 (no lone surrogate, which a JSON file can carry as an escape)."""
    if type(v) is not str:
        return False
    try:
        v.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def intent_shape_errors(intent):
    """Addendum 4, E: the whole intent parsed against one typed shape before anything is done; the fields that fail."""
    if not isinstance(intent, dict):
        return ['the intent (not a JSON object)']
    wrong = []
    required = {'command', 'utc', 'tool', 'profile', 'pick_reader', 'models_to_unload', 'models_note', 'work', 'twin',
                'launcher', 'record', 'scripts', 'launch'}
    wrong += ['%s (missing)' % k for k in sorted(required - set(intent))]
    wrong += ['%s (not a field of an intent)' % k for k in sorted(set(intent) - required - {'load_identifier'})]
    S = plain_string                       # addendum 7, E: a string with a lone surrogate is not of the shape
    I = lambda v: type(v) is int
    SN = lambda v: v is None or plain_string(v)
    IN = lambda v: v is None or type(v) is int
    D = lambda v, keys: type(v) is dict and set(v) == set(keys)
    g = intent.get

    def need(path, ok):
        if not ok:
            wrong.append(path)
    need('command', S(g('command')) and g('command') in ('run', 'load-test'))
    need('utc', S(g('utc')))
    tool = g('tool')
    need('tool', D(tool, ('pid', 'start')) and I(tool['pid']) and SN(tool['start']))
    need('profile', SN(g('profile')))
    pr = g('pick_reader')
    if not D(pr, ('label', 'before')):
        need('pick_reader', False)
    else:
        need('pick_reader.label', S(pr['label']))
        before = pr['before']
        if not D(before, ('listed', 'plist', 'state')):
            need('pick_reader.before', False)
        else:
            need('pick_reader.before.listed', type(before['listed']) is bool)
            need('pick_reader.before.plist', SN(before['plist']))
            need('pick_reader.before.state', SN(before['state']))
    need('models_to_unload', type(g('models_to_unload')) is list and all(S(x) for x in g('models_to_unload')))
    need('models_note', S(g('models_note')))
    need('work', S(g('work')))
    need('twin', SN(g('twin')))
    need('record', S(g('record')))
    need('scripts', g('scripts') == ROLE_SCRIPTS and type(g('scripts')) is dict)
    launch = g('launch')
    need('launch', launch is None or (D(launch, ('spawning_utc',)) and S(launch['spawning_utc'])))
    launcher = g('launcher')
    if launcher is not None:
        if not D(launcher, ('pid', 'start', 'pgid')):
            need('launcher', False)
        else:
            need('launcher.pid', I(launcher['pid']))
            need('launcher.start', SN(launcher['start']))
            need('launcher.pgid', IN(launcher['pgid']))
    if 'load_identifier' in intent:
        need('load_identifier', SN(intent['load_identifier']))
    return wrong


def intent_mismatch(host, intent):
    """Addendum 4, C: recover loads one thing only, the pick reader's own plist, a constant. An intent that names another
    label or another plist, or a work folder that is not a staged folder of the crew folder, is refused whole."""
    pr = intent['pick_reader']; before = pr['before']
    out = []
    if pr['label'] != PICKS_LABEL:
        out.append('the intent names the job %r, not %s' % (pr['label'][:200], PICKS_LABEL))
    if before['plist'] not in (None, host.picks_plist) or (before['listed'] and before['plist'] is None):
        out.append('the intent names the plist %r, not %s' % ((before['plist'] or '')[:300], host.picks_plist))
    work = Path(intent['work'])
    if work.parent != host.crew or not work.name.startswith('valedia-native-') or '..' in work.parts:
        out.append('the intent names the work folder %r, not a staged folder in %s' % (intent['work'][:300], host.crew))
    return out


def find_survivors(host, intent):
    """Addendum 7, A: survivors are found by the scan alone. Every process of this user is put to the proof in BOTH
    roles, launcher and observer, whatever any file proposed. What the intent's launcher entry and the staged folder's
    observer identity file propose is a hint only: it adds nothing except the start the intent holds for its launcher,
    compared in the launcher role alone, and a proposed pid that is proven in no role is reported (by pid) as not this
    run's. Returns (proven, doubtful, not_ours, scan_note, guard): proven [{role, pid, start, by}] (the start as read by
    the proof, compared again before any signal); doubtful [{role, pid, by, why}] (the proof could not be made: never
    signalled, and it holds everything back); not_ours (made, and the answer is no: never signalled); why the scan could
    not be made (None when it was); the guard."""
    work = intent['work']
    guard = {'ancestors': ancestors(host), 'jobs': job_pids(host)}
    held = {}                          # pid -> the start the intent holds, for the launcher role only
    hints = []                         # (pid, role, by)
    launcher = intent['launcher']
    if launcher is not None:
        hints.append((launcher['pid'], 'launcher', 'the intent'))
        if launcher['start'] is not None:
            held[launcher['pid']] = launcher['start']
    try:
        identity = json.loads((Path(work) / 'builder' / IDENTITY_FILE).read_text())
    except (OSError, ValueError):
        identity = None
    if isinstance(identity, dict) and 'pid' in identity:
        hints.append((identity['pid'], 'observer', 'the identity file'))
    hinted = {}
    for pid, role, by in hints:
        if type(pid) is int:
            hinted.setdefault(pid, []).append('proposed as the %s by %s' % (role, by))
    scan_note = None
    try:
        pids = sorted({p for p in host.proc_scan() if type(p) is int})
        if host.self_pid not in pids:      # a scan that does not list this very process is not a scan of this user
            raise OperatorError('the scan did not list this tool')
    except Exception as e:
        pids = []; scan_note = 'THE PROCESS SCAN COULD NOT BE MADE (%s)' % type(e).__name__
    proven, doubtful, not_ours = [], [], []
    for pid in pids:
        by = '; '.join(['the scan'] + hinted.get(pid, []))
        try:
            tokens = command_tokens(host, pid)
            if tokens is None:
                if read_identity(host, pid)[1] != 'gone':
                    doubtful.append({'role': 'launcher or observer', 'pid': pid, 'by': by,
                                     'why': UNREADABLE + 'pid %d: its command could not be read' % pid})
                continue
            named = [r for r in ROLES if names_role(tokens, work, r)]
            tokens = None
            if not named and pid not in hinted:
                continue                   # its command names neither role: not a candidate
            results = [(r,) + proof(host, pid, held.get(pid) if r == 'launcher' else None, r, work, guard)
                       for r in (named or ROLES)]
        except Exception as e:             # a read that raised: the proof could not be made
            doubtful.append({'role': 'launcher or observer', 'pid': pid, 'by': by,
                             'why': UNREADABLE + 'pid %d: a read failed (%s)' % (pid, type(e).__name__)})
            continue
        good = [(r, seen) for r, why, seen in results if why is None]
        unread = [(r, why) for r, why, _ in results if why is not None and why.startswith(UNREADABLE)]
        no = [(r, why) for r, why, _ in results if why is not None and why != 'gone' and not why.startswith(UNREADABLE)]
        if good:
            proven += [{'role': r, 'pid': pid, 'start': seen['start'], 'by': by} for r, seen in good]
        elif unread:
            doubtful.append({'role': unread[0][0], 'pid': pid, 'by': by, 'why': unread[0][1]})
        elif no:
            not_ours.append({'role': no[0][0], 'pid': pid, 'by': by, 'why': no[0][1]})   # addendum 5: never signalled
    if scan_note is None:
        scanned = set(pids)
        for pid, role, by in hints:        # a proposal outside this user's processes: reported, never a survivor
            if type(pid) is int and pid in scanned:
                continue
            if type(pid) is not int or pid <= 1:
                not_ours.append({'role': role, 'pid': pid if type(pid) is int else repr(pid)[:40], 'by': by,
                                 'why': 'pid %s is not above 1' % repr(pid)[:40]})
                continue
            try:
                state = read_identity(host, pid)[1]
            except Exception:
                state = 'unreadable'
            if state != 'gone':
                not_ours.append({'role': role, 'pid': pid, 'by': by, 'why': 'pid %d is not among this user\'s processes' % pid})
    return proven, doubtful, not_ours, scan_note, guard


def cmd_recover(host, args):
    """Addendum 3, item 2, and addendum 4, A to F: after a run or load test that ended before its restore and its row (a
    KILL, a crash, a power cut). It holds the operator lock (a live writer holds it: refused). The intent file is parsed
    against its typed shape and checked against the constants (else refused whole, the file kept). Every process proposed
    as the run's launcher or observer is put to the proof; a proven survivor is reported in capitals and, only with
    --stop, stopped the documented way (TERM to a proven observer, else SIGINT to a proven launcher). While a proven
    survivor lives, the scan could not be made, or a proposed process is alive but not proven, the pick reader is not put
    back and the intent file is kept. Otherwise the pick reader is bootstrapped from its own plist when it was loaded
    before and is not now; leftover mf-native instances are reported (nothing is unloaded); a row `recovered after an
    unfinished run` is appended; the intent file is removed. --dry-run changes nothing."""
    say = host.out
    lock = OperatorLock(host)
    if not lock.take():
        say('refused: ' + lock.refusal()); return 2
    try:
        return _recover(host, args, say)
    finally:
        lock.release()


def _recover(host, args, say):
    dry = args.dry_run; t = host.now()
    if not host.intent.exists():
        say('recover: no intent file at %s: no unfinished run to recover; nothing was changed' % host.intent)
        return 0
    try:
        intent = json.loads(host.intent.read_text())
    except (OSError, ValueError) as e:
        say('THE INTENT FILE %s IS NOT READABLE (%s): NOTHING WAS CHANGED; READ IT BY HAND' % (host.intent, type(e).__name__))
        return 3
    wrong = intent_shape_errors(intent)
    if wrong:
        say('refused: THE INTENT FILE %s HAS THE WRONG SHAPE in %s: NOTHING WAS DONE AND THE FILE IS KEPT' % (
            host.intent, ', '.join(wrong)))
        return 2
    mismatch = intent_mismatch(host, intent)
    if mismatch:
        say('refused: MISMATCH: %s: NOTHING WAS DONE AND THE FILE IS KEPT' % '; '.join(mismatch))
        return 2
    try:                                   # addendum 7, E: read before anything is done; a read that raises is a refusal
        pick_reader_state(host)
    except Exception as e:
        say('refused: THE PICK READER\'S LAUNCHD JOB COULD NOT BE READ (%s): NOTHING WAS DONE AND THE FILE %s IS KEPT' % (
            type(e).__name__, host.intent))
        return 2
    row = {'command': 'recover', 'utc_start': utc(t), 'utc_end': None, 'recovered': RECOVERED, 'dry_run': dry,
           'profile': intent['profile'], 'launched': intent['launch'] is not None or intent['launcher'] is not None,
           'intent': intent, 'proven': [], 'doubtful': [], 'scan': None, 'pick_reader': None, 'launcher_alive': None,
           'observer_alive': None, 'stop': None, 'launcher_alive_after': None, 'observer_alive_after': None,
           'native_loaded': None, 'post': None, 'counts': False, 'alerts': []}
    would = []; changed = False
    try:
        proven, doubtful, not_ours, scan_note, guard = find_survivors(host, intent)
    except Exception as e:
        proven, doubtful, not_ours, guard = [], [], [], None
        scan_note = 'THE PROCESS SCAN COULD NOT BE MADE (%s)' % type(e).__name__
    row['proven'], row['doubtful'], row['not_ours'], row['scan'] = proven, doubtful, not_ours, scan_note or 'made'
    for d in doubtful:
        row['alerts'].append(('NOT SIGNALLED: THE PROOF COULD NOT BE MADE FOR pid %s proposed as the %s by %s: %s' % (
            d['pid'], d['role'], d['by'], d['why'][len(UNREADABLE):])).upper())
    for d in not_ours:
        say(('NOT SIGNALLED, NOT A SURVIVOR OF THIS RUN: pid %s (proposed as the %s by %s): %s' % (
            d['pid'], d['role'], d['by'], d['why'])).upper())
    if scan_note:
        row['alerts'].append(scan_note)
    row['launcher_alive'] = any(p['role'] == 'launcher' for p in proven)
    row['observer_alive'] = any(p['role'] == 'observer' for p in proven)

    def recheck(p):
        """The proof made again, with the start it read: None when it still holds."""
        try:
            return proof_failure(host, p['pid'], p['start'], p['role'], intent['work'], guard)
        except Exception as e:
            return UNREADABLE + 'a read failed (%s)' % type(e).__name__

    def still(p):                          # proven again, or the proof could not be made: treated as alive (addendum 7, B)
        why = recheck(p)
        return why is None or why.startswith(UNREADABLE)
    if args.stop and proven:
        stop = row['stop'] = {'by': None, 'sent': [], 'not_sent': []}
        for role, sig, by in (('observer', signal.SIGTERM, 'observer'), ('launcher', LAUNCHER_STOP_SIGNAL, 'launcher interrupt')):
            sent_now = False
            for p in [p for p in proven if p['role'] == role]:
                if dry:
                    would.append('send %s to pid %d (the %s)' % (signal.Signals(sig).name, p['pid'], role))
                    continue
                why = recheck(p)           # proven again immediately before its signal (addendum 7, A)
                if why is not None:
                    stop['not_sent'].append({'role': role, 'pid': p['pid'], 'why': why}); continue
                host.send_signal(p['pid'], sig); changed = sent_now = True
                stop['sent'].append({'role': role, 'pid': p['pid'], 'signal': signal.Signals(sig).name, 'utc': utc(host.now())})
                stop['by'] = stop['by'] or by
            if dry:
                if any(p['role'] == role for p in proven):
                    break
                continue
            if not sent_now:
                continue
            sent = host.now()
            while host.now() < sent + STOP_WAIT_S and any(still(p) for p in proven):
                host.sleep(WATCH_S)
            if not any(still(p) for p in proven):
                break
    alive = [p for p in proven if still(p)] if not dry else list(proven)
    row['launcher_alive_after'] = any(p['role'] == 'launcher' for p in alive)
    row['observer_alive_after'] = any(p['role'] == 'observer' for p in alive)
    for p in alive:
        row['alerts'].append('%s STILL RUNNING: pid %d (proven by its pid, start and command)%s' % (
            p['role'].upper(), p['pid'], '' if args.stop else '; `recover --stop` stops it the documented way'))
    blocked = bool(alive or doubtful or scan_note)
    # the pick reader: its own plist only, and never beside a run that may still be going on
    before = intent['pick_reader']['before']
    try:
        now = pick_reader_state(host)
    except Exception as e:                 # addendum 7, E: in words, its type only; nothing more is put back
        now = None
        row['alerts'].append('THE PICK READER\'S LAUNCHD JOB COULD NOT BE READ AGAIN (%s): IT IS NOT PUT BACK; THE INTENT '
                             'FILE IS KEPT' % type(e).__name__)
        blocked = True
    pr = row['pick_reader'] = {'label': PICKS_LABEL, 'before': before, 'found': now, 'bootstrap_exit': None, 'after': now}
    if now is None:
        pass
    elif before['listed'] and not now['listed']:
        if blocked:
            row['alerts'].append('PICK READER LEFT OFF: A RUN MAY STILL BE GOING ON (SEE ABOVE); THE INTENT FILE IS KEPT')
        elif dry:
            would.append('bootstrap %s from %s' % (PICKS_LABEL, host.picks_plist))
        else:
            r = host.run(['/bin/launchctl', 'bootstrap', 'gui/%d' % host.uid, host.picks_plist], timeout=CMD_S)
            changed = True
            pr['bootstrap_exit'] = r.returncode
            try:
                pr['after'] = pick_reader_state(host)
            except Exception as e:
                pr['after'] = None
                row['alerts'].append('THE PICK READER\'S LAUNCHD JOB COULD NOT BE READ AFTER ITS BOOTSTRAP (%s)' % type(e).__name__)
            if not (pr['after'] or {}).get('listed'):
                row['alerts'].append('PICK READER NOT RESTORED: %s IS NOT LOADED AGAIN FROM %s; BOOTSTRAP IT BY HAND' % (
                    PICKS_LABEL, host.picks_plist))
    reader_ok = not before['listed'] or bool(pr['after'] and pr['after']['listed'])
    post_state(host, row)                  # leftover instances: reported, never unloaded
    row['native_loaded'] = row['post']['native_loaded']
    row['utc_end'] = utc(host.now())
    say('recover: intent %s from %s (%s, %s), work %s' % (host.intent, intent['utc'], intent['command'], intent['profile'],
                                                           intent['work']))
    say('  proven survivors %s; proof not made %d; not this run\'s %s; scan %s' % (
        ['%s %d' % (p['role'], p['pid']) for p in proven] or 'none', len(doubtful), [d['pid'] for d in not_ours] or 'none',
        row['scan']))
    say('  pick reader: before %s, found %s, after %s' % (before['listed'], (now or {}).get('listed', 'unreadable'),
                                                          (pr['after'] or {}).get('listed', 'unreadable')))
    say('  stop %s; mf-native loaded %s; models unloaded by the run, %s: %s' % (
        row['stop'], row['native_loaded'], NOT_RELOADED, ', '.join(intent['models_to_unload']) or 'none'))
    for alert in row['alerts']:
        say(alert)
    if dry:
        say('dry run: would %s. Nothing was changed.' % ('; '.join(would) or 'change nothing'))
        return 0
    complete = not blocked and reader_ok
    if not (changed or complete):
        say('nothing was changed; THE INTENT FILE %s IS KEPT' % host.intent)
        return 3
    try:
        append_record(host.record, row, host.fsync); written = True
    except Exception as e:
        say('THE RECOVER ROW WAS NOT WRITTEN TO %s: %s; THE INTENT FILE IS KEPT' % (host.record, type(e).__name__))
        written = False
    if written and complete:
        try:
            IntentFile(host).remove()
            say('the intent file %s was removed' % host.intent)
        except OSError as e:
            say('THE INTENT FILE %s COULD NOT BE REMOVED: %s' % (host.intent, type(e).__name__)); return 3
    elif written:
        say('THE INTENT FILE %s IS KEPT: %s' % (host.intent, 'A RUN MAY STILL BE GOING ON' if blocked else 'THE PICK READER IS NOT RESTORED'))
    return 0 if written and complete and not row['alerts'] else 3


def cmd_check(host, args):
    if args.dry_run:
        host.out('dry run: check would read LM Link\'s state (lms link status), LM Studio\'s model list (GET %s/api/v1/models), '
                 'the collector\'s launchd job and its store read-only (%s: decisions.status, latency_s, decision_ts; heartbeats.ts), '
                 'memory_pressure and two sysctl values, the pick reader\'s launchd state, free disk on %s and the crew folder\'s '
                 'gate and observer identity files. Nothing was read or changed.' % (LMSTUDIO, host.store, WORK_VOLUME))
        return 0
    plan, _ = plan_of(args)
    s = do_check(host, args.profile, plan, 'run')
    for line in check_lines(s):
        host.out(line)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(s, indent=2, sort_keys=True) + '\n')
    if args.json:
        host.out(json.dumps(s, sort_keys=True))
    return 0


def cmd_record(host, args):
    rows, damaged = read_rows(host.record)
    if damaged:
        host.out('%d damaged line%s in %s skipped: never counted as a run, never a reset' % (
            damaged, '' if damaged == 1 else 's', host.record))
    for r in rows:
        if r.get('command') == 'recover':
            host.out('%s recover %s: %s; launched %s (a run that does not count); launcher alive %s; mf-native loaded %s' % (
                r.get('utc_start'), r.get('profile'), r.get('recovered'), r.get('launched'), r.get('launcher_alive'),
                r.get('native_loaded')))
            continue
        if r.get('command') == 'signal-note':
            host.out('%s signal-note: %s' % (r.get('utc_start'), r.get('signals_received')))
            continue
        host.out('%s %s %s %s: %s; verdicts %s; counts %s' % (
            r.get('utc_start'), r.get('command'), r.get('profile'), plan_label(r.get('plan')),
            r.get('refused') and 'refused' or r.get('error') and 'error' or r.get('stopped') or ('ran' if r.get('launched') else 'not launched'),
            r.get('verdicts'), r.get('counts')))
    for line in streak_lines(rows):
        host.out(line)
    return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    for name in ('check', 'load-test', 'run', 'record', 'recover'):
        q = sub.add_parser(name)
        q.add_argument('--dry-run', action='store_true')
        if name == 'recover':
            q.add_argument('--stop', action='store_true', help='stop a surviving run the documented way')
        if name in ('check', 'run'):
            q.add_argument('--profile', choices=SWARM_PROFILES, default='swarm8' if name == 'check' else None, required=name == 'run')
            q.add_argument('--model'); q.add_argument('--setup-model'); q.add_argument('--turns-model')
        if name == 'check':
            q.add_argument('--json-out'); q.add_argument('--json', action='store_true')
        if name == 'load-test':
            q.add_argument('--model', required=True); q.add_argument('--hold-minutes', type=int, required=True)
        if name in ('load-test', 'run'):
            q.add_argument('--announced', help='the lead\'s statement that the owner was told (stored verbatim; not checked)')
    return p


def main(argv=None, host=None):
    args = parser().parse_args(argv)
    host = host or Host()
    if args.cmd in ('load-test', 'run') and not (args.announced or '').strip():
        host.out('refused: --announced TEXT is required: the lead\'s statement that the owner was told (the tool cannot check it)')
        return 2
    if args.cmd == 'load-test' and args.hold_minutes < 2:
        host.out('refused: --hold-minutes must be at least 2 (the first and the last 60 s of the hold must not overlap)')
        return 2
    try:
        if args.cmd == 'check':
            return cmd_check(host, args)
        if args.cmd == 'record':
            return cmd_record(host, args)
        if args.cmd == 'recover':
            return cmd_recover(host, args)
        if args.cmd == 'load-test':
            return cmd_load_test(host, args)
        return cmd_run(host, args)
    except Refused as e:                   # an argument refusal, before anything was read or changed
        host.out('refused: %s' % e)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
