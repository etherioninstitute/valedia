"""Tests for the K-3 calendar and closure feed. No network: every fetch goes through a fake `http_get`, and a
socket guard makes any real connection fail the test. Every guard is tested both ways."""
import hashlib
import inspect
import json
import re
import socket
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape as xml_escape

import pytest

from valedia_trader.knowledge import k3_calendar as k3
from valedia_trader.knowledge.store import StoreError, add_fact, connect, facts_as_of

T0 = "2026-01-01T00:00:00Z"
NOW = datetime(2026, 11, 23, 18, 0, 0, tzinfo=timezone.utc)
NOW_S = "2026-11-23T18:00:00.000000Z"
TOKEN = "tok_ABCDEF0123456789"
H = "finnhub_market_holiday"
S = "finnhub_market_status"
FR = "federal_register_presdocs"
WH = "whitehouse_presidential_actions"
X = "xnys_exchange_calendars"
G_FIN, G_PRES, G_CAL = "FINNHUB", "US_PRESIDENT", "XNYS_CALENDAR"      # the authority groups


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


_FIN = {}      # general connection -> its paired Finnhub-store connection (K-7: Finnhub rows live in their own file)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "k.db")
    f = connect(tmp_path / "finnhub.db", role="finnhub")
    _FIN[id(c)] = f
    yield c
    _FIN.pop(id(c), None)
    f.close()
    c.close()


def fin(conn):
    """The Finnhub-store connection that goes with this test's general connection."""
    return _FIN[id(conn)]


def side(conn, source_id):
    """The connection a source's rows belong in: the Finnhub store for the two Finnhub sources, else the general one."""
    return fin(conn) if "finnhub" in source_id else conn


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def raw(obj):
    return json.dumps(obj).encode()


def put(conn, facts):
    """Store parsed fact dicts the way `refresh` does, straight through add_fact."""
    for f in facts:
        add_fact(side(conn, f["source_id"]), **f)


def unix(y, m, d, hh=0, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


# ---- fixtures: the shapes the ticket describes

def holiday_body(*items):
    return {"exchange": "US", "timezone": "America/New_York", "data": [
        {"eventName": n, "atDate": d, "tradingHour": th} for n, d, th in items]}


HOLIDAYS = holiday_body(("Thanksgiving Day", "2026-11-26", ""),
                        ("Day After Thanksgiving", "2026-11-27", "09:30-13:00"),
                        ("Christmas Day", "2026-12-25", ""))


def status_body(**over):
    body = {"exchange": "US", "holiday": None, "isOpen": False, "session": "pre-market",
            "timezone": "America/New_York", "t": unix(2026, 11, 24, 13, 0)}
    body.update(over)
    return body


def fr_doc(num="2026-99001", title="Some Proclamation", subtype="Proclamation", pub="2026-11-20", abstract=None,
           signing="2026-11-19"):
    return {"document_number": num, "title": title, "type": "Presidential Document", "subtype": subtype,
            "publication_date": pub, "signing_date": signing, "html_url": f"https://example.invalid/{num}",
            "abstract": abstract}


def fr_body(*docs):
    """The real shape: extra top-level keys (description, total_pages) come with the results."""
    return {"description": "", "count": len(docs), "total_pages": 1, "results": list(docs)}


CLOSING_TITLE = "Providing for the Closing of Executive Departments and Agencies of the Federal Government on {d}"


def closing_doc(num, day_text, pub):
    return fr_doc(num=num, title=CLOSING_TITLE.format(d=day_text), subtype="Executive Order", pub=pub)


MOURNING_ABSTRACT = "This proclamation designates {d}, as a National Day of Mourning for the late President."


def mourning_doc(num, day_text, pub):
    """A document that votes: a proclamation making the date a National Day of Mourning."""
    return fr_doc(num=num, title="National Day of Mourning", subtype="Proclamation", pub=pub,
                  abstract=MOURNING_ABSTRACT.format(d=day_text))


def register(conn, *ids, at=T0):
    ids = ids or [X, H, S, FR, WH]
    general = [i for i in ids if "finnhub" not in i]
    finnhub = [i for i in ids if "finnhub" in i]
    if general:
        k3.register_all(conn, at, only=general)
    if finnhub:
        k3.register_all(fin(conn), at, only=finnhub)


# ---- SOURCES and registration

def test_sources_have_the_listed_fields_and_roles():
    assert set(k3.SOURCES) == {X, H, S, FR, WH}
    for sid, e in k3.SOURCES.items():
        assert e["source_id"] == sid
        assert e["ua_policy"] == "GENERIC_CLIENT" and e["cost_usd_monthly"] == 0
        assert e["role"] in ("SCHEDULE", "AUTHORITY", "SIGNAL")
        assert e["terms_url"].startswith("https://") and e["licence_tag"]
        assert e["allow_external_llm"] in (0, 1)
    assert k3.SOURCES[X]["role"] == "SCHEDULE" and k3.SOURCES[X]["licence_tag"] == "Apache-2.0"
    assert k3.SOURCES[H]["role"] == k3.SOURCES[S]["role"] == k3.SOURCES[FR]["role"] == "AUTHORITY"
    assert k3.SOURCES[FR]["licence_tag"] == "US-PUBLIC-DOMAIN" and k3.SOURCES[FR]["time_zone"] == "America/New_York"
    for sid in (H, S):
        assert k3.SOURCES[sid]["allow_external_llm"] == 0
        assert k3.SOURCES[sid]["licence_tag"] == "FINNHUB-PERSONAL-TOS"
        assert k3.SOURCES[sid]["notes"] == ("terms: personal use only; no sharing with third parties; all data must be "
                                            "deleted if the subscription ends; 30 calls/s cap")
    assert k3.SOURCES[FR]["terms_url"] == \
        "https://www.federalregister.gov/reader-aids/using-federalregister-gov/developer-resources"
    assert k3.SOURCES[FR]["allow_external_llm"] == 1 and k3.SOURCES[X]["allow_external_llm"] == 1


def test_every_source_names_its_authority_group_and_signals_have_none():
    assert k3.SOURCES[H]["authority_group"] == k3.SOURCES[S]["authority_group"] == G_FIN
    assert k3.SOURCES[FR]["authority_group"] == k3.SOURCES[WH]["authority_group"] == G_PRES
    assert k3.SOURCES[X]["authority_group"] == G_CAL
    for sid, e in k3.SOURCES.items():
        if e["role"] == "SIGNAL":
            assert "authority_group" not in e
        else:
            assert isinstance(e["authority_group"], str) and e["authority_group"]
    assert k3.SOURCES[H]["authority_group"] != k3.SOURCES[FR]["authority_group"]


def test_the_group_is_code_only_and_registration_is_unchanged(conn):
    register(conn)
    for sid in (X, H, S, FR, WH):
        cur = side(conn, sid).execute("SELECT * FROM source_registry WHERE source_id = ?", (sid,))
        row = cur.fetchone()
        assert row is not None and "authority_group" not in [d[0] for d in cur.description]
        assert "authority_group" not in str(tuple(row))


def test_the_nasdaq_halts_feed_is_gone_and_the_docstring_says_why():
    assert "nasdaq_trade_halts_rss" not in k3.SOURCES
    doc = k3.__doc__.lower()
    assert "nasdaq" in doc and "excluded" in doc and "terms" in doc
    assert not hasattr(k3, "parse_halts_rss")
    assert "HALT:" not in inspect.getsource(k3)


def test_register_all_registers_only_the_named_sources(conn):
    register(conn, H, X)                       # K-7: the Finnhub source goes to the Finnhub store, the other to the general one
    assert {r[0] for r in conn.execute("SELECT source_id FROM source_registry")} == {X}
    assert {r[0] for r in fin(conn).execute("SELECT source_id FROM source_registry")} == {H}
    row = fin(conn).execute("SELECT * FROM source_registry WHERE source_id = ?", (H,)).fetchone()
    assert row["allow_external_llm"] == 0 and row["ua_policy"] == "GENERIC_CLIENT" and row["personal_use_ok"] == 1
    assert "personal use only; no sharing with third parties" in row["notes"] and "30 calls/s cap" in row["notes"]
    assert row["licence_tag"] == "FINNHUB-PERSONAL-TOS"
    assert row["cost_usd_monthly"] == 0 and row["terms_read_at_utc"] == "2026-01-01T00:00:00.000000Z"
    register(conn, H, X)                                          # same values again: nothing happens
    register(conn, FR)
    assert {r[0] for r in conn.execute("SELECT source_id FROM source_registry")} == {X, FR}
    assert {r[0] for r in fin(conn).execute("SELECT source_id FROM source_registry")} == {H}


@pytest.mark.parametrize("only", [[], ["no_such_source"], [X, "no_such_source"], None, "finnhub_market_holiday"])
def test_register_all_refuses_an_unknown_empty_or_malformed_list(conn, only):
    with pytest.raises(k3.K3Error):
        k3.register_all(conn, T0, only=only)
    assert conn.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0     # nothing half-registered


def test_register_all_accepts_a_comma_free_tuple(conn):
    k3.register_all(conn, T0, only=(X,))
    assert conn.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 1


# ---- the date-only rule

@pytest.mark.parametrize("day,zone,want", [
    ("2026-01-15", "America/New_York", "2026-01-16T04:59:59.999999Z"),       # winter: EST, UTC-5
    ("2026-07-15", "America/New_York", "2026-07-16T03:59:59.999999Z"),       # summer: EDT, UTC-4
    ("2026-03-08", "America/New_York", "2026-03-09T03:59:59.999999Z"),       # spring-forward day ends in EDT
    ("2026-11-01", "America/New_York", "2026-11-02T04:59:59.999999Z"),       # fall-back day ends in EST
    ("2026-01-15", "Asia/Tokyo", "2026-01-15T14:59:59.999999Z"),             # a zone east of UTC
    ("2026-01-15", "UTC", "2026-01-15T23:59:59.999999Z"),
])
def test_date_only_known_at_is_the_end_of_the_local_date(day, zone, want):
    assert k3.date_only_known_at(day, zone) == want


def test_date_only_known_at_never_uses_23_59_59_utc_in_new_york_winter():
    got = k3.date_only_known_at("2026-01-15", "America/New_York")
    assert got != "2026-01-15T23:59:59Z" and got > "2026-01-15T23:59:59.999999Z"      # the draft rule leaked ~5 h


def test_date_only_known_at_output_is_the_stores_format():
    got = k3.date_only_known_at("2026-01-15", "America/New_York")
    assert len(got) == 27 and got.endswith("Z")


@pytest.mark.parametrize("zone", [None, "", "   ", "Not/AZone", 5, b"UTC"])
def test_date_only_known_at_refuses_a_missing_or_bad_zone(zone):
    with pytest.raises(k3.K3Error):
        k3.date_only_known_at("2026-01-15", zone)


@pytest.mark.parametrize("day", ["2026-1-5", "2026-02-30", "20260115", "2026-01-15T00:00:00", "", None, 20260115,
                                 "2026-13-01", " 2026-01-15", "2026-01-15\n"])
def test_date_only_known_at_refuses_a_bad_date(day):
    with pytest.raises(k3.K3Error):
        k3.date_only_known_at(day, "America/New_York")


def test_a_proven_date_only_stamp_is_accepted_by_the_store(conn):
    register(conn, FR)
    stamp = k3.date_only_known_at("2026-01-15", "America/New_York")
    rid = add_fact(conn, domain="K3_CAL", entity_key="FR:1", source_id=FR, raw_sha256=sha("x"), payload_json="{}",
                   effective_from_utc=T0, fetched_at_utc="2026-02-01T00:00:00Z", published_at_utc=stamp,
                   published_proven=True)
    row = facts_as_of(conn, "2026-02-01T00:00:00Z")[0]
    assert row["fact_version_id"] == rid and row["known_at_utc"] == stamp and row["known_at_basis"] == "PUBLISHED"


# ---- schedule facts

HOLIDAYS_2026 = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
                 "2026-09-07", "2026-11-26", "2026-12-25"}
EARLY_2026 = {"2026-11-27", "2026-12-24"}
HOLIDAYS_2027 = {"2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
                 "2027-09-06", "2027-11-25", "2027-12-24"}
EARLY_2027 = {"2027-11-26"}


def sched(start, end):
    return {f["entity_key"].rsplit(":", 1)[1]: json.loads(f["payload_json"]) for f in
            k3.schedule_facts(start, end, now=NOW)}


def test_schedule_2026_matches_xnys_every_holiday_and_early_close():
    days = sched("2026-01-01", "2026-12-31")
    assert len(days) == 365
    weekday_closed = {d for d, p in days.items() if not p["open"] and date.fromisoformat(d).weekday() < 5}
    assert weekday_closed == HOLIDAYS_2026
    assert {d for d, p in days.items() if p["open"] and p["close_et"] == "13:00"} == EARLY_2026
    assert all(p["close_et"] == "16:00" for d, p in days.items() if p["open"] and d not in EARLY_2026)
    assert days["2026-11-26"] == {"open": False, "close_et": None}
    assert days["2026-11-27"] == {"open": True, "close_et": "13:00"}
    assert days["2026-11-24"] == {"open": True, "close_et": "16:00"}
    assert all(not days[d]["open"] for d in days if date.fromisoformat(d).weekday() >= 5)


def test_schedule_2027_matches_xnys_every_holiday_and_early_close():
    days = sched("2027-01-01", "2027-12-31")
    weekday_closed = {d for d, p in days.items() if not p["open"] and date.fromisoformat(d).weekday() < 5}
    assert weekday_closed == HOLIDAYS_2027
    assert {d for d, p in days.items() if p["open"] and p["close_et"] == "13:00"} == EARLY_2027


def test_a_special_closure_is_a_closed_session():
    assert sched("2025-01-09", "2025-01-09")["2025-01-09"] == {"open": False, "close_et": None}      # a day of mourning


def test_schedule_fact_shape():
    facts = k3.schedule_facts(date(2026, 11, 26), date(2026, 11, 27), now=NOW)
    assert [f["entity_key"] for f in facts] == ["XNYS:SESSION:2026-11-26", "XNYS:SESSION:2026-11-27"]
    f = facts[1]
    assert f["domain"] == "K3_CAL" and f["source_id"] == X and f["published_proven"] is False
    assert f["payload_json"] == '{"close_et":"13:00","open":true}'                 # sorted keys, no whitespace
    assert f["raw_sha256"] == sha(f["payload_json"])
    assert f["fetched_at_utc"] == NOW_S and f["published_at_utc"] is None
    assert f["effective_from_utc"] == "2026-11-27T05:00:00.000000Z"                # local midnight, UTC-5
    assert f["effective_to_utc"] == "2026-11-28T04:59:59.999999Z"                   # the date-only rule's end


def test_schedule_refuses_a_bad_range():
    for a, b in [("2026-12-31", "2026-01-01"), ("2026-01-01", "2026-13-01"), ("x", "y"), (None, "2026-01-01")]:
        with pytest.raises(k3.K3Error):
            k3.schedule_facts(a, b, now=NOW)
    with pytest.raises(k3.K3Error):
        k3.schedule_facts("2000-01-01", "2026-01-01", now=NOW)                      # more than the allowed span


def test_schedule_facts_store_and_a_second_store_changes_nothing(conn):
    register(conn, X)
    first = k3.refresh(conn, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-30"})
    assert first == {"stored": 8, "unchanged": 0, "error": 0}
    again = k3.refresh(conn, X, http_get=None, now=NOW + timedelta(hours=1),
                       params={"start": "2026-11-23", "end": "2026-11-30"})
    assert again == {"stored": 0, "unchanged": 8, "error": 0}


# ---- Finnhub holiday parser

def parse_h(body, fetched=NOW_S):
    return k3.parse_finnhub_holiday(body if isinstance(body, bytes) else raw(body), fetched_at_utc=fetched)


def test_finnhub_holiday_parser_makes_one_fact_per_date():
    facts = parse_h(HOLIDAYS)
    assert [f["entity_key"] for f in facts] == ["XNYS:CLOSURE:2026-11-26", "XNYS:CLOSURE:2026-11-27",
                                                "XNYS:CLOSURE:2026-12-25"]
    p = [json.loads(f["payload_json"]) for f in facts]
    assert p[0] == {"closed": True, "trading_hours": "", "name": "Thanksgiving Day"}
    assert p[1] == {"closed": False, "trading_hours": "09:30-13:00", "name": "Day After Thanksgiving"}
    for f in facts:
        assert f["domain"] == "K3_CAL" and f["source_id"] == H
        assert f["published_proven"] is False and f["published_at_utc"] is None      # no publication time: FETCHED
        assert f["fetched_at_utc"] == NOW_S
        assert f["raw_sha256"] == sha(f["payload_json"])
        assert f["payload_json"] == json.dumps(json.loads(f["payload_json"]), sort_keys=True, separators=(",", ":"))


BAD_HOLIDAYS = {
    "not json": b"<html>",
    "json but a list": raw([1]),
    "wrong exchange": raw({**HOLIDAYS, "exchange": "NASDAQ"}),
    "wrong timezone": raw({**HOLIDAYS, "timezone": "UTC"}),
    "no data": raw({"exchange": "US", "timezone": "America/New_York"}),
    "data not a list": raw({**HOLIDAYS, "data": {}}),
    "item not an object": raw({**HOLIDAYS, "data": ["x"]}),
    "item missing atDate": raw({**HOLIDAYS, "data": [{"eventName": "a", "tradingHour": ""}]}),
    "item missing tradingHour": raw({**HOLIDAYS, "data": [{"eventName": "a", "atDate": "2026-11-26"}]}),
    "item missing eventName": raw({**HOLIDAYS, "data": [{"atDate": "2026-11-26", "tradingHour": ""}]}),
    "bad date": raw(holiday_body(("a", "2026-02-30", ""))),
    "date with time": raw(holiday_body(("a", "2026-11-26T00:00:00", ""))),
    "tradingHour not text": raw({**HOLIDAYS, "data": [{"eventName": "a", "atDate": "2026-11-26", "tradingHour": None}]}),
    "name not text": raw({**HOLIDAYS, "data": [{"eventName": 5, "atDate": "2026-11-26", "tradingHour": ""}]}),
    "duplicate date": raw(holiday_body(("a", "2026-11-26", ""), ("b", "2026-11-26", "09:30-13:00"))),
    "good then bad": raw(holiday_body(("a", "2026-11-26", ""), ("b", "nope", ""))),
    "not bytes": "a string",
    "empty bytes": b"",
}


@pytest.mark.parametrize("name", sorted(BAD_HOLIDAYS))
def test_finnhub_holiday_parser_refuses_anything_unexpected_whole(name):
    with pytest.raises(k3.K3Error):
        k3.parse_finnhub_holiday(BAD_HOLIDAYS[name], fetched_at_utc=NOW_S)


def test_finnhub_holiday_parser_refuses_an_empty_list_but_not_a_one_item_list():
    with pytest.raises(k3.K3Error):
        parse_h(holiday_body())                                  # a broken feed, not "no holidays"
    assert len(parse_h(holiday_body(("a", "2026-11-26", "")))) == 1


def test_finnhub_holiday_parser_refuses_a_bad_fetch_time():
    with pytest.raises(k3.K3Error):
        parse_h(HOLIDAYS, fetched="2026-11-23 18:00")


# ---- Finnhub status parser

def parse_s(body, fetched=NOW_S):
    return k3.parse_finnhub_status(body if isinstance(body, bytes) else raw(body), fetched_at_utc=fetched)


def test_finnhub_status_parser_keys_on_the_new_york_date_of_t():
    (f,) = parse_s(status_body(t=unix(2026, 11, 27, 3, 0)))         # 03:00 UTC is 22:00 on the 26th in New York
    assert f["entity_key"] == "XNYS:STATUS:2026-11-26"
    assert json.loads(f["payload_json"]) == {"holiday": None, "session": "pre-market", "is_open": False}
    assert f["source_id"] == S and f["published_proven"] is False and f["raw_sha256"] == sha(f["payload_json"])
    (g,) = parse_s(status_body(t=unix(2026, 11, 27, 5, 30)))        # 05:30 UTC is 00:30 on the 27th
    assert g["entity_key"] == "XNYS:STATUS:2026-11-27"


def test_finnhub_status_parser_keeps_a_holiday_name():
    (f,) = parse_s(status_body(holiday="Thanksgiving Day", session="closed"))
    assert json.loads(f["payload_json"])["holiday"] == "Thanksgiving Day"


BAD_STATUS = {
    "not json": b"{",
    "wrong exchange": raw(status_body(exchange="X")),
    "wrong timezone": raw(status_body(timezone="UTC")),
    "isOpen text": raw(status_body(isOpen="true")),
    "isOpen missing": raw({k: v for k, v in status_body().items() if k != "isOpen"}),
    "t is a bool": raw(status_body(t=True)),
    "t is text": raw(status_body(t="1")),
    "t is a float": raw(status_body(t=1.5)),
    "t negative": raw(status_body(t=-1)),
    "t absurd": raw(status_body(t=10 ** 15)),
    "holiday a number": raw(status_body(holiday=5)),
    "session missing": raw({k: v for k, v in status_body().items() if k != "session"}),
    "session a number": raw(status_body(session=1)),
    "a list": raw([status_body()]),
}


@pytest.mark.parametrize("name", sorted(BAD_STATUS))
def test_finnhub_status_parser_refuses_anything_unexpected(name):
    with pytest.raises(k3.K3Error):
        k3.parse_finnhub_status(BAD_STATUS[name], fetched_at_utc=NOW_S)


# ---- Federal Register parser and the closure-date rule

def parse_fr(body, fetched=NOW_S):
    return k3.parse_federal_register(body if isinstance(body, bytes) else raw(body), fetched_at_utc=fetched)


def test_federal_register_parser_one_fact_per_document():
    body = fr_body(closing_doc("2026-12345", "December 24, 2026", "2026-12-20"),
                   fr_doc(num="2026-12346", title="Thanksgiving Day, 2026", pub="2026-11-20"))
    a, b = parse_fr(body, fetched="2026-12-22T00:00:00.000000Z")
    assert a["entity_key"] == "FR:2026-12345" and b["entity_key"] == "FR:2026-12346"
    pa = json.loads(a["payload_json"])
    assert set(pa) == {"document_number", "title", "type", "subtype", "publication_date", "signing_date", "html_url",
                       "abstract", "closure_dates", "closing_order_dates"}
    assert pa["closure_dates"] == [] and pa["closing_order_dates"] == ["2026-12-24"]       # a closing order is a signal
    pb = json.loads(b["payload_json"])
    assert pb["closure_dates"] == [] and pb["closing_order_dates"] == []
    (m,) = parse_fr(fr_body(mourning_doc("2026-12347", "December 24, 2026", "2026-12-20")),
                    fetched="2026-12-22T00:00:00.000000Z")
    pm = json.loads(m["payload_json"])
    assert pm["closure_dates"] == ["2026-12-24"] and pm["closing_order_dates"] == []
    assert a["domain"] == "K3_CAL" and a["source_id"] == FR and a["raw_sha256"] == sha(a["payload_json"])
    # known_at is the publication date, by the date-only rule (a proven stamp)
    assert a["published_proven"] is True
    assert a["published_at_utc"] == k3.date_only_known_at("2026-12-20", "America/New_York") == "2026-12-21T04:59:59.999999Z"


def test_federal_register_parser_accepts_the_real_extra_top_level_keys():
    body = {**fr_body(fr_doc()), "description": "Documents", "total_pages": 1}
    assert len(parse_fr(body)) == 1
    body["next_page_url"] = None                                  # null (or absent) means this is the last page
    assert len(parse_fr(body)) == 1


def test_known_at_is_the_publication_date_never_the_signing_date():
    (f,) = parse_fr(fr_body(fr_doc(pub="2026-11-16", signing="2026-11-10")), fetched="2026-11-20T00:00:00.000000Z")
    assert f["published_at_utc"] == "2026-11-17T04:59:59.999999Z"
    assert f["published_at_utc"] > "2026-11-11T00:00:00Z"                 # not the earlier signing date


def test_a_document_fetched_on_its_publication_day_is_not_proven_known_yet():
    # the end of the publication day is later than the fetch: the store would refuse a proven stamp, and it
    # would be false anyway, so the fact is known when it was fetched.
    (f,) = parse_fr(fr_body(fr_doc(pub="2026-11-23")), fetched="2026-11-23T18:00:00.000000Z")
    assert f["published_proven"] is False
    (g,) = parse_fr(fr_body(fr_doc(pub="2026-11-22")), fetched="2026-11-23T18:00:00.000000Z")
    assert g["published_proven"] is True


BAD_FR = {
    "not json": b"nope",
    "a list": raw([]),
    "no count": raw({"results": []}),
    "no results": raw({"count": 0}),
    "count not an int": raw({"count": "1", "results": [fr_doc()]}),
    "count says more than the page": raw({"count": 2, "results": [fr_doc()]}),
    "results not a list": raw({"count": 0, "results": {}}),
    "missing document_number": raw(fr_body({k: v for k, v in fr_doc().items() if k != "document_number"})),
    "missing title": raw(fr_body({k: v for k, v in fr_doc().items() if k != "title"})),
    "missing publication_date": raw(fr_body({k: v for k, v in fr_doc().items() if k != "publication_date"})),
    "title not text": raw(fr_body(fr_doc(title=5))),
    "bad publication date": raw(fr_body(fr_doc(pub="2026-02-30"))),
    "bad signing date": raw(fr_body(fr_doc(signing="soon"))),
    "abstract a number": raw(fr_body(fr_doc(abstract=5))),
    "document number with a slash": raw(fr_body(fr_doc(num="a/b"))),
    "duplicate documents": raw(fr_body(fr_doc(num="1"), fr_doc(num="1"))),
    "good then bad": raw(fr_body(fr_doc(num="1"), fr_doc(num="2", pub="x"))),
    "more pages follow": raw({**fr_body(fr_doc()), "total_pages": 2, "next_page_url": "https://example.invalid/p2"}),
}


@pytest.mark.parametrize("name", sorted(BAD_FR))
def test_federal_register_parser_refuses_anything_unexpected_whole(name):
    with pytest.raises(k3.K3Error):
        k3.parse_federal_register(BAD_FR[name], fetched_at_utc=NOW_S)


def test_federal_register_parser_allows_a_null_abstract_and_signing_date_and_subtype():
    (f,) = parse_fr(fr_body(fr_doc(abstract=None, signing=None, subtype=None)))
    p = json.loads(f["payload_json"])
    assert p["abstract"] is None and p["signing_date"] is None and p["subtype"] is None and p["closure_dates"] == []
    assert p["closing_order_dates"] == []


EO, PR = "Executive Order", "Proclamation"


@pytest.mark.parametrize("title,abstract,subtype,want", [
    # positives
    # (a closing order casts no vote: see CLOSING_POSITIVES below, where its dates are kept as a signal)
    (CLOSING_TITLE.format(d="December 24, 2025"), None, EO, []),
    (CLOSING_TITLE.format(d="January 9, 2025"), "", EO, []),
    ("National Day of Mourning for Jimmy Carter",
     "This proclamation designates Thursday, January 9, 2025, as a National Day of Mourning for the late President.",
     PR, ["2025-01-09"]),
    ("A National Day of Mourning on January 9, 2025", None, PR, ["2025-01-09"]),
    # positives: each accepted grammatical form
    ("Designating a Day", "The President declares Friday, March 6, 2026, to be a National Day of Mourning.", PR,
     ["2026-03-06"]),
    ("Day", "The President declares Friday, March 6, 2026 a National Day of Mourning.", PR, ["2026-03-06"]),
    ("National Day of Mourning", "Thursday, March 5, 2026, as a National Day of Mourning for X.", PR, ["2026-03-05"]),
    ("National Day of Mourning", "This proclamation designates March 5, 2026 and March 6, 2026, as a National Day of "
                                 "Mourning.", PR, ["2026-03-05", "2026-03-06"]),
    ("A proclamation", "I hereby designate Thursday, January 9, 2025, as a National Day of Mourning.", PR,
     ["2025-01-09"]),
    ("National Day of Mourning on Thursday, January 9, 2025", None, PR, ["2025-01-09"]),
    ("Order", "An order closing executive departments and agencies of the Federal Government on Friday, "
              "December 26, 2025.", EO, []),
    # negatives: no date, never guess
    (CLOSING_TITLE.format(d="the Day After Christmas"), None, EO, []),
    ("National Day of Mourning for Jimmy Carter", "Designates a national day of mourning.", PR, []),
    # a date with no year is not a date
    ("Closing of Executive Departments and Agencies on December 24", None, EO, []),
    ("Closing of Executive Departments and Agencies on December 24 and 26, 2025", None, EO, ["2025-12-26"][:0]),
    # not a real date
    ("Closing of Executive Departments and Agencies on February 30, 2026", None, EO, []),
    # a date in another sentence from the trigger
    ("National Day of Mourning for X", "The death occurred on December 29, 2024. It was a sad week.", PR, []),
    ("Closing of Executive Departments and Agencies", "Signed. Applies on January 9, 2025.", EO, []),
    # two dates that are not a plain list: unsure, so none
    ("National Day of Mourning for X", "President X died on December 29, 2024; the national day of mourning is "
                                       "January 9, 2025.", PR, []),
    # a trigger sentence with a date that is not the closure day, whatever the date's grammar
    ("National Day of Mourning; flags at half-staff through December 31, 2026", None, PR, []),
    ('Not Closing Executive Departments and Agencies on December 24, 2026', None, EO, []),
    ('Proposed Closing of Executive Departments and Agencies on December 24, 2026', None, EO, []),
    ('Notice of Closing Executive Departments and Agencies on December 24, 2026', None, EO, []),
    ('Closing Executive Departments and Agencies on December 24, 2026 only for employees in Alaska', None, EO, []),
    ('Closing Executive Departments and Agencies in the National Capital Region on December 24, 2026', None, EO, []),
    ('Closing Executive Departments and Agencies at noon on December 24, 2026', None, EO, []),
    ('Ending the National Day of Mourning on December 5, 2026', None, PR, []),
    ('National Day of Mourning on December 5, 2026 Is Not Being Declared', None, PR, []),
    ('Remembering December 5, 2020, as a National Day of Mourning', None, PR, []),
    ("National Day of Mourning for X who died on December 5, 2026", None, PR, []),
    ("National Day of Mourning Beginning December 5, 2026", None, PR, []),
    ("National Day of Mourning", "Flags at half-staff until December 12, 2026.", PR, []),
    ("National Day of Mourning for X", "X passed on December 5, 2026, and the nation mourns.", PR, []),
    ("Closing of Executive Departments and Agencies", "Following the death of X on December 5, 2026, the order is "
                                                      "issued.", EO, []),
    ("Closing of Executive Departments and Agencies after December 24, 2026", None, EO, []),
    ("National Day of Mourning observed from December 5, 2026", None, PR, []),
    ("Closing of Executive Departments and Agencies, due to the death of X, on December 5, 2026", None, EO, []),
    # not grammatically the closure day, and no word of span, start or death: the anchor alone refuses these
    ("National Day of Mourning for X, December 5, 2026", None, PR, []),
    ("National Day of Mourning (December 5, 2026)", None, PR, []),
    ("December 5, 2026 National Day of Mourning", None, PR, []),
    ("The nation observes December 5, 2026 as a day of reflection; National Day of Mourning", None, PR, []),
    ("Closing of Executive Departments and Agencies, December 24, 2026", None, EO, []),
    ("Closing of Executive Departments and Agencies; see December 24, 2026", None, EO, []),
    ("Closing of Executive Departments and Agencies of the Federal Government, effective on December 24, 2026", None,
     EO, []),
    # anchored, but a word of span, start or death is in the sentence: the word list alone refuses these
    ("National Day of Mourning", "This proclamation designates January 9, 2025, as a National Day of Mourning "
                                 "following the death of X.", PR, []),
    ("Closing of Executive Departments and Agencies on December 24, 2026 after the funeral", None, EO, []),
    ("National Day of Mourning on December 5, 2026 and flags at half-staff", None, PR, []),
    ("Closing of Executive Departments and Agencies on December 24, 2026 until further notice", None, EO, []),
    # no trigger at all
    ("Thanksgiving Day, 2026", "Sets November 26, 2026 as a day of thanks.", PR, []),
    ("Announcing the Death of a Former President", "Flags at half-staff through January 9, 2025.", PR, []),
    ("Day of National Mourning Observed", "On January 9, 2025.", PR, []),
    # the wrong kind of document
    (CLOSING_TITLE.format(d="December 24, 2025"), None, "Memorandum", []),
    (CLOSING_TITLE.format(d="December 24, 2025"), None, None, []),
    # a change or withdrawal is not a closure
    ("Amending the Order Closing Executive Departments and Agencies on December 24, 2025", None, EO, []),
    ("Revocation of the Closing of Executive Departments and Agencies on December 24, 2025", None, EO, []),
    ("Closing of Executive Departments and Agencies on December 24, 2025", "This order is rescinded.", EO, []),
])
def test_fr_closure_date_rule(title, abstract, subtype, want):
    assert k3.fr_closure_dates(title, abstract, subtype) == want


# The same grammar as before, now read for the SIGNAL: a closing order's dates, kept apart and never a vote.
CLOSING_POSITIVES = [
    (CLOSING_TITLE.format(d="December 24, 2025"), None, EO, ["2025-12-24"]),
    (CLOSING_TITLE.format(d="January 9, 2025"), "", EO, ["2025-01-09"]),
    ("Providing for the closing of executive departments and agencies on January 9, 2025", None, EO, ["2025-01-09"]),
    ("Closing Executive Departments and Agencies of the Federal Government on December 24, 2026 and December 26, 2026",
     None, EO, ["2026-12-24", "2026-12-26"]),
    ("Closing of Executive Departments and Agencies on December 26, 2026, and December 24, 2026", None, EO,
     ["2026-12-24", "2026-12-26"]),
    ("Executive Order", "The order closes things. It provides for the closing of executive departments and agencies "
                        "on Friday, December 26, 2025.", EO, ["2025-12-26"]),
    ("Order", "An order closing executive departments and agencies of the Federal Government on Friday, "
              "December 26, 2025.", EO, ["2025-12-26"]),
    ("Closing of Executive Departments and Agencies on December 26, 2026 and December 24, 2026 and December 24, 2026",
     None, EO, ["2026-12-24", "2026-12-26"]),
]


@pytest.mark.parametrize("title,abstract,subtype,want", CLOSING_POSITIVES)
def test_a_closing_orders_dates_are_a_signal_and_never_the_vote(title, abstract, subtype, want):
    assert k3._fr_closing_order_dates(title, abstract, subtype) == want       # kept for the log
    assert k3.fr_closure_dates(title, abstract, subtype) == []                # and casting no vote
    assert k3._fr_closing_order_dates(title, abstract, "Memorandum") == []    # the same document rules apply
    assert k3._fr_closing_order_dates("Amending the order. " + title, abstract, subtype) == []


def test_a_mourning_document_has_vote_dates_and_no_signal_dates():
    for title, abstract in [("National Day of Mourning", MOURNING_ABSTRACT.format(d="January 9, 2025")),
                            ("A National Day of Mourning on January 9, 2025", None)]:
        assert k3.fr_closure_dates(title, abstract, PR) == ["2025-01-09"]
        assert k3._fr_closing_order_dates(title, abstract, PR) == []


def test_a_sentence_that_is_both_a_closing_order_and_a_mourning_day_votes_for_the_mourning_form():
    title = "Closing of Executive Departments and Agencies on January 9, 2025, as a National Day of Mourning"
    assert k3.fr_closure_dates(title, None, EO) == ["2025-01-09"]
    assert k3._fr_closing_order_dates(title, None, EO) == []


def test_a_closing_sentence_that_is_unsure_voids_the_mourning_date_beside_it():
    clean = MOURNING_ABSTRACT.format(d="January 9, 2025")
    assert k3.fr_closure_dates("National Day of Mourning", clean, PR) == ["2025-01-09"]
    unsure = "Closing of Executive Departments and Agencies on January 9, 2025 until further notice."
    assert k3.fr_closure_dates("National Day of Mourning", f"{clean} {unsure}", PR) == []
    assert k3.fr_closure_dates("National Day of Mourning", f"{unsure} {clean}", PR) == []
    assert k3._fr_closing_order_dates("National Day of Mourning", f"{clean} {unsure}", PR) == []


def test_an_unsure_mourning_sentence_voids_the_closing_order_dates_beside_it():
    title = "Closing of Executive Departments and Agencies on January 9, 2025"
    assert k3._fr_closing_order_dates(title, None, EO) == ["2025-01-09"]
    unsure = "Flags will fly at half-staff through January 12, 2025, for the National Day of Mourning."
    assert k3._fr_closing_order_dates(title, unsure, EO) == []
    assert k3.fr_closure_dates(title, unsure, EO) == []


# The Federal Register's orders "closing executive departments and agencies", as their titles read (hand-written
# from the ticket's list; subtype "Executive Order", title only). 15 were Christmas-week leave on days XNYS was open;
# only the last two (13852, 14133) were real closures, and a separate proclamation made those National Days of Mourning.
_CLOSE = "Providing for the Closing of Executive Departments and Agencies of the Federal Government on {d}"
REAL_CLOSING_ORDERS = [
    ("14371", "December 24, 2025, and December 26, 2025", ["2025-12-24", "2025-12-26"]),
    ("14129", "December 24, 2024", ["2024-12-24"]),
    ("13965", "December 24, 2020", ["2020-12-24"]),
    ("13900", "December 24, 2019", ["2019-12-24"]),
    ("13854", "Monday, December 24, 2018", ["2018-12-24"]),
    ("13713", "Thursday, December 24, 2015", ["2015-12-24"]),
    ("13682", "December 26, 2014", ["2014-12-26"]),
    ("13633", "Monday, December 24, 2012", ["2012-12-24"]),
    ("13523", "Thursday, December 24, 2009", ["2009-12-24"]),
    ("13482", "December 26, 2008", ["2008-12-26"]),
    ("13453", "Monday, December 24, 2007", ["2007-12-24"]),
    ("13281", "December 24, 2002", ["2002-12-24"]),
    ("13109", "Thursday, December 24, 1998", ["1998-12-24"]),
    ("13852", "December 5, 2018", ["2018-12-05"]),
    ("14133", "January 9, 2025", ["2025-01-09"]),
    # titles whose date the grammar does not take: still no vote, and no signal either
    ("13713", "the Afternoon of December 24, 2015", []),
    ("13523", "the Last Half of December 24, 2009", []),
]


def test_the_fixtures_cover_every_order_the_ticket_names_by_number():
    # the ticket says 17 and numbers 15 of them (13 Christmas-week orders and the two mourning ones); the last two rows
    # are other forms a title can take, under numbers already listed
    assert len({n for n, _, _ in REAL_CLOSING_ORDERS}) == 15 and len(REAL_CLOSING_ORDERS) == 17


@pytest.mark.parametrize("number,day_text,signal", REAL_CLOSING_ORDERS)
def test_a_real_closing_order_title_casts_no_vote_and_keeps_its_date_as_a_signal(number, day_text, signal):
    title = _CLOSE.format(d=day_text)
    assert k3.fr_closure_dates(title, None, EO) == []
    (f,) = parse_fr(fr_body(fr_doc(num=f"EO-{number}", title=title, subtype=EO, pub="2026-11-20")))
    payload = json.loads(f["payload_json"])
    assert payload["closure_dates"] == [] and payload["closing_order_dates"] == signal


@pytest.mark.parametrize("number,day_text,signal", [r for r in REAL_CLOSING_ORDERS if r[2]])
def test_a_real_closing_order_does_not_vote_in_the_quorum(conn, number, day_text, signal):
    day = signal[0]
    register(conn)
    k3.refresh(conn, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-30"})
    # the quorum is asked about the order's own date; no schedule fact is needed for the vote to be refused
    seed_fr(conn, fr_doc(num=f"EO-{number}", title=_CLOSE.format(d=day_text), subtype=EO, pub="2026-11-20"))
    v = votes(conn, day=day, as_of="2099-01-01T00:00:00Z")
    assert v["authorities_closed"] == [] and v["sources_closed"] == []
    assert v["ordinary_stop"] is False and v["suspected"] is False


CLEAN = "This proclamation designates January 9, 2025, as a National Day of Mourning."
UNSURE = "Flags will fly at half-staff through January 12, 2025, for the National Day of Mourning."


@pytest.mark.parametrize("title,abstract,alone_title,alone_abstract", [
    ("National Day of Mourning", f"{CLEAN} {UNSURE}", "National Day of Mourning", CLEAN),
    ("National Day of Mourning", f"{UNSURE} {CLEAN}", "National Day of Mourning", CLEAN),
    ("National Day of Mourning for X who died on December 5, 2026", CLEAN, "National Day of Mourning for X", CLEAN),
    ("National Day of Mourning", f"{CLEAN} National Day of Mourning begins January 8, 2025.", "National Day of Mourning",
     CLEAN),
])
def test_one_unsure_sentence_voids_the_whole_document(title, abstract, alone_title, alone_abstract):
    for kind in ("Proclamation", "Executive Order"):
        assert k3.fr_closure_dates(alone_title, alone_abstract, kind) == ["2025-01-09"]      # the clean part alone counts
        assert k3.fr_closure_dates(title, abstract, kind) == []                              # beside an unsure one it does not


def test_fr_closure_dates_are_sorted_unique():
    got = k3.fr_closure_dates("National Day of Mourning", "This proclamation designates December 26, 2026 and "
                              "December 24, 2026 and December 24, 2026, as a National Day of Mourning.", PR)
    assert got == ["2026-12-24", "2026-12-26"]


def test_fr_closure_rule_refuses_non_text_input():
    for args in [(None, None, EO), (5, None, EO), ("t", 5, EO), ("t", None, 5)]:
        with pytest.raises(k3.K3Error):
            k3.fr_closure_dates(*args)


# ---- the quorum

D = "2026-11-24"              # a Tuesday, an open day
AS_OF = "2026-11-24T14:00:00Z"
LATE = "2026-11-23T20:00:00.000000Z"


def votes(conn, day=D, as_of=AS_OF):
    return k3.closure_votes(conn, day, as_of, finnhub_conn=fin(conn))


def seed(conn, days=("2026-11-23", "2026-11-30"), now=NOW):
    register(conn)
    k3.refresh(conn, X, http_get=None, now=now, params={"start": days[0], "end": days[1]})


def seed_holiday(conn, *items, fetched=LATE):
    put(conn, parse_h(holiday_body(*items), fetched=fetched))


def seed_status(conn, fetched=LATE, **over):
    put(conn, parse_s(status_body(**over), fetched=fetched))


def seed_fr(conn, *docs, fetched=LATE):
    put(conn, parse_fr(fr_body(*docs), fetched=fetched))


def test_quorum_open_calendar_with_no_authority(conn):
    seed(conn)
    v = votes(conn)
    assert v["calendar_open"] is True and v["calendar_closed"] is False
    assert v["authorities_closed"] == [] and v["ordinary_stop"] is False and v["suspected"] is False
    assert v["trade_date"] == D and v["schedule_known"] is True


def test_quorum_one_authority_is_only_suspected(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN] and v["suspected"] is True and v["ordinary_stop"] is False


def test_quorum_two_authorities_is_an_ordinary_stop(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    seed_fr(conn, mourning_doc("2026-55555", "November 24, 2026", "2026-11-22"))
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN, G_PRES] and v["ordinary_stop"] is True and v["suspected"] is False


def test_quorum_three_authorities_stays_an_ordinary_stop(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    seed_fr(conn, mourning_doc("2026-55555", "November 24, 2026", "2026-11-22"))
    seed_status(conn, holiday="Surprise Closure", t=unix(2026, 11, 24, 13, 0), fetched="2026-11-24T13:30:00.000000Z")
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN, G_PRES] and v["ordinary_stop"] is True


def test_quorum_two_finnhub_endpoints_are_one_group_and_only_suspected(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    seed_status(conn, holiday="Surprise Closure", t=unix(2026, 11, 24, 13, 0), fetched="2026-11-24T13:30:00.000000Z")
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN] and v["sources_closed"] == sorted([H, S])
    assert v["suspected"] is True and v["ordinary_stop"] is False


def test_quorum_finnhub_and_a_mourning_document_is_an_ordinary_stop(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    seed_fr(conn, mourning_doc("2026-55555", "November 24, 2026", "2026-11-22"))
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN, G_PRES] and v["sources_closed"] == sorted([H, FR])
    assert v["ordinary_stop"] is True and v["suspected"] is False


def test_quorum_both_finnhub_endpoints_and_a_mourning_document_are_two_groups(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    seed_status(conn, holiday="Surprise Closure", t=unix(2026, 11, 24, 13, 0), fetched="2026-11-24T13:30:00.000000Z")
    seed_fr(conn, mourning_doc("2026-55555", "November 24, 2026", "2026-11-22"))
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN, G_PRES] and v["sources_closed"] == sorted([H, S, FR])
    assert v["ordinary_stop"] is True


def test_quorum_two_mourning_documents_for_one_date_are_one_group(conn):
    seed(conn)
    seed_fr(conn, mourning_doc("2026-1", "November 24, 2026", "2026-11-21"),
            fr_doc(num="2026-2", title="A National Day of Mourning on November 24, 2026", pub="2026-11-22",
                   subtype="Proclamation"))
    v = votes(conn)
    assert v["authorities_closed"] == [G_PRES] and v["sources_closed"] == [FR]
    assert v["suspected"] is True and v["ordinary_stop"] is False


def test_quorum_a_closing_order_beside_finnhub_is_still_only_suspected(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    seed_fr(conn, closing_doc("2026-55555", "November 24, 2026", "2026-11-22"))
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN] and v["sources_closed"] == [H]
    assert v["suspected"] is True and v["ordinary_stop"] is False


def test_quorum_sources_closed_lists_the_source_ids_sorted_and_empty_when_none(conn):
    seed(conn)
    assert votes(conn)["sources_closed"] == []
    seed_status(conn, holiday="Surprise Closure", t=unix(2026, 11, 24, 13, 0), fetched="2026-11-24T13:30:00.000000Z")
    assert votes(conn)["sources_closed"] == [S]
    seed_holiday(conn, ("Surprise Closure", D, ""))
    assert votes(conn)["sources_closed"] == [H, S] == sorted([S, H])


def test_quorum_the_schedule_is_a_group_that_never_votes_closed(conn):
    seed(conn, days=("2026-11-23", "2026-11-30"))
    v = votes(conn, day="2026-11-26", as_of="2026-11-26T14:00:00Z")       # Thanksgiving: the calendar says closed
    assert v["calendar_closed"] is True and v["authorities_closed"] == [] and v["sources_closed"] == []


def test_quorum_an_authority_without_a_group_is_a_setup_error(conn, monkeypatch):
    seed(conn)
    entry = {k: v for k, v in k3.SOURCES[H].items() if k != "authority_group"}
    monkeypatch.setitem(k3.SOURCES, H, entry)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    with pytest.raises(k3.K3Error):
        votes(conn)


def test_quorum_a_status_with_a_holiday_counts_for_its_own_date_only(conn):
    seed(conn)
    seed_status(conn, holiday="Surprise Closure", t=unix(2026, 11, 24, 13, 0), fetched="2026-11-24T13:30:00.000000Z")
    assert votes(conn)["authorities_closed"] == [G_FIN]
    assert votes(conn, day="2026-11-25", as_of="2026-11-25T14:00:00Z")["authorities_closed"] == []


def test_quorum_a_status_before_the_open_with_isopen_false_and_no_holiday_does_not_vote(conn):
    seed(conn)
    seed_status(conn, holiday=None, isOpen=False, session="pre-market", t=unix(2026, 11, 24, 13, 0),
                fetched="2026-11-24T13:30:00.000000Z")
    v = votes(conn)
    assert v["authorities_closed"] == [] and v["suspected"] is False and v["ordinary_stop"] is False


def test_quorum_a_status_that_says_closed_session_without_a_holiday_does_not_vote(conn):
    seed(conn)
    seed_status(conn, holiday=None, isOpen=False, session="closed", t=unix(2026, 11, 24, 23, 0),
                fetched="2026-11-24T23:30:00.000000Z")
    assert votes(conn, as_of="2026-11-25T00:00:00Z")["authorities_closed"] == []


def test_quorum_a_holiday_row_that_is_a_half_day_is_not_a_closure(conn):
    seed(conn)
    seed_holiday(conn, ("Half day", D, "09:30-13:00"))
    assert votes(conn)["authorities_closed"] == []


def test_quorum_a_document_naming_another_date_does_not_vote(conn):
    seed(conn)
    seed_fr(conn, mourning_doc("2026-55555", "November 25, 2026", "2026-11-22"))
    assert votes(conn)["authorities_closed"] == []
    assert votes(conn, day="2026-11-25")["authorities_closed"] == [G_PRES]


def test_quorum_one_source_with_two_facts_for_the_date_counts_once(conn):
    seed(conn)
    seed_fr(conn, mourning_doc("2026-1", "November 24, 2026", "2026-11-21"),
            mourning_doc("2026-2", "November 24, 2026", "2026-11-22"))
    v = votes(conn)
    assert v["authorities_closed"] == [G_PRES] and v["suspected"] is True and v["ordinary_stop"] is False


def test_quorum_a_closed_calendar(conn):
    seed(conn)
    seed_holiday(conn, ("Thanksgiving Day", "2026-11-26", ""))
    seed_fr(conn, mourning_doc("2026-3", "November 26, 2026", "2026-11-22"))
    v = votes(conn, day="2026-11-26", as_of="2026-11-26T14:00:00Z")
    assert v["calendar_open"] is False and v["calendar_closed"] is True
    assert v["authorities_closed"] == [G_FIN, G_PRES]                    # reported, but they are no news
    assert v["ordinary_stop"] is False and v["suspected"] is False


def test_quorum_without_a_schedule_fact_is_unknown_not_open(conn):
    register(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    seed_fr(conn, mourning_doc("2026-55555", "November 24, 2026", "2026-11-22"))
    v = votes(conn)
    assert v["schedule_known"] is False and v["calendar_open"] is None and v["calendar_closed"] is None
    assert v["ordinary_stop"] is False and v["suspected"] is False


def test_quorum_a_fact_known_after_as_of_never_counts(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""), fetched="2026-11-24T13:00:00.000000Z")
    assert votes(conn, as_of="2026-11-24T12:59:59Z")["authorities_closed"] == []        # backfilled: not yet known
    assert votes(conn, as_of="2026-11-24T13:00:00Z")["authorities_closed"] == [G_FIN]       # and the edge itself counts


def test_quorum_a_document_published_that_day_is_known_only_after_the_day_ends(conn):
    seed(conn)
    doc = mourning_doc("2026-9", "November 24, 2026", "2026-11-24")
    seed_fr(conn, doc, fetched="2026-11-26T00:00:00.000000Z")               # fetched later: the publication date proves it
    stamp = k3.date_only_known_at("2026-11-24", "America/New_York")         # 2026-11-25T04:59:59.999999Z
    assert votes(conn, as_of="2026-11-24T23:00:00Z")["authorities_closed"] == []
    assert votes(conn, as_of=stamp)["authorities_closed"] == [G_PRES]
    assert votes(conn, as_of="2026-11-25T04:59:59Z")["authorities_closed"] == []         # one second short


def test_quorum_the_schedule_fact_known_after_as_of_is_not_used(conn):
    seed(conn, now=datetime(2026, 11, 25, tzinfo=timezone.utc))
    assert votes(conn, as_of="2026-11-24T14:00:00Z")["schedule_known"] is False
    assert votes(conn, as_of="2026-11-25T00:00:00Z")["schedule_known"] is True


def test_quorum_follows_a_changed_holiday_list_by_as_of(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""), fetched="2026-11-23T20:00:00.000000Z")
    seed_holiday(conn, ("Surprise Closure", D, "09:30-13:00"), fetched="2026-11-24T10:00:00.000000Z")   # reopened early
    assert votes(conn, as_of="2026-11-24T09:00:00Z")["authorities_closed"] == [G_FIN]
    assert votes(conn, as_of="2026-11-24T11:00:00Z")["authorities_closed"] == []


def test_quorum_counts_authorities_generically(conn, monkeypatch):
    """A third authority (the exchange's own notice) is a slot: add a source and a rule, nothing else changes."""
    seed(conn)
    entry = dict(k3.SOURCES[H], source_id="exchange_closure_notice", role="AUTHORITY", allow_external_llm=1,
                 licence_tag="EXCHANGE-NOTICE", authority_group="EXCHANGE")
    monkeypatch.setitem(k3.SOURCES, "exchange_closure_notice", entry)
    monkeypatch.setitem(k3._CLOSURE_RULES, "exchange_closure_notice",
                        lambda entity_key, payload, day: entry_says_closed(entity_key, payload, day))
    k3.register_all(conn, T0, only=["exchange_closure_notice"])
    put(conn, [dict(domain="K3_CAL", entity_key=f"NOTICE:{D}", source_id="exchange_closure_notice",
                    raw_sha256=sha("n"), payload_json='{"closed":true}', effective_from_utc=T0,
                    fetched_at_utc=LATE, published_at_utc=None, effective_to_utc=None, published_proven=False)])
    seed_holiday(conn, ("Surprise Closure", D, ""))
    v = votes(conn)
    assert v["authorities_closed"] == ["EXCHANGE", G_FIN] and v["ordinary_stop"] is True
    assert v["sources_closed"] == sorted([H, "exchange_closure_notice"])


def entry_says_closed(entity_key, payload, day):
    return entity_key == f"NOTICE:{day}" and payload.get("closed") is True


def test_every_authority_has_a_rule_and_no_other_source_votes():
    authorities = {sid for sid, e in k3.SOURCES.items() if e["role"] == "AUTHORITY"}
    assert authorities == {H, S, FR, WH} and set(k3._CLOSURE_RULES) == authorities


def test_quorum_checks_its_inputs(conn):
    seed(conn)
    for day in ["2026-11-31", "11/24/2026", None, "2026-11-24T00:00:00"]:
        with pytest.raises(k3.K3Error):
            votes(conn, day=day)
    for as_of in ["2026-11-24 14:00", None, "2026-11-24T14:00:00+00:00"]:
        with pytest.raises((k3.K3Error, StoreError)):
            votes(conn, as_of=as_of)


def test_quorum_is_a_pure_read_and_stores_nothing(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    before = facts_as_of(conn, "2027-01-01T00:00:00Z")
    before_fin = facts_as_of(fin(conn), "2027-01-01T00:00:00Z")
    assert before_fin and before                                               # both files hold rows, so both checks bite
    votes(conn)
    votes(conn)
    assert facts_as_of(conn, "2027-01-01T00:00:00Z") == before
    assert facts_as_of(fin(conn), "2027-01-01T00:00:00Z") == before_fin


def test_the_module_never_names_the_facts_table_itself():
    text = inspect.getsource(k3)
    assert "knowledge_facts" not in text.lower() and "facts_as_of" in text


# ---- fetch and refresh

class Fake:
    """An injected http_get that records every request and answers from a map of url prefix to bytes."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        for prefix, body in self.answers.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, bytes) else raw(body)
        raise AssertionError(f"unexpected url {url}")


def creds(name):
    assert name == "FINNHUB_API_KEY"
    return TOKEN


H_URL = "https://finnhub.io/api/v1/stock/market-holiday?exchange=US"
S_URL = "https://finnhub.io/api/v1/stock/market-status?exchange=US"
FR_URL = "https://www.federalregister.gov/api/v1/documents.json"


def test_fetch_finnhub_holiday_request_and_facts():
    fake = Fake({H_URL: HOLIDAYS})
    facts = k3.fetch(H, http_get=fake, now=NOW, credentials=creds)
    assert len(facts) == 3 and fake.calls[0][0] == H_URL
    headers = fake.calls[0][1]
    assert headers["X-Finnhub-Token"] == TOKEN and headers["User-Agent"] == "valedia-client/1.0"
    assert TOKEN not in fake.calls[0][0]
    assert all(f["fetched_at_utc"] == NOW_S for f in facts)


def test_fetch_finnhub_status_request():
    fake = Fake({S_URL: status_body()})
    (f,) = k3.fetch(S, http_get=fake, now=NOW, credentials=creds)
    assert fake.calls[0][0] == S_URL and fake.calls[0][1]["X-Finnhub-Token"] == TOKEN and f["source_id"] == S


def test_fetch_user_agent_is_generic():
    fake = Fake({FR_URL: fr_body()})
    k3.fetch(FR, http_get=fake, now=NOW)
    ua = fake.calls[0][1]["User-Agent"]
    assert ua == "valedia-client/1.0" and "@" not in ua
    assert not any("X-Finnhub-Token" == h for h in fake.calls[0][1])           # no key goes to a keyless source


def test_fetch_federal_register_request_is_the_documented_query():
    fake = Fake({FR_URL: fr_body()})
    k3.fetch(FR, http_get=fake, now=NOW, params={"start": "2026-11-01", "end": "2026-11-23"})
    url = fake.calls[0][0]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == FR_URL
    q = parse_qs(parts.query)
    assert q["conditions[type][]"] == ["PRESDOCU"]
    assert q["conditions[presidential_document_type][]"] == ["proclamation", "executive_order"]    # both kinds
    assert q["order"] == ["newest"]
    assert q["conditions[publication_date][gte]"] == ["2026-11-01"]
    assert q["conditions[publication_date][lte]"] == ["2026-11-23"]
    assert q["per_page"] == ["100"] and int(q["per_page"][0]) <= 100
    assert q["fields[]"] == ["document_number", "title", "type", "subtype", "publication_date", "signing_date",
                             "html_url", "abstract", "raw_text_url"]
    assert TOKEN not in url


def test_fetch_federal_register_default_window_is_thirty_days_ending_on_the_new_york_date_of_now():
    fake = Fake({FR_URL: fr_body()})
    k3.fetch(FR, http_get=fake, now=NOW)
    q = parse_qs(urlsplit(fake.calls[0][0]).query)
    assert q["conditions[publication_date][lte]"] == ["2026-11-23"] and q["conditions[publication_date][gte]"] == ["2026-10-24"]


@pytest.mark.parametrize("params", [{"start": "2026-11-23", "end": "2026-11-01"}, {"start": "x", "end": "2026-11-01"},
                                    {"bogus": 1}, {"start": "2026-01-01", "end": "2026-11-01"}, "text"])
def test_fetch_federal_register_refuses_bad_params(params):
    fake = Fake({FR_URL: fr_body()})
    with pytest.raises(k3.K3Error):
        k3.fetch(FR, http_get=fake, now=NOW, params=params)
    assert fake.calls == []


def test_fetch_refuses_params_for_finnhub_and_unknown_source_and_no_client():
    with pytest.raises(k3.K3Error):
        k3.fetch(H, http_get=Fake({}), now=NOW, credentials=creds, params={"x": 1})
    with pytest.raises(k3.K3Error):
        k3.fetch("no_such", http_get=Fake({}), now=NOW)
    with pytest.raises(k3.K3Error):
        k3.fetch(FR, http_get=None, now=NOW)                    # there is no default network client
    with pytest.raises(k3.K3Error):
        k3.fetch(H, http_get=Fake({H_URL: HOLIDAYS}), now=datetime(2026, 11, 23))        # a naive time
    assert "urllib" not in inspect.getsource(k3.fetch) and "urllib" not in inspect.getsource(k3.refresh)


def test_the_only_network_client_is_built_by_the_cli():
    src = inspect.getsource(k3)
    assert src.count("urllib.request") >= 1 and "def _urllib_get" in src
    for name in ("fetch", "refresh", "closure_votes", "schedule_facts"):
        assert "urllib.request" not in inspect.getsource(getattr(k3, name))


@pytest.mark.parametrize("bad", ["", "has space", "x" * 300, "tab\tkey", "naïve", 5, None])
def test_fetch_refuses_a_key_that_cannot_be_a_header_without_echoing_it(bad):
    fake = Fake({H_URL: HOLIDAYS})
    with pytest.raises(k3.K3Error) as err:
        k3.fetch(H, http_get=fake, now=NOW, credentials=lambda name: bad)
    assert fake.calls == [] and (not isinstance(bad, str) or not bad or bad not in str(err.value))


def test_fetch_missing_credentials_error_has_no_secret_text():
    def boom(name):
        raise RuntimeError(f"the key {TOKEN} is wrong")
    fake = Fake({H_URL: HOLIDAYS})
    with pytest.raises(k3.K3Error) as err:
        k3.fetch(H, http_get=fake, now=NOW, credentials=boom)
    assert TOKEN not in str(err.value) and fake.calls == []


def test_default_credentials_read_finnhub_api_key_through_core_secrets(monkeypatch):
    from valedia_trader.core import secrets
    seen = []
    monkeypatch.setattr(secrets, "secret", lambda name, *a, **k: seen.append(name) or TOKEN)
    fake = Fake({H_URL: HOLIDAYS})
    k3.fetch(H, http_get=fake, now=NOW)
    assert seen == ["FINNHUB_API_KEY"] and fake.calls[0][1]["X-Finnhub-Token"] == TOKEN


def test_default_credentials_without_a_secret_fail_closed():
    fake = Fake({H_URL: HOLIDAYS})
    with pytest.raises(k3.K3Error):
        k3.fetch(H, http_get=fake, now=NOW)                    # the suite points the secrets file at nothing
    assert fake.calls == []


def test_http_get_must_return_bytes():
    with pytest.raises(k3.K3Error):
        k3.fetch(FR, http_get=lambda url, headers: "text", now=NOW)


def test_refresh_stores_then_rereads_unchanged_then_stores_a_changed_version(conn):
    register(conn)
    fake = Fake({H_URL: HOLIDAYS})
    assert k3.refresh(fin(conn), H, http_get=fake, now=NOW, credentials=creds) == {"stored": 3, "unchanged": 0, "error": 0}
    assert k3.refresh(fin(conn), H, http_get=fake, now=NOW + timedelta(hours=1), credentials=creds) == \
        {"stored": 0, "unchanged": 3, "error": 0}
    changed = holiday_body(("Thanksgiving Day", "2026-11-26", ""), ("Day After Thanksgiving", "2026-11-27", "09:30-14:00"),
                           ("Christmas Day", "2026-12-25", ""), ("New Year's Day", "2027-01-01", ""))
    fake2 = Fake({H_URL: changed})
    got = k3.refresh(fin(conn), H, http_get=fake2, now=NOW + timedelta(days=1), credentials=creds)
    assert got == {"stored": 2, "unchanged": 2, "error": 0}                   # one changed date, one new date
    rows = {r["entity_key"]: json.loads(r["payload_json"]) for r in facts_as_of(fin(conn), "2027-06-01T00:00:00Z", domain="K3_CAL")}
    assert rows["XNYS:CLOSURE:2026-11-27"]["trading_hours"] == "09:30-14:00" and "XNYS:CLOSURE:2027-01-01" in rows
    old = {r["entity_key"]: json.loads(r["payload_json"]) for r in facts_as_of(fin(conn), "2026-11-23T19:00:00Z", domain="K3_CAL")}
    assert old["XNYS:CLOSURE:2026-11-27"]["trading_hours"] == "09:30-13:00"            # the old version is still readable


def test_refresh_returns_counts_only_and_a_failed_fetch_is_an_error_not_a_crash(conn):
    register(conn)
    boom = Fake({H_URL: RuntimeError(f"connection reset for {TOKEN}")})
    got = k3.refresh(fin(conn), H, http_get=boom, now=NOW, credentials=creds)
    assert got["error"] == 1 and got["stored"] == 0 and got["unchanged"] == 0
    assert TOKEN not in json.dumps(got) and TOKEN not in str(got.get("detail", ""))


def test_refresh_never_stores_half_a_parsed_body(conn):
    register(conn)
    body = holiday_body(("a", "2026-11-26", ""), ("b", "nope", ""))
    got = k3.refresh(fin(conn), H, http_get=Fake({H_URL: body}), now=NOW, credentials=creds)
    assert got["error"] == 1 and got["stored"] == 0
    assert facts_as_of(fin(conn), "2027-01-01T00:00:00Z") == []                # the file a Finnhub refresh writes
    assert facts_as_of(conn, "2027-01-01T00:00:00Z") == []                     # and the general file, which must stay empty too


def test_refresh_with_an_unregistered_source_stores_nothing_and_reports_an_error(conn):
    got = k3.refresh(fin(conn), H, http_get=Fake({H_URL: HOLIDAYS}), now=NOW, credentials=creds)
    assert got["error"] >= 1 and got["stored"] == 0
    assert facts_as_of(fin(conn), "2027-01-01T00:00:00Z") == []
    assert facts_as_of(conn, "2027-01-01T00:00:00Z") == []


def test_refresh_federal_register_and_status(conn):
    register(conn)
    fr = Fake({FR_URL: fr_body(closing_doc("2026-7", "December 24, 2026", "2026-11-20"))})
    assert k3.refresh(conn, FR, http_get=fr, now=NOW) == {"stored": 1, "unchanged": 0, "error": 0}
    assert k3.refresh(conn, FR, http_get=fr, now=NOW + timedelta(hours=1)) == {"stored": 0, "unchanged": 1, "error": 0}
    st = Fake({S_URL: status_body()})
    assert k3.refresh(fin(conn), S, http_get=st, now=NOW, credentials=creds)["stored"] == 1
    st2 = Fake({S_URL: status_body(session="regular", isOpen=True)})
    assert k3.refresh(fin(conn), S, http_get=st2, now=NOW + timedelta(hours=1), credentials=creds)["stored"] == 1


def test_finnhub_facts_never_reach_an_outside_model(conn):
    register(conn)
    k3.refresh(fin(conn), H, http_get=Fake({H_URL: HOLIDAYS}), now=NOW, credentials=creds)
    k3.refresh(fin(conn), S, http_get=Fake({S_URL: status_body()}), now=NOW, credentials=creds)
    k3.refresh(conn, FR, http_get=Fake({FR_URL: fr_body(fr_doc())}), now=NOW)
    k3.refresh(conn, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-24"})
    # K-7: the Finnhub rows are in their own store; the general store holds none and the Finnhub store refuses the outside read
    inside = facts_as_of(conn, "2027-01-01T00:00:00Z") + facts_as_of(fin(conn), "2027-01-01T00:00:00Z")
    outside = facts_as_of(conn, "2027-01-01T00:00:00Z", for_outside_llm=True)
    assert {r["source_id"] for r in inside} == {H, S, FR, X}                       # the two-way check: they do exist
    assert {r["source_id"] for r in facts_as_of(fin(conn), "2027-01-01T00:00:00Z")} == {H, S}
    assert {r["source_id"] for r in outside} == {FR, X}
    assert not any("finnhub" in r["source_id"] for r in outside)
    with pytest.raises(StoreError):
        facts_as_of(fin(conn), "2027-01-01T00:00:00Z", for_outside_llm=True)


def test_the_token_is_never_in_a_url_header_of_other_sources_or_a_stored_payload(conn):
    register(conn)
    fakes = [Fake({H_URL: HOLIDAYS}), Fake({S_URL: status_body()}), Fake({FR_URL: fr_body(fr_doc())})]
    for sid, fake in zip((H, S, FR), fakes):
        k3.refresh(side(conn, sid), sid, http_get=fake, now=NOW, credentials=creds)
    for fake in fakes:
        for url, headers in fake.calls:
            assert TOKEN not in url
            assert all(TOKEN not in v for h, v in headers.items() if h != "X-Finnhub-Token")
    rows = facts_as_of(conn, "2027-01-01T00:00:00Z") + facts_as_of(fin(conn), "2027-01-01T00:00:00Z")
    assert {r["source_id"] for r in rows} == {H, S, FR} and not any(TOKEN in json.dumps(dict(r)) for r in rows)


# ---- removed holidays (tombstones)

def refresh_h(conn, body, now):
    return k3.refresh(fin(conn), H, http_get=Fake({H_URL: body}), now=now, credentials=creds)


def closure_payload(conn, day, as_of="2099-01-01T00:00:00Z"):
    rows = [r for r in facts_as_of(fin(conn), as_of, domain="K3_CAL")
            if r["source_id"] == H and r["entity_key"] == f"XNYS:CLOSURE:{day}"]
    return json.loads(rows[0]["payload_json"]) if rows else None


T_A = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)
T_B = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
T_C = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
ANCHOR = ("Anchor Day", "2026-11-10", "")            # an earlier date, so that D falls inside the listed range
LIST_1 = holiday_body(ANCHOR, ("Surprise Closure", D, ""), ("Thanksgiving Day", "2026-11-26", ""),
                      ("Christmas Day", "2026-12-25", ""))
LIST_NO_D = holiday_body(ANCHOR, ("Thanksgiving Day", "2026-11-26", ""), ("Christmas Day", "2026-12-25", ""))
TOMBSTONE = {"closed": False, "removed": True, "trading_hours": None, "name": "Surprise Closure"}


def test_a_date_removed_inside_the_listed_range_gets_a_tombstone_and_stops_voting(conn):
    seed(conn)
    assert refresh_h(conn, LIST_1, T_A) == {"stored": 4, "unchanged": 0, "error": 0}
    assert votes(conn, as_of="2026-11-24T09:00:00Z")["authorities_closed"] == [G_FIN]
    assert refresh_h(conn, LIST_NO_D, T_B) == {"stored": 1, "unchanged": 3, "error": 0}
    assert closure_payload(conn, D) == TOMBSTONE
    assert votes(conn, as_of="2026-11-24T11:00:00Z")["authorities_closed"] == []          # the source no longer counts
    assert votes(conn, as_of="2026-11-24T09:00:00Z")["authorities_closed"] == [G_FIN]          # and the past is unchanged
    assert closure_payload(conn, D, as_of="2026-11-24T09:00:00Z")["closed"] is True


def test_a_tombstone_is_stored_once(conn):
    seed(conn)
    refresh_h(conn, LIST_1, T_A)
    refresh_h(conn, LIST_NO_D, T_B)
    assert refresh_h(conn, LIST_NO_D, T_C) == {"stored": 0, "unchanged": 3, "error": 0}


def test_a_date_that_only_rolled_off_the_edge_of_the_list_is_not_a_removal(conn):
    seed(conn)
    refresh_h(conn, LIST_1, T_A)
    front = holiday_body(("Christmas Day", "2026-12-25", ""), ("New Year's Day", "2027-01-01", ""))
    refresh_h(conn, front, T_B)                    # D and Nov 26 are before the new list's first date
    assert closure_payload(conn, D)["closed"] is True and "removed" not in closure_payload(conn, D)
    assert closure_payload(conn, "2026-11-26")["closed"] is True
    back = holiday_body(("Surprise Closure", D, ""), ("Thanksgiving Day", "2026-11-26", ""))
    refresh_h(conn, back, T_C)                     # Dec 25 is after the new list's last date
    assert closure_payload(conn, "2026-12-25")["closed"] is True and "removed" not in closure_payload(conn, "2026-12-25")


def test_the_range_edges_themselves_are_inside_the_range(conn):
    seed(conn)
    refresh_h(conn, LIST_1, T_A)
    inner = holiday_body(("Surprise Closure", D, ""), ("Christmas Day", "2026-12-25", ""))   # Nov 26 is between: removed
    refresh_h(conn, inner, T_B)
    assert closure_payload(conn, "2026-11-26") == {"closed": False, "removed": True, "trading_hours": None,
                                                   "name": "Thanksgiving Day"}
    assert closure_payload(conn, D)["closed"] is True and closure_payload(conn, "2026-12-25")["closed"] is True


def test_a_re_added_date_is_closed_again(conn):
    seed(conn)
    refresh_h(conn, LIST_1, T_A)
    refresh_h(conn, LIST_NO_D, T_B)
    assert refresh_h(conn, LIST_1, T_C) == {"stored": 1, "unchanged": 3, "error": 0}
    assert closure_payload(conn, D) == {"closed": True, "trading_hours": "", "name": "Surprise Closure"}
    assert votes(conn, as_of="2026-11-24T17:00:00Z")["authorities_closed"] == [G_FIN]


def test_an_empty_list_is_refused_whole_and_old_closures_stay_as_they_were(conn):
    seed(conn)
    refresh_h(conn, LIST_1, T_A)
    got = refresh_h(conn, holiday_body(), T_B)
    assert got["error"] == 1 and got["stored"] == 0 and got["unchanged"] == 0
    assert closure_payload(conn, D)["closed"] is True and "removed" not in closure_payload(conn, D)
    assert facts_as_of(fin(conn), "2099-01-01T00:00:00Z", domain="K3_CAL", entity_key="XNYS:CLOSURE:2026-11-26")[0][
        "known_at_utc"] == "2026-11-23T20:00:00.000000Z"            # no new version of anything


def test_a_removed_half_day_is_also_marked_removed(conn):
    seed(conn)
    refresh_h(conn, holiday_body(("Half", "2026-11-27", "09:30-13:00"), ("A", "2026-11-26", ""), ("B", "2026-12-25", "")), T_A)
    refresh_h(conn, holiday_body(("A", "2026-11-26", ""), ("B", "2026-12-25", "")), T_B)
    assert closure_payload(conn, "2026-11-27")["removed"] is True


def test_a_refused_body_adds_no_tombstones(conn):
    seed(conn)
    refresh_h(conn, LIST_1, T_A)
    bad = holiday_body(("Thanksgiving Day", "2026-11-26", ""), ("x", "nope", ""))
    assert refresh_h(conn, bad, T_B)["error"] == 1
    assert closure_payload(conn, D)["closed"] is True


def test_only_the_holiday_source_gets_tombstones(conn):
    seed(conn)
    seed_status(conn, holiday="Surprise Closure", t=unix(2026, 11, 24, 13, 0), fetched="2026-11-24T13:30:00.000000Z")
    seed_fr(conn, mourning_doc("2026-1", "November 24, 2026", "2026-11-21"))
    k3.refresh(conn, FR, http_get=Fake({FR_URL: fr_body()}), now=T_C)          # an empty FR answer removes nothing
    assert votes(conn, as_of="2026-11-24T17:00:00Z")["authorities_closed"] == [G_FIN, G_PRES]


# ---- the command line

def run(args, capsys):
    code = k3._main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_register_schedule_votes_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    fdb = str(tmp_path / "cli-finnhub.db")
    code, out, _ = run(["register", "--store", db, "--finnhub-store", fdb, "--only", f"{X},{H}", "--terms-read-at", T0], capsys)
    assert code == 0
    code, out, _ = run(["schedule", "--store", db, "--from", "2026-11-23", "--to", "2026-11-30"], capsys)
    assert code == 0 and json.loads(out)["stored"] == 8
    code, out, _ = run(["votes", "--store", db, "--finnhub-store", fdb, "--date", "2026-11-24",
                        "--as-of", "2099-01-01T00:00:00Z"], capsys)
    v = json.loads(out)
    assert code == 0 and v["calendar_open"] is True and v["authorities_closed"] == []
    code, out, _ = run(["votes", "--store", db, "--finnhub-store", fdb, "--date", "2026-11-26",
                        "--as-of", "2099-01-01T00:00:00Z"], capsys)
    assert code == 0 and json.loads(out)["calendar_closed"] is True


def test_cli_votes_exit_one_when_a_closure_is_suspected(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    fdb = str(tmp_path / "cli-finnhub.db")
    run(["register", "--store", db, "--finnhub-store", fdb, "--only", f"{X},{H}", "--terms-read-at", T0], capsys)
    run(["schedule", "--store", db, "--from", "2026-11-23", "--to", "2026-11-30"], capsys)
    c = connect(fdb, role="finnhub")
    try:
        for f in parse_h(holiday_body(("Surprise Closure", D, "")), fetched=LATE):
            add_fact(c, **f)
    finally:
        c.close()
    code, out, _ = run(["votes", "--store", db, "--finnhub-store", fdb, "--date", D, "--as-of", "2099-01-01T00:00:00Z"], capsys)
    assert code == 1 and json.loads(out)["suspected"] is True and json.loads(out)["finnhub_store_given"] is True
    # the opposite: without the Finnhub store the same question has no Finnhub vote, and the answer says so
    code, out, _ = run(["votes", "--store", db, "--date", D, "--as-of", "2099-01-01T00:00:00Z"], capsys)
    assert code == 0 and json.loads(out)["suspected"] is False and json.loads(out)["finnhub_store_given"] is False


def test_cli_votes_exit_one_when_the_schedule_is_missing(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    run(["register", "--store", db, "--only", X, "--terms-read-at", T0], capsys)
    code, out, _ = run(["votes", "--store", db, "--date", D, "--as-of", "2099-01-01T00:00:00Z"], capsys)
    assert code == 1 and json.loads(out)["schedule_known"] is False


def test_cli_exit_two_when_the_command_failed(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert run(["register", "--store", db, "--only", "no_such", "--terms-read-at", T0], capsys)[0] == 2
    assert run(["schedule", "--store", db, "--from", "2026-12-01", "--to", "2026-01-01"], capsys)[0] == 2
    assert run(["votes", "--store", db, "--date", "not-a-date"], capsys)[0] == 2
    assert run(["refresh", "--store", db, "--source", "no_such"], capsys)[0] == 2
    bad = tmp_path / "notastore.db"
    bad.write_bytes(b"this is not a database at all" * 100)
    assert run(["votes", "--store", str(bad), "--date", D], capsys)[0] == 2


def test_cli_refresh_uses_the_clis_own_client_and_reports_counts(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "cli.db")
    run(["register", "--store", db, "--only", FR, "--terms-read-at", T0], capsys)
    monkeypatch.setattr(k3, "_urllib_get", Fake({FR_URL: fr_body(fr_doc())}))
    code, out, _ = run(["refresh", "--store", db, "--source", FR], capsys)
    assert code == 0 and json.loads(out) == {"stored": 1, "unchanged": 0, "error": 0}
    code, out, _ = run(["refresh", "--store", db, "--source", FR], capsys)
    assert code == 0 and json.loads(out)["unchanged"] == 1


def test_cli_refresh_exits_one_when_a_body_is_refused(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "cli.db")
    run(["register", "--store", db, "--only", FR, "--terms-read-at", T0], capsys)
    monkeypatch.setattr(k3, "_urllib_get", Fake({FR_URL: b"<html>gateway error</html>"}))
    code, out, _ = run(["refresh", "--store", db, "--source", FR], capsys)
    assert code == 1 and json.loads(out)["error"] == 1


def test_cli_never_prints_the_token(tmp_path, capsys, monkeypatch):
    from valedia_trader.core import secrets
    monkeypatch.setattr(secrets, "secret", lambda name, *a, **k: TOKEN)
    db = str(tmp_path / "cli.db")
    fdb = str(tmp_path / "cli-finnhub.db")
    run(["register", "--store", db, "--finnhub-store", fdb, "--only", H, "--terms-read-at", T0], capsys)
    monkeypatch.setattr(k3, "_urllib_get", Fake({H_URL: RuntimeError(f"failed with {TOKEN}")}))
    code, out, err = run(["refresh", "--store", db, "--finnhub-store", fdb, "--source", H], capsys)
    assert code == 1 and TOKEN not in out and TOKEN not in err


def test_a_revised_fr_document_is_known_at_its_fetch_not_back_at_its_publication(conn):
    """Lead fix: only an entity's first version may be PUBLISHED; a later edit is FETCHED (no lookahead)."""
    register(conn)
    first = datetime(2026, 11, 10, 18, 0, 0, tzinfo=timezone.utc)
    second = datetime(2026, 11, 12, 18, 0, 0, tzinfo=timezone.utc)
    k3.refresh(conn, FR, http_get=Fake({FR_URL: fr_body(fr_doc(num="2026-8", pub="2026-11-02"))}), now=first)
    k3.refresh(conn, FR, http_get=Fake({FR_URL: fr_body(fr_doc(num="2026-8", pub="2026-11-02", abstract="Edited."))}),
               now=second)
    def abstract_at(t):
        rows = [r for r in facts_as_of(conn, t, domain="K3_CAL") if r["entity_key"] == "FR:2026-8"]
        return [json.loads(r["payload_json"])["abstract"] for r in rows]
    assert abstract_at("2026-11-05T00:00:00Z") == [None]           # the first version stays PUBLISHED
    assert abstract_at("2026-11-11T00:00:00Z") == [None]           # the edit is not visible before it was fetched
    assert abstract_at("2026-11-12T19:00:00Z") == ["Edited."]


@pytest.mark.parametrize("abstract", [
    "There shall be no National Day of Mourning on December 24, 2025.",
    "I decline to designate December 24, 2025, as a National Day of Mourning.",
    "I won't designate December 24, 2025, as a National Day of Mourning.",
    "I never declared December 24, 2025, as a National Day of Mourning.",
    "Withdrawing the designation of December 24, 2025, as a National Day of Mourning.",
    "I hereby designate December 24, 2025, as a National Day of Mourning in the State of Texas.",
    "In 1963 the President designated November 25, 1963, as a National Day of Mourning.",
])
def test_negated_partial_or_historical_mourning_sentences_cast_no_vote(abstract):
    assert k3.fr_closure_dates("Proclamation text", abstract, "Proclamation") == []


def test_a_plain_mourning_designation_still_votes_beside_the_new_void_words():
    a = "I hereby designate December 24, 2025, as a National Day of Mourning."
    assert k3.fr_closure_dates("Proclamation text", a, "Proclamation") == ["2025-12-24"]


def test_a_nationwide_mourning_designation_with_united_states_still_votes():
    a = "I hereby declare December 5, 2018, as a National Day of Mourning throughout the United States."
    assert k3.fr_closure_dates("Proclamation text", a, "Proclamation") == ["2018-12-05"]


# ---- T63: the date of a death proclamation sits in its body

FIXTURES = Path(__file__).parent / "fixtures"
BODY_9830 = (FIXTURES / "fr_proc_9830.txt").read_text()
BODY_10876 = (FIXTURES / "fr_proc_10876.txt").read_text()
BODY_URL = "https://www.federalregister.gov/documents/full_text/text/2025/01/03/2024-31762.txt"
TUESDAY = "2026-11-24"
MOURNING_BODY = ("<html><body><pre>It is my solemn duty to announce the death of a President on November 20, 2026. "
                 "I do further appoint November 24, 2026, as a National Day of Mourning throughout the United States. "
                 "The flag shall be flown at half-staff until sunset on December 3, 2026.</pre></body></html>")


def death_doc(num="2026-31762", url=BODY_URL, title="Announcing the Death of A Former President", pub="2026-11-22",
              abstract=None, subtype="Proclamation"):
    doc = fr_doc(num=num, title=title, subtype=subtype, pub=pub, abstract=abstract)
    if url is not None:
        doc["raw_text_url"] = url
    return doc


def fr_payload(conn, number):
    rows = facts_as_of(conn, "2027-06-01T00:00:00Z", domain="K3_CAL")
    (row,) = [r for r in rows if r["entity_key"] == f"FR:{number}"]
    return json.loads(row["payload_json"]), row["payload_json"]


def test_fr_body_dates_of_the_two_real_proclamations():
    assert k3.fr_body_closure_dates(BODY_9830) == ["2018-12-05"]
    assert k3.fr_body_closure_dates(BODY_10876) == ["2025-01-09"]


def test_fr_body_dates_strip_tags_and_collapse_whitespace():
    assert k3.fr_body_closure_dates(MOURNING_BODY) == [TUESDAY]
    assert k3.fr_body_closure_dates("<p>I appoint\n  November 24, 2026,\n as a <b>National</b>\n Day of Mourning.</p>") == [TUESDAY]


@pytest.mark.parametrize("body", [
    "I do further appoint November 24, 2026, as a National Day of Mourning only for the Capitol region.",
    "I do not appoint November 24, 2026, as a National Day of Mourning.",
    "I do further appoint November 24, 2026, as a National Day of Mourning. A National Day of Mourning is not declared "
    "for December 1, 2026.",                           # one unsure trigger sentence voids the body
    "I appoint November 24, as a National Day of Mourning.",                  # no year
    "",
    "The President died on November 20, 2026. The flag is at half-staff until December 3, 2026.",   # no trigger sentence
])
def test_fr_body_dates_are_empty_when_unsure_or_no_trigger(body):
    assert k3.fr_body_closure_dates(body) == []


def test_fr_body_non_trigger_sentences_never_void_or_vote():
    body = ("The thirty-ninth President died on December 29, 2024, and the flag stays at half-staff through January 9, "
            "2025. I do further appoint January 9, 2025, as a National Day of Mourning throughout the United States.")
    assert k3.fr_body_closure_dates(body) == ["2025-01-09"]                      # the death and flag sentences are ignored
    only_death = "The President died on January 9, 2025. Flags fly at half-staff on January 9, 2025."
    assert k3.fr_body_closure_dates(only_death) == []                          # ... and they never vote on their own


def test_fr_body_dates_treat_control_characters_as_whitespace():
    assert "\x00" in BODY_9830                                       # the real raw text has them
    assert k3.fr_body_closure_dates("I appoint\x00November 24, 2026,\x00as a National Day of\x00Mourning.") == [TUESDAY]


def test_fr_body_dates_refuse_a_non_text_body():
    for bad in (None, b"x", 5):
        with pytest.raises(k3.K3Error):
            k3.fr_body_closure_dates(bad)


def test_the_fr_request_asks_for_the_raw_text_url():
    fake = Fake({FR_URL: fr_body()})
    k3.fetch(FR, http_get=fake, now=NOW)
    assert "raw_text_url" in parse_qs(urlsplit(fake.calls[0][0]).query)["fields[]"]


def test_refresh_reads_the_body_of_each_real_death_proclamation(conn):
    register(conn)
    fake = Fake({FR_URL: fr_body(
        death_doc("2018-26612", url=BODY_URL + "?a", title="Announcing the Death of George Herbert Walker Bush", pub="2018-12-03"),
        death_doc("2024-31762", url=BODY_URL + "?b", title="Announcing the Death of James Earl Carter, Jr.", pub="2025-01-03")),
        BODY_URL + "?a": BODY_9830.encode(), BODY_URL + "?b": BODY_10876.encode()})
    assert k3.refresh(conn, FR, http_get=fake, now=NOW) == {"stored": 2, "unchanged": 0, "error": 0}
    a, _ = fr_payload(conn, "2018-26612")
    b, _ = fr_payload(conn, "2024-31762")
    assert a["closure_dates"] == ["2018-12-05"] and b["closure_dates"] == ["2025-01-09"]
    assert a["body_read"] is True and b["body_sha256"] == hashlib.sha256(BODY_10876.encode()).hexdigest()
    assert len(fake.calls) == 3                                               # one list request, one body each
    for url, headers in fake.calls[1:]:
        assert headers["User-Agent"] == "valedia-client/1.0"
    assert k3.refresh(conn, FR, http_get=fake, now=NOW + timedelta(hours=1)) == {"stored": 0, "unchanged": 2, "error": 0}


def test_a_non_death_proclamation_never_triggers_a_body_fetch(conn):
    register(conn)
    docs = (death_doc("2026-1", title="Thanksgiving Day, 2026"),                           # a proclamation, wrong title
            death_doc("2026-2", title="Announcing the Death of A Former President", subtype="Executive Order"),
            closing_doc("2026-3", "December 24, 2026", "2026-11-20"))
    fake = Fake({FR_URL: fr_body(*docs)})
    assert k3.refresh(conn, FR, http_get=fake, now=NOW) == {"stored": 3, "unchanged": 0, "error": 0}
    assert [c[0].split("?")[0] for c in fake.calls] == [FR_URL]                   # the list only: no body request
    for number in ("2026-1", "2026-2", "2026-3"):
        assert "body_read" not in fr_payload(conn, number)[0]
    # the two-way check: a death proclamation does trigger one
    fake2 = Fake({FR_URL: fr_body(death_doc("2026-4")), BODY_URL: MOURNING_BODY.encode()})
    k3.refresh(conn, FR, http_get=fake2, now=NOW)
    assert len(fake2.calls) == 2 and fake2.calls[1][0] == BODY_URL


def test_a_title_naming_the_day_of_mourning_also_reads_the_body(conn):
    register(conn)
    fake = Fake({FR_URL: fr_body(death_doc("2026-5", title="National Day of Mourning for A Former President")),
                 BODY_URL: MOURNING_BODY.encode()})
    k3.refresh(conn, FR, http_get=fake, now=NOW)
    assert fr_payload(conn, "2026-5")[0]["closure_dates"] == [TUESDAY]


@pytest.mark.parametrize("url", [
    "https://example.com/full_text.txt", "http://www.federalregister.gov/full_text.txt",
    "https://www.federalregister.gov.evil.example/full_text.txt", "https://evil.example@www.federalregister.gov/x",
    "https://www.federalregister.gov:8443/x", "https://federalregister.gov/x", "ftp://www.federalregister.gov/x", None])
def test_a_body_url_on_another_host_is_refused_and_not_requested(conn, url):
    register(conn)
    fake = Fake({FR_URL: fr_body(death_doc("2026-6", url=url)), "": MOURNING_BODY.encode()})
    got = k3.refresh(conn, FR, http_get=fake, now=NOW)
    assert got["error"] == 1 and got["stored"] == 1
    assert [c[0].split("?")[0] for c in fake.calls] == [FR_URL]                   # the bad address was never fetched
    payload, _ = fr_payload(conn, "2026-6")
    assert payload["closure_dates"] == [] and payload["body_read"] is False


def test_a_failed_body_fetch_keeps_only_the_abstracts_own_vote_and_counts_one_error(conn):
    register(conn)
    for i, answer in enumerate((RuntimeError("reset"), b"", b"x" * (k3._MAX_BODY + 1), b"\xff\xfe")):
        number = f"2026-7{i}"
        fake = Fake({FR_URL: fr_body(death_doc(number, abstract="This proclamation designates November 24, 2026, "
                                                                 "as a National Day of Mourning.")), BODY_URL: answer})
        got = k3.refresh(conn, FR, http_get=fake, now=NOW)
        assert got["error"] == 1 and got["stored"] == 1
        payload, _ = fr_payload(conn, number)
        assert payload["closure_dates"] == ["2026-11-24"] and payload["body_read"] is False and payload["body_sha256"] is None
    # the two-way check: with an empty abstract, a failed body read leaves no vote at all
    fake = Fake({FR_URL: fr_body(death_doc("2026-79", abstract=None)), BODY_URL: RuntimeError("reset")})
    got = k3.refresh(conn, FR, http_get=fake, now=NOW)
    assert got["error"] == 1 and fr_payload(conn, "2026-79")[0]["closure_dates"] == []


def test_a_failed_re_read_after_a_good_read_keeps_the_good_vote_and_stores_nothing(conn):
    register(conn)
    good = Fake({FR_URL: fr_body(death_doc("2026-90", abstract=None)), BODY_URL: MOURNING_BODY.encode()})
    assert k3.refresh(conn, FR, http_get=good, now=NOW)["stored"] == 1
    later = NOW + timedelta(hours=1)
    bad = Fake({FR_URL: fr_body(death_doc("2026-90", abstract=None)), BODY_URL: RuntimeError("timeout")})
    got = k3.refresh(conn, FR, http_get=bad, now=later)
    assert got["error"] == 1 and got["stored"] == 0
    payload, _ = fr_payload(conn, "2026-90")
    assert payload["closure_dates"] == [TUESDAY] and payload["body_read"] is True
    n = conn.execute("SELECT COUNT(*) FROM knowledge_facts WHERE entity_key = 'FR:2026-90'").fetchone()[0]
    assert n == 1
    # the two-way check: a changed abstract is new information, so the failed read is stored, with only its own dates
    changed = Fake({FR_URL: fr_body(death_doc("2026-90", abstract="Correction to a proclamation.")),
                    BODY_URL: RuntimeError("timeout")})
    got = k3.refresh(conn, FR, http_get=changed, now=later + timedelta(hours=1))
    assert got["error"] == 1 and got["stored"] == 1
    payload, _ = fr_payload(conn, "2026-90")
    assert payload["closure_dates"] == [] and payload["body_read"] is False


def test_the_body_is_read_together_with_the_abstract_and_one_unsure_sentence_voids_all(conn):
    register(conn)
    abstract = "This proclamation designates November 24, 2026, as a National Day of Mourning."
    fake = Fake({FR_URL: fr_body(death_doc("2026-8", abstract=abstract)), BODY_URL: MOURNING_BODY.encode()})
    k3.refresh(conn, FR, http_get=fake, now=NOW)
    assert fr_payload(conn, "2026-8")[0]["closure_dates"] == [TUESDAY]
    unsure = MOURNING_BODY.replace("The flag", "A National Day of Mourning is not set for December 1, 2026. The flag")
    fake = Fake({FR_URL: fr_body(death_doc("2026-9", abstract=abstract)), BODY_URL: unsure.encode()})
    k3.refresh(conn, FR, http_get=fake, now=NOW)
    payload, _ = fr_payload(conn, "2026-9")
    assert payload["closure_dates"] == [] and payload["body_read"] is True           # read, unsure: no vote, no error
    unsure_abstract = "This proclamation designates November 24, 2026, as a National Day of Mourning, not on a Sunday."
    fake = Fake({FR_URL: fr_body(death_doc("2026-10", abstract=unsure_abstract)), BODY_URL: MOURNING_BODY.encode()})
    k3.refresh(conn, FR, http_get=fake, now=NOW)
    assert fr_payload(conn, "2026-10")[0]["closure_dates"] == []                    # an unsure abstract voids the body's date


def test_finnhub_plus_a_death_proclamation_with_an_empty_abstract_is_an_ordinary_stop(conn, tmp_path):
    register(conn)
    k3.refresh(conn, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-26"})
    holidays = holiday_body(("Surprise Closure", TUESDAY, ""))
    k3.refresh(fin(conn), H, http_get=Fake({H_URL: holidays}), now=NOW, credentials=creds)
    fake = Fake({FR_URL: fr_body(death_doc("2026-11", abstract=None)), BODY_URL: MOURNING_BODY.encode()})
    assert k3.refresh(conn, FR, http_get=fake, now=NOW) == {"stored": 1, "unchanged": 0, "error": 0}
    v = k3.closure_votes(conn, TUESDAY, "2026-11-24T12:00:00.000000Z", finnhub_conn=fin(conn))
    assert v["authorities_closed"] == [G_FIN, G_PRES] and v["ordinary_stop"] is True
    # the two-way check: with the body unreadable, the Federal Register casts no vote
    conn2 = connect(tmp_path / "k2.db")
    _FIN[id(conn2)] = connect(tmp_path / "k2-finnhub.db", role="finnhub")
    try:
        register(conn2)
        k3.refresh(conn2, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-26"})
        k3.refresh(fin(conn2), H, http_get=Fake({H_URL: holidays}), now=NOW, credentials=creds)
        broken = Fake({FR_URL: fr_body(death_doc("2026-11", abstract=None)), BODY_URL: RuntimeError("down")})
        assert k3.refresh(conn2, FR, http_get=broken, now=NOW)["error"] == 1
        v2 = k3.closure_votes(conn2, TUESDAY, "2026-11-24T12:00:00.000000Z", finnhub_conn=fin(conn2))
        assert v2["authorities_closed"] == [G_FIN] and v2["ordinary_stop"] is False and v2["suspected"] is True
    finally:
        _FIN.pop(id(conn2)).close()
        conn2.close()


def test_no_body_text_is_stored(conn):
    register(conn)
    fake = Fake({FR_URL: fr_body(death_doc("2026-12")), BODY_URL: MOURNING_BODY.encode()})
    k3.refresh(conn, FR, http_get=fake, now=NOW)
    payload, text = fr_payload(conn, "2026-12")
    assert payload["body_sha256"] == hashlib.sha256(MOURNING_BODY.encode()).hexdigest()
    assert "solemn duty" not in text and "half-staff" not in text and "<pre>" not in text
    assert max(len(str(v)) for v in payload.values()) < 300                       # no field holds a body
    dump = "\n".join(str(tuple(r)) for r in conn.execute("SELECT * FROM knowledge_facts"))
    assert "solemn duty" not in dump and "half-staff" not in dump


# ---- W3-WH: the White House "Presidential Actions" feed (an AUTHORITY in the group US_PRESIDENT)
#
# WH_FEED is the real feed sample (fetched 2026-11-23: the channel header and the first 3 of 30 items), with each
# `content:encoded` body cut to its first paragraph. The live feed holds NO mourning item. So the mourning items below are a
# recorded item SHAPE (the sample's Proclamation item) carrying composed text: the title and sentences come from the
# Federal Register mourning tests above, with a placeholder name ("Person A"). No White House mourning post was observed.

WH_URL = "https://www.whitehouse.gov/presidential-actions/feed/"
WH_ACCEPT = "application/rss+xml, application/xml, text/xml"
WH_FEED = (FIXTURES / "whitehouse_presidential_actions.xml").read_bytes()
_WH_TEXT = WH_FEED.decode("utf-8")
_WH_ITEMS = re.findall(r"<item>.*?</item>", _WH_TEXT, re.S)
_WH_HEAD = _WH_TEXT[:_WH_TEXT.index("<item>")]
_WH_TAIL = _WH_TEXT[_WH_TEXT.rindex("</item>") + len("</item>"):]
_WH_TEMPLATE = _WH_ITEMS[1]                       # the sample's Proclamation item: the shape every composed item copies
GUID1, GUID2, GUID3 = (f"https://www.whitehouse.gov/?p={n}" for n in (55161, 55072, 55074))
PUB_OK = "Mon, 23 Nov 2026 15:00:00 +0000"        # known well before NOW (18:00 UTC)
DEATH_P = ("It is my solemn duty to announce officially the death of Person A, a former President of the United "
           "States, on November 20, 2026.")
MOURN_S = "I do further appoint {d}, as a National Day of Mourning throughout the United States."
HALF_STAFF = "The flag shall be flown at half-staff until sunset on December 3, 2026."


def wh_item(title="Announcing the Death of Person A", kind="Proclamations", n=70001, pub=PUB_OK,
            description="<p>An excerpt of the post.</p>", body=("A paragraph of the post.",), cats=None):
    """One item in the sample's own shape: the same tags, CDATA wrappers and attributes, with new values."""
    cats = ["Presidential Actions", kind] if cats is None else cats
    item = _WH_TEMPLATE
    item = re.sub(r"<title>.*?</title>", lambda m: f"<title>{xml_escape(title)}</title>", item, count=1, flags=re.S)
    item = re.sub(r"<link>.*?</link>", lambda m: f"<link>https://www.whitehouse.gov/presidential-actions/2026/11/"
                  f"person-a-{n}/</link>", item, count=1, flags=re.S)
    item = re.sub(r"<pubDate>.*?</pubDate>", lambda m: f"<pubDate>{pub}</pubDate>", item, count=1, flags=re.S)
    item = re.sub(r"(?:\s*<category>.*?</category>)+",
                  lambda m: "".join(f"\n\t\t<category><![CDATA[{c}]]></category>" for c in cats), item, count=1,
                  flags=re.S)
    item = re.sub(r'(<guid isPermaLink="false">).*?(</guid>)', lambda m: f"{m.group(1)}https://www.whitehouse.gov/?p={n}"
                  f"{m.group(2)}", item, count=1, flags=re.S)
    item = re.sub(r"(<description><!\[CDATA\[).*?(\]\]></description>)",
                  lambda m: f"{m.group(1)}{description}\n{m.group(2)}", item, count=1, flags=re.S)
    paras = "\n".join(f'<p class="wp-block-paragraph">{p}</p>' for p in body)
    item = re.sub(r'(<content:encoded><!\[CDATA\[.*?)<p class="wp-block-paragraph">.*?(\]\]></content:encoded>)',
                  lambda m: f"{m.group(1)}{paras}\n{m.group(2)}", item, count=1, flags=re.S)
    return item


def wh_feed(*items, head=_WH_HEAD):
    return (head + "\n".join(items) + _WH_TAIL).encode("utf-8")


def wh_mourning(day="November 24, 2026", n=70001, pub=PUB_OK, **over):
    """A Proclamation announcing a death, the date in the body. The item shape is recorded (it is the sample's Proclamation
    item); the text is composed (the Federal Register mourning sentences above, with the placeholder name "Person A");
    no White House mourning post was observed in the live feed."""
    kw = dict(body=(DEATH_P, MOURN_S.format(d=day) + " " + HALF_STAFF), n=n, pub=pub,
              description=f"<p>TO THE PEOPLE OF THE UNITED STATES: {DEATH_P} [&#8230;]</p>")
    kw.update(over)
    return wh_item(**kw)


def parse_wh(body, fetched=NOW_S):
    return k3.parse_whitehouse_feed(body, fetched_at_utc=fetched)


def wh_payload(body, i=0, fetched=NOW_S):
    return json.loads(parse_wh(body, fetched)[i]["payload_json"])


def seed_wh(conn, *items, fetched=LATE):
    put(conn, parse_wh(wh_feed(*items), fetched=fetched))


# -- the source entry

def test_the_whitehouse_source_entry():
    e = k3.SOURCES[WH]
    assert e["source_id"] == WH and e["role"] == "AUTHORITY" and e["endpoint"] == WH_URL
    assert e["authority_group"] == G_PRES == k3.SOURCES[FR]["authority_group"]
    assert e["terms_url"] == "https://www.whitehouse.gov/copyright/" and e["terms_url"].startswith("https://")
    assert e["licence_tag"] == "US-PUBLIC-DOMAIN" and e["allow_external_llm"] == 1
    assert e["ua_policy"] == "GENERIC_CLIENT" and e["cost_usd_monthly"] == 0 and e["time_zone"] == "America/New_York"
    assert k3._WH == WH and k3._UA == "valedia-client/1.0"


def test_the_whitehouse_source_registers_like_the_others(conn):
    register(conn, WH)
    row = conn.execute("SELECT * FROM source_registry WHERE source_id = ?", (WH,)).fetchone()
    assert row is not None
    with pytest.raises(k3.K3Error):
        k3.register_all(conn, T0, only=["whitehouse_nonsense"])


def test_the_docstring_says_the_whitehouse_feed_is_built():
    doc = k3.__doc__
    assert "Presidential Actions" in doc and "same day" in doc
    assert "NOT built yet" in doc                                   # the exchange's own notice still is not


# -- the real sample

def test_the_fixture_is_the_recorded_sample_shape():
    assert len(_WH_ITEMS) == 3 and _WH_TEXT.count("<![CDATA[") >= 12
    for tag in ("dc:creator", "content:encoded", 'guid isPermaLink="false"', "pubDate", "category"):
        assert tag in _WH_TEXT
    assert "<!DOCTYPE" not in _WH_TEXT and "<!ENTITY" not in _WH_TEXT
    assert "National Day of Mourning" not in _WH_TEXT               # the live feed holds no mourning item


def test_the_real_sample_parses_to_three_facts():
    facts = parse_wh(WH_FEED)
    assert [f["entity_key"] for f in facts] == [f"WH:{g}" for g in (GUID1, GUID2, GUID3)]
    assert {f["source_id"] for f in facts} == {WH} and {f["domain"] for f in facts} == {"K3_CAL"}
    assert all(f["fetched_at_utc"] == NOW_S and f["effective_to_utc"] is None for f in facts)
    # the start of the New York day of the pubDate: 23 Nov 23:36Z is 23 Nov in New York; 21 Nov 02:31Z is still 20 Nov
    assert facts[0]["effective_from_utc"] == "2026-11-23T05:00:00.000000Z"
    assert facts[1]["effective_from_utc"] == facts[2]["effective_from_utc"] == "2026-11-20T05:00:00.000000Z"


def test_the_real_sample_payloads():
    p1, p2, p3 = (wh_payload(WH_FEED, i) for i in range(3))
    assert p1["guid"] == GUID1 and p1["categories"] == ["Presidential Actions", "Presidential Memoranda"]
    assert p2["categories"] == ["Presidential Actions", "Proclamations"] and p2["type"] == "Proclamation"
    assert p3["categories"] == ["Presidential Actions", "Executive Orders"] and p3["type"] == "Executive Order"
    assert p1["type"] is None                                       # a memorandum is not a voting kind
    assert p3["title"] == "PROTECTING THE EXECUTIVE’S PRIVILEGES AND ENHANCING EFFICIENCIES IN FEDERAL CONTRACTING"
    assert p1["link"].startswith("https://www.whitehouse.gov/presidential-actions/2026/11/expeditious-enforcement")
    assert p1["pub_date_utc"] == "2026-11-23T23:36:12.000000Z"
    for p in (p1, p2, p3):
        assert p["closure_dates"] == [] and p["closing_order_dates"] == []
        assert re.fullmatch(r"[0-9a-f]{64}", p["description_sha256"]) and re.fullmatch(r"[0-9a-f]{64}", p["content_sha256"])
        assert p["description_sha256"] != p["content_sha256"]


def test_no_body_text_is_stored_for_the_whitehouse_feed(conn):
    register(conn, WH)
    fake = Fake({WH_URL: WH_FEED})
    assert k3.refresh(conn, WH, http_get=fake, now=NOW) == {"stored": 3, "unchanged": 0, "error": 0}
    dump = "\n".join(str(tuple(r)) for r in conn.execute("SELECT * FROM knowledge_facts"))
    for words in ("By the authority vested", "From the moment a child", "gift from God", "wp-block", "<p"):
        assert words not in dump
    for f in parse_wh(WH_FEED):
        assert max(len(str(v)) for v in json.loads(f["payload_json"]).values()) < 400


def test_the_hashes_are_of_the_description_and_the_body_text():
    import xml.etree.ElementTree as ET
    item = ET.fromstring(WH_FEED).find("channel/item")
    d = item.find("description").text
    c = item.find("{http://purl.org/rss/1.0/modules/content/}encoded").text
    p = wh_payload(WH_FEED)
    assert p["description_sha256"] == sha(d) and p["content_sha256"] == sha(c)


# -- loud failures, and their opposites

def drop(tag, which=0, feed=_WH_TEXT):
    """The sample with one tag of one item removed."""
    parts = re.split(r"(?=<item>)", feed)
    parts[which + 1] = re.sub(rf"<{tag}[ >].*?</{tag}>", "", parts[which + 1], count=1, flags=re.S)
    return "".join(parts).encode("utf-8")


def _blank(tag_pattern, replacement, item):
    out = re.sub(tag_pattern, lambda m: replacement, item, count=1, flags=re.S)
    assert out != item
    return out


def test_an_item_with_an_empty_title_or_an_empty_body_is_refused_and_an_empty_description_is_not():
    base = wh_mourning()
    assert len(parse_wh(wh_feed(base))) == 1                                         # opposite: the item as it is parses
    empty_title = _blank(r"<title>.*?</title>", "<title></title>", base)
    blank_title = _blank(r"<title>.*?</title>", "<title>  </title>", base)
    empty_body = _blank(r"<content:encoded>.*?</content:encoded>", "<content:encoded><![CDATA[ ]]></content:encoded>", base)
    none_body = _blank(r"<content:encoded>.*?</content:encoded>", "<content:encoded></content:encoded>", base)
    for bad, what in ((empty_title, "title"), (blank_title, "title"), (empty_body, "content:encoded"),
                      (none_body, "content:encoded")):
        with pytest.raises(k3.K3Error, match=f"empty {what}"):
            parse_wh(wh_feed(bad))
    empty_desc = _blank(r"<description>.*?</description>", "<description><![CDATA[]]></description>", base)
    assert len(parse_wh(wh_feed(empty_desc))) == 1


def test_an_item_missing_its_pubdate_makes_the_whole_read_loud():
    assert len(parse_wh(WH_FEED)) == 3                              # opposite: the sample as it is parses
    with pytest.raises(k3.K3Error, match="pubDate"):
        parse_wh(drop("pubDate"))


@pytest.mark.parametrize("tag", ["title", "link", "guid", "description", "content:encoded"])
def test_an_item_missing_a_tag_is_loud(tag):
    for which in (0, 2):
        with pytest.raises(k3.K3Error):
            parse_wh(drop(tag, which))


@pytest.mark.parametrize("pub", ["", "garbage", "Mon, 23 Nov 2026 25:99:00 +0000", "Mon, 31 Feb 2026 10:00:00 +0000",
                                 "Mon, 23 Nov 2026 23:36:12", "2026-11-23T23:36:12Z"])
def test_a_bad_pubdate_is_loud(pub):
    with pytest.raises(k3.K3Error, match="pubDate"):
        parse_wh(wh_feed(wh_item(pub=pub)))


@pytest.mark.parametrize("pub", ["Mon, 23 Nov 2026 15:00:00 +0000", "Mon, 23 Nov 2026 15:00:00 GMT",
                                 "Mon, 23 Nov 2026 10:00:00 -0500"])
def test_the_rfc822_forms_with_a_zone_all_mean_the_same_instant(pub):
    (f,) = parse_wh(wh_feed(wh_item(pub=pub)))
    assert json.loads(f["payload_json"])["pub_date_utc"] == "2026-11-23T15:00:00.000000Z"


@pytest.mark.parametrize("guid", ["", "https://www.whitehouse.gov/?p=1 2", "x<y", "https://www.whitehouse.gov/?p=\"1\"",
                                  "a" * 300])
def test_an_unsafe_guid_is_loud(guid):
    bad = re.sub(r'(<guid isPermaLink="false">).*?(</guid>)', lambda m: f"{m.group(1)}{xml_escape(guid)}{m.group(2)}",
                 wh_item(n=70002), flags=re.S)
    with pytest.raises(k3.K3Error, match="guid"):
        parse_wh(wh_feed(bad))
    assert len(parse_wh(wh_feed(wh_item(n=70002)))) == 1            # opposite: a normal guid is fine


def test_a_repeated_guid_is_loud_and_distinct_guids_are_not():
    with pytest.raises(k3.K3Error, match="repeated"):
        parse_wh(wh_feed(wh_item(n=70003), wh_item(n=70003, title="Other")))
    assert len(parse_wh(wh_feed(wh_item(n=70003), wh_item(n=70004)))) == 2


def test_an_empty_channel_is_an_error_not_a_zero():
    with pytest.raises(k3.K3Error, match="empty"):
        parse_wh(wh_feed())
    assert len(parse_wh(wh_feed(wh_item()))) == 1


@pytest.mark.parametrize("body", [
    b"",
    b"<rss version=\"2.0\"><channel><title>x</title>",                                     # cut off
    b"not xml at all",
    b'{"data": []}',
    b"<html><head><title>Blocked</title></head><body><p>Access denied</p></body></html>",
    b"<!doctype html><html><body>Blocked</body></html>",
    b'<?xml version="1.0"?><rss version="2.0"></rss>',                                       # no channel
    b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>',      # not RSS
    b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>',    # no item tags at all
])
def test_a_body_that_is_not_the_feed_is_an_error(body):
    with pytest.raises(k3.K3Error):
        parse_wh(body)


@pytest.mark.parametrize("prefix", [
    b'<!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>',
    b'<!DOCTYPE rss>',
    b'<!doctype rss SYSTEM "http://example.invalid/x.dtd">',
    b'<!ENTITY x "y">',
])
def test_a_doctype_or_entity_is_refused_before_any_parsing(prefix):
    body = prefix + WH_FEED[WH_FEED.index(b"<rss"):]
    with pytest.raises(k3.K3Error, match="DOCTYPE|ENTITY|entity"):
        parse_wh(body)
    assert len(parse_wh(WH_FEED)) == 3                                                          # opposite: no DOCTYPE parses


_DTD_UTF = ('<!DOCTYPE rss [<!ENTITY a "PWNED">]>')


def _as_encoded(codec, doctype=True, entity_in_title=True):
    """The sample as one feed in another encoding, with an internal DTD whose entity is used in the first title."""
    text = _WH_TEXT
    if entity_in_title:
        text = text.replace("<title>", "<title>&a; ", 1)
    if doctype:
        text = text.replace("<rss", _DTD_UTF + "<rss", 1)
    return text.replace('encoding="UTF-8"', f'encoding="{codec}"', 1).encode(codec)


@pytest.mark.parametrize("codec", ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"])
def test_a_body_in_another_encoding_is_refused_so_a_doctype_cannot_hide_in_it(codec):
    body = _as_encoded(codec)
    assert b"<!DOCTYPE" not in body                                                  # the byte search alone cannot see it
    with pytest.raises(k3.K3Error, match="UTF-8|NUL"):
        parse_wh(body)
    # also with no XML declaration at all
    bare = (_DTD_UTF + _WH_TEXT[_WH_TEXT.index("<rss"):].replace("<title>", "<title>&a; ", 1)).encode(codec)
    with pytest.raises(k3.K3Error, match="UTF-8|NUL"):
        parse_wh(bare)
    # the same text in UTF-8 is refused for its DTD
    with pytest.raises(k3.K3Error, match="DOCTYPE|ENTITY"):
        parse_wh(_as_encoded("utf-8"))


def test_utf8_bodies_are_still_read_with_or_without_a_byte_order_mark_and_other_encodings_are_not():
    assert len(parse_wh(WH_FEED)) == 3
    assert len(parse_wh(b"\xef\xbb\xbf" + WH_FEED)) == 3                                # opposite: a UTF-8 mark is fine
    assert len(parse_wh(_as_encoded("utf-8", doctype=False, entity_in_title=False))) == 3
    with pytest.raises(k3.K3Error, match="UTF-8"):
        parse_wh(_WH_TEXT.encode("latin-1").replace(b"<title>", b"<title>\xe9", 1))   # not UTF-8 bytes
    with pytest.raises(k3.K3Error):
        parse_wh(_as_encoded("utf-16", doctype=False, entity_in_title=False))


def test_an_oversize_body_is_an_error_and_one_at_the_cap_is_not():
    with pytest.raises(k3.K3Error, match="larger|oversize|limit"):
        parse_wh(WH_FEED + b" " * k3._MAX_BODY)
    assert len(parse_wh(WH_FEED + b" " * (k3._MAX_BODY - len(WH_FEED)))) == 3


def test_the_parser_refuses_non_bytes_and_a_bad_fetch_time():
    with pytest.raises(k3.K3Error):
        parse_wh(WH_FEED.decode("utf-8"))
    with pytest.raises(k3.K3Error):
        parse_wh(WH_FEED, fetched="2026-11-23 18:00")
    with pytest.raises(k3.K3Error):
        parse_wh(None)


# -- when the fact is known

def test_a_pubdate_at_or_before_the_fetch_is_proven_and_known_then(conn):
    register(conn, WH)
    for pub in (PUB_OK, "Mon, 23 Nov 2026 18:00:00 +0000"):            # before the fetch, and at the very moment
        (f,) = parse_wh(wh_feed(wh_item(n=70010, pub=pub)))
        assert f["published_proven"] is True and f["published_at_utc"] == _fmt_pub(pub)
    put(conn, parse_wh(wh_feed(wh_item(n=70010))))
    assert len(facts_as_of(conn, "2026-11-23T15:00:00.000000Z", domain="K3_CAL")) == 1
    assert facts_as_of(conn, "2026-11-23T14:59:59.000000Z", domain="K3_CAL") == []


def _fmt_pub(pub):
    from email.utils import parsedate_to_datetime
    return k3._fmt(parsedate_to_datetime(pub))


def test_a_pubdate_after_the_fetch_is_known_at_the_fetch_and_not_proven(conn):
    register(conn, WH)
    later = "Mon, 23 Nov 2026 18:00:01 +0000"
    (f,) = parse_wh(wh_feed(wh_item(n=70011, pub=later)))
    assert f["published_proven"] is False and f["published_at_utc"] is None
    put(conn, [f])
    assert facts_as_of(conn, "2026-11-23T17:59:59.000000Z", domain="K3_CAL") == []
    assert len(facts_as_of(conn, NOW_S, domain="K3_CAL")) == 1
    (row,) = conn.execute("SELECT known_at_basis, known_at_utc FROM knowledge_facts").fetchall()
    assert tuple(row) == ("FETCHED", NOW_S)


def test_a_revised_item_is_fetched_never_back_dated_and_the_same_bytes_again_are_unchanged(conn):
    register(conn, WH)
    v1 = wh_feed(wh_item(n=70020, body=("The first text.",)))
    v2 = wh_feed(wh_item(n=70020, body=("The first text, revised.",)))
    assert k3.refresh(conn, WH, http_get=Fake({WH_URL: v1}), now=NOW) == {"stored": 1, "unchanged": 0, "error": 0}
    again = k3.refresh(conn, WH, http_get=Fake({WH_URL: v1}), now=NOW + timedelta(hours=1))
    assert again == {"stored": 0, "unchanged": 1, "error": 0}
    later = NOW + timedelta(hours=2)
    assert k3.refresh(conn, WH, http_get=Fake({WH_URL: v2}), now=later) == {"stored": 1, "unchanged": 0, "error": 0}
    rows = conn.execute("SELECT known_at_basis, known_at_utc, published_at_utc FROM knowledge_facts "
                        "ORDER BY fact_version_id").fetchall()
    assert [tuple(r)[:2] for r in rows] == [("PUBLISHED", "2026-11-23T15:00:00.000000Z"), ("FETCHED", k3._fmt(later))]
    assert [r[2] for r in rows] == ["2026-11-23T15:00:00.000000Z", None]               # the revision is not proven
    # the old version is still the one known between the publication and the revision's fetch
    (old,) = facts_as_of(conn, "2026-11-23T19:30:00.000000Z", domain="K3_CAL")
    (new,) = facts_as_of(conn, k3._fmt(later), domain="K3_CAL")
    assert old["raw_sha256"] != new["raw_sha256"]


# -- the vote text: type, sentences, change words

SENT_CLEAN = MOURN_S.format(d="November 24, 2026")


def votes_of(item):
    p = wh_payload(wh_feed(item))
    return p["closure_dates"], p["closing_order_dates"]


def test_a_proclamation_with_the_mourning_sentence_in_its_body_votes_that_date():
    assert votes_of(wh_mourning()) == (["2026-11-24"], [])
    # opposite: the death and half-staff sentences alone name no mourning day
    assert votes_of(wh_mourning(body=(DEATH_P, HALF_STAFF))) == ([], [])


def test_the_same_text_under_other_types_does_not_vote():
    assert votes_of(wh_mourning(kind="Presidential Memoranda")) == ([], [])
    assert votes_of(wh_mourning(cats=["Presidential Actions", "Nominations & Appointments"])) == ([], [])
    assert votes_of(wh_mourning(cats=["Presidential Actions"])) == ([], [])
    assert votes_of(wh_mourning(cats=[])) == ([], [])
    assert votes_of(wh_mourning(cats=["Presidential Actions", "Proclamations", "Presidential Memoranda"])) == ([], [])
    assert votes_of(wh_mourning(cats=["Presidential Actions", "Proclamations", "Executive Orders"])) == ([], [])
    # two voting kinds at once name no one kind: no type, whatever way a set of two would be ordered
    both = wh_mourning(cats=["Presidential Actions", "Proclamations", "Executive Orders"])
    assert wh_payload(wh_feed(both))["type"] is None
    assert wh_payload(wh_feed(wh_mourning(cats=["Presidential Actions", "Executive Orders", "Proclamations"])))["type"] is None
    assert wh_payload(wh_feed(wh_mourning(kind="Presidential Memoranda")))["type"] is None
    assert votes_of(wh_mourning(kind="Proclamations")) == (["2026-11-24"], [])                # opposite


def test_the_category_text_is_matched_without_regard_to_case_or_spaces():
    assert votes_of(wh_mourning(cats=["Presidential Actions", "  proclamations "])) == (["2026-11-24"], [])


def test_a_change_word_in_the_title_or_description_voids_the_item():
    for title in ("Amending the Announcement of the Death of Person A", "Revoking the Announcement of the Death of Person A",
                  "Postponing the National Day of Mourning for Person A"):
        assert votes_of(wh_mourning(title=title)) == ([], [])
    desc = "<p>This proclamation amends the earlier one. [&#8230;]</p>"
    assert votes_of(wh_mourning(description=desc)) == ([], [])
    assert votes_of(wh_mourning(description="<p>This proclamation announces a death. [&#8230;]</p>")) == (["2026-11-24"], [])


def test_an_executive_order_votes_only_from_its_title_and_description():
    t = "A National Day of Mourning on November 24, 2026"
    assert votes_of(wh_item(title=t, kind="Executive Orders")) == (["2026-11-24"], [])
    assert votes_of(wh_item(title="Amending the " + t, kind="Executive Orders")) == ([], [])
    desc = f"<p>{SENT_CLEAN}</p>"
    assert votes_of(wh_item(title="Remembering Person A", kind="Executive Orders", description=desc)) == (["2026-11-24"], [])
    # the body of an Executive Order is never read for a vote, even when its description names the mourning day
    other = "I do further appoint November 25, 2026, as a National Day of Mourning throughout the United States."
    assert votes_of(wh_item(title="Remembering Person A", kind="Executive Orders", description=desc,
                            body=(other,))) == (["2026-11-24"], [])
    assert votes_of(wh_item(title="Remembering Person A", kind="Executive Orders", body=(SENT_CLEAN,))) == ([], [])


def test_the_body_is_read_only_for_a_proclamation_that_announces_a_death_or_names_the_mourning_day():
    plain = dict(title="National Adoption Month, 2026", body=(SENT_CLEAN,), description="<p>A month of adoption.</p>")
    assert votes_of(wh_item(**plain)) == ([], [])                                           # not a death proclamation
    assert votes_of(wh_item(**{**plain, "title": "National Day of Mourning for Person A"})) == (["2026-11-24"], [])
    assert votes_of(wh_item(**{**plain, "description": f"<p>{SENT_CLEAN} [&#8230;]</p>"})) == (["2026-11-24"], [])


def test_a_closing_order_is_a_signal_only_and_casts_no_vote(conn):
    title = "Providing for the Closing of Executive Departments and Agencies of the Federal Government on November 24, 2026"
    item = wh_item(title=title, kind="Executive Orders", n=70030)
    assert votes_of(item) == ([], ["2026-11-24"])
    seed(conn)
    seed_wh(conn, item)
    v = votes(conn)
    assert v["authorities_closed"] == [] and v["sources_closed"] == [] and v["suspected"] is False
    seed_wh(conn, wh_mourning(n=70031))                                                      # opposite: a mourning item votes
    assert votes(conn)["authorities_closed"] == [G_PRES]


def test_a_past_dated_mention_does_not_vote():
    past = "In 2018, the President designated November 24, 2018, as a National Day of Mourning."
    assert votes_of(wh_mourning(body=(DEATH_P, past))) == ([], [])
    assert votes_of(wh_mourning(body=(DEATH_P, "I designate November 24, 2026, as a National Day of Mourning."))) == \
        (["2026-11-24"], [])


def test_one_unsure_sentence_voids_the_whole_item():
    unsure = "A National Day of Mourning is not set for December 1, 2026."
    assert votes_of(wh_mourning(body=(DEATH_P, SENT_CLEAN, unsure))) == ([], [])
    assert votes_of(wh_mourning(body=(DEATH_P, unsure, SENT_CLEAN))) == ([], [])
    assert votes_of(wh_mourning(body=(DEATH_P, SENT_CLEAN))) == (["2026-11-24"], [])           # opposite: the clear one alone
    clear_head = "A National Day of Mourning on November 24, 2026"                           # a clear title, an unsure body
    assert votes_of(wh_mourning(title=clear_head, body=(DEATH_P,))) == (["2026-11-24"], [])
    assert votes_of(wh_mourning(title=clear_head, body=(DEATH_P, unsure))) == ([], [])
    unsure_title = "National Day of Mourning for Person A through December 1, 2026"
    assert votes_of(wh_mourning(title=unsure_title)) == ([], [])
    unsure_desc = "<p>A National Day of Mourning is not set for December 1, 2026. [&#8230;]</p>"
    assert votes_of(wh_mourning(description=unsure_desc)) == ([], [])


def test_the_mourning_sentence_is_read_through_the_html_of_the_body():
    body = ("It is my duty.", "I do further appoint <strong>November&nbsp;24, 2026</strong>, as a <em>National</em> Day of "
            "Mourning throughout the United States.")
    assert votes_of(wh_mourning(body=body)) == (["2026-11-24"], [])


def test_the_posts_own_footer_line_is_not_read_as_a_sentence():
    footer = ('<p>The post <a href="https://www.whitehouse.gov/x/">A National Day of Mourning on November 24, 2026</a> '
              'appeared first on <a href="https://www.whitehouse.gov">The White House</a>.</p>')
    assert votes_of(wh_item(title="Remembering Person A", kind="Proclamations", description=footer)) == ([], [])


_WH_FOOTER_P = ('<p>The post <a href="https://www.whitehouse.gov/x/">A Title</a> appeared first on '
                '<a href="https://www.whitehouse.gov">The White House</a>.</p>')


def test_a_post_word_ahead_of_the_footer_cannot_hide_a_change_word_from_the_description():
    amended = "<p>The post of envoy is amended here. I do further appoint November 24, 2026, as a National Day of Mourning throughout the United States.</p>"
    assert votes_of(wh_mourning(description=amended + _WH_FOOTER_P)) == ([], [])
    assert votes_of(wh_mourning(description=amended)) == ([], [])                            # the same without a footer
    hyphen = "<p>The post-war order is revoked. " + MOURN_S.format(d="November 24, 2026") + "</p>"
    assert votes_of(wh_mourning(description=hyphen + _WH_FOOTER_P)) == ([], [])
    # opposite: the same text with no change word still votes, and the footer alone is still removed
    plain = "<p>The post of envoy is open. " + MOURN_S.format(d="November 24, 2026") + "</p>"
    assert votes_of(wh_mourning(description=plain + _WH_FOOTER_P)) == (["2026-11-24"], [])


def test_a_post_word_ahead_of_the_footer_cannot_delete_an_unsure_sentence_of_the_body():
    unsure = "The post is vacant. A National Day of Mourning on November 25."
    assert votes_of(wh_mourning(body=(MOURN_S.format(d="November 24, 2026"), unsure, _WH_FOOTER_P))) == ([], [])
    # opposite: with no unsure sentence, the same body votes, footer and all
    ok = "The post is vacant."
    assert votes_of(wh_mourning(body=(MOURN_S.format(d="November 24, 2026"), ok, _WH_FOOTER_P))) == (["2026-11-24"], [])


def test_the_body_is_read_for_mourning_sentences_only_so_an_unsure_closing_sentence_there_does_not_void():
    closing = "The closing of the executive departments and agencies on November 25 is announced."
    assert votes_of(wh_mourning(body=(MOURN_S.format(d="November 24, 2026"), closing))) == (["2026-11-24"], [])
    # opposite: an unsure sentence that names the mourning day does void
    assert votes_of(wh_mourning(body=(MOURN_S.format(d="November 24, 2026"),
                                      "A National Day of Mourning on November 25."))) == ([], [])


def test_only_the_actions_category_and_the_one_voting_kind_may_stand_together():
    assert votes_of(wh_mourning(cats=["Presidential Actions", "Proclamations", "Nominations & Appointments"])) == ([], [])
    assert votes_of(wh_mourning(cats=["Presidential Actions", "Proclamations", "Briefings & Statements"])) == ([], [])
    assert votes_of(wh_mourning(cats=["Proclamations", "Nominations & Appointments"])) == ([], [])
    assert wh_payload(wh_feed(wh_mourning(cats=["Presidential Actions", "Proclamations", "Nominations & Appointments"])))["type"] is None
    assert votes_of(wh_mourning(cats=["Presidential Actions", "Proclamations"])) == (["2026-11-24"], [])     # opposite
    assert votes_of(wh_mourning(cats=["Proclamations"])) == (["2026-11-24"], [])


def test_a_title_in_capital_letters_is_read_like_one_in_title_case():
    assert votes_of(wh_item(title="A NATIONAL DAY OF MOURNING ON NOVEMBER 24, 2026", body=("x.",))) == (["2026-11-24"], [])
    assert votes_of(wh_item(title="A NATIONAL DAY OF MOURNING ON MAY 5, 2027", body=("x.",))) == (["2027-05-05"], [])
    # opposite: a capital-letter month with no year is still not a stated date, and a change word still voids
    assert votes_of(wh_item(title="A NATIONAL DAY OF MOURNING ON NOVEMBER 24", body=("x.",))) == ([], [])
    assert votes_of(wh_item(title="AMENDING A NATIONAL DAY OF MOURNING ON NOVEMBER 24, 2026", body=("x.",))) == ([], [])
    assert votes_of(wh_item(title="A National Day of Mourning on November 24, 2026", body=("x.",))) == (["2026-11-24"], [])


def test_the_shared_json_loader_still_refuses_a_body_that_starts_with_a_byte_order_mark():
    assert k3._load(b'{"a": 1}', "x") == {"a": 1}
    with pytest.raises(k3.K3Error, match="not valid JSON"):
        k3._load(b'\xef\xbb\xbf{"a": 1}', "x")


def test_a_normal_feed_with_no_mourning_stores_empty_closure_dates_and_no_error(conn):
    register(conn, WH)
    assert k3.refresh(conn, WH, http_get=Fake({WH_URL: WH_FEED}), now=NOW)["error"] == 0
    for row in facts_as_of(conn, "2026-12-01T00:00:00Z", domain="K3_CAL"):
        assert json.loads(row["payload_json"])["closure_dates"] == []


# -- fetch and refresh

def test_refresh_stores_the_feed_with_one_exact_request(conn):
    register(conn, WH)
    fake = Fake({WH_URL: WH_FEED})
    assert k3.refresh(conn, WH, http_get=fake, now=NOW) == {"stored": 3, "unchanged": 0, "error": 0}
    assert len(fake.calls) == 1
    url, headers = fake.calls[0]
    assert url == WH_URL
    assert headers == {"User-Agent": "valedia-client/1.0", "Accept": WH_ACCEPT}
    assert k3.refresh(conn, WH, http_get=fake, now=NOW) == {"stored": 0, "unchanged": 3, "error": 0}
    assert [c[0] for c in fake.calls] == [WH_URL, WH_URL]                                    # never an item page


def test_fetch_needs_no_credentials_and_sends_no_key(conn):
    def no_credentials(name):
        raise AssertionError("the White House feed asked for a credential")
    fake = Fake({WH_URL: WH_FEED})
    facts = k3.fetch(WH, http_get=fake, now=NOW, credentials=no_credentials)
    assert len(facts) == 3 and set(fake.calls[0][1]) == {"User-Agent", "Accept"}


def test_the_other_sources_still_ask_for_json(conn):
    fake = Fake({FR_URL: fr_body(fr_doc())})
    k3.fetch(FR, http_get=fake, now=NOW)
    assert fake.calls[0][1]["Accept"] == "application/json"


def test_refresh_with_a_failing_client_is_one_error_and_stores_nothing(conn):
    register(conn, WH)
    result = k3.refresh(conn, WH, http_get=Fake({WH_URL: RuntimeError("down")}), now=NOW)
    assert result["error"] == 1 and result["stored"] == 0 and result["unchanged"] == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0] == 0


@pytest.mark.parametrize("body", [b"", b'{"a": 1}', b"<html><body>blocked</body></html>", wh_feed(),
                                  drop("pubDate"), b"<!DOCTYPE rss>" + WH_FEED])
def test_refresh_with_a_refused_body_is_one_error_and_stores_nothing(conn, body):
    register(conn, WH)
    result = k3.refresh(conn, WH, http_get=Fake({WH_URL: body}), now=NOW)
    assert result["error"] == 1 and result["stored"] == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0] == 0


def test_fetch_needs_an_http_client_and_bytes():
    with pytest.raises(k3.K3Error):
        k3.fetch(WH, http_get=None, now=NOW)
    with pytest.raises(k3.K3Error):
        k3.fetch(WH, http_get=lambda url, headers: WH_FEED.decode("utf-8"), now=NOW)


def test_params_are_refused_for_the_whitehouse_feed(conn):
    register(conn, WH)
    fake = Fake({WH_URL: WH_FEED})
    for params in ({"start": "2026-11-01"}, {"end": "2026-11-30"}, {"start": "2026-11-01", "end": "2026-11-30"}):
        with pytest.raises(k3.K3Error, match="no params"):
            k3.refresh(conn, WH, http_get=fake, now=NOW, params=params)
        with pytest.raises(k3.K3Error, match="no params"):
            k3.fetch(WH, http_get=fake, now=NOW, params=params)
    assert fake.calls == []
    assert k3.refresh(conn, WH, http_get=fake, now=NOW, params={})["error"] == 0              # opposite: none is fine
    assert k3.refresh(conn, WH, http_get=fake, now=NOW, params=None)["error"] == 0


# -- the quorum

def test_quorum_a_whitehouse_mourning_item_alone_is_only_suspected(conn):
    seed(conn)
    seed_wh(conn, wh_mourning())
    v = votes(conn)
    assert v["authorities_closed"] == [G_PRES] and v["sources_closed"] == [WH]
    assert v["suspected"] is True and v["ordinary_stop"] is False and v["calendar_open"] is True


def test_quorum_a_whitehouse_item_with_no_mourning_votes_nothing(conn):
    seed(conn)
    put(conn, parse_wh(WH_FEED, fetched=LATE))
    v = votes(conn)
    assert v["authorities_closed"] == [] and v["suspected"] is False and v["ordinary_stop"] is False


def test_quorum_a_whitehouse_vote_is_for_its_own_date_only(conn):
    seed(conn)
    seed_wh(conn, wh_mourning(day="November 25, 2026"))
    assert votes(conn)["authorities_closed"] == []
    assert votes(conn, day="2026-11-25")["authorities_closed"] == [G_PRES]


def test_quorum_whitehouse_and_federal_register_for_one_event_are_one_group(conn):
    seed(conn)
    seed_wh(conn, wh_mourning())
    seed_fr(conn, mourning_doc("2026-55555", "November 24, 2026", "2026-11-22"))
    v = votes(conn)
    assert v["authorities_closed"] == [G_PRES] and v["sources_closed"] == sorted([FR, WH])
    assert v["suspected"] is True and v["ordinary_stop"] is False


def test_quorum_whitehouse_and_finnhub_are_two_groups(conn):
    seed(conn)
    seed_wh(conn, wh_mourning())
    seed_holiday(conn, ("Surprise Closure", D, ""))
    v = votes(conn)
    assert v["authorities_closed"] == [G_FIN, G_PRES] and v["sources_closed"] == sorted([H, WH])
    assert v["ordinary_stop"] is True and v["suspected"] is False


def test_quorum_a_whitehouse_fact_known_after_as_of_never_counts(conn):
    seed(conn)
    seed_holiday(conn, ("Surprise Closure", D, ""))
    # posted at 15:00 UTC on the day and fetched at 16:00: not known at 14:00, known at 17:00
    seed_wh(conn, wh_mourning(pub="Tue, 24 Nov 2026 15:00:00 +0000"), fetched="2026-11-24T16:00:00.000000Z")
    early = votes(conn, as_of="2026-11-24T14:00:00Z")
    assert early["authorities_closed"] == [G_FIN] and early["ordinary_stop"] is False and early["suspected"] is True
    late = votes(conn, as_of="2026-11-24T17:00:00Z")
    assert late["authorities_closed"] == [G_FIN, G_PRES] and late["ordinary_stop"] is True


def test_quorum_the_same_day_post_votes_before_the_federal_register_has_it(conn):
    seed(conn)
    seed_wh(conn, wh_mourning(pub="Tue, 24 Nov 2026 12:00:00 +0000"), fetched="2026-11-24T12:05:00.000000Z")
    seed_fr(conn, mourning_doc("2026-55555", "November 24, 2026", "2026-11-24"), fetched="2026-11-25T12:00:00.000000Z")
    v = votes(conn, as_of="2026-11-24T13:00:00Z")
    assert v["sources_closed"] == [WH] and v["suspected"] is True                              # the Register lags a day
    assert votes(conn, as_of="2026-11-25T13:00:00Z")["sources_closed"] == sorted([FR, WH])


def test_quorum_a_revised_whitehouse_item_that_drops_the_date_stops_voting(conn):
    register(conn, WH)
    seed(conn)
    k3.refresh(conn, WH, http_get=Fake({WH_URL: wh_feed(wh_mourning())}), now=NOW)
    assert votes(conn, as_of="2026-11-23T19:00:00Z")["authorities_closed"] == [G_PRES]
    revised = wh_feed(wh_mourning(body=(DEATH_P, "I withdraw the earlier designation.")))
    k3.refresh(conn, WH, http_get=Fake({WH_URL: revised}), now=NOW + timedelta(hours=2))
    assert votes(conn, as_of="2026-11-23T21:00:00Z")["authorities_closed"] == []
    assert votes(conn, as_of="2026-11-23T19:00:00Z")["authorities_closed"] == [G_PRES]        # history is kept
