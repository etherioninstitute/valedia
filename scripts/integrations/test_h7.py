#!/usr/bin/env python3
"""H7 — Arena offline strategy stress harness. No sats, no orders.

Wraps adversarial_paper_fight; scores vs injected fee-true FTL.
Run: python3 test_h7.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import adversarial_paper_fight as apf
import arena_stress as ar
import test_adversarial_paper_fight as adv
import valedia_config as vc

NOW = datetime(2026, 9, 18, 21, 0, 0, tzinfo=timezone.utc)


def _restore():
    os.environ.pop("LF_OPERATOR_KEY", None)
    os.environ.pop("LF_OPERATOR", None)
    os.environ.pop("ARENA_FAUCET", None)
    os.environ.pop("LNBITS_ADMIN", None)
    os.environ.pop("BURTON_ARENA_LN", None)
    os.environ.pop("VALEDIA_SELF_ADJUST", None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    vc._STAMP_CACHE = None


def _ftl_live(symbol, side, net, eid="e1"):
    return [
        {"type": "entry", "id": eid, "ticker": symbol, "side": side,
         "source": "live", "ts": "2026-09-18T20:00:00+00:00"},
        {"type": "settlement", "entry_id": eid, "ticker": symbol,
         "realized_pnl_net": net, "fee": 0.1, "source": "live",
         "won": net > 0, "ts": "2026-09-18T22:00:00+00:00"},
    ]


def test_hygiene_and_version():
    _restore()
    src = open(os.path.join(HERE, "arena_stress.py")).read()
    for banned in (
        "create_order", "place_market_order", "VENUE_WRITER",
        "REBALANCER_AUTOEXEC", "SPOT_RECONCILE_HTTP", "NAUTILUS_LIVE",
        "lnbits", "enqueue_intent", "import requests",
        "MIROFISH_AUTO_RAISE =", "VALEDIA_SELF_ADJUST =",
        "npm run arena", 'FUSION_WEIGHTS["arena"]',
    ):
        assert banned not in src, banned
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "arena_stress" not in cron
    assert "adversarial_run" not in cron
    assert "adversarial_paper_fight" not in cron
    assert "arena" not in vc.FUSION_WEIGHTS
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VALEDIA_SELF_ADJUST is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.VALEDIA_VERSION == "0.47.0", vc.VALEDIA_VERSION
    assert vc.version_stamp().startswith("v0.47.0 ")
    assert ar.ARENA_MODE == "paper"


def test_ln_always_refused():
    _restore()
    st = ar.ln_status({})
    assert st["arena_mode"] == "paper"
    assert st["ln_refused"] is True
    assert st["funded"] is False
    st2 = ar.ln_status({"LF_OPERATOR_KEY": "x", "BURTON_ARENA_LN": "1"})
    assert st2["ln_keys_seen"] is True
    assert st2["burton_arena_ln"] is True
    assert st2["funded"] is False
    assert st2["ln_refused"] is True


def test_primes_on_disagreement_and_hold():
    _restore()
    aapl = apf._opp("AAPL", {
        "score": 38, "direction": "SELL", "asset_class": "equity",
    })
    row = apf.fight_one(aapl, {"events": [], "overall_bias": 32})
    assert row["disagreement"] is True
    doc = {"fights": [row]}
    primed = ar.attach_primes(doc)["fights"][0]
    by = {p["lens"]: p for p in primed["primes"]}
    assert by["cooperative"]["side"] == "HOLD"
    assert by["competitive"]["side"] in ("BUY", "SELL")
    assert by["competitive"]["side"] != "HOLD"
    assert by["strategic"]["side"] in ("BUY", "SELL")
    sol = apf.fight_one(apf._opp("SOL-USD", adv.SIG["signals"]["SOL-USD"]), {})
    n = {p["lens"]: p for p in ar.attach_primes({"fights": [sol]})["fights"][0]["primes"]}
    assert n["neutral"]["side"] == "HOLD"
    assert n["competitive"]["side"] == "BUY"


def test_ftl_scores_hit_miss_skips_paper():
    _restore()
    md = tempfile.mkdtemp(prefix="h7_ftl_")
    rows = (
        _ftl_live("XRP-USD", "buy", 1.5, "ex")
        + _ftl_live("BTC-USD", "sell", 0.8, "eb")
        + [
            {"type": "entry", "id": "paper1", "ticker": "SOL-USD", "side": "buy",
             "source": "paper", "ts": "2026-09-18T20:00:00+00:00"},
            {"type": "settlement", "entry_id": "paper1", "ticker": "SOL-USD",
             "realized_pnl_net": 9.9, "fee": 0.0, "source": "paper",
             "won": True, "ts": "2026-09-18T22:00:00+00:00"},
        ]
    )
    doc = ar.run_stress(signal_doc=adv.SIG, enrich=adv.ENR, ere={},
                        ftl_rows=rows, models_dir=md, write=False, now=NOW)
    assert doc["kind"] == "arena_offline_stress"
    assert doc["wraps"] == "adversarial_paper_fight"
    assert doc["arena_mode"] == "paper"
    assert doc["no_orders"] is True
    assert doc["no_ln"] is True
    assert doc["funded"] is False
    assert doc["intents"] == []
    assert doc["fusion_touched"] is False
    assert doc["auto_raise"] is False
    sc = doc["ftl_score"]
    assert sc["fee_true"] is True
    assert sc["fusion_touched"] is False
    assert sc["intents"] == []
    by_sym = {r["symbol"]: r for r in sc["fights"]}
    assert by_sym["XRP-USD"]["scored"] is True
    assert by_sym["XRP-USD"]["realized"] == "UP"
    assert by_sym["BTC-USD"]["scored"] is True
    assert by_sym["BTC-USD"]["realized"] == "DOWN"
    assert by_sym["SOL-USD"]["scored"] is False
    xrp_l = {x["lens"]: x for x in by_sym["XRP-USD"]["lenses"]}
    assert xrp_l["momentum_kernel"]["hit"] is True
    assert xrp_l["mirofish_enriched"]["hit"] is True
    assert xrp_l["buffett_value"]["result"] == "skip"
    assert sc["n_scored"] == 2
    assert sc["n_unscored"] == 1
    assert sc["winner_lens"]
    assert "arena" not in vc.FUSION_WEIGHTS


def test_veto_records_not_orders():
    _restore()
    sig = dict(adv.SIG)
    sig["events"] = [{"direction": "RISK_OFF", "type": "geopolitics"}]
    rows = _ftl_live("XRP-USD", "buy", -1.2, "vx")
    doc = ar.run_stress(signal_doc=sig, enrich={}, ere={},
                        ftl_rows=rows, write=False, now=NOW)
    fight = next(r for r in doc["fights"] if r["symbol"] == "XRP-USD")
    assert fight["risk_veto"] is True
    assert fight["winner"]["lens"] == "risk_veto"
    assert fight["consensus_side"] == "HOLD"
    assert "intents" not in fight
    assert doc["intents"] == []
    scored = next(r for r in doc["ftl_score"]["fights"] if r["symbol"] == "XRP-USD")
    assert scored["risk_veto"] is True
    assert scored["veto"] == "ok"
    assert doc["ftl_score"]["by_lens"]["risk_veto"]["veto_ok"] == 1


def test_persist_cli_and_wrap():
    _restore()
    md = tempfile.mkdtemp(prefix="h7_cli_")
    sigp = os.path.join(md, "mirofish_signal.json")
    json.dump(adv.SIG, open(sigp, "w"))
    ftlp = os.path.join(md, "ftl.json")
    json.dump(_ftl_live("XRP-USD", "buy", 0.4, "c1"), open(ftlp, "w"))
    rc = ar.main(["--signal", sigp, "--models", md, "--ftl", ftlp])
    assert rc == 0
    files = [n for n in os.listdir(md) if n.startswith("arena_stress_")]
    assert len(files) == 1
    saved = json.load(open(os.path.join(md, files[0])))
    assert saved["arena_mode"] == "paper"
    assert saved["ln_refused"] is True
    assert saved["funded"] is False
    assert saved["valedia_version"] == "0.47.0"
    assert saved["ftl_score"]["n_scored"] >= 1
    fight_src = open(os.path.join(HERE, "adversarial_paper_fight.py")).read()
    assert "arena_stress" in fight_src


if __name__ == "__main__":
    test_hygiene_and_version()
    test_ln_always_refused()
    test_primes_on_disagreement_and_hold()
    test_ftl_scores_hit_miss_skips_paper()
    test_veto_records_not_orders()
    test_persist_cli_and_wrap()
    print("h7 arena stress: PASS")
