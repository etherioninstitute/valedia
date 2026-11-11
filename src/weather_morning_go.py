"""Valedia weather morning go — shadow scan + gated live admit.

Owner: Valedia runtime (Mac crontab / cron_runner). Grok Bot routine paused.

Flow:
  1) weather_live_preflight — Valedia decides WEATHER_LIVE from this
  2) PASS + flip_allowed → set WEATHER_LIVE=1 in ~/.hermes/secrets/trading.env;
     FAIL → set WEATHER_LIVE=0 (Valedia owns the switch; not Grok Bot / chat)
  3) Read-only Kalshi open KXHIGH*/KXLOW* books (full fee-map series list)
  4) morning_scan with live FV lookup (refuse create on no_fv)
  5) If live enabled + preflight PASS, weather_live.admit may send on
     NWS-settled official alerts only (TWC API path stays shadow without key;
     oil always fenced; Nimble 2 annotate-only)
  6) Always write WEATHER-MORNING-DIAGNOSIS.md explaining admits=0 or blockers

Best-of-free settle: NWS CLI / climate (CLIxxx in rules). Research tags never alone-pass create.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from kalshi_quote import yes_price
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
ENV_PATH = os.path.expanduser("~/.hermes/secrets/trading.env")
REC = os.path.join(ROOT, "recommendations")

_NWS_FV = frozenset({
    "nws", "nws_cli", "noaa", "climate", "climate_data", "nws_climate",
})
_TWC_FV = frozenset({"weather_com", "twc", "weather_company"})

# Fallback if fee-map missing
_DEFAULT_SERIES = [
    "KXHIGHMIA", "KXHIGHNY", "KXHIGHLAX", "KXHIGHCHI", "KXHIGHDEN",
    "KXHIGHTLV", "KXLOWTLV", "KXHIGHTATL", "KXHIGHHOU", "KXHIGHPHX",
    "KXHIGHAUS", "KXHIGHPHIL", "KXHIGHTBOS", "KXHIGHTDAL", "KXHIGHTDC",
    "KXHIGHTNOLA", "KXHIGHTPHX", "KXHIGHTSEA",
    "KXLOWTMIA", "KXLOWTLAX", "KXLOWTCHI", "KXLOWTDEN", "KXLOWTATL",
]


def _log(msg):
    print(msg, flush=True)


def _set_weather_live(on: bool) -> str:
    """Persist WEATHER_LIVE in ~/.hermes/secrets/trading.env and this process env. Valedia-owned."""
    want = "1" if on else "0"
    path = ENV_PATH
    try:
        text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    except OSError as e:
        _log(f"WEATHER_LIVE set failed read err={type(e).__name__}:{e}")
        os.environ["WEATHER_LIVE"] = want
        return want
    if re.search(r"(?m)^WEATHER_LIVE=", text):
        text = re.sub(r"(?m)^WEATHER_LIVE=.*$", f"WEATHER_LIVE={want}", text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"WEATHER_LIVE={want}\n"
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        _log(f"WEATHER_LIVE set failed write err={type(e).__name__}:{e}")
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
    os.environ["WEATHER_LIVE"] = want
    return want


def _valedia_decide_live(pre: dict) -> bool:
    allow = bool(pre.get("status") == "PASS" and pre.get("weather_live_flip_allowed"))
    before = os.environ.get("WEATHER_LIVE", "")
    after = _set_weather_live(allow)
    _log(
        f"valedia_WEATHER_LIVE_decision allow={allow} before={before!r} after={after!r} "
        f"preflight={pre.get('status')} flip_allowed={pre.get('weather_live_flip_allowed')}"
    )
    return allow


def _nws_settled(row: dict) -> bool:
    src = str(row.get("fv_source") or "").strip().lower()
    if src in _TWC_FV:
        return False
    return src in _NWS_FV


def _settled_confidence(row: dict) -> bool:
    sf = row.get("settlement_fv") if isinstance(row.get("settlement_fv"), dict) else {}
    conf = str(sf.get("confidence") or row.get("settlement_confidence") or "").strip().lower()
    return conf in ("final", "observation")


def admit_open(dry: bool, live_on: bool, pre_status) -> bool:
    """Shadow, --dry-decide, and a failed preflight never admit."""
    return (not dry) and bool(live_on) and pre_status == "PASS"


def pick_admit(alerts):
    """One NWS-settled official alert. Best fee_true_ev. None if the pool is empty."""
    pool = []
    skipped_non_nws = 0
    for r in alerts or []:
        if not isinstance(r, dict) or (r.get("tag") or "") == "no_fv":
            continue
        if not _nws_settled(r):
            skipped_non_nws += 1
            continue
        if not _settled_confidence(r):
            continue
        pool.append(r)
    if not pool:
        return None, skipped_non_nws
    best = max(pool, key=lambda r: float(r.get("fee_true_ev") or 0))
    return best, skipped_non_nws


def _series_list():
    path = os.path.join(REC, "kalshi-fee-map.json")
    out = []
    try:
        body = json.load(open(path, encoding="utf-8"))
        series = body.get("series") if isinstance(body, dict) else None
        if isinstance(series, dict):
            out = [
                k for k in series.keys()
                if str(k).startswith(("KXHIGH", "KXLOW"))
            ]
    except (OSError, ValueError) as e:
        _log(f"fee_map_series_fail err={type(e).__name__}:{e}")
    if not out:
        out = list(_DEFAULT_SERIES)
    return sorted(set(out))


def _open_weather_markets(limit_per=20):
    import kalshi_auth as ka

    out = []
    for ser in _series_list():
        try:
            body = ka.get(f"/markets?limit={limit_per}&status=open&series_ticker={ser}")
        except Exception as e:
            _log(f"markets_get_fail series={ser} err={type(e).__name__}:{e}")
            continue
        markets = (body or {}).get("markets") or []
        for m in markets:
            if not isinstance(m, dict):
                continue
            ticker = m.get("ticker") or ""
            rules = m.get("rules_primary") or m.get("rules") or ""
            title = m.get("title") or ""
            # mid when bid+ask quote; else last; else one side. dollars-first (kalshi_quote).
            price = yes_price(m)
            # observe_date from ticker token 26SEP24 → 2026-09-24 when possible
            observe = date.today().isoformat()
            parts = str(ticker).split("-")
            if len(parts) > 1 and len(parts[1]) >= 7:
                tok = parts[1]
                try:
                    # YYMONDD e.g. 26SEP24
                    mon = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                           "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
                    yy, mon_s, dd = int(tok[:2]), tok[2:5].upper(), int(tok[5:7])
                    observe = "%04d-%02d-%02d" % (2000 + yy, mon[mon_s], dd)
                except (ValueError, KeyError):
                    pass
            out.append({
                "venue": "kalshi",
                "ticker": ticker,
                "series": ser,
                "title": title,
                "rules": rules,
                "rules_primary": rules,
                "market": m,
                "side": "yes",
                "price": price,
                "strategy": "WEATHER_ARB",
                "use_lookup": True,
                "live": True,
                "observe_date": observe,
            })
    return out


def _diagnose(tags, live_on, admitted, n_markets, n_alert, skipped_non_nws, pre, price_stats=None,
             admit_reason_counts=None):
    """Why zero (or few) trades — Valedia must explain herself."""
    ps = price_stats or {}
    lines = [
        "# WEATHER-MORNING-DIAGNOSIS",
        "",
        f"ts: {datetime.now().astimezone().isoformat()}",
        f"owner: valedia-runtime",
        f"preflight: {pre.get('status')} flip_allowed={pre.get('weather_live_flip_allowed')}",
        f"WEATHER_LIVE_env: {os.environ.get('WEATHER_LIVE', '')!r}",
        f"weather_live_enabled: {live_on}",
        f"n_markets: {n_markets} n_alert: {n_alert} admitted: {admitted} skipped_non_nws: {skipped_non_nws}",
        f"admit_reason_counts: {dict(admit_reason_counts or {})}",
        f"n_price: {ps.get('n_price')} n_price_null: {ps.get('n_price_null')} "
        f"n_official_price: {ps.get('n_official_price')} n_fee_true_ev: {ps.get('n_ev')}",
        f"tags: {dict(tags)}",
        "",
        "## Why",
    ]
    reasons = []
    twc_key = bool((os.environ.get("WEATHER_COM_API_KEY") or "").strip())
    no_key = int(tags.get("no_api_key") or 0)
    not_final = int(tags.get("forecast_not_final") or 0)
    no_fv = int(tags.get("no_fv") or 0)
    wrong = int(tags.get("wrong_date") or 0)
    official = int(tags.get("official") or 0)
    if not live_on:
        reasons.append("WEATHER_LIVE off or preflight not PASS — admit path closed.")
    if admitted == 0 and live_on:
        if n_alert == 0:
            reasons.append(
                "No fee-true official alerts. INFORMED will not create without settlement FV + edge."
            )
        if no_key and twc_key is False:
            reasons.append(
                f"{no_key} books still unresolved as no_api_key (TWC path). "
                "Free stack should route CLIxxx rules to nws_cli — if this tag dominates, routing failed."
            )
        if not_final:
            reasons.append(
                f"{not_final} books are forecast_not_final — forecast is research-only; "
                "create waits for final NWS CLI observation for that observe-date."
            )
        if wrong:
            reasons.append(
                f"{wrong} books wrong_date — live CLI report date ≠ market observe-date "
                "(morning CLI is usually yesterday’s final; today’s high is not final yet)."
            )
        if no_fv:
            reasons.append(f"{no_fv} books no_fv after lookup.")
        if official and n_alert == 0:
            reasons.append(
                f"{official} official FV rows existed but none cleared fee-true alert "
                "(edge/lottery/late filters)."
            )
        if ps:
            reasons.append(
                f"book prices: {ps.get('n_price')} with price, {ps.get('n_price_null')} null, "
                f"{ps.get('n_official_price')} official-with-price, {ps.get('n_ev')} fee_true_ev "
                "(lottery/late/INFORMED unchanged)."
            )
        if skipped_non_nws:
            reasons.append(
                f"{skipped_non_nws} alerts skipped as non-NWS (TWC settle kept shadow on free stack)."
            )
        if not reasons:
            reasons.append("Live on, zero admits — see tags; no single dominant blocker classified.")
    if n_alert:
        reasons.append(
            f"{n_alert} fee-true official alerts (shadow file; create_order false)."
        )
    if admitted > 0:
        reasons.append(f"Admitted {admitted} NWS-settled official creates.")
    # Timing note
    reasons.append(
        "Timing note: free NWS CLI settlement FV for calendar day D usually posts the next morning "
        "as the YESTERDAY block. Same-day open books at 7:04 AM PT often cannot be INFORMED-final yet; "
        "evening CLI-final rescan is the free-stack edge window."
    )
    for r in reasons:
        lines.append(f"- {r}")
    lines.append("")
    path = os.path.join(REC, "WEATHER-MORNING-DIAGNOSIS.md")
    os.makedirs(REC, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _log("diagnosis_wrote " + path)
    return {"path": path, "reasons": reasons}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-decide" in argv
    _log(f"WEATHER_MORNING_GO start ts={datetime.now().astimezone().isoformat()} tree={ROOT}")
    _log(f"WEATHER_LIVE_env_before={os.environ.get('WEATHER_LIVE', '')!r}")

    import weather_live_preflight as wlp
    import valedia_config as vc
    import weather_shadow as ws

    pre = wlp.run()
    _log(f"preflight status={pre.get('status')} flip_allowed={pre.get('weather_live_flip_allowed')}")
    if pre.get("blockers"):
        _log("preflight blockers: " + ", ".join(pre["blockers"]))

    if dry:
        allow = bool(pre.get("status") == "PASS" and pre.get("weather_live_flip_allowed"))
        _log(f"dry-decide would_set_WEATHER_LIVE={'1' if allow else '0'} (no persist)")
        decided_on = False
    else:
        decided_on = _valedia_decide_live(pre)

    live_on = bool(vc.weather_live_enabled())
    _log(f"weather_live_enabled={live_on} decided_on={decided_on} oil_fenced={('OIL_BRACKET' in vc.LIVE_EXEC_FENCED)}")

    markets = _open_weather_markets()
    _log(f"open_weather_markets n={len(markets)} series_n={len(_series_list())}")

    scan = ws.morning_scan(markets, bankroll=100.0)
    tags = Counter()
    n_price = n_price_null = n_official_price = n_ev = 0
    for r in scan.get("rows") or []:
        tags[r.get("tag") or "unknown"] += 1
        if r.get("price") is None:
            n_price_null += 1
        else:
            n_price += 1
            if (r.get("tag") or "") == "official":
                n_official_price += 1
        if r.get("fee_true_ev") is not None:
            n_ev += 1
    _log(
        f"morning_scan n={scan.get('n')} n_alert={scan.get('n_alert')} "
        f"n_price={n_price} n_price_null={n_price_null} n_official_price={n_official_price} "
        f"n_fee_true_ev={n_ev} create_order={scan.get('create_order')} "
        f"tags={dict(tags)} alert={scan.get('alert')}"
    )

    admitted = 0
    admit_reasons = Counter()
    chosen, skipped_twc = pick_admit(scan.get("alerts") or [])
    if not admit_open(dry, live_on, pre.get("status")):
        _log("admit skipped (dry, WEATHER_LIVE off, or preflight not PASS) — shadow-only")
        chosen = None
    elif chosen is None:
        _log(f"admit skipped no NWS-settled official alert skipped_non_nws={skipped_twc}")
    else:
        import weather_live as wlive
        books = {m.get("ticker"): m for m in markets}
        informed = dict(chosen)
        book = books.get(chosen.get("ticker")) or {}
        if isinstance(book.get("market"), dict):
            informed["market"] = book["market"]
        order = {
            "ticker": chosen.get("ticker"),
            "strategy": "WEATHER_ARB",
            "side": chosen.get("side") or "yes",
            "price": chosen.get("price"),
            "informed": informed,
        }
        ack = wlive.admit(order)
        for code in ack.get("reasons") or ([ack["reason"]] if ack.get("reason") else []):
            admit_reasons[code] += 1
        _log(
            f"admit ticker={order.get('ticker')} create={ack.get('create_order')} "
            f"reason={ack.get('reason') or ack.get('reasons')} "
            f"skipped_non_nws={skipped_twc}"
        )
        if ack.get("create_order"):
            admitted += 1

    price_stats = {
        "n_price": n_price,
        "n_price_null": n_price_null,
        "n_official_price": n_official_price,
        "n_ev": n_ev,
    }
    diag = _diagnose(
        tags, live_on, admitted, len(markets), scan.get("n_alert") or 0, skipped_twc, pre,
        price_stats=price_stats, admit_reason_counts=admit_reasons,
    )

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "owner": "valedia-runtime",
        "grok_bot_routine": "paused",
        "WEATHER_LIVE_decider": "valedia",
        "preflight": pre.get("status"),
        "flip_allowed": pre.get("weather_live_flip_allowed"),
        "weather_live_enabled": live_on,
        "WEATHER_LIVE_env": os.environ.get("WEATHER_LIVE", ""),
        "n_markets": len(markets),
        "n_series": len(_series_list()),
        "n_alert": scan.get("n_alert"),
        "n_price": n_price,
        "n_price_null": n_price_null,
        "n_official_price": n_official_price,
        "n_fee_true_ev": n_ev,
        "tags": dict(tags),
        "admitted": admitted,
        "skipped_non_nws": skipped_twc,
        "admit_reason_counts": dict(admit_reasons),
        "dry": dry,
        "settle_policy": "nws_free_only",
        "diagnosis": diag.get("reasons"),
        "diagnosis_path": diag.get("path"),
    }
    out = os.path.join(REC, "WEATHER-MORNING-GO-LAST.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    _log("summary " + json.dumps(summary, sort_keys=True))
    if live_on and pre.get("status") != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
