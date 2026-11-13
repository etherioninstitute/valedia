"""Weather forecasts as they were issued, and the highs that were then observed.

Forecasts: the National Blend of Models station guidance (NBS) archived by Iowa State's mesonet (IEM), one row per
(runtime, ftime): `runtime` is when the run was issued, `ftime` the time it is valid for. The day's forecast high
is `txn` on the row valid at 00:00 UTC of the following day (on 12:00 UTC rows `txn` is the night's low). Runs
come at 01, 07, 13 and 19 UTC until 2026-05-05 and at 00, 06, 12 and 18 UTC after.

IEM answered 429 to two requests a second apart and was fine at 25 seconds, so requests here are 25 seconds apart.
Runs are asked a whole calendar month at a time (about 1.3 MB of JSON); a month IEM refuses is asked week by
week instead. Each range is cached once it is over, so a long download can stop and start again.

Observed highs: the Applied Climate Information System (ACIS), station USW00094728 (Central Park), the whole
history in one call.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from valedia_trader.venues.kalshi_data import HttpError, Pacer, urllib_get

__all__ = ["IEM_MOS_URL", "ACIS_URL", "WeatherArchiveError", "month_ranges", "week_ranges", "parse_mos", "nbs_runs",
           "cached_runs", "observed_highs"]

IEM_MOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
ACIS_URL = "https://data.rcc-acis.org/StnData"
UTC = timezone.utc


class WeatherArchiveError(RuntimeError):
    """The archive would not answer: retries ran out, or a week-sized range was refused too."""


def _day(d) -> date:
    return d if isinstance(d, date) and not isinstance(d, datetime) else date.fromisoformat(str(d)[:10])


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_ranges(start, end) -> list[tuple[date, date]]:
    """[first of month, first of next month) for every month that holds a day of [start, end]."""
    start, end = _day(start), _day(end)
    out, m = [], date(start.year, start.month, 1)
    while m <= end:
        out.append((m, _next_month(m)))
        m = _next_month(m)
    return out


def week_ranges(first: date, stop: date) -> list[tuple[date, date]]:
    """[first, stop) in seven-day pieces, the last one shorter."""
    out, a = [], first
    while a < stop:
        out.append((a, min(a + timedelta(days=7), stop)))
        a += timedelta(days=7)
    return out


def _stamp(s: str) -> str:
    """IEM's '2025-11-05T01:00:00.000' (UTC) -> '2025-11-05T01:00:00Z'."""
    return datetime.fromisoformat(str(s).replace("Z", "")[:19]).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_mos(body: bytes | str) -> list[dict]:
    """The rows of an IEM mos.py JSON answer that carry a txn, as {runtime, ftime, txn}."""
    data = json.loads(body)
    if isinstance(data, dict):
        if "data" not in data:
            raise ValueError("no rows in the answer (keys %s)" % sorted(data))
        data = data["data"]
    if not isinstance(data, list):
        raise ValueError("rows are not a list")
    return [{"runtime": _stamp(r["runtime"]), "ftime": _stamp(r["ftime"]), "txn": float(r["txn"])}
            for r in data if r.get("txn") is not None]


def _url(station: str, a: date, b: date) -> str:
    return IEM_MOS_URL + "?" + urllib.parse.urlencode({"station": station, "model": "NBS", "sts": a.isoformat() + "T00:00Z",
                                                       "ets": b.isoformat() + "T00:00Z", "format": "json"})


def _cache_file(cache_dir: Path, station: str, a: date, b: date) -> Path:
    return cache_dir / ("%s_%s_%s.json" % (station, a.isoformat(), b.isoformat()))


class _Refused(Exception):
    pass


def nbs_runs(station: str, start, end, fetch: Callable[[str], bytes] = urllib_get, spacing_seconds: float = 25.0,
             cache_dir: str | Path | None = None, sleep: Callable[[float], Any] = time.sleep,
             clock: Callable[[], float] = time.monotonic, now: datetime | None = None, tries: int = 5,
             backoff_seconds: float = 60.0, progress: Callable[[str], Any] | None = None) -> list[dict]:
    """Every NBS row with a txn whose run was issued from 00:00 UTC of `start` to before 00:00 UTC of the day after
    `end`, as {runtime, ftime, txn} sorted by runtime then ftime. Asked month by month, `spacing_seconds` apart; a
    429 or a server error waits backoff_seconds x 1, 2, 4, 8 and tries again (five tries in all). A month refused
    in any other way is asked week by week. Every range is saved in `cache_dir`, but one that had not ended a day
    before `now` is marked incomplete: `cached_runs` reads it, and the next call asks for it again."""
    start, end = _day(start), _day(end)
    now = now or datetime.now(UTC)
    done_before = (now - timedelta(days=1)).date()
    cache = Path(cache_dir) if cache_dir is not None else None
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
    pacer = Pacer(spacing_seconds, sleep, clock)

    def ask(a: date, b: date) -> list[dict]:
        url, last = _url(station, a, b), None
        for attempt in range(tries):
            if attempt:
                sleep(backoff_seconds * 2 ** (attempt - 1))
            pacer.wait()
            try:
                return parse_mos(fetch(url))
            except HttpError as e:
                if e.status == 429 or e.status >= 500:
                    last = e
                    continue
                raise _Refused(str(e)) from None
            except (ValueError, OSError) as e:
                raise _Refused(str(e)) from None
        if last is not None and last.status >= 500:
            raise _Refused(str(last))
        raise WeatherArchiveError("IEM gave up after %d tries: HTTP %s at %s" % (tries, last.status if last else "?", url))

    def cached(a: date, b: date) -> list[dict] | None:
        """A complete cached range; one saved before its end (complete: false) is asked again."""
        f = _cache_file(cache, station, a, b) if cache is not None else None
        if f is None or not f.exists():
            return None
        saved = json.loads(f.read_text())
        return saved["rows"] if saved.get("complete", True) else None

    def keep(a: date, b: date, rows: list[dict]) -> None:
        if cache is not None:
            f = _cache_file(cache, station, a, b)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"station": station, "sts": a.isoformat(), "ets": b.isoformat(),
                                       "complete": b <= done_before, "rows": rows}))
            tmp.replace(f)

    rows: list[dict] = []
    for a, b in month_ranges(start, end):
        got = cached(a, b)
        weeks = week_ranges(a, b)
        if got is None and all(cached(x, y) is not None for x, y in weeks):
            got = [r for x, y in weeks for r in cached(x, y)]
        if got is None:
            if progress:
                progress("forecast runs %s .. %s" % (a, b))
            try:
                got = ask(a, b)
                keep(a, b, got)
            except _Refused:
                got = []
                for x, y in weeks:
                    part = cached(x, y)
                    if part is None:
                        if progress:
                            progress("forecast runs %s .. %s (week)" % (x, y))
                        try:
                            part = ask(x, y)
                        except _Refused as e:
                            raise WeatherArchiveError("IEM refused the week %s .. %s too: %s" % (x, y, e)) from None
                        keep(x, y, part)
                    got += part
        rows += got
    lo, hi = start.isoformat() + "T00:00:00Z", (end + timedelta(days=1)).isoformat() + "T00:00:00Z"
    unique = {(r["runtime"], r["ftime"]): r for r in rows if lo <= r["runtime"] < hi}
    return [unique[k] for k in sorted(unique)]


def cached_runs(cache_dir: str | Path, station: str) -> list[dict]:
    """Every row in every cached range of a station, without repeats, sorted by runtime then ftime."""
    unique = {}
    for f in sorted(Path(cache_dir).glob("%s_*.json" % station)):
        for r in json.loads(f.read_text())["rows"]:
            unique[(r["runtime"], r["ftime"])] = r
    return [unique[k] for k in sorted(unique)]


def observed_highs(station_id: str, start, end, fetch: Callable[[str], bytes] = urllib_get) -> dict[str, int]:
    """Observed daily highs (whole degrees F) by ISO date from ACIS; missing ('M') or trace-like values are left out."""
    url = ACIS_URL + "?" + urllib.parse.urlencode({"sid": station_id, "sdate": _day(start).isoformat(),
                                                   "edate": _day(end).isoformat(), "elems": "maxt", "output": "json"})
    data = json.loads(fetch(url))
    if "data" not in data:
        raise WeatherArchiveError("ACIS answered without data: %s" % str(data)[:200])
    out = {}
    for day, value in data["data"]:
        try:
            out[day] = int(float(value))
        except (TypeError, ValueError):
            continue
    return out
