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
    with pytest.raises(sqlite3.IntegrityError):
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


_BAD_IDS = [-1, 0, -5, 2**62, 2**62 + 1, 9223372036854775807]
_BAD_ROWS = {                      # each one is refused by the database for its own reason
    "bad sha": dict(raw_sha256="zz"), "null entity_key": dict(entity_key=None),
    "bad domain": dict(domain="K9_OTHER"), "bad json": dict(payload_json="{nope"),
    "unregistered source": dict(source_id="example_unknown"), "bad time": dict(fetched_at_utc="2026-13-45T99:99:99.000000Z"),
}


def _values_sql(rows):
    """One multi-row INSERT ... VALUES (...),(...) statement text and its parameters (ids are automatic)."""
    cols = list(_raw_row())
    marks = "(" + ", ".join("?" * len(cols)) + ")"
    return (f"INTO knowledge_facts ({', '.join(cols)}) VALUES " + ", ".join([marks] * len(rows)),
            [v for r in rows for v in r.values()])


def _raw_row(**over):
    row = dict(domain="K2_NEWS", entity_key="ABC", effective_from_utc="2026-01-01T00:00:00.000000Z",
               effective_to_utc=None, published_at_utc="2026-01-01T00:00:00.000000Z",
               fetched_at_utc="2026-01-02T00:00:00.000000Z", known_at_utc="2026-01-02T00:00:00.000000Z",
               known_at_basis="FETCHED", source_id="example_public", raw_sha256=sha("a"), payload_json="{}")
    row.update(over)
    return row


def test_there_is_no_autoincrement_and_no_sequence_table_to_wedge(conn):
    fact(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "sqlite_sequence" not in names
    assert "AUTOINCREMENT" not in conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'knowledge_facts'").fetchone()[0].upper()
    with pytest.raises(sqlite3.OperationalError):          # a direct write to the sequence table cannot happen
        conn.execute("UPDATE sqlite_sequence SET seq = 9223372036854775807")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO sqlite_sequence VALUES ('knowledge_facts', 9223372036854775807)")
    assert fact(conn, raw_sha256=sha("b")) == 2


def test_raw_inserts_with_explicit_ids_that_are_not_the_next_one_are_refused_and_never_wedge_the_store(conn):
    # Rule: ids come from SQLite (the highest id plus one). An explicit id is allowed only if it is exactly that.
    fact(conn)                                   # id 1, so the next id is 2
    for bad in _BAD_IDS + [1, 3, 500]:
        n = count(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, fact_version_id=bad)
        assert count(conn) == n
        assert fact(conn, raw_sha256=sha(f"after-{bad}")) == n + 1       # an auto id still works afterwards
    ids = [r[0] for r in conn.execute("SELECT fact_version_id FROM knowledge_facts ORDER BY 1")]
    assert ids == list(range(1, len(ids) + 1))


def test_explicit_minus_one_is_refused_by_the_check_not_mistaken_for_an_auto_id(conn):
    # a BEFORE trigger shows -1 for an automatic id, so the table's own CHECK must be what stops an explicit -1
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _raw_insert(conn, fact_version_id=-1)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _raw_insert(conn, verb="INSERT OR REPLACE", fact_version_id=-1)
    assert count(conn) == 0
    assert fact(conn) == 1                                  # and the automatic id (-1 inside the trigger) works
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'knowledge_facts'").fetchone()[0]
    assert "BETWEEN 1 AND 4611686018427387904" in sql


@pytest.mark.parametrize("reconnect", [False, True])
@pytest.mark.parametrize("shape", ["single", "good_then_bad", "bad_then_good", "bad_good_bad"])
@pytest.mark.parametrize("verb", ["INSERT", "INSERT OR IGNORE", "INSERT OR REPLACE", "REPLACE", "INSERT OR FAIL",
                                  "INSERT OR ROLLBACK"])
@pytest.mark.parametrize("why", sorted(_BAD_ROWS))
@pytest.mark.parametrize("seeded", [False, True])
def test_a_bad_row_under_any_verb_or_shape_never_wedges_add_fact(tmp_path, why, verb, shape, seeded, reconnect):
    path = tmp_path / "w.db"
    c = connect(path)
    src(c)
    if seeded:
        fact(c)
    good, bad = _raw_row(), _raw_row(**_BAD_ROWS[why])
    rows = {"single": [bad], "good_then_bad": [good, bad], "bad_then_good": [bad, good],
            "bad_good_bad": [bad, good, bad]}[shape]
    text, params = _values_sql(rows)
    before = [tuple(r) for r in c.execute("SELECT * FROM knowledge_facts")]
    try:
        c.execute(f"{verb} {text}", params)
    except sqlite3.IntegrityError:
        pass
    after = [tuple(r) for r in c.execute("SELECT * FROM knowledge_facts")]
    assert after[:len(before)] == before                       # nothing already stored was changed or lost
    if reconnect:
        c.close()
        c = connect(path)
    top = max([r[0] for r in c.execute("SELECT fact_version_id FROM knowledge_facts")], default=0)
    new = fact(c, raw_sha256=sha("fresh"), entity_key="NEW")      # add_fact still works
    assert new == top + 1
    ids = [r[0] for r in c.execute("SELECT fact_version_id FROM knowledge_facts ORDER BY 1")]
    assert ids == sorted(set(ids)) and len(ids) == count(c)
    c.close()


def test_bad_rows_under_ignore_leave_a_plain_connection_and_the_next_write_working(tmp_path):
    path = tmp_path / "plain.db"
    c = connect(path)
    src(c)
    c.close()
    raw = sqlite3.connect(path, isolation_level=None)             # foreign keys off, nothing of ours
    for why in sorted(_BAD_ROWS):
        text, params = _values_sql([_raw_row(**_BAD_ROWS[why])])
        try:
            raw.execute(f"INSERT OR IGNORE {text}", params)
        except sqlite3.IntegrityError:
            pass
    assert raw.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0] == 0
    raw.close()
    c = connect(path)
    assert fact(c) == 1


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


def test_late_read_after_a_proven_published_version_case_a(conn):
    A, B = sha("a"), sha("b")
    fact(conn, raw_sha256=A, fetched_at_utc=day(10))
    fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(20))
    assert [_as_of_sha(conn, d) for d in (7, 12, 20, 25)] == [B, A, B, B]      # B was fetched on day 20
    n = count(conn)
    fact(conn, raw_sha256=B, fetched_at_utc=day(30))        # B is already current at day 30: nothing to store
    assert count(conn) == n
    assert [_as_of_sha(conn, d) for d in (7, 12, 20, 25, 31)] == [B, A, B, B, B]


def test_a_proven_read_under_an_older_version_stores_a_published_row_and_a_fetched_row(conn):
    A, B = sha("a"), sha("b")
    fact(conn, raw_sha256=A, fetched_at_utc=day(10))
    got = fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(20),
               payload_json='{"v": "b"}')
    rows = conn.execute("SELECT * FROM knowledge_facts WHERE raw_sha256 = ? ORDER BY fact_version_id", (B,)).fetchall()
    assert [r["known_at_basis"] for r in rows] == ["PUBLISHED", "FETCHED"]
    assert rows[0]["known_at_utc"].startswith("2026-01-05") and rows[1]["known_at_utc"].startswith("2026-01-20")
    assert rows[0]["payload_json"] == rows[1]["payload_json"] == '{"v": "b"}'
    assert rows[0]["published_at_utc"] == rows[1]["published_at_utc"] and rows[1]["published_at_utc"].startswith("2026-01-05")
    assert got == rows[1]["fact_version_id"]                  # the id of the version current at the fetch time


def test_a_proven_read_with_nothing_between_its_times_stores_one_row(conn):
    got = fact(conn, raw_sha256=sha("b"), published_at_utc=day(5), published_proven=True, fetched_at_utc=day(20))
    assert count(conn) == 1 and got == 1
    assert _as_of_sha(conn, 20) == sha("b")


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


# ---- the duplicate rule looks at both the known_at time and the fetch time ----------------------

def test_case_a_variant_a_reread_with_the_same_proven_publication_stores_nothing(conn):
    A, B = sha("a"), sha("b")
    fact(conn, raw_sha256=A, fetched_at_utc=day(10))
    fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(20))
    n = count(conn)
    fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(30))
    assert count(conn) == n
    assert [_as_of_sha(conn, d) for d in (7, 12, 20, 25, 31)] == [B, A, B, B, B]


def test_a_proven_reread_that_matches_at_its_published_time_but_not_at_its_fetch_time_is_stored_as_fetched(conn):
    A, B = sha("a"), sha("b")
    first_b = fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(6))
    fact(conn, raw_sha256=A, fetched_at_utc=day(10))        # a different version arrives later
    n = count(conn)
    again = fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(30))
    assert again != first_b and count(conn) == n + 1
    row = conn.execute("SELECT * FROM knowledge_facts WHERE fact_version_id = ?", (again,)).fetchone()
    assert row["known_at_basis"] == "FETCHED" and row["known_at_utc"] == row["fetched_at_utc"]
    assert row["known_at_utc"].startswith("2026-01-30") and row["published_at_utc"].startswith("2026-01-05")
    assert [_as_of_sha(conn, d) for d in (7, 12, 29, 31)] == [B, A, A, B]


def test_an_identical_reread_after_that_stores_nothing(conn):
    A, B = sha("a"), sha("b")
    fact(conn, raw_sha256=A, fetched_at_utc=day(10))
    fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(20))
    stored = fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(30))
    n = count(conn)
    assert fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(31)) == stored
    assert fact(conn, raw_sha256=B, fetched_at_utc=day(31)) == stored
    assert count(conn) == n


def test_proven_reread_that_matches_at_both_times_stores_nothing(conn):
    B = sha("b")
    first = fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(20))
    assert fact(conn, raw_sha256=B, published_at_utc=day(5), published_proven=True, fetched_at_utc=day(30)) == first
    assert count(conn) == 1


def test_proven_row_that_differs_at_its_known_at_keeps_its_published_basis(conn):
    row = conn.execute("SELECT * FROM knowledge_facts WHERE fact_version_id = ?",
                       (fact(conn, raw_sha256=sha("b"), published_at_utc=day(5), published_proven=True,
                             fetched_at_utc=day(20)),)).fetchone()
    assert row["known_at_basis"] == "PUBLISHED" and row["known_at_utc"].startswith("2026-01-05")


def test_the_duplicate_rule_is_documented_in_the_docstrings():
    for doc in (store.add_fact.__doc__, store.__doc__):
        assert "BOTH" in doc and "fetch" in doc and "FETCHED" in doc and "known_at" in doc
    assert "PUBLISHED" in store.add_fact.__doc__
    for doc in (store.add_fact.__doc__, store.__doc__):
        assert "at the fetch time" in doc and "facts_as_of" in doc
    for note in ("1e999", "lone surrogate"):
        assert note in store.__doc__, note


# ---- facts need a registered source even with foreign keys off ----------------------------------

def test_a_fact_with_an_unregistered_source_is_refused_even_on_a_plain_connection(tmp_path):
    path = tmp_path / "plain2.db"
    c = connect(path)
    src(c)
    c.close()
    raw = sqlite3.connect(path, isolation_level=None)
    assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0           # nothing of ours is on this connection
    _raw_insert(raw)                                                       # a registered source goes in (two-way)
    for verb in ("INSERT", "INSERT OR IGNORE", "INSERT OR REPLACE"):
        n = raw.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0]
        try:
            _raw_insert(raw, verb=verb, source_id="example_unknown")
            refused = False
        except sqlite3.IntegrityError as exc:
            refused = "not registered" in str(exc)
        assert refused or verb == "INSERT OR IGNORE"
        assert raw.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0] == n
    with pytest.raises(sqlite3.IntegrityError, match="not registered"):
        _raw_insert(raw, source_id="example_unknown")
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(raw, source_id=None)


# ---- every bad input raises StoreError -----------------------------------------------------------

def test_huge_numbers_are_a_store_error_not_an_overflow(conn):
    for bad in (10**400, -10**400, 10**308 * 10, float("inf")):
        with pytest.raises(StoreError, match="cost_usd_monthly"):
            src(conn, "example_big", cost_usd_monthly=bad)
    src(conn, "example_big", cost_usd_monthly=10**300)     # a huge but finite cost is fine
    assert count(conn, "source_registry") == 2


_SURROGATE = "ab\ud800cd"


@pytest.mark.parametrize("field", ["terms_url", "licence_tag", "notes", "ua_policy", "source_id"])
def test_lone_surrogate_in_a_registry_text_field_is_a_store_error(conn, field):
    over = {field: _SURROGATE}
    over.setdefault("source_id", "example_s")
    with pytest.raises(StoreError):
        src(conn, **over)
    assert count(conn, "source_registry") == 1


@pytest.mark.parametrize("field", ["entity_key", "source_id", "payload_json", "raw_sha256", "domain",
                                   "fetched_at_utc", "effective_from_utc", "published_at_utc"])
def test_lone_surrogate_in_a_fact_text_field_is_a_store_error(conn, field):
    over = {field: _SURROGATE}
    if field == "payload_json":
        over[field] = '{"v": "' + _SURROGATE + '"}'
    with pytest.raises(StoreError):
        fact(conn, **over)
    assert count(conn) == 0


@pytest.mark.parametrize("kw", [dict(entity_key=_SURROGATE), dict(domain=_SURROGATE)])
def test_lone_surrogate_in_a_read_filter_is_a_store_error(conn, kw):
    with pytest.raises(StoreError):
        facts_as_of(conn, T3, **kw)
    with pytest.raises(StoreError):
        facts_as_of(conn, _SURROGATE)


@pytest.mark.parametrize("bad", ['{"v": NaN}', "NaN", "Infinity", "-Infinity", '{"v": [1, -Infinity]}',
                                 "1e999", "-1e999", '{"v": 1e999}', "[" * 100000 + "]" * 100000, '{"a": 1', "{'a': 1}"])
def test_json_that_python_or_a_reader_cannot_use_is_a_store_error(conn, bad):
    with pytest.raises(StoreError, match="payload_json"):
        fact(conn, payload_json=bad)
    assert count(conn) == 0


def test_ordinary_and_large_finite_json_is_accepted(conn):
    for i, text in enumerate(['{"v": 1.5}', "1e300", '{"a": [1, 2, {"b": null}]}', "[" * 50 + "]" * 50, '"NaN"',
                              '{"NaN": 1}']):
        fact(conn, entity_key=f"J{i}", payload_json=text)
    assert count(conn) == 6


def test_json_nan_is_also_refused_by_the_database(conn):
    for bad in ("NaN", "Infinity", "-Infinity", '{"v": NaN}'):
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert(conn, payload_json=bad)


def test_a_database_refusal_is_a_store_error_in_both_writers(conn):
    # a trigger refusal inside add_fact's insert and a CHECK refusal in register_source both come out as StoreError
    conn.execute("CREATE TEMP TRIGGER t_refuse BEFORE INSERT ON knowledge_facts "
                 "WHEN NEW.entity_key = 'REFUSED' BEGIN SELECT RAISE(ABORT, 'refused by a trigger'); END")
    with pytest.raises(StoreError, match="refused"):
        fact(conn, entity_key="REFUSED")
    assert not conn.in_transaction and count(conn) == 0
    fact(conn)


def test_opening_a_file_that_is_not_a_database_is_a_store_error(tmp_path):
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"this is not a sqlite file at all, just text" * 50)
    with pytest.raises(StoreError):
        connect(bad)
    with pytest.raises(StoreError):
        connect(tmp_path / "no_such_folder" / "x.db")


_WEIRD = [None, "", "x", "\x00", _SURROGATE, 5, -1, 1.5, float("nan"), float("inf"), 10**400, -10**400, b"x", bytearray(b"x"),
          [], {}, (), True, False, object(), "2026-01-02T00:00:00Z", '{"a": 1}', "é", "A" * 100000, 2**63, -2**63 - 1]


def _only_store_errors(call):
    try:
        call()
    except StoreError:
        pass


def test_no_raw_python_or_sqlite_error_escapes_any_public_function(conn):
    good_src = dict(source_id="example_fz", terms_url="https://example.invalid/t", licence_tag="public",
                    personal_use_ok=True, allow_external_llm=False, ua_policy="GENERIC_CLIENT",
                    terms_read_at_utc=T0, cost_usd_monthly=0, notes=None)
    good_fact = dict(domain="K2_NEWS", entity_key="ABC", source_id="example_public", raw_sha256=sha("a"),
                     payload_json="{}", effective_from_utc=T0, fetched_at_utc=T1, effective_to_utc=None,
                     published_at_utc=None, published_proven=False)
    for key in good_src:
        for w in _WEIRD:
            args = dict(good_src, **{key: w})
            args["source_id"] = args["source_id"] if key == "source_id" else f"example_fz_{abs(hash(repr(w)))}"
            _only_store_errors(lambda: register_source(conn, **args))
    for key in good_fact:
        for w in _WEIRD:
            _only_store_errors(lambda: add_fact(conn, **dict(good_fact, **{key: w})))
    for key, default in (("as_of_utc", T3), ("domain", None), ("entity_key", None), ("for_outside_llm", False)):
        for w in _WEIRD:
            kw = dict(domain=None, entity_key=None, for_outside_llm=False)
            as_of = T3
            if key == "as_of_utc":
                as_of = w
            else:
                kw[key] = w
            _only_store_errors(lambda: facts_as_of(conn, as_of, **kw))
    assert not conn.in_transaction
    assert fact(conn, raw_sha256=sha("still-works"), entity_key="END")      # and the store still works


# ---- the schema is verified every time a store is opened ----------------------------------------

_EXPECTED_OBJECTS = {
    ("table", "source_registry"), ("table", "knowledge_facts"), ("index", "knowledge_facts_lookup"),
    ("trigger", "source_registry_no_update"), ("trigger", "source_registry_no_delete"),
    ("trigger", "source_registry_no_replace"), ("trigger", "knowledge_facts_no_update"),
    ("trigger", "knowledge_facts_no_delete"), ("trigger", "knowledge_facts_ids_from_sqlite"),
    ("trigger", "knowledge_facts_source_registered"),
}


def test_a_fresh_store_has_exactly_the_expected_schema_objects(tmp_path):
    c = connect(tmp_path / "s.db")
    got = {(r[0], r[1]) for r in c.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    assert got == _EXPECTED_OBJECTS
    c.close()


@pytest.mark.parametrize("kind,name", sorted(_EXPECTED_OBJECTS - {("table", "knowledge_facts"),
                                                                   ("table", "source_registry")}))
def test_a_store_missing_a_trigger_or_index_is_refused_on_open(tmp_path, kind, name):
    path = tmp_path / "m.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute(f"DROP {kind.upper()} {name}")
    raw.close()
    with pytest.raises(StoreError, match=name):
        connect(path)


@pytest.mark.parametrize("name", ["knowledge_facts", "source_registry"])
def test_a_store_missing_a_table_is_refused_on_open(tmp_path, name):
    path = tmp_path / "t.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute(f"DROP TABLE {name}")
    raw.close()
    with pytest.raises(StoreError, match="missing|differs"):
        connect(path)


def test_a_file_stamped_with_the_current_version_but_no_schema_is_refused(tmp_path):
    path = tmp_path / "stamped.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION}")
    raw.execute("CREATE TABLE unrelated (x)")
    raw.close()
    with pytest.raises(StoreError, match="missing"):
        connect(path)
    empty = tmp_path / "stamped_empty.db"
    raw = sqlite3.connect(empty)
    raw.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION}")
    raw.execute("CREATE TABLE t (x)")
    raw.execute("DROP TABLE t")
    raw.close()
    with pytest.raises(StoreError, match="missing"):
        connect(empty)


def test_a_store_whose_trigger_was_weakened_is_refused_on_open(tmp_path):
    path = tmp_path / "weak.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TRIGGER knowledge_facts_no_update")
    raw.execute("CREATE TRIGGER knowledge_facts_no_update BEFORE UPDATE ON knowledge_facts BEGIN SELECT 1; END")
    raw.close()
    with pytest.raises(StoreError, match="differs"):
        connect(path)


def test_an_untouched_store_reopens_and_keeps_its_rows(tmp_path):
    path = tmp_path / "ok.db"
    c = connect(path)
    src(c)
    fact(c)
    c.close()
    c = connect(path)
    assert count(c) == 1
    c.close()


# ---- property test: after any write, the store answers with this read at its fetch time ----------

SEED = 56007
WRITES = 2500


def hour(n):
    return f"2026-02-{1 + n // 24:02d}T{n % 24:02d}:00:00Z"


def _rows_for(conn, key, after_id=0):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM knowledge_facts WHERE domain = 'K2_NEWS' AND entity_key = ? AND source_id = 'example_public'"
        " AND fact_version_id > ? ORDER BY fact_version_id", (key, after_id))]


def _oracle_current(rows, t):
    """An independent version of 'current at t': greatest known_at at or before t, ties to the highest id."""
    ok = [r for r in rows if r["known_at_utc"] <= t.replace("Z", ".000000Z")]
    return max(ok, key=lambda r: (r["known_at_utc"], r["fact_version_id"])) if ok else None


def test_property_random_writes_keep_the_fetch_time_invariant_and_the_skip_rule(conn):
    import random
    conn.execute("PRAGMA synchronous = OFF")        # this test is about logic; skipping thousands of disk syncs
    rng = random.Random(SEED)
    hashes = [sha(f"h{i}") for i in range(4)]
    stats = dict(skipped=0, single=0, double=0, fetched_only=0, proven=0)
    seen = {"P1": [], "P2": []}          # rows already read, per key (the table is append-only; re-read in full below)
    for step in range(WRITES):
        key = rng.choice(["P1", "P2"])
        i = rng.randrange(len(hashes))
        h = hashes[i]
        f_n = rng.randrange(0, 600)
        F = hour(f_n)
        proven = rng.random() < 0.5
        args = dict(entity_key=key, raw_sha256=h, payload_json=f'{{"h": {i}}}', fetched_at_utc=F)
        K = F
        if proven:
            K = hour(rng.randrange(0, f_n + 1))
            args.update(published_at_utc=K, published_proven=True)
            stats["proven"] += 1
        before = list(seen[key])
        at_k, at_f = _oracle_current(before, K), _oracle_current(before, F)
        same_k = at_k is not None and at_k["raw_sha256"] == h
        same_f = at_f is not None and at_f["raw_sha256"] == h
        got = fact(conn, **args)
        added = _rows_for(conn, key, before[-1]["fact_version_id"] if before else 0)
        after = before + added
        seen[key] = after
        if step % 250 == 0:
            assert _rows_for(conn, key)[:len(before)] == before, step       # append-only: nothing earlier changed
        if same_k and same_f:                                                # the R12 skip rule
            assert added == [] and got == at_f["fact_version_id"], step
            stats["skipped"] += 1
        else:
            assert 1 <= len(added) <= 2, step
            stats["single" if len(added) == 1 else "double"] += 1
            if same_k:                                                       # matched at K, not at F
                assert len(added) == 1 and added[0]["known_at_basis"] == "FETCHED", step
                stats["fetched_only"] += 1
            if len(added) == 2:
                first, second = added
                assert first["known_at_basis"] == "PUBLISHED" and second["known_at_basis"] == "FETCHED", step
                assert second["known_at_utc"] == second["fetched_at_utc"] and K != F, step
                for col in ("raw_sha256", "payload_json", "published_at_utc", "fetched_at_utc"):
                    assert first[col] == second[col], (step, col)
        # the invariant: as of the fetch time, the store answers with this read
        cur = _oracle_current(after, F)
        assert cur["raw_sha256"] == h and cur["fact_version_id"] == got, step
        mine = [r for r in facts_as_of(conn, F, domain="K2_NEWS", entity_key=key) if r["source_id"] == "example_public"]
        assert [r["fact_version_id"] for r in mine] == [got] and mine[0]["raw_sha256"] == h, step
        # and the read path agrees with the independent oracle at a random time
        for _ in range(1):
            t = hour(rng.randrange(0, 620))
            want = _oracle_current(after, t)
            have = [r for r in facts_as_of(conn, t, domain="K2_NEWS", entity_key=key)]
            assert [r["fact_version_id"] for r in have] == ([want["fact_version_id"]] if want else []), (step, t)
    assert all(v >= 20 for v in stats.values()), stats                       # every branch was exercised


# ---- unexpected objects, a silently ignoring trigger, constraints switched off -------------------

def _store_with(tmp_path, sql, name="x.db"):
    path = tmp_path / name
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.executescript(sql)
    raw.close()
    return path


@pytest.mark.parametrize("sql", [
    "CREATE TRIGGER sneaky BEFORE INSERT ON knowledge_facts BEGIN SELECT RAISE(IGNORE); END;",
    "CREATE TRIGGER sneaky AFTER INSERT ON source_registry BEGIN SELECT 1; END;",
    "CREATE INDEX sneaky ON knowledge_facts (payload_json);",
    "CREATE VIEW sneaky AS SELECT * FROM knowledge_facts;",
    "CREATE TABLE sneaky (x);",
])
def test_a_store_with_an_unexpected_object_is_refused_on_open(tmp_path, sql):
    path = _store_with(tmp_path, sql)
    with pytest.raises(StoreError, match="sneaky"):
        connect(path)


def test_add_fact_never_reports_success_for_an_insert_that_was_silently_ignored(conn):
    conn.execute("CREATE TEMP TRIGGER t_ignore BEFORE INSERT ON main.knowledge_facts "
                 "BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(StoreError):
        fact(conn)
    assert not conn.in_transaction and count(conn) == 0
    conn.execute("DROP TRIGGER t_ignore")
    assert fact(conn) == 1                                   # two-way: without the trigger it is stored


def test_connect_sets_check_constraints_on_explicitly(tmp_path):
    c = connect(tmp_path / "c.db")
    assert c.execute("PRAGMA ignore_check_constraints").fetchone()[0] == 0
    c.close()


def _raw_unchecked(path):
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("PRAGMA ignore_check_constraints = ON")              # a writer that switched the checks off
    return raw


def _force_id(path, old, new):
    """Change a row's id with the checks off and the update trigger briefly dropped, then restore the trigger."""
    raw = _raw_unchecked(path)
    raw.execute("DROP TRIGGER knowledge_facts_no_update")
    raw.execute("UPDATE knowledge_facts SET fact_version_id = ? WHERE fact_version_id = ?", (new, old))
    raw.executescript(store._SCHEMA)                                 # puts the dropped trigger back
    raw.close()


def test_a_store_with_a_minus_one_inserted_while_checks_were_off_is_refused_and_left_untouched(tmp_path):
    path = tmp_path / "neg.db"
    c = connect(path)
    src(c)
    fact(c)
    c.close()
    raw = _raw_unchecked(path)
    cols = _raw_row()
    raw.execute(f"INSERT INTO knowledge_facts (fact_version_id, {', '.join(cols)}) VALUES (-1, "
                f"{', '.join('?' * len(cols))})", list(cols.values()))
    assert raw.execute("SELECT min(fact_version_id) FROM knowledge_facts").fetchone()[0] == -1   # it went in
    raw.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(StoreError, match="integrity_check|fact_version_id"):
        connect(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    raw = sqlite3.connect(path)                                  # the id-range check on its own, past integrity_check
    with pytest.raises(StoreError, match="fact_version_id"):
        store._verify_schema(raw, path)
    raw.close()


@pytest.mark.parametrize("bad_id", [0, -7, 4611686018427387905, 9223372036854775807])
def test_a_store_with_an_id_outside_the_bound_is_refused_and_a_clean_one_opens(tmp_path, bad_id):
    path = tmp_path / "ids.db"
    c = connect(path)
    src(c)
    fact(c)
    c.close()
    connect(path).close()                                            # clean: opens
    _force_id(path, 1, bad_id)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(StoreError, match="integrity_check|fact_version_id"):
        connect(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    raw = sqlite3.connect(path)
    with pytest.raises(StoreError, match="fact_version_id"):
        store._verify_schema(raw, path)
    raw.close()
    _force_id(path, bad_id, 1)
    connect(path).close()                                            # and clean again: opens


# ---- transactions --------------------------------------------------------------------------------

def test_a_write_inside_the_callers_open_transaction_is_refused(conn):
    conn.execute("BEGIN")
    with pytest.raises(StoreError, match="transaction"):
        fact(conn)
    with pytest.raises(StoreError, match="transaction"):
        src(conn, "example_tx")
    assert conn.in_transaction                                    # the caller's own transaction is left alone
    conn.execute("ROLLBACK")
    fact(conn)
    src(conn, "example_tx")                                       # two-way: fine outside a transaction
    assert count(conn) == 1


class _FailingCommitConnection(sqlite3.Connection):
    """A real connection whose next COMMIT fails, as a full disk or a lock would."""
    fail = True

    def execute(self, sql, *args):
        if self.fail and sql.strip().upper() == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        return super().execute(sql, *args)


def _failing_commit_connection(path):
    c = sqlite3.connect(path, isolation_level=None, factory=_FailingCommitConnection)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@pytest.mark.parametrize("which", ["add_fact", "register_source"])
def test_a_failed_commit_rolls_back_and_raises_store_error(tmp_path, which):
    path = tmp_path / "fc.db"
    connect(path).close()
    wrapped = _failing_commit_connection(path)
    wrapped.fail = False
    src(wrapped)                                                  # (two-way) works with a healthy commit
    wrapped.fail = True
    with pytest.raises(StoreError, match="commit"):
        if which == "add_fact":
            fact(wrapped)
        else:
            src(wrapped, "example_c1")
    assert not wrapped.in_transaction                             # never left stuck in a transaction
    assert count(wrapped) == 0 and count(wrapped, "source_registry") == 1
    wrapped.fail = False
    fact(wrapped)                                                 # and the connection works again
    assert count(wrapped) == 1
    wrapped.close()


# ---- escaped lone surrogates inside JSON --------------------------------------------------------

@pytest.mark.parametrize("bad", ['"\\ud800"', '{"k": "a\\udc00b"}', '{"\\ud800": 1}', '[1, {"x": ["\\udfff"]}]',
                                 '{"a": {"b": {"\\ud83d": "ok"}}}'])
def test_escaped_lone_surrogates_anywhere_in_parsed_json_are_refused(conn, bad):
    with pytest.raises(StoreError, match="payload_json"):
        fact(conn, payload_json=bad)
    assert count(conn) == 0


def test_a_proper_escaped_surrogate_pair_and_real_unicode_are_accepted(conn):
    for i, text in enumerate(['"\\ud83d\\ude00"', '{"k": "caf\\u00e9", "\\u00e9": 1}', '"\u00e9"']):
        fact(conn, entity_key=f"U{i}", payload_json=text)
    assert count(conn) == 3


# ---- the extra-object check is an exact allowlist, never a name pattern -------------------------

def _poison(name, kind="TRIGGER"):
    if kind == "TRIGGER":
        return f"CREATE TRIGGER {name} BEFORE INSERT ON knowledge_facts BEGIN SELECT 1; END;"
    if kind == "VIEW":
        return f"CREATE VIEW {name} AS SELECT 1;"
    return f"CREATE TABLE {name} (x);"


@pytest.mark.parametrize("sql,name", [
    (_poison("sqliteXpoison"), "sqliteXpoison"),
    (_poison("SQLITEevil"), "SQLITEevil"),
    (_poison("sqliteYview", "VIEW"), "sqliteYview"),
    (_poison("sqliteZtbl", "TABLE"), "sqliteZtbl"),
    (_poison("sqlite"), "sqlite"),
])
def test_objects_with_sqlite_lookalike_names_are_refused(tmp_path, sql, name):
    path = _store_with(tmp_path, sql)
    with pytest.raises(StoreError, match=name):
        connect(path)


def _write_schema_row(path, kind, name, tbl, sql):
    """Put a row straight into sqlite_master, the only way to get a reserved sqlite_ name."""
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA writable_schema = ON")
    raw.execute("INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) VALUES (?,?,?,0,?)",
                (kind, name, tbl, sql))
    raw.commit()
    raw.close()


@pytest.mark.parametrize("name", ["sqlite_Xfoo", "SQLITE_evil", "sqlite_autoindex_other_1x"])
def test_a_trigger_with_a_reserved_name_written_into_the_schema_is_refused(tmp_path, name):
    path = tmp_path / "res.db"
    connect(path).close()
    _write_schema_row(path, "trigger", name, "knowledge_facts",
                      f"CREATE TRIGGER {name} BEFORE INSERT ON knowledge_facts BEGIN SELECT 1; END")
    with pytest.raises(StoreError, match=name):
        connect(path)


def test_a_fake_statistics_table_is_refused(tmp_path):
    path = tmp_path / "stat.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE IF NOT EXISTS knowledge_facts_scratch (x)")      # something for ANALYZE to count
    raw.execute("DROP TABLE knowledge_facts_scratch")
    raw.execute("ANALYZE")                                                       # makes the real sqlite_stat1
    raw.execute("PRAGMA writable_schema = ON")
    raw.execute("UPDATE sqlite_master SET sql = 'CREATE TABLE sqlite_stat1(evil)' WHERE name = 'sqlite_stat1'")
    raw.commit()
    raw.close()
    raw = sqlite3.connect(path)
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    assert "sqlite_stat1" in names
    with pytest.raises(StoreError, match="sqlite_stat1"):                        # the exact-SQL allowance on its own
        store._verify_schema(raw, path)
    raw.close()
    with pytest.raises(StoreError):
        connect(path)
    path2 = tmp_path / "stat2.db"
    connect(path2).close()
    _write_schema_row(path2, "view", "sqlite_stat1", "sqlite_stat1", "CREATE VIEW sqlite_stat1 AS SELECT 1")
    with pytest.raises(StoreError):                  # (SQLite itself calls this file malformed)
        connect(path2)


@pytest.mark.parametrize("maintenance", [["VACUUM"], ["ANALYZE"], ["ANALYZE", "VACUUM"], ["VACUUM", "ANALYZE"],
                                         ["PRAGMA optimize"]])
def test_a_store_still_opens_after_vacuum_and_analyze(tmp_path, maintenance):
    path = tmp_path / "maint.db"
    c = connect(path)
    src(c)
    fact(c)
    fact(c, raw_sha256=sha("b"))
    for stmt in maintenance:
        c.execute(stmt)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    assert ("ANALYZE" not in maintenance) or "sqlite_stat1" in names          # ANALYZE really created it
    c.close()
    c = connect(path)                                                          # opens
    assert count(c) == 2
    fact(c, raw_sha256=sha("c"))
    c.close()


# ---- two objects can share a name (triggers have their own namespace): none may hide behind another --

_PLANT = ("INSERT INTO knowledge_facts (domain, entity_key, effective_from_utc, fetched_at_utc, known_at_utc,"
          " known_at_basis, source_id, raw_sha256, payload_json) SELECT 'K2_NEWS', 'planted',"
          " NEW.effective_from_utc, NEW.fetched_at_utc, NEW.fetched_at_utc, 'FETCHED', NEW.source_id,"
          " NEW.raw_sha256, '{}' WHERE NEW.entity_key <> 'planted';")


def _planted_store(tmp_path, which, vacuum_after=False):
    """A store where a trigger and an index share a name and the later row in the schema table is the one a
    dictionary keyed by name would keep (the expected object, re-created verbatim)."""
    path = tmp_path / f"dup_{which}.db"
    connect(path).close()
    raw = sqlite3.connect(path, isolation_level=None)
    if which == "trigger_then_index":              # probe 1
        raw.execute(f"CREATE TRIGGER knowledge_facts_lookup AFTER INSERT ON knowledge_facts BEGIN {_PLANT} END")
        sql = raw.execute("SELECT sql FROM sqlite_master WHERE name = 'knowledge_facts_lookup' AND type = 'index'"
                          ).fetchone()[0]
        raw.execute("DROP INDEX knowledge_facts_lookup")
        raw.execute(sql)                           # the real index again, now AFTER the planted trigger
    else:                                          # probe 2: an extra index, then the real trigger re-created
        sql = raw.execute("SELECT sql FROM sqlite_master WHERE name = 'knowledge_facts_no_update'").fetchone()[0]
        raw.execute("DROP TRIGGER knowledge_facts_no_update")
        raw.execute("CREATE INDEX knowledge_facts_no_update ON knowledge_facts (source_id)")
        raw.execute(sql)
    if vacuum_after:
        raw.execute("VACUUM")
    raw.close()
    return path


@pytest.mark.parametrize("vacuum", [False, True])
@pytest.mark.parametrize("which", ["trigger_then_index", "index_then_trigger"])
def test_an_object_hiding_behind_another_of_the_same_name_is_refused(tmp_path, which, vacuum):
    path = _planted_store(tmp_path, which, vacuum_after=vacuum)
    with pytest.raises(StoreError, match="knowledge_facts_"):
        connect(path)
    raw = sqlite3.connect(path, isolation_level=None)     # refuse again after a VACUUM of the planted store
    raw.execute("VACUUM")
    raw.close()
    with pytest.raises(StoreError, match="knowledge_facts_"):
        connect(path)


_EXPECTED_NAMES = sorted(n for _, n in _EXPECTED_OBJECTS)
_KINDS = {
    "trigger_on_facts": ("TRIGGER", "CREATE TRIGGER {n} BEFORE INSERT ON knowledge_facts BEGIN SELECT 1; END"),
    "trigger_on_registry": ("TRIGGER", "CREATE TRIGGER {n} BEFORE INSERT ON source_registry BEGIN SELECT 1; END"),
    "view": ("VIEW", "CREATE VIEW {n} AS SELECT 1"),
    "table": ("TABLE", "CREATE TABLE {n} (x)"),
    "index": ("INDEX", "CREATE INDEX {n} ON knowledge_facts (payload_json)"),
}
_TYPE_OF = {n: t for t, n in _EXPECTED_OBJECTS}


@pytest.mark.parametrize("name", _EXPECTED_NAMES)
@pytest.mark.parametrize("kind", sorted(_KINDS))
def test_a_second_object_with_an_expected_name_is_refused_in_every_type_combination(tmp_path, kind, name):
    path = tmp_path / "pair.db"
    connect(path).close()
    raw = sqlite3.connect(path, isolation_level=None)
    same_namespace = (_KINDS[kind][0] == "TRIGGER") == (_TYPE_OF[name] == "trigger")
    if same_namespace:                                     # SQLite itself refuses these, and the store stays valid
        with pytest.raises(sqlite3.OperationalError):
            raw.execute(_KINDS[kind][1].format(n=name))
        raw.close()
        connect(path).close()
        return
    raw.execute(_KINDS[kind][1].format(n=name))            # SQLite allows this pair: the store must not
    raw.close()
    with pytest.raises(StoreError, match=name):
        connect(path)


@pytest.mark.parametrize("name", _EXPECTED_NAMES)
@pytest.mark.parametrize("kind", ["trigger", "index", "view", "table"])
def test_a_duplicate_row_written_into_the_schema_table_is_refused(tmp_path, kind, name):
    path = tmp_path / "dupw.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    sql = {"trigger": f"CREATE TRIGGER {name} BEFORE INSERT ON knowledge_facts BEGIN SELECT 1; END",
           "index": f"CREATE INDEX {name} ON knowledge_facts (payload_json)",
           "view": f"CREATE VIEW {name} AS SELECT 1", "table": f"CREATE TABLE {name} (x)"}[kind]
    raw.execute("PRAGMA writable_schema = ON")
    raw.execute("INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) VALUES (?,?,?,0,?)",
                (kind, name, "knowledge_facts", sql))
    raw.commit()
    raw.close()
    with pytest.raises(StoreError):
        connect(path)


def test_a_planted_trigger_would_really_fire_if_the_store_were_opened(tmp_path):
    # shows the probe is a real attack: opened without our checks, the planted trigger writes an extra row
    path = _planted_store(tmp_path, "trigger_then_index")
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("PRAGMA foreign_keys = ON")
    cols = _raw_row()
    raw.execute("INSERT INTO source_registry VALUES ('example_public','u','x',1,1,'GENERIC_CLIENT',0,"
                "'2026-01-01T00:00:00.000000Z',NULL)")
    raw.execute(f"INSERT INTO knowledge_facts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                list(cols.values()))
    assert raw.execute("SELECT count(*) FROM knowledge_facts WHERE entity_key = 'planted'").fetchone()[0] == 1
    raw.close()


# ---- integrity check on open ---------------------------------------------------------------------

_FACT_COLS = ("domain, entity_key, effective_from_utc, fetched_at_utc, known_at_utc, known_at_basis, source_id,"
              " raw_sha256, payload_json")


def _filled_store(tmp_path, name, rows=5):
    path = tmp_path / name
    c = connect(path)
    src(c)
    for i in range(rows):
        fact(c, entity_key=f"E{i}", raw_sha256=sha(f"row{i}"))
    c.close()
    return path


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hostile_stat1_rootpage(path):
    raw = sqlite3.connect(path)
    raw.execute("ANALYZE")
    raw.execute("PRAGMA writable_schema = ON")        # point the statistics table at the facts table's pages
    raw.execute("UPDATE sqlite_master SET rootpage = (SELECT rootpage FROM sqlite_master WHERE name = "
                "'knowledge_facts') WHERE name = 'sqlite_stat1'")
    raw.commit()
    raw.close()


def _hostile_index_restored(path):
    raw = sqlite3.connect(path)                       # 1: take the index out of the schema
    raw.execute("PRAGMA writable_schema = ON")
    row = raw.execute("SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master "
                      "WHERE name = 'knowledge_facts_lookup'").fetchone()
    raw.execute("DELETE FROM sqlite_master WHERE name = 'knowledge_facts_lookup'")
    raw.commit()
    raw.close()
    raw = sqlite3.connect(path, isolation_level=None)   # 2: write a fact while nothing maintains the index
    raw.execute(f"INSERT INTO knowledge_facts ({_FACT_COLS}) VALUES ('K2_NEWS', 'HIDDEN', "
                "'2026-01-01T00:00:00.000000Z', '2026-01-02T00:00:00.000000Z', '2026-01-02T00:00:00.000000Z', "
                f"'FETCHED', 'example_public', '{'a' * 64}', '{{}}')")
    raw.close()
    raw = sqlite3.connect(path)                       # 3: put the index back, byte for byte the same entry
    raw.execute("PRAGMA writable_schema = ON")
    raw.execute("INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) VALUES (?,?,?,?,?)", row)
    raw.commit()
    raw.close()


def _hostile_finnhub_checks_off(path):
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA ignore_check_constraints = ON")
    raw.execute("INSERT INTO source_registry VALUES ('finnhub_local','u','x',1,1,'GENERIC_CLIENT',0,"
                "'2026-01-01T00:00:00.000000Z',NULL)")
    raw.commit()
    raw.close()


_HOSTILE = {"stat1_rootpage": _hostile_stat1_rootpage, "index_entry_removed_then_restored": _hostile_index_restored,
            "finnhub_external_with_checks_off": _hostile_finnhub_checks_off}


@pytest.mark.parametrize("quick", [False, True])
@pytest.mark.parametrize("probe", sorted(_HOSTILE))
def test_a_store_that_fails_the_integrity_check_is_refused_and_left_untouched(tmp_path, probe, quick):
    path = _filled_store(tmp_path, "hostile.db")
    connect(path, quick=quick).close()                    # two-way: the clean store opens
    _HOSTILE[probe](path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA integrity_check").fetchall() != [("ok",)]     # the probe really damaged it
    raw.close()
    before = _file_hash(path)
    with pytest.raises(StoreError, match="integrity|quick_check"):
        connect(path, quick=quick)
    assert _file_hash(path) == before


@pytest.mark.parametrize("quick", [False, True])
def test_a_clean_store_opens_with_either_check_and_a_new_file_needs_none(tmp_path, quick):
    path = _filled_store(tmp_path, "clean.db")
    c = connect(path, quick=quick)
    assert count(c) == 5
    c.close()
    connect(tmp_path / "fresh.db", quick=quick).close()


@pytest.mark.parametrize("bad", ["yes", "no", 2, None, 1.0, [], b"1"])
def test_quick_must_be_a_flag(tmp_path, bad):
    with pytest.raises(StoreError, match="quick"):
        connect(tmp_path / "q.db", quick=bad)
    assert list(tmp_path.iterdir()) == []


def test_connect_defaults_to_the_full_integrity_check():
    import inspect as _inspect
    assert _inspect.signature(connect).parameters["quick"].default is False
    assert "integrity_check" in _inspect.getsource(connect) and "quick_check" in _inspect.getsource(connect)


# ---- text encoding -------------------------------------------------------------------------------

def test_a_store_whose_encoding_is_not_utf8_is_refused_on_open(tmp_path):
    path = tmp_path / "u16.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA encoding = 'UTF-16le'")
    raw.executescript(store._SCHEMA)
    raw.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION}")
    raw.close()
    assert sqlite3.connect(path).execute("PRAGMA encoding").fetchone()[0] == "UTF-16le"
    before = _file_hash(path)
    with pytest.raises(StoreError, match="encoding"):
        connect(path)
    assert _file_hash(path) == before
    c = connect(tmp_path / "u8.db")                        # two-way: a new store is UTF-8 and opens
    assert c.execute("PRAGMA encoding").fetchone()[0] == "UTF-8"
    c.close()


_BAD_UTF8 = {"lone_ff": "x'ff41'", "cesu_surrogate": "x'eda080'", "truncated_sequence": "x'e282'", "overlong": "x'c080'"}


@pytest.mark.parametrize("hexbytes", sorted(_BAD_UTF8))
def test_invalid_utf8_bytes_that_reach_the_database_are_a_store_error_on_every_read(conn, hexbytes):
    # SQLite cannot check UTF-8 validity in a CHECK (no function does it, and a subquery is not allowed there),
    # so such text can be stored through raw SQL; every read must still raise StoreError, never a raw error.
    blob = _BAD_UTF8[hexbytes]
    conn.execute(f"INSERT INTO knowledge_facts ({_FACT_COLS}) VALUES ('K2_NEWS', CAST({blob} AS TEXT), "
                 "'2026-01-01T00:00:00.000000Z', '2026-01-02T00:00:00.000000Z', '2026-01-02T00:00:00.000000Z', "
                 f"'FETCHED', 'example_public', '{'c' * 64}', '{{}}')")
    with pytest.raises(StoreError):
        facts_as_of(conn, T3)
    with pytest.raises(StoreError):
        facts_as_of(conn, T3, domain="K2_NEWS")
    conn.execute(f"INSERT INTO source_registry VALUES ('example_badtext', CAST({blob} AS TEXT), 'x', 1, 0, "
                 "'GENERIC_CLIENT', 0, '2026-01-01T00:00:00.000000Z', NULL)")
    with pytest.raises(StoreError):
        src(conn, "example_badtext")                       # reading the stored row to compare it
    assert not conn.in_transaction
    assert fact(conn, entity_key="CLEAN", raw_sha256=sha("clean")) > 0     # and the connection still works


def test_invalid_utf8_in_a_stored_store_is_a_store_error_on_open_not_a_raw_error(tmp_path):
    path = tmp_path / "badtext.db"
    c = connect(path)
    src(c)
    c.execute("INSERT INTO source_registry VALUES ('example_badtext', CAST(x'ff41' AS TEXT), 'x', 1, 0, "
              "'GENERIC_CLIENT', 0, '2026-01-01T00:00:00.000000Z', NULL)")
    c.close()
    connect(path).close()                                  # the schema itself is fine, so it opens


# ---- the read path drops anything Finnhub-named, whatever the registry says --------------------

@pytest.mark.parametrize("name", ["finnhub_local", "FINNHUB_X", "My-FinnHub-feed", "xFiNnHuBx"])
def test_for_outside_llm_never_returns_a_finnhub_source_even_if_the_registry_says_external(conn, name):
    keep = fact(conn)                                      # an allowed source, kept
    conn.execute("PRAGMA ignore_check_constraints = ON")   # a hostile writer: the registry row bypasses the checks
    conn.execute("INSERT INTO source_registry VALUES (?, 'u', 'x', 1, 1, 'GENERIC_CLIENT', 0, "
                 "'2026-01-01T00:00:00.000000Z', NULL)", (name,))
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    bad = fact(conn, source_id=name, raw_sha256=sha("finn"))
    allowed = {r["fact_version_id"] for r in facts_as_of(conn, T3)}
    assert allowed == {keep, bad}                          # two-way: without the flag both are returned
    outside = {r["fact_version_id"] for r in facts_as_of(conn, T3, for_outside_llm=True)}
    assert outside == {keep}
    assert bad not in {r["fact_version_id"] for r in facts_as_of(conn, T3, for_outside_llm=True, domain="K2_NEWS")}


def test_the_finnhub_filter_is_done_in_python_not_with_a_sql_function():
    body = inspect.getsource(store.facts_as_of)
    assert "finnhub" in body and "casefold()" in body
    sql_part = body[body.index("sql = ("):body.index("cur = conn.execute")]
    assert "finnhub" not in sql_part.lower() and "lower(" not in sql_part.lower()


def test_the_module_docstring_states_the_hostile_connection_limits():
    doc = " ".join((store.__doc__ or "").split())
    assert "temp objects on the caller's own connection" in doc
    assert "writable_schema" in doc and "overridden SQL functions" in doc and "between integrity checks" in doc
    assert "full-file read on every open" in doc and "quick=True" in doc
    assert "cannot check UTF-8" in doc


# ---- the path and connection arguments -----------------------------------------------------------

class _BytesPath:
    def __fspath__(self):
        return b"/tmp/never-created.db"


@pytest.mark.parametrize("bad", [None, 5, 1.5, b"x.db", bytearray(b"x"), ["x.db"], {}, "", "a\x00b.db",
                                 "a\ud800b.db", _BytesPath(), object(), True])
def test_connect_refuses_a_bad_path_and_creates_no_file(tmp_path, monkeypatch, bad):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(StoreError):
        connect(bad)
    assert list(tmp_path.iterdir()) == []


def test_connect_accepts_str_and_pathlike_paths(tmp_path):
    connect(str(tmp_path / "a.db")).close()
    connect(tmp_path / "b.db").close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.db", "b.db"]


@pytest.mark.parametrize("bad", [None, "conn", 5, object(), [], b"x", sqlite3])
def test_functions_refuse_anything_that_is_not_a_sqlite_connection(bad):
    with pytest.raises(StoreError, match="connection"):
        register_source(bad, source_id="example_x", terms_url="u", licence_tag="x", personal_use_ok=True,
                        allow_external_llm=False, ua_policy="GENERIC_CLIENT", terms_read_at_utc=T0)
    with pytest.raises(StoreError, match="connection"):
        add_fact(bad, domain="K2_NEWS", entity_key="A", source_id="example_x", raw_sha256=sha("a"),
                 payload_json="{}", effective_from_utc=T0, fetched_at_utc=T1)
    with pytest.raises(StoreError, match="connection"):
        facts_as_of(bad, T3)


def test_functions_work_on_a_plain_connection_without_a_row_factory(tmp_path):
    path = tmp_path / "plain3.db"
    connect(path).close()
    plain = sqlite3.connect(path)                       # default row factory (tuples), default isolation level
    src(plain)
    first = fact(plain)
    again = fact(plain)
    assert again == first
    rows = facts_as_of(plain, T3)
    assert [r["fact_version_id"] for r in rows] == [first] and rows[0]["entity_key"] == "ABC"
    assert not plain.in_transaction
    plain.close()


# ---- the docstring states what connect checks and what it cannot ---------------------------------

def test_the_module_docstring_states_its_limits():
    doc = " ".join((store.__doc__ or "").split())
    assert "only the id range, the schema identity and the version" in doc
    assert "other than out-of-range ids" in doc and "are not detected" in doc
    assert "multiset" in doc and "duplicate name" in doc and "whatever its type" in doc
    assert "text-only" in doc and "built at run time" in doc
    assert "disables triggers" in doc and "re-creates them verbatim" in doc and "outside what the store can detect" in doc
    assert "which catches rows written with the checks switched off" not in doc


# ---- one read path -------------------------------------------------------------------------------

# Public callables of the package, by exact (module path, name). Adding a public function or class fails
# this test on purpose: the author must then show it cannot return fact rows, and add it here.
_ALLOWED_PUBLIC = {("store.py", n) for n in ("connect", "register_source", "add_fact", "facts_as_of", "StoreError")}
# Every place that may name the facts table, as (module path, scope, kind). `_SCHEMA` is the SQL text (its
# own two lookups are checked separately below); the one insert is in add_fact's inner function.
_MAY_MENTION_FACTS = {
    ("store.py", "facts_as_of", "other"), ("store.py", "_latest_fact_for_key", "other"),
    ("store.py", "_SCHEMA", "other"), ("store.py", "insert", "insert"),
    ("store.py", "_verify_schema", "other"),     # only min/max of the id, checked below
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


def test_the_schema_text_names_the_facts_table_for_reading_only_in_the_id_guard():
    matches = [store._SCHEMA[m.start():m.end() + 40] for m in _READS_FACTS.finditer(store._SCHEMA)]
    assert len(matches) == 1, matches
    assert matches[0].startswith("FROM knowledge_facts)")       # max(fact_version_id), for the next-id rule


def test_only_facts_as_of_returns_fact_rows():
    """Known limit: the scan finds the facts table by its name written out as one token (in any quoting).
    A name built at run time, such as "knowledge_" + "facts", or SQL that reaches the table through another
    object, would not be seen. The exact public-name test and the guards in the database are what back it up."""
    # (1) in every file of the package, in any spelling of the table name, the only code that names the facts
    # table is facts_as_of, one private helper that returns only an id and a hash, the SQL text, and the insert
    assert _mentions() == _MAY_MENTION_FACTS
    # (1b) the open-time check reads nothing but the lowest and highest id
    verify = inspect.getsource(store._verify_schema)
    assert re.findall(r"(?i)select[^\n]*knowledge_facts", verify) == [
        'SELECT min(fact_version_id), max(fact_version_id) FROM knowledge_facts']
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
