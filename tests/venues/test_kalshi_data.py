import inspect
import json
import urllib.parse

import pytest

from valedia_trader.venues import kalshi_data as kd


class FakeClock:
    """A clock that only moves when the code under test sleeps."""
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeTransport:
    """Answers by path and query; records every URL asked. A value can be an exception to raise."""
    def __init__(self, routes):
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        path = parts.path.split("/trade-api/v2", 1)[1]
        query = dict(urllib.parse.parse_qsl(parts.query))
        for (p, match), answer in self.routes.items():
            if p == path and all(query.get(k) == v for k, v in match):
                if callable(answer):
                    answer = answer()
                if isinstance(answer, Exception):
                    raise answer
                return json.dumps(answer).encode()
        raise kd.HttpError(404, url)


def client(routes, clock=None):
    clock = clock or FakeClock()
    return kd.KalshiData(fetch=FakeTransport(routes), sleep=clock.sleep, clock=clock), clock


def market(ticker, close, **extra):
    row = {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "title": "t", "yes_sub_title": "s",
           "strike_type": "greater", "floor_strike": 66, "cap_strike": None, "open_time": "2025-11-04T15:00:00Z",
           "close_time": close, "settlement_ts": "2025-11-06T15:00:00Z", "result": "yes", "expiration_value": "68",
           "volume_fp": "12.00", "status": "finalized"}
    row.update(extra)
    return row


def test_settled_markets_merges_both_tiers_pages_by_cursor_and_drops_duplicates():
    hist1 = {"markets": [market("KXHIGHNY-26SEP11-T86", "2026-09-12T05:00:00Z"),
                         market("KXHIGHNY-26SEP10-T80", "2026-09-11T05:00:00Z")], "cursor": "c1"}
    hist2 = {"markets": [market("KXHIGHNY-26SEP12-T70", "2026-09-13T05:00:00Z")], "cursor": ""}
    live = {"markets": [market("KXHIGHNY-26SEP12-T70", "2026-09-13T05:00:00Z", volume_fp="99.00"),
                        market("KXHIGHNY-26NOV11-T66", "2026-11-12T06:00:00Z", custom_strike={"City": "NYC"})],
            "cursor": None}
    c, _ = client({("/historical/markets", (("series_ticker", "KXHIGHNY"), ("cursor", "c1"))): hist2,
                   ("/historical/markets", (("series_ticker", "KXHIGHNY"),)): hist1,
                   ("/markets", (("series_ticker", "KXHIGHNY"), ("status", "settled"))): live})
    got = c.settled_markets("KXHIGHNY")
    tickers = [m["ticker"] for m in got]
    assert tickers == ["KXHIGHNY-26SEP10-T80", "KXHIGHNY-26SEP11-T86", "KXHIGHNY-26SEP12-T70", "KXHIGHNY-26NOV11-T66"]
    assert len(set(tickers)) == 4                                      # the 7-12 Sep overlap appears once
    by = {m["ticker"]: m for m in got}
    assert by["KXHIGHNY-26SEP12-T70"]["tier"] == "historical" and by["KXHIGHNY-26SEP12-T70"]["volume"] == 12.0
    assert by["KXHIGHNY-26NOV11-T66"]["tier"] == "live"
    assert {frozenset(m) for m in got} == {frozenset(got[0])}          # one key shape whatever the tier
    assert all(isinstance(m["volume"], float) for m in got)
    urls = c.fetch.urls
    assert sum("/historical/markets" in u for u in urls) == 2 and all("limit=1000" in u for u in urls)


HIST_CANDLE = {"end_period_ts": 1762318800, "open_interest": "2239.00", "volume": "101.00",
               "price": {"close": "0.5000", "high": "0.5300", "low": "0.5000", "mean": "0.5144", "open": "0.5200",
                         "previous": "0.5100"},
               "yes_ask": {"close": "0.5300", "high": "0.5500", "low": "0.5100", "open": "0.5200"},
               "yes_bid": {"close": "0.5100", "high": "0.5200", "low": "0.5000", "open": "0.5100"}}
LIVE_CANDLE = {"end_period_ts": 1794376800, "open_interest_fp": "5467.84", "volume_fp": "539.59",
               "price": {"close_dollars": None, "high_dollars": None, "low_dollars": None, "mean_dollars": None,
                         "open_dollars": None, "previous_dollars": "0.1800"},
               "yes_ask": {"close_dollars": "0.1200", "high_dollars": "0.1700", "low_dollars": "0.1100", "open_dollars": "0.1600"},
               "yes_bid": {"close_dollars": "0.1000", "high_dollars": "0.1400", "low_dollars": "0.0900", "open_dollars": "0.1400"}}


def test_candles_from_either_tier_come_back_in_one_shape():
    a = kd.normalize_candle(HIST_CANDLE)
    b = kd.normalize_candle(LIVE_CANDLE)
    assert a == {"end_ts": 1762318800, "volume": 101.0, "open_interest": 2239.0,
                 "price": {"open": 0.52, "high": 0.53, "low": 0.5, "close": 0.5, "mean": 0.5144, "previous": 0.51},
                 "yes_bid": {"open": 0.51, "high": 0.52, "low": 0.5, "close": 0.51},
                 "yes_ask": {"open": 0.52, "high": 0.55, "low": 0.51, "close": 0.53}}
    assert set(a) == set(b) and all(set(a[k]) == set(b[k]) for k in ("price", "yes_bid", "yes_ask"))
    assert b["yes_bid"]["close"] == 0.10 and b["yes_ask"]["close"] == 0.12 and b["price"]["close"] is None
    assert b["volume"] == 539.59 and b["open_interest"] == 5467.84


def test_candles_try_the_hinted_tier_then_fall_back_on_404_or_empty():
    t = "KXHIGHNY-26SEP12-T70"
    hist = ("/historical/markets/%s/candlesticks" % t, (("period_interval", "60"),))
    live = ("/series/KXHIGHNY/markets/%s/candlesticks" % t, (("period_interval", "60"),))
    c, _ = client({live: {"candlesticks": [LIVE_CANDLE]}})              # historical answers 404 (no route)
    got = c.candles(t, 1794300000, 1794463200, tier_hint="historical")
    assert [x["end_ts"] for x in got] == [1794376800]
    assert "/historical/" in c.fetch.urls[0] and "/series/KXHIGHNY/" in c.fetch.urls[1]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(c.fetch.urls[0]).query))
    assert q == {"start_ts": "1794300000", "end_ts": "1794463200", "period_interval": "60"}

    c, _ = client({live: {"candlesticks": []}, hist: {"candlesticks": [HIST_CANDLE]}})
    got = c.candles(t, 1, 2, tier_hint="live")                          # live empty -> historical
    assert [x["end_ts"] for x in got] == [1762318800] and len(c.fetch.urls) == 2

    c, _ = client({hist: {"candlesticks": [HIST_CANDLE]}})
    assert len(c.candles(t, 1, 2, tier_hint="historical")) == 1 and len(c.fetch.urls) == 1   # right tier: one call


def test_a_429_is_retried_with_back_off_and_succeeds():
    answers = iter([kd.HttpError(429, "u"), kd.HttpError(503, "u"), {"markets": [], "cursor": ""}])
    c, clock = client({("/markets", ()): lambda: next(answers)})
    assert c.get_json("/markets", {"series_ticker": "X"}) == {"markets": [], "cursor": ""}
    assert len(c.fetch.urls) == 3
    assert 1.0 in clock.sleeps and 2.0 in clock.sleeps                    # back-off 1 s then 2 s


def test_after_five_refusals_it_gives_up_with_a_clear_error():
    c, _ = client({("/markets", ()): kd.HttpError(429, "u")})
    with pytest.raises(kd.KalshiDataError, match="5 tries.*429"):
        c.get_json("/markets")
    assert len(c.fetch.urls) == 5


def test_a_404_is_not_retried():
    c, _ = client({})
    with pytest.raises(kd.HttpError) as e:
        c.get_json("/markets/NOPE")
    assert e.value.status == 404 and len(c.fetch.urls) == 1


def test_never_more_than_three_requests_a_second():
    c, clock = client({("/markets", ()): {"markets": [], "cursor": ""}})
    stamps = []
    real = c.fetch
    c.fetch = lambda url: (stamps.append(clock.t), real(url))[1]
    for _ in range(7):
        c.get_json("/markets")
    assert len(stamps) == 7
    for i in range(len(stamps) - 3):
        assert stamps[i + 3] - stamps[i] >= 1.0                           # any 4 consecutive requests span a second


def test_the_only_way_out_is_a_get(monkeypatch):
    seen = []

    class Resp:
        status = 200
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return Resp()

    monkeypatch.setattr(kd.urllib.request, "urlopen", fake_urlopen)
    assert kd.urllib_get("https://api.elections.kalshi.com/trade-api/v2/markets") == b"{}"
    assert seen[0].get_method() == "GET" and seen[0].data is None
    src = inspect.getsource(kd)
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert '"%s"' % verb not in src and "'%s'" % verb not in src
    assert "data=" not in src
