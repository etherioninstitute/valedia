# Webull

The trader connects to Webull through Webull's official OpenAPI and Python SDK. Interface facts were read from
developer.webull.com on 2026-11-11 and checked against SDK 3.0.4.

## What is ready

- `valedia_trader/venues/webull.py`: account list, balance, positions, and stock, ETF and crypto orders in Webull's format.
- It uses Webull's test environment (`api.sandbox.webull.com`) unless told otherwise.
- It is long-only: an order that would sell more than is held is refused.
- Credentials are never printed or logged (the SDK's own request logging is silenced).
- `python -m valedia_trader.doctor` reports whether a key is on file, the environment, and whether real-money orders are confirmed.

## Connecting your account

1. On Webull's website, apply for API access (Webull says review takes 1 to 2 business days) and generate an App Key and
   App Secret. For the test environment, create a test account as their "Trading API Application" page describes.
2. Add to `~/.hermes/secrets/trading.env` (mode 600):

       WEBULL_APP_KEY=...
       WEBULL_APP_SECRET=...
       WEBULL_ENV=sandbox
       # WEBULL_ACCOUNT_ID=...   only if the key sees more than one account

3. Check the connection (read-only):

       uv pip install --python .venv/bin/python 'webull-openapi-python-sdk>=3.0.4'   # once; already installed here
       .venv/bin/python -m valedia_trader.venues.webull check

   Webull may ask for a one-time approval in its app the first time a key is used.
4. Mirror paper orders to the test environment:

       .venv/bin/python -m valedia_trader.research_loop paper-step --webull

## Real money

Nothing in this repository sends a real-money order on its own, and nothing in it creates the file that allows one.

A production order needs all of: `WEBULL_ENV=production`; a file `config/webull_live.json` written by you,

    {"account_id": "<your account id>", "confirmed_by": "<your name>", "max_order_value": 250,
     "i_understand_this_trades_real_money": true}

naming the same account the key trades; an order value at or under `max_order_value`; and a known price for the order.
The paper mirror passes no price, so it can never trade live even with that file present. A live execution path is
deliberately not wired: it is the last step, taken with you, after a strategy has passed the development test, its
sealed test and paper trading. As of 2026-11-11 no strategy has passed the first of those.

## Not covered yet

Options, futures and event contracts (the API supports them; the trader does not trade them), short selling, order
status streaming, and Webull's market-data API (prices still come from the existing daily history).
