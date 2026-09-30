#!/usr/bin/env python3
"""Valedia TradingView alert receiver. Localhost paper fills only.

Bind 127.0.0.1. Never places orders. Does not start ngrok.

  VALEDIA_TV_WEBHOOK_PASSPHRASE=... \\
  python3 tc_auto_webhook_server.py --stdlib

Port: VALEDIA_TV_WEBHOOK_PORT (default 8791). 8787 is Hermes. 8788 is DaviddTech.

A public tunnel (ngrok or otherwise) is an operator choice outside this
process. Leave it off. TradingView cannot reach 127.0.0.1 without one,
and a tunnel is a live-path hazard until Valedia runtime unlocks OMS.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tc_auto_paper as desk  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8791
ROUTE = "/valedia-tv-alert"


def _port(env: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    try:
        return int(env.get("VALEDIA_TV_WEBHOOK_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def book_path_from(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    return str(env.get("TC_AUTO_BOOK") or desk.DEFAULT_BOOK)


def bind_host(host: Optional[str] = None) -> str:
    h = HOST if host is None else host
    if h != "127.0.0.1":
        raise ValueError("localhost_only")
    return h


def health_payload() -> dict:
    return {
        "ok": True,
        "mode": desk.MODE,
        "create_order": False,
        "live": False,
        "venue": desk.VENUE,
        "bankroll": desk.PAPER_BANKROLL,
        "route": ROUTE,
        "fee_books": ["CE-3", "alpaca_static"],
        "ngrok": False,
    }


def handle_order(body: Any, *, env: Optional[dict] = None, book_path: Optional[str] = None) -> tuple:
    env = os.environ if env is None else env
    path = book_path or book_path_from(env)
    code, payload = desk.handle_alert(body, env=env, book_path=path)
    payload["create_order"] = False
    payload["live"] = False
    return code, payload


def build_fastapi_app(env: Optional[dict] = None, book_path: Optional[str] = None):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Valedia TV paper webhook", docs_url=None, redoc_url=None)
    app.state.env = env  # type: ignore[attr-defined]
    app.state.book_path = book_path  # type: ignore[attr-defined]

    @app.get("/health")
    def health():
        return health_payload()

    @app.post(ROUTE)
    async def valedia_tv_alert(body: dict):
        if not isinstance(body, dict):
            return JSONResponse(
                {"ok": False, "error": "not_object", "create_order": False, "live": False},
                status_code=422,
            )
        code, payload = handle_order(
            body,
            env=getattr(app.state, "env", None),
            book_path=getattr(app.state, "book_path", None),
        )
        return JSONResponse(payload, status_code=code)

    return app


class _StdlibHandler:
    def __init__(self, env=None, book_path=None):
        self.env = env
        self.book_path = book_path

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
                if self.path.split("?", 1)[0] != ROUTE:
                    return self._json(404, {"ok": False, "error": "not_found", "create_order": False})
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8") or "null")
                except json.JSONDecodeError:
                    return self._json(422, {"ok": False, "error": "invalid_json", "create_order": False})
                code, payload = handle_order(body, env=outer.env, book_path=outer.book_path)
                return self._json(code, payload)

            def log_message(self, fmt, *args):  # quiet
                sys.stderr.write("[tc-auto-webhook] " + (fmt % args) + "\n")

        return H


def smoke_script(passphrase: str) -> list:
    """Flat-price round trips. Fee drag is the result. Sizing reference only."""
    common = {"passphrase": passphrase, "strategy": "supertrend_paper"}
    btc = {"ticker": "COINBASE:BTCUSD", "price": "83360.03"}
    aapl = {"ticker": "AAPL", "price": "329.4"}
    return [
        {**common, **btc, "action": "buy"},
        {**common, **btc, "action": "sell"},
        {**common, **aapl, "action": "buy"},
        {**common, **aapl, "action": "sell"},
    ]


def render_smoke_md(doc: dict) -> str:
    b = doc["book"]
    lines = [
        "# TC auto-trading paper smoke",
        "",
        f"**When:** {doc['ts']}",
        f"**Bankroll:** ${b['bankroll']:.2f} paper (Robinhood freely tradeable cash, sizing reference only).",
        f"**Path:** {doc['transport']} `POST /valedia-tv-alert` on 127.0.0.1.",
        "",
        "Flat mids (BTC 83360.03, AAPL 329.4) so P&L is fee and slippage drag, not a price story.",
        "Size is 10% of paper equity, the SuperTrend pine `default_qty_value`. Not Kelly.",
        "",
        "| | |",
        "|---|---|",
        f"| Cash left | {b['cash']} |",
        f"| Fees paid | {b['fees_paid']} |",
        f"| Realized P&L | {b['realized_pnl']} |",
        f"| P&L vs $504 | {b['pnl_vs_bankroll']} |",
        f"| Fills | {b['fills']} |",
        f"| Open positions | {json.dumps(b['positions'])} |",
        "| create_order | false |",
        "| live | false |",
        "",
        "## Fills",
        "",
        "| ticker | side | qty | mid | px | fee | book | realized |",
        "|---|---|---:|---:|---:|---:|---|---:|",
    ]
    for fill in doc["fills"]:
        lines.append(
            "| {ticker} | {side} | {qty} | {mid} | {px} | {fee} | {fee_book} | {realized} |".format(**fill)
        )
    lines += [
        "",
        "Crypto book is CE-3 (`CRYPTO_FEE_PCT` 0.001 + `CRYPTO_SLIPPAGE_PCT` 0.0005).",
        "Equity book is `fee_engine.STATIC_FEES['alpaca'].taker` (0) + 1 bp slippage.",
        "No venue order. Oil stayed NO_HARVEST. No public tunnel was started.",
        "",
    ]
    return "\n".join(lines) + "\n"


def run_http_smoke(report_json: str, report_md: str) -> dict:
    """Bind an ephemeral 127.0.0.1 port, post the flat-price script, stop."""
    import tempfile
    import urllib.request
    from http.server import ThreadingHTTPServer

    tmp = tempfile.mkdtemp(prefix="tc-auto-smoke-")
    book = os.path.join(tmp, "book.json")
    passphrase = "smoke-not-a-secret"
    env = {
        "VALEDIA_TV_WEBHOOK_PASSPHRASE": passphrase,
        "VALEDIA_TV_LIVE": "1",
        "TC_AUTO_LIVE": "1",
        "VALEDIA_TRADING_HALT": "0",
        "TC_AUTO_BOOK": book,
    }
    handler = _StdlibHandler(env=env, book_path=book).make_handler()
    httpd = ThreadingHTTPServer((HOST, 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    responses = []
    try:
        health_url = "http://%s:%s/health" % (HOST, port)
        with urllib.request.urlopen(health_url, timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        for alert in smoke_script(passphrase):
            raw = json.dumps(alert).encode("utf-8")
            req = urllib.request.Request(
                "http://%s:%s%s" % (HOST, port, ROUTE),
                data=raw,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                responses.append(json.loads(resp.read().decode("utf-8")))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)

    book_doc = desk.load_book(book)
    fills = []
    fills_path = os.path.join(tmp, "fills.jsonl")
    if os.path.exists(fills_path):
        with open(fills_path) as f:
            fills = [json.loads(line) for line in f if line.strip()]
    doc = {
        "ts": desk._now(),
        "transport": "http://127.0.0.1:%s" % port,
        "health": health,
        "responses_ok": [bool(r.get("ok")) for r in responses],
        "create_order": False,
        "live": False,
        "ngrok": False,
        "bankroll": desk.PAPER_BANKROLL,
        "book": desk.book_public(book_doc),
        "fills": fills,
        "gates": {
            "create_order": False,
            "live": False,
            "counts_toward_kelly": False,
            "oil": "NO_HARVEST",
            "size_rule": "supertrend_pine_10pct_of_paper_equity",
            "live_flag_on_request_ignored": True,
        },
    }
    blob = json.dumps(doc)
    if passphrase in blob or "api_token" in blob:
        raise RuntimeError("smoke report leaked a secret")
    for r in responses:
        if r.get("create_order") is not False or r.get("live") is not False:
            raise RuntimeError("fence breach in smoke response")
        if r.get("filled") is not True:
            raise RuntimeError("smoke fill missing: %s" % r)
    os.makedirs(os.path.dirname(report_json) or ".", exist_ok=True)
    with open(report_json, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    with open(report_md, "w") as f:
        f.write(render_smoke_md(doc))
    return doc


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--smoke" in argv:
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        report_json = os.path.join(root, "recommendations", "TC-AUTO-PAPER-SMOKE-2026-09-29.json")
        report_md = os.path.join(root, "recommendations", "TC-AUTO-PAPER-SMOKE-2026-09-29.md")
        doc = run_http_smoke(report_json, report_md)
        print(json.dumps({
            "smoke": "pass",
            "cash": doc["book"]["cash"],
            "realized_pnl": doc["book"]["realized_pnl"],
            "fees_paid": doc["book"]["fees_paid"],
            "fills": doc["book"]["fills"],
            "create_order": False,
            "report": report_json,
        }))
        return 0
    port = _port()
    host = bind_host()
    use_stdlib = "--stdlib" in argv
    print(json.dumps({
        "starting": True,
        "host": host,
        "port": port,
        "route": ROUTE,
        "mode": desk.MODE,
        "create_order": False,
        "bankroll": desk.PAPER_BANKROLL,
        "passphrase_env": "VALEDIA_TV_WEBHOOK_PASSPHRASE",
        "passphrase_configured": bool(desk.expected_passphrase()),
        "ngrok": False,
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
