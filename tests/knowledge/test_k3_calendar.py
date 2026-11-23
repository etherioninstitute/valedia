"""Tests for the K-3 calendar and closure feed. No network: every fetch goes through a fake `http_get`, and a
socket guard makes any real connection fail the test. Every guard is tested both ways."""
import hashlib
import inspect
import json
import socket
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

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
X = "xnys_exchange_calendars"
G_FIN, G_PRES, G_CAL = "FINNHUB", "US_PRESIDENT", "XNYS_CALENDAR"      # the authority groups


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "k.db")
    yield c
    c.close()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def raw(obj):
    return json.dumps(obj).encode()


def put(conn, facts):
    """Store parsed fact dicts the way `refresh` does, straight through add_fact."""
    for f in facts:
        add_fact(conn, **f)


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
    k3.register_all(conn, at, only=ids or [X, H, S, FR])


# ---- SOURCES and registration

def test_sources_have_the_listed_fields_and_roles():
    assert set(k3.SOURCES) == {X, H, S, FR}
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
    assert k3.SOURCES[FR]["authority_group"] == G_PRES
    assert k3.SOURCES[X]["authority_group"] == G_CAL
    for sid, e in k3.SOURCES.items():
        if e["role"] == "SIGNAL":
            assert "authority_group" not in e
        else:
            assert isinstance(e["authority_group"], str) and e["authority_group"]
    assert k3.SOURCES[H]["authority_group"] != k3.SOURCES[FR]["authority_group"]


def test_the_group_is_code_only_and_registration_is_unchanged(conn):
    register(conn)
    for sid in (X, H, S, FR):
        cur = conn.execute("SELECT * FROM source_registry WHERE source_id = ?", (sid,))
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
    k3.register_all(conn, T0, only=[H, X])
    ids = {r[0] for r in conn.execute("SELECT source_id FROM source_registry")}
    assert ids == {H, X}
    row = conn.execute("SELECT * FROM source_registry WHERE source_id = ?", (H,)).fetchone()
    assert row["allow_external_llm"] == 0 and row["ua_policy"] == "GENERIC_CLIENT" and row["personal_use_ok"] == 1
    assert "personal use only; no sharing with third parties" in row["notes"] and "30 calls/s cap" in row["notes"]
    assert row["licence_tag"] == "FINNHUB-PERSONAL-TOS"
    assert row["cost_usd_monthly"] == 0 and row["terms_read_at_utc"] == "2026-01-01T00:00:00.000000Z"
    k3.register_all(conn, T0, only=[H, X])                        # same values again: nothing happens
    k3.register_all(conn, T0, only=[FR])
    assert {r[0] for r in conn.execute("SELECT source_id FROM source_registry")} == {H, X, FR}


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
    return k3.closure_votes(conn, day, as_of)


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
    assert authorities == {H, S, FR} and set(k3._CLOSURE_RULES) == authorities


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
    votes(conn)
    votes(conn)
    assert facts_as_of(conn, "2027-01-01T00:00:00Z") == before


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
                             "html_url", "abstract"]
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
    assert k3.refresh(conn, H, http_get=fake, now=NOW, credentials=creds) == {"stored": 3, "unchanged": 0, "error": 0}
    assert k3.refresh(conn, H, http_get=fake, now=NOW + timedelta(hours=1), credentials=creds) == \
        {"stored": 0, "unchanged": 3, "error": 0}
    changed = holiday_body(("Thanksgiving Day", "2026-11-26", ""), ("Day After Thanksgiving", "2026-11-27", "09:30-14:00"),
                           ("Christmas Day", "2026-12-25", ""), ("New Year's Day", "2027-01-01", ""))
    fake2 = Fake({H_URL: changed})
    got = k3.refresh(conn, H, http_get=fake2, now=NOW + timedelta(days=1), credentials=creds)
    assert got == {"stored": 2, "unchanged": 2, "error": 0}                   # one changed date, one new date
    rows = {r["entity_key"]: json.loads(r["payload_json"]) for r in facts_as_of(conn, "2027-06-01T00:00:00Z", domain="K3_CAL")}
    assert rows["XNYS:CLOSURE:2026-11-27"]["trading_hours"] == "09:30-14:00" and "XNYS:CLOSURE:2027-01-01" in rows
    old = {r["entity_key"]: json.loads(r["payload_json"]) for r in facts_as_of(conn, "2026-11-23T19:00:00Z", domain="K3_CAL")}
    assert old["XNYS:CLOSURE:2026-11-27"]["trading_hours"] == "09:30-13:00"            # the old version is still readable


def test_refresh_returns_counts_only_and_a_failed_fetch_is_an_error_not_a_crash(conn):
    register(conn)
    boom = Fake({H_URL: RuntimeError(f"connection reset for {TOKEN}")})
    got = k3.refresh(conn, H, http_get=boom, now=NOW, credentials=creds)
    assert got["error"] == 1 and got["stored"] == 0 and got["unchanged"] == 0
    assert TOKEN not in json.dumps(got) and TOKEN not in str(got.get("detail", ""))


def test_refresh_never_stores_half_a_parsed_body(conn):
    register(conn)
    body = holiday_body(("a", "2026-11-26", ""), ("b", "nope", ""))
    got = k3.refresh(conn, H, http_get=Fake({H_URL: body}), now=NOW, credentials=creds)
    assert got["error"] == 1 and got["stored"] == 0
    assert facts_as_of(conn, "2027-01-01T00:00:00Z") == []


def test_refresh_with_an_unregistered_source_stores_nothing_and_reports_an_error(conn):
    got = k3.refresh(conn, H, http_get=Fake({H_URL: HOLIDAYS}), now=NOW, credentials=creds)
    assert got["error"] >= 1 and got["stored"] == 0
    assert facts_as_of(conn, "2027-01-01T00:00:00Z") == []


def test_refresh_federal_register_and_status(conn):
    register(conn)
    fr = Fake({FR_URL: fr_body(closing_doc("2026-7", "December 24, 2026", "2026-11-20"))})
    assert k3.refresh(conn, FR, http_get=fr, now=NOW) == {"stored": 1, "unchanged": 0, "error": 0}
    assert k3.refresh(conn, FR, http_get=fr, now=NOW + timedelta(hours=1)) == {"stored": 0, "unchanged": 1, "error": 0}
    st = Fake({S_URL: status_body()})
    assert k3.refresh(conn, S, http_get=st, now=NOW, credentials=creds)["stored"] == 1
    st2 = Fake({S_URL: status_body(session="regular", isOpen=True)})
    assert k3.refresh(conn, S, http_get=st2, now=NOW + timedelta(hours=1), credentials=creds)["stored"] == 1


def test_finnhub_facts_never_reach_an_outside_model(conn):
    register(conn)
    k3.refresh(conn, H, http_get=Fake({H_URL: HOLIDAYS}), now=NOW, credentials=creds)
    k3.refresh(conn, S, http_get=Fake({S_URL: status_body()}), now=NOW, credentials=creds)
    k3.refresh(conn, FR, http_get=Fake({FR_URL: fr_body(fr_doc())}), now=NOW)
    k3.refresh(conn, X, http_get=None, now=NOW, params={"start": "2026-11-23", "end": "2026-11-24"})
    inside = facts_as_of(conn, "2027-01-01T00:00:00Z")
    outside = facts_as_of(conn, "2027-01-01T00:00:00Z", for_outside_llm=True)
    assert {r["source_id"] for r in inside} == {H, S, FR, X}                       # the two-way check: they do exist
    assert {r["source_id"] for r in outside} == {FR, X}
    assert not any("finnhub" in r["source_id"] for r in outside)


def test_the_token_is_never_in_a_url_header_of_other_sources_or_a_stored_payload(conn):
    register(conn)
    fakes = [Fake({H_URL: HOLIDAYS}), Fake({S_URL: status_body()}), Fake({FR_URL: fr_body(fr_doc())})]
    for sid, fake in zip((H, S, FR), fakes):
        k3.refresh(conn, sid, http_get=fake, now=NOW, credentials=creds)
    for fake in fakes:
        for url, headers in fake.calls:
            assert TOKEN not in url
            assert all(TOKEN not in v for h, v in headers.items() if h != "X-Finnhub-Token")
    rows = facts_as_of(conn, "2027-01-01T00:00:00Z")
    assert rows and not any(TOKEN in json.dumps(dict(r)) for r in rows)


# ---- removed holidays (tombstones)

def refresh_h(conn, body, now):
    return k3.refresh(conn, H, http_get=Fake({H_URL: body}), now=now, credentials=creds)


def closure_payload(conn, day, as_of="2099-01-01T00:00:00Z"):
    rows = [r for r in facts_as_of(conn, as_of, domain="K3_CAL")
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
    assert facts_as_of(conn, "2099-01-01T00:00:00Z", domain="K3_CAL", entity_key="XNYS:CLOSURE:2026-11-26")[0][
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
    code, out, _ = run(["register", "--store", db, "--only", f"{X},{H}", "--terms-read-at", T0], capsys)
    assert code == 0
    code, out, _ = run(["schedule", "--store", db, "--from", "2026-11-23", "--to", "2026-11-30"], capsys)
    assert code == 0 and json.loads(out)["stored"] == 8
    code, out, _ = run(["votes", "--store", db, "--date", "2026-11-24", "--as-of", "2099-01-01T00:00:00Z"], capsys)
    v = json.loads(out)
    assert code == 0 and v["calendar_open"] is True and v["authorities_closed"] == []
    code, out, _ = run(["votes", "--store", db, "--date", "2026-11-26", "--as-of", "2099-01-01T00:00:00Z"], capsys)
    assert code == 0 and json.loads(out)["calendar_closed"] is True


def test_cli_votes_exit_one_when_a_closure_is_suspected(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    run(["register", "--store", db, "--only", f"{X},{H}", "--terms-read-at", T0], capsys)
    run(["schedule", "--store", db, "--from", "2026-11-23", "--to", "2026-11-30"], capsys)
    c = connect(db)
    try:
        seed_holiday(c, ("Surprise Closure", D, ""))
    finally:
        c.close()
    code, out, _ = run(["votes", "--store", db, "--date", D, "--as-of", "2099-01-01T00:00:00Z"], capsys)
    assert code == 1 and json.loads(out)["suspected"] is True


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
    run(["register", "--store", db, "--only", H, "--terms-read-at", T0], capsys)
    monkeypatch.setattr(k3, "_urllib_get", Fake({H_URL: RuntimeError(f"failed with {TOKEN}")}))
    code, out, err = run(["refresh", "--store", db, "--source", H], capsys)
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
