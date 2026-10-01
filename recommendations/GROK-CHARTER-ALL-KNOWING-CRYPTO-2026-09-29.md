# GROK CHARTER — ALL-KNOWING CRYPTO / MOVERS FUSION (PAPER/SHADOW)

**When:** 2026-09-29 ~23:57 PT  
**Executor:** Grok Build (`grok-valedia` → screen `valedia-grok`)  
**Worktree:** `~/.openclaw/workspace-v2`  
**Bankroll reference:** ~$500 paper (match TC-AUTO `$504` freely-tradeable cash convention)

## NORTH STAR (Burton steering)

**PROFIT FIRST.** Rank all movers (crypto + stocks/ETFs + weather-as-watch) by **fee-true expected edge** and **paper $500 bankroll impact**. Watch everything; **decide/size only** names that would have printed like **PUMP/QNT ~16% day**. Coverage without edge is secondary. PAPER/SHADOW fences held.

## Inventory gaps (do not rediscover — fix/fuse)

| Piece | Today | Gap |
|-------|--------|-----|
| `dynamic_universe.py` | CoinGecko/buzz → `hot_list.json` WATCH | Writes to **old** `~/.openclaw/workspace/models/` not workspace-v2; not fed into crypto-paper; stale hot_list (LSK/VTHO/…) |
| MiroFish `models/mirofish_signal.json` | BTC/SOL/XRP HOLD only | Movers like PUMP/QNT never appear |
| `crypto_paper` ALLOWLIST | BTC-USDT, ETH-USDT only | Cannot paper-size discovered movers |
| Jev (`jev_shadow.py`) | Annotate-only on MiroFish report path; fail-open | Not seasoning discovery movers |
| Laya | `VALEDIA-LAYA-FENCED.md` reference-only | Shadow decide stub OK; do NOT pip-install CoreML or put on critical path |
| FOMO radar | enrich/stamp only | Not a decide/size authority |
| Oil | `NO_HARVEST` / fenced | **Stay fenced** |
| Weather | shadow desks exist | Watch-rank only in this slice; no live weather unfence |

## Ship (thin fusion — YAGNI)

1. **Discovery adapter** — reuse `dynamic_universe` (fix MODELS_DIR → workspace-v2 `models/` if needed). Emit ranked movers with 24h % / buzz / class. Prefer real CoinGecko gainers so **PUMP** / **QNT** can appear when they are movers; stocks/ETFs via existing yahoo/finnhub paths; weather names stay WATCH-tagged only.
2. **Edge ranker (PROFIT FIRST)** — fee-true expected edge vs paper book: move% − (fee+slip)*roundtrip, notional clip ≤10% of ~$500, rank by would-have $ impact. Drop / demote low-edge noise even if "interesting".
3. **Jev annotate** — call existing `jev_shadow` fail-open path (or stamp `jev.fail_open` / annotate stub when `VALEDIA_JEV` off). **Do not** change score/Kelly/OMS. Seasoning only.
4. **Laya shadow decide** — reference-only local stub (no CoreML install). Output shadow action: `watch` / `would_buy` / `would_pass`. Never create_order.
5. **crypto-paper would-have sizing** — simulate size vs ~$500 bankroll for top edge names **without** widening live ALLOWLIST for venue orders. Keep `CRYPTO_PAPER_SAFETY` intact. Prefer a **new** fusion module + optional widen of a **paper-only watch** file/artifact — do **not** enable MIROFISH_AUTO_RAISE, do **not** bypass safety, do **not** place RH/CB/Kraken/Alpaca orders, no `--live`.
6. **CLI one-scan** — e.g. `python3 scripts/integrations/all_knowing_movers_scan.py` (name flexible) → writes JSON under `data/` + prints top table.
7. **Stamp** — `recommendations/ALL-KNOWING-CRYPTO-UNIVERSE-2026-09-29.md` with: what ships, how to run one scan, proof (sample top movers **including whether PUMP/QNT appear**), fences held, fee-true edge ranking note.
8. **Tests** — thin unit/smoke; offline fixture if network flakes. Ponytail audit short.
9. **Done gates** — MemPalace note if CLI available; codebase-memory `search_graph` / index touch if MCP up; update `recommendations/GROK-STATUS.md`; print **`GROK_EXIT:0`** on success else `GROK_EXIT:1`.

## Hard fences (must hold)

- No live venue orders (RH / Coinbase / Kraken / Alpaca / Kalshi create / Poly create)
- No `--live`, no `NAUTILUS_LIVE`, no wallet keys
- No `MIROFISH_AUTO_RAISE`
- No `CRYPTO_PAPER_SAFETY` bypass / Ornith skip-to-fill
- Oil stays `NO_HARVEST` / fenced; weather not live-admitted
- Laya remains fenced reference (stub decide only)
- Jev annotate-only (not OMS)
- Do not touch screens `ot-valedia-desk` / `valedia-glance`
- Do not install crontab unless Burton named it (he did not)

## Tools (accuracy bar)

- RuFlo `:8810` if healthy (skip hung hooks)
- codebase-memory MCP for nav
- Ponytail after code
- Prefer evidence from this codebase over inventing parallel stacks

## Acceptance

- One command runs a paper/shadow scan and ranks by fee-true edge / $500 impact
- Stamp proves movers table (PUMP/QNT present or explicitly absent with reason)
- Fences listed and held
- `GROK_EXIT:0`
