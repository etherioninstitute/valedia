"""One operator command for a MiroFish native run beside the live order-book collector (TMF6).

    $PY -B tools/mirofish_operator.py check [--profile swarm8] [--model X | --setup-model A --turns-model B] [--json-out F]
    $PY -B tools/mirofish_operator.py load-test --model X --hold-minutes M --announced TEXT [--dry-run]
    $PY -B tools/mirofish_operator.py run --profile P [--model X | --setup-model A --turns-model B] --announced TEXT [--dry-run]
    $PY -B tools/mirofish_operator.py record

`check` only reads. `load-test` and `run` switch off the hourly pick reader and every loaded model that is neither the decision
instance nor the embedding instance, restore the pick reader in a `finally` (Ctrl-C and exceptions included), watch the
collector every 30 s and stop on a breach. The tool never stops, signals, restarts, boots out or reconfigures the collector or
its launchd job, never sends a request to the decision instance and never reloads a model. Its use: docs/mirofish-operator.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
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
LMS = Path.home() / '.lmstudio/bin/lms'
LMSTUDIO = DM.URL

# ---- names
COLLECTOR_LABEL = 'com.valedia.orderbook-collector'
PICKS_LABEL = 'com.valedia.picks.hourly'
EMBEDDING_INSTANCE = 'ym-research-embedding'
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
PICKS_AFTER_S = 5               # ... until it is seen idle at least this long after that start (launchd starts it at once)
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


class OperatorError(RuntimeError):
    """Something failed; the command stops and restores."""


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
                 lms=LMS):
        self.run, self.spawn, self.http = run or _run, spawn or _spawn, http or _http   # _spawn: its own session and group
        self.now, self.sleep, self.send_signal = now or time.time, sleep or time.sleep, send_signal or os.kill
        self.disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self.uid = os.getuid() if uid is None else uid
        self.store, self.crew, self.record, self.load_tests = Path(store), Path(crew), Path(record), Path(load_tests)
        self.load_path = load_path or PipelineLoadPath
        self.out = out or (lambda line: print(line, flush=True))
        self.lms = str(lms)


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


def link_disabled(text, returncode):
    """LM Link counts as disabled only when `lms link status` exits 0 and the first non-empty line of its output, with
    surrounding blanks removed, begins with exactly LINK_DISABLED_LINE. Anything else is not known to be disabled."""
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    return returncode == 0 and bool(lines) and lines[0].startswith(LINK_DISABLED_LINE)


def lm_link(host):
    r = host.run([host.lms, 'link', 'status'], timeout=CMD_S)
    disabled = link_disabled(r.stdout, r.returncode)
    return {'disabled': disabled, 'state': 'disabled' if disabled else 'not known to be disabled', 'exit': r.returncode}


def models_state(host):
    """LM Studio's list judged with decision_model's pure functions: the decision instance loaded and right, the embedding
    instance loaded, every other loaded instance (what a run unloads) and any mf-native-* instance."""
    def unknown(why):
        return {'reachable': False, 'decision_instance': {'loaded_and_right': False, 'why': why}, 'embedding_loaded': False,
                'others': [], 'native_loaded': []}
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
    native = [iid for _, iid in loaded if isinstance(iid, str) and iid.startswith(NATIVE_PREFIX)]
    others = [{'instance_id': iid, 'key': key} for key, iid in loaded
              if isinstance(iid, str) and iid not in (DM.INSTANCE, EMBEDDING_INSTANCE) and not iid.startswith(NATIVE_PREFIX)]
    return {'reachable': True, 'decision_instance': {'loaded_and_right': why is None, 'why': why},
            'embedding_loaded': any(iid == EMBEDDING_INSTANCE for _, iid in loaded), 'others': others, 'native_loaded': native}


def observed_identity(host, pid):
    r = host.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,pgid=,lstart='], timeout=CMD_S)
    fields = (r.stdout or '').split()
    if r.returncode or len(fields) < 7 or not fields[0].isdigit() or not fields[1].isdigit():
        return None
    return {'pid': int(fields[0]), 'pgid': int(fields[1]), 'start': ' '.join(fields[2:])}


def identity_mismatch(host, identity):
    """None when the recorded observer {pid, start, pgid} is the live process now; else why it is not (never signal then)."""
    if not (isinstance(identity, dict) and type(identity.get('pid')) is int and identity['pid'] > 1
            and isinstance(identity.get('start'), str) and type(identity.get('pgid')) is int):
        return 'the observer identity file is not {"pid", "start", "pgid", ...}'
    seen = observed_identity(host, identity['pid'])
    if seen is None:
        return 'pid %d is not running' % identity['pid']
    if seen['start'] != identity['start']:
        return 'pid %d started %r, not %r as the identity file says' % (identity['pid'], seen['start'], identity['start'])
    if seen['pgid'] != identity['pgid']:
        return 'pid %d is in process group %d, not %d' % (identity['pid'], seen['pgid'], identity['pgid'])
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
    s['due'] = picks_due(host, s)
    return s


def picks_due(host, reader):
    """Correction C. Why the switch-offs may not begin now, or None: from PICKS_BEFORE_S before the reader's scheduled start
    (its calendar minute from launchd, the full hour when none is read; local clock, injected) until it has been read listed
    and idle at least PICKS_AFTER_S after that start, so that it cannot start between this read and the bootout. A reader
    that is not loaded is left alone and has no window. A running reader is refused by its own rule."""
    if not reader['listed']:
        return None
    minute = reader.get('minute') or 0
    t = host.now(); lt = time.localtime(t)
    since = (lt.tm_min * 60 + lt.tm_sec + (t % 1) - minute * 60) % 3600     # seconds since its last scheduled start
    if 3600 - since <= PICKS_BEFORE_S:
        return ('the hourly pick reader %s is due at :%02d in %d s; no switch-off begins from %d minutes before it until it '
                'is seen idle after it' % (PICKS_LABEL, minute, round(3600 - since), PICKS_BEFORE_S // 60))
    if since < PICKS_AFTER_S:
        return ('the hourly pick reader %s was due at :%02d, %d s ago, and is not yet seen idle after it (%d s needed)' % (
            PICKS_LABEL, minute, int(since), PICKS_AFTER_S))
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
    return LIMITS.model_label(limits_model(plan) or LIMITS.DEFAULT_MODEL)


def run_durations(profile, plan):
    p = stage_module().swarm_numbers(profile, limits_model(plan))
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
             'ANNOUNCEMENT (the lead gives this to the owner before starting): ' + s['announcement']]
    lines += ['REFUSED: ' + r for r in s['refusals']] or ['start rules: none refuses']
    return lines


# ============================================================================ switch-offs and their restore
class SwitchOffs:
    """The hourly pick reader (booted out, bootstrapped back from the same plist) and every loaded model that is neither the
    decision instance nor the embedding instance (unloaded by REST, never reloaded). Each with its before-state in the row."""

    def __init__(self, host, row):
        self.host = host
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
            r = host.run(['/bin/launchctl', 'bootout', 'gui/%d/%s' % (host.uid, PICKS_LABEL)], timeout=CMD_S)
            pr['booted_out'] = not pick_reader_state(host)['listed']
            if not pr['booted_out']:
                raise OperatorError('the pick reader bootout failed (exit %d); it is still loaded' % r.returncode)
        for other in summary['models']['others']:
            iid = other['instance_id']
            if iid in (DM.INSTANCE, EMBEDDING_INSTANCE) or iid.startswith(NATIVE_PREFIX):   # never, whatever the list says
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
            still = [o['instance_id'] for o in models_state(host)['others']]
            if still:
                raise OperatorError('still loaded after the unload: %s' % ', '.join(still))

    def restore(self):
        """True when the pick reader is as it was; never raises."""
        pr = self.rec['pick_reader']
        if pr is None:
            return True
        try:
            if pr['booted_out']:
                r = self.host.run(['/bin/launchctl', 'bootstrap', 'gui/%d' % self.host.uid, pr['before']['plist']], timeout=CMD_S)
                pr['bootstrap_exit'] = r.returncode
            pr['after'] = pick_reader_state(self.host)
            pr['restored'] = pr['after']['listed'] == pr['before']['listed']
        except Exception as e:
            pr['restored'] = False; pr['restore_failure'] = '%s: %s' % (type(e).__name__, e)
        return pr['restored']


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


def require_free(host, work):
    threshold = staged_threshold(work); free = memory(host)['free_percent']
    if free is None or free < threshold:
        raise Refused('%s%% free is under the staged start threshold %d%% (the pipeline would refuse at its start)' % (free, threshold))
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


def launch(host, work, gate, row):
    """The launcher, started by this tool in its own session and process group (Host.spawn); its pid, start and group are
    recorded at once, so that a stop by group can be checked against them."""
    argv = [str(PIPELINE_PY), '-B', 'mirofish_native_startup.py', '--work', str(work), '--gate', str(gate)]
    proc = host.spawn(argv, cwd=str(work / 'owned-source/observer-entry'), env=pipeline_env())
    seen = observed_identity(host, proc.pid)
    row['launcher'] = {'pid': proc.pid, 'start': seen and seen['start'], 'pgid': seen and seen['pgid'],
                       'own_group': bool(seen) and seen['pgid'] == proc.pid}
    return proc


class Stopper:
    """At most one stop signal. TERM to the observer named in <work>/builder/observer-identity.json, only when its pid, start
    and process group all match the live process. When that file has not appeared IDENTITY_WAIT_S after the launch: SIGINT to
    the launcher this tool started (correction A), only when it was started in its own group and its pid, start and group
    still match what was recorded at launch. The launcher's KeyboardInterrupt path then stops the observer group the
    pipeline's own way; a TERM would kill it without that cleanup. Never a group signal, never on a pid alone."""

    def __init__(self, host, work, row, t_launch):
        self.host, self.path, self.row, self.t_launch = host, work / 'builder' / IDENTITY_FILE, row, t_launch
        self.rec = row['stop'] = {'wanted': False, 'signalled': False, 'by': None, 'pid': None, 'pgid': None, 'utc': None,
                                  'not_signalled': None}
        self.sent_t = None

    def attempt(self):
        self.rec['wanted'] = True
        if self.rec['signalled']:
            return True
        try:
            identity = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            return self._by_launcher('no readable observer identity (%s)' % type(e).__name__)
        why = identity_mismatch(self.host, identity)
        if why:
            self.rec['not_signalled'] = why; return False
        self.host.send_signal(identity['pid'], signal.SIGTERM)
        self.sent_t = self.host.now()
        self.rec.update(signalled=True, by='observer', pid=identity['pid'], utc=utc(self.sent_t), not_signalled=None)
        return True

    def _by_launcher(self, why):
        if self.host.now() < self.t_launch + IDENTITY_WAIT_S:
            self.rec['not_signalled'] = why + '; waiting up to %d s after the launch for it' % IDENTITY_WAIT_S; return False
        launcher = self.row.get('launcher') or {}
        if not launcher.get('own_group'):
            self.rec['not_signalled'] = why + ', and the launcher is not in a process group of its own'; return False
        seen = observed_identity(self.host, launcher['pid'])
        if seen is None or seen['start'] != launcher['start'] or seen['pgid'] != launcher['pgid']:
            self.rec['not_signalled'] = why + ', and the launcher pid %s is no longer the process started (%s)' % (
                launcher['pid'], 'gone' if seen is None else 'start or group differs'); return False
        self.host.send_signal(launcher['pid'], LAUNCHER_STOP_SIGNAL)
        self.sent_t = self.host.now()
        self.rec.update(signalled=True, by='launcher interrupt', signal='SIGINT', pid=launcher['pid'], pgid=launcher['pgid'],
                        utc=utc(self.sent_t), not_signalled=None, why_launcher=why)
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
    """After a stop is wanted: wait for the launcher to end (the pipeline's own cleanup), STOP_WAIT_S after the signal at most."""
    while proc.poll() is None:
        if stopper.sent_t is not None and host.now() > stopper.sent_t + STOP_WAIT_S:
            row['alerts'].append('PIPELINE CLEANUP NOT SEEN WITHIN %d S OF THE STOP SIGNAL: THE LAUNCHER WAS LEFT RUNNING' % STOP_WAIT_S)
            return None
        host.sleep(WATCH_S)
        if not stopper.rec['signalled']:
            stopper.attempt()
    return proc.poll()


def pipeline_outcome(work, plan):
    out = {'receipt_found': False, 'complete': None, 'failure': None, 'calls': None, 'failed_calls': None, 'refused_calls': None,
           'swaps': None, 'swaps_clean': None, 'cleanup_proven': None, 'report_usable': None, 'memory_stop': None}
    path = work / 'builder/native-run-receipt.json'
    if not path.is_file():
        return out, {}
    receipt = json.loads(path.read_text())
    failure = receipt.get('failure') if isinstance(receipt.get('failure'), dict) else None
    out.update(receipt_found=True, complete=receipt.get('complete'), cleanup_proven=receipt.get('cleanup_proven'),
               failure=None if failure is None else str(failure.get('reason'))[:500], report_usable=receipt.get('paper_handoff') is True)
    reason = out['failure'] or ''
    out['memory_stop'] = bool(receipt.get('resource_failures') or receipt.get('resource_failure_samples')
                              or 'memory free threshold' in reason or 'RSS upper bound' in reason)
    ledger = work / 'builder/gateway-ledger.json'
    if ledger.is_file():
        calls = (json.loads(ledger.read_text()) or {}).get('calls') or []
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


def memory_outcome(receipt, swap_start, swap_end, own):
    res = receipt.get('resource') if isinstance(receipt.get('resource'), dict) else {}
    out = {'swap_start_bytes': swap_start, 'swap_end_bytes': swap_end, 'operator_lowest_free_percent_30s': own['lowest'],
           'operator_max_pressure_level': own['max_pressure'], 'notes': []}
    for key in ('lowest_sustained_free_percent_10s', 'max_pressure_level', 'co_resident_rss_bytes_max'):
        out[key] = res.get(key)
        if key not in res:
            out['notes'].append('receipt field resource.%s absent: null' % key)
    return out


def verdicts(collector, pipeline, memory):
    low = memory.get('lowest_sustained_free_percent_10s')
    st = collector.get('statuses') or {}       # correction B: by the count of each status, which the record carries
    return {'collector_unharmed': (collector['breach'] is None and collector['over_8s'] == 0
                                   and not any(st.get(k, 0) for k in HARMFUL_STATUSES) and st.get('error', 0) < HARMFUL_ERRORS),
            'pipeline_clean': (pipeline.get('failed_calls') == 0 and pipeline.get('refused_calls') == 0
                               and pipeline.get('memory_stop') is False and pipeline.get('swaps_clean') is True
                               and pipeline.get('cleanup_proven') is True and pipeline.get('failure') is None),
            'room_to_spare': None if not isinstance(low, (int, float)) else low >= STOP_FLOOR + ROOM_POINTS}


def post_state(host, row):
    m = models_state(host)
    row['post'] = {'decision_instance_right': m['decision_instance']['loaded_and_right'], 'why': m['decision_instance']['why'],
                   'native_loaded': m['native_loaded'], 'utc': utc(host.now())}
    if not m['decision_instance']['loaded_and_right']:
        row['alerts'].append('DECISION INSTANCE NOT LOADED AND RIGHT: %s (THE TOOL DOES NOT RELOAD IT; THE COLLECTOR\'S KEEPER DOES)'
                             % m['decision_instance']['why'])
    if m['native_loaded']:
        row['alerts'].append('MF-NATIVE INSTANCE STILL LOADED: %s' % ', '.join(m['native_loaded']))


# ============================================================================ the record
def append_record(path, row):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    with os.fdopen(fd, 'a') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n'); f.flush(); os.fsync(f.fileno())
    if new:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(value, indent=2, sort_keys=True) + '\n'); f.flush(); os.fsync(f.fileno())


def read_rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


def streaks(rows):
    out = {}
    for key, profile, target in STREAKS:
        n = 0
        for r in rows:
            if r.get('command') == 'run' and r.get('launched') is True and r.get('profile') == profile:
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
            'switch_offs': None, 'pipeline': None, 'memory': None, 'collector': None, 'stop': None, 'post': None,
            'verdicts': None, 'counts': False, 'alerts': []}


def watch(host, row, proc, stopper, t_start, t_launch, wall_s, own):
    """Every WATCH_S: the launcher, the collector since t_start, the memory. A breach (or an overrun of the wall clock) stops
    the run once; the loop then waits for the pipeline's own cleanup."""
    first = None
    while proc.poll() is None:
        host.sleep(WATCH_S)
        if proc.poll() is not None:
            break
        m = memory(host)
        if m['free_percent'] is not None:
            own['lowest'] = m['free_percent'] if own['lowest'] is None else min(own['lowest'], m['free_percent'])
        if m['pressure_level'] is not None:
            own['max_pressure'] = max(own['max_pressure'] or 0, m['pressure_level'])
        if (not row.get('identity_alerted') and host.now() >= t_launch + IDENTITY_WAIT_S
                and not (stopper.path.is_file() or stopper.rec['signalled'])):
            row['identity_alerted'] = True
            row['alerts'].append('OBSERVER IDENTITY FILE NOT WRITTEN WITHIN %d S OF LAUNCH: %s' % (
                IDENTITY_WAIT_S, 'A BREACH WILL INTERRUPT THE LAUNCHER (SIGINT) INSTEAD'
                if (row.get('launcher') or {}).get('own_group')
                else 'A BREACH CANNOT INTERRUPT THE LAUNCHER: IT IS NOT IN ITS OWN GROUP'))
        if first is None:
            h = collector_health(host, since=t_start)
            if h['breach']:
                first = h['breach']; row['stopped'] = 'collector breach'; row['first_breach'] = first
            elif host.now() > t_launch + wall_s + CLEANUP_S + 2 * WATCH_S:
                first = 'overran its wall clock'; row['stopped'] = 'overran its wall clock'
        if first is not None:
            if not stopper.rec['signalled']:
                stopper.attempt()
            if stopper.sent_t is not None and host.now() > stopper.sent_t + STOP_WAIT_S:
                row['alerts'].append('PIPELINE CLEANUP NOT SEEN WITHIN %d S OF THE STOP SIGNAL: THE LAUNCHER WAS LEFT RUNNING' % STOP_WAIT_S)
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
        say('dry run: would boot out %s, unload %s, stage %s and its twin, preflight the twin, write the gate, launch, watch '
            'every %d s, restore, append the record row. Nothing was changed.' % (
                PICKS_LABEL if s['pick_reader']['listed'] else 'nothing', ', '.join(o['instance_id'] for o in s['models']['others']) or 'nothing',
                args.profile, WATCH_S))
        return 2 if s['refusals'] else 0
    switch = SwitchOffs(host, row); proc = None; stopper = None; work = None; own = {'lowest': None, 'max_pressure': None}
    swap_start = None
    try:
        if s['refusals']:
            raise Refused('; '.join(s['refusals']))
        say('check passed; switching off: ' + s['announcement'])
        switch.apply(s)
        work = fresh_folder(host, args.profile); twin = Path(str(work) + '-twin')
        row['staged_folder'], row['twin_folder'] = str(work), str(twin)
        candidate = stage(host, args.profile, flags, work)
        stage(host, args.profile, flags, twin)
        require_stoppable(work)
        threshold, free = require_free(host, work)
        row['start_threshold'], row['free_at_start'] = threshold, free
        preflight(host, twin)
        gate = write_gate(host, work, candidate, args.announced)
        swap_start = memory(host)['swap_used_bytes']
        t_launch = host.now()
        stopper = Stopper(host, work, row, t_launch)
        proc = launch(host, work, gate, row)
        row['launched'] = True; row['utc_launch'] = utc(t_launch)
        say('launched %s; watching every %d s' % (work.name, WATCH_S))
        watch(host, row, proc, stopper, t_start, t_launch, s['durations']['wall_s'], own)
    except Refused as e:
        row['refused'] = str(e)
    except KeyboardInterrupt:
        row['stopped'] = 'interrupted (Ctrl-C)'
    except Exception as e:
        row['error'] = '%s: %s' % (type(e).__name__, e)
    finally:
        try:
            # Still running after Ctrl-C or an exception (the watch itself returns only once the launcher has ended, or
            # after it gave up waiting for the cleanup, which is alerted): stop it the documented way and wait.
            if proc is not None and proc.poll() is None and not any('LEFT RUNNING' in a for a in row['alerts']):
                stopper.attempt(); wait_after_stop(host, proc, stopper, row)
        except BaseException as e:     # a second Ctrl-C while waiting: the restore below still runs
            row['alerts'].append('THE STOP WAS INTERRUPTED: %s' % type(e).__name__)
        try:
            if stopper is not None and stopper.rec['by'] == 'launcher interrupt':
                after_interrupt_check(host, stopper, row)
        except BaseException as e:
            row['alerts'].append('THE CHECK AFTER THE LAUNCHER INTERRUPT DID NOT COMPLETE: %s' % type(e).__name__)
        if stopper is not None and stopper.rec['wanted'] and not stopper.rec['signalled']:
            row['alerts'].append('RUN NOT STOPPED: OBSERVER NOT SIGNALLED: %s' % stopper.rec['not_signalled'])
        restored = switch.restore()
        if not restored:
            row['alerts'].append('PICK READER NOT RESTORED: %s IS NOT LOADED AGAIN FROM %s; BOOTSTRAP IT BY HAND' % (
                PICKS_LABEL, (row['switch_offs']['pick_reader'] or {}).get('before', {}).get('plist')))
        try:
            finish_run_row(host, row, work, plan, t_start, swap_start, own)
        except Exception as e:
            row['alerts'].append('THE RUN ROW COULD NOT BE COMPLETED: %s: %s' % (type(e).__name__, e))
        written = True
        try:
            append_record(host.record, row)
        except Exception as e:
            written = False; row['alerts'].append('RECORD NOT WRITTEN to %s: %s: %s' % (host.record, type(e).__name__, e))
        say('RUN SUMMARY')
        for line in summary_lines(row):
            say(line)
    if not restored or not written:
        return 3
    if row['refused']:
        return 2
    if row['error'] or row['stopped']:
        return 1
    return 3 if row['alerts'] else 0


def finish_run_row(host, row, work, plan, t_start, swap_start, own):
    row['utc_end'] = utc(host.now())
    receipt = {}
    if work is not None and row['launched']:
        row['pipeline'], receipt = pipeline_outcome(work, plan)
    else:
        row['pipeline'] = pipeline_outcome(Path('/nonexistent'), plan)[0]
    health = collector_health(host, since=t_start)
    row['collector'] = collector_figures(health)
    if row.get('first_breach'):
        row['collector']['breach'] = row['first_breach']
    row['memory'] = memory_outcome(receipt, swap_start, memory(host)['swap_used_bytes'], own)
    post_state(host, row)
    if row['launched']:
        row['verdicts'] = verdicts(row['collector'], row['pipeline'], row['memory'])
        row['counts'] = all(v is True for v in row['verdicts'].values())


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


def sample_phase(host, seconds, t_since):
    """Memory samples SAMPLE_S apart for `seconds`, the collector checked every WATCH_S; (samples, breach or None)."""
    samples = []; start = host.now(); next_check = start + WATCH_S
    while host.now() - start < seconds:
        t = host.now()
        samples.append(dict(memory(host), t=round(t - start, 2)))
        if t >= next_check:
            next_check += WATCH_S
            breach = collector_health(host, since=t_since)['breach']
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
    switch = SwitchOffs(host, result); lp = None; asked = False; base, hold = [], []
    try:
        if s['refusals']:
            raise Refused('; '.join(s['refusals']))
        say('check passed; switching off: ' + s['announcement'])
        switch.apply(s)
        work = fresh_folder(host, 'loadtest-' + re.sub(r'[^a-z0-9.-]', '-', model.lower()))
        result['staged_folder'] = str(work)
        stage(host, 'swarm8', ['--model', model], work)
        result['start_threshold'], result['free_at_start'] = require_free(host, work)
        lp = host.load_path(work, Path(str(work) + '-out'), model, hold_s)
        lp.baseline()
        base, breach = sample_phase(host, BASELINE_S, t0)
        if breach:
            result['stopped'] = 'collector breach'; result['first_breach'] = breach
        else:
            asked = True
            try:
                result['load'].update(lp.load(), ok=True)
            except Exception as e:
                result['load']['refusal'] = '%s: %s' % (type(e).__name__, e)
                result['error'] = 'the load failed: ' + result['load']['refusal']
            if result['load']['ok']:
                hold, breach = sample_phase(host, hold_s, t0)
                if breach:
                    result['stopped'] = 'collector breach'; result['first_breach'] = breach
    except Refused as e:
        result['refused'] = str(e)
    except KeyboardInterrupt:
        result['stopped'] = 'interrupted (Ctrl-C)'
    except Exception as e:
        result['error'] = '%s: %s' % (type(e).__name__, e)
    finally:
        if lp is not None and asked:
            try:
                proven, detail = lp.unload(); result['unload'] = {'proven': bool(proven), 'detail': detail}
            except BaseException as e:
                result['unload'] = {'proven': False, 'failure': '%s: %s' % (type(e).__name__, e)}
            if not result['unload']['proven']:
                result['alerts'].append('THE TEST MODEL\'S UNLOAD IS NOT PROVEN: %s' % result['unload'])
        if lp is not None:
            try:
                lp.close()
            except BaseException as e:
                result['alerts'].append('THE LOAD PATH\'S HELPER DID NOT STOP CLEANLY: %s' % type(e).__name__)
        restored = switch.restore()
        if not restored:
            result['alerts'].append('PICK READER NOT RESTORED: %s IS NOT LOADED AGAIN; BOOTSTRAP IT BY HAND' % PICKS_LABEL)
        written = True
        try:
            result['utc_end'] = utc(host.now())
            load_test_figures(result, base, hold, hold_s)
            health = collector_health(host, since=t0)
            result['collector'] = collector_figures(health)
            if result.get('first_breach'):
                result['collector']['breach'] = result['first_breach']
            post_state(host, result)
            path = host.load_tests / ('%s-%s.json' % (result['utc_start'].replace(':', '').replace('+0000', 'Z'),
                                                      re.sub(r'[^A-Za-z0-9.-]', '-', model)))
            write_json(path, result); say('result: %s' % path)
        except Exception as e:
            written = False; result['alerts'].append('RESULT NOT WRITTEN: %s: %s' % (type(e).__name__, e))
        say('LOAD TEST %s: %s; baseline %s; after load %s; setup cost %s points; fall %s points; highest pressure %s; swap change %s; '
            'collector %s; unload proven %s; %s' % (
                model, result['refused'] or result['error'] or result['stopped'] or 'held to its end', result['baseline'],
                result['after_load'], result['setup_cost_points'], result['fall_points'], result['highest_pressure_level'],
                result['swap_change_bytes'], result.get('collector'), result['unload'].get('proven'),
                '; '.join(result['alerts']) or 'no alerts'))
    if not restored or not written:
        return 3
    if result['refused']:
        return 2
    if result['error'] or result['stopped']:
        return 1
    return 3 if result['alerts'] else 0


# ============================================================================ check, record, main
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
    rows = read_rows(host.record)
    for r in rows:
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
    for name in ('check', 'load-test', 'run', 'record'):
        q = sub.add_parser(name)
        q.add_argument('--dry-run', action='store_true')
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
        if args.cmd == 'load-test':
            return cmd_load_test(host, args)
        return cmd_run(host, args)
    except Refused as e:                   # an argument refusal, before anything was read or changed
        host.out('refused: %s' % e)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
