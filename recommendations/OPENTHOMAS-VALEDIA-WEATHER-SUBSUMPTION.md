# OPENTHOMAS → Valedia weather subsumption (paper/CE)

**Date:** 2026-09-28 23:44 PT  
**Clone:** `/Users/burtonchatelain/MemPalace/sources/repos/github/openthomas`  
**HEAD:** `fba4a339c6f116222fdb7aa326f35ed11e9cdec8` (main)  
**HARD FENCE:** reference/paper only — do **not** run OpenThomas as a parallel live bot; **Valedia sole live risk**; no `--live` from Build/Paperclip/Hermes.

---

## Logical subsumption (absorb mechanisms, not the bot)

| OpenThomas mechanism | Where it lives in OT | Absorb into Valedia as | Not absorb |
|---|---|---|---|
| NWS station ↔ Kalshi series settlement map | `openthomas/weather/stations.py` (STATIONS, KALSHI_SERIES, CLI location) | Shared settlement registry for INFORMED weather FV / VAL-1 CLI path | OT live `run` loop |
| Seven-model NWP consensus → strike probs | `weather/baseline.py`, `openmeteo.py`, GraphCast/GenCast | Shadow FV feature behind fee-true EV (paper CE) | Replacing INFORMED FV with unclamped LLM |
| Station bias / σ from leak-free hindcast | `weather/hindcast.py`, MOS | Per-(station, lead) bias table feeding FV; CE vs fee-true books | Auto-sizing from hindcast alone |
| LLM adjust clamped ±0.15 | forecast engine / paper E4 | Optional Ornith adjust **hard-clamped**; never replaces baseline | Unclamped LLM / frontier API default |
| Fractional Kelly + caps | `risk/engine.py` | Size under **fee-true EV** + Valedia max-1 / thin bankroll | Raw Kelly ignoring fees / lottery band |
| Longshot band min_price/max_price | risk engine | Supporting refuse signal only | Replacing LOTTERY_PX=0.05 / LATE_PX=0.90 |
| Replay vs real Kalshi books | `weather/replay.py` | Paper CE harness for VAL-10 two-sided ranking | Live admit path |
| Cross-platform Poly×Kalshi arb | EDGE scanner | Skip for now (Valedia bans fee-blind keyword arb; resolution mismatch risk) | Parallel arb bot |

**Hermes Kubernetes:** this does **not** appear in Hermes Agent K8s. Desk Hermes is local API gateways (:8642–8645). Subsumption = Valedia modules + Paperclip tips, not deploying `openthomas` pods.

---

## Conflicts with INFORMED weather fee-true (must preserve)

1. **LOTTERY_PX=0.05 / LATE_PX=0.90 unchanged** (`informed_before_create.py`, `weather_shadow.py`). OT longshot filter may help ranking; it must not redefine the band.
2. **Fee-true EV ≥ floor after fees** wins over OT raw edge; Kelly only after fee-true + INFORMED-BEFORE-CREATE.
3. **VAL-10 two-sided prefer / one-sided 1¢ refuse** — OT consensus improves FV quality; ranking/filter still Valedia's job in `workspace-accel-ornith`.
4. **No parallel live bot** — no `openthomas run --live`, no second Kalshi key path, no Poly live from OT.
5. **LLM is adjust-only** — Valedia Ornith-solo clamp; no dual Qwen load for weather brain.

---

## Concrete next slice (queue when ready)

**Doc:** this file (dual-home).  
**Executor:** Hermes CTO / Ornith ACCEL worktree **or** Grok Build if Burton opts in (`VALEDIA_CTO_BUILDER` / explicit yes). Prefer ACCEL for VAL-10 charter.

**Slice A (paper, 1 sitting):** Port OT STATIONS + KALSHI_SERIES (+ CLI location codes) into a Valedia-owned registry under `workspace-accel-ornith` (or recommendations JSON consumed by weather FV). Unit tests: NYC→KNYC/Central Park, CHI→KMDW not KORD. No orders.

**Slice B (paper CE):** Hindcast bias table for Valedia weather stations → shadow FV adjustment; compare fee-true EV on **two-sided** books vs baseline; prove lottery one-sided still refused at 0.05/0.90.

**Slice C (later):** Fee-aware fractional Kelly sizing helper wired to INFORMED size caps — still paper until unlock.

**Out of slice:** deploying OT to Hermes K8s; `openthomas run`; Poly live; loosening lottery/late; oil; eToro.

---

## Success signal

- Registry + tests green; VAL-10 tip can cite station-correct FV.
- Shadow CE shows consensus/bias improves fee-true ranking on two-sided books without admitting one-sided 1¢.
- Zero OT live process; Valedia remains sole live risk.


## Promote rule (Burton 2026-09-28 23:44 PT)

1. **Paper/CE only** until **one successful iteration** of the absorbed mechanism (registry + tests + shadow CE proof on fee-true / VAL-10 gates).
2. **Then live** — only via **Valedia runtime** when Valedia herself decides the slice is ready (INFORMED-BEFORE-CREATE + fee-true + lottery/late band + bankroll fences still win).
3. Grok Build / Paperclip / Hermes / OpenThomas clone **never** flip live or place orders. They may recommend unlock; Valedia is sole live risk and sole go-live authority for what she chooses to admit.

---

## Slice A DONE (2026-09-28 ~23:48 PT)

**Actor:** Grok executor (Hermes CTO :8645 auth-blocked for direct chat → direct edits in `workspace-accel-ornith` / branch `accel-ornith`; **not** Grok Build; flux-grok / valedia-grok screens left alone).

**Paths (Valedia-owned registry):**
- Module: `~/.openclaw/workspace-accel-ornith/scripts/integrations/weather_stations.py`
- JSON: `~/.openclaw/workspace-accel-ornith/recommendations/weather_stations.json`
- Tests: `~/.openclaw/workspace-accel-ornith/scripts/integrations/test_weather_stations.py`

**Source:** OpenThomas HEAD `fba4a339c6f116222fdb7aa326f35ed11e9cdec8` `openthomas/weather/stations.py` — Valedia-owned thereafter (ownership comment in module + JSON).

**Proof:** `python3 test_weather_stations.py` → **OK 6 tests** including NYC→KNYC/Central Park, CHI→KMDW not KORD (no KORD in registry), CLI location codes, KALSHI_SERIES coverage, JSON↔module parity.

**Fence:** paper/CE only; no `--live`, no `openthomas run`, no orders, LOTTERY_PX/LATE_PX untouched. Promote: paper until one successful iteration; Valedia runtime alone decides go-live later.

---

## Consensus / shadow FV slice DONE (2026-09-28 ~23:55 PT)

**Actor:** Grok executor (Hermes CTO direct edits in `workspace-accel-ornith` / branch `accel-ornith`; **not** Grok Build; flux-grok / valedia-grok screens left alone).

**Paths (Valedia-owned NWP consensus → shadow FV):**
- Module: `~/.openclaw/workspace-accel-ornith/scripts/integrations/weather_nwp_consensus.py` (Open-Meteo 7 models → mu/sigma; wired to Slice A `Station` lat/lon/timezone)
- Module: `~/.openclaw/workspace-accel-ornith/scripts/integrations/weather_strike_fv.py` (Strike + mix_cdf fat-tail + strike_probability + shadow_strike_fv)
- Tests: `~/.openclaw/workspace-accel-ornith/scripts/integrations/test_weather_nwp_strike_fv.py`

**Source:** OpenThomas HEAD `fba4a339c6f116222fdb7aa326f35ed11e9cdec8` — `openmeteo.py` / `baseline.py` / `strikes.py` / desk mu-sigma floor / `prior_sigma` — Valedia-owned thereafter (ownership comments in modules).

**Proof:** `python3 test_weather_nwp_strike_fv.py` → **OK 10 tests** (fixture NYC consensus mu/sigma shape; greater/less/between; observed high truncation; probs in [0,1]; mixture fat-tail; mock httpx client; optional live smoke skipped unless `WEATHER_NWP_LIVE_SMOKE=1`). Slice A `test_weather_stations.py` still **OK 6**.

**Fence:** paper/CE shadow only; no `--live`, no venue orders, no `openthomas run`, no WEATHER_LIVE flip, no LMS unload, exclusive screens not killed, LOTTERY_PX=0.05 / LATE_PX=0.90 untouched. Promote: paper until one successful iteration; Valedia runtime alone decides go-live later.

