# Ponytail audit — POLY-E Oscars/entertainment path (0.33.0)

**Mode:** `/ponytail` full on the POLY-E diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `polymarket_entertainment.py`, gatherer collect, shadow emit, FTL fill helpers, cutover doc, `test_poly_e.py`.
**Fence:** no `--live`, no `VENUE_WRITER` flip, `LIVE_CLIENT_HOOK` stays None, no CLOB POST, no keys.

## Applied this session

`yagni:` second emit/compare for entertainment CLOB bodies. Reuse `emit_polymarket_arb_shadow` + `kind=`. **Not a new POST builder.**

`yagni:` default Gamma HTTP. `fetch_markets` requires injected `get_fn`. **Not shipped.**

`yagni:` py-clob-client / archived `polymarket_executor`. Stub body already exists. **Not shipped.**

## Findings (biggest cut first)

`yagni:` websocket user-channel, retry loop, Decimal money types. Fill leak is POST-ack without GET-by-id — same H3 lesson. Helper is ~80 lines in FTL. **Not shipped.**

`yagni:` new cron to scan Oscars. Local JSON + gatherer is enough until Burton wants a schedule. **Not shipped.**

`yagni:` second Kelly / entertainment-specific sizer. Category alias `POLYMARKET_ENTERTAINMENT` → `ENTERTAINMENT` uses the existing gate. **Not shipped.**

`shrink:` accuracy ledger only passes `opportunity_tags` through. Scoring waits for fee-true FTL settlements.

## Not cut (spec)

- Classify Polymarket nav (Sports/Politics/Crypto/Weather/Tech/Culture/Econ); Culture = Oscars.
- Never-dispatchable shadows; desk_stage stays `risk` (writer none).
- POST-ack ≠ filled; no poll without order/trade id.
- Cutover checklist names flips and does not perform them.

**net:** ~250-line classifier + ~80 FTL lines + gatherer hook. 0 deps. 0 writer flips. Lean enough. Ship.
