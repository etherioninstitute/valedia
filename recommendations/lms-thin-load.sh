#!/bin/zsh
# LMS thin-load / dual-load policy (Phase 5 + dual 2026-09-19)
# Usage: lms-thin-load.sh <lane>
#
# Single-resident (unload all, then one):
#   ornith | cron | solo     -> Ornith Q5 (default ornith-1.5-k-m) @ 131072
#   coding | obliterated | hermes | valedia -> qwen3.8-27b-obliterated-k @ 65536
#   turbo  | ruvocal         -> qwen3.8-turbo-coder-k @ 65536
#   companion                -> ~17GB companion only (default planned UD-Q4_K_XL)
#
# Dual-resident (64GB / M2 dual days — NEVER Q5 + companion):
#   dual                     -> unload all, load Ornith Q4 + companion
#
# Ops:
#   status | ps              -> lms ps
#   unload | clear           -> unload all
#   roster                   -> print resolved keys + refuse rules
#
# Override keys (optional):
#   ORNITH_SOLO_KEY  ORNITH_DUAL_KEY  COMPANION_KEY  LMS_BIN
#
# LLM Stack 2026-09-19: solo=Q5; dual=Q4+Qwen3.8-27B-UD-Q4_K_XL; refuse Q5+~17GB co-load.
set -euo pipefail
export PATH="$HOME/.lmstudio/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH"
LMS="${LMS_BIN:-$HOME/.lmstudio/bin/lms}"
TTL="${LMS_TTL:-1800}"
PARALLEL="${LMS_PARALLEL:-1}"

ORNITH_SOLO_KEY="${ORNITH_SOLO_KEY:-ornith-1.5-k-m}"
ORNITH_DUAL_KEY="${ORNITH_DUAL_KEY:-ornith-1.5-q4-k-m}"
# Planned Hub file: unsloth/Qwen3.8-27B-GGUF → Qwen3.8-27B-UD-Q4_K_XL.gguf (~17.6GB).
# Until that key exists in LMS, set COMPANION_KEY to a ~17GB library id (e.g. twin-turbo).
COMPANION_KEY="${COMPANION_KEY:-qwen3.8-27b-ud-q4-k-xl}"

lane="${1:-status}"

key_present() {
  local want="$1"
  "$LMS" ls 2>/dev/null | awk 'NR>2 {print $1}' | grep -Fxq "$want"
}

require_key() {
  local want="$1" label="$2"
  if ! key_present "$want"; then
    echo "error: LMS library missing $label key '$want'" >&2
    echo "  Download/import the GGUF, rename/key it in Bionic/LMS, or override via env." >&2
    echo "  Tip: lms ls   |   COMPANION_KEY=... $0 dual" >&2
    exit 3
  fi
}

load_one() {
  local model="$1" ctx="$2"
  "$LMS" load -y "$model" -c "$ctx" --gpu max --parallel "$PARALLEL" --ttl "$TTL"
}

case "$lane" in
  status|ps)
    exec "$LMS" ps
    ;;
  unload|clear)
    "$LMS" unload --all || true
    exit 0
    ;;
  roster)
    cat <<ROSTER
lms-thin-load roster (resolved):
  solo/ornith : $ORNITH_SOLO_KEY   (~Q5, Ornith-only)
  dual ornith : $ORNITH_DUAL_KEY   (~Q4, with companion)
  companion   : $COMPANION_KEY     (~17GB)
Refuse: never co-load $ORNITH_SOLO_KEY + companion (Q5+~17GB).
ROSTER
    "$LMS" ls || true
    exit 0
    ;;
  ornith|cron|solo)
    require_key "$ORNITH_SOLO_KEY" "Ornith solo/Q5"
    "$LMS" unload --all 2>/dev/null || true
    load_one "$ORNITH_SOLO_KEY" 131072
    "$LMS" ps
    echo "thin-load ok: solo $ORNITH_SOLO_KEY ctx=131072 parallel=$PARALLEL ttl=${TTL}s"
    ;;
  coding|obliterated|hermes|valedia)
    require_key "qwen3.8-27b-obliterated-k" "coding"
    "$LMS" unload --all 2>/dev/null || true
    load_one "qwen3.8-27b-obliterated-k" 65536
    "$LMS" ps
    echo "thin-load ok: qwen3.8-27b-obliterated-k ctx=65536 parallel=$PARALLEL ttl=${TTL}s"
    ;;
  turbo|ruvocal)
    # Prefer explicit turbo-coder key; fall back to twin-turbo if present
    TURBO_KEY="qwen3.8-turbo-coder-k"
    if ! key_present "$TURBO_KEY"; then
      if key_present "qwen3.8-27b-twin-turbo-fable-cold-fusion-709-l-uncensored-nm-dau-neo-mtp"; then
        TURBO_KEY="qwen3.8-27b-twin-turbo-fable-cold-fusion-709-l-uncensored-nm-dau-neo-mtp"
      fi
    fi
    require_key "$TURBO_KEY" "turbo"
    "$LMS" unload --all 2>/dev/null || true
    load_one "$TURBO_KEY" 65536
    "$LMS" ps
    echo "thin-load ok: $TURBO_KEY ctx=65536 parallel=$PARALLEL ttl=${TTL}s"
    ;;
  companion)
    require_key "$COMPANION_KEY" "companion"
    "$LMS" unload --all 2>/dev/null || true
    load_one "$COMPANION_KEY" 65536
    "$LMS" ps
    echo "thin-load ok: companion $COMPANION_KEY ctx=65536 parallel=$PARALLEL ttl=${TTL}s"
    ;;
  dual)
    # Hard refuse if someone overrides dual ornith to the solo/Q5 key
    if [[ "$ORNITH_DUAL_KEY" == "$ORNITH_SOLO_KEY" ]]; then
      echo "error: refuse dual — ORNITH_DUAL_KEY must not equal solo/Q5 key ($ORNITH_SOLO_KEY)" >&2
      echo "  Q5 + ~17GB co-load leaves no usable KV on 64GB. Use Q4 key for dual." >&2
      exit 4
    fi
    require_key "$ORNITH_DUAL_KEY" "Ornith dual/Q4"
    require_key "$COMPANION_KEY" "companion"
    "$LMS" unload --all 2>/dev/null || true
    # Lower ctx on both so KV fits beside two weights
    load_one "$ORNITH_DUAL_KEY" 65536
    load_one "$COMPANION_KEY" 32768
    "$LMS" ps
    echo "dual-load ok: $ORNITH_DUAL_KEY@65536 + $COMPANION_KEY@32768 parallel=$PARALLEL ttl=${TTL}s"
    echo "note: Hermes model.default should match the lane you call (valedia_models.sh hermes …)"
    ;;
  *)
    echo "usage: $0 {solo|ornith|dual|companion|coding|turbo|status|unload|roster}" >&2
    exit 2
    ;;
esac
