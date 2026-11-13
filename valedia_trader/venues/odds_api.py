"""the-odds-api.com v4 client and sports-odds mathematics: conversions, devigging (multiplicative,
power, Shin), sharp-book fair probabilities, +EV and arbitrage detection, middles and Kelly staking.

The API key is read from the ODDS_API_KEY environment variable at call time and never appears in
exceptions, reprs or logs. The historical endpoints take the key the caller names: "paid" (ODDS_API_KEY) or
"free" (ODDS_API_KEY_FREE), read at call time through the project's secrets loader (`read_key`); historical
odds are refused with the free key before anything is sent. After every answer the three usage headers
(x-requests-remaining, x-requests-used, x-requests-last) are kept in `last_usage`."""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import optimize

__all__ = [
    "american_to_decimal", "decimal_to_american", "fractional_to_decimal", "implied_probability",
    "devig_multiplicative", "devig_power", "devig_shin", "devig", "fair_probabilities", "expected_value",
    "find_positive_ev", "find_arbitrage", "ArbResult", "find_middle", "Middle", "kelly_stakes",
    "OddsApiClient", "OddsApiError", "normalize_odds", "DEFAULT_SHARP_BOOKS", "KEY_ENV", "read_key",
    "historical_cost",
]

BASE_URL = "https://api.the-odds-api.com"
DEFAULT_SHARP_BOOKS = ("pinnacle", "circasports", "betfair_ex_eu", "betfair_ex_uk")
KELLY_MULTIPLIER_MIN, KELLY_MULTIPLIER_MAX = 0.15, 0.50
KEY_ENV = {"paid": "ODDS_API_KEY", "free": "ODDS_API_KEY_FREE"}
USAGE_HEADERS = {"remaining": "x-requests-remaining", "used": "x-requests-used", "last": "x-requests-last"}


# ---------------------------------------------------------------- conversions
def american_to_decimal(american):
    """American odds to decimal: +150 -> 2.50, -110 -> 1.9091."""
    a = np.asarray(american, dtype=float)
    if np.any(np.abs(a) < 100) or not np.all(np.isfinite(a)):
        raise ValueError("American odds must be finite with absolute value >= 100")
    d = np.where(a > 0, 1.0 + a / 100.0, 1.0 + 100.0 / np.abs(a))
    return float(d) if d.ndim == 0 else d


def decimal_to_american(decimal):
    """Decimal odds to American: 2.50 -> +150, 1.9091 -> -110."""
    d = _decimals(decimal)
    a = np.where(d >= 2.0, (d - 1.0) * 100.0, -100.0 / (d - 1.0))
    return float(a) if a.ndim == 0 else a


def fractional_to_decimal(numerator, denominator):
    """Fractional odds n/d to decimal: 5/2 -> 3.50."""
    n, d = np.asarray(numerator, dtype=float), np.asarray(denominator, dtype=float)
    if np.any(n <= 0) or np.any(d <= 0):
        raise ValueError("fractional odds need positive numerator and denominator")
    out = 1.0 + n / d
    return float(out) if out.ndim == 0 else out


def _decimals(decimal) -> np.ndarray:
    d = np.asarray(decimal, dtype=float)
    if np.any(d <= 1.0) or not np.all(np.isfinite(d)):
        raise ValueError("decimal odds must be finite and greater than 1")
    return d


def implied_probability(decimal):
    """Raw implied probability 1 / decimal odds (includes the bookmaker margin)."""
    p = 1.0 / _decimals(decimal)
    return float(p) if p.ndim == 0 else p


# ---------------------------------------------------------------- devigging
def _market(decimal_odds) -> np.ndarray:
    d = _decimals(decimal_odds).ravel()
    if d.size < 2:
        raise ValueError("a market needs at least two outcomes")
    return 1.0 / d


def devig_multiplicative(decimal_odds) -> np.ndarray:
    """Multiplicative devig: p_i = pi_i / sum(pi), with pi_i = 1 / odds_i."""
    pi = _market(decimal_odds)
    return pi / pi.sum()


def devig_power(decimal_odds) -> np.ndarray:
    """Power devig: p_i = pi_i^k with k chosen so the probabilities sum to 1."""
    pi = _market(decimal_odds)
    if abs(pi.sum() - 1.0) < 1e-12:
        return pi
    k = optimize.brentq(lambda e: float(np.sum(pi**e)) - 1.0, 1e-3, 100.0, xtol=1e-14)
    p = pi**k
    return p / p.sum()


def devig_shin(decimal_odds) -> np.ndarray:
    """Shin (1993) devig: p_i = (sqrt(z^2 + 4 (1 - z) pi_i^2 / B) - z) / (2 (1 - z)), B = sum(pi), with
    the insider-trading share z solving sum(p) = 1. Longshots are shrunk more than favourites."""
    pi = _market(decimal_odds)
    book = pi.sum()
    if book <= 1.0 + 1e-12:
        return pi / book

    def probs(z: float) -> np.ndarray:
        return (np.sqrt(z**2 + 4.0 * (1.0 - z) * pi**2 / book) - z) / (2.0 * (1.0 - z))

    z = optimize.brentq(lambda v: float(probs(v).sum()) - 1.0, 0.0, 0.999, xtol=1e-14)
    p = probs(z)
    return p / p.sum()


_DEVIG = {"multiplicative": devig_multiplicative, "power": devig_power, "shin": devig_shin}


def devig(decimal_odds, method: str = "shin") -> np.ndarray:
    """Remove the bookmaker margin with the named method; the result always sums to 1."""
    if method not in _DEVIG:
        raise ValueError(f"method must be one of {sorted(_DEVIG)}")
    return _DEVIG[method](decimal_odds)


def fair_probabilities(book_odds: Mapping[str, Sequence[float]], sharp_books: Sequence[str] = DEFAULT_SHARP_BOOKS,
                       weights: Mapping[str, float] | None = None, method: str = "shin") -> np.ndarray:
    """Consensus fair probabilities from sharp books: a weighted average of each sharp book's devigged
    probabilities. Raises if no sharp book quotes the market: fair value is never guessed from soft books."""
    sharp = [b for b in sharp_books if b in book_odds]
    if not sharp:
        raise LookupError("no sharp book quotes this market; cannot establish a fair price")
    w = np.array([1.0 if weights is None else float(weights.get(b, 0.0)) for b in sharp])
    if np.any(w < 0) or w.sum() <= 0:
        raise ValueError("sharp-book weights must be non-negative with a positive total")
    probs = np.array([devig(book_odds[b], method) for b in sharp])
    out = (w[:, None] * probs).sum(axis=0) / w.sum()
    return out / out.sum()


# ---------------------------------------------------------------- value, arbitrage, middles
def expected_value(fair_probability, decimal_odds):
    """Expected profit per unit staked: EV = p * odds - 1."""
    p = np.asarray(fair_probability, dtype=float)
    if np.any((p < 0) | (p > 1)):
        raise ValueError("probabilities must be in [0, 1]")
    ev = p * _decimals(decimal_odds) - 1.0
    return float(ev) if ev.ndim == 0 else ev


def find_positive_ev(quotes: pd.DataFrame, fair: Mapping[str, float], min_edge: float = 0.02) -> pd.DataFrame:
    """+EV scanner. `quotes` has columns book, outcome, decimal_odds; `fair` maps outcome -> fair
    probability. Returns quotes whose EV per unit is at least `min_edge`, best first."""
    for col in ("book", "outcome", "decimal_odds"):
        if col not in quotes.columns:
            raise ValueError("quotes need columns book, outcome, decimal_odds")
    df = quotes[quotes["outcome"].isin(list(fair))].copy()
    df["fair_probability"] = df["outcome"].map(fair).astype(float)
    df["ev"] = df["fair_probability"] * df["decimal_odds"].astype(float) - 1.0
    return df[df["ev"] >= min_edge].sort_values("ev", ascending=False).reset_index(drop=True)


@dataclass(frozen=True)
class ArbResult:
    is_arbitrage: bool
    margin: float             # 1 - sum(1 / effective odds); positive means a locked profit
    stakes: np.ndarray        # fraction of total outlay on each outcome
    guaranteed_return: float  # profit per unit of total outlay (negative if not an arbitrage)


def find_arbitrage(best_decimal_odds, commissions=0.0) -> ArbResult:
    """Cross-book arbitrage for an n-way market given the best price per outcome. `commissions` is the
    fraction of net winnings each book keeps (e.g. an exchange's 2%)."""
    d = _decimals(best_decimal_odds).ravel()
    c = np.broadcast_to(np.asarray(commissions, dtype=float), d.shape)
    if d.size < 2 or np.any((c < 0) | (c >= 1)):
        raise ValueError("need at least two outcomes and commissions in [0, 1)")
    eff = 1.0 + (d - 1.0) * (1.0 - c)
    inv = 1.0 / eff
    total = float(inv.sum())
    return ArbResult(bool(total < 1.0), 1.0 - total, inv / total, 1.0 / total - 1.0)


@dataclass(frozen=True)
class Middle:
    is_middle: bool
    width: float            # points between the two lines where both bets win
    win_both_profit: float  # per unit staked on each side
    miss_loss: float        # loss per unit staked on each side when the result lands outside


def find_middle(over_line: float, over_odds: float, under_line: float, under_odds: float) -> Middle:
    """Middle on a total (or spread): over X at one book and under Y > X at another. Any result
    strictly between the lines wins both bets; otherwise one wins and one loses."""
    do, du = float(_decimals(over_odds)), float(_decimals(under_odds))
    width = under_line - over_line
    miss = max(1.0 - (do - 1.0), 1.0 - (du - 1.0))
    return Middle(bool(width > 0), float(width), (do - 1.0) + (du - 1.0), float(miss))


def kelly_stakes(fair_probabilities_, decimal_odds, bankroll: float, multiplier: float = 0.25,
                 per_bet_cap: float = 0.05) -> np.ndarray:
    """Fractional Kelly stakes in currency for independent +EV bets: f = (p b - q) / b with b = odds - 1.
    The multiplier is clamped to [0.15, 0.50]; each stake is capped at per_bet_cap of bankroll and the
    total at the bankroll; negative-edge bets get exactly 0."""
    p, d = np.asarray(fair_probabilities_, dtype=float).ravel(), _decimals(decimal_odds).ravel()
    if p.size != d.size or np.any((p < 0) | (p > 1)) or not np.all(np.isfinite(p)):
        raise ValueError("need one probability in [0, 1] per price")
    if bankroll < 0 or not 0 < per_bet_cap <= 1:
        raise ValueError("bankroll must be non-negative and per_bet_cap in (0, 1]")
    m = KELLY_MULTIPLIER_MIN if not np.isfinite(multiplier) else min(max(multiplier, KELLY_MULTIPLIER_MIN),
                                                                    KELLY_MULTIPLIER_MAX)
    b = d - 1.0
    f = np.minimum(np.maximum((p * b - (1.0 - p)) / b, 0.0) * m, per_bet_cap)
    if f.sum() > 1.0:
        f = f / f.sum()
    return f * bankroll


# ---------------------------------------------------------------- API client
class OddsApiError(RuntimeError):
    """An Odds API request failed. The message never contains the API key. `status` is the HTTP status of the last
    answer, or None when no answer came (a network error, or nothing was sent)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


Transport = Callable[[str, dict[str, Any], float], tuple[int, Mapping[str, str], Any]]


def _requests_transport(url: str, params: dict[str, Any], timeout: float):
    import requests

    r = requests.get(url, params=params, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, r.headers, body


def read_key(name: str) -> str:
    """One Odds API key, read now through the project's secrets loader (the trading file, then the
    environment). The error names the variable, never a value."""
    from valedia_trader.core import secrets

    secrets.load_env()
    try:
        return secrets.secret(name).strip()
    except secrets.SecretError:
        raise OddsApiError(f"{name} is not set") from None


def _regions_count(bookmakers, regions) -> int:
    books = [b for b in (bookmakers.split(",") if isinstance(bookmakers, str) else list(bookmakers or [])) if b.strip()]
    if books:
        return math.ceil(len(books) / 10)                  # "every group of 10 bookmakers is ... 1 region"
    names = [r for r in (regions.split(",") if isinstance(regions, str) else list(regions or [])) if r.strip()]
    return max(len(names), 1)


def historical_cost(markets="h2h", bookmakers=None, regions="us") -> int:
    """Credits a historical odds call costs by the documented rule: 10 x markets x regions, where named
    bookmakers count one region per group of ten (and take priority over regions)."""
    n_markets = len([m for m in (markets.split(",") if isinstance(markets, str) else list(markets)) if m.strip()])
    return 10 * max(n_markets, 1) * _regions_count(bookmakers, regions)


def _usage(headers) -> dict[str, int | None]:
    low = {str(k).lower(): v for k, v in dict(headers or {}).items()}
    out: dict[str, int | None] = {}
    for field, name in USAGE_HEADERS.items():
        raw = low.get(name)
        out[field] = int(str(raw).strip()) if raw is not None and str(raw).strip().lstrip("-").isdigit() else None
    return out


class OddsApiClient:
    """Minimal the-odds-api.com v4 client with retry on 429/5xx and quota tracking."""

    def __init__(self, transport: Transport | None = None, timeout: float = 10.0, max_retries: int = 3,
                 backoff: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                 key_reader: Callable[[str], str] | None = None) -> None:
        self._transport = transport or _requests_transport
        self.timeout, self.max_retries, self.backoff, self._sleep = timeout, max_retries, backoff, sleep
        self._key_reader = key_reader or read_key
        self.requests_remaining: int | None = None
        self.requests_used: int | None = None
        self.requests_last: int | None = None
        self.last_usage: dict[str, int | None] = {"remaining": None, "used": None, "last": None}
        self.attempts_sent = 0               # requests handed to the transport by the last call

    def __repr__(self) -> str:
        return f"OddsApiClient(remaining={self.requests_remaining}, used={self.requests_used})"

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        key = os.environ.get("ODDS_API_KEY", "").strip()
        if not key:
            raise OddsApiError("ODDS_API_KEY is not set")
        return self._send(path, params, key)

    def _get_with(self, path: str, params: dict[str, Any], key: str, retries: int | None = None) -> Any:
        """GET with the named key ("paid" or "free"), read now; the usage headers land in `last_usage`.
        `retries` overrides `max_retries` for this call (0: one attempt only)."""
        self.last_usage = {"remaining": None, "used": None, "last": None}
        self.attempts_sent = 0
        if key not in KEY_ENV:
            raise ValueError("key must be 'paid' or 'free'")
        value = str(self._key_reader(KEY_ENV[key]) or "").strip()
        if not value:
            raise OddsApiError(f"{KEY_ENV[key]} is not set")
        return self._send(path, params, value, retries)

    def _send(self, path: str, params: dict[str, Any], key: str, retries: int | None = None) -> Any:
        query = {k: v for k, v in params.items() if v is not None}
        self.last_usage = {"remaining": None, "used": None, "last": None}
        self.attempts_sent = 0
        last, last_status = "no attempt made", None
        tries = self.max_retries if retries is None else int(retries)
        for attempt in range(tries + 1):
            self.attempts_sent += 1
            try:
                status, headers, body = self._transport(BASE_URL + path, {**query, "apiKey": key}, self.timeout)
            except Exception as exc:  # network errors: report the type only, never the URL with the key
                last = f"transport error ({type(exc).__name__})"
                status = None
            else:
                self.last_usage = _usage(headers)
                for attr, field in (("requests_remaining", "remaining"), ("requests_used", "used"),
                                    ("requests_last", "last")):
                    if self.last_usage[field] is not None:
                        setattr(self, attr, self.last_usage[field])
                if status == 200:
                    return body
                last, last_status = f"HTTP {status}", status
                if status not in (429, 500, 502, 503, 504):
                    break
            if attempt < tries:
                self._sleep(self.backoff * 2**attempt)
        raise OddsApiError(f"GET {path} failed: {last}", last_status if last.startswith("HTTP") else None)

    def sports(self, all_sports: bool = False) -> list[dict[str, Any]]:
        """GET /v4/sports (does not count against the quota)."""
        return self._get("/v4/sports", {"all": "true" if all_sports else None})

    def odds(self, sport: str, regions: str = "us", markets: str = "h2h", odds_format: str = "decimal",
             bookmakers: str | None = None) -> list[dict[str, Any]]:
        """GET /v4/sports/{sport}/odds: moneyline (h2h), spreads and totals across books."""
        return self._get(f"/v4/sports/{sport}/odds", {"regions": None if bookmakers else regions, "markets": markets,
                                                      "oddsFormat": odds_format, "bookmakers": bookmakers})

    def event_odds(self, sport: str, event_id: str, regions: str = "us", markets: str = "h2h",
                   odds_format: str = "decimal", bookmakers: str | None = None) -> dict[str, Any]:
        """GET /v4/sports/{sport}/events/{eventId}/odds: any market, including player props."""
        return self._get(f"/v4/sports/{sport}/events/{event_id}/odds",
                         {"regions": None if bookmakers else regions, "markets": markets,
                          "oddsFormat": odds_format, "bookmakers": bookmakers})

    def historical_odds(self, sport: str, date: str, bookmakers, markets: str = "h2h", key: str = "paid",
                        odds_format: str = "decimal", retries: int | None = None) -> dict[str, Any]:
        """GET /v4/historical/sports/{sport}/odds?date=: the closest snapshot at or before `date` (its own
        `timestamp`, `previous_timestamp`, `next_timestamp` and `data`). Paid plans only, so the free key is
        refused here before anything is sent. Costs `historical_cost(markets, bookmakers)`."""
        if key not in KEY_ENV:
            raise ValueError("key must be 'paid' or 'free'")
        if key != "paid":
            raise OddsApiError("historical odds need the paid key; the free key is never used for them")
        books = bookmakers if isinstance(bookmakers, str) else ",".join(bookmakers)
        return self._get_with(f"/v4/historical/sports/{sport}/odds",
                              {"date": date, "markets": markets, "bookmakers": books, "oddsFormat": odds_format,
                               "dateFormat": "iso"}, key, retries)

    def historical_events(self, sport: str, date: str, key: str = "free") -> dict[str, Any]:
        """GET /v4/historical/sports/{sport}/events?date= (1 credit, nothing when empty; all plans)."""
        return self._get_with(f"/v4/historical/sports/{sport}/events", {"date": date, "dateFormat": "iso"}, key)

    def sports_with(self, key: str = "paid", retries: int | None = None) -> list[dict[str, Any]]:
        """GET /v4/sports with the named key: free of charge, and it refreshes the usage headers."""
        return self._get_with("/v4/sports", {}, key, retries)


def normalize_odds(events, now=None, max_age_seconds: float | None = None) -> pd.DataFrame:
    """Flatten v4 odds JSON (a list of events or one event) into tidy rows: event_id, sport, commence_time,
    home_team, away_team, book, market, outcome, point, decimal_odds, last_update. Quotes older than
    `max_age_seconds` relative to `now` are dropped."""
    items = [events] if isinstance(events, dict) else list(events or [])
    rows = []
    for ev in items:
        for book in ev.get("bookmakers", []):
            for market in book.get("markets", []):
                stamp = market.get("last_update") or book.get("last_update")
                for out in market.get("outcomes", []):
                    price = out.get("price")
                    if price is None or not math.isfinite(float(price)) or float(price) <= 1.0:
                        continue
                    name = out.get("name") if not out.get("description") else f"{out['description']} {out['name']}"
                    rows.append((ev.get("id"), ev.get("sport_key"), ev.get("commence_time"), ev.get("home_team"),
                                 ev.get("away_team"), book.get("key"), market.get("key"), name, out.get("point"),
                                 float(price), stamp))
    df = pd.DataFrame(rows, columns=["event_id", "sport", "commence_time", "home_team", "away_team", "book",
                                     "market", "outcome", "point", "decimal_odds", "last_update"])
    for col in ("commence_time", "last_update"):
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    if max_age_seconds is not None:
        ref = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC") if pd.Timestamp(
            now).tzinfo else pd.Timestamp(now, tz="UTC")
        age = (ref - df["last_update"]).dt.total_seconds()
        df = df[age.notna() & (age <= max_age_seconds)]
    return df.reset_index(drop=True)
