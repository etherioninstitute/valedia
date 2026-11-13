import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from valedia_trader.research_loop import sports_lines as sl
from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh
from valedia_trader.venues import odds_api as oa

UTC = timezone.utc
KEYS = {"ODDS_API_KEY": "PAID-KEY-SECRET-0001", "ODDS_API_KEY_FREE": "FREE-KEY-SECRET-0002"}
PERIODS = {"dev": (date(2026, 1, 5), date(2026, 3, 1)), "test": (date(2026, 9, 20), date(2026, 11, 8))}
SERIES = [s for _, s in sl.LEAGUES.values()]


def T(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def ts(text):
    return int(T(text).timestamp())


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """The Odds API: the free sports list, and historical odds answered by (sport, date) from a dict or a function.
    Usage headers as the API sends them (`used` grows by each charge); `headers=False` leaves them out of the odds
    answers. Records every call with its params."""
    def __init__(self, routes, last="10", remaining=None, used=0, headers=True):
        self.routes, self.last, self.calls = routes, last, []
        self.remaining, self.used, self.headers = remaining, used, headers

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        if url.endswith("/v4/sports"):
            body, cost, send = [], 0, True
        else:
            sport, when = url.rstrip("/").split("/")[-2], params.get("date")
            body = self.routes(sport, when) if callable(self.routes) else self.routes.get((sport, when), snap(when, []))
            cost, send = (int(self.last) if body["data"] else 0), self.headers
        self.used += cost
        if self.remaining is not None:
            self.remaining -= cost
        if not send:
            return 200, {}, body
        headers = {"x-requests-last": str(cost), "x-requests-used": str(self.used)}
        if self.remaining is not None:
            headers["x-requests-remaining"] = str(self.remaining)
        return 200, headers, body

    def odds_calls(self):
        return [(u, p) for u, p in self.calls if u.endswith("/odds")]


def odds_client(transport, keys=KEYS):
    return oa.OddsApiClient(transport, key_reader=lambda name: keys[name], sleep=lambda s: None, max_retries=0)


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


def list_all(kstore, periods):
    """Listings for every series and period, as `kalshi-markets --period all` leaves them."""
    for name, (start, end) in periods.items():
        for s in SERIES:
            if not kstore.listed_any(s, start, end):
                kstore.put_listing(s, "historical", start, end, 0)


# ---------------------------------------------------------------- the waves

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


# ---------------------------------------------------------------- the credit ledger (outcomes 3, 4 and 5)

def one_day_routes(*dates):
    return {("basketball_nba", d): snap(d, [ev("e", "A", "B", d, {"pinnacle": (2, 2)})]) for d in dates}


def test_cap_on_the_command_line_refuses_the_call_that_would_pass_it(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport(one_day_routes("2026-01-05T16:00:00Z", "2026-01-06T16:00:00Z", "2026-01-07T16:00:00Z"))
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
    assert (call["remaining"], call["used"], call["last"], call["charged"]) == (90, 10, 10, 10)
    assert call["status"] == "done"
    assert t.calls[0][1]["apiKey"] == KEYS["ODDS_API_KEY"]                # the paid key was sent ...
    raw = (tmp_path / "credits.sqlite").read_bytes()
    assert b"PAID-KEY" not in raw and b"FREE-KEY" not in raw              # ... and never stored
    assert "apiKey" not in call["params"]


def test_a_cost_different_from_the_rule_stops_the_run_and_is_used_from_then_on(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport(one_day_routes(*("2026-01-0%dT16:00:00Z" % i for i in (5, 6, 7))), last="20")
    client = odds_client(t)
    assert ledger.expected_cost(sl.SNAPSHOT_RULE, 10) == 10
    with pytest.raises(sl.LedgerStop, match="20") as err:
        sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=45)
    assert "10" in str(err.value) and "book list" in str(err.value)
    events = ledger.events()
    assert len(events) == 1 and events[0]["kind"] == "cost_differs"
    assert json.loads(events[0]["detail"]) == {"rule": sl.SNAPSHOT_RULE, "expected": 10, "charged": 20}
    assert ledger.expected_cost(sl.SNAPSHOT_RULE, 10) == 20
    assert ledger.spent() == 20
    sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-06T16:00:00Z", cap=45)   # run again: 20 expected
    with pytest.raises(sl.CreditCapError):                                # 40 spent + 20 > 45, though the rule says 10
        sl.paid_snapshot(client, ledger, "basketball_nba", "2026-01-07T16:00:00Z", cap=45)
    assert len(t.calls) == 2 and len(ledger.events()) == 1


def test_an_empty_snapshot_that_costs_nothing_is_not_a_cost_difference(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport({})
    sl.paid_snapshot(odds_client(t), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    assert ledger.events() == [] and ledger.spent() == 0 and ledger.expected_cost(sl.SNAPSHOT_RULE, 10) == 10


def test_a_failed_call_is_attempted_at_most_three_times_each_attempt_its_own_row(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    sent = []

    def broken(url, params, timeout):
        sent.append(url)
        raise ConnectionError("refused " + params["apiKey"])

    with pytest.raises(oa.OddsApiError) as err:
        sl.paid_snapshot(odds_client(broken), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    assert "PAID-KEY" not in str(err.value)
    assert len(sent) == 3
    assert [c["status"] for c in ledger.calls()] == ["unanswered"] * 3
    assert ledger.spent() == 30                     # nothing came back: each attempt counted until headers confirm it


def test_both_caps_are_checked_before_each_attempt(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    sent = []

    def broken(url, params, timeout):
        sent.append(url)
        raise ConnectionError("down")

    with pytest.raises(sl.CreditCapError, match="cap of 25"):
        sl.paid_snapshot(odds_client(broken), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=25)
    assert len(sent) == 2 and ledger.spent() == 20                       # the third attempt would pass 25
    month = sl.CreditLedger(tmp_path / "m.sqlite", monthly_cap=15, clock=Clock())
    sent.clear()
    with pytest.raises(sl.CreditCapError, match="month"):
        sl.paid_snapshot(odds_client(broken), month, "basketball_nba", "2026-01-05T16:00:00Z", cap=1000)
    assert len(sent) == 1


def test_each_attempt_records_its_own_usage_headers(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    answers = [(503, {"x-requests-last": "0", "x-requests-used": "5", "x-requests-remaining": "995"}, None),
               (200, {"x-requests-last": "10", "x-requests-used": "15", "x-requests-remaining": "985"},
                snap("2026-01-05T15:58:00Z", [ev("e", "A", "B", "x", {"pinnacle": (2, 2)})]))]

    def flaky(url, params, timeout):
        return answers.pop(0)

    body = sl.paid_snapshot(odds_client(flaky), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    assert body["timestamp"] == "2026-01-05T15:58:00Z"
    calls = ledger.calls()
    assert [(c["status"], c["used"], c["remaining"], c["charged"]) for c in calls] == [
        ("http_error", 5, 995, 0), ("done", 15, 985, 10)]
    assert ledger.spent() == 10


def test_a_failure_before_anything_is_sent_records_no_charge(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport({})
    sl.paid_snapshot(odds_client(t), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)   # headers: used 0
    nokey = odds_client(t, keys={"ODDS_API_KEY": "", "ODDS_API_KEY_FREE": ""})
    with pytest.raises(oa.OddsApiError, match="not set"):
        sl.paid_snapshot(nokey, ledger, "basketball_nba", "2026-01-06T16:00:00Z", cap=100)
    rows = ledger.calls()
    assert len(rows) == 2 and len(t.calls) == 1                          # no retry: nothing was sent
    assert (rows[1]["status"], rows[1]["charged"], rows[1]["used"], rows[1]["last"]) == ("not_sent", 0, None, None)
    assert ledger.spent() == 0


def test_an_answer_without_usage_headers_stops_and_the_row_says_so(tmp_path):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    t = OddsTransport(one_day_routes("2026-01-05T16:00:00Z"), headers=False)
    with pytest.raises(sl.LedgerStop, match="usage headers"):
        sl.paid_snapshot(odds_client(t), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    row = ledger.calls()[0]
    assert row["status"] == "no_headers" and row["charged"] is None
    assert ledger.spent() == 10                                           # counted at its expected cost
    assert len(t.calls) == 1


def crash_after(transport):
    """A transport whose answer is lost: the request reaches the API, then the process is interrupted."""
    def go(url, params, timeout):
        transport(url, params, timeout)
        raise KeyboardInterrupt
    return go


def crash_before(url, params, timeout):
    raise KeyboardInterrupt


@pytest.mark.parametrize("crash, cost", [(crash_after, 10), (lambda t: crash_before, 0)])
def test_a_pending_row_left_by_a_crash_stays_counted_until_the_headers_confirm_it(tmp_path, crash, cost):
    path = tmp_path / "credits.sqlite"
    ledger = sl.CreditLedger(path, clock=Clock())
    t = OddsTransport(one_day_routes("2026-01-05T16:00:00Z"), remaining=1000, used=100)
    sl.refresh_usage(odds_client(t), ledger)                              # the free call: used 100
    with pytest.raises(KeyboardInterrupt):
        sl.paid_snapshot(odds_client(crash(t)), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    assert [c["status"] for c in ledger.calls()] == ["done", "pending"] and ledger.spent() == 10
    again = sl.CreditLedger(path, clock=Clock())                          # a later run
    assert again.spent() == 10
    with pytest.raises(sl.CreditCapError):                               # the pending 10 is counted against caps
        sl.paid_snapshot(odds_client(t), again, "basketball_nba", "2026-01-05T16:00:00Z", cap=15)
    sl.refresh_usage(odds_client(t), again)                               # used is now 100 + what the crash cost
    row = again.calls()[1]
    assert (row["status"], row["charged"]) == ("confirmed", cost) and again.spent() == cost


def test_an_answer_stored_with_its_ledger_row_is_never_bought_again(tmp_path, monkeypatch):
    ledger = sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock())
    store = sl.LinesStore(tmp_path / "lines.sqlite")
    t = OddsTransport({("basketball_nba", "2026-01-05T16:00:00Z"):
                       snap("2026-01-05T15:57:00Z", [ev("e", "A", "B", "x", {"pinnacle": (2, 2)})])})
    put = sl.LinesStore.put_snapshot

    def lost(self, *a, **k):
        raise RuntimeError("killed before the snapshot was written")

    monkeypatch.setattr(sl.LinesStore, "put_snapshot", lost)
    with pytest.raises(RuntimeError):
        sl.buy(odds_client(t), ledger, store, "NBA", T("2026-01-05T16:00:00Z"), "first", date(2026, 1, 5), 9000,
               Clock())
    monkeypatch.setattr(sl.LinesStore, "put_snapshot", put)
    got = sl.buy(odds_client(t), sl.CreditLedger(tmp_path / "credits.sqlite", clock=Clock()),
                 sl.LinesStore(tmp_path / "lines.sqlite"), "NBA", T("2026-01-05T16:00:00Z"), "first",
                 date(2026, 1, 5), 9000, Clock())
    assert got["timestamp"] == "2026-01-05T15:57:00Z"
    assert len(t.odds_calls()) == 1 and ledger.spent() == 10


def test_one_process_at_a_time_per_ledger(tmp_path):
    store_dir = tmp_path / "store"
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    list_all(kstore, PERIODS)
    t = OddsTransport({})
    holder = sl.CreditLedger(store_dir / "credits.sqlite", clock=Clock())
    args = ["--store", str(store_dir), "fetch-odds", "--period", "dev", "--cap", "9000", "--leagues", "NBA"]
    with holder.lock():
        with pytest.raises(sl.LedgerBusy, match="another process"):
            sl.main(args, odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
        code = ("import sys\nfrom valedia_trader.research_loop import sports_lines as sl\n"
                "from valedia_trader.venues import odds_api as oa\n"
                "def no(*a):\n    raise SystemExit('a call was made')\n"
                "sys.exit(sl._cli(sys.argv[1:], odds_client=oa.OddsApiClient(no, key_reader=lambda n: 'x')))\n")
        proc = subprocess.run([sys.executable, "-B", "-c", code] + args, cwd=str(tmp_path), capture_output=True,
                              text=True, timeout=300, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert proc.returncode == 2, proc.stderr
    assert proc.stderr.strip().count("\n") == 0 and "another process holds the credit ledger" in proc.stderr
    assert t.calls == []
    assert sl.main(args, odds_client=odds_client(t), clock=Clock(), periods=PERIODS) == 0     # free again


def test_a_ledger_made_before_rev_2_stays_usable(tmp_path):
    path = tmp_path / "credits.sqlite"
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, key TEXT, rule TEXT, path TEXT,
            params TEXT, expected INTEGER, charged INTEGER, remaining INTEGER, used INTEGER, last INTEGER, ok INTEGER,
            error TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, detail TEXT);
        CREATE TABLE costs (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, rule TEXT, cost INTEGER);
        INSERT INTO calls (at, key, rule, path, params, expected, charged, remaining, used, last, ok, error) VALUES
            ('2026-11-13T01:00:00Z', 'paid', 'x', '/v4/x', '{}', 10, 10, 990, 10, 10, 1, NULL);
    """)
    db.commit()
    db.close()
    ledger = sl.CreditLedger(path, clock=Clock())
    assert ledger.spent() == 10 and ledger.calls()[0]["status"] is None
    t = OddsTransport(one_day_routes("2026-01-05T16:00:00Z"), remaining=990, used=10)
    sl.paid_snapshot(odds_client(t), ledger, "basketball_nba", "2026-01-05T16:00:00Z", cap=100)
    assert ledger.spent() == 20 and ledger.calls()[1]["status"] == "done"


# ---------------------------------------------------------------- a small world: stores built by the commands

NBA_BOOKS_1 = {"pinnacle": (1.80, 2.10), "draftkings": (1.75, 2.15), "fanduel": (1.85, 2.00)}
NBA_BOOKS_2 = {"pinnacle": (1.50, 2.80), "draftkings": (1.55, 2.60), "fanduel": (1.48, 2.90), "bovada": (1.01, 30.0)}
M0 = ts("2026-01-10T14:25:00Z")             # the reported time of the early wave's snapshot (before 16:00 UTC)
M1 = ts("2026-01-10T23:55:00Z")             # ... of the evening wave
M2 = ts("2026-01-11T02:25:00Z")             # ... of the late wave
EARLY, EVENING, LATE = "KXNBAGAME-26JAN10CHIORL", "KXNBAGAME-26JAN10BOSMIA", "KXNBAGAME-26JAN10LALNYK"
ALTERED_BOOKS = {"pinnacle": (1.01, 50.0), "draftkings": (1.01, 50.0), "fanduel": (1.01, 50.0)}


def world_routes(alter_after=None, start_change=None, wave1_stamp="2026-01-10T23:55:00Z"):
    """Odds snapshots of one NBA day. `alter_after`: every price of a snapshot reported after that time is changed
    (start times kept). `start_change`: (event id, new start) in the 16:00 UTC snapshot only."""
    e0 = ev("o0", "Orlando Magic", "Chicago Bulls", "2026-01-10T15:00:00Z", NBA_BOOKS_1)   # 10 am Eastern
    e1 = ev("o1", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z", NBA_BOOKS_1)
    e2 = ev("o2", "New York Knicks", "Los Angeles Lakers", "2026-01-11T03:00:00Z", NBA_BOOKS_2)
    e3 = ev("o3", "Utah Jazz", "Denver Nuggets", "2026-01-12T02:00:00Z", NBA_BOOKS_1)   # next Eastern day
    first = [e0, e1, e2, e3]
    if start_change:
        first = [dict(e, commence_time=start_change[1]) if e["id"] == start_change[0] else e for e in first]
    snaps = {"2026-01-10T16:00:00Z": snap("2026-01-10T15:55:00Z", first),
             "2026-01-10T14:30:00Z": snap("2026-01-10T14:25:00Z", [e0, e1]),
             "2026-01-11T00:00:00Z": snap(wave1_stamp, [e1, e2]),
             "2026-01-11T02:30:00Z": snap("2026-01-11T02:25:00Z", [e2])}
    if alter_after:
        for at, s in list(snaps.items()):
            if T(s["timestamp"]) > T(alter_after):
                snaps[at] = snap(s["timestamp"], [ev(e["id"], e["home_team"], e["away_team"], e["commence_time"],
                                                     ALTERED_BOOKS) for e in s["data"]])
    return {("basketball_nba", at): s for at, s in snaps.items()}


def kalshi_world(alter_after=None):
    markets = [kmarket(EARLY, "CHI", "Chicago", "no", close="2026-01-10T17:40:00Z", expected="2026-01-10T18:00:00Z"),
               kmarket(EARLY, "ORL", "Orlando", "yes", close="2026-01-10T17:40:00Z", expected="2026-01-10T18:00:00Z"),
               kmarket(EVENING, "BOS", "Boston", "no"), kmarket(EVENING, "MIA", "Miami", "yes"),
               kmarket(LATE, "LAL", "Los Angeles L", "no", close="2026-01-11T05:30:00Z",
                       expected="2026-01-11T06:00:00Z"),
               kmarket(LATE, "NYK", "New York", "yes", close="2026-01-11T05:30:00Z", expected="2026-01-11T06:00:00Z")]
    m0, m1, m2 = M0 // 60 * 60, M1 // 60 * 60, M2 // 60 * 60
    candles = {"%s-ORL" % EARLY: [kcandle(m0 - 60, 0.50, 0.53), kcandle(m0, 0.55, 0.57), kcandle(m0 + 300, 0.6, 0.62)],
               "%s-CHI" % EARLY: [kcandle(m0 - 60, 0.45, 0.48), kcandle(m0, 0.42, 0.44), kcandle(m0 + 300, 0.36, 0.4)],
               "%s-MIA" % EVENING: [kcandle(m1 - 60, 0.30, 0.31), kcandle(m1, 0.50, 0.52),
                                    kcandle(m1 + 300, 0.60, 0.61)],
               "%s-BOS" % EVENING: [kcandle(m1 - 60, 0.68, 0.70), kcandle(m1, 0.47, 0.49),
                                    kcandle(m1 + 300, 0.38, 0.40)],
               "%s-NYK" % LATE: [kcandle(m1, 0.55, 0.57), kcandle(m2, 0.58, 0.60)],
               "%s-LAL" % LATE: [kcandle(m1, 0.43, 0.45), kcandle(m2, 0.40, 0.42)]}
    if alter_after:
        cut = ts(alter_after)
        flip = {"yes": "no", "no": "yes"}
        markets = [dict(m, last_price_dollars="0.9900", volume_fp="999999", result=flip[m["result"]])
                   for m in markets]
        candles = {k: [c if c["end_period_ts"] <= cut else kcandle(c["end_period_ts"], 0.97, 0.99) for c in v]
                   + [kcandle(cut // 60 * 60 + 60, 0.01, 0.02)] for k, v in candles.items()}
    return markets, candles


def setup_world(root: Path, alter_after=None, periods=PERIODS, candles=True):
    store_dir = root / "store"
    markets, cdl = kalshi_world(alter_after)
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    for m in markets:
        kstore.put_market(m, "historical", "KXNBAGAME")
    list_all(kstore, periods)
    if candles:
        put_candles(kstore, cdl)
    return store_dir


def put_candles(kstore, candles):
    for ticker, rows in candles.items():
        for c in rows:
            kstore.put_candle(ticker, c, "historical")
        kstore.put_window(ticker, 0, 2**40, "historical", len(rows))


def build_world(root: Path, alter_after=None, resolver=None, start_change=None, transport=None,
                wave1_stamp="2026-01-10T23:55:00Z"):
    """Kalshi markets and candles through the store; odds snapshots bought through the real code path."""
    store_dir = setup_world(root, alter_after)
    t = transport or OddsTransport(world_routes(alter_after, start_change, wave1_stamp))
    out = sl.main(["--store", str(store_dir), "fetch-odds", "--period", "dev", "--cap", "9000",
                   "--leagues", "NBA"], odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
    assert out == 0
    sl.main(["--store", str(store_dir), "pair", "--period", "dev", "--leagues", "NBA", "--no-resolver"],
            clock=Clock(), periods=PERIODS)
    return store_dir, t


def test_snapshots_follow_the_rule_with_ten_named_books_and_are_never_bought_twice(tmp_path):
    store_dir, t = build_world(tmp_path)
    dates = [p["date"] for _, p in t.odds_calls()]
    assert dates == ["2026-01-10T16:00:00Z", "2026-01-10T14:30:00Z", "2026-01-11T00:00:00Z", "2026-01-11T02:30:00Z"]
    assert all(p["bookmakers"] == ",".join(sl.BOOKS) and p["markets"] == "h2h" for _, p in t.odds_calls())
    assert len(sl.BOOKS) == 10 and sl.BOOKS[0] == "pinnacle"
    store = sl.LinesStore(store_dir / "lines.sqlite")
    snaps = store.snapshots("NBA")
    assert [s["timestamp"] for s in snaps] == ["2026-01-10T14:25:00Z", "2026-01-10T15:55:00Z", "2026-01-10T23:55:00Z",
                                               "2026-01-11T02:25:00Z"]
    assert sl.main(["--store", str(store_dir), "fetch-odds", "--period", "dev", "--cap", "9000", "--leagues", "NBA"],
                   odds_client=odds_client(t), clock=Clock(), periods=PERIODS) == 0
    assert len(t.odds_calls()) == 4                                             # nothing bought again


def test_fetch_odds_begins_with_the_free_sports_call_which_refreshes_a_stale_remaining_figure(tmp_path):
    store_dir = tmp_path / "store"
    old = sl.CreditLedger(store_dir / "credits.sqlite", clock=Clock("2026-10-31T12:00:00Z"))
    sl.paid_snapshot(odds_client(OddsTransport(one_day_routes("2026-01-04T16:00:00Z"), remaining=10)), old,
                     "basketball_nba", "2026-01-04T16:00:00Z", cap=9000)
    assert old.last_remaining() == 0                                   # last month's figure: nothing left
    _, t = build_world(tmp_path, transport=OddsTransport(world_routes(), remaining=20000))
    assert t.calls[0][0].endswith("/v4/sports") and "date" not in t.calls[0][1]
    assert len(t.odds_calls()) == 4                                    # the fresh figure lets the purchase go on


def test_a_cost_per_call_other_than_ten_stops_the_run_before_the_second_call(tmp_path, capsys):
    store_dir = setup_world(tmp_path)
    t = OddsTransport(world_routes(), last="20")
    rc = sl.main(["--store", str(store_dir), "fetch-odds", "--period", "dev", "--cap", "9000", "--leagues", "NBA"],
                 odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and len(t.odds_calls()) == 1
    assert "20" in out["stopped"] and "10" in out["stopped"]
    assert sl.LinesStore(store_dir / "lines.sqlite").snapshot("NBA", "2026-01-10T16:00:00Z") is not None


def test_a_run_stops_after_an_answer_without_usage_headers(tmp_path, capsys):
    store_dir = setup_world(tmp_path)
    t = OddsTransport(world_routes(), headers=False)
    rc = sl.main(["--store", str(store_dir), "fetch-odds", "--period", "dev", "--cap", "9000", "--leagues", "NBA"],
                 odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and len(t.odds_calls()) == 1 and "usage headers" in out["stopped"]
    assert [c["status"] for c in sl.CreditLedger(store_dir / "credits.sqlite").calls()] == ["done", "no_headers"]


def test_development_purchase_is_refused_until_the_test_period_is_listed_for_all_five_leagues(tmp_path):
    kstore = kh.HistoryStore(tmp_path / "kalshi.sqlite")
    kstore.put_listing("KXNBAGAME", "historical", *PERIODS["dev"], 0)
    kstore.put_listing("KXNBAGAME", "live", *PERIODS["test"], 0)        # the named league's test period only
    t = OddsTransport({})
    with pytest.raises(sl.Refused, match="test period"):
        sl.main(["--store", str(tmp_path), "fetch-odds", "--period", "dev", "--cap", "9000", "--leagues", "NBA"],
                odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
    assert t.odds_calls() == []


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


def test_plan_for_development_shows_the_test_reservation_of_all_five_leagues(tmp_path, capsys):
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
    list_all(kstore, PERIODS)
    rc = sl.main(["--store", str(store_dir), "plan", "--period", "dev", "--cap", "160", "--leagues", "NBA"],
                 clock=Clock(), periods=PERIODS)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reserved_for_test"] == 80 and out["left_for_dev"] == 80     # NHL's test days, though NBA is named
    assert out["dev"]["dropped_dates"] == ["2026-01-07"] and out["dev"]["cost"] == 80


class SlateRoutes:
    """Any league-day: three games at 18:00 and 21:00 UTC and 7:30 pm Eastern, so 1 + 3 snapshots a day."""
    def __call__(self, sport, when):
        day = sl.eastern_date(when)
        base = datetime(day.year, day.month, day.day, tzinfo=UTC)
        times = [base + timedelta(hours=18), base + timedelta(hours=21), base + timedelta(hours=24, minutes=30)]
        return snap(when, [ev("%s-%s-%d" % (sport, day, i), "H%d" % i, "A%d" % i, iso(s), NBA_BOOKS_1, sport)
                           for i, s in enumerate(times)])


def test_the_verifier_sequence_development_for_three_leagues_then_the_test_period(tmp_path, capsys):
    store_dir = tmp_path / "store"
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    for lg in ("NBA", "NHL", "NFL"):
        series = sl.LEAGUES[lg][1]
        for d in ("26JAN05", "26JAN06", "26JAN07"):
            e = "%s-%sAAABBB" % (series, d)
            kstore.put_market(kmarket(e, "AAA", "A", "no"), "historical", series)
            kstore.put_market(kmarket(e, "BBB", "B", "yes"), "historical", series)
    for lg, (_, series) in sl.LEAGUES.items():
        e = "%s-26SEP20AAABBB" % series
        kstore.put_market(kmarket(e, "AAA", "A", "no"), "live", series)
        kstore.put_market(kmarket(e, "BBB", "B", "yes"), "live", series)
    list_all(kstore, PERIODS)
    t = OddsTransport(SlateRoutes())
    args = ["--store", str(store_dir)]
    rc = sl.main(args + ["fetch-odds", "--period", "dev", "--cap", "360", "--leagues", "NBA", "NHL", "NFL"],
                 odds_client=odds_client(t), clock=Clock(), periods=PERIODS)
    dev = json.loads(capsys.readouterr().out)
    assert rc == 0 and dev["spent_after"] == 120 and dev["stopped"]      # one day of three leagues; the rest waits
    sl.main(args + ["plan", "--period", "test", "--cap", "360"], clock=Clock(), periods=PERIODS)
    plan = json.loads(capsys.readouterr().out)
    assert plan["test"]["cost"] == 200 and plan["fits"] is True
    sl.main(args + ["choose", "--margin", "0.03", "--version", "median"], clock=Clock(), periods=PERIODS)
    capsys.readouterr()
    rc = sl.main(args + ["fetch-odds", "--period", "test", "--cap", "360"], odds_client=odds_client(t), clock=Clock(),
                 periods=PERIODS)
    test = json.loads(capsys.readouterr().out)
    assert rc == 0 and test["stopped"] is None and test["bought"] == 20 and test["spent_after"] == 320
    store = sl.LinesStore(store_dir / "lines.sqlite")
    assert all(len([s for s in store.snapshots(lg) if s["day"] == "2026-09-20"]) == 4 for lg in sl.LEAGUES)


# ---------------------------------------------------------------- pairing (outcomes 1 and 8)

def test_team_names_match_by_one_strict_rule():
    assert sl.names_match("Los Angeles L", "Los Angeles Lakers")
    assert not sl.names_match("Los Angeles L", "Los Angeles Clippers")
    assert sl.names_match("NY Rangers", "New York Rangers") and sl.names_match("New York I", "New York Islanders")
    assert sl.names_match("Kent St.", "Kent State Golden Flashes")
    assert not sl.names_match("Florida", "Florida State Seminoles") and sl.names_match("Florida", "Florida Gators")
    assert not sl.names_match("Miami (FL)", "Miami (OH) RedHawks") and not sl.names_match("Miami", "Miami (OH) RedHawks")
    assert not sl.names_match("Miami (FL)", "Miami Hurricanes")         # a bracketed part must be on both sides
    assert sl.names_match("Miami (OH)", "Miami (OH) RedHawks") and sl.names_match("Miami", "Miami Hurricanes")
    assert sl.names_match("Texas A&M", "Texas A&M Aggies") and not sl.names_match("Texas", "Texas A&M Aggies")
    assert not sl.names_match("Michigan", "Central Michigan Chippewas")
    assert sl.names_match("St. John's", "St. John's Red Storm") and sl.names_match("St. Louis", "St Louis Blues")
    # the verifier's examples, and qualifying words in both directions
    assert not sl.names_match("Penn St.", "Washington State Cougars")
    assert not sl.names_match("Kansas", "Kansas State Wildcats") and not sl.names_match("Texas", "Texas Tech Red Raiders")
    assert not sl.names_match("Florida", "Florida Atlantic Owls")
    assert not sl.names_match("Florida St.", "Florida Gators") and not sl.names_match("Kansas St.", "Kansas Jayhawks")
    assert not sl.names_match("Utah", "Utah Valley Wolverines") and not sl.names_match("Utah Valley", "Utah Utes")
    assert not sl.names_match("Kansas", "Kansas City Chiefs") and not sl.names_match("Virginia", "West Virginia Mountaineers")
    assert not sl.names_match("St. Mary's", "Mount St. Mary's Mountaineers")
    assert sl.names_match("St. Mary's", "Saint Mary's Gaels")
    assert not sl.names_match("Texas A&M", "Texas A&M-Corpus Christi Islanders")
    assert sl.names_match("Texas A&M-Corpus Christi", "Texas A&M-Corpus Christi Islanders")
    assert sl.names_match("Boston U", "Boston University Terriers")
    assert not sl.names_match("Boston College", "Boston University Terriers")
    assert not sl.names_match("North Carolina", "North Carolina A&T Aggies")
    assert not sl.names_match("NC State", "North Carolina Tar Heels")
    assert not sl.names_match("Texas St.", "Texas Southern Tigers")      # "St." is never an acronym of other words
    assert not sl.names_match("Carolina", "Coastal Carolina Chanticleers")   # the name must start the full name
    assert not hasattr(sl, "loosely_names")                              # one name rule only


def kev(event, teams, day=None, expected="2026-01-11T03:30:00Z", league="NBA"):
    return {"event": event, "league": league, "day": day or kh.event_date(event), "expected": expected,
            "teams": [{"code": c, "name": n, "ticker": event + "-" + c} for c, n in teams]}


def ogame(eid, home, away, commence, league="NBA"):
    return {"id": eid, "league": league, "home": home, "away": away, "commence": commence,
            "day": sl.eastern_date(commence)}


def test_pairing_by_league_teams_eastern_date_and_start_with_consecutive_days():
    k = [kev("KXNBAGAME-26JAN10BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")]),
         kev("KXNBAGAME-26JAN11BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")], expected="2026-01-12T03:30:00Z")]
    o = [ogame("x2", "Miami Heat", "Boston Celtics", "2026-01-12T00:30:00Z"),       # 11 January, Eastern
         ogame("x1", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z")]       # 10 January, Eastern
    pairs, left, log = sl.pair_games(k, o, resolver=None)
    got = {p["kalshi_event"]: p["odds_event_id"] for p in pairs}
    assert got == {"KXNBAGAME-26JAN10BOSMIA": "x1", "KXNBAGAME-26JAN11BOSMIA": "x2"} and left == {}
    p = pairs[0]
    assert p["home_ticker"].endswith("-MIA") and p["away_ticker"].endswith("-BOS") and p["source"] == "code"


def test_the_start_must_lie_within_six_hours_before_kalshis_expected_end():
    k = [kev("KXNBAGAME-26JAN10BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")], expected="2026-01-11T03:30:00Z")]
    for start, ok in (("2026-01-10T21:30:00Z", True), ("2026-01-10T21:29:00Z", False),
                      ("2026-01-11T03:30:00Z", True), ("2026-01-11T03:31:00Z", False)):
        pairs, left, _ = sl.pair_games(k, [ogame("x", "Miami Heat", "Boston Celtics", start)])
        assert (len(pairs) == 1) is ok and (left == {} if ok else left == {"no_match": 1}), start
    pairs, left, _ = sl.pair_games([dict(k[0], expected="")], [ogame("x", "Miami Heat", "Boston Celtics",
                                                                     "2026-01-11T00:30:00Z")])
    assert pairs == [] and left == {"no_match": 1}                       # no expected end: never guessed


def test_a_doubleheader_is_paired_by_the_time_check_not_by_order():
    first = kev("KXNBAGAME-26JAN10BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")], expected="2026-01-10T20:30:00Z")
    second = kev("KXNBAGAME-26JAN10BOSMIAG2", [("BOS", "Boston"), ("MIA", "Miami")], day=date(2026, 1, 10),
                 expected="2026-01-11T03:30:00Z")
    o = [ogame("late", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z"),
         ogame("early", "Miami Heat", "Boston Celtics", "2026-01-10T17:30:00Z")]
    pairs, left, _ = sl.pair_games([second, first], o, resolver=None)
    assert {p["kalshi_event"]: p["odds_event_id"] for p in pairs} == {first["event"]: "early", second["event"]: "late"}
    pairs, left, _ = sl.pair_games([second], o, resolver=None)           # Kalshi lists only the late game
    assert [p["odds_event_id"] for p in pairs] == ["late"] and left == {}
    pairs, left, _ = sl.pair_games([first], o, resolver=None)            # ... or only the early one
    assert [p["odds_event_id"] for p in pairs] == ["early"] and left == {}
    close = [ogame("g1", "Miami Heat", "Boston Celtics", "2026-01-10T22:00:00Z"),
             ogame("g2", "Miami Heat", "Boston Celtics", "2026-01-10T23:30:00Z")]
    pairs, left, _ = sl.pair_games([second], close, resolver=None)       # both start inside the window
    assert pairs == [] and left == {"ambiguous": 1}

    def pick_one(kevent, candidates):                                    # a proposal cannot break the tie
        return {"odds_event_id": "g2", "teams": {"Miami": "Miami Heat", "Boston": "Boston Celtics"}}

    pairs, left, log = sl.pair_games([second], close, resolver=pick_one)
    assert pairs == [] and left == {"ambiguous": 1} and "another game" in log[0]["reason"]
    both = [dict(second, event="KXNBAGAME-26JAN10BOSMIAG3"), second]
    pairs, left, _ = sl.pair_games(both, [close[1]], resolver=None)      # two events for one game
    assert pairs == [] and left == {"ambiguous": 2}


def test_the_verifiers_lookalikes_are_never_paired_by_code_or_by_a_proposal():
    kansas = kev("KXNCAAMBGAME-26JAN10KUTEX", [("KU", "Kansas"), ("TEX", "Texas")], league="NCAAMB",
                 expected="2026-01-11T03:00:00Z")
    lookalike = ogame("ksu-ttu", "Kansas State Wildcats", "Texas Tech Red Raiders", "2026-01-11T01:00:00Z", "NCAAMB")
    proposals = []

    def propose(kevent, candidates):
        proposals.append([c["id"] for c in candidates])
        return {"odds_event_id": "ksu-ttu", "teams": {"Kansas": "Kansas State Wildcats",
                                                      "Texas": "Texas Tech Red Raiders"}}

    pairs, left, log = sl.pair_games([kansas], [lookalike], resolver=propose)
    assert pairs == [] and left == {"no_match": 1} and proposals == [["ksu-ttu"]]
    assert log[0]["accepted"] is False and "name" in log[0]["reason"]
    florida = kev("KXNCAAMBGAME-26JAN10UFUK", [("UF", "Florida"), ("UK", "Kentucky")], league="NCAAMB",
                  expected="2026-01-11T03:00:00Z")
    fau = ogame("fau", "Florida Atlantic Owls", "Kentucky Wildcats", "2026-01-11T01:00:00Z", "NCAAMB")
    pairs, left, _ = sl.pair_games([florida], [fau])                    # the true game is missing
    assert pairs == [] and left == {"no_match": 1}


def test_a_name_that_matches_two_teams_of_the_league_is_ambiguous():
    k = [kev("KXNCAAMBGAME-26JAN10SOUJXST", [("SOU", "Southern"), ("JXST", "Jackson St.")], league="NCAAMB",
             expected="2026-01-11T03:00:00Z")]
    miss = ogame("usm", "Southern Miss Golden Eagles", "Jackson State Tigers", "2026-01-11T01:00:00Z", "NCAAMB")
    elsewhere = ogame("sou", "Southern Jaguars", "Grambling Tigers", "2026-01-14T01:00:00Z", "NCAAMB")
    pairs, left, _ = sl.pair_games(k, [miss, elsewhere])
    assert pairs == [] and left == {"ambiguous": 1}


def test_a_resolver_proposal_must_pass_the_same_checks_as_code():
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
    assert pairs == [] and left == {"no_match": 1} and log[0]["accepted"] is False
    assert "date" in log[0]["reason"] and asked == [["right", "wrongday", "wrongteam"]]

    def wrong_team(kevent, candidates):
        return {"odds_event_id": "wrongteam", "teams": {"Golden St. W": "Sacramento Kings",
                                                         "Phx Suns": "Phoenix Suns"}}

    pairs, left, log = sl.pair_games(k, o, resolver=wrong_team)
    assert pairs == [] and left == {"no_match": 1} and "team" in log[0]["reason"]

    def plausible(kevent, candidates):                     # the right game, but "Phx" is not a name the rule knows
        return {"odds_event_id": "right", "teams": {"Golden St. W": "Golden State Warriors",
                                                     "Phx Suns": "Phoenix Suns"}}

    pairs, left, log = sl.pair_games(k, o, resolver=plausible)
    assert pairs == [] and left == {"no_match": 1} and "name" in log[0]["reason"]


def test_unmatched_games_are_left_out_and_counted_by_reason():
    k = [kev("KXNBAGAME-26JAN10BOSMIA", [("BOS", "Boston"), ("MIA", "Miami")]),
         kev("KXNBAGAME-26JAN10LALBKN", [("LAL", "Los Angeles"), ("BKN", "Brooklyn")])]
    o = [ogame("a", "Brooklyn Nets", "Los Angeles Lakers", "2026-01-11T00:30:00Z"),
         ogame("b", "Brooklyn Nets", "Los Angeles Clippers", "2026-01-11T00:30:00Z")]
    pairs, left, _ = sl.pair_games(k, o, resolver=None)
    assert pairs == [] and left == {"no_match": 1, "ambiguous": 1}
    pairs, left, log = sl.pair_games(k, o, resolver=lambda ke, c: (_ for _ in ()).throw(RuntimeError("down")))
    assert left == {"no_match": 1, "ambiguous": 1} and [r["reason"] for r in log] == ["resolver error"] * 2


# --- generated slates, labelled independently by the generator

NBA_POOL = [("Boston", "Boston Celtics"), ("Miami", "Miami Heat"), ("Los Angeles L", "Los Angeles Lakers", "la"),
            ("Los Angeles C", "Los Angeles Clippers", "la"), ("New York", "New York Knicks"),
            ("Brooklyn", "Brooklyn Nets"), ("Golden State", "Golden State Warriors"), ("Phoenix", "Phoenix Suns"),
            ("Oklahoma City", "Oklahoma City Thunder"), ("Portland", "Portland Trail Blazers"),
            ("Charlotte", "Charlotte Hornets"), ("Dallas", "Dallas Mavericks"), ("Houston", "Houston Rockets"),
            ("Utah", "Utah Jazz"), ("Denver", "Denver Nuggets"), ("Sacramento", "Sacramento Kings")]
NHL_POOL = [("Boston", "Boston Bruins"), ("New York R", "New York Rangers", "ny"),
            ("New York I", "New York Islanders", "ny"), ("New Jersey", "New Jersey Devils"),
            ("Los Angeles", "Los Angeles Kings"), ("Anaheim", "Anaheim Ducks"), ("Tampa Bay", "Tampa Bay Lightning"),
            ("Florida", "Florida Panthers"), ("St. Louis", "St Louis Blues"), ("Vegas", "Vegas Golden Knights"),
            ("Utah", "Utah Mammoth"), ("Montreal", "Montréal Canadiens"), ("Columbus", "Columbus Blue Jackets"),
            ("Winnipeg", "Winnipeg Jets")]
NFL_POOL = [("New York G", "New York Giants", "ny"), ("New York J", "New York Jets", "ny"),
            ("Los Angeles R", "Los Angeles Rams", "la"), ("Los Angeles C", "Los Angeles Chargers", "la"),
            ("Kansas City", "Kansas City Chiefs"), ("Tampa Bay", "Tampa Bay Buccaneers"),
            ("Green Bay", "Green Bay Packers"), ("New England", "New England Patriots"),
            ("New Orleans", "New Orleans Saints"), ("Las Vegas", "Las Vegas Raiders"),
            ("Jacksonville", "Jacksonville Jaguars"), ("Carolina", "Carolina Panthers"),
            ("Arizona", "Arizona Cardinals")]
NCAA_POOL = [("Kansas", "Kansas Jayhawks", "kansas"), ("Kansas St.", "Kansas State Wildcats", "kansas"),
             ("Texas", "Texas Longhorns", "texas"), ("Texas Tech", "Texas Tech Red Raiders", "texas"),
             ("Texas A&M", "Texas A&M Aggies", "texas"), ("Texas Southern", "Texas Southern Tigers", "texas"),
             ("Texas A&M-Corpus Christi", "Texas A&M-Corpus Christi Islanders", "texas"),
             ("Florida", "Florida Gators", "florida"), ("Florida St.", "Florida State Seminoles", "florida"),
             ("Florida Atlantic", "Florida Atlantic Owls", "florida"),
             ("Florida Gulf Coast", "Florida Gulf Coast Eagles", "florida"),
             ("Michigan", "Michigan Wolverines", "michigan"), ("Michigan St.", "Michigan State Spartans", "michigan"),
             ("Central Michigan", "Central Michigan Chippewas", "michigan"),
             ("Western Michigan", "Western Michigan Broncos", "michigan"),
             ("Washington", "Washington Huskies", "washington"),
             ("Washington St.", "Washington State Cougars", "washington"),
             ("Penn St.", "Penn State Nittany Lions", "penn"), ("Penn", "Penn Quakers", "penn"),
             ("Miami", "Miami Hurricanes", "miami"), ("Miami (OH)", "Miami (OH) RedHawks", "miami"),
             ("Georgia", "Georgia Bulldogs", "georgia"), ("Georgia Tech", "Georgia Tech Yellow Jackets", "georgia"),
             ("Georgia St.", "Georgia State Panthers", "georgia"),
             ("Georgia Southern", "Georgia Southern Eagles", "georgia"),
             ("Utah", "Utah Utes", "utah"), ("Utah St.", "Utah State Aggies", "utah"),
             ("Utah Valley", "Utah Valley Wolverines", "utah"),
             ("Kentucky", "Kentucky Wildcats", "kentucky"), ("Eastern Kentucky", "Eastern Kentucky Colonels", "kentucky"),
             ("Western Kentucky", "Western Kentucky Hilltoppers", "kentucky"),
             ("North Carolina", "North Carolina Tar Heels", "nc"), ("NC State", "NC State Wolfpack", "nc"),
             ("North Carolina A&T", "North Carolina A&T Aggies", "nc"),
             ("North Carolina Central", "North Carolina Central Eagles", "nc"),
             ("Virginia", "Virginia Cavaliers", "virginia"), ("Virginia Tech", "Virginia Tech Hokies", "virginia"),
             ("West Virginia", "West Virginia Mountaineers", "virginia"),
             ("St. Mary's", "Saint Mary's Gaels", "marys"), ("Mount St. Mary's", "Mount St. Mary's Mountaineers", "marys"),
             ("Southern", "Southern Jaguars", "southern", "ambiguous"),
             ("Southern Miss", "Southern Miss Golden Eagles", "southern"),
             ("Southern Utah", "Southern Utah Thunderbirds", "southern"),
             ("Boston College", "Boston College Eagles", "boston"), ("Boston U", "Boston University Terriers", "boston"),
             ("Ohio", "Ohio Bobcats", "ohio"), ("Ohio St.", "Ohio State Buckeyes", "ohio"),
             ("Iowa", "Iowa Hawkeyes", "iowa"), ("Iowa St.", "Iowa State Cyclones", "iowa")]
POOLS = {"NBA": NBA_POOL, "NHL": NHL_POOL, "NFL": NFL_POOL, "NCAAMB": NCAA_POOL}


def team(entry):
    return {"kalshi": entry[0], "full": entry[1], "family": entry[2] if len(entry) > 2 else None,
            "ambiguous": len(entry) > 3}


def make_slate(rng, n):
    """One league-day with its true games, then the traps: a missing true game beside a lookalike, a doubleheader,
    the same teams the next day, a copy of a game in another league, a game missing on either side. Every event and
    game carries the generator's own label (`truth`)."""
    league = rng.choice(sorted(POOLS))
    pool = [team(e) for e in POOLS[league]]
    day = date(2026, 1, 5) + timedelta(days=n % 50)
    base = datetime(day.year, day.month, day.day, 16, tzinfo=UTC)
    m = rng.randint(2, min(6, len(pool) // 2 - 1))
    picks = rng.sample(pool, 2 * m)
    kevents, games, truth_k, truth_g, traps = [], [], {}, {}, []

    def add(gid, home, away, start, d, kalshi=True, odds=True, odds_league=None):
        if kalshi:
            e = {"event": "KX%sGAME-S%dX%s" % (league, n, gid), "league": league, "day": d,
                 "expected": iso(start + timedelta(hours=rng.choice([2.5, 3, 3.5]))),
                 "teams": [{"code": "A", "name": away["kalshi"], "ticker": "S%d%s-A" % (n, gid)},
                           {"code": "H", "name": home["kalshi"], "ticker": "S%d%s-H" % (n, gid)}]}
            rng.shuffle(e["teams"])
            kevents.append(e)
            truth_k[e["event"]] = gid
        if odds:
            g = {"id": "s%d-%s" % (n, gid), "league": odds_league or league, "home": home["full"],
                 "away": away["full"], "commence": iso(start), "day": sl.eastern_date(iso(start))}
            games.append(g)
            truth_g[g["id"]] = gid if odds_league is None else "other-league-" + gid

    true = []
    for i in range(m):
        start = base + timedelta(minutes=30 * rng.randrange(0, 7 if i == 0 else 18))
        true.append((picks[2 * i], picks[2 * i + 1], start))
    used_names = {t["full"] for t in picks}
    for i, (h, a, start) in enumerate(true):
        gid = "g%d" % i
        roll = rng.random()
        if roll < 0.30:                                    # the true game missing, a lookalike in its slot
            look = [t for t in pool if t["family"] and t["family"] in (h["family"], a["family"])
                    and t["full"] not in used_names]
            if look:
                lk = rng.choice(look)
                lh, la_ = (lk, a) if lk["family"] == h["family"] else (h, lk)
                if rng.random() < 0.3:                     # both teams replaced when both have lookalikes
                    other = [t for t in pool if t["family"] and t["family"] == (la_ if lh is lk else lh)["family"]
                             and t["full"] not in used_names | {lk["full"]}]
                    if other:
                        lh, la_ = (lk, rng.choice(other)) if lh is lk else (rng.choice(other), lk)
                add(gid, h, a, start, day, odds=False)
                add("look%d" % i, lh, la_, start, day, kalshi=False)
                traps.append("lookalike")
                continue
        if roll < 0.40:
            add(gid, h, a, start, day, odds=False)
            traps.append("odds_missing")
            continue
        if roll < 0.48:
            add(gid, h, a, start, day, kalshi=False)
            traps.append("kalshi_missing")
            continue
        add(gid, h, a, start, day)
        if i == 0 and rng.random() < 0.4:                  # a doubleheader: the same teams 4 to 6 hours later
            add("dh%d" % i, h, a, start + timedelta(hours=rng.choice([4, 4.5, 5, 6])), day)
            traps.append("doubleheader")
        if rng.random() < 0.25:                            # the same two teams the next day
            nxt = start + timedelta(days=1, minutes=30 * rng.randrange(-4, 5))
            add("next%d" % i, h, a, nxt, sl.eastern_date(iso(nxt)))
            traps.append("next_day")
        if rng.random() < 0.25:                            # the same names in another league at the same time
            add("x%d" % i, h, a, start, day, kalshi=False, odds_league=rng.choice([x for x in POOLS if x != league]))
            traps.append("other_league")
    rest = rng.sample(pool, len(pool))
    for j in range(0, len(rest), 2):                       # every team of the league also plays three days earlier
        add("bg%d" % j, rest[j], rest[(j + 1) % len(rest)], base - timedelta(days=3) + timedelta(hours=j % 6),
            day - timedelta(3), kalshi=False)
    clean = {e["event"] for e in kevents if truth_k[e["event"]] in {truth_g[g["id"]] for g in games}
             and not any(t["ambiguous"] for t in pool if t["kalshi"] in [x["name"] for x in e["teams"]])}
    return kevents, games, truth_k, truth_g, clean, traps


def test_generated_slates_pair_no_wrong_game_and_count_every_refusal():
    rng = random.Random(20261113)
    totals = {"slates": 0, "events": 0, "paired": 0, "wrong": 0, "clean_unpaired": 0, "refused": {}}
    seen_traps: dict[str, int] = {}

    def adversary(kevent, candidates):                     # proposes some candidate, named as Kalshi names them
        g = rng.choice(candidates)
        names = [t["name"] for t in kevent["teams"]]
        return {"odds_event_id": g["id"], "teams": {names[0]: g["home"], names[1]: g["away"]}}

    for n in range(320):
        kevents, games, truth_k, truth_g, clean, traps = make_slate(rng, n)
        for t in traps:
            seen_traps[t] = seen_traps.get(t, 0) + 1
        for resolver in (None, adversary):
            pairs, left, log = sl.pair_games(kevents, games, resolver=resolver)
            wrong = [p for p in pairs if truth_k[p["kalshi_event"]] != truth_g[p["odds_event_id"]]]
            assert not wrong, (n, wrong)
            assert sum(left.values()) + len(pairs) == len(kevents)
            assert set(left) <= {"no_match", "ambiguous"}
            assert all(not r["accepted"] for r in log)
            paired = {p["kalshi_event"] for p in pairs}
            assert clean <= paired, (n, clean - paired)
            if resolver is None:
                totals["events"] += len(kevents)
                totals["paired"] += len(pairs)
                for r, c in left.items():
                    totals["refused"][r] = totals["refused"].get(r, 0) + c
        totals["slates"] += 1
    assert totals["slates"] >= 300
    assert all(seen_traps.get(t, 0) >= 30 for t in ("lookalike", "odds_missing", "kalshi_missing", "doubleheader",
                                                    "next_day", "other_league")), seen_traps
    assert totals["refused"]["no_match"] >= 200 and totals["paired"] >= 600, totals


def test_resolver_proposals_are_logged_and_never_override_the_checks(tmp_path, capsys):
    store_dir, _ = build_world(tmp_path)
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    e = "KXNBAGAME-26JAN11DENUTA"
    kstore.put_market(kmarket(e, "DEN", "Dnvr", "no", expected="2026-01-12T05:00:00Z"), "historical", "KXNBAGAME")
    kstore.put_market(kmarket(e, "UTA", "Utah", "yes", expected="2026-01-12T05:00:00Z"), "historical", "KXNBAGAME")
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
    assert json.loads(capsys.readouterr().out) == []
    store = sl.LinesStore(store_dir / "lines.sqlite")
    log = [dict(r) for r in store.db.execute("SELECT * FROM resolver_log")]
    assert len(log) == 1 and log[0]["accepted"] == 0 and "name" in log[0]["reason"]
    assert store.latest_unpaired()[e] == "no_match"


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


# ---------------------------------------------------------------- the numbers

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


# ---------------------------------------------------------------- evaluation

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


# --- what is known at a game's moment (outcome 7)

NUMBERS = ("kalshi_event", "odds_event_id", "home_team", "away_team", "moment", "books", "home", "away", "reason")


def numbers(store_dir):
    games, _ = sl.collect_games(sl.LinesStore(store_dir / "lines.sqlite"), kh.HistoryStore(store_dir / "kalshi.sqlite"),
                                *PERIODS["dev"], leagues=["NBA"])
    return {g["kalshi_event"]: {k: g[k] for k in NUMBERS} for g in games}


def test_no_look_ahead_everything_after_a_moment_changes_nothing_but_start_times_come_from_16_utc(tmp_path):
    plain_dir, _ = build_world(tmp_path / "plain")
    plain = numbers(plain_dir)
    assert set(plain) == {EARLY, EVENING, LATE} and all(g["reason"] is None for g in plain.values())
    # the early game's moment (14:25 UTC) is before the 16:00 UTC snapshot that gave its start time
    assert plain[EARLY]["moment"] == "2026-01-10T14:25:00Z"
    assert plain[EARLY]["home"] == {"bid": 0.55, "ask": 0.57} and plain[EARLY]["away"] == {"bid": 0.42, "ask": 0.44}
    assert plain[EVENING]["moment"] == "2026-01-10T23:55:00Z"
    assert plain[EVENING]["home"] == {"bid": 0.50, "ask": 0.52} and plain[EVENING]["away"] == {"bid": 0.47, "ask": 0.49}
    for i, cut in enumerate(("2026-01-10T14:25:00Z", "2026-01-10T23:55:00Z", "2026-01-11T02:25:00Z")):
        altered_dir, _ = build_world(tmp_path / ("cut%d" % i), alter_after=cut)
        lb = sl.LinesStore(altered_dir / "lines.sqlite")              # a later snapshot that is never a wave's
        later = snap("2026-01-11T00:25:00Z", [ev("o1", "Miami Heat", "Boston Celtics", "2026-01-11T00:30:00Z",
                                                 ALTERED_BOOKS)])
        lb.put_snapshot("NBA", "2026-01-11T00:25:00Z", "extra", date(2026, 1, 10), later)
        got = numbers(altered_dir)
        for event, g in plain.items():
            if g["moment"] <= cut:
                assert got[event] == g, (cut, event)
            else:
                assert got[event]["books"] != g["books"], (cut, event)   # the change was real for later games
        ea = sl.evaluate_games([x for x in sl.collect_games(lb, kh.HistoryStore(altered_dir / "kalshi.sqlite"),
                                                            *PERIODS["dev"], leagues=["NBA"])[0]
                                if x["moment"] <= cut], seed=3, reps=50)
        assert ea["median"]["0.02"]["games_counted"] == i + 1


def test_a_start_time_changed_in_the_16_utc_snapshot_changes_only_wave_membership(tmp_path):
    plain = numbers(build_world(tmp_path / "plain")[0])
    moved_dir, t = build_world(tmp_path / "moved", start_change=("o2", "2026-01-11T01:00:00Z"))
    assert [p["date"] for _, p in t.odds_calls()] == ["2026-01-10T16:00:00Z", "2026-01-10T14:30:00Z",
                                                      "2026-01-11T00:00:00Z"]   # o2 joins the evening wave
    moved = numbers(moved_dir)
    assert moved[EARLY] == plain[EARLY] and moved[EVENING] == plain[EVENING]
    assert plain[LATE]["moment"] == "2026-01-11T02:25:00Z"
    late = moved[LATE]
    assert late["moment"] == "2026-01-10T23:55:00Z"                # the evening wave's snapshot, its own numbers
    assert late["books"] == {b: v for b, v in NBA_BOOKS_2.items() if b in sl.BOOKS}
    assert late["home"] == {"bid": 0.55, "ask": 0.57} and late["away"] == {"bid": 0.43, "ask": 0.45}
    assert {k: v for k, v in late.items() if k not in ("moment", "home", "away")} == \
        {k: v for k, v in plain[LATE].items() if k not in ("moment", "home", "away")}


def test_a_wave_snapshot_reported_more_than_ten_minutes_early_is_left_out_and_counted(tmp_path, capsys):
    stale_dir, _ = build_world(tmp_path / "stale", wave1_stamp="2026-01-10T23:49:00Z")
    got = numbers(stale_dir)
    assert got[EVENING]["reason"] == "stale_snapshot" and got[EVENING]["moment"] is None
    assert got[EARLY]["reason"] is None and got[LATE]["reason"] is None
    capsys.readouterr()
    sl.main(["--store", str(stale_dir), "evaluate", "--period", "dev"], clock=Clock(), periods=PERIODS)
    out = json.loads(capsys.readouterr().out)
    assert out["results"]["median"]["0.02"]["left_out"] == {"stale_snapshot": 1}
    edge_dir, _ = build_world(tmp_path / "edge", wave1_stamp="2026-01-10T23:50:00Z")   # exactly 10 minutes: used
    assert numbers(edge_dir)[EVENING]["reason"] is None


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
    assert out["period"] == "dev" and out["pairing"]["paired"] == 3
    assert set(out["results"]) == {"median", "pinnacle"}
    assert set(out["results"]["median"]) == {"0.02", "0.03", "0.05"}
    assert out["results"]["median"]["0.02"]["games_counted"] == 3
    assert out["results"]["pinnacle"]["0.02"]["games_counted"] == 3
    for key in ("brier", "trading", "by_league", "left_out"):
        assert key in out["results"]["median"]["0.03"]


# --- the test period (outcome 6)

T2 = {"dev": (date(2026, 1, 1), date(2026, 1, 9)), "test": (date(2026, 1, 10), date(2026, 1, 12))}


def scoring(store_dir):
    return [json.loads(line) for line in (store_dir / "scoring.log").read_text().splitlines()]


def test_the_test_period_is_scored_once_with_everything_stored_and_a_log_beside_the_store(tmp_path, capsys):
    store_dir = setup_world(tmp_path, periods=T2, candles=False)
    t = OddsTransport(world_routes())
    args = ["--store", str(store_dir)]

    def run(*more, **kw):
        return sl.main(args + list(more), clock=Clock(), periods=T2, **kw)

    with pytest.raises(sl.Refused, match="choice"):
        run("evaluate", "--period", "test")
    with pytest.raises(sl.Refused, match="choice"):
        run("fetch-odds", "--period", "test", "--cap", "9000", odds_client=odds_client(t))
    with pytest.raises(SystemExit):
        run("evaluate", "--period", "test", "--margin", "0.03")
    with pytest.raises(ValueError):
        run("choose", "--margin", "0.04", "--version", "median")
    assert run("choose", "--margin", "0.03", "--version", "pinnacle") == 0
    log = scoring(store_dir)
    assert [x["kind"] for x in log] == ["choice"] and log[0]["choice"] == {"margin": 0.03, "version": "pinnacle"}
    assert set(log[0]["sha256"]) == {"lines.sqlite", "kalshi.sqlite", "credits.sqlite"}
    assert all(len(h) == 64 for h in log[0]["sha256"].values())
    with pytest.raises(sl.Refused, match="already"):
        run("choose", "--margin", "0.02", "--version", "median")
    with pytest.raises(sl.Refused, match="snapshot"):                     # nothing bought yet
        run("evaluate", "--period", "test")
    assert run("fetch-odds", "--period", "test", "--cap", "9000", odds_client=odds_client(t)) == 0
    with pytest.raises(sl.Refused, match="pairing"):
        run("evaluate", "--period", "test")
    run("pair", "--period", "test", "--no-resolver")
    with pytest.raises(sl.Refused, match="candles") as err:
        run("evaluate", "--period", "test")
    assert "KXNBAGAME-26JAN10BOSMIA-MIA" in str(err.value)
    put_candles(kh.HistoryStore(store_dir / "kalshi.sqlite"), kalshi_world()[1])
    capsys.readouterr()
    assert run("evaluate", "--period", "test") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["choice"] == {"margin": 0.03, "version": "pinnacle"} and set(out["results"]) == {"pinnacle"}
    assert set(out["results"]["pinnacle"]) == {"0.03"} and out["results"]["pinnacle"]["0.03"]["games_counted"] == 3
    log = scoring(store_dir)
    assert [x["kind"] for x in log] == ["choice", "test_result"]
    assert log[1]["result"]["results"] == out["results"]
    with pytest.raises(sl.Refused, match="already"):
        run("evaluate", "--period", "test")
    store = sl.LinesStore(store_dir / "lines.sqlite")
    assert store.result("test")["choice"] == {"margin": 0.03, "version": "pinnacle"}
    store.db.execute("DELETE FROM results")                                # the store says nothing was scored ...
    store.db.execute("DELETE FROM choice")
    store.db.commit()
    with pytest.raises(sl.Refused, match="scoring.log"):                  # ... the log beside it still refuses
        run("evaluate", "--period", "test")
    with pytest.raises(sl.Refused, match="scoring.log"):
        run("choose", "--margin", "0.02", "--version", "median")


def test_kalshi_candles_are_fetched_only_around_each_moment(tmp_path):
    store_dir, _ = build_world(tmp_path)
    kstore = kh.HistoryStore(store_dir / "kalshi.sqlite")
    kstore.db.execute("DELETE FROM windows")
    kstore.db.execute("DELETE FROM candle_asks")
    kstore.db.commit()
    routes = {"/historical/cutoff": {"market_settled_ts": "2026-09-13T00:00:00Z"}}
    for event, codes in ((EARLY, ("ORL", "CHI")), (EVENING, ("MIA", "BOS")), (LATE, ("NYK", "LAL"))):
        for c in codes:
            routes["/historical/markets/%s-%s/candlesticks" % (event, c)] = {"candlesticks": [kcandle(60, 0.5, 0.52)]}
    client, t = kalshi_client(routes)
    sl.main(["--store", str(store_dir), "kalshi-candles", "--period", "dev", "--leagues", "NBA"],
            kalshi_client=client, clock=Clock(), periods=PERIODS)
    asked = [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)) for u in t.urls if "candlesticks" in u]
    assert len(asked) == 6
    ends = sorted({int(q["end_ts"]) for q in asked})
    assert ends == [M0 // 60 * 60, M1 // 60 * 60, M2 // 60 * 60]
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
