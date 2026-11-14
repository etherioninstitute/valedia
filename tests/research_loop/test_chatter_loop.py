"""The chatter collector: r/sportsbook's newest comments and Kalshi's game prices, minute by minute, in a store of
its own. Everything runs on stand-in answers and a clock that moves only when told to; nothing touches the network.

Comment text in these tests is made up and is never printed by the program: the tests also check that.
"""
import base64
import email.utils
import hashlib
import json
import math
import os
import random
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tracemalloc
import urllib.parse
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from valedia_trader.research_loop import chatter_loop as cl
from valedia_trader.venues import kalshi_data as kd

UTC = timezone.utc
T0 = 1794549600            # 2026-11-13 06:00:00 UTC, on the hour (a Friday)
SERIES = ("KXNFLGAME", "KXNBAGAME", "KXNHLGAME", "KXNCAAFGAME", "KXNCAAMBGAME")


def stamp(ts):
    """The store's time text for epoch seconds: UTC, to the nearest millisecond."""
    ms = int(round(ts * 1000))
    return datetime.fromtimestamp(ms // 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ms % 1000)


class Clock:
    """Epoch seconds that move only when told to (sleeping, or a stand-in answer that takes time)."""

    def __init__(self, t=T0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


def entry(cid, body="text", author="/u/a", thread="t3_x", time_="2026-11-13T06:00:00Z", **kw):
    return cl.Entry(comment_id=cid, thread_id=thread, permalink="https://www.reddit.com/r/sportsbook/comments/x/y/%s/" % cid,
                    author=author, body=body, source_time=time_, source_kind=kw.pop("kind", "atom_updated"),
                    source_raw=time_, edited=kw.pop("edited", None))


@pytest.fixture
def store(tmp_path):
    s = cl.Store(tmp_path / "chatter.sqlite", synchronous="OFF")
    yield s
    s.close()


def raw_bodies(store, where="1 = 1"):
    """The raw answers stored (decompressed), in order."""
    return [store.payload_bytes(r["id"]) for r in store.rows("SELECT id FROM payloads WHERE %s ORDER BY id" % where)]


def feed(store, entries, at, slot=None, page=1, backend="public", body=b"<feed/>", bad=0):
    """One comment fetch stored the way the collector stores it; returns the fetch row id."""
    f = cl.Fetch(at=at, slot=int(at) if slot is None else slot, backend=backend, purpose="feed", page=page, status=200,
                 failure=None, body=body, entries=list(entries), bad_entries=bad)
    return store.save_comment_slot([f], [])


def versions(store, cid):
    return [(r["version"], r["author"], r["body"], r["change"]) for r in store.versions(cid)]


# ------------------------------------------------------------------------------------------ the store (6)

def test_store_created_with_own_identity_and_wal(tmp_path):
    p = tmp_path / "c.sqlite"
    s = cl.Store(p)
    s.close()
    h = p.read_bytes()[:100]
    assert h[:16] == b"SQLite format 3\x00"
    assert int.from_bytes(h[68:72], "big") == cl.APP_ID == 0x43484154
    assert int.from_bytes(h[60:64], "big") == cl.LAYOUT
    assert cl.APP_ID != 0x50494B32                                   # not the pick-list store's
    assert h[18] == 2 and h[19] == 2                                 # WAL
    c = sqlite3.connect(str(p))
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    c.close()


def test_store_refuses_a_file_of_another_kind_before_sqlite_opens_it(tmp_path):
    for name, content in (("junk", b"hello" * 100), ("empty-ish", b"SQLite format 3\x00" + b"\x00" * 90)):
        p = tmp_path / name
        p.write_bytes(content)
        before = p.read_bytes()
        with pytest.raises(cl.StoreError):
            cl.Store(p)
        assert p.read_bytes() == before
        assert not Path(str(p) + cl.LOCK_SUFFIX).exists()            # a foreign file gets no lock file either
        assert not Path(str(p) + "-wal").exists() and not Path(str(p) + "-shm").exists()


def test_store_refuses_the_pick_list_store(tmp_path):
    from valedia_trader.data_and_fees import pick_lists as pl
    p = tmp_path / "picks.sqlite"
    pl.Store(p).close()
    before = p.read_bytes()
    with pytest.raises(cl.StoreError):
        cl.Store(p)
    assert p.read_bytes() == before


def test_store_refuses_another_layout_number(tmp_path):
    p = tmp_path / "c.sqlite"
    cl.Store(p).close()
    c = sqlite3.connect(str(p))
    c.execute("PRAGMA user_version = %d" % (cl.LAYOUT + 1))
    c.close()
    with pytest.raises(cl.StoreError):
        cl.Store(p)


def test_store_refuses_a_changed_layout(tmp_path):
    p = tmp_path / "c.sqlite"
    cl.Store(p).close()
    c = sqlite3.connect(str(p))
    c.execute("DROP TRIGGER no_update_comment_versions")
    c.commit()
    c.close()
    with pytest.raises(cl.StoreError):
        cl.Store(p)


@pytest.mark.parametrize("table", cl.TABLES)
def test_every_table_refuses_update_delete_and_replace(tmp_path, table):
    """Rows of every table, made by a short real run; then every statement that could rewrite one is refused."""
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    s.save_comment_slot([cl.Fetch(at=T0, slot=T0, backend="public", purpose="feed", page=1, status=200, failure=None,
                                  body=b"<feed/>", entries=[entry("c1"), entry("c2")])],
                        [cl.Event(at=T0, kind="silence", slot=T0, until=T0 + 60, count=1, detail="hourly")])
    s.save_price_fetch(series="KXNBAGAME", slot=T0, at=T0, failure=None, markets=[
        cl.Quote(ticker="A-1", event_ticker="A", bid=0.4, ask=0.5, last=0.45, volume=1.0, open_interest=2.0, bid_size=1.0,
                 ask_size=1.0, status="active", note=None)], seen=1, bad_rows=0, body=None)
    s.save_price_event(cl.Event(at=T0, kind="skipped", slot=T0, until=None, count=1, detail="x"), series="KXNBAGAME")
    s.save_request(at=T0, service="reddit", backend="public", purpose="feed", slot=T0, page=1)     # (rev 2: the new table)
    s.save_run(started=T0, backend="public", comment_interval=120, price_interval=60)
    c = s.conn
    n = c.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    assert n >= 1, table
    cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % table)]
    for sql in ("UPDATE %s SET %s = %s" % (table, cols[1], cols[1]), "DELETE FROM %s" % table):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(sql)
    row = c.execute("SELECT * FROM %s LIMIT 1" % table).fetchone()
    ph = ",".join("?" * len(cols))
    for verb in ("INSERT OR REPLACE INTO", "REPLACE INTO"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute("%s %s (%s) VALUES (%s)" % (verb, table, ",".join(cols), ph), row)
    c.rollback()
    assert c.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0] == n
    s.close()


def test_a_time_earlier_than_the_last_row_is_refused_by_the_table(tmp_path):
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    s.save_comment_slot([cl.Fetch(at=T0 + 100, slot=T0 + 100, backend="public", purpose="feed", page=1, status=200,
                                  failure=None, body=b"x", entries=[entry("c1")])], [])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.conn.execute("INSERT INTO comment_fetches (fetched_at, slot_ts, backend, purpose, page, ok) VALUES (?,?,?,?,?,?)",
                       (stamp(T0), T0, "public", "feed", 1, 1))
    s.conn.rollback()
    s.close()


def test_a_clock_behind_the_last_row_stops_the_write_with_a_plain_message(tmp_path):
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    feed(s, [entry("c1")], T0 + 100)
    with pytest.raises(cl.ClockBehind) as e:
        feed(s, [entry("c2")], T0 + 50)
    assert "earlier" in str(e.value) and "c2" not in str(e.value)
    assert [r["comment_id"] for r in s.rows("SELECT comment_id FROM comments")] == ["c1"]
    s.close()


def test_the_clock_check_covers_the_price_side_too(tmp_path):
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    s.save_price_fetch(series="KXNBAGAME", slot=T0, at=T0 + 100, failure=None, markets=[], seen=0, bad_rows=0, body=None)
    with pytest.raises(cl.ClockBehind):
        feed(s, [entry("c1")], T0 + 10)
    s.close()


def test_one_writer_at_a_time(tmp_path):
    p = tmp_path / "c.sqlite"
    a = cl.Store(p, lock=True)
    with pytest.raises(cl.StoreError, match="another"):
        cl.Store(p, lock=True)
    b = cl.Store(p, readonly=True)                                   # a reader is never kept out
    b.close()
    a.close()
    cl.Store(p, lock=True).close()                                   # released when the first closes


def test_a_reader_is_not_kept_out_by_the_writer_and_sees_only_whole_slots(tmp_path):
    p = tmp_path / "c.sqlite"
    w = cl.Store(p, lock=True, synchronous="OFF")
    feed(w, [entry("c1"), entry("c2")], T0)
    r = cl.Store(p, readonly=True)
    assert r.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 2
    with pytest.raises(sqlite3.DatabaseError):
        r.conn.execute("DELETE FROM comments")
    feed(w, [entry("c3")], T0 + 60)
    assert r.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 3
    r.close()
    w.close()


def test_a_missing_store_is_not_made_by_a_reader(tmp_path):
    with pytest.raises(cl.StoreError):
        cl.Store(tmp_path / "nope.sqlite", readonly=True)
    assert not (tmp_path / "nope.sqlite").exists()


def test_creation_killed_midway_leaves_no_store_and_the_next_creation_works(tmp_path, monkeypatch):
    p = tmp_path / "c.sqlite"
    real = os.link

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cl.os, "link", boom)
    with pytest.raises(KeyboardInterrupt):
        cl.Store(p)
    assert not p.exists()
    monkeypatch.setattr(cl.os, "link", real)
    cl.Store(p).close()
    assert p.exists()


# ------------------------------------------------------------------------------- versions, kept exactly (5, 4)

def test_first_sight_makes_a_comment_and_version_one(store):
    feed(store, [entry("c1", "hello", "/u/a")], T0)
    assert versions(store, "c1") == [(1, "/u/a", "hello", "first")]
    r = store.rows("SELECT * FROM comment_versions")[0]
    assert r["seen_at"] == stamp(T0)
    assert r["source_time"] == "2026-11-13T06:00:00Z" and r["source_kind"] == "atom_updated"


def test_unchanged_comment_adds_no_version_and_a_changed_text_or_author_does(store):
    feed(store, [entry("c1", "hello", "/u/a")], T0)
    feed(store, [entry("c1", "hello", "/u/a")], T0 + 120)
    assert len(versions(store, "c1")) == 1
    feed(store, [entry("c1", "hello!", "/u/a")], T0 + 240)
    feed(store, [entry("c1", "hello!", "/u/b")], T0 + 360)
    assert versions(store, "c1") == [(1, "/u/a", "hello", "first"), (2, "/u/a", "hello!", "edit"), (3, "/u/b", "hello!", "edit")]


def test_return_to_an_earlier_text_is_a_new_version(store):
    for i, text in enumerate(["a", "b", "a", "a", "b"]):
        feed(store, [entry("c1", text)], T0 + 120 * i)
    assert [v[2] for v in versions(store, "c1")] == ["a", "b", "a", "b"]


def test_every_version_carries_the_time_of_its_own_fetch_once_per_fetch(store):
    feed(store, [entry("c1", "a"), entry("c2", "a")], T0 + 3.25)
    feed(store, [entry("c1", "b"), entry("c2", "a")], T0 + 125.5)
    rows = store.rows("SELECT comment_id, version, seen_at FROM comment_versions ORDER BY id")
    assert {(r["comment_id"], r["version"]): r["seen_at"] for r in rows} == {
        ("c1", 1): stamp(T0 + 3.25), ("c2", 1): stamp(T0 + 3.25), ("c1", 2): stamp(T0 + 125.5)}


def test_text_is_kept_exactly_including_odd_characters_and_lone_surrogates(store):
    odd = ["  padded \n\t", "<script>alert(1)</script>", "'; DROP TABLE comments; --", "{0} %s {x}", "‮ rtl \u0000 nul",
           "emoji \U0001F600 ok", "lone \ud800 surrogate", "", "x" * 100000]
    for i, t in enumerate(odd):
        feed(store, [entry("c%d" % i, t, "/u/\ud800odd" if i == 6 else "/u/a")], T0 + i)
    for i, t in enumerate(odd):
        assert versions(store, "c%d" % i)[0][2] == t
    assert store.versions("c6")[0]["author"] == "/u/\ud800odd"
    assert store.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == len(odd)


def test_the_same_comment_twice_in_one_fetch_makes_one_version_and_is_counted(store):
    feed(store, [entry("c1", "a"), entry("c1", "a"), entry("c2", "z")], T0)
    assert len(versions(store, "c1")) == 1
    assert store.rows("SELECT duplicates FROM comment_fetches")[0]["duplicates"] == 1


def test_a_fetch_row_counts_what_it_found_and_added(store):
    feed(store, [entry("c1", "a"), entry("c2", "b")], T0)
    feed(store, [entry("c1", "a"), entry("c2", "b2"), entry("c3", "c")], T0 + 120)
    r = store.rows("SELECT comments_in_feed, new_comments, new_versions, ok, failure FROM comment_fetches ORDER BY id")
    assert r == [{"comments_in_feed": 2, "new_comments": 2, "new_versions": 2, "ok": 1, "failure": None},
                 {"comments_in_feed": 3, "new_comments": 1, "new_versions": 2, "ok": 1, "failure": None}]


def test_raw_answer_stored_once_by_hash(store):
    body = b"<feed>same</feed>"
    feed(store, [entry("c1")], T0, body=body)
    feed(store, [entry("c1")], T0 + 120, body=body)
    feed(store, [entry("c1")], T0 + 240, body=b"<feed>other</feed>")
    rows = store.rows("SELECT id, sha256, size FROM payloads ORDER BY id")
    assert [r["sha256"] for r in rows] == [hashlib.sha256(body).hexdigest(), hashlib.sha256(b"<feed>other</feed>").hexdigest()]
    assert store.payload_bytes(rows[0]["id"]) == body and rows[0]["size"] == len(body)
    fetch_payloads = [r["payload_id"] for r in store.rows("SELECT payload_id FROM comment_fetches ORDER BY id")]
    assert fetch_payloads[0] == fetch_payloads[1] != fetch_payloads[2]


def test_a_fetch_that_could_not_be_parsed_is_stored_raw_and_counted(store):
    f = cl.Fetch(at=T0, slot=T0, backend="public", purpose="feed", page=1, status=200, failure="unparseable",
                 body=b"\xff\xfe not xml at all", entries=None)
    store.save_comment_slot([f], [])
    r = store.rows("SELECT ok, failure, comments_in_feed, payload_id FROM comment_fetches")[0]
    assert r["ok"] == 0 and r["failure"] == "unparseable" and r["payload_id"] is not None
    assert raw_bodies(store) == [b"\xff\xfe not xml at all"]
    assert store.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def test_a_slot_is_one_transaction(store, monkeypatch):
    """If anything inside a slot fails, none of it is stored: no fetch row without its versions, none the other way."""
    bad = entry("c2", "b")
    bad.body = None                                                  # a body that cannot be stored
    with pytest.raises(Exception):
        feed(store, [entry("c1", "a"), bad], T0)
    for t in ("comments", "comment_versions", "comment_fetches", "payloads"):
        assert store.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] == 0, t


# ---------------------------------------------------------------------------- possible misses and gaps (5)

def misses(store):
    return store.rows("SELECT at, slot_ts, detail FROM comment_events WHERE kind = 'possible_miss'")


def test_possible_miss_when_the_oldest_comment_is_newer_than_the_newest_stored(store):
    feed(store, [entry("c1", time_="2026-11-13T06:00:10Z"), entry("c2", time_="2026-11-13T06:00:20Z")], T0)
    assert misses(store) == []                                       # nothing stored before the first fetch: no mark
    feed(store, [entry("c2", time_="2026-11-13T06:00:20Z"), entry("c3", time_="2026-11-13T06:01:00Z")], T0 + 120)
    assert misses(store) == []                                       # overlaps c2
    feed(store, [entry("c4", time_="2026-11-13T06:05:00Z"), entry("c5", time_="2026-11-13T06:06:00Z")], T0 + 240)
    assert len(misses(store)) == 1                                   # oldest (06:05) is newer than the newest stored (06:01)


def test_no_possible_miss_when_the_feed_reaches_back_to_something_stored(store):
    feed(store, [entry("c1", time_="2026-11-13T06:00:10Z")], T0)
    feed(store, [entry("c0", time_="2026-11-13T06:00:00Z"), entry("c2", time_="2026-11-13T06:00:30Z")], T0 + 120)
    assert misses(store) == []


def test_an_empty_feed_is_no_miss_and_no_row_of_comments(store):
    feed(store, [entry("c1")], T0)
    feed(store, [], T0 + 120)
    assert misses(store) == []
    assert store.rows("SELECT ok, comments_in_feed FROM comment_fetches ORDER BY id")[1] == {"ok": 1, "comments_in_feed": 0}


def test_when_the_feeds_times_cannot_be_read_a_feed_with_nothing_in_common_is_a_miss(store):
    feed(store, [entry("c1", time_=None), entry("c2", time_=None)], T0)
    feed(store, [entry("c2", time_=None), entry("c3", time_=None)], T0 + 120)
    assert misses(store) == []
    feed(store, [entry("c8", time_=None), entry("c9", time_=None)], T0 + 240)
    assert len(misses(store)) == 1


def test_events_are_rows_of_their_own(store):
    store.save_comment_slot([], [cl.Event(at=T0, kind="backoff", slot=T0, until=T0 + 300, count=None, detail="http_429 default"),
                                 cl.Event(at=T0 + 1, kind="silence", slot=T0, until=T0 + 480, count=4, detail="hourly")])
    rows = store.rows("SELECT kind, until, count, detail FROM comment_events ORDER BY id")
    assert rows[0] == {"kind": "backoff", "until": stamp(T0 + 300), "count": None, "detail": "http_429 default"}
    assert rows[1]["kind"] == "silence" and rows[1]["count"] == 4


# --------------------------------------------------------------------- the two real layouts (made-up text)
# A trimmed real answer of Reddit's public feed (2026-11-14): three of its hundred entries, every author, title, link
# slug and comment text replaced by letters of the same length. Atom, newest first, no `published`, times with +00:00.
ATOM_FIXTURE = (
    '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><category term="sportsbook" label="r/sportsbook" /><updated>2026-11-14T02:14:04+00:00</updated><icon>https://www.redditstatic.com/icon.png/</icon><id>/r/sportsbook/comments/.rss?limit=100</id><link rel="self" href="https://www.reddit.com/r/sportsbook/comments/.rss?limit=100" type="application/atom+xml" /><link rel="alternate" href="https://www.reddit.com/r/sportsbook/comments/?limit=100" type="text/html" /><subtitle>xxxxxx</subtitle><title>xxxxxxxx : xxxxxxxxxx</title>'
    '<entry><author><name>/u/xxxxxxxxxxxxx</name><uri>https://www.reddit.com/user/xxxxxxxxxxxxx</uri></author><category term="sportsbook" label="r/sportsbook" /><content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;xx-x xxx xxx xxx&lt;/p&gt; &lt;/div&gt;&lt;!-- SC_ON --&gt;</content><id>t1_19sloxbkbhfyd</id><link href="https://www.reddit.com/r/sportsbook/comments/1xuv17w/xxxxxx_xxxxxxx_xxxxxxxxxx_xxxxxxx_xxx_xxxxx/19sloxbkbhfyd/" /><updated>2026-11-14T02:13:19+00:00</updated><title>/x/xxxxxxxxxxxxx xx xxxxxx xxxxxxx xxxxxxxxxx xxxxxxx xxx xxxxx - xx/xx/xx (xxxxxx)</title></entry>'
    '<entry><author><name>/u/xxxxxxxxxx</name><uri>https://www.reddit.com/user/xxxxxxxxxx</uri></author><category term="sportsbook" label="r/sportsbook" /><content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;xxxxxxxx xx xxxx xxxxxxxx xxxx xx xxxxx xxxxx xx xxx xxxx.&lt;/p&gt; &lt;/div&gt;&lt;!-- SC_ON --&gt;</content><id>t1_19slox3lp4ry9</id><link href="https://www.reddit.com/r/sportsbook/comments/1xus5vo/xxxx_xx_xxx_xxx_xxxxxx_xxxxxx/19slox3lp4ry9/" /><updated>2026-11-14T02:13:11+00:00</updated><title>/x/xxxxxxxxxx xx xxxx xx xxx xxx - xx/xx/xx (xxxxxx)</title></entry>'
    '<entry><author><name>/u/xxxxxxxxxxx-xxxx-xxx</name><uri>https://www.reddit.com/user/xxxxxxxxxxx-xxxx-xxx</uri></author><category term="sportsbook" label="r/sportsbook" /><content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;xx&amp;#xx;x xxx xxxxx xxxxxx xxx xxx xxxxx. xx&amp;#xx;x xxx xxxx xxx xxxx xxx xxx xxxxx&lt;/p&gt; &lt;/div&gt;&lt;!-- SC_ON --&gt;</content><id>t1_19slowxw78hvo</id><link href="https://www.reddit.com/r/sportsbook/comments/1xus5vo/xxxx_xx_xxx_xxx_xxxxxx_xxxxxx/19slowxw78hvo/" /><updated>2026-11-14T02:13:05+00:00</updated><title>/x/xxxxxxxxxxx-xxxx-xxx xx xxxx xx xxx xxx - xx/xx/xx (xxxxxx)</title></entry>'
    '</feed>'
)


# A trimmed real answer of Kalshi's public GET /markets?series_ticker=..&status=open (2026-11-14): three of its markets,
# the rules text removed. A missing bid is "0.0000" with size "0.00".
KALSHI_FIXTURE = json.loads('{"cursor": "", "markets": [{"can_close_early": true, "close_time": "2026-11-26T01:15:00Z", "created_time": "2026-11-10T05:04:39.642978Z", "event_ticker": "KXNFLGAME-26NOV23CINWAS", "exchange_index": 0, "expected_expiration_time": "2026-11-24T04:15:00Z", "expiration_time": "2026-11-26T01:15:00Z", "expiration_value": "", "last_price_dollars": "0.3600", "latest_expiration_time": "2026-11-26T01:15:00Z", "market_type": "binary", "no_ask_dollars": "0.6500", "no_bid_dollars": "0.6300", "no_sub_title": "Washington", "notional_value_dollars": "1.0000", "occurrence_datetime": "2026-11-24T04:15:00Z", "open_interest_fp": "1833.75", "open_time": "2026-11-10T05:06:00Z", "previous_price_dollars": "0.3800", "previous_yes_ask_dollars": "0.3800", "previous_yes_bid_dollars": "0.3300", "price_level_structure": "linear_cent", "result": "", "settlement_bounds_type": "default", "settlement_timer_seconds": 60, "status": "active", "strike_type": "structured", "ticker": "KXNFLGAME-26NOV23CINWAS-WAS", "title": "Washington wins", "updated_time": "2026-11-10T05:06:00.243247Z", "volume_24h_fp": "1594.80", "volume_fp": "1947.57", "yes_ask_dollars": "0.3700", "yes_ask_size_fp": "500.00", "yes_bid_dollars": "0.3500", "yes_bid_size_fp": "13.00", "yes_sub_title": "Washington"}, {"can_close_early": true, "close_time": "2026-11-26T01:15:00Z", "created_time": "2026-11-10T05:04:39.642978Z", "event_ticker": "KXNFLGAME-26NOV23CINWAS", "exchange_index": 0, "expected_expiration_time": "2026-11-24T04:15:00Z", "expiration_time": "2026-11-26T01:15:00Z", "expiration_value": "", "last_price_dollars": "0.6600", "latest_expiration_time": "2026-11-26T01:15:00Z", "market_type": "binary", "no_ask_dollars": "0.3700", "no_bid_dollars": "0.3400", "no_sub_title": "Cincinnati", "notional_value_dollars": "1.0000", "occurrence_datetime": "2026-11-24T04:15:00Z", "open_interest_fp": "521.01", "open_time": "2026-11-10T05:06:00Z", "previous_price_dollars": "0.6700", "previous_yes_ask_dollars": "0.6700", "previous_yes_bid_dollars": "0.6200", "price_level_structure": "linear_cent", "result": "", "settlement_bounds_type": "default", "settlement_timer_seconds": 60, "status": "active", "strike_type": "structured", "ticker": "KXNFLGAME-26NOV23CINWAS-CIN", "title": "Cincinnati wins", "updated_time": "2026-11-10T05:06:00.243247Z", "volume_24h_fp": "231.46", "volume_fp": "586.45", "yes_ask_dollars": "0.6600", "yes_ask_size_fp": "95.21", "yes_bid_dollars": "0.6300", "yes_bid_size_fp": "345.00", "yes_sub_title": "Cincinnati"}, {"can_close_early": true, "close_time": "2026-11-19T01:00:00Z", "created_time": "2026-11-13T02:22:26.700744Z", "event_ticker": "KXNCAAMBGAME-26NOV16WIUMINN", "exchange_index": 3, "expected_expiration_time": "2026-11-17T04:00:00Z", "expiration_time": "2026-11-19T01:00:00Z", "expiration_value": "", "last_price_dollars": "0.0100", "latest_expiration_time": "2026-11-19T01:00:00Z", "market_type": "binary", "no_ask_dollars": "1.0000", "no_bid_dollars": "0.9900", "no_sub_title": "Western Illinois", "notional_value_dollars": "1.0000", "occurrence_datetime": "2026-11-17T04:00:00Z", "open_interest_fp": "1344.00", "open_time": "2026-11-13T02:23:00Z", "previous_price_dollars": "0.0000", "previous_yes_ask_dollars": "0.0000", "previous_yes_bid_dollars": "0.0000", "price_level_structure": "linear_cent", "result": "", "settlement_bounds_type": "default", "settlement_timer_seconds": 45, "status": "active", "strike_type": "structured", "ticker": "KXNCAAMBGAME-26NOV16WIUMINN-WIU", "title": "Western Illinois wins", "updated_time": "2026-11-13T02:23:00.814323Z", "volume_24h_fp": "1344.00", "volume_fp": "1344.00", "yes_ask_dollars": "0.0100", "yes_ask_size_fp": "46072.00", "yes_bid_dollars": "0.0000", "yes_bid_size_fp": "0.00", "yes_sub_title": "Western Illinois"}]}')


# Reddit's official interface (a Listing of t1 things, newest first). NOT a real answer: no application is registered
# yet, so this follows the documented shape.
LISTING_FIXTURE = json.dumps({"kind": "Listing", "data": {"after": "t1_ccc", "before": None, "dist": 3, "children": [
    {"kind": "t1", "data": {"id": "aaa", "name": "t1_aaa", "author": "zed", "body": "one &amp; <two>", "created_utc": 1794549660.0,
                            "edited": False, "link_id": "t3_q1", "parent_id": "t3_q1", "permalink": "/r/sportsbook/comments/q1/s/aaa/"}},
    {"kind": "t1", "data": {"id": "bbb", "name": "t1_bbb", "author": "[deleted]", "body": "[deleted]", "created_utc": 1794549600,
                            "edited": 1794549650.0, "link_id": "t3_q1", "parent_id": "t1_aaa", "permalink": "/r/sportsbook/comments/q1/s/bbb/"}},
    {"kind": "t1", "data": {"id": "ccc", "name": "t1_ccc", "author": "yan", "body": "", "created_utc": 1794549500.0, "edited": False,
                            "link_id": "t3_q2", "parent_id": "t3_q2", "permalink": "/r/sportsbook/comments/q2/s/ccc/"}}]}}).encode()


def atom_with(*entries, head=""):
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">%s%s</feed>' % (head, "".join(entries))).encode()


def atom_entry(cid="t1_aaa", author="/u/a", body="hi", updated="2026-11-14T02:13:19+00:00", thread="1abc"):
    return ('<entry><author><name>%s</name></author><content type="html">%s</content><id>%s</id>'
            '<link href="https://www.reddit.com/r/sportsbook/comments/%s/slug/%s/"/><updated>%s</updated></entry>'
            % (author, body, cid, thread, cid[3:], updated))


# ------------------------------------------------------------------------------------------------- parsing (4, 5)

def test_the_real_public_feed_layout_parses():
    entries, bad = cl.parse_atom(ATOM_FIXTURE.encode())
    assert bad == 0 and [e.comment_id for e in entries] == ["t1_19sloxbkbhfyd", "t1_19slox3lp4ry9", "t1_19slowxw78hvo"]
    e = entries[0]
    assert e.author == "/u/xxxxxxxxxxxxx"
    assert e.body == '<!-- SC_OFF --><div class="md"><p>xx-x xxx xxx xxx</p> </div><!-- SC_ON -->'     # exactly as sent, unescaped once
    assert e.source_time == "2026-11-14T02:13:19Z" and e.source_raw == "2026-11-14T02:13:19+00:00" and e.source_kind == "atom_updated"
    assert e.thread_id == "t3_1xuv17w" and e.permalink.endswith("/19sloxbkbhfyd/")
    assert entries[2].body.count("&#") == 2                                                          # an escaped entity stays as sent


def test_the_listing_layout_parses_and_gives_the_cursor():
    entries, bad, after = cl.parse_listing(LISTING_FIXTURE)
    assert bad == 0 and after == "t1_ccc"
    assert [e.comment_id for e in entries] == ["t1_aaa", "t1_bbb", "t1_ccc"]
    a, b, c = entries
    assert (a.author, a.body, a.thread_id) == ("zed", "one &amp; <two>", "t3_q1")           # raw_json=1: no escaping to undo
    assert a.source_time == "2026-11-13T06:01:00Z" and a.source_kind == "created_utc" and a.edited == "false"
    assert b.edited == "1794549650.0" and b.body == "[deleted]" and c.body == ""
    assert a.permalink == "https://www.reddit.com/r/sportsbook/comments/q1/s/aaa/"


@pytest.mark.parametrize("junk", [b"", b"not xml", b"\xff\xfe\x00", b"<feed/>", b"<html></html>",
                                  b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>', b"[]", b"{}"])
def test_an_answer_that_is_not_a_feed_is_unparseable(junk):
    with pytest.raises(cl.Unparseable):
        cl.parse_atom(junk)


@pytest.mark.parametrize("junk", [b"", b"<feed/>", b"[]", b'{"kind":"t3"}', b'{"data":{"children":3}}', b"\xff", b'{"data":{}}'])
def test_a_listing_that_is_not_a_listing_is_unparseable(junk):
    with pytest.raises(cl.Unparseable):
        cl.parse_listing(junk)


def test_entries_without_an_id_or_text_are_counted_as_bad_not_dropped_quietly():
    data = atom_with(atom_entry("t1_ok1"), "<entry><content>x</content></entry>", "<entry><id>t1_nobody</id></entry>",
                     atom_entry("t1_ok2"))
    entries, bad = cl.parse_atom(data)
    assert [e.comment_id for e in entries] == ["t1_ok1", "t1_ok2"] and bad == 2


def test_an_entity_bomb_and_a_doctype_are_refused_without_expanding():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><feed xmlns="http://www.w3.org/2005/Atom"><entry>&c;</entry></feed>')
    t = time.time()
    with pytest.raises(cl.Unparseable):
        cl.parse_atom(bomb)
    assert time.time() - t < 1
    with pytest.raises(cl.Unparseable):
        cl.parse_atom(b'<!DOCTYPE feed SYSTEM "file:///etc/passwd"><feed xmlns="http://www.w3.org/2005/Atom"/>')
    with pytest.raises(cl.Unparseable):
        cl.parse_atom(b'<!doctype feed><feed xmlns="http://www.w3.org/2005/Atom"/>')


def test_times_are_normalised_and_unreadable_ones_are_kept_as_text_only():
    assert cl.normalize_time("2026-11-14T02:13:19+00:00") == "2026-11-14T02:13:19Z"
    assert cl.normalize_time("2026-11-14T03:13:19+01:00") == "2026-11-14T02:13:19Z"
    assert cl.normalize_time("2026-11-14T02:13:19Z") == "2026-11-14T02:13:19Z"
    assert cl.normalize_time(1794549600.9) == "2026-11-13T06:00:00Z"
    for bad in ("", "yesterday", None, True, float("nan"), 1e30, "2026-11-14T02:13:19"):    # no zone: not a time
        assert cl.normalize_time(bad) is None
    entries, bad = cl.parse_atom(atom_with(atom_entry(updated="soon")))
    assert entries[0].source_time is None and entries[0].source_raw == "soon"


# ---------------------------------------------------------------------------------- stand-in Reddit and Kalshi
def resp(status=200, body=b"", headers=None, took=0.2, truncated=False):
    r = cl.Response(status, {k.lower(): v for k, v in (headers or {}).items()}, body, truncated)
    r.took = took
    return r


class FakeReddit:
    """The transport: records every request with the clock's reading, takes the time the answer says it takes, and
    gives up (TimeoutError) when that is more than the deadline it was handed, as the real one does."""

    def __init__(self, clock, answer):
        self.clock, self.answer, self.log = clock, answer, []

    def __call__(self, method, url, headers, data, timeout):
        t = self.clock()
        self.log.append({"t": t, "method": method, "url": url, "headers": dict(headers), "data": data, "timeout": timeout})
        a = self.answer(len(self.log), t, method, url)
        if isinstance(a, Exception):
            self.clock.sleep(min(0.1, timeout))
            raise a
        if a.took > timeout:
            self.clock.sleep(timeout)
            raise TimeoutError
        self.clock.sleep(a.took)
        return a


def feed_answer(n_new=2, depth=30):
    """An answer function: a public feed that rolls like the real one: the newest `depth` comments, so that each
    answer overlaps the one before it."""
    state = {"i": 0, "window": []}

    def answer(n, t, method, url):
        for k in range(n_new):
            state["i"] += 1
            when = datetime.fromtimestamp(t - 5 - k, UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            state["window"].insert(0, atom_entry("t1_c%06d" % state["i"], body="text %d" % state["i"], updated=when))
        del state["window"][depth:]
        return resp(200, atom_with(*state["window"]), {"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": "56", "x-ratelimit-used": "1"})
    return answer


def market(ticker, bid="0.4000", ask="0.4500", last="0.4200", **kw):
    m = {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "yes_bid_dollars": bid, "yes_ask_dollars": ask,
         "last_price_dollars": last, "volume_fp": "10.00", "open_interest_fp": "5.00", "yes_bid_size_fp": "3.00",
         "yes_ask_size_fp": "4.00", "status": "active"}
    m.update(kw)
    return m


class FakeKalshi:
    def __init__(self, clock, answer=None):
        self.clock, self.log = clock, []
        self.answer = answer or (lambda n, t, series: (200, {"markets": [market(series + "-26NOV20AAABBB-AAA"), market(series + "-26NOV20AAABBB-BBB")], "cursor": ""}, 0.3))

    def __call__(self, url, timeout):
        t = self.clock()
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.log.append({"t": t, "url": url, "series": q.get("series_ticker"), "timeout": timeout})
        status, doc, took = self.answer(len(self.log), t, q.get("series_ticker"))
        if took > timeout:
            self.clock.sleep(timeout)
            raise TimeoutError
        self.clock.sleep(took)
        if status != 200:
            raise kd.HttpError(status, url)
        return doc if isinstance(doc, bytes) else json.dumps(doc).encode()


def make(tmp_path, clock=None, answer=None, kanswer=None, env=None, name="c.sqlite", **kw):
    clock = clock or Clock()
    store = cl.Store(tmp_path / name, lock=False, synchronous="OFF")
    reddit = FakeReddit(clock, answer or feed_answer())
    kalshi = FakeKalshi(clock, kanswer)
    lines = []
    col = cl.Collector(store, clock=clock, sleep=clock.sleep, reddit_fetch=reddit, kalshi_get=kalshi, env=env or {},
                       log=lines.append, **kw)
    return col, store, reddit, kalshi, clock, lines


def run_for(col, clock, seconds):
    col.start()
    end = clock() + seconds
    while clock() < end:
        col.step()
        clock.sleep(min(max(col.next_due() - clock(), 0.0), 1.0))


def minute_of_hour(t):
    return int(t % 3600) // 60


# ------------------------------------------------------------------------------------- Reddit by its front door (1)

def test_public_feed_is_asked_every_120_seconds_from_a_fixed_address_with_a_fixed_plain_agent(tmp_path):
    col, store, reddit, _, clock, _ = make(tmp_path)
    run_for(col, clock, 40 * 60)
    ts = [r["t"] for r in reddit.log]
    assert ts and all(int(t) % 120 == 0 for t in ts)
    assert {r["url"] for r in reddit.log} == {cl.PUBLIC_URL}
    assert cl.PUBLIC_URL == "https://www.reddit.com/r/sportsbook/comments/.rss?limit=100"
    assert {r["method"] for r in reddit.log} == {"GET"}
    for r in reddit.log:
        assert set(k.lower() for k in r["headers"]) <= {"user-agent", "accept"}
        assert r["headers"]["User-Agent"] == cl.USER_AGENT == "valedia-trader-chatter-collector/1.0"
        assert r["data"] is None
    assert not re.search(r"burton|chatelain|@|/u/|valedia\.ai|\d{3}-\d{4}", cl.USER_AGENT, re.I)


def test_no_public_request_from_minute_5_to_minute_12_of_any_hour_and_silence_is_a_row(tmp_path):
    col, store, reddit, _, clock, _ = make(tmp_path)
    run_for(col, clock, 3 * 3600)
    assert all(not (5 <= minute_of_hour(r["t"]) <= 12) for r in reddit.log)
    assert len(reddit.log) == 3 * 26                                   # 30 even minutes an hour less 6, 8, 10 and 12
    sil = store.rows("SELECT at, until, count FROM comment_events WHERE kind = 'silence' ORDER BY id")
    assert len(sil) == 3 and all(r["count"] == 4 for r in sil)         # four slots a window
    assert [minute_of_hour(0) for _ in sil] and sil[0]["until"][11:16] == "06:13"


def test_never_two_public_requests_within_60_seconds_even_after_a_restart(tmp_path):
    col, store, reddit, _, clock, _ = make(tmp_path)
    run_for(col, clock, 600)
    clock.t -= 0                                                       # a second collector starts at once
    col2 = cl.Collector(store, clock=clock, sleep=clock.sleep, reddit_fetch=reddit, kalshi_get=FakeKalshi(clock), env={}, log=lambda s: None)
    end = clock() + 1200
    col2.start()
    while clock() < end:
        col2.step()
        clock.sleep(1)
    ts = [r["t"] for r in reddit.log]
    assert all(b - a >= 60 for a, b in zip(ts, ts[1:]))


def test_the_answers_own_reset_time_holds_the_next_request_back(tmp_path):
    def answer(n, t, m, u):
        return resp(200, atom_with(atom_entry("t1_c%d" % n)), {"x-ratelimit-reset": "400"})
    col, store, reddit, _, clock, _ = make(tmp_path, answer=answer)
    run_for(col, clock, 1800)
    ts = [r["t"] for r in reddit.log]
    assert all(b - a >= 400 for a, b in zip(ts, ts[1:]))


@pytest.mark.parametrize("status", [429, 403])
@pytest.mark.parametrize("headers,expected", [({"Retry-After": "90"}, 90), ({}, 300), ({"Retry-After": "99999"}, 3600),
                                              ({"Retry-After": "soon"}, 300), ({"x-ratelimit-reset": "150"}, 150),
                                              ({"Retry-After": "0"}, 1), ({"Retry-After": "-5"}, 1),
                                              ({"Retry-After": "nan"}, 300)])
def test_a_429_or_403_is_a_back_off_for_the_time_named_else_5_minutes_at_most_10_and_never_retried_inside(tmp_path, status, headers, expected):
    count = {"n": 0}

    def answer(n, t, m, u):
        count["n"] += 1
        return resp(status, b"slow down", headers) if count["n"] == 1 else resp(200, atom_with(atom_entry("t1_ok%d" % n)))
    col, store, reddit, _, clock, _ = make(tmp_path, answer=answer)
    run_for(col, clock, 4000)                                           # rev 2: long enough for a named hour
    first, second = reddit.log[0]["t"], reddit.log[1]["t"]
    g = -(-(first + 0.2 + expected) // 120) * 120                       # the first slot at or after the back-off's end
    #                                                                     (rev 2: it starts when the answer came, 0.2 s in) ...
    while 5 <= minute_of_hour(g) <= 12:                                  # ... that is not in the hourly silence
        g += 120
    assert second == g
    ev = store.rows("SELECT at, until, detail FROM comment_events WHERE kind = 'backoff'")
    assert len(ev) == 1 and ev[0]["detail"].startswith("http_%d" % status)
    f = store.rows("SELECT ok, failure, status FROM comment_fetches ORDER BY id")[0]
    assert (f["ok"], f["failure"], f["status"]) == (0, "http_%d" % status, status)
    assert raw_bodies(store)[0] == b"slow down"                                                    # the raw answer is kept


def test_a_redirect_is_not_followed_and_is_a_failed_fetch(tmp_path):
    def answer(n, t, m, u):
        return resp(302, b"", {"Location": "https://evil.example/x"})
    col, store, reddit, _, clock, _ = make(tmp_path, answer=answer)
    run_for(col, clock, 700)
    assert all(r["url"] == cl.PUBLIC_URL for r in reddit.log)
    assert {r["failure"] for r in store.rows("SELECT failure FROM comment_fetches")} == {"redirect"}


def test_the_real_transport_never_follows_a_redirect_and_refuses_other_hosts():
    import http.server
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/never")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        # the opener itself (the same one reddit_get uses) refuses to follow, and reddit_get refuses any other host
        with pytest.raises(ValueError):
            cl.reddit_get("GET", "http://127.0.0.1:%d/x" % srv.server_port, {}, None, 2)
        with pytest.raises(ValueError):
            cl.reddit_get("GET", "https://evil.example/r/sportsbook/comments/.rss", {}, None, 2)
        with pytest.raises(ValueError):
            cl.reddit_get("GET", "https://www.reddit.com.evil.example/x", {}, None, 2)
        with pytest.raises(ValueError):
            cl.reddit_get("GET", "https://user:pw@www.reddit.com/x", {}, None, 2)
        # the module's own transport (the one reddit_get and kalshi_get use) hands the 302 back and never follows it;
        # it has no cookie jar and no proxy at all (outcome 3 replaced the urllib opener this test used to inspect)
        r = cl._http("GET", "http://127.0.0.1:%d/x" % srv.server_port, {}, None, 2, "reddit")
        assert r.status == 302 and hits == ["/x"]
        assert not hasattr(cl, "_OPENER")
    finally:
        srv.shutdown()


def test_bounded_call_gives_up_on_time_and_refuses_when_too_many_are_stuck():
    t = time.time()
    with pytest.raises(TimeoutError):
        cl.bounded_call(lambda: time.sleep(1.5), 0.2)
    assert time.time() - t < 1.0
    assert cl.bounded_call(lambda: 7, 1) == 7
    with pytest.raises(KeyError):
        cl.bounded_call(lambda: {}["x"], 1)
    stuck = threading.Event()
    for _ in range(cl.MAX_ABANDONED):
        with pytest.raises(TimeoutError):
            cl.bounded_call(lambda: stuck.wait(5), 0.05)
    t = time.time()
    with pytest.raises(TimeoutError):
        cl.bounded_call(lambda: 7, 5)                                   # not even started
    assert time.time() - t < 0.5
    stuck.set()
    time.sleep(0.2)
    assert cl.bounded_call(lambda: 8, 1) == 8


# ------------------------------------------------------------------------- the official interface (1), credentials
CREDS = {"REDDIT_CLIENT_ID": "madeUpClientId123", "REDDIT_CLIENT_SECRET": "madeUpSecretValue/456+xyz"}


def listing(ids_times, after=None, remaining=None, reset=None):
    kids = [{"kind": "t1", "data": {"id": i[3:], "name": i, "author": "u", "body": "b " + i, "created_utc": float(t),
                                    "edited": False, "link_id": "t3_q", "permalink": "/r/sportsbook/comments/q/s/%s/" % i[3:]}}
            for i, t in ids_times]
    h = {}
    if remaining is not None:
        h = {"x-ratelimit-remaining": str(remaining), "x-ratelimit-reset": str(reset if reset is not None else 60)}
    return resp(200, json.dumps({"kind": "Listing", "data": {"after": after, "before": None, "children": kids}}).encode(), h)


def token_answer(ttl=3600, tok="madeUpTokenValue.abc"):
    return resp(200, json.dumps({"access_token": tok, "token_type": "bearer", "expires_in": ttl, "scope": "*"}).encode())


def oauth_answer(pages, token_ttl=3600, log=None):
    """pages: a function (count of listing requests so far, url) -> Response."""
    state = {"n": 0}

    def answer(n, t, method, url):
        if url == cl.TOKEN_URL:
            return token_answer(token_ttl)
        state["n"] += 1
        return pages(state["n"], url)
    return answer


def test_with_both_credentials_the_official_interface_is_used_every_minute_with_an_application_only_token(tmp_path):
    def pages(n, url):
        return listing([("t1_n%03d" % n, T0 + n * 60 - 3)])
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    assert col.backend == "oauth" and col.c_interval == 60
    run_for(col, clock, 20 * 60)
    tok = [r for r in reddit.log if r["url"] == cl.TOKEN_URL]
    feeds = [r for r in reddit.log if r["url"] != cl.TOKEN_URL]
    assert len(tok) == 1                                                 # one token serves the hour
    t = tok[0]
    assert t["method"] == "POST" and t["data"] == b"grant_type=client_credentials"
    assert t["headers"]["Authorization"] == "Basic " + base64.b64encode(b"madeUpClientId123:madeUpSecretValue/456+xyz").decode()
    assert len(feeds) == 20 and all(int(r["t"]) % 60 == 0 for r in feeds)       # every minute, silence or not
    for r in feeds:
        assert r["method"] == "GET" and r["url"].startswith("https://oauth.reddit.com/r/sportsbook/comments?limit=100&raw_json=1")
        assert r["headers"]["Authorization"] == "Bearer madeUpTokenValue.abc"
        assert set(k.lower() for k in r["headers"]) <= {"user-agent", "accept", "authorization"}
    assert any(5 <= minute_of_hour(r["t"]) <= 12 for r in feeds)                 # no hourly silence on the official interface


def test_one_credential_alone_keeps_the_public_feed(tmp_path):
    for env in ({"REDDIT_CLIENT_ID": "a"}, {"REDDIT_CLIENT_SECRET": "b"}, {"REDDIT_CLIENT_ID": "a", "REDDIT_CLIENT_SECRET": ""}, {}):
        col, *_ = make(tmp_path, env=env, name="x%d.sqlite" % len(env))
        assert col.backend == "public"


def test_the_secret_the_token_and_the_headers_are_never_stored_or_logged(tmp_path, capsys):
    def pages(n, url):
        return listing([("t1_n%03d" % n, T0 + n * 60 - 3)])
    col, store, reddit, _, clock, lines = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    run_for(col, clock, 10 * 60)
    store.close()
    blob = (tmp_path / "c.sqlite").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("c.sqlite-*"))
    for secret in (b"madeUpClientId123", b"madeUpSecretValue", b"madeUpTokenValue", base64.b64encode(b"madeUpClientId123")[:12]):
        assert secret not in blob
    out = "\n".join(lines) + capsys.readouterr().out
    assert "madeUp" not in out


def test_a_failed_token_request_is_a_failed_fetch_and_the_feed_is_not_asked(tmp_path):
    def answer(n, t, method, url):
        return resp(500, b"oops") if url == cl.TOKEN_URL else listing([])
    col, store, reddit, _, clock, _ = make(tmp_path, answer=answer, env=CREDS)
    run_for(col, clock, 300)
    assert {r["url"] for r in reddit.log} == {cl.TOKEN_URL}
    rows = store.rows("SELECT purpose, ok, failure FROM comment_fetches")
    assert rows and all(r == {"purpose": "token", "ok": 0, "failure": "http_500"} for r in rows)


def test_a_401_drops_the_token_and_the_next_slot_asks_for_a_new_one(tmp_path):
    state = {"n": 0}

    def pages(n, url):
        state["n"] += 1
        return resp(401, b"") if state["n"] == 1 else listing([("t1_n%03d" % n, T0 + n * 60 - 3)])
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    run_for(col, clock, 5 * 60)
    assert sum(1 for r in reddit.log if r["url"] == cl.TOKEN_URL) == 2


def test_the_token_is_renewed_before_it_expires(tmp_path):
    def pages(n, url):
        return listing([("t1_n%03d" % n, T0 + n * 60 - 3)])
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages, token_ttl=3600), env=CREDS)
    run_for(col, clock, 3 * 3600)
    n = sum(1 for r in reddit.log if r["url"] == cl.TOKEN_URL)
    assert 3 <= n <= 4


def test_paging_back_by_the_cursor_until_the_newest_stored_comment_is_found(tmp_path):
    seen = {"urls": []}

    def pages(n, url):
        seen["urls"].append(url)
        if n == 1:
            return listing([("t1_a%d" % i, T0 + 100 - i) for i in range(3)], after="t1_a2")       # a first page that holds only new comments
        if "after=t1_a2" in url:
            return listing([("t1_b%d" % i, T0 + 90 - i) for i in range(3)], after="t1_b2")
        return listing([("t1_a0", T0 + 100)], after=None)
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    # the store already holds a comment, t1_b1, older than every page-one comment
    feed(store, [entry("t1_b1", time_="%s" % datetime.fromtimestamp(T0 + 89, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))], T0 - 500)
    run_for(col, clock, 70)
    feeds = [r["url"] for r in reddit.log if r["url"] != cl.TOKEN_URL]
    assert feeds[0] == cl.OAUTH_URL and "after=t1_a2" in feeds[1]
    assert len(feeds) >= 2
    got = {r["comment_id"] for r in store.rows("SELECT comment_id FROM comments")}
    assert {"t1_a0", "t1_b0", "t1_b1"} <= got


def test_paging_stops_after_five_pages(tmp_path):
    def pages(n, url):
        return listing([("t1_p%d_%d" % (n, i), T0 + 1000 - 10 * n - i) for i in range(3)], after="t1_p%d_2" % n)
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    feed(store, [entry("t1_stored", time_=datetime.fromtimestamp(T0 - 3000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))], T0 - 500)
    run_for(col, clock, 70)
    first_slot_requests = [r for r in reddit.log if r["url"] != cl.TOKEN_URL and r["t"] < T0 + 60]
    assert len(first_slot_requests) == cl.OAUTH_MAX_PAGES == 5
    assert len(store.rows("SELECT * FROM comment_events WHERE kind = 'possible_miss'")) >= 1       # never found: a possible miss


def test_rate_limit_headers_are_obeyed_and_never_more_than_30_requests_a_minute(tmp_path):
    def pages(n, url):
        return listing([("t1_n%03d" % n, T0 + n * 60 - 3)], remaining=0.0, reset=200) if n == 3 else listing([("t1_n%03d" % n, T0 + n * 60 - 3)], remaining=90, reset=60)
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    run_for(col, clock, 12 * 60)
    feeds = [r["t"] for r in reddit.log if r["url"] != cl.TOKEN_URL]
    third = feeds[2]
    assert feeds[3] - third >= 200                                      # held for the named reset
    assert store.rows("SELECT kind FROM comment_events WHERE kind = 'ratelimit'")
    for t in [r["t"] for r in reddit.log]:
        assert sum(1 for r in reddit.log if t - 60 < r["t"] <= t) <= cl.OAUTH_BURST


def test_oauth_429_backs_off_like_the_public_feed(tmp_path):
    state = {"n": 0}

    def pages(n, url):
        state["n"] += 1
        return resp(429, b"", {"Retry-After": "180"}) if state["n"] == 2 else listing([("t1_n%03d" % n, T0 + n * 60 - 3)])
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    run_for(col, clock, 8 * 60)
    feeds = [r["t"] for r in reddit.log if r["url"] != cl.TOKEN_URL]
    assert feeds[2] - feeds[1] >= 180


# ---------------------------------------------------------------------------------- Kalshi's public interface (2)

def test_the_five_series_are_those_of_sports_lines():
    from valedia_trader.research_loop import sports_lines
    assert set(cl.SERIES) == {v[1] for v in sports_lines.LEAGUES.values()} and len(cl.SERIES) == 5


def test_each_series_is_asked_once_a_minute_by_the_package_reader_with_nothing_but_the_query(tmp_path):
    col, store, _, kalshi, clock, _ = make(tmp_path)
    run_for(col, clock, 10 * 60)
    for s in cl.SERIES:
        ts = [r["t"] for r in kalshi.log if r["series"] == s]
        assert len(ts) == 10 and all(int(t) % 60 in (0, 1, 2) for t in ts) and all(b - a >= 59 for a, b in zip(ts, ts[1:]))
    for r in kalshi.log:
        u = urllib.parse.urlsplit(r["url"])
        assert u.scheme == "https" and u.netloc == "api.elections.kalshi.com" and u.path == "/trade-api/v2/markets"
        assert dict(urllib.parse.parse_qsl(u.query)) == {"series_ticker": r["series"], "status": "open", "limit": "1000"}
    assert len(kalshi.log) == 50


def test_the_real_price_transport_is_this_modules_own_bounded_one(monkeypatch):
    """rev 2 (outcome 3 changes invariant 2): Kalshi is read through this module's own transport, keyless, GET only,
    to Kalshi's public host only; the package reader is no longer used."""
    import inspect
    src = inspect.getsource(cl.kalshi_get)
    assert "urllib_get" not in src and "urlopen" not in src
    assert "KalshiData(" not in inspect.getsource(cl.Collector.__init__)
    seen = []

    def fake_http(method, url, headers, data, timeout, service, **kw):
        seen.append((method, url, dict(headers), data, timeout, service))
        return cl.Response(200, {}, b'{"markets": [], "cursor": ""}')
    monkeypatch.setattr(cl, "_http", fake_http)
    url = "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXNBAGAME&status=open&limit=1000"
    assert cl.kalshi_get(url, 7.5) == b'{"markets": [], "cursor": ""}'
    assert seen == [("GET", url, {"User-Agent": cl.USER_AGENT, "Accept": "application/json"}, None, 7.5, "kalshi")]
    for bad in ("http://api.elections.kalshi.com/trade-api/v2/markets", "https://evil.example/trade-api/v2/markets",
                "https://api.elections.kalshi.com.evil.example/x", "https://u:p@api.elections.kalshi.com/trade-api/v2/markets"):
        with pytest.raises(ValueError):
            cl.kalshi_get(bad, 5)
    assert len(seen) == 1


def test_a_price_row_is_stored_only_when_bid_ask_or_last_changed_and_each_fetch_is_a_row(tmp_path):
    state = {"n": 0}

    def kanswer(n, t, series):
        if series != "KXNBAGAME":
            return 200, {"markets": [], "cursor": ""}, 0.2
        state["n"] += 1
        k = state["n"]
        bid, ask, last = [("0.4000", "0.4500", "0.4200"), ("0.4000", "0.4500", "0.4200"), ("0.4100", "0.4500", "0.4200"),
                          ("0.4100", "0.4500", "0.4200"), ("0.4100", "0.4600", "0.4300")][min(k, 5) - 1]
        return 200, {"markets": [market("KXNBAGAME-X-A", bid, ask, last, volume_fp=str(k)), market("KXNBAGAME-X-B")], "cursor": ""}, 0.2
    col, store, _, kalshi, clock, _ = make(tmp_path, kanswer=kanswer)
    run_for(col, clock, 5 * 60)
    a = store.rows("SELECT yes_bid, yes_ask, last_price FROM prices WHERE ticker = 'KXNBAGAME-X-A' ORDER BY id")
    assert a == [{"yes_bid": 0.4, "yes_ask": 0.45, "last_price": 0.42}, {"yes_bid": 0.41, "yes_ask": 0.45, "last_price": 0.42},
                 {"yes_bid": 0.41, "yes_ask": 0.46, "last_price": 0.43}]
    assert len(store.rows("SELECT id FROM prices WHERE ticker = 'KXNBAGAME-X-B'")) == 1
    pf = store.rows("SELECT ok, markets_seen, rows_stored FROM price_fetches WHERE series = 'KXNBAGAME' ORDER BY id")
    assert [(r["ok"], r["markets_seen"], r["rows_stored"]) for r in pf] == [(1, 2, 2), (1, 2, 0), (1, 2, 1), (1, 2, 0), (1, 2, 1)]
    assert len(store.rows("SELECT id FROM price_fetches")) == 25                # a row per fetch of every series
    row = store.rows("SELECT * FROM prices WHERE ticker = 'KXNBAGAME-X-A' ORDER BY id")[0]
    assert (row["volume"], row["open_interest"], row["yes_bid_size"], row["yes_ask_size"], row["status"]) == (1.0, 5.0, 3.0, 4.0, "active")


def test_a_restart_does_not_repeat_a_price_row(tmp_path):
    col, store, _, kalshi, clock, _ = make(tmp_path)
    run_for(col, clock, 180)
    n = len(store.rows("SELECT id FROM prices"))
    store.close()
    store2 = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    col2 = cl.Collector(store2, clock=clock, sleep=clock.sleep, reddit_fetch=FakeReddit(clock, feed_answer()), kalshi_get=kalshi, env={}, log=lambda s: None)
    clock.sleep(30)
    run_for(col2, clock, 180)
    assert len(store2.rows("SELECT id FROM prices")) == n


def test_the_real_layout_of_a_market_parses_to_a_quote(tmp_path):
    col, *_ = make(tmp_path)
    q = [col._quote(m) for m in KALSHI_FIXTURE["markets"]]
    assert q[0].ticker == "KXNFLGAME-26NOV23CINWAS-WAS" and (q[0].bid, q[0].ask, q[0].last) == (0.35, 0.37, 0.36)
    assert (q[0].volume, q[0].open_interest, q[0].bid_size, q[0].ask_size, q[0].status) == (1947.57, 1833.75, 13.0, 500.0, "active")
    assert q[0].event_ticker == "KXNFLGAME-26NOV23CINWAS" and q[0].note is None
    assert (q[2].bid, q[2].bid_size, q[2].ask) == (0.0, 0.0, 0.01)             # no bid shows as 0.0000 with size 0


def test_a_failed_price_fetch_is_a_row_with_a_constant_reason(tmp_path):
    def kanswer(n, t, series):
        return {"KXNFLGAME": (429, {}, 0.1), "KXNBAGAME": (500, {}, 0.1), "KXNHLGAME": (404, {}, 0.1)}.get(series, (200, {"markets": [], "cursor": ""}, 0.1))
    col, store, _, kalshi, clock, _ = make(tmp_path, kanswer=kanswer)
    run_for(col, clock, 60)
    got = {r["series"]: (r["ok"], r["failure"]) for r in store.rows("SELECT series, ok, failure FROM price_fetches")}
    assert got["KXNFLGAME"] == (0, "http_429") and got["KXNBAGAME"] == (0, "http_500") and got["KXNHLGAME"] == (0, "http_404")
    assert got["KXNCAAFGAME"] == (1, None)
    assert len([r for r in kalshi.log if r["series"] == "KXNFLGAME"]) == 1          # no retry inside the minute


def test_an_answer_that_cannot_be_read_is_stored_raw_and_counted(tmp_path):
    def kanswer(n, t, series):
        return (200, b"<html>not json</html>", 0.1) if series == "KXNBAGAME" else (200, {"nothing": 1}, 0.1) if series == "KXNFLGAME" \
            else (200, {"markets": [], "cursor": ""}, 0.1)
    col, store, _, _, clock, _ = make(tmp_path, kanswer=kanswer)
    run_for(col, clock, 60)
    got = {r["series"]: r["failure"] for r in store.rows("SELECT series, failure FROM price_fetches")}
    assert got["KXNBAGAME"] == "not_json" and got["KXNFLGAME"] == "bad_shape"
    raw = set(raw_bodies(store, "kind = 'kalshi_unread'"))
    assert raw == {b"<html>not json</html>", json.dumps({"nothing": 1}).encode()}


def test_odd_market_rows_are_counted_not_dropped_quietly(tmp_path):
    def kanswer(n, t, series):
        if series != "KXNBAGAME":
            return 200, {"markets": [], "cursor": ""}, 0.1
        return 200, {"markets": [market("A-1"), "junk", {"no_ticker": 1}, market("A-1"), market("A-2", bid="oops"),
                                 market("A-3", bid="nan"), {"ticker": "A-4"}], "cursor": ""}, 0.1
    col, store, *_ = make(tmp_path, kanswer=kanswer)
    clock = col.clock
    run_for(col, clock, 60)
    f = store.rows("SELECT markets_seen, rows_stored, bad_rows FROM price_fetches WHERE series = 'KXNBAGAME'")[0]
    assert f == {"markets_seen": 5, "rows_stored": 4, "bad_rows": 3}                # junk, no ticker and the repeated A-1
    notes = {r["ticker"]: r["note"] for r in store.rows("SELECT ticker, note FROM prices")}
    assert notes["A-2"] == "bad_yes_bid_dollars" and notes["A-3"] == "bad_yes_bid_dollars" and notes["A-4"] == "no_prices"


# ------------------------------------------------------------------------------------------ the clock is honest (7)

def test_a_slow_reddit_answer_is_cut_at_10_seconds_and_costs_no_price_minute(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path, answer=lambda n, t, m, u: resp(200, b"", took=60.0))
    run_for(col, clock, 12 * 60)
    assert all(r["timeout"] <= 10.0 for r in reddit.log)
    assert {r["failure"] for r in store.rows("SELECT failure FROM comment_fetches")} == {"timeout"}
    for s in cl.SERIES:
        slots = {r["slot_ts"] for r in store.rows("SELECT slot_ts FROM price_fetches WHERE series = ? AND ok = 1", (s,))}
        assert len(slots) == 12


def test_a_price_pass_that_cannot_finish_in_10_seconds_fails_the_rest_and_never_holds_reddit_up(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path, kanswer=lambda n, t, s: (200, {"markets": [], "cursor": ""}, 6.0))
    run_for(col, clock, 6 * 60)
    ok = store.rows("SELECT COUNT(*) AS n FROM price_fetches WHERE ok = 1")[0]["n"]
    bad = store.rows("SELECT COUNT(*) AS n FROM price_fetches WHERE ok = 0")[0]["n"]
    assert ok == 6 and bad == 24                                      # one series makes it in 10 s of a 6 s answer, the others time out
    assert {r["failure"] for r in store.rows("SELECT failure FROM price_fetches WHERE ok = 0")} <= {"timeout"}
    assert all(r["timeout"] <= 10.0 for r in kalshi.log)
    late = max(r["t"] - (int(r["t"]) // 120 * 120) for r in reddit.log)
    assert late <= 10.0 + 1e-6


def test_a_slot_more_than_half_over_is_skipped_and_recorded(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path)
    col.start()
    clock.sleep(400)                                                    # the whole process was stalled
    col.step()
    ev = store.rows("SELECT kind, count, detail FROM comment_events WHERE kind = 'skipped'")
    assert ev and sum(r["count"] for r in ev) >= 2
    pe = store.rows("SELECT kind, count FROM price_events WHERE kind = 'skipped'")
    assert pe and sum(r["count"] for r in pe) >= 5


def test_a_slot_just_started_late_but_within_half_is_still_taken(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path)
    col.start()
    clock.sleep(150)                                                    # 06:02:30 is 30 s into the 06:02 slot: taken
    col.step()
    assert [int(r["t"]) for r in reddit.log] == [T0 + 150] or reddit.log[-1]["t"] == T0 + 150
    assert store.rows("SELECT slot_ts FROM comment_fetches ORDER BY id")[-1]["slot_ts"] == T0 + 120


def test_the_grid_is_the_clocks_not_the_work_it_does(tmp_path):
    """Answers that take 3 s each do not make slots drift."""
    col, store, reddit, kalshi, clock, _ = make(tmp_path, answer=lambda n, t, m, u: resp(200, atom_with(atom_entry("t1_c%d" % n)), took=3.0))
    run_for(col, clock, 60 * 60)
    for r in reddit.log:
        assert r["t"] - int(r["t"]) // 120 * 120 < 10.0
    slots = [x["slot_ts"] for x in store.rows("SELECT slot_ts FROM comment_fetches ORDER BY id")]
    assert all(s % 120 == 0 for s in slots)


def test_a_restart_keeps_the_grid_and_the_gap_is_a_row(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path)
    run_for(col, clock, 600)
    store.close()
    clock.sleep(1000)
    store2 = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    col2 = cl.Collector(store2, clock=clock, sleep=clock.sleep, reddit_fetch=reddit, kalshi_get=kalshi, env={}, log=lambda s: None)
    run_for(col2, clock, 300)
    assert all(r["slot_ts"] % 120 == 0 for r in store2.rows("SELECT slot_ts FROM comment_fetches"))
    assert all(r["slot_ts"] % 60 == 0 for r in store2.rows("SELECT slot_ts FROM price_fetches"))
    down = store2.rows("SELECT detail FROM comment_events WHERE kind = 'down'")
    assert len(down) == 1 and down[0]["detail"].startswith("from 2026-")
    assert len(store2.rows("SELECT id FROM price_events WHERE kind = 'down'")) == 1
    assert len(store2.rows("SELECT id FROM runs")) == 2


def test_a_clock_that_runs_behind_the_store_stops_the_run_with_a_plain_message(tmp_path, capsys):
    col, store, reddit, kalshi, clock, _ = make(tmp_path)
    run_for(col, clock, 300)
    store.close()
    clock.t -= 3600
    store2 = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    col2 = cl.Collector(store2, clock=clock, sleep=clock.sleep, reddit_fetch=reddit, kalshi_get=kalshi, env={}, log=lambda s: None)
    with pytest.raises(cl.ClockBehind) as e:
        col2.start()
    assert "earlier than the latest time" in str(e.value)
    # and when it goes back in the middle of a run
    col3, store3, r3, k3, clock3, _ = make(tmp_path, name="d.sqlite")
    col3.start()
    clock3.sleep(200)
    col3.step()
    clock3.t -= 500
    with pytest.raises(cl.ClockBehind):
        for _ in range(1000):
            clock3.sleep(1)
            col3.step()


# ------------------------------------------------------ 48 hours on a fake clock with slow, failing and limited answers (1, 7)

def stormy_reddit(seed, oauth=False):
    rng = random.Random(seed)
    state = {"i": 0, "window": []}

    def answer(n, t, method, url):
        if url == cl.TOKEN_URL:
            return token_answer(rng.choice([600, 3600])) if rng.random() < 0.9 else resp(rng.choice([429, 500]), b"", {})
        r = rng.random()
        if r < 0.03:
            return resp(429, b"limited", {"Retry-After": str(rng.choice([5, 30, 90, 400, 5000]))} if rng.random() < 0.7 else {}, took=rng.uniform(0.1, 3))
        if r < 0.04:
            return resp(403, b"no", {}, took=0.2)
        if r < 0.05:
            return resp(500, b"err", {}, took=0.2)
        if r < 0.06:
            return resp(200, b"garbage \xff\xfe", {}, took=0.2)
        if r < 0.09:
            return resp(200, b"", {}, took=rng.uniform(10.5, 30))           # past the deadline: the transport gives up
        if r < 0.12:
            return OSError("connection reset")
        took = rng.uniform(0.1, 9.0) if r < 0.25 else rng.uniform(0.1, 1.0)
        n_new = rng.randint(0, 6)
        if oauth:
            kids = []
            for k in range(n_new):
                state["i"] += 1
                kids.append(("t1_s%06d" % state["i"], t - 10 - k))
            a = listing(kids, after=("t1_s%06d" % state["i"]) if kids and rng.random() < 0.3 else None,
                        remaining=rng.choice([None, 90, 90, 90, 0]), reset=rng.choice([20, 60, 700]))
            a.took = took
            return a
        for k in range(n_new):
            state["i"] += 1
            when = datetime.fromtimestamp(t - 10 - k, UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            state["window"].insert(0, atom_entry("t1_s%06d" % state["i"], body="text %d" % state["i"], updated=when))
        del state["window"][40:]
        a = resp(200, atom_with(*state["window"]), {"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": str(rng.choice([56, 56, 56, 100]))}, took=took)
        return a
    return answer


def stormy_kalshi(seed):
    rng = random.Random(seed + 1)

    def answer(n, t, series):
        r = rng.random()
        if r < 0.03:
            return 429, {}, 0.2
        if r < 0.05:
            return 500, {}, 0.2
        if r < 0.06:
            return 200, b"<html>", 0.2
        if r < 0.08:
            return 200, {"markets": []}, 12.0                              # too slow
        took = rng.uniform(0.1, 2.0)
        ms = [market("%s-26NOV20AB%02d-X" % (series, k), bid="%.4f" % (rng.randint(1, 98) / 100), ask="0.9900",
                     last="%.4f" % (rng.randint(1, 98) / 100)) for k in range(rng.randint(0, 6))]
        return 200, {"markets": ms, "cursor": ""}, took
    return answer


@pytest.mark.parametrize("oauth", [False, True])
def test_48_hours_of_slow_failing_and_limited_answers_keep_every_limit(tmp_path, oauth):
    clock = Clock(T0 + 17)
    col, store, reddit, kalshi, clock, _ = make(tmp_path, clock=clock, answer=stormy_reddit(5, oauth), kanswer=stormy_kalshi(5),
                                                env=CREDS if oauth else {})
    run_for(col, clock, 48 * 3600)
    log = reddit.log
    assert len(log) > 500
    ts = [r["t"] for r in log]
    assert ts == sorted(ts)
    # back-offs: no request inside one, ever
    for e in store.rows("SELECT at, until FROM comment_events WHERE kind IN ('backoff', 'ratelimit')"):
        a, b = datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp(), \
            datetime.strptime(e["until"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp()
        assert 1 <= b - a <= 3600 + 1e-6                                   # rev 2: a wait the service names, up to an hour
        assert not [t for t in ts if a < t < b], (e, )
    if not oauth:
        assert all(m["method"] == "GET" and m["url"] == cl.PUBLIC_URL for m in log)
        assert all(not (5 <= minute_of_hour(t) <= 12) for t in ts)
        assert all(b - a >= 60 for a, b in zip(ts, ts[1:]))                # never more than one in any 60 seconds
        assert max(sum(1 for u in ts if t - 3600 < u <= t) for t in ts[::50]) <= 26
    else:
        for t in ts[::5]:
            assert sum(1 for u in ts if t - 60 < u <= t) <= cl.OAUTH_BURST
        assert all(u == cl.TOKEN_URL or u.startswith("https://oauth.reddit.com/") for u in (m["url"] for m in log))
        slots = {}
        for r in store.rows("SELECT slot_ts, page FROM comment_fetches WHERE purpose = 'feed'"):
            slots.setdefault(r["slot_ts"], []).append(r["page"])
        assert max(len(v) for v in slots.values()) <= cl.OAUTH_MAX_PAGES
    for m in log:
        assert m["timeout"] <= cl.STEP_DEADLINE_S
        assert "cookie" not in {k.lower() for k in m["headers"]}
    # every request that was sent has a fetch row, and every sent Kalshi request is within the deadline
    sent = store.rows("SELECT COUNT(*) AS n FROM comment_fetches WHERE failure IS NULL OR failure NOT IN ('held_back', 'deadline')")[0]["n"]
    assert sent == len(log)
    assert all(r["timeout"] <= cl.STEP_DEADLINE_S for r in kalshi.log)
    # the recorders never cost each other more than 10 seconds: a Reddit request starts within 10 s of its slot, and
    # the first Kalshi request of a pass too
    I = col.c_interval
    first_of_slot = {}
    for r in log:
        first_of_slot.setdefault(int(r["t"] // I * I), r["t"])
    late_reddit = max(t - sl for sl, t in list(first_of_slot.items())[1:])     # (the first slot of a run starts where the run does)
    assert late_reddit <= 10.0 + 1e-6
    firsts = {}
    for r in kalshi.log:
        firsts.setdefault(int(r["t"] // 60 * 60), r["t"])
    assert max(t - s for s, t in list(firsts.items())[1:]) <= 10.0 + 1e-6
    # price series: never more than one fetch per series per slot, and one per minute for 48 hours
    for s in cl.SERIES:
        slots = [r["slot_ts"] for r in store.rows("SELECT slot_ts FROM price_fetches WHERE series = ?", (s,))]
        assert len(slots) == len(set(slots)) and len(slots) > 2700
    # coverage is countable
    store.close()
    c = cl.Store(tmp_path / "c.sqlite", readonly=True)
    assert cl.verify(c) == []
    c.close()


# ------------------------------------------------------------------------------------- the store keeps its word (6)

def healthy(tmp_path, minutes=30, **kw):
    col, store, reddit, kalshi, clock, lines = make(tmp_path, **kw)
    run_for(col, clock, minutes * 60)
    return col, store, reddit, kalshi, clock


def test_verify_passes_a_store_made_by_a_run(tmp_path):
    col, store, *_ = healthy(tmp_path, 40)
    assert store.conn.execute("SELECT COUNT(*) FROM comment_versions").fetchone()[0] > 10
    assert cl.verify(store) == []


def break_it(store, trigger_names, sql, args=()):
    for t in trigger_names:
        store.conn.execute("DROP TRIGGER IF EXISTS " + t)
    store.conn.execute(sql, args)


@pytest.mark.parametrize("name,triggers,sql,args,expect", [
    ("text changed in place", ["no_update_comment_versions"], "UPDATE comment_versions SET body = 'x' WHERE id = 1", (), "signature"),
    ("version deleted", ["no_delete_comment_versions"], "DELETE FROM comment_versions WHERE id = 1", (), "version"),
    ("duplicate version", [], "INSERT INTO comment_versions (comment_id, version, author, author_enc, body, body_enc, sig, seen_at, fetch_id, change) "
     "SELECT comment_id, 2, author, author_enc, body, body_enc, sig, '2099-01-01T00:00:00.000Z', fetch_id, 'edit' FROM comment_versions WHERE id = 1", (), "duplicate"),
    ("payload changed", ["no_update_payloads"], "UPDATE payloads SET body = x'00' WHERE id = 1", (), "payload"),
    ("payload not compressed data", ["no_update_payloads"], "UPDATE payloads SET body = x'6e6f74207a6c6962' WHERE id = 1", (), "decompress"),
    ("payload truncated", ["no_update_payloads"], "UPDATE payloads SET body = substr(body, 1, length(body) - 6) WHERE id = 1", (), "decompress"),
    ("fetch count changed", ["no_update_comment_fetches"], "UPDATE comment_fetches SET new_versions = new_versions + 1 WHERE id = 1", (), "new_versions"),
    ("comment row removed", ["no_delete_comments"], "DELETE FROM comments WHERE rowid = 1", (), "comment"),
    ("price row repeated", [], "INSERT INTO prices (fetch_id, seen_at, series, ticker, yes_bid, yes_ask, last_price) "
     "SELECT fetch_id, '2099-01-01T00:00:00.000Z', series, ticker, yes_bid, yes_ask, last_price FROM prices WHERE id = 1", (), "price"),
    ("time goes back", ["time_order_comment_events"], "INSERT INTO comment_events (at, kind) VALUES ('2000-01-01T00:00:00.000Z', 'x')", (), "time"),
    ("version of nothing", [], "INSERT INTO comment_versions (comment_id, version, author, author_enc, body, body_enc, sig, seen_at, fetch_id, change) "
     "VALUES ('t1_ghost', 1, NULL, 'utf8', 'x', 'utf8', 'abc', '2099-01-01T00:00:00.000Z', 1, 'first')", (), "comment"),
])
def test_verify_finds_what_was_done_to_the_store(tmp_path, name, triggers, sql, args, expect):
    col, store, *_ = healthy(tmp_path, 40)
    assert cl.verify(store) == []
    break_it(store, triggers, sql, args)
    problems = cl.verify(store)
    assert problems and any(expect in p for p in problems), (name, problems)
    assert all("text " not in p or True for p in problems)


# --------------------------------------------------------------------------------------- what status counts (8)

def run_store(tmp_path, seconds, start=T0, **kw):
    clock = Clock(start)
    col, store, reddit, kalshi, clock, lines = make(tmp_path, clock=clock, **kw)
    run_for(col, clock, seconds)
    return col, store, reddit, kalshi, clock


def test_status_counts_slots_per_utc_day_with_the_silence_shown_and_excluded(tmp_path):
    col, store, reddit, kalshi, clock = run_store(tmp_path, 3 * 3600)
    st = cl.status(store, T0 + 3 * 3600)
    assert [d["day"] for d in st["days"]] == ["2026-11-13"]
    d = st["days"][0]
    c = d["comments"]
    assert (c["expected"], c["silent"], c["made"], c["failed"]) == (78, 12, 78, 0)
    assert c["comments"] == store.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] > 0
    assert c["versions"] == store.conn.execute("SELECT COUNT(*) FROM comment_versions").fetchone()[0]
    assert c["backoff_minutes"] == 0 and c["misses"] == 0 and c["share"] == 1.0 and c["covered"] is True
    p = d["prices"]
    assert p["expected"] == 5 * 180 and p["made"] == 900 and p["failed"] == 0 and p["share"] == 1.0 and p["covered"] is True
    for s in cl.SERIES:
        ps = p["series"][s]
        assert (ps["expected"], ps["made"], ps["failed"]) == (180, 180, 0)
        assert ps["markets_seen"] == 2 and ps["rows"] == 2
    assert d["counts"] is True


def test_status_splits_days_at_midnight_utc(tmp_path):
    midnight = 1794528000 + 86400                                    # 2026-11-14 00:00 UTC
    col, store, *_ = run_store(tmp_path, 2 * 3600, start=midnight - 3600)
    st = cl.status(store, midnight + 3600)
    assert [d["day"] for d in st["days"]] == ["2026-11-13", "2026-11-14"]
    assert st["days"][0]["prices"]["expected"] == 5 * 60 and st["days"][1]["prices"]["expected"] == 5 * 60
    assert st["days"][0]["comments"]["expected"] == 26 and st["days"][1]["comments"]["expected"] == 26


def test_status_counts_failures_backoffs_and_possible_misses(tmp_path):
    state = {"n": 0}

    def answer(n, t, m, u):
        state["n"] += 1
        if state["n"] == 1:
            return resp(429, b"", {"Retry-After": "240"})
        if state["n"] == 5:
            return resp(500, b"")
        if state["n"] == 8:                                          # a feed that starts far after what was stored
            return resp(200, atom_with(atom_entry("t1_late1", updated="2026-11-13T07:59:00+00:00")))
        return resp(200, atom_with(atom_entry("t1_c%d" % n, updated=datetime.fromtimestamp(T0 + 60 * n, UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"))))
    col, store, *_ = run_store(tmp_path, 3600, answer=answer)
    c = cl.status(store, T0 + 3600)["days"][0]["comments"]
    assert c["failed"] == 2 and c["backoff_minutes"] == 4.0 and c["misses"] >= 1
    assert c["made"] + c["failed"] < c["expected"]                  # the back-off's slots were neither made nor failed
    p = cl.status(store, T0 + 3600)["days"][0]["prices"]
    assert p["made"] == 300


def test_status_calls_a_blind_stretch_a_gap_not_a_quiet_one(tmp_path):
    col, store, reddit, kalshi, clock = run_store(tmp_path, 3600, answer=lambda n, t, m, u: resp(200, atom_with()))
    quiet = cl.status(store, T0 + 3600)["days"][0]
    assert quiet["comments"]["share"] == 1.0 and quiet["comments"]["comments"] == 0       # an empty feed is a quiet hour, covered
    st = cl.status(store, T0 + 4 * 3600)["days"][0]                # then nobody records for three hours
    assert st["comments"]["share"] < 0.3 and st["comments"]["covered"] is False and st["counts"] is False
    assert st["prices"]["share"] < 0.3 and st["prices"]["covered"] is False


def test_status_a_failing_price_series_is_shown_and_80_percent_is_the_line(tmp_path):
    def kanswer(n, t, series):
        return (500, {}, 0.1) if series == "KXNHLGAME" else (200, {"markets": [], "cursor": ""}, 0.1)
    col, store, *_ = run_store(tmp_path, 3600, kanswer=kanswer)
    d = cl.status(store, T0 + 3600)["days"][0]
    assert d["prices"]["series"]["KXNHLGAME"]["failed"] == 60 and d["prices"]["series"]["KXNHLGAME"]["share"] == 0.0
    assert d["prices"]["share"] == 0.8 and d["prices"]["covered"] is True                 # four of five series: exactly the line
    assert min(v["share"] for v in d["prices"]["series"].values()) == 0.0


def test_status_text_has_counts_only(tmp_path):
    col, store, *_ = run_store(tmp_path, 1800, answer=lambda n, t, m, u: resp(200, atom_with(atom_entry("t1_c%d" % n, body="CANARY-%d" % n))))
    text = cl.format_status(cl.status(store, T0 + 1800))
    assert "2026-11-13" in text and "KXNBAGAME" in text and "CANARY" not in text
    assert "comments" in text and "prices" in text


# ------------------------------------------------------------------------------------------ the command line (7, 4)

def cli(tmp_path, argv, clock=None, answer=None, kanswer=None, env=None):
    clock = clock or Clock(T0)
    out = []
    code = cl.main(argv, clock=clock, sleep=clock.sleep, reddit_fetch=FakeReddit(clock, answer or feed_answer()),
                   kalshi_get=FakeKalshi(clock, kanswer), env=env or {}, out=out.append)
    return code, "\n".join(out), clock


def test_run_for_minutes_then_status_then_check(tmp_path):
    db = str(tmp_path / "chatter.sqlite")
    code, out, clock = cli(tmp_path, ["run", "--db", db, "--for-minutes", "20"])
    assert code == 0 and "started" in out and "stopped" in out
    code, out, _ = cli(tmp_path, ["status", "--db", db], clock=clock)
    assert code == 0 and "2026-11-13" in out
    code, out, _ = cli(tmp_path, ["check", "--db", db], clock=clock)
    assert code == 0 and out.startswith("check: ok")


def test_the_output_of_run_status_and_check_never_holds_a_comment_text_an_author_or_an_id(tmp_path):
    db = str(tmp_path / "chatter.sqlite")

    def answer(n, t, m, u):
        return resp(200, atom_with(atom_entry("t1_SECRETID%d" % n, author="/u/SECRETUSER", body="SECRETTEXT-%d" % n, thread="SECRETTHREAD",
                                              updated=datetime.fromtimestamp(t - 5, UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"))))
    code, out1, clock = cli(tmp_path, ["run", "--db", db, "--for-minutes", "30"], answer=answer)
    _, out2, _ = cli(tmp_path, ["status", "--db", db], clock=clock)
    _, out3, _ = cli(tmp_path, ["check", "--db", db], clock=clock)
    for out in (out1, out2, out3):
        assert "SECRET" not in out


def test_no_failure_text_carries_anything_the_answer_said(tmp_path):
    def answer(n, t, m, u):
        return resp(500, b"SECRETBODY <script> " + b"x" * 100, {"x-evil": "SECRETHEADER", "retry-after": "SECRETRETRY"})
    col, store, *_ = run_store(tmp_path, 900, answer=answer)
    for tbl, cols in (("comment_fetches", "failure"), ("comment_events", "kind, detail"), ("price_events", "kind, detail")):
        for r in store.rows("SELECT %s FROM %s" % (cols, tbl)):
            assert "SECRET" not in repr(r)


def test_check_exits_1_and_says_so_for_a_store_that_does_not_keep_its_word(tmp_path):
    db = tmp_path / "chatter.sqlite"
    code, _, clock = cli(tmp_path, ["run", "--db", str(db), "--for-minutes", "15"])
    s = cl.Store(db)
    s.conn.execute("DROP TRIGGER no_update_comment_versions")
    s.conn.execute("UPDATE comment_versions SET body = 'x' WHERE id = 1")
    s.close()
    code, out, _ = cli(tmp_path, ["check", "--db", str(db)], clock=clock)
    assert code == 1 and "check: FAILED" in out


def test_check_refuses_a_foreign_file_and_a_missing_one_without_touching_them(tmp_path):
    junk = tmp_path / "junk"
    junk.write_bytes(b"hello" * 50)
    code, out, _ = cli(tmp_path, ["check", "--db", str(junk)])
    assert code == 1 and "not a chatter store" in out and junk.read_bytes() == b"hello" * 50
    code, out, _ = cli(tmp_path, ["check", "--db", str(tmp_path / "nope.sqlite")])
    assert code == 1 and not (tmp_path / "nope.sqlite").exists()
    code, out, _ = cli(tmp_path, ["status", "--db", str(tmp_path / "nope.sqlite")])
    assert code == 1


def test_run_refuses_a_second_writer(tmp_path):
    db = tmp_path / "chatter.sqlite"
    held = cl.Store(db, lock=True)
    code, out, _ = cli(tmp_path, ["run", "--db", str(db), "--for-minutes", "1"])
    assert code == 1 and "another" in out
    held.close()


def test_run_stops_with_a_plain_message_when_the_clock_is_behind_the_store(tmp_path):
    db = str(tmp_path / "chatter.sqlite")
    cli(tmp_path, ["run", "--db", db, "--for-minutes", "20"])
    code, out, _ = cli(tmp_path, ["run", "--db", db, "--for-minutes", "5"], clock=Clock(T0 - 7200))
    assert code == 1 and "earlier than the latest time" in out


def test_run_on_a_foreign_file_does_nothing(tmp_path):
    junk = tmp_path / "junk"
    junk.write_bytes(b"x" * 200)
    code, out, _ = cli(tmp_path, ["run", "--db", str(junk), "--for-minutes", "1"])
    assert code == 1 and junk.read_bytes() == b"x" * 200 and not Path(str(junk) + ".lock").exists()


# ------------------------------------------------------------------------- nothing from the trading secrets file (3)

def test_the_module_never_loads_the_trading_secrets_file_or_starts_a_child_process():
    src = Path(cl.__file__).read_text()
    assert "load_env" not in src and "trading.env" not in src
    assert not re.search(r"\bsubprocess\b|os\.system|os\.popen|os\.exec|os\.spawn|posix_spawn|Popen", src)
    code = ("import sys; import valedia_trader.research_loop.chatter_loop as c; "
            "bad = [m for m in ('valedia_trader.core.secrets', 'valedia_trader.venues.odds_api', 'subprocess') if m in sys.modules and m != 'subprocess']; "
            "print(bad)")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(Path(cl.__file__).parents[2])},
                         cwd="/").stdout.strip()
    assert out == "[]"


def test_the_trading_secrets_in_the_environment_are_not_read(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "madeUpOddsKey")
    monkeypatch.setenv("WEBULL_APP_SECRET", "madeUpWebullSecret")
    col, store, reddit, kalshi, clock, lines = make(tmp_path, env=os.environ)
    run_for(col, clock, 600)
    store.close()
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("c.sqlite*"))
    assert b"madeUp" not in blob
    for r in reddit.log + kalshi.log:
        assert "madeUp" not in repr(r)


# ------------------------------------- generated: 2,000 fetch sequences against a model of the versions that must exist (5)

class Model:
    """What must exist, written without looking at the store's code: a version whenever author or text differs from the
    latest one of that comment, stamped with the fetch that first showed it; a possible miss by the stated rule."""

    def __init__(self):
        self.versions = {}          # id -> [(author, body, seen_ms, source_time)]
        self.first_time = {}        # id -> source time of its first version
        self.misses = 0
        self.failed = 0

    def fetch(self, entries, at_ms):
        if entries is None:
            self.failed += 1
            return
        last = {}
        for e in entries:
            last[e.comment_id] = e                                       # the later of two copies in one answer wins
        if entries and self.first_time:
            newest = max(t for t in self.first_time.values() if t) if any(self.first_time.values()) else None
            times = [e.source_time for e in entries if e.source_time]
            if times and newest:
                miss = min(times) > newest
            else:
                miss = not any(e.comment_id in self.versions for e in entries)
            self.misses += 1 if miss else 0
        for cid, e in last.items():
            vs = self.versions.setdefault(cid, [])
            if not vs:
                self.first_time[cid] = e.source_time
            if not vs or (vs[-1][0], vs[-1][1]) != (e.author, e.body):
                vs.append((e.author, e.body, at_ms, e.source_time))


def sequence(seed):
    rng = random.Random(seed)
    n_ids = rng.randint(3, 25)
    ids = ["t1_g%03d" % i for i in range(n_ids)]
    born = {i: T0 + 40 * k for k, i in enumerate(ids)}                   # creation times, increasing
    texts = ["alpha", "beta", "gamma", "", "[deleted]", "x" * rng.randint(1, 50), "unicode é中", "<b>&amp;</b>"]
    state = {i: (rng.choice(["/u/a", "/u/b", None]), rng.choice(texts)) for i in ids}
    history = {i: [state[i]] for i in ids}
    fetches = []
    t = float(T0 + 10_000)
    avail = rng.randint(1, max(1, n_ids // 2))
    for k in range(rng.randint(2, 12)):
        avail = min(n_ids, avail + rng.choice([0, 1, 1, 2, n_ids // 2]))      # new comments keep arriving; sometimes a burst
        t += rng.choice([60, 120, 120, 300]) + rng.randint(0, 999) / 1000
        for i in ids:                                                    # edits, deletions, returns to an earlier text
            r = rng.random()
            if r < 0.15:
                state[i] = (state[i][0], rng.choice(texts + ["new %d" % rng.randint(0, 5)]))
            elif r < 0.20:
                state[i] = (rng.choice(["/u/a", "/u/b", "[deleted]", None]), state[i][1])
            elif r < 0.30:
                state[i] = rng.choice(history[i])
            history[i].append(state[i])
        recent = ids[max(0, avail - rng.randint(2, 9)):avail]                 # a feed reaches back only so far
        shown = [i for i in recent if rng.random() < 0.8]
        if rng.random() < 0.2:
            shown = shown[-rng.randint(0, 2):] if shown else shown         # a feed with a hole: only the newest
        rng.shuffle(shown) if rng.random() < 0.5 else shown.sort(reverse=True)           # reorderings
        shown += [rng.choice(shown) for _ in range(rng.randint(0, 2))] if shown else []   # duplicates
        entries = [entry(i, state[i][1], state[i][0], time_=datetime.fromtimestamp(born[i], UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
                   for i in shown]
        if rng.random() < 0.12:
            entries = None                                               # an answer that could not be read
        fetches.append((t, entries))
    return fetches


def test_two_thousand_generated_fetch_sequences_keep_exactly_the_versions_the_model_says(tmp_path):
    template = tmp_path / "template.sqlite"
    cl.Store(template).close()
    checked = {"versions": 0, "misses": 0, "failed": 0}
    for seed in range(2000):
        path = tmp_path / "seq.sqlite"
        for p in tmp_path.glob("seq.sqlite*"):
            p.unlink()
        shutil.copy(template, path)
        s = cl.Store(path, synchronous="OFF")
        model = Model()
        for at, entries in sequence(seed):
            f = cl.Fetch(at=at, slot=int(at), backend="public", purpose="feed", page=1, status=200,
                         failure=None if entries is not None else "unparseable", body=b"<feed/>" + str(at).encode(), entries=entries)
            s.save_comment_slot([f], [])
            model.fetch(entries, int(round(at * 1000)))
        got = {}
        for r in s.rows("SELECT comment_id FROM comments"):
            got[r["comment_id"]] = [(v["author"], v["body"], v["seen_at"]) for v in s.versions(r["comment_id"])]
        want = {cid: [(a, b, stamp(ms / 1000)) for a, b, ms, _ in vs] for cid, vs in model.versions.items()}
        assert got == want, seed
        assert len(s.rows("SELECT id FROM comment_events WHERE kind = 'possible_miss'")) == model.misses, seed
        assert s.conn.execute("SELECT COUNT(*) FROM comment_fetches WHERE ok = 0").fetchone()[0] == model.failed, seed
        for v in s.rows("SELECT comment_id, version, change FROM comment_versions"):
            assert (v["change"] == "first") == (v["version"] == 1)
        if seed % 25 == 0:
            assert cl.verify(s) == [], seed
        checked["versions"] += sum(len(v) for v in want.values())
        checked["misses"] += model.misses
        checked["failed"] += model.failed
        s.close()
    assert checked["versions"] > 10000 and checked["misses"] > 300 and checked["failed"] > 500      # the sequences do exercise it


# ------------------------------------------------------- real kills at random moments, each followed by check (6)

CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[3])
from datetime import datetime, timezone
from valedia_trader.research_loop import chatter_loop as cl
db, start = sys.argv[1], float(sys.argv[2])


class Clock:
    t = start
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


clock = Clock()
state = {"n": 0}


def reddit(method, url, headers, data, timeout):
    state["n"] += 1
    n = state["n"]
    ents = []
    for k in range(40):
        cid = n * 5 - k if k < 5 else n * 5 - k
        j = n * 5 - k
        if j < 1:
            continue
        text = "t%d-v%d" % (j, (n + j) // 6)
        when = datetime.fromtimestamp(clock.t - 3 * k, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        ents.append("<entry><author><name>/u/a%d</name></author><content type=\"html\">%s</content><id>t1_k%07d</id>"
                    "<link href=\"https://www.reddit.com/r/sportsbook/comments/zz/s/k%07d/\"/><updated>%s</updated></entry>"
                    % (j % 7, text, j, j, when))
    body = ('<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(ents) + "</feed>").encode()
    return cl.Response(200, {"x-ratelimit-reset": "56"}, body)


def kalshi(url, timeout):
    n = state["n"]
    ms = [{"ticker": "S-%d" % i, "event_ticker": "S", "yes_bid_dollars": "%.4f" % (0.30 + ((n + i) % 9) / 100),
           "yes_ask_dollars": "0.9000", "last_price_dollars": "0.5000", "volume_fp": "1.00", "open_interest_fp": "1.00",
           "yes_bid_size_fp": "1.00", "yes_ask_size_fp": "1.00", "status": "active"} for i in range(6)]
    return json.dumps({"markets": ms, "cursor": ""}).encode()


store = cl.Store(db, lock=True)
col = cl.Collector(store, clock=clock, sleep=clock.sleep, reddit_fetch=reddit, kalshi_get=kalshi, env={}, log=lambda s: print(s, flush=True))
col.run(float(sys.argv[4]))
store.close()
'''


def test_real_kills_at_random_moments_leave_a_store_that_check_passes_and_the_next_run_continues(tmp_path):
    db = tmp_path / "chatter.sqlite"
    rng = random.Random(30)
    repo = str(Path(cl.__file__).resolve().parents[2])
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(tmp_path)}
    kills, base, seen_versions, started = 0, float(T0), 0, 0
    for i in range(64):
        start = base
        if db.exists():
            s = cl.Store(db, readonly=True)
            latest = s.latest_time()
            s.close()
            if latest:
                start = max(start, datetime.strptime(latest, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp() + 1.0)
        p = subprocess.Popen(["nice", "-n", "15", sys.executable, "-u", "-c", CHILD, str(db), repr(start), repo, "100000"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
        try:
            if i == 0 or rng.random() < 0.7:
                assert p.stdout.readline().startswith("chatter_loop: started")      # a run that is under way
                started += 1
                time.sleep(rng.uniform(0.0, 0.6))
            else:
                time.sleep(rng.uniform(0.0, 0.5))                                   # or one still starting up
            p.send_signal(signal.SIGKILL)
        finally:
            p.wait(timeout=30)
            p.stdout.close()
        assert p.returncode == -signal.SIGKILL
        kills += 1
        if db.exists():
            s = cl.Store(db, readonly=True)
            assert cl.verify(s) == [], i
            seen_versions = s.conn.execute("SELECT COUNT(*) FROM comment_versions").fetchone()[0]
            s.close()
            assert cl.main(["check", "--db", str(db)], out=lambda line: None) == 0, i
        assert not Path(str(db) + ".creating").exists() or not db.exists()
    assert kills >= 60 and started >= 30 and seen_versions > 100
    # the next run continues and finishes by itself; the store is still whole and has no duplicate version
    s = cl.Store(db, readonly=True)
    before = (s.conn.execute("SELECT COUNT(*) FROM comment_versions").fetchone()[0], s.conn.execute("SELECT COUNT(*) FROM price_fetches").fetchone()[0])
    latest = s.latest_time()
    s.close()
    start = datetime.strptime(latest, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp() + 1.0
    done = subprocess.run(["nice", "-n", "15", sys.executable, "-u", "-c", CHILD, str(db), repr(start), repo, "20"], capture_output=True,
                          text=True, env=env, timeout=120)
    assert done.returncode == 0 and "stopped" in done.stdout
    s = cl.Store(db, readonly=True)
    assert cl.verify(s) == []
    after = (s.conn.execute("SELECT COUNT(*) FROM comment_versions").fetchone()[0], s.conn.execute("SELECT COUNT(*) FROM price_fetches").fetchone()[0])
    assert after[1] > before[1]
    assert s.conn.execute("SELECT COUNT(*) FROM (SELECT comment_id, version FROM comment_versions GROUP BY 1, 2 HAVING COUNT(*) > 1)").fetchone()[0] == 0
    assert s.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= started + 1
    s.close()


# ---------------------------------------------- limits hold across a restart too: the store remembers (1)

def restart(tmp_path, store, clock, reddit, kalshi, env=None):
    store.close()
    s2 = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    return cl.Collector(s2, clock=clock, sleep=clock.sleep, reddit_fetch=reddit, kalshi_get=kalshi, env=env or {}, log=lambda s: None), s2


def test_a_restart_within_a_minute_of_a_request_does_not_make_a_second_one(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path)
    col.start()
    col.step()                                                            # the 06:00 slot: one request
    assert len(reddit.log) == 1
    clock.sleep(20)
    col2, s2 = restart(tmp_path, store, clock, reddit, kalshi)           # killed and restarted 20 s later
    run_for(col2, clock, 200)
    ts = [r["t"] for r in reddit.log]
    assert all(b - a >= 60 for a, b in zip(ts, ts[1:])), ts


def test_a_restart_inside_a_back_off_does_not_ask_until_it_ends(tmp_path):
    state = {"n": 0}

    def answer(n, t, m, u):
        state["n"] += 1
        return resp(429, b"", {"Retry-After": "500"}) if state["n"] == 1 else resp(200, atom_with(atom_entry("t1_x%d" % n)))
    col, store, reddit, kalshi, clock, _ = make(tmp_path, answer=answer)
    col.start()
    col.step()
    first = reddit.log[0]["t"]
    clock.sleep(130)
    col2, s2 = restart(tmp_path, store, clock, reddit, kalshi)
    run_for(col2, clock, 1200)
    assert len(reddit.log) >= 2 and reddit.log[1]["t"] - first >= 500


def test_a_restart_remembers_the_official_interfaces_recent_requests(tmp_path):
    def pages(n, url):
        return listing([("t1_n%03d" % n, T0 + n * 60 - 3)], remaining=90, reset=60)
    col, store, reddit, kalshi, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    run_for(col, clock, 300)
    col2, s2 = restart(tmp_path, store, clock, reddit, kalshi, env=CREDS)
    run_for(col2, clock, 300)
    for t in [r["t"] for r in reddit.log]:
        assert sum(1 for r in reddit.log if t - 60 < r["t"] <= t) <= cl.OAUTH_BURST


def test_status_for_the_official_interface_has_no_silence(tmp_path):
    def pages(n, url):
        return listing([("t1_n%03d" % n, T0 + n * 60 - 3)])
    col, store, *_ = run_store(tmp_path, 3600, answer=oauth_answer(pages), env=CREDS)
    c = cl.status(store, T0 + 3600)["days"][0]["comments"]
    assert (c["expected"], c["silent"], c["made"]) == (60, 0, 60)


# ---------------------------------------------------------------------------- edges found by breaking the code on purpose

def test_the_latest_slot_more_than_half_over_is_skipped_for_both_recorders(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path)
    col.start()
    clock.sleep(100)                                                     # 100 s into a 120 s slot, 40 s into a 60 s slot
    col.step()
    assert reddit.log == [] and kalshi.log == []
    assert store.rows("SELECT kind, count, detail FROM comment_events WHERE kind = 'skipped'") == [
        {"kind": "skipped", "count": 1, "detail": "more than half over"}]
    assert store.rows("SELECT count, detail FROM price_events WHERE kind = 'skipped' ORDER BY id") == [
        {"count": 1, "detail": "late"}, {"count": 1, "detail": "more than half over"}]      # the 06:00 slot, then the 06:01 one
    clock.sleep(20)                                                      # and the next slots are taken on time
    col.step()
    assert len(reddit.log) == 1 and len(kalshi.log) == 5


def test_a_slot_that_starts_in_the_hourly_silence_sends_nothing_and_is_a_failed_fetch(tmp_path):
    col, store, reddit, kalshi, clock, _ = make(tmp_path)
    clock.t = T0 + 239
    col.start()                                                          # the next slot is 06:04
    clock.sleep(61)                                                      # exactly 06:05:00, a minute into the 06:04 slot
    col.step()
    assert reddit.log == []
    assert store.rows("SELECT ok, failure FROM comment_fetches") == [{"ok": 0, "failure": "held_back"}]


def test_a_store_with_another_application_id_or_layout_number_in_its_header_is_refused_untouched(tmp_path):
    for name, offset in (("app", 68), ("layout", 60)):
        p = tmp_path / ("c-%s.sqlite" % name)
        cl.Store(p).close()
        raw = bytearray(p.read_bytes())
        raw[offset:offset + 4] = (int.from_bytes(raw[offset:offset + 4], "big") + 1).to_bytes(4, "big")
        p.write_bytes(bytes(raw))
        before = p.read_bytes()
        with pytest.raises(cl.StoreError):
            cl.Store(p)
        with pytest.raises(cl.StoreError):
            cl.Store(p, readonly=True)
        assert p.read_bytes() == before
        assert not Path(str(p) + "-wal").exists() and not Path(str(p) + cl.LOCK_SUFFIX).exists()


def test_a_public_answer_that_is_not_a_feed_is_a_failed_fetch_stored_raw_and_counted(tmp_path):
    col, store, reddit, *_ = make(tmp_path, answer=lambda n, t, m, u: resp(200, b"<html><body>Reddit is down</body></html>"))
    run_for(col, col.clock, 300)
    rows = store.rows("SELECT ok, failure, payload_id, comments_in_feed FROM comment_fetches")
    assert rows and all(r["ok"] == 0 and r["failure"] == "unparseable" and r["payload_id"] for r in rows)
    assert raw_bodies(store)[0] == b"<html><body>Reddit is down</body></html>"
    c = cl.status(store, T0 + 300)["days"][0]["comments"]
    assert c["failed"] == len(rows) and c["made"] == 0


def test_a_transport_that_breaks_is_a_failed_fetch_not_a_stopped_run(tmp_path):
    seq = iter([OSError("boom"), RuntimeError("bug"), ValueError("bad")])

    def answer(n, t, m, u):
        return next(seq, None) or resp(200, atom_with(atom_entry("t1_ok%d" % n)))
    col, store, *_ = make(tmp_path, answer=answer)
    run_for(col, col.clock, 1000)
    fails = [r["failure"] for r in store.rows("SELECT failure FROM comment_fetches ORDER BY id") if r["failure"]]
    assert fails == ["network", "error", "error"]
    assert store.conn.execute("SELECT COUNT(*) FROM comment_versions").fetchone()[0] >= 1


def test_the_oversized_answer_is_a_failed_fetch_and_what_was_read_is_kept(tmp_path):
    col, store, *_ = make(tmp_path, answer=lambda n, t, m, u: resp(200, b"x" * 100, truncated=True))
    run_for(col, col.clock, 200)
    assert {r["failure"] for r in store.rows("SELECT failure FROM comment_fetches")} == {"too_large"}
    assert store.rows("SELECT size FROM payloads")[0]["size"] == 100


# ------------------------------------------------------- raw answers are kept compressed, hash over the exact bytes (6)

HOSTILE = [b"", b"\xff\xfe\x00 not utf-8 \xc3\x28", bytes(range(256)) * 40, b"<feed>" + b"a" * (cl.MAX_BODY - 13) + b"</feed>",
           os.urandom(70_000), b"\x00" * 100_000]


@pytest.mark.parametrize("raw", HOSTILE, ids=["empty", "bad-utf8", "all-bytes", "MAX_BODY", "random", "zeros"])
def test_a_raw_answer_round_trips_exactly_whatever_its_bytes_and_the_hash_is_over_what_was_received(store, raw):
    f = cl.Fetch(at=T0, slot=T0, backend="public", purpose="feed", page=1, status=200, failure="unparseable", body=raw, entries=None)
    store.save_comment_slot([f], [])
    r = store.rows("SELECT id, sha256, size, body FROM payloads")[0]
    assert store.payload_bytes(r["id"]) == raw
    assert r["sha256"] == hashlib.sha256(raw).hexdigest() and r["size"] == len(raw)
    assert zlib.decompress(bytes(r["body"])) == raw                         # the blob is the bytes, zlib-compressed
    assert cl.verify(store) == []


def test_a_repeated_answer_is_still_stored_once_and_the_blob_is_smaller_than_the_answer(store):
    raw = ATOM_FIXTURE.encode() * 20
    for i in range(3):
        feed(store, [entry("c1")], T0 + 120 * i, body=raw)
    rows = store.rows("SELECT size, length(body) AS blob FROM payloads")
    assert len(rows) == 1 and rows[0]["size"] == len(raw) and rows[0]["blob"] < len(raw) / 4


def test_check_finds_a_blob_that_does_not_decompress_one_with_the_wrong_hash_and_one_with_the_wrong_size(tmp_path):
    for name, sql, word in (("garbage", "UPDATE payloads SET body = x'0102030405' WHERE id = 1", "decompress"),
                            ("other bytes", "UPDATE payloads SET body = x'%s' WHERE id = 1" % zlib.compress(b"<feed>fake</feed>").hex(), "hash"),
                            ("wrong size", "UPDATE payloads SET size = size + 1 WHERE id = 1", "decompress")):
        s = cl.Store(tmp_path / ("%s.sqlite" % name.replace(" ", "")), synchronous="OFF")
        feed(s, [entry("c1")], T0, body=b"<feed>real</feed>")
        assert cl.verify(s) == []
        for t in ("no_update_payloads",):
            s.conn.execute("DROP TRIGGER " + t)
        s.conn.execute(sql)
        bad = cl.verify(s)
        assert bad and any("payload" in p and word in p for p in bad), (name, bad)
        s.close()


def test_a_decompression_bomb_in_the_store_is_caught_without_filling_memory(tmp_path):
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    feed(s, [entry("c1")], T0, body=b"<feed>real</feed>")
    bomb = zlib.compress(b"\x00" * 400_000_000, 9)                            # a few hundred KB that would be 400 MB
    s.conn.execute("DROP TRIGGER no_update_payloads")
    s.conn.execute("UPDATE payloads SET body = ? WHERE id = 1", (bomb,))
    import tracemalloc
    tracemalloc.start()
    t = time.time()
    assert any("payload" in p for p in cl.verify(s))
    with pytest.raises(cl.StoreError):
        s.payload_bytes(1)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert time.time() - t < 5 and peak < 50_000_000
    s.close()


# ==================================================================== rev 2: the repairs after the blind check (V90)

class Killed(BaseException):
    """A kill at this point: nothing after it runs and nothing uncommitted is kept. A BaseException, so that no handler
    in the program can catch it, as none can catch SIGKILL."""


def iso_ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp()


def collector(store, clock, reddit, kalshi, env=None):
    return cl.Collector(store, clock=clock, sleep=clock.sleep, reddit_fetch=reddit, kalshi_get=kalshi, env=env or {},
                        log=lambda s: None)


def kalshi_ok(clock, took=0.1):
    def get(url, timeout):
        clock.sleep(took)
        return b'{"markets": [], "cursor": ""}'
    return get


# ------------------------------------------------------- outcome 1: a durable row before any request leaves

def test_a_durable_row_is_committed_before_each_request_leaves(tmp_path):
    """Public feed, token, official interface and Kalshi: while the request is out, another connection to the file
    already sees the row that says it was about to be sent, with its service, purpose, slot and time."""
    path = tmp_path / "c.sqlite"
    problems, seen = [], []

    def look(service, purpose, t, series=None):
        try:
            r = cl.Store(path, readonly=True)
            try:
                row = r.rows("SELECT at, service, backend, purpose, slot_ts, page, series FROM requests ORDER BY id DESC LIMIT 1")
            finally:
                r.close()
            if not row or (row[0]["service"], row[0]["purpose"], row[0]["at"], row[0]["series"]) != (service, purpose, stamp(t), series):
                problems.append((service, purpose, row))
            else:
                seen.append((service, purpose))
        except Exception as e:                                          # an assertion here would be swallowed as a failed fetch
            problems.append((service, purpose, type(e).__name__))

    for env in ({}, CREDS):
        for p in tmp_path.glob("c.sqlite*"):
            p.unlink()
        clock = Clock()
        answer = oauth_answer(lambda n, url: listing([("t1_n%03d" % n, T0 + n)])) if env else feed_answer()

        def reddit(method, url, headers, data, timeout):
            look("reddit", "token" if url == cl.TOKEN_URL else "feed", clock())
            clock.sleep(0.2)
            return answer(0, clock(), method, url)

        def kalshi(url, timeout):
            look("kalshi", "prices", clock(), dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["series_ticker"])
            clock.sleep(0.1)
            return b'{"markets": [], "cursor": ""}'
        store = cl.Store(path, synchronous="OFF")
        run_for(collector(store, clock, reddit, kalshi, env), clock, 300)
        store.close()
    assert problems == []
    n = Counter(seen)
    assert n[("reddit", "feed")] >= 3 + 5 and n[("reddit", "token")] == 1 and n[("kalshi", "prices")] == 2 * 25


def test_a_request_whose_answer_never_came_is_recorded_as_unknown_by_appending_and_kalshi_is_not_asked_twice(tmp_path):
    path = tmp_path / "c.sqlite"
    clock = Clock()

    def reddit_killed(method, url, headers, data, timeout):
        raise Killed                                                    # killed while the request is out
    store = cl.Store(path, synchronous="OFF")
    col = collector(store, clock, reddit_killed, kalshi_ok(clock))
    col.start()
    with pytest.raises(Killed):
        col.step()
    store.close()
    s = cl.Store(path, synchronous="OFF")
    before = s.rows("SELECT * FROM requests ORDER BY id")
    assert [(r["service"], r["purpose"], r["slot_ts"], r["at"]) for r in before] == [("reddit", "feed", T0, stamp(T0))]
    assert s.rows("SELECT id FROM comment_fetches") == []
    clock.sleep(1)
    col = collector(s, clock, FakeReddit(clock, feed_answer()), kalshi_ok(clock))
    col.start()
    assert s.rows("SELECT * FROM requests ORDER BY id")[:1] == before                       # never rewritten
    assert s.rows("SELECT fetched_at, slot_ts, purpose, page, ok, failure, request_id FROM comment_fetches") == [
        {"fetched_at": stamp(T0 + 1), "slot_ts": T0, "purpose": "feed", "page": 1, "ok": 0, "failure": "unknown",
         "request_id": before[0]["id"]}]
    s.close()
    # Kalshi: killed while the third series is out; the restart in the same minute asks only the two never asked
    path2 = tmp_path / "k.sqlite"
    clock = Clock()
    asked = []

    def kalshi(url, timeout):
        series = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["series_ticker"]
        asked.append((clock(), series))
        if len(asked) == 3:
            raise Killed
        clock.sleep(0.1)
        return b'{"markets": [], "cursor": ""}'
    reddit = FakeReddit(clock, feed_answer())
    store = cl.Store(path2, synchronous="OFF")
    col = collector(store, clock, reddit, kalshi)
    col.start()
    with pytest.raises(Killed):
        col.step()
    store.close()
    clock.sleep(0.5)
    s = cl.Store(path2, synchronous="OFF")
    run_for(collector(s, clock, reddit, kalshi), clock, 20)
    assert [x[1] for x in asked] == list(cl.SERIES)                   # each series once in the 06:00 minute
    rows = {r["series"]: (r["ok"], r["failure"]) for r in s.rows("SELECT series, ok, failure FROM price_fetches WHERE slot_ts = ?", (T0,))}
    assert rows == {"KXNFLGAME": (1, None), "KXNBAGAME": (1, None), "KXNHLGAME": (0, "unknown"), "KXNCAAFGAME": (1, None),
                    "KXNCAAMBGAME": (1, None)}
    assert cl.verify(s) == []
    s.close()


@pytest.mark.parametrize("restart_after", [0.0, 1.0, 40.0, 59.0])
def test_a_kill_during_a_public_request_and_a_restart_sends_nothing_inside_the_60_seconds(tmp_path, restart_after):
    """The check's break: the floor cut from 60 s to 30 s was caught by no test. This one catches it (the 40 s and
    59 s restarts are inside the half of the 06:00 slot, so the slot is still taken, and only the floor holds it)."""
    path = tmp_path / "c.sqlite"
    clock = Clock()
    sent = []

    def killed(method, url, headers, data, timeout):
        sent.append(clock())
        raise Killed
    store = cl.Store(path, synchronous="OFF")
    col = collector(store, clock, killed, kalshi_ok(clock))
    col.start()
    with pytest.raises(Killed):
        col.step()
    store.close()
    clock.t = T0 + restart_after
    reddit = FakeReddit(clock, feed_answer())
    s = cl.Store(path, synchronous="OFF")
    run_for(collector(s, clock, reddit, kalshi_ok(clock)), clock, 400)
    ts = sent + [r["t"] for r in reddit.log]
    assert ts[0] == T0 and ts[1] >= T0 + 60, ts
    assert all(b - a >= 60 for a, b in zip(ts, ts[1:]))
    s.close()


def test_a_storm_of_restarts_never_sends_more_than_30_official_requests_in_any_60_seconds(tmp_path):
    """The check's other uncaught break: the official burst raised from 30 to 45. Every run is killed while its
    token request is out and restarted 0.5 s later, 41 times in 20 s (and the runs held back each read prices,
    three Kalshi requests a second, all inside the slot's first half)."""
    path = tmp_path / "c.sqlite"
    clock = Clock()
    sent = []

    def killed(method, url, headers, data, timeout):
        sent.append(clock())
        raise Killed
    for _ in range(41):
        store = cl.Store(path, synchronous="OFF")
        try:
            col = collector(store, clock, killed, kalshi_ok(clock, 0.0), CREDS)
            col.start()
            col.step()
        except Killed:
            pass
        finally:
            store.close()
        clock.sleep(0.5)
    assert 25 <= len(sent) <= cl.OAUTH_BURST == 30
    for t in sent:
        assert sum(1 for u in sent if t - 60 < u <= t) <= 30
    s = cl.Store(path, readonly=True)
    held = s.rows("SELECT COUNT(*) AS n FROM comment_fetches WHERE failure = 'held_back'")[0]["n"]
    assert held == 41 - len(sent)                                       # each held-back slot is a row
    s.close()


def test_the_page_count_of_a_slot_holds_across_a_kill(tmp_path):
    path = tmp_path / "c.sqlite"
    clock = Clock()
    feeds = []

    def reddit(method, url, headers, data, timeout):
        clock.sleep(0.2)
        if url == cl.TOKEN_URL:
            return token_answer()
        feeds.append(clock())
        n = len(feeds)
        if n == 4:
            raise Killed                                                # killed while the fourth page is out
        return listing([("t1_p%d_%d" % (n, i), T0 + 1000 - 10 * n - i) for i in range(3)], after="t1_p%d_2" % n)
    store = cl.Store(path, synchronous="OFF")
    feed(store, [entry("t1_stored", time_=datetime.fromtimestamp(T0 - 3000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))], T0 - 500)
    col = collector(store, clock, reddit, kalshi_ok(clock), CREDS)
    col.start()
    with pytest.raises(Killed):
        col.step()
    store.close()
    s = cl.Store(path, synchronous="OFF")
    run_for(collector(s, clock, reddit, kalshi_ok(clock), CREDS), clock, 30)    # restarted at once, the same slot
    assert len([t for t in feeds if t < T0 + 60]) == cl.OAUTH_MAX_PAGES == 5
    n = s.rows("SELECT COUNT(*) AS n FROM requests WHERE service = 'reddit' AND purpose = 'feed' AND slot_ts = ?", (T0,))[0]["n"]
    assert n == 5
    s.close()


# a kill at generated points, an immediate restart, and an independent model of the limits

class KillPlan:
    """Where a run is killed: just before or just after a commit, or while a request is out (its answer then never
    comes). An answer the program received stays `pending` until a commit has followed it; a kill before that loses
    it, as a real kill would, and the model then does not hold the program to it."""

    def __init__(self, rng, p_commit=0.004, p_reddit=0.12, p_kalshi=0.02):
        self.rng, self.p_commit, self.p_reddit, self.p_kalshi = rng, p_commit, p_reddit, p_kalshi
        self.pending = []
        self.kills = Counter()

    def commit_point(self):
        if self.rng.random() < self.p_commit:
            self.kills["commit"] += 1
            raise Killed


def install_kill_points(monkeypatch, holder):
    real = cl._Writing.__exit__

    def exit_(self, et, ev, tb):
        plan = holder.get("plan")
        if et is None and plan is not None:
            try:
                plan.commit_point()                                     # just before the commit: the transaction is lost
            except Killed:
                self.store.conn.execute("ROLLBACK")
                raise
        out = real(self, et, ev, tb)
        if et is None and plan is not None:
            plan.pending.clear()
            plan.commit_point()                                         # just after it
        return out
    monkeypatch.setattr(cl._Writing, "__exit__", exit_)


class KillReddit:
    """Reddit as the model sees it: every request it received (`log`) and every answer that reached the program."""

    def __init__(self, clock, plan, rng, oauth):
        self.clock, self.plan, self.rng, self.oauth = clock, plan, rng, oauth
        self.log, self.answers, self.window, self.i = [], [], [], 0

    def __call__(self, method, url, headers, data, timeout):
        t, rng = self.clock(), self.rng
        self.log.append(t)
        if rng.random() < self.plan.p_reddit:
            self.plan.kills["reddit"] += 1
            raise Killed
        if url == cl.TOKEN_URL:
            a = token_answer(rng.choice([600, 3600]))
        else:
            r = rng.random()
            if r < 0.06:
                a = resp(429, b"", {"Retry-After": str(rng.choice([5, 40, 90, 400, 2000]))} if rng.random() < 0.7 else {})
            elif r < 0.08:
                a = resp(403, b"", {})
            elif r < 0.11:
                a = resp(500, b"", {})
            elif r < 0.14:
                self.clock.sleep(timeout)
                raise TimeoutError
            elif self.oauth:
                self.i += 1
                a = listing([("t1_k%06d" % self.i, t - 3)], after=("t1_k%06d" % self.i) if rng.random() < 0.3 else None,
                            remaining=rng.choice([90, 90, 90, 0.0]), reset=rng.choice([20, 60, 300]))
            else:
                self.i += 1
                when = datetime.fromtimestamp(t - 3, UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                self.window.insert(0, atom_entry("t1_k%06d" % self.i, updated=when))
                del self.window[30:]
                a = resp(200, atom_with(*self.window), {"x-ratelimit-reset": "56"})
        took = rng.uniform(0.1, 3.0)
        if took > timeout:
            self.clock.sleep(timeout)
            raise TimeoutError
        self.clock.sleep(took)
        rec = {"at": self.clock(), "status": a.status, "headers": dict(a.headers), "feed": url != cl.TOKEN_URL, "lost": False}
        self.answers.append(rec)
        self.plan.pending.append(rec)
        return a


class KillKalshi:
    def __init__(self, clock, plan, rng):
        self.clock, self.plan, self.rng, self.log = clock, plan, rng, []

    def __call__(self, url, timeout):
        series = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["series_ticker"]
        self.log.append((self.clock(), series))
        if self.rng.random() < self.plan.p_kalshi:
            self.plan.kills["kalshi"] += 1
            raise Killed
        self.clock.sleep(self.rng.uniform(0.05, 0.6))
        return json.dumps({"markets": [market(series + "-26NOV20AAABBB-AAA", bid="%.4f" % (self.rng.randint(1, 98) / 100))],
                           "cursor": ""}).encode()


def model_wait(a, oauth):
    """How long an answer the program received binds it, by the rules alone: a 429 or 403, the time it names (Retry-After,
    else the reset header) from one second to an hour, else five minutes; an official page with under one request
    left, its reset, else five minutes."""
    h = {k.lower(): v for k, v in a["headers"].items()}

    def num(name):
        try:
            v = float(h[name])
        except (KeyError, TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def named(name):
        v = num(name)
        return None if v is None else min(max(v, 1.0), 3600.0)
    if a["status"] in (429, 403):
        return named("retry-after") or named("x-ratelimit-reset") or 300.0
    if oauth and a["feed"] and a["status"] == 200 and num("x-ratelimit-remaining") is not None and num("x-ratelimit-remaining") < 1:
        return named("x-ratelimit-reset") or 300.0
    return 0.0


def limit_breaks(reddit, kalshi, oauth):
    """Every break of a limit, judged only from what the stand-in servers received and answered, never from the store."""
    out = []
    ts = sorted(reddit.log)
    if not oauth:
        out += ["floor: %.3f then %.3f" % (a, b) for a, b in zip(ts, ts[1:]) if b - a < 60]
        out += ["silence: %.3f" % t for t in ts if 5 <= minute_of_hour(t) <= 12]
    else:
        out += ["burst: %.3f" % t for t in ts if sum(1 for u in ts if t - 60 < u <= t) > 30]
    for a in reddit.answers:
        w = 0.0 if a["lost"] else model_wait(a, oauth)
        out += ["inside a back-off from %.3f for %g s: %.3f" % (a["at"], w, t) for t in ts if a["at"] < t < a["at"] + w]
    seen = set()
    for t, series in kalshi.log:
        k = (series, int(t // 60))
        if k in seen:
            out.append("kalshi twice: %s %d" % k)
        seen.add(k)
    return out


def run_with_kills(path, holder, seed, oauth, hours):
    rng = random.Random(1000 * seed + (1 if oauth else 0))
    plan = holder["plan"] = KillPlan(rng)
    clock = Clock(T0 + rng.randint(0, 3599) + rng.random())
    reddit, kalshi = KillReddit(clock, plan, rng, oauth), KillKalshi(clock, plan, rng)
    end = clock() + hours * 3600
    quick = 0
    while clock() < end:
        store = cl.Store(path, synchronous="OFF")
        try:
            col = collector(store, clock, reddit, kalshi, CREDS if oauth else {})
            col.start()
            while clock() < end:
                col.step()
                clock.sleep(min(max(col.next_due() - clock(), 0.0), 1.0))
        except Killed:
            for a in plan.pending:
                a["lost"] = True
            plan.pending.clear()
            gap = rng.choice([0.0, 0.0, 0.0, 0.4, 1.5, 4.0, rng.uniform(30.0, 59.9), rng.uniform(30.0, 59.9), 61.0, 150.0])
            quick += gap < 60
            clock.sleep(gap)
        finally:
            store.close()
    holder["plan"] = None                                               # one clean start: every open request gets its outcome
    store = cl.Store(path, synchronous="OFF")
    collector(store, clock, reddit, kalshi, CREDS if oauth else {}).start()
    store.close()
    return plan, reddit, kalshi, quick


@pytest.mark.parametrize("oauth", [False, True])
def test_kills_at_generated_points_with_immediate_restarts_keep_every_limit(tmp_path, monkeypatch, oauth):
    holder = {"plan": None}
    install_kill_points(monkeypatch, holder)
    kills, quick, bound = Counter(), 0, 0
    for seed in range(8):
        path = tmp_path / ("k%d.sqlite" % seed)
        plan, reddit, kalshi, q = run_with_kills(path, holder, seed, oauth, hours=2)
        kills.update(plan.kills)
        quick += q
        bound += sum(1 for a in reddit.answers if not a["lost"] and model_wait(a, oauth))
        assert limit_breaks(reddit, kalshi, oauth) == [], seed
        s = cl.Store(path, readonly=True)
        assert cl.verify(s) == [], seed
        # every request that reached a server had its row, at its time, before it left
        rows = Counter(round(iso_ts(r["at"]) * 1000) for r in s.rows("SELECT at FROM requests WHERE service = 'reddit'"))
        assert not Counter(round(t * 1000) for t in reddit.log) - rows, seed
        rows = Counter((round(iso_ts(r["at"]) * 1000), r["series"]) for r in s.rows("SELECT at, series FROM requests WHERE service = 'kalshi'"))
        assert not Counter((round(t * 1000), x) for t, x in kalshi.log) - rows, seed
        # every request has exactly one outcome, appended
        assert s.rows("SELECT COUNT(*) AS n FROM requests r WHERE service = 'reddit' AND "
                      "(SELECT COUNT(*) FROM comment_fetches f WHERE f.request_id = r.id) != 1")[0]["n"] == 0, seed
        assert s.rows("SELECT COUNT(*) AS n FROM (SELECT DISTINCT series, slot_ts FROM requests WHERE service = 'kalshi') r WHERE "
                      "(SELECT COUNT(*) FROM price_fetches f WHERE f.series = r.series AND f.slot_ts = r.slot_ts "
                      "AND f.request_id IS NOT NULL) != 1")[0]["n"] == 0, seed
        pages = s.rows("SELECT MAX(n) AS m FROM (SELECT COUNT(*) AS n FROM requests WHERE service = 'reddit' AND purpose = 'feed' "
                       "GROUP BY slot_ts)")[0]["m"]
        assert pages <= (cl.OAUTH_MAX_PAGES if oauth else 2), seed
        s.close()
    assert sum(kills.values()) >= 150 and kills["reddit"] >= 20 and kills["commit"] >= 20 and kills["kalshi"] >= 20, kills
    assert quick >= 100 and bound >= 10, (quick, bound)


# ------------------------------------------------------------------------- outcome 2: coverage is Clarification 3

DAY = 1794614400                     # 2026-11-14 00:00 UTC


def test_a_full_utc_day_has_7200_price_slots_and_624_public_or_1440_official_comment_slots(tmp_path):
    for backend, interval, want in (("public", 120, 624), ("oauth", 60, 1440)):
        s = cl.Store(tmp_path / ("%s.sqlite" % backend), synchronous="OFF")
        s.save_run(started=DAY, backend=backend, comment_interval=interval, price_interval=60)
        d = cl.status(s, DAY + 86400)["days"][0]
        assert d["day"] == "2026-11-14"
        assert (d["comments"]["expected"], d["comments"]["covered_slots"], d["prices"]["expected"], d["prices"]["covered_slots"]) == (want, 0, 7200, 0)
        assert all(v["expected"] == 1440 for v in d["prices"]["series"].values())
        assert d["counts"] is False
        s.close()


def test_coverage_of_a_day_with_two_restarts_inside_every_other_minute(tmp_path):
    """Every two minutes a run starts 26 s into the even minute, takes it and dies; a second starts at once in the
    same minute and dies; nothing runs in the odd minute. Half the price minutes were read, each once."""
    path = tmp_path / "c.sqlite"
    clock = Clock(DAY + 26)
    reddit, kalshi = FakeReddit(clock, feed_answer()), FakeKalshi(clock)
    for k in range(720):
        clock.t = max(clock.t, DAY + 120 * k + 26)
        for _ in range(2):
            s = cl.Store(path, synchronous="OFF")
            col = collector(s, clock, reddit, kalshi)
            col.start()
            col.step()
            s.close()
            clock.sleep(1)
    asked = Counter((r["series"], int(r["t"] // 60)) for r in kalshi.log)
    assert len(asked) == 5 * 720 and max(asked.values()) == 1                         # never the same series twice in a minute
    s = cl.Store(path, readonly=True)
    st = cl.status(s, DAY + 86400)
    d = st["days"][0]
    assert d["day"] == "2026-11-14"
    p, c = d["prices"], d["comments"]
    assert (p["expected"], p["covered_slots"], p["failed"]) == (7200, 3600, 0)
    assert p["share"] == 0.5 and p["covered"] is False and d["counts"] is False      # covered: at least 80% of the day
    for v in p["series"].values():
        assert (v["expected"], v["covered_slots"]) == (1440, 720)
    distinct = s.rows("SELECT COUNT(*) AS n FROM (SELECT DISTINCT series, slot_ts FROM price_fetches WHERE ok = 1)")[0]["n"]
    assert distinct == p["covered_slots"]
    assert (c["expected"], c["covered_slots"]) == (624, 624) and c["covered"] is True
    text = cl.format_status(st)
    assert "covered" in text
    s.close()


def test_a_comment_slot_with_a_possible_miss_mark_is_not_covered(tmp_path):
    def when(n):
        return datetime.fromtimestamp(T0 + 60 * n, UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def answer(n, t, m, u):
        if n in (3, 7):                                                  # a feed that starts after the newest stored
            return resp(200, atom_with(atom_entry("t1_jump%d" % n, updated=when(n))))
        return resp(200, atom_with(atom_entry("t1_c%d" % n, updated=when(n)), atom_entry("t1_c%d" % (n - 1), updated=when(n - 1))))
    col, store, *_ = run_store(tmp_path, 3600, answer=answer)
    c = cl.status(store, T0 + 3600)["days"][0]["comments"]
    marks = store.rows("SELECT COUNT(DISTINCT slot_ts) AS n FROM comment_events WHERE kind = 'possible_miss'")[0]["n"]
    assert marks >= 2
    assert marks == 2 and c["made"] == 26 and c["failed"] == 0 and c["miss_uncovered"] == marks and c["covered_slots"] == 26 - marks
    assert c["share"] == pytest.approx((26 - marks) / 26)


def test_covered_never_exceeds_expected_whatever_the_rows_say(tmp_path):
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    s.save_run(started=T0, backend="public", comment_interval=120, price_interval=60)
    for i in range(3):                                                  # the same minute read three times
        s.save_price_fetch(series="KXNBAGAME", slot=T0, at=T0 + i, failure=None, markets=[], seen=0, bad_rows=0, body=None)
    s.save_price_fetch(series="KXNBAGAME", slot=T0 + 3600 * 5, at=T0 + 5, failure=None, markets=[], seen=0, bad_rows=0, body=None)  # a slot after now
    s.save_price_fetch(series="KXOTHER", slot=T0, at=T0 + 6, failure=None, markets=[], seen=0, bad_rows=0, body=None)        # not one of the five
    feed(s, [entry("c1")], T0 + 7, slot=T0)
    feed(s, [entry("c2")], T0 + 8, slot=T0)
    feed(s, [entry("c3")], T0 + 9, slot=T0 + 360)                      # minute 6: in the silence, not a slot of the plan
    st = cl.status(s, T0 + 120)
    for d in st["days"]:
        assert d["prices"]["covered_slots"] <= d["prices"]["expected"] and d["comments"]["covered_slots"] <= d["comments"]["expected"]
        for v in d["prices"]["series"].values():
            assert v["covered_slots"] <= v["expected"]
    d = st["days"][0]
    assert d["prices"]["covered_slots"] == 1 and d["prices"]["series"]["KXNBAGAME"]["covered_slots"] == 1
    assert d["comments"]["expected"] == 1 and d["comments"]["covered_slots"] == 1
    s.close()


# ------------------------------------------- outcome 3: one slow or hostile answer costs nothing but its own slot

class LocalServer:
    """A stand-in server on 127.0.0.1 that behaves one way. "ok" answers at once with `status`, `extra` headers and
    `body`; the others misbehave. Nothing here is reachable from outside the machine."""

    def __init__(self, behaviour, body=b'{"markets": [], "cursor": ""}', status=200, extra=b""):
        self.behaviour, self.body, self.status, self.extra = behaviour, body, status, extra
        self.requests, self.hits, self.stop = [], 0, threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, name="test-server", daemon=True).start()

    def url(self, path="/trade-api/v2/markets", scheme="http"):
        return "%s://127.0.0.1:%d%s" % (scheme, self.port, path)

    def _serve(self):
        while not self.stop.is_set():
            try:
                c, _ = self.sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            self.hits += 1
            threading.Thread(target=self._handle, args=(c,), name="test-server-conn", daemon=True).start()

    def _hold(self, c):
        while not self.stop.is_set():
            try:
                if c.recv(65536) == b"":
                    return
            except (socket.timeout, TimeoutError):
                continue

    def _slowly(self, c, data, every):
        for i in range(len(data)):
            if self.stop.wait(every):
                return
            c.sendall(data[i:i + 1])

    def _handle(self, c):
        try:
            c.settimeout(0.05)
            head, t = b"", time.monotonic()
            while b"\r\n\r\n" not in head and time.monotonic() - t < 1.0 and not self.stop.is_set():
                try:
                    chunk = c.recv(65536)
                except (socket.timeout, TimeoutError):
                    continue
                if not chunk:
                    break
                head += chunk
            self.requests.append(head)
            b = self.behaviour
            if b == "ok":
                c.sendall(b"HTTP/1.1 %d X\r\nContent-Length: %d\r\n%s\r\n" % (self.status, len(self.body), self.extra) + self.body)
            elif b == "silent":
                self._hold(c)
            elif b == "trickle_headers":
                c.sendall(b"HTTP/1.1 200 OK\r\n")
                self._slowly(c, b"X-Slow: " + b"a" * 2000 + b"\r\n\r\n", 0.05)
            elif b == "trickle_body":
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
                self._slowly(c, b"x" * 100000, 0.05)
            elif b == "endless":
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
                c.settimeout(2.0)
                sent = 0
                while sent < 6 * 1024 * 1024 and not self.stop.is_set():
                    c.sendall(b"x" * 65536)
                    sent += 65536
                c.settimeout(0.05)
                self._hold(c)
            elif b == "endless_slow":
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
                while not self.stop.wait(0.02):
                    c.sendall(b"y" * 1024)
        except OSError:
            pass
        finally:
            c.close()

    def close(self):
        self.stop.set()
        self.sock.close()


def local_hosts_allowed(monkeypatch):
    """Let the real transports reach the stand-in servers on 127.0.0.1 (they refuse any host but their own otherwise)."""
    local = {("http", "127.0.0.1"), ("https", "127.0.0.1")}
    monkeypatch.setattr(cl, "_ALLOWED", set(cl._ALLOWED) | local)
    monkeypatch.setattr(cl, "_KALSHI_ALLOWED", local, raising=False)


def through(service, url, timeout):
    if service == "reddit":
        return cl.reddit_get("GET", url, {"User-Agent": cl.USER_AGENT, "Accept": "application/atom+xml"}, None, timeout)
    return cl.kalshi_get(url, timeout)


def request_threads():
    return {t for t in threading.enumerate() if t.name.startswith("chatter-get") and t.is_alive()}


@pytest.mark.parametrize("service", ["reddit", "kalshi"])
@pytest.mark.parametrize("kind", ["silent", "tls_silent", "trickle_headers", "trickle_body", "endless", "endless_slow"])
def test_a_hostile_server_cannot_keep_a_request_past_its_whole_deadline(monkeypatch, service, kind):
    local_hosts_allowed(monkeypatch)
    srv = LocalServer("silent" if kind == "tls_silent" else kind)
    before = request_threads()
    limit = 1.0
    try:
        t = time.monotonic()
        try:
            r = through(service, srv.url(scheme="https" if kind == "tls_silent" else "http"), limit)
            outcome = "answer"
        except Exception as e:
            r, outcome = None, type(e).__name__
        elapsed = time.monotonic() - t
        assert elapsed <= limit + 0.3, (kind, outcome, elapsed)
        if kind == "endless":
            assert (outcome == "answer" and r.truncated and len(r.body) == cl.MAX_BODY) if service == "reddit" else outcome != "answer"
        else:
            assert outcome != "answer", kind
        while request_threads() - before and time.monotonic() - t < limit + 2.0:
            time.sleep(0.02)
        assert not request_threads() - before, "a request thread outlived its deadline by 2 s"
    finally:
        srv.close()


def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    import datetime as dtm
    import ipaddress
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dtm.datetime.now(dtm.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dtm.timedelta(days=1))
            .not_valid_after(now + dtm.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cf, kf = tmp_path / "cert.pem", tmp_path / "key.pem"
    cf.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kf.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cf, kf


@pytest.mark.parametrize("kind", ["trickle_headers", "trickle_body"])
def test_a_trickling_answer_over_tls_is_cut_at_the_deadline_too(tmp_path, monkeypatch, kind):
    import ssl
    local_hosts_allowed(monkeypatch)
    cf, kf = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cf, kf)
    client_ctx = ssl.create_default_context(cafile=str(cf))
    monkeypatch.setattr(cl, "_SSL_CONTEXT", client_ctx, raising=False)
    srv = LocalServer(kind)
    plain_handle = srv._handle

    def tls_handle(c):
        try:
            c.settimeout(2.0)
            c = server_ctx.wrap_socket(c, server_side=True)
        except OSError:
            c.close()
            return
        plain_handle(c)
    srv._handle = tls_handle
    before = request_threads()
    try:
        t = time.monotonic()
        with pytest.raises(Exception):
            through("reddit", srv.url(path="/x", scheme="https"), 1.0)
        assert time.monotonic() - t <= 1.3
        while request_threads() - before and time.monotonic() - t < 3.0:
            time.sleep(0.02)
        assert not request_threads() - before
        assert srv.requests and srv.requests[0].startswith(b"GET /x HTTP/1.1")       # it was TLS all the way through
    finally:
        srv.close()


def test_abandoned_requests_of_one_service_never_stop_the_other():
    stuck = threading.Event()
    try:
        for service, other in (("reddit", "kalshi"), ("kalshi", "reddit")):
            for _ in range(cl.MAX_ABANDONED):
                with pytest.raises(TimeoutError):
                    cl.bounded_call(lambda: stuck.wait(10), 0.05, service)
            with pytest.raises(TimeoutError):
                cl.bounded_call(lambda: 1, 1, service)                  # this one is full: none started
            assert cl.bounded_call(lambda: 7, 1, other) == 7            # the other is not
            if service == "reddit":
                stuck.set()
                time.sleep(0.2)
                stuck.clear()
    finally:
        stuck.set()
        time.sleep(0.2)


def test_trickling_answers_of_one_service_never_cost_a_request_of_the_other(monkeypatch):
    """The check's finding: four trickling Reddit answers left every later price minute failing as a timeout."""
    local_hosts_allowed(monkeypatch)
    slow, good = LocalServer("trickle_body"), LocalServer("ok")
    try:
        for service, other in (("reddit", "kalshi"), ("kalshi", "reddit")):
            for _ in range(cl.MAX_ABANDONED + 2):
                with pytest.raises(Exception):
                    through(service, slow.url(), 0.3)
            r = through(other, good.url(), 2.0)
            assert (r if other == "kalshi" else r.body) == good.body
            assert (through(service, good.url(), 2.0) if service == "kalshi" else through(service, good.url(), 2.0).body) == good.body
    finally:
        slow.close()
        good.close()


def test_no_request_uses_a_proxy_from_the_environment_follows_a_redirect_or_sends_more_than_the_fixed_headers(monkeypatch):
    local_hosts_allowed(monkeypatch)
    proxy, target, elsewhere = LocalServer("ok"), LocalServer("ok"), LocalServer("ok")
    redirecting = LocalServer("ok", status=302, body=b"", extra=b"Location: %s\r\n" % elsewhere.url().encode())
    for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(k, proxy.url("/"))
    for k in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(k, raising=False)
    try:
        assert through("reddit", target.url(), 2.0).status == 200
        assert through("kalshi", target.url(), 2.0) == target.body
        assert target.hits == 2 and proxy.hits == 0
        assert through("reddit", redirecting.url(), 2.0).status == 302
        with pytest.raises(kd.HttpError) as e:
            through("kalshi", redirecting.url(), 2.0)
        assert e.value.status == 302 and elsewhere.hits == 0 and proxy.hits == 0
        for head in target.requests:
            names = {line.split(b":", 1)[0].strip().lower() for line in head.split(b"\r\n")[1:] if b":" in line}
            assert names <= {b"host", b"user-agent", b"accept"}, names
    finally:
        for s in (proxy, target, elsewhere, redirecting):
            s.close()


def test_a_kalshi_answer_larger_than_the_limit_is_a_failed_fetch(tmp_path):
    def kanswer(url, timeout):
        raise cl.TooLarge(b"x" * 100)
    clock = Clock()
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    run_for(collector(s, clock, FakeReddit(clock, feed_answer()), kanswer), clock, 60)
    assert {r["failure"] for r in s.rows("SELECT failure FROM price_fetches")} == {"too_large"}
    s.close()


# --------------------------------------------- outcome 4: memory stays bounded wherever a stored blob is read back

def stream_bomb(n_bytes):
    """A small blob that inflates to `n_bytes` zeros, made a megabyte at a time (the whole is never held)."""
    co, chunk = zlib.compressobj(9), b"\x00" * (1 << 20)
    parts = [co.compress(chunk) for _ in range(n_bytes >> 20)]
    return b"".join(parts) + co.flush()


def test_check_never_inflates_past_max_body_whatever_a_row_states_and_goes_on(tmp_path, monkeypatch):
    s = cl.Store(tmp_path / "c.sqlite", synchronous="OFF")
    for i in range(3):
        feed(s, [entry("c%d" % i)], T0 + 120 * i, body=b"<feed>real %d</feed>" % i)
    bomb = stream_bomb(256 << 20)                                      # a quarter of a megabyte that would be 256 MB
    assert len(bomb) < 1 << 20
    trig = dict(s.conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall())
    s.conn.execute("DROP TRIGGER no_update_payloads")
    s.conn.execute("UPDATE payloads SET body = ?, size = ? WHERE id = 1", (bomb, 1 << 30))          # states a gigabyte
    s.conn.execute("UPDATE payloads SET body = ?, size = ? WHERE id = 2", (bomb, cl.MAX_BODY))       # states the most an answer can be
    s.conn.execute("DROP TRIGGER no_update_comment_fetches")
    s.conn.execute("UPDATE comment_fetches SET new_versions = new_versions + 1 WHERE id = 3")       # damage of another kind after them
    for name in ("no_update_payloads", "no_update_comment_fetches"):                               # the layout itself put back
        s.conn.execute(trig[name])
    asked, real = [], zlib.decompressobj

    class Watched:
        """zlib's decompressor, refusing to be asked for more than MAX_BODY + 1 bytes (or for no limit at all)."""
        def __init__(self, *a):
            self._d = real(*a)

        def decompress(self, data, max_length=0):
            asked.append(max_length)
            if max_length <= 0 or max_length > cl.MAX_BODY + 1:
                raise AssertionError("asked to inflate up to %d bytes" % max_length)
            return self._d.decompress(data, max_length)

        def __getattr__(self, name):
            return getattr(self._d, name)
    monkeypatch.setattr(cl.zlib, "decompressobj", Watched)
    tracemalloc.start()
    try:
        problems = cl.verify(s)
        with pytest.raises(cl.StoreError):
            s.payload_bytes(1)
        with pytest.raises(cl.StoreError):
            s.payload_bytes(2)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 300_000_000 and peak < 3 * cl.MAX_BODY + 20_000_000
    assert any("payload: 2 raw answers" in p for p in problems), problems
    assert any("new_versions" in p for p in problems), problems
    assert all(0 < n <= cl.MAX_BODY + 1 for n in asked)
    s.close()
    out = []
    assert cl.main(["check", "--db", str(tmp_path / "c.sqlite")], out=out.append) == 1
    assert any("payload: 2 raw answers" in line for line in out) and any("new_versions" in line for line in out), out
    s = cl.Store(tmp_path / "big.sqlite", synchronous="OFF")                                        # nor is one ever stored
    big = cl.Fetch(at=T0, slot=T0, backend="public", purpose="feed", page=1, status=200, failure="too_large",
                   body=b"x" * (cl.MAX_BODY + 1), entries=None)
    with pytest.raises(ValueError):
        s.save_comment_slot([big], [])
    assert s.rows("SELECT COUNT(*) AS n FROM payloads")[0]["n"] == 0
    s.close()


# ------------------------------------------------------------------- outcome 5: Retry-After as an HTTP date

@pytest.mark.parametrize("value,expected", [("future", 200), ("past", 300), ("Someday, 99 Foo 2026 25:61:61 GMT", 300),
                                            ("3000", 3000), ("7200", 3600)])
def test_retry_after_as_an_http_date_is_honoured_and_a_named_wait_up_to_an_hour(tmp_path, value, expected):
    count = {"n": 0}

    def answer(n, t, m, u):
        count["n"] += 1
        if count["n"] == 1:
            v = {"future": email.utils.formatdate(t + 0.2 + 200, usegmt=True),
                 "past": email.utils.formatdate(t - 3600, usegmt=True)}.get(value, value)
            return resp(429, b"", {"Retry-After": v})
        return resp(200, atom_with(atom_entry("t1_ok%d" % n)))
    col, store, reddit, _, clock, _ = make(tmp_path, answer=answer)
    run_for(col, clock, 4000)
    first, second = reddit.log[0]["t"], reddit.log[1]["t"]
    ev = store.rows("SELECT at, until FROM comment_events WHERE kind = 'backoff'")
    assert len(ev) == 1 and abs(iso_ts(ev[0]["until"]) - (first + 0.2 + expected)) <= 1.0, ev
    g = -(-iso_ts(ev[0]["until"]) // 120) * 120
    while 5 <= minute_of_hour(g) <= 12:
        g += 120
    assert second == g


def test_an_official_reset_the_service_names_is_honoured_up_to_an_hour(tmp_path):
    def pages(n, url):
        return listing([("t1_n%03d" % n, T0 + n * 60 - 3)], remaining=0.0, reset=2000) if n == 2 else \
            listing([("t1_n%03d" % n, T0 + n * 60 - 3)], remaining=90, reset=60)
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    run_for(col, clock, 2400)
    feeds = [r["t"] for r in reddit.log if r["url"] != cl.TOKEN_URL]
    assert feeds[2] - feeds[1] >= 2000


# ------------------------------------------------------------------------------------- outcome 6: the smaller ones

@pytest.mark.parametrize("name", ["utf16-bom", "utf16-no-bom", "utf8-bom", "latin1-declared", "latin1-bytes", "utf16-entity-bomb",
                                  "utf16-doctype"])
def test_a_feed_must_be_utf8_without_a_byte_order_mark(name):
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><feed xmlns="http://www.w3.org/2005/Atom">'
            + atom_entry(body="&c;") + '</feed>')
    data = {"utf16-bom": ATOM_FIXTURE.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16"),
            "utf16-no-bom": ATOM_FIXTURE.replace('encoding="UTF-8"', 'encoding="UTF-16LE"').encode("utf-16-le"),
            "utf8-bom": b"\xef\xbb\xbf" + ATOM_FIXTURE.encode(),
            "latin1-declared": ATOM_FIXTURE.replace('encoding="UTF-8"', 'encoding="ISO-8859-1"').encode(),
            "latin1-bytes": atom_with(atom_entry(body="caf\xe9")).decode().encode("latin-1"),
            "utf16-entity-bomb": bomb.encode("utf-16"),
            "utf16-doctype": ('<!DOCTYPE feed SYSTEM "file:///etc/passwd">' + ATOM_FIXTURE[38:]).encode("utf-16")}[name]
    t = time.time()
    with pytest.raises(cl.Unparseable):
        cl.parse_atom(data)
    assert time.time() - t < 1


def test_the_parser_itself_refuses_entity_and_doctype_declarations(monkeypatch):
    never = re.compile(r"(?!)")
    monkeypatch.setattr(cl, "_DOCTYPE", re.compile(rb"(?!)"))           # with the text check gone, the parser still refuses
    monkeypatch.setattr(cl, "_DOCTYPE_TEXT", never, raising=False)
    for doc in ('<!DOCTYPE feed [<!ENTITY a "x">]><feed xmlns="http://www.w3.org/2005/Atom">' + atom_entry(body="&a;") + "</feed>",
                '<!DOCTYPE feed><feed xmlns="http://www.w3.org/2005/Atom">' + atom_entry() + "</feed>",
                '<!DOCTYPE feed [<!ENTITY % p "x">]><feed xmlns="http://www.w3.org/2005/Atom"/>'):
        with pytest.raises(cl.Unparseable):
            cl.parse_atom(doc.encode())
    entries, bad = cl.parse_atom(ATOM_FIXTURE.encode())                 # the real layout still parses, exactly as before
    assert len(entries) == 3 and entries[0].body == '<!-- SC_OFF --><div class="md"><p>xx-x xxx xxx xxx</p> </div><!-- SC_ON -->'


@pytest.mark.parametrize("after,ok", [("t1_abc123", True), ("t1_" + "a" * 61, True), ("t1_" + "a" * 62, False), ("t1_\ud800", False),
                                      ("t1 abc", False), ("t1_é", False), ("t1_a&b=c", False), ("t1_a/../b", False), (7, False), (None, None), ("", None)])
def test_an_official_cursor_must_match_a_strict_pattern(after, ok):
    doc = json.loads(LISTING_FIXTURE)
    doc["data"]["after"] = after
    data = json.dumps(doc).encode()
    if ok is False:
        with pytest.raises(cl.Unparseable):
            cl.parse_listing(data)
    else:
        assert cl.parse_listing(data)[2] == (after if ok else None)


def test_a_lone_surrogate_cursor_is_a_bad_answer_and_the_run_goes_on(tmp_path):
    def pages(n, url):
        body = listing([("t1_n%03d" % n, T0 + 3000 + n)], after="PLACEHOLDER").body.replace(b'"PLACEHOLDER"', b'"t1_\\ud800"')
        return resp(200, body)
    col, store, reddit, _, clock, _ = make(tmp_path, answer=oauth_answer(pages), env=CREDS)
    feed(store, [entry("t1_stored", time_="2026-11-13T05:00:00Z")], T0 - 500)
    run_for(col, clock, 300)                                             # does not raise
    rows = store.rows("SELECT failure FROM comment_fetches WHERE purpose = 'feed' AND id > 1")
    assert len(rows) == 5 and {r["failure"] for r in rows} == {"unparseable"}


class GetRaises(dict):
    def get(self, *a, **k):
        raise RuntimeError("SECRET-TEXT-OF-THE-ERROR")


def test_an_exception_while_handling_a_slot_is_a_failed_slot_with_its_class_name_and_the_run_goes_on(tmp_path, monkeypatch):
    def answer(n, t, m, u):
        r = resp(200, atom_with(atom_entry("t1_c%d" % n)))
        if n == 2:
            r.headers = GetRaises()
        return r
    calls = {"n": 0}
    real_quote = cl.Collector._quote

    def quote(self, m):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("SECRET-TEXT-OF-THE-ERROR")
        return real_quote(self, m)
    monkeypatch.setattr(cl.Collector, "_quote", quote)
    col, store, reddit, kalshi, clock, _ = make(tmp_path, answer=answer)
    run_for(col, clock, 1200)
    f = store.rows("SELECT slot_ts, failure, request_id FROM comment_fetches ORDER BY id")
    assert [r["failure"] for r in f][:3] == [None, "error_RuntimeError", None] and f[1]["request_id"] is not None
    assert len(reddit.log) == 6                                          # 06:00 to 06:18, less the silence: the run went on
    p = store.rows("SELECT series, failure FROM price_fetches ORDER BY id")
    assert p[0]["failure"] == "error_RuntimeError" and all(r["failure"] is None for r in p[1:]) and len(p) == 100
    store.close()
    blob = b"".join(x.read_bytes() for x in tmp_path.glob("c.sqlite*"))
    assert b"SECRET" not in blob


def test_an_error_of_the_store_itself_still_stops_the_run_loudly(tmp_path, monkeypatch):
    def broken(self, **kw):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(cl.Store, "save_price_fetch", broken)
    code, out, _ = cli(tmp_path, ["run", "--db", str(tmp_path / "c.sqlite"), "--for-minutes", "5"])
    assert code == 1 and "could not be written" in out


def test_run_leaves_no_lock_file_beside_a_file_it_refused_and_keeps_one_it_did_not_make(tmp_path):
    p = tmp_path / "c.sqlite"
    cl.Store(p).close()
    c = sqlite3.connect(str(p))
    c.execute("DROP TRIGGER no_update_comment_versions")                # a valid header, the wrong layout
    c.commit()
    c.close()
    code, out, _ = cli(tmp_path, ["run", "--db", str(p), "--for-minutes", "1"])
    assert code == 1 and "not a chatter store" in out
    assert not Path(str(p) + cl.LOCK_SUFFIX).exists()
    with pytest.raises(cl.StoreError):
        cl.Store(p, lock=True)
    assert not Path(str(p) + cl.LOCK_SUFFIX).exists()
    Path(str(p) + cl.LOCK_SUFFIX).write_bytes(b"")                      # one that was there before is not ours to remove
    with pytest.raises(cl.StoreError):
        cl.Store(p, lock=True)
    assert Path(str(p) + cl.LOCK_SUFFIX).exists()


def test_check_finds_requests_and_outcomes_that_do_not_belong_together(tmp_path):
    for name, sql in (("an outcome of nothing", "INSERT INTO comment_fetches (fetched_at, slot_ts, backend, purpose, page, ok, failure, "
                                                 "request_id) VALUES ('2099-01-01T00:00:00.000Z', 0, 'public', 'feed', 1, 0, 'unknown', 999)"),
                      ("two outcomes of one", "INSERT INTO comment_fetches (fetched_at, slot_ts, backend, purpose, page, ok, failure, "
                                              "request_id) SELECT '2099-01-01T00:00:00.000Z', slot_ts, backend, purpose, page, 0, 'unknown', "
                                              "request_id FROM comment_fetches WHERE request_id IS NOT NULL LIMIT 1"),
                      ("an outcome of another slot's request", "INSERT INTO comment_fetches (fetched_at, slot_ts, backend, purpose, page, "
                                                               "ok, failure, request_id) SELECT '2099-01-01T00:00:00.000Z', slot_ts + 120, "
                                                               "backend, purpose, page, 0, 'unknown', MAX(id) FROM requests "
                                                               "WHERE service = 'reddit'"),
                      ("a price outcome of nothing", "INSERT INTO price_fetches (fetched_at, slot_ts, series, ok, failure, request_id) "
                                                     "VALUES ('2099-01-01T00:00:00.000Z', 0, 'KXNBAGAME', 0, 'unknown', 999)")):
        sub = tmp_path / name.replace(" ", "_")
        sub.mkdir()
        col, store, *_ = make(sub)
        run_for(col, col.clock, 600)
        assert cl.verify(store) == []
        store.conn.execute(sql)
        assert any("request" in p for p in cl.verify(store)), name
        store.close()
