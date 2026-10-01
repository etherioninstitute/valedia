"""CRYPTO-PAPER-1: research/paper simulation ledger for crypto.

Simulates fills as if an exchange account existed. Tracks would-have
fee-true P&L. Never wallets, never signing, never create_order/withdraw.

Public REST (--live-data) is allowlisted klines only. No keys.

Ideas only (not copies): jesse report fields (MIT), hummingbot paper-vs-public
(Apache), ccxt ticker shape (MIT). freqtrade pairlist = reimplement, not GPL.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta, timezone

import valedia_config as vc

STATUS = "PAPER/SHADOW"
ALLOWLIST = ("BTC-USDT", "ETH-USDT")
BINANCE_SYM = {"BTC-USDT": "BTCUSDT", "ETH-USDT": "ETHUSDT"}
KLINES_URL = "https://api.binance.us/api/v3/klines?symbol={sym}&interval=1h&limit=200"
FAST, SLOW = 5, 20
CLIP_FRAC = 0.10

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_FIXTURE = os.path.join(_ROOT, "data", "crypto-paper", "bars_fixture.json")
DEFAULT_OUT = os.path.join(_ROOT, "data", "crypto-paper", "equity.json")
DECISION_LOG = os.path.join(_ROOT, "data", "crypto-paper", "decisions.jsonl")
DECISION_SOURCE = "crypto_paper"  # contains "paper"; Kelly / real_track_record skip it
FEE_TABLE = "CRYPTO_FEE_PCT"  # paper fee table. Not fee_engine.STATIC_FEES.
# ponytail: tail rewrite (cap 400) and one 2h stale window. Raise either if a schedule needs it.
DECISION_KEEP = 400
STALE_TICK_S = 7200


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


def load_watchlist_bars(pairs=None, *, live_data=False, fixture_path=None, get_fn=None):
    """Fixture by default. --live-data pulls allowlisted public klines (Binance.US)."""
    want = [norm_pair(p) for p in (pairs or ALLOWLIST)]
    for p in want:
        if p not in ALLOWLIST:
            raise ValueError(f"pair not allowlisted: {p}")
    if live_data:
        out = []
        for p in want:
            out.extend(fetch_public(p, get_fn=get_fn))
        return out
    bars = load_fixture(fixture_path)
    return [b for b in bars if norm_pair(b.get("pair") or "") in set(want)
            or (not b.get("pair") and len(want) == 1)]


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


def paper_fees(maker=None, taker=None, slip=None):
    fee = float(vc.CRYPTO_FEE_PCT)
    return (fee if maker is None else float(maker),
            fee if taker is None else float(taker),
            float(vc.CRYPTO_SLIPPAGE_PCT if slip is None else slip))


def _quote(mid, qty, side, maker, taker, slip):
    mid = float(mid or 0)
    qty = float(qty or 0)
    if mid <= 0 or qty <= 0:
        return 0.0, None, 0.0
    px = fill_px(mid, side, slip)
    fee = fee_usd(px * qty, "taker", maker, taker)
    return round(qty, 8), round(px, 8), round(fee, 8)


def _pnl(side, qty, mid, px, fee):
    qty, mid, px, fee = float(qty), float(mid), float(px), float(fee)
    gross = qty * (mid - px) if side == "buy" else qty * (px - mid)
    return round(gross - fee, 6)


def decision_row(dec, *, pair, maker, taker, slip, bar_ts="", snap=None, cash=10000.0):
    """FTL-shaped paper decision. source is always crypto_paper. Never a settlement."""
    fill = (dec or {}).get("would_have_fill") if isinstance(dec, dict) else None
    side = "buy"
    filled = bool(fill)
    pnl = None
    if fill:
        side = str(fill.get("side") or "buy")
        qty = float(fill.get("qty") or 0)
        mid = float(fill.get("mid") or 0)
        px = fill.get("px")
        fee = float(fill.get("fee") or 0)
        if px is not None and mid > 0 and qty > 0:
            pnl = _pnl(side, qty, mid, px, fee)
        qty, px, fee = round(qty, 8), (None if px is None else round(float(px), 8)), round(fee, 8)
    else:
        close = (snap or {}).get("close")
        mid = float(close) if close else 0.0
        qty_guess = (float(cash) * CLIP_FRAC / mid) if mid > 0 else 0.0
        qty, px, fee = _quote(mid, qty_guess, side, maker, taker, slip)
    return {
        "type": "decision",
        "source": DECISION_SOURCE,
        "strategy": "CRYPTO_PAPER",
        "venue": "paper",
        "ticker": norm_pair(pair),
        "side": side,
        "contracts": qty,
        "qty": qty,
        "entry_price": px,
        "entry_fee": fee,
        "fee_pct": float(taker),
        "slip_pct": float(slip),
        "fee_table": FEE_TABLE,
        "fee_assumption": {
            "table": FEE_TABLE,
            "maker": float(maker),
            "taker": float(taker),
            "slip": float(slip),
            "role": "taker",
        },
        "reason": (dec or {}).get("reason"),
        "pass": bool((dec or {}).get("pass")),
        "filled": filled,
        "ornith": (dec or {}).get("reason"),
        "model": (dec or {}).get("model"),
        "would_have_pnl": pnl,
        "bar_ts": bar_ts or "",
        "create_order": False,
        "live": False,
        "inferred": True,
        "counts_toward_kelly": False,
        "paperTrading": True,
        "ts": _now(),
        "valedia_version": vc.VALEDIA_VERSION,
    }


def read_decisions(path=None):
    path = path or DECISION_LOG
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def append_decision(row, path=None, keep=DECISION_KEEP):
    """Append one paper decision, then keep the last `keep` lines.

    Forces source=crypto_paper. Paper log only.
    """
    path = path or DECISION_LOG
    row = dict(row or {})
    row["type"] = "decision"
    row["source"] = DECISION_SOURCE
    row["create_order"] = False
    row["live"] = False
    row["counts_toward_kelly"] = False
    row.setdefault("fee_table", FEE_TABLE)
    line = json.dumps(row, default=str) + "\n"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lock_path = path + ".write.lock"
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with open(path, "a") as f:
                f.write(line)
            if keep and keep > 0:
                with open(path) as f:
                    lines = f.readlines()
                if len(lines) > keep:
                    with open(path, "w") as f:
                        f.writelines(lines[-keep:])
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return path


def already_filled(path, pair, bar_ts):
    """True when this pair+bar already has a paper fill. Empty bar_ts never matches."""
    if not bar_ts:
        return False
    pair = norm_pair(pair)
    for row in read_decisions(path):
        if (row.get("filled") and row.get("pass")
                and norm_pair(row.get("ticker")) == pair
                and row.get("bar_ts") == bar_ts):
            return True
    return False


def try_tick_lock(path):
    """Non-blocking exclusive lock. None means another tick holds it."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def release_tick_lock(fh):
    if fh is None:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _age_s(ts, now=None):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - t).total_seconds()))


def tick_status(path=None, now=None, stale_s=STALE_TICK_S):
    """Last CE tick for Controls. Missing log is stale, not a silent zero."""
    rows = read_decisions(path)
    last = rows[-1] if rows else None
    if not last:
        return {
            "last_tick_age_s": "none",
            "last_ornith": "none",
            "stale": True,
            "n": 0,
            "source": DECISION_SOURCE,
        }
    age = _age_s(last.get("ts"), now=now)
    stale = age is None or age > int(stale_s)
    return {
        "last_tick_age_s": "none" if age is None else age,
        "last_ornith": str(last.get("ornith") or last.get("reason") or "none"),
        "stale": stale,
        "n": len(rows),
        "source": DECISION_SOURCE,
    }


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
