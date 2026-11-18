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
only that ticket's orders, each after fresh checks. Whole shares only (`--fractional` is refused). The command line
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

**Not built yet:** fractional orders in live mode (they are built for practice only, below, and live still refuses
`--fractional`); the live kill switch and its reset; the stake ladder's use (the first stake, its steps and
`ladder.json`); unattended live runs (live mode never runs from a job).

## Fractional orders, as built (practice path only)

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
fractional run the journal's `order` and `fill` lines carry `order_type` (LIMIT or MARKET) and `leg` (`whole` or
`remainder`); without `--fractional` no line carries them and everything is written exactly as before. A fund whose own
tag would equal another fund's remainder tag is refused in words before anything is sent for either. Each leg is one
order against `max_orders_per_day` (sales first, as before). The MARKET leg goes only after its fund's LIMIT leg of the
same run was accepted by the venue (or partly or wholly filled); when the LIMIT leg was refused, left over, dropped,
cut by the day's cap or its outcome is unknown, the remainder is not sent in that run and its line says why. On a
ticket, a remainder line holds the collar price in its limit column and its leg follows from its size alone (under one
share); an approval sends each line as the leg, type and size it was proposed as, and is refused in words when its
`--fractional` differs from the ticket's.

**The collar.** Before a MARKET leg is sent, six checks, each of which holds the leg back with a line naming it
(`collar` on its `left_over` entry) and is not a fault, not an attention item and not a stop (its LIMIT leg goes as
usual):
- `clock`: only from 30 minutes after the open to 30 minutes before the close (`MARKET_AFTER_OPEN`,
  `MARKET_BEFORE_CLOSE`; a half-day's close as the calendar has it), judged again at the moment of sending;
- `quote_age`: its quote at most 120 seconds old at the moment of sending (`MARKET_QUOTE_MAX_AGE_SECONDS`; the order
  step asks again once for a fresher one first; LIMIT legs keep their 20 minutes), judged again at the moment of sending;
- `quote_move`: the quote within 0.5% (`TICKET_QUOTE_MOVE`) of the quote the leg was decided at (an approval: the
  ticket's quote);
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

**The residual.** A rest too small to send (under the minimum with its margin) is not sent. It is written per fund
on the run's summary under `residuals` (units, quote, value and why) and is never a fault, never an attention item and
never drift.

**What `--fractional` now sends on practice.** Whole-share LIMIT orders and MARKET orders of under one share, nothing
else. Before this build `--fractional` sent one LIMIT order with decimals per fund (1.5 shares, say), which the test
environment took but production refuses for fractions; those orders are gone: no LIMIT order carries decimals and no
MARKET order carries 1 share or more. The ledger file is layout 4 (columns `order_type` and `collar_price`); a layout-3
file is upgraded once, the first time it is opened for writing, every earlier row reading as a LIMIT order with
no collar; the code before this build refuses a layout-4 file in words.

**Live still refuses `--fractional`** (the command line and the order step both, before anything is read), and the
live adapter still refuses any production order without a limit price. Live fractional orders wait for a later build:
the practice proof below, read from the practice record, and your own consent to fractional live orders.

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

## Not covered yet

Options, futures and event-contract orders (the API supports them; the trader does not trade them), short selling,
the order-status stream (Webull can push each change), Webull's market-data API (prices still come from the existing
daily history), and its splits-and-dividends feed.
