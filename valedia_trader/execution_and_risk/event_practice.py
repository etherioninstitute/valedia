"""Event-contract practice: one capped order through the order ledger in Webull's paper Events account. Test only.

    python -m valedia_trader.execution_and_risk.event_practice --ticker T --outcome yes|no --contracts N --side buy|sell [--limit P]
    python -m valedia_trader.execution_and_risk.event_practice --reconcile-only
    python -m valedia_trader.execution_and_risk.event_practice --confirm-settlement ID --who NAME --note TEXT
    python -m valedia_trader.execution_and_risk.event_practice --withdraw-pending ID --who NAME --note TEXT

Design: docs/specs/2026-11-13-event-orders-design.md, part 2 (roadmap 4.7). No strategy is wired to it: it proves the
order path for event contracts with the guarantees the order step gives stocks (an order is sent at most once, an
unknown outcome blocks, every fill is journaled once, positions reconcile). `--reconcile-only` sends nothing: it
reconciles, asks Webull to cancel this ledger's open orders, reconciles again and journals the fills not yet journaled.

One run, in order. A failure at any step sends nothing; the reason is returned (and journaled once the lock is held).

 1. environment  the venue's name a plain word ending in "-events" (an Events venue: its order ledger, state and lock
                 files are its own, never the Cash venue's); its `environment` a plain str equal to "sandbox" by a
                 static and by an ordinary lookup, and its `host` present and, by both lookups, a plain str with
                 "sandbox" as a whole dot-separated label (the order step's rule, order_step._sandbox_host); the ledger
                 treating exactly NotTransmitted and OrderRejected as not sent. Read before anything else and again
                 before the order is intended and immediately before submit_once. The adapter refuses event orders in
                 production by itself as well.
    input        the request within the caps fixed here, whatever is passed: a ticker and outcome forming an event
                 name, 1 to MAX_CONTRACTS whole contracts (an int), side buy or sell, a limit given as text or a
                 Decimal with at most two decimals from 0.01 to 0.99 (or none). Checked before anything is read.
    lock         one run at a time per state file; everything below runs under it and journals a summary.
    journal      every journal file is read line by line; a damaged line the order step has not recorded as torn, or a
                 last file not ending in a newline, stops the run (a person must look).
 2. baseline     at the first run (no baseline, no orders on the ledger) the account's event positions are recorded;
                 they are never sold. With orders on the ledger and no baseline, the run stops.
 3. reconcile    reconcile (positions given: baseline plus the fills applied), ask Webull to cancel every order of this
                 ledger still open, reconcile again; each new fill journaled once (keyed by its fill id), the
                 journal file synced to disk, then marked applied. A blocked ledger, an order still open, or a holding
                 that differs from baseline plus fills stops the run.
    settle       (T45-1) first, every state row whose journal line is missing is written. After the cancels and the
                 second reconcile, the holdings K the ledger expects and the account shows at exactly 0 (and nothing
                 else differs) are settled, together, only on two witnesses: Kalshi's record shows exactly a
                 settlement for each (judge_settlement; no ledger order on it open, no fill on it unapplied, the time
                 after every submission of a filled order on it), and the account's cash confirms it: a reading now,
                 with no order of this ledger open, against the latest clean earlier reading, exactly in cents,
                 now - prev == (known cash now - known cash then) + the payouts of K (known_cents). Then each is a row
                 with an integer id, a journal line keyed by it (synced), the row marked journaled, and a third
                 reconcile with the new book, which must be clean. If the record fails, nothing is settled and the
                 stop names the fact; if the cash fails, one pending record per name waits for a person
                 (--confirm-settlement) and nothing is sent meanwhile. No order is sent on a settled ticker.
    person       --confirm-settlement and --withdraw-pending: the environment first, then under the lock the same
                 reconcile (nothing of this ledger open or unjournaled, else refused: run --reconcile-only first).
                 A confirmation needs the pending name at exactly 0 and nothing else different (other pending names
                 gone together aside), then Kalshi's record again; a withdrawal (no settlement) needs a clean
                 reconcile, the holding shown in full. A refusal journals nothing and changes no practice state.
    cash         every run reads the buying power once (unread unless a finite number from 0 in whole cents), with the
                 known cash; only a reading after a clean reconcile can be the earlier side of the equation.
                 `win_cash_observed` stays false until a win has been settled on the equation.
 4. cap          at most MAX_ORDERS_PER_DAY orders handed to the ledger in one New York day, across runs.
 5. sale         a sale is only of contracts this program bought (the ledger's fills of that name), never the baseline;
                 the adapter checks the account's holdings of that ticker and outcome less its open sales as well.
 6. quote        Kalshi's public book (KalshiData.orderbook) for the ticker: two-sided, prices on the cent from 0.01 to
                 0.99, the ask above the bid and at most MAX_SPREAD above it. For `no` the bid is the best no bid and the
                 ask one less the best yes bid. The quote's time is taken before the book is asked for.
 7. limit        by default the ask plus one cent for a purchase, the bid less one cent for a sale, never above 0.99 or
                 below 0.01; a given limit may not cross the book by more than MAX_CROSS (above the ask for a purchase,
                 below the bid for a sale). At most MAX_ORDER_DOLLARS (contracts times limit) an order.
 8. send         the quote at most QUOTE_MAX_AGE_SECONDS old on the clock before ledger.intend and again immediately
                 before ledger.submit_once (with the environment read between); an order failing the second look stays
                 `intended`, never sent. A refusal or an unknown outcome stops the run; an unknown outcome blocks every
                 later run until a reconcile settles it.
 9. after        reconcile once more and journal the fills.

Only the command's own words reach the journal and the summary: from an error its class name and plain code
(order_step._venue_error), from the venue only words and numbers this module checks. The ledger's own file keeps
the venue's text. Nothing here builds a broker client except main(), which reads the secrets file the way the
order step's command does and refuses any configured environment other than the test environment before building it.
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import pandas as pd

from valedia_trader.execution_and_risk.order_step import (CANCEL_WORDS, NOT_SENT, OPEN_STATES, TEST_ENVIRONMENT, _RunLock,
                                                          _sandbox_host, _sync_file, _venue_error, ledger_path_for,
                                                          open_ledger, wall_clock)
from valedia_trader.execution_and_risk.orders import NOTICE_KINDS, STATES, OrderError, OrderLedger, OrdersBlocked
from valedia_trader.venues.kalshi_data import MARKET_RECORD_KEYS
from valedia_trader.venues.webull import EVENTS_CLASS, parse_event_name

__all__ = ["MAX_CONTRACTS", "MAX_ORDER_DOLLARS", "MAX_ORDERS_PER_DAY", "QUOTE_MAX_AGE_SECONDS", "MAX_SPREAD", "MAX_CROSS",
           "PracticeState", "PracticeError", "NotSettled", "run_event_practice", "quote_from_book", "choose_limit",
           "kalshi_book", "kalshi_market", "judge_settlement", "known_cents", "confirm_settlement", "withdraw_pending",
           "build_events_venue", "state_path_for", "main"]

MAX_CONTRACTS = 5
MAX_ORDER_DOLLARS = Decimal("5.00")
MAX_ORDERS_PER_DAY = 10
QUOTE_MAX_AGE_SECONDS = 20.0
MAX_SPREAD = Decimal("0.10")
MAX_CROSS = Decimal("0.02")
CENT = Decimal("0.01")
LOWEST, HIGHEST = Decimal("0.01"), Decimal("0.99")
NEW_YORK = "America/New_York"
EVENTS_SUFFIX = "-events"
JOURNAL_TYPE = "event_practice"
MAX_UNITS = 1e12
SIDES = ("buy", "sell")
NOT_COUNTED = ("refused", "not sent")              # outcomes that are not an order handed to the venue
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_TAG = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_LIMIT_TEXT = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,2})?")
_ABSENT = object()
# settlement (T45-1): only these exact values of Kalshi's record are a settlement
SETTLED_VALUES = {"0.0000": Decimal("0.00"), "1.0000": Decimal("1.00")}      # settlement_value_dollars: the YES value
RESULT_VALUE = {"yes": "1.0000", "no": "0.0000"}
_STATUS_WORD = re.compile(r"[a-z_]{1,32}")
_KALSHI_TIME = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z")
TIERS = ("live", "historical")
WHO_MAX, NOTE_MAX = 100, 1000


class PracticeError(RuntimeError):
    pass


class NotSettled(ValueError):
    """Kalshi's record does not show exactly a settlement; the message names the fact, in this module's own words."""


class _Stop(Exception):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage, self.reason = stage, reason


def _plain(pattern: re.Pattern, x) -> bool:
    return type(x) is str and pattern.fullmatch(x) is not None


def _as_float(x) -> float | None:
    try:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


# ---- pure pieces


def _limit_given(limit) -> Decimal | None:
    """The limit asked for, as a Decimal on the cent from 0.01 to 0.99, or None for none; _Stop("input") otherwise. Text
    of digits with at most two decimals, or a finite Decimal with at most two decimals; nothing else (not a float)."""
    if limit is None:
        return None
    if type(limit) is str:
        if not _LIMIT_TEXT.fullmatch(limit):
            raise _Stop("input", "the limit must be written as a price with at most two decimals, such as 0.45")
        d = Decimal(limit)
    elif isinstance(limit, Decimal):
        d = limit
    else:
        raise _Stop("input", "the limit must be given as text (such as 0.45) or a Decimal")
    try:
        on_cent = d.is_finite() and d == d.quantize(CENT)
    except InvalidOperation:
        on_cent = False
    if not on_cent or not LOWEST <= d <= HIGHEST:
        raise _Stop("input", "the limit must have at most two decimals and be from 0.01 to 0.99")
    return d.quantize(CENT)


def check_request(ticker, outcome, contracts, side, limit) -> dict:
    """The request within the caps, or _Stop("input"). Never adjusts anything."""
    if type(ticker) is not str or type(outcome) is not str or parse_event_name(f"{ticker}:{outcome}") != (ticker, outcome):
        raise _Stop("input", "the ticker must be in Kalshi's shape (a capital letter first, then capitals, digits, dots and "
                             "hyphens, at most 80) and the outcome exactly yes or no")
    if type(contracts) is not int or not 1 <= contracts <= MAX_CONTRACTS:
        raise _Stop("input", f"the order must be for 1 to {MAX_CONTRACTS} whole contracts, given as an int")
    if type(side) is not str or side not in SIDES:
        raise _Stop("input", "the side must be buy or sell")
    return {"ticker": ticker, "outcome": outcome, "name": f"{ticker}:{outcome}", "contracts": contracts, "side": side,
            "limit": _limit_given(limit)}


def _cents(x) -> Decimal | None:
    """A book price as a Decimal when it is a finite number on the cent from 0.01 to 0.99, else None."""
    v = _as_float(x)
    if v is None:
        return None
    d = Decimal(repr(v))
    return d if d == d.quantize(CENT) and LOWEST <= d <= HIGHEST else None


def _bids(levels) -> list:
    """The prices of one side's levels that carry a finite positive size; anything unreadable is left out."""
    out = []
    if not isinstance(levels, (list, tuple)):
        return out
    for level in levels:
        if isinstance(level, (list, tuple)) and len(level) == 2:
            size = _as_float(level[1])
            if size is not None and size > 0 and _as_float(level[0]) is not None:
                out.append(level[0])
    return out


def quote_from_book(book, outcome: str) -> tuple[Decimal, Decimal]:
    """(bid, ask) of `outcome` from Kalshi's book ({"yes": [(price, size), ...], "no": [...]}: each side's bids), or
    _Stop("quote"). The bid is the outcome's best bid; the ask is one less the other side's best bid. Two-sided, on the
    cent, the ask above the bid by at most MAX_SPREAD."""
    if not isinstance(book, dict):
        raise _Stop("quote", "Kalshi's book is not an object; nothing is sent")
    own, other = (book.get("yes"), book.get("no")) if outcome == "yes" else (book.get("no"), book.get("yes"))
    own, other = _bids(own), _bids(other)
    if not own or not other:
        raise _Stop("quote", f"Kalshi's book for the {outcome} side is not two-sided; nothing is sent")
    bid, other_bid = _cents(max(own, key=float)), _cents(max(other, key=float))
    if bid is None or other_bid is None:
        raise _Stop("quote", "Kalshi's best prices are not on the cent from 0.01 to 0.99; nothing is sent")
    ask = Decimal(1) - other_bid
    if not ask > bid:
        raise _Stop("quote", f"Kalshi's spread is not positive (bid {bid}, ask {ask}); nothing is sent")
    if ask - bid > MAX_SPREAD:
        raise _Stop("quote", f"Kalshi's spread of {ask - bid} (bid {bid}, ask {ask}) is above {MAX_SPREAD}; nothing is sent")
    return bid, ask


def choose_limit(side: str, bid: Decimal, ask: Decimal, given: Decimal | None) -> Decimal:
    """The limit sent: the given one if it does not cross the book by more than MAX_CROSS, else _Stop("limit"); by
    default the ask plus a cent (a purchase) or the bid less a cent (a sale), kept within 0.01 to 0.99."""
    if side == "buy":
        limit = given if given is not None else min(ask + CENT, HIGHEST)
        if limit > ask + MAX_CROSS:
            raise _Stop("limit", f"the limit {limit} is above the ask {ask} by more than {MAX_CROSS}; nothing is sent")
    else:
        limit = given if given is not None else max(bid - CENT, LOWEST)
        if limit < bid - MAX_CROSS:
            raise _Stop("limit", f"the limit {limit} is below the bid {bid} by more than {MAX_CROSS}; nothing is sent")
    if not LOWEST <= limit <= HIGHEST:
        raise _Stop("limit", "the limit is not from 0.01 to 0.99; nothing is sent")
    return limit


def kalshi_book(ticker: str) -> dict:
    """Kalshi's public book for `ticker`, best level of each side (keyless; reaches the network). No test calls this."""
    from valedia_trader.venues.kalshi_data import KalshiData
    return KalshiData().orderbook(ticker, depth=1)


def kalshi_market(ticker: str) -> dict:
    """Kalshi's public record of one market (KalshiData.market: keyless; reaches the network). No test calls this."""
    from valedia_trader.venues.kalshi_data import KalshiData
    return KalshiData().market(ticker)


def _kalshi_time(text) -> float | None:
    """A UTC time exactly as Kalshi writes it ("2026-11-14T03:49:44.800032Z": a plain str, ASCII digits, a capital T
    and Z, at most six decimals of a second) in epoch seconds, else None."""
    if type(text) is not str:
        return None
    m = _KALSHI_TIME.fullmatch(text)
    if m is None:
        return None
    try:
        y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
        micro = int(m.group(7).ljust(6, "0")) if m.group(7) else 0
        return datetime(y, mo, d, h, mi, s, micro, tzinfo=timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return None


def judge_settlement(record, ticker: str, now: float, after: float | None = None) -> Decimal:
    """The YES value (Decimal 1.00 or 0.00) of `ticker`'s settlement when Kalshi's record (KalshiData.market's
    shape) shows exactly one, else NotSettled naming the fact that is missing. Every field must be a plain str of
    exactly the safe value: `ticker` equal to the one asked, `market_type` "binary", `status` "finalized", `result`
    "yes" or "no", `settlement_value_dollars` "1.0000" for yes and "0.0000" for no, `settlement_ts` a well-formed UTC
    time before `now` and, when `after` is given (the latest time this ledger submitted an order on that name that
    filled; a fill cannot precede its order), later than it.
    Nothing is inferred: another spelling, a padded or empty string, a number, a bool or None is not a settlement."""
    if not isinstance(record, dict):
        raise NotSettled("Kalshi's record is not an object")

    def exact(key):
        v = record.get(key)
        return v if type(v) is str else None
    if exact("ticker") != ticker:
        raise NotSettled("Kalshi's record names another ticker, or none")
    if exact("market_type") != "binary":
        raise NotSettled("Kalshi's market type is not exactly binary")
    status = exact("status")
    if status != "finalized":
        seen = ("missing or not text" if status is None else status if _STATUS_WORD.fullmatch(status)
                else "not a plain word")
        raise NotSettled(f"Kalshi's status is {seen}, not finalized")
    result = exact("result")
    if result not in RESULT_VALUE:
        raise NotSettled("Kalshi's result is not exactly yes or no")
    value = exact("settlement_value_dollars")
    if value not in SETTLED_VALUES:
        raise NotSettled("Kalshi's settlement value is not exactly 0.0000 or 1.0000")
    if value != RESULT_VALUE[result]:
        raise NotSettled(f"Kalshi's settlement value {value} does not match its result {result}")
    ts = _kalshi_time(record.get("settlement_ts"))
    if ts is None:
        raise NotSettled("Kalshi's settlement time is missing or not a well-formed UTC time")
    if not ts < now:
        raise NotSettled("Kalshi's settlement time is not in the past")
    if after is not None and not ts > after:
        raise NotSettled("Kalshi's settlement time is not later than every time this program submitted an order on that "
                         "name that filled")
    return SETTLED_VALUES[value]


def _person_words(x, most: int) -> str | None:
    """A person's words for confirming a settlement: a plain str, trimmed, not empty, at most `most` characters, and no
    control or format character (Unicode category C) anywhere; else None."""
    if type(x) is not str:
        return None
    t = x.strip()
    if not t or len(t) > most or any(unicodedata.category(c).startswith("C") for c in t):
        return None
    return t


def _whole_cents(d: Decimal) -> int | None:
    """An amount in dollars as whole cents, or None when it is not exactly a whole number of cents."""
    try:
        c = d * 100
        return int(c) if c.is_finite() and c == c.to_integral_value() else None
    except (InvalidOperation, OverflowError, ValueError):
        return None


def _dec(x) -> Decimal | None:
    v = _as_float(x)
    return Decimal(repr(v)) if v is not None else None


CENT_SLACK = Decimal("0.000001")                         # cents: the ledger keeps floats; a millionth of a cent is them
UNIT_SLACK = Decimal("0.000000001")                      # contracts, likewise


def _near_cents(d: Decimal) -> int | None:
    """`d` dollars as whole cents when it is within CENT_SLACK of a whole cent (the float sums of the ledger's rows),
    else None."""
    try:
        c = d * 100
        r = c.to_integral_value()
        return int(r) if c.is_finite() and abs(c - r) <= CENT_SLACK else None
    except (InvalidOperation, OverflowError, ValueError):
        return None


def known_cents(fills: list[dict], settlements: list[dict]) -> tuple[int | None, str | None]:
    """The ledger's cumulative known cash in whole cents, from its fill rows as orders.py writes them, plus each
    settlement's payout. The rows are taken order by order (by tag): a row's units are signed like its order (a sale
    negative), its price covers `price_covers_units` contracts, a positive count, and a row of 0 units carries only a
    later fee or a later price. For each order, a purchase costs price times contracts plus its fees and a sale brings
    price times contracts less its fees. (None, why) when a piece is not known exactly: a fill not yet applied, a row
    with contracts and no fee, an order whose priced contracts are not exactly its filled contracts, or a figure not
    in whole cents. orders.py signs every row of an order like the order, so an order's summed units give its side."""
    orders: dict = {}
    for f in fills:
        if not f.get("applied"):
            return None, "a fill is not yet applied"
        units = _dec(f.get("units"))
        if units is None:
            return None, "a fill has no readable number of contracts"
        o = orders.setdefault(f.get("tag"), {"units": Decimal(0), "covered": Decimal(0), "value": Decimal(0),
                                             "fee": Decimal(0)})
        fee = f.get("fee")
        if fee is None:
            if units != 0:
                return None, "a fill has no fee"
        else:
            fee = _dec(fee)
            if fee is None or fee < 0:
                return None, "a fill's fee cannot be read"
            o["fee"] += fee
        o["units"] += units
        price, covers = f.get("price"), f.get("price_covers_units")
        if price is not None or covers is not None:
            price, covers = _dec(price), _dec(covers)
            if price is None or covers is None or price < 0 or not covers > 0:
                return None, "a fill's price cannot be read"
            o["covered"] += covers
            o["value"] += price * covers
    total = 0
    for o in orders.values():
        if abs(o["covered"] - abs(o["units"])) > UNIT_SLACK:
            return None, "a fill has no price for all its contracts"
        cash = (o["value"] if o["units"] < 0 else -o["value"]) - o["fee"]     # a sale brings, a purchase pays
        c = _near_cents(cash)
        if c is None:
            return None, "a fill's cash is not in whole cents"
        total += c
    for r in settlements:
        units = _dec(r.get("units"))
        if units is None or r.get("price") not in ("1.00", "0.00"):
            return None, "a settlement's payout cannot be read"
        c = _whole_cents(units * Decimal(r["price"]))
        if c is None:
            return None, "a settlement's payout is not in whole cents"
        total += c
    return total, None


def _cash_equation(prev: dict | None, now: dict, rows: list[dict]) -> tuple[dict, str | None]:
    """(the figures, None) when, exactly in cents, now - prev == (known now - known then) + the payouts of `rows`;
    else (the figures, why). `prev` is the latest clean reading (PracticeState.last_clean_reading), `now` this run's."""
    payout = 0
    for r in rows:
        c = _whole_cents(Decimal(repr(float(r["units"]))) * Decimal(r["price"]))
        payout = None if c is None or payout is None else payout + c
    figures = {"reading_prev": prev["id"] if prev else None, "reading_now": now["id"],
               "prev_cents": prev["cents"] if prev else None, "now_cents": now["cents"],
               "known_prev_cents": prev["known_cents"] if prev else None, "known_now_cents": now["known_cents"],
               "payout_cents": payout, "difference_cents": None}
    if prev is None:
        return figures, "there is no earlier clean reading with its buying power and known cash"
    if now["cents"] is None:
        return figures, f"this run's reading is unread: {now['unread']}"
    if now["known_cents"] is None:
        return figures, f"the known cash could not be computed: {now['known_why']}"
    if payout is None:
        return figures, "the payouts are not in whole cents"
    moved, known = now["cents"] - prev["cents"], now["known_cents"] - prev["known_cents"]
    figures["difference_cents"] = diff = moved - known - payout
    if diff != 0:
        return figures, (f"buying power moved by {moved} cents, the ledger knows of {known} cents and the payouts are "
                         f"{payout} cents: a difference of {diff} cents")
    return figures, None


def _cash_words(f: dict) -> str:
    """The figures a person needs, in words: both readings where both exist, the known cash between them, the payouts
    expected and the difference in cents; "no earlier clean reading" where there is none."""
    def cents(x):
        return "unread" if x is None else f"{x} cents"
    words = ["no earlier clean reading" if f["reading_prev"] is None
             else f"buying power {cents(f['prev_cents'])} at reading {f['reading_prev']}",
             f"{cents(f['now_cents'])} at reading {f['reading_now']} now"]
    if f["known_prev_cents"] is not None and f["known_now_cents"] is not None:
        words.append(f"the known cash between them {f['known_now_cents'] - f['known_prev_cents']} cents")
    elif f["known_now_cents"] is None:
        words.append("the known cash now unknown")
    words.append("payouts expected " + ("not in whole cents" if f["payout_cents"] is None else f"{f['payout_cents']} cents"))
    if f["difference_cents"] is not None:
        words.append(f"a difference of {f['difference_cents']} cents")
    return "; ".join(words)


def _settle_blockers(sym: str, unapplied: set, busy: set) -> None:
    """NotSettled when a fill of this program on `sym` is unapplied or an order of this ledger on it is open or of
    unknown outcome. A second line: today every fill is applied right after the reconcile that wrote it, before this
    runs, so the first cannot fire; the second can (a cancel that did not take)."""
    if sym in unapplied:
        raise NotSettled("a fill of this program on it is not yet applied")
    if sym in busy:
        raise NotSettled("an order of this ledger on it is open or its outcome is not known; the next run looks again")


def _program_owned(name: str, fills: list[dict], settlements: list[dict]) -> float:
    """Contracts of `name` this program bought and still holds: its fills less its settled part. A second line: an
    order on a settled ticker is refused before the sale check, so the subtraction cannot matter today."""
    return math.fsum([float(f["units"]) for f in fills if f["symbol"] == name]
                     + [-float(r["program_units"]) for r in settlements if r["symbol"] == name])


def _latest_submission(ledger: OrderLedger, fills: list[dict]) -> float | None:
    """The latest time this ledger submitted (`sent_at`) an order with one of `fills`; None for none (a baseline
    holding). NotSettled when such an order has no submission time."""
    times = []
    for tag in sorted({f["tag"] for f in fills}):
        rec = ledger.order(tag)
        t = _as_float(rec.get("sent_at")) if rec else None
        if t is None:
            raise NotSettled("an order of this program on it that filled has no submission time")
        times.append(t)
    return max(times) if times else None


def _settlement_row(sym: str, outcome: str, units: float, base: float, program: float, yes_value: Decimal, record: dict,
                    batch) -> dict:
    price = yes_value if outcome == "yes" else Decimal("1.00") - yes_value
    tier = record.get("tier")
    kalshi = {**{k: record[k] for k in MARKET_RECORD_KEYS}, "tier": tier if type(tier) is str and tier in TIERS else None}
    return {"symbol": sym, "units": float(units), "baseline_units": base, "program_units": program, "price": f"{price:.2f}",
            "kalshi": kalshi, "batch": batch}


def _settlement_entry(r: dict) -> dict:
    return {"event": "settlement", "settlement_id": r["id"], "symbol": r["symbol"], "units": -float(r["units"]),
            "baseline_units": float(r["baseline_units"]), "program_units": float(r["program_units"]),
            "price": float(Decimal(r["price"])), "fee": None, "fee_seen": "none seen", "kalshi": r["kalshi"],
            "witnesses": r["witnesses"], "confirmed_by": r["confirmed_by"], "note": r["note"], "pending_id": r["pending_id"],
            "reading_prev": r["reading_prev"], "reading_now": r["reading_now"]}


def _pending_entry(r: dict) -> dict:
    return {"event": "settlement_pending", "pending_id": r["id"], "symbol": r["symbol"], "units": -float(r["units"]),
            "baseline_units": float(r["baseline_units"]), "program_units": float(r["program_units"]),
            "price": float(Decimal(r["price"])), "kalshi": r["kalshi"], "figures": r["figures"], "reason": r["reason"]}


def _withdrawal_entry(r: dict) -> dict:
    return {"event": "settlement_withdrawn", "withdrawal_id": r["id"], "pending_id": r["pending_id"], "symbol": r["symbol"],
            "who": r["who"], "note": r["note"]}


def _reading_entry(r: dict) -> dict:
    return {"event": "buying_power", "reading_id": r["id"], "buying_power": r["buying_power"], "cents": r["cents"],
            "unread": r["unread"], "clean": bool(r["clean"]), "known_cents": r["known_cents"], "known_why": r["known_why"]}


def state_path_for(ledger_path: str | Path, venue_name: str) -> Path:
    """The practice command's state file: beside the order ledger, named for the venue."""
    return Path(ledger_path).with_name(f"event_practice-{re.sub(r'[^A-Za-z0-9_.-]', '_', str(venue_name))}.sqlite")


# ---- the state file

STATE_LAYOUT = 4
LAYOUT_1_SCHEMA = [                                      # layout 1 (T19), kept as it was
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, units REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends (tag TEXT PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, outcome TEXT)",
]
LAYOUT_4_ADDED = [                                       # layout 4 (T45-1): settlements, pending, readings, withdrawals
    "CREATE TABLE IF NOT EXISTS settlements (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, units REAL NOT NULL, "
    "baseline_units REAL NOT NULL, program_units REAL NOT NULL, price TEXT NOT NULL, kalshi TEXT NOT NULL, "
    "time REAL NOT NULL, batch TEXT, witnesses TEXT NOT NULL, confirmed_by TEXT, note TEXT, pending_id INTEGER, "
    "reading_prev INTEGER, reading_now INTEGER, journaled INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, units REAL NOT NULL, "
    "baseline_units REAL NOT NULL, program_units REAL NOT NULL, price TEXT NOT NULL, kalshi TEXT NOT NULL, "
    "figures TEXT NOT NULL, reason TEXT NOT NULL, time REAL NOT NULL, batch TEXT, settlement_id INTEGER, "
    "journaled INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY, time REAL NOT NULL, batch TEXT, buying_power REAL, "
    "cents INTEGER, unread TEXT, book TEXT NOT NULL, clean INTEGER NOT NULL, known_cents INTEGER, known_why TEXT, "
    "journaled INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY, pending_id INTEGER NOT NULL UNIQUE, "
    "symbol TEXT NOT NULL, who TEXT NOT NULL, note TEXT NOT NULL, time REAL NOT NULL, batch TEXT, "
    "journaled INTEGER NOT NULL DEFAULT 0)",
]
STATE_SCHEMA = LAYOUT_1_SCHEMA + LAYOUT_4_ADDED
LAYOUT_1_TABLES = frozenset({"meta", "baseline", "sends"})
LAYOUT_4_TABLES = LAYOUT_1_TABLES | {"settlements", "pending", "readings", "withdrawals"}
WITNESSES = ("kalshi_record+cash_equation", "kalshi_record+person")


class PracticeState:
    """The command's own small file: the baseline (recorded once), each order handed to the ledger with its New York
    day and outcome, and (layout 4) each settlement, each pending settlement, each withdrawal of one and each
    buying-power reading, every one with a flag saying whether its journal line was written. Every write is one
    IMMEDIATE transaction. A layout-1 file holding exactly its three tables is migrated in place to layout 4 by adding
    the four new tables only (its meta, baseline and sends are not touched); any other layout is not opened: the
    layout 2 (with its holds table) and the layout 3 of earlier T45-1 builds included, neither ever deployed."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = wall_clock) -> None:
        self.path, self.clock = str(path), clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        try:
            self._db.execute("PRAGMA synchronous = FULL")
            with self._write():
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if not tables:
                    if version != 0:
                        raise PracticeError(f"{path} is a database of another kind; it is not opened")
                    for statement in STATE_SCHEMA:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {STATE_LAYOUT}")
                elif version == 1 and tables == LAYOUT_1_TABLES:          # layout 1 -> 4: the new tables only
                    for statement in LAYOUT_4_ADDED:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {STATE_LAYOUT}")
                elif not (version == STATE_LAYOUT and tables == LAYOUT_4_TABLES):
                    raise PracticeError(f"{path} is not an event-practice state file of layout {STATE_LAYOUT}; it is not opened")
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _write(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            try:
                self._db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        self._db.execute("COMMIT")

    def close(self) -> None:
        self._db.close()

    def baseline(self) -> dict[str, float] | None:
        if not self._db.execute("SELECT 1 FROM meta WHERE key = 'baseline_recorded'").fetchone():
            return None
        return {sym: float(u) for sym, u in self._db.execute("SELECT symbol, units FROM baseline ORDER BY symbol")}

    def record_baseline(self, positions: dict) -> dict[str, float]:
        clean = {}
        for sym, units in positions.items():
            u = _as_float(units)
            if parse_event_name(sym) is None or u is None or not 0 <= u <= MAX_UNITS:
                raise PracticeError("the baseline must be event names with finite numbers from 0")
            if u > 0:
                clean[sym] = u
        with self._write():
            if self._db.execute("SELECT 1 FROM meta WHERE key = 'baseline_recorded'").fetchone():
                raise PracticeError("a baseline is already recorded; it is recorded once")
            self._db.executemany("INSERT INTO baseline (symbol, units) VALUES (?, ?)", sorted(clean.items()))
            self._db.execute("INSERT INTO meta (key, value) VALUES ('baseline_recorded', ?)", (repr(float(self.clock())),))
        return clean

    def sent_on(self, day: str) -> int:
        """Orders handed to the ledger on `day` and not known to have stayed unsent (an outcome never written counts)."""
        marks = ",".join("?" * len(NOT_COUNTED))
        return self._db.execute(f"SELECT COUNT(*) FROM sends WHERE day = ? AND (outcome IS NULL OR outcome NOT IN ({marks}))",
                                (day, *NOT_COUNTED)).fetchone()[0]

    def note_sending(self, tag: str, day: str) -> None:
        with self._write():
            self._db.execute("INSERT INTO sends (tag, day, time, outcome) VALUES (?, ?, ?, NULL)", (tag, day, float(self.clock())))

    def note_outcome(self, tag: str, outcome: str) -> None:
        with self._write():
            self._db.execute("UPDATE sends SET outcome = ? WHERE tag = ?", (outcome, tag))

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        cur = self._db.execute(sql, args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    # -- settlements (T45-1)
    def settlements(self) -> list[dict]:
        """Every settlement row, oldest first; `kalshi` is the dict of Kalshi's fields read."""
        rows = self._rows("SELECT * FROM settlements ORDER BY id")
        for r in rows:
            r["kalshi"] = json.loads(r["kalshi"])
        return rows

    def settled_tickers(self) -> set[str]:
        return {parse_event_name(r["symbol"])[0] for r in self._rows("SELECT symbol FROM settlements")
                if parse_event_name(r["symbol"]) is not None}

    def add_settlements(self, rows: list[dict], *, witnesses: str, observed: bool = False, pending_id: int | None = None,
                        confirmed_by: str | None = None, note: str | None = None, reading_prev: int | None = None,
                        reading_now: int | None = None) -> list[int]:
        """Record settlements (not yet journaled) in one write; each integer id keys its journal entry. Once per name.
        With `observed` the flag win_cash_observed is set in the same write; with `pending_id` that pending record is
        resolved by the first of them in the same write."""
        if witnesses not in WITNESSES:
            raise PracticeError("a settlement names its witnesses")
        for r in rows:
            if parse_event_name(r["symbol"]) is None or r["price"] not in ("1.00", "0.00"):
                raise PracticeError("a settlement is of an event name at 1.00 or 0.00")
        ids = []
        with self._write():
            for r in rows:
                if self._db.execute("SELECT 1 FROM settlements WHERE symbol = ?", (r["symbol"],)).fetchone():
                    raise PracticeError("that name is already settled; a settlement is recorded once")
                cur = self._db.execute(
                    "INSERT INTO settlements (symbol, units, baseline_units, program_units, price, kalshi, time, batch, "
                    "witnesses, confirmed_by, note, pending_id, reading_prev, reading_now, journaled) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (r["symbol"], float(r["units"]), float(r["baseline_units"]), float(r["program_units"]), r["price"],
                     json.dumps(r["kalshi"], sort_keys=True), float(self.clock()), r.get("batch"), witnesses, confirmed_by,
                     note, pending_id, reading_prev, reading_now))
                ids.append(int(cur.lastrowid))
            if observed:
                self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('win_cash_observed', 'true')")
            if pending_id is not None:
                changed = self._db.execute("UPDATE pending SET settlement_id = ? WHERE id = ? AND settlement_id IS NULL "
                                           "AND id NOT IN (SELECT pending_id FROM withdrawals)", (ids[0], pending_id)).rowcount
                if changed != 1:
                    raise PracticeError(f"pending record {pending_id} is not open")
        return ids

    def mark_settlement_journaled(self, sid: int) -> None:
        with self._write():
            self._db.execute("UPDATE settlements SET journaled = 1 WHERE id = ?", (sid,))

    def win_cash_observed(self) -> bool:
        """False until a win has been settled on the cash equation (the account's cash showed exactly its payout)."""
        row = self._db.execute("SELECT value FROM meta WHERE key = 'win_cash_observed'").fetchone()
        return row is not None and row[0] == "true"

    # -- pending settlements: Kalshi's record says settled, the account's cash does not confirm it
    def add_pending(self, rows: list[dict], figures: dict, reason: str, batch: str | None) -> list[int]:
        ids = []
        with self._write():
            for r in rows:
                if self._db.execute("SELECT 1 FROM pending WHERE symbol = ? AND settlement_id IS NULL AND id NOT IN "
                                    "(SELECT pending_id FROM withdrawals)", (r["symbol"],)).fetchone():
                    raise PracticeError("a pending record of that name is already open")
                cur = self._db.execute(
                    "INSERT INTO pending (symbol, units, baseline_units, program_units, price, kalshi, figures, reason, time, "
                    "batch, journaled) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                    (r["symbol"], float(r["units"]), float(r["baseline_units"]), float(r["program_units"]), r["price"],
                     json.dumps(r["kalshi"], sort_keys=True), json.dumps(figures, sort_keys=True), reason,
                     float(self.clock()), batch))
                ids.append(int(cur.lastrowid))
        return ids

    def pending(self) -> list[dict]:
        rows = self._rows("SELECT * FROM pending ORDER BY id")
        for r in rows:
            r["kalshi"], r["figures"] = json.loads(r["kalshi"]), json.loads(r["figures"])
        return rows

    def open_pending(self) -> list[dict]:
        """Pending records neither settled nor withdrawn."""
        withdrawn = {r["pending_id"] for r in self.withdrawals()}
        return [r for r in self.pending() if r["settlement_id"] is None and r["id"] not in withdrawn]

    # -- withdrawals: a person closes a pending record without a settlement (the holding is back in full)
    def add_withdrawal(self, pending_id: int, who: str, note: str, batch: str | None) -> int:
        with self._write():
            row = self._db.execute("SELECT symbol FROM pending WHERE id = ? AND settlement_id IS NULL AND id NOT IN "
                                   "(SELECT pending_id FROM withdrawals)", (pending_id,)).fetchone()
            if row is None:
                raise PracticeError(f"pending record {pending_id} is not open")
            cur = self._db.execute("INSERT INTO withdrawals (pending_id, symbol, who, note, time, batch, journaled) "
                                   "VALUES (?,?,?,?,?,?,0)", (pending_id, row[0], who, note, float(self.clock()), batch))
            return int(cur.lastrowid)

    def withdrawals(self) -> list[dict]:
        return self._rows("SELECT * FROM withdrawals ORDER BY id")

    def mark_withdrawal_journaled(self, wid: int) -> None:
        with self._write():
            self._db.execute("UPDATE withdrawals SET journaled = 1 WHERE id = ?", (wid,))

    def open_sends(self) -> list[str]:
        """Orders handed to the ledger whose outcome this file has not written yet."""
        return [t for (t,) in self._db.execute("SELECT tag FROM sends WHERE outcome IS NULL ORDER BY tag")]

    def mark_pending_journaled(self, pid: int) -> None:
        with self._write():
            self._db.execute("UPDATE pending SET journaled = 1 WHERE id = ?", (pid,))

    # -- buying-power readings
    def add_reading(self, batch: str | None, buying_power: float | None, cents: int | None, unread: str | None,
                    book: dict, clean: bool, known: int | None, known_why: str | None) -> int:
        with self._write():
            cur = self._db.execute("INSERT INTO readings (time, batch, buying_power, cents, unread, book, clean, known_cents, "
                                   "known_why, journaled) VALUES (?,?,?,?,?,?,?,?,?,0)",
                                   (float(self.clock()), batch, buying_power, cents, unread, json.dumps(book, sort_keys=True),
                                    int(bool(clean)), known, known_why))
            return int(cur.lastrowid)

    def readings(self) -> list[dict]:
        rows = self._rows("SELECT * FROM readings ORDER BY id")
        for r in rows:
            r["book"] = json.loads(r["book"])
        return rows

    def last_clean_reading(self) -> dict | None:
        """The latest reading taken after a clean reconcile with its buying power and the known cash both read: the
        only kind that may be the earlier side of the cash equation. A reading taken while a holding was missing is
        not clean and is never this."""
        for r in reversed(self.readings()):
            if r["clean"] and r["cents"] is not None and r["known_cents"] is not None:
                return r
        return None

    def mark_reading_journaled(self, rid: int) -> None:
        with self._write():
            self._db.execute("UPDATE readings SET journaled = 1 WHERE id = ?", (rid,))

    def settle_sends(self, ledger: OrderLedger) -> None:
        """A send whose outcome was never written gets the ledger's state now; one still `intended` was never sent."""
        for (tag,) in list(self._db.execute("SELECT tag FROM sends WHERE outcome IS NULL")):
            rec = ledger.order(tag)
            self.note_outcome(tag, "not sent" if rec is None or rec["state"] == "intended" else rec["state"])


# ---- the venue as the ledger sees it


class _LedgerVenue:
    """The Events venue handed to the ledger: order_status passes the event name the ledger recorded for the tag, so
    the adapter checks the row read back against it (another ticker or outcome is an error, never a fill), and the
    submit call's exception or answer is remembered for the command's own words."""

    def __init__(self, venue, ledger: OrderLedger) -> None:
        self._venue, self._ledger = venue, ledger
        self.called, self.error, self.answer = False, None, None

    @property
    def name(self):
        return self._venue.name

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        self.called = True
        try:
            self.answer = self._venue.submit(symbol, units, tag, price=price, limit_price=limit_price)
        except BaseException as exc:
            self.error = exc
            raise
        return self.answer

    def order_status(self, tag):
        rec = self._ledger.order(tag)
        if rec is None or parse_event_name(rec.get("symbol")) is None:
            raise PracticeError("the ledger has no event name for this tag")
        units = _as_float(rec.get("units"))
        if units is None or units == 0:
            raise PracticeError("the ledger has no side for this tag")
        return self._venue.order_status(tag, expected=rec["symbol"], expected_side="BUY" if units > 0 else "SELL")

    def positions(self):
        return self._venue.positions()


# ---- one run


def run_event_practice(*, ledger: OrderLedger, venue, journal, state, ticker=None, outcome=None, contracts=None, side=None,
                       limit=None, reconcile_only: bool = False, book: Callable | None = None,
                       clock: Callable[[], float] = wall_clock, market: Callable | None = None) -> dict:
    """One practice run (see the module notes). `state` is a PracticeState or the path of its file; `book(ticker)` gives
    Kalshi's book in KalshiData.orderbook's shape (default kalshi_book); `market(ticker)` Kalshi's record of one
    market in KalshiData.market's shape (default kalshi_market), asked only for a holding that is gone. Returns the
    summary, also journaled once the lock is held. Never raises because of the venue, the book, the record or the
    request."""
    return _Run(ledger, venue, journal, state, dict(ticker=ticker, outcome=outcome, contracts=contracts, side=side, limit=limit),
                reconcile_only is True, book if book is not None else kalshi_book, clock,
                market if market is not None else kalshi_market).go()


class _Run:
    def __init__(self, ledger, venue, journal, state, request, reconcile_only, book, clock, market=None,
                 person: dict | None = None) -> None:
        self.ledger, self.venue, self.journal, self.state_arg = ledger, venue, journal, state
        self.person = dict(person) if person is not None else None       # a person's command: confirm or withdraw
        self.person_out: dict = {}
        self.request, self.reconcile_only, self.book, self.clock = request, reconcile_only, book, clock
        self.market = market
        self.settled_now: list[dict] = []
        self.settlements_journaled = 0
        self.buying_power: float | None = None
        self._settled_seen: set = set()
        self._pending_seen: set = set()
        self._reading_seen: set = set()
        self._withdrawn_seen: set = set()
        self._read_this_run = False
        self._settle_why: dict[str, str] = {}
        self.view = _LedgerVenue(venue, ledger)
        self.name, self.batch, self.day = "?", None, None
        self.state: PracticeState | None = None
        self.stage = "environment"
        self.cancels: list[dict] = []
        self.order: dict | None = None
        self.quote: dict | None = None
        self.fills_journaled = 0
        self._seen: set = set()
        self._locked = False

    # -- helpers
    def _now(self) -> float:
        try:
            now = self.clock()
        except Exception:
            now = None
        if _as_float(now) is None:
            raise _Stop("clock", "the clock did not give a finite number of seconds")
        return float(now)

    def _write(self, entry: dict) -> None:
        if not self._locked:
            raise RuntimeError("the practice command wrote to the journal without holding the lock")
        self.journal.write(self.day, {"type": JOURNAL_TYPE, "venue": self.name, "batch": self.batch, **entry})

    def _journal_file(self) -> Path | None:
        d = getattr(self.journal, "dir", None)
        return Path(d) / f"{self.day}.jsonl" if d is not None else None

    def go(self) -> dict:
        try:
            now = self._now()
        except _Stop as stop:
            self.day = str(pd.Timestamp.now(tz=NEW_YORK).date())
            return self._finish("stopped", stop.stage, stop.reason)
        stamp = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
        self.day = str(stamp.date())
        self.batch = f"ep-{stamp:%Y%m%d-%H%M%S}-{os.urandom(3).hex()}"
        try:
            self._environment()                          # the venue's name, environment and host, and the ledger
            self.stage = "input"
            req = None if self.reconcile_only else check_request(**self.request)
            if self.person is not None:
                self._check_person()
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        path = self.state_arg.path if isinstance(self.state_arg, PracticeState) else str(self.state_arg)
        if self.person is not None and not isinstance(self.state_arg, PracticeState) and not Path(path).is_file():
            return self._finish("stopped", "state", "there is no practice state file for this venue, so no pending record")
        lock = _RunLock(path)
        try:
            got = lock.acquire()
        except OSError as exc:
            return self._finish("stopped", "lock", f"the lock file could not be taken: {_venue_error(exc)}")
        if not got:
            return self._finish("stopped", "lock", "another practice run is running on this venue's state file")
        self._locked = True
        try:
            try:
                self.stage = "journal"
                self._scan_journal()
                self.stage = "state"
                self.state = self.state_arg if isinstance(self.state_arg, PracticeState) else PracticeState(path, self.clock)
            except _Stop as stop:
                return self._finish("stopped", stop.stage, stop.reason)
            except (PracticeError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", self.stage, f"the journal or the state file could not be read: {_venue_error(exc)}")
            try:
                return self._rest(req)
            finally:
                if not isinstance(self.state_arg, PracticeState):
                    self.state.close()
        except Exception as exc:                         # a fault of the command's own: still stopped and journaled
            return self._finish("stopped", "unexpected", f"the run failed unexpectedly: {_venue_error(exc)}")
        finally:
            lock.release()

    def _rest(self, req: dict | None) -> dict:
        if self.person is not None:
            return self._person_step()
        try:
            self.stage = "baseline"
            baseline = self._baseline()
            self.stage = "reconcile"
            self._reconcile_and_cancel(baseline)
            self.stage = "cash"
            self._cash(baseline)
            if req is None:
                return self._finish("done", None, None)
            self._send(req)
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except OrdersBlocked:
            return self._finish("stopped", self.stage, "the order ledger is blocked; nothing is sent")
        except (OrderError, PracticeError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", self.stage, f"the ledger, the state file or the journal failed: {_venue_error(exc)}; "
                                                       "nothing more is sent and the next run's reconcile settles what was")
        try:
            self.stage = "after"
            self._reconcile(baseline)
        except (OrderError, PracticeError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "after", f"the last reconcile could not be completed: {_venue_error(exc)}")
        return self._finish("done", None, None)

    # -- 1
    def _check_name_and_environment(self) -> None:
        try:
            name = self.venue.name
        except Exception:
            name = None
        if not _plain(_NAME, name) or not name.endswith(EVENTS_SUFFIX):
            raise _Stop("environment", "the venue is not an Events venue: its name must be a plain word ending in -events (a "
                                       f"Webull venue built for the {EVENTS_CLASS} class), so that its ledger is its own")
        self.name = name
        self._check_environment()

    def _environment(self) -> None:
        self._check_name_and_environment()
        not_sent = tuple(getattr(self.ledger, "not_sent", ()) or ())
        missing = [c.__name__ for c in NOT_SENT if c not in not_sent]
        broader = [t for t in not_sent if not (isinstance(t, type) and issubclass(t, NOT_SENT))]
        if missing or broader:
            raise _Stop("environment", "the order ledger must treat exactly NotTransmitted and OrderRejected as not sent; open "
                                       "it with order_step.open_ledger")

    def _check_environment(self) -> None:
        try:
            static_env = inspect.getattr_static(self.venue, "environment", _ABSENT)
        except Exception:
            static_env = None
        try:
            env = self.venue.environment
        except Exception:
            env = None
        if not all(type(e) is str and e == TEST_ENVIRONMENT for e in (static_env, env)):
            raise _Stop("environment", f"event practice runs against Webull's test environment ({TEST_ENVIRONMENT}, a plain "
                                       "str) only; this venue's environment is not that. Live event orders are not built")
        try:
            static = inspect.getattr_static(self.venue, "host", _ABSENT)
        except Exception:
            static = None
        try:
            dynamic = getattr(self.venue, "host", _ABSENT)
        except Exception:
            dynamic = None
        if not all(_sandbox_host(h) for h in (static, dynamic)):
            raise _Stop("environment", "the venue's host is not a plain str attribute naming the sandbox")

    # -- the journal
    def _tag_word(self, tag):
        return tag if _plain(_TAG, tag) else None

    def _scan_journal(self) -> None:
        d = getattr(self.journal, "dir", None)
        if d is None:
            try:
                entries = list(self.journal.read())
            except Exception as exc:
                raise _Stop("journal", f"the journal could not be read: {_venue_error(exc)}") from None
        else:
            try:
                files = sorted(Path(d).glob("????-??-??.jsonl"))
                entries, torn = [], []
                for i, path in enumerate(files):
                    data = path.read_bytes()
                    if data and not data.endswith(b"\n"):
                        raise _Stop("journal", f"the journal file {path.name} does not end with a full line; a person must look")
                    for n, raw in enumerate(data.split(b"\n"), 1):
                        if not raw.strip():
                            continue
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            obj = None
                        if isinstance(obj, dict):
                            entries.append(obj)
                        else:
                            torn.append((path.name, n))
            except OSError as exc:
                raise _Stop("journal", f"the journal could not be read: {_venue_error(exc)}") from None
            known = {(e.get("file"), e.get("line")) for e in entries
                     if e.get("type") == "order_step" and e.get("event") == "torn_line"}
            bad = [t for t in torn if t not in known]
            if bad:
                raise _Stop("journal", "the journal has a damaged line ("
                                       + ", ".join(f"{name} line {n}" for name, n in bad[:5]) + "); a person must look")
        # keyed by the fill id alone, as the order step does: Journal.write may redact a piece of any text (a tag) that
        # happens to look like a remembered secret, and a key holding text would then never match again
        self._seen = {e.get("fill_id") for e in entries
                      if e.get("type") == JOURNAL_TYPE and e.get("event") == "fill" and e.get("venue") == self.name}
        def ids(event, key):
            return {e.get(key) for e in entries if e.get("type") == JOURNAL_TYPE and e.get("event") == event
                    and e.get("venue") == self.name and type(e.get(key)) is int}
        self._settled_seen = ids("settlement", "settlement_id")
        self._pending_seen = ids("settlement_pending", "pending_id")
        self._reading_seen = ids("buying_power", "reading_id")
        self._withdrawn_seen = ids("settlement_withdrawn", "withdrawal_id")

    # -- 2
    def _baseline(self) -> dict[str, float]:
        base = self.state.baseline()
        if base is not None:
            return base
        if self.ledger.orders():
            raise _Stop("baseline", "no baseline is recorded but the order ledger already has orders; a person must look")
        try:
            held = dict(self.venue.positions())
        except Exception as exc:
            raise _Stop("baseline", f"the venue's positions could not be read: {_venue_error(exc)}") from None
        bad = [k for k, u in held.items() if parse_event_name(k) is None or _as_float(u) is None or not 0 <= _as_float(u) <= MAX_UNITS]
        if bad:
            raise _Stop("baseline", f"the venue's positions hold {len(bad)} entries that are not an event name with a finite "
                                    "number from 0; no baseline is recorded")
        base = self.state.record_baseline(held)
        self._write({"event": "baseline", "positions": base})
        return base

    # -- 3
    def _applied_positions(self, baseline: dict) -> dict[str, float]:
        """What the account should hold: the baseline plus the applied fills, less each settled holding."""
        out = dict(baseline)
        for f in self.ledger.fills():
            if f["applied"]:
                out[f["symbol"]] = math.fsum((out.get(f["symbol"], 0.0), float(f["units"])))
        for r in self.state.settlements():
            out[r["symbol"]] = math.fsum((out.get(r["symbol"], 0.0), -float(r["units"])))
        return out

    def _reconcile(self, baseline: dict) -> dict:
        result = self.ledger.reconcile(self.view, self._applied_positions(baseline))
        self._journal_fills()
        return result

    def _journal_fills(self) -> None:
        for f in self.ledger.unapplied_fills():
            key = f["id"]
            if key not in self._seen:
                self._write({"event": "fill", "fill_id": f["id"], "tag": self._tag_word(f["tag"]),
                             "symbol": f["symbol"] if parse_event_name(f["symbol"]) else None,
                             "units": _as_float(f["units"]), "price": _as_float(f["price"]),
                             "price_covers_units": _as_float(f["price_covers_units"]), "fee": _as_float(f["fee"])})
                path = self._journal_file()
                if path is not None:
                    _sync_file(path)                     # on disk before the fill counts as applied
                self._seen.add(key)
                self.fills_journaled += 1
            self.ledger.mark_applied([f["id"]])

    def _reconcile_and_cancel(self, baseline: dict) -> None:
        self.state.settle_sends(self.ledger)
        self._journal_rows()                             # what the state knows and the journal does not (a crash): first
        self._reconcile(baseline)
        for o in self.ledger.orders(OPEN_STATES):
            tag = self._tag_word(o["tag"]) or "an order"
            try:
                answer = self.venue.cancel(o["tag"])
                word = answer.get("status") if isinstance(answer, dict) else None
                word = word if type(word) is str and word in CANCEL_WORDS else "an answer that is not a cancel word"
            except Exception as exc:
                word = f"error: {_venue_error(exc)}"
            self.cancels.append({"tag": tag, "answer": word})
            self._write({"event": "cancel", "tag": tag, "answer": word})
        result = self._reconcile(baseline)
        still = self.ledger.orders(OPEN_STATES)
        if self._settle(baseline, result, still):       # settled on both witnesses: the book changed, reconcile again
            result = self._reconcile(baseline)
            still = self.ledger.orders(OPEN_STATES)
        if still or self.ledger.blocked():
            raise _Stop("reconcile", "; ".join(self._ledger_words(still, result))[:2000])

    def _ledger_words(self, still: list[dict], result: dict) -> list[str]:
        """Why the ledger stands in the way, in the command's own words (tags, states, names, numbers)."""
        reasons = []
        if still:
            reasons.append(f"{len(still)} order(s) of this ledger still open at the broker after the cancel requests ("
                           + ", ".join(f"{self._tag_word(o['tag']) or 'an order'} {o['state'] if o['state'] in STATES else '?'}"
                                       for o in still) + "); the next run asks again")
        unsettled = self.ledger.orders(["sent", "unknown"])
        if unsettled:
            reasons.append(f"{len(unsettled)} order(s) whose outcome is not known ("
                           + ", ".join(self._tag_word(o["tag"]) or "an order" for o in unsettled)
                           + "); nothing is sent until a reconcile settles them, and a person must look if it cannot")
        if self.ledger.attention():
            reasons.append(f"{len(self.ledger.attention())} attention item(s) open on the ledger; a person must look")
        diffs = result.get("position_differences") or {}
        if diffs:
            plain = {}
            for sym, d in diffs.items():
                if parse_event_name(sym) is not None and isinstance(d, dict):
                    plain[sym] = {k: _as_float(d.get(k)) for k in ("book", "venue", "unapplied")}
            vanished = sorted(sym for sym, d in plain.items()
                              if (d["book"] or 0.0) + (d["unapplied"] or 0.0) > 0 and not (d["venue"] or 0.0) > 0)
            for sym in vanished:
                why = self._settle_why.get(sym, "no settlement was looked for")
                reasons.append(f"an event holding the ledger expects is not in the account ({sym}); it is not taken as a "
                               f"settlement: {why}; a person must look")
            reasons.append(f"positions differ from baseline plus fills: {json.dumps(plain, sort_keys=True)}"
                           + (f" and {len(diffs) - len(plain)} more" if len(diffs) > len(plain) else "") + "; a person must look")
        if result.get("positions_error"):
            reasons.append("the venue's positions could not be read")
        if result.get("unresolved"):
            reasons.append(f"{len(result['unresolved'])} order(s) whose status could not be read or used")
        if result.get("anomalies"):
            reasons.append(f"{len(result['anomalies'])} anomaly(ies) in the reconcile; a person must look")
        return reasons or ["the order ledger is blocked"]

    # -- 3b settlement (T45-1): two witnesses, Kalshi's record and the account's cash
    def _journal_rows(self) -> None:
        """Every reading, pending record and settlement in the state whose journal line was not written (a crash or a
        failed write): its line, keyed by its integer id, written once (not again when the journal already has it),
        the journal file synced, then the row marked journaled."""
        kinds = ((self.state.readings, _reading_entry, self._reading_seen, self.state.mark_reading_journaled),
                 (self.state.pending, _pending_entry, self._pending_seen, self.state.mark_pending_journaled),
                 (self.state.settlements, _settlement_entry, self._settled_seen, self.state.mark_settlement_journaled),
                 (self.state.withdrawals, _withdrawal_entry, self._withdrawn_seen, self.state.mark_withdrawal_journaled))
        for rows, entry, seen, mark in kinds:
            for r in rows():
                if r["journaled"]:
                    continue
                if r["id"] not in seen:
                    self._write(entry(r))
                    path = self._journal_file()
                    if path is not None:
                        _sync_file(path)
                    seen.add(r["id"])
                    if entry is _settlement_entry:
                        self.settlements_journaled += 1
                mark(r["id"])

    def _settle(self, baseline: dict, result: dict, still: list[dict]) -> bool:
        """After the cancels and the second reconcile. The holdings K the ledger expects and the account shows at
        exactly 0 are settled, all together, only when (1) for each Kalshi's record shows exactly a settlement
        (judge_settlement; no order of this ledger on it open, no fill on it unapplied) and no other name differs,
        and (2) the account's cash confirms it: a reading now (no order of this ledger open) against the latest
        clean earlier reading, exactly in cents, now - prev == (known cash now - known cash then) + the payouts of K.
        (1) failing settles nothing and the stop names the fact; (2) failing keeps one pending record per name for a
        person (--confirm-settlement) and stops at stage `settlement`. True when K was settled."""
        diffs = result.get("position_differences") or {}
        if not diffs:
            return False
        expected = self._applied_positions(baseline)
        candidates, others = [], []
        for sym, d in diffs.items():
            venue = _as_float(d.get("venue")) if isinstance(d, dict) else None
            exp = _as_float(expected.get(sym))
            if parse_event_name(sym) is not None and venue == 0.0 and exp is not None and exp > 0:
                candidates.append(sym)
            else:
                others.append(sym)
        if not candidates:
            return False
        unclean = [w for w, k in (("the venue's positions could not be read", "positions_error"),
                                  ("an order's status could not be read", "unresolved"),
                                  ("the reconcile found an anomaly", "anomalies")) if result.get(k)]
        if others or unclean:
            why = unclean[0] if unclean else "another holding also differs from baseline plus fills"
            for sym in candidates:
                self._settle_why[sym] = f"{why}, so none is taken as settled"
            return False
        waiting = {r["symbol"]: r["id"] for r in self.state.open_pending()}
        if any(sym in waiting for sym in candidates):
            ids = sorted(waiting[s] for s in candidates if s in waiting)
            raise _Stop("settlement", f"pending record(s) {', '.join(map(str, ids))} wait for a person: Kalshi's record shows "
                                      "the result, but the account's cash does not confirm the holding was still there; "
                                      "only a person settles it: --confirm-settlement ID --who NAME --note TEXT")
        unapplied = {f["symbol"] for f in self.ledger.unapplied_fills()}
        busy = {o["symbol"] for o in still}
        now_t = self._now()
        judged = []
        for sym in sorted(candidates):
            try:
                judged.append(self._judge(sym, expected[sym], baseline, unapplied, busy, now_t))
            except NotSettled as why:
                self._settle_why[sym] = str(why)
        if len(judged) != len(candidates):
            for j in judged:
                self._settle_why[j["symbol"]] = ("Kalshi's record shows it settled, but another holding that is gone is not, "
                                                 "and holdings gone together are settled together")
            return False
        now = self._take_reading(baseline, clean=False, open_orders=bool(still))     # before anything of K is settled
        prev = self.state.last_clean_reading()
        figures, why = _cash_equation(prev, now, judged)
        if why is None:
            ids = self.state.add_settlements(judged, witnesses=WITNESSES[0], observed=any(j["price"] == "1.00" for j in judged),
                                             reading_prev=prev["id"], reading_now=now["id"])
            self.settled_now += [{"settlement_id": i, "symbol": j["symbol"], "units": -float(j["units"]),
                                  "price": float(Decimal(j["price"]))} for i, j in zip(ids, judged)]
            self._journal_rows()
            return True
        names = ", ".join(j["symbol"] for j in judged)
        reason = (f"Kalshi's record shows the result of {names}, but the account's cash does not confirm the holding was "
                  f"still there ({why}; {_cash_words(figures)}); nothing of it is settled")
        pids = self.state.add_pending(judged, figures, reason, self.batch)
        self._journal_rows()
        raise _Stop("settlement", f"{reason}; pending record(s) {', '.join(map(str, pids))} wait for a person: "
                                  "--confirm-settlement ID --who NAME --note TEXT")

    def _judge(self, sym: str, units: float, baseline: dict, unapplied: set, busy: set, now: float) -> dict:
        """The first witness for one holding: the row to settle it with, or NotSettled naming the fact."""
        ticker, outcome = parse_event_name(sym)
        _settle_blockers(sym, unapplied, busy)
        fills = [f for f in self.ledger.fills() if f["symbol"] == sym]
        after = _latest_submission(self.ledger, fills)
        try:
            record = self.market(ticker)
        except Exception as exc:
            raise NotSettled(f"Kalshi's record could not be read ({_venue_error(exc)})") from None
        yes_value = judge_settlement(record, ticker, now, after)
        return _settlement_row(sym, outcome, units, float(baseline.get(sym, 0.0)), math.fsum(float(f["units"]) for f in fills),
                               yes_value, record, self.batch)

    # -- 3c buying power, read only with no order of this ledger open
    def _read_buying_power(self) -> tuple[float | None, int | None, str | None]:
        """(buying power, its whole cents, None) when the venue gives a finite number from 0 in whole cents, else
        (None, None, the shape of the reason)."""
        try:
            cash = self.venue.cash()
        except Exception as exc:
            return None, None, f"the venue's cash() raised {_venue_error(exc)}"
        if not isinstance(cash, dict):
            return None, None, f"the venue's cash() gave a {type(cash).__name__}, not an object"
        v = cash.get("buying_power")
        f = _as_float(v)
        if f is None:
            return None, None, f"buying_power is a {type(v).__name__}, not a finite number"
        if f < 0:
            return None, None, "buying_power is below 0"
        cents = _whole_cents(Decimal(repr(f)))
        if cents is None:
            return None, None, "buying_power is not in whole cents"
        return f, cents, None

    def _take_reading(self, baseline: dict, clean: bool, open_orders: bool) -> dict:
        if open_orders:
            bp, cents, unread = None, None, "an order of this ledger is open, so buying power is not read"
        else:
            bp, cents, unread = self._read_buying_power()
        known, known_why = known_cents(self.ledger.fills(), self.state.settlements())
        book = {s: u for s, u in self._applied_positions(baseline).items() if u > 1e-9}
        rid = self.state.add_reading(self.batch, bp, cents, unread, book, clean and not open_orders, known, known_why)
        self.buying_power, self._read_this_run = bp, True
        self._journal_rows()
        return {"id": rid, "cents": cents, "unread": unread, "known_cents": known, "known_why": known_why}

    def _cash(self, baseline: dict) -> None:
        """The run's reading after a clean reconcile (one already taken for a settlement is the run's reading)."""
        if not self._read_this_run:
            self._take_reading(baseline, clean=True, open_orders=bool(self.ledger.orders(OPEN_STATES)))

    # -- 4 to 9
    def _fresh(self, quote_at: float) -> None:
        age = self._now() - quote_at
        if not 0 <= age <= QUOTE_MAX_AGE_SECONDS:
            raise _Stop("quote", f"Kalshi's quote is {age:.0f} s old, more than {QUOTE_MAX_AGE_SECONDS:.0f} s; nothing is sent")

    def _send(self, req: dict) -> None:
        name, contracts, side = req["name"], req["contracts"], req["side"]
        self.stage = "pending"
        waiting = self.state.open_pending()
        if waiting:
            raise _Stop("pending", f"{len(waiting)} pending settlement record(s) (" + ", ".join(str(r["id"]) for r in waiting)
                                   + ") wait for a person: nothing is sent until each is confirmed with --confirm-settlement "
                                     "ID --who NAME --note TEXT")
        self.stage = "settled"
        if req["ticker"] in self.state.settled_tickers():
            raise _Stop("settled", f"the market {req['ticker']} has settled; no order is sent on a settled ticker")
        self.stage = "cap"
        if self.state.sent_on(self.day) >= MAX_ORDERS_PER_DAY:
            raise _Stop("cap", f"the day's cap of {MAX_ORDERS_PER_DAY} practice orders ({self.day}, New York) is reached")
        if side == "sell":
            self.stage = "sale"
            owned = _program_owned(name, self.ledger.fills(), self.state.settlements())
            if owned < contracts - 1e-9:
                raise _Stop("sale", f"a sale is only of contracts this program bought: the ledger holds {owned:g} of {name}; "
                                    "the baseline is never sold")
        self.stage = "quote"
        quote_at = self._now()
        try:
            raw = self.book(req["ticker"])
        except Exception as exc:
            raise _Stop("quote", f"Kalshi's book could not be read: {_venue_error(exc)}") from None
        bid, ask = quote_from_book(raw, req["outcome"])
        self.quote = {"bid": float(bid), "ask": float(ask), "time": quote_at}
        self.stage = "limit"
        limit = choose_limit(side, bid, ask, req["limit"])
        value = contracts * limit
        if value > MAX_ORDER_DOLLARS:
            raise _Stop("cap", f"the order's value of {value} is above {MAX_ORDER_DOLLARS} dollars; nothing is sent")
        self.stage = "send"
        units = float(contracts if side == "buy" else -contracts)
        self._check_environment()
        self._fresh(quote_at)
        tag = f"{self.batch}-{side}"
        self.ledger.intend(tag, name, units, float(ask if side == "buy" else bid), limit_price=float(limit), batch=self.batch)
        self.state.note_sending(tag, self.day)
        try:
            self._check_environment()                    # the last look: environment, then the quote's age
            self._fresh(quote_at)
        except _Stop:
            self.state.note_outcome(tag, "not sent")     # it stays intended, never sent
            raise
        try:
            rec = self.ledger.submit_once(self.view, tag)
        except OrdersBlocked:
            raise _Stop("send", f"the ledger refused to send {tag}: it is blocked; nothing is sent") from None
        state = rec["state"] if rec.get("state") in STATES else "unknown"
        self.state.note_outcome(tag, state)
        entry = {"tag": tag, "symbol": name, "units": units, "side": side, "limit_price": float(limit),
                 "bid": float(bid), "ask": float(ask), "state": state}
        self.order = entry
        if state == "refused":
            why = f"the venue did not take it: {_venue_error(self.view.error)}" if self.view.error is not None else "the venue did not take it"
            self._write({"event": "refused", **entry, "reason": why})
            raise _Stop("send", why)
        if state != "accepted":
            why = (f"the venue's call raised {_venue_error(self.view.error)}" if self.view.error is not None
                   else "the venue did not say accepted")
            self._write({"event": "unknown", **entry, "reason": why})
            raise _Stop("send", f"the outcome of order {tag} ({name} {units:+g}) is {state}: {why}; nothing more is sent until "
                                "a reconcile settles it")
        self._write({"event": "order", **entry})

    # -- the summary
    # -- a person's command (T45-1): confirm a pending settlement, or withdraw a pending record
    def _check_person(self) -> None:
        pid = self.person.get("id")
        if type(pid) is not int or pid < 1:
            raise _Stop("input", "the pending record's id must be a whole number from 1")
        w, n = _person_words(self.person.get("who"), WHO_MAX), _person_words(self.person.get("note"), NOTE_MAX)
        if w is None or n is None:
            raise _Stop("input", f"--who and --note are both needed: plain text, not empty after trimming, without control "
                                 f"characters (who at most {WHO_MAX} characters, the note at most {NOTE_MAX})")
        self.person["who"], self.person["note"] = w, n

    def _person_step(self) -> dict:
        """Under the lock: the pending record open; nothing of the ledger open or unjournaled; the same reconcile a
        --reconcile-only run makes (baseline plus applied fills less settled units); what the account shows now
        checked; only then the write. Any refusal leaves the journal and the practice state as they were (the
        ledger records its reconcile, as every reconcile does)."""
        kind, pid = self.person["kind"], self.person["id"]
        try:
            self.stage = "pending"
            found = [r for r in self.state.pending() if r["id"] == pid]
            if not found:
                raise _Stop("pending", f"no pending record has id {pid}")
            p = found[0]
            if p["settlement_id"] is not None:
                raise _Stop("pending", f"pending record {pid} is already settled")
            if pid in {r["pending_id"] for r in self.state.withdrawals()}:
                raise _Stop("pending", f"pending record {pid} is already withdrawn")
            if p["symbol"] in {r["symbol"] for r in self.state.settlements()}:
                raise _Stop("pending", f"{p['symbol']} is already settled")
            self.stage = "baseline"
            baseline = self.state.baseline()
            if baseline is None:
                raise _Stop("baseline", "no baseline is recorded")
            self.stage = "reconcile"
            self._quiet(before=True)
            result = self.ledger.reconcile(self.view, self._applied_positions(baseline))
            self._quiet(before=False)
            self.stage = "account"
            self._account_now(kind, p["symbol"], result)
            if kind == "confirm":
                expected = _as_float(self._applied_positions(baseline).get(p["symbol"]))
                if expected is None or abs(expected - float(p["units"])) > 1e-9:
                    raise _Stop("account", f"the pending record holds {float(p['units']):g} of {p['symbol']} but the ledger "
                                           f"now expects {expected if expected is None else format(expected, 'g')}: a "
                                           "fill arrived since it was made; the record stays open; a person must look")
            return self._confirm(p) if kind == "confirm" else self._withdraw(p)
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except OrdersBlocked:
            return self._finish("stopped", self.stage, "the order ledger is blocked")
        except (OrderError, PracticeError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", self.stage, f"the ledger, the state file or the journal failed: {_venue_error(exc)}")

    def _quiet(self, before: bool) -> None:
        """Nothing a reconcile run would cancel, journal or settle first: no order of this ledger open or of unknown
        outcome, no fill unapplied, no attention item, and (before) no state row unjournaled and no send whose outcome
        is not written. Otherwise _Stop: a --reconcile-only run comes first."""
        reasons = []
        if self.ledger.orders(OPEN_STATES):
            reasons.append("an order of this ledger is open or of unknown outcome")
        if self.ledger.unapplied_fills():
            reasons.append("a fill is not yet journaled")
        if [a for a in self.ledger.attention() if a.get("kind") not in NOTICE_KINDS]:     # a notice never blocks
            reasons.append("an attention item is open on the ledger")
        if before:
            rows = (self.state.readings(), self.state.pending(), self.state.settlements(), self.state.withdrawals())
            if any(not r["journaled"] for group in rows for r in group):
                reasons.append("a row of the practice state is not yet journaled")
            if self.state.open_sends():
                reasons.append("a send's outcome is not yet written")
        if reasons:
            raise _Stop("reconcile", "; ".join(reasons) + ("" if before else " (found by the reconcile)")
                        + "; nothing is changed: run --reconcile-only first, then this command again")

    def _account_now(self, kind: str, name: str, result: dict) -> None:
        """What the account shows now. confirm: the pending name at exactly 0, and every other difference a name of
        another open pending record also at exactly 0 (holdings gone together are pending together). withdraw: a
        clean reconcile, so the holding is shown in full. Otherwise _Stop and the record stays open."""
        unclean = [w for w, k in (("the venue's positions could not be read", "positions_error"),
                                  ("an order's status could not be read", "unresolved"),
                                  ("the reconcile found an anomaly", "anomalies")) if result.get(k)]
        if unclean:
            raise _Stop("account", f"{unclean[0]}; the pending record stays open; a person must look")
        diffs = result.get("position_differences") or {}
        if kind == "withdraw":
            if diffs or result.get("clean") is not True:
                raise _Stop("account", f"the reconcile is not clean ({', '.join(sorted(map(str, diffs))) or 'not clean'} "
                                       f"differs): the account does not show {name} in full with nothing else different; "
                                       "the pending record stays open")
            return
        def at_zero(sym):
            d = diffs.get(sym)
            return isinstance(d, dict) and _as_float(d.get("venue")) == 0.0
        if not at_zero(name):
            if name not in diffs:
                raise _Stop("account", f"the account shows {name} as the ledger expects it: the holding is there, so it is "
                                       "not settled; when it is back in full, --withdraw-pending closes the record")
            raise _Stop("account", f"the account shows {name} at neither 0 nor what the ledger expects; a person must look")
        waiting = {r["symbol"] for r in self.state.open_pending()}
        bad = sorted(str(s) for s in diffs if s != name and not (s in waiting and at_zero(s)))
        if bad:
            raise _Stop("account", f"another name differs from baseline plus fills ({', '.join(bad)}); the pending record "
                                   "stays open; a person must look")

    def _confirm(self, p: dict) -> dict:
        self.stage = "kalshi"
        ticker, outcome = parse_event_name(p["symbol"])
        now = self._now()
        try:
            fills = [f for f in self.ledger.fills() if f["symbol"] == p["symbol"]]
            after = _latest_submission(self.ledger, fills)
            try:
                record = self.market(ticker)
            except Exception as exc:
                raise NotSettled(f"Kalshi's record could not be read ({_venue_error(exc)})") from None
            yes_value = judge_settlement(record, ticker, now, after)
        except NotSettled as why:
            raise _Stop("kalshi", f"Kalshi's record does not show the settlement now: {why}") from None
        row = _settlement_row(p["symbol"], outcome, p["units"], p["baseline_units"], p["program_units"], yes_value, record,
                              self.batch)
        if row["price"] != p["price"]:
            raise _Stop("kalshi", "Kalshi's record now gives another result than when the pending record was made; a person "
                                  "must look")
        self.stage = "write"
        who, note = self.person["who"], self.person["note"]
        sid = self.state.add_settlements([row], witnesses=WITNESSES[1], pending_id=p["id"], confirmed_by=who, note=note)[0]
        entry = _settlement_entry([r for r in self.state.settlements() if r["id"] == sid][0])
        self.person_out = {"settlement_id": sid, "symbol": p["symbol"], "price": entry["price"], "who": who, "note": note,
                           "time": now}
        self._person_line(entry, sid, self._settled_seen, self.state.mark_settlement_journaled)
        return self._finish("confirmed", None, None)

    def _withdraw(self, p: dict) -> dict:
        self.stage = "write"
        who, note = self.person["who"], self.person["note"]
        wid = self.state.add_withdrawal(p["id"], who, note, self.batch)
        entry = _withdrawal_entry([r for r in self.state.withdrawals() if r["id"] == wid][0])
        self.person_out = {"withdrawal_id": wid, "symbol": p["symbol"], "who": who, "note": note, "time": self._now()}
        self._person_line(entry, wid, self._withdrawn_seen, self.state.mark_withdrawal_journaled)
        return self._finish("withdrawn", None, None)

    def _person_line(self, entry: dict, rid: int, seen: set, mark: Callable) -> None:
        """The row stands; its journal line (synced), then the row marked journaled. A failed line: the next run."""
        try:
            self._write(entry)
            path = self._journal_file()
            if path is not None:
                _sync_file(path)
            seen.add(rid)
            mark(rid)
        except (sqlite3.Error, OSError) as exc:
            self.person_out["journal_error"] = f"{_venue_error(exc)}; the next run writes the line"

    def _finish(self, status: str, stage: str | None, reason: str | None) -> dict:
        if self.person is not None:                      # a person's command journals only its own line
            pid = self.person.get("id")
            return {"status": "refused" if status == "stopped" else status, "stage": stage, "reason": reason,
                    "pending_id": pid if type(pid) is int else None, "venue": self.name, "batch": self.batch,
                    **self.person_out}
        if self.order is not None and status == "done":
            try:
                rec = self.ledger.order(self.order["tag"])
                self.order = {**self.order, "state": rec["state"] if rec and rec.get("state") in STATES else "unknown",
                              "filled_units": _as_float(rec.get("filled_units")) if rec else None}
            except Exception:
                pass
        try:
            pending_open = [r["id"] for r in self.state.open_pending()] if self.state is not None else None
            observed = self.state.win_cash_observed() if self.state is not None else None
        except Exception:
            pending_open, observed = None, None
        summary = {"status": status, "stage": stage, "reason": reason, "venue": self.name, "batch": self.batch, "day": self.day,
                   "order": self.order, "quote": self.quote, "cancels": self.cancels, "fills_journaled": self.fills_journaled,
                   "settlements": self.settled_now, "settlements_journaled": self.settlements_journaled,
                   "buying_power": self.buying_power, "pending_open": pending_open, "win_cash_observed": observed,
                   "caps": {"contracts": MAX_CONTRACTS, "dollars": float(MAX_ORDER_DOLLARS), "orders_a_day": MAX_ORDERS_PER_DAY,
                            "quote_age_seconds": QUOTE_MAX_AGE_SECONDS, "spread": float(MAX_SPREAD), "cross": float(MAX_CROSS)}}
        try:
            if self._locked:
                if status == "stopped":
                    self._write({"event": "stopped", "stage": stage, "reason": reason})
                self._write({"event": "summary", **summary})
        except Exception as exc:
            summary["journal_error"] = _venue_error(exc)
        return summary


# ---- confirming a pending settlement (a person's command)


def confirm_settlement(*, journal, state, ledger: OrderLedger, venue, pending_id, who, note,
                       market: Callable | None = None, clock: Callable[[], float] = wall_clock) -> dict:
    """A person settles a pending record. The venue's name, environment and host first (as every run), then the inputs:
    `pending_id` a plain int from 1; `who` and `note` plain text, not empty after trimming, without control or format
    characters (who at most WHO_MAX characters, the note at most NOTE_MAX). Under the run's lock: nothing of the
    ledger open or unjournaled; the same reconcile a --reconcile-only run makes, which must show the pending name at
    exactly 0 and nothing else different (other names pending with it aside); Kalshi's record read again showing
    exactly the same settlement (judge_settlement, the time guard on the ledger's submissions included). Only then the
    settlement row (witnesses kalshi_record+person, who and the note) and the record's resolution in one write, the
    journal line (synced), the row marked journaled; a failed line is written by the next run. Any refusal journals
    nothing and changes no practice state. The command line then runs a reconcile, which takes the fresh reading the
    next settlement starts from."""
    return _Run(ledger, venue, journal, state, {}, True, None, clock, market if market is not None else kalshi_market,
                person={"kind": "confirm", "id": pending_id, "who": who, "note": note}).go()


def withdraw_pending(*, journal, state, ledger: OrderLedger, venue, pending_id, who, note,
                     clock: Callable[[], float] = wall_clock) -> dict:
    """A person closes a pending record WITHOUT a settlement, because the holding is back in the account in full. The
    same checks and inputs as confirm_settlement, and the same reconcile, which must be clean (the holding shown in
    full, nothing else different). Then a withdrawal row (who and the note) in one write, its journal line
    (`settlement_withdrawn`, synced), the row marked journaled. Any refusal journals nothing and changes no practice
    state."""
    return _Run(ledger, venue, journal, state, {}, True, None, clock, None,
                person={"kind": "withdraw", "id": pending_id, "who": who, "note": note}).go()


# ---- the command line


def events_environment(environ) -> dict:
    """A copy of the loaded settings with WEBULL_ACCOUNT_CLASS set to EVENTS_CASH and WEBULL_ACCOUNT_ID dropped (the
    account is chosen by its class), whatever the secrets file says."""
    chosen = {k: v for k, v in dict(environ).items() if k != "WEBULL_ACCOUNT_ID"}
    chosen["WEBULL_ACCOUNT_CLASS"] = EVENTS_CLASS
    return chosen


def build_events_venue(environ):
    """The Webull venue for the Events Cash account from the settings in `environ` (see events_environment)."""
    from valedia_trader.venues.webull import WebullVenue
    return WebullVenue.from_secrets(events_environment(environ))


def _loaded_environment():
    from valedia_trader.core.secrets import load_env
    load_env()
    return os.environ


def main(argv: list[str] | None = None, *, environ=None, build_venue: Callable | None = None, root: str | Path | None = None,
         book: Callable | None = None, clock: Callable[[], float] | None = None, market: Callable | None = None) -> int:
    """The command line (see the module notes). Prints the summary; 0 when the run went through, 2 when it stopped or
    was refused. A configured environment other than the test environment is refused before the venue is built, and the
    venue's name, environment and host are checked before any folder or file is made."""
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.execution_and_risk.event_practice",
                                 description="One capped event-contract practice order in Webull's paper Events account.")
    ap.add_argument("--ticker")
    ap.add_argument("--outcome", choices=["yes", "no"])
    ap.add_argument("--contracts", type=int)
    ap.add_argument("--side", choices=list(SIDES))
    ap.add_argument("--limit", help="a price with at most two decimals, such as 0.45 (default: the ask plus a cent to buy, "
                                    "the bid less a cent to sell)")
    ap.add_argument("--reconcile-only", action="store_true", help="send nothing: reconcile, cancel this ledger's open "
                                                                   "orders, journal the fills and settlements")
    ap.add_argument("--confirm-settlement", type=int, metavar="ID", help="settle pending record ID (a person has looked); "
                                                                          "needs --who and --note, then reconciles")
    ap.add_argument("--withdraw-pending", type=int, metavar="ID", help="close pending record ID without a settlement "
                                                                        "(the holding is back in full); needs --who and "
                                                                        "--note, then reconciles")
    ap.add_argument("--who", help="with --confirm-settlement or --withdraw-pending: who looked")
    ap.add_argument("--note", help="with --confirm-settlement or --withdraw-pending: what they found")
    args = ap.parse_args(argv)
    person = args.confirm_settlement is not None or args.withdraw_pending is not None
    if person:
        if (args.confirm_settlement is not None and args.withdraw_pending is not None) or args.reconcile_only \
                or any(x is not None for x in (args.ticker, args.outcome, args.contracts, args.side, args.limit)):
            ap.error("--confirm-settlement or --withdraw-pending is given alone, with --who and --note")
    elif not args.reconcile_only and None in (args.ticker, args.outcome, args.contracts, args.side):
        ap.error("--ticker, --outcome, --contracts and --side are needed unless --reconcile-only is given")
    elif args.who is not None or args.note is not None:
        ap.error("--who and --note go with --confirm-settlement or --withdraw-pending")
    from valedia_trader.core.secrets import SecretError
    from valedia_trader.venues.webull import WebullError
    try:
        env = environ if environ is not None else _loaded_environment()
        configured = (env.get("WEBULL_ENV") or "sandbox").strip().lower()
    except Exception as exc:
        print(f"refused: the trading secrets file could not be loaded ({_venue_error(exc)})", file=sys.stderr)
        return 2
    if configured != TEST_ENVIRONMENT:
        print(f"refused: event practice runs against Webull's test environment ({TEST_ENVIRONMENT}) only and WEBULL_ENV names "
              "another; live event orders are not built", file=sys.stderr)
        return 2
    try:
        venue = (build_venue or build_events_venue)(events_environment(env))
    except (WebullError, SecretError, OSError) as exc:
        print(f"refused: the Webull Events venue could not be set up ({_venue_error(exc)})", file=sys.stderr)
        return 2
    if venue is None:
        return 2
    try:                                                 # before any folder or file is made
        _Run(None, venue, None, None, {}, True, None, clock or wall_clock)._check_name_and_environment()
    except _Stop as stop:
        print(f"refused: {stop.reason}", file=sys.stderr)
        return 2
    from valedia_trader.research_loop import LoopPaths
    from valedia_trader.research_loop.paper import Journal
    paths = LoopPaths(Path(root)) if root is not None else LoopPaths()
    clock = clock or wall_clock
    ledger_path = ledger_path_for(paths.memory, venue.name)
    ledger = open_ledger(ledger_path, clock=clock)
    try:
        reconcile_only = args.reconcile_only
        if person:
            common = dict(journal=Journal(paths.journal), state=state_path_for(ledger_path, venue.name), ledger=ledger,
                          venue=venue, who=args.who, note=args.note, clock=clock)
            if args.confirm_settlement is not None:
                done = confirm_settlement(pending_id=args.confirm_settlement, market=market, **common)
            else:
                done = withdraw_pending(pending_id=args.withdraw_pending, **common)
            print(json.dumps(done, indent=2, default=str))
            if done["status"] not in ("confirmed", "withdrawn"):
                return 2
            reconcile_only = True                        # the fresh reading the next settlement starts from
        out = run_event_practice(ledger=ledger, venue=venue, journal=Journal(paths.journal),
                                 state=state_path_for(ledger_path, venue.name), ticker=args.ticker, outcome=args.outcome,
                                 contracts=args.contracts, side=args.side, limit=args.limit,
                                 reconcile_only=reconcile_only, book=book, clock=clock, market=market)
    finally:
        ledger.close()
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["status"] == "done" else 2


if __name__ == "__main__":
    sys.exit(main())
