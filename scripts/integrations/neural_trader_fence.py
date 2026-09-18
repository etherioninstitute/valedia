"""Fenced neural-trader research CLI. MiroFish intel only — not an OMS.

Allow: backtest, signal, regime, risk, portfolio, train, explain.
Deny: live, broker, --live, --broker, mcp, cloud, --swarm.
CLI-first: never spawn ruflo-neural-trader opus/sonnet agents.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PIN_DIR = os.path.join(ROOT, "tools", "neural-trader")
ALLOWLIST_PATH = os.path.join(PIN_DIR, "allowlist.json")
PKG_PATH = os.path.join(PIN_DIR, "package.json")

MIN_VERSION = (2, 8, 11)
ALLOW = frozenset(
    {"backtest", "signal", "regime", "risk", "portfolio", "train", "explain"}
)
DENY = frozenset(
    {"live", "--live", "broker", "--broker", "mcp", "cloud", "--swarm"}
)

# Valedia live-path files that must not invoke neural-trader / broker CLI.
LIVE_PATH_FILES = (
    "scripts/install_cron.sh",
    "scripts/cron_runner.sh",
    "scripts/cron_runner_mac.sh",
    "scripts/integrations/valedia_config.py",
    ".mcp.json",
    ".grok/config.toml",
)
LIVE_PATH_FORBIDDEN = (
    "neural-trader",
    "ruflo-neural-trader",
    "npx neural",
    "trader live",
)


class FenceError(ValueError):
    """Research-only argv or pin violated."""


def load_allowlist():
    with open(ALLOWLIST_PATH) as f:
        return json.load(f)


def parse_semver(s):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(s) or "")
    if not m:
        raise FenceError(f"unparseable version: {s!r}")
    return tuple(int(x) for x in m.groups())


def pin_spec():
    with open(PKG_PATH) as f:
        dep = json.load(f).get("dependencies", {}).get("neural-trader", "")
    if not str(dep).startswith("^2.8.") and not str(dep).startswith("2.8."):
        raise FenceError(f"package.json pin must be ^2.8.x, got {dep!r}")
    if parse_semver(dep) < MIN_VERSION:
        raise FenceError(f"pin {dep} older than 2.8.11 — refuse (fork-bomb risk)")
    return dep


def installed_version():
    pkg = os.path.join(PIN_DIR, "node_modules", "neural-trader", "package.json")
    if not os.path.isfile(pkg):
        return None
    with open(pkg) as f:
        return json.load(f).get("version")


def pin_ok():
    spec = pin_spec()
    ver = installed_version()
    if ver is not None and parse_semver(ver) < MIN_VERSION:
        raise FenceError(f"installed {ver} < 2.8.11")
    return {"spec": spec, "installed": ver}


def _tokens(argv):
    out = []
    for a in argv:
        s = str(a).strip()
        if not s:
            continue
        out.append(s)
        if s.startswith("--") and "=" in s:
            out.append(s.split("=", 1)[0])
            out.append(s.split("=", 1)[1])
    return out


def assert_research_argv(argv):
    """Raise FenceError if argv is not a research-only neural-trader invocation."""
    toks = [t.lower() for t in _tokens(argv)]
    for t in toks:
        base = t[2:] if t.startswith("--") else t
        if t in DENY or base in DENY:
            raise FenceError(f"denied token {t!r}")
        if t in ("opus", "sonnet") or t.startswith("claude-"):
            raise FenceError(f"denied agent model {t!r} — CLI-first / LMS only")
    verbs = []
    for t in toks:
        if t in ALLOW:
            verbs.append(t)
        elif t.startswith("--") and t[2:] in ALLOW:
            verbs.append(t[2:])
    if not verbs:
        raise FenceError("no allowlisted research verb in argv")
    return verbs


def research_argv(verb, extra=None):
    v = str(verb).strip().lower()
    if v not in ALLOW:
        raise FenceError(f"verb {verb!r} not allowlisted")
    argv = ["npx", "--no-install", "neural-trader", f"--{v}"]
    if extra:
        argv.extend(str(x) for x in extra)
    assert_research_argv(argv)
    return argv


def scan_live_path_files(root=ROOT):
    hits = []
    for rel in LIVE_PATH_FILES:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        text = open(path, errors="replace").read().lower()
        for needle in LIVE_PATH_FORBIDDEN:
            if needle.lower() in text:
                hits.append({"file": rel, "needle": needle})
    return hits


def crontab_hits():
    try:
        out = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL, timeout=5)
        text = out.decode(errors="replace").lower()
    except Exception:
        return []
    needles = ("neural-trader", "npx neural", "trader live", "--broker", "ruflo-neural-trader")
    return [n for n in needles if n in text]


def fence_flags():
    import valedia_config as vc

    return {
        "NAUTILUS_LIVE": vc.NAUTILUS_LIVE,
        "NAUTILUS_ENABLED": vc.NAUTILUS_ENABLED,
        "SPOT_RECONCILE_HTTP": vc.spot_reconcile_http_enabled(),
        "REBALANCER_AUTOEXEC": vc.rebalancer_autoexec_enabled(),
        "MIROFISH_AUTO_RAISE": vc.MIROFISH_AUTO_RAISE,
        "MIROFISH_WEIGHT": vc.MIROFISH_WEIGHT,
        "VENUE_WRITER": dict(vc.VENUE_WRITER),
    }


def flags_hold():
    f = fence_flags()
    assert f["NAUTILUS_LIVE"] is False
    assert f["SPOT_RECONCILE_HTTP"] is False
    assert f["REBALANCER_AUTOEXEC"] is False
    assert f["MIROFISH_AUTO_RAISE"] is False
    for venue, writer in f["VENUE_WRITER"].items():
        assert writer in ("valedia", "none"), (venue, writer)
        assert writer != "neural-trader"
    return f
