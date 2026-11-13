import json
import math
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from valedia_trader.research_loop import sports_lines as sl
from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh
from valedia_trader.venues import odds_api as oa

UTC = timezone.utc
KEYS = {"ODDS_API_KEY": "PAID-KEY-SECRET-0001", "ODDS_API_KEY_FREE": "FREE-KEY-SECRET-0002"}
PERIODS = {"dev": (date(2026, 1, 5), date(2026, 3, 1)), "test": (date(2026, 9, 20), date(2026, 11, 8))}


def T(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def ts(text):
    return int(T(text).timestamp())


# ---------------------------------------------------------------- fakes

class Clock:
    def __init__(self, now="2026-11-13T12:00:00Z"):
        self.now = T(now)

    def __call__(self):
        return self.now


def ev(eid, home, away, commence, books, sport="basketball_nba"):
    return {"id": eid, "sport_key": sport, "commence_time": commence, "home_team": home, "away_team": away,
            "bookmakers": [{"key": b, "last_update": commence, "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": dh}, {"name": away, "price": da}]}]} for b, (dh, da) in books.items()]}


def snap(stamp, events):
    return {"timestamp": stamp, "previous_timestamp": None, "next_timestamp": None, "data": events}


class OddsTransport:
    """The Odds API's historical odds, answered by (sport, date); records every call with its params."""
    def __init__(self, routes, last="10", remaining=None):
        self.routes, self.last, self.calls = routes, last, []
        self.remaining = remaining

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        sport = url.rstrip("/").split("/")[-2] if url.endswith("/odds") else None
        body = self.routes.get((sport, params.get("date")), snap(params.get("date"), []))
        headers = {"x-requests-last": self.last if body["data"] else "0", "x-requests-used": "1"}
        if self.remaining is not None:
            self.remaining -= int(headers["x-requests-last"])
            headers["x-requests-remaining"] = str(self.remaining)
        return 200, headers, body


def odds_client(transport):
    return oa.OddsApiClient(transport, key_reader=lambda name: KEYS[name], sleep=lambda s: None, max_retries=0)


class KalshiTransport:
    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        path = parts.path.split("/trade-api/v2", 1)[1]
        for p, answer in self.routes.items():
            if p == path:
                return json.dumps(answer).encode()
        raise kd.HttpError(404, url)


def kalshi_client(routes):
    t = KalshiTransport(routes)
    return kh.HistoryClient(fetch=t, sleep=lambda s: None, clock=lambda: 0.0), t


def kmarket(event, code, name, result, close="2026-01-11T03:00:00Z", expected="2026-01-11T03:30:00Z"):
    return {"ticker": event + "-" + code, "event_ticker": event, "title": name + " wins", "yes_sub_title": name,
            "result": result, "status": "finalized", "close_time": close, "settlement_ts": close,
            "expected_expiration_time": expected, "occurrence_datetime": expected, "last_price_dollars": "0.5000"}


def kcandle(end_ts, bid, ask):
    return {"end_period_ts": end_ts, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}, "price": {}, "volume": 1}


# ---------------------------------------------------------------- the waves (outcome 3)

def starts(*times):
    return [T(t) for t in times]


def test_wave_rule_one_start():
    w = sl.select_waves(starts("2026-01-11T00:30:00Z"))
    assert w == [(T("2026-01-11T00:00:00Z"), starts("2026-01-11T00:30:00Z"))]


def test_wave_rule_two_starts_inside_and_outside_ninety_minutes():
    assert len(sl.select_waves(starts("2026-01-11T00:00:00Z", "2026-01-11T01:30:00Z"))) == 1
    two = sl.select_waves(starts("2026-01-11T00:00:00Z", "2026-01-11T01:31:00Z"))
    assert [s for s, _ in two] == [T("2026-01-10T23:30:00Z"), T("2026-01-11T01:01:00Z")]


def test_wave_rule_five_starts():
    w = sl.select_waves(starts("2026-01-10T17:00:00Z", "2026-01-10T18:00:00Z", "2026-01-10T21:25:00Z",
                               "2026-01-11T00:15:00Z", "2026-01-11T01:30:00Z"))
    assert [(s, len(g)) for s, g in w] == [(T("2026-01-10T16:30:00Z"), 2), (T("2026-01-10T20:55:00Z"), 1),
                                          (T("2026-01-10T23:45:00Z"), 2)]


def test_wave_rule_twelve_starts_keeps_the_three_biggest_waves():
    s = starts("2026-01-10T17:00:00Z",                                                     # wave of 1
               "2026-01-10T19:00:00Z", "2026-01-10T19:30:00Z",                             # wave of 2
               "2026-01-10T21:00:00Z", "2026-01-10T21:10:00Z", "2026-01-10T22:00:00Z",     # wave of 3
               "2026-01-11T00:00:00Z", "2026-01-11T00:30:00Z", "2026-01-11T01:00:00Z",     # wave of 4
               "2026-01-11T01:30:00Z",
               "2026-01-11T03:00:00Z", "2026-01-11T03:30:00Z")                             # wave of 2
    w = sl.select_waves(s)
    assert [(at, len(g)) for at, g in w] == [(T("2026-01-10T18:30:00Z"), 2), (T("2026-01-10T20:30:00Z"), 3),
                                            (T("2026-01-10T23:30:00Z"), 4)]   # tie of 2: the earlier kept
    assert all(len(sl.all_waves(s)[i]) == n for i, n in enumerate([1, 2, 3, 4, 2]))


def test_wave_rule_across_midnight_utc_on_one_eastern_day():
    s = starts("2026-01-10T23:30:00Z", "2026-01-11T00:40:00Z")      # 6:30 pm and 7:40 pm Eastern, 10 January
    w = sl.select_waves(s)
    assert w == [(T("2026-01-10T23:00:00Z"), s)]
    assert sl.eastern_date("2026-01-11T00:40:00Z") == date(2026, 1, 10)
    assert sl.first_snapshot_time(date(2026, 1, 10)) == T("2026-01-10T16:00:00Z")


# ---------------------------------------------------------------- the credit ledger (outcomes 1 and 2)

def test_cap_on_the_command_line_refuses_the_call_that_would_pass_it(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport({("basketball_nba", d): snap(d, [ev("e", "A", "B", d, {"pinnacle": (2, 2)})])
                       for d in ("2026-01-05T16:00:00Z", "2026-01-06T16:00:00Z", "2026-01-07T16:00:00Z")})
    client = odds_client(t)
    for d in ("2026-01-05T16:00:00Z", "2026-01-06T16:00:00Z"):
        sl.paid_snapshot(client, ledger, "basketball_nba", d, cap=25)
    with pytest.raises(sl.CreditCapError, match="cap of 25"):
        sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-07T16:00:00Z", cap=25)
    assert len(t.calls) == 2 and ledger.spent() == 20


def test_monthly_cap_counts_the_ledger_and_the_last_remaining_header(tmp_path):
    clock = Clock("2026-11-13T12:00:00Z")
    day = {("basketball_nba", "2026-01-0%dT16:00:00Z" % i): snap("x", [ev("e", "A", "B", "x", {"pinnacle": (2, 2)})])
           for i in range(5, 10)}
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", monthly_cap=30, clock=clock)
    t = OddsTransport(day)
    client = odds_client(t)
    for i in (5, 6, 7):
        sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-0%dT16:00:00Z" % i, cap=1000)
    with pytest.raises(sl.CreditCapError, match="month"):                 # 30 used this month by the ledger
        sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-08T16:00:00Z", cap=1000)
    assert len(t.calls) == 3
    clock.now = T("2026-12-01T00:00:01Z")                                 # a new month: the ledger alone allows it
    t2 = OddsTransport(day, remaining=15)                                 # ... but the account says 5 are left after
    client2 = odds_client(t2)
    sl.paid_snapshot(client2, ledger, "basketball_nba", "2026-01-08T16:00:00Z", cap=1000)
    assert ledger.last_remaining() == 5
    with pytest.raises(sl.CreditCapError, match="remaining"):
        sl.paid_snapshot(client2, ledger, "basketball_nba", "2026-01-09T16:00:00Z", cap=1000)
    assert len(t2.calls) == 1


def test_usage_headers_are_stored_and_no_key_reaches_the_ledger(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport({("basketball_nba", "2026-01-05T16:00:00Z"):
                       snap("2026-01-05T15:55:00Z", [ev("e", "A", "B", "x", {"pinnacle": (2, 2)})])}, remaining=100)
    sl.paid_snapshot(odds_client(t), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    call = ledger.calls()[0]
    assert (call["remaining"], call["used"], call["last"], call["charged"]) == (90, 1, 10, 10)
    assert t.calls[0][1]["apiKey"] == KEYS["ODDS_API_KEY"]                # the paid key was sent ...
    raw = (tmp_path / "credits.sqlite").read_bytes()
    assert b"PAID-KEY" not in raw and b"FREE-KEY" not in raw              # ... and never stored
    assert "apiKey" not in call["params"]


def test_a_cost_different_from_the_rule_is_an_event_and_used_from_then_on(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    routes = {("basketball_nba", "2026-01-0%dT16:00:00Z" % i): snap("x", [ev("e", "A", "B", "x", {"pinnacle": (2, 2)})])
              for i in (5, 6, 7)}
    t = OddsTransport(routes, last="20")
    client = odds_client(t)
    assert ledger.expected_cost(sl.SNAPSHOT_RULE, 10) == 10
    sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=45)
    events = ledger.events()
    assert len(events) == 1 and events[0]["kind"] == "cost_differs"
    assert json.loads(events[0]["detail"]) == {"rule": sl.SNAPSHOT_RULE, "expected": 10, "charged": 20}
    assert ledger.expected_cost(sl.SNAPSHOT_RULE, 10) == 20
    sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-06T16:00:00Z", cap=45)
    with pytest.raises(sl.CreditCapError):                                # 40 spent + 20 > 45, though the rule says 10
        sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-07T16:00:00Z", cap=45)
    assert len(t.calls) == 2 and len(ledger.events()) == 1


def test_an_empty_snapshot_that_costs_nothing_is_not_a_cost_difference(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport({})
    sl.paid_snapshot(odds_client(t), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    assert ledger.events() == [] and ledger.spent() == 0 and ledger.expected_cost(sl.SNAPSHOT_RULE, 10) == 10


def test_a_failed_call_without_headers_is_charged_at_the_expected_cost(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())

    def broken(url, params, timeout):
        raise ConnectionError("refused " + params["apiKey"])

    with pytest.raises(oa.OddsApiError) as err:
        sl.paid_snapshot(odds_client(broken), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    assert "PAID-KEY" not in str(err.value)
    assert ledger.spent() == 10 and ledger.calls()[0]["ok"] == 0


# ---------------------------------------------------------------- a small world: stores built by the commands

NBA_BOOKS_1 = {"pinnacle": (1.80, 2.10), "draftkings": (1.75, 2.15), "fanduel": (1.85, 2.00)}
NBA_BOOKS_2 = {"pinnacle": (1.50, 2.80), "draftkings": (1.55, 2.60), "fanduel": (1.48, 2.90), "bovada": (1.01, 30.0)}
M1 = ts("2026-01-10T23:55:00Z")             # the reported time of the first wave's snapshot
M2 = ts("2026-01-11T02:25:00Z")             # ... of the second


def world_routes(alter_after=False):
    e1 = ev("o1", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z", NBA_BOOKS_1)
    e2 = ev("o2", "New York Knicks", "Los Angeles Lakers", "2026-01-11T03:00:00Z", NBA_BOOKS_2)
    e3 = ev("o3", "Utah Jazz", "Denver Nuggets", "2026-01-12T02:00:00Z", NBA_BOOKS_1)   # next Eastern day
    first = snap("2026-01-10T15:55:00Z", [e1, e2, e3])
    wave1 = snap("2026-01-10T23:55:00Z", [e1, e2])
    wave2 = snap("2026-01-11T02:25:00Z", [e2])
    if alter_after:                       # what the books said after the moment is not in the snapshots used
        wave2 = snap("2026-01-11T02:25:00Z", [e2])
    return {("basketball_nba", "2026-01-10T16:00:00Z"): first,
            ("basketball_nba", "2026-01-11T00:00:00Z"): wave1,
            ("basketball_nba", "2026-01-11T02:30:00Z"): wave2}


def kalshi_world(alter_after=False):
    a = "KXNBAGAME-26JAN10BOSMIA"
    b = "KXNBAGAME-26JAN10LALNYK"
    markets = [kmarket(a, "BOS", "Boston", "no"), kmarket(a, "MIA", "Miami", "yes"),
               kmarket(b, "LAL", "Los Angeles L", "no", close="2026-01-11T05:30:00Z"),
               kmarket(b, "NYK", "New York", "yes", close="2026-01-11T05:30:00Z")]
    if alter_after:
        markets = [dict(m, last_price_dollars="0.9900", volume_fp="999999") for m in markets]
    m1, m2 = M1 // 60 * 60, M2 // 60 * 60
    candles = {"%s-MIA" % a: [kcandle(m1 - 60, 0.30, 0.31), kcandle(m1, 0.50, 0.52), kcandle(m1 + 300, 0.60, 0.61)],
               "%s-BOS" % a: [kcandle(m1 - 60, 0.68, 0.70), kcandle(m1, 0.47, 0.49), kcandle(m1 + 300, 0.38, 0.40)],
               "%s-NYK" % b: [kcandle(m2, 0.58, 0.60)], "%s-LAL" % b: [kcandle(m2, 0.40, 0.42)]}
    if alter_after:
        candles = {k: [c if c["end_period_ts"] <= (M1 if "MIA" in k or "BOS" in k else M2)
                       else kcandle(c["end_period_ts"], 0.97, 0.99) for c in v] + [kcandle(M2 + 600, 0.01, 0.02)]
                   for k, v in candles.items()}
    return markets, candles


def build_world(root: Path, alter_after=False, resolver=None):
    """Kalshi markets and candles through the store; odds snapshots bought through the real code path."""
    store_dir = root / "store"
    markets, candles = kalshi_world(alter_after)
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    for m in markets:
        kstore.put_market(m, "historical", "KXNBAGAME")
    kstore.put_listing("KXNBAGAME", "historical", *PERIODS["dev"], len(markets))
    kstore.put_listing("KXNBAGAME", "live", *PERIODS["test"], 0)          # the test period is known (and reserved)
    for ticker, rows in candles.items():
        for c in rows:
            kstore.put_candle(ticker, c, "historical")
        kstore.put_window(ticker, 0, 2**40, "historical", len(rows))
    t = OddsTransport(world_routes(alter_after))
    out = sl.main(["--store", str(store_dir), "fetch-odds", "--period", "dev", "--cap", "9000",
                   "--leagues", "NBA"], odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
    assert out == 0
    sl.main(["--store", str(store_dir), "pair", "--period", "dev", "--leagues", "NBA", "--no-resolver"],
            clock=Clock(), periods=PERIODS)
    return store_dir, t


def test_snapshots_follow_the_rule_with_ten_named_books_and_are_never_bought_twice(tmp_path):
    store_dir, t = build_world(tmp_path)
    dates = [p["date"] for _, p in t.calls]
    assert dates == ["2026-01-10T16:00:00Z", "2026-01-11T00:00:00Z", "2026-01-11T02:30:00Z"]
    assert all(p["bookmakers"] == ",".join(sl.BOOKS) and p["markets"] == "h2h" for _, p in t.calls)
    assert len(sl.BOOKS) == 10 and sl.BOOKS[0] == "pinnacle"
    store = sl.LinesStore(store_dir / "lines.sqlite")
    snaps = store.snapshots("NBA")
    assert [s["timestamp"] for s in snaps] == ["2026-01-10T15:55:00Z", "2026-01-10T23:55:00Z", "2026-01-11T02:25:00Z"]
    assert sl.main(["--store", str(store_dir), "fetch-odds", "--period", "dev", "--cap", "9000", "--leagues", "NBA"],
                   odds_client=odds_client(t), clock=Clock(), periods=PERIODS) == 0
    assert len(t.calls) == 3                                                    # nothing bought again


def test_development_purchase_is_refused_until_the_test_period_is_listed(tmp_path):
    kstore = kh.HistoryStore(tmp_path / "kalshi.sqlite")
    kstore.put_listing("KXNBAGAME", "historical", *PERIODS["dev"], 0)
    t = OddsTransport({})
    with pytest.raises(sl.Refused, match="test period"):
        sl.main(["--store", str(tmp_path), "fetch-odds", "--period", "dev", "--cap", "9000", "--leagues", "NBA"],
                odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
    assert t.calls == []


def test_a_snapshot_in_the_future_is_not_bought(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "c.sqlite", clock=Clock("2026-01-10T20:00:00Z"))
    store = sl.LinesStore(tmp_path / "l.sqlite")
    t = OddsTransport({})
    got = sl.buy(odds_client(t), ledger, store, "NBA", T("2026-01-11T00:00:00Z"), "wave", date(2026, 1, 10), 9000,
                 Clock("2026-01-10T20:00:00Z"))
    assert got is None and t.calls == []


def test_plan_makes_no_paid_call_and_drops_development_days_from_the_end_only(tmp_path, capsys):
    store_dir = tmp_path / "store"
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    for d in ("26JAN05", "26JAN06", "26JAN07"):
        e = "KXNBAGAME-%sBOSMIA" % d
        kstore.put_market(kmarket(e, "BOS", "Boston", "no"), "historical", "KXNBAGAME")
        kstore.put_market(kmarket(e, "MIA", "Miami", "yes"), "historical", "KXNBAGAME")
    for d in ("26SEP20", "26SEP21"):
        e = "KXNHLGAME-%sBOSMTL" % d
        kstore.put_market(kmarket(e, "BOS", "Boston", "no"), "live", "KXNHLGAME")
        kstore.put_market(kmarket(e, "MTL", "Montreal", "yes"), "live", "KXNHLGAME")

    def no_call(*a, **k):
        raise AssertionError("plan made a call")

    rc = sl.main(["--store", str(store_dir), "plan", "--period", "all", "--cap", "160"],
                 odds_client=odds_client(no_call), clock=Clock(), periods=PERIODS)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    # no 16:00 snapshot stored yet: a league-day is planned at its upper bound, 1 + 3 snapshots = 40 credits
    assert out["test"]["cost"] == 80 and out["test"]["dropped_dates"] == []
    assert out["dev"]["dropped_dates"] == ["2026-01-07"] and out["dev"]["cost"] == 80
    assert out["budget"] == 160 and out["fits"] is True
    assert "dropped" in out["note"]
    assert "dev:NBA" in out["unlisted"] and "not listed" in out["note"]       # markets put by hand, never listed
    assert {s["kind"] for s in out["dev"]["snapshots"]} == {"first", "wave"}
    first = [s for s in out["dev"]["snapshots"] if s["kind"] == "first"]
    assert first[0] == {"league": "NBA", "day": "2026-01-05", "kind": "first", "at": "2026-01-05T16:00:00Z",
                        "known": True, "cost": 10}
    rc = sl.main(["--store", str(store_dir), "plan", "--period", "all", "--cap", "30"],
                 odds_client=odds_client(no_call), clock=Clock(), periods=PERIODS)
    out = json.loads(capsys.readouterr().out)
    assert out["fits"] is False and out["test"]["dropped_dates"] == []      # the test period is never cut


# ---------------------------------------------------------------- pairing (outcome 5)

def test_team_names_match_by_code():
    assert sl.names_match("Los Angeles L", "Los Angeles Lakers")
    assert not sl.names_match("Los Angeles L", "Los Angeles Clippers")
    assert sl.names_match("NY Rangers", "New York Rangers") and sl.names_match("NYI Islanders", "New York Islanders")
    assert sl.names_match("Kent St.", "Kent State Golden Flashes")
    assert not sl.names_match("Florida", "Florida State Seminoles") and sl.names_match("Florida", "Florida Gators")
    assert sl.names_match("Miami (FL)", "Miami Hurricanes") and not sl.names_match("Miami (FL)", "Miami (OH) RedHawks")
    assert not sl.names_match("Miami", "Miami (OH) RedHawks")
    assert sl.names_match("Texas A&M", "Texas A&M Aggies") and not sl.names_match("Texas", "Texas A&M Aggies")
    assert not sl.names_match("Michigan", "Central Michigan Chippewas")
    assert sl.names_match("St. John's", "St. John's Red Storm")


def kev(event, teams, day=None, expected="2026-01-11T03:30:00Z"):
    return {"event": event, "league": "NBA", "day": day or kh.event_date(event), "expected": expected,
            "teams": [{"code": c, "name": n, "ticker": event + "-" + c} for c, n in teams]}


def ogame(eid, home, away, commence):
    return {"id": eid, "league": "NBA", "home": home, "away": away, "commence": commence,
            "day": sl.eastern_date(commence)}


def test_pairing_by_league_teams_and_eastern_date_with_consecutive_days():
    k = [kev("KXNBAGAME-26JAN10BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")]),
         kev("KXNBAGAME-26JAN11BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")])]
    o = [ogame("x2", "Miami Heat", "Boston Celtics", "2026-01-12T00:30:00Z"),       # 11 January, Eastern
         ogame("x1", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z")]       # 10 January, Eastern
    pairs, left, log = sl.pair_games(k, o, resolver=None)
    got = {p["kalshi_event"]: p["odds_event_id"] for p in pairs}
    assert got == {"KXNBAGAME-26JAN10BOSMIA": "x1", "KXNBAGAME-26JAN11BOSMIA": "x2"} and left == {}
    p = pairs[0]
    assert p["home_ticker"].endswith("-MIA") and p["away_ticker"].endswith("-BOS") and p["source"] == "code"


def test_pairing_a_doubleheader_pairs_each_game_once_in_time_order():
    k = [kev("KXNBAGAME-26JAN10BOSMIAG2", [("BOS", "Boston"), ("MIA", "Miami")], day=date(2026, 1, 10),
             expected="2026-01-11T03:30:00Z"),
         kev("KXNBAGAME-26JAN10BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")], expected="2026-01-10T20:30:00Z")]
    o = [ogame("late", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z"),
         ogame("early", "Miami Heat", "Boston Celtics", "2026-01-10T17:30:00Z")]
    pairs, left, _ = sl.pair_games(k, o, resolver=None)
    got = {p["kalshi_event"]: p["odds_event_id"] for p in pairs}
    assert got == {"KXNBAGAME-26JAN10BOSMIA": "early", "KXNBAGAME-26JAN10BOSMIAG2": "late"}
    assert len({p["odds_event_id"] for p in pairs}) == 2 and left == {}


def test_a_resolver_that_proposes_a_wrong_game_is_refused_and_a_right_one_kept_with_its_source():
    k = [kev("KXNBAGAME-26JAN10GSWPHX", [("GSW", "Golden St. W"), ("PHX", "Phx Suns")])]
    o = [ogame("right", "Phoenix Suns", "Golden State Warriors", "2026-01-11T03:00:00Z"),
         ogame("wrongday", "Phoenix Suns", "Golden State Warriors", "2026-01-12T03:00:00Z"),
         ogame("wrongteam", "Phoenix Suns", "Sacramento Kings", "2026-01-11T03:00:00Z")]
    asked = []

    def wrong(kevent, candidates):
        asked.append(sorted(c["id"] for c in candidates))
        return {"odds_event_id": "wrongday", "teams": {"Golden St. W": "Golden State Warriors",
                                                        "Phx Suns": "Phoenix Suns"}}

    pairs, left, log = sl.pair_games(k, o, resolver=wrong)
    assert pairs == [] and left == {"resolver_refused": 1} and log[0]["accepted"] is False
    assert "date" in log[0]["reason"] and asked == [["right", "wrongday", "wrongteam"]]

    def wrong_team(kevent, candidates):
        return {"odds_event_id": "wrongteam", "teams": {"Golden St. W": "Sacramento Kings",
                                                         "Phx Suns": "Phoenix Suns"}}

    pairs, left, log = sl.pair_games(k, o, resolver=wrong_team)
    assert pairs == [] and left == {"resolver_refused": 1} and "team" in log[0]["reason"]

    def right(kevent, candidates):
        return {"odds_event_id": "right", "teams": {"Golden St. W": "Golden State Warriors",
                                                     "Phx Suns": "Phoenix Suns"}}

    right.source = "fake-model"
    pairs, left, log = sl.pair_games(k, o, resolver=right)
    assert [p["odds_event_id"] for p in pairs] == ["right"] and pairs[0]["source"] == "resolver:fake-model"
    assert pairs[0]["home_ticker"].endswith("-PHX") and log[0]["accepted"] is True

    pairs, left, log = sl.pair_games(k, o, resolver=right, taken_odds={"right"})          # never paired twice
    assert pairs == [] and left == {"resolver_refused": 1}


def test_unmatched_games_are_left_out_and_counted_by_reason():
    k = [kev("KXNBAGAME-26JAN10BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")]),
         kev("KXNBAGAME-26JAN10LALBKN", [("LAL", "Los Angeles"), ("BKN", "Brooklyn")])]
    o = [ogame("a", "Brooklyn Nets", "Los Angeles Lakers", "2026-01-11T00:30:00Z"),
         ogame("b", "Brooklyn Nets", "Los Angeles Clippers", "2026-01-11T00:30:00Z")]
    pairs, left, _ = sl.pair_games(k, o, resolver=None)
    assert pairs == [] and left == {"no_match": 1, "ambiguous": 1}
    pairs, left, _ = sl.pair_games(k, o, resolver=lambda ke, c: (_ for _ in ()).throw(RuntimeError("down")))
    assert left == {"resolver_error": 2}


def test_accepted_resolver_proposals_are_stored_and_listed(tmp_path, capsys):
    store_dir, _ = build_world(tmp_path)
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    e = "KXNBAGAME-26JAN11DENUTA"
    kstore.put_market(kmarket(e, "DEN", "Dnvr", "no"), "historical", "KXNBAGAME")
    kstore.put_market(kmarket(e, "UTA", "Utah", "yes"), "historical", "KXNBAGAME")
    calls = []

    def resolver(kevent, candidates):
        calls.append(kevent["event"])
        return {"odds_event_id": "o3", "teams": {"Dnvr": "Denver Nuggets", "Utah": "Utah Jazz"}}

    resolver.source = "fake"
    sl.main(["--store", str(store_dir), "pair", "--period", "dev", "--leagues", "NBA"], resolver=resolver,
            clock=Clock(), periods=PERIODS)
    assert calls == [e]
    capsys.readouterr()
    sl.main(["--store", str(store_dir), "resolved"], clock=Clock(), periods=PERIODS)
    listed = json.loads(capsys.readouterr().out)
    assert [(r["kalshi_event"], r["odds_event_id"], r["source"]) for r in listed] == [(e, "o3", "resolver:fake")]
    sl.main(["--store", str(store_dir), "pair", "--period", "dev", "--leagues", "NBA"], resolver=resolver,
            clock=Clock(), periods=PERIODS)
    assert calls == [e]                                                  # paired once, never offered again


def test_default_resolver_request_shape():
    sent = []

    def post(url, payload, timeout):
        sent.append((url, payload))
        return {"choices": [{"message": {"content": '{"odds_event_id": "x", "teams": {"A": "B"}}'}}]}

    r = sl.LocalModelResolver(post=post)
    got = r({"event": "E", "league": "NBA", "day": date(2026, 1, 10), "teams": [{"code": "A", "name": "A"}]},
            [{"id": "x", "home": "B", "away": "C", "commence": "2026-01-11T00:00:00Z", "day": date(2026, 1, 10)}])
    url, payload = sent[0]
    assert url == "http://127.0.0.1:1235/v1/chat/completions" and payload["model"] == "qwen/qwen3.8-27b"
    assert payload["reasoning_effort"] == "none" and payload["temperature"] == 0
    assert got == {"odds_event_id": "x", "teams": {"A": "B"}} and r.source == "qwen/qwen3.8-27b"
    bad = sl.LocalModelResolver(post=lambda u, p, t: {"choices": [{"message": {"content": "not json"}}]})
    assert bad({"event": "E", "league": "NBA", "day": date(2026, 1, 10), "teams": []}, []) is None


# ---------------------------------------------------------------- the numbers (outcome 7)

def test_fee_rounded_up_to_the_cent_on_an_order_of_100():
    assert sl.kalshi_fee(0.01) == pytest.approx(0.0007, abs=1e-12)    # 0.0693 dollars on 100 -> 0.07
    assert sl.kalshi_fee(0.50) == pytest.approx(0.0175, abs=1e-12)    # 1.75 exactly
    assert sl.kalshi_fee(0.99) == pytest.approx(0.0007, abs=1e-12)
    assert sl.kalshi_fee(0.52) == pytest.approx(0.0175, abs=1e-12)    # 1.7472 -> 1.75


def test_books_probability_is_the_median_of_normalised_books_or_pinnacle_alone():
    books = {"pinnacle": (1.80, 2.10), "draftkings": (1.75, 2.15), "fanduel": (1.85, 2.00)}
    per = sl.book_probabilities(books)
    assert per["pinnacle"] == pytest.approx(2.10 / 3.90)
    assert sl.books_p(books, "median") == pytest.approx(2.10 / 3.90)
    assert sl.books_p({"pinnacle": (1.8, 2.1), "fanduel": (1.85, 2.0)}, "median") is None     # fewer than 3
    assert sl.books_p({"draftkings": (1.8, 2.1)}, "pinnacle") is None
    four = dict(books, betmgm=(1.70, 2.20))
    assert sl.books_p(four, "median") == pytest.approx((2.15 / 3.90 + 2.10 / 3.90) / 2)


# ---------------------------------------------------------------- evaluation (outcomes 6 and 8)

def game(league, day, books, home, away, y, reason=None):
    return {"league": league, "day": day, "kalshi_event": "E%s%s" % (league, day), "books": books,
            "home": None if home is None else {"bid": home[0], "ask": home[1]},
            "away": None if away is None else {"bid": away[0], "ask": away[1]}, "y": y, "reason": reason}


HAND = [
    game("NBA", date(2026, 1, 5), {"pinnacle": (1.80, 2.10), "draftkings": (1.75, 2.15), "fanduel": (1.85, 2.00)},
         (0.50, 0.52), (0.47, 0.49), 1),
    game("NBA", date(2026, 1, 7), {"pinnacle": (1.50, 2.80), "draftkings": (1.55, 2.60), "fanduel": (1.48, 2.90)},
         (0.58, 0.60), (0.40, 0.42), 0),
    game("NHL", date(2026, 1, 13), {"pinnacle": (2.40, 1.62), "draftkings": (2.30, 1.65)},
         (0.33, 0.35), (0.64, 0.66), 1),
    game("NHL", date(2026, 1, 14), {"pinnacle": (2.0, 2.0), "draftkings": (2.0, 2.0), "fanduel": (2.0, 2.0)},
         (0.40, 0.47), (0.52, 0.55), 1),                                                # 7 cents wide
    game("NHL", date(2026, 1, 15), {"pinnacle": (2.0, 2.0), "draftkings": (2.0, 2.0), "fanduel": (2.0, 2.0)},
         None, (0.52, 0.55), 1),                                                         # no quote
]


def test_hand_computed_evaluation_matches_to_six_decimals():
    out = sl.evaluate_games(HAND, margins=(0.02, 0.03, 0.05), versions=("median", "pinnacle"), seed=7, reps=500)
    pa, pb, pc = 2.10 / 3.90, 2.80 / 4.30, 1.62 / 4.02
    med = out["median"]["0.02"]
    assert med["games_counted"] == 2
    assert med["left_out"] == {"too_few_books": 1, "wide_spread": 1, "no_bid_ask": 1}
    assert med["brier"]["books"] == pytest.approx(((pa - 1) ** 2 + pb ** 2) / 2, abs=1e-6)
    assert med["brier"]["kalshi"] == pytest.approx(((0.51 - 1) ** 2 + 0.59 ** 2) / 2, abs=1e-6)
    assert med["brier"]["difference"] == pytest.approx(((pa - 1) ** 2 + pb ** 2 - 0.49 ** 2 - 0.59 ** 2) / 2, abs=1e-6)
    assert med["brier"]["ci95"][0] == pytest.approx(med["brier"]["difference"], abs=1e-12)   # one week only
    assert med["brier"]["ci95"][1] == pytest.approx(med["brier"]["difference"], abs=1e-12)
    # game B: home edge 0.651163 - 0.60 - 0.0168 = 0.034363 >= 0.02 and >= 0.03, not >= 0.05; home lost
    assert med["trading"]["trades"] == 1 and med["trading"]["mean_profit"] == pytest.approx(-0.6168, abs=1e-6)
    assert med["trading"]["t_weekly"] is None                                    # one week: no t
    assert out["median"]["0.03"]["trading"]["trades"] == 1 and out["median"]["0.05"]["trading"]["trades"] == 0
    pin = out["pinnacle"]["0.02"]
    assert pin["games_counted"] == 3 and pin["left_out"] == {"wide_spread": 1, "no_bid_ask": 1}
    assert pin["brier"]["books"] == pytest.approx(((pa - 1) ** 2 + pb ** 2 + (pc - 1) ** 2) / 3, abs=1e-6)
    assert pin["brier"]["kalshi"] == pytest.approx((0.49 ** 2 + 0.59 ** 2 + 0.66 ** 2) / 3, abs=1e-6)
    # game C: home edge 0.402985 - 0.35 - 0.0160 = 0.036985; home won: profit 1 - 0.35 - 0.016 = 0.634
    assert pin["trading"]["trades"] == 2
    assert pin["trading"]["mean_profit"] == pytest.approx((-0.6168 + 0.634) / 2, abs=1e-6)
    a, b = -0.6168, 0.634                                         # weeks 2 and 3 of 2026, one trade each
    sd = abs(a - b) / math.sqrt(2)
    assert pin["trading"]["t_weekly"] == pytest.approx(((a + b) / 2) / (sd / math.sqrt(2)), abs=1e-6)
    assert pin["trading"]["weeks"] == 2
    lo, hi = pin["brier"]["ci95"]
    assert lo <= pin["brier"]["difference"] <= hi
    nhl = pin["by_league"]["NHL"]
    assert nhl["games_counted"] == 1 and nhl["trading"]["mean_profit"] == pytest.approx(0.634, abs=1e-6)
    assert out == sl.evaluate_games(HAND, margins=(0.02, 0.03, 0.05), versions=("median", "pinnacle"), seed=7,
                                    reps=500)                      # a fixed seed: the same interval every time


def test_one_trade_a_game_on_the_side_with_the_larger_edge():
    g = game("NBA", date(2026, 1, 5), {"pinnacle": (3.0, 1.5), "draftkings": (3.0, 1.5), "fanduel": (3.0, 1.5)},
             (0.30, 0.32), (0.55, 0.57), 0)                       # p(home) = 1/3: home edge < 0, away 0.0795
    out = sl.evaluate_games([g], margins=(0.02,), versions=("median",), seed=1, reps=10)
    tr = out["median"]["0.02"]["trading"]
    assert tr["trades"] == 1 and tr["list"][0]["side"] == "away"
    assert tr["list"][0]["profit"] == pytest.approx(1 - 0.57 - sl.kalshi_fee(0.57))


def test_no_look_ahead_everything_after_the_moment_changes_nothing(tmp_path):
    plain, _ = build_world(tmp_path / "a")
    altered, _ = build_world(tmp_path / "b", alter_after=True)
    kb = kh.HistoryStore(altered / "kalshi.sqlite")                  # more prints after the moment in the altered one
    for ticker in ("KXNBAGAME-26JAN10BOSMIA-MIA", "KXNBAGAME-26JAN10BOSMIA-BOS"):
        kb.put_candle(ticker, kcandle(M1 + 60, 0.99, 1.0), "historical")
    lb = sl.LinesStore(altered / "lines.sqlite")
    later = snap("2026-01-11T00:25:00Z", [ev("o1", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z",
                                             {"pinnacle": (1.01, 50.0), "draftkings": (1.01, 50.0),
                                              "fanduel": (1.01, 50.0)})])
    lb.put_snapshot("NBA", "2026-01-11T00:25:00Z", "extra", date(2026, 1, 10), later)
    ga, _ = sl.collect_games(sl.LinesStore(plain / "lines.sqlite"), kh.HistoryStore(plain / "kalshi.sqlite"),
                             *PERIODS["dev"], leagues=["NBA"])
    gb, _ = sl.collect_games(lb, kb, *PERIODS["dev"], leagues=["NBA"])
    assert ga == gb and len(ga) == 2
    first = [g for g in ga if g["kalshi_event"] == "KXNBAGAME-26JAN10BOSMIA"][0]
    assert first["moment"] == "2026-01-10T23:55:00Z"                 # the snapshot's own reported time
    assert first["home"] == {"bid": 0.50, "ask": 0.52} and first["away"] == {"bid": 0.47, "ask": 0.49}
    ea = sl.evaluate_games(ga, seed=3, reps=50)
    eb = sl.evaluate_games(gb, seed=3, reps=50)
    assert ea == eb


def test_prints_after_a_market_close_are_dropped():
    candles = [{"end_ts": 120, "yes_bid": {"close": 0.4}, "yes_ask": {"close": 0.42}},
               {"end_ts": 180, "yes_bid": {"close": 0.98}, "yes_ask": {"close": 0.99}}]
    # Kalshi leaves out a minute in which nothing changed: the last candle at or before the moment stands for it
    assert sl.quote_at(candles, moment=250, close_ts=9000) == {"bid": 0.98, "ask": 0.99}
    assert sl.quote_at(candles, moment=180 + 3600, close_ts=9000) is None     # nothing within the hour before
    assert sl.quote_at(candles, moment=179, close_ts=900) == {"bid": 0.4, "ask": 0.42}   # the minute ending 120
    assert sl.quote_at(candles, moment=180, close_ts=900) == {"bid": 0.98, "ask": 0.99}
    assert sl.quote_at(candles, moment=180, close_ts=150) == {"bid": 0.4, "ask": 0.42}   # the print after the close
    assert sl.quote_at(candles, moment=180, close_ts=100) is None             # ... is dropped, and so is all of it
    assert sl.quote_at([{"end_ts": 120, "yes_bid": {"close": 0.0}, "yes_ask": {"close": 0.42}}], 120, 200) is None


def test_end_to_end_dev_evaluation_from_the_stores(tmp_path, capsys):
    store_dir, _ = build_world(tmp_path)
    capsys.readouterr()
    assert sl.main(["--store", str(store_dir), "evaluate", "--period", "dev"], clock=Clock(), periods=PERIODS) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["period"] == "dev" and out["pairing"]["paired"] == 2
    assert set(out["results"]) == {"median", "pinnacle"}
    assert set(out["results"]["median"]) == {"0.02", "0.03", "0.05"}
    assert out["results"]["median"]["0.02"]["games_counted"] == 2
    assert out["results"]["pinnacle"]["0.02"]["games_counted"] == 2
    for key in ("brier", "trading", "by_league", "left_out"):
        assert key in out["results"]["median"]["0.03"]


def test_test_period_needs_a_stored_choice_and_runs_once(tmp_path, capsys):
    store_dir, t = build_world(tmp_path)
    args = ["--store", str(store_dir)]
    with pytest.raises(sl.Refused, match="choice"):
        sl.main(args + ["evaluate", "--period", "test"], clock=Clock(), periods=PERIODS)
    with pytest.raises(sl.Refused, match="choice"):
        sl.main(args + ["fetch-odds", "--period", "test", "--cap", "9000"], odds_client=odds_client(t),
                clock=Clock(), periods=PERIODS)
    with pytest.raises(SystemExit):
        sl.main(args + ["evaluate", "--period", "test", "--margin", "0.03"], clock=Clock(), periods=PERIODS)
    with pytest.raises(ValueError):
        sl.main(args + ["choose", "--margin", "0.04", "--version", "median"], clock=Clock(), periods=PERIODS)
    assert sl.main(args + ["choose", "--margin", "0.03", "--version", "pinnacle"], clock=Clock(), periods=PERIODS) == 0
    with pytest.raises(sl.Refused, match="already"):
        sl.main(args + ["choose", "--margin", "0.02", "--version", "median"], clock=Clock(), periods=PERIODS)
    capsys.readouterr()
    assert sl.main(args + ["evaluate", "--period", "test"], clock=Clock(), periods=PERIODS) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["choice"] == {"margin": 0.03, "version": "pinnacle"} and set(out["results"]) == {"pinnacle"}
    assert set(out["results"]["pinnacle"]) == {"0.03"}
    with pytest.raises(sl.Refused, match="already"):
        sl.main(args + ["evaluate", "--period", "test"], clock=Clock(), periods=PERIODS)
    store = sl.LinesStore(store_dir / "lines.sqlite")
    assert store.result("test")["choice"] == {"margin": 0.03, "version": "pinnacle"}


def test_kalshi_candles_are_fetched_only_around_each_moment(tmp_path):
    store_dir, _ = build_world(tmp_path)
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    kstore.db.execute("DELETE FROM windows")
    kstore.db.commit()
    routes = {"/historical/cutoff": {"market_settled_ts": "2026-09-13T00:00:00Z"}}
    for ticker in ("KXNBAGAME-26JAN10BOSMIA-MIA", "KXNBAGAME-26JAN10BOSMIA-BOS", "KXNBAGAME-26JAN10LALNYK-NYK",
                   "KXNBAGAME-26JAN10LALNYK-LAL"):
        routes["/historical/markets/%s/candlesticks" % ticker] = {"candlesticks": [kcandle(60, 0.5, 0.52)]}
    client, t = kalshi_client(routes)
    sl.main(["--store", str(store_dir), "kalshi-candles", "--period", "dev", "--leagues", "NBA"],
            kalshi_client=client, clock=Clock(), periods=PERIODS)
    asked = [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)) for u in t.urls if "candlesticks" in u]
    assert len(asked) == 4
    ends = sorted({int(q["end_ts"]) for q in asked})
    assert ends == [M1 // 60 * 60, M2 // 60 * 60]
    assert all(int(q["end_ts"]) - int(q["start_ts"]) == 3600 and q["period_interval"] == "1" for q in asked)


def test_kalshi_markets_command_fetches_each_series_keyless(tmp_path):
    routes = {"/historical/cutoff": {"market_settled_ts": "2026-09-13T00:00:00Z"},
              "/historical/markets": {"markets": [kmarket("KXNBAGAME-26JAN10BOSMIA", "BOS", "Boston", "no")],
                                      "cursor": ""}}
    client, t = kalshi_client(routes)
    sl.main(["--store", str(tmp_path / "s"), "kalshi-markets", "--period", "dev", "--leagues", "NBA"],
            kalshi_client=client, clock=Clock(), periods=PERIODS)
    assert any("series_ticker=KXNBAGAME" in u for u in t.urls) and all("key" not in u.lower() for u in t.urls)
    assert len(kh.HistoryStore(tmp_path / "s" / "kalshi.sqlite").markets("KXNBAGAME")) == 1


def test_nothing_here_can_place_an_order_or_read_webull():
    src = Path(sl.__file__).read_text() + Path(kh.__file__).read_text()
    for word in ("webull", "orders", "order_step", "paper", "portfolio", "create_order", "risk_gate"):
        assert word not in src.lower()
    assert "POST" not in Path(kh.__file__).read_text()
