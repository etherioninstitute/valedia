#!/usr/bin/env python3
"""Pull Kalshi's settled CPI markets and their candles near each release into one sqlite store (read only, no key).

    python scripts/kalshi_cpi_pull.py --store PATH [--series KXCPI CPI KXCPIYOY CPIYOY]
        [--ref-start 2021-12-01 --ref-end 2024-11-30] [--limit-events N] [--listings-only] [--refresh] [--collector-db PATH] [--sent-mark PATH]

Ticker months are not a safe calendar: Kalshi's tickers up to CPI-22MAR name the RELEASE month, later ones the BLS
reference month. So each series is listed once over a wide ticker-month range, and the releases pulled are chosen by
each market's own release date (`release_date`, US-Eastern), never past SEAL_CEILING. Candles are asked for the
"above k" strikes only (`is_greater`), for 10:00-14:00 US-Eastern on each of the 1st to 6th weekdays before the
release (`cpi_moments`, `before=4 h`). Plain GETs
at the quiet-minute pace of `kalshi_data`; nothing is written except the --store file and the --sent-mark file's mtime. One JSON line per series, then
one of totals.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from valedia_trader.venues import collector_gate as cg  # noqa: E402
from valedia_trader.venues import kalshi_data as kd  # noqa: E402
from valedia_trader.venues import kalshi_history as kh  # noqa: E402

DEFAULT_SERIES = ("KXCPI", "CPI", "KXCPIYOY", "CPIYOY")
BEFORE = 4 * 3600
SEAL_CEILING = date(2024, 12, 31)       # last release date that may be pulled (SEAL_EPOCH_1 starts 2025-01-01)
DAYS_BACK = (1, 2, 3, 4, 5, 6)    # weekdays before the release; the study maps them to nowcast rows later
SUM_KEYS = ("windows_fetched", "windows_cached", "windows_empty", "candles_stored")


DEFAULT_COLLECTOR_DB = Path(__file__).resolve().parent.parent / "memory" / "orderbook_loop.sqlite"


def _log_closed(reason: str) -> None:
    print(time.strftime("%H:%M:%S %Z", time.localtime()) + " pull held, " + reason, file=sys.stderr, flush=True)


DEFAULT_SENT_MARK = Path.home() / "valedia-data" / "kalshi.noindex" / "kalshi_pull.sent"   # .noindex: Spotlight skips the folder


class SentMark:
    """Wraps a pacer: each time it lets a request go, the mark file's mtime is set to now. The lead's guard reads
    that mtime to tell a pull that is sending from one that is held (a held pull puts no load on the collector)."""

    def __init__(self, inner, path):
        self.inner, self.path = inner, Path(path)

    def wait(self) -> None:
        self.inner.wait()
        self.path.touch()


def build_client(collector_db=DEFAULT_COLLECTOR_DB, sent_mark=DEFAULT_SENT_MARK) -> kh.HistoryClient:
    """The client of a real run (tests replace this function). Requests start only in the quiet seconds of a
    minute AND only while the live collector's database says it is idle; there is no way to turn the gate off.
    Each request that goes touches `sent_mark`."""
    pacer = SentMark(cg.GatedPacer(kd.quiet_pacer(), cg.CollectorGate(collector_db), log=_log_closed), sent_mark)
    return kh.HistoryClient(fetch=kd.quiet_fetch, pacer=pacer)


def _date(text: str) -> date:
    return date.fromisoformat(text)


def _date_of(row: dict) -> date | None:
    return kh.cpi_ref_month(row.get("ticker", ""))


def is_greater(market: dict) -> bool:
    """An "above k" strike. Markets from 2023 on say so in strike_type; the 2022 ones carry no strike_type, so their
    own terms decide: a "-T" strike whose yes side reads "Above". Terms only, never a price or a result."""
    if market.get("strike_type"):
        return market["strike_type"] == "greater"
    return market["ticker"].rsplit("-", 1)[-1].startswith("T") and \
        str(market.get("yes_sub_title") or "").startswith("Above")


def _event(market: dict) -> str:
    return market.get("event_ticker") or market["ticker"].rsplit("-", 1)[0]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--store", required=True, help="the sqlite store (the only file written)")
    p.add_argument("--series", nargs="+", default=list(DEFAULT_SERIES))
    p.add_argument("--ref-start", type=_date, default=date(2021, 11, 1), help="listing range, by ticker month")
    p.add_argument("--ref-end", type=_date, default=date(2024, 12, 31), help="listing range, by ticker month")
    p.add_argument("--release-start", type=_date, default=date(2022, 1, 1), help="releases pulled, by ET date")
    p.add_argument("--release-end", type=_date, default=SEAL_CEILING, help="capped at SEAL_CEILING")
    p.add_argument("--limit-events", type=int, default=None, help="pilot: candles for the first N events only")
    p.add_argument("--listings-only", action="store_true", help="list markets, fetch no candles")
    p.add_argument("--sent-mark", type=Path, default=DEFAULT_SENT_MARK,
                   help="touched at each request that goes (read by the lead's guard)")
    p.add_argument("--collector-db", type=Path, default=DEFAULT_COLLECTOR_DB,
                   help="the live collector's sqlite file, read only; requests are sent only while it is idle")
    p.add_argument("--refresh", action="store_true", help="list again even where a listing is stored")
    return p.parse_args(argv)


def pull_series(client, store, series: str, args, cutoff_of) -> dict:
    """List one series and fetch the candles of its in-range "greater" markets; the one-line summary."""
    before_requests = client.requests
    asked = args.refresh or not store.listed_any(series, args.ref_start, args.ref_end)
    listing = kh.fetch_settled(client, store, series, args.ref_start, args.ref_end,
                               cutoff=cutoff_of() if asked else None, refresh=args.refresh, date_of=_date_of)
    hi = min(args.release_end, SEAL_CEILING)
    listed = [m for m in store.markets(series)
              if (r := kh.cpi_ref_month(m["ticker"])) and args.ref_start <= r <= args.ref_end]
    release = {m["ticker"]: kh.release_date(m) for m in listed}
    in_range = [m for m in listed if release[m["ticker"]] and args.release_start <= release[m["ticker"]] <= hi]
    greater = [m for m in in_range if is_greater(m)]
    events = sorted({(release[m["ticker"]], _event(m)) for m in greater})                  # by release date
    if args.limit_events is not None:
        events = events[:max(args.limit_events, 0)]
    out = {"series": series, "markets": len(in_range), "greater": len(greater), "events": len(events),
           "releases": len({d for d, _ in events}), "listing_cached": listing["cached"],
           "no_release": sum(1 for m in listed if release[m["ticker"]] is None),
           "sealed": sum(1 for m in listed if release[m["ticker"]] and release[m["ticker"]] > SEAL_CEILING),
           **{k: 0 for k in SUM_KEYS}}
    if not args.listings_only:
        keep = {e for _, e in events}
        for m in greater:
            if _event(m) not in keep:
                continue
            moments = kh.cpi_moments(release[m["ticker"]], DAYS_BACK)
            spans = kh.windows(moments, BEFORE)
            if all(store.covered(m["ticker"], a, b) for a, b in spans):      # nothing to ask, so no cutoff request
                out["windows_cached"] += len(spans)
                continue
            got = kh.fetch_candles(client, store, m, moments, cutoff_of(), before=BEFORE)
            for k in SUM_KEYS:
                out[k] += got[k]
    out["requests"] = client.requests - before_requests
    return out


def main(argv=None) -> int:
    args = parse_args(argv)
    client = build_client(args.collector_db, args.sent_mark)
    store = kh.HistoryStore(args.store)
    cache: list[float] = []

    def cutoff_of() -> float:
        if not cache:
            cache.append(kh.cutoff_ts(client, store))
        return cache[0]

    totals = {"series": "TOTAL", "markets": 0, "greater": 0, "events": 0, "no_release": 0,
              **{k: 0 for k in SUM_KEYS}, "requests": 0}
    try:
        for series in args.series:
            row = pull_series(client, store, series, args, cutoff_of)
            print(json.dumps(row, sort_keys=True))
            for k in totals:
                if k != "series":
                    totals[k] += row[k]
        print(json.dumps(totals, sort_keys=True))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
