# VAL-11 — Silent-Failure Checklist → Probe / Feed Map

**Date:** 2026-09-28 ~23:50 PT  
**Paperclip:** VAL-11 `a404190c-6435-4134-9c1f-130c56113c22` (company `4fb233c2-27e9-4d0d-a217-2dc7205210a0`)  
**Charter:** `VALEDIA-SELF-HEAL-1.md`  
**Brief:** `VALEDIA-FLUX-IMPLEMENTATION-BRIEF.md` §D P0-2  
**Checklist source:** `~/Library/CloudStorage/ProtonDrive-burtworthy@protonmail.com-folder/AI/Flux-Growth-Collection/silent-failure-checklist.md` (Flux FREE #27)  
**Status:** mapping landed; Signals **#10, #11, #14, #2/#7, #5, #16** fail-closed wired in `workspace-v2/scripts/valedia_silent_failure_probes.py`

---

## Fail-closed rule (house)

**Empty artifact ≠ PASS.**  
`?` / `null` / silent-when-clean / exit 0 with empty or placeholder output = candidate bug until proven otherwise.  
A producer must write every run (or emit an explicit SKIP with reason); a probe that finds missing/empty must **FAIL** (non-zero), not WARN-and-green.

Verification: trace number → lowest writer; production-shaped smoke; do not call a class closed until 2–3 later clean cycles.

---

## Surface inventory (VAL-11 + feed writers)

| Surface | Probe / writer (lowest) | Notes |
|---------|-------------------------|-------|
| Neo4j / MiroFish | `:7687` LISTEN + bolt; `scripts/mirofish_healthcheck.py`; compose `~/mirofish-offline` | Safe remediate: `docker compose up -d neo4j` (no `-v`) |
| Hermes gateways | `:8642`–`:8645` `/health` | Ticket if down; known restart only |
| Paperclip recovery | `GET http://127.0.0.1:3100/api/health` + agent `execution.phase` / issue recovery | Reconcile `actionId` + `executionReconciliation`; else ticket |
| LMS `:1234` | `GET /v1/models` (listed) **and** `GET /api/v0/models` (`state`) | **exists ≠ loaded**; propose-only unload |
| Tailscale Serve | `tailscale serve status` → proxy `:3100` | Documented rebind only |
| Desk screens | `screen -ls` (`valedia-grok`, `flux-grok`, `pc-*`, `ot-valedia-desk`) | Never auto-kill exclusive Build without Burton yes |
| Kalshi quote | `scripts/integrations/kalshi_quote.py` | Zero/missing → `None`; do not invent book |
| Weather fee-true | `weather_morning_go.py` + `kalshi_fee_map.py` artifact | `fee_true_ev` / `n_price_null`; WEATHER_LIVE untouched here |
| Davidd webhook jsonl | `daviddtech_paper.py` / `daviddtech_webhook_server.py` → `data/daviddtech-paper/decisions.jsonl` | `create_order=false` always; empty jsonl ≠ healthy desk |

---

## Signal map (16 → Valedia)

| # | Pattern | Valedia mapping (where it bites) | Probe / writer to grep | Fail-closed expectation |
|---|---------|----------------------------------|------------------------|-------------------------|
| 1 | Empty / swallowed catch | Hermes cron runners, webhook handlers, Neo4j reconnect | `daviddtech_webhook_server.py`, `cron_runner_mac.sh`, self-heal recipes | catch must log + count or rethrow; unknown → Paperclip |
| **2** | **Dangerous fallback** | **`kalshi_quote` / fee helpers laundering None→0; LMS model default** | **`kalshi_quote.py`, `weather_morning_go.py` price_stats** | **WIRED (hot-path)** — None stays None; never default a live price |
| 3 | Unawaited promise | Node Paperclip UI / Hermes JS helpers (less Python desk) | Paperclip local app, Hermes gateway plugins | await or explicit void+error sink |
| 4 | Promise.race vs timer | LMS chat timeouts in `mirofish_healthcheck.py` live chat; Hermes wake | healthcheck §8, Hermes cron fire | abort signal or accept orphan; don't call cancelled "ok" |
| **5** | **Silent skip, no counter** | **Weather scan skip non-NWS; Davidd filter; recovery skip** | **`weather_morning_go.py` `_diagnose` + LAST artifact** | **WIRED (hot-path)** — `skipped_non_nws` + tallies required on artifact |
| 6 | False-block log | Fence logs that don't close admit path | `weather_morning_go.py` admit gates, KILL-SWITCH | log "blocked" ⇒ admit path closed |
| **7** | **Shaped-default gap** | **Quote/fee returning partial; consumer reads undefined** | **`kalshi_quote.yes_price`, weather price_stats** | **WIRED (hot-path w/ #2)** — consumer treats None as missing (not 0) |
| 8 | Eligibility ≠ actionable | "eligible alerts" vs fee-true actionable; LMS listed vs usable | weather diagnose, healthcheck model lists | report both ceiling and actionable (partially via #5 keys) |
| 9 | `===` vs prefixed enum | Paperclip `execution.phase` / issue status prefixes; recovery enums | Paperclip recovery reconcile | match prefix family or explicit set |
| **10** | **Presence ≠ readiness** | **LMS id in `/v1/models` but `state≠loaded`; Docker container exists but bolt down; screen socket exists but hung** | **`/api/v0/models` state; Neo4j bolt ping; screen health** | **WIRED** — probe fails if required model not `loaded` |
| **11** | **Stub / empty behind green exit** | **Empty `decisions.jsonl`, empty accuracy ledger, fee-map missing/empty series, cron exit 0** | **Davidd jsonl, `mirofish_accuracy_ledger.jsonl`, `kalshi-fee-map.json`** | **WIRED** — empty artifact ⇒ FAIL |
| 12 | Missing I/O error check | Port probes without checking HTTP body; file open unchecked | connection probes, jsonl append | check rc + bytes/shape |
| 13 | Speculative write | Claiming Tailscale URL / webhook ready without verify | serve status, webhook `/health` | verify before "ready" stamp |
| **14** | **Null display / schema mask** | **`fee_true_ev` null, `n_price_null`, `?`/`—` in briefs treated as "no data yet"** | **weather morning-go price_stats; fee artifact `healthy`** | **WIRED** — null fee-true / unhealthy fee artifact ⇒ FAIL (not PASS) |
| 15 | Non-atomic write on synced disk | jsonl `.write.lock` + rewrite on iCloud/Proton paths | `daviddtech_paper.append_decision` | keep writes under `~/.openclaw/` (not synced); atomic replace |
| **16** | **Stale source, no age** | **Dated fee-map `as_of`, Davidd last row age, ledger age** | **fee `as_of`, jsonl last `ts`** | **WIRED (hot-path)** — age > WARN/FAIL thresholds surfaced |

---

## Wired this pass (proof)

Script: `~/.openclaw/workspace-v2/scripts/valedia_silent_failure_probes.py`  
Wrapper: `~/.openclaw/workspace-v2/scripts/valedia_silent_failure_probes.sh`

| Signal | Check | File:line (probe) | Exit |
|--------|-------|-------------------|------|
| #10 | Required LMS ids must have `state=="loaded"` via `/api/v0/models` (not merely listed on `/v1/models`) | `valedia_silent_failure_probes.py` `check_lms_loaded` | FAIL if Ornith (or `VAL11_REQUIRED_LMS`) not loaded |
| #11 | Davidd `decisions.jsonl` non-empty parseable rows; MiroFish accuracy ledger exists with ≥1 prediction **or** explicit SKIP reason file; fee-map artifact `mode==get` + non-empty `series` | `check_davidd_jsonl`, `check_mirofish_ledger`, `check_fee_map_artifact` | FAIL on empty/missing |
| #14 | Fee-map healthy; weather last-run must not claim PASS with `n_fee_true_ev==0` + `n_price_null` without SKIP | `check_fee_null_mask` (reads `WEATHER-MORNING-GO-LAST.json`) | FAIL if unhealthy / null-masked PASS |
| #2/#7 | Static: `kalshi_quote` zero→None; no `yes_price(...) or 0`; price_stats uses `is None`. Runtime shape on last weather artifact (SKIP if artifact missing — never invent prices / no live Kalshi) | `check_quote_none_not_zero` | FAIL on coercion; WARN if residual `fee_true_ev or 0` ranking |
| #5 | Last morning-go artifact must carry `skipped_non_nws` + price/alert/admitted tallies | `check_weather_skip_tallies` | FAIL if keys missing; SKIP if artifact absent |
| #16 | Fee-map `as_of` age + Davidd last-row `ts` vs WARN/FAIL hour thresholds (env-overridable) | `check_age_gates` | WARN soft / FAIL hard; missing as_of/ts ⇒ FAIL |

**Fence:** read-only probes; no `--live`, no venue orders, no WEATHER_LIVE flip, no LMS unload, no money four-pack (VAL-12).

---

## Hot-path (Kalshi / weather / Davidd) beyond #10/#11/#14

Writers live under `workspace-v2/scripts/integrations/` (mirrored under `workspace/scripts/integrations/` where present). Cite **workspace-v2** lines unless noted.

| Signal | Writer / consumer file:line | What bites | Probe check / ticket |
|--------|------------------------------|------------|----------------------|
| **#2** Dangerous fallback | `kalshi_quote.py:9–20` (`_to_dollars` — `x<=0` → `None`); `kalshi_quote.py:39–51` (`yes_price` may return `None`) | Inventing a book mid from missing/zero | **WIRED** `check_quote_none_not_zero` (static writer contract) |
| **#2 residual** | `weather_morning_go.py:130` `float(r.get("fee_true_ev") or 0)` in `pick_admit` ranking | None EV ranked as 0 (not a book invent, but dangerous fallback) | **TICKETED** — probe WARNs if residual present; CTO fix → drop `or 0` |
| **#7** Shaped-default gap | `weather_morning_go.py:170` `price = yes_price(m)`; `:323–337` / `:374–378` price_stats (`is None` → `n_price_null`); artifact `:410–412` `WEATHER-MORNING-GO-LAST.json` | Consumer treating missing mid as 0 | **WIRED** — static `is None` + artifact `n_price`/`n_price_null` shape; **SKIP** if LAST missing (no live Kalshi) |
| **#5** Silent skip | `weather_morning_go.py:116–131` `pick_admit` / `skipped_non_nws`; `:202–272` `_diagnose` prints tallies; summary `:403` writes `skipped_non_nws` | Skip without counter → false green | **WIRED** `check_weather_skip_tallies` on LAST artifact |
| **#8** Eligibility ≠ actionable | `_diagnose` `:214` / summary `:396–402` (`n_alert` vs `admitted`) | Eligible alerts without actionable admit | Partially covered by #5 required keys; full #8 admit-gate audit remains ticketed |
| **#16** Stale source | Fee artifact `kalshi-fee-map.json` `as_of` (writer `kalshi_fee_map.py`); Davidd `daviddtech_paper.py:238` `ts` / `:239` `alert_timestamp`; append `:267–289`; webhook `daviddtech_webhook_server.py:85` `desk.append_decision` | Stale fee/book age silent | **WIRED** `check_age_gates` (defaults: fee WARN 72h / FAIL 168h; Davidd WARN 36h / FAIL 168h) |
| Davidd fence writers | `daviddtech_paper.py:24` `DECISION_LOG`; `:204–255` `to_decision` (`create_order=False`); `:267` `append_decision`; `daviddtech_webhook_server.py:67–88` health + append | Empty jsonl / live flag leak | Covered by **#11** jsonl + fence breach; age by **#16** |

### Hot-path dual-home note

- Prefer `workspace-v2` for map + probes edits.
- `workspace/` mirrors of the four integrations exist (same line anchors as of 2026-09-28).
- Probe script is **workspace-v2 only** (`valedia_silent_failure_probes.py` / `.sh`); map dual-homed.

---

## Ticketed follow-ups (CTO / Ornith — concrete file:line)

1. **Signal #2 residual — pick_admit EV fallback** — Replace `weather_morning_go.py:130` `float(r.get("fee_true_ev") or 0)` with None-safe ranking (exclude None EV from `max` pool).
2. **Signal #8 full** — Ensure every morning-go path reports eligible vs actionable beyond LAST keys (admit-reason matrix in diagnose when live_on).
3. **Signal #9 — Paperclip recovery enum** — Reconcile `recovery_needed` / `legacy_execution_requires_reconciliation` with prefix-aware match (not `phase === 'recovery'` only). API: `http://127.0.0.1:3100` agents + issue runs.
4. **Signal #1/#12 — Neo4j remediate path** — Wrap compose up + bolt ping; on failure open Paperclip issue with `lsof` + compose logs (charter recipe).
5. **Signals #3/#4/#15** — Lower priority on Python desk; scan Hermes JS / Paperclip UI when touching those trees.
6. **Signal #6** — False-block log audit on admit gates / KILL-SWITCH (weather + Davidd).

---

## Dual-home paths

- Map: `~/.openclaw/workspace-v2/recommendations/VAL-11-SILENT-FAILURE-MAP.md`  
- Mirror: `~/.openclaw/workspace/recommendations/VAL-11-SILENT-FAILURE-MAP.md`  
- Charter: both trees `VALEDIA-SELF-HEAL-1.md`  
- Status stamp: dual-home `recommendations/GROK-STATUS.md` §VAL-11 HOT-PATH / SILENT-FAILURE MAP

---

## Done when (P0-2 success)

- [x] 16 signals mapped to VAL-11 surfaces  
- [x] Fail-closed rule documented  
- [x] ≥1 silent class wired (#10/#11/#14) with file:line proof  
- [x] Hot-path #2/#7 + #5 + #16 wired (read-only; SKIP when artifact missing)  
- [ ] 2–3 later clean probe cycles (ops follow-up)  
- [ ] Remaining ticketed items closed by CTO/Ornith without live risk  
