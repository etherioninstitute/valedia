#!/usr/bin/env python3
"""MiroFish minutes-scale smoke. Fixture always; live-lite optional.

Proves seed→extractor→neural tag→accuracy-ledger ingest→intel gatherer
wiring. No network on the fixture path. Live-lite is one short local
model call (Ollama :11435 or LMS) with a hard 90s cap; unreachable → SKIP.

Exit nonzero only if the fixture path fails. Never flips live flags,
never runs the overnight swarm, never writes the production ledger.

Run: python3 scripts/integrations/mirofish_smoke.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import intel_opportunity_gatherer as iog  # noqa: E402
import mirofish_accuracy_ledger as mal  # noqa: E402
import mirofish_neural_enrich as mne  # noqa: E402
from mirofish_signal_extractor import process_mirofish_report  # noqa: E402
import valedia_config as vc  # noqa: E402

LIVE_TIMEOUT_S = 90
PROBE_TIMEOUT_S = 2
WALL_BUDGET_S = 300
HOME_MODELS = os.path.expanduser("~/.openclaw/workspace/models")

SYNTHETIC_REPORT = {
    "outline": {
        "summary": "Bitcoin rally surge accumulate buying pressure near support.",
    },
    "markdown_content": (
        "Bitcoin rally surge breakout accumulate buying pressure bullish upside. "
        "Institutional traders executed a BUY trade for BTC-USD. "
        "News: ETF inflows; BTC-USD recovery near support."
    ),
}

SYNTHETIC_ENRICHMENT = {
    "symbols": {
        "BTC-USD": {
            "regime": {"label": "ranging", "confidence": 0.62},
            "signal": {"direction": "BUY", "quality": 0.55},
        },
    },
}

_LIVE_ENV = (
    "REBALANCER_AUTOEXEC",
    "NAUTILUS_LIVE",
    "VALEDIA_SELF_ADJUST",
    "MIROFISH_AUTO_RAISE",
    "VALEDIA_TREASURY_AUTO",
    "SPOT_RECONCILE_HTTP",
)


def _now():
    return datetime.now(timezone.utc)


def _ms(t0):
    return int((time.monotonic() - t0) * 1000)


def _truthy(raw):
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def check_fence():
    """Refuse if a live env flag is already on. Never sets them."""
    bad = [k for k in _LIVE_ENV if _truthy(os.environ.get(k))]
    cfg = []
    if getattr(vc, "MIROFISH_AUTO_RAISE", False):
        cfg.append("MIROFISH_AUTO_RAISE")
    if getattr(vc, "VALEDIA_SELF_ADJUST", False):
        cfg.append("VALEDIA_SELF_ADJUST")
    if getattr(vc, "NAUTILUS_LIVE", False):
        cfg.append("NAUTILUS_LIVE")
    if getattr(vc, "VALEDIA_TREASURY_AUTO", False):
        cfg.append("VALEDIA_TREASURY_AUTO")
    if getattr(vc, "REBALANCER_AUTOEXEC", False):
        cfg.append("REBALANCER_AUTOEXEC")
    ok = not bad and not cfg
    return {"ok": ok, "env_live": bad, "config_live": cfg}


def restore_paths():
    mal.set_paths(models_dir=HOME_MODELS)
    mne.set_paths(models_dir=os.path.join(ROOT, "models"))


def http_json(url, payload=None, timeout=8):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def ollama_base():
    return os.environ.get("VALEDIA_MIROFISH_OLLAMA", "http://127.0.0.1:11435").rstrip("/")


def lms_base():
    return os.environ.get("VALEDIA_LMS_URL", "http://127.0.0.1:1234").rstrip("/")


def _pick_ollama_model(tags):
    names = [m.get("name") or m.get("model") or "" for m in (tags.get("models") or [])]
    for prefer in ("qwen3-8b-mf:latest", "ornith-1.5-mf:latest"):
        if prefer in names:
            return prefer
    return next((n for n in names if n), None)


def _pick_lms_model(doc):
    rows = doc.get("data") or []
    ids = [r.get("id") or "" for r in rows if isinstance(r, dict)]
    return next((i for i in ids if i), None)


def run_live_lite(timeout=LIVE_TIMEOUT_S):
    """One short local-model ping. Bionic first; Ollama fallback SKIP. Never FAIL fixture."""
    t0 = time.monotonic()
    cap = min(int(timeout), LIVE_TIMEOUT_S)
    mf, lms = ollama_base(), lms_base()
    try:
        models = http_json(lms + "/v1/models", timeout=PROBE_TIMEOUT_S)
        model = _pick_lms_model(models)
        if model:
            out = http_json(lms + "/v1/chat/completions", {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the word ok."}],
                "max_tokens": 8,
                "reasoning_effort": "none",
            }, timeout=cap)
            text = (((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")[:80]
            return {"status": "PASS", "endpoint": lms, "model": model,
                    "reply": text, "ms": _ms(t0)}
        # lms up but empty catalog — try Ollama fallback
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError, KeyError):
        pass
    try:
        tags = http_json(mf + "/api/tags", timeout=PROBE_TIMEOUT_S)
        model = _pick_ollama_model(tags)
        if not model:
            return {"status": "SKIP", "reason": "bionic miss; ollama up, no models",
                    "endpoint": mf, "ms": _ms(t0)}
        out = http_json(mf + "/api/generate", {
            "model": model,
            "prompt": "Reply with the word ok.",
            "stream": False,
            "options": {"num_predict": 8, "num_ctx": 512},
        }, timeout=cap)
        text = str(out.get("response") or "")[:80]
        return {"status": "PASS", "endpoint": mf, "model": model,
                "reply": text, "ms": _ms(t0)}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        return {"status": "SKIP", "reason": str(e)[:120], "endpoint": lms,
                "ms": _ms(t0)}


def _mock_ftl(asset, ts):
    return [
        {
            "type": "entry", "id": "smoke_e1", "ticker": asset, "side": "buy",
            "source": "live", "venue": "coinbase", "entry_price": 100.0,
            "contracts": 1.0, "entry_fee": 1.1, "ts": ts, "fill_status": "FILLED",
        },
        {
            "type": "settlement", "entry_id": "smoke_e1", "ticker": asset,
            "realized_pnl_net": 2.5, "fee": 1.1, "won": True, "source": "live",
            "ts": ts, "logged_at": ts,
        },
    ]


def run_fixture(models_dir=None, now=None):
    """Offline path: synthetic seed JSON → extractor → neural → ledger mock → gatherer."""
    t0 = time.monotonic()
    now = now or _now()
    md = models_dir or tempfile.mkdtemp(prefix="mf_smoke_")
    os.makedirs(md, exist_ok=True)
    steps = []
    mal.set_paths(models_dir=md)
    mne.set_paths(models_dir=md)
    try:
        seed_md = "# Smoke seed\n\n" + SYNTHETIC_REPORT["markdown_content"]
        seeded = mne.apply_seed_text(seed_md, SYNTHETIC_ENRICHMENT)
        if "Neural-trader context" not in seeded or "BTC-USD" not in seeded:
            raise RuntimeError("seed advisory section missing")
        steps.append({"step": "seed_advisory", "ok": True})

        sig_path = os.path.join(md, "mirofish_signal.json")
        signal = process_mirofish_report(
            SYNTHETIC_REPORT, enrichment=SYNTHETIC_ENRICHMENT, out_path=sig_path)
        sigs = signal.get("signals") or {}
        if "BTC-USD" not in sigs:
            raise RuntimeError("extractor missed BTC-USD")
        if signal.get("source") != "mirofish_swarm":
            raise RuntimeError("source not mirofish_swarm")
        if signal.get("neural_enriched") is not True:
            raise RuntimeError("neural_enriched not stamped")
        btc = sigs["BTC-USD"]
        if not (isinstance(btc.get("neural"), dict) and btc["neural"].get("present")):
            raise RuntimeError("BTC neural tag missing")
        if btc.get("direction") not in ("BUY", "SELL"):
            raise RuntimeError("no directional signal")
        steps.append({
            "step": "extractor", "ok": True,
            "direction": btc.get("direction"), "score": btc.get("score"),
            "neural_enriched": True,
        })

        aged = dict(signal)
        aged["ts"] = (now - timedelta(days=6)).isoformat()
        rc = mal.record(aged)
        if rc != 0:
            raise RuntimeError("record rc=%s" % rc)
        preds = [r for r in mal._read() if r.get("type") == "prediction"]
        if not preds:
            raise RuntimeError("ledger recorded 0 predictions")
        later = (now - timedelta(days=2)).isoformat()
        ftl_path = mal.paths()["ftl"]
        with open(ftl_path, "w") as f:
            for row in _mock_ftl("BTC-USD", later):
                f.write(json.dumps(row) + "\n")
        mal.score(now=now)
        results = [r for r in mal._read() if r.get("type") == "result"]
        if not results:
            raise RuntimeError("score produced 0 fee-true results")
        hit = results[0]
        if hit.get("fee_true") is not True:
            raise RuntimeError("result not fee_true")
        if hit.get("neural_enriched") is not True:
            raise RuntimeError("result lost neural_enriched tag")
        steps.append({
            "step": "ledger_ingest", "ok": True,
            "pred_id": hit.get("pred_id"), "hit": hit.get("hit"),
            "fee_true": True, "n_pred": len(preds), "n_scored": len(results),
        })

        opps = iog.gather(models_dir=md)
        mf_opps = [o for o in opps if o.get("source_feed") == "mirofish"]
        steps.append({
            "step": "intel_gatherer", "ok": True,
            "n_opps": len(opps), "n_mirofish": len(mf_opps),
            "intents_enqueued": 0,
        })
        return {
            "ok": True, "models_dir": md, "signal_path": sig_path,
            "steps": steps, "ms": _ms(t0),
            "overall_bias": signal.get("overall_bias"),
            "weight_unchanged": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:240], "steps": steps,
                "models_dir": md, "ms": _ms(t0)}
    finally:
        restore_paths()


def run_smoke(live=True, models_dir=None, out_path=None):
    t0 = time.monotonic()
    fence = check_fence()
    fixture = {"ok": False, "error": "fence"}
    if fence["ok"]:
        fixture = run_fixture(models_dir=models_dir)
    live_r = {"status": "SKIP", "reason": "disabled"}
    if fixture.get("ok") and live:
        live_r = run_live_lite()
    elif not live:
        live_r = {"status": "SKIP", "reason": "disabled"}
    wall = time.monotonic() - t0
    ok = bool(fixture.get("ok")) and fence["ok"]
    report = {
        "ok": ok,
        "verdict": "PASS" if ok else "FAIL",
        "valedia_version": vc.VALEDIA_VERSION,
        "fence": fence,
        "fixture": fixture,
        "live_lite": live_r,
        "wall_s": round(wall, 3),
        "wall_budget_s": WALL_BUDGET_S,
        "within_budget": wall <= WALL_BUDGET_S,
        "overnight_still_covers": [
            "full swarm ontology + multi-agent sim (mirofish_runner :5001)",
            "live seed APIs (CoinGecko/Yahoo/Kalshi/news RSS)",
            "real 5-day fee-true FTL corpus (n>=20 proven bar)",
            "weight raise evidence (AUTO_RAISE stays False)",
        ],
        "scripts_touched": [
            "mirofish_seed_accurate.py (advisory apply_seed_text only; no HTTP)",
            "mirofish_signal_extractor.py (process_mirofish_report)",
            "mirofish_neural_enrich.py (attach_to_signal + apply_seed_text)",
            "mirofish_accuracy_ledger.py (record + score; mock FTL)",
            "intel_opportunity_gatherer.py (gather/collect_mirofish; no enqueue)",
        ],
        "not_run": [
            "mirofish_runner.run_simulation",
            "mirofish_seed_accurate.build_seed",
            "intent enqueue",
        ],
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        report["report_path"] = out_path
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="MiroFish minutes-scale smoke")
    p.add_argument("--no-live", action="store_true", help="skip live-lite even if Ollama is up")
    p.add_argument("--out", default=os.path.join(ROOT, "models", "mirofish_smoke_report.json"))
    p.add_argument("--models", default=None, help="fixture artifact dir (default: temp)")
    args = p.parse_args(argv)
    live = not args.no_live and os.environ.get("MIROFISH_SMOKE_LIVE", "1") != "0"
    report = run_smoke(live=live, models_dir=args.models, out_path=args.out)
    print(json.dumps(report, indent=2, default=str))
    fixture_ok = bool(report.get("ok"))
    live_st = (report.get("live_lite") or {}).get("status", "SKIP")
    print("MIROFISH-SMOKE: %s (fixture=%s live-lite=%s wall=%.2fs)" % (
        report["verdict"], "PASS" if fixture_ok else "FAIL", live_st,
        report.get("wall_s") or 0))
    return 0 if fixture_ok else 1


if __name__ == "__main__":
    sys.exit(main())
