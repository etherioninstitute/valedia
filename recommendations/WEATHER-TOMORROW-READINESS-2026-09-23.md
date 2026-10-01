# WEATHER tomorrow readiness — 2026-09-23 ~19:07 PT

**19:34 PT update:** `weather_live_preflight.py` **PASS**. Env `WEATHER_LIVE` was not set. `weather_live_enabled()` is false. The gaps below were closed in `WEATHER-JEV-MORNING-1` (build 0.74.0). Oil stays fenced. Set `WEATHER_LIVE=1` on the build tree only if a fresh preflight still prints PASS.

**For parent relay.** Do not flip `WEATHER_LIVE` tonight. Oil stays fenced.

## Default for tomorrow morning
- **Intent:** Unfence **WEATHER_LIVE** for **INFORMED fee-true weather only** after preflight PASS.
- **Still fenced tonight:** `WEATHER_LIVE=0` (env), `weather_live_enabled()` hard-off, `LIVE_EXEC_FENCED` still includes `WEATHER_ARB` + `OIL_BRACKET`, series fence `KXWTI`/`KXOIL`.
- **If any preflight check fails at go-live:** leave `WEATHER_LIVE=0` and name the blocker — no silent flip.

## Jev role
- Env `VALEDIA_JEV=1` (git/code default still **False**; runtime on via `~/.openclaw/.env`).
- `jev_shadow.py`: fail-open typed annotations; **not OMS**; never alone-pass create.
- Not yet wired into `weather_shadow` / morning bundle (STEER asks Build for thin annotate wire; full morning path = `WEATHER-JEV-MORNING-1`).

## Morning scan intent
- New Kalshi daily weather markets → settle FV (**TWC** for daily city high/low) + NWS/Accu/TWC research tags + book mids → **Jev shadow FAST** → ledger annotate → alert if fee-true edge under INFORMED shape.
- Refuse lottery (tail ≤~0.05) + late-collapse (favorite ≥~0.90 late observe-day) on **create path**.
- Ornith optional only if already wired; do not block.
- Alert artifact: `recommendations/WEATHER-MORNING-ALERT.md` (or jsonl) for parent glance.

## Preflight helper
- `scripts/integrations/weather_live_preflight.py` (run: `python3 …/weather_live_preflight.py`)
- Tonight run: **FAIL** — `flip_allowed=False` (expected).

### Tonight PASS
| Check | Notes |
|---|---|
| INFORMED fail-closed without FV | research tags alone → `informed_pass=False`, `create_order=False` |
| Jev shadow fail-open | `jev_on=True`, code default False, not OMS |
| Fence tonight | weather live off; oil fenced |
| Risk caps | `KELLY_SAFETY_FRACTION=0.5`, `KELLY_BASE_CAP=0.08` |

### Tonight GAPS (blockers — must clear before WEATHER_LIVE=1)
| Check | Blocker | Gap |
|---|---|---|
| Settle FV TWC daily | `settle_fv_not_twc_for_daily_city` | `weather_lookup` still routes daily KXHIGH/KXLOW → **nws_cli** even when rules say Weather Company |
| Stations / TZ | `stations_or_tz_unmapped` | `CITY_TZ` missing **LAS/TLV**; NWS station map missing **MIA, LAS, TLV** (only NYC wired) |
| Fee-map KXHIGH/KXLOW | `fee_map_missing_kxhigh_kxlow` | artifact has **KXHURRICANE + KXTEMP only** — no KXHIGH*/KXLOW* city series |
| Lottery / late on create | `lottery_late_not_in_create_path` | no `SKIP_LOTTERY` / `SKIP_LATE_COLLAPSE` in informed/weather create path (`create_order` still removed from weather_arb) |
| Live ledger + alert | `live_ledger_alert_hooks_missing` | no create ledger/alert hooks yet |

## Build / queue
- **Active:** WEATHER-SCRAPE-APIS-1 on `valedia-grok` (do not kill). STEER folded (TWC settle + Jev/preflight).
- **Next after exit:** WEATHER-JEV-MORNING-1 → then Burton stop.
- Charter: `recommendations/WEATHER-JEV-MORNING-1.md`
- STEER: `recommendations/WEATHER-SCRAPE-APIS-1-STEER.md`

## Hard rules
- Do **not** set `WEATHER_LIVE=1` tonight.
- Do **not** touch `OIL_LIVE` / oil unfence.
- Do **not** launch another grok while scrape Active.
