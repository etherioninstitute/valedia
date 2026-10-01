"""Signal messaging helper — Valedia ASI (transport only).
Sends messages to Burton via VPS relay. Escapes dollar signs.
Money reports must go through signal_verify.deliver(), which checks every
figure against broker/ledger before calling send().
send() returns True only when the relay exits 0; it never raises.
VALEDIA_SIGNAL_DRY=1 prints instead of sending (tests / dry runs).
(c) GOATWORTHY Post LLC
"""
import os
import subprocess
import sys

TARGET = "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"


def send(msg):
    """Send a message via Signal through VPS relay. True = relay accepted it."""
    msg = str(msg)
    try:
        from valedia_config import version_stamp
        st = version_stamp()
        if st and st not in msg:
            msg = msg.rstrip() + "\n" + st
    except Exception:
        pass
    if os.environ.get("VALEDIA_SIGNAL_DRY") == "1":
        print(f"[signal_send DRY] {msg}")
        return False
    try:
        # Replace $ with USD to survive bash escaping through SSH
        safe = msg.replace('$', 'USD ').replace("'", "'\\''")
        r = subprocess.run([
            'ssh', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no', 'openclaw',
            f"openclaw message send --channel signal --target '{TARGET}' --message '{safe}'"
        ], capture_output=True, timeout=20)
        if r.returncode != 0:
            print(f"[signal_send] relay exit {r.returncode}: "
                  f"{(r.stderr or b'')[:200]!r}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[signal_send] relay failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False
