# Ponytail audit — adversarial paper fights (0.27.0)

**Mode:** `/ponytail-audit` on the new slice only (not whole-repo). Report after applying safe small cuts.  
**Tree:** `~/.openclaw/workspace-v2`  
**Scope:** `adversarial_paper_fight.py`, `adversarial_run.py`, `test_adversarial_paper_fight.py`.  
**Fence:** no `--live`, no venue orders, no LN/Arena funding, no crontab install, `MIROFISH_AUTO_RAISE` untouched.

## Applied this session (before this report)

`delete:` unused `LENSES` / `HERE` / future-annotations / local `_f` / `fight()` wrapper / split+weak-bias veto (disagreement is recorded, not auto-vetoed). Buffett duplicate replaced by `persona_lenses.buffett`. [scripts/integrations/adversarial_paper_fight.py]

## Remaining findings (biggest first)

`yagni:` `adversarial_run.py` (28) duplicates module CLI. Spec asked for a cron-optional entry like `parity_run.py`. **Keep.** Do not add to `install_cron.sh`. [scripts/integrations/adversarial_run.py]

`yagni:` `--out` writes a second JSON after `models/adversarial_round_*.json`. CLI convenience only. Drop when nobody uses `--out`. [adversarial_paper_fight.py main]

`shrink:` `build_round` stamps neural via `attach_to_signal` and again for `--opps` not in the signal. Second loop is the opps-only path. Keep. [adversarial_paper_fight.py build_round]

`yagni:` LMS/persona-prompt lenses. Heuristics cover Buffett abstain, momentum follow, MiroFish+neural, risk veto. Add LMS when fee-true FTL says heuristics lose. [not present]

`yagni:` Lynch/Hayes/Arena primes. Charter H2 leftovers; not this slice. [ADAPT-DOSSIERS/ai-fund.md]

`yagni:` FTL winner/loser scoring. Marked `settle_later=fee_true_ftl`. Add when a round has aged settlements. [adversarial_paper_fight.py]

## Not cut (correctness / spec)

- Risk veto + Buffett `OUTSIDE_CIRCLE` on crypto without PE/MOS — H1 dossier, not bloat.
- `set_paths` globals — same test-injection pattern as NT-1.
- Hygiene tests that ban order/LN strings.

**net:** applied ~25 lines cut this pass; runner (28) stays because the charter named it. 0 new deps. Lean enough. Ship.
