#!/bin/bash
# Install Valedia cron jobs on Mac Studio  (SINGLE SOURCE OF TRUTH for the schedule)
# The Mac system clock is America/Los_Angeles (PT). Every cron field below is
# Mac-LOCAL time (PT), NOT UTC. UTC equivalents are in skills/valedia/cron-schedule.md
# (shown for PDT = UTC-7; add 1h in PST). That doc MUST match this file exactly;
# scripts/test_cron_schedule.py enforces it (same jobs, same times, scripts exist).
#
# WARNING: the clean step below drops EVERY crontab line containing "cron_runner_mac"
# (plus the named .sh jobs). Live-only lines run through the runner that are not listed
# here (e.g. kalshi_audit, intel_refresh) would be removed. Diff `crontab -l` first.
# Do not run this on the live machine without Burton. Shadow parity is report-only.

RUNNER="/Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh"
ENV_CMD="export PATH=\$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH && export OLLAMA_MODELS=/Volumes/NIMROD_SSD/ollama_models"
SCRIPTS="/Users/burtonchatelain/.openclaw/workspace/scripts/integrations"

# Clear existing Valedia crons
crontab -l 2>/dev/null | grep -v "VALEDIA\|cron_runner_mac\|mirofish_primary\|brain_open_gated\|weather_morning_go\|unified_brain\|hedge_fund\|fund_pipeline\|ml_kernel\|emerging\|insider\|whale\|rebalancer\|dca_module\|sentiment\|alpha_intel\|asi_scan\|sports_run\|allocator_run\|settle_run\|reconcile_run" > /tmp/clean_cron

# Add all Valedia jobs (times in PT since Mac is PT)
cat >> /tmp/clean_cron << 'CRON'
# ═══ VALEDIA ASI — Mac Studio Cron Schedule (Pacific Time, Mac-local) ═══
SHELL=/bin/zsh
PATH=/usr/local/bin:/opt/homebrew/bin:/Users/burtonchatelain/.npm-global/bin:/usr/bin:/bin

# --- Trading engines (high frequency) ---
# Latency arb is DRY-RUN only (arb_run.py mode='dry-run'); never orders.
*/30 * * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh arb_run.py "LATENCY ARB" 2>&1
30 * * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh settle_run.py "SETTLEMENT" 2>&1
# Kalshi live writers — STAGGERED (Slice 7). Sports :00 every 2h; allocator :20
# every 4h. Same-minute collision was a double-bet race on 4h boundaries.
0 */2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh sports_run.py "SPORTS ENGINE" 2>&1
20 */4 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh allocator_run.py "MASTER ALLOCATOR" 2>&1
# Ledger (live crontab already ran these; installer was stale)
0 */6 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh reconcile_run.py "LEDGER RECONCILE" 2>&1
# Shadow parity report-only (Slice 16). No POST. No flag flips. Do not run this installer on live.
45 6 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh parity_run.py "SHADOW PARITY" 2>&1
0 */6 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh quant_run.py "QUANT ENGINE" 2>&1
30 */6 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh scanner_run.py "MARKET SCANNER" 2>&1
# Whale monitor (every 4 hours)
0 */4 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh whale_copier.py "WHALE MONITOR" 2>&1
30 */6 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh dynamic_universe.py "DISCOVER" 2>&1

# --- Intelligence (daily, overnight; PT) ---
# MiroFish primary: real swarm, intel_refresh fallback; writes the mirofish_ready marker.
0 1 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/mirofish_primary.sh # MIROFISH SWARM
0 1 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh ml_kernel_retrain.py "ML RETRAIN" 2>&1
0 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh intel_sentiment.py "INTEL+SENTIMENT" 2>&1
0 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh asi_scan.py "ASI SCAN" 2>&1
15 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh insider_mirror.py "INSIDER" 2>&1
30 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh hedge_fund_intel.py "HEDGE INTEL" 2>&1
0 3 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh fund_pipeline.py "FUND CHECK" 2>&1

# --- DEPRECATED, NOT INSTALLED (scripts renamed *.DEPRECATED; do not re-add) ---
# emerging_crypto_scanner.py "EMERGING"     — DEPRECATED: script is marked scripts/integrations/emerging_crypto_scanner.py.DEPRECATED (discovery now = dynamic_universe DISCOVER)
# sentiment_scanner.py "SENTIMENT"          — DEPRECATED: script is marked scripts/integrations/sentiment_scanner.py.DEPRECATED (replaced by intel_sentiment.py)
# alpha_intelligence.py "ALPHA INTEL"       — DEPRECATED: script is marked scripts/integrations/alpha_intelligence.py.DEPRECATED (its sources live in insider_mirror / hedge_fund_intel / whale_copier)

# --- Market hours (Mon-Fri, PT) ---
# BRAIN OPEN goes through the gate: waits (bounded) for the MiroFish marker, then runs unified_brain.py.
35 6 * * 1-5 /Users/burtonchatelain/.openclaw/workspace/scripts/brain_open_gated.sh # BRAIN OPEN
0 9 * * 1-5 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh unified_brain.py "BRAIN MIDDAY" 2>&1
45 12 * * 1-5 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh unified_brain.py "BRAIN CLOSE" 2>&1

# --- Reports ---
0 7 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh kalshi_alert.py "MORNING BRIEF" 2>&1
# Weather morning go ~7:04 PT. Valedia runtime decides WEATHER_LIVE from preflight; this line flips nothing.
4 7 * * * /Users/burtonchatelain/.openclaw/workspace-v2/scripts/weather_morning_go.sh # WEATHER MORNING GO
0 15 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh portfolio_monitor.py "EOD PORTFOLIO" 2>&1
0 23 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh pnl_run.py "P&L TRACKER" 2>&1

# --- Crypto (24/7) ---
0 19 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh unified_brain.py "BRAIN CRYPTO" 2>&1

# --- Weekly ---
0 17 * * 0 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh dca_module.py "DCA" 2>&1
0 12 * * 5 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh rebalancer.py "REBALANCE" 2>&1
# Self-evolve weekly: policy-lab proposals only (offline, no LLM/network; writes recommendations/proposals/ + labs
# under ~/.openclaw/state/policy_lab; never live config, crontab, models/ or orders). Sun 4:50 AM PT; :50 dodges
# every other job (allocator is 04:20). Runner cds to scripts/integrations, hence the relative path.
50 4 * * 0 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh ../self_evolve/weekly_run.py "SELF-EVOLVE WEEKLY" 2>&1
CRON

crontab /tmp/clean_cron && echo "Cron jobs installed" && crontab -l | grep -c "cron_runner_mac" && echo "total Valedia jobs"
