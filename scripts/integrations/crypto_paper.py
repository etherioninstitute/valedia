"""CRYPTO-PAPER-1: research/paper simulation ledger for crypto.

Simulates fills as if an exchange account existed. Tracks would-have
fee-true P&L. Never wallets, never signing, never create_order/withdraw.

Public REST (--live-data) is allowlisted klines only. No keys.

Ideas only (not copies): jesse report fields (MIT), hummingbot paper-vs-public
(Apache), ccxt ticker shape (MIT). freqtrade pairlist = reimplement, not GPL.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import valedia_config as vc

STATUS = "PAPER/SHADOW"
ALLOWLIST = ("BTC-USDT", "ETH-USDT")
BINANCE_SYM = {"BTC-USDT": "BTCUSDT", "ETH-USDT": "ETHUSDT"}
KLINES_URL = "https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&limit=200"
FAST, SLOW = 5, 20
CLIP_FRAC = 0.10

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_FIXTURE = os.path.join(_ROOT, "data", "crypto-paper", "bars_fixture.json")
DEFAULT_OUT = os.path.join(_ROOT, "data", "crypto-paper", "equity.json")


def _now():
    return datetime.now(timezone.utc).isoformat()


def norm_pair(pair):
    s = str(pair or "").upper().replace("_", "-").strip()
    if "-" not in s and s.endswith("USDT"):
        s = s[:-4] + "-USDT"
    return s


def book(cash=10000.0):
    return {"cash": float(cash), "positions": {}, "fees_paid": 0.0, "fills": []}


def bar_mid(bar):
    if bar.get("mid") is not None:
        return float(bar["mid"])
    h, l = bar.get("high"), bar.get("low")
    if h is not None and l is not None:
        return (float(h) + float(l)) / 2.0
    return float(bar["close"])


def fill_px(mid, side, slip):
    m, s = float(mid), float(slip)
    return m * (1.0 + s) if side == "buy" else m * (1.0 - s)


def fee_usd(notional, role, maker, taker):
    pct = float(maker if str(role).lower() == "maker" else taker)
    return abs(float(notional)) * pct


def apply_fill(state, pair, side, qty, mid, *, slip, maker, taker, role="taker", ts=""):
    """Paper fill at mid±slip. Mutates state. None if skipped."""
    qty = float(qty)
    if qty <= 0:
        return None
    px = fill_px(mid, side, slip)
    fee = fee_usd(px * qty, role, maker, taker)
    pos = float(state["positions"].get(pair, 0.0))
    if side == "buy":
        cost = px * qty + fee
        if state["cash"] + 1e-12 < cost:
            return None
        state["cash"] -= cost
        state["positions"][pair] = pos + qty
    else:
        qty = min(qty, pos)
        if qty <= 0:
            return None
        fee = fee_usd(px * qty, role, maker, taker)
        state["cash"] += px * qty - fee
        left = pos - qty
        if left <= 1e-12:
            state["positions"].pop(pair, None)
        else:
            state["positions"][pair] = left
    state["fees_paid"] += fee
    rec = {
        "ts": ts, "pair": pair, "side": side, "qty": round(qty, 8),
        "mid": float(mid), "px": round(px, 8), "fee": round(fee, 8),
        "role": str(role), "source": "paper", "inferred": True,
    }
    state["fills"].append(rec)
    return rec


def mark_to_market(state, marks):
    eq = float(state["cash"])
    for p, q in state["positions"].items():
        eq += float(q) * float(marks.get(p, 0) or 0)
    return eq


def sma(xs, n):
    if n <= 0 or len(xs) < n:
        return None
    return sum(xs[-n:]) / float(n)


def sma_intents(bars, pair, fast=FAST, slow=SLOW):
    """Long-only SMA cross → paper intents. Not orders."""
    pair = norm_pair(pair)
    closes, intents, long = [], [], False
    for b in bars or []:
        if norm_pair(b.get("pair") or pair) != pair:
            continue
        closes.append(float(b["close"]))
        if len(closes) < slow + 1:
            continue
        f0, s0 = sma(closes[:-1], fast), sma(closes[:-1], slow)
        f1, s1 = sma(closes, fast), sma(closes, slow)
        if None in (f0, s0, f1, s1):
            continue
        it = {"ts": b.get("ts"), "pair": pair, "mid": bar_mid(b), "role": "taker"}
        if (not long) and f0 <= s0 and f1 > s1:
            intents.append({**it, "side": "buy"})
            long = True
        elif long and f0 >= s0 and f1 < s1:
            intents.append({**it, "side": "sell"})
            long = False
    return intents


def synthetic_bars(pair="BTC-USDT", n_down=25, n_up=30, n_down2=20, start_px=100.0):
    """Deterministic OHLC so SMA(5/20) crosses. Fixture generator."""
    pair = norm_pair(pair)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    px, bars = float(start_px), []
    moves = [0.994] * n_down + [1.007] * n_up + [0.993] * n_down2
    for i, m in enumerate(moves):
        o = px
        px = px * m
        hi, lo = max(o, px) * 1.001, min(o, px) * 0.999
        bars.append({
            "ts": (start + timedelta(hours=i)).isoformat(),
            "pair": pair,
            "open": round(o, 6), "high": round(hi, 6),
            "low": round(lo, 6), "close": round(px, 6),
            "volume": 1.0,
        })
    return bars


def load_fixture(path=None):
    p = path or DEFAULT_FIXTURE
    with open(p) as f:
        doc = json.load(f)
    if isinstance(doc, list):
        return doc
    bars = doc.get("bars") or []
    pair = norm_pair(doc.get("pair") or "BTC-USDT")
    for b in bars:
        b.setdefault("pair", pair)
    return bars


def parse_klines(raw, pair):
    pair = norm_pair(pair)
    out = []
    for row in raw or []:
        ts = datetime.fromtimestamp(int(row[0]) / 1000.0, tz=timezone.utc).isoformat()
        o, h, l, c, v = (float(row[i]) for i in (1, 2, 3, 4, 5))
        out.append({
            "ts": ts, "pair": pair,
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
    return out


def _http_get(url, timeout=15):
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": "valedia-crypto-paper/0.47"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_public(pair, get_fn=None):
    pair = norm_pair(pair)
    if pair not in ALLOWLIST:
        raise ValueError(f"pair not allowlisted: {pair}")
    url = KLINES_URL.format(sym=BINANCE_SYM[pair])
    raw = (get_fn or _http_get)(url)
    return parse_klines(raw, pair)


def daily_pnl(curve):
    last = {}
    for row in curve or []:
        last[str(row["ts"])[:10]] = float(row["equity"])
    out, prev = [], None
    for d in sorted(last):
        eq = last[d]
        out.append({
            "date": d, "equity": round(eq, 6),
            "pnl": round(0.0 if prev is None else eq - prev, 6),
        })
        prev = eq
    return out


def persist(doc, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, default=str)
    return path


def run(*, fixture_path=None, live_data=False, pair="BTC-USDT", cash=10000.0,
        maker=None, taker=None, slip=None, out_path=None, get_fn=None,
        fast=FAST, slow=SLOW, clip_frac=CLIP_FRAC):
    """Replay bars → SMA paper intents → simulated fills → equity JSON."""
    pair = norm_pair(pair)
    maker = float(vc.CRYPTO_FEE_PCT if maker is None else maker)
    taker = float(vc.CRYPTO_FEE_PCT if taker is None else taker)
    slip = float(vc.CRYPTO_SLIPPAGE_PCT if slip is None else slip)
    cash = float(cash)
    if live_data:
        bars = fetch_public(pair, get_fn=get_fn)
        source = "public"
    else:
        bars = load_fixture(fixture_path)
        source = "fixture"
    bars = [b for b in bars if norm_pair(b.get("pair") or pair) == pair]
    st = book(cash)
    clip = cash * float(clip_frac)
    by_ts = {}
    for it in sma_intents(bars, pair, fast, slow):
        by_ts.setdefault(it["ts"], []).append(it)
    curve, marks = [], {}
    for b in bars:
        mid = bar_mid(b)
        marks[pair] = float(b["close"])
        for it in by_ts.get(b.get("ts"), []):
            px = fill_px(it["mid"], it["side"], slip)
            qty = (clip / px) if it["side"] == "buy" and px else st["positions"].get(pair, 0.0)
            apply_fill(
                st, pair, it["side"], qty, it["mid"],
                slip=slip, maker=maker, taker=taker, role=it.get("role") or "taker",
                ts=b.get("ts") or "",
            )
        curve.append({
            "ts": b.get("ts"),
            "equity": round(mark_to_market(st, marks), 6),
            "cash": round(st["cash"], 6),
            "mark": marks[pair],
        })
    end_eq = curve[-1]["equity"] if curve else cash
    path = persist({
        "ts": _now(),
        "valedia_version": vc.VALEDIA_VERSION,
        "status": STATUS,
        "mode": STATUS,
        "create_order": False,
        "live": False,
        "live_data": bool(live_data),
        "source": source,
        "pair": pair,
        "starting_equity": cash,
        "ending_equity": end_eq,
        "would_have_pnl": round(end_eq - cash, 6),
        "n_fills": len(st["fills"]),
        "fees_paid": round(st["fees_paid"], 6),
        "maker": maker, "taker": taker, "slip": slip,
        "fast": fast, "slow": slow,
        "fills": st["fills"],
        "equity_curve": curve,
        "daily_pnl": daily_pnl(curve),
        "positions": st["positions"],
        "cash": round(st["cash"], 6),
        "note": "research paper simulation ledger; would-have P&L; no wallets; no exchange orders",
    }, out_path or DEFAULT_OUT)
    doc = json.load(open(path))
    doc["path"] = path
    return doc
