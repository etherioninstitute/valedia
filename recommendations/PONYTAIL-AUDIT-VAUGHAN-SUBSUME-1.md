# Ponytail audit — VAUGHAN-SUBSUME-1

**Mode:** `/ponytail` full. Poly↔Kalshi paper/shadow only.
**Fence:** `PAPER/SHADOW`. Ornith still the fill gate. Oil/weather still fenced. Screens `ot-valedia-desk` (783), `valedia-glance` (755), `valedia-grok` (44478) not killed.

## Applied

`reuse:` `kalshi_fee_map.fee` taker formula for the Kalshi leg. **Shipped** as `fee_table=kalshi_fee_map.taker+poly_rate`. Poly rate stays 0 (global CLOB paper assumption). No second fee schedule.

`reuse:` CE-1 Ornith shape (`crypto_paper_safety._ornith_shape`, `_http_post`, `extract_json`). **Shipped** in `ask_ornith`. Down, malformed, Qwen, or a missing id refuses. No paper fill on the live `malformed` reply.

`reuse:` `ticker_family_fenced` and `forward_test_ledger._is_non_real_source` (source string contains `paper`). **Shipped** as `fenced_series` and `source=vaughan_arb_paper`. The live ledger file is not written.

`reuse:` controls file rows. **Shipped** `VAUGHAN_ARB_MODE`, `VAUGHAN_ARB_TICK_AGE_S`, `VAUGHAN_ARB_LAST_ORNITH`.

## Skipped

`yagni:` BB-Terminal `signals.ts` / Slice 2. **Not started.** Add when Burton queues it.

`yagni:` copying the ArbitrageBot TypeScript monorepo, dashboard, Railway path, or trailing-stop position manager. A binary paper scan has no open position to trail. **Not copied.**

`yagni:` `--read` during this run. Fixture quotes proved the gates. A signed Kalshi GET can wait until the parent env is paper. **Not called.**

`yagni:` cron/launchd. The command is in `VAUGHAN-ARB-PAPER.md`. **Not installed.**

`yagni:` a partial git commit. The branch still has a large uncommitted set. **Not committed. Not tagged.**

## Findings

`shrink:` one JSONL plus one flock. Retention is a tail rewrite at 400 rows (`ponytail:` on `DECISION_KEEP`). A dated archive is the upgrade when an audit needs the dropped head.

`shrink:` heuristic match is an O(n×m) Jaccard over one scan. Fine at the 100×200 GET cap. An index is the upgrade if `--read` ever pages the whole catalog.

**net:** one module, allowlist fill path, review-only heuristic, fee-true Kalshi taker fee, Ornith fail-closed. Version `0.66.0` working tree, not tagged. Control stays `VAUGHAN_ARB_MODE=PAPER/SHADOW`.
