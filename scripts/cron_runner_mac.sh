#!/bin/bash
# Valedia Smart Cron Runner v2 — Self-Healing + Clean Reports
# Catches errors, diagnoses, auto-fixes, retries, filters noise.
# Only sends clean output to Burton. Logs everything locally.
# (c) GOATWORTHY Post LLC

SCRIPTS="/Users/burtonchatelain/.openclaw/workspace/scripts/integrations"
ENV_FILE="/Users/burtonchatelain/.openclaw/.env"
LOG="/Users/burtonchatelain/.openclaw/logs/valedia-cron.log"
HEAL_LOG="/Users/burtonchatelain/.openclaw/logs/self-heal.log"
LESSONS="/Users/burtonchatelain/.openclaw/workspace/tasks/lessons.md"
export PATH="$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
export OLLAMA_MODELS="/Volumes/NIMROD_SSD/ollama_models"

# Source env
set -a && source "$ENV_FILE" && set +a

SCRIPT="$1"
LABEL="${2:-CRON}"
MAX_RETRIES=2
TS=$(date -u '+%Y-%m-%d %H:%M UTC')

echo "[$LABEL] $TS starting" >> "$LOG"

# ═══ SELF-HEALING ENGINE ═══
diagnose_and_fix() {
    local OUTPUT="$1"
    local FIXED=0

    # Missing Python module → auto-install
    if echo "$OUTPUT" | grep -q "ModuleNotFoundError: No module named"; then
        MODULE=$(echo "$OUTPUT" | grep "No module named" | head -1 | sed "s/.*No module named '\\([^']*\\)'.*/\\1/" | tr '.' ' ' | awk '{print $1}')
        echo "[SELF-HEAL] $TS Missing module: $MODULE — installing..." >> "$HEAL_LOG"

        # Map common module names to pip packages
        case "$MODULE" in
            sklearn)   pip3 install scikit-learn --quiet 2>/dev/null ;;
            cv2)       pip3 install opencv-python --quiet 2>/dev/null ;;
            talib)     pip3 install TA-Lib --quiet 2>/dev/null ;;
            Crypto)    pip3 install pycryptodome --quiet 2>/dev/null ;;
            *)         pip3 install "$MODULE" --quiet 2>/dev/null ;;
        esac

        if [ $? -eq 0 ]; then
            echo "[SELF-HEAL] Installed $MODULE ✅" >> "$HEAL_LOG"
            echo "$(date -u '+%Y-%m-%d') | $LABEL: ModuleNotFoundError '$MODULE' | Auto-installed via pip" >> "$LESSONS"
            FIXED=1
        fi
    fi

    # NaN in training data → already patched, but catch new instances
    if echo "$OUTPUT" | grep -q "Input X contains NaN"; then
        echo "[SELF-HEAL] $TS NaN in training data — known issue, script should handle" >> "$HEAL_LOG"
        FIXED=0  # don't retry, the script itself needs the fix
    fi

    # Rate limit (CoinGecko, Finnhub, etc) → wait and retry
    if echo "$OUTPUT" | grep -qE "429|rate.limit|Too Many Requests"; then
        echo "[SELF-HEAL] $TS Rate limited — waiting 30s before retry" >> "$HEAL_LOG"
        sleep 30
        FIXED=1
    fi

    # Connection timeout / network error → retry once
    if echo "$OUTPUT" | grep -qE "ConnectionError|timeout|ConnectTimeout|MaxRetryError|ConnectionResetError"; then
        echo "[SELF-HEAL] $TS Network error — waiting 10s before retry" >> "$HEAL_LOG"
        sleep 10
        FIXED=1
    fi

    # SSL error → tunnel may be down, restart it
    if echo "$OUTPUT" | grep -qE "SSLError|SSL: WRONG_VERSION_NUMBER|ProxyError"; then
        echo "[SELF-HEAL] $TS SSL/Proxy error — checking SOCKS tunnel" >> "$HEAL_LOG"
        if ! lsof -nP -iTCP:1080 -sTCP:LISTEN > /dev/null 2>&1; then
            echo "[SELF-HEAL] SOCKS tunnel dead — restarting..." >> "$HEAL_LOG"
            ssh -D 1080 -f -N -i ~/.ssh/openclaw_vps -o ServerAliveInterval=30 root@85.239.234.32 2>/dev/null
            sleep 5
            echo "$(date -u '+%Y-%m-%d') | $LABEL: SOCKS tunnel died | Auto-restarted SSH tunnel" >> "$LESSONS"
            FIXED=1
        fi
    fi

    # Permission denied on exchange API → log but don't retry
    if echo "$OUTPUT" | grep -qE "401 Unauthorized|403 Forbidden|Permission denied"; then
        echo "[SELF-HEAL] $TS Auth error — API key may be expired or IP-locked" >> "$HEAL_LOG"
        FIXED=0
    fi

    return $FIXED
}

# ═══ OUTPUT CLEANER — Strip noise before sending to Burton ═══
clean_output() {
    local RAW="$1"
    echo "$RAW" | \
        grep -v "^Traceback\|^  File \"/\|^    \|^HTTP Error 404\|^WARNING:\|^FutureWarning\|^DeprecationWarning" | \
        grep -v "possibly delisted\|No data found, symbol may be delisted\|quoteSummary" | \
        grep -v "^$" | \
        head -60 | cut -c1-1500
}

# ═══ MAIN EXECUTION LOOP ═══
cd "$SCRIPTS"
ATTEMPT=0
SUCCESS=false
HEALED=""

while [ $ATTEMPT -le $MAX_RETRIES ]; do
    ATTEMPT=$((ATTEMPT + 1))

    OUTPUT=$(python3 "$SCRIPT" 2>&1)
    EXIT=$?

    # Check for errors
    HAS_ERROR=false
    if [ $EXIT -ne 0 ]; then
        HAS_ERROR=true
    elif echo "$OUTPUT" | grep -qE "^Traceback|raise .*Error|FATAL|CRITICAL"; then
        HAS_ERROR=true
    fi

    if $HAS_ERROR && [ $ATTEMPT -le $MAX_RETRIES ]; then
        echo "[$LABEL] Attempt $ATTEMPT failed — diagnosing..." >> "$LOG"
        echo "$OUTPUT" >> "$LOG"

        diagnose_and_fix "$OUTPUT"
        FIXED=$?

        if [ $FIXED -eq 1 ]; then
            HEALED="[self-healed: attempt $ATTEMPT]"
            echo "[SELF-HEAL] $TS Retrying $SCRIPT (attempt $((ATTEMPT+1)))..." >> "$HEAL_LOG"
            continue  # retry
        else
            # Can't fix — break and report
            break
        fi
    else
        if ! $HAS_ERROR; then
            SUCCESS=true
        fi
        break
    fi
done

echo "[$LABEL] exit=$EXIT attempts=$ATTEMPT success=$SUCCESS" >> "$LOG"
echo "$OUTPUT" >> "$LOG"
echo "---" >> "$LOG"

# ═══ SEND TO BURTON (via VPS relay — openclaw is only on VPS) ═══
# Only send for IMPORTANT events. Routine scans go to log only.
# Burton only wants Signal for: money invested + profit realized.
# ALL cron output goes to log only. Scripts handle their own Signal sends.
should_send() {
    return 1  # Never send from cron runner — scripts send directly
}

_send_signal() {
    local MSG="$1"
    # Escape dollar signs so they survive SSH + remote bash
    MSG=$(echo "$MSG" | sed 's/\$/USD /g' | head -c 1400)
    # Relay through VPS via SSH (openclaw CLI is only on VPS)
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no openclaw \
        "openclaw message send --channel signal --target 'uuid:47bd6dd5-2c96-4514-9541-acd8ed013612' --message '$(echo "$MSG" | sed "s/'/\\\\'/g")'" \
        2>> "$LOG" || true
}

if should_send; then
    if $SUCCESS; then
        MSG=$(clean_output "$OUTPUT")
        if [ -n "$HEALED" ]; then
            MSG="$HEALED
$MSG"
        fi
    else
        ERROR_LINE=$(echo "$OUTPUT" | grep -E "Error:|Exception:" | tail -1 | cut -c1-200)
        HEAL_ATTEMPTS=$(grep -c "$LABEL" "$HEAL_LOG" 2>/dev/null || echo "0")
        MSG="⚠️ $LABEL failed after $ATTEMPT attempts.
Error: $ERROR_LINE
Self-heal: $HEAL_ATTEMPTS fixes attempted."
    fi
    _send_signal "[$LABEL] $MSG"
fi
