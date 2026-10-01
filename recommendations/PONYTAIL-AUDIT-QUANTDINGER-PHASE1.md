# Ponytail audit — QUANTDINGER Phase 1

**Mode:** `/ponytail` full. Paper/shadow only.
**Fence:** `create_order` false. Oil `NO_HARVEST`. Screens `ot-valedia-desk` / `valedia-glance` not touched.

## Applied

`reuse:` `crypto_paper.fill_px` and `fee_usd`. Slip and the rate-on-notional were already there. The new file only adds the long/short position transition and the 5 bps label.

`reuse:` `forward_test_ledger._is_non_real_source`. Source `quantdinger_paper` already fails the Kelly read. `counts_toward_kelly` is also false on the row.

## Skipped

`yagni:` editing `crypto_paper.py`. A flag inside the live paper book would risk the default 10 bps table. The compare is a separate module. Add a book flag only if a tick must emit both labels.

`yagni:` limit orders, idempotency keys, walk-forward, PBO, Jev question pack. Those are later phases.

`yagni:` a repo reindex. CBM index `2026-09-30T07:24:33Z` has `crypto_paper.py` at `metadata_match`. The new files are `not_tracked`. Source was read and tested. Reindex when the graph needs these symbols.

`yagni:` git commit. **Not committed.**

## Findings

`shrink:` one helper, one test, one JSON decision. Eight scripted fills (flat long, then flat short).

`ponytail:` leverage is ignored inside `commission_quote`. Multiply by leverage only if a margin schedule defines the fee base as borrowed notional.

**net:** QD 5 bps is an opt-in label. Default book stays `CRYPTO_FEE_PCT` 10 bps. Open at 5× leverage, qty 2, fill 100.05, commission **0.10005** (5× would have been 0.50025). Tests PASS.
