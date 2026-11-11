"""Manual trusted-local artifact observation. No backend execution or remote attestation.

Control objects are supplied by reviewed operator Python code, never proposal JSON.
The host owner and approved producer/store writers are the explicit trust boundary.
"""
from __future__ import annotations
import base64
import contextlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from recovery_state import Refused, canonical

MAX_FILE = 1024 * 1024
MAX_TOTAL = 16 * MAX_FILE
MAX_REPORTS = 128
EXTRACTOR = Path(__file__).with_name("mirofish_signal_extractor.py")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _reject(value):
    raise Refused("nonfinite JSON")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("duplicate JSON key")
        result[key] = value
    return result


def decode(raw):
    try:
        result = json.loads(raw, parse_constant=_reject, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as exc:
        raise Refused("malformed artifact JSON") from exc
    if not isinstance(result, dict):
        raise Refused("artifact object required")
    return result


def _absolute(value):
    p = Path(value)
    if not p.is_absolute() or ".." in p.parts:
        raise Refused("explicit absolute root required")
    if any(q.is_symlink() for q in (p, *p.parents)):
        raise Refused("symlink path refused")
    return p


def read_file(root, relative, *, missing=False):
    root = _absolute(root)
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise Refused("artifact path escape")
    p = _absolute(root / rel)
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if missing and not os.path.lexists(p):
            return None
        raise Refused("artifact missing")
    except OSError as exc:
        raise Refused("artifact unavailable") from exc
    try:
        import stat
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            raise Refused("bounded regular artifact required")
        raw = b""
        while len(raw) <= MAX_FILE:
            chunk = os.read(fd, min(65536, MAX_FILE + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > MAX_FILE:
            raise Refused("artifact oversized")
        return raw
    finally:
        os.close(fd)


def _id(kind, value):
    if not isinstance(value, str) or not re.fullmatch(kind + r"_[0-9a-f]{12}", value):
        raise Refused("invalid " + kind + " identity")
    return value


def _native(value):
    try:
        if not isinstance(value, str) or not value:
            raise ValueError()
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is not None:
            raise ValueError()
        return stamp
    except (ValueError, TypeError, OverflowError) as exc:
        raise Refused("naive producer-local timestamp required; not UTC") from exc


@dataclass(frozen=True)
class ProducerProfile:
    producer_id: str
    root: str
    source_pins: tuple[tuple[str, str], ...]
    extractor_sha256: str
    research_only: bool = True
    max_age_seconds: int = 600
    native_clock: str = "declared-single-producer-naive-local"

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", self.producer_id):
            raise Refused("canonical producer ID required")
        _absolute(self.root)
        if type(self.research_only) is not bool or type(self.max_age_seconds) is not int or not 1 <= self.max_age_seconds <= 3600:
            raise Refused("explicit bounded producer policy required")
        if self.native_clock != "declared-single-producer-naive-local":
            raise Refused("unsupported native clock profile")
        if type(self.source_pins) is not tuple or not self.source_pins:
            raise Refused("reviewed immutable source pins required")
        names = []
        for item in self.source_pins:
            if type(item) is not tuple or len(item) != 2:
                raise Refused("immutable source pin required")
            name, digest = item
            if not name.startswith("backend/app/") or ".." in Path(name).parts or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise Refused("invalid producer source pin")
            names.append(name)
        if len(set(names)) != len(names) or len(names) > 16 or not re.fullmatch(r"[0-9a-f]{64}", self.extractor_sha256):
            raise Refused("bounded unique source pins required")


@dataclass(frozen=True)
class PaperBinding:
    account: str
    journal_path: str
    registry: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", self.account):
            raise Refused("canonical account required")
        _absolute(self.journal_path)
        if type(self.registry) is not tuple or not self.registry or len(set(self.registry)) != len(self.registry):
            raise Refused("immutable canonical registry required")
        for venue, instrument in self.registry:
            if venue not in {"alpaca", "coinbase"} or not isinstance(instrument, str) or not re.fullmatch(r"[A-Z0-9.-]{1,30}", instrument):
                raise Refused("unsupported canonical venue/instrument")


class AuthorityStore:
    """One pinned store/producer/account/Journal/registry. No JSON authority import."""
    def __init__(self, root, profile, binding, *, clock):
        if type(profile) is not ProducerProfile or type(binding) is not PaperBinding or not callable(clock):
            raise Refused("reviewed profile, binding and clock required")
        self.root = _absolute(root)
        self.profile, self.binding, self.clock = profile, binding, clock
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "authority.sqlite3"
        _absolute(self.path)
        self._frozen = self._configuration()
        self._seen = None
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                raise Refused("authority initialization raced; explicit reopen required")
            try:
                with contextlib.closing(sqlite3.connect(self.path)) as c, c:
                    c.executescript("CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT NOT NULL); CREATE TABLE sessions(id TEXT PRIMARY KEY,body TEXT NOT NULL,digest TEXT NOT NULL); CREATE TABLE receipts(id TEXT PRIMARY KEY,artifact_digest TEXT NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL); CREATE TABLE claims(id TEXT PRIMARY KEY,leg_digest TEXT NOT NULL,status TEXT NOT NULL,result TEXT); CREATE TABLE acknowledgements(id TEXT PRIMARY KEY,order_body TEXT NOT NULL);")
                    c.execute("INSERT INTO meta VALUES('configuration',?)", (self._frozen,))
                    c.execute("INSERT INTO meta VALUES('clock','')")
            except Exception as exc:
                raise Refused("incomplete authority initialization; manual recovery required") from exc
        with self._tx():
            pass

    def _configuration(self):
        return canonical({"schema": 1, "store_root": str(self.root), "profile": asdict(self.profile), "paper": asdict(self.binding)})

    @contextlib.contextmanager
    def _tx(self):
        _absolute(self.path)
        c = None
        try:
            if self._configuration() != self._frozen:
                raise Refused("frozen authority object changed")
            c = sqlite3.connect(self.path, timeout=5)
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT v FROM meta WHERE k='configuration'").fetchone()
            if row != (self._frozen,):
                raise Refused("authority schema/configuration drift")
            yield c
            if self._configuration() != self._frozen:
                raise Refused("frozen authority changed during operation")
            c.commit()
        except (sqlite3.Error, OSError) as exc:
            if c is not None:
                c.rollback()
            raise Refused("authority unavailable/corrupt; no automatic reset") from exc
        except BaseException:
            if c is not None:
                c.rollback()
                # Refused late guards must also remember their observed clock.
                if self._seen is not None:
                    c.execute("BEGIN IMMEDIATE")
                    if c.execute("SELECT v FROM meta WHERE k='configuration'").fetchone() != (self._frozen,):
                        raise Refused("authority changed while persisting clock high-water")
                    floor = c.execute("SELECT v FROM meta WHERE k='clock'").fetchone()
                    if floor is None:
                        raise Refused("clock high-water missing")
                    if not floor[0] or self._seen > datetime.fromisoformat(floor[0]):
                        c.execute("UPDATE meta SET v=? WHERE k='clock'", (self._seen.isoformat(),))
                    c.commit()
            raise
        finally:
            if c is not None:
                c.close()

    def _clock(self, c):
        stamp = self.clock()
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(stamp):
            raise Refused("trusted aware UTC clock required")
        stamp = stamp.astimezone(timezone.utc)
        row = c.execute("SELECT v FROM meta WHERE k='clock'").fetchone()
        if row is None:
            raise Refused("clock high-water missing")
        if (row[0] and stamp < datetime.fromisoformat(row[0])) or (self._seen is not None and stamp < self._seen):
            raise Refused("trusted clock regressed")
        self._seen = stamp
        c.execute("UPDATE meta SET v=? WHERE k='clock'", (stamp.isoformat(),))
        return stamp

    def now(self):
        with self._tx() as c:
            return self._clock(c)

    def _pins(self):
        files = {}
        for rel, expected in self.profile.source_pins:
            raw = read_file(self.profile.root, rel)
            if sha(raw) != expected:
                raise Refused("producer source pin changed")
            files[rel] = raw
        raw = read_file(EXTRACTOR.parent, EXTRACTOR.name)
        if sha(raw) != self.profile.extractor_sha256:
            raise Refused("extractor pin changed")
        files["@extractor"] = raw
        return files

    def _pass(self, sim_id, report_id=None):
        sim_id = _id("sim", sim_id)
        sim_path = "backend/uploads/simulations/" + sim_id
        files = self._pins()
        files[sim_path + "/state.json"] = read_file(self.profile.root, sim_path + "/state.json")
        sim = decode(files[sim_path + "/state.json"])
        project_id = _id("proj", sim.get("project_id"))
        project_path = "backend/uploads/projects/" + project_id + "/project.json"
        files[project_path] = read_file(self.profile.root, project_path)
        files[sim_path + "/run_state.json"] = read_file(self.profile.root, sim_path + "/run_state.json")
        project = decode(files[project_path])
        run = decode(files[sim_path + "/run_state.json"])
        if sim.get("simulation_id") != sim_id or run.get("simulation_id") != sim_id or project.get("project_id") != project_id:
            raise Refused("directory/metadata identity mismatch")
        graph = sim.get("graph_id")
        if not isinstance(graph, str) or not graph or graph != project.get("graph_id"):
            raise Refused("project/simulation graph mismatch")
        if not _native(project.get("created_at")) <= _native(sim.get("created_at")) <= _native(run.get("started_at")):
            raise Refused("native project/sim/run chronology")
        info = {"sim": sim, "project": project, "run": run}
        if report_id is not None:
            report_id = _id("report", report_id)
            report_path = "backend/uploads/reports/" + report_id
            files[report_path + "/meta.json"] = read_file(self.profile.root, report_path + "/meta.json")
            files[report_path + "/full_report.md"] = read_file(self.profile.root, report_path + "/full_report.md")
            meta = decode(files[report_path + "/meta.json"])
            if meta.get("report_id") != report_id or meta.get("simulation_id") != sim_id or meta.get("graph_id") != graph:
                raise Refused("report simulation/graph identity mismatch")
            body = meta.get("markdown_content")
            if not isinstance(body, str) or not body.strip() or body.encode("utf8") != files[report_path + "/full_report.md"]:
                raise Refused("partial/split report publication")
            if run.get("runner_status") != "completed" or meta.get("status") != "completed":
                raise Refused("terminal runner and completed report required")
            if not _native(run.get("started_at")) <= _native(run.get("completed_at")) <= _native(meta.get("created_at")) <= _native(meta.get("completed_at")):
                raise Refused("native run/report chronology")
            outline = meta.get("outline")
            if outline is not None and (not isinstance(outline, dict) or not isinstance(outline.get("summary", ""), str)):
                raise Refused("bound report summary malformed")
            info["report"] = meta
        if len(files) > 24 or sum(map(len, files.values())) > MAX_TOTAL:
            raise Refused("capture exceeds byte/count bounds")
        return files, info

    def _baseline(self):
        parent = _absolute(Path(self.profile.root) / "backend/uploads/reports")
        if not parent.is_dir():
            raise Refused("reports directory required")
        entries = sorted(parent.iterdir())
        if len(entries) > MAX_REPORTS:
            raise Refused("report baseline count exceeded")
        ids, bodies, snapshots = [], set(), {}
        total = 0
        for item in entries:
            _absolute(item)
            if not item.is_dir():
                raise Refused("unexpected report baseline entry")
            report_id = _id("report", item.name)
            ids.append(report_id)
            for name in ("meta.json", "full_report.md"):
                rel = "backend/uploads/reports/" + report_id + "/" + name
                raw = read_file(self.profile.root, rel, missing=True)
                snapshots[rel] = None if raw is None else sha(raw)
                if raw is not None:
                    total += len(raw)
                    if name == "full_report.md":
                        bodies.add(sha(raw))
                    else:
                        meta = decode(raw)
                        text = meta.get("markdown_content")
                        if isinstance(text, str):
                            bodies.add(sha(text.encode("utf8")))
            if total > MAX_TOTAL:
                raise Refused("report baseline byte bound exceeded")
        return {"ids": ids, "bodies": sorted(bodies), "snapshots": snapshots}

    def _stable(self, sim_id, report_id=None, baseline=False):
        first, info = self._pass(sim_id, report_id)
        old = self._baseline() if baseline else None
        second, again = self._pass(sim_id, report_id)
        new = self._baseline() if baseline else None
        if first != second or old != new:
            raise Refused("unstable artifact/source read-recheck")
        return first, again, new

    def begin(self, sim_id):
        if self.profile.research_only:
            raise Refused("BLOCKED_HISTORICAL research-only producer profile")
        begin = self.now()
        files, info, baseline = self._stable(sim_id, baseline=True)
        if info["run"].get("runner_status") != "running":
            raise Refused("begin requires already RUNNING; no token learned at completion")
        self.now()
        session = {"simulation_id": sim_id, "project_id": info["sim"]["project_id"], "graph_id": info["sim"]["graph_id"], "started_at": info["run"]["started_at"], "sim_created_at": info["sim"]["created_at"], "project_hash": sha(files["backend/uploads/projects/" + info["sim"]["project_id"] + "/project.json"]), "begin": begin.isoformat(), "baseline": baseline, "configuration": sha(self._frozen.encode())}
        body = canonical(session)
        sid = "session-" + uuid.uuid4().hex
        with self._tx() as c:
            c.execute("INSERT INTO sessions VALUES(?,?,?)", (sid, body, sha(body.encode())))
        return sid

    def _session(self, sid):
        with self._tx() as c:
            row = c.execute("SELECT body,digest FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row or sha(row[0].encode()) != row[1]:
            raise Refused("missing/corrupt trusted capture session")
        return decode(row[0])

    def _fresh(self, receipt, now):
        try:
            begin = datetime.fromisoformat(receipt["begin"])
            end = datetime.fromisoformat(receipt["observed_complete"])
            if not begin.tzinfo or not end.tzinfo or not begin <= end <= now or (now-begin).total_seconds() >= self.profile.max_age_seconds:
                raise ValueError()
        except (ValueError, TypeError, KeyError) as exc:
            raise Refused("expired/future/invalid trusted observation interval") from exc

    def finish(self, sid, report_id):
        session = self._session(sid)
        self.now()
        files, info, _ = self._stable(session["simulation_id"], report_id)
        now = self.now()
        if session["configuration"] != sha(self._frozen.encode()):
            raise Refused("capture authority changed")
        project_id = info["sim"]["project_id"]
        if (project_id, info["sim"]["graph_id"], info["run"]["started_at"], info["sim"]["created_at"]) != (session["project_id"], session["graph_id"], session["started_at"], session["sim_created_at"]) or sha(files["backend/uploads/projects/"+project_id+"/project.json"]) != session["project_hash"]:
            raise Refused("capture run/project/graph token changed")
        body_hash = sha(files["backend/uploads/reports/"+report_id+"/full_report.md"])
        if report_id in session["baseline"]["ids"] or body_hash in session["baseline"]["bodies"]:
            raise Refused("baseline/orphan report ID or content cannot become fresh")
        identity = {"producer": self.profile.producer_id, "project": project_id, "simulation": session["simulation_id"], "report": report_id}
        rid = "receipt-" + sha(canonical(identity).encode())
        snapshots = {key: base64.b64encode(raw).decode("ascii") for key, raw in files.items()}
        manifest = {"identity": identity, "graph_id": info["sim"]["graph_id"], "run_started_at": info["run"]["started_at"], "files": {key: sha(raw) for key, raw in files.items()}, "configuration": session["configuration"]}
        receipt = {"id": rid, "manifest": manifest, "snapshots": snapshots, "begin": session["begin"], "observed_complete": now.isoformat(), "proof": "approved-local-observation; native-local-clock only; not physical-run/backend/event attestation"}
        self._fresh(receipt, now)
        artifact_digest = sha(canonical(manifest).encode())
        body = canonical(receipt)
        with self._tx() as c:
            old = c.execute("SELECT artifact_digest,body,digest FROM receipts WHERE id=?", (rid,)).fetchone()
            if old:
                if old[0] != artifact_digest or sha(old[1].encode()) != old[2]:
                    raise Refused("semantic report evidence changed")
                existing = decode(old[1])
                self._fresh(existing, now)
                return existing["id"]
            c.execute("INSERT INTO receipts VALUES(?,?,?,?)", (rid, artifact_digest, body, sha(body.encode())))
        return rid

    def validate(self, rid):
        with self._tx() as c:
            return self._validate_locked(c, rid)

    def _validate_locked(self, c, rid):
        now = self._clock(c)
        row = c.execute("SELECT body,digest,artifact_digest FROM receipts WHERE id=?", (rid,)).fetchone()
        if not row or sha(row[0].encode()) != row[1]:
            raise Refused("unverified/foreign/corrupt trusted receipt")
        receipt = decode(row[0])
        self._fresh(receipt, now)
        manifest = receipt["manifest"]
        if manifest.get("configuration") != sha(self._frozen.encode()) or sha(canonical(manifest).encode()) != row[2]:
            raise Refused("receipt configuration/manifest drift")
        identity = manifest["identity"]
        files, info, _ = self._stable(identity["simulation"], identity["report"])
        if {key: sha(raw) for key, raw in files.items()} != manifest["files"] or {key: base64.b64encode(raw).decode("ascii") for key, raw in files.items()} != receipt["snapshots"]:
            raise Refused("receipt snapshot/source changed")
        from mirofish_signal_extractor import derive_report_features
        from mirofish_intel_archive import evaluate
        meta = info["report"]
        summary = (meta.get("outline") or {}).get("summary", "")
        features = derive_report_features(meta["markdown_content"], summary)
        now = self._clock(c)
        self._fresh(receipt, now)
        signal = {**features, "as_of": receipt["observed_complete"]}
        if not evaluate(signal, now=now)["usable_for_fv"]:
            raise Refused("derived report not usable evidence")
        return receipt, features, now

    def claim(self, decision, leg):
        digest = sha(canonical(leg).encode())
        with self._tx() as c:
            row = c.execute("SELECT leg_digest,status,result FROM claims WHERE id=?", (decision,)).fetchone()
            if row:
                if row[0] != digest:
                    raise Refused("semantic decision review/evidence/amount changed")
                if row[1] != "DONE":
                    raise Refused("one-shot claim pending; manual recovery, no retry")
                return json.loads(row[2])
            c.execute("INSERT INTO claims VALUES(?,?,?,NULL)", (decision, digest, "PENDING"))
        return None

    def complete_claim(self, decision, result):
        with self._tx() as c:
            if c.execute("UPDATE claims SET status='DONE',result=? WHERE id=? AND status='PENDING'", (canonical(result), decision)).rowcount != 1:
                raise Refused("one-shot completion mismatch")
