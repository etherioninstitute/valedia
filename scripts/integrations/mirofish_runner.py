"""MiroFish Autonomous Runner — Valedia Integration
Feeds financial data as seeds → runs swarm prediction → returns to brain.

Pipeline:
  1. Generate seed report from hedge fund intel + market data
  2. Upload to MiroFish API as "Reality Seed"  
  3. Build knowledge graph (entities = market participants)
  4. Run simulation (agents = traders, analysts, institutions)
  5. Extract prediction report
  6. Feed prediction into Valedia's unified brain

(c) GOATWORTHY Post LLC.
"""
import requests, json, time, os, sys
from datetime import datetime, timezone

MIROFISH_URL = "http://localhost:5001/api"
SEED_DIR = os.path.expanduser("~/.openclaw/workspace/mirofish_seeds")

def generate_financial_seed():
    """Create a seed document from current market conditions."""
    sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts/integrations"))
    
    lines = []
    lines.append(f"# Financial Market Analysis — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append("")
    
    # Fear/Greed
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if r.ok:
            fg = r.json()["data"][0]
            lines.append(f"## Market Sentiment")
            lines.append(f"Fear & Greed Index: {fg['value']} ({fg['value_classification']})")
            lines.append("")
    except: pass
    
    # Top movers
    try:
        import yfinance as yf
        for tk, name in [("BTC-USD","Bitcoin"),("ETH-USD","Ethereum"),("SPY","S&P 500"),("QQQ","Nasdaq"),("GLD","Gold")]:
            data = yf.Ticker(tk).history(period="5d")
            if len(data) >= 2:
                ret = (data["Close"].iloc[-1] / data["Close"].iloc[-2] - 1) * 100
                price = data["Close"].iloc[-1]
                lines.append(f"- {name} ({tk}): ${price:.2f} ({ret:+.1f}% 1-day)")
        lines.append("")
    except: pass
    
    # Hedge fund intel if available
    try:
        intel_path = os.path.expanduser("~/.openclaw/workspace/models/hedge_fund_intel.json")
        with open(intel_path) as f:
            intel = json.load(f)
        
        lines.append("## Institutional Intelligence")
        if intel.get("short_squeeze"):
            lines.append("### Short Squeeze Candidates")
            for s in intel["short_squeeze"][:3]:
                lines.append(f"- {s['ticker']}: {s['short_pct']}% short float, ${s.get('price',0):.2f}")
        
        if intel.get("sentiment_divergence"):
            lines.append("### Sentiment vs Fundamentals Divergence")
            for s in intel["sentiment_divergence"][:3]:
                lines.append(f"- {s['ticker']}: PE={s['pe']}, ROE={s['roe']}%, 52w change={s['change52w']}%")
        lines.append("")
    except: pass
    
    # ML retrain signals
    try:
        retrain_path = os.path.expanduser("~/.openclaw/workspace/models/latest_retrain.json")
        with open(retrain_path) as f:
            ml = json.load(f)
        lines.append("## ML Model Signals")
        for r in ml.get("results",[]):
            lines.append(f"- {r['ticker']}: {r['signal']} (buy_prob={r['buy_probability']:.1%}, accuracy={r['accuracy']:.1%})")
        lines.append("")
    except: pass

    lines.append("## Prediction Request")
    lines.append("Given the above market conditions, simulate how institutional traders,")
    lines.append("retail investors, quant funds, and market makers would react over the next")
    lines.append("5 trading days. Focus on: BTC, ETH, SOL, SPY, QQQ price direction,")
    lines.append("key support/resistance levels, and potential catalysts for regime change.")
    
    seed_text = "\n".join(lines)
    
    # Save as file
    seed_path = os.path.join(SEED_DIR, f"financial_seed_{datetime.now().strftime('%Y%m%d')}.md")
    os.makedirs(SEED_DIR, exist_ok=True)
    with open(seed_path, "w") as f:
        f.write(seed_text)
    
    return seed_path, seed_text

def run_simulation(seed_path):
    """Full MiroFish pipeline: upload → build graph → simulate → report."""
    results = {"status": "starting", "steps": []}
    
    # Step 1: Upload seed + generate ontology
    _t0 = time.time()
    def _stage(label):
        print(f"[MiroFish] {label} +{time.time()-_t0:.0f}s", flush=True)
    _stage("Step 1: Uploading seed and generating ontology")
    with open(seed_path, "rb") as f:
        r = requests.post(f"{MIROFISH_URL}/graph/ontology/generate", 
            files={"files": (os.path.basename(seed_path), f, "text/markdown")},
            data={
                "simulation_requirement": "Predict crypto and equity market movements over next 5 trading days. Simulate institutional traders, retail investors, quant funds, and market makers.",
                "project_name": f"Valedia Market Prediction {datetime.now().strftime('%Y-%m-%d')}"
            }, timeout=1200)  # ontology LLM gen is highly variable (37s–16min under GPU contention); generous budget
    
    if not r.ok:
        results["status"] = f"ontology failed: {r.text[:200]}"
        return results
    
    data = r.json()
    if not data.get("success"):
        results["status"] = f"ontology error: {data.get('error','?')}"
        return results
    
    project_id = data["data"]["project_id"]
    results["project_id"] = project_id
    results["steps"].append(f"ontology: {len(data['data'].get('ontology',{}).get('entity_types',[]))} entity types")
    print(f"  Project: {project_id}")
    _stage("ontology done")
    
    # Step 2: Build knowledge graph
    _stage("Step 2: Building knowledge graph")
    r = requests.post(f"{MIROFISH_URL}/graph/build", 
        json={"project_id": project_id}, timeout=600)
    
    if not r.ok:
        results["status"] = f"graph build failed: {r.text[:200]}"
        return results
    
    build_data = r.json()
    results["steps"].append("graph built")
    
    # Wait for graph build to complete
    task_id = build_data.get("data",{}).get("task_id","")
    graph_ok = False
    for _ in range(600):  # max 50 min wait (rich seed measured ~28min on 2026-09-10; margin so an unattended 02:00 cron never hits the wall)
        time.sleep(5)
        status_r = requests.get(f"{MIROFISH_URL}/graph/project/{project_id}", timeout=30)
        if status_r.ok:
            proj = status_r.json().get("data",{})
            if proj.get("status") in ["graph_completed", "completed"]:
                graph_ok = True
                print(f"  graph {proj.get('status')} node_count={proj.get('node_count', proj.get('entity_count', '?'))}", flush=True)
                break
            if proj.get("status") == "failed":
                results["status"] = f"graph build failed: {proj.get('error','?')}"
                return results
    if not graph_ok:
        results["status"] = "graph build timed out before graph_completed"
        return results
    _stage("graph done")
    # Step 3: Create simulation
    _stage("Step 3: Creating simulation")
    r = requests.post(f"{MIROFISH_URL}/simulation/create",
        json={"project_id": project_id}, timeout=60)
    
    if not r.ok:
        results["status"] = f"sim create failed: {r.text[:200]}"
        return results
    
    sim_id = r.json().get("data",{}).get("simulation_id","")
    results["simulation_id"] = sim_id
    
    # Step 4: Prepare (generate agent profiles)
    _stage("Step 4: Preparing agents")
    r = requests.post(f"{MIROFISH_URL}/simulation/prepare",
        json={"simulation_id": sim_id}, timeout=600)
    
    # Wait for prep (per-agent profile + sim-config gen on qwen3:8b; budget 90 min, see loop)
    prepared = False
    for _ in range(1080):  # max 90 min — persona+sim-config gen scales with agent count (25 agents measured ~30min personas alone on 2026-09-10); margin for the unattended cron
        time.sleep(5)
        sr = requests.post(f"{MIROFISH_URL}/simulation/prepare/status",
            json={"simulation_id": sim_id}, timeout=30)
        if sr.ok:
            d = sr.json().get("data", {})
            # MiroFish-Offline: data.status in (ready|completed) or already_prepared
            if d.get("already_prepared") or str(d.get("status", "")).lower() in ("ready", "completed"):
                prepared = True
                break
            if str(d.get("status", "")).lower() in ("failed", "error"):
                results["status"] = f"prepare failed: {d.get('error', '?')}"
                return results
    if not prepared:
        results["status"] = "agents not ready after 90min (prepare timeout) — did not start sim"
        return results
    results["steps"].append("agents prepared")
    _stage("agents prepared")

    # Step 5: Start simulation
    # NOTE (2026-09-10): enable_graph_memory_update is the intended rounds->report bridge (writes
    # each round post into Neo4j via NER so the report's graph tools read them). The API storage
    # bug is fixed (simulation.py) so it now INITIALIZES, BUT on a local 32B it floods the queue:
    # ~100 posts each trigger an NER call that starves the report's own LLM calls -> 600s timeouts.
    # Left OFF until the sim->report sequencing is fixed (drain graph-memory before the report, or a
    # lighter/faster embed-only path). Report still runs on the accurate seed graph + personas.
    _stage("Step 5: Running simulation")
    r = requests.post(f"{MIROFISH_URL}/simulation/start",
        json={"simulation_id": sim_id, "max_rounds": 8}, timeout=60)
    
    if not r.ok:
        results["status"] = f"sim start failed: {r.text[:200]}"
        return results
    
    # Wait for completion (budget ~20 min for full 30-agent/8-round local sim)
    for i in range(600):  # max 50 min — no-cap sim: many agents x rounds
        time.sleep(5)
        sr = requests.get(f"{MIROFISH_URL}/simulation/{sim_id}/run-status", timeout=30)
        if sr.ok:
            d = sr.json().get("data", {})
            # MiroFish-Offline: data.runner_status (idle|running|stopped|completed)
            rstatus = str(d.get("runner_status", "")).lower()
            if rstatus in ("completed", "stopped", "finished"):
                break
            if rstatus in ("failed", "error"):
                results["status"] = "simulation failed"
                return results
            if i % 12 == 0:
                print(f"  ... round {d.get('current_round', '?')}")
    
    results["steps"].append("simulation completed")
    _stage("simulation completed")
    
    # Step 6: Generate report (MiroFish-Offline: async generate -> poll status -> fetch content)
    _stage("Step 6: Generating prediction report")
    r = requests.post(f"{MIROFISH_URL}/report/generate",
        json={"simulation_id": sim_id}, timeout=60)
    if not r.ok:
        results["status"] = f"report start failed: {r.text[:200]}"
        return results
    gd = r.json().get("data", {})
    report_id = gd.get("report_id", "")
    task_id = gd.get("task_id", "")
    report_done = str(gd.get("status", "")).lower() == "completed" or gd.get("already_generated")
    # report generation is LLM-heavy (ReportAgent interviews agents + queries graph); budget ~15 min
    for _ in range(1440):  # max 120 min — interview-rich ReACT report (real OASIS interviews + insight_forge deep retrieval) measured ~90min under DAYTIME GPU contention; at 02:00 (free GPU) ~60-70min. Generous ceiling so the cron CONSUMES a slow-but-complete report instead of timing out. Still finishes inside 02:00-07:00 even worst-case (~05:30).
        if report_done:
            break
        time.sleep(5)
        pr = requests.post(f"{MIROFISH_URL}/report/generate/status",
            json={"task_id": task_id, "simulation_id": sim_id}, timeout=30)
        if pr.ok:
            pstat = str(pr.json().get("data", {}).get("status", "")).lower()
            if pstat in ("completed", "success"):
                report_done = True
                break
            if pstat in ("failed", "error"):
                results["status"] = "report generation failed"
                return results
    if not report_done:
        results["status"] = "report timeout (120min)"
        return results
    # fetch the finished report content
    fr = requests.get(f"{MIROFISH_URL}/report/{report_id}", timeout=60)
    if fr.ok:
        results["report"] = fr.json().get("data", {})
        results["steps"].append("report generated")
        results["status"] = "complete"
        _stage("report done")
    else:
        results["status"] = f"report fetch failed: {fr.text[:200]}"

    return results

def save_prediction(results):
    """Save MiroFish prediction for brain consumption."""
    pred_path = os.path.expanduser("~/.openclaw/workspace/models/mirofish_prediction.json")
    with open(pred_path, "w") as f:
        json.dump({
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": results.get("status"),
            "steps": results.get("steps",[]),
            "project_id": results.get("project_id"),
            "simulation_id": results.get("simulation_id"),
            "report_summary": str(results.get("report",{}))[:2000]
        }, f, indent=2)
    return pred_path


def _completed_report_mtime(path):
    """Directory mtime when a completed marker exists and full_report.md is non-empty."""
    if not os.path.isdir(path):
        return None
    md = os.path.join(path, "full_report.md")
    try:
        if os.path.getsize(md) <= 0:
            return None
    except OSError:
        return None
    done = False
    for marker in ("progress.json", "meta.json"):
        try:
            with open(os.path.join(path, marker)) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and str(doc.get("status", "")).lower() == "completed":
            done = True
            break
    if not done:
        return None
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def pick_newest_completed_report(report_dir):
    # Newest completed report by directory mtime; skip incomplete/placeholder (empty full_report.md or no completed marker). Not os.listdir order.
    best = None
    if not report_dir or not os.path.isdir(report_dir):
        return None
    for name in os.listdir(report_dir):
        if name.startswith("."):
            continue
        path = os.path.join(report_dir, name)
        mtime = _completed_report_mtime(path)
        if mtime is None:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, name, path)
    return {"mtime": best[0], "id": best[1], "path": best[2]} if best else None


def _assert_pick_newest_not_lexical():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        older = os.path.join(td, "report_aaa")
        newer = os.path.join(td, "report_zzz")
        placeholder = os.path.join(td, "report_000")
        for p in (older, newer, placeholder):
            os.makedirs(p)
        open(os.path.join(older, "full_report.md"), "w").write("bitcoin " * 20)
        open(os.path.join(newer, "full_report.md"), "w").write("ethereum " * 20)
        open(os.path.join(placeholder, "full_report.md"), "w").write("")
        for p, status in ((older, "completed"), (newer, "completed"), (placeholder, "generating")):
            json.dump({"status": status}, open(os.path.join(p, "progress.json"), "w"))
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_800_000_000, 1_800_000_000))
        os.utime(placeholder, (1_900_000_000, 1_900_000_000))
        picked = pick_newest_completed_report(td)
        assert picked and picked["id"] == "report_zzz", picked


def apply_disk_report(report_data, report_dir=None):
    report_dir = report_dir or os.path.expanduser("~/mirofish-offline/backend/uploads/reports")
    picked = pick_newest_completed_report(report_dir)
    if not picked:
        return None
    with open(os.path.join(picked["path"], "full_report.md")) as rf:
        report_data["markdown_content"] = rf.read()
    outline_path = os.path.join(picked["path"], "outline.json")
    if os.path.exists(outline_path):
        with open(outline_path) as of:
            report_data["outline"] = json.load(of)
    meta_path = os.path.join(picked["path"], "meta.json")
    report_data["report_id"] = picked["id"]
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path))
        except (OSError, ValueError):
            meta = {}
        if isinstance(meta, dict):
            if meta.get("report_id"):
                report_data["report_id"] = meta["report_id"]
            if meta.get("simulation_id"):
                report_data.setdefault("simulation_id", meta["simulation_id"])
    return picked


def extract_newest_report():
    """Rewrite mirofish_signal.json from the newest completed report. Does not start a sim."""
    _assert_pick_newest_not_lexical()
    from mirofish_signal_extractor import extract_signals_from_report, process_mirofish_report
    import re
    import shutil
    bogus = extract_signals_from_report(
        "Quant funds are adapting their risk. Intraday prices adapted to flows.")
    report_data = {}
    picked = apply_disk_report(report_data)
    text = (report_data.get("markdown_content") or "").lower()
    real_ada = re.search(r"(?<![a-z0-9])(?:ada|cardano)(?![a-z0-9])", text)
    pre = extract_signals_from_report(text) if picked else {}
    if (not picked) or ("ADA-USD" in bogus) or ("ADA-USD" in pre and not real_ada):
        why = "no-report" if not picked else "ada"
        print("SMOKE FAIL", why, (picked or {}).get("id"))
        return 1
    sig = process_mirofish_report(report_data)
    ada = "ADA-USD" in (sig.get("signals") or {})
    if ada and not real_ada:
        print("SMOKE FAIL spurious ADA after write", picked["id"])
        return 1
    dest = os.path.expanduser("~/.openclaw/workspace/models/mirofish_signal.json")
    mirror = os.path.expanduser("~/.openclaw/workspace-v2/models/mirofish_signal.json")
    if os.path.isfile(mirror) and os.path.abspath(mirror) != os.path.abspath(dest):
        shutil.copy2(dest, mirror)
    mt = datetime.fromtimestamp(picked["mtime"]).astimezone().isoformat(timespec="seconds")
    tickers = ",".join(sorted(sig.get("signals") or {})) or "-"
    print(f"SMOKE PASS report={picked['id']} mtime={mt} tickers={tickers} ada={'present' if ada else 'absent'} path={dest}")
    return 0


if __name__ == "__main__":
    if "--extract-only" in sys.argv:
        sys.exit(extract_newest_report())
    print("=" * 60)
    print("  MIROFISH AUTONOMOUS RUNNER — Valedia ASI")
    print("=" * 60)

    # ── GPU STEWARD: acquire a lease before loading any model ──
    # Denied (busy / memory / outside quiet window) → exit 2 so the cron wrapper
    # falls back to intel_refresh (lightweight). The steward is the only loader.
    import subprocess as _sp
    _steward = os.path.expanduser("~/.local/bin/gpu-steward")
    _lease = None
    if os.path.exists(_steward):
        try:
            # TTL covers the whole worst-case pipeline (~5h: ontology+graph+prepare+sim+report)
            # so the steward keeps seeing MiroFish hold the GPU for the full run and won't grant a
            # competing heavy :11434 load mid-run. Normal path releases the lease on completion
            # (below); this TTL is only the crash-safety backstop.
            _r = _sp.run([_steward, "acquire", "--job", "batch_mirofish",
                          "--model", "qwen3:8b", "--ttl", "19800"],
                         capture_output=True, text=True, timeout=120)
            _resp = json.loads(_r.stdout.strip().splitlines()[-1]) if _r.stdout.strip() else {}
            if not _resp.get("granted"):
                print(f"[MiroFish] GPU steward DENIED: {_resp.get('reason','?')}")
                print("[MiroFish] Fallback: not running heavy sim now (caller uses intel_refresh).")
                sys.exit(2)
            _lease = _resp.get("lease_id")
            print(f"[MiroFish] GPU lease acquired: {_lease}")
        except SystemExit:
            raise
        except Exception as _e:
            print(f"[MiroFish] steward unavailable ({_e}); proceeding without lease")

    # Step 1: Generate seed from current market data
    print("\n[1/2] Generating financial seed from live data...")
    # Accurate, entity-dense seed (API-first: CoinGecko/Yahoo/alt.me/Polymarket/RSS).
    # Falls back to the legacy inline generator if the accurate builder errors.
    try:
        import mirofish_seed_accurate
        seed_path, seed_text = mirofish_seed_accurate.generate_seed()
    except Exception as _seed_err:
        print(f"[MiroFish] accurate seed failed ({_seed_err}); falling back to legacy seed")
        seed_path, seed_text = generate_financial_seed()
    print(f"  Seed: {seed_path}")
    print(f"  Length: {len(seed_text)} chars")
    print(f"  Preview: {seed_text[:200]}...")
    
    # Step 2: Run full simulation
    print("\n[2/2] Running MiroFish simulation...")
    results = run_simulation(seed_path)
    
    print(f"\n{'=' * 60}")
    print(f"  Status: {results['status']}")
    print(f"  Steps: {' → '.join(results.get('steps',[]))}")
    
    if results["status"] == "complete":
        pred_path = save_prediction(results)
        print(f"  Prediction saved: {pred_path}")
        print(f"  Report: {str(results.get('report',{}))[:500]}")
        
        # Extract signals for the brain
        try:
            from mirofish_signal_extractor import process_mirofish_report
            report_data = results.get("report", {})
            # Also load full_report.md if sim produced it
            sim_id = results.get("simulation_id", "")
            # OPTIONAL enrichment: prefer the on-disk full_report.md (richer than the API summary)
            # if present. Missing dir must NOT abort extraction — report_data already carries
            # markdown_content from the API. (Reports live in the backend's uploads/reports, not SEED_DIR.)
            report_dir = os.path.expanduser("~/mirofish-offline/backend/uploads/reports")
            picked = apply_disk_report(report_data, report_dir)
            if picked:
                print(f"  Report pick: {picked['id']} mtime={picked['mtime']}")

            sig = process_mirofish_report(report_data)
            print(f"  Signals extracted: {len(sig.get('signals',{}))} assets, bias={sig.get('overall_bias',50)}")
        except Exception as e:
            print(f"  Signal extraction error: {e}")

    # ── GPU STEWARD: release the lease (TTL also expires it as a safety net) ──
    if _lease and os.path.exists(_steward):
        try:
            _sp.run([_steward, "release", "--lease", _lease], capture_output=True, text=True, timeout=60)
            print(f"[MiroFish] GPU lease released: {_lease}")
        except Exception as _e:
            print(f"[MiroFish] lease release error: {_e}")

    print(f"{'=' * 60}")
    # Exit non-zero unless the full pipeline completed — so the cron wrapper
    # correctly falls back to intel_refresh instead of falsely logging success.
    sys.exit(0 if results.get("status") == "complete" else 1)
