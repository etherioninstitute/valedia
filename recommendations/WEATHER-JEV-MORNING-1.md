# WEATHER-JEV-MORNING-1 — Jev morning parse + WEATHER go-live preflight

**Queued:** 2026-09-23 ~19:06 PT (after WEATHER-SCRAPE-APIS-1).  
**Burton intent:** Tomorrow unfence `WEATHER_LIVE` for **INFORMED fee-true WEATHER ONLY** if preflight PASS. Refuse lotteries + late-collapse. **Oil stays fenced.** Do **not** flip `WEATHER_LIVE` until `weather_live_preflight.py` returns PASS.

**Depends on:** WEATHER-SCRAPE-APIS-1 exit (STEER folded: TWC daily settle + station map + Jev thin wire if absorbed). Cite STEER: `WEATHER-SCRAPE-APIS-1-STEER.md`.

## Goals

1. **Morning scan path (shadow first, then gated live)**  
   On each new Kalshi daily weather market:
   - Pull settle FV (**TWC** for daily city high/low per rules / CLIxxx) + NWS research + Accu/TWC forecast tags + book mids
   - Run **Jev** typed shadow over that bundle FAST → annotate agree/disagree/confidence/status-honesty
   - Append shadow ledger; **never alone-pass create** on Jev
   - If fee-true edge ≥ threshold under INFORMED shape → write `recommendations/WEATHER-MORNING-ALERT.md` (or jsonl) for parent glance
   - Ornith: optional only if already wired; do not block

2. **Create-path refuses (not shadow-only)**  
   - Lottery: refuse tail ≤~0.05 (or conviction-ROI lottery shape)
   - Late-collapse: refuse favorite ≥~0.90 late in observe-day without early-day soft book
   - Wire into `informed_before_create` / weather create path

3. **Go-live preflight (hard gate before flip)**  
   Implement/extend `scripts/integrations/weather_live_preflight.py` (stub already landed 2026-09-23).  
   Flip `WEATHER_LIVE=1` / enable `weather_live_enabled()` **only if ALL PASS**:
   1. Settle FV from contract’s own source (TWC for daily city high/low); INFORMED fail-closes without it
   2. Stations + TZ mapped for each series traded (MIA, LAS/TLV, existing)
   3. Fee-map has KXHIGH*/KXLOW* city series (fee-true EV real)
   4. Lottery + late-collapse enforced on **create** path
   5. Jev stays shadow-only fail-open; size caps = existing Kelly (conservative/half-Kelly)
   6. Live path writes ledger + alerts on each create  
   Else stay `WEATHER_LIVE=0` and name blockers. **Do not touch OIL_LIVE / oil fence.**

4. **Live enable (only after PASS)**  
   - Carefully lift weather-only: `weather_live_enabled()` may honor env when preflight PASS + Burton go; keep `OIL_BRACKET` + `KXWTI`/`KXOIL` fenced
   - Restore a **narrow** weather create path behind INFORMED + fee-true + lottery/late refuses + size caps
   - Each create → ledger row + alert artifact

5. **Docs**  
   - Results on this charter; Ponytail audit; both-tree STATUS/queue; Next = Burton stop

## Explicit non-goals

- Flipping WEATHER_LIVE without preflight PASS
- Oil / OIL_BRACKET / KXWTI / KXOIL unfence
- Jev-as-OMS / Jev alone-pass create
- Mass Poly create; wallet/treasury ACH; eToro write
- Blocking on Ornith swarm

## Hard fence (until preflight PASS)

- Tonight / until PASS: `WEATHER_LIVE` stays False/0; `weather_live_enabled()` dead-path OK
- Oil remains in `LIVE_EXEC_FENCED` / series fence
- INFORMED fail-closed; Jev fail-open non-OMS
- Preserve `ot-valedia-desk` + `valedia-glance`; only recycle `valedia-grok`

## Deliverables

1. Morning scan + Jev annotate wire (weather bundle → `jev_shadow`)
2. Create-path lottery/late refuses
3. `weather_live_preflight.py` green (or FAIL with named blockers — do not flip)
4. Alert + live ledger hooks
5. Fee-map refresh for KXHIGH*/KXLOW* city series; station/TZ for MIA + LAS/TLV
6. Only if PASS: env/code path for WEATHER_LIVE weather-only
7. STATUS/queue/Ponytail; Next = Burton stop

## Done when

- Preflight run documented (PASS → live path armed; FAIL → blockers listed, live stays 0)
- Morning alert path works in shadow even if live stays 0
- Oil still fenced; Jev never OMS
