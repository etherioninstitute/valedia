# Grok Build queue (Burton — seamless adapt)

## DONE this session
- **TREASURY-1 (0.36.0)** — report-only multi-venue funding. Burton first-fill + compound-in-place + floors. `treasury_status.py` never moves money. `VALEDIA_TREASURY_AUTO=False` (auto path refused). `test_treasury.py` PASS. Ponytail `PONYTAIL-AUDIT-TREASURY-1.md`. Do not fund anything.
- **POLY-US-2 (0.35.0)** — retail whoami via balances (not institutional `/v1/whoami`); preview dry-run harness `poly_us_smoke.py` (LIVE=0, create blocked). `test_poly_us.py` PASS. Ponytail `PONYTAIL-AUDIT-POLY-US-2.md`.
- **POLY-US (0.34.0)** — Polymarket US DCM API matrix + thin client (not Int'l CLOB). create/cancel gated. Nav tags. Fill-true. `test_poly_us.py` PASS. Cutover does not flip. Ponytail `PONYTAIL-AUDIT-POLY-US.md`.
- **H8 + POLY-E (0.33.0)** — fortress intel fallback (flag off, engine parked) + Polymarket entertainment/Oscars path (nav buckets, shadow Intents, fill-true GET-by-id). Live Poly not enabled. `test_h8.py` + `test_poly_e.py` PASS. Ponytail audits written.
- **H7 (0.32.0)** — `arena_stress.py` wraps `adversarial_paper_fight.py`. Arena primes + fee-true FTL score. LN refused. Not crontab. `test_h7.py` PASS. Ponytail `PONYTAIL-AUDIT-H7.md`.
- **Adversarial paper fights (0.27.0)** — `adversarial_paper_fight.py` + `adversarial_run.py` (not crontab). Reuses H2 Buffett; momentum/MiroFish/risk-veto. JSON under `models/`. Tests mocked PASS. Ponytail audit written. Arena LN left; fight pattern pointed at this module.
- **H1** — `ADAPT-DOSSIERS/` + `VALEDIA-SEAMLESS-ARCHITECTURE.md` (wired).

## STOP
TREASURY-1 SHIPPED (`0.36.0`). Do not fund anything. Do not set `VALEDIA_TREASURY_AUTO`. Do not enable live Polymarket. Do not set `POLYMARKET_US_LIVE`. Do not auto-start Arena funding / NT live. Do not install crontab. Do not flip `NAUTILUS_LIVE` / `VENUE_WRITER` / `SPOT_RECONCILE_HTTP` / `VALEDIA_SELF_ADJUST` / `MIROFISH_AUTO_RAISE` / `FORTRESS_INTEL_FALLBACK` / `LIVE_CLIENT_HOOK` / `REBALANCER_AUTOEXEC`.

## Later (Burton go)
- H2 leftovers: Lynch / Hayes lenses on the same fight module
- Cockpit read-only `arena_stress` summary
- Gini / 5-game battery (still paper)
- Polymarket live cutover — `recommendations/POLYMARKET-ENTERTAINMENT-CUTOVER.md` (Burton flips)

Live venue fences still held unless Burton flips.
