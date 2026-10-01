# H1 dossier — OpenTerminal

**Path:** `/Users/burtonchatelain/MemPalace/sources/repos/github/OpenTerminal`  
**License:** MIT (Copyright 2026 OpenTerminal contributors)  
**Stack:** Next.js + Express + TypeScript; `web/` widgets + `server/` API  
**Slice:** H4 (read-only Valedia cockpit). Sibling of NT-3 in `VALEDIA-NEURAL-TRADER-FENCED.md`.

---

## Identity

Keyboard-first, widget-grid market terminal on **free public data** (no paid keys). Dark, dense, `react-grid-layout`, ⌘K palette, lightweight-charts, news RSS, screener, heatmap, options chain, crypto board, macro (UST/VIX), local SQLite **portfolio log**, optional Claude **AI assistant**.

Grep of `web/` + `server/`: **no `place_order` / venue create / WebSocket**. Quotes poll (1s). Portfolio buy/sell is a **local SQLite journal** (`transactions.side IN ('BUY','SELL')`), not an exchange. Next.js proxy currently forwards POST/PUT/PATCH/DELETE — H4 Valedia routes must be **GET-only**. Public Binance orderbook route exists; no widget calls it; do not treat as Valedia book.

---

## Architecture

```
web/components/widgets/*  ←  server/src  ←  public Yahoo/RSS/etc + fallbacks
        │
        ├─ Quote / Chart / Watchlist / News / Macro / Crypto / Heatmap / Screener
        ├─ PortfolioWidget (local SQLite P&L)
        └─ AiWidget → POST /api/ai/chat  (Claude; optional)
```

Layout persisted locally. Quotes refresh ~1s. Fallback chains so one dead provider doesn’t blank the board.

---

## STEAL

1. **Widget grid + local layout** — Valedia panel as one more widget family, not a rewrite of the terminal.
2. **Read-only quote/chart/news/macro** for Burton’s eye; data already in Valedia models/seed.
3. **Command palette** to jump: version, FTL, MiroFish, shadow counts, neural, personas.
4. **Fallback chains** — same idea as `mirofish_seed_accurate.py` independent sections.
5. **Flash-on-change** for fee-true P&L / UNRECONCILED (truthful labels, Slice 21+).
6. **Keyboard-first** — Signal is the alert channel; terminal is the glance surface.

H4 panel contents (from NT charter):

| Panel | Source of truth |
|-------|-----------------|
| Version / sha / dirty | `valedia_config.version_stamp()` |
| Mode | exactly one of LIVE / DRY / SHADOW / FENCED |
| FTL | fee-true only; UNRECONCILED ≠ Profit |
| Shadow Intent counts | `nautilus_intent` queue, `dispatchable=false` |
| MiroFish weight + accuracy | `effective_mirofish_weight()` + ledger summary |
| Neural | `mirofish_neural_enrichment.json` fed / idle |
| Personas (after H2) | `persona_lens_scores.json` |

---

## LEAVE

| Leave | Why |
|-------|-----|
| `AiWidget` / `/api/ai/chat` Claude | Spends Anthropic; NT charter: OpenTerminal assistant **off**; local LMS if ever |
| Wiring PortfolioWidget “buy” to Valedia writers | Local log ≠ OMS. Never a send button |
| Paid/reverse-engineered endpoints as live risk inputs | Research UI only; fusion stays on Valedia APIs |
| Options chain as a new live strategy | Not a profit leak-fix |
| Vendoring the whole Next app into cron | Cockpit is optional; brain does not need a browser |

---

## Valedia touchpoints

- `scripts/integrations/valedia_config.py` — version + fences  
- `forward_test_ledger.py` — Profit:  
- `mirofish_accuracy_ledger.py` + `effective_mirofish_weight()`  
- `models/mirofish_signal.json`, `mirofish_neural_enrichment.json`, `persona_lens_scores.json`  
- `nautilus_intent_queue.jsonl` — counts only  
- **No** `VENUE_WRITER`, `create_order`, allocator POST

---

## Risks

- UI that *looks* like a blotter invites a “just send it” button. H4 acceptance: **zero POST to venues**.
- Stale JSON vs live cron — stamp `ts` + version on every panel.
- Claude assistant leaking Burton IP / ledger off-box.
- MIT is fine; don’t rebrand their screenshots as Valedia product.

---

## Seamless hook

OpenTerminal **reads** the one brain; it never becomes a second one:

```
Valedia runtime writes models/*.json + FTL
        ▲  poll / file watch (read-only)
OpenTerminal Valedia widgets
        ✕  no order route
```

Same files MiroFish, neural, personas, and gatherer already use. No extra ledger.

---

## H4-ready

Thin Valedia workspace: 4–6 widgets bound to existing JSON. Disable AiWidget. Portfolio widget may *display* FTL, not accept buys. Ponytail: don’t fork the terminal; add a `web/components/widgets/ValediaWidget.tsx` + a tiny `/api/valedia/status` that only `read`s files.
