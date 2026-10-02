"""Valedia Unified Brain v3.1 — Plug & Play Master Orchestrator
========================================================================
DESIGN GOALS:
  - Runs on ANY system: Mac primary, VPS backup, any future node
  - Trades ANY asset class: stocks, crypto, forex, commodities, ETFs
  - Self-configures: detects available platforms, adapts gracefully
  - Maximally profitable: two-filter system, day-of-week gate, boosters
  - Zero single points of failure: each stage degrades gracefully

PIPELINE:
  Config → Intel → Universe → Kernel v16e → ERE → Fusion → Filter v3
  → Kelly → Fee Gate → Execute → Log → Self-Improve

FILTER v3 (empirically derived from 2025 backtests):
  Equity  : votes≥8 + MOM regime + 5d trend confirms
  Crypto  : MOM regime + directional trend (5d+3d both confirm)
  DAY GATE: skip Mon/Tue (30-44% hit). Prefer Wed/Thu (66-86% hit).
  BOOSTERS: low-vol day, weekly trend, RSI alignment → position ×1.45

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, traceback, platform
import numpy as np
from datetime import datetime, timezone
from valedia_config import (  # shared source of truth (drift-guarded)
    MIROFISH_WEIGHT, KELLY_MIN_PROVEN_TRADES, FUSION_WEIGHTS, fusion_weights_for,
    version_stamp,
)
try:
    import forward_test_ledger as _ftl  # the REAL fee-inclusive track record (single source for sizing)
except Exception:
    _ftl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nimble_wiring_spot as nws  # Nimble 2 entry veto (shadow default; SELL never asked)

# Memory system (graceful — works even if Memori unavailable)
try:
    from valedia_memory import (
        capture_market_condition, capture_trade, inject_memory_context,
        recall_memories, memory_status
    )
    MEMORY_AVAILABLE = True
except Exception:
    MEMORY_AVAILABLE = False
    def capture_market_condition(*a, **k): pass
    def capture_trade(*a, **k): pass
    def inject_memory_context(q, lines): return lines
    def recall_memories(*a, **k): return []
    def memory_status(): return "Memory: unavailable"


# ══════════════════════════════════════════════════════════════════════
# SYSTEM CONFIG — auto-detects host, adapts paths + platforms
# ══════════════════════════════════════════════════════════════════════

def get_system_config():
    """Detect current system and return appropriate config."""
    home     = os.path.expanduser("~")
    hostname = platform.node().lower()
    is_mac   = platform.system() == "Darwin"
    is_linux = platform.system() == "Linux"

    # Paths that work on both Mac and Linux/VPS
    base = os.path.join(home, ".openclaw")
    cfg  = {
        "base_dir":     base,
        "scripts_dir":  os.path.join(base, "workspace/scripts/integrations"),
        "models_dir":   os.path.join(base, "workspace/models"),
        "logs_dir":     os.path.join(base, "logs"),
        "env_file":     os.path.join(base, ".env"),
        "is_mac":       is_mac,
        "is_linux":     is_linux,
        "hostname":     hostname,
        # Platform availability — try imports, mark failed ones unavailable
        "has_alpaca":   False,
        "has_coinbase": False,
        "has_kraken":   False,
        "has_kalshi":   False,
        "has_polymarket": False,
    }

    # Detect available platforms
    for mod, key in [
        ("alpaca_executor",  "has_alpaca"),
        ("coinbase_trader",  "has_coinbase"),
        ("kraken_client",    "has_kraken"),
        ("kalshi_auth",      "has_kalshi"),
        ("polymarket_client","has_polymarket"),
    ]:
        try:
            __import__(mod)
            cfg[key] = True
        except Exception:
            pass

    return cfg


SYS = get_system_config()


# ══════════════════════════════════════════════════════════════════════
# UNIVERSE — dynamic, expandable, class-tagged
# ══════════════════════════════════════════════════════════════════════

# Core universe — tested and validated
UNIVERSE_CORE = {
    # Crypto (Coinbase)
    "BTC":   ("BTC-USD",  "crypto",  "coinbase"),
    "ETH":   ("ETH-USD",  "crypto",  "coinbase"),
    "SOL":   ("SOL-USD",  "crypto",  "coinbase"),
    "XRP":   ("XRP-USD",  "crypto",  "coinbase"),
    "ADA":   ("ADA-USD",  "crypto",  "coinbase"),
    "DOGE":  ("DOGE-USD", "crypto",  "coinbase"),
    "AVAX":  ("AVAX-USD", "crypto",  "coinbase"),
    "LINK":  ("LINK-USD", "crypto",  "coinbase"),
    # US Equities + ETFs (Alpaca)
    "SPY":   ("SPY",   "equity", "alpaca"),
    "QQQ":   ("QQQ",   "equity", "alpaca"),
    "TSLA":  ("TSLA",  "equity", "alpaca"),
    "NVDA":  ("NVDA",  "equity", "alpaca"),
    "META":  ("META",  "equity", "alpaca"),
    "GOOGL": ("GOOGL", "equity", "alpaca"),
    "AMZN":  ("AMZN",  "equity", "alpaca"),
    "AAPL":  ("AAPL",  "equity", "alpaca"),
    "AMD":   ("AMD",   "equity", "alpaca"),
    "MSFT":  ("MSFT",  "equity", "alpaca"),
    "COIN":  ("COIN",  "equity", "alpaca"),
    "MSTR":  ("MSTR",  "equity", "alpaca"),
    "GLD":   ("GLD",   "equity", "alpaca"),   # gold ETF
    "XLE":   ("XLE",   "equity", "alpaca"),   # energy ETF
    "TLT":   ("TLT",   "equity", "alpaca"),   # long bonds
    # Forex via ETFs (Alpaca)
    "UUP":   ("UUP",   "forex",  "alpaca"),   # USD bull
    "FXE":   ("FXE",   "forex",  "alpaca"),   # EUR/USD
    "FXY":   ("FXY",   "forex",  "alpaca"),   # JPY/USD
    "FXB":   ("FXB",   "forex",  "alpaca"),   # GBP/USD
    "FXA":   ("FXA",   "forex",  "alpaca"),   # AUD/USD
}

# Extended universe — added dynamically when high-conviction scan fires
UNIVERSE_EXTENDED = {
    "PLTR":  ("PLTR",  "equity", "alpaca"),
    "HOOD":  ("HOOD",  "equity", "alpaca"),
    "RIOT":  ("RIOT",  "equity", "alpaca"),   # crypto-correlated
    "MARA":  ("MARA",  "equity", "alpaca"),   # crypto-correlated
    "IWM":   ("IWM",   "equity", "alpaca"),   # small caps
    "ARKK":  ("ARKK",  "equity", "alpaca"),   # innovation
    "DOT":   ("DOT-USD","crypto", "coinbase"),
    "UNI":   ("UNI-USD","crypto", "coinbase"),
    "ATOM":  ("ATOM-USD","crypto","coinbase"),
}

def get_active_universe():
    """Return core + extended + dynamically discovered assets."""
    u = {}
    for name, (ticker, cls, plat) in {**UNIVERSE_CORE, **UNIVERSE_EXTENDED}.items():
        plat_key = f"has_{plat}"
        if SYS.get(plat_key, False):
            u[name] = (ticker, cls, plat)
    if not u:
        u = {**UNIVERSE_CORE, **UNIVERSE_EXTENDED}

    # Dynamically discovered assets from dynamic_universe.py
    hot_path = os.path.join(SYS["models_dir"], "hot_list.json")
    try:
        with open(hot_path) as f:
            hl = json.load(f)
        assets = hl.get("assets", {})
        added  = 0
        for sym, d in assets.items():
            if sym in u: continue          # already in core universe
            ticker = d.get("ticker","")
            cls    = d.get("class","crypto")
            plat   = d.get("platform","coinbase")
            if not ticker: continue
            # Tag as discovered so brain applies tighter position limits
            u[sym] = (ticker, cls, plat, "discovered",
                      d.get("max_trade_pct", 0.02),
                      d.get("kelly_mult", 0.5),
                      d.get("sell_threshold", 50),
                      d.get("cg_id", ""))
            added += 1
    except Exception:
        pass

    return u


# ══════════════════════════════════════════════════════════════════════
# FILTER ENGINE v3 — high-conviction gate
# ══════════════════════════════════════════════════════════════════════

DOW_NAMES = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"}

def compute_signal_quality(prices, kernel_sig, asset_class="equity"):
    """
    Full filter + booster computation. Always returns a complete dict
    so callers never get KeyError.
    """
    # ── Safe defaults (always present in return) ────────────────────
    q = {
        "direction":     kernel_sig.get("direction", "HOLD"),
        "score":         kernel_sig.get("score", 50),
        "votes":         int(kernel_sig.get("votes", 6)),
        "dom":           kernel_sig.get("dom", "?"),
        "dist":          abs(kernel_sig.get("score", 50) - 50),
        "is_buy":        kernel_sig.get("direction") == "BUY",
        "is_sell":       kernel_sig.get("direction") == "SELL",
        "is_hold":       kernel_sig.get("direction") == "HOLD",
        # Filter results
        "passes_core":   False,
        "passes_filter": False,
        "pos_multiplier":1.0,
        "boost_score":   0,
        "boost_labels":  [],
        # Trend defaults
        "p5":0,"p3":0,"p10":0,
        "t5":False,"t3":False,"trend_dir":False,"wk_trend":False,
        # RSI defaults
        "rsi":50,"rsi_align":False,
        # Volatility defaults
        "vol_recent":0.02,"vol_hist":0.02,"low_vol_day":False,
        # Day of week
        "dow":        datetime.now(timezone.utc).weekday(),
        "is_wed_thu": datetime.now(timezone.utc).weekday() in [2,3],
        "is_mon_tue": datetime.now(timezone.utc).weekday() in [0,1],
    }

    # Skip HOLDs and short price series
    if q["is_hold"] or len(prices) < 20:
        return q

    # ── Price trends ────────────────────────────────────────────────
    p = prices
    q["p5"]  = float(p[-1]/p[-6]-1)  if len(p) > 6  else 0.0
    q["p3"]  = float(p[-1]/p[-4]-1)  if len(p) > 4  else 0.0
    q["p10"] = float(p[-1]/p[-11]-1) if len(p) > 11 else 0.0

    q["t5"]       = (q["is_buy"] and q["p5"]>0) or (q["is_sell"] and q["p5"]<0)
    q["t3"]       = (q["is_buy"] and q["p3"]>0) or (q["is_sell"] and q["p3"]<0)
    q["trend_dir"]= q["t5"] and q["t3"]
    q["wk_trend"] = (q["is_buy"] and q["p10"]>0) or (q["is_sell"] and q["p10"]<0)

    # ── RSI (14-period) ─────────────────────────────────────────────
    if len(p) >= 16:
        delta  = np.diff(p[-15:])
        gains  = np.where(delta>0, delta, 0.0)
        losses = np.where(delta<0, -delta, 0.0)
        ag = np.mean(gains[-14:]); al = np.mean(losses[-14:])
        rs = ag / (al + 1e-10)
        q["rsi"]       = float(100 - (100/(1+rs)))
        q["rsi_align"] = (q["is_buy"] and q["rsi"]<45) or (q["is_sell"] and q["rsi"]>55)

    # ── Volatility regime ───────────────────────────────────────────
    if len(p) >= 30:
        ret = np.diff(np.log(np.clip(p, 1e-10, None)))
        q["vol_recent"]  = float(np.std(ret[-10:]))
        q["vol_hist"]    = float(np.std(ret[-30:]))
        q["low_vol_day"] = q["vol_recent"] < q["vol_hist"]

    # ── Core filter ─────────────────────────────────────────────────
    if asset_class == "equity":
        q["passes_core"] = (
            q["votes"] >= 8 and
            q["dom"] == "MOM" and
            q["t5"]
        )
    else:
        # crypto / forex: directional trend (5d+3d both confirm)
        q["passes_core"] = (
            q["dom"] == "MOM" and
            q["trend_dir"]
        )

    # ── Boosters ────────────────────────────────────────────────────
    boosts = [
        q["is_wed_thu"],         # Wed 86.7% / Thu 66.7%
        q["low_vol_day"],        # +3% lift
        not q["is_mon_tue"],     # skip bad days
        q["wk_trend"],           # +1% lift
        q["rsi_align"],          # +1% lift
    ]
    q["boost_score"] = sum(boosts)

    labels = []
    if q["is_wed_thu"]:  labels.append("WED/THU")
    if q["low_vol_day"]: labels.append("LOW_VOL")
    if q["wk_trend"]:    labels.append("WK_TREND")
    if q["rsi_align"]:   labels.append(f"RSI{q['rsi']:.0f}")
    if q["is_mon_tue"]:  labels.append("MON/TUE_SKIP")
    q["boost_labels"] = labels

    # ── Final gate ──────────────────────────────────────────────────
    q["passes_filter"] = q["passes_core"] and not q["is_mon_tue"]

    # Position size multiplier (1.0 → 1.45 based on boosters)
    q["pos_multiplier"] = round(1.0 + 0.15 * min(q["boost_score"], 3), 2)

    return q


# ══════════════════════════════════════════════════════════════════════
# STAGE 1: INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════

def stage_intel():
    """Fear/Greed + macro context. Fails gracefully."""
    import requests
    intel = {"ts": datetime.now(timezone.utc).isoformat(),
             "fear_greed": 50, "fg_label": "Neutral", "fg_signal": 0}
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        fg = r.json()["data"][0]
        intel["fear_greed"] = int(fg["value"])
        intel["fg_label"]   = fg["value_classification"]
        v = intel["fear_greed"]
        if   v <= 20: intel["fg_signal"] = 15
        elif v <= 35: intel["fg_signal"] = 8
        elif v >= 80: intel["fg_signal"] = -15
        elif v >= 65: intel["fg_signal"] = -8
    except Exception:
        pass
    return intel


# ══════════════════════════════════════════════════════════════════════
# STAGE 2: KERNEL v16e
# ══════════════════════════════════════════════════════════════════════

def stage_kernel(prices, asset_class="equity"):
    _neutral = {"score": 50.0, "direction": "HOLD", "size": 0.0, "conf": 0.0,
                "xi": 0, "xiR2": 0, "votes": 0, "svd": 0, "stab": 0,
                "dom": "NEUTRAL", "mom": 0, "snr": 0, "se": 0, "D": 0.5,
                "coh": 0, "gap_s": 0, "vr": 1.0, "rsi": 50.0}
    try:
        from kernel_predictor_v16e import KernelPredictor
        kp = KernelPredictor(asset_class)
        result = kp.predict(prices)
        if isinstance(result, dict):
            return result
        return _neutral
    except Exception:
        return _neutral


# ══════════════════════════════════════════════════════════════════════
# STAGE 3: ERE SAFETY
# ══════════════════════════════════════════════════════════════════════

def stage_ere_safety(ticker):
    """Read one dated state. Naked price arrays cannot establish freshness."""
    try:
        from ere_runtime import read_gate
        return read_gate(ticker, models_dir=SYS["models_dir"])
    except Exception:
        import logging
        logging.getLogger("ere").exception("Brain ERE stage CLOSED")
        return {"safe": False, "multiplier": 0.0, "status": "HALT",
                "level": "HALT", "halt": True, "data_quality": "INSUFFICIENT"}


# ══════════════════════════════════════════════════════════════════════
# STAGE 4: SIGNAL FUSION
# ══════════════════════════════════════════════════════════════════════

def stage_fusion(kernel_sig, ere_safety, intel,
                 ml_score=50, mirofish_score=50, asset_class="equity"):
    k_score     = float(kernel_sig.get("score", 50))
    intel_boost = intel.get("fg_signal", 0)
    w = fusion_weights_for(asset_class)

    fused = (
        k_score        * w["kernel"] +
        mirofish_score * w["mirofish"] +
        ml_score       * w["ml"] +
        (50 + intel_boost) * w["intel"] +
        50             * w["remainder"]
    )
    # ERE is a position-risk gate, not an alpha/confidence transform.
    return round(float(np.clip(fused, 5, 95)), 1)


# ══════════════════════════════════════════════════════════════════════
# STAGE 5: KELLY SIZING
# ══════════════════════════════════════════════════════════════════════

def stage_kelly(fused_score, equity, max_pct=0.04, pos_multiplier=1.0,
                expected_return_pct=None, regime="SIDEWAYS", category=None,
                track_record_mult=None, risk_factor=None, concurrent_bets=None):
    """Delegate to KellySizer. One sizer. Fail-closed on error."""
    if fused_score <= 55 or equity <= 0:
        return 0.0
    try:
        from kelly_sizing import KellySizer
        exp_ret = expected_return_pct
        if exp_ret is None:
            exp_ret = abs(float(fused_score) - 50.0) * 0.12
        sizer = KellySizer(total_capital=float(equity))
        rec = sizer.size_position(
            composite_score=float(fused_score),
            expected_return_pct=float(exp_ret),
            regime=regime,
            category=category,
            track_record_mult=track_record_mult,
            risk_factor=risk_factor,
            concurrent_bets=concurrent_bets,
        )
        usd = float(rec.get("position_usd") or 0)
        if rec.get("action") != "EXECUTE":
            return 0.0  # SKIP records can still contain a positive diagnostic size.
        from valedia_config import dynamic_per_trade_cap
        import math
        usd = float(rec.get("position_usd") or 0) * float(pos_multiplier)
        cap = dynamic_per_trade_cap(regime, track_record_mult, risk_factor, category=category)
        usd = min(usd, float(equity) * min(float(max_pct), cap))
        if not all(math.isfinite(x) for x in (usd, float(equity), float(max_pct), float(pos_multiplier), cap)):
            return 0.0
        # Reapply the ceiling AFTER boosters; round down rather than through it.
        return math.floor(max(usd, 0.0) * 100.0) / 100.0
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════
# STAGE 6: FEE GATE — fee_engine.STATIC_FEES only (no second table)
# ══════════════════════════════════════════════════════════════════════

def stage_fee_gate(position_size, expected_return_pct, platform="alpaca"):
    if position_size <= 0:
        return {"pass": False, "reason": "zero position"}
    from fee_engine import roundtrip_cost
    rt = roundtrip_cost(platform, position_size, use_static=True)
    rt_fee = float(rt["total_roundtrip"])
    exp_pnl = position_size * expected_return_pct / 100.0
    if exp_pnl <= rt_fee:
        return {"pass": False, "reason": f"fees ${rt_fee:.4f} >= profit ${exp_pnl:.4f}"}
    return {"pass": True, "net_profit": round(exp_pnl - rt_fee, 4)}


def scalper_place_buy(ticker, notional, alp_buy_fn=None, **_):
    """Slice 8: MicroScalper never places live buys. Report-only. alp_buy_fn unused."""
    return {
        "bought": False,
        "ticker": ticker,
        "notional": notional,
        "reason": "SCALPER_LIVE=False",
        "status": "DISABLED",
    }


# ══════════════════════════════════════════════════════════════════════
# STAGE 7: SELF-IMPROVEMENT
# ══════════════════════════════════════════════════════════════════════

LOG_FILE = os.path.join(SYS["models_dir"], "brain_log.json")

def load_log():
    try:
        with open(LOG_FILE) as f: return json.load(f)
    except Exception:
        return {"trades": [], "stats": {"total": 0, "wins": 0,
                                        "hit_rate": 50, "recent_20": 50}}

def save_log(log):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "w") as f: json.dump(log, f, indent=2, default=str)

def log_trade(ticker, direction, score, price, size, boosters="", platform=""):
    log = load_log()
    log["trades"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker, "dir": direction, "score": score,
        "entry": price, "size": size, "exit": None, "result": None,
        "boosters": boosters, "platform": platform
    })
    log["trades"] = log["trades"][-1000:]
    # Recompute stats
    resolved = [t for t in log["trades"] if t.get("result")]
    # Normalize win check: the resolver (self_audit.py) writes "WIN"/"LOSS";
    # some paths wrote short "W". Accept both so stats aren't silently zeroed
    # (this mismatch previously produced a false 0% here and 100% in self_audit).
    _is_win = lambda r: str(r).upper().startswith("W")
    wins     = sum(1 for t in resolved if _is_win(t["result"]))
    r20      = [t for t in resolved[-20:] if t.get("result")]
    w20      = sum(1 for t in r20 if _is_win(t["result"]))
    log["stats"] = {
        "total":    len(resolved),
        "wins":     wins,
        "hit_rate": round(wins/len(resolved)*100, 1) if resolved else 50.0,
        "recent_20":round(w20/len(r20)*100, 1)       if r20      else 50.0,
    }
    save_log(log)

def get_confidence_multiplier():
    # Reads the REAL fee-inclusive ledger (forward_test_ledger) — the same single source the
    # Kelly cap uses — not the paper-marked brain_log. Neutral 1.0 until a real settled sample
    # exists; the dynamic cap does the conservative sizing while confidence stays neutral.
    try:
        tr = _ftl.real_track_record()
    except Exception:
        return 1.0
    if tr["n"] < KELLY_MIN_PROVEN_TRADES:
        return 1.0
    b = tr["recent_20"] * 0.7 + tr["hit_rate"] * 0.3
    if   b >= 65: return 1.5
    elif b >= 60: return 1.3
    elif b >= 55: return 1.1
    elif b <= 35: return 0.3
    elif b <= 40: return 0.5
    elif b <= 45: return 0.7
    return 1.0


# ══════════════════════════════════════════════════════════════════════
# PLATFORM ADAPTERS — graceful degradation
# ══════════════════════════════════════════════════════════════════════

def get_alpaca():
    """Returns (account_fn, buy_fn, sell_fn, position_fn, IS_LIVE) or None."""
    try:
        import alpaca_executor as ae
        return ae.get_account, ae.market_buy, ae.market_sell, ae.get_position, ae.alpaca_is_live()
    except Exception:
        return None

def get_coinbase():
    """Returns (balances_fn, buy_fn, sell_fn, price_fn) or None."""
    try:
        import coinbase_trader as ct
        return ct.get_balances, ct.market_buy, ct.market_sell, ct.get_price
    except Exception:
        return None


def _spot_source(plat, alpaca_live=False):
    if plat == "coinbase":
        return "live"
    return "live" if alpaca_live else "paper"


def _shadow_coinbase_write(side, ticker, usd_amount=None, crypto_amount=None):
    """Slice 4B: never-dispatchable Intent before the Valedia Coinbase writer. Best-effort."""
    try:
        from nautilus_shadow_coinbase import emit_brain_shadow
        emit_brain_shadow(side, ticker, usd_amount=usd_amount, crypto_amount=crypto_amount)
    except Exception:
        pass


def ledger_spot_buy(plat, ticker, resp, alpaca_live=False, strategy=None):
    """Book a live Coinbase/Alpaca buy (spot_ledger -> _ftl.book_spot_fill): fee-true
    EXECUTED row on a confirmed fill, else UNRECONCILED. Paper writes nothing.
    Returns the ledger status string; never raises, never touches the order."""
    try:
        import spot_ledger as sl
        return sl.book_buy(strategy or sl.BRAIN.get(plat, "BRAIN_SPOT"), plat, ticker, resp,
                           intended_source=_spot_source(plat, alpaca_live))["status"]
    except Exception as e:
        print(f"[unified_brain] ledger_spot_buy failed: {e}", file=sys.stderr)
        return "UNRECONCILED"


def ledger_spot_sell(plat, ticker, resp, alpaca_live=False, strategy=None):
    """Settle a live Coinbase/Alpaca sell: confirmed fill -> fee-true FIFO settlement of the
    open lots; POST-ack only -> UNRECONCILED sell row (spot_reconcile settles it later).
    Returns the ledger status string; never raises, never touches the order."""
    try:
        import spot_ledger as sl
        return sl.book_sell(strategy or sl.BRAIN.get(plat, "BRAIN_SPOT"), plat, ticker, resp,
                            intended_source=_spot_source(plat, alpaca_live))["status"]
    except Exception as e:
        print(f"[unified_brain] ledger_spot_sell failed: {e}", file=sys.stderr)
        return "UNRECONCILED"


def _ledger_note(status):
    return f" ledger={status}" if status else ""


# ══════════════════════════════════════════════════════════════════════
# MASTER ORCHESTRATOR — run_brain()
# ══════════════════════════════════════════════════════════════════════

def run_brain():
    import yfinance as yf
    import logging as _lg
    _lg.getLogger("yfinance").setLevel(_lg.CRITICAL)
    _lg.getLogger("urllib3").setLevel(_lg.CRITICAL)
    import warnings; warnings.filterwarnings("ignore")

    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dow = datetime.now(timezone.utc).weekday()
    dow_note = {
        0: " [Mon — low-confidence, tighter gates]",
        1: " [Tue — low-confidence, tighter gates]",
        2: " [Wed — HIGH confidence, full position]",
        3: " [Thu — high confidence, full position]",
        4: " [Fri — standard, watch end-of-week bias]",
    }.get(dow, "")

    try:
        stamp = version_stamp()
    except Exception:
        stamp = ""
    head = f"[BRAIN] Unified Brain v3.1 — {ts}"
    if stamp:
        head = f"{head} {stamp}"
    lines = [
        head,
        f"Host: {SYS['hostname']} ({'Mac' if SYS['is_mac'] else 'Linux/VPS'})"
        f" | Day: {DOW_NAMES[dow]}{dow_note}",
        ""
    ]

    # ── Stage 1: Intel ───────────────────────────────────────────────
    intel = stage_intel()
    lines.append(f"INTEL: Fear/Greed={intel['fear_greed']} ({intel['fg_label']}) signal={intel['fg_signal']:+d}")

    # ML signals
    ml_signals = {}
    try:
        mdl = os.path.join(SYS["models_dir"], "latest_retrain.json")
        with open(mdl) as f: ml_data = json.load(f)
        for r in ml_data.get("results", []):
            ml_signals[r["ticker"]] = float(r.get("buy_probability", 0.5))
        lines.append(f"ML: {len(ml_signals)} signals loaded")
    except Exception as e:
        lines.append(f"ML: signals unavailable — {str(e)[:50]}")

    # MiroFish
    mirofish_signals = {}
    try:
        import mirofish_intel_archive as _mia
        mfp = os.path.join(SYS["models_dir"], "mirofish_signal.json")
        view = _mia.freshness_of(mfp)
        mf = _mia.load_for_fv(mfp) or {}
        if mf.get("status") == "active":
            mirofish_signals = mf.get("signals", {})
            acc_note = ""
            try:
                import mirofish_accuracy_ledger as _mal
                st = _mal.read_summary(SYS["models_dir"])
                acc_note = (f" acc={st.get('directional_accuracy')} "
                            f"n={st.get('n_scored')} proven={st.get('proven')} "
                            f"w={st.get('weight')}")
            except Exception:
                acc_note = ""
            lines.append(f"MIROFISH: bias={mf.get('overall_bias',50):.0f} "
                         f"({len(mirofish_signals)} assets){acc_note}")
        else:
            lines.append(
                "MIROFISH: not fuel stale=%s age_s=%s usable_for_fv=%s"
                % (view.get("stale"), view.get("age_s"), view.get("usable_for_fv")))
    except Exception as e:
        lines.append(f"MIROFISH: signal unavailable — {str(e)[:50]}")

    # ── Platform accounts ────────────────────────────────────────────
    alpaca  = get_alpaca()
    cb      = get_coinbase()
    equity  = 0.0; cash = 0.0; cb_cash = 0.0; cb_bal = {}

    if alpaca:
        get_account, alp_buy, alp_sell, alp_pos, IS_LIVE = alpaca
        try:
            acct   = get_account()
            equity = float(acct.get("equity", 0))
            cash   = float(acct.get("cash", 0))
            lines.append(f"ALPACA ({'LIVE' if IS_LIVE else 'PAPER'}): ${equity:.2f} equity, ${cash:.2f} cash")
        except Exception as e:
            lines.append(f"ALPACA: offline — {str(e)[:50]}")
    else:
        lines.append("ALPACA: not configured on this system")

    if cb:
        cb_balances, cb_buy, cb_sell, cb_price = cb
        try:
            cb_bal  = cb_balances()
            cb_cash = float(cb_bal.get("USD", {}).get("balance", 0))
            lines.append(f"COINBASE: ${cb_cash:.2f} USD + {len(cb_bal)-1} crypto positions")
        except Exception as e:
            lines.append(f"COINBASE: offline — {str(e)[:50]}")
    else:
        lines.append("COINBASE: not configured on this system")

    total = equity + cb_cash
    if total < 1:
        return "\n".join(lines) + "\n[BRAIN] Insufficient capital — skipping"

    # Capture market conditions for memory
    if MEMORY_AVAILABLE:
        try:
            capture_market_condition(intel, {})
        except Exception:
            pass

    # Track record + confidence multiplier — from the REAL fee-inclusive ledger (single source)
    conf_mult = get_confidence_multiplier()
    _tr = _ftl.real_track_record() if _ftl else {"n": 0}
    if _tr["n"] > 0:
        lines.append(
            f"TRACK RECORD (real, fee-inclusive): {_tr['hit_rate']}% "
            f"({_tr['wins']}W/{_tr['n']-_tr['wins']}L over {_tr['n']}) "
            f"net=${_tr['net']:+.2f} recent20={_tr['recent_20']}% conf_mult={conf_mult}x"
        )

    lines.append("")

    # ── Inject relevant memories ────────────────────────────────────
    if MEMORY_AVAILABLE:
        market_query = f"trading Fear/Greed={intel.get('fear_greed',50)} {intel.get('fg_label','')}"
        lines = list(inject_memory_context(market_query, lines))

    # ── Position budget ──────────────────────────────────────────────
    max_alp = equity  * 0.10; spent_alp = 0.0
    max_cb  = cb_cash * 0.10; spent_cb  = 0.0
    actions = []

    # ── Process universe ─────────────────────────────────────────────
    universe = get_active_universe()

    for name, asset_info in universe.items():
        # Unpack: core assets have 3 fields, discovered assets have 7
        if len(asset_info) == 3:
            ticker, asset_cls, plat = asset_info
            is_discovered  = False
            max_trade_pct  = 0.04
            kelly_mult_disc= 1.0
            sell_thresh    = 40
        else:
            *fields, = asset_info
            ticker, asset_cls, plat = fields[0], fields[1], fields[2]
            is_discovered = True
            max_trade_pct  = fields[4] if len(fields) > 4 else 0.02
            kelly_mult_disc= fields[5] if len(fields) > 5 else 0.5
            sell_thresh    = fields[6] if len(fields) > 6 else 50
            cg_id          = fields[7] if len(fields) > 7 else ""
        try:
            # Fetch price data — CoinGecko for discovered crypto, yfinance for everything else
            prices = None
            if is_discovered and asset_cls == "crypto" and cg_id:
                from dynamic_universe import get_crypto_prices_cg
                import time as _time
                # Try progressively shorter periods (CG free tier rate limits)
                for _days in [365, 90, 30]:
                    prices = get_crypto_prices_cg(cg_id, days=_days)
                    if prices is not None and len(prices) >= 20:
                        break
                    _time.sleep(0.5)  # avoid rate limit
                # Last resort: direct API call for 30d prices
                if prices is None or len(prices) < 20:
                    try:
                        _r = requests.get(
                            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
                            params={"vs_currency": "usd", "days": "30"},
                            timeout=15)
                        if _r.ok:
                            _pts = [float(p[1]) for p in _r.json().get("prices", [])]
                            if len(_pts) >= 20:
                                step = max(1, len(_pts) // 30)
                                prices = np.array(_pts[::step])
                    except Exception:
                        pass
            if prices is None:
                import io, contextlib
                with contextlib.redirect_stderr(io.StringIO()):
                    data = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
                if not data.empty and len(data) >= 80:
                    prices = data["Close"].values
            if prices is None or len(prices) < 20:
                # Don't silently drop discovered assets — log why
                if is_discovered:
                    lines.append(f"  {name:6s} SKIP  no price data (cg_id={cg_id})")
                    # SELF-HEAL: Track failures, auto-remove after 5 consecutive
                    fail_path = os.path.join(SYS["models_dir"], "ticker_failures.json")
                    try:
                        fails = json.load(open(fail_path)) if os.path.exists(fail_path) else {}
                        fails[name] = fails.get(name, 0) + 1
                        if fails[name] >= 5:
                            # Auto-remove from hot_list
                            hot_path = os.path.join(SYS["models_dir"], "hot_list.json")
                            if os.path.exists(hot_path):
                                hl = json.load(open(hot_path))
                                if name in hl.get("assets", {}):
                                    del hl["assets"][name]
                                    json.dump(hl, open(hot_path, "w"), indent=2)
                                    lines.append(f"  {name:6s} AUTO-REMOVED (failed {fails[name]}x)")
                            del fails[name]
                        json.dump(fails, open(fail_path, "w"))
                    except Exception:
                        pass
                continue
            # NaN/Inf guard — clean ALL corrupt values before any analysis
            prices = np.array(prices, dtype=float)
            mask = np.isfinite(prices) & (prices > 0)
            if not np.all(mask):
                lines.append(f"  {name:6s} SKIP invalid price data; no row deletion")
                continue
            cur_price = float(prices[-1])

            # Stage 2: Kernel
            kernel = stage_kernel(prices, asset_cls)

            # Stage 3: ERE
            ere = stage_ere_safety(ticker)

            # Stage 4: Filter v3
            q = compute_signal_quality(prices, kernel, asset_cls)

            # Stage 5: Fusion
            ml_score = float(ml_signals.get(ticker, 0.5)) * 100
            mf_score = float(mirofish_signals.get(ticker, {}).get("score", 50)) if isinstance(mirofish_signals.get(ticker), dict) else 50.0
            fused    = stage_fusion(kernel, ere, intel, ml_score, mf_score, asset_cls)

            # Stage 6: Kelly
            if plat == "coinbase":
                base_equity = cb_cash
                budget_left = max_cb - spent_cb
            else:
                base_equity = equity
                budget_left = max_alp - spent_alp

            # Tighter sizing for discovered assets
            disc_mult = kelly_mult_disc if is_discovered else 1.0
            exp_ret   = abs(fused - 50) * (0.3 if asset_cls == "crypto" else 0.12)
            position = min(
                stage_kelly(fused, base_equity,
                            max_pct=max_trade_pct,
                            pos_multiplier=q["pos_multiplier"] * conf_mult * disc_mult,
                            expected_return_pct=exp_ret, risk_factor=ere["multiplier"]),
                budget_left
            )

            # Stage 7: Fee gate (fee_engine.STATIC_FEES, round-trip incl. Coinbase spread)
            fee_check = stage_fee_gate(position, exp_ret, plat)

            # ── Get current position ─────────────────────────────────
            has_pos = False
            cur_pnl = 0.0
            if alpaca and plat in ("alpaca",):
                try:
                    pos = alp_pos(ticker)
                    has_pos = pos is not None
                    if has_pos:
                        cur_pnl = float(pos.get("unrealized_pl", 0))
                except Exception as e:
                    lines.append(f"  {name:6s} position check failed — {str(e)[:40]}")
            elif cb and plat == "coinbase":
                base = ticker.replace("-USD","")
                has_pos = float(cb_bal.get(base, {}).get("balance", 0)) > 0.0001

            # ── Decision logic ───────────────────────────────────────
            # Must pass filter OR very high fused conviction (≥72)
            actionable = q["passes_filter"] or fused >= 72

            booster_str = ",".join(q["boost_labels"]) or "-"

            # BUY signal
            if (fused > 55 and position >= 0.40 and
                    fee_check["pass"] and ere["safe"] and
                    not has_pos and actionable):

                # Live spot BUY needs the venue writer to be valedia. Exits below are
                # deliberately not writer-gated (a lock must never strand a position).
                if plat in ("coinbase", "alpaca") and not nws.venue_buy_allowed(plat):
                    lines.append(nws.writer_skip_line(name, plat))
                    continue
                _nv = nws.gate(asset_cls, ticker, {"fused": fused, "q": q, "fee_check": fee_check,
                               "has_pos": has_pos, "ere": ere}, source="unified_brain", venue=plat, label="buy") \
                    if (plat == "coinbase" and cb and cb_cash >= position) or (plat == "alpaca" and alpaca) else None
                lines.extend(nws.note(name, _nv))
                if nws.blocked(_nv):
                    continue
                executed = False
                buy_resp = None
                if plat == "coinbase" and cb and cb_cash >= position:
                    try:
                        _shadow_coinbase_write("buy", ticker, usd_amount=position)
                        buy_resp = cb_buy(ticker, position)
                        if _ftl and not _ftl.spot_ack_ok("coinbase", buy_resp):
                            lines.append(f"  {name:6s} BUY FAILED: {buy_resp}")
                        else:
                            spent_cb += position; executed = True
                    except Exception as e:
                        lines.append(f"  {name:6s} BUY FAILED: {e}")

                elif plat == "alpaca" and alpaca:
                    try:
                        buy_resp = alp_buy(ticker, notional=position)
                        if _ftl and not _ftl.spot_ack_ok("alpaca", buy_resp):
                            lines.append(f"  {name:6s} BUY FAILED: {buy_resp}")
                        else:
                            spent_alp += position; executed = True
                    except Exception as e:
                        lines.append(f"  {name:6s} BUY FAILED: {e}")

                if executed:
                    log_trade(ticker, "BUY", fused, cur_price, position,
                              booster_str, plat)
                    # Fill details → fee-true live row; POST-only → UNRECONCILED (never silent).
                    alp_live = bool(alpaca[4]) if alpaca else False
                    led = ledger_spot_buy(plat, ticker, buy_resp, alpaca_live=alp_live)
                    if MEMORY_AVAILABLE:
                        try:
                            capture_trade(ticker, "BUY", fused, cur_price, position)
                        except Exception:
                            pass
                    actions.append(f"BUY ${position:.2f} {ticker}")
                    lines.append(
                        f"  >> BUY ACK ${position:.2f} {name:6s} "
                        f"fused={fused:.0f} boosters=[{booster_str}] "
                        f"dom={q['dom']} votes={q['votes']} (POST-ack, not fill-confirmed)"
                        f"{_ledger_note(led)}"
                    )

            # SELL signal (exit existing position)
            elif fused < sell_thresh and has_pos:
                executed = False
                sell_resp = None
                bal = 0.0
                if plat == "coinbase" and cb:
                    try:
                        base = ticker.replace("-USD","")
                        bal  = float(cb_bal.get(base, {}).get("balance", 0))
                        if bal > 0.0001:
                            _shadow_coinbase_write("sell", ticker, crypto_amount=bal)
                            sell_resp = cb_sell(ticker, bal)
                            if _ftl and not _ftl.spot_ack_ok("coinbase", sell_resp):
                                lines.append(f"  {name:6s} SELL FAILED: {sell_resp}")
                            else:
                                executed = True
                    except Exception as e:
                        lines.append(f"  {name:6s} SELL FAILED: {e}")
                elif plat == "alpaca" and alpaca:
                    try:
                        sell_resp = alp_sell(ticker)
                        if _ftl and not _ftl.spot_ack_ok("alpaca", sell_resp):
                            lines.append(f"  {name:6s} SELL FAILED: {sell_resp}")
                        else:
                            executed = True
                    except Exception as e:
                        lines.append(f"  {name:6s} SELL FAILED: {e}")

                if executed:
                    log_trade(ticker, "SELL", fused, cur_price, 0,
                              booster_str, plat)
                    alp_live = bool(alpaca[4]) if alpaca else False
                    # Fee: broker body, else fee_engine static (fee_status=modeled) in spot_ledger.
                    led = ledger_spot_sell(plat, ticker, sell_resp, alpaca_live=alp_live)
                    actions.append(f"SELL {ticker}")
                    lines.append(
                        f"  >> SELL ACK {name:6s} fused={fused:.0f} "
                        f"boosters=[{booster_str}] (POST-ack, not fill-confirmed)"
                        f"{_ledger_note(led)}"
                    )

            # HOLD existing position
            elif has_pos:
                pnl_str = f" P&L=${cur_pnl:+.2f}" if cur_pnl else ""
                lines.append(
                    f"  {name:6s} HOLD  fused={fused:.0f} "
                    f"filter={'✓' if q['passes_filter'] else '-'}{pnl_str}"
                )

            # WATCH (no position, signal below threshold)
            else:
                why = (f"dom={q['dom']}"
                       + (f" v={q['votes']}" if asset_cls=="equity" else "")
                       + (f" t5={'✓' if q['t5'] else '✗'}")
                       + (" SKIP_MON_TUE" if q["is_mon_tue"] else ""))
                lines.append(
                    f"  {name:6s} --    fused={fused:.0f} "
                    f"ere={ere['status']} [{why}]"
                )

        except Exception as e:
            lines.append(f"  {name:6s} ERR: {traceback.format_exc().splitlines()[-1][:60]}")

    # ── DCA ──────────────────────────────────────────────────────────
    try:
        from strategy_modules import DCAEngine
        dca = DCAEngine(
            weekly_budget=10,
            assets={"BTC-USD":0.4,"ETH-USD":0.25,"SOL-USD":0.2,"XRP-USD":0.15}
        )
        if cb and dca.should_buy() and cb_cash >= 5:
            _, cb_buy_fn, _, _ = cb
            # Facts DCA actually knows: fee only (static round trip). No kernel score or price
            # history here, and already_held is never sent for DCA (re-buying held names is the point).
            _rows = {pair: nws.dca_row("coinbase", dca.weekly_budget * w) for pair, w in dca.assets.items()}
            # Each acked DCA buy is booked as DCA_CRYPTO (bookkeeping only, never blocks).
            _dca_book = lambda pair, resp: ledger_spot_buy("coinbase", pair, resp,
                                                           strategy="DCA_CRYPTO")
            for da in dca.execute(nws.wrap_buy(cb_buy_fn, source="unified_brain_dca", rows=_rows),
                                  lambda: cb_cash, venue="coinbase", book=_dca_book):
                lines.append(f"  {da}"); actions += [da] if da.startswith("DCA: bought") else []
        elif dca.should_buy():
            lines.append("  DCA: ready — insufficient USD or Coinbase offline")
    except Exception as e:
        lines.append(f"  DCA: {str(e)[:50]}")

    # ── Scalper (report-only; never alp_buy / never book live) ────────
    try:
        from strategy_modules import MicroScalper
        scalper = MicroScalper()
        wp = {}
        for t in ["SPY","QQQ","AAPL","NVDA","AMD","TSLA"]:
            try: wp[t] = yf.Ticker(t).history(period="1mo")["Close"].values
            except: pass
        for s in scalper.get_scalp_candidates(wp):
            # Slice 8: live scalper buys are dead. alp_buy is not called.
            rec = scalper_place_buy(s.get("ticker"), 0)
            lines.append(
                f"  SCALP {s.get('signal')} {s.get('ticker')} z={s.get('z')} "
                f"({rec['reason']})"
            )
    except Exception as e:
        lines.append(f"  Scalp: {str(e)[:50]}")

    # ── Summary ──────────────────────────────────────────────────────
    lines.append("")
    if actions:
        lines.append(
            f"ACKED: {len(actions)} orders (POST-ack, not fill-confirmed) "
            f"(Alpaca ${spent_alp:.2f}, Coinbase ${spent_cb:.2f})"
        )
        for a in actions: lines.append(f"  {a}")
    else:
        lines.append("NO NEW TRADES this cycle (existing positions held)")

    # Save report
    report = "\n".join(lines)
    try:
        rpt_path = os.path.join(SYS["models_dir"], "latest_brain_report.json")
        os.makedirs(os.path.dirname(rpt_path), exist_ok=True)
        with open(rpt_path, "w") as f:
            json.dump({"ts": ts, "actions": actions, "intel": intel,
                       "equity": equity, "conf_mult": conf_mult,
                       "version": "v3.1", "host": SYS["hostname"]},
                      f, indent=2, default=str)
    except Exception: pass

    return report


if __name__ == "__main__":
    # Auto-source .env if keys not already in environment
    if not os.environ.get("ALPACA_API_KEY"):
        import subprocess
        r = subprocess.run(
            f"set -a && source {SYS['env_file']} && env",
            shell=True, capture_output=True, text=True
        )
        for line in r.stdout.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)
    print(run_brain())
