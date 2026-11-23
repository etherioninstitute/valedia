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
held to the same rules.

Re-registering a source: identical values do nothing; different values are refused. A change of terms
means a new source_id (for example "example_public_v2"); the old row stays as the record of the old terms.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

DOMAINS = ("K1_LISTING", "K2_NEWS", "K3_CAL", "K4_VENUE", "K5_REG", "K6_TAX")
UA_POLICIES = ("GENERIC_CLIENT", "SEC_DECLARED_CONTACT")

_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# The same shape, as a database check on the stored (fixed-width) form.
_TIME_GLOB = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'"


class StoreError(ValueError):
    """A write or read was refused by the store's rules."""


def _q(items: tuple[str, ...]) -> str:
    return ", ".join(f"'{i}'" for i in items)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS source_registry (
    source_id TEXT PRIMARY KEY CHECK (length(source_id) > 0),
    terms_url TEXT NOT NULL CHECK (length(terms_url) > 0),
    licence_tag TEXT NOT NULL CHECK (length(licence_tag) > 0),
    personal_use_ok INTEGER NOT NULL CHECK (personal_use_ok = 1),
    allow_external_llm INTEGER NOT NULL CHECK (allow_external_llm IN (0, 1)),
    ua_policy TEXT NOT NULL CHECK (ua_policy IN ({_q(UA_POLICIES)})),
    cost_usd_monthly REAL NOT NULL DEFAULT 0 CHECK (cost_usd_monthly >= 0),
    terms_read_at_utc TEXT NOT NULL CHECK (terms_read_at_utc GLOB {_TIME_GLOB}),
    notes TEXT,
    CHECK (NOT (lower(source_id) LIKE '%finnhub%' AND allow_external_llm = 1))
);

CREATE TRIGGER IF NOT EXISTS source_registry_no_update BEFORE UPDATE ON source_registry
BEGIN SELECT RAISE(ABORT, 'source_registry is append-only: use a new source_id'); END;
CREATE TRIGGER IF NOT EXISTS source_registry_no_delete BEFORE DELETE ON source_registry
BEGIN SELECT RAISE(ABORT, 'source_registry is append-only: use a new source_id'); END;

CREATE TABLE IF NOT EXISTS knowledge_facts (
    fact_version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL CHECK (domain IN ({_q(DOMAINS)})),
    entity_key TEXT NOT NULL CHECK (length(entity_key) > 0),
    effective_from_utc TEXT NOT NULL CHECK (effective_from_utc GLOB {_TIME_GLOB}),
    effective_to_utc TEXT CHECK (effective_to_utc IS NULL OR effective_to_utc GLOB {_TIME_GLOB}),
    published_at_utc TEXT CHECK (published_at_utc IS NULL OR published_at_utc GLOB {_TIME_GLOB}),
    fetched_at_utc TEXT NOT NULL CHECK (fetched_at_utc GLOB {_TIME_GLOB}),
    known_at_utc TEXT NOT NULL CHECK (known_at_utc GLOB {_TIME_GLOB}),
    known_at_basis TEXT NOT NULL CHECK (known_at_basis IN ('PUBLISHED', 'FETCHED')),
    source_id TEXT NOT NULL REFERENCES source_registry(source_id),
    raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256) = 64 AND raw_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    CHECK (effective_to_utc IS NULL OR effective_to_utc >= effective_from_utc),
    CHECK (
        (known_at_basis = 'PUBLISHED' AND published_at_utc IS NOT NULL
            AND known_at_utc = published_at_utc AND known_at_utc <= fetched_at_utc)
        OR (known_at_basis = 'FETCHED' AND known_at_utc = fetched_at_utc)
    )
);

CREATE INDEX IF NOT EXISTS knowledge_facts_lookup ON knowledge_facts (domain, entity_key, known_at_utc);

CREATE TRIGGER IF NOT EXISTS knowledge_facts_no_update BEFORE UPDATE ON knowledge_facts
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: no updates'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_facts_no_delete BEFORE DELETE ON knowledge_facts
BEGIN SELECT RAISE(ABORT, 'knowledge_facts is append-only: no deletes'); END;
"""


def connect(path) -> sqlite3.Connection:
    """Open (and create if needed) the store at `path`. The one place connections are made."""
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit; writes use explicit BEGIN
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    conn.executescript(_SCHEMA)
    return conn


def _utc(value, name: str) -> str:
    """Check a UTC time with a Z suffix and return its fixed-width stored form."""
    if not isinstance(value, str) or not _TIME_RE.match(value):
        raise StoreError(f"{name}: must be UTC ISO-8601 ending in Z, got {value!r}")
    try:
        parsed = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise StoreError(f"{name}: not a real time: {value!r}") from exc
    frac = value[20:-1] if "." in value else ""
    return parsed.strftime("%Y-%m-%dT%H:%M:%S") + "." + frac.ljust(6, "0") + "Z"


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
    if not isinstance(source_id, str) or not source_id:
        raise StoreError("source_id: required")
    if not personal_use_ok:
        raise StoreError("personal_use_ok must be true: a source we may not use is not registered")
    if allow_external_llm and "finnhub" in source_id.lower():
        raise StoreError("a Finnhub source may never be allowed to reach an outside model")
    if ua_policy not in UA_POLICIES:
        raise StoreError(f"ua_policy: must be one of {UA_POLICIES}")
    row = (
        source_id, terms_url, licence_tag, 1, 1 if allow_external_llm else 0, ua_policy,
        float(cost_usd_monthly), _utc(terms_read_at_utc, "terms_read_at_utc"), notes,
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


def _latest_fact_for_key(conn, domain: str, entity_key: str, source_id: str):
    """Newest stored version for one (domain, entity_key, source_id): id and hash only. Internal."""
    return conn.execute(
        "SELECT fact_version_id, raw_sha256 FROM knowledge_facts"
        " WHERE domain = ? AND entity_key = ? AND source_id = ? ORDER BY fact_version_id DESC LIMIT 1",
        (domain, entity_key, source_id),
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
    """Store a new version and return its id, or return the id of the existing newest version if the
    hash is unchanged (nothing is stored then).

    `known_at` is the published time only when `published_proven` is true (the source carries an official
    timestamp proving the fact was public then); otherwise it is the fetch time."""
    if domain not in DOMAINS:
        raise StoreError(f"domain: must be one of {DOMAINS}")
    if not isinstance(entity_key, str) or not entity_key:
        raise StoreError("entity_key: required")
    if not isinstance(raw_sha256, str) or not _SHA_RE.match(raw_sha256):
        raise StoreError("raw_sha256: must be 64 lowercase hex characters")
    try:
        json.loads(payload_json)
    except (TypeError, ValueError) as exc:
        raise StoreError("payload_json: must be valid JSON text") from exc
    eff_from = _utc(effective_from_utc, "effective_from_utc")
    eff_to = None if effective_to_utc is None else _utc(effective_to_utc, "effective_to_utc")
    fetched = _utc(fetched_at_utc, "fetched_at_utc")
    published = None if published_at_utc is None else _utc(published_at_utc, "published_at_utc")
    if eff_to is not None and eff_to < eff_from:
        raise StoreError("effective_to_utc is before effective_from_utc")
    if published_proven:
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
        latest = _latest_fact_for_key(conn, domain, entity_key, source_id)
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
    if for_outside_llm:
        sql += " AND s.allow_external_llm = 1"
    sql += " ORDER BY f.domain, f.entity_key, f.source_id"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
