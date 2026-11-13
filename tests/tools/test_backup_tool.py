"""Tests for tools/backup.py (roadmap 5.1: backups of the trader's memory).

Every test builds its own small repository in a temporary folder, with its own destination. Nothing here
opens, copies or touches memory/, artifacts/ or journal/ of the real repository.

Seams used from the tool: main(argv), verify_backup(folder), free_bytes(path), utcnow(), copy_file(...),
STAMP_RE."""
import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "backup.py"
_spec = importlib.util.spec_from_file_location("valedia_backup_tool", SCRIPT)
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)

APP_ID = 0x56414C44
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


# --------------------------------------------------------------------------------------- builders
def make_db(path, *, mode="delete", rows=40, pad=100, triggers=False, app_id=0, user_version=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA journal_mode={mode}")
    if app_id:
        conn.execute(f"PRAGMA application_id={app_id}")
    if user_version:
        conn.execute(f"PRAGMA user_version={user_version}")
    conn.execute("CREATE TABLE ledger(seq INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
    conn.execute("CREATE TABLE mirror(seq INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE meta(last INTEGER NOT NULL)")
    conn.execute("INSERT INTO meta VALUES (?)", (rows,))
    conn.executemany("INSERT INTO ledger VALUES (?, ?)", [(i, b"x" * pad) for i in range(1, rows + 1)])
    conn.executemany("INSERT INTO mirror VALUES (?)", [(i,) for i in range(1, rows + 1)])
    if triggers:
        conn.execute("CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger "
                     "BEGIN SELECT RAISE(ABORT, 'append only'); END")
        conn.execute("CREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger "
                     "BEGIN SELECT RAISE(ABORT, 'append only'); END")
    conn.commit()
    conn.close()


def write_file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


class Repo:
    def __init__(self, base):
        self.base = Path(base)
        self.root = self.base / "repo"
        self.dest = self.base / "dest"
        self.writer = None

    def close(self):
        if self.writer is not None:
            self.writer.close()
            self.writer = None


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path)
    root = r.root
    make_db(root / "memory" / "rollback.sqlite", mode="delete", rows=60)
    make_db(root / "memory" / "triggers.sqlite", mode="delete", rows=30, triggers=True,
            app_id=APP_ID, user_version=7)
    make_db(root / "memory" / "wal_quiet.sqlite", mode="wal", rows=45)          # closed cleanly: no sidecars
    make_db(root / "memory" / "wal_open.sqlite", mode="wal", rows=40)
    r.writer = sqlite3.connect(root / "memory" / "wal_open.sqlite")             # open writer: -wal and -shm exist
    r.writer.execute("PRAGMA wal_autocheckpoint=0")
    r.writer.executemany("INSERT INTO ledger VALUES (?, ?)", [(i, b"w" * 100) for i in range(41, 66)])
    r.writer.executemany("INSERT INTO mirror VALUES (?)", [(i,) for i in range(41, 66)])
    r.writer.execute("UPDATE meta SET last = 65")
    r.writer.commit()
    make_db(root / "artifacts" / "pick_grading" / "day1.sqlite", rows=20)
    make_db(root / "artifacts" / "pick_grading" / "day2.db", rows=22)
    make_db(root / "artifacts" / "blobs" / "store.dat", rows=10)                # a database found by content only
    write_file(root / "artifacts" / "readme.txt", "not a database")
    write_file(root / "journal" / "2026-11-12.jsonl", '{"a":1}\n{"a":2}\n')
    write_file(root / "journal" / "sub" / "deeper" / "x.jsonl", '{"b":1}\n')
    write_file(root / "config" / "risk.json", '{"stop":0.25}')
    write_file(root / "config" / "webull_live.json", '{"owner_confirmation":"yes"}')
    write_file(root / "src" / "not_backed_up.py", "print(1)\n")
    yield r
    r.close()


@pytest.fixture
def small_repo(tmp_path):
    r = Repo(tmp_path)
    make_db(r.root / "memory" / "a.sqlite", rows=20)
    write_file(r.root / "journal" / "j.jsonl", '{"j":1}\n')
    write_file(r.root / "config" / "c.json", "{}")
    yield r
    r.close()


# --------------------------------------------------------------------------------------- helpers
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_state(root):
    """Every folder (mtime), every file (size, mtime, bytes). A -shm file is shared memory of a live reader
    or writer: for it only the existence is compared (its bytes are volatile index data)."""
    root = Path(root)
    state = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        state[str(d.relative_to(root)) + "/"] = ("dir", d.stat().st_mtime_ns)
        for name in filenames:
            p = d / name
            st = p.stat()
            if name.endswith("-shm"):
                state[str(p.relative_to(root))] = ("shm-exists",)
            else:
                state[str(p.relative_to(root))] = ("file", st.st_size, st.st_mtime_ns, sha256(p))
    return state


def run(repo, *extra, dest=None, root=None):
    argv = ["run", "--root", str(root or repo.root), "--dest", str(dest or repo.dest), *extra]
    return backup.main(argv)


def finals(dest):
    dest = Path(dest)
    if not dest.exists():
        return []
    return sorted(p for p in dest.iterdir() if backup.STAMP_RE.match(p.name) and p.is_dir())


def newest(dest):
    return finals(dest)[-1]


def manifest_of(folder):
    return json.loads((Path(folder) / "MANIFEST.json").read_text())


def entry(manifest, rel):
    return next(e for e in manifest["files"] if e["path"] == rel)


def failed_paths(manifest):
    return {f["path"] for f in manifest["failures"]}


def hidden_leftovers(dest):
    dest = Path(dest)
    if not dest.exists():
        return []
    return sorted(p.name for p in dest.iterdir() if p.name.startswith((".tmp-", ".scratch-", ".trash-")))


def open_copy(path):
    """Open a backup copy the way the tool itself does: read-only, immutable (never writes beside it)."""
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def counts(db_path):
    with contextlib.closing(open_copy(db_path)) as c:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ledger", "mirror"))


# --------------------------------------------------------------------------------------- outcome: a backup
def test_backup_holds_every_database_journal_config_and_a_manifest(repo, capsys):
    code = run(repo)
    assert code == 0, capsys.readouterr().err
    folder = newest(repo.dest)
    assert backup.STAMP_RE.match(folder.name)
    assert dt.datetime.strptime(folder.name, "%Y%m%dT%H%M%SZ")
    m = manifest_of(folder)
    assert m["status"] == "complete" and m["failures"] == []
    paths = {e["path"] for e in m["files"]}
    assert paths == {
        "memory/rollback.sqlite", "memory/triggers.sqlite", "memory/wal_quiet.sqlite", "memory/wal_open.sqlite",
        "artifacts/pick_grading/day1.sqlite", "artifacts/pick_grading/day2.db", "artifacts/blobs/store.dat",
        "journal/2026-11-12.jsonl", "journal/sub/deeper/x.jsonl",
        "config/risk.json", "config/webull_live.json",
    }
    for e in m["files"]:
        copy = folder / e["path"]
        assert copy.is_file() and copy.stat().st_size == e["size"] and sha256(copy) == e["sha256"]
    for rel in paths:
        if entry(m, rel)["kind"] == "sqlite":
            assert entry(m, rel)["integrity"] == "ok"
    assert entry(m, "memory/rollback.sqlite")["tables"] == {"ledger": 60, "mirror": 60, "meta": 1}
    assert entry(m, "memory/wal_open.sqlite")["tables"]["ledger"] == 65     # the un-checkpointed rows are in
    assert counts(folder / "memory" / "wal_open.sqlite") == (65, 65)
    assert counts(folder / "memory" / "wal_quiet.sqlite") == (45, 45)
    assert (folder / "journal" / "sub" / "deeper" / "x.jsonl").read_bytes() == b'{"b":1}\n'
    assert not (folder / "src").exists() and not (folder / "artifacts" / "readme.txt").exists()
    assert not any(p.name.endswith(("-wal", "-shm", "-journal")) for p in folder.rglob("*"))
    assert hidden_leftovers(repo.dest) == []


def test_triggers_application_id_and_user_version_come_out_unchanged(repo):
    assert run(repo) == 0
    folder = newest(repo.dest)
    src, cpy = repo.root / "memory" / "triggers.sqlite", folder / "memory" / "triggers.sqlite"
    with contextlib.closing(sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)) as a, \
            contextlib.closing(open_copy(cpy)) as b:
        assert a.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name").fetchall() == \
            b.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name").fetchall()
        assert a.execute("PRAGMA application_id").fetchone() == b.execute("PRAGMA application_id").fetchone() \
            == (APP_ID,)
        assert b.execute("PRAGMA user_version").fetchone() == (7,)
        assert len([r for r in b.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]) == 2
    m = manifest_of(folder)
    assert entry(m, "memory/triggers.sqlite")["application_id"] == APP_ID
    assert entry(m, "memory/triggers.sqlite")["user_version"] == 7
    # the append-only trigger still bites in the copy (a private working copy, not the backup itself)
    work = repo.base / "work.sqlite"
    work.write_bytes(cpy.read_bytes())
    with contextlib.closing(sqlite3.connect(work)) as c:
        with pytest.raises(sqlite3.DatabaseError, match="append only"):
            c.execute("UPDATE ledger SET payload = x'00' WHERE seq = 1")


def _start_writer(path, stop, log, pad=2000):
    def work():
        conn = sqlite3.connect(path, timeout=60, isolation_level=None)
        n = conn.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
        try:
            while not stop.is_set():
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    n += 1
                    conn.execute("INSERT INTO ledger VALUES (?, ?)", (n, b"y" * pad))
                    conn.execute("INSERT INTO mirror VALUES (?)", (n,))
                    conn.execute("UPDATE meta SET last = ?", (n,))
                    conn.execute("COMMIT")
                    log.append(n)
                except sqlite3.Error as exc:                  # a busy moment is not a failure of the writer
                    log.append(f"error: {exc}")
                    with contextlib.suppress(sqlite3.Error):
                        conn.execute("ROLLBACK")
                    n = conn.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
        finally:
            conn.close()
    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_database_written_during_the_backup_comes_out_consistent(small_repo, mode, capsys):
    live = small_repo.root / "memory" / "orderbook_loop.sqlite"
    make_db(live, mode=mode, rows=12000, pad=4000)                  # about 50 MB: the copy takes a while
    stop, log = threading.Event(), []
    t = _start_writer(live, stop, log)
    try:
        deadline = time.monotonic() + 30
        while len([x for x in log if isinstance(x, int)]) < 20 and time.monotonic() < deadline:
            time.sleep(0.01)
        before = len(log)
        code = run(small_repo)
        during = len(log) - before
    finally:
        stop.set()
        t.join(timeout=60)
    assert code == 0, capsys.readouterr().err
    assert not [x for x in log if isinstance(x, str) and "locked" not in x and "busy" not in x]
    folder = newest(small_repo.dest)
    cpy = folder / "memory" / "orderbook_loop.sqlite"
    e = entry(manifest_of(folder), "memory/orderbook_loop.sqlite")
    assert e["integrity"] == "ok"
    with contextlib.closing(open_copy(cpy)) as c:
        n_ledger = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        n_mirror = c.execute("SELECT COUNT(*) FROM mirror").fetchone()[0]
        top, last = c.execute("SELECT MAX(seq) FROM ledger").fetchone()[0], c.execute("SELECT last FROM meta").fetchone()[0]
        assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert n_ledger == n_mirror == top == last        # one snapshot: the three agree
    assert n_ledger >= 12000 + 20
    if mode == "wal":
        assert during >= 1                            # the writer really did commit while the copy ran


# --------------------------------------------------------------------------------------- outcome: sources untouched
def test_sources_unchanged_bytes_times_and_no_new_files(repo):
    before = tree_state(repo.root)
    assert "memory/wal_open.sqlite-wal" in before and "memory/wal_open.sqlite-shm" in before
    assert not any(k.startswith("memory/wal_quiet.sqlite-") for k in before)
    assert run(repo) == 0
    after = tree_state(repo.root)
    assert after.keys() == before.keys(), set(after) ^ set(before)      # no file created or removed
    changed = [k for k in before if before[k] != after[k]]
    assert changed == [], changed                                         # bytes and times, folders too
    assert not any(k.startswith("memory/wal_quiet.sqlite-") for k in after)
    assert not [k for k in after if k.endswith(("-journal", "-wal", "-shm")) and k not in before]


def test_a_second_run_leaves_sources_unchanged_too(repo):
    assert run(repo) == 0
    before = tree_state(repo.root)
    assert run(repo) == 0
    assert tree_state(repo.root) == before


# --------------------------------------------------------------------------------------- outcome: rename at the end
def _spawn(root, dest):
    return subprocess.Popen([sys.executable, str(SCRIPT), "run", "--root", str(root), "--dest", str(dest)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _assert_no_half_backup(dest, checked):
    """Every entry under a final name is a whole backup that verifies (each is verified once)."""
    dest = Path(dest)
    for p in dest.iterdir():
        if p.name.startswith("."):
            continue
        assert p.is_dir() and backup.STAMP_RE.match(p.name), f"stray entry under a final name: {p.name}"
        assert (p / "MANIFEST.json").is_file(), f"final name without manifest: {p.name}"
        if p.name not in checked:
            code, lines = backup.verify_backup(p)
            assert code == 0, (p.name, lines)
            checked.add(p.name)


@pytest.mark.timeout(280)
def test_kill_at_random_points_never_leaves_a_half_backup_and_the_next_run_works(tmp_path):
    root, dest = tmp_path / "repo", tmp_path / "dest"
    for i in range(6):
        make_db(root / "memory" / f"db{i}.sqlite", mode="wal" if i % 2 else "delete", rows=1500, pad=3000)
    for i in range(150):
        write_file(root / "journal" / f"day{i:03}.jsonl", ('{"n":%d}\n' % i) * 40)
    write_file(root / "config" / "risk.json", "{}")
    t0 = time.monotonic()
    p = _spawn(root, dest)
    assert p.wait(timeout=120) == 0
    duration = time.monotonic() - t0
    rng = random.Random(27)
    checked = set()
    kills, with_temp, sigterm = 0, 0, 0
    for k in range(40):
        p = _spawn(root, dest)
        time.sleep(rng.uniform(0.02, 1.15) * duration)
        sig = signal.SIGTERM if k % 4 == 3 else signal.SIGKILL
        if p.poll() is None:
            os.kill(p.pid, sig)
            sigterm += sig == signal.SIGTERM
        p.wait(timeout=60)
        kills += 1
        with_temp += bool(hidden_leftovers(dest)) if dest.exists() else 0
        if dest.exists():
            _assert_no_half_backup(dest, checked)
        code = backup.main(["run", "--root", str(root), "--dest", str(dest)])         # the next run works
        assert code == 0
        assert hidden_leftovers(dest) == []
        _assert_no_half_backup(dest, checked)
    print(f"kills={kills} sigterm={sigterm} killed_with_a_temp_folder_present={with_temp} run_seconds={duration:.2f}")
    assert kills >= 30 and sigterm >= 5
    assert with_temp >= 3, f"only {with_temp} of {kills} kills landed mid-copy; the test is not exercising the window"
    assert len(finals(dest)) == 14


def test_a_run_that_fails_midway_says_what_failed_and_leaves_nothing(repo, monkeypatch, capsys):
    real = backup.copy_file
    calls = []

    def flaky(src, dst, *a, **k):
        calls.append(src)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device", str(dst))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(backup, "copy_file", flaky)
    code = run(repo)
    err = capsys.readouterr().err
    assert code == 2
    assert "No space left on device" in err and "backup" in err.lower()
    assert finals(repo.dest) == [] and hidden_leftovers(repo.dest) == []


def test_next_run_clears_what_a_killed_run_left_and_only_that(repo):
    repo.dest.mkdir()
    for name in (".tmp-20250101T000000Z-1", ".scratch-20250101T000000Z-1", ".trash-20240101T000000Z-1"):
        write_file(repo.dest / name / "partial.bin", b"x")
    write_file(repo.dest / ".keepme" / "f", b"x")
    write_file(repo.dest / "notes.txt", b"mine")
    assert run(repo) == 0
    assert hidden_leftovers(repo.dest) == []
    assert (repo.dest / ".keepme" / "f").exists() and (repo.dest / "notes.txt").read_bytes() == b"mine"


def test_two_runs_in_one_second_get_two_distinct_final_folders(small_repo):
    assert run(small_repo) == 0 and run(small_repo) == 0
    assert len(finals(small_repo.dest)) == 2


def test_a_second_run_while_one_is_running_is_refused(small_repo, capsys):
    small_repo.dest.mkdir()
    with open(small_repo.dest / ".backup.lock", "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(small_repo) == 2
    assert "another backup" in capsys.readouterr().err
    assert finals(small_repo.dest) == []


# --------------------------------------------------------------------------------------- outcome: retention
class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, days=1):
        self.t += dt.timedelta(days=days)


def test_retention_keeps_the_newest_14(small_repo, monkeypatch):
    clock = Clock(dt.datetime(2026, 1, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    stamps = []
    for _ in range(20):
        assert run(small_repo) == 0
        stamps.append(newest(small_repo.dest).name)
        clock.advance()
    assert [p.name for p in finals(small_repo.dest)] == stamps[-14:]
    assert hidden_leftovers(small_repo.dest) == []


def test_retention_for_large_files_keeps_only_the_newest_3_copies(tmp_path, monkeypatch):
    r = Repo(tmp_path)
    make_db(r.root / "memory" / "big.sqlite", rows=400, pad=300)          # about 150 KB: "large" below
    make_db(r.root / "memory" / "small.sqlite", rows=2, pad=1)
    write_file(r.root / "journal" / "j.jsonl", "{}\n")
    big_size = (r.root / "memory" / "big.sqlite").stat().st_size
    assert big_size > 50_000 > (r.root / "memory" / "small.sqlite").stat().st_size
    clock = Clock(dt.datetime(2026, 2, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    for _ in range(6):
        assert run(r, "--large-bytes", "50000") == 0
        clock.advance()
    folders = finals(r.dest)
    assert len(folders) == 6                                              # 14 not reached: nothing dropped whole
    newest_three, older = folders[-3:], folders[:-3]
    for f in newest_three:
        assert (f / "memory" / "big.sqlite").is_file()
        assert "pruned" not in entry(manifest_of(f), "memory/big.sqlite")
    for f in older:
        m = manifest_of(f)
        assert not (f / "memory" / "big.sqlite").exists()
        assert entry(m, "memory/big.sqlite").get("pruned")                # the manifest still tells the story
        assert entry(m, "memory/big.sqlite")["sha256"]                    # and keeps what the copy was
        assert (f / "memory" / "small.sqlite").is_file() and (f / "journal" / "j.jsonl").is_file()
        code, lines = backup.verify_backup(f)
        assert code == 0, lines                                           # a pruned copy is not a failure


def test_retention_never_deletes_anything_outside_the_destination(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    dest = outer / "dest"
    lookalike = write_file(outer / "20200101T000000Z" / "MANIFEST.json", "{}")   # beside dest, not in it
    sibling = write_file(outer / "dest_sibling" / "precious.txt", "keep")
    r = Repo(tmp_path)
    make_db(r.root / "memory" / "a.sqlite", rows=3)
    write_file(r.root / "journal" / "j.jsonl", "{}\n")
    write_file(dest / "20190101T000000Z" / "no_manifest.txt", "foreign")        # stamp-named but not ours
    write_file(dest / "notes.txt", "mine")
    write_file(dest / "keepme" / "f", "mine")
    outside_link = tmp_path / "linked_target"
    write_file(outside_link / "20180101T000000Z" / "MANIFEST.json", "{}")
    (dest / "20180101T000000Z").symlink_to(outside_link / "20180101T000000Z")  # stamp-named symlink out of dest
    clock = Clock(dt.datetime(2026, 3, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    for _ in range(17):
        assert backup.main(["run", "--root", str(r.root), "--dest", str(dest)]) == 0
        clock.advance()
    ours = [p for p in finals(dest) if not p.is_symlink() and (p / "MANIFEST.json").is_file()]
    assert len(ours) == 14
    assert lookalike.exists() and sibling.exists()
    assert (dest / "20190101T000000Z" / "no_manifest.txt").exists()
    assert (dest / "notes.txt").exists() and (dest / "keepme" / "f").exists()
    assert (outside_link / "20180101T000000Z" / "MANIFEST.json").exists()


def test_retention_does_not_let_partial_backups_push_out_the_last_complete_one(small_repo, monkeypatch):
    clock = Clock(dt.datetime(2026, 4, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    assert run(small_repo) == 0
    good = newest(small_repo.dest).name
    write_file(small_repo.root / "memory" / "broken.sqlite", b"this is not a database at all")
    for _ in range(16):
        clock.advance()
        assert run(small_repo) == 1
    assert (small_repo.dest / good / "MANIFEST.json").is_file()


# --------------------------------------------------------------------------------------- outcome: failures are named
def _break_pages(path):
    data = bytearray(path.read_bytes())
    for page in (2, 3, 4):                           # smash table pages, keep the 100-byte header intact
        start = (page - 1) * 4096
        data[start:start + 4096] = b"\xff" * 4096
    path.write_bytes(bytes(data))


def test_corrupt_sources_are_reported_with_exit_1_and_the_rest_is_backed_up(repo, capsys):
    write_file(repo.root / "memory" / "garbage.sqlite", b"this is not a database at all, just text")
    make_db(repo.root / "memory" / "broken.sqlite", rows=800, pad=300)
    _break_pages(repo.root / "memory" / "broken.sqlite")
    with contextlib.closing(sqlite3.connect(repo.root / "memory" / "broken.sqlite")) as c:
        try:
            ok = c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        except sqlite3.DatabaseError:
            ok = False
    assert not ok                                     # the fixture really is corrupt
    code = run(repo)
    captured = capsys.readouterr()
    assert code == 1
    folder = newest(repo.dest)
    m = manifest_of(folder)
    assert m["status"] == "partial"
    assert failed_paths(m) == {"memory/garbage.sqlite", "memory/broken.sqlite"}
    assert "memory/garbage.sqlite" in captured.err and "memory/broken.sqlite" in captured.err
    assert not (folder / "memory" / "garbage.sqlite").exists() and not (folder / "memory" / "broken.sqlite").exists()
    assert {e["path"] for e in m["files"]} >= {"memory/rollback.sqlite", "memory/wal_open.sqlite",
                                                "journal/2026-11-12.jsonl", "config/risk.json"}
    assert all(e["path"] not in failed_paths(m) for e in m["files"])


@pytest.mark.skipif(IS_ROOT, reason="root can read anything")
def test_unreadable_sources_are_reported_with_exit_1_and_the_rest_is_backed_up(repo, capsys):
    locked_db = repo.root / "memory" / "locked.sqlite"
    locked_file = repo.root / "journal" / "secret.jsonl"
    make_db(locked_db, rows=5)
    write_file(locked_file, "{}\n")
    os.chmod(locked_db, 0)
    os.chmod(locked_file, 0)
    try:
        code = run(repo)
    finally:
        os.chmod(locked_db, 0o600)
        os.chmod(locked_file, 0o600)
    captured = capsys.readouterr()
    assert code == 1
    folder = newest(repo.dest)
    m = manifest_of(folder)
    assert failed_paths(m) == {"memory/locked.sqlite", "journal/secret.jsonl"}
    assert "memory/locked.sqlite" in captured.err and "journal/secret.jsonl" in captured.err
    assert (folder / "memory" / "rollback.sqlite").is_file() and (folder / "journal" / "2026-11-12.jsonl").is_file()


@pytest.mark.skipif(IS_ROOT, reason="root can read anything")
def test_an_unreadable_folder_is_named_not_skipped_silently(repo, capsys):
    locked = repo.root / "journal" / "sealed"
    write_file(locked / "a.jsonl", "{}\n")
    os.chmod(locked, 0)
    try:
        code = run(repo)
    finally:
        os.chmod(locked, 0o700)
    assert code == 1
    assert "journal/sealed" in failed_paths(manifest_of(newest(repo.dest)))


def test_a_database_that_stays_locked_is_named_after_the_timeout(small_repo, capsys):
    stuck = small_repo.root / "memory" / "stuck.sqlite"
    make_db(stuck, mode="delete", rows=50)
    holder = sqlite3.connect(stuck, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        t0 = time.monotonic()
        code = run(small_repo, "--db-timeout", "1")
        took = time.monotonic() - t0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert code == 1 and took < 30
    m = manifest_of(newest(small_repo.dest))
    assert failed_paths(m) == {"memory/stuck.sqlite"}
    assert (newest(small_repo.dest) / "memory" / "a.sqlite").is_file()


def test_no_backup_possible_exits_2(tmp_path, capsys):
    root = tmp_path / "empty_repo"
    root.mkdir()
    assert backup.main(["run", "--root", str(root), "--dest", str(tmp_path / "dest")]) == 2
    assert "nothing to back up" in capsys.readouterr().err
    assert backup.main(["run", "--root", str(tmp_path / "missing"), "--dest", str(tmp_path / "dest")]) == 2
    only_bad = tmp_path / "only_bad"
    write_file(only_bad / "memory" / "bad.sqlite", b"not a database")
    assert backup.main(["run", "--root", str(only_bad), "--dest", str(tmp_path / "dest2")]) == 2
    assert finals(tmp_path / "dest2") == [] and hidden_leftovers(tmp_path / "dest2") == []


def test_a_missing_top_level_folder_is_named_in_the_manifest(small_repo):
    assert run(small_repo) == 0
    assert manifest_of(newest(small_repo.dest))["absent_folders"] == ["artifacts"]


# --------------------------------------------------------------------------------------- outcome: refused destinations
def test_destinations_inside_or_equal_to_the_repository_are_refused(repo, tmp_path, capsys):
    (tmp_path / "elsewhere").mkdir()
    link_to_repo = tmp_path / "elsewhere" / "link_to_repo"
    link_to_repo.symlink_to(repo.root, target_is_directory=True)
    link_into_repo = tmp_path / "elsewhere" / "link_into_repo"
    link_into_repo.symlink_to(repo.root / "memory", target_is_directory=True)
    inside_via_link = link_to_repo / "backups"
    cases = [repo.root, repo.root / "backups", repo.root / "memory", repo.root / "journal" / "sub" / "new",
             link_to_repo, link_into_repo, inside_via_link]
    before = tree_state(repo.root)
    for dest in cases:
        capsys.readouterr()
        code = backup.main(["run", "--root", str(repo.root), "--dest", str(dest)])
        err = capsys.readouterr().err
        assert code == 2, dest
        assert "repository" in err, (dest, err)
    assert tree_state(repo.root) == before            # nothing created, nothing touched, not even a folder
    assert not (repo.root / "backups").exists()


def test_a_destination_that_is_a_file_is_refused(small_repo, capsys):
    write_file(small_repo.dest, "i am a file")
    assert run(small_repo) == 2
    assert "not a folder" in capsys.readouterr().err


def test_too_little_free_space_is_refused_plainly(repo, monkeypatch, capsys):
    monkeypatch.setattr(backup, "free_bytes", lambda path: 10)
    assert run(repo) == 2
    err = capsys.readouterr().err
    assert "free space" in err and "twice" in err
    assert finals(repo.dest) == [] and hidden_leftovers(repo.dest) == []


def test_free_space_rule_is_twice_what_will_be_copied(small_repo, monkeypatch):
    seen = []
    monkeypatch.setattr(backup, "free_bytes", lambda path: seen.append(path) or 10 ** 12)
    assert run(small_repo) == 0
    total = sum(e["size"] for e in manifest_of(newest(small_repo.dest))["files"])
    assert seen
    need = 2 * total
    monkeypatch.setattr(backup, "free_bytes", lambda path: need - 1)
    assert run(small_repo) == 2
    monkeypatch.setattr(backup, "free_bytes", lambda path: need + 64 * 1024)      # a little room for sidecars
    assert run(small_repo) == 0


# --------------------------------------------------------------------------------------- outcome: verify
def _flip_one_byte(path, offset=None):
    data = bytearray(Path(path).read_bytes())
    i = len(data) // 2 if offset is None else offset
    data[i] ^= 0x01
    Path(path).write_bytes(bytes(data))


def test_verify_passes_a_good_backup_and_does_not_touch_it(repo):
    assert run(repo) == 0
    folder = newest(repo.dest)
    before = tree_state(folder)
    code, lines = backup.verify_backup(folder)
    assert code == 0, lines
    assert tree_state(folder) == before                   # no -wal, no -shm, nothing rewritten
    assert backup.main(["verify", str(folder)]) == 0


def test_verify_catches_one_changed_byte_in_a_file_and_in_a_database(repo, capsys):
    assert run(repo) == 0
    folder = newest(repo.dest)
    target = folder / "journal" / "2026-11-12.jsonl"
    _flip_one_byte(target)
    code, lines = backup.verify_backup(folder)
    assert code == 1 and any("journal/2026-11-12.jsonl" in x and "sha256" in x for x in lines), lines
    assert backup.main(["verify", str(folder)]) == 1
    capsys.readouterr()
    target.write_bytes(b'{"a":1}\n{"a":2}\n')
    assert backup.verify_backup(folder)[0] == 0
    db = folder / "memory" / "rollback.sqlite"
    _flip_one_byte(db, offset=len(db.read_bytes()) - 5)
    code, lines = backup.verify_backup(folder)
    assert code == 1 and any("memory/rollback.sqlite" in x for x in lines), lines


def test_verify_runs_the_integrity_check_even_when_the_hash_was_made_to_match(repo):
    assert run(repo) == 0
    folder = newest(repo.dest)
    db = folder / "memory" / "rollback.sqlite"
    make_db(repo.base / "scratch_big.sqlite", rows=800, pad=300)
    big = repo.base / "scratch_big.sqlite"
    _break_pages(big)
    db.write_bytes(big.read_bytes())
    m = manifest_of(folder)
    e = entry(m, "memory/rollback.sqlite")
    e["size"], e["sha256"] = db.stat().st_size, sha256(db)
    (folder / "MANIFEST.json").write_text(json.dumps(m))
    code, lines = backup.verify_backup(folder)
    assert code == 1 and any("integrity" in x and "memory/rollback.sqlite" in x for x in lines), lines


def test_verify_names_missing_and_unlisted_files(repo):
    assert run(repo) == 0
    folder = newest(repo.dest)
    (folder / "config" / "risk.json").unlink()
    write_file(folder / "memory" / "rollback.sqlite-wal", b"stray")
    code, lines = backup.verify_backup(folder)
    text = "\n".join(lines)
    assert code == 1 and "config/risk.json" in text and "rollback.sqlite-wal" in text


def test_verify_of_a_partial_backup_says_so_and_exits_1(small_repo):
    write_file(small_repo.root / "memory" / "broken.sqlite", b"not a database")
    assert run(small_repo) == 1
    code, lines = backup.verify_backup(newest(small_repo.dest))
    assert code == 1 and "PARTIAL" in "\n".join(lines) and "memory/broken.sqlite" in "\n".join(lines)


def test_verify_of_something_that_is_not_a_backup_exits_2(tmp_path):
    assert backup.main(["verify", str(tmp_path / "nope")]) == 2
    (tmp_path / "plain").mkdir()
    assert backup.main(["verify", str(tmp_path / "plain")]) == 2


# --------------------------------------------------------------------------------------- outcome: list
def test_list_shows_the_backups_with_sizes(small_repo, monkeypatch, capsys):
    clock = Clock(dt.datetime(2026, 5, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    assert run(small_repo) == 0
    clock.advance()
    write_file(small_repo.root / "memory" / "broken.sqlite", b"not a database")
    assert run(small_repo) == 1
    write_file(small_repo.dest / "20190101T000000Z" / "foreign.txt", "x")        # not ours: not listed
    capsys.readouterr()
    assert backup.main(["list", "--dest", str(small_repo.dest)]) == 0
    out = capsys.readouterr().out
    lines = [x for x in out.splitlines() if x.startswith("2026")]
    assert len(lines) == 2
    assert "complete" in lines[0] and "partial" in lines[1]
    assert all(("B" in x) for x in lines)
    assert "20190101T000000Z" not in out
    assert "total" in out.lower()


def test_list_of_an_empty_or_missing_destination_says_so(tmp_path, capsys):
    assert backup.main(["list", "--dest", str(tmp_path / "none")]) == 0
    assert "no backups" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------------------- outcome: restore
def test_restore_copies_a_backup_into_an_empty_folder(repo, capsys):
    assert run(repo) == 0
    folder = newest(repo.dest)
    m = manifest_of(folder)
    to_new, to_empty = repo.base / "restored_new", repo.base / "restored_empty"
    to_empty.mkdir()
    for to in (to_new, to_empty):
        assert backup.main(["restore", str(folder), "--to", str(to), "--root", str(repo.root)]) == 0
        for e in m["files"]:
            assert sha256(to / e["path"]) == e["sha256"]
        with contextlib.closing(open_copy(to / "memory" / "triggers.sqlite")) as c:
            assert c.execute("PRAGMA application_id").fetchone() == (APP_ID,)
            assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert counts(to / "memory" / "wal_open.sqlite") == (65, 65)
        assert not (to / "MANIFEST.json").exists()
    assert not [p for p in repo.base.iterdir() if ".restoring" in p.name]


def test_restore_refuses_a_folder_that_is_not_empty(repo, capsys):
    assert run(repo) == 0
    folder = newest(repo.dest)
    full = repo.base / "full"
    write_file(full / "mine.txt", "mine")
    before = tree_state(full)
    assert backup.main(["restore", str(folder), "--to", str(full), "--root", str(repo.root)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert tree_state(full) == before


def test_restore_never_writes_into_the_repository(repo, tmp_path, capsys):
    assert run(repo) == 0
    folder = newest(repo.dest)
    link = tmp_path / "link_to_repo"
    link.symlink_to(repo.root, target_is_directory=True)
    (repo.root / "scratch_empty").mkdir()
    before = tree_state(repo.root)
    for to in (repo.root, repo.root / "restored", repo.root / "scratch_empty", link, link / "restored2"):
        capsys.readouterr()
        assert backup.main(["restore", str(folder), "--to", str(to), "--root", str(repo.root)]) == 2, to
        assert "repository" in capsys.readouterr().err
    assert tree_state(repo.root) == before


def test_restore_refuses_a_target_inside_the_backup_and_a_damaged_backup(repo, capsys):
    assert run(repo) == 0
    folder = newest(repo.dest)
    assert backup.main(["restore", str(folder), "--to", str(folder / "inside"), "--root", str(repo.root)]) == 2
    _flip_one_byte(folder / "config" / "risk.json")
    to = repo.base / "never"
    capsys.readouterr()
    assert backup.main(["restore", str(folder), "--to", str(to), "--root", str(repo.root)]) == 2
    assert "verify" in capsys.readouterr().err
    assert not to.exists()


def test_restore_of_a_backup_with_pruned_large_files_says_what_is_missing(tmp_path, monkeypatch, capsys):
    r = Repo(tmp_path)
    make_db(r.root / "memory" / "big.sqlite", rows=400, pad=300)
    write_file(r.root / "journal" / "j.jsonl", "{}\n")
    clock = Clock(dt.datetime(2026, 6, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    for _ in range(4):
        assert run(r, "--large-bytes", "50000") == 0
        clock.advance()
    oldest = finals(r.dest)[0]
    capsys.readouterr()
    assert backup.main(["restore", str(oldest), "--to", str(tmp_path / "out"), "--root", str(r.root)]) == 0
    out = capsys.readouterr().out
    assert "memory/big.sqlite" in out and "pruned" in out
    assert (tmp_path / "out" / "journal" / "j.jsonl").is_file() and not (tmp_path / "out" / "memory" / "big.sqlite").exists()
