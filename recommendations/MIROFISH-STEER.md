# MIROFISH STEER — Burton 2026-09-22 ~16:11 PT

**Burton ask:** Run MiroFish **now** (outside the 03:00 PT cron) so we can verify a full finish.

## Priority for MIROFISH-100-E2E (in flight)
1. Land the smallest durable fix for overnight ontology timeout (likely sync on Ollama `qwen3-8b-mf:latest` `:11435` for ontology/graph; keep Bionic Ornith for report — already documented in `.env`).
2. **Immediately** run a full out-of-schedule primary equivalent:
   `~/.openclaw/workspace/scripts/mirofish_primary.sh`
   (or the same runner path cron uses). Respect gpu-steward lease. Do **not** wait for 03:00.
3. Done only when that run exits 0 and `models/mirofish_signal.json` on the live tree is overwritten with real swarm source (not `intel_refresh`).
4. Log stage timings into `MIROFISH-100-E2E.md` Results.

Do not treat a partial ontology PASS as Done. Do not kill `ot-valedia-desk` / `valedia-glance`. No venue/live flips.
