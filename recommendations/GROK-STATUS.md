# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-18 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** MIROFISH-SMOKE SHIPPED. Prior: TREASURY-1 0.36.0.

**Version:** `0.37.0` tag `valedia-v0.37.0` (this commit). Prior: TREASURY-1 `0.36.0` `93f9b66`.

**Tests:** `test_mirofish_smoke.py` PASS offline. Harness `mirofish_smoke.py` PASS (fixture 6ms, live-lite 7.4s on `:11435` `qwen3-8b-mf:latest`, wall 7.44s). `test_nt2.py` / `test_slice23.py` PASS. No production ledger writes. No live flips.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-MIROFISH-SMOKE.md`

**MIROFISH-SMOKE:** Minutes-scale wiring gate. Fixture: synthetic seed JSON → extractor → `neural_enriched` → ledger `record`+mocked fee-true `score` → intel `gather`. Live-lite: one Ollama generate, 90s cap, SKIP if down (this run: PASS). Overnight swarm still the corpus job. Caps 0.10/0.15. `MIROFISH_AUTO_RAISE` False. Report: `recommendations/MIROFISH-SMOKE.md`.

**TREASURY-1:** Burton guide `VALEDIA-TREASURY-FUNDING.md`. `treasury_status.py` reads cash where helpers exist (Kalshi cents, Poly US balances, Coinbase USD, Alpaca cash/buying_power); HL/Kraken DOCUMENT. Floors: Poly 25, Kalshi stub 25, Coinbase 20, Alpaca 10. Emits `models/treasury_status.json`. Below floor → WARN strings only. `VALEDIA_TREASURY_AUTO=False`; `attempt_auto_topup` raises even if env=1. `TREASURY_ALERT_WEBHOOK` unused. Rebalancer stays shadow (`REBALANCER_AUTOEXEC=False`). First human fund still required.

**Fence:** No `--live`, no ACH/wire/card, no wallet/venue transfer, no API-key edits, no `VENUE_WRITER` flips, no `SPOT_RECONCILE_HTTP`, no `NAUTILUS_LIVE`, no LN, no crontab. `MIROFISH_AUTO_RAISE` False. `VALEDIA_SELF_ADJUST` False. `FORTRESS_INTEL_FALLBACK` False. `LIVE_CLIENT_HOOK` None. `POLYMARKET_US_LIVE` False. `REBALANCER_AUTOEXEC` False. `VALEDIA_TREASURY_AUTO` False.

**Next:** STOP (do not flip live). Queue after Burton go: PONYTAIL-FULL → FULL-SMOKE → KILL-SWITCH → staged ON. See GROK-NEXT-QUEUE.md.
