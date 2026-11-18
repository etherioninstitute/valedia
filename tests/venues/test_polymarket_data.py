"""The Polymarket public data reader: GET only, paced, retried, cursor walks that never return a silent partial list,
and every row validated (a wrong-shaped row is a counted reject). All answers here are synthetic: no request leaves."""
import email.message
import io
import json
import math
import socket
import urllib.parse
import urllib.request
import urllib.response
from pathlib import Path

import pytest

from valedia_trader.venues import polymarket_data as pd


@pytest.fixture(autouse=True)
def no_socket_may_connect(monkeypatch):
    """A second lock beside the fakes: any socket connection in these tests fails at once."""
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


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
    """`handler(path, query) -> dict | Exception | bytes`; records every URL asked."""
    def __init__(self, handler):
        self.handler = handler
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        assert parts.scheme + "://" + parts.netloc == pd.BASE_URL
        ans = self.handler(parts.path, dict(urllib.parse.parse_qsl(parts.query)))
        if isinstance(ans, Exception):
            raise ans
        return ans if isinstance(ans, bytes) else json.dumps(ans).encode()

    def queries(self):
        return [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)) for u in self.urls]


def client(handler, **kw):
    clock = FakeClock()
    t = FakeTransport(handler)
    return pd.PolymarketData(t, sleep=clock.sleep, clock=clock, **kw), t, clock


def page(rows, has_more=False, cursor=None, offset=0):
    return {"data": rows, "pagination": {"limit": 1000, "offset": offset, "has_more": has_more, "next_cursor": cursor}}


def trade(i=0, **kw):
    r = {"proxy_wallet": "0xabc", "side": "BUY", "token_id": "111", "condition_id": "0xc1", "size": 10.0,
         "price": 0.42, "timestamp": 1_700_000_000 + i, "title": "T", "slug": "s", "event_slug": "ev",
         "outcome": "Yes", "outcome_index": 0, "transaction_hash": "0xt%d" % i, "name": "n", "pseudonym": "p",
         "bio": "", "profile_image": "", "profile_image_optimized": "", "icon": ""}
    r.update(kw)
    return r


def board_row(rank, user, pnl=1000.0):
    return {"rank": rank, "user_id": user, "pnl": pnl, "volume": 5.0, "user_name": "u", "x_username": "",
            "verified": False, "profile_image": ""}


# ------------------------------------------------------------------ manners
def test_constants_and_generic_client_name():
    assert pd.BASE_URL == "https://data-api.polymarket.com"
    assert pd.USER_AGENT == "market-data-reader/1.0"


class FakeHTTPS(urllib.request.HTTPSHandler):
    """A stand-in for the network inside urllib's own opener: answers (status, headers, body) and records requests."""

    def __init__(self, status=200, headers=None, body=b'{"data": []}'):
        super().__init__()
        self.status, self.headers, self.body = status, headers or {}, body
        self.requests = []

    def https_open(self, req):
        self.requests.append((req.get_method(), req.get_header("User-agent"), req.full_url, req.data))
        h = email.message.Message()
        for k, v in self.headers.items():
            h[k] = v
        resp = urllib.response.addinfourl(io.BytesIO(self.body), h, req.full_url, self.status)
        resp.msg = "fake"
        return resp


def fake_network(monkeypatch, **kw):
    fake = FakeHTTPS(**kw)
    monkeypatch.setattr(pd, "_HANDLERS", (urllib.request.ProxyHandler({}), fake))
    return fake


def test_default_transport_sends_only_a_get_with_the_generic_name(monkeypatch):
    fake = fake_network(monkeypatch, headers={"Date": "Tue, 17 Nov 2026 10:00:00 GMT"})
    body, date = pd.urllib_get(pd.BASE_URL + "/v2/resolutions?condition=0x1")
    assert body == b'{"data": []}' and date.startswith("Tue")
    assert fake.requests == [("GET", "market-data-reader/1.0", pd.BASE_URL + "/v2/resolutions?condition=0x1", None)]


@pytest.mark.parametrize("url", [pd.BASE_URL + "/v2/resolutions?condition=0x1%2C0x2",
                                 pd.BASE_URL + "/v2/prices-history?token_id=111&start=1&end=2",
                                 pd.BASE_URL + "/v2/leaderboard"])
def test_the_addresses_the_reader_builds_are_allowed(monkeypatch, url):
    fake = fake_network(monkeypatch)
    pd.urllib_get(url)
    assert len(fake.requests) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@pytest.mark.parametrize("where", ["https://example.com/v2/trades", pd.BASE_URL + "/v2/trades?user=0xother"])
def test_a_redirect_is_never_followed_one_request_and_an_error(monkeypatch, status, where):
    fake = fake_network(monkeypatch, status=status, headers={"Location": where})
    with pytest.raises(pd.HttpError, match=str(status)) as e:
        pd.urllib_get(pd.BASE_URL + "/v2/trades?user=0xabc")
    assert e.value.status == status and len(fake.requests) == 1
    c = pd.PolymarketData(pd.urllib_get, sleep=lambda s: None)
    fake.requests.clear()
    with pytest.raises(pd.HttpError):
        c.get_json("/v2/trades", {"user": "0xabc"})
    assert len(fake.requests) == 1                     # not retried


def test_any_other_non_200_answer_is_an_error(monkeypatch):
    fake_network(monkeypatch, status=204, body=b"")
    with pytest.raises(pd.HttpError, match="204"):
        pd.urllib_get(pd.BASE_URL + "/v2/trades?user=0xabc")


@pytest.mark.parametrize("url", ["https://example.com/v2/trades", "http://data-api.polymarket.com/v2/trades",
                                 "https://data-api.polymarket.com.evil.io/v2/trades",
                                 "https://data-api.polymarket.com/v1/trades",
                                 "https://data-api.polymarket.com/v2/../v1/x",
                                 "https://data-api.polymarket.com/v2/%2e%2e/v1/x",
                                 "https://data-api.polymarket.com//v2/trades",
                                 "https://user@data-api.polymarket.com/v2/trades",
                                 "https://data-api.polymarket.com:8443/v2/trades",
                                 "https://data-api.polymarket.com/v2/%252e%252e/v1/x",
                                 "https://data-api.polymarket.com/v2%2fx",
                                 " https://data-api.polymarket.com/v2/trades",
                                 "https://data-api.polymarket.com/v2/trades ",
                                 "https://data-api.polymarket.com/v2/trades\n",
                                 "https://data-api.polymarket.com/v2/trades#x",
                                 "https://data-api.polymarket.com/v2/Trades",
                                 "https://data-api.polymarket.com/v2/",
                                 "https://data-api.polymarket.com/v2/trades?user=0x1 x",
                                 "HTTPS://data-api.polymarket.com/v2/trades",
                                 "https://data-api.polymarket.com/v2//x",          # no empty path segment
                                 "https://data-api.polymarket.com/v2/x//y",
                                 "https://data-api.polymarket.com/v2///",
                                 "https://data-api.polymarket.com/v2/trades/",     # no trailing slash
                                 "https://data-api.polymarket.com/v2/trades/?user=0x1"])
def test_default_transport_refuses_any_other_address(monkeypatch, url):
    fake = fake_network(monkeypatch)
    with pytest.raises(pd.PolymarketDataError):
        pd.urllib_get(url)
    assert fake.requests == []


@pytest.mark.parametrize("host", ["data-apiXpolymarket.com", "data-api.polymarketXcom", "data-api.polymarket.com"])
def test_a_look_alike_host_differing_where_the_real_one_has_a_dot_is_refused(monkeypatch, host):
    """Fails when the service's host is not escaped in the expression: an unescaped dot matches any character."""
    fake = fake_network(monkeypatch)
    url = "https://%s/v2/trades?user=0x1" % host
    if host == "data-api.polymarket.com":                 # the real host still passes: the check has two sides
        pd.urllib_get(url)
        assert len(fake.requests) == 1
        return
    assert not pd._allowed(url)
    with pytest.raises(pd.PolymarketDataError):
        pd.urllib_get(url)
    assert fake.requests == []


def test_client_has_no_base_url_knob_and_no_send_verbs():
    import inspect
    src = inspect.getsource(pd)
    for verb in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', "data=", "urlencode(data"):
        assert verb not in src
    assert "base_url" not in inspect.signature(pd.PolymarketData).parameters


def test_at_most_three_requests_a_second():
    c, t, clock = client(lambda p, q: {"data": []})
    starts = []
    orig = t.handler
    t.handler = lambda p, q: (starts.append(clock.t), orig(p, q))[1]
    for _ in range(7):
        c.get_json("/v2/resolutions", {"condition": "0x1"})
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g > 1 / 3 for g in gaps)
    assert all(starts[k + 3] - starts[k] > 1.0 for k in range(len(starts) - 3))


def test_429_and_server_errors_are_retried_with_a_growing_wait_then_a_clear_error():
    calls = []

    def h(p, q):
        calls.append(1)
        return pd.HttpError(503 if len(calls) % 2 else 429, p)

    c, t, clock = client(h)
    with pytest.raises(pd.PolymarketDataError, match="5 tries"):
        c.get_json("/v2/resolutions", {"condition": "0x1"})
    assert len(calls) == 5
    waits = [s for s in clock.sleeps if s >= 1.0]
    assert waits == [1.0, 2.0, 4.0, 8.0]


def test_retry_after_is_honoured_and_a_success_after_a_retry_is_returned():
    calls = []

    def h(p, q):
        calls.append(1)
        return pd.HttpError(429, p, retry_after=7.0) if len(calls) == 1 else {"data": []}

    c, t, clock = client(h)
    assert c.get_json("/v2/resolutions", {"condition": "0x1"}) == {"data": []}
    assert 7.0 in clock.sleeps


def test_a_timeout_or_reset_is_retried_with_the_same_waits_then_a_clear_error():
    calls = []

    def h(p, q):
        calls.append(1)
        return [TimeoutError("timed out"), ConnectionResetError("reset"), OSError("dropped")][len(calls) % 3]

    c, t, clock = client(h)
    with pytest.raises(pd.PolymarketDataError, match="5 tries"):
        c.get_json("/v2/resolutions", {"condition": "0x1"})
    assert len(calls) == 5
    assert [s for s in clock.sleeps if s >= 1.0] == [1.0, 2.0, 4.0, 8.0]


def test_a_success_after_a_timeout_is_returned():
    calls = []

    def h(p, q):
        calls.append(1)
        return TimeoutError("timed out") if len(calls) < 3 else {"data": []}

    c, t, clock = client(h)
    assert c.get_json("/v2/resolutions", {"condition": "0x1"}) == {"data": []} and len(calls) == 3


def test_a_connection_dropped_mid_body_is_retried():
    import http.client
    calls = []

    def h(p, q):
        calls.append(1)
        return http.client.IncompleteRead(b'{"da') if len(calls) == 1 else {"data": []}

    c, t, clock = client(h)
    assert c.get_json("/v2/resolutions", {"condition": "0x1"}) == {"data": []} and len(calls) == 2


def test_a_short_body_through_urllib_is_retried(monkeypatch):
    import http.client

    class Short(io.BytesIO):
        def read(self, *a):
            raise http.client.IncompleteRead(b"{")

    class Once(FakeHTTPS):
        def https_open(self, req):
            resp = super().https_open(req)
            if len(self.requests) == 1:
                resp.fp = Short(b"")
                resp.read = resp.fp.read
            return resp
    fake = Once()
    monkeypatch.setattr(pd, "_HANDLERS", (urllib.request.ProxyHandler({}), fake))
    c = pd.PolymarketData(pd.urllib_get, sleep=lambda s: None)
    assert c.get_json("/v2/resolutions", {"condition": "0x1"}) == {"data": []} and len(fake.requests) == 2


def test_a_400_or_404_is_not_retried():
    calls = []
    c, t, clock = client(lambda p, q: (calls.append(1), pd.HttpError(400, p))[1])
    with pytest.raises(pd.HttpError):
        c.get_json("/v2/trades", {"user": "0xabc"})
    assert len(calls) == 1


def test_not_json_is_a_clear_error():
    c, t, clock = client(lambda p, q: b"<html>")
    with pytest.raises(pd.PolymarketDataError, match="not JSON"):
        c.get_json("/v2/trades", {"user": "0xabc"})


def test_only_v2_paths_are_asked():
    c, t, clock = client(lambda p, q: {"data": []})
    for bad in ("/v1/trades", "trades", "/v2/../admin", "//evil.io/v2/trades", "/v2/%2e%2e/x", "/v2/trades ",
                "/v2/T", "/v2/", " /v2/trades", "/v2//x", "/v2/x//y", "/v2///", "/v2/trades/"):
        with pytest.raises(pd.PolymarketDataError):
            c.get_json(bad, {})
    assert t.urls == []


# ------------------------------------------------------------------ the cursor walk
def test_cursor_walk_of_three_pages_resends_the_filters_and_stops_when_has_more_is_false():
    pages = {None: page([trade(0), trade(1)], True, "c1"), "c1": page([trade(2)], True, "c2", 2),
             "c2": page([trade(3)], False, None, 3)}
    c, t, clock = client(lambda p, q: pages[q.get("cursor")])
    w = c.trades("0xabc")
    assert [r["transaction_hash"] for r in w.rows] == ["0xt0", "0xt1", "0xt2", "0xt3"]
    assert w.pages == 3 and sum(w.rejects.values()) == 0 and len(w.raw) == 4 and len(w.read_at) == 3
    qs = t.queries()
    assert qs[0] == {"user": "0xabc", "start": "1", "limit": "1000", "taker_only": "false"}
    assert qs[1] == {"user": "0xabc", "start": "1", "taker_only": "false", "cursor": "c1"}
    assert qs[2]["cursor"] == "c2"


def test_a_repeated_cursor_raises():
    pages = {None: page([trade(0)], True, "c1"), "c1": page([trade(1)], True, "c1")}
    c, t, clock = client(lambda p, q: pages[q.get("cursor")])
    with pytest.raises(pd.PolymarketDataError, match="repeated cursor"):
        c.trades("0xabc")


def test_has_more_without_a_cursor_raises():
    c, t, clock = client(lambda p, q: page([trade(0)], True, None))
    with pytest.raises(pd.PolymarketDataError, match="no next_cursor"):
        c.trades("0xabc")


def test_the_page_cap_raises_never_a_silent_partial_list():
    c, t, clock = client(lambda p, q: page([trade(int(q.get("cursor", "0")))], True, str(int(q.get("cursor", "0")) + 1)))
    with pytest.raises(pd.PageCapHit):
        c.trades("0xabc", page_cap=4)
    assert len(t.urls) == 4


@pytest.mark.parametrize("answer", [{"rows": []}, {"data": {}, "pagination": {"has_more": False}},
                                    {"data": []}, {"data": [], "pagination": {"has_more": "no"}},
                                    {"data": [], "pagination": {"has_more": True, "next_cursor": 5}}, [1, 2]])
def test_a_wrong_envelope_raises(answer):
    c, t, clock = client(lambda p, q: answer)
    with pytest.raises(pd.PolymarketDataError):
        c.trades("0xabc")


# ------------------------------------------------------------------ shapes
@pytest.mark.parametrize("field,value,reason", [
    ("size", True, "bad_size"), ("size", None, "bad_size"), ("size", float("nan"), "bad_size"),
    ("size", "10", "bad_size"), ("size", 0.0, "bad_size"), ("size", -1.0, "bad_size"),
    ("price", None, "bad_price"), ("price", float("inf"), "bad_price"), ("price", 1.5, "bad_price"),
    ("price", False, "bad_price"), ("timestamp", "1700000000", "bad_timestamp"), ("timestamp", 1.7e9, "bad_timestamp"),
    ("timestamp", True, "bad_timestamp"), ("side", "HOLD", "bad_side"), ("outcome_index", 2, "bad_outcome_index"),
    ("outcome_index", True, "bad_outcome_index"), ("token_id", "", "bad_token_id"), ("token_id", 111, "bad_token_id"),
    ("condition_id", None, "bad_condition_id"), ("event_slug", None, "bad_event_slug"),
    ("proxy_wallet", "0xother", "other_wallet")])
def test_a_wrong_trade_row_is_a_counted_reject(field, value, reason):
    bad = trade(9, **{field: value})
    c, t, clock = client(lambda p, q: page([trade(0), bad, trade(1)]))
    w = c.trades("0xabc")
    assert [r["transaction_hash"] for r in w.rows] == ["0xt0", "0xt1"]
    assert dict(w.rejects) == {reason: 1}
    assert [reason_ for _, reason_ in w.raw] == [None, reason, None]


def test_a_missing_field_and_a_non_object_row_are_counted_rejects():
    r = trade(5)
    del r["price"]
    c, t, clock = client(lambda p, q: page([r, "x", trade(1)]))
    w = c.trades("0xabc")
    assert len(w.rows) == 1 and dict(w.rejects) == {"bad_price": 1, "not_an_object": 1}


def test_wallet_case_is_compared_folded():
    c, t, clock = client(lambda p, q: page([trade(0, proxy_wallet="0xABC")]))
    assert len(c.trades("0xAbc").rows) == 1


def test_validate_returns_typed_values():
    row, why = pd.validate_trade(trade(3), "0xabc")
    assert why is None and row["size"] == 10.0 and row["timestamp"] == 1_700_000_003 and row["outcome_index"] == 0


def test_leaderboard_reads_the_first_rows_only_across_pages():
    def h(p, q):
        assert p == "/v2/leaderboard"
        if "cursor" not in q:
            assert q == {"time_period": "all", "sort_by": "PNL", "limit": "100"}
            return page([board_row(i + 1, "0x%02d" % i) for i in range(60)], True, "b1")
        return page([board_row(i + 61, "0x%02d" % (i + 60)) for i in range(60)], True, "b2")
    c, t, clock = client(h)
    w = c.leaderboard("all", rows=100)
    assert len(w.rows) == 100 and w.rows[-1]["user_id"] == "0x99" and w.pages == 2
    assert t.queries()[1] == {"cursor": "b1"}           # a board cursor pins the board: no params beside it


@pytest.mark.parametrize("field,value", [("rank", "1"), ("rank", True), ("user_id", ""), ("user_id", None),
                                         ("pnl", None), ("pnl", float("nan")), ("pnl", "12")])
def test_a_wrong_board_row_is_a_counted_reject(field, value):
    bad = board_row(2, "0xbad")
    bad[field] = value
    c, t, clock = client(lambda p, q: page([board_row(1, "0xa"), bad, board_row(3, "0xc")]))
    w = c.leaderboard("month", rows=100)
    assert [r["user_id"] for r in w.rows] == ["0xa", "0xc"] and sum(w.rejects.values()) == 1


def test_leaderboard_refuses_unknown_windows():
    c, t, clock = client(lambda p, q: page([]))
    with pytest.raises(ValueError):
        c.leaderboard("year")


def position(i=0, **kw):
    r = {"proxy_wallet": "0xabc", "token_id": "t%d" % i, "condition_id": "0xc", "current_size": 1.0, "avg_price": 0.5,
         "entry_cost_usdc": 0.5, "entry_fees_usdc": 0.0, "total_cost_usdc": 0.5, "current_price": 0.5,
         "current_value": 0.5, "total_size": 1.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0,
         "status": "OPEN", "redeemable": False, "negative_risk": False, "title": "t", "slug": "s", "event_id": "1",
         "event_slug": "e", "outcome": "Yes", "outcome_index": 0, "opposite_outcome": "No", "opposite_token_id": "u",
         "end_date": "2026-01-01", "last_event_at": 1, "first_entry_at": 1}
    r.update(kw)
    return r


def test_positions_open_and_closed():
    c, t, clock = client(lambda p, q: page([position(0, status=q.get("status", "OPEN")), position(1, total_pnl="x")]))
    w_open, w_closed = c.positions("0xabc"), c.positions("0xabc", status="CLOSED")
    assert len(w_open.rows) == 1 and dict(w_open.rejects) == {"bad_total_pnl": 1}
    assert w_closed.rows[0]["status"] == "CLOSED"
    assert t.queries()[0] == {"user": "0xabc", "limit": "1000"}
    assert t.queries()[1] == {"user": "0xabc", "status": "CLOSED", "limit": "1000"}
    with pytest.raises(ValueError):
        c.positions("0xabc", status="ALL")


def resolution(cond, **kw):
    r = {"condition_id": cond, "status": "resolved", "payouts": [1_000_000, 0], "resolved_at": "2026-02-01T10:00:00Z",
         "extended_review": False, "was_disputed": False, "new_version_q": False, "transaction_hash": "",
         "log_index": "", "last_update_timestamp": "2026-02-01T10:00:00Z", "market_type": "BINARY"}
    r.update(kw)
    return r


def test_resolutions_ask_up_to_twenty_conditions_in_one_get_with_no_pagination():
    def h(p, q):
        assert p == "/v2/resolutions"
        conds = q["condition"].split(",")
        return {"data": [resolution(cd) for cd in conds[:-1]] + [resolution(conds[-1], payouts=[1, 2, 3])]}
    c, t, clock = client(h)
    w = c.resolutions(["0x%d" % i for i in range(20)])
    assert len(w.rows) == 19 and dict(w.rejects) == {"bad_payouts": 1} and w.pages == 1
    assert w.rows[0]["resolved_ts"] == 1769940000
    with pytest.raises(ValueError):
        c.resolutions(["0x%d" % i for i in range(21)])
    with pytest.raises(ValueError):
        c.resolutions([])


@pytest.mark.parametrize("field,value,reason", [
    ("payouts", [True, 0], "bad_payouts"), ("payouts", [0.5, 0.5], "bad_payouts"), ("payouts", [-1, 1], "bad_payouts"),
    ("payouts", "1,0", "bad_payouts"), ("resolved_at", "yesterday", "bad_resolved_at"), ("resolved_at", 5, "bad_resolved_at"),
    ("resolved_at", "2026-02-01T10:00:00", "bad_resolved_at"), ("status", None, "bad_status"),
    ("condition_id", None, "bad_condition_id")])
def test_a_wrong_resolution_row_is_a_counted_reject(field, value, reason):
    row, why = pd.validate_resolution(resolution("0x1", **{field: value}))
    assert row is None and why == reason


def test_an_unresolved_row_without_payouts_is_valid():
    row, why = pd.validate_resolution(resolution("0x1", status="proposed", payouts=None, resolved_at=None))
    assert why is None and row["payouts"] is None and row["resolved_ts"] is None


def test_resolutions_refuse_a_paged_answer():
    c, t, clock = client(lambda p, q: page([resolution("0x1")], True, "r1"))
    with pytest.raises(pd.PolymarketDataError):
        c.resolutions(["0x1"])


def test_prices_history_window_walk_and_point_shapes():
    def h(p, q):
        assert p == "/v2/prices-history"
        if "cursor" not in q:
            assert q == {"token_id": "111", "start": "1700000100", "end": "1700000200"}
            return page([{"price": 0.4, "resolution_seconds": 300, "timestamp": 1700000100},
                         {"price": True, "resolution_seconds": 300, "timestamp": 1700000400}], True, "p1")
        assert q == {"token_id": "111", "start": "1700000100", "end": "1700000200", "cursor": "p1"}
        return page([{"price": 0.45, "resolution_seconds": 0, "timestamp": 1700000150},
                     {"price": 0.5, "resolution_seconds": -1, "timestamp": 1700000160},
                     {"price": float("nan"), "resolution_seconds": 0, "timestamp": 1700000170}])
    c, t, clock = client(h)
    w = c.prices_history("111", 1_700_000_100, 1_700_000_200)
    assert [(r["timestamp"], r["price"], r["resolution_seconds"]) for r in w.rows] == [(1_700_000_100, 0.4, 300), (1_700_000_150, 0.45, 0)]
    assert dict(w.rejects) == {"bad_price": 2, "bad_resolution_seconds": 1}
    for start, end in ((200, 100), (0, 100), (True, 100)):
        with pytest.raises(ValueError):
            c.prices_history("111", start, end)


def test_an_empty_price_window_is_a_finished_walk_with_no_points():
    c, t, clock = client(lambda p, q: page([]))
    w = c.prices_history("111", 100, 200)
    assert w.rows == [] and w.pages == 1


def test_the_market_trade_feed_is_gone():
    assert not hasattr(pd.PolymarketData, "market_trades")


def test_resolutions_are_batched_by_twenty_and_a_short_answer_is_recorded_per_id():
    def h(p, q):
        conds = q["condition"].split(",")
        rows = [resolution(cd) for cd in conds if cd != "0x7"]
        return {"data": rows + ([resolution("0x8", payouts="bad")] if "0x8" in conds else [])}
    c, t, clock = client(h)
    out = list(c.resolution_batches(["0x%d" % i for i in range(45)]))
    assert [len(batch) for batch, _, _ in out] == [20, 20, 5] and len(t.urls) == 3
    answers = {k: v for _, _, ans in out for k, v in ans.items()}
    assert len(answers) == 45
    assert answers["0x7"] == "no_row" and answers["0x1"] == "row" and answers["0x8"] == "row_and_reject"
    assert out[0][1].rejects == {"bad_payouts": 1}


def test_validate_number_helper_fails_closed():
    for v in (True, False, None, "1", float("nan"), float("inf"), -float("inf"), [1]):
        assert pd.is_number(v) is False
    assert pd.is_number(0) and pd.is_number(0.5) and not math.isnan(0.5)


# ------------------------------------------------------------------ one identity per row
def test_a_trade_repeated_across_pages_is_kept_once_and_counted():
    pages = {None: page([trade(1), trade(2)], True, "c1"),
             "c1": page([trade(2), trade(3), trade(3, size=11.0)])}
    c, t, clock = client(lambda p, q: pages[q.get("cursor")])
    w = c.trades("0xabc")
    assert [(r["timestamp"], r["size"]) for r in w.rows] == [(1_700_000_001, 10.0), (1_700_000_002, 10.0),
                                                          (1_700_000_003, 10.0), (1_700_000_003, 11.0)]
    assert dict(w.rejects) == {"repeated_row": 1}
    assert [why for _, why in w.raw].count("repeated_row") == 1


def test_a_trade_identity_is_wallet_hash_token_condition_side_size_price_time_and_outcome():
    assert pd.trade_identity(pd.validate_trade(trade(1))[0]) == (
        "0xabc", "0xt1", "111", "0xc1", "BUY", 10.0, 0.42, 1_700_000_001, 0)


def test_a_price_point_repeated_is_kept_once_and_counted():
    pts = [{"price": 0.4, "resolution_seconds": 300, "timestamp": 1700000100},
           {"price": 0.4, "resolution_seconds": 300, "timestamp": 1700000100},
           {"price": 0.41, "resolution_seconds": 0, "timestamp": 1700000100}]
    c, t, clock = client(lambda p, q: page(pts))
    w = c.prices_history("111", 1_700_000_050, 1_700_000_200)
    assert len(w.rows) == 2 and dict(w.rejects) == {"repeated_row": 1}


def test_an_identical_resolution_row_twice_is_one_row_but_two_different_rows_are_not_trusted():
    r1 = {"condition_id": "0x1", "status": "resolved", "payouts": [1_000_000, 0], "resolved_at": "2026-02-01T00:00:00Z"}
    r2 = dict(r1, condition_id="0x2")
    r2b = dict(r2, payouts=[0, 1_000_000])
    c, t, clock = client(lambda p, q: {"data": [r1, r1, r2, r2b]})
    (batch, w, answers), = list(c.resolution_batches(["0x1", "0x2"]))
    assert answers == {"0x1": "row", "0x2": "duplicate"}
    assert dict(w.rejects) == {"repeated_row": 1}


# ------------------------------------------------------------------ times are whole seconds in a plausible span
@pytest.mark.parametrize("ts,why", [(1_700_000_000_000, "timestamp_out_of_span"), (1_500_000_000, "timestamp_out_of_span"),
                                    (1_577_836_799, "timestamp_out_of_span"), (1_577_836_800, None),
                                    (1.7e9, "bad_timestamp")])
def test_a_trade_time_must_be_whole_seconds_from_2020_to_a_day_after_the_pull_started(ts, why):
    assert pd.validate_trade(trade(0, timestamp=ts))[1] == why


def test_the_span_ends_one_day_after_the_clients_start():
    now = 1_790_000_000
    c, t, clock = client(lambda p, q: page([trade(0, timestamp=now + 86400), trade(1, timestamp=now + 86401)]),
                         wall=lambda: now)
    w = c.trades("0xabc")
    assert [r["timestamp"] for r in w.rows] == [now + 86400] and dict(w.rejects) == {"timestamp_out_of_span": 1}


def test_a_price_point_or_resolution_time_outside_the_span_is_a_counted_reject():
    assert pd.validate_price_point({"price": 0.4, "resolution_seconds": 0, "timestamp": 1_700_000_000_000})[1] == \
        "timestamp_out_of_span"
    assert pd.validate_resolution({"condition_id": "0x1", "status": "resolved", "payouts": [1_000_000, 0],
                                   "resolved_at": "2019-06-01T00:00:00Z"})[1] == "resolved_at_out_of_span"


# ------------------------------------------------------------------ rows that disagree are a conflict, not a repeat
def test_price_points_alike_in_identity_but_not_in_price_are_a_conflict_and_neither_is_kept():
    pts = [{"price": 0.4, "resolution_seconds": 300, "timestamp": 1_700_000_100},
           {"price": 0.6, "resolution_seconds": 300, "timestamp": 1_700_000_100},
           {"price": 0.5, "resolution_seconds": 300, "timestamp": 1_700_000_200},
           {"price": 0.5, "resolution_seconds": 300, "timestamp": 1_700_000_200}]
    c, t, clock = client(lambda p, q: page(pts))
    w = c.prices_history("111", 1_700_000_050, 1_700_000_300)
    assert [(r["timestamp"], r["price"]) for r in w.rows] == [(1_700_000_200, 0.5)]
    assert dict(w.rejects) == {"conflicting_row": 2, "repeated_row": 1}
    assert [why for _, why in w.raw] == ["conflicting_row", "conflicting_row", None, "repeated_row"]


def test_trades_alike_in_identity_but_not_elsewhere_are_a_conflict():
    c, t, clock = client(lambda p, q: page([trade(1), trade(1, title="another"), trade(1)]))
    w = c.trades("0xabc")
    assert len(w.rows) == 1 and dict(w.rejects) == {"conflicting_row": 1, "repeated_row": 1}


# ------------------------------------------------------------------ addendum 6: a walk page by page
def _three_pages():
    return {None: page([trade(1), trade(2), trade(3, outcome_index=7)], True, "c1"),
            "c1": page([trade(2), trade(4), trade(1, title="another")], True, "c2"),
            "c2": page([trade(4), trade(5), trade(1)])}


def test_a_walk_page_by_page_yields_each_page_alone_and_together_equals_the_whole_walk():
    pages = _three_pages()
    whole, _, _ = client(lambda p, q: pages[q.get("cursor")])
    w = whole.trades("0xabc")
    c, t, clock = client(lambda p, q: pages[q.get("cursor")])
    ids = {}                                            # the caller holds the identities (a store, in the pull)
    got = []
    for one in c.trade_pages("0xabc", ids=ids):
        assert one.pages == 1 and len(t.urls) == len(got) + 1        # one page asked at a time, as it is used
        got.append(one)
    assert len(got) == 3
    assert [r for g in got for r in g.rows] == w.rows
    assert [r for g in got for r in g.raw] == w.raw
    assert [a for g in got for a in g.row_read_at] == w.row_read_at
    assert sum((g.rejects for g in got), pd.Counter()) == w.rejects == {"repeated_row": 3, "conflicting_row": 1,
                                                                       "bad_outcome_index": 1}
    assert t.queries() == [dict(user="0xabc", start="1", limit="1000", taker_only="false"),
                           dict(user="0xabc", start="1", taker_only="false", cursor="c1"),
                           dict(user="0xabc", start="1", taker_only="false", cursor="c2")]


def test_price_pages_leave_the_conflicts_to_the_caller():
    p1 = [{"price": 0.4, "resolution_seconds": 300, "timestamp": 1_700_000_100},
          {"price": 0.5, "resolution_seconds": 300, "timestamp": 1_700_000_200}]
    p2 = [{"price": 0.6, "resolution_seconds": 300, "timestamp": 1_700_000_100},
          {"price": 0.5, "resolution_seconds": 300, "timestamp": 1_700_000_200}]
    pages = {None: page(p1, True, "c1"), "c1": page(p2)}
    c, t, clock = client(lambda p, q: pages[q.get("cursor")])
    ids, conflicted = {}, set()
    got = list(c.price_pages("111", 1_700_000_050, 1_700_000_300, ids=ids, conflicted=conflicted))
    assert [len(g.rows) for g in got] == [2, 0]
    assert conflicted == {("111", 1_700_000_100, 300)}
    assert [why for g in got for _, why in g.raw] == [None, None, "conflicting_row", "repeated_row"]


def test_page_by_page_the_cap_raises_after_that_many_pages_and_says_how_many():
    c, t, clock = client(lambda p, q: page([trade(int(q.get("cursor", "0")))], True, str(int(q.get("cursor", "0")) + 1)))
    got = []
    with pytest.raises(pd.PageCapHit) as e:
        for one in c.trade_pages("0xabc", page_cap=4, ids={}):
            got.append(one)
    assert len(got) == 4 and e.value.pages == 4 and len(t.urls) == 4


def test_page_by_page_a_repeated_cursor_or_more_without_a_cursor_raises_before_that_page_is_given():
    pages = {None: page([trade(1)], True, "c1"), "c1": page([trade(2)], True, "c1")}
    c, t, clock = client(lambda p, q: pages[q.get("cursor")])
    got = []
    with pytest.raises(pd.PolymarketDataError, match="repeated cursor"):
        for one in c.trade_pages("0xabc", ids={}):
            got.append(one)
    assert len(got) == 1                               # the page that repeated its cursor is never handed on
    c, t, clock = client(lambda p, q: page([trade(1)], True, None))
    with pytest.raises(pd.PolymarketDataError, match="no next_cursor"):
        list(c.trade_pages("0xabc", ids={}))


# ------------------------------------------------------------------ T47 addendum 8
@pytest.mark.parametrize("big", [2 ** 63, 2 ** 64, -2 ** 63 - 1])
def test_a_whole_number_too_large_for_the_store_is_a_malformed_row_with_its_own_reason(big):
    assert pd.validate_trade(dict(trade(3), size=abs(big)), "0xabc") == (None, "number_too_large_for_store")
    assert pd.validate_price_point({"price": 0.5, "resolution_seconds": abs(big), "timestamp": 1_700_000_000}) == \
        (None, "number_too_large_for_store")
    assert pd.validate_resolution({"condition_id": "c", "status": "resolved", "payouts": [0, abs(big)],
                                   "resolved_at": None}) == (None, "number_too_large_for_store")
    assert pd.validate_board_row({"rank": big, "user_id": "u", "pnl": 1.0, "volume": 1.0}) == \
        (None, "number_too_large_for_store")
    assert pd.validate_trade(dict(trade(3), size=2 ** 63 - 1), "0xabc")[1] is None    # the largest that fits


# ------------------------------------------------------------------ T52: the leaders' record (ruling R2)
def test_board_categories_are_a_closed_set_sent_as_the_category_parameter():
    assert pd.BOARD_CATEGORIES == ("sports", "crypto")
    seen = []

    def h(p, q):
        seen.append(q)
        return page([board_row(i + 1, "0x%02d" % i) for i in range(5)])
    c, t, clock = client(h)
    for cat in pd.BOARD_CATEGORIES:
        w = c.leaderboard("all", rows=5, category=cat)
        assert len(w.rows) == 5
    c.leaderboard("all", rows=5)                                    # None: the overall board, as before
    assert seen == [{"time_period": "all", "sort_by": "PNL", "category": "sports", "limit": "5"},
                    {"time_period": "all", "sort_by": "PNL", "category": "crypto", "limit": "5"},
                    {"time_period": "all", "sort_by": "PNL", "limit": "5"}]


@pytest.mark.parametrize("cat", ["overall", "Sports", "SPORTS", "esports", "politics", "", "sports&x=1", 1, True])
def test_any_other_category_is_refused_before_a_request(cat):
    c, t, clock = client(lambda p, q: page([]))
    with pytest.raises(ValueError):
        c.leaderboard("all", rows=5, category=cat)
    assert t.urls == []


@pytest.mark.parametrize("cat", ["sports", "crypto"])
def test_a_category_board_address_matches_the_unchanged_allow_list(monkeypatch, cat):
    seen = []
    c, t, clock = client(lambda p, q: page([board_row(1, "0xa")]))
    c.leaderboard("all", rows=5, category=cat)
    assert len(t.urls) == 1 and pd._allowed(t.urls[0]) and "category=" + cat in t.urls[0]
    fake = fake_network(monkeypatch, body=json.dumps(page([])).encode())
    pd.urllib_get(t.urls[0])                                          # the real transport sends it unchanged
    assert [r[2] for r in fake.requests] == t.urls
    for other in (t.urls[0].replace("data-api.polymarket.com", "data-api.polymarket.co"),
                  t.urls[0].replace("/v2/leaderboard", "/v1/leaderboard"),
                  t.urls[0].replace("/v2/leaderboard", "/v2/leaderboard.json"),
                  t.urls[0] + "#x", t.urls[0].replace("https://", "http://")):
        assert not pd._allowed(other)
        with pytest.raises(pd.PolymarketDataError):
            pd.urllib_get(other)
    assert len(fake.requests) == 1


def test_a_position_keeps_title_outcome_end_date_and_current_price_for_information():
    good, why = pd.validate_position(position(0, title="Who wins?", outcome="Yes", end_date="2026-12-01",
                                              current_price=0.42), "0xabc")
    assert why is None
    assert (good["title"], good["outcome"], good["end_date"], good["current_price"]) == \
        ("Who wins?", "Yes", "2026-12-01", 0.42)
    assert good["unread"] == ()


@pytest.mark.parametrize("field,value", [("title", None), ("title", ""), ("title", 5), ("title", "x" * 501),
                                         ("outcome", None), ("outcome", ""), ("outcome", "y" * 201),
                                         ("end_date", None), ("end_date", "2026-13-01"), ("end_date", "2026-1-01"),
                                         ("end_date", "1970-01-01"), ("end_date", "2026-12-01T00:00:00Z"),
                                         ("current_price", None), ("current_price", "0.5"), ("current_price", True),
                                         ("current_price", 1.5), ("current_price", -0.1),
                                         ("current_price", float("nan")), ("current_price", float("inf"))])
def test_a_missing_or_malformed_information_field_is_none_and_counted_never_a_reject(field, value):
    row = position(0)
    if value is None:
        del row[field]
    else:
        row[field] = value
    good, why = pd.validate_position(row, "0xabc")
    assert why is None and good[field] is None and good["unread"] == (field,)
    c, t, clock = client(lambda p, q: page([row, position(1)]))
    w = c.positions("0xabc")
    assert len(w.rows) == 2 and sum(w.rejects.values()) == 0 and dict(w.unread) == {field: 1}


def test_the_longest_bounded_strings_and_the_price_ends_are_kept():
    good, why = pd.validate_position(position(0, title="x" * 500, outcome="y" * 200, current_price=0), "0xabc")
    assert why is None and good["unread"] == () and good["current_price"] == 0.0
    assert pd.validate_position(position(0, current_price=1), "0xabc")[0]["current_price"] == 1.0


def test_a_first_entry_of_zero_is_none():
    assert pd.validate_position(position(0, first_entry_at=0), "0xabc")[0]["first_entry_at"] is None
    assert pd.validate_position(position(0, first_entry_at=1_700_000_000), "0xabc")[0]["first_entry_at"] == \
        1_700_000_000


def test_the_money_fields_and_ids_still_decide_a_reject():
    c, t, clock = client(lambda p, q: page([position(0, current_size="1"), position(1, token_id=""), position(2)]))
    w = c.positions("0xabc")
    assert [r["token_id"] for r in w.rows] == ["t2"] and dict(w.rejects) == {"bad_current_size": 1, "bad_token_id": 1}


def test_positions_drop_exact_repeats_and_conflicts_by_wallet_and_token_counted():
    rows = [position(0), position(0), position(1), position(1, current_size=2.0), position(2, proxy_wallet="0xABC"),
            position(2, proxy_wallet="0xabc")]
    c, t, clock = client(lambda p, q: page(rows[:3], True, "c1") if "cursor" not in q else page(rows[3:]))
    w = c.positions("0xabc")
    assert [r["token_id"] for r in w.rows] == ["t0"]                 # t1 in conflict across pages, t2 alike folded
    assert dict(w.rejects) == {"repeated_row": 1, "conflicting_row": 4}
    assert pd.position_identity(pd.validate_position(position(5, proxy_wallet="0xAbC"))[0]) == ("0xabc", "t5")


def test_position_pages_hand_one_page_at_a_time_with_identity_across_pages():
    rows = [position(0), position(1), position(1), position(2, current_size=3.0), position(2)]
    c, t, clock = client(lambda p, q: page(rows[:2], True, "c1") if "cursor" not in q else page(rows[2:]))
    ids, conflicted = {}, set()
    got = list(c.position_pages("0xabc", page_cap=20, ids=ids, conflicted=conflicted))
    assert [len(w.raw) for w in got] == [2, 3] and conflicted == {("0xabc", "t2")}
    assert sum(w.rejects["repeated_row"] for w in got) == 1
    assert t.queries()[0] == {"user": "0xabc", "limit": "1000"}
    c, t, clock = client(lambda p, q: page([position(0)], True, "c%d" % len(t.urls)))
    with pytest.raises(pd.PageCapHit):
        list(c.position_pages("0xabc", page_cap=3, ids={}, conflicted=set()))
    assert len(t.urls) == 3


# ------------------------------------------------------------------ T52 addendum 2, item 1: no value stops a read
def _deep(n, kind=list):
    """A value nested `n` deep, built without recursion (deeper than json.dumps or repr can walk)."""
    top = cur = kind()
    for _ in range(n):
        nxt = kind()
        if kind is list:
            cur.append(nxt)
        else:
            cur["k"] = nxt
        cur = nxt
    return top


BIG = 10 ** 400                                  # a whole number of 401 digits: beyond a float's range
HUGE_STR = "x" * 10_000_000
HOSTILE = [BIG, -BIG, 2 ** 63, True, False, None, 0, -1, 1e308, float("nan"), float("inf"),
           "", "\ud800", "a\x00b", "\udfff" * 3, HUGE_STR, [], {}, [1, 2], [BIG, BIG], {"a": BIG},
           _deep(50_000), _deep(50_000, dict)]
VALIDATORS = {
    "board": (lambda r: pd.validate_board_row(r), board_row(1, "0xabc")),
    "trade": (lambda r: pd.validate_trade(r, "0xabc", 1_800_000_000), trade(1)),
    "trade_any_wallet": (lambda r: pd.validate_trade(r), trade(1)),
    "position": (lambda r: pd.validate_position(r, "0xabc"), position(1)),
    "position_any_wallet": (lambda r: pd.validate_position(r), position(1)),
    "resolution": (lambda r: pd.validate_resolution(r, 1_800_000_000), resolution("0xc1")),
    "price_point": (lambda r: pd.validate_price_point(r, 1_800_000_000),
                    {"price": 0.5, "resolution_seconds": 60, "timestamp": 1_700_000_000}),
}


def test_is_number_never_raises_and_every_whole_number_is_a_number_whatever_its_size():
    for v in HOSTILE:
        assert pd.is_number(v) in (True, False)
    assert pd.is_number(BIG) is True and pd.is_number(-BIG) is True        # was an OverflowError
    for v in (True, False, None, "1", float("nan"), float("inf"), [1], {}, "\ud800"):
        assert pd.is_number(v) is False


@pytest.mark.parametrize("name", sorted(VALIDATORS))
def test_no_validator_raises_whatever_value_sits_in_whatever_field(name):
    check, base = VALIDATORS[name]
    fields = list(base) + ["an_extra_field"]
    for f in fields:
        for v in HOSTILE:
            good, why = check(dict(base, **{f: v}))
            assert (good is None) != (why is None), (f, type(v).__name__)
    for v in HOSTILE:
        assert check(v)[1] == "not_an_object" or isinstance(v, dict)
        if isinstance(v, dict):
            assert check(v)[0] is None


@pytest.mark.parametrize("name", sorted(VALIDATORS))
def test_a_walk_keeps_counting_whatever_row_the_service_sends(name):
    """Walk.add (the validator, then the identity and the whole-row signature) never raises either."""
    check, base = VALIDATORS[name]
    rows = [dict(base, an_extra_field=v) for v in HOSTILE] + [dict(base, an_extra_field=_deep(50_000))] * 2
    w, ids, conflicted = pd.Walk(), {}, set()
    for r in rows:
        w.add(r, check, "t", identity=lambda g: "one", ids=ids, conflicted=conflicted)
    assert len(w.raw) == len(rows)


def test_a_whole_number_beyond_a_floats_range_takes_the_too_large_reason_where_that_rule_applies():
    tl = "number_too_large_for_store"
    for big in (BIG, -BIG):
        assert pd.validate_trade(dict(trade(3), size=abs(big)), "0xabc") == (None, tl)
        assert pd.validate_board_row(board_row(1, "u", pnl=big)) == (None, tl)
        assert pd.validate_board_row(dict(board_row(1, "u"), volume=big)) == (None, tl)
        assert pd.validate_board_row(dict(board_row(1, "u"), rank=big)) == (None, tl)
        assert pd.validate_price_point({"price": 0.5, "resolution_seconds": abs(big),
                                        "timestamp": 1_700_000_000}) == (None, tl)
        assert pd.validate_resolution(resolution("c", payouts=[0, abs(big)])) == (None, tl)
    # where a price must lie in [0, 1] the number is simply out of it, as 2**63 is today
    assert pd.validate_trade(dict(trade(3), price=BIG), "0xabc") == (None, "bad_price")
    assert pd.validate_price_point({"price": BIG, "resolution_seconds": 1, "timestamp": 1_700_000_000}) == \
        (None, "bad_price")
    assert pd.validate_trade(dict(trade(3), timestamp=BIG), "0xabc") == (None, "timestamp_out_of_span")


@pytest.mark.parametrize("field", pd.POSITION_NUM)
def test_a_position_money_field_beyond_a_floats_range_is_that_fields_reject(field):
    assert pd.validate_position(position(0, **{field: BIG}), "0xabc") == (None, "bad_" + field)
    assert pd.validate_position(position(0, **{field: -BIG}), "0xabc") == (None, "bad_" + field)
    assert pd.validate_position(position(0, **{field: 2 ** 63}), "0xabc")[1] is None     # in range: as today


def test_a_401_digit_current_price_is_none_and_counted_never_an_error():
    good, why = pd.validate_position(position(0, current_price=BIG), "0xabc")
    assert why is None and good["current_price"] is None and good["unread"] == ("current_price",)
    c, t, clock = client(lambda p, q: b'{"data": [' + json.dumps(position(0)).encode()[:-1] +
                         b', "current_price": ' + b"9" * 401 + b'}], "pagination": {"has_more": false}}')
    w = c.positions("0xabc")
    assert len(w.rows) == 1 and dict(w.unread) == {"current_price": 1}


BAD_TEXT = ["\ud800", "ok\udc00", "a\x00b", "\x00"]


@pytest.mark.parametrize("value", BAD_TEXT)
@pytest.mark.parametrize("field", ["title", "outcome"])
def test_an_information_string_that_cannot_be_stored_or_printed_is_none_and_counted(field, value):
    good, why = pd.validate_position(position(0, **{field: value}), "0xabc")
    assert why is None and good[field] is None and good["unread"] == (field,)
    good["title" if field == "outcome" else "outcome"].encode("utf-8")


def test_a_lone_surrogate_title_through_the_wire_is_none_and_counted():
    body = json.dumps(page([position(0, title="\ud800"), position(1)])).encode()     # JSON escapes it as \ud800
    assert b"\\ud800" in body
    c, t, clock = client(lambda p, q: body)
    w = c.positions("0xabc")
    assert len(w.rows) == 2 and w.rows[0]["title"] is None and dict(w.unread) == {"title": 1}


@pytest.mark.parametrize("value", BAD_TEXT)
def test_a_position_string_that_decides_a_reject_and_cannot_be_stored_is_that_fields_reject(value):
    for f in ("proxy_wallet", "token_id", "condition_id", "status"):
        assert pd.validate_position(position(0, **{f: value})) == (None, "bad_" + f)


@pytest.mark.parametrize("value", BAD_TEXT)
def test_a_kept_position_string_that_cannot_be_stored_is_none(value):
    good, why = pd.validate_position(position(0, event_slug=value, event_id=value, opposite_token_id=value))
    assert why is None and (good["event_slug"], good["event_id"], good["opposite_token_id"]) == (None, None, None)


@pytest.mark.parametrize("value", BAD_TEXT)
def test_the_string_rule_is_for_positions_only_other_rows_answer_as_before(value):
    """Addendum 4: trades, board rows and resolutions keep such strings exactly as the reader always did."""
    for f in ("proxy_wallet", "token_id", "condition_id", "event_slug", "transaction_hash", "title", "slug",
              "outcome"):
        good, why = pd.validate_trade(dict(trade(1), **{f: value}))
        assert why is None and good[f] == value, f
    assert pd.validate_board_row(board_row(1, value))[0]["user_id"] == value
    assert pd.validate_resolution(resolution(value))[0]["condition_id"] == value
    assert pd.validate_resolution(resolution("c", status=value))[0]["status"] == value


F747911 = "f747911"


def _reader_at(commit, tmp_path):
    """The reader as it was at `commit`, from this repository's history, imported under a name of its own."""
    import importlib.util
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    got = subprocess.run(["git", "-C", str(root), "show", "%s:valedia_trader/venues/polymarket_data.py" % commit],
                         capture_output=True, text=True)
    if got.returncode != 0:                  # never a skip: a missing reference is a failure that says so
        pytest.fail("the reader at commit %s cannot be read from this checkout's history (git show: %s); this "
                    "property needs it" % (commit, got.stderr.strip()[:200]), pytrace=False)
    f = tmp_path / ("reader_%s.py" % commit)
    f.write_text(got.stdout)
    name = "reader_at_%s" % commit
    spec = importlib.util.spec_from_file_location(name, f)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


SEED_VALUES = [0, 1, -1, 0.0, 0.5, 1.0, 1.5, -0.1, 1e308, 2 ** 53, 2 ** 63 - 1, 2 ** 63, -2 ** 63 - 1, 10 ** 30,
               float("nan"), float("inf"), True, False, None, "", "x", "BUY", "SELL", "resolved", "0xabc", "0xABC",
               "0xother", "Yes", "2026-02-01T10:00:00Z", "2026-02-01T10:00:00+01:00", "2026-02-01T10:00:00",
               "2019-01-01T00:00:00Z", "caf\u00e9 \U0001f600", "z" * 3000, [], {}, [1], [0, 1], [1_000_000, 0],
               [1, 2, 3], [-1, 0], [2 ** 63, 0], {"a": 1}, [[[1]]], 1_700_000_000, 1_577_836_799, 60, 1700,
               "\ud800", "ok\udc00", "a\x00b", "\x00", "2026-02-01T10:00:00\x00Z", "2026-02-01T10:00:00\ud800Z",
               10 ** 400, -10 ** 400]


def _seeded_rows(base, rng, n):
    rows = [dict(base, **{f: v}) for f in list(base) + ["extra"] for v in SEED_VALUES]
    for _ in range(n):
        r = dict(base)
        for _ in range(rng.randint(1, 4)):
            f = rng.choice(list(base) + ["extra"])
            if rng.random() < 0.1:
                r.pop(f, None)
            else:
                r[f] = rng.choice(SEED_VALUES)
        rows.append(r)
    return rows + list(SEED_VALUES)


def _answer(f, *a):
    try:
        return "ok", json.dumps(f(*a), sort_keys=True, default=repr)
    except Exception as e:
        return "raise", type(e).__name__


def test_property_trades_price_points_resolutions_and_board_rows_answer_as_the_reader_at_f747911(tmp_path):
    import random
    old = _reader_at(F747911, tmp_path)
    rng = random.Random(20261117)
    bases = {
        "board": (lambda m: m.validate_board_row, {"rank": 1, "user_id": "0xabc", "pnl": 5.0, "volume": 1.0}),
        "trade": (lambda m: (lambda r: m.validate_trade(r, "0xabc", 1_800_000_000)), trade(1)),
        "trade_any": (lambda m: m.validate_trade, trade(1)),
        "resolution": (lambda m: (lambda r: m.validate_resolution(r, 1_800_000_000)), resolution("0xc1")),
        "price_point": (lambda m: (lambda r: m.validate_price_point(r, 1_800_000_000)),
                        {"price": 0.5, "resolution_seconds": 60, "timestamp": 1_700_000_000}),
    }
    compared = differ = 0
    for name, (mk, base) in bases.items():
        fo, fn = mk(old), mk(pd)
        for r in _seeded_rows(base, rng, 4000):
            a = _answer(fo, r)
            if a[0] == "raise":                  # the old reader crashed here: addendum 2 makes it a counted row
                assert _answer(fn, r)[0] == "ok"
                continue
            compared += 1
            differ += _answer(fn, r) != a
    assert compared > 20_000 and differ == 0, (compared, differ)
    for v in SEED_VALUES:
        a = _answer(old.is_number, v)
        assert a[0] == "raise" or _answer(pd.is_number, v) == a


def test_property_every_walk_but_positions_answers_as_the_reader_at_f747911(tmp_path):
    import random
    old = _reader_at(F747911, tmp_path)
    rng = random.Random(4)
    tr, pp, br = trade(1), {"price": 0.5, "resolution_seconds": 60, "timestamp": 1_700_000_000}, board_row(1, "0xa")
    compared = differ = 0
    for world in range(60):
        rows = {"/v2/trades": [dict(tr, transaction_hash="0xt%d" % rng.randint(0, 9)) for _ in range(30)],
                "/v2/prices-history": [dict(pp, timestamp=1_700_000_000 + 60 * rng.randint(0, 9)) for _ in range(30)],
                "/v2/leaderboard": [dict(br, rank=i + 1, user_id="0x%d" % i) for i in range(30)]}
        for path in rows:
            rows[path] = [r if rng.random() < 0.6 else _seeded_rows(r, rng, 1)[-len(SEED_VALUES) - 1]
                          for r in rows[path]]
            rows[path] += [dict(rows[path][0])] if rows[path] else []
        res = [resolution("0xc%d" % i) if rng.random() < 0.7 else
               _seeded_rows(resolution("0xc%d" % i), rng, 1)[-len(SEED_VALUES) - 1] for i in range(45)]

        def handler(p, q):
            if p == "/v2/resolutions":
                return {"data": res}
            half = len(rows[p]) // 2
            return page(rows[p][:half], True, "c1") if "cursor" not in q else page(rows[p][half:])

        def walks(m):
            c = m.PolymarketData(FakeTransport(handler), sleep=lambda s: None, clock=lambda: 0.0,
                                 wall=lambda: 1_790_000_000.0, now=lambda: "2026-11-17T12:00:00+00:00")
            out = []
            for call in (lambda: c.trades("0xabc"), lambda: c.prices_history("111", 1, 2),
                         lambda: c.leaderboard("all", rows=50),
                         lambda: list(c.resolution_batches(["0xc%d" % i for i in range(45)]))):
                try:
                    got = call()                 # the fields a walk had at f747911 (its repr gained `unread`)
                    ws = [(x[1], x[0], x[2]) for x in got] if isinstance(got, list) else [(got, None, None)]
                    out.append(("ok", json.dumps([[w.rows, w.raw, sorted(w.rejects.items()), w.pages, w.read_at,
                                                   batch, answers] for w, batch, answers in ws],
                                                 sort_keys=True, default=repr)))
                except Exception as e:
                    out.append(("raise", type(e).__name__))
            return out
        for a, b in zip(walks(old), walks(pd)):
            if a[0] == "raise":
                continue
            compared += 1
            differ += a != b
    assert compared > 150 and differ == 0, (compared, differ)
def test_a_resolution_row_whose_condition_is_not_a_string_is_an_answer_not_an_error():
    rows = [resolution("0x1"), {"condition_id": ["0x2"], "status": "r"}, {"condition_id": {"a": 1}, "status": "r"}]
    c, t, clock = client(lambda p, q: {"data": rows})
    (batch, w, answers), = list(c.resolution_batches(["0x1", "0x2"]))
    assert answers == {"0x1": "row", "0x2": "no_row"} and dict(w.rejects) == {"bad_condition_id": 2}


def test_the_store_check_looks_inside_lists_at_any_depth_without_recursion():
    assert pd._fits_store([[0, [2 ** 63]]]) is False and pd._fits_store([1, [2, [-2 ** 63 - 1]]]) is False
    assert pd._fits_store([[0, [2 ** 63 - 1]], _deep(50_000)]) is True


# ------------------------------------------------------------------ T47e: an id the service refuses (fourth amendment)
B55BC88 = "b55bc88"                                  # the reader before T47e: the reference for "nothing else changes"
IDS = ["0x%02d" % i for i in range(20)]


def refusing(bad=(), no_row=(), fail=None):
    """A stand-in /v2/resolutions: HTTP 400 for any request holding an id of `bad` (the service's words for it are
    not kept by the transport); `fail(conds)` may answer another failure first; else one row an id but `no_row`."""
    def h(p, q):
        assert p == "/v2/resolutions"
        conds = q["condition"].split(",")
        if fail is not None:
            ans = fail(conds)
            if ans is not None:
                return ans
        if set(conds) & set(bad):
            return pd.HttpError(400, "invalid resolution condition id")
        return {"data": [resolution(cd) for cd in conds if cd not in no_row]}
    return h


def _asked(t):
    return [q["condition"].split(",") for q in t.queries()]


def test_a_refused_twenty_is_asked_again_in_halves_down_to_single_ids_and_a_refused_id_gets_its_own_answer():
    told = []
    c, t, clock = client(refusing(bad={"0x03", "0x11"}, no_row={"0x07"}))
    out = list(c.resolution_batches(IDS, narrowed=told.append))
    assert [b for b, _, _ in out] == [IDS[0:2], ["0x02"], ["0x03"], ["0x04"], IDS[5:10], ["0x10"], ["0x11"],
                                      IDS[12:15], IDS[15:20]]                 # the first half first, depth first
    answers = {k: v for _, _, a in out for k, v in a.items()}
    assert sum(len(b) for b, _, _ in out) == 20 and sorted(answers) == IDS        # each id answered once
    assert answers["0x03"] == answers["0x11"] == pd.REFUSED == "refused"
    assert pd.REFUSED not in ("row", "no_row", "rejected", "row_and_reject", "duplicate")
    assert answers["0x07"] == "no_row" and {answers[k] for k in IDS if k not in ("0x03", "0x07", "0x11")} == {"row"}
    for b, w, a in out:
        if a[b[0]] == pd.REFUSED:
            assert len(b) == 1 and w.rows == [] and w.raw == [] and w.pages == 1 and len(w.read_at) == 1
    assert len(t.urls) == 17 and c.requests == 17                                  # 1 + 16 more: at most 38 more
    assert told == [{"asked": 20, "answered": 18, "refused": 2, "requests": 16, "finished": True}]
    assert all(pd._allowed(u) for u in t.urls)                                     # the allow-list, every request
    assert all(set(a) <= set(IDS) for a in _asked(t))                              # every id exactly as given
    assert {x for a in _asked(t) for x in a} == set(IDS)
    assert len(clock.sleeps) == 16 and all(abs(s - (1 / 3 + 0.01)) < 1e-9 for s in clock.sleeps)   # the pace


def test_the_more_requests_a_refused_request_costs_are_at_most_2n_minus_2_and_reach_it_when_every_id_is_refused():
    import random
    worst = 0
    for seed in range(300):
        rng = random.Random(seed)
        n = 20 if seed == 0 else rng.randint(1, 20)
        ids = ["0xc%03d" % i for i in rng.sample(range(1000), n)]                # distinct ids
        bad = set(ids) if seed % 10 == 0 else {x for x in ids if rng.random() < rng.choice([0.0, 0.05, 0.2, 0.5])}
        told = []
        c, t, clock = client(refusing(bad=bad))
        out = list(c.resolution_batches(ids, narrowed=told.append))
        answers = {k: v for _, _, a in out for k, v in a.items()}
        assert sorted(answers) == sorted(ids) and sum(len(b) for b, _, _ in out) == n
        assert {k for k, v in answers.items() if v == pd.REFUSED} == bad
        more = len(t.urls) - 1
        if not bad:
            assert more == 0 and told == []
            continue
        assert more <= 2 * n - 2, (seed, n, len(bad), more)
        assert told == [{"asked": n, "answered": n - len(bad), "refused": len(bad), "requests": more,
                         "finished": True}]
        if bad == set(ids):
            assert more == 2 * n - 2
            worst = max(worst, n)
    assert worst == 20                                                     # twenty refused: 38 more requests


def test_a_single_id_refused_is_answered_refused_with_no_more_request_and_an_answer_after_it_is_still_read():
    told = []
    c, t, clock = client(refusing(bad={"0xbad"}))
    out = list(c.resolution_batches(IDS + ["0xbad", "0xok"], narrowed=told.append))
    assert [(b, a) for b, _, a in out][1:] == [(["0xbad"], {"0xbad": "refused"}), (["0xok"], {"0xok": "row"})]
    assert _asked(t) == [IDS, ["0xbad", "0xok"], ["0xbad"], ["0xok"]]       # twenty ids a request, then halves
    assert told == [{"asked": 2, "answered": 1, "refused": 1, "requests": 2, "finished": True}]
    told.clear()
    c, t, clock = client(refusing(bad={"0xbad"}))
    assert list(c.resolution_batches(["0xbad"], narrowed=told.append))[0][2] == {"0xbad": "refused"}
    assert len(t.urls) == 1 and told == [{"asked": 1, "answered": 0, "refused": 1, "requests": 0, "finished": True}]


OTHER_FAILURES = {                                   # each failure in the error class of the reader `m` asking
    "429": lambda m=pd: m.HttpError(429, "u"), "503": lambda m=pd: m.HttpError(503, "u"),
    "404": lambda m=pd: m.HttpError(404, "u"), "302": lambda m=pd: m.HttpError(302, "u"),
    "reset": lambda m=pd: ConnectionResetError("reset by peer"), "timeout": lambda m=pd: socket.timeout("timed out"),
    "not_json": lambda m=pd: b"<html>", "paged": lambda m=pd: page([], True, "c"),
    "no_data": lambda m=pd: {"rows": []}}


@pytest.mark.parametrize("kind", sorted(OTHER_FAILURES))
def test_any_other_first_failure_is_not_narrowed_and_raises_as_the_reader_before_did(kind, tmp_path):
    old = _reader_at(B55BC88, tmp_path)
    got = []
    for m in (old, pd):
        clock = FakeClock()
        t = FakeTransport(refusing(bad={"0x03"}, fail=lambda conds, m=m: OTHER_FAILURES[kind](m)))
        c = m.PolymarketData(t, sleep=clock.sleep, clock=clock)
        told, yielded = [], []
        kw = {"narrowed": told.append} if m is pd else {}
        with pytest.raises(Exception) as e:
            for x in c.resolution_batches(IDS + ["0x20"], **kw):
                yielded.append(x)
        got.append((type(e.value).__name__, str(e.value), t.urls, clock.sleeps, yielded))
        assert told == []
    assert got[0] == got[1]
    assert got[1][4] == [] and len(got[1][2]) in (1, 5)                     # one request, or the reader's five tries


@pytest.mark.parametrize("kind", ["503", "404", "reset", "timeout", "not_json", "paged"])
def test_another_failure_met_while_narrowing_raises_and_the_ids_not_yet_answered_have_no_answer(kind):
    told, out = [], []
    c, t, clock = client(refusing(bad={"0x03"}, fail=lambda conds: OTHER_FAILURES[kind]() if "0x15" in conds
                                                                    and len(conds) < 20 else None))
    with pytest.raises((pd.PolymarketDataError, pd.HttpError)):
        for x in c.resolution_batches(IDS + ["0xnext"], narrowed=told.append):
            out.append(x)
    answers = {k: v for _, _, a in out for k, v in a.items()}
    assert sorted(answers) == IDS[:10] and answers["0x03"] == pd.REFUSED       # the first half, then the failure
    assert told == [{"asked": 20, "answered": 9, "refused": 1, "requests": 10, "finished": False}]
    assert not any("0xnext" in u for u in t.urls)                            # the next twenty is never asked


def test_with_no_refused_id_the_reader_asks_and_answers_exactly_as_before(tmp_path):
    import random
    old = _reader_at(B55BC88, tmp_path)
    for seed in range(40):
        rng = random.Random(seed)
        ids = ["0xc%d" % i for i in range(rng.randint(1, 65))]
        rows = {x: [resolution(x)] if rng.random() < 0.6 else [] if rng.random() < 0.5 else
                [resolution(x), resolution(x, payouts=[0, 1_000_000])] if rng.random() < 0.5 else
                [resolution(x, payouts="bad")] for x in ids}

        def h(p, q):
            return {"data": [r for x in q["condition"].split(",") for r in rows[x]]}
        got = []
        for m in (old, pd):
            t = FakeTransport(h)
            c = m.PolymarketData(t, sleep=lambda s: None, clock=lambda: 0.0, now=lambda: "2026-11-18T00:00:00+00:00",
                                 wall=lambda: 1_790_000_000.0)
            got.append((t.urls, json.dumps([[b, w.rows, w.raw, sorted(w.rejects.items()), w.pages, w.read_at, a]
                                            for b, w, a in c.resolution_batches(ids)], sort_keys=True, default=repr)))
        assert got[0] == got[1], seed
