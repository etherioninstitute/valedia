# Grok Build next queue (Burton 2026-09-18 ~23:47 PT)

## Order (do not reorder)
1. **MIROFISH-SMOKE** — done `0.37.0` / `5b49cc1`
2. **PONYTAIL-FULL** — done (report-only, `PONYTAIL-AUDIT-VALEDIA-FULL.md`, version stays 0.37.0)
3. **VALEDIA-FULL-SMOKE** — done `0.38.0` (`VALEDIA-FULL-SMOKE.md` PASS, wall 3.763s)
4. **KILL-SWITCH** — done `0.39.0` / `4b7b6d3` (`KILL-SWITCH.md`, `VALEDIA_TRADING_HALT`)
5. **OPENTERMINAL-VALEDIA (Grok Build only)** — done `0.40.0` / `3327adc` (`OPENTERMINAL-VALEDIA.md`, read-only glance :8765)
6. **Staged ON** — Kalshi-only already live (Burton 2026-09-19); Poly still dry.
7. **POLY-LEARN** — done `0.41.0` (`POLY-LEARN.md`, `PROFIT-PATHS-INVENTORY.md`; no POLYMARKET_US_LIVE flip).
7b. **SCAN-ACCURACY** — done `0.42.0` (honest Signal scan, oil ticker-family fence, Kalshi per-venue deployable).
7c. **KALSHI-ORDERS-V2** — done `0.43.0` (Create/Cancel Order V2; yes/no → bid/ask). Next = Burton re-run live allocator.
7d. **MIROFISH-BIONIC** — done `0.44.0` (Bionic `:1234` primary, Ollama `:11435` fallback). See `MIROFISH-BIONIC.md`.
7e. **KALSHI-FEE-MAP** — done `0.45.0` (series maker/taker map, `fee(P,C,M)`, consult gate default off). See `KALSHI-FEE-MAP.md`.
7f. **AUTONOMY-1 FEE-MAP-REFRESH** — done `0.46.0` (GET refresh + artifact + gate checklist; gate still False). See `KALSHI-FEE-MAP-GATE-CHECKLIST.md`. Next = Burton flip **or** AUTONOMY-2 MIROFISH-JEV-SHADOW.
7g. **CRYPTO-PAPER-1** — done `0.47.0` (paper/shadow crypto simulation ledger; fixture + optional public klines; no wallets/orders). See `CRYPTO-PAPER-DESK.md`.
7h. **PROFIT-1 EV-PREFLIGHT + MAKER-FIRST + FILL-LEDGER** — done `0.48.0`. See `PROFIT-1-EV-PREFLIGHT.md`. Gates default OFF.
7i. **CONTROLS-READOUT** — done `0.49.0`. See `CONTROLS-READOUT.md`. Snapshot + cockpit `/status.controls` + HTML + CLI. No live flips.
7j. **AUTONOMY-2 MIROFISH-JEV-SHADOW** — done `0.50.0`. See `AUTONOMY-2-JEV-SHADOW.md`. `VALEDIA_JEV` stays False.
7k. **POLY-LOOP** — done `0.51.0` (folded into 0.52.0 commit). See `POLY-LOOP.md`. Desk loop on POLY-LEARN; `POLYMARKET_US_LIVE` stays False.
7l. **POLY-LOOP-UNIVERSE** — done `0.52.0`. Universe filter + field map + cache FV + skip_reasons. Next = Burton (`KALSHI_FEE_MAP_GATE` runtime flip). Do not auto-flip Poly live.
8. Wider venue ON — Burton go only.

## SCAN-ACCURACY (done 0.42.0)
- Signal `opportunity_report`: no invented 100%+ ROI; extra-capital block deleted; fee-true sim, picks compete.
- Oil live fence by ticker family (`KXWTI`/`KXOIL`/`WTI`) even when strategy is `NO_HARVEST`.
- Kalshi deployable per-venue; CB/Alpaca writer `none` cannot zero Kalshi cash.
- Next = Burton. Do not unfence weather/oil. Do not restore writers.

## POLY-LEARN (done 0.41.0)
- Paper classroom: market-by-slug + BBO/book, `poly_learn.py` → `models/poly_us_proposals.json` **DRY/SHADOW**. Allocator execute never `create_order`.
- Preview still works (`poly_us_smoke`). Create still `LiveWriteBlocked`. `POLYMARKET_US_LIVE` stays False.
- Inventory: `recommendations/PROFIT-PATHS-INVENTORY.md`. Next = Burton (Kalshi stays ON; Poly live still STOP).

## OPENTERMINAL-VALEDIA (done 0.40.0)
- Glance: `python3 scripts/integrations/valedia_cockpit.py --listen` → `http://127.0.0.1:8765/` (JSON `/status`)
- Did **not** vendor OpenTerminal Next (`:3000` / API `:4010` left alone). Docs: `OPENTERMINAL-VALEDIA.md`
- Broker vs fee-true FTL (n=0 said plainly). Halt visible; ears stay gathering. No send/buy/create_order buttons.

## Notes
- Live OpenTerminal market terminal may already be at localhost:3000 (API :4010); Valedia widgets are the Build deliverable.
- Signal P&L remains truthful broker tracker; UI must not pretend FTL when n=0.

7c. **KALSHI-ORDERS-V2** — done `0.43.0`. Next = Burton re-run live allocator.
7d. **MIROFISH-BIONIC** — done `0.44.0` (Bionic `:1234` primary, Ollama `:11435` fallback; OpenAI embeddings). See `MIROFISH-BIONIC.md` Results.
