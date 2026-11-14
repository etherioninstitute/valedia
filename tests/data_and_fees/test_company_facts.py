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
import io
import json
import logging
import random
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path

import pytest

from valedia_trader.data_and_fees import company_facts as cf

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
        self.now, self.sleeps = float(start), []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


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
    stays): bytes, an exception (raised), a callable of the Call, or any other object (sent as JSON)."""
    def __init__(self, clock, script=None):
        self.clock, self.script, self.calls = clock, dict(script or {}), []

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
        return a if isinstance(a, bytes) else json.dumps(a).encode()

    def paths(self, symbol=None):
        return [c.path for c in self.calls if symbol is None or c.symbol == symbol]


def run(tmp_path, symbols, fetch=None, clock=None, name="cf.sqlite", **kw):
    clock = clock or Clock()
    fetch = fetch or Finnhub(clock)
    kw.setdefault("credentials", lambda n: KEY)
    result = cf.collect(symbols, tmp_path / name, fetch=fetch, clock=clock, sleep=clock.sleep, **kw)
    return result, fetch, clock


def rows(tmp_path, sql, args=(), name="cf.sqlite"):
    conn = sqlite3.connect(tmp_path / name)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def http_error(status, retry_after=None):
    return cf.HttpError(status, retry_after)


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
    future = T0 + 10 * 86400                                                     # a bad article date in the future
    fetch = Finnhub(clock, {("/company-news", "AAA"): [[article(1, future)]]})
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    second = [c for c in fetch.calls if c.path == "/company-news"][1]
    assert second.query["from"] <= second.query["to"]


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
    (sha, body, at), = rows(tmp_path, "SELECT body_sha256, body, fetched_at FROM observations WHERE kind='profile'")
    assert body == '{"a":[1,2],"b":2,"c":{"y":2,"z":1}}'
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
                cf.collect(["BBB"], db, fetch=inner, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY)
            seen["text"], seen["inner_calls"] = str(e.value), len(inner.calls)
        return b"[]" if "company-news" in url else b"{}"

    result = cf.collect(["AAA"], db, fetch=fetch, clock=clock, sleep=clock.sleep, credentials=lambda n: KEY)
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
    def names():                                  # SQLite's own -wal / -shm side files may come and go with a reader
        return sorted(p.name for p in tmp_path.iterdir() if not p.name.endswith(("-wal", "-shm")))
    before, size = names(), (tmp_path / "cf.sqlite").stat().st_size
    cf.as_of(tmp_path / "cf.sqlite", "AAA", "profile", T0 + 1)
    cf.news_as_of(tmp_path / "cf.sqlite", "AAA", T0 + 1)
    assert names() == before and (tmp_path / "cf.sqlite").stat().st_size == size
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


def test_news_as_of_needs_both_published_and_fetched_not_after_when(tmp_path):
    conn = sqlite3.connect(":memory:")
    make_store(conn)
    put_news(conn, "AAA", 1, published=10, fetched=20)      # published before, fetched after `when`
    put_news(conn, "AAA", 2, published=30, fetched=15)      # fetched before, "published" after `when` (a bad date)
    put_news(conn, "AAA", 3, published=10, fetched=12)
    put_news(conn, "AAA", 4, published=25, fetched=25)
    put_news(conn, "BBB", 5, published=1, fetched=1)
    got = cf.news_as_of(conn, "AAA", 16)
    assert [a["article_id"] for a in got] == ["3"]
    assert [a["article_id"] for a in cf.news_as_of(conn, "AAA", 25)] == ["3", "1", "4"]        # ordered by published_at
    assert [a["article_id"] for a in cf.news_as_of(conn, "AAA", 25, since=11)] == ["4"]
    assert [a["article_id"] for a in cf.news_as_of(conn, "AAA", 25, since=10)] == ["3", "1", "4"]
    assert cf.news_as_of(conn, "AAA", 25, since=26) == []
    assert set(got[0]) == {"symbol", "article_id", "published_at", "fetched_at", "headline", "source", "url", "summary"}


def test_a_damaged_row_is_refused_not_returned(tmp_path):
    conn = sqlite3.connect(":memory:")
    make_store(conn)
    put_obs(conn, "AAA", "profile", 5, "ok")
    conn.execute("UPDATE observations SET body='{\"tag\":\"changed\"}'")
    with pytest.raises(cf.Refused):
        cf.as_of(conn, "AAA", "profile", 10)


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
            at = float(rnd.randint(0, 40))                                    # many ties on purpose
            put_obs(conn, sym, kind, at, "r%d" % i)
            obs.append((sym, kind, at, i))
        for i in range(rnd.randint(0, 14)):
            sym = rnd.choice([s for s in syms if s != "*"])
            pub, fet = float(rnd.randint(0, 40)), float(rnd.randint(0, 40))
            put_news(conn, sym, i, pub, fet)
            news.append((sym, str(i), pub, fet))
        for _ in range(8):
            when = rnd.choice([float(rnd.randint(-3, 44)), rnd.randint(0, 40) + 0.5, rnd.choice([a[2] for a in obs] or [1.0])])
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
        return b"{}"
    cf.collect(["S%d" % i for i in range(12)], tmp_path / "cf.sqlite", fetch=fetch, clock=clock, sleep=clock.sleep,
               credentials=lambda n: KEY)
    assert state["n"] == 73                       # the run completed
    assert max(clock.sleeps) <= 61                # and no wait of ten minutes to "catch up" with the earlier readings


def test_a_429_waits_the_retry_after_and_tries_again(tmp_path):
    clock = Clock()
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [http_error(429, 7), {"v": 1}]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert clock.sleeps == [7] and result.failed == 0
    first, second = [c for c in fetch.calls if c.path == "/stock/profile2"]
    assert second.t - first.t == 7 and result.calls == 8
    assert rows(tmp_path, "SELECT body FROM observations WHERE kind='profile'") == [('{"v":1}',)]


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
    exact = b'"' + b"x" * (limit - 2) + b'"'
    over = b'"' + b"x" * (limit - 1) + b'"'
    assert len(exact) == limit and len(over) == limit + 1
    fetch = Finnhub(clock, {("/stock/profile2", "AAA"): [over], ("/stock/metric", "AAA"): [exact]})
    result, _, _ = run(tmp_path, ["AAA"], fetch=fetch, clock=clock)
    assert result.failed == 1
    assert fetch.paths("AAA").count("/stock/profile2") == 1                       # not retried
    assert rows(tmp_path, "SELECT count(*) FROM observations WHERE kind='profile'") == [(0,)]
    assert rows(tmp_path, "SELECT length(body) FROM observations WHERE kind='metrics'") == [(limit,)]


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
    assert cf.http_fetch(U, HDR, opener=cf._build_opener(handler)) == b'{"ok":1}'


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
             ("/stock/recommendation", None): [b'{"echo":"' + KEY.encode() + b'"}'],
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
    with pytest.raises(SystemExit) as e:
        cli(tmp_path, "collect", "--universe", "crypto", "--db", str(tmp_path / "cf3.sqlite"))
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli(tmp_path, "collect", "--universe", "stocks", "--symbols", "AAA", "--db", str(tmp_path / "cf3.sqlite"))
    assert e.value.code == 2
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
