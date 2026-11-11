#!/usr/bin/env python3
"""Arm/disarm VALEDIA_TRADING_HALT. No orders. No Signal send. No spend.

  python3 scripts/integrations/valedia_trading_halt.py on [reason]
  python3 scripts/integrations/valedia_trading_halt.py off
  python3 scripts/integrations/valedia_trading_halt.py status

Env one-liners (win over the halt file when set):
  export VALEDIA_TRADING_HALT=1
  unset VALEDIA_TRADING_HALT          # or =0 / false / off
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import valedia_config as vc  # noqa: E402


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = (argv[0] if argv else "status").strip().lower()
    if cmd in ("on", "arm", "halt"):
        reason = " ".join(argv[1:]).strip() or "manual on"
        doc = vc.write_halt_state(True, reason=reason, source="cli")
        print(json.dumps({"ok": True, "halted": True, "path": vc.halt_state_path(),
                          "reason": doc.get("reason")}, indent=2))
        print("also: export VALEDIA_TRADING_HALT=1")
        return 0
    if cmd in ("off", "clear", "unhalt"):
        doc = vc.write_halt_state(False, reason="manual off", source="cli")
        print(json.dumps({"ok": True, "halted": False, "path": vc.halt_state_path(),
                          "reason": doc.get("reason")}, indent=2))
        print("also: unset VALEDIA_TRADING_HALT")
        return 0
    if cmd in ("status", "show"):
        print(json.dumps({
            "halted": vc.trading_halted(),
            "reason": vc.halt_reason() or "",
            "env": os.environ.get("VALEDIA_TRADING_HALT"),
            "file": vc.halt_state_path(),
            "ping": vc.halt_ping_path(),
            "version": vc.VALEDIA_VERSION,
        }, indent=2))
        return 0
    print("usage: valedia_trading_halt.py on [reason] | off | status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
