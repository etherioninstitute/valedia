# VALEDIA-FOMO-RADAR-FENCED

**Updated:** 2026-09-21 PT  
**Clone:** ~/MemPalace/sources/repos/github/fomo-robinhood-radar @ 0bfee25  
**Upstream:** https://github.com/cvxv666/fomo-robinhood-radar (MIT)

## What it is
Robinhood Chain (4663) smart-trader radar: fomo profile→wallet resolve, on-chain tape, provenance quarantine, AI verdicts. Analytics only — does not place trades.

## Valedia use (Finance Scout 2026-09-21)
- Steal: provenance, resolve, judgement-rank, burst/exit → CRYPTO-PAPER / MiroFish **enrich only**
- Not Kalshi/Poly OMS; idea-transfer only for those desks
- Rank after PM leaderboard + Kalshi GetTrades anon flow

## Fence
No live orders, wallets, HALT bypass, PRIVATE_KEY paths, or fomo browser scrape revival. FOMOAPI optional for research, never hard dep for live Valedia.

Whale/radar score never sizes money alone. Annotate only.

## Build result (2026-09-21, Grok Build, `0.55.0`)

Dossier: `recommendations/ADAPT-DOSSIERS/fomo-robinhood-radar.md`  
Stub: `scripts/integrations/fomo_radar_enrich.py`  
Fixture: `data/crypto-paper/fomo_radar_fixture.json` (offline; not a track record)  
Tests: `python3 scripts/integrations/test_fomo_radar.py`  
Ponytail: `recommendations/PONYTAIL-AUDIT-FOMO-RADAR.md`

`enrich()` emits shadow tags (`wallet_conviction`, `provenance_ok`, `burst`, `exit`). `stamp_paper` / `stamp_signal` copy CRYPTO-PAPER equity docs and MiroFish signal docs. They do not change qty, cash, pnl, score, direction, or weight. Not wired into `crypto_paper.run`, Kalshi `execute_allocations`, or Poly `create_order`. No cron.

`POLYMARKET_US_LIVE` False. `VALEDIA_TRADING_HALT` unchanged. `VALEDIA_TREASURY_AUTO` False. No wallet keys.

## Cannot-do (this slice)

- No FOMOAPI client and no hard dependency on it
- No browser scrape, no extension
- No chain RPC / Codex / Helius resolve (`resolve.py` stays a documented pattern)
- No Claude/Anthropic judgement call (`score.py` stays upstream)
- No sliding-window burst engine and no candle exit study (fixture fields only)
- No order path, no sizing from conviction or judgement rank
- No Kalshi V2, no `POLYMARKET_US_LIVE`, no `VENUE_WRITER` edits, no treasury auto
- Not a fomo product port
