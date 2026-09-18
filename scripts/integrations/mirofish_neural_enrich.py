"""NT-1: neural-trader regime/signal → MiroFish-adjacent JSON.

Writes models/mirofish_neural_enrichment.json. Does not change fusion
weight, MIROFISH_AUTO_RAISE, Intents, or venue writers. CLI optional and
allowlisted; tests inject dicts (no network).
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import neural_trader_fence as ntf
import valedia_config as vc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
_MODELS = os.path.join(ROOT, "models")
_SIGNAL = None
_OUT = None


def set_paths(models_dir=None, signal_path=None, out_path=None):
    global _MODELS, _SIGNAL, _OUT
    if models_dir:
        _MODELS = models_dir
    _SIGNAL = signal_path or os.path.join(_MODELS, "mirofish_signal.json")
    _OUT = out_path or os.path.join(_MODELS, "mirofish_neural_enrichment.json")
    return _SIGNAL, _OUT


set_paths()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _f(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def canon_symbol(s):
    t = str(s or "").strip().upper().replace("/", "-")
    if t.endswith("USD") and "-" not in t and len(t) > 3:
        return t[:-3] + "-USD"
    return t


def index_by_symbol(payload):
    if isinstance(payload, list):
        out = {}
        for item in payload:
            if isinstance(item, dict) and item.get("symbol"):
                out[canon_symbol(item["symbol"])] = item
        return out
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("symbols"), dict):
        return {canon_symbol(k): v for k, v in payload["symbols"].items()}
    if payload.get("symbol"):
        return {canon_symbol(payload["symbol"]): payload}
    # already symbol-keyed objects
    out = {}
    for k, v in payload.items():
        if k in ("command", "ts", "version", "source") or not isinstance(v, dict):
            continue
        out[canon_symbol(k)] = v
    return out


def normalize_regime(raw):
    raw = raw if isinstance(raw, dict) else {}
    label = raw.get("regime") or raw.get("label") or raw.get("name") or "unknown"
    conf = raw.get("confidence", raw.get("score", 0.0))
    return {"label": str(label), "confidence": _f(conf), "raw": raw}


def normalize_signal(raw):
    raw = raw if isinstance(raw, dict) else {}
    direction = raw.get("direction") or raw.get("side") or raw.get("signal") or "HOLD"
    quality = raw.get("quality", raw.get("score", raw.get("confidence")))
    q = None if quality is None else _f(quality)
    return {"direction": str(direction), "quality": q, "raw": raw}


def mirofish_symbols(signal_doc):
    sigs = (signal_doc or {}).get("signals") or {}
    if isinstance(sigs, dict):
        return [canon_symbol(k) for k in sigs]
    return []


def build_enrichment(mirofish_signal, regime_by_symbol=None, signal_by_symbol=None):
    regimes = index_by_symbol(regime_by_symbol or {})
    signals = index_by_symbol(signal_by_symbol or {})
    symbols = mirofish_symbols(mirofish_signal)
    if not symbols:
        symbols = sorted(set(regimes) | set(signals))
    pin = ntf.installed_version() or "2.8.11"
    out_syms = {}
    for sym in symbols:
        out_syms[sym] = {
            "regime": normalize_regime(regimes.get(sym) or {}),
            "signal": normalize_signal(signals.get(sym) or {}),
            "mirofish": (mirofish_signal or {}).get("signals", {}).get(sym)
            or (mirofish_signal or {}).get("signals", {}).get(sym.replace("-USD", ""))
            or {},
        }
    return {
        "version": "nt-1",
        "source": "neural-trader",
        "pin": pin,
        "ts": _now(),
        "mirofish_weight_unchanged": True,
        "fusion_touched": False,
        "intents": [],
        "auto_raise": False,
        "valedia_version": vc.VALEDIA_VERSION,
        "symbols": out_syms,
    }


def write_enrichment(obj, path=None):
    path = path or _OUT
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def enrich(signal_path=None, out_path=None, regime=None, signal=None):
    sig = _load(signal_path or _SIGNAL)
    obj = build_enrichment(sig, regime, signal)
    return write_enrichment(obj, out_path or _OUT), obj


def main(argv=None):
    ntf.assert_research_argv(["npx", "neural-trader", "--regime", "--signal"])
    p = argparse.ArgumentParser(description="MiroFish neural enrichment (no live, no weight)")
    p.add_argument("--in", dest="src", default=None)
    p.add_argument("--out", dest="out", default=None)
    p.add_argument("--regime-json", default=None)
    p.add_argument("--signal-json", default=None)
    args = p.parse_args(argv)
    regime = _load(args.regime_json) if args.regime_json else None
    signal = _load(args.signal_json) if args.signal_json else None
    path, obj = enrich(args.src, args.out, regime, signal)
    print(path)
    return 0 if obj.get("fusion_touched") is False else 2


if __name__ == "__main__":
    raise SystemExit(main())
