#!/usr/bin/env python3
"""VALEDIA-FULL-SMOKE — offline PASS; optional live SKIP.

No network, no keys, no --live, no writer/AUTO_RAISE/SELF_ADJUST/treasury flips.
Run: python3 test_valedia_full_smoke.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import poly_us_smoke as poly_smoke
import polymarket_us_client as pmu
import test_poly_us as tpu
import valedia_cockpit as cockpit
import valedia_config as vc
import valedia_full_smoke as smoke


def _restore():
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_ENABLED = False
    vc.REBALANCER_AUTOEXEC = False
    vc.VALEDIA_TREASURY_AUTO = False
    vc.SPOT_RECONCILE_HTTP = False
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.VENUE_WRITER["polymarket"] = "none"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["kraken"] = "none"
    os.environ.pop("POLYMARKET_US_LIVE", None)
    os.environ.pop("POLYMARKET_US_HTTP", None)
    os.environ.pop("REBALANCER_AUTOEXEC", None)
    os.environ.pop("NAUTILUS_LIVE", None)
    os.environ.pop("VALEDIA_SELF_ADJUST", None)
    os.environ.pop("MIROFISH_AUTO_RAISE", None)
    os.environ.pop("VALEDIA_TREASURY_AUTO", None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("VALEDIA_FULL_SMOKE_OFFLINE", None)
    os.environ.pop("MIROFISH_SMOKE_LIVE", None)
    os.environ.pop("VALEDIA_TRADING_HALT", None)
    os.environ.pop("VALEDIA_TRADING_HALT_FILE", None)
    vc.VALEDIA_TRADING_HALT = False
    cockpit.set_paths(models_dir=cockpit.MODELS, ledger=None, intent_queue=None)
    vc._STAMP_CACHE = None
    tpu._restore()


def _poly_client():
    return tpu._client(tpu.MockXport(), live=False)


def test_hygiene_and_fence():
    _restore()
    src = open(os.path.join(HERE, "valedia_full_smoke.py")).read()
    for banned in (
        "import requests", "place_market_order", "create_order(",
        "POLYMARKET_US_LIVE = True", "REBALANCER_AUTOEXEC = True",
        "NAUTILUS_LIVE = True", "VALEDIA_SELF_ADJUST = True",
        "MIROFISH_AUTO_RAISE = True", "VALEDIA_TREASURY_AUTO = True",
        "VENUE_WRITER[", "POLYMARKET_US_LIVE\"] = \"1\"",
    ):
        assert banned not in src, banned
    assert "mf.run_smoke" in src or "mirofish_smoke" in src
    assert "ts.collect_status" in src
    assert "poly_smoke.run_smoke" in src
    assert "cockpit.panel" in src
    assert "pnl.load_pnl" in src
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VALEDIA_TREASURY_AUTO is False
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert pmu.live_enabled() is False
    fence = smoke.check_fence()
    assert fence["status"] == "PASS"
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "valedia_full_smoke" not in cron


def test_offline_pass_with_injectors():
    _restore()
    md = tempfile.mkdtemp(prefix="full_smoke_t_")
    q = os.path.join(md, "nautilus_intent_queue.jsonl")
    with open(q, "w") as f:
        f.write(json.dumps({"intent_id": "s1", "dispatchable": False}) + "\n")
        f.write(json.dumps({"intent_id": "s2", "dispatchable": False}) + "\n")
    cockpit.set_paths(models_dir=md, intent_queue=q)
    w0 = vc.effective_mirofish_weight()
    writers0 = dict(vc.VENUE_WRITER)
    report = smoke.run_smoke(
        offline=True,
        live_mirofish=False,
        poly_client=_poly_client(),
        kalshi_reader=lambda: {"balance": 2500},
        models_dir=md,
        queue_path=q,
        treasury_readers={"kalshi": 25.0},
    )
    assert report["ok"] is True
    assert report["verdict"] == "PASS"
    by = {s["name"]: s for s in report["sections"]}
    assert by["fence"]["status"] == "PASS"
    assert by["treasury"]["status"] == "PASS"
    assert by["treasury"]["extra"]["moves_money"] is False
    assert by["treasury"]["extra"]["auto"]["refused"] is True
    assert by["mirofish"]["status"] == "PASS"
    assert by["mirofish"]["extra"]["live_lite"] == "SKIP"
    assert by["kalshi"]["status"] == "PASS"
    assert by["kalshi"]["extra"]["cash_usd"] == 25.0
    assert by["poly_us"]["status"] == "PASS"
    assert by["poly_us"]["extra"]["create_blocked"] is True
    assert by["poly_us"]["extra"]["live"] is False
    assert by["shadow_adversarial"]["status"] == "PASS"
    assert by["shadow_adversarial"]["extra"]["intents"] == 2
    assert by["shadow_adversarial"]["extra"]["dispatchable"] == 0
    assert by["cockpit"]["status"] == "PASS"
    assert by["pnl"]["status"] == "PASS"
    assert "broker" in (by["pnl"]["extra"].get("label") or "").lower()
    assert "FTL" in by["pnl"]["extra"]["label"]
    assert "not FTL" in by["pnl"]["extra"]["message"]
    assert report["wall_s"] < 120
    assert vc.effective_mirofish_weight() == w0
    assert vc.MIROFISH_AUTO_RAISE is False
    assert dict(vc.VENUE_WRITER) == writers0
    assert pmu.live_enabled() is False
    _restore()


def test_offline_skips_optional_live():
    _restore()
    md = tempfile.mkdtemp(prefix="full_smoke_skip_")
    report = smoke.run_smoke(offline=True, live_mirofish=False, models_dir=md)
    assert report["ok"] is True
    by = {s["name"]: s for s in report["sections"]}
    assert by["kalshi"]["status"] == "SKIP"
    assert by["poly_us"]["status"] == "SKIP"
    assert by["mirofish"]["status"] == "PASS"
    assert by["fence"]["status"] == "PASS"
    _restore()


def test_exit_nonzero_only_on_critical():
    _restore()
    md = tempfile.mkdtemp(prefix="full_smoke_cli_")
    rc = smoke.main(["--offline", "--models", md,
                     "--out", os.path.join(md, "r.json"),
                     "--md", os.path.join(md, "r.md")])
    assert rc == 0
    saved = json.load(open(os.path.join(md, "r.json")))
    assert saved["verdict"] == "PASS"
    md_txt = open(os.path.join(md, "r.md")).read()
    assert "VALEDIA-FULL-SMOKE" in md_txt
    assert "**PASS**" in md_txt or "PASS" in md_txt

    orig = smoke.check_fence
    smoke.check_fence = lambda: smoke._row("fence", "FAIL", "injected", critical=True)
    try:
        rc2 = smoke.main(["--offline", "--models", md,
                          "--out", os.path.join(md, "r2.json"),
                          "--md", os.path.join(md, "r2.md")])
    finally:
        smoke.check_fence = orig
    assert rc2 == 1
    _restore()


def test_fence_fail_on_live_flag():
    _restore()
    vc.MIROFISH_AUTO_RAISE = True
    try:
        row = smoke.check_fence()
        assert row["status"] == "FAIL"
        assert row["critical"] is True
    finally:
        vc.MIROFISH_AUTO_RAISE = False
    vc.VENUE_WRITER["polymarket"] = "nautilus"
    try:
        row = smoke.check_fence()
        assert row["status"] == "FAIL"
        assert "writer" in row["reason"]
    finally:
        vc.VENUE_WRITER["polymarket"] = "none"
    os.environ["POLYMARKET_US_LIVE"] = "1"
    try:
        row = smoke.check_fence()
        assert row["status"] == "PASS"
        assert row["extra"]["polymarket_us_live"] is True
        assert "POLYMARKET_US_LIVE on" in row["reason"]
    finally:
        os.environ.pop("POLYMARKET_US_LIVE", None)
    _restore()


def test_poly_write_stays_blocked():
    _restore()
    out = poly_smoke.run_smoke(client=_poly_client())
    assert out["create_blocked"] is True
    row = smoke.check_poly_us(client=_poly_client())
    assert row["status"] == "PASS"
    assert row["extra"]["create_blocked"] is True
    _restore()


def test_version_and_docs():
    _restore()
    assert vc.VALEDIA_VERSION == "0.74.1", vc.VALEDIA_VERSION
    vc._STAMP_CACHE = None
    assert vc.version_stamp().startswith("v0.74.1 ")
    rec = open(os.path.join(ROOT, "recommendations", "VALEDIA-FULL-SMOKE.md")).read()
    assert "fence" in rec.lower()
    assert "KILL-SWITCH" in rec
    audit = open(os.path.join(ROOT, "recommendations",
                              "PONYTAIL-AUDIT-FULL-SMOKE.md")).read()
    assert "yagni:" in audit.lower() or "yagni:" in audit
    src = open(os.path.join(HERE, "valedia_full_smoke.py")).read()
    assert "WALL_BUDGET_S = 600" in src
    _restore()


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_offline_pass_with_injectors()
    test_offline_skips_optional_live()
    test_exit_nonzero_only_on_critical()
    test_fence_fail_on_live_flag()
    test_poly_write_stays_blocked()
    test_version_and_docs()
    print("valedia_full_smoke: PASS")
