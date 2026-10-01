# ETORO-SUBSUME-1 — eToro Public API → Valedia research + paper/shadow desk

**Burton:** 2026-09-22 ~23:57 PT — implement eToro CopyTrader / Smart Portfolios / other readable API data into Valedia wherever it fits.  
**Keys:** already in `~/.openclaw/.env` as `ETORO_API_KEY` (x-api-key / public), `ETORO_USER_KEY` (x-user-key / private), aliases `ETORO_PUBLIC_KEY` / `ETORO_PRIVATE_KEY`. **`ETORO_LIVE=0`**, **`ETORO_MODE=research`**. Backup: `.env.bak-pre-etoro-20260922`.  
**Do not** print, log, or commit key values. Load from env / ProtonDrive paths only if re-sync needed.  
**Scout map:** eToro Scout `etoro-copytrader-api` + this charter. Docs: https://api-portal.etoro.com/ · llms.txt

## Auth smoke (already green on Mac)
- `GET /api/v1/me` → 200 (gcid/realCid/demoCid present)
- Scopes on this key include **trade.real:write** and **crypto:write** — treat as capability only; **Valedia must refuse all write/copy/order/money** while `ETORO_LIVE!=1`
- `GET /api/v2/portfolios/rankings?period=OneYearAgo&sort=-copiers&page=1&pageSize=5&popularInvestor=true` → 200
- Market data search + instrument-types → 200
- `GET /api/v1/portfolios/search` needs required `Type` (`trader` | `smart-portfolio`) — verify casing in code

## Goal
Wire eToro into Valedia as a **first-class research + paper/shadow venue**:
1. Client module that auth-headers every call (`x-api-key`, `x-user-key`, unique `x-request-id` UUID).
2. CopyTrader rankings + people/search discovery → scored candidates → jsonl decisions (would-copy / review_only / refuse).
3. Smart Portfolio search + deepen stats where read endpoints allow.
4. Market-data helpers Valedia can reuse (instrument search/types) without becoming a second live risk brain.
5. Hard gate: **no** start/adjust/close copy, **no** real/demo order create/cancel, **no** money/crypto withdraw — unless `ETORO_LIVE=1` **and** Burton-documented unlock (default off; this slice never sets live).


## STEER (2026-09-22 23:57 PT) — Burton clarification
**Primary job now:** eToro is **intel/data only** for Valedia’s existing trading (Kalshi / Poly / crypto-paper shadow). Use rankings, Smart Portfolios, people/stats, market-data to **inform** FV/edge/allowlists — do **not** treat eToro as a live execution venue in this slice.

**Keep the door open:** Implement a clean **stubbed live surface** (functions/modules for start/adjust/close copy + order create/cancel) that:
- is unreachable unless `ETORO_LIVE=1` **and** an explicit unlock path documented
- raises/refuses with clear codes when live is off
- never half-fires (no real copy with simulated hedge)
- does not call write endpoints in tests or dry-run

Paper/shadow “would-copy” may log hypotheticals for research; it must not allocate funds. Enrich Valedia decision pipelines where natural (e.g. annotate candidates, Controls connection line, optional jsonl consumers).

## Scope (do)
1. **Client:** `scripts/integrations/etoro_client.py` (or package-fit path) — load keys from env; refuse if missing; rate-aware (~60/60s shared); never log secrets.
2. **Desk module:** `scripts/integrations/etoro_copy_paper.py` (name fit layout) that:
   - pulls rankings (`/api/v2/portfolios/rankings`) with configurable period/sort/filters
   - pulls Smart Portfolios via `/api/v1/portfolios/search?Type=smart-portfolio&…`
   - optional deepen via Users Info / User Stats **GET only**
   - scores candidates (gain, riskScore, copiers, peakToValley, winRatio) into allowlist / review_only / refuse
   - paper/shadow “would-copy” sizing only when allowlisted + gates pass; `create_order`/`start_copy` always false in this slice
   - persists `data/etoro-paper/decisions.jsonl` (mirror vaughan/crypto-paper decision shape where natural: `source` contains `paper`, `create_order` false)
3. **Controls / desk note:** short `ETORO-PAPER.md` + Controls readout line if that pattern exists (connection health aware).
4. **Fence helpers:** explicit refuse codes for `etoro_live`, `write_endpoint`, `copy_start`, `half_live`; kill switch + daily would-copy notional caps (mirror Vaughan RiskGuard spirit).
5. **Tests:** auth header shape; rankings parse; smart-portfolio search; live flag blocks any write stub; missing keys refuse; decisions jsonl; no network in unit tests (fixtures).
6. **Docs + audit:** Ponytail; bump working-tree version; RuFlo + CBM tool use noted in `GROK-STATUS.md`.
7. **Optional stretch:** OpenTerminal-Valedia read-only panel stub listing top candidates from jsonl — **no** Copy buttons that call live API.

## Explicit non-goals
- Setting `ETORO_LIVE=1` or calling POST copy/trading/money endpoints
- Replacing Kalshi/Poly as primary live prediction path
- HAR /sapi scraping (Public API only; HAR skill = fallback docs only)
- Committing secrets; echoing keys in STATUS/log
- Auto-chaining Slice 2 / BB-Terminal

## Hard fence
`ETORO_LIVE=0`. Paper/shadow + research only. Screen `valedia-grok` only; preserve `ot-valedia-desk` / `valedia-glance`. No `--live`, no wallet, no copy allocation.

## Tools
RuFlo `:8810` filtered. CBM for Valedia navigation. Prefer Grok 4.7. Prove tool use in STATUS.

## Done when
- Module(s) + tests green offline
- One dry-run (or fixture) writes would-copy/review/refuse decisions with `create_order` false and no live copy
- Live-flag test proves write/copy stubs refuse
- `GROK_EXIT:0`; **Next = Burton stop** (parent may queue deepen / OT panel later)

## Results

**2026-09-22. VALEDIA_VERSION 0.67.0** (working tree, not committed, not tagged).

`scripts/integrations/etoro_client.py` GETs the public API with `x-api-key`, `x-user-key`, and a fresh `x-request-id`. Portfolio search sends OpenAPI `type` and retries `Type` only on HTTP 400. Rate gate 60/60s. User-Agent is the house Valedia research agent. The default Python agent got Cloudflare 1010 once; the next `/api/v1/me` was 200 (`gcid` / `realCid` / `demoCid` present, values not logged).

`scripts/integrations/etoro_copy_paper.py` scores gain, riskScore, copiers, peakToValley, winRatio into allowlist / review_only / refuse. Would-copy notional is $25 inside a $100 daily cap. `create_order` and `start_copy` stay false. Source `etoro_copy_paper`. Kelly ignores it.

Fixture log: `crypto-smart` would-copy $25 (score 0.62), `steady-pi` would-copy $25 (score 0.556), `mid-book` review_only, `blowup` refuse.

Live `--read` health: me ok, instrument types ok, instrument search ok, rankings 5, smart portfolios 4. Same log then would-copy `Smudliczek` (gain 0.3887, risk 4, copiers 15686, peak-to-valley 0.1319, win 72.87, $25) and `JeppeKirkBonde` (gain 0.3869, risk 5, copiers 25339, peak-to-valley 0.1433, win 67.97, $25). `CPHequities` matched the allowlist and stopped on `daily_notional` (the $100 cap was already used). `thomaspj` refuse (gain -0.0767). Smart-portfolio search rows stay review_only because that payload has copiers and no gain or risk. No copy was started.

Write stubs raise `copy_start`, `etoro_live`, `half_live`, or `write_endpoint` and do not open a socket. Unlock path, not set here: `ETORO_LIVE=1` and `ETORO_UNLOCK=burton-documented` and a mode that is not research/paper. Even that path raises `write_endpoint`.

Tests `test_etoro_copy_paper.py` PASS offline. `test_controls_readout.py`, `test_vaughan_arb_paper.py`, `test_slice23.py` PASS in a child that hid inherited `VALEDIA_JEV` / fee-map / Poly flags. Parent `POLYMARKET_US_LIVE` stayed 1. `ETORO_LIVE` was not set. Oil and weather fences held. Screens 783 / 755 / 64260 not killed.

Docs: `ETORO-PAPER.md`. Ponytail: `PONYTAIL-AUDIT-ETORO-SUBSUME-1.md`. RuFlo `hooks_route` + `memory_search` + `hooks_post-task`. CBM `list_projects` + `search_graph` on `controls_snapshot`.

`GROK_EXIT:0`

**Next = Burton stop.** Do not auto-chain a deepen slice, an OpenTerminal panel, or an eToro live unlock. Do not unfence oil/weather. Do not mass create_order.
