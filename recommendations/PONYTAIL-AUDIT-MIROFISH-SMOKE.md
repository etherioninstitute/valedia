# Ponytail audit — MIROFISH-SMOKE (0.37.0)

**Mode:** `/ponytail` full on the MIROFISH-SMOKE diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `mirofish_smoke.py`, `test_mirofish_smoke.py`, `recommendations/MIROFISH-SMOKE.md`.
**Fence:** no `--live`, no writer/AUTOEXEC/US_LIVE/SPOT/NT/SELF_ADJUST/AUTO_RAISE/treasury flips. No weight math. No production ledger writes.

## Applied this session

`yagni:` overnight swarm in the smoke. `mirofish_runner.run_simulation` stays the night job. **Not shipped.**

`yagni:` calling `mirofish_seed_accurate.build_seed` (CoinGecko/Yahoo/Kalshi/RSS). Fixture injects 4 lines of JSON. Seed HTTP remains overnight. **Not shipped.**

`yagni:` a second accuracy ledger or mock FTL class. Reuse `record`/`score` + two JSONL rows. **Not shipped.**

`yagni:` crontab for the smoke. Burton runs it by hand before flips. **Not shipped.**

`reuse:` `process_mirofish_report`, `apply_seed_text`/`attach_to_signal`, `mal.record`/`score`, `iog.gather`. Stdlib `urllib` for live-lite (no `requests`).

## Findings (biggest cut first)

`yagni:` wrapping live-lite in a client class / retry loop. One POST, 90s cap, SKIP on any error.

`yagni:` LMS **and** Ollama in parallel. Sequential probe; first that answers wins.

`shrink:` no new fixture file on disk. Constants live in the smoke module.

`yagni:` changing extractor/ledger/gatherer. Smoke is a caller. Weight caps untouched.

## Not cut (spec)

- Fixture path always; live-lite optional SKIP.
- Exit nonzero only on fixture fail.
- Mocked fee-true ingest; production JSONL not appended.
- Docs list exact scripts + what overnight still covers.

**net:** 1 script, 1 test, 2 recs, version bump, 0 deps, 0 cron, 0 live flags. Lean enough. Ship.
