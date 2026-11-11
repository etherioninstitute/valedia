#!/usr/bin/env python3
"""ARB-OPENSSL-HARDEN — SSLContext loaded once; Session reused; cron 139/134.

No --live. No orders. Parallel HTTPS must not reload verify locations.
Run: /usr/local/bin/python3 test_arb_openssl_harden.py
"""
import inspect
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import latency_arb as la


def test_arb_run_stays_dry():
    arb_run = open(os.path.join(HERE, "arb_run.py")).read()
    assert "dry-run" in arb_run
    assert "mode='execute'" not in arb_run and 'mode="execute"' not in arb_run
    assert "https_session()" in arb_run


def test_poller_reuses_session():
    src = inspect.getsource(la.SpotPriceTracker.poll_prices)
    assert "sess.get(" in src
    assert "requests.get(" not in src
    run_src = inspect.getsource(la.run)
    assert "https_session()" in run_src


def test_cron_139_134_no_retry():
    sh = open(os.path.join(ROOT, "scripts", "cron_runner_mac.sh")).read()
    assert "exit=$EXIT known OpenSSL/TLS crash — no retry" in sh
    assert "-eq 139" in sh and "-eq 134" in sh
    assert "openssl-tls-crash" in sh
    assert "LATENCY ARB" in sh
    # Must break before diagnose_and_fix on 139/134.
    crash_at = sh.index("known OpenSSL/TLS crash")
    diag_at = sh.index("diagnose_and_fix", crash_at)
    assert crash_at < diag_at


def _selfsigned(tmpdir):
    cert = os.path.join(tmpdir, "c.pem")
    key = os.path.join(tmpdir, "k.pem")
    subprocess.check_call(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key, "-out", cert, "-days", "1", "-nodes",
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cert, key


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":1}')

    def log_message(self, *a):
        pass


def test_parallel_https_does_not_reload_verify_locations():
    la.reset_https_session()
    loads = []
    orig = ssl.SSLContext.load_verify_locations

    def wrapped(self, *a, **k):
        loads.append(1)
        return orig(self, *a, **k)

    ssl.SSLContext.load_verify_locations = wrapped
    httpd = None
    try:
        tmp = tempfile.mkdtemp(prefix="arb-ssl-")
        cert, key = _selfsigned(tmp)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        sess = la.https_session(cafile=cert)
        n = len(loads)
        assert n >= 1, n
        assert la.https_session() is sess
        assert len(loads) == n, (n, len(loads))

        errors = []

        def hit():
            try:
                r = sess.get(f"https://localhost:{port}/", timeout=3)
                if r.status_code != 200:
                    errors.append(r.status_code)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert not errors, errors
        assert len(loads) == n, (n, len(loads))
    finally:
        ssl.SSLContext.load_verify_locations = orig
        la.reset_https_session()
        if httpd is not None:
            httpd.shutdown()


if __name__ == "__main__":
    test_arb_run_stays_dry()
    test_poller_reuses_session()
    test_cron_139_134_no_retry()
    test_parallel_https_does_not_reload_verify_locations()
    print("ARB-OPENSSL-HARDEN: PASS")
