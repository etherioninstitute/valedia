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
- It chooses its account by class (`WEBULL_ACCOUNT_CLASS`, for example `INDIVIDUAL_CASH`), refuses an order that
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

Add to `~/.hermes/secrets/trading.env` (mode 600). Never paste keys anywhere else.

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
4. **Generate Key** (shown once), then put it in the secrets file with `WEBULL_ENV=production`.
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

Nothing in this repository sends a real-money order on its own, and nothing in it creates the file that allows one.

A production order needs all of: `WEBULL_ENV=production`; a file `config/webull_live.json` written by you,

    {"account_id": "<your account id>", "confirmed_by": "<your name>", "max_order_value": 250,
     "i_understand_this_trades_real_money": true}

naming the same account the key trades (the id Webull's API uses is a long internal one that the console does not
show; how you confirm the live account will be changed to the account's class and last characters before go-live); an order value at or under `max_order_value`; and a known price for the order.
The paper mirror passes no price, so it can never trade live even with that file present. A live execution path is
deliberately not wired: it is the last step, taken with you, after the order path has been proven in the test
environment, a strategy has a paper record, and the order-handling code has been reviewed by a model from another
family or you have waived that. As of 2026-11-13 two strategies have passed their backtests and sealed tests; the
paper record is days old.

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
  is refused until a run reads Kalshi's record as plainly not finalized (the record that said finalized stays in
  the state). A later run never settles a pending record by itself. The 17 November holding has no earlier
  reading, so it waits for a person.
- **What a loss is confirmed as.** A loss is confirmed by three facts: Kalshi's record says the contract lost, the
  account shows none of it, and the account's cash moved by exactly what the ledger knows, no more and no less.
  A contract transferred out, or sold by hand for a price that nets exactly zero after fees, would be recorded
  the same way. The money would still be right; only the word "settlement" would be wrong. The account's own
  order history is the third witness a later ticket adds. For a win, the cash rising by exactly the payout is
  real evidence, and the flag `win_cash_observed`, shown in every summary, turns true on the first one.
- **Not handled:** a correction Kalshi makes after `finalized` would show only as a cash difference. Any cash
  move from outside the ledger that exactly cancels a difference passes the equation. An average the venue rounds
  to the cent can leave the known cash off by that rounding; the equation then usually fails and a person
  confirms, but it can cancel (two roundings of opposite sign, or a rounding beside a same-size hand sale), and a
  rounding that entered before the earlier clean reading cancels exactly. A zero fee reported only after the fill
  is never stored by the ledger: that order's cash stays unknown for good, so every later settlement on that
  account then needs a person until the ledger is repaired (T45-3).

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
