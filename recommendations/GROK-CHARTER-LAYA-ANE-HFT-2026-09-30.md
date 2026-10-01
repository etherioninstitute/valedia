# GROK-CHARTER — Laya/ANE HFT-shaped hot path (paper/shadow)

**Date:** 2026-09-30 ~00:45 PT  
**Executor:** Grok Build via `grok-valedia`  
**Worktree:** `~/.openclaw/workspace-v2`  
**North star:** PROFIT FIRST — a trading machine that is *informed* (MiroFish / news / all-movers) and *fast where it matters* (deterministic code + ANE Laya), paper/shadow until Valedia runtime unlock.

## Intent (Burton)

Implement ANE/Laya correctly for an HFT-*shaped* path: informed by MiroFish/News/etc., covering ALL possible venues/items (stocks, crypto, predictions), and decide whether the smaller Ornith model (`ornith-1.5-35b-a3b`) belongs in Valedia.

### Reality fence (do not overclaim)

- Retail RH / Coinbase / Kalshi / Poly are **not** colocated HFT. Latency target = "deterministic hot path on Studio ANE," not exchange matching engines.
- **Code owns the money path.** Laya = typed micro-decisions on ANE (shadow). Jev / MiroFish / Ornith / Qwen = seasoning / informed layer — never OMS authority.
- No `--live`, no venue orders, `LAYA_DECIDE_ENFORCEMENT=none`, `create_order=false` everywhere this slice touches.

## Already present

| Piece | Path / note |
|-------|-------------|
| Laya service | `~/MemPalace/sources/tools/laya-decide-service` — **port 8766** (glance owns 8765) |
| Core ML venv | `/Volumes/NIMROD_SSD/venvs/laya-coreml` |
| Weights | `/Volumes/NIMROD_SSD/models/laya-coreml/laya-multilingual-coreml` |
| Pack today | `packs/shadow-v1.json` (needs_web_search / risk_tier / tool_side_effect) |
| Valedia stub | `scripts/integrations/all_knowing_movers_scan.py` → `laya_shadow_decide` (`stub_no_coreml`) |
| Fence doc | `recommendations/VALEDIA-LAYA-FENCED.md` (was reference-only; this charter is the go for **shadow wire**) |
| All-movers | fee-true rank + paper would-have (crypto/stocks/ETFs/weather watch) |
| QuantDinger P1 | DONE — virtual costs helper; do not re-do |

## DO this slice

1. **Bring Laya up** with `~/MemPalace/sources/tools/laya-decide-service/scripts/start.sh`. Prove `GET http://127.0.0.1:8766/health` shows model/cache ready, `mode=shadow`, `enforcement=none`. Never bind 8765.
2. **Trading decide pack** `packs/valedia-paper-v1.json` (or extend shadow) with questions suited to paper admit, e.g. `admit_paper` (noul), `edge_quality` (score), `venue_class` (choice: crypto|equity|prediction|weather_watch|oil_blocked). Keep shadow/enforcement none.
3. **Hot-path client** in workspace-v2: `scripts/integrations/laya_hotpath_client.py`
   - HTTP to `127.0.0.1:8766` only
   - Fail-open: if down/timeout → stub label + continue (same as Jev fail-open)
   - Compact non-secret `state` (symbol, class, chg%, fee-true edge, impact$, informed flags)
   - Unit tests with mocked HTTP + optional live health probe gated offline-safe
4. **Replace stub** in `all_knowing_movers_scan.py`: call real client when `VALEDIA_LAYA=1` (default **off** until proven; document how to enable). Stamp must show `laya.backend=coreml_http` when on, else stub.
5. **Informed layer (slow path, not inside ANE loop):** attach MiroFish/news/watch flags into Laya `state` when present; never block hot path waiting on LLM. If MiroFish artifact missing, proceed with tape-only.
6. **HFT-shaped paper sniper proof** (one command): tape → fee-true gate → Laya shadow decide → paper would-have rows with `create_order=false`. Cover at least crypto + one equity/ETF + one prediction/weather watch symbol in the proof table.
7. **Smaller Ornith (`ornith-1.5-35b-a3b`):** write a short GO/NO-GO in the stamp — recommend **seasoning / paper ticks / light Hermes helper only** if latency/VRAM helps; **NO** money path, **NO** replace qwen3.8-27b desk ctx, **NO** LMS auto-unload. Do not load/unload LMS models unless already loaded; read-only probe is enough.
8. **Stamp:** `recommendations/LAYA-ANE-HFT-HOTPATH-2026-09-30.md` — architecture, enable flags, one-scan command, proof table (health + decide latency ms + sample admits), fences held, Ornith verdict.
9. Update `VALEDIA-LAYA-FENCED.md` status → shadow-wired (still not live authority). Update `GROK-STATUS.md`. Ponytail after code. RuFlo `:8810` + codebase-memory.
10. Tests green. Print `GROK_EXIT:0`.

## DO NOT

- `--live`, RH/CB/Kraken/Alpaca/Kalshi/Poly orders, Instant, wallets
- `LAYA_DECIDE_ENFORCEMENT` anything but `none`
- Bind Laya to `:8765` or kill glance
- `MIROFISH_AUTO_RAISE`, `CRYPTO_PAPER_SAFETY` bypass, widen live `ALLOWLIST`
- Oil harvest (NO_HARVEST), weather live admit without existing INFORMED gate
- Docker QuantDinger, touch `ot-valedia-desk` / `valedia-glance`
- Unload/reload LMS Ornith/Qwen without Burton same-turn yes
- Claim true exchange HFT latency in user-facing copy — say "HFT-shaped / ANE hot path"

## Success criteria

- Laya health 8766 OK with Core ML
- Valedia can call ANE Laya from movers scan when `VALEDIA_LAYA=1`
- Fail-open proven (service stop → stub continues)
- Paper sniper proof table stamped; `create_order` always false
- Ornith-1.5 GO/NO-GO written
- `GROK_EXIT:0`
