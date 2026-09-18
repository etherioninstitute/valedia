#!/usr/bin/env python3
"""
Valedia consistency / drift detector — the guard that keeps the codebase "connected".
Read-only. Fails loudly (exit 1) if any drift class the 2026-09-11 audit found reappears:
  - a manuscript market-functional defined in >1 live file (the v9/v16e-class mismatch)
  - any live import of a non-canonical kernel predictor (must be kernel_predictor_v16e)
  - a shared constant disagreeing across files (e.g. the MiroFish fusion weight)
  - the ML-signal artifact (latest_retrain.json) gone stale (retrainer not running)
  - backup/versioned files sitting in the live tree
Run anytime, or nightly:  python3 ~/.openclaw/workspace/scripts/valedia_consistency_check.py
(c) GOATWORTHY Post LLC.
"""
import os, re, sys, glob, json, time

_HERE = os.path.dirname(os.path.abspath(__file__))
INT = os.path.join(_HERE, "integrations")
MODELS = os.path.expanduser("~/.openclaw/workspace/models")
def live_pys():
    return [f for f in glob.glob(f"{INT}/*.py") if "/archive/" not in f]

P, W, F = "PASS", "WARN", "FAIL"
counts = {P: 0, W: 0, F: 0}
def r(level, name, detail=""):
    counts[level] += 1
    print(f"  [{level}] {name}" + (f" — {detail}" if detail else ""))

print("\n=== Valedia consistency / drift check ===")

# 1. Canonical kernel predictor: only v16e may be imported by live code.
print("\n[1] Kernel predictor is single-source (v16e)")
bad = []
for f in live_pys():
    txt = open(f, encoding="utf-8", errors="ignore").read()
    if re.search(r"import\s+kernel_predictor_v9\b|from\s+kernel_predictor_v9\b", txt) \
       or re.search(r"import\s+kernel_predictor\b(?!_v16e)|from\s+kernel_predictor\b(?!_v16e)", txt):
        bad.append(os.path.basename(f))
r(F, "no non-canonical kernel imports", f"offenders: {bad}") if bad else r(P, "all kernel imports use v16e")

# 2. Manuscript market-functionals defined in exactly one live file.
print("\n[2] Manuscript market-math is single-source")
functionals = ["ns_trend_noise", "ns_cascade", "rh_D", "rh_se", "rh_conv", "rh_prod",
               "bsd_S2", "bsd_flow", "ym_gap", "hodge_coh", "rosetta_stab",
               "adaptive_svd", "clustering_horizon", "lyapunov_dom"]
dup = {}
for fn in functionals:
    defs = [os.path.basename(f) for f in live_pys()
            if re.search(rf"^def {fn}\b", open(f, encoding='utf-8', errors='ignore').read(), re.M)]
    if len(defs) > 1:
        dup[fn] = defs
r(F, "no functional defined in >1 file", f"DRIFT: {dup}") if dup else r(P, "each market functional defined once")

# 3. Shared constants agree across files.
print("\n[3] Shared constants agree")
def weight_in(fname, line_pat):
    p = f"{INT}/{fname}"
    if not os.path.exists(p):
        return None
    for line in open(p, encoding='utf-8', errors='ignore'):
        if re.search(line_pat, line):
            code = line.split("#")[0]  # ignore inline comments
            nums = re.findall(r"0\.\d+", code)
            if nums:
                return nums[-1]  # the weight is the last decimal literal on the live line
    return None
ub_w = weight_in("unified_brain.py", r"mirofish_score\s*\*")           # the fusion line
qe_w = weight_in("quant_engine.py", r"intel_sources\.append.*MiroFish")  # the live append (not the docstring example)
hard = {k: v for k, v in {"unified_brain": ub_w, "quant_engine": qe_w}.items() if v is not None}
if not hard:
    r(P, "MiroFish weight sourced from valedia_config (no hardcoded copies)")
elif len(hard) == 2 and len(set(hard.values())) == 1:
    r(W, "MiroFish weight hardcoded but consistent", f"{hard} — move to valedia_config.MIROFISH_WEIGHT")
else:
    r(F, "MiroFish weight hardcoded/divergent (drift reintroduced)", f"{hard}")

# 4. ML-signal artifact freshness (the retrainer must actually run).
print("\n[4] ML retrain artifact is fresh")
lr = f"{MODELS}/latest_retrain.json"
if os.path.exists(lr):
    age_h = (time.time() - os.path.getmtime(lr)) / 3600
    r(P if age_h < 48 else F, f"latest_retrain.json age {age_h:.0f}h",
      "" if age_h < 48 else "STALE — ml_retrain cron not producing; live ML signal degraded")
else:
    r(F, "latest_retrain.json missing", "ML signal is neutral across the stack")

# 5. No backup/versioned files in the live tree.
print("\n[5] No dead/backup files in live tree")
junk = [os.path.basename(f) for f in glob.glob(f"{INT}/*")
        if re.search(r"\.bak$|_backup|_old|_copy", os.path.basename(f)) and "/archive/" not in f]
r(P, "live tree clean") if not junk else r(W, "backup/versioned files present", f"{junk}")

# 6. Kalshi fees route through the canonical per-contract helper (no flat-rate arithmetic).
print("\n[6] Kalshi fees single-source (fee_engine.kalshi_fee_per_contract)")
flat = []
for f in live_pys():
    b = os.path.basename(f)
    if b in ("fee_engine.py", "forward_test_ledger.py"):
        continue
    code = "\n".join(l.split("#")[0] for l in open(f, encoding="utf-8", errors="ignore"))
    if re.search(r"[-*]\s*KALSHI_FEE(_RATE)?\b", code) or re.search(r"market_price\s*\*\s*fees_pct", code):
        flat.append(b)
r(F, "flat Kalshi-fee arithmetic reappeared", f"{flat}") if flat else r(P, "Kalshi fees use fee_engine.kalshi_fee_per_contract")

# 7. Kelly fraction single-source (valedia_config.KELLY_SAFETY_FRACTION).
print("\n[7] Kelly: half-Kelly fraction + dynamic cap, single-source")
kbad = []
for b in ("kelly_sizing.py", "quant_engine.py", "sports_engine.py"):
    p = f"{INT}/{b}"
    if not os.path.exists(p):
        continue
    code = "\n".join(l.split("#")[0] for l in open(p, encoding="utf-8", errors="ignore"))
    if re.search(r"kelly\w*\s*/\s*2\b", code) or re.search(r"fraction\s*=\s*0\.25", code) or re.search(r"full_kelly\s*\*\s*0\.\d+", code):
        kbad.append(b + ":hardcoded-fraction")
# the two capping sizers must use the dynamic cap, not a flat per-trade number
for b in ("kelly_sizing.py", "quant_engine.py"):
    p = f"{INT}/{b}"
    if os.path.exists(p) and "dynamic_per_trade_cap" not in open(p, encoding="utf-8", errors="ignore").read():
        kbad.append(b + ":no-dynamic-cap")
r(F, "Kelly drift (hardcoded fraction or flat cap)", f"{kbad}") if kbad else r(P, "half-Kelly via KELLY_SAFETY_FRACTION + dynamic_per_trade_cap")

# 8. Live fusion is valedia_config.FUSION_WEIGHTS; signal_fusion is non-live.
print("\n[8] Fusion single-source (valedia_config.FUSION_WEIGHTS)")
ub = open(f"{INT}/unified_brain.py", encoding="utf-8", errors="ignore").read()
sf = open(f"{INT}/signal_fusion.py", encoding="utf-8", errors="ignore").read() if os.path.exists(f"{INT}/signal_fusion.py") else ""
vc_txt = open(f"{INT}/valedia_config.py", encoding="utf-8", errors="ignore").read()
fusion_ok = "FUSION_WEIGHTS" in vc_txt and "fusion_weights_for" in ub
live_off = bool(re.search(r"LIVE_PATH\s*=\s*False", sf))
inline = bool(re.search(r"kernel_weight\s*=\s*0\.3[05]", ub))
if fusion_ok and live_off and not inline:
    r(P, "live fusion = FUSION_WEIGHTS; signal_fusion LIVE_PATH=False")
else:
    r(F, "fusion drift (second live brain or missing FUSION_WEIGHTS)",
      f"fusion_ok={fusion_ok} live_off={live_off} inline={inline}")

# 9. No second fee table on the brain.
print("\n[9] Fee table single-source (fee_engine.STATIC_FEES)")
fees_dict = bool(re.search(r"^FEES\s*=", ub, re.M))
gate_ok = "roundtrip_cost" in ub and "use_static=True" in ub
if fees_dict or not gate_ok:
    r(F, "unified_brain.FEES still present or fee gate not using fee_engine",
      f"FEES_dict={fees_dict} gate_ok={gate_ok}")
else:
    r(P, "brain fee gate uses fee_engine.STATIC_FEES (use_static)")

# 10. Rebalancer auto-exec default False; scalper/weather live off.
print("\n[10] Rebalancer AUTOEXEC=False; scalper/weather live off")
bad10 = []
if not re.search(r"^REBALANCER_AUTOEXEC\s*=\s*False\b", vc_txt, re.M):
    bad10.append("REBALANCER_AUTOEXEC")
if not re.search(r"^SCALPER_LIVE\s*=\s*False\b", vc_txt, re.M):
    bad10.append("SCALPER_LIVE")
if not re.search(r"^WEATHER_LIVE\s*=\s*False\b", vc_txt, re.M):
    bad10.append("WEATHER_LIVE")
if "alp_buy_fn" in ub.split("# ── Scalper", 1)[-1].split("# ── Summary", 1)[0]:
    bad10.append("scalper-alp_buy")
wx = open(f"{INT}/weather_arb.py", encoding="utf-8", errors="ignore").read()
wx_exec = wx.split("def execute_weather_trades", 1)[-1].split("def run", 1)[0]
if "create_order(" in wx_exec:
    bad10.append("weather-create_order")
if bad10:
    r(F, "live-adjacent kills incomplete", f"{bad10}")
else:
    r(P, "AUTOEXEC=False; scalper/weather cannot place live buys")

print(f"\n=== {counts[P]} PASS · {counts[W]} WARN · {counts[F]} FAIL ===")
sys.exit(1 if counts[F] else 0)
