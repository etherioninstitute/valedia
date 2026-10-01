#!/usr/bin/env python3
"""Paper/shadow mover scan. Fee-true rank vs a ~$500 book.

Watch crypto + stocks/ETFs + weather. Size only names that printed like
PUMP/QNT (~16% day) after a roundtrip of crypto taker+slip. Never orders.

Reuses the dynamic_universe CoinGecko gainer query, market_data Yahoo
summary, Finnhub headlines, crypto_paper fee/clip, and jev_shadow.annotate
(fail-open). Ornith 1.5 9B seasoning stays off unless VALEDIA_ORNITH_SEASON=1
and never reorders. CoinGecko 429 falls through to the public Binance 24h tape.
Fee-true code still labels and sizes. Nimble System One is off unless
VALEDIA_SYSTEMONE=1, then a loopback /v1/systemone decide is attached and a
down server falls open to the same label. No ALLOWLIST change.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crypto_paper as cp
import jev_shadow
import ornith_seasoning
import systemone_hotpath_client as s1
import valedia_config as vc

BANKROLL = 504.0  # TC-AUTO freely-tradeable cash. ~$500 paper.
PRINT_BAR_PCT = 16.0  # PUMP/QNT-class day. Below this: watch or pass, no size.
PROBE = ("PUMP", "QNT")
ETFS = frozenset({"SPY", "QQQ", "DIA", "IWM", "GLD", "TLT"})
OIL_SYMS = frozenset({"USO", "UCO", "BNO", "SCO", "DBO", "CL", "WTI"})
# Same series heads as weather_shadow.PREFIXES. Watch only.
WEATHER_PREFIXES = (
    "KXHURRICANE", "KXWEATH", "KXHIGH", "KXLOW", "KXRAIN",
    "KXSNOW", "KXTEMP", "KXWIND",
)
DEFAULT_OUT = os.path.join(ROOT, "data", "all-knowing", "movers_scan.json")
PAPER_WATCH = os.path.join(ROOT, "data", "all-knowing", "paper_watch.json")
DEFAULT_FIXTURE = os.path.join(ROOT, "data", "all-knowing", "fixture_movers.json")
UA = "valedia-all-knowing-paper/0.1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sym(raw):
    return str(raw or "").upper().replace("-USD", "").strip()


def oil_fenced(symbol, cls=""):
    head = _sym(symbol).split("-", 1)[0]
    if str(cls) == "oil":
        return True
    if head in OIL_SYMS or head.startswith("KXWTI") or head.startswith("KXOIL"):
        return True
    return False


def weather_name(symbol, cls=""):
    if str(cls) == "weather":
        return True
    head = _sym(symbol).split("-", 1)[0]
    return any(head.startswith(p) for p in WEATHER_PREFIXES)


def fee_true_edge(chg_pct, taker, slip):
    """move fraction minus (fee+slip) roundtrip. None if there is no print."""
    if chg_pct is None:
        return None
    rt = 2.0 * (float(taker) + float(slip))
    return (float(chg_pct) / 100.0) - rt


def fee_true_label(row):
    """Fee-true paper label. watch / would_buy / would_pass. Never create_order.

    Not OMS. Not Kelly. Stored as row["label"]. Nimble System One does not
    change it.
    """
    if weather_name(row.get("symbol"), row.get("class")) or row.get("oil_fenced"):
        return "watch"
    edge, chg = row.get("edge"), row.get("chg_24h")
    if edge is None or chg is None:
        return "watch"
    if float(edge) <= 0:
        return "would_pass"
    if float(chg) >= PRINT_BAR_PCT:
        return "would_buy"
    return "watch"


def _would_have(price, notional, maker, taker, slip, impact):
    rt = 2.0 * (float(taker) + float(slip))
    out = {
        "sized": True,
        "notional_usd": round(float(notional), 4),
        "qty": None,
        "entry_px": None,
        "entry_fee_usd": None,
        "roundtrip_cost_usd": round(float(notional) * rt, 6),
        "impact_usd": None if impact is None else round(float(impact), 6),
        "create_order": False,
        "live": False,
        "venue_allowlist": False,
        "counts_toward_kelly": False,
    }
    if price and float(price) > 0 and notional > 0:
        qty = float(notional) / float(price)
        px = cp.fill_px(price, "buy", slip)
        fee = cp.fee_usd(px * qty, "taker", maker, taker)
        out["qty"] = round(qty, 8)
        out["entry_px"] = round(px, 8)
        out["entry_fee_usd"] = round(fee, 8)
    return out


def _blank_size():
    return {
        "sized": False, "qty": None, "create_order": False, "live": False,
        "venue_allowlist": False, "counts_toward_kelly": False,
    }


def rank_movers(rows, *, bankroll=BANKROLL, maker=None, taker=None, slip=None,
                systemone_post=None, informed=None):
    """Rank by would-have $ on the clipped notional. Low edge sorts down."""
    maker, taker, slip = cp.paper_fees(maker, taker, slip)
    notional = float(bankroll) * float(cp.CLIP_FRAC)
    if s1.enabled() and informed is None:
        informed = s1.load_informed()
    ranked = []
    for raw in rows or []:
        if not isinstance(raw, dict) or not raw.get("symbol"):
            continue
        sym = _sym(raw.get("symbol"))
        cls = str(raw.get("class") or "unknown")
        if sym in ETFS:
            cls = "etf"
        chg = raw.get("chg_24h")
        chg = None if chg is None else float(chg)
        if chg is not None and not math.isfinite(chg):
            chg = None
        edge = fee_true_edge(chg, taker, slip)
        impact = None if edge is None else edge * notional
        row = {
            "symbol": sym,
            "class": "oil" if oil_fenced(sym, cls) else cls,
            "chg_24h": None if chg is None else round(chg, 4),
            "buzz": round(float(raw.get("buzz") or 0), 1),
            "price": raw.get("price"),
            "quote_volume_usd": raw.get("quote_volume_usd"),
            "reasons": list(raw.get("reasons") or [])[:5],
            "edge": None if edge is None else round(edge, 6),
            "impact_usd": None if impact is None else round(impact, 6),
            "notional_cap_usd": round(notional, 4),
            "fee_roundtrip": round(2.0 * (float(taker) + float(slip)), 6),
            "oil_fenced": oil_fenced(sym, cls),
            "harvest": "NO_HARVEST" if oil_fenced(sym, cls) else None,
        }
        row["label"] = fee_true_label(row)
        if s1.enabled():
            row["systemone_shadow"] = s1.decide(
                s1.compact_state(row, informed), post=systemone_post)
        if row["label"] == "would_buy":
            row["would_have"] = _would_have(
                row["price"], notional, maker, taker, slip, impact)
        else:
            row["would_have"] = _blank_size()
        ranked.append(row)
    ranked.sort(
        key=lambda r: (
            r["impact_usd"] is not None,
            r["impact_usd"] if r["impact_usd"] is not None else -1e18,
            r["chg_24h"] if r["chg_24h"] is not None else -1e18,
        ),
        reverse=True,
    )
    return ranked


def normalize_equity(summary, have):
    rows = []
    for s in summary or []:
        sym = _sym(s.get("symbol"))
        if not sym or sym in have:
            continue
        cls = "etf" if sym in ETFS else "equity"
        if oil_fenced(sym):
            cls = "oil"
        rows.append({
            "symbol": sym,
            "class": cls,
            "chg_24h": s.get("change_pct"),
            "buzz": 0,
            "price": s.get("price"),
            "reasons": ["yahoo"],
        })
        have.add(sym)
    return rows


def _get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# Same gainer query as dynamic_universe.scan_coingecko. One call, not three.
CG_MARKETS = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=price_change_percentage_24h_desc"
    "&per_page=30&page=1&sparkline=false"
)
# Public 24h tapes. Used when CoinGecko answers 429. Not order routes.
BINANCE_24H_URLS = (
    "https://api.binance.com/api/v3/ticker/24hr",
    "https://api.binance.us/api/v3/ticker/24hr",
)
BINANCE_MIN_QUOTE_USD = 250_000.0
_STABLE = frozenset({"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USD", "USDP", "EUR"})


def rows_from_cg_markets(payload):
    if isinstance(payload, dict):
        code = ((payload.get("status") or {}).get("error_code"))
        raise RuntimeError("coingecko_%s" % (code or "bad_payload"))
    rows = []
    for c in payload or []:
        if not isinstance(c, dict):
            continue
        sym = _sym(c.get("symbol"))
        chg = c.get("price_change_percentage_24h")
        if not sym or chg is None:
            continue
        px = c.get("current_price")
        rows.append({
            "symbol": sym,
            "class": "crypto",
            "chg_24h": float(chg),
            "buzz": 0.0,
            "price": float(px) if px else None,
            "reasons": ["coingecko_markets"],
        })
    return rows


def rows_from_binance(payload, *, min_quote=BINANCE_MIN_QUOTE_USD):
    """Top USDT gainers by 24h %, plus PUMP/QNT even when they are not in the top cut."""
    if not isinstance(payload, list):
        raise RuntimeError("binance_bad_payload")
    parsed = []
    for t in payload:
        if not isinstance(t, dict):
            continue
        raw = str(t.get("symbol") or "")
        if not raw.endswith("USDT"):
            continue
        base = raw[:-4]
        if base in _STABLE:
            continue
        try:
            chg = float(t.get("priceChangePercent"))
            px = float(t.get("lastPrice") or 0)
            qv = float(t.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            continue
        parsed.append((chg, qv, base, px))
    liquid = [row for row in parsed if row[1] >= float(min_quote)]
    liquid.sort(key=lambda row: row[0], reverse=True)
    chosen = liquid[:30]
    have = {base for _, _, base, _ in chosen}
    by_base = {item[2]: item for item in parsed}
    for sym in PROBE:
        if sym not in have and sym in by_base:
            chosen.append(by_base[sym])
            have.add(sym)
    out = []
    for chg, qv, base, px in chosen:
        out.append({
            "symbol": base,
            "class": "crypto",
            "chg_24h": chg,
            "buzz": 0.0,
            "price": px or None,
            "reasons": ["binance_24h"],
            "quote_volume_usd": round(qv, 2),
        })
    return out


def load_tape(get_json=None):
    """CoinGecko gainer page, else Binance public 24h. No keys. No orders."""
    get = get_json or _get_json
    notes = {}
    rows = []
    try:
        rows = rows_from_cg_markets(get(CG_MARKETS))
        notes["coingecko_markets"] = "ok" if rows else "empty"
    except Exception as e:
        notes["coingecko_markets"] = "%s:%s" % (type(e).__name__, e)
        notes["coingecko_markets"] = notes["coingecko_markets"][:160]
        rows = []
    if rows:
        notes["tape"] = "coingecko"
    else:
        binance_err = None
        for url in BINANCE_24H_URLS:
            try:
                rows = rows_from_binance(get(url))
            except Exception as e:
                binance_err = "%s:%s" % (type(e).__name__, e)
                rows = []
                continue
            if rows:
                notes["binance_24h"] = url
                break
        if not rows and binance_err:
            notes["binance_24h"] = binance_err[:160]
        notes["tape"] = "binance_public_24h" if rows else "none"
    if not rows:
        raise RuntimeError("no_priced_movers")
    have = {r["symbol"] for r in rows}
    for sym in PROBE:
        if sym in have:
            src = "in_coingecko_scan" if notes["tape"] == "coingecko" else "in_binance_24h"
            notes[sym] = src
        else:
            notes[sym] = "absent"
    return rows, notes


def fetch_live(get_json=None):
    """Gainer tape, Yahoo index tape, Finnhub buzz. Paper only."""
    rows, notes = load_tape(get_json)
    have = {r["symbol"] for r in rows}
    try:
        import market_data
        summary = market_data.get_market_summary()
    except Exception as e:
        summary = []
        notes["equity_error"] = type(e).__name__
    rows.extend(normalize_equity(summary, have))
    try:
        import dynamic_universe as du
        fn = du.scan_finnhub_news()
    except Exception:
        fn = {}
    buzz = sorted((fn or {}).items(), key=lambda kv: (kv[1] or {}).get("score", 0), reverse=True)
    for sym, data in buzz[:12]:
        sym = _sym(sym)
        if not sym or sym in have:
            continue
        rows.append({
            "symbol": sym,
            "class": "oil" if oil_fenced(sym) else "equity",
            "chg_24h": None,
            "buzz": float((data or {}).get("score") or 0),
            "price": None,
            "reasons": list((data or {}).get("reasons") or ["finnhub"])[:3],
        })
        have.add(sym)
    return rows, notes


def load_fixture(path):
    with open(path) as f:
        doc = json.load(f)
    if isinstance(doc, list):
        return doc
    return list(doc.get("rows") or [])


def _weather_row():
    return {
        "symbol": "KXHIGHNY",
        "class": "weather",
        "chg_24h": None,
        "buzz": 0,
        "price": None,
        "reasons": ["weather_watch"],
    }


def season_jev(ranked, annotate=None):
    """Stamp Jev on a copy. Ranking is already fixed. Fail-open."""
    doc = {
        "source": "all_knowing_movers",
        "overall_bias": "WATCH",
        "signals": {},
        "events": [],
    }
    for r in ranked[:8]:
        doc["signals"][r["symbol"]] = {
            "direction": "WATCH",
            "chg_24h": r.get("chg_24h"),
        }
    fn = annotate or jev_shadow.annotate
    try:
        stamped = fn(doc)
    except Exception as e:
        stamped = dict(doc)
        stamped["jev"] = {"enabled": True, "fail_open": True, "error": type(e).__name__}
    if not isinstance(stamped, dict):
        stamped = dict(doc)
    if "jev" not in stamped:
        stamped = dict(stamped)
        stamped["jev"] = {
            "enabled": False,
            "fail_open": True,
            "reason": "VALEDIA_JEV_off",
            "seasoning_only": True,
        }
    jev = dict(stamped["jev"])
    jev["seasoning_only"] = True
    jev["changes_rank"] = False
    return jev


def presence(ranked, notes, sym):
    for r in ranked:
        if r["symbol"] == sym:
            return {
                "present": True,
                "chg_24h": r["chg_24h"],
                "edge": r["edge"],
                "impact_usd": r["impact_usd"],
                "label": r["label"],
                "sized": bool((r.get("would_have") or {}).get("sized")),
                "note": (notes or {}).get(sym),
            }
    return {"present": False, "reason": (notes or {}).get(sym) or "not_in_scan"}


def fences():
    return {
        "create_order": False,
        "live": False,
        "nautilus_live": False,
        "mirofish_auto_raise": bool(vc.MIROFISH_AUTO_RAISE),
        "crypto_paper_safety": vc.CRYPTO_PAPER_SAFETY,
        "crypto_paper_allowlist": list(cp.ALLOWLIST),
        "allowlist_widened": False,
        "oil": "NO_HARVEST",
        "weather": "WATCH",
        "systemone": "stub",
        "jev": "seasoning_only",
        "ornith": "seasoning_only",
        "counts_toward_kelly": False,
    }


def build(*, rows=None, fixture_path=None, fetch=None, annotate=None, bankroll=BANKROLL,
          systemone_post=None, informed=None, season=None):
    notes, err = {}, None
    if rows is not None:
        origin = "injected"
        use = list(rows)
    elif fixture_path:
        origin = "fixture"
        use = load_fixture(fixture_path)
    else:
        try:
            use, notes = (fetch or fetch_live)()
            origin = "live"
        except Exception as e:
            use = load_fixture(DEFAULT_FIXTURE)
            origin = "fixture_fallback"
            err = "%s:%s" % (type(e).__name__, e)
            notes = {"fallback": err}
    if not any(weather_name(r.get("symbol"), r.get("class")) for r in use):
        use.append(_weather_row())
    order_before = [r.get("symbol") for r in use]
    if s1.enabled() and informed is None:
        informed = s1.load_informed()
    ranked = rank_movers(
        use, bankroll=bankroll, systemone_post=systemone_post, informed=informed)
    ranked_order = [r["symbol"] for r in ranked]
    jev = season_jev(ranked, annotate=annotate)
    ornith = ornith_seasoning.stamp(ranked, season=season)
    assert [r["symbol"] for r in ranked] == ranked_order
    _ = order_before
    shadow = s1.summary(ranked, informed)
    held = fences()
    held["systemone"] = shadow["backend"] if shadow["enabled"] else "stub"
    doc = {
        "ts": _now(),
        "status": "PAPER/SHADOW",
        "origin": origin,
        "error": err,
        "bankroll_usd": float(bankroll),
        "clip_frac": cp.CLIP_FRAC,
        "print_bar_pct": PRINT_BAR_PCT,
        "fee_table": cp.FEE_TABLE,
        "fences": held,
        "systemone": shadow,
        "jev": jev,
        "ornith": ornith,
        "probe": {s: presence(ranked, notes, s) for s in PROBE},
        "notes": notes,
        "n": len(ranked),
        "ranked": ranked,
    }
    return doc


def paper_watch_doc(doc):
    names = []
    for r in doc["ranked"]:
        if r["label"] == "would_pass":
            continue
        names.append({
            "symbol": r["symbol"],
            "class": r["class"],
            "label": r["label"],
            "chg_24h": r["chg_24h"],
            "impact_usd": r["impact_usd"],
            "sized": bool((r.get("would_have") or {}).get("sized")),
        })
    return {
        "ts": doc["ts"],
        "paper_only": True,
        "create_order": False,
        "live": False,
        "allowlist_widened": False,
        "crypto_paper_allowlist": list(cp.ALLOWLIST),
        "names": names,
    }


def write_doc(doc, out_path=None, watch_path=None):
    out_path = out_path or DEFAULT_OUT
    watch_path = watch_path or PAPER_WATCH
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(watch_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=2, default=str)
    with open(watch_path, "w") as f:
        json.dump(paper_watch_doc(doc), f, indent=2)
    return out_path, watch_path


def format_table(doc, n=12):
    lines = [
        "origin=%s bankroll=%.2f clip=%.0f%% bar=%.0f%% safety=%s"
        % (doc["origin"], doc["bankroll_usd"], doc["clip_frac"] * 100,
           doc["print_bar_pct"], doc["fences"]["crypto_paper_safety"]),
        f"{'symbol':10} {'class':8} {'chg%':>8} {'edge':>9} {'impact$':>10} {'label':12} sized",
    ]
    for r in doc["ranked"][:n]:
        chg = "—" if r["chg_24h"] is None else "%+8.2f" % r["chg_24h"]
        edge = "—" if r["edge"] is None else "%9.4f" % r["edge"]
        impact = "—" if r["impact_usd"] is None else "%10.4f" % r["impact_usd"]
        sized = "Y" if (r.get("would_have") or {}).get("sized") else "n"
        lines.append(
            f"{r['symbol']:10} {r['class']:8} {chg:>8} {edge:>9} {impact:>10} {r['label']:12} {sized}")
    for sym, info in (doc.get("probe") or {}).items():
        if info.get("present"):
            lines.append("probe %s present chg=%s label=%s sized=%s" % (
                sym, info.get("chg_24h"), info.get("label"), info.get("sized")))
        else:
            lines.append("probe %s ABSENT reason=%s" % (sym, info.get("reason")))
    shadow = doc.get("systemone") or {}
    lines.append(
        "systemone.backend=%s model=%s fail_open=%s enforcement=%s create_order=false"
        % (shadow.get("backend"), shadow.get("model"), shadow.get("fail_open"),
           shadow.get("enforcement")))
    orn = doc.get("ornith") or {}
    lines.append(
        "ornith enabled=%s fail_open=%s reason=%s changes_rank=false"
        % (orn.get("enabled"), orn.get("fail_open"), orn.get("reason")))
    if orn.get("reason") == "not_loaded" and orn.get("lms_load_cmd"):
        lines.append("ornith lms_load_cmd print-only: %s" % orn["lms_load_cmd"])
    if shadow.get("enabled"):
        lines.append(_proof_lines(doc))
    return "\n".join(lines)


def _proof_lines(doc):
    """One crypto, one equity/ETF, one weather/prediction row."""
    picked = {"crypto": None, "equity": None, "prediction": None}
    for r in doc.get("ranked") or []:
        cls = r.get("class")
        if cls == "crypto" and picked["crypto"] is None:
            picked["crypto"] = r
        elif cls in ("equity", "etf") and picked["equity"] is None:
            picked["equity"] = r
        elif cls in ("weather", "prediction") and picked["prediction"] is None:
            picked["prediction"] = r
    lines = []
    for key, r in picked.items():
        if not r:
            lines.append("proof %s MISSING" % key)
            continue
        sh = r.get("systemone_shadow") or {}
        ans = sh.get("answers") or {}
        lines.append(
            "proof %s %s class=%s label=%s sized=%s admit=%s edge_q=%s venue=%s latency_ms=%s create_order=false"
            % (key, r.get("symbol"), r.get("class"), r.get("label"),
               "Y" if (r.get("would_have") or {}).get("sized") else "n",
               ans.get("admit_paper"), ans.get("edge_quality"), ans.get("venue_class"),
               sh.get("latency_ms")))
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Paper/shadow all-knowing mover scan")
    p.add_argument("--fixture", default="", help="offline rows JSON (no network)")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--watch", default=PAPER_WATCH)
    p.add_argument("--bankroll", type=float, default=BANKROLL)
    args = p.parse_args(argv)
    health = None
    if s1.enabled():
        h = s1.health()
        health = h
        print(
            "systemone.health ok=%s version=%s model_present=%s model=%s mode=%s enforcement=%s"
            % (h.get("ok"), h.get("version"), h.get("model_present"),
               h.get("model"), h.get("mode"), h.get("enforcement")))
    doc = build(
        fixture_path=args.fixture or None,
        bankroll=args.bankroll,
    )
    if health is not None:
        doc["systemone"]["health"] = health
    out, watch = write_doc(doc, args.out, args.watch)
    print(format_table(doc))
    print("wrote %s" % out)
    print("watch %s" % watch)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("ALL_KNOWING_FAIL %s" % e, file=sys.stderr)
        sys.exit(1)
