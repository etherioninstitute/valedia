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
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import unicodedata
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
    return sorted(p.name for p in dest.iterdir()
                  if p.name.startswith((".valedia-backup-", ".tmp-", ".scratch-", ".trash-")))


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


@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_database_written_during_the_backup_comes_out_consistent(small_repo, mode, capsys):
    live = small_repo.root / "memory" / "orderbook_loop.sqlite"
    make_db(live, mode=mode, rows=12000, pad=4000)                  # about 50 MB: the copy takes a while
    stop = small_repo.base / "stop_writer"
    writer = subprocess.Popen([sys.executable, "-c", WRITER, str(live), str(stop), "6000"])   # capped: 6000 rows
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with contextlib.suppress(sqlite3.Error):
                with contextlib.closing(sqlite3.connect(f"{live.as_uri()}?mode=ro", uri=True)) as c:
                    if c.execute("SELECT last FROM meta").fetchone()[0] > 12000 + 20:
                        break
            time.sleep(0.01)
        code = run(small_repo)
    finally:
        stop.write_text("stop")
        try:
            writer.wait(timeout=60)
        except subprocess.TimeoutExpired:
            writer.kill()                                      # the process this test started, by its handle
    assert code == 0, capsys.readouterr().err
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
    durations = []
    for _ in range(3):                                   # the fastest clean run bounds where a kill can land
        t0 = time.monotonic()
        assert _spawn(root, dest).wait(timeout=120) == 0
        durations.append(time.monotonic() - t0)
    fastest = min(durations)
    rng = random.Random(27)
    checked = set()
    kills, sigterm, attempts = 0, 0, 0
    while kills < 36 and attempts < 120:                 # a kill counts only when the process was still running
        attempts += 1
        p = _spawn(root, dest)
        time.sleep(rng.uniform(0.0, 0.8) * fastest)
        if p.poll() is None:
            sig = signal.SIGTERM if kills % 4 == 3 else signal.SIGKILL
            os.kill(p.pid, sig)                          # only the process this test started, by its pid
            kills += 1
            sigterm += sig == signal.SIGTERM
        p.wait(timeout=60)
        if dest.exists():
            _assert_no_half_backup(dest, checked)
        assert backup.main(["run", "--root", str(root), "--dest", str(dest)]) == 0     # the next run works
        assert hidden_leftovers(dest) == []
        _assert_no_half_backup(dest, checked)
    print(f"kills={kills} sigterm={sigterm} attempts={attempts} fastest_run={fastest:.2f}s")
    assert kills >= 30 and sigterm >= 5
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
    ours = [f"{backup.WORK_PREFIX}tmp-20250101T000000Z-1", f"{backup.WORK_PREFIX}scratch-20250101T000000Z-1"]
    for name in ours:
        write_file(repo.dest / name / backup.WORK_MARK, b"")
        write_file(repo.dest / name / "partial.bin", b"x")
    (repo.dest / f"{backup.WORK_PREFIX}tmp-20250101T000001Z-2").mkdir()          # killed before its marker
    write_file(repo.dest / f"{backup.WORK_PREFIX}trash-20240101T000000Z-1" / "MANIFEST.json", b"{}")
    assert run(repo) == 0
    assert hidden_leftovers(repo.dest) == []


def test_a_users_files_with_a_working_folders_look_are_never_deleted(repo):
    repo.dest.mkdir()
    mine = [".tmp-mine", ".scratch-x", ".trash-y", ".tmp-20250101T000000Z-1", ".scratch-20250101T000000Z-1",
            ".trash-20240101T000000Z-1", f"{backup.WORK_PREFIX}notes", f"{backup.WORK_PREFIX}tmp-notes"]
    for name in mine:
        write_file(repo.dest / name / "f.txt", b"mine")
    write_file(repo.dest / ".tmp-afile", b"mine")
    # a folder of ours' exact name pattern but not marked, not empty, no manifest: not ours to delete
    write_file(repo.dest / f"{backup.WORK_PREFIX}tmp-20250202T000000Z-7" / "user.txt", b"mine")
    for _ in range(3):
        assert run(repo) == 0
    for name in mine:
        assert (repo.dest / name / "f.txt").read_bytes() == b"mine", name
    assert (repo.dest / ".tmp-afile").read_bytes() == b"mine"
    assert (repo.dest / f"{backup.WORK_PREFIX}tmp-20250202T000000Z-7" / "user.txt").read_bytes() == b"mine"


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
    _make_corrupt_db(small_repo.root / "memory" / "broken.sqlite")
    for _ in range(16):
        clock.advance()
        assert run(small_repo) == 1
    assert (small_repo.dest / good / "MANIFEST.json").is_file()


# --------------------------------------------------------------------------------------- outcome: failures are named
def _make_corrupt_db(path):
    make_db(path, rows=800, pad=300)
    _break_pages(path)


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
    assert failed_paths(m) == {"memory/broken.sqlite"}                        # a non-database name is not a failure
    assert "memory/garbage.sqlite" in captured.err and "memory/broken.sqlite" in captured.err
    assert not (folder / "memory" / "broken.sqlite").exists()                 # a corrupt database: left out
    assert (folder / "memory" / "garbage.sqlite").read_bytes() == b"this is not a database at all, just text"
    assert entry(m, "memory/garbage.sqlite")["not_a_database"] is True       # not a database: a flagged plain copy
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
    assert backup.main(["run", "--root", str(only_bad), "--dest", str(tmp_path / "dest2")]) == 0   # kept as a plain file
    corrupt_only = tmp_path / "corrupt_only"
    make_db(corrupt_only / "memory" / "broken.sqlite", rows=800, pad=300)
    _break_pages(corrupt_only / "memory" / "broken.sqlite")
    assert backup.main(["run", "--root", str(corrupt_only), "--dest", str(tmp_path / "dest3")]) == 2
    assert finals(tmp_path / "dest3") == [] and hidden_leftovers(tmp_path / "dest3") == []


def test_a_missing_top_level_folder_is_named_in_the_manifest(small_repo):
    assert run(small_repo) == 0
    assert manifest_of(newest(small_repo.dest))["absent_folders"] == ["artifacts", "state"]


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
    _make_corrupt_db(small_repo.root / "memory" / "broken.sqlite")
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
    _make_corrupt_db(small_repo.root / "memory" / "broken.sqlite")
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


# =========================================================================================== rev 2
# --------------------------------------------------------------------------------------- outcome 1
WRITER = r'''
import os, sqlite3, sys
path, stop = sys.argv[1:3]
cap = int(sys.argv[3]) if len(sys.argv) > 3 else 20000          # every writer is capped
conn = sqlite3.connect(path, timeout=60, isolation_level=None)
n = conn.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
stop_at = n + cap
while not os.path.exists(stop) and n < stop_at:
    try:
        conn.execute("BEGIN IMMEDIATE")
        n += 1
        conn.execute("INSERT INTO ledger VALUES (?, ?)", (n, b"z" * 300))
        conn.execute("INSERT INTO mirror VALUES (?)", (n,))
        conn.execute("UPDATE meta SET last = ?", (n,))
        conn.execute("COMMIT")
    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        n = conn.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
'''


def _rw_check(path, expected_tables):
    """The ordinary way a restored database is used: a plain read-write open."""
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        got = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in expected_tables}
        assert got == expected_tables
        assert got["ledger"] == got["mirror"] == conn.execute("SELECT last FROM meta").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.timeout(280)
@pytest.mark.parametrize("folder", ["journal", "config"])
@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_a_database_under_journal_or_config_restores_to_the_rows_that_were_checked(tmp_path, folder, mode):
    r = Repo(tmp_path)
    live = r.root / folder / "live.sqlite"
    make_db(live, mode=mode, rows=300, pad=300)
    make_db(r.root / "memory" / "anchor.sqlite", rows=5)
    stop = tmp_path / "stop"
    writer = subprocess.Popen([sys.executable, "-c", WRITER, str(live), str(stop)])
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with contextlib.suppress(sqlite3.Error):
                with contextlib.closing(sqlite3.connect(f"{live.as_uri()}?mode=ro", uri=True)) as c:
                    if c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] > 320:
                        break
            time.sleep(0.02)
        for i in range(20):
            assert run(r) == 0
            backed = newest(r.dest)
            m = manifest_of(backed)
            assert not [e["path"] for e in m["files"] if e["path"].endswith(("-wal", "-shm", "-journal"))]
            assert not [p for p in backed.rglob("*") if p.name.endswith(("-wal", "-shm", "-journal"))]
            assert entry(m, f"{folder}/live.sqlite")["kind"] == "sqlite"
            to = tmp_path / f"restored{i}"
            assert backup.main(["restore", str(backed), "--to", str(to), "--root", str(r.root)]) == 0
            _rw_check(to / folder / "live.sqlite", entry(m, f"{folder}/live.sqlite")["tables"])
    finally:
        stop.write_text("stop")
        try:
            writer.wait(timeout=60)
        except subprocess.TimeoutExpired:
            writer.kill()                                  # the process this test started, by its handle


def test_side_files_of_a_database_in_journal_are_not_copied_but_the_rest_is(small_repo):
    db = small_repo.root / "journal" / "x.sqlite"
    make_db(db, mode="wal", rows=10)
    keep = sqlite3.connect(db)
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute("INSERT INTO ledger VALUES (1000, x'00')")
    keep.commit()
    write_file(small_repo.root / "journal" / "orphan.txt-wal", "not a side file of any database")
    write_file(small_repo.root / "journal" / "notes.txt", "n")
    write_file(small_repo.root / "journal" / "notes.txt-journal", "plain text beside plain text")
    try:
        assert (Path(str(db) + "-wal")).exists()
        assert run(small_repo) == 0
    finally:
        keep.close()
    paths = {e["path"] for e in manifest_of(newest(small_repo.dest))["files"]}
    assert "journal/x.sqlite" in paths
    assert not paths & {"journal/x.sqlite-wal", "journal/x.sqlite-shm", "journal/x.sqlite-journal"}
    assert {"journal/orphan.txt-wal", "journal/notes.txt", "journal/notes.txt-journal"} <= paths
    assert counts(newest(small_repo.dest) / "journal" / "x.sqlite") == (11, 10)


def test_a_database_found_by_content_in_config_is_copied_by_the_online_backup(small_repo):
    make_db(small_repo.root / "config" / "settings.dat", mode="wal", rows=7)       # no database name at all
    assert run(small_repo) == 0
    e = entry(manifest_of(newest(small_repo.dest)), "config/settings.dat")
    assert e["kind"] == "sqlite" and e["integrity"] == "ok" and e["tables"]["ledger"] == 7


def test_verify_fails_a_backup_that_holds_a_side_file_beside_a_database_copy(repo):
    assert run(repo) == 0
    folder = newest(repo.dest)
    side = folder / "memory" / "rollback.sqlite-wal"
    side.write_bytes(b"left over")
    code, lines = backup.verify_backup(folder)
    assert code == 1 and any("rollback.sqlite-wal" in x for x in lines), lines
    m = manifest_of(folder)                                        # even when the manifest lists it (as 88d1f1d did)
    m["files"].append({"path": "memory/rollback.sqlite-wal", "kind": "file", "size": side.stat().st_size,
                       "sha256": sha256(side)})
    (folder / "MANIFEST.json").write_text(json.dumps(m))
    code, lines = backup.verify_backup(folder)
    assert code == 1 and any("rollback.sqlite-wal" in x for x in lines), lines
    assert backup.main(["restore", str(folder), "--to", str(repo.base / "never"), "--root", str(repo.root)]) == 2
    assert not (repo.base / "never").exists()


def test_verify_checks_the_row_counts_in_the_manifest(repo):
    assert run(repo) == 0
    folder = newest(repo.dest)
    m = manifest_of(folder)
    entry(m, "memory/rollback.sqlite")["tables"]["ledger"] += 1
    (folder / "MANIFEST.json").write_text(json.dumps(m))
    code, lines = backup.verify_backup(folder)
    assert code == 1 and any("memory/rollback.sqlite" in x and "rows" in x for x in lines), lines


# --------------------------------------------------------------------------------------- outcome 2
def test_links_are_never_followed_and_are_named(small_repo, tmp_path, capsys):
    root = small_repo.root
    outside = tmp_path / "outside"
    write_file(outside / "secret.txt", "s")
    make_db(outside / "o.sqlite", rows=3)
    (root / "journal" / "link_to_file").symlink_to(root / "journal" / "j.jsonl")
    (root / "journal" / "link_to_outside_dir").symlink_to(outside, target_is_directory=True)
    (root / "config" / "link_to_inside_dir").symlink_to(root / "journal", target_is_directory=True)
    (root / "memory" / "link_to_db.sqlite").symlink_to(root / "memory" / "a.sqlite")
    (root / "journal" / "broken").symlink_to(tmp_path / "nowhere")
    os.link(root / "journal" / "j.jsonl", root / "journal" / "hard.jsonl")
    assert run(small_repo) == 0                                     # a named link does not change the exit code
    err = capsys.readouterr().err
    m = manifest_of(newest(small_repo.dest))
    links = ["config/link_to_inside_dir", "journal/broken", "journal/link_to_file", "journal/link_to_outside_dir",
             "memory/link_to_db.sqlite"]
    assert sorted(m["link_not_followed"]) == links
    for link in links:
        assert link in err
    assert str(tmp_path / "outside") not in json.dumps(m)                    # the link's own path only
    assert str(tmp_path / "nowhere") not in json.dumps(m) and "secret.txt" not in json.dumps(m)
    paths = {e["path"] for e in m["files"]}
    assert paths == {"memory/a.sqlite", "journal/j.jsonl", "journal/hard.jsonl", "config/c.json"}
    assert m["status"] == "complete" and m["failures"] == []


# --------------------------------------------------------------------------------------- outcome 3
def _identity_repo(tmp_path):
    parent = tmp_path / "parent"
    root = parent / "Répo"                                      # a name with an accent: case and Unicode forms
    make_db(root / "memory" / "a.sqlite", rows=5)
    write_file(root / "journal" / "j.jsonl", "{}\n")
    (parent / "other").mkdir()
    (parent / "lnk").symlink_to(root, target_is_directory=True)
    (parent / "lnk2").symlink_to(root / "memory", target_is_directory=True)
    return parent, root


def _spellings(parent):
    nfd = unicodedata.normalize("NFD", "Répo")
    upper, lower = "RÉPO", "répo"
    out = [
        f"{parent}/Répo", f"{parent}/Répo/", f"{parent}/Répo/bk/", f"{parent}/Répo/./bk",
        f"{parent}/Répo/../Répo/bk", f"{parent}/other/../Répo/bk", f"{parent}//Répo//bk",
        f"{parent}/Répo/memory/new/deeper", f"{parent}/lnk/bk", f"{parent}/lnk", f"{parent}/lnk2",
        f"{parent}/lnk/../lnk/bk",
    ]
    if os.path.exists(f"{parent}/{upper}"):                         # a case-insensitive volume (this one)
        out += [f"{parent}/{upper}/bk", f"{parent}/{lower}/bk", f"{parent}/{upper}"]
    if os.path.exists(f"{parent}/{nfd}"):                           # a Unicode-normalising volume (this one)
        out += [f"{parent}/{nfd}/bk", f"{parent}/{nfd.upper()}/bk"]
    return out


def test_the_repository_guard_compares_the_place_not_the_spelling(tmp_path, monkeypatch, capsys):
    parent, root = _identity_repo(tmp_path)
    spellings = _spellings(parent)
    assert len(spellings) >= 12
    before_root, before_parent = tree_state(root), sorted(os.listdir(parent))
    for dest in spellings:
        capsys.readouterr()
        assert backup.main(["run", "--root", str(root), "--dest", dest]) == 2, dest
        assert "repository" in capsys.readouterr().err, dest
    monkeypatch.chdir(parent / "other")
    assert backup.main(["run", "--root", str(root), "--dest", "../Répo/bk"]) == 2
    assert backup.main(["run", "--root", str(root), "--dest", "../lnk/bk"]) == 2
    assert tree_state(root) == before_root and sorted(os.listdir(parent)) == before_parent
    if os.path.exists(f"{parent}/RÉPO"):                       # the repository itself spelled another way
        assert backup.main(["run", "--root", f"{parent}/RÉPO", "--dest", f"{root}/bk"]) == 2
        assert backup.main(["run", "--root", f"{parent}/lnk", "--dest", f"{root}/bk"]) == 2
    assert tree_state(root) == before_root


def test_restore_guard_compares_the_place_not_the_spelling(tmp_path, monkeypatch, capsys):
    parent, root = _identity_repo(tmp_path)
    good_dest = tmp_path / "good_dest"
    assert backup.main(["run", "--root", str(root), "--dest", str(good_dest)]) == 0
    folder = newest(good_dest)
    spellings = _spellings(parent)
    before_root, before_parent = tree_state(root), sorted(os.listdir(parent))
    for to in spellings:
        capsys.readouterr()
        assert backup.main(["restore", str(folder), "--to", to, "--root", str(root)]) == 2, to
        assert "repository" in capsys.readouterr().err, to
    monkeypatch.chdir(parent / "other")
    assert backup.main(["restore", str(folder), "--to", "../Répo/restored", "--root", str(root)]) == 2
    assert tree_state(root) == before_root and sorted(os.listdir(parent)) == before_parent
    assert backup.main(["restore", str(folder), "--to", str(tmp_path / "fine"), "--root", str(root)]) == 0


# --------------------------------------------------------------------------------------- outcome 4
BAD_MANIFESTS = {
    "empty_list": b"[]", "a_string": b'"manifest"', "a_number": b"5", "null": b"null", "empty_file": b"",
    "invalid_json": b"{not json", "not_utf8": b"\xff\xfe\x00{\x80", "deeply_nested": b"[" * 200000,
    "files_not_a_list": json.dumps({"format": backup.FORMAT, "files": "nope"}).encode(),
    "wrong_field_types": json.dumps({"format": backup.FORMAT, "status": 5, "failures": {}, "files": [
        {"path": 5, "kind": "file", "size": "big", "sha256": 1}]}).encode(),
    "entry_not_an_object": json.dumps({"format": backup.FORMAT, "status": "complete", "failures": [],
                                       "files": ["memory/a.sqlite"]}).encode(),
    "wrong_format": json.dumps({"format": "something-else", "files": []}).encode(),
}


@pytest.mark.parametrize("name", sorted(BAD_MANIFESTS))
def test_a_manifest_of_the_wrong_shape_crashes_nothing_and_its_folder_is_left_alone(small_repo, name, capsys):
    bad = small_repo.dest / "20200101T000000Z"
    write_file(bad / "MANIFEST.json", BAD_MANIFESTS[name])
    write_file(bad / "keep.txt", "mine")
    capsys.readouterr()
    assert run(small_repo) == 0                                       # run does not crash on it
    assert backup.main(["list", "--dest", str(small_repo.dest)]) == 0
    out = capsys.readouterr()
    assert "20200101T000000Z" in out.out and "unreadable" in out.out.lower()
    assert "Traceback" not in out.err + out.out
    assert backup.main(["verify", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and err.strip()
    assert backup.main(["restore", str(bad), "--to", str(small_repo.base / "never"), "--root",
                        str(small_repo.root)]) == 2
    assert "Traceback" not in capsys.readouterr().err and not (small_repo.base / "never").exists()
    assert (bad / "keep.txt").read_bytes() == b"mine"


def test_unreadable_manifests_are_never_deleted_or_counted_for_retention(small_repo, monkeypatch):
    clock = Clock(dt.datetime(2026, 7, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    for i, name in enumerate(sorted(BAD_MANIFESTS)):
        write_file(small_repo.dest / f"2019010{i % 10}T0000{i:02}Z" / "MANIFEST.json", BAD_MANIFESTS[name])
    bad_names = sorted(p.name for p in finals(small_repo.dest))
    for _ in range(17):
        assert run(small_repo) == 0
        clock.advance()
    ours = [p for p in finals(small_repo.dest) if p.name not in bad_names]
    assert len(ours) == 14
    assert sorted(p.name for p in finals(small_repo.dest) if p.name in bad_names) == bad_names


# --------------------------------------------------------------------------------------- outcome 5
def test_a_leftover_manifest_part_in_a_final_folder_is_cleared_and_harms_nothing(small_repo):
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    part = folder / "MANIFEST.json.part"
    write_file(part, b'{"half": ')
    assert backup.verify_backup(folder)[0] == 0
    assert backup.main(["restore", str(folder), "--to", str(small_repo.base / "out"), "--root",
                        str(small_repo.root)]) == 0
    assert run(small_repo) == 0
    assert not part.exists()
    assert backup.verify_backup(folder)[0] == 0


# --------------------------------------------------------------------------------------- outcome 6
@pytest.mark.parametrize("bad", ["/etc/hosts", "../escape.txt", "memory/../../escape.txt", "memory//a.sqlite",
                                 "./memory/a.sqlite", "", "memory/./a.sqlite", "memory/a.sqlite/../../x"])
def test_a_manifest_is_not_trusted_about_paths(small_repo, bad):
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    m = manifest_of(folder)
    m["files"].append({"path": bad, "kind": "file", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()})
    (folder / "MANIFEST.json").write_text(json.dumps(m))
    before = sorted(os.listdir(small_repo.base))
    code, lines = backup.verify_backup(folder)
    assert code != 0 and "path" in "\n".join(lines).lower(), lines
    to = small_repo.base / "target"
    assert backup.main(["restore", str(folder), "--to", str(to), "--root", str(small_repo.root)]) == 2
    assert sorted(os.listdir(small_repo.base)) == before                # nothing created: no target, no sibling
    assert not (small_repo.base / "escape.txt").exists()


def test_a_link_inside_a_backup_folder_fails_verify(small_repo, tmp_path):
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    outside = write_file(tmp_path / "elsewhere.txt", "x")
    (folder / "config" / "c.json").unlink()
    (folder / "config" / "c.json").symlink_to(outside)
    code, lines = backup.verify_backup(folder)
    assert code == 1 and any("config/c.json" in x for x in lines)


def test_restore_creates_nothing_outside_its_target(small_repo):
    assert run(small_repo) == 0
    before = sorted(os.listdir(small_repo.base))
    to = small_repo.base / "restored"
    assert backup.main(["restore", str(newest(small_repo.dest)), "--to", str(to), "--root", str(small_repo.root)]) == 0
    assert sorted(os.listdir(small_repo.base)) == sorted(before + ["restored"])
    assert sorted(p.name for p in to.iterdir()) == ["config", "journal", "memory"]
    empty = small_repo.base / "empty_target"
    empty.mkdir()
    before = sorted(os.listdir(small_repo.base))
    assert backup.main(["restore", str(newest(small_repo.dest)), "--to", str(empty), "--root",
                        str(small_repo.root)]) == 0
    assert sorted(os.listdir(small_repo.base)) == before


# --------------------------------------------------------------------------------------- outcome 7
def test_a_file_that_changed_while_copied_is_copied_again_up_to_three_times(small_repo, monkeypatch, capsys):
    real = backup.copy_file
    calls = []

    def changing(src, dst, *a, **k):
        size, digest, _changed = real(src, dst, *a, **k)
        if str(src).endswith("j.jsonl"):
            calls.append(1)
            return size, digest, len(calls) < 3                      # two torn copies, then a quiet one
        return size, digest, False

    monkeypatch.setattr(backup, "copy_file", changing)
    assert run(small_repo) == 0
    assert len(calls) == 3
    assert "changed_during_copy" not in entry(manifest_of(newest(small_repo.dest)), "journal/j.jsonl")

    calls.clear()

    def always(src, dst, *a, **k):
        size, digest, _changed = real(src, dst, *a, **k)
        if str(src).endswith("j.jsonl"):
            calls.append(1)
            return size, digest, True
        return size, digest, False

    monkeypatch.setattr(backup, "copy_file", always)
    capsys.readouterr()
    assert run(small_repo) == 0                                       # exit code unchanged: the journal appends
    assert len(calls) == 3
    folder = newest(small_repo.dest)
    assert entry(manifest_of(folder), "journal/j.jsonl")["changed_during_copy"] is True
    assert "journal/j.jsonl" in capsys.readouterr().err
    assert (folder / "journal" / "j.jsonl").read_bytes() == b'{"j":1}\n'


def test_a_file_with_a_database_name_that_is_not_one_is_kept_as_a_plain_file_and_flagged(small_repo, capsys):
    """The lead's rule after the first real run: such a file (the old state/ holds six) no longer makes the
    backup partial."""
    write_file(small_repo.root / "memory" / "notes.db", b"just some notes, not sqlite")
    capsys.readouterr()
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    m = manifest_of(folder)
    e = entry(m, "memory/notes.db")
    assert e["kind"] == "file" and e["not_a_database"] is True
    assert (folder / "memory" / "notes.db").read_bytes() == b"just some notes, not sqlite"
    assert m["status"] == "complete" and m["failures"] == []
    err = capsys.readouterr().err
    assert "NOTE" in err and "memory/notes.db" in err and "FAILED" not in err
    code, lines = backup.verify_backup(folder)
    assert code == 0 and "PARTIAL" not in "\n".join(lines), lines
    (folder / "memory" / "notes.db").write_bytes(b"just some notes, not sqlitE")      # its hash is re-checked
    assert backup.verify_backup(folder)[0] == 1


def test_the_message_after_a_termination_signal_says_whether_a_backup_was_published(small_repo, monkeypatch, capsys):
    def die(*a, **k):
        raise backup.Terminated()

    monkeypatch.setattr(backup, "write_manifest", die)                 # before the rename
    capsys.readouterr()
    assert run(small_repo) == 2
    err = capsys.readouterr().err
    assert "no backup was made" in err and finals(small_repo.dest) == [] and hidden_leftovers(small_repo.dest) == []
    monkeypatch.undo()
    monkeypatch.setattr(backup, "apply_retention", die)               # after the rename
    assert run(small_repo) in (1, 2)
    err = capsys.readouterr().err
    assert len(finals(small_repo.dest)) == 1
    assert "published" in err and "no backup was made" not in err and finals(small_repo.dest)[0].name in err


# --------------------------------------------------------------------------------------- outcome 8
def test_state_is_backed_up_like_memory_and_artifacts(small_repo):
    make_db(small_repo.root / "state" / "01e8.sqlite", rows=9)
    make_db(small_repo.root / "state" / "nested" / "x.db", mode="wal", rows=4)
    make_db(small_repo.root / "state" / "bycontent.bin", rows=3)
    write_file(small_repo.root / "state" / "notes.txt", "not a database")
    assert run(small_repo) == 0
    m = manifest_of(newest(small_repo.dest))
    assert {"state/01e8.sqlite", "state/nested/x.db", "state/bycontent.bin"} <= {e["path"] for e in m["files"]}
    assert "state/notes.txt" not in {e["path"] for e in m["files"]}
    assert entry(m, "state/01e8.sqlite")["tables"]["ledger"] == 9
    assert "state" not in m["absent_folders"]


# --------------------------------------------------------------------------------------- outcome 9
def test_the_docstring_states_the_two_measured_limits():
    doc = backup.__doc__
    assert "-shm" in doc and "rewrite" in doc.lower()
    assert "rollback" in doc.lower() and "1 GB" in doc and "writers" in doc.lower()


# =========================================================================================== rev 3
SECRET = b"OUTSIDE-SECRET-0123456789"


def _bytes_of_backup_contain(folder, needle):
    return any(needle in p.read_bytes() for p in Path(folder).rglob("*") if p.is_file())


def _wait_for_work_folder(dest, proc, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if Path(dest).exists() and any(p.name.startswith(f"{backup.WORK_PREFIX}tmp-") for p in Path(dest).iterdir()):
            return True
        time.sleep(0.005)
    return False


def _swap_in_link(path, target):
    tmp = Path(str(path) + ".swap")
    tmp.symlink_to(target)
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------- outcome 1
RACE_VARIANTS = ["file", "file_under_folder_link", "config_file", "database", "top_level_folder"]


@pytest.mark.timeout(280)
def test_a_link_swapped_in_between_the_scan_and_the_copy_is_never_followed(tmp_path):
    """30 races: the tool is held on a locked database (the first thing it copies) while a link is swapped
    in over a file, a folder, a database or a whole top-level folder that it has still to copy."""
    outside = tmp_path / "outside"
    write_file(outside / "secret.txt", SECRET)
    write_file(outside / "dir" / "f.txt", SECRET)
    write_file(outside / "cfgdir" / "zz.txt", SECRET)
    make_db(outside / "o.sqlite", rows=3, pad=len(SECRET))
    with contextlib.closing(sqlite3.connect(outside / "o.sqlite")) as c:
        c.execute("UPDATE ledger SET payload = ?", (SECRET,))
        c.commit()
    make_db(outside / "cfgdir" / "c.sqlite", rows=3, pad=10)
    with contextlib.closing(sqlite3.connect(outside / "cfgdir" / "c.sqlite")) as c:
        c.execute("UPDATE ledger SET payload = ?", (SECRET,))
        c.commit()
    seen_named = 0
    for trial in range(30):
        variant = RACE_VARIANTS[trial % len(RACE_VARIANTS)]
        base = tmp_path / f"t{trial}"
        root, dest = base / "repo", base / "dest"
        make_db(root / "memory" / "aaa_stuck.sqlite", rows=20)                 # copied first; held by the test
        write_file(root / "journal" / "target.txt", "honest journal bytes")
        write_file(root / "journal" / "sub" / "f.txt", "honest bytes under a folder")
        write_file(root / "config" / "zz.txt", "honest config bytes")
        make_db(root / "config" / "c.sqlite", rows=4)
        holder = sqlite3.connect(root / "memory" / "aaa_stuck.sqlite", isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "run", "--root", str(root), "--dest", str(dest),
                                 "--db-timeout", "2"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert _wait_for_work_folder(dest, proc), "the tool never reached its copy"
            if variant == "file":
                _swap_in_link(root / "journal" / "target.txt", outside / "secret.txt")
            elif variant == "file_under_folder_link":
                os.rename(root / "journal" / "sub", base / "sub_moved")
                (root / "journal" / "sub").symlink_to(outside / "dir", target_is_directory=True)
            elif variant == "config_file":
                _swap_in_link(root / "config" / "zz.txt", outside / "secret.txt")
            elif variant == "database":
                _swap_in_link(root / "config" / "c.sqlite", outside / "o.sqlite")
            else:
                os.rename(root / "config", base / "config_moved")
                (root / "config").symlink_to(outside / "cfgdir", target_is_directory=True)
            out, err = proc.communicate(timeout=120)
        finally:
            holder.execute("ROLLBACK")
            holder.close()
            if proc.poll() is None:
                proc.kill()                                # the process this test started
                proc.wait()
        folder = newest(dest)
        assert not _bytes_of_backup_contain(folder, SECRET), (trial, variant)
        m = manifest_of(folder)
        assert proc.returncode == 1, (trial, variant, err)
        named = set(m.get("changed_into_a_link", []))
        expected = {"file": {"journal/target.txt"}, "file_under_folder_link": {"journal/sub/f.txt"},
                    "config_file": {"config/zz.txt"}, "database": {"config/c.sqlite"},
                    "top_level_folder": {"config/zz.txt", "config/c.sqlite"}}[variant]
        assert named == expected, (trial, variant, named)
        assert expected <= failed_paths(m)
        assert not {e["path"] for e in m["files"]} & expected
        seen_named += 1
    assert seen_named == 30


def test_a_plain_file_rewritten_by_rename_during_the_run_is_copied_with_its_new_content(small_repo, monkeypatch,
                                                                                         capsys):
    """Another regular file (another inode) at the same path is not a link: its current content is copied
    through the no-follow opens, flagged `replaced_during_run`, and the exit code is unchanged."""
    real_scan = backup.scan

    def scan_then_rewrite_by_rename(root):
        result = real_scan(root)
        j = Path(root) / "journal" / "j.jsonl"
        other = write_file(Path(root) / "journal" / "other.tmp", b'{"j":2,"rewritten":true}\n')
        os.replace(other, j)
        return result

    monkeypatch.setattr(backup, "scan", scan_then_rewrite_by_rename)
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    m = manifest_of(folder)
    e = entry(m, "journal/j.jsonl")
    assert e["replaced_during_run"] is True and m["changed_into_a_link"] == [] and m["status"] == "complete"
    assert (folder / "journal" / "j.jsonl").read_bytes() == b'{"j":2,"rewritten":true}\n'
    assert "journal/j.jsonl" in capsys.readouterr().err
    assert backup.verify_backup(folder)[0] == 0
    assert "replaced_during_run" not in entry(m, "config/c.json")


def test_a_database_replaced_by_another_file_between_the_scan_and_the_copy_is_not_copied(small_repo, monkeypatch,
                                                                                         capsys):
    """A database is opened by SQLite by path, so its device and inode must be the scan's: else not copied."""
    real_scan = backup.scan

    def scan_then_replace_db(root):
        result = real_scan(root)
        db = Path(root) / "memory" / "a.sqlite"
        make_db(Path(root) / "memory" / "a.new", rows=3)
        os.replace(Path(root) / "memory" / "a.new", db)
        return result

    monkeypatch.setattr(backup, "scan", scan_then_replace_db)
    assert run(small_repo) == 1
    m = manifest_of(newest(small_repo.dest))
    assert "memory/a.sqlite" in m["changed_into_a_link"]
    assert "memory/a.sqlite" not in {e["path"] for e in m["files"]}
    assert "memory/a.sqlite" in capsys.readouterr().err


def test_a_regular_file_replaced_by_a_link_or_a_folder_is_still_refused(small_repo, tmp_path, monkeypatch):
    outside = write_file(tmp_path / "outside_secret.txt", SECRET)
    real_scan = backup.scan

    def scan_then_swap(root):
        result = real_scan(root)
        _swap_in_link(Path(root) / "journal" / "j.jsonl", outside)
        os.remove(Path(root) / "config" / "c.json")
        (Path(root) / "config" / "c.json").mkdir()
        return result

    monkeypatch.setattr(backup, "scan", scan_then_swap)
    assert run(small_repo) == 1
    folder = newest(small_repo.dest)
    m = manifest_of(folder)
    assert sorted(m["changed_into_a_link"]) == ["config/c.json", "journal/j.jsonl"]
    assert not _bytes_of_backup_contain(folder, SECRET)


# --------------------------------------------------------------------------------------- outcome 2
def _side_name_repo(tmp_path):
    r = Repo(tmp_path)
    root = r.root
    make_db(root / "memory" / "trade-journal", rows=3)                 # a real database named like a side file
    make_db(root / "memory" / "grades-wal", rows=4)
    make_db(root / "state" / "book.db-shm", rows=5)
    make_db(root / "memory" / "real.sqlite", rows=6)
    write_file(root / "memory" / "real.sqlite-wal", b"garbage that is a real side file name")
    write_file(root / "memory" / "real.sqlite-shm", b"garbage")
    make_db(root / "artifacts" / "x.sqlite", rows=7)
    write_file(root / "artifacts" / "x.sqlite-shm", b"a side file name")
    write_file(root / "memory" / "plain.txt", "just text")
    make_db(root / "memory" / "plain.txt-wal", rows=8)                  # base is no database: own content decides
    write_file(root / "journal" / "notes-wal", "plain text, no base beside it")
    write_file(root / "journal" / "kept.txt", "k")
    return r


def test_nothing_is_skipped_by_its_name_alone(tmp_path):
    r = _side_name_repo(tmp_path)
    assert run(r) == 0
    m = manifest_of(newest(r.dest))
    by_path = {e["path"]: e for e in m["files"]}
    for rel, rows in [("memory/trade-journal", 3), ("memory/grades-wal", 4), ("state/book.db-shm", 5),
                      ("memory/plain.txt-wal", 8), ("memory/real.sqlite", 6), ("artifacts/x.sqlite", 7)]:
        assert by_path[rel]["kind"] == "sqlite" and by_path[rel]["tables"]["ledger"] == rows, rel
    assert by_path["journal/notes-wal"]["kind"] == "file"
    assert sorted(m["side_files_skipped"]) == ["artifacts/x.sqlite-shm", "memory/real.sqlite-shm",
                                               "memory/real.sqlite-wal"]
    assert not {"memory/real.sqlite-wal", "memory/real.sqlite-shm", "artifacts/x.sqlite-shm"} & set(by_path)
    assert "memory/plain.txt" not in by_path                            # a plain text file in memory/: not taken
    code, lines = backup.verify_backup(newest(r.dest))
    assert code == 0, lines
    to = tmp_path / "restored"
    assert backup.main(["restore", str(newest(r.dest)), "--to", str(to), "--root", str(r.root)]) == 0
    _rw_check(to / "memory" / "trade-journal", by_path["memory/trade-journal"]["tables"])


# --------------------------------------------------------------------------------------- outcome 3
@pytest.mark.skipif(IS_ROOT, reason="root can read anything")
def test_one_unreadable_folder_in_the_destination_never_stops_the_tool(small_repo, monkeypatch, capsys):
    clock = Clock(dt.datetime(2026, 8, 1, 3, 0, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "utcnow", clock)
    work_like = small_repo.dest / f"{backup.WORK_PREFIX}tmp-20250101T000000Z-1"
    stamp_like = small_repo.dest / "20200101T000000Z"
    write_file(work_like / "f", "x")
    write_file(stamp_like / "MANIFEST.json", "{}")
    os.chmod(work_like, 0)
    os.chmod(stamp_like, 0)
    try:
        for _ in range(16):
            capsys.readouterr()
            assert run(small_repo) == 0                                  # exits by what it backed up
            clock.advance()
        err = capsys.readouterr().err
        assert work_like.name in err and stamp_like.name in err            # named on stderr
        assert backup.main(["list", "--dest", str(small_repo.dest)]) == 0
        out = capsys.readouterr().out
        assert stamp_like.name in out and "unreadable" in out.lower()
        ours = [p for p in finals(small_repo.dest) if p.name != stamp_like.name]
        assert len(ours) == 14                                              # never counted
    finally:
        os.chmod(work_like, 0o700)
        os.chmod(stamp_like, 0o700)
    assert (work_like / "f").exists() and (stamp_like / "MANIFEST.json").exists()      # never deleted


# --------------------------------------------------------------------------------------- outcome 4
def _tool_env_script(body):
    return ("import importlib.util, sys, time\n"
            f"spec = importlib.util.spec_from_file_location('b', {str(SCRIPT)!r})\n"
            "b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)\n" + body)


def test_a_termination_signal_during_the_scan_is_a_plain_message_and_exit_2(small_repo):
    script = _tool_env_script(
        "def slow(root):\n"
        "    print('ready', flush=True)\n"
        "    time.sleep(60)\n"
        "b.scan = slow\n"
        f"sys.exit(b.main(['run', '--root', {str(small_repo.root)!r}, '--dest', {str(small_repo.dest)!r}]))\n")
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        proc.send_signal(signal.SIGTERM)                                  # the process this test started
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 2, (proc.returncode, err)
    assert "no backup was made" in err and "Traceback" not in err


def test_a_termination_signal_while_waiting_for_a_database_lock_acts_within_a_few_seconds(small_repo):
    stuck = small_repo.root / "memory" / "aaa_stuck.sqlite"
    make_db(stuck, rows=20)
    holder = sqlite3.connect(stuck, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "run", "--root", str(small_repo.root), "--dest",
                             str(small_repo.dest), "--db-timeout", "600"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        assert _wait_for_work_folder(small_repo.dest, proc)
        time.sleep(1.0)                                                   # it is now inside the lock wait
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
        took = time.monotonic() - t0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 2 and "no backup was made" in err, (proc.returncode, err)
    assert took < 3.5, took
    assert finals(small_repo.dest) == [] and hidden_leftovers(small_repo.dest) == []


# --------------------------------------------------------------------------------------- outcome 5
def test_restore_refuses_when_the_parent_of_the_target_does_not_exist(small_repo, capsys):
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    before = sorted(os.listdir(small_repo.base))
    for to in (small_repo.base / "a" / "b" / "c", small_repo.base / "a" / "c"):
        assert backup.main(["restore", str(folder), "--to", str(to), "--root", str(small_repo.root)]) == 2
        assert "does not exist" in capsys.readouterr().err
    assert sorted(os.listdir(small_repo.base)) == before                  # nothing created above the target
    (small_repo.base / "a").mkdir()
    assert backup.main(["restore", str(folder), "--to", str(small_repo.base / "a" / "c"), "--root",
                        str(small_repo.root)]) == 0


# --------------------------------------------------------------------------------------- outcome 6
def test_publication_is_judged_by_the_disk_not_by_a_flag(small_repo, monkeypatch, capsys):
    real_rename = os.rename

    def rename_then_die(src, dst, *a, **k):
        real_rename(src, dst, *a, **k)
        if os.path.basename(str(src)).startswith(f"{backup.WORK_PREFIX}tmp-"):
            raise backup.Terminated()                                      # right after the rename, before any flag

    monkeypatch.setattr(backup.os, "rename", rename_then_die)
    capsys.readouterr()
    assert run(small_repo) == 1
    err = capsys.readouterr().err
    assert len(finals(small_repo.dest)) == 1 and "published" in err and "no backup was made" not in err
    monkeypatch.undo()


def test_restore_never_makes_a_working_folder_beside_its_target_during_the_run(small_repo, monkeypatch):
    assert run(small_repo) == 0
    parent = small_repo.base / "holder"
    parent.mkdir()
    to = parent / "target"
    listings = []
    real_copy = backup.shutil.copyfile

    def watching(src, dst, *a, **k):
        listings.append(sorted(os.listdir(parent)))
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(backup.shutil, "copyfile", watching)
    assert backup.main(["restore", str(newest(small_repo.dest)), "--to", str(to), "--root",
                        str(small_repo.root)]) == 0
    assert listings and all(x == ["target"] for x in listings), listings


def test_an_unexpected_error_is_a_plain_message_and_exit_2_never_a_traceback(small_repo, monkeypatch, capsys):
    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(backup, "list_backups", boom)
    assert backup.main(["list", "--dest", str(small_repo.dest)]) == 2
    assert "unexpected" in capsys.readouterr().err
    monkeypatch.undo()
    monkeypatch.setattr(backup, "scan", boom)
    assert run(small_repo) == 2
    err = capsys.readouterr().err
    assert "unexpected" in err and "Traceback" not in err


def test_a_top_level_folder_that_is_a_link_is_not_descended_into(small_repo, tmp_path):
    outside = tmp_path / "outside_top"
    write_file(outside / "secret.txt", SECRET)
    make_db(outside / "o.sqlite", rows=2)
    write_file(outside / "j2.jsonl", SECRET)
    for name in ("artifacts", "state"):
        (small_repo.root / name).symlink_to(outside, target_is_directory=True)
    shutil.rmtree(small_repo.root / "journal")
    (small_repo.root / "journal").symlink_to(outside, target_is_directory=True)
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    m = manifest_of(folder)
    assert sorted(m["link_not_followed"]) == ["artifacts", "journal", "state"]
    assert not _bytes_of_backup_contain(folder, SECRET)
    assert {e["path"] for e in m["files"]} == {"memory/a.sqlite", "config/c.json"}
    assert m["absent_folders"] == []


# =========================================================================================== rev 4
SWAPPER = r'''
import ctypes, os, random, sys, time
libc = ctypes.CDLL(None, use_errno=True)
a, b, stop, cap = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
rng = random.Random(4)
def wait(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass
n = 0
while not os.path.exists(stop) and n < cap:          # capped: it also ends by itself
    libc.renamex_np(a.encode(), b.encode(), 2)       # RENAME_SWAP: the link takes the path, atomically
    wait(rng.uniform(20e-6, 150e-6))                 # a flicker of about 0.1 ms
    libc.renamex_np(a.encode(), b.encode(), 2)       # and the real file is back
    wait(rng.uniform(0, 1.5e-3))
    n += 1
'''


def _secret_db(path, mode="delete", rows=6):
    """An outside database whose every payload is the secret marker."""
    make_db(path, mode=mode, rows=rows, pad=len(SECRET))
    with contextlib.closing(sqlite3.connect(path)) as c:
        c.execute("UPDATE ledger SET payload = ?", (SECRET,))
        c.commit()


SWAP_VARIANTS = ["rollback_db", "wal_db_with_a_live_writer", "middle_folder", "wal_file_of_a_live_database"]


@pytest.mark.timeout(280)
def test_a_database_path_swapped_to_a_link_and_back_as_fast_as_possible_is_never_copied_from_outside(tmp_path):
    """300 backups (75 per route) while a helper process swaps, atomically and as fast as it can, the database
    path, a folder in its path, or its -wal with a link to a file outside the repository."""
    outside = tmp_path / "outside"
    swapdir = tmp_path / "swapdir"
    swapdir.mkdir()
    write_file(outside / "anchor", b"x")
    _secret_db(outside / "d.sqlite")
    _secret_db(outside / "live.sqlite", mode="wal")
    _secret_db(outside / "sub" / "d.sqlite")
    keeper = sqlite3.connect(outside / "live.sqlite")                       # outside WAL with frames in it
    keeper.execute("PRAGMA wal_autocheckpoint=0")
    keeper.executemany("INSERT INTO ledger VALUES (?, ?)", [(100 + i, SECRET) for i in range(5)])
    keeper.commit()
    shutil.copyfile(str(outside / "live.sqlite") + "-wal", outside / "outside-wal-copy")
    keeper.close()
    stats = {v: {"attempts": 0, "kept": 0, "named": 0} for v in SWAP_VARIANTS}
    for variant in SWAP_VARIANTS:
        base = tmp_path / variant
        root, dest = base / "repo", base / "dest"
        write_file(root / "journal" / "anchor.txt", "an anchor so that every run publishes")
        writer = None
        stop = base / "stop_swapper"
        stop_writer = base / "stop_writer"
        if variant == "rollback_db":
            make_db(root / "memory" / "d.sqlite", rows=30)
            a, b, rel = root / "memory" / "d.sqlite", swapdir / f"{variant}-partner", "memory/d.sqlite"
            os.symlink(outside / "d.sqlite", b)
        elif variant == "wal_db_with_a_live_writer":
            make_db(root / "memory" / "d.sqlite", mode="wal", rows=30)
            writer = subprocess.Popen([sys.executable, "-c", WRITER, str(root / "memory" / "d.sqlite"),
                                       str(stop_writer), "30000"])
            a, b, rel = root / "memory" / "d.sqlite", swapdir / f"{variant}-partner", "memory/d.sqlite"
            os.symlink(outside / "live.sqlite", b)
        elif variant == "middle_folder":
            make_db(root / "memory" / "sub" / "d.sqlite", rows=30)
            a, b, rel = root / "memory" / "sub", swapdir / f"{variant}-partner", "memory/sub/d.sqlite"
            os.symlink(outside / "sub", b, target_is_directory=True)
        else:
            make_db(root / "memory" / "d.sqlite", mode="wal", rows=30)
            writer = subprocess.Popen([sys.executable, "-c", WRITER, str(root / "memory" / "d.sqlite"),
                                       str(stop_writer), "30000"])
            a, b, rel = Path(str(root / "memory" / "d.sqlite") + "-wal"), swapdir / f"{variant}-partner", "memory/d.sqlite"
            os.symlink(outside / "outside-wal-copy", b)
        try:
            if writer is not None:
                deadline = time.monotonic() + 20
                while not Path(str(root / "memory" / "d.sqlite") + "-wal").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
            swapper = subprocess.Popen([sys.executable, "-c", SWAPPER, str(a), str(b), str(stop), "1000000"])
            try:
                time.sleep(0.2)
                for _ in range(75):
                    backup.main(["run", "--root", str(root), "--dest", str(dest), "--keep", "2"])
                    folder = newest(dest)
                    assert not _bytes_of_backup_contain(folder, SECRET), (variant, folder.name)
                    m = manifest_of(folder)
                    stats[variant]["attempts"] += 1
                    stats[variant]["kept"] += any(e["path"] == rel for e in m["files"])
                    stats[variant]["named"] += bool(set(m.get("changed_into_a_link", [])) | failed_paths(m))
            finally:
                stop.write_text("stop")
                try:
                    swapper.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    swapper.kill()                                           # the process this test started
        finally:
            stop_writer.write_text("stop")
            if writer is not None:
                try:
                    writer.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    writer.kill()
    print({v: stats[v] for v in SWAP_VARIANTS})
    assert sum(s["attempts"] for s in stats.values()) == 300
    assert sum(s["kept"] for s in stats.values()) >= 1, stats                # the legitimate file does get backed up


@pytest.mark.parametrize("variant", ["before", "after"])
def test_a_database_renamed_over_another_during_a_lock_wait_is_refused(small_repo, variant, capsys):
    root = small_repo.root
    stuck, nxt = root / "memory" / "aaa_stuck.sqlite", root / "memory" / "zzz_next.sqlite"
    make_db(stuck, rows=20)
    make_db(nxt, rows=7)
    replacement = small_repo.base / "replacement.sqlite"
    make_db(replacement, rows=99)
    holder = sqlite3.connect(stuck, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "run", "--root", str(root), "--dest", str(small_repo.dest),
                             "--db-timeout", "60"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert _wait_for_work_folder(small_repo.dest, proc)
        time.sleep(0.6)                                                     # the tool is inside the lock wait
        victim = nxt if variant == "before" else stuck                      # not yet reached / being waited on
        os.replace(replacement, victim)
        holder.execute("ROLLBACK")
        out, err = proc.communicate(timeout=120)
    finally:
        holder.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    rel = "memory/zzz_next.sqlite" if variant == "before" else "memory/aaa_stuck.sqlite"
    assert proc.returncode == 1, (proc.returncode, err)
    folder = newest(small_repo.dest)
    m = manifest_of(folder)
    assert m["changed_into_a_link"] == [rel]
    assert rel not in {e["path"] for e in m["files"]} and not (folder / rel).exists()
    assert rel in err


# --------------------------------------------------------------------------------------- outcome 4
def test_a_wal_that_vanishes_between_the_look_and_the_open_is_looked_at_again(small_repo, monkeypatch, capsys):
    src_dir = small_repo.base / "image_src"
    make_db(src_dir / "img.sqlite", mode="wal", rows=10)
    live = sqlite3.connect(src_dir / "img.sqlite")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.executemany("INSERT INTO ledger VALUES (?, ?)", [(i, b"w") for i in range(11, 16)])
    live.executemany("INSERT INTO mirror VALUES (?)", [(i,) for i in range(11, 16)])
    live.execute("UPDATE meta SET last = 15")
    live.commit()
    target = small_repo.root / "memory" / "img.sqlite"                      # a crash image: db and -wal, no -shm
    shutil.copyfile(src_dir / "img.sqlite", target)
    shutil.copyfile(str(src_dir / "img.sqlite") + "-wal", str(target) + "-wal")
    live.close()
    real = backup._copy_nofollow
    calls = []

    def vanishing(root, rel, dst, expect=None):
        if rel.endswith("-wal") and not calls:
            calls.append(rel)
            os.remove(Path(root) / rel)                                       # gone between the look and the open
        return real(root, rel, dst, expect)

    monkeypatch.setattr(backup, "_copy_nofollow", vanishing)
    capsys.readouterr()
    assert run(small_repo) == 0, capsys.readouterr().err
    assert calls == ["memory/img.sqlite-wal"]
    m = manifest_of(newest(small_repo.dest))
    assert m["failures"] == [] and entry(m, "memory/img.sqlite")["integrity"] == "ok"


# --------------------------------------------------------------------------------------- outcome 5
def test_verify_and_restore_do_not_follow_links_at_their_own_edges(small_repo, capsys):
    assert run(small_repo) == 0
    folder = newest(small_repo.dest)
    link_to_backup = small_repo.base / "link_to_backup"
    link_to_backup.symlink_to(folder, target_is_directory=True)
    capsys.readouterr()
    assert backup.main(["verify", str(link_to_backup)]) == 2
    assert "link" in capsys.readouterr().err.lower()
    assert backup.main(["verify", str(link_to_backup) + "/"]) == 2
    assert backup.main(["restore", str(link_to_backup), "--to", str(small_repo.base / "never1"), "--root",
                        str(small_repo.root)]) == 2
    assert not (small_repo.base / "never1").exists()
    # a MANIFEST.json that is a link
    copy_dir = small_repo.base / "copy_of_backup"
    shutil.copytree(folder, copy_dir)
    real_manifest = small_repo.base / "manifest_elsewhere.json"
    os.replace(copy_dir / "MANIFEST.json", real_manifest)
    (copy_dir / "MANIFEST.json").symlink_to(real_manifest)
    capsys.readouterr()
    assert backup.main(["verify", str(copy_dir)]) == 2
    assert "link" in capsys.readouterr().err.lower()
    assert backup.main(["restore", str(copy_dir), "--to", str(small_repo.base / "never2"), "--root",
                        str(small_repo.root)]) == 2
    assert not (small_repo.base / "never2").exists()
    # restore --to a link (even one to an empty folder)
    empty = small_repo.base / "empty_elsewhere"
    empty.mkdir()
    link_to = small_repo.base / "link_to_empty"
    link_to.symlink_to(empty, target_is_directory=True)
    capsys.readouterr()
    assert backup.main(["restore", str(folder), "--to", str(link_to), "--root", str(small_repo.root)]) == 2
    assert "link" in capsys.readouterr().err.lower()
    assert backup.main(["restore", str(folder), "--to", str(link_to) + "/", "--root", str(small_repo.root)]) == 2
    assert list(empty.iterdir()) == []
    dangling = small_repo.base / "dangling"
    dangling.symlink_to(small_repo.base / "not_there")
    assert backup.main(["restore", str(folder), "--to", str(dangling), "--root", str(small_repo.root)]) == 2
    assert not (small_repo.base / "not_there").exists()
    # and a manifest that is a link is unreadable for list and retention too (never followed)
    bad = small_repo.dest / "20190101T000000Z"
    bad.mkdir()
    (bad / "MANIFEST.json").symlink_to(folder / "MANIFEST.json")
    assert backup.main(["list", "--dest", str(small_repo.dest)]) == 0
    out = capsys.readouterr().out
    assert "20190101T000000Z" in out and "unreadable" in out.lower()


# --------------------------------------------------------------------------------------- outcome 6
@pytest.mark.parametrize("stray", [b"", bytes([0xAB]) * 100, b"WAL junk"])
def test_reading_a_rollback_database_with_a_stray_wal_creates_nothing_beside_it(small_repo, stray):
    db = small_repo.root / "memory" / "stray.sqlite"
    make_db(db, mode="delete", rows=10)
    write_file(str(db) + "-wal", stray)
    before = tree_state(small_repo.root)
    assert not any(k.endswith("stray.sqlite-shm") for k in before)
    assert run(small_repo) == 0
    after = tree_state(small_repo.root)
    assert after == before, set(after) ^ set(before)                          # no -shm, no change at all
    e = entry(manifest_of(newest(small_repo.dest)), "memory/stray.sqlite")
    assert e["integrity"] == "ok" and e["tables"]["ledger"] == 10


def test_a_database_that_is_another_file_only_at_the_check_before_is_refused(small_repo, monkeypatch, capsys):
    """Another file stands at the path when the check before looks, and the original is back before SQLite
    opens it and before the check after: only the check before can refuse it."""
    db = small_repo.root / "memory" / "a.sqlite"
    aside = small_repo.root / "memory" / "a.aside"
    real_scan, real_fds = backup.scan, backup.open_fds
    fired = []

    def scan_then_stand_in(root):
        result = real_scan(root)
        os.rename(db, aside)
        make_db(db, rows=3)                                       # another file (another inode) at the path
        return result

    def fds_then_put_the_original_back():
        if aside.exists() and not fired:
            fired.append(1)
            os.replace(aside, db)                                  # the original is back (its own inode)
        return real_fds()

    monkeypatch.setattr(backup, "scan", scan_then_stand_in)
    monkeypatch.setattr(backup, "open_fds", fds_then_put_the_original_back)
    assert run(small_repo) == 1
    m = manifest_of(newest(small_repo.dest))
    assert m["changed_into_a_link"] == ["memory/a.sqlite"]
    assert "memory/a.sqlite" not in {e["path"] for e in m["files"]}
    assert "memory/a.sqlite" in capsys.readouterr().err
