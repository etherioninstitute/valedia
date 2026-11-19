"""T54a: the forward copier's matcher and paper book. Every answer comes from a stand-in (Kalshi, Polymarket's
description service, Polymarket's data service for the record); any socket connection fails at once. The record is
built with the real leaders_monitor code. No test kills a process or races several."""
import ast
import email.message
import hashlib
import io
import json
import random
import re
import socket
import sqlite3
import urllib.parse
import urllib.request
import urllib.response
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from valedia_trader.research_loop import forward_copy as fc
from valedia_trader.research_loop import leaders_monitor as lm
from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import polymarket_data as pd

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "forward_copy"
T0 = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc).timestamp()     # 3 PM ET on the day of the games
FMAP = fc.load_map()
NBA = FMAP["_tables"]["nba"]
H = 3600.0


@pytest.fixture(autouse=True)
def no_socket_may_connect(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_no_test_can_open_a_socket():
    with pytest.raises(AssertionError, match="network"):
        socket.create_connection(("127.0.0.1", 9))


# ------------------------------------------------------------------ stand-ins
class Wall:
    """The one stand-in clock: wall time, the pacer's clock, and every wait advances it."""
    def __init__(self, t=T0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def wallet(i):
    return "0x%040x" % (0xbeef00 + i)


def board_row(rank, user):
    return {"rank": rank, "user_id": user, "pnl": 1e6 - rank, "volume": 5e6, "user_name": "u", "verified": False}


def pos(w, tok, cid, title, outcome, slug, value=5000.0, price=0.5, opp=None):
    return {"proxy_wallet": w, "token_id": tok, "condition_id": cid, "current_size": round(value / price, 4) if price
            else 10.0, "avg_price": 0.4, "entry_cost_usdc": 1.0, "total_cost_usdc": 1.0, "current_value": value,
            "total_size": 10.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0, "status": "OPEN",
            "title": title, "outcome": outcome, "end_date": "2026-11-18", "current_price": price, "event_slug": slug,
            "event_id": "e1", "opposite_token_id": opp, "outcome_index": 0}


class PMWorld:
    """Polymarket's data service for the record: the boards and each wallet's positions."""
    def __init__(self, wall=None):
        self.boards = {"sports": [board_row(i + 1, wallet(i)) for i in range(5)],
                       "crypto": [board_row(i + 1, wallet(i + 5)) for i in range(5)]}
        self.held: dict = {}
        self.wall, self.advance = wall, 0.0          # `advance`: seconds each wallet's read takes (reads apart)
        self.fail: set = set()                       # wallets whose read fails in this pass

    def __call__(self, url):
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == "/v2/leaderboard":
            rows = self.boards.get(q.get("category"), [])[: int(q.get("limit", 100))]
        else:
            if self.advance and self.wall is not None:
                self.wall.t += self.advance
            if q["user"] in self.fail:
                raise pd.HttpError(403, url)
            rows = self.held.get(q["user"], [])
        return json.dumps({"data": rows, "pagination": {"limit": 1000, "offset": 0, "has_more": False,
                                                        "next_cursor": None}}).encode()


class Record:
    """A leaders' record made by leaders_monitor itself through stand-in services."""
    def __init__(self, path, wall):
        self.path, self.wall, self.world = Path(path), wall, PMWorld(wall)
        assert self._run(["fix-leaders"]) == 0

    def _run(self, argv):
        client = pd.PolymarketData(self.world, sleep=lambda s: None, clock=lambda: 0.0, wall=self.wall)
        return lm.main(argv + ["--store", str(self.path)], client=client, wall=self.wall, out=io.StringIO(),
                       memory=lambda: 10.0)

    def pass_(self, held: dict, fail=()):
        self.world.held = {w: list(rows) for w, rows in held.items()}
        self.world.fail = set(fail)
        code = self._run(["pass"])
        self.world.fail = set()
        assert code == 0 or fail, code

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def book_levels(prices_sizes):
    """Kalshi's way: dollar strings, ascending, best last."""
    return [["%.4f" % p, "%.2f" % s] for p, s in sorted(prices_sizes)]


class KWorld:
    """Kalshi's public API: open events by series, books, market records. Every call is logged with the clock."""
    def __init__(self, wall):
        self.wall, self.events, self.books, self.records, self.fail, self.calls = wall, {}, {}, {}, set(), []
        self.seq: dict = {}                          # ticker -> books answered in turn before `books` (a book moves)

    def __call__(self, url):
        assert fc.KALSHI_URL.fullmatch(url), url                     # only what the copier's own fetch would send
        parts = urllib.parse.urlsplit(url[len(kd.BASE_URL):])
        path, q = parts.path, dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((self.wall.t, path, q))
        if path in self.fail or any(f in path for f in self.fail):
            raise kd.HttpError(500, url)
        if path == "/events":
            return json.dumps({"events": self.events.get(q["series_ticker"], []), "cursor": ""}).encode()
        m = re.fullmatch(r"/markets/([^/]+)/orderbook", path)
        if m:
            if self.seq.get(m.group(1)):
                return json.dumps({"orderbook_fp": self.seq[m.group(1)].pop(0)}).encode()
            return json.dumps({"orderbook_fp": self.books.get(m.group(1), {"yes_dollars": [], "no_dollars": []})}
                              ).encode()
        m = re.fullmatch(r"/(?:historical/)?markets/([^/]+)", path)
        if m and m.group(1) in self.records:
            return json.dumps({"market": self.records[m.group(1)]}).encode()
        if m and m.group(1) in self.books:                     # a known market, still trading
            return json.dumps({"market": {"ticker": m.group(1), "market_type": "binary", "status": "active",
                                          "result": "", "settlement_value_dollars": None, "settlement_ts": None}}
                              ).encode()
        raise kd.HttpError(404, url)

    def kinds(self, since=0):
        out = []
        for _t, path, _q in self.calls[since:]:
            out.append("list" if path == "/events" else "book" if path.endswith("/orderbook") else "market")
        return out


class GWorld:
    """Polymarket's market-description service."""
    def __init__(self):
        self.markets, self.calls, self.fail = {}, [], set()

    def __call__(self, url):
        assert fc.GAMMA_URL.fullmatch(url), url
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.calls.append(q["condition_ids"])
        if q["condition_ids"] in self.fail:
            raise fc.GammaError("HTTP 500")
        m = self.markets.get(q["condition_ids"])
        rows = [m] if m is not None and bool(m.get("closed")) == (q["closed"] == "true") else []
        return json.dumps(rows).encode()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Game:
    """One NBA game on both venues, its texts filled from the map's templates (the real texts' shape)."""
    def __init__(self, a, b, day=date(2026, 11, 17), when="10:00 PM"):
        self.a, self.b, self.day = a, b, day
        ra, rb = NBA[a], NBA[b]
        h = hashlib.sha256(("%s|%s|%s" % (a, b, day)).encode()).hexdigest()
        self.cid = "0x" + h
        self.tok = {a: str(int(h[:15], 16)), b: str(int(h[15:30], 16))}
        self.slug = "nba-%s-%s-%s" % (ra["slug"], rb["slug"], day.isoformat())
        d2 = day + timedelta(days=2)
        t = FMAP["series"]["KXNBAGAME"]["templates"]
        self.pm_text = fc.fill(t["polymarket"], t1=a, t2=b, wday=fc.WDAYS[day.weekday()], month=fc.MONTHS[day.month - 1],
                               day=day.day, year=day.year, time=when, w1=a, w2=b, wday2=fc.WDAYS[d2.weekday()],
                               month2=fc.MONTHS[d2.month - 1], day2=d2.day, year2=d2.year)
        self.event_ticker = "KXNBAGAME-%s%s%s" % (fc.kalshi_day(day), ra["kalshi_code"], rb["kalshi_code"])
        title = "%s vs %s" % (ra["kalshi_words"], rb["kalshi_words"])
        close = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() + 3 * 86400
        self.markets = {}
        for team, r in ((a, ra), (b, rb)):
            self.markets[team] = {
                "ticker": self.event_ticker + "-" + r["kalshi_code"], "event_ticker": self.event_ticker,
                "yes_sub_title": r["kalshi_words"], "status": "active", "close_time": iso(close),
                "market_type": "binary",
                "rules_primary": fc.fill(t["kalshi_primary"], team=r["kalshi_words"], k1=ra["kalshi_words"],
                                         k2=rb["kalshi_words"], kmon=fc.MONS[day.month - 1], kday=day.day,
                                         kyear=day.year),
                "rules_secondary": fc.fill(t["kalshi_secondary"])}
        self.event = {"event_ticker": self.event_ticker, "series_ticker": "KXNBAGAME", "title": title,
                      "markets": list(self.markets.values())}

    def holding(self, w, team, value=5000.0, price=0.5):
        other = self.b if team == self.a else self.a
        return pos(w, self.tok[team], self.cid, "%s vs. %s" % (self.a, self.b), team, self.slug, value, price,
                   opp=self.tok[other])

    def gamma(self, closed=False, winner=None):
        prices = ["0.5", "0.5"] if winner is None else ["1" if winner == self.a else "0",
                                                         "1" if winner == self.b else "0"]
        return {"conditionId": self.cid, "description": self.pm_text, "question": "%s vs. %s" % (self.a, self.b),
                "outcomes": json.dumps([self.a, self.b]), "closed": closed,
                "clobTokenIds": json.dumps([self.tok[self.a], self.tok[self.b]]), "outcomePrices": json.dumps(prices),
                "umaResolutionStatus": "resolved" if closed else None}

    def install(self, kw, gw, no_bid=0.45, yes_bid=0.40):
        kw.events.setdefault("KXNBAGAME", []).append(self.event)
        for m in self.markets.values():
            kw.books[m["ticker"]] = {"yes_dollars": book_levels([(yes_bid, 100.0), (yes_bid - 0.02, 50.0)]),
                                     "no_dollars": book_levels([(no_bid, 77.0), (no_bid - 0.03, 10.0)])}
        gw.markets[self.cid] = self.gamma()
        return self


PAIRS = (("Jazz", "Clippers"), ("Hornets", "Lakers"), ("Celtics", "Knicks"), ("Heat", "Magic"), ("Suns", "Spurs"),
         ("Nets", "Bulls"), ("Hawks", "Pacers"), ("Bucks", "Cavaliers"), ("76ers", "Raptors"), ("Pistons", "Wizards"),
         ("Thunder", "Nuggets"), ("Warriors", "Kings"))


def games(kw, gw, n, **k):
    return [Game(a, b).install(kw, gw, **k) for a, b in PAIRS[:n]]


def run_step(rec, book, kw, gw, wall, cap=60, pause=10.0, hook=None):
    out = io.StringIO()
    code = fc.main(["step", "--record", str(rec.path), "--book", str(book), "--kalshi-pause-seconds", str(pause),
                    "--kalshi-cap", str(cap)], kalshi_fetch=kw, gamma_fetch=gw, wall=wall, sleep=wall.sleep,
                   clock=wall, out=out, write_hook=hook)
    return code, out.getvalue()


def rows(book, sql, params=()):
    con = sqlite3.connect(Path(book).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def skips(book):
    return dict(((w, t), r) for w, t, r in rows(book, "SELECT wallet_key, token_id, reason FROM skips"))


@pytest.fixture
def env(tmp_path):
    wall = Wall()
    rec = Record(tmp_path / "leaders.sqlite", wall)
    kw, gw = KWorld(wall), GWorld()
    return rec, tmp_path / "book.sqlite", kw, gw, wall


# ------------------------------------------------------------------ 1. the map, as data
def _doc(name):
    return (ROOT / "docs" / "research" / name).read_text()


def _table(text):
    return [[c.strip() for c in line.strip().strip("|").split("|")] for line in text.splitlines()
            if re.match(r"\|\s*KX", line)]


def test_map_series_and_classes_are_the_rule_maps():
    doc = _table(_doc("forward-copy-rule-map.md"))
    want = {r[0]: r[2].split()[0] for r in doc}
    assert want == {s: e["class"] for s, e in FMAP["series"].items()}
    assert {s for s, e in FMAP["series"].items() if e.get("half_point_lines_only")} == \
        {r[0] for r in doc if "half-point lines only" in r[2]}


def test_map_listing_rows_are_the_listing_tables():
    doc = _table(_doc("forward-copy-listing-table.md"))
    text = _doc("forward-copy-listing-table.md")
    doc = _table(text.split("\n## ")[0])                     # the table of series listed as tradable, nothing below
    assert [(r[0], r[1], r[2]) for r in doc] == [(x["series"], x["seen"], x["how"]) for x in FMAP["listing_table"]]
    both = re.search(r"In the rule map AND in this table \(so a copy may be made\): ([A-Z, ]+)\.", text).group(1)
    assert FMAP["_listed"] & set(FMAP["series"]) == set(both.replace(",", " ").split()) == \
        {"KXNBAGAME", "KXNBASPREAD", "KXPREMIERLEAGUE"}
    not_tradable = text.split("## Seen listed but NOT tradable")[1].split("\n## ")[0]
    assert "KXPRESNOMD" in FMAP["series"] and "KXPRESNOMD" in not_tradable and "KXPRESNOMD" not in FMAP["_listed"]


def test_the_words_the_map_file_quotes_are_in_the_rule_map():
    doc = fc.normal(_doc("forward-copy-rule-map.md"))
    for s, e in FMAP["series"].items():
        for words in e["map_words"]:
            assert fc.normal(words) in doc, (s, words)
    for fam in FMAP["near_families"]:
        assert fc.normal(fam["map_words"]) in doc


def _pm(name):
    return json.loads((FIX / "pm_markets" / name).read_text())


def _events(series):
    return json.loads((FIX / ("kalshi_events_%s.json" % series)).read_text())["events"]


JAZZ = "0xf705c405b0962ab4a25eefe5eb9849600d26b5b0d2fc9519f25d570882a22061"
SPREAD = "0x46956f6a1efcb8b8c434ada1acdfea2a67e768243e9dc0ad9485404fa823232d"
CITY = "0x86e8f678e6cebb9596ffbb5a736b2ff9f36b12905d5b65b1556329c28533c7a1"
OSSOFF = "0x74dba1ce1ae9dd535414e85f2d9ab5ea32c0fb1acc9b7130b67e6d91217e24e1"


def test_templates_fit_every_saved_real_text():
    pairs = [("KXNBAGAME", _pm(JAZZ + ".closed.json")[0]), ("KXNBASPREAD", _pm(SPREAD + ".closed.json")[0]),
             ("KXPREMIERLEAGUE", _pm(CITY + ".json")[0]), ("KXPRESNOMD", _pm(OSSOFF + ".json")[0])]
    for series, pm in pairs:
        c = FMAP["_compiled"][series]
        assert c["polymarket"].fullmatch(fc.normal(pm["description"])), series
        markets = [m for e in _events(series) for m in e["markets"]]
        assert markets
        for m in markets:
            assert c["kalshi_primary"].fullmatch(fc.normal(m["rules_primary"])), m["ticker"]
            assert c["kalshi_secondary"].fullmatch(fc.normal(m["rules_secondary"])), m["ticker"]


def test_the_name_tables_join_words_seen_on_both_venues():
    kalshi = {m["ticker"].rsplit("-", 1)[1]: m["yes_sub_title"] for s in ("KXNBAGAME", "KXPREMIERLEAGUE",
                                                                          "KXPRESNOMD") for e in _events(s)
              for m in e["markets"]}
    for table in ("epl", "dem_nominee_2028"):
        for r in FMAP["names"][table]:
            if r["kalshi_code"] in kalshi:
                assert kalshi[r["kalshi_code"]] == r["kalshi_words"]
    for r in FMAP["names"]["nba"]:
        assert r["kalshi_code"] == r["slug"].upper()
        if r["kalshi_code"] in kalshi:
            assert kalshi[r["kalshi_code"]] == r["kalshi_words"]
    held = json.loads((FIX / "record_holdings.json").read_text())["rows"]
    seen = {(t.split(" vs. ")[i], s.split("-")[1 + i]) for t, s in ((r["title"], r["event_slug"]) for r in held)
            if s and s.startswith("nba-") and re.fullmatch(r"[^:]+ vs\. [^:]+", t) for i in (0, 1)}
    for name, code in seen:
        assert NBA[name]["slug"] == code
    assert len(FMAP["names"]["nba"]) == 30 and "Crystal Palace" not in FMAP["_tables"]["epl"]


def _h(title, outcome, slug, **kw):
    return dict({"title": title, "outcome": outcome, "event_slug": slug}, **kw)


def test_recognise_each_series_its_near_families_and_nothing_else():
    r = lambda t, o, s: fc.recognise(_h(t, o, s), FMAP)
    assert r("Jazz vs. Clippers", "Clippers", "nba-uta-lac-2026-11-17")[:2] == ("same", "KXNBAGAME")
    assert r("Spread: Lakers (-7.5)", "Hornets", "nba-cha-lal-2026-11-17")[:2] == ("same", "KXNBASPREAD")
    assert r("Spread: Lakers (-7)", "Hornets", "nba-cha-lal-2026-11-17")[0] == "none"
    assert r("Will Arsenal win the 2026-27 English Premier League (EPL) Championship?", "Yes",
             "epl-2027-champion-20260701200428749")[:2] == ("same", "KXPREMIERLEAGUE")
    assert r("Will Jon Ossoff win the 2028 Democratic presidential nomination?", "No",
             "democratic-presidential-nominee-2028")[:2] == ("same", "KXPRESNOMD")
    assert r("Jazz vs. Thunder: O/U 209.5", "Over", "nba-uta-okc-2026-11-17")[0] == "none"
    assert r("Jets vs. Patriots", "Jets", "nfl-nyj-ne-2026-10-18")[0] == "none"
    assert r("Will Oklahoma City Thunder win the 2027 NBA Finals?", "Yes", "nba-2027-champion")[0] == "none"
    assert r("Will Bitcoin dip to $80,000 in November?", "Yes", "x") == ("near", "crypto price level by a date")
    assert r("Will Bitcoin hit $150k by December 31, 2026?", "Yes", "x") == ("near", "crypto price level by a date")
    assert r("Will Bitcoin reach $100,000 November 17-23?", "Yes", "x") == ("near", "crypto price level by a date")
    assert r("Will the price of Ethereum be above $3,000 on November 18?", "No", "x") == \
        ("near", "crypto price on a date")
    assert r("Will WTI Crude Oil (WTI) hit (HIGH) $80 in November?", "Yes", "x")[0] == "none"


def _resolve(title, outcome, slug):
    what = fc.recognise(_h(title, outcome, slug), FMAP)
    return fc.resolve_names(what[1], what[2], FMAP)


def test_names_never_guessed():
    for args in (("Will Crystal Palace win the 2026-27 English Premier League (EPL) Championship?", "Yes",
                  "epl-2027-champion-1"),
                 ("Will Gavin Newsom win the 2028 Democratic presidential nomination?", "Yes",
                  "democratic-presidential-nominee-2028"),
                 ("Jazz vs. Clippers", "Clippers", "nba-uta-lal-2026-11-17"),        # the slug names other teams
                 ("Jazz vs. Clippers", "Draw", "nba-uta-lac-2026-11-17"),
                 ("Spread: Celtics (-7.5)", "Hornets", "nba-cha-lal-2026-11-17")):
        with pytest.raises(fc.Skip) as e:
            _resolve(*args)
        assert e.value.reason == "name_not_in_table", args


def test_each_series_finds_its_one_market_and_side_on_the_real_answers():
    def active(series):
        evs = json.loads(json.dumps(_events(series)))
        for e in evs:
            for m in e["markets"]:
                m.update(status="active", close_time="2099-01-01T00:00:00Z")
        return evs
    cases = [("KXNBAGAME", ("Jazz vs. Clippers", "Clippers", "nba-uta-lac-2026-11-17"),
              "KXNBAGAME-26NOV17UTALAC-LAC", "yes", _pm(JAZZ + ".closed.json")[0]),
             ("KXNBASPREAD", ("Spread: Lakers (-7.5)", "Hornets", "nba-cha-lal-2026-11-17"),
              "KXNBASPREAD-26NOV17CHALAL-LAL8", "no", _pm(SPREAD + ".closed.json")[0]),
             ("KXNBASPREAD", ("Spread: Lakers (-7.5)", "Lakers", "nba-cha-lal-2026-11-17"),
              "KXNBASPREAD-26NOV17CHALAL-LAL8", "yes", _pm(SPREAD + ".closed.json")[0]),
             ("KXPREMIERLEAGUE", ("Will Manchester City win the 2026-27 English Premier League (EPL) Championship?",
                                  "No", "epl-2027-champion-20260701200428749"), "KXPREMIERLEAGUE-27-MCI", "no",
              _pm(CITY + ".json")[0]),
             ("KXPRESNOMD", ("Will Jon Ossoff win the 2028 Democratic presidential nomination?", "Yes",
                             "democratic-presidential-nominee-2028"), "KXPRESNOMD-28-JOSS", "yes",
              _pm(OSSOFF + ".json")[0])]
    for series, args, ticker, side, pm in cases:
        names = _resolve(*args)
        ev, m = fc.find_market(series, names, active(series))
        assert (m["ticker"], names["side"]) == (ticker, side)
        fc.check_texts(series, names, ev, m, pm["description"], FMAP)
        with pytest.raises(fc.Skip) as e:
            fc.check_texts(series, names, ev, m, pm["description"].replace("resolve", "settle", 1), FMAP)
        assert e.value.reason == "text_departs"
        bad = dict(m, rules_secondary=(m["rules_secondary"] or "") + " Or not.")
        with pytest.raises(fc.Skip):
            fc.check_texts(series, names, ev, bad, pm["description"], FMAP)


def test_texts_with_another_date_or_window_depart():
    g = Game("Jazz", "Clippers")
    names = _resolve("Jazz vs. Clippers", "Clippers", g.slug)
    m = g.markets["Clippers"]
    fc.check_texts("KXNBAGAME", names, g.event, m, g.pm_text, FMAP)
    for pm, km in ((g.pm_text.replace("November 19", "November 20"), m),
                   (g.pm_text.replace("November 17", "November 16"), m),
                   (g.pm_text, dict(m, rules_primary=m["rules_primary"].replace("Nov 17", "Nov 18"))),
                   (g.pm_text, dict(m, rules_secondary=m["rules_secondary"].replace("48 hours", "72 hours", 1)))):
        with pytest.raises(fc.Skip) as e:
            fc.check_texts("KXNBAGAME", names, g.event, km, pm, FMAP)
        assert e.value.reason == "text_departs"


def test_no_unique_market_when_none_or_two():
    g = Game("Jazz", "Clippers")
    names = _resolve("Jazz vs. Clippers", "Clippers", g.slug)
    for events in ([], [g.event, g.event]):
        with pytest.raises(fc.Skip) as e:
            fc.find_market("KXNBAGAME", names, events)
        assert e.value.reason == "no_unique_market"


# ------------------------------------------------------------------ 2. one step
def test_a_baseline_holding_becomes_one_paper_contract_at_the_match_read_ask(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw, no_bid=0.31)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers", 176736.0, 0.715)]})
    wall.t += 60
    code, out = run_step(rec, book, kw, gw, wall)
    assert code == 0, out
    (c,) = rows(book, "SELECT kalshi_market, side, ask, ask_size, match_ts, pass_read_ts, status, series FROM copies")
    assert c[:4] == (g.markets["Clippers"]["ticker"], "yes", 0.69, 77.0) and c[6:] == ("open", "KXNBAGAME")
    assert c[4] - c[5] <= 6 * H
    assert kw.kinds() == ["list", "book"]           # one list, one book; the copy's market is open in that list
    texts = dict(rows(book, "SELECT venue, body FROM texts"))
    assert texts["polymarket"] == g.pm_text and "Pro Basketball game" in texts["kalshi"]
    assert gw.calls == [g.cid]


def test_a_step_on_the_same_record_considers_nothing_again(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    n = len(kw.calls)
    code, out = run_step(rec, book, kw, gw, wall)
    assert code == 0 and "considered 0 of 0" in out
    assert kw.kinds(n) == ["list"] and len(gw.calls) == 1        # only the open copy's series list (settlement)


def test_only_vouched_new_positions_after_the_baseline(env):
    rec, book, kw, gw, wall = env
    g1, g2 = Game("Jazz", "Clippers").install(kw, gw), Game("Hornets", "Lakers").install(kw, gw)
    rec.pass_({wallet(0): [g1.holding(wallet(0), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    wall.t += 2 * H
    rec.pass_({wallet(0): [g1.holding(wallet(0), "Clippers"), g2.holding(wallet(0), "Hornets")]})
    wall.t += 60
    run_step(rec, book, kw, gw, wall)
    got = rows(book, "SELECT c.kind, p.kalshi_market FROM copies p JOIN considered c USING (wallet_key, read_id, "
                     "token_id) ORDER BY p.copy_id")
    assert got == [("baseline", g1.markets["Clippers"]["ticker"]), ("new", g2.markets["Hornets"]["ticker"])]
    assert rows(book, "SELECT count(*) FROM considered")[0][0] == 2


def test_each_condition_skips_with_its_one_reason(env):
    rec, book, kw, gw, wall = env
    games = [Game(a, b) for a, b in (("Jazz", "Clippers"), ("Hornets", "Lakers"), ("Celtics", "Knicks"),
                                     ("Heat", "Magic"), ("Suns", "Spurs"), ("Nets", "Bulls"), ("Hawks", "Pacers"))]
    for g in games[:4]:
        g.install(kw, gw)
    games[4].install(kw, gw, no_bid=0.02)                                   # ask 0.98
    games[5].install(kw, gw)
    games[5].markets["Nets"]["status"] = "closed"
    games[6].install(kw, gw)
    gw.markets[games[6].cid] = dict(games[6].gamma(), description=games[6].pm_text.replace("50-50", "No"))
    w0, w1 = wallet(0), wallet(1)
    city = pos(w0, "9001", OSSOFF, "Will Jon Ossoff win the 2028 Democratic presidential nomination?", "Yes",
               "democratic-presidential-nominee-2028")                     # in the rule map, NOT tradable: no copy
    near = pos(w0, "9002", "0x" + "1" * 64, "Will Bitcoin dip to $80,000 in November?", "Yes", "x")
    other = pos(w0, "9003", "0x" + "2" * 64, "Will Apple be the largest company in the world by market cap on "
                "December 31?", "Yes", "x")
    held = {w0: [games[0].holding(w0, "Clippers", value=999.99), games[1].holding(w0, "Hornets", price=0.91),
                 games[2].holding(w0, "Celtics"), games[3].holding(w0, "Heat"), games[4].holding(w0, "Suns"),
                 games[5].holding(w0, "Nets"), games[6].holding(w0, "Hawks"), city, near, other],
            w1: [games[2].holding(w1, "Knicks"), games[3].holding(w1, "Heat")]}
    rec.pass_(held)
    code, out = run_step(rec, book, kw, gw, wall)
    assert code == 0, out
    s = skips(book)
    tok = lambda g, t: g.tok[t]
    assert s[(w0, tok(games[0], "Clippers"))] == "value_under_1000"
    assert s[(w0, tok(games[1], "Hornets"))] == "price_outside"
    assert s[(w0, tok(games[2], "Celtics"))] == "leaders_disagree"
    assert s[(w1, tok(games[2], "Knicks"))] == "leaders_disagree"
    assert s[(w1, tok(games[3], "Heat"))] == "already_open"                 # w0 opened it first (its key sorts first)
    assert s[(w0, tok(games[4], "Suns"))] == "ask_outside"
    assert s[(w0, tok(games[5], "Nets"))] == "market_not_open"
    assert s[(w0, tok(games[6], "Hawks"))] == "text_departs"
    assert s[(w0, "9001")] == "not_listed" and s[(w0, "9002")] == "no_same" and s[(w0, "9003")] == "no_same"
    assert rows(book, "SELECT family FROM near_book") == [("crypto price level by a date",)]
    assert rows(book, "SELECT kalshi_market FROM copies") == [(games[3].markets["Heat"]["ticker"],)]
    assert rows(book, "SELECT role FROM copy_wallets ORDER BY role") == [("noted",), ("opened",)]
    assert kw.kinds().count("list") == 1                                     # one list for the series, all holdings


def test_late_holdings_make_no_request(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    wall.t += 6 * H + 1
    run_step(rec, book, kw, gw, wall)
    assert skips(book) == {(wallet(0), g.tok["Clippers"]): "late"} and kw.calls == [] and gw.calls == []


def test_a_match_read_that_lands_past_six_hours_is_late(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    wall.t += 6 * H - 5                                    # the list read's pause carries the book read past 6 h
    run_step(rec, book, kw, gw, wall)
    assert skips(book) == {(wallet(0), g.tok["Clippers"]): "late"} and kw.kinds() == ["list", "book"]


def test_a_failed_list_read_is_asked_once_a_step(env):
    rec, book, kw, gw, wall = env
    g1, g2 = Game("Jazz", "Clippers").install(kw, gw), Game("Hornets", "Lakers").install(kw, gw)
    kw.fail.add("/events")
    rec.pass_({wallet(0): [g1.holding(wallet(0), "Clippers"), g2.holding(wallet(0), "Hornets")]})
    run_step(rec, book, kw, gw, wall)
    assert set(skips(book).values()) == {"read_failed"} and kw.kinds() == ["list"]


def test_a_failed_book_read_is_a_skip(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    kw.fail.add("/orderbook")
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    assert skips(book) == {(wallet(0), g.tok["Clippers"]): "read_failed"}


def test_the_description_is_asked_once_a_market(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw, no_bid=0.99)                # ask 0.01: no copy, text kept
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    kw.books[g.markets["Clippers"]["ticker"]]["no_dollars"] = book_levels([(0.40, 5.0)])
    wall.t += H
    rec.pass_({wallet(1): [g.holding(wallet(1), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    assert gw.calls == [g.cid] and rows(book, "SELECT count(*) FROM copies")[0][0] == 1


def _book(no_bid, yes_bid=0.40):
    return {"yes_dollars": book_levels([(yes_bid, 100.0)]), "no_dollars": book_levels([(no_bid, 77.0)])}


def test_one_match_read_for_a_market_and_a_pass(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    t = g.markets["Clippers"]["ticker"]
    kw.seq[t] = [_book(0.02), _book(0.50)]                  # the first read asks 0.98; a second would ask 0.50
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")], wallet(1): [g.holding(wallet(1), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    assert set(skips(book).values()) == {"ask_outside"} and rows(book, "SELECT count(*) FROM copies")[0][0] == 0
    assert kw.kinds().count("book") == 1
    (r,) = rows(book, "SELECT kalshi_market, pass_key, ok FROM match_reads")
    assert r == (t, _pass_of(rec, wallet(0))[-1], 1)          # that pass: the record's pass of the read showing it


def _pass_of(rec, w):
    con = sqlite3.connect(rec.path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return [p for (p,) in con.execute("SELECT pass_id FROM wallet_reads WHERE wallet_key=? AND good=1 ORDER BY "
                                          "read_id", (w,))]
    finally:
        con.close()


def test_the_first_read_judges_every_holding_of_that_pass_by_its_own_window(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.world.advance = 600.0                                # each wallet's read takes ten minutes
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")], wallet(1): [g.holding(wallet(1), "Clippers")]})
    rec.world.advance = 0.0
    con = sqlite3.connect(rec.path.resolve().as_uri() + "?mode=ro", uri=True)
    ts = dict(con.execute("SELECT wallet_key, read_ts FROM wallet_reads WHERE good=1"))
    con.close()
    keys = sorted((wallet(0), wallet(1)), key=ts.get)
    assert ts[keys[1]] - ts[keys[0]] >= 600.0
    wall.t = ts[keys[0]] + 6 * H - 5                    # the list's pause carries the read past the first's window
    run_step(rec, book, kw, gw, wall)
    (stamp,) = rows(book, "SELECT stamp FROM match_reads")[0]
    assert stamp - ts[keys[0]] > 6 * H and stamp - ts[keys[1]] <= 6 * H
    assert skips(book) == {(keys[0], g.tok["Clippers"]): "late"}
    (c,) = rows(book, "SELECT match_ts, ask FROM copies")
    assert c == (stamp, 0.55) and kw.kinds().count("book") == 1          # copied from the FIRST read, not a second


def test_a_failed_first_read_is_the_answer_for_that_pass_even_in_a_later_step(env):
    rec, book, kw, gw, wall = env
    g1, *more = games(kw, gw, 7)
    kw.fail.add("/markets/%s/orderbook" % g1.markets["Clippers"]["ticker"])
    rec.pass_({wallet(0): [g1.holding(wallet(0), "Clippers")], wallet(1): [g.holding(wallet(1), g.a) for g in more],
               wallet(2): [g1.holding(wallet(2), "Clippers")]})
    assert run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP)[0] == fc.CAPPED   # list, failed book, 4 books; cap
    kw.fail.clear()
    for _ in range(4):
        if run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP)[0] == 0:
            break
    s = skips(book)
    assert s[(wallet(0), g1.tok["Clippers"])] == "read_failed" == s[(wallet(2), g1.tok["Clippers"])]
    books = [p for _t, p, _q in kw.calls if p.endswith("/orderbook")]
    assert books.count("/markets/%s/orderbook" % g1.markets["Clippers"]["ticker"]) == 1   # no second try
    assert sorted(rows(book, "SELECT kalshi_market FROM copies")) == sorted((g.markets[g.a]["ticker"],) for g in more)


def test_two_passes_in_one_step_have_each_their_own_first_read(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw, no_bid=0.02)             # ask 0.98: never copied
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")], wallet(1): []})
    wall.t += H
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")], wallet(1): [g.holding(wallet(1), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    p1, p2 = _pass_of(rec, wallet(0))
    assert p1 != p2 and _pass_of(rec, wallet(1))[-1] == p2
    assert sorted(r[0] for r in rows(book, "SELECT pass_key FROM match_reads")) == [p1, p2]
    assert kw.kinds().count("book") == 2 and set(skips(book).values()) == {"ask_outside"}


def test_a_pass_still_running_is_left_for_a_later_step(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    con = sqlite3.connect(str(rec.path))                     # the test's own record: a pass shown still running
    con.execute("UPDATE passes SET status='running', finished_ts=NULL")
    con.commit()
    con.close()
    code, out = run_step(rec, book, kw, gw, wall)
    assert code == 0 and "considered 0 of 0" in out and kw.calls == []


def test_a_failed_description_is_asked_once_a_step(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    gw.fail.add(g.cid)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")], wallet(1): [g.holding(wallet(1), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    assert set(skips(book).values()) == {"read_failed"} and gw.calls == [g.cid]


def test_a_series_whose_class_is_not_same_is_never_copied(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    fmap = fc.load_map()
    fmap["series"]["KXNBAGAME"]["class"] = "NEAR"
    code = fc.main(["step", "--record", str(rec.path), "--book", str(book)], kalshi_fetch=kw, gamma_fetch=gw,
                   wall=wall, sleep=wall.sleep, clock=wall, out=io.StringIO(), fmap=fmap)
    assert code == 0 and rows(book, "SELECT count(*) FROM copies")[0][0] == 0
    assert set(skips(book).values()) == {"no_same"} and kw.calls == []


def test_an_unvouched_new_is_never_copied(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    bad = dict(pos(wallet(0), "777", "0x" + "7" * 64, "x", "Yes", "x"), current_value="not a number")
    rec.pass_({wallet(0): [bad]})                            # a read with a rejected row: not clean
    wall.t += H
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    con = sqlite3.connect(rec.path.resolve().as_uri() + "?mode=ro", uri=True)
    assert con.execute("SELECT vouched FROM changes WHERE kind='NEW' AND token_id=?",
                       (g.tok["Clippers"],)).fetchall() == [(0,)]
    con.close()
    run_step(rec, book, kw, gw, wall)
    assert rows(book, "SELECT count(*) FROM copies")[0][0] == 0
    assert (wallet(0), g.tok["Clippers"]) not in {(w, t) for w, _r, t, _o in
                                                  rows(book, "SELECT wallet_key, read_id, token_id, outcome FROM "
                                                             "considered")}


def test_the_cap_stops_a_step_cleanly_and_the_next_carries_on(env, tmp_path):
    rec, book, kw, gw, wall = env
    gs = games(kw, gw, 8)
    rec.pass_({wallet(0): [g.holding(wallet(0), g.a) for g in gs]})
    codes, per_step = [], []
    for _ in range(6):
        n = len(kw.calls)
        code, out = run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP)
        codes.append(code)
        per_step.append(len(kw.calls) - n)
        if code == 0:
            break
    assert codes[0] == fc.CAPPED and codes[-1] == 0 and max(per_step) <= fc.MIN_CAP
    assert sorted(r[0] for r in rows(book, "SELECT kalshi_market FROM copies")) == \
        sorted(g.markets[g.a]["ticker"] for g in gs)
    assert rows(book, "SELECT count(*) FROM considered")[0][0] == 8


def test_the_pause_is_never_under_five_seconds_and_spaces_every_request(env):
    rec, book, kw, gw, wall = env
    with pytest.raises(SystemExit):
        run_step(rec, book, kw, gw, wall, pause=4.9)
    with pytest.raises(SystemExit):
        run_step(rec, book, kw, gw, wall, cap=1)
    with pytest.raises(SystemExit):
        run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP - 1)
    with pytest.raises(ValueError):
        fc.StepPacer(4.99, 60)
    games = [Game(a, b).install(kw, gw) for a, b in (("Jazz", "Clippers"), ("Hornets", "Lakers"))]
    rec.pass_({wallet(0): [g.holding(wallet(0), g.a) for g in games]})
    run_step(rec, book, kw, gw, wall, pause=7.0)
    times = [t for t, _p, _q in kw.calls]
    assert len(times) == 3 and all(b - a >= 7.0 - 1e-9 for a, b in zip(times, times[1:]))


def test_the_pause_holds_across_steps(env):
    rec, book, kw, gw, wall = env
    gs = games(kw, gw, 8)
    rec.pass_({wallet(0): [g.holding(wallet(0), g.a) for g in gs]})
    codes = [run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP)[0] for _ in range(3)]   # one after another
    assert codes[0] == fc.CAPPED and len(kw.calls) >= fc.MIN_CAP + 2
    times = [t for t, _p, _q in kw.calls]
    assert all(b - a >= 10.0 - 1e-9 for a, b in zip(times, times[1:])), times
    kept = float(rows(book, "SELECT value FROM meta WHERE key='kalshi_last'")[0][0])
    assert kept == times[-1]
    slept = []
    p = fc.StepPacer(10.0, 5, sleep=slept.append, clock=lambda: 0.0, wall=lambda: 1000.0, last_wall=4000.0)
    p.wait()                                                  # the clock was set back: the full pause, never none
    assert slept == [10.0]
    slept.clear()
    p = fc.StepPacer(10.0, 5, sleep=slept.append, clock=lambda: 0.0, wall=lambda: 1000.0, last_wall=996.0)
    p.wait()
    assert slept == [6.0]


def test_the_kalshi_client_has_one_try_and_the_steps_pacer():
    p = fc.StepPacer(5.0, 3, sleep=lambda s: None, clock=lambda: 0.0)
    c = fc.kalshi_client(lambda url: b"{}", p)
    assert c.pacer is p and c.tries == 1
    for _ in range(3):
        p.wait()
    with pytest.raises(fc.CapReached):
        p.wait()


def test_the_description_reader_asks_one_host_one_path_two_parameters():
    seen = []
    g = fc.GammaReader(lambda url: (seen.append(url), b"[]")[1], sleep=lambda s: None, clock=lambda: 0.0)
    assert g.market("0x" + "a" * 64, closed_first=False) is None
    assert seen == ["https://gamma-api.polymarket.com/markets?condition_ids=0x%s&closed=false" % ("a" * 64),
                    "https://gamma-api.polymarket.com/markets?condition_ids=0x%s&closed=true" % ("a" * 64)]
    with pytest.raises(fc.GammaError):
        g.ask("0x12&closed=true", False)
    for url in ("https://gamma-api.polymarket.com/events?condition_ids=0x" + "a" * 64 + "&closed=false",
                "https://evil.example/markets?condition_ids=0x" + "a" * 64 + "&closed=false",
                "http://gamma-api.polymarket.com/markets?condition_ids=0x" + "a" * 64 + "&closed=false"):
        with pytest.raises(fc.GammaError, match="refused"):
            fc.gamma_urllib_get(url)


class FakeHTTPS(urllib.request.HTTPSHandler):
    """Answers every https request in the process (no socket): a 302 to another host, or a body. Logs each request."""
    def __init__(self, status=200, body=b"{}", location="https://evil.example/trade-api/v2/events"):
        super().__init__()
        self.status, self.body, self.location, self.seen = status, body, location, []

    def https_open(self, req):
        self.seen.append((req.full_url, req.host, req.get_method(), req.get_header("User-agent")))
        headers = email.message.Message()
        if self.status in (301, 302, 303, 307, 308):
            headers["Location"] = self.location
        resp = urllib.response.addinfourl(io.BytesIO(self.body), headers, req.full_url, self.status)
        resp.msg = "x"
        return resp


KALSHI_OK = (kd.BASE_URL + "/events?series_ticker=KXNBAGAME&status=open&with_nested_markets=true&limit=200",
             kd.BASE_URL + "/events?series_ticker=KXNBAGAME&status=open&with_nested_markets=true&limit=200"
                           "&cursor=abc%3D%3D",
             kd.BASE_URL + "/markets/KXNBAGAME-26NOV17UTALAC-LAC/orderbook?depth=10",
             kd.BASE_URL + "/markets/KXNBAGAME-26NOV17UTALAC-LAC",
             kd.BASE_URL + "/historical/markets/KXNBAGAME-26NOV17UTALAC-LAC")


@pytest.mark.parametrize("reader", ["kalshi", "gamma"])
def test_the_copiers_own_fetches_follow_no_redirect_and_go_to_one_host(reader, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1")       # never routed through a proxy
    get, err = (fc.kalshi_urllib_get, (kd.HttpError,)) if reader == "kalshi" else (fc.gamma_urllib_get,
                                                                                    (fc.GammaError,))
    good = KALSHI_OK[2] if reader == "kalshi" else \
        "https://gamma-api.polymarket.com/markets?condition_ids=0x%s&closed=false" % ("a" * 64)
    fake = FakeHTTPS(302)
    monkeypatch.setattr(fc, "_HANDLERS", (fake,))
    with pytest.raises(err) as e:
        get(good)
    assert reader == "gamma" or e.value.status == 302
    assert [s[1] for s in fake.seen] == [urllib.parse.urlsplit(good).hostname]          # the redirect never asked
    assert fake.seen[0][2] == "GET" and fake.seen[0][3] == kd.USER_AGENT
    fake = FakeHTTPS(200, b"x" * (fc.MAX_BODY + 1))
    monkeypatch.setattr(fc, "_HANDLERS", (fake,))
    with pytest.raises(kd.KalshiDataError if reader == "kalshi" else fc.GammaError, match="limit"):
        get(good)
    fake = FakeHTTPS(200, b'{"ok": 1}')
    monkeypatch.setattr(fc, "_HANDLERS", (fake,))
    assert get(good) == b'{"ok": 1}'


def test_the_kalshi_fetch_refuses_any_other_address_before_sending():
    for url in KALSHI_OK:
        assert fc.KALSHI_URL.fullmatch(url), url
    bad = ("https://evil.example/trade-api/v2/markets/KXNBAGAME-26NOV17UTALAC-LAC",            # another host
           "http://api.elections.kalshi.com/trade-api/v2/markets/KXNBAGAME-26NOV17UTALAC-LAC",  # not https
           kd.BASE_URL + "/portfolio/orders",                                                  # another path
           kd.BASE_URL + "/markets/KXNBAGAME-26NOV17UTALAC-LAC/orders",
           kd.BASE_URL + "/markets/kx bad ticker/orderbook?depth=10",                          # a malformed ticker
           kd.BASE_URL + "/markets/KXNBAGAME-26NOV17UTALAC-LAC?x=1",
           kd.BASE_URL + "/markets/../portfolio",
           kd.BASE_URL + "/events?series_ticker=KXNBAGAME&status=open&with_nested_markets=true&limit=200&x=1",
           "https://api.elections.kalshi.com.evil.example/trade-api/v2/markets/KXNBAGAME-26NOV17UTALAC-LAC")
    for url in bad:
        with pytest.raises(kd.KalshiDataError, match="refused"):
            fc.kalshi_urllib_get(url)


def test_a_step_without_a_stand_in_uses_the_copiers_own_kalshi_fetch(env, monkeypatch):
    rec, book, kw, gw, wall = env
    Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [Game("Jazz", "Clippers").holding(wallet(0), "Clippers")]})
    monkeypatch.setattr(fc, "kalshi_urllib_get", kw)
    code = fc.main(["step", "--record", str(rec.path), "--book", str(book)], gamma_fetch=gw, wall=wall,
                   sleep=wall.sleep, clock=wall, out=io.StringIO())
    assert code == 0 and kw.kinds() == ["list", "book"]


# ------------------------------------------------------------------ closes
def finalize(kw, g, team, yes, ts):
    """Kalshi settles that market: its record final, and its row in the open list shown so."""
    m = g.markets[team]
    m["status"] = "finalized"
    kw.records[m["ticker"]] = {"ticker": m["ticker"], "market_type": "binary", "status": "finalized",
                               "result": "yes" if yes else "no", "settlement_value_dollars":
                               "1.0000" if yes else "0.0000", "settlement_ts": iso(ts)}


def test_settlement_checks_read_the_series_list_and_only_markets_not_open_in_it(env):
    rec, book, kw, gw, wall = env
    games = [Game(a, b).install(kw, gw) for a, b in (("Jazz", "Clippers"), ("Hornets", "Lakers"),
                                                     ("Celtics", "Knicks"), ("Heat", "Magic"))]
    rec.pass_({wallet(0): [g.holding(wallet(0), g.a) for g in games]})
    run_step(rec, book, kw, gw, wall)
    assert rows(book, "SELECT count(*) FROM copies WHERE status='open'")[0][0] == 4
    kw.events["KXNBAGAME"].remove(games[0].event)            # left the list (C = 2 of N = 4, S = 1)
    games[1].markets[games[1].a]["status"] = "closed"        # shown closed in the list, not yet settled
    n = len(kw.calls)
    wall.t += H
    assert run_step(rec, book, kw, gw, wall)[0] == 0
    assert kw.kinds(n) == ["list", "market", "market"]       # S + C requests, not N
    assert rows(book, "SELECT count(*) FROM copies WHERE status='open'")[0][0] == 4
    finalize(kw, games[0], games[0].a, True, wall.t - 60)
    wall.t += H
    run_step(rec, book, kw, gw, wall)
    assert rows(book, "SELECT kalshi_market, close_kind FROM copies WHERE status='closed'") == \
        [(games[0].markets[games[0].a]["ticker"], "settled")]


def test_a_settled_market_is_closed_at_settlement_never_by_a_later_exit(env):
    rec, book, kw, gw, wall = env
    g1, g2 = Game("Jazz", "Clippers").install(kw, gw), Game("Hornets", "Lakers").install(kw, gw, yes_bid=0.97)
    rec.pass_({wallet(0): [g1.holding(wallet(0), "Clippers"), g2.holding(wallet(0), "Hornets")]})
    run_step(rec, book, kw, gw, wall)
    settled = T0 + 5 * H
    finalize(kw, g1, "Clippers", True, settled)              # both venues settled alike, then the token is gone
    gw.markets[g1.cid] = g1.gamma(closed=True, winner="Clippers")
    finalize(kw, g2, "Hornets", True, settled)               # Kalshi final while its book still bids 0.97
    wall.t = settled + H
    rec.pass_({wallet(0): []})
    n = len(kw.calls)
    run_step(rec, book, kw, gw, wall)
    got = rows(book, "SELECT status, close_kind, close_price, close_ts FROM copies ORDER BY kalshi_market")
    assert got == [("closed", "settled", 1.0, settled)] * 2
    assert rows(book, "SELECT kind FROM copy_flags") == [("parted_pm_trading_at_settlement",)]   # g2 only
    assert "book" not in kw.kinds(n)


def test_close_a_settlement_and_the_venues_part(env):
    rec, book, kw, gw, wall = env
    g1, g2, g3 = (Game(a, b).install(kw, gw) for a, b in (("Jazz", "Clippers"), ("Hornets", "Lakers"),
                                                          ("Celtics", "Knicks")))
    rec.pass_({wallet(0): [g1.holding(wallet(0), "Clippers"), g2.holding(wallet(0), "Hornets"),
                           g3.holding(wallet(0), "Celtics")]})
    run_step(rec, book, kw, gw, wall)
    settled = T0 + 10 * H
    for g, team, yes in ((g1, "Clippers", False), (g2, "Hornets", False), (g3, "Celtics", True)):
        finalize(kw, g, team, yes, settled)
    kw.events["KXNBAGAME"].remove(g3.event)                  # a market that leaves the open list is still settled
    gw.markets[g1.cid] = g1.gamma(closed=True, winner="Jazz")               # agrees: Clippers lost on both
    gw.markets[g2.cid] = g2.gamma(closed=True, winner="Hornets")            # Polymarket paid Hornets: parted
    gw.markets[g3.cid] = g3.gamma(closed=False)                             # still trading at Kalshi's settlement
    wall.t = settled + H
    run_step(rec, book, kw, gw, wall)
    got = {r[0]: r[1:] for r in rows(book, "SELECT kalshi_market, status, close_kind, close_price, close_ts "
                                           "FROM copies")}           # copies are made in token order, so by ticker
    t1, t2, t3 = g1.markets["Clippers"]["ticker"], g2.markets["Hornets"]["ticker"], g3.markets["Celtics"]["ticker"]
    assert got == {t1: ("closed", "settled", 0.0, settled), t2: ("closed", "settled", 0.0, settled),
                   t3: ("closed", "settled", 1.0, settled)}
    flags = rows(book, "SELECT c.kalshi_market, f.kind FROM copy_flags f JOIN copies c USING (copy_id) "
                       "ORDER BY c.kalshi_market")
    assert sorted(flags) == sorted([(t2, "parted_settled_differently"), (t3, "parted_pm_trading_at_settlement")])


def test_close_on_the_opening_wallets_exit_at_the_bid(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw, yes_bid=0.52)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")], wallet(1): []})
    run_step(rec, book, kw, gw, wall)
    wall.t += H
    rec.pass_({wallet(1): [g.holding(wallet(1), "Clippers")]})              # a noted wallet; the opener exits
    run_step(rec, book, kw, gw, wall)
    (c,) = rows(book, "SELECT status, close_kind, close_price, close_ts FROM copies")
    assert c[:3] == ("closed", "exit", 0.52) and c[3] > T0 + H
    assert rows(book, "SELECT reason FROM skips") == [("already_open",)]      # new holdings come before the closes
    assert rows(book, "SELECT role FROM copy_wallets ORDER BY role") == [("noted",), ("opened",)]


def test_an_exit_after_polymarket_closed_is_the_venues_parting_held(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    gw.markets[g.cid] = g.gamma(closed=True, winner="Clippers")
    wall.t += 5 * H
    rec.pass_({wallet(0): []})
    n = len(kw.calls)
    run_step(rec, book, kw, gw, wall)
    assert rows(book, "SELECT status FROM copies") == [("open",)]
    assert rows(book, "SELECT kind FROM copy_flags") == [("parted_pm_closed_first",)]
    assert "book" not in kw.kinds(n)


def test_an_exit_with_no_bid_is_held_and_flagged(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")]})
    run_step(rec, book, kw, gw, wall)
    kw.books[g.markets["Clippers"]["ticker"]]["yes_dollars"] = []
    wall.t += 5 * H
    rec.pass_({wallet(0): []})
    run_step(rec, book, kw, gw, wall)
    run_step(rec, book, kw, gw, wall)
    assert rows(book, "SELECT status FROM copies") == [("open",)]
    assert rows(book, "SELECT kind FROM copy_flags") == [("exit_no_bid",)]
    con = sqlite3.connect(str(book), isolation_level=None)   # flags and notes are append-only (R47)
    for sql in ("UPDATE copy_flags SET kind='kalshi_fair_price'", "UPDATE copy_flags SET detail='x'",
                "DELETE FROM copy_flags", "UPDATE copy_wallets SET role='noted'", "UPDATE copy_wallets SET ts=0",
                "DELETE FROM copy_wallets", "UPDATE match_reads SET stamp=0", "DELETE FROM match_reads"):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql)
    con.close()


def test_settlement_reading_is_strict():
    rec = {"ticker": "K-1", "market_type": "binary", "status": "finalized", "result": "yes",
           "settlement_value_dollars": "1.0000", "settlement_ts": "2026-11-18T05:51:49.724819Z"}
    now = T0 + 86400
    assert fc.settlement(rec, "K-1", "yes", now)[:1] == (1.0,) and fc.settlement(rec, "K-1", "no", now)[0] == 0.0
    assert fc.settlement(dict(rec, status="determined"), "K-1", "yes", now) is None
    assert fc.settlement(dict(rec, settlement_value_dollars="0.0000"), "K-1", "yes", now) is None
    assert fc.settlement(dict(rec, ticker="K-2"), "K-1", "yes", now) is None
    assert fc.settlement(rec, "K-1", "yes", T0) is None
    assert fc.settlement(dict(rec, result="", settlement_value_dollars="0.5000"), "K-1", "no", now) == \
        (0.5, fc.iso_ts("2026-11-18T05:51:49.724819Z"), True)
    real = json.loads((FIX / "kalshi_events_KXNBAGAME.json").read_text())["events"][0]["markets"]
    lac = next(m for m in real if m["ticker"].endswith("-LAC"))
    assert fc.settlement({k: lac.get(k) for k in kd.MARKET_RECORD_KEYS}, lac["ticker"], "yes", now)[0] == 0.0


# ------------------------------------------------------------------ 3. the book
def test_only_step_makes_a_book_and_its_shape_is_checked(env, tmp_path):
    rec, book, kw, gw, wall = env
    out = io.StringIO()
    assert fc.main(["counts", "--book", str(book)], out=out) == fc.REFUSED and not book.exists()
    rec.pass_({})
    assert run_step(rec, book, kw, gw, wall)[0] == 0 and book.exists()
    con = sqlite3.connect(str(book))
    con.execute("CREATE TABLE extra (x)")
    con.close()
    assert run_step(rec, book, kw, gw, wall)[0] == fc.OTHER_SHAPE
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"not a database at all, not at all" * 50)
    code = fc.main(["counts", "--book", str(junk)], out=io.StringIO())
    assert code == fc.UNREADABLE
    assert fc.main(["step", "--record", str(rec.path), "--book", str(rec.path)], kalshi_fetch=kw, gamma_fetch=gw,
                   wall=wall, sleep=wall.sleep, clock=wall, out=io.StringIO()) == fc.REFUSED


def test_a_copy_changes_only_to_close_and_never_after(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    g2 = Game("Hornets", "Lakers").install(kw, gw)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers"), g2.holding(wallet(0), "Lakers", value=10.0)]})
    run_step(rec, book, kw, gw, wall)
    assert len(rows(book, "SELECT * FROM copies")) == 1 and len(rows(book, "SELECT * FROM skips")) == 1
    con = sqlite3.connect(str(book), isolation_level=None)
    for sql in ("UPDATE copies SET ask=0.5", "UPDATE copies SET status='closed'", "DELETE FROM copies",
                "UPDATE skips SET reason='late'", "DELETE FROM considered", "UPDATE considered SET outcome='skip'"):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql)
    con.execute("UPDATE copies SET status='closed', close_kind='exit', close_ts=1, close_price=0.4")
    for sql in ("UPDATE copies SET close_price=0.9", "INSERT INTO copy_flags VALUES (1, 'exit_no_bid', 1, '')",
                "INSERT INTO copy_wallets VALUES (1, 'w', 1, 't', 'noted', 1)"):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO skips VALUES ('w', 1, 't', 'a reason not in the list', '', 1, 1)")
    con.close()


class Died(BaseException):
    """The stand-in for a step that dies at a write point."""


def _state(book):
    return (sorted(rows(book, "SELECT wallet_key, read_id, token_id, outcome FROM considered")),
            sorted(rows(book, "SELECT wallet_key, token_id, reason FROM skips")),
            sorted(rows(book, "SELECT wallet_key, token_id, kalshi_market, side, ask, status, close_kind, close_price "
                              "FROM copies")),
            sorted(rows(book, "SELECT wallet_key, token_id, role FROM copy_wallets")),
            sorted(rows(book, "SELECT family, token_id FROM near_book")),
            sorted(rows(book, "SELECT kind FROM copy_flags")))


def _died_world(tmp_path, tag):
    wall = Wall()
    rec = Record(tmp_path / ("rec-%s.sqlite" % tag), wall)
    kw, gw = KWorld(wall), GWorld()
    g1, g2, g3 = (Game(a, b).install(kw, gw) for a, b in (("Jazz", "Clippers"), ("Hornets", "Lakers"),
                                                          ("Celtics", "Knicks")))
    near = pos(wallet(0), "9002", "0x" + "1" * 64, "Will Bitcoin dip to $80,000 in November?", "Yes", "x")
    rec.pass_({wallet(0): [g1.holding(wallet(0), "Clippers"), g2.holding(wallet(0), "Hornets"), near],
               wallet(1): [g1.holding(wallet(1), "Clippers"), g3.holding(wallet(1), "Celtics", value=10.0)]})
    return rec, kw, gw, wall, (g1, g2, g3)


def _two_steps(rec, book, kw, gw, wall, games, hook=None):
    """A first step (opens copies), then a pass with an exit and a settlement, then a second step (closes)."""
    try:
        run_step(rec, book, kw, gw, wall, hook=hook if hook and hook.phase == 1 else None)
    except Died:
        pass
    run_step(rec, book, kw, gw, wall)
    per: dict = {}
    for _t, path, _q in kw.calls:
        if path.endswith("/orderbook"):
            per[path] = per.get(path, 0) + 1
    assert max(per.values(), default=0) <= 1, per             # the match reads: one a market and pass (R53)
    g1, g2, _g3 = games
    finalize(kw, g2, "Hornets", True, T0 + H)
    gw.markets[g2.cid] = g2.gamma(closed=True, winner="Hornets")
    wall.t = T0 + 2 * H
    rec.pass_({wallet(0): [g2.holding(wallet(0), "Hornets")], wallet(1): [g1.holding(wallet(1), "Clippers")]})
    try:
        run_step(rec, book, kw, gw, wall, hook=hook if hook and hook.phase == 2 else None)
    except Died:
        pass
    run_step(rec, book, kw, gw, wall)
    return _state(book)


class Hook:
    def __init__(self, at, phase):
        self.at, self.phase, self.n = at, phase, 0

    def __call__(self, sql):
        self.n += 1
        if self.n == self.at:
            raise Died()


def _orphans(book, gs):
    """The tokens of the markets whose match read was BEGUN and never kept (R53): their holdings are read_failed."""
    tables = {n for (n,) in rows(book, "SELECT name FROM sqlite_master WHERE type='table'")}
    if "match_begun" not in tables:
        return set()
    begun = set(rows(book, "SELECT kalshi_market, pass_key FROM match_begun")) - \
        set(rows(book, "SELECT kalshi_market, pass_key FROM match_reads"))
    tickers = {t for t, _p in begun}
    return {g.tok[team] for g in gs for team in (g.a, g.b) if g.markets[team]["ticker"] in tickers}


def _without(state, toks):
    return tuple([r for r in part if not (set(map(str, r)) & toks)] for part in state)


def test_a_step_that_dies_at_any_write_point_leaves_a_book_the_next_step_continues(tmp_path):
    rec, kw, gw, wall, gs = _died_world(tmp_path, "clean")
    want = _two_steps(rec, tmp_path / "clean.sqlite", kw, gw, wall, gs)
    assert want[2] and any(r[6] == "exit" for r in want[2]) and any(r[6] == "settled" for r in want[2])
    for phase in (1, 2):
        counter = Hook(0, phase)
        rec, kw, gw, wall, gs = _died_world(tmp_path, "count%d" % phase)
        _two_steps(rec, tmp_path / ("count%d.sqlite" % phase), kw, gw, wall, gs, hook=counter)
        assert counter.n > 10
        for at in range(1, counter.n + 1):
            rec, kw, gw, wall, gs = _died_world(tmp_path, "d%d-%d" % (phase, at))
            book = tmp_path / ("d%d-%d.sqlite" % (phase, at))
            got = _two_steps(rec, book, kw, gw, wall, gs, hook=Hook(at, phase))
            orph = _orphans(book, gs)
            assert _without(got, orph) == _without(want, orph), (phase, at)
            for _w, _r, tok, outcome in got[0]:
                if tok in orph:
                    assert outcome == "skip", (phase, at)
            assert all(reason == "read_failed" for _w, tok, reason in got[1] if tok in orph), (phase, at)


def test_a_match_read_is_never_sent_twice_whatever_write_fails(tmp_path):
    """R53: the book holds that a market and pass is BEING read before the request is sent; a later step that finds
    it begun and never kept takes the pass's first read as failed. Dying at every write point of the first step."""
    def world(tag):
        wall = Wall()
        rec = Record(tmp_path / ("r53-%s.sqlite" % tag), wall)
        kw, gw = KWorld(wall), GWorld()
        g = Game("Jazz", "Clippers").install(kw, gw)
        kw.seq[g.markets["Clippers"]["ticker"]] = [_book(0.02), _book(0.50)]       # 0.98 first; 0.50 a second time
        rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers")], wallet(1): [g.holding(wallet(1), "Clippers")]})
        return rec, kw, gw, wall, g
    counter = Hook(0, 1)
    rec, kw, gw, wall, g = world("count")
    run_step(rec, tmp_path / "r53-book-count.sqlite", kw, gw, wall, hook=counter)
    assert counter.n > 5
    for at in range(1, counter.n + 1):
        rec, kw, gw, wall, g = world("d%d" % at)
        book = tmp_path / ("r53-book-d%d.sqlite" % at)
        try:
            run_step(rec, book, kw, gw, wall, hook=Hook(at, 1))
        except Died:
            pass
        run_step(rec, book, kw, gw, wall)
        run_step(rec, book, kw, gw, wall)
        assert kw.kinds().count("book") <= 1, at                                   # never a second request
        assert rows(book, "SELECT count(*) FROM copies")[0][0] == 0, at             # never priced from a second
        assert set(skips(book).values()) <= {"ask_outside", "read_failed"}, at


def _capped_world(tmp_path, tag):
    """Copies open on four games; then a pass with two exits, two settlements and four new holdings, so that one
    step has new holdings, settlement reads and exit reads to make."""
    wall = Wall()
    rec = Record(tmp_path / ("cap-%s.sqlite" % tag), wall)
    kw, gw = KWorld(wall), GWorld()
    gs = games(kw, gw, 8)
    rec.pass_({wallet(0): [g.holding(wallet(0), g.a) for g in gs[:4]], wallet(1): []})
    book = tmp_path / ("cap-%s-book.sqlite" % tag)
    assert run_step(rec, book, kw, gw, wall)[0] == 0
    for g in gs[2:4]:
        finalize(kw, g, g.a, True, T0 + H)
    kw.events["KXNBAGAME"].remove(gs[3].event)               # one shown final in the list, one gone from it
    wall.t = T0 + 2 * H
    rec.pass_({wallet(0): [gs[2].holding(wallet(0), gs[2].a), gs[3].holding(wallet(0), gs[3].a)],
               wallet(1): [g.holding(wallet(1), g.a) for g in gs[4:8]]})
    return rec, book, kw, gw, wall


def test_a_cap_at_every_request_count_ends_cleanly_and_later_steps_finish_the_same_book(tmp_path):
    rec, book, kw, gw, wall = _capped_world(tmp_path, "free")
    n = len(kw.calls)
    code, out = run_step(rec, book, kw, gw, wall)
    assert code == 0, out
    total = len(kw.calls) - n
    assert sorted(kw.kinds(n)) == sorted(["list"] + ["book"] * 4 + ["market"] * 2 + ["book"] * 2)
    want = _state(book)
    assert sorted((r[5], r[6] or "") for r in want[2]) == [("closed", "exit")] * 2 + [("closed", "settled")] * 2 + \
        [("open", "")] * 4
    assert total > fc.MIN_CAP + 1
    for cap in range(fc.MIN_CAP, total):
        rec, book, kw, gw, wall = _capped_world(tmp_path, "c%d" % cap)
        n = len(kw.calls)
        code, out = run_step(rec, book, kw, gw, wall, cap=cap)
        assert code == fc.CAPPED and "STOPPED" in out and "left" in out, (cap, out)
        assert len(kw.calls) - n == cap
        assert rows(book, "SELECT count(*) FROM steps WHERE status='running'")[0][0] == 0
        for _ in range(6):
            code, out = run_step(rec, book, kw, gw, wall)
            if code == 0:
                break
        assert code == 0 and _state(book) == want, cap


def test_a_cap_reached_in_the_settlement_checks_is_a_capped_step(env):
    rec, book, kw, gw, wall = env
    gs = games(kw, gw, 7)
    rec.pass_({wallet(0): [g.holding(wallet(0), g.a) for g in gs]})
    assert run_step(rec, book, kw, gw, wall)[0] == 0
    kw.events["KXNBAGAME"] = []                               # every copy out of the list: one market read each
    code, out = run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP)     # the list, five market reads; the cap
    assert code == fc.CAPPED and "in the settlement checks" in out and "2 settlement checks" in out, out
    assert rows(book, "SELECT status FROM steps ORDER BY step_id DESC LIMIT 1") == [("capped",)]
    code, out = run_step(rec, book, kw, gw, wall, cap=8)      # the list and the seven market reads
    assert code == 0, out


def _active_events(series):
    evs = json.loads(json.dumps(_events(series)))
    for e in evs:
        for m in e["markets"]:
            m.update(status="active", close_time="2099-01-01T00:00:00Z")
    return evs


def _three_series_world(tmp_path, tag):
    """Open copies in all three listed SAME series (the real saved spread and EPL answers, made open), then a pass
    with two exits, a settlement, a market gone from its list and two new holdings."""
    wall = Wall()
    rec = Record(tmp_path / ("s3-%s.sqlite" % tag), wall)
    kw, gw = KWorld(wall), GWorld()
    gs = games(kw, gw, 6)
    for series in ("KXNBASPREAD", "KXPREMIERLEAGUE"):
        kw.events[series] = _active_events(series)
        for e in kw.events[series]:
            for m in e["markets"]:
                kw.books[m["ticker"]] = _book(0.45)
    gw.markets[SPREAD] = dict(_pm(SPREAD + ".closed.json")[0], closed=False)
    gw.markets[CITY] = dict(_pm(CITY + ".json")[0], closed=False)
    spread = pos(wallet(0), "5501", SPREAD, "Spread: Lakers (-7.5)", "Lakers", "nba-cha-lal-2026-11-17")
    city = pos(wallet(0), "5502", CITY, "Will Manchester City win the 2026-27 English Premier League (EPL) "
               "Championship?", "Yes", "epl-2027-champion-20260701200428749")
    rec.pass_({wallet(0): [g.holding(wallet(0), g.a) for g in gs[:4]] + [spread, city], wallet(1): []})
    book = tmp_path / ("s3-%s-book.sqlite" % tag)
    assert run_step(rec, book, kw, gw, wall)[0] == 0
    assert sorted(r[0] for r in rows(book, "SELECT series FROM copies")) == \
        ["KXNBAGAME"] * 4 + ["KXNBASPREAD", "KXPREMIERLEAGUE"]
    finalize(kw, gs[2], gs[2].a, True, T0 + H)
    kw.events["KXNBAGAME"].remove(gs[3].event)
    wall.t = T0 + 2 * H
    rec.pass_({wallet(0): [gs[2].holding(wallet(0), gs[2].a), gs[3].holding(wallet(0), gs[3].a), spread, city],
               wallet(1): [g.holding(wallet(1), g.a) for g in gs[4:6]]})
    return rec, book, kw, gw, wall


def test_the_smallest_cap_lets_a_step_do_one_unit_after_its_lists(capsys):
    lists = len({s for s, e in FMAP["series"].items() if e["class"] == "SAME"} & FMAP["_listed"])
    assert lists == 3 and fc.MIN_CAP == lists + 3          # the lists, then the largest unit: market (2) + book (1)
    with pytest.raises(SystemExit):
        fc.main(["step", "--record", "r", "--book", "b", "--kalshi-cap", str(fc.MIN_CAP - 1)])
    assert "at least %d" % fc.MIN_CAP in capsys.readouterr().err


@pytest.mark.parametrize("extra", [0, 1, 2])
def test_capped_steps_reach_the_uncapped_book_within_the_bound(tmp_path, extra):
    rec, book, kw, gw, wall = _three_series_world(tmp_path, "free")
    holdings = 2
    open_copies = rows(book, "SELECT count(*) FROM copies WHERE status='open'")[0][0]
    assert run_step(rec, book, kw, gw, wall)[0] == 0
    want = _state(book)
    bound = holdings + open_copies + 1                      # the doc's bound: H + N + 1 steps
    rec, book, kw, gw, wall = _three_series_world(tmp_path, "c%d" % extra)
    reached = None
    for i in range(1, bound + 1):
        code, out = run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP + extra)
        assert "no progress" not in out, out
        if _state(book) == want:
            reached = i
            break
    assert reached is not None and reached <= bound


def _passes(rec, wall, passes):
    """Pass 1 on the game's day; passes 2 to 4 after midnight UTC, the first read of the new day taking each wallet's
    snapshot. Each item: (holdings, wallets whose read fails)."""
    for i, (held, fail) in enumerate(passes):
        wall.t = T0 + (0, 4.5, 5.0, 5.5)[i] * H
        rec.pass_(held, fail=fail)
    wall.t += 60


def test_the_leaders_disagree_at_that_pass_even_when_the_other_left_since(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    a, b = wallet(0), wallet(1)
    _passes(rec, wall, [({a: [], b: []}, ()), ({b: []}, (a,)),                # a's first read of day 2 fails
                        ({a: [g.holding(a, "Clippers")], b: [g.holding(b, "Jazz")]}, ()),   # a's snapshot of day 2
                        ({a: [g.holding(a, "Clippers")], b: []}, ())])                    # b has left since
    run_step(rec, book, kw, gw, wall)
    assert skips(book).get((a, g.tok["Clippers"])) == "leaders_disagree"
    assert rows(book, "SELECT count(*) FROM copies")[0][0] == 0


def test_a_wallet_that_opened_the_opposite_only_after_that_pass_is_no_disagreement_at_it(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    a, b = wallet(0), wallet(1)
    _passes(rec, wall, [({a: [], b: []}, ()), ({b: []}, (a,)),
                        ({a: [g.holding(a, "Clippers")], b: []}, ()),
                        ({a: [g.holding(a, "Clippers")], b: [g.holding(b, "Jazz")]}, ())])  # b opens it after
    run_step(rec, book, kw, gw, wall)
    assert rows(book, "SELECT kalshi_market FROM copies") == [(g.markets["Clippers"]["ticker"],)]
    assert skips(book)[(b, g.tok["Jazz"])] == "leaders_disagree"            # at ITS pass, a held Clippers


def test_where_the_record_cannot_say_a_wallet_holds_what_it_held_at_its_last_good_read(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    a, b = wallet(0), wallet(1)
    _passes(rec, wall, [({a: [], b: [g.holding(b, "Jazz")]}, ()), ({b: [g.holding(b, "Jazz")]}, (a,)),
                        ({a: [g.holding(a, "Clippers")]}, (b,)),                 # b's read fails at a's pass
                        ({a: [g.holding(a, "Clippers")], b: []}, ())])
    run_step(rec, book, kw, gw, wall)
    assert skips(book).get((a, g.tok["Clippers"])) == "leaders_disagree"


def test_a_kept_match_read_judges_a_later_holding_by_its_own_stamp(env):
    rec, book, kw, gw, wall = env
    g0, *more = games(kw, gw, 7)
    t0 = g0.markets[g0.a]["ticker"]
    kw.books[t0] = _book(0.02)                               # ask 0.98 at the kept read
    rec.pass_({wallet(0): [g0.holding(wallet(0), g0.a)], wallet(1): [g.holding(wallet(1), g.a) for g in more],
               wallet(2): [g0.holding(wallet(2), g0.a)]})
    assert run_step(rec, book, kw, gw, wall, cap=fc.MIN_CAP)[0] == fc.CAPPED    # wallet 2 not reached
    assert (wallet(2), g0.tok[g0.a]) not in skips(book)
    g0.markets[g0.a]["close_time"] = iso(T0 + 3 * H)         # closed since, but open at the kept read's stamp
    wall.t = T0 + 7 * H                                       # the step is late; the kept read is not
    n = len(kw.calls)
    run_step(rec, book, kw, gw, wall)
    assert skips(book)[(wallet(2), g0.tok[g0.a])] == "ask_outside"
    assert "book" not in kw.kinds(n)


def test_the_append_only_tables_refuse_insert_or_replace(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw)
    near = pos(wallet(0), "9002", "0x" + "1" * 64, "Will Bitcoin dip to $80,000 in November?", "Yes", "x")
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers"), near], wallet(1): [g.holding(wallet(1), "Clippers")],
               wallet(2): [Game("Hornets", "Lakers").holding(wallet(2), "Hornets", value=10.0)]})
    run_step(rec, book, kw, gw, wall)
    kw.books[g.markets["Clippers"]["ticker"]]["yes_dollars"] = []
    wall.t += H
    rec.pass_({wallet(0): [near], wallet(1): [g.holding(wallet(1), "Clippers")]})
    run_step(rec, book, kw, gw, wall)                         # an exit with no bid: a flag
    con = sqlite3.connect(str(book), isolation_level=None)
    tables = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    checked = 0
    for t in ("considered", "skips", "copies", "copy_wallets", "copy_flags", "match_reads", "match_begun",
              "near_book", "texts", "pm_descriptions"):
        if t not in tables:
            continue
        info = list(con.execute("PRAGMA table_info(%s)" % t))
        cols = [r[1] for r in info]
        before = con.execute("SELECT rowid, * FROM %s ORDER BY rowid" % t).fetchall()
        assert before, t
        row = list(before[0])
        free = [i for i, r in enumerate(info) if not r[5]]                    # not part of the primary key
        num = [i for i in free if isinstance(row[i + 1], (int, float))]
        if num:
            row[num[0] + 1] += 1
        else:
            row[next(i for i in free if cols[i] == "body") + 1] = "rewritten by hand"
        sql = "INSERT OR REPLACE INTO %s (rowid, %s) VALUES (%s)" % (t, ", ".join(cols), ", ".join("?" * len(row)))
        try:
            con.execute(sql, row)
        except sqlite3.IntegrityError:
            pass
        assert con.execute("SELECT rowid, * FROM %s ORDER BY rowid" % t).fetchall() == before, t
        checked += 1
    con.close()
    assert checked >= 9


# ------------------------------------------------------------------ 4. counts, and no result anywhere
def test_counts_prints_counts_only(env):
    rec, book, kw, gw, wall = env
    g = Game("Jazz", "Clippers").install(kw, gw, no_bid=0.3333)
    rec.pass_({wallet(0): [g.holding(wallet(0), "Clippers"),
                           pos(wallet(0), "9002", "0x" + "1" * 64, "Will Bitcoin dip to $80,000 in November?",
                               "Yes", "x")]})
    run_step(rec, book, kw, gw, wall)
    wall.t += 3 * 86400
    out = io.StringIO()
    assert fc.main(["counts", "--book", str(book)], wall=wall, out=out) == 0
    text = out.getvalue()
    assert "copies opened 1: open 1" in text and "NEAR book 1" in text and "days since the first copy: 3.0" in text
    assert "0.6667" not in text and "0.67" not in text and "0.3333" not in text


FORBIDDEN_SQL = re.compile(r"\b(?:sum|avg|total)\s*\(", re.I)
FORBIDDEN_WORDS = re.compile(r"\b(?:profit|pnl|p_value|resample|statistics|mean)\b", re.I)
PRICE_NAMES = re.compile(r"(?:ask|bid|price|value|paid)", re.I)


def test_the_module_computes_no_result():
    """What this honestly checks: no SQL aggregate over any column, no import of a statistics tool, no name for a
    result, and no subtraction or addition between two price-like names (a constant less a price, 1 - bid, is how a
    side's ask is read and is allowed). What it cannot catch: a result computed under an innocent name, through an
    index or a dict, in another module that reads this book, or by a reader with a calculator."""
    src = (ROOT / "valedia_trader" / "research_loop" / "forward_copy.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not FORBIDDEN_SQL.search(node.value), node.value[:80]
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any(n.split(".")[0] in ("statistics", "numpy", "pandas") for n in names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("sum", "mean"), ast.unparse(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            sides = [ast.unparse(x) for x in (node.left, node.right)]
            assert not all(PRICE_NAMES.search(s) and not re.fullmatch(r"[\d.]+", s) for s in sides), sides
        if isinstance(node, ast.Name):
            assert not FORBIDDEN_WORDS.search(node.id), node.id
        if isinstance(node, ast.FunctionDef):
            assert not FORBIDDEN_WORDS.search(node.name), node.name


def test_the_no_result_check_bites():
    for bad in ("x = sum(close_price for c in cs)", "r = close_price - ask", "q = 'SELECT avg(ask) FROM copies'",
                "import statistics", "profit = 1"):
        tree = ast.parse(bad)
        hit = False
        for node in ast.walk(tree):
            hit |= isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(FORBIDDEN_SQL.search(
                node.value))
            hit |= isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sum"
            hit |= isinstance(node, ast.Import) and node.names[0].name == "statistics"
            hit |= isinstance(node, ast.Name) and bool(FORBIDDEN_WORDS.search(node.id))
            if isinstance(node, ast.BinOp):
                sides = [ast.unparse(x) for x in (node.left, node.right)]
                hit |= all(PRICE_NAMES.search(s) for s in sides)
        assert hit, bad


# ------------------------------------------------------------------ 5. properties over seeded worlds
CAT_TEAMS = ("Jazz", "Clippers", "Hornets", "Lakers", "Celtics", "Knicks", "Heat", "Magic", "Suns", "Spurs")


def _world(seed, tmp_path, monkeypatch):
    rnd = random.Random(seed)
    wall = Wall()
    rec = Record(tmp_path / ("rec%d.sqlite" % seed), wall)
    kw, gw = KWorld(wall), GWorld()
    games = [Game(CAT_TEAMS[2 * i], CAT_TEAMS[2 * i + 1], day=date(2026, 11, 17) + timedelta(days=rnd.randrange(2)))
             for i in range(5)]
    truth_k = {}
    for g in games:
        if rnd.random() < 0.85:
            g.install(kw, gw, no_bid=rnd.choice([0.02, 0.3, 0.5, 0.7, 0.97]), yes_bid=rnd.choice([0.2, 0.45]))
            if rnd.random() < 0.15:
                g.markets[g.a]["status"] = "closed"
            if rnd.random() < 0.1:
                kw.events["KXNBAGAME"].append(g.event)                       # two answers: no unique market
            if rnd.random() < 0.15:
                gw.markets[g.cid] = dict(g.gamma(), description=g.pm_text.replace("overtime", "extra time"))
        else:
            gw.markets[g.cid] = g.gamma()
        truth_k[g.cid] = g
    extras = [pos(None, "88%02d" % i, "0x" + ("%02d" % i) * 32, t, o, s) for i, (t, o, s) in enumerate((
        ("Will Arsenal win the 2026-27 English Premier League (EPL) Championship?", "Yes",
         "epl-2027-champion-20260701200428749"),
        ("Will Bitcoin dip to $80,000 in November?", "Yes", "x"), ("Spread: Lakers (-7.5)", "Hornets", games[1].slug),
        ("Will Apple be the largest company in the world by market cap on December 31?", "No", "x")))]
    holders = [wallet(i) for i in range(4)]
    state = {w: {} for w in holders}
    truth = []
    t_pass = T0
    for p in range(3):
        for w in holders:
            for tok in list(state[w]):
                if rnd.random() < 0.3:
                    del state[w][tok]
            for g in games:
                for team in (g.a, g.b):
                    if rnd.random() < 0.2:
                        state[w][g.tok[team]] = g.holding(w, team, rnd.choice([500.0, 1000.0, 4000.0]),
                                                          rnd.choice([0.05, 0.1, 0.5, 0.9, 0.95]))
            for e in extras:
                if rnd.random() < 0.3:
                    state[w][e["token_id"]] = dict(e, proxy_wallet=w, current_value=rnd.choice([500.0, 3000.0]),
                                                   current_price=rnd.choice([0.05, 0.5]))
        wall.t = max(wall.t + 60, t_pass)
        rec.pass_({w: list(state[w].values()) for w in holders})
        truth.append({w: dict(state[w]) for w in holders})
        t_pass = wall.t + rnd.choice([3 * H, 8 * H, 26 * H])
        yield rec, kw, gw, wall, rnd, truth, truth_k, games, p


def _check(book, rec, kw, gw, truth, truth_k):
    """An independent check of every copy against the inputs (the world's truth), and of the book's bookkeeping."""
    con = sqlite3.connect(rec.path.resolve().as_uri() + "?mode=ro", uri=True)
    reads = {r[0]: r[1:] for r in con.execute("SELECT read_id, wallet_key, pass_id, read_ts FROM wallet_reads")}
    con.close()
    copies = rows(book, "SELECT copy_id, wallet_key, read_id, token_id, kalshi_market, side, match_ts, pass_read_ts, "
                        "ask, condition_id, status FROM copies ORDER BY copy_id")
    for cid_, w, read_id, tok, ticker, side, match_ts, pass_ts, ask, cid, status in copies:
        _w, pass_id, read_ts = reads[read_id]
        p = pass_id - 1
        h = truth[p][w][tok]
        assert h["current_value"] >= 1000 and 0.10 <= h["current_price"] <= 0.90
        g = truth_k[cid]
        team = h["outcome"]
        m = g.markets[team]
        assert (ticker, side) == (m["ticker"], "yes")
        listed = [e for e in kw.events.get("KXNBAGAME", []) if e["event_ticker"] == g.event_ticker]
        assert len(listed) == 1 and m["status"] == "active" and fc.iso_ts(m["close_time"]) > match_ts
        assert gw.markets[cid]["description"] == g.pm_text                  # the text did not depart
        other = g.tok[g.b if team == g.a else g.a]
        assert not any(other in truth[p][v] for v in truth[p] if v != w)
        assert pass_ts == read_ts and 0 <= match_ts - read_ts <= 6 * H
        no = sorted(float(x[0]) for x in kw.books[m["ticker"]]["no_dollars"])[-1]
        assert ask == round(1 - no, 6) and 0.05 <= ask <= 0.95
        assert p == 0 or tok not in truth[p - 1][w]                          # baseline, or seen to open
        assert rows(book, "SELECT stamp, ok FROM match_reads WHERE kalshi_market=? AND pass_key=?",
                    (ticker, pass_id)) == [(match_ts, 1)]                    # THE one match read of that pass
    considered = rows(book, "SELECT wallet_key, read_id, token_id, outcome FROM considered")
    keys = [(w, r, t) for w, r, t, _o in considered]
    assert len(keys) == len(set(keys))
    copy_keys = {(c[1], c[2], c[3]) for c in copies}
    skip_rows = rows(book, "SELECT wallet_key, read_id, token_id, reason FROM skips")
    skip_keys = {(w, r, t) for w, r, t, _x in skip_rows}
    assert len(skip_keys) == len(skip_rows) and not (copy_keys & skip_keys)
    assert {(w, r, t) for w, r, t, o in considered if o == "copy"} == copy_keys
    assert {(w, r, t) for w, r, t, o in considered if o == "skip"} == skip_keys
    assert all(x in fc.SKIP_REASONS for *_k, x in skip_rows)
    when = {(w, r, t): ts for w, r, t, ts in rows(book, "SELECT wallet_key, read_id, token_id, considered_ts FROM "
                                                        "considered")}
    for w, read_id, tok, reason in skip_rows:                               # every skip's one reason is true
        _w, pass_id, read_ts = reads[read_id]
        p = pass_id - 1
        h = truth[p][w][tok]
        big = h["current_value"] >= 1000
        if reason == "value_under_1000":
            assert not big
            continue
        assert big
        if reason == "price_outside":
            assert not 0.10 <= h["current_price"] <= 0.90
            continue
        assert 0.10 <= h["current_price"] <= 0.90
        g = truth_k.get(h["condition_id"])
        if g is None or h["title"].startswith("Spread"):
            listed_series = h["title"].startswith(("Spread", "Will Arsenal"))       # listed: their lists are empty
            if listed_series:
                assert reason == "no_unique_market" or (reason == "late" and when[(w, read_id, tok)] - read_ts >
                                                        6 * H), (reason, h["title"])
            else:
                assert reason == "no_same", (reason, h["title"])
            continue
        team = h["outcome"]
        m = g.markets[team]
        other = g.tok[g.b if team == g.a else g.a]
        n_events = sum(1 for e in kw.events.get("KXNBAGAME", []) if e["event_ticker"] == g.event_ticker)
        no = (sorted(float(x[0]) for x in kw.books[m["ticker"]]["no_dollars"])[-1]
              if m["ticker"] in kw.books else None)
        truths = {
            "leaders_disagree": any(other in truth[q][v] for q in range(p, len(truth)) for v in truth[q] if v != w),
            "late": when[(w, read_id, tok)] - read_ts > 6 * H,
            "no_unique_market": n_events != 1,
            "market_not_open": m["status"] != "active",
            "text_departs": gw.markets[g.cid]["description"] != g.pm_text,
            "already_open": any(c[4] == m["ticker"] for c in copies),
            "ask_outside": no is not None and not 0.05 <= round(1 - no, 6) <= 0.95}
        assert truths.get(reason), (reason, h["title"], team)
    assert rows(book, "SELECT max(n) FROM (SELECT count(*) AS n FROM copies WHERE status='open' GROUP BY "
                      "kalshi_market, side)")[0][0] in (None, 1)
    return {(reads[r][0], reads[r][1] - 1, t) for w, r, t in keys}


@pytest.mark.parametrize("seed", range(8))
def test_properties_over_seeded_worlds(seed, tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def spy(db, *a, **k):
        opened.append(str(db))
        return real_connect(db, *a, **k)
    monkeypatch.setattr(fc.sqlite3, "connect", spy)
    book = tmp_path / ("book%d.sqlite" % seed)
    for rec, kw, gw, wall, rnd, truth, truth_k, games, p in _world(seed, tmp_path, monkeypatch):
        wall.t += rnd.choice([60.0, H, 5 * H, 7 * H])
        for _ in range(60):
            before, n = rec.digest(), len(kw.calls)
            cap = rnd.choice([fc.MIN_CAP, fc.MIN_CAP + 1, fc.MIN_CAP + 3, 60])
            opened.clear()
            code, out = run_step(rec, book, kw, gw, wall, cap=cap)
            assert rec.digest() == before                                     # nothing written to the record
            record = str(rec.path.resolve())
            assert any(record in db for db in opened)
            assert all("mode=ro" in db for db in opened if record in db)      # opened read only, every time
            assert len(kw.calls) - n <= cap
            assert code in (0, fc.CAPPED), out
            _check(book, rec, kw, gw, truth, truth_k)
            if code == 0:
                break
        else:
            raise AssertionError("never finished")
    seen = _check(book, rec, kw, gw, truth, truth_k)
    want = set()
    for p, state in enumerate(truth):
        for w, held in state.items():
            for tok in held:
                if p == 0 or tok not in truth[p - 1][w]:
                    want.add((w, p, tok))
    assert seen == want                                                      # every holding once, none twice
    n, g = len(kw.calls), len(gw.calls)
    run_step(rec, book, kw, gw, wall)                                         # the record unchanged
    open_series = rows(book, "SELECT count(DISTINCT series) FROM copies WHERE status='open'")[0][0]
    assert "book" not in kw.kinds(n) and kw.kinds(n).count("list") == open_series     # only the settlement lists
    books = [(p, pk) for p, pk in rows(book, "SELECT kalshi_market, pass_key FROM match_reads")]
    assert len(books) == len(set(books))
    match_calls = sum(1 for _t, path, _q in kw.calls if path.endswith("/orderbook"))
    exits = rows(book, "SELECT count(*) FROM copies WHERE close_kind='exit'")[0][0] + \
        rows(book, "SELECT count(*) FROM copy_flags WHERE kind='exit_no_bid'")[0][0]
    assert match_calls == len(books) + exits                 # one book read a market and pass, one an exit close


# ------------------------------------------------------------------ the fixture record (real holdings)
def test_the_real_fixture_record_through_one_step(tmp_path):
    wall = Wall()
    rec = Record(tmp_path / "real.sqlite", wall)
    data = json.loads((FIX / "record_holdings.json").read_text())["rows"]
    wallets = sorted({r["wallet_key"] for r in data})
    alias = {w: wallet(i) for i, w in enumerate(wallets)}
    held = {}
    for r in data:
        w = alias[r["wallet_key"]]
        held.setdefault(w, []).append(pos(w, r["token_id"], r["condition_id"], r["title"], r["outcome"],
                                          r["event_slug"], r["current_value"], r["current_price"],
                                          r["opposite_token_id"]))
    rec.pass_(held)
    kw, gw = KWorld(wall), GWorld()
    for e in json.loads(json.dumps(_events("KXNBAGAME"))):
        for m in e["markets"]:
            m.update(status="active", close_time="2026-11-21T00:00:00Z")
            kw.books[m["ticker"]] = {"yes_dollars": book_levels([(0.5, 10.0)]), "no_dollars": book_levels([(0.4, 9.0)])}
        kw.events.setdefault("KXNBAGAME", []).append(e)
    gw.markets[JAZZ] = dict(_pm(JAZZ + ".closed.json")[0], closed=False)
    hornets = next(r for r in data if r["title"] == "Hornets vs. Lakers")
    gw.markets[hornets["condition_id"]] = dict(Game("Hornets", "Lakers").gamma(), conditionId=hornets["condition_id"])
    code, out = run_step(rec, tmp_path / "real-book.sqlite", kw, gw, wall)
    assert code == 0, out
    got = rows(tmp_path / "real-book.sqlite", "SELECT kalshi_market, side FROM copies ORDER BY kalshi_market")
    assert got == [("KXNBAGAME-26NOV17CHALAL-CHA", "yes"), ("KXNBAGAME-26NOV17UTALAC-LAC", "yes")]
    reasons = dict(rows(tmp_path / "real-book.sqlite", "SELECT reason, count(*) FROM skips GROUP BY reason"))
    assert reasons.get("name_not_in_table", 0) == 0 and reasons["not_listed"] >= 1
    assert {d for (d,) in rows(tmp_path / "real-book.sqlite", "SELECT detail FROM skips WHERE reason='not_listed'")} \
        == {"KXPRESNOMD"}                                    # in the rule map, listed but not tradable
    assert reasons["no_unique_market"] == 9                  # spreads and the EPL reach their (stand-in, empty) lists
    assert rows(tmp_path / "real-book.sqlite", "SELECT count(*) FROM near_book")[0][0] >= 40
