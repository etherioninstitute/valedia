"""TMF6: the MiroFish operator command, against fakes only. Nothing here runs launchctl, lms, ps, memory_pressure, sysctl,
the stager or the launcher, sends a request to LM Studio, opens the collector's real store, loads a model or signals a real
process: a World object stands in for all of them (an injected command runner, spawn, HTTP function, clock and sleeper, signal
sender and model load path) and a scratch SQLite file stands in for the collector's store."""
import ast, copy, fcntl, hashlib, importlib.util, json, os, random, signal, sqlite3, subprocess, sys, tempfile, time, types, unittest
from pathlib import Path

HERE = Path(__file__).absolute().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name + '_under_test', HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


OP = load('mirofish_operator')
T0 = 1794830400.0                      # 2026-11-16 12:00 UTC
START = T0 + 600                        # the world starts 10 minutes past a full hour: clear of the pick reader's window
SCHEMA = """CREATE TABLE decisions (id INTEGER PRIMARY KEY, watch_id INTEGER, ticker TEXT, slot_iso TEXT, slot_ts REAL,
    decision_iso TEXT, decision_ts REAL, status TEXT, forced INTEGER, battery_id TEXT, state_json TEXT, request_sha256 TEXT,
    model_tag TEXT, model_digest TEXT, latency_s REAL, raw_response TEXT, answers_json TEXT, input_tokens INTEGER,
    output_tokens INTEGER, error TEXT);
CREATE TABLE heartbeats (id INTEGER PRIMARY KEY, iso TEXT, ts REAL, watched INTEGER, kalshi_requests INTEGER,
    kalshi_failures INTEGER, model_calls INTEGER, paused INTEGER);"""
PICKS_PLIST = '/fake/LaunchAgents/com.valedia.picks.hourly.plist'
OBSERVER_PID, OBSERVER_START = 5151, 'Mon Nov 16 12:00:05 2026'
LAUNCHER_PID, LAUNCHER_START = 4242, 'Mon Nov 16 12:00:04 2026'
LINK_OFF = 'You have disabled LM Link. To re-enable it, run lms link enable.\n'
TOOL_PID, TOOL_PARENT, TOOL_START = 3131, 3030, 'Mon Nov 16 11:59:00 2026'      # the operator tool itself and its parent
NEAR_NEUTRAL = 'derived report not usable evidence'          # src/mirofish_provenance.py:437, via mirofish_native_run.py:806
COLLECTOR_PID, COLLECTOR_START = 7171, 'Mon Nov 16 08:00:00 2026'
PICKS_PID = 6161
SECRET = 'SECRETKEY'                     # in every fake command line: it must never reach an output, a row or a file
# Addendum 4, part B: real child processes for the kill tests. A launcher that starts an observer and stops it on SIGINT;
# an observer that ends on TERM; both end by themselves within 60 s. The stager writes them in place of the staged scripts.
REAL_LAUNCHER = '''import os, subprocess, sys, time
preflight = False
deadline=time.monotonic()+(30 if preflight else WALL);stop=None
work = sys.argv[sys.argv.index('--work') + 1]
entry = os.path.join(work, 'owned-source', 'observer-entry')
observer = subprocess.Popen([sys.executable, '-B', os.path.join(entry, 'mirofish_native_run.py')], cwd=work,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
with open(os.path.join(os.path.dirname(os.path.dirname(work)), 'real-pids'), 'a') as f:
    f.write(str(observer.pid) + chr(10))
end = time.monotonic() + 60
try:
    while observer.poll() is None and time.monotonic() < end:
        time.sleep(0.02)
except KeyboardInterrupt:
    observer.terminate(); observer.wait()
if observer.poll() is None:
    observer.kill(); observer.wait()
'''
REAL_OBSERVER = '# a stand-in that writes builder/observer-identity.json in the real pipeline\nimport time\ntime.sleep(60)\n'


def decision_entry():
    return {'key': 'elephant-1', 'type': 'decision', 'decision_type': 'openjev', 'publisher': 'debackerl',
            'quantization': {'name': 'Q8_0'}, 'size_bytes': 10151733984,
            'loaded_instances': [{'id': 'elephant-1', 'config': {'context_length': 8192, 'parallel': 1,
                                                                'physical_batch_size': 2048, 'eval_batch_size': 2048}}]}


def listing():
    return {'models': [decision_entry(),
                       {'key': 'google/embedding-gemma-300m', 'type': 'embedding',
                        'loaded_instances': [{'id': 'ym-research-embedding', 'config': {'context_length': 2048}}]},
                       {'key': 'qwen/qwen3.8-27b', 'type': 'llm', 'loaded_instances': [{'id': 'qwen3.8-27b-chat', 'config': {}}]},
                       {'key': 'qwen3-8b', 'type': 'llm', 'loaded_instances': []}]}


def done(rc=0, out='', err=''):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeLauncher:
    def __init__(self, world, work):
        self.world, self.work, self.pid, self.returncode = world, Path(work), LAUNCHER_PID, None
        self.started = world.t

    def poll(self):
        w = self.world
        if self.returncode is not None:
            return self.returncode
        termed = w.procs.get(OBSERVER_PID, {}).get('termed_at') or w.procs.get(LAUNCHER_PID, {}).get('termed_at')
        if (termed is not None and w.t >= termed + w.cleanup_s) or (termed is None and w.t >= self.started + w.run_s):
            receipt = copy.deepcopy(w.receipt)
            if termed is not None:
                receipt.update(complete=False, failure={'type': 'NativeRefused', 'reason': 'supervisor interrupted'})
            if w.receipt_raw is not None:
                (self.work / 'builder/native-run-receipt.json').write_text(w.receipt_raw)
            elif w.write_receipt:
                (self.work / 'builder/native-run-receipt.json').write_text(json.dumps(receipt))
            (self.work / 'builder/gateway-ledger.json').write_text(json.dumps(w.ledger))
            if w.procs.get(LAUNCHER_PID, {}).get('termed_at') is not None and w.native_left_after_interrupt:
                w.models['models'].append({'key': 'qwen3-8b', 'type': 'llm', 'loaded_instances': [{'id': 'mf-native-left', 'config': {}}]})
            w.procs.pop(OBSERVER_PID, None); w.procs.pop(LAUNCHER_PID, None)
            self.returncode = w.exit_override if w.exit_override is not None else (0 if receipt.get('complete') else 1)
            w.launcher_end_t = w.t
        return self.returncode

    def wait(self, timeout=None):
        return self.poll()

    def send_signal(self, sig):
        # as subprocess.Popen.send_signal: polls first and never signals a child that has been reaped
        if self.poll() is None:
            self.world.handle_signals.append((self.pid, sig)); self.world.send_signal(self.pid, sig)


class FakeLoadPath:
    def __init__(self, world, work, out, profile):
        self.world, self.work, self.out, self.profile = world, work, out, profile
        self.identifier = 'mf-native-' + 'a' * 32

    def baseline(self):
        return {'embedding': {'instanceReference': 'emb'}}

    def load(self):
        w = self.world; w.loads.append(self.profile)
        if w.load_error:
            raise RuntimeError(w.load_error)
        w.models['models'].append({'key': self.profile, 'type': 'llm', 'loaded_instances': [{'id': self.identifier, 'config': {}}]})
        w.free = w.free_after_load; w.load_t = w.t
        return {'identifier': self.identifier}

    def unload(self):
        w = self.world; w.unloads_via_pipeline.append(self.identifier)
        w.models['models'] = [e for e in w.models['models'] if not any(i['id'] == self.identifier for i in e['loaded_instances'])]
        w.free = 80
        return True, {'unloaded': self.identifier}

    def close(self):
        return None


class World:
    def __init__(self, tmp):
        self.tmp = Path(tmp); self.t = START
        self.store = self.tmp / 'orderbook_loop.sqlite'
        with sqlite3.connect(self.store) as db:
            db.executescript(SCHEMA)
        self.crew = self.tmp / 'crew'; self.crew.mkdir()
        self.collector_listed = True; self.beating = True; self.per_minute = 2
        self.latency = lambda t: 1.2; self.status = lambda t: 'ok'
        self.picks_loaded = True; self.bootout_rc = 0; self.bootstrap_rc = 0; self.picks_running = False
        self.link_text = LINK_OFF; self.link_rc = 0
        self.write_identity = True; self.pipeline_writes_identity = True; self.launcher_pgid = LAUNCHER_PID
        self.native_left_after_interrupt = False; self.picks_minute = None
        self.link_err = ''; self.identity = None; self.write_receipt = True; self.receipt_raw = None; self.exit_override = None
        self.bootout_raises_after = None; self.bootstrap_raises = None; self.bootstrap_raises_after = None
        self.fsyncs = []; self.timed = []
        self.handle_signals = []; self.launcher_end_t = None; self.handlers = {}; self.installs = []
        self.ps_unreadable = set(); self.ps_raises = {}; self.fsync_raises = None; self.staged_wall = None
        self.commands = {}; self.collector_pid = None; self.scan_raises = None; self.command_raises = None
        self.real = False; self.kill_at = None; self.term_in_spawn = False; self.on_bootstrap = None; self.on_fsync = None
        self.staged_manifest_wall = None; self.staged_startup = True; self.intent_at_bootout = 'unseen'
        self.free, self.free_after_load, self.pressure, self.swap_mb = 80, 60, 1, 100.0
        self.models = listing(); self.unload_status = 200
        self.procs = {TOOL_PID: {'start': TOOL_START, 'pgid': TOOL_PARENT, 'ppid': TOOL_PARENT},
                      TOOL_PARENT: {'start': 'Mon Nov 16 09:00:00 2026', 'pgid': TOOL_PARENT, 'ppid': 1}}
        self.calls = []; self.http_calls = []; self.signals = []; self.out = []
        self.launcher = None; self.spawned = []; self.spawn_error = None; self.stage_rc = 0; self.preflight_rc = 0; self.threshold = 56
        self.on_sleep = []; self.run_s = 180; self.cleanup_s = 5; self.identity_start = OBSERVER_START
        self.loads = []; self.unloads_via_pipeline = []; self.load_error = None; self.load_t = None
        self.receipt = {'complete': True, 'cleanup_proven': True, 'paper_handoff': True, 'resource_failures': [],
                        'events': [{'event': 'pre_resource'}],
                        'resource': {'lowest_sustained_free_percent_10s': 47, 'max_pressure_level': 1,
                                     'co_resident_rss_bytes_max': 21 * 2**30}}
        self.ledger = {'calls': [{'id': 'c1', 'kind': 'chat', 'state': 'complete'}, {'id': 'c2', 'kind': 'embedding', 'state': 'complete'}]}
        self.last_minute = int((START - 600) // 60)
        self.collector_step()

    # ---- the collector writing its store, once a minute
    def collector_step(self):
        with sqlite3.connect(self.store) as db:
            while (self.last_minute + 1) * 60 <= self.t:
                self.last_minute += 1; ts = self.last_minute * 60.0
                if self.beating:
                    db.execute('INSERT INTO heartbeats (ts, paused) VALUES (?, 0)', (ts,))
                for k in range(self.per_minute):
                    db.execute('INSERT INTO decisions (decision_ts, status, latency_s, answers_json, raw_response, state_json) '
                               'VALUES (?, ?, ?, ?, ?, ?)', (ts + 1 + k, self.status(ts), self.latency(ts), '{"a":1}', 'x', '{}'))

    def now(self):
        return self.t

    def sleep(self, seconds):
        if self.real:
            if self.kill_at == 'sleep' and self.spawned:
                os.kill(os.getpid(), signal.SIGKILL)
            time.sleep(0.25)               # real time for real children to end and be reaped
        self.t += seconds; self.collector_step()
        for callback in list(self.on_sleep):
            callback(self)

    # ---- commands
    def run(self, argv, cwd=None, env=None, timeout=None):
        argv = [str(a) for a in argv]; self.calls.append(argv); self.timed.append((self.t, argv))
        if argv[0].endswith('lms') and argv[1:] == ['link', 'status']:
            return done(self.link_rc, self.link_text, self.link_err)
        if argv[:2] == ['/bin/launchctl', 'print']:
            label = argv[2].rsplit('/', 1)[1]
            if label == OP.COLLECTOR_LABEL:
                return done(0 if self.collector_listed else 113, '\tprogram = /x\n%s\targuments = {\n\t\tcollector --key=SECRETKEY\n\t}\n' % (
                    '\tpid = %d\n' % self.collector_pid if self.collector_pid else ''))
            if label == OP.PICKS_LABEL:
                if not self.picks_loaded:
                    return done(113, '', 'Could not find service')
                state = 'running\n\tpid = %d' % PICKS_PID if self.picks_running else 'not running'
                trigger = '' if self.picks_minute is None else (
                    '\tevent triggers = {\n\t\tcom.valedia.picks.hourly.1 => {\n\t\t\tdescriptor = {\n'
                    '\t\t\t\t"Minute" => %d\n\t\t\t}\n\t\t}\n\t}\n' % self.picks_minute)
                return done(0, 'gui/501/com.valedia.picks.hourly = {\n\tpath = %s\n\tstate = %s\n\targuments = {\n'
                               '\t\tpicks --token=SECRETKEY\n\t}\n\tstdout path = /x/out.log\n%s}\n' % (PICKS_PLIST, state, trigger))
        if argv[:2] == ['/bin/launchctl', 'bootout']:
            intent = self.tmp / 'artifacts/mirofish/operator-intent.json'
            self.intent_at_bootout = json.loads(intent.read_text()) if intent.is_file() else None
            if self.bootout_rc == 0:
                self.picks_loaded = False
            if self.bootout_raises_after:
                raise self.bootout_raises_after()                  # Ctrl-C right after the bootout took effect
            return done(self.bootout_rc)
        if argv[:2] == ['/bin/launchctl', 'bootstrap']:
            if self.on_bootstrap:
                self.on_bootstrap(self)
            if self.bootstrap_raises:
                raise self.bootstrap_raises()                      # before it took effect
            if self.bootstrap_rc == 0 and argv[3] == PICKS_PLIST:
                self.picks_loaded = True
            if self.bootstrap_raises_after:
                raise self.bootstrap_raises_after()
            return done(self.bootstrap_rc)
        if argv == ['/usr/bin/memory_pressure']:
            return done(0, 'The system has 68719476736 (4194304 pages with a page size of 16384).\n'
                           'System-wide memory free percentage: %d%%\n' % self.free)
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level']:
            return done(0, '%d\n' % self.pressure)
        if argv == ['/usr/sbin/sysctl', '-n', 'vm.swapusage']:
            return done(0, 'total = 2048.00M  used = %.2fM  free = 1000.00M  (encrypted)\n' % self.swap_mb)
        if argv[:2] == ['/bin/ps', '-p'] and self.real and int(argv[2]) in self.real_set():
            if self.kill_at == 'ps':
                os.kill(os.getpid(), signal.SIGKILL)
            r = subprocess.run(argv, capture_output=True, text=True)          # read-only: pid, ppid, pgid and start only
            return done(r.returncode, r.stdout, '')
        if argv[:2] == ['/bin/ps', '-p'] and int(argv[2]) in self.ps_raises:
            raise self.ps_raises.pop(int(argv[2]))()              # once
        if argv[:2] == ['/bin/ps', '-p'] and int(argv[2]) in self.ps_unreadable:
            return done(1, '')
        if argv[:2] == ['/bin/ps', '-p']:
            proc = self.procs.get(int(argv[2]))
            if proc is None:
                return done(1, '')
            assert argv[3:] == ['-o', 'pid=,ppid=,pgid=,lstart='], argv          # never a command line
            return done(0, '%d %d %d %s\n' % (int(argv[2]), proc.get('ppid', 1), proc['pgid'], proc['start']))
        if any(a.endswith('mirofish_native_stage.py') for a in argv):
            work = Path(argv[argv.index('--work') + 1])
            if self.stage_rc:
                return done(self.stage_rc, '', 'refused: fake')
            (work / 'builder').mkdir(parents=True); (work / 'owned-source/observer-entry').mkdir(parents=True)
            (work / 'owned-source/observer-entry/mirofish_native_run.py').write_text(
                "atomic(work/'builder/observer-identity.json', identity)\n" if self.pipeline_writes_identity else '# an older revision\n')
            wall = self.staged_wall or OP.run_durations('swarm8', {'model': 'qwen3-8b'})['wall_s']
            if self.staged_startup:
                (work / 'owned-source/observer-entry/mirofish_native_startup.py').write_text(
                    REAL_LAUNCHER.replace('WALL', str(wall)) if self.real else
                    "    deadline=time.monotonic()+(30 if preflight else %d);stop=threading.Event();errors=[]\n" % wall)
            if self.real:
                (work / 'owned-source/observer-entry/mirofish_native_run.py').write_text(REAL_OBSERVER)
            manifest = {'launch_authorized': False, 'model_profile': {'start_threshold': {'percent_free': self.threshold}},
                        'uniform_policy': {'wall_seconds': self.staged_manifest_wall or wall}}
            (work / 'builder/candidate-manifest.json').write_text(json.dumps(manifest))
            digest = hashlib.sha256((work / 'builder/candidate-manifest.json').read_bytes()).hexdigest()
            return done(0, json.dumps({'status': 'STAGED_NOT_AUTHORIZED_NO_SERVICES', 'work': str(work), 'candidate_sha256': digest}) + '\n')
        if any(a.endswith('mirofish_native_startup.py') for a in argv) and '--preflight' in argv:
            work = Path(argv[argv.index('--work') + 1])
            (work / 'builder/startup-preflight.json').write_text('{}')
            return done(self.preflight_rc, json.dumps({'exit': self.preflight_rc, 'preflight': True}))
        raise AssertionError('unexpected command: %r' % argv)

    # ---- addendum 4: real children (part B), process commands (part A)
    @property
    def real_file(self):
        return self.tmp / 'real-pids'

    def real_set(self):
        try:
            return {int(x) for x in self.real_file.read_text().split()}
        except (OSError, ValueError):
            return set()

    def ours(self, pid):
        """The command of a real child this test started, still running (its command names this test's folder), else
        None: a pid that ended and was reused is never read further nor signalled."""
        if pid not in self.real_set():
            return None
        r = subprocess.run(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
        text = r.stdout.strip() if r.returncode == 0 else ''
        return text if str(self.tmp) in text else None

    def proc_command(self, pid):
        if self.command_raises:
            raise self.command_raises('%s fake failure' % SECRET)
        if self.real and pid in self.real_set():
            return self.ours(pid)
        return self.commands.get(pid) if pid in self.procs else None

    def proc_scan(self):
        if self.scan_raises:
            raise self.scan_raises('fake scan failure')
        return sorted(set(self.procs) | self.real_set())

    def spawn(self, argv, cwd=None, env=None):
        argv = [str(a) for a in argv]; self.spawned.append(argv)
        if self.spawn_error:
            raise self.spawn_error
        work = Path(argv[argv.index('--work') + 1])
        if self.real:
            if self.term_in_spawn:
                self.fire(signal.SIGTERM)
            p = subprocess.Popen([sys.executable, '-B', 'mirofish_native_startup.py', '--work', str(work), '--gate', argv[-1]],
                                 cwd=str(work / 'owned-source/observer-entry'), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            with open(self.real_file, 'a') as f:
                f.write('%d\n' % p.pid)
            if self.kill_at == 'spawn':
                os.kill(os.getpid(), signal.SIGKILL)
            return p
        self.commands[LAUNCHER_PID] = ' '.join(argv) + ' --note=%s-L' % SECRET
        self.commands[OBSERVER_PID] = ('/usr/bin/sandbox-exec -f %s/builder/observer.sb /x/bin/python3.11 -B '
                                       '%s/owned-source/observer-entry/mirofish_native_run.py --token=%s-O' % (work, work, SECRET))
        self.procs[LAUNCHER_PID] = {'start': LAUNCHER_START, 'pgid': self.launcher_pgid, 'ppid': TOOL_PID}
        self.procs[OBSERVER_PID] = {'start': OBSERVER_START, 'pgid': OBSERVER_PID, 'ppid': LAUNCHER_PID}
        if self.identity is not None:
            (work / 'builder/observer-identity.json').write_text(
                self.identity if isinstance(self.identity, str) else json.dumps(self.identity))
        elif self.write_identity:
            (work / 'builder/observer-identity.json').write_text(json.dumps(
                {'pid': OBSERVER_PID, 'start': self.identity_start, 'pgid': OBSERVER_PID, 'written_utc': '2026-11-16T12:00:05+00:00'}))
        self.launcher = FakeLauncher(self, work); return self.launcher

    def send_signal(self, pid, sig):
        self.signals.append((pid, sig))
        if self.real and pid in self.real_set():
            if self.ours(pid):                                    # a process this test started, still running
                os.kill(pid, sig)
            return
        if pid in self.procs:
            self.procs[pid].setdefault('termed_at', self.t)

    def fsync(self, fd):
        if self.real and self.kill_at == 'fsync' and self.spawned:
            os.kill(os.getpid(), signal.SIGKILL)
        if self.on_fsync:
            self.on_fsync(self, fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b'\0').decode())
        if self.fsync_raises:
            exc, self.fsync_raises = self.fsync_raises, None
            raise exc()
        self.fsyncs.append(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b'\0').decode()); os.fsync(fd)

    def install_signal(self, signum, handler):
        self.installs.append((self.t, len(self.calls), signum)); old = self.handlers.get(signum, 'default')
        self.handlers[signum] = handler; return old

    def fire(self, signum):
        handler = self.handlers.get(signum)
        if callable(handler):
            handler(signum, None)

    def http(self, method, url, body=None, timeout=None):
        self.http_calls.append((method, url, copy.deepcopy(body))); self.timed.append((self.t, [method, url]))
        if method == 'GET' and url.endswith('/api/v1/models'):
            return 200, copy.deepcopy(self.models)
        if method == 'POST' and url.endswith('/api/v1/models/unload'):
            if self.unload_status != 200:
                return self.unload_status, {'error': 'fake failure'}
            for e in self.models['models']:
                e['loaded_instances'] = [i for i in e['loaded_instances'] if i['id'] != body['instance_id']]
            return 200, {'instance_id': body['instance_id']}
        raise AssertionError('unexpected request: %s %s' % (method, url))

    def host(self, **overrides):
        values = dict(run=self.run, spawn=self.spawn, http=self.http, now=self.now, sleep=self.sleep,
                      send_signal=self.send_signal, disk_free=lambda path: 400 * 10**9, uid=501, store=self.store,
                      crew=self.crew, record=self.tmp / 'artifacts/runs.jsonl', load_tests=self.tmp / 'artifacts/load-tests',
                      load_path=lambda work, out, profile, hold_s: FakeLoadPath(self, work, out, profile),
                      out=self.out.append, lms='/fake/bin/lms', self_pid=TOOL_PID, parent_pid=TOOL_PARENT, fsync=self.fsync,
                      install_signal=self.install_signal, intent=self.tmp / 'artifacts/mirofish/operator-intent.json',
                      proc_command=self.proc_command, proc_scan=self.proc_scan, picks_plist=PICKS_PLIST,
                      lock=self.tmp / 'artifacts/mirofish/operator.lock')
        values.update(overrides)
        return OP.Host(**values)

    # ---- what happened
    def bootouts(self):
        return [c for c in self.calls if c[:2] == ['/bin/launchctl', 'bootout']]

    def posts(self):
        return [c for c in self.http_calls if c[0] == 'POST']

    def stages(self):
        return [c for c in self.calls if any(a.endswith('mirofish_native_stage.py') for a in c)]

    def rows(self, path=None):
        path = path or self.tmp / 'artifacts/runs.jsonl'
        rows = []
        for line in (path.read_text().splitlines() if path.is_file() else []):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass                                                 # a torn line (addendum 2, item 6)
        return rows

    def text(self):
        return '\n'.join(str(x) for x in self.out)


RUN = ['run', '--profile', 'swarm8', '--model', 'qwen3-8b', '--announced', 'Owner told 11:55 UTC: swarm8 on the 8B, picks off']


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.w = World(self.dir.name)

    def tearDown(self):
        for pid in self.w.real_set():          # only the children this test started, while they still run
            if self.w.ours(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        self.dir.cleanup()

    def main(self, argv, **overrides):
        return OP.main(argv, host=self.w.host(**overrides))


class StoreRules(Base):
    def test_no_issuable_sql_names_a_locked_column(self):
        self.assertTrue(OP.SQL)
        for sql in OP.SQL:
            for word in ('answers_json', 'raw_response', 'state_json'):
                self.assertNotIn(word, sql)

    def test_every_sql_literal_in_the_module_is_in_the_allowed_tuple(self):
        tree = ast.parse((HERE / 'mirofish_operator.py').read_text())
        literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and ('SELECT' in n.value.upper() or 'INSERT' in n.value.upper() or 'UPDATE ' in n.value.upper())}
        self.assertTrue(literals)
        self.assertEqual(literals - set(OP.SQL), set())

    def test_a_query_outside_the_tuple_is_refused(self):
        with self.assertRaises(OP.OperatorError):
            OP.query(self.w.host(), 'SELECT answers_json FROM decisions')
        rows = OP.query(self.w.host(), OP.SQL_LAST_HEARTBEAT)
        self.assertEqual(len(rows), 1)

    def test_the_store_is_opened_read_only(self):
        db = OP.open_store(self.w.store)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('INSERT INTO heartbeats (ts) VALUES (1)')
        finally:
            db.close()

    def test_model_failure_statuses_are_collector_statuses(self):
        source = (HERE.parent / 'valedia_trader/research_loop/orderbook_loop.py').read_text()
        statuses = next(ast.literal_eval(n.value) for n in ast.walk(ast.parse(source))
                        if isinstance(n, ast.Assign) and any(getattr(t, 'id', None) == 'STATUSES' for t in n.targets))
        self.assertTrue(set(OP.MODEL_FAILURE_STATUSES) <= set(statuses))
        self.assertNotIn('ok', OP.MODEL_FAILURE_STATUSES); self.assertNotIn('stale_data', OP.MODEL_FAILURE_STATUSES)
        self.assertEqual(set(OP.MODEL_FAILURE_STATUSES), {'model_unavailable', 'tag_mismatch', 'late', 'error'})


class Pure(unittest.TestCase):
    def test_lm_link_wording_addendum_6(self):
        self.assertTrue(OP.link_disabled(LINK_OFF, '', 0))
        self.assertTrue(OP.link_disabled('\n   You have disabled LM Link. To re-enable it, run lms link enable.  \n', '', 0))
        for text, rc in (('You have enabled LM Link.', 0), ('', 0), ('   \n', 0), (LINK_OFF, 1),
                         ('LM Link status\nYou have disabled LM Link.', 0), (LINK_OFF.lower(), 0),
                         ('LM Link: disabled', 0), ('you have disabled LM Link.', 0)):
            self.assertFalse(OP.link_disabled(text, '', rc), (text, rc))
            self.assertFalse(OP.link_disabled('', text, rc), ('stderr', text, rc))     # the same cases on standard error

    def test_lm_link_status_on_standard_error_addendum_2_item_12(self):
        self.assertTrue(OP.link_disabled('', LINK_OFF, 0))                 # measured: 0 bytes out, the line on stderr, exit 0
        self.assertTrue(OP.link_disabled(' \n', '\n' + LINK_OFF, 0))       # standard output blank: standard error decides
        self.assertFalse(OP.link_disabled('', LINK_OFF, 1))
        self.assertFalse(OP.link_disabled('You have enabled LM Link.', LINK_OFF, 0))   # standard output not empty: it decides
        self.assertFalse(OP.link_disabled('', '', 0)); self.assertFalse(OP.link_disabled(None, None, 0))

    def test_breach_statuses_addendum_1(self):
        def rows(*statuses):
            return [{'status': s, 'latency_s': 1.0, 'decision_ts': i} for i, s in enumerate(statuses)]
        for s in ('model_unavailable', 'tag_mismatch'):          # at once, even one among many oks
            self.assertIn(s, OP.breach_of(rows('ok', 'ok', s, 'ok', 'ok'), 10, True))
        for s in ('late', 'error'):
            self.assertIsNone(OP.breach_of(rows('ok', s, 'ok', 'ok'), 10, True), s)            # one alone: ordinary
            self.assertIsNone(OP.breach_of(rows(s, 'ok', 'ok', s, 'ok'), 10, True), s)         # never two in any three
            self.assertIn('two of three', OP.breach_of(rows('ok', s, 'ok', s), 10, True))      # two of the last three
            self.assertIn('two of three', OP.breach_of(rows(s, s), 10, True))
        self.assertIn('two of three', OP.breach_of(rows('late', 'error', 'ok', 'ok'), 10, True))   # met earlier in the watch
        self.assertIsNone(OP.breach_of(rows('stale_data', 'stale_data', 'stale_data'), 10, True))

    def test_breach_rules_both_ways(self):
        ok = [{'status': 'ok', 'latency_s': 1.0, 'decision_ts': i} for i in range(5)]
        self.assertIsNone(OP.breach_of(ok, 10, True))
        self.assertIn('not listed', OP.breach_of(ok, 10, False))
        self.assertIn('heartbeat', OP.breach_of(ok, 151, True)); self.assertIsNone(OP.breach_of(ok, 150, True))
        self.assertIsNone(OP.breach_of(ok, 120, True))                     # an ordinary slow minute
        self.assertIn('heartbeat', OP.breach_of(ok, None, True))
        one_slow = ok[:2] + [{'status': 'ok', 'latency_s': 9.0, 'decision_ts': 2}] + ok[3:]
        self.assertIsNone(OP.breach_of(one_slow, 10, True))
        two_slow = ok[:2] + [{'status': 'ok', 'latency_s': 9.0, 'decision_ts': 2}, {'status': 'ok', 'latency_s': 8.5, 'decision_ts': 3}]
        self.assertIn('in a row', OP.breach_of(two_slow, 10, True))
        self.assertIsNone(OP.breach_of(ok[:2] + [{'status': 'ok', 'latency_s': 8.0, 'decision_ts': 2}] * 2, 10, True))
        for status in OP.MODEL_FAILURE_STATUSES:
            self.assertIn(status, OP.breach_of(ok + [{'status': status, 'latency_s': None, 'decision_ts': 9}] * 2, 10, True))
        self.assertIsNone(OP.breach_of(ok + [{'status': 'stale_data', 'latency_s': None, 'decision_ts': 9}], 10, True))

    def test_streaks(self):
        def row(profile, counts, launched=True):
            return {'command': 'run', 'profile': profile, 'launched': launched, 'counts': counts}
        rows = [row('swarm8', True), row('swarm8', False), row('swarm8', True), row('swarm48', True), row('swarm8', True),
                row('swarm8', False, launched=False)]
        s = OP.streaks(rows)
        self.assertEqual(s['9']['streak'], 2); self.assertEqual(s['9']['target'], 10)
        self.assertEqual(s['48']['streak'], 1); self.assertEqual(s['48']['target'], 3)
        self.assertEqual(OP.streaks(rows + [row('swarm8', False)])['9']['streak'], 0)

    def test_verdicts(self):
        base = dict(collector={'breach': None, 'over_8s': 0, 'model_failures': 0, 'statuses': {'ok': 40}},
                    pipeline={'failed_calls': 0, 'refused_calls': 0, 'memory_stop': False, 'swaps_clean': True,
                              'cleanup_proven': True, 'failure': None, 'report_usable': True,
                              'receipt_found': True, 'receipt_readable': True, 'complete': True, 'launcher_exit': 0},
                    memory={'lowest_sustained_free_percent_10s': 40})
        v = OP.verdicts(**copy.deepcopy(base))
        self.assertEqual(v, {'collector_unharmed': True, 'pipeline_clean': True, 'room_to_spare': True})
        self.assertFalse(OP.verdicts(**dict(copy.deepcopy(base), memory={'lowest_sustained_free_percent_10s': 39}))['room_to_spare'])
        self.assertIsNone(OP.verdicts(**dict(copy.deepcopy(base), memory={'lowest_sustained_free_percent_10s': None}))['room_to_spare'])
        b = copy.deepcopy(base); b['collector']['over_8s'] = 1
        self.assertFalse(OP.verdicts(**b)['collector_unharmed'])
        b = copy.deepcopy(base); b['pipeline']['failed_calls'] = 1
        self.assertFalse(OP.verdicts(**b)['pipeline_clean'])
        b = copy.deepcopy(base); b['pipeline']['report_usable'] = False   # a near-neutral report is not a pipeline fault
        self.assertTrue(OP.verdicts(**b)['pipeline_clean'])
        b = copy.deepcopy(base); b['pipeline']['memory_stop'] = True
        self.assertFalse(OP.verdicts(**b)['pipeline_clean'])

    def test_collector_unharmed_by_status_counts_correction_b(self):
        def unharmed(statuses, breach=None):
            c = {'breach': breach, 'over_8s': 0, 'model_failures': sum(v for k, v in statuses.items() if k != 'ok'),
                 'statuses': dict(statuses, ok=40)}
            return OP.verdicts(collector=c, pipeline={}, memory={})['collector_unharmed']
        self.assertTrue(unharmed({}))
        self.assertTrue(unharmed({'error': 1}))                       # one error alone does not fail it
        self.assertFalse(unharmed({'error': 2}))
        self.assertFalse(unharmed({'late': 1}))
        self.assertFalse(unharmed({'model_unavailable': 1})); self.assertFalse(unharmed({'tag_mismatch': 1}))
        self.assertTrue(unharmed({'stale_data': 3}))
        self.assertFalse(unharmed({}, breach='no heartbeat for 160 s'))


class Check(Base):
    def test_check_reports_every_field_and_names_what_a_run_switches_off(self):
        out = self.dir.name + '/check.json'
        rc = self.main(['check', '--profile', 'swarm8', '--model', 'qwen3-8b', '--json-out', out])
        self.assertEqual(rc, 0)
        s = json.loads(Path(out).read_text())
        self.assertTrue(s['lm_link']['disabled'])
        self.assertTrue(s['models']['decision_instance']['loaded_and_right'])
        self.assertTrue(s['models']['embedding_loaded'])
        self.assertEqual([o['instance_id'] for o in s['models']['others']], ['qwen3.8-27b-chat'])
        self.assertTrue(s['collector']['job_listed']); self.assertEqual(len(s['collector']['last_decisions']), 10)
        self.assertLessEqual(s['collector']['heartbeat_age_s'], 60); self.assertIsNone(s['collector']['breach'])
        self.assertEqual(s['memory'], {'free_percent': 80, 'pressure_level': 1, 'swap_used_bytes': 100 * 2**20})
        self.assertEqual(s['pick_reader']['listed'], True); self.assertEqual(s['pick_reader']['plist'], PICKS_PLIST)
        self.assertEqual(s['disk']['free_bytes'], 400 * 10**9)
        self.assertEqual(s['durations'], {'expected_s': 2346, 'wall_s': 3600})
        a = s['announcement']
        for part in ('2026-11-16', '39 min', '60 min', 'com.valedia.picks.hourly', 'qwen3.8-27b-chat', 'NOT reloaded'):
            self.assertIn(part, a)
        self.assertEqual(s['refusals'], [])
        self.assertNotIn('SECRETKEY', self.w.text() + Path(out).read_text())   # no command line is ever kept
        self.assertEqual(self.w.posts(), []); self.assertEqual(self.w.bootouts(), [])

    def test_check_dry_run_reads_nothing(self):
        self.assertEqual(self.main(['check', '--dry-run']), 0)
        self.assertEqual(self.w.calls, []); self.assertEqual(self.w.http_calls, [])


class Refusals(Base):
    """Each refusal both ways: the condition refuses (exit 2, nothing switched off, nothing staged or launched), and the healthy
    world launches."""

    def assertRefused(self, why, *, switched=False):
        rc = self.main(RUN)
        self.assertEqual(rc, 2, self.w.text())
        self.assertIn(why, self.w.text())
        self.assertEqual(self.w.spawned, [])
        if not switched:
            self.assertEqual(self.w.bootouts(), []); self.assertEqual(self.w.posts(), []); self.assertEqual(self.w.stages(), [])
        self.assertTrue(self.w.picks_loaded)
        row = self.w.rows()[-1]
        self.assertFalse(row['launched']); self.assertIn(why, row['refused'])

    def test_healthy_world_launches(self):
        self.assertEqual(self.main(RUN), 0, self.w.text())
        self.assertEqual(len(self.w.spawned), 1)

    def test_announced_is_required(self):
        for argv in (RUN[:-2], RUN[:-1] + ['  ']):
            self.assertEqual(self.main(argv), 2)
        self.assertIn('--announced', self.w.text()); self.assertEqual(self.w.calls, []); self.assertEqual(self.w.rows(), [])

    def test_lm_link_enabled(self):
        self.w.link_text = 'You have enabled LM Link.'; self.assertRefused('LM Link')

    def test_lm_link_status_failing(self):
        self.w.link_rc = 1; self.assertRefused('LM Link')

    def test_collector_too_quiet_to_judge(self):
        self.w.per_minute = 0; self.w.sleep(16 * 60)
        self.w.per_minute = 1; self.w.sleep(4 * 60)            # 4 decisions in the last 15 minutes
        self.assertRefused('too quiet to judge')

    def test_five_decisions_in_15_minutes_are_enough(self):
        self.w.per_minute = 0; self.w.sleep(16 * 60)
        self.w.per_minute = 1; self.w.sleep(5 * 60)
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_an_old_failure_outside_15_minutes_does_not_refuse(self):
        self.w.status = lambda t: 'model_unavailable'; self.w.sleep(120)
        self.w.status = lambda t: 'ok'; self.w.sleep(16 * 60)
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_a_single_error_in_the_window_does_not_refuse(self):
        self.w.status = lambda t: 'error' if t == START + 60 else 'ok'; self.w.per_minute = 1; self.w.sleep(5 * 60)
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_two_errors_of_three_refuse(self):
        self.w.status = lambda t: 'error' if t == START + 60 else 'ok'; self.w.sleep(5 * 60)   # two a minute: both error
        self.assertRefused('two of three')

    def test_pick_reader_running_refuses_and_says_when_it_started(self):
        self.w.picks_running = True; self.w.procs[6161] = {'start': 'Mon Nov 16 12:00:01 2026', 'pgid': 6161}
        self.assertRefused('Mon Nov 16 12:00:01 2026')
        self.assertIn('running', self.w.rows()[-1]['refused'])

    # ---- correction C: from 2 minutes before each full hour until the reader is seen idle after it (local clock)
    def at_local(self, mm, ss):
        """Sleep the world forward (never zero) to the next local time hh:mm:ss with the given minutes and seconds."""
        lt = time.localtime(self.w.t)
        self.w.sleep((mm * 60 + ss - (lt.tm_min * 60 + lt.tm_sec)) % 3600 or 3600)
        lt = time.localtime(self.w.t); self.assertEqual((lt.tm_min, lt.tm_sec), (mm, ss))

    def reader(self, running):
        self.w.picks_running = running
        if running:
            self.w.procs[6161] = {'start': 'Mon Nov 16 13:00:01 2026', 'pgid': 6161}

    def test_c_57_59_reader_running_refused(self):
        self.at_local(57, 59); self.reader(True); self.assertRefused('is running')

    def test_c_57_59_reader_idle_launches(self):
        self.at_local(57, 59); self.reader(False)
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_c_58_00_reader_running_refused(self):
        self.at_local(58, 0); self.reader(True); self.assertRefused('due')

    def test_c_58_00_reader_idle_is_still_refused_and_not_booted_out(self):
        self.at_local(58, 0); self.reader(False); self.assertRefused('due')
        self.assertIn('2 minutes', self.w.rows()[-1]['refused'])

    def test_c_00_30_reader_running_refused(self):
        self.at_local(0, 30); self.reader(True); self.assertRefused('is running')

    def test_c_00_30_reader_idle_again_launches(self):
        # addendum 2, item 10: the window closes once the reader was seen RUNNING after its minute and is idle again
        self.at_local(0, 20); self.reader(True); self.assertEqual(self.main(RUN), 2, self.w.text())
        self.w.sleep(10); self.reader(False)
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_c_00_30_idle_but_never_seen_running_is_refused(self):
        self.at_local(0, 30); self.reader(False); self.assertRefused('not yet seen running')

    def test_c_on_the_hour_second_is_not_yet_after_it(self):
        self.at_local(0, 2); self.reader(False); self.assertRefused('not yet seen running')

    def test_c_running_seen_in_the_previous_hour_does_not_close_this_window(self):
        self.at_local(30, 0); self.reader(True); self.assertEqual(self.main(RUN), 2)
        self.at_local(0, 30); self.reader(False); self.assertRefused('not yet seen running')

    def test_c_a_missed_start_closes_the_window_after_3_minutes_idle(self):
        self.at_local(2, 59); self.reader(False); self.assertRefused('not yet seen running')
        self.dir.cleanup(); self.setUp()
        self.at_local(3, 0); self.reader(False); self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_reader_started_between_the_check_and_the_bootout_is_never_booted_out(self):
        orig = OP.refusals
        def start_it(s):
            out = orig(s); self.reader(True); return out
        OP.refusals = start_it
        try:
            rc = self.main(RUN)
        finally:
            OP.refusals = orig
        self.assertEqual(rc, 2); self.assertIn('is running', self.w.rows()[-1]['refused'])
        self.assertEqual(self.w.bootouts(), []); self.assertEqual(self.w.posts(), [])

    def test_c_the_scheduled_minute_is_read_from_launchd(self):
        self.w.picks_minute = 7
        self.at_local(5, 30); self.reader(False); self.assertRefused(':07')

    def test_c_the_window_is_judged_again_at_the_switch_off(self):
        # the check passes at 57:59 idle; the clock reads 58:00 by the time the switch-offs begin: no bootout
        self.at_local(57, 59); self.reader(False)
        checked = []
        orig = OP.refusals
        def mark(s):
            checked.append(True); return orig(s)
        OP.refusals = mark
        try:
            rc = self.main(RUN, now=lambda: self.w.t + (1 if checked else 0))
        finally:
            OP.refusals = orig
        self.assertEqual(rc, 2); self.assertIn('due', self.w.rows()[-1]['refused'])
        self.assertEqual(self.w.bootouts(), [])

    def test_pipeline_too_old_to_name_its_observer_is_not_launched(self):
        self.w.pipeline_writes_identity = False
        self.assertRefused('too old', switched=True)
        self.assertEqual(self.w.bootouts(), []); self.assertEqual(self.w.posts(), [])   # addendum 2, item 5: before any switch-off
        self.assertEqual(len(self.w.stages()), 2)
        work = Path(self.w.rows()[-1]['staged_folder'])
        self.assertFalse((work / 'root-one-native-attempt.json').exists())     # no gate written

    def test_decision_instance_missing(self):
        self.w.models['models'][0]['loaded_instances'] = []; self.assertRefused('decision instance')

    def test_decision_instance_wrong(self):
        self.w.models['models'][0]['loaded_instances'][0]['config']['context_length'] = 4096; self.assertRefused('context_length')

    def test_collector_slow(self):
        self.w.latency = lambda t: 9.0; self.w.collector_step(); self.w.sleep(120); self.assertRefused('in a row')

    def test_collector_model_failure(self):
        self.w.status = lambda t: 'model_unavailable'; self.w.sleep(60); self.assertRefused('model_unavailable')

    def test_collector_no_heartbeat(self):
        self.w.beating = False; self.w.sleep(180); self.assertRefused('heartbeat')

    def test_collector_slow_heartbeat_under_150_s_does_not_refuse(self):
        self.w.beating = False; self.w.sleep(100)                                # the last heartbeat 100 s ago
        self.w.on_sleep.append(lambda world: setattr(world, 'beating', True))    # it writes again at its next minute
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_collector_job_not_listed(self):
        self.w.collector_listed = False; self.assertRefused('not listed')

    def test_pressure_not_one(self):
        self.w.pressure = 2; self.assertRefused('pressure level 2')

    def test_unexpired_gate_of_an_earlier_run(self):
        old = self.w.crew / 'valedia-native-old'; old.mkdir()
        (old / 'root-one-native-attempt.json').write_text(json.dumps({'expires_at_utc': '2026-11-16T12:30:00+00:00'}))
        self.assertRefused('gate')

    def test_expired_gate_does_not_refuse(self):
        old = self.w.crew / 'valedia-native-old'; old.mkdir()
        (old / 'root-one-native-attempt.json').write_text(json.dumps({'expires_at_utc': '2026-11-16T11:30:00+00:00'}))
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_live_observer_of_an_earlier_run(self):
        old = self.w.crew / 'valedia-native-old/builder'; old.mkdir(parents=True)
        self.w.procs[777] = {'start': 'Sun Nov 15 09:00:00 2026', 'pgid': 777}
        (old / 'observer-identity.json').write_text(json.dumps({'pid': 777, 'start': 'Sun Nov 15 09:00:00 2026', 'pgid': 777}))
        self.assertRefused('observer')

    def test_dead_observer_of_an_earlier_run_does_not_refuse(self):
        old = self.w.crew / 'valedia-native-old/builder'; old.mkdir(parents=True)
        self.w.procs[777] = {'start': 'Mon Nov 16 08:00:00 2026', 'pgid': 777}     # the pid now belongs to another process
        (old / 'observer-identity.json').write_text(json.dumps({'pid': 777, 'start': 'Sun Nov 15 09:00:00 2026', 'pgid': 777}))
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_native_instance_already_loaded(self):
        self.w.models['models'][2]['loaded_instances'].append({'id': 'mf-native-0123', 'config': {}}); self.assertRefused('mf-native-0123')

    def test_free_under_the_staged_threshold(self):
        self.w.free = 55
        self.assertRefused('55% free is under the staged start threshold 56%', switched=True)
        self.assertEqual(len(self.w.stages()), 2)          # the threshold is the staged folder's own
        self.assertTrue(self.w.picks_loaded)                 # and what was switched off is back

    def test_free_at_the_staged_threshold_runs(self):
        self.w.free = 56; self.assertEqual(self.main(RUN), 0, self.w.text())


class Restore(Base):
    def assertRestored(self):
        self.assertTrue(self.w.picks_loaded)
        self.assertEqual([c for c in self.w.calls if c[:2] == ['/bin/launchctl', 'bootstrap']],
                         [['/bin/launchctl', 'bootstrap', 'gui/501', PICKS_PLIST]])

    def test_failing_launcher_restores(self):
        self.w.spawn_error = OSError('launcher would not start')
        rc = self.main(RUN)
        self.assertEqual(rc, 1); self.assertRestored()
        row = self.w.rows()[-1]
        self.assertIn('launcher would not start', row['error']); self.assertTrue(row['switch_offs']['pick_reader']['restored'])

    def test_failing_unload_restores_and_stops_before_the_launch(self):
        self.w.unload_status = 500
        rc = self.main(RUN)
        self.assertEqual(rc, 1); self.assertRestored(); self.assertEqual(self.w.spawned, [])
        self.assertEqual(len(self.w.stages()), 2)          # addendum 2, item 5: staging comes before the switch-offs
        self.assertFalse(any(c for c in self.w.calls if '--preflight' in c))
        self.assertIn('qwen3.8-27b-chat', self.w.rows()[-1]['error'])

    def test_ctrl_c_in_the_watch_loop_stops_once_and_restores(self):
        fired = []

        def interrupt(world):
            if world.launcher is not None and world.t >= world.launcher.started + 60 and not fired:
                fired.append(world.t); raise KeyboardInterrupt
        self.w.on_sleep.append(interrupt)
        rc = self.main(RUN)
        self.assertEqual(rc, 1); self.assertRestored()
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])
        row = self.w.rows()[-1]
        self.assertIn('interrupted', row['stopped']); self.assertTrue(row['pipeline']['receipt_found'])

    def test_exception_in_the_record_write_still_restores(self):
        (self.w.tmp / 'artifacts/runs.jsonl').mkdir(parents=True)      # a folder where the record file should be
        rc = self.main(RUN)
        self.assertEqual(rc, 3); self.assertRestored(); self.assertIn('RECORD NOT WRITTEN', self.w.text())

    def test_failed_restore_is_an_error_exit_in_capitals_and_in_the_record(self):
        self.w.bootstrap_rc = 5
        rc = self.main(RUN)
        self.assertEqual(rc, 3); self.assertFalse(self.w.picks_loaded)
        self.assertIn('PICK READER NOT RESTORED', self.w.text())
        row = self.w.rows()[-1]
        self.assertFalse(row['switch_offs']['pick_reader']['restored'])
        self.assertTrue(any('PICK READER NOT RESTORED' in a for a in row['alerts']))

    def test_pick_reader_not_loaded_before_is_left_alone(self):
        self.w.picks_loaded = False
        self.assertEqual(self.main(RUN), 0, self.w.text())
        self.assertEqual(self.w.bootouts(), []); self.assertFalse(self.w.picks_loaded)
        self.assertTrue(self.w.rows()[-1]['switch_offs']['pick_reader']['restored'])


class RunRecipe(Base):
    def test_clean_run_end_to_end(self):
        rc = self.main(RUN)
        self.assertEqual(rc, 0, self.w.text())
        stages = self.w.stages()
        self.assertEqual(len(stages), 2)
        self.assertIn('--model', stages[0]); self.assertIn('qwen3-8b', stages[0])
        work = Path(stages[0][stages[0].index('--work') + 1]); twin = Path(stages[1][stages[1].index('--work') + 1])
        self.assertTrue((twin / 'builder/startup-preflight.json').is_file())
        self.assertFalse((work / 'builder/startup-preflight.json').exists())
        gate = json.loads((work / 'root-one-native-attempt.json').read_text())
        self.assertEqual(gate['outcome'], 'VALEDIA-MIROFISH-FRESH-1'); self.assertEqual(gate['action'], 'one-native-attempt')
        self.assertEqual(gate['candidate_sha256'], hashlib.sha256((work / 'builder/candidate-manifest.json').read_bytes()).hexdigest())
        self.assertNotIn('--preflight', self.w.spawned[0]); self.assertIn('--gate', self.w.spawned[0])
        self.assertEqual(self.w.posts(), [('POST', OP.LMSTUDIO + '/api/v1/models/unload', {'instance_id': 'qwen3.8-27b-chat'})])
        self.assertEqual(self.w.signals, [])
        row = self.w.rows()[-1]
        self.assertEqual(row['announced'], RUN[-1]); self.assertTrue(row['launched']); self.assertIsNone(row['stopped'])
        self.assertEqual(row['staged_folder'], str(work)); self.assertEqual(row['plan'], {'model': 'qwen3-8b'})
        self.assertEqual(row['switch_offs']['pick_reader']['before']['listed'], True)
        self.assertEqual(row['switch_offs']['pick_reader']['after']['listed'], True)
        self.assertEqual([m['instance_id'] for m in row['switch_offs']['models_unloaded']], ['qwen3.8-27b-chat'])
        self.assertEqual(row['pipeline']['calls'], 2); self.assertEqual(row['pipeline']['failed_calls'], 0)
        self.assertTrue(row['pipeline']['complete']); self.assertTrue(row['pipeline']['cleanup_proven'])
        self.assertEqual(row['memory']['lowest_sustained_free_percent_10s'], 47)
        self.assertEqual(row['memory']['swap_start_bytes'], 100 * 2**20)
        self.assertGreater(row['collector']['decisions'], 0); self.assertIsNone(row['collector']['breach'])
        self.assertEqual(row['verdicts'], {'collector_unharmed': True, 'pipeline_clean': True, 'room_to_spare': True})
        self.assertTrue(row['counts'])
        self.assertTrue(row['post']['decision_instance_right']); self.assertEqual(row['post']['native_loaded'], [])
        summary = [line for line in self.w.out if isinstance(line, str) and line.startswith('  ')]
        self.assertEqual(len(summary), 10, '\n'.join(summary))
        self.assertIn('LM Studio loads', self.w.text())
        self.assertNotIn('SECRETKEY', json.dumps(row) + self.w.text())

    def test_never_a_request_to_the_decision_instance_and_never_a_load(self):
        self.main(RUN)
        for method, url, body in self.w.http_calls:
            self.assertNotIn('elephant-1', json.dumps(body))
            self.assertFalse(url.endswith('/load'))
        self.assertNotIn('ym-research-embedding', json.dumps(self.w.posts()))

    def test_breach_stops_the_run_exactly_once(self):
        self.w.cleanup_s = 120                                   # the observer takes a while to clean up
        def slow(world):
            if world.launcher is not None and world.t >= world.launcher.started + 30:
                world.latency = lambda t: 9.5
        self.w.on_sleep.append(slow)
        rc = self.main(RUN)
        self.assertEqual(rc, 1)
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])
        row = self.w.rows()[-1]
        self.assertEqual(row['stopped'], 'collector breach'); self.assertIn('in a row', row['collector']['breach'])
        self.assertGreaterEqual(row['collector']['highest_latency_s'], 9.5)
        self.assertFalse(row['verdicts']['collector_unharmed']); self.assertFalse(row['counts'])
        self.assertTrue(self.w.picks_loaded)

    def test_a_pid_whose_start_differs_is_never_sent_term_and_the_launcher_is_interrupted(self):
        self.w.identity_start = 'Sun Nov 15 01:02:03 2026'; self.w.cleanup_s = 120
        def slow(world):
            if world.launcher is not None and world.t >= world.launcher.started + 30:
                world.latency = lambda t: 9.5
        self.w.on_sleep.append(slow)
        self.main(RUN)
        self.assertEqual(self.w.signals, [(LAUNCHER_PID, signal.SIGINT)])     # addendum 2, item 3: a reused pid -> fallback
        row = self.w.rows()[-1]
        self.assertEqual(row['stop']['by'], 'launcher interrupt'); self.assertIn('start', row['stop']['why_launcher'])

    def slow_after(self, seconds):
        def slow(world):
            if world.launcher is not None and world.t >= world.launcher.started + seconds:
                world.latency = lambda t: 9.5
        self.w.on_sleep.append(slow)

    # ---- correction A: no identity file -> SIGINT to the launcher this tool started, never TERM, never its group
    def test_no_identity_file_after_60_s_is_alerted_and_a_breach_interrupts_the_launcher_once(self):
        self.w.write_identity = False; self.w.cleanup_s = 120; self.slow_after(30)
        rc = self.main(RUN)
        self.assertEqual(rc, 1)
        self.assertEqual(self.w.signals, [(LAUNCHER_PID, signal.SIGINT)])          # SIGINT, to the launcher pid, once
        row = self.w.rows()[-1]
        self.assertEqual(row['stop']['by'], 'launcher interrupt'); self.assertEqual(row['stop']['pid'], LAUNCHER_PID)
        self.assertEqual(row['stopped'], 'collector breach')
        self.assertTrue(any('OBSERVER IDENTITY FILE NOT WRITTEN WITHIN 60 S' in a for a in row['alerts']))
        self.assertTrue(any('INTERRUPT THE LAUNCHER' in a for a in row['alerts']))
        self.assertTrue(row['launcher']['own_group'])
        self.assertTrue(self.w.picks_loaded)

    def test_after_the_interrupt_the_cleanup_time_is_waited_and_no_native_instance_is_said_both_ways(self):
        for left in (False, True):
            with self.subTest(left=left):
                self.dir.cleanup(); self.setUp()
                self.w.write_identity = False; self.w.cleanup_s = 120; self.slow_after(30)
                self.w.native_left_after_interrupt = left
                self.main(RUN)
                row = self.w.rows()[-1]; after = row['stop']['after_check']
                self.assertGreaterEqual(after['seconds_after_interrupt'], OP.STOP_WAIT_S)
                self.assertEqual(after['native_loaded'], ['mf-native-left'] if left else [])
                said = 'MF-NATIVE INSTANCE STILL LOADED AFTER THE LAUNCHER INTERRUPT'
                self.assertEqual(any(said in a for a in row['alerts']), left)
                self.assertEqual(said in self.w.text(), left)
                self.assertEqual(self.w.posts(), [('POST', OP.LMSTUDIO + '/api/v1/models/unload', {'instance_id': 'qwen3.8-27b-chat'})])

    def test_a_launcher_whose_start_changed_is_never_signalled(self):
        self.w.write_identity = False; self.slow_after(30)
        def recycled(world):
            if world.launcher is not None and LAUNCHER_PID in world.procs and world.t >= world.launcher.started + 10:
                world.procs[LAUNCHER_PID]['start'] = 'Mon Nov 16 12:01:00 2026'
        self.w.on_sleep.append(recycled)
        self.main(RUN)
        self.assertEqual(self.w.signals, [])
        row = self.w.rows()[-1]
        self.assertIn('start or group differs', row['stop']['not_signalled'])
        self.assertTrue(any('NOT SIGNALLED' in a for a in row['alerts']))

    def test_no_identity_file_and_no_breach_only_alerts(self):
        self.w.write_identity = False
        rc = self.main(RUN)
        self.assertEqual(rc, 0)          # addendum 4, F: a counting run with a restore that succeeded is 0, alerts or not
        self.assertEqual(self.w.signals, [])
        self.assertTrue(any('OBSERVER IDENTITY FILE NOT WRITTEN' in a for a in self.w.rows()[-1]['alerts']))

    def test_identity_file_present_never_interrupts_the_launcher(self):
        self.w.cleanup_s = 120; self.slow_after(30)
        self.main(RUN)
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])
        row = self.w.rows()[-1]
        self.assertEqual(row['stop']['by'], 'observer'); self.assertNotIn('after_check', row['stop'])
        self.assertFalse(any('IDENTITY FILE NOT WRITTEN' in a for a in row['alerts']))

    def test_a_launcher_not_in_its_own_group_is_interrupted_through_its_handle_alone(self):
        # addendum 3, item 1: SIGINT goes through the handle of the tool's own unreaped child, to that pid only, never a group
        self.w.write_identity = False; self.w.launcher_pgid = 1; self.w.cleanup_s = 120; self.slow_after(30)
        self.main(RUN)
        self.assertEqual(self.w.handle_signals, [(LAUNCHER_PID, signal.SIGINT)]); self.assertEqual(self.w.signals, self.w.handle_signals)
        self.assertFalse(self.w.rows()[-1]['launcher']['own_group'])

    def test_the_tool_has_no_group_signal_at_all(self):
        self.assertFalse(hasattr(OP, 'GROUP_STOP_SIGNAL'))
        self.assertNotIn('killpg', open(OP.__file__).read())

    # ---- correction B, end to end: the record carries every status count
    def run_with(self, statuses, run_s=180):
        self.w.per_minute = 1; self.w.run_s = run_s
        self.w.status = lambda t: statuses.get(int(t - START), 'ok')    # keyed by seconds after START, one decision a minute
        self.assertIn(self.main(RUN), (0, 1, 3), self.w.text())     # addendum 4, F: 1 when the run does not count
        return self.w.rows()[-1]

    def test_b_one_error_in_the_run_leaves_the_collector_unharmed(self):
        row = self.run_with({60: 'error'})
        self.assertEqual(row['collector']['statuses'].get('error'), 1); self.assertIn('ok', row['collector']['statuses'])
        self.assertIsNone(row['collector']['breach']); self.assertTrue(row['verdicts']['collector_unharmed'])

    def test_b_two_errors_apart_in_the_run_harm_it(self):
        row = self.run_with({60: 'error', 300: 'error'}, run_s=420)
        self.assertEqual(row['collector']['statuses'].get('error'), 2); self.assertIsNone(row['collector']['breach'])
        self.assertFalse(row['verdicts']['collector_unharmed']); self.assertFalse(row['counts'])

    def test_b_one_late_harms_it(self):
        row = self.run_with({60: 'late'})
        self.assertEqual(row['collector']['statuses'].get('late'), 1); self.assertIsNone(row['collector']['breach'])
        self.assertFalse(row['verdicts']['collector_unharmed'])

    def test_b_no_error_is_unharmed(self):
        row = self.run_with({})
        self.assertEqual(set(row['collector']['statuses']), {'ok'}); self.assertTrue(row['verdicts']['collector_unharmed'])

    def test_decision_instance_wrong_after_the_run_is_said_in_capitals_and_never_reloaded(self):
        def drop(world):
            if world.launcher is not None and world.t >= world.launcher.started + world.run_s:
                world.models['models'][0]['loaded_instances'] = []
        self.w.on_sleep.append(drop)
        self.main(RUN)
        self.assertIn('DECISION INSTANCE NOT LOADED AND RIGHT', self.w.text())
        row = self.w.rows()[-1]
        self.assertFalse(row['post']['decision_instance_right'])
        self.assertTrue(any('DECISION INSTANCE NOT LOADED AND RIGHT' in a for a in row['alerts']))
        self.assertEqual(len(self.w.posts()), 1)

    def test_native_instance_left_loaded_is_said(self):
        def leave(world):
            if world.launcher is not None and world.t >= world.launcher.started + world.run_s and len(world.models['models']) == 4:
                world.models['models'].append({'key': 'qwen3-8b', 'type': 'llm', 'loaded_instances': [{'id': 'mf-native-ff', 'config': {}}]})
        self.w.on_sleep.append(leave)
        self.main(RUN)
        self.assertEqual(self.w.rows()[-1]['post']['native_loaded'], ['mf-native-ff'])
        self.assertIn('MF-NATIVE INSTANCE STILL LOADED', self.w.text())

    def test_paper_refused_report_is_recorded_unusable_and_still_counts(self):
        self.w.receipt['paper_handoff'] = False
        self.main(RUN)
        row = self.w.rows()[-1]
        self.assertFalse(row['pipeline']['report_usable']); self.assertTrue(row['verdicts']['pipeline_clean'])

    def test_near_neutral_report_is_clean_with_exactly_its_reason_addendum_2_item_4(self):
        self.w.receipt.update(complete=False, paper_handoff=None, failure={'type': 'Refused', 'reason': NEAR_NEUTRAL})
        self.main(RUN)
        row = self.w.rows()[-1]
        self.assertEqual(row['launcher']['exit'], 1); self.assertEqual(row['pipeline']['launcher_exit'], 1)
        self.assertTrue(row['verdicts']['pipeline_clean']); self.assertTrue(row['counts'])

    def test_missing_receipt_memory_fields_are_null_with_a_note(self):
        del self.w.receipt['resource']
        self.main(RUN)
        row = self.w.rows()[-1]
        self.assertIsNone(row['memory']['lowest_sustained_free_percent_10s']); self.assertTrue(row['memory']['notes'])
        self.assertIsNone(row['verdicts']['room_to_spare']); self.assertFalse(row['counts'])

    def test_plan_swaps_are_judged(self):
        self.w.receipt['events'] += [{'event': 'model_swap', 'boundary': 'B1'}, {'event': 'post_swap_compatibility_probes_passed', 'boundary': 'B1'}]
        argv = ['run', '--profile', 'swarm8', '--setup-model', 'ternary-bonsai-2-27b', '--turns-model', 'qwen3-8b', '--announced', 'told']
        self.main(argv)
        row = self.w.rows()[-1]
        self.assertEqual(row['plan'], {'setup': 'ternary-bonsai-2-27b', 'turns': 'qwen3-8b'})
        self.assertFalse(row['pipeline']['swaps_clean']); self.assertFalse(row['verdicts']['pipeline_clean'])

    def test_dry_run_changes_nothing(self):
        rc = self.main(RUN + ['--dry-run'])
        self.assertEqual(rc, 0, self.w.text())
        self.assertEqual(self.w.bootouts(), []); self.assertEqual(self.w.posts(), []); self.assertEqual(self.w.stages(), [])
        self.assertEqual(self.w.spawned, []); self.assertEqual(self.w.rows(), [])
        self.assertIn('would', self.w.text())

    def test_record_prints_rows_and_streaks(self):
        self.main(RUN); self.w.out.clear()
        self.assertEqual(self.main(['record']), 0)
        text = self.w.text()
        self.assertIn('9 participants: 1 in a row (target 10)', text); self.assertIn('48 participants: 0 in a row (target 3)', text)


class LoadTest(Base):
    LT = ['load-test', '--model', 'qwen3-8b', '--hold-minutes', '2', '--announced', 'Owner told: 8B load test, 5 min']

    def result(self):
        files = sorted((self.w.tmp / 'artifacts/load-tests').glob('*.json'))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text())

    def test_load_test_measures_and_restores(self):
        def fall(world):
            if world.load_t is not None and world.t >= world.load_t + 60 and world.free == 60:
                world.free = 55
        self.w.on_sleep.append(fall)
        rc = self.main(self.LT)
        self.assertEqual(rc, 0, self.w.text())
        r = self.result()
        self.assertEqual(r['announced'], self.LT[-1])
        self.assertEqual(r['baseline']['median'], 80); self.assertEqual(r['baseline']['lowest'], 80)
        self.assertEqual(r['baseline']['samples'], 120)
        self.assertEqual(r['after_load']['lowest'], 55)
        self.assertEqual(r['setup_cost_points'], 20); self.assertEqual(r['fall_points'], 5)
        self.assertEqual(r['highest_pressure_level'], 1); self.assertEqual(r['swap_change_bytes'], 0)
        self.assertIsNone(r['collector']['breach']); self.assertGreater(r['collector']['decisions'], 0)
        self.assertTrue(r['unload']['proven']); self.assertEqual(r['post']['native_loaded'], [])
        self.assertEqual(self.w.loads, ['qwen3-8b']); self.assertEqual(len(self.w.unloads_via_pipeline), 1)
        self.assertTrue(self.w.picks_loaded)
        stage = self.w.stages()
        self.assertEqual(len(stage), 1); self.assertIn('qwen3-8b', stage[0])
        self.assertEqual(self.w.spawned, [])

    def test_breach_during_the_hold_unloads_early(self):
        def slow(world):
            if world.load_t is not None and world.t >= world.load_t + 20:
                world.latency = lambda t: 9.5
        self.w.on_sleep.append(slow)
        rc = self.main(self.LT)
        self.assertEqual(rc, 1)
        r = self.result()
        self.assertEqual(r['stopped'], 'collector breach'); self.assertTrue(r['unload']['proven'])
        self.assertLess(r['after_load']['samples'], 240); self.assertTrue(self.w.picks_loaded)

    def test_ctrl_c_during_the_hold_unloads_and_restores(self):
        def interrupt(world):
            if world.load_t is not None and world.t >= world.load_t + 10:
                world.on_sleep.clear(); raise KeyboardInterrupt
        self.w.on_sleep.append(interrupt)
        rc = self.main(self.LT)
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.w.unloads_via_pipeline), 1); self.assertTrue(self.w.picks_loaded)
        self.assertIn('interrupted', self.result()['stopped'])

    def test_failed_load_still_unloads_and_restores(self):
        self.w.load_error = 'load refused by the helper'
        rc = self.main(self.LT)
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.w.unloads_via_pipeline), 1); self.assertTrue(self.w.picks_loaded)
        self.assertIn('load refused', self.result()['load']['refusal'])

    def test_load_test_needs_announced_and_two_minutes(self):
        self.assertEqual(self.main(self.LT[:-2]), 2)
        self.assertEqual(self.main(['load-test', '--model', 'qwen3-8b', '--hold-minutes', '1', '--announced', 'x']), 2)
        self.assertEqual(self.w.calls, [])

    def test_load_test_refuses_like_a_run(self):
        self.w.pressure = 4
        self.assertEqual(self.main(self.LT), 2)
        self.assertEqual(self.w.loads, []); self.assertEqual(self.w.bootouts(), [])

    def test_load_test_dry_run_changes_nothing(self):
        self.assertEqual(self.main(self.LT + ['--dry-run']), 0, self.w.text())
        self.assertEqual(self.w.loads, []); self.assertEqual(self.w.bootouts(), []); self.assertEqual(self.w.stages(), [])
        self.assertFalse((self.w.tmp / 'artifacts/load-tests').exists())


class Addendum2(Base):
    """The repair after the failed blind check of 13b8029, item by item."""
    LT = LoadTest.LT

    def slow_after(self, seconds):
        def slow(world):
            if world.launcher is not None and world.t >= world.launcher.started + seconds:
                world.latency = lambda t: 9.5
        self.w.on_sleep.append(slow)

    def fresh(self):
        self.dir.cleanup(); self.setUp()

    def bootstraps(self):
        return [c for c in self.w.calls if c[:2] == ['/bin/launchctl', 'bootstrap']]

    # ---- 1: the restore runs to its end through KeyboardInterrupt and SystemExit, then re-raises
    def test_1_an_interrupt_inside_the_bootstrap_still_runs_every_step_writes_the_row_and_summary_then_reraises(self):
        for exc in (KeyboardInterrupt, SystemExit):
            with self.subTest(exc=exc.__name__):
                self.fresh(); self.w.bootstrap_raises = exc
                with self.assertRaises(exc):
                    self.main(RUN)
                row = self.w.rows()[-1]; pr = row['switch_offs']['pick_reader']
                self.assertIsNotNone(pr['after']); self.assertFalse(pr['after']['listed'])   # the next step still ran
                self.assertFalse(pr['restored'])
                self.assertTrue(any('PICK READER NOT RESTORED' in a for a in row['alerts']))
                self.assertIn('RUN SUMMARY', self.w.text()); self.assertTrue(row['launched'])

    def test_1_an_interrupt_after_the_bootstrap_took_effect_is_restored_and_reraised(self):
        self.w.bootstrap_raises_after = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.main(RUN)
        self.assertTrue(self.w.picks_loaded)
        row = self.w.rows()[-1]
        self.assertTrue(row['switch_offs']['pick_reader']['restored']); self.assertIn('RUN SUMMARY', self.w.text())

    def test_1_load_test_restore_runs_to_its_end_too(self):
        self.w.bootstrap_raises = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.main(self.LT)
        files = sorted((self.w.tmp / 'artifacts/load-tests').glob('*.json'))
        self.assertEqual(len(files), 1)
        r = json.loads(files[0].read_text())
        self.assertFalse(r['switch_offs']['pick_reader']['restored']); self.assertIsNotNone(r['switch_offs']['pick_reader']['after'])
        self.assertIn('LOAD TEST', self.w.text())

    # ---- 2: TERM only to a child of the launcher this tool started
    def breach_naming(self, pid, start, ppid, pgid=None):
        self.w.procs[pid] = {'start': start, 'pgid': pid if pgid is None else pgid, 'ppid': ppid}
        self.w.identity = {'pid': pid, 'start': start, 'pgid': pid if pgid is None else pgid, 'written_utc': 'x'}
        self.w.cleanup_s = 120; self.slow_after(30)
        self.main(RUN)
        return self.w.rows()[-1]

    def assertNoTerm(self, row, pid):
        self.assertNotIn((pid, signal.SIGTERM), self.w.signals)
        self.assertFalse(any(sig == signal.SIGTERM for _, sig in self.w.signals), self.w.signals)
        self.assertEqual(self.w.signals, [(LAUNCHER_PID, signal.SIGINT)])          # item 3: the fallback instead
        self.assertEqual(row['stop']['by'], 'launcher interrupt')

    def test_2_the_tools_own_pid_is_never_sent_term_even_as_a_child_of_the_launcher(self):
        row = self.breach_naming(TOOL_PID, TOOL_START, LAUNCHER_PID, pgid=TOOL_PID); self.assertNoTerm(row, TOOL_PID)

    def test_2_the_tools_parent_is_never_sent_term(self):
        row = self.breach_naming(TOOL_PARENT, 'Mon Nov 16 09:00:00 2026', LAUNCHER_PID); self.assertNoTerm(row, TOOL_PARENT)

    def test_2_pid_1_and_pid_0_are_never_sent_term(self):
        for pid in (1, 0):
            with self.subTest(pid=pid):
                self.fresh()
                row = self.breach_naming(pid, 'Mon Nov 16 00:00:01 2026', LAUNCHER_PID); self.assertNoTerm(row, pid)

    def test_2_an_unrelated_live_pid_is_never_sent_term(self):
        row = self.breach_naming(7777, 'Mon Nov 16 10:00:00 2026', 1); self.assertNoTerm(row, 7777)

    def test_2_a_child_of_a_different_parent_is_never_sent_term(self):
        self.w.procs[9999] = {'start': 'Mon Nov 16 10:00:00 2026', 'pgid': 9999, 'ppid': 1}
        row = self.breach_naming(8888, 'Mon Nov 16 10:00:01 2026', 9999); self.assertNoTerm(row, 8888)

    def test_2_a_child_of_a_recycled_launcher_pid_is_never_signalled(self):
        def recycle(world):
            if world.launcher is not None and LAUNCHER_PID in world.procs and world.t >= world.launcher.started + 10:
                world.procs[LAUNCHER_PID]['start'] = 'Mon Nov 16 12:30:00 2026'
        self.w.on_sleep.append(recycle); self.slow_after(30)
        self.main(RUN)
        self.assertEqual(self.w.signals, [])
        self.assertTrue(any('NOT SIGNALLED' in a for a in self.w.rows()[-1]['alerts']))

    def test_2_the_true_observer_child_of_the_launcher_is_sent_term(self):
        self.w.cleanup_s = 120; self.slow_after(30); self.main(RUN)
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])

    # ---- 3: no usable identity for any reason -> the fallback; the watch has a hard deadline
    def test_3_a_malformed_or_reused_identity_and_a_breach_interrupt_the_launcher(self):
        for raw in ('{not json', json.dumps({'pid': 'x'}), json.dumps([OBSERVER_PID]), json.dumps({'pid': OBSERVER_PID}),
                    json.dumps({'pid': OBSERVER_PID, 'start': 'Sun Nov 15 01:02:03 2026', 'pgid': OBSERVER_PID}),
                    json.dumps({'pid': True, 'start': OBSERVER_START, 'pgid': OBSERVER_PID})):
            with self.subTest(raw=raw):
                self.fresh(); self.w.identity = raw; self.w.cleanup_s = 120; self.w.run_s = 14400; self.slow_after(30)
                self.main(RUN)
                row = self.w.rows()[-1]
                self.assertEqual(self.w.signals, [(LAUNCHER_PID, signal.SIGINT)]); self.assertEqual(row['stopped'], 'collector breach')
                self.assertLess(self.w.t - START, 900)

    def test_3_past_the_profiles_wall_plus_cleanup_the_run_is_stopped_as_wall_exceeded(self):
        self.w.run_s = 20000; self.w.cleanup_s = 60
        self.main(RUN)
        row = self.w.rows()[-1]
        self.assertEqual(row['stopped'], 'wall exceeded'); self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])
        wall = OP.run_durations('swarm8', {'model': 'qwen3-8b'})['wall_s']
        sent = OP._dt.datetime.fromisoformat(row['stop']['utc']).timestamp()
        launched = OP._dt.datetime.fromisoformat(row['utc_launch']).timestamp()
        self.assertGreaterEqual(sent - launched, wall + OP.CLEANUP_S); self.assertLessEqual(sent - launched, wall + OP.CLEANUP_S + OP.WATCH_S)

    def test_3_a_run_that_cannot_be_stopped_still_ends_the_watch(self):
        self.w.run_s = 50000; self.w.write_identity = False
        def recycle(world):                      # its start reads differently: the pid-and-start match fails, so no signal
            if world.launcher is not None and LAUNCHER_PID in world.procs and world.t >= world.launcher.started + 10:
                world.procs[LAUNCHER_PID]['start'] = 'Mon Nov 16 12:30:00 2026'
        self.w.on_sleep.append(recycle)
        self.main(RUN)
        wall = OP.run_durations('swarm8', {'model': 'qwen3-8b'})['wall_s']
        self.assertLess(self.w.t - START, wall + OP.CLEANUP_S + OP.IDENTITY_WAIT_S + 2 * OP.STOP_WAIT_S)
        row = self.w.rows()[-1]
        self.assertEqual(row['stopped'], 'wall exceeded'); self.assertEqual(self.w.signals, [])
        self.assertTrue(any('NOT SIGNALLED' in a for a in row['alerts'])); self.assertTrue(self.w.picks_loaded)

    # ---- 4: pipeline_clean reads the receipt and the launcher's exit
    def clean(self, **receipt):
        self.w.receipt.update(receipt); self.main(RUN)
        return self.w.rows()[-1]

    def test_4_complete_false_without_a_failure_is_not_clean(self):
        row = self.clean(complete=False); self.assertFalse(row['verdicts']['pipeline_clean']); self.assertFalse(row['counts'])

    def test_4_a_failure_in_the_receipt_is_not_clean_even_when_complete(self):
        row = self.clean(failure={'type': 'NativeRefused', 'reason': 'something else'})
        self.assertFalse(row['verdicts']['pipeline_clean'])

    def test_4_only_the_exact_near_neutral_reason_is_clean(self):
        for failure in ({'type': 'Refused', 'reason': NEAR_NEUTRAL + '.'}, {'type': 'Refused', 'reason': NEAR_NEUTRAL.upper()},
                        {'type': 'Refused'}, {'type': 'Refused', 'reason': [NEAR_NEUTRAL]}, {'type': 'Refused', 'reason': None},
                        NEAR_NEUTRAL, {'type': 'NativeRefused', 'reason': NEAR_NEUTRAL}):
            with self.subTest(failure=failure):
                self.fresh()
                row = self.clean(complete=False, failure=failure); self.assertFalse(row['verdicts']['pipeline_clean'])

    def test_4_a_zero_exit_with_complete_false_is_not_clean(self):
        self.w.exit_override = 0
        row = self.clean(complete=False, failure={'type': 'Refused', 'reason': NEAR_NEUTRAL})
        self.assertEqual(row['launcher']['exit'], 0); self.assertFalse(row['verdicts']['pipeline_clean'])

    def test_4_a_nonzero_exit_with_complete_true_is_not_clean(self):
        self.w.exit_override = 1
        row = self.clean(); self.assertEqual(row['launcher']['exit'], 1); self.assertFalse(row['verdicts']['pipeline_clean'])

    def test_4_a_missing_or_unreadable_receipt_is_not_clean(self):
        self.w.write_receipt = False
        row = self.clean(); self.assertFalse(row['pipeline']['receipt_found']); self.assertFalse(row['verdicts']['pipeline_clean'])
        self.fresh(); self.w.receipt_raw = '{"complete": tr'
        row = self.clean(); self.assertFalse(row['pipeline']['receipt_readable']); self.assertFalse(row['verdicts']['pipeline_clean'])

    def test_4_the_clean_run_records_its_exit(self):
        row = self.clean(); self.assertEqual(row['launcher']['exit'], 0); self.assertTrue(row['verdicts']['pipeline_clean'])

    # ---- 5: refusals that need no switch-off, and staging, come before any switch-off; the free reading after settling
    def changing(self):
        return self.w.bootouts() + self.w.posts() + self.w.spawned + self.w.signals

    def test_5_a_check_refusal_and_a_staging_refusal_have_zero_changing_actions(self):
        for setup in (lambda w: setattr(w, 'link_text', 'You have enabled LM Link.'), lambda w: setattr(w, 'stage_rc', 3),
                      lambda w: setattr(w, 'pipeline_writes_identity', False)):
            self.fresh(); setup(self.w)
            self.assertEqual(self.main(RUN), 2 if self.w.stage_rc == 0 else 1, self.w.text())
            self.assertEqual(self.changing(), []); self.assertTrue(self.w.picks_loaded)
            self.assertEqual(list(self.w.crew.glob('*/' + OP.GATE_NAME)), [])

    def test_5_the_free_reading_is_taken_after_the_unloads_settled(self):
        self.main(RUN)
        post = [t for t, c in self.w.timed if c[0] == 'POST'][-1]
        after = [t for t, c in self.w.timed if c == ['/usr/bin/memory_pressure'] and t > post]
        self.assertGreaterEqual(after[0] - post, OP.SETTLE_S)

    def test_5_under_the_staged_threshold_names_the_unloaded_models_and_how_they_come_back(self):
        self.w.free = 55
        self.assertEqual(self.main(RUN), 2)
        refused = self.w.rows()[-1]['refused']
        self.assertIn('qwen3.8-27b-chat', refused); self.assertIn('LM Studio loads', refused)
        self.assertTrue(self.w.picks_loaded); self.assertEqual(self.w.spawned, [])

    # ---- 6: a torn record line
    def test_6_a_torn_line_is_ended_before_the_append_skipped_and_counted(self):
        path = self.w.tmp / 'artifacts/runs.jsonl'; path.parent.mkdir(parents=True)
        good = {'command': 'run', 'profile': 'swarm8', 'launched': True, 'counts': True, 'plan': None, 'utc_start': 'a'}
        path.write_text(json.dumps(good) + '\n' + '{"command": "run", "launched": tr')
        self.assertEqual(self.main(RUN), 0, self.w.text())
        rows, damaged = OP.read_rows(path)
        self.assertEqual(damaged, 1); self.assertEqual(len(rows), 2); self.assertTrue(rows[-1]['launched'])
        self.w.out.clear(); self.assertEqual(self.main(['record']), 0)
        self.assertIn('1 damaged line', self.w.text())

    def test_6_a_damaged_line_never_counts_in_either_direction(self):
        path = self.w.tmp / 'artifacts/runs.jsonl'; path.parent.mkdir(parents=True)
        good = json.dumps({'command': 'run', 'profile': 'swarm8', 'launched': True, 'counts': True, 'plan': None})
        bad_counted = '{"command": "run", "profile": "swarm8", "launched": true, "counts": tru'
        path.write_text('\n'.join([good, bad_counted, '[1, 2]', '"text"', good]) + '\n')
        rows, damaged = OP.read_rows(path)
        self.assertEqual(damaged, 3); self.assertEqual(OP.streaks(rows)['9']['streak'], 2)       # neither reset nor counted

    def test_6_every_append_is_synced(self):
        record = os.path.realpath(self.w.tmp / 'artifacts/runs.jsonl')
        mine = lambda: [p for p in self.w.fsyncs if os.path.realpath(p) in (record, os.path.dirname(record))]
        self.main(RUN); self.assertEqual(len(mine()), 2)          # a new file: the file and its folder
        self.main(RUN); self.assertEqual(len(mine()), 3)          # an existing file: the file itself

    # ---- 7: the intent to restore is set before the bootout
    def test_7_ctrl_c_right_after_the_bootout_still_bootstraps(self):
        self.w.bootout_raises_after = KeyboardInterrupt
        rc = self.main(RUN)
        self.assertEqual(rc, 1); self.assertTrue(self.w.picks_loaded)
        self.assertEqual(self.bootstraps(), [['/bin/launchctl', 'bootstrap', 'gui/501', PICKS_PLIST]])
        self.assertTrue(self.w.rows()[-1]['switch_offs']['pick_reader']['restored'])

    # ---- 8: an interrupt while the row is finished still appends a minimal row
    def test_8_ctrl_c_while_finishing_the_row_appends_a_minimal_row_then_reraises(self):
        orig = OP.post_state
        def interrupted(host, row):
            raise KeyboardInterrupt
        OP.post_state = interrupted
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.main(RUN)
        finally:
            OP.post_state = orig
        row = self.w.rows()[-1]
        self.assertIn('interrupted while recording', row['recording']); self.assertFalse(row['counts'])
        self.assertTrue(row['launched']); self.assertIn('RUN SUMMARY', self.w.text()); self.assertTrue(self.w.picks_loaded)

    # ---- 9: a second instance of the decision model refuses and is never unloaded
    def test_9_a_second_decision_model_instance_refuses_and_is_never_unloaded(self):
        for add in (lambda m: m['models'][0]['loaded_instances'].append({'id': 'elephant-1:2', 'config': {}}),
                    lambda m: m['models'].append(dict(decision_entry(), loaded_instances=[{'id': 'elephant-copy', 'config': {}}]))):
            with self.subTest():
                self.fresh(); add(self.w.models)
                self.assertEqual(self.main(RUN), 2, self.w.text())
                self.assertIn('decision', self.w.rows()[-1]['refused']); self.assertEqual(self.w.posts(), [])
                self.assertEqual(self.w.bootouts(), [])

    # ---- 11: load-test reads the collector right after the load call returns
    def test_11_a_breach_during_the_load_call_is_seen_when_it_returns(self):
        orig = FakeLoadPath.load
        def slow_load(lp):
            out = orig(lp); lp.world.latency = lambda t: 9.5; lp.world.sleep(150); return out
        FakeLoadPath.load = slow_load
        try:
            rc = self.main(self.LT)
        finally:
            FakeLoadPath.load = orig
        self.assertEqual(rc, 1)
        files = sorted((self.w.tmp / 'artifacts/load-tests').glob('*.json')); r = json.loads(files[0].read_text())
        self.assertEqual(r['stopped'], 'collector breach'); self.assertIsNotNone(r['collector_after_load']['breach'])
        self.assertIsNone(r['after_load']['median']); self.assertTrue(r['unload']['proven'])

    # ---- 12: LM Link's status on standard error, end to end
    def test_12_the_status_line_on_standard_error_launches(self):
        self.w.link_text = ''; self.w.link_err = LINK_OFF
        self.assertEqual(self.main(RUN), 0, self.w.text())

    def test_12_the_enabled_line_on_standard_error_refuses(self):
        self.w.link_text = ''; self.w.link_err = 'You have enabled LM Link.\n'
        self.assertEqual(self.main(RUN), 2); self.assertEqual(self.changing(), [])


class Addendum3(Base):
    """The repair after the second blind check of 424230b."""
    LT = LoadTest.LT

    @property
    def intent(self):
        return self.w.tmp / 'artifacts/mirofish/operator-intent.json'

    def slow_after(self, seconds):
        def slow(world):
            if world.launcher is not None and world.t >= world.launcher.started + seconds:
                world.latency = lambda t: 9.5
        self.w.on_sleep.append(slow)

    def at(self, seconds, action):
        """Do `action(world)` once, `seconds` after the launch."""
        done_ = []
        def hook(world):
            if world.launcher is not None and world.t >= world.launcher.started + seconds and not done_:
                done_.append(world.t); action(world)
        self.w.on_sleep.append(hook)
        return done_

    def fresh(self):
        self.dir.cleanup(); self.setUp()

    def changing(self):
        return self.w.bootouts() + self.w.posts() + self.w.spawned + self.w.signals

    def assertRestoredAfterTheEnd(self):
        self.assertIsNotNone(self.w.launcher_end_t)
        boots = [t for t, c in self.w.timed if c[:2] == ['/bin/launchctl', 'bootstrap']]
        self.assertTrue(boots); self.assertGreaterEqual(boots[0], self.w.launcher_end_t)
        self.assertTrue(self.w.picks_loaded); self.assertFalse(self.intent.exists())

    # ---- 1: the launcher is the tool's own child from the instant it is spawned
    def test_1_the_spawn_result_is_bound_to_the_stop_state_in_the_same_statement(self):
        self.assertRegex(Path(OP.__file__).read_text(), r'\n\s+stopper\.proc = host\.spawn\(')

    def test_1_an_interrupt_at_the_ps_read_after_the_spawn_still_stops_restores_after_the_end_and_records(self):
        self.w.ps_raises = {LAUNCHER_PID: KeyboardInterrupt}; self.w.cleanup_s = 30
        self.assertEqual(self.main(RUN), 1, self.w.text())
        row = self.w.rows()[-1]
        self.assertTrue(row['launched']); self.assertIn('interrupted', row['stopped'])
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])
        self.assertRestoredAfterTheEnd()

    def test_1_an_exception_at_the_ps_read_is_retried_and_the_run_watched(self):
        self.w.ps_raises = {LAUNCHER_PID: RuntimeError}
        self.assertEqual(self.main(RUN), 0, self.w.text())
        self.assertEqual(self.w.rows()[-1]['launcher']['start'], LAUNCHER_START)

    def test_1_an_unreadable_start_does_not_disarm_the_fallback(self):
        self.w.ps_unreadable = {LAUNCHER_PID}; self.w.write_identity = False; self.w.cleanup_s = 120; self.slow_after(30)
        self.main(RUN)
        row = self.w.rows()[-1]
        self.assertIsNone(row['launcher']['start'])
        self.assertEqual(self.w.handle_signals, [(LAUNCHER_PID, signal.SIGINT)]); self.assertEqual(row['stop']['by'], 'launcher interrupt')
        self.assertRestoredAfterTheEnd()

    def test_1_an_unreadable_launcher_start_still_lets_the_observer_child_be_sent_term(self):
        self.w.ps_unreadable = {LAUNCHER_PID}; self.w.cleanup_s = 60; self.slow_after(30)
        self.main(RUN)
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])

    def test_1_an_exception_in_the_watch_goes_through_the_one_path(self):
        self.w.cleanup_s = 60
        def boom(world):
            raise RuntimeError('fake failure in the watch')
        self.at(40, boom)
        self.assertEqual(self.main(RUN), 1)
        row = self.w.rows()[-1]
        self.assertIn('fake failure', row['error']); self.assertTrue(row['launched'])
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)]); self.assertRestoredAfterTheEnd()
        self.assertEqual(row['post']['native_loaded'], [])

    def test_1_an_exit_in_the_watch_goes_through_the_one_path_then_exits(self):
        self.w.cleanup_s = 60
        def leave(world):
            raise SystemExit(7)
        self.at(40, leave)
        with self.assertRaises(SystemExit):
            self.main(RUN)
        row = self.w.rows()[-1]
        self.assertTrue(row['launched']); self.assertIn('SystemExit', row['stopped'])
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)]); self.assertRestoredAfterTheEnd()

    def test_1_a_signal_inside_the_spawn_is_held_until_the_handle_is_bound(self):
        self.w.cleanup_s = 30
        def spawn(argv, cwd=None, env=None):
            self.w.fire(signal.SIGTERM); return self.w.spawn(argv, cwd, env)
        self.assertEqual(self.main(RUN, spawn=spawn), 1, self.w.text())
        row = self.w.rows()[-1]
        self.assertTrue(row['launched']); self.assertEqual(row['stopped'], 'operator signalled (SIGTERM)')
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)]); self.assertRestoredAfterTheEnd()

    # ---- 2: TERM and HUP handlers; the intent file; recover
    def test_2_handlers_for_term_and_hup_are_installed_before_the_first_switch_off_and_put_back(self):
        self.assertEqual(self.main(RUN), 0)
        first_bootout = next(i for i, c in enumerate(self.w.calls) if c[:2] == ['/bin/launchctl', 'bootout'])
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.assertTrue(any(s == sig and n <= first_bootout for _, n, s in self.w.installs), sig)
            self.assertEqual(self.w.handlers[sig], 'default')                        # put back at the end

    def test_2_a_hang_up_already_ignored_by_nohup_stays_ignored(self):
        self.w.handlers[signal.SIGHUP] = signal.SIG_IGN; self.w.cleanup_s = 30
        self.at(40, lambda world: world.fire(signal.SIGHUP))
        self.assertEqual(self.main(RUN), 0, self.w.text())
        self.assertIsNone(self.w.rows()[-1]['stopped']); self.assertEqual(self.w.handlers[signal.SIGHUP], signal.SIG_IGN)
        self.assertTrue(self.w.rows()[-1]['hup_ignored'])

    def test_2_a_term_or_hup_during_the_watch_is_a_controlled_stop(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig.name):
                self.fresh(); self.w.cleanup_s = 60
                self.at(40, lambda world, sig=sig: world.fire(sig))
                self.assertEqual(self.main(RUN), 1, self.w.text())
                row = self.w.rows()[-1]
                self.assertEqual(row['stopped'], 'operator signalled (%s)' % sig.name)
                self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)]); self.assertRestoredAfterTheEnd()

    def test_2_a_second_signal_during_the_stop_does_not_abort_it(self):
        self.w.cleanup_s = 90
        for s in (40, 70, 100, 130):
            self.at(s, lambda world: world.fire(signal.SIGTERM))
        self.assertEqual(self.main(RUN), 1)
        row = self.w.rows()[-1]
        self.assertEqual(row['stopped'], 'operator signalled (SIGTERM)'); self.assertRestoredAfterTheEnd()
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])

    def test_2_the_intent_file_is_written_before_the_first_switch_off_and_removed_after_the_row(self):
        seen = {}
        self.at(40, lambda world: seen.update(json.loads(self.intent.read_text())))
        self.assertEqual(self.main(RUN), 0)
        before = self.w.intent_at_bootout; row = self.w.rows()[-1]
        self.assertEqual(before['pick_reader']['before']['listed'], True); self.assertEqual(before['pick_reader']['before']['plist'], PICKS_PLIST)
        self.assertEqual(before['models_to_unload'], ['qwen3.8-27b-chat']); self.assertEqual(before['work'], row['staged_folder'])
        self.assertIsNone(before['launcher'])
        self.assertEqual(seen['launcher']['pid'], LAUNCHER_PID); self.assertEqual(seen['launcher']['start'], LAUNCHER_START)
        self.assertFalse(self.intent.exists())

    def test_2_check_run_and_load_test_refuse_while_an_intent_file_exists(self):
        self.intent.parent.mkdir(parents=True); self.intent.write_text('{"command": "run"}')
        self.main(['check']); self.assertIn('UNFINISHED', self.w.text())
        self.assertEqual(self.main(RUN), 2); self.assertIn('UNFINISHED', self.w.rows()[-1]['refused'])
        self.assertEqual(self.main(self.LT), 2)
        self.assertEqual(self.changing(), []); self.assertEqual(self.w.stages(), []); self.assertTrue(self.intent.exists())

    def killed_run(self):
        """A run whose tool was KILLed 40 s after the launch: its intent file, the reader booted out, the run alive."""
        seen = {}
        def snap(world):
            seen['intent'] = self.intent.read_text(); seen['procs'] = copy.deepcopy(world.procs)
        self.at(40, snap)
        self.main(RUN)
        self.w.on_sleep.clear()
        self.intent.write_text(seen['intent']); self.w.picks_loaded = False; self.w.procs.update(seen['procs'])
        self.w.out.clear(); self.w.calls.clear(); self.w.signals.clear(); self.w.http_calls.clear(); self.w.timed.clear()
        self.rows_before = len(self.w.rows())

    def test_2_recover_reports_a_live_run_and_changes_nothing(self):
        # addendum 4, part B: beside a proven survivor the reader is not put back and the intent is kept
        self.killed_run()
        self.assertEqual(self.main(['recover']), 3)
        self.assertFalse(self.w.picks_loaded); self.assertEqual(self.w.signals, [])
        self.assertIn('LAUNCHER STILL RUNNING', self.w.text()); self.assertIn('OBSERVER STILL RUNNING', self.w.text())
        self.assertEqual(len(self.w.rows()), self.rows_before); self.assertTrue(self.intent.exists())

    def test_2_recover_stop_stops_the_surviving_run_the_documented_way(self):
        self.killed_run()
        def reap(world):
            for pid in [p for p, v in world.procs.items() if v.get('termed_at') is not None and world.t >= v['termed_at'] + 5]:
                world.procs.pop(pid)
                if pid == OBSERVER_PID:
                    world.procs.pop(LAUNCHER_PID, None)
        self.w.on_sleep.append(reap)
        self.assertEqual(self.main(['recover', '--stop']), 0, self.w.text())
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])
        row = self.w.rows()[-1]
        self.assertEqual(row['stop']['by'], 'observer'); self.assertFalse(row['launcher_alive_after'])
        self.assertTrue(self.w.picks_loaded); self.assertFalse(self.intent.exists())

    def test_2_recover_reports_leftover_native_instances_and_unloads_nothing(self):
        self.killed_run()
        self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID)
        self.w.models['models'].append({'key': 'qwen3-8b', 'type': 'llm', 'loaded_instances': [{'id': 'mf-native-left', 'config': {}}]})
        self.assertEqual(self.main(['recover']), 3)
        self.assertIn('MF-NATIVE INSTANCE STILL LOADED', self.w.text()); self.assertEqual(self.w.posts(), [])
        self.assertEqual(self.w.rows()[-1]['native_loaded'], ['mf-native-left'])

    def test_2_recover_dry_run_changes_nothing(self):
        self.killed_run()
        self.assertEqual(self.main(['recover', '--dry-run']), 0)
        self.assertFalse(self.w.picks_loaded); self.assertEqual(self.w.signals, []); self.assertTrue(self.intent.exists())
        self.assertEqual(len(self.w.rows()), self.rows_before)
        self.assertEqual([c for c in self.w.calls if c[:2] in (['/bin/launchctl', 'bootstrap'], ['/bin/launchctl', 'bootout'])], [])

    def test_2_recover_without_an_intent_file_has_nothing_to_do(self):
        self.assertEqual(self.main(['recover']), 0); self.assertIn('no intent file', self.w.text()); self.assertEqual(self.w.rows(), [])

    # ---- 3: never neither a row nor the intent file
    def test_3_a_second_interrupt_while_the_minimal_row_is_written_leaves_the_intent_file(self):
        orig = OP.post_state
        def interrupted(host, row):
            self.w.fsync_raises = KeyboardInterrupt; raise KeyboardInterrupt
        OP.post_state = interrupted
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.main(RUN)
        finally:
            OP.post_state = orig
        # the minimal row's line reached the file but its sync was interrupted: not a written row, so the intent stays
        self.assertTrue(self.intent.exists()); self.assertTrue(self.w.picks_loaded)
        self.assertEqual(self.main(['recover']), 0, self.w.text())
        self.assertEqual(self.w.rows()[-1]['command'], 'recover'); self.assertFalse(self.intent.exists())

    # ---- 4: a wrongly typed receipt field is an unreadable receipt
    def test_4_a_wrongly_typed_receipt_field_is_unreadable_and_never_an_exception(self):
        for field, value in (('events', 'x'), ('events', [1]), ('resource_failures', 'y'), ('complete', 'true'),
                             ('cleanup_proven', 1), ('failure', 'boom'), ('resource', [1]), ('paper_handoff', 'yes'),
                             ('resource_failure_samples', {})):
            with self.subTest(field=field, value=value):
                self.fresh(); self.w.receipt[field] = value
                self.assertIn(self.main(RUN), (1, 3), self.w.text())      # addendum 4, F: a run that does not count is not 0
                row = self.w.rows()[-1]
                self.assertFalse(row['pipeline']['receipt_readable']); self.assertFalse(row['verdicts']['pipeline_clean'])
                self.assertIsNone(row['error'])

    # ---- 5: the watch deadline from the staged folder; durations from the limits table
    def test_5_the_watch_deadline_comes_from_the_staged_folder(self):
        self.w.staged_wall = 1800; self.w.run_s = 20000; self.w.cleanup_s = 60
        self.main(RUN)
        row = self.w.rows()[-1]
        self.assertEqual(row['stopped'], 'wall exceeded'); self.assertEqual(row['deadline']['staged_wall_s'], 1800)
        self.assertIn('mirofish_native_startup.py', row['deadline']['source'])
        sent = OP._dt.datetime.fromisoformat(row['stop']['utc']).timestamp()
        launched = OP._dt.datetime.fromisoformat(row['utc_launch']).timestamp()
        self.assertGreaterEqual(sent - launched, 1800 + OP.CLEANUP_S); self.assertLessEqual(sent - launched, 1800 + OP.CLEANUP_S + OP.WATCH_S)
        self.assertIn('staged deadline 1800 s', self.w.text())

    def test_5_a_staged_folder_without_a_readable_deadline_refuses_before_any_switch_off(self):
        self.w.staged_startup = False
        self.assertEqual(self.main(RUN), 2); self.assertIn('deadline', self.w.rows()[-1]['refused']); self.assertEqual(self.changing(), [])

    def test_5_startup_and_manifest_deadlines_that_differ_refuse(self):
        self.w.staged_manifest_wall = 1234
        self.assertEqual(self.main(RUN), 2); self.assertIn('1234', self.w.rows()[-1]['refused']); self.assertEqual(self.changing(), [])

    def test_5_check_prints_the_expected_duration_and_the_wall_for_the_profile_and_plan(self):
        self.main(['check', '--profile', 'swarm8', '--model', 'qwen3-8b'])
        d = OP.run_durations('swarm8', {'model': 'qwen3-8b'})
        self.assertIn('expected %d s, wall %d s' % (d['expected_s'], d['wall_s']), self.w.text())


class Addendum4(Base):
    """The repair after the blind check of dde513d."""
    LT = LoadTest.LT
    intent = Addendum3.intent
    at = Addendum3.at
    fresh = Addendum3.fresh
    killed_run = Addendum3.killed_run
    slow_after = Addendum3.slow_after

    def edit_intent(self, **paths):
        data = json.loads(self.intent.read_text())
        for path, value in paths.items():
            node = data; keys = path.split('__')
            for k in keys[:-1]:
                node = node[k]
            node[keys[-1]] = value
        self.intent.write_text(json.dumps(data))

    def identity_names(self, pid, start, pgid):
        work = Path(json.loads(self.intent.read_text())['work'])
        (work / 'builder/observer-identity.json').write_text(json.dumps({'pid': pid, 'start': start, 'pgid': pgid}))

    def only_hostile(self):
        """A killed run whose launcher and observer are gone; the collector runs (with a command line that carries a key)."""
        self.killed_run()
        self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID)
        self.w.collector_pid = COLLECTOR_PID
        self.w.procs[COLLECTOR_PID] = {'start': COLLECTOR_START, 'pgid': COLLECTOR_PID, 'ppid': 1}
        self.w.commands[COLLECTOR_PID] = '/x/bin/python -m valedia_trader.collector --key=%s-C' % SECRET

    def assertNoSecret(self):
        texts = [self.w.text(), (self.w.tmp / 'artifacts/runs.jsonl').read_text() if (self.w.tmp / 'artifacts/runs.jsonl').exists() else '',
                 self.intent.read_text() if self.intent.exists() else '']
        for t in texts:
            self.assertNotIn(SECRET, t)

    def assertNotOursAndRecovered(self, rc):
        """Addendum 5: the proof was made and the answer is no: reported by pid only, never signalled, and with no proven
        survivor and a scan that succeeded, the recovery goes on: the reader back, the row appended, the file removed."""
        self.assertEqual(rc, 0, self.w.text()); self.assertEqual(self.w.signals, []); self.assertFalse(self.intent.exists())
        self.assertTrue(self.w.picks_loaded); row = self.w.rows()[-1]
        self.assertEqual(row['command'], 'recover'); self.assertTrue(row['not_ours']); self.assertNoSecret()
        self.assertIn('NOT SIGNALLED', self.w.text())

    def assertNothingSignalledAndKept(self, rc):
        self.assertNotEqual(rc, 0); self.assertEqual(self.w.signals, []); self.assertTrue(self.intent.exists()); self.assertNoSecret()

    # ---- A: a signal only to a process proven to belong to this run
    def test_A_a_launcher_written_as_the_collector_is_never_signalled(self):
        self.only_hostile()
        self.edit_intent(launcher={'pid': COLLECTOR_PID, 'start': COLLECTOR_START, 'pgid': COLLECTOR_PID})
        self.assertNotOursAndRecovered(self.main(['recover', '--stop']))
        self.assertIn('NOT SIGNALLED', self.w.text()); self.assertIn('COLLECTOR', self.w.text())

    def test_A_launcher_pid_1_and_an_identity_naming_the_collector_send_nothing(self):
        self.only_hostile()
        self.edit_intent(launcher={'pid': 1, 'start': None, 'pgid': None})
        self.identity_names(COLLECTOR_PID, COLLECTOR_START, COLLECTOR_PID)
        self.assertNotOursAndRecovered(self.main(['recover', '--stop']))

    def test_A_the_collector_and_the_pick_reader_proposed_every_way_with_a_matching_command_are_never_signalled(self):
        for who in ('collector', 'picks'):
            for how in ('launcher', 'identity', 'scan'):
                with self.subTest(who=who, how=how):
                    self.fresh(); self.only_hostile()
                    work = json.loads(self.intent.read_text())['work']
                    pid = COLLECTOR_PID if who == 'collector' else PICKS_PID
                    if who == 'picks':
                        self.w.picks_running = True; self.w.picks_loaded = True
                        self.w.procs[PICKS_PID] = {'start': 'Mon Nov 16 12:00:01 2026', 'pgid': PICKS_PID, 'ppid': 1}
                    start = self.w.procs[pid]['start']
                    # the command names the staged folder and the script: only the job-pid rule stops it
                    self.w.commands[pid] = ('/x/python -B mirofish_native_startup.py --work %s --gate g --k=%s' % (work, SECRET)
                                            if how != 'identity' else
                                            '/x/python -B %s/owned-source/observer-entry/mirofish_native_run.py %s' % (work, SECRET))
                    if how == 'launcher':
                        self.edit_intent(launcher={'pid': pid, 'start': start, 'pgid': pid})
                    if how == 'identity':
                        self.identity_names(pid, start, pid)
                    self.assertNotOursAndRecovered(self.main(['recover', '--stop']))
                    self.assertIn('JOB', self.w.text())

    def test_A_pids_not_above_1_never_signalled_and_mistyped_pids_refused_whole(self):
        for pid in (1, 0, -5):
            with self.subTest(pid=pid):
                self.fresh(); self.only_hostile(); self.edit_intent(launcher={'pid': pid, 'start': None, 'pgid': None})
                self.assertNotOursAndRecovered(self.main(['recover', '--stop']))
        for pid in (True, '4242', 4242.0):
            with self.subTest(pid=pid):
                self.fresh(); self.killed_run(); self.edit_intent(launcher={'pid': pid, 'start': LAUNCHER_START, 'pgid': LAUNCHER_PID})
                self.assertEqual(self.main(['recover', '--stop']), 2)
                self.assertNothingSignalledAndKept(2); self.assertIn('launcher.pid', self.w.text()); self.assertFalse(self.w.picks_loaded)

    def test_A_the_tool_or_an_ancestor_is_never_signalled(self):
        for pid in (TOOL_PID, TOOL_PARENT):
            with self.subTest(pid=pid):
                self.fresh(); self.only_hostile()
                work = json.loads(self.intent.read_text())['work']
                self.w.commands[pid] = '/x/python -B mirofish_native_startup.py --work %s --gate g' % work
                self.edit_intent(launcher={'pid': pid, 'start': self.w.procs[pid]['start'], 'pgid': self.w.procs[pid]['pgid']})
                self.assertNotOursAndRecovered(self.main(['recover', '--stop'])); self.assertIn('ANCESTOR', self.w.text())

    def test_A_right_pid_and_start_with_the_wrong_command_is_never_signalled(self):
        self.killed_run()
        self.w.commands[LAUNCHER_PID] = '/usr/bin/vim notes.txt %s' % SECRET
        self.w.commands[OBSERVER_PID] = '/usr/bin/less %s/owned-source/observer-entry/mirofish_native_run.py' % (
            json.loads(self.intent.read_text())['work'])
        self.assertNotOursAndRecovered(self.main(['recover', '--stop'])); self.assertIn('COMMAND', self.w.text())
        self.assertTrue(self.w.picks_loaded)        # addendum 5: definitely not ours does not hold the reader off

    def test_A_another_runs_launcher_and_observer_are_never_signalled(self):
        self.killed_run()
        other = str(self.w.crew / 'valedia-native-20261116-swarm8-op999999')
        self.w.commands[LAUNCHER_PID] = '/x/python -B mirofish_native_startup.py --work %s --gate g' % other
        self.w.commands[OBSERVER_PID] = '/x/python -B %s/owned-source/observer-entry/mirofish_native_run.py' % other
        self.assertNotOursAndRecovered(self.main(['recover', '--stop'])); self.assertIn('COMMAND', self.w.text())

    def test_A_the_right_command_with_a_wrong_start_is_never_signalled(self):
        self.killed_run()
        self.w.procs[LAUNCHER_PID]['start'] = 'Mon Nov 16 13:33:33 2026'
        self.w.procs[OBSERVER_PID]['start'] = 'Mon Nov 16 13:33:34 2026'
        self.assertNotOursAndRecovered(self.main(['recover', '--stop'])); self.assertIn('START', self.w.text())

    def test_A_a_command_that_cannot_be_read_sends_nothing_and_says_nothing_of_it(self):
        self.killed_run(); self.w.command_raises = RuntimeError
        self.assertNothingSignalledAndKept(self.main(['recover', '--stop']))

    def test_A_a_proven_run_is_signalled_once_the_documented_way_and_no_command_line_reaches_anything(self):
        self.killed_run()
        def reap(world):
            if world.procs.get(OBSERVER_PID, {}).get('termed_at') is not None:
                world.procs.pop(OBSERVER_PID, None); world.procs.pop(LAUNCHER_PID, None)
        self.w.on_sleep.append(reap)
        self.assertEqual(self.main(['recover', '--stop']), 0, self.w.text())
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)]); self.assertNoSecret()

    # ---- B: a KILL between the spawn and the launcher's entry, with real children
    def forked_kill(self, kill_at, held=False):
        self.w.real = True; self.w.kill_at = kill_at; self.w.term_in_spawn = held
        child = os.fork()
        if child == 0:
            try:
                OP.main(RUN, host=self.w.host())
            finally:
                os._exit(99)
        _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, (kill_at, status))
        end = time.time() + 10
        while time.time() < end and len([p for p in self.w.real_set() if self.w.ours(p)]) < 2:
            time.sleep(0.05)
        self.assertEqual(len([p for p in self.w.real_set() if self.w.ours(p)]), 2, kill_at)
        self.w.kill_at = None; self.w.picks_loaded = False          # the killed tool had booted the reader out
        self.w.out.clear(); self.w.calls.clear(); self.w.signals.clear(); self.w.timed.clear()

    def test_B_a_kill_anywhere_between_the_spawn_and_the_entry_never_orphans_the_run(self):
        for point, held in (('spawn', False), ('fsync', False), ('ps', False), ('sleep', True)):
            with self.subTest(point=point, held=held):
                self.fresh(); self.forked_kill(point, held)
                self.assertTrue(self.intent.exists()); rows = len(self.w.rows())
                self.assertEqual(self.main(['recover']), 3, self.w.text())           # reports, changes nothing
                self.assertIn('LAUNCHER STILL RUNNING', self.w.text()); self.assertIn('OBSERVER STILL RUNNING', self.w.text())
                self.assertFalse(self.w.picks_loaded); self.assertTrue(self.intent.exists()); self.assertEqual(self.w.signals, [])
                self.assertEqual(len(self.w.rows()), rows)
                self.w.out.clear()
                self.assertEqual(self.main(['recover', '--stop']), 0, self.w.text())   # stops both, reader back, row, file gone
                self.assertEqual([p for p in self.w.real_set() if self.w.ours(p)], [])
                self.assertTrue(self.w.picks_loaded); self.assertFalse(self.intent.exists())
                self.assertEqual(self.w.rows()[-1]['command'], 'recover'); self.assertNoSecret()
                self.w.out.clear()
                self.assertEqual(self.main(['recover']), 0); self.assertIn('no intent file', self.w.text())

    def test_B_a_failed_scan_keeps_the_file_and_the_reader_off(self):
        self.killed_run(); self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID); self.w.scan_raises = RuntimeError
        self.assertEqual(self.main(['recover', '--stop']), 3)
        self.assertIn('SCAN', self.w.text()); self.assertTrue(self.intent.exists()); self.assertFalse(self.w.picks_loaded)

    def test_B_the_intent_is_synced_before_the_spawn_with_the_proof(self):
        seen = {}
        orig = self.w.spawn
        def spawn(argv, cwd=None, env=None):
            seen.update(json.loads(self.intent.read_text())); return orig(argv, cwd, env)
        self.assertEqual(self.main(RUN, spawn=spawn), 0, self.w.text())
        self.assertEqual(seen['scripts'], {'launcher': 'mirofish_native_startup.py', 'observer': 'mirofish_native_run.py'})
        self.assertIsNotNone(seen['launch']['spawning_utc']); self.assertEqual(seen['work'], self.w.rows()[-1]['staged_folder'])

    # ---- C: recover bootstraps the pick reader's own plist only
    def test_C_an_intent_naming_another_label_or_plist_is_refused_whole(self):
        for change in ({'pick_reader__label': OP.COLLECTOR_LABEL},
                       {'pick_reader__before__plist': '/fake/LaunchAgents/com.valedia.orderbook-collector.plist'}):
            with self.subTest(change=change):
                self.fresh(); self.killed_run(); self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID)
                self.edit_intent(**change)
                self.assertEqual(self.main(['recover', '--stop']), 2)
                self.assertFalse(self.w.picks_loaded); self.assertTrue(self.intent.exists())
                self.assertEqual([c for c in self.w.calls if c[:2] == ['/bin/launchctl', 'bootstrap']], [])
                self.assertIn('MISMATCH', self.w.text())

    # ---- D: the writer's liveness by an exclusive lock the kernel drops
    def lock_holder(self):
        path = self.w.tmp / 'artifacts/mirofish/operator.lock'; path.parent.mkdir(parents=True, exist_ok=True)
        code = ('import fcntl, os, sys, time\nfd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)\n'
                'fcntl.flock(fd, fcntl.LOCK_EX)\nprint("locked", flush=True)\ntime.sleep(60)\n')
        child = subprocess.Popen([sys.executable, '-c', code, str(path), str(self.w.tmp)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), 'locked')
        return child

    def test_D_run_load_test_and_recover_refuse_while_a_live_tool_holds_the_lock_and_go_once_it_is_killed(self):
        self.killed_run(); self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID)
        child = self.lock_holder()
        try:
            self.assertEqual(self.main(['recover', '--stop']), 2); self.assertIn('LOCK', self.w.text())
            self.assertFalse(self.w.picks_loaded); self.assertTrue(self.intent.exists())
            self.intent.rename(self.intent.with_name('kept.json'))
            self.assertEqual(self.main(RUN), 2); self.assertIn('LOCK', self.w.rows()[-1]['refused'])
            self.assertEqual(self.main(self.LT), 2); self.assertEqual(self.w.bootouts(), [])
            self.intent.with_name('kept.json').rename(self.intent)
        finally:
            child.kill(); child.wait()
        self.w.out.clear()
        self.assertEqual(self.main(['recover', '--stop']), 0, self.w.text())
        self.assertTrue(self.w.picks_loaded); self.assertFalse(self.intent.exists())

    # ---- E: nothing read from a file can raise
    def test_E_mistyped_boundaries_and_ledger_states_are_not_clean_and_the_checks_still_run(self):
        for events, calls in (([{'event': 'model_swap', 'boundary': ['B1']}], None), ([{'event': 'model_swap', 'boundary': {'b': 1}}], None),
                              ([{'event': 'model_swap', 'boundary': 'B1'}, {'event': 'model_swap', 'boundary': 2}], None),
                              (None, [{'id': 'c1', 'state': ['complete']}])):
            with self.subTest(events=events, calls=calls):
                self.fresh()
                if events is not None:
                    self.w.receipt['events'] = events
                if calls is not None:
                    self.w.ledger = {'calls': calls}
                self.main(['run', '--profile', 'swarm8', '--setup-model', 'ternary-bonsai-2-27b', '--turns-model', 'qwen3-8b',
                           '--announced', 'told'])
                row = self.w.rows()[-1]
                self.assertIsNone(row['error']); self.assertFalse(row['verdicts']['pipeline_clean'])
                for key in ('pipeline', 'collector', 'memory', 'post'):
                    self.assertIsNotNone(row[key], key)

    def test_E_an_intent_of_the_wrong_shape_is_refused_and_kept(self):
        self.killed_run(); self.edit_intent(pick_reader__before='listed')
        self.assertEqual(self.main(['recover']), 2); self.assertIn('pick_reader.before', self.w.text())
        self.assertTrue(self.intent.exists()); self.assertFalse(self.w.picks_loaded)

    POOL = (None, True, False, 0, -1, 7, 1.5, '', 'x', 'B1', [], [1], [None], ['B1', 2], {}, {'a': 1}, [[1]], [{'boundary': [1]}],
            [{'event': 'model_swap', 'boundary': {'x': 1}}], {'calls': 'x'})

    def pick(self, rng):
        return copy.deepcopy(rng.choice(self.POOL))

    def test_E_property_random_mistyped_receipts_never_raise(self):
        rng = random.Random(4); work = self.w.tmp / 'prop'; (work / 'builder').mkdir(parents=True)
        fields = ('complete', 'cleanup_proven', 'paper_handoff', 'failure', 'events', 'resource_failures',
                  'resource_failure_samples', 'resource', 'extra')
        n = 0
        for i in range(3000):
            receipt = copy.deepcopy(self.w.receipt)
            for _ in range(rng.randint(1, 3)):
                target = rng.choice(fields + ('events.0', 'resource.lowest_sustained_free_percent_10s', 'failure.reason'))
                if '.' in target:
                    head, key = target.split('.')
                    node = receipt.get(head)
                    if isinstance(node, list) and node:
                        node[0] = {'event': self.pick(rng), 'boundary': self.pick(rng)}
                    elif isinstance(node, dict):
                        node[key] = self.pick(rng)
                else:
                    receipt[target] = self.pick(rng)
            (work / 'builder/native-run-receipt.json').write_text(json.dumps(receipt))
            (work / 'builder/gateway-ledger.json').write_text(json.dumps(rng.choice(
                [self.w.ledger, {'calls': [{'state': self.pick(rng)}]}, self.pick(rng)])))
            plan = rng.choice([None, {'model': 'm'}, {'setup': 'a', 'turns': 'b'}])
            p, r = OP.pipeline_outcome(work, plan, rng.choice([0, 1, None]))
            m = OP.memory_outcome(r, 1, 2, {'lowest': None, 'max_pressure': None})
            OP.verdicts({'breach': None, 'over_8s': 0, 'statuses': {}}, p, m); n += 1
        self.assertEqual(n, 3000)

    def test_E_property_random_mistyped_intents_never_raise(self):
        self.killed_run(); good = json.loads(self.intent.read_text()); self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID)
        rng = random.Random(5); paths = []
        def walk(node, prefix):
            if isinstance(node, dict):
                for k, v in node.items():
                    paths.append(prefix + (k,)); walk(v, prefix + (k,))
        walk(good, ())
        n = 0
        for i in range(1500):
            bad = copy.deepcopy(good)
            for _ in range(rng.randint(1, 2)):
                path = rng.choice(paths); node = bad
                try:
                    for k in path[:-1]:
                        node = node[k]
                    node[path[-1]] = self.pick(rng)
                except (KeyError, TypeError):
                    pass
            self.intent.write_text(json.dumps(bad) if rng.random() < 0.97 else rng.choice(['', '[', '[1]', '"x"']))
            rc = self.main(['recover', rng.choice(['--dry-run', '--stop'])]); n += 1
            self.assertIn(rc, (0, 2, 3))
            if rc == 0 and not self.intent.exists():
                self.intent.write_text(json.dumps(good))
        self.assertEqual(n, 1500)

    # ---- F: what the row records
    def test_F_a_signal_during_the_restore_is_in_the_row_and_a_counting_run_exits_0(self):
        self.w.on_bootstrap = lambda world: world.fire(signal.SIGTERM)
        self.assertEqual(self.main(RUN), 0, self.w.text())
        row = self.w.rows()[-1]
        self.assertTrue(row['counts']); self.assertEqual(row['signals_received'][0]['signal'], 'SIGTERM')
        self.assertEqual(row['signals_received'][0]['during'], 'the restore')

    def test_F_a_signal_during_the_row_is_recorded_on_disk(self):
        fired = []
        def hook(world, path):
            if path.endswith('runs.jsonl') and not fired:
                fired.append(1); world.fire(signal.SIGHUP)
        self.w.on_fsync = hook
        self.assertEqual(self.main(RUN), 0, self.w.text())
        notes = [r for r in self.w.rows() if r.get('command') == 'signal-note']
        self.assertEqual(notes[-1]['signals_received'][0]['during'], 'the row')

    def test_F_exit_0_exactly_when_the_run_counts_and_the_restore_succeeded(self):
        self.w.receipt['cleanup_proven'] = False
        self.assertEqual(self.main(RUN), 1); self.assertFalse(self.w.rows()[-1]['counts'])
        self.fresh(); self.w.bootstrap_rc = 1
        self.assertEqual(self.main(RUN), 3)

    def test_F_a_recover_row_for_a_launched_run_resets_the_streak(self):
        self.killed_run()
        self.assertEqual(OP.streaks(self.w.rows())['9']['streak'], 1)
        self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID)
        self.main(['recover'])
        self.assertTrue(self.w.rows()[-1]['launched']); self.assertEqual(OP.streaks(self.w.rows())['9']['streak'], 0)

    def test_F_the_intent_is_kept_when_the_stop_could_not_be_sent(self):
        self.w.run_s = 50000; self.w.write_identity = False
        def recycle(world):
            if world.launcher is not None and LAUNCHER_PID in world.procs and world.t >= world.launcher.started + 10:
                world.procs[LAUNCHER_PID]['start'] = 'Mon Nov 16 12:30:00 2026'
        self.w.on_sleep.append(recycle)
        self.assertEqual(self.main(RUN), 3); self.assertTrue(self.intent.exists())

    def test_F_the_announcement_states_the_staged_wall(self):
        self.w.staged_wall = 5100
        self.main(RUN)
        line = next(x for x in self.w.out if str(x).startswith('check passed and staged'))
        self.assertIn('wall clock of 85 min', line); self.assertNotIn('wall clock of 60 min', line)

    # ---- H: the tests the surviving mutants showed missing
    def test_H_the_intent_is_kept_when_the_restore_failed(self):
        self.w.bootstrap_rc = 1
        self.assertEqual(self.main(RUN), 3); self.assertTrue(self.intent.exists())

    def test_H_recover_keeps_the_intent_while_the_reader_is_still_off(self):
        self.killed_run(); self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID); self.w.bootstrap_rc = 1
        self.assertEqual(self.main(['recover']), 3); self.assertTrue(self.intent.exists()); self.assertFalse(self.w.picks_loaded)


class Addendum5(Base):
    """The rulings on 1719c80."""
    LT = LoadTest.LT
    intent = Addendum3.intent
    killed_run = Addendum3.killed_run
    at = Addendum3.at
    fresh = Addendum3.fresh
    changing = Addendum3.changing
    assertNoSecret = Addendum4.assertNoSecret

    # ---- G (1): the decision and embedding instances are never unloaded, whatever the listing says
    def test_G1_the_decision_and_embedding_instances_are_never_unloaded_whatever_the_listing_says(self):
        hostile = ([{'key': 'other/key', 'type': 'llm', 'loaded_instances': [{'id': 'elephant-1', 'config': {}}]}],
                   [{'key': 'elephant-1', 'type': 'llm', 'loaded_instances': [{'id': 'copy', 'config': {}}]}],
                   [{'key': 'x/emb', 'type': 'embedding', 'loaded_instances': [{'id': 'ym-research-embedding', 'config': {}}]}],
                   [{'key': 'y', 'type': 'llm', 'loaded_instances': [{'id': 'elephant-1-b', 'config': {}}]}],
                   [{'key': 'google/embedding-gemma-300m', 'type': 'embedding', 'loaded_instances': [{'id': 'emb-2', 'config': {}}]}])
        for extra in hostile:
            for argv in (RUN, self.LT):
                with self.subTest(extra=extra, argv=argv[0]):
                    self.fresh(); self.w.models['models'] += copy.deepcopy(extra)
                    self.main(argv)
                    ids = [b['instance_id'] for _, _, b in self.w.posts()]
                    self.assertNotIn('elephant-1', ids); self.assertNotIn('ym-research-embedding', ids)
                    self.assertFalse(any(i.startswith('elephant-1') for i in ids), ids)
                    keys = {i['id']: e['key'] for e in self.w.models['models'] for i in e['loaded_instances']}
                    self.assertFalse([i for i in ids if keys.get(i) == 'elephant-1'], ids)

    # ---- G (2): every unloaded instance is announced (key and id) before the first switch-off and in the row
    def test_G2_every_unloaded_instance_is_named_before_the_first_switch_off_and_in_the_row(self):
        said = []
        out = lambda line: (said.append((len(self.w.posts()), len(self.w.bootouts()), str(line))), self.w.out.append(line))
        self.w.models['models'].append({'key': 'other/model', 'type': 'llm', 'loaded_instances': [{'id': 'copy', 'config': {}}]})
        self.assertEqual(self.main(RUN, out=out), 0, self.w.text())
        posts, boots, line = next(x for x in said if x[2].startswith('check passed and staged'))
        self.assertEqual((posts, boots), (0, 0))
        row = self.w.rows()[-1]
        unloaded = [(m['key'], m['instance_id']) for m in row['switch_offs']['models_unloaded'] if m['unloaded']]
        self.assertEqual(sorted(unloaded), [('other/model', 'copy'), ('qwen/qwen3.8-27b', 'qwen3.8-27b-chat')])
        for key, iid in unloaded:
            self.assertIn('%s (%s)' % (iid, key), line); self.assertIn('%s (%s)' % (iid, key), row['announcement'])
        self.assertEqual(sorted(b['instance_id'] for _, _, b in self.w.posts()), ['copy', 'qwen3.8-27b-chat'])

    def test_G2_a_model_that_appears_after_the_announcement_is_reported_and_left_alone(self):
        orig = self.w.http; added = []
        def http(method, url, body=None, timeout=None):
            reply = orig(method, url, body, timeout)
            if method == 'POST' and not added:
                added.append(1)
                self.w.models['models'].append({'key': 'late/model', 'type': 'llm', 'loaded_instances': [{'id': 'late-1', 'config': {}}]})
            return reply
        self.assertEqual(self.main(RUN, http=http), 0, self.w.text())
        self.assertEqual([b['instance_id'] for _, _, b in self.w.posts()], ['qwen3.8-27b-chat'])
        row = self.w.rows()[-1]
        self.assertTrue(any('LOADED AFTER THE ANNOUNCEMENT' in a and 'late-1' in a for a in row['alerts']), row['alerts'])
        self.assertTrue(row['launched'])

    # ---- G (3): the load path's own instance, named before its load, is the only thing its unload names
    def test_G3_the_load_tests_instance_is_named_before_its_load_and_is_all_its_unload_names(self):
        seen = {}
        orig = FakeLoadPath.load
        def load(lp):
            seen['intent'] = json.loads(self.intent.read_text()); return orig(lp)
        FakeLoadPath.load = load
        try:
            self.assertEqual(self.main(self.LT), 0, self.w.text())
        finally:
            FakeLoadPath.load = orig
        ident = 'mf-native-' + 'a' * 32
        self.assertEqual(seen['intent']['load_identifier'], ident); self.assertEqual(self.w.unloads_via_pipeline, [ident])
        self.assertNotIn(ident, [b['instance_id'] for _, _, b in self.w.posts()])

    def test_G3_the_pipeline_load_path_names_its_own_identifier_in_load_and_unload_only(self):
        calls = []
        class SDK:
            def call(self, op, timeout, **kw):
                calls.append((op, kw))
                return {'info': {'instanceReference': 'ref-own'}} if op == 'load' else {'unloaded': kw.get('identifier')}
        lp = object.__new__(OP.PipelineLoadPath)
        lp.q = types.SimpleNamespace(LOAD_S=1, UNLOAD_S=1, BASELINE_S=1, unload_confirmed=lambda reply, ident: reply.get('unloaded') == ident)
        lp.identifier = OP.NATIVE_PREFIX + 'b' * 32; lp.owned = {}; lp.profile = 'qwen3-8b'; lp.embedding = 'ref-emb'
        lp.sdk = SDK(); lp.loaded = None
        lp.modules = types.SimpleNamespace(run=types.SimpleNamespace(check_load_readback=lambda *a: None))
        named_before = lp.identifier
        lp.load(); proven, _ = lp.unload()
        self.assertTrue(proven)
        self.assertEqual([kw['identifier'] for op, kw in calls], [named_before, named_before])
        unload = [kw for op, kw in calls if op == 'unload'][0]
        self.assertEqual(unload['instanceReference'], 'ref-own')      # the embedding reference is a check, never a target
        self.assertEqual(sorted(unload), ['embeddingReference', 'identifier', 'instanceReference'])

    # ---- recover: could not be made, against made and no
    def reused_launcher(self):
        self.w.procs[LAUNCHER_PID]['start'] = 'Mon Nov 16 14:44:44 2026'
        self.w.commands[LAUNCHER_PID] = '/usr/bin/vim notes.txt --k=%s' % SECRET

    def test_R_a_reused_pid_alone_does_not_hold_the_reader_off(self):
        self.killed_run(); self.w.procs.pop(OBSERVER_PID); self.reused_launcher()
        self.assertEqual(self.main(['recover']), 0, self.w.text())
        self.assertEqual(self.w.signals, []); self.assertTrue(self.w.picks_loaded); self.assertFalse(self.intent.exists())
        row = self.w.rows()[-1]
        self.assertEqual([x['pid'] for x in row['not_ours']], [LAUNCHER_PID]); self.assertNoSecret()

    def test_R_a_reused_pid_beside_a_real_survivor_is_never_signalled_and_the_survivor_holds_the_reader_off(self):
        self.killed_run(); self.reused_launcher()
        self.assertEqual(self.main(['recover']), 3)
        self.assertEqual(self.w.signals, []); self.assertFalse(self.w.picks_loaded); self.assertTrue(self.intent.exists())
        self.assertIn('OBSERVER STILL RUNNING', self.w.text())
        def reap(world):
            if world.procs.get(OBSERVER_PID, {}).get('termed_at') is not None:
                world.procs.pop(OBSERVER_PID, None)
        self.w.on_sleep.append(reap); self.w.out.clear()
        self.assertEqual(self.main(['recover', '--stop']), 0, self.w.text())
        self.assertEqual(self.w.signals, [(OBSERVER_PID, signal.SIGTERM)])        # never the reused pid
        self.assertTrue(self.w.picks_loaded); self.assertFalse(self.intent.exists()); self.assertNoSecret()

    def test_R_an_unreadable_job_list_keeps_everything(self):
        self.killed_run(); self.w.procs.pop(OBSERVER_PID); self.w.procs.pop(LAUNCHER_PID)
        orig = self.w.run
        def run(argv, cwd=None, env=None, timeout=None):
            if argv[:2] == ['/bin/launchctl', 'print'] and str(argv[2]).endswith(OP.COLLECTOR_LABEL):
                raise OSError('fake: launchctl unreadable')
            return orig(argv, cwd, env, timeout)
        self.assertEqual(self.main(['recover', '--stop'], run=run), 3)
        self.assertIn('COULD NOT BE MADE', self.w.text()); self.assertTrue(self.intent.exists()); self.assertFalse(self.w.picks_loaded)

    def test_R_an_unreadable_start_keeps_everything(self):
        self.killed_run(); self.w.procs.pop(OBSERVER_PID); self.w.ps_raises = {LAUNCHER_PID: OSError}
        self.assertEqual(self.main(['recover', '--stop']), 3)
        self.assertTrue(self.intent.exists()); self.assertFalse(self.w.picks_loaded); self.assertEqual(self.w.signals, [])

    # ---- the reader loaded from another plist: refused before any switch-off; the constant one: booted out
    def test_P_the_reader_from_its_own_plist_is_booted_out_and_from_another_refused_before_any_change(self):
        self.assertEqual(self.main(RUN), 0); self.assertEqual(len(self.w.bootouts()), 1)
        self.assertEqual(self.w.bootouts()[0][2], 'gui/501/%s' % OP.PICKS_LABEL)
        for argv in (RUN, self.LT):
            with self.subTest(argv=argv[0]):
                self.fresh()
                self.assertEqual(self.main(argv, picks_plist='/fake/LaunchAgents/elsewhere.plist'), 2)
                self.assertEqual(self.changing(), []); self.assertEqual(self.w.stages(), []); self.assertFalse(self.intent.exists())
                self.assertTrue(self.w.picks_loaded)
                if argv is RUN:
                    self.assertIn('elsewhere.plist', self.w.rows()[-1]['refused'])


if __name__ == '__main__':
    unittest.main()
