#!/bin/zsh
# Valedia local model switcher — list/show/set Hermes; MiroFish bionic|ollama flip.
# Fence: never touches trading flags or venue keys.
set -euo pipefail
SELF="$0"
LMS="${VALEDIA_LMS_URL:-http://127.0.0.1:1234}"
MF_OLLAMA="${VALEDIA_MIROFISH_OLLAMA:-http://127.0.0.1:11435}"
HERMES_CFG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"
MIROFISH_ENV="${MIROFISH_ENV:-$HOME/mirofish-offline/.env}"

usage() {
  cat <<'U'
Usage:
  valedia_models.sh list
  valedia_models.sh show
  valedia_models.sh hermes <lms-model-id>
  valedia_models.sh preset chat|reason|turbo   # Hermes only in v1
  valedia_models.sh mirofish-sync <tag> [--write]
  valedia_models.sh mirofish-report <tag> [--write]
  valedia_models.sh mirofish-inference bionic|ollama [--write]
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

mf_env() {
  python3 - <<'PY'
from pathlib import Path
import os
p = Path(os.environ["MIROFISH_ENV"])
want = ("MIROFISH_INFERENCE","LLM_BASE_URL","LLM_MODEL_NAME","MIROFISH_REPORT_MODEL",
        "EMBEDDING_API","EMBEDDING_MODEL","EMBEDDING_BASE_URL","OPENAI_API_BASE_URL")
got = {}
if p.exists():
    for line in p.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1)
        if k in want: got[k]=v
for k in want:
    print(f"{k}={got.get(k,'')}")
PY
}

cmd_list() {
  echo "== LMS $LMS =="
  lms_list || true
  echo
  echo "== MiroFish Ollama $MF_OLLAMA (fallback) =="
  mf_list || true
}

cmd_show() {
  echo "== Hermes ($HERMES_CFG) =="
  if [[ -f "$HERMES_CFG" ]]; then
    python3 - <<PY
import re
from pathlib import Path
t = Path("$HERMES_CFG").read_text()
m = re.search(r"^model:\s*\n(?:.*\n)*?^\s+default:\s*(\S+)", t, re.M)
print("default:", m.group(1) if m else "(not found)")
b = re.search(r"base_url:\s*(\S+)", t)
print("base_url:", b.group(1) if b else "(not found)")
PY
  else
    echo "missing config"
  fi
  echo
  echo "== MiroFish ($MIROFISH_ENV) =="
  MIROFISH_ENV="$MIROFISH_ENV" mf_env | sed 's/^/  /'
  echo "  LMS ids:"
  lms_list | sed 's/^/    /' || true
  echo "  Ollama tags:"
  mf_list | sed 's/^/    /' || true
}

cmd_hermes() {
  local id="${1:-}"
  [[ -n "$id" ]] || usage
  [[ -f "$HERMES_CFG" ]] || { echo "no $HERMES_CFG"; exit 1; }
  if ! lms_list | grep -Fxq "$id"; then
    echo "Refuse: '$id' not in LMS /v1/models. Run: $SELF list"
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
      local turbo
      turbo=$(lms_list | grep -i 'turbo' | head -1 || true)
      if [[ -z "$turbo" ]]; then
        echo "No turbo id loaded in LMS. Load one, then: $SELF list"
        exit 1
      fi
      cmd_hermes "$turbo"
      ;;
    *) echo "presets: chat | reason | turbo"; exit 2 ;;
  esac
}

cmd_mf_set() {
  local key="$1" tag="${2:-}" write="${3:-}"
  [[ -n "$tag" ]] || usage
  case "$key" in
    LLM_MODEL_NAME|MIROFISH_REPORT_MODEL) ;;
    *) echo "Refuse: unknown MiroFish key $key"; exit 1 ;;
  esac
  local have=0
  if lms_list 2>/dev/null | grep -Fxq "$tag"; then have=1; fi
  if mf_list 2>/dev/null | grep -Fxq "$tag"; then have=1; fi
  if [[ "$have" -ne 1 ]]; then
    echo "Refuse: '$tag' not in LMS /v1/models or Ollama :11435 tags. Run: $SELF list"
    exit 1
  fi
  echo "export ${key}=${tag}"
  if [[ "$write" != "--write" ]]; then
    echo "(dry) pass --write to persist to $MIROFISH_ENV"
    return 0
  fi
  [[ -f "$MIROFISH_ENV" ]] || { echo "no $MIROFISH_ENV"; exit 1; }
  local ts bak
  ts=$(date +%Y%m%d-%H%M%S)
  bak="${MIROFISH_ENV}.bak-modelswitch-${ts}"
  cp "$MIROFISH_ENV" "$bak"
  echo "backup: $bak"
  MIROFISH_ENV="$MIROFISH_ENV" python3 - "$key" "$tag" <<'PY'
from pathlib import Path
import os, re, sys
p = Path(os.environ["MIROFISH_ENV"])
key, tag = sys.argv[1], sys.argv[2]
t = p.read_text()
new, n = re.subn(rf"^{re.escape(key)}=.*$", f"{key}={tag}", t, count=1, flags=re.M)
if n == 0:
    if t and not t.endswith("\n"):
        t += "\n"
    new = t + f"{key}={tag}\n"
p.write_text(new)
print(f"{key} -> {tag}")
PY
}

cmd_mf_inference() {
  local mode="${1:-}" write="${2:-}"
  case "$mode" in
    bionic|ollama) ;;
    *) echo "usage: $SELF mirofish-inference bionic|ollama [--write]"; exit 2 ;;
  esac
  local pairs
  if [[ "$mode" == "bionic" ]]; then
    pairs=(
      "MIROFISH_INFERENCE=bionic"
      "LLM_API_KEY=lm-studio"
      "LLM_BASE_URL=http://127.0.0.1:1234/v1"
      "LLM_MODEL_NAME=qwen3.8-27b-obliterated-k"
      "MIROFISH_REPORT_MODEL=ornith-1.5-k-m"
      "EMBEDDING_API=openai"
      "EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5"
      "EMBEDDING_BASE_URL=http://127.0.0.1:1234"
      "OPENAI_API_KEY=lm-studio"
      "OPENAI_API_BASE_URL=http://127.0.0.1:1234/v1"
    )
  else
    pairs=(
      "MIROFISH_INFERENCE=ollama"
      "LLM_API_KEY=ollama"
      "LLM_BASE_URL=http://localhost:11435/v1"
      "LLM_MODEL_NAME=qwen3-8b-mf:latest"
      "MIROFISH_REPORT_MODEL=ornith-1.5-mf:latest"
      "EMBEDDING_API=ollama"
      "EMBEDDING_MODEL=nomic-embed-text"
      "EMBEDDING_BASE_URL=http://localhost:11435"
      "OPENAI_API_KEY=ollama"
      "OPENAI_API_BASE_URL=http://localhost:11435/v1"
    )
  fi
  echo "planned $mode:"
  printf '  %s\n' "${pairs[@]}"
  echo "Restart MiroFish backend after --write. Do not stop com.burton.ollama-mirofish."
  if [[ "$write" != "--write" ]]; then
    echo "(dry) pass --write to persist to $MIROFISH_ENV"
    return 0
  fi
  [[ -f "$MIROFISH_ENV" ]] || { echo "no $MIROFISH_ENV"; exit 1; }
  local ts bak
  ts=$(date +%Y%m%d-%H%M%S)
  bak="${MIROFISH_ENV}.bak-modelswitch-${ts}"
  cp "$MIROFISH_ENV" "$bak"
  echo "backup: $bak"
  MIROFISH_ENV="$MIROFISH_ENV" python3 - "${pairs[@]}" <<'PY'
from pathlib import Path
import os, re, sys
p = Path(os.environ["MIROFISH_ENV"])
t = p.read_text()
for item in sys.argv[1:]:
    key, tag = item.split("=", 1)
    new, n = re.subn(rf"^{re.escape(key)}=.*$", f"{key}={tag}", t, count=1, flags=re.M)
    if n == 0:
        if t and not t.endswith("\n"):
            t += "\n"
        t = t + f"{key}={tag}\n"
    else:
        t = new
p.write_text(t)
print("MIROFISH_INFERENCE ->", dict(x.split("=",1) for x in sys.argv[1:])["MIROFISH_INFERENCE"])
PY
}

case "${1:-}" in
  list) cmd_list ;;
  show) cmd_show ;;
  hermes) shift; cmd_hermes "$@" ;;
  preset) shift; cmd_preset "$@" ;;
  mirofish-sync) shift; cmd_mf_set LLM_MODEL_NAME "$@" ;;
  mirofish-report) shift; cmd_mf_set MIROFISH_REPORT_MODEL "$@" ;;
  mirofish-inference) shift; cmd_mf_inference "$@" ;;
  *) usage ;;
esac
