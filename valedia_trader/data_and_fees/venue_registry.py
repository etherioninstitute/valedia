"""Venue fee, schedule and market-close registry (DuckDB).

Rules this module enforces:
* A fee that is unknown or unverified blocks the trade (`UnverifiedFeeError`); it never defaults to zero.
* Fee rows are append-only and point-in-time: a lookup "as of" a past moment only sees rows that
  were both effective and known by then, so backtests cannot use fee changes from their future.
* Seeded numbers are transcribed from constants the owner already curated in the legacy modules;
  every row names its source. Venues without a sourced number are present but unverified.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import duckdb
import exchange_calendars as xcals
import pandas as pd

__all__ = ["VenueRegistry", "FeeBreakdown", "UnverifiedFeeError", "PreCloseAction", "REFRESH_PLAN",
           "SEED_FEES", "SEED_VENUES"]


class UnverifiedFeeError(LookupError):
    """No verified fee row applies. Callers must treat this as 'do not trade'."""


@dataclass(frozen=True)
class FeeBreakdown:
    commission: float      # venue commission in currency
    extra: float           # spread mark-ups, gas and other fixed costs in currency
    total: float
    bps: float             # total as basis points of notional
    source: str
    as_of: date


@dataclass(frozen=True)
class PreCloseAction:
    must_flatten: bool
    reason: str
    minutes_to_close: float | None


# venue, asset class, calendar ("24x7" or an exchange_calendars code), timezone
SEED_VENUES = (
    ("alpaca", "equities_options", "XNYS", "America/New_York"),
    ("interactive_brokers", "equities_options_futures", "XNYS", "America/New_York"),
    ("tradier", "equities_options", "XNYS", "America/New_York"),
    ("binance", "crypto", "24x7", "UTC"),
    ("coinbase", "crypto", "24x7", "UTC"),
    ("bybit", "crypto", "24x7", "UTC"),
    ("hyperliquid", "crypto_perp_dex", "24x7", "UTC"),
    ("okx", "crypto", "24x7", "UTC"),
    ("kraken", "crypto", "24x7", "UTC"),
    ("defi_uniswap", "crypto_dex", "24x7", "UTC"),
    ("polymarket", "prediction", "24x7", "UTC"),
    ("kalshi", "prediction", "24x7", "UTC"),
    ("the_odds_api", "sportsbooks", "24x7", "UTC"),
)

_KALSHI_URL = "https://kalshi.com/docs/kalshi-fee-schedule.pdf"
# venue, product, model, maker, taker, extra_bps, fixed, source, as_of, url, verified
# model "bps": maker/taker in basis points of notional. "per_contract": currency per contract.
# model "kalshi_quadratic": fee = round_up_cent(multiplier * coef * contracts * P * (1 - P)).
SEED_FEES = (
    ("kraken", "spot", "bps", 16.0, 26.0, 0.0, 0.0, "src/fee_engine.py:STATIC_FEES['kraken']", "2026-03-13", None, True),
    ("coinbase", "spot", "bps", 40.0, 60.0, 50.0, 0.0, "src/fee_engine.py:STATIC_FEES['coinbase'] (spread 0.005)",
     "2026-03-13", None, True),
    ("alpaca", "equity", "bps", 0.0, 0.0, 0.0, 0.0, "src/fee_engine.py:STATIC_FEES['alpaca']", "2026-03-13", None, True),
    ("alpaca", "option", "per_contract", 0.65, 0.65, 0.0, 0.0,
     "src/fee_engine.py:STATIC_FEES['alpaca']['commission_options']", "2026-03-13", None, True),
    ("polymarket", "event_contract", "bps", 0.0, 0.0, 0.0, 0.01,
     "src/fee_engine.py:STATIC_FEES['polymarket'] (gas 0.01)", "2026-03-13", None, True),
    ("kalshi", "event_contract", "kalshi_quadratic", 0.0175, 0.07, 0.0, 0.0,
     "src/kalshi_fee_map.py:MAKER_COEF/TAKER_COEF", "2026-07-07", _KALSHI_URL, True),
    ("defi_uniswap", "swap", "bps", 30.0, 30.0, 0.0, 0.50,
     "src/fee_engine.py:STATIC_FEES['defi_uniswap'] (gas estimate 0.50)", "2026-03-13", None, True),
) + tuple(
    (venue, product, "bps", None, None, None, None, "unsourced: needs venue fee schedule", "1970-01-01", url, False)
    for venue, product, url in (
        ("interactive_brokers", "equity", "https://www.interactivebrokers.com/en/pricing/commissions-stocks.php"),
        ("interactive_brokers", "option", "https://www.interactivebrokers.com/en/pricing/commissions-options.php"),
        ("tradier", "equity", "https://tradier.com/individuals/pricing"),
        ("tradier", "option", "https://tradier.com/individuals/pricing"),
        ("alpaca", "equity_regulatory", "https://alpaca.markets/support/regulatory-fees"),
        ("binance", "spot", "https://www.binance.com/en/fee/schedule"),
        ("binance", "perp", "https://www.binance.com/en/fee/futureFee"),
        ("bybit", "spot", "https://www.bybit.com/en/help-center/article/Trading-Fee-Structure"),
        ("bybit", "perp", "https://www.bybit.com/en/help-center/article/Trading-Fee-Structure"),
        ("hyperliquid", "perp", "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees"),
        ("okx", "spot", "https://www.okx.com/fees"),
        ("okx", "perp", "https://www.okx.com/fees"),
        ("kraken", "perp", "https://www.kraken.com/features/fee-schedule"),
        ("the_odds_api", "sportsbook_bet", "https://the-odds-api.com/liveapi/guides/v4/"),
    )
)

# Where each venue's authoritative fees come from, for a scheduled refresher. URLs only; nothing is fetched here.
REFRESH_PLAN = {
    "kalshi": {"kind": "api", "url": "https://api.elections.kalshi.com/trade-api/v2/series/fee_changes"},
    "kraken": {"kind": "api", "url": "https://api.kraken.com/0/private/TradeVolume"},
    "binance": {"kind": "api", "url": "https://api.binance.com/sapi/v1/asset/tradeFee"},
    "bybit": {"kind": "api", "url": "https://api.bybit.com/v5/account/fee-rate"},
    "okx": {"kind": "api", "url": "https://www.okx.com/api/v5/account/trade-fee"},
    "hyperliquid": {"kind": "api", "url": "https://api.hyperliquid.xyz/info (type=userFees)"},
    "coinbase": {"kind": "api", "url": "https://api.coinbase.com/api/v3/brokerage/transaction_summary"},
    "alpaca": {"kind": "page", "url": "https://alpaca.markets/support/regulatory-fees"},
    "interactive_brokers": {"kind": "page", "url": "https://www.interactivebrokers.com/en/pricing/commissions-home.php"},
    "tradier": {"kind": "page", "url": "https://tradier.com/individuals/pricing"},
    "polymarket": {"kind": "page", "url": "https://docs.polymarket.com/"},
    "the_odds_api": {"kind": "page", "url": "https://the-odds-api.com/liveapi/guides/v4/"},
}


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _naive(ts) -> datetime:
    return _utc(ts).tz_localize(None).to_pydatetime()


@lru_cache(maxsize=8)
def _calendar(code: str):
    return xcals.get_calendar(code)


class VenueRegistry:
    def __init__(self, path: str = ":memory:", seed: bool = True) -> None:
        self._db = duckdb.connect(path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS venues (venue TEXT PRIMARY KEY, asset_class TEXT, calendar TEXT, tz TEXT);
            CREATE TABLE IF NOT EXISTS fees (venue TEXT, product TEXT, model TEXT, maker DOUBLE, taker DOUBLE,
                extra_bps DOUBLE, fixed DOUBLE, multiplier DOUBLE, effective_from TIMESTAMP, known_at TIMESTAMP,
                source TEXT, source_url TEXT, as_of DATE, verified BOOLEAN);
            CREATE TABLE IF NOT EXISTS funding (venue TEXT, symbol TEXT, interval_hours DOUBLE, rate DOUBLE,
                observed_at TIMESTAMP, source TEXT);
            CREATE TABLE IF NOT EXISTS maintenance (venue TEXT, start_at TIMESTAMP, end_at TIMESTAMP, reason TEXT);
        """)
        if seed and self._db.execute("SELECT COUNT(*) FROM venues").fetchone()[0] == 0:
            self._db.executemany("INSERT INTO venues VALUES (?, ?, ?, ?)", SEED_VENUES)
            for venue, product, model, maker, taker, extra, fixed, source, as_of, url, verified in SEED_FEES:
                stamp = datetime.fromisoformat(as_of)
                self._db.execute("INSERT INTO fees VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (venue, product, model, maker, taker, extra, fixed, 1.0, stamp, stamp, source, url,
                                  date.fromisoformat(as_of), verified))

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ venues
    def venues(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT venue FROM venues ORDER BY venue").fetchall()]

    def _venue(self, venue: str) -> tuple[str, str]:
        row = self._db.execute("SELECT calendar, tz FROM venues WHERE venue = ?", (venue,)).fetchone()
        if row is None:
            raise KeyError(f"unknown venue '{venue}'")
        return row

    # ------------------------------------------------------------------ fees
    def upsert_fee(self, venue: str, product: str, model: str, maker: float | None, taker: float | None, *,
                   effective_from, source: str, as_of, extra_bps: float | None = 0.0, fixed: float | None = 0.0,
                   multiplier: float = 1.0, source_url: str | None = None, verified: bool = False,
                   known_at=None) -> None:
        """Append a fee row. History is never rewritten; the newest applicable row wins a lookup."""
        self._venue(venue)
        if model not in ("bps", "per_contract", "kalshi_quadratic"):
            raise ValueError("model must be 'bps', 'per_contract' or 'kalshi_quadratic'")
        if not source or not source.strip():
            raise ValueError("every fee row needs a source")
        if verified and any(v is None or not math.isfinite(v) for v in (maker, taker)):
            raise ValueError("a verified row needs finite maker and taker values")
        known = _naive(known_at) if known_at is not None else datetime.now(timezone.utc).replace(tzinfo=None)
        self._db.execute("INSERT INTO fees VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (venue, product, model, maker, taker, extra_bps, fixed, multiplier, _naive(effective_from),
                          known, source, source_url, pd.Timestamp(as_of).date(), verified))

    def _fee_row(self, venue: str, product: str, as_of) -> tuple:
        self._venue(venue)
        ts = datetime.now(timezone.utc).replace(tzinfo=None) if as_of is None else _naive(as_of)
        row = self._db.execute(
            "SELECT model, maker, taker, extra_bps, fixed, multiplier, source, as_of, verified FROM fees "
            "WHERE venue = ? AND product = ? AND effective_from <= ? AND known_at <= ? "
            "ORDER BY effective_from DESC, known_at DESC LIMIT 1", (venue, product, ts, ts)).fetchone()
        if row is None:
            raise UnverifiedFeeError(f"no fee row for {venue}/{product} as of {ts:%Y-%m-%d}")
        if not row[8] or any(v is None for v in row[1:5]):
            raise UnverifiedFeeError(f"fee for {venue}/{product} is unverified ({row[6]})")
        return row

    def fee_for(self, venue: str, product: str, liquidity: str, *, notional: float | None = None,
                quantity: float | None = None, price: float | None = None, as_of=None) -> FeeBreakdown:
        """Fee for one fill. `liquidity` is 'maker' or 'taker'. bps models need `notional`; per_contract
        needs `quantity`; kalshi_quadratic needs `quantity` (contracts) and `price` in (0, 1)."""
        if liquidity not in ("maker", "taker"):
            raise ValueError("liquidity must be 'maker' or 'taker'")
        model, maker, taker, extra_bps, fixed, mult, source, row_as_of, _ = self._fee_row(venue, product, as_of)
        rate = maker if liquidity == "maker" else taker
        if model == "bps":
            if notional is None or not math.isfinite(notional) or notional < 0:
                raise ValueError("a bps fee needs a non-negative notional")
            commission, extra = notional * rate * 1e-4, notional * extra_bps * 1e-4 + fixed
        elif model == "per_contract":
            if quantity is None or quantity < 0:
                raise ValueError("a per-contract fee needs a non-negative quantity")
            commission, extra = quantity * rate, fixed
            notional = notional if notional is not None else float("nan")
        else:
            if quantity is None or quantity < 0 or price is None or not 0.0 < price < 1.0:
                raise ValueError("the Kalshi fee needs contracts and a price strictly between 0 and 1")
            raw = mult * rate * quantity * price * (1.0 - price)
            commission, extra = math.ceil(raw * 100.0 - 1e-9) / 100.0 if raw > 0 else 0.0, fixed
            notional = quantity * price
        total = commission + extra
        bps = total / notional * 1e4 if notional and math.isfinite(notional) and notional > 0 else float("nan")
        return FeeBreakdown(float(commission), float(extra), float(total), float(bps), source, row_as_of)

    def round_trip_cost_bps(self, venue: str, product: str, notional: float, *, liquidity_in: str = "taker",
                            liquidity_out: str = "taker", quantity: float | None = None,
                            price: float | None = None, as_of=None) -> float:
        """Entry plus exit fees as basis points of notional."""
        a = self.fee_for(venue, product, liquidity_in, notional=notional, quantity=quantity, price=price, as_of=as_of)
        b = self.fee_for(venue, product, liquidity_out, notional=notional, quantity=quantity, price=price, as_of=as_of)
        base = notional if notional else (quantity or 0.0) * (price or 0.0)
        if base <= 0:
            raise ValueError("round trip needs a positive notional")
        return (a.total + b.total) / base * 1e4

    def stale_fees(self, max_age_days: int, today=None) -> pd.DataFrame:
        """Verified fee rows whose source date is older than `max_age_days` (candidates for refresh)."""
        today = pd.Timestamp(today if today is not None else datetime.now(timezone.utc).date()).date()
        df = self._db.execute(
            "SELECT venue, product, source, as_of FROM fees f WHERE verified AND known_at = (SELECT MAX(known_at) "
            "FROM fees g WHERE g.venue = f.venue AND g.product = f.product) ORDER BY venue, product").df()
        df["age_days"] = [(today - pd.Timestamp(d).date()).days for d in df["as_of"]]
        return df[df["age_days"] > max_age_days].reset_index(drop=True)

    def unverified(self) -> list[tuple[str, str]]:
        """(venue, product) pairs whose latest row is unverified: lookups for these fail closed."""
        rows = self._db.execute(
            "SELECT venue, product FROM fees f WHERE known_at = (SELECT MAX(known_at) FROM fees g WHERE "
            "g.venue = f.venue AND g.product = f.product) AND NOT verified ORDER BY venue, product").fetchall()
        return [(v, p) for v, p in rows]

    # ------------------------------------------------------------------ funding
    def record_funding(self, venue: str, symbol: str, interval_hours: float, rate: float, observed_at,
                       source: str) -> None:
        self._venue(venue)
        if interval_hours <= 0 or not math.isfinite(rate) or not source:
            raise ValueError("funding needs a positive interval, a finite rate and a source")
        self._db.execute("INSERT INTO funding VALUES (?,?,?,?,?,?)",
                         (venue, symbol, interval_hours, rate, _naive(observed_at), source))

    def funding_cost(self, venue: str, symbol: str, notional: float, hours: float, as_of=None) -> float:
        """Funding paid by a long of `notional` held `hours`, using the last rate observed at or before
        `as_of` (negative = received). Raises if no rate is known: funding is never assumed to be zero."""
        ts = datetime.now(timezone.utc).replace(tzinfo=None) if as_of is None else _naive(as_of)
        row = self._db.execute("SELECT interval_hours, rate FROM funding WHERE venue = ? AND symbol = ? AND "
                               "observed_at <= ? ORDER BY observed_at DESC LIMIT 1", (venue, symbol, ts)).fetchone()
        if row is None:
            raise UnverifiedFeeError(f"no funding rate known for {venue}/{symbol} as of {ts:%Y-%m-%d %H:%M}")
        return notional * row[1] * hours / row[0]

    # ------------------------------------------------------------------ sessions
    def add_maintenance(self, venue: str, start_at, end_at, reason: str) -> None:
        self._venue(venue)
        if _utc(end_at) <= _utc(start_at):
            raise ValueError("maintenance window must end after it starts")
        self._db.execute("INSERT INTO maintenance VALUES (?,?,?,?)", (venue, _naive(start_at), _naive(end_at), reason))

    def _maintenance(self, venue: str, ts: pd.Timestamp, upcoming: bool) -> tuple | None:
        t = _naive(ts)
        if upcoming:
            return self._db.execute("SELECT start_at, end_at, reason FROM maintenance WHERE venue = ? AND "
                                    "end_at > ? ORDER BY start_at LIMIT 1", (venue, t)).fetchone()
        return self._db.execute("SELECT start_at, end_at, reason FROM maintenance WHERE venue = ? AND "
                                "start_at <= ? AND end_at > ? LIMIT 1", (venue, t, t)).fetchone()

    def is_open(self, venue: str, ts) -> bool:
        """True if the venue is in its regular session at `ts` (and not in a maintenance window)."""
        cal, _ = self._venue(venue)
        t = _utc(ts)
        if self._maintenance(venue, t, upcoming=False) is not None:
            return False
        return True if cal == "24x7" else bool(_calendar(cal).is_open_on_minute(t.floor("min")))

    def next_close(self, venue: str, ts) -> pd.Timestamp | None:
        """Next moment trading stops: the session close (holidays and early closes included) or the
        start of the next maintenance window. None for a 24x7 venue with nothing scheduled."""
        cal, _ = self._venue(venue)
        t = _utc(ts)
        nxt = self._maintenance(venue, t, upcoming=True)
        maint = pd.Timestamp(nxt[0], tz="UTC") if nxt is not None and pd.Timestamp(nxt[0], tz="UTC") > t else None
        if cal == "24x7":
            return maint
        close = _calendar(cal).next_close(t.floor("min"))
        return min(close, maint) if maint is not None else close

    def time_to_close(self, venue: str, ts) -> timedelta | None:
        nxt = self.next_close(venue, ts)
        return None if nxt is None else (nxt - _utc(ts)).to_pytimedelta()

    def pre_close_action(self, venue: str, ts, flatten_minutes_before_close: float = 15.0,
                         market_close=None) -> PreCloseAction:
        """Should positions be flattened or hedged now so nothing is trapped over a close, a settlement
        halt or maintenance? `market_close` supplies a per-market deadline (event contract, game start)."""
        if flatten_minutes_before_close < 0:
            raise ValueError("flatten window must be non-negative")
        t = _utc(ts)
        deadlines = [d for d in (self.next_close(venue, t), None if market_close is None else _utc(market_close))
                     if d is not None]
        if not self.is_open(venue, t):
            return PreCloseAction(False, "venue closed: nothing can be traded now", None)
        if not deadlines:
            return PreCloseAction(False, "no close scheduled", None)
        minutes = (min(deadlines) - t).total_seconds() / 60.0
        if minutes <= flatten_minutes_before_close:
            return PreCloseAction(True, f"{minutes:.1f} min to close (window {flatten_minutes_before_close:g})",
                                  minutes)
        return PreCloseAction(False, "outside the pre-close window", minutes)
