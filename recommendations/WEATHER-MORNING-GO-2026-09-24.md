# WEATHER-MORNING-GO — 2026-09-24

**Owner:** Valedia runtime (Mac Studio M1 crontab / glance-adjacent scripts under `~/.openclaw`).  
**Grok Bot routine:** "Kalshi weather morning scan" is **PAUSED** — morning go does **not** depend on Grok Bot wake.  
**Written:** 2026-09-23 23:11:11 PDT  
**WEATHER_LIVE:** still **0** (not flipped). Oil fenced (`OIL_BRACKET`, `KXWTI`, `KXOIL`).

## How ~7:04 PT fires
| Piece | Value |
|---|---|
| Crontab | `4 7 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/weather_morning_go.sh` |
| Wrapper | pins miniconda python; sources `~/.openclaw/.env`; never sets `WEATHER_LIVE` |
| Entrypoint | `workspace/scripts/integrations/weather_morning_go.py` |
| Tree | Valedia live `~/.openclaw/workspace/` (promoted weather stack from build 0.74.0) |
| Nearby | `0 7` MORNING BRIEF (`kalshi_alert.py`) unchanged; weather go is separate at 7:04 |

Flow: **preflight → (Burton may set WEATHER_LIVE=1 only if PASS) → live FV lookup → morning_scan / alert → admit only if live already on**. Oil never. Jev shadow-only.

## Promote what / where
Copied **only** weather-related scripts + fee-map + station/tz deps from `workspace-v2` (0.74.0 PASS) → `workspace` (Valedia cron tree):

- `weather_shadow.py`, `weather_live.py`, `weather_live_preflight.py`, `weather_lookup.py`, `weather_nws.py`, `weather_arb.py`, `weather_trade.py`
- `weather_sources/` (`_get`, `weather_com`, `nws_cli`, `accuweather`, `ecmwf`)
- `informed_before_create.py`, `jev_shadow.py`, `kalshi_fee_map.py`, `shadow_common.py`, `session_clocks.py`
- `oil_shadow.py`, `oil_weather_shadow_tick.py` (shadow deps; oil stays fenced)
- fixtures under `scripts/integrations/fixtures/weather/`
- `recommendations/kalshi-fee-map.json` (38 KXHIGH*/KXLOW* city series)
- `models/weather_city_cache.json`
- Surgical patch: live `valedia_config.weather_live_enabled` → honor `WEATHER_LIVE=1` **only if** preflight PASS (was hard-off dead path). Constant `WEATHER_LIVE=False`. Oil fence untouched.
- Trivial fix: `weather_lookup` now live-fetches TWC `daily/15day` when `live=True` (needs `WEATHER_COM_API_KEY`). Synced to both trees.

Backup: `workspace/recommendations/WEATHER-PROMOTE-BACKUP-20260923-230851/`

## Post-promote preflight (morning tree = workspace)
```
WEATHER_LIVE_PREFLIGHT PASS
flip_allowed=True
  [PASS] settle_fv_twc_daily
  [PASS] informed_fail_closed_no_fv
  [PASS] stations_tz          (LAS/TLV present)
  [PASS] fee_map_kxhigh_kxlow
  [PASS] lottery_late_create_path
  [PASS] jev_shadow_fail_open
  [PASS] live_ledger_and_alert
  [PASS] fence_tonight_weather_off_oil_fenced  (weather_live_enabled=False, WEATHER_LIVE='0', oil_fenced=True)
  [PASS] risk_caps_kelly
```

## FV probe (read-only, workspace / promoted stack)
| City | Path | Result |
|---|---|---|
| MIA | TWC daily high (rules Weather Company) | **blocked** `no_api_key` — `WEATHER_COM_API_KEY` unset in `~/.openclaw/.env` |
| TLV/LAS | TWC daily high (CLILAS) | **blocked** `no_api_key` |
| MIA | NWS CLI live | **OK** settlement FV **83°F** (`nws_cli`, confidence final, client httpx) |
| LAS (TLV station) | NWS CLI live | **OK** settlement FV **93°F** (`nws_cli`) |

Smoke `no_fv` tonight: TWC-settled Kalshi dailies need `WEATHER_COM_API_KEY`; without it lookup fail-closes (correct). NWS path works when rules name NWS. Even with TWC key, forecast payloads are research-only until observation/final — INFORMED still refuses create on `no_fv` / `forecast_not_final`.

Dry-run `weather_morning_go.py` (WEATHER_LIVE=0): n_markets=72, n_alert=0, create_order=False, tags `no_api_key`×36 + `forecast_not_final`×36, admit skipped, oil_fenced=True.

## Checklist paths
- `~/.openclaw/workspace-v2/recommendations/WEATHER-SMOKE-2026-09-23.md` (Morning go checklist section)
- `~/.openclaw/workspace/recommendations/WEATHER-SMOKE-2026-09-23.md` (mirrored)
- This file: `recommendations/WEATHER-MORNING-GO-2026-09-24.md` (both trees)

## Remaining blockers for 7:04 AM go-live
1. **Set `WEATHER_COM_API_KEY`** in `~/.openclaw/.env` before expecting TWC settle FV on KXHIGH/KXLOW (most Kalshi city dailies).
2. **Burton flip only after fresh preflight PASS:** `WEATHER_LIVE=1` in `.env` (script never flips). Re-run `python3 weather_live_preflight.py` on `workspace` first.
3. Until TWC key lands: morning scan stays shadow / `no_fv` on TWC-settled books; NWS-settled books can score when rules say NWS.
4. Oil stays fenced — do not touch `OIL_LIVE` / `LIVE_EXEC_FENCED`.

**WEATHER_LIVE left=0**

## Valedia owns WEATHER_LIVE (2026-09-24 ~03:10 PT)

Burton: **WEATHER_LIVE needs to be decided by Valedia** — not Grok Bot, not a chat flip.

At ~7:04 PT `weather_morning_go.py`:
1. Runs preflight
2. **PASS + flip_allowed → writes `WEATHER_LIVE=1`** into `~/.openclaw/.env`
3. **Else → writes `WEATHER_LIVE=0`**
4. Scans open KXHIGH*/KXLOW*; admits only **NWS-settled** official alerts when live is on
5. TWC/weather.com settle stays shadow on the free stack; oil stays fenced; Grok Bot Kalshi weather routine stays **paused**

`--dry-decide` logs the decision without persisting LIVE or admitting.

Updated: 2026-09-24 03:06:52 PDT
