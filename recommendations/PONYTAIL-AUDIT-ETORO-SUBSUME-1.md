# Ponytail audit — ETORO-SUBSUME-1

**Mode:** `/ponytail` full. eToro research + paper/shadow only.
**Fence:** `ETORO_LIVE` off. Writes raise before a socket. Oil/weather still fenced. Screens `ot-valedia-desk` (783), `valedia-glance` (755), `valedia-grok` (64260) not killed.

## Applied

`reuse:` paper decision shape from `vaughan_arb_paper` / crypto-paper. **Shipped** as `data/etoro-paper/decisions.jsonl`, source `etoro_copy_paper`, `create_order` false, `start_copy` false. `forward_test_ledger._is_non_real_source` already ignores `paper`. The live ledger is not written.

`reuse:` controls file rows. **Shipped** `ETORO_MODE`, `ETORO_LIVE`, `ETORO_KEYS`, `ETORO_TICK_AGE_S`, `ETORO_LAST_DECISION`. Key values are not a control field.

`reuse:` house research User-Agent (`Valedia-ASI research@goatworthy.com`). Cloudflare 1010 blocks Python's default agent. **Shipped** on every GET.

## Skipped

`yagni:` a real POST body behind `write_endpoint`. The named stubs exist and refuse. Add the sender only when Burton sets the documented unlock on purpose.

`yagni:` Ornith on this desk. The score is five public numbers. Add a model gate if a would-copy is ever allowed to size a Kalshi or crypto-paper trade.

`yagni:` OpenTerminal copy buttons, cron, and a second fee table. **Not added.**

`yagni:` a partial git commit. **Not committed. Not tagged.**

## Findings

`shrink:` one JSONL plus one flock. Retention is a tail rewrite at 400 rows (`ponytail:` on `DECISION_KEEP`).

`shrink:` one shared 60/60s deque, including market-data's wider pool. Split the bucket if instrument search starts waiting on rankings.

**net:** GET client, numeric copy score, paper would-copy, write stubs that do not send. Version `0.67.0` working tree, not tagged. `ETORO_MODE=PAPER/SHADOW`.
