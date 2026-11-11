"""VPS Proxy Helper — Routes IP-locked API calls through VPS SOCKS tunnel.
Keeps Burton's home IP hidden. Requires: ssh -D 1080 -f -N root@85.239.234.32

Usage:
    from vps_proxy import get_proxies, ensure_tunnel
    
    # In any script that calls IP-locked APIs:
    ensure_tunnel()  # starts SSH tunnel if not running
    r = requests.get(url, proxies=get_proxies())

(c) GOATWORTHY Post LLC.
"""
import subprocess, os, time, requests

PROXY_PORT = 1080
VPS_DIRECT_IP = os.environ.get("VALEDIA_VPS_EGRESS_IP", "85.239.234.32")  # Direct IP — never use Tailscale IP (opens Safari for SSH auth)

def get_proxies():
    """Return SOCKS5 proxy dict for requests library."""
    return {
        'https': f'socks5h://127.0.0.1:{PROXY_PORT}',
        'http': f'socks5h://127.0.0.1:{PROXY_PORT}'
    }

def is_tunnel_alive():
    """Check if SOCKS tunnel is running."""
    try:
        r = subprocess.run(['lsof', '-i', f':{PROXY_PORT}'], capture_output=True, timeout=3)
        return b'ssh' in r.stdout
    except: return False

def start_tunnel():
    """Start SSH SOCKS tunnel to VPS via Tailscale."""
    if is_tunnel_alive():
        return True
    try:
        subprocess.Popen([
            'ssh', '-D', str(PROXY_PORT), '-f', '-N',
            '-i', os.path.expanduser('~/.ssh/openclaw_vps'),
            '-o', 'ServerAliveInterval=60',
            '-o', 'ServerAliveCountMax=3',
            '-o', 'ExitOnForwardFailure=yes',
            '-o', 'StrictHostKeyChecking=yes',
            '-o', 'PasswordAuthentication=no',
            '-o', 'BatchMode=yes',
            f'root@{VPS_DIRECT_IP}'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        return is_tunnel_alive()
    except: return False

def ensure_tunnel(max_retries=3):
    """Ensure tunnel is running. Retry if needed."""
    for i in range(max_retries):
        if is_tunnel_alive():
            return True
        if start_tunnel():
            return True
        time.sleep(3)
    return False

def test_proxy():
    """Verify proxy routes through VPS IP."""
    if not ensure_tunnel():
        return {"ok": False, "error": "Tunnel not running"}
    try:
        r = requests.get('https://ifconfig.me', proxies=get_proxies(), timeout=10)
        return {"ok": True, "exit_ip": r.text.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

if __name__ == "__main__":
    print("Testing VPS proxy tunnel...")
    result = test_proxy()
    print(f"  Tunnel alive: {is_tunnel_alive()}")
    print(f"  Exit IP: {result.get('exit_ip', 'FAILED')}")
    print(f"  OK: {result['ok']}")
