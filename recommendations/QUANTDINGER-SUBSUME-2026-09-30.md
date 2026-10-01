# QUANTDINGER-SUBSUME-2026-09-30 — OpenByteInc/QuantDinger → Valedia (patterns only)

**When:** 2026-09-30 ~00:20 PT (Mac Studio M1 `b3f456da-e4fc-42d6-8147-a47ad76a8982`)  
**Upstream:** https://github.com/OpenByteInc/QuantDinger  
**Clone (study-only):** `~/MemPalace/sources/repos/github/QuantDinger` @ `9e3095d` (v5.4.1, depth 1, tip 2026-09-29)  
**Inbox symlink:** `~/.openclaw/workspace-v2/vendors/inbox/QuantDinger` → MemPalace clone  
**License:** Backend **Apache-2.0**. Trademarks / name / logo **not** granted by Apache (`TRADEMARKS.md`). Frontend/mobile are separate source-available repos — **do not** vendor UI.  
**Mode:** PAPER/SHADOW study. **Not** installed as live OMS. No `docker compose up`, no broker keys into QD, no `--live`.  
**Executor:** Valedia parent completed stamp (grok-valedia available at `~/bin/grok-valedia`; implement slice deferred to Grok Build charter below).

---

## Improve + subsume?

| Question | Answer |
|----------|--------|
| Can Valedia **improve** by studying QuantDinger? | **YES** |
| Can Valedia **subsume** QuantDinger cleanly? | **YES — patterns / selective modules only** |
| Replace Valedia live OMS with QD stack? | **NO** |
| Run QD trading-worker / live adapters beside Valedia? | **NO** |
| Go / no-go for next Build slice | **GO** (paper/shadow pattern transfer) |

**Rationale:** QuantDinger is a full AI Trading OS (Flask+Gunicorn API, trading-worker, scheduler, Celery, Postgres, dual Redis, optional Prometheus). Valedia already owns live risk (Kalshi stage; Poly/CB/Alpaca fenced; crypto-paper shadow). QD’s unique value for Valedia is **research-grade paper/virtual accounting, walk-forward evolution stats, typed Jev pre-trade context, point-in-time universes, idempotent intents, and fee/fill honesty** — not a second write path.

---

## QuantDinger architecture (study notes)

```text
Clients (Web/H5/API/MCP)
  → Nginx → Flask API (validate + durable commands only)
  → PostgreSQL (state, leases, intents)
  → Redis cache | Redis jobs (separate)
  → trading-worker (strategy runtimes, broker sessions)  ← LIVE PATH — SKIP
  → scheduler-worker / celery-worker / celery-beat
  → optional Prometheus/Grafana
```

| Asset class | QD surface | Valedia today |
|-------------|------------|---------------|
| Crypto | Binance, OKX, Bitget, Bybit, Gate, HTX adapters + virtual accounts | crypto-paper (BTC/ETH allowlist), all-knowing movers scan, dynamic_universe, RH MCP read, CB fenced |
| Stocks | US/CN/HK data sources; Alpaca + IBKR live | Alpaca fenced; Yahoo/Finnhub ears |
| Forex / futures | data_sources forex/futures | dynamic_universe forex momentum only |
| Prediction markets | **none** | Kalshi LIVE (sports); Poly US DRY; weather/oil **FENCED** |
| AI gate | Jev System One Decision Context V2 + LLM fallback fail-open | `jev_shadow` seasoning on MiroFish (default off, fail-open) |
| Signals | Strategy API V2 intents → pending orders → broker **or** virtual fills | MiroFish → allocator; crypto_paper SMA intents; DaviddTech webhook paper |

**Execution path (QD):** signal → deterministic risk/budget → optional AI Decision Filter (Jev) → pending order queue → trading-worker → exchange adapter. Exits/SL/TP bypass AI. Grid/DCA/martingale excluded from Jev gate. Agent MCP requires `paper_only=false` **and** `AGENT_LIVE_TRADING_ENABLED=true` for live.

**Signal-only virtual accounts:** notification signals → internal virtual orders/fills/positions/PnL/equity. **Never** connects to a broker. Fixed **0.05% commission** + **0.05% adverse slippage** on notional; leverage not double-charged in commission. See `virtual_trading.py` + `virtual_execution_costs.py`.

**Risks in QD itself (do not import):** live create_order adapters; encrypted broker credential vault; USDT/Stripe billing + on-chain payment watchers; multi-tenant SaaS; `AGENT_LIVE_TRADING_ENABLED`; partner attribution; default admin password folklore in docs (installer already rejects weak pw — still never run QD production here).

---

## Diff vs Valedia today

| Capability | QuantDinger | Valedia | Subsume? |
|------------|-------------|---------|----------|
| Live crypto/stock OMS | Full adapters + workers | Kalshi-only live stage; CB/Alpaca writers fenced | **SKIP** (live writers) |
| Paper / virtual ledger | Virtual accounts 5bps+5bps | `crypto_paper` fee/slip stubs; FTL fee-true | **PATTERNS** — align virtual cost policy + equity curve shape |
| Strategy evolution | TPE/grid/random + walk-forward + PBO + Deflated Sharpe + block-bootstrap MC | Arena paper, backtest stubs, no PBO suite | **PATTERNS** — paper research module only |
| Jev pre-trade | Multi-question Decision Context V2 on **entries**; exits bypass; fail-open | `jev_shadow` MiroFish CIO seasoning; not on Kalshi execute | **PATTERNS** — deepen shadow questions; **never** hard-gate live Kalshi this slice |
| Universe | Point-in-time members (manual/watchlist/index/etf/mcap) | `dynamic_universe`, all-knowing movers, hot_list | **PATTERNS** — as-of membership / code normalize; keep oil/weather fences |
| Idempotent intents | `OrderIntentService` hashed keys | decision jsonl rows; Kalshi Create V2 idempotency elsewhere | **PATTERNS** — paper decision keys |
| Fee→quote normalize | `fee_quote.py` | `fee_engine`, `CRYPTO_FEE_PCT`, Kalshi fee map | **PATTERNS** — research helper |
| Fill accounting / recon | `fill_accounting`, `fill_evidence`, funding recon | Kalshi fill-true GET; crypto-paper mid±slip | **PATTERNS** — annotate only |
| MCP agent surface | Full QD MCP (incl. place_quick_order gated) | RH MCP, Gmail, Calendar, etc. — Valedia writers stay Valedia | **SKIP** installing QD MCP; **steal** dual paper_only + server kill-switch idea for docs |
| Event radar | SEC/Yahoo/Coindesk + Jev relevance | MiroFish, FOMO enrich, scanner | **PATTERNS** — ears only |
| Billing / USDT / Stripe | SaaS payments | n/a | **SKIP** |
| Docker hyperscale stack | Compose production overlays | Valedia scripts + LaunchAgents | **SKIP** (no second OMS) |
| Poly / Kalshi / weather | Absent | Core Valedia | Keep Valedia; do not route QD at PM venues |
| Laya / neural | Absent | Laya stub; MiroFish neural enrich | Keep; do not replace with QD AI authoring stack |

---

## Clean merge plan (no breakage)

**Principle:** Idea-transfer + thin Valedia modules. Reimplement under Apache-compatible original Valedia code. Do **not** import `live_trading/*` executors, usdt_payment, stripe, or run QD containers.

### Phase 0 — inventory (this stamp) ✅
- Clone + inbox symlink + remotes note + license/trademark fences recorded.

### Phase 1 — dossier + virtual-cost parity (Grok Build)
1. Write `recommendations/ADAPT-DOSSIERS/quantdinger.md` (short; link this stamp).
2. Add `scripts/integrations/quantdinger_virtual_costs.py` (or fold into `crypto_paper`) exposing **named** policy `commission_rate=0.0005`, `slippage_rate=0.0005`, `liquidity_role=taker`, **no** leverage double-charge — for **paper compare** against existing `CRYPTO_FEE_PCT` / slip. Default Valedia book **unchanged** unless a flag opts into QD-parity labels.
3. Offline tests: virtual fill open/add/reduce/close long+short math mirrors QD `calculate_virtual_fill` spirit (reimplemented, not pasted wholesale if avoidable; attribution comment OK).
4. `create_order=false` always. Kelly `counts_toward_kelly=false` for any QD-tagged paper rows.

### Phase 2 — evolution stats (paper research)
1. `scripts/integrations/strategy_evolution_paper.py`: walk-forward split helper + Deflated Sharpe / PBO **stubs or pure-numpy ports** fed by fixture equity curves (crypto-paper or Arena). No live data requirement for unit tests.
2. Output under `data/quantdinger-paper/evolution_*.json`. Never allocator execute.

### Phase 3 — Jev Decision Context deepen (shadow only)
1. Extend `jev_shadow` **optional** question pack (data_quality / signal_alignment / market_regime / risk_check / execution_quality) behind `VALEDIA_JEV=1` + new opt-in `VALEDIA_JEV_QD_CONTEXT=0` default.
2. Fail-open unchanged. **Must not** block Kalshi `create_order`. Exits/halt path never call Jev.
3. Stamp only; no score/Kelly mutation (same AUTONOMY-2 contract).

### Phase 4 — universe as-of + intent keys (optional thin)
1. Point-in-time member normalize helpers for `dynamic_universe` / all-knowing watch lists (market enum mapping to Valedia symbols; oil/weather still `NO_HARVEST`).
2. Idempotency key helper for paper decision jsonl (`source=quantdinger_paper`).

### Explicit non-goals (every phase)
- `docker compose` / install.sh / GHCR stack
- Import or call QD `live_trading` create/cancel/withdraw
- Alpaca/IBKR/OKX/Binance writers from QD
- USDT/Stripe/billing/wallet watchers; bip-utils payment flows
- Setting `AGENT_LIVE_TRADING_ENABLED` or QD `paper_only=false`
- Replacing Kalshi/Poly as venues; RH Instant; CB Instant; treasury auto
- `--live`, `NAUTILUS_LIVE`, `MIROFISH_AUTO_RAISE`, weather/oil unfence
- Rebranding Valedia as QuantDinger (trademark fence)
- Crontab install; allocator platform=quantdinger

---

## Breakage risks

| Risk | Mitigation |
|------|------------|
| Accidental second live OMS | Never start QD trading-worker; no Compose; inbox = study symlink only |
| Fee table confusion vs crypto-paper | QD-parity rates are **labeled compare**; default book keeps `CRYPTO_FEE_PCT` |
| Jev hard-gate creeps onto Kalshi | Keep AUTONOMY-2: seasoning only; exits bypass; fail-open; no executor import |
| GPL/trademark bleed | Backend Apache-2.0 OK for patterns; no QD logos/name in Valedia product UI; cite upstream in dossier |
| Dependency bloat (Flask/Celery/Postgres) | Do not vendor backend; reimplement tiny helpers |
| Credential / payment code in tree | Do not copy `usdt_payment`, stripe, credential encryption paths |
| Universe widen → oil/weather harvest | Ranker fences stay; `NO_HARVEST` for oil; weather watch-only |
| Idempotency key collision with Kalshi | Namespace `qdpaper:` prefix; separate jsonl dir |

---

## Fences held

- **PAPER/SHADOW only** — study clone; no QD live runtime
- **No `--live`**, no `NAUTILUS_LIVE`
- **Oil fenced** (`NO_HARVEST` / `LIVE_EXEC_FENCED_SERIES`)
- **No RH / CB Instant** moves; no wallet / ACH / treasury auto
- **No `MIROFISH_AUTO_RAISE`**
- Valedia runtime remains **sole live risk** (Kalshi stage per `PROFIT-PATHS-INVENTORY.md` / `KILL-SWITCH.md`)
- Clone under MemPalace + `vendors/inbox` is **read-only study** — not on PYTHONPATH for writers

---

## Suggested Grok Build charter (next implement)

```text
grok-valedia "QUANTDINGER-SUBSUME Phase 1 — PAPER/SHADOW only.
Read recommendations/QUANTDINGER-SUBSUME-2026-09-30.md.
Clone already at ~/MemPalace/sources/repos/github/QuantDinger (inbox symlink).
DO: ADAPT-DOSSIERS/quantdinger.md + quantdinger_virtual_costs helper (or crypto_paper opt-in label)
     + offline tests + data/quantdinger-paper/ fixture decision with create_order=false
     + Ponytail + GROK-STATUS. Use RuFlo :8810 + codebase-memory.
DO NOT: docker/compose, live_trading import, usdt/stripe, AGENT_LIVE, --live,
        RH/CB Instant, MIROFISH_AUTO_RAISE, oil/weather unfence, allocator execute,
        crontab, trademark rebrand. GROK_EXIT:0 when tests green."
```

Optional follow-on slices (Burton queue after Phase 1 green): Phase 2 evolution stats → Phase 3 Jev QD-context opt-in → Phase 4 universe/intent helpers.

---

## Paths

| Item | Path |
|------|------|
| Study clone | `/Users/burtonchatelain/MemPalace/sources/repos/github/QuantDinger` |
| Inbox | `/Users/burtonchatelain/.openclaw/workspace-v2/vendors/inbox/QuantDinger` |
| This stamp | `/Users/burtonchatelain/.openclaw/workspace-v2/recommendations/QUANTDINGER-SUBSUME-2026-09-30.md` |
| Remotes note | `~/MemPalace/sources/repos/github/GITHUB-REMOTES.md` (append 2026-09-30) |

## Status

**GO** for patterns-only paper/shadow subsume. **NO-GO** as live OMS. Phase 0 done. Phase 1 done 2026-09-30 00:34 PT (`GROK_EXIT:0`): `recommendations/ADAPT-DOSSIERS/quantdinger.md`, `scripts/integrations/quantdinger_virtual_costs.py`, `data/quantdinger-paper/decision.json` (`create_order` false). Default book still `CRYPTO_FEE_PCT`. Next = Burton. Phase 2 evolution stats only if queued. Do not start it from this slice.
