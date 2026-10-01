# Ponytail audit — CE-3 fee-true paper cron

**Mode:** `/ponytail` full. Crypto paper desk only.
**Fence:** `PAPER/SHADOW`. Ornith still the fill gate. Oil/weather still fenced. Screens `ot-valedia-desk` (783), `valedia-glance` (755), `valedia-grok` (81222) not killed. Glance was already listening and was not reloaded.

## Applied

`reuse:` `crypto_paper.apply_fill` fee math (`CRYPTO_FEE_PCT` / `CRYPTO_SLIPPAGE_PCT`) and `forward_test_ledger._is_non_real_source`. **Shipped** as `decisions.jsonl` rows with `source=crypto_paper`. No second fee table. No write to the live ledger.

`reuse:` CE-2 `run_tick`. Every result appends one row, including refuse and `ornith_down`. **Shipped** in `crypto_paper_run.run_tick`. A held `decisions.jsonl.lock` returns `tick_busy`. The same pair+bar fill returns `duplicate_tick` and skips Ornith.

`reuse:` `controls_snapshot` file rows. **Shipped** `CRYPTO_PAPER_TICK_AGE_S`, `CRYPTO_PAPER_LAST_ORNITH`, `CRYPTO_PAPER_TICK_STALE` (2h).

## Skipped

`yagni:` installing launchd/cron. The command is already in `CRYPTO-PAPER-DESK.md`. **Not installed.**

`yagni:` copying paper rows into `models/forward_test_ledger.jsonl`. The paper log is FTL-shaped and the source token already fails Kelly. **Not written.**

`yagni:` a partial git commit. `valedia_config.py` and the rest of this branch still carry earlier uncommitted slices. **Not committed.**

## Findings

`shrink:` one JSONL plus one flock. A rotator process, a second fee schedule, and a Kelly filter inside the paper module would repeat `CRYPTO_FEE_PCT` and `_is_non_real_source`.

`shrink:` retention is a tail rewrite at 400 rows (`ponytail:` on `DECISION_KEEP`). A dated archive is the upgrade when a tick audit needs the dropped head.

**net:** decision append on the existing tick, one lock, three control fields. Version `0.64.0` working tree, not tagged. Control stays `CRYPTO_PAPER_SAFETY=PAPER/SHADOW+ORNITH`.
