# POLY-LOOP — Polymarket US paper desk (0.54.0)

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

US + Gamma aliases: `endDate`/`endDateIso`/`event.endDate`; `liquidity`/`liquidityNum` as number or `{value}`; `resolutionSource` / `metadata` / `tags[].league.resolution` / `resolvedBy`. Hours still fail closed if missing.

## Resolution (0.53.0)

US `GET /v1/markets` + `GET /v1/market/slug/{slug}` **do not** document `resolutionSource` / `fairValue` (see Markets API overview). Rules live in `description` (and `event.rules` / `market.rules` / `resolutionCriteria` when present).

`resolution_source()`:

1. Explicit short name or URL: `resolutionSource` / `resolution_source` / `resolvedBy` / `resolver` / `oracle` / `resolutionURL` / nested `event`/`market`/`metadata` / `tags[].league.resolution`.
2. Else scan rules text for a named official source (NWS, AP, Fed, BLS, Reuters, MLB, …) or a URL host.
3. Else if any rules/description/resolution **text** exists → `rules_text:<12-hex>:<snippet>` (audit token; not invented).
4. Fail closed (`None` → skip `no_resolution`) only when that text is **zero**. Question/title/slug is vibes, not a source.

Resolution helper must return something before FV arms. Honest skips: `no_resolution` vs `no_fv` vs `edge_lt_8`.

HTTP smoke (2026-09-21, `POLYMARKET_US_HTTP=1` LIVE=0, keys present, no create): list+get_market both carry `description` (~500 chars) and **omit** `resolutionSource` / `resolvedBy` / `rules` / `fairValue` / nested `event`. GET slug adds no extra resolution fields vs list. 0.53.0 `resolution_source` on 8 rows: 4 named (e.g. MLB from description) + 4 `rules_text`. Full loop `skip_reasons` **`no_fv`×20** (was `no_resolution`×20 on 0.52.0). Remaining gap was numeric FV (no `fairValue`, exact-slug cache miss). `live=0` `create_order=0`.

## Fair value (0.54.0)

Gate: `resolution_source()` must return a value. Then first hit wins:

| source | when | ledger `fv=` |
|--------|------|----------------|
| blotter | `data/poly-loop/fv_overrides.json` `overrides` keyed by slug/id/event | `override` |
| market | `fairValue` / `fair_value` / `fv` / `p_yes` / `modeled_prob` on the row or tags | `market` |
| cache | MiroFish/persona JSON, fuzzy slug/id/event (exact → normalized `_`/`-` → unique contains, keys ≥8 chars) | `cache` |
| modeled | `priceHistory` / `midHistory` length ≥20 → `modeled_prob.ema_fair` (YES EMA, **not** P(up)) | `modeled` |
| tag_prior | `cheap_tags` / `classify_market` nav ≠ OTHER, **or** official label NWS/MLB/Fed/… mapped to a nav bucket. FV = `0.5*mid + 0.5*0.5` (favorite-longshot shrink). Needs mid. | `tag_prior` |

Fail closed (`no_fv`) if none apply. `rules_text` + unclassified + empty caches + no series + empty blotter = `no_fv`. Top-level cache `p_yes` is **not** applied to every market.

`tag_prior` is a documented 50/50 mid vs uninformed 0.5, **not** a per-question invention. `|FV−mid| = 0.5·|mid−0.5|` so 8pt flags need mid ≤0.34 or ≥0.66. Tighter mids skip `edge_lt_8`.

### `fv_overrides.json` schema

```
{
  "schema": "poly-loop-fv-overrides/v1",
  "note": "Desk/manual P(yes) in [0,1]. Not live orders.",
  "overrides": { "some-slug": 0.62 }
}
```

Value may be a float or `{"fv": 0.62, "note": "desk"}`. Seed file ships with `"overrides": {}`.

HTTP smoke (2026-09-21, `POLYMARKET_US_HTTP=1` LIVE=0, 20-row pull, empty blotter, no MiroFish per-slug keys): **n=5** DRY/SHADOW proposals, all `fv=tag_prior` (MLB longshots, mids 0.05–0.25, edges 12–23pt). `skip_reasons`: `slip`×7, `max_open`×5 (paper cap filled), `no_fv`×2, `edge_lt_8`×1. Residual `no_fv` is unclassified `rules_text` without series/cache — not all 20. `live=0` `create_order=0`.

## Loop

1. Active Poly US markets (existing client). Skip resolve-under-2h and liquidity &lt; $5k. Pull-time filter so stale NFL / zero-liq rows cannot occupy the whole batch.
2. Fair value: **resolution helper must return something** (explicit name, extracted official label, or `rules_text` hash — see Resolution 0.53.0). Numeric FV from the 0.54.0 ladder (override → market fields → fuzzy cache → modeled EMA → tag_prior). Ledger reason includes `fv=cache|modeled|tag_prior|market|override`. **No** vibes, **no** new Ollama/TypeSafe. Skip `no_fv` if none apply.
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
