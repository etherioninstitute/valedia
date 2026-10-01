# PHASE-1 — Kalshi create quality (merged Omni-Brain REVISE)

**Burton 2026-09-22 ~02:10 PT.** Queue after VALEDIA-TRADING-SMOKE. Then Phase 2.

**NORTH STAR:** informed → fee-true EV → size → FTL. MiroFish seasons only (refuse default-50 creates).

**Consults:** Finance Scout + LLM Stack REVISE Omni-Brain — keep this path; reject Laya sniper / Jev instant-buy / Omni forks as OMS.

## Goal
Close gaps so every Kalshi `create_order` / live execute is fail-closed on quality under **current runtime truth** (env already live):
- `POLYMARKET_US_HTTP=1`, `POLYMARKET_US_LIVE=1` (writer polymarket still **none**)
- `KALSHI_EV_PREFLIGHT_GATE=1`, `KALSHI_MAKER_FIRST=1`
- Informed-before-create on (0.57.0)

## Scope (do)
1. Audit create path vs INFORMED-BEFORE-CREATE + PROFIT-1 + fee-map gate — fix any bypass / soft fail / default-50 MiroFish fusion create.
2. Fee-true EV: model_edge − fee(P,C,M) − half_spread − slip ≥ floor; maker-first when maker_viable; higher hurdle for taker / dome near 50¢.
3. Fill-quality ledger: wire demote/refuse (or size demote) when ledger has evidence; honest empty-ledger behavior.
4. Fee-map gate: series must be present; refresh path healthy; controls_snapshot honesty for all gates (const vs env).
5. Tests + re-run relevant smoke / unit paths. No mass create_order. Optional documented qty=1 only if informed would pass AND you ask Burton first — default **no live create** in this Build.
6. Ponytail audit + GROK-STATUS. Version bump only if package changes.

## Out of scope
- session_clock / resolve_clock (Phase 2)
- Oil/weather unfence; CB/Alpaca writers; wallets/ACH/treasury auto
- Poly writer restore; mass Poly create
- Omni forks (Hummingbot/AgentTradeX/Lumibot) as engines
- Laya critical path; Jev as latency sniper

## Hard fence
- Do not kill `ot-valedia-desk` / `valedia-glance`
- Do not commit `POLYMARKET_US_LIVE=True` to git
- Do not point MiroFish at Ollama
- Do not auto-start Phase 2 until this ships with Next pointing at Phase 2 charter

## Tools
RuFlo MCP `http://127.0.0.1:8810/mcp` filtered ADR-035. CBM. Ponytail after code.

## Deliverables
1. Code/tests closing create-quality gaps
2. `PONYTAIL-AUDIT-PHASE-1-KALSHI-CREATE-QUALITY.md`
3. Results appended here + `GROK-STATUS.md`
4. **Next = Phase 2** — tell parent to launch `PHASE-2-SESSION-RESOLVE-CLOCKS.md` (do not auto-launch yourself if screen busy; set Next clearly)

## Done when
Kalshi create refuses uninformed / fee-blind / ledger-demoted / missing fee-map series under LIVE env truth; controls honest; audit written; Next=Phase 2.

## Results (2026-09-22 PT)

**Status:** DONE. **Next = Phase 2.** Parent launches `PHASE-2-SESSION-RESOLVE-CLOCKS.md`. This session did not start it.

Version `0.58.0` (git constant bumped; not committed, not tagged). Tip before the edit `10b24aa`.

Create path (`kalshi_auth.create_order` / `sell_position` → `informed_before_create`):

- Missing bid/ask or slip refuses (`spread_missing`, `slip_missing`). No zero-fill.
- MiroFish fusion with score omitted or 50 refuses. A model FV with a side score of 50 still admits.
- Fee-true floor is `edge − fee(P,C,M) − half_spread − slip`. Maker prefers when viable. Taker keeps the hurdle. Prices inside 40–60¢ add `dome_extra` (2¢ at 50¢).
- Ledger evidence (`n≥5` and `score≥0.03`) refuses on create even when `KALSHI_FILL_LEDGER_DEMOTE` is off. Empty or missing file does not refuse (`score` is null, not 0). Annotate-skip stays flag-gated.
- No injected `fmap`: series must be in a healthy `mode=get` artifact or the create is `fee_map_refresh` / `fee_map_missing`. Gate-on still drops unknown and taker-taxed.
- Executor and allocator no longer swallow fee/EV errors and continue. A refused maker place is `FAILED`, not `MAKER_PLACED`.

Controls: `KALSHI_FEE_MAP_REFRESH` source `file`. `POLYMARKET_US_HTTP` source `env` when set, else `const`. Git flags stay False. `POLYMARKET_US_LIVE` not committed True.

Tests PASS: informed, profit, fee-map, orders v2, kill switch, controls. No live create. Oil/weather fence unchanged. Screens not killed.

Audit: `PONYTAIL-AUDIT-PHASE-1-KALSHI-CREATE-QUALITY.md`.

