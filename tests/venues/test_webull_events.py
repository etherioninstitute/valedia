"""Tests for the read-only reader of Webull's event-contract instrument lists and its probe (T18).

Every Webull answer here comes from a fake SDK client built from the documented shapes (developer.webull.com
event-series-list and event-market-list, as recorded in .foreman/scratch/R4-sports-odds-recon.md C2) with method
signatures equal to the installed SDK's Instrument methods. No test calls Webull or Kalshi, reads the real secrets
file, or writes inside the repository.
"""
import inspect
import json
import random
import urllib.parse

import pytest

from valedia_trader.core import secrets as core_secrets
from valedia_trader.venues import webull as wb
from valedia_trader.venues import webull_events as we

KEY, SECRET = "Zq7RtKw9PmXv3LbN", "Hs4GdYp8TcVa2MeQ"
ENV = {"WEBULL_APP_KEY": KEY, "WEBULL_APP_SECRET": SECRET, "WEBULL_ENV": "sandbox"}


@pytest.fixture(autouse=True)
def _home_is_temporary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _no_tripwire(monkeypatch):
    """The trader's own output guard would hide a leak by printing <secret>; switched off here so that only this
    module's gates stand between the planted secret and the output."""
    monkeypatch.setattr(core_secrets, "remember", lambda value: None)


class Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class ServerException(Exception):
    """Shaped and named like the SDK's ServerException (webull/core/exception/exceptions.py)."""
    def __init__(self, error_code, error_msg, http_status, request_id=None):
        self.error_code, self.error_msg, self.http_status, self.request_id = error_code, error_msg, http_status, request_id

    def __str__(self):
        return f"request headers: x-app-key={KEY} x-app-secret={SECRET}"


def series_row(symbol, category="SPORTS", **extra):
    return {"category": category, "series_id": "778899", "symbol": symbol, "name": "Pro Basketball Game",
            "frequency": "custom", **extra}


def market_row(symbol, status="OC", **extra):
    series, event = symbol.split("-")[0], "-".join(symbol.split("-")[:2])
    return {"series_symbol": series, "event_symbol": event, "symbol": symbol, "instrument_id": "502257268",
            "yes_condition": "Detroit wins the game", "tradable_status": status, "fractionable": True,
            "can_close_early": False, "expected_exp_date": "2026-11-18", "latest_exp_date": "2026-12-02",
            "payout_date": "2026-12-03", "rules_primary": "If Detroit wins, the market resolves to Yes.",
            "price_ranges": [{"start": "0.0", "end": "1.0", "step": "0.01"}], **extra}


class FakeInstrument:
    """Stands in for webull.data.quotes.instrument.Instrument: the three list calls, keyword for keyword."""

    def __init__(self, series_pages=None, market_pages=None, events=None, fail=None):
        # series_pages / market_pages: {pagination_key or None: (rows, next_key)}; market_pages may be keyed by
        # (series_symbol, key). `fail`: {call name: exception or Response to give instead}.
        self.series_pages = {None: ([], None)} if series_pages is None else series_pages
        self.market_pages = {} if market_pages is None else market_pages
        self.events = [] if events is None else events
        self.fail = {} if fail is None else fail
        self.calls = []

    def _answer(self, name, rows, key):
        if name in self.fail:
            f = self.fail[name]
            if isinstance(f, BaseException):
                raise f
            return f
        body = {"data": rows}
        if key is not None:
            body["pagination_key"] = key
        return Response(body)

    def list_event_series(self, category=None, symbols=None, pagination_key=None):
        self.calls.append(("series", {"category": category, "symbols": symbols, "pagination_key": pagination_key}))
        rows, nxt = self.series_pages.get(pagination_key, ([], None))
        if symbols:
            wanted = set(symbols.split(",")) if isinstance(symbols, str) else set(symbols)
            rows = [r for page in self.series_pages.values() for r in page[0] if r.get("symbol") in wanted]
            nxt = None
        if category:
            rows = [r for r in rows if r.get("category") == category]
        return self._answer("series", rows, nxt)

    def get_event_events(self, series_symbol, symbols=None, status=None):
        self.calls.append(("events", {"series_symbol": series_symbol, "symbols": symbols, "status": status}))
        if "events" in self.fail:
            return self._answer("events", [], None)
        return Response([e for e in self.events if e.get("series_symbol") == series_symbol])

    def list_event_instruments(self, series_symbol=None, event_symbol=None, symbols=None, expiration_date_after=None,
                               pagination_key=None):
        self.calls.append(("markets", {"series_symbol": series_symbol, "event_symbol": event_symbol, "symbols": symbols,
                                       "expiration_date_after": expiration_date_after, "pagination_key": pagination_key}))
        pages = self.market_pages[series_symbol] if series_symbol is not None and series_symbol in self.market_pages else self.market_pages
        rows, nxt = pages.get(pagination_key, ([], None))
        if event_symbol:
            rows = [r for r in rows if r.get("event_symbol") == event_symbol]
        return self._answer("markets", rows, nxt)


class FakeData:
    def __init__(self, instrument):
        self.instrument = instrument


def endless_pages(make_rows):
    """A page table whose every key leads to another page: pagination never ends by itself."""
    class Pages(dict):
        def get(self, key, default=None):
            n = 0 if key is None else int(key.split("-")[1])
            return make_rows(n), f"page-{n + 1}"
    return Pages()


def reader(instrument, **kw):
    kw.setdefault("sleep", lambda s: None)
    return we.WebullEvents(KEY, SECRET, client=FakeData(instrument), **kw)


# ---- the fake follows the SDK

def test_fake_has_the_sdk_signatures():
    instrument = pytest.importorskip("webull.data.quotes.instrument").Instrument
    for name in ("list_event_series", "get_event_events", "list_event_instruments"):
        assert inspect.signature(getattr(FakeInstrument, name)) == inspect.signature(getattr(instrument, name)), name


# ---- the three calls: fields kept, everything else dropped

def test_series_keeps_only_the_listed_fields():
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME", extra_field="drop me")], None)})
    out = reader(fake).series()
    assert out == [{"series_symbol": "KXNBAGAME", "name": "Pro Basketball Game", "category": "SPORTS"}]
    assert out.complete is True and out.pages == 1
    assert fake.calls == [("series", {"category": None, "symbols": None, "pagination_key": None})]


def test_series_passes_category_and_symbols_and_checks_them():
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME"), series_row("KXCPI", "ECONOMICS")], None)})
    r = reader(fake)
    assert [s["series_symbol"] for s in r.series(category="SPORTS")] == ["KXNBAGAME"]
    assert [s["series_symbol"] for s in r.series(symbols=["KXCPI"])] == ["KXCPI"]
    assert fake.calls[1][1]["symbols"] == "KXCPI"
    calls = len(fake.calls)
    for bad in ({"category": "sports; drop"}, {"symbols": ["KX NBA"]}, {"symbols": [f"KX{i}" for i in range(101)]}):
        with pytest.raises(wb.WebullError):
            r.series(**bad)
    assert len(fake.calls) == calls                                   # refused before any request


def test_markets_keep_only_the_listed_fields():
    fake = FakeInstrument(market_pages={None: ([market_row("KXNBAGAME-26NOV17DETSAS-DET", extra="x")], None)})
    out = reader(fake).markets(series_symbol="KXNBAGAME")
    assert out == [{"series_symbol": "KXNBAGAME", "event_symbol": "KXNBAGAME-26NOV17DETSAS",
                    "market_symbol": "KXNBAGAME-26NOV17DETSAS-DET", "yes_condition": "Detroit wins the game",
                    "tradable_status": "OC", "fractionable": True, "can_close_early": False,
                    "expected_exp_date": "2026-11-18", "latest_exp_date": "2026-12-02",
                    "price_ranges": [{"start": 0.0, "end": 1.0, "step": 0.01}]}]
    assert "instrument_id" not in json.dumps(out) and "502257268" not in json.dumps(out)
    assert fake.calls[0] == ("markets", {"series_symbol": "KXNBAGAME", "event_symbol": None, "symbols": None,
                                         "expiration_date_after": None, "pagination_key": None})


def test_markets_by_event_and_symbols():
    rows = [market_row("KXNBAGAME-26NOV17DETSAS-DET"), market_row("KXNBAGAME-26NOV16HOUMIA-MIA")]
    fake = FakeInstrument(market_pages={None: (rows, None)})
    r = reader(fake)
    assert [m["market_symbol"] for m in r.markets(event_symbol="KXNBAGAME-26NOV16HOUMIA")] == ["KXNBAGAME-26NOV16HOUMIA-MIA"]
    r.markets(symbols=["KXNBAGAME-26NOV17DETSAS-DET", "KXNBAGAME-26NOV16HOUMIA-MIA"])
    assert fake.calls[-1][1]["symbols"] == "KXNBAGAME-26NOV17DETSAS-DET,KXNBAGAME-26NOV16HOUMIA-MIA"


def test_market_values_of_the_wrong_shape_are_not_kept():
    row = market_row("KXNBAGAME-26NOV17DETSAS-DET", tradable_status="oc; DROP", fractionable="maybe",
                     expected_exp_date="tomorrow", price_ranges="0-1")
    row["symbol"] = "KX NBA GAME"

    out = reader(FakeInstrument(market_pages={None: ([row], None)})).markets(series_symbol="KXNBAGAME")
    m = out[0]
    assert m["market_symbol"] is None and m["tradable_status"] == wb.WITHHELD and m["fractionable"] is None
    assert m["expected_exp_date"] is None and m["price_ranges"] is None


def test_events_of_keeps_only_the_listed_fields():
    events = [{"series_symbol": "KXNBAGAME", "symbol": "KXNBAGAME-26NOV17DETSAS", "name": "Detroit at San Antonio",
               "expected_exp_date": "2026-11-18", "event_id": "991", "status": "OPEN", "category": "SPORTS"}]
    fake = FakeInstrument(events=events)
    out = reader(fake).events_of("KXNBAGAME")
    assert out == [{"series_symbol": "KXNBAGAME", "event_symbol": "KXNBAGAME-26NOV17DETSAS", "name": "Detroit at San Antonio",
                    "category": "SPORTS", "expected_exp_date": "2026-11-18"}]
    assert fake.calls == [("events", {"series_symbol": "KXNBAGAME", "symbols": None, "status": None})]


# ---- pagination

def test_pagination_is_followed_to_the_end():
    pages = {None: ([market_row("KXNBAGAME-26NOV17DETSAS-DET")], "k1"), "k1": ([market_row("KXNBAGAME-26NOV17DETSAS-SAS")], None)}
    fake = FakeInstrument(market_pages=pages)
    out = reader(fake).markets(series_symbol="KXNBAGAME")
    assert len(out) == 2 and out.pages == 2 and out.complete is True and out.stopped is None
    assert [c[1]["pagination_key"] for c in fake.calls] == [None, "k1"]


def test_pagination_stops_at_the_page_cap():
    fake = FakeInstrument(market_pages=endless_pages(lambda n: [market_row(f"KXNBAGAME-26NOV{n:02d}AAABBB-AAA")]))
    out = reader(fake).markets(series_symbol="KXNBAGAME", max_pages=3)
    assert len(fake.calls) == 3 and len(out) == 3 and out.complete is False and out.stopped == "page cap"
    fake2 = FakeInstrument(series_pages=endless_pages(lambda n: [series_row(f"KXS{n}")]))
    assert len(reader(fake2, max_pages=we.MAX_PAGES).series()) == we.MAX_PAGES and len(fake2.calls) == we.MAX_PAGES


def test_a_repeated_or_unreadable_pagination_key_stops():
    rows = [market_row("KXNBAGAME-26NOV17DETSAS-DET")]
    fake = FakeInstrument(market_pages={None: (rows, "k1"), "k1": (rows, "k1")})
    out = reader(fake).markets(series_symbol="KXNBAGAME")
    assert len(fake.calls) == 2 and out.complete is False and out.stopped == "repeated pagination key"
    fake = FakeInstrument(market_pages={None: (rows, "bad key with spaces")})
    out = reader(fake).markets(series_symbol="KXNBAGAME")
    assert len(fake.calls) == 1 and out.complete is False and out.stopped == "unreadable pagination key"


def test_an_answer_of_another_shape_raises():
    fake = FakeInstrument(fail={"markets": Response({"rows": []})})
    with pytest.raises(wb.WebullError, match="shape"):
        reader(fake).markets(series_symbol="KXNBAGAME")


# ---- request caps

def test_the_request_cap_is_never_passed():
    fake = FakeInstrument(market_pages=endless_pages(lambda n: [market_row(f"KXNBAGAME-26NOV{n:02d}AAABBB-AAA")]))
    r = reader(fake, max_requests=5)
    out = r.markets(series_symbol="KXNBAGAME", max_pages=50)
    assert len(fake.calls) == 5 and r.requests == 5 and out.stopped == "request cap" and out.complete is False
    with pytest.raises(we.RequestCapReached):
        r.series()
    assert len(fake.calls) == 5


def test_a_429_retry_counts_as_a_request_and_respects_the_cap():
    fake = FakeInstrument(fail={"series": Response({"message": "too many"}, status=429)})
    sleeps = []
    r = reader(fake, max_requests=2, sleep=sleeps.append)
    with pytest.raises(wb.RateLimitHit):
        r.series()
    assert len(fake.calls) == 2 and r.requests == 2


def test_the_pacer_holds_the_test_environment_to_80_percent_of_30_in_60_s():
    t = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        t[0] += s
    fake = FakeInstrument(market_pages=endless_pages(lambda n: [market_row(f"KXNBAGAME-26NOV{n:02d}AAABBB-AAA")]))
    r = we.WebullEvents(KEY, SECRET, client=FakeData(fake), clock=lambda: t[0], sleep=sleep)
    r.markets(series_symbol="KXNBAGAME", max_pages=25)
    assert len(fake.calls) == 25 and sleeps and sleeps[0] >= 60          # the 25th call waited for the window
    assert we.EVENT_RATE_LIMITS["sandbox"] == (30, 60)


# ---- the environment rule

@pytest.mark.parametrize("env", ["production", "PRODUCTION", "paper", "sandbox2", "live"])
def test_the_probe_refuses_anything_but_the_test_environment(env, tmp_path, capsys):
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME")], None)})
    fetched = []
    out = tmp_path / "probe.json"
    code = we.main(["probe", "--out", str(out)], environ=dict(ENV, WEBULL_ENV=env), client=FakeData(fake),
                   kalshi_fetch=fetched.append, sleep=lambda s: None)
    assert code == 2 and fake.calls == [] and fetched == [] and not out.exists()
    assert "test environment" in capsys.readouterr().err


def test_the_probe_refuses_a_host_that_is_not_the_sandbox(monkeypatch, tmp_path):
    monkeypatch.setitem(we.HOSTS, "sandbox", "api.sandboxed.webull.com")
    fake = FakeInstrument()
    code = we.main(["probe"], environ=ENV, client=FakeData(fake), kalshi_fetch=lambda u: b"{}", sleep=lambda s: None)
    assert code == 2 and fake.calls == []


def test_the_host_rule_is_the_order_steps():
    order_step = pytest.importorskip("valedia_trader.execution_and_risk.order_step")
    hosts = ["api.sandbox.webull.com", "sandbox", "api.webull.com", "sandbox.webull.com", "api.sandboxed.webull.com",
             "api.sandbox-webull.com", "API.SANDBOX.WEBULL.COM", "api.sandbox.webull.com/", "api..sandbox", "", None, 5,
             "api.sandbox.webull.com ", "x" * 300 + ".sandbox"]
    for h in hosts:
        assert we._sandbox_host(h) == order_step._sandbox_host(h), h


# ---- the probe

def kalshi_fetch_for(tables, log=None, endless=False):
    """A fake Kalshi transport: `tables` maps a series to its open tickers; each answer one page of `markets`."""
    def fetch(url):
        if log is not None:
            log.append(url)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert urllib.parse.urlsplit(url).path.endswith("/markets") and q["status"] == "open"
        rows = [{"ticker": t, "event_ticker": t.rsplit("-", 1)[0], "status": "active"} for t in tables.get(q["series_ticker"], [])]
        cursor = (q.get("cursor", "") + "x") if endless else ""
        return json.dumps({"markets": rows, "cursor": cursor}).encode()
    return fetch


def probe_fake():
    series = [series_row("KXNBAGAME"), series_row("KXNFLGAME"), series_row("KXCPI", "ECONOMICS")]
    nba = [market_row("KXNBAGAME-26NOV17DETSAS-DET"), market_row("KXNBAGAME-26NOV17DETSAS-SAS"),
           market_row("KXNBAGAME-26NOV18BOSNYK-BOS", status="CO"), market_row("KXNBAGAME-26NOV19LALGSW-LAL", status="NT")]
    return FakeInstrument(series_pages={None: (series, None)},
                          market_pages={"KXNBAGAME": {None: (nba, None)}, "KXNFLGAME": {None: ([], None)}})


def test_the_probe_reports_the_comparison(tmp_path, capsys):
    out = tmp_path / "probe.json"
    kalshi = {"KXNBAGAME": ["KXNBAGAME-26NOV17DETSAS-DET", "KXNBAGAME-26NOV17DETSAS-SAS", "KXNBAGAME-26NOV20MILCHI-MIL"]}
    fake = probe_fake()
    code = we.main(["probe", "--series", "KXNBAGAME", "KXNFLGAME", "KXNHLGAME", "--out", str(out)], environ=ENV,
                   client=FakeData(fake), kalshi_fetch=kalshi_fetch_for(kalshi), sleep=lambda s: None)
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    report = json.loads(out.read_text())
    assert printed == report
    assert report["environment"] == "sandbox"
    assert report["series_by_category"] == {"ECONOMICS": 1, "SPORTS": 2}
    nba = report["series"]["KXNBAGAME"]
    assert nba["webull_lists_series"] is True
    assert nba["webull_markets_by_status"] == {"CO": 1, "NT": 1, "OC": 2}
    assert nba["kalshi_open_markets"] == 3
    assert (nba["both"], nba["webull_only"], nba["kalshi_only"]) == (2, 2, 1)
    assert nba["examples"]["kalshi_only"] == ["KXNBAGAME-26NOV20MILCHI-MIL"]
    assert nba["price_steps"] == [0.01]
    assert report["series"]["KXNHLGAME"]["webull_lists_series"] is False
    assert report["requests"]["webull"] <= we.PROBE_WEBULL_REQUESTS and report["requests"]["kalshi"] <= we.PROBE_KALSHI_REQUESTS
    assert "502257268" not in out.read_text()                          # no instrument_id
    assert list(we.DEFAULT_SERIES) == ["KXNFLGAME", "KXNBAGAME", "KXNHLGAME", "KXNCAAFGAME", "KXNCAAMBGAME"]


def test_the_probe_keeps_to_its_request_caps(tmp_path, capsys):
    fake = FakeInstrument(series_pages=endless_pages(lambda n: [series_row(f"KXS{n}")]),
                          market_pages=endless_pages(lambda n: [market_row(f"KXNBAGAME-26NOV{n:02d}AAABBB-AAA")]))
    log = []
    many = ["KXNBAGAME", "KXNFLGAME", "KXNHLGAME", "KXNCAAFGAME", "KXNCAAMBGAME", "KXMLBGAME", "KXWNBAGAME"]
    code = we.main(["probe", "--series", *many], environ=ENV, client=FakeData(fake),
                   kalshi_fetch=kalshi_fetch_for({s: [f"{s}-26NOV01AAABBB-AAA"] for s in many}, log, endless=True), sleep=lambda s: None)
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert len(fake.calls) <= 20 and len(log) <= 10
    assert report["requests"] == {"webull": len(fake.calls), "webull_cap": 20, "kalshi": len(log), "kalshi_cap": 10}
    assert report["series_listing"]["complete"] is False
    assert any(s.get("kalshi_complete") is False for s in report["series"].values())


def test_the_probe_writes_nothing_inside_the_repository(capsys):
    target = wb.REPO / "probe-should-not-exist.json"
    code = we.main(["probe", "--out", str(target)], environ=ENV, client=FakeData(probe_fake()),
                   kalshi_fetch=kalshi_fetch_for({}), sleep=lambda s: None)
    assert code == 2 and not target.exists()


# ---- a hostile fake: the planted secret in every field and message

def test_a_hostile_fake_leaks_nothing(tmp_path, capsys):
    planted = [KEY, SECRET, KEY.lower(), SECRET.upper()]

    def hostile_row(i):
        s = planted[i % len(planted)]
        return {k: f"{s} {s}" for k in ("category", "symbol", "name", "title", "series_symbol", "event_symbol", "yes_condition",
                                         "tradable_status", "fractionable", "can_close_early", "expected_exp_date",
                                         "latest_exp_date", "instrument_id")} | {
            "price_ranges": [{"start": s, "end": s, "step": s}], s: s,
            "symbol": f"KXNBAGAME-{s}", "series_symbol": f"KX{s}", "tradable_status": s.upper()[:8]}

    class Pages(dict):
        def get(self, key, default=None):
            return [hostile_row(i) for i in range(3)], f"{KEY}{(key or '')[:3]}x"

    fails = [ServerException(f"E_{KEY}", f"failed for {KEY} with {SECRET}", 500),
             ServerException("OPENAPI_X", f"key={KEY}", 403),
             Response({"error_code": KEY, "message": f"bad {SECRET}"}, status=500),
             RuntimeError(f"transport {KEY}:{SECRET}")]

    class Hostile(FakeInstrument):
        n = 0

        def list_event_instruments(self, **kw):
            self.calls.append(("markets", kw))
            Hostile.n += 1
            if Hostile.n % 2:
                f = fails[(Hostile.n // 2) % len(fails)]
                if isinstance(f, BaseException):
                    raise f
                return f
            return Response({"data": [hostile_row(i) for i in range(4)], "pagination_key": None})

    fake = Hostile(series_pages=Pages())
    kalshi = kalshi_fetch_for({s: [f"{s}-{KEY}", f"{s}-26NOV01AAABBB-AAA", f"{s}-{SECRET.lower()}"] for s in we.DEFAULT_SERIES})
    out = tmp_path / "probe.json"
    code = we.main(["probe", "--out", str(out)], environ=ENV, client=FakeData(fake), kalshi_fetch=kalshi, sleep=lambda s: None)
    captured = capsys.readouterr()
    texts = [captured.out, captured.err, out.read_text() if out.exists() else ""]
    assert code == 0 and texts[2]
    report = json.loads(captured.out)                                 # still a usable report, not "text withheld"
    assert set(report["series"]) == set(we.DEFAULT_SERIES)
    assert any("webull_error" in s for s in report["series"].values())            # the planted exceptions were met
    assert all(s["kalshi_open_markets"] == 1 for s in report["series"].values())  # only the clean Kalshi ticker kept
    r = reader(fake)
    for call in (r.series, lambda: r.markets(series_symbol="KXNBAGAME"), lambda: r.markets(series_symbol="KXNBAGAME"),
                 lambda: r.markets(series_symbol="KXNBAGAME"), lambda: r.markets(series_symbol="KXNBAGAME")):
        try:
            texts.append(json.dumps(call()))
        except wb.WebullError as exc:
            texts.append(str(exc))
            assert exc.__cause__ is None and exc.__context__ is None
    for text in texts:
        low = text.lower()
        for word in (KEY, SECRET):
            for i in range(len(word) - wb.SECRET_RUN + 1):
                assert word[i:i + wb.SECRET_RUN].lower() not in low, (word[i:i + wb.SECRET_RUN], text[:300])
        assert "<secret>" not in text


def test_a_failure_while_opening_the_session_is_gated(monkeypatch, capsys):
    def boom(*a, **k):
        raise ServerException(f"E_{KEY}", f"no session for {KEY}/{SECRET}", 401)
    monkeypatch.setattr(we, "_data_client", boom)
    code = we.main(["probe"], environ=ENV, kalshi_fetch=lambda u: b"{}", sleep=lambda s: None)
    err = capsys.readouterr().err
    assert code == 2 and "not ready" in err
    assert KEY[:6].lower() not in err.lower() and SECRET[:6].lower() not in err.lower()


# ---- the set comparison

def test_the_set_comparison_on_generated_sets():
    rng = random.Random(18)
    for _ in range(300):
        pool = [f"KXNBAGAME-26NOV{rng.randrange(100):02d}AAABBB-{c}" for c in "ABCDEFGH" for _ in range(rng.randrange(3))]
        a = set(rng.sample(pool, rng.randrange(len(pool) + 1))) if pool else set()
        b = set(rng.sample(pool, rng.randrange(len(pool) + 1))) if pool else set()
        c = we.compare(a, b)
        assert (c["both"], c["webull_only"], c["kalshi_only"]) == (len(a & b), len(a - b), len(b - a))
        for name, s in (("both", a & b), ("webull_only", a - b), ("kalshi_only", b - a)):
            ex = c["examples"][name]
            assert ex == sorted(s)[:5] and len(ex) <= 5
    c = we.compare([], [])
    assert c == {"both": 0, "webull_only": 0, "kalshi_only": 0, "examples": {"both": [], "webull_only": [], "kalshi_only": []}}

