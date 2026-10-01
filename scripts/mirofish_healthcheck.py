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
BIONIC = "http://127.0.0.1:1234"
OLLAMA = "http://127.0.0.1:11435"
BACKEND = "http://127.0.0.1:5001/api"

BIONIC_NEED = (
    "qwen/qwen3.8-27b",
    "ornith-1.5-35b-a3b-mlx",
    "text-embedding-nomic-embed-text-v1.5",
)
OLLAMA_NEED = (
    "ornith-1.5-mf:latest",
    "qwen3-8b-mf:latest",
    "nomic-embed-text:latest",
)

P, W, F = "\033[92mPASS\033[0m", "\033[93mWARN\033[0m", "\033[91mFAIL\033[0m"
counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
def r(level, name, detail=""):
    tag = {"PASS": P, "WARN": W, "FAIL": F}[level]; counts[level] += 1
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))

def envmap():
    out = {}
    try:
        for line in open(ENV):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k] = v
    except Exception:
        pass
    return out

def envval(key, em=None):
    return (em or envmap()).get(key)

def inference_mode(em=None):
    em = em or envmap()
    v = (em.get("MIROFISH_INFERENCE") or "").strip().lower()
    if v in ("bionic", "ollama", "split"):
        return v
    return "bionic" if ":1234" in (em.get("LLM_BASE_URL") or "") else "ollama"

def http(url, data=None, timeout=8, bearer=None):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)

def port_open(host, port, t=3):
    try:
        with socket.create_connection((host, port), t): return True
    except Exception: return False

def bionic_ids():
    d = http(f"{BIONIC}/v1/models", timeout=5)
    return {m.get("id") for m in d.get("data") or [] if m.get("id")}

def ollama_names():
    d = http(f"{OLLAMA}/api/tags", timeout=5)
    return {m.get("name") for m in d.get("models") or [] if m.get("name")}

print("\n=== MiroFish end-to-end health check ===")
em = envmap()
mode = inference_mode(em)
key = envval("LLM_API_KEY", em) or "lm-studio"
print(f"inference: {mode}  (MIROFISH_INFERENCE / LLM_BASE_URL)")

# 1. Serving: Bionic primary + Ollama fallback
print("\n[1] Serving (Bionic :1234 primary, Ollama :11435 fallback)")
try:
    have_b = bionic_ids()
    r("PASS", "Bionic :1234 reachable", f"{len(have_b)} models")
    for need in BIONIC_NEED:
        ok = need in have_b
        if mode == "split" and need == "qwen/qwen3.8-27b":
            lvl = "PASS" if ok else "WARN"  # not on the sync path
        elif mode == "split":
            lvl = "PASS" if ok else "FAIL"  # ornith report + nomic embed
        else:
            lvl = "PASS" if ok else ("FAIL" if mode == "bionic" else "WARN")
        detail = "" if ok else ("not on sync path" if mode == "split" and need.startswith("qwen3.8-27b") else "MISSING")
        r(lvl, f"bionic model: {need}", detail)
except Exception as e:
    r("FAIL" if mode == "bionic" else "WARN", "Bionic :1234 reachable", str(e)[:80])

try:
    have_o = ollama_names()
    r("PASS", "Ollama :11435 reachable (fallback)", f"{len(have_o)} tags")
    for need in OLLAMA_NEED:
        ok = need in have_o
        if mode == "split" and need == "qwen3-8b-mf:latest":
            lvl = "PASS" if ok else "FAIL"
        elif mode == "ollama":
            lvl = "PASS" if ok else "FAIL"
        else:
            lvl = "PASS" if ok else "WARN"
        r(lvl, f"ollama model: {need}", "" if ok else "MISSING — fallback incomplete")
except Exception as e:
    r("FAIL" if mode == "ollama" else "WARN", "Ollama :11435 reachable", str(e)[:80])

# 2. ornith-1.5-mf num_ctx cap (Ollama KV-blowup guard)
print("\n[2] ornith-1.5-mf num_ctx cap (KV-blowup guard, Ollama only)")
if mode == "bionic":
    r("PASS", "skipped on bionic primary", "Ollama cap still applies if you flip MIROFISH_INFERENCE=ollama")
else:
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
print("\n[4] Model split (.env)")
default_m, report_m = envval("LLM_MODEL_NAME", em), envval("MIROFISH_REPORT_MODEL", em)
if mode == "split":
    r("PASS" if default_m == "qwen3-8b-mf:latest" else "FAIL",
      f"SYNC model (LLM_MODEL_NAME) = {default_m}",
      "" if default_m == "qwen3-8b-mf:latest" else "overnight ontology needs the 8B, not the 27B")
    r("PASS" if report_m == "ornith-1.5-35b-a3b-mlx" else "FAIL",
      f"REPORT model (MIROFISH_REPORT_MODEL) = {report_m}")
    r("PASS" if ":11435" in (envval("LLM_BASE_URL", em) or "") else "FAIL",
      f"LLM_BASE_URL = {envval('LLM_BASE_URL', em)}")
    r("PASS" if ":1234" in (envval("LLM_REPORT_BASE_URL", em) or "") else "FAIL",
      f"LLM_REPORT_BASE_URL = {envval('LLM_REPORT_BASE_URL', em)}")
    emb_api = (envval("EMBEDDING_API", em) or "").lower()
    emb_base = envval("EMBEDDING_BASE_URL", em) or ""
    emb_ok = (emb_api == "ollama" and ":11435" in emb_base) or (emb_api == "openai" and ":1234" in emb_base)
    r("PASS" if emb_ok else "WARN",
      f"EMBEDDING_API={envval('EMBEDDING_API', em)} base={emb_base}",
      "" if emb_ok else "split embed should stay on Ollama so a Bionic report load cannot unload it")
elif mode == "bionic":
    want_s, want_r = "qwen/qwen3.8-27b", "ornith-1.5-35b-a3b-mlx"
    r("PASS" if default_m == want_s else "WARN", f"DEFAULT/sync model (LLM_MODEL_NAME) = {default_m}",
      "" if default_m == want_s else "expected qwen/qwen3.8-27b (FAST on Bionic)")
    r("PASS" if report_m == want_r else "WARN", f"REPORT-only model (MIROFISH_REPORT_MODEL) = {report_m}")
    r("PASS" if ":1234" in (envval("LLM_BASE_URL", em) or "") else "FAIL",
      f"LLM_BASE_URL = {envval('LLM_BASE_URL', em)}")
    r("PASS" if (envval("EMBEDDING_API", em) or "").lower() == "openai"
      or ":1234" in (envval("EMBEDDING_BASE_URL", em) or "") else "WARN",
      f"EMBEDDING_API={envval('EMBEDDING_API', em)} base={envval('EMBEDDING_BASE_URL', em)}")
else:
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

# 8. Live chat on the PRIMARY sync model (avoid loading a second LMS LLM)
print("\n[8] Primary sync chat (short)")
sync = envval("LLM_MODEL_NAME", em) or ("qwen/qwen3.8-27b" if mode == "bionic" else "qwen3-8b-mf:latest")
chat_url = f"{BIONIC}/v1/chat/completions" if mode == "bionic" else f"{OLLAMA}/v1/chat/completions"
try:
    out = http(chat_url, {
        "model": sync, "messages": [{"role": "user", "content": "Reply with the word ok."}],
        "max_tokens": 20, "temperature": 0,
        "reasoning_effort": "none", "options": {"num_ctx": 8192}}, timeout=180, bearer=key)
    c = (out["choices"][0]["message"].get("content") or "")
    r("PASS" if c.strip() else "WARN", f"{sync} chat", f"reply={c.strip()[:40]!r}")
except Exception as e:
    r("WARN", "live sync chat skipped/failed", str(e)[:80])

# Summary
print(f"\n=== {counts['PASS']} PASS · {counts['WARN']} WARN · {counts['FAIL']} FAIL ===")
sys.exit(1 if counts["FAIL"] else 0)
