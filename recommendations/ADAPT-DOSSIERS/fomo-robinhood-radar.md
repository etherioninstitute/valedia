# ADAPT: fomo-robinhood-radar

**Source:** `~/MemPalace/sources/repos/github/fomo-robinhood-radar` @ `0bfee25`  
**Upstream:** https://github.com/cvxv666/fomo-robinhood-radar  
**License:** MIT  
**Stack:** Python radar (Robinhood Chain 4663): profile→wallet resolve, on-chain tape, provenance quarantine, judgement leaderboard, burst/exit. Analytics. Does not place trades.  
**Audited:** 2026-09-21 (dossier + offline enrich stub)  
**Rank:** after PM Data API leaderboard + Kalshi GetTrades anon flow. Crypto-paper / MiroFish enrich only.

## 1) Summary

Finds fomo.family traders worth watching, resolves each profile to the wallet that actually trades, reads fills, checks whose trade a fill really was, and ranks the roster by an AI judgement score rather than headline PnL. Live product is a site, Telegram bot, and HTTP API. Valedia takes the **labels**, not the product.

## 2) Pipeline map

```
profile swaps → resolve.py (quiet-window makers)
    → wallet tape (rpc eth_getLogs; relayer, not tx.from)
    → provenance.py (direct / dust / trade / seed; seeded tokens leave every feed)
    → score.py (Claude JSON → traders.score) 
    → analyze.leaderboard (ORDER BY score, not PnL)
    → analyze.signals conviction Σ(score/100)^2
    → hot.py burst (same conviction, short window)
    → analyze.exits (sold ≥ half the watched entry)
```

Quote assets (USDG) are not signals. A fomo profile address is not a wallet. A token arriving from the router is not always that wallet buying it.

## 3) Valedia overlap

| Already here | What radar would duplicate |
|--------------|----------------------------|
| `crypto_paper.py` PAPER/SHADOW ledger (`BTC-USDT`/`ETH-USDT`, SMA intents, mid±slip, `equity.json`) | A second crypto book. Do not add one. |
| `mirofish_neural_enrich.attach_to_signal` | Advisory stamp on `signals[ticker]` that does not change score, direction, or weight. |
| `mirofish_signal_extractor` keys `score` / `direction` / `confidence` | Judgement score must not overwrite these. |
| Kalshi `execute_allocations`, Poly `create_order` | Radar has no OMS. Do not invent one. |
| Whale/insider paths already DRY/SHADOW | Another copy-trade sizer. Radar stays a tag. |

## 4) Steal

Patterns only. This slice does not vendor the repo.

1. **`pipeline/resolve.py`** — `rank_candidates`: each maker window adds `1/len(window)`. `decide` accepts the leader only with enough hits and a clear gap vs the runner-up (or a decisive hit gap). One address, one trader; conflicts stay unresolved. Needs chain or Codex. **Not in the stub.**
2. **`pipeline/provenance.py`** — kinds `direct` (router-sent, not the wallet's trade), `dust` (below floor / median share; stored, out of conviction), `trade`, `seed` (scripted one-fill wave). Seeded when trusted pushed wallets `>= seed_min_wallets` (3) **and** they outnumber real buyers. NULL kind counts as trade until a receipt exists. **Stub applies this gate on fixture rows.**
3. **Judgement leaderboard** — `analyze.leaderboard` orders by `traders.score` then PnL, status `active`. `score.py` is the producer (Claude JSON: score 0–100, status, style, red_flags). PnL including open bags is the model's primary input upstream; on-chain flow is the sanity check. **Stub passes `judgement_rank` through. It does not call a model.**
4. **Burst** — `hot.py`: sliding window of first buys; fires when Σ(score/100)^2 `>= hot_delta` (4.0) from `>= hot_min_wallets` (3) inside `hot_window_min` (30), span `>= hot_min_span_s` (180). **Stub accepts a precomputed `burst` object. It does not replay a tape.**
5. **Exit** — `analyze.exits`: a wallet counts once it has sold `min_exit` (0.5) of the watched token amount; token feed wants `min_sellers` (2). States from `position_state`: `closed` / `trimmed` / `open` / `held` / `pre-tape` / `unknown`. **Stub tags `exit` only for closed/trimmed at ≥ 0.5 with ≥ 2 sellers.**
6. **Conviction** — `analyze.conviction`: Σ(score/100)^2 over trusted holders (`TRUSTED = 60`), one wallet once, real fills only. A crowd of 40s cannot outvote one 85.

## 5) Hard exclusions

| Leave | Why |
|-------|-----|
| `fomo_agent/browser.py`, `extension/` | Charter: do not revive the browser scrape or extension. |
| `sources/fomoapi.py`, `sources/fomo.py` | Optional research client upstream. Not a Valedia dependency. |
| `sources/rpc.py`, Codex, Helius, wallet keys | Chain identity and receipts. No keys in this repo. |
| `score.py` Anthropic call, Telegram/Discord/X | Spend and outbound. |
| `scripts/paper.py`, `exit_rule.py` live candles | Upstream paper study. Valedia paper book is `crypto_paper.py`. |
| Any path into Kalshi `execute_allocations` or Poly `create_order` | Enrich only. |
| Using `wallet_conviction` or judgement score as size | Annotate only. Kelly / clip / fee gate stay the sizer. |

## 6) Valedia integration shape

Module: `scripts/integrations/fomo_radar_enrich.py`  
Fixture: `data/crypto-paper/fomo_radar_fixture.json` (offline sample, not a track record)  
Call: `enrich(load_fixture())` → `stamp_paper(equity_doc, tags)` and/or `stamp_signal(signal_doc, tags)`.  
Not called from `crypto_paper.run` or the allocator. No cron.

### Input (offline fixture)

```json
{
  "tokens": [{
    "symbol": "BTC",
    "pair": "BTC-USDT",
    "judgement_rank": 1,
    "holders": [{"score": 80, "kind": "trade"}],
    "real_wallets": null,
    "pushed_wallets": null,
    "burst": {"conviction": 4.48, "wallets": 4, "window_min": 30, "span_s": 400},
    "exit": {"state": "open", "exit_pct": 0.0, "sellers": 0}
  }]
}
```

`kind` is `trade` | `dust` | `direct` | `seed` (missing → `trade`).  
`real_wallets` / `pushed_wallets`, when set, override the holder count. Otherwise trusted holders (`score >= 60`) are counted.  
`burst` and `exit` are already-reduced. This module does not scan a tape.

### Output tag (one token)

| Field | Type | Meaning |
|-------|------|---------|
| `source` | `"fomo_radar_offline"` | Not a live radar read. |
| `annotate_only` | `true` | Shadow label. |
| `sizes_money` | `false` | Never a size. |
| `symbol` | string | MiroFish key. Also indexed as `{symbol}-USD` when `symbol` has no dash. |
| `pair` | string | CRYPTO-PAPER pair (`BTC-USDT`). |
| `wallet_conviction` | number | Σ(score/100)^2 on trusted `trade` holders. **0 when `provenance_ok` is false.** |
| `provenance_ok` | bool | Not seeded, and at least one trusted real wallet. |
| `seeded` | bool | Pushed trusted wallets `>= 3` and pushed `>` real. Quarantine. |
| `burst` | `"burst"` or `null` | Provenance ok, burst conviction `>= 4`, wallets `>= 3`, span `>= 180` (span omitted → allowed). |
| `exit` | `"exit"` or `null` | Provenance ok, state `closed` or `trimmed`, `exit_pct >= 0.5`, sellers `>= 2` (sellers omitted → allowed). |
| `tags` | list | Whichever of `burst` / `exit` fired. |
| `judgement_rank` | int or `null` | Pass-through. Not a Kelly input. |
| `note` | string | Annotate only. |

Envelope from `enrich()`: `{source, annotate_only, sizes_money, create_order: false, symbols, pairs, tokens}`.

### CRYPTO-PAPER-1

`stamp_paper` copies `equity.json`-shaped docs from `crypto_paper.run` and sets:

- `fomo_radar` — the tag for `doc["pair"]` (or `null`)
- `fomo_radar_enriched` — bool
- `fills[i].fomo_radar` — tag for that fill's pair

Untouched: `cash`, `positions`, `qty`, `px`, `fee`, `side`, `would_have_pnl`, `n_fills`. Paper fills stay simulated. They are not copied into the fee-true ledger as live.

### MiroFish enrich

Same shape as `mirofish_neural_enrich.attach_to_signal`. `stamp_signal` copies the extractor doc and sets `signals[ticker].fomo_radar` plus top-level `fomo_radar_enriched`. Lookup tries the ticker, its base, and `{base}-USDT`.

Untouched: `score`, `direction`, `confidence`, `weight`, `raw_sentiment`, neural fields. Caps `0.10` / `0.15` and `MIROFISH_AUTO_RAISE` stay as they are. This is not a fusion weight.

## 7) Effort / deps

**S.** Stdlib only. Shipped: dossier, fixture, `fomo_radar_enrich.py`, `test_fomo_radar.py`.  
Later, only if Burton asks: an offline resolver over a saved maker-window fixture (still no RPC in tests). Live tape, FOMOAPI, and judgement-model calls stay out.
