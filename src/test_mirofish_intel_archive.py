#!/usr/bin/env python3
"""MIROFISH-INTEL-ARCHIVE. PASS archives then replaces live. FAIL keeps last-good.

No venue network. Archive and near-neutral bias are not create fuel.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import mirofish_intel_archive as mia
from mirofish_signal_extractor import process_mirofish_report

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _live(md):
    return os.path.join(md, "mirofish_signal.json")


def _strong():
    return {
        "source": "mirofish_swarm",
        "status": "active",
        "overall_bias": 72.0,
        "signals": {"BTC-USD": {"score": 78.0, "direction": "BUY", "confidence": 0.7}},
    }


def test_pass_archives_then_replaces_and_fail_keeps_bytes():
    md = tempfile.mkdtemp(prefix="mfarch_")
    live = _live(md)
    arch = os.path.join(md, "arch")
    with open(live, "w") as f:
        f.write('{"source":"old","overall_bias":11}\n')
    body = mia.publish_pass(_strong(), live_path=live, archive_dir=arch,
                            run_id="rep/1", now=NOW)
    assert body["usable_for_fv"] is True
    assert body["stale"] is False
    raw = open(live, "rb").read()
    saved = json.loads(raw)
    assert saved["overall_bias"] == 72.0
    assert saved["as_of"].startswith("2026-09-22")
    assert not saved.get("archive")
    names = set(os.listdir(arch))
    assert "2026-09-22.json" in names
    assert "2026-09-22.rep1.json" in names
    archived = json.load(open(os.path.join(arch, "2026-09-22.json")))
    assert archived["usable_for_fv"] is False
    assert archived["archive"] is True
    assert mia.load_for_fv(os.path.join(arch, "2026-09-22.json"), now=NOW) is None
    assert mia.load_for_fv(live, now=NOW)["signals"]["BTC-USD"]["score"] == 78.0
    assert not any(n.startswith(".mf-") for n in os.listdir(md))
    mia.publish_fail(live, reason="sim_fail", now=NOW + timedelta(hours=1))
    assert open(live, "rb").read() == raw
    assert set(os.listdir(arch)) == names
    marker = json.load(open(mia.marker_path(live)))
    assert marker["stale"] is True
    assert marker["usable_for_fv"] is False
    assert "signals" not in marker
    assert mia.load_for_fv(live, now=NOW) is None
    view = mia.freshness_of(live, now=NOW)
    assert view["stale"] is True and view["usable_for_fv"] is False


def test_near_neutral_and_age_are_not_fuel():
    md = tempfile.mkdtemp(prefix="mfneut_")
    live = _live(md)
    doc = _strong()
    doc["overall_bias"] = 51.5
    out = mia.publish_pass(doc, live_path=live, archive_dir=os.path.join(md, "a"), now=NOW)
    assert out["usable_for_fv"] is False
    assert json.load(open(live))["overall_bias"] == 51.5
    assert mia.load_for_fv(live, now=NOW) is None
    light = {"source": "intel_refresh (lightweight)", "overall_bias": 80, "as_of": NOW.isoformat()}
    mia._atomic(live, light)
    mia._atomic(mia.marker_path(live), {"stale": False, "as_of": NOW.isoformat()})
    assert mia.load_for_fv(live, now=NOW) is None
    mia.publish_pass(_strong(), live_path=live, archive_dir=os.path.join(md, "a"), now=NOW)
    later = NOW + timedelta(hours=25)
    assert mia.load_for_fv(live, now=later) is None
    aged = mia.freshness_of(live, now=later)
    assert aged["stale"] is True and aged["age_s"] >= mia.MAX_AGE_S


def test_prune_and_missing_live():
    md = tempfile.mkdtemp(prefix="mfprune_")
    arch = os.path.join(md, "arch")
    os.makedirs(arch)
    old = os.path.join(arch, "2020-01-01.json")
    old_sib = os.path.join(arch, "2020-01-01.run.json")
    for p in (old, old_sib):
        with open(p, "w") as f:
            f.write("{}\n")
    mia.publish_pass(_strong(), live_path=_live(md), archive_dir=arch, now=NOW)
    assert not os.path.exists(old)
    assert not os.path.exists(old_sib)
    assert os.path.exists(os.path.join(arch, "2026-09-22.json"))
    missing = os.path.join(md, "nope", "mirofish_signal.json")
    assert mia.load_for_fv(missing, now=NOW) is None
    mia.publish_fail(missing, reason="no_live", now=NOW)
    assert not os.path.exists(missing)
    marker = json.load(open(mia.marker_path(missing)))
    assert marker["stale"] is True and marker["as_of"] is None


def test_extractor_pass_and_gather_consume():
    md = tempfile.mkdtemp(prefix="mfext_")
    live = _live(md)
    report = {
        "simulation_id": "rep1",
        "outline": {"summary": "Bitcoin rally surge accumulate"},
        "markdown_content": (
            "Institutional traders executed a BUY trade for BTC-USD. "
            "Bullish bounce. Confidence: HIGH."
        ),
    }
    doc = process_mirofish_report(report, out_path=live)
    assert doc["source"] == "mirofish_swarm"
    assert "as_of" in doc and "usable_for_fv" in doc and "stale" in doc
    arch = os.path.join(md, "mirofish-archive")
    names = os.listdir(arch)
    assert any(n.endswith(".json") and n[:10] == doc["as_of"][:10] for n in names)
    assert any("rep1" in n for n in names)
    raw = open(live, "rb").read()
    mia.publish_fail(live, reason="extract_fail")
    assert open(live, "rb").read() == raw

    import intel_opportunity_gatherer as iog
    fresh = datetime.now(timezone.utc)
    fuel = tempfile.mkdtemp(prefix="mffuel_")
    mia.publish_pass(_strong(), live_path=_live(fuel),
                     archive_dir=os.path.join(fuel, "arch"), now=fresh)
    opps = iog.gather(models_dir=fuel)
    assert any(o["source_feed"] == "mirofish" and o["instrument"] == "BTC-USD" for o in opps)
    mia.publish_fail(_live(fuel), reason="sim_fail", now=fresh)
    opps = iog.gather(models_dir=fuel)
    assert all(o["source_feed"] != "mirofish" for o in opps)
    neutral = tempfile.mkdtemp(prefix="mfhold_")
    hold = _strong()
    hold["overall_bias"] = 49.1
    hold["signals"] = {"BTC-USD": {"score": 80.0, "direction": "BUY", "confidence": 0.8}}
    mia.publish_pass(hold, live_path=_live(neutral),
                     archive_dir=os.path.join(neutral, "arch"), now=fresh)
    opps = iog.gather(models_dir=neutral)
    assert all(o["source_feed"] != "mirofish" for o in opps)


def test_wires_version_and_no_venue():
    src = open(os.path.join(HERE, "mirofish_intel_archive.py")).read()
    assert "create_order" not in src
    assert "requests" not in src
    for name in ("quant_engine.py", "unified_brain.py", "intel_layer.py",
                 "sports_engine.py", "intel_opportunity_gatherer.py",
                 "mirofish_runner.py"):
        text = open(os.path.join(HERE, name)).read()
        assert "load_for_fv" in text or "publish_fail" in text, name
    import valedia_config as vc
    assert vc.VALEDIA_VERSION == "0.75.0"
    got = {r["name"]: r for r in vc.controls_snapshot()["items"]}
    for n in ("MIROFISH_INTEL_AS_OF", "MIROFISH_INTEL_STALE", "MIROFISH_INTEL_USABLE_FOR_FV"):
        assert got[n]["source"] == "file", n
    assert isinstance(got["MIROFISH_INTEL_STALE"]["value"], bool)
    assert isinstance(got["MIROFISH_INTEL_USABLE_FOR_FV"]["value"], bool)
    assert vc.POLYMARKET_US_LIVE is False
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    assert "WEATHER_ARB" in vc.LIVE_EXEC_FENCED


def run():
    test_pass_archives_then_replaces_and_fail_keeps_bytes()
    test_near_neutral_and_age_are_not_fuel()
    test_prune_and_missing_live()
    test_extractor_pass_and_gather_consume()
    test_wires_version_and_no_venue()
    print("PASS test_mirofish_intel_archive")


if __name__ == "__main__":
    run()
