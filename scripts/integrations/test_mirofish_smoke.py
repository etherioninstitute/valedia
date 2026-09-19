#!/usr/bin/env python3
"""MIROFISH-SMOKE — offline fixture PASS; live-lite skip.

No network, no keys, no --live, no writer/AUTO_RAISE/SELF_ADJUST flips.
Run: python3 test_mirofish_smoke.py
"""
import json
import os
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import mirofish_accuracy_ledger as mal
import mirofish_smoke as smoke
import valedia_config as vc


def _restore():
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    vc.NAUTILUS_LIVE = False
    vc.REBALANCER_AUTOEXEC = False
    vc.VALEDIA_TREASURY_AUTO = False
    vc.SPOT_RECONCILE_HTTP = False
    vc.MIROFISH_WEIGHT = 0.10
    vc.MIROFISH_UNPROVEN_CAP = 0.10
    vc.MIROFISH_PROVEN_CAP = 0.15
    os.environ.pop("POLYMARKET_US_LIVE", None)
    os.environ.pop("REBALANCER_AUTOEXEC", None)
    os.environ.pop("NAUTILUS_LIVE", None)
    os.environ.pop("VALEDIA_SELF_ADJUST", None)
    os.environ.pop("MIROFISH_AUTO_RAISE", None)
    os.environ.pop("VALEDIA_TREASURY_AUTO", None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("MIROFISH_SMOKE_LIVE", None)
    smoke.restore_paths()
    vc._STAMP_CACHE = None


def test_hygiene_and_fence():
    _restore()
    src = open(os.path.join(HERE, "mirofish_smoke.py")).read()
    for banned in (
        "run_simulation(", "build_seed(", "enqueue_intent(",
        "create_order", "place_market_order", "import requests",
        "POLYMARKET_US_LIVE = True", "REBALANCER_AUTOEXEC = True",
        "NAUTILUS_LIVE = True", "VALEDIA_SELF_ADJUST = True",
        "MIROFISH_AUTO_RAISE = True", "VALEDIA_TREASURY_AUTO = True",
        "VENUE_WRITER[",
    ):
        assert banned not in src, banned
    assert "LIVE_TIMEOUT_S = 90" in src
    assert "process_mirofish_report" in src
    assert "mal.record" in src
    assert "mal.score" in src
    assert "iog.gather" in src
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VALEDIA_SELF_ADJUST is False
    assert vc.MIROFISH_UNPROVEN_CAP == 0.10
    assert vc.MIROFISH_PROVEN_CAP == 0.15
    fence = smoke.check_fence()
    assert fence["ok"] is True
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "mirofish_smoke" not in cron


def test_fixture_offline_pass():
    _restore()
    md = tempfile.mkdtemp(prefix="mf_smoke_t_")
    out = os.path.join(md, "report.json")
    w0 = vc.effective_mirofish_weight()
    report = smoke.run_smoke(live=False, models_dir=md, out_path=out)
    assert report["ok"] is True
    assert report["verdict"] == "PASS"
    fx = report["fixture"]
    assert fx["ok"] is True
    steps = {s["step"]: s for s in fx["steps"]}
    assert steps["seed_advisory"]["ok"] is True
    assert steps["extractor"]["neural_enriched"] is True
    assert steps["extractor"]["direction"] in ("BUY", "SELL")
    assert steps["ledger_ingest"]["fee_true"] is True
    assert steps["ledger_ingest"]["n_scored"] >= 1
    assert steps["intel_gatherer"]["intents_enqueued"] == 0
    assert report["live_lite"]["status"] == "SKIP"
    assert report["wall_s"] < 60
    assert vc.effective_mirofish_weight() == w0
    assert vc.MIROFISH_AUTO_RAISE is False
    saved = json.load(open(out))
    assert saved["verdict"] == "PASS"
    # production ledger not used
    assert mal.paths()["models"] == smoke.HOME_MODELS
    live_ledger = os.path.expanduser(
        "~/.openclaw/workspace/models/mirofish_accuracy_ledger.jsonl")
    if os.path.isfile(live_ledger):
        assert "smoke_e1" not in open(live_ledger).read()
    _restore()


def test_live_lite_skip_when_unreachable():
    _restore()

    def boom(url, payload=None, timeout=8):
        raise urllib.error.URLError("offline")

    orig = smoke.http_json
    smoke.http_json = boom
    try:
        r = smoke.run_live_lite(timeout=2)
    finally:
        smoke.http_json = orig
    assert r["status"] == "SKIP"
    assert r.get("ms", 0) < 5000
    _restore()


def test_live_lite_pass_injected():
    _restore()
    calls = []

    def fake(url, payload=None, timeout=8):
        calls.append(url)
        if url.endswith("/api/tags"):
            return {"models": [{"name": "qwen3-8b-mf:latest"}]}
        if url.endswith("/api/generate"):
            assert timeout <= 90
            assert payload["options"]["num_predict"] == 8
            return {"response": "ok"}
        raise urllib.error.URLError("no")

    orig = smoke.http_json
    smoke.http_json = fake
    try:
        r = smoke.run_live_lite(timeout=90)
    finally:
        smoke.http_json = orig
    assert r["status"] == "PASS"
    assert r["model"] == "qwen3-8b-mf:latest"
    assert r["reply"] == "ok"
    assert any(u.endswith("/api/generate") for u in calls)
    _restore()


def test_exit_nonzero_only_on_fixture_fail():
    _restore()
    md = tempfile.mkdtemp(prefix="mf_smoke_cli_")
    rc = smoke.main(["--no-live", "--models", md,
                     "--out", os.path.join(md, "r.json")])
    assert rc == 0
    orig = smoke.run_fixture
    smoke.run_fixture = lambda **kw: {"ok": False, "error": "boom", "steps": []}
    try:
        rc2 = smoke.main(["--no-live", "--models", md,
                          "--out", os.path.join(md, "r2.json")])
    finally:
        smoke.run_fixture = orig
    assert rc2 == 1
    _restore()


def test_version_and_docs():
    _restore()
    assert vc.VALEDIA_VERSION == "0.42.0", vc.VALEDIA_VERSION
    vc._STAMP_CACHE = None
    assert vc.version_stamp().startswith("v0.42.0 ")
    rec = open(os.path.join(ROOT, "recommendations", "MIROFISH-SMOKE.md")).read()
    assert "fixture" in rec.lower()
    assert "overnight" in rec.lower()
    assert "MIROFISH_AUTO_RAISE" in rec
    audit = open(os.path.join(ROOT, "recommendations",
                              "PONYTAIL-AUDIT-MIROFISH-SMOKE.md")).read()
    assert "yagni:" in audit.lower() or "yagni:" in audit
    _restore()


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_fixture_offline_pass()
    test_live_lite_skip_when_unreachable()
    test_live_lite_pass_injected()
    test_exit_nonzero_only_on_fixture_fail()
    test_version_and_docs()
    print("mirofish_smoke: PASS")
