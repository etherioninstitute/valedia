# INFORMED-BEFORE-CREATE — fail-closed quality before create_order

**Burton + Finance Scout (2026-09-21 PT).** North star: most-informed → size → trade → self-correct from fee-true FTL.

**This slice does NOT flip** `POLYMARKET_US_LIVE`, `VENUE_WRITER`, wallets, HALT, or fee-map git defaults. Kalshi may already be live; tighten the create path. Poly stays fenced until Burton flips after this gate + paper falsifiers.

## Kalshi (tighten create now)

Refuse `create_order` / live execute unless **ALL**:

1. Series present in fee-map; `EV = model_edge − fee(P,C,M) − half_spread − slip` ≥ floor (wire `KALSHI_EV_PREFLIGHT_GATE` path; prefer maker when viable; higher EV hurdle for taker).
2. FV source ≠ missing/default: refuse MiroFish fusion when score is default **50**; refuse tag_prior-alone; require model / rules-clock / researched FV + confidence flag.
3. Fences: oil `NO_HARVEST` / `LIVE_EXEC_FENCED`; sports attestation valid; `VALEDIA_TRADING_HALT` off is necessary≠sufficient.
4. Depth at limit ≥ min; size ≤ fee-adjusted FTL/Kelly (Valedia-owned).
5. If fill-quality ledger demotes the series, refuse (or demote size) when ledger is wired.

Expose rows in `controls_snapshot()` (const/env source clear). Default gates stay fail-closed for new checks; do not weaken existing fee-map env ON behavior.

## Poly US (gate only — no live flip)

Same Kalshi bar, plus:

- Hard **informed-before-create** on US client path (not ~10% fusion weight).
- POLY-LOOP: FV + ≥8pt **after fees** + book exit — ban `fv=tag_prior` alone for any future create path.
- Do **not** set `POLYMARKET_US_LIVE=1` in git or runtime from this Build.
- `VENUE_WRITER["polymarket"]` stays `none`.

## Refuse even if “research present”

Raw whale/copy mirror; PRIVATE_KEY bots; wallet-connect copy; taker longshots; fee-blind multi-leg; Kalshi↔Poly keyword arb; oil harvest; sports geo fail; crypto wallets pre-paper; HALT / fee-map / informed-gate bypass; MiroFish auto-size without fee-true EV.

## Deliverables

1. Code + tests for fail-closed checks on Kalshi create path (and Poly US create stub path remaining blocked).
2. `controls_snapshot` / cockpit honesty for new gate(s).
3. `PONYTAIL-AUDIT-INFORMED-BEFORE-CREATE.md`
4. Version bump; update `GROK-STATUS.md`; Next=Burton (Poly live still STOP; OT-VALEDIA-FORK-1 still queued).

## Tools

RuFlo MCP `:8810` filtered; CBM for navigation; Ponytail after code. Fence held.
