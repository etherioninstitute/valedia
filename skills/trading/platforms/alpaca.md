---
description: >
  Alpaca Trading — equity execution. Live account, zero commission.
  SSL cert fix required for Python 3.13.
---

# alpaca

Alpaca live trading account for stocks and ETFs. Zero commissions.
Current balance: $28.02 equity, $25.21 cash. One position: XLE.

## Mode: LIVE

`IS_LIVE = True` in `alpaca_executor.py`.
API endpoint: `https://api.alpaca.markets`
Keys: `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` in `.env`

## SSL fix (Python 3.13 issue)

Python 3.13 from python.org ships without a certificate bundle.
Fix: `SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` point to certifi's bundle.
Added to both `~/.openclaw/.env` and `~/.zshrc`.
Without this: `[SSL: CERTIFICATE_VERIFY_FAILED]` on every Alpaca call.

## Fractional shares

Alpaca supports fractional shares by dollar amount (`notional` parameter).
Valedia uses `market_buy(symbol, notional=position)` for all equity buys.
Minimum: $0.40 per trade.

## Key error patterns

| Error | Cause | Fix |
|-------|-------|-----|
| `SSL: CERTIFICATE_VERIFY_FAILED` | Missing cert bundle | Check SSL_CERT_FILE in .env |
| `401 Unauthorized` | Expired/wrong keys | Regenerate at app.alpaca.markets |
| `insufficient buying power` | Not enough cash | Check equity/cash balance |

## Related

- [[platforms/universe]] — which equity assets Valedia trades
- [[core/fee-gate]] — Alpaca is zero-fee (most profitable platform)
