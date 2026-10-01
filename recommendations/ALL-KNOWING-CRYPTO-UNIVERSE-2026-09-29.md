# All-knowing movers — paper/shadow scan

**When:** 2026-09-30 00:14 PT (scan `2026-09-30T07:14:11Z`)
**Book:** $504 paper (10% clip = **$50.40** per sized name)
**Status:** PAPER/SHADOW. `create_order=false`. `GROK_EXIT:0`

## What ships

- `scripts/integrations/all_knowing_movers_scan.py` — one paper scan. Ranks by fee-true edge × $50.40. Sizes only a ≥16% day that is not oil and not weather.
- `scripts/integrations/test_all_knowing_movers_scan.py` — fixture math, oil/weather fence, Jev fail-open, CoinGecko-then-Binance tape.
- `data/all-knowing/fixture_movers.json` — offline rows (PUMP/QNT at 16%+).
- `data/all-knowing/movers_scan.json` — last live scan.
- `data/all-knowing/paper_watch.json` — paper watch list. Not `crypto_paper.ALLOWLIST`.
- `dynamic_universe.MODELS_DIR` now `workspace-v2/models`. `hot_list.json` on disk was not rewritten (mtime 2026-09-14). The next DISCOVER cron writes here.

## One scan

```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 scripts/integrations/all_knowing_movers_scan.py
```

Offline (no network):

```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 scripts/integrations/all_knowing_movers_scan.py --fixture data/all-knowing/fixture_movers.json
```

Tests: `python3 scripts/integrations/test_all_knowing_movers_scan.py` on Python 3.13. **PASS.**

## Fee-true rank

Edge = 24h move − roundtrip (taker 0.10% + slip 0.05%) × 2. Roundtrip = **0.003** (30 bps), from `crypto_paper.paper_fees()` / `CRYPTO_FEE_PCT`. Impact $ = edge × $50.40. A name is sized only when the day is **≥16%**, edge > 0, and the row is not oil or weather. Jev does not change the order. Laya is a local stub (`watch` / `would_buy` / `would_pass`). No CoreML package.

Each sized name is its own $50.40 clip. This scan does not net several clips into one book. Three sized names would be $151.20 if someone stacked them.

## Live proof (Binance.US public 24h)

CoinGecko `/coins/markets` returned **HTTP 429**. The scan used `https://api.binance.us/api/v3/ticker/24hr` (public ticker, not an order). Top-list quote-volume floor is $250,000. PUMP and QNT are probed even under that floor.

| Symbol | Class | 24h % | Edge | Impact $ | Quote vol $ | Laya | Sized |
|---|---|---:|---:|---:|---:|---|---|
| ANT | crypto | 125.534 | 1.25234 | 63.12 | 264,422 | would_buy | Y |
| CUDOS | crypto | 19.947 | 0.19647 | 9.90 | 322,134 | would_buy | Y |
| POLY | crypto | 16.622 | 0.16322 | 8.23 | 392,961 | would_buy | Y |
| PUMP | crypto | 13.260 | 0.12960 | 6.53 | 305,689 | watch | n |
| QNT | crypto | 12.748 | 0.12448 | 6.27 | 184,462 | watch | n |
| GLD | etf | 1.32 | 0.0102 | 0.51 | — | watch | n |
| QQQ | etf | 0.19 | −0.0011 | −0.06 | — | would_pass | n |
| SPY | etf | −0.18 | −0.0048 | −0.24 | — | would_pass | n |
| KXHIGHNY | weather | — | — | — | — | watch | n |

**PUMP is present. QNT is present.** Neither printed a 16% day on this Binance.US tape, so neither is sized. QNT's $184,462 quote volume is under the $250k floor; it is on the page because the scan always probes PUMP and QNT. ANT's $50.40 clip is about 19% of that name's Binance.US day quote. Thin tape. Paper only.

Entry fee on a sized $50.40 buy is **$0.0504252**. Roundtrip cost at the fee table is **$0.1512**. `would_have.create_order` is false.

Yahoo tape (same run): GLD +1.32% watch, QQQ and SPY negative edge so `would_pass`. VIX and DXY returned no 2-day print. Live tape had no oil symbol. The oil rule is in the ranker and in the fixture test: USO +22% stays `watch`, `harvest=NO_HARVEST`, unsized.

Jev: `enabled=false`, `fail_open=true`, `reason=VALEDIA_JEV_off`, `changes_rank=false`.

## Fences held

- No RH / Coinbase / Kraken / Alpaca / Kalshi / Polymarket order
- No `--live`, no `NAUTILUS_LIVE`
- `MIROFISH_AUTO_RAISE` false
- `CRYPTO_PAPER_SAFETY` = `PAPER/SHADOW+ORNITH` (not bypassed)
- `crypto_paper.ALLOWLIST` still `BTC-USDT`, `ETH-USDT`. `allowlist_widened=false`
- Oil `NO_HARVEST`. Weather watch only
- Laya stub only. Jev seasoning only
- Screens `ot-valedia-desk` / `valedia-glance` not touched
- No crontab install

## Ponytail

One module. Did not run `discover_universe` (it sleeps, calls the kernel, and rewrites `hot_list.json`). Did not add a second gainer crawler: one CoinGecko markets call, then the public 24h tape this tree already uses for klines (`binance.us`). Fees, clip, fill price, and fee dollars come from `crypto_paper`. Equity day-change comes from `market_data.get_market_summary`. Jev is `jev_shadow.annotate`.

## Tools

- RuFlo `:8810` health `ok` at `2026-09-30T07:09:58Z`. Hooks not called.
- codebase-memory project `Users-burtonchatelain-.openclaw-workspace-v2`. `search_graph` sees `fee_true_edge` and `rank_movers`. `check_index_coverage` metadata_match on the new scan and test. `dynamic_universe.py` metadata_changed after the models-path edit. A second `index_repository` was refused: another index for this project was already active.
- MemPalace: one drawer in wing `valedia`, room `general`, from `ALL-KNOWING-2026-09-30.md`. `mempalace search "PUMP"` returns that note.
