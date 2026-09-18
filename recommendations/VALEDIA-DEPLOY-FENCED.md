# Valedia fenced deploy (Slice 15)

**When:** 2026-09-17  
**Source:** `~/.openclaw/workspace-v2` `valedia-v2-grok-build` (Slices 1–14, tip `3f2cd28` / code `5996181`)  
**Dest:** live `~/.openclaw/workspace` (runtime; git `master` was `7e0ff56` / tag `pre-ruflo-B1`)  
**Fence:** no `--live`, no venue orders, no wallet/spend, no API-key edits, no `VENUE_WRITER` flip, no `REBALANCER_AUTOEXEC=True`, no NT install/live. `SPOT_RECONCILE_HTTP` stays default **False**.

This is a **code sync**, not a flag lift. Live secrets stay where they are (`~/.openclaw/.env`, gitignored `.env*`). Models/ledgers stay live-owned.

## Do not copy

- `~/.openclaw/.env` and any `.env*` / `*.pem` / `*.key`
- `models/` (pkl, jsonl, live ledgers, queues)
- `archive/`, `__pycache__/`, `*.pyc`
- crontab (installer rewrites the whole table — see checklist)
- MCP/git metadata, `.claude-flow/`, `.swarm/`

## File list (content-diff only)

Checksum compare live vs v2. Time-only files (risk engine, kernel v16e, scanners, …) **left alone**.

### New on live

```
scripts/integrations/coinbase_order_body.py
scripts/integrations/hyperliquid_order_body.py
scripts/integrations/intel_opportunity_gatherer.py
scripts/integrations/nautilus_fill_sink.py
scripts/integrations/nautilus_intent.py
scripts/integrations/nautilus_shadow_coinbase.py
scripts/integrations/nautilus_shadow_parity.py
scripts/integrations/nautilus_shadow_venues.py
scripts/integrations/polymarket_order_body.py
scripts/integrations/spot_order_get.py
scripts/integrations/spot_reconcile.py
scripts/integrations/test_kalshi_fill_ledger.py
scripts/integrations/test_nautilus_intent.py
scripts/integrations/test_nautilus_shadow_coinbase.py
scripts/integrations/test_rebalancer_shadow.py
scripts/integrations/test_reconcile_run.py
scripts/integrations/test_slice7.py
scripts/integrations/test_slice8.py
scripts/integrations/test_slice9.py
scripts/integrations/test_slice10.py
scripts/integrations/test_slice11.py
scripts/integrations/test_slice12.py
scripts/integrations/test_slice13.py
scripts/integrations/test_slice14.py
scripts/integrations/test_sports_engine.py
scripts/integrations/test_sports_engine_b1_extra.py
scripts/integrations/test_spot_fill_ledger.py
scripts/integrations/test_spot_reconcile.py
```

### Replace on live (Slices 1–14 behavior)

```
scripts/integrations/allocator_run.py
scripts/integrations/alpaca_executor.py
scripts/integrations/coinbase_trader.py
scripts/integrations/dca_module.py
scripts/integrations/fee_engine.py
scripts/integrations/forward_test_ledger.py
scripts/integrations/kalshi_executor.py
scripts/integrations/kelly_sizing.py
scripts/integrations/master_allocator.py
scripts/integrations/mirofish_accuracy_ledger.py
scripts/integrations/mirofish_ledger_run.py
scripts/integrations/pnl_run.py
scripts/integrations/pnl_tracker.py
scripts/integrations/quant_engine.py
scripts/integrations/rebalancer.py
scripts/integrations/reconcile_run.py
scripts/integrations/signal_fusion.py
scripts/integrations/sports_engine.py
scripts/integrations/sports_run.py
scripts/integrations/strategy_modules.py
scripts/integrations/test_category_gate.py
scripts/integrations/unified_brain.py
scripts/integrations/valedia_config.py
scripts/integrations/weather_arb.py
scripts/integrations/weather_trade.py
scripts/install_cron.sh
scripts/valedia_consistency_check.py
skills/valedia/cron-schedule.md
recommendations/VALEDIA-DEPLOY-FENCED.md
```

Shipped-off after apply (must stay False / valedia|none):

| Flag | Expected |
|------|----------|
| `NAUTILUS_ENABLED` | False |
| `NAUTILUS_LIVE` | False |
| `SPOT_RECONCILE_HTTP` | False (env unset) |
| `REBALANCER_AUTOEXEC` | False (env unset) |
| `VENUE_WRITER` coinbase/kalshi/alpaca | `valedia` |
| `VENUE_WRITER` hyperliquid/polymarket/kraken | `none` |
| `LIVE_CLIENT_HOOK` | None |
| `SCALPER_LIVE` / `WEATHER_LIVE` | False |
| `MIROFISH_AUTO_RAISE` | False |

Kalshi still writes live through Valedia (fill-before-EXECUTED). Weather/scalper cannot live-buy. Spot GET stays mock.

## Cron checklist (do **not** run `install_cron.sh`)

Installer greps and **rewrites** crontab. This slice does not install.

Live now vs v2 installer:

| Job | Live crontab | v2 `install_cron.sh` | Action |
|-----|--------------|----------------------|--------|
| SPORTS | `0 */2` | `0 */2` | keep |
| ALLOCATOR | `0 */4` (collides sports every 4h) | `20 */4` | Burton: stagger when ready |
| RECONCILE | `0 */6` | `0 */6` | keep; GET HTTP still off |
| MIROFISH LEDGER | `0 8 * * *` already live | not in installer | keep live line |
| BRAIN / settle / etc. | live consolidated table | installer subset | **do not replace live table** |

After smoke, Burton may add (copy-paste, do not run installer):

```
# report-only shadow parity (Slice 16). No POST. No flag flips.
45 6 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh parity_run.py "SHADOW PARITY" 2>&1
```

Allocator stagger (optional, kills 4h double-bet race):

```
# replace `0 */4 ... allocator_run.py` with:
20 */4 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh allocator_run.py "MASTER ALLOCATOR" 2>&1
```

## Rollback

1. **On-disk tarball** (working tree, not just git HEAD):  
   `~/.openclaw/backups/live-scripts-pre-s15-20260917_224401.tar.gz`
2. Restore:  
   `tar -xzf ~/.openclaw/backups/live-scripts-pre-s15-20260917_224401.tar.gz -C ~/.openclaw/workspace`
3. Git tags:  
   - live `pre-ruflo-B1` / `pre-arc-f-s15` (`7e0ff56`) = before this apply  
   - live `fenced-deploy-s15` = after apply  
   - v2 `pre-ruflo-B1` unchanged
4. Do **not** `git reset --hard` live `master` — that would drop uncommitted models.

## Smoke (post-sync, no network)

From `~/.openclaw/workspace/scripts/integrations`:

```
python3 -c "import valedia_config as vc; assert vc.NAUTILUS_ENABLED is False; assert vc.NAUTILUS_LIVE is False; assert vc.SPOT_RECONCILE_HTTP is False; assert vc.spot_reconcile_http_enabled() is False; assert vc.REBALANCER_AUTOEXEC is False; assert vc.VENUE_WRITER['coinbase']=='valedia'; assert vc.VENUE_WRITER['hyperliquid']=='none'; assert vc.VENUE_WRITER['polymarket']=='none'; print('flags OK')"
python3 test_slice7.py
python3 test_slice8.py
python3 test_slice9.py
python3 test_slice10.py
python3 test_slice11.py
python3 test_slice12.py
python3 test_slice13.py
python3 test_slice14.py
python3 test_kalshi_fill_ledger.py
python3 test_spot_reconcile.py
```

Fail closed: if flags are not as tabled, restore the tarball before the next cron tick.

## How this was applied

```
rsync -ai --checksum --no-times  # content-diff files only, no --delete
# never: crontab installer, export SPOT_RECONCILE_HTTP=1, VENUE_WRITER=nautilus
```
