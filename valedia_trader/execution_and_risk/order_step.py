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
                 line anywhere else stops the run. The torn line stays in the file. The journal's other readers
                 (Journal.read) skip it and list it in `skipped_lines`; the tax notes and the tax command refuse to
                 compute from it, the weekly critic marks its review incomplete, and Journal.write starts the next
                 entry on a new line.
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
                 With a follow list (`follow`, a Follow: config/broker_book.json as the command line reads it, T32)
                 only the strategies it names, each an equal part of the capital: times capital / (paper share x
                 number followed), and the smallest trade at the same scale. What a strategy no longer followed
                 holds at the broker has a target of zero and is sold down by the ordinary steps below. A follow list
                 that is refused, or not a well-formed Follow, stops the run after the paper book is read, before
                 any venue call that sends, changes or cancels an order; in live mode a follow list is required
                 and is judged with the locks, before anything is read from the account.
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

Approval of a batch by ticket number (docs/specs/2026-11-13-go-live-gate-design.md, step 1; still the test
environment only). Without `propose` or `approve` a run is exactly the one above. With them it works in two runs:
 - propose  steps 1 to 10 up to the moment before intend: instead of intend, each order that passed every check
            becomes a line of a ticket (fund, side, units, quote, limit, value), counted against the caps as if it
            were sent. The ledger handed to the run cannot intend or send (_NoSend). After step 11 the ticket is
            written: a six-digit number from the system's secure random source (`secrets`), the lines, the equity and
            cash read, the time and an expiry TICKET_LIFE_SECONDS later, bound to the venue's name, a keyed hash of
            its account id (read only in these runs), the New York day, the capital and the share mode. Nothing is
            intended or sent. The journal gets one `ticket` entry holding the number's last two digits only; the
            full number is returned beside the summary (`ticket_number`, never journaled).
 - approve  the run checks, before any other venue call: approval not locked (WRONG_NUMBERS_MAX refused approvals
            this New York day since the latest ticket), the number six digits, a ticket of that number made today for
            this venue, not used, not expired, of this capital and share mode; then the account read again matches.
            Any failure: one plain reason, nothing sent, the refusal recorded. Then steps 3 to 10 run afresh; on
            entering the sending stage, before the first intend, the ticket is marked used (one row, committed; a
            second mark fails). An order is sent only when the fresh targets want the same fund, side and units
            (fractional: within TICKET_UNITS_SAME), its quote (the one the gate judged) is within TICKET_QUOTE_MOVE of
            the ticket's, the ticket is still live (checked before intend and again at the last look), and every check
            of an ordinary run passes; otherwise it is dropped with the reason. Orders not on the ticket are not sent.
Tickets live in their own file beside the state file (ticket_path_for, TicketBook): append-only tables, natural keys,
no update, no delete, no replacement; the state file's layout is unchanged.

Live mode (`live`, T22d-1) adds five checks to a ticket run; a practice run never makes them:
 - the run lock must still be the file the run locked (not unlinked, not replaced): judged at the start, before the
   ticket's checks, and in the last look before every submit_once; otherwise the run stops at stage "lock";
 - before the baseline and the reconcile, the state file must count at least the orders the order ledger holds as
   sent on the New York day (any state but intended and refused) and hold an equity peak at least the highest
   equity the journal recorded for the venue (summaries and tickets); otherwise the run stops at stage "state";
 - the account read must give a settled cash; otherwise the run stops at stage "prices" (never the cash figure);
 - every symbol held (the baseline and this ledger's book) has its mark judged by the second price source, as an
   order's own quote is, before the equity is recorded and the ticket used, and again whenever a refresh replaces
   it, before the gate prices the account; a verdict other than "agree" stops the run at stage "marks". Each quote
   (symbol, price, time) is asked about once in a run; an order's own quote already judged as a mark is not asked
   again;
 - the max_capital check works out this ledger's exposure afresh at each order, at the marks the gate has just used.

What the summary says for the stake ladder (T34b-2, docs/specs/2026-11-14-caps-ladder-design.md D4, D5, D6, D8):
 - `faults`, a list, only when not empty, on practice and live runs, done or stopped: `reconcile` when the final
   reconcile's own result is not clean for any reason but three (the run still ends as it would): a position
   difference in a symbol where this ledger still holds an order open at the venue after that reconcile's status reads
   (a fill racing the positions read), or one this run's batch ordered in any state, or one of a fill this run
   journaled (the venue's positions may lag its order status); positions the venue did not give (`positions_error`);
   and an unresolved order that the ledger, read back after that reconcile, holds in an open state (`accepted`,
   `partly_filled`: judged by its state, never by the words of a reason); those are set aside and said in
   `reconcile_pending` ({"symbols", "positions_unread", "status_unread": the count of such orders}, information, never
   a fault), and the next run's first reconcile judges every symbol again; `attention:<kind>` per distinct kind,
   sorted, of a blocking attention item (a kind not in orders.NOTICE_KINDS) whose id is above the largest id the ledger held when the run took it, read once before
   settle_sends (a kind not [a-z0-9_-]{1,40} is "unknown"); `cap` when a fill broke what was checked: on practice and
   live runs, for every fill at the moment a run first journals it, whichever run sent its order, one order's fills
   above the units it asked for, or the order's average fill price (the broker's figure, not a piece worked out from
   running averages) on the wrong side of its limit by more than one part in a million of the limit, the edge itself
   no breach (every limit is a whole number of cents, so an average the broker rounds to the cent is either at the
   limit or a cent through it; an average that includes fees would trip this rule); in live mode also, with the
   confirmation in force, a purchase fill of this batch worth more than max_order_value at its own price, or at the
   replayed average for a piece left unpriced (each fill on its own, never summed; a sale that fills above its limit
   is a better price, not a breach). Every write of the ledger that sets an order's average is judged once, by the
   line of THAT write, on the figure THAT write set, whenever the line is written (T34b-3 addendum 4): the averages
   are replayed from the ledger's durable records (`OrderLedger.average_writes`), so a verdict never depends on the
   order's average at journal time, on which run died, or on an equal value seen before. A priced fill row's line
   judges the average after its own row; an `avg_restated` notice's line judges its own `new`; a `piece_unpriced`
   notice that came with a row of its own is judged by that row's fill line (the notice line has no verdict), and
   one that came with no row of its own is judged by its own line, on its own `new`; a row whose units the
   venue gave no average for is `unjudged` until a later write prices them, then settles naming that write
   (`covered_by`); an old figure is never judged again. Each such line carries `judged_avg`, the figure judged. When
   the replay does not reproduce the order's stored figures, a fill line is judged on the stored average and says
   `judged_on: "stored"`. The verdict (`ok`, `breach`, `unjudged`) is the last field of the line, written before the
   fill counts as applied; a breach is `cap` in the summary of the run that wrote
   it, and `cap_refs` lists the sorted refs (`fill-<id>`, `notice-<id>`) each `cap` stands for. A breach journaled
   but in no summary's `cap_refs` (the run died first, or its summary could not be written) is carried by the next
   run of the same venue whose New York day is an exchange session (it passed the calendar test), whatever stage it
   then stops at: that summary has `cap` and the ref. The scan reads every journal file there is, on every run. What
   cannot be judged is named in `cap_unjudged` instead, on practice and live summaries, and stays named on every later
   run (after its scan) until a later read settles it with a `verdict` line ({"ref", "verdict", "covered_by",
   "judged_avg"}). A fill line without a `verdict` (written before T34b-3) is never carried or judged again.
 - `notices`, the sorted kinds of the ledger notices this run journaled (one `notice` line each: notice_id, kind, tag,
   old and new figures, and for an `avg_restated`, or a `piece_unpriced` that came with no row of its own, its
   verdict and `judged_avg`), only when not empty; never a fault.
 - stage `venue_read` (a skipped session for the ladder) instead of `reconcile` when the run stops at the reconcile
   only because the venue could not be read: the positions read raised in the second reconcile; every unresolved entry
   of both reconciles is a status that could not be read of an order the ledger holds, and each of the second
   reconcile's is of an order the ledger holds as accepted or partly filled;
   every order still open after the cancels is one of them; the first reconcile's positions error, if any, is the
   same; no order is sent or unknown; no anomaly or position difference in either reconcile; no blocking attention item
   open or opened in this run; no local failure. Anything else stays `reconcile`. Nothing is sent either way. A failure while they are worked out is the run's own: it
   stops at stage "unexpected", its reason keeping how the run had ended before (or, when it had already stopped
   there, its first reason with the second failure's words appended); a propose run so stopped records its ticket
   refused in the ticket book and returns no ticket number. The attempt row that refusal writes counts once toward the
   day's wrong-number lock on that ticket, which is void anyway, until the next propose lifts the lock. When that
   refusal itself cannot be written, the ticket is NOT void: no number is returned, and the run's reason says that its
   ticket could not be refused.
 - live mode only: `ceiling` (the confirmation's max_capital) once a confirmation is in force; `over_ceiling`
   ({"at_work", "ceiling"}), information and never a fault, when the book at the marks in hand is above it; `working_stop`
   ({"pct", "basis", "n"}, stake_ladder.working_stop_pct from decay.paper_record's exact returns of the strategies
   followed, under max_daily_loss_pct; the floor when the record cannot be read), worked out once before any order;
   `day_stop` ({"pct", "limit", "wall"}) from the look at which the day's loss first reaches the working stop, or
   with "carried": true when an earlier run recorded one for this venue and New York day. In the day stop every order
   that does not reduce a position is refused ("day stop") before the risk gate is asked; a reducing order goes on as
   before. A practice run computes no working stop and refuses nothing more.
 - the limit that stays: the journal is the only record of a trip (its `day_stop` line, written and synced at the
   look, and the summary). If the journal cannot be written from the trip on, the run stops and sends nothing more;
   but a later run the same day, should the journal work again, does not know of the stop, and only the owner's own
   daily limit in the risk gate (max_daily_loss_pct) still judges each of its orders.
 - the limits that stay for `cap`: a breach journaled on a day with no later session run of the venue is carried
   only by the next such run, however far off; a fill the ledger can never price (a broker that reports a fill with
   no average and never restates it) stays in `cap_unjudged` for good; a broker's honest correction of a trade price
   by more than a cent is a blocking anomaly, on purpose; pieces that reach the broker between two reads show only
   their average, so one piece through its limit diluted by honest pieces of the same read is not seen.
"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Callable

import pandas as pd

from valedia_trader.execution_and_risk.orders import (NOTICE_KINDS, RECONCILE_NOTICE_KINDS, REL_TOL, STATES,
                                                      OrderError, OrderLedger, OrdersBlocked, allocate, net_orders)
from valedia_trader.research_loop import risk_gate
from valedia_trader.venues.webull import EVENTS_CLASS, NotTransmitted, OrderRejected

__all__ = ["TEST_ENVIRONMENT", "NOT_SENT", "PaperTargets", "OrderStepState", "OrderStepError", "run_order_step",
           "paper_targets", "open_ledger", "ledger_path_for", "state_path_for", "broker_book", "targets", "strategy_holdings",
           "round_units", "limit_price", "market_window", "max_orders_per_day", "minute_quotes", "wall_clock",
           "TicketBook", "ticket_path_for", "ticket_number", "TICKET_LIFE_SECONDS", "TICKET_QUOTE_MOVE", "Follow",
           "judge_follow", "paper_strategies", "BROKER_BOOK_FILE"]

TEST_ENVIRONMENT = "sandbox"                      # the only environment a run accepts (WebullVenue.environment)
LIVE_ENVIRONMENT = "production"                   # live mode only (--live): Webull's production environment ...
LIVE_HOST = "api.webull.com"                      # ... at exactly this host
SECOND_VERDICTS = ("agree", "disagree", "no second quote")
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
TICKET_LIFE_MAX_SECONDS = 60 * 60                 # a ticket never lives longer than this
TICKET_LIFE_SECONDS = 15 * 60                     # a ticket can be approved for this long after it was made
TICKET_QUOTE_MOVE = 0.005                         # an approved order's fresh quote within 0.5% of the ticket's
TICKET_UNITS_SAME = 1e-9                          # fractional units: the same within this
LIMIT_SAME = 1e-6                                 # T34b-2 F3 (iii): an order's average at its limit within this part of it
LIMIT_EDGE = 1.0 + 1e-9                           # ... the edge itself included on both sides, whatever the rounding
VERDICTS = ("ok", "breach", "unjudged")           # T34b-3 (D): the verdict a fill line or a notice line carries
OPEN_AT_VENUE = ("accepted", "partly_filled")     # T34b-2 E2: an order of the ledger still working at the venue
WRONG_NUMBERS_MAX = 3                             # refused approvals in a New York day that lock approval
TICKET_UNWRITABLE = ("the ticket file cannot be written, so no approval can be judged or recorded; nothing is sent")


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
_ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ATTENTION_KIND = re.compile(r"[a-z0-9_-]{1,40}")  # T34b-2 (F2): an attention kind written as itself; else "unknown"
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


def paper_strategies(trader) -> tuple[str | None, tuple[str, ...] | None]:
    """The paper book's latest day and the strategies of its last summary (its `strategy_values`), or (that day,
    None) when there is no summary or it does not list them. Read only."""
    history = trader.state.get("history") or []
    last = history[-1] if history else {}
    day = last.get("day") if isinstance(last, dict) and last.get("day") else trader.state.get("last_day")
    values = last.get("strategy_values") if isinstance(last, dict) else None
    if not isinstance(values, dict):
        return (None if day is None else str(day)), None
    return (None if day is None else str(day)), tuple(str(s) for s in values)


BROKER_BOOK_FILE = "broker_book.json"           # config/broker_book.json: the strategies the broker book follows (T32)
FOLLOW_ID_MAX = 10**9                           # a hypothesis id is a whole number from 1 to this
_EXAMPLE = '{"follow": [26]}'


@dataclass(frozen=True)
class Follow:
    """Which strategies of the paper book the broker book follows (config/broker_book.json, T32), as the command
    line reads it: the hypothesis ids the owner named, their strategies' names in the same order, and a digest of
    the file's value and the names (a live ticket is bound to it); or `refused`, why the file or one of its ids
    cannot be followed, in words a person can act on (the run stops). `every` (rev 2): the names are every strategy
    of the paper book's latest day, so the broker book is the whole book, sized on the paper equity."""
    ids: tuple = ()
    names: tuple = ()
    digest: str | None = None
    refused: str | None = None
    every: bool = False


def _json_kind(x) -> str:
    """What a JSON value is, in words (rev 2: every kind reads as English)."""
    if type(x) is bool:
        return "true or false"
    if isinstance(x, str):
        return f"the text {json.dumps(x[:40])}"
    if isinstance(x, float):
        return f"the number {x!r}"
    if isinstance(x, int):
        shown = str(x)
        return f"the number {shown}" if len(shown) <= 40 else f"a number of {len(shown)} digits"
    if isinstance(x, dict):
        return "an object"
    if isinstance(x, list):
        return "a list"
    if x is None:
        return "null"
    return "a value that is not JSON"


def judge_follow(raw) -> tuple[tuple[int, ...] | None, str | None]:
    """The hypothesis ids of a broker book file's value: (ids, None), or (None, why) in words that say what to fix.
    The value must be an object whose `follow` is a list of at least one whole number from 1, none repeated; its
    other keys are allowed and ignored."""
    if not isinstance(raw, dict):
        return None, f"must be a JSON object such as {_EXAMPLE}, not {_json_kind(raw)}"
    if "follow" not in raw:
        return None, f'has no "follow" list of hypothesis ids (it should look like {_EXAMPLE})'
    ids = raw["follow"]
    if not isinstance(ids, list):
        return None, f'"follow" must be a list of hypothesis ids such as {_EXAMPLE}, not {_json_kind(ids)}'
    if not ids:
        return None, '"follow" names no hypothesis: at least one id is needed'
    seen: list[int] = []
    for x in ids:
        if type(x) is bool:
            return None, '"follow" holds true or false; a hypothesis id is a whole number such as 26'
        if type(x) is not int:
            return None, f'"follow" holds {_json_kind(x)}; a hypothesis id is a whole number such as 26 (not 26.0 or "26")'
        if not 1 <= x <= FOLLOW_ID_MAX:
            return None, f'"follow" holds {x}; a hypothesis id is a whole number from 1'
        if x in seen:
            return None, f'"follow" names hypothesis {x} more than once'
        seen.append(x)
    return tuple(seen), None


def _follow_problem(f) -> str | None:
    """Why the step cannot follow `f` (refused, or not a well-formed Follow), or None."""
    if type(f) is not Follow:
        return f"the follow list given is not a Follow but {type(f).__name__}"
    if f.refused is not None:
        return str(f.refused)[:MAX_TEXT * 3] or "it is refused"
    if type(f.ids) is not tuple or type(f.names) is not tuple:
        return "its ids and names are not tuples"
    if not f.ids:
        return "it names no strategy"
    if len(f.names) != len(f.ids):
        return "its ids and names do not pair up"
    if any(type(i) is not int or not 1 <= i <= FOLLOW_ID_MAX for i in f.ids) or len(set(f.ids)) != len(f.ids):
        return "its ids are not distinct whole numbers from 1"
    if not all(_plain(_NAME, n) for n in f.names) or len(set(f.names)) != len(f.names):
        return "its names are not distinct plain strategy names"
    if type(f.digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", f.digest):
        return "it carries no digest of the file it was read from"
    if type(f.every) is not bool:
        return "it does not say (true or false) whether it names every strategy of the paper book"
    return None


def _book_capital(paper: PaperTargets, follow, every: bool = False) -> float:
    """What the broker capital is spread over: the paper equity (the whole book, or a list that names every
    strategy of the paper book's latest day: rev 2), or the paper share of each strategy followed."""
    return float(paper.equity) if follow is None or every is True else float(paper.share) * len(follow)


def targets(paper: PaperTargets, capital: float, follow=None,
            every: bool = False) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """(per symbol, per strategy and symbol) target units at the broker: the paper's wanted units times
    capital / paper equity. With `follow` (strategy names), only those strategies, each on an equal part of the
    capital: their wanted units times capital / (paper share x number followed); with `every` (they are every
    strategy of the paper book's latest day), capital / paper equity, the whole book's scale."""
    scale = float(capital) / _book_capital(paper, follow, every)
    per_strategy = {s: {sym: float(u) * scale for sym, u in book.items()} for s, book in paper.wanted.items()
                    if follow is None or s in follow}
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


def _iso(seconds: float) -> str:
    return pd.Timestamp(seconds, unit="s", tz="UTC").tz_convert(NEW_YORK).isoformat()


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
LEDGER_NOT_SENT = ("intended", "refused")         # T22d-1: the ledger's states of an order never sent (NOT_COUNTED there)


def _ny_day(t) -> str | None:
    """The New York day of a time in seconds, or None when it cannot be read."""
    try:
        v = _as_float(t)
        return None if v is None else str(pd.Timestamp(v, unit="s", tz="UTC").tz_convert(NEW_YORK).date())
    except Exception:
        return None


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


TICKET_LAYOUT = 3
TICKET_KIND = "order-step tickets"
_NUMBER = re.compile(r"[0-9]{6}")
FINAL_OUTCOMES = ("sent", "not sent", "left over")   # a line's first row; "unknown" is never a first row
ROW_OUTCOMES = FINAL_OUTCOMES + ("unknown",)          # a correcting row may also say unknown, as the ledger then does
LEDGER_RECHECK_SECONDS = float(inspect.signature(OrderLedger.reconcile).parameters["recheck_seconds"].default)
REACHED_VENUE = ("accepted", "partly_filled", "filled", "cancelled", "rejected", "expired")   # ledger states: at the venue
_TICKET_TABLES = {
    "meta": ("key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL", ("key",)),
    "tickets": ("day TEXT NOT NULL, number TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, venue TEXT NOT NULL, "
                "environment TEXT NOT NULL, account TEXT NOT NULL, capital REAL NOT NULL, fractional INTEGER NOT NULL, "
                "equity REAL, cash REAL, settled_cash REAL, batch TEXT NOT NULL, PRIMARY KEY (day, number)", ("day", "number")),
    "lines": ("day TEXT NOT NULL, number TEXT NOT NULL, line INTEGER NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL "
              "CHECK (side IN ('buy', 'sell')), units REAL NOT NULL, quote REAL NOT NULL, limit_price REAL NOT NULL, "
              "value REAL NOT NULL, PRIMARY KEY (day, number, line)", ("day", "number", "line")),
    "fates": ("day TEXT NOT NULL, number TEXT NOT NULL, fate TEXT NOT NULL CHECK (fate IN ('used', 'expired', 'superseded')), "
              "time REAL NOT NULL, batch TEXT NOT NULL, PRIMARY KEY (day, number)", ("day", "number")),
    "tags": ("day TEXT NOT NULL, number TEXT NOT NULL, line INTEGER NOT NULL, tag TEXT NOT NULL UNIQUE, time REAL NOT NULL, "
             "batch TEXT NOT NULL, PRIMARY KEY (day, number, line)", ("day", "number", "line")),
    "outcomes": ("day TEXT NOT NULL, number TEXT NOT NULL, line INTEGER NOT NULL, seq INTEGER NOT NULL, outcome TEXT NOT "
                 "NULL CHECK (outcome IN ('sent', 'not sent', 'left over', 'unknown')), tag TEXT, reason TEXT, time REAL "
                 "NOT NULL, batch TEXT NOT NULL, PRIMARY KEY (day, number, line, seq)", ("day", "number", "line", "seq")),
    "attempts": ("batch TEXT NOT NULL PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, after_ticket INTEGER", ("batch",)),
    "verdicts": ("batch TEXT NOT NULL PRIMARY KEY, verdict TEXT NOT NULL CHECK (verdict IN ('accepted', 'refused')), "
                 "reason TEXT, time REAL NOT NULL", ("batch",)),
}
_LINE = "day IS NEW.day AND number IS NEW.number AND line IS NEW.line"
_TICKET_RULES = [   # what an outcome may say: never "left over" after a tag, never "sent" without the line's own tag;
    # a line's rows are numbered from 0; a later row only corrects a tagged line, with its tag, to another outcome
    "CREATE TRIGGER IF NOT EXISTS outcomes_in_order BEFORE INSERT ON outcomes WHEN NEW.seq IS NOT "
    f"COALESCE((SELECT MAX(seq) FROM outcomes WHERE {_LINE}) + 1, 0) "
    "BEGIN SELECT RAISE(ABORT, 'the rows of a line are numbered in order'); END",
    "CREATE TRIGGER IF NOT EXISTS outcomes_first_is_final BEFORE INSERT ON outcomes WHEN NEW.seq = 0 AND "
    "NEW.outcome = 'unknown' BEGIN SELECT RAISE(ABORT, 'a first row is a final outcome'); END",
    "CREATE TRIGGER IF NOT EXISTS outcomes_only_corrections BEFORE INSERT ON outcomes WHEN NEW.seq > 0 AND "
    f"(NEW.outcome = 'left over' OR NEW.tag IS NULL OR NOT EXISTS (SELECT 1 FROM tags WHERE {_LINE} AND tag IS NEW.tag) "
    f"OR NEW.outcome IS (SELECT outcome FROM outcomes WHERE {_LINE} AND seq = NEW.seq - 1)) "
    "BEGIN SELECT RAISE(ABORT, 'a later row only corrects a tagged line to another outcome'); END",
    f"CREATE TRIGGER IF NOT EXISTS tags_before_outcomes BEFORE INSERT ON tags WHEN EXISTS (SELECT 1 FROM outcomes WHERE "
    f"{_LINE}) BEGIN SELECT RAISE(ABORT, 'a line is tagged before it has an outcome'); END",
    "CREATE TRIGGER IF NOT EXISTS outcomes_left_over_untagged BEFORE INSERT ON outcomes WHEN NEW.outcome = 'left over' "
    "AND EXISTS (SELECT 1 FROM tags WHERE day IS NEW.day AND number IS NEW.number AND line IS NEW.line) "
    "BEGIN SELECT RAISE(ABORT, 'a line with a tag is never left over'); END",
    "CREATE TRIGGER IF NOT EXISTS outcomes_sent_tagged BEFORE INSERT ON outcomes WHEN NEW.outcome = 'sent' "
    "AND NOT EXISTS (SELECT 1 FROM tags WHERE day IS NEW.day AND number IS NEW.number AND line IS NEW.line "
    "AND tag IS NEW.tag) BEGIN SELECT RAISE(ABORT, 'a sent line carries its own tag'); END",
    "CREATE TRIGGER IF NOT EXISTS verdicts_after_attempt BEFORE INSERT ON verdicts WHEN NOT EXISTS "
    "(SELECT 1 FROM attempts WHERE batch IS NEW.batch) BEGIN SELECT RAISE(ABORT, 'a verdict follows its attempt'); END",
]


def _ticket_schema() -> list[str]:
    out = []
    for table, (columns, key) in _TICKET_TABLES.items():
        same = " AND ".join(f"{c} IS NEW.{c}" for c in key)
        out += [f"CREATE TABLE IF NOT EXISTS {table} ({columns})",
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} rows are append-only'); END",
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} rows are never deleted'); END",
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} "
                f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {same}) "
                f"BEGIN SELECT RAISE(ABORT, '{table} rows are never replaced'); END"]
    return out + _TICKET_RULES


def _expected_ticket_master() -> list[tuple]:
    """What sqlite_master of a ticket file must hold: every table, index and trigger, with its exact definition."""
    db = sqlite3.connect(":memory:")
    try:
        for statement in _ticket_schema():
            db.execute(statement)
        return sorted(db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"), key=repr)
    finally:
        db.close()


def ticket_number() -> str:
    """A ticket's number: six digits from the system's secure random source."""
    return f"{secrets.randbelow(1_000_000):06d}"


def ticket_life() -> float:
    """TICKET_LIFE_SECONDS as it is now, refused (OrderStepError) unless above 0 and at most TICKET_LIFE_MAX_SECONDS."""
    life = _as_float(TICKET_LIFE_SECONDS)
    if life is None or not 0 < life <= TICKET_LIFE_MAX_SECONDS:
        raise OrderStepError(f"the ticket life must be above 0 and at most {TICKET_LIFE_MAX_SECONDS // 60} minutes; "
                             f"TICKET_LIFE_SECONDS is {TICKET_LIFE_SECONDS!r}")
    return life


def ticket_expiry(ticket: dict) -> float:
    """When a ticket stops being live: the earliest of its stored expiry, its creation plus TICKET_LIFE_SECONDS as it
    is now, and its creation plus TICKET_LIFE_MAX_SECONDS. A life that is not a positive number: at its creation."""
    created = float(ticket["created"])
    life = _as_float(TICKET_LIFE_SECONDS)
    life = life if life is not None and life > 0 else 0.0
    return min(float(ticket["expires"]), created + life, created + TICKET_LIFE_MAX_SECONDS)


def ticket_path_for(state_path: str | Path) -> str | Path:
    """The ticket file for a state file: beside it (order_step-<venue>.tickets.sqlite)."""
    if str(state_path) == ":memory:":
        return ":memory:"
    p = Path(state_path)
    return p.with_name(f"{p.stem}.tickets.sqlite")


def _outcome_from_ledger(rec: dict | None) -> tuple[str | None, str]:
    """A tagged line's final outcome from its order's ledger record, or (None, why) while it is not known yet."""
    if rec is None:
        return "not sent", "the run ended between recording the tag and intend; the order was never intended"
    state = rec.get("state")
    if state in REACHED_VENUE:
        return "sent", f"the order reached the venue (the ledger says {state})"
    if state == "intended":
        return "not sent", "intended and never transmitted"
    if state == "refused":
        return "not sent", "the venue did not take it"
    if state == "never_arrived":
        since = _as_float(rec.get("never_arrived_at"))
        until = (f"until {_iso(since + LEDGER_RECHECK_SECONDS)}" if since is not None and math.isfinite(since)
                 else f"for {LEDGER_RECHECK_SECONDS / 86400:g} days after it was so marked")
        return "not sent", (f"the venue never had it (the ledger says never_arrived); the order ledger rechecks it {until}, "
                            "and a later run corrects this row if it turns up")
    return None, f"the ledger says {state}: not known yet"


class TicketBook:
    """The tickets of the propose and approve runs, in their own small file: each ticket, its lines, what became of
    the ticket (used, expired or superseded, once), the tag of each line handed to intend (before intend), the
    outcome rows of each line (its latest row is its outcome; a later row only corrects a tagged line to what the
    order ledger then says), and every approval tried with its verdict. Append-only: no row is updated, deleted
    or replaced (triggers, checked against their definitions whenever the file is opened); every write is one
    IMMEDIATE transaction. Holds the random key of the account hash, made once with the file. With create False a
    missing file is not made (OrderStepError)."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time, create: bool = True) -> None:
        self.path, self.clock = str(path), clock
        if self.path != ":memory:":
            if not create and not Path(self.path).exists():
                raise OrderStepError("there is no ticket file")
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA synchronous = FULL")
            with self._write():
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                master = sorted((tuple(r) for r in self._db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master")),
                                key=repr)
                if master:
                    kind = None
                    if version == TICKET_LAYOUT and master == _expected_ticket_master():
                        row = self._db.execute("SELECT value FROM meta WHERE key = 'kind'").fetchone()
                        kind = row[0] if row else None
                    if kind != TICKET_KIND:
                        raise OrderStepError("the ticket file's tables or triggers are not those of an order-step ticket "
                                             f"file of layout {TICKET_LAYOUT}; it is not used")
                else:
                    if version != 0 or not create:
                        raise OrderStepError("the ticket file is a database of another kind; it is not used")
                    for statement in _ticket_schema():
                        self._db.execute(statement)
                    self._db.execute("INSERT INTO meta (key, value) VALUES ('kind', ?)", (TICKET_KIND,))
                    self._db.execute("INSERT INTO meta (key, value) VALUES ('account_key', ?)", (os.urandom(32).hex(),))
                    self._db.execute(f"PRAGMA user_version = {TICKET_LAYOUT}")
            row = self._db.execute("SELECT value FROM meta WHERE key = 'account_key'").fetchone()
            if row is None or not re.fullmatch(r"[0-9a-f]{64}", str(row[0])):
                raise OrderStepError("the ticket file has no usable account key; it is not used")
            self._key = bytes.fromhex(row[0])
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

    def layout(self) -> int:
        return int(self._db.execute("PRAGMA user_version").fetchone()[0])

    def account_hash(self, account_id: str) -> str:
        """The account as the ticket records it: HMAC-SHA256 under this file's own random key."""
        return hmac.new(self._key, account_id.encode("utf-8"), hashlib.sha256).hexdigest()

    def find(self, number: str) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM tickets WHERE number = ? ORDER BY rowid", (number,))]

    def taken(self, day: str, number: str) -> bool:
        return self._db.execute("SELECT 1 FROM tickets WHERE day = ? AND number = ?", (day, number)).fetchone() is not None

    def lines(self, day: str, number: str) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM lines WHERE day = ? AND number = ? ORDER BY line", (day, number))]

    def fate(self, day: str, number: str) -> dict | None:
        row = self._db.execute("SELECT * FROM fates WHERE day = ? AND number = ?", (day, number)).fetchone()
        return dict(row) if row else None

    def tags(self, day: str, number: str) -> dict[int, str]:
        return {int(r["line"]): r["tag"] for r in self._db.execute("SELECT * FROM tags WHERE day = ? AND number = ?", (day, number))}

    def outcomes(self, day: str, number: str) -> dict[int, dict]:
        """Each line's outcome: its latest row."""
        return {int(r["line"]): dict(r) for r in self._db.execute(
            "SELECT * FROM outcomes o WHERE day = ? AND number = ? AND seq = (SELECT MAX(seq) FROM outcomes p WHERE "
            "p.day = o.day AND p.number = o.number AND p.line = o.line)", (day, number))}

    def history(self, day: str, number: str) -> dict[int, list[dict]]:
        """Each line's rows, oldest first."""
        out: dict[int, list[dict]] = {}
        for r in self._db.execute("SELECT * FROM outcomes WHERE day = ? AND number = ? ORDER BY line, seq", (day, number)):
            out.setdefault(int(r["line"]), []).append(dict(r))
        return out

    def _close_unused(self, day: str, number: str, fate: str, reason: str, batch: str) -> None:
        """Inside a write: give an unused ticket its fate and each of its lines "not sent"."""
        now = float(self.clock())
        self._db.execute("INSERT INTO fates (day, number, fate, time, batch) VALUES (?, ?, ?, ?, ?)", (day, number, fate, now, batch))
        for (line,) in self._db.execute("SELECT line FROM lines WHERE day = ? AND number = ? ORDER BY line", (day, number)).fetchall():
            self._db.execute("INSERT INTO outcomes (day, number, line, seq, outcome, tag, reason, time, batch) "
                             "VALUES (?, ?, ?, 0, 'not sent', NULL, ?, ?, ?)", (day, number, line, reason, now, batch))

    def live_mark(self, day: str, number: str) -> dict | None:
        """A live ticket's mark ({"mode": "live", "files": the digest of the owner's files it was made under}), or None
        for a ticket of the test environment's paper mode."""
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (f"live:{day}:{number}",)).fetchone()
        if row is None:
            return None
        try:
            mark = json.loads(row[0])
        except ValueError:
            return {"mode": "unreadable"}
        return mark if isinstance(mark, dict) else {"mode": "unreadable"}

    def follow_mark(self, day: str, number: str) -> dict | None:
        """A live ticket's broker book ({"digest": the follow list's digest, "ids": ..., "names": ...}, T32), or None
        when the ticket has none ({} when it cannot be read)."""
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (f"broker_book:{day}:{number}",)).fetchone()
        if row is None:
            return None
        try:
            mark = json.loads(row[0])
        except ValueError:
            return {}
        return mark if isinstance(mark, dict) else {}

    def create(self, ticket: dict, lines: list[dict], live: dict | None = None, follow: dict | None = None) -> None:
        """Write a ticket and its lines, and mark every earlier unused ticket of the same venue and account superseded,
        in one transaction: only the latest ticket is live. A live ticket also gets its mark (`live`) and the broker
        book it follows (`follow`, T32) in the meta table, in the same transaction."""
        cols = ("day", "number", "created", "expires", "venue", "environment", "account", "capital", "fractional", "equity",
                "cash", "settled_cash", "batch")
        with self._write():
            older = self._db.execute("SELECT day, number FROM tickets t WHERE venue = ? AND account = ? AND NOT EXISTS "
                                     "(SELECT 1 FROM fates f WHERE f.day = t.day AND f.number = t.number) ORDER BY rowid",
                                     (ticket["venue"], ticket["account"])).fetchall()
            for day, number in older:
                self._close_unused(day, number, "superseded", "superseded by a later ticket, unused", ticket["batch"])
            self._db.execute(f"INSERT INTO tickets ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             tuple(ticket[c] for c in cols))
            self._db.executemany("INSERT INTO lines (day, number, line, symbol, side, units, quote, limit_price, value) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 [(ticket["day"], ticket["number"], ln["line"], ln["fund"], ln["side"], ln["units"],
                                   ln["quote"], ln["limit"], ln["value"]) for ln in lines])
            if live is not None:
                self._db.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                                 (f"live:{ticket['day']}:{ticket['number']}", json.dumps(live, sort_keys=True)))
            if follow is not None:
                self._db.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                                 (f"broker_book:{ticket['day']}:{ticket['number']}", json.dumps(follow, sort_keys=True)))

    def mark_used(self, day: str, number: str, batch: str) -> None:
        """Mark the ticket used by `batch`; refused (OrderStepError) when it already has a fate."""
        try:
            with self._write():
                self._db.execute("INSERT INTO fates (day, number, fate, time, batch) VALUES (?, ?, 'used', ?, ?)",
                                 (day, number, float(self.clock()), batch))
        except sqlite3.IntegrityError:
            raise OrderStepError("the ticket has been used, has expired or was superseded") from None

    def mark_expired(self, day: str, number: str, batch: str) -> None:
        """Record that the ticket expired unused, each of its lines "not sent"; nothing when it has a fate."""
        with self._write():
            if not self._db.execute("SELECT 1 FROM fates WHERE day = ? AND number = ?", (day, number)).fetchone():
                self._close_unused(day, number, "expired", "the ticket expired unused", batch)

    def note_tag(self, day: str, number: str, line: int, tag: str, batch: str) -> None:
        """The tag a line's order is given, recorded before its intend."""
        with self._write():
            self._db.execute("INSERT INTO tags (day, number, line, tag, time, batch) VALUES (?, ?, ?, ?, ?, ?)",
                             (day, number, int(line), tag, float(self.clock()), batch))

    def note_outcome(self, day: str, number: str, line: int, outcome: str, tag: str | None, reason: str | None,
                     batch: str) -> None:
        """A line's first row (refused by the file when it has one)."""
        if outcome not in FINAL_OUTCOMES:
            raise OrderStepError(f"{outcome!r} is not a final outcome")
        with self._write():
            self._db.execute("INSERT INTO outcomes (day, number, line, seq, outcome, tag, reason, time, batch) "
                             "VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)",
                             (day, number, int(line), outcome, tag, reason, float(self.clock()), batch))

    def correct(self, day: str, number: str, line: int, outcome: str, tag: str, reason: str, batch: str) -> None:
        """A correcting row, after the line's latest: the order ledger now says something else about its tag."""
        if outcome not in ROW_OUTCOMES:
            raise OrderStepError(f"{outcome!r} is not an outcome")
        with self._write():
            self._db.execute("INSERT INTO outcomes (day, number, line, seq, outcome, tag, reason, time, batch) VALUES "
                             "(?, ?, ?, (SELECT MAX(seq) + 1 FROM outcomes WHERE day = ? AND number = ? AND line = ?), "
                             "?, ?, ?, ?, ?)", (day, number, int(line), day, number, int(line), outcome, tag, reason,
                                                float(self.clock()), batch))

    def settle(self, ledger, batch: str, only: tuple[str, str] | None = None) -> list[dict]:
        """Make every line of every used ticket (or of `only`) mirror the order ledger: a line with no row gets its
        first (an untagged line "left over"; a tagged one the outcome its order's ledger record gives, when that is
        known); a tagged line whose latest row no longer says what the ledger says gets a correcting row ("unknown"
        while the ledger does not know). Run where no run of that ticket is still going (under the run lock). Returns
        the corrections it wrote."""
        if only is not None:
            used = [only] if (self.fate(*only) or {}).get("fate") == "used" else []
        else:
            used = [(r["day"], r["number"]) for r in self._db.execute("SELECT day, number FROM fates WHERE fate = 'used' "
                                                                      "ORDER BY rowid")]
        corrections = []
        self.disagreements = []
        for day, number in used:
            latest, tags = self.outcomes(day, number), self.tags(day, number)
            for ln in self.lines(day, number):
                line, tag = int(ln["line"]), tags.get(int(ln["line"]))
                row = latest.get(line)
                if tag is None:                          # never tagged: never at the ledger, left over for good
                    if row is None:
                        self.note_outcome(day, number, line, "left over", None, "the run ended before this order's intend",
                                          batch)
                    continue
                rec = ledger.order(tag)
                outcome, why = _outcome_from_ledger(rec)
                if row is None:
                    if outcome is not None:              # unknown: no row yet, a later run writes it
                        self.note_outcome(day, number, line, outcome, tag, why, batch)
                    continue
                now = outcome if outcome is not None else "unknown"
                if now == row["outcome"]:
                    continue
                if row["outcome"] == "sent":             # never turned back: the order reached the venue once
                    self.disagreements.append({"number_ends": number[-2:], "line": line, "fund": ln["symbol"], "tag": tag,
                                               "record": "sent", "ledger": rec.get("state") if rec else "nothing"})
                    continue
                if now == "sent" and rec is not None and rec.get("never_arrived_at") is not None:
                    why = f"sent: found at the venue on recheck (the ledger now says {rec.get('state')})"
                else:
                    why = f"corrected: the order ledger now says {rec.get('state') if rec else 'nothing'}; {why}"
                self.correct(day, number, line, now, tag, why, batch)
                corrections.append({"number_ends": number[-2:], "line": line, "fund": ln["symbol"], "from": row["outcome"],
                                    "to": now, "tag": tag, "reason": why})
        return corrections

    def note_attempt(self, batch: str, day: str) -> None:
        """An approval tried, recorded before its number is compared, with the latest ticket written before it."""
        with self._write():
            self._db.execute("INSERT INTO attempts (batch, day, time, after_ticket) VALUES (?, ?, ?, "
                             "(SELECT MAX(rowid) FROM tickets))", (batch, day, float(self.clock())))

    def note_verdict(self, batch: str, verdict: str, reason: str | None) -> None:
        with self._write():
            self._db.execute("INSERT INTO verdicts (batch, verdict, reason, time) VALUES (?, ?, ?, ?)",
                             (batch, verdict, reason, float(self.clock())))

    def verdict(self, batch: str) -> dict | None:
        """The verdict recorded for `batch` ({"verdict", "reason"}), or None."""
        row = self._db.execute("SELECT verdict, reason FROM verdicts WHERE batch = ?", (batch,)).fetchone()
        return None if row is None else {"verdict": row[0], "reason": row[1]}

    def wrong_count(self, day: str, batch: str) -> int:
        """Approvals tried on `day` since the latest ticket was written that were not accepted (refused, or never
        judged because the run died), other than `batch`'s own. A propose that writes a ticket lifts the lock."""
        return int(self._db.execute(
            "SELECT COUNT(*) FROM attempts a WHERE day = ? AND batch != ? AND after_ticket IS (SELECT MAX(rowid) FROM tickets) "
            "AND NOT EXISTS (SELECT 1 FROM verdicts v WHERE v.batch = a.batch AND v.verdict = 'accepted')",
            (day, batch)).fetchone()[0])


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

    def follows(self) -> bool:
        """Whether the file this run locked is still the one at the lock's path (T22d-1, A2): not unlinked, not
        replaced by another file (which a second run could lock as well). A lock with no file (":memory:") follows."""
        if self.path is None:
            return True
        if self.fh is None:
            return False
        try:
            held, there = os.fstat(self.fh.fileno()), os.stat(self.path)
        except OSError:
            return False
        return held.st_nlink > 0 and (held.st_dev, held.st_ino) == (there.st_dev, there.st_ino)

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


class _NoSend:
    """The order ledger as a propose run holds it: everything but intend and submit_once."""

    def __init__(self, ledger) -> None:
        self._ledger = ledger

    def intend(self, *a, **k):
        raise RuntimeError("a propose run never intends an order")

    def submit_once(self, *a, **k):
        raise RuntimeError("a propose run never sends an order")

    def __getattr__(self, name):
        return getattr(self._ledger, name)


_ABSENT = object()


def run_order_step(*, ledger: OrderLedger, venue, paper, quotes: Callable, journal, limits_file, state,
                   clock: Callable[[], float] = wall_clock, calendar=None, halt_file=risk_gate.HALT_FILE,
                   latch_file=None, capital: float | None = None, fractional: bool = False, propose: bool = False,
                   approve: str | None = None, live=None, second_opinion=None, follow=None) -> dict:
    """One order step (see the module notes). `paper` is PaperTargets or a callable giving them; `quotes(symbols)`
    gives {symbol: (price, time)}; `state` is an OrderStepState or the path of its file; `calendar` answers
    is_session, session_open and session_close (default: the XNYS calendar). Returns the summary, also journaled.
    Never raises because of the venue, the quotes or the paper book; a crash of the process mid-run is safe.
    `propose` writes a ticket instead of sending; `approve` (the ticket's number) sends only what that ticket holds.
    `live` (a go_live.LiveFiles, with propose or approve and a capital) is live mode: Webull's production environment
    under the four locks of the go-live gate; `second_opinion` is the second price source's verdict function
    (default: second_quote.second_opinion), asked only in live mode. `follow` (a Follow: config/broker_book.json as
    the command line reads it, T32) makes the broker book follow only the strategies it names; None follows the
    whole book, except in live mode, which stops without one."""
    mode = "propose" if propose else ("approve" if approve is not None else None)
    run = _Run(ledger, venue, paper, quotes, journal, limits_file, state, clock, calendar, halt_file, latch_file,
               capital, fractional)
    if follow is not None:
        run.follow, run.follow_problem = follow, _follow_problem(follow)
        if run.follow_problem is None:
            run.follow_names, run.follow_every = follow.names, follow.every
    if live is not None:
        run.live = live
        run.expected_env = LIVE_ENVIRONMENT
        if second_opinion is None:
            from valedia_trader.data_and_fees.second_quote import second_opinion
        run.second_opinion = second_opinion
    if mode is not None:
        run.mode, run.approve_number = mode, approve
        if propose and approve is not None:
            run.mode = "both"
        elif propose:
            run.ledger = _NoSend(ledger)
    return run.go()


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
        self.journaled_symbols: set = set()              # T34b-2 J2: the symbols of the fills this run journaled
        self.quote_times: dict[str, float] = {}
        self.asked: dict[str, float] = {}               # when each symbol was last asked of the quote function
        self.torn_lines: list[dict] = []
        self.paper_read: PaperTargets | None = None
        self._line_ended = False
        self._locked = False
        self._class_later = False
        self.mode = None                                 # None (an ordinary run), "propose" or "approve"
        self.approve_number = None
        self.tickets: TicketBook | None = None
        self.run_path: str | None = None                 # the state file this run is locked on
        self.live = None                                 # live mode: the go_live.LiveFiles its locks are read from
        self.expected_env = TEST_ENVIRONMENT             # the environment the venue and the tickets must name
        self.second_opinion = None
        self.locks = None                                # live mode: the go_live.LockState judged at the start
        self.live_exposure = 0.0                         # live mode: what this ledger holds plus the buys passed so far
        self.run_lock: _RunLock | None = None            # T22d-1 (A2): the lock this run holds, checked to follow its file
        self.marks_judged: dict[tuple, str] = {}         # T22d-1 (A1): the second source's verdict per (symbol, price, time)
        self.live_book: dict[str, float] = {}            # T22d-1 (A3): this ledger's book and the marks in hand, from which
        self.live_marks: dict[str, float] = {}           # ... the exposure is worked out again at each order
        self.live_buys: list[float] = []                 # ... with the buys passed so far, in order
        self.journal_peak: float | None = None           # T22d-1 (A4): the highest equity the journal holds for this venue
        self._ticket_expired_seen = False                # once a reading is past the ticket's expiry, it stays expired
        self.disagreements: list[dict] = []
        self.ticket: dict | None = None                  # approve: the ticket's row; propose: the ticket written
        self.ticket_lines: list[dict] = []
        self.ticket_out: dict[int, dict] = {}            # approve: what became of each line in this run
        self.ticket_used = False
        self.ticket_number_made = None
        self.account = None
        self.dropped: list[dict] = []
        self.capital_used = None
        self.follow = None                               # T32: the Follow given (None: the whole book)
        self.follow_problem = None                       # why it cannot be followed, or None
        self.follow_names: tuple | None = None           # the strategies followed, once the Follow is well formed
        self.follow_every = False                        # rev 2: they are every strategy of the latest day
        self.attention_before: int | None = None         # T34b-2 (F2): the largest attention id when the run took the ledger
        self.final_unclean = False                       # T34b-2 (F1): the final reconcile was not clean
        self.cap_refs: set[str] = set()                  # T34b-3 (D): the breaches this run journaled (fill-<id>, notice-<id>)
        self.carried_refs: set[str] = set()              # ... breaches journaled earlier that no summary covers yet
        self.session_day = False                         # ... the run's New York day passed the calendar test (B1)
        self.pending_unjudged: dict[str, dict] = {}      # ... earlier unjudged verdicts (R3): ref -> the line, until settled
        self._seen_notice_ids: set[int] = set()          # ... the notices the journal already holds
        self.notice_kinds: set[str] = set()              # ... the kinds of the notices this run journaled
        self.cap_unjudged: set[str] = set()              # T34b-2: the symbols a fill of this run could not be judged for
        self.first_result: dict | None = None            # T34b-3 (G): the first reconcile's result, for the stop
        self.reconcile_pending: dict | None = None       # T34b-2 E2: what the final reconcile set aside, information only
        self.faults_failed = False                       # T34b-2 E4: the faults could not be worked out (once)
        self.over_ceiling: dict | None = None            # T34b-2 addendum 1 (live): the book above max_capital, information only
        self.working_stop: dict | None = None            # T34b-2 (S1, live): {"pct", "basis", "n"}, set once before any order
        self.wall: float | None = None                   # ... the limits' max_daily_loss_pct as this run loaded them
        self.day_stop: dict | None = None                # T34b-2 (S2, S4, live): set, the run is in the day stop

    # -- helpers
    def _follow_view(self) -> dict:
        return {"ids": list(self.follow.ids), "names": list(self.follow.names)}

    def _live_follow(self) -> str | None:
        """Live mode: why the run cannot follow what it was given (it never follows the whole book), or None."""
        if self.follow is None:
            return ("the broker book (config/broker_book.json) was not read: live mode follows only the strategies "
                    f"that file names (such as {_EXAMPLE}) and never follows the whole book by default")
        if self.follow_problem is not None:
            return f"the broker book's follow list: {self.follow_problem}"
        return None

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

    def _live_place(self) -> str | None:
        """Live mode reads the owner's files and the paper record only from the repository (go_live.live_repo()): the
        limits file this run was given must be its config/risk.json and the journal its journal/ folder. Why not, or
        None. Compared as given (links are refused by the owner-file reader); nothing is opened."""
        from valedia_trader.execution_and_risk import go_live
        repo = go_live.live_repo()
        try:
            limits = Path(os.path.abspath(os.fspath(self.limits_file)))
            folder = getattr(self.journal, "dir", None)
            journal = None if folder is None else Path(os.path.abspath(os.fspath(folder)))
        except (TypeError, ValueError):
            limits = journal = None
        if limits != repo / "config" / "risk.json" or journal != repo / "journal":
            return ("live mode reads the owner's files and the paper record only from the repository itself "
                    "(its config/ and journal/); this run was given another folder's")
        return None

    def go(self) -> dict:
        try:
            now = self._now()
            self.day = str(pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK).date())
        except _Stop as stop:
            self.day = str(pd.Timestamp.now(tz=NEW_YORK).date())
            return self._finish("stopped", stop.stage, stop.reason)
        stamp = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
        self.batch = f"os-{stamp:%Y%m%d-%H%M%S}-{os.urandom(3).hex()}"
        if self.mode == "both":                          # before the lock: writes nothing anywhere
            return self._finish("stopped", "ticket", "propose and approve cannot be asked for in one run")
        if self.live is not None:                        # live mode: only as a ticket run, a capital, whole shares
            why = ("live mode runs only with propose or approve" if self.mode is None else
                   "live mode needs the capital given (at most the confirmation's max_capital)" if self.capital is None else
                   "live mode trades whole shares only" if self.fractional else None)
            if why is not None:
                return self._finish("stopped", "live", why)
            why = self._live_place()                     # before the lock: nothing read, nothing written
            if why is not None:
                return self._finish("stopped", "lock", why)
        try:
            self._environment()                          # 1: the venue's name, environment and host only
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        path = self.state_arg.path if isinstance(self.state_arg, OrderStepState) else str(self.state_arg)
        self.run_path = path
        lock = self.run_lock = _RunLock(path)
        if not lock.acquire():                           # everything below runs under the lock
            return self._finish("stopped", "session", "another order step is running on this venue's state file")
        self._locked = True                              # from here on the journal may be written
        try:
            if self.live is not None:                    # the four locks, before anything is read from the account
                why = self._live_locks(path)
                if why is not None:
                    return self._finish("stopped", "lock", why)
            try:
                self._scan_journal()                     # before anything is asked of the venue
                self._session(now)                       # 2
                self._check_events_class()               # ... an account class only a property gives, read now
                self.paper_read = self._paper()          # 5, read now: a stale book stops before any venue call
                self._paper_day(self.paper_read)
                if self.follow_problem is not None:      # T32: a follow list that cannot be followed stops here
                    raise _Stop("follow", f"the broker book's follow list: {self.follow_problem}; nothing is sent")
            except _Stop as stop:
                return self._finish("stopped", stop.stage, stop.reason)
            try:
                self.state = self.state_arg if isinstance(self.state_arg, OrderStepState) else OrderStepState(path, self.clock)
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", "baseline", f"the order step's state file could not be opened: {_describe(exc)}")
            try:
                if self.mode is not None:                # the ticket file, beside the state file, under the same lock
                    try:
                        self.tickets = TicketBook(ticket_path_for(path), self.clock)
                    except (OrderStepError, sqlite3.Error, OSError) as exc:
                        return self._finish("stopped", "ticket", f"the ticket file could not be opened: {_describe(exc)}")
                return self._rest(now)
            finally:
                if self.tickets is not None:
                    try:
                        self.tickets.close()
                    except sqlite3.Error:
                        pass
                if not isinstance(self.state_arg, OrderStepState):
                    self.state.close()
        except Exception as exc:                         # a fault of the order step's own: still stopped and journaled
            return self._finish("stopped", "unexpected", f"the run failed unexpectedly: {_venue_error(exc)}")
        finally:
            lock.release()

    def _rest(self, now: float) -> dict:
        if self.live is not None:                        # T22d-1 (A2): the lock still the file this run took, at the start
            why = self._lock_lost("nothing is sent")
            if why is not None:
                return self._finish("stopped", "lock", why)
        if self.mode is not None:                        # the ticket's checks and the account, before any other venue call
            try:
                self._ticket_first(now)
            except _Stop as stop:
                return self._finish("stopped", stop.stage, stop.reason)
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", "ticket", f"the ticket file failed: {_local_error(exc)}; nothing is sent")
        try:
            if self.live is not None:                    # T22d-1 (A4): the state file knows what the ledger and journal do
                self._live_counters()
            baseline = self._baseline()                  # 3
            self._reconcile_and_cancel(baseline)         # 4
            paper = self.paper_read                      # 5
            book = broker_book(self.ledger.fills())
            if any(abs(u) > MAX_UNITS for u in book.values()):
                raise _Stop("reconcile", f"a position in the broker book is above {MAX_UNITS:,.0f} units in size; a person must look")
            capital = paper.equity if self.capital is None else self.capital
            if not _finite(capital) or capital <= 0:
                raise _Stop("targets", f"the capital must be a positive finite number, not {capital!r}")
            self.capital_used = float(capital)
            self.paper_equity = paper.equity
            self.target_units, per_strategy = targets(paper, float(capital), self.follow_names, self.follow_every)
            if self.follow_names is None or self.follow_every:
                min_value = paper.min_trade_pct * paper.share * float(capital) / paper.equity
            else:                                        # T32: the smallest trade at the followed strategies' scale
                min_value = paper.min_trade_pct * paper.share * float(capital) / _book_capital(paper, self.follow_names)
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
            final = self._reconcile(baseline, "after")  # 11
        except _Stop as stop:
            return self._finish("stopped", "after", stop.reason)
        except (OrderError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "after", f"the last reconcile could not be completed: {_local_error(exc)}")
        self._judge_final(final)                         # T34b-2 (F1, F3): a failure here is the run's own (unexpected)
        if self.mode == "propose":
            try:
                self._make_ticket()
            except _Stop as stop:
                return self._finish("stopped", stop.stage, stop.reason)
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", "ticket", f"the ticket could not be written: {_local_error(exc)}")
        return self._finish("done", None, None)

    # -- live mode (go_live): the locks, the account, each order's caps and second price
    def _live_locks(self, path: str) -> str | None:
        """Judge the four locks on the run's New York day; None when all are met. An unmet lock in an approve run is
        recorded as a refused approval of this batch (step 1's mark), in the ticket file when there is one."""
        from valedia_trader.execution_and_risk import go_live
        try:
            self.locks = go_live.judge_locks(self.live, date.fromisoformat(self.day), self.capital)
            why = self.locks.unmet()
            if why is None:                              # T32: the broker book file, judged with the locks
                why = self._live_follow()
        except Exception as exc:
            why = f"the locks could not be judged: {_describe(exc)}"
        if why is None or self.mode != "approve":
            return why
        try:
            book = TicketBook(ticket_path_for(path), self.clock, create=False)
        except (OrderStepError, sqlite3.Error, OSError):
            return why                                   # no ticket file: nothing to mark
        try:
            book.note_attempt(self.batch, self.day)
            book.note_verdict(self.batch, "refused", why[:1000])
        except (sqlite3.Error, OSError):
            pass
        finally:
            book.close()
        return why

    def _live_account(self, account: str) -> str | None:
        """Live mode, at the first account read: the account the venue will trade is the confirmation's (its class,
        its last 4). Why not, or None; no more than the last 4 of an id ever appears."""
        conf = self.locks.confirmation
        try:
            cls = self.venue.account_class
        except Exception as exc:
            return f"lock 1 (the confirmation): the venue's account class could not be read: {_venue_error(exc)}"
        if not (type(cls) is str and cls == conf["account_class"]):
            return (f"lock 1 (the confirmation): it names an {conf['account_class']} account; the venue's account is of "
                    "another class")
        if account[-4:] != conf["account_ends"]:
            return (f"lock 1 (the confirmation): it names the account ending {conf['account_ends']}; the venue's account "
                    f"ends {account[-4:]}")
        return None

    def _live_order(self, c: dict, units: float, limit: float, quote: float) -> str | None:
        """Live mode, for one order before its ticket line (propose) or its intend (approve): its value at its limit
        at most max_order_value; what this ledger holds plus this ticket's buys at most max_capital; the second
        price source agreeing on the fresh quote. Why not, or None (a buy is then counted against max_capital)."""
        conf = self.locks.confirmation
        value = abs(units) * limit
        if not value <= conf["max_order_value"]:
            return (f"lock 1 (the confirmation): the order's value {value:,.2f} at its limit is above max_order_value "
                    f"{conf['max_order_value']:,.2f}")
        self.live_exposure = self._exposure_now()       # T22d-1 (A3): at the marks the gate has just judged
        if units > 0 and not self.live_exposure + value <= conf["max_capital"]:
            return (f"lock 1 (the confirmation): with this order the money at work would be "
                    f"{self.live_exposure + value:,.2f}, above max_capital {conf['max_capital']:,.2f}")
        verdict = self._judged(c["symbol"], quote)       # a held symbol's quote: already judged as its mark
        if verdict != "agree":
            return f"lock 3 (the limits): the second price source says {verdict} for the quote {quote:g}"
        if units > 0:
            self.live_buys.append(value)
            self.live_exposure += value
        return None

    def _exposure_now(self) -> float:
        """Live mode (T22d-1, A3): what this ledger holds at the marks in hand now, plus the buys passed so far in
        this run (added one by one, in order); infinite when a holding has no usable mark (no buy can be judged)."""
        mine = [(u, self.live_marks.get(sym)) for sym, u in self.live_book.items() if u > UNITS_EPS]
        exposure = math.inf if any(not _finite(q) for _, q in mine) else math.fsum(u * float(q) for u, q in mine)
        for value in self.live_buys:
            exposure += value
        return exposure

    def _judged(self, symbol: str, price: float) -> str:
        """The second source's verdict on one quote in hand (its symbol, price and time), asked once per quote in a
        run (T22d-1, A1): the same quote is never asked about twice; a new quote is."""
        key = (symbol, float(price), self.quote_times.get(symbol))
        if key not in self.marks_judged:
            self.marks_judged[key] = self._second_verdict(symbol, price)
        return self.marks_judged[key]

    def _judge_marks(self, held: dict, marks: dict, after: str) -> None:
        """Live mode (T22d-1, A1): every symbol held (the baseline and this ledger's book) has its mark in hand judged
        by the second source with the rule an order's own quote gets; the first one it does not agree with, or
        cannot answer for, stops the run at stage "marks", naming the symbol."""
        for sym in sorted(s for s, u in held.items() if u > UNITS_EPS):
            price = marks[sym]
            verdict = self._judged(sym, price)
            if verdict == "agree":
                continue
            how = "does not agree with" if verdict == "disagree" else f"cannot answer for ({verdict})"
            raise _Stop("marks", f"live mode: the second price source {how} the mark {price:g} of {sym}, a symbol held, "
                                 "which prices the equity, the drawdown, the daily loss and the exposure; a mark is "
                                 f"never believed on one source, so {after}")

    def _lock_lost(self, after: str) -> str | None:
        """Live mode (T22d-1, A2): why the run lock is no longer this run's (its file unlinked or replaced since the
        run took it, so a second run may hold a lock of its own), or None."""
        lock = self.run_lock
        if lock is None or lock.follows():
            return None
        name = os.path.basename(lock.path or "") or "the lock file"
        return (f"the run lock ({name}) is no longer the file this run locked: it was removed or replaced, so another "
                f"run may hold a lock of its own; {after}")

    def _live_counters(self) -> None:
        """Live mode (T22d-1, A4): the state file must know at least what the order ledger holds of the orders sent
        on this New York day, and an equity peak at least the highest equity the journal recorded for this venue. An
        older state file put back would reopen the day's cap or lower the peak: the run stops; a person must look."""
        counted = self.state.sent_on(self.day)
        on_ledger = sum(1 for o in self.ledger.orders()
                        if o.get("state") not in LEDGER_NOT_SENT and _ny_day(o.get("created")) in (self.day, None))
        if counted < on_ledger:
            raise _Stop("state", f"the order step's state file counts {counted} order{'' if counted == 1 else 's'} sent on "
                                 f"{self.day} and the order ledger holds {on_ledger} sent that day: the state file knows "
                                 "less than the ledger (an older copy put back?), so the day's cap cannot be judged; "
                                 "a person must look; nothing is sent")
        peak, seen = self.state.peak(), self.journal_peak
        if seen is not None and (peak is None or peak < seen):
            held = "no equity" if peak is None else f"an equity peak of {peak:,.2f}"
            raise _Stop("state", f"the order step's state file holds {held} and the journal recorded an equity of "
                                 f"{seen:,.2f} for {self.name}: the state file knows less than the journal (an older copy "
                                 "put back?), so the drawdown cannot be judged; a person must look; nothing is sent")

    # -- T34b-2: the faults the summary carries, and (live mode) the working stop and the day stop
    def _judge_final(self, final) -> None:
        """After the final reconcile: F1, that reconcile's own result not clean by the order ledger's judgement, except
        what is set aside and said in `reconcile_pending` (information, never a fault): a position difference in a
        symbol where this ledger still holds an order open at the venue, or one of this run's batch in any state, or a
        fill this run journaled (J2: the venue's positions may lag its order status); the venue's positions not read
        (`positions_error`); an unresolved order that the ledger, read back, holds in an open state (G1: by its state,
        never by a reason's words). Anything else is F1: an unresolved order in any other state or that cannot be read
        back, an unsettled send, an anomaly, a difference in a symbol this run did not touch, or an unclean result that
        nothing here explains. The next run's first reconcile judges every symbol again, with nothing set aside. F3
        (live mode, the confirmation in force): the book against the ceiling, by `_judge_caps`; (i), (ii) and (iii)
        are each fill's own verdict, in its line (`_fill_verdict`)."""
        if not (isinstance(final, dict) and final.get("clean") is True):
            self._judge_unclean(final)
        if self.live is not None and self.locks is not None and self.locks.confirmation is not None:
            self._judge_caps(self.locks.confirmation)

    def _judge_unclean(self, final) -> None:
        if not isinstance(final, dict):
            self.final_unclean = True
            return
        unread = final.get("positions_error") is not None
        diffs = final.get("position_differences")
        diffs = diffs if isinstance(diffs, dict) else {}
        if diffs:                                       # J2 (addendum 5): a symbol this run touched is set aside too
            touched = {o["symbol"] for o in self.ledger.orders()
                       if o.get("state") in OPEN_AT_VENUE or (self.batch and o.get("batch") == self.batch)}
            working = touched | self.journaled_symbols
        else:
            working = set()
        aside = {sym for sym in diffs if sym in working}
        unresolved = final.get("unresolved")
        unresolved = unresolved if isinstance(unresolved, list) else ([] if not unresolved else [None])
        status_aside: dict[str, str] = {}               # G1 (addendum 4): judged by the order's state, never a reason
        stuck = False
        for item in unresolved:
            tag = item.get("tag") if isinstance(item, dict) else None
            order = self.ledger.order(tag) if type(tag) is str and tag else None
            if isinstance(order, dict) and order.get("state") in OPEN_AT_VENUE:
                status_aside[tag] = order.get("symbol")
            else:
                stuck = True
        other = (stuck or bool(final.get("anomalies")) or bool(set(diffs) - aside)
                 or bool(self.ledger.orders(("sent", "unknown"))) or not (aside or unread or status_aside))
        if other:
            self.final_unclean = True
        if aside or unread or status_aside:
            self.reconcile_pending = {"symbols": sorted({self._cap_symbol(sym) for sym in (*aside, *status_aside.values())}),
                                      "positions_unread": unread, "status_unread": len(status_aside)}

    @staticmethod
    def _limit_verdict(order, avg) -> str:
        """(iii), K1, on one average `avg` of the order (a broker's own figure, never a piece price worked out from two
        running averages): `breach` when it is beyond LIMIT_SAME x limit (times LIMIT_EDGE, so the edge itself is no
        breach) on the wrong side of the order's limit; `unjudged` when the order, its units, its limit or `avg` cannot
        be read as usable numbers; else `ok`. Every limit the order step sends is a whole number of cents, so an
        average the broker rounds to the cent is at its limit or a cent through it; an average that includes fees
        would trip this rule."""
        asked = _as_float(order.get("units")) if isinstance(order, dict) else None
        if asked is None or asked == 0:
            return "unjudged"
        avg, limit = _as_float(avg), _as_float(order.get("limit_price"))
        if avg is None or not avg > 0 or limit is None or not limit > 0:
            return "unjudged"
        return "breach" if ((avg - limit) if asked > 0 else (limit - avg)) > LIMIT_SAME * limit * LIMIT_EDGE else "ok"

    def _fill_verdict(self, f: dict, settling: bool = False) -> dict:
        """J1 (T34b-2 addendum 5), as T34b-3 (D, B3, addenda 3 and 4) has it: one fill's verdict, worked out BEFORE its
        journal line is written (a failure here leaves no line, and the next run judges the fill), whichever run or
        batch sent its order, practice and live alike, against its own order read back from the ledger. Returns
        {"verdict", "judged_avg", "judged_on", "covered_by"}.
        (iii) is made on the average that stood after THIS fill's own write, replayed from the ledger's durable
        records (`OrderLedger.average_writes`), so a fill journaled late gets the figure it would have had on time
        and every write that sets an average is judged once, by its own line: a priced row on the figure it set; the
        row of a `piece_unpriced` notice on that notice's figure; a row with units that sets no average (the venue
        gave none) is `unjudged` until a later write prices its units, and settles (`settling`, from
        `_settle_unjudged`) with no limit breach of its own, naming that write (`covered_by`), whose own line judges
        the figure; a row with no units and no price (a fee) has no limit test. `judged_avg` is the figure the limit
        test was made on (None when it made none). When the replay does not reproduce the stored figures, (iii) is
        made on the order's stored average as the ledger now holds it, and `judged_on` says "stored".
        `breach` also when (ii) the order's fills in the ledger now sum to more units than it asked for (beyond
        REL_TOL x max(1, |units|)); or, in live mode with the confirmation in force, (i) a purchase fill of this run's
        batch whose units times its own price, or times the replayed average after its write for a piece left
        unpriced (the stored average when the replay is not trusted; for a settling fill, the figure of the write
        that priced it), is above max_order_value (each fill on its own, never summed with the batch's other fills).
        A fill that settles in a later run is not given (i): it is not that run's batch. Else `unjudged` when any of
        the three could not be judged (the order, its units or limit, or a fill's units unusable; for (i) no price),
        else `ok`. What this cannot see: a breach of under one part in a million of the limit; and pieces that reach
        the broker between two reads show only their average, so one piece through its limit diluted by honest
        pieces of the same read is not seen."""
        out = {"verdict": "unjudged", "judged_avg": None, "judged_on": None, "covered_by": None}
        tag = f.get("tag")
        order = self.ledger.order(tag) if type(tag) is str and tag else None
        asked = _as_float(order.get("units")) if isinstance(order, dict) else None
        if asked is None or asked == 0:
            return out
        unjudged = breach = False
        filled = [_as_float(r.get("units")) for r in self.ledger.fills(tag)]
        if None in filled:
            unjudged = True
        elif math.fsum(filled) * (1.0 if asked > 0 else -1.0) > abs(asked) + REL_TOL * max(1.0, abs(asked)):
            breach = True                                        # (ii)
        replay = self.ledger.average_writes(tag)
        own = replay["fills"].get(f.get("id")) if isinstance(replay, dict) and replay.get("trusted") else None
        piece_price = None                                       # for (i): the average an unpriced piece is valued at
        if own is None:                                          # the replay cannot be trusted: the stored average
            limit_verdict = self._limit_verdict(order, order.get("avg_price"))
            out["judged_on"] = "stored"
            if limit_verdict != "unjudged":
                out["judged_avg"] = _as_float(order.get("avg_price"))
            piece_price = _as_float(order.get("avg_price"))
        elif own["sets"]:                                        # this write set the order's average: its own figure
            limit_verdict = self._limit_verdict(order, own["avg"])
            if limit_verdict != "unjudged":
                out["judged_avg"] = own["avg"]
            piece_price = own["avg"]
        elif own["waits"]:                                       # units this write did not price
            cover = own["covered_by"] if settling else None
            if cover is not None:
                kind, cid = cover
                out["covered_by"] = f"{kind}-{cid}"
                limit_verdict = "ok"                             # the covering write's own line judges the figure
                piece_price = (replay["fills"].get(cid) or {}).get("avg") if kind == "fill" else \
                    (replay["notices"].get(cid) or {}).get("avg")
            else:
                limit_verdict = "unjudged"
                piece_price = own["avg"]
        else:                                                    # no units and no price: no limit test of its own
            limit_verdict = "ok"
        breach = breach or limit_verdict == "breach"
        unjudged = unjudged or limit_verdict == "unjudged"
        conf = self.locks.confirmation if self.live is not None and self.locks is not None else None
        if conf is not None and self.batch and tag.startswith(self.batch + "-"):   # (i), live: this batch's fills
            units, price = _as_float(f.get("units")), _as_float(f.get("price"))
            if price is None:                                    # a piece left unpriced: the replayed average
                price = piece_price if piece_price is not None and piece_price > 0 else None
            if units is None or price is None:
                unjudged = True
            elif units > 0 and not units * price <= conf["max_order_value"]:
                breach = True                                    # (i), purchases only
        out["verdict"] = "breach" if breach else "unjudged" if unjudged else "ok"
        return out

    def _take_verdict(self, ref: str, line: dict) -> None:
        """A verdict this run wrote into a line: a breach is this run's `cap` with `ref`; an unjudged one waits to be
        settled (`pending_unjudged`, whose symbols the summary names in `cap_unjudged`), this run included."""
        if line.get("verdict") == "breach":
            self.cap_refs.add(ref)
        elif line.get("verdict") == "unjudged":
            self.pending_unjudged[ref] = line

    def _judge_caps(self, conf: dict) -> None:
        """F3, live mode, after the final reconcile: the money at work in the broker book (this ledger's holdings at
        the marks in hand, as `_exposure_now` measures it) above max_capital is information only (`over_ceiling`),
        never a fault: a market gain or a lowered ceiling takes it there as well; a holding without a usable mark is
        named in `cap_unjudged`. (i), (ii) and (iii) are each fill's own verdict (`_fill_verdict`), written into its
        fill line by the run that journals it."""
        at_work = []
        for sym, u in broker_book(self.ledger.fills()).items():
            if not u > UNITS_EPS:
                continue
            q = self.live_marks.get(sym)
            if not _finite(q):
                self.cap_unjudged.add(self._cap_symbol(sym))
            else:
                at_work.append(u * float(q))
        total = math.fsum(at_work)
        if not total <= conf["max_capital"]:                       # information only: the part that could be measured
            self.over_ceiling = {"at_work": total, "ceiling": float(conf["max_capital"])}

    @staticmethod
    def _cap_symbol(sym) -> str:
        return sym if _plain(_SYMBOL, sym) else "(a symbol that is not plain)"

    def _breach_refs(self) -> list[str]:
        """The refs this run's `cap` stands for, sorted: the breaches it journaled itself, and (B1) on a New York day
        that is an exchange session, the breaches journaled earlier that no summary of this venue lists in its
        `cap_refs`."""
        return sorted(self.cap_refs | (self.carried_refs if self.session_day else set()))

    def _faults(self) -> list[str]:
        """The summary's `faults` (D8), in order, each at most once: `reconcile` (F1); `attention:<kind>` for each
        distinct kind of a blocking attention item opened in this run, an id above the largest that existed when the
        run took the ledger, closed ones counted, notices never (F2); `cap` when `_breach_refs` is not empty, on
        practice and live runs alike (T34b-3 D: each fill's verdict is in its own line, and a breach no summary covers
        is carried). Raises what the ledger raises."""
        out = ["reconcile"] if self.final_unclean else []
        if self.attention_before is not None:
            kinds = set()
            for item in self.ledger.attention(open_only=False):
                kind = item.get("kind")
                if int(item["id"]) > self.attention_before and kind not in NOTICE_KINDS:
                    kinds.add(kind if type(kind) is str and _ATTENTION_KIND.fullmatch(kind) else "unknown")
            out += [f"attention:{kind}" for kind in sorted(kinds)]
        if self._breach_refs():
            out.append("cap")
        return out

    def _set_working_stop(self, limits) -> None:
        """S1, live mode, once, after the paper book is read and before any order: the working stop (D5) from the
        paper record's exact daily returns of the strategies followed, under the limits' max_daily_loss_pct (the
        wall); when the record cannot be built or read, the floor (or the wall, when lower), and the basis says so."""
        from valedia_trader.execution_and_risk import stake_ladder
        from valedia_trader.research_loop import decay
        self.wall = _as_float(limits.get("max_daily_loss_pct")) if isinstance(limits, dict) else None
        if self.wall is None:                            # the gate then refuses every order with the file's own error
            self.working_stop = {"pct": None, "n": 0, "basis": "the risk limits could not be loaded, so no working stop "
                                                               "is set; the risk gate refuses every order"}
            return
        try:
            record = decay.paper_record(self.journal.dir, self.day)
            stop = stake_ladder.working_stop_pct(stake_ladder.paper_returns(record, self.follow_names), self.wall)
            pct, basis, n = float(stop.pct), str(stop.basis), int(stop.n)
        except Exception:
            pct, n = min(self.wall, stake_ladder.STOP_FLOOR_PCT), 0
            basis = "the paper record could not be read: the floor" + (
                "; held at the owner's daily halt" if pct < stake_ladder.STOP_FLOOR_PCT else "")
        self.working_stop = {"pct": pct, "basis": basis, "n": n}

    def _look(self, loss: float) -> None:
        """S2, live mode, at every look at the account: the day's loss at or above the working stop puts the run in the
        day stop from this look on. Addendum 2 (C1): the trip is journaled at once, one `day_stop` line with the same
        three values, synced, so the stop lasts the day even when the run dies before its summary; a failure to write
        it is the journal's failure and propagates (nothing more is sent)."""
        stop = None if self.working_stop is None else self.working_stop["pct"]
        if self.day_stop is None and stop is not None and loss >= stop:
            self.day_stop = {"pct": float(loss), "limit": float(stop), "wall": bool(loss >= self.wall)}
            self._write({"event": "day_stop", **self.day_stop})
            self._sync_journal()

    def _carry_day_stop(self, entries: list[dict]) -> None:
        """S4, live mode, from the journal scanned: a day stop an earlier run recorded for this venue and New York day
        (stake_ladder.day_stopped; the first such summary, its three values copied where they can be read). Addendum 2
        (C1): equally an `order_step` `day_stop` event line of this venue and day, the line a tripping run writes at the
        look; the earliest of the two kinds, in file order, gives the values."""
        from valedia_trader.execution_and_risk import stake_ladder
        day = date.fromisoformat(self.day)

        def tripped(e) -> bool:
            return (isinstance(e, dict) and e.get("type") == "order_step" and e.get("event") == "day_stop"
                    and e.get("venue") == self.name and type(e.get("day")) is str and e["day"] == self.day)
        first = next((e for e in entries if tripped(e) or stake_ladder.day_stopped([e], self.name, day) is not None), None)
        if first is None:
            return
        if tripped(first):
            raw = first
        else:
            raw = first.get("day_stop") if isinstance(first.get("day_stop"), dict) else {}
        wall = raw.get("wall")
        self.day_stop = {"pct": _as_float(raw.get("pct")), "limit": _as_float(raw.get("limit")),
                         "wall": wall if type(wall) is bool else None, "carried": True}

    def _day_stop_reason(self) -> str:
        s = self.day_stop
        if s.get("carried"):
            return (f"an earlier run on {self.day} recorded the day stop: no order that adds to a position is sent for the "
                    "rest of the New York day; reducing orders still pass")
        halt = " and the owner's daily halt" if s["wall"] else ""
        return (f"the day's loss of {s['pct']:.2f}% reached the working stop of {s['limit']:.2f}%{halt}: no order that "
                f"adds to a position is sent for the rest of the New York day {self.day}; reducing orders still pass")

    def _second_verdict(self, symbol: str, quote: float) -> str:
        from valedia_trader.execution_and_risk import go_live
        try:
            now = datetime.fromtimestamp(self._now(), tz=timezone.utc)
            answer = self.second_opinion(quote, symbol, now=now, sources=go_live.SECOND_SOURCES, clock=self.clock)
            verdict = answer.get("verdict") if isinstance(answer, dict) else None
        except _Stop:
            return "no second quote (the clock gave no usable time)"
        except Exception as exc:
            return f"no second quote (the check failed: {type(exc).__name__})"
        return verdict if type(verdict) is str and verdict in SECOND_VERDICTS else "an answer that is not a verdict"

    def _ticket_expired(self, now: float, ticket: dict | None = None) -> bool:
        """Whether the ticket is past its expiry on this reading or was on any earlier reading of this run."""
        if now > ticket_expiry(self.ticket if ticket is None else ticket):
            self._ticket_expired_seen = True
        return self._ticket_expired_seen

    # -- tickets (propose and approve runs only)
    def _verdict(self, verdict: str, why: str | None = None) -> None:
        try:
            self.tickets.note_verdict(self.batch, verdict, why)
        except (sqlite3.Error, OSError):
            raise _Stop("ticket", TICKET_UNWRITABLE) from None

    def _refuse_ticket(self, why: str) -> None:
        self._verdict("refused", why)
        raise _Stop("ticket", f"{why}; nothing is sent")

    def _ticket_first(self, now: float) -> None:
        """Approve: the attempt recorded before anything is compared, then the ticket's own checks (no venue call).
        Both: the account, read now, as its keyed hash."""
        if self.mode == "approve":
            try:
                self.tickets.note_attempt(self.batch, self.day)
            except (sqlite3.Error, OSError):             # the same words whatever the number
                raise _Stop("ticket", TICKET_UNWRITABLE) from None
            self._check_ticket(now)
        try:
            account = self.venue.account_id
        except Exception as exc:
            if self.mode == "approve":
                self._verdict("refused", "the venue's account could not be read")
            raise _Stop("ticket", f"the venue's account could not be read: {_venue_error(exc)}; nothing is sent") from None
        if not _plain(_ACCOUNT, account):
            if self.mode == "approve":
                self._verdict("refused", "the venue's account is not a plain id")
            raise _Stop("ticket", "the venue's account is not a plain id; nothing is sent")
        if self.live is not None:                        # live: the account is the one the confirmation names
            why = self._live_account(account)
            if why is not None:
                if self.mode == "approve":
                    self._verdict("refused", why)
                raise _Stop("lock", f"{why}; nothing is sent")
        self.account = self.tickets.account_hash(account)
        if self.mode == "approve":
            if not hmac.compare_digest(self.account, str(self.ticket["account"])):
                self._refuse_ticket("that ticket was made for another account")
            self._verdict("accepted")

    def _check_ticket(self, now: float) -> None:
        book, number = self.tickets, self.approve_number
        if book.wrong_count(self.day, self.batch) >= WRONG_NUMBERS_MAX:
            self._refuse_ticket(f"approval is locked after {WRONG_NUMBERS_MAX} refused numbers today, until the next propose")
        if not _plain(_NUMBER, number):
            self._refuse_ticket("the number is not six digits")
        rows = book.find(number)
        today = [r for r in rows if r["day"] == self.day]
        if not rows:
            self._refuse_ticket("no ticket has that number")
        if not today:
            self._refuse_ticket("that ticket was made for another day")
        t = today[0]
        if t["venue"] != self.name:
            self._refuse_ticket("that ticket was made for another venue")
        try:
            env = self.venue.environment
        except Exception:
            env = None
        if t["environment"] != self.expected_env or type(env) is not str or env != t["environment"]:
            self._refuse_ticket("that ticket was made for another environment")
        fate = book.fate(t["day"], t["number"])
        if fate is not None:
            self._refuse_ticket({"used": "that ticket has been used", "expired": "that ticket has expired",
                                 "superseded": "that ticket was superseded by a later one"}.get(fate["fate"],
                                                                                                "that ticket is closed"))
        made = book.verdict(t["batch"])                 # T34b-2 E4: the run that made it was refused after it made it
        if made is not None and made["verdict"] == "refused":
            self._refuse_ticket(str(made["reason"] or "the run that made that ticket was refused"))
        mark = book.live_mark(t["day"], t["number"])     # the ticket's mode: live tickets carry their locks' files
        if self.live is None and mark is not None:
            self._refuse_ticket("that ticket was made in live mode")
        if self.live is not None:
            if mark is None or mark.get("mode") != "live":
                self._refuse_ticket("that ticket was not made in live mode")
            if mark.get("files") != self.locks.digest:
                self._refuse_ticket("the owner's files (the confirmation, the checklist or the limits) changed since that "
                                    "ticket was made")
            follow = book.follow_mark(t["day"], t["number"])     # T32: the broker book it was made to follow
            if not isinstance(follow, dict) or follow.get("digest") != self.follow.digest:
                self._refuse_ticket("the broker book's follow list (config/broker_book.json, or the strategies it names) "
                                    "changed since that ticket was made")
        if self._ticket_expired(now, t):
            try:
                book.mark_expired(t["day"], t["number"], self.batch)
            except (sqlite3.Error, OSError):
                raise _Stop("ticket", TICKET_UNWRITABLE) from None
            self._refuse_ticket("that ticket has expired")
        if now < t["created"]:
            self._refuse_ticket("that ticket was made after now on the clock")
        capital = self.paper_read.equity if self.capital is None else self.capital
        if _as_float(capital) is None or float(capital) != t["capital"]:
            self._refuse_ticket("that ticket was made for another capital")
        if bool(t["fractional"]) != self.fractional:
            self._refuse_ticket("that ticket was made " + ("with" if t["fractional"] else "without") + " fractional units")
        self.ticket = t
        self.ticket_lines = [{"line": int(ln["line"]), "fund": ln["symbol"], "side": ln["side"], "units": float(ln["units"]),
                              "quote": float(ln["quote"]), "limit": float(ln["limit_price"]), "value": float(ln["value"])}
                             for ln in book.lines(t["day"], t["number"])]

    def _make_ticket(self) -> None:
        """Propose: write the ticket of this run's lines (none: no ticket), superseding every earlier unused one of
        this venue and account, and journal it without its number."""
        if not self.ticket_lines:
            return
        life = ticket_life()                             # an ordinary check: above 0, at most an hour
        now = self._now()
        for _ in range(100):
            number = ticket_number()
            if not self.tickets.taken(self.day, number):
                break
        else:
            raise _Stop("ticket", "no free ticket number could be drawn")
        ticket = {"day": self.day, "number": number, "created": now, "expires": now + life, "venue": self.name,
                  "environment": self.expected_env, "account": self.account, "capital": self.capital_used,
                  "fractional": int(self.fractional), "equity": self.broker_equity, "cash": self.cash_read,
                  "settled_cash": self.settled_cash, "batch": self.batch}
        mark = None if self.live is None else {"mode": "live", "files": self.locks.digest}
        if self.live is None:
            self.tickets.create(ticket, self.ticket_lines, live=mark)
        else:                                            # T32: a live ticket is bound to the broker book it follows
            self.tickets.create(ticket, self.ticket_lines, live=mark,
                                follow={"digest": self.follow.digest, **self._follow_view()})
        self.ticket, self.ticket_number_made = ticket, number
        self._write({"event": "ticket", **self._ticket_view()})

    def _ticket_view(self) -> dict:
        t = self.ticket
        view = {"number_ends": t["number"][-2:], "orders": [dict(ln) for ln in self.ticket_lines], "equity": t["equity"],
                "cash": t["cash"], "settled_cash": t["settled_cash"], "created": _iso(t["created"]),
                "expires": _iso(t["expires"])}
        if self.follow_names is not None:                # T32: what the proposal follows
            view["follow"] = self._follow_view()
        return view

    def _line(self, c: dict) -> dict:
        return next(ln for ln in self.ticket_lines if ln["line"] == c["line"])

    def _same_size(self, units: float, line: dict) -> bool:
        """The same side and the same units as the ticket's line (fractional: within TICKET_UNITS_SAME, never more)."""
        t = line["units"]
        if ("buy" if units > 0 else "sell") != line["side"] or (t > 0) != (units > 0):
            return False
        if self.fractional:
            return abs(units - t) <= TICKET_UNITS_SAME and abs(units) <= abs(t)
        return units == t

    def _ticket_candidates(self, candidates: list[dict]) -> list[dict]:
        """Approve: the fresh candidates that match a line of the ticket, each carrying its line; every other line is
        dropped with its reason, every other candidate is not sent ("not on the ticket")."""
        fresh = {c["symbol"]: c for c in candidates}
        why_not = {x["symbol"]: x["reason"] for x in self.skipped + self.refused}
        out = []
        for ln in self.ticket_lines:
            c = fresh.pop(ln["fund"], None)
            stub = {"symbol": ln["fund"], "units": ln["units"], "line": ln["line"]}
            if c is None:
                why = why_not.get(ln["fund"])
                self._drop(stub, "the order is no longer wanted by the fresh targets" + (f" ({why})" if why else ""))
            elif not self._same_size(c["units"], ln):
                self._drop(stub, f"the wanted size changed: the ticket holds {ln['units']:+g}, the fresh targets want "
                                 f"{c['units']:+g}")
            else:
                out.append({**c, "line": ln["line"]})
        for c in fresh.values():
            self.skipped.append({"symbol": c["symbol"], "units": c["units"], "reason": "not on the ticket"})
        return out

    def _ticket_held(self) -> str | None:
        """Why the ticket no longer lets this run send because of its fate (not marked used by this run), or None.
        Its life is judged on the clock by the caller, on the reading the window is judged on."""
        fate = self.tickets.fate(self.ticket["day"], self.ticket["number"])
        if fate is None or fate["fate"] != "used" or fate["batch"] != self.batch:
            return "the ticket is not held by this run"
        return None

    def _ticket_check(self, c: dict, quote: float) -> str | None:
        """Approve, before intend: the ticket still this run's, the size still the line's, the quote judged within
        TICKET_QUOTE_MOVE of the ticket's."""
        line = self._line(c)
        why = self._ticket_held()
        if why is not None:
            return why
        if not self._same_size(c["units"], line):
            return "the wanted size changed"
        if not abs(quote - line["quote"]) <= TICKET_QUOTE_MOVE * line["quote"]:
            return (f"the fresh quote {quote:g} is more than {TICKET_QUOTE_MOVE:.1%} from the ticket's quote "
                    f"{line['quote']:g}")
        return None

    def _ticket_note(self, c: dict, outcome: str, tag: str | None = None, reason: str | None = None) -> None:
        """A line's final outcome, written to the ticket file (approve runs only)."""
        if self.mode != "approve" or "line" not in c:
            return
        tag = tag if tag is not None else c.get("tag_noted")
        self.tickets.note_outcome(self.ticket["day"], self.ticket["number"], c["line"], outcome, tag, reason, self.batch)
        self.ticket_out[c["line"]] = {"outcome": outcome, "tag": tag, "reason": reason}

    def _drop(self, c: dict, reason: str) -> None:
        item = {"symbol": c["symbol"], "units": c["units"], "reason": reason}
        self.dropped.append(item)
        self._write({"event": "dropped", **item})
        self._ticket_note(c, "not sent", reason=reason)

    def _settle_tickets(self) -> list[dict]:
        """At the end of every run that held the lock: make every used ticket's lines mirror the order ledger (first
        rows, and correcting rows where the ledger has changed since). A propose or approve run uses its own ticket
        file when it is open; otherwise a ticket file is opened only when it is there (none is ever made, no account
        is read). A ticket file that cannot be used or written is journaled, never a reason to stop. Returns the
        corrections written."""
        book, own = self.tickets, True
        self.disagreements = []
        if book is None:
            path = ticket_path_for(self.run_path) if self.run_path is not None else ":memory:"
            if str(path) == ":memory:" or not os.path.exists(path):
                return []
            try:
                book, own = TicketBook(path, self.clock, create=False), False
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                self._write({"event": "tickets_not_settled", "reason": _local_error(exc)})
                return []
        try:
            out = book.settle(self.ledger, self.batch)
            self.disagreements = list(getattr(book, "disagreements", []))
            return out
        except (OrderStepError, sqlite3.Error, OSError) as exc:
            self._write({"event": "tickets_not_settled", "reason": _local_error(exc)})
            return []
        finally:
            if not own:
                book.close()

    def _ticket_summary(self) -> dict:
        if self.mode == "propose":
            if self.ticket_number_made is not None:
                return self._ticket_view()
            return {"number_ends": None, "orders": [],
                    "reason": "nothing to propose" if self.ticket_lines == [] else "no ticket was written"}
        final, tags = {}, {}
        if self.ticket is not None and self.ticket_used:
            try:
                final = self.tickets.outcomes(self.ticket["day"], self.ticket["number"])
                tags = self.tickets.tags(self.ticket["day"], self.ticket["number"])
            except Exception:
                final, tags = {}, {}
        words = {"sent": "sent", "not sent": "dropped", "left over": "left over", "unknown": "unknown"}
        out = []
        for ln in self.ticket_lines:
            item = {"line": ln["line"], "fund": ln["fund"], "side": ln["side"], "units": ln["units"]}
            got = final.get(ln["line"]) or self.ticket_out.get(ln["line"])
            if got is not None:
                item.update(outcome=words.get(got["outcome"], got["outcome"]), **({"tag": got["tag"]} if got["tag"] else {}),
                            **({"reason": got["reason"]} if got["reason"] else {}))
            elif ln["line"] in tags:
                item.update(outcome="unknown", tag=tags[ln["line"]],
                            reason="handed to intend; what became of it is not known yet (a later run completes it)")
            else:
                item.update(outcome="left over", reason="the run ended before this order's intend" if self.ticket_used
                            else "the run stopped before sending; the ticket is not used")
            out.append(item)
        return {"number_ends": self.ticket["number"][-2:] if self.ticket else None, "orders": out}

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
        self._scan_verdicts(entries)                     # T34b-3: every file, every run (no cut-off)
        self.torn_lines = [{"file": name, "line": n} for name, n, _ in torn]
        if self.live is not None:                        # T22d-1 (A4): the highest equity journaled for this venue
            keys = {"summary": "broker_equity", "ticket": "equity"}
            seen = [_as_float(e.get(keys[e.get("event")])) for e in entries if e.get("type") == "order_step"
                    and e.get("venue") == self.name and type(e.get("event")) is str and e.get("event") in keys]
            seen = [v for v in seen if v is not None]
            self.journal_peak = max(seen) if seen else None
            self._carry_day_stop(entries)                # T34b-2 (S4): a day stop recorded earlier this New York day
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
        if self.live is not None:                        # live mode: production, at exactly Webull's production host
            self._check_live_environment(static_env, env)
            return
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

    def _check_live_environment(self, static_env, env) -> None:
        """Live mode: `environment` exactly "production" and `host` exactly LIVE_HOST, each a plain str (not a
        subclass) by a static lookup (a property or a class-level descriptor is refused) and by an ordinary one (also
        through __getattr__)."""
        if not all(type(e) is str and e == LIVE_ENVIRONMENT for e in (static_env, env)):
            raise _Stop("environment", f"live mode runs against Webull's production environment ({LIVE_ENVIRONMENT}, a "
                                       "plain str attribute) only; this venue's environment is not that")
        try:
            static = inspect.getattr_static(self.venue, "host", _ABSENT)
        except Exception:
            static = None
        try:
            dynamic = getattr(self.venue, "host", _ABSENT)
        except Exception:
            dynamic = None
        if not all(type(h) is str and h == LIVE_HOST for h in (static, dynamic)):
            raise _Stop("environment", f"live mode needs the venue's host to be exactly {LIVE_HOST} (a plain str attribute)")

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
        self.session_day = True                          # T34b-3 (B1): an exchange session; it may carry a breach
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
        self._journal_notices()
        self._settle_unjudged()
        return result

    def _journal_fills(self) -> None:
        """Each fill of the ledger not yet applied: journaled once (by fill_id), with `judged_avg` (and `judged_on`
        when the stored average stood in for the replay) and its verdict as the line's last field, judged before the
        line is written (`_fill_verdict`) and synced before the fill counts as applied (T34b-3 D, addenda 3 and 4)."""
        if self._seen_fill_ids is None:
            try:
                entries = self.journal.read()
            except Exception as exc:
                raise _Stop("reconcile", f"the journal could not be read to find the fills already written: {_describe(exc)}") from None
            self._seen_fill_ids = {e.get("fill_id") for e in entries
                                   if e.get("type") == "order_step" and e.get("event") == "fill" and e.get("venue") == self.name}
            self._scan_verdicts(entries)
        for f in self.ledger.unapplied_fills():
            if f["id"] not in self._seen_fill_ids:
                judged = self._fill_verdict(f)           # T34b-3 (D): first; a failure leaves no line to lose it
                entry = {"event": "fill", "fill_id": f["id"], "tag": f["tag"] if _plain(_TAG, f["tag"]) else None,
                         "symbol": f["symbol"] if _plain(_SYMBOL, f["symbol"]) else None,
                         "units": _number_or_none(f["units"]), "price": _number_or_none(f["price"]),
                         "price_covers_units": _number_or_none(f["price_covers_units"]),
                         "fee": _number_or_none(f["fee"]), "judged_avg": _number_or_none(judged["judged_avg"])}
                if judged["judged_on"] is not None:
                    entry["judged_on"] = judged["judged_on"]
                entry["verdict"] = judged["verdict"]
                self._write(entry)
                self._sync_journal()                     # on disk before the fill counts as applied
                self._seen_fill_ids.add(f["id"])
                self.fills_journaled += 1
                self.journaled_symbols.add(f["symbol"])
                self._take_verdict(f"fill-{f['id']}", entry)
            self.ledger.mark_applied([f["id"]])

    def _journal_notices(self) -> None:
        """T34b-3 (B2, R5, addenda 3 and 4): every ledger notice of the four reconcile kinds (orders.RECONCILE_NOTICE_
        KINDS) whose id the journal does not hold yet, open, closed or superseded, journaled once in id order: one
        `notice` line (its id, kind, tag, symbol, old and new figures). An `avg_restated` line also carries the limit
        test on its OWN `new` figure (with the order's limit, side and units), never on the order's average at journal
        time, with `judged_avg`, judged before the line is written; so does a `piece_unpriced` notice that came with
        no row of its own. A `piece_unpriced` notice with its own row carries no verdict: that row's fill line judges
        the figure it set. `old` is never judged (the line of the write that set it did). `late_amount` and
        `fee_fell` carry no verdict: a late amount's units come as a fill row, judged in its own line, and a fee is
        not an average."""
        for n in self.ledger.reconcile_notices():
            nid, kind, tag = n.get("id"), n.get("kind"), n.get("tag")
            if type(nid) is not int or nid in self._seen_notice_ids or kind not in RECONCILE_NOTICE_KINDS:
                continue
            order = self.ledger.order(tag) if type(tag) is str and tag else None
            symbol = order.get("symbol") if isinstance(order, dict) and _plain(_SYMBOL, order.get("symbol")) else None
            entry = {"event": "notice", "notice_id": nid, "kind": kind, "tag": tag if _plain(_TAG, tag) else None,
                     "symbol": symbol, "old": _number_or_none(n.get("old")), "new": _number_or_none(n.get("new"))}
            if kind == "avg_restated" or (kind == "piece_unpriced" and n.get("row") is None):
                verdict = self._limit_verdict(order, n.get("new"))
                entry["judged_avg"] = entry["new"] if verdict != "unjudged" else None
                entry["verdict"] = verdict
            self._write(entry)
            self._sync_journal()
            self._seen_notice_ids.add(nid)
            self.notice_kinds.add(kind)
            if "verdict" in entry:
                self._take_verdict(f"notice-{nid}", entry)

    def _settle_unjudged(self) -> None:
        """T34b-3 (R3, addendum 4): each `unjudged` verdict not settled yet (from the journal's scan, and this run's
        own lines) judged again on the ledger's durable records as they now are; one `verdict` line ({"ref",
        "verdict", "covered_by", "judged_avg"}) only when it settles to `ok` or `breach` (a breach is then this run's
        `cap`). A fill whose units had no price settles once a later write prices them: `covered_by` names that
        write, whose own line judges the figure, and the settling line has no limit breach of its own (a fill that
        settles in a later run is not given the value test (i), since it is not that run's batch). A notice is judged
        again on its own `new`. While it stays unjudged no line is written and its symbol is named in this run's
        `cap_unjudged`; nothing but settling ends that."""
        for ref, line in sorted(self.pending_unjudged.items()):
            tag = line.get("tag")
            order = self.ledger.order(tag) if type(tag) is str and tag else None
            if line.get("event") == "fill":
                row = next((r for r in self.ledger.fills(tag) if r.get("id") == line.get("fill_id")), None) if order else None
                judged = {"verdict": "unjudged"} if row is None else self._fill_verdict(row, settling=True)
            else:
                verdict = self._limit_verdict(order, line.get("new"))
                judged = {"verdict": verdict, "judged_avg": _as_float(line.get("new")) if verdict != "unjudged" else None}
            if judged["verdict"] == "unjudged":
                continue
            entry = {"event": "verdict", "ref": ref, "covered_by": judged.get("covered_by"),
                     "judged_avg": _number_or_none(judged.get("judged_avg"))}
            if judged.get("judged_on") is not None:
                entry["judged_on"] = judged["judged_on"]
            entry["verdict"] = judged["verdict"]
            self._write(entry)
            self._sync_journal()
            del self.pending_unjudged[ref]
            self._take_verdict(ref, {**entry, "symbol": line.get("symbol")})

    def _scan_verdicts(self, entries: list[dict]) -> None:
        """T34b-3 (D, B1, R3, R4), from every journal line there is, this venue's only: the notice ids already
        journaled; the breaches journaled (a fill or notice line whose verdict is `breach`, or a `verdict` line) that
        no summary lists in its `cap_refs` (carried, on an exchange session only: `_breach_refs`); and the `unjudged`
        verdicts no `verdict` line has settled. A fill line with no `verdict` field (before T34b-3) is neither."""
        breaches, covered, unjudged, settled = set(), set(), {}, set()
        for e in entries:
            if not (isinstance(e, dict) and e.get("type") == "order_step" and e.get("venue") == self.name):
                continue
            event, verdict = e.get("event"), e.get("verdict")
            if event == "summary":
                refs = e.get("cap_refs")
                covered |= {r for r in refs if type(r) is str} if isinstance(refs, list) else set()
                continue
            if event == "verdict":
                ref = e.get("ref")
                if type(ref) is str:
                    settled.add(ref)
                    if verdict == "breach":
                        breaches.add(ref)
                continue
            if event == "notice" and type(e.get("notice_id")) is int:
                self._seen_notice_ids.add(e["notice_id"])
            if event not in ("fill", "notice") or "verdict" not in e:
                continue
            key = e.get("fill_id") if event == "fill" else e.get("notice_id")
            if type(key) is not int:
                continue
            ref = f"{event}-{key}"
            if verdict == "breach":
                breaches.add(ref)
            elif verdict == "unjudged":
                unjudged[ref] = e
        self.carried_refs = breaches - covered
        self.pending_unjudged = {ref: e for ref, e in unjudged.items() if ref not in settled}

    def _reconcile_and_cancel(self, baseline: dict) -> None:
        if self.attention_before is None:                # T34b-2 (F2): read once, before anything settles or reconciles
            self.attention_before = max((int(i["id"]) for i in self.ledger.attention(open_only=False)), default=0)
        self.state.settle_sends(self.ledger)
        self.first_result = self._reconcile(baseline, "first")
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
            unread = self._venue_unread(self.first_result, second, still)   # T34b-3 (G): a skipped day, or a fault
            if unread is not None:
                raise _Stop("venue_read", unread)
            raise _Stop("reconcile", "; ".join(self._ledger_words(still, second))[:2000])

    def _venue_unread(self, first, second, still: list[dict]) -> str | None:
        """T34b-3 (G, addenda 1 and 2): why the run stops only because the venue could not be read, or None (the
        stop stays `reconcile`). All must hold: (a) the positions read raised in the second reconcile; (b) every
        unresolved entry of either reconcile is a status read that raised (`status_unread`) of an order the ledger
        knows, and for the SECOND reconcile's entries one it holds as accepted or partly_filled (a first-read entry
        is not judged by the order's state now: the second read may have answered it with a normal fill, addendum 3
        M2); the first reconcile's positions error, if any, is the same raise (R1); R2: every order still open after
        the cancels is one whose status read raised in the second reconcile; (c) no
        order is sent or unknown; (d) no anomaly and no position difference in either reconcile, and no blocking
        attention item open (whenever it was opened) or opened in this run; (e) no local failure (a fill not yet
        applied, an entry that could not be written: its own kind). Anything that cannot be read is None."""
        try:
            if not (isinstance(first, dict) and isinstance(second, dict)):
                return None
            if second.get("positions_error_kind") != "positions_unread":                          # (a)
                return None
            kind = first.get("positions_error_kind")
            if not (kind == "positions_unread" or (kind is None and first.get("positions_error") is None)):   # R1
                return None
            unread: set[str] = set()
            for res in (first, second):
                if res.get("anomalies") or res.get("position_differences"):                      # (d)
                    return None
                items = res.get("unresolved") or []
                if not isinstance(items, list):
                    return None
                for item in items:                                                                # (b), R1
                    tag = item.get("tag") if isinstance(item, dict) else None
                    order = self.ledger.order(tag) if type(tag) is str and tag else None
                    if item.get("kind") != "status_unread" or not isinstance(order, dict):
                        return None
                    if res is second and order.get("state") not in OPEN_AT_VENUE:                # M2: now, second only
                        return None
                    if res is second:
                        unread.add(tag)
            if any(o.get("tag") not in unread for o in still):                                    # R2
                return None
            if self.ledger.orders(("sent", "unknown")) or self.ledger.unapplied_fills():           # (c), (e)
                return None
            for item in self.ledger.attention(open_only=False):                                  # (d)
                if item.get("kind") in NOTICE_KINDS:
                    continue
                if item.get("closed_time") is None or int(item["id"]) > (self.attention_before or 0):
                    return None
        except Exception:
            return None
        tags = sorted(t for t in unread if _plain(_TAG, t))
        reads = "its positions read" + (f" and the status read of {len(tags)} order{'' if len(tags) == 1 else 's'} "
                                        f"({', '.join(tags)})" if tags else "")
        return (f"the venue could not be read: {reads} raised in the reconcile after the cancels, and nothing else "
                "stands in the way; nothing is sent and the next run reads again")

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
        items = [i for i in self.ledger.attention() if i.get("kind") not in NOTICE_KINDS]   # N2: notices block nothing
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
        if self.live is not None and self.settled_cash is None:   # T22d-1 (A6): never spends on the cash figure
            raise _Stop("prices", "live mode needs the venue's settled cash and the account read gave none; it never "
                                  "spends on the cash figure; nothing is sent")
        if unpriced:
            raise _Stop("prices", f"the broker equity cannot be worked out: no usable quote for {', '.join(sorted(unpriced))}; "
                                  "nothing is sent")
        if self.live is not None:                        # T22d-1 (A1): every held mark judged, before any equity, ticket
            self._judge_marks(held, prices, "nothing is sent")   # ... use or order
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
            figures = {"equity": now_equity,
                       "daily_loss_pct": max(0.0, (previous - now_equity) / previous * 100.0) if previous else 0.0,
                       "drawdown_pct": max(0.0, (peak - now_equity) / peak * 100.0) if peak else 0.0,
                       "open_positions": opened}
            if self.live is not None:                    # T34b-2 (S2): every look may trip the day stop
                self._look(figures["daily_loss_pct"])
            return figures
        try:
            limits = risk_gate.load_limits(self.limits_file)
        except risk_gate.RiskConfigError:
            limits = None                                # check_order then refuses with the file's own error
        if self.live is not None:                        # T34b-2 (S1): once, before any order
            self._set_working_stop(limits)
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
        if self.mode == "approve":                       # the sending stage: the ticket is used before the first intend
            try:
                self.tickets.mark_used(self.ticket["day"], self.ticket["number"], self.batch)
            except OrderStepError as exc:
                raise _Stop("ticket", f"{exc}; nothing is sent") from None
            self.ticket_used = True
            self._write({"event": "ticket_used", "number_ends": self.ticket["number"][-2:]})
            candidates = self._ticket_candidates(candidates)
        if self.live is not None:                        # live: the money this ledger has at work, at the quotes in hand
            self.live_book, self.live_marks, self.live_buys = book, in_hand, []   # worked out again at each order
            self.live_exposure = self._exposure_now()
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
                if self.live is not None:                # T22d-1 (A1): a mark the refresh replaced, judged before use
                    self._judge_marks(held, in_hand, "nothing more is sent")
                account = account_now()                  # ... the account priced at them
                if unpriced and not reduces:
                    self._leave(c, f"the account cannot be priced: no fresh quote for {', '.join(unpriced)}")
                    break
                if self.live is not None and self.day_stop is not None and not reduces:   # T34b-2 (S3)
                    self._refuse(c, "day stop", self._day_stop_reason())
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
            if self.mode == "approve":                   # the ticket: live, the same size, the quote within its band
                why = self._ticket_check(c, quote)
                if why is not None:
                    self._drop(c, why)
                    continue
            if self.live is not None:                    # live: the caps and the second price, at propose and approve
                why = self._live_order(c, units, limit, quote)
                if why is not None:
                    self._drop(c, why)
                    continue
            tag = f"{self.batch}-{sym}"
            watch = _SubmitWatch(self.venue)
            if self.mode == "approve":                   # the line's tag, on file before its intend
                self.tickets.note_tag(self.ticket["day"], self.ticket["number"], c["line"], tag, self.batch)
                c["tag_noted"] = tag
            now = self._now()
            if now > self.window_end:                    # 5. the window, on the clock, immediately before intend
                self._leave(c, "the session window closed during the run")
                continue
            if self.mode == "approve" and self._ticket_expired(now):         # ... the ticket's life on that reading
                self._drop(c, "the ticket expired before the order was intended")
                continue
            if self.mode == "propose":                   # a line of the ticket instead of intend: nothing is intended
                self.ticket_lines.append({"line": len(self.ticket_lines), "fund": sym, "side": order["side"], "units": units,
                                          "quote": quote, "limit": limit, "value": abs(units) * quote})
                sent_today += 1                          # counted against the caps as if it were sent
                spent += cost
                if units > 0 and owned_here <= UNITS_EPS:
                    opened += 1
                continue
            try:
                self.ledger.intend(tag, sym, units, quote, limit_price=limit, batch=self.batch, parts=c["parts"])
                self.state.note_sending(tag, self.day)
                self._check_environment()                # 6. the environment and host again ...
                late = self._last_look(sym, reduces)     # ... the quote's age, the halt and latch files ...
                if late is None and self.mode == "approve":
                    late = self._ticket_held()           # ... the ticket still this run's ...
                lost = None
                if late is None and self.live is not None:   # ... T22d-1 (A2): the run lock still the file it took ...
                    late = lost = self._lock_lost("nothing more is sent")
                if late is None:                         # ... then one reading: the window (and the ticket's life)
                    now = self._now()                    # ... nothing between it and submit_once
                    if now > self.window_end:
                        late = "the session window closed during the run"
                    elif self.mode == "approve" and self._ticket_expired(now):
                        late = "the ticket expired before the order was sent"
                if late is not None:
                    self.state.note_outcome(tag, "not sent")             # it stays intended, never sent
                    self._leave(c, late)
                    if lost is not None:                 # the lock gone: the run stops here
                        raise _Stop("lock", lost)
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
            self._ticket_note(c, "sent", tag=tag)

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
        self._ticket_note(c, "not sent", reason=reason)

    def _void_ticket(self, why: str) -> str:
        """E4: a propose run that made its ticket and then ended stopped: the ticket's batch is refused in the ticket
        book (an attempt and a refused verdict, the book's way of refusing a batch, with the reason), which every
        approval of that ticket then reads; no number is returned. The reason, with any failure to record it."""
        self.ticket_number_made = None
        try:
            self.tickets.note_attempt(self.batch, self.day)
            self.tickets.note_verdict(self.batch, "refused", f"the run that made that ticket ended stopped: {why}"[:1000])
        except Exception as exc:
            return f"{why}; its ticket could not be refused ({_local_error(exc)}), and its number is not given"
        return why

    def _refuse(self, c: dict, by: str, reason: str, **extra) -> None:
        item = {"symbol": c["symbol"], "units": c["units"], "by": by, "reason": str(reason)[:MAX_TEXT * 2]}
        self.refused.append(item)
        self._write({"event": "refused", **item, **extra})
        self._ticket_note(c, "not sent", reason=f"refused by the {by}: {item['reason']}")

    # -- 11
    def _finish(self, status: str, stage: str | None, reason: str | None) -> dict:
        faults: list[str] = []
        if self._locked:                                 # T34b-2 (D8): worked out first; a failure is the run's own
            try:
                faults = [] if self.faults_failed else self._faults()
            except Exception as exc:
                self.faults_failed = True                # worked out once: the stopped summary does not try again
                if stage != "unexpected":                # addendum 1 (B2): how the run had ended is kept, in one line
                    if status == "stopped":
                        before = f"stopped at {stage}" + ("" if reason is None else f": {reason}")
                    else:
                        before = str(status) + ("" if reason is None else f": {reason}")
                    before = " ".join(before.splitlines())
                    why = f"the run failed unexpectedly: {_venue_error(exc)}; the run had ended {before}"
                    if self.ticket_number_made is not None:   # addendum 3 (E4): the ticket it made is never usable
                        why = self._void_ticket(why)
                    return self._finish("stopped", "unexpected", why)
                reason = (f"{reason or 'the run failed unexpectedly'}; working out the faults also failed: "   # E4
                          f"{_venue_error(exc)}")
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
        if self.follow_names is not None:                # T32: which strategies the broker book followed
            summary["follow"] = self._follow_view()
        corrections = []
        if self._locked:                                 # every used ticket mirrors the order ledger as it now is
            try:
                corrections = self._settle_tickets()
            except Exception as exc:                     # a later run completes them
                try:
                    self._write({"event": "tickets_not_settled", "reason": _local_error(exc)})
                except Exception:
                    pass
        if self.mode is not None:
            summary.update(mode=self.mode, dropped=self.dropped, ticket=self._ticket_summary())
        if self.live is not None:                        # live mode: said so, with each lock as judged
            summary["live"] = True
            summary["locks"] = None if self.locks is None else [dict(lock) for lock in self.locks.locks]
        if corrections:                                  # named in the summary of the run that wrote them
            summary["ticket_corrections"] = corrections
        if self.disagreements:                           # a sent line the order ledger no longer knows as sent: kept
            summary["ticket_disagreements"] = [{**d, "reason": "the record says sent and the order ledger does not; the "
                                                "record is left as it is and a person must look"} for d in self.disagreements]
        if faults:                                       # T34b-2: only when there is one, practice and live alike
            summary["faults"] = faults
        if "cap" in faults:                              # T34b-3 (D, B4): every ref the cap stands for, sorted
            summary["cap_refs"] = self._breach_refs()
        if self.notice_kinds:                            # T34b-3: the kinds of the notices journaled, never a fault
            summary["notices"] = sorted(self.notice_kinds)
        if self.reconcile_pending is not None:           # addendum 3 (E2): what the final reconcile set aside
            summary["reconcile_pending"] = {"symbols": list(self.reconcile_pending["symbols"]),
                                            "positions_unread": bool(self.reconcile_pending["positions_unread"]),
                                            "status_unread": int(self.reconcile_pending["status_unread"])}
        unjudged = self.cap_unjudged | {self._cap_symbol(e.get("symbol")) for e in self.pending_unjudged.values()}
        if unjudged:                                     # addendum 5 (J1): practice and live alike; R3: until settled
            summary["cap_unjudged"] = sorted(unjudged)
        if self.live is not None:                        # T34b-2: live mode only
            if self.locks is not None and self.locks.confirmation is not None:
                summary["ceiling"] = float(self.locks.confirmation["max_capital"])
            if self.over_ceiling is not None:            # addendum 1: information, never a fault
                summary["over_ceiling"] = dict(self.over_ceiling)
            if self.working_stop is not None:
                summary["working_stop"] = dict(self.working_stop)
            if self.day_stop is not None:
                summary["day_stop"] = dict(self.day_stop)
        try:
            if not self._locked:                         # stopped before the lock: returned (and printed) only
                return summary
            if status == "stopped":
                self._write({"event": "stopped", "stage": stage, "reason": reason})
            self._write({"event": "summary", **summary})
        except Exception as exc:                         # the summary is still returned
            summary["journal_error"] = _describe(exc)
        if self.ticket_number_made is not None:          # returned beside the summary, never journaled
            summary["ticket_number"] = self.ticket_number_made
        return summary
