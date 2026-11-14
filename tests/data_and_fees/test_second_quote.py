"""Tests for the second price source (Cboe delayed quotes): the reader, the comparison, the probe.

No test touches the network: the fetch function is injected and faked, and the redirect rules are exercised through a
fake HTTPS handler. The two real answers kept below were read on 2026-11-13 (one request each), trimmed of the
volatility fields."""
import io
import json
import re
import math
import functools
import itertools
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from datetime import datetime, timedelta, timezone
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


# ======================================================================================================================
# T28b: licensed, real-time second sources (Alpaca, Finnhub) and the consensus
# ======================================================================================================================

AID, ASEC, FTOK = "PKFAKEID0123456789ABCD", "fakeAlpacaSecret0123456789abcdefXYZ", "fakeFinnhubToken0123456789qrstuv"
KEYS = {"ALPACA_API_KEY": AID, "ALPACA_SECRET_KEY": ASEC, "FINNHUB_API_KEY": FTOK}
FAKE_KEYS = (AID, ASEC, FTOK)
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/snapshots?symbols=%s&feed=iex"
FINNHUB_URL = "https://finnhub.io/api/v1/quote?symbol=%s"


def creds(name):
    return KEYS[name]


def zt(offset, frac="", base=None):
    """An RFC 3339 UTC time `offset` seconds from NOW (or from `base`), with an optional fraction of a second."""
    t = (NOW if base is None else base) + offset
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ("." + frac if frac else "") + "Z"


def alp_block(bid=752.5, ask=752.55, band_t=-30, last=752.54, last_t=-20, frac="092799711"):
    return {"latestQuote": {"ap": ask, "as": 3, "ax": "V", "bp": bid, "bs": 4, "bx": "V", "c": ["R"], "t": zt(band_t, frac), "z": "B"},
            "latestTrade": {"c": ["@"], "i": 12345, "p": last, "s": 100, "t": zt(last_t, frac), "x": "V", "z": "B"},
            "minuteBar": {"c": 752.5, "h": 752.6, "l": 752.4, "n": 10, "o": 752.45, "t": zt(-60), "v": 1000, "vw": 752.5},
            "dailyBar": {"c": 754.37, "h": 760.12, "l": 747.88, "n": 100, "o": 760.05, "t": zt(-3600), "v": 29900315, "vw": 752.0},
            "prevDailyBar": {"c": 757.12, "h": 758.0, "l": 750.0, "n": 100, "o": 751.0, "t": zt(-90000), "v": 30000000, "vw": 754.0}}


def alp(symbol="SPY", **kw):
    return json.dumps({symbol: alp_block(**kw)}).encode()


def fin(c=754.37, t=-20, **over):
    doc = {"c": c, "d": -2.75, "dp": -0.3632, "h": 760.12, "l": 747.88, "o": 760.05, "pc": 757.12, "t": int(NOW + t)}
    doc.update(over)
    return json.dumps(doc).encode()


# the answers as the lead saw them on 2026-11-13 at 21:04 New York time (market closed): kept whole, nothing but market data
REAL_ALPACA_XLE = json.dumps({"XLE": {
    "latestQuote": {"ap": 66.86, "as": 1, "ax": "V", "bp": 62.81, "bs": 1, "bx": "V", "c": ["R"], "t": "2026-11-13T21:00:00.092799711Z", "z": "B"},
    "latestTrade": {"c": ["@", "T"], "i": 4321, "p": 64.93, "s": 100, "t": "2026-11-13T21:55:04.282408928Z", "x": "V", "z": "B"},
    "minuteBar": {"c": 64.9, "h": 64.95, "l": 64.9, "n": 3, "o": 64.9, "t": "2026-11-13T20:59:00Z", "v": 300, "vw": 64.92},
    "dailyBar": {"c": 64.96, "h": 64.965, "l": 64.155, "n": 900, "o": 64.91, "t": "2026-11-13T05:00:00Z", "v": 17648505, "vw": 64.7},
    "prevDailyBar": {"c": 64.33, "h": 64.5, "l": 63.9, "n": 800, "o": 64.0, "t": "2026-11-12T05:00:00Z", "v": 15000000, "vw": 64.2}}}).encode()
REAL_FINNHUB_SPY = b'{"c": 754.37, "d": -2.75, "dp": -0.3632, "h": 760.12, "l": 747.88, "o": 760.05, "pc": 757.12, "t": 1794603600}'


class KFake:
    """A fetch function for all three services: records (url, timeout, headers) and answers by host (a list is a queue whose
    last item repeats; an exception is raised; a callable is called with (url, timeout, headers))."""
    def __init__(self, alpaca=None, finnhub=None, cboe=None):
        self.by_host = {"data.alpaca.markets": alpaca, "finnhub.io": finnhub, "cdn.cboe.com": cboe}
        self.calls = []

    def __call__(self, url, timeout, headers=None):
        self.calls.append((url, timeout, None if headers is None else dict(headers)))
        a = self.by_host[urllib.parse.urlsplit(url).hostname]
        if isinstance(a, list):
            a = a.pop(0) if len(a) > 1 else a[0]
        if callable(a):
            a = a(url, timeout, headers)
        if isinstance(a, BaseException):
            raise a
        return a

    def hosts(self):
        return [urllib.parse.urlsplit(c[0]).hostname for c in self.calls]


def aq(raw=None, symbol="SPY", fake=None, **kw):
    fake = fake or KFake(alpaca=raw if raw is not None else alp(symbol))
    return sq.alpaca_quote(symbol, fetch=fake, clock=CLOCK, credentials=kw.pop("credentials", creds), sleep=kw.pop("sleep", lambda s: None), **kw)


def fq(raw=None, symbol="SPY", fake=None, **kw):
    fake = fake or KFake(finnhub=raw if raw is not None else fin())
    return sq.finnhub_quote(symbol, fetch=fake, clock=CLOCK, credentials=kw.pop("credentials", creds), sleep=kw.pop("sleep", lambda s: None), **kw)


# ---- the readers: the real shapes, and what is sent

def test_the_real_alpaca_answer_is_read_into_the_normal_form():
    fake = KFake(alpaca=REAL_ALPACA_XLE)
    out = sq.alpaca_quote("XLE", fetch=fake, clock=CLOCK, credentials=creds, sleep=lambda s: None)
    assert set(out) == {"symbol", "bid", "ask", "band_time", "last", "last_time", "fetched_at", "source"}
    assert (out["symbol"], out["bid"], out["ask"], out["last"], out["fetched_at"], out["source"]) == ("XLE", 62.81, 66.86, 64.93, NOW, "alpaca-iex")
    assert out["band_time"] == pytest.approx(datetime(2026, 11, 13, 21, 0, 0, tzinfo=timezone.utc).timestamp() + 0.092799711, abs=1e-6)
    assert out["last_time"] == pytest.approx(datetime(2026, 11, 13, 21, 55, 4, tzinfo=timezone.utc).timestamp() + 0.282408928, abs=1e-6)
    assert fake.calls == [(ALPACA_URL % "XLE", 5.0, {"APCA-API-KEY-ID": AID, "APCA-API-SECRET-KEY": ASEC})]


def test_the_real_finnhub_answer_is_read_into_the_normal_form():
    fake = KFake(finnhub=REAL_FINNHUB_SPY)
    out = sq.finnhub_quote("SPY", fetch=fake, clock=CLOCK, credentials=creds, sleep=lambda s: None)
    assert out == {"symbol": "SPY", "bid": None, "ask": None, "band_time": None, "last": 754.37, "last_time": 1794603600.0,
                   "fetched_at": NOW, "source": "finnhub"}
    assert fake.calls == [(FINNHUB_URL % "SPY", 5.0, {"X-Finnhub-Token": FTOK})]


def test_the_credentials_are_asked_for_by_the_three_names_and_nothing_else():
    asked = []

    def record(name):
        asked.append(name)
        return KEYS[name]
    aq(credentials=record)
    fq(credentials=record)
    assert asked == ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY"]


def test_the_default_credentials_are_the_secrets_module_by_those_names(monkeypatch):
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    fake = KFake(alpaca=alp(), finnhub=fin())
    assert sq.alpaca_quote("SPY", fetch=fake, clock=CLOCK, sleep=lambda s: None)["last"] == 752.54
    assert sq.finnhub_quote("SPY", fetch=fake, clock=CLOCK, sleep=lambda s: None)["last"] == 754.37
    assert fake.calls[0][2] == {"APCA-API-KEY-ID": AID, "APCA-API-SECRET-KEY": ASEC} and fake.calls[1][2] == {"X-Finnhub-Token": FTOK}
    for name in KEYS:
        monkeypatch.delenv(name)
    silent = KFake(alpaca=alp(), finnhub=fin())
    assert sq.alpaca_quote("SPY", fetch=silent, clock=CLOCK, sleep=lambda s: None) is None
    assert sq.finnhub_quote("SPY", fetch=silent, clock=CLOCK, sleep=lambda s: None) is None
    assert silent.calls == []                                                   # a missing key: no request at all


BAD_KEYS = [None, "", " ", "  \t ", 5, b"abc", ["x"], "has space", "line\nbreak", "tab\there", "nul\x00byte", "cafékey", "x" * 300,
            "bell\x07", "cr\rkey", " key"]


@pytest.mark.parametrize("bad", BAD_KEYS, ids=[repr(b)[:20] for b in BAD_KEYS])
def test_a_missing_or_unusable_key_is_no_quote_and_no_request_to_that_service(bad):
    for broken in KEYS:
        fake = KFake(alpaca=alp(), finnhub=fin())
        keys = {**KEYS, broken: bad}
        a = sq.alpaca_quote("SPY", fetch=fake, clock=CLOCK, credentials=keys.__getitem__, sleep=lambda s: None)
        f = sq.finnhub_quote("SPY", fetch=fake, clock=CLOCK, credentials=keys.__getitem__, sleep=lambda s: None)
        if broken == "FINNHUB_API_KEY":
            assert f is None and a is not None and fake.hosts() == ["data.alpaca.markets"]
        else:
            assert a is None and f is not None and fake.hosts() == ["finnhub.io"]


def test_a_credentials_function_that_raises_is_no_quote_and_never_an_exception():
    for exc in (KeyError("ALPACA_API_KEY"), RuntimeError("boom " + AID), ValueError(ASEC), OSError(FTOK), SystemError("x")):
        def broken(name, exc=exc):
            raise exc
        fake = KFake(alpaca=alp(), finnhub=fin())
        assert aq(fake=fake, credentials=broken) is None and fq(fake=fake, credentials=broken) is None
        assert fake.calls == []


def test_a_secrets_error_for_a_missing_key_is_no_quote(monkeypatch):
    from valedia_trader.core import secrets
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    fake = KFake(alpaca=alp())
    assert sq.alpaca_quote("SPY", fetch=fake, clock=CLOCK, credentials=secrets.secret, sleep=lambda s: None) is None
    assert fake.calls == []


def test_the_symbol_rule_is_applied_before_any_request():
    for reader in (sq.alpaca_quote, sq.finnhub_quote):
        for bad in ("spy", "SPY,QQQ", "SPY&feed=sip", "SPY\n", "", None, 5, "TOOLONGX", "A B", "SPY/../x", "SPY%2c"):
            fake = KFake(alpaca=alp(), finnhub=fin())
            with pytest.raises(ValueError):
                reader(bad, fetch=fake, clock=CLOCK, credentials=creds)
            assert fake.calls == []


def test_only_the_symbol_is_sent_in_the_query_and_the_url_is_exact():
    for sym in ("SPY", "BRK.B", "BRK-B", "XLE"):
        a, f = KFake(alpaca=alp(sym)), KFake(finnhub=fin())
        aq(symbol=sym, fake=a)
        fq(symbol=sym, fake=f)
        assert a.calls[0][0] == ALPACA_URL % sym and f.calls[0][0] == FINNHUB_URL % sym
        for url in (a.calls[0][0], f.calls[0][0]):
            assert "key" not in url.lower() and "secret" not in url.lower() and "token" not in url.lower()


def test_each_key_goes_only_to_its_own_host_in_the_header_named():
    fake = KFake(alpaca=alp(), finnhub=fin())
    aq(fake=fake)
    fq(fake=fake)
    (a_url, _, a_headers), (f_url, _, f_headers) = fake.calls
    assert urllib.parse.urlsplit(a_url).hostname == "data.alpaca.markets" and a_headers == {"APCA-API-KEY-ID": AID, "APCA-API-SECRET-KEY": ASEC}
    assert urllib.parse.urlsplit(f_url).hostname == "finnhub.io" and f_headers == {"X-Finnhub-Token": FTOK}
    assert FTOK not in str(a_headers) + a_url and AID not in str(f_headers) + f_url and ASEC not in str(f_headers) + f_url


# ---- the readers: times, partial answers, hostile answers

def test_alpaca_times_are_read_with_any_number_of_decimals_in_utc():
    base = datetime(2026, 11, 13, 21, 10, 0, tzinfo=timezone.utc).timestamp()
    for frac, extra in (("", 0.0), ("5", 0.5), ("123", 0.123), ("092799711", 0.092799711), ("000000001", 1e-9), ("999999999", 0.999999999)):
        text = "2026-11-13T21:10:00" + ("." + frac if frac else "") + "Z"
        block = alp_block()
        block["latestQuote"]["t"] = text
        out = aq(json.dumps({"SPY": block}).encode())
        assert abs(out["band_time"] - (base + extra)) < 1e-6, text


@pytest.mark.parametrize("bad", ["2026-11-13T21:10:00", "2026-11-13T21:10:00+00:00", "2026-11-13 21:10:00Z", "2026-11-13T21:10:00.Z",
                                 "2026-11-13T21:10:00.1234567890Z", "2026-13-13T21:10:00Z", "2026-11-31T21:10:00Z", "2026-11-13T25:10:00Z",
                                 "2026-11-13T21:10:60Z", "2026-11-13T21:10:00z", "yesterday", "", None, 5, True, ["2026-11-13T21:10:00Z"], " 2026-11-13T21:10:00Z"])
def test_an_unreadable_alpaca_time_is_none_and_the_numbers_stay(bad):
    block = alp_block()
    block["latestQuote"]["t"] = bad
    block["latestTrade"]["t"] = bad
    out = aq(json.dumps({"SPY": block}).encode())
    assert out is not None and out["band_time"] is None and out["last_time"] is None and out["bid"] == 752.5 and out["last"] == 752.54


def test_alpaca_numbers_are_strict_one_by_one():
    for bad in ("752.5", True, None, -1.0, 0, 0.0, float("inf"), 1e7 + 1, [1], {}):
        block = alp_block()
        block["latestQuote"]["bp"] = bad
        out = aq(json.dumps({"SPY": block}, allow_nan=True).encode())
        assert out is not None and out["bid"] is None and out["ask"] == 752.55 and out["last"] == 752.54
        block = alp_block()
        block["latestTrade"]["p"] = bad
        out = aq(json.dumps({"SPY": block}, allow_nan=True).encode())
        assert out is not None and out["last"] is None and out["bid"] == 752.5


def test_alpaca_without_a_last_and_without_a_band_pair_is_none():
    block = alp_block()
    block["latestTrade"]["p"] = 0
    block["latestQuote"]["bp"] = 0
    assert aq(json.dumps({"SPY": block}).encode()) is None
    block = alp_block()
    del block["latestTrade"]
    out = aq(json.dumps({"SPY": block}).encode())
    assert out["last"] is None and out["last_time"] is None and out["bid"] == 752.5
    block = alp_block()
    del block["latestQuote"]
    out = aq(json.dumps({"SPY": block}).encode())
    assert out["bid"] is None and out["band_time"] is None and out["last"] == 752.54
    assert aq(json.dumps({"SPY": {"latestQuote": 5, "latestTrade": "x"}}).encode()) is None


def alpaca_hostile():
    out = {}

    def put(name, raw):
        out[name] = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
    good = json.loads(alp())
    put("another-symbols-block", {"QQQ": good["SPY"]})
    put("lowercase-symbol-key", {"spy": good["SPY"]})
    put("empty-object", {})
    put("symbol-block-null", {"SPY": None})
    put("symbol-block-string", {"SPY": "752.5"})
    put("symbol-block-list", {"SPY": [good["SPY"]]})
    put("data-wrapped", {"data": {"SPY": good["SPY"]}})
    put("list-of-blocks", [good])
    put("string", "SPY")
    put("null", None)
    put("number", 752.5)
    put("html-error-page", b"<html><body><h1>403 Forbidden</h1></body></html>")
    put("message-body", {"code": 40110000, "message": "request is not authorized"})
    put("empty", b"")
    put("truncated", alp()[:-30])
    put("invalid-utf8", b'{"SPY": {"latestTrade": {"p": 1.0, "x": "\xff\xfe"}}}')
    put("utf16", alp().decode().encode("utf-16"))
    put("duplicate-symbol-key", b'{"SPY": ' + json.dumps(good["SPY"]).encode() + b', "SPY": ' + json.dumps(good["SPY"]).encode() + b"}")
    put("duplicate-inside-block", b'{"SPY": {"latestTrade": {"p": 1.0, "p": 2.0, "t": "2026-11-13T21:19:00Z"}}}')
    put("duplicate-escaped", b'{"SPY": {"latestTrade": {"p": 1.0, "\\u0070": 2.0, "t": "2026-11-13T21:19:00Z"}}}')
    put("ten-megabytes-of-valid-data", json.dumps({"SPY": good["SPY"], "pad": "x" * (10 * 1024 * 1024)}).encode())
    put("ten-megabytes-of-junk", b"x" * (10 * 1024 * 1024))
    put("deep-nesting", b"[" * 30000 + b"]" * 30000)
    put("every-number-nan", alp().replace(b"752.5,", b"NaN,").replace(b"752.55", b"NaN").replace(b"752.54", b"NaN"))
    put("every-number-infinity", alp().replace(b"752.5,", b"Infinity,").replace(b"752.55", b"-Infinity").replace(b"752.54", b"Infinity"))
    put("every-number-1e999", alp().replace(b"752.5,", b"1e999,").replace(b"752.55", b"-1e999").replace(b"752.54", b"1e999"))
    put("every-number-giant-integer", alp().replace(b"752.5,", b"1" + b"0" * 400 + b",").replace(b"752.55", b"-" + b"1" + b"0" * 400).replace(b"752.54", b"1" + b"0" * 400))
    for name, bad in {"string": "752.5", "list": [1], "dict": {"a": 1}, "bool": True, "null": None, "negative": -5.0, "zero": 0, "huge": 1.0000001e7}.items():
        block = alp_block()
        block["latestQuote"].update(bp=bad, ap=bad)
        block["latestTrade"].update(p=bad)
        put("every-number-" + name, {"SPY": block})
    return out


ALPACA_HOSTILE = alpaca_hostile()


@pytest.mark.parametrize("name", sorted(ALPACA_HOSTILE))
def test_a_hostile_alpaca_answer_is_no_quote_never_an_exception(name):
    fake = KFake(alpaca=ALPACA_HOSTILE[name])
    assert sq.alpaca_quote("SPY", fetch=fake, clock=CLOCK, credentials=creds, sleep=lambda s: None) is None
    assert len(fake.calls) == 1                                                  # an unusable answer is not retried


def finnhub_hostile():
    out = {}

    def put(name, raw):
        out[name] = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
    t = int(NOW - 20)
    put("unknown-symbol-zeros", {"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0})
    put("c-zero", {"c": 0, "t": t})
    put("t-zero", {"c": 754.37, "t": 0})
    put("t-missing", {"c": 754.37})
    put("c-missing", {"t": t})
    put("t-string", {"c": 754.37, "t": str(t)})
    put("t-bool", {"c": 754.37, "t": True})
    put("t-negative", {"c": 754.37, "t": -t})
    put("t-huge", {"c": 754.37, "t": 10 ** 15})
    put("t-year-10000", {"c": 754.37, "t": 253402300800})
    put("t-nan", b'{"c": 754.37, "t": NaN}')
    put("t-infinity", b'{"c": 754.37, "t": Infinity}')
    put("t-giant", b'{"c": 754.37, "t": 1' + b"0" * 400 + b"}")
    put("c-string", {"c": "754.37", "t": t})
    put("c-bool", {"c": True, "t": t})
    put("c-negative", {"c": -754.37, "t": t})
    put("c-huge", {"c": 1.0000001e7, "t": t})
    put("c-nan", b'{"c": NaN, "t": %d}' % t)
    put("c-infinity", b'{"c": -Infinity, "t": %d}' % t)
    put("c-1e999", b'{"c": 1e999, "t": %d}' % t)
    put("c-list", {"c": [754.37], "t": t})
    put("c-dict", {"c": {"v": 754.37}, "t": t})
    put("list", [{"c": 754.37, "t": t}])
    put("string", "754.37")
    put("null", None)
    put("number", 754.37)
    put("wrapped", {"quote": {"c": 754.37, "t": t}})
    put("error-json", {"error": "Invalid API key"})
    put("html", b"<!DOCTYPE html><html><title>429 Too Many Requests</title></html>")
    put("empty", b"")
    put("truncated", fin()[:-10])
    put("invalid-utf8", b'{"c": 754.37, "t": %d, "x": "\xff"}' % t)
    put("duplicate-c", b'{"c": 754.37, "c": 1.0, "t": %d}' % t)
    put("duplicate-t", b'{"c": 754.37, "t": %d, "t": %d}' % (t, t))
    put("ten-megabytes", json.dumps({"c": 754.37, "t": t, "pad": "x" * (10 * 1024 * 1024)}).encode())
    put("deep-nesting", b"[" * 30000 + b"]" * 30000)
    return out


FINNHUB_HOSTILE = finnhub_hostile()


@pytest.mark.parametrize("name", sorted(FINNHUB_HOSTILE))
def test_a_hostile_finnhub_answer_is_no_quote_never_an_exception(name):
    fake = KFake(finnhub=FINNHUB_HOSTILE[name])
    assert sq.finnhub_quote("SPY", fetch=fake, clock=CLOCK, credentials=creds, sleep=lambda s: None) is None
    assert len(fake.calls) == 1


def test_finnhub_reads_the_price_and_its_own_time_and_nothing_more():
    out = fq(fin(c=100, t=-5))
    assert out["last"] == 100.0 and type(out["last"]) is float and out["last_time"] == float(int(NOW - 5)) and out["bid"] is None
    assert fq(json.dumps({"c": 100.5, "t": NOW - 5.5, "h": None, "l": "x"}).encode())["last_time"] == NOW - 5.5     # a float time is fine


def test_a_403_or_429_or_any_error_status_is_no_quote_and_client_errors_are_not_retried():
    for code in (400, 401, 403, 404, 429):
        err = urllib.error.HTTPError("https://x/", code, "no", None, None)
        for reader, host in ((aq, "alpaca"), (fq, "finnhub")):
            fake = KFake(**{host: err})
            sleeps = []
            assert reader(fake=fake, sleep=sleeps.append) is None
            assert len(fake.calls) == 1 and sleeps == []
    for code in (500, 502, 503):
        err = urllib.error.HTTPError("https://x/", code, "no", None, None)
        fake, sleeps = KFake(alpaca=err), []
        assert aq(fake=fake, sleep=sleeps.append) is None
        assert len(fake.calls) == 2 and sleeps == [2.0]


def test_two_tries_two_seconds_apart_for_both_readers():
    for reader, host in ((aq, "alpaca"), (fq, "finnhub")):
        fake, sleeps = KFake(**{host: OSError("boom")}), []
        assert reader(fake=fake, sleep=sleeps.append) is None
        assert len(fake.calls) == 2 and sleeps == [2.0] and all(c[1] == 5.0 for c in fake.calls)
        fake = KFake(**{host: [TimeoutError("slow"), alp() if host == "alpaca" else fin()]})
        assert reader(fake=fake, sleep=lambda s: None) is not None and len(fake.calls) == 2
        fake = KFake(**{host: [OSError("a"), ValueError("b"), alp() if host == "alpaca" else fin()]})
        assert reader(fake=fake, sleep=lambda s: None) is None and len(fake.calls) == 2


def test_a_try_longer_than_ten_seconds_fails_for_both_readers():
    for reader, host, body in ((sq.alpaca_quote, "alpaca", alp()), (sq.finnhub_quote, "finnhub", fin())):
        fc = FakeClock(NOW)
        calls = []

        def slow(url, timeout, headers=None):
            calls.append(url)
            fc.t += 2100.0
            return body
        assert reader("SPY", fetch=slow, clock=fc, credentials=creds, sleep=lambda s: None) is None and len(calls) == 2

        def ten(url, timeout, headers=None):
            fc.t += 10.0
            return body
        assert reader("SPY", fetch=ten, clock=fc, credentials=creds, sleep=lambda s: None) is not None

        def over(url, timeout, headers=None):
            fc.t += 10.5
            return body
        assert reader("SPY", fetch=over, clock=fc, credentials=creds, sleep=lambda s: None) is None


# ---- the real keyed fetch (a fake HTTPS handler stands in; nothing leaves the machine)

class Recorder(FakeHTTPS):
    def __init__(self, routes):
        super().__init__(routes)
        self.requests = []

    def https_open(self, req):
        self.requests.append({"url": req.full_url, "method": req.get_method(), "headers": dict(req.header_items()),
                              "unredirected": dict(req.unredirected_hdrs), "data": req.data, "regular": dict(req.headers)})
        return super().https_open(req)


AL = ALPACA_URL % "SPY"
FH = FINNHUB_URL % "SPY"
A_HEAD = {"APCA-API-KEY-ID": AID, "APCA-API-SECRET-KEY": ASEC}
F_HEAD = {"X-Finnhub-Token": FTOK}


def kf(routes, url, headers, **kw):
    handler = Recorder(routes)
    return handler, lambda: sq.keyed_fetch(url, 5.0, headers, opener=sq._build_opener(handler), **kw)


def test_the_keyed_fetch_sends_the_keys_as_headers_to_their_own_host_only():
    handler, go = kf({AL: (200, None, io.BytesIO(alp()))}, AL, A_HEAD)
    assert go() == alp()
    (req,) = handler.requests
    assert req["url"] == AL and req["method"] == "GET" and req["data"] is None
    sent = {k.lower(): v for k, v in req["headers"].items()}
    assert sent["apca-api-key-id"] == AID and sent["apca-api-secret-key"] == ASEC
    assert set(sent) <= {"apca-api-key-id", "apca-api-secret-key", "user-agent", "accept", "host"}
    handler, go = kf({FH: (200, None, io.BytesIO(fin()))}, FH, F_HEAD)
    assert go() == fin()
    sent = {k.lower(): v for k, v in handler.requests[0]["headers"].items()}
    assert sent["x-finnhub-token"] == FTOK and set(sent) <= {"x-finnhub-token", "user-agent", "accept", "host"}


def test_the_keys_are_unredirected_headers_so_nothing_can_copy_them_to_another_place():
    handler, go = kf({AL: (200, None, io.BytesIO(alp()))}, AL, A_HEAD)
    go()
    req = handler.requests[0]
    assert {k.lower() for k in req["unredirected"]} - {"host"} == {"apca-api-key-id", "apca-api-secret-key"}
    assert not any(k.lower().startswith(("apca", "x-finnhub")) for k in req["regular"])


@pytest.mark.parametrize("url,headers", [
    (AL, F_HEAD), (FH, A_HEAD), (AL, {}), (FH, {}), (AL, {"APCA-API-KEY-ID": AID}), (AL, {**A_HEAD, "X-Finnhub-Token": FTOK}),
    (FH, {**F_HEAD, "Authorization": "Bearer " + FTOK}), (AL, {**A_HEAD, "Cookie": "a=b"}), (AL, None), (AL, "key"), (AL, [("APCA-API-KEY-ID", AID)]),
    (AL, {"APCA-API-KEY-ID": "has space", "APCA-API-SECRET-KEY": ASEC}), (AL, {"APCA-API-KEY-ID": AID + "\r\nX-Evil: 1", "APCA-API-SECRET-KEY": ASEC}),
    (FH, {"X-Finnhub-Token": 5}), (FH, {"x-finnhub-token": FTOK}),
    ("http://data.alpaca.markets/v2/stocks/snapshots?symbols=SPY&feed=iex", A_HEAD), ("http://finnhub.io/api/v1/quote?symbol=SPY", F_HEAD),
    ("https://data.alpaca.markets/v2/stocks/snapshots?symbols=SPY&feed=sip", A_HEAD),
    ("https://data.alpaca.markets/v2/stocks/snapshots?symbols=SPY&feed=iex&apca=" + AID, A_HEAD),
    ("https://data.alpaca.markets/v2/stocks/snapshots?symbols=SPY,QQQ&feed=iex", A_HEAD),
    ("https://data.alpaca.markets/v2/stocks/snapshots?symbols=spy&feed=iex", A_HEAD),
    ("https://data.alpaca.markets/v2/stocks/SPY/quotes/latest?feed=iex", A_HEAD), ("https://data.alpaca.markets:443/v2/stocks/snapshots?symbols=SPY&feed=iex", A_HEAD),
    ("https://user@data.alpaca.markets/v2/stocks/snapshots?symbols=SPY&feed=iex", A_HEAD), ("https://data.alpaca.markets.evil.example/v2/stocks/snapshots?symbols=SPY&feed=iex", A_HEAD),
    ("https://finnhub.io/api/v1/quote?symbol=SPY&token=" + FTOK, F_HEAD), ("https://finnhub.io/api/v1/quote?symbol=SPY#x", F_HEAD),
    ("https://finnhub.io/api/v1/quote?symbol=SPY%0d", F_HEAD), ("https://finnhub.io/api/v1/quote?symbol=SPY&a=BC", F_HEAD),
    ("https://finnhub.io/api/v1/quote?symbol=SPY&symbol=QQQ", F_HEAD), ("https://data.alpaca.markets/v2/stocks/snapshots?symbols=SPY&feed=iex&x=Y", A_HEAD),
    ("https://data.alpaca.markets/v2/stocks/snapshots?symbols=SPY&feed=iex&feed=sip", A_HEAD), ("https://data.alpaca.markets/v2/stocks/snapshots?feed=iex&symbols=SPY", A_HEAD),
    ("https://finnhub.io/api/v1/quote?symbol=SPY\n", F_HEAD), ("https://finnhub.io/api/v1//quote?symbol=SPY", F_HEAD), ("https://FINNHUB.IO/api/v1/quote?symbol=SPY", F_HEAD), ("https://finnhub.io/api/v1/search?q=SPY", F_HEAD), ("https://finnhub.io@evil.example/api/v1/quote?symbol=SPY", F_HEAD),
    ("https://cdn.cboe.com/api/global/delayed_quotes/quotes/SPY.json", A_HEAD), ("https://cdn.cboe.com/api/global/delayed_quotes/quotes/SPY.json", F_HEAD),
    ("https://evil.example/v2/stocks/snapshots?symbols=SPY&feed=iex", A_HEAD), (None, A_HEAD), (5, F_HEAD), ("", F_HEAD),
])
def test_the_keyed_fetch_refuses_before_any_request_a_wrong_host_url_or_header_set(url, headers):
    handler = Recorder({})
    with pytest.raises(ValueError) as e:
        sq.keyed_fetch(url, 5.0, headers, opener=sq._build_opener(handler))
    assert handler.requests == []
    for key in FAKE_KEYS:
        assert key not in str(e.value)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_redirect_is_a_failed_try_for_the_keyed_sources_and_nothing_is_sent_on(code):
    for url, head, targets in ((AL, A_HEAD, ["https://data.alpaca.markets/other", FH, "https://evil.example/x", B, "http://data.alpaca.markets/x"]),
                               (FH, F_HEAD, ["https://finnhub.io/other", AL, "https://evil.example/x", A, "https://finnhub.io/api/v1/quote?symbol=QQQ"])):
        for target in targets:
            routes = {url: (code, target, io.BytesIO(b"")), target: (200, None, io.BytesIO(alp()))}
            handler, go = kf(routes, url, head)
            with pytest.raises(urllib.error.HTTPError) as e:
                go()
            assert e.value.code == code
            assert [r["url"] for r in handler.requests] == [url]                    # the target is never requested
            for key in FAKE_KEYS:
                assert key not in str(e.value)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 503])
def test_an_error_status_raises_an_http_error_whose_text_holds_no_key_and_the_body_is_not_read(code):
    body = Counting(("<html>" + AID + ASEC + FTOK + "</html>").encode() * 3)
    handler, go = kf({AL: (code, None, body)}, AL, A_HEAD)
    with pytest.raises(urllib.error.HTTPError) as e:
        go()
    assert e.value.code == code
    for key in FAKE_KEYS:
        assert key not in str(e.value) and key not in repr(e.value)
    assert body.total == 0


def test_an_answer_that_comes_from_another_place_is_refused():
    class Elsewhere(Recorder):
        def https_open(self, req):
            resp = super().https_open(req)
            resp.url = "https://evil.example/answer"
            return resp
    handler = Elsewhere({AL: (200, None, io.BytesIO(alp()))})
    with pytest.raises(ValueError):
        sq.keyed_fetch(AL, 5.0, A_HEAD, opener=sq._build_opener(handler))


def test_the_keyed_fetch_has_the_same_deadline_cap_and_tls_as_the_cboe_fetch():
    fc = FakeClock()
    body = Trickle(fc)
    handler = Recorder({AL: (200, None, body)})
    with pytest.raises(TimeoutError):
        sq.keyed_fetch(AL, 5.0, A_HEAD, opener=sq._build_opener(handler), clock=fc)
    assert body.reads == 3 and fc.t == 12.0
    big = Counting(b"x" * BIG)
    handler, go = kf({AL: (200, None, big)}, AL, A_HEAD)
    with pytest.raises(ValueError):
        go()
    assert big.total <= sq.MAX_BODY_BYTES + 1
    real = sq._build_opener()
    assert [h for h in real.handlers if type(h).__name__ == "_BudgetHTTPS"]


# ---- judging a quote by its own times (alpaca and finnhub)

def nq(bid=None, ask=None, band_t=-30, last=None, last_t=-20, fetched_t=0, symbol="SPY", base=NOW):
    """A normalised quote; times as offsets in seconds from `base` (None: unknown)."""
    return {"symbol": symbol, "bid": bid, "ask": ask, "band_time": None if band_t is None else base + band_t, "last": last,
            "last_time": None if last_t is None else base + last_t, "fetched_at": None if fetched_t is None else base + fetched_t,
            "source": "alpaca-iex"}


def jq(first, q, tol=0.01, now=NOW_DT, symbol="SPY", **kw):
    return sq.judge_quote(first, q, symbol=symbol, now=now, tolerance=tol, **kw)


def test_the_named_constants_of_the_rules():
    assert sq.BAND_MAX_AGE_SECONDS == 120 and sq.BAND_FUTURE_SLACK_SECONDS == 5 and sq.BAND_MAX_WIDTH == 0.005
    assert sq.MAX_FETCH_AGE_SECONDS == 120 and sq.DEFAULT_MAX_AGE_SECONDS == 1800 and sq.FUTURE_SLACK_SECONDS == 300
    assert sq.OVERALL_DEADLINE_SECONDS == 25.0


def test_a_fresh_narrow_band_decides_and_the_difference_is_from_its_mid():
    q = nq(752.5, 752.55, last=752.54)
    out = jq(752.6, q)
    assert out["verdict"] == "agree" and out["basis"] == "bid/ask band" and math.isclose(out["difference"], (752.6 - 752.525) / 752.525)
    assert jq(760.07525, q)["verdict"] == "agree" and jq(760.07526, q)["verdict"] == "disagree"      # ask + 1 % of the mid
    assert jq(744.97475, q)["verdict"] == "agree" and jq(744.97474, q)["verdict"] == "disagree"      # bid - 1 % of the mid
    assert jq(752.6, q, 0.0)["verdict"] == "disagree" and jq(752.55, q, 0.0)["verdict"] == "agree" and jq(752.5, q, 0.0)["verdict"] == "agree"


@pytest.mark.parametrize("age,ok", [(-5, True), (-6, False), (0, True), (1, True), (119, True), (120, True), (121, False), (3600, False)])
def test_the_band_must_be_at_most_120_seconds_old_and_5_ahead(age, ok):
    out = jq(100.0, nq(99.99, 100.01, band_t=-age))
    assert out["verdict"] == ("agree" if ok else "no second quote")


@pytest.mark.parametrize("bid,ask,ok", [
    (399, 401, True),            # width 2 on a mid of 400: exactly 0.5 %
    (399, 402, False),           # a bit over
    (3990, 4010, True), (3990, 4011, False), (3991, 4010, True),
    (100, 100, True), (99.5, 100.5, False),                      # 1 on 100: 1 %
])
def test_the_band_may_be_no_wider_than_half_a_percent_of_its_mid(bid, ask, ok):
    mid = (bid + ask) / 2
    out = jq(mid, nq(float(bid), float(ask), last=None))
    assert out["verdict"] == ("agree" if ok else "no second quote"), (bid, ask)


def test_a_band_too_wide_or_too_old_gives_way_to_a_usable_last_price():
    wide = nq(62.81, 66.86, band_t=-5, last=64.93, last_t=-10)                # the closing quote the lead saw
    out = jq(64.93, wide)
    assert out["verdict"] == "agree" and out["basis"] == "last price" and out["difference"] == 0.0
    assert jq(70.0, wide)["verdict"] == "disagree"
    stale = nq(99.99, 100.01, band_t=-500, last=100.0, last_t=-10)
    assert jq(100.0, stale)["basis"] == "last price" and jq(101.01, stale)["verdict"] == "disagree" and jq(101.0, stale)["verdict"] == "agree"
    assert jq(100.0, nq(99.99, 100.01, band_t=-500, last=None))["verdict"] == "no second quote"
    assert jq(100.0, nq(62.81, 66.86, band_t=-5, last=None))["verdict"] == "no second quote"


@pytest.mark.parametrize("age,ok", [(-300, True), (-301, False), (0, True), (1800, True), (1801, False), (7200, False)])
def test_the_last_trade_must_be_within_the_age_limit_and_300_ahead(age, ok):
    out = jq(100.0, nq(last=100.0, last_t=-age))
    assert out["verdict"] == ("agree" if ok else "no second quote")
    assert jq(100.0, nq(last=100.0, last_t=-60), max_age_seconds=60)["verdict"] == "agree"
    assert jq(100.0, nq(last=100.0, last_t=-61), max_age_seconds=60)["verdict"] == "no second quote"


@pytest.mark.parametrize("fetched_before,ok", [(120, True), (121, False), (0, True), (-1, True), (-30, True), (-31, False), (3600, False)])
def test_the_fetch_must_be_at_most_120_seconds_before_now_and_not_far_after(fetched_before, ok):
    out = jq(100.0, nq(99.99, 100.01, band_t=-10, fetched_t=-fetched_before))
    assert out["verdict"] == ("agree" if ok else "no second quote"), fetched_before


def test_a_crossed_band_is_no_quote_whatever_the_last_price_for_the_licensed_sources_too():
    for q in (nq(102.0, 100.0, last=500.0, last_t=-5), nq(100.01, 100.0, last=100.0), nq(102.0, 100.0, last=100.0, band_t=-500)):
        for first in (500.0, 100.0, 101.0):
            assert jq(first, q) == {"verdict": "no second quote", "difference": None, "basis": jq(first, q)["basis"]}
    assert jq(500.0, nq(102.0, None, last=500.0, last_t=-5))["verdict"] == "agree"          # one side only: the last price


def test_a_last_price_that_contradicts_its_own_usable_band_is_no_quote_but_a_stale_one_does_not():
    q = nq(99.99, 100.01, last=105.0, last_t=-5)
    assert jq(100.0, q)["verdict"] == "no second quote" and jq(105.0, q)["verdict"] == "no second quote"
    assert jq(100.0, nq(99.99, 100.01, last=105.0, last_t=-5000))["verdict"] == "agree"     # that last trade is hours old: not used
    assert jq(100.0, nq(99.99, 100.01, last=101.0, last_t=-5))["verdict"] == "agree"         # inside the band widened by 1 %
    assert jq(100.0, nq(99.99, 100.01, last=101.0101, last_t=-5))["verdict"] == "no second quote"
    assert jq(100.0, nq(99.99, 100.01, last=101.01, last_t=-5))["verdict"] == "agree"


def test_nothing_usable_is_no_second_quote_and_a_bad_first_price_never_agrees():
    assert jq(100.0, nq())["verdict"] == "no second quote"
    assert jq(100.0, nq(99.99, 100.01, band_t=None, last=None))["verdict"] == "no second quote"
    assert jq(100.0, nq(99.99, 100.01, band_t=None, last=100.0, last_t=None))["verdict"] == "no second quote"
    for bad in (None, float("nan"), float("inf"), -1.0, 0.0, True, "100", 1e8):
        out = jq(bad, nq(99.99, 100.01))
        assert out["verdict"] == "disagree" and out["difference"] is None
    assert jq(None, None)["verdict"] == "no second quote"


def test_a_quote_for_another_symbol_or_with_garbage_fields_is_no_second_quote():
    assert jq(100.0, nq(99.99, 100.01, symbol="QQQ"))["verdict"] == "no second quote"
    assert jq(100.0, nq(99.99, 100.01), symbol="QQQ")["verdict"] == "no second quote"
    for bad in (None, "x", 5, [], {}, nq(99.99, 100.01, fetched_t=None)):
        assert jq(100.0, bad)["verdict"] == "no second quote"
    for key, value in (("fetched_at", True), ("fetched_at", float("nan")), ("fetched_at", "now"), ("band_time", True), ("band_time", float("inf")),
                       ("bid", "99.99"), ("bid", True), ("ask", float("nan")), ("last", -1.0), ("last_time", "x")):
        q = nq(99.99, 100.01, last=100.0)
        q[key] = value
        out = jq(100.0, q)
        assert out["verdict"] == ("no second quote" if key == "fetched_at" else "agree"), (key, value)    # the rest falls back to what is usable
    q = nq(99.99, 100.01)
    del q["symbol"]
    assert jq(100.0, q)["verdict"] == "no second quote"


def test_judge_quote_validates_its_arguments_like_compare_and_reads_no_clock(monkeypatch):
    import inspect
    q = nq(99.99, 100.01)
    for bad in (float("nan"), -0.01, True, "0.01", 1.5):
        with pytest.raises(ValueError):
            jq(100.0, q, bad)
    for bad in (datetime(2026, 11, 13, 21, 20, 3), NOW, None):
        with pytest.raises(ValueError):
            sq.judge_quote(100.0, q, symbol="SPY", now=bad)
    for bad in (0, -1, float("nan"), True, "1800"):
        with pytest.raises(ValueError):
            sq.judge_quote(100.0, q, symbol="SPY", now=NOW_DT, max_age_seconds=bad)
    with pytest.raises(TypeError):
        sq.judge_quote(100.0, q, "SPY", NOW_DT)

    def boom(*a, **k):
        raise AssertionError("a clock was read")
    monkeypatch.setattr(sq.time, "time", boom)
    monkeypatch.setattr(sq.time, "monotonic", boom)
    assert jq(100.0, q)["verdict"] == "agree"
    src = inspect.getsource(sq.judge_quote)
    assert "time.time" not in src and "monotonic" not in src and ".now(" not in src


def _ref_judge(first_c, bid_c, ask_c, band_off, last_c, last_off, fetch_before, tol_t, max_age):
    """The rules written again in whole numbers: prices in cents, times in seconds, `now` at 0, the fetch `fetch_before`
    seconds before it. The quote is judged at the later of `now` and the fetch time."""
    if fetch_before > 120 or fetch_before < -30:
        return "no second quote", None
    eff = max(0, -fetch_before)
    if bid_c is not None and ask_c is not None and bid_c > ask_c:
        return "no second quote", None
    band_ok = (bid_c is not None and ask_c is not None and band_off is not None and -5 <= eff - band_off <= 120
               and 400 * (ask_c - bid_c) <= (ask_c + bid_c))
    last_ok = last_c is not None and last_off is not None and -300 <= eff - last_off <= max_age
    if not band_ok and not last_ok:
        return "no second quote", None
    T = Fraction(tol_t, 10000)
    if band_ok:
        mid = Fraction(bid_c + ask_c, 2)
        lo, hi = bid_c - T * mid, ask_c + T * mid
        if last_ok and not lo <= last_c <= hi:
            return "no second quote", None
        return ("agree" if lo <= first_c <= hi else "disagree"), float((first_c - mid) / mid)
    ok = abs(Fraction(first_c) - last_c) <= T * last_c
    return ("agree" if ok else "disagree"), float(Fraction(first_c - last_c, last_c))


def test_five_thousand_generated_cases_of_the_licensed_judgement_agree_with_the_independent_calculation():
    rng = random.Random(20261114)
    seen = {"agree": 0, "disagree": 0, "no second quote": 0, "band": 0, "last": 0, "crossed": 0, "exactly 0.5%": 0, "over 0.5%": 0}
    for i in range(6000):
        max_age = rng.choice([1800, 1800, 60, 3600, rng.randint(1, 5000)])
        tol_t = rng.choice([0, 1, 50, 100, 100, 100, 250, 1000, rng.randint(0, 10000)])
        kind = rng.random()
        k = rng.randint(1, 5000)
        if kind < 0.25:
            bid_c, ask_c = 399 * k, 401 * k                                    # exactly 0.5 % wide
        elif kind < 0.40:
            bid_c, ask_c = 399 * k, 401 * k + rng.choice([1, 2, 5])            # just over
        elif kind < 0.48:
            bid_c, ask_c = 399 * k + rng.choice([1, 2]), 401 * k              # just under
        else:
            bid_c = rng.randint(100, 2_000_000)
            ask_c = bid_c + rng.choice([0, 1, 5, rng.randint(0, 3000), rng.randint(0, max(1, bid_c // 100))])
        if rng.random() < 0.08:
            bid_c, ask_c = ask_c, bid_c                                        # crossed (or equal)
        if rng.random() < 0.10:
            bid_c = None
        elif rng.random() < 0.05:
            ask_c = None
        band_off = rng.choice([-120 - 1, -120, -119, 5, 6, 0, -5, -1, rng.randint(-200, 20)]) if rng.random() < 0.9 else None
        if bid_c is not None and ask_c is not None and bid_c <= ask_c and rng.random() < 0.7:
            last_c = rng.choice([bid_c, ask_c, (bid_c + ask_c) // 2, bid_c - 1, ask_c + 1, rng.randint(bid_c, ask_c)])
            last_c = max(last_c, 1)
        else:
            last_c = rng.randint(100, 2_000_000) if rng.random() < 0.8 else None
        last_off = rng.choice([-max_age - 1, -max_age, -max_age + 1, 300, 301, 299, 0, rng.randint(-max_age - 100, 400)]) if rng.random() < 0.9 else None
        fetch_before = rng.choice([121, 120, 119, 0, -1, -30, -31, rng.randint(-40, 130)])
        if bid_c is not None and ask_c is not None and bid_c <= ask_c and rng.random() < 0.6:
            T = Fraction(tol_t, 10000)
            mid = Fraction(bid_c + ask_c, 2)
            edge = rng.choice([bid_c - T * mid, ask_c + T * mid, mid])
            first_c = max(int(edge) + rng.choice([-1, 0, 1]), 1)
        elif last_c is not None and rng.random() < 0.6:
            first_c = max(int(last_c * (10000 + rng.choice([-1, 1]) * tol_t) / 10000) + rng.choice([-1, 0, 1]), 1)
        else:
            first_c = rng.randint(100, 2_000_000)
        now_abs = NOW
        q = {"symbol": "SPY", "bid": None if bid_c is None else bid_c / 100, "ask": None if ask_c is None else ask_c / 100,
             "band_time": None if band_off is None else now_abs + band_off, "last": None if last_c is None else last_c / 100,
             "last_time": None if last_off is None else now_abs + last_off, "fetched_at": now_abs - fetch_before, "source": "alpaca-iex"}
        want, want_diff = _ref_judge(first_c, bid_c, ask_c, band_off, last_c, last_off, fetch_before, tol_t, max_age)
        got = sq.judge_quote(first_c / 100, q, symbol="SPY", now=NOW_DT, tolerance=tol_t / 10000, max_age_seconds=max_age)
        assert got["verdict"] == want, (i, first_c, bid_c, ask_c, band_off, last_c, last_off, fetch_before, tol_t, max_age, got)
        if want_diff is None:
            assert got["difference"] is None
        else:
            assert math.isclose(got["difference"], want_diff, rel_tol=1e-9, abs_tol=1e-12), (i, got, want_diff)
        seen[want] += 1
        if want != "no second quote":
            seen["band" if got["basis"] == "bid/ask band" else "last"] += 1
        if bid_c is not None and ask_c is not None:
            if bid_c > ask_c:
                seen["crossed"] += 1
            elif 400 * (ask_c - bid_c) == ask_c + bid_c:
                seen["exactly 0.5%"] += 1
            elif 400 * (ask_c - bid_c) > ask_c + bid_c:
                seen["over 0.5%"] += 1
    assert min(seen.values()) > 100, seen


# ---- second_opinion: one answer from several sources

def cboe_bytes(price=752.54, bid=752.5, ask=752.55, ltt_age=60, snapshot_age=30, symbol="SPY"):
    data = {"symbol": symbol, "current_price": price, "bid": bid, "ask": ask, "last_trade_time": at(ltt_age)}
    return json.dumps({"timestamp": datetime.fromtimestamp(NOW - snapshot_age, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                       "data": data, "symbol": symbol}).encode()


def opinion(first=752.54, fake=None, sources=("alpaca", "finnhub"), **kw):
    kw.setdefault("credentials", creds)
    return sq.second_opinion(first, "SPY", now=NOW_DT, sources=sources, fetch=fake, clock=CLOCK, sleep=lambda s: None, **kw)


def test_the_consensus_rule_over_every_combination_of_verdicts():
    names = ("alpaca", "finnhub", "cboe")
    bodies = {
        "agree": {"alpaca": alp(bid=752.5, ask=752.55, last=752.54), "finnhub": fin(c=752.54), "cboe": cboe_bytes()},
        "disagree": {"alpaca": alp(bid=900.0, ask=900.1, last=900.05), "finnhub": fin(c=900.0), "cboe": cboe_bytes(900.05, 900.0, 900.1)},
        "no second quote": {"alpaca": b"{}", "finnhub": fin(c=0, t=0), "cboe": b"garbage"},
    }
    verdicts = ("agree", "disagree", "no second quote")
    count = 0
    for size in (1, 2, 3):
        for chosen in itertools.permutations(names, size):
            for combo in itertools.product(verdicts, repeat=size):
                fake = KFake(**{n: bodies[v][n] for n, v in zip(chosen, combo)})
                out = opinion(fake=fake, sources=chosen)
                want = "agree" if ("agree" in combo and "disagree" not in combo) else "disagree" if "disagree" in combo else "no second quote"
                assert out["verdict"] == want, (chosen, combo, out)
                assert [s["name"] for s in out["sources"]] == list(chosen) and [s["verdict"] for s in out["sources"]] == list(combo)
                assert len(fake.calls) == size                                      # each source asked once
                count += 1
    assert count == 3 * 3 + 6 * 9 + 6 * 27


def test_two_thousand_generated_consensus_cases_agree_with_the_rule():
    rng = random.Random(77)
    seen = {"agree": 0, "disagree": 0, "no second quote": 0, 2: 0, 3: 0}
    for i in range(2400):
        first = round(rng.uniform(10, 900), 2)
        sources = rng.sample(["alpaca", "finnhub", "cboe"], rng.choice([2, 3]))
        wants, routes = [], {}
        for name in sources:
            v = rng.choice(["agree", "disagree", "none"])
            far = round(first * rng.choice([0.9, 1.1, 1.5]), 2)
            px = first if v == "agree" else far
            if name == "alpaca":
                routes[name] = alp(bid=round(px - 0.01, 2), ask=round(px + 0.01, 2), last=px) if v != "none" else rng.choice(
                    [b"{}", b"<html>", alp(band_t=-500, last_t=-5000), alp("QQQ"), alp(bid=0, ask=0, last=0)])
            elif name == "finnhub":
                routes[name] = fin(c=px) if v != "none" else rng.choice([fin(c=0, t=0), b"x", fin(t=-5000), fin(c=-1)])
            else:
                routes[name] = cboe_bytes(px, round(px - 0.01, 2), round(px + 0.01, 2)) if v != "none" else rng.choice(
                    [b"nope", cboe_bytes(ltt_age=9000), cboe_bytes(symbol="QQQ"), cboe_bytes(snapshot_age=-900)])
            wants.append("no second quote" if v == "none" else v)
        out = opinion(first, KFake(**routes), tuple(sources))
        want = "agree" if ("agree" in wants and "disagree" not in wants) else "disagree" if "disagree" in wants else "no second quote"
        assert out["verdict"] == want and [s["verdict"] for s in out["sources"]] == wants, (i, sources, wants, out)
        seen[want] += 1
        seen[len(sources)] += 1
    assert min(seen.values()) > 100, seen


def test_second_opinion_returns_one_entry_per_source_with_the_times():
    fake = KFake(alpaca=alp(bid=752.5, ask=752.55, band_t=-30, last=752.54, last_t=-20), finnhub=fin(c=752.54, t=-10), cboe=cboe_bytes(ltt_age=60))
    out = opinion(fake=fake, sources=("alpaca", "finnhub", "cboe"))
    assert set(out) >= {"verdict", "sources", "symbol"} and out["verdict"] == "agree" and out["symbol"] == "SPY"
    a, f, c = out["sources"]
    assert (a["name"], f["name"], c["name"]) == ("alpaca", "finnhub", "cboe")
    assert a["basis"] == "bid/ask band" and a["band_time"] == pytest.approx(NOW - 30 + 0.092799711, abs=1e-6)
    assert a["last_time"] == pytest.approx(NOW - 20 + 0.092799711, abs=1e-6) and a["fetched_at"] == NOW
    assert a["bid"] == 752.5 and a["ask"] == 752.55 and a["last"] == 752.54 and a["age"] == pytest.approx(30 - 0.092799711, abs=1e-6)
    assert f["basis"] == "last price" and f["band_time"] is None and f["last_time"] == NOW - 10 and f["age"] == pytest.approx(10)
    assert c["basis"] == "bid/ask band" and c["last_time"] == NOW - 60 and c["age"] == pytest.approx(60)
    for entry in out["sources"]:
        assert set(entry) >= {"name", "verdict", "basis", "difference", "band_time", "last_time", "fetched_at", "bid", "ask", "last", "age"}


def test_the_default_sources_are_alpaca_and_finnhub_and_cboe_is_asked_only_when_named():
    fake = KFake(alpaca=alp(), finnhub=fin(), cboe=cboe_bytes())
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, fetch=fake, clock=CLOCK, sleep=lambda s: None, credentials=creds)
    assert [s["name"] for s in out["sources"]] == ["alpaca", "finnhub"] and sorted(fake.hosts()) == ["data.alpaca.markets", "finnhub.io"]
    fake = KFake(alpaca=alp(), finnhub=fin(), cboe=cboe_bytes())
    opinion(fake=fake, sources=("cboe",))
    assert fake.hosts() == ["cdn.cboe.com"]


def test_a_source_that_fails_times_out_or_has_no_key_counts_as_no_second_quote_for_that_source():
    keys = dict(KEYS)
    del keys["FINNHUB_API_KEY"]
    fake = KFake(alpaca=alp(), finnhub=fin(), cboe=OSError("down"))
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), fetch=fake, clock=CLOCK, sleep=lambda s: None,
                            credentials=keys.__getitem__)
    assert [s["verdict"] for s in out["sources"]] == ["agree", "no second quote", "no second quote"] and out["verdict"] == "agree"
    assert "finnhub.io" not in fake.hosts()
    out = opinion(fake=KFake(alpaca=TimeoutError("slow"), finnhub=OSError("x")))
    assert out["verdict"] == "no second quote" and all(s["verdict"] == "no second quote" for s in out["sources"])
    out = opinion(fake=KFake(alpaca=alp(bid=900.0, ask=900.1, last=900.0), finnhub=OSError("x")))
    assert out["verdict"] == "disagree"


def test_a_source_that_disagrees_outvotes_any_number_that_agree():
    out = opinion(fake=KFake(alpaca=alp(), finnhub=fin(c=900.0), cboe=cboe_bytes()), sources=("alpaca", "finnhub", "cboe"))
    assert [s["verdict"] for s in out["sources"]] == ["agree", "disagree", "agree"] and out["verdict"] == "disagree"


def test_second_opinion_refuses_a_bad_symbol_source_or_argument_before_any_request():
    for kw in ({"sources": ("alpaca", "nope")}, {"sources": ("SIP",)}, {"sources": "alpaca"}, {"sources": (5,)}, {"tolerance": -1}, {"max_age_seconds": 0},
               {"now": NOW}, {"now": datetime(2026, 11, 13, 21, 20, 3)}, {"deadline_seconds": 0}, {"deadline_seconds": float("nan")}):
        fake = KFake(alpaca=alp(), finnhub=fin())
        args = {"first_price": 752.54, "symbol": "SPY", "now": NOW_DT, "fetch": fake, "clock": CLOCK, "credentials": creds, "sleep": lambda s: None}
        args.update(kw)
        with pytest.raises(ValueError):
            sq.second_opinion(args.pop("first_price"), args.pop("symbol"), **args)
        assert fake.calls == [], kw
    for bad in ("spy", "SPY,QQQ", "", None):
        fake = KFake(alpaca=alp())
        with pytest.raises(ValueError):
            sq.second_opinion(752.54, bad, now=NOW_DT, fetch=fake, clock=CLOCK, credentials=creds)
        assert fake.calls == []


def test_second_opinion_with_no_sources_and_with_repeated_sources():
    out = opinion(fake=KFake(), sources=())
    assert out["verdict"] == "no second quote" and out["sources"] == []
    fake = KFake(alpaca=alp(), finnhub=fin())
    out = opinion(fake=fake, sources=("alpaca", "alpaca", "finnhub"))
    assert [s["name"] for s in out["sources"]] == ["alpaca", "finnhub"] and len(fake.calls) == 2


def test_second_opinion_judges_each_quote_at_the_later_of_now_and_its_fetch():
    # `now` was taken before the requests: a quote that arrives 20 s later and was made 3 s after `now` is still current
    ticking = FakeClock(NOW)
    ticks = []

    def fetch(url, timeout, headers=None):
        ticking.t += 10.0
        ticks.append(ticking.t)
        return alp(bid=752.5, ask=752.55, band_t=ticking.t - NOW - 2, last=752.54, last_t=ticking.t - NOW - 2) if "alpaca" in url else fin(c=752.54, t=ticking.t - NOW - 1)
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, fetch=fetch, clock=ticking, sleep=lambda s: None, credentials=creds)
    assert out["verdict"] == "agree" and [s["verdict"] for s in out["sources"]] == ["agree", "agree"]


def test_one_overall_deadline_of_25_seconds_on_the_injected_clock():
    fc = FakeClock(NOW)
    timeouts = []

    def slow(per_call):
        def fetch(url, timeout, headers=None):
            timeouts.append(timeout)
            fc.t += per_call
            if "alpaca" in url:
                return alp()
            return fin() if "finnhub" in url else cboe_bytes()
        return fetch
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), fetch=slow(9.0), clock=fc, sleep=lambda s: fc.__setattr__("t", fc.t + s),
                            credentials=creds)
    # 9 s, 9 s, then the third starts at 18 s with 7 s left: its answer arrives at 27 s, past the deadline, and is refused
    assert [s["verdict"] for s in out["sources"]] == ["agree", "agree", "no second quote"] and out["verdict"] == "agree"
    assert timeouts == [5.0, 5.0, 5.0] and fc.t == NOW + 27.0
    fc.t, timeouts[:] = NOW, []
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), fetch=slow(30.0), clock=fc, sleep=lambda s: fc.__setattr__("t", fc.t + s),
                            credentials=creds)
    assert len(timeouts) == 1 and out["verdict"] == "no second quote"                   # the first took 30 s: nothing else is started
    fc.t, timeouts[:] = NOW, []

    def stall(url, timeout, headers=None):
        timeouts.append(timeout)
        fc.t += timeout
        raise TimeoutError("stalled")
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), fetch=stall, clock=fc,
                            sleep=lambda s: fc.__setattr__("t", fc.t + s), credentials=creds)
    assert fc.t - NOW <= 25.0 + 1e-9 and all(t <= 5.0 for t in timeouts) and out["verdict"] == "no second quote"
    assert min(timeouts) > 0


def test_second_opinion_never_raises_on_hostile_answers():
    finnhub_names = sorted(FINNHUB_HOSTILE)
    for k, name in enumerate(sorted(ALPACA_HOSTILE)):
        out = opinion(fake=KFake(alpaca=ALPACA_HOSTILE[name], finnhub=FINNHUB_HOSTILE[finnhub_names[k % len(finnhub_names)]]))
        assert out["verdict"] == "no second quote" and all(s["verdict"] == "no second quote" for s in out["sources"])
    for first in (None, float("nan"), -1, 0, True, "x", [], 1e9):
        out = opinion(first, KFake(alpaca=alp(), finnhub=fin()))
        assert out["verdict"] == "disagree" and all(s["verdict"] == "disagree" and s["difference"] is None for s in out["sources"])


def test_the_defaults_of_the_new_readers_are_the_keyed_fetch_and_the_wall_clock():
    import inspect
    for reader in (sq.alpaca_quote, sq.finnhub_quote):
        params = inspect.signature(reader).parameters
        assert params["fetch"].default is sq.keyed_fetch and params["credentials"].default is None
        assert all(params[n].kind is inspect.Parameter.KEYWORD_ONLY for n in ("fetch", "clock", "credentials"))
    assert inspect.signature(sq.second_opinion).parameters["sources"].default == ("alpaca", "finnhub")


def test_second_opinion_hands_its_own_fetches_what_is_left_of_the_overall_deadline(monkeypatch):
    fc = FakeClock(NOW)
    seen = []

    def record(kind, body):
        def fetch(url, timeout, *args, deadline=None, **kw):
            seen.append((kind, timeout, deadline, args))
            fc.t += 10.0
            return body
        return fetch
    monkeypatch.setattr(sq, "keyed_fetch", record("keyed", alp()))
    monkeypatch.setattr(sq, "urllib_fetch", record("cboe", cboe_bytes()))
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), clock=fc, sleep=lambda s: None, credentials=creds)
    assert [k for k, *_ in seen] == ["keyed", "keyed", "cboe"]
    assert [round(d, 6) for _, _, d, _ in seen] == [10.0, 10.0, 5.0]               # 25, 15 and 5 s left: a try is at most 10
    assert [a for *_, a in seen] == [(A_HEAD,), (F_HEAD,), ()] and [t for _, t, _, _ in seen] == [5.0, 5.0, 5.0]
    assert out["sources"][0]["name"] == "alpaca"


def test_no_key_appears_anywhere_but_the_two_header_values_sent_to_the_right_host(capsys, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    outputs = []

    def hostile_echo(url, timeout, headers=None):
        raise RuntimeError("failed with headers %r for %s" % (headers, url))

    def echo_body(url, timeout, headers=None):
        return json.dumps({"SPY": {"latestQuote": {"ap": AID, "bp": ASEC}, "latestTrade": {"p": FTOK, "t": ASEC}}, "c": FTOK, "t": AID}).encode()
    scenarios = [KFake(alpaca=alp(), finnhub=fin(), cboe=cboe_bytes()), KFake(alpaca=hostile_echo, finnhub=hostile_echo, cboe=hostile_echo),
                 KFake(alpaca=echo_body, finnhub=echo_body, cboe=echo_body), KFake(alpaca=urllib.error.HTTPError("https://data.alpaca.markets/x", 403, "Forbidden", None, None),
                                                                                  finnhub=urllib.error.HTTPError("https://finnhub.io/x", 429, "Slow down", None, None), cboe=None)]
    recorded = []
    for fake in scenarios:
        out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), fetch=fake, clock=CLOCK, sleep=lambda s: None, credentials=creds)
        outputs += [repr(out), json.dumps(out, default=str)]
        outputs += [repr(sq.alpaca_quote("SPY", fetch=fake, clock=CLOCK, credentials=creds, sleep=lambda s: None)), repr(sq.finnhub_quote("SPY", fetch=fake, clock=CLOCK, credentials=creds, sleep=lambda s: None))]
        recorded += fake.calls
        buf = Out()
        sq.probe(["SPY"], first_quotes=first_source({"SPY": (752.54, FIRST_T)}), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=buf, credentials=creds)
        outputs += buf.lines
    captured = capsys.readouterr()
    outputs += [captured.out, captured.err, caplog.text]
    for text in outputs:
        for key in FAKE_KEYS:
            assert key not in text, text[:200]
    right = {"data.alpaca.markets": {AID, ASEC}, "finnhub.io": {FTOK}}
    for url, timeout, headers in recorded:
        host = urllib.parse.urlsplit(url).hostname
        for key in FAKE_KEYS:
            assert key not in url
        sent = set((headers or {}).values())
        assert sent <= right.get(host, set()), (host, sent)
        if host == "cdn.cboe.com":
            assert headers is None


def test_exception_texts_from_the_keyed_fetch_hold_no_key():
    texts = []
    for url, head, route in ((AL, A_HEAD, (403, None, io.BytesIO(AID.encode()))), (FH, F_HEAD, (429, None, io.BytesIO(FTOK.encode()))),
                             (AL, A_HEAD, (307, "https://evil.example/", io.BytesIO(b""))), (AL, F_HEAD, None)):
        handler = Recorder({url: route} if route else {})
        try:
            sq.keyed_fetch(url, 5.0, head, opener=sq._build_opener(handler))
        except Exception as e:
            texts += [str(e), repr(e)]
    assert texts and not any(k in t for t in texts for k in FAKE_KEYS)


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


def no_keys(name):
    raise KeyError(name)


def block(lines, sym):
    """The lines of one symbol's block: its first line (which starts with the symbol) and the indented lines under it."""
    out, inside = [], False
    for line in lines:
        if line.startswith(sym + "  "):
            inside = True
            out = [line]
        elif inside and line.startswith("  "):
            out.append(line)
        elif inside:
            break
    return out


def source_line(lines, sym, name):
    found = [l for l in block(lines, sym) if l.split() and l.split()[0] == name]
    assert len(found) == 1, (sym, name, lines)
    return found[0]


def cboe_probe(symbols, first, fetch, out, **kw):
    kw.setdefault("clock", PCLOCK)
    return sq.probe(symbols, first_quotes=first, fetch=fetch, sleep=lambda s: None, out=out, credentials=no_keys, sources=("cboe",), **kw)


def test_the_probe_prints_both_prices_both_times_the_difference_and_the_verdict():
    calls, out = [], Out()
    fake = Fake(REAL_SPY)
    code = cboe_probe(["SPY"], first_source({"SPY": (752.6, FIRST_T)}, calls), fake, out)
    text = "\n".join(out.lines)
    assert code == 0 and calls == [["SPY"]] and len(fake.calls) == 1
    for part in ("SPY", "752.6", "752.54", "752.5", "752.55", "2026-11-13T20:59:00Z", "2026-11-13T16:00:00", "+0.01%", "agree"):
        assert part in text, (part, text)
    line = source_line(out.lines, "SPY", "cboe")
    assert "age 1740 s" in line and "+0.01%" in line and "agree [bid/ask band]" in line


def test_the_probe_reports_a_disagreement_and_a_missing_second_quote():
    out = Out()
    cboe_probe(["SPY", "XLE"], first_source({"SPY": (800.0, FIRST_T), "XLE": (64.8, FIRST_T)}), Fake(REAL_SPY, b"garbage"), out)
    spy = source_line(out.lines, "SPY", "cboe")
    assert "disagree" in spy and "+6.3" in spy
    assert "no second quote" in source_line(out.lines, "XLE", "cboe")


def test_the_probe_survives_the_first_source_failing_or_missing_a_symbol():
    out = Out()
    cboe_probe(["SPY"], first_source(RuntimeError("down")), Fake(REAL_SPY), out)
    assert "first source failed" in block(out.lines, "SPY")[0]
    line = source_line(out.lines, "SPY", "cboe")
    assert "752.54" in line and "agree" not in line.replace("disagree", "")
    out = Out()
    cboe_probe(["SPY"], first_source({}), Fake(REAL_SPY), out)
    assert "no first quote" in block(out.lines, "SPY")[0]
    out = Out()
    cboe_probe(["SPY"], first_source({"SPY": (float("nan"), FIRST_T)}), Fake(REAL_SPY), out)
    assert "no first quote" in block(out.lines, "SPY")[0]


def test_the_probe_takes_at_most_ten_symbols_and_refuses_before_any_request():
    syms = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    calls, fake, out = [], Fake(REAL_SPY), Out()
    cboe_probe(syms, first_source({}, calls), fake, out)
    assert calls == [syms] and len(fake.calls) == 10                      # ten is fine, one request each
    calls, fake, out = [], Fake(REAL_SPY), Out()
    assert cboe_probe(syms + ["K"], first_source({}, calls), fake, out) == 2
    assert calls == [] and fake.calls == [] and any("at most 10" in l for l in out.lines)


def test_the_probe_refuses_a_bad_symbol_before_any_request():
    for bad in ("spy", "SPY?x=1", "TOOLONGX", "", "SPY/../x"):
        calls, fake, out = [], Fake(REAL_SPY), Out()
        assert cboe_probe(["SPY", bad], first_source({}, calls), fake, out) == 2
        assert calls == [] and fake.calls == []
    calls, fake, out = [], Fake(REAL_SPY), Out()
    assert cboe_probe([], first_source({}, calls), fake, out) == 2
    assert calls == [] and fake.calls == []


def test_a_repeated_symbol_is_fetched_once():
    calls, fake, out = [], Fake(REAL_SPY), Out()
    cboe_probe(["SPY", "SPY"], first_source({}, calls), fake, out)
    assert calls == [["SPY"]] and len(fake.calls) == 1


def test_the_default_first_source_is_the_order_steps_own_function_by_import(monkeypatch):
    from valedia_trader.execution_and_risk import order_step
    seen = []
    monkeypatch.setattr(order_step, "minute_quotes", lambda symbols: seen.append(list(symbols)) or {"SPY": (752.6, FIRST_T)})
    out = Out()
    sq.probe(["SPY"], fetch=Fake(REAL_SPY), clock=PCLOCK, sleep=lambda s: None, out=out, credentials=no_keys, sources=("cboe",))
    assert seen == [["SPY"]] and "752.6" in "\n".join(out.lines)


def test_main_runs_the_probe_subcommand_and_refuses_bad_arguments():
    out = Out()
    kw = dict(credentials=no_keys, sources=("cboe",), clock=PCLOCK, sleep=lambda s: None)
    assert sq.main(["probe", "SPY"], first_quotes=first_source({"SPY": (752.6, FIRST_T)}), fetch=Fake(REAL_SPY), out=out, **kw) == 0
    fake = Fake(REAL_SPY)
    assert sq.main(["probe", "spy"], first_quotes=first_source({}), fetch=fake, out=Out(), **kw) == 2
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
        cboe_probe(["SPY"], first_source({"SPY": (first, FIRST_T)}), Fake(REAL_SPY), out)
        line = source_line(out.lines, "SPY", "cboe")
        assert "| 0.00% |" in line and "-0.00%" not in line and "+0.00%" not in line, line
    out = Out()
    cboe_probe(["SPY"], first_source({"SPY": (752.4, FIRST_T)}), Fake(REAL_SPY), out)
    assert "| -0.02% |" in source_line(out.lines, "SPY", "cboe")


def test_the_probe_gives_compare_the_symbol_and_the_callers_clock(monkeypatch):
    seen = []
    real = sq.compare

    def spy(first, second, **kw):
        seen.append(kw)
        return real(first, second, **kw)
    monkeypatch.setattr(sq, "compare", spy)
    cboe_probe(["SPY", "XLE"], first_source({"SPY": (752.6, FIRST_T), "XLE": (64.8, FIRST_T)}), Fake(REAL_SPY, REAL_XLE), Out())
    assert [k["symbol"] for k in seen] == ["SPY", "XLE"]
    assert all(k["now"] == datetime.fromtimestamp(PNOW, timezone.utc) and k["now"].tzinfo is not None for k in seen)


def test_the_probe_judges_age_by_its_clock():
    out = Out()
    cboe_probe(["SPY"], first_source({"SPY": (752.6, FIRST_T)}), Fake(REAL_SPY), out, clock=lambda: PNOW + 3600)
    line = source_line(out.lines, "SPY", "cboe")
    assert "no second quote" in line and "agree" not in line


# ---- T28b: the probe over every source

def all_probe(symbols=("SPY",), fake=None, first=752.54, out=None, **kw):
    out = out if out is not None else Out()
    kw.setdefault("credentials", creds)
    kw.setdefault("clock", CLOCK)
    fake = fake or KFake(alpaca=alp(), finnhub=fin(c=752.60), cboe=cboe_bytes())
    code = sq.probe(list(symbols), first_quotes=first_source({s: (first, FIRST_T) for s in symbols}), fetch=fake, sleep=lambda s: None, out=out, **kw)
    return code, out, fake


def test_the_probe_prints_the_first_price_then_one_line_per_source_then_the_consensus():
    code, out, fake = all_probe()
    assert code == 0
    lines = block(out.lines, "SPY")
    assert [l.split()[0] for l in lines[1:]] == ["alpaca", "finnhub", "cboe", "consensus"]
    assert "752.54" in lines[0] and "2026-11-13T20:59:00Z" in lines[0]
    alpaca, finnhub, cboe, consensus = lines[1:]
    assert "bid 752.5" in alpaca and "ask 752.55" in alpaca and "last 752.54" in alpaca and "age 30 s" in alpaca and "agree [bid/ask band]" in alpaca
    assert zt(-30)[:19] in alpaca and zt(-20)[:19] in alpaca
    assert "last 752.6" in finnhub and "age 20 s" in finnhub and "-0.01%" in finnhub and "agree [last price]" in finnhub and zt(-20)[:19] in finnhub
    assert "752.54" in cboe and "age 60 s" in cboe and "agree" in cboe and "2026-11-13T" in cboe
    assert consensus.split() == ["consensus", "agree"]
    assert sorted(fake.hosts()) == ["cdn.cboe.com", "data.alpaca.markets", "finnhub.io"]      # each source asked once


def test_the_probe_consensus_line_follows_the_rule():
    code, out, _ = all_probe(fake=KFake(alpaca=alp(), finnhub=fin(c=900.0), cboe=cboe_bytes()))
    assert block(out.lines, "SPY")[-1].split() == ["consensus", "disagree"] and "disagree" in source_line(out.lines, "SPY", "finnhub")
    code, out, _ = all_probe(fake=KFake(alpaca=b"{}", finnhub=OSError("x"), cboe=b"nope"))
    assert block(out.lines, "SPY")[-1].split() == ["consensus", "no", "second", "quote"]
    assert all("none" in source_line(out.lines, "SPY", n) for n in ("alpaca", "finnhub", "cboe"))


def test_the_probe_asks_each_source_once_per_symbol_and_at_most_ten_symbols():
    syms = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    code, out, fake = all_probe(syms)
    assert code == 0 and len(fake.calls) == 30 and sorted(fake.hosts()) == sorted(["cdn.cboe.com", "data.alpaca.markets", "finnhub.io"] * 10)
    code, out, fake = all_probe(syms + ["K"])
    assert code == 2 and fake.calls == []
    code, out, fake = all_probe(["SPY", "spy"])
    assert code == 2 and fake.calls == []


def test_the_probe_loads_the_secrets_file_once_through_load_env_and_reads_the_keys_by_name(monkeypatch):
    from valedia_trader.core import secrets
    loaded = []
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: loaded.append((a, k)) or [])
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    out = Out()
    fake = KFake(alpaca=alp(), finnhub=fin(), cboe=cboe_bytes())
    code = sq.probe(["SPY", "XLE"], first_quotes=first_source({"SPY": (752.54, FIRST_T), "XLE": (752.54, FIRST_T)}), fetch=fake, clock=CLOCK,
                    sleep=lambda s: None, out=out)
    assert code == 0 and loaded == [((), {})]
    assert [c[2] for c in fake.calls if c[2]][:2] == [A_HEAD, F_HEAD]


def test_the_probe_does_not_touch_the_secrets_file_when_credentials_are_given_or_when_refusing(monkeypatch):
    from valedia_trader.core import secrets
    loaded = []
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: loaded.append(1) or [])
    all_probe()
    all_probe(["spy"])
    sq.probe([], out=Out())
    assert loaded == []


def test_a_secrets_file_that_cannot_be_loaded_is_a_note_with_the_error_class_only_and_the_sources_have_no_key(monkeypatch):
    from valedia_trader.core import secrets

    def broken(*a, **k):
        raise secrets.SecretError("/home/someone/.hermes/secrets/trading.env is readable by group or others " + AID)
    monkeypatch.setattr(secrets, "load_env", broken)
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    out = Out()
    fake = KFake(alpaca=alp(), finnhub=fin(), cboe=cboe_bytes())
    code = sq.probe(["SPY"], first_quotes=first_source({"SPY": (752.54, FIRST_T)}), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=out)
    text = "\n".join(out.lines)
    assert code == 0 and "SecretError" in text and AID not in text and "/home/someone" not in text
    assert fake.hosts() == ["cdn.cboe.com"]                                   # no keys: only the keyless source was asked


def test_every_probe_line_passes_the_output_guard_and_control_characters_are_dropped(monkeypatch):
    from valedia_trader.core import secrets
    seen = []
    real = secrets.redact
    monkeypatch.setattr(secrets, "redact", lambda text: seen.append(text) or real(text))
    code, out, _ = all_probe()
    assert out.lines and sorted(seen) == sorted(out.lines)
    for line in out.lines:
        assert all(ch.isprintable() for ch in line)


def test_a_remembered_key_is_masked_even_if_it_were_to_reach_a_line(monkeypatch):
    from valedia_trader.core import secrets
    secrets.remember(ASEC)
    out = Out()
    shown = sq._shown("price " + ASEC + " seen")
    assert ASEC not in shown and "<secret>" in shown
    code, out, _ = all_probe()
    assert all(ASEC not in l for l in out.lines)


# ======================================================================================================================
# T28b rev 2: four closures
# ======================================================================================================================

REASON_PATTERN = re.compile(r"no key|timed out|request failed|request failed \(HTTP [1-5][0-9]{2}\)|answer could not be used|quote not recent|"
                            r"band too wide and no recent last price|suspect data")


def test_the_fixed_set_of_reasons():
    assert set(sq.NO_QUOTE_REASONS) == {"no key", "timed out", "request failed", "answer could not be used", "quote not recent",
                                        "band too wide and no recent last price", "suspect data"}
    assert sq.HTTP_REASON % 403 == "request failed (HTTP 403)"


# ---- 1. the Cboe quote inside second_opinion is judged at a time never before its own fetch

def test_the_cboe_quote_inside_second_opinion_is_never_refused_for_the_rounding_of_its_fetch_time(monkeypatch):
    rng = random.Random(4040)
    judged = []
    real = sq.compare

    def spy(first, second, **kw):
        judged.append((second["fetched_at"], kw["now"]))
        return real(first, second, **kw)
    monkeypatch.setattr(sq, "compare", spy)
    rounds_down = 0
    for i in range(10000):
        d = rng.choice([rng.uniform(0.0, 3.0), round(rng.uniform(0.0, 3.0), 6) + rng.choice([-4e-7, -2e-7, -1e-8, 1e-8, 2e-7, 4e-7]),
                        rng.uniform(0.0, 1e-5), float(rng.randint(1, 3)), rng.randint(1, 3000000) / 1e6 + 4.9e-7])
        fc = FakeClock(NOW)

        def fetch(url, timeout, headers=None, d=d, fc=fc):
            fc.t += d
            return cboe_bytes()
        out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("cboe",), fetch=fetch, clock=fc, sleep=lambda s: None)
        fetched = fc.t
        if datetime.fromtimestamp(fetched, timezone.utc).timestamp() < fetched:
            rounds_down += 1
        entry = out["sources"][0]
        assert entry["verdict"] == "agree" and entry["basis"] == "bid/ask band", (i, repr(d), entry)
        assert judged[-1][0] == fetched and judged[-1][1].timestamp() >= fetched and judged[-1][1].tzinfo is not None
    assert rounds_down > 1000                                         # the generator did reach fetch times that round down


def test_the_judging_time_is_the_fetch_when_it_is_up_to_30_seconds_after_now_and_now_beyond_that(monkeypatch):
    judged = []
    real = sq.compare

    def spy(first, second, **kw):
        judged.append(kw["now"].timestamp())
        return real(first, second, **kw)
    monkeypatch.setattr(sq, "compare", spy)
    for d, verdict in ((5.0, "agree"), (9.5, "agree"), (10.0, "agree")):
        fc = FakeClock(NOW)

        def fetch(url, timeout, headers=None, d=d, fc=fc):
            fc.t += d
            return cboe_bytes(snapshot_age=-d + 30)
        out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("cboe",), fetch=fetch, clock=fc, sleep=lambda s: None)
        assert out["sources"][0]["verdict"] == verdict and judged[-1] >= NOW + d
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT - timedelta(seconds=31.0), sources=("cboe",), fetch=lambda u, t: cboe_bytes(), clock=CLOCK,
                            sleep=lambda s: None)
    assert out["sources"][0]["verdict"] == "no second quote" and judged[-1] == NOW - 31.0           # 31 s after `now`: not the fetch


# ---- 2. no text chosen by a server reaches an error of keyed_fetch

class Echo(Recorder):
    """A server that echoes the keys in its reason phrase, in a header and in the body."""
    def https_open(self, req):
        resp = super().https_open(req)
        resp.msg = "Unauthorized " + AID + " " + ASEC + " " + FTOK
        resp.headers["X-Echo"] = AID + ASEC + FTOK
        resp.headers["WWW-Authenticate"] = "Key realm=" + ASEC
        return resp


def _error_texts(e):
    import traceback
    return [str(e), repr(e), getattr(e, "reason", "") or "", "".join(traceback.format_exception(type(e), e, e.__traceback__)), str(e.args)]


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308, 400, 401, 403, 404, 429, 500, 502, 503])
def test_an_http_error_of_the_keyed_fetch_carries_the_status_code_and_the_service_name_only(code):
    for url, head, service in ((AL, A_HEAD, "alpaca"), (FH, F_HEAD, "finnhub")):
        body = Counting((AID + ASEC + FTOK).encode() * 4)
        handler = Echo({url: (code, "https://evil.example/" + AID, body)})
        with pytest.raises(urllib.error.HTTPError) as caught:
            sq.keyed_fetch(url, 5.0, head, opener=sq._build_opener(handler))
        e = caught.value
        assert e.code == code and e.reason == service and str(e) == "HTTP Error %d: %s" % (code, service)
        assert e.headers is None and (e.fp is None or e.fp.read() == b"") and e.__cause__ is None and e.__context__ is None and body.total == 0
        for text in _error_texts(e):
            assert not any(k in text for k in FAKE_KEYS), text[:120]
            assert "Unauthorized" not in text and "evil.example" not in text and "Key realm" not in text


def test_a_status_other_than_200_that_is_not_an_error_is_the_same_sanitised_error():
    for code in (201, 204, 206):
        handler = Echo({AL: (code, None, io.BytesIO(alp()))})
        with pytest.raises(urllib.error.HTTPError) as caught:
            sq.keyed_fetch(AL, 5.0, A_HEAD, opener=sq._build_opener(handler))
        assert caught.value.code == code and caught.value.reason == "alpaca" and caught.value.headers is None


def test_every_other_error_of_the_keyed_fetch_has_a_fixed_text_and_no_chained_error():
    import http.client

    class Raises(Recorder):
        def __init__(self, exc):
            super().__init__({})
            self.exc = exc

        def https_open(self, req):
            raise self.exc
    echo = AID + ASEC + FTOK
    cases = [(OSError("echo " + echo), OSError, "alpaca request failed"), (urllib.error.URLError("echo " + echo), OSError, "alpaca request failed"),
             (ConnectionResetError(echo), OSError, "alpaca request failed"), (http.client.BadStatusLine(echo), OSError, "alpaca request failed"),
             (RuntimeError(echo), OSError, "alpaca request failed"), (ValueError(echo), ValueError, "alpaca answer refused"),
             (TimeoutError(echo), TimeoutError, "alpaca request timed out"), (urllib.error.URLError(TimeoutError(echo)), TimeoutError, "alpaca request timed out")]
    for exc, kind, text in cases:
        with pytest.raises(kind) as caught:
            sq.keyed_fetch(AL, 5.0, A_HEAD, opener=sq._build_opener(Raises(exc)))
        e = caught.value
        assert type(e) is kind and str(e) == text and e.__cause__ is None and e.__context__ is None, (exc, e)
        assert not any(k in t for t in _error_texts(e) for k in FAKE_KEYS)
    handler = Recorder({FH: (200, None, io.BytesIO(b"x" * (sq.MAX_BODY_BYTES + 5)))})
    with pytest.raises(ValueError) as caught:
        sq.keyed_fetch(FH, 5.0, F_HEAD, opener=sq._build_opener(handler))
    assert str(caught.value) == "finnhub answer refused" and caught.value.__context__ is None


# ---- 3. a redirect to a keyed source ends the call: one request

@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_redirect_answered_to_a_keyed_source_is_one_request_and_no_retry(code):
    for reader, url, head, host in ((sq.alpaca_quote, AL, A_HEAD, "alpaca"), (sq.finnhub_quote, FH, F_HEAD, "finnhub")):
        handler = Recorder({url: (code, "https://data.alpaca.markets/x", io.BytesIO(b""))})
        sleeps = []
        out = reader("SPY", fetch=functools.partial(sq.keyed_fetch, opener=sq._build_opener(handler)), clock=CLOCK, credentials=creds, sleep=sleeps.append)
        assert out is None and [r["url"] for r in handler.requests] == [url] and sleeps == []


def test_a_redirect_error_from_any_fetch_is_final_and_an_error_status_from_the_server_still_retries():
    for code in (301, 307, 308, 400, 429):
        err = urllib.error.HTTPError("https://x/", code, "no", None, None)
        fake, sleeps = KFake(alpaca=err), []
        assert aq(fake=fake, sleep=sleeps.append) is None and len(fake.calls) == 1 and sleeps == []
    for code in (500, 503):
        fake, sleeps = KFake(alpaca=urllib.error.HTTPError("https://x/", code, "no", None, None)), []
        assert aq(fake=fake, sleep=sleeps.append) is None and len(fake.calls) == 2 and sleeps == [2.0]
    cb = KFake(cboe=urllib.error.HTTPError("https://x/", 307, "no", None, None))
    assert sq.cboe_quote("SPY", fetch=cb, clock=CLOCK, sleep=lambda s: None) is None and len(cb.calls) == 1


# ---- 4. each source says why it has nothing

def entry_of(name, fake, first=752.54, **kw):
    kw.setdefault("credentials", creds)
    out = sq.second_opinion(first, "SPY", now=NOW_DT, sources=(name,), fetch=fake, clock=CLOCK, sleep=lambda s: None, **kw)
    return out["sources"][0]


def http_error(code):
    return urllib.error.HTTPError("https://x/", code, "reason", None, None)


def test_a_missing_key_is_no_key_for_the_keyed_sources():
    for name in ("alpaca", "finnhub"):
        for creds_fn in (no_keys, lambda n: "", lambda n: None, lambda n: "has space", lambda n: 5):
            fake = KFake(alpaca=alp(), finnhub=fin())
            e = entry_of(name, fake, credentials=creds_fn)
            assert e["verdict"] == "no second quote" and e["reason"] == "no key" and e["basis"] == "no key" and fake.calls == []
    keys = {k: v for k, v in KEYS.items() if k != "ALPACA_SECRET_KEY"}
    e = entry_of("alpaca", KFake(alpaca=alp()), credentials=keys.__getitem__)
    assert e["reason"] == "no key"
    assert entry_of("finnhub", KFake(finnhub=fin()), credentials=keys.__getitem__)["reason"] is None                # its own key is there


def test_a_failed_request_gives_the_status_code_or_timed_out():
    for name, key in (("alpaca", "alpaca"), ("finnhub", "finnhub"), ("cboe", "cboe")):
        for code in (301, 307, 400, 401, 403, 404, 429, 500, 503):
            e = entry_of(name, KFake(**{key: http_error(code)}))
            assert e["verdict"] == "no second quote" and e["reason"] == e["basis"] == "request failed (HTTP %d)" % code, (name, code, e)
        for exc in (TimeoutError("slow"), socket.timeout("slow"), urllib.error.URLError(TimeoutError("x"))):
            e = entry_of(name, KFake(**{key: exc}))
            assert e["reason"] == "timed out" or isinstance(exc, urllib.error.URLError), (name, exc, e)
        for exc in (OSError("down"), ConnectionResetError("x"), RuntimeError("y"), ValueError("z"), urllib.error.URLError("w")):
            e = entry_of(name, KFake(**{key: exc}))
            assert e["reason"] == "request failed", (name, exc, e)
        e = entry_of(name, KFake(**{key: http_error(999)}))
        assert e["reason"] == "request failed"                                  # not a status code: no code is printed


def test_a_try_that_overruns_its_deadline_is_timed_out():
    fc = FakeClock(NOW)

    def slow(url, timeout, headers=None):
        fc.t += 2100.0
        return alp()
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca",), fetch=slow, clock=fc, sleep=lambda s: None, credentials=creds)
    assert out["sources"][0]["reason"] == "timed out"
    fc = FakeClock(NOW)
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), fetch=lambda *a, **k: (setattr(fc, "t", fc.t + 30), alp())[1], clock=fc,
                            sleep=lambda s: None, credentials=creds)
    assert [s["reason"] for s in out["sources"]] == ["timed out"] * 3                     # the first took 30 s: the others never started


def test_an_answer_that_could_not_be_used():
    for name, bodies in (("alpaca", [b"{}", b"<html>", b"", alp("QQQ"), alp(bid=0, ask=0, last=0), ALPACA_HOSTILE["duplicate-symbol-key"], None]),
                         ("finnhub", [fin(c=0, t=0), b"x", b"", FINNHUB_HOSTILE["duplicate-c"], None]),
                         ("cboe", [b"nope", b"", cboe_bytes(symbol="QQQ"), None])):
        for body in bodies:
            e = entry_of(name, KFake(**{name: body}))
            assert e["verdict"] == "no second quote" and e["reason"] == "answer could not be used", (name, body, e)


def test_a_quote_that_is_not_recent():
    for name, body in (("alpaca", alp(band_t=-500, last_t=-5000)), ("alpaca", alp(bid=None, ask=None, last_t=-5000)), ("finnhub", fin(t=-5000)),
                       ("cboe", cboe_bytes(ltt_age=9000)), ("cboe", cboe_bytes(snapshot_age=-900)), ("alpaca", alp(band_t=-500, last=None))):
        e = entry_of(name, KFake(**{name: body}))
        assert e["verdict"] == "no second quote" and e["reason"] == "quote not recent", (name, e)
    e = sq.second_opinion(752.54, "SPY", now=NOW_DT + timedelta(seconds=500), sources=("finnhub",), fetch=KFake(finnhub=fin()), clock=CLOCK, sleep=lambda s: None,
                          credentials=creds)["sources"][0]
    assert e["reason"] == "quote not recent"                                          # fetched 500 s before `now`


def test_a_band_too_wide_with_no_recent_last_price():
    wide = dict(bid=62.81, ask=66.86, band_t=-5)
    for kw in ({"last": None}, {"last": 64.93, "last_t": -5000}, {"last": 64.93, "last_t": 3600}):
        e = entry_of("alpaca", KFake(alpaca=alp(**wide, **kw)))
        assert e["verdict"] == "no second quote" and e["reason"] == "band too wide and no recent last price", (kw, e)
    e = entry_of("alpaca", KFake(alpaca=alp(**wide, last=64.93, last_t=-5)), first=64.93)
    assert e["verdict"] == "agree" and e["reason"] is None and e["basis"] == "last price"      # a recent last price decides


def test_suspect_data_a_crossed_band_or_a_last_trade_that_contradicts_its_band():
    for body in (alp(bid=102.0, ask=100.0, last=500.0), alp(bid=752.5, ask=752.55, last=900.0), alp(bid=100.01, ask=100.0, last=100.0)):
        e = entry_of("alpaca", KFake(alpaca=body), first=500.0)
        assert e["reason"] == "suspect data" and e["basis"] == "suspect data" and e["verdict"] == "no second quote"
    for body in (cboe_bytes(500.0, 102.0, 100.0), cboe_bytes(900.0, 752.5, 752.55)):
        e = entry_of("cboe", KFake(cboe=body), first=500.0)
        assert e["reason"] == "suspect data", e


def test_a_source_that_gives_a_verdict_has_no_reason_and_every_reason_is_from_the_fixed_set():
    out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=("alpaca", "finnhub", "cboe"), fetch=KFake(alpaca=alp(), finnhub=fin(c=900.0), cboe=cboe_bytes()),
                            clock=CLOCK, sleep=lambda s: None, credentials=creds)
    assert [(s["verdict"], s["reason"]) for s in out["sources"]] == [("agree", None), ("disagree", None), ("agree", None)]
    rng = random.Random(5)
    seen = set()
    bodies = {"alpaca": [b"{}", alp(band_t=-500, last_t=-5000), alp(bid=62.81, ask=66.86, last=None), alp(bid=102.0, ask=100.0), http_error(403), OSError("x"), TimeoutError("t")],
              "finnhub": [fin(c=0, t=0), fin(t=-5000), http_error(429), http_error(500), OSError("x")],
              "cboe": [b"nope", cboe_bytes(ltt_age=9000), http_error(404), TimeoutError("t")]}
    for i in range(600):
        names = rng.sample(["alpaca", "finnhub", "cboe"], rng.choice([1, 2, 3]))
        fake = KFake(**{n: rng.choice(bodies[n]) for n in names})
        creds_fn = creds if rng.random() < 0.8 else no_keys
        out = sq.second_opinion(752.54, "SPY", now=NOW_DT, sources=tuple(names), fetch=fake, clock=CLOCK, sleep=lambda s: None, credentials=creds_fn)
        for s in out["sources"]:
            assert s["verdict"] != "no second quote" or (REASON_PATTERN.fullmatch(s["reason"]) and s["basis"] == s["reason"]), s
            assert s["verdict"] == "no second quote" or s["reason"] is None
            seen.add(s["reason"])
    assert {"no key", "timed out", "request failed", "answer could not be used", "quote not recent", "suspect data",
            "band too wide and no recent last price", "request failed (HTTP 403)"} <= seen


def test_the_probe_prints_no_key_plainly_for_a_source_whose_key_is_absent():
    keys = {k: v for k, v in KEYS.items() if k != "FINNHUB_API_KEY"}
    code, out, fake = all_probe(credentials=keys.__getitem__)
    line = source_line(out.lines, "SPY", "finnhub")
    assert "no key" in line and "no second quote [no key]" in line and "finnhub.io" not in fake.hosts()
    assert "agree" in source_line(out.lines, "SPY", "alpaca")
    assert block(out.lines, "SPY")[-1].split()[:2] == ["consensus", "agree"]
    code, out, _ = all_probe(credentials=no_keys, fake=KFake(cboe=cboe_bytes()))
    assert "no key" in source_line(out.lines, "SPY", "alpaca") and "no key" in source_line(out.lines, "SPY", "finnhub")


def test_the_probe_prints_the_reason_even_when_there_is_no_first_price():
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({}), fetch=KFake(alpaca=http_error(403), finnhub=OSError("x"), cboe=b"nope"), clock=CLOCK,
             sleep=lambda s: None, out=out, credentials=creds)
    assert "no second quote [request failed (HTTP 403)]" in source_line(out.lines, "SPY", "alpaca")
    assert "no second quote [request failed]" in source_line(out.lines, "SPY", "finnhub")
    assert "no second quote [answer could not be used]" in source_line(out.lines, "SPY", "cboe")


def test_the_probe_prints_every_reason_without_a_key_or_anything_a_server_chose():
    echo = (AID + ASEC + FTOK)
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({"SPY": (752.54, FIRST_T)}), clock=CLOCK, sleep=lambda s: None, out=out, credentials=creds,
             fetch=KFake(alpaca=urllib.error.HTTPError("https://x/", 401, "Unauthorized " + echo, None, None), finnhub=RuntimeError(echo), cboe=OSError(echo)))
    assert not any(k in "\n".join(out.lines) for k in FAKE_KEYS) and "Unauthorized" not in "\n".join(out.lines)


# ---- 5. the docstring says what the code does

def test_the_docstring_states_the_finnhub_symbol_rule_and_the_reach_of_judging_at_the_later_time():
    doc = " ".join(sq.__doc__.split())
    assert "a Finnhub answer carries no symbol" in doc and "only because the request, with that symbol, went to its verified host" in doc
    assert "band time up to 35 seconds" in doc and "last time up to 330 seconds ahead of the caller's `now`" in doc
    assert "a redirect" in doc and "one request" in doc and "no key" in doc and "request failed (HTTP" in doc
    assert "status code and the service's name only" in doc




# ---- T22d-2, C2: the second quote trusts only the certificate bundle the program names (certifi's), whatever the
# environment says

_CA_VARIABLES = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT", "OPENSSL_CONF")


def _store(ctx):
    return sorted(json.dumps(c, sort_keys=True) for c in ctx.get_ca_certs())


def _programs_store():
    import certifi
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=certifi.where())
    return _store(ctx)


def _one_certificate(tmp_path):
    import certifi
    text = open(certifi.where(), encoding="ascii").read()
    end = "-----END CERTIFICATE-----"
    first = text[text.index("-----BEGIN CERTIFICATE-----"):text.index(end) + len(end)] + "\n"
    one = tmp_path / "one.pem"
    one.write_text(first)
    folder = tmp_path / "certs"
    folder.mkdir()
    (folder / "one.pem").write_text(first)
    return one, folder


def _contexts():
    opener = sq._build_opener()
    handler = [h for h in opener.handlers if type(h).__name__ == "_BudgetHTTPS"][0]
    return [sq._tls_context(), sq._BudgetHTTPS()._context, handler._context]


def test_the_trust_store_is_the_programs_bundle_and_ssl_cert_file_does_not_change_it(monkeypatch, tmp_path):
    for name in _CA_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    want = _programs_store()
    assert len(want) > 50                                                # the certifi bundle, a full set of authorities
    assert [_store(c) for c in _contexts()] == [want] * 3
    one, folder = _one_certificate(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(one))                        # the verifier's reproduction: one certificate
    assert [_store(c) for c in _contexts()] == [want] * 3
    monkeypatch.setenv("SSL_CERT_DIR", str(folder))
    for name in _CA_VARIABLES[2:4]:
        monkeypatch.setenv(name, str(one))
    assert [_store(c) for c in _contexts()] == [want] * 3
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))  # a file that is not there changes nothing either
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing"))
    assert [_store(c) for c in _contexts()] == [want] * 3


def test_the_bundle_is_named_by_the_program_not_found_by_openssl():
    import certifi
    assert sq.ca_bundle() == certifi.where() and sq.CA_BUNDLE_SOURCE == "certifi"
    source = open(sq.__file__, encoding="utf-8").read()
    for forbidden in ("load_default_certs", "set_default_verify_paths", "create_default_context", "capath="):
        assert forbidden + ("" if forbidden.endswith("=") else "(") not in source, forbidden


def test_no_function_of_the_module_gives_a_context_that_would_accept_an_untrusted_certificate(monkeypatch, tmp_path):
    import inspect
    one, folder = _one_certificate(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(one))
    monkeypatch.setenv("SSL_CERT_DIR", str(folder))
    contexts = _contexts()
    assert len({id(c) for c in contexts}) == 3                           # each a fresh object: changing one changes no other
    for ctx in contexts:
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
        assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2 and ctx.get_ca_certs()
    assert list(inspect.signature(sq._tls_context).parameters) == []     # nothing a caller passes loosens it
    assert list(inspect.signature(sq._BudgetHTTPS).parameters) == []
    source = open(sq.__file__, encoding="utf-8").read()
    for loosening in ("CERT_NONE", "CERT_OPTIONAL", "_create_unverified_context", "check_hostname = False", "check_hostname=False",
                      "verify_mode = ssl.CERT_N"):
        assert loosening not in source, loosening
    contexts[0].check_hostname = False                                   # a caller loosening its own copy ...
    contexts[0].verify_mode = ssl.CERT_NONE
    fresh = sq._tls_context()                                            # ... never reaches the next one built
    assert fresh.verify_mode == ssl.CERT_REQUIRED and fresh.check_hostname is True


def test_without_its_bundle_the_module_builds_no_context(monkeypatch, tmp_path):
    monkeypatch.setattr(sq, "ca_bundle", lambda: str(tmp_path / "gone.pem"))
    with pytest.raises((OSError, ssl.SSLError)):
        sq._tls_context()
