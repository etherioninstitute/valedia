"""CPI ticker parser, release date and moments, the date_of / before hooks, and the pull script. Fakes only."""
import importlib.util
import json
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh

UTC = timezone.utc
CUTOFF = {"market_settled_ts": "2026-09-13T00:00:00Z"}
ROOT = Path(__file__).resolve().parents[2]


def utc(*a):
    return datetime(*a, tzinfo=UTC).timestamp()


class FakePacer:
    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1


class FakeTransport:
    """Canned JSON by path; candlesticks answer two candles per window; every URL is recorded."""
    def __init__(self, markets, empty_candles=False):
        self.markets, self.empty_candles = markets, empty_candles
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        path = parts.path.split("/trade-api/v2", 1)[1]
        q = dict(urllib.parse.parse_qsl(parts.query))
        if path == "/historical/cutoff":
            return json.dumps(CUTOFF).encode()
        if path == "/historical/markets":
            return json.dumps({"markets": self.markets, "cursor": ""}).encode()
        if path.endswith("/candlesticks"):
            end = int(q["end_ts"])
            rows = [] if self.empty_candles else [candle(end), candle(end - 60)]
            return json.dumps({"candlesticks": rows}).encode()
        raise kd.HttpError(404, url)

    def paths(self, suffix):
        return [u for u in self.urls if urllib.parse.urlsplit(u).path.endswith(suffix)]


def candle(end_ts):
    return {"end_period_ts": end_ts, "yes_bid": {"close": 40}, "yes_ask": {"close": 44}, "price": {}, "volume": 1}


def client_for(transport):
    return kh.HistoryClient(fetch=transport, pacer=FakePacer(), sleep=lambda s: None)


def cpi_market(ticker, strike="greater", expected="2024-12-11T13:30:00Z", **extra):
    row = {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "strike_type": strike, "status": "finalized",
           "result": "yes", "close_time": "2024-12-12T13:30:00Z", "settlement_ts": "2024-12-11T14:00:00Z",
           "expected_expiration_time": expected}
    row.update(extra)
    return row


# ---------------------------------------------------------------- cpi_ref_month

@pytest.mark.parametrize("ticker,want", [
    ("KXCPI-24NOV", date(2024, 11, 1)),
    ("KXCPI-24NOV-T0.3", date(2024, 11, 1)),
    ("KXCPIYOY-24NOV-T2.7", date(2024, 11, 1)),
    ("CPI-22JAN-B0.25", date(2022, 1, 1)),
    ("CPIYOY-21DEC-T6.0", date(2021, 12, 1)),
    ("CPIYOY-21DEC", date(2021, 12, 1)),
])
def test_cpi_ref_month_reads_the_reference_month(ticker, want):
    assert kh.cpi_ref_month(ticker) == want


@pytest.mark.parametrize("ticker", [
    "KXCPICORE-24NOV-T0.3", "KXCPICOREYOY-24NOV-T3.0", "CPICORE-24NOV", "KXCPIX-24NOV", "XKXCPI-24NOV",
    "KXCPI-24XYZ-T0.3", "KXCPI-24nov-T0.3", "KXCPI-2024NOV", "KXCPI-24NOV5", "KXCPI-24NOVT0.3", "KXCPI-4NOV",
    "KXNBAGAME-26NOV12TORPHI", "CPI", "KXCPI-", "", "junk", None,
])
def test_cpi_ref_month_is_none_for_other_series_bad_months_and_junk(ticker):
    assert kh.cpi_ref_month(ticker) is None


# ---------------------------------------------------------------- release_date

def test_release_date_is_the_us_eastern_date_of_expected_expiration():
    assert kh.release_date({"expected_expiration_time": "2024-12-11T13:30:00Z"}) == date(2024, 12, 11)
    # 02:00 UTC is still the evening before in New York, in winter and summer
    assert kh.release_date({"expected_expiration_time": "2024-12-12T02:00:00Z"}) == date(2024, 12, 11)
    assert kh.release_date({"expected_expiration_time": "2024-07-12T03:30:00Z"}) == date(2024, 7, 11)
    assert kh.release_date({"expected_expiration_time": "2024-07-12T04:30:00Z"}) == date(2024, 7, 12)


def test_release_date_falls_back_to_close_time_and_then_none():
    assert kh.release_date({"close_time": "2024-12-11T13:30:00Z"}) == date(2024, 12, 11)
    assert kh.release_date({"expected_expiration_time": "", "close_time": "2024-12-11T13:30:00Z"}) == date(2024, 12, 11)
    assert kh.release_date({"expected_expiration_time": None, "close_time": "2024-12-11T13:30:00Z"}) == date(2024, 12, 11)
    both = {"expected_expiration_time": "2024-12-11T13:30:00Z", "close_time": "2024-12-20T13:30:00Z"}
    assert kh.release_date(both) == date(2024, 12, 11)
    assert kh.release_date({}) is None
    assert kh.release_date({"expected_expiration_time": "", "close_time": ""}) is None
    assert kh.release_date({"close_time": "not a time"}) is None


# ---------------------------------------------------------------- cpi_moments

def test_cpi_moments_default_is_five_weekdays_at_two_pm_eastern():
    got = kh.cpi_moments(date(2024, 12, 11))          # Wednesday, winter (EST = UTC-5)
    want = [utc(2024, 12, d, 19) for d in (9, 6, 5, 4, 3)]
    assert got == want


def test_cpi_moments_skip_the_weekend():
    # release on a Monday: 1 weekday back is Friday, then Thursday, then Wednesday
    assert kh.cpi_moments(date(2024, 12, 9), days_back=(1, 2, 3)) == [utc(2024, 12, d, 19) for d in (6, 5, 4)]
    # release on a Wednesday, 3 and 4 back cross the weekend (Tue, Mon, then Fri, Thu across the weekend)
    assert kh.cpi_moments(date(2024, 12, 11), days_back=(1, 2, 3, 4)) == \
        [utc(2024, 12, d, 19) for d in (10, 9, 6, 5)]
    # a release dated on a Saturday counts Friday as the first weekday back
    assert kh.cpi_moments(date(2024, 12, 14), days_back=(1, 2)) == [utc(2024, 12, 13, 19), utc(2024, 12, 12, 19)]
    for t in kh.cpi_moments(date(2024, 12, 9), days_back=range(1, 12)):
        assert datetime.fromtimestamp(t, kh.EASTERN).weekday() < 5


def test_cpi_moments_after_the_march_dst_switch():
    # DST began Sun 2024-03-10: Mon 03-11 is EDT (UTC-4) at 18:00Z, Fri 03-08 is EST (UTC-5) at 19:00Z
    got = kh.cpi_moments(date(2024, 3, 12), days_back=(1, 2, 3))
    assert got == [utc(2024, 3, 11, 18), utc(2024, 3, 8, 19), utc(2024, 3, 7, 19)]
    for t in got:
        assert datetime.fromtimestamp(t, kh.EASTERN).hour == 14


def test_cpi_moments_across_the_november_dst_switch():
    # DST ended Sun 2024-11-03: Mon 11-04 is EST (19:00Z), Fri 11-01 is still EDT (18:00Z)
    got = kh.cpi_moments(date(2024, 11, 5), days_back=(1, 2))
    assert got == [utc(2024, 11, 4, 19), utc(2024, 11, 1, 18)]
    for t in got:
        assert datetime.fromtimestamp(t, kh.EASTERN).hour == 14


def test_cpi_moments_with_no_days_is_empty():
    assert kh.cpi_moments(date(2024, 12, 11), days_back=()) == []


# ---------------------------------------------------------------- fetch_settled date_of

def sports(ticker):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "status": "finalized", "result": "yes"}


def test_fetch_settled_default_still_filters_by_the_game_date():
    rows = [sports("KXNBAGAME-24NOV12TORPHI-TOR"), sports("KXNBAGAME-24DEC25TORPHI-TOR"), sports("KXCPI-24NOV-T0.3")]
    store = kh.HistoryStore(":memory:")
    got = kh.fetch_settled(client_for(FakeTransport(rows)), store, "KXNBAGAME", date(2024, 11, 1), date(2024, 11, 30),
                           cutoff=utc(2026, 9, 13))
    assert (got["listed"], got["in_period"], got["stored"]) == (3, 1, 1)
    assert [m["ticker"] for m in store.markets()] == ["KXNBAGAME-24NOV12TORPHI-TOR"]


def test_fetch_settled_with_date_of_filters_cpi_by_reference_month():
    rows = [cpi_market("KXCPI-24NOV-T0.3"), cpi_market("KXCPI-24OCT-T0.3"), cpi_market("KXCPI-25JAN-T0.3"),
            cpi_market("KXCPICORE-24NOV-T0.3"), cpi_market("KXCPI-24NOV-T0.2", status="active")]
    store = kh.HistoryStore(":memory:")
    got = kh.fetch_settled(client_for(FakeTransport(rows)), store, "KXCPI", date(2021, 12, 1), date(2024, 11, 30),
                           cutoff=utc(2026, 9, 13), date_of=lambda r: kh.cpi_ref_month(r.get("ticker", "")))
    assert (got["listed"], got["in_period"], got["stored"]) == (5, 2, 2)
    assert [m["ticker"] for m in store.markets()] == ["KXCPI-24NOV-T0.3", "KXCPI-24OCT-T0.3"]


def test_the_sports_parser_stores_no_cpi_markets_without_date_of():
    store = kh.HistoryStore(":memory:")
    got = kh.fetch_settled(client_for(FakeTransport([cpi_market("KXCPI-24NOV-T0.3")])), store, "KXCPI",
                           date(2021, 12, 1), date(2024, 11, 30), cutoff=utc(2026, 9, 13))
    assert got["stored"] == 0


def test_date_of_is_keyword_only():
    with pytest.raises(TypeError):
        kh.fetch_settled(client_for(FakeTransport([])), kh.HistoryStore(":memory:"), "KXCPI", date(2024, 1, 1),
                         date(2024, 2, 1), utc(2026, 9, 13), False, lambda r: None)


# ---------------------------------------------------------------- fetch_candles before

def _starts(t):
    return [(int(q["start_ts"]), int(q["end_ts"])) for q in
            (dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)) for u in t.paths("/candlesticks"))]


def test_fetch_candles_default_window_is_one_hour():
    t = FakeTransport([])
    m = cpi_market("KXCPI-24NOV-T0.3", series="KXCPI")
    moment = utc(2024, 12, 6, 19)
    out = kh.fetch_candles(client_for(t), kh.HistoryStore(":memory:"), m, [moment], utc(2026, 9, 13))
    assert _starts(t) == [(int(moment) - 3600, int(moment))]
    assert out == {"windows_fetched": 1, "windows_cached": 0, "windows_empty": 0, "candles_stored": 2}


def test_fetch_candles_before_widens_the_window():
    t = FakeTransport([])
    m = cpi_market("KXCPI-24NOV-T0.3", series="KXCPI")
    moment = utc(2024, 12, 6, 19)
    kh.fetch_candles(client_for(t), kh.HistoryStore(":memory:"), m, [moment], utc(2026, 9, 13), before=4 * 3600)
    assert _starts(t) == [(int(moment) - 14400, int(moment))]
    assert t.paths("/candlesticks")[0].startswith(kd.BASE_URL + "/historical/markets/KXCPI-24NOV-T0.3/candlesticks")


# ---------------------------------------------------------------- the script

@pytest.fixture
def pull():
    spec = importlib.util.spec_from_file_location("kalshi_cpi_pull", ROOT / "scripts" / "kalshi_cpi_pull.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MARKETS = [
    cpi_market("KXCPI-24NOV-T0.3"),
    cpi_market("KXCPI-24NOV-T0.2"),
    cpi_market("KXCPI-24NOV-B0.25", strike="between"),
    cpi_market("KXCPI-24OCT-T0.3", expected="2024-11-13T13:30:00Z"),
    cpi_market("KXCPI-25JAN-T0.3", expected="2025-02-12T13:30:00Z"),       # reference month out of range
    cpi_market("KXCPICORE-24NOV-T0.3"),                                     # another series
]


def run(pull, monkeypatch, tmp_path, capsys, argv, transport=None):
    t = transport or FakeTransport(MARKETS)
    clients = []

    def build(collector_db=None):
        clients.append(client_for(t))
        return clients[-1]

    monkeypatch.setattr(pull, "build_client", build)
    assert pull.main(["--store", str(tmp_path / "cpi.sqlite"), *argv]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    return lines, t


def test_script_pulls_listing_and_candles_and_prints_a_summary_per_series(pull, monkeypatch, tmp_path, capsys):
    lines, t = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI"])
    assert [x["series"] for x in lines] == ["KXCPI", "TOTAL"]
    s = lines[0]
    # in range: NOV-T0.3, NOV-T0.2, NOV-B0.25, OCT-T0.3 (JAN 2025 and CORE are not); candles for the 3 "greater"
    assert (s["markets"], s["greater"], s["events"]) == (4, 3, 2)
    assert (s["windows_fetched"], s["windows_cached"], s["windows_empty"], s["candles_stored"]) == (18, 0, 0, 36)
    assert s["requests"] == 20 and s["requests"] == len(t.urls)       # cutoff + one listing + 18 windows
    assert lines[1]["requests"] == 20 and lines[1]["candles_stored"] == 36 and lines[1]["markets"] == 4
    assert len(t.paths("/historical/cutoff")) == 1 and len(t.paths("/historical/markets")) == 1
    # windows are 10:00-14:00 US-Eastern: the Nov release (Wed 2024-12-11) gives 4 h windows ending at 19:00Z
    spans = {(a, b) for a, b in _starts(t)}
    assert (int(utc(2024, 12, 6, 15)), int(utc(2024, 12, 6, 19))) in spans
    assert all(b - a == 4 * 3600 for a, b in spans)
    # the between strike and the other series were never asked
    assert not any("B0.25" in u or "CORE" in u or "25JAN" in u for u in t.paths("/candlesticks"))
    assert all("series_ticker=KXCPI" in u for u in t.paths("/historical/markets"))


def test_script_second_run_is_all_cached(pull, monkeypatch, tmp_path, capsys):
    run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI"])
    lines, t = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI"])
    s = lines[0]
    assert s["listing_cached"] is True
    assert (s["windows_fetched"], s["windows_cached"], s["requests"]) == (0, 18, 0)
    assert t.urls == []


def test_script_refresh_lists_again(pull, monkeypatch, tmp_path, capsys):
    run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI", "--listings-only"])
    lines, t = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI", "--listings-only", "--refresh"])
    assert len(t.paths("/historical/markets")) == 1 and lines[0]["listing_cached"] is False


def test_script_listings_only_fetches_no_candles(pull, monkeypatch, tmp_path, capsys):
    lines, t = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI", "--listings-only"])
    assert t.paths("/candlesticks") == []
    assert lines[0]["markets"] == 4 and lines[0]["windows_fetched"] == 0 and lines[0]["requests"] == 2


def test_script_limit_events_pilots_the_first_events(pull, monkeypatch, tmp_path, capsys):
    lines, t = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI", "--limit-events", "1"])
    s = lines[0]
    assert s["events"] == 1 and s["greater"] == 3 and s["windows_fetched"] == 6      # only the OCT event
    assert all("24OCT" in u for u in t.paths("/candlesticks"))


def test_script_counts_empty_windows(pull, monkeypatch, tmp_path, capsys):
    lines, _ = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI", "--limit-events", "1"],
                   FakeTransport(MARKETS, empty_candles=True))
    s = lines[0]
    assert s["windows_empty"] == s["windows_fetched"] and s["candles_stored"] == 0


def test_script_ref_range_arguments(pull, monkeypatch, tmp_path, capsys):
    lines, _ = run(pull, monkeypatch, tmp_path, capsys,
                   ["--series", "KXCPI", "--ref-start", "2024-11-15", "--ref-end", "2025-01-31", "--listings-only"])
    # the ticker month's first day must lie in [start, end]: 2024-11-01 is before the start, so only JAN 2025 is
    # listed, and its release (2025-02-12) is in the seal, so nothing is in range
    assert lines[0]["markets"] == 0 and lines[0]["sealed"] == 1


def test_script_defaults(pull):
    a = pull.parse_args(["--store", "x.sqlite"])
    assert a.series == ["KXCPI", "CPI", "KXCPIYOY", "CPIYOY"]
    assert (a.ref_start, a.ref_end) == (date(2021, 11, 1), date(2024, 12, 31))
    assert (a.release_start, a.release_end) == (date(2022, 1, 1), date(2024, 12, 31))
    assert pull.DAYS_BACK == (1, 2, 3, 4, 5, 6)
    assert a.limit_events is None and not a.listings_only and not a.refresh
    with pytest.raises(SystemExit):
        pull.parse_args([])


def test_script_writes_only_the_store(pull, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI", "CPI"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cpi.sqlite"]


def test_script_handles_each_default_series_and_totals(pull, monkeypatch, tmp_path, capsys):
    lines, _ = run(pull, monkeypatch, tmp_path, capsys, ["--listings-only"])
    assert [x["series"] for x in lines] == ["KXCPI", "CPI", "KXCPIYOY", "CPIYOY", "TOTAL"]


def test_build_client_is_quiet_and_keyless(pull):
    from valedia_trader.venues import collector_gate as cg
    c = pull.build_client("/nonexistent/collector.sqlite")        # never read here: no wait() is called
    assert c.fetch is kd.quiet_fetch and isinstance(c.pacer, cg.GatedPacer)
    assert isinstance(c.pacer.inner, kd.PhasePacer) and isinstance(c.pacer.gate, cg.CollectorGate)
    assert c.requests == 0


def test_script_never_pulls_a_release_in_the_seal(pull, monkeypatch, tmp_path, capsys):
    # an in-range reference month whose release date (by expected_expiration_time) falls in 2025 is skipped
    sealed = cpi_market("KXCPI-24NOV-T0.4", expected="2025-01-15T13:30:00Z")
    lines, t = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI"], FakeTransport([*MARKETS, sealed]))
    assert lines[0]["sealed"] == 1
    assert not any("T0.4" in u for u in t.paths("/candlesticks"))
    assert pull.SEAL_CEILING == date(2024, 12, 31)


def test_cpi_ref_month_rejects_a_trailing_newline():
    assert kh.cpi_ref_month("KXCPI-24NOV\n") is None


def test_is_greater_reads_2022_terms_when_strike_type_is_missing(pull):
    old = {"ticker": "CPI-22APR-T0.1", "strike_type": None, "yes_sub_title": "Above 0.1%"}
    assert pull.is_greater(old)
    assert not pull.is_greater({**old, "yes_sub_title": "Below 0.1%"})
    assert not pull.is_greater({**old, "ticker": "CPI-22APR-B0.15"})
    assert pull.is_greater({"ticker": "X-T1", "strike_type": "greater"})
    assert not pull.is_greater({"ticker": "X-T1", "strike_type": "less", "yes_sub_title": "Above 1"})


def test_script_selects_releases_by_release_date_not_ticker_month(pull, monkeypatch, tmp_path, capsys):
    # early tickers name the release month: CPI-21DEC settled 2021-12-10 (out), CPI-22JAN 2022-01-13 (in)
    rows = [cpi_market("CPI-21DEC-T0.5", strike=None, yes_sub_title="Above 0.5%", expected="2021-12-10T13:30:00Z"),
            cpi_market("CPI-22JAN-T0.5", strike=None, yes_sub_title="Above 0.5%", expected="2022-01-13T13:30:00Z"),
            cpi_market("KXCPI-24DEC-T0.3", expected="2025-01-15T13:30:00Z")]
    lines, t = run(pull, monkeypatch, tmp_path, capsys, ["--series", "KXCPI"], FakeTransport(rows))
    s = lines[0]
    assert (s["markets"], s["greater"], s["releases"], s["sealed"]) == (1, 1, 1, 1)
    assert all("CPI-22JAN" in u for u in t.paths("/candlesticks"))
