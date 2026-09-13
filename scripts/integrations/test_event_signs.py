#!/usr/bin/env python3
"""Guards RC3 event-sign calibration: inherently-directional catalysts must not be flipped by a
broadly-bullish (or bearish) report tone. Run: python3 test_event_signs.py"""
from mirofish_signal_extractor import extract_events


def _dirs(text):
    return {e["type"]: e["direction"] for e in extract_events(text, "")[0]}


def run():
    # War/conflict in an overwhelmingly bullish report must still be RISK_OFF (the RC3 bug).
    d = _dirs("massive rally surge bullish breakout upside tailwind — yet war and conflict escalate")
    assert d.get("geopolitics") == "RISK_OFF", d
    # Crackdown/lawsuit/sanctions -> RISK_OFF even amid optimism.
    d = _dirs("optimistic recovery, but sec charges and a crackdown lawsuit hit the sector")
    assert d.get("regulation") == "RISK_OFF", d
    # Rate cut / dovish / stimulus -> RISK_ON even amid caution/uncertainty.
    d = _dirs("caution, defensive, uncertainty — the fed signals a rate cut, dovish stimulus ahead")
    assert d.get("monetary_policy") == "RISK_ON", d
    # Rate hike / hawkish -> RISK_OFF.
    d = _dirs("rate hike, hawkish tightening from the fed")
    assert d.get("monetary_policy") == "RISK_OFF", d
    # Ambiguous event (IPO) with no anchor stays context-driven (bullish context -> RISK_ON).
    d = _dirs("a hotly anticipated ipo with a strong rally and bullish breakout")
    assert d.get("ipo") == "RISK_ON", d
    print("event-sign calibration checks: PASS")


if __name__ == "__main__":
    run()
