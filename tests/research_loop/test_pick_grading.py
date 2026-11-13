"""Tests for research_loop/pick_grading.py (rev 3). Every store is built here, in tmp_path; nothing touches the network,
the local model, Reddit, the real pick store, the real grading store or the real secrets file (the module's defaults are
pointed into tmp_path for every test)."""
import ast
import importlib.util
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from valedia_trader.data_and_fees import pick_lists as pl
from valedia_trader.research_loop import pick_grading as pg
from valedia_trader.research_loop import sports_lines as sl
from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh
from valedia_trader.venues import odds_api as oa

UTC = timezone.utc
KEYS = {"ODDS_API_KEY": "PAID-KEY-SECRET-0001", "ODDS_API_KEY_FREE": "FREE-KEY-SECRET-0002"}
D = date(2026, 11, 13)
TITLE = "Pick of the Day - 11/13/26 (Friday)"
SPORT = {lg: sport for lg, (sport, _) in sl.LEAGUES.items()}
SERIES = {lg: series for lg, (_, series) in sl.LEAGUES.items()}
REAL_DEFAULTS = (getattr(pg, "DEFAULT_PICKS", None), getattr(pg, "DEFAULT_STORE", None))


@pytest.fixture(autouse=True)
def _defaults_in_tmp(tmp_path_factory, monkeypatch):
    """The real pick store and the real grading store are never read: the defaults point into a temporary folder."""
    d = tmp_path_factory.mktemp("defaults")
    monkeypatch.setattr(pg, "DEFAULT_PICKS", d / "picks.sqlite", raising=False)
    monkeypatch.setattr(pg, "DEFAULT_STORE", d / "grading", raising=False)


def T(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def ts(text):
    return int(T(text).timestamp())


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ts(x):
    return iso(datetime.fromtimestamp(x, UTC))


def plus(text, seconds):
    return iso(T(text) + timedelta(seconds=seconds))


class Clock:
    def __init__(self, now="2026-11-13T16:00:00Z"):
        self.now = T(now)

    def set(self, text):
        self.now = T(text)

    def __call__(self):
        return self.now


def team_names(n, suffix="s"):
    """Distinct invented teams, built in code: (city, nickname)."""
    syl = ["ka", "lo", "mi", "ra", "te", "su", "no", "vi", "do", "pe"]
    out = [((a + b + "ton").title(), (b + a + "r" + suffix).title()) for a in syl for b in syl if a != b]
    return out[:n]


# ---------------------------------------------------------------- the pick store, built by the tests

class PickWorld:
    """A pick store made through pick_lists.Store, rows written in time order as ingest would."""

    def __init__(self):
        self.items = []
        self.n = 0
        self.unparsed = set()                  # comments whose versions get no parse at build (see `parse_later`)

    def _add(self, when, kind, data):
        self.n += 1
        self.items.append((when, self.n, kind, data))

    def thread(self, tid="t1", title=TITLE, seen="2026-11-13T12:00:00Z"):
        self._add(seen, "thread", {"tid": tid, "title": title})
        return tid

    def comment(self, cid, author, versions, tid="t1", role="user"):
        """versions: [(seen_at, fields or None, odds text or None[, deleted])]; fields None is not a pick."""
        for i, (seen, fields, odds, *rest) in enumerate(versions, 1):
            self._add(seen, "version", {"cid": cid, "tid": tid, "author": author, "version": i, "fields": fields,
                                        "odds": odds, "role": role, "deleted": bool(rest and rest[0]),
                                        "first": i == 1})

    def build(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        store = pl.Store(path)
        items = sorted(self.items, key=lambda x: (x[0], x[1]))
        try:
            store.conn.execute("PRAGMA synchronous=OFF")
            with store.writing(items[0][0]):                     # one transaction, rows in time order
                pid = store.payload("thread", "t", "{}", items[0][0])
                for when, _, kind, d in items:
                    if kind == "thread":
                        store.conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)",
                                           (d["tid"], "sub", "u", d["title"], "bot", None, None, when))
                        continue
                    if d["first"]:
                        store.conn.execute("INSERT INTO comments VALUES (?,?,?,?,?,?)",
                                           (d["cid"], d["tid"], "u", when, None, None))
                    f = d["fields"]
                    body = ("%s %s" % ((f or {}).get("selection", ""), d["odds"] or "")).strip() if f else \
                        "good luck everyone %s" % d["cid"]
                    vid = store.conn.execute(
                        "INSERT INTO comment_versions (comment_id, version, author, body, body_enc, body_sha256, seen_at, "
                        "role, deleted, backend, payload_id, change) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (d["cid"], d["version"], d["author"], body, "utf8", pl.sha256_text(body), when, d["role"],
                         int(d["deleted"]), "oauth", pid, "first" if d["first"] else "edit")).lastrowid
                    if d["cid"] in self.unparsed:
                        continue
                    dec = pl.decimal_from_odds(d["odds"]) if d["odds"] else None
                    parse = store.conn.execute(
                        "INSERT INTO parses (version_id, prompt_hash, model, parsed_at, status, reason, is_pick, "
                        "decimal_odds, raw_output) VALUES (?,?,?,?,?,?,?,?,?)",
                        (vid, pl.PROMPT_HASH, "m", when, "ok", None, 1 if f else 0, dec, "{}")).lastrowid
                    for k, v in (f or {}).items():
                        val = json.dumps(v) if k == "event" else (None if v is None else str(v))
                        store.conn.execute("INSERT INTO parse_fields (parse_id, field, value, phrase, kept, reason) "
                                           "VALUES (?,?,?,?,?,?)", (parse, k, val, "p", 1, None))
        finally:
            store.close()
        return path

    def parse_later(self, path, cid, when):
        """The parse of `cid`'s versions, stored at `when` (a parse that came after the comment)."""
        db = sqlite3.connect(path)
        try:
            for _, _, kind, d in sorted(self.items, key=lambda x: (x[0], x[1])):
                if kind != "version" or d["cid"] != cid:
                    continue
                vid = db.execute("SELECT id FROM comment_versions WHERE comment_id = ? AND version = ?",
                                 (cid, d["version"])).fetchone()[0]
                f = d["fields"]
                dec = pl.decimal_from_odds(d["odds"]) if d["odds"] else None
                parse = db.execute("INSERT INTO parses (version_id, prompt_hash, model, parsed_at, status, reason, is_pick, "
                                   "decimal_odds, raw_output) VALUES (?,?,?,?,?,?,?,?,?)",
                                   (vid, pl.PROMPT_HASH, "m", when, "ok", None, 1 if f else 0, dec, "{}")).lastrowid
                for k, v in (f or {}).items():
                    val = json.dumps(v) if k == "event" else (None if v is None else str(v))
                    db.execute("INSERT INTO parse_fields (parse_id, field, value, phrase, kept, reason) "
                               "VALUES (?,?,?,?,?,?)", (parse, k, val, "p", 1, None))
            db.commit()
        finally:
            db.close()


def ml(team, league=None):
    return {"market": "moneyline", "selection": team + " ML", **({"league": league} if league else {})}


# ---------------------------------------------------------------- fakes for The Odds API and Kalshi

class OddsTransport:
    def __init__(self):
        self.events, self.hist, self.scores, self.calls, self.used, self.fail = {}, {}, {}, [], 0, set()

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        if any(s in url for s in self.fail):
            raise OSError("down")
        path = url.split("api.the-odds-api.com", 1)[1]
        parts = path.strip("/").split("/")
        if parts[1] == "historical":
            sport = parts[3]
            body = self.hist.get((sport, params["date"]), {"timestamp": params["date"], "data": []})
            cost = 1 if body["data"] else 0
        elif parts[-1] == "scores":
            body, cost = self.scores.get(parts[2], []), 2 if params.get("daysFrom") else 1
        else:
            body, cost = self.events.get(parts[2], []), 0
        self.used += cost
        return 200, {"x-requests-last": str(cost), "x-requests-used": str(self.used),
                     "x-requests-remaining": str(500 - self.used)}, body

    def kinds(self):
        out = []
        for url, _ in self.calls:
            out.append("historical" if "/historical/" in url else "scores" if "/scores" in url else "events")
        return out


def odds_client(transport):
    return oa.OddsApiClient(transport, key_reader=lambda name: KEYS[name], sleep=lambda s: None, max_retries=0)


class KalshiTransport:
    def __init__(self):
        self.markets, self.open, self.candles, self.urls, self.endless = {}, {}, {}, [], False
        self.kevents, self.fail = {}, set()

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        path = parts.path.split("/trade-api/v2", 1)[1]
        q = dict(urllib.parse.parse_qsl(parts.query))
        if q.get("series_ticker") in self.fail:
            raise kd.HttpError(404, url)
        if path == "/historical/cutoff":
            return json.dumps({"market_settled_ts": "2026-01-01T00:00:00Z"}).encode()
        if path == "/events":
            rows = self.kevents.get(q.get("series_ticker"), [])
            return json.dumps({"events": rows, "milestones": [],
                               "cursor": "c%d" % len(self.urls) if self.endless else ""}).encode()
        if path == "/markets" and q.get("status") == "open":
            rows = self.open.get(q.get("series_ticker"), [])
            return json.dumps({"markets": rows, "cursor": "c%d" % len(self.urls) if self.endless else ""}).encode()
        if path == "/markets":
            return json.dumps({"markets": self.markets.get(q.get("series_ticker"), []), "cursor": ""}).encode()
        m = re.fullmatch(r"/series/([^/]+)/markets/([^/]+)/candlesticks", path)
        if m:
            lo, hi = int(q["start_ts"]), int(q["end_ts"])
            rows = [c for c in self.candles.get(m.group(2), []) if lo <= c["end_period_ts"] <= hi]
            return json.dumps({"candlesticks": rows}).encode()
        raise kd.HttpError(404, url)

    def result_urls(self):
        """Every request other than an events listing (settled markets, candles, the cutoff)."""
        return [u for u in self.urls if "status=open" not in u and "/events?" not in u]


def kalshi_client(transport):
    return kh.HistoryClient(fetch=transport, sleep=lambda s: None, clock=lambda: 0.0)


def ev(gid, home, away, start, sport="basketball_nba"):
    return {"id": gid, "sport_key": sport, "commence_time": start, "home_team": home, "away_team": away}


def kmarket(event, code, city, result, expected):
    return {"ticker": event + "-" + code, "event_ticker": event, "title": city + " wins", "yes_sub_title": city,
            "result": result, "status": "finalized", "close_time": plus(expected, 3600),
            "settlement_ts": plus(expected, 3600), "expected_expiration_time": expected, "occurrence_datetime": expected}


def kopen(event, code, name):
    return {"ticker": event + "-" + code, "event_ticker": event, "title": name + " wins", "yes_sub_title": name,
            "no_sub_title": name, "status": "active", "result": ""}


def kevent(league, day, a, b):
    """One event of Kalshi's events list, in its real shape: a = (code, Kalshi name), b likewise."""
    series = SERIES[league]
    return {"event_ticker": "%s-%s%s%s" % (series, day, a[0], b[0]), "series_ticker": series,
            "title": "%s vs %s" % (a[1], b[1]), "sub_title": "%s vs %s (Nov 13)" % (a[0], b[0]),
            "category": "Sports", "mutually_exclusive": True, "collateral_return_type": "", "strike_period": "",
            "product_metadata": {"competition_scope": "Game"}, "settlement_sources": [], "exchange_index": 0,
            "last_updated_ts": "2026-11-13T00:00:00Z"}


def kcandle(end_ts, bid, ask):
    return {"end_period_ts": end_ts, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}, "price": {}, "volume": 1}


class Env:
    """One grading folder with its fakes."""

    def __init__(self, tmp_path, world=None):
        self.root, self.picks = tmp_path / "grading", tmp_path / "picks.sqlite"
        self.audit_dir = tmp_path / "audit"
        self.clock, self.odds, self.kalshi = Clock(), OddsTransport(), KalshiTransport()
        if world is not None:
            world.build(self.picks)

    def run(self, *args):
        argv = ["--store", str(self.root), "--picks", str(self.picks), *args]
        return pg.main(argv, odds_client=odds_client(self.odds), kalshi_client=kalshi_client(self.kalshi),
                       clock=self.clock)

    def out(self, capsys, *args):
        capsys.readouterr()
        assert self.run(*args) == 0
        return json.loads(capsys.readouterr().out)

    def store(self):
        return pg.GradingStore(self.root / pg.STORE_FILE, readonly=True)

    def rows(self):
        s = self.store()
        try:
            return {r["comment_id"]: r for r in s.latest_matches(pg.fingerprint())}
        finally:
            s.close()

    def audit(self, capsys, wrong=0):
        a = self.out(capsys, "audit-sample", "--out", str(self.audit_dir))
        return self.out(capsys, "audit-pass", "--sample", str(a["audit_id"]), "--wrong", str(wrong))


# ---------------------------------------------------------------- matching: names, the window, uniqueness

def test_team_forms_follow_the_schedule_spelling_and_the_strict_qualifier_rule():
    assert ("seminoles",) in pg.team_forms("Florida St Seminoles")
    assert ("florida", "st") in pg.team_forms("Florida St Seminoles")
    assert ("florida",) not in pg.team_forms("Florida St Seminoles")        # Florida is not Florida State
    assert ("lakers",) in pg.team_forms("Los Angeles Lakers") and ("los", "angeles") in pg.team_forms("Los Angeles Lakers")
    assert all(len("".join(f)) >= 3 for f in pg.team_forms("Orlando Magic"))


def test_over_under_whole_words_or_joined_to_the_line():
    assert pg.over_under("Over 221.5", 221.5) == "over"
    assert pg.over_under("Kalo/Miton u221.5", 221.5) == "under"
    assert pg.over_under("Kalo/Miton o221.5", 221.5) == "over"
    assert pg.over_under("o 221.5", 221.5) is None                        # o is not joined to the line
    assert pg.over_under("Hangover 221.5", 221.5) is None
    assert pg.over_under("over or under 221.5", 221.5) is None
    assert pg.over_under("over/under 221.5", 221.5) is None
    assert pg.over_under("u220", 221.5) is None                           # joined to another number


def test_over_under_joined_form_must_end_at_the_text_a_space_or_a_stop():
    assert pg.over_under("Kalo/Miton o45x", 45) is None                   # o45x is not a total
    assert pg.over_under("Kalo/Miton u221.", 221) == "under"
    assert pg.over_under("Kalo/Miton u221.5)", 221.5) == "under"
    for stop in (".", ",", ";", ":", "!", "?", ")", " lock", ""):
        assert pg.over_under("o221.5" + stop, 221.5) == "over", stop
    for bad in ("x", "-", "/", "_", "5", "a"):
        assert pg.over_under("o221.5" + bad, 221.5) is None, bad


def test_thread_date_and_window_end():
    assert pg.thread_date(TITLE) == D
    assert pg.thread_date("Pick of the Day - 1/2/2027") == date(2027, 1, 2)
    assert pg.thread_date("Pick of the Day") is None
    assert pg.window_end(D) == "2026-11-14T11:00:00Z"                      # 06:00 US-Eastern (EST) the next day


def two_league_world(league=None, extra=()):
    w = PickWorld()
    w.thread()
    w.comment("c1", "alice", [("2026-11-13T20:00:00Z", ml("Kings", league), "-110")])
    for args in extra:
        w.comment(*args)
    return w


def kings_schedule(env, nhl_start="2026-11-14T01:00:00Z"):
    env.odds.events["basketball_nba"] = [ev("nba1", "Sacramento Kings", "Boston Celtics", "2026-11-14T00:00:00Z")]
    env.odds.events["icehockey_nhl"] = [ev("nhl1", "Los Angeles Kings", "Dallas Stars", nhl_start, "icehockey_nhl")]


@pytest.mark.parametrize("nhl_start", ["2026-11-14T01:00:00Z", "2026-11-13T19:30:00Z", "2026-11-10T00:00:00Z"])
def test_a_name_two_known_teams_share_is_not_graded_whatever_their_games(tmp_path, capsys, nhl_start):
    env = Env(tmp_path, two_league_world())
    kings_schedule(env, nhl_start)
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T21:00:00Z")
    assert env.out(capsys, "match")["by_outcome"] == {"name_shared": 1}


def test_the_league_written_narrows_the_teams(tmp_path, capsys):
    env = Env(tmp_path, two_league_world("NBA"))
    kings_schedule(env)
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T21:00:00Z")
    env.out(capsys, "match")
    r = env.rows()["c1"]
    assert (r["status"], r["game_id"], r["side"], r["team"]) == ("matched", "nba1", "home", "Sacramento Kings")


@pytest.mark.parametrize("second, outcome", [
    ("2026-11-14T11:00:00Z", "matched"),          # 06:00 US-Eastern the next day: outside the window
    ("2026-11-14T10:59:59Z", "more_than_one_game"),
    ("2026-11-13T19:30:00Z", "matched"),          # started before the comment was first seen: outside the window
    ("2026-11-13T20:00:01Z", "more_than_one_game"),
])
def test_uniqueness_counts_only_games_of_the_window(tmp_path, capsys, second, outcome):
    w = PickWorld()
    w.thread()
    w.comment("c1", "alice", [("2026-11-13T20:00:00Z", ml("Lokars"), "-110")])
    env = Env(tmp_path, w)
    env.clock.set("2026-11-13T19:00:00Z")
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:00:00Z"),
                                         ev("g2", "Miraton Ramirs", "Kaloton Lokars", second)]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T21:00:00Z")
    got = env.out(capsys, "match")["by_outcome"]
    assert list(got) == [outcome]
    if outcome == "matched":
        assert env.rows()["c1"]["game_id"] == "g1"


def test_one_pick_an_author_a_thread_the_first_seen(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    w.comment("c0", "alice", [("2026-11-13T19:00:00Z", None, None)])                      # not a pick: no row
    w.comment("c1", "alice", [("2026-11-13T19:30:00Z", None, None),
                              ("2026-11-13T21:00:00Z", ml("Celtics"), "-120")])            # a pick only from 21:00
    w.comment("c2", "Alice", [("2026-11-13T20:00:00Z", ml("Kings"), "+105")])              # first pick seen
    w.comment("c3", "bob", [("2026-11-13T20:30:00Z", ml("Celtics"), "-120")])
    w.comment("c4", "mod", [("2026-11-13T20:30:00Z", ml("Celtics"), "-120")], role="moderator")
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("nba1", "Sacramento Kings", "Boston Celtics", "2026-11-14T00:00:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T22:00:00Z")
    env.out(capsys, "match")
    rows = env.rows()
    assert set(rows) == {"c1", "c2", "c3"}
    assert rows["c1"]["reason"] == "not_first_of_author"
    assert rows["c2"]["status"] == rows["c3"]["status"] == "matched"
    assert rows["c2"]["author_key"] == rows["c1"]["author_key"] != rows["c3"]["author_key"]


def test_a_deleted_comment_is_not_counted(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    w.comment("c1", "a1", [("2026-11-13T20:00:00Z", ml("Lokars"), "-110", True)])         # deleted when first seen
    w.comment("c2", "a2", [("2026-11-13T20:00:00Z", ml("Lokars"), "-110"),
                           ("2026-11-13T21:00:00Z", ml("Lokars"), "-110", True)])          # deleted before the start
    w.comment("c3", "a3", [("2026-11-13T20:00:00Z", ml("Lokars"), "-110")])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:00:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T22:00:00Z")
    env.out(capsys, "match")
    rows = env.rows()
    assert "c1" not in rows
    assert rows["c2"]["status"] == "not_graded" and rows["c2"]["reason"] == "version_not_a_pick"
    assert rows["c3"]["status"] == "matched"


def test_an_edit_seen_after_the_start_is_ignored_and_a_comment_first_seen_after_it_is_not_counted(tmp_path, capsys):
    start = "2026-11-14T00:00:00Z"
    w = PickWorld()
    w.thread()
    w.comment("c1", "a1", [(plus(start, -600), ml("Lokars"), "-110"), (plus(start, 1), ml("Supers"), "+300")])
    w.comment("c2", "a2", [(start, ml("Lokars"), "-110")])                                  # first seen at the start
    w.comment("c3", "a3", [(plus(start, -1), ml("Lokars"), "-110")])                        # one second before
    w.comment("c4", "a4", [(plus(start, -900), None, None), (plus(start, -30), ml("Lokars"), "-110")])
    w.comment("c5", "a5", [(plus(start, -900), ml("Lokars"), "-110"), (plus(start, -10), None, None)])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", start)]
    env.clock.set("2026-11-13T20:00:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-14T06:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    assert (r["c1"]["status"], r["c1"]["version"], r["c1"]["side"]) == ("matched", 1, "home")
    assert r["c2"]["reason"] == "no_game_in_window"        # no game of the window starts after it was first seen
    assert (r["c3"]["status"], r["c3"]["known_at"]) == ("matched", plus(start, -1))
    assert (r["c4"]["status"], r["c4"]["known_at"]) == ("matched", plus(start, -30))
    assert r["c5"]["reason"] == "version_not_a_pick"       # the version in force at the start is not a pick


def test_markets_leagues_and_totals(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    w.comment("c1", "a1", [("2026-11-13T18:00:00Z", {"market": "prop", "selection": "Lokars over 4.5"}, "+120")])
    w.comment("c2", "a2", [("2026-11-13T18:00:00Z", {"market": "moneyline", "selection": "Lokars", "league": "EPL"},
                            "-110")])
    w.comment("c3", "a3", [("2026-11-13T18:00:00Z", {"market": "total", "selection": "Over 221.5", "line": 221.5,
                                                     "event": {"side_a": "Lokars", "side_b": "Supers"}}, "-110")])
    w.comment("c4", "a4", [("2026-11-13T18:00:00Z", {"market": "spread", "selection": "Lokars -3.5", "line": -3.5},
                            "-110")])
    w.comment("c5", "a5", [("2026-11-13T18:00:00Z", {"market": "moneyline", "selection": "Lokars over Supers"},
                            "-110")])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:00:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T19:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    assert r["c1"]["reason"] == "market_not_graded" and r["c2"]["reason"] == "league_not_graded"
    assert (r["c3"]["status"], r["c3"]["market"], r["c3"]["ou"], r["c3"]["line"]) == ("matched", "total", "over", 221.5)
    assert (r["c4"]["side"], r["c4"]["line"]) == ("home", -3.5)
    assert r["c5"]["reason"] == "side_unclear"


def two_games_env(tmp_path, capsys, picks):
    w = PickWorld()
    w.thread()
    for cid, f in picks.items():
        w.comment(cid, "a" + cid, [("2026-11-13T18:00:00Z", f, "-110")])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:00:00Z"),
                                         ev("g2", "Miraton Ramirs", "Doteton Tedors", "2026-11-14T01:00:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T19:00:00Z")
    env.out(capsys, "match")
    return env.rows()


def total(sel, a, b):
    return {"market": "total", "selection": sel, "line": 210.5, "event": {"side_a": a, "side_b": b}}


def test_a_selection_naming_teams_of_two_different_games_is_not_graded(tmp_path, capsys):
    r = two_games_env(tmp_path, capsys, {
        "t1": total("Kaloton Lokars/Ramirs o210.5", "Kaloton Lokars", "Ramirs"),     # A in full, another game's team
        "t2": total("Kaloton Lokars/Supers o210.5", "Kaloton Lokars", "Supers"),     # both teams of one game
        "m1": {"market": "moneyline", "selection": "Kaloton Lokars ML, Tedors next"},
        "m2": {"market": "spread", "selection": "Miraton Ramirs -3.5 then Lokars", "line": -3.5},
        "m3": {"market": "spread", "selection": "Ramirs -3.5 then Lokars", "line": -3.5}})   # a tie: shared
    assert r["m3"]["reason"] == "name_shared"
    assert r["t1"]["reason"] == "teams_of_two_games" and r["m1"]["reason"] == "teams_of_two_games"
    assert r["m2"]["reason"] == "teams_of_two_games"
    assert (r["t2"]["status"], r["t2"]["game_id"]) == ("matched", "g1")


def test_a_selection_betting_against_the_team_it_names_is_not_graded(tmp_path, capsys):
    sel = {"f1": "Fade Lokars ML", "f2": "against the Lokars ML", "f3": "anti-Lokars ML", "f4": "Not Lokars ML",
           "f5": "vs Lokars ML", "f6": "FADE LOKARS", "ok1": "Lokars ML not a lock", "ok2": "Lokars ML, fade the public"}
    r = two_games_env(tmp_path, capsys, {k: {"market": "moneyline", "selection": v} for k, v in sel.items()})
    for k in ("f1", "f2", "f3", "f4", "f5", "f6"):
        assert r[k]["reason"] == "bets_against", k
    assert r["ok1"]["status"] == r["ok2"]["status"] == "matched"


def test_the_opponent_named_less_fully_still_makes_the_side_unclear(tmp_path, capsys):
    r = two_games_env(tmp_path, capsys, {
        "s1": {"market": "moneyline", "selection": "Kaloton Lokars over Supers ML"},
        "s2": {"market": "spread", "selection": "Kaloton Lokars -3.5 vs Pesuton", "line": -3.5}})
    assert r["s1"]["reason"] == "side_unclear" and r["s2"]["reason"] in ("side_unclear", "bets_against")
    assert r["s1"]["status"] == "not_graded"


def test_no_model_proposes_matches(tmp_path):
    assert not hasattr(pg, "LocalModelProposer") and not hasattr(pg, "PROPOSER_SYSTEM")
    with pytest.raises(SystemExit):
        pg.main(["--store", str(tmp_path / "g"), "--picks", str(tmp_path / "p.sqlite"), "match", "--no-model"])


# ---------------------------------------------------------------- invariant 1: the earliest start any list gives

def test_the_start_is_the_earliest_any_stored_list_gives_whenever_it_was_fetched(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    w.comment("c1", "a1", [("2026-11-14T00:10:00Z", ml("Lokars"), "-110")])     # g1 began 00:00; a list says 00:25
    w.comment("c2", "a2", [("2026-11-13T23:50:00Z", ml("Lokars"), "-110"),
                           ("2026-11-14T00:10:00Z", ml("Supers"), "+120")])     # the edit falls after 00:00
    w.comment("c3", "a3", [("2026-11-14T00:10:00Z", ml("Ramirs"), "-110")])     # g2: lists said 00:20, began 00:00
    w.comment("c4", "a4", [("2026-11-13T23:59:00Z", ml("Ramirs"), "-110")])
    w.comment("c5", "a5", [("2026-11-13T23:00:00Z", ml("Ratears"), "-110")])    # g3: no list before its start held it
    w.comment("c6", "a6", [("2026-11-13T23:00:00Z", ml("Visurs"), "-110")])     # g4: 00:00, then 00:31
    w.comment("c7", "a7", [("2026-11-13T23:00:00Z", ml("Kanors"), "-110")])     # g5: 01:00, then 01:40 after it began
    w.comment("c8", "a8", [("2026-11-14T00:50:00Z", ml("Pikars"), "-110")])     # g6: 01:00, then 00:45 before it
    w.comment("c9", "a9", [("2026-11-14T00:40:00Z", ml("Pikars"), "-110")])
    env = Env(tmp_path, w)

    def lists(g1, g2, g4, g5, g6, g3=None):
        out = [ev("g1", "Kaloton Lokars", "Pesuton Supers", g1), ev("g2", "Miraton Ramirs", "Doteton Tedors", g2),
               ev("g4", "Suviton Visurs", "Dopeton Pedors", g4), ev("g5", "Nokaton Kanors", "Vimiton Lodars", g5),
               ev("g6", "Lodaton Pikars", "Sudoton Miders", g6)]
        return out + ([ev("g3", "Teraton Ratears", "Kadoton Midors", g3)] if g3 else [])
    env.clock.set("2026-11-13T19:00:00Z")
    env.odds.events["basketball_nba"] = lists("2026-11-14T00:00:00Z", "2026-11-14T00:20:00Z", "2026-11-14T00:00:00Z",
                                              "2026-11-14T01:00:00Z", "2026-11-14T01:00:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T23:00:00Z")
    env.odds.events["basketball_nba"] = lists("2026-11-14T00:00:00Z", "2026-11-14T00:20:00Z", "2026-11-14T00:31:00Z",
                                              "2026-11-14T01:00:00Z", "2026-11-14T00:45:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-14T00:05:00Z")
    env.odds.events["basketball_nba"] = lists("2026-11-14T00:25:00Z", "2026-11-14T00:00:00Z", "2026-11-14T00:31:00Z",
                                              "2026-11-14T01:00:00Z", "2026-11-14T00:45:00Z", "2026-11-14T00:00:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-14T02:00:00Z")
    env.odds.events["basketball_nba"] = lists("2026-11-14T00:25:00Z", "2026-11-14T00:00:00Z", "2026-11-14T00:31:00Z",
                                              "2026-11-14T01:40:00Z", "2026-11-14T00:45:00Z", "2026-11-14T00:00:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-14T06:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    assert r["c1"]["status"] == "not_graded"
    assert (r["c2"]["status"], r["c2"]["version"], r["c2"]["side"], r["c2"]["start"]) == \
        ("matched", 1, "home", "2026-11-14T00:00:00Z")
    assert r["c3"]["status"] == "not_graded"
    assert (r["c4"]["status"], r["c4"]["start"]) == ("matched", "2026-11-14T00:00:00Z")
    assert r["c5"]["reason"] == "no_start_known"
    assert r["c6"]["reason"] == "start_disagrees" and r["c7"]["reason"] == "start_disagrees"
    assert r["c8"]["status"] == "not_graded"
    assert (r["c9"]["status"], r["c9"]["start"]) == ("matched", "2026-11-14T00:45:00Z")


def oracle_start(obs):
    """obs [(fetched ts, commence ts)] of one game: (earliest start, held before it, flagged)."""
    start = min(c for _, c in obs)
    return start, any(a < start for a, _ in obs), max(c for _, c in obs) - start > 1800


def oracle_pick(versions, first_seen, starts):
    """versions [(seen ts, game id or None)]: the first version (in order) naming a game of the window (starting
    after the comment was first seen) that is the latest version first seen strictly before that game's start."""
    for i, (seen, gid) in enumerate(versions):
        if gid is None or not first_seen < starts[gid]:
            continue
        before = [j for j, (t, _) in enumerate(versions) if t < starts[gid]]
        if before and before[-1] == i:
            return gid, i
    return None


def test_text_and_starts_against_an_oracle_generated_3000_comments(tmp_path, capsys):
    rng = random.Random(20261113)
    names = team_names(80)
    fetches = ["2026-11-13T18:00:00Z", "2026-11-13T22:30:00Z", "2026-11-14T00:30:00Z", "2026-11-14T02:00:00Z"]
    lists = [[] for _ in fetches]
    obs, nick = {}, {}
    for k in range(40):
        gid = "g%02d" % k
        home, away = "%s %s" % names[2 * k], "%s %s" % names[2 * k + 1]
        nick[gid] = names[2 * k][1]
        base = ts("2026-11-13T23:00:00Z") + rng.randrange(0, 4 * 3600, 60)
        first = rng.choice([0, 0, 0, 1, 2, 3])
        obs[gid] = []
        for li, fetched in enumerate(fetches):
            if li < first:
                continue
            c = base + (0 if li == first else rng.choice([-2400, -1500, -900, -60, 0, 0, 0, 60, 900, 1500, 2400]))
            lists[li].append(ev(gid, home, away, iso_ts(c)))
            obs[gid].append((ts(fetched), c))
    truth = {gid: oracle_start(o) for gid, o in obs.items()}
    starts = {gid: t[0] for gid, t in truth.items()}
    w = PickWorld()
    w.thread()
    cases = {}
    for n in range(3000):
        g0 = rng.choice(sorted(obs))
        k = rng.choice([1, 1, 2, 2, 3])
        seen = sorted({starts[g0] + rng.randrange(-5400, 3600) for _ in range(k)})
        vs = [(t, rng.choice([None, g0, g0, g0, rng.choice(sorted(obs))])) for t in seen]
        cid = "c%04d" % n
        w.comment(cid, "author%04d" % n, [(iso_ts(t), ml(nick[g]) if g else None, "-110" if g else None) for t, g in vs])
        cases[cid] = vs
    env = Env(tmp_path, w)
    for fetched, events in zip(fetches, lists):
        env.clock.set(fetched)
        env.odds.events["basketball_nba"] = events
        env.out(capsys, "schedule")
    env.clock.set("2026-11-14T12:00:00Z")
    env.out(capsys, "match")
    rows = env.rows()
    assert set(rows) == {cid for cid, vs in cases.items() if any(g for _, g in vs)}
    tally = {"matched": 0, "start_unknown_or_flagged": 0, "none": 0}
    for cid, r in rows.items():
        vs = cases[cid]
        want = oracle_pick(vs, vs[0][0], starts)
        if want is None:
            assert r["status"] != "matched", cid
            tally["none"] += 1
            continue
        gid, i = want
        _, held, flagged = truth[gid]
        assert (r["game_id"], r["version"]) == (gid, i + 1), (cid, r["reason"])
        if not held:
            assert r["reason"] == "no_start_known", cid
        elif flagged:
            assert r["reason"] == "start_disagrees", cid
        else:
            assert r["status"] == "matched", (cid, r["reason"])
        if r["status"] == "matched":
            tally["matched"] += 1
            assert r["start"] == iso_ts(starts[gid])
            assert ts(r["known_at"]) <= vs[i][0] < starts[gid]
        else:
            tally["start_unknown_or_flagged"] += 1
    assert tally["matched"] > 300 and tally["start_unknown_or_flagged"] > 100 and tally["none"] > 300, tally


# ---------------------------------------------------------------- invariant 2: every team known, Kalshi's codes

LOOKALIKES = {   # league -> [(full name, city as written, nicknames as written, Kalshi code, Kalshi name)]
    "NBA": [("Kaloton Lokars", "Kaloton", ["Lokars"], "KAL", "Kaloton"),
            ("Miraton Tedors", "Miraton", ["Tedors"], "MIR", "Miraton"),
            ("Sunoton Ravirs", "Sunoton", ["Ravirs"], "SUN", "Sunoton"),
            ("Peraton Dokars", "Peraton", ["Dokars"], "PER", "Peraton")],
    "NHL": [("Kaloton Ramirs", "Kaloton", ["Ramirs"], "KAL", "Kaloton"),        # a city and a code of the NBA
            ("Peraton Visurs", "Peraton", ["Visurs"], "PRV", "Peraton"),
            ("Tevaton Nomirs", "Tevaton", ["Nomirs"], "TEV", "Tevaton"),
            ("Fumaton Gelirs", "Fumaton", ["Gelirs"], "FUM", "Fumaton")],
    "NFL": [("Suviton Tedors", "Suviton", ["Tedors"], "SUV", "Suviton"),        # a nickname of the NBA
            ("Nokaton Pedors", "Nokaton", ["Pedors"], "NOK", "Nokaton"),
            ("Ravaton Kirs", "Ravaton", ["Kirs"], "RAV", "Ravaton"),
            ("Bematon Losirs", "Bematon", ["Losirs"], "BEM", "Bematon")],
    "NCAAF": [("Nokaton St Kanors", "Nokaton St", ["Kanors"], "NOK", "Nokaton St."),   # "Nokaton" inside it; NFL code
              ("Raviton Sumirs", "Raviton", ["Sumirs"], "RVT", "Raviton"),       # the same name in basketball
              ("Lodaton Pikars", "Lodaton", ["Pikars"], "LOD", "Lodaton"),
              ("Gimaton Tarirs", "Gimaton", ["Tarirs"], "GIM", "Gimaton")],
    "NCAAMB": [("Raviton Sumirs", "Raviton", ["Sumirs"], "RVT", "Raviton"),
               ("Doteton Blue Visurs", "Doteton", ["Blue Visurs", "Visurs"], "DBV", "Doteton"),   # holds NHL "Visurs"
               ("Mikaton Lopers", "Mikaton", ["Lopers"], "MIK", "Mikaton"),
               ("Hunaton Werirs", "Hunaton", ["Werirs"], "HUN", "Hunaton")],
}
LOOK_STARTS = {"NBA": ("2026-11-14T00:00:00Z", "2026-11-14T01:00:00Z"),
               "NHL": ("2026-11-14T00:30:00Z", "2026-11-14T01:30:00Z"),
               "NFL": ("2026-11-14T00:15:00Z", "2026-11-14T02:00:00Z"),
               "NCAAF": ("2026-11-13T23:45:00Z", "2026-11-14T01:15:00Z"),
               "NCAAMB": ("2026-11-14T02:30:00Z", "2026-11-14T00:45:00Z")}
LEAGUE_WORD = {"NBA": "NBA", "NHL": "NHL", "NFL": "NFL", "NCAAF": "CFB", "NCAAMB": "NCAAB"}


def lookalike_env(tmp_path, capsys, world):
    env = Env(tmp_path, world)
    env.clock.set("2026-11-12T15:00:00Z")                  # yesterday's list: one more Kaloton team, in the NBA
    env.odds.events["basketball_nba"] = [ev("old1", "Kaloton Supers", "Peraton Dokars", "2026-11-13T00:00:00Z")]
    env.out(capsys, "schedule")
    games = {}
    for lg, teams in LOOKALIKES.items():
        events = []
        env.kalshi.kevents[SERIES[lg]] = []
        for j in range(2):
            (h, _, _, hc, hk), (a, _, _, ac, ak) = teams[2 * j], teams[2 * j + 1]
            gid = "%s%d" % (lg.lower(), j)
            events.append(ev(gid, h, a, LOOK_STARTS[lg][j], SPORT[lg]))
            games[gid] = (lg, h, a, LOOK_STARTS[lg][j])
            env.kalshi.kevents[SERIES[lg]].append(kevent(lg, "26NOV13", (ac, ak), (hc, hk)))
        env.odds.events[SPORT[lg]] = events
    env.clock.set("2026-11-13T15:00:00Z")
    env.out(capsys, "schedule")
    return env, games


def test_a_pick_names_a_team_only_against_every_team_known_generated_1200_picks(tmp_path, capsys):
    rng = random.Random(9)
    team_game = {}
    for lg, teams in LOOKALIKES.items():
        for j in range(2):
            team_game[(lg, teams[2 * j][0])] = ("%s%d" % (lg.lower(), j), "home", teams[2 * j + 1])
            team_game[(lg, teams[2 * j + 1][0])] = ("%s%d" % (lg.lower(), j), "away", teams[2 * j])
    w = PickWorld()
    w.thread()
    labels = {}

    def form(entry, kind):
        full, city, nicks, code, _ = entry
        return {"full": full, "city": city, "nick": rng.choice(nicks), "code": code, "code_lower": code.lower()}[kind]
    for n in range(1200):
        lg = rng.choice(sorted(LOOKALIKES))
        entry = rng.choice(LOOKALIKES[lg])
        gid, side, opp = team_game[(lg, entry[0])]
        start = LOOK_STARTS[lg][int(gid[-1])]
        inside = rng.random() < 0.5
        seen = plus(start, -rng.randint(60, 7200) if inside else rng.randint(0, 3600))
        kind = rng.choice(["full", "city", "nick", "code", "code_lower"])
        market = rng.choice(["moneyline", "moneyline", "spread", "total"])
        league = rng.choice([None, LEAGUE_WORD[lg]])
        text = form(entry, kind)
        if market == "moneyline":
            f = {"market": "moneyline", "selection": "Take " + text + " ML"}
        elif market == "spread":
            f = {"market": "spread", "selection": "Take " + text + " -3.5", "line": -3.5}
        else:
            other = form(opp, rng.choice(["full", "city", "nick", "code"]))
            f = {"market": "total", "selection": "%s/%s o210.5" % (text, other), "line": 210.5,
                 "event": {"side_a": text, "side_b": other}}
        if league:
            f["league"] = league
        cid = "c%04d" % n
        w.comment(cid, "author%04d" % n, [(seen, f, "-110")])
        labels[cid] = {"game": gid, "side": None if market == "total" else side, "inside": inside, "kind": kind}
    env, _ = lookalike_env(tmp_path, capsys, w)
    env.clock.set("2026-11-14T12:00:00Z")
    env.out(capsys, "match")
    rows = env.rows()
    assert set(rows) == set(labels)
    wrong = [(cid, r["game_id"], r["side"], labels[cid]) for cid, r in rows.items() if r["status"] == "matched" and (
        not labels[cid]["inside"] or r["game_id"] != labels[cid]["game"] or r["side"] != labels[cid]["side"])]
    assert wrong == []
    matched = [cid for cid, r in rows.items() if r["status"] == "matched"]
    assert len(matched) > 150
    sided = [c for c in matched if labels[c]["side"] is not None]    # a total may be found by the other team alone
    assert {labels[c]["kind"] for c in sided} == {"full", "city", "nick", "code"}   # never a code in lower case


def test_a_kalshi_code_names_a_team_only_in_capitals_and_never_one_another_league_shares(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    seen = "2026-11-13T22:00:00Z"
    sel = {"c1": ("Take KAL ML", None),          # the NBA and the NHL share it: no name without a league
           "c2": ("Take KAL ML", "NBA"),
           "c3": ("Take kal ML", "NBA"),         # not in capitals
           "c4": ("Take SUN ML", None),          # one team's code across all five tables
           "c11": ("KAL ML", "NBA"),             # in capitals with no word besides the code and the marker: a code
           "c15": ("KAL STRONG ML", "NBA"),      # in capitals with another word: no code
           "c12": ("Take NOK ML", "NFL"), "c13": ("Take NOK ML", "CFB"), "c14": ("Take NOK ML", None)}
    for cid, (text, lg) in sel.items():
        w.comment(cid, "a" + cid, [(seen, dict({"market": "moneyline", "selection": text},
                                                **({"league": lg} if lg else {})), "-110")])
    w.comment("c5", "a5", [(seen, ml("Kaloton", "NBA"), "-110")])            # Kaloton Supers is known too
    w.comment("c6", "a6", [(seen, ml("Blue Visurs"), "-110")])               # the longer name holds "Visurs"
    w.comment("c7", "a7", [(seen, ml("Visurs"), "-110")])
    w.comment("c8", "a8", [(seen, ml("Nokaton"), "-110")])                   # not "Nokaton St"
    w.comment("c9", "a9", [(seen, ml("Raviton Sumirs"), "-110")])            # the same full name in two leagues
    w.comment("c10", "a10", [(seen, ml("Raviton Sumirs", "NCAAB"), "-110")])
    env, _ = lookalike_env(tmp_path, capsys, w)
    env.clock.set("2026-11-14T12:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    assert r["c1"]["reason"] == "no_team_named"
    assert (r["c2"]["status"], r["c2"]["team"]) == ("matched", "Kaloton Lokars")
    assert r["c3"]["reason"] == "no_team_named" and r["c15"]["reason"] == "no_team_named"
    assert (r["c11"]["status"], r["c11"]["team"]) == ("matched", "Kaloton Lokars")
    assert (r["c4"]["status"], r["c4"]["team"]) == ("matched", "Sunoton Ravirs")
    assert (r["c12"]["status"], r["c12"]["team"]) == ("matched", "Nokaton Pedors")
    assert (r["c13"]["status"], r["c13"]["team"]) == ("matched", "Nokaton St Kanors")
    assert r["c14"]["reason"] == "no_team_named"
    assert r["c5"]["reason"] == "name_shared"
    assert (r["c6"]["status"], r["c6"]["team"]) == ("matched", "Doteton Blue Visurs")
    assert r["c7"]["reason"] == "name_shared"
    assert (r["c8"]["status"], r["c8"]["team"]) == ("matched", "Nokaton Pedors")
    assert r["c9"]["reason"] == "name_shared"
    assert (r["c10"]["status"], r["c10"]["league"], r["c10"]["game_id"]) == ("matched", "NCAAMB", "ncaamb0")
    env.clock.set("2026-11-20T15:00:01Z")                    # the latest tables are now more than 7 days old
    env.out(capsys, "match")
    r = env.rows()
    assert all(r[c]["reason"] == "no_team_named" for c in ("c2", "c4", "c12", "c13"))
    assert r["c8"]["status"] == "matched"


def test_no_code_is_a_name_unless_all_five_leagues_have_a_table(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    w.comment("c2", "a2", [("2026-11-13T22:00:00Z", {"market": "moneyline", "selection": "Take KAL ML",
                                                     "league": "NBA"}, "-110")])
    w.comment("c4", "a4", [("2026-11-13T22:00:00Z", {"market": "moneyline", "selection": "Take SUN ML"}, "-110")])
    env = Env(tmp_path, w)
    env.kalshi.fail = {SERIES["NHL"]}                        # only four leagues' tables can be listed
    env.clock.set("2026-11-13T15:00:00Z")
    for lg, teams in LOOKALIKES.items():
        env.odds.events[SPORT[lg]] = [ev("%s%d" % (lg.lower(), j), teams[2 * j][0], teams[2 * j + 1][0],
                                         LOOK_STARTS[lg][j], SPORT[lg]) for j in range(2)]
        env.kalshi.kevents[SERIES[lg]] = [kevent(lg, "26NOV13", teams[2 * j + 1][3:5], teams[2 * j][3:5])
                                          for j in range(2)]
    out = env.out(capsys, "schedule")
    assert str(out["kalshi_codes"]["NHL"]).startswith("error") and out["kalshi_codes"]["NBA"] == {"events": 2, "unreadable": 0}
    env.clock.set("2026-11-14T12:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    assert r["c2"]["reason"] == r["c4"]["reason"] == "no_team_named"


def test_schedule_stores_code_tables_from_the_events_lists_with_at_most_thirty_requests(tmp_path, capsys):
    env = Env(tmp_path, two_league_world("NBA"))
    kings_schedule(env)
    env.kalshi.kevents["KXNBAGAME"] = [kevent("NBA", "26NOV13", ("BOS", "Boston"), ("SAC", "Sacramento"))]
    out = env.out(capsys, "schedule")
    assert out["kalshi_codes"]["NBA"] == {"events": 1, "unreadable": 0}
    assert len(env.kalshi.urls) == 5 and all("/events?" in u and "min_close_ts=" in u for u in env.kalshi.urls)
    assert not any("/markets" in u for u in env.kalshi.urls)          # no market is read, of any event
    s = env.store()
    tables = s.rows("SELECT league, raw FROM code_tables")
    fetches = s.rows("SELECT league, fetched_at FROM code_fetches")
    s.close()
    assert sorted(t["league"] for t in tables) == sorted(pg.LEAGUES) and len(fetches) == 5   # empty tables count
    raw = json.loads([t for t in tables if t["league"] == "NBA"][0]["raw"])
    assert all(json.loads(t["raw"]) == [] for t in tables if t["league"] != "NBA")
    assert raw == [{"event_ticker": "KXNBAGAME-26NOV13BOSSAC", "sub_title": "BOS vs SAC (Nov 13)",
                    "title": "Boston vs Sacramento"}]
    env.kalshi.urls.clear()
    env.kalshi.endless = True                                 # pages without end: the run stops at 30 requests
    env.clock.set("2026-11-13T17:00:00Z")
    out = env.out(capsys, "schedule")
    assert len(env.kalshi.urls) == 30 and out["NBA"]["games"] == 1
    assert out["kalshi_codes"]["NFL"] == {"events": 0, "unreadable": 0}        # an empty first page ends the listing
    assert all(str(out["kalshi_codes"][lg]).startswith("error") for lg in ("NBA", "NHL", "NCAAF", "NCAAMB"))
    s = env.store()
    assert s.one("SELECT COUNT(*) AS n FROM code_fetches")["n"] == 6           # an unfinished listing is no table
    s.close()


# ---------------------------------------------------------------- invariant 3: codes from complete tables only

SHARED = {   # league -> [(full name, Kalshi name, code)]; games: team 0 v 1 and team 2 v 3
    "NFL": [("Caraton Panirs", "Caraton", "CAR"), ("Pitaton Stelirs", "Pitaton", "PIT"),
            ("Dalaton Cowirs", "Dalaton", "DAL"), ("Minaton Vikirs", "Minaton", "MNV")],
    "NHL": [("Caraton Hurirs", "Caraton", "CAR"), ("Pitaton Pengirs", "Pitaton", "PIT"),
            ("Dalaton Starirs", "Dalaton", "DAL"), ("Edmaton Oilirs", "Edmaton", "EDM")],
    "NBA": [("Dalaton Mavirs", "Dalaton", "DAL"), ("Minaton Timirs", "Minaton", "MNV"),
            ("Bosaton Celirs", "Bosaton", "BOS"), ("Phoaton Sunirs", "Phoaton", "PHX")],
    "NCAAF": [("Iowaton Hawkirs", "Iowaton", "IOWT"), ("Ohiaton Bucirs", "Ohiaton", "OSU"),
              ("Bamaton Tidirs", "Bamaton", "BAMA"), ("Texaton Longirs", "Texaton", "TEXT")],
    "NCAAMB": [("Iowaton Hawkirs", "Iowaton", "IOWT"), ("Ohiaton Bucirs", "Ohiaton", "OSU"),
               ("Dukaton Devirs", "Dukaton", "DUKE"), ("Kanaton Jayirs", "Kanaton", "KANT")],
}
SHARED_STARTS = {"NFL": ("2026-11-13T23:00:00Z", "2026-11-13T23:30:00Z"),
                 "NHL": ("2026-11-14T01:00:00Z", "2026-11-14T01:30:00Z"),
                 "NBA": ("2026-11-14T00:00:00Z", "2026-11-14T02:00:00Z"),
                 "NCAAF": ("2026-11-13T22:30:00Z", "2026-11-14T00:30:00Z"),
                 "NCAAMB": ("2026-11-14T02:30:00Z", "2026-11-14T03:00:00Z")}
ENGLISH = "car\npit\ntext\nduke\ntake\ntonight\nhammer\n"
TEMPLATES = (("Take {} ML", True, False), ("Hammer {} tonight ML", False, False), ("Take {} -3.5", True, False),
             ("{} ML", True, False), ("{} -3.5", True, False),            # capitals, nothing but the code and marker
             ("{} TONIGHT ML", True, True))                               # capitals with another word: no code


def shared_env(tmp_path, capsys, world, fail=(), unreadable=(), extra=None):
    """The SHARED world: its games, and Kalshi's events lists; `unreadable` leagues have their events' sub-titles
    written with names instead of codes ("Pitaton vs Caraton (Nov 13)"), `extra` adds events to a league's list."""
    env = Env(tmp_path, world)
    env.kalshi.fail = set(fail)
    env.clock.set("2026-11-13T15:00:00Z")
    for lg, teams in SHARED.items():
        env.odds.events[SPORT[lg]] = [ev("%s%d" % (lg.lower(), j), teams[2 * j][0], teams[2 * j + 1][0],
                                         SHARED_STARTS[lg][j], SPORT[lg]) for j in range(2)]
        env.kalshi.kevents[SERIES[lg]] = [kevent(lg, "26NOV13", teams[2 * j + 1][1:][::-1], teams[2 * j][1:][::-1])
                                          for j in range(2)]
        if lg in unreadable:
            for e in env.kalshi.kevents[SERIES[lg]]:
                e["sub_title"] = e["title"] + " (Nov 13)"
        env.kalshi.kevents[SERIES[lg]] += (extra or {}).get(lg, [])
    env.schedule_out = env.out(capsys, "schedule")
    env.clock.set("2026-11-14T12:00:00Z")
    env.out(capsys, "match")
    return env


def shared_picks(n_picks, seed):
    """`n_picks` generated picks on the SHARED world's games, each naming its team by Kalshi code only, with labels:
    the game and side meant, whether the game had begun, whether the code may name it (the oracle), and whether the
    code is one that several leagues share."""
    rng = random.Random(seed)
    owners = {}
    for lg, teams in SHARED.items():
        for j, (full, _, code) in enumerate(teams):
            owners.setdefault(code, set()).add((lg, full))
    w = PickWorld()
    w.thread()
    labels = {}
    for n in range(n_picks):
        lg = rng.choice(sorted(SHARED))
        j = rng.randrange(4)
        full, _, code = SHARED[lg][j]
        gid, start = "%s%d" % (lg.lower(), j // 2), SHARED_STARTS[lg][j // 2]
        begun = rng.random() < 0.5
        seen = plus(start, rng.randint(60, 5400) if begun else -rng.randint(60, 5400))
        template, before, caps_blocked = rng.choice(TEMPLATES)
        written = rng.random() < 0.5
        f = {"market": "spread" if "-3.5" in template else "moneyline", "selection": template.format(code)}
        if f["market"] == "spread":
            f["line"] = -3.5
        if written:
            f["league"] = LEAGUE_WORD[lg]
        cid = "c%04d" % n
        w.comment(cid, "author%04d" % n, [(seen, f, "-110")])
        scope = {o for o in owners[code] if not written or o[0] == lg}
        usable = (before or code.lower() not in ENGLISH.split()) and len(scope) == 1 and not caps_blocked
        labels[cid] = {"game": gid, "side": "home" if j % 2 == 0 else "away", "begun": begun, "usable": usable,
                       "risky": begun and len(owners[code]) > 1}
    return w, labels


def wrong_matches(rows, labels):
    return [(c, r["game_id"], labels[c]) for c, r in rows.items() if r["status"] == "matched" and (
        r["game_id"] != labels[c]["game"] or r["side"] != labels[c]["side"])]


def test_a_code_names_a_team_only_from_complete_tables_generated_1100_picks_with_shared_codes(
        tmp_path, capsys, monkeypatch):
    words = tmp_path / "words"
    words.write_text(ENGLISH)
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    w, labels = shared_picks(1100, 16)
    env = shared_env(tmp_path, capsys, w)
    rows = env.rows()
    assert set(rows) == set(labels)
    assert wrong_matches(rows, labels) == []
    for c, r in rows.items():                                   # the oracle: matched exactly when it should be
        assert (r["status"] == "matched") == (labels[c]["usable"] and not labels[c]["begun"]), (c, r["reason"])
    assert sum(1 for c in rows if rows[c]["status"] == "matched") > 150
    assert sum(1 for c in labels if labels[c]["risky"]) > 300
    env2 = shared_env(tmp_path / "four", capsys, w, fail=[SERIES["NHL"]])  # one league's table missing: no code at all
    assert not any(r["status"] == "matched" for r in env2.rows().values())


# ---------------------------------------------------------------- rev 4, outcome 1: a table read in full only

def test_a_league_whose_events_carry_names_instead_of_codes_has_no_complete_table_verifier_scenario(
        tmp_path, capsys, monkeypatch):
    """The verifier's case: the NFL's events said "Pitaton vs Caraton (Nov 13)"; CAR and PIT, which the NHL shares,
    then seemed the NHL's alone, and NFL picks went to the NHL's later games. Now no code names a team at all."""
    words = tmp_path / "words"
    words.write_text(ENGLISH)
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    w, labels = shared_picks(1100, 21)
    env = shared_env(tmp_path, capsys, w, unreadable=("NFL",))
    rows = env.rows()
    assert set(rows) == set(labels) and wrong_matches(rows, labels) == []
    assert not any(r["status"] == "matched" for r in rows.values())        # every pick named its team by code only
    assert env.schedule_out["kalshi_codes"]["NFL"] == {"events": 2, "unreadable": 2}
    assert env.schedule_out["kalshi_codes"]["NHL"] == {"events": 2, "unreadable": 0}
    st = env.out(capsys, "status")
    assert st["code_readability"]["NFL"]["unreadable"] == 2 and st["code_readability"]["NHL"]["unreadable"] == 0
    s = env.store()
    raw = json.loads(s.one("SELECT t.raw FROM code_tables t WHERE t.league = 'NFL'")["raw"])
    s.close()
    assert len(raw) == 2                                                    # stored as listed, unreadable events kept


def _fillers(n):
    """`n` more NBA events with readable codes, on other days, of teams that play no stored game."""
    return [kevent("NBA", "26NOV%02d" % (1 + k % 12), ("Z%03d" % k, "Zeton%d" % k), ("Y%03d" % k, "Yeton%d" % k))
            for k in range(n)]


@pytest.mark.parametrize("bad", [1, 0])
def test_one_unreadable_event_among_two_hundred_switches_every_code_off(tmp_path, capsys, monkeypatch, bad):
    words = tmp_path / "words"
    words.write_text(ENGLISH)
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    w, labels = shared_picks(600, 5)
    odd = kevent("NBA", "26NOV12", ("BOS", "Bosaton"), ("PHX", "Phoaton"))
    if bad:
        odd["sub_title"] = "Bosaton vs Phoaton (Nov 12)"
    env = shared_env(tmp_path, capsys, w, extra={"NBA": _fillers(197) + [odd]})
    rows = env.rows()
    assert wrong_matches(rows, labels) == []
    matched = sum(1 for r in rows.values() if r["status"] == "matched")
    assert (matched == 0) if bad else (matched > 50)
    assert env.schedule_out["kalshi_codes"]["NBA"] == {"events": 200, "unreadable": bad}


# ---------------------------------------------------------------- invariant 4: codes that are English words

def test_an_english_word_code_counts_only_before_the_bet_also_in_a_selection_all_in_capitals(
        tmp_path, capsys, monkeypatch):
    words = tmp_path / "words"
    words.write_text("no\nway\nis\nthe\nplay\ntake\ntonight\n")
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    sel = {"c1": ("moneyline", "NO WAY ML"), "c2": ("moneyline", "Take NO ML"), "c3": ("moneyline", "NO ML"),
           "c4": ("moneyline", "No way, NO is the play ML"), "c5": ("spread", "Take NO -4.5"),
           "c6": ("moneyline", "NO ml"), "c7": ("moneyline", "Take WAR ML"), "c8": ("moneyline", "Take WAR tonight ML"),
           "c9": ("moneyline", "Take NO tonight ML"), "c10": ("spread", "Take NO 4.5 points")}
    w = PickWorld()
    w.thread()
    for cid, (market, text) in sel.items():
        f = {"market": market, "selection": text}
        if market == "spread":
            f["line"] = -4.5 if "-" in text else 4.5
        w.comment(cid, "a" + cid, [("2026-11-13T18:00:00Z", f, "-110")])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Noraton Pelirs", "Waraton Bulirs", "2026-11-14T00:00:00Z")]
    env.kalshi.kevents["KXNBAGAME"] = [kevent("NBA", "26NOV13", ("WAR", "Waraton"), ("NO", "Noraton"))]
    env.clock.set("2026-11-13T17:00:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T19:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    for c in ("c1", "c4", "c9"):
        assert r[c]["reason"] == "no_team_named", c
    for c, team in (("c2", "Noraton Pelirs"), ("c3", "Noraton Pelirs"), ("c5", "Noraton Pelirs"),
                    ("c6", "Noraton Pelirs"),
                    ("c7", "Waraton Bulirs"), ("c8", "Waraton Bulirs"), ("c10", "Noraton Pelirs")):
        assert (r[c]["status"], r[c]["team"]) == ("matched", team), (c, r[c]["reason"])
    monkeypatch.setattr(pg, "WORDS_FILE", tmp_path / "missing", raising=False)
    fallback = pg._english_words()
    assert "no" in fallback and "was" in fallback and len(fallback) < 2000


# ---------------------------------------------------------------- the grading store's own copy of the word list

def _word_env(tmp_path, capsys):
    """"Take NO tonight ML" is not graded while "no" is an English word, and names the Noraton team once it is not."""
    w = PickWorld()
    w.thread()
    for cid, text in (("c2", "Take NO ML"), ("c9", "Take NO tonight ML")):
        w.comment(cid, "a" + cid, [("2026-11-13T18:00:00Z", {"market": "moneyline", "selection": text}, "-110")])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Noraton Pelirs", "Waraton Bulirs", "2026-11-14T00:00:00Z")]
    env.kalshi.kevents["KXNBAGAME"] = [kevent("NBA", "26NOV13", ("WAR", "Waraton"), ("NO", "Noraton"))]
    env.clock.set("2026-11-13T17:00:00Z")
    env.out(capsys, "schedule")
    return env


def _as_layout_3_of_3da5011(env):
    """The store as 3da5011 wrote it: layout 3, no word-list table (and so none of its triggers)."""
    c = sqlite3.connect(str(env.root / pg.STORE_FILE))
    for t in ("no_update_word_lists", "no_delete_word_lists", "no_replace_word_lists"):
        c.execute("DROP TRIGGER IF EXISTS %s" % t)
    c.execute("DROP TABLE IF EXISTS word_lists")
    c.commit()
    assert c.execute("SELECT version FROM layout").fetchone()[0] == 3
    c.close()


def _word_rows(env, fp):
    s = env.store()
    try:
        return {r["comment_id"]: r for r in s.latest_matches(fp)}
    finally:
        s.close()


def test_a_store_of_3da5011_takes_its_copy_of_the_word_list_on_its_first_match_and_keeps_it(
        tmp_path, capsys, monkeypatch):
    words = tmp_path / "words"
    words.write_text("no\nway\nis\nthe\nplay\ntake\ntonight\n")
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    env = _word_env(tmp_path, capsys)
    _as_layout_3_of_3da5011(env)
    before = env.out(capsys, "status")                                       # read only: the old store has no copy
    assert before["word_list"] == {"kept": False, "source": "file %s" % words, "words": 7}
    env.clock.set("2026-11-13T19:00:00Z")
    first = env.out(capsys, "match")
    st = env.out(capsys, "status")
    kept = st["word_list"]
    assert kept["kept"] is True and kept["source"] == "file %s" % words and kept["words"] == 7
    assert kept["at"] == "2026-11-13T19:00:00Z"
    assert st["fingerprint"] == first["fingerprint"] == pg.fingerprint()     # the copy is the file it was taken from
    r = _word_rows(env, first["fingerprint"])
    assert r["c9"]["reason"] == "no_team_named" and r["c2"]["status"] == "matched"

    words.write_text("way\nis\nthe\nplay\ntake\ntonight\nlock\n")            # a system update: "no" is gone
    os.utime(words, ns=(1, 1))
    assert pg.fingerprint() != first["fingerprint"]                          # the system file's own fingerprint moved
    env.clock.set("2026-11-13T20:00:00Z")
    again = env.out(capsys, "match")
    assert again["fingerprint"] == first["fingerprint"] and again["rows_written"] == 0
    assert again["by_outcome"] == first["by_outcome"]
    assert _word_rows(env, first["fingerprint"]) == r                         # no match row changed
    assert env.out(capsys, "status")["word_list"] == kept                     # the copy was not rewritten
    env.clock.set("2026-11-14T20:00:00Z")
    env.out(capsys, "daily")
    assert env.out(capsys, "status")["word_list"] == kept

    c = sqlite3.connect(str(env.root / pg.STORE_FILE))                        # and cannot be
    try:
        for sql in ("UPDATE word_lists SET raw = 'x'", "DELETE FROM word_lists",
                    "INSERT INTO word_lists (id, source, words, sha256, raw, at) VALUES (1, 's', 1, 'h', 'x', 't')"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(sql)
        assert c.execute("SELECT COUNT(*) FROM word_lists").fetchone()[0] == 1
    finally:
        c.close()


def test_a_frozen_rule_survives_a_change_of_the_system_word_list(tmp_path, capsys, monkeypatch):
    """A result read freezes the fingerprint; a later system update of the word list leaves every command running."""
    words = tmp_path / "words"
    words.write_text("no\nway\nis\nthe\nplay\ntake\ntonight\n")
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    env = _word_env(tmp_path, capsys)
    env.clock.set("2026-11-13T19:00:00Z")
    fp = env.out(capsys, "match")["fingerprint"]
    c = sqlite3.connect(str(env.root / pg.STORE_FILE))
    c.execute("INSERT INTO frozen (id, fingerprint, at) VALUES (1, ?, '2026-11-13T19:00:00Z')", (fp,))
    c.commit()
    c.close()
    words.write_text("way\n")
    os.utime(words, ns=(2, 2))
    env.clock.set("2026-11-13T20:00:00Z")
    assert env.out(capsys, "status")["fingerprint"] == fp
    assert env.out(capsys, "match")["fingerprint"] == fp


def test_without_the_system_word_list_the_store_keeps_the_built_in_one(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(pg, "WORDS_FILE", tmp_path / "missing", raising=False)
    env = _word_env(tmp_path, capsys)
    n = len(set(pg.FALLBACK_WORDS))
    assert env.out(capsys, "status")["word_list"] == {"kept": False, "source": "built-in fallback", "words": n}
    env.clock.set("2026-11-13T19:00:00Z")
    fp = env.out(capsys, "match")["fingerprint"]
    kept = env.out(capsys, "status")["word_list"]
    assert kept["kept"] is True and kept["source"] == "built-in fallback" and kept["words"] == n
    (tmp_path / "missing").write_text("way\n")                               # the file appears later: not read
    env.clock.set("2026-11-13T20:00:00Z")
    assert env.out(capsys, "match")["fingerprint"] == fp
    assert env.out(capsys, "status")["word_list"] == kept


def test_a_selection_in_capitals_keeps_its_codes_unless_it_holds_another_word(tmp_path, capsys, monkeypatch):
    """Clarification 20: the capitals rule takes the codes only from a selection holding a word besides team codes and
    the bet's markers (ML, a line, over / under and o / u, numbers, "/", "@", "vs" between two codes)."""
    words = tmp_path / "words"
    words.write_text("no\nway\nstrong\nis\n")
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    sel = {"p1": {"market": "moneyline", "selection": "LAL ML"},
           "p2": {"market": "spread", "selection": "BOS -4.5", "line": -4.5, "league": "NBA"},
           "p3": {"market": "total", "selection": "KC/BUF OVER 47.5", "line": 47.5},
           "p4": {"market": "total", "selection": "KC @ BUF O47.5", "line": 47.5},
           "p5": {"market": "total", "selection": "KC VS BUF UNDER 47", "line": 47},
           "p6": {"market": "moneyline", "selection": "MTL ML"},
           "n1": {"market": "moneyline", "selection": "IND STRONG ML"},      # IND is not English: the capitals rule
           "n2": {"market": "moneyline", "selection": "NO WAY ML"},
           "n3": {"market": "total", "selection": "KC/BUF OVER 47.5 LOCK", "line": 47.5},
           "s1": {"market": "moneyline", "selection": "BOS ML"},             # the NBA's and the NHL's code
           "s2": {"market": "spread", "selection": "BOS -4.5", "line": -4.5},
           "s3": {"market": "moneyline", "selection": "BOS ML", "league": "NHL"}}
    w = PickWorld()
    w.thread()
    for cid, f in sel.items():
        w.comment(cid, "a" + cid, [("2026-11-13T18:00:00Z", f, "-110")])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("nba1", "Lalaton Lakirs", "Bosaton Celirs", "2026-11-14T00:00:00Z")]
    env.odds.events[SPORT["NFL"]] = [
        ev("nfl1", "Kanaton Chiefirs", "Bufaton Bilirs", "2026-11-14T01:00:00Z", SPORT["NFL"]),
        ev("nfl2", "Indaton Coltirs", "Noraton Saintirs", "2026-11-14T01:00:00Z", SPORT["NFL"])]
    env.odds.events[SPORT["NHL"]] = [ev("nhl1", "Bosaton Brirs", "Montaton Canirs", "2026-11-14T00:30:00Z",
                                        SPORT["NHL"])]
    env.kalshi.kevents[SERIES["NBA"]] = [kevent("NBA", "26NOV13", ("BOS", "Bosaton"), ("LAL", "Lalaton"))]
    env.kalshi.kevents[SERIES["NFL"]] = [kevent("NFL", "26NOV13", ("BUF", "Bufaton"), ("KC", "Kanaton")),
                                         kevent("NFL", "26NOV13", ("NO", "Noraton"), ("IND", "Indaton"))]
    env.kalshi.kevents[SERIES["NHL"]] = [kevent("NHL", "26NOV13", ("MTL", "Montaton"), ("BOS", "Bosaton"))]
    env.clock.set("2026-11-13T17:00:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T19:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    for c, team in (("p1", "Lalaton Lakirs"), ("p2", "Bosaton Celirs"), ("p6", "Montaton Canirs"),
                    ("s3", "Bosaton Brirs")):
        assert (r[c]["status"], r[c]["team"]) == ("matched", team), (c, r[c]["reason"])
    for c in ("p3", "p4", "p5"):
        assert (r[c]["status"], r[c]["game_id"], r[c]["side"]) == ("matched", "nfl1", None), (c, r[c]["reason"])
    for c in ("n1", "n2", "n3", "s1", "s2"):
        assert r[c]["reason"] == "no_team_named", (c, r[c]["status"], r[c].get("team"))


# ---------------------------------------------------------------- the store

def test_the_grading_store_is_append_only_and_refuses_a_clock_behind(tmp_path, capsys):
    env = Env(tmp_path, two_league_world("NBA"))
    kings_schedule(env)
    env.out(capsys, "schedule")
    db = sqlite3.connect(env.root / pg.STORE_FILE)
    for sql in ("UPDATE schedules SET league = 'X'", "DELETE FROM schedules",
                "INSERT OR REPLACE INTO schedules SELECT * FROM schedules LIMIT 1"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(sql)
    db.close()
    env.clock.set("2026-11-13T15:59:59Z")
    with pytest.raises(pg.ClockBehind):
        env.run("match")
    assert pg._cli(["--store", str(env.root), "--picks", str(env.picks), "match"], clock=env.clock) == 2


def test_a_store_of_another_layout_is_refused(tmp_path):
    p = tmp_path / "g" / pg.STORE_FILE
    p.parent.mkdir()
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, at TEXT NOT NULL, command TEXT NOT NULL, "
               "fingerprint TEXT NOT NULL)")
    db.commit()
    db.close()
    for ro in (False, True):
        with pytest.raises(pg.Refused, match="layout"):
            pg.GradingStore(p, readonly=ro)


def test_status_reads_only_and_one_writer_at_a_time(tmp_path, capsys):
    env = Env(tmp_path, two_league_world("NBA"))
    with pytest.raises(pg.Refused):
        env.run("status")
    kings_schedule(env)
    env.out(capsys, "schedule")
    before = (env.root / pg.STORE_FILE).read_bytes()
    st = env.out(capsys, "status")
    assert st["schedules"] == {lg: 1 for lg in pg.LEAGUES} and (env.root / pg.STORE_FILE).read_bytes() == before
    with sl.file_lock(env.root / pg.LOCK_FILE, "held"):
        with pytest.raises(sl.Busy):
            env.run("match")


def other_world(seen="2026-11-13T11:00:00Z", tid="t9"):
    """A pick store of another identity: its first thread and payload differ."""
    w = PickWorld()
    w.thread(tid, seen=seen)
    w.comment("c1", "alice", [("2026-11-13T20:00:00Z", ml("Kings", "NBA"), "-110")], tid=tid)
    return w


def runner(odds=None):
    odds = odds or OddsTransport()

    def run(store, picks, *cmd):
        return pg.main(["--store", str(store), "--picks", str(picks), *(cmd or ("schedule",))],
                       odds_client=odds_client(odds), kalshi_client=kalshi_client(KalshiTransport()), clock=Clock())
    return run


def test_the_defaults_are_the_real_stores():
    assert REAL_DEFAULTS == (pg.REPO / "memory" / "pick_lists.sqlite", pg.REPO / "artifacts" / "pick_grading")


def test_the_real_pick_store_is_graded_into_the_default_grading_store_only(tmp_path, capsys, monkeypatch):
    real_picks, real_store = tmp_path / "real" / "picks.sqlite", tmp_path / "real" / "grading"
    two_league_world("NBA").build(real_picks)
    other_world().build(tmp_path / "test-picks.sqlite")
    monkeypatch.setattr(pg, "DEFAULT_PICKS", real_picks, raising=False)
    monkeypatch.setattr(pg, "DEFAULT_STORE", real_store, raising=False)
    run = runner()
    for store, picks in ((tmp_path / "elsewhere", real_picks),
                         (tmp_path / "elsewhere", tmp_path / "real" / ".." / "real" / "picks.sqlite"),
                         (real_store, tmp_path / "test-picks.sqlite")):
        for cmd in (("schedule",), ("status",), ("match",)):
            with pytest.raises(pg.Refused, match="one record"):
                run(store, picks, *cmd)
    assert not (tmp_path / "elsewhere").exists() and not real_store.exists()
    assert run(real_store, real_picks) == 0
    assert run(tmp_path / "any", tmp_path / "test-picks.sqlite") == 0


def test_a_copy_of_the_real_pick_store_does_not_reset_the_one_time_rules(tmp_path, capsys, monkeypatch):
    real_picks, real_store = tmp_path / "real" / "picks.sqlite", tmp_path / "real" / "grading"
    two_league_world("NBA").build(real_picks)
    monkeypatch.setattr(pg, "DEFAULT_PICKS", real_picks, raising=False)
    monkeypatch.setattr(pg, "DEFAULT_STORE", real_store, raising=False)
    run = runner()
    assert run(real_store, real_picks) == 0                            # the default grading store is bound now
    copies = tmp_path / "copies"
    copies.mkdir()
    shutil.copyfile(real_picks, copies / "cp.sqlite")                  # the verifier's case
    os.symlink(real_picks, copies / "symlink.sqlite")
    os.link(real_picks, copies / "hardlink.sqlite")
    shutil.copyfile(real_picks, copies / "appended.sqlite")
    db = sqlite3.connect(copies / "appended.sqlite")
    db.execute("INSERT INTO threads VALUES ('t2', 'sub', 'u', 'Pick of the Day - 11/14/26', 'bot', NULL, NULL, "
               "'2026-11-14T12:00:00Z')")
    db.commit()
    db.close()
    for name in ("cp", "symlink", "hardlink", "appended"):
        for cmd in (("schedule",), ("status",), ("match",), ("audit-sample", "--out", str(tmp_path / "audit"))):
            with pytest.raises(pg.Refused, match="one record"):
                run(tmp_path / ("store-" + name), copies / (name + ".sqlite"), *cmd)
        assert not (tmp_path / ("store-" + name)).exists()
    other_world().build(tmp_path / "other.sqlite")                    # the default store takes no other identity
    os.replace(tmp_path / "other.sqlite", real_picks)
    with pytest.raises(pg.Refused, match="one record"):
        run(real_store, real_picks, "match")
    other_world().build(tmp_path / "x.sqlite")
    other_world(seen="2026-11-13T10:00:00Z", tid="t8").build(tmp_path / "y.sqlite")
    assert run(tmp_path / "xs", tmp_path / "x.sqlite") == 0            # any store binds to its first pick store
    with pytest.raises(pg.Refused, match="one record"):
        run(tmp_path / "xs", tmp_path / "y.sqlite", "match")
    st = pg.GradingStore(real_store / pg.STORE_FILE, readonly=True)
    assert len(st.one("SELECT identity FROM bound")["identity"]) == 64
    st.close()


def test_a_store_of_another_layout_is_refused_without_creating_anything_beside_it(tmp_path):
    g = tmp_path / "g"
    g.mkdir()
    db = sqlite3.connect(g / pg.STORE_FILE)
    db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, at TEXT NOT NULL, command TEXT NOT NULL, "
               "fingerprint TEXT NOT NULL)")
    db.commit()
    db.close()
    two_league_world("NBA").build(tmp_path / "p.sqlite")
    for cmd in ("match", "schedule", "status"):
        with pytest.raises(pg.Refused, match="layout"):
            pg.main(["--store", str(g), "--picks", str(tmp_path / "p.sqlite"), cmd], clock=Clock())
    assert sorted(os.listdir(g)) == [pg.STORE_FILE]


# ---------------------------------------------------------------- credits

def test_the_free_events_call_is_recorded_at_cost_zero_and_the_monthly_cap_refuses_before_the_call(tmp_path, capsys):
    env = Env(tmp_path, two_league_world())
    env.out(capsys, "schedule")
    led = sl.CreditLedger(env.root / "credits.sqlite")
    calls = led.calls()
    assert [c["rule"] for c in calls] == ["events"] * 5 and {c["charged"] for c in calls} == {0}
    assert all("apiKey" not in c["params"] for c in calls)
    led.db.close()
    for sport in ("americanfootball_nfl", "basketball_nba"):
        env.odds.hist[(sport, "2026-11-12T11:00:00Z")] = {"timestamp": "2026-11-12T10:55:00Z",
                                                          "data": [ev("x" + sport, "A Team", "B Team",
                                                                      "2026-11-13T00:00:00Z", sport)]}
    env.clock.set("2026-11-13T17:00:00Z")
    out = env.out(capsys, "--monthly-cap", "1", "schedule", "--date", "2026-11-12")
    assert out["NFL"]["kind"] == "historical" and out["NBA"] == "error: CreditCapError"
    assert env.odds.kinds().count("historical") == 1        # the refused calls were never sent
    out = env.out(capsys, "--monthly-cap", "1", "schedule", "--date", "2026-11-12")
    assert out["NFL"] == "already stored" and env.odds.kinds().count("historical") == 1
    s = env.store()
    as_of = s.one("SELECT as_of, fetched_at FROM schedules WHERE kind = 'historical'")
    s.close()
    assert as_of["as_of"] == "2026-11-12T10:55:00Z"         # a historical list counts at its own timestamp


# ---------------------------------------------------------------- Question A's purchase rule and the fee

SEEN = ts("2026-11-13T23:00:30Z")
START = ts("2026-11-14T00:00:00Z")


def minute(k):
    """The end of the candle k minutes after the minute of SEEN (SEEN is 30 seconds into its minute)."""
    return SEEN - 30 + 60 * k


@pytest.mark.parametrize("candles, start, want", [
    ([(minute(1), .48, .50)], START, "reason:no_candle"),                 # ends 30 s after: too early, nothing else
    ([(SEEN + 59, .48, .50)], START, "reason:no_candle"),                 # 59 seconds after: too early
    ([(SEEN + 59, .40, .42), (SEEN + 60, .48, .50)], START, .50),
    ([(minute(1), .40, .42), (minute(2), .48, .50)], START, .50),         # the first ending >= 60 s after
    ([(SEEN + 60, .48, .50)], START, .50),                                # exactly 60 s after
    ([(SEEN + 600, .48, .50)], START, .50),                               # exactly 10 minutes after
    ([(SEEN + 601, .48, .50)], START, "reason:no_candle_within_10_minutes"),
    ([(minute(3), .48, .50)], minute(3), "reason:no_candle"),             # ends at the start: never read
    ([(minute(3), .48, .50)], minute(3) + 1, .50),
    ([(minute(2), .90, .95)], START, .95),
    ([(minute(2), .91, .96)], START, "reason:ask_above_0.95"),
    ([(minute(2), .45, .50)], START, .50),                                # a 5-cent spread
    ([(minute(2), .44, .50)], START, "reason:spread_above_5_cents"),
    ([(minute(2), .44, .50), (minute(3), .49, .50)], START, "reason:spread_above_5_cents"),   # no second try
    ([(minute(2), None, .50)], START, "reason:no_bid_ask"),
    ([(minute(2), None, .50), (minute(3), .49, .50)], START, "reason:no_bid_ask"),            # no bid: nothing bought
    ([(minute(2), 0.0, .03)], START, "reason:no_bid_ask"),
])
def test_purchase_minute_rule_at_every_edge(candles, start, want):
    got = pg.purchase_quote([kd.normalize_candle(kcandle(e, b, a)) for e, b, a in candles], SEEN, start)
    assert got.get("ask") == want if not isinstance(want, str) else got == {"reason": want.split(":")[1]}


def test_purchase_never_reads_a_candle_after_the_start_and_says_a_minute_without_a_bid_is_never_bought():
    late = [kd.normalize_candle(kcandle(START + 60 * k, .48, .50)) for k in range(5)]
    assert pg.purchase_quote(late, START - 30, START) == {"reason": "no_candle"}
    assert "no bid" in pg.purchase_quote.__doc__


def test_fee_is_kalshis_taker_fee_rounded_up_on_100_contracts():
    assert sl.kalshi_fee(0.50) == pytest.approx(0.0175)
    assert sl.kalshi_fee(0.95) == pytest.approx(0.0034)     # 7 x 95 x 5 = 3325 / 100 -> 33.25 -> 34 cents a hundred
    assert sl.kalshi_fee(0.01) == pytest.approx(0.0007)


# ---------------------------------------------------------------- Question B's grading

FINAL = {"home": 104, "away": 100}


@pytest.mark.parametrize("pick, final, want", [
    ({"market": "moneyline", "side": "home"}, FINAL, 1.0),
    ({"market": "moneyline", "side": "away"}, FINAL, -1.0),
    ({"market": "moneyline", "side": "home"}, {"home": 3, "away": 3}, 0.0),              # a tie pushes
    ({"market": "spread", "side": "home", "line": -3.5}, FINAL, 1.0),
    ({"market": "spread", "side": "home", "line": -4.0}, FINAL, 0.0),                    # push
    ({"market": "spread", "side": "home", "line": -4.5}, FINAL, -1.0),
    ({"market": "spread", "side": "away", "line": 4.0}, FINAL, 0.0),
    ({"market": "spread", "side": "away", "line": 4.5}, FINAL, 1.0),
    ({"market": "total", "side": None, "line": 204.0, "ou": "over"}, FINAL, 0.0),
    ({"market": "total", "side": None, "line": 203.5, "ou": "over"}, FINAL, 1.0),
    ({"market": "total", "side": None, "line": 203.5, "ou": "under"}, FINAL, -1.0),
    ({"market": "spread", "side": "away", "line": 1.5}, {"home": 3, "away": 2}, 1.0),         # OT final 3-2
    ({"market": "total", "side": None, "line": 5.5, "ou": "under"}, {"home": 3, "away": 2}, 1.0),
])
def test_spread_total_and_moneyline_grading_with_pushes_and_overtime(pick, final, want):
    r = dict({"decimal_odds": 2.0, "line": None, "ou": None}, **pick)
    got = pg.grade_b(r, final, None, False)
    assert got["value"] == want


def test_b_needs_odds_in_range_a_line_an_over_under_a_score_and_agreement_with_kalshi():
    base = {"market": "spread", "side": "home", "line": -3.5, "decimal_odds": 1.91, "ou": None}
    assert pg.grade_b(dict(base, decimal_odds=None), FINAL, None, False) == {"reason": "no_odds"}
    assert pg.grade_b(dict(base, decimal_odds=1.29), FINAL, None, False) == {"reason": "odds_out_of_range"}
    assert pg.grade_b(dict(base, decimal_odds=4.01), FINAL, None, False) == {"reason": "odds_out_of_range"}
    assert pg.grade_b(dict(base, decimal_odds=4.00), FINAL, None, False)["value"] == pytest.approx(3.0)
    assert pg.grade_b(dict(base, decimal_odds=1.30), FINAL, None, False)["value"] == pytest.approx(0.3)
    assert pg.grade_b(dict(base, line=None), FINAL, None, False) == {"reason": "no_line"}
    assert pg.grade_b(dict(base, market="total"), FINAL, None, False) == {"reason": "no_over_under"}
    assert pg.grade_b(base, None, None, False) == {"reason": "no_score"}
    assert pg.grade_b(base, "differ", None, False) == {"reason": "scores_differ"}
    assert pg.grade_b(base, FINAL, None, True) == {"reason": "kalshi_not_settled"}
    assert pg.grade_b(base, FINAL, "away", True) == {"reason": "winner_differs"}
    assert pg.grade_b(base, FINAL, "home", True)["value"] == pytest.approx(0.91)


def test_resampling_whole_days_is_reproducible_by_seed():
    days = ["2026-11-%02d" % (13 + i % 9) for i in range(60)]
    vals = [((i * 37) % 11 - 5) / 10 for i in range(60)]
    a = pg.day_interval(days, vals, seed=1, reps=10000)
    assert a == pg.day_interval(days, vals, seed=1, reps=10000)
    assert a != pg.day_interval(days, vals, seed=2, reps=10000)
    assert a[0] < sum(vals) / len(vals) < a[1]
    assert pg.day_interval(["d"] * 3, [0.5, 0.5, 0.5]) == [0.5, 0.5]       # one day: nothing to resample


def test_resampling_draws_whole_days_not_single_picks():
    # day A: one pick worth 1; day B: 99 picks worth 0. Whole days: the draw AA (a quarter of draws) has mean 1, BB 0.
    days, vals = ["A"] + ["B"] * 99, [1.0] + [0.0] * 99
    assert pg.day_interval(days, vals, reps=10000) == [0.0, 1.0]


# ---------------------------------------------------------------- the whole flow and its gates

N_GAMES = 22


DEV = {"title": TITLE, "thread": "2026-11-13T12:00:00Z", "seen": "2026-11-13T20:00:00Z",
       "start": "2026-11-14T00:00:00Z", "schedule": "2026-11-13T19:00:00Z", "match": "2026-11-13T23:00:00Z",
       "after": "2026-11-15T12:00:00Z", "event": "26NOV13"}
TEST = {"title": "Pick of the Day - 12/14/26 (Monday)", "thread": "2026-12-14T12:00:00Z", "seen": "2026-12-14T20:00:00Z",
        "start": "2026-12-15T00:00:00Z", "schedule": "2026-12-14T19:00:00Z", "match": "2026-12-14T23:00:00Z",
        "after": "2026-12-16T12:00:00Z", "event": "26DEC14"}


def big_world(day=DEV, n_authors=44, step=60):
    """N_GAMES NBA games at the day's start; author i backs the home team of game i % N_GAMES (moneyline), first seen
    at the day's `seen` + i * step seconds; authors 40 to 43 write a spread, a total and two props."""
    teams = team_names(2 * N_GAMES)
    games = [(f"g{k:02d}", "%s %s" % teams[2 * k], "%s %s" % teams[2 * k + 1]) for k in range(N_GAMES)]
    w = PickWorld()
    w.thread(title=day["title"], seen=day["thread"])
    for i in range(n_authors):
        gid, home, away = games[i % N_GAMES]
        seen = plus(day["seen"], step * i)
        if i == 40:
            f = {"market": "spread", "selection": home.split()[1] + " -2.5", "line": -2.5}
        elif i == 41:
            f = {"market": "total", "selection": home.split()[1] + "/" + away.split()[1] + " o210.5", "line": 210.5}
        elif i >= 42:
            f = {"market": "prop", "selection": "someone over 2.5"}
        else:
            f = ml(home.split()[1])
        w.comment("c%02d" % i, "author%02d" % i, [(seen, f, "+110")])
    return w, games


def setup_big(tmp_path, capsys, day=DEV, **kw):
    w, games = big_world(day, **kw)
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, day["start"]) for g, h, a in games]
    env.clock.set(day["schedule"])
    env.out(capsys, "schedule")
    env.clock.set(day["match"])
    env.out(capsys, "match")
    return env, games


def settle_world(env, games, day=DEV, start=None, candles_from=None, n_candles=300):
    """Kalshi markets (home wins game k when k is even), candles every minute from the day's `seen`, and the Odds
    API's finals."""
    start = start or day["start"]
    expected = plus(start, 3 * 3600)
    rows = []
    for k, (gid, home, away) in enumerate(games):
        event = "KXNBAGAME-%sG%02d" % (day["event"], k)
        hw = k % 2 == 0
        rows += [kmarket(event, "H%02d" % k, home.split()[0], "yes" if hw else "no", expected),
                 kmarket(event, "A%02d" % k, away.split()[0], "no" if hw else "yes", expected)]
        for code in ("H%02d" % k, "A%02d" % k):
            ticker = event + "-" + code
            base = ts(candles_from or day["seen"]) // 60 * 60
            env.kalshi.candles[ticker] = [kcandle(base + 60 * j, .48, .50) for j in range(0, n_candles)]
    env.kalshi.markets["KXNBAGAME"] = env.kalshi.markets.get("KXNBAGAME", []) + rows
    env.odds.scores["basketball_nba"] = env.odds.scores.get("basketball_nba", []) + [
        {"id": gid, "sport_key": "basketball_nba", "commence_time": start, "completed": True, "home_team": h,
         "away_team": a, "scores": [{"name": h, "score": "110" if k % 2 == 0 else "100"},
                                    {"name": a, "score": "100" if k % 2 == 0 else "110"}]}
        for k, (gid, h, a) in enumerate(games)]


def test_no_result_is_read_before_the_audit_pass_and_the_flow_after_it(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys)
    settle_world(env, games)
    env.clock.set("2026-11-15T12:00:00Z")
    for cmd in (["prices"], ["results"], ["evaluate", "--period", "development"]):
        with pytest.raises(pg.Refused, match="audit-pass"):
            env.run(*cmd)
    assert env.kalshi.result_urls() == [] and "scores" not in env.odds.kinds()
    out = env.out(capsys, "daily")
    assert out["gate_open"] is False and "prices" not in out and env.kalshi.result_urls() == []
    with pytest.raises(pg.Refused):
        env.run("audit-pass", "--sample", "1", "--wrong", "0")              # no sample yet
    with pytest.raises(pg.Refused, match="outside the repository"):
        env.run("audit-sample", "--out", str(pg.REPO / "artifacts"))
    a = env.out(capsys, "audit-sample", "--out", str(env.audit_dir))
    text = Path(a["path"]).read_text()
    assert a["picks"] == 40 and text.count("\n## ") == 40 and "Comment text (data):" in text
    assert env.out(capsys, "audit-pass", "--sample", str(a["audit_id"]), "--wrong", "2")["gate_open"] is True
    p = env.out(capsys, "prices")
    assert p["pairing"]["NBA"]["paired_now"] == N_GAMES and p["candles"]["candles_stored"] > 0
    r = env.out(capsys, "results")
    assert r["scores"]["NBA"]["games_listed"] == N_GAMES
    res = env.out(capsys, "evaluate", "--period", "development")
    # authors 0..39 back the home team of game i % 22: games 0..17 twice, 18..21 once
    a1, a2, a3 = res["A"]["1"], res["A"]["2"], res["A"]["3"]
    assert (a1["n"], a1["days"], a1["teams"]) == (N_GAMES, 1, N_GAMES)
    fee = sl.kalshi_fee(0.50)
    wins = sum(1 for k in range(N_GAMES) if k % 2 == 0)
    assert a1["mean"] == pytest.approx((wins * 1.0) / N_GAMES - 0.50 - fee)
    assert a2["n"] == 18 and a2["fewer_authors"] == 4 and a3["n"] == 0 and a3["mean"] is None
    b = res["B"]
    ml_vals = [1.1 if (i % N_GAMES) % 2 == 0 else -1.0 for i in range(40)]
    sp = 1.1 if 40 % N_GAMES % 2 == 0 else -1.0
    tot = -1.0                                             # 210 points against over 210.5
    assert b["n"] == 42 and b["mean"] == pytest.approx((sum(ml_vals) + sp + tot) / 42)
    assert res["by_outcome"] == {"market_not_graded": 2, "matched": 42}
    log = sl.scoring_entries(env.root)
    assert [e["kind"] for e in log] == ["prices", "results", "evaluate_development"]
    assert all(len(e["grading_sha256"]) == 64 for e in log)
    k = sqlite3.connect(env.root / "kalshi.sqlite")
    assert k.execute("SELECT MAX(end_ts) FROM candles").fetchone()[0] < ts(DEV["start"])   # nothing past a start
    k.close()


def test_no_candle_at_or_after_the_earliest_start_is_fetched_or_bought(tmp_path, capsys):
    """Lists fetched before the game say 00:00; a list fetched at 23:40 says it began at 23:30. Candles every minute
    from 23:00 to 01:00; the picks are first seen from 23:00, every 45 seconds."""
    day = dict(DEV, seen="2026-11-13T23:00:00Z", match="2026-11-13T23:45:00Z")
    w, games = big_world(day, step=45)
    env = Env(tmp_path, w)
    env.clock.set(day["schedule"])
    env.odds.events["basketball_nba"] = [ev(g, h, a, "2026-11-14T00:00:00Z") for g, h, a in games]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T23:40:00Z")
    env.odds.events["basketball_nba"] = [ev(g, h, a, "2026-11-13T23:30:00Z") for g, h, a in games]
    env.out(capsys, "schedule")
    env.clock.set(day["match"])
    env.out(capsys, "match")
    settle_world(env, games, day, start="2026-11-13T23:30:00Z", candles_from="2026-11-13T23:00:00Z", n_candles=120)
    env.clock.set("2026-11-15T12:00:00Z")
    env.audit(capsys)
    env.out(capsys, "prices")
    env.out(capsys, "results")
    res = env.out(capsys, "evaluate", "--period", "development")
    k = sqlite3.connect(env.root / "kalshi.sqlite")
    assert k.execute("SELECT MAX(end_ts) FROM candles").fetchone()[0] < ts("2026-11-13T23:30:00Z")
    k.close()
    rows = env.rows()
    assert {r["start"] for r in rows.values() if r["status"] == "matched"} == {"2026-11-13T23:30:00Z"}
    assert res["A"]["1"]["n"] == N_GAMES                    # every group's first pick is seen by 23:15:45
    two = res["A"]["2"]                                     # second picks: authors 22..39, seen 23:16:30 to 23:29:15

    def first_candle_end(i):                                # candles end on the minute
        return -(-(ts("2026-11-13T23:00:00Z") + 45 * i + 60) // 60) * 60
    late = [i for i in range(22, 40) if first_candle_end(i) >= ts("2026-11-13T23:30:00Z")]
    assert late == [38, 39]
    assert two["n"] == 18 - len(late) and two["not_bought"] == {"no_candle": len(late)}


def test_one_reading_per_rule(tmp_path, capsys, monkeypatch):
    env, games = setup_big(tmp_path, capsys)
    with pytest.raises(SystemExit):
        env.run("audit-sample", "--out", str(env.audit_dir), "--seed", "5")      # the reader does not choose the draw
    a = env.out(capsys, "audit-sample", "--out", str(env.audit_dir))
    again = env.out(capsys, "audit-sample", "--out", str(env.audit_dir))
    assert (again["audit_id"], again["path"]) == (a["audit_id"], a["path"])        # one draw for the fingerprint
    with pytest.raises(SystemExit):
        env.run("audit-pass", "--wrong", "0")                                     # a pass names its sample
    with pytest.raises(pg.Refused, match="sample"):
        env.run("audit-pass", "--sample", str(a["audit_id"] + 1), "--wrong", "0")
    assert env.out(capsys, "audit-pass", "--sample", str(a["audit_id"]), "--wrong", "3")["gate_open"] is False
    env.clock.set("2026-11-15T12:00:00Z")
    with pytest.raises(pg.Refused, match="closed"):
        env.run("audit-sample", "--out", str(env.audit_dir))
    with pytest.raises(pg.Refused):
        env.run("audit-pass", "--sample", str(a["audit_id"]), "--wrong", "0")
    env.out(capsys, "match")
    with pytest.raises(pg.Refused, match="closed"):
        env.run("audit-sample", "--out", str(env.audit_dir))
    with pytest.raises(pg.Refused, match="audit"):
        env.run("results")
    s = env.store()
    keys = s.one("SELECT keys FROM audits")["keys"]
    s.close()
    env2, _ = setup_big(tmp_path / "two", capsys)                                  # the same world draws the same 40
    env2.out(capsys, "audit-sample", "--out", str(env2.audit_dir))
    s = env2.store()
    assert s.one("SELECT keys FROM audits")["keys"] == keys
    s.close()
    changed = _changed_rule(tmp_path, monkeypatch)                                 # a changed rule: a new reading
    _out_with(changed, env, capsys, "match")
    b = _out_with(changed, env, capsys, "audit-sample", "--out", str(env.audit_dir))
    assert b["audit_id"] != a["audit_id"]
    assert _out_with(changed, env, capsys, "audit-pass", "--sample", str(b["audit_id"]), "--wrong", "0")["gate_open"]


def test_results_wait_for_a_match_run_after_the_latest_list(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys)
    settle_world(env, games)
    env.audit(capsys)
    env.clock.set("2026-11-15T11:00:00Z")
    env.odds.events["basketball_nba"] = env.odds.events["basketball_nba"] + [
        ev("late", "Vipeton Pevirs", "Pevoton Vopirs", "2026-11-16T00:00:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-15T12:00:00Z")
    for cmd in (["prices"], ["results"], ["evaluate", "--period", "development"]):
        with pytest.raises(pg.Refused, match="match"):
            env.run(*cmd)
    env.out(capsys, "match")
    env.out(capsys, "results")


def _changed_rule(tmp_path, monkeypatch):
    """This module with one more league word in its source: a changed matching rule (a runtime change of a value is
    not one: the fingerprint is of the source). Its defaults point into the temporary folder too."""
    mod = _copy_with_text(tmp_path, "changed_rule", lambda src: src.replace(
        '    "nfl": ("NFL",), "nba": ("NBA",),', '    "probasketball": ("NBA",), "nfl": ("NFL",), "nba": ("NBA",),', 1))
    assert mod.fingerprint() != pg.fingerprint()
    monkeypatch.setattr(mod, "DEFAULT_PICKS", pg.DEFAULT_PICKS)
    monkeypatch.setattr(mod, "DEFAULT_STORE", pg.DEFAULT_STORE)
    return mod


def _run_with(mod, env, *args):
    argv = ["--store", str(env.root), "--picks", str(env.picks), *args]
    return mod.main(argv, odds_client=odds_client(env.odds), kalshi_client=kalshi_client(env.kalshi), clock=env.clock)


def _out_with(mod, env, capsys, *args):
    capsys.readouterr()
    assert _run_with(mod, env, *args) == 0
    return json.loads(capsys.readouterr().out)


def test_a_changed_fingerprint_after_a_result_makes_every_command_refuse(tmp_path, capsys, monkeypatch):
    env, games = setup_big(tmp_path, capsys)
    settle_world(env, games)
    env.clock.set("2026-11-15T12:00:00Z")
    env.audit(capsys)
    env.out(capsys, "match")                                # before a result: the rule may still be repaired
    env.out(capsys, "results")
    changed = _changed_rule(tmp_path, monkeypatch)
    for cmd in (["status"], ["schedule"], ["match"], ["prices"], ["results"], ["daily"], ["choose", "--version", "1"],
                ["evaluate", "--period", "development"], ["audit-sample", "--out", str(env.audit_dir)]):
        with pytest.raises(changed.Refused, match="frozen"):
            _run_with(changed, env, *cmd)
    assert env.run("status") == 0


def test_a_changed_fingerprint_before_any_result_needs_a_new_audit(tmp_path, capsys, monkeypatch):
    env, games = setup_big(tmp_path, capsys)
    env.audit(capsys)
    changed = _changed_rule(tmp_path, monkeypatch)
    env.clock.set("2026-11-15T12:00:00Z")
    capsys.readouterr()
    assert _run_with(changed, env, "match") == 0             # every pick gets a row under the new fingerprint
    with pytest.raises(changed.Refused, match="audit-pass"):
        _run_with(changed, env, "results")
    s = env.store()
    assert len(s.latest_matches(changed.fingerprint())) == 44
    assert {r["fingerprint"] for r in s.rows("SELECT DISTINCT fingerprint FROM matches")} == \
        {changed.fingerprint(), s.one("SELECT fingerprint FROM audits")["fingerprint"]}
    s.close()


# ---------------------------------------------------------------- invariant 5: the fingerprint covers what match reaches

NOT_A_MATCH_RUN = {"cmd_schedule", "cmd_audit_sample", "cmd_audit_pass", "cmd_prices", "cmd_results", "cmd_evaluate",
                   "cmd_choose", "status"}             # the other commands main dispatches to (daily writes match rows)


def _reachable_from_match():
    """The module's functions and methods that a `match` run can reach, read from the source with ast, from the entry
    point down (`_cli`, which the module's `__main__` line calls; then `main`, its set-up and `cmd_match`): names of
    module-level functions other than the other commands, the special methods (`__init__`...) of the module's classes
    named, and methods of the module's classes by attribute name; with the helpers of sports_lines and pick_lists
    named on the way."""
    tree = ast.parse(Path(pg.__file__).read_text())
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    assert {n for n in funcs if n.startswith("cmd_")} - {"cmd_match", "cmd_daily"} <= NOT_A_MATCH_RUN
    methods = {}
    for c in classes.values():
        for m in c.body:
            if isinstance(m, ast.FunctionDef):
                methods.setdefault(m.name, []).append((c.name, m))
    seen, todo, helpers = {}, [("", "_cli", funcs["_cli"])], set()
    while todo:
        cls, name, node = todo.pop()
        if (cls, name) in seen:
            continue
        seen[(cls, name)] = node
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in funcs and sub.id not in NOT_A_MATCH_RUN:
                todo.append(("", sub.id, funcs[sub.id]))
            elif isinstance(sub, ast.Name) and sub.id in classes:
                todo += [(sub.id, m.name, m) for m in classes[sub.id].body
                         if isinstance(m, ast.FunctionDef) and m.name.startswith("__")]
            elif isinstance(sub, ast.Attribute):
                if isinstance(sub.value, ast.Name) and sub.value.id in ("sl", "pl"):
                    helpers.add((sub.value.id, sub.attr))
                for c, m in methods.get(sub.attr, ()):
                    todo.append((c, sub.attr, m))
    return seen, helpers


def _copy_with_probe(tmp_path, n, node=None):
    lines = Path(pg.__file__).read_text().splitlines(keepends=True)
    if node is not None:
        body = node.body
        k = 1 if (isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant)
                  and isinstance(body[0].value.value, str) and len(body) > 1) else 0
        at = body[k].lineno - 1 if k < len(body) else node.end_lineno
        lines.insert(at, " " * body[k].col_offset + "_fingerprint_probe = 1\n")
    path = tmp_path / ("pg_copy_%d.py" % n)
    path.write_text("".join(lines))
    spec = importlib.util.spec_from_file_location("pg_copy_%d" % n, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_function_match_can_reach_is_inside_the_fingerprint(tmp_path):
    reach, helpers = _reachable_from_match()
    assert ("", "cmd_match") in reach and ("", "_match_thread") in reach and ("", "evaluate_comment") in reach
    assert ("", "main") in reach and ("Grader", "__init__") in reach and ("GradingStore", "__init__") in reach
    assert ("", "_one_record") in reach and ("", "cmd_evaluate") not in reach and ("", "cmd_daily") in reach
    base = _copy_with_probe(tmp_path, 0).fingerprint()
    assert base == pg.fingerprint()                           # the fingerprint does not depend on where the file is
    missed = []
    for n, ((cls, name), node) in enumerate(sorted(reach.items(), key=lambda x: x[0]), 1):
        if _copy_with_probe(tmp_path, n, node).fingerprint() == base:
            missed.append("%s.%s" % (cls, name) if cls else name)
    assert missed == []
    scope = set(pg.fingerprint_scope()["functions"])
    for mod, attr in sorted(helpers):
        obj = getattr({"sl": sl, "pl": pl}[mod], attr)
        if callable(obj) and not isinstance(obj, type):
            assert "%s.%s" % (mod, attr) in scope, (mod, attr)
    assert "pl.Store.version_before" in scope


def _copy_with_text(tmp_path, name, change):
    path = tmp_path / ("pg_text_%s.py" % name)
    path.write_text(change(Path(pg.__file__).read_text()))
    spec = importlib.util.spec_from_file_location("pg_text_%s" % name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_comments_blank_lines_and_docstrings_do_not_change_the_fingerprint_but_code_constants_and_tables_do(tmp_path):
    base = pg.fingerprint()

    def comment(src):
        return src.replace("def cmd_match(gr: Grader) -> dict:\n",
                           "def cmd_match(gr: Grader) -> dict:\n    # a comment only\n\n    # and another\n", 1)

    def docstring(src):
        return src.replace('"""One version of a pick against the games of its window',
                           '"""Reworded: one version of a pick against the games of its window', 1)
    for name, change in (("comment", comment), ("docstring", docstring)):
        assert change(Path(pg.__file__).read_text()) != Path(pg.__file__).read_text(), name
        assert _copy_with_text(tmp_path, name, change).fingerprint() == base, name

    def statement(src):
        return src.replace("def cmd_match(gr: Grader) -> dict:\n",
                           "def cmd_match(gr: Grader) -> dict:\n    _unused = 1\n", 1)

    def constant(src):
        return src.replace("MIN_FORM_CHARS = 3", "MIN_FORM_CHARS = 4", 1)

    def table(src):
        return src.replace("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, ",
                           "CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, extra TEXT, ", 1)
    for name, change in (("statement", statement), ("constant", constant), ("table", table)):
        assert change(Path(pg.__file__).read_text()) != Path(pg.__file__).read_text(), name
        assert _copy_with_text(tmp_path, name, change).fingerprint() != base, name


def test_every_reading_ever_made_is_listed_by_status_every_evaluation_and_the_scoring_log(tmp_path, capsys, monkeypatch):
    env, games = setup_big(tmp_path, capsys)
    settle_world(env, games)
    a = env.out(capsys, "audit-sample", "--out", str(env.audit_dir))
    env.out(capsys, "audit-pass", "--sample", str(a["audit_id"]), "--wrong", "3")
    fp_a = pg.fingerprint()
    changed = _changed_rule(tmp_path, monkeypatch)                                 # a changed rule: a new reading
    _out_with(changed, env, capsys, "match")
    b = _out_with(changed, env, capsys, "audit-sample", "--out", str(env.audit_dir))
    _out_with(changed, env, capsys, "audit-pass", "--sample", str(b["audit_id"]), "--wrong", "0")
    env.clock.set("2026-11-15T12:00:00Z")
    _out_with(changed, env, capsys, "prices")
    _out_with(changed, env, capsys, "results")
    st = _out_with(changed, env, capsys, "status")
    got = [(x["fingerprint"], x["sample"], x["wrong"]) for x in st["readings"]]
    assert got == [(fp_a, a["audit_id"], 3), (changed.fingerprint(), b["audit_id"], 0)]
    assert all(x["at"] for x in st["readings"]) and st["fingerprints_audited"] == 2
    res = _out_with(changed, env, capsys, "evaluate", "--period", "development")
    assert res["readings"] == st["readings"] and res["fingerprints_audited"] == 2
    log = [e for e in sl.scoring_entries(env.root) if e["kind"] == "evaluate_development"]
    assert log[-1]["result"]["readings"] == st["readings"]


def test_the_scoring_code_is_pinned_at_the_choice(tmp_path, capsys, monkeypatch):
    env, games = setup_big(tmp_path, capsys)
    env.clock.set("2026-11-15T12:00:00Z")
    env.audit(capsys)
    ch = env.out(capsys, "choose", "--version", "1")
    assert ch["choice"] == 1 and len(ch["scoring_fingerprint"]) == 64
    assert "pg.purchase_quote" in pg.scoring_scope()["functions"] and "pg.cmd_evaluate" in pg.scoring_scope()["functions"]
    repaired = _copy_with_text(tmp_path, "ask_cap", lambda src: src.replace("MAX_ASK = 0.95", "MAX_ASK = 0.94", 1))
    monkeypatch.setattr(repaired, "DEFAULT_PICKS", pg.DEFAULT_PICKS)   # a repair of the scoring code after the choice
    monkeypatch.setattr(repaired, "DEFAULT_STORE", pg.DEFAULT_STORE)
    assert repaired.fingerprint() == pg.fingerprint() and repaired.scoring_fingerprint() != pg.scoring_fingerprint()
    env.clock.set("2027-04-04T12:00:00Z")                  # the test is scored once its games are over
    with pytest.raises(repaired.Refused, match="pinned"):
        _run_with(repaired, env, "evaluate", "--period", "test")
    assert not any(e["kind"] == "test_result" for e in sl.scoring_entries(env.root))
    res = _out_with(repaired, env, capsys, "evaluate", "--period", "test", "--code-changed", "the ask cap repaired")
    cc = res["code_changed"]
    assert cc["reason"] == "the ask cap repaired" and cc["at_choice"] == ch["scoring_fingerprint"]
    assert cc["now"] == repaired.scoring_fingerprint() != cc["at_choice"] and cc["at"]
    log = sl.scoring_entries(env.root)
    entry = [e for e in log if e["kind"] == "code_changed"][0]
    assert entry["reason"] == "the ask cap repaired" and entry["at_choice"] == ch["scoring_fingerprint"]
    assert [e for e in log if e["kind"] == "test_result"][0]["result"]["code_changed"] == cc


# ---------------------------------------------------------------- invariant 4: the test period, scored once at the end

def test_the_test_period_is_refused_before_the_counts_or_the_last_day_and_scored_once(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys)
    env.clock.set("2026-11-15T12:00:00Z")
    env.audit(capsys)
    with pytest.raises(pg.Refused, match="stored choice"):
        env.run("evaluate", "--period", "test")
    with pytest.raises(ValueError):
        env.run("choose", "--version", "4")
    assert env.out(capsys, "choose", "--version", "2")["choice"] == 2
    with pytest.raises(pg.Refused, match="once"):
        env.run("choose", "--version", "1")
    for now in ("2026-12-20T12:00:00Z", "2027-03-31T04:00:00Z",
                "2027-04-04T03:59:59Z"):                                    # 2027-04-03, 23:59:59 US-Eastern
        env.clock.set(now)
        with pytest.raises(pg.Refused, match="counts"):
            env.run("evaluate", "--period", "test")
    assert [e["kind"] for e in sl.scoring_entries(env.root)] == ["choice"]
    st = env.out(capsys, "status")
    assert st["test_counts"]["choice"] == 2 and st["test_counts"]["B_graded"] == 0
    assert "mean" not in json.dumps(st) and "ci95" not in json.dumps(st)
    with pytest.raises(SystemExit):
        env.run("evaluate", "--period", "test", "--more-data")
    env.clock.set("2027-04-04T04:00:00Z")                                   # 2027-04-04 00:00 US-Eastern
    first = env.out(capsys, "evaluate", "--period", "test")
    assert first["choice"] == 2 and first["A"]["2"]["n"] == 0 and first["A"]["2"]["mean"] == "not shown"
    assert first["pass_lines"] == {"A_counts_reached": False, "B_counts_reached": False, "A_passes": False,
                                   "B_passes": False}
    env.clock.set("2027-04-10T12:00:00Z")
    again = env.out(capsys, "evaluate", "--period", "test")
    assert "scored once" in again["note"] and again["pass_lines"] == first["pass_lines"]
    assert [e["kind"] for e in sl.scoring_entries(env.root)] == ["choice", "test_result", "test_result_shown"]
    s = env.store()
    assert s.one("SELECT COUNT(*) AS n FROM test_results")["n"] == 1
    s.close()


def test_test_period_picks_wait_for_a_choice(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys, TEST)
    settle_world(env, games, TEST)
    env.audit(capsys)
    env.clock.set(TEST["after"])
    r = env.out(capsys, "results")
    p = env.out(capsys, "prices")
    assert r["test_waits_for_choice"] == p["test_waits_for_choice"] == 42 and r["picks"] == p["moneyline_picks"] == 0
    assert "scores" not in env.odds.kinds() and env.kalshi.result_urls() == []
    env.out(capsys, "choose", "--version", "1")
    r = env.out(capsys, "results")
    assert r["picks"] == 42 and r["scores"]["NBA"]["games_listed"] == N_GAMES
    env.clock.set("2027-04-04T12:00:00Z")                  # the test is scored once its games are over
    res = env.out(capsys, "evaluate", "--period", "test")
    assert res["B"]["n"] == 42 and res["A"]["1"]["n"] == 0 and res["A"]["1"]["not_bought"] == {"no_candle": 22}


LATE = {"title": "Pick of the Day - 12/13/26 (Sunday)", "thread": "2026-12-13T12:00:00Z", "seen": "2026-12-13T20:00:00Z",
        "start": "2026-12-14T03:00:00Z", "schedule": "2026-12-13T19:00:00Z", "match": "2026-12-13T23:00:00Z",
        "event": "26DEC13"}


@pytest.mark.parametrize("now, bought", [("2026-12-14T10:59:59Z", True), ("2026-12-14T11:00:00Z", False)])
def test_without_a_choice_no_scores_are_bought_once_the_test_period_has_begun(tmp_path, capsys, now, bought):
    env, games = setup_big(tmp_path, capsys, LATE)
    settle_world(env, games, LATE)
    env.audit(capsys)
    env.clock.set(now)                                      # 06:00 US-Eastern on the test period's first day
    out = env.out(capsys, "results")
    assert ("scores" in env.odds.kinds()) is bought
    if not bought:
        assert out["scores"]["NBA"].startswith("refused: no choice stored")


@pytest.mark.parametrize("SEASON_DAYS, SEASON_GAMES, END", [
    (21, 16, 19),       # A's 20th day: 320 contracts and 320 picks, past 158 and 315
    (22, 15, 20),       # 300 picks on the 20th day reach the pass line, not the margin of 315: the 21st day ends it
])
def test_the_test_period_is_scored_once_both_counts_are_reached_and_ends_on_that_day(
        tmp_path, capsys, SEASON_DAYS, SEASON_GAMES, END):
    teams = team_names(2 * SEASON_GAMES)
    games = [("%s %s" % teams[2 * k], "%s %s" % teams[2 * k + 1]) for k in range(SEASON_GAMES)]
    w = PickWorld()
    days = [date(2026, 12, 14) + timedelta(days=d) for d in range(SEASON_DAYS)]

    def at(day, hour, minute=0, plus_days=0):
        return iso(datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC) + timedelta(days=plus_days))
    for d, day in enumerate(days):
        tid = "t%02d" % d
        w.thread(tid, "Pick of the Day - %d/%d/%02d" % (day.month, day.day, day.year % 100), at(day, 12))
        for i, (home, away) in enumerate(games):
            w.comment("c%02d_%02d" % (d, i), "author%02d" % i, [(at(day, 20, i), ml(home.split()[1]), "+110")], tid=tid)
    env = Env(tmp_path, w)
    by_day = []
    for d, day in enumerate(days):
        gl = [("d%02dg%02d" % (d, k), h, a) for k, (h, a) in enumerate(games)]
        by_day.append(gl)
        env.clock.set(at(day, 19))
        env.odds.events["basketball_nba"] = [ev(g, h, a, at(day, 0, plus_days=1)) for g, h, a in gl]
        env.out(capsys, "schedule")
        env.clock.set(at(day, 23))
        env.out(capsys, "match")
        if d == 2:
            env.audit(capsys)
            env.out(capsys, "choose", "--version", "1")
        if d >= 1:
            prev = days[d - 1]
            settle_world(env, by_day[d - 1], dict(DEV, seen=at(prev, 20), event=prev.strftime("%y%b%d").upper()),
                         start=at(prev, 0, plus_days=1), n_candles=30)
        if d >= 2:
            env.clock.set(at(day, 23, 30))
            env.out(capsys, "results")
    last = days[-1]
    settle_world(env, by_day[-1], dict(DEV, seen=at(last, 20), event=last.strftime("%y%b%d").upper()),
                 start=at(last, 0, plus_days=1), n_candles=30)
    env.clock.set(at(last, 12, plus_days=1))
    env.out(capsys, "results")
    env.out(capsys, "prices")
    st = env.out(capsys, "status")
    tc = st["test_counts"]
    assert tc["versions"]["1"] == {"contracts": SEASON_DAYS * SEASON_GAMES, "days": SEASON_DAYS}
    assert tc["B_graded"] == SEASON_DAYS * SEASON_GAMES and tc["reached_on"] == days[END].isoformat()
    assert "mean" not in json.dumps(st)
    reached = days[END]                                      # scored four days after the last counted thread's date
    opens = datetime(reached.year, reached.month, reached.day, 5, tzinfo=UTC) + timedelta(days=4)   # 00:00 Eastern
    env.clock.set(iso(opens - timedelta(seconds=1)))
    with pytest.raises(pg.Refused, match="four days"):
        env.run("evaluate", "--period", "test")
    assert not any(e["kind"] == "test_result" for e in sl.scoring_entries(env.root))
    env.clock.set(iso(opens))
    res = env.out(capsys, "evaluate", "--period", "test")
    assert res["end"] == days[END].isoformat()
    assert (res["A"]["1"]["n"], res["A"]["1"]["days"]) == ((END + 1) * SEASON_GAMES, END + 1)
    assert res["B"]["n"] == (END + 1) * SEASON_GAMES
    assert res["pass_lines"]["A_counts_reached"] and res["pass_lines"]["B_counts_reached"]
    again = env.out(capsys, "evaluate", "--period", "test")
    assert "scored once" in again["note"]
    kinds = [e["kind"] for e in sl.scoring_entries(env.root)]
    assert kinds.count("test_result") == 1 and kinds[-1] == "test_result_shown"


BOUNDARY = dict(LATE, start="2026-12-14T05:30:00Z", event="26DEC14")    # a development game on 2026-12-14 (00:30 ET)


def test_no_settlement_of_a_test_period_day_is_fetched_before_a_choice_even_for_a_development_game(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys, BOUNDARY)
    settle_world(env, games, BOUNDARY)
    env.audit(capsys)
    env.clock.set("2026-12-14T10:00:00Z")                  # 05:00 US-Eastern, more than three hours after the start
    r = env.out(capsys, "results")
    env.out(capsys, "prices")
    assert r["picks"] == 42 and not any("/markets" in u for u in env.kalshi.urls)
    k = env.root / "kalshi.sqlite"
    if k.exists():
        db = sqlite3.connect(k)
        assert db.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 0
        db.close()
    env.out(capsys, "choose", "--version", "1")
    env.out(capsys, "results")
    assert any("/markets" in u for u in env.kalshi.urls)


# ---------------------------------------------------------------- invariant 9: scores are not bought again and again

def test_scores_are_bought_only_past_three_hours_without_a_final_at_most_every_six_hours_and_never_after_three_days(
        tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys)                              # every game starts 2026-11-14 00:00Z
    env.audit(capsys)

    def buys():
        return env.odds.kinds().count("scores")
    for now, want in (("2026-11-14T03:00:00Z", 0), ("2026-11-14T03:00:01Z", 1), ("2026-11-14T05:00:00Z", 1),
                      ("2026-11-14T09:00:00Z", 1), ("2026-11-14T09:00:02Z", 2), ("2026-11-16T23:00:00Z", 3),
                      ("2026-11-17T00:00:00Z", 3), ("2026-11-18T12:00:00Z", 3)):
        env.clock.set(now)
        env.out(capsys, "results")
        assert buys() == want, now
    res = env.out(capsys, "evaluate", "--period", "development")
    assert res["B"]["not_graded"] == {"no_final_in_3_days": 42}
    assert res["A"]["1"]["not_bought"] == {"no_kalshi_pair": N_GAMES}


def test_a_final_bought_three_days_or_more_after_the_start_is_not_used(tmp_path, capsys):
    w, games = big_world()
    w.thread("t2", "Pick of the Day - 11/16/26 (Monday)", "2026-11-16T12:00:00Z")
    w.comment("late", "author_late", [("2026-11-16T20:00:00Z", ml("Pevirs"), "+110")], tid="t2")
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, DEV["start"]) for g, h, a in games] + [
        ev("late", "Vipeton Pevirs", "Pevoton Vopirs", "2026-11-17T00:00:00Z")]
    env.clock.set(DEV["schedule"])
    env.out(capsys, "schedule")
    env.clock.set(DEV["match"])
    env.out(capsys, "match")
    env.audit(capsys)
    env.clock.set("2026-11-16T23:59:59Z")
    env.out(capsys, "results")                                            # no final listed yet
    settle_world(env, games)                                              # from now on the scores list has them
    res0 = env.out(capsys, "evaluate", "--period", "development")
    assert res0["B"]["not_graded"] == {"no_score_yet": 43}
    env.clock.set("2026-11-17T06:00:00Z")                                 # the late game asks for the list again
    env.out(capsys, "results")
    assert env.odds.kinds().count("scores") == 2
    res = env.out(capsys, "evaluate", "--period", "development")
    assert res["B"]["n"] == 0 and res["B"]["not_graded"] == {"no_final_in_3_days": 42, "no_score_yet": 1}


# ---------------------------------------------------------------- daily, hygiene

def test_daily_goes_on_after_one_leagues_error_and_prints_no_comment_text(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys)
    env.odds.fail = {"icehockey_nhl"}
    env.clock.set("2026-11-13T23:30:00Z")
    out = env.out(capsys, "daily")
    assert out["schedule"]["NHL"] == "error: OddsApiError" and out["schedule"]["NBA"]["games"] == N_GAMES
    assert out["match"]["picks"] == 44
    s = env.store()
    assert s.one("SELECT league, kind FROM errors") == {"league": "NHL", "kind": "OddsApiError"}
    s.close()
    capsys.readouterr()
    env.run("status")
    printed = capsys.readouterr().out
    for word in ("author", "someone", "+110", "Lokars ML"):
        assert word not in printed


# ---------------------------------------------------------------- rev 4, outcome 2: Question B waits where Kalshi lists

def test_question_b_waits_for_kalshi_where_kalshi_lists_the_game_and_uses_the_scores_alone_where_it_does_not(
        tmp_path, capsys):
    w, games = big_world()
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, DEV["start"]) for g, h, a in games]
    _, home, away = games[1]
    env.kalshi.kevents["KXNBAGAME"] = [kevent("NBA", DEV["event"], ("AWY", away.split()[0]), ("HME", home.split()[0]))]
    env.clock.set(DEV["schedule"])
    env.out(capsys, "schedule")
    env.clock.set(DEV["match"])
    env.out(capsys, "match")
    settle_world(env, games)
    env.kalshi.markets["KXNBAGAME"] = [m for m in env.kalshi.markets["KXNBAGAME"]   # Kalshi settled neither game 1
                                       if not m["event_ticker"].endswith(("G01", "G02"))]   # nor game 2
    env.audit(capsys)
    env.clock.set(DEV["after"])
    env.out(capsys, "prices")
    env.out(capsys, "results")
    res = env.out(capsys, "evaluate", "--period", "development")
    # game 1 (authors 1 and 23) is in Kalshi's events list: it waits for Kalshi; game 2 (authors 2 and 24) is not
    # listed anywhere: graded on the scores alone
    assert res["B"]["not_graded"] == {"kalshi_not_settled": 2} and res["B"]["n"] == 40


# ---------------------------------------------------------------- rev 4, outcome 3: no test-period final before a choice

EVE = dict(LATE, start="2026-12-14T04:30:00Z")              # development games at 23:30 US-Eastern on 2026-12-13


def test_before_a_choice_no_final_or_settlement_of_a_game_a_test_period_pick_is_matched_to_is_stored(tmp_path, capsys):
    w, games = big_world(EVE)
    w.thread("t2", "Pick of the Day - 12/14/26 (Monday)", "2026-12-14T03:00:00Z")
    w.comment("t2c", "author_t2", [("2026-12-14T04:00:00Z", ml(games[0][1].split()[1]), "+110")], tid="t2")
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, EVE["start"]) for g, h, a in games]
    env.clock.set(EVE["schedule"])
    env.out(capsys, "schedule")
    env.clock.set("2026-12-14T04:10:00Z")
    env.out(capsys, "match")
    assert (env.rows()["t2c"]["status"], env.rows()["t2c"]["game_id"]) == ("matched", "g00")
    settle_world(env, games, EVE)
    env.audit(capsys)
    env.clock.set("2026-12-14T10:00:00Z")                  # 05:00 US-Eastern on 2026-12-14, no choice
    r = env.out(capsys, "results")
    assert r["scores"]["NBA"]["games_listed"] == N_GAMES - 1 and r["scores"]["NBA"]["dropped_test_period"] == 1
    s = env.store()
    stored = [e["id"] for row in s.rows("SELECT raw FROM scores") for e in json.loads(row["raw"])]
    s.close()
    assert "g00" not in stored and len(stored) == N_GAMES - 1
    assert not any("/markets" in u for u in env.kalshi.urls)     # game 0's day is not fetched from Kalshi either
    env.out(capsys, "choose", "--version", "1")
    env.out(capsys, "results")
    assert any("/markets" in u for u in env.kalshi.urls)


# ---------------------------------------------------------------- rev 4, outcome 4: one fingerprint, any entry point

def _run_as_main():
    """The module's code run as `python -m` runs it: executed afresh in a namespace named __main__, here with no
    command, which argparse refuses before anything is opened."""
    path = Path(pg.__file__)
    ns = {"__name__": "__main__", "__file__": str(path), "__package__": "valedia_trader.research_loop",
          "__builtins__": __builtins__}
    old = sys.argv
    sys.argv = ["pick_grading"]
    try:
        with pytest.raises(SystemExit):
            exec(compile(path.read_text(), str(path), "exec"), ns)
    finally:
        sys.argv = old
    return ns


def test_the_fingerprints_do_not_depend_on_how_the_module_was_started(tmp_path, capsys):
    ns = _run_as_main()
    assert ns["__name__"] == "__main__" and ns["main"] is not pg.main
    assert ns["scoring_fingerprint"]() == pg.scoring_fingerprint()
    assert ns["fingerprint"]() == pg.fingerprint()
    env = Env(tmp_path, two_league_world("NBA"))
    ns["DEFAULT_PICKS"], ns["DEFAULT_STORE"] = pg.DEFAULT_PICKS, pg.DEFAULT_STORE      # the temporary defaults
    capsys.readouterr()
    assert ns["main"](["--store", str(env.root), "--picks", str(env.picks), "choose", "--version", "1"],
                      clock=env.clock) == 0
    printed = json.loads(capsys.readouterr().out)
    s = env.store()
    pin = s.scoring_pin()
    s.close()
    assert pin == printed["scoring_fingerprint"] == pg.scoring_fingerprint()


# ---------------------------------------------------------------- rev 4, outcome 5: from the entry point down

def test_a_change_in_main_or_the_set_up_of_a_match_run_changes_the_matching_fingerprint(tmp_path):
    base, sbase = pg.fingerprint(), pg.scoring_fingerprint()

    def clock_shift(src):                                    # the verifier's edit: the clock shifted inside main
        return src.replace("    clock = clock or _system_clock\n", "    clock = clock or _system_clock\n"
                           "    clock = (lambda c: (lambda: c() + timedelta(hours=1)))(clock)\n", 1)

    def grader(src):
        return src.replace("        self.store = GradingStore(self.root / STORE_FILE)\n",
                           "        self.store = GradingStore(self.root / STORE_FILE)\n        self._extra = 1\n", 1)

    def pairing(src):
        return src.replace("    identity = pick_store_identity(picks)\n",
                           "    identity = pick_store_identity(picks)\n    _extra = 1\n", 1)

    def daily(src):                                          # daily writes match rows too
        return src.replace("def cmd_daily(gr: Grader) -> dict:\n", "def cmd_daily(gr: Grader) -> dict:\n    _extra = 1\n", 1)
    for name, change in (("clock_shift", clock_shift), ("grader", grader), ("pairing", pairing), ("daily", daily)):
        assert change(Path(pg.__file__).read_text()) != Path(pg.__file__).read_text(), name
        assert _copy_with_text(tmp_path, name, change).fingerprint() != base, name

    def evaluate(src):                                       # another command's code: not a match run's
        return src.replace("def cmd_evaluate(gr: Grader, period: str, code_changed: str | None = None) -> dict:\n",
                           "def cmd_evaluate(gr: Grader, period: str, code_changed: str | None = None) -> dict:\n"
                           "    _extra = 1\n", 1)
    mod = _copy_with_text(tmp_path, "evaluate", evaluate)
    assert mod.fingerprint() == base and mod.scoring_fingerprint() != sbase


# ---------------------------------------------------------------- rev 5, outcomes 1 and 5: held whenever it was matched

def _held_world(day, thread_seen, test_seen):
    """`big_world(day)` and a pick of the 2026-12-14 thread on game 0's home team, first seen at `test_seen`."""
    w, games = big_world(day)
    w.thread("t2", "Pick of the Day - 12/14/26 (Monday)", thread_seen)
    w.comment("t2c", "author_t2", [(test_seen, ml(games[0][1].split()[1]), "+110")], tid="t2")
    w.unparsed.add("t2c")
    return w, games


def _held_env(tmp_path, capsys, day, thread_seen, test_seen):
    w, games = _held_world(day, thread_seen, test_seen)
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, day["start"]) for g, h, a in games]
    env.clock.set(day["schedule"])
    env.out(capsys, "schedule")
    return env, w, games


def _assert_held_then_released(env, capsys, now):
    """Before a choice game 0 is held (its final and Kalshi's result are stored, but not read); after `choose` it is
    read. Authors 0 and 22 back game 0's home team in the development thread."""
    env.clock.set(now)
    res = env.out(capsys, "evaluate", "--period", "development")
    assert res["B"]["not_graded"].get("held_for_test") == 2 and res["B"]["n"] == 40
    assert res["A"]["1"]["not_bought"].get("held_for_test") == 1
    assert res["held_for_test"] == {"picks": 2, "league_days": 1}
    st = env.out(capsys, "status")
    assert st["held_for_test"] == {"picks": 2, "league_days": 1}
    env.out(capsys, "choose", "--version", "1")
    res = env.out(capsys, "evaluate", "--period", "development")
    assert "held_for_test" not in res["B"]["not_graded"] and res["B"]["n"] == 42
    assert res["held_for_test"] == {"picks": 0, "league_days": 0}


def test_a_game_is_held_for_the_test_when_the_test_pick_is_matched_after_its_final_was_stored_a_late_parse(
        tmp_path, capsys):
    env, w, games = _held_env(tmp_path, capsys, EVE, "2026-12-14T03:00:00Z", "2026-12-14T04:00:00Z")
    env.clock.set("2026-12-14T04:10:00Z")
    env.out(capsys, "match")
    assert "t2c" not in env.rows()                                         # its parse is not stored yet
    settle_world(env, games, EVE)
    env.audit(capsys)
    env.clock.set("2026-12-14T08:00:00Z")
    r = env.out(capsys, "results")                                          # game 0's final is stored
    assert r["scores"]["NBA"]["dropped_test_period"] == 0
    w.parse_later(env.picks, "t2c", "2026-12-14T09:00:00Z")
    env.clock.set("2026-12-14T09:30:00Z")
    env.out(capsys, "match")
    assert (env.rows()["t2c"]["status"], env.rows()["t2c"]["game_id"]) == ("matched", "g00")
    _assert_held_then_released(env, capsys, "2026-12-14T10:30:00Z")


def test_a_game_is_held_for_the_test_when_results_ran_after_the_last_match(tmp_path, capsys):
    env, w, games = _held_env(tmp_path, capsys, LATE, "2026-12-14T01:00:00Z", "2026-12-14T02:00:00Z")
    env.clock.set("2026-12-14T00:30:00Z")
    env.out(capsys, "match")                                                # the last match before the results
    settle_world(env, games, LATE)
    env.audit(capsys)
    w.parse_later(env.picks, "t2c", "2026-12-14T02:05:00Z")                 # the test pick reaches the pick store
    env.clock.set("2026-12-14T06:30:00Z")
    r = env.out(capsys, "results")                                          # unmatched yet: game 0's final is stored
    assert r["scores"]["NBA"]["dropped_test_period"] == 0
    env.clock.set("2026-12-14T07:00:00Z")
    env.out(capsys, "match")
    assert env.rows()["t2c"]["game_id"] == "g00"
    _assert_held_then_released(env, capsys, "2026-12-14T08:00:00Z")


def test_a_game_matched_by_a_test_pick_under_an_earlier_fingerprint_only_is_held(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys, EVE)
    settle_world(env, games, EVE)
    env.audit(capsys)
    env.clock.set("2026-12-14T08:00:00Z")
    env.out(capsys, "results")
    s = env.store()
    row = dict(s.rows("SELECT * FROM matches WHERE game_id = 'g00' ORDER BY id LIMIT 1")[0])
    s.close()
    row.update(id=None, comment_id="old_t", thread_id="t_old", thread_date="2026-12-14", fingerprint="0" * 64,
               at="2026-12-14T08:00:00Z")                                   # a test pick's row under an older rule
    db = sqlite3.connect(env.root / pg.STORE_FILE)
    db.execute("INSERT INTO matches (%s) VALUES (%s)" % (", ".join(row), ", ".join("?" * len(row))), list(row.values()))
    db.commit()
    db.close()
    env.clock.set("2026-12-14T09:00:00Z")
    res = env.out(capsys, "evaluate", "--period", "development")
    assert res["B"]["not_graded"].get("held_for_test") == 2 and res["held_for_test"] == {"picks": 2, "league_days": 1}


# ---------------------------------------------------------------- rev 5, outcome 2: fingerprints of the source alone

FRESH_PROCESS = """
import json, sys
from valedia_trader.data_and_fees import pick_lists as pl
from valedia_trader.research_loop import pick_grading as pg
before = [pg.fingerprint(), pg.scoring_fingerprint()]
s = pl.Store(sys.argv[1], readonly=True, create=False)
s.close()
g = pg.GradingStore(sys.argv[2])
g.close()
print(json.dumps({"before": before, "after": [pg.fingerprint(), pg.scoring_fingerprint()]}))
"""


def _python(args, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(pg.REPO))
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)


def test_the_fingerprints_are_of_the_source_alone_in_any_process_and_from_any_entry_point(tmp_path):
    env = Env(tmp_path, two_league_world("NBA"))
    pl.Store(env.picks, readonly=True, create=False).close()               # this process has opened a store
    here = [pg.fingerprint(), pg.scoring_fingerprint()]
    r = _python(["-c", FRESH_PROCESS, str(env.picks), str(tmp_path / "g" / pg.STORE_FILE)], tmp_path)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["before"] == got["after"] == here                           # a fresh import, before and after a store
    for args in (["-m", "valedia_trader.research_loop.pick_grading", "fingerprints"],
                 [str(Path(pg.__file__)), "fingerprints"]):                 # python -m, and a run by path
        r = _python(args, tmp_path)
        assert r.returncode == 0, (args, r.stderr)
        out = json.loads(r.stdout)
        assert [out["fingerprint"], out["scoring_fingerprint"]] == here, args


def test_a_runtime_value_of_a_module_does_not_reach_the_fingerprints(monkeypatch):
    base = [pg.fingerprint(), pg.scoring_fingerprint()]
    monkeypatch.setattr(pl, "_EXPECTED", {("table", "x", "x", "sql")})       # a lazy cache filled
    monkeypatch.setattr(pg, "LEAGUES", dict(pg.LEAGUES, XFL=("x", "y")))    # a module value changed at run time
    assert [pg.fingerprint(), pg.scoring_fingerprint()] == base


# ---------------------------------------------------------------- rev 5, outcome 4: no wait for Kalshi for ever

def _listed_env(tmp_path, capsys, start, kday, drop=("G01", "G02")):
    """Big world whose games start at `start`; Kalshi's events list holds game 1 dated `kday`; games 1 and 2 have
    no settled market."""
    day = dict(DEV, start=start)
    w, games = big_world(day)
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, start) for g, h, a in games]
    _, home, away = games[1]
    env.kalshi.kevents["KXNBAGAME"] = [kevent("NBA", kday, ("AWY", away.split()[0]), ("HME", home.split()[0]))]
    env.clock.set(day["schedule"])
    env.out(capsys, "schedule")
    env.clock.set(day["match"])
    env.out(capsys, "match")
    settle_world(env, games, day)
    env.kalshi.markets["KXNBAGAME"] = [m for m in env.kalshi.markets["KXNBAGAME"]
                                       if not m["event_ticker"].endswith(tuple(drop))]
    env.audit(capsys)
    return env, games


def test_a_pick_waiting_for_kalshi_is_not_graded_seven_days_after_the_start(tmp_path, capsys):
    env, games = _listed_env(tmp_path, capsys, DEV["start"], DEV["event"])
    env.clock.set(DEV["after"])
    env.out(capsys, "prices")
    env.out(capsys, "results")
    env.clock.set("2026-11-20T23:59:59Z")                                   # 7 days after 2026-11-14 00:00Z, less 1 s
    assert env.out(capsys, "evaluate", "--period", "development")["B"]["not_graded"] == {"kalshi_not_settled": 2}
    env.clock.set("2026-11-21T00:00:00Z")
    res = env.out(capsys, "evaluate", "--period", "development")
    assert res["B"]["not_graded"] == {"kalshi_never_settled": 2} and res["B"]["n"] == 40


@pytest.mark.parametrize("kday, waits", [("26NOV14", True), ("26NOV13", True), ("26NOV12", False)])
def test_kalshi_lists_a_game_on_its_eastern_date_or_the_day_before(tmp_path, capsys, kday, waits):
    env, games = _listed_env(tmp_path, capsys, "2026-11-14T05:30:00Z", kday)    # 00:30 US-Eastern on 2026-11-14
    env.clock.set("2026-11-15T12:00:00Z")
    env.out(capsys, "prices")
    env.out(capsys, "results")
    res = env.out(capsys, "evaluate", "--period", "development")
    assert res["B"]["not_graded"] == ({"kalshi_not_settled": 2} if waits else {}) and res["B"]["n"] == (40 if waits else 42)


# ---------------------------------------------------------------- rev 5, outcome 6: behaviours no test caught

def test_an_event_without_a_ticker_is_kept_and_counted_unreadable(tmp_path, capsys, monkeypatch):
    words = tmp_path / "words"
    words.write_text(ENGLISH)
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    w, labels = shared_picks(300, 7)
    nameless = dict(kevent("NBA", "26NOV12", ("BOS", "Bosaton"), ("PHX", "Phoaton")), event_ticker=None)
    env = shared_env(tmp_path, capsys, w, extra={"NBA": [nameless]})
    assert env.schedule_out["kalshi_codes"]["NBA"] == {"events": 3, "unreadable": 1}
    s = env.store()
    raw = json.loads(s.one("SELECT raw FROM code_tables WHERE league = 'NBA'")["raw"])
    s.close()
    assert len(raw) == 3 and sum(1 for e in raw if not e["event_ticker"]) == 1      # kept in the stored table
    assert not any(r["status"] == "matched" for r in env.rows().values())


def test_the_latest_code_table_of_a_league_is_judged_not_an_older_one(tmp_path, capsys, monkeypatch):
    words = tmp_path / "words"
    words.write_text(ENGLISH)
    monkeypatch.setattr(pg, "WORDS_FILE", words, raising=False)
    w, labels = shared_picks(600, 9)
    env = shared_env(tmp_path, capsys, w, unreadable=("NFL",))           # the first NFL table is unreadable
    assert not any(r["status"] == "matched" for r in env.rows().values())
    for e in env.kalshi.kevents[SERIES["NFL"]]:                       # the NFL's codes have three letters
        e["sub_title"] = "%s vs %s (Nov 13)" % (e["event_ticker"][-6:-3], e["event_ticker"][-3:])
    env.clock.set("2026-11-14T13:00:00Z")
    env.out(capsys, "schedule")                                           # the latest NFL table reads in full
    env.clock.set("2026-11-14T14:00:00Z")
    env.out(capsys, "match")
    rows = env.rows()
    assert wrong_matches(rows, labels) == [] and sum(1 for r in rows.values() if r["status"] == "matched") > 50
    for e in env.kalshi.kevents[SERIES["NFL"]]:
        e["sub_title"] = e["title"] + " (Nov 13)"
    env.clock.set("2026-11-14T15:00:00Z")
    env.out(capsys, "schedule")                                           # and the latest is unreadable again
    env.clock.set("2026-11-14T16:00:00Z")
    env.out(capsys, "match")
    assert not any(r["status"] == "matched" for r in env.rows().values())


def test_the_test_counts_wait_for_kalshi_where_kalshi_lists_the_game(tmp_path, capsys):
    w, games = big_world(TEST)
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, TEST["start"]) for g, h, a in games]
    _, home, away = games[1]
    env.kalshi.kevents["KXNBAGAME"] = [kevent("NBA", TEST["event"], ("AWY", away.split()[0]), ("HME", home.split()[0]))]
    env.clock.set(TEST["schedule"])
    env.out(capsys, "schedule")
    env.clock.set(TEST["match"])
    env.out(capsys, "match")
    settle_world(env, games, TEST)
    env.kalshi.markets["KXNBAGAME"] = [m for m in env.kalshi.markets["KXNBAGAME"]
                                       if not m["event_ticker"].endswith(("G01", "G02"))]
    env.audit(capsys)
    env.out(capsys, "choose", "--version", "1")
    env.clock.set(TEST["after"])
    env.out(capsys, "results")
    assert env.out(capsys, "status")["test_counts"]["B_graded"] == 40       # game 1's two picks wait for Kalshi


def test_a_game_kalshi_lists_only_in_its_stored_markets_waits_for_kalshi(tmp_path, capsys):
    """Two settled Kalshi events of game 1 on one day (a doubleheader, or a look-alike): no pair is made, and the
    stored markets still say Kalshi lists the game, so its picks wait, and seven days on are not graded."""
    env, games = _listed_env(tmp_path, capsys, DEV["start"], "26NOV01", drop=())   # the events list dates it elsewhere
    _, home, away = games[1]
    expected = plus(DEV["start"], 3 * 3600)
    env.kalshi.markets["KXNBAGAME"] += [kmarket("KXNBAGAME-26NOV13G01B", "H01", home.split()[0], "yes", expected),
                                        kmarket("KXNBAGAME-26NOV13G01B", "A01", away.split()[0], "no", expected)]
    env.clock.set(DEV["after"])
    p = env.out(capsys, "prices")
    env.out(capsys, "results")
    assert p["pairing"]["NBA"]["paired_now"] == N_GAMES - 1
    res = env.out(capsys, "evaluate", "--period", "development")
    assert res["B"]["not_graded"] == {"kalshi_not_settled": 2}
