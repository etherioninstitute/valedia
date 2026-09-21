#!/usr/bin/env python3
"""CONTROLS-READOUT — snapshot + cockpit + CLI. No network, no flips.

Run: python3 test_controls_readout.py
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import valedia_cockpit as cockpit
import valedia_config as vc
import valedia_controls as vctl


REQUIRED = (
    "VALEDIA_TRADING_HALT",
    "KALSHI_FEE_MAP_GATE",
    "KALSHI_EV_PREFLIGHT_GATE",
    "KALSHI_MAKER_FIRST",
    "KALSHI_FILL_LEDGER_DEMOTE",
    "VALEDIA_JEV",
    "VALEDIA_SELF_ADJUST",
    "MIROFISH_AUTO_RAISE",
    "POLYMARKET_US_LIVE",
    "VENUE_WRITER.kalshi",
    "VENUE_WRITER.coinbase",
    "VENUE_WRITER.alpaca",
    "VENUE_WRITER.kraken",
    "VENUE_WRITER.polymarket",
    "VENUE_WRITER.hyperliquid",
    "LIVE_EXEC_FENCED",
    "LIVE_EXEC_FENCED_SERIES",
    "WEATHER_LIVE",
    "SCALPER_LIVE",
    "NAUTILUS_LIVE",
    "CRYPTO_PAPER_MODE",
    "VALEDIA_VERSION",
    "GIT_SHA",
)

OFF_BOOL = (
    "VALEDIA_TRADING_HALT",
    "KALSHI_FEE_MAP_GATE",
    "KALSHI_EV_PREFLIGHT_GATE",
    "KALSHI_MAKER_FIRST",
    "KALSHI_FILL_LEDGER_DEMOTE",
    "VALEDIA_JEV",
    "VALEDIA_SELF_ADJUST",
    "MIROFISH_AUTO_RAISE",
    "POLYMARKET_US_LIVE",
    "WEATHER_LIVE",
    "SCALPER_LIVE",
    "NAUTILUS_LIVE",
    "NAUTILUS_ENABLED",
    "SPOT_RECONCILE_HTTP",
    "FORTRESS_INTEL_FALLBACK",
    "REBALANCER_AUTOEXEC",
    "VALEDIA_TREASURY_AUTO",
)

ENV_FLAGS = (
    "VALEDIA_JEV",
    "KALSHI_FEE_MAP_GATE",
    "POLYMARKET_US_LIVE",
    "VALEDIA_SELF_ADJUST",
)


def _by_name(snap):
    return {r["name"]: r for r in snap["items"]}


def _env(name, val=None):
    prev = os.environ.get(name)
    if val is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = val
    return prev


def _restore(name, prev):
    if prev is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = prev


def test_defaults_and_shape():
    snap = vc.controls_snapshot()
    assert set(snap) >= {"version", "sha", "branch", "dirty", "items"}
    assert snap["version"] == "0.50.0"
    assert vc.VALEDIA_VERSION == "0.50.0"
    assert vc.VALEDIA_JEV is False
    assert vc.POLYMARKET_US_LIVE is False
    assert vc.jev_enabled() is False
    names = [r["name"] for r in snap["items"]]
    assert names == list(dict.fromkeys(names))
    got = _by_name(snap)
    for n in REQUIRED:
        assert n in got, n
        row = got[n]
        assert set(row) >= {"name", "value", "source", "category", "notes"}
        assert row["source"] in ("const", "env", "file")
    for n in OFF_BOOL:
        assert got[n]["value"] is False, n
        assert got[n]["source"] == "const", n
    assert got["VENUE_WRITER.kalshi"]["value"] == "valedia"
    assert got["VENUE_WRITER.polymarket"]["value"] == "none"
    assert got["VENUE_WRITER.hyperliquid"]["value"] == "none"
    assert "OIL_BRACKET" in got["LIVE_EXEC_FENCED"]["value"]
    assert "KXWTI" in got["LIVE_EXEC_FENCED_SERIES"]["value"]
    assert got["CRYPTO_PAPER_MODE"]["value"] == "PAPER/SHADOW"
    assert got["VALEDIA_VERSION"]["value"] == "0.50.0"


def test_env_source_does_not_stick():
    prevs = {n: _env(n, "1") for n in ENV_FLAGS}
    try:
        snap = vc.controls_snapshot()
        got = _by_name(snap)
        for n in ENV_FLAGS:
            assert got[n]["value"] is True, n
            assert got[n]["source"] == "env", n
    finally:
        for n, prev in prevs.items():
            _restore(n, prev)
    got = _by_name(vc.controls_snapshot())
    for n in ENV_FLAGS:
        assert got[n]["value"] is False, n
        assert got[n]["source"] == "const", n
    assert vc.VALEDIA_JEV is False
    assert vc.POLYMARKET_US_LIVE is False
    assert vc.KALSHI_FEE_MAP_GATE is False


def test_halt_file_source():
    fd, path = tempfile.mkstemp(prefix="halt_", suffix=".json")
    os.close(fd)
    os.remove(path)
    ping = path + ".md"
    prev_e = _env("VALEDIA_TRADING_HALT", None)
    prev_f = _env("VALEDIA_TRADING_HALT_FILE", path)
    prev_p = _env("VALEDIA_TRADING_HALT_PING", ping)
    try:
        vc.write_halt_state(True, reason="test readout", source="test")
        row = _by_name(vc.controls_snapshot())["VALEDIA_TRADING_HALT"]
        assert row["value"] is True
        assert row["source"] == "file"
        vc.write_halt_state(False, reason="clear", source="test")
        row = _by_name(vc.controls_snapshot())["VALEDIA_TRADING_HALT"]
        assert row["value"] is False
        assert row["source"] == "file"
    finally:
        _restore("VALEDIA_TRADING_HALT", prev_e)
        _restore("VALEDIA_TRADING_HALT_FILE", prev_f)
        _restore("VALEDIA_TRADING_HALT_PING", prev_p)
        for p in (path, ping):
            try:
                os.remove(p)
            except OSError:
                pass


def test_panel_and_html():
    p = cockpit.panel()
    assert "controls" in p
    assert p["controls"]["version"] == "0.50.0"
    names = {r["name"] for r in p["controls"]["items"]}
    for n in REQUIRED:
        assert n in names, n
    code, body = cockpit.handle("GET", "/status")
    assert code == 200 and "controls" in body
    code, html = cockpit.handle("GET", "/")
    assert code == 200
    assert 'id="ctl-h"' in html
    assert ">Controls<" in html
    assert 'id="controls"' in html
    low = html.lower()
    assert "create_order" not in low
    assert "place_order" not in low
    assert "venue_writer" not in low


def test_cli_table():
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = vctl.main([])
    finally:
        sys.stdout = old
    out = buf.getvalue()
    assert rc == 0
    assert "VALEDIA_JEV" in out
    assert "POLYMARKET_US_LIVE" in out
    assert "VENUE_WRITER.kalshi" in out
    assert "OFF" in out
    assert "KALSHI_FEE_MAP_GATE" in out
    src = open(os.path.join(HERE, "valedia_controls.py")).read()
    assert "write_halt_state" not in src
    assert "os.environ[" not in src


def test_committed_defaults_stay_false():
    src = open(os.path.join(HERE, "valedia_config.py")).read()
    for stmt in (
        "VALEDIA_JEV           = False",
        "POLYMARKET_US_LIVE = False",
        "KALSHI_FEE_MAP_GATE = False",
        "KALSHI_EV_PREFLIGHT_GATE = False",
        "KALSHI_MAKER_FIRST = False",
        "KALSHI_FILL_LEDGER_DEMOTE = False",
        "NAUTILUS_LIVE = False",
        "MIROFISH_AUTO_RAISE   = False",
        "VALEDIA_SELF_ADJUST   = False",
    ):
        assert stmt in src, stmt
    assert "VALEDIA_JEV           = True" not in src
    assert "POLYMARKET_US_LIVE = True" not in src


if __name__ == "__main__":
    test_defaults_and_shape()
    test_env_source_does_not_stick()
    test_halt_file_source()
    test_panel_and_html()
    test_cli_table()
    test_committed_defaults_stay_false()
    print("CONTROLS-READOUT: PASS")
