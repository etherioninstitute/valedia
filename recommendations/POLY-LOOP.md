# POLY-LOOP — Polymarket US paper desk (0.52.0)

**Name is POLY-LOOP** (not POLY-GROK-LOOP).

Sits on POLY-LEARN. The loop **always runs**. Execution stays behind the existing `POLYMARKET_US_LIVE` lever (default **off** = DRY/SHADOW proposals only). This slice does **not** flip that lever in git. `VENUE_WRITER["polymarket"]` stays `none`. No second writer. No browser OMS. No keys in repo.

`create_order` still raises `LiveWriteBlocked` while LIVE is off (existing US client). POLY-LOOP never calls create/cancel.

## How to run

```
python3 scripts/integrations/test_poly_loop.py
python3 scripts/integrations/poly_loop.py
```

HTTP reads use the existing US client (`POLYMARKET_US_HTTP=1` for live public GET). Writes still need `POLYMARKET_US_LIVE=1` **and this loop still does not POST**.

Ledger: `data/poly-loop/ledger.jsonl` (row **before** propose). State: `data/poly-loop/state.json`. Last run: `data/poly-loop/last_run.json`. Peak-DD post-mortem: `data/poly-loop/post-mortem.json`.

Optional 10m cron later: only if flag file `data/poly-loop/INSTALL_CRON` is present **and** Burton asks. This slice does not install crontab.

Allocator / POLY-LEARN print a **status line only** (`poly_loop.status_line`). Not mixed into live `gather_all_opportunities`.

## Universe (0.52.0)

`GET /v1/markets` with `active=true`, `closed=false`, `archived=false` (do **not** send `endDateMin` — gateway then ignores `closed` and returns stale NFL). Over-fetch (~5×) then drop closed/archived/expired (`hours_left < 2`) and **present** `liquidity < $5k` before the batch fills. Missing liq ≠ 0 (US list often omits it). If the extra query 400s, retry `active=true` only. `last_run.json` `skip_reasons` + `pull.dropped` show why (expired / low_liq / no_resolution / no_fv / edge_lt_8 / slip / …). Unwrap `get_market` `{market: {...}}`.

US + Gamma aliases: `endDate`/`endDateIso`/`event.endDate`; `liquidity`/`liquidityNum` as number or `{value}`; `resolutionSource` / `metadata` / `tags[].league.resolution` / `resolvedBy`. Fail closed if hours or named source still missing.

HTTP smoke (2026-09-21, `POLYMARKET_US_HTTP=1` LIVE=0): `closed=false` returns live 2026 MLB; `skip_reasons` was `no_resolution`×8 (US payload has empty tags / no resolutionSource). Not expired-NFL domination. Filter held. Still no create.

## Loop

1. Active Poly US markets (existing client). Skip resolve-under-2h and liquidity &lt; $5k. Pull-time filter so stale NFL / zero-liq rows cannot occupy the whole batch.
2. Fair value: **named resolution source required**. Numeric FV from market/tags `fairValue`/`fv`/`p_yes`, else slug lookup in existing `mirofish_neural_enrichment.json` / `persona_lens_scores.json` caches (`poly_learn.cheap_tags` still tags only). **No** vibes, **no** new Ollama/TypeSafe, **no** `modeled_prob` price-series (crypto, not poly). Skip `no_fv` if none of those exist.
3. Flag only if `|FV − mid| ≥ 8` percentage points (`POLY_LOOP_EDGE_PTS`).
4. Book/BBO flatten: full size at ≤ 3% slippage or skip (`BLOCKED`).
5. Half-Kelly (`quant_engine.kelly_size` / `KELLY_SAFETY_FRACTION`); hard cap **6%** bankroll; max **5** open paper positions.
6. Ledger row before propose: market id/slug, FV, mid, edge pts, size, exit condition, reason, status `DRY/SHADOW` or `BLOCKED`.
7. Peak drawdown **40%** from paper peak → stop proposing + post-mortem file. **No auto category flip.**
8. Self-fund JSON only: paper balance vs subscription ($20) + reserve (`TREASURY_FLOOR_POLYMARKET_US`). No withdraw path.

## Live lever

| Lever | Git default | POLY-LOOP |
|-------|-------------|-----------|
| `POLYMARKET_US_LIVE` | False (env=1 Burton only) | off → DRY/SHADOW. on → still this loop does not create; existing US client + halt still bind |
| `VENUE_WRITER["polymarket"]` | `none` | do not flip |
| `WEATHER_LIVE` / `SCALPER_LIVE` / `VALEDIA_TREASURY_AUTO` / `NAUTILUS_LIVE` | False | do not enable |
| Halt `VALEDIA_TRADING_HALT` | False | BLOCKED if on |

### Ladder (Burton runtime only, later)

1. **Now:** loop + ledger. Preview still works. Create `LiveWriteBlocked`.
2. Keys in `~/.openclaw/.env` only. Never commit.
3. `POLYMARKET_US_HTTP=1`, LIVE unset. Reads + BBO/book.
4. Burton `POLYMARKET_US_LIVE=1`. Tiny qty=1 via **existing** `PolymarketUSClient.create_order`. Do **not** flip writer. Do **not** add a second writer.
5. Fill-true GET. Ack ≠ fill.
6. Stop. Writer stays `none`.

## Snapshot (read-only)

`controls_snapshot()` includes `POLY_LOOP_EDGE_PTS` (8), `POLY_LOOP_MAX_OPEN` (5), `POLY_LOOP_DD_STOP` (0.40), `POLY_LOOP_DD_STOP_ARMED` (True). Consts, not env flips.

## Fence (must still be true)

`POLYMARKET_US_LIVE` False in git · writer polymarket `none` · no weather/oil live unfence · no CB/Alpaca writer restore · no TREASURY_AUTO · no NAUTILUS_LIVE · no crontab from this slice.
