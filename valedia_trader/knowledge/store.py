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

Ids come from SQLite only: the facts table's ids are 1, 2, 3, ... with no gaps, enforced after each insert, so
no raw insert can skip ahead (-1, 0, a huge id) and wedge the sequence.

Text is checked as bytes in the database (LIKE, GLOB, length() and json_valid() stop at a NUL byte, so a NUL
could hide a Finnhub name, a time's tail or JSON garbage). Every text column is NUL-free.

The file carries a schema version (PRAGMA user_version). `connect` creates a new empty file at the current
version and refuses any existing file with a different one, rather than opening a store made with weaker guards.

"Current version at time t" means one thing everywhere: the version with the greatest `known_at_utc` at or
before t, ties broken by the highest `fact_version_id`. `facts_as_of` returns it, and `add_fact` skips a
new read only when it matches the version that is current at that read's own `known_at_utc`.

Re-registering a source: identical values do nothing; different values are refused. A change of terms
means a new source_id (for example "example_public_v2"); the old row stays as the record of the old terms.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 1   # kept in PRAGMA user_version
DOMAINS = ("K1_LISTING", "K2_NEWS", "K3_CAL", "K4_VENUE", "K5_REG", "K6_TAX")
UA_POLICIES = ("GENERIC_CLIENT", "SEC_DECLARED_CONTACT")

_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
# The same shape, as a database check on the stored (fixed-width) form.
_TIME_GLOB = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'"
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
    fact_version_id INTEGER PRIMARY KEY AUTOINCREMENT,
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

-- An id that already exists is refused (this is what stops REPLACE). A BEFORE trigger sees -1 for an
-- automatic id, which never exists, so it does not misfire.
CREATE TRIGGER IF NOT EXISTS knowledge_facts_no_replace BEFORE INSERT ON knowledge_facts
WHEN NEW.fact_version_id IS NOT NULL
 AND EXISTS (SELECT 1 FROM knowledge_facts WHERE fact_version_id = NEW.fact_version_id)
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: that fact_version_id exists'); END;

-- Ids come from SQLite only: 1, 2, 3, ... with no gaps. After the insert the new row's real id must equal the
-- row count, so an explicit id (-1, 0, a huge one) that would skip ahead or wedge the sequence is refused.
CREATE TRIGGER IF NOT EXISTS knowledge_facts_ids_dense AFTER INSERT ON knowledge_facts
WHEN NEW.fact_version_id <> (SELECT count(*) FROM knowledge_facts)
BEGIN SELECT RAISE(ABORT, 'knowledge_facts ids are assigned by the database: no gaps, no explicit ids'); END;

CREATE TRIGGER IF NOT EXISTS knowledge_facts_no_update BEFORE UPDATE ON knowledge_facts
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: no updates'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_facts_no_delete BEFORE DELETE ON knowledge_facts
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: no deletes'); END;
"""


def connect(path) -> sqlite3.Connection:
    """Open (and create if needed) the store at `path`. The one place connections are made."""
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit; writes use explicit BEGIN
    conn.row_factory = sqlite3.Row
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
        if version != SCHEMA_VERSION and (version != 0 or has_tables):
            raise StoreError(
                f"{path}: store schema version is {version}, this code needs {SCHEMA_VERSION}; "
                "it was made by an older or different build and is not opened silently")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        if version == 0:   # a brand-new empty file: create everything and stamp the version in one transaction
            conn.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;")
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


def _text(value, name: str, *, required: bool = True) -> str | None:
    """A text field: a str (never cast from anything else), no NUL byte, and not empty unless optional."""
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value) or "\x00" in value:
        raise StoreError(f"{name}: must be a non-empty str with no NUL byte, got {value!r}"
                         if required else f"{name}: must be a str with no NUL byte, or None, got {value!r}")
    return value


def _cost(value, name: str) -> float:
    """A monthly cost: an int or float (not a bool), finite, not below zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise StoreError(f"{name}: must be a finite number, 0 or more, got {value!r}")
    return float(value)


def _flag(value, name: str) -> int:
    """A yes/no setting: a bool, or the int 0 or 1. Anything else (a string like "no", 1.0, None) is refused."""
    if isinstance(value, bool) or (isinstance(value, int) and value in (0, 1)):
        return int(value)
    raise StoreError(f"{name}: must be a bool or the int 0 or 1, got {value!r}")


def _write(conn: sqlite3.Connection, fn):
    """Run `fn` inside one immediate transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn()
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
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
    at or before it, ties to the highest id. Id and hash only. Internal; the same rule as `facts_as_of`."""
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
    """Store a new version and return its id, or, if the hash matches the version that is current at this
    read's own `known_at`, return that version's id and store nothing.

    `known_at` is the published time only when `published_proven` is true (the source carries an official
    timestamp proving the fact was public then); otherwise it is the fetch time."""
    if domain not in DOMAINS:
        raise StoreError(f"domain: must be one of {DOMAINS}")
    _text(entity_key, "entity_key")
    _text(source_id, "source_id")
    proven = _flag(published_proven, "published_proven")
    if not isinstance(raw_sha256, str) or not _SHA_RE.fullmatch(raw_sha256):
        raise StoreError("raw_sha256: must be 64 lowercase hex characters")
    if not isinstance(payload_json, str) or "\x00" in payload_json:
        raise StoreError("payload_json: must be JSON text (a str with no NUL byte)")
    try:
        json.loads(payload_json)
    except ValueError as exc:
        raise StoreError("payload_json: must be valid JSON text") from exc
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
        latest = _latest_fact_for_key(conn, domain, entity_key, source_id, known_at)
        if latest is not None and latest["raw_sha256"] == raw_sha256:
            return latest["fact_version_id"]
        try:
            cur = conn.execute(
                "INSERT INTO knowledge_facts (domain, entity_key, effective_from_utc, effective_to_utc,"
                " published_at_utc, fetched_at_utc, known_at_utc, known_at_basis, source_id, raw_sha256,"
                " payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (domain, entity_key, eff_from, eff_to, published, fetched, known_at, basis, source_id,
                 raw_sha256, payload_json),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"fact refused by the database: {exc}") from exc
        return cur.lastrowid

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
    as_of = _utc(as_of_utc, "as_of_utc")
    outside = _flag(for_outside_llm, "for_outside_llm")
    if domain is not None and domain not in DOMAINS:
        raise StoreError(f"domain: must be one of {DOMAINS}")
    sql = (
        "SELECT f.* FROM knowledge_facts f JOIN source_registry s ON s.source_id = f.source_id"
        " WHERE f.known_at_utc <= ?"
        " AND f.fact_version_id = (SELECT g.fact_version_id FROM knowledge_facts g"
        "   WHERE g.domain = f.domain AND g.entity_key = f.entity_key AND g.source_id = f.source_id"
        "   AND g.known_at_utc <= ? ORDER BY g.known_at_utc DESC, g.fact_version_id DESC LIMIT 1)"
    )
    args: list = [as_of, as_of]
    if domain is not None:
        sql += " AND f.domain = ?"
        args.append(domain)
    if entity_key is not None:
        sql += " AND f.entity_key = ?"
        args.append(entity_key)
    if outside:
        sql += " AND s.allow_external_llm = 1"
    sql += " ORDER BY f.domain, f.entity_key, f.source_id"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
