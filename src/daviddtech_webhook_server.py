#!/usr/bin/env python3
"""DaviddTech TradingView webhook — localhost PAPER/SHADOW only.

Bind 127.0.0.1 only. Never create orders. No CCXT, no Cloudflare, no Qwen.

  DAVIDDTECH_WEBHOOK_PASSPHRASE=... \\
  python3 daviddtech_webhook_server.py

  # or TRADINGVIEW_WEBHOOK_PASSPHRASE
  # port: DAVIDDTECH_WEBHOOK_PORT. Code constant stays 8787.
  # Hermes owns 8787. Operator default while that is true: DAVIDDTECH_WEBHOOK_PORT=8788

FastAPI preferred (importable on this Mac). Stdlib http.server fallback if not.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import daviddtech_paper as desk  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def _port(env: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    try:
        return int(env.get("DAVIDDTECH_WEBHOOK_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def expected_passphrase(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    return str(
        env.get("DAVIDDTECH_WEBHOOK_PASSPHRASE")
        or env.get("TRADINGVIEW_WEBHOOK_PASSPHRASE")
        or ""
    )


def bind_host(host: Optional[str] = None) -> str:
    h = HOST if host is None else host
    if h != "127.0.0.1":
        raise ValueError("localhost_only")
    return h


def verify_passphrase(body: dict, env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    expected = expected_passphrase(env)
    if not expected:
        return False
    got = "" if not isinstance(body, dict) else str(body.get("passphrase") or "")
    return hmac.compare_digest(got, expected)


def health_payload() -> dict:
    return {"ok": True, "mode": desk.MODE, "create_order": False, "live": False, "venue": desk.VENUE}


def handle_order(body: Any, *, env: Optional[dict] = None, path: Optional[str] = None) -> tuple:
    """Return (status_code, response_dict). Never places orders."""
    env = os.environ if env is None else env
    if not isinstance(body, dict):
        return 422, {"ok": False, "error": "not_object", "create_order": False}
    if not verify_passphrase(body, env):
        return 401, {"ok": False, "error": "unauthorized", "create_order": False}
    ok, reason = desk.validate_payload(body)
    if not ok:
        return 422, {"ok": False, "error": reason, "create_order": False}
    halt = desk.trading_halted(env)
    row = desk.to_decision(body, halt=halt, env=env)
    # Absolute fence
    row["create_order"] = False
    row["live"] = False
    desk.append_decision(row, path=path)
    return 200, {
        "ok": True,
        "create_order": False,
        "live": False,
        "mode": desk.MODE,
        "decision": desk.public_fields(row),
    }


def build_fastapi_app(env: Optional[dict] = None, decision_path: Optional[str] = None):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="DaviddTech paper webhook", docs_url=None, redoc_url=None)
    app.state.env = env  # type: ignore[attr-defined]
    app.state.decision_path = decision_path  # type: ignore[attr-defined]

    @app.get("/health")
    def health():
        return health_payload()

    @app.post("/tradingview-to-webhook-order")
    async def tradingview_to_webhook_order(body: dict):
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "not_object", "create_order": False}, status_code=422)
        code, payload = handle_order(
            body,
            env=getattr(app.state, "env", None),
            path=getattr(app.state, "decision_path", None),
        )
        return JSONResponse(payload, status_code=code)

    return app


class _StdlibHandler:
    """Minimal stdlib fallback with the same routes."""

    def __init__(self, env=None, decision_path=None):
        self.env = env
        self.decision_path = decision_path

    def make_handler(self):
        from http.server import BaseHTTPRequestHandler
        outer = self

        class H(BaseHTTPRequestHandler):
            def _json(self, code, obj):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                if self.path.split("?", 1)[0] == "/health":
                    return self._json(200, health_payload())
                return self._json(404, {"ok": False, "error": "not_found", "create_order": False})

            def do_POST(self):  # noqa: N802
                if self.path.split("?", 1)[0] != "/tradingview-to-webhook-order":
                    return self._json(404, {"ok": False, "error": "not_found", "create_order": False})
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8") or "null")
                except json.JSONDecodeError:
                    return self._json(422, {"ok": False, "error": "invalid_json", "create_order": False})
                code, payload = handle_order(body, env=outer.env, path=outer.decision_path)
                return self._json(code, payload)

            def log_message(self, fmt, *args):  # quiet
                sys.stderr.write("[daviddtech-webhook] " + (fmt % args) + "\n")

        return H


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port = _port()
    host = bind_host()
    use_stdlib = "--stdlib" in argv
    print(json.dumps({
        "starting": True,
        "host": host,
        "port": port,
        "mode": desk.MODE,
        "create_order": False,
        "passphrase_env": "DAVIDDTECH_WEBHOOK_PASSPHRASE|TRADINGVIEW_WEBHOOK_PASSPHRASE",
        "passphrase_configured": bool(expected_passphrase()),
    }))
    if not use_stdlib:
        try:
            import uvicorn  # noqa: F401
            app = build_fastapi_app()
            uvicorn.run(app, host=host, port=port, log_level="info")
            return 0
        except ImportError:
            print(json.dumps({"warn": "fastapi/uvicorn missing; falling back to stdlib http.server"}), file=sys.stderr)
    from http.server import ThreadingHTTPServer
    handler = _StdlibHandler().make_handler()
    httpd = ThreadingHTTPServer((host, port), handler)
    print(json.dumps({"backend": "stdlib", "listening": "%s:%s" % (host, port)}))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
