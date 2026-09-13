"""Signal messaging helper — Valedia ASI
Sends messages to Burton via VPS relay. Escapes dollar signs.
(c) GOATWORTHY Post LLC
"""
import subprocess

TARGET = "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"

def send(msg):
    """Send a message via Signal through VPS relay."""
    try:
        # Replace $ with USD to survive bash escaping through SSH
        safe = msg.replace('$', 'USD ').replace("'", "'\\''")
        subprocess.run([
            'ssh', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no', 'openclaw',
            f"openclaw message send --channel signal --target '{TARGET}' --message '{safe}'"
        ], capture_output=True, timeout=20)
    except:
        pass
