#!/usr/bin/env python3
"""Print every Valedia control. Read-only. No flips.

  python3 scripts/integrations/valedia_controls.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import valedia_config as vc  # noqa: E402


def format_table(snap=None):
    snap = snap or vc.controls_snapshot()
    items = snap.get("items") or []
    w = max((len(r["name"]) for r in items), default=4)
    dirty = "dirty" if snap.get("dirty") else "clean"
    lines = [
        f"v{snap.get('version')} {snap.get('sha')} {snap.get('branch')} {dirty}",
        "",
    ]
    hdr = f"{'NAME':<{w}}  {'VALUE':<24}  {'SRC':<6}  {'CATEGORY':<10}  NOTES"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in items:
        val = r.get("value")
        if isinstance(val, bool):
            val = "ON" if val else "OFF"
        else:
            val = str(val)
        lines.append(
            f"{r['name']:<{w}}  {val:<24}  {r.get('source',''):<6}  "
            f"{r.get('category',''):<10}  {r.get('notes','')}"
        )
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print("usage: valedia_controls.py   # read-only table, no flips")
        return 0
    print(format_table())
    return 0


if __name__ == "__main__":
    sys.exit(main())
