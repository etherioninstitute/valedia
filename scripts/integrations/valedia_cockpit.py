"""Valedia read-only cockpit — OpenTerminal glance (H4 + OPENTERMINAL-VALEDIA).

Polls existing JSON + FTL + pnl_tracker. GET /status JSON. GET / HTML.
Non-GET → 405. No OMS, no venue send.

Attributed: status-GET panel idea from OpenTerminal (MIT).
Not a fork of their Next app; no AiWidget, no portfolio buy.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import valedia_config as vc

MODELS = os.path.expanduser("~/.openclaw/workspace/models")
_MODELS = MODELS
_LEDGER = None
_QUEUE = None
_HERE = os.path.dirname(os.path.abspath(__file__))
_UI = os.path.join(_HERE, "valedia_cockpit.html")

JSON_PATHS = frozenset({"/status", "/api/valedia/status"})
UI_PATHS = frozenset({"/", "/ui", "/index.html"})
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
EARS_NOTE = "Halt blocks new orders only. MiroFish and data streams keep gathering."


def set_paths(models_dir=None, ledger=None, intent_queue=None):
    global _MODELS, _LEDGER, _QUEUE
    if models_dir is not None:
        _MODELS = models_dir
    if ledger is not None:
        _LEDGER = ledger
    if intent_queue is not None:
        _QUEUE = intent_queue


def cockpit_mode(cfg=None):
    """Exactly one of LIVE / DRY / SHADOW / FENCED. This lane defaults FENCED."""
    cfg = cfg or vc
    if any((
        getattr(cfg, "NAUTILUS_LIVE", False),
        getattr(cfg, "SCALPER_LIVE", False),
        getattr(cfg, "WEATHER_LIVE", False),
        getattr(cfg, "REBALANCER_AUTOEXEC", False),
    )):
        return "LIVE"
    if getattr(cfg, "EXEC_DRY_RUN", False):
        return "DRY"
    return "FENCED"


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _shadow_counts(path):
    n = disp = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    if json.loads(line).get("dispatchable"):
                        disp += 1
                except Exception:
                    pass
    except OSError:
        pass
    return {"intents": n, "dispatchable": disp}


def _broker_block(models):
    data = _load_json(os.path.join(models, "pnl_tracker.json")) or {}
    s = data.get("summary") if isinstance(data, dict) else None
    s = s if isinstance(s, dict) else {}
    wins = int(s.get("wins") or 0)
    losses = int(s.get("losses") or 0)
    cash = s.get("current_balance")
    deployed = s.get("current_deployed")
    net = s.get("net_pnl")
    return {
        "label": "broker (pnl_tracker.json) ≠ FTL",
        "source": "pnl_tracker.json",
        "wins": wins,
        "losses": losses,
        "n": wins + losses,
        "net": None if net is None else float(net),
        "cash": None if cash is None else float(cash),
        "deployed": None if deployed is None else float(deployed),
        "updated": s.get("last_updated"),
    }


def _venue_row(venues, name):
    row = {}
    for v in venues or []:
        if isinstance(v, dict) and v.get("venue") == name:
            row = v
            break
    cash = row.get("cash")
    return {
        "cash": None if cash is None else float(cash),
        "status": row.get("status") or "UNREAD",
        "note": row.get("note") or row.get("error") or "",
    }


def _venues_block(models):
    tre = _load_json(os.path.join(models, "treasury_status.json"))
    if not isinstance(tre, dict):
        return {
            "source": "treasury_status.json missing (unread; no live GET)",
            "ts": None,
            "kalshi": {"cash": None, "status": "UNREAD", "note": "file missing"},
            "polymarket_us": {"cash": None, "status": "UNREAD", "note": "file missing"},
        }
    venues = tre.get("venues") or []
    return {
        "source": "treasury_status.json (cached; cockpit does not live GET)",
        "ts": tre.get("ts"),
        "kalshi": _venue_row(venues, "kalshi"),
        "polymarket_us": _venue_row(venues, "polymarket_us"),
    }


def _ftl_block():
    block = {
        "label": "fee-true FTL",
        "n": 0,
        "net": 0.0,
        "unreconciled": 0,
        "profit_is_fee_true": True,
        "empty": True,
        "note": "n=0 — no fee-true P&L yet; broker numbers are not FTL",
    }
    orig_ledger = None
    try:
        import forward_test_ledger as ftl
        if _LEDGER:
            orig_ledger = ftl.LEDGER
            ftl.LEDGER = _LEDGER
        tr = ftl.real_track_record()
        recs = ftl._read()
        unrec = sum(1 for r in recs if r.get("fill_status") == "UNRECONCILED"
                    or r.get("source") == "unreconciled")
        n = int(tr.get("n") or 0)
        block = {
            "label": "fee-true FTL",
            "n": n,
            "net": float(tr.get("net") or 0),
            "unreconciled": unrec,
            "profit_is_fee_true": True,
            "empty": n == 0,
            "note": ("" if n else
                     "n=0 — no fee-true P&L yet; broker numbers are not FTL"),
        }
    except Exception:
        pass
    finally:
        if orig_ledger is not None:
            try:
                import forward_test_ledger as ftl
                ftl.LEDGER = orig_ledger
            except Exception:
                pass
    return block


def panel(now=None):
    ident = vc.version_identity()
    now = now or datetime.now(timezone.utc)
    models = _MODELS
    queue = _QUEUE or os.path.join(models, "nautilus_intent_queue.jsonl")
    neural = _load_json(os.path.join(models, "mirofish_neural_enrichment.json"))
    personas = _load_json(os.path.join(models, "persona_lens_scores.json"))
    sig = _load_json(os.path.join(models, "mirofish_signal.json")) or {}
    ftl_block = _ftl_block()
    broker = _broker_block(models)
    venues = _venues_block(models)
    if venues.get("kalshi", {}).get("cash") is None and broker.get("cash") is not None:
        venues["kalshi"] = {
            "cash": broker["cash"],
            "status": "BROKER",
            "note": "pnl_tracker.json current_balance (not treasury GET)",
        }

    miro = {
        "weight": float(vc.effective_mirofish_weight()),
        "unproven_cap": float(vc.MIROFISH_UNPROVEN_CAP),
        "auto_raise": bool(vc.MIROFISH_AUTO_RAISE),
        "n_scored": None,
        "directional_accuracy": None,
        "proven": False,
        "bias": sig.get("overall_bias"),
        "ts": sig.get("ts"),
        "ears_on": True,
    }
    try:
        import mirofish_accuracy_ledger as mal
        stats = mal.accuracy_stats()
        miro["n_scored"] = stats.get("n_scored")
        miro["directional_accuracy"] = stats.get("directional_accuracy")
        miro["proven"] = bool(stats.get("proven"))
        miro["weight"] = float(stats.get("weight") or miro["weight"])
        miro["auto_raise"] = bool(stats.get("auto_raise"))
    except Exception:
        pass

    halted = bool(vc.trading_halted())
    return {
        "ts": now.isoformat(),
        "valedia_version": ident["version"],
        "stamp": vc.version_stamp(),
        "sha": ident["sha"],
        "dirty": bool(ident["dirty"]),
        "mode": cockpit_mode(),
        "ftl": ftl_block,
        "broker": broker,
        "cash": broker.get("cash"),
        "deployed": broker.get("deployed"),
        "cash_source": "pnl_tracker.json summary (Kalshi broker; not FTL)",
        "venues": venues,
        "shadow": _shadow_counts(queue),
        "mirofish": miro,
        "neural": {
            "fed": bool(neural and neural.get("symbols")),
            "idle": not bool(neural and neural.get("symbols")),
            "fusion_touched": bool(neural and neural.get("fusion_touched")),
        },
        "personas": {
            "present": bool(personas),
            "fusion_touched": bool(personas and personas.get("fusion_touched")),
        },
        "ears": {"gathering": True, "note": EARS_NOTE},
        "get_only": True,
        "no_orders": True,
        "auto_raise": bool(vc.MIROFISH_AUTO_RAISE),
        "trading_halt": halted,
        "trading_halt_reason": vc.halt_reason() if halted else "",
        "controls": vc.controls_snapshot(),
    }


def ui_html():
    try:
        with open(_UI, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ("<!doctype html><title>Valedia</title>"
                "<p>UI file missing. GET /status still works.</p>")


def handle(method, path):
    """Return (status_code, body). GET-only. body is dict (JSON) or str (HTML)."""
    method = str(method or "").upper()
    path = (path or "/").split("?", 1)[0]
    if method != "GET":
        return 405, {"error": "GET only", "get_only": True, "no_orders": True}
    if path in UI_PATHS:
        return 200, ui_html()
    if path not in JSON_PATHS:
        return 404, {"error": "not found"}
    return 200, panel()


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        if isinstance(body, dict):
            raw = json.dumps(body).encode()
            ctype = "application/json"
        else:
            raw = body.encode("utf-8") if isinstance(body, str) else body
            ctype = "text/html; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("application/json"):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(*handle("GET", self.path))

    def do_POST(self):
        self._send(*handle("POST", self.path))

    def log_message(self, *args):
        pass


def serve(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """GET server. Not started by cron. Default loopback :8765."""
    httpd = HTTPServer((host, int(port)), _Handler)
    httpd.serve_forever()


def _argv_opt(argv, flag, default):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]
    if "--listen" in argv:
        host = _argv_opt(argv, "--host", DEFAULT_HOST)
        port = int(_argv_opt(argv, "--port", str(DEFAULT_PORT)))
        serve(host, port)
    else:
        print(json.dumps(panel(), indent=2))
