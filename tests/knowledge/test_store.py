"""Tests for the knowledge store. Every guard is tested both ways: the allowed case goes through and
the forbidden case is refused (in code and, where the rule lives in the database, through raw SQL)."""
import ast
import hashlib
import inspect
import json
import re
import sqlite3
import sys
from pathlib import Path

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


def _raw_insert(conn, verb="INSERT", **over):
    cols = dict(domain="K2_NEWS", entity_key="ABC", effective_from_utc="2026-01-01T00:00:00.000000Z",
                effective_to_utc=None, published_at_utc="2026-01-01T00:00:00.000000Z",
                fetched_at_utc="2026-01-02T00:00:00.000000Z", known_at_utc="2026-01-02T00:00:00.000000Z",
                known_at_basis="FETCHED", source_id="example_public", raw_sha256=sha("a"),
                payload_json="{}")
    cols.update(over)
    names = ", ".join(cols)
    conn.execute(f"{verb} INTO knowledge_facts ({names}) VALUES ({', '.join('?' * len(cols))})",
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
    "2026-01-02T00:00:00Z\n", "\u0662\u0660\u0662\u0666-\u0660\u0661-\u0660\u0662T00:00:00Z",
    "2026-01-02T\u0660\u0660:00:00Z", " 2026-01-02T00:00:00Z",
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


# ---- REPLACE cannot get past append-only ---------------------------------------------------------

_REG_ROW = ("example_public", "https://example.invalid/OTHER", "x", 1, 1, "GENERIC_CLIENT", 0,
            "2026-01-01T00:00:00.000000Z", None)


def test_replace_cannot_overwrite_a_fact_through_raw_sql(conn):
    fid = fact(conn)
    before = conn.execute("SELECT * FROM knowledge_facts").fetchall()
    for verb in ("REPLACE", "INSERT OR REPLACE", "INSERT", "INSERT OR IGNORE"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _raw_insert(conn, verb=verb.replace("REPLACE INTO", "REPLACE"), fact_version_id=fid,
                        payload_json='{"v": 99}')
    assert [tuple(r) for r in conn.execute("SELECT * FROM knowledge_facts")] == [tuple(r) for r in before]


_BAD_IDS = [-1, 0, -5, 2, 3, 500, 2**62, 9223372036854775807]


def test_raw_inserts_with_explicit_ids_that_break_the_sequence_are_refused_and_never_wedge_the_store(conn):
    # Rule: ids are 1, 2, 3, ... with no gaps. An explicit id is allowed only if it is exactly the next one.
    fact(conn)                                   # id 1, so the next id is 2
    seq_before = conn.execute("SELECT * FROM sqlite_sequence").fetchall()
    for bad in [i for i in _BAD_IDS if i != 2]:
        n = count(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, fact_version_id=bad)
        assert count(conn) == n
        assert [tuple(r) for r in conn.execute("SELECT * FROM sqlite_sequence")] == [tuple(r) for r in seq_before]
        new = fact(conn, raw_sha256=sha(f"after-{bad}"))      # an auto id still works afterwards
        assert new == n + 1 and count(conn) == n + 1
        seq_before = conn.execute("SELECT * FROM sqlite_sequence").fetchall()
    assert [r[0] for r in conn.execute("SELECT fact_version_id FROM knowledge_facts ORDER BY 1")] == \
        list(range(1, count(conn) + 1))


def test_the_next_explicit_id_and_auto_ids_are_allowed(conn):
    fact(conn)
    _raw_insert(conn, fact_version_id=2)        # exactly the next id
    _raw_insert(conn)                           # auto id (SQLite shows a trigger -1 for this; it must not misfire)
    assert fact(conn, raw_sha256=sha("z")) == 4
    assert [r[0] for r in conn.execute("SELECT fact_version_id FROM knowledge_facts ORDER BY 1")] == [1, 2, 3, 4]


def test_an_explicit_id_of_the_first_row_must_be_one(conn):
    for bad in (-1, 0, 2, 9223372036854775807):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, fact_version_id=bad)
    assert count(conn) == 0 and fact(conn) == 1


def test_replace_of_the_last_row_is_still_refused(conn):
    fact(conn)
    last = fact(conn, raw_sha256=sha("b"), payload_json='{"v": 2}')
    for verb in ("REPLACE", "INSERT OR REPLACE"):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, verb=verb, fact_version_id=last, payload_json='{"v": 99}')
    assert conn.execute("SELECT payload_json FROM knowledge_facts WHERE fact_version_id = ?",
                        (last,)).fetchone()[0] == '{"v": 2}'


def test_replace_cannot_rewrite_a_source_through_raw_sql(conn):
    before = tuple(conn.execute("SELECT * FROM source_registry").fetchone())
    for verb in ("REPLACE", "INSERT OR REPLACE", "INSERT"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"{verb} INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)", _REG_ROW)
    assert tuple(conn.execute("SELECT * FROM source_registry").fetchone()) == before
    conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)", ("example_new",) + _REG_ROW[1:])
    assert count(conn, "source_registry") == 2


# ---- one definition of "current version" ---------------------------------------------------------

def day(n):
    return f"2026-01-{n:02d}T00:00:00Z"


def _as_of_sha(conn, n):
    rows = facts_as_of(conn, day(n))
    assert len(rows) <= 1
    return rows[0]["raw_sha256"] if rows else None


def test_late_read_after_a_proven_published_version_is_stored(conn):
    A, B = sha("a"), sha("b")
    fact(conn, raw_sha256=A, fetched_at_utc=day(10))
    fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(20))
    n = count(conn)
    fact(conn, raw_sha256=B, fetched_at_utc=day(30))   # at day 30 the current version is A, so this is new
    assert count(conn) == n + 1
    assert _as_of_sha(conn, 31) == B
    assert _as_of_sha(conn, 7) == B
    assert _as_of_sha(conn, 12) == A


def test_backfilled_older_version_then_a_later_read(conn):
    A, B = sha("a"), sha("b")
    fact(conn, raw_sha256=A, fetched_at_utc=day(20))
    fact(conn, raw_sha256=B, fetched_at_utc=day(10))   # backfill: older known_at, stored later
    fact(conn, raw_sha256=B, fetched_at_utc=day(30))
    assert _as_of_sha(conn, 15) == B
    assert _as_of_sha(conn, 25) == A
    assert _as_of_sha(conn, 31) == B
    assert count(conn) == 3


def test_unchanged_content_at_a_later_time_stores_nothing_even_after_a_backfill(conn):
    A, B = sha("a"), sha("b")
    first = fact(conn, raw_sha256=A, fetched_at_utc=day(20))
    fact(conn, raw_sha256=B, fetched_at_utc=day(10))
    assert fact(conn, raw_sha256=A, fetched_at_utc=day(25)) == first   # current at day 25 is A
    assert count(conn) == 2


def test_same_known_at_ties_go_to_the_highest_id(conn):
    A, B = sha("a"), sha("b")
    fact(conn, raw_sha256=A, fetched_at_utc=day(10))
    second = fact(conn, raw_sha256=B, fetched_at_utc=day(10))
    assert [r["fact_version_id"] for r in facts_as_of(conn, day(10))] == [second]
    assert fact(conn, raw_sha256=B, fetched_at_utc=day(10)) == second


# ---- impossible dates, types ---------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["2026-13-45T99:99:99.000000Z", "2026-02-30T00:00:00.000000Z",
                                 "2026-02-29T00:00:00.000000Z", "2026-01-02T24:00:00.000000Z",
                                 "2026-01-02T00:00:60.000000Z", "2026-00-10T00:00:00.000000Z"])
def test_database_refuses_impossible_dates_through_raw_sql(conn, bad):
    for col in ("effective_from_utc", "published_at_utc"):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, **{col: bad})
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, fetched_at_utc=bad, known_at_utc=bad)
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, effective_to_utc=bad)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)",
                     ("example_d", "u", "x", 1, 0, "GENERIC_CLIENT", 0, bad, None))
    assert count(conn) == 0 and count(conn, "source_registry") == 1


def test_a_real_leap_day_is_accepted_by_code_and_database(conn):
    _raw_insert(conn, effective_from_utc="2028-02-29T00:00:00.000000Z")
    fact(conn, raw_sha256=sha("z"), effective_from_utc="2028-02-29T00:00:00Z", fetched_at_utc="2028-03-01T00:00:00Z")
    assert count(conn) == 2


@pytest.mark.parametrize("field", ["personal_use_ok", "allow_external_llm"])
@pytest.mark.parametrize("bad", ["no", "yes", "1", "", 2, -1, 1.0, None, [1], b"1"])
def test_flags_must_be_bool_or_int_zero_or_one(conn, field, bad):
    with pytest.raises(StoreError, match=field):
        src(conn, "example_flag", **{field: bad})
    assert count(conn, "source_registry") == 1


def test_flags_accept_bool_and_int(conn):
    src(conn, "example_f1", personal_use_ok=1, allow_external_llm=0)
    src(conn, "example_f2", personal_use_ok=True, allow_external_llm=False)
    src(conn, "example_f3", personal_use_ok=True, allow_external_llm=1)
    assert [r[0] for r in conn.execute("SELECT allow_external_llm FROM source_registry WHERE source_id LIKE 'example_f%'"
                                       " ORDER BY source_id")] == [0, 0, 1]
    with pytest.raises(StoreError, match="personal_use_ok"):
        src(conn, "example_f4", personal_use_ok=0)


@pytest.mark.parametrize("bad", [b'{"v": 1}', bytearray(b"{}"), {"v": 1}, [1], 3, None])
def test_payload_must_be_text(conn, bad):
    with pytest.raises(StoreError, match="payload_json"):
        fact(conn, payload_json=bad)
    assert count(conn) == 0


@pytest.mark.parametrize("bad", [b'{"v": 1}', b"{}", b"3"])   # (a number is stored as text by column affinity)
def test_database_refuses_non_text_payload_through_raw_sql(conn, bad):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, payload_json=bad)
    assert count(conn) == 0


# ---- every flag goes through one check -----------------------------------------------------------

_BAD_FLAGS = ["no", "False", "True", "0", "1", 2, -1, 1.0, 0.0, None, "", [], b"\x01"]


@pytest.mark.parametrize("bad", _BAD_FLAGS)
def test_published_proven_must_be_bool_or_int_zero_or_one(conn, bad):
    with pytest.raises(StoreError, match="published_proven"):
        fact(conn, published_at_utc=T0, published_proven=bad)
    assert count(conn) == 0


@pytest.mark.parametrize("good,basis", [(True, "PUBLISHED"), (1, "PUBLISHED"), (False, "FETCHED"), (0, "FETCHED")])
def test_published_proven_accepts_bool_and_zero_one(conn, good, basis):
    fid = fact(conn, published_at_utc=T0, published_proven=good, fetched_at_utc=T2)
    assert conn.execute("SELECT known_at_basis FROM knowledge_facts WHERE fact_version_id = ?",
                        (fid,)).fetchone()[0] == basis


@pytest.mark.parametrize("bad", _BAD_FLAGS)
def test_for_outside_llm_must_be_bool_or_int_zero_or_one(conn, bad):
    fact(conn)
    with pytest.raises(StoreError, match="for_outside_llm"):
        facts_as_of(conn, T3, for_outside_llm=bad)


@pytest.mark.parametrize("good", [True, 1, False, 0])
def test_for_outside_llm_accepts_bool_and_zero_one(conn, good):
    src(conn, "example_closed", allow_external_llm=False)
    fact(conn)
    fact(conn, source_id="example_closed")
    assert len(facts_as_of(conn, T3, for_outside_llm=good)) == (1 if good else 2)


def test_no_function_in_the_module_takes_a_flag_without_the_flag_check():
    # every bool-like parameter of the public functions is named here; adding one without a check fails
    flags = {"personal_use_ok", "allow_external_llm", "published_proven", "for_outside_llm"}
    seen = set()
    for name in ("register_source", "add_fact", "facts_as_of"):
        for p in inspect.signature(getattr(store, name)).parameters.values():
            if p.default is True or p.default is False or "bool" in str(p.annotation) or p.name in flags:
                seen.add(p.name)
    assert seen == flags
    src_text = inspect.getsource(store)
    for f in flags:
        assert f'_flag({f}, "{f}")' in src_text, f


# ---- NUL bytes and exact lengths -----------------------------------------------------------------

NUL_TIME = "2026-01-02T00:00:00.000000Z"


def test_database_refuses_nul_tricks_through_raw_sql(conn):
    reg = ("example_ok", "https://example.invalid/t", "x", 1, 1, "GENERIC_CLIENT", 0, NUL_TIME, None)
    sql = "INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)"
    conn.execute(sql, reg)                                   # the clean row goes in (two-way)
    # a name that hides "finnhub" behind a NUL, with outside-model access on
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("x\x00finnhub",) + reg[1:])
    for i, v in [(0, "ok\x00tail"), (1, "u\x00tail"), (2, "x\x00tail"), (5, "GENERIC_CLIENT\x00"),
                 (7, NUL_TIME + "\x00junk"), (8, "note\x00tail")]:
        row = list(reg)
        row[0] = "example_nul"
        row[i] = v
        if i == 0:
            row[0] = v
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, row)
    assert count(conn, "source_registry") == 2              # the fixture's row and the clean row
    _raw_insert(conn, source_id="example_ok")               # facts: clean row in
    for col, v in [("fetched_at_utc", NUL_TIME + "\x00junk"), ("effective_from_utc", NUL_TIME + "\x00junk"),
                   ("effective_to_utc", NUL_TIME + "\x00junk"), ("published_at_utc", NUL_TIME + "\x00junk"),
                   ("payload_json", "{}\x00garbage"), ("entity_key", "ABC\x00x"),
                   ("raw_sha256", sha("a")[:63] + "\x00"), ("raw_sha256", sha("a") + "\x00"),
                   ("domain", "K2_NEWS\x00"), ("known_at_basis", "FETCHED\x00"),
                   ("raw_sha256", sha("a").encode())]:       # the last one is a BLOB
        over = {col: v}
        if col == "fetched_at_utc":
            over["known_at_utc"] = v
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, **over)
    assert count(conn) == 1


def test_database_refuses_year_zero_and_code_does_too(conn):
    for bad in ("0000-01-01T00:00:00.000000Z",):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, effective_from_utc=bad)
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, fetched_at_utc=bad, known_at_utc=bad)
    with pytest.raises(StoreError):
        fact(conn, effective_from_utc="0000-01-01T00:00:00Z")
    with pytest.raises(StoreError):
        facts_as_of(conn, "0000-01-01T00:00:00Z")
    _raw_insert(conn, effective_from_utc="0001-01-01T00:00:00.000000Z")   # the first real year is fine
    assert count(conn) == 1


@pytest.mark.parametrize("bad", [sha("a")[:63] + "\x00", sha("a") + "\x00", "\x00" + sha("a")[1:], sha("a") + "\n"])
def test_add_fact_refuses_nul_or_newline_tailed_sha(conn, bad):
    with pytest.raises(StoreError, match="64 lowercase hex"):      # the code's own message, not the database's
        fact(conn, raw_sha256=bad)
    assert count(conn) == 0


@pytest.mark.parametrize("field", ["entity_key"])
def test_add_fact_refuses_nul_in_text_fields(conn, field):
    with pytest.raises(StoreError, match=field):
        fact(conn, **{field: "ABC\x00x"})
    with pytest.raises(StoreError, match="payload_json"):
        fact(conn, payload_json="{}\x00garbage")
    assert count(conn) == 0


# ---- register_source checks types ----------------------------------------------------------------

@pytest.mark.parametrize("field", ["terms_url", "licence_tag", "source_id", "notes"])
@pytest.mark.parametrize("bad", [5, b"x", 1.5, ["x"], True])
def test_register_source_text_fields_must_be_str(conn, field, bad):
    over = {field: bad}
    over.setdefault("source_id", "example_t")
    with pytest.raises(StoreError, match=field):
        src(conn, **over)
    assert count(conn, "source_registry") == 1


def test_register_source_text_fields_refuse_empty_and_nul_and_accept_notes_none(conn):
    for field in ("terms_url", "licence_tag"):
        with pytest.raises(StoreError, match=field):
            src(conn, "example_t", **{field: ""})
        with pytest.raises(StoreError, match=field):
            src(conn, "example_t", **{field: "a\x00b"})
    with pytest.raises(StoreError, match="source_id"):
        src(conn, "a\x00b")
    with pytest.raises(StoreError, match="notes"):
        src(conn, "example_t", notes="a\x00b")
    src(conn, "example_t", notes=None)
    src(conn, "example_t2", notes="plain note")
    assert count(conn, "source_registry") == 3


def test_terms_url_is_stored_as_given_never_cast(conn):
    with pytest.raises(StoreError, match="terms_url"):
        src(conn, "example_t", terms_url=5)
    assert conn.execute("SELECT count(*) FROM source_registry WHERE source_id = 'example_t'").fetchone()[0] == 0


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), -1, -0.01, "1", None, True, b"1"])
def test_register_source_cost_must_be_a_finite_real_not_below_zero(conn, bad):
    with pytest.raises(StoreError, match="cost_usd_monthly"):
        src(conn, "example_c", cost_usd_monthly=bad)
    assert count(conn, "source_registry") == 1


@pytest.mark.parametrize("good", [0, 0.0, 12.5, 3, 1e6])
def test_register_source_cost_accepts_finite_non_negative(conn, good):
    src(conn, "example_c", cost_usd_monthly=good)
    assert conn.execute("SELECT cost_usd_monthly FROM source_registry WHERE source_id = 'example_c'"
                        ).fetchone()[0] == good


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1.0])
def test_database_refuses_bad_cost_through_raw_sql(conn, bad):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)",
                     ("example_c", "u", "x", 1, 0, "GENERIC_CLIENT", bad, NUL_TIME, None))
    assert count(conn, "source_registry") == 1


# ---- schema version ------------------------------------------------------------------------------

def test_new_file_gets_the_current_version_and_reopens(tmp_path):
    path = tmp_path / "new.db"
    c = connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION >= 1
    c.close()
    connect(path).close()                                       # an up-to-date store opens again


def test_old_store_without_a_version_is_refused_and_left_untouched(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)       # what an early build made: the tables, fewer checks, no version
    raw.executescript("CREATE TABLE source_registry (source_id TEXT PRIMARY KEY);"
                      "CREATE TABLE knowledge_facts (fact_version_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                      " payload_json TEXT);")
    raw.close()
    with pytest.raises(StoreError, match="version"):
        connect(path)
    raw = sqlite3.connect(path)
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    assert not any(n.startswith(("knowledge_facts_no", "source_registry_no")) for n in names)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    raw.close()


@pytest.mark.parametrize("version", [store.SCHEMA_VERSION + 1, store.SCHEMA_VERSION + 100, 1000000])
def test_store_with_a_different_version_is_refused(tmp_path, version):
    path = tmp_path / "other.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {version}")
    raw.close()
    with pytest.raises(StoreError, match="version"):
        connect(path)


def test_a_foreign_database_is_refused(tmp_path):
    path = tmp_path / "foreign.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE something_else (x)")
    raw.close()
    with pytest.raises(StoreError, match="version"):
        connect(path)


# ---- one read path -------------------------------------------------------------------------------

# Public callables of the package, by exact (module path, name). Adding a public function or class fails
# this test on purpose: the author must then show it cannot return fact rows, and add it here.
_ALLOWED_PUBLIC = {("store.py", n) for n in ("connect", "register_source", "add_fact", "facts_as_of", "StoreError")}
# Every place that may name the facts table, as (module path, scope, kind). `_SCHEMA` is the SQL text (its
# own two lookups are checked separately below); the one insert is in add_fact's inner function.
_MAY_MENTION_FACTS = {
    ("store.py", "facts_as_of", "other"), ("store.py", "_latest_fact_for_key", "other"),
    ("store.py", "_SCHEMA", "other"), ("store.py", "run", "insert"),
}
_PKG = Path(store.__file__).parent
_MENTION = re.compile(r"knowledge_facts", re.IGNORECASE)     # catches "x", [x], `x`, main.x, main."x" alike
_INSERT_BEFORE = re.compile(r"(?i)\binto\s+(main\s*\.\s*)?[\"'`\[]*$")
_READS_FACTS = re.compile(r"(?i)\b(from|join)\s+knowledge_facts\b")


def _package_files():
    files = sorted(_PKG.rglob("*.py"))              # the whole package, subpackages included
    assert {f.relative_to(_PKG).as_posix() for f in files} >= {"__init__.py", "store.py"}
    return files


def test_public_surface_is_exactly_the_allowed_names():
    public = set()
    for f in _package_files():
        for node in ast.parse(f.read_text()).body:       # module-level defs only
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                public.add((f.relative_to(_PKG).as_posix(), node.name))
    assert public == _ALLOWED_PUBLIC
    live = {name for name, obj in vars(store).items()
            if not name.startswith("_") and getattr(obj, "__module__", None) == store.__name__
            and (inspect.isfunction(obj) or inspect.isclass(obj))}
    assert live == {n for _, n in _ALLOWED_PUBLIC}


def _mentions():
    """(file, innermost function or constant or '<module>', 'insert' or 'other') for every place any file of
    the package names the facts table in any spelling."""
    found = set()
    for f in _package_files():
        text = f.read_text()
        rel = f.relative_to(_PKG).as_posix()
        tree = ast.parse(text)
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        consts = [n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)]
        for m in _MENTION.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            kind = "insert" if _INSERT_BEFORE.search(text[max(0, m.start() - 40):m.start()]) else "other"
            inner = [fn for fn in funcs if fn.lineno <= line <= fn.end_lineno]
            if inner:
                scope = min(inner, key=lambda fn: fn.end_lineno - fn.lineno).name
            else:
                top = [c.targets[0].id for c in consts if c.lineno <= line <= c.end_lineno]
                scope = top[0] if top else "<module>"
            found.add((rel, scope, kind))
    return found


def test_the_schema_text_names_the_facts_table_for_reading_only_in_two_guards():
    matches = [store._SCHEMA[m.start():m.end() + 60] for m in _READS_FACTS.finditer(store._SCHEMA)]
    assert len(matches) == 2, matches
    assert matches[0].startswith("FROM knowledge_facts WHERE fact_version_id = NEW.fact_version_id")
    assert matches[1].startswith("FROM knowledge_facts)")       # the count behind the no-gaps id rule


def test_only_facts_as_of_returns_fact_rows():
    # (1) in every file of the package, in any spelling of the table name, the only code that names the facts
    # table is facts_as_of, one private helper that returns only an id and a hash, the SQL text, and the insert
    assert _mentions() == _MAY_MENTION_FACTS
    # (2) the private helper never selects the payload or the other columns
    helper = inspect.getsource(store._latest_fact_for_key)
    assert "SELECT fact_version_id, raw_sha256 FROM knowledge_facts" in helper and "*" not in helper
    # (3) no public function other than facts_as_of is annotated as returning rows
    for name in {n for _, n in _ALLOWED_PUBLIC} - {"facts_as_of", "StoreError"}:
        ret = str(inspect.signature(getattr(store, name)).return_annotation)
        assert "dict" not in ret and "Row" not in ret and "list" not in ret, name
    assert "list[dict]" in str(inspect.signature(store.facts_as_of).return_annotation)


_SPELLINGS = ["FROM knowledge_facts", 'FROM "knowledge_facts"', "FROM [knowledge_facts]", "FROM `knowledge_facts`",
              "FROM main.knowledge_facts", 'FROM main."knowledge_facts"', "FROM source_registry, knowledge_facts",
              "FROM KNOWLEDGE_FACTS", "JOIN main.[knowledge_facts] k"]


@pytest.mark.parametrize("where", ["sneaky.py", "sub/deep.py", "sub/more/deeper.py", "__init__.py"])
@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_the_scan_catches_a_reader_hidden_anywhere_in_the_package(tmp_path, monkeypatch, spelling, where):
    extra = tmp_path / "pkg"
    (extra / "sub" / "more").mkdir(parents=True)
    (extra / "__init__.py").write_text("")
    (extra / "store.py").write_text(Path(store.__file__).read_text())
    assert _mentions_in(monkeypatch, extra) == _MAY_MENTION_FACTS          # the clean copy matches
    target = extra / where
    target.write_text(target.read_text() if target.exists() else "")
    with target.open("a") as fh:
        fh.write(f"def all_rows(c):\n    return c.execute({('SELECT * ' + spelling)!r})\n")
    got = _mentions_in(monkeypatch, extra)
    assert got != _MAY_MENTION_FACTS and (where, "all_rows", "other") in got


def _mentions_in(monkeypatch, pkg):
    monkeypatch.setattr(sys.modules[__name__], "_PKG", pkg)
    return _mentions()


def test_write_functions_return_no_rows(conn):
    assert register_source.__annotations__["return"] == "None"
    assert isinstance(fact(conn), int)
