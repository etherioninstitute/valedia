"""Valedia read-only cockpit — OpenTerminal GET panel (H4).

Polls existing JSON + FTL. GET /status only. Non-GET → 405.
No OMS, no venue send.

Attributed: status-GET / flash-on-change panel idea from OpenTerminal (MIT).
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

GET_PATHS = frozenset({"/", "/status", "/api/valedia/status"})


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


def panel(now=None):
    ident = vc.version_identity()
    now = now or datetime.now(timezone.utc)
    models = _MODELS
    queue = _QUEUE or os.path.join(models, "nautilus_intent_queue.jsonl")
    neural = _load_json(os.path.join(models, "mirofish_neural_enrichment.json"))
    personas = _load_json(os.path.join(models, "persona_lens_scores.json"))

    ftl_block = {"n": 0, "net": 0.0, "unreconciled": 0, "profit_is_fee_true": True}
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
        ftl_block = {
            "n": int(tr.get("n") or 0),
            "net": float(tr.get("net") or 0),
            "unreconciled": unrec,
            "profit_is_fee_true": True,
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

    miro = {
        "weight": float(vc.effective_mirofish_weight()),
        "unproven_cap": float(vc.MIROFISH_UNPROVEN_CAP),
        "auto_raise": bool(vc.MIROFISH_AUTO_RAISE),
        "n_scored": None,
        "directional_accuracy": None,
        "proven": False,
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

    return {
        "ts": now.isoformat(),
        "valedia_version": ident["version"],
        "stamp": vc.version_stamp(),
        "sha": ident["sha"],
        "dirty": bool(ident["dirty"]),
        "mode": cockpit_mode(),
        "ftl": ftl_block,
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
        "get_only": True,
        "no_orders": True,
        "auto_raise": bool(vc.MIROFISH_AUTO_RAISE),
        "trading_halt": bool(vc.trading_halted()),
        "trading_halt_reason": vc.halt_reason() if vc.trading_halted() else "",
    }


def handle(method, path):
    """Return (status_code, body_dict). GET-only."""
    method = str(method or "").upper()
    path = (path or "/").split("?", 1)[0]
    if method != "GET":
        return 405, {"error": "GET only", "get_only": True, "no_orders": True}
    if path not in GET_PATHS:
        return 404, {"error": "not found"}
    return 200, panel()


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(*handle("GET", self.path))

    def do_POST(self):
        self._send(*handle("POST", self.path))

    def log_message(self, *args):
        pass


def serve(host="127.0.0.1", port=8765):
    """Loopback GET server. Not started by cron. OpenTerminal may poll it."""
    httpd = HTTPServer((host, int(port)), _Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    import sys
    if "--listen" in sys.argv:
        serve()
    else:
        print(json.dumps(panel(), indent=2))
