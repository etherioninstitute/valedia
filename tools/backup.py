#!/usr/bin/env python3
"""Backups of the trader's memory (roadmap 5.1). Standard library only; no trading imports.

    python tools/backup.py run [--root REPO] [--dest FOLDER]
    python tools/backup.py verify BACKUP_FOLDER
    python tools/backup.py list [--dest FOLDER]
    python tools/backup.py restore BACKUP_FOLDER --to EMPTY_FOLDER

What `run` takes: every SQLite database under memory/, artifacts/ and state/ (found by content, not only
by name), and the whole of journal/ and config/. In every one of these folders a file is classified by its
content: a SQLite database is copied with SQLite's own online backup from a read-only connection, so a
database that is being written at that moment comes out as one consistent snapshot, with its triggers,
application_id and user_version carried unchanged. The -wal, -shm and -journal files that belong to a
database are never copied (the snapshot already holds what was in them); a restored database therefore
opens read-write to exactly the rows its manifest entry counts. Links are never followed: a symbolic link
inside a source folder is not copied and not descended into, it is named in the manifest
(`link_not_followed`, the link's own path only) and on stderr, and does not change the exit code. A hard
link is an ordinary file. A plain file that changes while it is copied is copied again (3 tries at most);
if it still changed the last copy is kept, flagged `changed_during_copy` and named on stderr. A file with a
database name that is not a database is kept as a plain file, flagged `not_a_database`, and the run exits 1.

What a source is spared, and what it is not. Nothing is created or removed beside a source and no source
file is written by us, with one exception that SQLite itself makes, which we cannot prevent: reading a
database in WAL mode (even an idle one with a leftover -wal) lets SQLite rewrite that database's -shm file
(its shared-memory index of the log), and nothing else beside a source changes. A WAL database with no
-wal/-shm beside it cannot be opened even read-only without SQLite creating those two files; such a
database, which by definition nobody has open, is first copied as a quiet file into a private scratch
folder inside the destination and that copy is backed up; if a writer shows up meanwhile, its side files
appear and the database is read in place, read-only, which creates nothing.

One limit to know. A rollback-mode (non-WAL) database holds its writers out for as long as its copy takes
(measured: 0.36 s at 40 MB and 0.47 s at 195 MB on this disk); in WAL mode writers are never held out. A
rollback-mode store that grows past about 1 GB while a process writes to it continuously needs another
plan (convert it to WAL, or back it up from the writer's side). The order-book collector is in WAL mode.
A second limit: one database is waited for at most --db-timeout seconds (default 3600) for its lock.

The copy goes into a working folder `<dest>/.valedia-backup-tmp-<stamp>-<pid>/`, marked by a file named
`.valedia-backup-work` written as the first thing in it, and is renamed to `<dest>/<stamp>/` (UTC,
YYYYMMDDTHHMMSSZ) in one step when everything is copied and every database copy passed its integrity check.
A run that is killed leaves only such working folders, which the next run removes. Only a folder whose
name is exactly `.valedia-backup-(tmp|scratch|trash)-<stamp>-<pid>` and which carries the mark file, holds a
MANIFEST.json, or is empty is ever removed as ours: a user's file or folder that merely starts with `.tmp-`
(or any other name) is left alone. A source that cannot be read or whose copy fails its integrity check is
named in the manifest (and on stderr), left out of the copy, and makes the exit code 1; everything else is
still backed up.

Exit codes of `run`: 0 everything copied and checked; 1 backup made but something was named as failed;
2 no backup could be made (refused destination, too little free space, another run holds the lock, a
write error at the destination, nothing to back up). verify: 0 matches its manifest and is complete,
1 does not match or is PARTIAL, 2 not a backup. list: 0. restore: 0 done, 2 refused or failed. A run stopped
by a signal exits 2 when no backup was published and 1 when the final folder already exists (the message says
which, by looking at the disk); an unexpected error is a plain message and exit 2, never a traceback.

The repository guard works by identity: the destination and the restore target, and each of their existing
ancestors, are compared with the repository root by device and inode after links are resolved, so any
spelling of a place inside the repository (letter case, trailing slash, `.` and `..`, a link, a Unicode
form) is refused, and nothing is created before the refusal. A manifest is not trusted about paths: an
absolute path, a `..` part or a path that would land outside the target makes verify fail and restore
refuse before it writes anything. restore works inside its target folder only.

Retention (after a successful rename): the newest 14 backups are kept (the newest complete one is never
dropped, so a run of partial backups cannot push out the last whole one); of a file larger than 200 MB
only the newest 3 copies are kept, older backups keep their manifest (the entry is marked pruned) and
their other files. Only folders named like a stamp, directly inside the destination, that are not links
and hold a readable MANIFEST.json of the expected shape, are ever deleted or counted; one whose manifest
is unreadable is shown by `list` as unreadable and left alone. A folder is first renamed to a working
`trash` folder so a kill never leaves half of an old backup under a final name.
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

VERSION = "VALEDIA-BACKUP-2.0"
FORMAT = "valedia-trader-backup-1"
MANIFEST = "MANIFEST.json"
MANIFEST_PART = MANIFEST + ".part"
LOCK_NAME = ".backup.lock"
STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
WORK_PREFIX = ".valedia-backup-"
WORK_MARK = ".valedia-backup-work"
WORK_RE = re.compile(r"^\.valedia-backup-(tmp|scratch|trash)-\d{8}T\d{6}Z-\d+$")
RESTORE_WORK = ".valedia-restore-work"
SQLITE_MAGIC = b"SQLite format 3\x00"
DB_SUFFIXES = (".sqlite", ".sqlite3", ".db")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
DB_FOLDERS = ("memory", "artifacts", "state")    # only the databases in these are taken
TREE_FOLDERS = ("journal", "config")             # everything in these is taken
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = "~/Backups/valedia-trader"
DEFAULT_KEEP = 14
DEFAULT_LARGE_BYTES = 200 * 1024 * 1024
DEFAULT_LARGE_KEEP = 3
DEFAULT_DB_TIMEOUT = 3600.0                      # per database; the one knob that bounds a stuck lock
COPY_TRIES = 3
CHUNK = 1 << 20
IGNORED_EXAMPLES = 20
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Refused(Exception):
    """No backup can be made; the message says why. Exit 2."""


class SourceError(Exception):
    """One source could not be read or copied; it is named in the manifest. Exit 1."""


class NotADatabase(SourceError):
    """A file with a database name that does not start with the SQLite header."""


class ManifestError(Exception):
    """A MANIFEST.json that is not an object of the expected shape."""


class Terminated(BaseException):
    """SIGTERM arrived: clean up the working folder and stop."""


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


# ------------------------------------------------------------------------------------------ places
def _absolute(path):
    raw = os.path.expanduser(str(path))
    return raw if os.path.isabs(raw) else os.path.join(os.getcwd(), raw)


def same_place_as_or_inside(path, folder):
    """True when `path`, or any of its existing ancestors, is `folder` itself (device and inode), however
    `path` is spelled. Links are resolved first; the lexical spelling is checked too."""
    for candidate in (os.path.realpath(_absolute(path)), os.path.abspath(_absolute(path))):
        cur = candidate
        while True:
            with contextlib.suppress(OSError):
                if os.path.exists(cur) and os.path.samefile(cur, folder):
                    return True
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return False


def check_destination(root, dest):
    """The resolved destination, or Refused. It may not be the repository, inside it, or a link into it."""
    if same_place_as_or_inside(dest, root):
        raise Refused(f"destination {dest} is inside (or is) the repository {root}, or is a link into it; "
                      "a backup must live outside the repository, on another disk")
    resolved = Path(os.path.realpath(_absolute(dest)))
    if resolved.exists() and not resolved.is_dir():
        raise Refused(f"destination {resolved} is not a folder")
    return resolved


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
    lower = os.path.basename(path).lower()
    if lower.endswith(DB_SUFFIXES):
        return "sqlite"
    if read_header(path)[:16] == SQLITE_MAGIC:
        return "sqlite"
    return None if in_db_folder else "file"


def sidecar_suffix(name):
    lower = name.lower()
    return next((s for s in SIDECAR_SUFFIXES if lower.endswith(s)), None)


def belongs_to_database(base_entry):
    """Is this folder entry a database (so that its -wal/-shm/-journal are not files of their own)?"""
    if base_entry.name.lower().endswith(DB_SUFFIXES):
        return True
    try:
        return read_header(base_entry.path)[:16] == SQLITE_MAGIC
    except OSError:
        return True                                    # cannot tell: it is named as failed elsewhere


def scan(root):
    """What will be copied: items [(kind, rel, abs, size)], failures, absent folders, ignored files, links."""
    items, failures, absent, links = [], [], [], []
    ignored = {"count": 0, "examples": []}

    def fail(rel, kind, why):
        failures.append({"path": rel, "kind": kind, "error": why})

    def walk(folder, rel, in_db_folder):
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as exc:
            fail(rel, "folder", f"cannot read folder: {exc.strerror or exc}")
            return
        by_name = {e.name: e for e in entries}
        for e in entries:
            erel = f"{rel}/{e.name}"
            suffix = sidecar_suffix(e.name)
            try:
                if e.is_symlink():
                    links.append(erel)                 # never followed, never descended into
                    continue
                if e.is_dir(follow_symlinks=False):
                    walk(e.path, erel, in_db_folder)
                    continue
                if suffix:
                    base = by_name.get(e.name[:-len(suffix)])
                    if in_db_folder or (base is not None and belongs_to_database(base)):
                        continue                       # a database's own side file: the snapshot holds it
                st = e.stat(follow_symlinks=False)
            except FileNotFoundError:
                if not suffix:
                    fail(erel, "file", "vanished while the folder was being scanned")
                continue                               # a side file that vanished (a writer finished): not an error
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
                ignored["count"] += 1
                if len(ignored["examples"]) < IGNORED_EXAMPLES:
                    ignored["examples"].append(erel)
                continue
            size = st.st_size
            if kind == "sqlite":                       # the -wal beside it ends up merged into the copy
                with contextlib.suppress(OSError):
                    size += os.stat(e.path + "-wal").st_size
            items.append((kind, erel, e.path, size))

    for top in DB_FOLDERS + TREE_FOLDERS:
        base = os.path.join(root, top)
        if not os.path.lexists(base):
            absent.append(top)
        elif os.path.islink(base):
            links.append(top)
        elif not os.path.isdir(base):
            fail(top, "folder", "not a folder")
        else:
            walk(base, top, top in DB_FOLDERS)
    return items, failures, absent, ignored, links


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


def copy_plain(src, dst):
    """copy_file, again while the source changed under it (COPY_TRIES at most); the last copy is kept."""
    for _try in range(COPY_TRIES):
        size, digest, changed = copy_file(src, dst)
        if not changed:
            break
    return size, digest, changed


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
        raise NotADatabase("not a SQLite database (the file does not start with the SQLite header)")
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
    cannot be read or its copy fails the integrity check; NotADatabase when it is not a database."""
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


# ------------------------------------------------------------------------------------------ manifest
def read_manifest(folder):
    """The validated manifest of a backup folder, or ManifestError (never any other exception)."""
    try:
        with open(Path(folder) / MANIFEST, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ManifestError(f"unreadable manifest: {type(exc).__name__}: {str(exc)[:120]}") from exc
    return validate_manifest(data)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_manifest(m):
    if not isinstance(m, dict):
        raise ManifestError("the manifest is not a JSON object")
    if m.get("format") != FORMAT:
        raise ManifestError("not a manifest of this tool (unknown format)")
    if not isinstance(m.get("files"), list):
        raise ManifestError("'files' is not a list")
    if m.get("status") not in ("complete", "partial"):
        raise ManifestError("'status' is not complete or partial")
    failures = m.get("failures", [])
    if not isinstance(failures, list) or not all(
            isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("error"), str)
            for f in failures):
        raise ManifestError("'failures' is not a list of {path, error}")
    for key in ("absent_folders", "link_not_followed"):
        if key in m and (not isinstance(m[key], list) or not all(isinstance(x, str) for x in m[key])):
            raise ManifestError(f"'{key}' is not a list of strings")
    for e in m["files"]:
        if not isinstance(e, dict):
            raise ManifestError("a 'files' entry is not an object")
        if not isinstance(e.get("path"), str):
            raise ManifestError("a 'files' entry has no text 'path'")
        if e.get("kind") not in ("sqlite", "file"):
            raise ManifestError(f"{e['path']}: 'kind' is not sqlite or file")
        if not _is_int(e.get("size")) or e["size"] < 0:
            raise ManifestError(f"{e['path']}: 'size' is not a whole number")
        if not isinstance(e.get("sha256"), str) or not HEX64.match(e["sha256"]):
            raise ManifestError(f"{e['path']}: 'sha256' is not a SHA-256")
        if "tables" in e and (not isinstance(e["tables"], dict)):
            raise ManifestError(f"{e['path']}: 'tables' is not an object")
    return m


def bad_entry_path(rel):
    """Why a manifest path is not safe to join under a folder, or None."""
    if not rel or rel != rel.strip():
        return "empty or padded"
    if "\x00" in rel:
        return "contains a NUL"
    if rel.startswith("/") or os.path.isabs(rel):
        return "absolute"
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "has an empty, '.' or '..' part"
    if parts[0] in (RESTORE_WORK, MANIFEST, MANIFEST_PART):
        return "names a reserved file"
    return None


def write_manifest(folder, manifest):
    folder = Path(folder)
    tmp = folder / MANIFEST_PART
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, folder / MANIFEST)


def our_backups(dest):
    """(valid, unreadable): stamp-named real folders with a MANIFEST.json, oldest first. `valid` is
    [(folder, manifest)]; `unreadable` is [(folder, reason)] and is never deleted or counted."""
    valid, unreadable = [], []
    for p in sorted(Path(dest).iterdir()):
        if STAMP_RE.match(p.name) and not p.is_symlink() and p.is_dir() and (p / MANIFEST).is_file():
            try:
                valid.append((p, read_manifest(p)))
            except ManifestError as exc:
                unreadable.append((p, str(exc)))
    return valid, unreadable


# ------------------------------------------------------------------------------------------ run
def make_stamp(dest, now):
    t = now.astimezone(dt.timezone.utc).replace(microsecond=0)
    while True:
        stamp = t.strftime("%Y%m%dT%H%M%SZ")
        if not os.path.lexists(dest / stamp):
            return stamp, t
        t += dt.timedelta(seconds=1)


def is_our_work_folder(p):
    """A working folder of this tool: the exact name, a real folder, and the mark, a manifest or nothing in it."""
    if not WORK_RE.match(p.name) or p.is_symlink() or not p.is_dir():
        return False
    return (p / WORK_MARK).exists() or (p / MANIFEST).exists() or not any(p.iterdir())


def remove_work_folder(p):
    """Remove one of our working folders so that a kill half way still leaves it recognisable as ours: the
    mark is put in first and taken out last (a folder that holds a MANIFEST.json is recognised too)."""
    p = Path(p)
    marker = p / WORK_MARK
    if not marker.exists():
        marker.write_text("being removed\n")
    for child in sorted(p.iterdir()):
        if child.name != WORK_MARK:
            remove_tree(child)
    marker.unlink()
    p.rmdir()


def clear_leftovers(dest):
    """What a killed run left: our own working folders, and nothing else."""
    for p in sorted(dest.iterdir()):
        if is_our_work_folder(p):
            remove_work_folder(p)


def mark_work_folder(p):
    p.mkdir()
    with open(p / WORK_MARK, "w") as f:
        f.write("working folder of tools/backup.py; safe to delete when no backup run is active\n")


def apply_retention(dest, keep, large_bytes, large_keep):
    """Returns a list of problems (empty when all went well)."""
    problems = []
    backups, _unreadable = our_backups(dest)
    newest_first = list(reversed(backups))
    keep_set = {p.name for p, _ in newest_first[:keep]}
    last_complete = next((p.name for p, m in newest_first if m["status"] == "complete"), None)
    if last_complete:
        keep_set.add(last_complete)
    for p, _m in newest_first:
        if p.name in keep_set:
            continue
        trash = dest / f"{WORK_PREFIX}trash-{p.name}-{os.getpid()}"
        try:
            os.rename(p, trash)                       # atomic: never half of it under a final name
            remove_work_folder(trash)
        except OSError as exc:
            problems.append(f"retention: cannot delete old backup {p.name}: {exc}")
    seen = {}
    for p, m in [(p, m) for p, m in newest_first if p.name in keep_set]:
        with contextlib.suppress(OSError):            # a kill during a prune left it: not part of the backup
            (p / MANIFEST_PART).unlink()
        changed = False
        for e in m["files"]:
            rel = e["path"]
            if bad_entry_path(rel):
                continue
            copy = p / rel
            if e.get("pruned"):
                with contextlib.suppress(OSError):    # a kill between manifest and unlink: finish the job
                    copy.unlink()
                continue
            if e["size"] <= large_bytes or not copy.is_file():
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
                    if e.get("pruned") and not bad_entry_path(e["path"]):
                        with contextlib.suppress(FileNotFoundError):
                            (p / e["path"]).unlink()
            except OSError as exc:
                problems.append(f"retention: cannot prune large files of {p.name}: {exc}")
    return problems


def run_backup(root, dest, *, keep=DEFAULT_KEEP, large_bytes=DEFAULT_LARGE_BYTES, large_keep=DEFAULT_LARGE_KEEP,
               db_timeout=DEFAULT_DB_TIMEOUT):
    root = Path(os.path.realpath(_absolute(root)))
    if not root.is_dir():
        raise Refused(f"repository {root} is not a folder")
    dest = check_destination(root, dest)
    items, failures, absent, ignored, links = scan(root)
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
        return _locked_run(root, dest, items, failures, absent, ignored, links, keep, large_bytes, large_keep,
                           db_timeout)
    finally:
        lock.close()


def _locked_run(root, dest, items, failures, absent, ignored, links, keep, large_bytes, large_keep, db_timeout):
    stamp, created = make_stamp(dest, utcnow())
    final = dest / stamp
    tmp = dest / f"{WORK_PREFIX}tmp-{stamp}-{os.getpid()}"
    scratch = dest / f"{WORK_PREFIX}scratch-{stamp}-{os.getpid()}"
    renamed = False

    def on_term(_signum, _frame):
        raise Terminated()

    old = {}
    with contextlib.suppress(ValueError):              # only the main thread may set handlers
        old[signal.SIGTERM] = signal.signal(signal.SIGTERM, on_term)
    try:
        try:
            mark_work_folder(tmp)
            mark_work_folder(scratch)
            files, notes = [], []
            failures = list(failures)
            for kind, rel, src, _size in items:
                target = tmp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    try:
                        if kind == "sqlite":
                            try:
                                info = copy_sqlite(src, target, str(scratch), db_timeout)
                            finally:
                                for child in scratch.iterdir():     # a quiet private copy is needed for its turn only
                                    if child.name != WORK_MARK:
                                        remove_tree(child)
                            entry = {"path": rel, "kind": "sqlite", **info}
                        else:
                            size, digest, changed = copy_plain(src, target)
                            entry = {"path": rel, "kind": "file", "size": size, "sha256": digest}
                            if changed:
                                entry["changed_during_copy"] = True
                                notes.append(f"{rel}: changed while it was copied, even on the last of "
                                             f"{COPY_TRIES} tries; the last copy is kept (flagged in the manifest)")
                    except NotADatabase as exc:
                        size, digest, changed = copy_plain(src, target)
                        entry = {"path": rel, "kind": "file", "size": size, "sha256": digest,
                                 "not_a_database": True}
                        if changed:
                            entry["changed_during_copy"] = True
                        failures.append({"path": rel, "kind": "not_a_database",
                                         "error": f"{exc}; kept as a plain file"})
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
                "absent_folders": absent, "link_not_followed": sorted(links),
                "not_backed_up": {"non_database_files_in_memory_artifacts_state": ignored["count"],
                                  "examples": ignored["examples"]},
                "files": files, "failures": failures,
            }
            write_manifest(tmp, manifest)
            (tmp / WORK_MARK).unlink()                 # the manifest is now what marks the folder as ours
            fsync_path(tmp)
            os.rename(tmp, final)                      # the only moment the backup gets its final name
            renamed = True
            with contextlib.suppress(OSError):
                fsync_path(dest)
        finally:
            with contextlib.suppress(OSError):
                remove_work_folder(scratch)
            if not renamed:
                with contextlib.suppress(OSError):
                    remove_work_folder(tmp)
        problems = apply_retention(dest, keep, large_bytes, large_keep)
        nbytes = sum(e["size"] for e in files)
        say(f"backup {final} {manifest['status']}: {len(files)} files, {human(nbytes)}")
        if os.stat(root).st_dev == os.stat(dest).st_dev:
            warn(f"note: {dest} is on the same disk as the repository; a backup there does not survive the disk")
        for link in sorted(links):
            warn(f"link not followed: {link}")
        for note in notes:
            warn(f"NOTE {note}")
        for f in failures:
            warn(f"FAILED {f['path']}: {f['error']}")
        for p in problems:
            warn(p)
        return 1 if failures or problems else 0
    except (Terminated, KeyboardInterrupt) as exc:
        exc.published = final.is_dir()                 # looked at on disk, not remembered in a flag
        exc.final = final
        raise
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


# ------------------------------------------------------------------------------------------ verify
def verify_backup(folder):
    """(exit code, lines): 0 matches its manifest and is complete, 1 mismatch or PARTIAL, 2 not a backup."""
    folder = Path(folder)
    if not folder.is_dir() or not (folder / MANIFEST).is_file():
        return 2, [f"{folder} is not a backup folder (no {MANIFEST})"]
    try:
        m = read_manifest(folder)
    except ManifestError as exc:
        return 2, [f"{folder}: {exc}"]
    folder_real = os.path.realpath(folder)
    lines, bad = [], False
    listed, sqlite_paths, pruned = set(), set(), []
    for e in m["files"]:
        rel = e["path"]
        why = bad_entry_path(rel)
        if why:
            lines.append(f"BADPATH {rel!r}: the manifest path is {why}; not trusted")
            bad = True
            continue
        listed.add(rel)
        copy = folder / rel
        real = os.path.realpath(copy)
        if real != folder_real and not real.startswith(folder_real + os.sep) and not copy.is_symlink():
            lines.append(f"BADPATH {rel!r}: would land outside the backup folder")
            bad = True
            continue
        if e["kind"] == "sqlite":
            sqlite_paths.add(rel)
        if e.get("pruned"):
            pruned.append(rel)
            continue
        if copy.is_symlink():
            lines.append(f"LINK {rel}: is a link, not a copy")
            bad = True
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
        if e["kind"] == "sqlite":
            try:
                ok, info = inspect_copy(copy)
            except sqlite3.Error as exc:
                lines.append(f"INTEGRITY {rel}: integrity_check could not run: {exc}")
                bad = True
                continue
            if not ok:
                lines.append(f"INTEGRITY {rel}: integrity_check says {info['integrity']}")
                bad = True
            elif "tables" in e and e["tables"] != info["tables"]:
                lines.append(f"COUNTS {rel}: rows by table are {info['tables']}, the manifest says {e['tables']}")
                bad = True
    for p in sorted(folder.rglob("*")):
        if p.is_file() or p.is_symlink():
            rel = p.relative_to(folder).as_posix()
            if rel in (MANIFEST, MANIFEST_PART):
                continue                               # a half-written manifest of a killed prune is not data
            suffix = sidecar_suffix(rel)
            if suffix and rel[:-len(suffix)] in sqlite_paths:
                lines.append(f"SIDEFILE {rel}: a side file beside a database copy; a restore would replay it")
                bad = True
            elif rel not in listed:
                lines.append(f"UNLISTED {rel}: in the backup folder but not in the manifest")
                bad = True
    for rel in pruned:
        lines.append(f"pruned {rel}: its copy was dropped by the large-file retention (manifest keeps its hash)")
    partial = m["status"] != "complete" or bool(m.get("failures"))
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


FATAL_PREFIXES = ("BADPATH", "MISSING", "CHANGED", "INTEGRITY", "UNLISTED", "SIDEFILE", "LINK", "COUNTS")


# ------------------------------------------------------------------------------------------ list
def list_backups(dest):
    dest = Path(os.path.expanduser(str(dest)))
    if not dest.is_dir():
        return ["no backups: " + str(dest) + " does not exist"]
    valid, unreadable = our_backups(dest)
    rows = {}
    total = 0
    for p, m in valid:
        present = []
        for e in m["files"]:
            if not e.get("pruned") and not bad_entry_path(e["path"]) and (p / e["path"]).is_file():
                present.append(e)
        size = sum((p / e["path"]).stat().st_size for e in present)
        total += size
        nfail = len(m.get("failures", []))
        npruned = len([e for e in m["files"] if e.get("pruned")])
        extra = (f"  {nfail} failed" if nfail else "") + (f"  {npruned} pruned" if npruned else "")
        rows[p.name] = f"{p.name}  {m['status']:8}  {len(present):4} files  {human(size):>10}{extra}"
    for p, why in unreadable:
        rows[p.name] = f"{p.name}  UNREADABLE manifest ({why}); left alone"
    if not rows:
        return [f"no backups in {dest}"]
    lines = [rows[k] for k in sorted(rows)]
    return lines + [f"{len(valid)} backups, total {human(total)} in {dest}"
                    + (f"; {len(unreadable)} unreadable" if unreadable else "")]


# ------------------------------------------------------------------------------------------ restore
def restore_backup(folder, to, root):
    folder = Path(os.path.abspath(os.path.expanduser(str(folder))))
    root_real = os.path.realpath(_absolute(root))
    if same_place_as_or_inside(to, root_real):
        raise Refused(f"will not restore into the repository ({root_real}); choose an empty folder elsewhere")
    if same_place_as_or_inside(to, folder):
        raise Refused("will not restore into the backup folder itself")
    target = Path(os.path.realpath(_absolute(to)))
    if target.exists():
        if not target.is_dir():
            raise Refused(f"{target} is not a folder")
        if any(target.iterdir()):
            raise Refused(f"{target} is not empty; restore needs an empty folder")
    code, lines = verify_backup(folder)
    if code == 2 or any(x.startswith(FATAL_PREFIXES) for x in lines):
        raise Refused(f"{folder} does not pass verify, nothing restored:\n  " + "\n  ".join(lines[:10]))
    m = read_manifest(folder)
    created_target = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    work = target / RESTORE_WORK                       # inside the target: nothing is created outside it
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
        for child in sorted(work.iterdir()):
            os.rename(child, target / child.name)
        work.rmdir()
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        if created_target:
            with contextlib.suppress(OSError):
                target.rmdir()
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


def _stopped(exc, what):
    published = getattr(exc, "published", False)
    if published:
        warn(f"backup: {what} after the backup was published as {getattr(exc, 'final', '?')}; the copy is complete "
             "and kept, but retention or the report may not have finished (run `verify` on it)")
        return 1
    warn(f"backup: {what}; the working folder was removed, no backup was made")
    return 2


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
    except Terminated as exc:
        return _stopped(exc, "stopped by SIGTERM")
    except KeyboardInterrupt as exc:
        return _stopped(exc, "interrupted")
    except OSError as exc:
        warn(f"backup: failed: {exc}")
        return 2
    except Exception as exc:                          # a plain message and exit 2, never a traceback
        warn(f"backup: unexpected {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
