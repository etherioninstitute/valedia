"""Tests for the second price source (Cboe delayed quotes): the reader, the comparison, the probe.

No test touches the network: the fetch function is injected and faked, and the redirect rules are exercised through a
fake HTTPS handler. The two real answers kept below were read on 2026-11-13 (one request each), trimmed of the
volatility fields."""
import io
import json
import math
import random
import urllib.error
import urllib.request
import urllib.response
from datetime import datetime, timezone
from email.message import Message
from fractions import Fraction

import pytest

from valedia_trader.data_and_fees import second_quote as sq

NOW = datetime(2026, 11, 13, 21, 30, 3, tzinfo=timezone.utc).timestamp()          # 16:30 in New York, after the close
CLOCK = lambda: NOW                                                                # noqa: E731

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


# ---- compare

FETCHED = NOW                                                       # 16:30 New York on 2026-11-13


def sec(bid=None, ask=None, last=None, ltt="2026-11-13T16:00:00", fetched=FETCHED):
    return {"symbol": "SPY", "bid": bid, "ask": ask, "last": last, "last_trade_time": ltt, "fetched_at": fetched,
            "source": "cboe-delayed"}


def verdict(first, second, tol=0.01):
    return sq.compare(first, second, tol)["verdict"]


def test_compare_returns_the_three_fields_and_nothing_else():
    out = sq.compare(100.0, sec(99.98, 100.02))
    assert set(out) == {"verdict", "difference", "basis"}
    assert out["verdict"] == "agree" and out["difference"] == 0.0 and out["basis"] == "bid/ask band"


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


def test_a_bid_above_the_ask_is_not_a_band_and_the_last_price_is_used():
    s = sec(100.5, 100.0, last=100.2)
    out = sq.compare(101.2, s)
    assert out["basis"] == "last price" and out["verdict"] == "agree"
    assert verdict(101.21, s) == "disagree"
    assert verdict(100.2, sec(100.5, 100.0)) == "no second quote"       # crossed and no last: nothing to judge against


def test_without_both_sides_the_last_price_is_used_and_without_a_last_there_is_no_quote():
    for s in (sec(None, 100.1, last=100.0), sec(99.9, None, last=100.0), sec(None, None, last=100.0)):
        out = sq.compare(101.0, s)
        assert out["basis"] == "last price" and out["verdict"] == "agree" and abs(out["difference"] - 0.01) < 1e-12
        assert verdict(101.01, s) == "disagree"
        assert verdict(99.0, s) == "agree" and verdict(98.99, s) == "disagree"
    for s in (sec(None, 100.1), sec(99.9, None), sec(None, None)):
        assert sq.compare(100.0, s) == {"verdict": "no second quote", "difference": None, "basis": s_basis(s)}


def s_basis(s):
    return sq.compare(100.0, s)["basis"]


def test_the_band_is_preferred_over_the_last_price_when_both_exist():
    s = sec(99.0, 101.0, last=150.0)
    assert sq.compare(100.0, s)["basis"] == "bid/ask band" and verdict(100.0, s) == "agree"
    assert verdict(150.0, s) == "disagree"


def test_difference_is_the_signed_fraction_from_the_mid_or_the_last():
    assert math.isclose(sq.compare(102.0, sec(99.0, 101.0))["difference"], 0.02)
    assert math.isclose(sq.compare(98.0, sec(99.0, 101.0))["difference"], -0.02)
    assert math.isclose(sq.compare(95.0, sec(last=100.0))["difference"], -0.05)


def test_a_quote_from_an_earlier_day_is_never_agree():
    for ltt in ("2026-11-12T16:00:00", "2026-11-12T23:59:59", "2025-11-13T16:00:00", "2026-10-13T16:00:00"):
        out = sq.compare(100.0, sec(99.98, 100.02, last=100.0, ltt=ltt))
        assert out["verdict"] == "no second quote" and out["difference"] is None


def test_a_missing_or_unreadable_time_is_no_second_quote():
    for ltt in (None, "", "garbage", "2026-11-13", "2026-11-13 16:00:00", "2026-13-13T16:00:00", "2026-11-31T16:00:00",
                "2026-11-13T25:00:00", 20261113, ["2026-11-13T16:00:00"], "2026-11-13T16:00:00Z", "2026-11-13T16:00:00+00:00"):
        assert verdict(100.0, sec(99.98, 100.02, ltt=ltt)) == "no second quote", ltt
    s = sec(99.98, 100.02)
    del s["last_trade_time"]
    assert verdict(100.0, s) == "no second quote"


def test_the_trading_day_is_the_new_york_day_not_the_utc_day():
    late = datetime(2026, 11, 14, 1, 30, tzinfo=timezone.utc).timestamp()          # 20:30 on the 13th in New York
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-11-13T16:00:00", fetched=late)) == "agree"
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-11-14T00:30:00", fetched=late)) == "no second quote"
    early = datetime(2026, 11, 13, 4, 59, tzinfo=timezone.utc).timestamp()          # 23:59 on the 12th in New York
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-11-12T16:00:00", fetched=early)) == "agree"
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-11-13T00:00:00", fetched=early)) == "no second quote"


def test_summer_time_is_handled():
    fetched = datetime(2026, 7, 14, 3, 30, tzinfo=timezone.utc).timestamp()         # 23:30 on the 13th in New York (UTC-4)
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-07-13T16:00:00", fetched=fetched)) == "agree"
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-07-14T00:10:00", fetched=fetched)) == "no second quote"


def test_a_trade_time_in_the_future_is_no_second_quote():
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-11-13T17:30:00")) == "no second quote"          # an hour ahead of 16:30
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-11-13T16:34:00")) == "agree"                    # within the allowed 5 min
    assert verdict(100.0, sec(99.98, 100.02, ltt="2026-11-13T16:36:00")) == "no second quote"


def test_garbage_in_place_of_a_second_quote_is_no_second_quote_and_never_an_exception():
    for bad in (None, "x", 5, [], {}, {"bid": 1.0}, sec(99.98, 100.02, fetched=None), sec(99.98, 100.02, fetched="now"),
                sec(99.98, 100.02, fetched=float("nan")), sec(99.98, 100.02, fetched=True), sec(99.98, 100.02, fetched=float("inf")),
                sec(bid="99", ask="101", last="100"), sec(bid=float("nan"), ask=float("nan"), last=float("nan")),
                sec(bid=-1.0, ask=-2.0, last=-3.0), sec(bid=0, ask=0, last=0), sec(bid=1e9, ask=1e9, last=1e9),
                sec(bid=True, ask=True, last=True), sec(bid=[1], ask={}, last=None)):
        assert sq.compare(100.0, bad) == {"verdict": "no second quote", "difference": None, "basis": sq.compare(100.0, bad)["basis"]}
        assert sq.compare(100.0, bad)["verdict"] == "no second quote"


def test_an_unusable_first_price_is_never_agree():
    for bad in (None, float("nan"), float("inf"), -1.0, 0.0, 0, True, "100", [], 1e8):
        out = sq.compare(bad, sec(99.98, 100.02))
        assert out["verdict"] == "disagree" and out["difference"] is None


def test_no_second_quote_is_reported_before_a_bad_first_price():
    assert verdict(None, None) == "no second quote"


def test_a_bad_tolerance_is_refused():
    for bad in (float("nan"), float("inf"), -0.01, True, "0.01", None, 1.5, [0.01]):
        with pytest.raises(ValueError):
            sq.compare(100.0, sec(99.98, 100.02), bad)


# ---- compare against an independent calculation on generated cases (whole numbers, no floats in the oracle)

def _oracle(first_c, bid_c, ask_c, last_c, ltt, tol_t):
    """Prices in whole cents, tolerance in ten-thousandths, the time as a string; the answer from integer arithmetic."""
    if ltt is None or ltt[:10] != "2026-11-13":
        return "no second quote", None
    if bid_c is not None and ask_c is not None and bid_c <= ask_c:
        # first >= bid - t/10000 * mid  and  first <= ask + t/10000 * mid, mid = (bid + ask) / 2
        lo = Fraction(bid_c) - Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2)
        hi = Fraction(ask_c) + Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2)
        ref = Fraction(bid_c + ask_c, 2)
        return ("agree" if lo <= first_c <= hi else "disagree"), float((first_c - ref) / ref)
    if last_c is not None:
        ok = abs(Fraction(first_c) - last_c) * 10000 <= tol_t * last_c
        return ("agree" if ok else "disagree"), float(Fraction(first_c - last_c, last_c))
    return "no second quote", None


def test_a_thousand_generated_cases_agree_with_the_independent_calculation():
    rng = random.Random(20261113)
    seen = {"agree": 0, "disagree": 0, "no second quote": 0, "band": 0, "last": 0}
    for i in range(3000):
        bid = rng.randint(100, 2_000_000)
        ask = bid + rng.randint(0, 5000)
        last = rng.randint(100, 2_000_000)
        tol_t = rng.choice([0, 1, 5, 50, 100, 100, 100, 250, 1000, rng.randint(0, 10000)])
        kind = rng.random()
        bid_c, ask_c, last_c = bid, ask, last
        if kind < 0.10:
            bid_c, ask_c = ask, bid                                                  # crossed (or equal)
        elif kind < 0.20:
            bid_c = None
        elif kind < 0.30:
            ask_c = None
        elif kind < 0.40:
            bid_c = ask_c = None
        if rng.random() < 0.15:
            last_c = None
        ltt = rng.choice(["2026-11-13T16:00:00", "2026-11-13T09:31:00", "2026-11-12T16:00:00", None, "2026-11-13T16:00:00"])
        # first price: often right on the edge of whatever band will be used, a cent either way
        if bid_c is not None and ask_c is not None and bid_c <= ask_c and rng.random() < 0.6:
            edge = rng.choice([bid_c - Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2),
                               ask_c + Fraction(tol_t, 10000) * Fraction(bid_c + ask_c, 2)])
            first_c = int(edge) + rng.choice([-1, 0, 1])
        elif last_c is not None and rng.random() < 0.6:
            first_c = int(last_c * (10000 + rng.choice([-1, 1]) * tol_t) / 10000) + rng.choice([-1, 0, 1])
        else:
            first_c = rng.randint(100, 2_000_000)
        first_c = max(first_c, 1)
        second = {"symbol": "SPY", "bid": None if bid_c is None else bid_c / 100, "ask": None if ask_c is None else ask_c / 100,
                  "last": None if last_c is None else last_c / 100, "last_trade_time": ltt, "fetched_at": NOW,
                  "source": "cboe-delayed"}
        want, want_diff = _oracle(first_c, bid_c, ask_c, last_c, ltt, tol_t)
        got = sq.compare(first_c / 100, second, tol_t / 10000)
        assert got["verdict"] == want, (i, first_c, bid_c, ask_c, last_c, ltt, tol_t, got)
        if want_diff is None:
            assert got["difference"] is None
        else:
            assert math.isclose(got["difference"], want_diff, rel_tol=1e-9, abs_tol=1e-12), (i, got, want_diff)
        seen[want] += 1
        if want != "no second quote":
            seen["band" if bid_c is not None and ask_c is not None and bid_c <= ask_c else "last"] += 1
    assert min(seen.values()) > 100, seen                                            # every branch was exercised a lot


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
    code = sq.probe(["SPY"], first_quotes=first_source({"SPY": (752.6, FIRST_T)}, calls), fetch=fake, clock=CLOCK,
                    sleep=lambda s: None, out=out)
    text = "\n".join(out.lines)
    assert code == 0 and calls == [["SPY"]] and len(fake.calls) == 1
    for part in ("SPY", "752.6", "752.54", "752.5", "752.55", "2026-11-13T20:59:00Z", "2026-11-13T16:00:00", "+0.01%", "agree"):
        assert part in text, (part, text)


def test_the_probe_reports_a_disagreement_and_a_missing_second_quote():
    out = Out()
    sq.probe(["SPY", "XLE"], first_quotes=first_source({"SPY": (800.0, FIRST_T), "XLE": (64.8, FIRST_T)}),
             fetch=Fake(REAL_SPY, b"garbage"), clock=CLOCK, sleep=lambda s: None, out=out)
    spy = [l for l in out.lines if l.startswith("SPY")]
    assert len(spy) == 1 and "disagree" in spy[0] and "+6.3" in spy[0]
    xle = [l for l in out.lines if l.startswith("XLE")]
    assert len(xle) == 1 and "no second quote" in xle[0]


def test_the_probe_survives_the_first_source_failing_or_missing_a_symbol():
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source(RuntimeError("down")), fetch=Fake(REAL_SPY), clock=CLOCK, sleep=lambda s: None, out=out)
    line = [l for l in out.lines if l.startswith("SPY")][0]
    assert "first source failed" in line and "752.54" in line and "agree" not in line.replace("disagree", "")
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({}), fetch=Fake(REAL_SPY), clock=CLOCK, sleep=lambda s: None, out=out)
    assert "no first quote" in [l for l in out.lines if l.startswith("SPY")][0]
    out = Out()
    sq.probe(["SPY"], first_quotes=first_source({"SPY": (float("nan"), FIRST_T)}), fetch=Fake(REAL_SPY), clock=CLOCK,
             sleep=lambda s: None, out=out)
    assert "no first quote" in [l for l in out.lines if l.startswith("SPY")][0]


def test_the_probe_takes_at_most_ten_symbols_and_refuses_before_any_request():
    syms = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    calls, fake, out = [], Fake(REAL_SPY), Out()
    sq.probe(syms, first_quotes=first_source({}, calls), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=out)
    assert calls == [syms] and len(fake.calls) == 10                      # ten is fine, one request each
    calls, fake, out = [], Fake(REAL_SPY), Out()
    assert sq.probe(syms + ["K"], first_quotes=first_source({}, calls), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=out) == 2
    assert calls == [] and fake.calls == [] and any("at most 10" in l for l in out.lines)


def test_the_probe_refuses_a_bad_symbol_before_any_request():
    for bad in ("spy", "SPY?x=1", "TOOLONGX", "", "SPY/../x"):
        calls, fake, out = [], Fake(REAL_SPY), Out()
        assert sq.probe(["SPY", bad], first_quotes=first_source({}, calls), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=out) == 2
        assert calls == [] and fake.calls == []
    calls, fake, out = [], Fake(REAL_SPY), Out()
    assert sq.probe([], first_quotes=first_source({}, calls), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=out) == 2
    assert calls == [] and fake.calls == []


def test_a_repeated_symbol_is_fetched_once():
    calls, fake, out = [], Fake(REAL_SPY), Out()
    sq.probe(["SPY", "SPY"], first_quotes=first_source({}, calls), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=out)
    assert calls == [["SPY"]] and len(fake.calls) == 1


def test_the_default_first_source_is_the_order_steps_own_function_by_import(monkeypatch):
    from valedia_trader.execution_and_risk import order_step
    seen = []
    monkeypatch.setattr(order_step, "minute_quotes", lambda symbols: seen.append(list(symbols)) or {"SPY": (752.6, FIRST_T)})
    out = Out()
    sq.probe(["SPY"], fetch=Fake(REAL_SPY), clock=CLOCK, sleep=lambda s: None, out=out)
    assert seen == [["SPY"]] and "752.6" in "\n".join(out.lines)


def test_main_runs_the_probe_subcommand_and_refuses_bad_arguments():
    out = Out()
    assert sq.main(["probe", "SPY"], first_quotes=first_source({"SPY": (752.6, FIRST_T)}), fetch=Fake(REAL_SPY), clock=CLOCK,
                   sleep=lambda s: None, out=out) == 0
    fake = Fake(REAL_SPY)
    assert sq.main(["probe", "spy"], first_quotes=first_source({}), fetch=fake, clock=CLOCK, sleep=lambda s: None, out=Out()) == 2
    assert fake.calls == []
    with pytest.raises(SystemExit) as e:
        sq.main([], fetch=fake, out=Out())
    assert e.value.code == 2 and fake.calls == []
    with pytest.raises(SystemExit):
        sq.main(["probe"], fetch=fake, out=Out())
    with pytest.raises(SystemExit):
        sq.main(["nonsense", "SPY"], fetch=fake, out=Out())
    assert fake.calls == []
