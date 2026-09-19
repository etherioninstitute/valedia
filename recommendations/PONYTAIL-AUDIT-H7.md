# Ponytail audit — H7 Arena offline stress harness (0.32.0)

**Mode:** `/ponytail` full on the H7 diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `arena_stress.py` wrap of `adversarial_paper_fight.py`, `test_h7.py`.
**Fence:** no `--live`, no venue orders, no LN/faucet, no crontab, no `FUSION_WEIGHTS["arena"]`, `MIROFISH_AUTO_RAISE` / `VALEDIA_SELF_ADJUST` untouched.

## Applied this session

`delete:` unused `_MODELS` / `set_models` (use `apf._MODELS`). [arena_stress.py]

`delete:` `_match` wrapper; call `mal._best_ftl_match` directly. [arena_stress.py]

`delete:` dead `veto=` branch on `bump`; unused `valedia_config` import. [arena_stress.py]

`shrink:` `run_stress` calls `apf.run_round(..., write=False)` instead of re-loading signal/enrich/ere. [arena_stress.py]

## Findings (biggest cut first)

`yagni:` `lf-game-theory` process / `npm run arena:*` / Lightning faucet / agent keys. Replacement: paper wrap. **Not shipped.**

`yagni:` Gini / exploitation / classic 5-game battery / cockpit `arena_stress` pane / `arena_stress_latest.json`. Fight JSON + FTL score is the log. Add when a reader exists.

`yagni:` second runner `arena_run.py`. Module `__main__` matches ADV CLI; do not crontab-install. [arena_stress.py]

`yagni:` Lynch/Hayes on the fight. H2 leftovers, not this slice.

`yagni:` persist duplicate of `apf.persist` (different prefix `arena_stress_`). 12 lines. Fold into a prefix arg if a third writer appears.

## Not cut (spec)

- Four Arena primes as extra votes (not mixed into 0.27.0 winner).
- Fee-true FTL scoring via `mirofish_accuracy_ledger` helpers (one outcome mapping).
- Risk veto records disagreement; `intents` stay empty.
- `ARENA_MODE=paper`; LN keys seen still `funded=False`.
- Paper/sandbox FTL pairs excluded by existing `_ftl_pairs`.

**net:** ~300-line wrap, 2-line fight docstring, 0 deps, 0 cron, 0 fusion keys. Lean enough. Ship.
