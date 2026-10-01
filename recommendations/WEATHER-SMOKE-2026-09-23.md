# WEATHER SMOKE — 2026-09-23

**Timestamp:** 2026-09-23 21:32:37 PDT (PT)  
**Host:** Mac Studio M1 (`b3f456da-e4fc-42d6-8147-a47ad76a8982`)  
**Mode:** READ-ONLY (no orders, no `WEATHER_LIVE` flip, oil fence untouched)  
**WEATHER_LIVE left=0** (confirmed before and after)

## Versions
| Tree | Path | `VALEDIA_VERSION` |
|---|---|---|
| Build (go-live path) | `~/.openclaw/workspace-v2/` | **0.74.0** |
| Live (contrast) | `~/.openclaw/workspace/` | **0.44.0** |

## 1. Fence / env (secrets redacted)
| Name | Value |
|---|---|
| `WEATHER_LIVE` (`~/.openclaw/.env` + process) | **0** |
| `OIL_LIVE` | \<unset\> |
| `VALEDIA_JEV` | 1 |
| `KALSHI_API_KEY_ID` | \<set:redacted\> |
| `KALSHI_API_KEY` / `KALSHI_PRIVATE_KEY` / `KALSHI_KEY_ID` / `KALSHI_ACCESS_KEY` | \<unset\> (auth uses key id + `~/.openclaw/kalshi_private_key.pem`) |
| `vc.weather_live_enabled()` (build) | False |
| `LIVE_EXEC_FENCED` (shadow tick) | `OIL_BRACKET,WEATHER_ARB` |

**Explicit: WEATHER_LIVE left=0. Zero Kalshi create/place/cancel calls in this smoke.**

## 2. Build-tree preflight (`workspace-v2` / 0.74.0)
```
WEATHER_LIVE_PREFLIGHT PASS
flip_allowed=True
  [PASS] settle_fv_twc_daily
  [PASS] informed_fail_closed_no_fv
  [PASS] stations_tz          city_tz includes ATL…TLV (LAS/TLV present)
  [PASS] fee_map_kxhigh_kxlow
  [PASS] lottery_late_create_path (informed_before_create hit)
  [PASS] jev_shadow_fail_open (jev_on=True, fail_open_module=yes)
  [PASS] live_ledger_and_alert (WEATHER-MORNING-ALERT + weather_live_ledger hooks)
  [PASS] fence_tonight_weather_off_oil_fenced (weather_live_enabled=False, WEATHER_LIVE='0', oil_fenced=True)
  [PASS] risk_caps_kelly (KELLY_SAFETY_FRACTION=0.5, KELLY_BASE_CAP=0.08)
```
**Status: PASS · flip_allowed=True** (do not flip in this smoke)

## 3. Live-tree preflight (`workspace` / 0.44.0) — contrast
```
WEATHER_LIVE_PREFLIGHT FAIL
flip_allowed=False
blockers:
  - stations_or_tz_unmapped          (tz_gaps=['LAS','TLV'])
  - fee_map_missing_kxhigh_kxlow     (weather_fee=[] n=0)
  - lottery_late_not_in_create_path  (informed_before_create miss)
  - live_ledger_alert_hooks_missing
  [PASS] settle_fv_twc_daily
  [PASS] informed_fail_closed_no_fv
  [PASS] jev_shadow_fail_open
  [PASS] fence_tonight_weather_off_oil_fenced
  [PASS] risk_caps_kelly
```
**Status: FAIL · flip_allowed=False** (expected old gaps on 0.44.0)

## 4. Kalshi auth smoke (read-only)
- Interpreter: `/opt/homebrew/Caskroom/miniconda/base/bin/python3` (system `python3` lacks `requests`)
- `kalshi_auth.py` portfolio summary: **Auth OK**
- Balance: **$59.12** · Portfolio: **$74.72** · Positions listed (read)
- Weather markets GET (`/markets?status=open&series_ticker=…`):
  - Sampled series totals: **96** open markets across KXHIGHMIA/NY/LAX/CHI/DEN/TLV, KXLOWTLV, KXHIGHTATL (12 each where live; HOU/PHX/KXTEMP=0 in this pull)
  - MIA high: 12 · TLV high: 12 · TLV low: 12 · LAS series tickers: 0 (TLV covers Las Vegas / CLILAS)
- **No orders created.**

## 5. Shadow / morning path
- `oil_weather_shadow_tick.py --dry`: **OK** — `dispatchable=0/0 create_order=False/False WEATHER_LIVE=False` (oil+weather fenced). Printed `jev fail-open: URLError` once (fail-open path exercised).
- Live-book `weather_shadow.morning_scan` on 8 MIA/TLV contracts: **n=8, n_alert=0, create_order=False, dispatchable=0**. Rows tagged `no_fv` (live TWC settle FV not returned without credentials/fixtures); each row Jev-stamped (`enabled=True`, `oms=False`, model `jev-1.13.0`, agree=abstain).
- FV sources: **fixture path hit `weather_com`** (TWC parse observation FV=88°F for MIA-shaped payload; `wl.lookup` → `fv_source=weather_com`, `informed_pass=False`, `create_order=False`). Live network lookup without fixtures → no FV (fail-closed).
- Morning alert writer: `write_morning_alert` **works** → `recommendations/WEATHER-MORNING-ALERT-SMOKE-2026-09-23.md` (smoke path; live scan produced no alertable edge because `no_fv`).
- No dedicated `weather_shadow` CLI `__main__`; used library `morning_scan` + `--dry` tick. Did **not** invent create paths.

## 6. Jev shadow fail-open
- Preflight: **PASS** (`jev_on=True`, code default False, `fail_open_module=yes`)
- Runtime: `VALEDIA_JEV=1` env; `vc.VALEDIA_JEV` code default False
- `jev_shadow._fail` returns `{enabled: True, fail_open: True, error…}`; dry tick logged `jev fail-open: URLError`
- Annotate on scored bundles: `oms=False`; never alone-passes create

## 7. PASS/FAIL checklist
| Check | Result |
|---|---|
| WEATHER_LIVE=0 before/after | **PASS** |
| Build preflight PASS + flip_allowed | **PASS** (True) |
| Live preflight contrast FAIL | **PASS** (expected; 4 blockers) |
| Kalshi read auth / balance | **PASS** |
| Weather markets GET | **PASS** (96 sampled) |
| Shadow dry tick no create | **PASS** |
| Morning scan path (live books) | **PASS** (runs; n_alert=0 / no_fv) |
| FV source hit (fixture TWC) | **PASS** (`weather_com`) |
| Jev fail-open | **PASS** |
| Zero orders / oil untouched | **PASS** |
| Report written | **PASS** |

## Blockers / notes for parent
- **None for read-only smoke.** Go-live still requires a fresh build preflight PASS at flip time (already True tonight on 0.74.0) — **do not flip here**.
- Live FV over network for morning alerts needs TWC/NWS credentials/path that return settle FV; tonight live scan was `no_fv` so no production `WEATHER-MORNING-ALERT.md` edge lines.
- Live tree 0.44.0 still blocked on LAS/TLV tz, fee-map KXHIGH/KXLOW, lottery-late create-path, ledger/alert hooks — use **workspace-v2 0.74.0** for go-live.

## Artifacts
- Smoke alert: `recommendations/WEATHER-MORNING-ALERT-SMOKE-2026-09-23.md`
- Smoke ledger: `models/weather_shadow_ledger_smoke_2026-09-23.json`
- This report: `recommendations/WEATHER-SMOKE-2026-09-23.md`

**WEATHER_LIVE left=0**

## Morning go checklist (Valedia runtime — PT)

Owner: **Valedia** Mac crontab / `weather_morning_go.sh` (Grok Bot "Kalshi weather morning scan" **PAUSED**). Do not depend on Grok Bot wake.

- Preflight on promoted tree (`~/.openclaw/workspace/`) must print **PASS** + `flip_allowed=True`
- Only then set `WEATHER_LIVE=1` on that tree (weather-only; never OIL)
- Confirm live FV from TWC (daily high/low) / NWS when rules say so — refuse create on `no_fv`
- Run morning scan / alert; INFORMED fee-true; refuse lottery + late-collapse
- Jev shadow-only; size = existing Kelly caps
- If preflight FAIL → leave `WEATHER_LIVE=0`, name blockers, shadow-only

