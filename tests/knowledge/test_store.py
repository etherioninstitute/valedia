"""Tests for the knowledge store. Every guard is tested both ways: the allowed case goes through and
the forbidden case is refused (in code and, where the rule lives in the database, through raw SQL)."""
import ast
import hashlib
import inspect
import json
import re
import sqlite3

import pytest

from valedia_trader.knowledge import store
from valedia_trader.knowledge.store import StoreError, add_fact, connect, facts_as_of, register_source

T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"
T2 = "2026-01-03T00:00:00Z"
T3 = "2026-01-04T00:00:00Z"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def src(conn, source_id="example_public", **over):
    args = dict(source_id=source_id, terms_url="https://example.invalid/terms", licence_tag="public",
                personal_use_ok=True, allow_external_llm=True, ua_policy="GENERIC_CLIENT",
                terms_read_at_utc=T0)
    args.update(over)
    register_source(conn, **args)


def fact(conn, **over):
    args = dict(domain="K2_NEWS", entity_key="ABC", source_id="example_public", raw_sha256=sha("a"),
                payload_json='{"v": 1}', effective_from_utc=T0, fetched_at_utc=T1)
    args.update(over)
    return add_fact(conn, **args)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "k.db")
    src(c)
    yield c
    c.close()


def count(conn, table="knowledge_facts"):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ---- connection ----------------------------------------------------------------------------------

def test_connection_pragmas(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL


# ---- append-only ---------------------------------------------------------------------------------

def test_insert_allowed_update_and_delete_refused(conn):
    fid = fact(conn)
    assert count(conn) == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE knowledge_facts SET payload_json = '{}' WHERE fact_version_id = ?", (fid,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM knowledge_facts WHERE fact_version_id = ?", (fid,))
    assert count(conn) == 1
    assert conn.execute("SELECT payload_json FROM knowledge_facts").fetchone()[0] == '{"v": 1}'


def test_registry_rows_cannot_be_updated_or_deleted(conn):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE source_registry SET allow_external_llm = 0")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM source_registry")
    assert count(conn, "source_registry") == 1


# ---- versions ------------------------------------------------------------------------------------

def test_unchanged_hash_stores_nothing_changed_hash_adds_version(conn):
    first = fact(conn)
    again = fact(conn, fetched_at_utc=T2)  # same hash, later read
    assert again == first and count(conn) == 1
    second = fact(conn, raw_sha256=sha("b"), payload_json='{"v": 2}', fetched_at_utc=T2)
    assert second != first and count(conn) == 2
    assert conn.execute("SELECT payload_json FROM knowledge_facts WHERE fact_version_id = ?",
                        (first,)).fetchone()[0] == '{"v": 1}'  # the old version is kept


def test_hash_is_compared_per_domain_entity_and_source(conn):
    src(conn, "example_other")
    a = fact(conn)
    assert fact(conn, entity_key="XYZ") != a
    assert fact(conn, domain="K3_CAL") != a
    assert fact(conn, source_id="example_other") != a
    assert count(conn) == 4


def test_change_then_revert_is_a_new_version(conn):
    a = fact(conn)
    fact(conn, raw_sha256=sha("b"), fetched_at_utc=T2)
    c = fact(conn, raw_sha256=sha("a"), fetched_at_utc=T3)  # back to the first content: latest is "b"
    assert c != a and count(conn) == 3


# ---- known_at rule -------------------------------------------------------------------------------

def test_known_at_is_fetch_time_unless_published_is_proven(conn):
    fid = fact(conn, published_at_utc=T0, fetched_at_utc=T2)
    row = conn.execute("SELECT * FROM knowledge_facts WHERE fact_version_id = ?", (fid,)).fetchone()
    assert row["known_at_basis"] == "FETCHED"
    assert row["known_at_utc"] == row["fetched_at_utc"] and row["known_at_utc"].startswith("2026-01-03")
    assert row["published_at_utc"].startswith("2026-01-01")  # still recorded


def test_known_at_is_published_time_when_proven(conn):
    fid = fact(conn, published_at_utc=T0, published_proven=True, fetched_at_utc=T2)
    row = conn.execute("SELECT * FROM knowledge_facts WHERE fact_version_id = ?", (fid,)).fetchone()
    assert row["known_at_basis"] == "PUBLISHED"
    assert row["known_at_utc"] == row["published_at_utc"] and row["known_at_utc"].startswith("2026-01-01")


def test_published_equal_to_fetched_is_allowed(conn):
    fact(conn, published_at_utc=T1, published_proven=True, fetched_at_utc=T1)


def test_published_later_than_fetched_is_refused(conn):
    with pytest.raises(StoreError, match="later than fetched"):
        fact(conn, published_at_utc=T2, published_proven=True, fetched_at_utc=T1)
    assert count(conn) == 0


def test_proven_without_published_time_is_refused(conn):
    with pytest.raises(StoreError, match="published_at_utc"):
        fact(conn, published_proven=True)


def _raw_insert(conn, **over):
    cols = dict(domain="K2_NEWS", entity_key="ABC", effective_from_utc="2026-01-01T00:00:00.000000Z",
                effective_to_utc=None, published_at_utc="2026-01-01T00:00:00.000000Z",
                fetched_at_utc="2026-01-02T00:00:00.000000Z", known_at_utc="2026-01-02T00:00:00.000000Z",
                known_at_basis="FETCHED", source_id="example_public", raw_sha256=sha("a"),
                payload_json="{}")
    cols.update(over)
    names = ", ".join(cols)
    conn.execute(f"INSERT INTO knowledge_facts ({names}) VALUES ({', '.join('?' * len(cols))})",
                 list(cols.values()))


def test_database_enforces_known_at_rule_even_through_raw_sql(conn):
    _raw_insert(conn)  # a consistent row goes in
    bad = [
        dict(known_at_basis="PUBLISHED"),                                    # known_at is the fetch time
        dict(known_at_utc="2026-01-01T00:00:00.000000Z"),                    # FETCHED but not the fetch time
        dict(known_at_basis="PUBLISHED", published_at_utc="2026-01-05T00:00:00.000000Z",
             known_at_utc="2026-01-05T00:00:00.000000Z"),                    # published after fetched
        dict(known_at_basis="PUBLISHED", published_at_utc=None),
        dict(known_at_basis="GUESSED"),
    ]
    for over in bad:
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, **over)
    assert count(conn) == 1


# ---- times ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "2026-01-02T00:00:00", "2026-01-02 00:00:00Z", "2026-01-02T00:00:00+00:00", "2026-01-02T01:00:00+01:00",
    "2026-01-02", "2026-13-02T00:00:00Z", "2026-02-30T00:00:00Z", "2026-01-02T24:00:00Z", "",
    "2026-01-02T00:00:00.1234567Z", None,
])
def test_naive_or_offset_times_are_refused(conn, bad):
    with pytest.raises(StoreError):
        fact(conn, fetched_at_utc=bad)
    with pytest.raises(StoreError):
        fact(conn, effective_from_utc=bad)
    with pytest.raises(StoreError):
        facts_as_of(conn, bad)
    assert count(conn) == 0


def test_z_times_with_and_without_fractions_are_accepted_and_ordered(conn):
    a = fact(conn, fetched_at_utc="2026-01-02T00:00:00Z")
    b = fact(conn, raw_sha256=sha("b"), fetched_at_utc="2026-01-02T00:00:00.5Z")
    assert a != b
    assert [r["fact_version_id"] for r in facts_as_of(conn, "2026-01-02T00:00:00.4Z")] == [a]
    assert [r["fact_version_id"] for r in facts_as_of(conn, "2026-01-02T00:00:00.5Z")] == [b]


def test_database_refuses_unnormalised_times_through_raw_sql(conn):
    for bad in ("2026-01-02T00:00:00Z", "2026-01-02T00:00:00", "2026-01-02 00:00:00.000000Z"):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, fetched_at_utc=bad, known_at_utc=bad)


def test_effective_to_before_from_is_refused(conn):
    fact(conn, effective_from_utc=T1, effective_to_utc=T1)
    with pytest.raises(StoreError):
        fact(conn, raw_sha256=sha("b"), effective_from_utc=T2, effective_to_utc=T1)


# ---- facts_as_of ---------------------------------------------------------------------------------

def test_as_of_before_known_at_returns_nothing_at_or_after_returns_the_fact(conn):
    fid = fact(conn, fetched_at_utc=T2)
    assert facts_as_of(conn, T1) == []
    assert facts_as_of(conn, "2026-01-02T23:59:59Z") == []
    assert [r["fact_version_id"] for r in facts_as_of(conn, T2)] == [fid]
    assert [r["fact_version_id"] for r in facts_as_of(conn, T3)] == [fid]


def test_as_of_uses_published_time_when_proven(conn):
    fid = fact(conn, published_at_utc=T0, published_proven=True, fetched_at_utc=T2)
    assert [r["fact_version_id"] for r in facts_as_of(conn, T1)] == [fid]


def test_two_versions_return_the_right_one_at_each_time(conn):
    v1 = fact(conn, fetched_at_utc=T1)
    v2 = fact(conn, raw_sha256=sha("b"), payload_json='{"v": 2}', fetched_at_utc=T3)
    assert facts_as_of(conn, T0) == []
    assert [r["fact_version_id"] for r in facts_as_of(conn, T2)] == [v1]
    got = facts_as_of(conn, T3)
    assert [r["fact_version_id"] for r in got] == [v2] and json.loads(got[0]["payload_json"]) == {"v": 2}


def test_one_row_per_domain_entity_and_source_and_filters(conn):
    src(conn, "example_other")
    fact(conn)
    fact(conn, source_id="example_other")
    fact(conn, entity_key="XYZ")
    fact(conn, domain="K3_CAL")
    assert len(facts_as_of(conn, T3)) == 4
    assert len(facts_as_of(conn, T3, domain="K2_NEWS")) == 3
    assert len(facts_as_of(conn, T3, domain="K2_NEWS", entity_key="ABC")) == 2
    assert facts_as_of(conn, T3, entity_key="NOPE") == []
    with pytest.raises(StoreError):
        facts_as_of(conn, T3, domain="BOGUS")


def test_for_outside_llm_excludes_restricted_sources_and_keeps_allowed(conn):
    src(conn, "example_closed", allow_external_llm=False)
    open_id = fact(conn)
    closed_id = fact(conn, source_id="example_closed")
    assert {r["fact_version_id"] for r in facts_as_of(conn, T3)} == {open_id, closed_id}
    assert [r["fact_version_id"] for r in facts_as_of(conn, T3, for_outside_llm=True)] == [open_id]


# ---- source registry -----------------------------------------------------------------------------

def test_finnhub_with_external_llm_refused_in_code_and_in_database(conn):
    with pytest.raises(StoreError, match="Finnhub"):
        src(conn, "finnhub_local", allow_external_llm=True)
    with pytest.raises(StoreError, match="Finnhub"):
        src(conn, "My-FinnHub-feed", allow_external_llm=True)
    for sid in ("finnhub_local", "FINNHUB_X"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)",
                         (sid, "https://example.invalid/t", "x", 1, 1, "GENERIC_CLIENT", 0,
                          "2026-01-01T00:00:00.000000Z", None))
    assert count(conn, "source_registry") == 1
    src(conn, "finnhub_local", allow_external_llm=False)  # the allowed case
    assert count(conn, "source_registry") == 2


def test_personal_use_not_ok_refused_in_code_and_in_database(conn):
    with pytest.raises(StoreError, match="personal_use_ok"):
        src(conn, "example_nope", personal_use_ok=False)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)",
                     ("example_nope", "u", "x", 0, 0, "GENERIC_CLIENT", 0, "2026-01-01T00:00:00.000000Z", None))


def test_bad_ua_policy_refused(conn):
    src(conn, "example_sec", ua_policy="SEC_DECLARED_CONTACT")
    with pytest.raises(StoreError):
        src(conn, "example_bad", ua_policy="WHATEVER")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)",
                     ("example_bad", "u", "x", 1, 0, "WHATEVER", 0, "2026-01-01T00:00:00.000000Z", None))


def test_re_register_identical_is_a_no_op_different_is_refused(conn):
    src(conn)  # identical to the fixture's registration
    assert count(conn, "source_registry") == 1
    with pytest.raises(StoreError, match="new source_id"):
        src(conn, terms_read_at_utc=T2)
    with pytest.raises(StoreError, match="new source_id"):
        src(conn, allow_external_llm=False)
    src(conn, "example_public_v2", terms_read_at_utc=T2)  # a terms change is a new id
    assert count(conn, "source_registry") == 2
    old = conn.execute("SELECT terms_read_at_utc FROM source_registry WHERE source_id = 'example_public'")
    assert old.fetchone()[0].startswith("2026-01-01")  # the old row is intact


def test_fact_for_unregistered_source_refused_in_code_and_in_database(conn):
    with pytest.raises(StoreError, match="not registered"):
        fact(conn, source_id="example_unknown")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _raw_insert(conn, source_id="example_unknown")
    assert count(conn) == 0


# ---- fact field guards ---------------------------------------------------------------------------

def test_every_domain_is_accepted_and_a_bad_domain_refused(conn):
    for d in store.DOMAINS:
        fact(conn, domain=d)
    assert count(conn) == len(store.DOMAINS)
    with pytest.raises(StoreError, match="domain"):
        fact(conn, domain="K9_OTHER")
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, domain="K9_OTHER")


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, sha("a").upper(), sha("a")[:63], sha("a") + "0", "g" * 64, None])
def test_bad_sha_refused(conn, bad):
    with pytest.raises(StoreError, match="sha"):
        fact(conn, raw_sha256=bad)
    if isinstance(bad, str):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, raw_sha256=bad)
    assert count(conn) == 0


@pytest.mark.parametrize("bad", ["{not json", "", "undefined", None])
def test_invalid_json_refused(conn, bad):
    with pytest.raises(StoreError, match="JSON"):
        fact(conn, payload_json=bad)
    if isinstance(bad, str):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, payload_json=bad)
    assert count(conn) == 0


def test_valid_json_forms_accepted(conn):
    for i, text in enumerate(['{"a": [1, 2]}', "[]", "null", '"s"', "3"]):
        fact(conn, entity_key=f"K{i}", payload_json=text)
    assert count(conn) == 5


def test_refused_write_leaves_no_open_transaction(conn):
    with pytest.raises(StoreError):
        fact(conn, source_id="example_unknown")
    assert not conn.in_transaction
    fact(conn)  # and the connection still works


# ---- one read path -------------------------------------------------------------------------------

# Public callables of the module, by exact name. Adding a public function fails this test on purpose:
# the author must then show it cannot return fact rows, and add it here.
_ALLOWED_PUBLIC = {"connect", "register_source", "add_fact", "facts_as_of", "StoreError"}
_MAY_SELECT_FACTS = {"facts_as_of", "_latest_fact_for_key"}  # the only functions that read the facts table


def _module_functions():
    tree = ast.parse(inspect.getsource(store))
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_public_surface_is_exactly_the_allowed_names():
    public = {name for name, obj in vars(store).items()
              if not name.startswith("_") and getattr(obj, "__module__", None) == store.__name__
              and (inspect.isfunction(obj) or inspect.isclass(obj))}
    assert public == _ALLOWED_PUBLIC


def test_only_facts_as_of_returns_fact_rows():
    # (1) the only code that reads the facts table is facts_as_of and one private, id-and-hash-only helper
    reading = set()
    for fn in _module_functions():
        body = ast.get_source_segment(inspect.getsource(store), fn)
        if re.search(r"(?i)\b(from|join)\s+knowledge_facts\b", body):
            reading.add(fn.name)
    assert reading == _MAY_SELECT_FACTS
    # (2) the private helper never selects the payload or the other columns
    helper = inspect.getsource(store._latest_fact_for_key)
    assert "SELECT fact_version_id, raw_sha256 FROM knowledge_facts" in helper and "*" not in helper
    # (3) no public function other than facts_as_of is annotated as returning rows or a connection cursor
    for name in _ALLOWED_PUBLIC - {"facts_as_of", "StoreError"}:
        ret = str(inspect.signature(getattr(store, name)).return_annotation)
        assert "dict" not in ret and "Row" not in ret and "list" not in ret, name
    assert "list[dict]" in str(inspect.signature(store.facts_as_of).return_annotation)


def test_write_functions_return_no_rows(conn):
    assert register_source.__annotations__["return"] == "None"
    assert isinstance(fact(conn), int)

