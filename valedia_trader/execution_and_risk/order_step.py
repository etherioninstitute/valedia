"""The order step: from the paper book's decisions to orders at the broker. Test environment only.

Once per trading day, inside the regular session, `run_order_step` compares what the paper book wants to hold
with what this program owns at the broker and sends one order per symbol for the difference, through the order
ledger (execution_and_risk/orders.py). It decides nothing itself. Design: docs/specs/2026-11-13-order-step-design.md.

Three books are kept apart: the paper book (simulated, the research record, only read here); the broker book (units
per symbol this program owns at the broker: never stored, always the signed sum of the order ledger's fills); the
baseline (what the account held before the first order step: recorded once, never sold, never counted as owned).

One run, in order. A failure in steps 1 to 6 sends nothing; the reason is journaled and returned.

 1. environment  the venue must be the test environment: its `environment` a plain str equal to TEST_ENVIRONMENT
                 by a static lookup and by an ordinary getattr (a subclass, a property or an answer only through
                 __getattribute__ is refused) and, when it has a `host` (by a static lookup or an ordinary getattr, so
                 also through __getattr__), both answers a plain str of letters, digits, dots and hyphens with
                 "sandbox" as one of its dot-separated labels (so a property or __getattr__-only host, whose static
                 lookup is not a plain str, is refused); and the ledger must treat
                 exactly the adapter's NotTransmitted and OrderRejected as not sent. An Events venue is refused: a
                 name ending in "-events", or an `account_class` equal to EVENTS_CLASS by static or ordinary lookup
                 (one only a property gives is read in the session, before any other venue call: the adapter's
                 reads the account list). Checked before any venue call,
                 and read again before each order is intended and immediately before each submit_once; any failure
                 stops the run with no further venue call.
    lock         one run at a time per state file. Only the venue's name, environment, host and (when it is not a
                 property) account class are read before the lock; everything below runs under it, and once it is held a summary is always journaled: a fault
                 of the run's own stops it at stage "unexpected" rather than raising. A run stopped before the lock
                 (clock, environment, lock taken) only returns its summary: it writes nothing anywhere. Every
                 journal write happens under the lock, after the journal scan.
    journal      every journal file is read line by line before any venue call. A torn last line of the newest file
                 (a crash mid-write) is skipped, recorded once and reported in the summary (`torn_lines`); a torn
                 line anywhere else stops the run. The torn line stays in the file, and the journal's other readers
                 (Journal.read, used by the weekly critic and the tax notes) still stop on it; that repair belongs to
                 paper.py, in a later ticket.
 2. session      inside the regular session of a trading day, SESSION_AFTER_OPEN after the open to
                 SESSION_BEFORE_CLOSE before the close (09:35 to 15:45 New York time on a full day; earlier on a
                 half-day), from the exchange calendar. Outside it nothing is read from the venue.
    paper book   read now, and followed only when its last day is the exchange session before the run's day or the
                 run's day itself; an older (or newer, or unnamed) book stops the run before any venue call.
 3. baseline     recorded from the venue's positions when none is recorded and the ledger has no orders; with no
                 baseline and orders on the ledger, the run stops.
 4. reconcile    reconcile (positions given: baseline plus the fills already applied; the ledger adds the rest);
                 ask the venue to cancel every order of this ledger still open; reconcile again. Every new fill is
                 journaled once (keyed by its fill id), the journal file fsynced, and only then marked applied. A
                 blocked ledger, or an order still open at the broker, stops the run.
 5. targets      per symbol, each strategy's paper units held plus pending, summed, times capital / paper equity.
 6. prices       one quote per symbol with its time; older than QUOTE_MAX_AGE_SECONDS, missing, a time whose
                 timestamp() is not finite or raises, or a price not above 0 or above MAX_QUOTE_PRICE: no order for
                 that symbol. The venue's cash, and the broker equity (cash
                 plus holdings at the quotes), must be finite numbers from 0 to MAX_CASH; the venue's settled cash,
                 when it gives one (the key present and not None), a finite number from 0 to MAX_CASH and at most
                 the cash plus SETTLED_ABOVE_CASH (a cent); and every held symbol must
                 have a usable quote; otherwise the run stops before any order and records no equity or peak. A
                 position above MAX_UNITS in size is unusable too (the baseline is not recorded; a broker book that
                 large stops the run).
 7. differences  target minus broker book, rounded toward zero (whole shares, or FRACTIONAL_DECIMALS decimals with
                 `fractional`); a sale never exceeds the broker book; less than the paper trader's smallest trade
                 is dropped. The strategies' parts of each order sum to it exactly (order_parts).
 8. risk gate    risk_gate.check_order against the broker account as it is at that moment, priced at the quotes in
                 hand (equity = the venue's cash as read plus every holding, the baseline and the broker book with
                 this run's fills so far, at its latest usable quote, so a quote asked for again in step 10 prices
                 the account too; before each call every holding whose quote in hand is older than
                 QUOTE_MAX_AGE_SECONDS is asked for again, in the same single ask as the order's own quote, but not
                 within QUOTE_ASK_GAP_SECONDS of its last ask in the run (the order's own quote is always asked when
                 too old), so a run makes at most 1 + 2 requests per order considered; when a holding still has no
                 usable fresh quote the account cannot be priced: an order that adds risk is left over with that
                 reason, a sale that reduces risk is still judged, the stale holding kept at its last usable price;
                 the day's loss against the equity recorded at the last run on an earlier day; the
                 fall from the peak of the earlier runs and this equity; positions held), latch and halt files
                 honoured. The run's own equity row is kept at the same prices (set again whenever they change), so
                 the next run's day's loss and peak use them.
 9. caps         sales first, then purchases, largest value first; purchases stop once the cash the venue reports
                 (or its settled cash, when given and smaller), less CASH_KEPT, would be exceeded at the limit
                 prices, so a purchase never spends unsettled money: a sale's proceeds (this run's or an earlier
                 run's that day) reach the cash at once but not the settled cash. Settled cash is compared with
                 cash in whole cents. Each purchase left over for money is labelled by the budget that stops it,
                 judged against what both have left at that moment (the cash budget as if it had also paid the
                 purchases already counted as left for lack of settled money): over the settled budget only, it is
                 "settled cash" and its cost at the limit price is added to the summary's
                 `purchases_left_unsettled`; over the cash budget only, "cash"; over both, the one with less left;
                 one that fits both after purchases stopped keeps the label of the purchase that stopped them. The
                 summary also gives the `cash` and `settled_cash` read; at most max_orders_per_day sent in one New
                 York day across runs. What is not sent is journaled, not queued.
10. send         an order above MAX_UNITS in size is left over, never sent. For each other order, in this order:
                  a. the quote's age on the clock: older than QUOTE_MAX_AGE_SECONDS, the quote function is asked
                     once more for that symbol (with the holdings of step 8, in one ask), and an unusable answer
                     leaves the order over;
                  b. the risk gate, at that quote (the limit price, order value, cash check and position value all
                     come from it);
                  c. the environment and host read;
                  d. the quote's age again: too old, back to (a) once; too old the second time, the order is left
                     over. What is sent always uses the quote of the last round's (b), the one the gate judged;
                  e. the window check, then ledger.intend;
                  f. the environment and host read; a last look: the quote's age once more, the halt file and (for an
                     order that adds risk) the latch file; the window check again, then ledger.submit_once.
                 Nothing that can take time sits between a window check and the call it guards; an order failing the
                 last look or the second window check stays `intended`, never sent, and is reported left over with
                 the reason. A day limit order in the regular session. A refusal is journaled and the run goes on;
                 an unknown outcome stops the run at once. Nothing a venue or the quote function returns or raises makes the run raise: it becomes a
                 stop or a left-over order, in the order step's own words.
11. after        reconcile once more, journal the fills, return and journal a summary.

Points to know:
 - A halt or latch file is there unless it is plainly absent (risk_gate.file_is_there, the one rule the gate and
   the last look share): a symlink (also one to nothing or to itself), a path under a regular file, a path with a
   NUL byte and any path whose check raises are there; only a clean "no such file" is absent.
 - Every sale is priced at the quote less SELL_BELOW_REDUCING (2%): the program never sells more than it owns, so
   every sale reduces a holding and counts as reducing risk.
 - reconcile is given the baseline plus the fills already applied; the ledger adds the fills not yet applied.
 - The baseline counts toward the broker equity, the position count and the position limit of the risk gate,
   though it is never sold and never counted as this program's.
 - The ledger and the state file are kept per venue name (ledger_path_for, state_path_for), not per account: one
   account needs one home folder; two order steps on one account would each find the other's fills in the
   venue's positions and stop at the reconcile.
 - An order left `intended` by a crash (between intend and submit_once) stays `intended`; it is never sent later,
   and settle_sends records it as not sent.
 - Only the order step's own words reach the journal, the summary and the state file: from a venue error, its class
   name when that is a plain identifier and its `code` when that is capitals, digits and underscores (up to 64);
   from a venue answer, only fields the order step names itself (a state or status word from a fixed list,
   numbers, plain symbols and tags). The ledger's own file keeps the venue's text.
 - ledger.submit_once reads the venue's name and waits for its own write lock before it calls the venue, so with
   slow venue attributes the venue call can begin after the window check (the real adapter's attributes are plain).
 - A second kill while the torn-line record is being written, or another program appending to the journal after a
   torn order-step line, still stops the step until a person looks.

The order step keeps a small sqlite state file (OrderStepState) beside the ledger, one per venue name: the baseline,
the equity of each run, and the orders sent each day. Every write is one transaction. Everything a run needs is
passed in; nothing here builds a broker client.
"""
from __future__ import annotations

import inspect
import json
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Callable

import pandas as pd

from valedia_trader.execution_and_risk.orders import STATES, OrderError, OrderLedger, OrdersBlocked, allocate, net_orders
from valedia_trader.research_loop import risk_gate
from valedia_trader.venues.webull import EVENTS_CLASS, NotTransmitted, OrderRejected

__all__ = ["TEST_ENVIRONMENT", "NOT_SENT", "PaperTargets", "OrderStepState", "OrderStepError", "run_order_step",
           "paper_targets", "open_ledger", "ledger_path_for", "state_path_for", "broker_book", "targets", "strategy_holdings",
           "round_units", "limit_price", "market_window", "max_orders_per_day", "minute_quotes", "wall_clock"]

TEST_ENVIRONMENT = "sandbox"                      # the only environment a run accepts (WebullVenue.environment)
NOT_SENT = (NotTransmitted, OrderRejected)        # the adapter's answers that mean no order exists
NEW_YORK = "America/New_York"
SESSION_AFTER_OPEN = pd.Timedelta(minutes=5)      # 09:35 on a 09:30 open
SESSION_BEFORE_CLOSE = pd.Timedelta(minutes=15)   # 15:45 on a 16:00 close, 12:45 on a 13:00 half-day
QUOTE_MAX_AGE_SECONDS = 20 * 60
QUOTE_MAX_AHEAD_SECONDS = 60                      # a quote stamped later than this after now is not believed
QUOTE_ASK_GAP_SECONDS = 60                        # a holding is not asked for again sooner than this in one run
MAX_CASH = 1e12                                   # cash and equity outside 0 to this are unusable, like a non-finite one
MAX_QUOTE_PRICE = 1e7                             # a quote price must be above 0 and at most this
MAX_UNITS = 1e12                                  # a position size above this (in size) is unusable
_EQUITY_BOUNDS = f"a finite number from 0 to {MAX_CASH:,.0f}"
DEFAULT_MAX_ORDERS_PER_DAY = 40
BUY_ABOVE = Decimal("0.01")                       # purchase limit: quote plus 1%, rounded down to the cent
SELL_BELOW = Decimal("0.01")                      # sale limit: quote less 1%, rounded up to the cent
SELL_BELOW_REDUCING = Decimal("0.02")             # ... less 2% when the sale reduces risk
SETTLED_ABOVE_CASH = 0.01                         # settled cash may exceed cash by at most a cent (rounding)
CASH_KEPT = 0.01                                  # purchases may use the venue's cash less 1%
FRACTIONAL_DECIMALS = 5
OPEN_STATES = ("accepted", "partly_filled", "sent", "unknown")   # orders that may still be open at the broker
UNITS_EPS = 1e-12
CENT = Decimal("0.01")
MAX_TEXT = 200


class OrderStepError(RuntimeError):
    pass


def wall_clock() -> float:
    """The time in seconds; the command line passes this to a run (tests pass their own clock)."""
    return time.time()


def _describe(exc: BaseException) -> str:
    try:
        text = str(exc)
    except Exception:
        text = ""
    return f"{type(exc).__name__}: {text[:MAX_TEXT]}" if text else type(exc).__name__


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_CODE = re.compile(r"[A-Z0-9_]{1,64}")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SYMBOL = re.compile(r"[A-Za-z0-9./-]{1,24}")
_TAG = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
CANCEL_WORDS = ("cancel requested", "not yet", "already finished", "no such order")   # WebullVenue.cancel's words
STATUS_WORDS = ("accepted", "pending", "open", "rejected", "refused", "unknown")      # a submit answer's status words


def _plain(pattern: re.Pattern, x) -> bool:
    return type(x) is str and pattern.fullmatch(x) is not None


def _venue_error(exc: BaseException) -> str:
    """An error from the venue or the quote function in the order step's own words: its class name when that is a
    plain identifier, and its `code` when that is capitals, digits and underscores (up to 64). Never its text."""
    name = type(exc).__name__
    words = name if _plain(_IDENTIFIER, name) else "an exception"
    try:
        code = getattr(exc, "code", None)
    except Exception:
        code = None
    return f"{words} (code {code})" if _plain(_CODE, code) else words


def _local_error(exc: BaseException) -> str:
    """An error of the ledger, the state file or the journal. A blocked ledger's reasons can hold the venue's words,
    so they are not repeated."""
    if isinstance(exc, OrdersBlocked):
        return "OrdersBlocked (the order ledger is blocked)"
    return _describe(exc)


_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")


def _sandbox_host(host) -> bool:
    """A plain str of letters, digits, dots and hyphens with `sandbox` as one of its dot-separated labels."""
    return _plain(_HOST, host) and TEST_ENVIRONMENT in host.split(".")


def _status_word(answer) -> str:
    try:
        word = answer.get("status") if isinstance(answer, dict) else None
    except Exception:
        word = None
    return repr(word) if type(word) is str and word in STATUS_WORDS else "an answer without a status word"


def _number_or_none(x):
    return _as_float(x)


def _settled_figure(cash: float, raw) -> tuple[float | None, bool]:
    """(the settled cash, unusable?) from the venue's answer. None (key missing or None) is no figure: (None, False).
    Otherwise a finite number (or its text) from 0 to MAX_CASH and, compared in whole cents, at most the cash plus
    SETTLED_ABOVE_CASH; anything else is (None, True)."""
    if raw is None:
        return None, False
    try:
        v = _as_float(float(raw) if isinstance(raw, str) else raw)
    except Exception:
        return None, True
    if v is None or not 0.0 <= v <= MAX_CASH or round(v * 100) > round(cash * 100) + round(SETTLED_ABOVE_CASH * 100):
        return None, True
    return v, False


_present = risk_gate.file_is_there                 # one rule for the halt and latch files, shared with the gate


def _is_events(account_class) -> bool:
    """Whether an account class names the Events account (EVENTS_CLASS), whatever its case or surrounding blanks."""
    return isinstance(account_class, str) and str.upper(str.strip(account_class)) == EVENTS_CLASS


EVENTS_REFUSAL = (f"the order step does not trade an Events account ({EVENTS_CLASS}, or a venue named ...-events): "
                  "event contracts go through the event-orders command and its own ledger")


def events_refusal(venue, name=None) -> str | None:
    """Why the order step refuses this venue as an Events venue, or None: its name ends in "-events", or its
    `account_class` is EVENTS_CLASS by static lookup, or by ordinary lookup (also through __getattr__) when the
    static lookup finds no descriptor. A descriptor's class (the adapter's property reads the account list over the
    network) is not read here: the order step reads it in the session. A name or class that cannot be looked up is
    refused too. Reads nothing else; the command line uses it before any file is made."""
    try:
        name = venue.name if name is None else name
    except Exception:
        name = None
    if isinstance(name, str) and str.lower(name).endswith("-events"):
        return EVENTS_REFUSAL
    try:
        static = inspect.getattr_static(venue, "account_class", _ABSENT)
    except Exception:
        return "the venue's account class could not be looked up"
    if _is_events(static):
        return EVENTS_REFUSAL
    if static is not _ABSENT and hasattr(type(static), "__get__"):
        return None
    try:
        ordinary = getattr(venue, "account_class", None)
    except Exception:
        return "the venue's account class could not be read"
    return EVENTS_REFUSAL if _is_events(ordinary) else None


def _sync_file(path: str | Path) -> None:
    """Make what was written to `path` durable: fsync the file (F_FULLFSYNC where the system has it), then its folder."""
    import fcntl
    for target, flags in ((str(path), os.O_RDONLY), (os.path.dirname(str(path)) or ".", os.O_RDONLY)):
        fd = os.open(target, flags)
        try:
            try:
                fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            except (AttributeError, OSError):
                os.fsync(fd)
        finally:
            os.close(fd)


def _as_float(x) -> float | None:
    """A plain finite float from an int or a float (subclasses included), else None. Never raises, whatever `x`'s
    methods do."""
    try:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        v = float(x)
        return v if type(v) is float and math.isfinite(v) else None
    except Exception:
        return None


def _finite(x) -> bool:
    return _as_float(x) is not None


def _is_crypto(symbol: str) -> bool:
    return symbol.upper().endswith("-USD")


# ---- pure pieces


@dataclass(frozen=True)
class PaperTargets:
    """What the paper book wants: per strategy and symbol, units held plus pending (PaperTrader.wanted_units); the
    paper equity those units were sized against; the capital per strategy (the paper step's `share`); the paper
    trader's `min_trade_pct`; and the paper book's last day (YYYY-MM-DD). A run follows only a book whose day is
    the session before the run's day or the run's day itself."""
    wanted: dict
    equity: float
    share: float
    min_trade_pct: float
    day: str | None = None


def paper_targets(trader) -> PaperTargets:
    """The paper targets of a PaperTrader, as of its latest day."""
    history = trader.state.get("history") or []
    if not history:
        raise OrderStepError("the paper book has not run a day yet; there is nothing to follow")
    last = history[-1]
    return PaperTargets(wanted=trader.wanted_units(), equity=float(last["equity"]),
                        share=float(last.get("capital_per_strategy") or last["equity"]),
                        min_trade_pct=float(trader.min_trade_pct), day=trader.state.get("last_day"))


def _check_paper(p) -> PaperTargets:
    if not isinstance(p, PaperTargets):
        raise OrderStepError(f"the paper targets must be PaperTargets, not {type(p).__name__}")
    for name in ("equity", "share"):
        v = getattr(p, name)
        if not _finite(v) or v <= 0:
            raise OrderStepError(f"the paper {name} must be a positive finite number, not {v!r}")
    if not _finite(p.min_trade_pct) or p.min_trade_pct < 0:
        raise OrderStepError(f"min_trade_pct must be a finite number at least 0, not {p.min_trade_pct!r}")
    if not isinstance(p.wanted, dict):
        raise OrderStepError("the paper's wanted units must be a dict of strategies")
    for strategy, book in p.wanted.items():
        if not isinstance(strategy, str) or not isinstance(book, dict):
            raise OrderStepError(f"the paper's wanted units for {strategy!r} are not a dict of symbols")
        for sym, units in book.items():
            if not isinstance(sym, str) or not sym or not _finite(units):
                raise OrderStepError(f"the paper wants {units!r} of {sym!r} for {strategy}: not a symbol and a finite number")
    return p


def targets(paper: PaperTargets, capital: float) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """(per symbol, per strategy and symbol) target units at the broker: the paper's wanted units times
    capital / paper equity."""
    scale = float(capital) / float(paper.equity)
    per_strategy = {s: {sym: float(u) * scale for sym, u in book.items()} for s, book in paper.wanted.items()}
    per_symbol: dict[str, list[float]] = {}
    for book in per_strategy.values():
        for sym, u in book.items():
            per_symbol.setdefault(sym, []).append(u)
    return {sym: math.fsum(us) for sym, us in per_symbol.items()}, per_strategy


def broker_book(fills) -> dict[str, float]:
    """Units per symbol this program owns at the broker: the signed sum of the given ledger fill rows."""
    by: dict[str, list[float]] = {}
    for f in fills:
        by.setdefault(f["symbol"], []).append(float(f["units"]))
    return {sym: math.fsum(us) for sym, us in by.items()}


UNATTRIBUTED = "(unattributed)"


def strategy_holdings(orders) -> dict[str, dict[str, float]]:
    """Per strategy and symbol, the units the broker filled for it: each ledger order's filled amount shared out to
    its parts with orders.allocate. An order without usable parts counts for UNATTRIBUTED. Summed over strategies,
    a symbol's holdings are its broker book."""
    out: dict[str, dict[str, list[float]]] = {}
    for o in orders:
        filled = float(o.get("filled_units") or 0.0)
        if filled <= 0:
            continue
        signed = math.copysign(filled, o["units"])
        try:
            shares = allocate(o["parts"], signed) if o.get("parts") else {UNATTRIBUTED: signed}
        except (OrderError, KeyError, TypeError):
            shares = {UNATTRIBUTED: signed}
        for strategy, units in shares.items():
            out.setdefault(strategy, {}).setdefault(o["symbol"], []).append(float(units))
    return {s: {sym: math.fsum(us) for sym, us in book.items()} for s, book in out.items()}


def order_parts(netted_parts, units: float) -> list[dict]:
    """The strategies' parts of an order of `units`, sorted by strategy (orders.allocate), with the rounding absorbed
    so that the parts' exact sum is `units` (then sum, math.fsum and a plain running total all give `units`).

    First the last part absorbs it: every other part is put on a power-of-two grid just fine enough that each
    running total is exact (moving it by at most half a unit in the last place of the largest running total) and
    the last part is `units` less their sum; failing that, the last part alone is `units` less the others' exact
    sum. That covers every whole-share order and every fractional order whose parts stay within the order's own
    power of two. When the last part cannot, the smallest part absorbs it. No parts can sum exactly when `units`
    is not a multiple of the finest unit in the last place among the parts (a fractional order with crossing parts
    far larger than itself); those parts are left as allocate gives them, their sum within a few units in the last
    place of the largest part."""
    shares = sorted(allocate(netted_parts, units).items())
    if not shares:
        return []
    units = float(units)
    vals = [float(u) for _, u in shares]

    def exact(vs):
        return math.fsum([*vs, -units]) == 0.0        # fsum is correctly rounded: 0.0 only when the sum is exact

    def done(vs):
        return [{"strategy": s, "units": u} for (s, _), u in zip(shares, vs)]
    reach, running = [abs(units)], 0.0
    for v in vals[:-1]:
        running += v
        reach += [abs(v), abs(running), abs(units - running)]
    top = math.frexp(max(reach))[1]                      # every value reached is below 2**top
    for widen in range(3):
        g = math.ldexp(1.0, top + widen - 53)
        if not (units / g).is_integer():
            break
        head = [round(v / g) * g for v in vals[:-1]]
        total = 0.0
        for v in head:
            total += v
        if exact([*head, units - total]):
            return done([*head, units - total])
    for j in [len(vals) - 1] + sorted(range(len(vals) - 1), key=lambda i: (abs(vals[i]), i)):
        others = vals[:j] + vals[j + 1:]
        trial = vals[:j] + [math.fsum([units, *(-v for v in others)])] + vals[j + 1:]
        if exact(trial):
            return done(trial)
    return done(vals)


def round_units(units: float, fractional: bool = False) -> float:
    """Toward zero: whole shares, or FRACTIONAL_DECIMALS decimals when `fractional`. Refuses a non-finite number."""
    if not _finite(units):
        raise OrderStepError(f"units must be a finite number, not {units!r}")
    if not fractional:
        return float(math.trunc(float(units)))
    step = Decimal(1).scaleb(-FRACTIONAL_DECIMALS)
    return float(Decimal(repr(float(units))).quantize(step, rounding=ROUND_DOWN))


def limit_price(units: float, quote: float, reduces_risk: bool = False) -> float | None:
    """A purchase at the quote plus 1% rounded down to the cent; a sale at the quote less 1% (less 2% when it reduces
    risk) rounded up to the cent. None when the result is not a positive price."""
    if not _finite(quote) or quote <= 0 or not _finite(units) or units == 0:
        return None
    q = Decimal(repr(float(quote)))
    if units > 0:
        price = (q * (1 + BUY_ABOVE)).quantize(CENT, rounding=ROUND_FLOOR)
    else:
        price = (q * (1 - (SELL_BELOW_REDUCING if reduces_risk else SELL_BELOW))).quantize(CENT, rounding=ROUND_CEILING)
    return float(price) if price > 0 else None


def max_orders_per_day(limits: dict | None) -> int:
    """The limits file's optional max_orders_per_day, else DEFAULT_MAX_ORDERS_PER_DAY."""
    if limits and "max_orders_per_day" in limits:
        return int(limits["max_orders_per_day"])
    return DEFAULT_MAX_ORDERS_PER_DAY


def _default_calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS")


def market_window(calendar, now: float) -> tuple[float | None, float | None, str | None]:
    """(start, end, None) in seconds when `now` falls on a trading day of `calendar`; (None, None, why) when it does
    not. The window runs SESSION_AFTER_OPEN after the open to SESSION_BEFORE_CLOSE before the close."""
    ny = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
    day = str(ny.date())
    try:
        if not calendar.is_session(day):
            return None, None, f"{day} ({ny.day_name()}) is not a trading day"
        start = pd.Timestamp(calendar.session_open(day)) + SESSION_AFTER_OPEN
        end = pd.Timestamp(calendar.session_close(day)) - SESSION_BEFORE_CLOSE
    except Exception as exc:
        return None, None, f"the exchange calendar could not answer for {day}: {_describe(exc)}"
    return start.timestamp(), end.timestamp(), None


def _hhmm(seconds: float) -> str:
    return pd.Timestamp(seconds, unit="s", tz="UTC").tz_convert(NEW_YORK).strftime("%H:%M")


def _quote_time(t) -> float | None:
    if _as_float(t) is not None:
        return _as_float(t)
    if isinstance(t, datetime):                       # pandas Timestamps are datetimes too
        if t.tzinfo is None:
            return None                               # a time without a zone cannot be compared with the clock
        try:
            return _as_float(t.timestamp())           # a timestamp that is not finite, or raises: no usable time
        except Exception:
            return None
    return None


def _read_quote(raw, now: float) -> tuple[float | None, float | None, str | None]:
    """(price, time, None) for a usable quote, else (None, None, why), in the order step's own words. Never raises,
    whatever the quote's methods do."""
    try:
        return _read_quote_unguarded(raw, now)
    except Exception:
        return None, None, "the quote could not be read"


def _read_quote_unguarded(raw, now: float) -> tuple[float | None, float | None, str | None]:
    if raw is None:
        return None, None, "no quote"
    try:
        price, when = (raw["price"], raw["time"]) if isinstance(raw, dict) else (raw[0], raw[1])
    except Exception:
        return None, None, "the quote is not a price and a time"
    price = _as_float(price)
    if price is None or price <= 0 or price > MAX_QUOTE_PRICE:
        return None, None, f"the quote's price is not a positive number up to {MAX_QUOTE_PRICE:,.0f}"
    try:
        t = _quote_time(when)
    except Exception:
        t = None
    if t is None:
        return None, None, "the quote's time is not usable"
    age = now - t
    if age > QUOTE_MAX_AGE_SECONDS:
        return None, None, f"the quote is {age / 60:.1f} minutes old, more than {QUOTE_MAX_AGE_SECONDS // 60}"
    if age < -QUOTE_MAX_AHEAD_SECONDS:
        return None, None, f"the quote is stamped {-age:.0f} s after now"
    return price, t, None


def usable_quote(raw, now: float) -> tuple[float | None, str | None]:
    """(price, None) for a usable quote, else (None, why). A quote is (price, time) or {"price", "time"}; the time
    is seconds or a datetime with a zone."""
    price, _, why = _read_quote(raw, now)
    return price, why


def minute_quotes(symbols) -> dict:
    """The default quote function: the last one-minute bar of today from the price source the repository already
    uses (yfinance), as {symbol: (close, time of the bar in seconds)}. A symbol without a bar is left out. No test
    calls this; it reaches the network."""
    import yfinance as yf
    symbols = list(symbols)
    if not symbols:
        return {}
    raw = yf.download(symbols, period="1d", interval="1m", auto_adjust=False, progress=False, threads=False)
    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    out = {}
    for sym in symbols:
        if sym not in close.columns:
            continue
        s = close[sym].dropna()
        if s.empty:
            continue
        ts = pd.Timestamp(s.index[-1])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        out[sym] = (float(s.iloc[-1]), float(ts.timestamp()))
    return out


# ---- files


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name)) or "venue"


def ledger_path_for(directory: str | Path, venue_name: str) -> Path:
    """The order ledger's file for a venue in `directory`: one ledger per venue name (Webull's carry the environment)."""
    return Path(directory) / f"orders-{_safe_name(venue_name)}.sqlite"


def state_path_for(ledger_path: str | Path, venue_name: str) -> Path:
    """The order step's state file for a venue: beside the order ledger's file."""
    return Path(ledger_path).with_name(f"order_step-{_safe_name(venue_name)}.sqlite")


def open_ledger(path: str | Path, clock: Callable[[], float] = time.time) -> OrderLedger:
    """An order ledger that treats the adapter's NotTransmitted and OrderRejected as not sent."""
    return OrderLedger(path, not_sent=NOT_SENT, clock=clock)


STATE_LAYOUT = 1
STATE_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, units REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS equity (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, time REAL NOT NULL, "
    "equity REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends (tag TEXT PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, outcome TEXT)",
]
NOT_COUNTED = ("refused", "not sent")             # outcomes that are not an order sent


class OrderStepState:
    """The order step's own small file: the baseline (recorded once), the equity at each run, and each order handed
    to the ledger for sending, with its outcome. Every write is one IMMEDIATE transaction, so a crash at any point
    leaves the file as it was before that write or after it."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self.path, self.clock = str(path), clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        try:
            self._db.execute("PRAGMA synchronous = FULL")
            with self._write():
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                tables.discard("sqlite_sequence")
                if tables and (version != STATE_LAYOUT or not tables <= {"meta", "baseline", "equity", "sends"}):
                    raise OrderStepError(f"{path} is not an order-step state file of layout {STATE_LAYOUT}; it is not opened")
                if not tables:
                    if version != 0:
                        raise OrderStepError(f"{path} is a database of another kind; it is not opened")
                    for statement in STATE_SCHEMA:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {STATE_LAYOUT}")
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
            if not _plain(_SYMBOL, sym) or not _finite(units) or units < 0:
                raise OrderStepError("the baseline must be plain symbols with finite numbers at least 0")
            if units > 0:
                clean[sym] = float(units)
        with self._write():
            if self._db.execute("SELECT 1 FROM meta WHERE key = 'baseline_recorded'").fetchone():
                raise OrderStepError("a baseline is already recorded; it is recorded once")
            self._db.executemany("INSERT INTO baseline (symbol, units) VALUES (?, ?)", sorted(clean.items()))
            self._db.execute("INSERT INTO meta (key, value) VALUES ('baseline_recorded', ?)", (repr(float(self.clock())),))
        return clean

    def previous_equity(self, day: str) -> float | None:
        """The equity recorded at the latest run on a day before `day`."""
        row = self._db.execute("SELECT equity FROM equity WHERE day < ? ORDER BY day DESC, id DESC LIMIT 1", (day,)).fetchone()
        return float(row[0]) if row else None

    def peak(self) -> float | None:
        row = self._db.execute("SELECT MAX(equity) FROM equity").fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def record_equity(self, day: str, equity: float) -> int:
        """Record a run's equity; returns the row's id (for reprice_equity)."""
        if not _finite(equity):
            raise OrderStepError(f"equity must be a finite number, not {equity!r}")
        with self._write():
            cur = self._db.execute("INSERT INTO equity (day, time, equity) VALUES (?, ?, ?)", (day, float(self.clock()), float(equity)))
        return int(cur.lastrowid)

    def reprice_equity(self, row: int, equity: float) -> None:
        """Set the equity of this run's row again, when a quote in hand has changed during the run."""
        if not _finite(equity):
            raise OrderStepError(f"equity must be a finite number, not {equity!r}")
        with self._write():
            self._db.execute("UPDATE equity SET time = ?, equity = ? WHERE id = ?", (float(self.clock()), float(equity), int(row)))

    def sent_on(self, day: str) -> int:
        """Orders handed to the ledger for sending on `day` and not known to have stayed unsent. An order whose outcome
        was never written (a crash during the call) counts until settle_sends says otherwise."""
        marks = ",".join("?" * len(NOT_COUNTED))
        return self._db.execute(f"SELECT COUNT(*) FROM sends WHERE day = ? AND (outcome IS NULL OR outcome NOT IN ({marks}))",
                                (day, *NOT_COUNTED)).fetchone()[0]

    def note_sending(self, tag: str, day: str) -> None:
        with self._write():
            self._db.execute("INSERT INTO sends (tag, day, time, outcome) VALUES (?, ?, ?, NULL)", (tag, day, float(self.clock())))

    def note_outcome(self, tag: str, outcome: str) -> None:
        with self._write():
            self._db.execute("UPDATE sends SET outcome = ? WHERE tag = ?", (outcome, tag))

    def settle_sends(self, ledger: OrderLedger) -> int:
        """Give every send whose outcome was never written the outcome the ledger now shows: an order still `intended`
        was never handed to the venue ("not sent"). Returns how many were settled."""
        rows = [r[0] for r in self._db.execute("SELECT tag FROM sends WHERE outcome IS NULL")]
        n = 0
        for tag in rows:
            rec = ledger.order(tag)
            outcome = "not sent" if rec is None or rec["state"] == "intended" else rec["state"]
            self.note_outcome(tag, outcome)
            n += 1
        return n


class _RunLock:
    """One run at a time per state file: an exclusive lock on a file beside it, released when the process ends."""

    def __init__(self, state_path: str) -> None:
        self.fh = None
        self.path = None if state_path == ":memory:" else state_path + ".lock"

    def acquire(self) -> bool:
        if self.path is None:
            return True
        import fcntl
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def release(self) -> None:
        if self.fh is not None:
            import fcntl
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()
                self.fh = None


# ---- one run


class _Stop(Exception):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage, self.reason = stage, reason


class _SubmitWatch:
    """The venue as handed to ledger.submit_once: it remembers the call's exception or answer, so that what the run
    writes about the outcome comes from the order step's own words, not from the venue's text in the ledger's note."""

    def __init__(self, venue) -> None:
        self._venue = venue
        self.called, self.error, self.answer = False, None, None

    def submit(self, *args, **kwargs):
        self.called = True
        try:
            self.answer = self._venue.submit(*args, **kwargs)
        except BaseException as exc:
            self.error = exc
            raise
        return self.answer

    def __getattr__(self, name):
        return getattr(self._venue, name)


_ABSENT = object()


def run_order_step(*, ledger: OrderLedger, venue, paper, quotes: Callable, journal, limits_file, state,
                   clock: Callable[[], float] = wall_clock, calendar=None, halt_file=risk_gate.HALT_FILE,
                   latch_file=None, capital: float | None = None, fractional: bool = False) -> dict:
    """One order step (see the module notes). `paper` is PaperTargets or a callable giving them; `quotes(symbols)`
    gives {symbol: (price, time)}; `state` is an OrderStepState or the path of its file; `calendar` answers
    is_session, session_open and session_close (default: the XNYS calendar). Returns the summary, also journaled.
    Never raises because of the venue, the quotes or the paper book; a crash of the process mid-run is safe."""
    return _Run(ledger, venue, paper, quotes, journal, limits_file, state, clock, calendar, halt_file, latch_file,
                capital, fractional).go()


class _Run:
    def __init__(self, ledger, venue, paper, quotes, journal, limits_file, state, clock, calendar, halt_file,
                 latch_file, capital, fractional) -> None:
        self.ledger, self.venue, self.paper, self.quotes, self.journal = ledger, venue, paper, quotes, journal
        self.limits_file, self.state_arg, self.clock, self.calendar = limits_file, state, clock, calendar
        self.halt_file, self.latch_file, self.capital, self.fractional = halt_file, latch_file, capital, bool(fractional)
        self.name = "?"
        self.batch = None
        self.day = None
        self.state: OrderStepState | None = None
        self.sent: list[dict] = []
        self.refused: list[dict] = []
        self.left_over: list[dict] = []
        self.skipped: list[dict] = []
        self.cancels: list[dict] = []
        self.fills_journaled = 0
        self.target_units: dict[str, float] = {}
        self.broker_equity = None
        self.paper_equity = None
        self.cash_read = None                            # the venue's cash and settled cash as read, when usable
        self.settled_cash = None
        self._settled_unusable = False
        self.purchases_left_unsettled = 0.0
        self.window_end = None
        self._seen_fill_ids: set | None = None
        self.quote_times: dict[str, float] = {}
        self.asked: dict[str, float] = {}               # when each symbol was last asked of the quote function
        self.torn_lines: list[dict] = []
        self.paper_read: PaperTargets | None = None
        self._line_ended = False
        self._locked = False
        self._class_later = False

    # -- helpers
    def _write(self, entry: dict) -> None:
        if not self._locked:                             # a run without the lock writes nothing anywhere
            raise RuntimeError("the order step wrote to the journal without holding the lock")
        if not self._line_ended:                         # a torn last line from a crash must not swallow this entry
            self._line_ended = True
            path = self._journal_file(self.day)
            try:
                if path is not None and path.exists() and path.stat().st_size:
                    with path.open("rb") as fh:
                        fh.seek(-1, os.SEEK_END)
                        ended = fh.read(1) == b"\n"
                    if not ended:
                        with path.open("ab") as fh:
                            fh.write(b"\n")
            except OSError:
                pass                                     # the write below then reports the journal's trouble
        self.journal.write(self.day, {"type": "order_step", "venue": self.name, "batch": self.batch, **entry})

    def _sync_journal(self) -> None:
        path = self._journal_file(self.day)
        if path is not None:
            _sync_file(path)

    def _now(self) -> float:
        now = self.clock()
        if not _finite(now):
            raise _Stop("clock", f"the clock gave {now!r}, not a finite number of seconds")
        return float(now)

    def go(self) -> dict:
        try:
            now = self._now()
            self.day = str(pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK).date())
        except _Stop as stop:
            self.day = str(pd.Timestamp.now(tz=NEW_YORK).date())
            return self._finish("stopped", stop.stage, stop.reason)
        stamp = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
        self.batch = f"os-{stamp:%Y%m%d-%H%M%S}-{os.urandom(3).hex()}"
        try:
            self._environment()                          # 1: the venue's name, environment and host only
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        path = self.state_arg.path if isinstance(self.state_arg, OrderStepState) else str(self.state_arg)
        lock = _RunLock(path)
        if not lock.acquire():                           # everything below runs under the lock
            return self._finish("stopped", "session", "another order step is running on this venue's state file")
        self._locked = True                              # from here on the journal may be written
        try:
            try:
                self._scan_journal()                     # before anything is asked of the venue
                self._session(now)                       # 2
                self._check_events_class()               # ... an account class only a property gives, read now
                self.paper_read = self._paper()          # 5, read now: a stale book stops before any venue call
                self._paper_day(self.paper_read)
            except _Stop as stop:
                return self._finish("stopped", stop.stage, stop.reason)
            try:
                self.state = self.state_arg if isinstance(self.state_arg, OrderStepState) else OrderStepState(path, self.clock)
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", "baseline", f"the order step's state file could not be opened: {_describe(exc)}")
            try:
                return self._rest(now)
            finally:
                if not isinstance(self.state_arg, OrderStepState):
                    self.state.close()
        except Exception as exc:                         # a fault of the order step's own: still stopped and journaled
            return self._finish("stopped", "unexpected", f"the run failed unexpectedly: {_venue_error(exc)}")
        finally:
            lock.release()

    def _rest(self, now: float) -> dict:
        try:
            baseline = self._baseline()                  # 3
            self._reconcile_and_cancel(baseline)         # 4
            paper = self.paper_read                      # 5
            book = broker_book(self.ledger.fills())
            if any(abs(u) > MAX_UNITS for u in book.values()):
                raise _Stop("reconcile", f"a position in the broker book is above {MAX_UNITS:,.0f} units in size; a person must look")
            capital = paper.equity if self.capital is None else self.capital
            if not _finite(capital) or capital <= 0:
                raise _Stop("targets", f"the capital must be a positive finite number, not {capital!r}")
            self.paper_equity = paper.equity
            self.target_units, per_strategy = targets(paper, float(capital))
            min_value = paper.min_trade_pct * paper.share * float(capital) / paper.equity
            prices, why_not = self._prices(sorted(set(self.target_units) | set(book) | set(baseline)), now)   # 6
            cash = self._cash()
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except (OrderError, OrderStepError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "reconcile", f"the ledger, the state file or the journal failed: {_local_error(exc)}")
        try:
            candidates = self._differences(book, per_strategy, prices, why_not, min_value)   # 7
            self._send(candidates, baseline, book, prices, cash)                            # 8, 9, 10
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except (OrderError, OrderStepError, sqlite3.Error, OSError) as exc:   # nothing more is sent
            return self._finish("stopped", "send", f"the ledger, the state file or the journal failed: {_local_error(exc)}; "
                                                   "nothing more is sent and the next run's reconcile settles what was")
        try:
            self._reconcile(baseline, "after")          # 11
        except _Stop as stop:
            return self._finish("stopped", "after", stop.reason)
        except (OrderError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "after", f"the last reconcile could not be completed: {_local_error(exc)}")
        return self._finish("done", None, None)

    # -- the journal as it is on disk
    def _journal_file(self, day: str) -> Path | None:
        d = getattr(self.journal, "dir", None)
        return Path(d) / f"{day}.jsonl" if d is not None else None

    def _scan_journal(self) -> None:
        """Read every journal file line by line. A line that is not a JSON object is torn (a crash mid-write). The
        last line of the newest file may be torn: it is skipped, recorded once with a `torn_line` entry and reported
        in the summary, and stays skipped in later runs. A torn line anywhere else stops the run."""
        d = getattr(self.journal, "dir", None)
        if d is None:
            return                                       # a journal without files: its read() is used instead
        try:
            files = sorted(Path(d).glob("????-??-??.jsonl"))
            today = self._journal_file(self.day)
            entries, torn = [], []
            for i, path in enumerate(files):
                data = path.read_bytes()
                if data and not data.endswith(b"\n") and (i == len(files) - 1 or path == today):
                    with path.open("ab") as fh:          # what is written next starts on its own line
                        fh.write(b"\n")
                lines = data.split(b"\n")
                numbered = [(n, raw) for n, raw in enumerate(lines, 1) if raw.strip()]
                for n, raw in numbered:
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        obj = None
                    if isinstance(obj, dict):
                        entries.append(obj)
                    else:
                        torn.append((path.name, n, i == len(files) - 1 and n == numbered[-1][0]))
        except OSError as exc:
            raise _Stop("journal", f"the journal could not be read: {_describe(exc)}") from None
        known = {(e.get("file"), e.get("line")) for e in entries
                 if e.get("type") == "order_step" and e.get("event") == "torn_line"}
        new = [(name, n) for name, n, last in torn if (name, n) not in known]
        bad = [(name, n) for name, n, last in torn if (name, n) not in known and not last]
        if bad:
            raise _Stop("journal", "the journal has a damaged line that is not the last line of the newest file ("
                                   + ", ".join(f"{name} line {n}" for name, n in bad[:5])
                                   + "); a person must look before the order step runs again")
        self._seen_fill_ids = {e.get("fill_id") for e in entries
                               if e.get("type") == "order_step" and e.get("event") == "fill" and e.get("venue") == self.name}
        self.torn_lines = [{"file": name, "line": n} for name, n, _ in torn]
        for name, n in new:
            self._write({"event": "torn_line", "file": name, "line": n,
                         "reason": "a damaged last line, from a crash mid-write; skipped by the order step"})

    # -- 1, read again before every order
    def _check_events(self, name: str) -> None:
        """Refuse an Events venue (events_refusal). A class that only a descriptor gives is read later, once, in the
        session, before any other venue call (`_check_events_class`)."""
        why = events_refusal(self.venue, name)
        if why is not None:
            raise _Stop("environment", why)
        static = inspect.getattr_static(self.venue, "account_class", _ABSENT)
        self._class_later = static is not _ABSENT and hasattr(type(static), "__get__")

    def _check_events_class(self) -> None:
        """The account class a descriptor gives, read once in the session before any other venue call."""
        if not self._class_later:
            return
        try:
            cls = self.venue.account_class
        except Exception as exc:
            raise _Stop("environment", f"the venue's account class could not be read: {_venue_error(exc)}") from None
        if _is_events(cls):
            raise _Stop("environment", f"the order step does not trade an Events account ({EVENTS_CLASS}): event "
                                       "contracts go through the event-orders command and its own ledger")

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
            raise _Stop("environment", f"the order step runs against the test environment ({TEST_ENVIRONMENT}, a plain "
                                       "str) only; this venue's environment is not that. Live trading is not built")
        try:
            static = inspect.getattr_static(self.venue, "host", _ABSENT)
        except Exception:
            static = None                                # present but unreadable: refused below
        try:
            dynamic = getattr(self.venue, "host", _ABSENT)   # also a host that comes through __getattr__
        except Exception:
            raise _Stop("environment", "the venue's host could not be read") from None
        if static is not _ABSENT or dynamic is not _ABSENT:
            if not all(_sandbox_host(h) for h in (static, dynamic)):
                raise _Stop("environment", "the venue's host is not a plain str attribute naming the sandbox")

    # -- the paper book's day
    def _paper_day(self, p: PaperTargets) -> None:
        day = p.day
        if not _plain(_DATE, day):
            raise _Stop("targets", "the paper book does not say which day it is from; the order step follows only a "
                                   "paper book from the session before the run's day or from that day")
        try:
            prev = str(pd.Timestamp(self.calendar.previous_session(self.day)).date())
        except Exception as exc:
            raise _Stop("targets", f"the exchange calendar could not name the session before {self.day}: "
                                   f"{_describe(exc)}") from None
        if day not in (prev, self.day):
            raise _Stop("targets", f"the paper book is from {day}; the order step follows only a paper book from {prev} "
                                   f"(the session before {self.day}) or from {self.day}. Run the paper step first")

    # -- 1
    def _environment(self) -> None:
        try:
            name = self.venue.name
        except Exception:
            name = None
        if not _plain(_NAME, name):
            raise _Stop("environment", "the venue has no plain name (letters, digits, '_', '.', '-', up to 64)")
        self.name = name
        self._check_events(name)
        self._check_environment()
        not_sent = tuple(getattr(self.ledger, "not_sent", ()) or ())
        missing = [c.__name__ for c in NOT_SENT if c not in not_sent]
        broader = [getattr(t, "__name__", repr(t)) for t in not_sent if not (isinstance(t, type) and issubclass(t, NOT_SENT))]
        if missing or broader:                           # anything broader would read an unknown outcome as "not sent"
            raise _Stop("environment", "the order ledger must treat exactly NotTransmitted and OrderRejected as not sent "
                                       f"(missing: {missing or 'none'}; not allowed: {broader or 'none'}); open it with open_ledger")

    # -- 2
    def _session(self, now: float) -> None:
        if self.calendar is None:
            try:
                self.calendar = _default_calendar()
            except Exception as exc:
                raise _Stop("session", f"the exchange calendar could not be loaded: {_describe(exc)}") from None
        start, end, why = market_window(self.calendar, now)
        if why:
            raise _Stop("session", why)
        if not start <= now <= end:
            raise _Stop("session", f"outside the regular session window {_hhmm(start)} to {_hhmm(end)} New York time "
                                   f"(it is {_hhmm(now)})")
        self.window_end = end

    # -- 3
    def _baseline(self) -> dict[str, float]:
        base = self.state.baseline()
        if base is not None:
            return base
        if self.ledger.orders():
            raise _Stop("baseline", "no baseline is recorded but the order ledger already has orders; the account's "
                                    "positions can no longer tell the owner's holdings from this program's. A person must look")
        try:
            held = {sym: _as_float(units) for sym, units in dict(self.venue.positions()).items()}
            bad = sum(1 for sym, units in held.items()
                      if not (_plain(_SYMBOL, sym) and units is not None and 0 <= units <= MAX_UNITS))
        except Exception as exc:
            raise _Stop("baseline", f"the venue's positions could not be read: {_venue_error(exc)}") from None
        if bad:
            raise _Stop("baseline", f"the venue's positions hold {bad} entr{'y' if bad == 1 else 'ies'} that {'is' if bad == 1 else 'are'} "
                                    "not a plain symbol with a finite number at least 0; no baseline is recorded")
        try:
            base = self.state.record_baseline(held)
        except OrderStepError as exc:
            raise _Stop("baseline", str(exc)) from None
        self._write({"event": "baseline", "positions": base})
        return base

    # -- 4
    def _applied_positions(self, baseline: dict) -> dict[str, float]:
        applied = broker_book(f for f in self.ledger.fills() if f["applied"])
        out = dict(baseline)
        for sym, u in applied.items():
            out[sym] = math.fsum((out.get(sym, 0.0), u))
        return out

    def _reconcile(self, baseline: dict, when: str) -> dict:
        result = self.ledger.reconcile(self.venue, self._applied_positions(baseline))
        self._journal_fills()
        return result

    def _journal_fills(self) -> None:
        if self._seen_fill_ids is None:
            try:
                self._seen_fill_ids = {e.get("fill_id") for e in self.journal.read()
                                       if e.get("type") == "order_step" and e.get("event") == "fill" and e.get("venue") == self.name}
            except Exception as exc:
                raise _Stop("reconcile", f"the journal could not be read to find the fills already written: {_describe(exc)}") from None
        for f in self.ledger.unapplied_fills():
            if f["id"] not in self._seen_fill_ids:
                self._write({"event": "fill", "fill_id": f["id"], "tag": f["tag"] if _plain(_TAG, f["tag"]) else None,
                             "symbol": f["symbol"] if _plain(_SYMBOL, f["symbol"]) else None,
                             "units": _number_or_none(f["units"]), "price": _number_or_none(f["price"]),
                             "price_covers_units": _number_or_none(f["price_covers_units"]), "fee": _number_or_none(f["fee"])})
                self._sync_journal()                     # on disk before the fill counts as applied
                self._seen_fill_ids.add(f["id"])
                self.fills_journaled += 1
            self.ledger.mark_applied([f["id"]])

    def _reconcile_and_cancel(self, baseline: dict) -> None:
        self.state.settle_sends(self.ledger)
        self._reconcile(baseline, "first")
        for o in self.ledger.orders(OPEN_STATES):
            tag = o["tag"] if _plain(_TAG, o["tag"]) else "an order"
            symbol = o["symbol"] if _plain(_SYMBOL, o["symbol"]) else None
            try:
                answer = self.venue.cancel(o["tag"])
                word = answer.get("status") if isinstance(answer, dict) else None
                word = word if type(word) is str and word in CANCEL_WORDS else "an answer that is not a cancel word"
            except Exception as exc:
                word = f"error: {_venue_error(exc)}"
            self.cancels.append({"tag": tag, "symbol": symbol, "answer": word})
            self._write({"event": "cancel", "tag": tag, "symbol": symbol, "answer": word})
        second = self._reconcile(baseline, "second")
        still = self.ledger.orders(OPEN_STATES)
        blocked = self.ledger.blocked()
        if still or blocked:
            raise _Stop("reconcile", "; ".join(self._ledger_words(still, second))[:2000])

    def _ledger_words(self, still: list[dict], result: dict) -> list[str]:
        """Why the ledger stands in the way, from its records (tags, states, counts), never from its text, which can
        hold the venue's words."""
        def tag(o):
            return o["tag"] if _plain(_TAG, o.get("tag")) else "an order"

        def state(o):
            return o["state"] if o.get("state") in STATES else "a state the order step does not know"
        reasons = []
        if still:
            reasons.append(f"{len(still)} order{'' if len(still) == 1 else 's'} of this ledger "
                           f"{'is' if len(still) == 1 else 'are'} still open at the broker after the cancel requests "
                           f"({', '.join(tag(o) + ' ' + state(o) for o in still)}); the next run asks again")
        waiting = len(self.ledger.unapplied_fills())
        if waiting:
            reasons.append(f"{waiting} fill{'' if waiting == 1 else 's'} not yet applied to the book")
        items = self.ledger.attention()
        if items:
            reasons.append(f"{len(items)} attention item{'' if len(items) == 1 else 's'} open "
                           f"({', '.join(str(int(i['id'])) + ' for ' + tag(i) for i in items)}); close each with "
                           "close_attention once a person has looked")
        last = self.ledger.last_reconcile()
        if last is None or not last.get("clean"):
            counts = [f"{len(result.get(k) or [])} {k}" for k in ("unresolved", "anomalies") if result.get(k)]
            if result.get("positions_error"):
                counts.append("the venue's positions could not be read")
            reasons.append("the latest reconcile was not clean" + (f" ({', '.join(counts)})" if counts else ""))
        diffs = result.get("position_differences") or {}
        if diffs:
            plain = {sym: {k: _number_or_none(v) for k, v in d.items() if k in ("book", "venue", "unapplied")}
                     for sym, d in diffs.items() if _plain(_SYMBOL, sym) and isinstance(d, dict)}
            odd = len(diffs) - len(plain)
            reasons.append(f"positions differ from baseline plus broker book: {json.dumps(plain, sort_keys=True)}"
                           + (f" and {odd} more that are not plain symbols" if odd else ""))
        return reasons or ["the order ledger is blocked"]

    # -- 5
    def _paper(self) -> PaperTargets:
        try:
            p = self.paper() if callable(self.paper) else self.paper
            return _check_paper(p)
        except Exception as exc:
            raise _Stop("targets", f"the paper targets could not be read: {_describe(exc)}") from None

    # -- 6
    def _prices(self, symbols: list[str], now: float) -> tuple[dict[str, float], dict[str, str]]:
        if not symbols:
            return {}, {}
        asked = self._now()
        for sym in symbols:
            self.asked[sym] = asked
        try:
            raw = self.quotes(symbols)
            raw = dict(raw or {})
        except Exception as exc:
            raise _Stop("prices", f"the quotes could not be read: {_venue_error(exc)}") from None
        prices, why_not = {}, {}
        now = self._now()
        for sym in symbols:
            price, t, why = _read_quote(raw.get(sym), now)
            if price is None:
                why_not[sym] = why
            else:
                prices[sym] = price
                self.quote_times[sym] = t
        return prices, why_not

    def _quote_fresh(self, sym: str) -> bool:
        """Whether the quote in hand for `sym` is within QUOTE_MAX_AGE_SECONDS on the clock now."""
        t, now = self.quote_times.get(sym), self._now()
        return t is not None and -QUOTE_MAX_AHEAD_SECONDS <= now - t <= QUOTE_MAX_AGE_SECONDS

    def _refresh(self, sym: str, quote: float, held: dict, in_hand: dict) -> tuple[float | None, str | None, list[str]]:
        """At most one ask of the quote function before an order is judged: the order's own symbol when its quote in
        hand is older than QUOTE_MAX_AGE_SECONDS (always: it is the quote the order is judged and sent on), and every
        holding whose quote in hand is too old and was last asked for QUOTE_ASK_GAP_SECONDS or more ago. Returns
        (the order's quote, None, the holdings still without a fresh quote), or (None, why, []) when the order's own
        quote asked for again is not usable. Usable answers go into `in_hand` with their times."""
        own = not self._quote_fresh(sym)
        now = self._now()
        holdings = [s for s, u in sorted(held.items()) if u > UNITS_EPS and s != sym and not self._quote_fresh(s)
                    and now - self.asked.get(s, -math.inf) >= QUOTE_ASK_GAP_SECONDS]
        ask = ([sym] if own else []) + holdings
        raw, failed = {}, None
        if ask:
            for s in ask:
                self.asked[s] = now
            try:
                raw = dict(self.quotes(ask) or {})
            except Exception as exc:
                failed = exc
        now = self._now()
        for s in holdings:
            price, t, _ = _read_quote(raw.get(s), now)
            if price is not None:
                self.quote_times[s], in_hand[s] = t, price
        if own:
            old = f"the quote is older than {QUOTE_MAX_AGE_SECONDS // 60} minutes"
            if failed is not None:
                return None, f"{old} and asking for it again failed: {_venue_error(failed)}", []
            price, t, why = _read_quote(raw.get(sym), now)
            if price is None:
                return None, f"{old} and the quote asked for again is not usable: {why}", []
            self.quote_times[sym], quote = t, price
        in_hand[sym] = quote
        return quote, None, sorted(s for s, u in held.items() if u > UNITS_EPS and not self._quote_fresh(s))

    def _cash(self) -> float | None:
        try:
            answer = self.venue.cash()
        except Exception as exc:
            raise _Stop("prices", f"the venue's cash could not be read: {_venue_error(exc)}") from None
        try:                                             # whatever the answer's methods do: no usable number
            cash = answer.get("cash") if isinstance(answer, dict) else None
            if isinstance(cash, str):
                cash = float(cash)
            cash = _as_float(cash)
            cash = cash if cash is not None and 0.0 <= cash <= MAX_CASH else None
        except Exception:
            cash = None
        if cash is None:
            return None
        try:                                             # missing or None: no settled cash given, as before
            settled, self._settled_unusable = _settled_figure(cash, answer.get("settled_cash"))
        except Exception:
            self._settled_unusable, settled = True, None
        self.cash_read, self.settled_cash = cash, settled
        return cash

    # -- 7
    def _differences(self, book, per_strategy, prices, why_not, min_value) -> list[dict]:
        holdings = strategy_holdings(self.ledger.orders())
        out = []
        for sym in sorted(set(self.target_units) | set(book)):
            target, owned = self.target_units.get(sym, 0.0), book.get(sym, 0.0)
            delta = target - owned
            if abs(delta) <= UNITS_EPS:
                continue
            if _is_crypto(sym):
                self.skipped.append({"symbol": sym, "reason": "crypto is not traded by the order step"})
                continue
            units = round_units(delta, self.fractional)
            if units < 0:
                units = round_units(max(units, -max(owned, 0.0)), self.fractional)   # a sale never exceeds the book
            if abs(units) <= UNITS_EPS:
                continue
            quote = prices.get(sym)
            if quote is None:
                reason = why_not.get(sym, "no quote")
                self.skipped.append({"symbol": sym, "units": units, "reason": reason})
                self._write({"event": "no_quote", "symbol": sym, "units": units, "reason": reason})
                continue
            value = abs(units) * quote
            if value < min_value:
                self.skipped.append({"symbol": sym, "units": units, "reason": f"worth {value:.2f}, less than the smallest "
                                                                              f"trade of {min_value:.2f}"})
                continue
            deltas = [{"strategy": s, "symbol": sym, "units": book_s.get(sym, 0.0) - holdings.get(s, {}).get(sym, 0.0)}
                      for s, book_s in per_strategy.items()]
            deltas += [{"strategy": s, "symbol": sym, "units": -h[sym]} for s, h in holdings.items()
                       if s not in per_strategy and sym in h]
            netted = net_orders(deltas)
            try:
                if not netted:
                    raise OrderError("no strategy has a part in this order")
                parts = order_parts(netted[0]["parts"], units)
            except OrderError as exc:
                self.refused.append({"symbol": sym, "units": units, "by": "order step",
                                     "reason": f"the strategies' parts could not be worked out: {_describe(exc)}"})
                self._write({"event": "refused", "symbol": sym, "units": units, "by": "order step",
                             "reason": self.refused[-1]["reason"]})
                continue
            out.append({"symbol": sym, "units": units, "quote": quote, "value": value, "parts": parts, "owned": owned})
        return out

    # -- 8, 9, 10
    def _send(self, candidates, baseline, book, prices, cash) -> None:
        held = {sym: math.fsum((baseline.get(sym, 0.0), book.get(sym, 0.0))) for sym in set(baseline) | set(book)}
        unpriced = [sym for sym, u in held.items() if u > UNITS_EPS and sym not in prices]
        if cash is None:                                 # unusable: stop before any order, record nothing
            raise _Stop("prices", f"the venue's cash is not a usable number ({_EQUITY_BOUNDS}); nothing is sent")
        if self._settled_unusable:
            raise _Stop("prices", f"the venue's settled cash is not a usable number ({_EQUITY_BOUNDS}, at most the cash "
                                  f"plus {SETTLED_ABOVE_CASH:.2f}); nothing is sent")
        if unpriced:
            raise _Stop("prices", f"the broker equity cannot be worked out: no usable quote for {', '.join(sorted(unpriced))}; "
                                  "nothing is sent")
        in_hand = dict(prices)                           # every holding at its latest usable quote
        equity = self._equity(cash, held, in_hand)
        previous, prior_peak = self.state.previous_equity(self.day), self.state.peak()
        row = self.state.record_equity(self.day, equity)
        self.broker_equity = equity
        opened = sum(1 for u in held.values() if u > UNITS_EPS)

        def account_now() -> dict:
            """The account at the quotes in hand now (a holding without a fresh quote at its last usable price). The
            day's row (and so the peak) is kept at the same prices."""
            now_equity = self._equity(cash, held, in_hand)
            if now_equity != self.broker_equity:
                self.state.reprice_equity(row, now_equity)
                self.broker_equity = now_equity
            peak = now_equity if prior_peak is None else max(prior_peak, now_equity)
            return {"equity": now_equity,
                    "daily_loss_pct": max(0.0, (previous - now_equity) / previous * 100.0) if previous else 0.0,
                    "drawdown_pct": max(0.0, (peak - now_equity) / peak * 100.0) if peak else 0.0,
                    "open_positions": opened}
        try:
            limits = risk_gate.load_limits(self.limits_file)
        except risk_gate.RiskConfigError:
            limits = None                                # check_order then refuses with the file's own error
        cap = max_orders_per_day(limits)
        sent_today = self.state.sent_on(self.day)
        settled = self.settled_cash
        cash_budget = cash * (1.0 - CASH_KEPT) if cash > 0 else 0.0
        settled_budget = None if settled is None else (settled * (1.0 - CASH_KEPT) if settled > 0 else 0.0)
        budget = cash_budget if settled_budget is None else min(cash_budget, settled_budget)   # never unsettled money
        words = {"cash": f"the purchases would exceed the cash the venue reports less {CASH_KEPT:.0%} ({cash_budget:.2f})",
                 "settled": f"the purchases would exceed the settled cash the venue reports less {CASH_KEPT:.0%} "
                            f"({(settled_budget or 0.0):.2f})"}
        spent = 0.0
        purchases_stopped = None

        def money(cost: float) -> str | None:
            """Which budget stops a purchase of `cost`, judged against what each has left now: the cash budget as if
            it had also paid every purchase already counted as left for lack of settled money, the settled budget
            less what was spent. Over the settled budget only: "settled", and the cost is counted in
            purchases_left_unsettled; over the cash budget only: "cash"; over both: the one with less left; None
            when it fits both."""
            cash_left = cash_budget - spent - self.purchases_left_unsettled
            settled_left = math.inf if settled_budget is None else settled_budget - spent
            over_cash, over_settled = cost > cash_left, cost > settled_left
            if over_settled and not over_cash:
                self.purchases_left_unsettled += cost
                return "settled"
            if over_cash and not over_settled:
                return "cash"
            if over_cash:
                return "settled" if settled_left < cash_left else "cash"
            return None
        sales = sorted((c for c in candidates if c["units"] < 0), key=lambda c: (-c["value"], c["symbol"]))
        buys = sorted((c for c in candidates if c["units"] > 0), key=lambda c: (-c["value"], c["symbol"]))
        for c in sales + buys:
            sym, units = c["symbol"], c["units"]
            owned_here = held.get(sym, 0.0)
            reduces = units < 0 and owned_here > 0 and abs(owned_here + units) < abs(owned_here)
            if self._now() > self.window_end:
                self._leave(c, "the session window closed during the run")
                continue
            if sent_today >= cap:
                self._leave(c, f"the day's cap of {cap} orders is reached")
                continue
            if units > 0 and purchases_stopped:            # left over; labelled by the budget that stops it now
                q = in_hand.get(sym, c["quote"])
                lp = limit_price(units, q)
                self._leave(c, words[money(units * (lp if lp is not None else q)) or purchases_stopped])
                continue
            if abs(units) > MAX_UNITS:
                self._leave(c, f"the order is above {MAX_UNITS:,.0f} units in size")
                continue
            quote, judged = in_hand.get(sym, c["quote"]), None   # the latest quote in hand (a holding's may be newer)
            for round_ in (1, 2):
                quote, stale, unpriced = self._refresh(sym, quote, held, in_hand)   # 1. fresh quotes, one ask at most
                if quote is None:
                    self._leave(c, stale)
                    break
                account = account_now()                  # ... the account priced at them
                if unpriced and not reduces:
                    self._leave(c, f"the account cannot be priced: no fresh quote for {', '.join(unpriced)}")
                    break
                order = {"symbol": sym, "side": "buy" if units > 0 else "sell", "value": abs(units) * quote,
                         "reduces_risk": bool(reduces), "adds_to_existing": owned_here > UNITS_EPS,
                         "position_value_after": abs(owned_here + units) * quote}
                ok, why = risk_gate.check_order(order, dict(account), limits,        # 2. the risk gate, on that quote
                                                limits_file=None if limits else self.limits_file,
                                                latch_file=self.latch_file, halt_file=self.halt_file)
                if not ok:
                    self._refuse(c, "risk gate", why, order=order, account=account)
                    break
                limit = limit_price(units, quote, reduces)
                if limit is None:
                    self._refuse(c, "order step", f"no positive limit price comes from the quote {quote}")
                    break
                cost = units * limit if units > 0 else 0.0
                if units > 0 and spent + cost > budget:
                    purchases_stopped = money(cost) or ("cash" if settled_budget is None or cash_budget <= settled_budget
                                                        else "settled")
                    self._leave(c, words[purchases_stopped])
                    break
                self._check_environment()                # 3. the environment and host, read again
                if self._quote_fresh(sym):               # 4. the quote still fresh on the clock: judged at it
                    judged = (quote, limit, order, cost)
                    break
                if round_ == 2:                          # ... stale again: back to 1 once, then left over
                    self._leave(c, "the quote went stale again while the order was checked")
            if judged is None:
                continue
            quote, limit, order, cost = judged
            tag = f"{self.batch}-{sym}"
            watch = _SubmitWatch(self.venue)
            if self._now() > self.window_end:            # 5. the window, on the clock, immediately before intend
                self._leave(c, "the session window closed during the run")
                continue
            try:
                self.ledger.intend(tag, sym, units, quote, limit_price=limit, batch=self.batch, parts=c["parts"])
                self.state.note_sending(tag, self.day)
                self._check_environment()                # 6. the environment and host again ...
                late = self._last_look(sym, reduces)     # ... the quote's age, the halt and latch files ...
                if late is None and self._now() > self.window_end:   # ... then the window, nothing between it and submit_once
                    late = "the session window closed during the run"
                if late is not None:
                    self.state.note_outcome(tag, "not sent")             # it stays intended, never sent
                    self._leave(c, late)
                    continue
                rec = self.ledger.submit_once(watch, tag)
            except OrdersBlocked:
                words = "; ".join(self._ledger_words(self.ledger.orders(OPEN_STATES), {}))
                raise _Stop("send", f"the ledger refused to send {tag}: {words}"[:1000]) from None
            except (OrderError, sqlite3.Error) as exc:
                raise _Stop("send", f"order {tag} could not be recorded or sent: {_local_error(exc)}; the next run's "
                                    "reconcile settles it") from None
            outcome = rec["state"] if rec.get("state") in STATES else "unknown"
            self.state.note_outcome(tag, outcome)
            if outcome == "refused":
                why = f"the venue did not take it: {_venue_error(watch.error)}" if watch.error is not None else "the venue did not take it"
                self._refuse(c, "venue", why, tag=tag)
                continue
            if outcome != "accepted":
                if watch.error is not None:
                    why = f"the venue's call raised {_venue_error(watch.error)}"
                elif watch.called:
                    why = f"the venue answered {_status_word(watch.answer)}, not 'accepted'"
                else:
                    why = "the venue was not called"
                self._write({"event": "unknown", "tag": tag, "symbol": sym, "units": units, "state": outcome, "reason": why})
                raise _Stop("send", f"the outcome of order {tag} ({sym} {units:+g}) is {outcome}: {why}; nothing more is "
                                    "sent until a reconcile settles it")
            sent_today += 1
            spent += cost
            if units > 0 and owned_here <= UNITS_EPS:
                opened += 1
            entry = {"symbol": sym, "units": units, "side": order["side"], "limit_price": limit, "quote": quote,
                     "value": abs(units) * quote, "tag": tag, "parts": c["parts"], "reduces_risk": bool(reduces)}
            self.sent.append(entry)
            self._write({"event": "order", **entry, "state": outcome})

    def _equity(self, cash: float, held: dict, in_hand: dict) -> float:
        """The venue's cash plus every holding at its quote in hand; a number outside 0 to MAX_CASH stops the run."""
        equity = _as_float(cash + math.fsum(u * in_hand[sym] for sym, u in held.items() if u > UNITS_EPS))
        if equity is None or not 0.0 <= equity <= MAX_CASH:
            raise _Stop("prices", f"the broker equity is not a usable number ({_EQUITY_BOUNDS}); nothing more is sent")
        return equity

    def _last_look(self, sym: str, reduces: bool) -> str | None:
        """Why an intended order may not be sent after all (its quote is too old now, the halt file or, for an order
        that adds risk, the latch has appeared), or None. A file that cannot be looked at counts as there."""
        if not self._quote_fresh(sym):
            return "the quote went stale before the order was sent"
        if _present(self.halt_file):
            return "the operator halt file is there at the last look before sending"
        if not reduces and _present(self.latch_file):
            return "the drawdown latch is there at the last look before sending"
        return None

    def _leave(self, c: dict, reason: str) -> None:
        item = {"symbol": c["symbol"], "units": c["units"], "reason": reason}
        self.left_over.append(item)
        self._write({"event": "left_over", **item})

    def _refuse(self, c: dict, by: str, reason: str, **extra) -> None:
        item = {"symbol": c["symbol"], "units": c["units"], "by": by, "reason": str(reason)[:MAX_TEXT * 2]}
        self.refused.append(item)
        self._write({"event": "refused", **item, **extra})

    # -- 11
    def _finish(self, status: str, stage: str | None, reason: str | None) -> dict:
        try:
            book = broker_book(self.ledger.fills())
            mine = broker_book(f for f in self.ledger.fills() if self.batch and f["tag"].startswith(self.batch + "-"))
        except Exception:
            book, mine = {}, {}
        gaps = {sym: {"broker_book": book.get(sym, 0.0), "target": t, "gap": t - book.get(sym, 0.0)}
                for sym, t in sorted(self.target_units.items())}
        for sym in sorted(set(book) - set(self.target_units)):
            gaps[sym] = {"broker_book": book[sym], "target": 0.0, "gap": -book[sym]}
        summary = {"status": status, "stage": stage, "reason": reason, "venue": self.name, "batch": self.batch,
                   "orders_sent": len(self.sent), "sent": self.sent, "refused": self.refused, "left_over": self.left_over,
                   "skipped": self.skipped, "cancels": self.cancels, "filled_so_far": mine, "fills_journaled": self.fills_journaled,
                   "broker_equity": self.broker_equity if _finite(self.broker_equity) else None,
                   "paper_equity": self.paper_equity, "gaps": gaps, "torn_lines": self.torn_lines,
                   "cash": self.cash_read, "settled_cash": self.settled_cash,
                   "purchases_left_unsettled": self.purchases_left_unsettled}
        try:
            if not self._locked:                         # stopped before the lock: returned (and printed) only
                return summary
            if status == "stopped":
                self._write({"event": "stopped", "stage": stage, "reason": reason})
            self._write({"event": "summary", **summary})
        except Exception as exc:                         # the summary is still returned
            summary["journal_error"] = _describe(exc)
        return summary
