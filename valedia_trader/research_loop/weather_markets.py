"""Does the official forecast, with nothing but its own past errors behind it, beat Kalshi's New York high-temperature prices?

Kalshi lists six yes-or-no markets a day on Central Park's high (KXHIGHNY): two tails ("67 or above", "58 or
below") and four two-degree brackets ("59 to 60"). Each opens the morning before its day and closes at midnight
New York time. The model here is deliberately plain: take the latest National Blend of Models (NBS) forecast high
that had been issued (and had time to reach the archive) by the decision time, add each of the last year's
errors of the same run cycle and lead, and count how often the market would have resolved yes. No fitting, no
tuning: the only inputs are the forecast and its past misses.

At each decision time (hours from 00:00 UTC of the event date: -3, +3 and +9; +15 is reported apart) the model's
probability is set against the market's quote:

* accuracy  the Brier score (squared miss) of the model and of the quote's midpoint on the same market-times,
            their difference and its t statistic with each ISO week counted once;
* trading   at the first of those times where the model beats the ask (or the bid) by more than the taker fee
            plus a margin, buy (or sell) one contract and hold it to settlement; at most one trade a market.

Both are also given by era: before the KX tickers (to 2024-10-24), the first months of them (to 2025-06-30),
and after.

    python -m valedia_trader.research_loop.weather_markets fetch --series KXHIGHNY [--limit-markets 30]
    python -m valedia_trader.research_loop.weather_markets check
    python -m valedia_trader.research_loop.weather_markets evaluate --start 2025-07-01 --end 2026-11-10

`fetch` caches everything under artifacts/kalshi_weather/ and continues where it stopped; `check` looks at the
data only (does each market's result agree with the observed high?); `evaluate` is the test itself.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from valedia_trader.data_and_fees import weather_archive as wa
from valedia_trader.research_loop import REPO
from valedia_trader.venues import kalshi_data as kd

__all__ = ["Rule", "parse_market", "is_yes", "RunIndex", "forecast_at", "error_sample", "probability", "quote_at",
           "taker_fee", "t_by_week", "evaluate", "run_fetch", "check", "main", "ERAS", "SKIP_REASONS"]

UTC = timezone.utc
STATION, ACIS_SID, SERIES = "KNYC", "USW00094728", "KXHIGHNY"
PRIMARY_HOURS, REPORT_ONLY_HOURS = (-3, 3, 9), (15,)
ERAS = (("before_2024-10-25", None, date(2024, 10, 24)), ("2024-10-25_to_2025-06-30", date(2024, 10, 25), date(2025, 6, 30)),
        ("from_2025-07-01", date(2025, 7, 1), None))
SKIP_REASONS = ("no_forecast", "too_little_error_history", "no_quote", "wide_spread")
STALE_SECONDS = 3 * 3600
MONTHS = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
_TICKER = re.compile(r"^[A-Z]+-(\d{2})([A-Z]{3})(\d{2})-([TB])(-?\d+(?:\.\d+)?)$")
_NUM = r"(-?\d+(?:\.\d+)?)"


# ---------------------------------------------------------------- markets

@dataclass(frozen=True)
class Rule:
    """When a market resolves yes on the day's high h: greater -> h > floor; less -> h < cap; between -> floor <= h <= cap."""
    kind: str
    floor: int | None
    cap: int | None


def _whole(v) -> int | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else None


def _event_date(ticker: str) -> date | None:
    m = _TICKER.match(ticker or "")
    if not m or m.group(2) not in MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


def _rule_from_words(sub: str) -> Rule | None:
    s = (sub or "").replace("°", "").replace("–", "-").strip().lower()
    m = re.fullmatch(_NUM + r"\s*(?:or higher|or above|or more)", s)
    if m:                                                # "87 or higher", "44.0 or higher": h >= x
        return Rule("greater", math.ceil(float(m.group(1))) - 1, None)
    m = re.fullmatch(_NUM + r"\s*(?:or lower|or below|or less)", s)
    if m:                                                # "58 or below": h <= x
        return Rule("less", None, math.floor(float(m.group(1))) + 1)
    m = re.fullmatch(r"<\s*" + _NUM, s)
    if m and _whole(m.group(1)) is not None:            # "<94": h < 94
        return Rule("less", None, _whole(m.group(1)))
    m = re.fullmatch(r">\s*" + _NUM, s)
    if m and _whole(m.group(1)) is not None:            # ">94": h > 94
        return Rule("greater", _whole(m.group(1)), None)
    m = re.fullmatch(_NUM + r"\s*(?:to|-)\s*" + _NUM, s) or re.fullmatch(r"(-?\d+)()", s)
    if m:                                                # "74 to 75", and a bare "96" (subtitle "96-96") = 96 to 96
        lo, hi = _whole(m.group(1)), _whole(m.group(2) or m.group(1))
        return Rule("between", lo, hi) if lo is not None and hi is not None and lo <= hi else None
    return None


def parse_market(row: dict) -> tuple[date, Rule] | None:
    """The event date (from the ticker) and the rule: from the strike fields when there are any, otherwise (the old
    HIGHNY- markets) from the subtitle, and for a bracket with no subtitle from the ticker's B-number. None if unreadable."""
    ticker = row.get("ticker") or ""
    day = _event_date(ticker)
    if day is None:
        return None
    kind, floor, cap = row.get("strike_type"), _whole(row.get("floor_strike")), _whole(row.get("cap_strike"))
    if kind == "greater" and floor is not None:
        return day, Rule("greater", floor, None)
    if kind == "less" and cap is not None:
        return day, Rule("less", None, cap)
    if kind == "between" and floor is not None and cap is not None:
        return day, Rule("between", floor, cap)
    rule = _rule_from_words(row.get("yes_sub_title") or "")
    if rule is None:
        m = _TICKER.match(ticker)
        if m.group(4) == "B" and not float(m.group(5)).is_integer():  # B74.5 -> 74 to 75
            x = float(m.group(5))
            rule = Rule("between", math.floor(x), math.ceil(x))
    return (day, rule) if rule is not None else None


def is_yes(rule: Rule, high: float) -> bool:
    if rule.kind == "greater":
        return high > rule.floor
    if rule.kind == "less":
        return high < rule.cap
    return rule.floor <= high <= rule.cap


# ---------------------------------------------------------------- forecasts and their errors

def _time(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, UTC)
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _cycle(hour: int) -> int:
    """The run cycle: 01/07/13/19 UTC (to May 2026) and 00/06/12/18 UTC (after) are the same four cycles."""
    return hour - hour % 6


class RunIndex:
    """Forecast highs by event date: for each date, the runs (issue time, value) that forecast it, oldest first."""

    def __init__(self, runs: Iterable[dict]):
        by: dict[date, dict[datetime, float]] = {}
        for r in runs:
            if r.get("txn") is None:
                continue
            ft = _time(r["ftime"])
            if ft.hour or ft.minute:                     # only rows valid at 00Z carry the previous day's high
                continue
            by.setdefault((ft - timedelta(days=1)).date(), {})[_time(r["runtime"])] = float(r["txn"])
        self.highs = {d: sorted(v.items()) for d, v in by.items()}
        self._errors: tuple[int, dict] | None = None

    def latest(self, event_date: date, cutoff: datetime) -> tuple[float, datetime] | None:
        runs = self.highs.get(event_date, [])
        i = bisect.bisect_right([t for t, _ in runs], cutoff)
        return (runs[i - 1][1], runs[i - 1][0]) if i else None

    def errors(self, observed: dict) -> dict[tuple[int, int], tuple[list[date], list[float]]]:
        """(cycle, days from run date to event date) -> event dates (oldest first) and observed minus forecast."""
        if self._errors is not None and self._errors[0] == id(observed):
            return self._errors[1]
        obs = {_as_date(k): v for k, v in observed.items()}
        table: dict[tuple[int, int], dict[date, tuple[datetime, float]]] = {}
        for d, runs in self.highs.items():
            if d not in obs:
                continue
            for t, v in runs:                            # later runs of the same cycle and lead replace earlier ones
                table.setdefault((_cycle(t.hour), (d - t.date()).days), {})[d] = (t, float(obs[d]) - v)
        out = {}
        for key, per in table.items():
            days = sorted(per)
            out[key] = (days, [per[d][1] for d in days])
        self._errors = (id(observed), out)
        return out


def _as_date(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _index(runs) -> RunIndex:
    return runs if isinstance(runs, RunIndex) else RunIndex(runs)


def forecast_at(runs, event_date, decision_time, lag_hours: float = 2) -> tuple[float, datetime] | None:
    """The forecast high for `event_date` from the latest run issued at or before decision_time - lag_hours that has
    one, and that run's issue time. None if no such run."""
    return _index(runs).latest(_as_date(event_date), _time(decision_time) - timedelta(hours=lag_hours))


def error_sample(runs, observed: dict, event_date, run_hour: int, day_offset: int, window: int = 365,
                 min_count: int = 120) -> list[float] | None:
    """Past errors (observed minus forecast) of runs of the same cycle and the same days-ahead, for event dates at
    least two days before `event_date` (whose highs were known by the decision), the most recent `window` of them,
    oldest first. None if fewer than `min_count`."""
    days, errs = _index(runs).errors(observed).get((_cycle(int(run_hour)), int(day_offset)), ([], []))
    stop = bisect.bisect_right(days, _as_date(event_date) - timedelta(days=2))
    sample = errs[max(0, stop - window):stop]
    return sample if len(sample) >= min_count and sample else None


def probability(rule: Rule, forecast: float, errors: Sequence[float]) -> float:
    """The share of forecast + past error that resolves yes, smoothed: (count + 0.5) / (n + 1)."""
    yes = sum(is_yes(rule, forecast + e) for e in errors)
    return (yes + 0.5) / (len(errors) + 1)


# ---------------------------------------------------------------- prices

def quote_at(candles: Sequence[dict], time) -> tuple[float, float] | None:
    """The yes bid and ask at the close of the last 60-minute candle ending at or before `time`. None unless both sides
    are quoted (bid above 0, ask below 1) and that candle ended within the last three hours."""
    ts = _time(time).timestamp()
    best = None
    for c in candles:
        if c["end_ts"] <= ts and (best is None or c["end_ts"] > best["end_ts"]):
            best = c
    if best is None or ts - best["end_ts"] > STALE_SECONDS:
        return None
    bid, ask = (best.get("yes_bid") or {}).get("close"), (best.get("yes_ask") or {}).get("close")
    if bid is None or ask is None or not (bid > 0) or not (ask < 1):
        return None
    return float(bid), float(ask)


def taker_fee(price: float) -> float:
    """Kalshi's taker fee per contract, 0.07 x price x (1 - price), before its rounding up to the cent per order."""
    return 0.07 * price * (1.0 - price)


# ---------------------------------------------------------------- statistics

def _week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def t_by_week(dates: Sequence[date], values: Sequence[float]) -> tuple[float | None, int]:
    """The t statistic of the values with each ISO week counted once (its mean), and how many weeks. As in the
    tournament: 0.0 when the weekly means do not vary (or there is one week); None when there is nothing."""
    by: dict[tuple[int, int], list[float]] = {}
    for d, v in zip(dates, values):
        by.setdefault(_week(_as_date(d)), []).append(float(v))
    if not by:
        return None, 0
    weekly = np.array([np.mean(v) for v in by.values()])
    spread = float(weekly.std(ddof=1)) if len(weekly) > 1 else 0.0
    return (float(weekly.mean() / (spread / math.sqrt(len(weekly)))) if spread > 0 else 0.0), len(weekly)


def _accuracy_block(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "weeks": 0, "model_brier": None, "market_brier": None, "model_minus_market": None, "t_by_week": None}
    model = np.array([(r["probability"] - r["outcome"]) ** 2 for r in rows])
    market = np.array([(r["mid"] - r["outcome"]) ** 2 for r in rows])
    t, weeks = t_by_week([r["event_date"] for r in rows], model - market)
    return {"n": len(rows), "weeks": weeks, "model_brier": float(model.mean()), "market_brier": float(market.mean()),
            "model_minus_market": float(model.mean() - market.mean()), "t_by_week": t}


def _accuracy(rows: list[dict], hours: Sequence[int], primary: Sequence[int]) -> dict:
    return {"by_hour": {str(h): _accuracy_block([r for r in rows if r["hour"] == h]) for h in hours},
            "pooled_primary": _accuracy_block([r for r in rows if r["hour"] in primary])}


def _trading(trades: list[dict], with_list: bool = False) -> dict:
    profit = [t["profit"] for t in trades]
    t, weeks = t_by_week([x["event_date"] for x in trades], profit)
    out = {"trades": len(trades), "buys": sum(x["side"] == "buy" for x in trades), "sells": sum(x["side"] == "sell" for x in trades),
           "mean_profit": float(np.mean(profit)) if trades else None, "total_profit": float(np.sum(profit)) if trades else 0.0,
           "win_share": float(np.mean([p > 0 for p in profit])) if trades else None, "t_by_week": t, "weeks": weeks}
    if with_list:
        out["trades_list"] = [dict(x, event_date=x["event_date"].isoformat()) for x in trades]
    return out


def _era(d: date) -> str:
    for name, lo, hi in ERAS:
        if (lo is None or d >= lo) and (hi is None or d <= hi):
            return name
    raise AssertionError(d)


# ---------------------------------------------------------------- the evaluation

def evaluate(markets: Sequence[dict], candles: dict[str, list[dict]], runs, observed: dict, start, end,
             decision_hours: Sequence[int] = PRIMARY_HOURS, margin: float = 0.05, max_spread: float = 0.10, *,
             report_only_hours: Sequence[int] = REPORT_ONLY_HOURS, lag_hours: float = 2, window: int = 365,
             min_count: int = 120) -> dict:
    """The test. Every market with its event date in [start, end], at every decision hour (primary, then the
    report-only ones): model probability, quote and outcome (the market's own result). A market-time is skipped,
    for the first reason that applies, when there is no forecast, too little error history, no usable quote, or a
    spread wider than max_spread. Trades happen only at the primary hours, the first that clears the margin, one per
    market, one contract, held to settlement."""
    start, end = _as_date(start), _as_date(end)
    primary = sorted(int(h) for h in decision_hours)
    hours = primary + [int(h) for h in report_only_hours if int(h) not in primary]
    index = _index(runs)
    skipped = {str(h): {k: 0 for k in SKIP_REASONS} for h in hours}
    rows, trades, unparsed, in_period, no_result = [], [], 0, 0, 0
    for m in markets:
        parsed = parse_market(m)
        if parsed is None:
            unparsed += 1
            continue
        day, rule = parsed
        if not (start <= day <= end):
            continue
        in_period += 1
        if m.get("result") not in ("yes", "no"):
            no_result += 1
            continue
        outcome = 1 if m["result"] == "yes" else 0
        traded = False
        for h in hours:
            t = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=h)
            fc = forecast_at(index, day, t, lag_hours)
            if fc is None:
                skipped[str(h)]["no_forecast"] += 1
                continue
            value, issued = fc
            errs = error_sample(index, observed, day, issued.hour, (day - issued.date()).days, window, min_count)
            if errs is None:
                skipped[str(h)]["too_little_error_history"] += 1
                continue
            q = quote_at(candles.get(m["ticker"], []), t)
            if q is None:
                skipped[str(h)]["no_quote"] += 1
                continue
            bid, ask = q
            if ask - bid > max_spread + 1e-12:
                skipped[str(h)]["wide_spread"] += 1
                continue
            p = probability(rule, value, errs)
            rows.append({"ticker": m["ticker"], "event_date": day, "hour": h, "probability": p, "mid": (bid + ask) / 2,
                         "outcome": outcome})
            if traded or h not in primary:
                continue
            side = price = None
            if p - ask - taker_fee(ask) > margin:
                side, price = "buy", ask
            elif bid - p - taker_fee(bid) > margin:
                side, price = "sell", bid
            if side:
                fee = taker_fee(price)
                profit = (outcome - price - fee) if side == "buy" else (price - outcome - fee)
                trades.append({"ticker": m["ticker"], "event_date": day, "hour": h, "side": side, "price": price,
                               "fee": fee, "probability": p, "payoff": outcome, "profit": profit})
                traded = True
    trades.sort(key=lambda x: (x["event_date"], x["ticker"]))
    total = {k: sum(s[k] for s in skipped.values()) for k in SKIP_REASONS}
    eras = {name: {"accuracy": _accuracy([r for r in rows if _era(r["event_date"]) == name], hours, primary),
                   "trading": _trading([x for x in trades if _era(x["event_date"]) == name])} for name, _, _ in ERAS}
    return {"period": {"start": start.isoformat(), "end": end.isoformat()},
            "settings": {"decision_hours": primary, "report_only_hours": [h for h in hours if h not in primary],
                         "margin": margin, "max_spread": max_spread, "lag_hours": lag_hours, "window": window,
                         "min_count": min_count, "fee": "0.07 x p x (1 - p) per contract"},
            "markets_in_period": in_period, "unparsed_markets": unparsed, "no_result": no_result,
            "scored_market_times": len(rows), "accuracy": _accuracy(rows, hours, primary),
            "trading": _trading(trades, with_list=True), "eras": eras, "skipped": {"total": total, "by_hour": skipped}}


# ---------------------------------------------------------------- files

def _base(root: Path) -> Path:
    return Path(root) / "artifacts" / "kalshi_weather"


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)


def _load(root: Path) -> tuple[list[dict], dict[str, list[dict]], list[dict], dict]:
    base = _base(root)
    markets = json.loads((base / "markets.json").read_text())["markets"] if (base / "markets.json").exists() else []
    candles = {}
    if (base / "candles").exists():
        for f in (base / "candles").glob("*.json"):
            d = json.loads(f.read_text())
            candles[d["ticker"]] = d["candles"]
    runs = wa.cached_runs(base / "nbs", STATION) if (base / "nbs").exists() else []
    observed = json.loads((base / "observed.json").read_text())["highs"] if (base / "observed.json").exists() else {}
    return markets, candles, runs, observed


def _seconds(v) -> int:
    return int(_time(v).timestamp())


def run_fetch(root: Path, series: str = SERIES, limit_markets: int | None = None, history_days: int = 400, *,
              client: kd.KalshiData | None = None, weather_fetch: Callable[[str], bytes] = kd.urllib_get,
              sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic,
              refresh_markets: bool = False, progress: Callable[[str], Any] = print) -> dict:
    """Download and cache: the series' settled markets (once, unless refresh_markets), 60-minute candles over each
    selected market's life (skipping those already cached), the NBS runs from `history_days` before the first selected
    event date to the last, and the observed highs over the same span to today. `limit_markets` selects the most
    recently closed ones."""
    base = _base(root)
    client = client or kd.KalshiData()
    mfile = base / "markets.json"
    if mfile.exists() and not refresh_markets:
        markets = json.loads(mfile.read_text())["markets"]
    else:
        progress("settled markets of %s, both tiers" % series)
        markets = client.settled_markets(series)
        _write(mfile, {"series": series, "fetched_at": datetime.now(UTC).isoformat(), "markets": markets})
    chosen = sorted(markets, key=lambda m: (m.get("close_time") or "", m["ticker"]), reverse=True)
    if limit_markets is not None:
        chosen = chosen[:limit_markets]
    fetched = cached = 0
    for i, m in enumerate(chosen):
        f = base / "candles" / ("%s.json" % m["ticker"])
        if f.exists():
            cached += 1
            continue
        got = client.candles(m["ticker"], _seconds(m["open_time"]), _seconds(m["close_time"]), tier_hint=m.get("tier"), series=series)
        _write(f, {"ticker": m["ticker"], "tier_hint": m.get("tier"), "start": m["open_time"], "end": m["close_time"], "candles": got})
        fetched += 1
        if fetched % 10 == 0 or i == len(chosen) - 1:
            progress("candles: %d of %d markets done (%d fetched now, %d cached before)" % (i + 1, len(chosen), fetched, cached))
    days = sorted(d for d in (_event_date(m["ticker"]) for m in chosen) if d is not None)
    out = {"markets": len(markets), "selected": len(chosen), "candles_fetched": fetched, "candles_cached": cached}
    if not days:
        return out
    first = days[0] - timedelta(days=history_days + 2)
    runs = wa.nbs_runs(STATION, first, days[-1], fetch=weather_fetch, cache_dir=base / "nbs", sleep=sleep, clock=clock, progress=progress)
    today = datetime.now(UTC).date()
    progress("observed highs %s .. %s" % (first, today))
    highs = wa.observed_highs(ACIS_SID, first, today, fetch=weather_fetch)
    _write(base / "observed.json", {"station": ACIS_SID, "start": first.isoformat(), "end": today.isoformat(), "highs": highs})
    out.update({"forecast_rows": len(runs), "forecast_span": [first.isoformat(), days[-1].isoformat()], "observed_days": len(highs)})
    return out


def check(root: Path, decision_hour: int = 9, lag_hours: float = 2) -> dict:
    """Data consistency only: candle coverage, whether each market's result agrees with is_yes on the observed high,
    unreadable markets, and how many event dates have a forecast by `decision_hour`. No prices against outcomes."""
    markets, candles, runs, observed = _load(root)
    obs = {_as_date(k): v for k, v in observed.items()}
    index = RunIndex(runs)
    unparsed, agree, disagree, with_obs, no_result, dates = [], 0, [], 0, 0, set()
    for m in markets:
        parsed = parse_market(m)
        if parsed is None:
            unparsed.append(m["ticker"])
            continue
        day, rule = parsed
        dates.add(day)
        if day not in obs:
            continue
        if m.get("result") not in ("yes", "no"):
            no_result += 1
            continue
        with_obs += 1
        if is_yes(rule, obs[day]) == (m["result"] == "yes"):
            agree += 1
        else:
            disagree.append(m["ticker"])
    with_c = [m["ticker"] for m in markets if candles.get(m["ticker"])]
    fetched_dates = {d for d in (_event_date(t) for t in candles) if d is not None}
    with_fc = sum(index.latest(d, datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(hours=decision_hour - lag_hours)) is not None
                  for d in fetched_dates)
    return {"markets": len(markets), "candle_files": len(candles), "with_candles": len(with_c),
            "empty_candle_files": sum(1 for v in candles.values() if not v), "unparsed": unparsed,
            "with_observed_high": with_obs, "result_agrees": agree, "result_disagrees": disagree, "no_result": no_result,
            "event_dates": len(dates), "fetched_event_dates": len(fetched_dates),
            "fetched_event_dates_with_forecast_by_hour_%d" % decision_hour: with_fc, "forecast_rows": len(runs),
            "observed_days": len(obs)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="weather_markets", description="Kalshi New York daily highs against the NBS forecast.")
    ap.add_argument("--root", default=None, help="the repository root (default: this repository)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and cache markets, candles, forecast runs and observed highs")
    f.add_argument("--series", default=SERIES)
    f.add_argument("--limit-markets", type=int, default=None, help="only the N most recently closed markets")
    f.add_argument("--history-days", type=int, default=400, help="forecast runs this many days before the first market")
    f.add_argument("--refresh-markets", action="store_true", help="ask Kalshi for the market list again")
    sub.add_parser("check", help="data consistency only")
    e = sub.add_parser("evaluate", help="the test: prints JSON")
    e.add_argument("--start", required=True)
    e.add_argument("--end", required=True)
    e.add_argument("--margin", type=float, default=0.05)
    e.add_argument("--lag-hours", type=float, default=2)
    e.add_argument("--max-spread", type=float, default=0.10)
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else REPO
    if args.cmd == "fetch":
        out = run_fetch(root, args.series, args.limit_markets, args.history_days, refresh_markets=args.refresh_markets,
                        progress=lambda s: print(s, file=sys.stderr, flush=True))
    elif args.cmd == "check":
        out = check(root)
    else:
        markets, candles, runs, observed = _load(root)
        out = evaluate(markets, candles, runs, observed, args.start, args.end, PRIMARY_HOURS, args.margin, args.max_spread,
                       lag_hours=args.lag_hours)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
