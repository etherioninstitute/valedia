---
description: >
  All active trading engines. Sports, latency arb, quant, scanner.
  Every engine's edge, sizing, and risk controls.
  Updated March 30, 2026.
---

# trading engines

## Sports Engine v2.0 (sports_engine.py, 483 lines) — LIVE

Record: 7W/1L (87.5%) net: +6.06
Edge: bets 70-85c favorites where our WR (87.5%) beats market implied prob (77%)
Cron: every 2 hours

Intelligence wired:
- MiroFish swarm bias (risk-on/risk-off)
- ERE v2.0 regime (CALM = favorites reliable)
- Fear/Greed index (extreme fear = favorites dominate)
- Spread analysis (tight = market confident = bigger bet)
- Dog price analysis (close game = reduce or skip)
- Injury checker (ESPN API, DANGER = -20 edge, CAUTION = -5)

Sizing: variable 4-9% of bankroll based on composite edge score
Edge score components: spread + price zone + dog price + injuries + MiroFish + ERE + Fear/Greed
Games skipped if edge_score < -10

Series scanned: KXNBAGAME, KXNHLGAME, KXMLBGAME, KXNCAAMBGAME
Signal: "Invested: {amount} on {N} games" on execution

## Latency Arb Engine (latency_arb.py, 538 lines) — DRY-RUN

Exploits lag between CEX spot moves and Kalshi binary repricing.
BTC at 67K: tested 46-49 cent edges when spot moves >0.3% in <30s.
Cron: every 30 minutes, 15-min monitoring window

Pipeline:
1. SpotPriceTracker: CoinGecko polling (2s intervals, thread-safe)
2. price_delta_trigger: fires on >0.3% moves within 30s window
3. find_kalshi_contract: matches KXBTCD/KXETH strike-price contracts
4. execute_arb: limit orders with risk gates

Risk gates: 50/day daily loss cutoff, 25/trade max, 20 contracts max, 120s cooldown
Switch to live: change arb_run.py mode from 'dry-run' to 'execute'

## Quant Engine (quant_engine.py, 394 lines) — SCANNING

LunarResearcher framework. 5 formulas:
1. LMSR pricing: C(q) = b * ln(sum(e^(qi/b))) — price impact modeling
2. EV gap: true_prob - market_price - fees (min 3c edge)
3. Kelly criterion: quarter-Kelly, 10% bankroll hard cap
4. Bayesian estimation: blends MiroFish + ERE + base rates (70/30 external/market)
5. Cross-platform arb: Kalshi vs Polymarket (Polymarket DISABLED, built for future)

Platforms:
- KalshiAdapter: ACTIVE
- PolymarketAdapter: BUILT, DISABLED (flip ENABLED = True when non-sports access granted)

Cron: every 6 hours. Results saved to quant_engine_log.json.

## Market Scanner (kalshi_scanner.py, 167 lines) — ACTIVE

Port of kalshi-command-center edge scoring.
Scans 600+ Kalshi markets (3 pages x 200), filters exclude MVE.
Edge score = spread_tightness(15) + centrality(25) + liquidity(35) + time_value(25)
Found 185 sports prop opportunities (untapped profit surface).
Feeds scanner_results.json to master allocator.
Cron: every 6 hours.

## Settlement Checker (settle_run.py, 120 lines) — ACTIVE

Checks all pending bets for Kalshi settlement every hour.
Updates sports_engine_log.json with win/loss/pnl.
Recomputes cumulative stats.
Signal: "Profit: {+/-amount} ({W}W/{L}L)" ONLY when bets settle.
Silent when nothing to report.

## Master Allocator (master_allocator.py, 540 lines) — ACTIVE

5 sources: Kalshi executor, crypto fusion, stock fusion, weather (killed), scanner results.
Ranks all opportunities by EV per dollar per day.
Respects strategy blacklist (auto-kills <25% WR after 3+ trades).
Deploys up to 70% of capital. Min 3% expected return.

## P&L Tracker (pnl_tracker.py, 291 lines) — NIGHTLY

Real Kalshi settlement tracking (not fake self-reported).
Strategy auto-kill: 3+ trades AND <25% WR = permanently blacklisted.
Ollama qwen3:8b audit: reads thinking field, 2000 token limit.
Writes to pnl_tracker.json + opus_audit.json.

## Killed strategies

- WEATHER_ARB: 0W/11L, -22.91 (PERMANENTLY KILLED)
- OIL_BRACKET: 0W/4L, -19.90 (PERMANENTLY KILLED)
- Heavy favorites 90%+: negative EV (our 86% < market 94%)
