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

## To observe in Webull's test environment, because Webull does not document them

- how it answers for an order that does not exist (the adapter's `confirmed_not_found` stays off until this is seen);
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
