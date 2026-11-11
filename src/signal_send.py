"""Signal messaging helper — Valedia ASI (transport only).
Sends messages to Burton via VPS relay. Escapes dollar signs.
Money reports must go through signal_verify.deliver(), which checks every
figure against broker/ledger before calling send().
send() is TRI-STATE and never raises:
  True  = relay exited 0 (delivered)
  False = confirmed failure (relay exited non-zero other than 255 / ssh missing) -> retry
  None  = unknown (ssh exit 255, timeout or unexpected error): may or may not have arrived
VALEDIA_SIGNAL_DRY=1 prints instead of sending (tests / dry runs).
(c) GOATWORTHY Post LLC
"""
import os
import subprocess
import sys

TARGET = "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"


def send(msg):
    """Send via the VPS relay. True delivered / False confirmed failure / None unknown."""
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
        if r.returncode == 255:  # ssh's own error: the link may drop after the remote ran
            print(f"[signal_send] ssh exit 255, delivery unknown: "
                  f"{(r.stderr or b'')[:200]!r}", file=sys.stderr)
            return None
        if r.returncode != 0:
            print(f"[signal_send] relay exit {r.returncode}: "
                  f"{(r.stderr or b'')[:200]!r}", file=sys.stderr)
            return False
        return True
    except FileNotFoundError as e:  # no ssh binary: nothing left this host
        print(f"[signal_send] relay unavailable: {e}", file=sys.stderr)
        return False
    except Exception as e:  # timeout / unexpected: delivery state unknown
        print(f"[signal_send] relay outcome unknown: {type(e).__name__}: {e}", file=sys.stderr)
        return None
