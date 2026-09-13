"""
═══════════════════════════════════════════════════════════════════════
  VALEDIA ASI — UNIFIED OPENCLAW SKILL STACK ANALYSIS & ARCHITECTURE
  KALSHI-FIRST LATENCY ARBITRAGE ENGINE
  (c) GOATWORTHY Post LLC — March 2026
═══════════════════════════════════════════════════════════════════════

DOCUMENT PURPOSE:
  Complete audit of all OpenClaw skills mentioned across both Grok
  responses, overlap analysis, and unified Kalshi-first implementation
  blueprint. Includes the Price_Delta_Trigger latency arb system.

═══════════════════════════════════════════════════════════════════════
  SECTION 1: COMPLETE SKILL INVENTORY (ALL UNIQUE SKILLS)
═══════════════════════════════════════════════════════════════════════

RESPONSE 1 (Polymarket-focused):
  1. Polyclaw / polymarket-auto-trader + polymarket-trade
  2. Minara Skills (chat, Intent-to-Swap, Perp Trading, Prediction Market Analysis)
  3. Playwright (scraping, news sentiment)
  4. Capability Evolver / self-improving-agent
  5. ClawStrike (security)
  6. Twelve Data / summarize (market data)
  7. Binance skills (CEX execution)
  8. Hyperliquid skills (perps)
  9. Backtest-expert (custom)
  10. Risk-management (custom)

RESPONSE 2 (Kalshi adaptation):
  1. kalshi-claw-skill + get-kalshi-live-games (Kalshi execution)
  2. Binance WebSocket / Twelve Data (low-latency spot)
  3. Polyclaw-style execution wrappers / ClawForge Kalshi module
  4. hyperliquid-claw / HyperX (perps leg)
  5. Playwright (scraping + news)
  6. Capability Evolver (self-learning)
  7. ClawStrike (security)
  8. Minara / market-pulse (signal layer)
  9. Price_Delta_Trigger function (custom latency arb)

═══════════════════════════════════════════════════════════════════════
  SECTION 2: OVERLAP AUDIT
═══════════════════════════════════════════════════════════════════════

A. APPEARS IN BOTH RESPONSES:
  - Playwright              → Keep as-is (scraping/news)
  - Capability Evolver      → Keep as-is (self-learning)
  - ClawStrike              → NOT FOUND on ClawHub — skip (we use fail2ban + Unbound)
  - Minara                  → Keep as-is (crypto swap/perps/wallet)
  - Twelve Data             → Keep as-is (real-time market data)
  - Hyperliquid             → Keep as-is (perps execution)
  - Binance WebSocket       → REPLACE with Coinbase WS (we use Coinbase, not Binance)

B. POLYMARKET → KALSHI REPLACEMENTS:
  - Polyclaw / polymarket-auto-trader → REPLACED BY: kalshi-trading + kalshi-command-center
  - polymarket-trade                  → REPLACED BY: kalshi-cli-trading (already installed)
  - polymarket-arbitrage              → REPLACED BY: our custom sports_engine.py + master_allocator.py
  - polymarket-whale-copier           → KEEP for cross-platform intel (read-only)

C. SHARED SKILLS (REUSE WITHOUT MODIFICATION):
  - Playwright scraper      → Installed ✓ (news sentiment, injury reports)
  - Capability Evolver      → Installed ✓ (self-improving agent)
  - Self-improving agent    → Installed ✓ (memory + corrections)
  - Twelve Data integration → Installed ✓ (real-time prices)
  - Minara                  → Installed ✓ (crypto execution)

D. CONSOLIDATION OPPORTUNITIES:
  - kalshi + kalshi-cli-trading + kalshi-trading + kalshi-command-center
    → 4 Kalshi skills doing overlapping things
    → CONSOLIDATE: Use kalshi-command-center as primary (has edge scoring,
      risk management, Kelly sizing) + kalshi-trading for raw API access
    → Our custom kalshi_executor.py + kalshi_auth.py remain the actual
      execution layer (already proven with 50+ live trades)

  - polymarket-api + polymarket-arbitrage + polymarket-odds + polyedge
    → 4 Polymarket skills we can't use for non-sports
    → KEEP polymarket-odds + polyedge for READ-ONLY cross-platform price
      comparison (detect when Polymarket and Kalshi disagree on same event)

  - crypto-trading-bot + trader + trading-brain
    → 3 generic trading skills with heavy overlap
    → CONSOLIDATE into our unified_brain.py (already 904 lines, proven)

═══════════════════════════════════════════════════════════════════════
  SECTION 3: UNIFIED KALSHI-FIRST SKILL STACK (FINAL)
═══════════════════════════════════════════════════════════════════════

TIER 1 — EXECUTION (the money-making layer):
  ┌─────────────────────────┬────────────────────────────┬──────────────┐
  │ Skill                   │ Purpose                    │ Status       │
  ├─────────────────────────┼────────────────────────────┼──────────────┤
  │ kalshi-command-center   │ Edge scoring, risk mgmt,   │ NEW INSTALL  │
  │                         │ Kelly sizing, 600+ markets │              │
  │ kalshi-trading          │ Raw API: orders, orderbook │ NEW INSTALL  │
  │                         │ portfolio, market search   │              │
  │ kalshi-cli-trading      │ CLI trade execution        │ EXISTING     │
  │ kalshi (original)       │ Portfolio + market scripts  │ EXISTING     │
  │ OUR: kalshi_executor.py │ Proven RSA-PSS auth, 50+   │ CUSTOM       │
  │                         │ live trades, ERE-gated     │              │
  │ OUR: sports_engine.py   │ 86% WR sports favorites    │ CUSTOM       │
  │ OUR: master_allocator.py│ Cross-strategy allocation  │ CUSTOM       │
  │ hyperliquid             │ Perps execution (backup)   │ EXISTING     │
  │ minara                  │ Crypto swap/perps/wallet   │ NEW INSTALL  │
  └─────────────────────────┴────────────────────────────┴──────────────┘

TIER 2 — DATA INGESTION (the intelligence layer):
  ┌─────────────────────────┬────────────────────────────┬──────────────┐
  │ twelve-data-integration │ Real-time prices, technicals│ NEW INSTALL  │
  │ OUR: coinbase_ws.py     │ Live BTC/ETH/SOL WebSocket │ CUSTOM       │
  │ playwright-scraper      │ News, injuries, anti-bot   │ NEW INSTALL  │
  │ OUR: intel_layer.py     │ Hedge fund intel, sentiment│ CUSTOM       │
  │ OUR: mirofish_seed_v2   │ 5-agent swarm consensus    │ CUSTOM       │
  │ market-sentiment-pulse  │ Social sentiment scanning  │ EXISTING     │
  │ polymarket-odds         │ Cross-platform price check  │ EXISTING     │
  │ polyedge                │ Correlation arbitrage       │ EXISTING     │
  └─────────────────────────┴────────────────────────────┴──────────────┘

TIER 3 — SELF-IMPROVEMENT (the learning layer):
  ┌─────────────────────────┬────────────────────────────┬──────────────┐
  │ self-improving          │ Memory, corrections, hot   │ NEW INSTALL  │
  │                         │ memory ≤100 lines          │              │
  │ capability-evolver-zc   │ Runtime analysis, self-    │ NEW INSTALL  │
  │                         │ repair, auto-patching      │              │
  │ OUR: pnl_tracker.py     │ Real Kalshi P&L + auto-kill│ CUSTOM       │
  │ OUR: Ollama qwen3:8b    │ Nightly AI audit (local)   │ CUSTOM       │
  └─────────────────────────┴────────────────────────────┴──────────────┘

TIER 4 — SECURITY & MONITORING:
  ┌─────────────────────────┬────────────────────────────┬──────────────┐
  │ OUR: Unbound DNS-over-TLS│ Encrypted DNS queries     │ CUSTOM       │
  │ OUR: SOCKS5 tunnel       │ IP masking for API calls  │ CUSTOM       │
  │ OUR: fail2ban + SSH keys │ VPS hardening             │ CUSTOM       │
  │ OUR: Tailscale WireGuard │ Encrypted mesh network    │ CUSTOM       │
  │ (ClawStrike NOT on hub)  │ Would add API key rotation│ MISSING      │
  └─────────────────────────┴────────────────────────────┴──────────────┘

SKILLS REMOVED (redundant or unusable):
  - polymarket-api          → Can't trade non-sports on Polymarket
  - polymarket-arbitrage    → Replaced by sports_engine.py
  - polymarket-whale-copier → Replaced by whale_copier.py
  - polymarket-arbitrage-scanner → Replaced by master_allocator.py
  - crypto-trading-bot      → Replaced by kernel_auto_trader.py
  - trader                  → Replaced by unified_brain.py
  - trading-brain           → Replaced by unified_brain.py
  - kalshi-paper-trading    → We're past paper trading
  - cross-chain-arbitrage   → Not our current strategy
  - defi-yield-scanner      → Not our current strategy
  - funding-rate-scanner    → Not our current strategy
  - funding-rate-trader     → Not our current strategy

═══════════════════════════════════════════════════════════════════════
  SECTION 4: MODIFICATIONS NEEDED PER SKILL
═══════════════════════════════════════════════════════════════════════

1. kalshi-command-center (NEW)
   Purpose: Edge scoring across 600+ markets, Kelly sizing, risk caps
   Type: DIRECT REPLACEMENT for Polyclaw execution engine
   Mods needed:
     - Wire KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH env vars
     - Integrate with our ERE v2.0 risk gates (SKIP_INTEL_CONTRADICTS)
     - Feed its edge scores into master_allocator.py
     - Respect our strategy blacklist (pnl_tracker.py)

2. kalshi-trading (NEW)
   Purpose: Raw API access (orderbook, fills, positions)
   Type: DIRECT REPLACEMENT for Polyclaw API layer
   Mods needed:
     - Point at our RSA-PSS auth (kalshi_auth.py)
     - Use for latency arb orderbook monitoring

3. twelve-data-integration (NEW)
   Purpose: Real-time BTC/ETH/SOL prices + technical indicators
   Type: NEW CAPABILITY (was using only CoinGecko + Coinbase)
   Mods needed:
     - Get free API key from twelvedata.com
     - Wire into latency_arb.py as secondary price source
     - Feed RSI/MACD into sports_engine edge scoring? (future)

4. playwright-scraper-skill (NEW)
   Purpose: Anti-bot scraping for injury reports, news
   Type: NEW CAPABILITY (was using basic requests)
   Mods needed:
     - npm install in skill directory
     - Wire into sports_engine.py for injury checking before bets
     - Set up Cloudflare stealth mode for ESPN/injury sites

5. self-improving (NEW)
   Purpose: Persistent memory across sessions, auto-corrections
   Type: NEW CAPABILITY (was relying on lessons.md only)
   Mods needed:
     - Run setup.md to create ~/self-improving/
     - Wire into cron (heartbeat checks)
     - Feed pnl_tracker audit results into corrections.md

6. capability-evolver-zc (NEW)
   Purpose: Analyze runtime errors, auto-patch failures
   Type: NEW CAPABILITY
   Mods needed:
     - Configure to scan ~/.openclaw/logs/ for cron errors
     - Set --review mode initially (human-in-loop)
     - Wire Signal alerts for proposed patches

7. minara (NEW)
   Purpose: Crypto execution (swap, perps, transfers)
   Type: CONSOLIDATION of Coinbase + Hyperliquid execution
   Mods needed:
     - Install minara CLI (npm i -g minara)
     - Configure wallet
     - Could replace coinbase_trader.py for simpler execution

═══════════════════════════════════════════════════════════════════════
  SECTION 5: REALITY-CHECK WARNINGS
═══════════════════════════════════════════════════════════════════════

1. MALWARE RISK: Community skills can contain malicious code.
   We installed 5 skills with --force (flagged as "suspicious").
   MITIGATION: They run on VPS (isolated from NIMROD), and our
   actual money-touching code is custom Python on NIMROD.

2. LATENCY ARB IS NOT FREE MONEY: The 15-minute BTC contracts
   resolve quickly, but Kalshi's orderbook is thin. Large orders
   will move the market. Start with 1-5 contract test trades.

3. SKILL DEPENDENCY HELL: 39 skills installed. Many have npm
   dependencies that could conflict. Our custom Python scripts
   are dependency-isolated and proven. Skills are SUPPLEMENTARY
   intelligence, not execution replacements.

4. KALSHI API RATE LIMITS: 10 requests/second for trading,
   100/second for market data. Latency arb needs careful
   throttling. Our kalshi_auth.py already handles this.

5. REGULATORY: Kalshi is CFTC-regulated. All trades are legal.
   But automated trading at high frequency may trigger review.
   Keep trade sizes reasonable ($25 max per the command-center
   defaults is sensible for initial deployment).
"""
