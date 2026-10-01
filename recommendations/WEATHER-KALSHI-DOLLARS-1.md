# WEATHER-KALSHI-DOLLARS-1 — Kalshi dollars price fields for fee-true weather

**Queued:** 2026-09-24 ~17:58 PT  
**Burton intent:** Fix weather morning-go / shadow book price read so INFORMED fee-true EV can compute. Afternoon scan (~15:24 PT) had **87 official nws_cli FVs** but **0 alerts** because every `price=null` → `fee_true_ev=null`. Kalshi serves `yes_bid_dollars` / `yes_ask_dollars` / `last_price_dollars`; weather path still reads legacy `yes_bid` / `last_price`.

**Evidence (live tree, 2026-09-24 ~15:24 PT):**
- `recommendations/WEATHER-MORNING-GO-LAST.json`: n_markets=360, n_alert=0, admitted=0; tags include official×87 + wrong_date×198
- `scripts/integrations/weather_morning_go.py` `_open_weather_markets` (~L138): `yes = m.get("yes_bid"); if None: m.get("last_price")`
- Working pattern already in-tree: `kalshi_scanner.py` + `latency_arb.py` read `yes_bid_dollars` (0–1 dollars)

## Goals

1. **Shared quote helper** (prefer one place, e.g. `kalshi_quote.py` or small fn next to scanner):
   - Prefer `yes_bid_dollars` / `yes_ask_dollars` / `last_price_dollars` (float dollars 0–1)
   - Fall back to legacy `yes_bid` / `yes_ask` / `last_price` (cents or 0–1; same normalize as morning-go: `/100` if `>1`)
   - Return `(yes_bid, yes_ask, last, mid)` as floats in **0–1 dollars**, or None when absent
   - Unit tests covering dollars-only, cents-only, mixed, missing

2. **Wire weather path**
   - `weather_morning_go.py` `_open_weather_markets`: set `price` from helper (prefer mid or best actionable side for fee-true; document choice; do not invent)
   - Any other weather market assemblers that still use bare `yes_bid` (grep weather_* / oil stays out of scope unless identical one-liner)
   - Keep lottery + late-collapse refuses; do not loosen INFORMED

3. **Both trees**
   - Implement on **build tree** `~/.openclaw/workspace-v2`
   - **Promote / sync** the helper + `weather_morning_go.py` change into **live** `~/.openclaw/workspace` so tonight’s **19:34 PT** crontab (`weather_morning_go.sh`) sees non-null prices
   - Do **not** replace unrelated live cron code; leave oil fence + LIVE_EXEC_FENCED alone

4. **Prove**
   - Dry/read-only: fetch a few open KXHIGH* markets; assert helper returns non-null prices when API has `*_dollars`
   - Re-run `weather_morning_go.py` (LIVE may stay 1; admits still Valedia-owned). Expect: official rows with `price` set when book exists; `fee_true_ev` computable when edge exists. Late ~99¢ favorites may still refuse via SKIP_LATE_COLLAPSE — that is OK; goal is **alerts can fire**, not force lottery/late creates
   - Update `WEATHER-MORNING-DIAGNOSIS.md` path / STATUS with before→after tag/price stats

5. **Docs / exit**
   - Results on this charter; Ponytail one-pager; bump build `VALEDIA_VERSION` patch if that’s the lane norm
   - `GROK-STATUS.md` + `GROK-NEXT-QUEUE.md`: Active done; **Next = Burton stop** (no auto-chain)
   - Preserve screens `ot-valedia-desk` + `valedia-glance`; only recycle `valedia-grok`

## Explicit non-goals

- Oil / OIL_BRACKET / KXWTI / KXOIL unfence
- Flipping WEATHER_LIVE (already 1 on live when preflight PASS — do not flip off)
- Loosening lottery / late-collapse / INFORMED
- Mass create_order; wallet; treasury ACH; eToro write; paid TWC key
- Camofox / whole-wing mempalace mine

## Hard fence

- Live risk = Valedia runtime only; Grok Build codes/tests/merges into trees — no `--live` order spam from Build
- RuFlo MCP `:8810` filtered ADR-035 tools when coding
- Prefer codebase-memory for structural lookup

## Done when

- Helper + weather_morning_go wired on **both** trees
- Proof: sample open weather markets show non-null normalized prices from `*_dollars`
- Morning-go run no longer has `price=null` on every official row when book quotes exist
- STATUS Next = Burton stop; oil still fenced

## Result — 2026-09-24 18:15 PT

`GROK_EXIT:0`. Build tree **0.74.1** (not committed, not tagged). Live cron stays **0.44.0**. Helper `scripts/integrations/kalshi_quote.py` prefers `*_dollars`, falls back to legacy cents (`/100` if `>1`). `_open_weather_markets` sets `price` to **mid** when bid+ask quote, else last, else the one side. Zero is no quote.

**Proof (read-only KXHIGH*):** 8/8 sample books had `yes_bid`/`last_price` **None** and non-null `yes_price` from `*_dollars`. Live `_open_weather_markets`: n=336, n_price=336, n_price_null=0 (legacy fields None).

**Morning-go before → after** (live tree, 19:34 crontab path):

| | 15:24 PT (before) | 18:15 PT `--dry-decide` (after) |
|---|---|---|
| n_markets | 360 | 360 |
| n_price / n_price_null | (all null) | **360 / 0** |
| official | 87 | 121 |
| n_official_price | 0 | **121** |
| n_fee_true_ev | 0 | **75** |
| n_alert | 0 | **2** |
| admitted / create_order | 0 / false | 0 / false |

Alerts (shadow, `create_order=false`): `KXLOWTHOU-26SEP24-B76.5` ev=0.6206 px=0.06; `KXLOWTPHX-26SEP24-T73` ev=2.7262 px=0.085. Lottery 5¢ / late 90¢ / INFORMED unchanged. `WEATHER_LIVE` left **1**. Oil fenced. Screens 783/17171 left running.

Promoted to live: `kalshi_quote.py` + `weather_morning_go.py` only. Ponytail `PONYTAIL-AUDIT-WEATHER-KALSHI-DOLLARS-1.md`. Next = Burton. Stop.
