# CONTROLS-READOUT — every Valedia gate on/off

Version `0.49.0`. **Read-only.** This slice does not flip any gate.

Single source of truth: `valedia_config.controls_snapshot()`.

```
{version, sha, branch, dirty,
 items: [{name, value, source, category, notes}, ...]}
```

`source` is `const` | `env` | `file` (halt file only). Env wins over the committed constant when the flag helper already reads env.

## Surfaces

| Surface | How |
|---------|-----|
| JSON | `GET /status` → `controls` (same snapshot) |
| UI | `GET /` Controls card — ON/OFF (green/red); writers `valedia`/`none` |
| CLI | `python3 scripts/integrations/valedia_controls.py` |

Cockpit stays GET-only. No order buttons. No live flips from the panel or CLI.

## Included (at least)

Halt, Kalshi fee-map / EV preflight / maker-first / fill-ledger demote, `VALEDIA_JEV` (default **False**), `VALEDIA_SELF_ADJUST`, `MIROFISH_AUTO_RAISE`, `POLYMARKET_US_LIVE`, POLY-LOOP (`POLY_LOOP_EDGE_PTS` / `MAX_OPEN` / `DD_STOP` / `DD_STOP_ARMED`, read-only), `VENUE_WRITER.*` per venue, oil/`NO_HARVEST` fence (`LIVE_EXEC_FENCED` + series `KXWTI`/`KXOIL`), `WEATHER_LIVE`/`SCALPER_LIVE` hard-off, `NAUTILUS_LIVE`/`NAUTILUS_ENABLED`, crypto `PAPER/SHADOW`, version/sha. Also existing: SPOT GET, fortress, rebalancer autoexec, treasury auto, `EXEC_DRY_RUN`.

## Fence (unchanged)

Committed defaults stay **False** for live/autonomy gates. Do not enable Jev. Do not Poly live / writers / wallets. Do not auto-flip `KALSHI_FEE_MAP_GATE`.

## Tests

```bash
python3 scripts/integrations/test_controls_readout.py
python3 scripts/integrations/test_openterminal_valedia.py
python3 scripts/integrations/test_h4.py
python3 scripts/integrations/test_kill_switch.py
```

## Next

**AUTONOMY-2** shipped `0.50.0` (`VALEDIA_JEV` still False in git). Then fee-map **runtime** flip (env / LaunchAgent only; do not commit True).
