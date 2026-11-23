"""The knowledge store: sources, append-only facts, and the single read path.

Rules, in plain words:
- A source must be registered (with its terms and licence) before any fact can cite it.
- Facts are never changed or deleted. A changed read is a new version; an unchanged read is skipped.
- Every fact records when she could first have known it (`known_at_utc`) and why that time was chosen.
- The only function that returns fact rows is `facts_as_of`.
- Nothing from a Finnhub source may ever be marked as allowed to reach an outside model.

Times are UTC, ISO-8601 with a Z suffix ("2026-01-02T03:04:05Z", fractions allowed). Anything else is
refused. Accepted times are stored in one fixed shape (microseconds, "2026-01-02T03:04:05.000000Z") so
that plain text comparison orders them correctly. The guards live in the database too, so raw SQL is
held to the same rules, including REPLACE (a REPLACE would delete without firing a delete trigger, so a
BEFORE INSERT trigger on each table refuses any insert whose key already exists).

Ids come from SQLite only: there is no AUTOINCREMENT (so no sequence table to corrupt), ids run from 1 to
2^62, and an explicit id is accepted only if it is exactly the next one. A bad row, under INSERT OR IGNORE,
REPLACE or a multi-row INSERT, can therefore never wedge later writes. Ids rise with insertion order, which is
what "ties go to the highest id" relies on.

Text is checked as bytes in the database (LIKE, GLOB, length() and json_valid() stop at a NUL byte, so a NUL
could hide a Finnhub name, a time's tail or JSON garbage). Every text column is NUL-free.

The file carries a schema version (PRAGMA user_version). `connect` creates a new empty file at the current
version and refuses any existing file with a different one, rather than opening a store made with weaker guards.

"Current version at time t" means one thing everywhere: the version with the greatest `known_at_utc` at or
before t, ties broken by the highest `fact_version_id`. `facts_as_of` returns it.

A read is always current at its own fetch time: after `add_fact`, `facts_as_of` at the fetch time returns that
read's hash. `add_fact` stores nothing only when the hash already matches the current version at BOTH the
read's `known_at_utc` and its fetch time. When a proven publication time would leave an older, different
version current at the fetch time, a FETCHED row at the fetch time is stored too, in the same transaction.

What `connect` checks on opening a store: the schema version, that every expected table, index and trigger is
present with exactly the SQL this code creates, that nothing else is in the file (the whole schema table is
compared as a multiset of (type, name, tbl_name, sql) rows, never by a name pattern and never through a
dictionary keyed by name; a duplicate name is refused whatever its type; SQLite's own automatic indexes and
the statistics tables that ANALYZE makes are the only extras allowed), and that the ids lie within 1..2^62. It
checks only the id range, the schema identity and the version. Rows written with the checks switched off, other
than out-of-range ids, are not detected.

Known limits:
- SQLite cannot check UTF-8 validity in a CHECK constraint (no function does it, and a CHECK may not contain a
  subquery), so text that is not valid UTF-8 can be stored through raw SQL; every read of it raises
  StoreError instead of a raw error. `connect` refuses a store whose text encoding is not UTF-8.
- `connect` runs `PRAGMA integrity_check` on an existing store: a full-file read on every open. `quick=True`
  runs `quick_check` instead, which is faster and skips some index checks.
- What the store can see is limited: temp objects on the caller's own connection are outside it. A raw
  connection that uses writable_schema, or relies on overridden SQL functions, between integrity checks can
  alter the file; the next `connect` (its schema comparison and integrity check) is where that is caught.
- A raw number such as 1e999 inside payload JSON, and an escaped lone surrogate such as "\\ud800" inside a JSON
  string, are refused by `add_fact` (in code) but not by the database itself, which accepts them as valid JSON.
- The test that guards the single read path is text-only: it finds the facts table by its name written out in
  the package's source, so a name built at run time would not be seen.
- A raw connection that disables triggers, or drops and re-creates them verbatim, is outside what the store
  can detect.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone

SCHEMA_VERSION = 2   # kept in PRAGMA user_version
DOMAINS = ("K1_LISTING", "K2_NEWS", "K3_CAL", "K4_VENUE", "K5_REG", "K6_TAX")
UA_POLICIES = ("GENERIC_CLIENT", "SEC_DECLARED_CONTACT")

_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
# The same shape, as a database check on the stored (fixed-width) form.
_TIME_GLOB = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'"
_MAX_ID = 4611686018427387904   # 2**62, the same bound as the id CHECK
_TIME_BYTES = 27   # len("2026-01-02T03:04:05.000000Z")


class StoreError(ValueError):
    """A write or read was refused by the store's rules."""


def _text_ok(col: str, nullable: bool = False) -> str:
    """Database check: real TEXT with no NUL byte. LIKE, GLOB, length() and json_valid() all stop reading at
    a NUL, so a NUL could hide anything behind it; looking at the value as bytes sees all of it."""
    check = f"(typeof({col}) = 'text' AND instr(CAST({col} AS BLOB), x'00') = 0)"
    return f"({col} IS NULL OR {check})" if nullable else check


def _time_ok(col: str, nullable: bool = False) -> str:
    """Database check: exact byte length, fixed shape, year 0001 or later, and a real date and time (a round
    trip through strftime must not move it)."""
    check = (f"({_text_ok(col)} AND length(CAST({col} AS BLOB)) = {_TIME_BYTES} AND {col} GLOB {_TIME_GLOB}"
             f" AND substr({col}, 1, 4) >= '0001' AND substr({col}, 12, 2) <= '23' AND "
             f"strftime('%Y-%m-%dT%H:%M:%S', substr({col}, 1, 19)) IS substr({col}, 1, 19))")
    return f"({col} IS NULL OR {check})" if nullable else check


def _q(items: tuple[str, ...]) -> str:
    return ", ".join(f"'{i}'" for i in items)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS source_registry (
    source_id TEXT PRIMARY KEY CHECK ({_text_ok('source_id')} AND length(source_id) > 0),
    terms_url TEXT NOT NULL CHECK ({_text_ok('terms_url')} AND length(terms_url) > 0),
    licence_tag TEXT NOT NULL CHECK ({_text_ok('licence_tag')} AND length(licence_tag) > 0),
    personal_use_ok INTEGER NOT NULL CHECK (typeof(personal_use_ok) = 'integer' AND personal_use_ok = 1),
    allow_external_llm INTEGER NOT NULL CHECK (typeof(allow_external_llm) = 'integer' AND allow_external_llm IN (0, 1)),
    ua_policy TEXT NOT NULL CHECK ({_text_ok('ua_policy')} AND ua_policy IN ({_q(UA_POLICIES)})),
    cost_usd_monthly REAL NOT NULL DEFAULT 0
        CHECK (typeof(cost_usd_monthly) = 'real' AND cost_usd_monthly BETWEEN 0 AND 1.7976931348623157e308),
    terms_read_at_utc TEXT NOT NULL CHECK ({_time_ok('terms_read_at_utc')}),
    notes TEXT CHECK ({_text_ok('notes', True)}),
    CHECK (NOT (lower(source_id) LIKE '%finnhub%' AND allow_external_llm = 1))
);

CREATE TRIGGER IF NOT EXISTS source_registry_no_update BEFORE UPDATE ON source_registry
BEGIN SELECT RAISE(ABORT, 'source_registry is append-only: use a new source_id'); END;
CREATE TRIGGER IF NOT EXISTS source_registry_no_delete BEFORE DELETE ON source_registry
BEGIN SELECT RAISE(ABORT, 'source_registry is append-only: use a new source_id'); END;

-- REPLACE deletes the old row without firing delete triggers, so a key that already exists cannot be inserted.
CREATE TRIGGER IF NOT EXISTS source_registry_no_replace BEFORE INSERT ON source_registry
WHEN EXISTS (SELECT 1 FROM source_registry WHERE source_id = NEW.source_id)
BEGIN SELECT RAISE(ABORT, 'source_registry is append-only: that source_id exists, use a new one'); END;

CREATE TABLE IF NOT EXISTS knowledge_facts (
    fact_version_id INTEGER PRIMARY KEY CHECK (fact_version_id BETWEEN 1 AND 4611686018427387904),
    domain TEXT NOT NULL CHECK ({_text_ok('domain')} AND domain IN ({_q(DOMAINS)})),
    entity_key TEXT NOT NULL CHECK ({_text_ok('entity_key')} AND length(entity_key) > 0),
    effective_from_utc TEXT NOT NULL CHECK ({_time_ok('effective_from_utc')}),
    effective_to_utc TEXT CHECK ({_time_ok('effective_to_utc', True)}),
    published_at_utc TEXT CHECK ({_time_ok('published_at_utc', True)}),
    fetched_at_utc TEXT NOT NULL CHECK ({_time_ok('fetched_at_utc')}),
    known_at_utc TEXT NOT NULL CHECK ({_time_ok('known_at_utc')}),
    known_at_basis TEXT NOT NULL CHECK ({_text_ok('known_at_basis')} AND known_at_basis IN ('PUBLISHED', 'FETCHED')),
    source_id TEXT NOT NULL CHECK ({_text_ok('source_id')}) REFERENCES source_registry(source_id),
    raw_sha256 TEXT NOT NULL CHECK ({_text_ok('raw_sha256')} AND length(CAST(raw_sha256 AS BLOB)) = 64
        AND raw_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK ({_text_ok('payload_json')} AND json_valid(payload_json)),
    CHECK (effective_to_utc IS NULL OR effective_to_utc >= effective_from_utc),
    CHECK (
        (known_at_basis = 'PUBLISHED' AND published_at_utc IS NOT NULL
            AND known_at_utc = published_at_utc AND known_at_utc <= fetched_at_utc)
        OR (known_at_basis = 'FETCHED' AND known_at_utc = fetched_at_utc)
    )
);

CREATE INDEX IF NOT EXISTS knowledge_facts_lookup ON knowledge_facts (domain, entity_key, known_at_utc);

-- Ids come from SQLite only (the highest id plus one; there is no AUTOINCREMENT and so no sequence table
-- to corrupt). An explicit id is accepted only if it is exactly what SQLite would assign, so an existing id
-- (REPLACE), a gap, or a huge id near the bound is refused and the store cannot be wedged. A BEFORE trigger
-- shows -1 for an automatic id; an explicit -1 is then refused by the column's CHECK (1 to 2^62).
CREATE TRIGGER IF NOT EXISTS knowledge_facts_ids_from_sqlite BEFORE INSERT ON knowledge_facts
WHEN NEW.fact_version_id IS NOT NULL AND NEW.fact_version_id <> -1
 AND NEW.fact_version_id <> coalesce((SELECT max(fact_version_id) FROM knowledge_facts), 0) + 1
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: ids are assigned by the database, an explicit id must be the next one'); END;

-- A fact needs a registered source even on a connection that has foreign keys off.
CREATE TRIGGER IF NOT EXISTS knowledge_facts_source_registered BEFORE INSERT ON knowledge_facts
WHEN NOT EXISTS (SELECT 1 FROM source_registry WHERE source_id = NEW.source_id)
BEGIN SELECT RAISE(ABORT, 'knowledge_facts: source is not registered'); END;

CREATE TRIGGER IF NOT EXISTS knowledge_facts_no_update BEFORE UPDATE ON knowledge_facts
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: no updates'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_facts_no_delete BEFORE DELETE ON knowledge_facts
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: no deletes'); END;
"""


_STATS_TABLES = {      # made by ANALYZE; the only tables whose names start with sqlite_ that are allowed
    "sqlite_stat1": "CREATE TABLE sqlite_stat1(tbl,idx,stat)",
    "sqlite_stat4": "CREATE TABLE sqlite_stat4(tbl,idx,neq,nlt,ndlt,sample)",
}


def _objects(conn: sqlite3.Connection) -> list[tuple]:
    """Every row of the schema table as (type, name, tbl_name, sql), sorted, with no name filtering and no
    dictionary keyed by name (a trigger and an index can share a name, and one must not hide the other)."""
    return sorted((tuple(row) for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master")),
                  key=repr)


def _reference_objects() -> list[tuple]:
    """What a correct store holds: the schema built in a throwaway in-memory database. It includes the
    automatic indexes SQLite makes for the primary keys, under the exact names SQLite gives them."""
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(_SCHEMA)
        return _objects(ref)
    finally:
        ref.close()


def _verify_schema(conn: sqlite3.Connection, path) -> None:
    """The whole schema table, as a multiset of (type, name, tbl_name, sql) rows, must equal the expected rows,
    plus at most the statistics tables ANALYZE makes. No name may appear twice, whatever the types."""
    have = _objects(conn)
    names = Counter(name for _, name, _, _ in have)
    twice = sorted(name for name, n in names.items() if n > 1)
    if twice:    # one object could be hiding behind another of the same name
        raise StoreError(f"{path}: store has more than one object named {twice}; it is not opened silently")
    have_rows = Counter(row for row in have
                        if not (row[1] in _STATS_TABLES and row == ("table", row[1], row[1], _STATS_TABLES[row[1]])))
    want_rows = Counter(_reference_objects())
    missing = want_rows - have_rows
    extra = have_rows - want_rows
    gone = {name for _, name, _, _ in missing}
    added = {name for _, name, _, _ in extra}
    for name in sorted(gone):
        if name in added:
            raise StoreError(f"{path}: {name} differs from what this code creates; it is not opened silently")
        raise StoreError(f"{path}: store is missing {name}; it is not opened silently")
    if added:    # a trigger, view or index nobody asked for could change what a write or a read does
        raise StoreError(f"{path}: store has unexpected objects {sorted(added)}; it is not opened silently")
    low, high = conn.execute("SELECT min(fact_version_id), max(fact_version_id) FROM knowledge_facts").fetchone()
    if low is not None and (low < 1 or high > _MAX_ID):
        raise StoreError(f"{path}: fact_version_id range {low}..{high} is outside 1..{_MAX_ID}; "
                         "rows were written with the checks off")


def _path(path) -> str:
    """A store path: a non-empty str or os.PathLike (giving a str) with no NUL byte or lone surrogate."""
    try:
        text = os.fspath(path) if isinstance(path, (str, os.PathLike)) else None
    except (TypeError, ValueError):
        text = None
    if not isinstance(text, str) or not text or "\x00" in text or not _encodable(text):
        raise StoreError(f"path: must be a non-empty str or path-like with no NUL byte, got {str(path)[:60]!r}")
    return text


def _conn(conn) -> None:
    if not isinstance(conn, sqlite3.Connection):
        raise StoreError("conn: must be a sqlite3 connection, normally the one `connect` returns")


def connect(path, *, quick: bool = False) -> sqlite3.Connection:
    """Open (and create if needed) the store at `path`. The one place connections are made. A store with a
    different schema version or text encoding, with any expected table, index or trigger missing or changed,
    or that fails SQLite's own integrity check, is refused. A new empty file is created at the current version.

    Cost: opening an existing store runs `PRAGMA integrity_check`, which is a full-file read on every open
    (measured 0.14 s for 10,000 facts in a 5 MB file, so roughly 1.4 s per hundred thousand). `quick=True` runs
    `PRAGMA quick_check` instead, which skips some index and uniqueness checks."""
    quick = _flag(quick, "quick")
    path = _path(path)
    try:
        conn = sqlite3.connect(path, isolation_level=None)  # autocommit; writes use explicit BEGIN
    except sqlite3.Error as exc:
        raise StoreError(f"{path}: cannot open: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_objects = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
        if version != SCHEMA_VERSION and (version != 0 or has_objects):
            raise StoreError(
                f"{path}: store schema version is {version}, this code needs {SCHEMA_VERSION}; "
                "it was made by an older or different build and is not opened silently")
        encoding = conn.execute("PRAGMA encoding").fetchone()[0]
        if encoding != "UTF-8":
            raise StoreError(f"{path}: store text encoding is {encoding}, this code needs UTF-8")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        if version == 0:   # a brand-new empty file: create everything and stamp the version in one transaction
            conn.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;")
        else:              # an existing store: SQLite's own consistency check, before anything is trusted
            check = "quick_check" if quick else "integrity_check"
            found = [row[0] for row in conn.execute(f"PRAGMA {check}(5)").fetchall()]
            if found != ["ok"]:
                raise StoreError(f"{path}: {check} failed, the file is damaged or was altered: "
                                 f"{' | '.join(found)[:300]!r}")
        _verify_schema(conn, path)
    except StoreError:
        conn.close()
        raise
    except (sqlite3.Error, UnicodeError) as exc:
        conn.close()
        raise StoreError(f"{path}: cannot open as a store: {exc}") from exc
    except BaseException:
        conn.close()
        raise
    return conn


def _utc(value, name: str) -> str:
    """Check a UTC time with a Z suffix and return its fixed-width stored form."""
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        raise StoreError(f"{name}: must be UTC ISO-8601 ending in Z, got {value!r}")
    try:
        parsed = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise StoreError(f"{name}: not a real time: {value!r}") from exc
    frac = value[20:-1] if "." in value else ""
    return parsed.strftime("%Y-%m-%dT%H:%M:%S") + "." + frac.ljust(6, "0") + "Z"


def _encodable(text: str) -> bool:
    """False for text with a lone surrogate, which cannot be stored (it cannot be encoded as UTF-8)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _json_ok(text, name: str) -> None:
    """The payload must be a str of strict, finite JSON: no NaN, Infinity or -Infinity, no number too large
    for a float, no NUL byte or lone surrogate, nesting a reader can handle."""
    if not isinstance(text, str) or "\x00" in text or not _encodable(text):
        raise StoreError(f"{name}: must be JSON text (a str with no NUL byte or lone surrogate)")

    def no_constant(token):
        raise ValueError(f"{token} is not allowed")

    def finite_float(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError(f"{token} is out of range")
        return value

    try:
        parsed = json.loads(text, parse_constant=no_constant, parse_float=finite_float)
    except (ValueError, RecursionError) as exc:
        raise StoreError(f"{name}: must be valid, finite JSON text") from exc
    pending = [parsed]                      # an escaped lone surrogate ("\\ud800") only shows up after parsing
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if not _encodable(item):
                raise StoreError(f"{name}: contains a lone surrogate escape")
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            for key, value in item.items():
                if not _encodable(key):
                    raise StoreError(f"{name}: contains a lone surrogate escape in a key")
                pending.append(value)


def _text(value, name: str, *, required: bool = True) -> str | None:
    """A text field: a str (never cast from anything else), no NUL byte, and not empty unless optional."""
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value) or "\x00" in value or not _encodable(value):
        raise StoreError(f"{name}: must be a non-empty str with no NUL byte, got {value!r}"
                         if required else f"{name}: must be a str with no NUL byte, or None, got {value!r}")
    return value


def _cost(value, name: str) -> float:
    """A monthly cost: an int or float (not a bool), finite, not below zero."""
    try:
        ok = not isinstance(value, bool) and isinstance(value, (int, float))
        number = float(value) if ok else None          # an int too large for a float raises OverflowError
    except OverflowError:
        number = None
    if number is None or not math.isfinite(number) or number < 0:
        raise StoreError(f"{name}: must be a finite number, 0 or more, got {str(value)[:40]!r}")
    return number


def _flag(value, name: str) -> int:
    """A yes/no setting: a bool, or the int 0 or 1. Anything else (a string like "no", 1.0, None) is refused."""
    if isinstance(value, bool) or (isinstance(value, int) and value in (0, 1)):
        return int(value)
    raise StoreError(f"{name}: must be a bool or the int 0 or 1, got {value!r}")


def _write(conn: sqlite3.Connection, fn):
    """Run `fn` inside one immediate transaction of our own. Refused if the caller already has a transaction
    open. Any failure, including a failed COMMIT, rolls back and surfaces as StoreError (or the StoreError
    `fn` raised), so the connection is never left stuck in a transaction."""
    if conn.in_transaction:
        raise StoreError("a transaction is already open on this connection; the store writes in its own")

    def rollback():
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise StoreError(f"cannot start a write transaction: {exc}") from exc
    try:
        result = fn()
    except StoreError:
        rollback()
        raise
    except (sqlite3.Error, UnicodeError) as exc:      # (a decode error: text in the file that is not UTF-8)
        rollback()
        raise StoreError(f"write failed: {exc}") from exc
    except BaseException:
        rollback()
        raise
    try:
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        rollback()
        raise StoreError(f"commit failed, nothing was written: {exc}") from exc
    return result


def register_source(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    terms_url: str,
    licence_tag: str,
    personal_use_ok: bool,
    allow_external_llm: bool,
    ua_policy: str,
    terms_read_at_utc: str,
    cost_usd_monthly: float = 0,
    notes: str | None = None,
) -> None:
    """Register a source. Same values again: nothing happens. Different values: refused."""
    _conn(conn)
    _text(source_id, "source_id")
    _text(terms_url, "terms_url")
    _text(licence_tag, "licence_tag")
    _text(notes, "notes", required=False)
    cost = _cost(cost_usd_monthly, "cost_usd_monthly")
    personal = _flag(personal_use_ok, "personal_use_ok")
    external = _flag(allow_external_llm, "allow_external_llm")
    if not personal:
        raise StoreError("personal_use_ok must be true: a source we may not use is not registered")
    if external and "finnhub" in source_id.lower():
        raise StoreError("a Finnhub source may never be allowed to reach an outside model")
    if not isinstance(ua_policy, str) or ua_policy not in UA_POLICIES:
        raise StoreError(f"ua_policy: must be one of {UA_POLICIES}")
    row = (
        source_id, terms_url, licence_tag, 1, external, ua_policy,
        cost, _utc(terms_read_at_utc, "terms_read_at_utc"), notes,
    )

    def run():
        old = conn.execute(
            "SELECT source_id, terms_url, licence_tag, personal_use_ok, allow_external_llm, ua_policy,"
            " cost_usd_monthly, terms_read_at_utc, notes FROM source_registry WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if old is not None:
            if tuple(old) == row:
                return
            raise StoreError(f"source {source_id!r} is already registered with different values;"
                             " a terms change needs a new source_id")
        try:
            conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)", row)
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"source refused by the database: {exc}") from exc

    _write(conn, run)


def _latest_fact_for_key(conn, domain: str, entity_key: str, source_id: str, known_at: str):
    """The version of one (domain, entity_key, source_id) that is current at `known_at`: greatest known_at_utc
    at or before it, ties to the highest id. Returns (id, hash) or None. Internal; the same rule as `facts_as_of`."""
    return conn.execute(
        "SELECT fact_version_id, raw_sha256 FROM knowledge_facts"
        " WHERE domain = ? AND entity_key = ? AND source_id = ? AND known_at_utc <= ?"
        " ORDER BY known_at_utc DESC, fact_version_id DESC LIMIT 1",
        (domain, entity_key, source_id, known_at),
    ).fetchone()


def add_fact(
    conn: sqlite3.Connection,
    *,
    domain: str,
    entity_key: str,
    source_id: str,
    raw_sha256: str,
    payload_json: str,
    effective_from_utc: str,
    fetched_at_utc: str,
    effective_to_utc: str | None = None,
    published_at_utc: str | None = None,
    published_proven: bool = False,
) -> int:
    """Store this read and return the id of the version that `facts_as_of` returns for it at its fetch time.

    `known_at` is the published time only when `published_proven` is true (the source carries an official
    timestamp proving the fact was public then); otherwise it is the fetch time.

    Invariant: after any call, `facts_as_of` at the fetch time, for this (domain, entity_key, source_id),
    returns this read's hash. Nothing is stored (and the existing id is returned) only when the hash already
    matches the current version at BOTH the read's `known_at` and its fetch time. A proven read that matches
    at its published time but not at its fetch time is stored with basis FETCHED at the fetch time. A proven
    read that differs is stored as PUBLISHED at its published time, and also as FETCHED at the fetch time
    (same hash, payload and published_at) when an older, different version would otherwise still be current
    at the fetch time. Both rows are written in one transaction."""
    _conn(conn)
    if domain not in DOMAINS:
        raise StoreError(f"domain: must be one of {DOMAINS}")
    _text(entity_key, "entity_key")
    _text(source_id, "source_id")
    proven = _flag(published_proven, "published_proven")
    if not isinstance(raw_sha256, str) or not _SHA_RE.fullmatch(raw_sha256):
        raise StoreError("raw_sha256: must be 64 lowercase hex characters")
    _json_ok(payload_json, "payload_json")
    eff_from = _utc(effective_from_utc, "effective_from_utc")
    eff_to = None if effective_to_utc is None else _utc(effective_to_utc, "effective_to_utc")
    fetched = _utc(fetched_at_utc, "fetched_at_utc")
    published = None if published_at_utc is None else _utc(published_at_utc, "published_at_utc")
    if eff_to is not None and eff_to < eff_from:
        raise StoreError("effective_to_utc is before effective_from_utc")
    if proven:
        if published is None:
            raise StoreError("published_proven needs published_at_utc")
        if published > fetched:
            raise StoreError("published_at_utc is later than fetched_at_utc: that is not possible")
        known_at, basis = published, "PUBLISHED"
    else:
        known_at, basis = fetched, "FETCHED"

    def run():
        if conn.execute("SELECT 1 FROM source_registry WHERE source_id = ?", (source_id,)).fetchone() is None:
            raise StoreError(f"source {source_id!r} is not registered")

        def current(at):
            return _latest_fact_for_key(conn, domain, entity_key, source_id, at)

        def insert(known, how):
            try:
                cur = conn.execute(
                    "INSERT INTO knowledge_facts (domain, entity_key, effective_from_utc, effective_to_utc,"
                    " published_at_utc, fetched_at_utc, known_at_utc, known_at_basis, source_id, raw_sha256,"
                    " payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (domain, entity_key, eff_from, eff_to, published, fetched, known, how, source_id,
                     raw_sha256, payload_json),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError(f"fact refused by the database: {exc}") from exc
            if cur.rowcount != 1:                  # e.g. a trigger that quietly ignores the insert
                raise StoreError("fact was not stored: the database ignored the insert")

        at_known = current(known_at)
        at_fetch = at_known if known_at == fetched else current(fetched)
        same_known = at_known is not None and at_known[1] == raw_sha256
        same_fetch = at_fetch is not None and at_fetch[1] == raw_sha256
        if same_known and same_fetch:
            return at_fetch[0]
        if same_known:                      # current when it was published, but not by the time it was fetched
            insert(fetched, "FETCHED")
        else:
            insert(known_at, basis)
            if known_at != fetched:
                now = current(fetched)
                if now is None or now[1] != raw_sha256:
                    insert(fetched, "FETCHED")   # an older version sits between: it must not win at the fetch time
        final = current(fetched)                 # the invariant, checked before anything is committed
        if final is None or final[1] != raw_sha256:
            raise StoreError("fact was not stored: it cannot be read back as current at its fetch time")
        return final[0]

    return _write(conn, run)


def facts_as_of(
    conn: sqlite3.Connection,
    as_of_utc: str,
    *,
    domain: str | None = None,
    entity_key: str | None = None,
    for_outside_llm: bool = False,
) -> list[dict]:
    """What she knew at `as_of_utc`: for each (domain, entity_key, source_id), the newest version whose
    `known_at_utc` is at or before that time. With `for_outside_llm=True`, facts from sources that do not
    allow an outside model are left out. This is the only function that returns fact rows."""
    _conn(conn)
    as_of = _utc(as_of_utc, "as_of_utc")
    outside = _flag(for_outside_llm, "for_outside_llm")
    _text(entity_key, "entity_key", required=False)
    if domain is not None and domain not in DOMAINS:
        raise StoreError(f"domain: must be one of {DOMAINS}")
    where = " WHERE f.known_at_utc <= ?"
    args: list = [as_of]
    if domain is not None:
        where += " AND f.domain = ?"
        args.append(domain)
    if entity_key is not None:
        where += " AND f.entity_key = ?"
        args.append(entity_key)
    if outside:
        where += " AND s.allow_external_llm = 1"
    # one pass: number each (domain, entity_key, source_id) group newest first, keep number 1
    sql = (
        "SELECT * FROM (SELECT f.*, row_number() OVER (PARTITION BY f.domain, f.entity_key, f.source_id"
        " ORDER BY f.known_at_utc DESC, f.fact_version_id DESC) AS _rank"
        " FROM knowledge_facts f JOIN source_registry s ON s.source_id = f.source_id" + where + ")"
        " WHERE _rank = 1 ORDER BY domain, entity_key, source_id"
    )
    try:
        cur = conn.execute(sql, args)
        names = [d[0] for d in cur.description]             # works whatever row factory the connection has
        rows = [{k: v for k, v in zip(names, row) if k != "_rank"} for row in cur.fetchall()]
    except (sqlite3.Error, UnicodeError) as exc:           # e.g. text in the file that is not valid UTF-8
        raise StoreError(f"read failed: {exc}") from exc
    if outside:     # belt and braces, in Python (not a SQL function): nothing Finnhub-named, whatever the registry says
        rows = [r for r in rows if "finnhub" not in r["source_id"].casefold()]
    return rows
