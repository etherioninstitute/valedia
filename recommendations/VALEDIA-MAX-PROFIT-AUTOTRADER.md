# Valedia → highest-profit local auto-trader

**Date:** 2026-09-15  
**Author:** Grok Build (architect pass only — no live, no NT cutover)  
**Audience:** Burton / Valedia runtime / coding agents  
**Trees:** `workspace-v2` (`valedia-v2-grok-build`, Slices 1–5 `e385c59`…`c3086df`); live cron still runs `~/.openclaw/workspace` until merge.

**Fence (this doc does not lift it):** no orders, no `--live`, no wallet/spend, no key changes, no `pip install nautilus_trader`, no `VENUE_WRITER=nautilus`, no auto-exec of rebalancer or shadow Intents. Valedia runtime = sole live risk.

---

## Verdict (read this first)

**Do not install Nautilus next. Do not cut Coinbase to NT. Do not auto-exec the rebalancer.**

Slices 1–5 built the *breakage guards* a compounding auto-trader needs: gated Intent, exclusive writer lock, default-deny live fills, POST-ack ≠ fill, paper/sandbox/unreconciled excluded from Kelly. That is necessary. It is **not yet compounding**.

The money printer today is still **Kalshi via Valedia scripts**. That path still **books POST-ack as a live fill**, sports still **sizes 4–10% off a retracted 86% WR**, and two crons can **double-bet the same game**. Those three leaks dominate any OMS elegance.

North star = **fee-true realized P&L, risk-adjusted, compounding**. Paper Sharpe, NT uptime, and kernel OOS slides are not the objective.

**Order of work:** (1) stop lying to Kelly on the live Kalshi writer, (2) size sports/crypto to the *same* unproven throttle the allocator already uses, (3) finish Coinbase fill-GET so crypto edge can *exist* on the ledger, (4) only then NT sidecar for Coinbase resting/shorts/arb. Subsume venue order in `VALEDIA-SUBSUME-NAUTILUS.md` still holds.

---

## 1) Current state (post Slice 1–5)

### What already compounds (or at least stops anti-compounding)

| Piece | Where | Why it is profit infrastructure |
|---|---|---|
| Exclusive writer lock | `valedia_config.VENUE_WRITER` — all venues `valedia` or `none`; `NAUTILUS_ENABLED=False`, `NAUTILUS_LIVE=False` | Dual-write is the catastrophic −EV. Locked. |
| Intent seam | `nautilus_intent.validate_intent` / `enqueue_intent` | Fail-closed gates; size never rounds up; shadow cannot force `dispatchable`. |
| Live-fill default deny | `nautilus_fill_sink.live_source_allowed` / `apply_fill` | `source=live` refused unless NT flags **and** writer=`nautilus` **and** `live_flag`. |
| Paper/sandbox/UNRECONCILED exclusion | `forward_test_ledger._is_non_real_source` → `real_track_record`, `open_position_count`, `should_trade_category` | Kelly cannot be inflated by paper, NT sandbox, or POST-only stubs. |
| Spot fill parser | `extract_spot_fill` / `book_spot_fill` / `spot_ack_ok` | Quote/notional is **not** a fill. Incomplete → `record_unreconciled`. |
| Brain/DCA/scalper wiring | `unified_brain.ledger_spot_buy/sell`, `dca_module.py:82`, scalper `:954` | Real Coinbase writer books fee-true or UNRECONCILED. Alpaca no longer forced `source=paper` when `IS_LIVE`. |
| Shadow POST-diff | `nautilus_shadow_coinbase` — allocator, `unified_brain`, rebalancer | Cutover mismatches are visible *before* a writer swap. Allocator sell-notional vs trader `base_size` is documented, not papered over. |
| GET-reconcile (code) | `spot_reconcile.py` + `reconcile_run.set_spot_order_getter` | Upgrade path exists. Default getter is **mock / no HTTP**. |
| Rebalancer | `rebalancer.py` — propose + shadow only | No `market_buy`/`market_sell`. Dead capital, not a ruin path. |
| Kalshi live authority | `allocator_run.py` → `master_allocator.run(dry_run=False)` + `sports_run.py` | `kalshi_executor.run` still inert. `record_entry` on allocator Kalshi EXECUTED (`:473`). Sports B1: fees via `ftl.kalshi_fee`, `record_entry(CATEGORY="SPORTS")`, DRY_RUN not persisted. |
| Maker persistence | `execution_policy.MakerOrderManager._record_fill` | Records on **get_fills**, not on place. This is the pattern Kalshi taker must copy. |
| Unproven throttle | `valedia_config._track_record_mult` → `KELLY_UNPROVEN_MULT=0.5` until 20 real settled | Correct given a thin/losing ledger. Doors: `$1000 undeployed until the ledger earns it`. |

### What leaks edge (ranked by realized-P&L damage)

**A. Kalshi POST-ack = fill (live money, every 2h/4h).**  
`sports_engine.execute_bets` (`:287–294`) and `master_allocator.execute_allocations` (`:466–474`) ignore `create_order` return, stamp `EXECUTED`, and `record_entry(..., source="live")`. Resting/unfilled → fake open → `open_position_count` throttles **all** strategies via Thorp `f/√N`. Opposite error: unfilled “wins” never settle, so sports looks better than it is. Maker path already does this right (`execution_policy.py:179–186`). Taker path does not.

**B. Sports sizes like a proven money printer. It is not.**  
`tasks/lessons.md` 2026-09-09 retraction: the 86%/100% WR was a W vs WIN bug + no-horizon resolver. Real record cited there: pnl_tracker net −$12.62; sports executed-only −$62.66. Code still:

- docstring: “PROVEN: 86% … money printer” (`sports_engine.py:1–8`)
- size 4–10% of bankroll from an `edge` score (`:234–245`), **not** `dynamic_per_trade_cap` (allocator uses that at `:296`)
- 10% elite > fund-rule 4%/trade (`skills/trading/rules/fund-rules.md`)
- imports `KELLY_SAFETY_FRACTION` and does not use it for size

This is the largest **live** overbet. It will bankrupt a compounding path faster than any missing NT adapter.

**C. Two Kalshi sports writers, two category labels.**  
- `sports_engine` every 2h, category `"SPORTS"`, `should_trade_category("SPORTS")`  
- `allocator_run` every 4h at :00 (same minute), `kalshi_executor` strategy `"SPORTS_TONIGHT"` (`kalshi_executor.py:210`), gate on `a["type"]` so the sports gate **does not cover** allocator sports  
- both skip only `get_positions` “already held” — race on the 4h boundary → double order  

**D. Coinbase/Alpaca edge is still invisible to Kelly.**  
`unified_brain` is the real Coinbase writer (allocator `place_market_order` **does not exist** — Slice 2 correctly refused to add it). Slice 3 books UNRECONCILED when POST lacks `filled_size`+`average_filled_price` (Coinbase Advanced Trade typically acks `order_id` only). Slice 5 cron walks those rows with **`mock_spot_order_getter` → None**. Until Valedia injects a GET, crypto/equity never become `source=live` FILLED, so `_track_record_mult` cannot *earn* the 8% cap on real Coinbase P&L. Allocator Coinbase/Alpaca branches are dead (`place_order` also missing on Alpaca) — they FAILED, which is safer than a silent second writer.

**E. Rebalancer does not execute.**  
`TARGET_ALLOC` BTC 35 / ETH 25 / SOL 20 / XRP 10 / ADA 5 / AVAX 5, threshold 5%. Prints + shadow. Compounding on a drifted mix is a leak; ungated auto-exec is a ruin path. Keep gated.

**F. Two brains + three fee tables.**  
- Fusion: `signal_fusion.WEIGHTS` ml 40/tech 30/sent 15/vol 10/whale 5 vs `unified_brain.stage_fusion` kernel 30–35 / MiroFish 10 / ML 18 / intel 10–15 (`:380–394`).  
- Fees: `fee_engine.STATIC_FEES` Coinbase taker 0.6%+spread 0.5%; `unified_brain.FEES` Coinbase 0.6% flat, Kalshi 0.02 flat, Alpaca 0.0; `forward_test_ledger._spot_fee` uses STATIC_FEES (taker+spread).  
- `kelly_sizing.make_trade_decision` → `compare_platforms` will prefer Polymarket/Alpaca (fee=0) for a crypto idea. Dead if unused; poison if wired.

**G. Brain Kelly is not `KellySizer`.**  
`unified_brain.stage_kelly` (`:403–407`) is `edge * 0.5 * equity` clipped to 4%. It does **not** call `kelly_sizing.KellySizer`, does **not** Bayesian-shrink to category record, does **not** Thorp-reduce. Allocator does use `dynamic_per_trade_cap`. Two sizers → two books.

**H. Parallel logs still treated as truth in places.**  
`sports_engine_log.json`, `pnl_tracker.json` (fee-exclusive fallback `:73`), `brain_log.json` (paper marks — correctly *not* in `_track_record_mult`). `pnl_tracker` WR-kill `<25%` after 3 trades can murder a fat-tailed winner the category gate was designed to spare (net, not WR).

**I. Backfill counts as “real” for Kelly sample size.**  
`real_track_record` excludes paper/sandbox/unreconciled only. `source=backfill:pnl_tracker` with `fee_status=unverified_source` still increments `n` toward `KELLY_MIN_PROVEN_TRADES=20`. Currently net-negative so it *throttles* (good). If someone “cleans” those rows into wins, it would *open* the cap on unverified fees. Exclude `backfill` the same way as paper.

**J. Dead / −EV strategy surface still in the gather loop.**  
Allocator still `find_edges()` for `WEATHER_ARB` (killed 0W/11L). Relies on `pnl_tracker.is_strategy_blacklisted`. Weather code path still has `create_order`. Latency arb cron is dry-run (`arb_run.py:9`) but `execute_arb(dry_run=False)` is one flag from live, uses CoinGecko poll not a book, fee-blind `potential_profit`. MicroScalper fires inside `unified_brain` on **daily** z from 1mo history, $2 notionals, Alpaca “0% fees” — that is noise, not a scalp; equity live-off until OOS Hit% under filter `votes≥8+MOM+5d` (handoff lock).

**K. Cron vs durable OMS (the NT-shaped hole — real, but not next).**  
Maker JSON is the only resting state that survives a process death. Coinbase GTD/post-only/OCO cannot compound on 30-min Python. Hyperliquid/shorts archived. Polymarket `ENABLED=False`. Kraken read-only. This is why NT exists **later**. It does not fix A–C.

---

## 2) Profit equation (what to maximize)

Maximize **realized compounding wealth** \(W_t\):

\[
\mathbb{E}[\Delta W] = \underbrace{f_{\text{fee-true}}}_{\text{expectancy after fees+slip}} \times \underbrace{s}_{\text{Kelly/ERE size}} \times \underbrace{\nu}_{\text{independent bets/time}} \;-\; \underbrace{\Lambda_{\text{DD}}}_{\text{drawdown / ruin tax}} \;-\; \underbrace{\Omega}_{\text{idle + wrong-mix + double-fill}}
\]

**Constraints (never relax for a hot signal):**

- ERE HALT/DANGER → size 0 (`nautilus_intent.ere_ok`, Etherion JSON).
- Fee gate: expected net ≤ 0 → reject (`fee_engine.is_trade_profitable`; brain `stage_fee_gate` must use the same table).
- Category gate: block only verified recent **net** loss, fail-open, 45d (`should_trade_category`).
- Fuses: `KELLY_FUSE=15%` / `KELLY_DAILY_FUSE=20%`; fund-rules 4%/trade and 10%/day are the *operating* intent — sports 10% currently violates them.
- `source` not in `{live}` **or** paired with paper/sandbox/unreconciled/backfill → **must not** lift `_track_record_mult` or the $1k undeployed gate.
- Exclusive writer per venue.
- Leverage = 0 until Burton.
- MiroFish ≤ 10% until accuracy ledger >55% over 2–4 weeks.

**Expectancy is fee-true or it is fiction.** A 1% edge on Coinbase taker+spread (~1.1% round-trip in `_spot_fee`) is −EV. Kalshi nonlinear `0.07 P (1-P)` at 77¢ is ~1.2¢/contract — sports 70–85¢ favorites can still be +EV **if** the true hit rate beats implied **after** fees. We do not know that number until fill-reconciled settlements exist.

**Size \(s\)** is half-Kelly × regime × `_track_record_mult` × Etherion × Thorp \(1/\sqrt{N}\), fused. Unproven → 0.5× → ~4% at neutral, not 8%, not sports’ 10%. **Do not size to kernel OOS +40.7%.** Size to ledger net.

**Frequency \(\nu\)** is *independent resolved bets*, not cron ticks. Overtrading is a fee tax. Scalper + 30-min arb poll + 2h sports + 4h allocator on the same ticker is how small books die.

**Ruin tax \(\Lambda\)** is why we keep ERE, daily fuse, category park, Mercury freeze, and NT RiskEngine as a *floor* later. One double-fill or one sports 10% loser-streak ends compounding.

**Opportunity cost \(\Omega\)** = cash idle on Coinbase while Kalshi is the only measured book, **and** a drifted BTC/ETH mix vs `TARGET_ALLOC`, **and** shorts/futures unexpressed. Rebalancer-through-gates and (later) HL reduce-only are \(\Omega\) fixes — after A–C.

### Metrics (fee-true ledger **only**)

Canonical file: `models/forward_test_ledger.jsonl` via `forward_test_ledger.py`. Nothing else is admissible (SDA HOLDS / lessons retraction).

| Metric | Function | Use |
|---|---|---|
| `n`, `wins`, `hit_rate`, `recent_20`, `net` | `real_track_record()` | Kelly multiplier; $1k undeployed gate |
| per-category `n/wins/net` (45d) | `category_stats()` | `should_trade_category`; **per-strategy size (P1)** |
| `open_position_count` | real opens only | Thorp; must not include unfilled acks |
| fill mix | `fill_status` FILLED vs UNRECONCILED vs missing | ops: GET backlog |
| fee paid | `entry_fee` + settlement `fee` | fee-gate calibration |
| Brier (if `predicted_prob`) | `summary()` | calibration, not vanity WR |
| shadow parity | `nautilus_intent_queue.jsonl` `compare.ok` | NT cutover evidence |
| MiroFish | `mirofish_accuracy_ledger.jsonl` | weight cap only |

**Do not track for sizing:** `brain_log.json` hit%, `self_audit` 100% WR, `sports_engine_log` fee-exclusive, NT sandbox rows, Coinbase POST `success=true`, kernel backtest alpha, paper Alpaca.

**Kill a strategy** on **ledger net** (category gate), not `pnl_tracker` WR<25%. Keep WR-kill as a *report*, not a live fuse, until it is net-based.

---

## 3) Ranked roadmap (P0 → P3)

Each item: **why profit**, **modules**, **fence**, **code-only vs Burton live go**.

### P0 — stop the live P&L lie (this week; no NT)

| # | Change | Why it raises realized profit | Touch | Fence | Who |
|---|---|---|---|---|---|
| P0.1 | **Fill-before-EXECUTED on Kalshi taker** | Stops fake opens (Thorp throttle) and fake edge (Kelly). Maker already records on fill. | `sports_engine.execute_bets`, `master_allocator` Kalshi branch, `kalshi_auth.create_order` return, reuse `MakerOrderManager._fills_by_order` / get_fills | No new `--live`. Read fills only. | **Code-only** (Slice 6) |
| P0.2 | **One sports category + stagger cron** | One gate sees all sports P&L; no double-bet on 4h :00. | `kalshi_executor` `"SPORTS_TONIGHT"` → `"SPORTS"`; `allocator_run` minute offset; optional per-ticker file lock | Do not add a third writer. | **Code-only** |
| P0.3 | **Sports size through `dynamic_per_trade_cap`** | Aligns the 2h live path with unproven throttle + fund 4%. Biggest live overbet. | `sports_engine` sizing block `:234–245`; cap from `valedia_config`; still fee-true `profit_if_win` | Does not place extra orders; **reduces** size. | **Code-only** |
| P0.4 | **Exclude `backfill:` from `real_track_record`** | Unverified fees cannot open the 20-trade proven gate. | `_is_non_real_source` add `backfill` | None | **Code-only** |
| P0.5 | **Spot GET getter, default still mock** | Makes Slice 3–5 *able* to book Coinbase/Alpaca live fills without inventing them. | `coinbase_trader` GET-by-id helper (read); `alpaca` GET `/v2/orders/{id}`; `reconcile_run.set_spot_order_getter` remains Valedia-owned | **Do not** enable HTTP in tests. **Do not** place orders. Wiring the callable onto live cron = **Burton/Valedia go**. | Code ships hook + function; **enable = live go** |

### P1 — one brain, one sizer, one fee table (next)

| # | Change | Why | Touch | Fence | Who |
|---|---|---|---|---|---|
| P1.1 | Per-strategy size from `category_stats` | Proven SPORTS/CRYPTO not starved by OIL/weather graves; losers not riding the aggregate. Handoff B3. | `kelly_sizing.size_position`, `master_allocator.allocate`, `valedia_config` | Code-only | Code |
| P1.2 | Unify fusion weights into `valedia_config` | Two brains = two edges. Handoff B2. Pick **brain** scheme (kernel-first) as canonical; `signal_fusion` becomes a consumer or dies. | `valedia_config`, `unified_brain.stage_fusion`, `signal_fusion.WEIGHTS`, drift detector | Behavioral: do not retune thresholds without fee-true backtest | Code |
| P1.3 | Brain Kelly → `KellySizer` + `fee_engine` | `stage_kelly` / `FEES` dict is a second, fee-blind-ish sizer. | `unified_brain.py:403–425` | Code-only | Code |
| P1.4 | `pnl_tracker` report-only; net-based kill | WR-kill fights the category gate. | `pnl_tracker.py:73`, blacklist writer | Do not auto-unblacklist weather/oil | Code |
| P1.5 | Kill/park MicroScalper live path | Daily-bar “scalp” + $2 + fee=0 is −EV noise that can still `market_buy`. Equity OOS lock. | `unified_brain` scalper block `:936–963` | Code-only (stop calling `alp_buy`) | Code |
| P1.6 | Hard-skip WEATHER_ARB in `gather_all_opportunities` | Don’t depend on a JSON blacklist file. | `master_allocator.py:132–154` | Code-only | Code |
| P1.7 | Rebalancer **gated** exec flag, default False | Fixes drifted mix (\(\Omega\)) without a second Coinbase writer. Intents still fail-closed; writer stays valedia; same gates as brain. | `rebalancer.py`, `valedia_config.REBALANCER_AUTOEXEC=False` | **Burton flips the flag.** This lane never sets it True. | Code + **Burton go** |

### P2 — execution quality / new surfaces (NT-shaped, still fenced)

| # | Change | Why | Touch | Fence | Who |
|---|---|---|---|---|---|
| P2.1 | NT **isolated venv** pin + sandbox OMS tests | Proves unknown-outcome / restart / no-double-Intent. **Labeled engineering gate, not a product.** | separate venv; `source=nautilus_sandbox` already excluded | No signing keys in that venv. No `VENUE_WRITER` change. | **Burton go** to `pip install` |
| P2.2 | Coinbase shadow parity report (7d of `compare.ok` on **brain** posts) | Cutover evidence. Allocator compare will stay dirty (missing `place_market_order`) — **ignore allocator Coinbase for parity.** | queue JSONL | No dispatch | Code |
| P2.3 | Latency arb: WS book ingest, stay dry-run | Polling CoinGecko cannot fire a 30s edge. NT DataEngine or Coinbase WS — **data only**. | `latency_arb.py` (docstring already claims WS; code polls) | Keep `arb_run` `dry-run`. Live arb = Burton. | Code data; **Burton** execute |
| P2.4 | Hyperliquid Intent surface, leverage=0 | NORTH STAR shorts/futures. Archived client stays archived. NT adapter is the writer **when** Valedia enables. | Intent schema extras (`reduce_only`, `leverage=0`); no un-archive | No HL keys from this lane | Design now; live = **Burton** |
| P2.5 | Polymarket CLOB as arb *leg* | Quant adapter exists, `ENABLED=False`. NT has CLOB. Kalshi leg stays Valedia. | `quant_engine.PolymarketAdapter` | Geo/wallet/policy = Burton | **Burton go** |

### P3 — unify writers (last)

| # | Change | Why | Fence |
|---|---|---|---|
| P3.1 | Custom Kalshi NT adapter (clean-room, no `vendors/kalshi-agent`) | One OMS. **Last** because it is the only live printer. Shadow alongside allocator, then exclusive cutover. | Burton + proven parity |
| P3.2 | Alpaca stay Valedia (or IB **explicit** swap) | No NT Alpaca adapter in clone. Fix remaining truth bugs first. | No silent IB |
| P3.3 | Kraken exec | Read-only + IP lock + NT exec WIP. | Burton trade perms **and** non-WIP adapter |
| P3.4 | Mercury | Never a venue. Never NT. | Forever |

**Do not do:** rewrite kernel as NT `Strategy` (Option D — rejected). Embed LiveNode in OpenClaw. Link MiroFish+NT+UGFT. Copy kalshi-agent. Enable `ruflo-neural-trader`. Paper-trade “until it feels good.”

---

## 4) Strategy mix (keep / kill / add)

Capital is ~hundreds of dollars (USER.md snapshot is stale; use live balances at runtime). **Small-book law:** few independent +EV bets, fat fee relative to size, ruin from overbet. That kills HFT fantasies and most arb until WS+two-leg exec exist.

### Venue preference (subsume order — still sound)

1. **Kalshi — keep Valedia writer.** Only proven live path. Do not migrate first.  
2. **Coinbase — first NT subsumption, later.** Real writer today = `unified_brain` + `coinbase_trader.market_buy/sell`. Fill-GET + shadow parity **before** `VENUE_WRITER["coinbase"]="nautilus"`.  
3. **Hyperliquid — second NT, shorts/perps.** Archived on purpose. Leverage off.  
4. **Polymarket — third, arb leg.** Scan-only now.  
5. **Alpaca — Valedia until adapter; equity live-off until OOS filter.** Commission 0 ≠ edge.  
6. **Kraken — data/read-only.**  
7. **Mercury — never.**

### Keep (fix, don’t rewrite)

| Strategy | Why it can compound | Condition |
|---|---|---|
| **Kalshi sports 70–85¢ favorites** | Fast resolve, nonlinear fee still small vs binary payout **if** true hit > implied after fee | P0.1–P0.3 first. Size to ledger, not 86% myth. MLB already killed in `SERIES`. |
| **Kalshi kernel/scanner +EV** (CONVICTION, CRYPTO_BRACKET, MACRO) | Allocator already ranks EV/day + category gate + dynamic cap | Fill-before-EXECUTED. Drop WR-kill. |
| **Coinbase kernel + filter-v3 crypto** | Trends are the NORTH STAR crypto book; DCA is the small-capital default | Fee-true fills (P0.5). Round-trip fee ~1%+ → need real edge, not 30-min churn. |
| **Rebalance to TARGET_ALLOC** | Compounding on the intended mix | Only through gated Intents, flag off until Burton. |
| **MiroFish as ≤10% intel** | Social/news surface | Accuracy ledger; never exec; never raise weight on NT backtests. |
| **Maker-vs-taker on Kalshi** | `execution_policy` is a real fee save | Keep; `EXEC_DRY_RUN=False` already (Valedia runtime). |

### Kill / freeze

| Strategy | Why |
|---|---|
| **WEATHER_ARB / OIL_BRACKET** | Permanently killed (0W/11L, 0W/4L). Hard-skip in gather. |
| **90%+ favorites** | Already documented −EV (our WR < market). |
| **MicroScalper** | Not a scalp; equity lock; fee=0 laundering. Remove live `alp_buy`. |
| **Allocator Coinbase/Alpaca execute branches** | Dead functions. Do **not** implement `place_market_order` / `place_order` — that opens a second live writer. Leave FAILED; brain is the writer. |
| **`kalshi_executor.run`** | Inert. Do not rewire. |
| **Sports 10% “elite” sizing** | Violates fund 4% and unproven throttle. |
| **pnl_tracker WR auto-kill as live fuse** | Fights net-based gate. |

### Park (build later, don’t live)

| Surface | Why parked | Unlock |
|---|---|---|
| **Latency arb** | DRY-RUN; poll not book; fee-blind | WS ingest + fill-true Kalshi + Burton `--mode execute` |
| **Polymarket arb** | `ENABLED=False`; no wallet/policy | Burton + NT CLOB + Kalshi leg Valedia |
| **HL shorts/futures** | Client archived; leverage forbidden | Burton + NT adapter + reduce-only Intents + leverage=0 |
| **Grid / DeFi uniswap** | `fee_engine` has a schedule; no live path we want | Never until custody story exists (it shouldn’t) |
| **NT sandbox “trading”** | Engineering gate only | Never lifts Kelly |

### Add (only after tests + adversarial review)

| Add | Role |
|---|---|
| **Coinbase post-only / GTD via NT** | Maker edge on crypto once sidecar is the writer |
| **HL reduce-only flatten on ERE HALT** | Survival, then shorts as a signed Burton surface |
| **Kalshi↔Polymarket LMSR arb, two-leg atomic-enough** | `quant_engine.find_arbitrage` already; needs two writers that cannot double-leg |
| **Per-strategy Kelly** | P1.1 — not a new market, a better sizer |

**Opinion:** with a few hundred dollars, **Kalshi sports (re-sized, fill-true) + slow Coinbase DCA/kernel + gated rebalance** is the compounding core. Shorts/arb are the *next* profit surface, not this week’s. Adding venues before fill-truth is how you launder more paper into Kelly.

---

## 5) Execution architecture (close the loop)

```
Valedia brain (KEEP)
  Kernel v16e → ERE → Fusion → Filter v3 → Kelly/fuses → fee-gate
  → category → blacklist → daily budget → dedup
        │
        ▼
   Intent {qty|notional, max_*, gates, ledger_source, live_flag}
        │
        ├─ today: Valedia writer (Kalshi create_order / Coinbase market_* / Alpaca market_*)
        │         fill → book_spot_fill / record_entry  OR  UNRECONCILED
        │         GET/fills → upgrade  OR  stay UNRECONCILED
        │
        └─ later: Nautilus sidecar IntentConsumer (round DOWN only)
                  VENUE_WRITER[venue]==nautilus  AND  NAUTILUS_ENABLED
                  NT RiskEngine ≤ Valedia caps; HALT mirrors ERE
        │
        ▼
forward_test_ledger.jsonl   ← ONLY Kelly / $1k / category input
        │
        ▼
_track_record_mult / category_stats / Thorp N  → next size
```

**Intents today:** enqueue-only, `shadow=true`, `dispatchable=false`, `live_flag=false` (`nautilus_shadow_coinbase.shadow_gates`). Writer stays valedia. That is correct.

**Fill loop today (spot):** POST → `spot_ack_ok` else fail; `book_spot_fill` → FILLED iff qty+price else UNRECONCILED; `reconcile_run` upgrades iff getter complete; `settle_spot_fill` never launders paper (`settle_open` copies non-real source).

**Fill loop today (Kalshi): broken on taker** (P0.1). Maker loop is the template.

**When (if ever) to cut `VENUE_WRITER["coinbase"]` to `nautilus`:**

All of these, in order — none skipped:

1. P0.1–P0.5 and P1.3 live on Valedia (Kelly sees real fills, Kalshi acks aren’t fills).  
2. Isolated NT venv sandbox proves: unknown-outcome, restart reconcile, no double Intent, never round up (`source=nautilus_sandbox` stays excluded).  
3. ≥7 calendar days of **unified_brain** shadow rows with `compare.ok=true` against actual `coinbase_trader` POST bodies (buy `quote_size`, sell `base_size`). Allocator Coinbase compare is **not** evidence.  
4. Sidecar on the host that satisfies Coinbase SOCKS/IP (VPS or proven adapter SOCKS).  
5. `coinbase_trader` order POSTs become read-only (`get_balances`/`get_price` stay).  
6. Burton/Valedia sets `NAUTILUS_ENABLED`, `NAUTILUS_LIVE`, `VENUE_WRITER["coinbase"]="nautilus"`, and a venue live flag. **This lane never flips those.**  
7. First live NT fill is small, fee-positive, `record_entry(source=live)`, Signal `Invested:`. Old POST path grep-dead.  
8. Watchdog: one LiveNode; VPS unit stopped while NIMROD healthy.

**Do not cut Kalshi** until a clean-room adapter has shadowed `master_allocator` while allocator remains the writer.

**Rebalancer auto-exec** uses the same Intent path as brain, **after** Coinbase writer is singular (still valedia or already NT — never both). Flag default False.

---

## 6) Edge discovery loop (no unvetted live)

```
Discover (GitHub/OSS, papers, MiroFish, news)
  → Vet (license MIT/BSD/Apache/LGPL-ok; reject UNLICENSED / AGPL-into-kernel;
         cargo-deny / pin; no withdraw keys; blast radius vs MANUSCRIPT_MANIFEST)
  → Adversarial review (dual-write, round-up, paper-as-live, failover two-writers,
         fee=0, POST-ack=fill)
  → Breakage guards (VENUE_WRITER, Intent fail-closed, _is_non_real_source, drift detector)
  → Tests with no network/keys
  → Labeled sandbox/OMS gate if needed (never Kelly)
  → Fee-true forward on the ledger (small, gated)
  → Valedia runtime enables
  → Coding agents stop at the flag
```

**Owners:** RuFlo / Grok Build / Claude Code = code, tests, audits, docs. Continuous coding is **harness quality**, not a second trader.  
**Valedia runtime** = live risk, cron, keys, `--live`.  
**MiroFish** = intel ≤10%, AGPL isolated process, weight from `mirofish_accuracy_ledger` only.  
**Nautilus** = instance #1 of this loop (LGPL sidecar). Next candidates: extra perp adapters **after** HL is gated. Never Freqtrade/OpenBB as live risk. Never kalshi-agent lines.

**Adversarial bar for a new strategy:** fee-true backtest **and** a forward-test plan with `record_entry` **before** cron. “Simulation 3 days” in fund-rules is the spirit; the letter is the ledger. Kernel-derived waiver in fund-rules is how the 100% WR myth survived — **revoke that waiver** for anything that sizes money.

---

## 7) Anti-patterns (Slice 3–5 guards vs remaining holes)

| Anti-pattern | Guard in tree? | Remaining hole |
|---|---|---|
| Paper/sandbox laundering into Kelly | **Yes** — `_is_non_real_source`; sink default-deny live | `backfill:` still counts as real `n` (P0.4) |
| `fee=0` booking | **Partial** — `book_spot_fill` uses venue fee or `_spot_fee`; Kalshi `kalshi_fee` | `unified_brain.FEES` Alpaca/Polymarket 0; `compare_platforms` prefers them; scalper “0% fees” |
| POST-ack as fill (spot) | **Yes** — `extract_spot_fill` complete? else UNRECONCILED; `spot_ack_ok` | GET not injected → stubs never upgrade (P0.5) |
| POST-ack as fill (Kalshi taker) | **No** | P0.1 — **live hole** |
| Shadow dispatch / extra override | **Yes** — `enqueue_intent` locks `dispatchable`; shadow forces False even if NT flags on | Do not add a “just this once” dispatch kwarg |
| Dual Coinbase writer | **Yes** — allocator path dead (missing fn); brain is writer; rebalancer does not POST | Implementing `place_market_order` would **open** the hole — never |
| NT live without flags | **Yes** — `nautilus_live_fills_allowed` | Don’t flip flags in this lane |
| Overtrading fees | **Weak** | Sports 2h + allocator 4h same ticker; scalper; arb poll; brain 3×/day IOC on ~1% rt fees |
| WR-based kill of fat tails | Category gate is net-based | `pnl_tracker` still WR-kills |
| Hit-rate vanity | lessons.md retraction | `sports_engine` docstring + trading-engines.md still say 86–87.5% |
| Fail-open category on empty ledger | **By design** | Sports gate inert until ≥4 **live** SPORTS settles in 45d — so P0.1 must feed real fills or the gate never binds |
| Round size up | **Yes** — Intent + fill sink | — |
| Cron collision | **No** | P0.2 |
| Two fusion brains | **No** | P1.2 |
| Equity live vs paper tag | **Mostly** — Slice 3 uses `IS_LIVE` | `portfolio_monitor` still keyed off `ALPACA_PAPER` (truth bug, handoff) |

**Slice 3–5 did their job.** They did not (and could not, under fence) fix the Kalshi taker ack or inject live GET. Those are the next profit levers.

---

## 8) First three implementation slices (6–8)

Still fenced unless Burton lifts a specific flag. Same shape as 1–5: no NT wheel, no `VENUE_WRITER` cut, no rebalancer auto-exec, no `--live`.

### Slice 6 — Kalshi fill-true + sports size + one category  
**Goal:** the live printer stops lying to Kelly and stops overbetting.

- `sports_engine.execute_bets`: capture `create_order` body; **EXECUTED + `record_entry(source=live)` only if fill count > 0** (get_fills / order status). Else log `UNRECONCILED`/`PENDING` with `order_id`, **not** live edge. Mirror `MakerOrderManager._record_fill`.  
- Same for `master_allocator` Kalshi taker branch (`:466–474`). Maker branch already OK.  
- `kalshi_executor` strategy `"SPORTS_TONIGHT"` → `"SPORTS"` (one `should_trade_category` bucket).  
- Sports sizing: `min(edge_pct_table, dynamic_per_trade_cap() * bankroll)` — **never** 10% while unproven.  
- `_is_non_real_source`: treat `backfill` like paper.  
- Tests: no network; mocked create_order ack-without-fill must **not** increment `real_track_record`/`open_position_count`; fill payload does.  
- **Out:** cron minute change can ship here *or* Slice 7; do not enable GET HTTP; do not touch Coinbase POSTs.

**Exit:** tests PASS; sports cannot inflate Kelly on an unfilled limit.

### Slice 7 — spot GET (shipped off) + per-strategy cap + cron stagger  
**Goal:** crypto/equity *can* enter the ledger; winners aren’t starved by graves; no double sports fire.

- Implement `coinbase_order_get(order_id)` using existing JWT GET (read-only) and Alpaca GET; **do not call them from tests**.  
- `reconcile_run`: keep default `mock_spot_order_getter`. Add `valedia_config.SPOT_RECONCILE_HTTP=False`. Document Valedia one-liner to inject.  
- `kelly_sizing` / allocator: per-category multiplier from `category_stats` (thin → unproven 0.5; loser → 0.3; winner can earn toward 1.0 **within fuse**).  
- Stagger `allocator_run` vs `sports_run` (e.g. sports `:00`, allocator `:20`) in `install_cron.sh` + `cron-schedule.md` (docs were already stale — fix with live crontab proof).  
- Tests: getter complete → upgrade; incomplete stays UNRECONCILED; `SPOT_RECONCILE_HTTP` False ⇒ no HTTP.

**Exit:** tests PASS; live HTTP still off. Burton/Valedia injects getter when he wants Coinbase truth.

### Slice 8 — one fusion + one fee table; scalper/weather dead; rebalancer flag (False)  
**Goal:** one brain, fewer −EV ticks, rebalance *ready* but not live.

- Lift fusion weights into `valedia_config`; `unified_brain.stage_fusion` consumes them; `signal_fusion` imports or is marked non-live. Drift detector asserts.  
- Delete/bypass `unified_brain.FEES`; fee gate calls `fee_engine`.  
- `stage_kelly` delegates to `KellySizer` (or delete and call `size_position`).  
- MicroScalper: do not `alp_buy` (report-only).  
- Allocator: hard-skip `WEATHER_ARB` even if blacklist file missing.  
- `REBALANCER_AUTOEXEC = False`; if True (Burton), proposed trades go through **existing** brain/Intent gates then the **single** Coinbase writer — still valedia. This lane never sets True.  
- Tests: fusion constants single-source; scalper path no buy; rebalancer True still cannot dispatch NT; False does not POST.

**Exit:** tests PASS; `VENUE_WRITER` unchanged; `NAUTILUS_*` unchanged.

**After 6–8, stop for Burton.** Next would be P2.1 (NT venv) only if he says go.

---

## Pointers

- Seam: `scripts/integrations/nautilus_{intent,fill_sink,shadow_coinbase}.py`  
- Ledger: `forward_test_ledger.py`, `spot_reconcile.py`, `reconcile_run.py`  
- Live writers: `master_allocator.py`, `sports_engine.py`, `unified_brain.py`, `coinbase_trader.py`, `alpaca_executor.py`  
- Size/fees: `valedia_config.py`, `kelly_sizing.py`, `fee_engine.py`, `execution_policy.py`  
- Parked: `latency_arb.py`, `quant_engine.py`, `archive/hyperliquid_client.py`  
- Prior plan: `recommendations/VALEDIA-SUBSUME-NAUTILUS.md` (architecture still right; this doc is the **profit ranking** after Slices 1–5)  
- Lane: `GROK-BUILD-LANE.md`, `RUFLO-MASTER-GOAL.md`

---

*GOATWORTHY Post LLC — design only. Valedia remains sole live risk authority. Objective = realized compounding profit, not a prettier OMS.*
