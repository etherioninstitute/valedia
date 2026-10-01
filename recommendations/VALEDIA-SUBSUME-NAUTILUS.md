# VALEDIA subsumes NautilusTrader — profit-maximizing execution chassis

**Date:** 2026-09-14 (NORTH STAR revision)  
**Status:** Architecture proposal (code/design only)  
**Audience:** Burton / Valedia runtime / coding agents  
**Trees read:**
- Valedia primary (this session): `/Users/burtonchatelain/.openclaw/workspace-v2`
- Live Valedia charter/docs: `/Users/burtonchatelain/.openclaw/workspace` (per `recommendations/RUFLO-MASTER-GOAL.md`: live root is `~/.openclaw/workspace`)
- NautilusTrader clone: `/Users/burtonchatelain/MemPalace/sources/repos/github/nautilus_trader` (`version.json` = `v2.0.0rc5`; workspace `Cargo.toml` = `0.64.0`, `LGPL-3.0-only`)

---

## NORTH STAR (drives every recommendation)

Valedia is a fully local, ASI-aspirational **profit-maximizing** trading intelligence.

- **Markets:** trends, futures, arb, shorts, crypto, MiroFish social/news, multi-step lookahead, compounding.
- **Objective function:** maximum **realized**, fee-inclusive, **risk-adjusted compounding** P&L under Etherion / Kelly / fee-gate / category gate. Not research elegance. Not paper purity. Not “Nautilus-native strategies.”
- **Self-evolution:** discover high-signal GitHub/OSS → vet safety / license / supply-chain → implement **only** after tests + adversarial review + breakage guards. NautilusTrader is instance #1 of this loop, not a new brain.
- **Continuous improvement owner:** RuFlo / Claude Code (this chat / Grok Build = **code + audit only**).
- **Live risk owner:** **Valedia runtime only.** No live orders, no wallet/spend, no key changes from coding agents.

If a design choice does not raise expected realized compounding (or does not prevent a realized-P&L lie that would size the book wrong), **do not do it**.

### Scorecard (use on every NT decision)

| Question | Fail = drop the idea |
|---|---|
| Does this capture more **real** fill P&L, or stop a silent P&L lie? | Research dashboards, extra backtest frameworks, NT Strategy rewrites |
| Does this unlock a NORTH STAR surface we cannot execute today (shorts/futures/arb/resting crypto)? | “Semantic parity for its own sake” |
| Does this keep UGFT / ERE / Kelly as the only sizer? | NT `Strategy` that computes size |
| Can Valedia still freeze money independently of NT? | Embedding NT inside chat / OpenClaw |
| Is paper/sandbox a **brief labeled gate**, not a destination? | “Let’s paper-trade until it feels good” |

---

## Hard fence (non-negotiable)

This document is **code / architecture / design only**.

- Do **not** place orders, connect live venues, spend, change wallet/API keys, or run `--live`.
- Do **not** bypass Valedia as sole live risk authority.
- Paper trading is **not** the goal. Design for **real execution capability**. Paper / sandbox may appear only as a **temporary engineering gate**; every such gate is labeled as such below.
- Coding agents (Ruflo / Grok Build / this chat) ship code, tests, audits, docs. **Valedia runtime** remains the only process allowed to send venue orders under Burton’s locked rules (`CLAUDE.md` Valedia fence; `recommendations/RUFLO-MASTER-GOAL.md`; `skills/valedia/doors.md`).

---

## TLDR

**Architecture:** Sidecar Nautilus `LiveNode` as Valedia’s **execution + market-data engine**. Valedia remains the firm (UGFT kernel, ERE, Kelly/fuses, fee-gate, category gate, MiroFish ≤10%, cron, Signal, Kalshi live path). After the 7 gates, Valedia emits a signed **Intent**. One long-lived NT process is the **only** writer on assigned venues. Fills return to `forward_test_ledger.jsonl`. NT `RiskEngine` is a **stricter-or-equal floor**, never a second brain.

**How this serves maximum profit:** Today’s cron REST scripts leak P&L (Coinbase/Alpaca fills not in the ledger; rebalancer prints and does not trade; latency arb polls instead of a live book; Hyperliquid/shorts archived; Polymarket arb unexecutable). NT closes **execution quality, fill truth, WS books, perps/shorts, and CLOB arb** — the surfaces that compound. It does **not** replace kernel, sports edge, MiroFish, or Kalshi (no NT adapter; Kalshi is the current live money path — do not break it).

**Next slice (this chat / Ruflo, still no keys/orders):** Intent schema + exclusive `VENUE_WRITER` lock + fill sink that **cannot** write `source="live"` unless Valedia flags say so. No `pip install nautilus_trader`. No `LiveNode`. Parallel non-NT profit work (do not block): `sports_engine` ledger-truth (handoff B1).

First live NT venue (later, Valedia-gated): **Coinbase**. Then Hyperliquid (shorts/futures). Then Polymarket (arb). Kalshi last (custom adapter; keep Valedia writer until cutover). Kraken stays read-only. Mercury never enters NT.

---

## 0. What “subsume” means under the NORTH STAR

**Subsume** = Valedia **owns** Nautilus as a capability, the way it owns `coinbase_trader.py` today — except NT is a real OMS instead of a thin REST script.

| Valedia (brain) | Nautilus (chassis) |
|---|---|
| *Whether* to trade | *How* the order lives on the wire |
| *What* (instrument, side, thesis) | Venue adapters, WS data, books |
| *How much* (Kelly / ERE / fuses) | Mechanical qty/notional/rate floor |
| *When* (cron, lookahead, compounding path) | Child-order lifecycle (GTD, maker, TWAP **after** parent Intent) |
| Ledger truth + Signal | Reconcile unknown outcomes → ledger sink |
| OSS discovery + gates | One more vetted engine, pin the wheel |

**Reject as primary:** rewrite kernel/ERE/fusion as Nautilus `Strategy.on_bar`. That is research elegance. It wraps UGFT in LGPL, moves sizing into NT, and stalls Kalshi/Alpaca (no adapters). The only NT Strategy Valedia ships is a thin **IntentConsumer** that **never enlarges size**.

Nautilus’s own marketing (“research, simulation, and live share one architecture”) is **useful for OMS parity tests**, not a product goal. Valedia already has a brain. The gap is **realized-P&L plumbing**.

---

## 1. Valedia trading-relevant surface

Live charter lives in `~/.openclaw/workspace`. This session’s primary tree is `workspace-v2`. Paths below are relative to either tree unless noted; they currently match.

### 1.1 Identity, fence, one risk brain

| What | Path |
|---|---|
| Persona / mission | `SOUL.md` — 7 gates, 8 platforms, fund rules |
| Doors / freeze switches | `skills/valedia/doors.md` — one risk brain; Mercury freeze; cron + `strategy_blacklist.json` |
| Architecture | `skills/valedia/architecture.md` — NIMROD primary, Contabo VPS backup, SOCKS, IP locks |
| Signal protocol | `skills/valedia/signal-protocol.md` — only `Invested:` and `Profit:` |
| Trading skill graph | `skills/trading/INDEX.md` |
| Fund rules | `skills/trading/rules/fund-rules.md` — 4%/trade, 10%/day, fee-gate, ERE, no leverage w/o approval |
| Coding fence | `CLAUDE.md` (Valedia fence); `recommendations/RUFLO-MASTER-GOAL.md`; `recommendations/CLAUDE-TO-RUFLO-HANDOFF.md` |
| Shared constants | `scripts/integrations/valedia_config.py` — `MIROFISH_WEIGHT=0.10`, Kelly fuses, `EXEC_DRY_RUN=False` (live maker enabled 2026-09-12) |
| Manuscript blast radius | `MANUSCRIPT_MANIFEST.md` — kernel vs ERE canonical modules |
| Drift detector | `scripts/valedia_consistency_check.py` |

`SOUL.md` decision engine (7 gates): Kernel v16e → ERE → Signal Fusion → Kelly → Fee Gate → Daily Budget → Dedup.

`doors.md` non-negotiables: one risk brain; approval gate on spend/send/transfer; fee-gate; crypto-first; `$1000 undeployed until the forward-test ledger earns it`; MiroFish ≤10%.

### 1.2 Intelligence / strategy (Valedia keeps this)

| Layer | Path | Role |
|---|---|---|
| Unified Brain | `scripts/integrations/unified_brain.py` | Orchestrator: Config → Intel → Universe → Kernel → ERE → Fusion → Filter v3 → Kelly → Fee Gate → Execute → Log |
| Kernel v16e | `scripts/integrations/kernel_predictor_v16e.py` | UGFT market functionals; adaptive SVD; `MANUSCRIPT_VERSION = UGFT-v32.9` |
| Etherion Risk | `scripts/integrations/Etherion_Risk_Engine.py` | Heuristic NS/YM/Fusion mappings; writes `models/latest_etherion_risk.json`; HALT → size 0 |
| Signal fusion | `scripts/integrations/signal_fusion.py` | Parallel brain (weights ML 40 / tech 30 / sentiment 15 / vol 10 / whale 5) — **diverges** from `unified_brain` inline scheme (known; handoff B2) |
| Kelly | `scripts/integrations/kelly_sizing.py` | Half-Kelly + `dynamic_per_trade_cap` + Thorp concurrent `f/√N` + Bayesian shrink |
| Fee gate | `scripts/integrations/fee_engine.py` | Canonical Kalshi nonlinear fee; static schedules for Coinbase/Kraken/Alpaca/Polymarket |
| Execution policy | `scripts/integrations/execution_policy.py` | `build_execution_plan` → taker / maker / no_trade; `MakerOrderManager` persists across cron |
| Book state | `scripts/integrations/market_state.py` | Microstructure for maker/taker |
| Filter / universe | `skills/trading/core/filter-v3.md`; `scripts/integrations/dynamic_universe.py` | Equity vs crypto gates; day-of-week |
| Intel | `intel_layer.py`, `intel_sentiment.py`, `hedge_fund_intel.py`, `insider_mirror.py`, `whale_copier.py`, `fred_macro.py`, `market_data.py` | Fear/Greed, 13F, Form 4, whales, FRED |
| ML | `ml_kernel_retrain.py`, `ml_retrain.py`; `models/*_USD_model.pkl` | 8 GradientBoosting models |
| Quant / LMSR | `quant_engine.py`, `quant_run.py` | Kalshi adapter active; Polymarket built, `ENABLED` false |
| Sports | `sports_engine.py`, `sports_run.py` | Live Kalshi 70–85¢ favorites; cron every 2h (`skills/valedia/cron-schedule.md`) |
| Latency arb | `latency_arb.py`, `arb_run.py` | CEX spot vs Kalshi strikes — **DRY-RUN** |
| Scanner | `kalshi_scanner.py`, `scanner_run.py` | 600+ Kalshi markets → `models/scanner_results.json` |
| DCA | `dca_module.py`; `models/dca_config.json` | Scheduled buys of kernel picks |
| Rebalancer | `scripts/integrations/rebalancer.py` | Coinbase target weights BTC 35 / ETH 25 / SOL 20 / XRP 10 / ADA 5 / AVAX 5; **prints actions, does not auto-execute** |
| Allocator | `master_allocator.py`, `allocator_run.py` | **True live Kalshi authority** (handoff 2026-09-13). Cron every 4h. `kalshi_executor.run` is inert/legacy. |
| Blacklist | `models/strategy_blacklist.json`; `pnl_tracker.py` | Auto-kill <25% WR after 3+ trades |
| Killed | `skills/valedia/trading-engines.md` | WEATHER_ARB, OIL_BRACKET permanently killed |

### 1.3 Measurement (the only admissible edge)

| What | Path |
|---|---|
| Forward-test ledger | `scripts/integrations/forward_test_ledger.py` → `models/forward_test_ledger.jsonl` |
| Reconcile cron | `scripts/integrations/reconcile_run.py` |
| Category gate | `forward_test_ledger.should_trade_category` — real losses only, 45d window, fail-open, never permanent |
| Track record | `valedia_config._track_record_mult()` — unproven until `KELLY_MIN_PROVEN_TRADES=20` **real** settled (paper excluded) |
| P&L tracker | `pnl_tracker.py`, `pnl_run.py` → `models/pnl_tracker.json` |
| Sports log | `models/sports_engine_log.json` (parallel; handoff says this is a ledger-truth hole) |
| Self-audit | `self_audit.py`; `models/opus_audit.json` |
| MiroFish accuracy | `mirofish_accuracy_ledger.py`, `mirofish_ledger_run.py` → `models/mirofish_accuracy_ledger.jsonl` |

Handoff snapshot (do not treat as live P&L without re-reading the ledger): 38 backfilled + live-recording; thin/UNPROVEN; cap throttled. **Paper marks in `brain_log.json` are not admissible.** Compounding **must not** accelerate on sandbox/NT-paper rows.

### 1.4 Venue clients (today’s execution surface)

| Venue | Path | Mode today | Notes |
|---|---|---|---|
| **Kalshi** | `kalshi_auth.py`, `kalshi_executor.py`, `kalshi_trade.py`, `kalshi_scanner.py` | **LIVE** via `master_allocator` + `sports_engine` | RSA-PSS; proven fills. `EXEC_DRY_RUN=False` (maker live). **Do not migrate first.** |
| **Coinbase** | `coinbase_trader.py` | LIVE market IOC buy/sell | JWT ES256; SOCKS via `vps_proxy.py`. Used by `unified_brain` and `rebalancer`. Allocator Coinbase branch marks `EXECUTED` **without** `record_entry` (`master_allocator.py` ~478–488). |
| **Alpaca** | `alpaca_executor.py` | `IS_LIVE = True` | Thin REST; fractional notional. `unified_brain` still tags some Alpaca fills `source="paper"` (`:839`). `portfolio_monitor.py` still keys off `ALPACA_PAPER`. |
| **Kraken** | `kraken_client.py` | **READ-ONLY** | Explicit: no withdraw/deposit/create-order. IP-locked to VPS `85.239.234.32`. |
| **Polymarket** | `polymarket_client.py` | Scan only | Public CLOB/Gamma. Quant engine: `ENABLED = True` not flipped. |
| **Hyperliquid** | `scripts/integrations/archive/hyperliquid_client.py` | Archived | Skills live in vendor HyperGrok. `tasks/lessons.md`: “Hyperliquid removed but still referenced.” **NORTH STAR shorts/futures gap.** |
| **Mercury** | `mercury_client.py`, `fund_pipeline.py`, `fund_manager.py` | Banking | IP-locked. Transfers ≥$100/tx or ≥$200/day need Burton. **Not a trading venue.** |
| SOCKS / VPS | `vps_proxy.py`; architecture.md | Required for Coinbase/Kraken/Mercury from Mac | NT adapters must honor this or stay off those venues. |

### 1.5 Cron / process model (the architectural constraint)

`skills/valedia/cron-schedule.md`: ~24 jobs on NIMROD. Each job is a **fresh Python process**. That is why `MakerOrderManager` persists to `models/maker_orders.json` — otherwise a resting order is unmanaged next cycle.

This is the opposite of Nautilus: one long-lived `LiveNode`, one event loop, process-wide state (`docs/concepts/live.md`: **one LiveNode per process**).

**Profit implication:** GTD / maker / OCO / reduce-only **cannot compound** on a process that dies every cron tick. That is the OMS gap, not a strategy gap.

Watchdog: `scripts/integrations/watchdog.py`. Heartbeat / VPS failover: 3 missed heartbeats → VPS activates. A sidecar NT node must be in that failover story or live orders become un-managed on failover.

### 1.6 MiroFish (KEEP — intelligence, never execution)

| What | Path |
|---|---|
| Skill | `skills/trading/intelligence/mirofish.md` — AGPL-3.0 swarm, ≤10% weight |
| Runner | `scripts/integrations/mirofish_runner.py` — seed → localhost:5001 → `models/mirofish_signal.json` |
| Extractor | `mirofish_signal_extractor.py` |
| Seeds | `mirofish_seeds/` |
| Health | `scripts/mirofish_healthcheck.py`, `scripts/mirofish_primary.sh` |
| Accuracy | `mirofish_accuracy_ledger.py` — **this** earns weight; NT does not |

MiroFish is NORTH STAR **social/news**. It stays a **separate OS process**. AGPL-3.0: do not link it into a Nautilus binary or into Valedia’s proprietary kernel process. Weight stays ≤10% until the accuracy ledger >55% over 2–4 weeks (handoff lock).

### 1.7 Council / agents (advisory, not execution)

| What | Path |
|---|---|
| Council | `council/README.md` — 45 advisor personas. Ornith = CIO. risk-manager convened first. |
| Connector sketch | `council/lib/connector-interface.ts` — **Not wired to live Python.** Do not become a second OMS. |
| Execution planner (TS) | `council/lib/execution-planner.ts` — TWAP/VWAP pure functions. Port candidate **into Valedia Intent child-plans**, not into NT Strategy size. |
| Credential store (TS) | `council/lib/credential-store.ts` | Do not merge into NT. |

Council output is argument, not an order. Same rule after NT: advisors propose, Valedia gates, Nautilus (maybe) sends.

### 1.8 Vendors (do not confuse with live path)

| Vendor | Path | License / disposition |
|---|---|---|
| **kalshi-agent** | `vendors/kalshi-agent/` | **UNLICENSED — do not copy/merge.** Valedia already clean-roomed `execution_policy` / `market_state` / Kelly shrink. NT Kalshi adapter (Phase 5) is also clean-room. |
| **hypergrok-trading-desk** | `vendors/hypergrok-trading-desk/` | MIT skills. **KEEP process model** (one writer, trade-only key, expiresAfter, ticket id). **REPLACE** any idea of a second Grok-bot `/exchange` writer — NT Hyperliquid adapter is the writer when Valedia enables. |
| **OpenTerminal** | `vendors/OpenTerminal/` | Research UI / Yahoo, FRED, CoinGecko. **KEEP as research.** Not an OMS. Do not dual-feed live sizing from it. |
| **fortress** | `vendors/fortress/` | Stealth browser. **PARKED** (M1 Colima qemu crash). Irrelevant to OMS. |
| **youtube-automation-agent** | `vendors/youtube-automation-agent/` | Not a trading path. |

### 1.9 What Valedia is missing (the profit gap NT is allowed to close)

From the live tree, scored by compounding P&L — not from Nautilus marketing:

| Gap | P&L effect today | NT’s job |
|---|---|---|
| 1. No durable order/position state across cron (except maker JSON) | Resting liquidity dies; maker edge on Coinbase un-compoundable | LiveNode order state machine |
| 2. No venue-report reconciliation (unknown outcomes, external orders) | REST POST success treated as fill → **P&L lies** → Kelly mis-sizes | NT reconciliation → ledger sink |
| 3. No WebSocket market-data engine | `latency_arb` cannot fire reliably; books stale | Coinbase/Kraken/HL DataEngine |
| 4. No OCO/OTO/post-only/reduce-only on crypto | Cannot express shorts/futures risk the NORTH STAR wants | Adapter order types **after** Valedia parent Intent |
| 5. Incomplete fill capture Coinbase/Alpaca (`master_allocator` ~478–497) | Crypto/equity edge **invisible** → compounding throttled or wrong | Fill events → `record_entry` |
| 6. Rebalancer does not execute | Target book drifts; compounding on the wrong mix | Intent-emit through gates |
| 7. Two brains + `source="paper"` / `ALPACA_PAPER` inconsistency | Truth bugs, not OMS — **fix in Valedia**, NT must not inherit | — |
| 8. Hyperliquid / Polymarket / Kraken exec archived, disabled, or permission-locked | Shorts/futures/arb **unexpressed** | NT adapters when Valedia enables |
| 9. No OMS backtest↔live parity | Kernel has its own backtests; venue behavior does not | Sandbox as **engineering gate only** |

Nautilus exists to close 1–6 and 8–9. It must not be allowed to close “who decides.” Gap 7 is a Valedia-only fix and is **higher immediate profit leverage** than installing NT.

---

## 2. KEEP vs REPLACE (scripts, ledgers, MiroFish, vendors)

This is the inventory coding agents must not “simplify” away.

### 2.1 KEEP forever (Valedia brain — profit intelligence)

Do **not** port these into Nautilus `Strategy` callbacks.

- `kernel_predictor_v16e.py`, `Etherion_Risk_Engine.py`, `kelly_sizing.py`, `fee_engine.py`, `valedia_config.py`
- `unified_brain.py` decision pipeline (gates). Execute **tail** for NT-owned venues becomes Intent emit.
- `master_allocator.py` ranking + Kalshi live writer (until Phase 5)
- `sports_engine.py` **edge scoring / universe / intel** (fix ledger-truth in Valedia; NT does not write sports bets until Kalshi adapter)
- `quant_engine.py` LMSR / EV gap (strategy). Polymarket **exec** later via NT.
- `kalshi_scanner.py`, `intel_layer.py` and intel family
- `filter-v3` / `dynamic_universe.py`
- `mirofish_runner.py` + extractor + accuracy ledger (separate process)
- `forward_test_ledger.py` as **the** edge source
- `valedia_consistency_check.py`
- Mercury / `fund_pipeline.py` / key files
- `kraken_client.py` read-only until Burton issues trade perms
- `alpaca_executor.py` until an Alpaca adapter exists (do not silent-swap IB)
- Council personas (advisory)

### 2.2 REPLACE (execution chassis — when a venue is cut over)

| Script / path | What dies on cutover | Replacement |
|---|---|---|
| `coinbase_trader.py` **order POSTs** (`market_buy` / `market_sell` / `place_market_order`) | Live writer | NT Coinbase exec adapter via Intent. **KEEP** `get_balances` / `get_price` / SOCKS helpers until DataEngine is proven. |
| `MakerOrderManager` JSON as Coinbase order home | Cron-surviving maker file for CB | NT order state. **KEEP** `execution_policy.build_execution_plan` (taker/maker/no_trade is Valedia policy). |
| Archived `hyperliquid_client.py` | Already dead | NT Hyperliquid adapter when Valedia enables. Do not un-archive the old client as a second writer. |
| `polymarket_client.py` **order** path (today scan-only) | Homegrown CLOB exec | NT Polymarket adapter when Valedia enables. **KEEP** Gamma/scan. |
| Council TS `connector-interface.ts` order methods | Never live; do not activate | Dead as OMS. |

### 2.3 KEEP logic, CHANGE wiring (profit-critical)

#### Rebalancer — `scripts/integrations/rebalancer.py`

| KEEP | REPLACE |
|---|---|
| `TARGET_ALLOC` (BTC 35 / ETH 25 / SOL 20 / XRP 10 / ADA 5 / AVAX 5) | `"Execute manually or enable auto-rebalance"` (dead capital) |
| `REBAL_THRESHOLD = 0.05` | Direct `market_buy` / `market_sell` as a **second** Coinbase writer |
| Drift math, skip if total < $5, balance/price reads | Ungated auto-exec |

**Target wiring:** rebalance **actions** become Intents that still pass ERE / Kelly / fee-gate / daily budget / exclusive-writer. NT (or, until cutover, the single Valedia Coinbase writer) executes. A drifting book is a compounding leak; an ungated rebalancer is a ruin path. Both are -EV.

#### Ledgers

| Artifact | KEEP | REPLACE / extend |
|---|---|---|
| `forward_test_ledger.py` + `forward_test_ledger.jsonl` | Canonical truth; category gate; `real_track_record()` paper exclusion | Exclude `sandbox` / `nautilus_sandbox` the same way as `paper`. Add Coinbase/Alpaca fees via `fee_engine` (today Kalshi-only `entry_fee`). |
| `reconcile_run.py` | Cron | Consume NT fill/unknown-outcome events, not only Kalshi REST |
| `pnl_tracker.py` | Strategy kill <25% WR after 3+ | Fee-inclusive; reconcile with ledger (handoff B5). **Not** a second Kelly source. |
| `models/sports_engine_log.json` | Temporary until B1 | **Stop treating as truth.** Sports fills → `record_entry` |
| `models/maker_orders.json` | Until NT owns that venue | Per-venue: Kalshi file stays until Phase 5; Coinbase file dies on Phase 2 |
| `models/brain_log.json` paper marks | Ignore for sizing | Never feed `_track_record_mult` |
| `mirofish_accuracy_ledger.jsonl` | KEEP — MiroFish weight | Never merge into NT cache |

**Compounding rule:** only `source` that Valedia runtime labeled `live`, fee-inclusive, fill-reconciled, may lift Kelly. NT being “up” is not evidence.

#### MiroFish

| KEEP | FORBIDDEN |
|---|---|
| Separate process, AGPL, seeds, runner, extractor, ≤10% until earned | Linking MiroFish + Nautilus + UGFT in one process |
| Accuracy ledger as the only weight-increase evidence | Using NT backtests to “prove” MiroFish |
| Signal JSON consumed by fusion | MiroFish submitting orders |

#### Vendors (repeat, operational)

- **kalshi-agent:** reference / park. Zero lines into Valedia or into an NT adapter.
- **hypergrok-trading-desk:** steal **controls**, not runtime. One writer on HL.
- **OpenTerminal:** research quotes only.
- **fortress:** parked.

### 2.4 KEEP as Valedia writer until a proven NT adapter

- Kalshi: `kalshi_auth.create_order`, `master_allocator`, `sports_engine`
- Alpaca: `alpaca_executor.py` (fix `source="paper"` **before** any NT equities work)
- Kraken: no writer
- Mercury: never NT

---

## 3. NautilusTrader core value (chassis, not brain)

Clone: `/Users/burtonchatelain/MemPalace/sources/repos/github/nautilus_trader`.

### 3.1 What it is

From `README.md` and `docs/concepts/overview.md`:

> Open-source, production-grade, **Rust-native** engine for multi-asset, multi-venue trading. Research, deterministic simulation, and live execution share one event-driven architecture. **Python is the control plane** (strategy, config, orchestration).

Valedia uses the **live execution + data** half. The research/simulation half is a **labeled engineering gate** for OMS tests, not a paper-trading product.

Three environment contexts (`docs/concepts/architecture.md`):

| Context | Data | Execution | Valedia use |
|---|---|---|---|
| `Backtest` | Historical | Simulated matching | OMS parity tests / child-order lookahead tests |
| `Sandbox` | Real-time | Simulated (`crates/adapters/sandbox`) | **TEMPORARY ENGINEERING GATE only** |
| `Live` | Real-time | Venue connections (paper **or** real accounts — NT does not treat “paper account” as a separate architecture) | **Product.** Valedia live flag still required. |

### 3.2 Python / Rust split

| Layer | Path | Role |
|---|---|---|
| Rust workspace | `Cargo.toml` members | core, model, common, data, execution, live, backtest, risk, portfolio, persistence, network, adapters/* |
| Live node | `crates/live/` → Python `nautilus_trader.live.LiveNode` | Long-lived runner, reconciliation, shutdown. **One per process.** |
| Execution | `crates/execution/` | Order lifecycle, matching engine (sim), emulator, clients |
| Data | `crates/data/` | Subscriptions, bars, books, routing — **latency arb ingest** |
| Risk | `crates/risk/` | Pre-trade validation, `TradingState::{Active,Reducing,Halted}` — **floor, not ERE** |
| Portfolio | `crates/portfolio/` | Balances, positions, PnL — **feeds ledger, does not replace it** |
| Trading/strategy | `crates/trading/` | Thin IntentConsumer only |
| Python package | `python/nautilus_trader/` | PyO3 re-exports from `_libnautilus` |
| v1 → v2 | `MIGRATION_V2.md` | v2 is the path (`TradingNode` → `LiveNode`). Do **not** start on Cython v1. |
| Version | `version.json` | `v2.0.0rc5` — **API still moving**. Pin a wheel; do not track `develop` for live. |

No Rust toolchain required for official wheels. MSRV = latest stable Rust if building from source (`1.98.1` in `Cargo.toml`).

### 3.3 Kernel data flow (Valedia sits *above*)

```
Data clients → DataEngine → Cache + MessageBus → Trader (actors/strategies)
Strategy commands → RiskEngine → ExecutionEngine → Exec clients
Exec events → Cache / Bus → Portfolio / Risk / Strategy
```

Stock Nautilus: **Strategy emits orders into RiskEngine.**  
Valedia subsumption: Strategy is **IntentConsumer**. Size is already gated. Consumer may only translate, round **down**, or refuse.

### 3.4 Adapters vs NORTH STAR surfaces

| Adapter | NT type | NORTH STAR surface | Valedia overlap |
|---|---|---|---|
| Coinbase | Data/Execution | Crypto trends + rebalance compounding | **Direct. First live subsumption.** |
| Kraken | Data yes; **Execution WIP** | Crypto + (later) futures data | Data useful for arb. Exec **off** until WIP done **and** Burton trade perms. |
| Hyperliquid | Data/Execution | **Shorts / perps / futures** | Archived Valedia client. **Second live subsumption.** Leverage off until Burton. |
| Polymarket | Data/Execution | **Arb** vs Kalshi | Scan-only today. **Third.** |
| Interactive Brokers | Data/Execution | Equities / possible futures | Not a silent Alpaca replacement. |
| Binance, Bybit, OKX, BitMEX, Deribit, dYdX, … | Data/Execution | Expansion inventory (more perps/shorts) | Discover/vet later under OSS loop. Not current live. |
| Betfair | Data/Execution | Sports exchange | **Not a Kalshi substitute.** |
| Databento, Tardis, Blockchain | Data | Research/data | Not exec. |
| Sandbox | Execution sim | **Engineering gate only** | |

**Confirmed absent in this clone (Kalshi, Alpaca).** Those stay Valedia-native until a custom adapter is written.

Python live examples: `examples/live/{coinbase,kraken,hyperliquid,polymarket,sandbox,interactive_brokers,...}/`. Out-of-tree adapter template: `examples/live/_template/README.md` + `docs/developer_guide/python_adapters.md`.

### 3.5 Why NT execution policy is a profit feature

`docs/concepts/live.md` + `docs/concepts/execution/policies.md`: a transport result does **not** prove what the venue did (unknown command outcome, no global FIFO, external orders, startup reconcile).

Valedia’s cron scripts currently pretend REST `POST` success = fill (`master_allocator` Coinbase/Alpaca branches set `EXECUTED` with no `record_entry`). That is a **compounding bug**: Kelly and category gates cannot see crypto/equity reality.

Subsume so that **fills, rejects, and unknowns** hit the ledger. That is profit infrastructure, not ceremony.

### 3.6 License / supply-chain (OSS vet — instance #1)

- `LICENSE` + `Cargo.toml`: **GNU LGPL v3.0 only**
- Combined Work (LGPL §4): dynamic linking **or** Minimal Corresponding Source + relinkable app; notices; GPL+LGPL texts
- `README.md`: `cargo-deny` allow-list compatible with `LGPL-3.0-only`
- Community adapters must be LGPL-compatible (`ADAPTERS.md`)
- RC (`v2.0.0rc5`): pin wheel; isolate venv; do not vendor `develop`

**Implication:** sidecar process + JSON/IPC Intent bus keeps UGFT/ERE **proprietary** and NT **LGPL**. In-process PyO3 embed **is** a Combined Work.

MiroFish is **AGPL-3.0**. Never link MiroFish + Nautilus + Valedia into one process.

### 3.7 How NT maps onto NORTH STAR profit surfaces

| Surface | Valedia owns | NT owns when enabled |
|---|---|---|
| **Trends** | Kernel v16e, filter-v3, fusion | WS books / bars so entries are not CoinGecko-stale |
| **Futures / shorts** | Direction, size, leverage **off** until Burton, ERE HALT | Hyperliquid (then other perp adapters) reduce-only / pos-side |
| **Arb** | `latency_arb` trigger + Kalshi leg; quant Kalshi↔Polymarket thesis | Spot WS ingest; Polymarket CLOB leg |
| **Crypto** | Universe, Kelly, rebalance **targets** | Coinbase exec + resting orders + fill truth |
| **MiroFish social/news** | Swarm, weight, accuracy ledger | Nothing (process isolation) |
| **Multi-step lookahead** | Parent plan (kernel path, arb chain, TWAP notionals) | Child orders after parent Intent; OMS backtest of **children only** |
| **Compounding** | Ledger → Kelly multiplier → undeployed $1k gate | Reconciled live fills; sandbox never lifts caps |

---

## 4. Subsumption architecture

### 4.1 Ownership split (non-negotiable)

```
                    Burton (approval on spend / leverage / key changes)
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ VALEDIA  — sole live risk authority  +  profit intelligence          │
│  Kernel v16e · ERE · Fusion · Filter v3 · Kelly/fuses · Fee-gate     │
│  category gate · blacklist · EXEC_DRY_RUN / --live · Signal pings    │
│  forward_test_ledger · valedia_config · MiroFish ≤10% · Council      │
│  lookahead / compounding path / OSS discovery gates                  │
│                                                                      │
│  Emits: Intent {venue, instrument, side, qty|notional, order_type,   │
│          tif, client_order_id, strategy, max_notional, gates_json}   │
│  Never: raw venue REST from a second writer on a NT-owned venue      │
└──────────────────────────────────────────────────────────────────────┘
                │ Intent JSONL / Unix socket (local, no cloud)
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ NAUTILUS SIDECAR  — execution + market-data chassis                  │
│  DataEngine · ExecutionEngine · Portfolio · Cache · Reconciliation   │
│  RiskEngine = mechanical floor (notional/qty/rate/TradingState)      │
│               configured ≤ Valedia caps; Halt mirrors ERE HALT       │
│  Adapters: Coinbase, Hyperliquid, Polymarket, (later Kalshi custom)  │
│  Thin IntentConsumer Strategy: translate Intent → SubmitOrder        │
│  MUST NOT: increase size, invent symbols, bypass Valedia gates       │
└──────────────────────────────────────────────────────────────────────┘
                │ fills / rejects / unknown-outcomes / positions
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LEDGER SINK                                                          │
│  forward_test_ledger.record_entry / settlement  (fee-inclusive)      │
│  Signal: Invested / Profit only (existing protocol)                  │
└──────────────────────────────────────────────────────────────────────┘
```

**Valedia owns**

- Strategy and intelligence (kernel, fusion, MiroFish, council, scanners, sports edge, arb thesis)
- Risk authority (ERE, Kelly, fuses, fee-gate, category gate, blacklist, daily budget)
- Runtime gates (`EXEC_DRY_RUN`, `--live`, `IS_LIVE`, per-venue enable flags)
- Ledger truth and Signal
- Custody / banking (Mercury) and key files
- Kalshi (until a custom NT adapter exists and is proven)
- Alpaca (until an adapter exists — IB is a possible later replacement, not a silent swap)
- Kraken permissions (stay read-only)
- OSS intake (discover → vet → test → adversarial review → breakage guards)

**Nautilus provides**

- Long-lived event loop and order state machine
- Venue adapters (normalized domain model)
- Data pipeline (WS quotes/trades/books/bars)
- Execution algorithms (TWAP etc. — *after* Valedia has gated the parent Intent)
- Portfolio cache + startup/runtime reconciliation
- Sandbox + backtest for **OMS parity tests** (not as a product goal)
- Mechanical RiskEngine as a second fuse

**Nautilus must never own**

- Edge, sizing, or “should we trade”
- Withdraw / transfer (Mercury, exchange withdraw keys)
- UGFT manuscript math
- MiroFish
- A second live writer on a venue Valedia still scripts

### 4.2 Intent contract (the seam)

An Intent is **not** a Nautilus `SubmitOrder`. It is Valedia’s already-gated decision. Required fields:

- `intent_id` (uuid; becomes NT `client_order_id`)
- `ts`, `strategy`, `venue`, `instrument`
- `side`, `order_type` (`market|limit|maker_improve`), `tif`
- `qty` **or** `notional` (exactly one)
- `limit_price` if limit/maker
- `max_notional`, `max_qty` (hard caps copied from Kelly/fuses — NT RiskEngine re-checks these)
- `gates`: `{ere_level, fee_ok, category_ok, blacklist_ok, exec_dry_run, live_flag}` — consumer **refuses** if `live_flag` false or `ere_level` in `{HALT,DANGER}`
- `ledger_source` (`live` only when Valedia runtime says so; never because NT is up)

Consumer rules:

1. If any gate is missing or fail-closed → drop, log, do not send.
2. Never round size **up**. Slippage/lot-size rounding only **down**.
3. Unknown command outcome → do not retry as a new Intent; reconcile by `client_order_id` (HyperGrok pattern + NT policies).
4. Fill → `forward_test_ledger.record_entry(..., source="live"|temporary sandbox label)`.

### 4.3 Exclusive venue writer lock

Double-execution is the catastrophic **negative-profit** risk (two fills, one thesis). Rule:

> For each venue, **exactly one** live writer exists: either a Valedia script **or** the Nautilus sidecar, never both.

| Venue | Writer now | Writer after subsumption (target) |
|---|---|---|
| Kalshi | Valedia (`master_allocator`, `sports_engine`) | Valedia until custom adapter + cutover |
| Coinbase | Valedia (`coinbase_trader`) | **Nautilus** (first cutover) |
| Alpaca | Valedia (`alpaca_executor`) | Valedia (no NT adapter) |
| Kraken | none (read-only) | none |
| Polymarket | none | Nautilus **when** Valedia enables |
| Hyperliquid | none (archived) | Nautilus **when** Valedia enables |
| Mercury | Valedia banking | Valedia forever |

Cutover of a venue = (a) Valedia scripts for that venue become read-only, (b) sidecar is the only `SubmitOrder` path, (c) watchdog knows how to stop the sidecar.

### 4.4 RiskEngine vs ERE (do not dual-brain)

Nautilus `RiskEngine` (`crates/risk/src/engine/mod.rs`): order field/notional/qty checks, rate limits, `TradingState`.

Etherion: regime/cascade/QEI drawdown → `latest_etherion_risk.json` → `dynamic_per_trade_cap` and HALT.

**Policy:** NT RiskEngine is a **floor**. Map ERE `HALT`/`DANGER` → `TradingState::Halted`; `WARNING` → `Reducing`. NT max-notional ≤ Valedia fuse. If they disagree, **deny the order**. Never let NT be more aggressive than Valedia.

### 4.5 Cron vs daemon

Keep Valedia crons for intelligence, allocation, sports, ledger reconcile, Signal, MiroFish.

Add **one** launchd/systemd unit: `valedia-nautilus-sidecar` on NIMROD, replicated on VPS as hot backup **stopped** until heartbeat failover. Failover must not start a second writer while NIMROD is still sending.

`LiveNode.run_async()` hosting inside OpenClaw/Hermes is possible (`docs/concepts/live.md`) but couples trading crash domain to chat. Prefer a dedicated process.

### 4.6 SOCKS / IP locks

Coinbase, Kraken, Mercury currently egress via VPS SOCKS. NT Rust adapters do **not** automatically use `vps_proxy.py`. Cutover of Coinbase requires either:

- sidecar runs **on the VPS** (simplest for IP-locked keys), or
- sidecar on NIMROD with adapter-level SOCKS (must be verified per adapter; do not assume).

Kraken keys are IP-locked **and** read-only — no NT execution until Burton explicitly issues trade perms **and** an exec-capable adapter exists.

### 4.7 OSS self-evolution loop (Nautilus is the template)

RuFlo / Claude Code / this chat run the loop. Valedia runtime still flips live.

1. **Discover** high-signal GitHub/OSS (production-grade, venue overlap, local-runnable, not a second brain).
2. **Vet:** license (MIT/BSD/Apache/LGPL-ok; reject UNLICENSED / AGPL-into-kernel); supply-chain (`cargo-deny` / lockfile pin / no mystery wheels); safety (no withdraw keys, no `--live` from agents); blast radius vs `MANUSCRIPT_MANIFEST.md`.
3. **Adversarial review:** dual-write, size-up rounding, paper-as-live, failover two-writers, license Combined Work.
4. **Breakage guards:** exclusive `VENUE_WRITER`, Intent gate refusal, ledger `source` exclusions, drift detector.
5. **Tests** (no network / no keys) then a **labeled** sandbox gate if OMS behavior cannot be unit-tested.
6. **Implement** the smallest slice that unblocks realized P&L.
7. **Valedia runtime** enables the venue. Coding agents stop at the flag.

Later candidates (not now): additional NT perp adapters (Deribit/dYdX) for shorts/futures **after** Hyperliquid is gated; never Freqtrade/OpenBB as live risk (already locked in handoff).

---

## 5. Integration options, ranked by profit (not elegance)

### Option A — Sidecar `LiveNode` (recommended)

Valedia process and Nautilus process. Intent queue on localhost. Fills back to ledger.

| | |
|---|---|
| **Pros** | Process isolation; UGFT stays out of Combined Work; one LiveNode constraint satisfied; crash of chat ≠ kill OMS; exclusive writer easy to enforce; matches HyperGrok “one writer”; failover is start/stop of a unit; MiroFish AGPL stays out |
| **Cons** | Two runtimes to operate; IPC to design; SOCKS/host placement; v2 RC API pin |
| **Live path** | Real Coinbase/HL/Polymarket orders from sidecar **after** Valedia `--live` + venue lock + Burton enable |
| **Paper** | NT `Sandbox` only as Phase 1 engineering gate |
| **Profit** | Unlocks fill-truth, resting orders, WS arb ingest, shorts/futures, Polymarket leg **without** pausing Kalshi |

### Option B — In-process embed (`LiveNode.run_async` inside a Valedia daemon)

| | |
|---|---|
| **Pros** | No IPC; shared memory cache |
| **Cons** | Combined Work (LGPL); MiroFish/AGPL contamination risk; crash domain merged; harder exclusive writer vs leftover cron |
| **Verdict** | Acceptable **later** if sidecar IPC is a **measured** fill-quality bottleneck. Not first. Elegance ≠ profit. |

### Option C — Gradual replace of HTTP clients (keep cron, swap `coinbase_trader` internals)

| | |
|---|---|
| **Pros** | Smallest conceptual change |
| **Cons** | Fights long-lived node; still no reconciliation; starting a LiveNode per cron is **forbidden**; easy two writers |
| **Verdict** | Use only as a **transitional shim**: cron writes Intent, sidecar (A) executes. |

### Option D — Rewrite strategies as Nautilus `Strategy`

| | |
|---|---|
| **Pros** | Textbook NT research-to-live |
| **Cons** | Moves risk authority into NT; LGPL wraps UGFT; Kalshi/Alpaca missing; months of rewrite; stalls live Kalshi P&L; contradicts NORTH STAR |
| **Verdict** | **Reject.** IntentConsumer only. |

### Ranking

1. **A — Sidecar** (do this)
2. **C-as-shim** (cron → Intent → sidecar)
3. **B — Embed** — only if A is proven and IPC is the measured bottleneck
4. **D — Full rewrite** — do not

---

## 6. Migration phases (every phase ends in REAL trading under Valedia)

Paper/sandbox is an **engineering gate**, not a destination. Each phase’s exit criterion is **real-capability that compounds**, not “more paper.”

**Do not serialize Valedia profit work behind NT.** Handoff B1 (`sports_engine` fees + ledger + gate) is **higher immediate P&L leverage** than installing a wheel. NT phases run **beside** that, not instead of it.

### Phase 0 — Lock the seam (no venue I/O) — NEXT SLICE

- Intent schema + JSONL queue + fill sink **stubs**
- Exclusive venue lock in `valedia_config`
- Tests: Intent without gates is refused; size never rounds up; `source="paper"` / `sandbox` cannot open Kelly
- **Exit:** tests pass; no NT wheel required; no keys touched
- **Profit:** breakage guard so later Coinbase/HL cannot double-fill

### Phase 1 — OMS engineering gate (sandbox / backtest) — TEMPORARY

**LABEL: TEMPORARY ENGINEERING GATE. Not the product. Not “stay on paper.” Skip any extra week of paper that does not prove unknown-outcome / restart / no-double-Intent.**

- Pin `nautilus_trader==` a specific v2 RC/wheel in a **separate venv**
- Run NT `Sandbox` + Coinbase **data** client (market data only) **or** recorded fixtures
- IntentConsumer submits; matching engine fills; sink writes ledger with `source="nautilus_sandbox"` (exclude from `real_track_record()`; add `"sandbox"` to that exclusion)
- Prove: unknown-outcome handling, restart reconciliation, no double Intent, size never rounds up
- **Exit:** deterministic tests + one supervised sandbox session on NIMROD **without** exec API keys loaded
- **Forbidden:** loading Coinbase/Kalshi/Alpaca/HL **signing** keys into this venv
- **Profit:** only as insurance that live cutover will not lie to Kelly

### Phase 2 — Coinbase real execution under Valedia gates (first live subsumption)

**Why first:** official NT adapter; Valedia already trades it; fill-capture hole is a live compounding bug; rebalancer is dead capital; does **not** interrupt Kalshi.

- Sidecar on the host that satisfies Coinbase egress (VPS or SOCKS-capable)
- `coinbase_trader` order POSTs become **read-only** (balances/prices) + Intent producer
- `unified_brain` / rebalancer emit Coinbase Intents **only if** existing gates pass **and** `NAUTILUS_COINBASE_LIVE` is set by **Valedia runtime** (Burton-owned, not this chat)
- Fills → `record_entry(..., venue="coinbase", source="live")` — closes handoff B4 for crypto
- Rebalancer auto-executes **through Intent**, still under ERE/Kelly/fee-gate
- **Exit:** one real Coinbase fill (small, fee-positive) recorded in the ledger, Signal `Invested:` fired, exclusive-writer proven (old `POST /orders` path dead)

### Phase 3 — Hyperliquid (perps / shorts / futures) under the same Intent bus

**Why second:** NORTH STAR explicitly wants shorts and futures. This is **new profit surface**, not OMS completeness.

- NT official adapter (`crates/adapters/hyperliquid`)
- Steal HyperGrok **controls** (trade-only API wallet, one writer, expiresAfter, ticket id) — not the Grok-bot runtime
- Leverage remains **off** until Burton approval (`fund-rules.md`)
- Reduce-only / flatten must be expressible as Intents when ERE HALTs
- **Exit:** Valedia can express a gated perp Intent; NT is the only `/exchange` writer

### Phase 4 — Polymarket enablement (arb)

- Valedia already has scan + disabled adapter (`quant_engine.py`)
- NT has a real CLOB adapter
- Flip is a **Valedia policy** decision (non-sports access, wallet, geo), then NT executes
- **Exit:** Kalshi↔Polymarket arb can be **executed** on the Polymarket leg without a second homegrown client

### Phase 5 — Custom Kalshi adapter (unify the money printer last)

- Highest current live path is Kalshi; NT does not have it. **Migrating first would be elegance that risks the only live writer.**
- Build **out-of-tree** using `examples/live/_template` (Python) or independent Rust/PyO3 package
- License: original code, LGPL-compatible; **do not copy `vendors/kalshi-agent`**
- Cutover only after: recorded-book tests → shadow Intents alongside `master_allocator` (allocator still the writer) → exclusive cutover
- Sports engine stays Valedia **strategy**; NT becomes the order home
- **Exit:** `master_allocator` and `sports_engine` no longer call `create_order`; they emit Intents; Kalshi adapter is the only writer

### Phase 6 — Equities

- Keep `alpaca_executor.py` until an Alpaca adapter exists **or** Burton chooses IB
- Do not silently move Alpaca live flow to IB
- Fix `source="paper"` vs `IS_LIVE=True` **before** any NT work on equities (truth bug, not an OMS bug)

### Phase 7 — Decommission parallel writers

- `coinbase_trader` order POSTs gone
- Archived `hyperliquid_client.py` stays archived
- `rebalancer.py` executes via Intent or is deleted
- Watchdog knows sidecar health; VPS failover: stop NIMROD sidecar before start VPS sidecar
- **Exit:** `grep` for venue order POST in `scripts/integrations` shows only the sidecar adapter + Kalshi-until-Phase-5

**End state:** real multi-venue execution (crypto, perps/shorts, arb, then Kalshi), Valedia still the only brain, ledger is fee-inclusive and fill-reconciled, paper/sandbox used only in CI, compounding reads only live rows.

---

## 7. Risks

| Risk | Why it’s real | Mitigation |
|---|---|---|
| **Double-execution** | Allocator + sports + unified_brain + rebalancer + (future) NT all can POST | Exclusive venue lock; kill old writers before enabling sidecar live; watchdog asserts one writer |
| **P&L lie → bad compounding** | Sandbox/paper counted as edge; Coinbase `EXECUTED` without fill | `source` exclusions; fill sink refuses `live` unless Valedia flags; Coinbase `record_entry` |
| **LGPL Combined Work** | Embed PyO3 in Valedia | Prefer sidecar; never mix AGPL MiroFish |
| **kalshi-agent UNLICENSED** | Vendor tree sits next to live scripts | Clean-room only |
| **NT RiskEngine vs ERE** | Two “risk” modules | Floor vs authority (§4.4) |
| **Research elegance stall** | Months of NT Strategy rewrite / extra paper | Option D rejected; Phase 1 time-boxed; Kalshi stays live on Valedia |
| **Custody / keys** | NT adapters want signing keys; HL wallet can withdraw if mis-issued | Trade-only keys; sidecar does not load Mercury; no key changes from coding agents |
| **v2.0.0rc5 churn** | Still RC | Pin wheel; isolate venv |
| **No Kalshi/Alpaca adapters** | Live money is Kalshi-heavy | Don’t block Coinbase on Kalshi; don’t pretend Betfair = Kalshi |
| **Kraken exec WIP + read-only keys** | Surprise live if perms later added | Kraken data-only until Burton trade perms **and** exec adapter is non-WIP |
| **SOCKS / IP lock** | NT may egress home IP or fail | Sidecar on VPS or prove SOCKS per adapter |
| **Cron vs daemon failover** | Two LiveNodes | Heartbeat mutex; VPS unit disabled while NIMROD healthy |
| **Alpaca truth bug** | `IS_LIVE=True` but some paths log `paper` | Fix in Valedia **before** NT equities |
| **Sports engine ledger hole** | Live every 2h, fee-exclusive, own log | **Valedia B1 — do not wait on NT** |
| **Leverage on HL** | NORTH STAR wants shorts; fund rules forbid leverage w/o approval | Intents carry leverage=0 until Burton |
| **Trademark / fork** | `ADAPTERS.md`, `TRADEMARK.md` | Out-of-tree Kalshi adapter: don’t imply official NT |

---

## 8. Recommended next engineering slice

**Smallest useful code change toward a profit-capable live path. No live keys. No `--live`. No NT process. No wheel.**

Do **not** `pip install nautilus_trader` next. Installing NT and paper-trading is elegance. The profit-capable path is: **Valedia already-gated Intent → (later) one writer → fill in the real ledger.** Slice 0 builds that seam and the dual-write guard.

### Files (proposed)

| File | Action |
|---|---|
| `scripts/integrations/nautilus_intent.py` | `Intent` dataclass + `validate_intent()` + `enqueue_intent()` JSONL append-only at `models/nautilus_intent_queue.jsonl` |
| `scripts/integrations/nautilus_fill_sink.py` | `apply_fill(intent, fill)` → `forward_test_ledger.record_entry`; maps sandbox/live source; **refuses** to write `source="live"` unless Valedia flags say so |
| `scripts/integrations/valedia_config.py` | Add `VENUE_WRITER = {"kalshi":"valedia","coinbase":"valedia","alpaca":"valedia","kraken":"none","polymarket":"none","hyperliquid":"none"}` and `NAUTILUS_ENABLED = False` |
| `scripts/integrations/forward_test_ledger.py` | Treat `sandbox` / `nautilus_sandbox` like `paper` in `real_track_record()` / open-position counts |
| `scripts/integrations/test_nautilus_intent.py` | Unit tests (no network) |

### `validate_intent()` must fail if

1. `NAUTILUS_ENABLED` is False **and** caller asked for sidecar dispatch (queue may still record `status=held`)
2. Any of `ere_ok`, `fee_ok`, `category_ok`, `blacklist_ok` is missing or false
3. `exec_dry_run` is True → enqueue as `dry_run` only, never dispatchable
4. `max_notional` / `max_qty` missing
5. `qty` would exceed `max_qty` (no silent clip-up)
6. `venue` writer is not `nautilus` (prevents dual-write even in code)
7. `source=="live"` while `NAUTILUS_ENABLED` is False

### Explicitly out of this slice

- `pip install nautilus_trader`
- Starting `LiveNode`
- Changing `EXEC_DRY_RUN`, `IS_LIVE`, Alpaca/Coinbase/Kalshi keys
- Wiring `master_allocator` to enqueue (that is slice 2, after tests exist)
- Auto-executing `rebalancer.py`
- Copying kalshi-agent or HyperGrok execution code
- Un-archiving Hyperliquid

### Slice 2 (immediately after, still no live NT)

- Shadow Intents from the Coinbase branch of `master_allocator` (writer still `valedia`)
- Compare shadow Intent vs actual `coinbase_trader` POST args in logs
- Optional same-sprint **Valedia** profit (not NT): Coinbase/Alpaca `record_entry` with real fill or `UNRECONCILED` — today’s compounding hole, and the sink NT will use

### Parallel, do not block on NT (higher immediate profit)

Handoff B1: `sports_engine.py` fees → `fee_engine`, fills → `forward_test_ledger`, `should_trade_category`, never log DRY_RUN as executed. NT does not help this. Kalshi stays Valedia-native.

### Why this is the right first cut

It creates the subsumption boundary **inside Valedia**, where the gates already live, without a second writer or an LGPL binary. It is the opposite of “install Nautilus and paper-trade.” It is the breakage guard that lets Coinbase fills, rebalancer Intents, and later shorts/arb **compound** instead of double-firing.

---

## 9. What success looks like (real, not paper)

- Maximum **realized** risk-adjusted compounding is the metric. NT uptime is not.
- Coinbase (then HL shorts/futures, then Polymarket arb, then Kalshi) orders are sent by **one** Nautilus sidecar.
- Every order that sidecar sends is a Valedia Intent that already passed ERE, fee-gate, Kelly/fuses, category gate, blacklist, and the Valedia live flag.
- `forward_test_ledger.jsonl` is the only edge source; sandbox/paper rows never lift caps or the $1000 undeployed gate.
- Rebalancer maintains target crypto weights **through Intents**, not printouts.
- MiroFish remains a separate AGPL intelligence process at ≤10% until the accuracy ledger earns more.
- Signal still only says `Invested:` / `Profit:`.
- Burton can freeze money (Mercury card) and freeze the sidecar (launchd) independently.
- Coding agents still cannot `--live` or touch keys.
- New GitHub/OSS engines follow the same discover → vet → test → adversarial review → breakage-guard loop.

---

## 10. Pointers (absolute)

**Valedia**

- `/Users/burtonchatelain/.openclaw/workspace-v2/skills/valedia/{architecture,trading-engines,cron-schedule,doors,signal-protocol}.md`
- `/Users/burtonchatelain/.openclaw/workspace-v2/skills/trading/{INDEX.md,rules/fund-rules.md,intelligence/mirofish.md,platforms/{coinbase,alpaca}.md}`
- `/Users/burtonchatelain/.openclaw/workspace-v2/scripts/integrations/{valedia_config,execution_policy,forward_test_ledger,master_allocator,unified_brain,coinbase_trader,alpaca_executor,kraken_client,kalshi_executor,sports_engine,rebalancer,Etherion_Risk_Engine,kernel_predictor_v16e,fee_engine,kelly_sizing,latency_arb,mirofish_runner}.py`
- `/Users/burtonchatelain/.openclaw/workspace/recommendations/{RUFLO-MASTER-GOAL,CLAUDE-TO-RUFLO-HANDOFF,RUFLO-STATUS,GROK-BUILD-LANE}.md`

**NautilusTrader**

- `/Users/burtonchatelain/MemPalace/sources/repos/github/nautilus_trader/{README.md,LICENSE,ADAPTERS.md,MIGRATION_V2.md,version.json,Cargo.toml}`
- `docs/concepts/{overview,architecture,live,strategies}.md`
- `docs/concepts/execution/{policies,reconciliation}.md`
- `crates/{live,execution,data,risk,trading,adapters/{coinbase,kraken,hyperliquid,polymarket,sandbox}}/README.md`
- `python/nautilus_trader/` · `examples/live/_template/README.md`

---

*GOATWORTHY Post LLC — design only. Valedia remains sole live risk authority. Objective = realized compounding profit.*
