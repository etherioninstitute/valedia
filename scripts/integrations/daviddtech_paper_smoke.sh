#!/bin/bash
# POST entry+exit for the three paper strategies to 127.0.0.1:8788.
# Assert create_order=false and no passphrase in jsonl.
# Stops the desk only if this script started it. Never signals port 8787.
set -euo pipefail
set +m

PORT="${DAVIDDTECH_WEBHOOK_PORT:-8788}"
if [[ "$PORT" == "8787" ]]; then
  echo "refusing port 8787 (Hermes)" >&2
  exit 2
fi
export DAVIDD_LIVE=0
export DAVIDDTECH_WEBHOOK_PORT="$PORT"

ROOT="${DAVIDDTECH_ROOT:-$HOME/.openclaw/workspace-v2}"
export DAVIDDTECH_JSONL="$ROOT/data/daviddtech-paper/decisions.jsonl"
URL="http://127.0.0.1:${PORT}"
BIN="${DAVIDDTECH_PAPER_BIN:-$HOME/bin/daviddtech-paper-webhook}"

load_pass() {
  local file="$1" line key val q
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == export\ * ]] && line="${line#export }"
    [[ "$line" == DAVIDDTECH_WEBHOOK_PASSPHRASE=* || "$line" == TRADINGVIEW_WEBHOOK_PASSPHRASE=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ ${#val} -ge 2 ]]; then
      q="${val:0:1}"
      if [[ ( "$q" == '"' || "$q" == "'" ) && "${val: -1}" == "$q" ]]; then
        val="${val:1:${#val}-2}"
      fi
    fi
    [[ -n "${!key:-}" ]] && continue
    printf -v "$key" '%s' "$val"
    export "$key"
  done < "$file"
}

load_pass "$ROOT/.env"
load_pass "$HOME/.openclaw/workspace/.env"
load_pass "$HOME/.openclaw/.env"
export DAVIDD_LIVE=0

if [[ -z "${DAVIDDTECH_WEBHOOK_PASSPHRASE:-}" && -z "${TRADINGVIEW_WEBHOOK_PASSPHRASE:-}" ]]; then
  echo "webhook passphrase is unset" >&2
  exit 1
fi

started=0
pid=""
log=""
err=""
cleanup() {
  local rc=$?
  if [[ "$started" == 1 && -n "$pid" ]]; then
    local hermes=""
    hermes="$(lsof -nP -t -iTCP:8787 -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
    if [[ -n "$hermes" && "$pid" == "$hermes" ]]; then
      echo "refusing to stop the 8787 listener" >&2
    elif kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      local _
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
      wait "$pid" 2>/dev/null || true
    fi
  fi
  if [[ -n "$log" || -n "$err" ]]; then
    set +e
    python3 - "$log" "$err" << 'PY'
import os, pathlib, sys
secrets = [os.environ.get("DAVIDDTECH_WEBHOOK_PASSPHRASE") or "", os.environ.get("TRADINGVIEW_WEBHOOK_PASSPHRASE") or ""]
secrets = [s for s in secrets if s]
hits = 0
for raw in sys.argv[1:]:
    if not raw:
        continue
    path = pathlib.Path(raw)
    if not path.exists():
        continue
    text = path.read_text(errors="replace")
    if any(s in text for s in secrets):
        hits += 1
        path.write_text("redacted\n")
print("log_passphrase_hits=%s" % hits)
raise SystemExit(6 if hits else 0)
PY
    scan_rc=$?
    set -e
    rm -f "$log" "$err"
    if [[ "$scan_rc" -ne 0 && "$rc" -eq 0 ]]; then
      rc=$scan_rc
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT

health_file="$(mktemp)"
bind_ok=0
if curl -sf --max-time 2 "$URL/health" >"$health_file"; then
  bind_ok=1
else
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port ${PORT} is open but /health failed; not posting" >&2
    rm -f "$health_file"
    exit 3
  fi
  log="$(mktemp)"
  err="$(mktemp)"
  "$BIN" >"$log" 2>"$err" &
  pid=$!
  disown "$pid" 2>/dev/null || true
  started=1
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -sf --max-time 1 "$URL/health" >"$health_file"; then
      bind_ok=1
      break
    fi
    sleep 0.2
  done
fi

if [[ "$bind_ok" -ne 1 ]]; then
  echo "health did not come up on port ${PORT}" >&2
  rm -f "$health_file"
  exit 3
fi

if [[ "$started" == 1 ]]; then
  got="$(cat "$log")"
  if [[ "$got" != "http://127.0.0.1:${PORT}/health" ]]; then
    echo "operator stdout was not the health URL only" >&2
    rm -f "$health_file"
    exit 7
  fi
fi

bind="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $9; exit}')"
case "$bind" in
  127.0.0.1:"$PORT") ;;
  *)
    echo "refusing bind ${bind:-unknown}" >&2
    rm -f "$health_file"
    exit 3
    ;;
esac

hdr="$(curl -sSI --max-time 2 "$URL/health" | tr -d '\r' | awk 'BEGIN{IGNORECASE=1} $1=="Server:" {print $2; exit}')"
if [[ "$hdr" == *Hermes* ]]; then
  echo "port ${PORT} is Hermes; not posting" >&2
  rm -f "$health_file"
  exit 3
fi

python3 - "$health_file" << 'PY'
import json, sys
p = json.load(open(sys.argv[1]))
if p.get("create_order") is not False or p.get("live") is not False or p.get("mode") != "PAPER/SHADOW":
    print("health_fence_fail")
    raise SystemExit(3)
print("health_ok mode=%s create_order=false" % p.get("mode"))
PY
rm -f "$health_file"

python3 << 'PY'
import json, os, urllib.error, urllib.request
from datetime import datetime, timezone

port = os.environ["DAVIDDTECH_WEBHOOK_PORT"]
pw = os.environ.get("DAVIDDTECH_WEBHOOK_PASSPHRASE") or os.environ.get("TRADINGVIEW_WEBHOOK_PASSPHRASE") or ""
if not pw:
    raise SystemExit("webhook passphrase is unset")
url = "http://127.0.0.1:%s/tradingview-to-webhook-order" % port
jsonl = os.environ["DAVIDDTECH_JSONL"]

def lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [ln for ln in f if ln.strip()]

before_lines = lines(jsonl)
before = len(before_lines)
specs = [
    ("Waddah_1H_Anchor", "BTCUSDT", 65000.0, 0.01, 0.5),
    ("Trendhoo_45M", "SOLUSDT", 150.0, 1.0, 0.3),
    ("TopG_5M_Scalp", "ETHUSDT", 3500.0, 0.1, 0.2),
]
actions = ("entry", "exit")
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ok_n = 0
for sid, ticker, price, vol, risk in specs:
    for action in actions:
        body = {
            "passphrase": pw,
            "timestamp": ts,
            "strategy_id": sid,
            "ticker": ticker,
            "action": action,
            "subaccount": "paper",
            "entry_price": price,
            "volume": vol,
            "portfolio_risk": risk,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read()
            code = e.code
        text = raw.decode(errors="replace")
        if pw in text:
            print("response_leaked_passphrase")
            raise SystemExit(4)
        payload = json.loads(text or "{}")
        dec = payload.get("decision") or {}
        if (
            code != 200
            or payload.get("create_order") is not False
            or payload.get("live") is not False
            or dec.get("strategy_id") != sid
            or dec.get("action") != action
        ):
            print(
                "post_fail strategy_id=%s action=%s http=%s create_order=%s error=%s"
                % (sid, action, code, payload.get("create_order"), payload.get("error"))
            )
            raise SystemExit(4)
        ok_n += 1

after_lines = lines(jsonl)
new = after_lines[before:]
expect = [(s, a) for s, *_rest in specs for a in actions]
if len(new) != 6:
    print("jsonl_count_mismatch before=%s after=%s new=%s" % (before, len(after_lines), len(new)))
    raise SystemExit(5)
got = []
hits = 0
for ln in new:
    if "passphrase" in ln.lower() or pw in ln:
        hits += 1
        continue
    row = json.loads(ln)
    got.append((row.get("strategy_id"), row.get("action")))
    if row.get("create_order") is not False or row.get("live") is not False:
        print("jsonl_fence_fail strategy_id=%s" % row.get("strategy_id"))
        raise SystemExit(5)
    if "davidd_live_ignored" in str(row.get("reason") or ""):
        print("jsonl_live_flag strategy_id=%s" % row.get("strategy_id"))
        raise SystemExit(5)
whole = "".join(after_lines)
if hits or "passphrase" in whole.lower() or pw in whole:
    print("passphrase_hits=%s" % (hits or 1))
    raise SystemExit(6)
if got != expect:
    print("jsonl_order_mismatch")
    raise SystemExit(5)
print(
    "posts=%s http_200=%s create_order_false=%s jsonl_before=%s jsonl_after=%s passphrase_hits=0"
    % (ok_n, ok_n, ok_n, before, len(after_lines))
)
PY
