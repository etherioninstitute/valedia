"""Owned native MiroFish qualification lifecycle. Build and offline checks are inert.

The supervisor, gateway and observer remain outside the producer process group.
Execution requires a separately issued, expiring launch receipt for the frozen
candidate. Importing this file never imports MiroFish or starts services.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from mirofish_native_gateway import NativeRefused, atomic, decode, encoded

OUTCOME = "VALEDIA-MIROFISH-FRESH-1"
QWS = Path("/Volumes/NIMROD_SSD/Qwen-Weight-Space")
ARCHIVE = Path("/Volumes/NIMROD_SSD/backups/2026-10-22/home/mirofish-offline/backend")
PROJECT = QWS / "valedia-trader"
RUNTIME = QWS / "runtime/mirofish-local-20261106"
PYTHON = RUNTIME / "venv-py311/bin/python"
JAVA = Path("/Library/Java/JavaVirtualMachines/temurin-25.jdk/Contents/Home")
PRODUCER_SCRIPTS = ("action_logger.py", "owned_context_budget.py", "public_research.py",
                    "run_parallel_simulation.py", "run_reddit_simulation.py", "run_twitter_simulation.py")
PINNED_APP = ("__init__.py", "config.py", "api/graph.py", "api/simulation.py", "api/report.py",
              "models/project.py", "services/graph_builder.py", "services/ontology_generator.py",
              "services/simulation_manager.py", "services/simulation_runner.py", "services/report_agent.py",
              "storage/neo4j_storage.py", "storage/embedding_service.py", "utils/llm_client.py",
              "services/oasis_profile_generator.py", "services/simulation_config_generator.py")


def strict_probe_success(value):
    """The two nonmarket probes require one actual JSON boolean, no coercion."""
    return type(value) is dict and set(value) == {"ok"} and value["ok"] is True


def require(ok, message):
    if not ok:
        raise NativeRefused(message)


def digest(path):
    sha=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def owned(path, root):
    path, root = Path(path), Path(root)
    require(path.is_absolute() and root.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)), "absolute nonsymlink owned path required")
    require(path == root or path.is_relative_to(root), "path outside owned scope")
    return path


def identity(pid):
    from mirofish_native_startup import process_info_tool
    result = subprocess.run([process_info_tool(), "-p", str(pid), "-o", "pid=,pgid=,lstart="],
                            capture_output=True, text=True, timeout=3, check=False)
    if result.returncode or not result.stdout.strip():
        return None
    fields = result.stdout.split()
    require(len(fields) >= 7 and fields[0].isdigit() and fields[1].isdigit(), "process metadata shape")
    return {"pid": int(fields[0]), "pgid": int(fields[1]), "start": " ".join(fields[2:])}


def group_members(pgid):
    from mirofish_native_startup import process_info_tool
    result = subprocess.run([process_info_tool(), "-axo", "pid=,pgid=,lstart=,stat="], capture_output=True, text=True, timeout=3, check=True)
    rows = []
    for line in result.stdout.splitlines():
        values = line.split()
        if len(values) == 8 and values[1].isdigit() and int(values[1]) == pgid and not values[-1].startswith("Z"):
            rows.append({"pid": int(values[0]), "pgid": int(values[1]), "start": " ".join(values[2:7])})
    return rows


class OwnedGroup:
    def __init__(self, process):
        self.process = process
        self.owner = identity(process.pid)
        require(self.owner is not None and self.owner["pgid"] == process.pid and process.pid != os.getpgrp(), "dedicated process group required")

    def stop(self, deadline):
        observed = identity(self.process.pid)
        if observed is None:
            require(self.process.poll() is not None and not group_members(self.owner["pgid"]), "lost live process-group ownership anchor")
            return {"owner": self.owner, "already_exited": True, "live_group_members": []}
        require(observed == self.owner and observed["pgid"] != os.getpgrp(), "process owner changed; refuse signaling")
        before = group_members(self.owner["pgid"])
        os.killpg(self.owner["pgid"], signal.SIGTERM)
        end = min(deadline, time.monotonic() + 3)
        while group_members(self.owner["pgid"]) and time.monotonic() < end:
            time.sleep(.03)
        remaining = group_members(self.owner["pgid"])
        if remaining:
            require(any(row in before for row in remaining), "no exact surviving ownership anchor; refuse group reuse")
            os.killpg(self.owner["pgid"], signal.SIGKILL)
        self.process.wait(timeout=max(.1, min(3, deadline - time.monotonic())))
        end = min(deadline, time.monotonic() + 2)
        while group_members(self.owner["pgid"]) and time.monotonic() < end:
            time.sleep(.03)
        after = group_members(self.owner["pgid"])
        require(not after, "owned live descendants remain after bounded cleanup")
        return {"owner": self.owner, "returncode": self.process.returncode, "stopped": True, "live_group_members": after}


def _quote(value):
    return json.dumps(str(value))


def worker_policy(producer, entry_root, proxy_port, graph_port):
    producer, entry_root = Path(producer), Path(entry_root)
    require(all(type(p) is int and 1024 <= p <= 65535 for p in (proxy_port, graph_port)) and proxy_port != graph_port and 1235 not in (proxy_port, graph_port), "dedicated local ports required")
    reads = [producer, entry_root, RUNTIME, Path("/System"), Path("/usr/lib"), Path("/usr/share"),
             Path("/opt/homebrew/Cellar"), Path("/opt/homebrew/opt"), Path("/opt/homebrew/lib"),
             Path("/Library/Apple/System/Library"), Path("/private/etc/ssl")]
    writes = [producer / "backend/uploads", producer / "backend/log", producer / "backend/logs", producer / "tmp"]
    lines = ["(version 1)", "(deny default)", "(allow process-fork)", '(allow sysctl-read (sysctl-name "hw.ncpu" "hw.activecpu" "hw.logicalcpu" "hw.physicalcpu" "hw.optional.arm64" "hw.optional.neon" "hw.memsize" "hw.cpufamily" "hw.pagesize" "hw.cachelinesize" "hw.machine" "hw.model" "hw.tbfrequency" "kern.osrelease" "kern.osversion" "kern.ostype" "kern.osrevision" "kern.argmax" "kern.boottime" "kern.hostname" "kern.version"))',
             "(allow file-read-metadata)", "(allow process-info* (target self))", "(allow signal (target self))"]
    for path in reads:
        lines.append("(allow file-read* file-map-executable (subpath " + _quote(path) + "))")
    for path in writes:
        lines.append("(allow file-read* file-write* (subpath " + _quote(path) + "))")
    for path in ["/dev/null", "/dev/urandom", "/dev/random", "/private/etc/localtime", "/usr/bin/uname", "/usr/bin/file", "/usr/share/misc/magic.mgc"]:
        lines.append("(allow file-read* (literal " + _quote(path) + "))")
    for path in [PYTHON, PYTHON.resolve(), PYTHON.resolve().parents[1] / "Resources/Python.app/Contents/MacOS/Python", Path("/usr/bin/uname"), Path("/usr/bin/file")]:
        lines.append("(allow process-exec (literal " + _quote(path) + "))")
    for port in (proxy_port, graph_port):
        lines.append(f'(allow network-outbound (remote ip "localhost:{port}"))')
    lines.append('(import "/System/Library/Sandbox/Profiles/dyld-support.sb")')
    return "\n".join(lines) + "\n"


def copy_sources(work):
    work = Path(work)
    parent = owned(work / "owned-source", work)
    require(not parent.exists(), "owned source already exists; no silent replacement")
    producer = parent / "producer"
    backend = producer / "backend"
    backend.mkdir(parents=True)
    rows = []
    files = sorted((ARCHIVE / "app").rglob("*.py")) + [ARCHIVE / "scripts" / n for n in PRODUCER_SCRIPTS]
    files += [ARCHIVE / "requirements.txt", ARCHIVE / "pyproject.toml"]
    for source in files:
        require(source.is_file() and not source.is_symlink(), "allowlisted regular source required")
        relative = source.relative_to(ARCHIVE)
        require(not any(p.startswith(".") or p in {"uploads", "log", "logs", "tests"} for p in relative.parts), "excluded source path")
        target = backend / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes(); target.write_bytes(raw)
        rows.append({"source": str(source), "target": str(target), "source_sha256": hashlib.sha256(raw).hexdigest()})
    adapter_source=PROJECT/'src/mirofish_oasis_db.py'
    adapter_target=backend/'scripts/owned_oasis_db.py'
    adapter_target.write_bytes(adapter_source.read_bytes())
    rows.append({'source':str(adapter_source),'target':str(adapter_target),'source_sha256':digest(adapter_source)})
    policy_source=PROJECT/'src/mirofish_native_caller_policy.py'
    policy_target=backend/'app/owned_caller_budget.py'
    policy_target.write_bytes(policy_source.read_bytes())
    rows.append({'source':str(policy_source),'target':str(policy_target),'source_sha256':digest(policy_source)})
    patches = []
    def patch(relative, old, new):
        path = backend / relative
        text = path.read_text()
        require(text.count(old) == 1, "reviewed transport seam drift: " + relative)
        path.write_text(text.replace(old, new))
        patches.append({"path": relative, "old": old, "new": new})
    patch("app/storage/embedding_service.py", "    raw = (api if api is not None else os.environ.get(\"EMBEDDING_API\", \"\")).strip().lower()", "    raw = (api if api is not None else os.environ.get(\"EMBEDDING_API\", \"\")).strip().lower()\n    if os.environ.get('MIROFISH_TEST_ISOLATED') == '1' and raw in ('', 'openai') and base_url.rstrip('/') == Config.LLM_BASE_URL.rstrip('/'):\n        return 'openai'")
    patch("app/storage/embedding_service.py", "                    json=payload,\n                    timeout=self.timeout,", "                    json=payload,\n                    headers={'Authorization': 'Bearer ' + Config.LLM_API_KEY} if os.environ.get('MIROFISH_TEST_ISOLATED') == '1' else {},\n                    timeout=self.timeout,")
    patch("app/utils/llm_client.py", "and os.environ.get('MIROFISH_TEST_ROUTE') == 'ornith-local')", "and os.environ.get('MIROFISH_TEST_ROUTE') == 'native-local')")
    patch("app/storage/embedding_service.py", "        timeout: int = 30,", "        timeout: int = 180,")
    patch("scripts/run_parallel_simulation.py", "            timeout=90,", "            timeout=180,")
    patch('scripts/run_parallel_simulation.py', '\nasync def main():', "\nif os.environ.get('MIROFISH_TEST_ISOLATED') == '1' and os.environ.get('MIROFISH_TEST_ROUTE') == 'native-local':\n    from owned_oasis_db import wrap_platforms as _owned_wrap_platforms\n    run_twitter_simulation, run_reddit_simulation = _owned_wrap_platforms(\n        run_twitter_simulation, run_reddit_simulation,\n        os.path.join(os.environ['MIROFISH_TEST_ROOT'], 'backend', 'uploads', 'simulations'))\n\n\nasync def main():")
    patch('app/config.py', "    PREP_LLM_MAX_TOKENS = int(os.environ.get('MIROFISH_PREP_LLM_MAX_TOKENS', '2000'))", "    PREP_LLM_MAX_TOKENS = int(os.environ.get('MIROFISH_PREP_LLM_MAX_TOKENS', '2000'))\n    if os.environ.get('MIROFISH_TEST_ISOLATED') == '1' and os.environ.get('MIROFISH_TEST_ROUTE') == 'native-local':\n        from .owned_caller_budget import CALLER_OUTPUT_LIMIT as NATIVE_CHAT_OUTPUT_LIMIT, choose_output_limit as _task_output_limit\n        PREP_LLM_MAX_TOKENS = _task_output_limit(PREP_LLM_MAX_TOKENS)")
    patch('app/utils/llm_client.py', '        kwargs = {\n', "        if os.environ.get('MIROFISH_TEST_ISOLATED') == '1' and os.environ.get('MIROFISH_TEST_ROUTE') == 'native-local':\n            from ..owned_caller_budget import choose_output_limit\n            max_tokens = choose_output_limit(max_tokens)\n            if max_tokens > Config.NATIVE_CHAT_OUTPUT_LIMIT:\n                raise ValueError('task caller output policy drift')\n        kwargs = {\n")
    for row in rows:
        target = Path(row["target"]); row["copied_sha256"] = digest(target); target.chmod(0o444)
    control = parent / "worker-entry"
    control.mkdir()
    for name in ["mirofish_native_run.py", "mirofish_native_gateway.py", "mirofish_native_graph.py", "mirofish_native_startup.py", "mirofish_native_children.py"]:
        source = PROJECT / "src" / name; target = control / name
        target.write_bytes(source.read_bytes()); target.chmod(0o444)
        rows.append({"source": str(source), "target": str(target), "source_sha256": digest(source), "copied_sha256": digest(target)})
    for path in [backend / "uploads", backend / "uploads/reports", backend / "log", backend / "logs", producer / "tmp"]:
        path.mkdir()
    scenario = (work / "scenario-input.txt").read_bytes()
    seal = decode((work / "scenario-input-seal.json").read_bytes())
    # The lead's separate seal schema is validated by the frozen candidate manifest;
    # the bytes used by the worker are always copied verbatim, never reconstructed.
    (producer / "scenario-input.txt").write_bytes(scenario); (producer / "scenario-input.txt").chmod(0o444)
    manifest = {"schema": 1, "files": rows, "transport_patches": patches,
                "scenario_sha256": hashlib.sha256(scenario).hexdigest(), "scenario_seal_sha256": hashlib.sha256(encoded(seal)).hexdigest()}
    atomic(work / "builder/source-copy-manifest.json", manifest)
    return producer, control, manifest


def worker_environment(producer, proxy_url, model, secret, graph_port, event_fd):
    require(proxy_url.startswith("http://127.0.0.1:") and proxy_url.endswith("/v1"), "fixed local proxy required")
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "MIROFISH_TEST_ISOLATED": "1", "MIROFISH_TEST_ROOT": str(producer),
           "MIROFISH_TEST_PROXY_URL": proxy_url, "MIROFISH_TEST_MODEL": model,
           "MIROFISH_TEST_ROUTE": "native-local", "LLM_API_KEY": secret,
           "LLM_BASE_URL": proxy_url, "LLM_REPORT_BASE_URL": proxy_url, "LLM_MODEL_NAME": model,
           "MIROFISH_REPORT_MODEL": model, "EMBEDDING_API": "openai", "EMBEDDING_BASE_URL": proxy_url,
           "EMBEDDING_MODEL": "ym-research-embedding", "NEO4J_URI": f"bolt://127.0.0.1:{graph_port}",
           "NEO4J_USER": "native-local", "NEO4J_PASSWORD": "local-empty-owned-graph",
           "FLASK_DEBUG": "false", "MIROFISH_PREP_LLM_TIMEOUT": "180", "MIROFISH_PREP_LLM_MAX_TOKENS": "4096",
           "MIROFISH_REPORT_LLM_TIMEOUT": "180", "NATIVE_EVENT_FD": str(event_fd),
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
           "TOKENIZERS_PARALLELISM": "false", "TIKTOKEN_CACHE_DIR": str(RUNTIME / "cache/tiktoken")}
    temp = Path(producer) / "tmp"
    for key, name in {"TMPDIR": "tmp", "XDG_CACHE_HOME": "cache", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "state", "HF_HOME": "hf", "TORCH_HOME": "torch", "MPLCONFIGDIR": "mpl", "NLTK_DATA": "nltk", "NUMBA_CACHE_DIR": "numba", "SENTENCE_TRANSFORMERS_HOME": "sentence"}.items():
        path = temp / name; path.mkdir(exist_ok=True); env[key] = str(path)
    return env


class WorkerChannel:
    def __init__(self, work, producer, control, proxy_url, model, secret, graph_port, active_deadline):
        require(proxy_url=="http://127.0.0.1:62471/v1" and graph_port==62472,"fixed host ports required")
        from mirofish_native_children import start_worker
        self.process,self.group,self.read_fd=start_worker(model,secret,active_deadline)
        self.buffer=b""
        self.events_path=Path(work)/"builder/worker-events";self.events_path.mkdir(exist_ok=True)
        os.set_blocking(self.process.stdin.fileno(),False)

    def request(self, stage, deadline, payload=None):
        require(stage in {"graph_gate", "prepare", "start", "wait", "report"}, "closed worker stage protocol")
        outgoing=encoded({"stage":stage,"payload":payload})+b"\n";offset=0
        while offset<len(outgoing):
            remaining=deadline-time.monotonic()
            require(remaining>0 and self.process.poll() is None,"worker command absolute deadline")
            _,writable,_=select.select([],[self.process.stdin],[],min(.05,remaining))
            if writable:
                try:offset+=os.write(self.process.stdin.fileno(),outgoing[offset:])
                except BlockingIOError:pass
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                value = decode(line)
                atomic(self.events_path/(stage+".json"),value)
                require(time.monotonic()<deadline,"native worker stage late result")
                if stage=="graph_gate" and value.get("ok") is not True:
                    failure=NativeRefused("strict actual graph prerequisite refused");failure.proof=value;raise failure
                require(value.get("stage") == stage and value.get("ok") is True, "native worker stage refused: " + stage)
                return value["data"]
            ready, _, _ = select.select([self.read_fd], [], [], min(.1, max(0, deadline-time.monotonic())))
            if ready:
                data = os.read(self.read_fd, 65536)
                require(bool(data), "native worker closed event pipe")
                self.buffer += data; require(len(self.buffer) <= 131072, "worker event byte limit")
            require(self.process.poll() is None, "worker exited")
        raise NativeRefused("worker stage deadline: " + stage)


def native_worker():
    # This is the only archived-backend import point, reached solely in the
    # reviewed OS-confined producer after explicit launch authorization.
    root = Path(os.environ["MIROFISH_TEST_ROOT"])
    require(Path.cwd() == root / "backend" and os.environ.get("MIROFISH_TEST_ISOLATED") == "1", "owned worker cwd/mode")
    sys.path.insert(0, str(root / "backend"))
    from app import create_app
    from app.services.simulation_runner import SimulationRunner
    app = create_app(); client = app.test_client()
    events = os.fdopen(int(os.environ["NATIVE_EVENT_FD"]), "w", buffering=1)
    end = min(time.monotonic() + 2280, float(os.environ["MIROFISH_NATIVE_ACTIVE_DEADLINE"]))
    def checked(response):
        value = response.get_json()
        require(response.status_code == 200 and isinstance(value, dict) and value.get("success") is True, "native API refused")
        return value.get("data", {})
    def poll(path, payload=None):
        while time.monotonic() < end:
            value = checked(client.post(path, json=payload) if payload is not None else client.get(path))
            status = value.get("status")
            if status in {"completed", "ready"}:
                return value
            require(status not in {"failed", "error", "cancelled"}, "native asynchronous task failed")
            time.sleep(.1)
        raise NativeRefused("native task deadline")
    graph_qualified = False
    prepared = started = terminal = False
    sim = project = graph = None
    process = monitor = None
    for line in sys.stdin:
        command=decode(line)
        stage = command.get("stage")
        try:
            if stage == "graph_gate" and not graph_qualified and not prepared:
                from app.storage import neo4j_schema
                from mirofish_native_graph import qualify_graph
                storage=app.extensions.get('neo4j_storage')
                require(storage is not None and getattr(storage,'_driver',None) is not None,'actual native Neo4j storage missing')
                payload=command.get('payload')
                require(isinstance(payload,dict) and set(payload)=={'embedding','deadline'},'closed graph prerequisite payload')
                data=qualify_graph(storage._driver,neo4j_schema.ALL_SCHEMA_QUERIES,digest(root/'backend/app/storage/neo4j_schema.py'),payload['embedding'],deadline=min(end,float(payload['deadline'])))
                data.update(producer_root=str(root),owned_instance=os.environ['MIROFISH_TEST_MODEL'])
                graph_qualified=True
            elif stage == "prepare" and graph_qualified and not prepared:
                text = (root / "scenario-input.txt").read_bytes()
                ontology = checked(client.post("/api/graph/ontology/generate", data={"files": (io.BytesIO(text), "scenario.txt", "text/plain"), "simulation_requirement": text.decode(), "project_name": "Hypothetical native pipeline qualification"}, content_type="multipart/form-data"))
                project = ontology["project_id"]
                task = checked(client.post("/api/graph/build", json={"project_id": project, "chunk_size": 500, "chunk_overlap": 50}))
                built = poll("/api/graph/task/" + task["task_id"])
                graph = built.get("result", {}).get("graph_id") or checked(client.get("/api/graph/project/" + project)).get("graph_id")
                require(bool(graph), "native graph identity missing")
                sim = checked(client.post("/api/simulation/create", json={"project_id": project, "graph_id": graph, "enable_twitter": True, "enable_reddit": True}))["simulation_id"]
                task = checked(client.post("/api/simulation/prepare", json={"simulation_id": sim, "use_llm_for_profiles": True, "parallel_profile_count": 1}))
                poll("/api/simulation/prepare/status", {"simulation_id": sim, "task_id": task.get("task_id")})
                config_path = root / "backend/uploads/simulations" / sim / "simulation_config.json"
                config = decode(config_path.read_bytes())
                require(1 <= len(config.get("agent_configs", [])) <= 4, "actual generated participant count outside bound")
                prepared = True; data = {"project_id": project, "simulation_id": sim, "graph_id": graph, "agent_count": len(config["agent_configs"]), "config_sha256": digest(config_path)}
            elif stage == "start" and prepared and not started:
                checked(client.post("/api/simulation/start", json={"simulation_id": sim, "platform": "parallel", "max_rounds": 1, "enable_graph_memory_update": False}))
                process = SimulationRunner._processes.get(sim); monitor = SimulationRunner._monitor_threads.get(sim)
                require(process is not None and monitor is not None, "actual native child/monitor handles missing")
                started = True
                data = {"simulation_id": sim, "child_pid": process.pid, "child_pgid": os.getpgid(process.pid), "runner_state": SimulationRunner.get_run_state(sim).to_dict()}
            elif stage == "wait" and started and not terminal:
                while process.poll() is None and time.monotonic() < end:
                    time.sleep(.1)
                require(process.poll() == 0, "native child did not exit zero")
                monitor.join(timeout=max(0, min(10, end-time.monotonic())))
                require(not monitor.is_alive(), "native monitor did not finish final save")
                state_path = root / "backend/uploads/simulations" / sim / "run_state.json"
                first = state_path.read_bytes(); final = state_path.read_bytes()
                state = decode(final)
                require(first == final and state.get("runner_status") == "completed" and state.get("completed_at"), "native final run state not quiescent/completed")
                terminal = True
                evidence = {}
                for platform in ("twitter", "reddit"):
                    action_path = state_path.parent / platform / "actions.jsonl"
                    require(action_path.is_file(), "actual platform action log missing")
                    rows = [decode(line) for line in action_path.read_bytes().splitlines() if line]
                    evidence[platform] = {"rows": len(rows), "sha256": digest(action_path), "event_types": sorted({str(row.get("type", row.get("event", row.get("action_type", "unknown")))) for row in rows})}
                data = {"simulation_id": sim, "child_exit": process.returncode, "monitor_joined": True, "run_state_sha256": hashlib.sha256(final).hexdigest(), "state": state, "platform_logs": evidence, "live_interviews": "unavailable after native --no-wait; no keepalive patch"}
            elif stage == "report" and terminal:
                item = checked(client.post("/api/report/generate", json={"simulation_id": sim}))
                result = poll("/api/report/generate/status", {"simulation_id": sim, "task_id": item["task_id"]})
                report = result.get("report_id") or result.get("result", {}).get("report_id") or item["report_id"]
                data = {"simulation_id": sim, "report_id": report}
            else:
                raise NativeRefused("native stage order/replay refused")
            events.write(json.dumps({"stage": stage, "ok": True, "data": data}) + "\n")
        except BaseException as exc:
            events.write(json.dumps({"stage": stage, "ok": False, "failure": type(exc).__name__, "details": getattr(exc,"proof",None)}) + "\n")
            # Remain a live group ownership anchor; the supervisor owns termination.
            for ignored in sys.stdin:
                pass
            return


def verify_launch(work, gate, candidate_digest, run_id=None):
    value = decode(Path(gate).read_bytes())
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(value.get("expires_at_utc", ""))
    require(expires.tzinfo is not None and now < expires and (expires-now).total_seconds() <= 3600, "finite current lead launch receipt required")
    require(value.get("outcome") == OUTCOME and value.get("action") == "one-native-attempt" and value.get("candidate_sha256") == candidate_digest, "lead launch receipt does not bind candidate")
    claim = Path(work) / "builder/one-attempt.claim"
    fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded({"gate_sha256": digest(gate), "candidate_sha256": candidate_digest, "claimed_at": now.isoformat(), "run_id": run_id})); stream.flush(); os.fsync(stream.fileno())
    return value



def resource_sample(pre=False):
    from mirofish_native_startup import resource_from_capsule
    sample=resource_from_capsule()
    reason='memory free threshold' if sample['free_percent'] < (65 if pre else 30) else ('conservative owned-model RSS upper bound exceeded' if not pre and sample['owned_scope_rss_bytes']>40*1024**3 else None)
    if reason:
        failure=NativeRefused(reason);failure.resource_sample=sample
        raise failure
    return {**sample,'rss_proof':'Original privileged host fixed-field process baseline; includes all Bionic/listener family and every new PID/start, retaining reparented identities. Only stable baseline unrelated identities excluded. Full-host RSS diagnostic retained; signed capsule<=3s.'}


def record_resource_failure(receipt,path,exc):
    sample=getattr(exc,'resource_sample',None)
    if sample is not None:
        receipt.setdefault('resource_failure_samples',[]).append({'reason':str(exc),'sample':sample})
        atomic(path,receipt)  # Exact validated bad measurement is durable before cancellation.

def monitor_resource_once(receipt,path,aborted,gateway,errors):
    try:resource_sample(False)
    except BaseException as exc:
        errors.append(type(exc).__name__+':'+str(exc))
        try:record_resource_failure(receipt,path,exc)
        except BaseException as evidence_error:errors.append('resource evidence write failure:'+type(evidence_error).__name__+':'+str(evidence_error))
        finally:
            aborted.set()
            if gateway is not None:gateway.cancel()
        return False
    return True


def copy_paper(work):
    source_manifest=Path(work).parent/'valedia-mirofish-paper-20261106/repair1/source-copy-manifest.json'
    rows=decode(source_manifest.read_bytes());require(type(rows) is list and len(rows)==15,'accepted fifteen-source paper manifest required')
    app=Path(work)/'owned-source/paper_app';(app/'src').mkdir(parents=True);(app/'state').mkdir()
    copied=[]
    for row in rows:
        source=Path(row['source']);require(source.parent==PROJECT/'src' and not source.is_symlink() and digest(source)==row['sha256'],'accepted current paper source drift')
        target=app/'src'/source.name;target.write_bytes(source.read_bytes());target.chmod(0o444)
        copied.append({'path':str(target),'sha256':digest(target),'original':str(source)})
    atomic(Path(work)/'builder/paper-source-manifest.json',copied)
    return app


def graph_configuration(work,port):
    graph=Path(work)/'builder/graph'
    for name in ['conf','data','logs','run','import','plugins','tmp']:(graph/name).mkdir(parents=True,exist_ok=True)
    config=['server.default_listen_address=127.0.0.1',f'server.bolt.listen_address=127.0.0.1:{port}',
            f'server.bolt.advertised_address=127.0.0.1:{port}','server.http.enabled=false','server.https.enabled=false',
            'dbms.security.auth_enabled=false','dbms.usage_report.enabled=false','server.fleet_discovery.enabled=false',
            'server.memory.heap.initial_size=256m','server.memory.heap.max_size=512m','server.memory.pagecache.size=128m',
            'server.jvm.additional=-XX:-UsePerfData',f'server.jvm.additional=-Djava.io.tmpdir={graph/"tmp"}',
            f'server.jvm.additional=-Djna.tmpdir={graph/"tmp"}']
    for name in ['data','logs','run','import','plugins']:config.append(f'server.directories.{name}={graph/name}')
    (graph/'conf/neo4j.conf').write_text('\n'.join(config)+'\n')
    return graph



def graph_environment(graph, original_home):
    graph=Path(graph);neo=RUNTIME/'neo4j-community-2026.10.1'
    env={'PATH':'/usr/bin:/bin','JAVA_HOME':str(JAVA),'NEO4J_HOME':str(neo),'NEO4J_CONF':str(graph/'conf'),'TMPDIR':str(graph/'tmp'),'JAVA_OPTS':'-XX:-UsePerfData -Djava.io.tmpdir='+str(graph/'tmp')}
    if original_home is not None:
        env['HOME']=original_home  # Preserve the existing value; never re-home or copy other ambient values.
    return env

def observer_policy(work,proxy_port=62471,graph_port=62472):
    from mirofish_native_model import SDK_PORT,observer_reads
    tokenizer_roots,model_files=observer_reads()  # R7: exactly the plan's tokenizer origins and pinned serving files
    work=Path(work)
    text=worker_policy(work,work/'owned-source/observer-entry',proxy_port,graph_port)
    extra=['(allow dynamic-code-generation)']
    for path in [JAVA,*tokenizer_roots,QWS/'runtime/flint-lmstudio-sdk-2.1.0']:
        extra.append('(allow file-read* file-map-executable (subpath '+_quote(path)+'))')
    extra += ['(allow file-read* file-write* (subpath '+_quote(work)+'))',
              '(allow file-read* file-write* (subpath '+_quote(RUNTIME/'tmp')+'))',
              '(allow signal (target children))','(allow process-info-listpids)','(allow process-info-pidinfo)','(allow sysctl-read)' ]
    for path in ['/usr/bin/sandbox-exec',str(work/'builder/ps-identity'),'/usr/bin/memory_pressure','/usr/sbin/lsof','/usr/local/bin/node',str(Path('/usr/local/bin/node').resolve()),str(JAVA/'bin/java'),'/bin/sh','/bin/bash','/usr/bin/env','/usr/bin/dirname','/usr/bin/uname',str(RUNTIME/'neo4j-community-2026.10.1/bin/neo4j')]:
        extra += ['(allow process-exec (literal '+_quote(path)+'))','(allow file-read* (literal '+_quote(path)+'))']
    for port in [1235,SDK_PORT]:extra.append(f'(allow network-outbound (remote ip "localhost:{port}"))')
    for port in [proxy_port,graph_port]:extra.append(f'(allow network-inbound (local ip "localhost:{port}"))')
    for path in model_files:extra.append('(allow file-read* (literal '+_quote(path)+'))')
    extra.append('(allow file-map-executable (literal "/bin/bash"))')
    extra.append('(deny file-write* (subpath '+_quote(work/'host-resource')+'))')
    return text+'\n'.join(extra)+'\n'



def graph_policy(work,graph_port=62472):
    graph=Path(work)/'builder/graph';neo=RUNTIME/'neo4j-community-2026.10.1'
    lines=['(version 1)','(deny default)','(allow file-read-metadata)','(allow sysctl-read)',
           '(allow process-fork)','(allow process-info* (target self))','(allow signal (target self))',
           '(allow dynamic-code-generation)']
    for path in [Path('/System'),Path('/usr/lib'),Path('/usr/share'),JAVA,neo]:
        lines.append('(allow file-read* file-map-executable (subpath '+_quote(path)+'))')
    lines.append('(allow file-read* file-write* (subpath '+_quote(graph)+'))')
    for path in ['/dev/null','/dev/random','/dev/urandom','/private/etc/localtime']:
        lines.append('(allow file-read* (literal '+_quote(path)+'))')
    lines.append('(allow file-write* (literal "/dev/null"))')
    for path in [str(JAVA/'bin/java'),str(neo/'bin/neo4j'),'/usr/bin/env','/bin/sh','/bin/bash','/usr/bin/dirname','/usr/bin/uname']:
        lines.append('(allow process-exec (literal '+_quote(path)+'))')
        lines.append('(allow file-read* file-map-executable (literal '+_quote(path)+'))')
    lines.append('(allow process-exec (literal '+_quote(JAVA/'lib/jspawnhelper')+'))')
    lines.append(f'(allow network-inbound (local ip "localhost:{graph_port}"))')
    lines.append('(import "/System/Library/Sandbox/Profiles/dyld-support.sb")')
    return '\n'.join(lines)+'\n'

def prepare_candidate(work):
    from mirofish_native_model import SDK_SCRIPT, TOKENIZER_FILES, GGUF_SELECTION, SDK_FILE, MODEL_PROFILES, MODEL_PLAN, PLAN_PROFILES, DEFAULT_MODEL_PROFILE, profile_files
    work=owned(Path(work),Path(work));builder=work/'builder';builder.mkdir(exist_ok=True)
    producer,control,source=copy_sources(work);paper=copy_paper(work)
    auth=[]
    for relative in ["app/utils/llm_client.py","app/services/simulation_config_generator.py","app/services/oasis_profile_generator.py","scripts/run_parallel_simulation.py"]:
        path=producer/'backend'/relative;tree=ast.parse(path.read_bytes())
        guards=[ast.unparse(node) for node in ast.walk(tree) if isinstance(node,ast.Compare) and 'Config.LLM_API_KEY' in ast.unparse(node)]
        require(bool(guards),'native Config-bound authentication guard missing')
        auth.append({'path':str(path),'guards':guards,'sha256':digest(path)})
    for path in (producer/'backend').rglob('*.py'):ast.parse(path.read_bytes(),filename=str(path))
    atomic(builder/'auth-contract-static.json',{'guards':auth,'per_run_token_input':'explicit LLM_API_KEY; constructor/child pinned to Config.LLM_API_KEY','auth_source_patches':0,'copied_embedding_header':'Bearer + Config.LLM_API_KEY in isolated mode','compiled_only':True})
    review=builder/'review-source';review.mkdir()
    for name in ['mirofish_native_gateway.py','mirofish_native_run.py','mirofish_native_model.py','mirofish_native_graph.py','mirofish_native_startup.py','mirofish_native_children.py','mirofish_oasis_db.py','mirofish_native_caller_policy.py','test_mirofish_native.py']:
        target=review/name;target.write_bytes((PROJECT/'src'/name).read_bytes());target.chmod(0o444)
    (review/'mirofish-native-qualification.md').write_bytes((PROJECT/'docs/mirofish-native-qualification.md').read_bytes())
    observer=work/'owned-source/observer-entry';observer.mkdir()
    for name in ['mirofish_native_run.py','mirofish_native_gateway.py','mirofish_native_model.py','mirofish_native_graph.py','mirofish_native_startup.py','mirofish_native_children.py']:
        target=observer/name;target.write_bytes((PROJECT/'src'/name).read_bytes());target.chmod(0o444)
    for entry in (review,observer):  # the checkpoint choice travels with the candidate
        for carried in ('mirofish_model_profile.json','mirofish_model_registry.json'):
            if (PROJECT/'src'/carried).exists():
                target=entry/carried;target.write_bytes((PROJECT/'src'/carried).read_bytes());target.chmod(0o444)
    (builder/'sdk_native.mjs').write_text(SDK_SCRIPT)
    graph_configuration(work,62472)
    (builder/'observer.sb').write_text(observer_policy(work))
    (builder/'graph.sb').write_text(graph_policy(work))
    (builder/'worker.sb').write_text(worker_policy(producer,control,62471,62472))
    # The five same-base tokenizer-origin metadata files are distinct from the
    # loaded GGUF serving files. No MLX weight shard is a GGUF input. A plan
    # pins each of its profiles' files; a tokenizer origin two profiles share is
    # listed once.
    model_files=[];by_profile={};records={}
    for profile in PLAN_PROFILES:
        entry=MODEL_PROFILES[profile];root=entry['tokenizer_root'];pins={};rows=[]
        for name in TOKENIZER_FILES:
            path=root/name
            require(path.is_file() and not path.is_symlink(),'frozen tokenizer origin missing')
            sha=digest(path);pins[name]=sha
            rows.append({'path':str(path),'bytes':path.stat().st_size,'sha256':sha,'role':'same-base tokenizer/template origin metadata; not serving weights'})
        for item in profile_files(profile):
            path=item['path']
            require(path.is_file() and not path.is_symlink() and path.stat().st_size==item['bytes'],'single verified GGUF source missing/size drift')
            sha=digest(path);require(sha==item['sha256'],'single GGUF source hash drift')
            phase='' if MODEL_PLAN is None else ('turns model ' if profile==MODEL_PLAN['turns'] else 'setup and report model ')+profile+': '
            rows.append({'path':str(path),'bytes':item['bytes'],'sha256':sha,'role':phase+item['role']})
        model_files+=[row for row in rows if row not in model_files];by_profile[profile]=pins
        records[profile]={'required_tokenizer_files':pins,'tokenizer_origin_root':str(root),'files':rows,'GGUF_selection':entry['gguf_selection']}
        if profile==DEFAULT_MODEL_PROFILE:  # R10: the 27B's revision identifiers describe the 27B's files only
            records[profile].update(same_base_revision='1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0',GGUF_source_revision='a316ea527fde32c12487be7574e2db5ac2ad2a7e')
    model=by_profile[PLAN_PROFILES[0]]
    if MODEL_PLAN is None:
        source_record=dict(records[PLAN_PROFILES[0]],sdk_source_sha256=digest(SDK_FILE))
        if PLAN_PROFILES[0]!=DEFAULT_MODEL_PROFILE:source_record['profile']=PLAN_PROFILES[0]
    else:
        source_record={'plan':dict(MODEL_PLAN),'profiles':records,'files':model_files,'sdk_source_sha256':digest(SDK_FILE)}
    atomic(builder/'model-source-manifest.json',source_record)
    # Runtime source files are frozen through complete per-file hashes, not only
    # distribution names. Mutable cache and rejected environments are excluded.
    inputs=[]
    for root in [work/'owned-source',builder/'review-source',RUNTIME/'venv-py311/lib/python3.11/site-packages',RUNTIME/'neo4j-community-2026.10.1/lib',QWS/'runtime/flint-lmstudio-sdk-2.1.0/node_modules']:
        for path in sorted(root.rglob('*')):
            if path.is_file() and not path.is_symlink() and not any(part in {'__pycache__','state','uploads','logs','log','tmp'} for part in path.relative_to(root).parts):
                inputs.append({'path':str(path),'sha256':digest(path)})
    for path in [work/'final-build-contract.json',work/'scenario-input.txt',work/'scenario-input-seal.json',RUNTIME/'requirements-py311.lock',RUNTIME/'venv-py311/pyvenv.cfg',Path('/usr/local/bin/node').resolve(),PYTHON.resolve(),JAVA/'release',JAVA/'bin/java',SDK_FILE,builder/'sdk_native.mjs',builder/'observer.sb',builder/'graph.sb',builder/'worker.sb',builder/'graph/conf/neo4j.conf',builder/'model-source-manifest.json',builder/'auth-contract-static.json',builder/'jdk-repair/validation-result.json',builder/'source-copy-manifest.json',builder/'paper-source-manifest.json']:
        inputs.append({'path':str(path),'sha256':digest(path)})
    manifest={'outcome':OUTCOME,'files':inputs,'model_files':model_files,'model_tokenizer':model,'proxy_port':62471,'graph_port':62472,
              'uniform_policy':{'caller_timeout':180,'gateway_arrival_seconds':175,'queue_seconds':170,'cleanup_seconds':120,'wall_seconds':2400},
              'embedding_context_predicate':'fixed existing SDK embedding handle; exact instance/readback/context and same raw text SDK countTokens/embed methods; no OpenAI REST prefix layer',
              'runtime_predicates':['actual checkpoint+owned instance+context readback','SDK formatted prompt matches one frozen local thinking/raw-arguments branch; SDK tokenizer agrees','baseline embedding identity/context stable; actual vectors finite768','free RAM/RSS guards','two no-thinking nonmarket probes inside all budgets'],
              'launch_available':True,'launch_authorized':False,
              'known_blockers':(['UNRESOLVED_EXACT_GGUF_SDK_SELECTOR'] if GGUF_SELECTION is None else [])+([] if decode((builder/'jdk-repair/validation-result.json').read_bytes()).get('exit')==0 else ['BLOCKED_JDK_POLICY']),
              'source_status':'FROZEN_UNLAUNCHED_COHERENT_CANDIDATE'}
    if MODEL_PLAN is not None:
        manifest.update(model_plan=dict(MODEL_PLAN),model_tokenizer_by_profile=by_profile)
        manifest['runtime_predicates'].append('two pinned models by phase: setup model for preparation and report, turns model for the simulation, swapped at the two boundaries with the gateway idle, the same two probes after each swap')
    atomic(builder/'candidate-manifest.json',manifest)
    return {'candidate_sha256':digest(builder/'candidate-manifest.json'),'manifest':str(builder/'candidate-manifest.json'),'frozen_file_count':len(inputs),'model_files':len(model_files)}


def verify_candidate(work):
    work=Path(work);path=work/'builder/candidate-manifest.json';value=decode(path.read_bytes())
    require(value.get('outcome')==OUTCOME,'wrong candidate outcome')
    require(not value.get('known_blockers'),'candidate has an unresolved prerequisite; no launch')
    for item in value['files']+value['model_files']:
        target=Path(item['path']);require(target.is_file() and not target.is_symlink() and digest(target)==item['sha256'],'frozen candidate input changed: '+target.name)
    return value,digest(path)


def observer_store(work,producer,run_id):
    import importlib
    app=Path(work)/'owned-source/paper_app';src=app/'src'
    protected=['recovery_state','recovery_runtime','valedia_config','mirofish_provenance','mirofish_paper','master_allocator','mirofish_signal_extractor']
    require(not any(name in sys.modules for name in protected),'paper module was preloaded before exact relocation')
    sys.path.insert(0,str(src))
    os.environ['ALPACA_PAPER']='true';os.environ['VALEDIA_LIVE_OPT_IN']='0'
    provenance=importlib.import_module('mirofish_provenance');recovery=importlib.import_module('recovery_state');paper=importlib.import_module('mirofish_paper')
    clock=lambda:datetime.now(timezone.utc)
    account='native-paper-'+run_id;journal_path=str(app/'state/native-paper.sqlite3')
    profile=provenance.ProducerProfile('native-'+run_id,str(producer),tuple(('backend/app/'+name,digest(Path(producer)/'backend/app'/name)) for name in PINNED_APP),digest(src/'mirofish_signal_extractor.py'),research_only=False,max_age_seconds=3600)
    binding=provenance.PaperBinding(account,journal_path,(('alpaca','NVDA'),))
    store=provenance.AuthorityStore(str(Path(work)/'builder/observer'),profile,binding,clock=clock)
    journal=recovery.Journal(journal_path,account=account,equity='1000.00',daily_limit='20.00',clock=clock)
    review=paper.PaperReview('native-review-'+run_id,account,journal_path,'alpaca','NVDA','10.00','1000.00','20.00')
    paths={name:str(module.__file__) for name,module in sys.modules.items() if name in protected}
    require(all(Path(path).parent==src for path in paths.values()),'original paper module imported')
    atomic(Path(work)/'builder/observer-module-origins.json',paths)
    return store,paper.LocalPaperFlow(store,journal),review,recovery.Refused


def completed_native_chat_calls(calls):
    return [row for row in calls if row.get('kind')=='chat' and row.get('state')=='complete' and row.get('dispatched') is True and row.get('upstream_status')==200]



def qualify_native_chat_calls(calls):
    native=[row for row in calls if row.get('kind')=='chat']
    completed=completed_native_chat_calls(native)
    require(len(native)>=2 and len(completed)==len(native),'all native chats must be completed dispatched200 forwards; refused/unknown or insufficient calls cannot qualify')
    return completed


def compatibility_probes(gateway,identifier,secret):
    """Two fixed nonmarket compatibility requests. Both count in all budgets. Run at the start and, in a plan, again
    on the newly loaded model after each swap (TMF4 addendum 1, ruling d)."""
    text_probe={'model':identifier,'messages':[{'role':'user','content':'Non-market transport check. Reply exactly with JSON {"ok":true}.'}],'max_tokens':512,'temperature':0}
    raw=gateway.handle('/v1/chat/completions',text_probe,'Bearer '+secret);reply=decode(raw)['choices'][0]
    require(reply.get('finish_reason')=='stop' and strict_probe_success(decode(reply['message'].get('content',''))) and not reply['message'].get('reasoning_content'),'no-thinking JSON probe failed')
    tool_probe={'model':identifier,'messages':[{'role':'user','content':'Non-market transport check. Call no_effect once with ok=true. This function has no effects.'}],'max_tokens':512,'temperature':0,'parallel_tool_calls':False,'tools':[{'type':'function','function':{'name':'no_effect','description':'Transport probe only; no implementation and no effects.','parameters':{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}}}],'tool_choice':'required'}
    raw=gateway.handle('/v1/chat/completions',tool_probe,'Bearer '+secret);reply=decode(raw)['choices'][0];calls=reply['message'].get('tool_calls',[])
    require(reply.get('finish_reason')=='tool_calls' and len(calls)==1 and calls[0]['function']['name']=='no_effect' and strict_probe_success(decode(calls[0]['function']['arguments'])) and not reply['message'].get('reasoning_content'),'no-thinking function-tool probe failed')


def check_load_readback(loaded,identifier,profile):
    """A plan load is read back as the run's identifier carrying the plan profile's checkpoint (the helper has already
    checked the selection, size, hash, load configuration and instance reference)."""
    from mirofish_native_model import MODEL_PROFILES
    key=MODEL_PROFILES[profile]['gguf_selection']['modelKey']
    info=loaded.get('info') if type(loaded) is dict else None;requested=loaded.get('requested') if type(loaded) is dict else None
    require(type(info) is dict and type(requested) is dict and info.get('identifier')==identifier and info.get('modelKey')==key and requested.get('identifier')==identifier and requested.get('key')==key and type(info.get('instanceReference')) is str and bool(info['instanceReference']),'model load readback does not name the plan profile under the run identifier: '+profile)
    return loaded


def swap_model(sdk,gateway,identifier,profile,state,embedding_reference,admission_for,deadline):
    """R4, at a plan boundary only: with the gateway provably idle (its serial lock held, no call reserved or in flight)
    unload the owned instance and confirm no language model is listed, load the next profile under the same identifier
    with the same load settings, read it back, rebuild the admission for that profile, and require that no call arrived
    meanwhile. state carries the profile behind the identifier and its readback for cleanup. Any failure freezes the
    gateway before the serial lock is released, so nothing queued can reach either model, and refuses the run."""
    from mirofish_native_model import MODEL_PROFILES
    locked=gateway.serial.acquire(blocking=False)
    try:
        require(locked,'gateway not idle at the model boundary: a call holds the serial lock')
        require(profile in MODEL_PROFILES and profile!=state.get('profile'),'closed model swap target')
        with gateway.lock:
            require(not gateway.cancelled.is_set(),'gateway cancelled at the model boundary')
            busy=[row.get('id') for row in gateway.state['calls'] if row.get('state') in ('reserved','inflight')]
            require(not busy,'gateway not idle at the model boundary: calls reserved or in flight: '+str(busy[:4]))
            count=len(gateway.state['calls'])
        previous=state['profile'];reference=((state.get('loaded') or {}).get('info') or {}).get('instanceReference')
        require(deadline-time.monotonic()>0,'model swap deadline')
        unloaded=sdk.call('unload',min(45,max(.1,deadline-time.monotonic())),identifier=identifier,instanceReference=reference,embeddingReference=embedding_reference,profile=previous)
        require(type(unloaded) is dict and unloaded.get('unloaded')==identifier and not unloaded.get('alreadyAbsent') and type(unloaded.get('snapshot')) is dict and unloaded['snapshot'].get('llm')==[],'model swap unload not confirmed: the owned instance was absent or a language model is still listed')
        state['loaded']=None;state['profile']=profile  # cleanup now targets the next profile under the same identifier
        require(deadline-time.monotonic()>0,'model swap deadline')
        loaded=check_load_readback(sdk.call('load',min(180,deadline-time.monotonic()),identifier=identifier,profile=profile),identifier,profile)
        state['loaded']=loaded
        admission=admission_for(profile)
        with gateway.lock:
            require(not gateway.cancelled.is_set() and len(gateway.state['calls'])==count,'gateway call arrived during the model swap')
            gateway.admit_context=admission
        return {'from_profile':previous,'to_profile':profile,'gateway_calls':count,'unload':unloaded,'load':loaded}
    except BaseException:
        gateway.cancel()
        raise
    finally:
        if locked:gateway.serial.release()


def execute(work,gate):
    import threading
    import sqlite3
    from mirofish_native_gateway import Gateway,Limits,make_server
    from mirofish_native_model import SDKBridge,TemplateAdmission,MODEL_PLAN
    plan=MODEL_PLAN  # None: one model for the whole run, exactly as before TMF4
    work=Path(work);start=time.monotonic();deadline=start+2400;active=deadline-120
    original_jvm_home=os.environ.get('HOME')
    deadline=min(deadline,float(os.environ.get('MIROFISH_HOST_DEADLINE',deadline)));active=deadline-120
    allowed={key:os.environ[key] for key in ['PATH','TMPDIR','PYTHONDONTWRITEBYTECODE','XDG_CACHE_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','MIROFISH_RESOURCE_CAPSULE','MIROFISH_RESOURCE_KEY','MIROFISH_RESOURCE_NONCE','MIROFISH_RESOURCE_HOST','MIROFISH_HOST_DEADLINE','MIROFISH_IDENTITY_PS','MIROFISH_HOST_CONTROL_FD','MIROFISH_WORKER_COMMAND_FD','MIROFISH_WORKER_EVENT_FD'] if key in os.environ}
    os.environ.clear();os.environ.update(allowed)
    candidate,candidate_digest=verify_candidate(work)
    if plan is not None:
        require(candidate.get('model_plan')==plan and type(candidate.get('model_tokenizer_by_profile')) is dict and set(candidate['model_tokenizer_by_profile'])==set(plan.values()),'candidate manifest does not bind this model plan and its tokenizer pins')
    run_id=uuid.uuid4().hex;identifier='mf-native-'+run_id
    verify_launch(work,gate,candidate_digest,run_id)
    # A valid receipt does not itself provide filesystem confinement. Prove the
    # real original public source is unreadable before any service/model start.
    try:
        (PROJECT/'AGENTS.md').read_bytes()
    except PermissionError:
        pass
    else:
        raise NativeRefused('observer is not OS-confined from original source/state')
    from mirofish_native_children import client
    client().call('bind',run_id=run_id,candidate_sha256=candidate_digest)
    producer=work/'owned-source/producer';control=work/'owned-source/worker-entry'
    receipt={'outcome':OUTCOME,'candidate_sha256':candidate_digest,'run_id':run_id,'requested_checkpoint':'exact pinned GGUF SDK variant; see frozen model-source-manifest','api_owned_instance':identifier,'complete':False,'model_usage':'unknown until reported','events':[]}
    if plan is not None:receipt['model_plan']=dict(plan)
    sdk=worker=graph_group=server=gateway=None;server_thread=None;model_intent=False;loaded=baseline=None;secret=None
    model_state={'profile':plan['setup'] if plan is not None else None,'loaded':None}  # a plan's checkpoint behind the identifier
    aborted=threading.Event();stop_monitor=threading.Event();errors=[]
    def save(event,**data):
        receipt['events'].append({'event':event,'utc':datetime.now(timezone.utc).isoformat(),**data});atomic(work/'builder/native-run-receipt.json',receipt)
    def resource(pre=False):
        require(not aborted.is_set(),'resource monitor aborted')
        return resource_sample(pre)
    def monitor():
        while not stop_monitor.wait(1):
            if not monitor_resource_once(receipt,work/'builder/native-run-receipt.json',aborted,gateway,errors):return
    def remaining():
        require(not aborted.is_set() and time.monotonic()<active,'active whole-task deadline/resource refusal')
        return active
    def interrupted(signum,frame):
        aborted.set()
        if gateway is not None:gateway.cancel()
        raise NativeRefused('supervisor interrupted')
    def admission_for(profile):
        return TemplateAdmission(sdk,identifier,candidate['model_tokenizer_by_profile'][profile],embedding_native_semantics=True,profile=profile)
    def boundary(name,phase):
        # B1 (setup -> turns) and B2 (turns -> setup); nothing happens here with one model.
        if plan is None:return
        event=swap_model(sdk,gateway,identifier,plan[phase],model_state,(baseline or {}).get('embedding',{}).get('instanceReference'),admission_for,remaining())
        save('model_swap',boundary=name,**event)
        compatibility_probes(gateway,identifier,secret)
        save('post_swap_compatibility_probes_passed',boundary=name,profile=plan[phase],attempts=gateway.state['chat_attempts'],reserved_output=gateway.state['reserved_output'])
    old_handlers={sig:signal.signal(sig,interrupted) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        save('pre_resource',sample=resource(True))
        sdk=SDKBridge(work,active);baseline=sdk.call('baseline',15);save('baseline',observation=baseline)
        require(baseline.get('embeddingContext')==2048,'baseline embedding context changed')
        resource_thread=threading.Thread(target=monitor,daemon=True);resource_thread.start()
        model_intent=True
        if plan is None:loaded=sdk.call('load',min(180,active-time.monotonic()),identifier=identifier)
        else:loaded=model_state['loaded']=check_load_readback(sdk.call('load',min(180,active-time.monotonic()),identifier=identifier,profile=plan['setup']),identifier,plan['setup'])
        save('owned_model_readback',observation=loaded)
        admission=TemplateAdmission(sdk,identifier,candidate['model_tokenizer'],embedding_native_semantics=True) if plan is None else admission_for(plan['setup'])
        secret=uuid.uuid4().hex+uuid.uuid4().hex
        gateway=Gateway(Limits(identifier),secret,work/'builder/gateway-ledger.json',admission,resource_check=lambda:resource(False),deadline=deadline,calls_path=work/"run1/calls")
        def dispatch(route,body,deadline):
            if route=='/v1/embeddings':
                return 200,encoded(sdk.call('embed',deadline=deadline,body=body))
            return gateway._http(route,body,deadline)
        gateway.dispatch=dispatch
        server=make_server(gateway,candidate['proxy_port']);server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        compatibility_probes(gateway,identifier,secret)
        save('two_compatibility_probes_passed',attempts=gateway.state['chat_attempts'],reserved_output=gateway.state['reserved_output'])
        graph_path=work/'builder/graph';neo=RUNTIME/'neo4j-community-2026.10.1'
        from mirofish_native_children import start_graph
        graph_process,graph_group=start_graph();save('graph_owner',owner=graph_group.owner)
        end=min(active,time.monotonic()+45)
        while time.monotonic()<end:
            remaining();require(graph_process.poll() is None,'owned Neo4j exited during startup')
            try:
                with socket.create_connection(('127.0.0.1',candidate['graph_port']),timeout=.2):break
            except OSError:time.sleep(.1)
        else:raise NativeRefused('owned graph listen deadline')
        store,flow,review,paper_refused=observer_store(work,producer,run_id)
        worker=WorkerChannel(work,producer,control,f'http://127.0.0.1:{candidate["proxy_port"]}/v1',identifier,secret,candidate['graph_port'],active)
        gate_end=min(remaining(),time.monotonic()+30)
        embedding_raw=gateway.handle('/v1/embeddings',{'model':'ym-research-embedding','input':['Non-market native graph schema dimension prerequisite.']},'Bearer '+secret,deadline=gate_end)
        embedding_reply=decode(embedding_raw);embedding_rows=embedding_reply.get('data')
        require(isinstance(embedding_rows,list) and len(embedding_rows)==1,'single actual embedding prerequisite required')
        embedding=embedding_rows[0]['embedding']
        from mirofish_native_graph import vector_dimension
        vector_dimension(embedding)
        embedding_call=gateway.state['calls'][-1]
        save('actual_embedding_prerequisite',model='ym-research-embedding',dimension=768,cardinality=1,call_id=embedding_call['id'],response_sha256=hashlib.sha256(embedding_raw).hexdigest(),context=embedding_call.get('context'),producer_root=str(producer),run_id=run_id,budget_charged=True)
        graph_proof=worker.request('graph_gate',gate_end,{'embedding':embedding,'deadline':gate_end})
        require(graph_proof.get('qualified') is True and graph_proof.get('producer_root')==str(producer) and graph_proof.get('owned_instance')==identifier,'native graph prerequisite identity mismatch')
        save('graph_prerequisite_qualified',schema=graph_proof,embedding={'model':'ym-research-embedding','dimension':768,'cardinality':1,'call_id':embedding_call['id'],'response_sha256':hashlib.sha256(embedding_raw).hexdigest(),'context':embedding_call.get('context'),'budget_charged':True},producer_root=str(producer),run_id=run_id)
        prepared=worker.request('prepare',remaining());save('native_prepared',data=prepared)
        boundary('B1','turns')  # the worker waits for its start command; the simulation's turns go to the turns model
        before=gateway.state['chat_attempts'];native_call_start=len(gateway.state['calls'])
        started=worker.request('start',remaining());require(started['child_pgid']==worker.group.owner['pgid'],'native child escaped worker group')
        session=store.begin(prepared['simulation_id']);save('prospective_running_begin',session=session,native=started)
        terminal=worker.request('wait',remaining());require(terminal['child_exit']==0 and terminal['monitor_joined'] is True,'native terminal order failed')
        native_completed=qualify_native_chat_calls(gateway.state['calls'][native_call_start:])
        save('native_terminal',data=terminal,native_chat_attempts=gateway.state['chat_attempts']-before,completed_native_forward_calls=[row['id'] for row in native_completed])
        boundary('B2','setup')  # the worker waits for its report command; the report goes to the setup model
        report=worker.request('report',remaining());rid=store.finish(session,report['report_id']);receipt_body,features,now=store.validate(rid)
        save('fresh_report_validated',receipt_ref=rid,report=report,features=features,manifest=receipt_body['manifest'])
        proposal={'receipt_ref':rid,'instrument':'NVDA','action':'buy','reason':'Fixed reviewed local-paper qualification from exact captured native report.'}
        try:
            first=flow.submit(proposal,review)
            with sqlite3.connect(store.path) as db:count_before=db.execute('SELECT count(*) FROM acknowledgements').fetchone()[0]
            replay=flow.submit(proposal,review)
            with sqlite3.connect(store.path) as db:count_after=db.execute('SELECT count(*) FROM acknowledgements').fetchone()[0]
            require(count_before==1 and count_after==1 and all(row.get('attempted') is False and row.get('replay') is True for row in replay),'actual local paper replay sent again')
            save('local_paper_handoff',first=first,replay=replay,acknowledgements=count_after)
            receipt['paper_handoff']=True
        except paper_refused as exc:
            save('paper_refused',reason=str(exc));receipt['paper_handoff']=False
        receipt['complete']=True
    except BaseException as exc:
        receipt['failure']={'type':type(exc).__name__,'reason':str(exc)[:2000],'resource_sample':getattr(exc,'resource_sample',None),'graph_prerequisite_evidence':getattr(exc,'proof',None)};save('stopped',failure=receipt['failure'])
    finally:
        if gateway is not None:gateway.cancel()
        cleanup=[]
        if worker is not None:
            try:cleanup.append({'worker':worker.group.stop(deadline)})
            except BaseException as exc:cleanup.append({'worker_cleanup_failure':str(exc)})
        if graph_group is not None:
            try:cleanup.append({'graph':graph_group.stop(deadline)})
            except BaseException as exc:cleanup.append({'graph_cleanup_failure':str(exc)})
        if model_intent and sdk is not None:
            try:
                # Cleanup gets the reserved wall tail and only the exact owned identifier.
                if sdk.poisoned:
                    cleanup.append({'model_cleanup_uncertain':'prior SDK operation timed out; late server work remains unknown even if later lookup is absent'})
                    cleanup.append({'poisoned_sdk':sdk.group.stop(deadline)})
                    sdk=SDKBridge(work,deadline)
                sdk.deadline=deadline
                reference=((loaded if plan is None else model_state['loaded']) or {}).get('info',{}).get('instanceReference')
                embedding_reference=(baseline or {}).get('embedding',{}).get('instanceReference')
                owned_values={} if plan is None else {'profile':model_state['profile']}  # a plan's checkpoint behind the identifier now
                cleanup.append({'model':sdk.call('unload',min(45,max(.1,deadline-time.monotonic())),identifier=identifier,instanceReference=reference,embeddingReference=embedding_reference,**owned_values)})
            except BaseException as exc:cleanup.append({'model_cleanup_failure':str(exc)})
        stop_monitor.set()
        if sdk is not None:
            try:cleanup.append({'sdk':sdk.group.stop(deadline)})
            except BaseException as exc:cleanup.append({'sdk_cleanup_failure':str(exc)})
        if server is not None:server.shutdown();server.server_close()
        for sig,handler in old_handlers.items():signal.signal(sig,handler)
        receipt['cleanup']=cleanup;receipt['resource_failures']=errors;receipt['wall_seconds']=time.monotonic()-start
        receipt['cleanup_proven']=not any('failure' in key or 'uncertain' in key for item in cleanup for key in item)
        receipt['complete']=receipt['complete'] and receipt['cleanup_proven'] and receipt['wall_seconds']<=2400
        atomic(work/'builder/native-run-receipt.json',receipt)
    return receipt

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--copy-sources", type=Path)
    parser.add_argument("--offline-check", action="store_true")
    parser.add_argument("--prepare-candidate", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--work", type=Path)
    parser.add_argument("--gate", type=Path)
    args = parser.parse_args(argv)
    if args.worker:
        native_worker(); return 0
    if args.prepare_candidate:
        print(json.dumps(prepare_candidate(args.prepare_candidate))); return 0
    if args.execute:
        require(args.work is not None and args.gate is not None, "owned work and lead gate required")
        result=execute(args.work,args.gate);print(json.dumps({"complete":result["complete"],"run_id":result["run_id"]}));return 0 if result["complete"] else 1
    if args.copy_sources:
        producer, entry, manifest = copy_sources(args.copy_sources)
        print(json.dumps({"producer": str(producer), "entry": str(entry), "files": len(manifest["files"])})); return 0
    if args.offline_check:
        require(PYTHON.exists() and (RUNTIME / "requirements-py311.lock").is_file(), "qualified setup missing")
        print(json.dumps({"outcome": OUTCOME, "build_only": True, "runtime": str(PYTHON), "no_service_or_inference": True})); return 0
    parser.error("explicit build action or separately gated execute required")


if __name__ == "__main__":
    raise SystemExit(main())
