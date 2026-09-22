"""Valedia read-only cockpit — OpenTerminal glance (H4 + OPENTERMINAL-ACCURACY).

GET /status JSON. GET / HTML. Non-GET → 405. No OMS, no venue send.

Restart (not cron, not from this page):
  python3 scripts/integrations/valedia_cockpit.py --listen
  http://127.0.0.1:8765/

Treasury: live Kalshi GET only when KALSHI_API_KEY_ID is set (existing read
gate; there is no separate Kalshi HTTP flag). Poly GET only when
POLYMARKET_US_HTTP is already on. Otherwise the cache is CACHED or STALE
with age. Never ACH, never a wallet move.

Attributed: status-GET panel idea from OpenTerminal (MIT).
Not a fork of their Next app; no AiWidget, no portfolio buy.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

import valedia_config as vc

MODELS = os.path.expanduser("~/.openclaw/workspace/models")
_MODELS = MODELS
_LEDGER = None
_QUEUE = None
_POLY_LOOP = None
_READERS = None
_PING = None
_LIVE = False
_UNSET = object()
_VENUE_MEMO = {"at": 0.0, "rows": None}
_CONN_MEMO = {"at": 0.0, "rows": None}
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_UI = os.path.join(_HERE, "valedia_cockpit.html")
STALE_S = 15 * 60
# ponytail: 60s memo. Drop it if the glance must be sub-minute.
_MEMO_S = 60

JSON_PATHS = frozenset({"/status", "/api/valedia/status"})
UI_PATHS = frozenset({"/", "/ui", "/index.html"})
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
EARS_NOTE = "Halt blocks new orders only. MiroFish and data streams keep gathering."


def set_paths(models_dir=None, ledger=None, intent_queue=None,
              poly_loop=_UNSET, readers=_UNSET, ping=_UNSET):
    global _MODELS, _LEDGER, _QUEUE, _POLY_LOOP, _READERS, _PING
    if models_dir is not None:
        _MODELS = models_dir
    if ledger is not None:
        _LEDGER = ledger
    if intent_queue is not None:
        _QUEUE = intent_queue
    if poly_loop is not _UNSET:
        _POLY_LOOP = poly_loop
    if readers is not _UNSET:
        _READERS = readers
    if ping is not _UNSET:
        _PING = ping
    restoring = (models_dir == MODELS and ledger is None and intent_queue is None
                 and poly_loop is _UNSET and readers is _UNSET and ping is _UNSET)
    if restoring:
        _POLY_LOOP = None
        _READERS = None
        _PING = None
        _VENUE_MEMO["rows"] = None
        _CONN_MEMO["rows"] = None


def _halted(cfg):
    fn = getattr(cfg, "trading_halted", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            return True
    if cfg is vc:
        return bool(vc.trading_halted())
    return bool(getattr(cfg, "VALEDIA_TRADING_HALT", False))


def _live_flag(cfg):
    if cfg is vc:
        return any((
            vc.NAUTILUS_LIVE,
            vc.SCALPER_LIVE,
            vc.WEATHER_LIVE,
            vc.rebalancer_autoexec_enabled(),
            vc.polymarket_us_live_enabled(),
        ))
    return any(bool(getattr(cfg, n, False)) for n in (
        "NAUTILUS_LIVE", "SCALPER_LIVE", "WEATHER_LIVE",
        "REBALANCER_AUTOEXEC", "POLYMARKET_US_LIVE",
    ))


def _writer_live(cfg, items=None):
    if items is None:
        fn = getattr(cfg, "controls_snapshot", None)
        if not callable(fn):
            return False
        try:
            items = (fn() or {}).get("items") or []
        except Exception:
            return False
    for it in items or []:
        if it.get("category") == "writer" and str(it.get("value") or "").lower() in (
            "valedia", "nautilus",
        ):
            return True
    return False


def _shadow(cfg):
    if any(bool(getattr(cfg, n, False)) for n in (
        "NAUTILUS_SHADOW_COINBASE", "NAUTILUS_SHADOW_VENUES", "INTEL_SHADOW_INTENTS",
    )):
        return True
    return "SHADOW" in str(getattr(cfg, "CRYPTO_PAPER_MODE", "") or "")


def cockpit_mode(cfg=None, items=None):
    """One of LIVE / DRY / SHADOW / FENCED from halt, live flags, and writers."""
    cfg = cfg or vc
    if _halted(cfg):
        return "FENCED"
    if _live_flag(cfg) or _writer_live(cfg, items):
        return "LIVE"
    if getattr(cfg, "EXEC_DRY_RUN", False):
        return "DRY"
    if _shadow(cfg):
        return "SHADOW"
    return "FENCED"


def _mode_why(cfg=None, items=None):
    cfg = cfg or vc
    mode = cockpit_mode(cfg, items)
    if mode == "FENCED" and _halted(cfg):
        return "halt"
    if mode == "LIVE" and _live_flag(cfg):
        return "live flag"
    if mode == "LIVE":
        return "writer"
    if mode == "DRY":
        return "EXEC_DRY_RUN"
    if mode == "SHADOW":
        return "shadow desks"
    return "no live writer"


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


def _parse_ts(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def _age_s(ts, now):
    t = _parse_ts(ts)
    if t is None or now is None:
        return None
    return max(0, int((now - t).total_seconds()))


def _http_on(name):
    env = os.environ.get(name)
    if env is None or str(env).strip() == "":
        return False
    return str(env).strip().lower() in ("1", "true", "yes", "on")


def _rows_from_readers(readers):
    import treasury_status as ts
    out = {}
    for name, payload in (readers or {}).items():
        if name in ("kalshi", "polymarket_us"):
            out[name] = ts._parse_injected(name, payload)
    return out


def _live_rows():
    """Kalshi/Poly GET or an injected reader. None = do not try."""
    if _READERS is not None:
        return _rows_from_readers(_READERS)
    if not _LIVE:
        return None
    now_m = time.monotonic()
    if _VENUE_MEMO["rows"] is not None and now_m - _VENUE_MEMO["at"] < _MEMO_S:
        return _VENUE_MEMO["rows"]
    import socket
    import treasury_status as ts
    rows = {}
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(4)
    try:
        # ponytail: Kalshi has no HTTP flag; the key is the existing read gate.
        if os.environ.get("KALSHI_API_KEY_ID", "").strip():
            rows["kalshi"] = ts._parse_injected("kalshi", ts._live_reader("kalshi"))
        if _http_on("POLYMARKET_US_HTTP"):
            rows["polymarket_us"] = ts._parse_injected(
                "polymarket_us", ts._live_reader("polymarket_us"))
    except Exception as e:
        rows["_error"] = str(e)[:160]
    finally:
        socket.setdefaulttimeout(prev)
    _VENUE_MEMO["at"] = now_m
    _VENUE_MEMO["rows"] = rows
    return rows


def _mark_cached(row, ts, now, missing):
    row = dict(row)
    age = None if missing else _age_s(ts, now)
    row["age_s"] = age
    if missing:
        row["freshness"] = "STALE"
        row["status"] = row.get("status") or "UNREAD"
        return row
    stale = age is None or age > STALE_S
    row["freshness"] = "STALE" if stale else "CACHED"
    if stale and row.get("cash") is not None:
        row["cached_status"] = row.get("status")
        row["status"] = "STALE"
    return row


def _venues_block(models, now=None):
    now = now or datetime.now(timezone.utc)
    tre = _load_json(os.path.join(models, "treasury_status.json"))
    missing = not isinstance(tre, dict)
    venues = [] if missing else (tre.get("venues") or [])
    ts = None if missing else tre.get("ts")
    block = {
        "ts": ts,
        "age_s": None if missing else _age_s(ts, now),
        "kalshi": _mark_cached(_venue_row(venues, "kalshi"), ts, now, missing),
        "polymarket_us": _mark_cached(
            _venue_row(venues, "polymarket_us"), ts, now, missing),
    }
    if missing:
        block["kalshi"]["note"] = "file missing"
        block["polymarket_us"]["note"] = "file missing"
        block["polymarket_us"]["status"] = "UNREAD"
        block["kalshi"]["status"] = "UNREAD"
    live = _live_rows() or {}
    for name in ("kalshi", "polymarket_us"):
        got = live.get(name)
        if isinstance(got, dict) and got.get("cash") is not None:
            got = dict(got)
            got["freshness"] = "LIVE"
            got["age_s"] = 0
            block[name] = got
            continue
        if isinstance(got, dict) and (got.get("error") or got.get("note")):
            why = got.get("error") or got.get("note")
            block[name]["note"] = (
                str(block[name].get("note") or "") + " live GET failed: " + str(why)[:120]
            ).strip()
            block[name]["freshness"] = "STALE"
            if block[name].get("cash") is not None:
                block[name]["cached_status"] = block[name].get("cached_status") or block[name].get("status")
                block[name]["status"] = "STALE"
    bits = []
    if any(block[n].get("freshness") == "LIVE" for n in ("kalshi", "polymarket_us")):
        bits.append("live GET")
    if missing:
        bits.append("treasury_status.json missing")
    else:
        age = block.get("age_s")
        stale = any(block[n].get("freshness") == "STALE" for n in ("kalshi", "polymarket_us"))
        label = "STALE" if stale else "cached"
        bits.append("treasury_status.json " + label + (
            " age=unknown" if age is None else f" age={age}s"))
    if not _http_on("POLYMARKET_US_HTTP") and block["polymarket_us"].get("freshness") != "LIVE":
        bits.append("POLYMARKET_US_HTTP off")
    block["source"] = "; ".join(bits)
    return block


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


def _poly_loop_block():
    path = _POLY_LOOP or os.path.join(_ROOT, "data", "poly-loop", "last_run.json")
    doc = _load_json(path)
    empty = {
        "present": False, "n": None, "skip_reasons": {}, "live": None,
        "status": None, "ts": None, "path": "data/poly-loop/last_run.json",
    }
    if not isinstance(doc, dict):
        return empty
    reasons = doc.get("skip_reasons")
    n = doc.get("n")
    return {
        "present": True,
        "n": None if n is None else int(n),
        "skip_reasons": reasons if isinstance(reasons, dict) else {},
        "live": bool(doc.get("live")),
        "status": doc.get("status"),
        "ts": doc.get("ts"),
        "path": "data/poly-loop/last_run.json",
    }


def _tcp(host, port):
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        s.close()


_CONN = (
    ("litellm", "127.0.0.1", 4000),
    ("bionic", "127.0.0.1", 1234),
    ("ruflo", "127.0.0.1", 8810),
    ("kalshi", "api.elections.kalshi.com", 443),
)


def _connections():
    """Alert-only pings. No restart, no heal."""
    if _PING is None and not _LIVE:
        return [{"name": n, "ok": None, "status": "SKIP"} for n, _h, _p in _CONN]
    now_m = time.monotonic()
    if _PING is None and _CONN_MEMO["rows"] is not None and now_m - _CONN_MEMO["at"] < 30:
        return _CONN_MEMO["rows"]
    fn = _PING or _tcp
    rows = []
    for name, host, port in _CONN:
        try:
            ok = bool(fn(host, port))
        except Exception:
            ok = False
        rows.append({"name": name, "ok": ok, "status": "OK" if ok else "FAIL"})
    if _PING is None:
        _CONN_MEMO["at"] = now_m
        _CONN_MEMO["rows"] = rows
    return rows


def render_controls_html(items):
    """Every snapshot row. Bool → ON/OFF. Source badge is const | env | file."""
    parts = []
    for it in items or []:
        name = escape(str(it.get("name") or ""), quote=True)
        src = escape(str(it.get("source") or ""), quote=True)
        notes = escape(str(it.get("notes") or ""), quote=True)
        val = it.get("value")
        if isinstance(val, bool):
            shown, klass = ("ON", "on") if val else ("OFF", "off")
        else:
            shown = escape(str(val))
            low = str(val).lower()
            klass = "off" if low in ("none", "off", "false") else (
                "on" if low in ("valedia", "on", "true") else "")
        parts.append(
            f'<div class="row" data-control="{name}" data-source="{src}">'
            f'<span class="k" title="{notes}">{name}</span>'
            f'<span class="v {klass}">{shown}<span class="badge {src}">{src}</span></span>'
            f'</div>'
        )
    return "".join(parts)


def panel(now=None):
    controls = vc.controls_snapshot()
    ident = {
        "version": controls.get("version"),
        "sha": controls.get("sha"),
        "branch": controls.get("branch"),
        "dirty": bool(controls.get("dirty")),
    }
    now = now or datetime.now(timezone.utc)
    models = _MODELS
    queue = _QUEUE or os.path.join(models, "nautilus_intent_queue.jsonl")
    neural = _load_json(os.path.join(models, "mirofish_neural_enrichment.json"))
    personas = _load_json(os.path.join(models, "persona_lens_scores.json"))
    sig = _load_json(os.path.join(models, "mirofish_signal.json")) or {}
    ftl_block = _ftl_block()
    broker = _broker_block(models)
    venues = _venues_block(models, now)
    if venues.get("kalshi", {}).get("cash") is None and broker.get("cash") is not None:
        venues["kalshi"] = {
            "cash": broker["cash"],
            "status": "BROKER",
            "freshness": "BROKER",
            "age_s": None,
            "note": "pnl_tracker.json current_balance (not a treasury GET)",
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
    items = controls.get("items") or []
    return {
        "ts": now.isoformat(),
        "valedia_version": ident["version"],
        "stamp": vc.version_stamp(),
        "sha": ident["sha"],
        "branch": ident["branch"],
        "dirty": bool(ident["dirty"]),
        "identity": vc.version_line(),
        "mode": cockpit_mode(items=items),
        "mode_why": _mode_why(items=items),
        "ftl": ftl_block,
        "broker": broker,
        "cash": broker.get("cash"),
        "deployed": broker.get("deployed"),
        "cash_source": "pnl_tracker.json summary (Kalshi broker; not FTL)",
        "venues": venues,
        "poly_loop": _poly_loop_block(),
        "connections": _connections(),
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
        "controls": controls,
    }


def ui_html():
    try:
        with open(_UI, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return ("<!doctype html><title>Valedia</title>"
                "<p>UI file missing. GET /status still works.</p>")
    try:
        rows = render_controls_html(vc.controls_snapshot().get("items") or [])
    except Exception:
        rows = ""
    return raw.replace(
        '<div id="controls"></div>',
        '<div id="controls">' + rows + '</div>',
        1,
    )


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
    """GET server. Not started by cron. Default loopback :8765.

    Restart: python3 scripts/integrations/valedia_cockpit.py --listen
    """
    global _LIVE
    _LIVE = True
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
        _LIVE = True
        print(json.dumps(panel(), indent=2))
