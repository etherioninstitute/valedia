#!/usr/bin/env python3
"""Slice 23 — VALEDIA_VERSION + valedia_version.sh identity.

No network, no keys, no --live.
Run: python3 test_slice23.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import valedia_config as vc


def test_version_and_script():
    assert vc.VALEDIA_VERSION == "0.45.0", vc.VALEDIA_VERSION
    ident = vc.version_identity()
    assert ident["version"] == "0.45.0"
    assert ident["sha"]
    assert ident["branch"]
    assert "dirty" in ident
    stamp = vc.version_stamp()
    assert stamp.startswith("v0.45.0 ")
    line = vc.version_line()
    assert line.startswith("v0.45.0 ")
    sh = os.path.join(ROOT, "scripts", "valedia_version.sh")
    assert os.path.isfile(sh)
    out = subprocess.check_output(["bash", sh], cwd=ROOT, timeout=5).decode().strip()
    parts = out.split()
    assert parts[0] == "v0.45.0", out
    assert parts[-1] in ("clean", "dirty"), out
    assert ident["sha"] in out
    assert ident["branch"] in out


if __name__ == "__main__":
    test_version_and_script()
    print("slice23 versioning: PASS")
