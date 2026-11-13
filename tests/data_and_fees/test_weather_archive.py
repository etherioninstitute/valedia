import json
import urllib.parse
from datetime import datetime, timezone

import pytest

from valedia_trader.data_and_fees import weather_archive as wa
from valedia_trader.venues.kalshi_data import HttpError


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def mos_rows(runtime, rows):
    """IEM's NBS JSON: one row per (runtime, ftime), most fields left out; txn only on 00Z (max) and 12Z (min)."""
    return [{"runtime": runtime, "ftime": f, "model": "NBS", "station": "KNYC", "tmp": 50, "txn": txn} for f, txn in rows]


class FakeIem:
    """Answers mos.py by its sts/ets; records requests and the clock time of each."""
    def __init__(self, clock, refuse=(), throttle_first=0):
        self.clock, self.refuse, self.throttle = clock, set(refuse), throttle_first
        self.asked: list[tuple[str, str]] = []
        self.times: list[float] = []

    def __call__(self, url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert q["station"] == "KNYC" and q["model"] == "NBS" and q["format"] == "json"
        self.asked.append((q["sts"], q["ets"]))
        self.times.append(self.clock.t)
        if self.throttle > 0:
            self.throttle -= 1
            raise HttpError(429, url)
        if (q["sts"], q["ets"]) in self.refuse:
            raise HttpError(400, url)
        day = q["sts"][:10]
        return json.dumps(mos_rows(day + "T01:00:00.000", [(day + "T06:00:00.000", None), ("2099-01-01T00:00:00.000", 63.0)])
                          + mos_rows(day + "T07:00:00.000", [(day + "T12:00:00.000", 46.0)])).encode()


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def test_runs_are_asked_by_whole_month_25_seconds_apart_and_cached(tmp_path):
    clock = FakeClock()
    iem = FakeIem(clock)
    rows = wa.nbs_runs("KNYC", "2025-09-20", "2025-11-03", fetch=iem, cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=NOW)
    assert iem.asked == [("2025-09-01T00:00Z", "2025-10-01T00:00Z"), ("2025-10-01T00:00Z", "2025-11-01T00:00Z"),
                         ("2025-11-01T00:00Z", "2025-12-01T00:00Z")]
    assert [b - a for a, b in zip(iem.times, iem.times[1:])] == [25.0, 25.0]
    # each month answered one row-run on its first day; only rows with a txn are kept; the September run (09-01)
    # is before the asked start and is dropped; the 11-01 run is inside [2025-09-20, 2025-11-03]
    assert rows == [{"runtime": "2025-10-01T01:00:00Z", "ftime": "2099-01-01T00:00:00Z", "txn": 63.0},
                    {"runtime": "2025-10-01T07:00:00Z", "ftime": "2025-10-01T12:00:00Z", "txn": 46.0},
                    {"runtime": "2025-11-01T01:00:00Z", "ftime": "2099-01-01T00:00:00Z", "txn": 63.0},
                    {"runtime": "2025-11-01T07:00:00Z", "ftime": "2025-11-01T12:00:00Z", "txn": 46.0}]
    again = FakeIem(clock)
    assert wa.nbs_runs("KNYC", "2025-09-20", "2025-11-03", fetch=again, cache_dir=tmp_path, sleep=clock.sleep,
                       clock=clock, now=NOW) == rows
    assert again.asked == []                                              # every month came from the cache


def test_a_month_not_yet_over_is_asked_again_next_time(tmp_path):
    clock = FakeClock()
    now = datetime(2025, 11, 10, tzinfo=timezone.utc)
    wa.nbs_runs("KNYC", "2025-11-01", "2025-11-09", fetch=FakeIem(clock), cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=now)
    assert len(wa.cached_runs(tmp_path, "KNYC")) == 2                    # kept for reading, marked not complete
    again = FakeIem(clock)
    wa.nbs_runs("KNYC", "2025-11-01", "2025-11-09", fetch=again, cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=now)
    assert again.asked == [("2025-11-01T00:00Z", "2025-12-01T00:00Z")]
    later = FakeIem(clock)                                                # once the month is over it is asked one last time
    wa.nbs_runs("KNYC", "2025-11-01", "2025-11-09", fetch=later, cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=NOW)
    assert later.asked == [("2025-11-01T00:00Z", "2025-12-01T00:00Z")]
    last = FakeIem(clock)
    wa.nbs_runs("KNYC", "2025-11-01", "2025-11-09", fetch=last, cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=NOW)
    assert last.asked == []


def test_a_refused_month_is_asked_week_by_week(tmp_path):
    clock = FakeClock()
    iem = FakeIem(clock, refuse={("2025-02-01T00:00Z", "2025-03-01T00:00Z")})
    rows = wa.nbs_runs("KNYC", "2025-02-01", "2025-02-28", fetch=iem, cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=NOW)
    assert iem.asked == [("2025-02-01T00:00Z", "2025-03-01T00:00Z"), ("2025-02-01T00:00Z", "2025-02-08T00:00Z"),
                         ("2025-02-08T00:00Z", "2025-02-15T00:00Z"), ("2025-02-15T00:00Z", "2025-02-22T00:00Z"),
                         ("2025-02-22T00:00Z", "2025-03-01T00:00Z")]
    assert [r["runtime"][:10] for r in rows] == ["2025-02-01", "2025-02-01", "2025-02-08", "2025-02-08", "2025-02-15",
                                                 "2025-02-15", "2025-02-22", "2025-02-22"]
    again = FakeIem(clock, refuse={("2025-02-01T00:00Z", "2025-03-01T00:00Z")})
    assert wa.nbs_runs("KNYC", "2025-02-01", "2025-02-28", fetch=again, cache_dir=tmp_path, sleep=clock.sleep,
                       clock=clock, now=NOW) == rows
    assert again.asked == []                                              # the weeks' caches stand in for the month


def test_a_429_from_the_forecast_archive_waits_and_retries(tmp_path):
    clock = FakeClock()
    iem = FakeIem(clock, throttle_first=2)
    rows = wa.nbs_runs("KNYC", "2025-03-01", "2025-03-31", fetch=iem, cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=NOW)
    assert len(iem.asked) == 3 and len(rows) == 2
    gaps = [b - a for a, b in zip(iem.times, iem.times[1:])]
    assert all(g >= 25.0 for g in gaps) and gaps[1] > gaps[0]            # waits grow after each refusal


def test_endless_429s_end_in_a_clear_error(tmp_path):
    clock = FakeClock()
    iem = FakeIem(clock, throttle_first=99)
    with pytest.raises(wa.WeatherArchiveError, match="429"):
        wa.nbs_runs("KNYC", "2025-03-01", "2025-03-31", fetch=iem, cache_dir=tmp_path, sleep=clock.sleep, clock=clock, now=NOW)
    assert len(iem.asked) == 5


def test_cached_runs_reads_back_every_cached_range(tmp_path):
    clock = FakeClock()
    rows = wa.nbs_runs("KNYC", "2025-09-01", "2025-10-31", fetch=FakeIem(clock), cache_dir=tmp_path, sleep=clock.sleep,
                       clock=clock, now=NOW)
    assert wa.cached_runs(tmp_path, "KNYC") == rows


def test_observed_highs_parse_acis_and_skip_missing_values():
    asked = []

    def acis(url):
        asked.append(url)
        return json.dumps({"meta": {"name": "NY CITY CENTRAL PARK"},
                           "data": [["2025-11-01", "58"], ["2025-11-02", "M"], ["2025-11-03", "59"], ["2025-11-04", "T"]]}).encode()

    got = wa.observed_highs("USW00094728", "2025-11-01", "2025-11-04", fetch=acis)
    assert got == {"2025-11-01": 58, "2025-11-03": 59}
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(asked[0]).query))
    assert q == {"sid": "USW00094728", "sdate": "2025-11-01", "edate": "2025-11-04", "elems": "maxt", "output": "json"}
    assert asked[0].startswith("https://data.rcc-acis.org/StnData?")
