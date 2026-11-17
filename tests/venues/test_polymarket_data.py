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
