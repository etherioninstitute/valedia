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
                 that symbol (a quote older than PRIMARY_QUOTE_MAX_AGE_SECONDS values a holding but never prices an
                 order: step 10 asks for it again). The venue's cash, and the broker equity (cash
                 plus holdings at the quotes), must be finite numbers from 0 to MAX_CASH; the venue's settled cash,
                 when it gives one (the key present and not None), a finite number from 0 to MAX_CASH and at most
                 the cash plus SETTLED_ABOVE_CASH (a cent); and every held symbol must
                 have a usable quote; otherwise the run stops before any order and records no equity or peak. A
                 position above MAX_UNITS in size is unusable too (the baseline is not recorded; a broker book that
                 large stops the run).
 7. differences  target minus broker book, rounded toward zero (whole shares, or FRACTIONAL_DECIMALS decimals with
                 `fractional`); a sale never exceeds the broker book, nor (CF-C) its settled-quantity cap: the
                 smaller of the book's settled units of the fund (book_settled_units: the ledger's OWN lots whose
                 settlement day, T+1 from the New York day the lot's DAY order was sent (the first day after the
                 trade date that is both an exchange session and a settlement day: not a US settlement holiday),
                 has come; a lot whose trade date cannot be established is unsettled; the owner's own units at
                 the account are never in it) and the account's sellable quantity (the venue's `sellable`, read
                 once per sale; Webull: the holding less open sells and sales in flight). A sale over the cap is cut
                 to it, toward zero to the run's step (a fractional run's split never sells beyond it, its zero
                 rule included); a cap of 0, or one either input of which cannot be had (fail closed: 0), sends
                 nothing for that fund and the run skips it with the reason. The summary's `sale_caps` gives, per
                 sale, the cap, its two inputs (`book_settled`, `account_sellable`), the units wanted and the units
                 kept, and `why` when an input failed; `sales_left_unsettled` the units each cap held back. A run
                 with no sale has neither key. The account holding less than the book counts as its own still stops
                 the run at the reconcile (step 4), before any cap is worked out. Less than the paper trader's
                 smallest trade is dropped. The strategies' parts of each order sum to it exactly (order_parts).
 8. risk gate    risk_gate.check_order against the broker account as it is at that moment, priced at the quotes in
                 hand (equity = the venue's cash as read plus every holding, the baseline and the broker book with
                 this run's fills so far, at its latest usable quote, so a quote asked for again in step 10 prices
                 the account too; before each call every holding whose quote in hand is older than
                 QUOTE_MAX_AGE_SECONDS is asked for again, in the same single ask as the order's own quote, but not
                 within QUOTE_ASK_GAP_SECONDS of its last ask in the run (the order's own quote is always asked when
                 older than PRIMARY_QUOTE_MAX_AGE_SECONDS), so a run makes at most 1 + 2 requests per order
                 considered; when a holding still has no usable fresh quote the account cannot be priced: an order that adds risk is left over with that
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
                  a. the quote's age on the clock: older than PRIMARY_QUOTE_MAX_AGE_SECONDS (the quote an order is
                     priced from; a holding's limit stays QUOTE_MAX_AGE_SECONDS), the quote function is asked once
                     more for that symbol (with the holdings of step 8, in one ask), and an answer that is unusable
                     or itself older than PRIMARY_QUOTE_MAX_AGE_SECONDS leaves the order over;
                  b. the risk gate, at that quote (the limit price, order value, cash check and position value all
                     come from it);
                  c. the environment and host read;
                  d. the quote's age again (PRIMARY_QUOTE_MAX_AGE_SECONDS): too old, back to (a) once; too old the
                     second time, the order is left over. What is sent always uses the quote of the last round's (b), the one the gate judged;
                  e. the window check, then ledger.intend;
                  f. the environment and host read; a last look: the quote's age once more, the halt file and (for an
                     order that adds risk) the latch file; the window check again, then ledger.submit_once.
                 Nothing that can take time sits between a window check and the call it guards; an order failing the
                 last look or the second window check stays `intended`, never sent, and is reported left over with
                 the reason. A day limit order in the regular session. Each `order` line of the journal carries
                 `quote_ts`, the time of the quote it was priced from (ISO 8601, UTC), and `quote_age_s`, that quote's
                 age in seconds on the clock reading of (d) that judged it. A refusal is journaled and the run goes on;
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
            A ticket's stored limit (for a remainder line, its collar price) and quote are INFORMATIONAL (T51 B6): the
            approval works out a fresh limit or collar price from the quote it judges, as an ordinary run does; the
            ticket fixes the fund, the side, the leg and the size, and the quote the fresh one must stay within
            TICKET_QUOTE_MOVE of. (The ticket file is a database with no header of its own: this note and
            docs/WEBULL.md carry the wording.)
Tickets live in their own file beside the state file (ticket_path_for, TicketBook): append-only tables, natural keys,
no update, no delete, no replacement; the state file's layout is unchanged.

Fractions on live (T51 build B; docs/WEBULL.md, "Fractions on live"): with `fractional` on in live mode the run reads, once
at its start and read-only, the three things its remainder legs need: the shared book, the owner's consent in the
confirmation the locks judged (`fractional_live_consent`, JSON true only), and the practice proof (`practice_proof`: a
clean MARKET remainder purchase and sale in the practice ledger and journal). Missing any, the opening is shut and the run
is exactly the live run without `fractional` (no split, no zero rule, no five-decimal units; only the words are added to
the summary); all present, the whole legs follow the split as on practice and a remainder leg is a MARKET order by quantity handed to the venue with `fractional_live=True`, and until one live
remainder leg has filled and reconciled cleanly in the live record at most ONE goes per run (reason `first_contact`).
The adapter takes a production order with no limit price only for such a leg and judges it again at its place_order call.
A MARKET leg whose quote has passed MARKET_QUOTE_MAX_AGE_SECONDS at the moment its collar is about to judge it is asked
for once more (`_ask_again`), judged again from the top on a newer usable quote, once.

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

The shared run (`shared`, T51c S4c, and live since S4d; docs/WEBULL.md "The shared run"): the account is also the owner's,
and the fund book is judged on its OWN figures (`book_figures`, from the ledger's fills and the owner's entries). Before
anything is read from the account the state file latches (an owner's entry refuses a run that is not shared: stage
`state`), its entries are compared with the journal (`state`), every tag with the ledger (`reconcile`) and the book
must have its opening entry (`targets`). The reconcile is one-sided: the account holding LESS than the book stops the
run at stage `shortfall` (a skipped session for the ladder); the baseline is never sold, priced or counted. The prices
come first and the book's value at them is the capital (`--capital` is refused); a held fund far from its last
`book_nav` mark stops at `marks`; the position limit, the count of positions, the day's loss and the drawdown are the
book's (the loss and the drawdown on the value per unit, from `book_nav`; the old `equity` table is neither read nor
written); purchases are bounded by the book's usable cash too; a ticket binds the book's value.
In live mode (S4d: the command line takes `--live` only with `--shared`, and never a capital) the same run holds, with
lock 1's `max_capital` binding the book's value at its UPPER bound (stage `lock`, before any order), the day stop
judged on the loss of the value per unit, a live ticket marked shared as well, and the state file's counters read from
the book's own `book_nav` rows and the journal's value per unit (Decimal), never the `equity` table.

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
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal, DecimalException, InvalidOperation, localcontext
from pathlib import Path
from typing import Callable

import pandas as pd

from valedia_trader.execution_and_risk.orders import (FINISHED, NOTICE_KINDS, RECONCILE_NOTICE_KINDS, REL_TOL, STATES,
                                                      OrderError, OrderLedger, OrdersBlocked, allocate, net_orders)
from valedia_trader.research_loop import risk_gate
from valedia_trader.venues.webull import EVENTS_CLASS, NotTransmitted, OrderRejected

__all__ = ["TEST_ENVIRONMENT", "NOT_SENT", "PaperTargets", "OrderStepState", "OrderStepError", "run_order_step",
           "paper_targets", "open_ledger", "ledger_path_for", "state_path_for", "broker_book", "targets", "strategy_holdings",
           "round_units", "limit_price", "market_window", "max_orders_per_day", "minute_quotes", "wall_clock",
           "TicketBook", "ticket_path_for", "ticket_number", "TICKET_LIFE_SECONDS", "TICKET_QUOTE_MOVE", "Follow",
           "judge_follow", "paper_strategies", "BROKER_BOOK_FILE", "split_difference", "Split", "SplitLeg",
           "FRACTIONAL_MIN_VALUE"]

TEST_ENVIRONMENT = "sandbox"                      # the only environment a run accepts (WebullVenue.environment)
LIVE_ENVIRONMENT = "production"                   # live mode only (--live): Webull's production environment ...
LIVE_HOST = "api.webull.com"                      # ... at exactly this host
SECOND_VERDICTS = ("agree", "disagree", "no second quote")
NOT_SENT = (NotTransmitted, OrderRejected)        # the adapter's answers that mean no order exists
NEW_YORK = "America/New_York"
SESSION_AFTER_OPEN = pd.Timedelta(minutes=5)      # 09:35 on a 09:30 open
SESSION_BEFORE_CLOSE = pd.Timedelta(minutes=15)   # 15:45 on a 16:00 close, 12:45 on a 13:00 half-day
QUOTE_MAX_AGE_SECONDS = 20 * 60                   # a quote read at all (holdings valued, owner marks) at most this old
PRIMARY_QUOTE_MAX_AGE_SECONDS = 300               # CF-Q2: the quote an order is priced from at most this old (step 10)
SETTLEMENT_SEARCH_DAYS = 14                       # CF-C: a lot's settlement day (T+1) looked for this many days on, at most
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
MARKET_QUOTE_MAX_AGE_SECONDS = 120                # T51 R5 (B1): a MARKET leg's judged quote at most this old when sent
MARKET_AFTER_OPEN = pd.Timedelta(minutes=30)      # T51 R5 (B4): MARKET legs only from this long after the open ...
MARKET_BEFORE_CLOSE = pd.Timedelta(minutes=30)    # ... to this long before the close (a half-day's, as the calendar has it)
FRACTIONAL_MIN_VALUE = Decimal(5)                 # T51 R1: Webull's smallest fractional order value, in dollars (M)
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


# ---- T51c S2: the fund book's own figures in an account the owner also trades (pure; called by nothing yet)

ENTRY_KINDS = ("cash_in", "cash_out", "units_out", "split", "mark")   # R5: the owner's entries; T51c S4e (R34): `mark`
_BOOK_PREC = 60                                   # decimal digits: every sum and product of ledger figures is exact


class BookError(OrderStepError):
    """The ledger's rows, the owner's entries or the marks cannot make a book: said in words, nothing assumed."""


@dataclass(frozen=True)
class BookFigures:
    """The book's own figures (R4). `cash` is the cautious figure, `cash_low`; `cash_low` and `cash_high` bound the
    cash (None: no bound on that side), equal when every price and fee is known and an order's fee total and its rows'
    fees agree within REL_TOL (`cash_bounded` False; otherwise the bounds hold both fee figures). `usable_cash`
    (R8a) is `cash_low` less the proceeds (at that bound) of the book's sales with a fill on the given New York day
    or later, which are unsettled until the next session. `value` is `cash_low` plus every unit at its mark (None
    when the cash has no lower bound or a symbol held has no mark: `unmarked`); `value_high` is the same at
    `cash_high`. `units_outstanding` are the book's units issued and redeemed by the owner's entries;
    `value_per_unit` is `value` over them (None with none outstanding). `fees_taken_as_none` counts the purchases
    whose fee the venue left empty and whose recheck window has closed (addendum 7, R22 (b)). `open_by` holds the
    lines of `bounded_by` whose order leaves a bound open (a fee not known, or units without a price that no limit
    bounds or that a sale brought), the reasons a refusal gives (S4a addendum 1, R32). `bounded_orders` and
    `open_orders` are the WHOLE tags of the orders those lines name (a line cuts its tag for the eye; two tags sharing
    their first characters are still two orders: T51c S4b)."""
    units: dict
    cash: Decimal | None
    cash_low: Decimal | None
    cash_high: Decimal | None
    cash_bounded: bool
    bounded_by: tuple
    usable_cash: Decimal | None
    value: Decimal | None
    value_high: Decimal | None
    units_outstanding: Decimal
    value_per_unit: Decimal | None
    unmarked: tuple
    fees_taken_as_none: int                       # addendum 7 (R22): purchases whose empty fee was taken as none
    open_by: tuple                                # S4a addendum 1 (R32): the lines of `bounded_by` that leave a bound open
    bounded_orders: frozenset = frozenset()       # T51c S4b: the whole tags of the orders `bounded_by` names ...
    open_orders: frozenset = frozenset()          # ... and of those `open_by` names


@dataclass(frozen=True)
class PublishedFee:
    rate: Decimal
    source: str
    note: str


# addendum 7 (R22): the venue's published fee, the only figure ever assumed for a purchase whose fee it left empty. ONE
# constant with its citation; a change of the page changes it by a commit, never by a run.
PUBLISHED_PURCHASE_FEE = PublishedFee(
    rate=Decimal("0.000003"),
    source="Webull pricing page, Consolidated Audit Trail fee, read 2026-11-18: 0.000003 per unit of total trade "
           "volume, buys and sells (no commission on stocks and funds)",
    note="the page does not say whether the volume is in shares or dollars, so the larger of the two is taken")


def _dec(x, what: str) -> Decimal:
    """A ledger figure as an exact decimal: a float is the shortest decimal that reads back as it (repr), text and
    Decimal as written. Not finite, a bool or anything else: BookError."""
    if isinstance(x, bool) or x is None:
        raise BookError(f"{what} is {x!r}, not a number")
    try:
        d = Decimal(repr(float(x))) if isinstance(x, (int, float)) else Decimal(str(x)) if isinstance(x, (str, Decimal)) else None
    except Exception:
        d = None
    if d is None or not d.is_finite():
        raise BookError(f"{what} is {_cut(repr(x))}, not a finite number")
    return d


def _cut(text: str, n: int = 60) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "..."


def _opt_dec(x, what: str) -> Decimal | None:
    return None if x is None or x == "" else _dec(x, what)


def _published_fee_bound(units: Decimal, bound: Decimal) -> Decimal:
    """Addendum 7 (R22): B, the most the published rate can charge a purchase of `units` at a limit (or collar) of
    `bound`: the rate times the larger of the units and their value at it, rounded UP to the cent."""
    return (PUBLISHED_PURCHASE_FEE.rate * max(units, units * bound)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _fee_window_closed(o: dict, now: float) -> bool:
    """Addendum 7 (R22): whether the ledger has stopped asking the venue about this order, judged at `now` as its
    reconcile judges it, over the ledger's own `recheck_seconds` (LEDGER_RECHECK_SECONDS, its default): an order that
    reached filled, cancelled or expired is asked while `finished_at >= now - recheck_seconds`, one marked never
    arrived while `never_arrived_at` is. Closed only when that is KNOWN: any other state, or a time missing or not a
    finite number, is an open window (the cautious side)."""
    state = o.get("state")
    at = o.get("finished_at") if state in FINISHED else (
        o.get("never_arrived_at") if state == "never_arrived" else None)
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        return False
    return at < now - LEDGER_RECHECK_SECONDS


def book_figures(fills, orders, entries, marks, day: str, *, now: float) -> BookFigures:
    """T51c S2 (R4, R5, R8a): the book's figures from this ledger's own `fills` and `orders` rows (as
    OrderLedger.fills() and .orders() give them), the owner's `entries` (as the state file holds them, each with the
    figures next_entry gave it) and `marks` {symbol: price}; `day` is today's New York day (YYYY-MM-DD), for the
    unsettled proceeds of R8a; `now` is the present time in seconds on that day (addendum 7, R22: the reading `day`
    was taken from; one that is not a finite reading on `day` is refused), against which each order's recheck window
    is judged. Pure: it reads no file, clock, venue or global (the window's length is the ledger's own
    `recheck_seconds` default), and the order in which rows are given changes nothing (fills and entries are taken by
    time, a fill before an entry of the same time, then by id).

    Units per symbol: the signed sum of the fill rows (applied or not), times the ratio of each later `split` of that
    symbol, less each later `units_out`. Cash: the `cash_in` entries less the `cash_out` ones, less the cost of every
    purchase, plus the proceeds of every sale, less the fees, in exact decimals; the account's average cost is never
    read. A fill row's price covers `price_covers_units` units; what an order filled beyond the units its rows price
    is valued at the order's limit (a MARKET leg: its collar price), the most a purchase can have cost and the least a
    sale can have brought; it bounds the cash: a purchase's upper bound counts it at nothing, a sale's has no upper
    bound. An order with fills whose fee total is not known (the order's `fee` None), addendum 7 (R22): a PURCHASE none
    of whose rows carries a fee either, while its recheck window is open (or not known closed), has a fee between 0
    and B (PUBLISHED_PURCHASE_FEE's rate times the larger of its filled units and their value at its limit, a MARKET
    leg's collar, rounded up to the cent): the lower bound subtracts B, the upper nothing, and it is named in
    `bounded_by`; once its window has closed its fee is none for both bounds, counted in `fees_taken_as_none`; any
    other such order (a sale, a purchase with fee rows but no total, or one without a limit or collar) leaves the
    lower bound open (no fee bound is assumed), said in words, with its known row fees in the upper one. An order whose fee total is known and none of whose
    rows carries a fee has that total as its fee in both bounds. An order with a known total and at least one row
    fee (rows without one counting 0) takes the two figures as a pair: the larger in the lower bound, the smaller in
    the upper, and the cash marked bounded only when they differ by more than REL_TOL (a total that fell under the
    rows, which the ledger keeps). Units outstanding are the last entry's
    `units_after`; every entry must start where the one before it ended, or BookError names it."""
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or _ny_day(now) != day:
        raise BookError(f"the present time {_cut(repr(now))} is not a finite reading in seconds on the New York day "
                        f"{_cut(repr(day))} the figures are asked for, so no fee window can be judged (a reading in "
                        "milliseconds or another day's clock is a caller's mistake); no figure is given")
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        by_tag = {}
        for o in orders:
            by_tag[o["tag"]] = o
        rows: dict[str, list] = {}
        for f in fills:
            if f["tag"] not in by_tag:
                raise BookError(f"fill row {f.get('id')} is of order {_cut(f['tag'])}, which the ledger's orders do not hold")
            rows.setdefault(f["tag"], []).append(f)
        low: Decimal | None = Decimal(0)
        high: Decimal | None = Decimal(0)
        today_low = Decimal(0)
        none_taken = 0
        bounded: list[str] = []
        opened: list[str] = []                    # R32: the lines of orders that leave a bound OPEN (not merely bounded)
        bounded_tags: set = set()                 # S4b: the WHOLE tags of the orders those lines name
        open_tags: set = set()
        for tag in sorted(rows):
            o, rs = by_tag[tag], rows[tag]
            lines_before, open_before = len(bounded), len(opened)
            total = sum((_dec(r["units"], f"the units of fill row {r.get('id')}") for r in rs), Decimal(0))
            priced = covered = Decimal(0)
            for r in rs:
                if r.get("price") is not None and r.get("price_covers_units") is not None:
                    p = _dec(r["price"], f"the price of fill row {r.get('id')}")
                    c = _dec(r["price_covers_units"], f"the units priced by fill row {r.get('id')}")
                    priced += p * c
                    covered += c
            open_units = abs(total) - covered
            if open_units <= Decimal(repr(REL_TOL)) * max(Decimal(1), abs(total)):
                open_units = Decimal(0)
            buy = _dec(o["units"], f"the units of order {_cut(tag)}") > 0
            limit = o.get("collar_price") if o.get("order_type") == "MARKET" else o.get("limit_price")
            bound = _opt_dec(limit, f"the limit of order {_cut(tag)}")
            if open_units > 0:
                bounded.append(f"order {_cut(tag)}: {open_units} units without a price, "
                               + (f"bounded by its {'collar' if o.get('order_type') == 'MARKET' else 'limit'} {bound}"
                                  if bound is not None else "with no limit to bound them"))
                if bound is None or not buy:              # a purchase's lower bound, or a sale's upper one, is open
                    opened.append(bounded[-1])
            if buy:
                worst = None if open_units > 0 and bound is None else priced + open_units * (bound or 0)
                low = None if low is None or worst is None else low - worst
                high = None if high is None else high - priced
            else:
                least = priced + open_units * (bound or 0)
                low = None if low is None else low + least
                high = None if high is None or open_units > 0 else high + priced
                if any(_ny_day(r.get("time")) is None or _ny_day(r.get("time")) >= day for r in rs):
                    today_low += least
            fees = sum((_dec(r["fee"], f"the fee of fill row {r.get('id')}") for r in rs if r.get("fee") is not None),
                       Decimal(0))
            fees_least = fees_most = fees                # addendum 5 (R17): the order's total and its rows' sum as a
            if o.get("fee") is not None and total != 0:  # pair; the truth is one of the two (a total that fell, or
                told = _dec(o["fee"], f"the fee of order {_cut(tag)}")   # floats a hair apart), so the bounds hold both
                if not any(r.get("fee") is not None for r in rs):   # addendum 6 (R20): rows silent, not zero:
                    fees_least = fees_most = told                   # the total is the fee for both bounds
                else:
                    fees_least, fees_most = min(told, fees), max(told, fees)
                if fees_most - fees_least > Decimal(repr(REL_TOL)) * max(Decimal(1), fees_most):
                    bounded.append(f"order {_cut(tag)}: its fee total {told} and its rows' {fees} differ")
            if o.get("fee") is None and total != 0:      # addendum 7 (R22): a purchase the venue reported no fee for
                silent = not any(r.get("fee") is not None for r in rs)
                if buy and silent and _fee_window_closed(o, now):
                    none_taken += 1                       # (b) its window has closed: none, for both bounds
                elif buy and silent and bound is not None:
                    most = _published_fee_bound(abs(total), bound)        # (a) between 0 and B
                    bounded.append(
                        f"order {_cut(tag)}: the venue reported no fee and may still report one (its recheck window is "
                        f"open), so its fee is taken as between 0 and {most}: the published rate "
                        f"{PUBLISHED_PURCHASE_FEE.rate} times the larger of its {abs(total)} units and their value at "
                        f"its {'collar' if o.get('order_type') == 'MARKET' else 'limit'} {bound}, rounded up to the "
                        f"cent ({PUBLISHED_PURCHASE_FEE.source})")
                    if low is not None:
                        low -= most
                else:                                     # (d) as built, in its words: a sale, a purchase with fee rows
                    bounded.append(f"order {_cut(tag)}: its fee is not known")   # but no total, or no limit or collar
                    opened.append(bounded[-1])
                    low = None
            elif low is not None:
                low -= fees_most
            if high is not None:
                high -= fees_least
            if len(bounded) > lines_before:
                bounded_tags.add(tag)
            if len(opened) > open_before:
                open_tags.add(tag)
        units: dict[str, Decimal] = {}
        steps = [((_dec(f["time"], "a fill's time"), 0, int(f.get("id") or 0)), "fill", f) for f in fills]
        steps += [((_dec(e["time"], "an entry's time"), 1, int(e["id"])), "entry", e) for e in entries]
        flows = Decimal(0)
        outstanding = Decimal(0)
        chain = sorted(entries, key=lambda e: int(e["id"]))
        for i, e in enumerate(chain):
            before = _dec(e["units_before"], f"entry {e['id']}'s units before")
            if before != outstanding:
                raise BookError(f"entry {e['id']} starts from {before} units outstanding, but the entry before it left "
                                f"{outstanding}: the owner's entries do not chain")
            outstanding = _dec(e["units_after"], f"entry {e['id']}'s units after")
        for _, what, x in sorted(steps, key=lambda s: s[0]):
            if what == "fill":
                units[x["symbol"]] = units.get(x["symbol"], Decimal(0)) + _dec(x["units"], f"the units of fill row {x.get('id')}")
                continue
            kind = x["kind"]
            if kind == "cash_in":
                flows += _dec(x["amount"], f"entry {x['id']}'s amount")
            elif kind == "cash_out":
                flows -= _dec(x["amount"], f"entry {x['id']}'s amount")
            elif kind == "units_out":
                units[x["symbol"]] = units.get(x["symbol"], Decimal(0)) - _dec(x["units"], f"entry {x['id']}'s units")
            elif kind == "split":
                units[x["symbol"]] = units.get(x["symbol"], Decimal(0)) * _dec(x["ratio"], f"entry {x['id']}'s ratio")
            elif kind == "mark":                      # T51c S4e (R34): the owner took a quote as a fund's new mark: it
                pass                                  # changes no cash and no unit (it is read by far_quotes only)
            else:
                raise BookError(f"entry {x['id']} is of kind {_cut(repr(kind))}, which is none of {', '.join(ENTRY_KINDS)}")
        low = None if low is None else low + flows
        high = None if high is None else high + flows
        usable = None if low is None else low - today_low
        unmarked = tuple(sorted(s for s, u in units.items() if u != 0 and marks.get(s) is None))
        held = sum((u * _dec(marks[s], f"the mark of {_cut(s)}") for s, u in units.items() if u != 0 and s not in unmarked),
                   Decimal(0))
        value = None if low is None or unmarked else low + held
        value_high = None if high is None or unmarked else high + held
        per_unit = None if value is None or outstanding <= 0 else value / outstanding
        return BookFigures(units=units, cash=low, cash_low=low, cash_high=high, cash_bounded=bool(bounded),
                           bounded_by=tuple(sorted(bounded)), usable_cash=usable, value=value, value_high=value_high,
                           units_outstanding=outstanding, value_per_unit=per_unit, unmarked=unmarked,
                           fees_taken_as_none=none_taken, open_by=tuple(sorted(opened)),
                           bounded_orders=frozenset(bounded_tags), open_orders=frozenset(open_tags))


def _value_unknown_why(figures: BookFigures) -> str:
    """S4a addendum 1 (R32): why the value is not known, for a refusal: the orders that leave a bound open and the
    symbols without a mark, first and in full up to the cut, then how many other orders are only bounded (never given
    as the reason)."""
    reasons = list(figures.open_by) + ([f"no mark for {', '.join(figures.unmarked)}"] if figures.unmarked else [])
    if not reasons:                                       # nothing open is known: every line, as before
        return _cut("; ".join(figures.bounded_by), 300)
    others = len(figures.bounded_orders - figures.open_orders)      # T51c S4b: by the WHOLE tag, never the cut one
    why = _cut("; ".join(reasons), 300)
    if others:
        why += (f"; {others} other order{' is' if others == 1 else 's are'} only bounded, which is not why the value "
                "is not known")
    return why


def _positive(x, what: str) -> Decimal:
    if not isinstance(x, Decimal) or not x.is_finite() or x <= 0:
        raise BookError(f"{what} must be a positive decimal figure, not {_cut(repr(x))}; no figure is assumed")
    return x


def next_entry(figures: BookFigures, kind: str, *, amount: Decimal | None = None, symbol: str | None = None,
               units: Decimal | None = None, ratio: Decimal | None = None, mark: Decimal | None = None) -> dict:
    """T51c S2 (R5): the figures an owner's entry of `kind` is recorded with, worked out from the book's figures at
    its moment (`figures`, from book_figures with the marks the owner gave): its own figure as text, and the units
    outstanding and the value per unit before and after. Pure. Every figure is the owner's, a positive Decimal; none is
    defaulted. A stake change issues or redeems units at the value per unit (R4), so it is never profit or loss: a
    `cash_in` issues amount / value per unit (the first, with none outstanding, issues one unit per unit of the book's
    value after it, a value per unit of 1); a `cash_out` (at most the book's cash, lower bound) and a `units_out` (at
    most the book's units of that symbol, valued at `mark`) redeem at it; a `split` changes no unit outstanding. T51c S4e
    (R34): a `mark` (a fund's quote the owner took as its new mark: `symbol` and `mark`, the book holding units of that
    symbol) changes no cash, no unit and no value per unit (the figures are valued at the marks given, as a split's are).
    Refused (BookError) in words: another kind, a figure that is not a positive decimal, more than the book holds, or
    a book whose value is not known."""
    if kind not in ENTRY_KINDS:
        raise BookError(f"an owner's entry is of kind {', '.join(ENTRY_KINDS)}, not {_cut(repr(kind))}")
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        out = {"kind": kind, "symbol": None, "amount": None, "units": None, "ratio": None, "mark": None}
        if kind in ("cash_in", "cash_out"):
            out["amount"] = str(_positive(amount, "the amount"))
        else:
            if not _plain(_SYMBOL, symbol):
                raise BookError(f"a {kind} entry names a plain symbol, not {_cut(repr(symbol))}")
            out["symbol"] = symbol
            if kind == "units_out":
                out["units"], out["mark"] = str(_positive(units, "the units")), str(_positive(mark, "the mark"))
            elif kind == "mark":                          # T51c S4e (R34): the owner's yes to a quote; nothing else moves
                out["mark"] = str(_positive(mark, "the mark"))
                if figures.units.get(symbol, Decimal(0)) == 0:
                    raise BookError(f"the book holds no {symbol}, so a mark of it changes nothing and is not recorded; "
                                    "nothing was written")
            else:
                out["ratio"] = str(_positive(ratio, "the ratio"))
                if figures.units.get(symbol, Decimal(0)) == 0:   # addendum 6 (R21): a mistyped symbol records nothing
                    raise BookError(f"the book holds no {symbol}, so a split of it changes nothing and is not recorded; "
                                    "nothing was written")
        value, before, per_unit = figures.value, figures.units_outstanding, figures.value_per_unit
        if value is None:
            raise BookError(f"the book's value is not known ({_value_unknown_why(figures)}), so no entry can be valued now")
        if kind == "cash_in" and before == 0:
            after = value + amount
            if after <= 0:
                raise BookError(f"the book's value after this entry would be {after}, not above nothing")
            per_after = Decimal(1)
        else:
            if per_unit is None or per_unit <= 0:
                raise BookError(f"the book has no value per unit ({before} units outstanding, value {value}); only a "
                                "cash_in can open it")
            if kind == "cash_in":
                after = before + amount / per_unit
                value_after = value + amount
            elif kind == "cash_out":
                if figures.cash_low is None or amount > figures.cash_low:
                    raise BookError(f"the cash_out of {amount} is more than the book's cash ({figures.cash_low})")
                after, value_after = before - amount / per_unit, value - amount
            elif kind == "units_out":
                held = figures.units.get(symbol, Decimal(0))
                if units > held:
                    raise BookError(f"the units_out of {units} {symbol} is more than the book's {held} {symbol}")
                after, value_after = before - units * mark / per_unit, value - units * mark
            else:
                after, value_after = before, value
            if after <= 0:
                per_after = None
            elif kind in ("cash_in", "cash_out"):         # addendum 4 (R14): issued or redeemed AT the value per unit,
                per_after = per_unit                      # so it is the same decimal, by construction
            else:
                per_after = value_after / after
        out.update(units_before=str(before), units_after=str(after),
                   value_per_unit_before=None if before == 0 else str(per_unit),
                   value_per_unit_after=None if per_after is None else str(per_after))
        return out


def untracked_tags(ledger_tags, *, venue: str, journal=(), tickets=(), sends=()) -> list[dict]:
    """T51c S3 (R3): the local check for what a one-sided reconcile cannot see, needing no venue read. Every tag this
    code wrote to the journal (an `order_step` line of `venue` with a tag), to the state's send record (`sends`) or to
    a ticket (`tickets`: (tag, the line's outcome) pairs) must be among `ledger_tags`; one that is not is listed, in
    words (a ledger restored from an older copy, or a send the ledger lost), and is a fault that stops the run before
    anything is sent once S4 wires it. A ticket line whose outcome is "not sent" or "left over" had its tag noted before
    an intend that never happened, so it is not listed. Empty: nothing found. Pure; called by nothing yet."""
    known = set(ledger_tags)
    out, seen = [], set()

    def miss(tag, where: str) -> None:
        if tag in known or (tag, where) in seen:
            return
        seen.add((tag, where))
        out.append({"tag": tag, "where": where,
                    "reason": f"order {_cut(tag)} is in the {where} but not in the ledger: a ledger restored from an "
                              "older copy, or a send the ledger lost; nothing may be sent until a person has looked"})
    for e in journal:
        if isinstance(e, dict) and e.get("type") == "order_step" and e.get("venue") == venue and _plain(_TAG, e.get("tag")):
            miss(e["tag"], "journal")
    for tag in sends:
        miss(tag, "send record")
    for tag, outcome in tickets:
        if outcome in ("not sent", "left over"):
            continue
        miss(tag, "ticket")
    return out


# ---- T51c S4c: what a shared run judges on the book's value per unit (pure; every figure an exact decimal)

@dataclass(frozen=True)
class BookLoss:
    """The day's loss and the drawdown, in percent, on the value per unit (R27). `previous` is the value per unit the
    day's loss is measured from and `previous_from` says where it came from; `upper_open` is True when the previous
    look's upper figure was not known and its lower one stood in; `peak` is the largest upper figure of every look."""
    daily_loss_pct: Decimal
    drawdown_pct: Decimal
    previous: Decimal
    previous_from: str
    peak: Decimal
    upper_open: bool


def _nav_vpu(row: dict) -> tuple[Decimal | None, Decimal | None]:
    """(lower, upper) value per unit of a `book_nav` row: its value bounds over its units outstanding, exact; None for
    a bound the row does not have (the figures are stored as text, so they are compared only as decimals)."""
    units = _opt_dec(row.get("units_outstanding"), "a book_nav row's units outstanding")
    if units is None or units <= 0:
        return None, None
    low, high = (_opt_dec(row.get(k), f"a book_nav row's {k}") for k in ("value_low", "value_high"))
    return (None if low is None else low / units), (None if high is None else high / units)


def _stake_entries(entries) -> list:
    """T51c S4e (R34): the owner's entries that can be a base for the value per unit: every kind but `mark`. A mark entry
    moves no cash and no unit; it records a fund's new mark and is read by far_quotes alone, so the value per unit it
    carries (a real fall, taken as the new mark, is a LOWER one) must never become the previous figure the day stop
    measures from when no earlier row gives one, nor the history check's base: that would loosen both."""
    return [e for e in entries or () if e.get("kind") != "mark"]


def book_loss(rows, entries, day: str, *, low: Decimal, high: Decimal | None) -> BookLoss:
    """T51c S4c (R27): the day's loss and the drawdown of the value per unit now (`low`: the lower figure, `high`: the
    upper one, None when not known) against the book's own history. `rows` are the `book_nav` rows (dicts with their
    figures as text: day, id, units_outstanding, value_low, value_high), `entries` the owner's entries (day, id,
    value_per_unit_after), `day` today's New York day. The previous value per unit is the upper figure of the latest
    row (by day, then id) of an earlier day (its lower figure when its upper is not known: `upper_open`), else the
    value per unit after the latest owner entry of an earlier day, else the opening entry's (the book opened today).
    The peak is the largest upper figure (the lower where the upper is not known) over every row and now, compared
    as decimals. The day's loss is (previous - low) / previous and the drawdown (peak - low) / peak, each at least 0,
    in percent. The owner's entries change no value per unit, so they are never a gain or a loss. Refused (BookError)
    where no previous value per unit can be found, or a figure is not a finite decimal."""
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        seen = []
        for r in rows:
            lo, hi = _nav_vpu(r)
            if lo is not None or hi is not None:
                seen.append((str(r["day"]), int(r["id"]), lo, hi))
        earlier = [s for s in seen if s[0] < day]
        open_ = False
        if earlier:
            d, _, lo, hi = max(earlier, key=lambda s: (s[0], s[1]))
            previous, open_ = (hi, False) if hi is not None else (lo, True)
            came = f"the last look of {d}"
        else:
            entries = _stake_entries(entries)             # T51c S4e (R34): a `mark` entry is never a base for the loss
            before = [e for e in entries if str(e["day"]) < day and e.get("value_per_unit_after") is not None]
            if before:
                e = max(before, key=lambda e: int(e["id"]))
                previous, came = _dec(e["value_per_unit_after"], f"entry {e['id']}'s value per unit"), f"owner entry {e['id']}"
            elif entries and entries[0].get("value_per_unit_after") is not None:
                previous = _dec(entries[0]["value_per_unit_after"], "the opening entry's value per unit")
                came = "the opening entry (the book was opened today)"
            else:
                raise BookError("there is no earlier value per unit to measure the day's loss from (no earlier look, no "
                                "owner's entry with one); no figure is assumed")
        now_high = low if high is None else high
        peak = max([hi if hi is not None else lo for _, _, lo, hi in seen] + [now_high])
        if previous <= 0 or peak <= 0:
            raise BookError(f"the value per unit to measure from ({previous}) or the peak ({peak}) is not above nothing")
        return BookLoss(daily_loss_pct=max(Decimal(0), (previous - low) / previous * 100),
                        drawdown_pct=max(Decimal(0), (peak - low) / peak * 100), previous=previous, previous_from=came,
                        peak=peak, upper_open=open_)


def far_quotes(last_row: dict | None, entries, quotes: dict, held, limit_pct) -> list[dict]:
    """T51c S4c (R28): the held funds whose quote is further from their last `book_nav` mark than `limit_pct` percent
    (the owner's max_drawdown_pct). `last_row` is the latest row ({"time", "marks": {symbol: price text}}, None for
    no row: nothing to judge), `entries` the owner's entries, read in the order written: a `split` of the symbol
    written after the row's time moves the mark to what the split makes of it (the mark divided by its ratio), which is
    how the owner clears a real split; a `mark` entry of the symbol written after the row's time (T51c S4e, R34) sets
    the reference to the mark the owner took (a real move, said with that entry), and a split written after it divides
    that. A fund with no mark in the row or no quote is not judged here (a missing quote is the prices' own stop).
    Exactly `limit_pct` away is not beyond. Each answer: {"symbol", "quote", "mark", "reference"}."""
    if not last_row:
        return []
    marks, out = last_row.get("marks") or {}, []
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        limit = _dec(limit_pct, "the drawdown limit")
        for sym in sorted(held):
            mark, quote = marks.get(sym), quotes.get(sym)
            if mark is None or quote is None:
                continue
            ref = _dec(mark, f"the last mark of {_cut(sym)}")
            for e in entries:                              # in the order written: a mark entry sets the reference, a split divides it
                if e.get("symbol") == sym and float(e["time"]) > float(last_row["time"]):
                    if e.get("kind") == "split":
                        ref /= _dec(e["ratio"], f"entry {e.get('id')}'s ratio")
                    elif e.get("kind") == "mark":         # T51c S4e (R34): the owner took this quote as the new mark
                        ref = _dec(e["mark"], f"entry {e.get('id')}'s mark")
            price = _dec(quote, f"the quote of {_cut(sym)}")
            if ref > 0 and abs(price / ref - 1) * 100 > limit:
                out.append({"symbol": sym, "quote": float(price), "mark": float(_dec(mark, "mark")), "reference": float(ref)})
    return out


def _nav_rows_of(db) -> list[dict]:
    """The `book_nav` rows of an open state file, oldest first (see OrderStepState.book_nav_rows)."""
    cols = ("id", "day", "time", "batch", *OrderStepState._NAV_COLUMNS)
    out = []
    for raw in db.execute(f"SELECT {', '.join(cols)} FROM book_nav ORDER BY id").fetchall():
        row = dict(zip(cols, raw))
        try:
            marks = json.loads(row["marks"])
        except (ValueError, RecursionError):
            marks = None
        if not (isinstance(marks, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in marks.items())):
            raise OrderStepError(f"book_nav row {row['id']} holds marks that are not a JSON object of price texts; "
                                 "a person must look")
        row["marks"] = marks
        out.append(row)
    return out


def _accepted_hash(record: dict) -> str:
    """The hash of an owner's acceptance of a day's figure (`history_accepted`, S4d): every field but the hash itself,
    who and note included (a line edited afterwards, or one whose who or note the journal's writer redacted, is no longer
    an acceptance), chained by `prev_hash`. No secret is in it: whoever can write the journal can write a valid line."""
    fields = {k: v for k, v in record.items() if k in _ACCEPTED_FIELDS}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


_ACCEPTED_FIELDS = ("venue", "event", "accepted_day", "row_id", "row_batch", "value_per_unit", "value_per_unit_high",
                    "journal_had", "prev_hash", "who", "note")


def _decimal_or_none(text) -> Decimal | None:
    try:
        figure = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return figure if figure.is_finite() else None


def _scan_shared_looks(entries, venue: str) -> tuple[list[dict], str, set]:
    """T51c S4d: (looks, last_accepted_hash, batches), from the journal's entries in the journal's order.
    `looks`: the shared looks the journal's summaries of `venue` recorded: each summary with a `shared` block that holds
    a finite `value_per_unit` (the lower figure of its last look, the text the run's `book_nav` row holds) gives
    {"day", "low", "high", "has_high"}: its New York day, the lower figure and the upper one (`value_per_unit_high`, a
    decimal or None when the upper value was not known; `has_high` False for a summary written before the upper figure
    was journaled). A summary without a readable figure is not counted (a run that stopped before its first look has
    none); a ticket's `equity` never is (a later look may lower the row it was made at). An owner's acceptance of a day's
    figure (`history_accepted`: see accept_history_command) counts as a look of its `accepted_day` when its hash is its
    own fields' and its `prev_hash` is the hash of the acceptance before it, AND its figures are not below the day's last
    look before it (lower figure, and upper where known else lower): the tighten rule, read again (a line that only looks
    like an acceptance, or one that would loosen, is ignored and does not move the chain).
    `batches`: every batch a summary of `venue` names, whatever it holds. An acceptance of a day with no earlier journaled day
    is held against the base `book_loss` falls back to (`_entry_base`) AS IT STOOD when its line was written: the journal's
    own owner-entry lines before it, on its own day, never an entry made after it (a later ordinary entry cannot cancel it)."""
    looks, last, batches, owners = [], "", set(), []
    for e in entries:
        if isinstance(e, dict) and e.get("type") == "owner_entry" and e.get("venue") == venue:
            owners.append(e)                             # the owner's entries so far, in the journal's order
            continue
        if not (isinstance(e, dict) and e.get("type") == "order_step" and e.get("venue") == venue):
            continue
        if e.get("event") == "history_accepted":
            low, high = _decimal_or_none(e.get("value_per_unit")), _decimal_or_none(e.get("value_per_unit_high"))
            if (low is not None and e.get("prev_hash") == last and isinstance(e.get("hash"), str)
                    and e["hash"] == _accepted_hash(e) and isinstance(e.get("accepted_day"), str)):
                before = [x for x in looks if x["day"] == e["accepted_day"]]      # the tighten rule, read again here:
                if not before:                                                     # a day with no look yet: the last day's
                    earlier = [x for x in looks if x["day"] < e["accepted_day"]]   # figures before it, or with no such day
                    try:                                                           # the floor AS IT STOOD when the line was
                        floor = _entry_base(owners, str(e.get("day") or e["accepted_day"]))   # written: the owner's entries
                    except (OrderStepError, KeyError, TypeError, ValueError):                # before it in the journal,
                        floor = None                                               # never those made after it
                    before = ([x for x in earlier if x["day"] == max(y["day"] for y in earlier)] if earlier else
                              [floor] if floor is not None else [])                # the base book_loss falls back to
                if before and low >= before[-1]["low"] and (                       # an acceptance counts only when it is
                        high if high is not None else low) >= (                   # not below the day's last figures
                        before[-1]["high"] if before[-1]["high"] is not None else before[-1]["low"]):
                    last = e["hash"]
                    looks.append({"day": e["accepted_day"], "low": low, "high": high, "has_high": True, "accepted": True})
            continue
        if e.get("event") != "summary":
            continue
        if isinstance(e.get("batch"), str):
            batches.add(e["batch"])
        if not (isinstance(e.get("shared"), dict) and isinstance(e.get("day"), str)):
            continue
        block = e["shared"]
        low = _decimal_or_none(block.get("value_per_unit"))
        if low is None:
            continue
        has_high = "value_per_unit_high" in block
        looks.append({"day": e["day"], "low": low, "has_high": has_high,
                      "high": _decimal_or_none(block["value_per_unit_high"]) if has_high else None})
    return looks, last, batches


def _journal_shared_looks(entries, venue: str) -> list[dict]:
    return _scan_shared_looks(entries, venue)[0]


def _row_figures(row: dict) -> tuple[Decimal, Decimal | None]:
    """T51c S4d: a `book_nav` row's (lower, upper) value per unit as the day stop and the peak read them (`book_loss`, by
    `_nav_vpu`): value_low and value_high over units_outstanding, exact decimals; the upper None when the row has none. The
    one basis of every safety decision on a row (the history check, the accept judge, the confirm code, the preview): the
    row's text `value_per_unit` is never one. A row that book_loss would read differently from what it says is refused in
    words (OrderStepError: a state fault, a person must look): units outstanding, value_low, value_high or the text that are
    not finite numbers (NaN, text), units that are not above nothing, a value_high below its value_low, and a `value_per_unit`
    text that is not value_low over the units (the writer stores `value / units_outstanding` from the same division at
    _BOOK_PREC digits, so the two are equal exactly, with no rounding allowed between them)."""
    try:
        return _row_figures_unguarded(row)
    except DecimalException as exc:                      # Overflow, Underflow, InvalidOperation, DivisionByZero ...
        raise OrderStepError(f"book_nav row {row.get('id')} of {_cut(repr(row.get('day')))} cannot be read as the day stop "
                             f"reads it ({type(exc).__name__}: its figures are out of range); a person must look") from None


def _row_figures_unguarded(row: dict) -> tuple[Decimal, Decimal | None]:
    where = f"book_nav row {row.get('id')} of {_cut(repr(row.get('day')))}"

    def need(key: str, optional: bool = False):
        raw = row.get(key)
        if raw is None or raw == "":
            if optional:
                return None
            raise OrderStepError(f"{where} has no {key}; a person must look")
        try:
            figure = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            figure = None
        if figure is None or not figure.is_finite():
            raise OrderStepError(f"{where} holds a {key} that is not a finite number ({_cut(repr(raw))}); a person must look")
        return figure
    units, value_low, value_high, text = (need("units_outstanding"), need("value_low"), need("value_high", True),
                                          need("value_per_unit"))
    if units <= 0:
        raise OrderStepError(f"{where} holds units outstanding of {units}, which is not above nothing; a person must look")
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        low, high = _nav_vpu(row)
        if text != low:
            raise OrderStepError(f"{where} reads a value per unit of {text} but its value_low over its units outstanding is "
                                 f"{low}; the day stop and the peak read value_low, never the text; a person must look")
        if value_high is not None and value_high < value_low:
            raise OrderStepError(f"{where} holds a value_high ({value_high}) below its value_low ({value_low}); a person "
                                 "must look")
    return low, high


_ROW_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _row_day(row: dict, today: str) -> str:
    """A `book_nav` row's day, which must be a standard date: exactly YYYY-MM-DD and a real calendar date, and not after
    `today` (the run's New York session day). The history check looks days up as text and `book_loss` orders them as text,
    so a day that only looks like a date ('2026-11-12z', '2026-11-12 ', '2026-02-30') would escape the one and sort oddly in
    the other. Anything else is OrderStepError in words: a state fault, a person must look."""
    day = row.get("day")
    where = f"book_nav row {row.get('id')} of the day {_cut(repr(day))}"
    if not (isinstance(day, str) and _ROW_DAY.fullmatch(day)):
        raise OrderStepError(f"{where}: a row's day must be exactly YYYY-MM-DD; a person must look")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise OrderStepError(f"{where}: that is not a real calendar date; a person must look") from None
    if day > today:
        raise OrderStepError(f"{where}: that day is after today's session day {today}; a person must look")
    return day


def _entry_base(entries, today: str):
    """The value per unit `book_loss` falls back to when no row of an earlier day gives one: the value per unit after the
    latest owner entry of an earlier day, else after the opening (first) entry, as {"low", "high", "has_high", "entry"} (the
    one figure for both bounds), or None when there is none. The same choice as book_loss's, in the same order."""
    entries = _stake_entries(entries)                     # T51c S4e (R34): never a `mark` entry
    before = [e for e in entries if str(e["day"]) < today and e.get("value_per_unit_after") is not None]
    if before:
        e, what = max(before, key=lambda e: int(e["id"])), "the latest earlier owner entry"
    elif entries and entries[0].get("value_per_unit_after") is not None:
        e, what = entries[0], "the opening owner entry (the book was opened today)"
    else:
        return None
    figure = _dec(e["value_per_unit_after"], f"entry {e['id']}'s value per unit")
    return {"low": figure, "high": figure, "has_high": True, "entry": f"{what}, entry {e['id']}"}


def _nav_history(rows, looks, today: str, entries=None) -> dict:
    """T51c S4d: the state file's `book_nav` rows against the journal's looks (see _live_nav_history): {"lacking": the
    days with a look and no row, "changed": per day the look and the day's latest row where they differ, "unheld": the days
    with a row, no look and a place between the last day the journal holds before `today` and `today` (such a row would be
    the previous value per unit `book_loss` takes: see _live_nav_history), "base": the last look of that last day, or with
    no such day the value per unit `book_loss` falls back to (_entry_base: the owner's entries), "peak":
    the highest figure the rows hold (a row's upper figure where finite, else its lower), "seen": the journal's, or None}.
    Every row's day must be a standard date not after `today` (_row_day)."""
    latest, peak = {}, None
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        for row in rows:                                 # by id: a later row of a day replaces an earlier one
            _row_day(row, today)
            low, high = _row_figures(row)                # the day stop's own basis, value_low and value_high over units
            basis = high if high is not None else low
            peak = basis if peak is None or basis > peak else peak
            latest[str(row["day"])] = {"low": low, "high": high, "row": row}
    last = {}
    for look in looks:                                   # the journal's order: the last look of a day wins
        last[look["day"]] = look
    lacking, changed = [], {}
    for day in sorted(last):
        look, got = last[day], latest.get(day)
        if got is None:
            lacking.append(day)
            continue
        fits = [look]                                    # the day's latest row reads the last look ...
        if look.get("accepted"):                         # ... or, when that is an acceptance, the last SUMMARY before it
            fits += [x for x in looks if x["day"] == day and not x.get("accepted")][-1:]   # (the real state file back)
        if not any(got["low"] == x["low"] and (not x["has_high"] or got["high"] == x["high"]) for x in fits):
            changed[day] = {"look": look, "now": got}
    seen = max((look["high"] if look["high"] is not None else look["low"] for look in looks if not look.get("accepted")),
               default=None)                              # an acceptance is of a row the state file holds: no peak of its own
    earlier = [d for d in last if d < today]
    base_day = max(earlier, default="")
    unheld = sorted(d for d in latest if d not in last and base_day < d < today)
    base = last[base_day] if earlier else _entry_base(entries, today)       # no earlier journaled day: book_loss's own
    return {"lacking": lacking, "changed": changed, "unheld": unheld, "base": base, "peak": peak, "seen": seen}


def _row_batch_of(rows, day: str):
    return [r["batch"] for r in rows if r["day"] == day][-1]


def _figures_said(low, high, has_high=True) -> str:
    upper = "not journaled" if not has_high else ("not known" if high is None else str(high))
    return f"{low} (upper {upper})"


def _shortfall_figures(shorts: dict) -> dict:
    """A shared run's shortfalls as the summary says them: per plain symbol, the book's units, the account's, the
    unapplied fills' and the units short, as numbers only."""
    return {sym: {k: _number_or_none(d.get(k)) for k in ("book", "venue", "unapplied", "short")}
            for sym, d in sorted(shorts.items(), key=lambda kv: str(kv[0])) if _plain(_SYMBOL, sym) and isinstance(d, dict)}


_FIGURE = re.compile(r"[0-9]+(\.[0-9]+)?")       # the owner's figure as written: digits, an optional decimal part
_OWNER_USES = {"cash_in": ("amount",), "cash_out": ("amount",), "units_out": ("symbol", "units"),
               "split": ("symbol", "ratio"), "mark": ("symbol",)}
_OWNER_ARGUMENTS = ("amount", "units", "ratio", "symbol")
_ENTRY_HASH = re.compile(r"[0-9a-f]{64}")
_ENTRY_NUMBER = re.compile(r"[1-9][0-9]*")
OWNER_PREVIEW_KIND = "order-step owner-entry preview"   # T51c S4b: what the pending preview's file says it is
_PREVIEW_FIELDS = ("what", "venue", "code", "created", "expires", "marks", "arguments", "head", "ledger", "held",
                   "close")                               # addendum 2 (R38): every field a confirm reads
# T51c S4b: the ledger's fields that book_figures reads; a confirm holds them against its preview's
_FIGURED_FILL_FIELDS = ("id", "tag", "symbol", "units", "price", "price_covers_units", "fee", "time")
_FIGURED_ORDER_FIELDS = ("tag", "units", "order_type", "limit_price", "collar_price", "fee", "state", "finished_at",
                         "never_arrived_at")
_RUN_FLAGS_WORDS = ("the owner's entry is written on its own, without --propose, --approve, --check, "
                    "--capital, --fractional or --shared (and --live only for a production venue's book); nothing was "
                    "written")


def _owner_figure(text, what: str) -> Decimal:
    if not isinstance(text, str) or not _FIGURE.fullmatch(text) or Decimal(text) <= 0:
        raise BookError(f"{what} must be a positive decimal figure written as digits with an optional decimal part "
                        f"(such as 1250.00), not {_cut(repr(text))}; nothing was written")
    return Decimal(text)


def owner_preview_path_for(state_path: str | Path) -> Path:
    """T51c S4b (R34 as built): the owner's ONE pending preview of a venue, in its own small file beside the state
    file (order_step-<venue>.owner-preview.json), written atomically and owner-only. Read only by the owner's command:
    no run, reconcile, ticket book, journal reader or alert opens it."""
    p = Path(state_path)
    return p.with_name(f"{p.stem}.owner-preview.json")


def _spend_preview(path: Path, record: dict | None) -> str:
    """Remove the pending preview `record` from `path` (None: whatever is there, a file that is not a preview); a
    preview made since (another code) is left as it is. The words a refusal adds."""
    try:
        now_there = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "; the preview is spent"
    except (OSError, ValueError, RecursionError):        # addendum 2 (R38): whatever reading, decoding or parsing raises
        now_there = None
    if record is not None and isinstance(now_there, dict) and now_there.get("code") != record.get("code"):
        return "; a newer preview was made meanwhile and stays pending"
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        return f"; the preview could not be removed ({_local_error(exc)}), but its code is never taken twice"
    return "; the preview is spent: run the command again without --confirm to see the figures again"


def _owner_refuse(words: str, pending: tuple | None = None) -> int:
    """The owner's command's one way to refuse: the words on stderr, exit 2. With `pending` ((path, record): a confirm
    that has read the pending preview) the preview is spent first (T51c S4b). Every refusal goes through here."""
    import sys
    if pending is not None:
        words += _spend_preview(*pending)
    print(f"refused: {words}", file=sys.stderr)
    return 2


def _write_private(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (a new file in the same folder, made durable, then renamed over it), readable
    and writable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        _sync_file(tmp)
        os.replace(tmp, path)
        _sync_file(path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ledger_digest(fills, orders) -> str:
    """The sha256 of the ledger's rows as book_figures reads them (its fields only, in any order the rows come)."""
    rows = {"fills": sorted(([f.get(k) for k in _FIGURED_FILL_FIELDS] for f in fills), key=repr),
            "orders": sorted(([o.get(k) for k in _FIGURED_ORDER_FIELDS] for o in orders), key=repr)}
    return hashlib.sha256(json.dumps(rows, default=repr, separators=(",", ":")).encode()).hexdigest()


def _owner_arrived(ledger_path: Path, state_path: Path, day: str, now: float, valued: dict,
                   ledger_was_there: bool) -> str | None:
    """T51c S4b addendum 1 (R35): under the run lock, the ledger's fills and orders and the owner's entries read AGAIN;
    what arrived since the entry was valued (`valued`: its ledger digest, chain head and set of funds held), in words,
    or None when the figures read what was valued. No quote is asked: the lock is never held across the network.
    Addendum 2 (R38): a ledger that was read when the entry was valued and is gone now, or that cannot be read again,
    is said so (`ledger_was_there`: the first read found the file)."""
    from valedia_trader.execution_and_risk.orders import open_read_only
    fills, orders = [], []
    if ledger_path.exists():
        try:
            ro = open_read_only(ledger_path)
            try:
                fills, orders = ro.fills(), ro.orders()
            finally:
                ro.close()
        except (OrderStepError, OrderError, sqlite3.Error, OSError) as exc:
            return f"the ledger could not be read again under the lock ({_cut(str(exc), 300)})"
    elif ledger_was_there:
        return ("the ledger is gone: it was read when the entry was valued and is not there under the lock (a person "
                "must look)")
    try:
        entries = _owner_entries_read_only(Path(state_path))
    except (OrderStepError, OrderError, sqlite3.Error, OSError) as exc:
        return f"the owner's entries could not be read again under the lock ({_cut(str(exc), 300)})"
    try:
        head = entries[-1]["hash"] if entries else _GENESIS
        held = sorted(book_figures(fills, orders, entries, {}, day, now=now).unmarked)
    except (OrderStepError, OrderError) as exc:
        return f"the book's figures could not be read again under the lock ({_cut(str(exc), 300)})"
    if head != valued["head"]:
        return "an owner's entry arrived while the entry was being confirmed"
    if _ledger_digest(fills, orders) != valued["ledger"] or held != valued["held"]:
        return "a fill or an order arrived while the entry was being confirmed"
    return None


def owner_mark_close(calendar, now: float) -> tuple[bool, float | None, str | None]:
    """T51c S4b: which quotes may mark an owner's entry at `now`. (True, None, None) INSIDE the exchange's session:
    from its open to its close on `now`'s New York day, by `_window` (the order step's own reading of the calendar)
    with no offsets. (False, close, None) OUTSIDE it: the last session's close, in seconds: today's close once it has
    passed, else the calendar's own `previous_close` of `now`'s minute (before the open, a day without a session).
    (False, None, why) when the calendar cannot say."""
    start, end, why = _window(calendar, now, pd.Timedelta(0), pd.Timedelta(0))
    if why is None and start <= now <= end:
        return True, None, None
    if why is None and now > end:
        return False, float(end), None
    try:
        close = _as_float(pd.Timestamp(calendar.previous_close(
            pd.Timestamp(now, unit="s", tz="UTC").floor("min"))).timestamp())
    except Exception as exc:
        return False, None, f"the exchange calendar could not give the last session's close ({_describe(exc)})"
    if close is None or close > now:
        return False, None, "the exchange calendar gave no session close before now"
    return False, close, None


def _owner_marks(symbols: list[str], quotes, calendar, now: float) -> tuple[dict, float | None, str | None]:
    """T51c S4b (R29): the marks of `symbols` from the order step's own quote source (`quotes`, the function a run is
    handed: minute_quotes), each read with the run's own reader (_read_quote): inside the session judged at `now`
    (at most QUOTE_MAX_AGE_SECONDS old, not stamped more than QUOTE_MAX_AHEAD_SECONDS ahead); outside it judged by the
    same rule at the last session's close, so the quote that was usable at that close serves. ({symbol: {"price",
    "time"}}, the close or None inside, None), or ({}, None, why) naming each fund and why. No symbol: nothing is
    asked, not even the calendar."""
    if not symbols:
        return {}, None, None
    try:
        cal = _default_calendar() if calendar is None else calendar
    except Exception as exc:
        return {}, None, f"the exchange calendar could not be read ({_describe(exc)}); nothing was written"
    inside, close, why = owner_mark_close(cal, now)
    if why is not None:
        return {}, None, f"{why}, so no quote can be judged; nothing was written"
    try:
        raw = dict(quotes(list(symbols)) or {})
    except Exception as exc:
        return {}, None, f"the quotes could not be read: {_venue_error(exc)}; nothing was written"
    marks, bad = {}, []
    for sym in symbols:
        price, t, why = _read_quote(raw.get(sym), now if inside else close)
        if price is None:
            bad.append(f"{sym}: {why}" + ("" if inside else f" at the last session's close, {_iso(close)}"))
        else:
            marks[sym] = {"price": price, "time": t}
    if bad:
        return {}, None, (f"no usable quote for {'; '.join(bad)} (the order step's own quote source and age rule; a "
                          "typed figure is never a mark); nothing was written")
    return marks, close, None


def _owner_journal_line(venue: str, row: dict) -> dict:
    """The journal line of an owner's entry, as the command writes it (and the repair writes it again)."""
    return {"type": "owner_entry", "venue": venue, **{k: row[k] for k in _JOURNALED_ENTRY},
            "time": repr(float(row["time"]))}


def _figure_text(kind: str, arguments: dict) -> str:
    if kind in ("cash_in", "cash_out"):
        return arguments["amount"]
    if kind == "units_out":
        return f"{arguments['units']} {arguments['symbol']}"
    if kind == "mark":                                    # T51c S4e (R34): the figure is the quote, shown in the marks
        return f"{arguments['symbol']}, its quote taken as the fund's new mark"
    return f"{arguments['symbol']} at {arguments['ratio']} new units per old"


def _read_preview(path: Path, venue: str) -> dict | None:
    """The pending preview of `venue`, or None when there is none; ValueError when it is not one."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except RecursionError:                                # addendum 2 (R38): JSON nested too deeply to parse
        raise ValueError("it is nested too deeply to parse") from None
    # addendum 2 (R38): every one of the fields a confirm reads is PRESENT (a missing key is not read as null)
    if not (isinstance(record, dict) and all(key in record for key in _PREVIEW_FIELDS)):
        raise ValueError("it is not an owner-entry preview of this venue")
    # addendum 1 (R36): every field a confirm reads is checked here, so a file edited by hand is refused, not a crash:
    # the code (compared), created and expires (the life), each mark's price (the band), and the fields compared
    # whole with the figures now (arguments, head, ledger, held, close), of the types the preview writes.
    if not (isinstance(record, dict) and record.get("what") == OWNER_PREVIEW_KIND and record.get("venue") == venue
            and isinstance(record.get("code"), str) and _plain(_NUMBER, record["code"])
            and _finite(record.get("created")) and _finite(record.get("expires"))
            and isinstance(record.get("marks"), dict)
            and all(isinstance(m, dict) and _finite(m.get("price")) for m in record["marks"].values())
            and isinstance(record.get("arguments"), dict) and isinstance(record.get("head"), str)
            and isinstance(record.get("ledger"), str) and isinstance(record.get("held"), list)
            and all(isinstance(s, str) for s in record["held"])
            and (record.get("close") is None or _finite(record.get("close")))):
        raise ValueError("it is not an owner-entry preview of this venue")
    return record


def _preview_differences(pending: dict, now_record: dict) -> list[str]:
    """T51c S4b: what differs between the pending preview and the same command's figures now, in words: anything but
    the marks differing at all, a mark more than TICKET_QUOTE_MOVE from the preview's (the ticket's own band, judged
    as an approval judges a line's quote)."""
    out = []
    words = {"arguments": "the entry's arguments (its kind, figures, symbol, who or note)",
             "head": "the owner's entries (another entry was written since the preview)",
             "ledger": "the ledger's fills or orders as the book's figures read them (a fill or an order arrived or "
                       "changed since the preview)",
             "held": "the set of funds the book holds",
             "close": "the marks' basis (the session opened or closed since the preview)"}
    for key, said in words.items():
        if pending.get(key) != now_record[key]:
            out.append(said)
    old, new = pending.get("marks") or {}, now_record["marks"]
    if set(old) != set(new):
        out.append(f"the funds quoted ({', '.join(sorted(old)) or 'none'} then, {', '.join(sorted(new)) or 'none'} now)")
    else:
        for sym in sorted(new):
            was, now = _as_float((old[sym] or {}).get("price")), new[sym]["price"]
            if was is None or not abs(now - was) <= TICKET_QUOTE_MOVE * was:
                out.append(f"the mark of {sym}: {now:g} now, more than {TICKET_QUOTE_MOVE:.1%} from the preview's "
                           f"{was if was is None else format(was, 'g')}")
    return out


def owner_entry_command(args, paths, clock: Callable[[], float] | None = None, *, quotes: Callable | None = None,
                        calendar=None) -> int:
    """T51c S3 (R5), wired by S4b (R29): `order-step --owner-entry KIND --venue NAME --who WHO --note NOTE` and the
    figures of that kind, which the OWNER runs: `cash_in`/`cash_out` take --amount; `units_out` takes --symbol and
    --units (shares he took that the book counted as its own); `split` takes --symbol and --ratio (new units per old);
    `mark` (T51c S4e, R34: "this quote is real; take it as the new mark") takes --symbol alone and moves no cash and
    no unit: it records the quote the order step's own quote path gave as that fund's new mark, so the marks stop (R28)
    clears for it on the next run.

    Marks are never typed (`--mark` is refused): every fund the book holds units of, and the entry's own symbol for a
    units_out or a split, is quoted by the order step's own quote source (`quotes`; minute_quotes, the function a run
    is handed) and read by the run's own reader and age rule (_owner_marks, owner_mark_close: inside the session at
    the clock reading, outside it at the last session's close). A book holding no units asks for no quote.

    Run without --confirm it is a PREVIEW: it prints the kind and its figure, each mark with its time and source, the
    units outstanding and the value per unit before and after, the book's value and its bounds, and a six-digit code,
    and writes one pending preview (owner_preview_path_for; a new preview replaces the one before) and nothing else:
    no byte of the state file, the journal or the ledger. Run again with the same arguments and `--confirm CODE`
    within TICKET_LIFE_SECONDS, it asks the quotes again and writes the entry (at the CONFIRM's marks) only when every
    mark is within TICKET_QUOTE_MOVE of the preview's and nothing else differs (_preview_differences); a confirm that
    has read the pending preview and does not write SPENDS it, whatever the reason, so the owner sees the figures again
    before any entry. The entry is valued with book_figures and next_entry and appended to the venue's state file
    under the run lock, then journaled (one `owner_entry` line, by the journal's own writer, addendum 4 R12).

    `--owner-entry-rejournal N --hash H --who --note` (no kind, no figure) writes the missing journal line of entry N
    (_owner_rejournal). A production venue's name is refused in every form, before any quote is asked, unless `--live`
    is given with it (S4d: the live book's entries, with everything above holding there too); `--live` with any other
    name is refused the same way (R23).

    Exit 0: a preview printed, an entry written and journaled, or a line rejournaled. Exit 2: refused in words on
    stderr, writing nothing to the state file, the journal or the ledger (R13: every refusal is decided before the
    lock is taken or the state file opened for writing; the clock is read once). Exit JOURNAL_NOT_WRITTEN (3): the
    entry is written and its journal line is not; the words give its hash for the repair."""
    import sys
    kind = getattr(args, "owner_entry", None)
    confirm = getattr(args, "confirm", None)
    if (getattr(args, "propose", False) or getattr(args, "approve", None) is not None
            or getattr(args, "check", False) or getattr(args, "capital", None) is not None
            or getattr(args, "fractional", False) or getattr(args, "shared", False)):
        return _owner_refuse(_RUN_FLAGS_WORDS)
    live = bool(getattr(args, "live", False))
    venue = getattr(args, "venue", None)
    if not isinstance(venue, str) or not venue or _safe_name(venue) != venue or venue.strip(".") == "":
        return _owner_refuse("the owner's entry needs --venue, the plain name of the venue whose book it is (the name "
                             "in its state file's name); nothing was written")
    # addendum 7 (R23, reversed for --live only by S4d): the live venue's state file is reached by its name alone, so a
    # production venue's name is taken only together with --live, and --live takes only a production venue's name
    if _production_venue_name(venue) != live:
        return _owner_refuse(
            f"{_cut(repr(venue))} is a production venue's name: an owner's entry for a live venue is written only "
            "together with --live; nothing was written" if not live else
            "the owner's entry is written on its own, without --propose, --approve, --check, --capital, --fractional or "
            f"--shared; --live goes only with a production venue's name (it starts {_PRODUCTION_PREFIX!r}: the live "
            f"venue's book), and {_cut(repr(venue))} is a practice name; nothing was written")
    if getattr(args, "mark", None) is not None:           # S4b (R29): marks are never typed
        return _owner_refuse("--mark is not taken: an owner's entry is valued at the order step's own quotes (the "
                             "quote source and age rule a run uses; outside the session the last session's close), "
                             "never at a typed figure; nothing was written")
    for what in ("who", "note"):                          # addendum 5 (R15): judged in full, before anything is read
        problem = _owner_text_problem(what, getattr(args, what, None))
        if problem is not None:
            return _owner_refuse(f"the owner's entry needs --{problem}")
    if getattr(args, "owner_entry_rejournal", None) is not None:
        return _owner_rejournal(args, paths, venue, clock)
    if getattr(args, "hash", None) is not None:
        return _owner_refuse("--hash goes only with --owner-entry-rejournal N; nothing was written")
    if kind not in _OWNER_USES:
        return _owner_refuse(f"an owner's entry is of kind {', '.join(ENTRY_KINDS)}; nothing was written")
    for name in _OWNER_ARGUMENTS:
        given = getattr(args, name, None) is not None
        if name in _OWNER_USES[kind] and not given:
            return _owner_refuse(f"a {kind} entry needs --{name}; no figure is ever assumed; nothing was written")
        if name not in _OWNER_USES[kind] and given:
            return _owner_refuse(f"a {kind} entry takes no --{name}; nothing was written")
    if confirm is not None and not _plain(_NUMBER, confirm):
        return _owner_refuse(f"--confirm takes the six-digit code the preview printed, not {_cut(repr(confirm))}; "
                             "nothing was written")
    try:
        figs = {n: _owner_figure(getattr(args, n), f"--{n}") for n in ("amount", "units", "ratio")
                if n in _OWNER_USES[kind]}
    except BookError as exc:
        return _owner_refuse(str(exc))
    symbol = getattr(args, "symbol", None)
    if symbol is not None and not _plain(_SYMBOL, symbol):
        return _owner_refuse(f"--symbol names a plain symbol, not {_cut(repr(symbol))}; nothing was written")
    from valedia_trader.execution_and_risk.orders import open_read_only
    ledger_path = ledger_path_for(paths.memory, venue)
    state_path = state_path_for(ledger_path, venue)
    preview_path = owner_preview_path_for(state_path)
    clock = clock if clock is not None else wall_clock
    quotes = quotes if quotes is not None else minute_quotes
    reading = clock()                                     # addendum 4 (R13): read ONCE; the entry's time and day
    day = _ny_day(reading)
    if day is None:
        return _owner_refuse("the clock gives no New York day, so the entry has no day; nothing was written")
    now = float(reading)
    arguments = {"kind": kind, **{n: getattr(args, n, None) for n in _OWNER_ARGUMENTS}, "who": args.who,
                 "note": args.note}
    pending = None
    if confirm is not None:                               # S4b: the pending preview, before anything else is read
        try:
            record = _read_preview(preview_path, venue)
        except (OSError, ValueError) as exc:
            return _owner_refuse(f"the pending preview cannot be read ({_cut(_local_error(exc), 200)}); nothing was "
                                 "written", (preview_path, None))
        if record is None:
            return _owner_refuse("no preview is pending for this venue: run the same command without --confirm first, "
                                 "read what it prints, then confirm its code; nothing was written")
        pending = (preview_path, record)
        if not hmac.compare_digest(record["code"].encode(), confirm.encode()):
            return _owner_refuse("that code is not the pending preview's (a mistyped code, or an earlier preview's: a "
                                 "new preview replaces the one before it); nothing was written", pending)
        if now > ticket_expiry(record):
            return _owner_refuse(f"the preview has expired (made at {_iso(float(record['created']))}, good until "
                                 f"{_iso(ticket_expiry(record))}); nothing was written", pending)
        if not now >= float(record["created"]):
            return _owner_refuse("the clock gives a time before the preview was made: the clock is behind the "
                                 "record; nothing was written", pending)
    from valedia_trader.research_loop.paper import Journal
    journal_dir = Path(paths.journal)
    try:                                                  # every refusal is decided here, before anything is created,
        fills, orders = [], []                            # taken or opened for writing
        ledger_was_there = ledger_path.exists()
        if ledger_was_there:
            ro = open_read_only(ledger_path)
            try:
                fills, orders = ro.fills(), ro.orders()
            finally:
                ro.close()
        entries = _owner_entries_read_only(Path(state_path))
        if entries and not now > float(entries[-1]["time"]):   # addendum 5 (R18): the clock is behind the record
            return _owner_refuse(f"the clock gives {now!r}, not later than owner entry {entries[-1]['id']}'s time "
                                 f"{float(entries[-1]['time'])!r}: the clock is behind the record (the entry would be "
                                 "filed in an earlier day's journal); nothing was written", pending)
        lines: list[dict] = []
        if journal_dir.is_dir():                          # (the Journal makes its folder; only one that is there is read)
            lines = Journal(journal_dir).read()           # R18: a line that is not whole is skipped, as a run skips it;
        differences = owner_entry_differences(entries, lines, venue=venue)
        if differences:
            return _owner_refuse(f"the state file and the journal differ on the owner's entries: "
                                 f"{_cut('; '.join(differences), 600)}; a person must look; nothing was written",
                                 pending)
        held = sorted(book_figures(fills, orders, entries, {}, day, now=now).unmarked)   # every fund it holds units of
        asked = sorted(set(held) | ({symbol} if kind in ("units_out", "split", "mark") else set()))
        marks, close, why = _owner_marks(asked, quotes, calendar, now)
        if why is not None:
            return _owner_refuse(why, pending)
        prices = {s: m["price"] for s, m in marks.items()}
        valued = entries
        if kind == "split":                               # valued after the split, at the marks after it
            last = entries[-1]["units_after"] if entries else "0"
            valued = entries + [{"id": len(entries) + 1, "time": now, "kind": "split", "symbol": symbol,
                                 "ratio": str(figs["ratio"]), "units_before": last, "units_after": last}]
        figures = book_figures(fills, orders, valued, prices, day, now=now)
        mark = _dec(prices[symbol], f"the mark of {symbol}") if kind in ("units_out", "mark") else None
        fields = next_entry(figures, kind, amount=figs.get("amount"), symbol=symbol, units=figs.get("units"),
                            ratio=figs.get("ratio"), mark=mark)
        head = entries[-1]["hash"] if entries else _GENESIS
        now_record = {"arguments": arguments, "head": head, "ledger": _ledger_digest(fills, orders), "held": held,
                      "close": close, "marks": marks}
        life = ticket_life()
    except (OrderStepError, OrderError, sqlite3.Error, OSError) as exc:
        return _owner_refuse(f"{_cut(str(exc), 600)}", pending)
    if pending is None:
        return _owner_preview(venue, preview_path, now, life, now_record, fields, figures, quotes)
    moved = _preview_differences(pending[1], now_record)
    if moved:
        return _owner_refuse(f"the confirm differs from its preview: {_cut('; '.join(moved), 600)}; nothing was "
                             "written", pending)
    lock = _RunLock(str(state_path))                      # only an entry that will be written takes the lock
    if not lock.acquire():
        return _owner_refuse("an order-step run holds this venue's state file now; write the entry once it has ended; "
                             "nothing was written", pending)
    state = None
    try:
        spent = _spend_preview(*pending)                  # used once: spent before the entry is written
        if not spent.startswith("; the preview is spent"):
            return _owner_refuse(f"the pending preview could not be taken{spent}; nothing was written")
        # addendum 1 (R35): judged again under the lock, as the last read before the write. Every writer of a fill
        # or an order row (the order step's runs, practice and live, and the event practice) writes them only while
        # holding this same lock (<state file>.lock), and so does every writer of an owner's entry; nothing the
        # figures read can arrive between this read and the write below.
        arrived = _owner_arrived(ledger_path, Path(state_path), day, now, now_record, ledger_was_there)
        if arrived is not None:
            return _owner_refuse(f"{arrived}; nothing was written{spent}")
        try:
            state = OrderStepState(state_path, clock=clock)
            row = state.add_owner_entry(fields, who=args.who, note=args.note, now=now, after=head)
        except (OrderStepError, OrderError, sqlite3.Error) as exc:
            return _owner_refuse(f"{_cut(str(exc), 600)}; the preview is spent")
        finally:
            if state is not None:
                state.close()
        try:                                              # R12: the witness outside the file, by the journal's writer
            Journal(journal_dir).write(row["day"], _owner_journal_line(venue, row))
            _sync_file(journal_dir / f"{row['day']}.jsonl")
        except (OSError, ValueError, TypeError) as exc:
            print(f"written: owner entry {row['id']}, {kind}, to {state_path.name}, hash {row['hash']}; but its "
                  f"journal line could not be written ({_cut(str(exc), 300)}): the entry stays in the state file, and "
                  "the next owner's entry (and, from S4, every run) finds the state file and the journal differ "
                  f"until the line is written: `order-step --venue {venue} --owner-entry-rejournal {row['id']} "
                  f"--hash {row['hash']} --who WHO --note NOTE` writes it, and only it", file=sys.stderr)
            return JOURNAL_NOT_WRITTEN
    finally:
        lock.release()
    print(f"written: owner entry {row['id']}, {kind}, to {state_path.name}, and journaled")
    print(json.dumps(row, indent=2))
    return 0


def _owner_preview(venue: str, path: Path, now: float, life: float, now_record: dict, fields: dict,
                   figures: BookFigures, quotes) -> int:
    """T51c S4b: print the preview and write it as the one pending preview of `venue`. Nothing else is written."""
    code = ticket_number()
    record = {"what": OWNER_PREVIEW_KIND, "venue": venue, "code": code, "created": now, "expires": now + life,
              **now_record, "entry": fields}
    try:
        _write_private(path, json.dumps(record, sort_keys=True))
    except OSError as exc:
        return _owner_refuse(f"the preview could not be written ({_cut(_local_error(exc), 200)}); nothing was written")
    kind, a = fields["kind"], now_record["arguments"]
    source = getattr(quotes, "__name__", None) or "the quote source"
    close = now_record["close"]
    basis = (f"outside the session: the quote usable at the last session's close, {_iso(close)}" if close is not None
             else "inside the session: at most "
                  f"{QUOTE_MAX_AGE_SECONDS // 60} minutes old")
    print(f"preview: owner entry, {kind} {_figure_text(kind, a)}, for {venue}; NOTHING was written")
    for sym, m in sorted(now_record["marks"].items()):
        print(f"  mark {sym}: {m['price']!r} at {_iso(m['time'])} (source: {source}, the order step's own; {basis})")
    if not now_record["marks"]:
        print("  marks: none (the book holds no units, so no quote was asked)")
    high = "no upper bound" if figures.value_high is None else str(figures.value_high)
    print(f"  the book's value: {figures.value} (from {figures.value} to {high}"
          + (f"; bounded by: {_cut('; '.join(figures.bounded_by), 300)}" if figures.bounded_by else "") + ")")
    if kind == "mark":                                    # T51c S4e (R34): said plainly, beside the mark it records
        print(f"  this takes the quote above as {a['symbol']}'s new mark, so the marks stop clears for it on the next run "
              "(a quote then further than max_drawdown_pct from it stops the run again); no cash and no units change")
    print(f"  units outstanding: {fields['units_before']} before, {fields['units_after']} after")
    print(f"  value per unit: {fields['value_per_unit_before'] or 'none (no units outstanding)'} before, "
          f"{fields['value_per_unit_after'] or 'none'} after")
    print(f"to write it, run the same command again with --confirm {code} by {_iso(record['expires'])}; it asks the "
          f"quotes again and writes only if every mark is within {TICKET_QUOTE_MOVE:.1%} and nothing else changed")
    print(f"code: {code}")
    return 0


def _owner_rejournal(args, paths, venue: str, clock: Callable[[], float] | None) -> int:
    """T51c S4b (R29): `order-step --venue NAME --owner-entry-rejournal N --hash H --who WHO --note NOTE`, the repair
    of an entry whose journal line could not be written (exit JOURNAL_NOT_WRITTEN). It writes entry N's journal line
    exactly as the entry's own command writes it (_owner_journal_line, into the journal of the entry's day), with one
    field more, `rejournaled` ({who, note, time}: who repaired it, why, when); the journal's writer adds its own
    timestamp as for every line. Only when ALL hold, judged before the lock and again under it: the chain of the
    state file checks; owner_entry_differences gives exactly "owner entry N is in the state file and not in the
    journal" and nothing else; N is the LAST entry; H is entry N's hash. Anything else is refused in words, nothing
    written. A changed or reordered entry is never made good: it is another difference, or a broken chain."""
    import sys
    number, given = getattr(args, "owner_entry_rejournal", None), getattr(args, "hash", None)
    if (getattr(args, "owner_entry", None) is not None or getattr(args, "confirm", None) is not None
            or any(getattr(args, n, None) is not None for n in _OWNER_ARGUMENTS)):
        return _owner_refuse("--owner-entry-rejournal takes only --venue, --hash, --who and --note: it writes a "
                             "missing journal line, never an entry; nothing was written")
    if not _plain(_ENTRY_NUMBER, number):
        return _owner_refuse(f"--owner-entry-rejournal takes the entry's number, not {_cut(repr(number))}; nothing "
                             "was written")
    if not _plain(_ENTRY_HASH, given):
        return _owner_refuse("--hash takes the entry's hash, the 64 hexadecimal digits the failed command printed, not "
                             f"{_cut(repr(given))}; nothing was written")
    n = int(number)
    state_path = state_path_for(ledger_path_for(paths.memory, venue), venue)
    journal_dir = Path(paths.journal)
    reading = (clock if clock is not None else wall_clock)()   # read once: the repair's own time
    if _ny_day(reading) is None:
        return _owner_refuse("the clock gives no New York day; nothing was written")
    from valedia_trader.research_loop.paper import Journal

    def judged() -> tuple[dict | None, str | None]:
        try:
            entries = _owner_entries_read_only(Path(state_path))   # the chain checks, or this refuses
            lines = Journal(journal_dir).read() if journal_dir.is_dir() else []
            differences = owner_entry_differences(entries, lines, venue=venue)
        except (OrderStepError, sqlite3.Error, OSError) as exc:
            return None, f"{_cut(str(exc), 600)}; nothing was written"
        missing = f"owner entry {n} is in the state file and not in the journal"
        if not entries or int(entries[-1]["id"]) != n:
            return None, (f"only the LAST entry can be rejournaled, and owner entry {n} is not it (the state file holds "
                          f"{len(entries)}); nothing was written")
        if differences != [missing]:
            return None, ("the repair writes a missing journal line only when that is the ONE difference between the "
                          f"state file and the journal; they differ in: {_cut('; '.join(differences) or 'nothing', 600)}"
                          "; a person must look; nothing was written")
        if not hmac.compare_digest(str(entries[-1]["hash"]).encode(), given.encode()):
            return None, f"that hash is not owner entry {n}'s; nothing was written"
        return entries[-1], None
    row, why = judged()
    if why is None:
        lock = _RunLock(str(state_path))
        if not lock.acquire():
            why = "an order-step run holds this venue's state file now; repair once it has ended; nothing was written"
        else:
            try:
                row, why = judged()                       # again under the lock: nothing moved meanwhile
                if why is None:
                    line = {**_owner_journal_line(venue, row),
                            "rejournaled": {"who": args.who, "note": args.note, "time": repr(float(reading))}}
                    try:
                        Journal(journal_dir).write(row["day"], line)
                        _sync_file(journal_dir / f"{row['day']}.jsonl")
                    except (OSError, ValueError, TypeError) as exc:
                        print(f"the journal line of owner entry {n} could not be written ({_cut(str(exc), 300)}); the "
                              "state file is as it was; the repair may be run again", file=sys.stderr)
                        return JOURNAL_NOT_WRITTEN
            finally:
                lock.release()
    if why is not None:
        return _owner_refuse(why)
    print(f"rejournaled: owner entry {n}, {row['kind']}, to the journal of {row['day']}; the state file and the "
          "journal now hold the same entries")
    return 0


_ACCEPT_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _accept_refuse(words: str) -> int:
    import sys
    print(f"refused: {words}; nothing was written", file=sys.stderr)
    return 2


def _nav_rows_read_only(path: Path) -> list[dict]:
    """The `book_nav` rows of a state file, through a read-only open that changes nothing (the owner's entries are
    checked first, so a file of another layout or a broken chain is refused in words)."""
    _owner_entries_read_only(path)
    if not path.exists():
        return []
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'book_nav'").fetchone():
            return []
        return _nav_rows_of(db)
    finally:
        db.close()


def _accept_judge(state_path: Path, lines, venue: str, day: str, today: str) -> tuple[dict | None, str | None]:
    """T51c S4d: what accepting the state file's latest `book_nav` row of `day` as the day's figure would write, or why not.
    AN ACCEPTANCE MAY ONLY TIGHTEN, NEVER LOOSEN: the row's lower figure and its upper figure (the day stop's base; the lower
    where the upper is not known) must both be at least the journal's last figures of the day (an earlier acceptance of the
    day is the journal's last figure). A row lower on either is refused: accepting it would loosen the day stop and the
    drawdown, and the command cannot tell a real fall between a summary and a lost one from a copy put back, so a lower row
    has no route here (the real state file is put back, or the lead decides by hand). The day must have a look in the
    journal, the state file must have a row of it, the two must differ, and, as an extra refusal that the safety does not
    depend on, the row's run (its batch) must have no summary in the journal at all (a lost summary's row)."""
    owner_entries = _owner_entries_read_only(state_path)
    looks, last_hash, batches = _scan_shared_looks(lines, venue)
    rows = _nav_rows_read_only(state_path)
    found = _nav_history(rows, looks, today, owner_entries)
    mine = [look for look in looks if look["day"] == day]
    if not mine and day not in found["unheld"]:
        return None, (f"the journal holds no shared summary with a value per unit for {day} (an acceptance is of a day the "
                      "journal holds, or of a day with a row that the journal has no summary for, after the last day it "
                      "does)")
    if day in found["lacking"]:
        return None, (f"the state file has no book_nav row for {day}: a row that is not there cannot be accepted (a state "
                      "file put back is cured only by the real state file)")
    if day in found["unheld"]:                            # a row with no summary: held against the last day's figures
        base = found["base"]
        if base is None:
            return None, (f"there is no figure (no earlier journaled day, no owner entry's value per unit) to hold the row of "
                          f"{day} against; the real state file is needed (or the lead, by hand)")
        latest_row = [r for r in rows if r["day"] == day][-1]
        low, high = _row_figures(latest_row)
        change = {"look": base, "now": {"low": low, "high": high, "row": latest_row}, "unheld": True}
    elif day not in found["changed"]:
        return None, f"nothing differs for {day}: the state file's latest row of the day reads what the journal's last summary says"
    else:
        change = found["changed"][day]
    look, now = change["look"], change["now"]
    row = now["row"]
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        jbasis = look["high"] if look["high"] is not None else look["low"]
        rbasis = now["high"] if now["high"] is not None else now["low"]
        lower = now["low"] < look["low"] or rbasis < jbasis
    if lower:
        return None, (f"the row is lower than the journal's figure for {day} (the row reads {_figures_said(now['low'], now['high'])}"
                      f", the journal's last figure {_figures_said(look['low'], look['high'], look['has_high'])}); accepting it would "
                      "loosen the day stop and the drawdown; put the real state file back, or ask the lead (a lower row, a real "
                      "fall between a summary and a lost one or a copy, has no command route)")
    if row["batch"] in batches:
        return None, (f"the state file's latest row of {day} (batch {_cut(str(row['batch']))}) belongs to a run whose summary the "
                      "journal holds: an acceptance is only of the row of a run the journal has no summary of; put the real "
                      "state file back")
    figures = {"row_id": int(row["id"]), "row_batch": str(row["batch"]), "value_per_unit": str(now["low"]),
               "value_per_unit_high": None if now["high"] is None else str(now["high"]),
               "journal_had": {"value_per_unit": str(look["low"]),
                               "value_per_unit_high": None if look["high"] is None else str(look["high"])}}
    others = {d: {"journal": [str(c["look"]["low"]), None if c["look"]["high"] is None else str(c["look"]["high"])],
                  "row": [str(c["now"]["low"]), None if c["now"]["high"] is None else str(c["now"]["high"]),
                          int(c["now"]["row"]["id"]), str(c["now"]["row"]["batch"])]}
              for d, c in sorted(found["changed"].items()) if d != day}
    against = None
    if change.get("unheld"):
        against = ((f"the owner's acceptance of {look['day']}" if look.get("accepted") else
                    f"the journal's last summary of {look['day']}") if "day" in look and "entry" not in look else
                   f"the value per unit after {look['entry']}, which is what the day stop falls back to when no row of an "
                   "earlier day gives one")
    return {"day": day, "figures": figures, "last_hash": last_hash, "others": others, "lacking": list(found["lacking"]),
            "unheld": [d for d in found["unheld"] if d != day], "look": look, "now": now, "against": against}, None


def _accept_code(venue: str, info: dict) -> str:
    """The six digits that bind a confirm to the preview it came from: a digest of everything the preview shows (the venue,
    the day, the row and its figures, the journal's figures, the chain's last hash, and every other day that differs or
    lacks a row, with its figures). Run again, the command recomputes it from the files as they are: a code that does not
    match means something changed, on any listed day, or the code is wrong."""
    text = json.dumps({"venue": venue, "day": info["day"], "figures": info["figures"], "last_hash": info["last_hash"],
                       "others": info["others"], "lacking": info["lacking"], "unheld": info["unheld"]},
                      sort_keys=True, separators=(",", ":"))
    return f"{int(hashlib.sha256(text.encode()).hexdigest(), 16) % 10 ** 6:06d}"


def accept_history_command(args, paths, clock: Callable[[], float] | None = None) -> int:
    """T51c S4d (repair): `order-step --live --accept-history DAY --venue webull-production --who WHO --note NOTE`, and the
    same with `--confirm CODE`: the owner's way past a live run that stops at `state` because the state file's latest
    `book_nav` row of DAY differs from the journal's last figures of that day. The command does not tell WHY it differs
    (a summary that never reached the journal, an older state file put back, a row changed) and does not claim to; it only
    ever takes a row that TIGHTENS the day stop and the drawdown: the row's lower and upper figures, as `book_loss` reads
    them (value_low and value_high over units_outstanding, never the text column), must both be at least the journal's
    last figures of the day. Run without --confirm it PRINTS what differs (the journal's last figures of the day, the state
    file's latest row and its run, every other day that still differs) and a six-digit code covering all of it, and writes
    nothing. Run again with the code it writes ONE journal line (`history_accepted`: the day, the row, its figures, what the
    journal had, who and why, and a hash over all of them chained to the acceptance before it) and nothing else: the state
    file is never written, and after it the history check takes the row as that day's figure and still stops on any other
    difference. It refuses, writing nothing, when nothing differs, for a day the journal holds no figure of, for a day with
    no row, for a row lower on either figure (no command route: _accept_judge), for a row of a run whose summary the
    journal holds, and for a state file whose rows cannot be read as `book_loss` reads them (_row_figures). The venue must
    be a production venue's name and `--live` must be given, as for the owner's entries. Exit 0: a preview printed or the
    line written; 2: refused."""
    import sys
    day, confirm = getattr(args, "accept_history", None), getattr(args, "confirm", None)
    venue = getattr(args, "venue", None)
    if (getattr(args, "propose", False) or getattr(args, "approve", None) is not None or getattr(args, "check", False)
            or getattr(args, "capital", None) is not None or getattr(args, "fractional", False)
            or getattr(args, "shared", False) or getattr(args, "owner_entry", None) is not None
            or getattr(args, "owner_entry_rejournal", None) is not None or getattr(args, "hash", None) is not None
            or getattr(args, "mark", None) is not None
            or any(getattr(args, n, None) is not None for n in _OWNER_ARGUMENTS)):
        return _accept_refuse("--accept-history is run on its own, with --live, --venue, --who, --note and (to write) "
                              "--confirm, and no other flag")
    if not isinstance(venue, str) or not venue or _safe_name(venue) != venue or venue.strip(".") == "":
        return _accept_refuse("--accept-history needs --venue, the plain name of the live venue")
    if not (getattr(args, "live", False) and _production_venue_name(venue)):
        return _accept_refuse("--accept-history is for the live venue's book: --live with a production venue's name (it "
                              f"starts {_PRODUCTION_PREFIX!r}), not {_cut(repr(venue))}")
    if not (isinstance(day, str) and _ACCEPT_DAY.fullmatch(day)):
        return _accept_refuse(f"--accept-history takes a date, YYYY-MM-DD, not {_cut(repr(day))}")
    try:
        date.fromisoformat(day)
    except ValueError:
        return _accept_refuse(f"--accept-history takes a real date, not {_cut(repr(day))}")
    for what in ("who", "note"):
        problem = _owner_text_problem(what, getattr(args, what, None))
        if problem is not None:
            return _accept_refuse(f"the acceptance needs --{problem}")
    if confirm is not None and not _plain(_NUMBER, confirm):
        return _accept_refuse(f"--confirm takes the six-digit code the preview printed, not {_cut(repr(confirm))}")
    reading = (clock if clock is not None else wall_clock)()
    today = _ny_day(reading)
    if today is None:
        return _accept_refuse("the clock gives no New York day")
    from valedia_trader.research_loop.paper import Journal
    state_path = state_path_for(ledger_path_for(paths.memory, venue), venue)
    journal_dir = Path(paths.journal)

    def judged() -> tuple[dict | None, str | None]:
        try:
            lines = Journal(journal_dir).read() if journal_dir.is_dir() else []
            return _accept_judge(state_path, lines, venue, day, today)
        except (OrderStepError, sqlite3.Error, OSError, DecimalException) as exc:
            return None, _cut(str(exc), 600)
    info, why = judged()
    if why is not None:
        return _accept_refuse(why)
    code = _accept_code(venue, info)
    if confirm is None:
        f, had = info["figures"], info["figures"]["journal_had"]
        print(f"preview: accept the state file's latest book_nav row of {day} as the day's figure, for {venue}; NOTHING was written")
        if info["against"] is None:
            source = (f"the owner's acceptance of {day} (the journal's last figure of the day)" if info["look"].get("accepted")
                      else f"the journal's last summary of {day}")
            print(f"  {source} says: value per unit {had['value_per_unit']} (upper "
                  f"{had['value_per_unit_high'] if info['look']['has_high'] else 'not journaled'})")
        else:
            print(f"  the journal holds no summary of {day}; the row is held against {info['against']}: value per unit "
                  f"{had['value_per_unit']} (upper {had['value_per_unit_high'] if info['look']['has_high'] else 'not journaled'})")
        print(f"  the state file's latest row of {day} (row {f['row_id']}, batch {f['row_batch']}) says: value per unit "
              f"{f['value_per_unit']} (upper {f['value_per_unit_high'] if f['value_per_unit_high'] is not None else 'not known'})")
        print(f"  the row is not below the journal's figure on either bound, and its run has no summary in the journal; this "
              "command does not tell a summary that never reached the journal from anything else that makes the row differ: it "
              "only ever takes a row that tightens the day stop and the drawdown")
        if info["others"] or info["lacking"] or info["unheld"]:
            listed = [*(f"{d} (row {c['row'][2]}, batch {c['row'][3]}: {c['row'][0]}; the journal {c['journal'][0]})"
                        for d, c in info["others"].items()), *(f"{d} (no row)" for d in info["lacking"]),
                      *(f"{d} (a row, and no summary in the journal)" for d in info["unheld"])]
            print(f"  still differing, and still stopping the run after this (the code covers them): {'; '.join(listed[:10])}")
        print(f"to write it, run the same command again with --confirm {code}; it writes one journal line and nothing else")
        print(f"code: {code}")
        return 0
    lock = _RunLock(str(state_path))
    if not lock.acquire():
        return _accept_refuse("an order-step run holds this venue's state file now; accept once it has ended")
    try:
        info, why = judged()                              # again under the lock: nothing moved meanwhile
        if why is not None:
            return _accept_refuse(why)
        if not hmac.compare_digest(code.encode(), confirm.encode()):
            return _accept_refuse("that code is not this preview's (it is wrong, or the state file or the journal changed "
                                  "since the preview): run the command without --confirm to see what differs now")
        record = {"type": "order_step", "venue": venue, "event": "history_accepted", "accepted_day": day,
                  **info["figures"], "who": args.who, "note": args.note, "prev_hash": info["last_hash"]}
        record["hash"] = _accepted_hash(record)
        try:
            Journal(journal_dir).write(today, record)
            _sync_file(journal_dir / f"{today}.jsonl")
        except (OSError, ValueError, TypeError) as exc:
            return _accept_refuse(f"the journal line could not be written ({_cut(str(exc), 300)})")
    finally:
        lock.release()
    print(f"accepted: the state file's latest book_nav row of {day} (batch {info['figures']['row_batch']}) is the day's "
          f"figure, journaled with hash {record['hash']}; every other difference still stops a live run")
    return 0


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


@dataclass(frozen=True)
class SplitLeg:
    """One order of a split: the `whole` leg (a LIMIT order of whole shares) or the `remainder` leg (a MARKET order
    by quantity of less than one share). `units` is signed: above zero buys, below zero sells."""
    leg: str
    order_type: str
    units: Decimal


@dataclass(frozen=True)
class Split:
    """What the split makes of one fund's difference, exact in Decimal.

    `difference`: the difference rounded toward zero to FRACTIONAL_DECIMALS decimals, a sale capped at the ledger's
    own holding (the units today's order step would send). `legs`: none, the whole leg, the remainder leg, or both
    in that order, all on one side. `residual`: `difference` less what the legs send, so the legs and the residual
    always sum to `difference` exactly; it is a remainder held back (worth less than the minimum with its margin),
    or, when the target was taken as zero, the part of the difference that zeroing changed. `residual_value`: its
    size at the judged quote. `target_zeroed`: the target, of less than one share, was worth less than
    FRACTIONAL_MIN_VALUE and was taken as zero. `why`: words for a residual that is not zero, else None."""
    difference: Decimal
    legs: tuple
    residual: Decimal
    residual_value: Decimal
    target_zeroed: bool
    why: str | None


def _split_number(x, name: str, low: float, high: float, low_ok: bool) -> Decimal:
    """`x` as an exact Decimal (of its float's shortest text, as `round_units` reads it), or OrderStepError in words
    naming it when it is not a plain finite number in the range."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise OrderStepError(f"the split's {name} must be a number, not {type(x).__name__}; nothing is split")
    try:
        v = float(x)
    except (OverflowError, ValueError):
        v = math.nan
    if not math.isfinite(v):
        raise OrderStepError(f"the split's {name} must be a finite number, not {x!r}; nothing is split")
    if not ((v >= low) if low_ok else (v > low)) or v > high:
        raise OrderStepError(f"the split's {name} {v!r} is outside {'from' if low_ok else 'above'} {low:g} to "
                             f"{high:g}; nothing is split")
    return Decimal(repr(v))


# ---- CF-C: the settled-quantity sale cap (pure; the run's _sale_cap uses it)

def lot_trade_day(fill: dict, order: dict | None) -> str | None:
    """CF-C: the New York trade date (YYYY-MM-DD) of the lot a purchase fill row opened, or None when it cannot be
    established. Every order the order step sends is a DAY order for the regular session, so it can execute only on
    the New York day it was sent (`sent_at`); that day is the fill's own trade date, whenever a reconcile saw the fill
    (the fill row's `time` is the moment the ledger recorded it, never the trade date: a 15:58 fill first seen at the
    next morning's reconcile is still of the day before). None, so the lot counts as unsettled (fail closed), when the
    order is not known, its `sent_at` is not a finite time, or the fill row's time is not a finite time at or after
    it."""
    if not isinstance(order, dict):
        return None
    sent, seen = _as_float(order.get("sent_at")), _as_float(fill.get("time"))
    if sent is None or seen is None or seen < sent:
        return None
    return _ny_day(sent)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Monday 0) of the month; n = -1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_settlement_holiday(day) -> bool:
    """CF-C2: is `day` (a date or YYYY-MM-DD) a US settlement holiday? Source: the Federal Reserve's holiday schedule
    (the Fed's 11 holidays); DTCC follows it for US equities, so on such a day nothing settles even when NYSE trades
    (Columbus Day, Veterans Day). A fixed-date holiday on a Sunday is observed the following Monday; one on a Saturday
    is NOT moved: the Friday before is a settlement day (the Fed is open; unlike pandas' nearest_workday). Pure."""
    d = pd.Timestamp(day).date()
    y = d.year
    fixed = [date(y, 1, 1), date(y, 6, 19), date(y, 7, 4), date(y, 11, 11), date(y, 12, 25)]
    observed = {f + timedelta(days=1) if f.weekday() == 6 else f for f in fixed}
    observed |= {_nth_weekday(y, 1, 0, 3),     # Martin Luther King Jr. Day: 3rd Monday of January
                 _nth_weekday(y, 2, 0, 3),     # Washington's Birthday: 3rd Monday of February
                 _nth_weekday(y, 5, 0, -1),    # Memorial Day: last Monday of May
                 _nth_weekday(y, 9, 0, 1),     # Labor Day: 1st Monday of September
                 _nth_weekday(y, 10, 0, 2),    # Columbus Day: 2nd Monday of October
                 _nth_weekday(y, 11, 3, 4)}    # Thanksgiving: 4th Thursday of November
    return d in observed


def settlement_day(calendar, trade_day: str) -> str | None:
    """CF-C: the day a lot traded on `trade_day` settles: T+1, the first day after the trade date that is both an
    exchange session and a settlement day (CF-C2: a session on the exchange's calendar that is not a US settlement
    holiday, us_settlement_holiday; a half day is a session, so it settles; an exchange holiday, or a Columbus or
    Veterans Day the exchange trades on, is not). None when `trade_day` is not a session, or the calendar cannot
    answer (the lot then counts as unsettled)."""
    try:
        day = pd.Timestamp(trade_day)
        if not calendar.is_session(str(day.date())):
            return None
        for k in range(1, SETTLEMENT_SEARCH_DAYS + 1):
            nxt = str((day + pd.Timedelta(days=k)).date())
            if calendar.is_session(nxt) and not us_settlement_holiday(nxt):
                return nxt
    except Exception:
        return None
    return None


def book_settled_units(fills, orders, entries, calendar, day: str) -> dict[str, float]:
    """CF-C: per symbol, the units of the book's OWN lots that have settled on the New York day `day`: every purchase
    fill row whose lot's settlement day (settlement_day of lot_trade_day: the first day after the trade date that is
    both an exchange session and a settlement day) is on or before `day`, less every sale fill
    row and every owner's `units_out` in full, each `split` multiplying what came before it, walked in the book's own
    order (book_figures: by time, a fill before an entry of the same time, then by id). A sale is taken from the
    settled lots first, so the figure is the least the settled holding can be; it is then held between 0 and the
    book's units. Only this ledger's fills and the owner's entries count: units the owner holds by hand at the
    account are never in it. A lot whose trade date or settlement cannot be established is unsettled. Pure."""
    by_tag = {o["tag"]: o for o in orders}
    settles: dict[str, str | None] = {}
    steps = [((_dec(f["time"], "a fill's time"), 0, int(f.get("id") or 0)), "fill", f) for f in fills]
    steps += [((_dec(e["time"], "an entry's time"), 1, int(e["id"])), "entry", e) for e in entries]
    settled: dict[str, Decimal] = {}
    held: dict[str, Decimal] = {}
    with localcontext() as ctx:
        ctx.prec = _BOOK_PREC
        for _, what, x in sorted(steps, key=lambda s: s[0]):
            sym = x["symbol"] if what == "fill" or x.get("kind") in ("units_out", "split") else None
            if sym is None:
                continue
            s, h = settled.get(sym, Decimal(0)), held.get(sym, Decimal(0))
            if what == "fill":
                u = _dec(x["units"], f"the units of fill row {x.get('id')}")
                h += u
                if u < 0:
                    s += u
                else:
                    trade = lot_trade_day(x, by_tag.get(x["tag"]))
                    if trade is not None and trade not in settles:
                        settles[trade] = settlement_day(calendar, trade)
                    on = settles.get(trade) if trade is not None else None
                    if on is not None and on <= day:
                        s += u
            elif x["kind"] == "units_out":
                out = _dec(x["units"], f"entry {x['id']}'s units")
                s, h = s - out, h - out
            else:
                ratio = _dec(x["ratio"], f"entry {x['id']}'s ratio")
                s, h = s * ratio, h * ratio
            settled[sym], held[sym] = s, h
    return {sym: float(max(Decimal(0), min(settled[sym], max(held[sym], Decimal(0))))) for sym in settled}


def split_difference(difference, quote, side: str, held, cap=None) -> Split:
    """T51 R1: one fund's difference between target and the ledger's own book, in units, split into a whole-share
    LIMIT leg and a MARKET remainder leg by QUANTITY, at the judged `quote`. A pure function: it reads nothing but
    its arguments and the module's constants, and writes nothing.

    `difference` is target less `held` (signed, not zero); `side` is "buy" or "sell" and must agree with its sign;
    `held` is the LEDGER'S OWN holding (never the baseline, never the venue's); `cap` (CF-C, optional) the most a
    sale may sell (the run's settled-quantity sale cap), None for no cap beyond `held`. The rule, exact in Decimal:
      (i)   a sale is capped at `held` (and at `cap` when given), and the difference is rounded toward zero to
            FRACTIONAL_DECIMALS decimals;
      (ii)  a target (held + difference) of LESS THAN ONE SHARE worth less than FRACTIONAL_MIN_VALUE at the quote is
            a target of zero: the holding (itself under one share) is sold as far as the minimum allows, never more
            than `cap`; a target of
            one share or more is never changed by this rule (T51 addendum 3), so a purchase never yields a sale leg
            and a sale never a purchase leg;
      (iii) a = |rounded difference|, W = floor(a), r = a - W;
      (iv)  a LIMIT leg of W when W >= 1; a MARKET leg of r when r > 0 and r x quote x (1 - TICKET_QUOTE_MOVE) is at
            least FRACTIONAL_MIN_VALUE (a margin so a move the ticket allows still clears the minimum); otherwise r
            is held back as the residual (never a fault).
    Refuses in words (OrderStepError) a difference, quote or holding that is not a plain finite number, a
    difference of zero or above MAX_UNITS in size, a quote not above zero or above MAX_QUOTE_PRICE, a holding below
    zero or above MAX_UNITS, a cap that is not None or a plain finite number from 0 to MAX_UNITS, and a side that is
    not "buy" or "sell" or disagrees with the difference."""
    with localcontext() as ctx:
        ctx.prec = 100                                     # every product below is exact
        d = _split_number(difference, "difference", -MAX_UNITS, MAX_UNITS, True)
        q = _split_number(quote, "quote", 0.0, MAX_QUOTE_PRICE, False)
        h = _split_number(held, "held (the ledger's own holding)", 0.0, MAX_UNITS, True)
        limit = h if cap is None else min(h, _split_number(cap, "cap (the sale cap)", 0.0, MAX_UNITS, True))
        if d == 0:
            raise OrderStepError("the split's difference is zero: there is nothing to split")
        if side not in ("buy", "sell") or type(side) is not str:
            raise OrderStepError(f"the split's side must be 'buy' or 'sell', not {side!r}; nothing is split")
        if (side == "buy") != (d > 0):
            raise OrderStepError(f"the split's side {side!r} disagrees with the difference {d} "
                                 f"(a {'purchase' if d > 0 else 'sale'}); nothing is split")
        step = Decimal(1).scaleb(-FRACTIONAL_DECIMALS)
        rounded = max(d, -limit).quantize(step, rounding=ROUND_DOWN)
        why = []
        target = h + d
        target_value = target * q
        zeroed = target < 1 and target_value < FRACTIONAL_MIN_VALUE   # T51 addendum 3: one share or more never zeroed
        effective = rounded
        if zeroed:
            effective = (-limit).quantize(step, rounding=ROUND_DOWN)
            why.append(f"the target of less than one share is worth {target_value.quantize(CENT, rounding=ROUND_DOWN)}, "
                       f"less than the minimum of {FRACTIONAL_MIN_VALUE}, and is taken as zero")
        size = abs(effective)
        sign = 1 if effective > 0 else -1
        whole = size.to_integral_value(rounding=ROUND_FLOOR)
        rest = size - whole
        legs = []
        if whole >= 1:
            legs.append(SplitLeg("whole", "LIMIT", sign * whole))
        if rest > 0:
            margin = 1 - Decimal(repr(float(TICKET_QUOTE_MOVE)))
            if rest * q * margin >= FRACTIONAL_MIN_VALUE:
                legs.append(SplitLeg("remainder", "MARKET", sign * rest))
            else:
                why.append(f"the remainder of {rest} units is worth {(rest * q).quantize(CENT, rounding=ROUND_DOWN)} "
                           f"at the quote, less than the minimum of {FRACTIONAL_MIN_VALUE} with its margin of "
                           f"{TICKET_QUOTE_MOVE:g}; it is not sent")
        sent = sum((leg.units for leg in legs), Decimal(0))
        residual = rounded - sent
        return Split(difference=rounded, legs=tuple(legs), residual=residual, residual_value=abs(residual) * q,
                     target_zeroed=zeroed, why="; ".join(why) if residual != 0 else None)


def _leg_of(c: dict) -> dict:
    """T51 R4: a fractional run's candidate (or ticket line) names its leg; a whole-share run's never does (ruling A)."""
    return {"leg": c["leg"]} if "leg" in c else {}


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
    return _window(calendar, now, SESSION_AFTER_OPEN, SESSION_BEFORE_CLOSE)


def market_leg_window(calendar, now: float) -> tuple[float | None, float | None, str | None]:
    """T51 R5 (B4): the window a MARKET leg is sent in, read as `market_window` reads the day (a half-day's close as
    the calendar has it): MARKET_AFTER_OPEN after the open to MARKET_BEFORE_CLOSE before the close."""
    return _window(calendar, now, MARKET_AFTER_OPEN, MARKET_BEFORE_CLOSE)


def _window(calendar, now: float, after, before) -> tuple[float | None, float | None, str | None]:
    ny = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
    day = str(ny.date())
    try:
        if not calendar.is_session(day):
            return None, None, f"{day} ({ny.day_name()}) is not a trading day"
        start = pd.Timestamp(calendar.session_open(day)) + after
        end = pd.Timestamp(calendar.session_close(day)) - before
    except Exception as exc:
        return None, None, f"the exchange calendar could not answer for {day}: {_describe(exc)}"
    return start.timestamp(), end.timestamp(), None


def market_leg_clears_minimum(units, quote) -> bool:
    """T51 R5 (B6): whether a MARKET leg of `units` (either side) still clears the split's minimum at `quote`, by the
    split's own rule (R1 iv): |units| x quote x (1 - TICKET_QUOTE_MOVE) at least FRACTIONAL_MIN_VALUE, exact in
    Decimal. False for anything that is not a finite number above zero."""
    if not (_finite(units) and _finite(quote)) or units == 0 or quote <= 0:
        return False
    with localcontext() as ctx:
        ctx.prec = 100
        margin = 1 - Decimal(repr(float(TICKET_QUOTE_MOVE)))
        return abs(Decimal(repr(float(units)))) * Decimal(repr(float(quote))) * margin >= FRACTIONAL_MIN_VALUE


PRACTICE_VENUE = "webull-sandbox"                 # T51 B1: the practice record's venue (go_live.PAPER_VENUE, held equal by a test)
PROOF_PURCHASE = "a MARKET remainder PURCHASE that filled and reconciled cleanly"
PROOF_SALE = "a MARKET remainder SALE that filled and reconciled cleanly"


@dataclass(frozen=True)
class PracticeProof:
    """T51 B1: what a ledger and journal hold of MARKET remainder legs that filled and reconciled cleanly (see
    `clean_remainder_fills`). `unreadable`: why the record could not be read at all, else None."""
    purchases: int = 0
    sales: int = 0
    unreadable: str | None = None

    @property
    def ok(self) -> bool:
        """Both items: at least one purchase and at least one sale (B1)."""
        return self.unreadable is None and self.purchases >= 1 and self.sales >= 1

    @property
    def missing(self) -> list[str]:
        return [] if self.unreadable is not None else (
            ([PROOF_PURCHASE] if self.purchases < 1 else []) + ([PROOF_SALE] if self.sales < 1 else []))

    def words(self) -> str:
        if self.unreadable is not None:
            return f"the record cannot be used: {self.unreadable}"
        if self.ok:
            return (f"the record holds {self.purchases} MARKET remainder purchase(s) and {self.sales} sale(s) that filled "
                    "and reconciled cleanly: both are there")
        return "the record lacks " + " and ".join(self.missing)


def _shortfall_in(summary: dict) -> bool:
    """A shared run's summary whose last reconcile found a shortfall (as the go-live gate reads it): not clean."""
    shared = summary.get("shared")
    return False if shared is None else (True if not isinstance(shared, dict) else bool(shared.get("shortfalls")))


def clean_remainder_fills(ledger_path, journal_dir, venue_name: str) -> PracticeProof:
    """T51 B1, read-only: the MARKET remainder legs of `venue_name`'s own ledger and journal that filled and reconciled
    cleanly, counted by side. A leg counts only when ALL of these hold:
      - the ledger is layout 4 (a layout-3 file was written by an older code path and counts nothing) and holds the
        order as filled, MARKET, with a collar price and no limit price, under one unit, and every fill row of it
        applied, their units adding up to the order's;
      - the journal holds an `order` line of this venue with that tag, `leg` remainder and `order_type` MARKET, and a
        `fill` line with that tag (so this code path wrote it);
      - every batch involved (the order's, and each that journaled a fill of it) has a run summary that ended `done`
        with no fault, and no shortfall;
      - "reconciled cleanly": the first reconcile on the ledger AFTER the one that wrote the order's last fill row
        exists, read its positions, and shows no position difference and no shortfall for the fund and no anomaly or
        unresolved entry naming the order's tag.
    Nothing is written, upgraded or created; a record that cannot be read at all is `unreadable`, in words."""
    try:
        return _clean_remainder_fills(ledger_path, journal_dir, venue_name)
    except Exception as exc:                          # reading a record never raises
        return PracticeProof(unreadable=f"it could not be read ({_describe(exc)})")


def _clean_remainder_fills(ledger_path, journal_dir, venue_name: str) -> PracticeProof:
    from valedia_trader.execution_and_risk.orders import open_read_only
    from valedia_trader.research_loop.paper import Journal, JournalDamaged
    try:
        led = open_read_only(ledger_path)
    except OrderError as exc:
        return PracticeProof(unreadable=f"the order ledger cannot be read: {_one_line_words(exc)}")
    try:
        layout = led._db.execute("PRAGMA user_version").fetchone()[0]
        rows = [] if layout != 4 else [o for o in led.orders(("filled",)) if o["order_type"] == "MARKET"
                                       and o["collar_price"] is not None and o["limit_price"] is None
                                       and 0 < abs(o["units"]) < 1]
        fills = {o["tag"]: led.fills(o["tag"]) for o in rows}
        recs = led.reconciles()
    finally:
        led.close()
    folder = Path(journal_dir)
    if folder.is_symlink() or not folder.is_dir():
        return PracticeProof(unreadable="the journal folder is not there")
    try:
        entries = Journal(folder).read_complete()
    except JournalDamaged as exc:
        n = len(getattr(exc, "skipped", []) or [])
        return PracticeProof(unreadable=f"the journal is damaged ({n} line{'s' if n != 1 else ''} cannot be read)")
    mine = [e for e in entries if isinstance(e, dict) and e.get("type") == "order_step" and e.get("venue") == venue_name]
    summaries: dict = {}
    for e in mine:
        if e.get("event") == "summary" and type(e.get("batch")) is str:
            summaries.setdefault(e["batch"], []).append(e)

    def clean(batch) -> bool:
        got = summaries.get(batch)
        return bool(got) and all(e.get("status") == "done" and not e.get("faults") and not _shortfall_in(e) for e in got)
    buys = sells = 0
    for o in rows:
        tag, sym, units = o["tag"], o["symbol"], o["units"]
        line = [e for e in mine if e.get("event") == "order" and e.get("tag") == tag and e.get("leg") == "remainder"
                and e.get("order_type") == "MARKET"]
        fill_lines = [e for e in mine if e.get("event") == "fill" and e.get("tag") == tag]
        rs = fills[tag]
        if not (line and fill_lines and rs) or not all(f["applied"] for f in rs):
            continue
        if abs(math.fsum(f["units"] for f in rs) - units) > max(1e-9, REL_TOL * max(1.0, abs(units))):
            continue
        if not (clean(o["batch"]) and clean(line[0].get("batch")) and all(clean(e.get("batch")) for e in fill_lines)):
            continue
        last = max(f["id"] for f in rs)
        at = [i for i, r in enumerate(recs) if r["summary"] and any(isinstance(f, dict) and f.get("id") == last
                                                                    for f in r["summary"].get("fills") or [])]
        if not at or at[0] + 1 >= len(recs):
            continue                                  # the reconcile that wrote the fill is not "the next one"
        nxt = recs[at[0] + 1]["summary"]
        if not isinstance(nxt, dict) or nxt.get("positions_error"):
            continue
        if sym in (nxt.get("position_differences") or {}) or sym in (nxt.get("position_shortfalls") or {}):
            continue
        if any(isinstance(a, dict) and a.get("tag") == tag for a in (nxt.get("anomalies") or []) + (nxt.get("unresolved") or [])):
            continue
        buys += units > 0
        sells += units < 0
    return PracticeProof(purchases=int(buys), sales=int(sells))


def _one_line_words(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300]


def practice_proof(ledger_path, journal_dir, venue_name: str = PRACTICE_VENUE) -> PracticeProof:
    """T51 B1: the practice record's reading (`clean_remainder_fills` on the practice ledger and journal); `ok` only with
    at least one clean MARKET remainder purchase AND one clean MARKET remainder sale."""
    return clean_remainder_fills(ledger_path, journal_dir, venue_name)


def _load_second_source():
    """T51 ruling C: the second price source's verdict function, the one live mode asks (read-only quotes), for the
    practice path too. Imported here and only here, so a test can stand it in."""
    from valedia_trader.data_and_fees.second_quote import second_opinion
    return second_opinion


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


def _age_words(seconds: float) -> str:
    """CF-Q2: an age limit in words, whole minutes when it is whole minutes ("5 minutes"), else seconds."""
    return f"{seconds // 60:g} minutes" if seconds % 60 == 0 else f"{seconds:g} seconds"


def _iso_utc(seconds: float) -> str:
    """CF-Q2: an instant in seconds as ISO 8601 in UTC (the journal's quote_ts)."""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _read_quote(raw, now: float, max_age: float = QUOTE_MAX_AGE_SECONDS) -> tuple[float | None, float | None, str | None]:
    """(price, time, None) for a usable quote (at most `max_age` old: QUOTE_MAX_AGE_SECONDS, or for the quote an order
    is priced from PRIMARY_QUOTE_MAX_AGE_SECONDS), else (None, None, why), in the order step's own words. Never raises,
    whatever the quote's methods do."""
    try:
        return _read_quote_unguarded(raw, now, max_age)
    except Exception:
        return None, None, "the quote could not be read"


def _read_quote_unguarded(raw, now: float, max_age: float = QUOTE_MAX_AGE_SECONDS) -> tuple[float | None, float | None, str | None]:
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
    if age > max_age:
        return None, None, f"the quote is {age / 60:.1f} minutes old, more than {max_age / 60:g}"
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


STATE_LAYOUT = 2                                  # T51c S3: layout 1 and the owner's entries
STATE_SCHEMA = [                                  # layout 1, as every layout-1 file holds it
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, units REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS equity (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, time REAL NOT NULL, "
    "equity REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends (tag TEXT PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, outcome TEXT)",
]
_LAYOUT1_TABLES = frozenset({"meta", "baseline", "equity", "sends"})
# T51c S3 (R5): layout 2 = layout 1 and these, run in that one write transaction whether the file is new or upgraded,
# so a new file and an upgraded one have the same schema. The old `equity` table is kept as it is.
_STATE_UPGRADE_1_TO_2 = [
    "CREATE TABLE owner_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, day TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('cash_in', 'cash_out', 'units_out', 'split', 'mark')), symbol TEXT, amount TEXT, "
    "units TEXT, ratio TEXT, mark TEXT, units_before TEXT NOT NULL, units_after TEXT NOT NULL, "
    "value_per_unit_before TEXT, value_per_unit_after TEXT, who TEXT NOT NULL CHECK (length(trim(who)) > 0), "
    "note TEXT NOT NULL CHECK (length(trim(note)) > 0), prev_hash TEXT NOT NULL, hash TEXT NOT NULL UNIQUE)",
    "CREATE TRIGGER owner_entries_no_update BEFORE UPDATE ON owner_entries "
    "BEGIN SELECT RAISE(ABORT, 'owner entries are append-only'); END",
    "CREATE TRIGGER owner_entries_no_delete BEFORE DELETE ON owner_entries "
    "BEGIN SELECT RAISE(ABORT, 'owner entries are append-only'); END",
    # The book's own value history for S4's day loss and drawdown, keyed like `equity` (an id, the New York day, the
    # time; several runs a day): what book_figures yields (units outstanding, value per unit, the value's bounds,
    # cash_bounded) as exact decimal text, the run's batch, the marks it was valued at (canonical JSON of decimal text)
    # and their sha256, and the hash of the latest owner entry it includes, so that a restart, a second run the same
    # day and a reader a year later can recompute it. Written and read by nothing in this build.
    "CREATE TABLE book_nav (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, time REAL NOT NULL, "
    "batch TEXT NOT NULL, units_outstanding TEXT NOT NULL, value_per_unit TEXT, value_low TEXT, value_high TEXT, "
    "cash_bounded INTEGER NOT NULL CHECK (cash_bounded IN (0, 1)), marks TEXT NOT NULL, marks_hash TEXT NOT NULL, "
    "entry_hash TEXT NOT NULL)",
]
_LAYOUT2_NEW = frozenset({"owner_entries", "book_nav"})
_LAYOUT2_TABLES = _LAYOUT1_TABLES | _LAYOUT2_NEW
_ENTRY_FIELDS = ("id", "time", "day", "kind", "symbol", "amount", "units", "ratio", "mark", "units_before",
                 "units_after", "value_per_unit_before", "value_per_unit_after", "who", "note", "prev_hash")
_GENESIS = "0" * 64
MAX_WHO, MAX_NOTE = 200, 1000
JOURNAL_NOT_WRITTEN = 3                           # addendum 4 (R12): the owner's entry is written, its journal line not


_PRODUCTION_PREFIX = "webull-production"          # R23: webull.py names a venue f"webull-{environment}{suffix}" and
                                                  # go_live.LIVE_VENUE is "webull-production" (not imported here)


def _production_venue_name(venue: str) -> bool:
    """Addendum 7 (R23): whether `venue` names (or nearly names) a production venue: compared without case (a case-
    blind folder would open the same file) and without trailing dots or spaces, a name starting with
    "webull-production" (the plain venue and its "-events" venue). "webull-sandbox..." and every other name are
    practice names, as before."""
    return venue.casefold().rstrip(". ").startswith(_PRODUCTION_PREFIX)


def _owner_text_problem(what: str, text) -> str | None:
    """Addendum 5 (R15): the one judgement of an owner's entry's `who` or `note`, which the command makes among its
    first refusals and the writing method makes again: plain printable text (no newline or tab), not blank, of at most
    MAX_WHO or MAX_NOTE characters. None when it is fine, else what is wrong, in words."""
    most = MAX_WHO if what == "who" else MAX_NOTE
    if not isinstance(text, str) or not text.strip() or len(text) > most or not text.isprintable():
        return (f"{what}: plain text on one line (no newline or tab) of at most {most} characters, not "
                f"{_cut(repr(text))}; nothing was written")
    return None


def _entry_hash(row: dict) -> str:
    """The sha256 of an owner entry's every field but its own hash, the previous entry's hash among them."""
    fields = [repr(float(row["time"])) if k == "time" else row[k] for k in _ENTRY_FIELDS]
    return hashlib.sha256(json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
NOT_COUNTED = ("refused", "not sent")             # outcomes that are not an order sent
LEDGER_NOT_SENT = ("intended", "refused")         # T22d-1: the ledger's states of an order never sent (NOT_COUNTED there)


def _checked_entries(db) -> list[dict]:
    """Every owner entry of a layout-2 file open on `db`, in order, after checking the chain: ids 1 to n with none
    missing (the file's own counter of ids given out says how many were ever written), each entry's previous hash the
    one before it, and each hash its own fields'. Refused in words (OrderStepError) otherwise: an entry changed or
    removed. What a rewrite of the file that recomputes the hashes does is found only against the journal
    (owner_entry_differences)."""
    cur = db.execute(f"SELECT {', '.join(_ENTRY_FIELDS)}, hash FROM owner_entries ORDER BY id")
    names = [c[0] for c in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    seq = db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'owner_entries'").fetchone()
    given = int(seq[0]) if seq else 0
    after = "a person must look before the book is worked out again"
    prev = _GENESIS
    for n, row in enumerate(rows, start=1):
        if row["id"] != n:
            raise OrderStepError(f"owner entry {n} was removed (the entries left are {', '.join(str(r['id']) for r in rows)}"
                                 f" of {given} written); {after}")
        if row["prev_hash"] != prev or row["hash"] != _entry_hash(row):
            raise OrderStepError(f"owner entry {n} was changed after it was written (its hash does not match what it "
                                 f"holds, or the entry before it); {after}")
        prev = row["hash"]
    if given > len(rows):
        raise OrderStepError(f"owner entry {len(rows) + 1} was removed ({given} were written, {len(rows)} are left); "
                             f"{after}")
    return rows


def _owner_entries_read_only(path: Path) -> list[dict]:
    """Addendum 4 (R13): the owner's entries of a state file, read through a read-only open that never creates,
    upgrades or changes anything: no file is no entries, a layout-1 file is no entries, a layout-2 file its checked
    entries. Anything else is refused in words (OrderStepError), as the writing open refuses it."""
    if not path.exists():
        return []
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0)
    try:
        return owner_entries_of(db, path)
    finally:
        db.close()


def owner_entries_of(db, path) -> list[dict]:
    """The owner's entries of the state file open on `db` (any read-only connection), after the chain is checked: none for
    an empty file or a layout-1 file, else _checked_entries. Anything that is not a state file of layout 1 or 2 is refused
    in words (OrderStepError). Reads only. T51c S4e: the owner's page reads through it on its own connection."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    tables.discard("sqlite_sequence")
    if not tables and version == 0:
        return []
    if version == 1 and tables <= _LAYOUT1_TABLES:
        return []
    if not (version == STATE_LAYOUT and _LAYOUT2_NEW <= tables <= _LAYOUT2_TABLES):
        raise OrderStepError(f"{path} is not an order-step state file of layout 1 or {STATE_LAYOUT}; it is not "
                             "opened")
    return _checked_entries(db)


_JOURNALED_ENTRY = ("id", "kind", "symbol", "amount", "units", "ratio", "mark", "units_before", "units_after",
                    "value_per_unit_before", "value_per_unit_after", "who", "note", "prev_hash", "hash")
_COMPARED_FIELDS = tuple(k for k in _JOURNALED_ENTRY if k not in ("id", "who", "note"))   # R16: the writer may redact these two


def owner_entry_differences(entries, journal, *, venue: str) -> list[str]:
    """Addendum 4 (R12): every difference, in words, between the owner's entries a state file holds (`entries`, as
    owner_entries gives them) and the journal's `owner_entry` lines of `venue` (`journal`: the journal's entries, any
    type, in the order read): an entry in the state file and not in the journal, a journal line whose entry the state
    file does not hold, the same id differing in any of _COMPARED_FIELDS (kind, symbol, amount, units, ratio, mark,
    units and value per unit before and after, previous hash, hash: every journaled field but who and note, which the
    journal's writer may redact), an id journaled twice or not a whole number,
    and lines in another order than their ids. Pure. Called only by the owner's command in this build (S4 wires it into
    every run). It finds a change to ONE of the two records; two changed to match, or both put back from one older
    copy, agree and are not found here."""
    state = {int(e["id"]): e for e in entries}
    lines = [x for x in journal if isinstance(x, dict) and x.get("type") == "owner_entry" and x.get("venue") == venue]
    out: list[str] = []
    seen: dict[int, dict] = {}
    order: list[int] = []
    for x in lines:
        n = x.get("id")
        if isinstance(n, bool) or not isinstance(n, int):
            out.append(f"a journal line of an owner's entry has the id {_cut(repr(n))}, not a whole number")
            continue
        if n in seen:
            out.append(f"owner entry {n} is journaled twice")
            continue
        seen[n] = x
        order.append(n)
    for n in sorted(set(state) | set(seen)):
        if n not in seen:
            out.append(f"owner entry {n} is in the state file and not in the journal")
        elif n not in state:
            out.append(f"the journal holds owner entry {n}, which is not in the state file")
        else:                                             # addendum 5 (R16): every journaled field but who and note
            other = [k for k in _COMPARED_FIELDS if seen[n].get(k) != state[n].get(k)]
            held_time = state[n].get("time")              # addendum 6 (R19): the journal's text of it against the same
            if seen[n].get("time") != (None if held_time is None else repr(float(held_time))):   # text of the state's
                other.append("time")
            if other:
                out.append(f"owner entry {n} differs between the journal and the state file in "
                           f"{', '.join(other)}" + (" (it has another hash)" if "hash" in other else ""))
    if order != sorted(order):
        out.append(f"the journal holds the owner's entries in the order {', '.join(map(str, order))}, not their ids' order")
    return out


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
    leaves the file as it was before that write or after it.

    T51c S3: layout 2 adds the owner's entries (R5: `owner_entries`, append-only by trigger, each hash-chained to the
    one before it). A new file is created at layout 2. A layout-1 file is upgraded the first time it is opened, inside
    that open's own write transaction (all of the upgrade or none of it), every row kept and the old `equity` table
    left as it is; the code before this layout refuses a layout-2 file in words. Any other layout, or tables that are
    not the layout's, is refused, never repaired. Nothing in a run reads the entries yet (S4)."""

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
                if tables and not ((version == 1 and tables <= _LAYOUT1_TABLES)
                                   or (version == STATE_LAYOUT and _LAYOUT2_NEW <= tables <= _LAYOUT2_TABLES)):
                    raise OrderStepError(f"{path} is not an order-step state file of layout 1 or {STATE_LAYOUT}; it is "
                                         "not opened")
                if not tables:
                    if version != 0:
                        raise OrderStepError(f"{path} is a database of another kind; it is not opened")
                    for statement in STATE_SCHEMA:
                        self._db.execute(statement)
                if version != STATE_LAYOUT:               # new, or layout 1: in this transaction, or not at all
                    for statement in _STATE_UPGRADE_1_TO_2:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {STATE_LAYOUT}")
        except BaseException:
            self._db.close()
            raise

    # ---- T51c S3 (R5): the owner's entries

    def _entries_checked(self) -> list[dict]:
        """Every owner entry in order, after checking the chain (_checked_entries)."""
        return _checked_entries(self._db)

    def owner_entries(self) -> list[dict]:
        """The owner's entries, oldest first, each a dict of its fields, once the chain is checked."""
        return self._entries_checked()

    def add_owner_entry(self, figures: dict, who: str, note: str, *, now: float | None = None,
                        after: str | None = None) -> dict:
        """Append one owner entry: `figures` as next_entry gives them, `who` and `note` (both required text), the
        time and New York day of `now` (the one clock reading its caller took; this file's clock when None), and the
        hash chained to the entry before it, all in one write transaction after the chain is checked. With `after`
        (the hash of the last entry its caller valued the book with, or the genesis hash for none), refused when the
        entries are no longer those. Returns the entry as written. No figure is defaulted here."""
        for what, text in (("who", who), ("note", note)):
            problem = _owner_text_problem(what, text)     # the same judgement as the command's (R15)
            if problem is not None:
                raise OrderStepError(f"an owner's entry needs {problem}")
        if figures.get("kind") not in ENTRY_KINDS:
            raise OrderStepError(f"an owner's entry is of kind {', '.join(ENTRY_KINDS)}; nothing was written")
        reading = self.clock() if now is None else now
        day = _ny_day(reading)
        if day is None:
            raise OrderStepError("the clock gives no New York day; nothing was written")
        now = float(reading)
        with self._write():
            rows = self._entries_checked()
            if after is not None and (rows[-1]["hash"] if rows else _GENESIS) != after:
                raise OrderStepError("the owner's entries changed while this entry was being worked out (another "
                                     "owner's entry was written meanwhile); nothing was written")
            if figures.get("kind") == "mark":
                self._allow_mark_kind(len(rows))              # the chain is checked above; this is in the same transaction
            row = {k: figures.get(k) for k in _ENTRY_FIELDS if k not in ("id", "time", "day", "who", "note", "prev_hash")}
            row.update(id=len(rows) + 1, time=now, day=day, who=who, note=note,
                       prev_hash=rows[-1]["hash"] if rows else _GENESIS)
            row["hash"] = _entry_hash(row)
            cols = (*_ENTRY_FIELDS, "hash")
            self._db.execute(f"INSERT INTO owner_entries ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             tuple(row[k] for k in cols))
        return {k: row[k] for k in (*_ENTRY_FIELDS, "hash")}

    def _allow_mark_kind(self, count: int) -> None:
        """T51c S4e (R34): a layout-2 file made before the `mark` kind has a CHECK on `owner_entries.kind` that refuses it,
        and a CHECK cannot be altered in place. In the write transaction of the first `mark` entry (the chain already
        checked, so no entry is missing or changed), the table is made again from the module's own statement, every row
        copied with its id, then the table renamed and its two append-only triggers made again; `user_version` stays 2. A
        file whose table already names the kind (a new one) is left as it is. All of it or none: the caller's transaction
        rolls back on any failure, and a copy that does not hold the same number of rows is one."""
        have = self._db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'owner_entries'").fetchone()
        if have is not None and "'mark'" in have[0]:
            return
        cols = ", ".join((*_ENTRY_FIELDS, "hash"))
        self._db.execute(_STATE_UPGRADE_1_TO_2[0].replace("CREATE TABLE owner_entries", "CREATE TABLE owner_entries_new", 1))
        self._db.execute(f"INSERT INTO owner_entries_new ({cols}) SELECT {cols} FROM owner_entries ORDER BY id")
        if self._db.execute("SELECT COUNT(*) FROM owner_entries_new").fetchone()[0] != count:
            raise OrderStepError("the owner's entries could not be copied whole into the table that names the mark kind; "
                                 "nothing was written")
        self._db.execute("DROP TABLE owner_entries")
        self._db.execute("ALTER TABLE owner_entries_new RENAME TO owner_entries")
        for statement in _STATE_UPGRADE_1_TO_2[1:3]:          # the append-only triggers (they went with the old table)
            self._db.execute(statement)

    def send_tags(self) -> set[str]:
        """R3: every tag the send record holds."""
        return {r[0] for r in self._db.execute("SELECT tag FROM sends")}

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

    # ---- T51c S4c (R27): the book's own value history, written by a shared run in place of `equity`

    _NAV_COLUMNS = ("units_outstanding", "value_per_unit", "value_low", "value_high", "cash_bounded", "marks",
                    "marks_hash", "entry_hash")

    def book_nav_rows(self) -> list[dict]:
        """Every `book_nav` row, oldest first, each a dict of its columns (the figures as the exact decimal text they
        were written as); `marks` is parsed into {symbol: price text}. A row whose marks are not such an object is
        refused in words (OrderStepError): a person must look."""
        return _nav_rows_of(self._db)

    def record_book_nav(self, day: str, batch: str, nav: dict) -> int:
        """Record a shared run's first look: one `book_nav` row, `nav` holding the figures as text (units_outstanding,
        value_per_unit, value_low, value_high, cash_bounded, marks as canonical JSON text, marks_hash, entry_hash).
        Returns the row's id (for reprice_book_nav). One write transaction."""
        with self._write():
            cur = self._db.execute(
                f"INSERT INTO book_nav (day, time, batch, {', '.join(self._NAV_COLUMNS)}) VALUES (?, ?, ?, "
                f"{', '.join('?' * len(self._NAV_COLUMNS))})",
                (day, float(self.clock()), batch, *(nav[k] for k in self._NAV_COLUMNS)))
        return int(cur.lastrowid)

    def reprice_book_nav(self, row: int, nav: dict) -> None:
        """Set the figures of this run's row again, when a quote in hand has changed (as reprice_equity does)."""
        sets = ", ".join(f"{k} = ?" for k in self._NAV_COLUMNS)
        with self._write():
            self._db.execute(f"UPDATE book_nav SET time = ?, {sets} WHERE id = ?",
                             (float(self.clock()), *(nav[k] for k in self._NAV_COLUMNS), int(row)))

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

    def shared_mark(self, day: str, number: str) -> dict | None:
        """T51c S4c: a ticket made by a SHARED run ({"mode": "shared"}: its capital is the book's value), or None for a
        ticket of an ordinary run ({} when it cannot be read)."""
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (f"shared:{day}:{number}",)).fetchone()
        if row is None:
            return None
        try:
            mark = json.loads(row[0])
        except ValueError:
            return {}
        return mark if isinstance(mark, dict) else {}

    def tagged_outcomes(self) -> list[tuple[str, str | None]]:
        """T51c S4c: every tag a ticket line was given before its intend, with the line's latest outcome (None: no
        outcome row yet), for `untracked_tags`. Reads only."""
        rows = self._db.execute(
            "SELECT t.tag, (SELECT o.outcome FROM outcomes o WHERE o.day = t.day AND o.number = t.number AND o.line = t.line "
            "ORDER BY o.seq DESC LIMIT 1) FROM tags t ORDER BY t.rowid").fetchall()
        return [(r[0], r[1]) for r in rows]

    def create(self, ticket: dict, lines: list[dict], live: dict | None = None, follow: dict | None = None,
               shared: dict | None = None) -> None:
        """Write a ticket and its lines, and mark every earlier unused ticket of the same venue and account superseded,
        in one transaction: only the latest ticket is live. A live ticket also gets its mark (`live`) and the broker
        book it follows (`follow`, T32) in the meta table, in the same transaction; so does a ticket of a shared run
        (`shared`, T51c S4c)."""
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
            if shared is not None:
                self._db.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                                 (f"shared:{ticket['day']}:{ticket['number']}", json.dumps(shared, sort_keys=True)))

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

    def __init__(self, venue, extra: dict | None = None) -> None:
        self._venue = venue
        self._extra = dict(extra or {})                  # T51 B3: arguments added to every submit (the live remainder's flag)
        self.called, self.error, self.answer = False, None, None

    def submit(self, *args, **kwargs):
        self.called = True
        try:
            self.answer = self._venue.submit(*args, **{**kwargs, **self._extra})
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
                   approve: str | None = None, live=None, second_opinion=None, follow=None, shared: bool = False,
                   practice_ledger=None) -> dict:
    """One order step (see the module notes). `paper` is PaperTargets or a callable giving them; `quotes(symbols)`
    gives {symbol: (price, time)}; `state` is an OrderStepState or the path of its file; `calendar` answers
    is_session, session_open and session_close (default: the XNYS calendar). Returns the summary, also journaled.
    Never raises because of the venue, the quotes or the paper book; a crash of the process mid-run is safe.
    `propose` writes a ticket instead of sending; `approve` (the ticket's number) sends only what that ticket holds.
    `live` (a go_live.LiveFiles, with propose or approve and a capital) is live mode: Webull's production environment
    under the four locks of the go-live gate; `second_opinion` is the second price source's verdict function
    (default: second_quote.second_opinion), asked in live mode and, with `fractional` on, for each MARKET leg on
    practice (T51 ruling C; with `fractional` off practice asks none). `follow` (a Follow: config/broker_book.json as
    the command line reads it, T32) makes the broker book follow only the strategies it names; None follows the
    whole book, except in live mode, which stops without one. `shared` (T51c S4c; live mode takes it from S4d, and the
    command line requires it there) is the shared-account mode: the book is judged on its own figures (see the module
    notes, "The shared run"). `practice_ledger` (T51 B1; a live fractional run only): the PRACTICE order ledger's path, read
    read-only for the proof reading; None: the practice ledger beside this run's own ledger file."""
    mode = "propose" if propose else ("approve" if approve is not None else None)
    run = _Run(ledger, venue, paper, quotes, journal, limits_file, state, clock, calendar, halt_file, latch_file,
               capital, fractional)
    run.shared = bool(shared)
    if live is not None:                                 # T51 B repair2 (1): a live run is no fractional run until
        run.fractional = False                           # `_live_fractions` has judged the opening and opened it; so no
                                                         # path, a stop before it included, can write a fractional-only key
    run.practice_ledger = practice_ledger
    if follow is not None:
        run.follow, run.follow_problem = follow, _follow_problem(follow)
        if run.follow_problem is None:
            run.follow_names, run.follow_every = follow.names, follow.every
    if live is not None:
        run.live = live
        run.expected_env = LIVE_ENVIRONMENT
        if second_opinion is None:
            second_opinion = _load_second_source()
        run.second_opinion = second_opinion
    elif run.fractional:                                 # T51 ruling C: a MARKET leg on practice needs it too; loaded
        if second_opinion is None:                       # ... (the live path's own) when the first one asks, once
            run.second_load = _load_second_source
        else:
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
        self.fractional_asked = bool(fractional)         # T51 B repair2: what the command asked (live: self.fractional starts off)
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
        self.ask_counts: dict[str, int] = {}             # T51 B repair3: how many times each symbol has been asked, in all
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
        self.residuals: list[dict] = []                  # T51 R1 (fractional): what each fund's split held back
        self.sale_caps: dict[str, dict] = {}             # CF-C: each sale's settled-quantity cap and its two inputs
        self.sales_left_unsettled: dict[str, float] = {}  # CF-C: the units of each sale its cap held back
        self._settled_book: tuple[dict, str | None] | None = None   # CF-C: the book's settled units, worked out once
        self.whole_passed: dict[str, float] = {}         # T51 R4: fund -> its LIMIT leg's units accepted (proposed) this run
        self.whole_fate: dict[str, str] = {}             # T51 A3 (a): fund -> what became of its LIMIT leg that did not pass
        self.second_load = None                          # T51 ruling C: practice, fractional: how to load the second source
        self.second_unloadable: str | None = None        # ... why it could not be loaded, once tried
        self.market_hours: tuple | None = None           # T51 R5 (B4): (start, end, why) of the MARKET legs' window
        self.practice_ledger = None                      # T51 B1: where the practice proof is read (None: beside the ledger)
        self.live_fractions: dict | None = None          # T51 B1-B4 (live, fractional): the opening as judged at the start
        self.live_open = False                           # ... remainder legs may go: B1, B2 and the shared book all hold
        self.first_contact = False                       # ... B4: no live remainder fill has reconciled cleanly yet
        self.shared = False                              # T51c S4c: the shared-account mode (practice only)
        self.last_fig: BookFigures | None = None        # ... the book's figures at the run's latest look
        self.entries: list[dict] = []                    # ... the owner's entries, read once under the lock
        self.journal_lines: list | None = None           # ... the journal as the scan read it (a shared run only)
        self.nav_row: int | None = None                  # ... this run's book_nav row
        self.nav_prior: list[dict] | None = None         # ... the book_nav rows written before this run's own
        self.nav_sig: tuple | None = None                # ... what this run's row holds now
        self.in_hand: dict[str, float] = {}              # ... the marks in hand at the run's last look
        self.shared_info: dict = {}                      # ... what the summary says of the book
        self.shortfalls: dict = {}                       # ... the shortfalls the run saw and did not set aside
        self.journaled_units: dict[str, float] = {}      # ... the units of the fills this run journaled, per symbol
        self.owner_surplus: set[str] = set()             # ... the latest reconcile's symbols the owner holds beyond the book

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
        if self.live is not None:                        # live mode: only as a ticket run, whole shares, and a capital
            why = ("live mode runs only with propose or approve" if self.mode is None else   # unless the book is shared:
                   "live mode needs the capital given (at most the confirmation's max_capital)"   # then it is the book's
                   if self.capital is None and not self.shared else None)                        # own value (T51c S4d, R26)
            if why is not None:
                return self._finish("stopped", "live", why)
            why = self._live_place()                     # before the lock: nothing read, nothing written
            if why is not None:
                return self._finish("stopped", "lock", why)
        try:
            self._environment()                          # 1: the venue's name, environment and host only
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        if self.shared and self.capital is not None:     # T51c S4c (R26): before the lock; the book's value is the capital
            return self._finish("stopped", "targets",
                                "--capital is refused in the shared mode: the capital is the book's own value (the cautious "
                                "bound), one source of truth, so a stake is changed by the owner's entry and never by a "
                                "figure typed to a run; nothing is read or sent")
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
                if self.fractional_asked:                # T51 B1 to B4: read at the start, read-only; a hold, never a stop
                    self._live_fractions()
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
        try:                                             # T51c S4c (R25): the latch, and a shared run's first refusals
            self._latch()
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except (OrderError, OrderStepError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "state", f"the owner's entries, the journal or the ledger could not be checked: "
                                                    f"{_local_error(exc)}; a person must look; nothing is read from the "
                                                    "account and nothing is sent")
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
            book = self._shared_units() if self.shared else broker_book(self.ledger.fills())
            if any(abs(u) > MAX_UNITS for u in book.values()):
                raise _Stop("reconcile", f"a position in the broker book is above {MAX_UNITS:,.0f} units in size; a person must look")
            if self.shared:                              # T51c S4c (R26): the prices come first; the capital is the book's value
                prices, why_not = self._prices(self._shared_symbols(paper, book), now)
                capital = self._shared_capital(book, prices)
            else:
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
            if not self.shared:
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
        except (OrderError, sqlite3.Error, OSError, BookError) as exc:
            return self._finish("stopped", "after", f"the last reconcile could not be completed: {_local_error(exc)}")
        self._judge_final(final)                         # T34b-2 (F1, F3): a failure here is the run's own (unexpected)
        if self.mode == "propose":
            try:
                self._make_ticket()
            except _Stop as stop:
                return self._finish("stopped", stop.stage, stop.reason)
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", "ticket", f"the ticket could not be written: {_local_error(exc)}")
        if self.shared:
            self._final_look()                           # T51c S4c: the run's row, at the book as the last reconcile left it
        return self._finish("done", None, None)

    # -- T51c S4c: the shared run (practice): the book judged on its own figures
    def _read_journal(self) -> list:
        try:
            return list(self.journal.read())
        except Exception as exc:
            raise _Stop("journal", f"the journal could not be read to compare the owner's entries: {_describe(exc)}") from None

    def _ticket_pairs(self) -> list[tuple[str, str | None]]:
        """Every tag a ticket line was given, with its line's outcome, from the ticket file (read only): the run's own
        when it has one open, else the file beside the state file when there is one."""
        book, own = self.tickets, False
        if book is None:
            path = ticket_path_for(self.run_path) if self.run_path is not None else ":memory:"
            if str(path) == ":memory:" or not os.path.exists(path):
                return []
            try:
                book, own = TicketBook(path, self.clock, create=False), True
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                raise _Stop("ticket", f"the ticket file could not be read: {_local_error(exc)}; nothing is read from the "
                                      "account and nothing is sent") from None
        try:
            return book.tagged_outcomes()
        except sqlite3.Error as exc:
            raise _Stop("ticket", f"the ticket file could not be read: {_local_error(exc)}; nothing is read from the "
                                  "account and nothing is sent") from None
        finally:
            if own:
                book.close()

    def _latch(self) -> None:
        """T51c S4c (R25, and study step 1), before anything is read from the account or sent. The state file latches:
        one that holds an owner's entry refuses a run that is not shared (stage `state`), so the book_nav history is
        never broken by a run that scales to the paper equity. A shared run, in this order: the state file's entries and
        the journal's must agree (`state`: a person must look); every tag the journal, the send record or a ticket holds
        must be in the ledger (`reconcile`: untracked_tags); and the book must have its opening entry (`targets`, a
        skip: the owner opens it)."""
        entries = self.state.owner_entries()             # the chain checked: a broken one is OrderStepError -> `state`
        if not self.shared:
            if entries:
                raise _Stop("state", f"this state file holds {len(entries)} owner's "
                                     f"entr{'y' if len(entries) == 1 else 'ies'}, so it is the shared account's book: a run "
                                     "without --shared would scale to the paper equity, write the old equity table and "
                                     "break the book's value history; run it with --shared; nothing is read from the "
                                     "account and nothing is sent")
            return
        lines = self.journal_lines if self.journal_lines is not None else self._read_journal()
        differences = owner_entry_differences(entries, lines, venue=self.name)
        if differences:
            raise _Stop("state", f"the state file and the journal differ on the owner's entries: "
                                 f"{_cut('; '.join(differences), 600)}; a person must look; nothing is read from the "
                                 "account and nothing is sent")
        found = untracked_tags([o["tag"] for o in self.ledger.orders()], venue=self.name, journal=lines,
                               tickets=self._ticket_pairs(), sends=self.state.send_tags())
        if found:
            raise _Stop("reconcile", f"{_cut('; '.join(f['reason'] for f in found[:3]), 600)}"
                                     f"{f' (and {len(found) - 3} more)' if len(found) > 3 else ''}; nothing is read from the "
                                     "account and nothing is sent")
        if not entries or entries[0].get("kind") != "cash_in":
            raise _Stop("targets", "the shared mode needs the book's opening entry (order-step --owner-entry cash_in "
                                   "--venue NAME ...), written by the owner; nothing is read from the account and nothing "
                                   "is sent")
        self.entries = entries
        if self.live is not None:                        # T51c S4d (A4 for the book): the value history the journal holds
            self._live_nav_history()

    def _shared_units(self, applied_only: bool = False) -> dict[str, float]:
        """The book's units per symbol after the owner's entries (a units_out, a split), as book_figures reads them: of
        every fill, or of the applied ones only (what the reconcile is given; it adds the unapplied itself)."""
        fills = self.ledger.fills()
        if applied_only:
            fills = [f for f in fills if f["applied"]]
        figures = book_figures(fills, self.ledger.orders(), self.entries, {}, self.day, now=self._now())
        return {sym: float(u) for sym, u in figures.units.items() if u != 0}

    def _shared_symbols(self, paper: PaperTargets, book: dict) -> list[str]:
        """The symbols to quote: the paper book's funds (the same set at any capital) and every fund the book holds. The
        baseline's holdings are never among them (R30)."""
        scale_one, _ = targets(paper, _book_capital(paper, self.follow_names, self.follow_every), self.follow_names,
                               self.follow_every)
        return sorted(set(scale_one) | {sym for sym, u in book.items() if u != 0})

    def _nav_rows(self) -> list[dict]:
        if self.nav_prior is None:
            self.nav_prior = self.state.book_nav_rows()     # before this run's own row is written
        return self.nav_prior

    def _shared_capital(self, book: dict, prices: dict) -> float:
        """R26, R28, after the prices: the book's value at them is the capital (the lower bound, the cautious one). Not
        known: stage `prices`; a held fund far from its last mark: stage `marks`; an approval whose book value moved
        from its ticket's by more than TICKET_QUOTE_MOVE: stage `ticket`."""
        figures = book_figures(self.ledger.fills(), self.ledger.orders(), self.entries, prices, self.day, now=self._now())
        if figures.value is None:
            raise _Stop("prices", f"the book's value is not known ({_value_unknown_why(figures)}); nothing is sent")
        if figures.value_per_unit is None:
            raise _Stop("targets", f"the book has {figures.units_outstanding} units outstanding, so it has no value per "
                                   "unit; the owner's entry opens it; nothing is sent")
        try:
            limits = risk_gate.load_limits(self.limits_file)
        except risk_gate.RiskConfigError:
            limits = None                                # check_order then refuses every order with the file's own error
        rows = self._nav_rows()
        held = [sym for sym, u in figures.units.items() if u != 0]
        if limits is not None:
            for far in far_quotes(rows[-1] if rows else None, self.entries, prices, held, limits["max_drawdown_pct"]):
                raise _Stop("marks", f"{far['symbol']} quotes {far['quote']:g}, and the book's last mark of it was "
                                     f"{far['mark']:g}" + (f" ({far['reference']:g} after the owner's split and mark entries)"
                                                           if far["reference"] != far["mark"] else "") +
                                     f", further than the owner's max_drawdown_pct of {float(limits['max_drawdown_pct']):g}% "
                                     "away: a split, a rename or a bad quote; if it is a split, the owner writes a split "
                                     "entry, and if the quote is real a mark entry; a person must look; nothing is sent")
        value = float(figures.value)
        if self.live is not None:                        # T51c S4d (R26): lock 1 against the BOOK's value, the upper bound
            self._lock_one_book(figures)
        if self.mode == "approve" and self.ticket is not None:
            made = float(self.ticket["capital"])
            if not abs(value - made) <= TICKET_QUOTE_MOVE * made:
                raise _Stop("ticket", f"that ticket was made at a book value of {made:,.2f}; it is now {value:,.2f}, more "
                                      f"than {TICKET_QUOTE_MOVE:.1%} away; nothing is sent")
        self.shared_info["capital"] = value
        return value

    def _lock_one_book(self, figures: BookFigures) -> None:
        """T51c S4d (R26): lock 1's `max_capital` stays binding in a live shared run, and the capital it binds is the
        book's own value taken at its UPPER bound (the cautious side of a ceiling): a book above it, or one whose upper
        value is not known, is refused before any order (stage `lock`, a skip; the confirmation's own words). The
        figures are said in the summary (`shared.book_value`, `shared.book_value_high`, `ceiling`)."""
        conf = self.locks.confirmation if self.locks is not None else None
        if conf is None:
            raise _Stop("lock", "lock 1 (the confirmation) is not in force; nothing is sent")
        ceiling = Decimal(str(conf["max_capital"]))
        high = figures.value_high
        self.shared_info["book_value"] = float(figures.value)
        self.shared_info["book_value_high"] = None if high is None else float(high)
        if high is None:
            raise _Stop("lock", f"lock 1 (the confirmation): the book's value has no upper bound known (the book's lower "
                                f"value is {figures.value:,.2f}), so it cannot be shown to be at most max_capital "
                                f"{ceiling:,.2f}; nothing is sent")
        if not high <= ceiling:
            raise _Stop("lock", f"lock 1 (the confirmation): the book's value (upper bound) {high:,.2f} is above "
                                f"max_capital {ceiling:,.2f} (by {high - ceiling:,.2f}); nothing is sent")

    def _book_look(self, marks: dict) -> BookFigures:
        """The book's figures at the marks in hand, from the ledger as it is now; the value must be known."""
        figures = book_figures(self.ledger.fills(), self.ledger.orders(), self.entries, marks, self.day, now=self._now())
        if figures.value is None or figures.value_per_unit is None:
            why = _value_unknown_why(figures) if figures.value is None else "no units outstanding"
            raise _Stop("prices", f"the book's value is not known ({why}); nothing more is sent")
        self.last_fig = figures
        return figures

    def _shared_look(self, marks: dict) -> tuple[BookFigures, BookLoss, dict]:
        """One look at the book: its figures at `marks`, the day's loss and the drawdown on the value per unit, and the
        summary's account of them (said again at each look), which the caller applies to `shared_info` only once the
        row of that look is written (_shared_account): the summary carries the figures of the row actually written."""
        figures = self._book_look(marks)
        with localcontext() as ctx:
            ctx.prec = _BOOK_PREC
            high = None if figures.value_high is None else figures.value_high / figures.units_outstanding
        loss = book_loss(self._nav_rows(), self.entries, self.day, low=figures.value_per_unit, high=high)
        info = dict(
            book_value=float(figures.value), book_value_high=None if figures.value_high is None else float(figures.value_high),
            value_per_unit=str(figures.value_per_unit), value_per_unit_high=None if high is None else str(high),
            units_outstanding=str(figures.units_outstanding),
            cash_bounded=figures.cash_bounded, fees_taken_as_none=figures.fees_taken_as_none,
            fees_bounded={"count": len(figures.bounded_orders), "orders": sorted(_cut(t) for t in figures.bounded_orders)[:20]},
            day_loss_pct=float(loss.daily_loss_pct), drawdown_pct=float(loss.drawdown_pct),
            previous_value_per_unit=str(loss.previous), previous_from=loss.previous_from,
            day_loss_upper_open=loss.upper_open)
        return figures, loss, info

    def _nav_sync(self, figures: BookFigures, marks: dict) -> None:
        """This run's `book_nav` row at the figures: written at the first look, set again at a later one when it changed."""
        held = {sym: str(_dec(marks[sym], f"the mark of {_cut(sym)}")) for sym, u in sorted(figures.units.items())
                if u != 0 and sym in marks}
        text = json.dumps(held, sort_keys=True, separators=(",", ":"))
        nav = {"units_outstanding": str(figures.units_outstanding), "value_per_unit": str(figures.value_per_unit),
               "value_low": str(figures.value), "value_high": None if figures.value_high is None else str(figures.value_high),
               "cash_bounded": int(figures.cash_bounded), "marks": text,
               "marks_hash": hashlib.sha256(text.encode()).hexdigest(), "entry_hash": self.entries[-1]["hash"]}
        sig = tuple(nav.values())
        if self.nav_row is None:
            self.nav_row = self.state.record_book_nav(self.day, self.batch, nav)
        elif sig != self.nav_sig:
            self.state.reprice_book_nav(self.nav_row, nav)
        self.nav_sig = sig

    def _shared_account(self, marks: dict, opened: int, look: bool = True) -> dict:
        """The account the risk gate judges, in a shared run: the BOOK's value at the marks in hand (the lower bound) as
        its equity, the day's loss and the drawdown on its value per unit, the funds it holds as the count (R27, R31).
        In live mode every look but the run's last may trip the day stop, on the loss of the value per unit (S4d)."""
        figures, loss, info = self._shared_look(marks)
        self._nav_sync(figures, marks)                   # the row first: a failure leaves the summary as the last row was
        self.shared_info.update(info)
        self.broker_equity = float(figures.value)
        if look and self.live is not None:               # T34b-2 (S2) as the ordinary account's look does it
            self._look(float(loss.daily_loss_pct))
        return {"equity": float(figures.value), "daily_loss_pct": float(loss.daily_loss_pct),
                "drawdown_pct": float(loss.drawdown_pct), "open_positions": opened}

    def _final_look(self) -> None:
        """After the last reconcile: the run's row (and the summary's figures) at the book as that reconcile left it, at
        the marks in hand, so a fund bought in this run has its mark for the next run's check. The run is over: a look
        that cannot be made leaves the row as the last judged look had it."""
        if self.nav_row is None:
            return
        info, equity, last = dict(self.shared_info), self.broker_equity, self.last_fig
        try:
            self._shared_account(self.in_hand, 0, look=False)
        except Exception:
            self.shared_info, self.broker_equity, self.last_fig = info, equity, last   # the summary says what the row holds

    def _shortfall_stop(self, first, second, still: list[dict]) -> str | None:
        """R24: the words of a stop at stage `shortfall`, or None. The ledger stands in the way only because the account
        holds less than the book counts as its own: the second reconcile lists a shortfall (or, when its positions read
        raised, the first one did: it is never taken as "the venue could not be read"), and nothing else is wrong in
        either (no unresolved order, anomaly or difference; no order still open, sent or unknown; no fill unapplied; no
        blocking attention item). Anything else stays `reconcile`, with the shortfall in its words."""
        try:
            if still or not (isinstance(first, dict) and isinstance(second, dict)):
                return None
            shorts, from_first = second.get("position_shortfalls") or {}, False
            if not shorts and second.get("positions_error_kind") == "positions_unread":
                shorts, from_first = first.get("position_shortfalls") or {}, True
            if not shorts or not isinstance(shorts, dict):
                return None
            for res in (first, second):
                if res.get("unresolved") or res.get("anomalies") or res.get("position_differences"):
                    return None
            if self.ledger.orders(("sent", "unknown")) or self.ledger.unapplied_fills():
                return None
            for item in self.ledger.attention(open_only=False):
                if item.get("kind") in NOTICE_KINDS:
                    continue
                if item.get("closed_time") is None or int(item["id"]) > (self.attention_before or 0):
                    return None
            if any(why != "the latest reconcile was not clean" for why in self.ledger.blocked()):
                return None
            self.shortfalls = _shortfall_figures(shorts)
            return self._shortfall_words(shorts) + ("; the second read of the account's positions failed" if from_first else "")
        except Exception:
            return None

    def _shortfall_words(self, shorts: dict) -> str:
        """The shortfalls in words (R2's sentence, from the ledger's own record: the symbol, both figures, the two
        ordinary causes), for the stop and the ledger words; at most 2000 characters."""
        parts = []
        for sym in sorted(shorts, key=str):
            d = shorts[sym]
            if _plain(_SYMBOL, sym) and isinstance(d, dict) and isinstance(d.get("reason"), str):
                parts.append(d["reason"])
            else:
                parts.append("the account holds less than the book counts as its own of a symbol that is not plain")
        return _cut("the account holds less than the book counts as its own: " + "; ".join(parts) +
                    "; nothing is sent; it clears when the shares are back or when the owner writes a units_out or a split "
                    "entry", 2000)

    def _short_within(self, sym, d) -> bool:
        """R24: whether a shortfall `d` of `sym` is at most the units this run's own orders of it (open at the venue, or
        of this batch) or the fills it journaled of it amount to."""
        short = _as_float(d.get("short")) if isinstance(d, dict) else None
        if short is None:
            return False
        orders = [o for o in self.ledger.orders() if o.get("symbol") == sym
                  and (o.get("state") in OPEN_AT_VENUE or (self.batch and o.get("batch") == self.batch))]
        cap = max(math.fsum(abs(float(o["units"])) for o in orders), self.journaled_units.get(sym, 0.0))
        return short <= cap * (1.0 + 1e-9) + 1e-9

    def _shared_summary(self) -> dict:
        info = dict(self.shared_info)
        info["owner_surplus"] = sorted(self.owner_surplus)
        if self.shortfalls:
            info["shortfalls"] = self.shortfalls
        return info

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

    def _practice_ledger_path(self):
        """T51 B1: the practice ledger the proof is read from: the one given, else the practice venue's ledger file beside
        this run's own (the configured layout: one ledger per venue name in the memory folder). None when neither."""
        if self.practice_ledger is not None:
            return self.practice_ledger
        mine = getattr(self.ledger, "path", None)
        if type(mine) is not str or mine in (":memory:", ""):
            return None
        return ledger_path_for(Path(mine).parent, PRACTICE_VENUE)

    def _journal_folder(self):
        folder = getattr(self.journal, "dir", None)
        return None if folder is None else folder

    def _live_fractions(self) -> None:
        """T51 B1 to B4, once, at the start of a live fractional run (the locks are met, nothing is read from the account):
        whether the remainder legs may go on the live account, by three locks, each read without writing anything:
          - the shared book (live is always shared, S4d);
          - B2, the owner's consent: the confirmation the locks judged holds `fractional_live_consent` as JSON true;
          - B1, the practice proof: `practice_proof` on the practice ledger and journal, a clean MARKET remainder
            purchase and a clean MARKET remainder sale.
        When any is missing the opening is SHUT and the run is, in everything it reads, decides and writes, the same live
        run as without `--fractional` (T51 B repair F1: `self.fractional` is switched off, so no split, no zero rule, no
        five-decimal units or minimum, no residual and no `fractional` mark on the ticket), saying what is missing in words
        on the summary (`live_fractions`), the only addition. A hold, not a stop: the whole-share path is exactly what it
        was, and a stop would take it down with the fractions. When all hold, B4 is read from the LIVE record by the same definition: until one live
        remainder leg has filled and reconciled cleanly, at most ONE remainder leg goes in a run (`first_contact`)."""
        missing = []
        if not self.shared:
            missing.append("the live book is always the shared account's (the run was not shared)")
        conf = self.locks.confirmation if self.locks is not None else None
        if not (isinstance(conf, dict) and conf.get("fractional_live_consent") is True):
            missing.append("the owner's consent is not given: config/webull_live.json does not hold "
                           "\"fractional_live_consent\": true (absent, false, text or anything but JSON true is no consent)")
        proof = None
        folder = self._journal_folder()
        mine = self._practice_ledger_path()
        if mine is None or folder is None:
            missing.append("the practice record cannot be found (no practice ledger or journal folder to read it from)")
        else:
            proof = practice_proof(mine, folder)
            if not proof.ok:
                missing.append("the practice proof is not there: " + proof.words())
        info = {"open": not missing, "consent": bool(isinstance(conf, dict) and conf.get("fractional_live_consent") is True)}
        if proof is not None:
            info["practice"] = {"purchases": proof.purchases, "sales": proof.sales}
            if proof.unreadable is not None:
                info["practice"]["unreadable"] = proof.unreadable
        if missing:
            info["why"] = "; ".join(missing)
        else:
            live = clean_remainder_fills(self.ledger.path, folder, self.name) if getattr(self.ledger, "path", None) else None
            cleared = live is not None and live.unreadable is None and (live.purchases + live.sales) >= 1
            self.first_contact = not cleared             # unreadable: the safe side, one leg at most
            info["first_contact"] = self.first_contact
            info["live_clean"] = {"purchases": 0 if live is None else live.purchases, "sales": 0 if live is None else live.sales}
        self.live_fractions, self.live_open = info, not missing
        self.fractional = not missing                    # T51 B repair F1: a shut opening is no fractional run at all (it
                                                         # started off): no split, no zero rule, no 5-decimal units, no
                                                         # residual, the ticket flag as without --fractional; only the words

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
        if self.shared:                                  # T51c S4d (R27): the book's own figures, never the equity table:
            return                                       # judged in `_latch`, before anything is read from the account
        peak, seen = self.state.peak(), self.journal_peak
        if seen is not None and (peak is None or peak < seen):
            held = "no equity" if peak is None else f"an equity peak of {peak:,.2f}"
            raise _Stop("state", f"the order step's state file holds {held} and the journal recorded an equity of "
                                 f"{seen:,.2f} for {self.name}: the state file knows less than the journal (an older copy "
                                 "put back?), so the drawdown cannot be judged; a person must look; nothing is sent")

    def _live_nav_history(self) -> None:
        """T51c S4d (A4 for the shared book, R27), before anything is read from the account: the state file's `book_nav`
        rows must hold the value history the journal's summaries of this venue recorded, or an older state file put back
        would change the previous value per unit (the day stop) or lower the peak (the drawdown). (a) Every New York day on
        which the journal holds a shared summary with a value per unit must have a row of that day, and the day's LATEST
        row must read what the journal's LAST such summary of the day says: the lower figure, and the upper one where the
        summary journals it (exact decimals, never text); an owner's acceptance of a day (accept_history_command) stands for its
        last summary. (b) The highest figure the rows hold (the UPPER figure of a row
        where it is finite, the drawdown's basis, else its lower) must not be below the journal's. A day with no such
        summary is not required to have a row. Otherwise the run stops at `state`, a fault: a person must look. The old
        `equity` table is not read."""
        looks = _scan_shared_looks(self.journal_lines or [], self.name)[0]
                                                         # (no look at all: the rows' days and figures are still judged)
        try:
            found = _nav_history(self._nav_rows(), looks, self.day, self.entries)
        except OrderStepError as exc:
            raise _Stop("state", f"the state file's book_nav rows cannot be read the way the day stop and the drawdown read "
                                 f"them: {exc}; nothing is sent") from None
        if found["lacking"] or found["changed"] or found["unheld"]:
            said = ([f"no book_nav row for {', '.join(found['lacking'][:5])}"] if found["lacking"] else [])
            for day in found["unheld"][:5]:
                said.append(f"a book_nav row of {day} (batch {_cut(str(_row_batch_of(self._nav_rows(), day)))}) on a day the "
                            "journal holds no summary for, after the last day it does (or with none before today): it would "
                            "be taken as the previous value per unit")
            for day in list(found["changed"])[:5]:
                look, now = found["changed"][day]["look"], found["changed"][day]["now"]
                said.append(f"{day}: the journal's last summary says {_figures_said(look['low'], look['high'], look['has_high'])}, "
                            f"the state file's latest row (batch {_cut(str(now['row']['batch']))}) says "
                            f"{_figures_said(now['low'], now['high'])}")
            raise _Stop("state", f"the state file lacks the book's value history the journal holds: a copy put back? Or a run's "
                                 f"summary that never reached the journal (its row is in the state file, its summary is not "
                                 f"in the journal)? ({'; '.join(said)}), so the day's loss and the drawdown cannot be judged; "
                                 "a person must look: a copy put back is cured only by the real state file; a row that is not below "
                                 "the journal's figure may be accepted by the owner with order-step --live --accept-history DAY "
                                 f"--venue {self.name} --who WHO --note NOTE (a preview, then --confirm CODE; the command "
                                 "cannot tell a lost summary from a copy, and a lower row has no command route); nothing is sent")
        peak, seen = found["peak"], found["seen"]
        if seen is not None and (peak is None or peak < seen):
            held = "no value per unit" if peak is None else f"a highest value per unit of {peak}"
            raise _Stop("state", f"the order step's state file holds {held} in its book_nav rows (the upper figure where it "
                                 f"is known) and the journal recorded a value per unit of {seen} for {self.name}: the state "
                                 "file knows less than the journal (an older copy put back?), so the drawdown cannot be "
                                 "judged; a person must look; nothing is sent")

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
        shorts = final.get("position_shortfalls")        # T51c S4c (R24): only a one-sided (shared) reconcile has them
        shorts = shorts if isinstance(shorts, dict) else {}
        if diffs or shorts:                             # J2 (addendum 5): a symbol this run touched is set aside too
            touched = {o["symbol"] for o in self.ledger.orders()
                       if o.get("state") in OPEN_AT_VENUE or (self.batch and o.get("batch") == self.batch)}
            working = touched | self.journaled_symbols
        else:
            working = set()
        aside = {sym for sym in diffs if sym in working}
        # R24: a shortfall in a symbol this run's own orders touched, of at most their units, is set aside like a
        # difference (the account's holding falls, or has not yet risen, before the order's status says so); any other
        # shortfall is no fault either (an owner's sale is an ordinary day): it is said in the summary and the next
        # run's first reconcile stops at stage `shortfall`
        aside_short = {sym for sym, d in shorts.items() if sym in working and self._short_within(sym, d)}
        self.shortfalls = _shortfall_figures({sym: d for sym, d in shorts.items() if sym not in aside_short})
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
                 or bool(self.ledger.orders(("sent", "unknown"))) or not (aside or unread or status_aside or shorts))
        if other:
            self.final_unclean = True
        if aside or aside_short or unread or status_aside:
            self.reconcile_pending = {"symbols": sorted({self._cap_symbol(sym)
                                                         for sym in (*aside, *aside_short, *status_aside.values())}),
                                      "positions_unread": unread, "status_unread": len(status_aside)}

    @staticmethod
    def _limit_verdict(order, avg) -> str:
        """(iii), K1, on one average `avg` of the order (a broker's own figure, never a piece price worked out from two
        running averages): `breach` when it is beyond LIMIT_SAME x limit (times LIMIT_EDGE, so the edge itself is no
        breach) on the wrong side of the order's limit; `unjudged` when the order, its units, its limit or `avg` cannot
        be read as usable numbers; else `ok`. Every limit the order step sends is a whole number of cents, so an
        average the broker rounds to the cent is at its limit or a cent through it; an average that includes fees
        would trip this rule. T51 R6: a MARKET leg (no limit) is judged against its collar price, the price a LIMIT leg
        would have carried (a whole number of cents too), by the same rule; a breach of it is `cap` like any other."""
        asked = _as_float(order.get("units")) if isinstance(order, dict) else None
        if asked is None or asked == 0:
            return "unjudged"
        bound = "collar_price" if order.get("order_type") == "MARKET" else "limit_price"
        avg, limit = _as_float(avg), _as_float(order.get(bound))
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
        for sym, u in (self._shared_units() if self.shared else broker_book(self.ledger.fills())).items():
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
        shared_mark = book.shared_mark(t["day"], t["number"])     # T51c S4c: a shared run's ticket has the book's value
        if self.shared and shared_mark is None:                   # as its capital; an ordinary run's has the paper's
            self._refuse_ticket("that ticket was not made in shared mode")
        if not self.shared and shared_mark is not None:
            self._refuse_ticket("that ticket was made in shared mode")
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
        if not self.shared and (_as_float(capital) is None or float(capital) != t["capital"]):
            self._refuse_ticket("that ticket was made for another capital")   # (shared: judged after the prices, R26)
        if bool(t["fractional"]) != self.fractional:
            self._refuse_ticket("that ticket was made " + ("with" if t["fractional"] else "without") + " fractional units")
        lines = [{"line": int(ln["line"]), "fund": ln["symbol"], "side": ln["side"], "units": float(ln["units"]),
                  "quote": float(ln["quote"]), "limit": float(ln["limit_price"]), "value": float(ln["value"])}
                 for ln in book.lines(t["day"], t["number"])]
        legs = set()
        for ln in lines:                                 # T51 ruling B: a line's leg is a function of its units alone
            leg = self._line_leg(ln, bool(t["fractional"]))
            if (ln["fund"], leg) in legs:
                self._refuse_ticket(f"that ticket holds two lines of the {leg} leg of {ln['fund']}: it is malformed")
            legs.add((ln["fund"], leg))
            if self.fractional:
                ln["leg"] = leg
        self.ticket = t
        self.ticket_lines = lines

    def _line_leg(self, ln: dict, fractional: bool) -> str:
        """T51 ruling B: a ticket line's leg from its units alone: under one unit in size the remainder leg (only on a
        ticket made with fractional units), whole and at least one the whole leg; anything else is a malformed ticket,
        refused in words."""
        size = abs(ln["units"])
        if not (_finite(size) and size > 0):
            self._refuse_ticket(f"line {ln['line']} of that ticket holds no units ({ln['units']!r}): it is malformed")
        if size < 1:
            if not fractional:
                self._refuse_ticket(f"line {ln['line']} of that ticket holds {ln['units']:+g} units, under one unit, on a "
                                    "ticket made without fractional units: it is malformed")
            return "remainder"
        if size != math.floor(size):
            self._refuse_ticket(f"line {ln['line']} of that ticket holds {ln['units']:+g} units, one or more with "
                                "decimals, which is no leg of the split: it is malformed")
        return "whole"

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
            self.tickets.create(ticket, self.ticket_lines, live=mark, shared={"mode": "shared"} if self.shared else None)
        else:                                            # T32: a live ticket is bound to the broker book it follows
            kept = {"shared": {"mode": "shared"}} if self.shared else {}   # T51c S4d: and, in a shared run, marked so
            self.tickets.create(ticket, self.ticket_lines, live=mark,
                                follow={"digest": self.follow.digest, **self._follow_view()}, **kept)
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
        fresh = {(c["symbol"], c.get("leg", "whole")): c for c in candidates}   # T51 R4: keyed by (fund, leg)
        why_not = {x["symbol"]: x["reason"] for x in self.skipped + self.refused}
        out = []
        for ln in self.ticket_lines:
            c = fresh.pop((ln["fund"], ln.get("leg", "whole")), None)
            stub = {"symbol": ln["fund"], "units": ln["units"], **_leg_of(ln), "line": ln["line"]}
            if c is None:
                why = why_not.get(ln["fund"])
                self._drop(stub, "the order is no longer wanted by the fresh targets" + (f" ({why})" if why else ""))
            elif not self._same_size(c["units"], ln):
                self._drop(stub, f"the wanted size changed: the ticket holds {ln['units']:+g}, the fresh targets want "
                                 f"{c['units']:+g}")
            else:
                out.append({**c, "line": ln["line"]})
        for c in fresh.values():
            self.skipped.append({"symbol": c["symbol"], "units": c["units"], **_leg_of(c), "reason": "not on the ticket"})
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
        item = {"symbol": c["symbol"], "units": c["units"], **_leg_of(c), "reason": reason}
        self.dropped.append(item)
        self._write({"event": "dropped", **item})
        self._ticket_note(c, "not sent", reason=reason)
        self._whole_failed(c, f"was dropped ({reason})")

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
            item = {"line": ln["line"], "fund": ln["fund"], "side": ln["side"], "units": ln["units"], **_leg_of(ln)}
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
        if self.shared:                                  # T51c S4c: the owner's entries and the tags are compared with it
            self.journal_lines = entries
        self._seen_fill_ids = {e.get("fill_id") for e in entries
                               if e.get("type") == "order_step" and e.get("event") == "fill" and e.get("venue") == self.name}
        self._scan_verdicts(entries)                     # T34b-3: every file, every run (no cut-off)
        self.torn_lines = [{"file": name, "line": n} for name, n, _ in torn]
        if self.live is not None:                        # T22d-1 (A4): the highest equity journaled for this venue
            if self.shared:                              # T51c S4d (R27): the book's own looks are read from journal_lines
                pass
            else:
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
        if self.fractional:                              # T51 R5 (B4): the MARKET legs' own, narrower window
            self.market_hours = market_leg_window(self.calendar, now)

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
        if self.shared:                                  # T51c S4c (R24, R30): one-sided, the book's own units after the
            result = self.ledger.reconcile(self.venue, self._shared_units(applied_only=True), one_sided=True)   # entries
            if isinstance(result, dict):
                self.owner_surplus = {s for s in (result.get("owner_surplus") or {}) if _plain(_SYMBOL, s)}
        else:
            result = self.ledger.reconcile(self.venue, self._applied_positions(baseline))
        self._journal_fills()
        self._journal_notices()
        self._settle_unjudged()
        return result

    def _order_type_of(self, tag) -> str | None:
        """T51 R4: the order type the ledger holds for `tag` (LIMIT or MARKET), or None when it holds no such order."""
        order = self.ledger.order(tag) if type(tag) is str and tag else None
        kind = order.get("order_type") if isinstance(order, dict) else None
        return kind if kind in ("LIMIT", "MARKET") else None

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
                if self.fractional:                      # T51 R4, ruling A: keyed by leg on a fractional run only
                    kind = self._order_type_of(f["tag"])
                    entry["order_type"], entry["leg"] = kind, {"MARKET": "remainder", "LIMIT": "whole"}.get(kind)
                if judged["judged_on"] is not None:
                    entry["judged_on"] = judged["judged_on"]
                entry["verdict"] = judged["verdict"]
                self._write(entry)
                self._sync_journal()                     # on disk before the fill counts as applied
                self._seen_fill_ids.add(f["id"])
                self.fills_journaled += 1
                self.journaled_symbols.add(f["symbol"])
                self.journaled_units[f["symbol"]] = self.journaled_units.get(f["symbol"], 0.0) + abs(float(f["units"]))
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
            if self.shared:                              # T51c S4c (R24): a shortfall alone is a hold, not a fault
                held = self._shortfall_stop(self.first_result, second, still)
                if held is not None:
                    raise _Stop("shortfall", held)
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
                if res.get("anomalies") or res.get("position_differences") or res.get("position_shortfalls"):   # (d)
                    return None                        # T51c S4c (K4): a shortfall is never "the venue could not be read"
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
            counts = [f"{len(result.get(k) or [])} {k}" for k in ("unresolved", "anomalies", "position_shortfalls")
                      if result.get(k)]
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
        shorts = result.get("position_shortfalls") or {}      # T51c S4c (K2): a shortfall's words are the ledger's own
        if shorts:
            reasons.append(self._shortfall_words(shorts))
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

    def _quote_fresh(self, sym: str, max_age: float = QUOTE_MAX_AGE_SECONDS, now: float | None = None) -> bool:
        """Whether the quote in hand for `sym` is within `max_age` (QUOTE_MAX_AGE_SECONDS for a holding; the order's
        own quote PRIMARY_QUOTE_MAX_AGE_SECONDS; a MARKET leg's collar MARKET_QUOTE_MAX_AGE_SECONDS) on the clock now, or
        on the reading `now` when given."""
        t, now = self.quote_times.get(sym), (self._now() if now is None else now)
        return t is not None and -QUOTE_MAX_AHEAD_SECONDS <= now - t <= max_age

    def _refresh(self, sym: str, quote: float, held: dict, in_hand: dict,
                 market: bool = False) -> tuple[float | None, str | None, list[str]]:
        """At most one ask of the quote function before an order is judged: the order's own symbol when its quote in
        hand is older than PRIMARY_QUOTE_MAX_AGE_SECONDS (always: it is the quote the order is priced, judged and sent
        on; the answer is usable only within that same age), and every holding whose quote in hand is older than
        QUOTE_MAX_AGE_SECONDS and was last asked for QUOTE_ASK_GAP_SECONDS or more ago. Returns
        (the order's quote, None, the holdings still without a fresh quote), or (None, why, []) when the order's own
        quote asked for again is not usable. Usable answers go into `in_hand` with their times. T51 R5 (B1): a MARKET
        leg (`market`) also asks for its own symbol when its quote is older than MARKET_QUOTE_MAX_AGE_SECONDS and takes
        the answer only when it is usable and newer; otherwise it keeps the quote in hand (the collar then judges it)."""
        own = not self._quote_fresh(sym, PRIMARY_QUOTE_MAX_AGE_SECONDS)
        fresher = market and not own and not self._quote_fresh(sym, MARKET_QUOTE_MAX_AGE_SECONDS)
        now = self._now()
        holdings = [s for s, u in sorted(held.items()) if u > UNITS_EPS and s != sym and not self._quote_fresh(s)
                    and now - self.asked.get(s, -math.inf) >= QUOTE_ASK_GAP_SECONDS]
        ask = ([sym] if own or fresher else []) + holdings
        raw, failed = {}, None
        if ask:
            for s in ask:
                self.asked[s] = now
                self.ask_counts[s] = self.ask_counts.get(s, 0) + 1
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
            old = f"the quote is older than {_age_words(PRIMARY_QUOTE_MAX_AGE_SECONDS)}"
            if failed is not None:
                return None, f"{old} and asking for it again failed: {_venue_error(failed)}", []
            price, t, why = _read_quote(raw.get(sym), now, PRIMARY_QUOTE_MAX_AGE_SECONDS)
            if price is None:
                return None, f"{old} and the quote asked for again is not usable: {why}", []
            self.quote_times[sym], quote = t, price
        elif fresher and failed is None:
            price, t, _ = _read_quote(raw.get(sym), now)
            if price is not None and t > self.quote_times.get(sym, -math.inf):
                self.quote_times[sym], quote = t, price
        in_hand[sym] = quote
        return quote, None, sorted(s for s, u in held.items() if u > UNITS_EPS and not self._quote_fresh(s))

    def _asked_this_second(self, sym: str) -> bool:
        """T51 B repair F6: whether `sym` was last asked for in the same WHOLE clock second as now (the clock's floor),
        so a fraction of a second never counts as time for a quote to have moved on. Never asked: False."""
        asked = self.asked.get(sym)
        return asked is not None and math.floor(asked) == math.floor(self._now())

    def _ask_again(self, sym: str, in_hand: dict) -> bool:
        """T51 B5: ask the order step's own quote path ONCE more for `sym` (nothing else), and take the answer only when it
        is usable and newer than the quote in hand; True when taken (it goes into `in_hand` with its time). A failure to
        ask or an answer that is not usable or newer leaves everything as it was: the collar then judges the old quote."""
        self.asked[sym] = self._now()
        self.ask_counts[sym] = self.ask_counts.get(sym, 0) + 1
        try:
            raw = dict(self.quotes([sym]) or {})
        except Exception:
            return False
        price, t, _ = _read_quote(raw.get(sym), self._now())
        if price is None or not t > self.quote_times.get(sym, -math.inf):
            return False
        self.quote_times[sym], in_hand[sym] = t, price
        return True

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
    def _sale_cap(self, sym: str) -> tuple[float, float, float | None, str | None]:
        """CF-C: (cap, book settled, account sellable, why) for a sale of `sym`: the cap is the smaller of the book's
        settled units of it (book_settled_units on the run's calendar and New York day, worked out once a run) and
        the quantity the account lets a sale draw on (the venue's `sellable`, read once per sale). Either one that
        cannot be had makes the cap 0 (fail closed), and `why` says which: a venue without `sellable`, one whose read
        raises or gives anything but a finite number from 0 to MAX_UNITS, or a book whose figures cannot be read."""
        if self._settled_book is None:
            try:
                got = book_settled_units(self.ledger.fills(), self.ledger.orders(), self.entries, self.calendar, self.day)
                self._settled_book = (got, None)
            except Exception as exc:
                self._settled_book = ({}, f"the book's settled units could not be worked out: {_local_error(exc)}")
        figures, why = self._settled_book
        settled = float(figures.get(sym, 0.0))
        account = None
        try:
            answer = self.venue.sellable(sym)
            account = None if isinstance(answer, bool) else _as_float(answer)
            if account is None or not 0.0 <= account <= MAX_UNITS:
                account = None
                why = why or "the account's sellable quantity is not a finite number from 0 to the largest size"
        except Exception as exc:
            why = why or f"the account's sellable quantity could not be read: {_venue_error(exc)}"
        cap = 0.0 if why is not None or account is None else min(settled, account)
        return cap, settled, account, why

    def _capped_sale(self, sym: str, units: float) -> tuple[float | None, float]:
        """CF-C: a sale of `units` (below 0) cut to its cap (_sale_cap), rounded toward zero to the run's step; the
        cap and its inputs go into the summary's `sale_caps`, the units held back into `sales_left_unsettled`. A sale
        cut to nothing is skipped with the reason and gives (None, cap); otherwise (the units, cap)."""
        cap, settled, account, why = self._sale_cap(sym)
        cut = round_units(max(units, -cap), self.fractional)
        self.sale_caps[sym] = {"cap": cap, "book_settled": settled, "account_sellable": account, "wanted": units,
                               "units": cut, **({"why": why} if why is not None else {})}
        if cut != units:
            self.sales_left_unsettled[sym] = float(Decimal(repr(cut)) - Decimal(repr(units)))
        if abs(cut) > UNITS_EPS:
            return cut, cap
        inputs = f"the book's settled units {settled:g}, the account's sellable quantity " + \
            ("not known" if account is None else f"{account:g}")
        self.skipped.append({"symbol": sym, "units": units, "reason": f"the sale cap is {cap:g} ({inputs}"
                                                                     f"{'; ' + why if why else ''}): nothing is sold"})
        return None, cap

    def _differences(self, book, per_strategy, prices, why_not, min_value) -> list[dict]:
        holdings = strategy_holdings(self.ledger.orders())
        out = []
        funds = sorted(set(self.target_units) | set(book))
        clash = {}                                       # T51 A3 (b): a fund's tag that is another fund's remainder tag
        if self.fractional:
            for sym in funds:
                if sym.endswith(".r") and sym[:-2] in funds:
                    clash.setdefault(sym, sym[:-2])
                    clash.setdefault(sym[:-2], sym[:-2])
        for sym in funds:
            target, owned = self.target_units.get(sym, 0.0), book.get(sym, 0.0)
            delta = target - owned
            if abs(delta) <= UNITS_EPS:
                continue
            if _is_crypto(sym):
                self.skipped.append({"symbol": sym, "reason": "crypto is not traded by the order step"})
                continue
            units = round_units(delta, self.fractional)
            cap = None
            if units < 0:
                units = round_units(max(units, -max(owned, 0.0)), self.fractional)   # a sale never exceeds the book
                if units < 0:                            # CF-C: ... nor its settled-quantity cap
                    units, cap = self._capped_sale(sym, units)
                    if units is None:
                        continue
            if abs(units) <= UNITS_EPS:
                continue
            if sym in clash:                             # refused before anything is sent for either fund
                base = clash[sym]
                self._refuse({"symbol": sym, "units": units}, "order step",
                             f"the fund {base}.r's whole-share tag would equal the fund {base}'s remainder tag "
                             f"({self.batch}-{base}.r), and no two orders may share a tag, so neither fund is sent in "
                             "this run")
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
                if self.fractional:                      # T51 R4: the split decides the legs, and nothing else
                    split = split_difference(delta, quote, "buy" if delta > 0 else "sell", max(owned, 0.0), cap=cap)
                    if float(split.difference) != units:
                        raise OrderStepError(f"the split's difference {split.difference} is not the order step's "
                                             f"{units!r}")
                    shares = netted[0]["parts"]
                    if split.target_zeroed and split.legs:   # the target taken as zero: each strategy sells its own
                        zeroed = net_orders([{"strategy": s, "symbol": sym, "units": -h[sym]} for s, h in holdings.items()
                                             if sym in h])
                        if not zeroed:
                            raise OrderError("no strategy holds a part of this fund to sell")
                        shares = zeroed[0]["parts"]
                    legs = [(leg, order_parts(shares, float(leg.units))) for leg in split.legs]
                else:
                    parts = order_parts(netted[0]["parts"], units)
            except (OrderError, OrderStepError) as exc:
                if isinstance(exc, OrderStepError) and not self.fractional:
                    raise                                # whole shares: as before
                what ="the fund's difference could not be split" if isinstance(exc, OrderStepError) else \
                    "the strategies' parts could not be worked out"
                self.refused.append({"symbol": sym, "units": units, "by": "order step", "reason": f"{what}: {_describe(exc)}"})
                self._write({"event": "refused", "symbol": sym, "units": units, "by": "order step",
                             "reason": self.refused[-1]["reason"]})
                continue
            if not self.fractional:
                out.append({"symbol": sym, "units": units, "quote": quote, "value": value, "parts": parts, "owned": owned})
                continue
            if split.residual != 0:                      # T51 R1: on the summary; never a fault, an attention item or drift
                self.residuals.append({"symbol": sym, "units": float(split.residual), "quote": quote,
                                       "value": float(split.residual_value), "why": split.why})
            whole = any(leg.leg == "whole" for leg in split.legs)
            for leg, parts in legs:
                u = float(leg.units)
                c = {"symbol": sym, "units": u, "quote": quote, "value": abs(u) * quote, "parts": parts, "owned": owned,
                     "leg": leg.leg, "order_type": leg.order_type}
                if leg.leg == "remainder":
                    c["after_whole"] = whole             # sent only after this fund's LIMIT leg of this run passed
                out.append(c)
        if self.live is not None and self.live_open and self.first_contact:   # T51 B4: one live remainder leg, at most
            remainders = [c for c in out if c.get("leg") == "remainder"]
            keep = self._first_contact_pick(remainders)
            for c in remainders:
                if c is not keep:
                    out.remove(c)
                    self._hold_live(c, "first_contact", "first contact: no live remainder fill has reconciled cleanly yet, "
                                                        "so at most one live MARKET remainder leg goes in a run; this one "
                                                        "waits for a later run")
        return out

    def _hold_live(self, c: dict, reason: str, words: str) -> None:
        """T51 B3, B4: a live remainder leg that is not a candidate is a residual of its fund, with the reason's name;
        never a fault, an attention item or drift."""
        self.residuals.append({"symbol": c["symbol"], "units": c["units"], "quote": c["quote"], "value": c["value"],
                               "why": words, "reason": reason})

    def _first_contact_pick(self, remainders: list[dict]) -> dict | None:
        """T51 B4: the one remainder leg a first-contact run may send: on an approval the fund of the ticket's remainder
        line (the leg the owner saw proposed), else the smallest by value at its quote (the least real money first),
        the fund's name deciding a tie. None when there is none."""
        if not remainders:
            return None
        if self.mode == "approve":
            named = {ln["fund"] for ln in self.ticket_lines if ln.get("leg") == "remainder"}
            for c in remainders:
                if c["symbol"] in named:
                    return c
        return min(remainders, key=lambda c: (c["value"], c["symbol"]))

    # -- 8, 9, 10
    def _send(self, candidates, baseline, book, prices, cash) -> None:
        if self.shared:                                  # T51c S4c (R30, R31): the baseline is never held, priced or counted
            held = dict(book)
        else:
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
        if self.shared:                                  # T51c S4c (R27): the book_nav row, never the equity table
            self.in_hand = in_hand
            opened = sum(1 for u in held.values() if u > UNITS_EPS)
            self._shared_account(in_hand, opened)        # the first look: the run's row is written here
            previous = prior_peak = row = None
        else:
            equity = self._equity(cash, held, in_hand)
            previous, prior_peak = self.state.previous_equity(self.day), self.state.peak()
            row = self.state.record_equity(self.day, equity)
            self.broker_equity = equity
            opened = sum(1 for u in held.values() if u > UNITS_EPS)

        def account_now() -> dict:
            """The account at the quotes in hand now (a holding without a fresh quote at its last usable price). The
            day's row (and so the peak) is kept at the same prices."""
            if self.shared:                              # the book at the quotes in hand: its value, loss and drawdown
                return self._shared_account(in_hand, opened)
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
        book_budget = None                               # T51c S4c (step 5): the book's own usable cash is a third bound
        if self.shared:
            usable = self.last_fig.usable_cash
            book_budget = float(usable) * (1.0 - CASH_KEPT) if usable is not None and usable > 0 else 0.0
            budget = min(budget, book_budget)
        words = {"cash": f"the purchases would exceed the cash the venue reports less {CASH_KEPT:.0%} ({cash_budget:.2f})",
                 "settled": f"the purchases would exceed the settled cash the venue reports less {CASH_KEPT:.0%} "
                            f"({(settled_budget or 0.0):.2f})"}
        if book_budget is not None:
            words["book"] = (f"the purchases would exceed the book's usable cash less {CASH_KEPT:.0%} ({book_budget:.2f}): "
                             "the account may hold more, but the book spends only its own")
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
            if book_budget is not None and cost > book_budget - spent:   # the book's own bound: the one with least left
                lefts = {"cash": cash_left, "settled": settled_left, "book": book_budget - spent}
                return min((k for k in lefts if cost > lefts[k]), key=lambda k: lefts[k])
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
        def order_key(c):                                # T51 R9: every leg is one order, sales first, by value;
            return -c["value"], c["symbol"], c.get("leg") == "remainder"   # a fund's LIMIT leg before its remainder
        sales = sorted((c for c in candidates if c["units"] < 0), key=order_key)
        buys = sorted((c for c in candidates if c["units"] > 0), key=order_key)
        queue = sales + buys
        todo = list(enumerate(queue))                    # T51 B5: a MARKET leg may be put back at the front, once
        while todo:
            i, c = todo.pop(0)
            sym, units = c["symbol"], c["units"]
            market = c.get("order_type") == "MARKET"     # T51 R4: the remainder leg, by quantity, behind its collar
            owned_here = held.get(sym, 0.0)
            if c.get("leg") == "remainder":              # T51 R4: judged on top of its fund's LIMIT leg of this run
                owned_here = math.fsum((owned_here, self.whole_passed.get(sym, 0.0)))
            reduces = units < 0 and owned_here > 0 and abs(owned_here + units) < abs(owned_here)
            if market and self.live is not None and not self.live_open:   # T51 B3: never a live MARKET leg unopened
                self._refuse(c, "order step", "a live MARKET leg is sent only when the practice proof, the owner's consent "
                                              "and the shared book all hold; this run's opening is shut")
                continue
            if c.get("leg") == "remainder" and c["after_whole"] and sym not in self.whole_passed:
                self._leave(c, self._gate_words(sym))     # T51 R4, R9, A3 (a): by the LIMIT leg's state, named
                continue
            if self._now() > self.window_end:
                self._leave(c, "the session window closed during the run")
                continue
            if sent_today >= cap:
                self._leave(c, f"the day's cap of {cap} orders is reached")
                continue
            if units > 0 and purchases_stopped:            # left over; labelled by the budget that stops it now
                q = in_hand.get(sym, c["quote"])
                lp = limit_price(units, q)
                self._leave(c, words[money(units * (lp if lp is not None else q)) or purchases_stopped],
                            collar="value" if market else None)   # T51 R5 (B5): r x c, the collar's own price
                continue
            if abs(units) > MAX_UNITS:
                self._leave(c, f"the order is above {MAX_UNITS:,.0f} units in size")
                continue
            quote, judged = in_hand.get(sym, c["quote"]), None   # the latest quote in hand (a holding's may be newer)
            c.setdefault("asks_at_start", self.ask_counts.get(sym, 0))   # T51 B repair3: asks of this leg's symbol so far
            for round_ in (1, 2):
                quote, stale, unpriced = self._refresh(sym, quote, held, in_hand, market and not c.get("reasked"))   # 1. one ask
                                                         # (T51 B5: a leg put back after its re-ask asks no more)
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
                    purchases_stopped = money(cost)
                    if purchases_stopped is None:        # it fits what each has left; the smallest budget names it
                        purchases_stopped = ("cash" if settled_budget is None or cash_budget <= settled_budget
                                             else "settled")
                        if book_budget is not None and book_budget < (cash_budget if purchases_stopped == "cash"
                                                                       else settled_budget):
                            purchases_stopped = "book"
                    self._leave(c, words[purchases_stopped], collar="value" if market else None)   # T51 R5 (B5)
                    break
                self._check_environment()                # 3. the environment and host, read again
                priced_at = self._now()                  # CF-Q2: the moment the order is priced, on the clock
                if self._quote_fresh(sym, PRIMARY_QUOTE_MAX_AGE_SECONDS, priced_at):   # 4. the quote still fresh: judged at it
                    judged = (quote, limit, order, cost, self.quote_times[sym], priced_at)
                    break
                if round_ == 2:                          # ... stale again: back to 1 once, then left over
                    self._leave(c, "the quote went stale again while the order was checked")
            if judged is None:
                continue
            quote, limit, order, cost, quote_t, priced_at = judged
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
            if market and self.mode != "propose":        # T51 R5: the collar, before its intend (a line is not an order)
                if (not c.get("reasked") and not self._quote_fresh(sym, MARKET_QUOTE_MAX_AGE_SECONDS)
                        and self.ask_counts.get(sym, 0) == c["asks_at_start"]       # T51 B repair3: the refresh has not asked
                        and not self._asked_this_second(sym)):
                    c["reasked"] = True                  # T51 B5: past 120 s at the moment of judging, and not asked for in
                    if self._ask_again(sym, in_hand):    # this very second: ask once more, right before the collar; a newer
                        if self.live is not None and units > 0 and self.live_buys:   # usable quote is taken, and the leg is
                            self.live_buys.pop()         # judged again from the top on it (its collar price, its value, the
                        todo.insert(0, (i, c))           # gate, the budget, the caps and the second source), once
                        continue
                held_back = self._collar(c, units, quote)
                if held_back is not None:
                    self._leave(c, held_back[1], collar=held_back[0])
                    continue
            tag = f"{self.batch}-{sym}" + (".r" if c.get("leg") == "remainder" else "")
            watch = _SubmitWatch(self.venue, {"fractional_live": True} if market and self.live is not None else None)
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
                line = {"line": len(self.ticket_lines), "fund": sym, "side": order["side"], "units": units,
                        "quote": quote, "limit": limit, "value": abs(units) * quote}
                if self.fractional:                      # ruling B: a remainder line holds its collar as its limit
                    line["leg"] = c["leg"]
                self.ticket_lines.append(line)
                sent_today += 1                          # counted against the caps as if it were sent
                spent += cost
                if units > 0 and owned_here <= UNITS_EPS:
                    opened += 1
                if c.get("leg") == "whole":
                    self.whole_passed[sym] = units
                continue
            try:
                if market:                               # no limit price; its collar is the price a LIMIT leg would carry
                    self.ledger.intend(tag, sym, units, quote, batch=self.batch, parts=c["parts"], order_type="MARKET",
                                       collar_price=limit)
                else:
                    self.ledger.intend(tag, sym, units, quote, limit_price=limit, batch=self.batch, parts=c["parts"])
                self.state.note_sending(tag, self.day)
                self._check_environment()                # 6. the environment and host again ...
                late = self._last_look(sym, reduces)     # ... the quote's age, the halt and latch files ...
                if late is None and self.mode == "approve":
                    late = self._ticket_held()           # ... the ticket still this run's ...
                lost = late_collar = None
                if late is None and self.live is not None:   # ... T22d-1 (A2): the run lock still the file it took ...
                    late = lost = self._lock_lost("nothing more is sent")
                if late is None:                         # ... then one reading: the window (and the ticket's life)
                    now = self._now()                    # ... nothing between it and submit_once
                    if now > self.window_end:
                        late = "the session window closed during the run"
                    elif self.mode == "approve" and self._ticket_expired(now):
                        late = "the ticket expired before the order was sent"
                    elif market:                         # T51 R5 (B4, B1): a MARKET leg, on that same reading
                        late_collar, late = next(((n, w) for n, w in (("clock", self._market_clock(now)),
                                                                      ("quote_age", self._market_quote_age(sym, now)))
                                                  if w is not None), (None, None))
                if late is not None:
                    self.state.note_outcome(tag, "not sent")             # it stays intended, never sent
                    self._leave(c, late, collar=late_collar)
                    if lost is not None:                 # the lock gone: the run stops here
                        self._hold_remainders(c, queue[i + 1:])
                        raise _Stop("lock", lost)
                    continue
                rec = self.ledger.submit_once(watch, tag)
            except OrdersBlocked:
                self._hold_remainders(c, queue[i + 1:], tag)
                words = "; ".join(self._ledger_words(self.ledger.orders(OPEN_STATES), {}))
                raise _Stop("send", f"the ledger refused to send {tag}: {words}"[:1000]) from None
            except (OrderError, sqlite3.Error) as exc:
                self._hold_remainders(c, queue[i + 1:], tag)
                raise _Stop("send", f"order {tag} could not be recorded or sent: {_local_error(exc)}; the next run's "
                                    "reconcile settles it") from None
            outcome = rec["state"] if rec.get("state") in STATES else "unknown"
            self.state.note_outcome(tag, outcome)
            if outcome == "refused":
                why = f"the venue did not take it: {_venue_error(watch.error)}" if watch.error is not None else "the venue did not take it"
                self._refuse(c, "venue", why, tag=tag)
                continue
            took = outcome == "accepted" or (self.fractional and outcome in ("partly_filled", "filled"))   # A3 (a)
            if not took:
                if watch.error is not None:
                    why = f"the venue's call raised {_venue_error(watch.error)}"
                elif watch.called:
                    why = f"the venue answered {_status_word(watch.answer)}, not 'accepted'"
                else:
                    why = "the venue was not called"
                self._write({"event": "unknown", "tag": tag, "symbol": sym, "units": units, **_leg_of(c), "state": outcome,
                             "reason": why})
                self._hold_remainders(c, queue[i + 1:], tag)
                raise _Stop("send", f"the outcome of order {tag} ({sym} {units:+g}) is {outcome}: {why}; nothing more is "
                                    "sent until a reconcile settles it")
            sent_today += 1
            spent += cost
            if units > 0 and owned_here <= UNITS_EPS:
                opened += 1
            if c.get("leg") == "whole":
                self.whole_passed[sym] = units
            entry = {"symbol": sym, "units": units, "side": order["side"], "limit_price": limit, "quote": quote,
                     "value": abs(units) * quote, "tag": tag, "parts": c["parts"], "reduces_risk": bool(reduces)}
            if self.fractional:                          # ruling A: these fields only on a fractional run
                if market:
                    entry["limit_price"], entry["collar_price"] = None, limit
                entry["order_type"], entry["leg"] = c["order_type"], c["leg"]
            self.sent.append(entry)
            self._write({"event": "order", **entry, "state": outcome,   # CF-Q2, at the line's end: the quote's own
                         "quote_ts": _iso_utc(quote_t), "quote_age_s": round(priced_at - quote_t, 3)})   # time, its age priced
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
        if not self._quote_fresh(sym, PRIMARY_QUOTE_MAX_AGE_SECONDS):
            return "the quote went stale before the order was sent"
        if _present(self.halt_file):
            return "the operator halt file is there at the last look before sending"
        if not reduces and _present(self.latch_file):
            return "the drawdown latch is there at the last look before sending"
        return None

    def _leave(self, c: dict, reason: str, collar: str | None = None) -> None:
        """Left over, with its reason; `collar` names the check of T51 R5 that held a MARKET leg back (B1 quote_age,
        B2 second_source, B3 quote_move, B4 clock, B5 value, B6 minimum), on its line and in the summary."""
        item = {"symbol": c["symbol"], "units": c["units"], **_leg_of(c), "reason": reason}
        if collar is not None:
            item["collar"] = collar
        self.left_over.append(item)
        self._write({"event": "left_over", **item})
        self._ticket_note(c, "not sent", reason=reason)
        self._whole_failed(c, f"was left over ({reason})")

    def _whole_failed(self, c: dict, fate: str) -> None:
        """T51 A3 (a): what became of a fund's whole-share LIMIT leg that did not pass, the first time it is said."""
        if c.get("leg") == "whole":
            self.whole_fate.setdefault(c["symbol"], fate)

    def _gate_words(self, sym: str) -> str:
        """T51 R4, A3 (a): why a fund's remainder leg is held back: its LIMIT leg of this run did not pass, and how."""
        fate = self.whole_fate.get(sym, "was not accepted by the venue")
        if self.mode == "propose":
            return (f"its fund's whole-share LIMIT leg of this run did not become a line of this ticket (it {fate}), so "
                    "the remainder is not sent in this run")
        return f"its fund's whole-share LIMIT leg of this run {fate}, so the remainder is not sent in this run"

    def _hold_remainders(self, c: dict, rest: list[dict], tag: str | None = None) -> None:
        """T51 A3 (a), just before the run stops at the leg `c`: a whole leg's fate by its state in the ledger, then a
        held-back line for every remainder still to come whose fund's LIMIT leg did not pass. A failure here is
        swallowed: the stop's own reason is what the run reports."""
        try:
            if c.get("leg") == "whole" and c["symbol"] not in self.whole_passed:
                rec = self.ledger.order(tag) if tag else None
                state = rec.get("state") if isinstance(rec, dict) and rec.get("state") in STATES else "unknown"
                self._whole_failed(c, f"has the state {state} in the ledger after its submit was tried, with no answer "
                                      "that it was accepted")
            for r in rest:
                if (r.get("leg") == "remainder" and r.get("after_whole") and r["symbol"] in self.whole_fate
                        and r["symbol"] not in self.whole_passed):
                    self._leave(r, self._gate_words(r["symbol"]))
        except Exception:
            pass

    def _collar(self, c: dict, units: float, quote: float) -> tuple[str, str] | None:
        """T51 R5: why a MARKET leg is held back before its intend, as (the check's name, words), or None. In order:
        B4 the clock; B1 its quote's age; B3 the quote's move from the one it was decided at (an approval: the ticket's
        line); B6 the minimum at this quote; B2 the second price source's verdict on this quote, asked last and only
        when the others pass. B5 is the caller's: the collar price c is this leg's `limit` (the price a LIMIT leg would
        carry), charged as r x c to the purchase budget, and (live) to max_order_value and max_capital. B1 and B4 are
        judged again at the moment of sending."""
        sym, now = c["symbol"], self._now()
        why = self._market_clock(now)
        if why is not None:
            return "clock", why
        why = self._market_quote_age(sym, now)
        if why is not None:
            return "quote_age", why
        ref = self._line(c)["quote"] if self.mode == "approve" else c["quote"]
        if not abs(quote - ref) <= TICKET_QUOTE_MOVE * ref:
            return "quote_move", (f"the quote {quote:g} is more than {TICKET_QUOTE_MOVE:.1%} from the quote {ref:g} the "
                                  f"leg was {'proposed' if self.mode == 'approve' else 'decided'} at; a MARKET leg goes "
                                  "only within that band")
        if not market_leg_clears_minimum(units, quote):
            return "minimum", (f"at the quote {quote:g} the remainder of {abs(units):g} units no longer clears the "
                               f"minimum of {FRACTIONAL_MIN_VALUE} with its margin of {TICKET_QUOTE_MOVE:g}")
        verdict = self._second_source_verdict(sym, quote)
        if verdict != "agree":
            return "second_source", (f"the second price source says {verdict} for the quote {quote:g}; a MARKET leg "
                                     "goes only when it agrees")
        return None

    def _market_clock(self, now: float) -> str | None:
        """T51 R5 (B4): why `now` is outside the MARKET legs' window, or None."""
        start, end, why = self.market_hours or (None, None, "it was not worked out")
        if why is not None:
            return f"no window for MARKET legs could be worked out ({why})"
        if not start <= now <= end:
            after, before = (int(x.total_seconds() // 60) for x in (MARKET_AFTER_OPEN, MARKET_BEFORE_CLOSE))
            at = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK).strftime("%H:%M:%S")
            return (f"a MARKET leg is sent only from {_hhmm(start)} to {_hhmm(end)} New York time ({after} minutes after "
                    f"the open to {before} minutes before the close); it is {at}")
        return None

    def _market_quote_age(self, sym: str, now: float) -> str | None:
        """T51 R5 (B1): why the quote in hand for a MARKET leg is too old on the reading `now`, or None."""
        if self._quote_fresh(sym, MARKET_QUOTE_MAX_AGE_SECONDS, now):
            return None
        return (f"the quote is older than {MARKET_QUOTE_MAX_AGE_SECONDS} seconds at the moment of sending; a MARKET leg "
                "goes only on a quote at most that old")

    def _second_source_verdict(self, sym: str, quote: float) -> str:
        """T51 ruling C (B2): the second source's verdict on a MARKET leg's quote, as live mode asks it (`_judged`). On
        practice it is loaded (the live path's own loader) the first time it is needed, once; when it cannot be
        loaded, the verdict says so and every MARKET leg of the run is held back."""
        if self.second_opinion is None and self.second_load is not None:
            load, self.second_load = self.second_load, None
            try:
                fn = load()
            except Exception as exc:
                self.second_unloadable = type(exc).__name__
            else:
                if callable(fn):
                    self.second_opinion = fn
                else:
                    self.second_unloadable = "it is not a function"
        if self.second_opinion is None:
            return ("no second quote (the second price source could not be loaded: "
                    f"{self.second_unloadable or 'none was given'})")
        return self._judged(sym, quote)

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
        item = {"symbol": c["symbol"], "units": c["units"], **_leg_of(c), "by": by, "reason": str(reason)[:MAX_TEXT * 2]}
        self.refused.append(item)
        self._write({"event": "refused", **item, **extra})
        self._ticket_note(c, "not sent", reason=f"refused by the {by}: {item['reason']}")
        self._whole_failed(c, f"was refused by the {by} ({item['reason']})")

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
        if self.shared and self.entries:                 # T51c S4c: the book's units after the owner's entries
            try:
                book = self._shared_units()
            except Exception:
                pass
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
        if self.fractional:                              # T51 R1: each fund's residual (ruling A: fractional runs only)
            summary["residuals"] = [dict(r) for r in self.residuals]
        if self.live_fractions is not None:              # T51 B1 to B4: a live fractional run says how the opening stood
            summary["live_fractions"] = dict(self.live_fractions)   # (the only addition when it is shut: the words)
        elif self.live is not None and self.fractional_asked:   # a live run that stopped before the opening was judged
            summary["live_fractions"] = {"open": False, "why": "the run stopped before the opening for fractions was judged "
                                                               "(see its reason); it is the live run without --fractional"}
        if self.follow_names is not None:                # T32: which strategies the broker book followed
            summary["follow"] = self._follow_view()
        if self.shared:                                  # T51c S4c: the book's figures, its fees bounded, its shortfalls
            summary["shared"] = self._shared_summary()
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
        if self.sale_caps:                               # CF-C: each sale's cap, its two inputs, the units it held back
            summary["sale_caps"] = {sym: dict(c) for sym, c in sorted(self.sale_caps.items())}
            summary["sales_left_unsettled"] = dict(sorted(self.sales_left_unsettled.items()))
        summary["held_lock"] = bool(self._locked)        # BR-E: always the last key; False for a stop before the lock
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
