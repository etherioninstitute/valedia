#!/usr/bin/env python3
"""NT-0 — pin neural-trader@^2.8.11, research allowlist, live/broker unreachable.

No network orders, no --live venue, no broker writers.
Run: python3 test_nt0.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import neural_trader_fence as ntf
import valedia_config as vc


def test_pin():
    spec = ntf.pin_spec()
    assert spec == "^2.8.11", spec
    info = ntf.pin_ok()
    assert info["spec"] == "^2.8.11"
    ver = ntf.installed_version()
    assert ver is not None, "cd tools/neural-trader && npm install --ignore-scripts --no-audit --no-fund"
    assert ntf.parse_semver(ver) >= (2, 8, 11), ver
    al = ntf.load_allowlist()
    assert al["min_version"] == "2.8.11"
    assert set(al["allow"]) == set(ntf.ALLOW)
    deny = set(al["deny"])
    for d in ("live", "--live", "broker", "--broker"):
        assert d in deny
    pkg = os.path.join(ntf.PIN_DIR, "package.json")
    assert os.path.isfile(pkg)


def test_allow_research_argv():
    for v in ntf.ALLOW:
        argv = ntf.research_argv(v, ["--symbol", "BTC-USD"])
        assert f"--{v}" in argv
        assert ntf.assert_research_argv(argv) == [v]


def test_deny_live_broker():
    bad = [
        ["npx", "neural-trader", "live", "--broker", "coinbase"],
        ["npx", "neural-trader", "--live"],
        ["npx", "neural-trader", "--broker", "alpaca"],
        ["npx", "neural-trader", "--regime", "--live"],
        ["npx", "neural-trader", "--signal", "scan", "--broker=kalshi"],
        ["npx", "neural-trader", "--swarm", "enabled", "--broker", "x"],
        ["npx", "neural-trader", "mcp", "start"],
        ["npx", "neural-trader", "--regime", "--model", "opus"],
        ["npx", "neural-trader", "--backtest", "sonnet"],
        ["trader", "live"],
        ["npx", "neural-trader"],  # no research verb
    ]
    for argv in bad:
        try:
            ntf.assert_research_argv(argv)
        except ntf.FenceError:
            continue
        raise AssertionError(f"should deny: {argv}")


def test_live_path_unwired():
    hits = ntf.scan_live_path_files()
    assert hits == [], hits
    cron = ntf.crontab_hits()
    assert cron == [], cron
    mcp = os.path.join(ROOT, ".mcp.json")
    grok = os.path.join(ROOT, ".grok", "config.toml")
    text = open(mcp).read() + open(grok).read()
    assert "neural-trader" not in text
    assert "ruflo-neural-trader" not in text


def test_flags_unchanged():
    f = ntf.flags_hold()
    assert vc.NAUTILUS_LIVE is False
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert f["MIROFISH_AUTO_RAISE"] is False
    src = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "neural-trader" not in src
    assert "MIROFISH_AUTO_RAISE   = False" in src
    assert "NAUTILUS_LIVE = False" in src
    assert "SPOT_RECONCILE_HTTP = False" in src


if __name__ == "__main__":
    test_pin()
    test_allow_research_argv()
    test_deny_live_broker()
    test_live_path_unwired()
    test_flags_unchanged()
    print("nt0 pin+allowlist+unreachable: PASS")
