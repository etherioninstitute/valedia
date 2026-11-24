"""Tests for the separate Finnhub store (K-7). A row whose source is Finnhub lives only in the Finnhub store file, and
nothing that feeds an outside model can open that file or receive its rows.

No network: nothing here fetches, and a socket guard fails any real connection. Every fixture is a made-up SHAPE of the
project's own records (a fact dict as `add_fact` takes it, a source-registry row) on a tmp_path store. No real Finnhub
payload text appears anywhere. Every rule is tested both ways."""
import ast
import hashlib
import inspect
import json
import re
import socket
import sqlite3
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

import valedia_trader
from valedia_trader.knowledge import k3_calendar as k3
from valedia_trader.knowledge import store
from valedia_trader.knowledge.store import StoreError, add_fact, audit_roles, connect, facts_as_of, register_source, role_of

T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"
T9 = "2026-12-31T00:00:00Z"
FIN_ID = 0x46494E48
H = "finnhub_market_holiday"
S = "finnhub_market_status"
X = "xnys_exchange_calendars"
FR = "federal_register_presdocs"
NOW = datetime(2026, 11, 23, 18, 0, 0, tzinfo=timezone.utc)
D = "2026-11-24"                                   # a Tuesday, an open day
AS_OF = "2026-11-24T14:00:00Z"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def gen(tmp_path):
    c = connect(tmp_path / "general.db")
    yield c
    c.close()


@pytest.fixture
def fin(tmp_path):
    c = connect(tmp_path / "finnhub.db", role="finnhub")
    yield c
    c.close()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def src(conn, source_id, **over):
    args = dict(source_id=source_id, terms_url="https://example.invalid/terms", licence_tag="test-tag",
                personal_use_ok=True, allow_external_llm=False, ua_policy="GENERIC_CLIENT", terms_read_at_utc=T0)
    args.update(over)
    register_source(conn, **args)


def fact(conn, source_id, **over):
    args = dict(domain="K3_CAL", entity_key="XNYS:CLOSURE:2026-11-24", source_id=source_id, raw_sha256=sha("a"),
                payload_json='{"closed": true}', effective_from_utc=T0, fetched_at_utc=T1)
    args.update(over)
    return add_fact(conn, **args)


def raw_source(conn, source_id, external=0):
    """A hostile writer: straight SQL, past every guard in Python."""
    conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?)",
                 (source_id, "https://example.invalid/t", "x", 1, external, "GENERIC_CLIENT", 0,
                  "2026-01-01T00:00:00.000000Z", None))


def raw_fact(conn, source_id, sha_text="raw", entity_key="XNYS:CLOSURE:2026-11-24",
             when="2026-01-02T00:00:00.000000Z", payload="{}"):
    conn.execute(
        "INSERT INTO knowledge_facts (domain, entity_key, effective_from_utc, effective_to_utc, published_at_utc,"
        " fetched_at_utc, known_at_utc, known_at_basis, source_id, raw_sha256, payload_json)"
        " VALUES ('K3_CAL',?,'2026-01-01T00:00:00.000000Z',NULL,NULL,?,?,'FETCHED',?,?,?)",
        (entity_key, when, when, source_id, sha(sha_text), payload))


def app_id(path):
    raw = sqlite3.connect(path)
    try:
        return raw.execute("PRAGMA application_id").fetchone()[0]
    finally:
        raw.close()


# ---- 1. the role decides which sources a store accepts

def test_a_general_store_refuses_a_finnhub_source_and_a_finnhub_store_takes_it(gen, fin):
    with pytest.raises(StoreError, match="(?i)finnhub"):
        src(gen, H)
    assert gen.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0
    src(fin, H)                                                         # the opposite: the Finnhub store takes it
    assert [r[0] for r in fin.execute("SELECT source_id FROM source_registry")] == [H]


def test_a_finnhub_store_refuses_a_source_that_is_not_finnhub_and_a_general_store_takes_it(gen, fin):
    with pytest.raises(StoreError, match="(?i)finnhub"):
        src(fin, X)
    assert fin.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0
    src(gen, X)
    assert [r[0] for r in gen.execute("SELECT source_id FROM source_registry")] == [X]


@pytest.mark.parametrize("name", ["FINNHUB_X", "My-FinnHub-feed", "xFiNnHuBx", "ﬁnnhub_ligature"])
def test_the_name_test_ignores_case_and_ligatures(gen, fin, name):
    with pytest.raises(StoreError):
        src(gen, name)
    src(fin, name)


# fullwidth letters, a zero-width space, a space inside, the Turkish dotted capital I, a soft hyphen
_LOOKALIKES = ["\uff46\uff49\uff4e\uff4e\uff48\uff55\uff42_h", "finn\u200bhub", "finn hub", "F\u0130NNHUB", "fin\u00adnhub"]


@pytest.mark.parametrize("name", _LOOKALIKES)
def test_the_name_test_sees_through_look_alike_spellings(gen, fin, name):
    with pytest.raises(StoreError):
        src(gen, name)
    assert gen.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0
    src(fin, name)                                                      # the Finnhub store takes it as a Finnhub source
    assert [r[0] for r in fin.execute("SELECT source_id FROM source_registry")] == [name]


@pytest.mark.parametrize("name", ["finn_hub", "financial_hub", "fin-nhu", "xnys_exchange_calendars"])
def test_the_name_test_does_not_swallow_ordinary_names(gen, fin, name):
    src(gen, name)                                                      # not Finnhub: the general store takes it
    with pytest.raises(StoreError):
        src(fin, name)


def test_a_look_alike_row_never_reaches_an_outside_model_read(gen):
    raw_source(gen, "finn\u200bhub_h", external=1)                       # past every guard: the CHECK is ASCII-only
    raw_fact(gen, "finn\u200bhub_h")
    raw_source(gen, "example_public", external=1)
    raw_fact(gen, "example_public", entity_key="XNYS:CLOSURE:2026-11-25")
    got = facts_as_of(gen, "2027-01-01T00:00:00Z", for_outside_llm=True)
    assert [r["source_id"] for r in got] == ["example_public"]


def test_a_finnhub_store_never_allows_an_outside_model(fin):
    with pytest.raises(StoreError, match="(?i)outside model"):
        src(fin, H, allow_external_llm=True)
    assert fin.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0
    src(fin, H, allow_external_llm=False)
    assert fin.execute("SELECT allow_external_llm FROM source_registry").fetchone()[0] == 0


def test_add_fact_checks_the_role_even_when_a_hostile_row_got_the_source_in(gen, fin):
    raw_source(gen, H)                                    # a Finnhub source smuggled into the general file
    with pytest.raises(StoreError, match="(?i)finnhub"):
        fact(gen, H)
    assert gen.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0] == 0
    raw_source(fin, X, external=1)                        # a non-Finnhub source smuggled into the Finnhub file
    with pytest.raises(StoreError, match="(?i)finnhub"):
        fact(fin, X)
    assert fin.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0] == 0
    src(fin, H)
    assert isinstance(fact(fin, H), int)                  # the opposite: a Finnhub fact in the Finnhub store
    src(gen, "example_public")
    assert isinstance(fact(gen, "example_public"), int)   # and an ordinary fact in the general store


def test_the_role_comes_from_the_file_not_from_the_caller(gen, fin):
    assert role_of(gen) == "general" and role_of(fin) == "finnhub"
    with pytest.raises(StoreError):
        role_of(object())


# ---- 2. the role is on the file

def test_a_finnhub_file_carries_its_role_and_a_general_file_stays_zero(tmp_path):
    connect(tmp_path / "g.db").close()
    connect(tmp_path / "f.db", role="finnhub").close()
    assert app_id(tmp_path / "g.db") == 0 and app_id(tmp_path / "f.db") == FIN_ID


def test_a_file_opens_only_in_its_own_role(tmp_path):
    connect(tmp_path / "g.db").close()
    connect(tmp_path / "f.db", role="finnhub").close()
    connect(tmp_path / "g.db", role="general").close()                  # the right role opens
    connect(tmp_path / "f.db", role="finnhub").close()
    with pytest.raises(StoreError, match="(?i)finnhub"):
        connect(tmp_path / "f.db", role="general")
    with pytest.raises(StoreError, match="(?i)general"):
        connect(tmp_path / "g.db", role="finnhub")
    with pytest.raises(StoreError, match="(?i)finnhub"):
        connect(tmp_path / "f.db")                                      # the default role is general


def test_a_legacy_file_with_application_id_zero_opens_as_general_and_is_not_a_finnhub_file(tmp_path):
    path = tmp_path / "legacy.db"
    c = connect(path)
    src(c, "example_public", allow_external_llm=True)
    c.close()
    assert app_id(path) == 0
    again = connect(path)
    assert role_of(again) == "general"
    again.close()
    with pytest.raises(StoreError):
        connect(path, role="finnhub")


def test_the_default_role_is_general_so_existing_callers_keep_working():
    assert inspect.signature(connect).parameters["role"].default == "general"
    assert store.ROLES == ("general", "finnhub")


@pytest.mark.parametrize("bad", ["", "Finnhub", "other", None, 0, b"finnhub"])
def test_an_unknown_role_is_refused(tmp_path, bad):
    with pytest.raises(StoreError, match="role"):
        connect(tmp_path / "x.db", role=bad)
    assert not (tmp_path / "x.db").exists()


def test_an_unknown_application_id_is_refused(tmp_path):
    path = tmp_path / "odd.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA application_id = 12345")
    raw.commit()
    raw.close()
    with pytest.raises(StoreError, match="(?i)application_id"):
        connect(path)
    with pytest.raises(StoreError, match="(?i)application_id"):
        connect(path, role="finnhub")


def test_the_two_roles_share_one_schema_and_one_version(tmp_path):
    connect(tmp_path / "g.db").close()
    connect(tmp_path / "f.db", role="finnhub").close()
    got = []
    for name in ("g.db", "f.db"):
        raw = sqlite3.connect(tmp_path / name)
        got.append((raw.execute("PRAGMA user_version").fetchone()[0],
                    sorted(tuple(r) for r in raw.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))))
        raw.close()
    assert got[0] == got[1] and got[0][0] == store.SCHEMA_VERSION == 2


def test_a_wrong_role_is_refused_before_anything_is_written(tmp_path):
    path = tmp_path / "f.db"
    connect(path, role="finnhub").close()
    before = path.read_bytes()
    with pytest.raises(StoreError):
        connect(path, role="general")
    assert path.read_bytes() == before


def test_audit_reports_a_hostile_finnhub_row_in_a_general_file_and_is_clean_otherwise(gen):
    assert audit_roles(gen) == []                                       # a clean general file
    src(gen, "example_public")
    fact(gen, "example_public")
    assert audit_roles(gen) == []
    raw_source(gen, H)
    raw_fact(gen, H)
    got = audit_roles(gen)
    assert f"source:{H}" in got and any(g.startswith("fact:") for g in got)
    assert all(isinstance(g, str) for g in got)
    assert "source:example_public" not in got and len(got) == 2


def test_audit_reports_a_stranger_in_a_finnhub_file_and_is_clean_otherwise(fin):
    assert audit_roles(fin) == []
    src(fin, H)
    fact(fin, H)
    assert audit_roles(fin) == []
    raw_source(fin, X, external=1)
    raw_fact(fin, X)
    got = audit_roles(fin)
    assert f"source:{X}" in got and any(g.startswith("fact:") for g in got)
    assert f"source:{H}" not in got and len(got) == 2


def test_audit_reports_a_finnhub_source_that_allows_an_outside_model_even_when_the_name_is_right(fin):
    # The CHECK in the file folds ASCII only, so a ligature name with the flag set gets past it; the audit must not.
    raw_source(fin, "\ufb01nnhub_lig", external=1)
    assert audit_roles(fin) == ["source:\ufb01nnhub_lig"]
    raw_source(fin, "\ufb01nnhub_ok", external=0)                       # opposite: right name, flag off, clean
    assert audit_roles(fin) == ["source:\ufb01nnhub_lig"]


def test_audit_reports_and_never_rewrites(tmp_path):
    path = tmp_path / "general.db"
    c = connect(path)
    raw_source(c, S)
    raw_fact(c, S)
    c.close()
    before = path.read_bytes()
    c = connect(path)
    assert audit_roles(c)
    assert c.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0] == 1
    c.close()
    assert path.read_bytes() == before


# ---- 3. a Finnhub store never feeds an outside model

def test_facts_as_of_for_an_outside_model_raises_on_a_finnhub_store_and_returns_nothing(fin):
    src(fin, H)
    fact(fin, H)
    with pytest.raises(StoreError, match="(?i)outside model"):
        facts_as_of(fin, T9, for_outside_llm=True)
    with pytest.raises(StoreError, match="(?i)outside model"):
        facts_as_of(fin, T9, for_outside_llm=True, domain="K3_CAL")
    rows = facts_as_of(fin, T9, for_outside_llm=False)                  # the opposite: an inside reader gets the rows
    assert [r["source_id"] for r in rows] == [H]
    assert [r["source_id"] for r in facts_as_of(fin, T9)] == [H]


def test_facts_as_of_for_an_outside_model_still_works_on_a_general_store(gen):
    src(gen, "example_public", allow_external_llm=True)
    src(gen, "example_closed", allow_external_llm=False)
    fact(gen, "example_public")
    fact(gen, "example_closed")
    assert [r["source_id"] for r in facts_as_of(gen, T9, for_outside_llm=True)] == ["example_public"]
    assert {r["source_id"] for r in facts_as_of(gen, T9)} == {"example_public", "example_closed"}


# ---- 4. the quorum across two files

def make_stores(tmp_path):
    g = connect(tmp_path / "g.db")
    f = connect(tmp_path / "f.db", role="finnhub")
    k3.register_all(g, T0, only=[X, FR])
    k3.register_all(f, T0, only=[H, S])
    k3.refresh(g, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-30"})
    return g, f


def finnhub_closed_row(f):
    body = json.dumps({"exchange": "US", "timezone": "America/New_York",
                       "data": [{"eventName": "Made Up Closure", "atDate": D, "tradingHour": ""}]}).encode()
    for fct in k3.parse_finnhub_holiday(body, fetched_at_utc="2026-11-23T20:00:00.000000Z"):
        add_fact(f, **fct)


def fr_mourning_row(g):
    doc = {"document_number": "2026-55555", "title": "National Day of Mourning", "type": "Presidential Document",
           "subtype": "Proclamation", "publication_date": "2026-11-22", "signing_date": "2026-11-21",
           "html_url": "https://example.invalid/2026-55555",
           "abstract": "This proclamation designates November 24, 2026, as a National Day of Mourning for a person."}
    body = json.dumps({"description": "", "count": 1, "total_pages": 1, "results": [doc]}).encode()
    for fct in k3.parse_federal_register(body, fetched_at_utc="2026-11-23T20:00:00.000000Z"):
        add_fact(g, **fct)


def test_without_the_finnhub_store_no_finnhub_vote_is_counted_and_the_result_says_it_was_not_given(tmp_path):
    g, f = make_stores(tmp_path)
    finnhub_closed_row(f)
    v = k3.closure_votes(g, D, AS_OF)
    assert v["finnhub_store_given"] is False
    assert v["authorities_closed"] == [] and v["sources_closed"] == []
    assert v["suspected"] is False and v["ordinary_stop"] is False
    v2 = k3.closure_votes(g, D, AS_OF, finnhub_conn=f)                  # the opposite: asked, and it counts
    assert v2["finnhub_store_given"] is True
    assert v2["authorities_closed"] == ["FINNHUB"] and v2["sources_closed"] == [H] and v2["suspected"] is True
    g.close()
    f.close()


def test_the_general_file_is_never_read_for_finnhub_rows_even_when_a_hostile_one_is_there(tmp_path):
    g, f = make_stores(tmp_path)
    raw_source(g, H)                                                     # a Finnhub source in the general file ...
    raw_fact(g, H, entity_key=f"XNYS:CLOSURE:{D}", when="2026-11-23T20:00:00.000000Z", payload='{"closed": true}')
    assert any(r["source_id"] == H for r in facts_as_of(g, T9))          # (it is really there)
    v = k3.closure_votes(g, D, AS_OF)
    assert v["authorities_closed"] == [] and v["finnhub_store_given"] is False       # ... is not read
    v = k3.closure_votes(g, D, AS_OF, finnhub_conn=f)                    # an empty Finnhub store: still not read
    assert v["authorities_closed"] == [] and v["sources_closed"] == []
    finnhub_closed_row(f)                                                # the opposite: the Finnhub store's own row
    assert k3.closure_votes(g, D, AS_OF, finnhub_conn=f)["authorities_closed"] == ["FINNHUB"]
    g.close()
    f.close()


def test_the_finnhub_group_and_a_federal_register_vote_make_the_two_group_quorum(tmp_path):
    g, f = make_stores(tmp_path)
    finnhub_closed_row(f)
    fr_mourning_row(g)
    alone = k3.closure_votes(g, D, AS_OF)                                # only the general file: one group
    assert alone["authorities_closed"] == ["US_PRESIDENT"] and alone["suspected"] is True
    assert alone["ordinary_stop"] is False
    both = k3.closure_votes(g, D, AS_OF, finnhub_conn=f)
    assert both["authorities_closed"] == ["FINNHUB", "US_PRESIDENT"]
    assert both["sources_closed"] == sorted([H, FR]) and both["ordinary_stop"] is True and both["suspected"] is False
    g.close()
    f.close()


def test_a_group_counts_once_across_both_finnhub_sources(tmp_path):
    g, f = make_stores(tmp_path)
    finnhub_closed_row(f)
    status = {"exchange": "US", "holiday": "Made Up Closure", "isOpen": False, "session": "closed",
              "timezone": "America/New_York", "t": int(datetime(2026, 11, 24, 13, 0, tzinfo=timezone.utc).timestamp())}
    for fct in k3.parse_finnhub_status(json.dumps(status).encode(), fetched_at_utc="2026-11-24T13:30:00.000000Z"):
        add_fact(f, **fct)
    v = k3.closure_votes(g, D, AS_OF, finnhub_conn=f)
    assert v["authorities_closed"] == ["FINNHUB"] and v["sources_closed"] == sorted([H, S])
    assert v["suspected"] is True and v["ordinary_stop"] is False
    g.close()
    f.close()


def test_the_finnhub_file_is_read_only_for_finnhub_rows_even_when_a_stranger_is_in_it(tmp_path):
    g, f = make_stores(tmp_path)
    raw_source(f, FR)                                                    # a stranger in the Finnhub file, by raw SQL
    raw_fact(f, FR, entity_key="FR:2026-99999", when="2026-11-23T20:00:00.000000Z",
             payload=json.dumps({"closure_dates": [D]}))
    assert audit_roles(f) == [f"source:{FR}", "fact:1"]                  # the audit sees it (the file is really dirty)
    assert any(r["source_id"] == FR for r in facts_as_of(f, T9))         # and it is really there to be read
    v = k3.closure_votes(g, D, AS_OF, finnhub_conn=f)                    # but it is not a vote
    assert v["finnhub_store_given"] is True
    assert "US_PRESIDENT" not in v["authorities_closed"] and v["authorities_closed"] == []
    assert v["sources_closed"] == [] and v["suspected"] is False
    fr_mourning_row(g)                                                   # the opposite: the same source in the general file counts
    assert k3.closure_votes(g, D, AS_OF, finnhub_conn=f)["authorities_closed"] == ["US_PRESIDENT"]
    g.close()
    f.close()


def test_closure_votes_refuses_connections_of_the_wrong_role(tmp_path):
    g, f = make_stores(tmp_path)
    with pytest.raises(k3.K3Error, match="(?i)general"):
        k3.closure_votes(f, D, AS_OF)                                    # the Finnhub store as the general one
    with pytest.raises(k3.K3Error, match="(?i)finnhub"):
        k3.closure_votes(g, D, AS_OF, finnhub_conn=g)                    # the general store as the Finnhub one
    assert k3.closure_votes(g, D, AS_OF, finnhub_conn=f)["finnhub_store_given"] is True
    g.close()
    f.close()


def test_closure_votes_takes_the_finnhub_connection_as_a_keyword_that_defaults_to_none():
    p = inspect.signature(k3.closure_votes).parameters
    assert p["finnhub_conn"].kind is inspect.Parameter.KEYWORD_ONLY and p["finnhub_conn"].default is None
    assert "finnhub store" in " ".join((k3.closure_votes.__doc__ or "").lower().split())


# ---- 5. refresh and register take the connection that matches the source

def test_refresh_refuses_the_wrong_store_before_any_request(tmp_path):
    g, f = make_stores(tmp_path)
    calls = []

    def http_get(url, headers):
        calls.append(url)
        raise AssertionError("no request may be made")

    with pytest.raises(k3.K3Error, match="(?i)finnhub store"):
        k3.refresh(g, H, http_get=http_get, now=NOW, credentials=lambda n: "tok_ABCDEF0123456789")
    with pytest.raises(k3.K3Error, match="(?i)general store"):
        k3.refresh(f, FR, http_get=http_get, now=NOW)
    with pytest.raises(k3.K3Error, match="(?i)general store"):
        k3.refresh(f, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-30"})
    assert calls == []
    got = k3.refresh(f, H, http_get=lambda u, h: json.dumps(
        {"exchange": "US", "timezone": "America/New_York",
         "data": [{"eventName": "Made Up Closure", "atDate": D, "tradingHour": ""}]}).encode(),
        now=NOW, credentials=lambda n: "tok_ABCDEF0123456789")           # the opposite: the matching store works
    assert got == {"stored": 1, "unchanged": 0, "error": 0}
    assert [r["source_id"] for r in facts_as_of(f, T9)] == [H]
    g.close()
    f.close()


def test_register_all_sends_each_source_to_its_own_store_and_refuses_a_mixed_list_whole(gen, fin):
    with pytest.raises(k3.K3Error, match="(?i)finnhub store"):
        k3.register_all(gen, T0, only=[X, H])
    assert gen.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0       # not half-registered
    with pytest.raises(k3.K3Error, match="(?i)general store"):
        k3.register_all(fin, T0, only=[H, X])
    assert fin.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0
    k3.register_all(gen, T0, only=[X, FR])
    k3.register_all(fin, T0, only=[H, S])
    assert {r[0] for r in gen.execute("SELECT source_id FROM source_registry")} == {X, FR}
    assert {r[0] for r in fin.execute("SELECT source_id FROM source_registry")} == {H, S}


def test_the_command_line_needs_the_finnhub_store_for_a_finnhub_source_and_opens_it_in_its_role(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(k3, "_urllib_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request")))
    g, f = tmp_path / "g.db", tmp_path / "f.db"
    assert k3._main(["register", "--store", str(g), "--only", H, "--terms-read-at", T0]) == 2
    assert "finnhub-store" in capsys.readouterr().err
    assert not f.exists()
    assert k3._main(["refresh", "--store", str(g), "--source", H]) == 2
    assert "finnhub-store" in capsys.readouterr().err
    assert not f.exists()
    assert k3._main(["register", "--store", str(g), "--finnhub-store", str(f), "--only", f"{X},{H},{S}",
                     "--terms-read-at", T0]) == 0
    capsys.readouterr()
    assert app_id(g) == 0 and app_id(f) == FIN_ID
    gc, fc = connect(g), connect(f, role="finnhub")
    assert {r[0] for r in gc.execute("SELECT source_id FROM source_registry")} == {X}
    assert {r[0] for r in fc.execute("SELECT source_id FROM source_registry")} == {H, S}
    gc.close()
    fc.close()


def test_the_command_line_register_refuses_a_mixed_list_before_writing_either_half(tmp_path, capsys):
    path = tmp_path / "both.db"                                         # one file named as both stores: the second open must fail
    assert k3._main(["register", "--store", str(path), "--finnhub-store", str(path), "--only", f"{X},{H}",
                     "--terms-read-at", T0]) == 2
    assert "finnhub" in capsys.readouterr().err.lower()
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0     # the general half was not written
    finally:
        raw.close()
    path2 = tmp_path / "ok.db"                                          # opposite: two files, both halves written
    other = tmp_path / "ok_f.db"
    assert k3._main(["register", "--store", str(path2), "--finnhub-store", str(other), "--only", f"{X},{H}",
                     "--terms-read-at", T0]) == 0


def test_the_command_line_votes_say_whether_the_finnhub_store_was_given(tmp_path, capsys):
    g, f = tmp_path / "g.db", tmp_path / "f.db"
    k3._main(["register", "--store", str(g), "--finnhub-store", str(f), "--only", f"{X},{H}", "--terms-read-at", T0])
    capsys.readouterr()
    k3._main(["votes", "--store", str(g), "--date", D, "--as-of", AS_OF])
    assert json.loads(capsys.readouterr().out)["finnhub_store_given"] is False
    k3._main(["votes", "--store", str(g), "--finnhub-store", str(f), "--date", D, "--as-of", AS_OF])
    assert json.loads(capsys.readouterr().out)["finnhub_store_given"] is True


def _tree(root):
    return sorted(p.name for p in root.iterdir())


def test_votes_refuses_a_finnhub_store_path_that_does_not_exist_and_creates_nothing(tmp_path, capsys):
    g, f = tmp_path / "g.db", tmp_path / "typo_f.db"
    assert k3._main(["votes", "--store", str(g), "--finnhub-store", str(f), "--date", D, "--as-of", AS_OF]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err.startswith("error:") and "typo_f.db" in out.err and "Traceback" not in out.err
    assert not f.exists() and not g.exists() and _tree(tmp_path) == []   # neither file, as a side effect of the refusal
    long_path = tmp_path / ("x" * 400)                                   # the path is named, cut to a sane length
    assert k3._main(["votes", "--store", str(g), "--finnhub-store", str(long_path), "--date", D]) == 2
    err = capsys.readouterr().err
    assert "xxxx" in err and len(err) <= 301 and not g.exists() and _tree(tmp_path) == []
    # the opposite: the same call with an existing Finnhub-role file works, and so does a general store that is new
    connect(f, role="finnhub").close()
    assert k3._main(["votes", "--store", str(g), "--finnhub-store", str(f), "--date", D, "--as-of", AS_OF]) in (0, 1)
    assert json.loads(capsys.readouterr().out)["finnhub_store_given"] is True
    assert g.exists()


def test_votes_with_an_existing_finnhub_file_works_as_before_and_without_the_option_still_creates_the_general_store(
        tmp_path, capsys):
    g, f = tmp_path / "g.db", tmp_path / "f.db"
    assert k3._main(["register", "--store", str(g), "--finnhub-store", str(f), "--only", f"{X},{H}",
                     "--terms-read-at", T0]) == 0
    capsys.readouterr()
    assert k3._main(["votes", "--store", str(g), "--finnhub-store", str(f), "--date", D, "--as-of", AS_OF]) in (0, 1)
    got = json.loads(capsys.readouterr().out)
    assert got["finnhub_store_given"] is True and got["authorities_closed"] == []
    fresh = tmp_path / "fresh.db"                                        # `--store` itself is unchanged: a new path is made
    assert k3._main(["votes", "--store", str(fresh), "--date", D, "--as-of", AS_OF]) in (0, 1)
    assert json.loads(capsys.readouterr().out)["finnhub_store_given"] is False and fresh.exists()


def test_votes_still_refuses_an_existing_general_file_given_as_the_finnhub_store(tmp_path, capsys):
    g, other = tmp_path / "g.db", tmp_path / "other_general.db"
    connect(other).close()
    before = other.read_bytes()
    assert k3._main(["votes", "--store", str(g), "--finnhub-store", str(other), "--date", D, "--as-of", AS_OF]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "general" in err and "Traceback" not in err
    assert other.read_bytes() == before and app_id(other) == 0 and not g.exists()      # and no --store file is made
    connect(tmp_path / "ok_f.db", role="finnhub").close()                # the opposite: the right role passes
    assert k3._main(["votes", "--store", str(g), "--finnhub-store", str(tmp_path / "ok_f.db"), "--date", D,
                     "--as-of", AS_OF]) in (0, 1)


@pytest.mark.parametrize("make_bad", [lambda p: p / "a_dir", lambda p: ""], ids=["directory", "empty-string"])
def test_votes_refuses_a_finnhub_store_that_is_a_directory_or_empty_in_words(tmp_path, capsys, make_bad):
    bad = make_bad(tmp_path)
    if bad:
        bad.mkdir()
    before = _tree(tmp_path)
    g = tmp_path / "g.db"
    assert k3._main(["votes", "--store", str(g), "--finnhub-store", str(bad), "--date", D, "--as-of", AS_OF]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "finnhub" in err.lower() and "Traceback" not in err and len(err) <= 301
    assert not g.exists() and _tree(tmp_path) == before                  # nothing created


def test_register_and_refresh_may_still_create_the_finnhub_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(k3, "_urllib_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request")))
    g, f = tmp_path / "g.db", tmp_path / "new_f.db"
    assert k3._main(["register", "--store", str(g), "--finnhub-store", str(f), "--only", H, "--terms-read-at", T0]) == 0
    assert f.exists() and app_id(f) == FIN_ID                            # register is how the Finnhub file is first made


def test_there_is_no_default_path_for_the_finnhub_store_in_the_code():
    for module in (store, k3):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and " " not in node.value:
                assert not re.search(r"\.(sqlite3?|db)$", node.value), (module.__name__, node.value)
    assert inspect.signature(k3.refresh).parameters["conn"].default is inspect.Parameter.empty
    assert inspect.signature(connect).parameters["path"].default is inspect.Parameter.empty


# ---- 6. the repo scan: who may touch the stores

_PKG = Path(valedia_trader.__file__).parent
GUARDED = {"knowledge/store.py", "knowledge/k3_calendar.py", "data_and_fees/company_facts.py"}
ALLOWLIST = {"knowledge/store.py", "knowledge/k3_calendar.py", "knowledge/k4_venue.py",
             "data_and_fees/company_facts.py"}
_GUARDED_MODULES = ("valedia_trader.knowledge.store", "valedia_trader.knowledge.k3_calendar",
                    "valedia_trader.knowledge.k4_venue", "valedia_trader.data_and_fees.company_facts")
_GUARDED_TAILS = ("knowledge.store", "k3_calendar", "k4_venue", "company_facts")
_ROLE_TEXT = re.compile(r"""role\s*=\s*["']finnhub["']""")


def _docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def scan(root: Path) -> dict[str, list[str]]:
    """Every module under `root` that is one of the guarded modules, imports one (absolute, relative, `from package
    import module`, or by name through importlib), names company_facts.sqlite, or passes role="finnhub". Docstrings are
    not code and are skipped. Returns {path relative to root: [why, ...]}."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text()
        tree = ast.parse(text)
        why: list[str] = []
        if rel in GUARDED:
            why.append("is a guarded module")
        skip = _docstrings(tree)
        package = ["valedia_trader"] + rel.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                why += [f"imports {a.name}" for a in node.names if a.name.startswith(_GUARDED_MODULES)]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    keep = package[:len(package) - node.level + 1]
                    base = ".".join(keep + ([node.module] if node.module else []))
                names = [f"{base}.{a.name}" for a in node.names] + [base]
                why += [f"imports {n}" for n in names if n.startswith(_GUARDED_MODULES)]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if "company_facts.sqlite" in node.value:
                    why.append("names company_facts.sqlite")
                if re.fullmatch(r"[\w.]+", node.value) and any(t in node.value for t in _GUARDED_TAILS):
                    why.append(f"names the module {node.value[:40]}")
            elif isinstance(node, ast.keyword) and node.arg == "role" and isinstance(node.value, ast.Constant) \
                    and node.value.value == "finnhub":
                why.append('passes role="finnhub"')
        if _ROLE_TEXT.search(text):
            why.append('writes role="finnhub"')
        if why:
            found[rel] = sorted(set(why))
    return found


def test_only_the_allowlisted_modules_reach_the_stores():
    found = scan(_PKG)
    assert set(found) == ALLOWLIST, {k: v for k, v in found.items() if k not in ALLOWLIST}


def test_the_modules_that_feed_models_import_none_of_them():
    found = scan(_PKG)
    for rel in found:
        assert not rel.startswith(("agents/", "research_loop/")) and rel != "core/decision_model.py", (rel, found[rel])
    assert (_PKG / "agents").is_dir() and (_PKG / "research_loop").is_dir() and (_PKG / "core" / "decision_model.py").is_file()


def test_the_venue_feed_stays_general_only():
    text = (_PKG / "knowledge" / "k4_venue.py").read_text()
    tree = ast.parse(text)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"]
    assert calls, "k4_venue is expected to open a store"
    for call in calls:
        assert not any(k.arg == "role" for k in call.keywords)
    assert not _ROLE_TEXT.search(text)
    assert not any(isinstance(n, ast.Constant) and n.value == "finnhub" for n in ast.walk(tree))


def _fake_tree(tmp_path, files: dict[str, str]) -> Path:
    root = tmp_path / "valedia_trader"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
    return root


_FLAGGED_MODULES = {
    "from-import": "from valedia_trader.knowledge import k3_calendar\n",
    "import-as": "import valedia_trader.knowledge.k3_calendar as k\n",
    "store-import": "from valedia_trader.knowledge import store\n",
    "venue-import": "from valedia_trader.knowledge import k4_venue\n",
    "company-facts": "from valedia_trader.data_and_fees import company_facts\n",
    "relative": "from ..knowledge import k3_calendar\n",
    "relative-deep": "from ..knowledge.store import connect\n",
    "importlib": "import importlib\nm = importlib.import_module('valedia_trader.knowledge.k3_calendar')\n",
    "sqlite-name": "PATH = 'memory/company_facts.sqlite'\n",
    "role-keyword": "def f(c):\n    return c(role='finnhub')\n",
    "role-text": "X = dict(role = \"finnhub\")\n",
}


@pytest.mark.parametrize("form", sorted(_FLAGGED_MODULES))
def test_the_scan_flags_a_new_reader_in_a_model_facing_module(tmp_path, form):
    root = _fake_tree(tmp_path, {"agents/sneaky.py": _FLAGGED_MODULES[form], "agents/clean.py": "x = 1\n"})
    found = scan(root)
    assert set(found) == {"agents/sneaky.py"}, (form, found)


def test_the_scan_leaves_plain_modules_and_docstrings_alone(tmp_path):
    root = _fake_tree(tmp_path, {
        "agents/a.py": '"""Mentions k3_calendar and company_facts.sqlite in prose only."""\nx = 1\n',
        "research_loop/b.py": "import json\nfrom valedia_trader.core import alerts\n",
        "core/decision_model.py": "def f():\n    '''uses role finnhub never'''\n    return 1\n",
    })
    assert scan(root) == {}


def test_the_scan_counts_a_guarded_module_by_its_path(tmp_path):
    root = _fake_tree(tmp_path, {"knowledge/store.py": "x = 1\n", "agents/a.py": "x = 1\n"})
    assert set(scan(root)) == {"knowledge/store.py"}
