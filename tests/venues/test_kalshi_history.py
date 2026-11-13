import json
import urllib.error
import urllib.parse
from datetime import date, datetime, timezone

import pytest

from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh

UTC = timezone.utc
CUTOFF = {"market_settled_ts": "2026-09-13T00:00:00Z", "trades_created_ts": "2026-09-13T00:00:00Z",
          "orders_updated_ts": "2026-10-29T00:00:00Z"}


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeTransport:
    """Answers by path and query; records each URL with the fake time it was asked at."""
    def __init__(self, routes, clock=None):
        self.routes, self.clock = routes, clock
        self.urls: list[str] = []
        self.times: list[float] = []

    def __call__(self, url):
        self.urls.append(url)
        self.times.append(self.clock() if self.clock else 0.0)
        parts = urllib.parse.urlsplit(url)
        path = parts.path.split("/trade-api/v2", 1)[1]
        query = dict(urllib.parse.parse_qsl(parts.query))
        for (p, match), answer in self.routes.items():
            if p == path and all(query.get(k) == v for k, v in match):
                if isinstance(answer, list):
                    answer = answer.pop(0)
                if callable(answer):
                    answer = answer()
                if isinstance(answer, Exception):
                    raise answer
                return json.dumps(answer).encode()
        raise kd.HttpError(404, url)


def make(routes, **kw):
    clock = FakeClock()
    t = FakeTransport(routes, clock)
    return kh.HistoryClient(fetch=t, sleep=clock.sleep, clock=clock, **kw), t, clock


def mkt(ticker, settled, result="yes", sub="Toronto", **extra):
    row = {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "title": sub + " wins", "yes_sub_title": sub,
           "open_time": "2026-01-01T00:00:00Z", "close_time": settled, "settlement_ts": settled, "result": result,
           "status": "finalized", "volume_fp": "100.00"}
    row.update(extra)
    return row


def candle(end_ts, bid, ask, live=True):
    if live:
        return {"end_period_ts": end_ts, "yes_bid": {"close_dollars": "%.4f" % bid},
                "yes_ask": {"close_dollars": "%.4f" % ask}, "price": {}, "volume_fp": "1.00"}
    return {"end_period_ts": end_ts, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}, "price": {}, "volume": 1}


def test_event_date_is_read_from_the_ticker_as_a_us_eastern_date():
    assert kh.event_date("KXNBAGAME-26NOV12TORPHI") == date(2026, 11, 12)
    assert kh.event_date("KXNBAGAME-26NOV12TORPHI-TOR") == date(2026, 11, 12)
    assert kh.event_date("KXNFLGAME-26JAN05BUFMIA") == date(2026, 1, 5)
    assert kh.event_date("nonsense") is None
    assert kh.team_code("KXNBAGAME-26NOV12TORPHI-TOR") == "TOR"
    assert set(kh.SERIES.values()) == {"KXNFLGAME", "KXNBAGAME", "KXNHLGAME", "KXNCAAFGAME", "KXNCAAMBGAME"}


def test_requests_are_paced_to_at_most_five_a_second():
    c, t, clock = make({("/historical/cutoff", ()): CUTOFF})
    for _ in range(11):
        c.get_json("/historical/cutoff")
    gaps = [b - a for a, b in zip(t.times, t.times[1:])]
    assert min(gaps) >= 0.2 - 1e-9
    assert c.requests == 11
    for i in range(len(t.times) - 5):                          # never six starts inside one second
        assert t.times[i + 5] - t.times[i] >= 1.0 - 1e-9


def test_retry_with_a_wait_on_429_and_on_a_network_error_at_most_four_tries():
    answers = [kd.HttpError(429, "u", retry_after=3.0), urllib.error.URLError("down"), TimeoutError("slow"), CUTOFF]
    c, t, clock = make({("/historical/cutoff", ()): answers})
    assert c.get_json("/historical/cutoff")["market_settled_ts"] == CUTOFF["market_settled_ts"]
    assert len(t.urls) == 4
    waits = [s for s in clock.sleeps if s >= 0.5]               # the pacing sleeps are below 0.25 s
    assert waits[0] == 3.0 and waits[1:] == [2.0, 4.0]
    always = [ConnectionError("x")] * 10
    c, t, _ = make({("/historical/cutoff", ()): always})
    with pytest.raises(kh.KalshiHistoryError, match="4 tries"):
        c.get_json("/historical/cutoff")
    assert len(t.urls) == 4
    c, t, _ = make({})                                           # a 404 is not retried
    with pytest.raises(kd.HttpError):
        c.get_json("/historical/cutoff")
    assert len(t.urls) == 1


def test_only_plain_gets_without_any_key_and_a_request_budget():
    c, t, _ = make({("/historical/cutoff", ()): CUTOFF}, max_requests=2)
    c.get_json("/historical/cutoff")
    c.get_json("/historical/cutoff")
    with pytest.raises(kh.KalshiHistoryError, match="budget"):
        c.get_json("/historical/cutoff")
    assert len(t.urls) == 2
    assert all(u.startswith(kd.BASE_URL) and "key" not in u.lower() for u in t.urls)
    assert not hasattr(c, "post") and not hasattr(c, "put") and not hasattr(c, "delete")


def test_cutoff_routing_for_the_settled_lists(tmp_path):
    hist = {"markets": [mkt("KXNBAGAME-26JAN10TORPHI-TOR", "2026-01-11T03:00:00Z"),
                        mkt("KXNBAGAME-26JAN10TORPHI-PHI", "2026-01-11T03:00:00Z", result="no", sub="Philadelphia"),
                        mkt("KXNBAGAME-25DEC01TORPHI-TOR", "2025-12-02T03:00:00Z")], "cursor": ""}
    live = {"markets": [mkt("KXNBAGAME-26OCT01TORPHI-TOR", "2026-10-02T03:00:00Z")], "cursor": None}
    routes = {("/historical/cutoff", ()): CUTOFF,
              ("/historical/markets", (("series_ticker", "KXNBAGAME"),)): hist,
              ("/markets", (("series_ticker", "KXNBAGAME"), ("status", "settled"))): live}
    store = kh.HistoryStore(tmp_path / "k.sqlite")

    c, t, _ = make(dict(routes))
    out = kh.fetch_settled(c, store, "KXNBAGAME", date(2026, 1, 5), date(2026, 3, 1))
    paths = [urllib.parse.urlsplit(u).path for u in t.urls]
    assert "/trade-api/v2/historical/markets" in paths and "/trade-api/v2/markets" not in paths
    assert out["stored"] == 2 and out["in_period"] == 2                     # the December row is outside

    c, t, _ = make(dict(routes))
    kh.fetch_settled(c, store, "KXNBAGAME", date(2026, 9, 20), date(2026, 11, 8))
    paths = [urllib.parse.urlsplit(u).path for u in t.urls]
    assert "/trade-api/v2/markets" in paths and "/trade-api/v2/historical/markets" not in paths

    c, t, _ = make(dict(routes))
    kh.fetch_settled(c, store, "KXNBAGAME", date(2026, 9, 1), date(2026, 9, 30))
    paths = [urllib.parse.urlsplit(u).path for u in t.urls]
    assert "/trade-api/v2/markets" in paths and "/trade-api/v2/historical/markets" in paths
    assert {m["tier"] for m in store.markets("KXNBAGAME")} == {"historical", "live"}


def test_candles_route_by_the_cutoff_and_fall_back_on_404(tmp_path):
    store = kh.HistoryStore(tmp_path / "k.sqlite")
    old = mkt("KXNBAGAME-26JAN10TORPHI-TOR", "2026-01-11T03:00:00Z")
    new = mkt("KXNBAGAME-26OCT01TORPHI-TOR", "2026-10-02T03:00:00Z")
    m = 1768086000                                                      # a moment on 2026-01-10
    routes = {("/historical/cutoff", ()): CUTOFF,
              ("/historical/markets/%s/candlesticks" % old["ticker"], (("period_interval", "1"),)):
                  {"candlesticks": [candle(m - 60, 0.40, 0.42, live=False), candle(m, 0.41, 0.43, live=False)]},
              ("/series/KXNBAGAME/markets/%s/candlesticks" % new["ticker"], (("period_interval", "1"),)):
                  {"candlesticks": [candle(1790000000, 0.5, 0.52)]}}
    c, t, _ = make(routes)
    cutoff = kh.cutoff_ts(c)
    got = kh.fetch_candles(c, store, old, [m + 30], cutoff)
    assert got == {"windows_fetched": 1, "windows_cached": 0, "candles_stored": 2}
    assert "/historical/markets/" in t.urls[-1]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(t.urls[-1]).query))
    assert q == {"start_ts": str(m - 3600), "end_ts": str(m), "period_interval": "1"}   # the minute and the hour before
    kh.fetch_candles(c, store, new, [1790000010], cutoff)
    assert "/series/KXNBAGAME/markets/" in t.urls[-1]
    rows = store.candles(old["ticker"])
    assert [r["end_ts"] for r in rows] == [m - 60, m] and rows[1]["yes_ask"]["close"] == 0.43

    moved = mkt("KXNBAGAME-26SEP20TORPHI-TOR", "2026-09-21T03:00:00Z")    # settled after the cutoff, now historical
    routes[("/historical/markets/%s/candlesticks" % moved["ticker"], (("period_interval", "1"),))] = \
        {"candlesticks": [candle(1789900000, 0.3, 0.31, live=False)]}
    kh.fetch_candles(c, store, moved, [1789900000], cutoff)
    assert "/series/KXNBAGAME/markets/" in t.urls[-2] and "/historical/markets/" in t.urls[-1]
    assert store.candles(moved["ticker"])[0]["yes_bid"]["close"] == 0.3


def test_only_the_needed_minutes_are_fetched_once(tmp_path):
    store = kh.HistoryStore(tmp_path / "k.sqlite")
    old = mkt("KXNBAGAME-26JAN10TORPHI-TOR", "2026-01-11T03:00:00Z")
    path = "/historical/markets/%s/candlesticks" % old["ticker"]
    c, t, _ = make({(path, ()): {"candlesticks": [candle(1768086000, 0.4, 0.42, live=False)]}})
    assert kh.windows([1768086030, 1768087000, 1768200000]) == [(1768082400, 1768086960), (1768196400, 1768200000)]
    kh.fetch_candles(c, store, old, [1768086030, 1768087000, 1768200000], kh.iso_ts(CUTOFF["market_settled_ts"]))
    assert len(t.urls) == 2
    again = kh.fetch_candles(c, store, old, [1768086030, 1768200000], kh.iso_ts(CUTOFF["market_settled_ts"]))
    assert len(t.urls) == 2 and again["windows_cached"] == 2 and again["windows_fetched"] == 0


def test_store_never_overwrites_a_fact_and_records_a_difference(tmp_path):
    store = kh.HistoryStore(tmp_path / "k.sqlite")
    row = mkt("KXNBAGAME-26JAN10TORPHI-TOR", "2026-01-11T03:00:00Z")
    assert store.put_market(row, "historical", "KXNBAGAME") == "new"
    assert store.put_market(dict(row), "historical", "KXNBAGAME") == "same"
    assert store.put_market(dict(row, result="no"), "live", "KXNBAGAME") == "different"
    kept = store.markets("KXNBAGAME")
    assert len(kept) == 1 and kept[0]["result"] == "yes" and kept[0]["tier"] == "historical"
    diffs = store.differences()
    assert len(diffs) == 1 and diffs[0]["kind"] == "market" and json.loads(diffs[0]["new"])["result"] == "no"
    store.put_candle("T", candle(100, 0.1, 0.2), "live")
    store.put_candle("T", candle(100, 0.1, 0.3), "live")
    assert store.candles("T")[0]["yes_ask"]["close"] == 0.2 and len(store.differences()) == 2


def test_a_listing_already_stored_is_not_fetched_again(tmp_path):
    hist = {"markets": [mkt("KXNBAGAME-26JAN10TORPHI-TOR", "2026-01-11T03:00:00Z")], "cursor": ""}
    routes = {("/historical/cutoff", ()): CUTOFF, ("/historical/markets", (("series_ticker", "KXNBAGAME"),)): hist}
    store = kh.HistoryStore(tmp_path / "k.sqlite")
    c, t, _ = make(routes)
    kh.fetch_settled(c, store, "KXNBAGAME", date(2026, 1, 5), date(2026, 3, 1))
    n = len(t.urls)
    again = kh.fetch_settled(c, store, "KXNBAGAME", date(2026, 1, 5), date(2026, 3, 1))
    assert len(t.urls) == n and again["cached"] is True
    assert kh.iso_ts("2026-09-13T00:00:00Z") == datetime(2026, 9, 13, tzinfo=UTC).timestamp()
