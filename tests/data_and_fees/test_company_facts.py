"""Tests for the company-facts store (Finnhub's free plan, kept point-in-time).

No test touches the network: the fetch function is injected and faked, the clock and sleep are fakes that move one
fake time, and the real transport is exercised only through a fake HTTPS handler. No real key is read: a recognisable
fake key is planted and searched for.

Which test covers which numbered outcome of ticket T33 (the section names below say the same):
  1 collect: the kinds, paths, news window, calendar once      -> "1. collect" section
  2 store: observations / news / runs, WAL, one writer         -> "2. store" section
  3 read with no look-ahead (random stores, 250)               -> "3. read" section
  4 limits: 55 a minute, 429, 403, 5xx, max_calls, body, URLs, symbols -> "4. limits" section
  5 the key                                                    -> "5. the key" section
  6 the client name                                            -> "6. client name" section
  7 command line                                               -> "7. command line" section
  8 docstring rules and no use on the order path               -> "8. rules" section
"""
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import logging
import os
import random
import re
import select
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
import zlib
from datetime import datetime, timezone
from email.message import Message
from email.utils import format_datetime
from pathlib import Path

import pytest

from valedia_trader.data_and_fees import company_facts as cf
from valedia_trader.data_and_fees import facts_codec as fc

T0 = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()
KEY = "fhk-9Q7xT2mLpW4vB8nZc3RsD6aYe1UjHg0K"                     # a recognisable fake, 36 characters
HALVES = (KEY[:18], KEY[18:])
PATHS = {"profile": "/stock/profile2", "metrics": "/stock/metric", "earnings": "/stock/earnings",
         "recommendations": "/stock/recommendation", "insiders": "/stock/insider-transactions",
         "news": "/company-news", "earnings_calendar": "/calendar/earnings"}
SIX = ("profile", "metrics", "earnings", "recommendations", "insiders", "news")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class Clock:
    """One fake time: the clock reads it, sleep moves it."""
    def __init__(self, start=T0):
        self.now, self.mono, self.sleeps = float(start), 1000.0, []

    def __call__(self):
        return self.now

    def monotonic(self):
        return self.mono

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.mono += seconds


def day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def article(i, published, **extra):
    a = {"category": "company", "datetime": published, "headline": "H%s" % i, "id": i, "image": "", "related": "X",
         "source": "Wire", "summary": "S%s" % i, "url": "https://example.test/%s" % i}
    a.update(extra)
    return a


def default_body(path, sym):
    if path == "/stock/profile2":
        return {"ticker": sym, "name": "Name " + sym, "marketCapitalization": 1000.5}
    if path == "/stock/metric":
        return {"metric": {"peTTM": 20.5}, "series": {"annual": {}}}
    if path == "/stock/earnings":
        return [{"actual": 1.0, "estimate": 0.9, "period": "2026-09-30", "symbol": sym, "surprise": 0.1}]
    if path == "/stock/recommendation":
        return [{"buy": 10, "hold": 5, "sell": 1, "period": "2026-11-01", "symbol": sym}]
    if path == "/stock/insider-transactions":
        return {"data": [{"name": "A B", "share": 100, "change": 5}], "symbol": sym}
    if path == "/company-news":
        base = sum(map(ord, sym)) * 10
        return [article(base + 1, T0 - 3600), article(base + 2, T0 - 7200)]
    if path == "/calendar/earnings":
        return {"earningsCalendar": [{"date": "2026-11-20", "symbol": "AAA", "epsEstimate": 1.2}]}
    raise AssertionError(path)


class Call:
    def __init__(self, url, headers, t):
        parts = urllib.parse.urlsplit(url)
        self.url, self.headers, self.t = url, dict(headers), t
        self.path = parts.path[len("/api/v1"):]
        self.query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        self.symbol = self.query.get("symbol")


class Finnhub:
    """A fake fetch. `script` maps (path, symbol) or (path, None) to a list of answers used in turn (the last one
    stays): bytes, an exception (raised), a callable of the Call, or any other object (sent as JSON). It answers
    (body, headers) as the transport does; `date(call)`, if given, is the answer's Date header (None: no Date)."""
    def __init__(self, clock, script=None, date=None):
        self.clock, self.script, self.calls, self.date = clock, dict(script or {}), [], date

    def __call__(self, url, headers):
        call = Call(url, headers, self.clock())
        self.calls.append(call)
        for key in ((call.path, call.symbol), (call.path, None)):
            if key in self.script:
                answers = self.script[key]
                a = answers.pop(0) if len(answers) > 1 else answers[0]
                break
        else:
            a = default_body(call.path, call.symbol)
        if isinstance(a, BaseException):
            raise a
        if callable(a):
            a = a(call)
        dated = self.date(call) if self.date else None
        return (a if isinstance(a, bytes) else json.dumps(a).encode()), ({} if dated is None else {"date": dated})

    def paths(self, symbol=None):
        return [c.path for c in self.calls if symbol is None or c.symbol == symbol]


def run(tmp_path, symbols, fetch=None, clock=None, name="cf.sqlite", **kw):
    clock = clock or Clock()
    fetch = fetch or Finnhub(clock)
    kw.setdefault("credentials", lambda n: KEY)
    kw.setdefault("monotonic", clock.monotonic)
    result = cf.collect(symbols, tmp_path / name, fetch=fetch, clock=clock, sleep=clock.sleep, **kw)
    return result, fetch, clock


def snap(folder):
    """Everything observable about a folder: names, modes, sizes, modification times and content hashes."""
    out = {}
    for p in sorted(Path(folder).iterdir()):
        st = p.lstat()
        digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None
        out[p.name] = (st.st_mode, st.st_size, st.st_mtime_ns, digest)
    return out


def rows(tmp_path, sql, args=(), name="cf.sqlite"):
    conn = sqlite3.connect(tmp_path / name)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def http_error(status, retry_after=None):
    return cf.HttpError(status, retry_after)


def stored_text(body):
    """The canonical text a plain or full body holds (T36a D2: a new row is written full or changed-fields, never plain)."""
    if type(body) is str:
        return body
    form, text = fc.decode(body)
    assert form == "F", "a changed-fields row: read it with as_of"
    return text


# ---- 1. collect: the kinds, paths, the news window, the calendar once

def test_each_symbol_is_asked_for_the_six_kinds_at_the_named_paths_and_the_calendar_once(tmp_path):
    result, fetch, clock = run(tmp_path, ["AAA", "BBB"])
    base = cf.BASE_URL
    assert base == "https://finnhub.io/api/v1/"
    today = day(T0)
    assert [c.url for c in fetch.calls if c.symbol is None] == [
        base + "calendar/earnings?from=%s&to=%s" % (today, day(T0 + 90 * 86400))]
    for sym in ("AAA", "BBB"):
        urls = [c.url for c in fetch.calls if c.symbol == sym]
        assert urls == [base + "stock/profile2?symbol=%s" % sym,
                        base + "stock/metric?symbol=%s&metric=all" % sym,
                        base + "stock/earnings?symbol=%s" % sym,
                        base + "stock/recommendation?symbol=%s" % sym,
                        base + "stock/insider-transactions?symbol=%s" % sym,
                        base + "company-news?symbol=%s&from=%s&to=%s" % (sym, day(T0 - 30 * 86400), today)]
    assert result.calls == 13 == len(fetch.calls)
    assert set(c.path for c in fetch.calls) == set(PATHS.values())             # no kind the ticket does not name
    assert [c.symbol for c in fetch.calls].count(None) == 1


def test_no_other_kind_and_no_price_or_fund_request_is_ever_made(tmp_path):
    _, fetch, _ = run(tmp_path, ["AAA"])
    for c in fetch.calls:
        assert not any(w in c.path for w in ("candle", "dividend", "split", "etf", "quote"))


def test_news_window_is_thirty_days_back_the_first_time(tmp_path):
    _, fetch, _ = run(tmp_path, ["AAA"])
    news = [c for c in fetch.calls if c.path == "/company-news"][0]
    assert news.query == {"symbol": "AAA", "from": "2026-10-15", "to": "2026-11-14"}


def test_news_window_starts_two_days_before_the_newest_stored_article_day(tmp_path):
    clock = Clock()
    published = datetime(2026, 11, 12, 23, 59, 0, tzinfo=timezone.utc).timestamp()
    fetch = Finnhub(clock, {("/company-news", "AAA"): [[article(1, published), article(2, published - 5 * 86400)]]})
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    clock.now += 86400
    run(tmp_path, ["AAA", "BBB"], fetch=fetch, clock=clock)
    news = {c.symbol: c for c in fetch.calls if c.path == "/company-news"}
    second = [c for c in fetch.calls if c.path == "/company-news" and c.symbol == "AAA"][1]
    assert second.query["from"] == "2026-11-10" and second.query["to"] == "2026-11-15"
    assert news["BBB"].query["from"] == "2026-10-16"                            # no stored article for BBB: 30 days back


def test_news_window_never_starts_after_today(tmp_path):
    clock = Clock()
    future = T0 + 2 * 86400                                                      # the latest article time that is accepted
    fetch = Finnhub(clock, {("/company-news", "AAA"): [[article(1, future), article(2, future + 1)]]})
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    second = [c for c in fetch.calls if c.path == "/company-news"][1]
    assert second.query["from"] <= second.query["to"]
    assert rows(tmp_path, "SELECT article_id FROM news") == [("1",)]             # one second further is not accepted


def test_the_calendar_is_stored_under_the_symbol_star(tmp_path):
    run(tmp_path, ["AAA"])
    assert rows(tmp_path, "SELECT symbol, kind FROM observations WHERE kind='earnings_calendar'") == [("*", "earnings_calendar")]


# ---- 2. store

def test_the_store_is_wal_and_has_the_three_tables_with_the_named_columns(tmp_path):
    run(tmp_path, ["AAA"])
    assert rows(tmp_path, "PRAGMA journal_mode") == [("wal",)]
    cols = lambda t: [r[1] for r in rows(tmp_path, "PRAGMA table_info(%s)" % t)]            # noqa: E731
    assert cols("observations") == ["symbol", "kind", "fetched_at", "body_sha256", "body"]
    assert cols("news") == ["symbol", "article_id", "published_at", "fetched_at", "headline", "source", "url", "summary"]
    assert cols("runs") == ["started_at", "ended_at", "calls", "stored", "unchanged", "failed", "kinds_refused"]


def test_observation_body_is_canonical_json_and_carries_its_sha256(tmp_path):
    import hashlib
    run(tmp_path, ["AAA"], fetch=Finnhub(Clock(), {("/stock/profile2", "AAA"): [b'{ "b": 2,\n "a": [1, 2], "c": {"z":1,"y":2} }']}))
    (sha, stored, at), = rows(tmp_path, "SELECT body_sha256, body, fetched_at FROM observations WHERE kind='profile'")
    body = stored_text(stored)                           # T36a D2: stored as a full body; the hash is the canonical text's
    assert type(stored) is bytes and body == '{"a":[1,2],"b":2,"c":{"y":2,"z":1}}'
    assert sha == hashlib.sha256(body.encode()).hexdigest() == cf.body_sha256(body)
    assert at == T0


def test_an_unchanged_answer_writes_nothing_and_is_counted(tmp_path):
    first, fetch, clock = run(tmp_path, ["AAA"])
    assert (first.stored, first.unchanged) == (6 + 2, 0)                         # 5 kinds and the calendar, and 2 articles
    before = rows(tmp_path, "SELECT * FROM observations ORDER BY rowid")
    clock.now += 3600
    second, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert (second.stored, second.unchanged, second.failed) == (0, 7, 0)       # 6 + the calendar; news: nothing new
    assert rows(tmp_path, "SELECT * FROM observations ORDER BY rowid") == before


def test_a_different_key_order_or_spacing_is_the_same_answer(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [b'{"a":1,"b":2}', b'{ "b" : 2 ,\n"a" : 1 }']})
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    clock.now += 60
    second, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='profile'") == [(1,)]
    assert second.unchanged >= 1


def test_a_changed_answer_is_a_new_row_and_a_return_to_an_old_value_is_a_change(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [{"v": 1}, {"v": 2}, {"v": 1}, {"v": 1}]})
    for _ in range(4):
        run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
        clock.now += 100
    got = rows(tmp_path, "SELECT fetched_at, body FROM observations WHERE kind='profile' ORDER BY fetched_at")
    got = [(at, stored_text(b)) for at, b in got]         # T36a D2: small answers are stored full (rule e)
    assert got == [(T0, '{"v":1}'), (T0 + 100, '{"v":2}'), (T0 + 200, '{"v":1}')]


def test_news_is_one_row_per_article_at_first_sight_and_a_later_sight_changes_nothing(tmp_path):
    clock = Clock()
    a1 = article(7, T0 - 500, headline="First", summary="one")
    fetch = Finnhub(clock, {("/company-news", "AAA"): [[a1], [dict(a1, headline="Edited", summary="two"), article(8, T0)]]})
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    clock.now += 1000
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    got = rows(tmp_path, "SELECT symbol, article_id, published_at, fetched_at, headline, source, url, summary FROM news ORDER BY article_id")
    assert got == [("AAA", "7", T0 - 500, T0, "First", "Wire", "https://example.test/7", "one"),
                   ("AAA", "8", T0, T0 + 1000, "H8", "Wire", "https://example.test/8", "S8")]


def test_news_without_an_id_or_a_time_is_skipped_not_filled_in(tmp_path):
    clock = Clock()
    bad = [{"headline": "no id", "datetime": T0}, {"id": 5, "headline": "no time"}, {"id": True, "datetime": T0},
           {"id": 6, "datetime": "yesterday"}, "text", None, article(9, T0 - 1, summary=None)]
    run(tmp_path, ["AAA"], fetch=Finnhub(clock, {("/company-news", "AAA"): [bad]}), clock=clock)
    assert rows(tmp_path, "SELECT article_id, summary FROM news") == [("9", None)]


def test_a_news_answer_that_is_not_a_list_is_counted_failed(tmp_path):
    clock = Clock()
    result, _, _ = run(tmp_path, ["AAA"], fetch=Finnhub(clock, {("/company-news", "AAA"): [{"error": "x"}]}), clock=clock)
    assert result.failed == 1 and rows(tmp_path, "SELECT count(*) FROM news") == [(0,)]


def test_the_runs_row_holds_the_counts_and_the_refused_kinds(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/insider-transactions", None): [http_error(403)], ("/stock/metric", "AAA"): [http_error(500)]})
    result, _, _ = run(tmp_path, ["AAA", "BBB"], fetch=fetch, clock=clock)
    (started, ended, calls, stored, unchanged, failed, refused), = rows(tmp_path, "SELECT * FROM runs")
    assert (started, ended) == (T0, clock.now) and calls == result.calls and stored == result.stored
    assert (unchanged, failed, refused) == (0, 1, "insiders")
    assert result.kinds_refused == ("insiders",)
    assert result.summary().startswith("company_facts:") and "\n" not in result.summary()


def test_a_second_collector_is_refused_and_names_the_lock(tmp_path):
    clock = Clock()
    seen = {}
    db = tmp_path / "cf.sqlite"

    def fetch(url, headers):
        if "nested" not in seen:
            seen["nested"] = True
            inner = Finnhub(clock)
            with pytest.raises(cf.Refused) as e:
                cf.collect(["BBB"], db, fetch=inner, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY,
                           monotonic=clock.monotonic)
            seen["text"], seen["inner_calls"] = str(e.value), len(inner.calls)
        return Finnhub(clock)(url, headers)

    result = cf.collect(["AAA"], db, fetch=fetch, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY,
                        monotonic=clock.monotonic)
    assert seen["inner_calls"] == 0 and str(db) + ".lock" in seen["text"] and "\n" not in seen["text"]
    assert result.failed == 0
    run(tmp_path, ["CCC"])                                                         # the lock is free once the run ended


def test_a_clock_earlier_than_the_store_is_refused_before_any_request(tmp_path):
    clock = Clock()
    run(tmp_path, ["AAA"], clock=clock)
    early = Clock(T0 - 3600)
    fetch = Finnhub(early)
    with pytest.raises(cf.Refused):
        run(tmp_path, ["AAA"], fetch=fetch, clock=early)
    assert fetch.calls == []


def test_a_clock_that_steps_back_mid_run_stores_nothing_for_that_answer(tmp_path):
    clock = Clock()

    def back(call):
        clock.now -= 5000
        return {"v": 1}
    fetch = Finnhub(clock, {("/stock/metric", "AAA"): [back]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.failed >= 1
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='metrics'") == [(0,)]


def test_a_file_that_is_not_a_store_is_refused_and_left_alone(tmp_path):
    p = tmp_path / "cf.sqlite"
    p.write_bytes(b"this is not a database at all, just text" * 4)
    with pytest.raises(cf.Refused):
        run(tmp_path, ["AAA"])
    assert p.read_bytes().startswith(b"this is not")


# ---- 3. read, with no look-ahead

def make_store(conn):
    cf.create_schema(conn)


def put_obs(conn, symbol, kind, fetched_at, tag):
    body = cf.canonical_json({"tag": tag, "f": fetched_at})
    conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", (symbol, kind, fetched_at, cf.body_sha256(body), body))


def put_news(conn, symbol, aid, published, fetched):
    conn.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?)", (symbol, str(aid), published, fetched, "h", "s", "u", "m"))


def test_as_of_returns_the_newest_row_fetched_at_or_before_when(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [{"v": 1}, {"v": 2}, {"v": 3}]})
    for _ in range(3):
        run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
        clock.now += 100
    db = tmp_path / "cf.sqlite"
    assert cf.as_of(db, "AAA", "profile", T0 - 0.001) is None
    assert cf.as_of(db, "AAA", "profile", T0)["body"] == {"v": 1}
    assert cf.as_of(db, "AAA", "profile", T0 + 99.999)["body"] == {"v": 1}
    got = cf.as_of(db, "AAA", "profile", T0 + 100)
    assert got["body"] == {"v": 2} and got["fetched_at"] == T0 + 100 and got["symbol"] == "AAA" and got["kind"] == "profile"
    assert cf.as_of(db, "AAA", "profile", T0 + 10**9)["body"] == {"v": 3}
    assert cf.as_of(db, "ZZZ", "profile", T0 + 10**9) is None
    assert cf.as_of(db, "*", "earnings_calendar", T0 + 1)["body"]["earningsCalendar"][0]["symbol"] == "AAA"


def test_as_of_reads_the_store_without_making_or_locking_anything(tmp_path):
    run(tmp_path, ["AAA"])
    before = snap(tmp_path)
    cf.as_of(tmp_path / "cf.sqlite", "AAA", "profile", T0 + 1)
    cf.news_as_of(tmp_path / "cf.sqlite", "AAA", T0 + 1)
    cf.status(tmp_path / "cf.sqlite")
    after = snap(tmp_path)                                  # rev 3 (D1): SQLite's own side files may appear in a writable folder
    assert {k: v for k, v in after.items() if k not in ("cf.sqlite-wal", "cf.sqlite-shm")} == before
    assert set(after) - set(before) <= {"cf.sqlite-wal", "cf.sqlite-shm"}        # no other file, no changed byte or time
    assert rows(tmp_path, "SELECT count(*) FROM runs") == [(1,)]
    with pytest.raises(cf.Refused):
        cf.as_of(tmp_path / "missing.sqlite", "AAA", "profile", T0)
    assert not (tmp_path / "missing.sqlite").exists()


def test_as_of_refuses_a_bad_symbol_kind_or_time(tmp_path):
    run(tmp_path, ["AAA"])
    db = tmp_path / "cf.sqlite"
    for args in (("aaa", "profile", T0), ("AAA", "news", T0), ("AAA", "prices", T0), ("AAA", "profile", float("nan")),
                 ("AAA", "profile", True), ("AAA", "profile", "2026-11-14"), ("*", "profile", T0), ("AAA", "earnings_calendar", T0)):
        with pytest.raises(ValueError):
            cf.as_of(db, *args)


B = T0          # rev 3 (D4): a stored fetch time before 2000 is impossible, so hand-made rows are stamped from here


def test_news_as_of_needs_both_published_and_fetched_not_after_when(tmp_path):
    conn = sqlite3.connect(":memory:")
    make_store(conn)
    put_news(conn, "AAA", 1, published=B + 10, fetched=B + 20)      # published before, fetched after `when`
    put_news(conn, "AAA", 2, published=B + 30, fetched=B + 15)      # fetched before, "published" after `when` (a bad date)
    put_news(conn, "AAA", 3, published=B + 10, fetched=B + 12)
    put_news(conn, "AAA", 4, published=B + 25, fetched=B + 25)
    put_news(conn, "BBB", 5, published=B + 1, fetched=B + 1)
    got = cf.news_as_of(conn, "AAA", B + 16)
    assert [a["article_id"] for a in got] == ["3"]
    assert [a["article_id"] for a in cf.news_as_of(conn, "AAA", B + 25)] == ["3", "1", "4"]        # ordered by published_at
    assert [a["article_id"] for a in cf.news_as_of(conn, "AAA", B + 25, since=B + 11)] == ["4"]
    assert [a["article_id"] for a in cf.news_as_of(conn, "AAA", B + 25, since=B + 10)] == ["3", "1", "4"]
    assert cf.news_as_of(conn, "AAA", B + 25, since=B + 26) == []
    assert set(got[0]) == {"symbol", "article_id", "published_at", "fetched_at", "headline", "source", "url", "summary"}


def test_a_damaged_row_is_refused_not_returned(tmp_path):
    conn = sqlite3.connect(":memory:")
    make_store(conn)
    put_obs(conn, "AAA", "profile", B + 5, "ok")
    conn.execute("UPDATE observations SET body='{\"tag\":\"changed\"}'")
    with pytest.raises(cf.Refused):
        cf.as_of(conn, "AAA", "profile", B + 10)


def test_nothing_fetched_after_when_ever_comes_back_over_250_random_stores():
    kinds = ("profile", "earnings", "metrics")
    checked = 0
    for seed in range(250):
        rnd = random.Random(seed)
        conn = sqlite3.connect(":memory:")
        make_store(conn)
        syms = ["AAA", "BBB", "*"][:rnd.randint(1, 3)]
        obs, news = [], []
        for i in range(rnd.randint(0, 14)):
            sym, kind = rnd.choice(syms), rnd.choice(kinds)
            if sym == "*":
                kind = "earnings_calendar"
            at = B + float(rnd.randint(0, 40))                                # many ties on purpose
            put_obs(conn, sym, kind, at, "r%d" % i)
            obs.append((sym, kind, at, i))
        for i in range(rnd.randint(0, 14)):
            sym = rnd.choice([s for s in syms if s != "*"])
            pub, fet = B + float(rnd.randint(0, 40)), B + float(rnd.randint(0, 40))
            put_news(conn, sym, i, pub, fet)
            news.append((sym, str(i), pub, fet))
        for _ in range(8):
            when = rnd.choice([B + float(rnd.randint(-3, 44)), B + rnd.randint(0, 40) + 0.5, rnd.choice([a[2] for a in obs] or [B + 1.0])])
            for sym in syms:
                for kind in kinds + ("earnings_calendar",):
                    if (sym == "*") != (kind == "earnings_calendar"):
                        continue
                    got = cf.as_of(conn, sym, kind, when)
                    eligible = [o for o in obs if o[0] == sym and o[1] == kind and o[2] <= when]
                    if not eligible:
                        assert got is None
                    else:
                        best = max(eligible, key=lambda o: (o[2], o[3]))
                        assert got["body"]["tag"] == "r%d" % best[3]
                        assert got["fetched_at"] <= when and got["body"]["f"] <= when
                    checked += 1
                if sym != "*":
                    got = cf.news_as_of(conn, sym, when)
                    want = sorted(a[1] for a in news if a[0] == sym and a[2] <= when and a[3] <= when)
                    assert sorted(a["article_id"] for a in got) == want
                    assert all(a["published_at"] <= when and a["fetched_at"] <= when for a in got)
                    checked += 1
        conn.close()
    assert checked > 3000


def test_history_built_by_real_runs_shows_each_moment_only_what_had_been_fetched(tmp_path):
    clock = Clock()
    versions = [{"v": 1}, {"v": 2}, {"v": 3}]
    news_by_run = [[article(1, T0 - 10)], [article(1, T0 - 10), article(2, T0 + 1500)], [article(1, T0 - 10), article(2, T0 + 1500), article(3, T0 + 2000)]]
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): list(versions), ("/company-news", "AAA"): list(news_by_run)})
    for _ in range(3):
        run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
        clock.now += 1000
    db = tmp_path / "cf.sqlite"
    for when, v, ids in ((T0 - 1, None, []), (T0 + 500, 1, ["1"]), (T0 + 1000, 2, ["1"]), (T0 + 1999, 2, ["1", "2"]), (T0 + 2000, 3, ["1", "2", "3"])):
        got = cf.as_of(db, "AAA", "profile", when)
        assert (got["body"]["v"] if got else None) == v
        assert [a["article_id"] for a in cf.news_as_of(db, "AAA", when)] == ids


# ---- 4. limits

def test_at_most_55_requests_in_any_60_seconds_and_the_56th_waits(tmp_path):
    clock = Clock()
    result, fetch, _ = run(tmp_path, ["S%02d" % i for i in range(20)], clock=clock, max_calls=1000)
    ts = [c.t for c in fetch.calls]
    assert len(ts) == 121 == result.calls
    assert ts[:55] == [T0] * 55 and ts[55] >= T0 + 60                              # the 56th is not sent inside the minute
    for t in ts:
        assert sum(1 for u in ts if t <= u <= t + 60) <= 55
    assert clock.sleeps and all(s >= 0 for s in clock.sleeps)


def test_the_window_count_includes_the_retries_and_waits(tmp_path):
    clock = Clock()
    state = {"n": 0}

    def flaky(call):
        state["n"] += 1
        if state["n"] % 2:
            raise http_error(500)
        return {"v": state["n"]}
    fetch = Finnhub(clock, {("/stock/profile2", None): [flaky]})
    run(tmp_path, ["S%d" % i for i in range(10)], fetch=fetch, clock=clock)
    ts = [c.t for c in fetch.calls]
    assert len(ts) > 55 and len([c for c in fetch.calls if c.path == "/stock/profile2"]) == 20
    for t in ts:
        assert sum(1 for u in ts if t <= u <= t + 60) <= 55


def test_a_clock_that_steps_back_does_not_let_requests_through_faster(tmp_path):
    clock = Clock()
    state = {"n": 0}

    def fetch(url, headers):
        state["n"] += 1
        if state["n"] == 30:
            clock.now -= 600
            clock.mono -= 600                     # rev 3: the PACING clock steps back too, so the pacer's clamp is exercised
        return b"{}", {}
    cf.collect(["S%d" % i for i in range(12)], tmp_path / "cf.sqlite", fetch=fetch, clock=clock, sleep=clock.sleep,
               credentials=lambda n: KEY, monotonic=clock.monotonic)
    assert state["n"] == 73                       # the run completed
    assert clock.sleeps and max(clock.sleeps) <= 61   # and no wait of ten minutes to "catch up" with the earlier readings


def test_a_429_waits_the_retry_after_and_tries_again(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [http_error(429, 7), {"v": 1}]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert clock.sleeps == [7] and result.failed == 0
    first, second = [c for c in fetch.calls if c.path == "/stock/profile2"]
    assert second.t - first.t == 7 and result.calls == 8
    assert [stored_text(b) for b, in rows(tmp_path, "SELECT body FROM observations WHERE kind='profile'")] == ['{"v":1}']   # T36a D2


@pytest.mark.parametrize("sent, waited", [(None, 30), (0, 0), (500, 120), (120, 120), (float("nan"), 30), (-3, 30), ("abc", 30), (2.5, 2.5)])
def test_a_429_wait_defaults_to_30_and_is_at_most_120(tmp_path, sent, waited):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [http_error(429, sent), {"v": 1}]})
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert clock.sleeps[0] == waited


def test_a_request_is_retried_after_429_at_most_three_times(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [http_error(429, 1)]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert len([c for c in fetch.calls if c.path == "/stock/profile2"]) == 4 and clock.sleeps == [1, 1, 1]
    assert result.failed == 1 and result.calls == 4 + 6 and rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='profile'") == [(0,)]


def test_a_403_marks_the_kind_and_it_is_not_asked_again_for_any_symbol(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/insider-transactions", None): [http_error(403)]})
    result, _, _ = run(tmp_path, ["AAA", "BBB", "CCC"], fetch=fetch, clock=clock)
    assert fetch.paths().count("/stock/insider-transactions") == 1
    assert result.kinds_refused == ("insiders",) and result.failed == 0
    assert fetch.paths("CCC") == [PATHS[k] for k in SIX if k != "insiders"]
    assert rows(tmp_path, "SELECT kinds_refused FROM runs") == [("insiders",)]


def test_the_mark_lasts_for_the_run_only(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/insider-transactions", None): [http_error(403), {"data": [], "symbol": "AAA"}]})
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    clock.now += 10
    second, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert fetch.paths().count("/stock/insider-transactions") == 2 and second.kinds_refused == ()


def test_a_403_on_the_calendar_marks_it_too(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/calendar/earnings", None): [http_error(403)]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.kinds_refused == ("earnings_calendar",) and fetch.paths().count("/calendar/earnings") == 1


@pytest.mark.parametrize("failure", [http_error(500), http_error(503), TimeoutError("t"), socket.timeout("t"), ConnectionResetError("r"), OSError("o")])
def test_a_5xx_or_a_timeout_gets_two_tries_then_is_failed_and_the_run_goes_on(tmp_path, failure):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/metric", "AAA"): [failure]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert fetch.paths("AAA").count("/stock/metric") == 2
    assert result.failed == 1 and result.calls == 8 and fetch.paths("AAA")[-1] == "/company-news"
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='metrics'") == [(0,)]


def test_a_5xx_followed_by_an_answer_is_stored(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/metric", "AAA"): [http_error(502), {"metric": {}}]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.failed == 0 and rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='metrics'") == [(1,)]


@pytest.mark.parametrize("failure", [http_error(404), http_error(400), http_error(302), http_error(301), ValueError("x"), RuntimeError("y")])
def test_any_other_failure_is_counted_and_not_retried(tmp_path, failure):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/metric", "AAA"): [failure]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert fetch.paths("AAA").count("/stock/metric") == 1 and result.failed == 1 and result.calls == 7


def test_a_401_ends_the_run_cleanly_because_every_later_request_would_fail_too(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [http_error(401)]})
    result, _, _ = run(tmp_path, ["AAA", "BBB"], fetch=fetch, clock=clock)
    assert result.stopped == "key rejected" and result.failed == 1 and len(fetch.calls) == 2
    assert rows(tmp_path, "SELECT ended_at IS NOT NULL FROM runs") == [(1,)]


def test_max_calls_ends_the_run_cleanly(tmp_path):
    result, fetch, clock = run(tmp_path, ["AAA", "BBB"], max_calls=5)
    assert result.calls == 5 == len(fetch.calls) and result.stopped == "max_calls"
    assert rows(tmp_path, "SELECT calls, ended_at FROM runs") == [(5, clock.now)]
    assert rows(tmp_path, "SELECT count(*) FROM observations") == [(5,)]


def test_retries_count_toward_max_calls(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/calendar/earnings", None): [http_error(500)]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock, max_calls=2)
    assert result.calls == 2 and result.stopped == "max_calls"


def test_the_default_max_calls_is_1000_and_a_bad_one_is_refused(tmp_path):
    import inspect
    assert inspect.signature(cf.collect).parameters["max_calls"].default == 1000 == cf.DEFAULT_MAX_CALLS
    result, fetch, _ = run(tmp_path, ["S%03d" % i for i in range(170)])        # 1 + 170*6 = 1021 requests wanted
    assert result.calls == 1000 and result.stopped == "max_calls"
    for bad in (0, -1, 1.5, "10", True, None):
        with pytest.raises(cf.Refused):
            run(tmp_path, ["AAA"], max_calls=bad)


def test_a_run_that_ends_by_itself_has_no_stop_reason(tmp_path):
    result, _, _ = run(tmp_path, ["AAA"])
    assert result.stopped is None


def test_a_body_over_5_mb_is_refused_and_counted_failed(tmp_path):
    clock = Clock()
    limit = cf.MAX_BODY_BYTES
    assert limit == 5 * 1024 * 1024
    exact = b'{"x":"' + b"x" * (limit - 8) + b'"}'
    over = b'{"x":"' + b"x" * (limit - 7) + b'"}'
    assert len(exact) == limit and len(over) == limit + 1
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [over], ("/stock/metric", "AAA"): [exact]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.failed == 1
    assert fetch.paths("AAA").count("/stock/profile2") == 1                       # not retried
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='profile'") == [(0,)]
    assert [len(stored_text(b)) for b, in rows(tmp_path, "SELECT body FROM observations WHERE kind='metrics'")] == [limit]   # T36a D2


@pytest.mark.parametrize("body", [b"not json", b"", b"<html>", b'{"a":1,"a":2}', b'{"x":[{"k":1,"k":1}]}', b"NaN", b'{"a":Infinity}', b'{"a":1e999}',
                                  b'{"a":1}trailing', b"\xff\xfe{}", b"[" * 100000, "x".encode(), b'{"a":' + b"9" * 6000 + b"}"])
def test_a_body_that_is_not_json_or_repeats_a_key_is_refused_and_counted_failed(tmp_path, body):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [body]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.failed == 1 and fetch.paths("AAA").count("/stock/profile2") == 1
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='profile'") == [(0,)]


def test_a_body_that_is_not_bytes_is_counted_failed(tmp_path):
    def fetch(url, headers):
        return "text, not bytes"
    result = cf.collect(["AAA"], tmp_path / "cf.sqlite", fetch=fetch, clock=Clock(), sleep=lambda s: None, credentials=lambda n: KEY)
    assert result.failed == 7 and rows(tmp_path, "SELECT count(*) FROM observations") == [(0,)]


def test_only_urls_under_the_finnhub_base_are_requested(tmp_path):
    _, fetch, _ = run(tmp_path, ["AAA", "BRK.B", "BRK-B"])
    assert fetch.calls and all(c.url.startswith("https://finnhub.io/api/v1/") for c in fetch.calls)
    assert all("@" not in c.url and " " not in c.url for c in fetch.calls)


@pytest.mark.parametrize("url", ["http://finnhub.io/api/v1/stock/profile2?symbol=A", "https://finnhub.io.evil.test/api/v1/x",
                                 "https://finnhub.io@evil.test/api/v1/x", "https://evil.test/https://finnhub.io/api/v1/x",
                                 "https://finnhub.io/api/v2/x", "https://finnhub.io/api/v1", "https://finnhub.io:444/api/v1/x",
                                 "https://FINNHUB.io/api/v1/x", "ftp://finnhub.io/api/v1/x", "https://finnhub.io/api/v1/../v2/x",
                                 "https://finnhub.io/api/v1/x y", "https://finnhub.io/api/v1/x\r\nHost: evil.test", "", None])
def test_the_real_fetch_refuses_any_other_url_before_any_request(url):
    seen = []

    class Spy(urllib.request.HTTPSHandler):
        def https_open(self, req):
            seen.append(req.full_url)
            raise AssertionError("a request was made")
    with pytest.raises(ValueError):
        cf.http_fetch(url, {"X-Finnhub-Token": KEY}, opener=cf._build_opener(Spy()))
    assert seen == []


class FakeHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, routes):
        super().__init__()
        self.routes, self.seen = routes, []

    def https_open(self, req):
        self.seen.append(req)
        code, headers, body = self.routes[req.full_url]
        h = Message()
        for k, v in headers.items():
            h[k] = v
        resp = urllib.response.addinfourl(io.BytesIO(body) if isinstance(body, bytes) else body, h, req.full_url, code)
        resp.msg = "answer"
        return resp


U = "https://finnhub.io/api/v1/stock/profile2?symbol=AAA"
HDR = {"X-Finnhub-Token": KEY, "User-Agent": cf.USER_AGENT, "Accept": "application/json"}


def test_the_real_fetch_follows_no_redirect():
    evil = "https://evil.test/api/v1/stock/profile2?symbol=AAA"
    for code in (301, 302, 303, 307, 308):
        handler = FakeHTTPS({U: (code, {"Location": evil}, b""), evil: (200, {}, b'{"stolen":1}')})
        with pytest.raises(cf.HttpError) as e:
            cf.http_fetch(U, HDR, opener=cf._build_opener(handler))
        assert e.value.status == code
        assert [r.full_url for r in handler.seen] == [U]


def test_a_redirect_answer_is_failed_and_not_retried_by_collect(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [http_error(302)]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.failed == 1 and fetch.paths("AAA").count("/stock/profile2") == 1


def test_the_real_fetch_returns_the_body_and_reads_no_more_than_the_cap():
    class Counting(io.BytesIO):
        total = 0

        def read(self, n=-1):
            b = super().read(n)
            Counting.total += len(b)
            return b
    handler = FakeHTTPS({U: (200, {}, Counting(b"x" * (cf.MAX_BODY_BYTES * 3)))})
    with pytest.raises(ValueError):
        cf.http_fetch(U, HDR, opener=cf._build_opener(handler))
    assert Counting.total <= cf.MAX_BODY_BYTES + 65536
    handler = FakeHTTPS({U: (200, {}, b'{"ok":1}')})
    assert cf.http_fetch(U, HDR, opener=cf._build_opener(handler))[0] == b'{"ok":1}'          # rev 3 (D4): (body, headers)


def test_the_real_fetch_turns_statuses_into_http_errors_with_retry_after():
    for code, headers, retry in ((429, {"Retry-After": "7"}, 7.0), (429, {}, None), (429, {"Retry-After": "soon"}, None), (500, {}, None), (403, {}, None)):
        handler = FakeHTTPS({U: (code, headers, b'{"error":"x"}')})
        with pytest.raises(cf.HttpError) as e:
            cf.http_fetch(U, HDR, opener=cf._build_opener(handler))
        assert e.value.status == code and e.value.retry_after == retry
        assert str(e.value) == "HTTP %d" % code


def test_the_real_fetch_maps_timeouts_and_connection_errors_to_oserrors_with_fixed_text():
    class Boom(urllib.request.HTTPSHandler):
        def __init__(self, exc):
            super().__init__()
            self.exc = exc

        def https_open(self, req):
            raise self.exc
    with pytest.raises(TimeoutError):
        cf.http_fetch(U, HDR, opener=cf._build_opener(Boom(urllib.error.URLError(TimeoutError("slow " + KEY)))))
    with pytest.raises(OSError) as e:
        cf.http_fetch(U, HDR, opener=cf._build_opener(Boom(urllib.error.URLError(ConnectionRefusedError("refused " + KEY)))))
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    assert e.value.__cause__ is None and e.value.__context__ is None


@pytest.mark.parametrize("symbol", ["A", "AAPL", "BRK.B", "BRK-B", "A1", "ABCDEFGHIJ", "X.Y-Z9"])
def test_a_good_symbol_is_asked(tmp_path, symbol):
    result, fetch, _ = run(tmp_path, [symbol], name="cf_%s.sqlite" % abs(hash(symbol)))
    assert result.calls == 7 and all(c.symbol in (symbol, None) for c in fetch.calls)


BAD_SYMBOLS = ["aapl", "Aapl", "", "1A", "-A", ".A", "ABCDEFGHIJK", "A B", "A/B", "A?x=1", "A\n", "AAPL\n", "A&metric=x", "../x", "A%41",
               "A#", "ÄB", "ＡＢ", "A\x00", " AAPL", "AAPL ", "A;B", None, 123, b"AAPL", ["AAPL"]]


@pytest.mark.parametrize("bad", BAD_SYMBOLS)
def test_a_bad_symbol_is_refused_before_any_request_or_any_file(tmp_path, bad):
    clock = Clock()
    fetch = Finnhub(clock)
    with pytest.raises(cf.Refused) as e:
        run(tmp_path, ["AAA", bad], fetch=fetch, clock=clock)
    assert fetch.calls == [] and list(tmp_path.iterdir()) == []
    assert "\n" not in str(e.value) and "\x00" not in str(e.value)


def test_symbols_must_be_a_list_of_strings_and_duplicates_are_asked_once(tmp_path):
    with pytest.raises(cf.Refused):
        run(tmp_path, "AAPL")
    result, fetch, _ = run(tmp_path, ["AAA", "AAA", "BBB"])
    assert result.calls == 13 and fetch.paths("AAA").count("/stock/profile2") == 1


def test_valid_symbol_matches_the_ticket_pattern():
    assert cf.valid_symbol("BRK.B") and cf.valid_symbol("A") and not cf.valid_symbol("AAPL\n") and not cf.valid_symbol("a")


# ---- 5. the key

def test_the_key_is_read_through_credentials_by_its_name_and_sent_only_in_the_header(tmp_path):
    asked = []

    def credentials(name):
        asked.append(name)
        return KEY
    _, fetch, _ = run(tmp_path, ["AAA"], credentials=credentials)
    assert asked == ["FINNHUB_API_KEY"] * len(asked) and asked
    for c in fetch.calls:
        assert c.headers["X-Finnhub-Token"] == KEY
        assert KEY not in c.url and not any(h in c.url for h in HALVES)
        assert KEY not in [v for k, v in c.headers.items() if k != "X-Finnhub-Token"]
        assert "token" not in c.url.lower() and "apikey" not in c.url.lower()


def test_the_default_credentials_is_core_secrets_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", KEY)
    result, fetch, _ = run(tmp_path, ["AAA"], credentials=None)
    assert fetch.calls[0].headers["X-Finnhub-Token"] == KEY and result.failed == 0


def test_a_missing_or_unusable_key_is_refused_before_any_request_naming_the_variable_only(tmp_path):
    def missing(name):
        raise RuntimeError("credential %s is not set; the value was %s" % (name, KEY))
    for credentials in (missing, lambda n: "", lambda n: "   ", lambda n: None, lambda n: 5, lambda n: KEY + "\nX-Evil: 1",
                        lambda n: "k e y" * 4, lambda n: "é" * 20, lambda n: "k" * 300):
        clock = Clock()
        fetch = Finnhub(clock)
        with pytest.raises(cf.Refused) as e:
            run(tmp_path, ["AAA"], fetch=fetch, clock=clock, credentials=credentials)
        assert fetch.calls == [] and "FINNHUB_API_KEY" in str(e.value) and KEY not in str(e.value) and "\n" not in str(e.value)
        assert list(tmp_path.iterdir()) == []


def everything_in(path):
    return b"".join(p.read_bytes() for p in sorted(Path(path).iterdir()) if p.is_file())


def test_the_key_is_nowhere_in_output_logs_exceptions_results_or_the_database(tmp_path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    clock = Clock()
    leaky = {("/stock/profile2", None): [RuntimeError("failed with header " + KEY)],
             ("/stock/metric", None): [OSError("connection to https://finnhub.io/?token=" + KEY)],
             ("/stock/earnings", None): [http_error(500)],
             ("/stock/recommendation", None): [b'[{"echo":"' + KEY.encode() + b'"}]'],
             ("/stock/insider-transactions", None): [b'{"half":"' + HALVES[0].encode() + b'-tail"}'],
             ("/company-news", None): [lambda call: [article(1, T0 - 5, headline="seen with " + call.headers["X-Finnhub-Token"])]]}
    fetch = Finnhub(clock, leaky)
    texts = []
    result, _, _ = run(tmp_path, ["AAA", "BBB"], fetch=fetch, clock=clock)
    texts += [result.summary(), repr(result), str(result.failures), repr(result.failures)]
    assert cf.main(["collect", "--symbols", "CCC", "--db", str(tmp_path / "second.sqlite"), "--max-calls", "20"], fetch=fetch, clock=clock,
                   sleep=clock.sleep, credentials=lambda n: KEY) == 0
    for argv in (["status", "--db", str(tmp_path / "cf.sqlite")], ["show", "AAA", "--db", str(tmp_path / "cf.sqlite")],
                 ["show", "AAA", "--kind", "news", "--db", str(tmp_path / "cf.sqlite")]):
        cf.main(argv)
    for bad in (lambda n: KEY + " x", lambda n: (_ for _ in ()).throw(RuntimeError(KEY))):
        assert cf.main(["collect", "--symbols", "AAA", "--db", str(tmp_path / "third.sqlite")], fetch=fetch, clock=clock, sleep=clock.sleep,
                       credentials=bad) == 2
    with pytest.raises(cf.Refused) as e:
        run(tmp_path, ["aaa" + KEY], fetch=fetch, clock=clock)
    texts.append(str(e.value))
    captured = capsys.readouterr()
    haystack = "\n".join(texts + [captured.out, captured.err, caplog.text]).encode() + everything_in(tmp_path)
    assert KEY.encode() not in haystack and HALVES[0].encode() not in haystack and HALVES[1].encode() not in haystack
    # the planted echoes are not stored either: a body that carries the key is refused
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='recommendations'") == [(0,)]
    assert rows(tmp_path, "SELECT count(*) FROM news") == [(0,)]


def test_the_database_and_its_side_files_hold_no_key_after_a_normal_run(tmp_path):
    run(tmp_path, ["AAA", "BBB"])
    blob = everything_in(tmp_path)
    assert KEY.encode() not in blob and b"X-Finnhub-Token" not in blob and b"market-data-reader" not in blob


def test_the_real_fetch_puts_the_key_only_in_a_header_not_in_the_url_or_an_error():
    handler = FakeHTTPS({U: (200, {}, b"{}")})
    cf.http_fetch(U, HDR, opener=cf._build_opener(handler))
    req, = handler.seen
    assert KEY not in req.full_url and req.get_header("X-finnhub-token") == KEY
    assert dict(req.header_items())["X-finnhub-token"] == KEY
    handler = FakeHTTPS({U: (500, {}, b"{}")})
    with pytest.raises(cf.HttpError) as e:
        cf.http_fetch(U, HDR, opener=cf._build_opener(handler))
    assert KEY not in str(e.value) and KEY not in repr(e.value)


def test_the_real_fetch_sends_no_header_it_was_not_given_a_name_for():
    with pytest.raises(ValueError):
        cf.http_fetch(U, {"X-Finnhub-Token": KEY, "Cookie": "a=b"}, opener=cf._build_opener(FakeHTTPS({U: (200, {}, b"{}")})))
    with pytest.raises(ValueError):
        cf.http_fetch(U, {"X-Finnhub-Token": KEY + "\r\nX: y"}, opener=cf._build_opener(FakeHTTPS({U: (200, {}, b"{}")})))


# ---- 6. client name

FORBIDDEN = ("valedia", "trader", "burton", "chatelain", "etherion", "hermes", "openstock", "qwen", "python", "urllib", "@")


def test_the_request_name_is_exactly_the_generic_constant_and_names_no_project_owner_or_business(tmp_path):
    assert cf.USER_AGENT == "market-data-reader/1.0"
    _, fetch, _ = run(tmp_path, ["AAA"])
    for c in fetch.calls:
        assert c.headers["User-Agent"] == "market-data-reader/1.0"
        for text in list(c.headers.values()) + [c.url]:
            if text != KEY:
                assert not any(w in text.lower() for w in FORBIDDEN), text
    assert not any(w in cf.USER_AGENT.lower() for w in FORBIDDEN)


def test_the_real_fetch_sends_the_constant_name_whatever_it_is_given():
    handler = FakeHTTPS({U: (200, {}, b"{}")})
    cf.http_fetch(U, dict(HDR, **{"User-Agent": "Python-urllib/3.12 valedia"}), opener=cf._build_opener(handler))
    req, = handler.seen
    assert req.get_header("User-agent") == "market-data-reader/1.0"
    assert not any(w in " ".join(v.lower() for _, v in req.header_items() if v != KEY) for w in FORBIDDEN)


# ---- 7. command line

def cli(tmp_path, *argv, clock=None, fetch=None, **kw):
    clock = clock or Clock()
    fetch = fetch or Finnhub(clock)
    kw.setdefault("credentials", lambda n: KEY)
    kw.setdefault("monotonic", clock.monotonic)
    rc = cf.main(list(argv), fetch=fetch, clock=clock, sleep=clock.sleep, **kw)
    return rc, fetch, clock


def test_collect_prints_one_summary_line_and_exits_0(tmp_path, capsys):
    rc, fetch, _ = cli(tmp_path, "collect", "--symbols", "AAA,BBB", "--db", str(tmp_path / "cf.sqlite"))
    out = capsys.readouterr()
    assert rc == 0 and out.err == "" and len(out.out.strip().splitlines()) == 1
    assert "13 calls" in out.out and "stored" in out.out and "unchanged" in out.out and "failed" in out.out
    assert len(fetch.calls) == 13


def test_collect_exits_0_when_some_requests_failed_but_the_run_ended(tmp_path, capsys):
    clock = Clock()
    rc, _, _ = cli(tmp_path, "collect", "--symbols", "AAA", "--db", str(tmp_path / "cf.sqlite"), clock=clock,
                   fetch=Finnhub(clock, {("/stock/metric", None): [http_error(500)]}))
    assert rc == 0 and "1 failed" in capsys.readouterr().out


def test_collect_max_calls_flag(tmp_path, capsys):
    rc, fetch, _ = cli(tmp_path, "collect", "--symbols", "AAA", "--db", str(tmp_path / "cf.sqlite"), "--max-calls", "3")
    assert rc == 0 and len(fetch.calls) == 3 and "3 calls" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--symbols", "aaa"], ["--symbols", "AAA,,BBB"], ["--symbols", ""], ["--symbols", "AAA,B B"], ["--max-calls", "0"],
                                  ["--max-calls", "x"], ["--symbols", "AAA", "--max-calls", "-4"]])
def test_collect_refusals_exit_2_with_one_line_and_no_request(tmp_path, capsys, argv):
    rc, fetch, _ = cli(tmp_path, "collect", "--db", str(tmp_path / "cf.sqlite"), *argv)
    out = capsys.readouterr()
    assert rc == 2 and fetch.calls == [] and out.out == "" and len(out.err.strip().splitlines()) == 1 and "Traceback" not in out.err


def test_a_bad_command_is_refused_before_the_secrets_file_is_loaded(tmp_path, monkeypatch, capsys):
    from valedia_trader.core import secrets
    loaded = []
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: loaded.append(1))
    for argv in (["--symbols", "aaa"], ["--symbols", "AAA", "--max-calls", "0"], ["--symbols", "AAA", "--max-calls", "x"]):
        assert cf.main(["collect", "--db", str(tmp_path / "cf.sqlite")] + argv) == 2
    assert loaded == [] and list(tmp_path.iterdir()) == []
    capsys.readouterr()


def test_no_key_exits_2_naming_the_variable(tmp_path, capsys):
    def no_key(name):
        raise RuntimeError("credential %s is not set" % name)
    rc, fetch, _ = cli(tmp_path, "collect", "--symbols", "AAA", "--db", str(tmp_path / "cf.sqlite"), credentials=no_key)
    err = capsys.readouterr().err
    assert rc == 2 and fetch.calls == [] and "FINNHUB_API_KEY" in err and len(err.strip().splitlines()) == 1


def test_a_store_held_by_another_run_exits_2_naming_the_lock(tmp_path, capsys):
    clock = Clock()
    db = tmp_path / "cf.sqlite"
    state = {}

    def fetch(url, headers):
        if not state:
            state["rc"] = cf.main(["collect", "--symbols", "BBB", "--db", str(db)], fetch=Finnhub(clock), clock=clock, sleep=clock.sleep,
                                  credentials=lambda n: KEY)
            state["err"] = capsys.readouterr().err
        return b"{}"
    assert cf.main(["collect", "--symbols", "AAA", "--db", str(db)], fetch=fetch, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY) == 0
    assert state["rc"] == 2 and str(db) + ".lock" in state["err"] and len(state["err"].strip().splitlines()) == 1


def test_the_universe_flag_reads_the_stocks_entry_of_the_universes_file(tmp_path, monkeypatch, capsys):
    uni = tmp_path / "universes.json"
    uni.write_text(json.dumps({"note": "x", "stocks": {"folder": "f", "start": "2015-01-01", "symbols": ["AAA", "BRK-B"]},
                               "crypto": {"folder": "g", "symbols": ["BTC"]}}))
    monkeypatch.setattr(cf, "UNIVERSES_PATH", uni)
    rc, fetch, _ = cli(tmp_path, "collect", "--universe", "stocks", "--db", str(tmp_path / "cf.sqlite"))
    assert rc == 0 and sorted({c.symbol for c in fetch.calls if c.symbol}) == ["AAA", "BRK-B"]
    rc, fetch, _ = cli(tmp_path, "collect", "--db", str(tmp_path / "cf2.sqlite"))                 # the default universe is stocks
    assert rc == 0 and sorted({c.symbol for c in fetch.calls if c.symbol}) == ["AAA", "BRK-B"]
    for argv in (["--universe", "crypto"], ["--universe", "stocks", "--symbols", "AAA"]):
        rc, _, _ = cli(tmp_path, "collect", *argv, "--db", str(tmp_path / "cf3.sqlite"))
        err = capsys.readouterr().err
        assert rc == 2 and len(err.strip().splitlines()) == 1
    capsys.readouterr()


def test_the_real_universe_file_is_read_and_unchanged_in_shape():
    syms = cf.universe_symbols("stocks")
    data = json.loads(Path(cf.UNIVERSES_PATH).read_text())
    assert syms == data["stocks"]["symbols"] and len(syms) > 50 and all(cf.valid_symbol(s) for s in syms)
    assert cf.UNIVERSES_PATH.name == "universes.json" and cf.UNIVERSES_PATH.parent.name == "research_loop"


def test_a_universe_file_that_is_wrong_is_refused(tmp_path, capsys):
    for text in ("not json", "[]", '{"stocks": 3}', '{"stocks": {"symbols": "AAA"}}', '{"stocks": {"symbols": []}}', "{}"):
        p = tmp_path / "u.json"
        p.write_text(text)
        with pytest.raises(cf.Refused):
            cf.universe_symbols("stocks", p)
    with pytest.raises(cf.Refused):
        cf.universe_symbols("stocks", tmp_path / "missing.json")


def test_the_default_db_is_memory_company_facts_sqlite_under_the_repository():
    root = Path(cf.__file__).resolve().parents[2]
    assert cf.DEFAULT_DB == root / "memory" / "company_facts.sqlite"
    assert (root / "valedia_trader" / "research_loop" / "universes.json").exists()


def test_status_reports_rows_per_kind_symbols_the_last_run_and_the_fetch_range(tmp_path, capsys):
    clock = Clock()
    run(tmp_path, ["AAA", "BBB"], clock=clock, fetch=Finnhub(clock, {("/stock/insider-transactions", None): [http_error(403)]}))
    clock.now += 3600
    run(tmp_path, ["AAA"], clock=clock, fetch=Finnhub(clock))
    st = cf.status(tmp_path / "cf.sqlite")
    assert st["rows"]["profile"] == 2 and st["rows"]["news"] == 4 and st["rows"]["insiders"] == 1 and st["rows"]["earnings_calendar"] == 1
    assert st["symbols"] == 2
    assert st["last_run"]["calls"] == 7 and st["last_run"]["stored"] == 1 and st["last_run"]["failed"] == 0
    assert st["oldest"] == T0 and st["newest"] == T0 + 3600
    assert cf.main(["status", "--db", str(tmp_path / "cf.sqlite")]) == 0
    out = capsys.readouterr().out
    for word in ("profile", "news", "earnings_calendar", "symbols", "last run", "2026-11-14T12:00:00Z", "2026-11-14T13:00:00Z"):
        assert word in out


def test_status_and_show_refuse_a_missing_store_with_exit_2_and_make_nothing(tmp_path, capsys):
    for argv in (["status"], ["show", "AAA"]):
        assert cf.main(argv + ["--db", str(tmp_path / "none.sqlite")]) == 2
        assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert list(tmp_path.iterdir()) == []


def test_show_prints_the_newest_fact_as_of_a_time(tmp_path, capsys):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [{"v": 1}, {"v": 2}]})
    for _ in range(2):
        run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
        clock.now += 3600
    db = str(tmp_path / "cf.sqlite")
    assert cf.main(["show", "AAA", "--kind", "profile", "--db", db, "--as-of", "2026-11-14T12:30:00Z"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["body"] == {"v": 1} and got["fetched_at"] == "2026-11-14T12:00:00Z" and got["kind"] == "profile"
    assert cf.main(["show", "AAA", "--kind", "profile", "--db", db, "--as-of", "2026-11-14T13:00:00+00:00"]) == 0
    assert json.loads(capsys.readouterr().out)["body"] == {"v": 2}
    assert cf.main(["show", "AAA", "--kind", "profile", "--db", db, "--as-of", "2026-11-14T11:59:59Z"]) == 0
    assert json.loads(capsys.readouterr().out) is None
    assert cf.main(["show", "AAA", "--db", db, "--as-of", "2026-11-14T12:30:00Z"]) == 0
    everything = json.loads(capsys.readouterr().out)
    assert set(everything) == set(SIX) and everything["profile"]["body"] == {"v": 1}
    assert len(everything["news"]) == 2 and everything["news"][0]["published_at"] <= everything["news"][1]["published_at"]
    assert cf.main(["show", "AAA", "--kind", "news", "--db", db, "--as-of", "2026-11-14T12:30:00Z"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert cf.main(["show", "*", "--kind", "earnings_calendar", "--db", db, "--as-of", "2026-11-14T12:30:00Z"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "earnings_calendar"


@pytest.mark.parametrize("argv", [["show", "aaa"], ["show", "AAA", "--kind", "prices"], ["show", "AAA", "--as-of", "2026-11-14"],
                                  ["show", "AAA", "--as-of", "2026-11-14T12:00:00"], ["show", "AAA", "--as-of", "2026-11-14T12:00:00+05:00"],
                                  ["show", "AAA", "--as-of", "yesterday"], ["show", "AAA", "--kind", "earnings_calendar"]])
def test_show_refuses_bad_input_with_exit_2_and_one_line(tmp_path, capsys, argv):
    run(tmp_path, ["AAA"])
    rc = cf.main(argv + ["--db", str(tmp_path / "cf.sqlite")])
    out = capsys.readouterr()
    assert rc == 2 and len(out.err.strip().splitlines()) == 1 and out.out == ""


# ---- 8. the rules of the source

def test_the_docstring_states_the_two_rules_of_the_source():
    doc = " ".join((cf.__doc__ or "").split())
    assert "personal use" in doc
    assert "nothing read from this store, and nothing derived from it, may be sent to an outside model or any other outside service" in doc.lower()
    assert "local models only" in doc.lower()


def test_nothing_else_in_the_package_imports_this_module():
    root = Path(cf.__file__).resolve().parents[1]
    offenders = [str(p) for p in root.rglob("*.py") if p.name != "company_facts.py" and "company_facts" in p.read_text()]
    assert offenders == []


def test_the_module_has_no_dependency_beyond_the_standard_library_and_the_package():
    import ast
    tree = ast.parse(Path(cf.__file__).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add((node.module or "").split(".")[0])
    import sys
    assert names <= set(sys.stdlib_module_names) | {"valedia_trader"}, names


# ======================================================================================================================
# rev 2: repairs after the blind check
# ======================================================================================================================

# ---- R1. a store is created only where nothing is, and opened only when it is this store

def _good_store(dest, tmp_path):
    side = tmp_path / "side"
    side.mkdir(exist_ok=True)
    run(side, ["AAA"], name="good.sqlite")
    shutil.copy(side / "good.sqlite", dest)


def _sql(path, *statements):
    conn = sqlite3.connect(path)
    for st in statements:
        conn.execute(st)
    conn.commit()
    conn.close()


def _mk_prices(p, t):
    _sql(p, "CREATE TABLE prices(day TEXT, close REAL)", "INSERT INTO prices VALUES('2026-11-13', 1.0)")


def _mk_empty_sqlite(p, t):
    _sql(p, "CREATE TABLE x(a)", "DROP TABLE x")


def _mk_conflicting(p, t):
    _sql(p, "CREATE TABLE observations(a, b)", "INSERT INTO observations VALUES(1, 2)")


def _mk_unmarked_with_our_tables(p, t):
    conn = sqlite3.connect(p)
    cf.create_schema(conn)
    conn.commit()
    conn.close()


def _mk_good_then(*statements):
    def make(p, t):
        _good_store(p, t)
        _sql(p, *statements)
    return make


def _mk_wal_foreign(p, t):
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE prices(day TEXT)")
    conn.commit()
    conn.close()


def _mk_truncated(p, t):
    _good_store(p, t)
    p.write_bytes(p.read_bytes()[:60])


def _mk_symlink(p, t):
    _good_store(t / "side" / "real.sqlite", t)
    os.symlink(t / "side" / "real.sqlite", p)


def _live_copy(src, dest, *statements):
    """Run `statements` on `src` in WAL mode with checkpoints off and copy it WITH its live -wal and -shm to `dest` while
    the connection is still open: `dest` then has a write-ahead file whose changes are not in its main file."""
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    for st in statements:
        conn.execute(st)
    conn.commit()
    for suffix in ("", "-wal", "-shm"):
        shutil.copy2(str(src) + suffix, str(dest) + suffix)
    conn.close()


def _mk_mark_other_tables_live(p, t):
    src = t / "side" / "marked_other.sqlite"
    src.parent.mkdir(exist_ok=True)
    _sql(src, "PRAGMA application_id=%d" % cf.APPLICATION_ID, "PRAGMA user_version=%d" % cf.USER_VERSION)
    _live_copy(src, p, "CREATE TABLE prices(day TEXT, close REAL)", "INSERT INTO prices VALUES('2026-11-13', 1.0)")


def _mk_extra_table_only_in_the_live_wal(p, t):
    src = t / "side" / "good_live.sqlite"
    _good_store(src, t)
    _live_copy(src, p, "CREATE TABLE extra(x)", "INSERT INTO extra VALUES(1)")


def _mk_cut(n):
    def make(p, t):
        side = t / "side"
        side.mkdir(exist_ok=True)
        run(side, ["AAA", "BBB", "CCC"], name="cut.sqlite")
        p.write_bytes((side / "cut.sqlite").read_bytes()[:-n])
    return make


def _mk_shm_symlink(p, t):
    _good_store(p, t)
    victim = t / "side" / "victim.bin"
    victim.write_bytes(b"not a shared-memory file")
    os.symlink(victim, str(p) + "-shm")


FOREIGN = {
    "another_programs_database": _mk_prices,
    "empty_sqlite_file": _mk_empty_sqlite,
    "zero_byte_file": lambda p, t: p.write_bytes(b""),
    "text_file": lambda p, t: p.write_text("not a database, just words\n" * 20),
    "conflicting_observations_table": _mk_conflicting,
    "our_tables_but_no_mark": _mk_unmarked_with_our_tables,
    "other_programs_application_id": lambda p, t: (_mk_unmarked_with_our_tables(p, t), _sql(p, "PRAGMA application_id=305419896")),
    "other_application_id_with_our_version_and_tables": lambda p, t: (_mk_unmarked_with_our_tables(p, t), _sql(
        p, "PRAGMA application_id=305419896", "PRAGMA user_version=%d" % cf.USER_VERSION)),
    "store_of_a_later_version": _mk_good_then("PRAGMA user_version=3"),          # T36a D6: this revision reads 1 and 2
    "our_application_id_version_zero": _mk_good_then("PRAGMA user_version=0"),
    "our_mark_but_an_extra_column": _mk_good_then("ALTER TABLE news ADD COLUMN extra TEXT"),
    "our_mark_but_a_table_missing": _mk_good_then("DROP TABLE runs"),
    "foreign_database_already_in_wal_mode": _mk_wal_foreign,
    "sixteen_byte_header_only": lambda p, t: p.write_bytes(b"SQLite format 3\x00"),
    "truncated_below_a_header": _mk_truncated,
    "binary_garbage": lambda p, t: p.write_bytes(bytes(range(256)) * 20),
    "a_directory": lambda p, t: p.mkdir(),
    "a_symlink_to_a_good_store": _mk_symlink,
    # rev 3 (D3, D6, D7): refused, and left exactly as found, side files included
    "our_mark_other_tables_with_live_side_files": _mk_mark_other_tables_live,
    "our_store_with_an_extra_table_only_in_its_live_wal": _mk_extra_table_only_in_the_live_wal,
    "our_store_with_an_extra_table": _mk_good_then("CREATE TABLE extra(x)"),
    "our_store_with_a_view": _mk_good_then("CREATE VIEW v AS SELECT 1 AS one"),
    "our_store_cut_by_1_byte": _mk_cut(1),
    "our_store_cut_by_100_bytes": _mk_cut(100),
    "our_store_cut_by_4096_bytes": _mk_cut(4096),
    "our_store_whose_shm_is_a_symlink": _mk_shm_symlink,
}


@pytest.mark.parametrize("name", list(FOREIGN))
def test_a_foreign_file_is_refused_before_any_write_of_any_kind(tmp_path, name, capsys):
    fo = tmp_path / "fo"
    fo.mkdir()
    path = fo / "cf.sqlite"
    FOREIGN[name](path, tmp_path)
    before = snap(fo)
    fetch = Finnhub(Clock())
    with pytest.raises(cf.Refused) as e:
        run(fo, ["AAA"], fetch=fetch)
    assert fetch.calls == [] and "\n" not in str(e.value)
    for call in (lambda: cf.as_of(path, "AAA", "profile", T0 + 1), lambda: cf.news_as_of(path, "AAA", T0 + 1), lambda: cf.status(path)):
        with pytest.raises(cf.Refused):
            call()
    assert cf.main(["collect", "--symbols", "AAA", "--db", str(path)], fetch=fetch, credentials=lambda n: KEY) == 2
    assert cf.main(["status", "--db", str(path)]) == 2
    assert cf.main(["show", "AAA", "--db", str(path)]) == 2
    capsys.readouterr()
    assert fetch.calls == [] and snap(fo) == before          # bytes, modification times, and no -wal, -shm or lock file


def test_a_store_is_created_where_nothing_is_with_its_own_mark_and_no_stray_file(tmp_path):
    sub = tmp_path / "new" / "deeper"
    result, _, _ = run(sub, ["AAA"])
    assert result.failed == 0
    assert rows(sub, "PRAGMA application_id") == [(cf.APPLICATION_ID,)]
    assert rows(sub, "PRAGMA user_version") == [(cf.USER_VERSION,)]
    assert rows(sub, "PRAGMA journal_mode") == [("wal",)]
    assert sorted(p.name for p in sub.iterdir()) == ["cf.sqlite", "cf.sqlite.lock"]
    again, _, _ = run(sub, ["AAA"], clock=Clock(T0 + 10))                         # and a store with the mark opens again
    assert again.failed == 0 and again.unchanged >= 6


def test_a_store_that_appears_while_a_new_one_is_being_made_is_not_replaced(tmp_path, monkeypatch):
    path = tmp_path / "cf.sqlite"
    real_link = os.link

    def racing_link(src, dst, *a, **k):                    # another program makes the file in the moment between
        Path(dst).write_bytes(b"someone else's file")
        return real_link(src, dst, *a, **k)
    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(cf.Refused):
        run(tmp_path, ["AAA"])
    assert path.read_bytes() == b"someone else's file"
    assert sorted(p.name for p in tmp_path.iterdir() if ".new" in p.name) == []


# ---- R2. no answer can stop a later run

def _article_with(time_value, i=1):
    a = article(i, 0)
    if time_value == "missing":
        del a["datetime"]
    else:
        a["datetime"] = time_value
    return a


@pytest.mark.parametrize("value, usable", [
    (T0 - 60, True), (946684800, True), (946684799, False), (T0 + 2 * 86400, True), (T0 + 2 * 86400 + 1, False),
    (1763121600000, False), (1.7e12, False), (1e300, False), (10 ** 30, False), (-5, False), (0, False), (1e-300, False),
    ("2026-11-12", False), (True, False), (None, False), ([], False), ({}, False), ("missing", False)])
def test_an_article_time_is_usable_only_between_2000_and_two_days_after_the_fetch(tmp_path, value, usable):
    clock = Clock()
    fetch = Finnhub(clock, {("/company-news", "AAA"): [[_article_with(value)]]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert rows(tmp_path, "SELECT count(*) FROM news") == [(1 if usable else 0,)]
    assert result.skipped == (0 if usable else 1) and result.failed == 0
    later, _, _ = run(tmp_path, ["AAA", "BBB"], fetch=fetch, clock=Clock(T0 + 5))     # and the next run is not stopped by it
    assert later.stopped is None and later.failed == 0


def test_an_article_without_a_usable_id_is_skipped_and_counted(tmp_path):
    clock = Clock()
    bad = [{"datetime": T0 - 1}, {"id": True, "datetime": T0 - 1}, {"id": [], "datetime": T0 - 1}, {"id": "", "datetime": T0 - 1},
           "text", None, 5, {"id": "\ud800", "datetime": T0 - 1}, article(2, T0 - 1, headline="\ud800bad")]
    result, _, _ = run(tmp_path, ["AAA"], fetch=Finnhub(clock, {("/company-news", "AAA"): [bad]}), clock=clock)
    assert rows(tmp_path, "SELECT count(*) FROM news") == [(0,)] and result.skipped == 9 and result.failed == 0


def test_a_store_already_holding_a_row_with_an_impossible_time_does_not_stop_a_run(tmp_path):
    run(tmp_path, ["AAA"])
    _sql(tmp_path / "cf.sqlite",
         "INSERT INTO news VALUES('AAA','h1',1763121600000.0,1.0,'h','s','u','m')",
         "INSERT INTO news VALUES('AAA','h2',1e300,1.0,'h','s','u','m')",
         "INSERT INTO news VALUES('AAA','h3',-5.0,1.0,'h','s','u','m')",
         "INSERT INTO news VALUES('AAA','h4','soon',1.0,'h','s','u','m')",
         "INSERT INTO news VALUES('BBB','h5',1e300,1.0,'h','s','u','m')",
         "INSERT INTO news VALUES('BBB','h6',5.0,'soon','h','s','u','m')",
         "INSERT INTO observations VALUES('AAA','metrics','later','x','{}')")
    clock = Clock(T0 + 100)
    result, fetch, _ = run(tmp_path, ["AAA", "BBB", "CCC"], clock=clock)
    assert result.stopped is None and result.failed == 0
    assert fetch.paths("CCC") == [PATHS[k] for k in SIX]
    news = {c.symbol: c for c in fetch.calls if c.path == "/company-news"}
    assert news["AAA"].query["from"] <= news["AAA"].query["to"] and news["BBB"].query["from"] == "2026-10-15"
    again, _, _ = run(tmp_path, ["AAA", "BBB", "CCC"], clock=Clock(T0 + 200))
    assert again.stopped is None


def test_an_exception_while_handling_one_symbol_and_kind_is_counted_failed_and_the_run_goes_on(tmp_path, monkeypatch):
    original = cf._Run._store

    def bad_store(self, kind, symbol, value, canon):
        if symbol == "BBB" and kind == "profile":
            raise RuntimeError("boom " + KEY)
        return original(self, kind, symbol, value, canon)

    def bad_url(self, symbol):
        if symbol == "BBB":
            raise OverflowError("year 57841 is out of range " + KEY)
        return "https://finnhub.io/api/v1/company-news?symbol=%s&from=2026-10-15&to=2026-11-14" % symbol
    monkeypatch.setattr(cf._Run, "_store", bad_store)
    monkeypatch.setattr(cf._Run, "_news_url", bad_url)
    result, fetch, _ = run(tmp_path, ["AAA", "BBB", "CCC"])
    assert result.failed == 2 and result.stopped is None
    assert sorted(result.failures) == [("news", "error OverflowError"), ("profile", "error RuntimeError")]
    assert fetch.paths("CCC") == [PATHS[k] for k in SIX] and KEY not in repr(result)
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE symbol='CCC'") == [(5,)]


def _hostile(rnd, kind):
    times = [1763121600000, 1.7e12, 1e300, -5, 0, "2026-01-01", True, None, [], {}, 10 ** 30, 946684799, 1e-300, T0 + 2 * 86400 + 1]
    ids = [None, True, 1.5, 10 ** 40, "", "x" * 10000, [], {"a": 1}, -1, 0, "∑", "\ud800", "i" * 300]
    texts = [None, 5, "\ud800", "x" * 100000, "\u0000", [], {"k": "v"}, "\U0001F600", ""]
    r = rnd.random()
    if r < 0.3:
        deep = lambda n: ("[" * n + "]" * n).encode()                            # noqa: E731
        return rnd.choice([b"null", b"12", b'"s"', b"true", b"[]", b"{}", b'[1,2,{"a":[null]}]', b'{"a":{"b":{"c":[1,2,3]}}}',
                           deep(10), deep(500), deep(5000), deep(100000), b'{"a":' + deep(900) + b"}", b'[' + b'{"id":1},' * 20000 + b'{}]'])
    if kind == "news":
        items = []
        for _ in range(rnd.randint(1, 6)):
            items.append({"id": rnd.choice(ids + [rnd.randint(1, 10 ** 6)]), "datetime": rnd.choice(times + [T0 - 5]),
                          "headline": rnd.choice(texts), "source": rnd.choice(texts), "url": rnd.choice(texts), "summary": rnd.choice(texts)})
        return json.dumps(items).encode()
    body = {rnd.choice(["a", "\ud800", "k" * 1000, "\u0000"]): rnd.choice(texts + times + [[[[]]], {"x": {"y": {}}}, 10 ** 400 % 7])}
    return json.dumps(body if kind not in ("earnings", "recommendations") else [body]).encode()


def test_300_hostile_answers_mixed_with_ordinary_ones_never_stop_a_run_or_a_later_run(tmp_path):
    rnd = random.Random(2026)
    hostile_count = 0
    for r in range(60):
        clock = Clock(T0 + r * 1000)
        script, hostile, ordinary = {}, [], []
        for i in range(10):
            sym = "H%d" % i if i < 5 else "O%d" % i
            if i < 5:
                kind = rnd.choice(SIX)
                script[(PATHS[kind], sym)] = [_hostile(rnd, kind)]
                hostile.append(sym)
                hostile_count += 1
            else:
                ordinary.append(sym)
        fetch = Finnhub(clock, script)
        db = "fuzz%d.sqlite" % r
        for again in range(2):                                                  # the run, and a later run on the same store
            before = len(fetch.calls)
            result, _, _ = run(tmp_path, hostile + ordinary, fetch=fetch, clock=Clock(T0 + r * 1000 + again * 100), name=db)
            assert result.stopped is None and result.ended_at is not None, (r, result)
            asked = fetch.calls[before:]
            for sym in ordinary:
                assert [c.path for c in asked if c.symbol == sym] == [PATHS[k] for k in SIX]
            assert not any(KEY in repr(f) for f in result.failures)
        assert rows(tmp_path, "SELECT count(*) FROM news WHERE published_at < 946684800 OR published_at > ?", (T0 + r * 1000 + 100 + 2 * 86400,), name=db) == [(0,)]
        assert rows(tmp_path, "SELECT count(*) FROM runs WHERE ended_at IS NULL", name=db) == [(0,)]
    assert hostile_count == 300


# ---- R3. the rate rule holds in true time (a monotonic clock)

def _no_more_than_55_in_60(ts):
    j = 0
    for i in range(len(ts)):
        while ts[i] - ts[j] > 60:
            j += 1
        if i - j + 1 > 55:
            return False
    return True


class Stepping(Finnhub):
    """A fetch that takes time on the fake clock (both clocks) and sometimes steps the WALL clock; it records true time."""
    def __init__(self, world, rnd, spacing=(0, 0, 0, 0.2, 1.08, 3)):
        super().__init__(world.monotonic)
        self.world, self.rnd, self.spacing = world, rnd, spacing

    def __call__(self, url, headers):
        out = super().__call__(url, headers)
        dt = self.rnd.choice(self.spacing)
        self.world.now += dt
        self.world.mono += dt
        if self.rnd.random() < 0.15:
            self.world.now += self.rnd.choice([-7200, -30, 45, 3600, 86400])      # the wall clock only
        return out


def test_requests_1_08_seconds_apart_are_never_56_in_60_seconds(tmp_path):
    world = Clock()
    fetch = Stepping(world, random.Random(1), spacing=(1.08,))
    run(tmp_path, ["S%02d" % i for i in range(30)], fetch=fetch, clock=world)
    ts = [c.t for c in fetch.calls]
    assert len(ts) == 181 and _no_more_than_55_in_60(ts)
    assert max(b - a for a, b in zip(ts, ts[1:])) > 1.08                           # it did have to wait


def test_100_schedules_with_forward_and_backward_wall_clock_steps_never_exceed_55_in_60_seconds(tmp_path):
    for seed in range(100):
        world = Clock()
        fetch = Stepping(world, random.Random(seed))
        run(tmp_path, ["S%02d" % i for i in range(25)], fetch=fetch, clock=world, name="sched%d.sqlite" % seed)
        ts = [c.t for c in fetch.calls]
        assert len(ts) > 100 and _no_more_than_55_in_60(ts), seed


def test_a_wall_clock_jump_forward_does_not_let_a_burst_through(tmp_path):
    world = Clock()
    seen = []

    def fetch(url, headers):
        seen.append(world.monotonic())
        if len(seen) == 55:
            world.now += 3600                                                     # a clock correction, or a wake from sleep
        return Finnhub(world)(url, headers)
    cf.collect(["S%d" % i for i in range(12)], tmp_path / "cf.sqlite", fetch=fetch, clock=world, sleep=world.sleep,
               credentials=lambda n: KEY, monotonic=world.monotonic)
    assert _no_more_than_55_in_60(seen) and seen[55] - seen[0] >= 60


# ---- R4. readers work on read-only places and write nothing

def test_a_store_in_a_read_only_folder_and_a_read_only_store_file_can_be_read_and_nothing_changes(tmp_path, capsys):
    for variant in ("folder", "file"):
        d = tmp_path / variant
        d.mkdir()
        run(d, ["AAA"])
        target = d / "cf.sqlite"
        os.chmod(d, 0o555) if variant == "folder" else os.chmod(target, 0o444)
        try:
            before = snap(d)
            assert cf.as_of(target, "AAA", "profile", T0 + 1)["body"]["ticker"] == "AAA"
            assert len(cf.news_as_of(target, "AAA", T0 + 1)) == 2
            assert cf.status(target)["rows"]["profile"] == 1
            assert cf.main(["show", "AAA", "--kind", "profile", "--db", str(target), "--as-of", "2026-11-14T13:00:00Z"]) == 0
            assert cf.main(["status", "--db", str(target)]) == 0
            capsys.readouterr()
            after = snap(d)
            if variant == "folder":
                assert after == before
            else:                       # rev 3 (D1): a writable folder may gain SQLite's side files; the store itself is unchanged
                assert {k: v for k, v in after.items() if k not in ("cf.sqlite-wal", "cf.sqlite-shm")} == before
        finally:
            os.chmod(d, 0o755)
            os.chmod(target, 0o644)


def test_a_store_that_cannot_be_read_is_refused_and_the_message_says_why(tmp_path):
    run(tmp_path, ["AAA"])
    p = tmp_path / "cf.sqlite"
    p.write_bytes(p.read_bytes()[:4096])                                          # the header page only: the rest is gone
    before = snap(tmp_path)
    with pytest.raises(cf.Refused) as e:
        cf.as_of(p, "AAA", "profile", T0 + 1)
    assert "malformed" in str(e.value) or "damaged" in str(e.value)
    assert snap(tmp_path) == before


def test_a_reader_never_returns_a_wrong_answer_while_a_collector_writes_in_a_read_only_folder(tmp_path):
    seen = {}
    db = tmp_path / "cf.sqlite"
    clock = Clock()
    inner = Finnhub(clock)

    def fetch(url, headers):
        out = inner(url, headers)
        if len(inner.calls) == 4 and "outcome" not in seen:                       # calendar and profile/metrics/earnings are committed
            os.chmod(tmp_path, 0o555)
            try:
                seen["outcome"] = cf.as_of(db, "AAA", "profile", T0 + 1)
            except cf.Refused as e:
                seen["outcome"] = e
            finally:
                os.chmod(tmp_path, 0o755)
        return out
    cf.collect(["AAA"], db, fetch=fetch, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY, monotonic=clock.monotonic)
    out = seen["outcome"]
    assert isinstance(out, cf.Refused) or (out is not None and out["body"]["ticker"] == "AAA")      # refused, or the committed truth
    if isinstance(out, cf.Refused):
        assert "read-only" in str(out) and "\n" not in str(out)


# ---- R5. the key in any form

def _key_bodies(key):
    b = key.encode()
    b64, b64u, hx = base64.b64encode(b).decode(), base64.urlsafe_b64encode(b).decode().rstrip("="), b.hex()
    return {
        "plain": json.dumps({"note": key}).encode(),
        "embedded": json.dumps({"note": "see " + key + " here"}).encode(),
        "unicode_escapes": ('{"note":"' + "".join("\\u%04x" % ord(c) for c in key) + '"}').encode(),
        "as_a_member_name": json.dumps({key: 1}).encode(),
        "base64": json.dumps({"note": b64}).encode(),
        "base64_url_safe": json.dumps({"note": b64u}).encode(),
        "base64_at_offset_1": json.dumps({"note": base64.b64encode(b"x" + b).decode()[2:]}).encode(),
        "base64_at_offset_2": json.dumps({"note": base64.b64encode(b"xy" + b).decode()[3:]}).encode(),
        "hex": json.dumps({"note": hx}).encode(),
        "hex_upper": json.dumps({"note": hx.upper()}).encode(),
    }


QUOTED_KEY = 'Ab"Cd\\Ef-1234567890xyzABCDEFGH'


@pytest.mark.parametrize("form", list(_key_bodies(QUOTED_KEY)))
def test_a_key_echoed_in_any_form_is_refused_counted_failed_and_not_stored(tmp_path, form):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [_key_bodies(QUOTED_KEY)[form]]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock, credentials=lambda n: QUOTED_KEY)
    assert result.failed == 1 and result.failures == [("profile", "key in answer")]
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='profile'") == [(0,)]
    blob = everything_in(tmp_path)
    for text in (QUOTED_KEY, json.dumps(QUOTED_KEY)[1:-1], base64.b64encode(QUOTED_KEY.encode()).decode()[:24]):
        assert text.encode() not in blob


def test_a_word_like_key_does_not_refuse_honest_answers_but_its_full_text_is_still_refused(tmp_path):
    clock = Clock()
    result, _, _ = run(tmp_path, ["AAA"], clock=clock, credentials=lambda n: "headlinesummary")
    assert result.failed == 0 and rows(tmp_path, "SELECT count(*) FROM news") == [(2,)]
    fetch = Finnhub(clock, {("/stock/profile2", "BBB"): [{"note": "xx headlinesummary xx"}]})
    result, _, _ = run(tmp_path, ["BBB"], fetch=fetch, clock=Clock(T0 + 10), credentials=lambda n: "headlinesummary")
    assert result.failures == [("profile", "key in answer")]


def test_the_half_key_rule_applies_only_to_halves_of_at_least_12_characters(tmp_path):
    long_key, short_key = "L" * 3 + "0123456789abcdefghijklmnopqrstu", "S0123456789abcdefghi"             # 34 and 20 characters
    for key, half_len, refused in ((long_key, 17, True), (short_key, 10, False)):
        clock = Clock()
        fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [{"note": "x " + key[:half_len] + " y"}]})
        result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock, credentials=lambda n, key=key: key, name="k%d.sqlite" % len(key))
        assert (result.failed == 1) is refused


# ---- R6. only whole, well-shaped answers are stored

def test_a_body_shorter_than_its_declared_length_is_a_failure_not_an_answer():
    handler = FakeHTTPS({U: (200, {"Content-Length": "100"}, b'{"a":1}')})
    with pytest.raises(ValueError):
        cf.http_fetch(U, HDR, opener=cf._build_opener(handler))
    handler = FakeHTTPS({U: (200, {"Content-Length": "7"}, b'{"a":1}')})
    assert cf.http_fetch(U, HDR, opener=cf._build_opener(handler))[0] == b'{"a":1}'       # rev 3 (D4): (body, headers)
    handler = FakeHTTPS({U: (200, {"Content-Length": "7x"}, b'{"a":1}')})
    assert cf.http_fetch(U, HDR, opener=cf._build_opener(handler))[0] == b'{"a":1}'       # an unreadable length is not a claim


SHAPES = {"profile": dict, "metrics": dict, "earnings": list, "recommendations": list, "insiders": dict, "news": list, "earnings_calendar": dict}


def test_the_module_states_one_expected_top_level_type_per_kind():
    assert dict(cf.EXPECTED_TYPES) == {k: v for k, v in SHAPES.items()}
    for kind in SHAPES:
        assert kind in " ".join((cf.__doc__ or "").split())


WRONG_TYPES = [(kind, wrong) for kind, expected in SHAPES.items()
               for wrong in (b'"text"', b"12", b"null", b"true", b'{"a":1}' if expected is list else b"[1]")]


@pytest.mark.parametrize("kind, wrong", WRONG_TYPES)
def test_an_answer_of_another_top_level_type_is_refused_and_counted_failed(tmp_path, kind, wrong):
    path = {"earnings_calendar": "/calendar/earnings"}.get(kind) or PATHS[kind]
    clock = Clock()
    fetch = Finnhub(clock, {(path, None): [wrong]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.failed == 1 and result.failures == [(kind, "wrong type")]
    table = "news" if kind == "news" else "observations WHERE kind='%s'" % kind
    assert rows(tmp_path, "SELECT count(*) FROM " + table) == [(0,)]


# ---- R7. hand-made rows are refused, never crashed on

@pytest.mark.parametrize("body", ['{"a":NaN}', '{"a":1,"a":2}', "not json", "", '{"a":Infinity}', '{"a":1}extra'])
def test_a_stored_body_that_is_not_strict_json_is_refused_even_with_a_matching_hash(body):
    conn = sqlite3.connect(":memory:")
    make_store(conn)
    conn.execute("INSERT INTO observations VALUES ('AAA','profile',?,?,?)", (B + 5, cf.body_sha256(body), body))
    with pytest.raises(cf.Refused):
        cf.as_of(conn, "AAA", "profile", B + 10)


@pytest.mark.parametrize("body", [sqlite3.Binary(b'{"a":1}'), 5, 2.5])
def test_a_stored_body_that_is_not_text_is_refused_not_crashed_on(body):
    conn = sqlite3.connect(":memory:")
    make_store(conn)
    conn.execute("INSERT INTO observations VALUES ('AAA','profile',?,'x',?)", (B + 5, body))
    with pytest.raises(cf.Refused):
        cf.as_of(conn, "AAA", "profile", B + 10)


def test_rows_with_a_time_that_is_not_a_number_are_never_returned():
    conn = sqlite3.connect(":memory:")
    make_store(conn)
    body = cf.canonical_json({"v": 1})
    conn.execute("INSERT INTO observations VALUES ('AAA','profile','soon',?,?)", (cf.body_sha256(body), body))
    conn.execute("INSERT INTO news VALUES ('AAA','1','soon',5,'h','s','u','m')")
    conn.execute("INSERT INTO news VALUES ('AAA','2',5,'soon','h','s','u','m')")
    assert cf.as_of(conn, "AAA", "profile", 10) is None and cf.news_as_of(conn, "AAA", 10) == []


# ---- R8. one line, always

@pytest.mark.parametrize("argv", [["collect", "--symbols", "AAA", "--universe", "stocks"], ["frobnicate"], [], ["collect", "--symbols", "-A"],
                                  ["show"], ["collect", "--universe", "bonds"], ["status", "--nope"], ["collect", "--max-calls"],
                                  ["collect", "--db"], ["show", "AAA", "BBB"]])
def test_every_refusal_of_the_command_line_including_the_parsers_is_one_line_and_exit_2(capsys, argv):
    rc = cf.main(argv)
    out = capsys.readouterr()
    assert rc == 2 and out.out == "" and len(out.err.strip().splitlines()) == 1 and out.err.startswith("company_facts: refused:")


# ---- R9. a killed run is visible

def test_a_killed_runs_row_is_closed_as_interrupted_at_the_next_run_and_status_counts_it(tmp_path, capsys):
    run(tmp_path, ["AAA"])
    _sql(tmp_path / "cf.sqlite", "INSERT INTO runs VALUES (%r, NULL, 0, 0, 0, 0, '')" % (T0 - 500))
    assert cf.status(tmp_path / "cf.sqlite")["interrupted"] == 0                  # still open: not yet known to be dead
    run(tmp_path, ["AAA"], clock=Clock(T0 + 3600))
    got = rows(tmp_path, "SELECT started_at, ended_at, calls, stored, unchanged, failed, kinds_refused FROM runs ORDER BY rowid")
    assert got[1] == (T0 - 500, None, 0, 0, 0, 0, cf.INTERRUPTED) and got[0][1] is not None and got[2][1] is not None
    run(tmp_path, ["AAA"], clock=Clock(T0 + 7200))
    assert cf.status(tmp_path / "cf.sqlite")["interrupted"] == 1                  # once, not again
    assert cf.main(["status", "--db", str(tmp_path / "cf.sqlite")]) == 0
    assert "interrupted runs: 1" in capsys.readouterr().out


# ======================================================================================================================
# rev 3: the reader and the creation path, with the design decided (D1 to D8)
# ======================================================================================================================

@pytest.fixture
def uris(monkeypatch):
    """Every SQLite URI opened while the test runs (the readers open the store by URI)."""
    seen = []
    real = sqlite3.connect

    def spy(*a, **k):
        target = a[0] if a else k.get("database")
        if isinstance(target, str) and target.startswith("file:"):
            seen.append(target)
        return real(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", spy)
    return seen


def _read_everything(db, capsys=None):
    """Every reader once: as_of, news_as_of, status, and the command line's status and show."""
    out = {"profile": cf.as_of(db, "AAA", "profile", T0 + 1), "news": cf.news_as_of(db, "AAA", T0 + 1), "status": cf.status(db)}
    assert cf.main(["status", "--db", str(db)]) == 0
    assert cf.main(["show", "AAA", "--db", str(db), "--as-of", "2026-11-14T13:00:00Z"]) == 0
    if capsys is not None:
        capsys.readouterr()
    return out


# ---- outcome 1 (D1): no torn or stale read beside a real collector, no lock probe, no immutable mode in a writable folder

def test_a_reader_never_opens_or_locks_the_collectors_lock_file(tmp_path, monkeypatch, capsys):
    run(tmp_path, ["AAA"])
    lock = tmp_path / "cf.sqlite.lock"
    before = lock.stat()
    opened, locked = [], []
    real_open, real_flock = os.open, fcntl.flock
    monkeypatch.setattr(os, "open", lambda p, *a, **k: (opened.append(os.fspath(p)), real_open(p, *a, **k))[1])
    monkeypatch.setattr(fcntl, "flock", lambda fd, op: (locked.append(op), real_flock(fd, op))[1])
    _read_everything(tmp_path / "cf.sqlite", capsys)
    assert not [p for p in opened if p.endswith(".lock")] and locked == []
    after = lock.stat()
    assert (after.st_mtime_ns, after.st_size, after.st_ino) == (before.st_mtime_ns, before.st_size, before.st_ino)


def test_a_reader_in_a_writable_folder_reads_in_ordinary_read_only_mode_never_immutable(tmp_path, uris, capsys):
    run(tmp_path, ["AAA"])
    got = _read_everything(tmp_path / "cf.sqlite", capsys)                     # no side file beside the store yet
    assert got["profile"]["body"]["ticker"] == "AAA" and len(got["news"]) == 2
    _read_everything(tmp_path / "cf.sqlite", capsys)                           # and with the side files a reader leaves
    assert uris and all("mode=ro" in u and "immutable" not in u for u in uris), uris


COLLECTOR_CHILD = r'''
import json, random, sqlite3, sys
from valedia_trader.data_and_fees import company_facts as cf
db, t0, key = sys.argv[1], float(sys.argv[2]), sys.argv[3]
state = {"next": int(sys.argv[4]), "run": 0, "now": t0, "mono": 0.0}
rnd = random.Random(31)

def clock():
    state["now"] += 0.5
    return state["now"]

def monotonic():
    state["mono"] += 0.001
    return state["mono"]

def fetch(url, headers):
    if "company-news" in url:
        ids = range(state["next"], state["next"] + state["per"])
        state["next"] += state["per"]
        body = [{"id": "a%07d" % i, "datetime": t0 - rnd.uniform(60, 20 * 86400), "headline": "h%d" % i, "source": "w",
                 "url": "u", "summary": "s" * 40} for i in ids]
    elif "profile2" in url:
        body = {"v": state["run"]}
    elif "calendar" in url:
        body = {"earningsCalendar": [{"run": state["run"]}]}
    elif "stock/earnings" in url or "recommendation" in url:
        body = [{"run": state["run"]}]
    else:
        body = {"run": state["run"]}
    return json.dumps(body).encode(), {}

print("ready", flush=True)
for line in sys.stdin:
    words = line.split()
    if not words or words[0] != "run":
        break
    state["run"] += 1
    state["per"] = int(words[1])
    result = cf.collect(["AAA"], db, fetch=fetch, clock=clock, sleep=lambda s: None, credentials=lambda n: key,
                        monotonic=monotonic)
    conn = sqlite3.connect(db)                       # a checkpoint, as a collector's close makes when it is the last one
    ckpt = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    conn.close()
    print("done", state["run"], state["next"] - 1, result.failed, result.stopped, *ckpt, flush=True)
'''


def _consecutive_ids(articles):
    ids = [int(a["article_id"][1:]) for a in articles]
    return len(ids) if ids and len(set(ids)) == len(ids) and min(ids) == 1 and max(ids) == len(ids) else None


def test_300_reads_beside_a_real_collector_are_each_a_committed_state_never_torn_or_stale(tmp_path, monkeypatch, capsys):
    """A second process collects (starting, committing, checkpointing and finishing) while this one reads through as_of,
    news_as_of, status and show. Each read must be one state the collector committed, and not older than the newest one
    committed before the read began. Some reads are held in the middle (inside SQLite) while the collector starts and
    makes whole runs that end in a checkpoint; such a read must still answer the state of its own beginning."""
    live = tmp_path / "live"
    live.mkdir()
    db = live / "cf.sqlite"
    n0 = 20000
    first = [{"id": "a%07d" % i, "datetime": T0 - 86400 - (i * 37) % (20 * 86400), "headline": "h%d" % i, "source": "w",
              "url": "u", "summary": "s" * 40} for i in range(1, n0 + 1)]
    clock = Clock()
    run(live, ["AAA"], clock=clock, fetch=Finnhub(clock, {("/company-news", "AAA"): [first], ("/stock/profile2", "AAA"): [{"v": 0}]}))
    root = Path(cf.__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen([sys.executable, "-c", COLLECTOR_CHILD, str(db), repr(T0 + 100), KEY, str(n0 + 1)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env, cwd=str(root))
    committed = [n0]                     # the news count after each committed run, in order (run 0 is the parent's)
    hold = {"action": None, "at": 10 ** 9, "max": 0}
    real_connect = sqlite3.connect

    def connect(*a, **k):                # holds a reader inside SQLite (a progress handler) when asked to
        conn = real_connect(*a, **k)
        target = a[0] if a else k.get("database")
        if isinstance(target, str) and target.startswith("file:") and hold["action"] is not None:
            calls = [0]

            def handler():
                calls[0] += 1
                hold["max"] = max(hold["max"], calls[0])
                if calls[0] == hold["at"] and hold["action"] is not None:
                    action, hold["action"] = hold["action"], None
                    action()
                return 0
            conn.set_progress_handler(handler, 1000)
        return conn
    monkeypatch.setattr(sqlite3, "connect", connect)

    def send(per):
        child.stdin.write("run %d\n" % per)
        child.stdin.flush()

    def done():
        words = child.stdout.readline().split()
        assert words[:1] == ["done"] and words[3:5] == ["0", "None"], words      # the run ended, nothing failed
        committed.append(int(words[2]))
        return words

    def check_news(articles, allowed):
        m = _consecutive_ids(articles)
        assert m is not None and m in allowed, ("TORN or STALE", len(articles), m, sorted(allowed))
        return m

    tally = {"news": 0, "as_of": 0, "status": 0, "show": 0, "held": 0, "refused": 0}
    try:
        assert child.stdout.readline().startswith("ready")
        # how long one news read takes, in progress-handler calls, so a held read is held in its middle
        hold.update(action=lambda: None, at=10 ** 9, max=0)
        cf.news_as_of(db, "AAA", T0 + 10 ** 6)
        middle = max(2, hold["max"] // 3)
        # 1. a read that starts BEFORE the collector does and lasts past two of its runs and their checkpoints
        hold.update(action=lambda: (send(3000), done(), send(3000), done()), at=middle)
        assert check_news(cf.news_as_of(db, "AAA", T0 + 10 ** 6), {n0}) == n0
        assert len(committed) == 3 and hold["action"] is None
        tally["held"] += 1
        # 2. reads beside runs in flight; now and then a read held while the run in flight ends and a whole run follows
        inflight, per, reads, kinds = False, 0, 0, ("news", "as_of", "status", "show")
        while reads < 300:
            if not inflight:
                per = 100
                send(per)
                inflight = True
            runs_done = len(committed) - 1
            allowed_news = {committed[-1], committed[-1] + per} if inflight else {committed[-1]}
            allowed_v = {runs_done, runs_done + 1} if inflight else {runs_done}
            kind = kinds[reads % 4]
            try:
                if kind == "news" and reads % 20 == 0 and inflight:
                    hold.update(action=lambda: (done(), send(3000), done()), at=middle)
                    check_news(cf.news_as_of(db, "AAA", T0 + 10 ** 6), allowed_news)
                    assert hold["action"] is None
                    tally["held"] += 1
                    inflight = False
                elif kind == "news":
                    check_news(cf.news_as_of(db, "AAA", T0 + 10 ** 6), allowed_news)
                elif kind == "as_of":
                    assert cf.as_of(db, "AAA", "profile", T0 + 10 ** 6)["body"]["v"] in allowed_v
                elif kind == "status":
                    st = cf.status(db)
                    assert st["rows"]["news"] in allowed_news and st["rows"]["profile"] - 1 in allowed_v
                    if inflight and st["rows"]["news"] == committed[-1] + per:          # news commits after the profile
                        assert st["rows"]["profile"] - 1 == runs_done + 1
                else:
                    assert cf.main(["show", "AAA", "--kind", "profile", "--db", str(db), "--as-of", "2026-12-01T00:00:00Z"]) == 0
                    assert json.loads(capsys.readouterr().out)["body"]["v"] in allowed_v
                tally[kind] += 1
            except cf.Refused as e:
                assert "\n" not in str(e)
                tally["refused"] += 1
            reads += 1
            if inflight and select.select([child.stdout], [], [], 0)[0]:
                done()
                inflight = False
        if inflight:
            done()
    finally:
        monkeypatch.undo()
        if child.poll() is None:
            child.stdin.close()
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        size = sum(p.stat().st_size for p in live.iterdir())
        shutil.rmtree(live)                                                       # the store is deleted here
    assert child.returncode == 0 and reads == 300
    assert size < 200 * 1024 * 1024 and tally["refused"] <= 3, (size, tally)
    assert tally["held"] >= 5 and min(tally[k] for k in ("news", "as_of", "status", "show")) >= 60, tally


# ---- outcome 2 (D1): read-only places

def test_a_store_in_a_read_only_folder_without_wal_content_is_read_immutably_and_nothing_changes(tmp_path, uris, capsys):
    d = tmp_path / "ro"
    d.mkdir()
    run(d, ["AAA"])
    for left_by_a_reader in (False, True):
        if left_by_a_reader:                       # an earlier reader in the writable folder left an empty -wal and a -shm
            cf.as_of(d / "cf.sqlite", "AAA", "profile", T0 + 1)
            assert (d / "cf.sqlite-shm").exists() and (d / "cf.sqlite-wal").stat().st_size == 0
        os.chmod(d, 0o555)
        try:
            del uris[:]
            before = snap(d)
            got = _read_everything(d / "cf.sqlite", capsys)
            assert got["profile"]["body"]["ticker"] == "AAA" and len(got["news"]) == 2 and got["status"]["rows"]["profile"] == 1
            assert snap(d) == before and uris and all("immutable=1" in u for u in uris if "cf.sqlite" in u)
        finally:
            os.chmod(d, 0o755)


def test_a_store_in_a_read_only_folder_with_wal_content_is_refused_in_one_line_and_nothing_changes(tmp_path, capsys):
    side = tmp_path / "side"
    side.mkdir()
    run(side, ["AAA"])
    body = cf.canonical_json({"ticker": "AAA", "v": "only in the wal"})
    row = "INSERT INTO observations VALUES ('AAA','profile',%r,'%s','%s')" % (T0 + 50, cf.body_sha256(body), body)
    for with_shm in (True, False):
        d = tmp_path / ("ro_shm" if with_shm else "ro_no_shm")
        d.mkdir()
        _live_copy(side / "cf.sqlite", d / "cf.sqlite", row)
        if not with_shm:
            os.unlink(d / "cf.sqlite-shm")
        assert (d / "cf.sqlite-wal").stat().st_size > 32
        os.chmod(d, 0o555)
        try:
            before = snap(d)
            for call in (lambda: cf.as_of(d / "cf.sqlite", "AAA", "profile", T0 + 100), lambda: cf.news_as_of(d / "cf.sqlite", "AAA", T0 + 100),
                         lambda: cf.status(d / "cf.sqlite")):
                with pytest.raises(cf.Refused) as e:
                    call()
                text = str(e.value)
                assert "\n" not in text and "read-only" in text and "-wal" in text and "copy" in text, text
            for argv in (["status"], ["show", "AAA"]):
                assert cf.main(argv + ["--db", str(d / "cf.sqlite")]) == 2
                assert len(capsys.readouterr().err.strip().splitlines()) == 1
            assert snap(d) == before
        finally:
            os.chmod(d, 0o755)
        copy = tmp_path / ("copy_" + d.name)                       # and the advice works: copied to a writable place, it reads
        copy.mkdir()
        for p in d.iterdir():
            shutil.copy2(p, copy / p.name)
        assert cf.as_of(copy / "cf.sqlite", "AAA", "profile", T0 + 100)["body"]["v"] == "only in the wal"


def test_a_read_only_store_file_in_a_writable_folder_reads_correctly_and_its_bytes_do_not_change(tmp_path, capsys):
    run(tmp_path, ["AAA"])
    p = tmp_path / "cf.sqlite"
    os.chmod(p, 0o444)
    try:
        before = snap(tmp_path)["cf.sqlite"]
        got = _read_everything(p, capsys)
        assert got["profile"]["body"]["ticker"] == "AAA" and len(got["news"]) == 2
        assert snap(tmp_path)["cf.sqlite"] == before
    finally:
        os.chmod(p, 0o644)


# ---- outcome 3 (D2): creation cannot be steered

def _forced_temp_name(monkeypatch, tmp_path, name=None):
    """Make the module use a known temporary name (rev 2's predictable `<store>.new-<pid>` by default)."""
    name = name or "cf.sqlite.new-%d" % os.getpid()
    monkeypatch.setattr(cf, "_temp_name", lambda path: path.with_name(name), raising=False)
    return tmp_path / name


def test_the_temporary_file_is_made_by_the_module_exclusively_without_following_links_under_an_unpredictable_name(tmp_path, monkeypatch):
    made = []
    real_open = os.open

    def spy(p, flags, mode=0o777, *a, **k):
        if ".new-" in os.fspath(p) and flags & os.O_CREAT:
            made.append((os.fspath(p), flags, mode))
        return real_open(p, flags, mode, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    for name in ("a", "b"):
        run(tmp_path / name, ["AAA"])
    assert len(made) == 2
    for p, flags, mode in made:
        assert flags & os.O_CREAT and flags & os.O_EXCL and flags & os.O_NOFOLLOW and mode == 0o600
        assert re.fullmatch(r"cf\.sqlite\.new-[0-9a-f]{32}", Path(p).name) and str(os.getpid()) not in Path(p).name[14:]
    assert Path(made[0][0]).name != Path(made[1][0]).name
    assert sorted(x.name for x in (tmp_path / "a").iterdir()) == ["cf.sqlite", "cf.sqlite.lock"]


def _victims(t):
    v = t / "victims"
    v.mkdir(exist_ok=True)
    (v / "notes.txt").write_bytes(b"somebody's notes\n" * 50)
    _sql(v / "prices.db", "CREATE TABLE prices(day TEXT, close REAL)", "INSERT INTO prices VALUES('2026-11-13', 1.0)")
    if not (v / "store.sqlite").exists():
        _good_store(v / "store.sqlite", t)
    (v / "dir").mkdir(exist_ok=True)
    return v


PLANTS = {
    "an_empty_file": lambda p, v: p.write_bytes(b""),
    "a_file_with_bytes": lambda p, v: p.write_bytes(b"leftover bytes " * 100),
    "another_programs_database": lambda p, v: shutil.copy2(v / "prices.db", p),
    "a_good_store": lambda p, v: shutil.copy2(v / "store.sqlite", p),
    "a_symlink_to_a_file": lambda p, v: os.symlink(v / "notes.txt", p),
    "a_symlink_to_a_database": lambda p, v: os.symlink(v / "prices.db", p),
    "a_symlink_to_a_good_store": lambda p, v: os.symlink(v / "store.sqlite", p),
    "a_dangling_symlink": lambda p, v: os.symlink(v / "made_by_nobody.db", p),
    "a_symlink_to_a_directory": lambda p, v: os.symlink(v / "dir", p),
    "a_fifo": lambda p, v: os.mkfifo(p),
    "a_directory": lambda p, v: p.mkdir(),
    "a_hard_link_to_a_file": lambda p, v: os.link(v / "notes.txt", p),
    "a_hard_link_to_a_database": lambda p, v: os.link(v / "prices.db", p),
    "a_hard_link_to_a_good_store": lambda p, v: os.link(v / "store.sqlite", p),
    "a_symlink_to_a_symlink_to_a_database": lambda p, v: (os.symlink(v / "prices.db", v / "chain.db"), os.symlink(v / "chain.db", p)),
    "a_read_only_file": lambda p, v: (p.write_bytes(b"read only " * 400), os.chmod(p, 0o444)),
}


def _whole(folder):
    """snap(), and for each symbolic link its target text."""
    out = snap(folder)
    for p in Path(folder).iterdir():
        if p.is_symlink():
            out[p.name] = out[p.name] + (os.readlink(p),)
    return out


@pytest.mark.parametrize("kind", list(PLANTS))
def test_a_thing_at_the_temporary_name_before_creation_is_never_written_and_never_becomes_the_store(tmp_path, monkeypatch, kind):
    v = _victims(tmp_path)
    d = tmp_path / "d"
    d.mkdir()
    tmp = _forced_temp_name(monkeypatch, d)
    PLANTS[kind](tmp, v)
    before, victims = _whole(d), _whole(v)
    fetch = Finnhub(Clock())
    try:
        result, _, _ = run(d, ["AAA"], fetch=fetch)
    except cf.Refused as e:
        result = e
    assert isinstance(result, cf.Refused) and "\n" not in str(result) and fetch.calls == [], result
    assert _whole(v) == victims and _whole(d) == before            # not a byte, a time or a name changed (no store either)
    assert not (v / "made_by_nobody.db").exists()


def _swap(tmp, kind, v):
    """Put `kind` at `tmp` in place of what is there (another program, racing the module)."""
    if kind == "a_symlink_to_a_database":
        os.unlink(tmp)
        os.symlink(v / "prices.db", tmp)
    elif kind == "another_programs_database":
        shutil.copy2(v / "prices.db", str(tmp) + ".x")
        os.replace(str(tmp) + ".x", tmp)
    elif kind == "a_directory":
        os.unlink(tmp)
        os.mkdir(tmp)
    elif kind == "a_fifo":
        os.unlink(tmp)
        os.mkfifo(tmp)
    elif kind == "a_hard_link_to_a_database":
        os.unlink(tmp)
        os.link(v / "prices.db", tmp)


SWAPS = [("made", k) for k in ("a_symlink_to_a_database", "another_programs_database", "a_directory", "a_fifo", "a_hard_link_to_a_database")] + \
        [("opened", k) for k in ("a_symlink_to_a_database", "another_programs_database", "a_hard_link_to_a_database")] + \
        [("linked", k) for k in ("a_symlink_to_a_database", "another_programs_database", "a_fifo", "a_hard_link_to_a_database")]


@pytest.mark.parametrize("moment, kind", SWAPS)
def test_a_thing_swapped_in_at_the_temporary_name_during_creation_is_never_written_and_never_becomes_the_store(tmp_path, monkeypatch, moment, kind):
    """`made`: just after the module created its temporary file; `opened`: as SQLite opens it; `linked`: just before it is
    linked into place."""
    v = _victims(tmp_path)
    d = tmp_path / "d"
    d.mkdir()
    victims = _whole(v)
    swapped = []
    real_open, real_connect, real_link = os.open, sqlite3.connect, os.link

    def is_tmp(p):
        return ".new-" in os.fspath(p) and os.fspath(p).startswith(str(d))

    def spy_open(p, flags, *a, **k):
        fd = real_open(p, flags, *a, **k)
        if moment == "made" and is_tmp(p) and flags & os.O_CREAT and not swapped:
            swapped.append(os.fspath(p))
            _swap(p, kind, v)
        return fd

    def spy_connect(target, *a, **k):
        if moment == "opened" and isinstance(target, (str, os.PathLike)) and is_tmp(target) and not swapped:
            swapped.append(os.fspath(target))
            _swap(target, kind, v)
        return real_connect(target, *a, **k)

    def spy_link(src, dst, *a, **k):
        if moment == "linked" and is_tmp(src) and not swapped:
            swapped.append(os.fspath(src))
            _swap(src, kind, v)
        return real_link(src, dst, *a, **k)
    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(sqlite3, "connect", spy_connect)
    monkeypatch.setattr(os, "link", spy_link)
    fetch = Finnhub(Clock())
    with pytest.raises(cf.Refused) as e:
        run(d, ["AAA"], fetch=fetch)
    monkeypatch.undo()
    assert swapped and "\n" not in str(e.value) and fetch.calls == []
    assert _whole(v) == victims                                       # the victims: not a byte, a time or a name changed
    assert not (d / "cf.sqlite").exists() and not (d / "cf.sqlite").is_symlink()
    planted = Path(swapped[0])
    assert planted.exists() or planted.is_symlink()                   # what the other program put there is left there


CREATION_CHILD = r'''
import os, signal, sys
from valedia_trader.data_and_fees import company_facts as cf
db, k, key = sys.argv[1], int(sys.argv[2]), sys.argv[3]
create, module = cf._create_store.__code__, cf.__file__
count, inside = [0], [0]

def local(frame, event, arg):
    if event == "line":
        count[0] += 1
        if count[0] == k:
            os.kill(os.getpid(), signal.SIGKILL)
    elif event == "return" and frame.f_code is create:
        inside[0] -= 1
    return local

def tracer(frame, event, arg):                       # every line of the creation and of what it calls in the module
    if frame.f_code is create:
        inside[0] += 1
        return local
    return local if inside[0] and frame.f_code.co_filename == module else None

sys.settrace(tracer)
cf.collect(["AAA"], db, fetch=lambda u, h: (b"{}" if "profile2" in u or "metric" in u or "insider" in u or "calendar" in u else b"[]", {}),
           clock=lambda: 1763121600.0, sleep=lambda s: None, credentials=lambda n: key)
sys.settrace(None)
print("finished", count[0], flush=True)
'''


def _whole_store_or_nothing(path):
    """'absent', or 'store' when the file at `path` is a complete company-facts store (mark, tables, integrity)."""
    if not path.exists():
        return "absent"
    head = path.read_bytes()[:100]
    assert head[:16] == b"SQLite format 3\x00" and int.from_bytes(head[68:72], "big") == cf.APPLICATION_ID
    copy = path.with_name("check.sqlite")
    shutil.copy2(path, copy)
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"observations", "news", "runs"}
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm", "-journal"):
            with __import__("contextlib").suppress(FileNotFoundError):
                os.unlink(str(copy) + suffix)
    return "store"


def test_kills_during_creation_never_leave_a_half_made_store_and_the_next_run_removes_only_its_own_hard_link(tmp_path):
    root = Path(cf.__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")

    def child(db, k):
        p = subprocess.run([sys.executable, "-c", CREATION_CHILD, str(db), str(k), KEY], capture_output=True, text=True, env=env,
                           cwd=str(root), timeout=60)
        return p.returncode, p.stdout
    rc, out = child(tmp_path / "whole" / "cf.sqlite", 10 ** 9)
    assert rc == 0 and out.startswith("finished"), out
    lines = int(out.split()[1])
    assert lines >= 30                                               # a kill at each line of the creation
    outcomes = {}
    for k in range(1, lines + 1):
        d = tmp_path / ("k%03d" % k)
        d.mkdir()
        rc, out = child(d / "cf.sqlite", k)
        assert rc == -signal.SIGKILL, (k, rc, out)
        state = _whole_store_or_nothing(d / "cf.sqlite")
        left = {p.name: p.lstat() for p in d.iterdir() if ".new-" in p.name}
        live = (d / "cf.sqlite").lstat() if state == "store" else None
        own = {n for n, st in left.items() if live and (st.st_dev, st.st_ino) == (live.st_dev, live.st_ino)}
        others = {n: _whole(d)[n] for n in left if n not in own}
        listed = cf.status(d / "cf.sqlite")["leftovers"] if state == "store" else None
        if state == "store":
            assert sorted(listed) == sorted(left)                        # status lists every leftover by name
        result, _, _ = run(d, ["AAA"], clock=Clock(T0 + 100))           # the next run
        assert result.stopped is None and _whole_store_or_nothing(d / "cf.sqlite") == "store"
        after = _whole(d)
        assert not any(n in after for n in own)                          # a hard link to the live store is removed
        assert {n: after.get(n) for n in others} == others               # any other leftover is untouched
        assert sorted(cf.status(d / "cf.sqlite")["leftovers"]) == sorted(others)
        outcomes[(state, bool(own), bool(others))] = outcomes.get((state, bool(own), bool(others)), 0) + 1
        shutil.rmtree(d)
    assert sum(outcomes.values()) >= 30
    assert any(o[0] == "absent" for o in outcomes) and any(o[0] == "store" for o in outcomes), outcomes


def test_a_leftover_hard_link_to_the_live_store_is_removed_and_every_other_leftover_is_listed_and_untouched(tmp_path, capsys):
    run(tmp_path, ["AAA"])
    store = tmp_path / "cf.sqlite"
    v = _victims(tmp_path)
    own = ["cf.sqlite.new-" + "a" * 32, "cf.sqlite.new-%d" % 4242]
    for n in own:
        os.link(store, tmp_path / n)
    others = {"cf.sqlite.new-" + "b" * 32: lambda p: p.write_bytes(b"half made"),
              "cf.sqlite.new-" + "c" * 32: lambda p: os.symlink(store, p),
              "cf.sqlite.new-" + "d" * 32: lambda p: os.link(v / "notes.txt", p),
              "cf.sqlite.new-" + "e" * 32 + "-wal": lambda p: p.write_bytes(b"w" * 100),
              "cf.sqlite.new-" + "f" * 32: lambda p: os.mkfifo(p),
              "cf.sqlite.new-" + "0" * 32: lambda p: p.mkdir(),
              "cf.sqlite.new-77": lambda p: shutil.copy2(store, p)}
    for n, make in others.items():
        make(tmp_path / n)
    assert sorted(cf.status(store)["leftovers"]) == sorted(own + list(others))
    before = {n: v_ for n, v_ in _whole(tmp_path).items() if n in others}
    victims = _whole(v)
    run(tmp_path, ["AAA"], clock=Clock(T0 + 10))
    after = _whole(tmp_path)
    assert not any(n in after for n in own)
    assert {n: after.get(n) for n in others} == before and _whole(v) == victims
    assert sorted(cf.status(store)["leftovers"]) == sorted(others)
    assert cf.main(["status", "--db", str(store)]) == 0
    out = capsys.readouterr().out
    assert all(n in out for n in others) and "leftover" in out


# ---- outcome 4 (D3, D6, D7): see FOREIGN above (18 kinds of rev 2 and 8 more); and a short main file that the -wal explains

def test_a_short_main_file_with_a_write_ahead_file_that_holds_the_missing_pages_is_not_cut(tmp_path):
    """A kill in the middle of a checkpoint leaves the main file shorter than its header says while the -wal still holds
    every frame; SQLite completes it. Such a store is not 'cut' (D6 as built: see the report)."""
    side = tmp_path / "side"
    side.mkdir()
    run(side, ["AAA"])
    rows_ = ["INSERT INTO news VALUES ('AAA','w%d',%r,%r,'%s','s','u','m')" % (i, T0 - 60, T0 + 1, "x" * 900) for i in range(40)]
    _live_copy(side / "cf.sqlite", tmp_path / "cf.sqlite", *rows_)
    p = tmp_path / "cf.sqlite"
    head = bytearray(p.read_bytes()[:100])
    pages = int.from_bytes(head[28:32], "big")
    page = int.from_bytes(head[16:18], "big")
    with open(p, "r+b") as f:                       # the header of a half-done checkpoint: it already counts 8 more pages
        f.seek(28)
        f.write((pages + 8).to_bytes(4, "big"))
    assert p.stat().st_size < (pages + 8) * page
    assert cf._store_problem(p) is None
    os.unlink(str(p) + "-wal")
    assert "cut short" in cf._store_problem(p)


# ---- outcome 5 (D4): the clock guard

def _http_date(ts):
    return format_datetime(datetime.fromtimestamp(ts, timezone.utc), usegmt=True)


@pytest.mark.parametrize("offset, words", [(25 * 3600, "25 hours 0 minutes"), (365 * 86400, "365 days 0 hours 0 minutes"),
                                           (-365 * 86400, "365 days 0 hours 0 minutes")])      # rev 4 (R4b): hours and minutes
def test_an_answer_dated_more_than_24_hours_from_the_wall_clock_stops_the_run_and_nothing_of_it_is_stored(tmp_path, capsys, offset, words):
    clock = Clock()
    dated = lambda call: _http_date(call.t + offset) if call.path == "/stock/profile2" else _http_date(call.t)   # noqa: E731
    fetch = Finnhub(clock, date=dated)
    result, _, _ = run(tmp_path, ["AAA", "BBB"], fetch=fetch, clock=clock)
    assert result.stopped and "\n" not in result.stopped and words in result.stopped
    assert cf._iso(T0) in result.stopped and cf._iso(T0 + offset) in result.stopped
    assert [c.path for c in fetch.calls] == ["/calendar/earnings", "/stock/profile2"]             # nothing after it is asked
    assert rows(tmp_path, "SELECT kind FROM observations") == [("earnings_calendar",)]           # nothing of it is stored
    assert rows(tmp_path, "SELECT ended_at IS NOT NULL FROM runs") == [(1,)]
    rc, _, _ = cli(tmp_path, "collect", "--symbols", "AAA", "--db", str(tmp_path / "cli.sqlite"), clock=clock, fetch=Finnhub(clock, date=dated))
    out = capsys.readouterr()
    assert rc == 2 and len(out.out.strip().splitlines()) == 1 and words in out.out


@pytest.mark.parametrize("date", [_http_date(T0 - 23 * 3600 - 59 * 60), _http_date(T0), _http_date(T0 + 24 * 3600), None, "yesterday", "",
                                  "Mon, 99 Foo 2026 12:00:00 GMT", b"Sat, 14 Nov 2026 12:00:00 GMT", 12345, "Sat, 14 Nov 99999 12:00:00 GMT"])
def test_an_answer_dated_within_24_hours_or_with_no_readable_date_is_stored(tmp_path, date):
    clock = Clock()
    fetch = Finnhub(clock, date=(lambda call: date) if date is not None else None)
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.stopped is None and result.failed == 0 and result.stored == 8
    readable = isinstance(date, str) and date.endswith("GMT") and "Foo" not in date and "99999" not in date
    assert result.undated == (0 if readable else 7) and ("without a readable Date: %d" % result.undated) in result.summary()


def test_the_real_fetch_returns_the_answers_headers_with_lower_cased_names():
    handler = FakeHTTPS({U: (200, {"Date": "Sat, 14 Nov 2026 12:00:00 GMT", "X-Ratelimit-Remaining": "59"}, b'{"ok":1}')})
    body, headers = cf.http_fetch(U, HDR, opener=cf._build_opener(handler))
    assert body == b'{"ok":1}' and headers["date"] == "Sat, 14 Nov 2026 12:00:00 GMT" and headers["x-ratelimit-remaining"] == "59"
    assert all(k == k.lower() for k in headers)


IMPOSSIBLE_TIMES = {"1e300": "1e300", "infinity": "9e999", "minus infinity": "-9e999", "text": "'later'", "after the year 9999": "253402300800.0",
                    "negative": "-5.0", "1999": "946684799.0", "2100-01-02": "4102531200.0", "a blob": "x'00'"}


def test_stored_impossible_fetch_times_neither_refuse_a_run_nor_ever_come_back_from_a_reader(tmp_path):
    run(tmp_path, ["AAA"])
    db = tmp_path / "cf.sqlite"
    body = cf.canonical_json({"impossible": True})
    for i, (name, t) in enumerate(IMPOSSIBLE_TIMES.items()):
        _sql(db, "INSERT INTO observations VALUES ('AAA','profile',%s,'%s','%s')" % (t, cf.body_sha256(body), body),
             "INSERT INTO news VALUES ('AAA','imp%d',%r,%s,'h','s','u','m')" % (i, T0 - 60, t))
    later_clock = Clock(T0 + 3600)
    later, _, _ = run(tmp_path, ["AAA"], clock=later_clock, fetch=Finnhub(later_clock, {("/stock/profile2", "AAA"): [{"v": 2}]}))
    assert later.stopped is None and later.failed == 0 and later.unchanged == 6     # compared with the newest PLAUSIBLE rows
    for when in (T0 + 7200, 4102444800.0, 1e300, 1e308):
        got = cf.as_of(db, "AAA", "profile", when)
        assert got["body"] == {"v": 2} and got["fetched_at"] == T0 + 3600
        assert cf.as_of(db, "AAA", "metrics", when)["fetched_at"] == T0
        assert not [a for a in cf.news_as_of(db, "AAA", when) if a["article_id"].startswith("imp")]
    st = cf.status(db)
    assert st["newest"] == T0 + 3600 and st["oldest"] == T0 and st["impossible"] == 2 * len(IMPOSSIBLE_TIMES)


def test_the_collector_never_stamps_an_impossible_time_itself(tmp_path):
    for start in (4102444800.0 + 10, 946684800.0 - 10):
        fetch = Finnhub(Clock(start))
        with pytest.raises(cf.Refused) as e:
            run(tmp_path, ["AAA"], fetch=fetch, clock=Clock(start), name="c%d.sqlite" % start)
        assert fetch.calls == [] and "2000" in str(e.value) and "2100" in str(e.value)


@pytest.mark.parametrize("ahead, words", [(2 * 3600, "2 hours 0 minutes"), (200 * 86400, "200 days 0 hours 0 minutes")])     # rev 4 (R4b)
def test_a_store_whose_newest_plausible_fetch_is_ahead_of_the_clock_is_refused_naming_the_times(tmp_path, ahead, words):
    run(tmp_path, ["AAA"], clock=Clock(T0 + ahead))
    fetch = Finnhub(Clock())
    with pytest.raises(cf.Refused) as e:
        run(tmp_path, ["AAA"], fetch=fetch)
    text = str(e.value)
    assert fetch.calls == [] and "\n" not in text and cf._iso(T0 + ahead) in text and cf._iso(T0) in text and words in text


# ---- outcome 6 (D5): no traceback from status or show over hand-made bad rows

def _bad_rows():
    """At least 40 hand-made bad rows for each table, as SQL value lists."""
    times = ["1e300", "9e999", "-9e999", "-1e15", "'soon'", "x'00ff'", "CAST(x'ff41' AS TEXT)", "253402300800.0", "0", "%r" % (T0 + 10)]
    texts = ["x'00ff'", "CAST(x'fffe' AS TEXT)", "12", "1.5", "'%s'" % ("y" * 5000), "''", "'*'", "'profile'"]
    sha = cf.body_sha256('{"a":1}')
    obs = []
    for i, (t, x) in enumerate((t, x) for t in times for x in texts):
        obs.append("('AAA', %s, %s, %s, %s)" % (x if i % 3 else "'profile'", t, ("'%s'" % sha) if i % 2 else "'x'", x))
    obs += ["(%s, 'profile', %r, '%s', '{\"a\":1}')" % (s, T0 + 5, sha) for s in ("x'41'", "CAST(x'ff' AS TEXT)", "'AAA'", "12")]
    news = ["('AAA', %s, %s, %s, %s, %s, %s, %s)" % ("'n%d'" % i if i % 4 else ("x'%04x'" % i), times[i % len(times)], times[(i * 3) % len(times)],
                                                    texts[i % len(texts)], texts[(i + 1) % len(texts)], texts[(i + 2) % len(texts)],
                                                    "NULL" if i % 5 == 0 else texts[(i + 3) % len(texts)]) for i in range(60)]
    news += ["('AAA', %d, %r, %r, 'h', 's', 'u', 'm')" % (10 ** 18 + i, T0 - 60, T0 + 1) for i in range(3)]
    counts = ["'many'", "1.5", "-1", "x'00'", "9223372036854775807", "1e300", "CAST(x'ff' AS TEXT)", "0"]
    runs_ = ["(%s, %s, %s, %s, 0, 0, %s)" % (times[i % len(times)], "NULL" if i % 3 == 0 else times[(i + 4) % len(times)],
                                            counts[i % len(counts)], counts[(i * 5) % len(counts)],
                                            ["x'00'", "CAST(x'ff' AS TEXT)", "12", "'%s'" % cf.INTERRUPTED, "''", "'news,profile'"][i % 6])
             for i in range(48)]
    return {"observations": obs, "news": news, "runs": runs_}


@pytest.mark.parametrize("table", ["observations", "news", "runs", "all"])
def test_status_and_show_never_end_in_a_traceback_over_hand_made_bad_rows(tmp_path, capsys, table):
    run(tmp_path, ["AAA"])
    db = tmp_path / "cf.sqlite"
    bad = _bad_rows()
    assert all(len(v) >= 40 for v in bad.values())
    conn = sqlite3.connect(db)
    for name, values in bad.items():
        if table in (name, "all"):
            for vals in values:
                conn.execute("INSERT INTO %s VALUES %s" % (name, vals))
    conn.commit()
    if table in ("runs", "all"):                     # each bad runs row in turn as the newest one, the row `status` shows
        for vals in bad["runs"]:
            rowid = conn.execute("INSERT INTO runs VALUES %s" % vals).lastrowid
            conn.commit()
            assert cf.main(["status", "--db", str(db)]) == 0, vals
            out = capsys.readouterr()
            assert out.err == "" and "Traceback" not in out.out, vals
            conn.execute("DELETE FROM runs WHERE rowid=?", (rowid,))
            conn.commit()
    conn.close()
    st = cf.status(db)                                                   # the function itself: no exception
    if table in ("runs", "all"):
        assert st["unreadable_runs"] >= 30 and st["last_run"] is not None
    if table in ("observations", "news", "all"):
        assert st["impossible"] >= 20
    assert cf.main(["status", "--db", str(db)]) == 0                       # every row it cannot show is skipped and counted
    out = capsys.readouterr()
    assert out.err == "" and "Traceback" not in out.out
    for argv in (["show", "AAA"], ["show", "AAA", "--kind", "news"], ["show", "AAA", "--kind", "profile"], ["show", "*"],
                 ["show", "AAA", "--as-of", "9999-12-31T23:59:59Z"], ["show", "AAA", "--kind", "news", "--as-of", "2000-01-01T00:00:00Z"]):
        rc = cf.main(argv + ["--db", str(db)])
        out = capsys.readouterr()
        assert rc in (0, 2) and "Traceback" not in out.err + out.out
        if rc == 2:
            assert out.out == "" and len(out.err.strip().splitlines()) == 1
        else:
            json.loads(out.out)
            assert len(out.err.strip().splitlines()) <= 1


# ---- outcome 7 (D8 and three breaks no test caught)

def test_a_refusal_after_the_lock_was_taken_leaves_no_lock_file_behind(tmp_path, monkeypatch):
    run(tmp_path, ["AAA"])
    os.unlink(tmp_path / "cf.sqlite.lock")
    real = cf._take_lock

    def take_then_swap(path):
        got = real(path)                       # the store turns foreign between the first check and the second
        _sql(tmp_path / "foreign.db", "CREATE TABLE prices(day TEXT)")
        os.replace(tmp_path / "foreign.db", path)
        return got
    monkeypatch.setattr(cf, "_take_lock", take_then_swap)
    with pytest.raises(cf.Refused):
        run(tmp_path, ["AAA"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cf.sqlite"]


@pytest.mark.parametrize("aid, usable", [("", False), ("x", True), ("x" * 128, True), ("x" * 129, False), (0, True), (2 ** 63 - 1, True),
                                         (2 ** 63, False), (-(2 ** 63 - 1), True), (-(2 ** 63), False), (10 ** 40, False), (1.0, False)])
def test_an_article_id_is_usable_only_as_1_to_128_characters_or_an_integer_below_2_to_the_63(tmp_path, aid, usable):
    clock = Clock()
    result, _, _ = run(tmp_path, ["AAA"], fetch=Finnhub(clock, {("/company-news", "AAA"): [[dict(article(1, T0 - 5), id=aid)]]}), clock=clock)
    assert rows(tmp_path, "SELECT count(*) FROM news") == [(1 if usable else 0,)] and result.skipped == (0 if usable else 1)


def test_the_pacer_gives_up_cleanly_when_its_clock_has_not_advanced_across_1000_waits(tmp_path):
    clock = Clock()
    waits = []

    def sleep(s):                                    # rev 4 (R3): bounded, so a broken rule fails at once instead of hanging
        waits.append(s)
        if len(waits) > 2000:
            raise _Hung("the pacer waited 2000 times on a clock that never advanced")
    result = cf.collect(["S%02d" % i for i in range(20)], tmp_path / "cf.sqlite", fetch=Finnhub(clock), clock=clock,
                        sleep=sleep, credentials=lambda n: KEY, monotonic=lambda: 1000.0)
    assert result.calls == 55 and len(waits) == 1000 and result.stopped and "does not advance" in result.stopped
    assert rows(tmp_path, "SELECT calls, ended_at IS NOT NULL FROM runs") == [(55, 1)]


def test_the_pacer_goes_on_when_its_clock_advances_after_999_waits(tmp_path):
    clock = Clock()
    mono = [1000.0]
    waits = []

    def sleep(s):
        waits.append(s)
        if len(waits) % 999 == 0:
            mono[0] += 61.0
    result = cf.collect(["S%02d" % i for i in range(12)], tmp_path / "cf.sqlite", fetch=Finnhub(clock), clock=clock,
                        sleep=sleep, credentials=lambda n: KEY, monotonic=lambda: mono[0])
    assert result.stopped is None and result.calls == 73 and len(waits) == 999


# ======================================================================================================================
# rev 4 (ticket T33 rev 4): R1 the cut-store rule decided from bytes before SQLite opens anything; R2 status and show read
# one committed state; R3 tests for four breaks no test caught; R4 the words a person reads.
# ======================================================================================================================

def _wal_sum(data, s, big):
    """SQLite's WAL checksum over `data` (a multiple of 8 bytes), continuing from `s` (written here from the file format,
    independently of the module)."""
    s1, s2 = s
    w = struct.unpack((">" if big else "<") + "%dI" % (len(data) // 4), data)
    for i in range(0, len(w), 2):
        s1 = (s1 + w[i] + s2) & 0xFFFFFFFF
        s2 = (s2 + w[i + 1] + s1) & 0xFFFFFFFF
    return s1, s2


def _read_wal(path):
    """(page size, frames) of a -wal SQLite wrote, every frame as (page number, commit size, page bytes), unvalidated."""
    b = Path(path).read_bytes()
    ps = struct.unpack(">I", b[8:12])[0]
    frames, off = [], 32
    while off + 24 + ps <= len(b):
        pg, commit = struct.unpack(">2I", b[off:off + 8])
        frames.append((pg, commit, b[off + 24:off + 24 + ps]))
        off += 24 + ps
    return ps, frames


def _write_wal(path, ps, frames, big=False, salts=(0x1A2B3C4D, 0x5E6F7081)):
    """A valid -wal holding `frames` in the word order `big` (magic 0x377F0683) or little (0x377F0682)."""
    head = struct.pack(">6I", 0x377F0682 | int(big), 3007000, ps, 0, *salts)
    s = _wal_sum(head, (0, 0), big)
    out = [head, struct.pack(">2I", *s)]
    for pg, commit, page in frames:
        fh = struct.pack(">4I", pg, commit, *salts)
        s = _wal_sum(page, _wal_sum(fh[:8], s, big), big)
        out.append(fh + struct.pack(">2I", *s) + page)
    Path(path).write_bytes(b"".join(out))


def _sqlite_frames(path):
    """How many frames of the -wal beside `path` SQLite itself trusts: open a copy (no -shm, so SQLite recovers from the
    -wal alone) and ask for its log size."""
    work = Path(str(path) + ".sq")
    work.mkdir()
    try:
        for suffix in ("", "-wal"):
            shutil.copy(str(path) + suffix, str(work / "x.db") + suffix)
        conn = sqlite3.connect(work / "x.db")
        try:
            return conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[1]
        finally:
            conn.close()
    finally:
        shutil.rmtree(work)


def _header(p):
    b = Path(p).read_bytes()[:100]
    return int.from_bytes(b[16:18], "big"), int.from_bytes(b[28:32], "big")


NEWS_ROWS = 60


def _news_rows(n=NEWS_ROWS):
    return ["INSERT INTO news VALUES ('AAA','w%03d',%r,%r,'%s','s','u','m')" % (i, T0 - 60, T0 + 1, "x" * 900) for i in range(n)]


def _model(n=NEWS_ROWS):
    """The AAA articles a reader must see at T0 + 100 in a store made by `_live_store`."""
    return sorted(["%d" % (sum(map(ord, "AAA")) * 10 + k) for k in (1, 2)] + ["w%03d" % i for i in range(n)])


def _live_store(d):
    """d/cf.sqlite: a store of one run with NEWS_ROWS more articles committed in its -wal and not checkpointed."""
    side = d.parent / (d.name + "_side")
    side.mkdir()
    run(side, ["AAA"])
    _live_copy(side / "cf.sqlite", d / "cf.sqlite", *_news_rows())
    shutil.rmtree(side)
    return d / "cf.sqlite"


def _newest(frames):
    out = {}
    for pg, _, page in frames:
        out[pg] = page
    return out


def _kill_in_checkpoint(d, extra=3):
    """d/cf.sqlite as a kill in the middle of a checkpoint leaves it, byte for byte: the checkpoint has written page 1 and
    the pages up to K (the old size + `extra`) from the -wal, in page order, and died; the main file is K pages long, its
    header says M (the -wal's last commit), and every page from K + 1 to M is in a committed frame. Returns (path, K, M)."""
    p = _live_store(d)
    ps, frames = _read_wal(str(p) + "-wal")
    old = p.stat().st_size // ps
    commit = [c for _, c, _ in frames if c][-1]
    k = old + extra
    with open(p, "r+b") as f:
        for pg, page in sorted(_newest(frames).items()):
            if pg == 1 or pg <= k:
                f.seek((pg - 1) * ps)
                f.write(page)
    assert p.stat().st_size == k * ps and _header(p) == (ps, commit) and commit > k + 2
    return p, k, commit


def _cut_main(d, pages=5):
    """d/cf.sqlite: a whole store with NEWS_ROWS more articles, checkpointed (no -wal), then cut by `pages` pages."""
    side = d.parent / (d.name + "_cut")
    side.mkdir()
    run(side, ["AAA"])
    _sql(side / "cf.sqlite", *_news_rows())
    b = (side / "cf.sqlite").read_bytes()
    ps = _header(side / "cf.sqlite")[0]
    (d / "cf.sqlite").write_bytes(b[:len(b) - pages * ps])
    shutil.rmtree(side)
    return d / "cf.sqlite"


def _wal_from(d, name, statements, base_store=True):
    """The -wal of a database (a fresh one-run store, or another program's database) after `statements`, each committed."""
    src = d.parent / (d.name + "_" + name)
    src.mkdir()
    db = src / "x.db"
    if base_store:
        run(src, ["AAA"], name="x.db")
    else:
        _sql(db, "CREATE TABLE t(x)", *["INSERT INTO t VALUES ('%s')" % ("y" * 300)] * 200)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    for st in statements:
        conn.execute(st)
        conn.commit()
    wal = Path(str(db) + "-wal").read_bytes()
    conn.close()
    shutil.rmtree(src)
    return wal


def _k_one_page_wal(d):                      # a -wal of 12 frames, all of one page of this store
    p = _cut_main(d)
    Path(str(p) + "-wal").write_bytes(_wal_from(d, "w", ["UPDATE runs SET calls=%d" % i for i in range(12)]))
    ps, frames = _read_wal(str(p) + "-wal")
    assert len(frames) == 12 and len({pg for pg, _, _ in frames}) == 1
    return p


def _k_foreign_wal(d):                       # another database's -wal
    p = _cut_main(d)
    Path(str(p) + "-wal").write_bytes(_wal_from(d, "f", ["UPDATE t SET x='%s' WHERE rowid=%d" % ("z" * 300, i + 1) for i in range(10)],
                                                base_store=False))
    return p


def _k_stale_wal(d):                         # a -wal of this same store from before later writes
    p = _cut_main(d, pages=4)
    Path(str(p) + "-wal").write_bytes(_wal_from(d, "s", ["INSERT INTO runs VALUES (%r, %r, 0, 0, 0, 0, '')" % (T0 + i, T0 + i) for i in range(8)]))
    return p


def _k_frame1_checksum_broken(d):            # the right -wal with frame 1's checksum broken
    p, _, _ = _kill_in_checkpoint(d)
    with open(str(p) + "-wal", "r+b") as f:
        f.seek(32 + 16)
        x = f.read(1)
        f.seek(32 + 16)
        f.write(bytes([x[0] ^ 0xFF]))
    return p


def _rewal(p, edit, big=False):
    """Rewrite the -wal beside `p` as a valid -wal of the frames `edit(frames, k, m)` returns (the -shm is removed)."""
    ps, frames = _read_wal(str(p) + "-wal")
    k = p.stat().st_size // ps
    m = [c for _, c, _ in frames if c][-1]
    _write_wal(str(p) + "-wal", ps, edit(frames, k, m), big=big)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(str(p) + "-shm")
    return p


def _drop(page_of):
    """The frames without those of one page; a dropped commit frame hands its commit size to the frame before it, so the
    last commit still says M."""
    def edit(frames, k, m):
        out = []
        for pg, commit, page in frames:
            if pg == page_of(k, m):
                if commit and out:
                    out[-1] = (out[-1][0], commit, out[-1][2])
                continue
            out.append((pg, commit, page))
        return out
    return edit


def _s_no_commit(d):                         # every frame valid, none a commit
    p, _, _ = _kill_in_checkpoint(d)
    return _rewal(p, lambda frames, k, m: [(pg, 0, page) for pg, _, page in frames])


def _s_all_missing_pages_but_one(d):         # a counted frame for every missing page but one
    p, _, _ = _kill_in_checkpoint(d)
    return _rewal(p, _drop(lambda k, m: k + 2))


def _s_missing_page_only_after_the_last_commit(d):      # a valid frame AFTER the last commit holds the missing page
    p, _, _ = _kill_in_checkpoint(d)

    def edit(frames, k, m):
        return _drop(lambda k_, m_: k + 2)(frames, k, m) + [(k + 2, 0, _newest(frames)[k + 2])]
    return _rewal(p, edit)


def _s_header_checksum_broken(d):
    p, _, _ = _kill_in_checkpoint(d)
    _rewal(p, lambda frames, k, m: frames)
    with open(str(p) + "-wal", "r+b") as f:  # the checkpoint sequence number changes; the stored checksum does not
        f.seek(15)
        f.write(b"\x07")
    return p


def _s_frame_checksum_broken(d):             # one byte of a page in the frame of a missing page
    p, k, _ = _kill_in_checkpoint(d)
    ps, frames = _read_wal(str(p) + "-wal")
    i = max(n for n, f in enumerate(frames) if f[0] == k + 1)
    with open(str(p) + "-wal", "r+b") as f:
        f.seek(32 + i * (24 + ps) + 24 + 777)
        x = f.read(1)
        f.seek(32 + i * (24 + ps) + 24 + 777)
        f.write(bytes([x[0] ^ 0x01]))
    return p


def _s_partial_last_page(d):                 # page K + 1 is only partly in the main file and in no frame
    p, k, _ = _kill_in_checkpoint(d)
    ps, frames = _read_wal(str(p) + "-wal")
    part = _newest(frames)[k + 1][:100]
    _rewal(p, _drop(lambda k_, m: k + 1))
    with open(p, "ab") as f:
        f.write(part)
    return p


def _s_commit_size_beyond_every_frame(d):    # a whole main file; the -wal's commit says M pages but page M is in no frame
    p = _live_store(d)
    return _rewal(p, _drop(lambda k, m: m))


def _s_page_number_0_frame_in_the_chain(d):  # addendum B1: the chain passes through a frame of page number 0
    p, _, _ = _kill_in_checkpoint(d)
    _rewal(p, lambda frames, k, m: [(0, 0, frames[0][2])] + frames)      # right salts and checksums, page number 0
    ps, frames = _read_wal(str(p) + "-wal")
    wal = cf._wal_scan(p, ps)
    assert wal.salted == len(frames) and wal.counted == 0             # every salt right; the walk ends at that frame
    return p


R1_REFUSED = {
    "cut_main_and_a_page_number_0_frame_before_the_missing_pages": _s_page_number_0_frame_in_the_chain,
    "cut_main_and_a_wal_of_12_frames_of_one_page": _k_one_page_wal,
    "cut_main_and_another_databases_wal": _k_foreign_wal,
    "cut_main_and_a_stale_wal_of_this_store": _k_stale_wal,
    "cut_main_and_the_right_wal_with_frame_1s_checksum_broken": _k_frame1_checksum_broken,
    "cut_main_and_valid_frames_but_no_commit_frame": _s_no_commit,
    "cut_main_and_a_frame_for_every_missing_page_but_one": _s_all_missing_pages_but_one,
    "cut_main_and_the_missing_page_only_after_the_last_commit": _s_missing_page_only_after_the_last_commit,
    "cut_main_and_the_wal_header_checksum_broken": _s_header_checksum_broken,
    "cut_main_and_a_frame_checksum_broken": _s_frame_checksum_broken,
    "cut_main_whose_last_page_is_partial_and_in_no_frame": _s_partial_last_page,
    "a_commit_size_beyond_every_frame": _s_commit_size_beyond_every_frame,
}


@pytest.fixture
def opened_by_sqlite(monkeypatch):
    """Every database name SQLite is asked to open while the test runs."""
    seen = []
    real = sqlite3.connect

    def spy(*a, **k):
        seen.append(str(a[0] if a else k.get("database")))
        return real(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", spy)
    return seen


@pytest.mark.parametrize("name", list(R1_REFUSED))
def test_r1_a_store_that_is_not_whole_is_refused_before_sqlite_opens_it_and_nothing_changes(tmp_path, name, capsys, opened_by_sqlite):
    d = tmp_path / "s"
    d.mkdir()
    path = R1_REFUSED[name](d)
    before = snap(d)
    del opened_by_sqlite[:]
    fetch = Finnhub(Clock(T0 + 100))
    with pytest.raises(cf.Refused) as e:
        run(d, ["AAA"], fetch=fetch, clock=Clock(T0 + 100))
    text = str(e.value)
    assert "\n" not in text and "nothing was done" in text and ("cut short" in text or "not whole" in text), text
    for call in (lambda: cf.as_of(path, "AAA", "profile", T0 + 100), lambda: cf.news_as_of(path, "AAA", T0 + 100), lambda: cf.status(path)):
        with pytest.raises(cf.Refused) as e:
            call()
        assert "nothing was done" in str(e.value)
    assert cf.main(["collect", "--symbols", "AAA", "--db", str(path)], fetch=fetch, credentials=lambda n: KEY, clock=Clock(T0 + 100)) == 2
    assert len(capsys.readouterr().err.strip().splitlines()) == 1
    for argv in (["status"], ["show", "AAA"], ["show", "AAA", "--kind", "news"]):
        assert cf.main(argv + ["--db", str(path)]) == 2
        assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert fetch.calls == [] and snap(d) == before                     # bytes, modification times and directory entries
    assert not [n for n in opened_by_sqlite if str(d) in n], opened_by_sqlite      # SQLite was never asked to open it


def _opened_everywhere(path, capsys):
    """Every reader reads the model, the collector runs on it, and SQLite finds it whole."""
    assert sorted(a["article_id"] for a in cf.news_as_of(path, "AAA", T0 + 100) if a["fetched_at"] <= T0 + 1) == _model()
    assert cf.as_of(path, "AAA", "profile", T0 + 100)["body"]["ticker"] == "AAA"
    assert cf.status(path)["rows"]["news"] == len(_model())
    assert cf.main(["status", "--db", str(path)]) == 0
    assert cf.main(["show", "AAA", "--db", str(path), "--as-of", "2026-11-14T12:05:00Z"]) == 0
    capsys.readouterr()
    clock = Clock(T0 + 200)
    result, _, _ = run(path.parent, ["AAA"], clock=clock)
    assert result.stopped is None and result.failed == 0
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        got = sorted(r[0] for r in conn.execute("SELECT article_id FROM news WHERE fetched_at<=?", (T0 + 1,)))
    finally:
        conn.close()
    assert got == _model()


@pytest.mark.parametrize("big", [None, False, True])
def test_r1_a_main_file_left_short_by_a_kill_during_a_checkpoint_is_opened_whole(tmp_path, capsys, big):
    """None: the -wal exactly as SQLite wrote it; False/True: rewritten in each word order of the magic."""
    d = tmp_path / "k"
    d.mkdir()
    p, k, m = _kill_in_checkpoint(d)
    if big is not None:
        _rewal(p, lambda frames, k_, m_: frames, big=big)
        assert Path(str(p) + "-wal").read_bytes()[3] == (0x83 if big else 0x82)
    assert p.stat().st_size // _header(p)[0] == k < m == _header(p)[1]
    assert cf._store_problem(p) is None
    assert cf._wal_scan(p, _header(p)[0]).counted == _sqlite_frames(p) > 0
    _opened_everywhere(p, capsys)


def _random_db(d, rnd, big):
    """A database SQLite wrote with a random history, copied with its live -wal (checkpoints off); sometimes in the middle
    of a transaction whose pages spilled into the -wal before its commit. Returns the copy's path and its page size."""
    src = d / "src.db"
    ps = rnd.choice([512, 1024, 4096, 8192])
    conn = sqlite3.connect(src, isolation_level=None)
    conn.execute("PRAGMA page_size=%d" % ps)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t(a INTEGER PRIMARY KEY, b TEXT)")
    for _ in range(rnd.randint(1, 12)):
        conn.execute("BEGIN")
        for _ in range(rnd.randint(1, 30)):
            op = rnd.random()
            if op < 0.6:
                conn.execute("INSERT INTO t(b) VALUES (?)", ("v" * rnd.randint(1, 3000),))
            elif op < 0.85:
                conn.execute("UPDATE t SET b=? WHERE a % 7 = ?", ("u" * rnd.randint(1, 900), rnd.randint(0, 6)))
            else:
                conn.execute("DELETE FROM t WHERE a % 11 = ?", (rnd.randint(0, 10),))
        conn.execute("COMMIT")
    spilled = rnd.random() < 0.3
    if spilled:                                  # an open transaction whose pages spill into the -wal before any commit
        conn.execute("PRAGMA cache_size=2")
        conn.execute("BEGIN")
        for _ in range(60):
            conn.execute("INSERT INTO t(b) VALUES (?)", ("s" * 2000,))
    dest = d / "copy.db"
    for suffix in ("", "-wal"):
        shutil.copy(str(src) + suffix, str(dest) + suffix)
    if spilled:
        conn.execute("ROLLBACK")
    conn.close()
    if big:
        _rewal(dest, lambda frames, k, m: frames, big=True)
    return dest, ps


def test_r1_the_walk_counts_exactly_the_frames_sqlite_trusts_on_60_generated_stores_and_none_from_a_flipped_byte_on(tmp_path):
    rnd = random.Random(404)
    flips = 0
    for i in range(60):
        d = tmp_path / ("g%02d" % i)
        d.mkdir()
        db, ps = _random_db(d, rnd, big=(i % 4 == 3))
        want = _sqlite_frames(db)
        assert cf._wal_scan(db, ps).counted == want, i
        ps_, frames = _read_wal(str(db) + "-wal")
        commits = [n + 1 for n, f in enumerate(frames) if f[1]]
        if frames:
            k = rnd.randint(1, len(frames))                       # flip one byte of frame k (its header or its page)
            off = 32 + (k - 1) * (24 + ps) + rnd.choice([rnd.randint(0, 7), rnd.randint(16, 23), 24 + rnd.randint(0, ps - 1)])
            with open(str(db) + "-wal", "r+b") as f:
                f.seek(off)
                x = f.read(1)
                f.seek(off)
                f.write(bytes([x[0] ^ (1 << rnd.randint(0, 7))]))
            before_k = max([c for c in commits if c < k], default=0)
            got = cf._wal_scan(db, ps).counted
            assert got == _sqlite_frames(db) == before_k, (i, k, got, before_k)
            flips += 1
        shutil.rmtree(d)
    assert flips >= 50


# ---- R1: "nothing was done" only before SQLite opened the store

def test_r1_a_refusal_after_sqlite_opened_the_store_says_so_and_never_says_nothing_was_done(tmp_path, monkeypatch):
    run(tmp_path, ["AAA"])
    real = cf._remove_own_leftovers

    def swap(path):                                  # the store is replaced just before SQLite opens it
        real(path)
        _sql(tmp_path / "other.db", "PRAGMA application_id=%d" % cf.APPLICATION_ID, "PRAGMA user_version=1")
        os.replace(tmp_path / "other.db", path)
    monkeypatch.setattr(cf, "_remove_own_leftovers", swap)
    with pytest.raises(cf.Refused) as e:
        run(tmp_path, ["AAA"], clock=Clock(T0 + 10))
    text = str(e.value)
    assert "nothing was done" not in text and "SQLite" in text and "side files" in text and "\n" not in text, text
    monkeypatch.undo()
    (tmp_path / "x").mkdir()
    run(tmp_path / "x", ["AAA"])
    good = tmp_path / "x" / "cf.sqlite"
    ps, n = _header(good)
    with open(good, "r+b") as f:                     # whole by its bytes, but its pages after page 1 are garbage
        f.seek(ps)
        f.write(b"\xa5" * (ps * (n - 1)))
    with pytest.raises(cf.Refused) as e:
        cf.as_of(good, "AAA", "profile", T0 + 1)
    text = str(e.value)
    assert "nothing was done" not in text and "SQLite could not use it" in text and "side files" in text, text


# ---- R2: status and show read one committed state

def _commit_between_statements(monkeypatch, db):
    """Make a second connection commit new rows of every kind between the first statement a reader runs on the store's
    data and the next one (a trace callback on the reader's connection: deterministic, no timing)."""
    real = sqlite3.connect
    state = {"selects": 0, "committed": False, "error": None}
    body = cf.canonical_json({"ticker": "AAA", "v": "new"})
    statements = ["INSERT INTO observations VALUES ('%s','%s',%r,'%s','%s')" % (s, k, T0 + 30, cf.body_sha256(body), body)
                  for s, k in [("AAA", k) for k in cf.STORED_KINDS if k != "earnings_calendar"] + [("*", "earnings_calendar"), ("NEWSYM", "profile")]]
    statements += ["INSERT INTO observations VALUES ('AAA','unknown',%r,'%s','%s')" % (T0 + 30, cf.body_sha256(body), body),
                   "INSERT INTO observations VALUES ('AAA','profile',1e300,'%s','%s')" % (cf.body_sha256(body), body),
                   "INSERT INTO observations VALUES ('AAA','metrics',%r,'%s','%s')" % (cf.MIN_FETCH_TIME + 5, cf.body_sha256(body), body),
                   "INSERT INTO news VALUES ('AAA','late',%r,%r,'h','s','u','m')" % (T0 + 20, T0 + 40),
                   "INSERT INTO runs VALUES (%r, %r, 9, 9, 9, 9, '')" % (T0 + 30, T0 + 31),
                   "INSERT INTO runs VALUES (%r, NULL, 1, 0, 0, 0, '%s')" % (T0 + 29, cf.INTERRUPTED),
                   "INSERT INTO runs VALUES ('bad', NULL, 1, 0, 0, 0, '')"]

    def commit_now():
        try:
            w = real(db)
            for st in statements:
                w.execute(st)
            w.commit()
            w.close()
            state["committed"] = True
        except Exception as e:                       # (a callback's error would be swallowed: keep it for the test)
            state["error"] = e

    def trace(sql):
        if sql.lstrip().upper().startswith("SELECT") and "sqlite_master" not in sql:
            state["selects"] += 1
            if state["selects"] == 2 and not state["committed"]:
                commit_now()

    def spy(*a, **k):
        conn = real(*a, **k)
        target = a[0] if a else k.get("database")
        if isinstance(target, str) and target.startswith("file:"):
            conn.set_trace_callback(trace)
        return conn
    monkeypatch.setattr(sqlite3, "connect", spy)
    return state


def test_r2_one_status_call_reads_one_committed_state_in_every_field(tmp_path, monkeypatch):
    run(tmp_path, ["AAA", "BBB"])
    db = tmp_path / "cf.sqlite"
    before = cf.status(db)
    state = _commit_between_statements(monkeypatch, db)
    got = cf.status(db)
    assert state["error"] is None and state["committed"] and state["selects"] >= 2
    monkeypatch.undo()
    after = cf.status(db)
    assert got == before
    assert all(after[k] != before[k] for k in before if k != "leftovers"), {k: (before[k], after[k]) for k in before}


@pytest.mark.parametrize("argv", [["show", "AAA"], ["show", "AAA", "--kind", "news"], ["show", "AAA", "--kind", "metrics"]])
def test_r2_one_show_call_reads_one_committed_state(tmp_path, monkeypatch, capsys, argv):
    run(tmp_path, ["AAA", "BBB"])
    db = tmp_path / "cf.sqlite"
    argv = argv + ["--db", str(db), "--as-of", "2026-11-14T13:00:00Z"]
    assert cf.main(argv) == 0
    before = capsys.readouterr().out
    state = _commit_between_statements(monkeypatch, db)
    assert cf.main(argv) == 0
    got = capsys.readouterr().out
    monkeypatch.undo()
    assert state["error"] is None and got == before
    if "--kind" in argv:                     # one kind is one statement on the data: one commit by construction
        assert state["selects"] == 1 and not state["committed"]
    else:
        assert state["committed"] and cf.main(argv) == 0 and capsys.readouterr().out != before   # the commit did land


# ---- R3: breaks no test caught

def _immutable_hook(monkeypatch, db, act):
    """Run `act()` once, when an immutable reader of `db` starts its first statement on the store's data."""
    real = sqlite3.connect
    done = []

    def spy(*a, **k):
        conn = real(*a, **k)
        target = a[0] if a else k.get("database")
        if isinstance(target, str) and "immutable=1" in target:
            def trace(sql):
                if not done and sql.lstrip().upper().startswith("SELECT") and "sqlite_master" not in sql:
                    done.append(sql)
                    act()
            conn.set_trace_callback(trace)
        return conn
    monkeypatch.setattr(sqlite3, "connect", spy)
    return done


def _touch(db):
    st = db.stat()
    os.utime(db, ns=(st.st_atime_ns, st.st_mtime_ns + 7 * 10 ** 9))


def _scribble(db):
    ps, n = _header(db)
    with open(db, "r+b") as f:
        f.seek(ps)
        f.write(b"\xa5" * (ps * (n - 1)))


@pytest.mark.parametrize("act", [_touch, _scribble])
@pytest.mark.parametrize("reader", ["as_of", "news_as_of", "status"])
def test_r3_r4_a_read_in_a_read_only_place_that_overlaps_a_write_is_refused_as_that_never_returned(tmp_path, monkeypatch, act, reader):
    d = tmp_path / "ro"
    d.mkdir()
    run(d, ["AAA"])
    db = d / "cf.sqlite"
    done = _immutable_hook(monkeypatch, db, lambda: act(db))
    call = {"as_of": lambda: cf.as_of(db, "AAA", "profile", T0 + 1), "news_as_of": lambda: cf.news_as_of(db, "AAA", T0 + 1),
            "status": lambda: cf.status(db)}[reader]
    os.chmod(d, 0o555)
    try:
        with pytest.raises(cf.Refused) as e:
            call()
    finally:
        os.chmod(d, 0o755)
    text = str(e.value)
    assert done and "\n" not in text and "being written while it was read" in text and "ask again" in text, text
    assert "malformed" not in text


def test_r3_a_bare_bytes_answer_from_the_transport_is_counted_failed_and_stores_nothing(tmp_path):
    clock = Clock()
    inner = Finnhub(clock)
    result = cf.collect(["AAA"], tmp_path / "cf.sqlite", fetch=lambda u, h: inner(u, h)[0], clock=clock, sleep=clock.sleep,
                        credentials=lambda n: KEY, monotonic=clock.monotonic)
    assert result.calls == 7 and result.failed == 7 and result.stored == 0
    assert {r for _, r in result.failures} == {"not an answer"}
    assert rows(tmp_path, "SELECT count(*) FROM observations") == [(0,)] and rows(tmp_path, "SELECT count(*) FROM news") == [(0,)]


@pytest.mark.parametrize("kind", [None, "news"])
def test_r3_show_skips_and_counts_a_stored_article_it_cannot_show(tmp_path, capsys, kind):
    run(tmp_path, ["AAA"])
    db = tmp_path / "cf.sqlite"
    _sql(db, "INSERT INTO news VALUES ('AAA','far',-1e300,%r,'h','s','u','m')" % (T0 + 1),
         "INSERT INTO news VALUES ('AAA','blob',%r,%r,x'00ff','s','u','m')" % (T0 - 60, T0 + 1))
    argv = ["show", "AAA", "--db", str(db), "--as-of", "2026-11-14T13:00:00Z"] + (["--kind", kind] if kind else [])
    assert cf.main(argv) == 0
    out = capsys.readouterr()
    shown = json.loads(out.out)
    articles = shown if kind else shown["news"]
    assert sorted(a["article_id"] for a in articles) == sorted(_model(0)) and len(out.err.strip().splitlines()) == 1
    assert "2 stored articles cannot be shown and were skipped" in out.err


class _Hung(BaseException):
    """Raised by a fake clock or sleep that has been read far more often than the rule allows: a broken rule fails fast."""


# ---- R4: the words a person reads

@pytest.mark.parametrize("seconds, words", [(24 * 3600 + 30, "24 hours 1 minute"), (24 * 3600 + 0.001, "24 hours 1 minute"),
                                            (25 * 3600, "25 hours 0 minutes"), (2 * 3600, "2 hours 0 minutes"), (90, "0 hours 2 minutes"),
                                            (365 * 86400, "365 days 0 hours 0 minutes"), (-(3 * 86400 + 61), "3 days 0 hours 2 minutes")])
def test_r4_a_gap_is_printed_in_hours_and_minutes_rounded_up(seconds, words):
    assert cf._span(seconds) == words


def test_r4_an_answer_dated_just_over_24_hours_away_never_reads_24_point_0_hours(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, date=lambda call: _http_date(call.t - 24 * 3600 - 30))
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert "more than 24 hours" in result.stopped and "24 hours 1 minute" in result.stopped and "24.0 hours" not in result.stopped


def test_r4_rows_ahead_of_the_clock_name_both_times_say_runs_resume_by_themselves_and_what_to_do_sooner(tmp_path):
    run(tmp_path, ["AAA"], clock=Clock(T0 + 3 * 3600))
    with pytest.raises(cf.Refused) as e:
        run(tmp_path, ["AAA"])
    text = str(e.value)
    assert cf._iso(T0) in text and cf._iso(T0 + 3 * 3600) in text and "3 hours 0 minutes" in text and "\n" not in text
    assert "resume by themselves" in text and "--db" in text and "set the clock right" not in text, text


# ---- addendum 1, B2: a store file (or its -wal) that cannot be written is a one-line refusal, never a raw SQLite error

@pytest.mark.parametrize("which", ["store_file", "wal_file"])
@pytest.mark.parametrize("seen", ["before_sqlite_opens", "after_sqlite_opened"])
def test_b2_collecting_into_a_store_that_cannot_be_written_is_a_one_line_refusal(tmp_path, capsys, monkeypatch, which, seen):
    """The volume enforces the modes, so the module sees them before SQLite opens anything: refused, nothing changes. Where
    it could not see them (os.access made to say yes), SQLite's own error after it opened the store is the refusal, in
    words that say SQLite had opened it."""
    d = tmp_path / "d"
    d.mkdir()
    if which == "store_file":
        run(d, ["AAA"])
        target = d / "cf.sqlite"
    else:                                            # a writable store and folder beside a -wal (with content) that is not
        target = Path(str(_live_store(d)) + "-wal")
    os.chmod(target, 0o444)
    try:
        if seen == "after_sqlite_opened":
            monkeypatch.setattr(os, "access", lambda *a, **k: True)
        before = snap(d)
        fetch = Finnhub(Clock(T0 + 100))
        with pytest.raises(cf.Refused) as e:
            run(d, ["AAA"], fetch=fetch, clock=Clock(T0 + 100))
        text = str(e.value)
        rc = cf.main(["collect", "--symbols", "AAA", "--db", str(d / "cf.sqlite")], fetch=fetch, credentials=lambda n: KEY,
                     clock=Clock(T0 + 100))
        out = capsys.readouterr()
        monkeypatch.undo()
        assert "\n" not in text and rc == 2 and out.out == "" and len(out.err.strip().splitlines()) == 1 and "Traceback" not in out.err
        assert fetch.calls == []
        after = snap(d)
        if seen == "before_sqlite_opens":
            assert "nothing was done" in text and "read-only" in text and after == before, text
        else:
            assert "nothing was done" not in text and "SQLite" in text and "side files" in text, text
            assert after["cf.sqlite"] == before["cf.sqlite"] and after.get(target.name) == before[target.name]   # the file
            assert set(after) - set(before) <= {"cf.sqlite-wal", "cf.sqlite-shm"}, sorted(after)        # SQLite's own side files
    finally:
        os.chmod(target, 0o644)


# ======================================================================================================================
# T36a: the store keeps only the fields that changed (D1 to D11; D12 is measured outside the suite)
# ======================================================================================================================

DAMAGED = "does not match its hash; the store is damaged"
OBJ_KINDS = ("profile", "metrics", "insiders")
ARR_KINDS = ("earnings", "recommendations")
FIVE = OBJ_KINDS + ARR_KINDS


def _damaged_text(kind="profile", symbol="AAA"):
    return "a stored %s row for %s does not match its hash; the store is damaged" % (kind, symbol)


REFERENCE_COMMIT = "530ccce"                     # the commit that last changed the module before T36a (rev 4)
REFERENCE_FILE = "valedia_trader/data_and_fees/company_facts.py"
_SESSION = {}


@pytest.fixture(autouse=True, scope="session")
def _session_tmp(tmp_path_factory):
    """Where the reference module read from git is written, once per session: a pytest temporary directory, never the
    repository."""
    _SESSION["tmp"] = tmp_path_factory
    yield


def _reference_from_git() -> str:
    """The reference module's text from `git show REFERENCE_COMMIT:REFERENCE_FILE` in $CF_REFERENCE_REPO (default: the
    repository this file is in), written once per session to a pytest temporary file; skips, saying why, when git, the
    repository or the commit is not available. Only `git show` is run, and it only reads."""
    if "git_path" in _SESSION:
        return _SESSION["git_path"]
    repo = os.environ.get("CF_REFERENCE_REPO") or str(Path(__file__).resolve().parents[2])
    git = shutil.which("git")
    if git is None:
        pytest.skip("the reference module (rev 4): git is not available, and no $CF_REFERENCE_MODULE or side file")
    if not os.path.isdir(repo):
        pytest.skip("the reference module (rev 4): the repository %s is not there" % repo)
    try:
        p = subprocess.run([git, "--no-pager", "-C", repo, "show", "%s:%s" % (REFERENCE_COMMIT, REFERENCE_FILE)],
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0"))
    except (OSError, subprocess.SubprocessError) as e:
        pytest.skip("the reference module (rev 4): git could not be run: %s" % e)
    if p.returncode != 0:
        why = (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["no message"])[0]
        what = "is not a git repository" if "not a git repository" in why else "has not got commit %s (or its %s)" % (
            REFERENCE_COMMIT, REFERENCE_FILE)
        pytest.skip("the reference module (rev 4): %s %s: git said: %s" % (repo, what, why))
    path = _SESSION["tmp"].mktemp("cf_reference") / "rev4_company_facts.py"
    path.write_bytes(p.stdout)
    _SESSION["git_path"] = str(path)
    return str(path)


def _reference_module():
    """The module exactly as it was before T36a (rev 4), loaded under another name: the measure of "exactly as today".
    Found at $CF_REFERENCE_MODULE, else beside the repository as rev4_company_facts.py, else read from git (commit
    REFERENCE_COMMIT of $CF_REFERENCE_REPO or of this repository); a test that needs it skips, saying why, without all three."""
    name = "cf_rev4_reference"
    if name not in sys.modules:
        path = os.environ.get("CF_REFERENCE_MODULE") or str(Path(__file__).resolve().parents[3] / "rev4_company_facts.py")
        if not os.environ.get("CF_REFERENCE_MODULE") and not os.path.isfile(path):
            path = _reference_from_git()
        if not os.path.isfile(path):
            pytest.skip("the reference module (rev 4) is not here: %s" % path)
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    mod = sys.modules[name]
    assert mod.USER_VERSION == 1 and not hasattr(mod, "compact") and mod is not cf
    return mod


class Days:
    """A fetch that answers each (path, symbol) with that day's value of its history (`day` is set before each run).
    News is always empty: this ticket does not touch news."""
    def __init__(self, answers):
        self.answers, self.day = answers, 0

    def __call__(self, url, headers):
        call = Call(url, headers, 0)
        if call.path == "/company-news":
            return b"[]", {}
        seq = self.answers[(call.path, call.symbol)]
        return json.dumps(seq[min(self.day, len(seq) - 1)]).encode(), {}


def collect_day(mod, db, symbols, days, day):
    days.day = day
    clock = Clock(T0 + day * 86400)
    r = mod.collect(symbols, db, fetch=days, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY, monotonic=clock.monotonic)
    assert r.failed == 0 and r.stopped is None, r.failures
    return r


SPECIAL36 = [None, True, False, 0, 0.0, -0.0, 1, 1.0, 2 ** 70, -(2 ** 65), 1e-320, 1e308, -1e308, "", "é😀", "/", [], {}]
KEYS36 = ["", "-", "=", "~", "/", "ключ", "😀", "k" * 300, "a", "b", "c", "d", "e"]


def _v36(rnd, depth=0):
    r = rnd.random()
    if depth >= 3 or r < 0.5:
        return rnd.choice(SPECIAL36 + [rnd.randint(-9, 9), round(rnd.random() * 100, 3), "s%d" % rnd.randint(0, 9)])
    if r < 0.8:
        return {rnd.choice(KEYS36): _v36(rnd, depth + 1) for _ in range(rnd.randint(0, 4))}
    return [_v36(rnd, depth + 1) for _ in range(rnd.randint(0, 3))]


def _change36(rnd, v, depth=0):
    """A value near `v`, never changed in place: keys changed, added and removed; objects, arrays and scalars swapped."""
    if type(v) is dict:
        out = dict(v)
        for k in list(out):
            r = rnd.random()
            if r < 0.12:
                del out[k]
            elif r < 0.35:
                out[k] = _change36(rnd, out[k], depth + 1) if rnd.random() < 0.6 else _v36(rnd, depth + 1)
        if rnd.random() < 0.5:
            out[rnd.choice(KEYS36)] = _v36(rnd, depth + 1)
        return out
    if type(v) is list:
        out = [_change36(rnd, x, depth + 1) if rnd.random() < 0.3 else x for x in v]
        if rnd.random() < 0.3:
            out.append(_v36(rnd, depth + 1))
        if out and rnd.random() < 0.2:
            out.pop(rnd.randrange(len(out)))
        return out
    return _v36(rnd, depth)


def _history36(rnd, top, days):
    first = ({k: _v36(rnd, 1) for k in rnd.sample(KEYS36, rnd.randint(1, 7))} if top is dict
             else [_v36(rnd, 1) for _ in range(rnd.randint(0, 3))])
    seq = [first]
    while len(seq) < days:
        r = rnd.random()
        seq.append(rnd.choice(seq) if r < 0.1 else seq[-1] if r < 0.2 else _change36(rnd, seq[-1]))
    return seq


def _deep36(n, leaf):
    v = leaf
    for i in range(n):
        v = {"d": v, "i": i}
    return v


def _set36(d, **kv):
    out = dict(d)
    for k, v in kv.items():
        k = {"EMPTY": "", "SLASH": "/", "LONG": "k" * 300}.get(k, k)
        if v is _DROP:
            out.pop(k, None)
        else:
            out[k] = v
    return out


_DROP = object()


def _corners(top, days, rnd):
    """A history that holds every corner the ticket names, in a fixed order, then random changes."""
    if top is dict:
        s0 = {"a": None, "x": 1, "z": 0, "deep": _deep36(200, 1), "": {"-": [1, {"=": "~"}]}, "/": "é😀", "k" * 300: 2 ** 70,
              "f": 1e-320, "g": 1e308, "t": True, "e": {}, "l": [], "~": "=", "-": "-", "=": ["~"]}
        s1 = _set36(s0, x=1.0, z=0.0, deep=_deep36(200, 1.0), a=_DROP)              # 1 -> 1.0, 0 -> 0.0, a change 200 deep, a removal
        s2 = _set36(s1, x=True, z=-0.0, a=None, EMPTY={"-": [1, {"=": "~"}], "~": {}})  # 1.0 -> true, 0.0 -> -0.0, null added back
        s3 = _set36(s2, z=False, EMPTY=[], SLASH={"o": 1}, t=1, a=_DROP)          # object -> array, scalar -> object, true -> 1
        s4 = s0                                                                    # back to the first value
        s5 = _set36(s4, SLASH=[1, 2], e={"n": None}, l={"o": []}, f=0.0, g=-0.0, LONG=-(2 ** 70))
        s6 = _set36(s5, e=None, l=[], a={"x": {}})                                 # object -> null, null -> object
        seq = [s0, s1, s2, s3, s4, s5, s6, s5]
    else:
        seq = [[], [1], [1.0], [True], [{"a": 1}], [], [{"": None}, [], {}], [1]]
    while len(seq) < days:
        seq.append(_change36(rnd, seq[-1]))
    return seq[:days]


N_STORES, SYMS, N_DAYS, LONG_DAYS = 20, 10, 12, 75            # 20 stores x 10 symbols = 200 histories of five kinds each


def _plan36(s):
    """Store `s`'s symbols and answers: 9 random histories and one of every corner; store N_STORES is the long run."""
    rnd = random.Random(3600 + s)
    if s == N_STORES:
        symbols = ["LONG"]
        big = [rnd.randint(0, 10 ** 9) for _ in range(120)]
        answers = {(PATHS[k], "LONG"): [default_body(PATHS[k], "LONG")] for k in FIVE}
        answers[(PATHS["profile"], "LONG")] = [{"n": d, "big": big, "c": {"k": d % 3}} for d in range(LONG_DAYS)]
        answers[(PATHS["earnings_calendar"], None)] = [default_body(PATHS["earnings_calendar"], None)]
        return symbols, answers, LONG_DAYS
    symbols = ["Q%02d" % i for i in range(SYMS - 1)] + ["CRN"]
    answers = {}
    for sym in symbols:
        for kind in FIVE:
            top = dict if kind in OBJ_KINDS else list
            answers[(PATHS[kind], sym)] = _corners(top, N_DAYS, rnd) if sym == "CRN" else _history36(rnd, top, N_DAYS)
    answers[(PATHS["earnings_calendar"], None)] = _history36(rnd, dict, N_DAYS)
    return symbols, answers, N_DAYS


def _keys36(symbols):
    return [(s, k) for s in symbols for k in FIVE] + [("*", "earnings_calendar")]


def _times36(conn, keys):
    out = {}
    for sym, kind in keys:
        ts = sorted({r[0] for r in conn.execute("SELECT fetched_at FROM observations WHERE symbol=? AND kind=?", (sym, kind))})
        if not ts:
            out[(sym, kind)] = [T0 - 1.0, T0 + 10.0 ** 7]
            continue
        out[(sym, kind)] = ([ts[0] - 1.0, ts[0] - 0.001] + ts + [(a + b) / 2 for a, b in zip(ts, ts[1:])]
                            + [ts[-1] + 0.5, ts[-1] + 10.0 ** 7])
    return out


def _answer(mod, conn, sym, kind, when):
    try:
        got = mod.as_of(conn, sym, kind, when)
    except mod.Refused as e:
        return ("refused", str(e))
    if got is None:
        return None
    return (got, cf.canonical_json(got["body"]), json.dumps(got["body"]), type(got["fetched_at"]).__name__)   # (key order too)


def _answers36(mod, db, times, upto=None):
    conn = sqlite3.connect(db)
    try:
        return {(sym, kind, w): _answer(mod, conn, sym, kind, w) for (sym, kind), ws in times.items() for w in ws
                if upto is None or w < upto}
    finally:
        conn.close()


def _forms(db, symbol=None, kind=None):
    """The stored form of each row ('P' plain, 'F' full, 'D' changed-fields, '?' other), in chain order."""
    sql = ("SELECT CASE WHEN typeof(body)='text' THEN 'P' WHEN substr(body,1,2)=x'0146' THEN 'F' WHEN substr(body,1,2)=x'0144'"
           " THEN 'D' ELSE '?' END FROM observations")
    args = ()
    if symbol is not None:
        sql += " WHERE symbol=? AND kind=?"
        args = (symbol, kind)
    conn = sqlite3.connect(db)
    try:
        return "".join(r[0] for r in conn.execute(sql + " ORDER BY symbol, kind, fetched_at, rowid", args))
    finally:
        conn.close()


@pytest.fixture(scope="module")
def histories(tmp_path_factory):
    """The reference stores: every planned history collected day by day by the reference module (plain rows only), and
    the reference answers for every symbol and kind at every fetch time, between them, before the first and after the
    last."""
    ref = _reference_module()
    base = tmp_path_factory.mktemp("t36a")
    out = []
    for s in range(N_STORES + 1):
        symbols, answers, ndays = _plan36(s)
        db = base / ("ref%02d.sqlite" % s)
        days = Days(answers)
        for d in range(ndays):
            collect_day(ref, db, symbols, days, d)
        conn = sqlite3.connect(db)
        times = _times36(conn, _keys36(symbols))
        conn.close()
        out.append({"s": s, "symbols": symbols, "answers": answers, "days": ndays, "ref_db": db, "times": times,
                    "want": _answers36(ref, db, times)})
    return out


def _model_ok(h, db):
    """Independent of the reference: every answer this module gives holds the very answer fetched on its row's day, and no
    row of that symbol and kind lies between its fetch and `when`."""
    conn = sqlite3.connect(db)
    try:
        checked = 0
        for (sym, kind), ws in h["times"].items():
            stamps = sorted(r[0] for r in conn.execute("SELECT fetched_at FROM observations WHERE symbol=? AND kind=?", (sym, kind)))
            path = PATHS[kind]
            seq = h["answers"][(path, None if sym == "*" else sym)]
            for w in ws:
                got = cf.as_of(conn, sym, kind, w)
                before = [t for t in stamps if t <= w]
                if not before:
                    assert got is None
                    continue
                assert got["fetched_at"] == before[-1]
                day = int((got["fetched_at"] - T0) // 86400)
                assert cf.canonical_json(got["body"]) == cf.canonical_json(seq[min(day, len(seq) - 1)]), (sym, kind, w)
                checked += 1
        return checked
    finally:
        conn.close()


def test_t36a_1_this_collector_gives_exactly_the_reference_answers_over_200_histories(histories, tmp_path, capsys):
    ref = _reference_module()
    compared = histories_n = 0
    forms = ""
    for h in histories:
        db = tmp_path / ("mine%02d.sqlite" % h["s"])
        days = Days(h["answers"])
        for d in range(h["days"]):
            collect_day(cf, db, h["symbols"], days, d)
        got = _answers36(cf, db, h["times"])
        assert got.keys() == h["want"].keys()
        diffs = [k for k in got if got[k] != h["want"][k]]
        assert diffs == [], diffs[:3]
        compared += len(got)
        histories_n += len(h["symbols"])
        assert _model_ok(h, db) > 0
        forms += _forms(db)
        assert "P" not in _forms(db)                                   # this revision never writes a row plain
        for argv in (["show", h["symbols"][-1]], ["show", "*", "--kind", "earnings_calendar"]):
            outs = []
            for mod, store in ((ref, h["ref_db"]), (cf, db)):
                assert mod.main(argv + ["--db", str(store), "--as-of", "2026-11-17T12:00:00Z"]) == 0
                outs.append(capsys.readouterr().out)
            assert outs[0] == outs[1]
        if h["s"] == N_STORES:                                         # the long run: cut at 30 changed-fields rows, twice
            chain = _forms(db, "LONG", "profile")
            assert len(chain) == LONG_DAYS and chain == "F" + ("D" * 30 + "F") * 2 + "D" * (LONG_DAYS - 63), chain
        os.unlink(db)
    assert histories_n >= 200 and compared > 20000 and forms.count("D") >= 100 and forms.count("F") > 500
    print("T36a comparison 1: %d histories, %d answers compared, 0 differences, forms %d full %d changed"
          % (histories_n, compared, forms.count("F"), forms.count("D")), file=sys.stderr)


def test_t36a_2_compact_of_a_reference_store_changes_no_answer(histories, tmp_path):
    compared = 0
    for h in histories:
        db = tmp_path / "c.sqlite"
        for suffix in ("", "-wal"):                                    # (an empty -wal a read-only reader left, if any)
            if os.path.exists(str(h["ref_db"]) + suffix):
                shutil.copy(str(h["ref_db"]) + suffix, str(db) + suffix)
        assert _answers36(cf, db, h["times"]) == h["want"]               # a version-1 store read as found
        got = cf.compact(db)
        assert got["full"] + got["changed"] == len(_forms(db)) and got["damaged"] == 0
        assert set(_forms(db)) <= {"F", "D"}
        assert _answers36(cf, db, h["times"]) == h["want"]
        compared += 2 * len(h["want"])
        assert cf.compact(db)["full"] == cf.compact(db)["changed"] == 0     # a second run rewrites nothing
        for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)
    assert compared > 40000
    print("T36a comparison 2: %d stores, %d answers compared before and after compact, 0 differences" % (len(histories), compared),
          file=sys.stderr)


def test_t36a_3_a_reference_store_continued_by_this_collector_then_compacted(histories, tmp_path):
    ref = _reference_module()
    compared = mixed = 0
    for h in histories:
        db = tmp_path / "m.sqlite"
        days = Days(h["answers"])
        half = h["days"] // 2
        for d in range(half):
            collect_day(ref, db, h["symbols"], days, d)
        upto = T0 + half * 86400
        stage = _answers36(cf, db, h["times"], upto)
        assert stage == {k: v for k, v in h["want"].items() if k[2] < upto}
        for d in range(half, h["days"]):
            collect_day(cf, db, h["symbols"], days, d)
        forms = _forms(db)
        assert "P" in forms and ("F" in forms or "D" in forms)
        mixed += sum("PD" in _forms(db, sym, kind) for sym, kind in h["times"])
        assert _answers36(cf, db, h["times"]) == h["want"]
        cf.compact(db)
        assert "P" not in _forms(db)
        assert _answers36(cf, db, h["times"]) == h["want"]
        compared += len(stage) + 2 * len(h["want"])
        for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm"), Path(str(db) + ".lock")):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)
    assert compared > 40000 and mixed >= 10                       # changed-fields rows whose base is a plain row
    print("T36a comparison 3: %d stores, %d answers compared over three stages, 0 differences, %d chains with a changed-fields"
          " row on a plain base" % (len(histories), compared, mixed), file=sys.stderr)


def test_t36a_every_answer_is_the_one_fetched_on_its_day_without_the_reference_module(tmp_path):
    """The same histories, judged against the generated answers themselves (runs where the reference module is absent)."""
    for s in (0, 1, 2, N_STORES):
        symbols, answers, ndays = _plan36(s)
        db = tmp_path / ("m%d.sqlite" % s)
        days = Days(answers)
        for d in range(ndays):
            collect_day(cf, db, symbols, days, d)
        conn = sqlite3.connect(db)
        times = _times36(conn, _keys36(symbols))
        conn.close()
        assert _model_ok({"times": times, "answers": answers}, db) > 100
        cf.compact(db)
        assert _model_ok({"times": times, "answers": answers}, db) > 100


# ---- D6: the version

def _plain_store(path, rows_, version=1):
    """A store made by this module with hand-made plain rows (text and hash as the earlier revision wrote them), marked
    `version`. `rows_` holds (symbol, kind, fetched_at, value)."""
    cf._Writer(path).close()
    conn = sqlite3.connect(path)
    for sym, kind, at, value in rows_:
        text = cf.canonical_json(value)
        conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", (sym, kind, at, cf.body_sha256(text), text))
    conn.execute("PRAGMA user_version=%d" % version)
    conn.commit()
    conn.close()
    for suffix in ("-wal", "-shm", ".lock"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(str(path) + suffix)
    return path


def _header_version(p):
    return int.from_bytes(Path(p).read_bytes()[60:64], "big")


def test_t36a_a_new_store_is_made_at_version_2(tmp_path):
    assert (cf.USER_VERSION, cf.READABLE_VERSIONS, cf.MAX_DELTAS, cf.MAX_TEXT_BYTES) == (2, (1, 2), 30, 8 * cf.MAX_BODY_BYTES)
    run(tmp_path, ["AAA"])
    assert _header_version(tmp_path / "cf.sqlite") == 2 and rows(tmp_path, "PRAGMA user_version") == [(2,)]


@pytest.mark.parametrize("version", [0, 3, 7, 65536])
def test_t36a_a_store_of_another_version_is_refused_in_one_line_naming_both_versions_and_left_alone(tmp_path, version, capsys):
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0, {"v": 1})], version)
    before = snap(tmp_path)
    for call in (lambda: run(tmp_path, ["AAA"]), lambda: cf.as_of(db, "AAA", "profile", T0), lambda: cf.status(db),
                 lambda: cf.compact(db)):
        with pytest.raises(cf.Refused) as e:
            call()
        assert "a store of version %d; this program reads versions 1 and 2" % version in str(e.value) and "\n" not in str(e.value)
    assert cf.main(["compact", "--db", str(db)]) == 2 and len(capsys.readouterr().err.strip().splitlines()) == 1
    assert snap(tmp_path) == before


def _statements(monkeypatch, db):
    """Every statement run on a connection opened by name to `db` (the writer's), in order."""
    seen = []
    real = sqlite3.connect

    def spy(*a, **k):
        conn = real(*a, **k)
        if (a[0] if a else k.get("database")) == str(db):
            conn.set_trace_callback(seen.append)
        return conn
    monkeypatch.setattr(sqlite3, "connect", spy)
    return seen


@pytest.mark.parametrize("writer", ["collect", "compact"])
def test_t36a_a_writer_raises_a_version_1_store_to_2_in_its_own_transaction_after_its_probe_and_before_any_row(tmp_path, monkeypatch, writer):
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0, {"v": 1}), ("AAA", "profile", T0 + 1, {"v": 2})])
    assert _header_version(db) == 1
    seen = _statements(monkeypatch, db)
    if writer == "collect":
        run(tmp_path, ["AAA"], clock=Clock(T0 + 100))
    else:
        cf.compact(db)
    monkeypatch.undo()
    up = seen.index("PRAGMA user_version=2")
    assert seen[up - 1] == "BEGIN IMMEDIATE" and seen[up + 1] == "COMMIT"
    assert seen.index("DELETE FROM runs WHERE 0") < up                                   # after the write probe
    first_write = min(i for i, s in enumerate(seen) if s.split()[0].upper() in ("INSERT", "UPDATE"))
    assert up < first_write
    assert _header_version(db) == 2 and rows(tmp_path, "PRAGMA user_version") == [(2,)]
    seen = _statements(monkeypatch, db)                                                    # a version-2 store: not again
    cf.compact(db)
    assert not [s for s in seen if "user_version=" in s]


def test_t36a_a_version_1_store_is_read_by_every_reader_without_a_byte_written(tmp_path, capsys):
    d = tmp_path / "ro"
    d.mkdir()
    db = _plain_store(d / "cf.sqlite", [("AAA", "profile", T0, {"v": 1}), ("AAA", "metrics", T0 + 5, {"m": [1]})])
    before = snap(d)

    def read_all():
        assert cf.as_of(db, "AAA", "profile", T0 + 10)["body"] == {"v": 1}
        assert cf.news_as_of(db, "AAA", T0 + 10) == []
        assert cf.status(db)["bodies"]["plain"] == 2
        assert cf.main(["show", "AAA", "--db", str(db), "--as-of", "2026-11-14T13:00:00Z"]) == 0
        assert cf.main(["status", "--db", str(db)]) == 0
        capsys.readouterr()
    os.chmod(d, 0o555)                                    # a place that cannot hold side files: read immutably, nothing at all
    try:
        read_all()
    finally:
        os.chmod(d, 0o755)
    assert snap(d) == before
    read_all()                                            # a writable place: the store's own bytes and time do not change
    after = snap(d)
    assert after["cf.sqlite"] == before["cf.sqlite"] and _header_version(db) == 1
    assert set(after) - set(before) <= {"cf.sqlite-wal", "cf.sqlite-shm"}
    if "cf.sqlite-wal" in after:
        assert after["cf.sqlite-wal"][1] == 0                                              # SQLite's own empty side file


def test_t36a_the_earlier_revision_refuses_a_checkpointed_version_2_store_in_one_line_and_leaves_it_untouched(tmp_path, capsys):
    ref = _reference_module()
    run(tmp_path, ["AAA"])
    db = tmp_path / "cf.sqlite"
    assert not os.path.exists(str(db) + "-wal") and _header_version(db) == 2           # checkpointed when the writer closed
    before = snap(tmp_path)
    for call in (lambda: ref.as_of(db, "AAA", "profile", T0), lambda: ref.status(db),
                 lambda: ref.collect(["AAA"], db, fetch=Finnhub(Clock()), clock=Clock(T0 + 10), credentials=lambda n: KEY)):
        with pytest.raises(ref.Refused) as e:
            call()
        assert "a store of version 2; this program reads version 1" in str(e.value) and "nothing was done" in str(e.value)
    for argv in (["status"], ["show", "AAA"]):
        assert ref.main(argv + ["--db", str(db)]) == 2
        assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert snap(tmp_path) == before


# ---- D2, D3, D4: reading a chain, and damage

def _value36(n, salt=7):
    """A value that changes a little each step: `n` is set again each time and one key is added under "c" for good."""
    rnd = random.Random(salt)
    return {"blob": [rnd.randint(0, 10 ** 9) for _ in range(150)], "n": n, "c": {"f%d" % i: i for i in range(n)}}


def _chain_store(tmp_path, values, name="cf.sqlite", symbol="AAA"):
    """A store whose `symbol` profile history is `values`, one run per value 100 seconds apart, written by this collector."""
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", symbol): list(values) + [values[-1]]})
    for _ in values:
        run(tmp_path, [symbol], fetch=fetch, clock=clock, name=name)
        clock.now += 100
    return tmp_path / name


def _chain(db, symbol="AAA", kind="profile"):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT rowid, fetched_at, body_sha256, body FROM observations WHERE symbol=? AND kind=?"
                            " ORDER BY fetched_at, rowid", (symbol, kind)).fetchall()
    finally:
        conn.close()


def _readable(db, chain_rows, symbol="AAA", kind="profile"):
    """For each row of the chain: its value's canonical text, or 'damaged'."""
    out = []
    for _, at, _, _ in chain_rows:
        try:
            got = cf.as_of(db, symbol, kind, at)
            out.append("none" if got is None else cf.canonical_json(got["body"]))
        except cf.Refused as e:
            assert str(e) == _damaged_text(kind, symbol), str(e)
            out.append("damaged")
    return out


def _set_body(db, rowid, body, sha=None):
    conn = sqlite3.connect(db)
    if sha is None:
        conn.execute("UPDATE observations SET body=? WHERE rowid=?", (body, rowid))
    else:
        conn.execute("UPDATE observations SET body=?, body_sha256=? WHERE rowid=?", (body, sha, rowid))
    conn.commit()
    conn.close()


@pytest.fixture
def run12(tmp_path):
    """AAA profile: a full row and eleven changed-fields rows, with what each must read."""
    values = [_value36(n) for n in range(12)]
    db = _chain_store(tmp_path, values)
    ch = _chain(db)
    assert _forms(db, "AAA", "profile") == "F" + "D" * 11
    want = [cf.canonical_json(v) for v in values]
    assert _readable(db, ch) == want
    return db, ch, want


def test_t36a_a_changed_fields_row_is_one_zlib_stream_of_the_patch_against_the_row_before_it(run12):
    db, ch, want = run12
    form, text = fc.decode(ch[0][3])
    assert form == "F" and text == want[0] and ch[0][2] == cf.body_sha256(want[0])
    for i in range(1, len(ch)):
        form, patch = fc.decode(ch[i][3])
        assert form == "D" and patch == cf.canonical_json(fc.diff(json.loads(want[i - 1]), json.loads(want[i])))
        assert ch[i][2] == cf.body_sha256(want[i])                     # the hash is the answer's, whatever the form


@pytest.mark.parametrize("which", [0, 5])
def test_t36a_one_byte_flipped_at_every_position_of_a_small_body_is_refused_as_damaged(tmp_path, which):
    db = _chain_store(tmp_path, [{"v": 1}, {"v": 2, "w": "x" * 40}, {"v": 3, "w": "x" * 40}])
    if which == 5:                                     # a small changed-fields stream
        values = [_value36(n) for n in range(3)]
        db = _chain_store(tmp_path, values, name="d.sqlite")
        which = 1
    ch = _chain(db)
    rid, at, sha, body = ch[which]
    assert len(body) < 60
    good = _readable(db, ch)
    flips = refused = 0
    for i in range(len(body)):
        for bit in (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80):
            b = bytearray(body)
            b[i] ^= bit
            _set_body(db, rid, bytes(b))
            got = _readable(db, ch)
            assert got[:which] == good[:which] and set(got[which:]) <= {"damaged"} | set(good[which:]), (i, bit)
            if got[which] == "damaged":
                refused += 1
            else:                       # a flip of a padding bit after the last deflate code: the very same text, proved by
                assert got[which] == good[which] and i == len(body) - 5, (i, bit)    # its hash; never another body
            flips += 1
    _set_body(db, rid, body)
    assert _readable(db, ch) == good and flips == 8 * len(body) and refused >= flips - 7


DAMAGE = {
    # name: (what is done to the chain of twelve, the rows that must then refuse)
    "full_blob_truncated": (lambda db, ch: _set_body(db, ch[0][0], ch[0][3][:-1]), range(0, 12)),
    "full_blob_cut_to_its_prefix": (lambda db, ch: _set_body(db, ch[0][0], ch[0][3][:2]), range(0, 12)),
    "full_blob_with_a_byte_appended": (lambda db, ch: _set_body(db, ch[0][0], ch[0][3] + b"\x00"), range(0, 12)),
    "changed_blob_truncated": (lambda db, ch: _set_body(db, ch[4][0], ch[4][3][:-1]), range(4, 12)),
    "changed_blob_with_a_byte_appended": (lambda db, ch: _set_body(db, ch[4][0], ch[4][3] + b"\x00"), range(4, 12)),
    "changed_blob_with_a_second_stream_appended": (lambda db, ch: _set_body(db, ch[4][0], ch[4][3] + ch[4][3][2:]), range(4, 12)),
    "a_body_of_no_known_form": (lambda db, ch: _set_body(db, ch[4][0], b"\x01X" + ch[4][3][2:]), range(4, 12)),
    "a_one_byte_blob": (lambda db, ch: _set_body(db, ch[4][0], b"\x01"), range(4, 12)),
    "base_replaced_by_another_valid_answer_hash_kept": (
        lambda db, ch: _set_body(db, ch[0][0], fc.encode_full(cf.canonical_json(_value36(0, salt=8)))), range(0, 12)),
    "base_replaced_by_another_valid_plain_answer_hash_kept": (
        lambda db, ch: _set_body(db, ch[0][0], cf.canonical_json(_value36(0, salt=8))), range(0, 12)),
    "a_row_in_the_middle_replaced_by_another_valid_patch": (
        lambda db, ch: _set_body(db, ch[6][0], fc.encode_changed('{"extra":["=",1],"n":["=",6]}')), range(6, 12)),
    "a_patch_removing_a_missing_key": (lambda db, ch: _set_body(db, ch[6][0], fc.encode_changed('{"nokey":["-"]}')), range(6, 12)),
    "a_tilde_on_a_key_that_holds_no_object": (lambda db, ch: _set_body(db, ch[6][0], fc.encode_changed('{"n":["~",{}]}')), range(6, 12)),
    "an_unknown_edit": (lambda db, ch: _set_body(db, ch[6][0], fc.encode_changed('{"n":["+",6]}')), range(6, 12)),
    "a_patch_that_is_not_json": (lambda db, ch: _set_body(db, ch[6][0], fc.encode_changed('{"n":["=",6]')), range(6, 12)),
    "a_patch_with_a_repeated_key": (lambda db, ch: _set_body(db, ch[6][0], fc.encode_changed('{"n":["=",6],"n":["=",6]}')), range(6, 12)),
    "a_stream_that_inflates_past_the_cap": (
        lambda db, ch: _set_body(db, ch[6][0], b"\x01D" + zlib.compress(b" " * (cf.MAX_TEXT_BYTES + 10), 9)), range(6, 12)),
    "a_plain_row_whose_text_was_changed": (lambda db, ch: _set_body(db, ch[0][0], cf.canonical_json(_value36(0))[:-1] + " }"), [0]),
    "the_base_row_removed": (lambda db, ch: _sql(db, "DELETE FROM observations WHERE rowid=%d" % ch[0][0]), range(1, 12)),
    "two_rows_of_a_run_exchanged": (lambda db, ch: (_set_body(db, ch[3][0], ch[4][3]), _set_body(db, ch[4][0], ch[3][3])), [3, 4]),
    "a_row_of_the_run_removed": (lambda db, ch: _sql(db, "DELETE FROM observations WHERE rowid=%d" % ch[5][0]), range(6, 12)),
    "a_changed_row_stored_as_text": (lambda db, ch: _set_body(db, ch[4][0], ch[4][3].decode("latin-1")), range(4, 12)),
}


@pytest.mark.parametrize("name", list(DAMAGE))
def test_t36a_damage_is_refused_in_todays_words_never_a_wrong_body_and_a_chain_that_avoids_it_is_still_read(run12, name):
    """Rows at the damage refuse; rows before it read as before; a later row reads only when its own hash proves the
    result (a patch that sets every field the damage touched), so no row ever gives a wrong body."""
    db, ch, want = run12
    damage, refused = DAMAGE[name]
    damage(db, ch)
    got = _readable(db, ch)
    removed = {"the_base_row_removed": 0, "a_row_of_the_run_removed": 5}.get(name)
    for i in range(len(ch)):
        if i == removed:
            continue
        if i in refused:
            assert got[i] == "damaged", (name, i)
        elif i < min(refused):
            assert got[i] == want[i], (name, i)                        # a chain that does not pass through the damage
        else:
            assert got[i] in (want[i], "damaged"), (name, i)           # never a wrong body
    # the collector goes on after a damaged base: the new answer is stored full and reads; the old rows still refuse
    clock = Clock(T0 + 5000)
    result, _, _ = run(db.parent, ["AAA"], fetch=Finnhub(clock, {("/stock/profile2", "AAA"): [_value36(50)]}), clock=clock)
    assert result.failed == 0 and result.stored == 1 and cf.as_of(db, "AAA", "profile", T0 + 5000)["body"] == _value36(50)
    if got[-1] == "damaged":
        assert _forms(db, "AAA", "profile")[-1] == "F"
    after = _readable(db, ch)
    assert all(after[i] == "damaged" for i in refused if i != removed)


def test_t36a_more_than_30_changed_fields_rows_in_a_row_made_by_hand_are_damaged_from_the_31st(tmp_path):
    db = tmp_path / "cf.sqlite"
    cf._Writer(db).close()
    values = [_value36(n) for n in range(33)]
    conn = sqlite3.connect(db)
    text = cf.canonical_json(values[0])
    conn.execute("INSERT INTO observations VALUES ('AAA','profile',?,?,?)", (B, cf.body_sha256(text), fc.encode_full(text)))
    for i in range(1, 33):
        text = cf.canonical_json(values[i])
        patch = cf.canonical_json(fc.diff(values[i - 1], values[i]))
        conn.execute("INSERT INTO observations VALUES ('AAA','profile',?,?,?)", (B + i, cf.body_sha256(text), fc.encode_changed(patch)))
    conn.commit()
    conn.close()
    got = _readable(db, _chain(db))
    assert got[:31] == [cf.canonical_json(v) for v in values[:31]] and got[31:] == ["damaged", "damaged"]
    run(tmp_path, ["AAA"], clock=Clock(B + 100), fetch=Finnhub(Clock(), {("/stock/profile2", "AAA"): [_value36(40)]}))
    assert _forms(db, "AAA", "profile")[-1] == "F"                    # its base does not read back: stored full


def test_t36a_a_chain_that_reaches_its_beginning_with_no_full_row_is_damaged(tmp_path):
    db = tmp_path / "cf.sqlite"
    cf._Writer(db).close()
    text = cf.canonical_json({"n": 1})
    _sql(db, "INSERT INTO observations VALUES ('AAA','profile',%r,'%s',x'%s')" % (B, cf.body_sha256(text), fc.encode_changed('{}').hex()))
    with pytest.raises(cf.Refused) as e:
        cf.as_of(db, "AAA", "profile", B)
    assert str(e.value) == _damaged_text()


def test_t36a_nothing_fetched_after_when_is_looked_at_even_when_every_later_row_is_garbage(histories, tmp_path):
    """On compacted copies of reference stores, every row after `when` is replaced by garbage (and more garbage rows are
    added after it), inside a transaction rolled back after each read: the answer at `when` is unchanged."""
    checked = 0
    for h in histories[:6] + histories[-1:]:
        db = tmp_path / ("la%02d.sqlite" % h["s"])
        shutil.copy(h["ref_db"], db)
        cf.compact(db)
        conn = sqlite3.connect(db)
        try:
            for (sym, kind), ws in h["times"].items():
                for w in ws:
                    want = h["want"][(sym, kind, w)]
                    conn.execute("BEGIN")
                    conn.execute("UPDATE observations SET body=randomblob(40), body_sha256='garbage' WHERE fetched_at>?", (w,))
                    conn.execute("UPDATE observations SET body=CAST(x'0144' || randomblob(9) AS BLOB) WHERE fetched_at>? AND rowid % 2", (w,))
                    conn.execute("UPDATE observations SET body=CAST(randomblob(9) AS TEXT) WHERE fetched_at>? AND rowid % 3 = 0", (w,))
                    conn.execute("INSERT INTO observations VALUES (?,?,?,?,x'01447801')", (sym, kind, w + 0.0001, "x"))
                    conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", (sym, kind, w + 1, "y", "not json"))
                    assert _answer(cf, conn, sym, kind, w) == want, (sym, kind, w)
                    conn.execute("ROLLBACK")
                    checked += 1
        finally:
            conn.close()
    assert checked > 2000


def test_t36a_nothing_fetched_after_when_is_looked_at_in_a_chain_of_changed_fields_rows(run12):
    db, ch, want = run12
    conn = sqlite3.connect(db)
    for i, (_, at, _, _) in enumerate(ch):
        conn.execute("BEGIN")
        conn.execute("UPDATE observations SET body=CAST(x'0144' || randomblob(30) AS BLOB), body_sha256='z' WHERE fetched_at>?", (at,))
        assert cf.canonical_json(cf.as_of(conn, "AAA", "profile", at)["body"]) == want[i]
        assert cf.canonical_json(cf.as_of(conn, "AAA", "profile", at + 99.9)["body"]) == want[i]
        conn.execute("ROLLBACK")
    conn.close()


def test_t36a_as_of_reads_one_statement_and_never_more_than_32_rows(run12, monkeypatch):
    db, ch, want = run12
    seen = []
    real = sqlite3.connect

    def spy(*a, **k):
        conn = real(*a, **k)
        conn.set_trace_callback(seen.append)
        return conn
    monkeypatch.setattr(sqlite3, "connect", spy)
    assert cf.as_of(db, "AAA", "profile", T0 + 10 ** 6)["body"] == json.loads(want[-1])
    data = [s for s in seen if s.lstrip().upper().startswith("SELECT") and "sqlite_master" not in s]
    assert len(data) == 1 and "LIMIT 32" in data[0] and "fetched_at DESC, rowid DESC" in data[0]


# ---- D5: what a new answer is stored as

def test_t36a_a_new_answer_is_changed_fields_only_when_every_condition_holds(tmp_path):
    clock = Clock()
    big = [_value36(n) for n in range(3)]
    script = {("/stock/profile2", "AAA"): [big[0], big[1], {"v": 1}, {"v": 2}, big[2]],               # objects
              ("/stock/earnings", "AAA"): [[_value36(0)], [_value36(1)], [_value36(2)], [_value36(2)], [_value36(3)]],  # arrays
              ("/stock/metric", "AAA"): [big[0], [1], big[1], big[2], big[2]]}                         # an array in an object kind
    fetch = Finnhub(clock, script)
    for i in range(5):
        r, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
        assert r.failed == (1 if i == 1 else 0), r.failures                  # [1] is not a metrics answer
        clock.now += 100
    db = tmp_path / "cf.sqlite"
    assert _forms(db, "AAA", "metrics") == "FDD"
    assert _forms(db, "AAA", "profile") == "FDFFF"          # first: no base; {"v":1}, {"v":2}: rule (e); big[2] after {"v":2}: (e)
    assert _forms(db, "AAA", "earnings") == "FFFF"           # arrays are never patched
    assert cf.as_of(db, "AAA", "earnings", T0 + 10 ** 6)["body"] == [_value36(3)]


def test_t36a_a_damaged_base_is_neither_repaired_nor_hidden_and_the_new_answer_is_stored_full(run12):
    db, ch, want = run12
    _set_body(db, ch[-1][0], ch[-1][3][:-2])
    clock = Clock(T0 + 9000)
    r, _, _ = run(db.parent, ["AAA"], fetch=Finnhub(clock, {("/stock/profile2", "AAA"): [_value36(77)]}), clock=clock)
    assert r.failed == 0 and r.stored == 1 and _forms(db, "AAA", "profile") == "F" + "D" * 11 + "F"
    assert _readable(db, ch)[-1] == "damaged" and cf.as_of(db, "AAA", "profile", T0 + 9000)["body"] == _value36(77)
    assert _chain(db)[-2][3] == ch[-1][3][:-2]                    # not repaired


@pytest.mark.parametrize("fault", ["diff_recursion", "apply_raises", "dumps_raises", "diff_wrong", "encode_raises"])
def test_t36a_any_error_while_making_or_proving_the_patch_means_full_and_the_run_goes_on(tmp_path, monkeypatch, fault):
    values = [_value36(n) for n in range(4)]
    db = _chain_store(tmp_path, values[:2])
    real_diff = fc.diff

    def wrong(a, b):                                          # a patch that gets one field wrong
        p = real_diff(a, b)
        p["n"] = ["=", -1]
        return p
    if fault == "diff_recursion":
        monkeypatch.setattr(fc, "diff", lambda a, b: (_ for _ in ()).throw(RecursionError()))
    elif fault == "apply_raises":
        monkeypatch.setattr(fc, "apply", lambda a, p: (_ for _ in ()).throw(fc.Damaged()))
    elif fault == "dumps_raises":
        monkeypatch.setattr(fc, "diff", lambda a, b: {"x": ["=", float("nan")]})
    elif fault == "diff_wrong":
        monkeypatch.setattr(fc, "diff", wrong)
    else:
        monkeypatch.setattr(fc, "encode_changed", lambda t: (_ for _ in ()).throw(MemoryError()))
    clock = Clock(T0 + 1000)
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): values[2:]})
    for _ in range(2):
        r, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
        assert r.failed == 0 and r.stopped is None
        clock.now += 100
    monkeypatch.undo()
    assert _forms(db, "AAA", "profile") == "FDFF"
    assert _readable(db, _chain(db)) == [cf.canonical_json(v) for v in values]


def test_t36a_a_new_answer_that_could_land_before_its_base_in_chain_order_is_stored_full(tmp_path):
    """With a row at the largest rowid SQLite picks new rowids at random, so a new row stamped at its base's very time could
    sort before its base: it is stored full."""
    db = _chain_store(tmp_path, [_value36(0)])
    text = cf.canonical_json(_value36(1))
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO observations(rowid, symbol, kind, fetched_at, body_sha256, body) VALUES (?, 'AAA','profile',?,?,?)",
                 (2 ** 63 - 1, T0 + 100, cf.body_sha256(text), text))
    conn.commit()
    conn.close()
    clock = Clock(T0 + 100)
    run(tmp_path, ["AAA"], fetch=Finnhub(clock, {("/stock/profile2", "AAA"): [_value36(2)]}), clock=clock)
    ch = _chain(db)
    assert len(ch) == 3 and all(fc.decode(r[3])[0] == "F" for r in ch if type(r[3]) is bytes)
    assert _readable(db, ch).count("damaged") == 0


# ---- D7: compact

def _all_rows(db):
    conn = sqlite3.connect(db)
    try:
        return {r[0]: r[1:] for r in conn.execute("SELECT rowid, symbol, kind, fetched_at, body_sha256, body FROM observations")}
    finally:
        conn.close()


def _every_answer(db, keys):
    conn = sqlite3.connect(db)
    try:
        out = {}
        for sym, kind in keys:
            for (at,) in conn.execute("SELECT DISTINCT fetched_at FROM observations WHERE symbol=? AND kind=? AND typeof(fetched_at)"
                                      " IN ('integer','real') AND fetched_at BETWEEN ? AND ?", (sym, kind, cf.MIN_FETCH_TIME, cf.MAX_FETCH_TIME)).fetchall():
                for w in (at, at + 0.5):
                    out[(sym, kind, w)] = _answer(cf, conn, sym, kind, w)
        return out
    finally:
        conn.close()


def test_t36a_compact_rewrites_plain_rows_in_place_and_counts_what_it_left(tmp_path, capsys):
    db = tmp_path / "cf.sqlite"
    rows_ = [("AAA", "profile", T0 + i, _value36(i)) for i in range(6)]
    rows_ += [("BBB", "metrics", T0 + i, _value36(i, salt=3)) for i in range(4)]
    rows_ += [("AAA", "earnings", T0 + i, [_value36(i)]) for i in range(3)]
    rows_ += [("AAA", "unknownkind", T0 + 1, {"u": 1}), ("AAA", "news", T0 + 2, {"u": 2}), ("AAA", "profile", 1e300, {"t": 1}),
              ("AAA", "profile", 5.0, {"t": 2}), ("CCC", "insiders", T0, {"v": 1})]
    _plain_store(db, rows_)
    _sql(db, "UPDATE observations SET body=body || ' ' WHERE symbol='BBB' AND fetched_at=%r" % (T0 + 1))         # damaged plain
    keys = [("AAA", "profile"), ("BBB", "metrics"), ("AAA", "earnings"), ("CCC", "insiders")]
    before_rows, before = _all_rows(db), _every_answer(db, keys)
    assert cf.main(["compact", "--db", str(db)]) == 0
    out = capsys.readouterr()
    line = out.out.strip()
    assert "\n" not in line and out.err == "" and "blob" not in line and "{" not in line
    after_rows = _all_rows(db)
    assert after_rows.keys() == before_rows.keys()
    for rid, (sym, kind, at, sha, body) in before_rows.items():
        assert after_rows[rid][:4] == (sym, kind, at, sha)                         # rowid, time and hash never change
        if kind not in cf.STORED_KINDS or not cf._plausible(at) or (sym == "BBB" and at == T0 + 1):
            assert after_rows[rid][4] == body                                       # never touched
    assert _forms(db, "AAA", "profile") == "PFDDDDDP"                              # the two impossible times, untouched
    assert _forms(db, "BBB", "metrics") == "FPFD"                                  # the damaged row left; the row after it full
    assert _forms(db, "AAA", "earnings") == "FFF" and _forms(db, "CCC", "insiders") == "F"
    assert _every_answer(db, keys) == before
    numbers = [int(x) for x in re.findall(r"\d+", line)]
    assert numbers[:6] == [7, 6, 0, 1, 2, 2], line                                 # full, changed, encoded, damaged, impossible, unknown
    for words in ("7 rows rewritten as full", "6 as changed-fields", "0 already encoded", "1 damaged",
                  "2 with an impossible fetch time", "2 of no known kind", "bytes before", "free"):
        assert words in line, (words, line)
    got = cf.compact(db)
    assert got == {"full": 0, "changed": 0, "encoded": 13, "damaged": 1, "impossible": 2, "unknown_kind": 2,
                   "bytes_before": 0, "bytes_after": 0, "free_bytes": got["free_bytes"]}


def test_t36a_compact_returns_its_counts_and_the_bytes_before_and_after(tmp_path):
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0 + i, _value36(i)) for i in range(5)])
    before = sum(len(cf.canonical_json(_value36(i)).encode()) for i in range(5))
    got = cf.compact(db)
    stored = sum(len(r[4]) for r in _all_rows(db).values())
    assert (got["full"], got["changed"], got["bytes_before"], got["bytes_after"]) == (1, 4, before, stored)
    assert got["bytes_after"] * 3 < got["bytes_before"] and got["free_bytes"] >= 0


def test_t36a_compact_rewrites_a_plain_row_in_front_of_an_existing_run_full(tmp_path):
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0, _value36(0)), ("AAA", "profile", T0 + 1, _value36(1))])
    clock = Clock(T0 + 100)
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [_value36(n) for n in range(2, 33)]})
    for _ in range(31):
        run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
        clock.now += 100
    assert _forms(db, "AAA", "profile") == "PP" + "D" * 30 + "F"                    # a run of 30 on the plain row
    before = _every_answer(db, [("AAA", "profile")])
    got = cf.compact(db)
    assert _forms(db, "AAA", "profile") == "FF" + "D" * 30 + "F" and (got["full"], got["changed"]) == (2, 0)
    assert _every_answer(db, [("AAA", "profile")]) == before


def test_t36a_compact_never_rewrites_an_encoded_row(tmp_path):
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0 + i, _value36(i)) for i in range(4)])
    texts = [cf.canonical_json(_value36(i)) for i in range(4)]
    conn = sqlite3.connect(db)                       # rows 1 and 2 encoded by hand, in ways compact itself would not choose
    conn.execute("UPDATE observations SET body=? WHERE fetched_at=?", (b"\x01F" + zlib.compress(texts[1].encode(), 1), T0 + 1))
    conn.execute("UPDATE observations SET body=? WHERE fetched_at=?", (b"\x01F" + zlib.compress(texts[2].encode(), 9), T0 + 2))
    conn.commit()
    conn.close()
    before_rows = _all_rows(db)
    got = cf.compact(db)
    after_rows = _all_rows(db)
    assert (got["full"], got["changed"], got["encoded"]) == (1, 1, 2)
    for rid, r in before_rows.items():
        if type(r[4]) is bytes:
            assert after_rows[rid][4] == r[4]
    assert _forms(db, "AAA", "profile") == "FFFD"


def test_t36a_compact_rolls_a_chain_back_when_it_does_not_read_back_and_refuses_with_exit_2(tmp_path, monkeypatch, capsys):
    db = _plain_store(tmp_path / "cf.sqlite", [(s, "profile", T0 + i, _value36(i, salt=len(s))) for s in ("AAA", "BBB", "CCC")
                                               for i in range(4)])
    keys = [(s, "profile") for s in ("AAA", "BBB", "CCC")]
    before, before_rows = _every_answer(db, keys), _all_rows(db)
    real = fc.encode_full
    calls = []

    def wrong_for_bbb(text):                          # a full body of other text: its hash no longer holds
        calls.append(text)
        return real(text + " ") if '"n":0' in text and len(calls) > 1 else real(text)
    monkeypatch.setattr(fc, "encode_full", wrong_for_bbb)
    rc = cf.main(["compact", "--db", str(db)])
    out = capsys.readouterr()
    monkeypatch.undo()
    assert rc == 2 and out.out == "" and len(out.err.strip().splitlines()) == 1 and "rolled back" in out.err and "Traceback" not in out.err
    assert _forms(db, "AAA", "profile") == "FDDD"                                   # the chain before it stays compacted
    assert _forms(db, "BBB", "profile") == "PPPP" and _forms(db, "CCC", "profile") == "PPPP"
    assert {r: v for r, v in _all_rows(db).items() if v[0] != "AAA"} == {r: v for r, v in before_rows.items() if v[0] != "AAA"}
    assert _every_answer(db, keys) == before
    assert cf.compact(db)["full"] == 2 and _every_answer(db, keys) == before          # a rerun finishes


def test_t36a_compact_holds_the_collectors_lock_and_needs_an_existing_store(tmp_path, capsys):
    assert cf.main(["compact", "--db", str(tmp_path / "none.sqlite")]) == 2
    assert "no store" in capsys.readouterr().err and list(tmp_path.iterdir()) == []
    run(tmp_path, ["AAA"])
    fd = os.open(tmp_path / "cf.sqlite.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(cf.Refused) as e:
            cf.compact(tmp_path / "cf.sqlite")
        assert "another collector holds the store lock" in str(e.value)
    finally:
        os.close(fd)
    import inspect
    assert "compact" not in inspect.getsource(cf.collect) + inspect.getsource(cf._Run)


CHILD = ("import sys, json\n"
         "from valedia_trader.data_and_fees import company_facts as cf\n"
         "print('ready', flush=True)\n"
         "print(json.dumps(cf.compact(sys.argv[1])), flush=True)\n")


def _child_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(cf.__file__).resolve().parents[2])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _kill_template(path, nsym=8, ints=400):
    """A store of plain rows: `nsym` symbols, five kinds, 12 days, each answer holding `ints` random integers."""
    rnd = random.Random(52)
    rows_ = []
    for s in range(nsym):
        for kind in FIVE:
            v = {"blob": [rnd.randint(0, 10 ** 9) for _ in range(ints)], "n": 0, "o": {"k": 1}}
            for d in range(12):
                v = dict(v, n=d, o={"k": rnd.randint(0, 3)})
                if rnd.random() < 0.15:
                    v = dict(v, blob=[rnd.randint(0, 10 ** 9) for _ in range(ints)])
                rows_.append(("K%d" % s, kind, T0 + d * 86400, v if kind in OBJ_KINDS else [v]))
    return _plain_store(path, rows_), [("K%d" % s, k) for s in range(nsym) for k in FIVE]


def test_t36a_compact_under_50_real_sigkills_leaves_every_answer_as_before(tmp_path):
    template, keys = _kill_template(tmp_path / "template.sqlite")
    want = _every_answer(template, keys)
    assert len(want) == 8 * 5 * 12 * 2
    db = tmp_path / "k" / "cf.sqlite"
    db.parent.mkdir()
    shutil.copy(template, db)
    began = time.monotonic()
    cf.compact(db)
    span = time.monotonic() - began                                  # how long one compact of the template takes here
    rnd = random.Random(5050)
    kills = finished = attempts = 0
    while kills < 50:
        attempts += 1
        assert attempts < 200
        if attempts % 4 == 1:                                        # a fresh plain store now and then; otherwise go on killing
            for p in db.parent.iterdir():
                p.unlink()
            shutil.copy(template, db)
        proc = subprocess.Popen([sys.executable, "-c", CHILD, str(db)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=_child_env())
        try:
            assert proc.stdout.readline().strip() == b"ready"
            try:
                proc.wait(timeout=rnd.uniform(0.0, 1.3 * span))                  # a random moment inside compact
                finished += 1
            except subprocess.TimeoutExpired:
                proc.send_signal(signal.SIGKILL)
                proc.wait()
                kills += 1
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
        assert cf._store_problem(db) is None                         # the store opens whole
        assert _every_answer(db, keys) == want, attempts
    got = cf.compact(db)                                             # a rerun finishes
    assert _every_answer(db, keys) == want and "P" not in _forms(db)
    assert cf.compact(db)["full"] == 0
    print("T36a kills: %d SIGKILLs in %d attempts, %d runs finished first, compact took %.3f s, last rerun %s"
          % (kills, attempts, finished, span, got), file=sys.stderr)


READER = r'''
import json, os, random, sys, time
from valedia_trader.data_and_fees import company_facts as cf
db, want, stop = sys.argv[1], json.load(open(sys.argv[2])), sys.argv[3]
rnd = random.Random(7)
done, written, bad, stamps = 0, 0, [], []
print("ready", flush=True)
while not os.path.exists(stop) or done < 50:
    sym, kind, w, sha = rnd.choice(want)
    t = time.monotonic()
    try:
        got = cf.as_of(db, sym, kind, w)
        ok = got is not None and got["body_sha256"] == sha and cf.body_sha256(cf.canonical_json(got["body"])) == sha
    except cf.Refused as e:
        ok = "being written while it was read" in str(e)
        written += ok
    if not ok and len(bad) < 5:
        bad.append([sym, kind, w])
    stamps.append(t)
    done += 1
print(json.dumps({"done": done, "bad": bad, "written": written, "stamps": stamps}), flush=True)
'''


def test_t36a_readers_in_a_second_process_during_compact_and_collection_never_see_damage(tmp_path):
    db, keys = _kill_template(tmp_path / "cf.sqlite", nsym=16, ints=1500)
    conn = sqlite3.connect(db)
    want = []
    for sym, kind in keys:
        for (at,) in conn.execute("SELECT fetched_at FROM observations WHERE symbol=? AND kind=?", (sym, kind)).fetchall():
            for w in (at, at + 3600):
                got = cf.as_of(conn, sym, kind, w)
                want.append([sym, kind, w, got["body_sha256"]])
    plain = conn.execute("SELECT rowid, body FROM observations").fetchall()
    conn.close()
    wf, stop = tmp_path / "want.json", tmp_path / "stop"
    wf.write_text(json.dumps(want))
    proc = subprocess.Popen([sys.executable, "-c", READER, str(db), str(wf), str(stop)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=_child_env())
    windows = []
    try:
        assert proc.stdout.readline().strip() == b"ready"
        for r in range(14):                      # compact; then the test puts the plain rows back in one transaction; again
            if r:
                w = sqlite3.connect(db, isolation_level=None)
                w.execute("BEGIN IMMEDIATE")
                w.executemany("UPDATE observations SET body=? WHERE rowid=?", [(b, rid) for rid, b in plain])
                w.execute("COMMIT")
                w.close()
            t = time.monotonic()
            cf.compact(db)
            windows.append(("compact", t, time.monotonic()))
        answers = {}
        for sym, kind in keys:                                          # later answers, collected while the reader reads
            answers[(PATHS[kind], sym)] = [{"later": d} if kind in OBJ_KINDS else [d] for d in range(2000)]
        answers[(PATHS["earnings_calendar"], None)] = [{"c": 1}]
        days = Days(answers)
        t = time.monotonic()
        d = 12
        while time.monotonic() - t < 5.0 and d < 2000:                   # collect for at least 5 seconds
            collect_day(cf, db, sorted({s for s, _ in keys}), days, d)
            d += 1
        windows.append(("collect", t, time.monotonic()))
        stop.write_text("")
        out, err = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, err[-2000:]
    report = json.loads(out.strip().splitlines()[-1])
    assert report["bad"] == []
    during = {name: sum(1 for s in report["stamps"] for n, a, b in windows if n == name and a <= s <= b) for name in ("compact", "collect")}
    assert during["compact"] + during["collect"] >= 1000 and during["compact"] >= 300 and during["collect"] >= 300, during
    print("T36a concurrent reads: %d in all, %s during writes, %d written-while-read refusals"
          % (report["done"], during, report["written"]), file=sys.stderr)


# ---- D8: status

def test_t36a_status_counts_the_forms_in_sql_without_decoding_any_body(tmp_path, monkeypatch, capsys):
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0 + i, _value36(i)) for i in range(3)])
    cf.compact(db)
    _sql(db, "INSERT INTO observations VALUES ('AAA','profile',%r,'x','plain text')" % (T0 + 9),
         "INSERT INTO observations VALUES ('AAA','profile',%r,'x',x'00ff')" % (T0 + 10),
         "INSERT INTO observations VALUES ('AAA','profile',%r,'x',x'01')" % (T0 + 11),
         "INSERT INTO observations VALUES ('AAA','zzz',%r,'x',x'0146')" % (T0 + 12))
    stored = sum(len(r[4] if type(r[4]) is bytes else r[4].encode()) for r in _all_rows(db).values())
    monkeypatch.setattr(fc, "decode", lambda *a, **k: (_ for _ in ()).throw(AssertionError("status decoded a body")))
    st = cf.status(db)
    assert st["bodies"] == {"plain": 1, "full": 2, "changed": 2, "unknown": 2, "bytes": stored}
    assert cf.main(["status", "--db", str(db)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("rows: ")
    assert lines[2] == "bodies: 1 plain, 2 full, 2 changed-fields, 2 of no known form, %d bytes" % stored
    assert [x.split(":")[0] for x in lines[3:]] == ["symbols covered", "last run", "interrupted runs", "oldest fetch",
                                                    "newest fetch", "skipped and counted", "leftover temporary files"]


def test_t36a_status_text_is_todays_with_one_line_added(tmp_path, capsys):
    ref = _reference_module()
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0 + i, _value36(i)) for i in range(3)])
    assert ref.main(["status", "--db", str(db)]) == 0
    old = capsys.readouterr().out.splitlines()
    assert cf.main(["status", "--db", str(db)]) == 0
    new = capsys.readouterr().out.splitlines()
    assert new[:2] + new[3:] == old and new[2].startswith("bodies: 3 plain, 0 full, 0 changed-fields, 0 of no known form, ")


# ---- D11: three -wal cases owed from the last blind check

def _write_wal_d11(path, ps, frames, salts=(0x1A2B3C4D, 0x5E6F7081), other_salts_from=None, version=3007000, head_ps=None):
    """A -wal of `frames` whose header says `version` and `head_ps`, its header checksum right; frames from index
    `other_salts_from` on carry other salts, with every running checksum still made right."""
    head = struct.pack(">6I", 0x377F0682, version, head_ps or ps, 0, *salts)
    s = _wal_sum(head, (0, 0), False)
    out = [head, struct.pack(">2I", *s)]
    for i, (pg, commit, page) in enumerate(frames):
        fs = salts if other_salts_from is None or i < other_salts_from else (salts[0] ^ 0x5A5A5A5A, salts[1] ^ 0xA5A5A5A5)
        fh = struct.pack(">4I", pg, commit, *fs)
        s = _wal_sum(page, _wal_sum(fh[:8], s, False), False)
        out.append(fh + struct.pack(">2I", *s) + page)
    Path(path).write_bytes(b"".join(out))


def _d11(**kw):
    def make(d):
        p, k, _ = _kill_in_checkpoint(d)
        ps, frames = _read_wal(str(p) + "-wal")
        if kw.get("other_salts_from") == "first_missing":
            kw2 = dict(kw, other_salts_from=min(i for i, f in enumerate(frames) if f[0] > k))
        else:
            kw2 = kw
        _write_wal_d11(str(p) + "-wal", ps, frames, **kw2)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(str(p) + "-shm")
        return p
    return make


D11_REFUSED = {
    "cut_main_and_a_wal_whose_salts_are_wrong_from_frame_k_on_with_checksums_made_right": _d11(other_salts_from="first_missing"),
    "cut_main_and_a_wal_of_another_format_version": _d11(version=3007001),
    "cut_main_and_a_wal_of_another_page_size": _d11(head_ps=8192),
}


def test_t36a_d11_the_same_wal_written_right_opens(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    p = _d11()(d)
    assert cf._store_problem(p) is None


@pytest.mark.parametrize("name", list(D11_REFUSED))
def test_t36a_d11_a_cut_store_beside_such_a_wal_is_refused_and_every_side_file_is_untouched(tmp_path, name, capsys, opened_by_sqlite):
    d = tmp_path / "s"
    d.mkdir()
    path = D11_REFUSED[name](d)
    before = snap(d)
    del opened_by_sqlite[:]
    fetch = Finnhub(Clock(T0 + 100))
    with pytest.raises(cf.Refused) as e:
        run(d, ["AAA"], fetch=fetch, clock=Clock(T0 + 100))
    assert "cut short" in str(e.value) and "nothing was done" in str(e.value) and "\n" not in str(e.value)
    for call in (lambda: cf.as_of(path, "AAA", "profile", T0 + 100), lambda: cf.status(path), lambda: cf.compact(path)):
        with pytest.raises(cf.Refused) as e:
            call()
        assert "nothing was done" in str(e.value)
    for argv in (["status"], ["show", "AAA"], ["compact"]):
        assert cf.main(argv + ["--db", str(path)]) == 2
        assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert fetch.calls == [] and snap(d) == before
    assert not [n for n in opened_by_sqlite if str(d) in n]


# ---- T36a addendum 1: the chain's order when rowids run against the fetch times, and a tie decided by the rowid

def _ordered_store(path, rows_, version=2):
    """A store whose AAA profile chain is made by direct SQL: `rows_` holds (rowid, fetched_at, value, form), form 'P'
    (plain text), 'F' (full) or 'D' (changed-fields against the value before it in (fetched_at, rowid) order). Bodies are
    made in chain order; rows are inserted in the order given, with their explicit rowids."""
    cf._Writer(path).close()
    bodies, prev = {}, None
    for rowid, at, value, form in sorted(rows_, key=lambda r: (r[1], r[0])):
        text = cf.canonical_json(value)
        bodies[rowid] = {"P": lambda: text, "F": lambda: fc.encode_full(text),
                         "D": lambda: fc.encode_changed(cf.canonical_json(fc.diff(prev, value)))}[form]()
        prev = value
    conn = sqlite3.connect(path)
    for rowid, at, value, _ in rows_:
        conn.execute("INSERT INTO observations(rowid, symbol, kind, fetched_at, body_sha256, body) VALUES (?,?,?,?,?,?)",
                     (rowid, "AAA", "profile", at, cf.body_sha256(cf.canonical_json(value)), bodies[rowid]))
    conn.execute("PRAGMA user_version=%d" % version)
    conn.commit()
    conn.close()
    for suffix in ("-wal", "-shm", ".lock"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(str(path) + suffix)
    return path


def _known(rows_):
    """(when, the answer as_of must give) at every fetch time, between them, before the first and after the last; the
    answer is (value, its hash, its fetch time, its key order) of the newest row at or before `when` in (fetched_at, rowid)
    order, so on a tie the larger rowid."""
    chain = sorted(rows_, key=lambda r: (r[1], r[0]))
    ts = sorted({r[1] for r in chain})
    whens = [ts[0] - 1.0] + ts + [(a + b) / 2 for a, b in zip(ts, ts[1:])] + [ts[-1] + 0.5, ts[-1] + 10.0 ** 6]
    out = []
    for w in whens:
        at_or_before = [r for r in chain if r[1] <= w]
        if not at_or_before:
            out.append((w, None))
            continue
        _, at, value, _ = at_or_before[-1]
        text = cf.canonical_json(value)
        out.append((w, (value, cf.body_sha256(text), at, json.dumps(json.loads(text)))))
    return out


def _check_known(db, known):
    for w, want in known:
        got = cf.as_of(db, "AAA", "profile", w)
        if want is None:
            assert got is None, w
            continue
        assert got is not None, w
        assert (got["body"], got["body_sha256"], got["fetched_at"], json.dumps(got["body"])) == want, w
        assert (got["symbol"], got["kind"]) == ("AAA", "profile")


def _against(forms):
    """Rows whose rowids run against their fetch times: the earliest time has the largest rowid."""
    n = len(forms)
    return [(100 * (n - i), T0 + 100 * i, _value36(i), forms[i]) for i in range(n)]


@pytest.mark.parametrize("insert", ["by_rowid", "by_time"])
def test_t36a_as_of_reads_the_chain_in_fetch_time_order_when_the_rowids_run_against_it(tmp_path, insert):
    rows_ = _against("FDDDFDDDD")
    rows_ = sorted(rows_, key=lambda r: r[0]) if insert == "by_rowid" else rows_
    db = _ordered_store(tmp_path / "cf.sqlite", rows_)
    assert _forms(db, "AAA", "profile") == "FDDDFDDDD"
    _check_known(db, _known(rows_))


def test_t36a_compact_of_plain_rows_whose_rowids_run_against_their_times_writes_the_chain_in_time_order(tmp_path):
    rows_ = _against("PPPPPPPPP")
    db = _ordered_store(tmp_path / "cf.sqlite", sorted(rows_, key=lambda r: r[0]), version=1)
    known = _known(rows_)
    _check_known(db, known)
    got = cf.compact(db)
    assert (got["full"], got["changed"], got["damaged"]) == (1, 8, 0)
    assert _forms(db, "AAA", "profile") == "F" + "D" * 8
    chain = _chain(db)                                                  # (fetched_at, rowid) order
    assert [r[0] for r in chain] == [100 * (9 - i) for i in range(9)]  # the rowids, unchanged, against the times
    for i, (_, _, _, body) in enumerate(chain[1:], 1):                # each patch is against the row before it IN TIME
        assert fc.decode(body) == ("D", cf.canonical_json(fc.diff(_value36(i - 1), _value36(i))))
    _check_known(db, known)
    again = cf.compact(db)
    assert (again["full"], again["changed"]) == (0, 0)                  # a second run rewrites nothing


@pytest.mark.parametrize("first", ["smaller_rowid_inserted_first", "larger_rowid_inserted_first"])
@pytest.mark.parametrize("forms", ["FDDD", "PPPP"])
def test_t36a_on_equal_fetch_times_the_larger_rowid_is_the_later_row(tmp_path, first, forms):
    """Rows 1 and 2 share a fetch time; the rowid decides which is later, whatever order they were inserted in. As
    written by hand (full and changed-fields) and as plain rows then compacted."""
    rows_ = [(50, T0, _value36(0), forms[0]), (70, T0 + 100, _value36(1), forms[1]), (71, T0 + 100, _value36(2), forms[2]),
             (20, T0 + 200, _value36(3), forms[3])]
    order = [0, 1, 2, 3] if first == "smaller_rowid_inserted_first" else [0, 2, 1, 3]
    db = _ordered_store(tmp_path / "cf.sqlite", [rows_[i] for i in order], version=2 if forms == "FDDD" else 1)
    known = _known(rows_)
    assert [w for w, want in known if want is not None and want[0] == _value36(1)] == []   # row 70 is never the answer
    _check_known(db, known)
    if forms == "PPPP":
        got = cf.compact(db)
        assert (got["full"], got["changed"]) == (1, 3) and _forms(db, "AAA", "profile") == "FDDD"
        chain = _chain(db)
        assert [r[0] for r in chain] == [50, 70, 71, 20]
        for i, (_, _, _, body) in enumerate(chain[1:], 1):
            assert fc.decode(body) == ("D", cf.canonical_json(fc.diff(_value36(i - 1), _value36(i))))
        _check_known(db, known)
    assert cf.as_of(db, "AAA", "profile", T0 + 100)["body"] == _value36(2)


# ---- T36a addendum 3. H1: no reading depends on nesting or on the caller's stack; H2: the body is the parse of the proved
# text; H3: every body read as bytes, one sentence for damage; H5: no encoded row while the main file says version 1

DEPTHS3 = [1, 10, 64, 65, 100, 500, 800, 900, 985, 995, 1500]
CALLERS3 = [0, 100, 300, 900]
BULK3 = "".join(map(str, random.Random(3).choices(range(10), k=3000)))      # 3,000 random digits: a full copy is large


def _deep_text3(n, kind, day):
    """An answer object nesting exactly `n` levels (the object itself is one): a long string that never changes, and a
    value under "deep" inside n - 1 levels of objects ("o"), arrays ("a") or both in turn ("m"), ending in `day`."""
    opens, closes = [], []
    for i in range(n - 1):
        k = "oa"[i % 2] if kind == "m" else kind
        opens.append('{"k":' if k == "o" else "[")
        closes.append("}" if k == "o" else "]")
    return ('{"bulk":"%s","deep":%s%d%s}' % (BULK3, "".join(opens), day, "".join(reversed(closes)))).encode()


def _tokens3(v):
    """The value as a flat list of (type, key order, scalar repr), made without recursion: equal lists mean equal values
    with equal key order and equal number types."""
    out, todo = [], [v]
    while todo:
        x = todo.pop()
        t = type(x)
        if t is dict:
            out.append(("dict", tuple(x)))
            todo.extend(reversed(list(x.values())))
        elif t is list:
            out.append(("list", len(x)))
            todo.extend(reversed(x))
        else:
            out.append((t.__name__, float.__repr__(x) if t is float else x))
    return out


def _at_depth3(n, fn):
    return fn() if n == 0 else _at_depth3(n - 1, fn)


def _outcome3(mod, db, when, frames):
    try:
        got = _at_depth3(frames, lambda: mod.as_of(db, "AAA", "profile", when))
    except mod.Refused as e:
        return ("damaged",) if str(e) == _damaged_text() else ("refused", str(e)[:60])
    except RecursionError:
        return ("RecursionError",)
    except Exception as e:                                      # noqa: BLE001 - every outcome is recorded, none hidden
        return (type(e).__name__,)
    if got is None:
        return ("none",)
    return ("ok", got["body_sha256"], got["fetched_at"], _tokens3(got["body"]))


def _collect3(mod, db, answer, day):
    clock = Clock(T0 + day * 86400)
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [answer]})
    return mod.collect(["AAA"], db, fetch=fetch, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY,
                       monotonic=clock.monotonic)


SCENARIOS3 = {"first": lambda n, k: [_deep_text3(n, k, 0)],
              "from_shallow": lambda n, k: [_deep_text3(1, k, 0), _deep_text3(n, k, 1)],
              "from_deep": lambda n, k: [_deep_text3(n, k, 0), _deep_text3(n, k, 1), _deep_text3(n, k, 2)]}


def _short3(o):
    return "ok" if o[0] == "ok" else o[0]


@pytest.mark.parametrize("n", DEPTHS3)
def test_t36a_a_deep_answer_reads_as_the_reference_reads_it_from_every_caller_stack(tmp_path, n):
    ref = _reference_module()
    table = []
    for kind in ("o", "a", "m"):
        for scen, make in SCENARIOS3.items():
            answers = make(n, kind)
            dbs = {}
            for writer, mod in (("ref", ref), ("this", cf)):
                d = tmp_path / ("%s_%s_%s" % (writer, kind, scen))
                d.mkdir()
                dbs[writer] = d / "cf.sqlite"
                for day, a in enumerate(answers):
                    _collect3(mod, dbs[writer], a, day)
            forms = _forms(dbs["this"], "AAA", "profile")
            deep_d = n > 64 and "D" in forms                                    # a changed-fields row for a deep value
            for day in range(len(answers)):
                when = T0 + day * 86400 + 5
                r = [_outcome3(ref, dbs["ref"], when, f) for f in CALLERS3]
                a = [_outcome3(cf, dbs["ref"], when, f) for f in CALLERS3]
                b = [_outcome3(cf, dbs["this"], when, f) for f in CALLERS3]
                for i in range(len(CALLERS3)):
                    if r[i][0] == "ok":
                        assert a[i] == r[i] and b[i] == r[i], (kind, scen, day, CALLERS3[i])
                for x in (a, b):
                    pattern = [o[0] == "ok" for o in x]
                    if len(set(pattern)) > 1:                                    # reads at one caller depth, not another:
                        assert pattern == [o[0] == "ok" for o in r], (kind, scen, day, x)   # only as the reference does
                table.append("%s %s d%d forms=%s ref=%s this(ref store)=%s this(own store)=%s" % (
                    kind, scen, day, forms, "/".join(map(_short3, r)), "/".join(map(_short3, a)), "/".join(map(_short3, b))))
            for p_ in dbs.values():
                shutil.rmtree(p_.parent)
            assert not deep_d, (kind, scen, forms)                              # (MAX_PATCH_DEPTH is 64)
    assert cf.MAX_PATCH_DEPTH == 64
    print("T36a H1 depth %d (callers %s):\n  %s" % (n, CALLERS3, "\n  ".join(table)), file=sys.stderr)


@pytest.mark.parametrize("kind", ["o", "a", "m"])
def test_t36a_a_value_nesting_64_levels_may_be_changed_fields_and_one_of_65_never(tmp_path, kind):
    for n, want in ((64, "FD"), (65, "FF")):
        d = tmp_path / str(n)
        d.mkdir()
        _collect3(cf, d / "cf.sqlite", _deep_text3(n, kind, 0), 0)
        _collect3(cf, d / "cf.sqlite", _deep_text3(n, kind, 1), 1)
        assert _forms(d / "cf.sqlite", "AAA", "profile") == want, n
    d = tmp_path / "into"                                                     # shallow base, deep new value: full
    d.mkdir()
    _collect3(cf, d / "cf.sqlite", _deep_text3(3, kind, 0), 0)
    _collect3(cf, d / "cf.sqlite", _deep_text3(65, kind, 1), 1)
    _collect3(cf, d / "cf.sqlite", _deep_text3(3, kind, 2), 2)                 # and deep base, shallow new value: full
    assert _forms(d / "cf.sqlite", "AAA", "profile") == "FFF"


def test_t36a_compact_stores_a_deep_value_full(tmp_path):
    vals = [json.loads(_deep_text3(n, "m", i)) for i, n in enumerate((3, 70, 70, 3, 64, 64))]
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0 + i, v) for i, v in enumerate(vals)])
    before = _every_answer(db, [("AAA", "profile")])
    cf.compact(db)
    assert _forms(db, "AAA", "profile") == "FFFFDD"                            # 3, 70, 70, 3: full; 64 on 3, 64 on 64: changed
    assert _every_answer(db, [("AAA", "profile")]) == before


def test_t36a_the_body_returned_is_the_parse_of_the_proved_text_whatever_the_base_looks_like(tmp_path):
    """H2: a plain base reformatted (keys in another order, value and hash kept), then a changed-fields row: the body's
    key order and number types are those of the canonical text, as the earlier revision would give them."""
    v0 = {"z": 1, "a": {"y": 2.0, "b": [1, 1.0]}, "m": 0, "bulk": BULK3}
    v1 = dict(v0, m=1)                                                       # no key added: nothing re-sorts the keys
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0, v0)])
    reordered = json.dumps({"z": 1, "m": 0, "bulk": BULK3, "a": {"y": 2.0, "b": [1, 1.0]}}, separators=(",", ":"))
    assert reordered != cf.canonical_json(v0) and json.loads(reordered) == v0
    conn = sqlite3.connect(db)
    conn.execute("UPDATE observations SET body=? WHERE fetched_at=?", (reordered, T0))
    conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", ("AAA", "profile", T0 + 100, cf.body_sha256(cf.canonical_json(v1)),
                                                                 fc.encode_changed(cf.canonical_json(fc.diff(v0, v1)))))
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()
    with pytest.raises(cf.Refused) as e:                                      # the base itself: its text is not its hash's
        cf.as_of(db, "AAA", "profile", T0)
    assert str(e.value) == _damaged_text()
    got = cf.as_of(db, "AAA", "profile", T0 + 100)
    canonical = json.loads(cf.canonical_json(v1))
    assert got["body"] == v1 and _tokens3(got["body"]) == _tokens3(canonical)
    assert json.dumps(got["body"]) == json.dumps(canonical)


MARK3 = b"SECRET-BODY-BYTES"


def _bad_utf8_store(tmp_path, where):
    """A store whose plain row is not UTF-8 (`where`: "asked" the row asked for, "passed" the base of a changed-fields
    row asked for). Its bytes carry MARK3, so any message quoting the body shows it."""
    v0, v1 = {"a": 1, "bulk": BULK3}, {"a": 2, "bulk": BULK3}
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0, v0)])
    conn = sqlite3.connect(db)
    bad = b'{"a":1,"x":"' + MARK3 + b'\xff\xfe"}'
    conn.execute("UPDATE observations SET body=CAST(? AS TEXT) WHERE fetched_at=?", (bad, T0))
    if where == "passed":
        conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", ("AAA", "profile", T0 + 100, cf.body_sha256(cf.canonical_json(v1)),
                                                                     fc.encode_changed(cf.canonical_json(fc.diff(v0, v1)))))
        conn.execute("PRAGMA user_version=2")
    conn.commit()
    assert conn.execute("SELECT typeof(body) FROM observations WHERE fetched_at=?", (T0,)).fetchone() == ("text",)
    conn.close()
    return db, (T0 if where == "asked" else T0 + 100)


@pytest.mark.parametrize("where", ["asked", "passed"])
def test_t36a_a_text_body_that_is_not_utf8_is_the_damaged_refusal_in_one_sentence_with_no_byte_of_it(tmp_path, where, capsys):
    db, when = _bad_utf8_store(tmp_path, where)
    with pytest.raises(cf.Refused) as e:
        cf.as_of(db, "AAA", "profile", when)
    assert str(e.value) == _damaged_text()
    conn = sqlite3.connect(db)                                                 # a caller's own connection: the same refusal
    try:
        with pytest.raises(cf.Refused) as e:
            cf.as_of(conn, "AAA", "profile", when)
        assert str(e.value) == _damaged_text()
    finally:
        conn.close()
    iso = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for argv in (["show", "AAA", "--kind", "profile", "--as-of", iso], ["show", "AAA"], ["status"]):
        cf.main(argv + ["--db", str(db)])
        out = capsys.readouterr()
        for text in (out.out, out.err):
            assert MARK3.decode() not in text and "�" not in text and "Traceback" not in text
    got = cf.compact(db)                                                       # left as found, counted
    assert got["damaged"] == 1


def _snapshot_holder(db):
    """A second process holding a read snapshot of `db` until its stdin closes."""
    code = ("import sqlite3, sys\n"
            "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
            "c.execute('BEGIN')\n"
            "c.execute('SELECT count(*) FROM observations').fetchone()\n"
            "print('holding', flush=True)\n"
            "sys.stdin.read()\n"
            "c.execute('COMMIT')\n")
    proc = subprocess.Popen([sys.executable, "-c", code, str(db)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=_child_env(), text=True)
    assert proc.stdout.readline().strip() == "holding"
    return proc


def _release(proc):
    proc.stdin.close()
    proc.wait(timeout=30)


@pytest.mark.parametrize("writer", ["collect", "compact"])
def test_t36a_a_raise_a_reader_holds_back_is_a_one_line_refusal_with_no_row_written(tmp_path, writer, capsys):
    v = [{"a": i, "bulk": BULK3} for i in range(3)]
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0 + i, v[i]) for i in range(2)])
    before = _all_rows(db)
    known = _every_answer(db, [("AAA", "profile")])
    ref = _reference_module()
    proc = _snapshot_holder(db)
    try:
        argv = (["compact"] if writer == "compact" else ["collect", "--symbols", "AAA"]) + ["--db", str(db)]
        if writer == "collect":
            with pytest.raises(cf.Refused) as e:
                _collect3(cf, db, json.dumps(v[2]).encode(), 1)
            msg = str(e.value)
        else:
            assert cf.main(argv) == 2
            msg = capsys.readouterr().err.strip()
        assert "\n" not in msg and "still says version 1 in its main file" in msg and "no row was written" in msg
        assert _header_version(db) == 1 and _all_rows(db) == before                   # nothing else written
        assert rows(tmp_path, "SELECT count(*) FROM runs") == [(0,)]
        assert _every_answer(db, [("AAA", "profile")]) == known                       # read correctly by this revision
        for at in (T0, T0 + 1, T0 + 5):                                               # and by the earlier one
            assert ref.as_of(db, "AAA", "profile", at)["body"] == cf.as_of(db, "AAA", "profile", at)["body"]
    finally:
        _release(proc)
    if writer == "collect":                                                           # the next run tries again
        r = _collect3(cf, db, json.dumps(v[2]).encode(), 1)
        assert r.stored >= 1 and _forms(db, "AAA", "profile") == "PPD"
    else:
        assert cf.compact(db)["changed"] == 1 and _forms(db, "AAA", "profile") == "FD"
    assert _header_version(db) == 2
    after = _every_answer(db, [("AAA", "profile")])
    assert {k: after[k] for k in known} == known


@pytest.mark.parametrize("writer", ["collect", "compact"])
def test_t36a_unhindered_the_main_files_header_says_2_before_the_first_row_is_written(tmp_path, monkeypatch, writer):
    db = _plain_store(tmp_path / "cf.sqlite", [("AAA", "profile", T0 + i, {"a": i, "bulk": BULK3}) for i in range(2)])
    assert _header_version(db) == 1
    seen = []
    real = sqlite3.connect

    def spy(*a, **k):
        conn = real(*a, **k)
        if (a[0] if a else k.get("database")) == str(db):
            conn.set_trace_callback(lambda s: seen.append((s, _header_version(db))))
        return conn
    monkeypatch.setattr(sqlite3, "connect", spy)
    if writer == "collect":
        _collect3(cf, db, json.dumps({"a": 9, "bulk": BULK3}).encode(), 1)
    else:
        cf.compact(db)
    monkeypatch.undo()
    writes = [i for i, (s, _) in enumerate(seen) if s.split()[0].upper() in ("INSERT", "UPDATE")]
    up = [i for i, (s, _) in enumerate(seen) if s == "PRAGMA user_version=2"]
    assert writes and up and up[0] < writes[0]
    assert all(h == 2 for _, h in seen[writes[0]:]), seen[writes[0]]
    assert any("wal_checkpoint" in s for s, _ in seen[up[0]:writes[0]])
    assert "D" in _forms(db, "AAA", "profile") or writer == "collect"


KILL_RAISE3 = r'''
import json, os, signal, sqlite3, sys
from valedia_trader.data_and_fees import company_facts as cf
db, n, writer, t0, bulk = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4]), sys.argv[5]
count = [0]
real = sqlite3.connect
def cb(s):
    count[0] += 1
    if count[0] == n:
        os.kill(os.getpid(), signal.SIGKILL)
def spy(*a, **k):
    c = real(*a, **k)
    if (a[0] if a else k.get("database")) == db:
        c.set_trace_callback(cb)
    return c
sqlite3.connect = spy
now = [t0]
def fetch(url, headers):
    if "profile2" in url:
        return json.dumps({"a": 9, "bulk": bulk}).encode(), {}
    if "calendar" in url:
        return b'{"earningsCalendar":[]}', {}
    if "metric" in url or "insider" in url:
        return b'{"x":1}', {}
    return b"[]", {}
def sleep(s):
    now[0] += s
if writer == "collect":
    cf.collect(["AAA"], db, fetch=fetch, clock=lambda: now[0], sleep=sleep, credentials=lambda n: "fhk-9Q7xT2mLpW4vB8nZc3RsD6aYe1UjHg0K",
               monotonic=lambda: now[0])
else:
    cf.compact(db)
print("finished", count[0], flush=True)
'''


@pytest.mark.parametrize("writer", ["collect", "compact"])
def test_t36a_a_kill_at_every_statement_of_the_raise_leaves_version_1_with_no_encoded_row_or_version_2(tmp_path, writer):
    (tmp_path / "t").mkdir()
    template = _plain_store(tmp_path / "t" / "cf.sqlite", [("AAA", "profile", T0 + i, {"a": i, "bulk": BULK3}) for i in range(3)])
    known = _every_answer(template, [("AAA", "profile")])
    ref = _reference_module()
    kills, n, outcomes = 0, 1, set()
    while True:
        d = tmp_path / ("k%d" % n)
        shutil.copytree(template.parent, d)
        db = d / "cf.sqlite"
        p = subprocess.run([sys.executable, "-c", KILL_RAISE3, str(db), str(n), writer, repr(T0 + 86400), BULK3],
                           capture_output=True, text=True, env=_child_env(), timeout=120)
        finished = p.returncode == 0 and p.stdout.startswith("finished")
        assert finished or p.returncode == -signal.SIGKILL, p.stderr[-300:]
        if not finished:
            kills += 1
        header = _header_version(db)                       # before any connection of the test's own (a last close checkpoints)
        assert header in (1, 2)
        if header == 1:                                                              # the earlier revision reads it as before
            for at in (T0, T0 + 1, T0 + 2):
                assert ref.as_of(db, "AAA", "profile", at)["body"] == {"a": at - T0, "bulk": BULK3}
        cf.status(db)                                                                # the store opens whole
        ro = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            forms = "".join("P" if t == "text" else "E" for (t,) in ro.execute(
                "SELECT typeof(body) FROM observations WHERE kind='profile' ORDER BY fetched_at, rowid"))
        finally:
            ro.close()
        if header == 1:
            assert set(forms) <= {"P"}, (n, forms)                                   # never an encoded row under a 1
        got = _every_answer(db, [("AAA", "profile")])
        assert {k: w for k, w in got.items() if k[2] < T0 + 3} == known, n
        outcomes.add((header, "E" in forms))
        if writer == "collect":                                                      # a rerun finishes
            r = _collect3(cf, db, json.dumps({"a": 9, "bulk": BULK3}).encode(), 2)
            assert r.stopped is None and _header_version(db) == 2
        else:
            cf.compact(db)
            assert _header_version(db) == 2 and _forms(db, "AAA", "profile") == "FDD"
        assert {k: w for k, w in _every_answer(db, [("AAA", "profile")]).items() if k[2] < T0 + 3} == known
        shutil.rmtree(d)
        if finished:
            break
        n += 1
    assert kills >= 10 and (1, False) in outcomes and any(h == 2 for h, _ in outcomes), (kills, outcomes)
    print("T36a H5 %s: %d kills, one at each statement, outcomes (header, encoded rows) %s" % (writer, kills, sorted(outcomes)),
          file=sys.stderr)
