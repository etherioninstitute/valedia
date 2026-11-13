import numpy as np
import pandas as pd
import pytest

from valedia_trader.venues import odds_api as oa

EVENT = {
    "id": "evt1", "sport_key": "basketball_nba", "commence_time": "2026-11-11T00:10:00Z",
    "home_team": "Boston Celtics", "away_team": "Miami Heat",
    "bookmakers": [
        {"key": "pinnacle", "last_update": "2026-11-10T23:58:00Z", "markets": [
            {"key": "h2h", "last_update": "2026-11-10T23:58:00Z", "outcomes": [
                {"name": "Boston Celtics", "price": 1.50}, {"name": "Miami Heat", "price": 2.75}]},
            {"key": "totals", "last_update": "2026-11-10T23:58:00Z", "outcomes": [
                {"name": "Over", "price": 1.93, "point": 215.5}, {"name": "Under", "price": 1.93, "point": 215.5}]}]},
        {"key": "softbook", "last_update": "2026-11-10T23:30:00Z", "markets": [
            {"key": "h2h", "last_update": "2026-11-10T23:30:00Z", "outcomes": [
                {"name": "Boston Celtics", "price": 1.45}, {"name": "Miami Heat", "price": 3.10}]}]},
    ],
}


# conversions
def test_odds_conversions_round_trip():
    assert oa.american_to_decimal(150) == pytest.approx(2.5)
    assert oa.american_to_decimal(-110) == pytest.approx(1.909091, abs=1e-6)
    assert oa.decimal_to_american(2.5) == pytest.approx(150)
    assert oa.decimal_to_american(1.909091) == pytest.approx(-110, abs=1e-3)
    am = np.array([-250.0, -110.0, 100.0, 135.0, 900.0])
    assert oa.decimal_to_american(oa.american_to_decimal(am)) == pytest.approx(am)
    assert oa.fractional_to_decimal(5, 2) == pytest.approx(3.5)
    assert oa.implied_probability(4.0) == pytest.approx(0.25)
    with pytest.raises(ValueError):
        oa.american_to_decimal(50)
    with pytest.raises(ValueError):
        oa.implied_probability(1.0)


# devig
def test_even_market_devigs_to_half_under_every_method():
    odds = oa.american_to_decimal([-110, -110])
    for method in ("multiplicative", "power", "shin"):
        assert oa.devig(odds, method) == pytest.approx([0.5, 0.5])


def test_all_methods_sum_to_one_and_shin_shrinks_longshots():
    odds = np.array([1.20, 6.00, 15.00])                         # heavy favourite and two longshots
    mult, power, shin = (oa.devig(odds, m) for m in ("multiplicative", "power", "shin"))
    for p in (mult, power, shin):
        assert p.sum() == pytest.approx(1.0) and np.all(p > 0)
    assert shin[2] < mult[2] and shin[1] < mult[1] and shin[0] > mult[0]
    assert power[2] < mult[2] and power[0] > mult[0]
    fair = np.array([2.0, 4.0, 4.0])                             # already a fair book: nothing to remove
    for m in ("multiplicative", "power", "shin"):
        assert oa.devig(fair, m) == pytest.approx([0.5, 0.25, 0.25])
    with pytest.raises(ValueError):
        oa.devig(odds, "magic")
    with pytest.raises(ValueError):
        oa.devig([1.9], "shin")


def test_shin_two_way_matches_closed_form():
    odds = np.array([1.50, 2.75])
    pi = 1 / odds
    book = pi.sum()
    # for two outcomes Shin reduces to the additive method: remove half the overround from each
    assert oa.devig_shin(odds) == pytest.approx(pi - (book - 1) / 2, abs=1e-9)


def test_fair_probabilities_need_a_sharp_book():
    books = {"pinnacle": [1.50, 2.75], "softbook": [1.45, 3.10]}
    fair = oa.fair_probabilities(books)
    assert fair == pytest.approx(oa.devig_shin([1.50, 2.75]))
    blended = oa.fair_probabilities({**books, "circasports": [1.52, 2.70]}, weights={"pinnacle": 3, "circasports": 1})
    assert blended.sum() == pytest.approx(1.0) and fair[0] < blended[0] + 0.01
    with pytest.raises(LookupError):
        oa.fair_probabilities({"softbook": [1.45, 3.10]})


# value and arbitrage
def test_positive_ev_scanner():
    fair = dict(zip(["Boston Celtics", "Miami Heat"], oa.devig_shin([1.50, 2.75])))
    quotes = oa.normalize_odds([EVENT])
    quotes = quotes[quotes["market"] == "h2h"]
    hits = oa.find_positive_ev(quotes, fair, min_edge=0.02)
    assert len(hits) == 1 and hits.loc[0, "book"] == "softbook" and hits.loc[0, "outcome"] == "Miami Heat"
    assert hits.loc[0, "ev"] == pytest.approx(fair["Miami Heat"] * 3.10 - 1)
    assert oa.expected_value(0.5, 2.1) == pytest.approx(0.05)
    assert oa.find_positive_ev(quotes, fair, min_edge=0.5).empty


def test_arbitrage_detection_and_stakes():
    arb = oa.find_arbitrage([2.10, 2.05])                         # two books, opposite sides
    assert arb.is_arbitrage and arb.margin == pytest.approx(1 - (1 / 2.10 + 1 / 2.05))
    payout = arb.stakes * np.array([2.10, 2.05])
    assert payout[0] == pytest.approx(payout[1]) and payout[0] - 1 == pytest.approx(arb.guaranteed_return)
    assert arb.guaranteed_return > 0
    none = oa.find_arbitrage([1.91, 1.91])
    assert not none.is_arbitrage and none.guaranteed_return < 0
    three = oa.find_arbitrage([2.9, 3.6, 3.4])
    assert three.is_arbitrage and three.stakes.sum() == pytest.approx(1.0)
    thin = oa.find_arbitrage([2.04, 2.02])
    assert thin.is_arbitrage and thin.margin == pytest.approx(1 - (1 / 2.04 + 1 / 2.02))
    with_fee = oa.find_arbitrage([2.04, 2.02], commissions=0.05)          # 5% of winnings kills a thin edge
    assert not with_fee.is_arbitrage
    assert with_fee.margin == pytest.approx(1 - (1 / (1 + 1.04 * 0.95) + 1 / (1 + 1.02 * 0.95)))
    with pytest.raises(ValueError):
        oa.find_arbitrage([2.0])


def test_middles():
    mid = oa.find_middle(over_line=44.5, over_odds=1.91, under_line=46.5, under_odds=1.91)
    assert mid.is_middle and mid.width == pytest.approx(2.0)
    assert mid.win_both_profit == pytest.approx(1.82) and mid.miss_loss == pytest.approx(0.09)
    assert not oa.find_middle(46.5, 1.91, 44.5, 1.91).is_middle


def test_kelly_stakes_clamp_cap_and_zero_edge():
    stakes = oa.kelly_stakes([0.55, 0.45, 0.60], [2.0, 2.0, 2.0], bankroll=10_000, multiplier=0.25, per_bet_cap=0.05)
    assert stakes[0] == pytest.approx(0.10 * 0.25 * 10_000)       # full Kelly 10% of bankroll, quarter Kelly
    assert stakes[1] == 0.0                                        # negative edge
    assert stakes[2] == pytest.approx(0.05 * 10_000)               # 20% * 0.25 = 5%, at the cap
    low = oa.kelly_stakes([0.55], [2.0], 10_000, multiplier=0.01, per_bet_cap=1.0)
    high = oa.kelly_stakes([0.55], [2.0], 10_000, multiplier=5.0, per_bet_cap=1.0)
    assert low[0] == pytest.approx(0.10 * 0.15 * 10_000) and high[0] == pytest.approx(0.10 * 0.50 * 10_000)
    assert oa.kelly_stakes([0.99] * 30, [2.0] * 30, 1_000, 0.5, 0.2).sum() <= 1_000 + 1e-9
    with pytest.raises(ValueError):
        oa.kelly_stakes([1.2], [2.0], 1_000)


# client
class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_client_retries_on_429_and_tracks_quota(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "SECRET-KEY-VALUE")
    slept = []
    transport = FakeTransport([(429, {"x-requests-remaining": "0"}, {"message": "slow down"}),
                               (200, {"X-Requests-Remaining": "498", "x-requests-used": "2"}, [EVENT])])
    client = oa.OddsApiClient(transport, sleep=slept.append, backoff=0.5)
    data = client.odds("basketball_nba", regions="us", markets="h2h,totals")
    assert data[0]["id"] == "evt1" and len(transport.calls) == 2 and slept == [0.5]
    assert client.requests_remaining == 498 and client.requests_used == 2
    url, params = transport.calls[0]
    assert url == "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    assert params["apiKey"] == "SECRET-KEY-VALUE" and params["markets"] == "h2h,totals"
    assert "SECRET-KEY-VALUE" not in repr(client)


def test_client_endpoints_and_bookmaker_filter(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    transport = FakeTransport([(200, {}, [{"key": "basketball_nba"}]), (200, {}, EVENT)])
    client = oa.OddsApiClient(transport)
    assert client.sports()[0]["key"] == "basketball_nba"
    assert client.event_odds("basketball_nba", "evt1", markets="player_points", bookmakers="pinnacle")["id"] == "evt1"
    assert transport.calls[0][0].endswith("/v4/sports")
    url, params = transport.calls[1]
    assert url.endswith("/v4/sports/basketball_nba/events/evt1/odds")
    assert params["bookmakers"] == "pinnacle" and "regions" not in params


def test_client_errors_never_leak_the_key(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    with pytest.raises(oa.OddsApiError, match="not set"):
        oa.OddsApiClient(FakeTransport([])).sports()
    monkeypatch.setenv("ODDS_API_KEY", "SECRET-KEY-VALUE")
    boom = ConnectionError("https://api.the-odds-api.com/v4/sports?apiKey=SECRET-KEY-VALUE refused")
    client = oa.OddsApiClient(FakeTransport([boom, boom]), max_retries=1, sleep=lambda s: None)
    with pytest.raises(oa.OddsApiError) as err:
        client.sports()
    assert "SECRET-KEY-VALUE" not in str(err.value) and "ConnectionError" in str(err.value)
    denied = oa.OddsApiClient(FakeTransport([(401, {}, {"message": "bad key SECRET-KEY-VALUE"})]))
    with pytest.raises(oa.OddsApiError) as err:
        denied.sports()
    assert "SECRET-KEY-VALUE" not in str(err.value) and "401" in str(err.value)
    always_500 = FakeTransport([(500, {}, None)] * 3)
    with pytest.raises(oa.OddsApiError, match="HTTP 500"):
        oa.OddsApiClient(always_500, max_retries=2, sleep=lambda s: None).sports()
    assert len(always_500.calls) == 3


def test_normalize_and_stale_quote_filter():
    df = oa.normalize_odds([EVENT])
    assert len(df) == 6 and set(df["market"]) == {"h2h", "totals"}
    over = df[(df["market"] == "totals") & (df["outcome"] == "Over")].iloc[0]
    assert over["point"] == 215.5 and over["decimal_odds"] == 1.93
    assert df["last_update"].dt.tz is not None
    fresh = oa.normalize_odds([EVENT], now="2026-11-11T00:00:00Z", max_age_seconds=600)
    assert set(fresh["book"]) == {"pinnacle"}                      # softbook quote is 30 minutes old
    assert oa.normalize_odds([]).empty
    prop = {"id": "e2", "bookmakers": [{"key": "b", "markets": [{"key": "player_points", "outcomes": [
        {"name": "Over", "description": "Jayson Tatum", "price": 1.87, "point": 27.5},
        {"name": "Under", "description": "Jayson Tatum", "price": 1.0}]}]}]}
    props = oa.normalize_odds(prop)
    assert props["outcome"].tolist() == ["Jayson Tatum Over"]      # the unpriced leg is dropped
    assert isinstance(props, pd.DataFrame)


# historical endpoints, usage headers and which key
TEN = ["pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "espnbet", "fanatics", "lowvig",
       "betonlineag"]
SNAP = {"timestamp": "2026-01-10T15:55:00Z", "previous_timestamp": "2026-01-10T15:50:00Z",
        "next_timestamp": "2026-01-10T16:00:00Z", "data": [EVENT]}


def test_historical_cost_follows_the_documented_rule():
    assert oa.historical_cost("h2h", bookmakers=TEN) == 10                  # up to 10 books = one region
    assert oa.historical_cost("h2h", bookmakers=TEN + ["bovada"]) == 20     # 11 to 20 books = two regions
    assert oa.historical_cost("h2h,spreads,totals", regions="us") == 30
    assert oa.historical_cost("h2h", regions="us,uk,eu") == 30
    assert oa.historical_cost(["h2h", "totals"], regions="us,uk") == 40


def test_historical_odds_uses_the_paid_key_read_at_call_time_and_keeps_the_usage_headers():
    keys = {"ODDS_API_KEY": "PAID-KEY-0001", "ODDS_API_KEY_FREE": "FREE-KEY-0002"}
    asked = []

    def reader(name):
        asked.append(name)
        return keys[name]

    transport = FakeTransport([(200, {"X-Requests-Remaining": "19990", "x-requests-used": "10", "x-requests-last": "10"},
                                SNAP)])
    client = oa.OddsApiClient(transport, key_reader=reader)
    assert asked == []                                                      # nothing read before a call
    body = client.historical_odds("basketball_nba", "2026-01-10T16:00:00Z", bookmakers=TEN)
    assert body["timestamp"] == "2026-01-10T15:55:00Z" and asked == ["ODDS_API_KEY"]
    url, params = transport.calls[0]
    assert url == "https://api.the-odds-api.com/v4/historical/sports/basketball_nba/odds"
    assert params == {"date": "2026-01-10T16:00:00Z", "markets": "h2h", "bookmakers": ",".join(TEN),
                      "oddsFormat": "decimal", "dateFormat": "iso", "apiKey": "PAID-KEY-0001"}
    assert client.last_usage == {"remaining": 19990, "used": 10, "last": 10}
    assert client.requests_remaining == 19990 and client.requests_last == 10


def test_historical_odds_is_never_asked_with_the_free_key():
    transport = FakeTransport([])
    client = oa.OddsApiClient(transport, key_reader=lambda name: "FREE-KEY-0002")
    with pytest.raises(oa.OddsApiError, match="paid key"):
        client.historical_odds("basketball_nba", "2026-01-10T16:00:00Z", bookmakers=TEN, key="free")
    assert transport.calls == []
    with pytest.raises(ValueError):
        client.historical_odds("basketball_nba", "2026-01-10T16:00:00Z", bookmakers=TEN, key="mine")


def test_historical_events_may_use_the_free_key():
    asked = []
    transport = FakeTransport([(200, {"x-requests-remaining": "499", "x-requests-used": "1", "x-requests-last": "1"},
                                {"timestamp": "2026-01-10T16:00:00Z", "data": []})])
    client = oa.OddsApiClient(transport, key_reader=lambda name: asked.append(name) or "FREE-KEY-0002")
    client.historical_events("icehockey_nhl", "2026-01-10T16:00:00Z", key="free")
    assert asked == ["ODDS_API_KEY_FREE"]
    url, params = transport.calls[0]
    assert url.endswith("/v4/historical/sports/icehockey_nhl/events") and params["apiKey"] == "FREE-KEY-0002"
    assert client.last_usage == {"remaining": 499, "used": 1, "last": 1}


def test_usage_is_reset_per_call_and_missing_headers_read_as_none():
    transport = FakeTransport([(200, {"x-requests-last": "10", "x-requests-remaining": "5"}, SNAP), (200, {}, SNAP)])
    client = oa.OddsApiClient(transport, key_reader=lambda name: "PAID-KEY-0001")
    client.historical_odds("basketball_nba", "2026-01-10T16:00:00Z", bookmakers=TEN)
    client.historical_odds("basketball_nba", "2026-01-11T16:00:00Z", bookmakers=TEN)
    assert client.last_usage == {"remaining": None, "used": None, "last": None}


def test_keyed_calls_never_leak_either_key(monkeypatch):
    def missing(name):
        raise oa.OddsApiError(f"{name} is not set")

    with pytest.raises(oa.OddsApiError, match="ODDS_API_KEY"):
        oa.OddsApiClient(FakeTransport([]), key_reader=missing).historical_odds("basketball_nba", "2026-01-10", TEN)
    boom = ConnectionError("https://api.the-odds-api.com/v4/historical?apiKey=PAID-KEY-0001 refused")
    client = oa.OddsApiClient(FakeTransport([boom]), max_retries=0, key_reader=lambda n: "PAID-KEY-0001")
    with pytest.raises(oa.OddsApiError) as err:
        client.historical_odds("basketball_nba", "2026-01-10T16:00:00Z", TEN)
    assert "PAID-KEY-0001" not in str(err.value) and "PAID-KEY-0001" not in repr(client)
    denied = oa.OddsApiClient(FakeTransport([(401, {"x-requests-last": "0"}, {"message": "bad FREE-KEY-0002"})]),
                              key_reader=lambda n: "FREE-KEY-0002")
    with pytest.raises(oa.OddsApiError) as err:
        denied.historical_events("basketball_nba", "2026-01-10T16:00:00Z", key="free")
    assert "FREE-KEY-0002" not in str(err.value) and denied.last_usage["last"] == 0


def test_default_key_reader_goes_through_the_secrets_loader(monkeypatch):
    from valedia_trader.core import secrets
    seen = []
    real_load, real_secret = secrets.load_env, secrets.secret
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: seen.append("load_env") or real_load(*a, **k))
    monkeypatch.setattr(secrets, "secret", lambda name, environ=None: seen.append(name) or real_secret(name, environ))
    monkeypatch.setenv("ODDS_API_KEY", "PAID-KEY-FROM-ENV-1")
    assert oa.read_key("ODDS_API_KEY") == "PAID-KEY-FROM-ENV-1"
    assert seen == ["load_env", "ODDS_API_KEY"]
    monkeypatch.delenv("ODDS_API_KEY_FREE", raising=False)
    with pytest.raises(oa.OddsApiError) as err:
        oa.read_key("ODDS_API_KEY_FREE")
    assert "ODDS_API_KEY_FREE" in str(err.value)
