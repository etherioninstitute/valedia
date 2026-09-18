#!/bin/zsh
# Valedia local model switcher — list/show/set Hermes; show MiroFish.
# Fence: never touches trading flags or venue keys.
set -euo pipefail
LMS="${VALEDIA_LMS_URL:-http://127.0.0.1:1234}"
MF_OLLAMA="${VALEDIA_MIROFISH_OLLAMA:-http://127.0.0.1:11435}"
HERMES_CFG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"

usage() {
  cat <<'U'
Usage:
  valedia_models.sh list
  valedia_models.sh show
  valedia_models.sh hermes <lms-model-id>
  valedia_models.sh preset chat|reason|turbo   # Hermes only in v1
U
  exit 2
}

lms_list() {
  curl -s -m 5 "$LMS/v1/models" | python3 -c '
import sys,json
try:
  d=json.load(sys.stdin)
except Exception as e:
  print("LMS unreachable:", e); sys.exit(1)
for m in d.get("data") or []:
  print(m.get("id",""))
'
}

mf_list() {
  curl -s -m 5 "$MF_OLLAMA/api/tags" | python3 -c '
import sys,json
try:
  d=json.load(sys.stdin)
except Exception as e:
  print("MiroFish Ollama :11435 unreachable:", e); sys.exit(0)
for m in d.get("models") or []:
  print(m.get("name") or m.get("model") or "")
' 2>/dev/null || echo "(mirofish ollama down)"
}

cmd_list() {
  echo "== LMS $LMS =="
  lms_list || true
  echo
  echo "== MiroFish Ollama $MF_OLLAMA =="
  mf_list || true
}

cmd_show() {
  echo "== Hermes ($HERMES_CFG) =="
  if [[ -f "$HERMES_CFG" ]]; then
    python3 - <<PY
import re
from pathlib import Path
t = Path("$HERMES_CFG").read_text()
# naive: first default under model:
m = re.search(r"^model:\s*\n(?:.*\n)*?^\s+default:\s*(\S+)", t, re.M)
print("default:", m.group(1) if m else "(not found)")
b = re.search(r"base_url:\s*(\S+)", t)
print("base_url:", b.group(1) if b else "(not found)")
PY
  else
    echo "missing config"
  fi
  echo
  echo "== MiroFish (from healthcheck conventions) =="
  echo "sync/report: see mirofish_healthcheck / env LLM_MODEL_NAME + MIROFISH_REPORT_MODEL"
  echo "Ollama tags:"
  mf_list | sed 's/^/  /' || true
}

cmd_hermes() {
  local id="${1:-}"
  [[ -n "$id" ]] || usage
  [[ -f "$HERMES_CFG" ]] || { echo "no $HERMES_CFG"; exit 1; }
  if ! lms_list | grep -Fxq "$id"; then
    echo "Refuse: '$id' not in LMS /v1/models. Run: $0 list"
    exit 1
  fi
  local ts bak
  ts=$(date +%Y%m%d-%H%M%S)
  bak="${HERMES_CFG}.bak-modelswitch-${ts}"
  cp "$HERMES_CFG" "$bak"
  python3 - <<PY
from pathlib import Path
import re
p = Path("$HERMES_CFG")
t = p.read_text()
# Replace model.default under top-level model: block only (first occurrence of "  default:")
new, n = re.subn(
    r"(^model:\s*\n(?:^[ \t]+(?!default:).*\n)*?^[ \t]+default:\s*)(\S+)",
    r"\1$id",
    t,
    count=1,
    flags=re.M,
)
if n != 1:
    raise SystemExit(f"could not patch model.default (matches={n})")
p.write_text(new)
print("Hermes model.default -> $id")
print("backup:", "$bak")
PY
}

cmd_preset() {
  local name="${1:-}"
  case "$name" in
    chat)   cmd_hermes "qwen3.8-27b-obliterated-k" ;;
    reason) cmd_hermes "ornith-1.5-k-m" ;;
    turbo)
      # prefer twin-turbo id if present
      local turbo
      turbo=$(lms_list | grep -i 'turbo' | head -1 || true)
      if [[ -z "$turbo" ]]; then
        echo "No turbo id loaded in LMS. Load one, then: $0 list"
        exit 1
      fi
      cmd_hermes "$turbo"
      ;;
    *) echo "presets: chat | reason | turbo"; exit 2 ;;
  esac
}

case "${1:-}" in
  list) cmd_list ;;
  show) cmd_show ;;
  hermes) shift; cmd_hermes "$@" ;;
  preset) shift; cmd_preset "$@" ;;
  *) usage ;;
esac
