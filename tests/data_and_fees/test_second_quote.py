"""Tests for the second price source (Cboe delayed quotes): the reader, the comparison, the probe.

No test touches the network: the fetch function is injected and faked, and the redirect rules are exercised through a
fake HTTPS handler. The two real answers kept below were read on 2026-11-13 (one request each), trimmed of the
volatility fields."""
import io
import json
import math
import functools
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import urllib.response
from datetime import datetime, timezone
from email.message import Message
from fractions import Fraction
from zoneinfo import ZoneInfo

import pytest

from valedia_trader.data_and_fees import second_quote as sq

NOW = datetime(2026, 11, 13, 21, 20, 3, tzinfo=timezone.utc).timestamp()          # 16:20:03 in New York
CLOCK = lambda: NOW                                                                # noqa: E731
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)
PNOW = datetime(2026, 11, 13, 21, 29, 0, tzinfo=timezone.utc).timestamp()         # 16:29:00: the real answers' trade is 29 min old
PCLOCK = lambda: PNOW                                                              # noqa: E731

REAL_SPY = (b'{"timestamp": "2026-11-13 21:28:34", "data": {"symbol": "SPY", "security_type": "stock", "exchange_id": 3, '
            b'"current_price": 752.54, "price_change": -2.75, "price_change_percent": -0.3632, "bid": 752.5, "ask": 752.55, '
            b'"bid_size": 480, "ask_size": 640, "open": 760.05, "high": 760.12, "low": 747.88, "close": 754.37, '
            b'"prev_day_close": 757.12, "volume": 29900315, "seqno": 50597840375, "last_trade_time": "2026-11-13T16:00:00", '
            b'"tick": "up"}, "symbol": "SPY"}')
REAL_XLE = (b'{"timestamp": "2026-11-13 21:28:35", "data": {"symbol": "XLE", "security_type": "stock", "exchange_id": 3, '
            b'"current_price": 64.83, "price_change": 0.63, "price_change_percent": 0.9793, "bid": 64.76, "ask": 64.96, '
            b'"bid_size": 2300, "ask_size": 2500, "open": 64.91, "high": 64.965, "low": 64.155, "close": 64.96, '
            b'"prev_day_close": 64.33, "volume": 17648505, "seqno": 50588982624, "last_trade_time": "2026-11-13T15:59:59", '
            b'"tick": "up"}, "symbol": "XLE"}')
URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/%s.json"


class Fake:
    """A fetch function that answers from a list (an exception in the list is raised) and records its calls."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, BaseException):
            raise a
        return a


def answer(symbol="SPY", **fields):
    data = {"symbol": symbol, "current_price": 100.0, "bid": 99.9, "ask": 100.1, "last_trade_time": "2026-11-13T16:00:00"}
    data.update(fields)
    return json.dumps({"timestamp": "2026-11-13 21:28:34", "data": data, "symbol": symbol}).encode()


def quote(symbol="SPY", raw=None, sleeps=None):
    return sq.cboe_quote(symbol, fetch=Fake(raw if raw is not None else answer(symbol)), clock=CLOCK,
                         sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


# ---- the reader: the real layout

def test_the_real_spy_answer_is_read_into_the_plain_result():
    fake = Fake(REAL_SPY)
    out = sq.cboe_quote("SPY", fetch=fake, clock=CLOCK, sleep=lambda s: None)
    assert out == {"symbol": "SPY", "bid": 752.5, "ask": 752.55, "last": 752.54, "last_trade_time": "2026-11-13T16:00:00",
                   "snapshot_at": datetime(2026, 11, 13, 21, 28, 34, tzinfo=timezone.utc).timestamp(),
                   "fetched_at": NOW, "source": "cboe-delayed"}
    assert fake.calls == [(URL % "SPY", 5.0)]                  # one request, the symbol alone in the path, a 5 s timeout


def test_the_real_sector_fund_answer_is_read():
    out = sq.cboe_quote("XLE", fetch=Fake(REAL_XLE), clock=CLOCK, sleep=lambda s: None)
    assert (out["symbol"], out["bid"], out["ask"], out["last"], out["last_trade_time"]) == \
        ("XLE", 64.76, 64.96, 64.83, "2026-11-13T15:59:59")


def test_integers_are_accepted_and_become_floats():
    out = quote(raw=answer(current_price=100, bid=99, ask=101))
    assert out["last"] == 100.0 and type(out["last"]) is float and type(out["bid"]) is float


def test_symbols_with_a_class_suffix_are_asked_for_by_name():
    for sym in ("BRK.B", "BRK-B"):
        fake = Fake(answer(sym))
        assert sq.cboe_quote(sym, fetch=fake, clock=CLOCK, sleep=lambda s: None)["symbol"] == sym
        assert fake.calls == [(URL % sym, 5.0)]


# ---- the reader: partial answers keep what is usable and nothing else

def test_a_missing_or_unusable_field_is_none_and_the_rest_stays():
    assert quote(raw=answer(current_price="752.4"))["last"] is None          # a string is not a number
    assert quote(raw=answer(current_price=True))["last"] is None             # nor is a boolean
    out = quote(raw=answer(ask=-1.0))
    assert out["ask"] is None and out["bid"] == 99.9 and out["last"] == 100.0
    out = quote(raw=answer(bid=0))
    assert out["bid"] is None and out["ask"] == 100.1
    out = quote(raw=answer(last_trade_time="yesterday"))
    assert out["last_trade_time"] is None and out["last"] == 100.0
    out = quote(raw=answer(last_trade_time=None))
    assert out["last_trade_time"] is None


def test_no_last_and_no_bid_ask_pair_is_no_result():
    assert quote(raw=answer(current_price=None, bid=None)) is None
    assert quote(raw=answer(current_price=None, ask=-3)) is None
    assert quote(raw=answer(current_price=None)) is not None                  # a bid and an ask remain


def test_a_last_price_alone_is_a_result():
    out = quote(raw=answer(bid=None, ask=None))
    assert out["last"] == 100.0 and out["bid"] is None and out["ask"] is None


# ---- the reader: hostile answers

BIG = 10 * 1024 * 1024


def _hostile():
    base = json.loads(answer())
    out = {}

    def put(name, raw):
        out[name] = raw if isinstance(raw, bytes) else json.dumps(raw).encode()

    def with_data(**f):
        d = dict(base["data"])
        d.update(f)
        return {"timestamp": "t", "data": d, "symbol": "SPY"}

    for name, bad in {"string": "752.4", "list": [1], "dict": {"a": 1}, "bool": False, "null": None, "negative": -5.0,
                      "zero": 0, "huge": 1.0000001e7, "enormous": 1e300}.items():
        put("every-number-" + name, with_data(current_price=bad, bid=bad, ask=bad))
    put("nan-literal", answer().replace(b"100.0", b"NaN").replace(b"99.9", b"NaN").replace(b"100.1", b"NaN"))
    put("infinity-literal", answer().replace(b"100.0", b"Infinity").replace(b"99.9", b"-Infinity").replace(b"100.1", b"Infinity"))
    put("overflow-literal", answer().replace(b"100.0", b"1e999").replace(b"99.9", b"-1e999").replace(b"100.1", b"1e999"))
    put("giant-integer", answer().replace(b"100.0", b"1" + b"0" * 400).replace(b"99.9", b"1" + b"0" * 400).replace(b"100.1", b"-1" + b"0" * 400))
    put("another-symbol-in-data", with_data(symbol="QQQ"))
    put("another-symbol-on-top", {"timestamp": "t", "data": base["data"], "symbol": "QQQ"})
    put("lowercase-symbol", with_data(symbol="spy"))
    put("symbol-not-a-string", with_data(symbol=["SPY"]))
    put("no-symbol", {"timestamp": "t", "data": {k: v for k, v in base["data"].items() if k != "symbol"}})
    put("data-is-a-string", {"timestamp": "t", "data": "SPY", "symbol": "SPY"})
    put("data-is-a-list", {"timestamp": "t", "data": [base["data"]], "symbol": "SPY"})
    put("data-missing", {"timestamp": "t", "symbol": "SPY"})
    put("fields-on-top-only", {"timestamp": "t", "symbol": "SPY", "current_price": 100.0, "bid": 99.9, "ask": 100.1})
    put("top-is-a-list", [base])
    put("top-is-a-string", "SPY")
    put("top-is-null", None)
    put("top-is-a-number", 752.4)
    put("empty", b"")
    put("not-json", b"<html>503</html>")
    put("truncated", answer()[:-20])
    put("invalid-utf8", b'{"data": {"symbol": "SPY", "current_price": 100.0, "x": "\xff\xfe"}, "symbol": "SPY"}')
    put("bare-invalid-utf8", b"\xff\xfe\x00{")
    put("utf16", answer().decode().encode("utf-16"))
    put("ten-megabytes-of-valid-data", json.dumps({"data": base["data"], "symbol": "SPY", "pad": "x" * BIG}).encode())
    put("ten-megabytes-of-junk", b"x" * BIG)
    put("deep-nesting", b"[" * 30000 + b"]" * 30000)
    put("deep-nesting-in-data", b'{"data": {"symbol": "SPY", "current_price": 1.0, "x": ' + b"[" * 20000 + b"]" * 20000 + b'}, "symbol": "SPY"}')
    return out


HOSTILE = _hostile()


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_a_hostile_answer_is_no_result_and_never_an_exception(name):
    raw = HOSTILE[name]
    fake = Fake(raw)
    out = sq.cboe_quote("SPY", fetch=fake, clock=CLOCK, sleep=lambda s: None)
    assert out is None
    assert len(fake.calls) == 1                                        # a bad answer is not retried


@pytest.mark.parametrize("reply", [None, 5, 1.5, "{}", ["x"], {"data": 1}, object(), bytearray(b"{}")])
def test_a_fetch_that_returns_something_other_than_bytes_is_no_result(reply):
    assert sq.cboe_quote("SPY", fetch=Fake(reply), clock=CLOCK, sleep=lambda s: None) is None


def test_a_bad_number_never_becomes_another_number():
    for bad in (float("nan"), float("inf"), -1.0, 0.0, 1e7 + 1, "5", True, None, [], {}):
        raw = json.dumps({"data": {"symbol": "SPY", "current_price": bad, "bid": 99.9, "ask": 100.1}, "symbol": "SPY"},
                         allow_nan=True).encode()
        out = quote(raw=raw)
        assert out is not None and out["last"] is None and out["bid"] == 99.9 and out["ask"] == 100.1


def test_the_bounds_of_a_usable_number_are_inclusive_at_the_top_and_open_at_zero():
    assert quote(raw=answer(current_price=1e7))["last"] == 1e7
    assert quote(raw=answer(current_price=1e7 + 1, bid=None, ask=None)) is None
    assert quote(raw=answer(current_price=5e-324))["last"] == 5e-324
    assert quote(raw=answer(current_price=0.0, bid=None, ask=None)) is None


# ---- the reader: the request bounds

def test_two_tries_two_seconds_apart_then_no_result():
    fake, sleeps = Fake(OSError("boom")), []
    assert sq.cboe_quote("SPY", fetch=fake, clock=CLOCK, sleep=sleeps.append) is None
    assert fake.calls == [(URL % "SPY", 5.0), (URL % "SPY", 5.0)] and sleeps == [2.0]


def test_a_second_try_can_succeed_and_a_third_is_never_made():
    fake, sleeps = Fake(TimeoutError("slow"), REAL_SPY), []
    out = sq.cboe_quote("SPY", fetch=fake, clock=CLOCK, sleep=sleeps.append)
    assert out is not None and out["last"] == 752.54 and len(fake.calls) == 2 and sleeps == [2.0]
    fake, sleeps = Fake(OSError("a"), ValueError("b"), REAL_SPY), []
    assert sq.cboe_quote("SPY", fetch=fake, clock=CLOCK, sleep=sleeps.append) is None
    assert len(fake.calls) == 2


def test_a_success_makes_exactly_one_request_and_no_sleep():
    fake, sleeps = Fake(REAL_SPY), []
    sq.cboe_quote("SPY", fetch=fake, clock=CLOCK, sleep=sleeps.append)
    assert len(fake.calls) == 1 and sleeps == []


def test_the_defaults_are_the_real_fetch_and_the_wall_clock():
    import inspect
    params = inspect.signature(sq.cboe_quote).parameters
    assert params["fetch"].default is sq.urllib_fetch
    assert params["clock"].default is not None


# ---- the symbol rule

GOOD = ["SPY", "XLE", "A", "ABCDEF", "BRK.B", "BRK-B", "BF.B"]
BAD = ["spy", "Spy", "", " SPY", "SPY ", "SPY\n", "ABCDEFG", "SP Y", "SPY/../x", "SPY.json?x=1", "SPY?a=b", "SPY#", "SPY%2e",
       "BRK..B", "BRK.", ".B", "-B", "BRK.BB", "BRK.b", "BRK.1", "S1", "SPY1", "ＳＰＹ", "SPY\x00", "SPİ", "../SPY",
       "SPY/QQQ", "BRK.B.C", "BRK-.B", None, 5, 1.5, ["SPY"], b"SPY", ("SPY",)]


@pytest.mark.parametrize("sym", GOOD)
def test_a_good_symbol_passes_the_rule(sym):
    assert sq.valid_symbol(sym) is True


@pytest.mark.parametrize("sym", BAD, ids=[repr(s) for s in BAD])
def test_a_bad_symbol_is_refused_before_any_request(sym):
    assert sq.valid_symbol(sym) is False
    fake = Fake(REAL_SPY)
    with pytest.raises(ValueError):
        sq.cboe_quote(sym, fetch=fake, clock=CLOCK, sleep=lambda s: None)
    assert fake.calls == []


# ---- the real fetch: hosts, redirects, size (no network: a fake HTTPS handler stands in)

class FakeHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, routes):
        super().__init__()
        self.routes, self.seen = routes, []

    def https_open(self, req):
        self.seen.append(req.full_url)
        code, location, body = self.routes[req.full_url]
        headers = Message()
        if location:
            headers["Location"] = location
        headers["Content-Type"] = "application/json"
        resp = urllib.response.addinfourl(body, headers, req.full_url, code)
        resp.msg = "answer"
        return resp


class Counting(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.total = 0

    def read(self, n=-1):
        b = super().read(n)
        self.total += len(b)
        return b

    def read1(self, n=-1):
        b = super().read1(n)
        self.total += len(b)
        return b


A = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/SPY.json"
B = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/SPY.json"


def _fetch_through(routes, url=A):
    handler = FakeHTTPS(routes)
    opener = sq._build_opener(handler)
    return handler, lambda: sq.urllib_fetch(url, 5.0, opener=opener)


def test_the_real_redirect_from_one_cboe_host_to_the_other_is_followed():
    handler, go = _fetch_through({A: (307, B, io.BytesIO(b"")), B: (200, None, io.BytesIO(REAL_SPY))})
    assert go() == REAL_SPY
    assert handler.seen == [A, B]


@pytest.mark.parametrize("target", [
    "https://evil.example/api/global/delayed_quotes/quotes/SPY.json",
    "http://cdn-api.cboe.com/api/global/delayed_quotes/quotes/SPY.json",
    "https://cdn-api.cboe.com.evil.example/x",
    "https://evil.example/cdn-api.cboe.com",
    "https://cdn-api.cboe.com@evil.example/x",
    "https://user:pw@cdn-api.cboe.com/x",
    "https://cdn-api.cboe.com:8443/x",
    "https://CDN-API.CBOE.COM.evil.example/x",
    "https://www.cboe.com/x",
    "file:///etc/passwd",
    "ftp://cdn-api.cboe.com/x",
    "//evil.example/x",
    "",
])
def test_a_redirect_to_any_other_place_is_refused_and_never_requested(target):
    routes = {A: (307, target, io.BytesIO(b"")), target: (200, None, io.BytesIO(REAL_SPY))}
    handler, go = _fetch_through(routes)
    with pytest.raises(urllib.error.HTTPError) as e:                    # the redirect itself is the error
        go()
    assert e.value.code == 307
    assert handler.seen == [A]                                          # the other place is never requested


def test_a_chain_of_redirects_is_cut_short():
    routes = {A: (307, B, io.BytesIO(b"")), B: (307, A, io.BytesIO(b""))}
    handler, go = _fetch_through(routes)
    with pytest.raises(urllib.error.HTTPError):
        go()
    assert handler.seen == [A, B, A]


def test_the_first_url_must_be_the_cboe_host_over_https():
    for url in ("https://evil.example/x", "http://cdn.cboe.com/x", "https://cdn-api.cboe.com@evil.example/x",
                "file:///etc/passwd", "https://cdn.cboe.com:99/x"):
        handler = FakeHTTPS({})
        with pytest.raises(ValueError):
            sq.urllib_fetch(url, 5.0, opener=sq._build_opener(handler))
        assert handler.seen == []


def test_the_body_is_read_only_up_to_a_cap():
    body = Counting(b"x" * BIG)
    handler, go = _fetch_through({A: (200, None, body)})
    with pytest.raises(ValueError):
        go()
    assert body.total <= sq.MAX_BODY_BYTES + 1
    assert sq._read_capped(io.BytesIO(b"abc"), 3) == b"abc"
    with pytest.raises(ValueError):
        sq._read_capped(io.BytesIO(b"abcd"), 3)


def test_a_non_200_answer_is_an_error_not_data():
    handler, go = _fetch_through({A: (404, None, io.BytesIO(b'{"data": 1}'))})
    with pytest.raises(urllib.error.HTTPError) as e:
        go()
    assert e.value.code == 404


def test_only_the_symbol_goes_in_the_request_and_nothing_else_is_sent():
    seen = []

    class Spy(FakeHTTPS):
        def https_open(self, req):
            seen.append((req.full_url, req.get_method(), dict(req.header_items()), req.data))
            return super().https_open(req)
    handler = Spy({A: (200, None, io.BytesIO(REAL_SPY))})
    sq.urllib_fetch(A, 5.0, opener=sq._build_opener(handler))
    (url, method, headers, data), = seen
    assert url == A and method == "GET" and data is None
    assert "?" not in url and "#" not in url
    assert set(k.lower() for k in headers) <= {"user-agent", "accept", "host"}
    assert "authorization" not in {k.lower() for k in headers} and "cookie" not in {k.lower() for k in headers}


def test_the_module_starts_no_child_process():
    src = open(sq.__file__).read()
    for word in ("subprocess", "os.system", "os.popen", "os.exec", "os.spawn", "Popen"):
        assert word not in src


# ---- outcome 4: strict JSON objects (a repeated key is an unusable answer)

def test_a_duplicate_key_at_any_level_is_no_result():
    good = answer()
    assert quote(raw=good) is not None
    cases = {
        "top-level": b'{"timestamp": "2026-11-13 21:28:34", "symbol": "SPY", "symbol": "SPY", "data": {"symbol": "SPY", "current_price": 100.0}}',
        "data-symbol": b'{"symbol": "SPY", "data": {"symbol": "SPY", "symbol": "SPY", "current_price": 100.0}}',
        "data-price": b'{"symbol": "SPY", "data": {"symbol": "SPY", "current_price": 100.0, "current_price": 100.0}}',
        "data-key-twice-with-different-values": b'{"symbol": "SPY", "data": {"symbol": "SPY", "bid": 99.9, "ask": 100.1, "bid": 1.0}}',
        "data-twice": b'{"symbol": "SPY", "data": {"symbol": "SPY", "current_price": 100.0}, "data": {"symbol": "SPY", "current_price": 200.0}}',
        "nested-in-an-unused-field": b'{"symbol": "SPY", "data": {"symbol": "SPY", "current_price": 100.0, "x": {"a": 1, "a": 2}}}',
        "inside-a-list": b'{"symbol": "SPY", "data": {"symbol": "SPY", "current_price": 100.0, "x": [{"a": 1, "a": 1}]}}',
        "timestamp-twice": b'{"timestamp": "2026-11-13 21:28:34", "timestamp": "2026-11-13 21:28:35", "symbol": "SPY", "data": {"symbol": "SPY", "current_price": 100.0}}',
        "escaped-spelling": b'{"symbol": "SPY", "data": {"symbol": "SPY", "current_price": 100.0, "cur\\u0072ent_price": 5.0}}',
    }
    for name, raw in cases.items():
        fake = Fake(raw)
        assert sq.cboe_quote("SPY", fetch=fake, clock=CLOCK, sleep=lambda s: None) is None, name
        assert len(fake.calls) == 1


def test_an_answer_with_no_repeated_key_is_still_read_whatever_the_order():
    raw = b'{"symbol": "SPY", "data": {"last_trade_time": "2026-11-13T16:00:00", "ask": 100.1, "bid": 99.9, "current_price": 100.0, "symbol": "SPY"}, "timestamp": "2026-11-13 21:28:34"}'
    out = quote(raw=raw)
    assert out["last"] == 100.0 and out["bid"] == 99.9 and out["ask"] == 100.1


# ---- the snapshot time (outcome 1c): `snapshot_at`

def test_the_snapshot_time_is_returned_as_utc_seconds():
    spy = sq.cboe_quote("SPY", fetch=Fake(REAL_SPY), clock=CLOCK, sleep=lambda s: None)
    assert spy["snapshot_at"] == datetime(2026, 11, 13, 21, 28, 34, tzinfo=UTC).timestamp()
    xle = sq.cboe_quote("XLE", fetch=Fake(REAL_XLE), clock=CLOCK, sleep=lambda s: None)
    assert xle["snapshot_at"] == datetime(2026, 11, 13, 21, 28, 35, tzinfo=UTC).timestamp()
    assert set(spy) == {"symbol", "bid", "ask", "last", "last_trade_time", "snapshot_at", "fetched_at", "source"}


def test_a_missing_or_unreadable_snapshot_time_is_none_and_the_rest_stays():
    base = json.loads(answer())
    for bad in (None, "", "2026-11-13T21:28:34", "2026-11-13 21:28:34Z", "2026-13-13 21:28:34", "2026-11-13 25:28:34", 20261113,
                ["2026-11-13 21:28:34"], "yesterday", "2026-11-13 21:28", " 2026-11-13 21:28:34"):
        doc = dict(base)
        doc["timestamp"] = bad
        out = quote(raw=json.dumps(doc).encode())
        assert out is not None and out["snapshot_at"] is None and out["last"] == 100.0, bad
    doc = dict(base)
    del doc["timestamp"]
    out = quote(raw=json.dumps(doc).encode())
    assert out is not None and out["snapshot_at"] is None


# ---- outcome 3: one deadline for the whole request

class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class Trickle:
    """A body that gives one byte per read and takes `cost` seconds of the fake clock for each."""
    closed = False

    def __init__(self, clock, cost=4.0):
        self.clock, self.cost, self.reads = clock, cost, 0

    def read1(self, n=-1):
        self.reads += 1
        self.clock.t += self.cost
        return b"x"

    read = read1

    def close(self):
        pass


class Timed(FakeHTTPS):
    """The fake HTTPS handler, each answer taking some fake-clock seconds, and the timeout of each request recorded."""
    def __init__(self, routes, clock, cost):
        super().__init__(routes)
        self.clock, self.cost, self.timeouts = clock, cost, []

    def https_open(self, req):
        self.timeouts.append(req.timeout)
        self.clock.t += self.cost.get(req.full_url, 0)
        return super().https_open(req)


def test_a_body_arriving_one_byte_every_four_seconds_is_cut_off_at_the_deadline():
    fc = FakeClock()
    body = Trickle(fc)
    handler = FakeHTTPS({A: (200, None, body)})
    with pytest.raises(TimeoutError):
        sq.urllib_fetch(A, 5.0, opener=sq._build_opener(handler), clock=fc)
    assert body.reads == 3 and fc.t == 12.0           # the third read began at 8 s, before the deadline; none began after it
    assert sq.DEADLINE_SECONDS == 10.0


def test_the_deadline_covers_the_redirect_and_the_second_request_gets_only_what_is_left():
    fc = FakeClock()
    handler = Timed({A: (307, B, io.BytesIO(b"")), B: (200, None, io.BytesIO(REAL_SPY))}, fc, {A: 6.0, B: 6.0})
    with pytest.raises(TimeoutError):
        sq.urllib_fetch(A, 5.0, opener=sq._build_opener(handler), clock=fc)
    assert handler.seen == [A, B] and handler.timeouts == [5.0, 4.0]


def test_a_redirect_after_the_deadline_is_not_followed():
    fc = FakeClock()
    handler = Timed({A: (307, B, io.BytesIO(b"")), B: (200, None, io.BytesIO(REAL_SPY))}, fc, {A: 10.5})
    with pytest.raises(TimeoutError):
        sq.urllib_fetch(A, 5.0, opener=sq._build_opener(handler), clock=fc)
    assert handler.seen == [A]


def test_an_answer_inside_the_deadline_is_returned():
    fc = FakeClock()
    handler = Timed({A: (307, B, io.BytesIO(b"")), B: (200, None, io.BytesIO(REAL_SPY))}, fc, {A: 4.0, B: 5.0})
    assert sq.urllib_fetch(A, 5.0, opener=sq._build_opener(handler), clock=fc) == REAL_SPY
    assert handler.timeouts == [5.0, 5.0]


def test_a_redirect_body_is_never_read():
    fc = FakeClock()
    big = Counting(b"y" * BIG)
    handler = Timed({A: (307, B, big), B: (200, None, io.BytesIO(REAL_SPY))}, fc, {})
    assert sq.urllib_fetch(A, 5.0, opener=sq._build_opener(handler), clock=fc) == REAL_SPY
    assert big.total == 0


def test_a_try_that_overruns_ten_seconds_on_the_callers_clock_fails():
    fc = FakeClock(NOW)
    calls, sleeps = [], []

    def slow(url, timeout):
        calls.append(url)
        fc.t += 2100.0                                   # thirty-five minutes, then a good answer
        return REAL_SPY
    assert sq.cboe_quote("SPY", fetch=slow, clock=fc, sleep=sleeps.append) is None
    assert len(calls) == 2 and sleeps == [2.0]

    def ten(url, timeout):
        fc.t += 10.0
        return REAL_SPY
    assert sq.cboe_quote("SPY", fetch=ten, clock=fc, sleep=sleeps.append) is not None          # exactly the deadline is fine

    def over(url, timeout):
        fc.t += 10.5
        return REAL_SPY
    assert sq.cboe_quote("SPY", fetch=over, clock=fc, sleep=sleeps.append) is None


def test_the_watchdog_shuts_a_socket_at_the_deadline_and_can_be_cancelled():
    import socket
    a, b = socket.socketpair()
    b.settimeout(5.0)
    budget = sq._Budget(time.monotonic, 0.2)
    budget.register(a)
    assert budget.fired.wait(3.0)
    assert b.recv(1) == b""                              # the far end sees the connection closed
    b.close()
    quiet = sq._Budget(time.monotonic, 0.2)
    quiet.cancel()
    assert not quiet.fired.wait(0.5)
    c, d = socket.socketpair()
    late = sq._Budget(time.monotonic, 0.05)
    late.fired.wait(3.0)
    late.register(c)                                     # registering after the deadline closes it at once
    d.settimeout(3.0)
    assert d.recv(1) == b""
    d.close()


def test_the_real_opener_connects_through_the_watchdog_and_never_reads_a_proxy_from_the_environment(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    opener = sq._build_opener()
    kinds = {type(h).__name__ for h in opener.handlers}
    assert "ProxyHandler" not in kinds                   # an empty proxy table registers no handler at all
    assert "_BudgetHTTPS" in kinds and "_NoRedirects" in kinds and "HTTPRedirectHandler" not in kinds


# ---- rev 3: the TLS setup takes nothing from the environment (SSLKEYLOGFILE)

def test_the_tls_context_of_the_real_opener_does_not_honour_sslkeylogfile(monkeypatch, tmp_path):
    keys = tmp_path / "keys.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keys))
    opener = sq._build_opener()
    handler = [h for h in opener.handlers if type(h).__name__ == "_BudgetHTTPS"][0]
    assert handler._context.keylog_filename is None
    assert not keys.exists()                                           # nothing opened, nothing written
    ctx = sq._tls_context()
    assert ctx.keylog_filename is None and not keys.exists()
    assert sq._BudgetHTTPS()._context.keylog_filename is None and not keys.exists()
    assert list(tmp_path.iterdir()) == []


def test_the_standard_library_would_have_honoured_it_which_is_why_the_context_is_built_by_hand(monkeypatch, tmp_path):
    keys = tmp_path / "keys.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keys))
    assert ssl.create_default_context().keylog_filename == str(keys)   # the default would log: the control for the test above


def test_the_tls_context_still_verifies_the_server_and_the_host_name():
    ctx = sq._tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert ctx.get_ca_certs() or ctx.cert_store_stats() is not None


# ---- rev 3: the name lookup is inside the deadline

class Stuck:
    """A name lookup that does not answer until released (a safety limit of 4 s, then it fails)."""
    def __init__(self):
        self.release, self.calls = threading.Event(), 0

    def __call__(self, *a, **k):
        self.calls += 1
        self.release.wait(4.0)
        raise socket.gaierror(-2, "released")


def _lookup_threads():
    return [t for t in threading.enumerate() if t.name == "second-quote-lookup"]


def _wait_for(condition, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        time.sleep(0.01)
    return condition()


def test_a_resolver_that_does_not_answer_fails_the_try_at_the_deadline_and_never_piles_up_threads(monkeypatch):
    baseline = threading.active_count()
    stuck = Stuck()
    monkeypatch.setattr(socket, "getaddrinfo", stuck)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        sq.urllib_fetch(A, 5.0, deadline=0.3)
    assert time.monotonic() - started < 2.0                            # the deadline, not the resolver's own 4 s
    assert stuck.calls == 1 and len(_lookup_threads()) == 1 and _lookup_threads()[0].daemon
    for _ in range(3):                                                  # more tries while that lookup still hangs
        began = time.monotonic()
        with pytest.raises(TimeoutError):
            sq.urllib_fetch(A, 5.0, deadline=0.3)
        assert time.monotonic() - began < 2.0
    assert stuck.calls == 1 and len(_lookup_threads()) == 1             # no second lookup, no second thread
    stuck.release.set()
    assert _wait_for(lambda: not _lookup_threads())
    assert _wait_for(lambda: threading.active_count() == baseline)      # the deadline timers are gone too
    fast = Stuck()                                                      # a fresh resolver that fails at once
    fast.release.set()
    monkeypatch.setattr(socket, "getaddrinfo", fast)
    with pytest.raises(urllib.error.URLError):                          # the lookup runs again once the old one is over
        sq.urllib_fetch(A, 5.0, deadline=5.0)
    assert fast.calls == 1
    assert _wait_for(lambda: threading.active_count() == baseline)


def test_cboe_quote_with_the_real_fetch_is_none_when_the_lookup_stalls_and_leaves_no_thread(monkeypatch):
    baseline = threading.active_count()
    stuck = Stuck()
    monkeypatch.setattr(socket, "getaddrinfo", stuck)
    sleeps = []
    started = time.monotonic()
    assert sq.cboe_quote("SPY", fetch=functools.partial(sq.urllib_fetch, deadline=0.3), sleep=sleeps.append) is None
    assert time.monotonic() - started < 3.0 and sleeps == [2.0] and stuck.calls == 1
    stuck.release.set()
    assert _wait_for(lambda: not _lookup_threads()) and _wait_for(lambda: threading.active_count() == baseline)


def test_a_lookup_that_answers_gives_the_address_the_connection_goes_to(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    calls = []

    def lookup(host, port_, *a, **k):
        calls.append((host, port_))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    budget = sq._Budget(time.monotonic, 5.0)
    try:
        conn = sq._Conn("cdn.cboe.com", timeout=5.0, context=sq._tls_context())
        conn._budget = budget
        sock = conn._create_connection(("cdn.cboe.com", 443), 5.0, None)
        peer, _ = srv.accept()
        assert sock.getpeername() == ("127.0.0.1", port) and calls == [("cdn.cboe.com", 443)]
        assert sock in budget._socks                                    # the socket is under the watchdog
        sock.close()
        peer.close()
    finally:
        budget.cancel()
        srv.close()
    assert not _lookup_threads()


def test_a_failed_lookup_or_connection_is_an_oserror_and_the_next_address_is_tried(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    good = srv.getsockname()[1]
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    refused = dead.getsockname()[1]
    dead.close()                                                        # nothing listens there now

    def make(addresses):
        return lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p)) for p in addresses]
    budget = sq._Budget(time.monotonic, 5.0)
    conn = sq._Conn("cdn.cboe.com", timeout=5.0, context=sq._tls_context())
    conn._budget = budget
    try:
        monkeypatch.setattr(socket, "getaddrinfo", make([refused]))
        with pytest.raises(OSError):
            conn._create_connection(("cdn.cboe.com", 443), 5.0, None)
        monkeypatch.setattr(socket, "getaddrinfo", make([]))
        with pytest.raises(OSError):
            conn._create_connection(("cdn.cboe.com", 443), 5.0, None)
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(-2, "no such name")))
        with pytest.raises(OSError):
            conn._create_connection(("cdn.cboe.com", 443), 5.0, None)
        monkeypatch.setattr(socket, "getaddrinfo", make([refused, good]))
        sock = conn._create_connection(("cdn.cboe.com", 443), 5.0, None)
        assert sock.getpeername() == ("127.0.0.1", good)
        sock.close()
    finally:
        budget.cancel()
        srv.close()


def test_no_lookup_is_started_once_the_deadline_has_passed(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a) or [])
    budget = sq._Budget(time.monotonic, 0.05)
    assert budget.fired.wait(3.0)
    conn = sq._Conn("cdn.cboe.com", timeout=5.0, context=sq._tls_context())
    conn._budget = budget
    with pytest.raises(TimeoutError):
        conn._create_connection(("cdn.cboe.com", 443), 5.0, None)
    assert calls == []


# ---- compare (rev 2: age is judged against the caller's `now`)

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def sec(bid=None, ask=None, last=None, ltt="2026-11-13T16:00:00", fetched=NOW - 5, snapshot=NOW - 35, symbol="SPY"):
    """A second quote as `cboe_quote` returns it. By default: the trade 20 minutes old, fetched 5 s before `NOW`, the
    snapshot 30 s before the fetch."""
    return {"symbol": symbol, "bid": bid, "ask": ask, "last": last, "last_trade_time": ltt, "snapshot_at": snapshot,
            "fetched_at": fetched, "source": "cboe-delayed"}


def judge(first, second, tol=0.01, now=NOW_DT, symbol="SPY", **kw):
    return sq.compare(first, second, symbol=symbol, now=now, tolerance=tol, **kw)


def verdict(first, second, tol=0.01, **kw):
    return judge(first, second, tol, **kw)["verdict"]


def at(seconds_before_now, now=NOW):
    """The New York wall-clock text of the instant `seconds_before_now` before `now`."""
    return datetime.fromtimestamp(now - seconds_before_now, NY).strftime("%Y-%m-%dT%H:%M:%S")


def test_compare_returns_the_three_fields_and_nothing_else():
    out = judge(100.0, sec(99.98, 100.02))
    assert set(out) == {"verdict", "difference", "basis"}
    assert out["verdict"] == "agree" and out["difference"] == 0.0 and out["basis"] == "bid/ask band"


def test_the_symbol_now_and_options_are_keywords_only():
    s = sec(99.98, 100.02)
    with pytest.raises(TypeError):
        sq.compare(100.0, s, "SPY", NOW_DT)
    with pytest.raises(TypeError):
        sq.compare(100.0, s, symbol="SPY")                              # `now` is required
    with pytest.raises(TypeError):
        sq.compare(100.0, s, now=NOW_DT)                                # so is `symbol`
    with pytest.raises(TypeError):
        sq.compare(100.0, s, 0.01, symbol="SPY", now=NOW_DT)


def test_the_band_edges_are_inside_and_a_cent_beyond_is_outside():
    s = sec(99.98, 100.02)                                          # mid 100.00, tolerance 1 % is 1.00
    assert verdict(101.02, s) == "agree"                            # ask + 1.00
    assert verdict(101.03, s) == "disagree"                         # a cent outside
    assert verdict(98.98, s) == "agree"                             # bid - 1.00
    assert verdict(98.97, s) == "disagree"
    assert verdict(100.0, s) == "agree"
    s = sec(752.5, 752.55)                                          # mid 752.525: tolerance 7.52525
    assert verdict(752.55 + 7.52525, s) == "agree"
    assert verdict(752.55 + 7.53, s) == "disagree"
    assert verdict(752.5 - 7.52525, s) == "agree"
    assert verdict(752.5 - 7.53, s) == "disagree"


def test_tolerance_is_a_fraction_of_the_mid_and_zero_means_the_bare_band():
    s = sec(99.0, 101.0)
    assert verdict(101.0, s, 0.0) == "agree" and verdict(99.0, s, 0.0) == "agree"
    assert verdict(101.01, s, 0.0) == "disagree" and verdict(98.99, s, 0.0) == "disagree"
    assert verdict(106.0, s, 0.05) == "agree" and verdict(106.01, s, 0.05) == "disagree"     # 101 + 0.05 * 100


def test_a_band_with_no_width_works():
    s = sec(100.0, 100.0)
    assert verdict(101.0, s) == "agree" and verdict(101.01, s) == "disagree"


def test_a_crossed_band_is_no_quote_whatever_the_last_price():
    for s in (sec(100.5, 100.0, last=100.2), sec(100.5, 100.0), sec(102.0, 100.0, last=500.0), sec(100.01, 100.0, last=100.0),
              sec(102.0, 100.0, last=102.0), sec(102.0, 100.0, last=100.0), sec(1e7, 5e-324, last=100.0)):
        for first in (100.0, 100.2, 101.2, 500.0, 102.0):
            out = judge(first, s)
            assert out == {"verdict": "no second quote", "difference": None, "basis": out["basis"]}, (s, first, out)
            assert "crossed" in out["basis"]
    assert verdict(500.0, sec(102.0, 100.0, last=500.0)) == "no second quote"       # the case that read "agree"
    assert verdict(500.0, sec(102.0, 100.0, last=500.0), 0.0) == "no second quote"
    assert verdict(500.0, sec(102.0, 100.0, last=500.0), 1.0) == "no second quote"


def test_a_band_with_equal_bid_and_ask_is_not_crossed_and_one_usable_side_still_uses_the_last_price():
    assert verdict(100.0, sec(100.0, 100.0, last=100.0)) == "agree"
    for s in (sec(102.0, None, last=500.0), sec(None, 100.0, last=500.0), sec(102.0, -1.0, last=500.0), sec(0, 100.0, last=500.0),
              sec(102.0, float("nan"), last=500.0), sec(102.0, "100", last=500.0)):
        out = judge(500.0, s)
        assert out["verdict"] == "agree" and out["basis"] == "last price", s


def test_without_both_sides_the_last_price_is_used_and_without_a_last_there_is_no_quote():
    for s in (sec(None, 100.1, last=100.0), sec(99.9, None, last=100.0), sec(None, None, last=100.0)):
        out = judge(101.0, s)
        assert out["basis"] == "last price" and out["verdict"] == "agree" and abs(out["difference"] - 0.01) < 1e-12
        assert verdict(101.01, s) == "disagree"
        assert verdict(99.0, s) == "agree" and verdict(98.99, s) == "disagree"
    for s in (sec(None, 100.1), sec(99.9, None), sec(None, None)):
        out = judge(100.0, s)
        assert out["verdict"] == "no second quote" and out["difference"] is None


def test_the_band_is_preferred_over_the_last_price_when_both_exist():
    s = sec(99.0, 101.0, last=100.5)
    assert judge(100.0, s)["basis"] == "bid/ask band" and verdict(100.0, s) == "agree"
    assert verdict(150.0, s) == "disagree"


def test_difference_is_the_signed_fraction_from_the_mid_or_the_last():
    assert math.isclose(judge(102.0, sec(99.0, 101.0))["difference"], 0.02)
    assert math.isclose(judge(98.0, sec(99.0, 101.0))["difference"], -0.02)
    assert math.isclose(judge(95.0, sec(last=100.0))["difference"], -0.05)


def test_a_quote_from_an_earlier_day_is_never_agree():
    for ltt in ("2026-11-12T16:00:00", "2026-11-12T23:59:59", "2025-11-13T16:00:00", "2026-10-13T16:00:00"):
        out = judge(100.0, sec(99.98, 100.02, last=100.0, ltt=ltt))
        assert out["verdict"] == "no second quote" and out["difference"] is None


def test_a_missing_or_unreadable_time_is_no_second_quote():
    for ltt in (None, "", "garbage", "2026-11-13", "2026-11-13 16:00:00", "2026-13-13T16:00:00", "2026-11-31T16:00:00",
                "2026-11-13T25:00:00", 20261113, ["2026-11-13T16:00:00"], "2026-11-13T16:00:00Z", "2026-11-13T16:00:00+00:00"):
        assert verdict(100.0, sec(99.98, 100.02, ltt=ltt)) == "no second quote", ltt
    s = sec(99.98, 100.02)
    del s["last_trade_time"]
    assert verdict(100.0, s) == "no second quote"


def test_garbage_in_place_of_a_second_quote_is_no_second_quote_and_never_an_exception():
    for bad in (None, "x", 5, [], {}, {"bid": 1.0}, sec(99.98, 100.02, fetched=None), sec(99.98, 100.02, fetched="now"),
                sec(99.98, 100.02, fetched=float("nan")), sec(99.98, 100.02, fetched=True), sec(99.98, 100.02, fetched=float("inf")),
                sec(bid="99", ask="101", last="100"), sec(bid=float("nan"), ask=float("nan"), last=float("nan")),
                sec(bid=-1.0, ask=-2.0, last=-3.0), sec(bid=0, ask=0, last=0), sec(bid=1e9, ask=1e9, last=1e9),
                sec(bid=True, ask=True, last=True), sec(bid=[1], ask={}, last=None)):
        out = judge(100.0, bad)
        assert out["verdict"] == "no second quote" and out["difference"] is None


def test_an_unusable_first_price_is_never_agree():
    for bad in (None, float("nan"), float("inf"), -1.0, 0.0, 0, True, "100", [], 1e8):
        out = judge(bad, sec(99.98, 100.02))
        assert out["verdict"] == "disagree" and out["difference"] is None


def test_no_second_quote_is_reported_before_a_bad_first_price():
    assert verdict(None, None) == "no second quote"


def test_a_bad_tolerance_is_refused():
    for bad in (float("nan"), float("inf"), -0.01, True, "0.01", None, 1.5, [0.01]):
        with pytest.raises(ValueError):
            judge(100.0, sec(99.98, 100.02), bad)


# ---- outcome 1: a quote hours old must not approve a current price

def test_a_0931_trade_does_not_approve_a_price_at_1559():
    now = datetime(2026, 11, 13, 15, 59, 0, tzinfo=NY)
    t = now.timestamp()
    s = sec(99.98, 100.02, last=100.0, ltt="2026-11-13T09:31:00", fetched=t - 1, snapshot=t - 10)
    out = judge(100.0, s, now=now)
    assert out["verdict"] == "no second quote" and out["difference"] is None
    fresh = sec(99.98, 100.02, last=100.0, ltt="2026-11-13T15:58:30", fetched=t - 1, snapshot=t - 10)
    assert judge(100.0, fresh, now=now)["verdict"] == "agree"


@pytest.mark.parametrize("age,ok", [(0, True), (1, True), (1799, True), (1800, True), (1801, False), (7200, False),
                                    (-1, True), (-299, True), (-300, True), (-301, False), (-3600, False)])
def test_the_trade_must_be_at_most_1800_seconds_before_now_and_300_after(age, ok):
    out = judge(100.0, sec(99.98, 100.02, ltt=at(age)))
    assert out["verdict"] == ("agree" if ok else "no second quote")


def test_the_age_limit_is_the_callers_to_set():
    s = sec(99.98, 100.02, ltt=at(60))
    assert verdict(100.0, s, max_age_seconds=60) == "agree"
    assert verdict(100.0, s, max_age_seconds=59) == "no second quote"
    assert verdict(100.0, s, max_age_seconds=59.5) == "no second quote"
    assert verdict(100.0, sec(99.98, 100.02, ltt=at(3600)), max_age_seconds=3600) == "agree"


@pytest.mark.parametrize("before,ok", [(0, True), (1, True), (119, True), (120, True), (121, False), (3600, False),
                                       (-1, False), (-120, False)])
def test_the_quote_must_have_been_fetched_at_most_120_seconds_before_now_and_not_after(before, ok):
    t = NOW - before
    out = judge(100.0, sec(99.98, 100.02, fetched=t, snapshot=t - 10))
    assert out["verdict"] == ("agree" if ok else "no second quote")


@pytest.mark.parametrize("offset,ok", [(-121, False), (-120, True), (-119, True), (-1, True), (0, True), (1, True),
                                       (119, True), (120, True), (121, False), (-3600, False), (3600, False)])
def test_the_snapshot_must_be_within_120_seconds_of_the_fetch(offset, ok):
    fetched = NOW - 5
    out = judge(100.0, sec(99.98, 100.02, fetched=fetched, snapshot=fetched + offset))
    assert out["verdict"] == ("agree" if ok else "no second quote")


def test_a_missing_or_unreadable_snapshot_time_is_no_second_quote():
    for snap in (None, "2026-11-13 21:19:00", True, float("nan"), float("inf"), "", [NOW], {}):
        assert verdict(100.0, sec(99.98, 100.02, snapshot=snap)) == "no second quote", snap
    s = sec(99.98, 100.02)
    del s["snapshot_at"]
    assert verdict(100.0, s) == "no second quote"


def test_a_snapshot_in_the_future_is_no_second_quote_even_with_a_fresh_trade():
    fetched = NOW - 5
    assert verdict(100.0, sec(99.98, 100.02, fetched=fetched, snapshot=fetched + 500)) == "no second quote"
    assert verdict(100.0, sec(99.98, 100.02, fetched=fetched, snapshot=NOW + 1000)) == "no second quote"


def test_a_snapshot_that_is_stale_is_no_second_quote_even_with_a_fresh_trade():
    fetched = NOW - 5
    assert verdict(100.0, sec(99.98, 100.02, fetched=fetched, snapshot=fetched - 600)) == "no second quote"


def test_now_may_be_in_any_time_zone_and_is_an_instant():
    s = sec(99.98, 100.02)
    for tz in (UTC, NY, ZoneInfo("Asia/Tokyo"), timezone.utc):
        assert verdict(100.0, s, now=NOW_DT.astimezone(tz)) == "agree"


def test_now_must_be_an_aware_datetime_and_the_age_limit_a_positive_number():
    s = sec(99.98, 100.02)
    for bad in (datetime(2026, 11, 13, 21, 20, 3), NOW, None, "2026-11-13T21:20:03Z", 5, [NOW_DT]):
        with pytest.raises(ValueError):
            sq.compare(100.0, s, symbol="SPY", now=bad)
    for bad in (0, -1, float("nan"), float("inf"), True, "1800", None, [1800]):
        with pytest.raises(ValueError):
            sq.compare(100.0, s, symbol="SPY", now=NOW_DT, max_age_seconds=bad)


def test_compare_never_reads_a_clock(monkeypatch):
    import inspect
    def boom(*a, **k):
        raise AssertionError("a clock was read")
    monkeypatch.setattr(sq.time, "time", boom)
    monkeypatch.setattr(sq.time, "monotonic", boom)
    assert verdict(100.0, sec(99.98, 100.02)) == "agree"
    src = inspect.getsource(sq.compare)
    assert "time.time" not in src and "monotonic" not in src and ".now(" not in src and "utcnow" not in src


def test_daylight_saving_change_days_spring_forward():
    now = datetime(2026, 3, 8, 7, 10, 0, tzinfo=UTC)                    # 03:10 EDT; the clock jumped 02:00 -> 03:00
    t = now.timestamp()

    def v(ltt, **kw):
        return judge(100.0, sec(99.98, 100.02, ltt=ltt, fetched=t - 5, snapshot=t - 15), now=now, **kw)["verdict"]
    assert v("2026-03-08T01:59:59") == "agree"                          # 06:59:59 UTC, 10 minutes before
    assert v("2026-03-08T03:00:00") == "agree"                          # 07:00 UTC
    assert v("2026-03-08T02:30:00") == "no second quote"                # a wall time that did not exist
    assert v("2026-03-08T02:00:00") == "no second quote"
    assert v("2026-03-08T00:50:00") == "no second quote"                # 05:50 UTC, 80 minutes before
    assert v("2026-03-08T02:05:00") == "no second quote"                # read as EDT it is 65 minutes old
    assert v("2026-03-08T02:05:00", max_age_seconds=7200) == "agree"    # both readings (07:05 and 06:05 UTC) are recent
    assert v("2026-03-08T02:30:00", max_age_seconds=7200) == "no second quote"    # read as EST it is 20 minutes ahead


def test_daylight_saving_change_days_fall_back():
    def v(now_utc, ltt, **kw):
        now = datetime(2026, 11, 1, *now_utc, tzinfo=UTC)
        t = now.timestamp()
        return judge(100.0, sec(99.98, 100.02, ltt=ltt, fetched=t - 5, snapshot=t - 15), now=now, **kw)["verdict"]
    # 01:00-01:59 happens twice (05:00-05:59 UTC in EDT, then 06:00-06:59 UTC in EST)
    assert v((5, 20, 0), "2026-11-01T01:10:00") == "no second quote"    # read as EST it would be 50 minutes ahead
    assert v((6, 20, 0), "2026-11-01T01:10:00") == "no second quote"    # read as EDT it would be 70 minutes old
    assert v((6, 20, 0), "2026-11-01T01:10:00", max_age_seconds=7200) == "agree"
    assert v((5, 20, 0), "2026-11-01T00:50:00") == "agree"              # 04:50 UTC: exactly 1800 s
    assert v((7, 5, 0), "2026-11-01T02:00:00") == "agree"               # 07:00 UTC
    assert v((7, 5, 0), "2026-11-01T01:50:00") == "no second quote"     # ambiguous: 05:50 UTC is 75 minutes old


def test_the_quote_age_works_across_midnight_in_new_york():
    now = datetime(2026, 11, 14, 0, 5, 0, tzinfo=NY)                    # 00:05 the next day
    t = now.timestamp()
    s = sec(99.98, 100.02, ltt="2026-11-13T23:50:00", fetched=t - 5, snapshot=t - 15)
    assert judge(100.0, s, now=now)["verdict"] == "agree"


def test_the_real_answer_read_at_1629_agrees_and_at_1630_is_a_half_hour_old_trade():
    spy = sq.cboe_quote("SPY", fetch=Fake(REAL_SPY), clock=lambda: PNOW, sleep=lambda s: None)
    now = datetime.fromtimestamp(PNOW, UTC)
    out = sq.compare(752.6, spy, symbol="SPY", now=now)
    assert out["verdict"] == "agree" and out["basis"] == "bid/ask band"
    later = sq.cboe_quote("SPY", fetch=Fake(REAL_SPY), clock=lambda: PNOW + 63, sleep=lambda s: None)    # 16:30:03, trade 1803 s old
    assert sq.compare(752.6, later, symbol="SPY", now=datetime.fromtimestamp(PNOW + 63, UTC))["verdict"] == "no second quote"
    assert sq.compare(752.6, later, symbol="SPY", now=datetime.fromtimestamp(PNOW + 63, UTC), max_age_seconds=1803)["verdict"] == "agree"


# ---- outcome 2: the quote's symbol must be the symbol asked for

def test_the_quote_must_be_for_the_symbol_given():
    s = sec(99.98, 100.02)
    assert verdict(100.0, s) == "agree"
    assert verdict(100.0, sec(99.98, 100.02, symbol="QQQ")) == "no second quote"
    assert verdict(100.0, s, symbol="QQQ") == "no second quote"
    for bad in (None, 5, ["SPY"], "spy", "SPY ", b"SPY"):
        assert verdict(100.0, s, symbol=bad) == "no second quote", bad
    for bad in (None, 5, ["SPY"], "spy"):
        assert verdict(100.0, sec(99.98, 100.02, symbol=bad)) == "no second quote", bad
    no_symbol = sec(99.98, 100.02)
    del no_symbol["symbol"]
    assert verdict(100.0, no_symbol) == "no second quote"


# ---- outcome 5: a band that its own last trade contradicts is not a band

def test_a_last_trade_outside_the_band_by_more_than_the_tolerance_is_suspect_data():
    s = sec(99.0, 101.0, last=105.0)                                    # band 99..101, tolerance 1 % of the mid is 1.00
    out = judge(100.0, s)
    assert out["verdict"] == "no second quote" and out["difference"] is None
    assert verdict(105.0, s) == "no second quote"                       # even a first price that matches the last
    assert verdict(100.0, sec(99.0, 101.0, last=102.0)) == "agree"      # exactly on the edge: fine
    assert verdict(100.0, sec(99.0, 101.0, last=102.01)) == "no second quote"
    assert verdict(100.0, sec(99.0, 101.0, last=98.0)) == "agree"
    assert verdict(100.0, sec(99.0, 101.0, last=97.99)) == "no second quote"
    assert verdict(100.0, sec(99.0, 101.0, last=101.01), 0.0) == "no second quote"
    assert verdict(100.0, sec(99.0, 101.0, last=101.0), 0.0) == "agree"
    assert verdict(100.0, sec(99.0, 101.0, last=100.0), 0.0) == "agree"


def test_the_last_trade_check_applies_only_when_there_is_a_usable_band():
    assert verdict(105.0, sec(None, 100.0, last=105.0)) == "agree"      # only one side is usable: the last price is used
    assert verdict(105.0, sec(100.5, None, last=105.0)) == "agree"
    assert verdict(100.0, sec(99.0, 101.0, last=None)) == "agree"


# ---- compare against an independent calculation on generated cases (whole numbers, no floats in the oracle)

def _oracle(first_c, bid_c, ask_c, last_c, ltt, tol_t):
    """Prices in whole cents, tolerance in ten-thousandths, the trade time a string on 2026-11-13 (now is 16:20:03);
    the answer from integer arithmetic."""
    if ltt not in ("2026-11-13T16:00:00", "2026-11-13T16:19:00"):         # 20 or 1 minute old: recent; anything else is not
        return "no second quote", None
    if bid_c is not None and ask_c is not None and bid_c > ask_c:
        return "no second quote", None                                  # a crossed band is no quote
    if bid_c is not None and ask_c is not None and bid_c <= ask_c:
        lo = Fraction(bid_c) - Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2)
        hi = Fraction(ask_c) + Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2)
        if last_c is not None and not (lo <= last_c <= hi):
            return "no second quote", None                              # the last trade contradicts the band
        ref = Fraction(bid_c + ask_c, 2)
        return ("agree" if lo <= first_c <= hi else "disagree"), float((first_c - ref) / ref)
    if last_c is not None:
        ok = abs(Fraction(first_c) - last_c) * 10000 <= tol_t * last_c
        return ("agree" if ok else "disagree"), float(Fraction(first_c - last_c, last_c))
    return "no second quote", None


def test_a_thousand_generated_cases_agree_with_the_independent_calculation():
    rng = random.Random(20261113)
    seen = {"agree": 0, "disagree": 0, "no second quote": 0, "band": 0, "last": 0, "contradicted": 0, "crossed": 0}
    for i in range(3000):
        bid = rng.randint(100, 2_000_000)
        ask = bid + rng.randint(0, 5000)
        last = rng.randint(bid - 3000 if bid > 4000 else 100, ask + 3000)  # near the band most of the time
        if rng.random() < 0.3:
            last = rng.randint(100, 2_000_000)
        elif rng.random() < 0.4:
            last = rng.randint(bid, ask)
        tol_t = rng.choice([0, 1, 5, 50, 100, 100, 100, 250, 1000, rng.randint(0, 10000)])
        kind = rng.random()
        bid_c, ask_c, last_c = bid, ask, last
        if kind < 0.10:
            bid_c, ask_c = ask, bid
        elif kind < 0.20:
            bid_c = None
        elif kind < 0.30:
            ask_c = None
        elif kind < 0.40:
            bid_c = ask_c = None
        if rng.random() < 0.15:
            last_c = None
        ltt = rng.choice(["2026-11-13T16:00:00", "2026-11-13T16:19:00", "2026-11-13T09:31:00", "2026-11-12T16:00:00", None,
                          "2026-11-13T16:00:00"])
        if bid_c is not None and ask_c is not None and bid_c <= ask_c and rng.random() < 0.6:
            edge = rng.choice([bid_c - Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2),
                               ask_c + Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2)])
            first_c = int(edge) + rng.choice([-1, 0, 1])
        elif last_c is not None and rng.random() < 0.6:
            first_c = int(last_c * (10000 + rng.choice([-1, 1]) * tol_t) / 10000) + rng.choice([-1, 0, 1])
        else:
            first_c = rng.randint(100, 2_000_000)
        first_c = max(first_c, 1)
        second = sec(None if bid_c is None else bid_c / 100, None if ask_c is None else ask_c / 100,
                     None if last_c is None else last_c / 100, ltt)
        want, want_diff = _oracle(first_c, bid_c, ask_c, last_c, ltt, tol_t)
        got = judge(first_c / 100, second, tol_t / 10000)
        assert got["verdict"] == want, (i, first_c, bid_c, ask_c, last_c, ltt, tol_t, got)
        if want_diff is None:
            assert got["difference"] is None
        else:
            assert math.isclose(got["difference"], want_diff, rel_tol=1e-9, abs_tol=1e-12), (i, got, want_diff)
        seen[want] += 1
        if ltt in ("2026-11-13T16:00:00", "2026-11-13T16:19:00"):
            if want != "no second quote":
                seen["band" if bid_c is not None and ask_c is not None and bid_c <= ask_c else "last"] += 1
            elif bid_c is not None and ask_c is not None and bid_c > ask_c:
                seen["crossed"] += 1
            elif bid_c is not None and ask_c is not None and last_c is not None:
                seen["contradicted"] += 1
    assert min(seen.values()) > 100, seen                                            # every branch was exercised a lot


# the age rule on generated cases, with an oracle that does not use the time zone database: United States daylight time
# in 2026 runs from 2026-03-08 07:00 UTC to 2026-11-01 06:00 UTC, and New York is 4 hours behind UTC during it, else 5.

DST_START = datetime(2026, 3, 8, 7, 0, 0, tzinfo=UTC).timestamp()
DST_END = datetime(2026, 11, 1, 6, 0, 0, tzinfo=UTC).timestamp()


def _wall_offset(u):
    return -4 * 3600 if DST_START <= u < DST_END else -5 * 3600


def _instants_of(wall):
    """Every instant (seconds) a New York wall-clock reading (given as seconds as if UTC) can mean: one for an ordinary
    time, two for the repeated hour, and for the missing hour both of the two readings an offset change allows."""
    candidates = (wall + 4 * 3600, wall + 5 * 3600)
    valid = [u for u in candidates if wall == u + _wall_offset(u)]
    return valid or list(candidates)


def test_four_thousand_generated_cases_of_the_age_rule_including_the_daylight_saving_days():
    rng = random.Random(1113)
    days = [datetime(2026, 3, d, tzinfo=UTC).timestamp() for d in (7, 8, 9)] + \
           [datetime(2026, m, d, tzinfo=UTC).timestamp() for m, d in ((10, 31), (11, 1), (11, 2))]
    seen = {"agree": 0, "no second quote": 0, "ambiguous": 0, "missing": 0}
    for i in range(4000):
        max_age = rng.choice([1800, 1800, 1800, 60, 3600, rng.randint(1, 7200)])
        edge = rng.choice([-max_age - 1, -max_age, -max_age + 1, 301, 300, 299, 0, rng.randint(-max_age - 600, 600)])
        if rng.random() < 0.3:
            # a wall time read straight off the clock around a change, whatever it means; now placed relative to it
            centre = rng.choice([DST_START, DST_END])
            wall = float(int(centre) + _wall_offset(centre) + rng.randint(-7200, 7200))
            instants = _instants_of(wall)
            now_u = float(rng.choice(instants) - edge)
        else:
            base = rng.choice(days) if rng.random() < 0.7 else datetime(2026, 1, 1, tzinfo=UTC).timestamp() + rng.randint(0, 364 * 86400)
            now_u = float(int(base) + rng.randint(0, 86399))
            trade_u = now_u + edge
            wall = float(int(trade_u + _wall_offset(trade_u)))
            instants = _instants_of(wall)
        text = datetime.fromtimestamp(wall, UTC).strftime("%Y-%m-%dT%H:%M:%S")
        ok_a = all(now_u - max_age <= u <= now_u + 300 for u in instants)
        fetched_before = rng.choice([-1, 0, 1, 119, 120, 121, rng.randint(-60, 300)])
        fetched = now_u - fetched_before
        ok_b = 0 <= fetched_before <= 120
        snap_offset = rng.choice([-121, -120, -119, 0, 119, 120, 121, rng.randint(-300, 300)])
        ok_c = -120 <= snap_offset <= 120
        second = sec(99.98, 100.02, None, text, fetched=fetched, snapshot=fetched + snap_offset)
        want = "agree" if (ok_a and ok_b and ok_c) else "no second quote"
        got = sq.compare(100.0, second, symbol="SPY", now=datetime.fromtimestamp(now_u, UTC), max_age_seconds=max_age)
        assert got["verdict"] == want, (i, text, now_u, max_age, fetched_before, snap_offset, instants, got)
        seen[want] += 1
        if len(instants) == 2 and instants[0] != instants[1]:
            seen["ambiguous" if wall in (u + _wall_offset(u) for u in instants) else "missing"] += 1
    assert seen["agree"] > 300 and seen["no second quote"] > 300 and seen["ambiguous"] > 10 and seen["missing"] > 10, seen


# ---- the probe

class Out:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.extend(str(text).splitlines() or [""])


def first_source(mapping, calls=None):
    def f(symbols):
        if calls is not None:
            calls.append(list(symbols))
        if isinstance(mapping, BaseException):
            raise mapping
        return {s: mapping[s] for s in symbols if s in mapping}
    return f


FIRST_T = datetime(2026, 11, 13, 20, 59, tzinfo=timezone.utc).timestamp()


def test_the_probe_prints_both_prices_both_times_the_difference_and_the_verdict():
    calls, out = [], Out()
    fake = Fake(REAL_SPY)
    code = sq.probe(["SPY"], first_quotes=first_source({"SPY": (752.6, FIRST_T)}, calls), fetch=fake, clock=PCLOCK,
                    sleep=lambda s: None, out=out)
    text = "\n".join(out.lines)
    assert code == 0 and calls == [["SPY"]] and len(fake.calls) == 1
    for part in ("SPY", "752.6", "752.54", "752.5", "752.55", "2026-11-13T20:59:00Z", "2026-11-13T16:00:00", "+0.01%", "agree"):
        assert part in text, (part, text)


def test_the_probe_reports_a_disagreement_and_a_missing_second_quote():
    out = Out()
    sq.probe(["SPY", "XLE"], first_quotes=first_source({"SPY": (800.0, FIRST_T), "XLE": (64.8, FIRST_T)}),
             fetch=Fake(REAL_SPY, b"garbage"), clock=PCLOCK, sleep=lambda s: None, out=out)
    spy = [l for l in out.lines if l.startswith("SPY")]
    assert len(spy) == 1 and "disagree" in spy[0] and "+6.3" in spy[0]
    xle = [l for l in out.lines if l.startswith("XLE")]
    assert len(xle) == 1 and "no second quote" in xle[0]


def test_the_probe_survives_the_first_source_failing_or_missing_a_symbol():
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source(RuntimeError("down")), fetch=Fake(REAL_SPY), clock=PCLOCK, sleep=lambda s: None, out=out)
    line = [l for l in out.lines if l.startswith("SPY")][0]
    assert "first source failed" in line and "752.54" in line and "agree" not in line.replace("disagree", "")
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({}), fetch=Fake(REAL_SPY), clock=PCLOCK, sleep=lambda s: None, out=out)
    assert "no first quote" in [l for l in out.lines if l.startswith("SPY")][0]
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({"SPY": (float("nan"), FIRST_T)}), fetch=Fake(REAL_SPY), clock=PCLOCK,
             sleep=lambda s: None, out=out)
    assert "no first quote" in [l for l in out.lines if l.startswith("SPY")][0]


def test_the_probe_takes_at_most_ten_symbols_and_refuses_before_any_request():
    syms = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    calls, fake, out = [], Fake(REAL_SPY), Out()
    sq.probe(syms, first_quotes=first_source({}, calls), fetch=fake, clock=PCLOCK, sleep=lambda s: None, out=out)
    assert calls == [syms] and len(fake.calls) == 10                      # ten is fine, one request each
    calls, fake, out = [], Fake(REAL_SPY), Out()
    assert sq.probe(syms + ["K"], first_quotes=first_source({}, calls), fetch=fake, clock=PCLOCK, sleep=lambda s: None, out=out) == 2
    assert calls == [] and fake.calls == [] and any("at most 10" in l for l in out.lines)


def test_the_probe_refuses_a_bad_symbol_before_any_request():
    for bad in ("spy", "SPY?x=1", "TOOLONGX", "", "SPY/../x"):
        calls, fake, out = [], Fake(REAL_SPY), Out()
        assert sq.probe(["SPY", bad], first_quotes=first_source({}, calls), fetch=fake, clock=PCLOCK, sleep=lambda s: None, out=out) == 2
        assert calls == [] and fake.calls == []
    calls, fake, out = [], Fake(REAL_SPY), Out()
    assert sq.probe([], first_quotes=first_source({}, calls), fetch=fake, clock=PCLOCK, sleep=lambda s: None, out=out) == 2
    assert calls == [] and fake.calls == []


def test_a_repeated_symbol_is_fetched_once():
    calls, fake, out = [], Fake(REAL_SPY), Out()
    sq.probe(["SPY", "SPY"], first_quotes=first_source({}, calls), fetch=fake, clock=PCLOCK, sleep=lambda s: None, out=out)
    assert calls == [["SPY"]] and len(fake.calls) == 1


def test_the_default_first_source_is_the_order_steps_own_function_by_import(monkeypatch):
    from valedia_trader.execution_and_risk import order_step
    seen = []
    monkeypatch.setattr(order_step, "minute_quotes", lambda symbols: seen.append(list(symbols)) or {"SPY": (752.6, FIRST_T)})
    out = Out()
    sq.probe(["SPY"], fetch=Fake(REAL_SPY), clock=PCLOCK, sleep=lambda s: None, out=out)
    assert seen == [["SPY"]] and "752.6" in "\n".join(out.lines)


def test_main_runs_the_probe_subcommand_and_refuses_bad_arguments():
    out = Out()
    assert sq.main(["probe", "SPY"], first_quotes=first_source({"SPY": (752.6, FIRST_T)}), fetch=Fake(REAL_SPY), clock=PCLOCK,
                   sleep=lambda s: None, out=out) == 0
    fake = Fake(REAL_SPY)
    assert sq.main(["probe", "spy"], first_quotes=first_source({}), fetch=fake, clock=PCLOCK, sleep=lambda s: None, out=Out()) == 2
    assert fake.calls == []
    with pytest.raises(SystemExit) as e:
        sq.main([], fetch=fake, out=Out())
    assert e.value.code == 2 and fake.calls == []
    with pytest.raises(SystemExit):
        sq.main(["probe"], fetch=fake, out=Out())
    with pytest.raises(SystemExit):
        sq.main(["nonsense", "SPY"], fetch=fake, out=Out())
    assert fake.calls == []


def test_the_probe_prints_zero_as_0_00_percent_never_minus_zero():
    for first in (752.525, 752.524, 752.526):
        out = Out()
        sq.probe(["SPY"], first_quotes=first_source({"SPY": (first, FIRST_T)}), fetch=Fake(REAL_SPY), clock=PCLOCK,
                 sleep=lambda s: None, out=out)
        line = [l for l in out.lines if l.startswith("SPY")][0]
        assert "| 0.00% |" in line and "-0.00%" not in line and "+0.00%" not in line, line
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({"SPY": (752.4, FIRST_T)}), fetch=Fake(REAL_SPY), clock=PCLOCK, sleep=lambda s: None, out=out)
    assert "| -0.02% |" in [l for l in out.lines if l.startswith("SPY")][0]


def test_the_probe_gives_compare_the_symbol_and_the_callers_clock(monkeypatch):
    seen = []
    real = sq.compare

    def spy(first, second, **kw):
        seen.append(kw)
        return real(first, second, **kw)
    monkeypatch.setattr(sq, "compare", spy)
    sq.probe(["SPY", "XLE"], first_quotes=first_source({"SPY": (752.6, FIRST_T), "XLE": (64.8, FIRST_T)}),
             fetch=Fake(REAL_SPY, REAL_XLE), clock=PCLOCK, sleep=lambda s: None, out=Out())
    assert [k["symbol"] for k in seen] == ["SPY", "XLE"]
    assert all(k["now"] == datetime.fromtimestamp(PNOW, timezone.utc) and k["now"].tzinfo is not None for k in seen)


def test_the_probe_judges_age_by_its_clock():
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({"SPY": (752.6, FIRST_T)}), fetch=Fake(REAL_SPY), clock=lambda: PNOW + 3600,
             sleep=lambda s: None, out=out)
    line = [l for l in out.lines if l.startswith("SPY")][0]
    assert "no second quote" in line and "agree" not in line
