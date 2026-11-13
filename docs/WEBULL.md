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
    # WEBULL_ACCOUNT_ID=...   only if the key sees more than one account

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

Limits: most market-data calls allow 60 requests a minute per kind of call in production (30 on the paper side);
the stock data bought for programs allows 300. The limits for trading calls are on Webull's rate-limits page and
have not been read into this guide yet.

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

## Still to observe there, because Webull does not document them

- a fill: how soon it shows, how fees arrive, what a holding row looks like (a one-share buy and sell is set to run at the next open);
- fractional quantities in the regular session;
- the same checks on the live side before any real order (the paper side may not behave identically);
- the quantity step for stocks and the price steps;
- whether one holding is ever split over several rows, and whether every row carries its instrument type;
- how far its open-order and position answers lag behind fills, and how late fees arrive;
- whether any answer repeats a key.

## Real money

Nothing in this repository sends a real-money order on its own, and nothing in it creates the file that allows one.

A production order needs all of: `WEBULL_ENV=production`; a file `config/webull_live.json` written by you,

    {"account_id": "<your account id>", "confirmed_by": "<your name>", "max_order_value": 250,
     "i_understand_this_trades_real_money": true}

naming the same account the key trades; an order value at or under `max_order_value`; and a known price for the order.
The paper mirror passes no price, so it can never trade live even with that file present. A live execution path is
deliberately not wired: it is the last step, taken with you, after the order path has been proven in the test
environment, a strategy has a paper record, and the order-handling code has been reviewed by a model from another
family or you have waived that. As of 2026-11-13 two strategies have passed their backtests and sealed tests; the
paper record is days old.

## Not covered yet

Options, futures and event-contract orders (the API supports them; the trader does not trade them), short selling,
the order-status stream (Webull can push each change), Webull's market-data API (prices still come from the existing
daily history), and its splits-and-dividends feed.
