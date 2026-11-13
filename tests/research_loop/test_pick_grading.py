"""Tests for research_loop/pick_grading.py. Every store is built here, in tmp_path; nothing touches the network, the
local model, Reddit, the real pick store or the real secrets file."""
import json
import random
import re
import sqlite3
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


def T(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def ts(text):
    return int(T(text).timestamp())


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    def _add(self, when, kind, data):
        self.n += 1
        self.items.append((when, self.n, kind, data))

    def thread(self, tid="t1", title=TITLE, seen="2026-11-13T12:00:00Z"):
        self._add(seen, "thread", {"tid": tid, "title": title})
        return tid

    def comment(self, cid, author, versions, tid="t1", role="user"):
        """versions: [(seen_at, fields or None, odds text or None)]; fields None is a comment that is not a pick."""
        for i, (seen, fields, odds, *rest) in enumerate(versions, 1):
            self._add(seen, "version", {"cid": cid, "tid": tid, "author": author, "version": i, "fields": fields,
                                        "odds": odds, "role": role, "deleted": bool(rest and rest[0]),
                                        "first": i == 1})

    def build(self, path):
        store = pl.Store(path)
        items = sorted(self.items, key=lambda x: (x[0], x[1]))
        try:
            with store.writing(items[0][0]):
                pid = store.payload("thread", "t", "{}", items[0][0])
            for when, _, kind, d in items:
                with store.writing(when):
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
                    dec = pl.decimal_from_odds(d["odds"]) if d["odds"] else None
                    parse = store.conn.execute(
                        "INSERT INTO parses (version_id, prompt_hash, model, parsed_at, status, reason, is_pick, "
                        "decimal_odds, raw_output) VALUES (?,?,?,?,?,?,?,?,?)",
                        (vid, pl.PROMPT_HASH, "m", when, "ok", None, 1 if f else 0, dec, "{}")).lastrowid
                    for k, v in (f or {}).items():
                        val = json.dumps(v) if k == "event" else (None if v is None else str(v))
                        store.conn.execute("INSERT INTO parse_fields (parse_id, field, value, phrase, kept, reason) "
                                           "VALUES (?,?,?,?,?,?)", (parse, k, val, "p", 1, None))
                    if d["odds"]:
                        store.conn.execute("INSERT INTO parse_fields (parse_id, field, value, phrase, kept, reason) "
                                           "VALUES (?,?,?,?,?,?)", (parse, "odds_as_written", d["odds"], "p", 1, None))
        finally:
            store.close()
        return path


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
        self.markets, self.candles, self.urls = {}, {}, []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        path = parts.path.split("/trade-api/v2", 1)[1]
        q = dict(urllib.parse.parse_qsl(parts.query))
        if path == "/historical/cutoff":
            return json.dumps({"market_settled_ts": "2026-01-01T00:00:00Z"}).encode()
        if path == "/markets":
            return json.dumps({"markets": self.markets.get(q.get("series_ticker"), []), "cursor": ""}).encode()
        m = re.fullmatch(r"/series/([^/]+)/markets/([^/]+)/candlesticks", path)
        if m:
            lo, hi = int(q["start_ts"]), int(q["end_ts"])
            rows = [c for c in self.candles.get(m.group(2), []) if lo <= c["end_period_ts"] <= hi]
            return json.dumps({"candlesticks": rows}).encode()
        raise kd.HttpError(404, url)


def kalshi_client(transport):
    return kh.HistoryClient(fetch=transport, sleep=lambda s: None, clock=lambda: 0.0)


def ev(gid, home, away, start, sport="basketball_nba"):
    return {"id": gid, "sport_key": sport, "commence_time": start, "home_team": home, "away_team": away}


def kmarket(event, code, city, result, expected):
    return {"ticker": event + "-" + code, "event_ticker": event, "title": city + " wins", "yes_sub_title": city,
            "result": result, "status": "finalized", "close_time": plus(expected, 3600),
            "settlement_ts": plus(expected, 3600), "expected_expiration_time": expected, "occurrence_datetime": expected}


def kcandle(end_ts, bid, ask):
    return {"end_period_ts": end_ts, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}, "price": {}, "volume": 1}


class Env:
    """One grading folder with its fakes."""

    def __init__(self, tmp_path, world=None):
        self.root, self.picks = tmp_path / "grading", tmp_path / "picks.sqlite"
        self.audit_dir = tmp_path / "audit"
        self.clock, self.odds, self.kalshi = Clock(), OddsTransport(), KalshiTransport()
        self.proposer = None
        if world is not None:
            world.build(self.picks)

    def run(self, *args, capsys=None):
        argv = ["--store", str(self.root), "--picks", str(self.picks), *args]
        if args and args[0] in ("match", "daily") and self.proposer is None:
            argv.append("--no-model")
        return pg.main(argv, odds_client=odds_client(self.odds), kalshi_client=kalshi_client(self.kalshi),
                       proposer=self.proposer, clock=self.clock)

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


# ---------------------------------------------------------------- matching: names, the window, uniqueness

def test_team_forms_follow_the_schedule_spelling_and_the_strict_qualifier_rule():
    assert ("seminoles",) in pg.team_forms("Florida St Seminoles")
    assert ("florida", "st") in pg.team_forms("Florida St Seminoles")
    assert ("florida",) not in pg.team_forms("Florida St Seminoles")        # Florida is not Florida State
    assert ("lakers",) in pg.team_forms("Los Angeles Lakers") and ("los", "angeles") in pg.team_forms("Los Angeles Lakers")
    named = pg.named_teams("Duke Blue Devils ML", ["Duke Blue Devils", "New Jersey Devils"])
    assert set(named) == {"Duke Blue Devils"}                             # a name inside a longer one is not counted
    assert pg.named_teams("Florida ML", ["Florida St Seminoles", "Florida Gators"]) == {"Florida Gators": {("florida",)}}
    assert pg.named_teams("o 221.5", ["Orlando Magic"]) == {}             # no single letters, no numbers


def test_over_under_whole_words_or_joined_to_the_line():
    assert pg.over_under("Over 221.5", 221.5) == "over"
    assert pg.over_under("Kalo/Miton u221.5", 221.5) == "under"
    assert pg.over_under("Kalo/Miton o221.5", 221.5) == "over"
    assert pg.over_under("o 221.5", 221.5) is None                        # o is not joined to the line
    assert pg.over_under("Hangover 221.5", 221.5) is None
    assert pg.over_under("over or under 221.5", 221.5) is None
    assert pg.over_under("u220", 221.5) is None                           # joined to another number


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


def test_a_team_naming_two_games_of_the_window_is_not_graded(tmp_path, capsys):
    env = Env(tmp_path, two_league_world())
    kings_schedule(env)
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T21:00:00Z")
    assert env.out(capsys, "match")["by_outcome"] == {"more_than_one_game": 1}


def test_the_league_written_narrows_the_teams(tmp_path, capsys):
    env = Env(tmp_path, two_league_world("NBA"))
    kings_schedule(env)
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T21:00:00Z")
    env.out(capsys, "match")
    r = env.rows()["c1"]
    assert (r["status"], r["game_id"], r["side"], r["team"]) == ("matched", "nba1", "home", "Sacramento Kings")


@pytest.mark.parametrize("nhl_start, outcome", [
    ("2026-11-14T11:00:00Z", "matched"),          # 06:00 US-Eastern the next day: outside the window
    ("2026-11-14T10:59:59Z", "more_than_one_game"),
    ("2026-11-13T19:30:00Z", "matched"),          # started before the comment was first seen: outside the window
    ("2026-11-13T20:00:01Z", "more_than_one_game"),
])
def test_uniqueness_counts_only_games_of_the_window(tmp_path, capsys, nhl_start, outcome):
    env = Env(tmp_path, two_league_world())
    env.clock.set("2026-11-13T19:00:00Z")
    kings_schedule(env, nhl_start)
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T21:00:00Z")
    got = env.out(capsys, "match")["by_outcome"]
    assert list(got) == [outcome]
    if outcome == "matched":
        assert env.rows()["c1"]["game_id"] == "nba1"


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


def oracle(versions, games):
    """Independent statement of the rule for the generated cases: versions [(seen, team or None)] and games
    {team: (game id, start)}. The pick is the first version (in order) that names a team whose game starts after it
    was seen and that is the latest version first seen strictly before that start. Returns (game, version index)."""
    for i, (seen, team) in enumerate(versions):
        if team is None:
            continue
        gid, start = games[team]
        before = [j for j, (s, _) in enumerate(versions) if s < start]
        if before and before[-1] == i:
            return gid, i
    return None


def test_text_first_seen_at_or_after_a_start_is_never_used_generated(tmp_path, capsys):
    rng = random.Random(7)
    s1, s2 = "2026-11-14T00:00:00Z", "2026-11-14T00:05:00Z"
    games = {"Kaloton Lokars": ("g1", s1), "Miraton Ramirs": ("g2", s2)}
    w = PickWorld()
    w.thread()
    expected, cases = {}, {}
    offsets = [-200, -61, -60, -1, 0, 1, 59, 60, 240, 299, 300, 301, 500]
    for n in range(120):
        k = rng.randint(1, 4)
        base = rng.choice(["2026-11-13T23:50:00Z", s1, s2])
        seen = sorted({plus(base, rng.choice(offsets)) for _ in range(k)})
        vs = [(t, rng.choice([None, "Kaloton Lokars", "Miraton Ramirs"])) for t in seen]
        cid = "c%03d" % n
        w.comment(cid, "author%03d" % n, [(t, ml(team.split()[1]) if team else None, "-110" if team else None)
                                          for t, team in vs])
        cases[cid] = vs
        first_pick = next((t for t, team in vs if team), None)
        if first_pick is not None:
            expected[cid] = oracle([(ts(t), team) for t, team in vs], {k2: (g, ts(s)) for k2, (g, s) in games.items()})
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Doteton Tedors", s1),
                                         ev("g2", "Miraton Ramirs", "Pesuton Supers", s2)]
    env.clock.set("2026-11-13T20:00:00Z")
    env.out(capsys, "schedule")
    env.clock.set("2026-11-14T06:00:00Z")
    env.out(capsys, "match")
    rows = env.rows()
    assert set(rows) == set(expected)
    matched = 0
    for cid, r in rows.items():
        vs = cases[cid]
        if r["status"] == "matched":
            matched += 1
            seen = vs[r["version"] - 1][0]
            assert ts(seen) < ts(r["start"]) and ts(r["known_at"]) <= ts(seen) < ts(r["start"])
            latest = [i for i, (t, _) in enumerate(vs) if ts(t) < ts(r["start"])][-1]
            assert latest == r["version"] - 1
            assert vs[latest][1] == (r["team"])
            assert (r["game_id"], r["version"] - 1) == expected[cid]
        else:
            assert expected[cid] is None, (cid, r["reason"], expected[cid])
    assert matched > 10 and len(rows) - matched > 10


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
    assert r["c2"]["reason"] == "no_team_named"            # no game of the window starts after it was first seen
    assert (r["c3"]["status"], r["c3"]["known_at"]) == ("matched", plus(start, -1))
    assert (r["c4"]["status"], r["c4"]["known_at"]) == ("matched", plus(start, -30))
    assert r["c5"]["reason"] == "version_not_a_pick"       # the version in force at the start is not a pick


def test_start_is_the_latest_list_before_it_and_disagreeing_lists_flag_the_game(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    w.comment("c1", "a1", [("2026-11-13T18:00:00Z", ml("Lokars"), "-110")])
    w.comment("c2", "a2", [("2026-11-13T18:00:00Z", ml("Ramirs"), "-110")])
    w.comment("c3", "a3", [("2026-11-13T18:00:00Z", ml("Ratears"), "-110")])
    w.comment("c4", "a4", [("2026-11-13T18:00:00Z", ml("Visurs"), "-110")])
    env = Env(tmp_path, w)
    env.clock.set("2026-11-13T17:00:00Z")
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:00:00Z"),
                                         ev("g2", "Miraton Ramirs", "Doteton Tedors", "2026-11-14T00:00:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T23:00:00Z")                  # g1 moves 30 minutes, g2 moves 31 minutes
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:30:00Z"),
                                         ev("g2", "Miraton Ramirs", "Doteton Tedors", "2026-11-14T00:31:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-14T00:40:00Z")                  # a list fetched after a start never sets it
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:30:00Z"),
                                         ev("g3", "Teraton Ratears", "Nokaton Kanors", "2026-11-14T00:35:00Z"),
                                         ev("g4", "Suviton Visurs", "Dopeton Pedors", "2026-11-14T03:00:00Z")]
    env.out(capsys, "schedule")
    env.clock.set("2026-11-14T06:00:00Z")
    env.out(capsys, "match")
    r = env.rows()
    assert (r["c1"]["status"], r["c1"]["start"]) == ("matched", "2026-11-14T00:30:00Z")
    assert r["c2"]["reason"] == "start_disagrees"
    assert r["c3"]["reason"] == "no_start_known"           # seen only in a list fetched after its start
    assert (r["c4"]["status"], r["c4"]["start"]) == ("matched", "2026-11-14T03:00:00Z")


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


def test_a_model_proposal_is_kept_only_under_the_two_conditions(tmp_path, capsys):
    w = PickWorld()
    w.thread()
    w.comment("c1", "a1", [("2026-11-13T18:00:00Z", {"market": "moneyline", "selection": "Lokars over Supers"},
                            "-110")])
    w.comment("c2", "a2", [("2026-11-13T18:00:00Z", {"market": "moneyline", "selection": "Ramirs over Tedors"},
                            "-110")])
    w.comment("c3", "a3", [("2026-11-13T18:00:00Z", {"market": "moneyline", "selection": "the home dog"}, "+150")])
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev("g1", "Kaloton Lokars", "Pesuton Supers", "2026-11-14T00:00:00Z"),
                                         ev("g2", "Miraton Ramirs", "Doteton Tedors", "2026-11-14T00:00:00Z")]
    answers = {"Lokars": "Kaloton Lokars", "Ramirs": "Kaloton Lokars", "home": "Miraton Ramirs"}
    seen_bodies = []

    def proposer(body, market, cands):
        seen_bodies.append(body)
        return next(v for k, v in answers.items() if k in body)
    env.proposer = proposer
    env.out(capsys, "schedule")
    env.clock.set("2026-11-13T19:00:00Z")
    out = env.out(capsys, "match")
    r = env.rows()
    assert (r["c1"]["status"], r["c1"]["source"], r["c1"]["team"]) == ("matched", "model", "Kaloton Lokars")
    assert r["c2"]["reason"] == "side_unclear"             # the proposed team's name is not in that comment
    assert r["c3"]["reason"] == "no_team_named"            # no team name in the comment at all
    assert out["model_asked"] == 3 and out["by_model"] == 1
    env.clock.set("2026-11-13T19:30:00Z")
    env.out(capsys, "match")
    assert len(seen_bodies) == 3                            # proposals are kept and not asked again


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
    assert pg._cli(["--store", str(env.root), "--picks", str(env.picks), "match", "--no-model"], clock=env.clock) == 2


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
    ([(minute(2), 0.0, .03)], START, "reason:no_bid_ask"),
])
def test_purchase_minute_rule_at_every_edge(candles, start, want):
    got = pg.purchase_quote([kd.normalize_candle(kcandle(e, b, a)) for e, b, a in candles], SEEN, start)
    assert got.get("ask") == want if not isinstance(want, str) else got == {"reason": want.split(":")[1]}


def test_purchase_never_reads_a_candle_after_the_start():
    late = [kd.normalize_candle(kcandle(START + 60 * k, .48, .50)) for k in range(5)]
    assert pg.purchase_quote(late, START - 30, START) == {"reason": "no_candle"}


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


# ---------------------------------------------------------------- the whole flow and its gates

N_GAMES = 22


DEV = {"title": TITLE, "thread": "2026-11-13T12:00:00Z", "seen": "2026-11-13T20:00:00Z",
       "start": "2026-11-14T00:00:00Z", "schedule": "2026-11-13T19:00:00Z", "match": "2026-11-13T23:00:00Z",
       "after": "2026-11-15T12:00:00Z", "event": "26NOV13"}
TEST = {"title": "Pick of the Day - 12/14/26 (Monday)", "thread": "2026-12-14T12:00:00Z", "seen": "2026-12-14T20:00:00Z",
        "start": "2026-12-15T00:00:00Z", "schedule": "2026-12-14T19:00:00Z", "match": "2026-12-14T23:00:00Z",
        "after": "2026-12-16T12:00:00Z", "event": "26DEC14"}


def big_world(day=DEV, n_authors=44):
    """N_GAMES NBA games at the day's start; author i backs the home team of game i % N_GAMES (moneyline), first seen
    at the day's `seen` + i minutes; authors 40 to 43 write a spread, a total and two props."""
    teams = team_names(2 * N_GAMES)
    games = [(f"g{k:02d}", "%s %s" % teams[2 * k], "%s %s" % teams[2 * k + 1]) for k in range(N_GAMES)]
    w = PickWorld()
    w.thread(title=day["title"], seen=day["thread"])
    for i in range(n_authors):
        gid, home, away = games[i % N_GAMES]
        seen = plus(day["seen"], 60 * i)
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


def setup_big(tmp_path, capsys, day=DEV):
    w, games = big_world(day)
    env = Env(tmp_path, w)
    env.odds.events["basketball_nba"] = [ev(g, h, a, day["start"]) for g, h, a in games]
    env.clock.set(day["schedule"])
    env.out(capsys, "schedule")
    env.clock.set(day["match"])
    env.out(capsys, "match")
    return env, games


def settle_world(env, games, day=DEV):
    """Kalshi markets (home wins game k when k is even), candles every minute from the day's `seen` for five hours
    (past the start), and the Odds API's finals."""
    start = day["start"]
    expected = plus(start, 3 * 3600)
    rows = []
    for k, (gid, home, away) in enumerate(games):
        event = "KXNBAGAME-%sG%02d" % (day["event"], k)
        hw = k % 2 == 0
        rows += [kmarket(event, "H%02d" % k, home.split()[0], "yes" if hw else "no", expected),
                 kmarket(event, "A%02d" % k, away.split()[0], "no" if hw else "yes", expected)]
        for code in ("H%02d" % k, "A%02d" % k):
            ticker = event + "-" + code
            base = ts(day["seen"]) // 60 * 60
            env.kalshi.candles[ticker] = [kcandle(base + 60 * j, .48, .50) for j in range(0, 300)]
    env.kalshi.markets["KXNBAGAME"] = rows
    env.odds.scores["basketball_nba"] = [
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
    assert env.kalshi.urls == [] and "scores" not in env.odds.kinds()
    out = env.out(capsys, "daily")
    assert out["gate_open"] is False and "prices" not in out and env.kalshi.urls == []
    with pytest.raises(pg.Refused):
        env.run("audit-pass", "--wrong", "0")                               # no sample yet
    with pytest.raises(pg.Refused, match="outside the repository"):
        env.run("audit-sample", "--out", str(pg.REPO / "artifacts"))
    a = env.out(capsys, "audit-sample", "--out", str(env.audit_dir), "--seed", "5")
    text = Path(a["path"]).read_text()
    assert a["picks"] == 40 and text.count("\n## ") == 40 and "Comment text (data):" in text
    s = env.store()
    assert s.one("SELECT seed, n FROM audits") == {"seed": 5, "n": 40}
    s.close()
    env.out(capsys, "audit-pass", "--wrong", "3")                              # more than 2 wrong: still closed
    with pytest.raises(pg.Refused, match="audit-pass"):
        env.run("results")
    with pytest.raises(pg.Refused, match="already has its count"):
        env.run("audit-pass", "--wrong", "0")
    env.out(capsys, "audit-sample", "--out", str(env.audit_dir), "--seed", "6")
    assert env.out(capsys, "audit-pass", "--wrong", "2")["gate_open"] is True
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
    # B: 40 moneylines at +110 (2.10), the spread (home -2.5 wins by 10 when the home team wins) and the total (210)
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


def test_a_changed_fingerprint_after_a_result_makes_every_command_refuse(tmp_path, capsys, monkeypatch):
    env, games = setup_big(tmp_path, capsys)
    settle_world(env, games)
    env.clock.set("2026-11-15T12:00:00Z")
    env.out(capsys, "audit-sample", "--out", str(env.audit_dir), "--seed", "1")
    env.out(capsys, "audit-pass", "--wrong", "0")
    env.out(capsys, "match")                                # before a result: the rule may still be repaired
    env.out(capsys, "results")
    monkeypatch.setitem(pg.LEAGUE_WORDS, "pro basketball", ("NBA",))
    for cmd in (["status"], ["schedule"], ["match"], ["prices"], ["results"], ["daily"], ["choose", "--version", "1"],
                ["evaluate", "--period", "development"], ["audit-sample", "--out", str(env.audit_dir)]):
        with pytest.raises(pg.Refused, match="frozen"):
            env.run(*cmd)
    monkeypatch.delitem(pg.LEAGUE_WORDS, "pro basketball")
    assert env.run("status") == 0


def test_a_changed_fingerprint_before_any_result_needs_a_new_audit(tmp_path, capsys, monkeypatch):
    env, games = setup_big(tmp_path, capsys)
    env.out(capsys, "audit-sample", "--out", str(env.audit_dir), "--seed", "1")
    env.out(capsys, "audit-pass", "--wrong", "0")
    monkeypatch.setitem(pg.LEAGUE_WORDS, "pro basketball", ("NBA",))
    env.clock.set("2026-11-15T12:00:00Z")
    with pytest.raises(pg.Refused, match="audit-pass"):
        env.run("results")
    env.out(capsys, "match")                                 # every pick gets a row under the new fingerprint
    s = env.store()
    assert len(s.latest_matches(pg.fingerprint())) == 44
    assert {r["fingerprint"] for r in s.rows("SELECT DISTINCT fingerprint FROM matches")} == \
        {pg.fingerprint(), s.one("SELECT fingerprint FROM audits")["fingerprint"]}
    s.close()


def test_the_test_period_is_refused_without_a_choice_and_scored_once(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys)
    env.clock.set("2026-11-15T12:00:00Z")
    env.out(capsys, "audit-sample", "--out", str(env.audit_dir), "--seed", "1")
    env.out(capsys, "audit-pass", "--wrong", "0")
    with pytest.raises(pg.Refused, match="stored choice"):
        env.run("evaluate", "--period", "test")
    with pytest.raises(ValueError):
        env.run("choose", "--version", "4")
    assert env.out(capsys, "choose", "--version", "2") == {"choice": 2}
    with pytest.raises(pg.Refused, match="once"):
        env.run("choose", "--version", "1")
    first = env.out(capsys, "evaluate", "--period", "test")
    assert first["choice"] == 2 and first["A"]["2"]["n"] == 0 and first["A"]["2"]["mean"] == "not shown"
    assert first["pass_lines"] == {"A_counts_reached": False, "B_counts_reached": False, "A_passes": False,
                                   "B_passes": False}
    again = env.out(capsys, "evaluate", "--period", "test")
    assert "scored once" in again["note"]
    env.clock.set("2026-11-15T13:00:00Z")
    env.out(capsys, "evaluate", "--period", "test", "--more-data")
    kinds = [e["kind"] for e in sl.scoring_entries(env.root)]
    assert kinds == ["choice", "test_result", "test_result_shown", "test_result_more_data"]
    s = env.store()
    assert [r["more_data"] for r in s.rows("SELECT more_data FROM test_results ORDER BY id")] == [0, 1]
    s.close()


def test_test_period_picks_wait_for_a_choice(tmp_path, capsys):
    env, games = setup_big(tmp_path, capsys, TEST)
    settle_world(env, games, TEST)
    env.out(capsys, "audit-sample", "--out", str(env.audit_dir), "--seed", "3")
    env.out(capsys, "audit-pass", "--wrong", "0")
    env.clock.set(TEST["after"])
    r = env.out(capsys, "results")
    p = env.out(capsys, "prices")
    assert r["test_waits_for_choice"] == p["test_waits_for_choice"] == 42 and r["picks"] == p["moneyline_picks"] == 0
    assert "scores" not in env.odds.kinds() and not any("/markets" in u and "series" not in u for u in env.kalshi.urls)
    env.out(capsys, "choose", "--version", "1")
    r = env.out(capsys, "results")
    assert r["picks"] == 42 and r["scores"]["NBA"]["games_listed"] == N_GAMES
    res = env.out(capsys, "evaluate", "--period", "test")
    assert res["B"]["n"] == 42 and res["A"]["1"]["n"] == 0 and res["A"]["1"]["not_bought"] == {"no_candle": 22}


LATE = {"title": "Pick of the Day - 12/13/26 (Sunday)", "thread": "2026-12-13T12:00:00Z", "seen": "2026-12-13T20:00:00Z",
        "start": "2026-12-14T03:00:00Z", "schedule": "2026-12-13T19:00:00Z", "match": "2026-12-13T23:00:00Z",
        "event": "26DEC13"}


@pytest.mark.parametrize("now, bought", [("2026-12-14T10:59:59Z", True), ("2026-12-14T11:00:00Z", False)])
def test_without_a_choice_no_scores_are_bought_once_the_test_period_has_begun(tmp_path, capsys, now, bought):
    env, games = setup_big(tmp_path, capsys, LATE)
    settle_world(env, games, LATE)
    env.out(capsys, "audit-sample", "--out", str(env.audit_dir), "--seed", "3")
    env.out(capsys, "audit-pass", "--wrong", "0")
    env.clock.set(now)                                      # 06:00 US-Eastern on the test period's first day
    out = env.out(capsys, "results")
    assert ("scores" in env.odds.kinds()) is bought
    if not bought:
        assert out["scores"]["NBA"].startswith("refused: no choice stored")


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
