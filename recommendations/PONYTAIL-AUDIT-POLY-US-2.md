# Ponytail audit — POLY-US-2 retail whoami + preview dry-run (0.35.0)

**Mode:** `/ponytail` full on the POLY-US-2 diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `whoami_retail` via balances, `poly_us_smoke.py`, matrix note, `test_poly_us.py`.
**Fence:** `POLYMARKET_US_LIVE` stays False. No create/cancel on the wire. No writer flip. No keys in git.

## Applied this session

`yagni:` calling `GET /v1/whoami` on `api.polymarket.us`. That path is institutional (`api.prod.polymarketexchange.com` bearer JWT) and 404s on retail Ed25519. **Identity = balances.**

`yagni:` extra preview client. Live 400 `"Request is required"` → wrap `{request: order}` inside existing `preview_order`. One guard, all callers.

`yagni:` pip install `polymarket-us`. Same urllib client. **SDK not shipped.**

`yagni:` flipping `VENUE_WRITER["polymarket"]` or `POLYMARKET_US_LIVE`. Smoke asserts create raises `LiveWriteBlocked`. **Not shipped.**

## Findings (biggest cut first)

`shrink:` whoami_retail redacts to currency/buyingPower/currentBalance/assetAvailable/openOrders + optional positions count. No pendingWithdrawals/bank fields.

`yagni:` second smoke framework. One script + mocked test + optional live-http skip if no keys.

## Not cut (spec)

- Retail identity must not pretend institutional `/v1/whoami` works on `api.polymarket.us`.
- Preview dry-run on one active market, qty=1 tiny limit.
- create/cancel gated; smoke never creates.

**net:** ~40 lines on the client + ~80-line smoke + mock tests. 0 new deps. 0 writer flips. Lean enough. Ship.
