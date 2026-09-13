#!/usr/bin/env python3
"""
MiroFish end-to-end health check ("/debug for MiroFish").
Verifies every stage of the chain — serving, config split, the real-swarm signal path,
the accuracy-ledger evidence loop, and the BRAIN OPEN gate — and prints PASS/WARN/FAIL
per stage with a specific reason. Read-only: it diagnoses, it does not change anything.
Run anytime:  python3 ~/.openclaw/workspace/scripts/mirofish_healthcheck.py
(c) GOATWORTHY Post LLC.
"""
import json, os, socket, subprocess, sys, urllib.request, urllib.error
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
MODELS = f"{HOME}/.openclaw/workspace/models"
STATE = f"{HOME}/.openclaw/state"
ENV = f"{HOME}/mirofish-offline/.env"
OLLAMA = "http://127.0.0.1:11435"
BACKEND = "http://127.0.0.1:5001/api"

P, W, F = "\033[92mPASS\033[0m", "\033[93mWARN\033[0m", "\033[91mFAIL\033[0m"
counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
def r(level, name, detail=""):
    tag = {"PASS": P, "WARN": W, "FAIL": F}[level]; counts[level] += 1
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))

def http(url, data=None, timeout=8):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)

def port_open(host, port, t=3):
    try:
        with socket.create_connection((host, port), t): return True
    except Exception: return False

def envval(key):
    try:
        for line in open(ENV):
            line = line.strip()
            if line.startswith(key + "="): return line.split("=", 1)[1]
    except Exception: pass
    return None

print("\n=== MiroFish end-to-end health check ===")

# 1. Serving: Ollama :11435 + required models
print("\n[1] Serving (dedicated MiroFish Ollama :11435)")
try:
    tags = http(f"{OLLAMA}/api/tags")
    have = {m["name"] for m in tags.get("models", [])}
    for need in ("ornith-1.5-mf:latest", "qwen3-8b-mf:latest", "nomic-embed-text:latest"):
        r("PASS" if need in have else "FAIL", f"model present: {need}",
          "" if need in have else "MISSING — pipeline stage will fail")
except Exception as e:
    r("FAIL", "Ollama :11435 reachable", str(e)[:80])

# 2. ornith-1.5-mf num_ctx cap (the 256K-blowup guard)
print("\n[2] ornith-1.5-mf num_ctx cap (KV-blowup guard)")
try:
    show = subprocess.run(["ollama", "show", "ornith-1.5-mf:latest"],
                          env={**os.environ, "OLLAMA_HOST": "127.0.0.1:11435"},
                          capture_output=True, text=True, timeout=20).stdout
    ctx = next((l.split()[-1] for l in show.splitlines() if "num_ctx" in l.lower()), "?")
    r("PASS" if ctx == "8192" else "WARN", f"num_ctx = {ctx}",
      "" if ctx == "8192" else "expected 8192 (cap prevents 256K KV blowup)")
except Exception as e:
    r("WARN", "could not read ornith-1.5-mf modelfile", str(e)[:60])

# 3. Backend + Neo4j
print("\n[3] Backend & graph")
try:
    http(BACKEND, timeout=5); r("PASS", "MiroFish backend :5001 up")
except urllib.error.HTTPError:
    r("PASS", "MiroFish backend :5001 up", "(server responds; bare /api has no route)")
except Exception as e:
    r("FAIL", "MiroFish backend :5001", str(e)[:80])
r("PASS" if port_open("127.0.0.1", 7687) else "FAIL", "Neo4j :7687 reachable")

# 4. Config split (report vs swarm model)
print("\n[4] Model split (.env) — report-only ornith")
default_m, report_m = envval("LLM_MODEL_NAME"), envval("MIROFISH_REPORT_MODEL")
r("PASS" if default_m == "qwen3-8b-mf:latest" else "WARN", f"DEFAULT/sync model (LLM_MODEL_NAME) = {default_m}",
  "" if default_m == "qwen3-8b-mf:latest" else "sync stages need the FAST model or the runner times out")
r("PASS" if report_m == "ornith-1.5-mf:latest" else "WARN", f"REPORT-only model (MIROFISH_REPORT_MODEL) = {report_m}")

# 5. Signal freshness + source (real swarm vs lightweight fallback)
print("\n[5] Latest signal (real swarm vs fallback)")
sig_path = f"{MODELS}/mirofish_signal.json"
if os.path.exists(sig_path):
    sig = json.load(open(sig_path))
    src = str(sig.get("source", "?"))
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(
        sig["ts"].replace("Z", "+00:00"))).total_seconds() / 3600 if sig.get("ts") else 999
    if "lightweight" in src.lower():
        r("WARN", f"source = {src}", f"last run FELL BACK (not a real swarm); age {age_h:.0f}h — will NOT be scored")
    else:
        r("PASS", f"source = {src}", f"real swarm signal, age {age_h:.0f}h")
else:
    r("FAIL", "no mirofish_signal.json", "nothing has produced a signal")

# 6. Accuracy ledger — the evidence loop that unlocks weight > 10%
print("\n[6] Accuracy evidence loop (gates weight > 10%)")
led = f"{MODELS}/mirofish_accuracy_ledger.jsonl"
if os.path.exists(led):
    rows = [json.loads(l) for l in open(led) if l.strip()]
    preds = [x for x in rows if x.get("type") == "prediction"]
    scored = [x for x in rows if x.get("type") == "result"]
    hits = sum(1 for x in scored if x.get("hit"))
    acc = (hits / len(scored) * 100) if scored else None
    r("PASS" if preds else "FAIL", f"recorded predictions: {len(preds)}",
      "" if preds else "empty — MiroFish can NEVER earn past 10%")
    if scored:
        r("PASS" if acc and acc > 55 else "WARN", f"scored: {len(scored)}, accuracy {acc:.0f}%",
          "eligible to raise weight" if acc and acc > 55 else "below 55% gate — keep at 10%")
    else:
        r("WARN", "scored predictions: 0", f"{len(preds)} pending (5-day horizon) — too early or loop stalled")
else:
    r("FAIL", "no accuracy ledger", "real swarm has never recorded a prediction — evidence loop is OPEN")

# 7. BRAIN OPEN gate wiring
print("\n[7] BRAIN OPEN gate (dynamic MiroFish->open trigger)")
gate = f"{HOME}/.openclaw/workspace/scripts/brain_open_gated.sh"
r("PASS" if os.access(gate, os.X_OK) else "FAIL", "brain_open_gated.sh executable")
try:
    ct = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    r("PASS" if "brain_open_gated.sh" in ct else "FAIL", "crontab routes 06:35 BRAIN OPEN through the gate")
except Exception as e:
    r("WARN", "could not read crontab", str(e)[:50])
mk = f"{STATE}/mirofish_ready"
r("PASS" if os.path.exists(mk) else "WARN", "mirofish_ready marker exists",
  open(mk).read().strip() if os.path.exists(mk) else "not written yet (no run since wiring)")

# 8. Thinking-off live check on the report model
print("\n[8] Report model non-thinking (reasoning_effort:none)")
try:
    out = http(f"{OLLAMA}/v1/chat/completions", {
        "model": "ornith-1.5-mf:latest", "messages": [{"role": "user", "content": "Reply with the word ok."}],
        "max_tokens": 20, "reasoning_effort": "none", "options": {"num_ctx": 8192}}, timeout=120)
    c = out["choices"][0]["message"]["content"]
    r("PASS" if "<think>" not in c.lower() else "FAIL", "ornith-1.5-mf responds non-thinking",
      f"reply={c.strip()[:40]!r}")
except Exception as e:
    r("WARN", "live non-thinking check skipped", str(e)[:60])

# Summary
print(f"\n=== {counts['PASS']} PASS · {counts['WARN']} WARN · {counts['FAIL']} FAIL ===")
sys.exit(1 if counts["FAIL"] else 0)
