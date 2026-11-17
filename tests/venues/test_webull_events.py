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
    assert m["market_symbol"] == wb.WITHHELD and m["tradable_status"] == wb.WITHHELD and m["fractionable"] is None
    assert "expected_exp_date" not in m and m["price_ranges"] is None      # rev 2: a date that does not parse is dropped


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



# ---- rev 2

ACCOUNT_SHAPED = "98765432"


@pytest.mark.parametrize("symbol", [ACCOUNT_SHAPED, "1KXNBAGAME", "kxnbagame-26nov17detsas-det", "KXNBAGAME_26NOV",
                                    "KX" + "A" * 79, "KXNBAGAME-26NOV17DETSAS-DÉT", "-KXNBAGAME", ""])
def test_a_symbol_is_kept_only_in_its_strict_shape(symbol):
    row = market_row("KXNBAGAME-26NOV17DETSAS-DET")
    row.update(symbol=symbol, series_symbol=symbol, event_symbol=symbol)
    m = reader(FakeInstrument(market_pages={None: ([row], None)})).markets(series_symbol="KXNBAGAME")[0]
    assert m["market_symbol"] == m["series_symbol"] == m["event_symbol"] == wb.WITHHELD


@pytest.mark.parametrize("symbol", ["KXNBAGAME-26NOV17DETSAS-DET", "KXCPI-26JAN-T0.3", "K", "KX" + "A" * 78])
def test_a_symbol_in_its_shape_is_kept(symbol):
    row = market_row("KXNBAGAME-26NOV17DETSAS-DET")
    row["symbol"] = symbol
    assert reader(FakeInstrument(market_pages={None: ([row], None)})).markets()[0]["market_symbol"] == symbol


def test_long_digit_runs_are_withheld_from_free_text():
    row = market_row("KXNBAGAME-26NOV17DETSAS-DET", yes_condition=f"Account {ACCOUNT_SHAPED} wins by 1234567 or 123456 points")
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME", name=f"Series {ACCOUNT_SHAPED}")], None)},
                          market_pages={None: ([row], None)},
                          events=[{"series_symbol": "KXNBAGAME", "symbol": "KXNBAGAME-26NOV17DETSAS", "title": f"x{ACCOUNT_SHAPED}y"}])
    r = reader(fake)
    m = r.markets()[0]
    assert m["yes_condition"] == f"Account {wb.WITHHELD} wins by {wb.WITHHELD} or 123456 points"
    assert r.series()[0]["name"] == f"Series {wb.WITHHELD}"
    assert ACCOUNT_SHAPED not in json.dumps(r.events_of("KXNBAGAME"))


@pytest.mark.parametrize("value,kept", [
    ("2026-11-18", True), ("2026-11-18T05:00:00Z", True), ("2026-11-18T05:00:00.123+00:00", True),
    (1763424000, True), (1763424000000, True),
    (ACCOUNT_SHAPED, False), ("20261118", False), ("1763424000", False), ("2026-13-45", False), ("2026-02-30", False),
    ("1999-12-31", False), ("2101-01-01", False), (12345678, False), (99999999999999, False), (True, False),
    ("２０２６-11-18", False), ("2026-11-18 junk", False), (1763424000.5, False)])
def test_dates_are_kept_only_when_they_parse_between_2020_and_2100(value, kept):
    row = market_row("KXNBAGAME-26NOV17DETSAS-DET", expected_exp_date=value, latest_exp_date=value)
    m = reader(FakeInstrument(market_pages={None: ([row], None)})).markets()[0]
    if kept:
        assert m["expected_exp_date"] == value and m["latest_exp_date"] == value
    else:
        assert "expected_exp_date" not in m and "latest_exp_date" not in m


@pytest.mark.parametrize("value,kept", [
    ("0.01", 0.01), ("0", 0.0), ("1", 1.0), ("1.0", 1.0), (0.01, 0.01), (1, 1.0), (0, 0.0),
    ("1_000", None), ("١", None), ("１", None), ("1e-400", None), ("-0.5", None), (-0.5, None), ("1.5", None), (2, None),
    ("0.01.0", None), (" 0.01", None), ("nan", None), ("inf", None), (float("nan"), None), (float("inf"), None),
    (True, None), ("", None), (".5", None), ("+0.5", None), (1e-400, 0.0)])
def test_price_range_numbers_are_kept_only_as_plain_decimals_between_0_and_1(value, kept):
    row = market_row("KXNBAGAME-26NOV17DETSAS-DET", price_ranges=[{"start": value, "end": value, "step": value}])
    r = reader(FakeInstrument(market_pages={None: ([row], None)})).markets()[0]["price_ranges"][0]
    for k in ("start", "end"):
        assert (r.get(k) if kept is not None else k not in r) == (kept if kept is not None else True), (k, value)
    if kept is not None and kept > 0:
        assert r["step"] == kept
    else:
        assert "step" not in r                                        # a step must be above 0


def test_a_market_with_no_usable_step_reports_none(capsys):
    rows = [market_row("KXNBAGAME-26NOV17DETSAS-DET", price_ranges=[{"start": "0.0", "end": "1.0", "step": "1e-2"}]),
            market_row("KXNBAGAME-26NOV17DETSAS-SAS", price_ranges=[{"start": "0.0", "end": "1.0", "step": "0"}])]
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME")], None)}, market_pages={"KXNBAGAME": {None: (rows, None)}})
    assert we.main(["probe", "--series", "KXNBAGAME"], environ=ENV, client=FakeData(fake),
                   kalshi_fetch=kalshi_fetch_for({}), sleep=lambda s: None) == 0
    assert json.loads(capsys.readouterr().out)["series"]["KXNBAGAME"]["price_steps"] == []


def test_account_shaped_values_never_reach_the_probe_report(tmp_path, capsys):
    def row(i):
        return {k: ACCOUNT_SHAPED for k in ("series_symbol", "event_symbol", "symbol", "name", "yes_condition", "category",
                                            "tradable_status", "expected_exp_date", "latest_exp_date", "fractionable")} | {
            "price_ranges": [{"start": ACCOUNT_SHAPED, "end": ACCOUNT_SHAPED, "step": ACCOUNT_SHAPED}]}
    fake = FakeInstrument(series_pages={None: ([row(0), series_row("KXNBAGAME")], None)},
                          market_pages={"KXNBAGAME": {None: ([row(1), market_row("KXNBAGAME-26NOV17DETSAS-DET")], None)}})
    out = tmp_path / "p.json"
    code = we.main(["probe", "--series", "KXNBAGAME", "--out", str(out)], environ=ENV, client=FakeData(fake),
                   kalshi_fetch=kalshi_fetch_for({"KXNBAGAME": [ACCOUNT_SHAPED, "KXNBAGAME-26NOV17DETSAS-DET"]}), sleep=lambda s: None)
    text = capsys.readouterr().out
    assert code == 0 and ACCOUNT_SHAPED not in text and ACCOUNT_SHAPED not in out.read_text()
    nba = json.loads(text)["series"]["KXNBAGAME"]
    assert nba["webull_unreadable_symbols"] == 1 and nba["kalshi_unreadable_symbols"] == 1
    assert (nba["both"], nba["webull_only"], nba["kalshi_only"]) == (1, 0, 0)
    assert json.loads(text)["series_listing"]["unreadable_symbols"] == 1


@pytest.mark.parametrize("asked,allowance", [(["KXNCAAMBGAME"], 15), (["KXNCAAMBGAME", "KXNCAAFGAME"], 7),
                                             (["KXA", "KXB", "KXC"], 5), (list(DEFAULT := we.DEFAULT_SERIES), 3),
                                             ([f"KXS{i}" for i in range(10)], 1)])
def test_the_market_page_budget_is_shared_by_the_series_asked(asked, allowance, capsys):
    fake = FakeInstrument(series_pages={None: ([series_row(s) for s in asked], None)},
                          market_pages=endless_pages(lambda n: [market_row(f"KXNCAAMBGAME-26NOV{n:02d}AAABBB-AAA")]))
    code = we.main(["probe", "--series", *asked], environ=ENV, client=FakeData(fake), kalshi_fetch=kalshi_fetch_for({}),
                   sleep=lambda s: None)
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["requests"]["webull"] <= we.PROBE_WEBULL_REQUESTS
    for s in asked:
        assert report["series"][s]["webull_page_allowance"] == allowance
        assert report["series"][s]["webull_pages"] == allowance
    assert sum(1 for c in fake.calls if c[0] == "markets") == allowance * len(asked)


@pytest.mark.parametrize("raised", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_a_base_exception_inside_an_sdk_call_ends_with_one_plain_line(raised, tmp_path, capsys):
    class Stopping(FakeInstrument):
        def list_event_instruments(self, **kw):
            self.calls.append(("markets", kw))
            raise raised(f"stopped with x-app-key={KEY} {SECRET}")
    fake = Stopping(series_pages={None: ([series_row("KXNBAGAME")], None)})
    out = tmp_path / "p.json"
    code = we.main(["probe", "--series", "KXNBAGAME", "--out", str(out)], environ=ENV, client=FakeData(fake),
                   kalshi_fetch=kalshi_fetch_for({}), sleep=lambda s: None)
    captured = capsys.readouterr()
    assert code not in (0, None) and isinstance(code, int)
    assert captured.out == "" and not out.exists()
    lines = captured.err.splitlines()
    assert len(lines) == 1 and raised.__name__ in lines[0]
    assert KEY[:6] not in captured.err and SECRET[:6] not in captured.err and "x-app-key" not in captured.err


# ---- T50: the events reader is a practice entry: a live name in reach is refused, and it never opens the live file

LIVE_KEY, LIVE_SECRET = "ZqLiveKey7Rt4Kw9PmXv2Bn", "ZqLiveSecret3LbNHs4GdYp8"          # made up


@pytest.mark.parametrize("name", ["WEBULL_LIVE_APP_KEY", "WEBULL_LIVE_APP_SECRET"])
def test_t50_the_probe_refuses_when_a_live_name_is_in_reach(tmp_path, capsys, name):
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME")], None)})
    fetched = []
    code = we.main(["probe"], environ={**ENV, name: LIVE_SECRET}, client=FakeData(fake), kalshi_fetch=fetched.append,
                   sleep=lambda s: None)
    err = capsys.readouterr().err
    clean = LIVE_SECRET not in err and KEY not in err and SECRET not in err
    assert clean and code == 2 and fake.calls == [] and fetched == []
    assert err.startswith("refused:") and name in err


def test_t50_the_probe_finds_a_live_name_in_the_ordinary_file_and_never_opens_the_live_file(tmp_path, monkeypatch, capsys):
    live = tmp_path / "live" / "webull-live.env"
    live.parent.mkdir(mode=0o700)
    live.write_text(f"WEBULL_LIVE_APP_KEY={LIVE_KEY}\nWEBULL_LIVE_APP_SECRET={LIVE_SECRET}\n")
    live.chmod(0o000)                                                  # present, unreadable
    monkeypatch.setattr(core_secrets, "LIVE_ENV_FILE", live)
    monkeypatch.setattr(core_secrets, "read_live_pair", lambda *a, **k: pytest.fail("the live file was read"))
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in ENV.items()) + f"WEBULL_LIVE_APP_KEY={LIVE_KEY}\n")
    f.chmod(0o600)
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(f))
    for k in (*ENV, "WEBULL_LIVE_APP_KEY"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)                                          # the undo removes what the load puts there
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME")], None)})
    code = we.main(["probe"], client=FakeData(fake), kalshi_fetch=lambda u: b"{}", sleep=lambda s: None)
    err = capsys.readouterr().err
    clean = LIVE_KEY not in err
    assert clean and code == 2 and fake.calls == [] and "WEBULL_LIVE_APP_KEY" in err
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME")], None)})
    code = we.main(["probe"], environ=dict(ENV), client=FakeData(fake), kalshi_fetch=kalshi_fetch_for({}),
                   sleep=lambda s: None)
    assert code == 0                                                   # no live name: it runs, the live file unopened


# ---- T50 addendum 3 (F): no host and no environment word of the events reader is read from a variable

def test_t50_a3_planted_host_and_environment_variables_move_the_events_reader_nowhere(monkeypatch):
    planted = {"WEBULL_HOST": "planted.invalid", "WEBULL_API_HOST": "planted.invalid", "WEBULL_SANDBOX_HOST":
               "planted.invalid", "WEBULL_EVENTS_HOST": "planted.invalid", "WEBULL_ENDPOINT": "https://planted.invalid",
               "WEBULL_ENVIRONMENT": "production", "WEBULL_EVENTS_ENV": "production", "WEBULL_LIVE_ENV": "sandbox"}
    for name, value in planted.items():
        monkeypatch.setenv(name, value)
    fake = FakeInstrument(series_pages={None: ([series_row("KXNBAGAME")], None)})
    report, reader = we.probe(environ={**ENV, **planted}, client=FakeData(fake), kalshi_fetch=kalshi_fetch_for({}),
                              sleep=lambda s: None)
    assert (report["environment"], report["host"]) == ("sandbox", wb.SANDBOX_HOST)
    assert (reader.environment, reader.host) == ("sandbox", wb.SANDBOX_HOST)
