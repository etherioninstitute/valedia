#!/bin/bash
# Install Valedia cron jobs on Mac Studio
# All times in UTC (Mac cron uses system time — set to PT, so we adjust)
# These match the VPS exactly

RUNNER="/Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh"
ENV_CMD="export PATH=\$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH && export OLLAMA_MODELS=/Volumes/NIMROD_SSD/ollama_models"
SCRIPTS="/Users/burtonchatelain/.openclaw/workspace/scripts/integrations"

# Clear existing Valedia crons
crontab -l 2>/dev/null | grep -v "VALEDIA\|cron_runner_mac\|unified_brain\|hedge_fund\|fund_pipeline\|ml_kernel\|emerging\|insider\|whale\|rebalancer\|dca_module\|sentiment\|alpha_intel\|asi_scan" > /tmp/clean_cron

# Add all Valedia jobs (times in PT since Mac is PT)
cat >> /tmp/clean_cron << 'CRON'
# ═══ VALEDIA ASI — Mac Studio Cron Schedule (Pacific Time) ═══
SHELL=/bin/zsh
PATH=/usr/local/bin:/opt/homebrew/bin:/Users/burtonchatelain/.npm-global/bin:/usr/bin:/bin

# Pre-market Intel (ET times, converted to PT)
0 1 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh ml_kernel_retrain.py "ML RETRAIN" 2>&1
30 1 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh emerging_crypto_scanner.py "EMERGING" 2>&1
0 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh asi_scan.py "ASI SCAN" 2>&1
0 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh sentiment_scanner.py "SENTIMENT" 2>&1
0 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh alpha_intelligence.py "ALPHA INTEL" 2>&1
15 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh insider_mirror.py "INSIDER" 2>&1
30 2 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh hedge_fund_intel.py "HEDGE INTEL" 2>&1
0 3 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh fund_pipeline.py "FUND CHECK" 2>&1

# Trading Brain (market hours ET → PT)
35 6 * * 1-5 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh unified_brain.py "BRAIN OPEN" 2>&1
0 9 * * 1-5 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh unified_brain.py "BRAIN MIDDAY" 2>&1
45 12 * * 1-5 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh unified_brain.py "BRAIN CLOSE" 2>&1

# Post-market
0 15 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh portfolio_monitor.py "EOD PORTFOLIO" 2>&1

# Crypto (24/7)
0 19 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh unified_brain.py "BRAIN CRYPTO" 2>&1

# Whale monitor (every 4 hours)
0 */4 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh whale_copier.py "WHALE MONITOR" 2>&1

# Weekly
0 17 * * 0 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh dca_module.py "DCA" 2>&1
0 12 * * 5 /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh rebalancer.py "REBALANCE" 2>&1
CRON

crontab /tmp/clean_cron && echo "Cron jobs installed" && crontab -l | grep -c "cron_runner_mac" && echo "total Valedia jobs"
