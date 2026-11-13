"""The two calls added to OddsApiClient for grading the pick lists: the free events list and the scores list."""
import pytest

from valedia_trader.venues import odds_api as oa

KEYS = {"ODDS_API_KEY": "PAID-KEY-0001", "ODDS_API_KEY_FREE": "FREE-KEY-0002"}
EVENTS = [{"id": "e1", "sport_key": "basketball_nba", "sport_title": "NBA", "commence_time": "2026-11-14T00:10:00Z",
           "home_team": "Indiana Pacers", "away_team": "New York Knicks"}]
SCORES = [dict(EVENTS[0], completed=True, last_update="2026-11-14T03:00:00Z",
               scores=[{"name": "Indiana Pacers", "score": "101"}, {"name": "New York Knicks", "score": "99"}])]


class Transport:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        return self.answers.pop(0)


def client(transport, asked=None):
    def reader(name):
        if asked is not None:
            asked.append(name)
        return KEYS[name]
    return oa.OddsApiClient(transport, key_reader=reader, sleep=lambda s: None)


def test_events_uses_the_free_key_by_default_and_keeps_the_usage_headers():
    asked = []
    t = Transport([(200, {"x-requests-remaining": "500", "x-requests-used": "0", "x-requests-last": "0"}, EVENTS)])
    c = client(t, asked)
    assert c.events("basketball_nba") == EVENTS
    url, params = t.calls[0]
    assert url == "https://api.the-odds-api.com/v4/sports/basketball_nba/events"
    assert params == {"dateFormat": "iso", "apiKey": "FREE-KEY-0002"} and asked == ["ODDS_API_KEY_FREE"]
    assert c.last_usage == {"remaining": 500, "used": 0, "last": 0} and c.attempts_sent == 1


def test_events_with_the_paid_key():
    t = Transport([(200, {"x-requests-last": "0"}, [])])
    client(t).events("icehockey_nhl", key="paid")
    assert t.calls[0][1]["apiKey"] == "PAID-KEY-0001"
    with pytest.raises(ValueError):
        client(Transport([])).events("icehockey_nhl", key="mine")


def test_scores_with_and_without_days_from():
    t = Transport([(200, {"x-requests-last": "2", "x-requests-used": "2"}, SCORES), (200, {"x-requests-last": "1"}, [])])
    c = client(t)
    assert c.scores("basketball_nba", days_from=3) == SCORES
    assert c.last_usage["last"] == 2
    c.scores("basketball_nba")
    (u1, p1), (u2, p2) = t.calls
    assert u1 == u2 == "https://api.the-odds-api.com/v4/sports/basketball_nba/scores/"
    assert p1 == {"daysFrom": 3, "dateFormat": "iso", "apiKey": "FREE-KEY-0002"}
    assert p2 == {"dateFormat": "iso", "apiKey": "FREE-KEY-0002"}          # no daysFrom: nothing sent for it


@pytest.mark.parametrize("bad", [0, 4, -1, True])
def test_scores_refuses_a_days_from_outside_one_to_three_before_sending(bad):
    t = Transport([])
    with pytest.raises(ValueError):
        client(t).scores("basketball_nba", days_from=bad)
    assert t.calls == []


def test_retries_can_be_turned_off_per_call_and_errors_never_hold_a_key():
    t = Transport([(503, {}, None), (503, {}, None)])
    c = client(t)
    with pytest.raises(oa.OddsApiError) as e:
        c.events("basketball_nba", retries=0)
    assert len(t.calls) == 1 and e.value.status == 503
    for k in KEYS.values():
        assert k not in str(e.value) and k not in repr(c)
    t = Transport([(429, {}, None), (200, {"x-requests-last": "2"}, SCORES)])
    assert client(t).scores("basketball_nba", days_from=1, retries=1) == SCORES and len(t.calls) == 2


def test_existing_calls_are_unchanged():
    t = Transport([(200, {"x-requests-last": "1"}, {"timestamp": "2026-11-12T10:55:00Z", "data": []})])
    client(t).historical_events("basketball_nba", "2026-11-12T11:00:00Z", key="paid")
    assert t.calls[0][0].endswith("/v4/historical/sports/basketball_nba/events")
