# Valedia adversarial paper fights (Burton 2026-09-18)

**NORTH STAR:** dynamic world-class auto-trader.  
**Implement now.** No Lightning / Arena sats.

## IS
Multi-agent **paper/shadow** competition inside Valedia:
- Lenses/personas (start: Buffett/value, momentum/kernel, MiroFish-enriched, risk veto) propose or score the same opportunity set.
- They “fight” — disagreement recorded; risk lens can veto.
- Winner/loser scored later against **fee-true FTL** (and MiroFish accuracy tags), not sats.
- Output: JSON under `models/adversarial_round_*.json` + optional Signal summary that is truthful (PAPER/SHADOW only).

## IS NOT
- LN faucet, Arena coordinator funding, sat balances
- Live venue orders / second OMS
- Weight auto-raise (still gated)

## Slice deliverable (Grok Build)

**SHIPPED 2026-09-18 — `VALEDIA_VERSION` 0.27.0** (H2 personas remain 0.26.0). Crontab not installed. Arena not funded.

1. `scripts/integrations/adversarial_paper_fight.py` + thin `adversarial_run.py` (cron-optional, **not** installed).
2. Round input: `mirofish_signal.json` (+ neural enrichment if present) and/or `--opps`.
3. Lenses: Buffett/value, momentum/kernel-style, MiroFish-enriched, risk veto. Heuristics/stdlib (no LMS).
4. Persist `models/adversarial_round_*.json`; no venue orders; no LN.
5. Tests mocked: `python3 test_adversarial_paper_fight.py`. Ponytail audit → `PONYTAIL-AUDIT-ADV-FIGHT.md`.
6. Wired in `VALEDIA-SEAMLESS-ARCHITECTURE.md` + Arena dossier (leave LN, steal fight pattern).

CLI-first; local models only for any LLM calls (LMS/MiroFish stack) — prefer pure/heuristic lenses first (Ponytail) so no cloud required.
