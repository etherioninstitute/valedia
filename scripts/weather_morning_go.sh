#!/bin/bash
# Valedia weather morning go @ ~7:04 PT. Owner: Valedia runtime (not Grok Bot).
# Valedia decides WEATHER_LIVE from preflight (PASS→1, else→0). Does NOT unfence oil.
# Creates only via weather_live.admit on NWS-settled official alerts when live on.
set -euo pipefail
ENV_FILE="/Users/burtonchatelain/.openclaw/.env"
SCRIPTS="/Users/burtonchatelain/.openclaw/workspace-v2/scripts/integrations"
PY="/opt/homebrew/Caskroom/miniconda/base/bin/python3"
LOG="/Users/burtonchatelain/.openclaw/logs/weather-morning-go.log"
mkdir -p "$(dirname "$LOG")"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
cd "$SCRIPTS"
export PYTHONPATH="$SCRIPTS"
export PATH="/opt/homebrew/Caskroom/miniconda/base/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') WEATHER_MORNING_GO ===="
  echo "WEATHER_LIVE_before=${WEATHER_LIVE:-unset} (Valedia decides inside python)"
  "$PY" weather_morning_go.py
  echo "exit=$?"
} >>"$LOG" 2>&1
