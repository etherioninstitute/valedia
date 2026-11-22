# Webull

The trader connects to Webull through Webull's official OpenAPI and Python SDK. Interface facts were read from
developer.webull.com on 2026-11-11 and again on 2026-11-13, and checked against SDK 3.0.4.

## What is ready

- `valedia_trader/venues/webull.py`: account list, balance, cash, positions, open orders, an order's status and
  fills, cancel, cancel-all, and stock, ETF and crypto orders in Webull's format.
- It uses Webull's test environment (`api.sandbox.webull.com`) unless told otherwise.
- It is long-only: only rows marked as stock or crypto count as holdings, open sells are deducted, and an order that
  would sell more than is free is refused before anything is sent.
- Quantities and limit prices are sent exactly as given or not at all; nothing is rounded.
- It chooses its account by class (`WEBULL_ACCOUNT_CLASS`, for example `INDIVIDUAL_CASH`, for the practice venue; live
  mode takes the class from your confirmation file instead), refuses an order that
  does not fit the account, tells a refusal by Webull apart from an unknown outcome, and paces its own calls under
  Webull's limits (accepted for test use 2026-11-13 after two more blind checks; not cleared for real money).
- No key, secret or account number can leave it: it passes on only text of a harmless shape and never hands back
  Webull's raw answer. Its request log and its login token are kept out of the repository
  (`~/.valedia/webull/<environment>/`).
- `valedia_trader/execution_and_risk/orders.py`: the order ledger. An order is sent at most once; an unknown outcome
  blocks further orders until the broker confirms it or you settle it by hand.
- `python -m valedia_trader.doctor` reports whether a key is on file, the environment, and whether real-money orders
  are confirmed.

## Connecting your account: the exact steps

### 1. Paper-trading keys (about ten minutes; approved automatically)

1. Log in at webull.com, click your avatar (top right), then **Developer Console**.
2. Top left of the console, click **paperTrade**, then **Confirm Switch**.
3. Left menu: **API Keys**, then **Apply for OpenAPI for PaperTrade**. Approval usually takes a few minutes.
4. **Register an API Application**: give it a name, keep the permissions **Market Data and Trading**, submit.
5. In the **Operate** column click **Generate Key**. The App Key and App Secret are shown once: copy both.

The paper side has its own accounts (cash, margin, events), and you can reset their funds under **My Account**.
Webull says paper market data "may be delayed or synthetic".

### 2. Put the keys where the trader reads them

Add to `~/.hermes/secrets/trading.env` (mode 600). Never paste keys anywhere else. This file holds the PAPER key
only: its `WEBULL_ENV` stays `sandbox` (or is left out), and a live key never goes here (see "Real money").

    WEBULL_APP_KEY=...
    WEBULL_APP_SECRET=...
    WEBULL_ENV=sandbox
    WEBULL_ACCOUNT_CLASS=INDIVIDUAL_CASH   # which of the key's accounts to trade: stocks and funds use the cash account

### 3. Check the connection (read-only)

    .venv/bin/python -m valedia_trader.venues.webull check

### 4. Live API access (the review takes 1 to 2 business days)

1. You need an opened Webull brokerage account.
2. Developer Console, live side: **Access Credentials**, **API Keys**, **Apply for API Service**.
3. After approval: **Register an API Application** with **Market Data and Trading**; leave **2FA Verification** on;
   under **Add More Address Segments** enter this Mac's public address so the key works only from here.
4. **Generate Key** (shown once), then put it in its own file, `~/.hermes/secrets/webull-live.env`, as
   `WEBULL_LIVE_APP_KEY` and `WEBULL_LIVE_APP_SECRET` (see "Real money"); never in `trading.env`, and never with
   `WEBULL_ENV=production` anywhere.
5. The first connection asks for a one-time approval in the Webull app.

### 5. Market data

| Data | What it needs |
| --- | --- |
| Crypto, with order-book depth | the API key only |
| Event contracts (prediction markets), with depth | the API key only |
| Stocks and ETFs, live or historical | a separate OpenAPI subscription (Nasdaq Basic for quotes, TotalView for depth), bought on the website under your avatar: **Advanced Quotes**, **OpenAPI Advanced Quotes**. A subscription in the phone app does not count, and only one device may use it at a time |

Limits, per kind of call and per key (Webull's rate-limits page, read 2026-11-13; the owner's console states 60
requests a minute for the Trading API):

| Call | Paper side | Live side |
| --- | --- | --- |
| Account list | 30 a minute | 10 in 30 seconds |
| Balance, positions | 30 a minute | 2 in 2 seconds |
| Order status, open orders, order history | 30 a minute | 2 in 2 seconds |
| Place, change, cancel an order | 30 a minute | 600 a minute |
| Most market-data calls | 30 a minute | 60 a minute (300 for stock data bought for programs) |

Going over a limit is answered with HTTP 429 and the request is not processed; Webull warns that doing it repeatedly
can get the address blocked for a time. The adapter paces itself: it stays at 80% of every limit, with
at most 48 calls a minute on the live side and 24 on the paper side, and it reads holdings at most 5 seconds before a
sale goes out. The pacing belongs to one running program: a second program on the same key is not seen by it. A normal
day for the two fund strategies is a few dozen calls. Market data is never read from Webull (the owner's rule).

## Observed in Webull's test environment (2026-11-13, read-only, the owner's paper key)

- The connection works; real-money orders report "off: test environment".
- The paper side has five practice accounts: Individual Margin, Individual Cash, Futures, Events Cash and Crypto.
  Each row of the account list carries `account_class` (`INDIVIDUAL_CASH`, `EVENTS_CASH`, `CRYPTO`, ...) and
  `account_label` beside `account_type` (only `CASH` or `MARGIN`). Crypto has its own account.
- The `account_id` the API uses is a long internal id. It is not the 8-character account number the console shows,
  so an account has to be chosen by its class, not by typing an id.
- An order that was never sent: on the Individual Cash and Events Cash accounts the answer is HTTP 417 with error code
  `OPENAPI_PARAM_ERR` and the message "Parameter error, Order not present."; on the Crypto account it is HTTP 200 with
  `{"orders": []}`. Neither is the `ORDER_NOT_FOUND` code the adapter was written to look for.
- Balances: the cash account answers with the documented fields (`total_cash_balance`, and per currency `cash_balance`,
  `settled_cash`, `buying_power`); the events account gives `buying_power` but no cash fields; an account with nothing
  in it lists positions as `[]` and open orders as `{"data": []}`.

Practice orders on the paper cash account the same night (limit buys of one SPY share far below the market, each
cancelled; the account ended with no open order and no holding):

- Outside regular hours an ordinary order is refused at once, not queued (HTTP 417,
  `OPENAPI_CAN_NOT_TRADING_FOR_FIXGW_NOT_READY_NIGHT`). The overnight session takes limit orders marked for it
  (`support_trading_session` `NIGHT`).
- An accepted order answers `{client_order_id, order_id}` and is visible in the order detail on the very next read
  (about 0.2 s later, five reads over two seconds, four orders): no "not present" gap was seen. Its first status is
  `SUBMITTED`, or `PENDING` for a moment; a cancel sent while it is `PENDING` is refused
  (`OPENAPI_ORDER_CAN_NOT_BE_CANCEL_FOR_PENDING_SUBMIT`) and works a little later.
- The same client order id sent twice is refused: `OPENAPI_TRADE_PLACE_ORDER_REPEAT`. Webull itself enforces "send
  once".
- A refusal at placement is always HTTP 417 with its own code, and the order then does not exist ("Order not
  present"). Seen: outside hours; fractional shares in the overnight session (`OPENAPI_FRACT_ONLY_DAY_ORDER`); a price
  off the step (`OPENAPI_STOCK_ORDER_PRICE_PRECISION_EXCEED`: "Price increment should be 0.01 when price is equal to
  or greater than 0.9999").
- A cancel answers `{client_order_id, order_id}` and the status is `CANCELLED` on the next read; cancelling again is
  refused (`OPENAPI_ORDER_CAN_NOT_BE_CANCEL`); cancelling an order that never existed answers "Order not present".
- Below one dollar a limit of 0.5001 was accepted but stored as 0.50, and 0.50001 was refused. So Webull may round a
  sub-dollar limit; the adapter should send at most two decimals there too.
- The adapter's own readings matched what Webull held: order status (open, nothing filled), the open-order list,
  cash (1,000,000 practice dollars), holdings (none), cancel-all.

## The final adapter against the paper account (2026-11-13)

- Accounts are chosen by class: five paper accounts, the Individual Cash one used.
- An order outside the hours of its session comes back as a refusal with Webull's code
  (`OPENAPI_CAN_NOT_TRADING_FOR_FIXGW_NOT_READY_NIGHT`), not as an unknown outcome. A session of `ALL` was refused at
  03:24 New York time and accepted at 03:58; the exact hours of each session are not documented and not yet mapped.
- A resting limit order reads as open; cancel answers `cancel requested`, a second cancel `already finished`, and
  the order then reads as cancelled. A tag never sent cancels as `no such order` and, with the option on, reads as
  no order at all.
- The same order sent twice is one order at Webull.
- Not seen yet: the answer `not yet` to a cancel.

## Fills, fees and the first order-step run (paper account, the open of 2026-11-13)

- **Fills.** A market order for 1 SPY read as pending or submitted for up to a second and as filled within 0.5 to
  4 seconds, with an average price. The holding showed in the positions answer within seconds.
- **Fees.** A purchase carries no fee (an empty list; the adapter reports none). A sale carries a list of three
  fee entries as soon as it is filled; the adapter reported 0.03 dollars on a sale of 1 SPY at 754.18, unchanged
  a minute later.
- **A fee that cannot be read (T51c R33a, 2026-11-18).** The stock, fund and crypto order status answer carries
  `fee_unreadable`: true when Webull gives a commission or fee entry that cannot be read (the fee is then None,
  never a partial sum), false when no fee is given (commission {} and fees [] on a purchase) or the fee is read.
  The ledger keeps a true value as the notice `fee_unreadable` on the order (it blocks nothing and changes no
  figure) and closes it itself when a later report of the order gives a fee that is read. Event orders are read
  as before. ROLL-BACK COST: code from before this build treats that notice as blocking and refuses orders on a
  ledger that holds one, until a person closes it. Not yet seen: what a real purchase sends in `commission`.
- **Quantities.** Up to 5 decimals (a sixth is refused, `OPENAPI_FRACT_QTY_SCALE_LESS_5`). A limit order for less
  than one share is refused unless it is marketable (`OPENAPI_FRACT_NO_MKT_LMT_QTY_GREAT_1`); 1.5 and 1.12345
  shares were accepted as limit orders; a market order for 0.5 filled.
- **One holding, one row:** two fills of the same fund read as one row with the summed quantity.
- **Selling more than held** is refused by the adapter before anything is sent.
- **Cash after a sale.** Webull adds the proceeds to `cash` and `buying_power` at once; `settled_cash` does not
  rise the same day (and a purchase lowers it at once). The order step spends against `cash`. In a cash account
  that allows buying with unsettled proceeds; before live trading it must spend against settled cash (roadmap 4.8).
- **The order step, end to end.** At 09:55 New York it read the paper book (one strategy), took minute quotes
  from the price source (not from Webull), passed the risk gate, and sent two day limit orders through the order
  ledger: 146 XLE (quote 64.54, limit 65.18) and 43 XLK (quote 170.97, limit 172.67). A second run three minutes
  later found both filled (64.55 and 170.89), journaled each fill once, saw no gap left and sent nothing.

## Still to observe there, because Webull does not document them

- the same checks on the live side before any real order (the paper side may not behave identically);
- the price steps;
- whether every holding row carries its instrument type;
- a sale and a partial fill through the order step, a cancel answered `not yet`, a day order left unfilled at the close;
- whether any answer repeats a key.

## Real money

Nothing in this repository sends a real-money order on its own, and nothing in it creates the files that allow one.
Live mode exists only as attended commands, run by you at a terminal, from the repository itself:

    .venv/bin/python -m valedia_trader.research_loop order-step --live --check
    .venv/bin/python -m valedia_trader.research_loop order-step --live --propose --capital C
    .venv/bin/python -m valedia_trader.research_loop order-step --live --approve NUMBER --capital C

`--check` places nothing: it prints each lock and, once locks 1 to 3 are met, reads the production account: it
connects to `api.webull.com` with the live pair (Webull may ask for a one-time approval in its app the first time the
key is used) and asks for the account list, to print the class and last 4 characters of the account the venue would
trade. It places, changes or cancels no order. `--propose` writes a ticket and sends no order; `--approve NUMBER` sends
only that ticket's orders, each after fresh checks. Whole shares only, unless `--fractional` is given and every lock of
"Fractions on live" (below) holds: then the remainder legs under one share go too, and the whole legs follow the split. The command line
takes no abbreviation of any flag: `--liv`, `--li`, `--l` and every other prefix are errors, never `--live`.

**The live key pair** lives in a file of its own, `~/.hermes/secrets/webull-live.env`, beside `trading.env`. The code
computes the place exactly as `<home>/.hermes/secrets/webull-live.env`, where `<home>` is your home folder as the
system's user record gives it (not `$HOME`), so no environment variable moves it. It holds exactly two lines:

    WEBULL_LIVE_APP_KEY=...
    WEBULL_LIVE_APP_SECRET=...

The format is exact: the two names once each, in either order, each line `NAME=value` ended by a single line feed (the
last one may be left off), and nothing else: no comment, no blank line, no `export`, no quote, no space or tab
anywhere, no carriage return (CRLF line ends are refused), no byte-order mark, only printable ASCII in a value, and a
value of 8 to 512 characters. Every departure is a refusal that names it (a byte-order mark as a byte-order mark, a
carriage return as a carriage return) and shows no value.

It must be a regular file (not a link, no link in a folder above it, one name on disk) and yours. It must not be
readable or writable by your group or others: mode 600 or 400 passes, 644 is refused (stricter than the owner's JSON
files, which may be 644, because this file holds a key). The folder that holds it, `~/.hermes/secrets/`, must be yours
and writable by you alone; the reader checks that folder each time it reads the file (the folders above it are checked
for links only); the doctor does not check it. A wrong permission or owner is a refusal that names the fault and
shows no value.

Only the live commands read it. The ordinary loader (the one every other command uses for `trading.env`) refuses the
live file however it is named (through `VALEDIA_ENV_FILE`, a link, another spelling of the path or a second name on
disk: it compares the real paths and the file's device and inode before reading anything), and refuses any file that
names either live name anywhere, setting nothing from it; the refusal names the fault and no value. The alerts
commands and the owner page make the same identity test before they open the trading file, and register no setting
from a file that names a live name (a copy of the live file under another name is read, refused, and its two values
are handed only to the output guard, so that they are masked wherever they would be printed).

The live commands never take a key from the process environment or from `trading.env`, fix the environment to production and the host to
`api.webull.com` in code, pass no account id (the account is chosen by the class in your confirmation, and its last 4
characters are checked before any other account read), and refuse when `trading.env` or the process environment holds
`WEBULL_ENV` other than `sandbox` (absent counts as `sandbox`) or either live name: `trading.env` stays a practice
file. The values are handed to the output guard, so they are masked in anything printed or logged.

**Needs a terminal.** Every live command, `--check` included, refuses when standard input is not a terminal, so a
job, a pipe or a schedule cannot run it. This stops an accident only: a program that fakes a terminal is not caught by
it. The other half is the doctor's `jobs` check: it fails, naming the file, when any job file in `tools/launchd/`, any
installed LaunchAgent whose label starts with `com.valedia.`, or any script such a job runs (one level down: its
program given as a path, the first argument after an interpreter such as `bash` or `python`, any argument ending in
`.sh`, `.zsh`, `.bash`, `.py` or `.command`) holds `--live` or either live name. A property list is read as launchd
reads it, text or binary, and every string in it is tested as well as its raw text. Anything the check cannot read or
find (a folder, a job file, a property list it cannot parse, a script a job runs) fails the check, named, and a
folder that does not exist on this machine is said to be so. In your LaunchAgents folder, which holds other programs'
files too, a file that cannot be read or parsed fails the check only when its name starts with `com.valedia.`; any
other is listed in the check's line as not examined.

**Practice runs never reach production.** The order step without `--live` (the 06:55 and 07:10 jobs), the events
probe (`python -m valedia_trader.venues.webull_events probe`) and the connection check
(`python -m valedia_trader.venues.webull check`) refuse to build a venue when either live name is in their process
environment or in `trading.env`, refuse any environment or host but the test one, and never open `webull-live.env`
(pointed at it, the ordinary loader refuses it before reading). The doctor's `venues` check fails while a live name is
in reach, and fails, saying why, when `trading.env` cannot be loaded. The owner page flags a row while a live name is
in the process environment, and is not built, saying so, when `trading.env` names one or is the live file. The event
practice (`python -m valedia_trader.execution_and_risk.event_practice`) is refused by the loader and the venue builder
it shares (its line names only the error's class); its own refusal, naming the live name, is not built yet.

**The files only you write**, each read without trusting it (a link, a second name, a file others can write, a
repeated key, NaN and the rest are refused):

- `config/webull_live.json`, lock 1 (the confirmation), also read by the adapter before each production order:
  `account_class` (exactly `INDIVIDUAL_CASH`), `account_ends` (the account's last 4 characters), `confirmed_by`,
  `max_order_value`, `max_capital`, `valid_until` (at most 31 days ahead) and `i_understand_this_trades_real_money`
  (`true`). `--capital` may not exceed `max_capital`.
- `config/go_live.json`, lock 2 (the checklist, judged against the paper record): `min_paper_broker_days`,
  `min_reconciled_fills`, `min_days_since_a_stopped_run`, `fills_checked_by_hand`, `min_fills_checked_by_hand` and
  `confirmed_by`.
- `config/ladder.json`, the stake ladder: `confirmed_by`, `first_stake`, `unattended`, `fractional` and
  `valid_until`. Nothing in the order path reads it yet, so it feeds no lock.

Lock 3 is `config/risk.json` (it must hold `max_orders_per_day`, 1 to 200) with the second price sources'
`ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and `FINNHUB_API_KEY` in `trading.env`; lock 4 is the ticket (propose, then
approve by number). Live mode follows only the strategies `config/broker_book.json` names.

**Not built yet:** the live kill switch and its reset; the stake ladder's use (the first stake, its steps and
`ladder.json`); unattended live runs (live mode never runs from a job).

## Fractional orders, as built (practice path; live: see "Fractions on live" below)

Webull's documentation says fractional trading is MARKET orders only, by quantity above 0 and at most 1 share, of at
least 5 dollars, in core hours. With `--fractional`, the order step on the practice path follows that shape:

    .venv/bin/python -m valedia_trader.research_loop order-step --fractional
    .venv/bin/python -m valedia_trader.research_loop order-step --propose --fractional --capital C
    .venv/bin/python -m valedia_trader.research_loop order-step --approve NUMBER --fractional --capital C

**The split.** Each fund's difference between its target and the ledger's own book (never the baseline of holdings
that were there before the first run, never the venue's holding) is rounded toward zero to 5 decimals, a sale capped
at that book, and split into its whole shares and the rest of less than one share. A target of less than one share
worth less than 5 dollars at the quote is taken as zero (a target of one share or more is never changed by this). The
whole shares go as a day LIMIT order exactly as without `--fractional`. The rest goes as a MARKET order by quantity
(never by dollar amount; the order is always `entrust_type` QTY, session CORE, no limit price) only when it is worth at
least 5 dollars at the quote less a margin of 0.5% (`TICKET_QUOTE_MOVE`), so a small move still clears the minimum.

**Two legs, two tags.** The LIMIT leg keeps today's tag `{batch}-{fund}`; the MARKET leg is `{batch}-{fund}.r`. On a
fractional run EVERY journal `order` and `fill` line, a whole (LIMIT) leg's as well as a remainder's, carries `order_type`
(LIMIT or MARKET) and `leg` (`whole` or `remainder`), and the `unknown`, `left_over`, `refused` and `dropped` lines carry
`leg`; without `--fractional` no line carries any of them and everything is written exactly as before. A fund whose own
tag would equal another fund's remainder tag is refused in words before anything is sent for either. Each leg is one
order against `max_orders_per_day` (sales first, as before). The MARKET leg goes only after its fund's LIMIT leg of the
same run was accepted by the venue (or partly or wholly filled); when the LIMIT leg was refused, left over, dropped,
cut by the day's cap or its outcome is unknown, the remainder is not sent in that run and its line says why. On a
ticket, a remainder line holds the collar price in its limit column and its leg follows from its size alone (under one
share); an approval sends each line as the leg, type and size it was proposed as, and is refused in words when its
`--fractional` differs from the ticket's. **A ticket's stored limit, collar price and quote are informational** (T51 B6):
what an approval sends is a limit (or collar price) it works out afresh from the quote it judges at that moment, as an
ordinary run does; the ticket's figures fix only the fund, the side, the leg and the size, and the quote the fresh one
must stay within 0.5% of. (The ticket file is a database with no header of its own; this is stated here and in the
order step's module notes.)

**The collar.** Before a MARKET leg is sent, six checks, each of which holds the leg back with a line naming it
(`collar` on its `left_over` entry) and is not a fault, not an attention item and not a stop (its LIMIT leg goes as
usual):
- `clock`: only from 30 minutes after the open to 30 minutes before the close (`MARKET_AFTER_OPEN`,
  `MARKET_BEFORE_CLOSE`; a half-day's close as the calendar has it), judged again at the moment of sending;
- `quote_age`: its quote at most 120 seconds old at the moment of sending (`MARKET_QUOTE_MAX_AGE_SECONDS`; LIMIT legs keep
  their 20 minutes), judged again at the moment of sending. When the quote in hand has passed 120 seconds at the moment
  the collar is about to judge it (and the clock has moved since the leg last asked for it), the order step asks its own
  quote path ONCE more, right before the collar, and takes the answer only when it is usable and newer; the leg is then
  judged again from the top on that quote (its collar price, value, the gate, the budget, live's caps and second source),
  once. A source with nothing newer leaves the leg to the collar's own `quote_age` hold-back, which is no fault. This holds
  on practice and live alike (an approval; a proposal has no collar);
- `quote_move`: the quote within 0.5% (`TICKET_QUOTE_MOVE`) of the quote the leg was decided at. This name appears on an
  ordinary run only. On an approval the ticket check runs before the collar and compares the fresh quote with the
  ticket's: a quote more than 0.5% away DROPS the leg there (a `dropped` entry with the reason in words, not named
  `quote_move`), and a remainder whose fund's LIMIT leg was dropped is left over with the reason that its LIMIT leg
  was; so on an approval a move beyond 0.5% takes both legs of a fund out at the ticket check;
- `minimum`: still worth at least 5 dollars with its margin at that quote (`FRACTIONAL_MIN_VALUE`);
- `second_source`: the second price source agrees on that very quote. On practice the run loads the same second
  source live mode loads (read-only quotes, the keys of lock 3) the first time a MARKET leg needs it; if it cannot be
  loaded, or it says anything but "agree", the leg is held back;
- `value`: its collar price c, the price a LIMIT leg of that size would carry (a purchase: the quote plus 1% rounded
  down to the cent; a sale: the quote less 1%, or less 2% when the sale reduces risk, rounded up), is stored with the
  order in the ledger (`collar_price`), and
  the leg's units times c are charged against the purchase budget like a LIMIT order's.

**The verdict on a MARKET fill.** Its average is judged against c with the rules a LIMIT fill is judged by against its
limit: an average beyond c on the wrong side is a `breach` and writes the fault word `cap` (no new word), and more
units filled than asked is judged as before. A MARKET leg's fill is never left unjudged for want of a limit.

**The residual.** A rest too small to send (under the minimum with its margin) is not sent. The residual also carries
the part the zero rule changed: when a target of less than one share worth less than 5 dollars is taken as zero, the
residual is the difference between what the rounded difference was and what the legs send, so the legs and the
residual always add up to the fund's whole difference. It is written per fund on the run's summary under `residuals`
(units, quote, value and why) and is never a fault, never an attention item and never drift. On live, a remainder held
back by first contact is a residual too, with `reason: first_contact` (see "Fractions on live", further down).

**What `--fractional` now sends on practice.** Whole-share LIMIT orders and MARKET orders of under one share, nothing
else. Before this build `--fractional` sent one LIMIT order with decimals per fund (1.5 shares, say), which the test
environment took but production refuses for fractions; those orders are gone: no LIMIT order carries decimals and no
MARKET order carries 1 share or more. The ledger file is layout 4 (columns `order_type` and `collar_price`); a layout-3
file is upgraded once, the first time it is opened for writing, every earlier row reading as a LIMIT order with
no collar; the code before this build refuses a layout-4 file in words.

**Live and `--fractional`.** Build B opened the live path for remainder legs, behind four locks and nothing else
changed; see "Fractions on live" below. Without every lock the opening is SHUT and a live `--fractional` run is exactly the
live run without `--fractional`: the same orders, residuals (none) and journal lines, the split, the zero rule and the
five-decimal units not applied at all; the only addition is the words saying why fractions are refused. With every
lock the opening is OPEN and the whole legs, too, follow the split exactly as on practice (so a whole leg can differ
from the run without the flag: a target under one share worth under 5 dollars is taken as zero, and the minimum is
judged on the five-decimal units).

**The practice proof (the lead runs it, in core hours, on the practice account).** Each item with what the design
does if the answer is the unwelcome one:
- a. A fractional MARKET purchase accepted, seen on the `order` and `fill` journal lines of a remainder leg. If it is
  refused: whole shares only; the remainder stays a residual.
- b. The decimals Webull reports for the fill and for the holding, seen on the fill line and a clean reconcile. If they
  are not 5 decimals: the rounding step and the reconcile's tolerance follow Webull's.
- c. A fractional sale, seen with a second approval at a lower capital. If it is refused: no fractional sales; what is
  left under one share when a target goes to zero is dust.
- d. A closing sale under 5 dollars, which comes up only when a target goes to zero (today the step holds such a piece
  back by its minimum, so it shows as a residual). If Webull refuses it: the dust stays a residual.

## Fractions on live (T51 build B)

`order-step --live --shared --propose --fractional`, then `order-step --live --shared --fractional --approve NUMBER`.
With `--fractional` on live, each fund's difference is split as on practice: its whole shares go as a day LIMIT order
exactly as without `--fractional`, and its rest of under one share may go as ONE MARKET order by quantity (never by
dollar amount; `entrust_type` QTY, session CORE, no limit price). That MARKET order is the only production order this
system ever sends without a limit price, and it is sent only behind four locks, all of which must hold:

1. **The practice proof** (read from the PRACTICE ledger and journal, read-only, at the start of the run). The practice
   record must hold at least one MARKET remainder PURCHASE and at least one MARKET remainder SALE that filled and
   reconciled cleanly. "This code path wrote it" means: a layout-4 ledger row with `order_type` MARKET (a collar price,
   no limit price, under one unit, state filled, every fill row applied and their units adding up to the order's), joined
   to a journal `order` line of the practice venue with that tag, `leg` `remainder` and `order_type` MARKET, and to a
   journal `fill` line with that tag. A layout-3 row (an older code path wrote it) never counts: reading it does not
   upgrade the file. "Cleanly" means: every batch involved (the order's, and each that journaled a fill of it) has a
   run summary that ended `done` with no `faults` and no shared shortfall, AND the first reconcile on the ledger AFTER the
   one that wrote the order's last fill row exists, read the positions, and shows no position difference and no shortfall
   for that fund and no anomaly or unresolved entry naming that tag. (The venue's holding changes before an order's status
   says filled; the next reconcile settles it, so the reconcile that wrote the fill is never the one that counts.) The
   reading is `order_step.practice_proof`; it writes nothing and creates nothing.
2. **Your consent, in your own file.** `config/webull_live.json`, the file the live adapter's confirmation guards already
   read, takes one more key, and only you write it. The exact line to add, beside the other keys:

       "fractional_live_consent": true

   It counts only when its value is JSON `true`. Absent, `false`, the text `"true"`, `1`, `null` or anything else is no
   consent: the run says so in words (`live_fractions.why`) and the opening is shut. The key never makes the
   confirmation itself fail, so whole-share runs go on as before. The adapter reads the file again at every order, so
   taking the line out (or setting it to anything but `true`) stops the next order at once; and because a ticket is bound to
   the digest of your files, an approval of a ticket proposed before you changed the file is refused. Nothing in this
   repository creates or edits that file. The doctor's count of unknown keys in the confirmation (when it also has a truly
   unknown key) counts this one among them; it reads `LIVE_KEYS`.
3. **The opening itself.** The live refusals of `--fractional` (the command line's and the order step's) are lifted for
   remainder legs only. The production adapter takes an order with no limit price ONLY when ALL of these hold: it is a
   MARKET order, `fractional_live` is exactly True (the order step's own vouching, per order, that this run passed locks 1
   and 2 and is the shared book's; carried as an argument of `submit`, never a global), a stock or fund (not crypto) of more
   than 0 and under 1 unit, `entrust_type` QTY, session CORE, a remainder leg's tag (ending `.r`), and the confirmation read
   for that very order holds the consent. Its per-order value cap and its account checks apply like any other order's. The
   single `place_order` call judges the payload once more (`_production_payload_ok`), so a failure of the first lock still
   leaves a second. Every other production order needs a limit as before (`_require_live_limit`). Live stays always shared:
   a remainder leg is judged by the shared book's limits exactly as a whole leg is (lock 1's `max_order_value` and
   `max_capital` at its collar price, the risk gate on her book alone, the day stop, the shortfall stop, the second price
   source's agreement), and the book's value, the reconcile and the day stop work in exact decimals on five-decimal units
   (tested with a purchase of 0.43859 and a sale of about 0.31095).
4. **First contact.** Until one live MARKET remainder leg has filled and reconciled cleanly in the LIVE record (the same
   reading as lock 1, on the live ledger and journal), at most ONE live MARKET remainder leg goes in a run: the one with
   the least money, or, on an approval, the fund of the ticket's remainder line. The other remainder legs of that run are
   held back as residuals with `reason: first_contact`: never a fault and never an attention item. Practice is not limited
   by this rule.

**A shut opening is not a stop and not a different run.** When lock 1 or 2 (or the shared book) is missing, the run does
NOT stop and does NOT turn fractional: it is, in what it reads, decides and writes, the same live run as without
`--fractional` (no split, no zero rule, no five-decimal units or minimum, no residual, the ticket not marked
fractional), because a stop, or a half-fractional run, would change the whole-share path. The one addition is the
`live_fractions` block on its summary, which says why: `open` (false), `consent`, the practice reading (`practice`:
purchases and sales found, and why if it could not be read) and `why` (what is missing, in words). When the opening is
open the block also carries `first_contact` and the live reading (`live_clean`). A ticket made with an open opening is
approved only with `--fractional` (and a ticket made with a shut one is marked as without; if the opening opens
before the approval the approval is refused, as the owner's files then differ), and the command line prints the approve
line with `--fractional`. The first MARKET order on live is therefore your own decision twice over: the practice proof
you ran, and the line you wrote.

**The quote re-ask** (B5, see the collar's `quote_age`) applies to a live remainder leg as to a practice one.

**Still true:** with `--fractional` off nothing differs from before (the output of every run is compared byte for byte
with the accepted order path's, live included); there is no dollar-amount order mode; nothing here is scheduled (live mode
never runs from a job).

## Event contracts: the Events account trades Kalshi's markets (seen 2026-11-13, test environment)

Webull's documents never name the exchange behind its event contracts. A read-only probe
(`python -m valedia_trader.venues.webull_events probe --out <file outside the repository>`; instrument lists only,
no price, order or account call) compared Webull's lists with Kalshi's public list:

| Series | Kalshi open markets | Listed by Webull as tradable under the same ticker |
| --- | --- | --- |
| NBA games (KXNBAGAME) | 64 | 64 |
| NFL games (KXNFLGAME) | 52 | 52 |
| NHL games (KXNHLGAME) | 98 | 98 |
| College football (KXNCAAFGAME) | 506 | 506 |
| Men's college basketball (KXNCAAMBGAME) | 354 | 354 |

The only markets Webull listed that Kalshi's open list did not were the previous day's games, marked not tradable.
Webull listed more than 2,000 series in eleven categories (sports 931, elections 495, politics 162, entertainment
142, financials 117 among them). Price step: one cent. So the tickers are Kalshi's, prices and history can be
read from Kalshi's public feed, and orders would go through the Events Cash account. Event orders are not built:
the adapter refuses them.

One practice order by hand in the paper Events account (10:03 New York, one NBA game market, Kalshi's public
book at 0.70 bid, 0.71 ask):

- An event order is a limit order with `instrument_type` EVENT, an `event_outcome` of yes or no, a whole quantity,
  a price in dollars, time in force DAY. A resting buy of 1 yes contract at 0.01 was accepted, read as submitted,
  showed in the open orders, and cancelled cleanly.
- A buy of 1 yes contract with a limit of 0.72 filled within 3 seconds at 0.71, Kalshi's ask at that moment. A
  sale with a limit of 0.69 filled within 10 seconds at 0.70, Kalshi's bid. So the paper side fills at the real
  book's prices, not at the limit.
- The holding is one row: the market's ticker, `instrument_type` EVENT, `event_outcome` yes, quantity, cost.
- Every event order carries one fee entry from the moment it is accepted. The round trip cost 0.03 dollars: one
  cent of spread and about one cent a contract a side. The fee entry's fields were not printed and Webull's
  real fee schedule for event contracts has not been read.
- The Events account's balance answer has buying power and market value but no cash line.

The same through the trader's own code (11:17 New York, `python -m valedia_trader.execution_and_risk.event_practice`,
the order ledger of the Events account): a resting buy of one contract at 0.05 was accepted and, on the next
run, cancelled; a buy with a limit of 0.46 filled at 0.45 and a sale with a limit of 0.43 filled at 0.44, each
journaled once with a fee of 0.01 dollars; a further run journaled nothing and sent nothing. One contract of
another game, bought by hand earlier, is in that ledger's starting position and is held to its result to see
what settlement looks like.

### Settlement (seen 2026-11-17, paper Events account; built in T45-1)

- **A losing contract vanishes at 0.** The hand-bought yes contract of `KXNBAGAME-26NOV13ATLCHI-ATL` left the
  account with no order and no fill. `--reconcile-only` then stopped: the ledger expected 1, the account showed
  0. Kalshi's public record for that ticker showed `market_type` "binary", `status` "finalized", `result` "no",
  `settlement_value_dollars` "0.0000" and `settlement_ts` "2026-11-14T03:49:44.800032Z" (close 03:47:58Z).
- **What the command now does: two witnesses.** A holding the ledger expects that the account shows at exactly 0
  is closed only when both of these agree:
  - *Kalshi's record* for that ticker has exactly the safe values: `ticker` the same, `market_type` "binary",
    `status` "finalized", `result` yes or no, a settlement value of exactly "1.0000" for yes or "0.0000" for no,
    and `settlement_ts` in the past and later than every time the program submitted an order on that name that
    filled. The record is read from `/markets/<ticker>`, and from `/historical/markets/<ticker>` only when the
    first answers 404.
  - *The account's own cash.* Every run reads buying power once, only with no order of its own open, together
    with the cash the ledger knows: from each order's current figures in the ledger (its filled contracts, its
    average as last restated, its fee total as last reported), a purchase's average times its contracts plus its
    fee paid and a sale's less its fee received, plus every settlement's payout.
    A holding gone now is confirmed only against the latest earlier reading taken after a clean reconcile with
    both figures read; a reading taken while a holding is missing never counts. That reading need not have shown
    the holding: one bought after it is sound, because every fill between the two readings is inside the known
    cash.
    Exactly, in whole cents, the buying power must have moved by the change in known cash plus the payouts of
    every holding gone now.

  The holding must also have no open order and no unapplied fill, and no other name may differ. Holdings gone
  in the same run are settled together on one figure. The closing entry is journaled once, at 1.00 a contract
  for a holding whose outcome won and 0.00 otherwise (a held `no` with result "no" wins). It names its
  `witnesses`. The fee is recorded as "none seen", because no settlement fee has been seen, not because one is
  known to be zero. After it, no order of either outcome is sent on that ticker.
- **When Kalshi's record fails, the run stops and names the fact**: a partial disappearance, a status before
  `finalized` (for example `determined`), a fraction, a void, another ticker, a missing or malformed field, or a
  read error. Webull can drop a holding before Kalshi finalizes it, so the whole Events path waits until Kalshi
  does.
- **When the cash does not confirm it**, nothing is settled and the run stops. This covers four cases: no earlier
  reading, a reading now that cannot be taken (an order still open, an unread or non-cent figure), known cash
  that cannot be computed, or an equation off by even a cent (for example a contract sold by hand for 0.60). The
  stop gives the figures, and one pending record per name waits. While one waits, a run still judges every
  other name gone: each that Kalshi's record shows finalized gets its own pending record (never a settlement on
  the equation, since the cash between the readings can no longer be put on one name), and one that it does not
  stops the run, naming it. The known cash is also unknown while an order's fee figure is older than its last
  filled contracts (a later piece reported without a fee). Nothing is sent until a person runs
  `python -m valedia_trader.execution_and_risk.event_practice --confirm-settlement ID --who NAME --note TEXT`.
  That command first checks the environment, as every run does. It then refuses while anything of the ledger is
  open or not yet journaled, and runs the same reconcile `--reconcile-only` makes. That reconcile must show the
  pending name at exactly 0. Another name that differs refuses it only when that name has no open pending
  record of its own, so names pending together are cleared one command each, in either order. Only then
  does it read Kalshi's record again and require the same safe values. It journals the settlement with
  `witnesses` "kalshi_record+person", who and the note, then reconciles and takes a fresh reading. It also refuses
  when the pending record's contracts differ from what the ledger now expects (a fill arrived since), unless
  `--units N` gives exactly that figure; it then settles N and records both. Any refusal
  journals nothing, changes no practice state and says why. If the holding is back in the account in full, no one
  settles it: `--withdraw-pending ID --who NAME --note TEXT` closes the record without a settlement, only on a
  reconcile clean apart from names with open pending records of their own. After that, a purchase on the ticker
  is refused until a run reads Kalshi's record as still trading (status exactly "active" or "open"; the record that said finalized stays in
  the state). A later run never settles a pending record by itself. The 17 November holding has no earlier
  reading, so it waits for a person.
- **What a loss is confirmed as.** A loss is confirmed by three facts: Kalshi's record says the contract lost, the
  account shows none of it, and the account's cash moved by exactly what the ledger knows, no more and no less.
  A contract transferred out, or sold by hand for a price that nets exactly zero after fees, would be recorded
  the same way. The money would still be right; only the word "settlement" would be wrong. The account's own
  order history is the third witness a later ticket adds. For a win, the cash rising by exactly the payout is
  real evidence, and the flag `win_cash_observed`, shown in every summary, turns true on the first one.
- **Not handled:** a correction Kalshi makes after `finalized` would show only as a cash difference. Any cash
  move from outside the ledger that exactly cancels a difference passes the equation. The known cash is the cash
  of everything the venue has reported readably; a charge it makes and does not report readably (a fee added
  after the last piece, answered with a fee that cannot be read) is such a move: alone the equation fails and a
  person confirms; beside a hand sale of the same size the two cancel, the cash journaled is right and the label
  (settled, where it was sold) is wrong, until T45-3 adds the account's order history and a ledger trace. An
  average the venue rounds
  to the cent can leave the known cash off by that rounding; the equation then usually fails and a person
  confirms, but it can cancel (two roundings of opposite sign, or a rounding beside a same-size hand sale), and a
  rounding that entered before the earlier clean reading cancels exactly. Three cases leave an order's cash
  unknown for good: a zero fee reported only after the fill (never stored by the ledger), a fee that falls and a
  later piece whose reported total is below the sum of the rows' fees, and a fee that falls after a piece
  reported with no fee. Each means every later settlement on that account then needs a person until the ledger is
  repaired (T45-3).

### Fees and settlement in a real account (Webull's help pages, read by the lead 2026-11-17)

- **Fees** ("Event Contract Fees", help/faq/11174). Every opening and every closing trade costs 0.01 dollars
  commission plus 0.01 dollars exchange fee a contract. Holding to settlement costs nothing, and neither does a
  cancelled or unfilled order. The paper account's 0.01 a contract is therefore not the real charge. The code
  journals whatever fee the venue reports and assumes none.
- **Settlement** ("Event Contract Settlement and Payouts", help/faq/11172; "Event Contract Funding and Buying
  Power", help/faq/11052). Winning proceeds go to the balance of the *linked brokerage account*, and losing
  positions are simply removed. Event buying power is derived from that linked account's settled cash.
- **What this means for the cash witness.** In a real account, buying power also moves with the linked brokerage
  account (its trades, deposits, withdrawals, settling stock proceeds), so there the equation will often not hold.
  The settlement then waits for a person (or, later, the account's own order history) to confirm it. The exact
  equation is meaningful as built in the paper Events account, where nothing else moves that cash. A
  `win_cash_observed` set in the paper account does not prove the live cash path.

## The shared account (being built)

The owner also trades the account the fund book is to trade in (T51c). What exists after slices S1 to S3 and S4a to
S4e (S4b wired the owner's command; S4c wired a PRACTICE run: "The shared run", below; S4d opened the LIVE venue: "A
live shared run", below; S4e added the alerts, the owner's page and the `mark` entry: "The alerts and the owner's page",
below):

- **The reconcile can compare one way** (`OrderLedger.reconcile(..., one_sided=True)`): what the account holds beyond
  the book and its unapplied fills is listed under `owner_surplus` and is no difference; an account holding LESS than
  the book is listed under `position_shortfalls`, naming the symbol, both figures and the two ordinary causes (the
  owner sold shares the book counts as its own, or a split), and makes the reconcile unclean, which stops the whole
  book until a person has looked. The default is unchanged: every run today compares both ways, as before.
- **The book's own figures** (`book_figures` in `order_step.py`, pure): units per symbol from this ledger's own fills;
  cash from the owner's entries less purchases plus sales less fees, in exact decimals, with a lower and an upper bound
  and `cash_bounded` when a price or fee is not known (an unpriced piece is valued at its limit, a MARKET leg at its
  collar; an unknown fee leaves the lower bound open, except on the purchases below); the cash usable for purchases today (the lower bound less the
  proceeds of the book's sales of this New York day); the value, the value per unit and the units outstanding. A
  deposit or withdrawal issues or redeems units at the value per unit, so it is never profit or loss.
- **The owner's entries**: the order step's state file is now layout 2 (a layout-1 file is upgraded the first time it
  is opened, every row kept; the order step before this build refuses a layout-2 file in words, so going back to it
  needs the layout-1 file back). The new table `owner_entries` is append-only and hash-chained, and each entry is
  also written as one `owner_entry` line in the journal. A change to ONE of the two (an entry changed, removed or
  added by hand in the state file, its hashes recomputed or not, or the state file put back from an older copy; or
  a journal line changed, removed or added) makes them differ, and the owner's command finds that before it writes
  and refuses in words. Compared for each entry: its id, kind, symbol, amount, units, ratio, mark, units and value
  per unit before and after, time, previous hash and hash; NOT `who` and `note` (the journal's writer may redact them), so
  a change to those two in the journal alone is not found. A journal line that is not whole is skipped as a run
  skips it; if it was an owner's entry, that entry is then missing from the journal and the command refuses. An
  entry whose time is not later than the last entry's (a clock behind the record) is refused. Someone who rewrites BOTH to match, or puts both back from one older copy, is not caught by
  this build; the nightly copies and the venue's own record are what would show it. A second new table, `book_nav`, holds the book's own value
  per unit, its bounds, the marks it was valued at and the latest entry it includes, run by run, for the day's
  loss and the drawdown; a shared run writes and reads it (S4c). The old `equity` table is kept as it was: a shared
  run neither reads nor writes it.
- **A purchase the venue reports no fee for** (T51c S4a, addendum 7, R22). Seen on the practice account on
  2026-11-18: a filled purchase has no fee on the order or its fills (empty, not zero), while each sale reports its
  fee. The rule's source: Webull's pricing page, read 2026-11-18: no commission on stocks and funds, and a
  Consolidated Audit Trail fee of 0.000003 per unit of total trade volume on buys and sells; the page does not say
  whether the volume is shares or dollars. That rate, its source and its date are ONE constant
  (`PUBLISHED_PURCHASE_FEE` in `order_step.py`); a change of the page changes it by a commit, never by a run. For a
  filled purchase whose fee total is empty and none of whose fill rows carries a fee: while the ledger still asks the
  venue about it (its recheck window: the ledger's own `recheck_seconds`, 72 hours from when it finished; an order
  not known to have finished counts as inside it), its fee is taken as between 0 and B, B being the rate times the
  LARGER of its filled units and their value at its limit (a MARKET leg: its collar), rounded up to the cent: the
  lower cash bound subtracts B, the upper nothing, the order is named in `bounded_by` with the source, and the value
  and the usable cash are known. Once that window has closed with the fee still empty, the fee is taken as none for
  both bounds and the order is counted in `fees_taken_as_none`. A fee that arrives is the fee (the earlier rules
  decide, as before). A sale with no fee, a purchase with fee rows but no total, or one with no limit or collar
  stays as before: the lower bound open, so the value is not known and the owner's command refuses, naming the
  order. `book_figures` is handed the present time (`now`, the reading its New York day was taken from; a reading on
  another day, or in milliseconds, is refused) and reads no clock; the owner's command hands it its one clock
  reading. A shared run uses it (S4c). **KNOWN GAP:** a fee the venue charges later outside the order (a monthly
  CAT charge, say), or reports only after the window has closed, is never seen by the book. Also: the ledger cannot
  tell a fee the venue left empty from fee entries the adapter could not read (both are stored empty), so a purchase
  whose fee entries could not be read is bounded by B too; and a fee of 0 reported only at a recheck is not stored by
  the ledger as built (no rise), so such an order stays bounded until its window closes.
- **A local check** (`untracked_tags`): every tag the order step wrote to the journal, a ticket or its send record
  must be in the ledger (a ledger put back from an older copy shows up here). A shared run makes it before anything
  is read from the account.

**Not wired:** nothing of build B (fractional live orders are wired: "Fractions on live", above). A run WITHOUT `--shared` is as it was,
except that a state file holding an owner's entry refuses it (below). Still not seen by the book at all: a distribution paid on the book's shares, and any order the venue holds that the
ledger never knew (the venue's order history is a later slice).

**The owner's command** (T51c S3, wired by S4b, addendum 7 R29). The entry's own figure is the owner's; the command
never proposes or defaults one, refuses a figure that is not a positive decimal written as digits, and needs `--who`
and `--note`. The kinds: `cash_in` and `cash_out` (`--amount`), `units_out` (`--symbol`, `--units`: shares he took
that the book counted as its own, which clears exactly that much of a shortfall), `split` (`--symbol`, `--ratio`: new
units per old unit; the value stays, the units change), `mark` (`--symbol` alone; S4e, R34: "this quote is real; take it as
the new mark", below; no cash and no units change).

*Marks are never typed* (`--mark` is refused in words). Every fund the book holds units of, and the entry's own symbol
for a `units_out` or a `split`, is asked of the order step's OWN quote source (the function a run is handed: the last
one-minute bar, stamped at the bar's start) and read with the run's own reader and age rule. Inside the session
(from the exchange's open to its close on that New York day, by the calendar the order step already reads, through
its `_window` with no offsets) a quote more than `QUOTE_MAX_AGE_SECONDS` old, stamped more than
`QUOTE_MAX_AHEAD_SECONDS` ahead, or missing refuses, naming the fund and why. Outside the session the last session's
close serves: the latest close at or before the clock (today's once it has passed, else the calendar's own
`previous_close`), and a quote is taken when the same age rule, judged AT that close, finds it usable: at most
`QUOTE_MAX_AGE_SECONDS` before the close, and, with the order step's own allowance for a stamp ahead, at most
`QUOTE_MAX_AHEAD_SECONDS` after it (so the last bar of the session, stamped a minute before the close, serves; a bar
stamped 30 seconds after the close is taken, and one stamped 61 seconds after it is refused; a quote from an earlier
day does not serve); its time is shown. A book holding no units asks for no quote (the opening `cash_in` works with the
network down). No new age limit, band or lifetime exists: only the order step's constants.

*A preview, then a confirm.* Run without `--confirm`, the command PRINTS the kind and its figure, each mark with its
time, source and basis (inside, or the close it stands for), the book's value with its bounds, the units outstanding
and the value per unit before and after, and a six-digit code; when the value is not known it refuses as before,
naming the order (R32). It writes NO byte of the state file, the journal or the ledger: only the one pending preview
of that venue, `memory/order_step-VENUE_NAME.owner-preview.json`, beside the state file, written atomically and
readable by its owner only (a new preview replaces the one before; no run, reconcile, ticket, journal reader, alert
or owner's page reads that file, so it is never a fault). A preview on a root with no `memory` folder yet creates
that folder to hold the file. A pending file that is not a preview the command
wrote (any of its eleven fields missing, `close` included, or of another type, a mark that is not an object with a
finite price, a code that is not six digits: a file edited by hand) is "not an owner-entry preview", and one that
cannot be read at all (not readable, not UTF-8, not JSON, or JSON nested too deeply to parse) is "the pending preview
cannot be read": either way the confirm refuses in words, spends it and writes nothing. Run again with the same arguments and `--confirm CODE`
within a ticket's life (`TICKET_LIFE_SECONDS`), it asks the quotes AGAIN and writes the entry, at the confirm's own
marks, only when every mark is within `TICKET_QUOTE_MOVE` of the preview's and nothing else differs: the arguments
(kind, figures, symbol, who, note), the chain's last hash (no entry since), the ledger's fills and orders as the
book's figures read them (no fill or order arrived or changed), the funds held, and the marks' basis (the session has
not opened or closed meanwhile). Otherwise it refuses in words saying what moved and writes nothing. Then, once it
holds the run lock and before anything is written, it reads the ledger's fills and orders and the owner's entries
AGAIN and writes only if the figures read what was valued (the same digest of the ledger as the book's figures read
it, the same last hash of the chain, the same funds held); otherwise it refuses, "a fill or an order arrived while the
entry was being confirmed" (or "an owner's entry arrived ..."; a ledger read when the entry was valued and gone now,
or one that cannot be read again, is said so), and writes nothing. No quote is asked under the lock:
the marks are the confirm's own, and the lock is never held across the network. Nothing the figures read can arrive
between that second read and the write: every writer of fill and order rows to the ledger (the order step's practice
and live runs, and the event practice on a venue of the same name) writes them only while holding this same lock
(`memory/order_step-VENUE_NAME.sqlite.lock`), and so does every writer of an owner's entry; the alerts and the
owner's page only read the ledger. A confirm that has read the pending preview and does not write SPENDS it, whatever
the reason (a wrong or old code, expired, moved, another difference, a run holding the lock, something that arrived
under the lock), except that a newer preview made meanwhile (another code) is left pending and the refusal says so:
the owner then runs the preview again and sees the figures before any entry is written, and no code is tried twice
against one preview. The code checks against a stale or mistyped
confirm; it is not a secret. The shape, with figures that are AN EXAMPLE ONLY and not a suggestion of any amount:

    order-step --owner-entry cash_in --venue VENUE_NAME --amount 1234.56 --who owner --note "example only"
    order-step --owner-entry cash_in --venue VENUE_NAME --amount 1234.56 --who owner --note "example only" --confirm CODE

`VENUE_NAME` is the name in the venue's state file, `memory/order_step-VENUE_NAME.sqlite` (under `--root` when one
is given); the ledger beside it is only read, and the confirm is refused while a run holds that file's lock. A
production venue's name (anything starting `webull-production`, compared without case and trailing dots) is refused
in every form (preview, confirm, repair) before any quote is asked unless `--live` is given with it (S4d, below); a
practice name is never taken with `--live`. A refusal writes nothing to the state file or the journal: no folder, lock file or upgrade of the state
file (it exits 2). An entry written whose journal line could not be written is said in words, with the entry's hash
and the repair to run, and exits 3; the entry stays in the state file, and every later entry is refused until the
line is written.

*The repair*: `order-step --venue VENUE_NAME --owner-entry-rejournal N --hash H --who WHO --note NOTE` writes entry
N's missing journal line exactly as the entry's command would have (the same fields, into the journal of the entry's
day), with one field more, `rejournaled` (who repaired it, why, when); the journal's writer adds its own timestamp as
to every line. Only when ALL hold, checked before the lock and again under it: the state file's chain checks; the
ONE difference between the state file and the journal is "entry N is in the state file and not in the journal"; N
is the LAST entry; H is entry N's hash, as the failed command printed it. Anything else is refused and nothing is
written. It cannot make good an entry that was changed (the chain breaks, or the journal differs in a field), lines
put in another order, a missing entry that is not the last, or an entry whose hash is not given; and, as before, an
entry appended by hand with its hash recomputed and its hash typed is not told from the owner's own.

*Live entries* (S4d, R23 reversed for `--live` only): `order-step --live --owner-entry KIND --venue webull-production
...` (and `--confirm CODE`, and the repair `--live --venue webull-production --owner-entry-rejournal N --hash H ...`)
takes the production venue's name, and everything above holds there unchanged: the preview is printed first with its
code and writes nothing, the confirm re-asks the quotes (the order step's own quote path, never typed; outside the
session the last close) and writes only if every mark is within `TICKET_QUOTE_MOVE`, the repair is for the last entry
only. The two flags go together: a production venue's name without `--live` is refused ("written only together with
--live"), and `--live` with any other name is refused ("a practice name"); `--live` with `--propose`, `--approve`,
`--check`, `--capital`, `--fractional` or `--shared` is refused as every run flag is. (A run reads the entries and the
book's figures only in the shared mode, below; no run ever reads the pending preview.)

### The shared run (T51c S4c, practice; S4d, live; `order-step --shared`)

`order-step --shared` (the keyword `shared=True` of `run_order_step`) runs the order step against an account the owner
also trades, judging the fund book on its OWN figures. On the practice venue it is a flag; on the live venue it is
required (S4d, "A live shared run", below); `--shared` with `--check` is refused. The scheduled practice job does not
pass the flag yet; the lead moves the gate and the job together.

**The stages of a shared run**, in order, each stop a `stage` the stake ladder knows (a skip counts the session as
skipped; a fault is a fault):

1. *Before the lock*: `--capital` is refused in words (stage `targets`, a skip), because the capital is the book's own
   value (the cautious, lower bound), one source of truth; a stake changes by the owner's entry and never by a figure
   typed to a run. (It replaces R6's "equal to the cent".)
2. *After the lock and the journal scan, before anything is read from the account or sent* (`_latch`): (a) the
   state file's owner entries and the journal's `owner_entry` lines must agree (stage `state`, a fault: a person must
   look); (b) every tag the journal, the send record or a ticket holds must be in the ledger (`untracked_tags`; stage
   `reconcile`, a fault: a ledger put back from an older copy); (c) the book must have its opening entry (`cash_in`
   first; stage `targets`, a skip: the owner opens it). The order is (a) before (c), so a state file replaced by an
   empty one is a fault (the journal holds entry 1), not a skip.
3. *The latch (R25)*: a state file that holds an owner's entry REFUSES a run that is not shared (stage `state`, before
   anything is read from the account): a run without the flag would scale to the paper equity, write the old
   `equity` table and break the book's value history.
4. *The reconcile is one-sided* (R24, R30): what the account holds beyond the book is `owner_surplus` (the owner's;
   on the practice book that includes the whole baseline, which is never sold, priced or counted), what it holds LESS
   than the book counts as its own is a shortfall. The book's units are the ledger's fills after the owner's entries
   (a `units_out` lowers them, a `split` multiplies them); the baseline is read by nothing in a shared run.
5. *A shortfall* stops the run at stage `shortfall`, a SKIPPED session for the stake ladder, never a fault: an
   ordinary cause exists (the owner sold shares the book counts as its own). The stop and the summary carry the
   ledger's words (the symbol, both figures, the two ordinary causes) and `shared.shortfalls` (the book's units, the
   account's, the unapplied fills' and the units short); the ledger is blocked, which the alerts already show
   (`orders:<ledger>`), and a stopped run is the alert `shared-stopped:<venue>:<class>:shortfall` ("The alerts and the owner's page", below). It is cleared only by the shares coming back
   or by the owner's `units_out` (the shares he took) or `split` entry. It is never taken as "the venue could not be
   read" (`venue_read`): a shortfall in the first reconcile whose second positions read failed is still `shortfall`.
   After the last reconcile of a run a shortfall in a symbol this run's own orders touched (open at the venue, or of
   its batch, or of a fill it journaled), of at most those orders' units or the fills' units, is set aside as a
   position difference is (the venue's positions lag its order status, seen on practice 2026-11-18: after a sale the
   holding falls before the order says filled) and listed in `reconcile_pending`; any other shortfall found by that
   last reconcile is no fault either (an owner's sale is an ordinary day): it is in `shared.shortfalls` and the next
   run's first reconcile stops at `shortfall`.
   The go-live gate reads it the same way (R32, R33): a paper run stopped at `shortfall` whose summary carries a `shared` block is a skipped session (not a stopped run; listed as `skipped_shortfall_runs`; one without the block is corrupt and is a stopped run), and a run that ended `done` with `shared.shortfalls` not empty is not clean (its fills are not reconciled fills, it is no paper broker day, the stopped-run clock is untouched; listed as `unclean_shared_runs`). The stake ladder reads such a done run as a skipped session, never a fault, and `fractional_proven` counts none of its fills.
6. *The prices come before the capital*: one quote call for the paper book's funds and the funds the book holds
   (never a baseline fund). The book's value at those quotes (lower bound) is the capital. A value not known (a fund
   held with no usable quote, or a sale whose fee is empty: the lower bound stays open) stops at `prices`, a skip,
   with the book's own words (R32). A purchase whose fee the venue left empty is bounded by the published CAT
   fee (R22) and counted: `shared.fees_bounded` ({count, orders}) and `shared.fees_taken_as_none`, never a fault.
7. *A held fund far from its last mark* (R28): a fund the book holds whose quote is further than the owner's
   `max_drawdown_pct` (config/risk.json) from its mark in the latest `book_nav` row stops the run at stage `marks`
   (a skip) before any order, because the one quote prices every order's limit and the whole book's value. A real
   split is cleared by the owner's `split` entry: the mark is divided by the ratio of every split of that fund written
   after the row; a real move of the quote is cleared by the owner's `mark` entry (S4e, R34, below): the fund's reference
   becomes the mark that entry took. No earlier row, or a fund with no mark in it, is not judged. (When the limits file cannot be read the
   check is not made: the risk gate then refuses every order with the file's own error.)
8. *A ticket* (R26): a proposal stores the value it was made at (as its `capital`) and is marked as made by a shared run
   (a ticket of an ordinary run is refused by a shared approval and the reverse: "not made in shared mode"); an
   approval recomputes the value AFTER the prices and refuses (stage `ticket`, a skip; the ticket stays unused) when it
   has moved by more than `TICKET_QUOTE_MOVE` (0.5%) from the ticket's. The exact-capital check is not made for a shared
   ticket. Lock 1's `max_capital` binds a live run only, against the book's value at its upper bound (S4d).
9. *Every limit is judged on the book alone* (R31, R27): the risk gate is given the book's value (lower bound) as the
   equity, the book's units as what is held (so the position limit and the count of positions never see the owner's
   holdings, the baseline included), and the day's loss and the drawdown on the VALUE PER UNIT, so the owner's deposits,
   withdrawals and shares taken are never profit or loss: the previous value per unit is the upper figure of the
   latest `book_nav` row of an earlier New York day (its lower figure when its upper is not known, said in
   `shared.day_loss_upper_open`), else the value per unit after the latest owner entry of an earlier day, else the
   opening entry's (1); the day's loss holds that upper figure against the present LOWER one; the peak is the largest
   upper figure of every row and now, compared as decimals (the figures are stored as text: never an SQL MAX). A
   width of the bounds (a cent a purchase at most, from R22) can hold the drawdown up by that width. `book_nav` gets one
   row at a run's first look, set again at each later look when it changed (as the equity row is) and after the last
   reconcile, so the next run has the marks of what this one bought.
10. *Purchases* spend at most the smallest of the account's cash, its settled cash and the BOOK's own usable cash (its
    cash less the proceeds of its sales that are unsettled today), each less the 1% kept; a purchase left over because
    of the book's own bound says so ("the book's usable cash", `shared`; the account may hold more).
    `broker_equity` in a shared summary is the book's value (lower bound), not the account's.

The summary of a shared run carries `shared`: `capital`, `book_value` and `book_value_high` (at the latest look),
`value_per_unit`, `units_outstanding`, `cash_bounded`, `fees_bounded`, `fees_taken_as_none`, `day_loss_pct`,
`drawdown_pct`, `previous_value_per_unit` and where it came from, `owner_surplus`, and `shortfalls` when there are
any.

**What is NOT there yet.** The fee a purchase's venue reports ABOVE the
published bound writes no journal notice (R22 (c): the bounds simply follow the reported figure); a book value for a
`units_outstanding` of nothing (every unit redeemed) stops at `targets` until the owner's entry opens it again. The
go-live gate reads a shortfall as R32 and R33 say (above).

### A live shared run (T51c S4d; `order-step --live --shared --propose`, then `--live --shared --approve NUMBER`)

A live run is ALWAYS the shared account's book (the owner also trades that account). The command line refuses `--live`
without `--shared`, in words, before anything is read, and it refuses `--capital` with it (R26: the capital is the
book's own value, so a stake is changed by the owner's entry and never by a figure typed to a run). It does not imply
`--shared`: the explicit flag keeps the printed command (`order-step --live --shared --approve NUMBER`, which the
propose prints) saying what it runs, and an old habit (`--capital C`, no flag) is refused instead of changing the mode
silently. `--live --check` is as it was. (`run_order_step` itself still accepts `live` without `shared`, for the
tests that drive it; the command line is the gate, and a live state file that holds an owner's entry refuses such a run
at stage `state` (R25).)

The owner opens the live book first: `order-step --live --owner-entry cash_in --venue webull-production --amount ...`
(preview, then `--confirm CODE`; "The owner's command", above). Without its opening entry a live shared run stops at
`targets`, a skip. A live shared run is then the shared run of the practice venue, stage by stage ("The stages of a
shared run", above), under the four locks and every live rule, with these differences:

- *Lock 1 binds the book* (R26): after the prices and the marks check, a book whose value at its UPPER bound is above
  the confirmation's `max_capital` stops the run at stage `lock` (a skip) before any order, in words (the figure, the
  ceiling and the difference; `nothing is sent`) that reach the stop and the journaled summary, with
  `shared.book_value`, `shared.book_value_high` and `ceiling`. A value exactly at the ceiling passes. A book whose
  upper value is not known (no upper bound) is refused the same way: it cannot be shown to be at most the ceiling.
  The per-order checks (`max_order_value`, the money at work against `max_capital`, the second price source on every
  mark held and every order's quote) are as before; the money at work is the BOOK's holdings.
- *The state file's value history* (T22d-1 A4, for the book), judged before anything is read from the account: every New
  York day on which this venue's journal holds a shared summary with a value per unit must have a `book_nav` row of that
  day, and the day's LATEST row must read what the journal's LAST such summary of the day says: the lower value per unit
  (`shared.value_per_unit`) and the upper one (`shared.value_per_unit_high`, journaled from S4d; None when the upper value
  was not known), exact decimals compared as decimals, never text. **The row's figures are read the way the day stop and
  the peak read them** (`book_loss`): its lower figure is `value_low` over `units_outstanding` and its upper one
  `value_high` over `units_outstanding` (the lower where the row has no upper), in one helper shared by the history check,
  the accept command, its code and its preview; the row's text `value_per_unit` column is never the basis of a safety
  decision. A row whose text disagrees with `value_low` over the units (the writer stores that very quotient, at 60
  digits, so they are equal exactly, with no rounding between them), or whose units, `value_low`, `value_high` or text are
  not finite numbers (NaN, words), or whose units are not above nothing, or whose `value_high` is below its `value_low`,
  stops the run at `state`, a fault, in words, and the accept command refuses it, writing nothing.
  **Every row's day** must be a standard date: exactly `YYYY-MM-DD`, a real calendar date, and not after the run's New York
  session day; anything else ('2026-11-12z', '2026-11-12 ', '2026-02-30', a future day) is the same `state` fault, because the
  history check looks days up as text and `book_loss` orders them as text, so a day that only looks like a date would
  escape the one and sort after the real day in the other. **An added row** on a standard date can lower the base
  `book_loss` uses (the previous value per unit is the upper figure of the latest row of an earlier day) when the journal
  holds no summary of its day and the day lies after the last journaled day before today (or there is no journaled day
  before today): such a row stops the run at `state` too ("on a day the journal holds no summary for ... it would be taken as
  the previous value per unit"). A row on a day with no summary that cannot be that base (before the last journaled day, or
  today's own) can only raise the peak and is left alone. The accept command may take such a row, held against the last
  journaled day's figures by the same tighten rule (never lower on either figure). **These checks run on every row always,
  with or without a summary in the journal** (the book's first live runs included): with no journaled day before today the
  row is held against the value per unit `book_loss` itself falls back to when no row of an earlier day gives one, the value
  per unit after the latest owner entry of an earlier day, else after the opening entry (the preview names it), and the
  accept command takes a row at or above it by the same tighten rule, so a lost first summary has a route; a row below it,
  or with no owner entry's figure to hold it against, has none (the real state file, or the lead by hand). **A known
  limit, with its stage:** a first day's only summary lost leaves that day's row without a summary, so the next day's FIRST
  run stops at `state` ("... on a day the journal holds no summary for ..."); the accept command clears it when the row is
  at or above the opening base, and a lower row has no command route (the lead, by hand). **The run's own looks**
  (each one in the run, and the last after the final reconcile) update the row first and the summary's figures only once the
  row is written, so the summary always carries the figures of the row actually written: when a reprice fails (a locked or
  failing state file) the summary keeps the figures of the row as it was, so the two never disagree and the next run is not
  stopped at `state` for it. A decimal signal while a row is read (Overflow, Underflow, InvalidOperation, DivisionByZero:
  units like 1E-999999) is the same `state` fault in the run and a refusal writing nothing in the command, never an
  `unexpected` stop or a traceback. A day with no such summary (a run that stopped before its first look) is not required
  to have a row. So an older state file put back, or a row removed, changed or ADDED (see below), stops the run at
  `state`, a fault ("the state file lacks the book's value history the journal holds: a copy put back?"): without it, a
  removed later day would change the previous value per unit and the day stop would be missed. The peak is judged as well:
  the highest figure the rows hold (a row's UPPER figure where it is finite, the drawdown's basis, else its lower) must not
  be below the journal's, by the same basis, in Decimal (never an SQL MAX over text). A ticket's `equity` is not counted in
  a shared run (a later look may lower the row it was made at). The old `equity` table is neither read nor written.
- *When that stop may not be a copy put back* (the recovery route, which may only TIGHTEN). The same stop follows a run whose
  `book_nav` row was written at its first look and whose summary never reached the journal (the summary write raised, or
  the process died between the first look and the summary), and it follows a state file put back from an older copy, or a
  row changed by hand. **The order step cannot tell these apart, and neither does the command below.** The stop's words
  name both causes, the day(s), the journal's last figures and the state file's latest row (its run's batch), and the way
  out. The way out is the owner's, on the owner-entry pattern and needing no hand-editing of the journal or the state file:
  `order-step --live --accept-history DAY --venue webull-production --who WHO --note NOTE` PRINTS what differs (the
  journal's last summary of DAY, the state file's latest row and its run, every other day that still differs) and a
  six-digit code, and writes nothing; run again with `--confirm CODE` it writes ONE journal line (`history_accepted`: the
  day, the row, its figures, what the journal had, who and why, and a hash over all of them chained to the acceptance
  before it) and nothing else (the state file is never written). **An acceptance may only tighten, never loosen:** it takes
  the day's row only when BOTH its lower figure and its upper one (the day stop's base; the lower where the upper is not
  known) are at least the journal's last figures of the day (an earlier acceptance of the day is the journal's last
  figure); a row lower on either is refused ("the row is lower than the journal's figure for DAY; accepting it would
  loosen the day stop and the drawdown; put the real state file back, or ask the lead"). The code is a digest of
  everything the preview showed, every other listed day included, recomputed from the files as they are, so a confirm
  after anything changed on ANY listed day is refused; the confirm needs the run lock. It also refuses, writing nothing:
  when nothing differs for the day, for a day the journal holds no figure of, for a day with no row, without `--live` or
  with a practice name, with any other flag, and (an extra refusal the safety does not depend on) for a row of a run
  whose summary the journal holds. At run time an acceptance line counts only if its figures are again not below the day's last figures before it (the same
  tighten rule, read again, on the journal's lines); a hand-written line that would loosen is ignored, does not move the
  chain, and the day stays stopped. The floor an acceptance is read against is the one AS IT STOOD when its line was written: the day's earlier
  journaled figures and, with none, the owner's entries journaled BEFORE it (never one made after it), so a later ordinary
  owner entry whose value per unit is higher cannot cancel an acceptance; a hand-written line that was below its own floor at
  its own time stays ignored. After an acceptance the check passes a day whose latest row reads the journal's last
  look (the acceptance) OR the last summary before it, so the real state file put back after a copy's higher row was
  accepted passes by itself; the check still stops on every other difference (another day, the same row changed
  afterwards, the peak: an acceptance adds no peak of its own). Who and note are in the hash: an acceptance edited
  afterwards clears nothing.
  **Known limits:** a LOWER row (a real fall between a summary and a lost one, or a copy put back) has no command route: it
  is cured by putting the real state file back, or by the lead by hand. A day whose row is missing from the state file has
  no route either. A summary lost on a day with no later journaled day is held like any other (the unheld-row rule above: the next day's first run stops at `state` (a later run the same day is not stopped, and its summary holds the day again) until the owner accepts the row or the real state file is put back); once a LATER day has a journaled summary, the lost day's row can no longer be the previous figure and is left alone. The acceptance's hash holds
  no secret: anyone who can write the journal can write a valid line (as they can write any summary line, which carries
  no hash at all); the chain only shows a line that does not follow the one before it, and the batch refusal is a courtesy,
  not a guard.
- *The day stop* (T34b-2) is judged on the loss of the value per unit at each look but the run's last, so the owner's
  deposits and withdrawals are never a loss; a loss at or above the working stop refuses every purchase (stage of the
  order: `day stop`), as on the ordinary live account.
- *A live ticket* is marked shared as well as live (`shared:` and `live:` entries beside the ticket file's rows); an
  approval of a ticket not made in shared mode, or of a shared ticket by a run that is not shared, is refused, and the
  approval recomputes the book's value after the prices and refuses beyond `TICKET_QUOTE_MOVE` (R26).
- *The shortfall (R24), the latch (R25), the marks (R28) and every limit on her book alone (R31)* hold as on practice; the
  owner's own holdings of a fund (the baseline) are never priced or second-sourced.

**Known limits of the live section:** the engine called directly (`run_order_step(live=..., shared=False)`) still runs
live without shared (unreachable from the command line; a live state file that holds an owner's entry refuses it at
`state`); `--live --check` is as it was (it judges the locks and sends nothing, and takes no `--shared`); and `--root`
on the owner's entry names any folder (the entry's state file is that folder's `memory/`, unlike a live run, which reads
the repository only).

**What is still missing on live:** nothing of the book's own (fractional live orders: "Fractions on live", above). The alerts, the
owner's page and the `mark` entry (S4e) hold on live as on practice ("The alerts and the owner's page", below).

### The alerts and the owner's page (T51c S4e)

**The alerts** (`core/alerts.py`, the watchdog's own checks over the journal's summaries; no new way of delivery).
*A stop of a shared run* (practice or live; unattended, propose or approve) is an alert that reflects the book's PRESENT STATE, not
the day: `shared-stopped:<venue>:<class>:<stage>`. The class is `unattended` (a summary with no `mode` key, whatever other keys it
carries; critical) or `attended` (any `mode` key: propose, approve, and anything else; a warning); the two classes are separate keys,
so an attended stop that is open as a warning can never keep an unattended stop from being raised as critical. It names the venue, the
stage, the day of the stop and the run's own words (the stop's reason, R2), and never an order's id, a key or an account number (the
watchdog's cleaning). It is open while the LATEST shared summary of that venue and class (read from the journal's last 30 days, any
day) is a stop at that stage. So a stop that nothing has fixed stays open over a weekend, and a stop that a watch first sees after
midnight is raised. It clears only when a later summary of the same venue AND the same class is `done` with no shortfall, or stops at
another stage (which opens that stage's key); a clean run of the other class, or of another venue, clears nothing, and a later `done`
that found a shortfall ends the stop (the run got past it) and opens the shortfall alert below. Ordinary skips neither open nor clear:
a closed market (the unattended run's stage `session` with its two ordinary reasons), and stages `ticket` and `session` of an attended
run, as before. What counts as a stop is what the old keys judged, with one fix: a shared summary is judged here whatever other keys it
carries (a `Mode` key reads as the unattended run; `mode` "both" or empty as an attended one), so none falls between the checks; a test
lists the stages that alert, old and new, per mode. A repeat of one cause is ONE alert, not one a run; a practice stop and a live stop
are separate alerts. *Before this repair* these stops raised `step-stopped:<venue>` (no stage) and `practice-stopped:<mode>:<day>` (no
venue or stage), which merged causes and let a clean propose run clear an unattended stop, and the key held the day, which cleared
a standing stop at New York midnight; a shared stop no longer raises either old key, so an alert still open under one of them (or under
the day form of the new key) is cleared by the next watch IN WHICH THE SHARED CHECK RAN, and the stop is raised again under the new key
(a watch where that check failed leaves the old keys open). A run that is NOT shared keeps both old keys and their rules (so two stages
of one non-shared venue still merge there). The order ledger's own block (`orders:<ledger>`) stays as well. A stop is read from the
last 30 days of the journal (a whole-journal read on every watch would be heavy file work beside the collector), and that window is for
RAISING. An open `shared-stopped` or `shared-shortfall` alert is cleared ONLY on positive evidence STRICTLY LATER than the summary that
raised it: a same-venue, same-class summary stamped after it that is `done` with no shortfall, or a stop at another stage. The alert
records the stamp of the summary that raised it (an `evidence` field on its line in the alert file: the journal's `timestamp`, to the
second, and `#n`, the summary's place among the journal's lines of that same timestamp); an alert without the field (raised before it
was recorded) is read by its own `time`, which is clearly later than the stop it was raised for. A summary at or before that stamp
(an earlier clean run, an earlier stop at another stage) never clears it and never raises a stale key over it. When the window holds no
later summary of that venue and class (the stop aged out, its day file was deleted, its line is torn, the venue went quiet) the alert
stays open untouched, still critical or warning as it was, with no `cleared` line, and the owner's page keeps showing it (a torn line
is still reported by `journal:damaged`). The accepted cost: the alert of a RETIRED venue
never clears by itself. The plain way to end it is a clean run (`done`, no shortfall) of that venue and class; the alert file has no
clearing command (the file alone says what is open, and this module adds none). The watch reads the open alerts under the same lock as its write, so a second watch cannot clear a key the first never saw. (The journal stamps to the second; two summaries of one second are told apart by their place in the journal, which is read within the 30-day window.)
*The one gap S4e closes*: a run that ENDS `done` whose last reconcile found a shortfall (its summary's `shared.shortfalls` not empty: the
owner's sale of shares the book counts as its own, seen after the run's orders) raised nothing but that block. It now raises
`shared-shortfall:<venue>:<class>` (a warning), under the same state rule: open while the latest relevant summary of that venue and
class is such a run, whatever the day, closed by a later `done` with none or by a stop of that class. Its detail is cut by the watchdog
at 300 characters, so it opens with the venue, the day, the stage (the run's last reconcile), the COUNT of short funds and the fund
names that fit (then ", and K more" for the rest), then the two ordinary causes and what clears it, and last the figures of the first
funds that still fit (what the book counts, what the account holds, the units short). Built from the summary's figures only.

**The owner's page** (`research_loop/owner_page.py`) has a ninth section, "Shared book", when a venue has a shared book (its
state file holds an owner's entry, or the journal holds one of that venue's; otherwise the page is as it was). For each such
venue: the book's value per unit, lower and upper, and the latest look's day and time (from its latest `book_nav` row); the
units outstanding (after the last entry); the value per unit the day stop measures from (the previous figure, and where it
came from) and the peak value per unit (`book_loss`, as a run reads them); the open alerts of the watchdog about this venue's
book; the owner's entries, latest first (number, kind, figures, units and value per unit after, who, note, time; the
`mark` entry among them, "SPY taken as the new mark at 505.0"); any `history_accepted` lines of the venue (day, figures,
who, note, when written); and one line per fund with the book's units, the owner's own units (what the account held beyond
the book at the ledger's last reconcile, its time said) and the two together. The combined holding is INFORMATION ONLY (R31):
nothing that judges an order imports or reads the page, and every limit stays on the book's own units. The page only reads:
the state file is opened read-only on the page's own connection (waiting at most 5 seconds for a writer), the order ledger and
the journal likewise. A state file that is missing (the journal still names the venue's entries), busy, of another layout or
whose chain no longer checks, a ledger that is missing, and a last reconcile that was not one-sided each say so in plain words
for that venue or that part ("Not available", "Unavailable"), never a traceback and never a write. A fund's owner's own units
are "not known" when the last reconcile was not one-sided; a held fund the one-sided reconcile listed no surplus for shows 0.

### The `mark` entry (T51c S4e, R34)

A held fund whose quote is further from its last `book_nav` mark than `max_drawdown_pct` stops a shared run at stage `marks` (R28);
until S4e only a `split` entry or the quote coming back cleared it. A REAL move (a fund up 40% overnight) needs the owner's yes:
`order-step --owner-entry mark --venue VENUE --symbol SYMBOL --who WHO --note NOTE` (with `--live` for the production venue,
as every live entry), then the same command with `--confirm CODE`. It is the owner's command of every other entry (R29): the
preview prints first with a code and writes nothing; the mark is the order step's own quote (never typed: `--mark` is refused;
`--amount`, `--units` and `--ratio` are refused for it), outside the session the last close, its time shown; the confirm asks
the quote again and writes only if the mark has not moved by more than `TICKET_QUOTE_MOVE` and nothing else changed. The entry
is hash-chained and journaled like the others (one `owner_entry` line; the repair of a lost line is the same). It names ONE fund
(a book holding units of it; a mistyped or unheld symbol is refused, nothing written): to clear two funds the owner writes two
entries. It changes no cash, no unit and no value per unit (the figures at its marks are the figures before). On the next run
the fund's reference mark is the mark of the latest `mark` entry written after the last `book_nav` row (a later split of the
fund divides it, a later `mark` replaces it), so a quote within `max_drawdown_pct` of that mark passes and one further away
stops the run again; another fund's stop is not cleared.

A `mark` entry is no stake change and no profit or loss, so it is never read as a base for a value per unit: `book_loss`'s
fallback (the day stop's previous figure when no row of an earlier day exists) and the history check's base (`_entry_base`) skip
it, and the peak is read from the `book_nav` rows alone. A mark taken at a LOWER value therefore cannot lower the day stop's
base or the history check, and one at a higher value cannot raise it; tests show the figures equal with and without the entry.

*Schema.* `owner_entries.kind` is a CHECK of the kinds. A new state file's statement names `mark`; a layout-2 file made before
S4e is rebuilt in place by its FIRST `mark` entry, in the write transaction of that entry after its chain is checked (every row
copied with its id, the table renamed, the two append-only triggers made again; all or none), with `user_version` still 2.
An order step of an earlier build does not know the kind (its book figures raise "entry N is of kind 'mark', which is none of ..."),
so going back to it needs the state file from before the first `mark` entry.

## Not covered yet

Options, futures and event-contract orders (the API supports them; the trader does not trade them), short selling,
the order-status stream (Webull can push each change), Webull's market-data API (prices still come from the existing
daily history), and its splits-and-dividends feed.
