# MIROFISH-SMOKE — minutes-scale wiring gate (0.37.0)

**Updated:** 2026-09-18 PT
**Harness:** `python3 scripts/integrations/mirofish_smoke.py`
**Fence:** No `POLYMARKET_US_LIVE`, no `VENUE_WRITER` flips, no `REBALANCER_AUTOEXEC`, no `NAUTILUS_LIVE`, no `VALEDIA_SELF_ADJUST`, no `MIROFISH_AUTO_RAISE`, no treasury auto, no `--live` orders. Fixture is local JSON only.

## Verdict

| Path | Result | Notes |
|------|--------|-------|
| Fixture (always) | **PASS** (offline; see run below) | Synthetic seed JSON → extractor → `neural_enriched` → ledger `record` + mocked fee-true `score` → intel `gather`. Exit 0. |
| Live-lite (optional) | **SKIP** if Ollama/LMS down; **PASS** if one short local generate returns | Hard timeout ≤90s. Unreachable does **not** fail the smoke. |
| Overnight full swarm | **not this gate** | Still the 5-day / corpus job. |

Burton can run the harness anytime and get PASS/FAIL in minutes. Overnight cron remains for the full corpus. **Do not flip live venues on a smoke PASS.**

## What was tested (fixture)

1. **Seed advisory (no HTTP).** `mirofish_neural_enrich.apply_seed_text` on a tiny synthetic briefing — same advisory section `mirofish_seed_accurate.build_seed` would append. CoinGecko / Yahoo / Kalshi / RSS **not** called.
2. **Extractor.** `process_mirofish_report` on synthetic news/markdown JSON → `models-dir/mirofish_signal.json` with `source=mirofish_swarm` and a BTC-USD direction.
3. **Neural tag.** Injected NT-1-shaped enrichment → `neural_enriched=True` on the signal and per-symbol `neural.present`. Scores/bias/weight unchanged.
4. **Accuracy-ledger ingest (mocked fee-true).** `record()` then a fake FTL entry+settlement (`realized_pnl_net=2.5`, `source=live`, matured ts) → `score()` writes a `fee_true` result that copies `neural_enriched`. Production `~/.openclaw/workspace/models/mirofish_accuracy_ledger.jsonl` is **not** appended.
5. **Intel gatherer.** `intel_opportunity_gatherer.gather` on the temp models dir. No `enqueue_intent`. Shadow-only.

## What was not run

- `mirofish_runner.run_simulation` (ontology + Neo4j + overnight swarm on `:5001`)
- `mirofish_seed_accurate.build_seed` (live APIs)
- Weight math / `effective_mirofish_weight` raise
- `MIROFISH_AUTO_RAISE` / `VALEDIA_SELF_ADJUST`
- Venue writers, `--live`, treasury auto, Nautilus live

## Scripts touched (read / called, not rewritten)

| Script | Smoke use |
|--------|-----------|
| `mirofish_seed_accurate.py` | Documented overnight seed. Smoke uses the **same** neural `apply_seed_text` hook the seed builder already calls. No `build_seed()`. |
| `mirofish_signal_extractor.py` | `process_mirofish_report` |
| `mirofish_neural_enrich.py` | `apply_seed_text` + extractor `attach_to_signal` |
| `mirofish_accuracy_ledger.py` | `record` + `score` against mock FTL |
| `intel_opportunity_gatherer.py` | `gather` / `collect_mirofish` (no enqueue) |
| `mirofish_runner.py` | **not invoked** (overnight) |

Weight caps stay 0.10 unproven / 0.15 proven. `MIROFISH_AUTO_RAISE=False`.

## Latency (measured 2026-09-18, this ship)

| Step | Result | Time |
|------|--------|------|
| Fixture (seed→extractor→neural→ledger mock→gatherer) | PASS | **6 ms** |
| Live-lite `POST :11435/api/generate` `qwen3-8b-mf:latest` | PASS | **7432 ms** |
| **Wall** | PASS | **7.44 s** (budget 300 s) |

`--no-live` fixture-only is ~1–10 ms after import. Live-lite empty `response` body is still PASS: the generate JSON returned (model is up). Overnight swarm is the corpus job, not this ping.

## Overnight still covers

- Full ontology generate + multi-agent sim (`localhost:5001`, dedicated Ollama `:11435`)
- Live seed APIs (CoinGecko, Yahoo chart, Fear/Greed, Polymarket gamma, Kalshi, news RSS)
- Real 5-day horizon vs **actual** fee-true FTL settlements
- Proven bar (`n≥20` scored, accuracy `>55%`) — evidence only; auto-raise stays off
- Signal freshness / `mirofish_ready` marker / BRAIN OPEN gate (`mirofish_healthcheck.py`)

Smoke PASS ≠ “MiroFish is accurate.” Smoke PASS = **the pipes are connected.**

## Run

```bash
python3 scripts/integrations/mirofish_smoke.py          # fixture + live-lite if up
python3 scripts/integrations/mirofish_smoke.py --no-live
python3 scripts/integrations/test_mirofish_smoke.py     # offline
```

Exit 1 only if fixture fails. Live-lite SKIP is still overall PASS.

**Next = STOP.** Do not flip live.
