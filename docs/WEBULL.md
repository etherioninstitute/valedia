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
| College football, men's college basketball | 506 and 354 | 298 each of the first 300 read (the probe's page cap stopped the read) |

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

## Not covered yet

Options, futures and event-contract orders (the API supports them; the trader does not trade them), short selling,
the order-status stream (Webull can push each change), Webull's market-data API (prices still come from the existing
daily history), and its splits-and-dividends feed.
