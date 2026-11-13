#!/usr/bin/env python3
"""Backups of the trader's memory (roadmap 5.1). Standard library only; no trading imports.

    python tools/backup.py run [--root REPO] [--dest FOLDER]
    python tools/backup.py verify BACKUP_FOLDER
    python tools/backup.py list [--dest FOLDER]
    python tools/backup.py restore BACKUP_FOLDER --to EMPTY_FOLDER

What `run` takes: every SQLite database under memory/ and artifacts/ (found by content, not only by name),
the whole journal/ folder and config/. Each database is copied with SQLite's own online backup from a
read-only connection, so a database that is being written at that moment comes out as one consistent
snapshot, with its triggers, application_id and user_version carried unchanged. A source is never changed:
no file is created or removed beside it. (A database in WAL mode with no -wal/-shm beside it cannot be
opened even read-only without SQLite creating those two files, so such a database, which by definition
nobody has open, is first copied as a quiet file into a private scratch folder inside the destination, and
that copy is backed up. If a writer shows up while that copy is made, its sidecars appear and the database
is then read in place, read-only, which creates nothing.)

The copy goes into `<dest>/.tmp-<stamp>-<pid>/` and is renamed to `<dest>/<stamp>/` (UTC,
YYYYMMDDTHHMMSSZ) in one step when everything is copied and every database copy passed its integrity
check. A run that is killed leaves only dot-folders, which the next run removes. A source that cannot be
read or whose copy fails its integrity check is named in the manifest (and on stderr), left out of the
copy, and makes the exit code 1; everything else is still backed up.

Exit codes of `run`: 0 everything copied and checked; 1 backup made but something was named as failed;
2 no backup could be made (refused destination, too little free space, another run holds the lock, a
write error at the destination, nothing to back up). verify: 0 matches its manifest and is complete,
1 does not match or is PARTIAL, 2 not a backup. list: 0. restore: 0 done, 2 refused or failed.

Retention (after a successful rename): the newest 14 backups are kept (the newest complete one is never
dropped, so a run of partial backups cannot push out the last whole one); of a file larger than 200 MB
only the newest 3 copies are kept, older backups keep their manifest (the entry is marked pruned) and
their other files. Only folders named like a stamp that hold a MANIFEST.json, directly inside the
destination, are ever deleted; a folder is first renamed to `.trash-*` so a kill never leaves half of an
old backup under a final name.
"""
import sys

sys.dont_write_bytecode = True
import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import time
from pathlib import Path

VERSION = "VALEDIA-BACKUP-1.0"
FORMAT = "valedia-trader-backup-1"
MANIFEST = "MANIFEST.json"
LOCK_NAME = ".backup.lock"
STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
SQLITE_MAGIC = b"SQLite format 3\x00"
DB_SUFFIXES = (".sqlite", ".sqlite3", ".db")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
DB_FOLDERS = ("memory", "artifacts")             # only the databases in these are taken
TREE_FOLDERS = ("journal", "config")             # everything in these is taken
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = "~/Backups/valedia-trader"
DEFAULT_KEEP = 14
DEFAULT_LARGE_BYTES = 200 * 1024 * 1024
DEFAULT_LARGE_KEEP = 3
DEFAULT_DB_TIMEOUT = 3600.0                      # per database; the one knob that bounds a stuck lock
CHUNK = 1 << 20
IGNORED_EXAMPLES = 20


class Refused(Exception):
    """No backup can be made; the message says why. Exit 2."""


class SourceError(Exception):
    """One source could not be read or copied; it is named in the manifest. Exit 1."""


class Terminated(BaseException):
    """SIGTERM arrived: clean up the temporary folder and stop."""


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def free_bytes(path):
    return shutil.disk_usage(path).free


def say(msg):
    print(msg, flush=True)


def warn(msg):
    print(msg, file=sys.stderr, flush=True)


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def fsync_path(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_tree(path):
    """Remove a folder or file that lives inside the destination; never follow a symlink."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, ignore_errors=False)


# ------------------------------------------------------------------------------------------ destination
def _inside(child, parent):
    return child == parent or parent in child.parents


def check_destination(root, dest):
    """The absolute destination, or Refused. It may not be the repository, inside it, or a link into it."""
    root_real = Path(os.path.realpath(root))
    root_abs = Path(os.path.abspath(root))
    absolute = Path(os.path.abspath(os.path.expanduser(str(dest))))
    real = Path(os.path.realpath(absolute))
    if _inside(real, root_real) or _inside(absolute, root_abs) or _inside(absolute, root_real):
        how = "is a symlink into the repository" if absolute != real and not _inside(absolute, root_abs) \
            else "is inside (or is) the repository"
        raise Refused(f"destination {absolute} {how} ({root_real}); a backup must live outside the repository, "
                      "on another disk")
    if absolute.exists() and not absolute.is_dir():
        raise Refused(f"destination {absolute} is not a folder")
    return absolute


def nearest_existing(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


# ------------------------------------------------------------------------------------------ scanning
def read_header(path):
    with open(path, "rb") as f:
        return f.read(100)


def classify(path, in_db_folder):
    """'sqlite', 'file' or None (not taken), or raise OSError when it cannot be read to tell."""
    name = os.path.basename(path)
    lower = name.lower()
    if in_db_folder and lower.endswith(SIDECAR_SUFFIXES):
        return None
    if lower.endswith(DB_SUFFIXES):
        return "sqlite"
    if read_header(path)[:16] == SQLITE_MAGIC:
        return "sqlite"
    return None if in_db_folder else "file"


def scan(root):
    """What will be copied: items [(kind, rel, abs, size)], failures, absent folders, ignored files."""
    items, failures, absent = [], [], []
    ignored = {"count": 0, "examples": []}

    def fail(rel, kind, why):
        failures.append({"path": rel, "kind": kind, "error": why})

    def walk(folder, rel, in_db_folder):
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as exc:
            fail(rel, "folder", f"cannot read folder: {exc.strerror or exc}")
            return
        for e in entries:
            erel = f"{rel}/{e.name}"
            try:
                if e.is_symlink():
                    if not os.path.exists(e.path):
                        fail(erel, "symlink", "broken symlink")
                        continue
                    if os.path.isdir(e.path):
                        fail(erel, "symlink", "symlinked folder not followed")
                        continue
                elif e.is_dir(follow_symlinks=False):
                    walk(e.path, erel, in_db_folder)
                    continue
                st = os.stat(e.path)
            except OSError as exc:
                fail(erel, "file", f"cannot stat: {exc.strerror or exc}")
                continue
            if not stat.S_ISREG(st.st_mode):
                fail(erel, "file", "not a regular file (socket, pipe or device)")
                continue
            try:
                kind = classify(e.path, in_db_folder)
            except OSError as exc:
                fail(erel, "file", f"cannot read: {exc.strerror or exc}")
                continue
            if kind is None:
                if not e.name.lower().endswith(SIDECAR_SUFFIXES):
                    ignored["count"] += 1
                    if len(ignored["examples"]) < IGNORED_EXAMPLES:
                        ignored["examples"].append(erel)
                continue
            size = st.st_size
            if kind == "sqlite":                       # the -wal beside it ends up merged into the copy
                with contextlib.suppress(OSError):
                    size += os.stat(os.path.realpath(e.path) + "-wal").st_size
            items.append((kind, erel, e.path, size))

    for top in DB_FOLDERS + TREE_FOLDERS:
        base = os.path.join(root, top)
        if not os.path.lexists(base):
            absent.append(top)
        elif os.path.islink(base) and not os.path.isdir(base):
            fail(top, "folder", "broken symlink")
        elif not os.path.isdir(base):
            fail(top, "folder", "not a folder")
        else:
            walk(base, top, top in DB_FOLDERS)
    return items, failures, absent, ignored


# ------------------------------------------------------------------------------------------ copying
def copy_file(src, dst):
    """Chunked copy; returns (size, sha256 of the copy, changed_during_copy). A read problem is a
    SourceError (named, the run goes on); a write problem is an OSError (the run stops)."""
    try:
        before = os.stat(src)
        fin = open(src, "rb")
    except OSError as exc:
        raise SourceError(f"cannot read: {exc.strerror or exc}") from exc
    with fin:
        with open(dst, "wb") as fout:
            while True:
                try:
                    chunk = fin.read(CHUNK)
                except OSError as exc:
                    raise SourceError(f"read error: {exc.strerror or exc}") from exc
                if not chunk:
                    break
                fout.write(chunk)
            fout.flush()
            os.fsync(fout.fileno())
    os.chmod(dst, stat.S_IMODE(before.st_mode) | 0o600)
    try:
        after = os.stat(src)
        changed = (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)
    except OSError:
        changed = True
    return os.stat(dst).st_size, sha256_file(dst), changed


def ro_uri(path, immutable=False):
    return Path(path).resolve().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")


def _wal_header(header):
    return len(header) >= 20 and header[18] == 2 and header[19] == 2


def _signature(real):
    out = []
    for suffix in ("", "-wal", "-shm"):
        try:
            st = os.stat(real + suffix)
            out.append((st.st_size, st.st_mtime_ns, st.st_ino))
        except FileNotFoundError:
            out.append(None)
    return tuple(out)


def open_source(src, scratch, timeout):
    """Read-only connection to a source database that creates nothing beside it.
    Returns (connection, how)."""
    real = os.path.realpath(src)
    try:
        header = read_header(real)
    except OSError as exc:
        raise SourceError(f"cannot read: {exc.strerror or exc}") from exc
    if header and header[:16] != SQLITE_MAGIC:
        raise SourceError("not a SQLite database (the file does not start with the SQLite header)")
    wal = _wal_header(header)
    has_wal, has_shm = os.path.exists(real + "-wal"), os.path.exists(real + "-shm")
    if wal and not (has_wal and has_shm):
        # Opening this read-only would make SQLite create -wal/-shm beside the source. Nobody has it open,
        # so a quiet copy of the file(s) in a private folder is the database; back that up instead.
        for _attempt in range(5):
            before = _signature(real)
            if before[2] is not None and before[1] is not None:
                break                                   # both sidecars appeared: a writer is live
            snap = os.path.join(scratch, f"snap-{os.getpid()}-{time.monotonic_ns()}")
            os.makedirs(snap)
            target = os.path.join(snap, "db.sqlite")
            try:
                shutil.copyfile(real, target)
                if before[1] is not None:
                    shutil.copyfile(real + "-wal", target + "-wal")
            except OSError as exc:
                raise SourceError(f"cannot read: {exc.strerror or exc}") from exc
            if _signature(real) == before:
                try:
                    return sqlite3.connect(target, timeout=min(timeout, 30)), "online backup of a quiet private copy"
                except sqlite3.Error as exc:
                    raise SourceError(f"cannot open: {exc}") from exc
            time.sleep(0.2)
        else:
            raise SourceError("kept changing while a quiet copy was made")
    try:
        return sqlite3.connect(ro_uri(real), uri=True, timeout=min(timeout, 30)), "online backup, read-only"
    except sqlite3.Error as exc:
        raise SourceError(f"cannot open read-only: {exc}") from exc


def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def inspect_copy(path):
    """Integrity check and row counts of a finished copy, opened read-only and immutable."""
    conn = sqlite3.connect(ro_uri(path, immutable=True), uri=True)
    try:
        rows = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        ok = rows == ["ok"]
        info = {"integrity": "ok" if ok else "; ".join(str(r) for r in rows[:5])}
        tables = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' "
                                    "ESCAPE '\\' ORDER BY name").fetchall():
            try:
                tables[name] = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()[0]
            except sqlite3.Error as exc:
                tables[name] = f"error: {exc}"
        info["tables"] = tables
        info["application_id"] = conn.execute("PRAGMA application_id").fetchone()[0]
        info["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
        return ok, info
    finally:
        conn.close()


def copy_sqlite(src, dst, scratch, db_timeout):
    """Online backup of one database into dst; returns the manifest fields. SourceError when the source
    cannot be read or its copy fails the integrity check."""
    conn, how = open_source(src, scratch, db_timeout)
    deadline = time.monotonic() + db_timeout
    out = sqlite3.connect(dst)
    failed = False
    try:
        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError(f"timed out after {db_timeout:g} s waiting for the database lock")

        try:
            conn.backup(out, pages=-1, progress=progress, sleep=0.25)
        except sqlite3.OperationalError as exc:
            if "disk is full" in str(exc).lower():
                raise OSError(errno.ENOSPC, "No space left on device", str(dst)) from exc
            failed = True
            hint = ""
            if os.path.exists(os.path.realpath(src) + "-journal"):
                hint = " (a -journal file lies beside it: a writer was interrupted; the next writer repairs it)"
            raise SourceError(f"online backup failed: {exc}{hint}") from exc
        except sqlite3.Error as exc:
            failed = True
            raise SourceError(f"online backup failed: {exc}") from exc
        except TimeoutError as exc:
            failed = True
            raise SourceError(str(exc)) from exc
    finally:
        with contextlib.suppress(sqlite3.Error):
            out.close()
        with contextlib.suppress(sqlite3.Error):
            conn.close()
        if failed:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(dst) + suffix).unlink(missing_ok=True)
    for suffix in ("-journal", "-wal", "-shm"):
        Path(str(dst) + suffix).unlink(missing_ok=True)      # only ever our own copy's leftovers
    try:
        ok, info = inspect_copy(dst)
    except sqlite3.Error as exc:
        Path(dst).unlink(missing_ok=True)
        raise SourceError(f"the copy cannot be read back: {exc}") from exc
    if not ok:
        Path(dst).unlink(missing_ok=True)
        raise SourceError(f"the copy failed PRAGMA integrity_check: {info['integrity']}")
    fsync_path(dst)
    try:
        header = read_header(dst)
    except OSError:
        header = b""
    info.update({"size": os.stat(dst).st_size, "sha256": sha256_file(dst), "how": how,
                 "journal_mode_in_file": "wal" if _wal_header(header) else "rollback"})
    return info


# ------------------------------------------------------------------------------------------ run
def make_stamp(dest, now):
    t = now.astimezone(dt.timezone.utc).replace(microsecond=0)
    while True:
        stamp = t.strftime("%Y%m%dT%H%M%SZ")
        if not os.path.lexists(dest / stamp):
            return stamp, t
        t += dt.timedelta(seconds=1)


def clear_leftovers(dest):
    """What a killed run left: temporary, scratch and trash folders. Only dot-folders of ours."""
    for p in sorted(dest.iterdir()):
        if p.name.startswith((".tmp-", ".scratch-", ".trash-")):
            remove_tree(p)


def read_manifest(folder):
    with open(Path(folder) / MANIFEST, "rb") as f:
        return json.load(f)


def write_manifest(folder, manifest):
    folder = Path(folder)
    tmp = folder / (MANIFEST + ".part")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, folder / MANIFEST)


def our_backups(dest):
    """Final folders of ours, oldest first: stamp-named real folders with a readable manifest."""
    found = []
    for p in sorted(Path(dest).iterdir()):
        if STAMP_RE.match(p.name) and not p.is_symlink() and p.is_dir() and (p / MANIFEST).is_file():
            try:
                found.append((p, read_manifest(p)))
            except (OSError, ValueError):
                continue
    return found


def apply_retention(dest, keep, large_bytes, large_keep):
    """Returns a list of problems (empty when all went well)."""
    problems = []
    backups = our_backups(dest)
    newest_first = list(reversed(backups))
    keep_set = {p.name for p, _ in newest_first[:keep]}
    last_complete = next((p.name for p, m in newest_first if m.get("status") == "complete"), None)
    if last_complete:
        keep_set.add(last_complete)
    for p, _m in newest_first:
        if p.name in keep_set:
            continue
        trash = dest / f".trash-{p.name}-{os.getpid()}"
        try:
            os.rename(p, trash)                       # atomic: never half of it under a final name
            shutil.rmtree(trash)
        except OSError as exc:
            problems.append(f"retention: cannot delete old backup {p.name}: {exc}")
    seen = {}
    for p, m in [(p, m) for p, m in newest_first if p.name in keep_set]:
        changed = False
        for e in m.get("files", []):
            rel = e.get("path")
            copy = p / rel
            if e.get("pruned"):
                with contextlib.suppress(OSError):    # a kill between manifest and unlink: finish the job
                    copy.unlink()
                continue
            if e.get("size", 0) <= large_bytes or not copy.is_file():
                continue
            seen[rel] = seen.get(rel, 0) + 1
            if seen[rel] > large_keep:
                e["pruned"] = "older than the newest %d copies of a file over %d bytes" % (large_keep, large_bytes)
                e["pruned_utc"] = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                changed = True
        if changed:
            try:
                write_manifest(p, m)                  # first the manifest, then the files
                for e in m["files"]:
                    if e.get("pruned"):
                        with contextlib.suppress(FileNotFoundError):
                            (p / e["path"]).unlink()
            except OSError as exc:
                problems.append(f"retention: cannot prune large files of {p.name}: {exc}")
    return problems


def run_backup(root, dest, *, keep=DEFAULT_KEEP, large_bytes=DEFAULT_LARGE_BYTES, large_keep=DEFAULT_LARGE_KEEP,
               db_timeout=DEFAULT_DB_TIMEOUT):
    root = Path(os.path.abspath(os.path.expanduser(str(root))))
    if not root.is_dir():
        raise Refused(f"repository {root} is not a folder")
    dest = check_destination(root, dest)
    items, failures, absent, ignored = scan(root)
    if not items and not failures:
        raise Refused(f"nothing to back up: no database, journal or config file found under {root}")
    total = sum(size for _k, _r, _a, size in items)
    free = free_bytes(nearest_existing(dest))
    if free < 2 * total:
        raise Refused(f"not enough free space at {dest}: {human(free)} free, but a backup needs twice what is "
                      f"copied ({human(total)} x 2 = {human(2 * total)}); free some space or pick another disk")
    dest.mkdir(parents=True, exist_ok=True)
    lock = open(dest / LOCK_NAME, "a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refused(f"another backup run holds the lock {dest / LOCK_NAME}; try again when it is done") from None
        clear_leftovers(dest)
        return _locked_run(root, dest, items, failures, absent, ignored, keep, large_bytes, large_keep, db_timeout)
    finally:
        lock.close()


def _locked_run(root, dest, items, failures, absent, ignored, keep, large_bytes, large_keep, db_timeout):
    started = utcnow()
    stamp, created = make_stamp(dest, started)
    tmp = dest / f".tmp-{stamp}-{os.getpid()}"
    scratch = dest / f".scratch-{stamp}-{os.getpid()}"
    renamed = False

    def on_term(_signum, _frame):
        raise Terminated()

    old = {}
    for sig in (signal.SIGTERM,):
        with contextlib.suppress(ValueError):          # only the main thread may set handlers
            old[sig] = signal.signal(sig, on_term)
    try:
        tmp.mkdir()
        scratch.mkdir()
        files = []
        failures = list(failures)
        for kind, rel, src, _size in items:
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if kind == "sqlite":
                    try:
                        info = copy_sqlite(src, target, str(scratch), db_timeout)
                    finally:
                        for child in scratch.iterdir():     # a quiet private copy is needed only for its turn
                            remove_tree(child)
                    entry = {"path": rel, "kind": "sqlite", **info}
                else:
                    size, digest, changed = copy_file(src, target)
                    entry = {"path": rel, "kind": "file", "size": size, "sha256": digest}
                    if changed:
                        entry["changed_during_copy"] = True
                files.append(entry)
            except SourceError as exc:
                failures.append({"path": rel, "kind": kind, "error": str(exc)})
                with contextlib.suppress(OSError):
                    target.unlink()
            except OSError as exc:
                raise Refused(f"backup stopped: cannot write the copy of {rel} under {dest}: "
                              f"{exc.strerror or exc}; nothing was kept") from exc
        if not files:
            detail = "; ".join(f"{f['path']}: {f['error']}" for f in failures[:5])
            raise Refused(f"no backup could be made: nothing could be copied ({detail})")
        for folder, _dn, _fn in os.walk(tmp):
            with contextlib.suppress(OSError):
                fsync_path(folder)
        manifest = {
            "format": FORMAT, "tool": VERSION, "created_utc": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stamp": stamp, "root": str(root), "sqlite_version": sqlite3.sqlite_version,
            "status": "complete" if not failures else "partial",
            "absent_folders": absent,
            "not_backed_up": {"non_database_files_in_memory_and_artifacts": ignored["count"],
                              "examples": ignored["examples"]},
            "files": files, "failures": failures,
        }
        write_manifest(tmp, manifest)
        fsync_path(tmp)
        os.rename(tmp, dest / stamp)                   # the only moment the backup gets its final name
        renamed = True
        with contextlib.suppress(OSError):
            fsync_path(dest)
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        with contextlib.suppress(OSError):
            remove_tree(scratch)
        if not renamed:
            with contextlib.suppress(OSError):
                remove_tree(tmp)
    final = dest / stamp
    problems = apply_retention(dest, keep, large_bytes, large_keep)
    nbytes = sum(e["size"] for e in files)
    say(f"backup {final} {manifest['status']}: {len(files)} files, {human(nbytes)}")
    if os.stat(root).st_dev == os.stat(dest).st_dev:
        warn(f"note: {dest} is on the same disk as the repository; a backup there does not survive the disk")
    for f in failures:
        warn(f"FAILED {f['path']}: {f['error']}")
    for p in problems:
        warn(p)
    return 1 if failures or problems else 0


# ------------------------------------------------------------------------------------------ verify
def verify_backup(folder):
    """(exit code, lines): 0 matches its manifest and is complete, 1 mismatch or PARTIAL, 2 not a backup."""
    folder = Path(folder)
    if not folder.is_dir() or not (folder / MANIFEST).is_file():
        return 2, [f"{folder} is not a backup folder (no {MANIFEST})"]
    try:
        m = read_manifest(folder)
        if m.get("format") != FORMAT or not isinstance(m.get("files"), list):
            raise ValueError("unknown manifest format")
    except (OSError, ValueError) as exc:
        return 2, [f"{folder}: unreadable manifest: {exc}"]
    lines, bad = [], False
    listed = set()
    pruned = []
    for e in m["files"]:
        rel = e["path"]
        listed.add(rel)
        copy = folder / rel
        if e.get("pruned"):
            pruned.append(rel)
            continue
        if not copy.is_file():
            lines.append(f"MISSING {rel}")
            bad = True
            continue
        size = copy.stat().st_size
        if size != e["size"]:
            lines.append(f"CHANGED {rel}: size {size} != manifest {e['size']}")
            bad = True
            continue
        if sha256_file(copy) != e["sha256"]:
            lines.append(f"CHANGED {rel}: sha256 does not match the manifest")
            bad = True
            continue
        if e.get("kind") == "sqlite":
            try:
                ok, info = inspect_copy(copy)
            except sqlite3.Error as exc:
                lines.append(f"INTEGRITY {rel}: integrity_check could not run: {exc}")
                bad = True
                continue
            if not ok:
                lines.append(f"INTEGRITY {rel}: integrity_check says {info['integrity']}")
                bad = True
    for p in sorted(folder.rglob("*")):
        if p.is_file() or p.is_symlink():
            rel = p.relative_to(folder).as_posix()
            if rel != MANIFEST and rel not in listed:
                lines.append(f"UNLISTED {rel}: in the backup folder but not in the manifest")
                bad = True
    for rel in pruned:
        lines.append(f"pruned {rel}: its copy was dropped by the large-file retention (manifest keeps its hash)")
    partial = m.get("status") != "complete" or bool(m.get("failures"))
    if partial:
        lines.append("PARTIAL: sources that failed when this backup was made:")
        for f in m.get("failures", []):
            lines.append(f"  {f['path']}: {f['error']}")
    if bad:
        lines.insert(0, f"verify {folder.name}: DOES NOT MATCH ITS MANIFEST")
        return 1, lines
    lines.insert(0, f"verify {folder.name}: {len([e for e in m['files'] if not e.get('pruned')])} files match "
                    f"their manifest" + (" (backup is PARTIAL)" if partial else ""))
    return (1 if partial else 0), lines


# ------------------------------------------------------------------------------------------ list
def list_backups(dest):
    dest = Path(os.path.expanduser(str(dest)))
    if not dest.is_dir():
        return ["no backups: " + str(dest) + " does not exist"]
    rows, total = [], 0
    for p, m in our_backups(dest):
        present = [e for e in m.get("files", []) if not e.get("pruned") and (p / e["path"]).is_file()]
        size = sum((p / e["path"]).stat().st_size for e in present)
        total += size
        nfail = len(m.get("failures", []))
        npruned = len([e for e in m.get("files", []) if e.get("pruned")])
        extra = (f"  {nfail} failed" if nfail else "") + (f"  {npruned} pruned" if npruned else "")
        rows.append(f"{p.name}  {m.get('status', '?'):8}  {len(present):4} files  {human(size):>10}{extra}")
    if not rows:
        return [f"no backups in {dest}"]
    return rows + [f"{len(rows)} backups, total {human(total)} in {dest}"]


# ------------------------------------------------------------------------------------------ restore
def restore_backup(folder, to, root):
    folder = Path(os.path.abspath(os.path.expanduser(str(folder))))
    root_real = Path(os.path.realpath(root))
    target = Path(os.path.abspath(os.path.expanduser(str(to))))
    real = Path(os.path.realpath(target))
    if _inside(real, root_real) or _inside(target, Path(os.path.abspath(root))) or _inside(target, root_real):
        raise Refused(f"will not restore into the repository ({root_real}); choose an empty folder elsewhere")
    if _inside(real, Path(os.path.realpath(folder))):
        raise Refused("will not restore into the backup folder itself")
    if target.exists():
        if not target.is_dir():
            raise Refused(f"{target} is not a folder")
        if any(target.iterdir()):
            raise Refused(f"{target} is not empty; restore needs an empty folder")
    code, lines = verify_backup(folder)
    if code == 2 or any(x.startswith(("MISSING", "CHANGED", "INTEGRITY", "UNLISTED")) for x in lines):
        raise Refused(f"{folder} does not pass verify, nothing restored:\n  " + "\n  ".join(lines[:10]))
    m = read_manifest(folder)
    work = target.parent / f"{target.name}.restoring-{os.getpid()}"
    work.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir()
    try:
        missing = []
        for e in m["files"]:
            if e.get("pruned"):
                missing.append(e["path"])
                continue
            dst = work / e["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / e["path"], dst)
            os.chmod(dst, stat.S_IMODE(os.stat(folder / e["path"]).st_mode) | 0o600)
            if sha256_file(dst) != e["sha256"]:
                raise OSError(f"{e['path']}: the restored copy does not match the manifest")
        if target.exists():
            target.rmdir()                               # empty: checked above
        os.rename(work, target)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    say(f"restored {len(m['files']) - len(missing)} files from {folder.name} into {target}")
    for rel in missing:
        say(f"not restored: {rel} (pruned: its copy was dropped by the large-file retention)")
    for f in m.get("failures", []):
        say(f"not in this backup (failed when it was made): {f['path']}: {f['error']}")
    return 0


# ------------------------------------------------------------------------------------------ cli
def build_parser():
    ap = argparse.ArgumentParser(prog="backup.py", description="Backups of the trader's memory.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="make a new backup")
    r.add_argument("--root", default=str(DEFAULT_ROOT), help="the repository (default: this one)")
    r.add_argument("--dest", default=DEFAULT_DEST, help=f"backup folder (default {DEFAULT_DEST})")
    r.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="backups to keep (default 14)")
    r.add_argument("--large-bytes", type=int, default=DEFAULT_LARGE_BYTES,
                   help="a file larger than this keeps only its newest copies (default 200 MB)")
    r.add_argument("--large-keep", type=int, default=DEFAULT_LARGE_KEEP, help="copies kept of a large file (3)")
    r.add_argument("--db-timeout", type=float, default=DEFAULT_DB_TIMEOUT,
                   help="seconds to wait for one database's lock before naming it failed (default 3600)")
    v = sub.add_parser("verify", help="re-check a backup against its manifest")
    v.add_argument("backup")
    ls = sub.add_parser("list", help="show the backups with sizes")
    ls.add_argument("--dest", default=DEFAULT_DEST)
    rs = sub.add_parser("restore", help="copy a backup out into an empty folder")
    rs.add_argument("backup")
    rs.add_argument("--to", required=True)
    rs.add_argument("--root", default=str(DEFAULT_ROOT), help="the repository, which is never written to")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "run":
            if args.keep < 1 or args.large_keep < 1:
                raise Refused("--keep and --large-keep must be at least 1")
            return run_backup(args.root, args.dest, keep=args.keep, large_bytes=args.large_bytes,
                              large_keep=args.large_keep, db_timeout=args.db_timeout)
        if args.cmd == "verify":
            code, lines = verify_backup(args.backup)
            for line in lines:
                (warn if code == 2 else say)(line)
            return code
        if args.cmd == "list":
            for line in list_backups(args.dest):
                say(line)
            return 0
        return restore_backup(args.backup, args.to, args.root)
    except Refused as exc:
        warn(f"backup: {exc}")
        return 2
    except Terminated:
        warn("backup: stopped by SIGTERM; the temporary folder was removed, no backup was made")
        return 2
    except KeyboardInterrupt:
        warn("backup: interrupted; the temporary folder was removed, no backup was made")
        return 2
    except OSError as exc:
        warn(f"backup: failed: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
