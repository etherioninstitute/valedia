# PAPERCLIP INFORMED Risk Loop — 1

**Date:** 2026-09-25 PT  
**Status:** charter / policy draft (docs only — no Paperclip goal mutation, no `--live`)  
**Owner plane:** Paperclip company control over existing Hermes Valedia trading profiles  
**Live risk plane:** Valedia runtime only  

**Related:** `PAPERCLIP-VALEDIA-ORG-2026-09-24.md`, `RUFLO-MASTER-GOAL.md`, `INFORMED-BEFORE-CREATE.md`, `WEATHER-INFORMED-ADMIT-BRIDGE-1.md`, `WEATHER-FIRST-LIVE-ADMIT-1.md`, `KILL-SWITCH.md`, `VALEDIA-DEPLOY-FENCED.md`

---

## Goal statement (Valedia-native)

**Paperclip INFORMED Control Loop:** drive Scout → Decider → Auditor → desks so that **NWS-settled weather INFORMED edges** are the only path that can clear fee-true Kalshi admit gates — under thin bankroll, oil fenced, and Board (Burton) gate before any live risk flip. Paperclip owns company goals + issue routing; Valedia runtime remains sole live risk authority and sole `WEATHER_LIVE` / `create_order` owner.

North-star anchors:

- Fee-true INFORMED edges only (refuse lottery / late / uninformed FV).
- Thin bankroll → max-1 admit discipline, Kelly/FTL caps Valedia-owned.
- Primary venues: Kalshi / Poly / weather; oil stays fenced (`OIL` / `OIL_BRACKET` / ticker-family fence).
- Paperclip controls **existing** Hermes profiles (`valedia-trading-scout|decider|auditor`, coding-only `valedia-cto`) — **no duplicate trading agents**.
- **No `--live` from Grok / Paperclip adapters**; Grok Build is CTO opt-in coding only.

Pilot goal already on company (do not recreate blindly):  
> INFORMED weather fee-true only — improve NWS CLI + refuse lottery  
Company id `4fb233c2-27e9-4d0d-a217-2dc7205210a0` · Goal id `3b85af61-87e4-4e9a-825d-1994546cb6a3` · Pilot VAL-1.

---

## Auditor policy draft (Hermes `valedia-trading-auditor` / Paperclip Auditor)

Role: **HOLD / FAIL** on admit candidates and coding tickets. Metrics are **Valedia INFORMED**, not equities ATR.

### Daily loss trip

- Refuse new admits when Valedia daily loss fuse / Kelly daily fuse is tripped (`KELLY_DAILY_FUSE` / halt path).
- `VALEDIA_TRADING_HALT` ON → Auditor FAIL on any create-path ticket (orders blocked by `require_not_halted`; intel may continue).
- Evidence: `scripts/integrations/valedia_trading_halt.py`, `kelly_sizing.py`, `KILL-SWITCH.md`.

### Session / dead-zone refuse

- Admit only when `session_clock` (or weather observe-window) is active for the desk; refuse dead-zone / fenced session reasons (`LIVE_EXEC_FENCED`, stub, not_required mis-route).
- Weather: prefer NWS CLI-final / observation settle windows; refuse wrong_date / non-final morning force-trades.
- Evidence: `session_clocks.py`, `weather_morning_go.py`, `weather_live.py`.

### Confirmation required (Valedia metrics)

Before Auditor HOLD→PASS on a Kalshi weather admit candidate, require **all**:

| Check | Valedia field / rule | Refuse reason |
|-------|----------------------|---------------|
| FV source | `fv_source` in ALLOWED path for weather admit (model / rules_clock / researched; NWS bridge may remap `nws_cli`/`nws` only on weather admit path) | `fv_source` |
| Edge | fee-true edge present (`edge` / fee_true_ev derivation) | `edge_missing` |
| Depth | `depth` ≥ `MIN_DEPTH` (default 1.0) | `depth` |
| Size | Kelly/FTL/bankroll-valid; hard cap **count ≤ 1** on first-live / thin-bankroll path | `size` / bankroll |
| Lottery / late | price not ≤ `LOTTERY_PX` (0.05) and not ≥ `LATE_PX` (0.90) | `SKIP_LOTTERY` / late |
| Oil | ticker/strategy not oil family | `oil_fenced` |
| Live flag | `WEATHER_LIVE` owned by Valedia runtime; Auditor does not flip env | `WEATHER_LIVE_OFF` |

Evidence: `informed_before_create.py`, `weather_live.admit`, `WEATHER-INFORMED-ADMIT-BRIDGE-1.md`.

Auditor outputs structured HOLD/FAIL with reason codes; never places orders.

---

## Decider policy draft (Hermes `valedia-trading-decider` / Paperclip CIO)

### Sizing

- Size via Valedia Kelly + `dynamic_per_trade_cap` / FTL — Decider proposes, Valedia computes.
- Thin bankroll default: **max 1 contract** per admit run for weather first-live path.
- Prefer maker / fee-true EV ≥ floor; taker needs higher hurdle (`INFORMED-BEFORE-CREATE.md`).

### Max-1 admit

- At most **one** dispatchable create per weather admit run (`weather_live.admit` / first-live charter).
- Shadow / `--dry-decide` / morning_scan remain `create_order=false`.

### Board gate before `--live`

- Burton (Board) must approve before: env flips, `--live`, venue/wallet keys, oil unfence, `WEATHER_LIVE` toggles, Poly US live, halt disarm for live path.
- Decider may decompose & assign tickets; **cannot** authorize live risk alone.
- Evidence: `PAPERCLIP-VALEDIA-ORG-2026-09-24.md` Board gate section.

Routing on heartbeats: Scout (research) → CTO (code if needed, Ornith+RuFlo default) → Auditor (HOLD/FAIL) → Valedia runtime for any live admit.

---

## Hard fences (non-negotiable)

1. **No `--live` from Grok or Paperclip adapters** — coding tickets only; Grok Build never silent-default / never auto-chain.
2. **Oil fenced** — `OIL` / `OIL_BRACKET` / `ticker_family_fenced`; no unfence from this loop.
3. **Thin bankroll** — max-1 admit discipline; no mass create.
4. **No duplicate trading agents** — Paperclip maps only to existing `valedia-trading-*` + `valedia-cto`; never hire CoS/life `scout|decider|auditor` aliases into Valedia trading.
5. **Venue keys not in Paperclip** — no Kalshi/Poly/wallet secrets in Paperclip `adapterConfig` / data-dir; API_SERVER_KEY only for Hermes profile HTTP auth.
6. **WEATHER live stays Valedia runtime** — `weather_live.admit` + `WEATHER_LIVE` env owned outside Paperclip.
7. **Valedia runtime = sole live risk authority** — mirrors `RUFLO-MASTER-GOAL.md` / deploy fence.

---

## Phases

| Phase | What | Done when |
|-------|------|-----------|
| **P0 — Document** | Issues under INFORMED weather goal (this charter + child tickets: Auditor rules encode, Decider max-1, Board checklist) | MD + optional Paperclip issue drafts (no prod mutate without Burton) |
| **P1 — Encode Auditor** | Wire Auditor SOUL / ticket checklist to Valedia reason codes (`fv_source`, `edge`, `depth`, `size`, oil, lottery/late, halt) | Auditor HOLD/FAIL rubric documented + Hermes profile note |
| **P2 — Board-gated paper/sim** | Paper/sim wire of Scout→Decider→Auditor loop on shadow ledgers only | Shadow jsonl proof; `create_order=false` |
| **P3 — Never auto-live** | Any real admit remains Valedia + Board-authorized charter (e.g. WEATHER-FIRST-LIVE-ADMIT) | No Paperclip/Grok `--live`; no DLL decompile |

**Explicit never:**

- Never decompile proprietary NT / third-party trading DLLs.
- Never auto `--live` from Paperclip, RuFlo, Grok Build, or CTO.
- Never copy UNLICENSED `kalshi-agent` modules into Valedia (clean-room only).

---

## Company-control shape (Valedia-native)

This charter **subsumes the company-control shape** seen in commercial multi-desk NT systems — Scout research, Decider sizing/admit, Auditor risk gates, Board override — as **Valedia-native INFORMED gates** on Kalshi/Poly weather. Implementation language is fee-true EV, `fv_source`, depth, size, session clocks, and oil fences — **not** equities ATR / NinjaTrader DLL surfaces.

---

## Optional Paperclip create plan (document only — do not mutate until Burton says)

If Paperclip is up (`http://127.0.0.1:3100`, data-dir `~/.paperclip-valedia`):

1. Confirm health: `curl -sS http://127.0.0.1:3100/api/health`
2. Prefer **child issues under existing INFORMED goal** `3b85af61-87e4-4e9a-825d-1994546cb6a3` rather than a second north-star goal.
3. Suggested issue titles (assignee Decider unless noted):
   - `INFORMED Risk Loop — encode Auditor reason-code rubric` (Auditor review)
   - `INFORMED Risk Loop — Decider max-1 + Board checklist`
   - `INFORMED Risk Loop — paper/sim Scout→Auditor dry path` (CTO coding-only if code)
4. CLI pattern (from org doc):  
   `cd ~/MemPalace/sources/repos/github/paperclip && node cli/node_modules/tsx/dist/cli.mjs cli/src/index.ts … --data-dir ~/.paperclip-valedia`  
   Or POST `/api/companies/4fb233c2-27e9-4d0d-a217-2dc7205210a0/…` when Board approves.

**As of charter write (2026-09-25 ~23:50 PT):** port 3100 was **down** — start Paperclip per org doc before creating issues.

---

## Success criteria

- Auditor and Decider policies above are the written source of truth for INFORMED weather admit tickets.
- No venue keys / `--live` / oil unfence appear in Paperclip.
- Next coding work (if any) goes CTO → Ornith+RuFlo default; Grok Build only on Burton opt-in.
