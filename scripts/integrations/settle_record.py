"""Persistent records + lock for settle_run's Profit ping (split out to keep files
under 500 lines). Paths: primary in signal_verify.signal_dir(), backup copy in
settle_run.MODELS (read at call time so tests / harnesses can repoint it).
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _models():
    import settle_run
    return settle_run.MODELS


# ── persistent records: which settlement ids Burton has been told about ──────────
# One JSON id per line, primary in data/signal/ + backup copy in models/. The
# unconfirmed record is the WRITE-AHEAD log: ids are appended (fsync) BEFORE a send;
# confirmed delivery moves them to the sent record; an id appearing N times there was
# sent N times with unknown outcome (crash after send counts as unknown).
SENT_FILE = "profit_sent_ids.jsonl"
UNCONF_FILE = "profit_unconfirmed_ids.jsonl"
STATE_FILE = "settle_state.json"               # last bad-row set already notified
NOTICE_FILE = "settle_notice_days.json"        # once-per-day paused notices
CURSOR_FILE = "settle_ftl_signal_cursor.txt"   # old deployed cursor (bootstrap seed only)
SENT_IDS_MAX = 5000
LOCK_STALE_S = 20 * 60


class SentRecordError(Exception):
    pass


def _pair(name):
    import signal_verify as sv
    return os.path.join(sv.signal_dir(), name), os.path.join(_models(), "settle_" + name)


def _paths():
    return _pair(SENT_FILE) + (_pair(UNCONF_FILE)[0], _pair(STATE_FILE)[0])


def _read_ids(path):
    """(Counter ids, bad_lines, tail_fragment). Missing -> empty. Directory / parent not
    a directory / unreadable -> SentRecordError."""
    from collections import Counter
    parent = os.path.dirname(path)
    if os.path.lexists(parent) and not os.path.isdir(parent):
        raise SentRecordError(f"{parent} is not a directory")
    if not os.path.lexists(path):
        return Counter(), 0, None
    if not os.path.isfile(path):
        raise SentRecordError(f"{path} is not a file")
    try:
        with open(path, "rb") as f:
            parts = f.read().split(b"\n")
    except OSError as e:
        raise SentRecordError(str(e))
    ids, bad, frag = Counter(), 0, None
    for i, line in enumerate(parts):
        if not line.strip():
            continue
        try:
            v = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            v = None
        if isinstance(v, str) and v:
            ids[v] += 1
        else:
            bad += 1
            if i == len(parts) - 1:  # last line without "\n": cut mid-write
                frag = line.decode("utf-8", "replace")
    return ids, bad, frag


def _load_pair(name):
    """Max-per-id union of primary + backup. One unreadable copy is tolerated only when
    the other exists and is readable; otherwise SentRecordError (fail closed)."""
    from collections import Counter
    out = {"ids": Counter(), "bad": 0, "frags": [], "ok": [], "errs": [], "seen": [], "mtime": 0}
    for path in _pair(name):
        try:
            ids, bad, frag = _read_ids(path)
        except SentRecordError as e:
            out["errs"].append(str(e))
            continue
        out["ok"].append(path)
        if os.path.lexists(path):
            out["seen"].append(ids)
            out["mtime"] = max(out["mtime"], os.path.getmtime(path))
        out["bad"] += bad
        out["frags"] += [frag] if frag else []
        for k, n in ids.items():
            out["ids"][k] = max(out["ids"][k], n)
    if out["errs"] and not out["seen"]:
        raise SentRecordError("; ".join(out["errs"]))
    if out["errs"]:
        print(f"[settle_run] {name}: using the readable copy ({'; '.join(out['errs'])})")
    out["differ"] = len(out["seen"]) == 1 and len(out["ok"]) == 2 or any(
        c != out["seen"][0] for c in out["seen"])
    return out


def _append(path, lines):
    """Append + fsync; first ends an unterminated last line so ids never merge."""
    data = "".join(l + "\n" for l in lines).encode()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+b") as f:
        f.seek(0, 2)
        if f.tell():
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                data = b"\n" + data
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _rewrite(path, lines):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write("".join(l + "\n" for l in lines).encode())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_pair(name, lines, append=True, tries=3):
    """Write to every copy independently (retrying); returns how many copies took it."""
    done = 0
    for path in _pair(name):
        for i in range(tries):
            try:
                (_append if append else _rewrite)(path, lines)
                done += 1
                break
            except OSError as e:
                if i == tries - 1:
                    print(f"[settle_run] could not write {path}: {e}")
    return done


def record_sent(ids):
    """Append ids (write + fsync) to the sent record and its backup. Returns copies written."""
    return _write_pair(SENT_FILE, [json.dumps(str(i)) for i in sorted(ids)])


def load_sent(keep=(), recover=None):
    """(ids, first_run). Bad / duplicate lines are rewritten away. A last line cut
    mid-write (a batch appended after a CONFIRMED delivery) is recovered: every valid
    settlement booked by the time of that append was either already recorded or in that
    batch, so unsent ids booked before the record's mtime whose JSON starts with the
    fragment count as sent (never re-reported) and are logged. Unreadable ->
    SentRecordError."""
    import ledger_view as lv
    d = _load_pair(SENT_FILE)
    ids, first = set(d["ids"]), not d["seen"] and not d["errs"]
    rec = set()
    limit = datetime.fromtimestamp(d["mtime"], timezone.utc) + lv.TOL if d["mtime"] else None
    for frag in d["frags"]:
        if frag.startswith('"') and limit:
            rec |= {k for k, s in (recover or {}).items() if k not in ids
                    and json.dumps(k).startswith(frag) and lv.booked(s) <= limit}
    if rec:
        print(f"[settle_run] truncated sent-record line: {sorted(rec)} treated as sent")
        ids |= rec
    dup = any(n > 1 for n in d["ids"].values())
    if not first and (d["bad"] or d["differ"] or dup or rec or len(ids) > SENT_IDS_MAX):
        keep, others = set(keep), sorted(ids - set(keep))
        room = max(0, SENT_IDS_MAX - len(ids & keep))  # ids still in the ledger never dropped
        ids = set(sorted(ids & keep) + (others[len(others) - room:] if room else []))
        for path in d["ok"]:
            try:
                _rewrite(path, [json.dumps(i) for i in sorted(ids)])
            except OSError as e:
                print(f"[settle_run] could not repair {path}: {e}")
    return ids, first


def load_sent_ids():
    return load_sent()[0]


def _unc_lines(counter):
    return [json.dumps(k) for k in sorted(counter) for _ in range(counter[k])]


def set_unconfirmed(counter):
    """Rewrite the write-ahead record. Empty -> primary removed, backup kept as an empty
    file (so one readable copy always exists if the primary path is ever broken)."""
    lines = _unc_lines(counter)
    if lines:
        return _write_pair(UNCONF_FILE, lines, append=False, tries=1)
    primary, backup = _pair(UNCONF_FILE)
    try:
        if os.path.isfile(primary):
            os.remove(primary)
    except OSError as e:
        print(f"[settle_run] could not clear {primary}: {e}")
    try:
        _rewrite(backup, [])
    except OSError as e:
        print(f"[settle_run] could not clear {backup}: {e}")
    return 1


def _json_pair(name):
    for path in _pair(name):
        try:
            with open(path) as f:
                v = json.load(f)
            if isinstance(v, dict):
                return v
        except (OSError, ValueError):
            continue
    return {}


def _save_json_pair(name, obj):
    _write_pair(name, [json.dumps(obj, sort_keys=True)], append=False, tries=1)


def _cursor(now=None):
    """Old deployed cursor (models/settle_ftl_signal_cursor.txt): datetime or None
    (missing / garbage / future)."""
    import signal_verify as sv
    import ledger_view as lv
    try:
        with open(os.path.join(_models(), CURSOR_FILE)) as f:
            t = sv.parse_ts(f.read().strip())
    except (OSError, ValueError):
        return None
    return t if t and t <= (now or sv.utcnow()) + lv.TOL else None


def _write_cursor(t):
    try:  # last-report time: seeds a bootstrap if both sent records are ever lost
        _rewrite(os.path.join(_models(), CURSOR_FILE), [t.isoformat()])
    except OSError as e:
        print(f"[settle_run] cursor not written: {e}")



# ── lock: flock; ENOLCK / unsupported -> O_EXCL pid file; none at all -> daily notice ──
def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _lock_info(path):
    """(pid, age_s) from a lock file's "pid epoch" content (else mtime)."""
    import time
    try:
        with open(path) as f:
            pid, t = f.read().split()[:2]
        return int(pid), time.time() - float(t)
    except (OSError, ValueError):
        try:
            return None, time.time() - os.path.getmtime(path)
        except OSError:
            return None, 0


def _stamp_lock(f):
    import time
    f.seek(0)
    f.truncate()
    f.write(f"{os.getpid()} {time.time():.0f}\n")
    f.flush()


def _excl(path):
    """True acquired / "held" / None (cannot lock here)."""
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            pid, age = _lock_info(path)
            if age <= LOCK_STALE_S and (pid is None or _pid_alive(pid)):
                return "held"
            try:
                os.remove(path)  # stale: dead pid or older than LOCK_STALE_S
            except OSError:
                return None
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w") as f:
            _stamp_lock(f)
        return True
    return "held"


def _acquire():
    """("flock", fh) | ("excl", path) | ("held", path) | (None, errors)."""
    import errno
    import fcntl
    import signal_verify as sv
    d, errs = sv.signal_dir(), []
    try:
        os.makedirs(d, exist_ok=True)
        fh = open(os.path.join(d, "settle.lock"), "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _stamp_lock(fh)
            return "flock", fh
        except OSError as e:
            fh.close()
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return "held", os.path.join(d, "settle.lock")
            errs.append(f"flock: {e}")
    except OSError as e:
        errs.append(f"lock file: {e}")
    for base in (d, _models()):
        p = os.path.join(base, "settle.lock.pid")
        r = _excl(p)
        if r == "held":
            return "held", p
        if r:
            print(f"[settle_run] flock unavailable ({'; '.join(errs)}); using {p}")
            return "excl", p
        errs.append(f"pid lock {p} unavailable")
    return None, errs
