# KALSHI-INFORMED-LIVE — 2026-09-23

**Owner:** Valedia Trader (Burton go). **Build:** Grok Build via `~/bin/grok-valedia` / screen `valedia-grok`.
**North star:** researched/model FV → fee-true EV → size. MiroFish narrative-only must **NOT** replace official sources. Missing FV = **refuse create** (not neutral-50).

## Goal
1. **Cancel** the six resting Kalshi Master Allocator **CONVICTION** lottery orders (and any still-resting matching CONVICTION lottery for those markets).
2. Ship **INFORMED-before-create** fail-closed on the **live Kalshi** create path used by Master Allocator / `kalshi_executor` CONVICTION (gate exists in workspace-v2 as `informed_before_create.py` but is **not** on the live cron tree).
3. Wire **wanted Jev hooks** so `VALEDIA_JEV=1` (already env-on from max-env) actually does something useful on the live Kalshi path — typed/shadow/CIO honesty **beside** INFORMED — without making Jev OMS.

## FTL cite (null FV lottery proof)
Ledger: `~/.openclaw/workspace/models/forward_test_ledger.jsonl` (live cron tree). Strategy `CONVICTION_*`, `predicted_prob=null` (FTL null FV). Confirm via Kalshi API before cancel — FTL `fill_status=FILLED` may mean accepted entry; cancel **resting open orders** only, do **not** flatten unrelated filled positions.

| # | Ticker | Side | FTL line / id | Notes |
|---|--------|------|---------------|-------|
| 1 | `KXTRUMPPARDONS-29JAN21-BCJ` | yes | L136 `ft_39307b502996` oid `01a0cfb6-92e0-71d7-ab05-c067d882f8a5` | Brian Cole Yes; ts 2026-09-23T19:20:44Z |
| 2 | `KXGDP-27APR29-T4.0` | yes | L131 `ft_248c0515e9fc` oid `01a0cedb-4640-7a75-aef5-ef2551c39214` | GDP Above 4.0% Yes |
| 3 | `KXGDP-27APR29-T0.0` | no | L82 `ft_9532de1daeb3` oid `01a0bb54-7ec0-75ac-aea1-c17d20cf910d` | GDP Above 0.0% Sell Yes / No |
| 4 | `KXINSURRECTION-29-27` | yes | L116 `ft_75e36687e42b` oid `01a0cc47-be68-7306-8551-a502a3768eb2` | Insurrection Before 2027 Yes |
| 5 | `KXGDP-27JUL29-T0.5` | no | L115 `ft_4392c8f6d15b` oid `01a0cc47-b2b0-7c3d-87a9-aa7b115e2b50` | GDP Above 0.5% Sell Yes / No |
| 6 | `KXGDP-27JAN28-T3.5` | yes | L98 `ft_cdefd85ac66e` oid `01a0c56a-4840-7ec3-9727-9b58f42d3ddd` | GDP Q4'26 Above 3.5% Yes (best match) |

Also cancel any **still-resting** CONVICTION lottery orders on those same markets (same ticker prefix / market) found via `kalshi_auth.get_orders()` — log every canceled order id. Do **not** cancel unrelated tickers/strategies/positions.

## Live path clarification (must document in Results)
- **Cron / live tree:** `~/.openclaw/workspace` (live `VALEDIA_VERSION` ~**0.44.0**).
- **Build tree:** `~/.openclaw/workspace-v2` (working **0.69.0**).
- `informed_before_create.py` + tests live in **workspace-v2 only** today; live `workspace/.../kalshi_executor.py` has **no** INFORMED import.
- Prefer **promote/sync** needed modules into the tree cron actually runs (`workspace`), same pattern as ARB-OPENSSL-HARDEN file promote — clarify which files landed where; bump version note (build tree + live promote note).

## Build steps

### A) Cancel the six (+ matching resting CONVICTION on those markets)
1. `get_orders()` (or existing Valedia cancel helpers / `execution_policy` cancel path / `kalshi_auth.cancel_order`).
2. Match tickers above (and still-resting CONVICTION lottery on those markets). Confirm before cancel.
3. Cancel only those; log order ids canceled + API responses.
4. Proof: post-cancel open-order list shows none of the six resting; write short cancel log under `recommendations/` (e.g. `KALSHI-LOTTERY-CANCEL-2026-09-23.md`).

### B) INFORMED-before-create on live Kalshi create path (fail-closed)
1. Port/wire `scripts/integrations/informed_before_create.py` onto the **live** create path used by Master Allocator / `kalshi_executor` CONVICTION (`create_order` call sites).
2. Fail-closed: **no** researched/model FV (`fv_source` in `{model, rules_clock, researched}`) + fee-true edge ≥ threshold → **no create**.
3. Missing FV / `predicted_prob=null` / MiroFish default-50 / tag_prior-alone → refuse (not neutral-50).
4. MiroFish narrative-only must not replace official/model FV.
5. Sync into cron tree (`workspace`) so the next CONVICTION cron cycle is gated.

### C) Stop CONVICTION ROI-if-right cheap Yes/No without INFORMED
CONVICTION must not create on cheap Yes/No “ROI if right” alone. INFORMED pass is mandatory before any live CONVICTION `create_order`.

### D) Promote + version + tests
1. Promote/sync needed code from workspace-v2 → live workspace (document file list).
2. Bump version note (build tree const; live promote note — do not pretend live is 0.69.0 unless you ship it).
3. Tests: refuse-without-FV; allow-with-FV; existing `test_informed_before_create.py` still PASS; add/extend coverage for CONVICTION create-path hook.
4. Short Ponytail / Results note.

### E) Jev beside INFORMED (Burton add-on — highest/smartest)
`VALEDIA_JEV=1` is already env-on (max-env). Today Jev is only on MiroFish report path (`jev_shadow` in `mirofish_signal_extractor`); **not** on Kalshi create. Wire the **wanted** hooks so the env bit is real on live Kalshi path — still **fail-open / flipable**.

**INFORMED stays the hard fail-closed create gate.** Jev sits **beside** it for typed/shadow/CIO honesty — never replaces INFORMED, never replaces Laya research as critical path, never becomes OMS.

Wanted Jev uses (NOT live OMS):
1. **Typed gates / TypeSafe checks** when `VALEDIA_JEV=1` (reuse `jev_shadow.py` / SystemOne).
2. **MiroFish swarm-worth-it triage + CIO pack** shadow `stand_down` / size multipliers (MIROFISH-JEV-SHADOW intent) — annotate/season; do not hard-block create on Jev alone.
3. **Opportunity classification / confidence thresholds / status-honesty** checks (stamp controls / create metadata).
4. **Annotate/shadow seasoning on creates** — attest quality; **fail-open**: timeout/error/missing key → log + continue; Valedia fully works with Jev OFF; **no TypeSafe dependency on critical path**.
5. **Do NOT** make Jev the OMS; **do NOT** put Jev on arb / GOLD-15m speed path; **do NOT** replace Laya research with Jev as critical path.

Concrete: hook optional Jev annotate/shadow on CONVICTION (or shared create preflight) **after** INFORMED would allow — or parallel annotate that never overrides INFORMED refuse. Prefer promote `jev_shadow.py` with the INFORMED promote if cron tree lacks it. Keep git default `VALEDIA_JEV=False`. Extend tests: Jev OFF = zero TypeSafe; Jev ON fail-open; INFORMED refuse still blocks even if Jev would annotate.

### F) Fence / preserve
- Preserve screens `ot-valedia-desk`, `valedia-glance` (and this `valedia-grok`).
- No oil/weather unfence. No wallet. No treasury ACH.
- Poly writer changes only if required for INFORMED consistency — **do not** mass Poly create; do not flip Poly LIVE broadly.
- Do not spend/deposit. Do not cancel unrelated orders/positions.
- Do not put Jev on latency arb / GOLD-15m.

## Done when
1. Six (+ matching) resting CONVICTION lottery orders canceled; order ids logged; unrelated left alone.
2. Live Kalshi CONVICTION create path fail-closed on INFORMED (no FV / no fee-true edge → no create); code in the tree cron runs.
3. CONVICTION cheap Yes/No ROI-if-right cannot create without INFORMED pass.
4. `VALEDIA_JEV=1` wires real annotate/shadow/CIO honesty beside INFORMED on that path; Jev OFF = full Valedia; fail-open preserved; Jev ≠ OMS.
5. Tests: refuse-without-FV, allow-with-FV, Jev off/on fail-open.
6. Version note + Results; `GROK_EXIT:0` (or non-zero with blocker).
7. Next = **Burton stop** unless proof incomplete.

## Cannot-do / Hard fence
- No mass Poly create / no broad POLYMARKET writer flip.
- No wallet / treasury ACH / deposits / spends.
- No oil / weather unfence.
- No cancel of unrelated Kalshi orders or position flatten outside the six markets' resting CONVICTION lottery.
- No Jev-as-OMS; no Jev on arb/GOLD-15m; no Jev replacing Laya research critical path.
- Do not commit `VALEDIA_JEV=True` or weaken INFORMED to soft-gate.
- Do not kill `ot-valedia-desk` / `valedia-glance`.

## Proof (required)
1. Cancel log: order ids + tickers + API ok; post-`get_orders` clear for those six.
2. Grep/import proof: live `workspace/.../kalshi_executor.py` (or create path) calls `informed_before_create`; refuse path exercised in test.
3. Unit tests PASS for refuse-without-FV / allow-with-FV / Jev fail-open.
4. Controls honesty: `VALEDIA_JEV` row still accurate; INFORMED gate visible if controls surface exists.
5. Results section + `GROK_EXIT:0`.

## Tools
Shell on Mac Studio. Kalshi via existing `kalshi_auth` / Valedia helpers. RuFlo filtered MCP optional. CBM optional. Screen `valedia-grok` only for this Build.

## Deliverables
1. Cancel proof note under `recommendations/`
2. Code + promote list (v2 → live workspace)
3. Tests + version note
4. Results below + update `GROK-STATUS.md` / `GROK-NEXT-QUEUE.md` → Next=Burton stop
5. Optional short `PONYTAIL-AUDIT-KALSHI-INFORMED-LIVE.md`

## GROK_EXIT format
End the live log with exactly:
```
GROK_EXIT:0
```
or `GROK_EXIT:1` with a one-line blocker. Parent relays via exit-ping — do not wait for parent.

## Results

**2026-09-23 16:05 PT — DONE.** `GROK_EXIT:0`. Next = Burton stop.

1. Six resting CONVICTION lottery orders canceled. They were the only resting orders on the account. Log: `recommendations/KALSHI-LOTTERY-CANCEL-2026-09-23.md`. Prior fills were not flattened.
2. Live cron tree `~/.openclaw/workspace` now fail-closes Kalshi creates in `kalshi_auth.create_order` / `sell_position` via `informed_before_create.require_kalshi`. `kalshi_executor.execute_trade` and `master_allocator` call `create_preflight` before CONVICTION taker and maker creates. ROI-if-right with null `predicted_prob` is `SKIP_INFORMED` and does not POST.
3. `VALEDIA_JEV=1` annotates through `jev_shadow.annotate` only after INFORMED allows, and only on `CONVICTION_*`. Timeout, missing key, and `stand_down` stay fail-open. Arb and `KXGOLD15M` / `KXBTC15M` / `KXETH15M` do not call it. Const `VALEDIA_JEV` stays False on both trees.
4. Build tree `VALEDIA_VERSION` **0.70.0** (not committed, not tagged). Live cron version stays **0.44.0**.
5. Tests PASS: `test_informed_before_create.py` (refuse-without-FV, allow-with-FV, Jev off, Jev timeout fail-open, `stand_down` still allows, executor posts only with model FV), `test_jev_shadow.py`, `test_kalshi_orders_v2.py` (build and live), `test_kalshi_profit.py`.
6. Screens left running: `ot-valedia-desk` 783, `valedia-glance` 17171, `valedia-grok` 47103. Oil/weather fence unchanged. No wallet, no treasury ACH, no mass Poly create.

### Promote (workspace-v2 → workspace cron tree)

Copied: `informed_before_create.py`, `session_clocks.py`, `kalshi_profit.py`, `jev_shadow.py`, `weather_shadow.py` (`session_clocks` import; research module, not a weather unfence).

Patched in place on the live tree: `kalshi_auth.py`, `kalshi_executor.py`, `master_allocator.py`, `execution_policy.py`, `valedia_config.py` (consts + `jev_enabled()` only), `test_kalshi_orders_v2.py`.

```
GROK_EXIT:0
```
