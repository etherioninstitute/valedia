# Ponytail audit — POLY-US API matrix (0.34.0)

**Mode:** `/ponytail` full on the POLY-US diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `polymarket_us_client.py`, FTL US fill fields, nav tag helpers, matrix + cutover, `test_poly_us.py`.
**Fence:** no `--live`, no `VENUE_WRITER` flip, `LIVE_CLIENT_HOOK` stays None, no US create on the wire, no keys in git.

## Applied this session

`yagni:` `pip install polymarket-us`. cryptography already present for Ed25519; stdlib urllib for REST. **SDK not shipped.**

`yagni:` websocket client, modify/batch, close-position, combos/RFQ/FIX/gRPC. Matrix lists them **missing**. **Not shipped.**

`yagni:` default HTTP. Same habit as HL public / POLY-E: inject transport or `POLYMARKET_US_HTTP=1`. **Not shipped.**

`yagni:` second fill parser. Extended `extract_polymarket_fill` for US `tradeId` / `lastShares` / GET order `cumQuantity`. **Not a new ledger.**

`yagni:` flipping `VENUE_WRITER["polymarket"]` or `POLYMARKET_US_LIVE`. **Not shipped.**

## Findings (biggest cut first)

`yagni:` wrapping OpenAPI `PreviewOrderRequest.request`. SDK posts the create body; we do that. Add the wrap if preview 400s on live.

`shrink:` nav fetches one primary tag slug (`sports`, `politics`, …). No fan-out over every synonym in `NAV_TAGS`.

`yagni:` new cron. Client + tests only.

## Not cut (spec)

- Separate US module so Int'l CLOB ≠ US DCM.
- create/cancel gated `POLYMARKET_US_LIVE` default False.
- Category nav Sports/Politics/Crypto/Weather/Tech/Culture/Econ.
- preview ≠ create; create ack ≠ fill; GET by order/trade id.
- Cutover checklist names flips and does not perform them.

**net:** ~220-line urllib client + small FTL field adds + mock tests. 0 new deps. 0 writer flips. Lean enough. Ship.
