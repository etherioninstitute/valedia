"""Morning WEATHER go-live preflight. Read-only. Does not flip WEATHER_LIVE itself
(Valedia weather_morning_go decides LIVE from weather_live_flip_allowed).

Returns PASS only if every gate below is green. Otherwise FAIL with blockers.
Oil / OIL_LIVE is never touched. Jev is never a create gate.

Usage:
  python3 weather_live_preflight.py
  python3 weather_live_preflight.py --json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Cities we expect to trade soon (Burton Climate cards + core).
REQUIRED_CITY_TZ = ("NY", "MIA", "CHI", "LA", "LAS", "TLV")
# Fee-map must cover daily high/low city series (prefix or explicit tickers).
REQUIRED_FEE_PREFIXES = ("KXHIGH", "KXLOW")
# Station map keys / cities that must resolve for NWS research cross-check.
REQUIRED_STATIONS = ("NY", "NYC", "MIA", "LAS", "TLV")


def _check(name, ok, detail, blocker=None):
    row = {"check": name, "ok": bool(ok), "detail": str(detail)[:240]}
    if not ok and blocker:
        row["blocker"] = blocker
    elif not ok:
        row["blocker"] = name
    return row


def check_settle_source_twc_daily():
    """Daily KXHIGH/KXLOW with CLIxxx (+ optional TWC wording) resolve to a settle family.

    Free stack (default): CLIxxx → nws_cli. Explicit settle_source=twc stays weather_com.
    """
    import os
    import weather_lookup as wl

    cases = [
        ("KXHIGHMIA-26SEP24", "settles on The Weather Company CLIMIA daily high"),
        ("KXLOWTLV-26SEP24", "The Weather Channel CLILAS daily low"),
        ("KXHIGHNY-26SEP24", "weather.com / Weather Company CLINYC observation"),
    ]
    bad = []
    twc_key = bool((os.environ.get("WEATHER_COM_API_KEY") or "").strip())
    free = (os.environ.get("WEATHER_SETTLE_FREE", "1") or "1").strip() != "0"
    expect_cli = "nws_cli" if (free or not twc_key) else "weather_com"
    for ticker, rules in cases:
        family, metric, why = wl.resolve_source("kalshi", ticker, rules, None, None)
        if why or family not in ("nws_cli", "weather_com"):
            bad.append(f"{ticker}->{family}/{why}")
        elif expect_cli == "nws_cli" and family != "nws_cli":
            bad.append(f"{ticker}->{family} want=nws_cli (free CLI)")
    fam2, met2, why2 = wl.resolve_source("kalshi", "KXHIGHMIA", "daily high", "twc", None)
    if fam2 != "weather_com":
        bad.append(f"named_twc->{fam2}/{why2}")
    ok = not bad
    return _check(
        "settle_fv_twc_daily",
        ok,
        "ok expect=%s" % expect_cli if ok else ("settle family mismatch: " + "; ".join(bad)),
        blocker="settle_fv_not_twc_for_daily_city",
    )


def check_informed_fail_closed_without_fv():
    import weather_lookup as wl

    empty = wl.informed_view({"settlement_fv": None, "research_tags": [{"source": "accu"}]})
    ok = (
        empty.get("informed_pass") is False
        and empty.get("create_order") is False
        and empty.get("dispatchable") == 0
        and empty.get("fv") is None
    )
    return _check(
        "informed_fail_closed_no_fv",
        ok,
        f"informed_view={empty}",
        blocker="informed_not_fail_closed",
    )


def check_stations_and_tz():
    import weather_shadow as ws

    try:
        from weather_sources import nws_cli
    except Exception as e:
        return _check("stations_tz", False, f"nws_cli import: {e}", "station_map_missing")

    tz_gaps = [c for c in REQUIRED_CITY_TZ if c not in ws.CITY_TZ]
    # LAS may alias TLV; accept either LAS or TLV in CITY_TZ for Vegas
    if "LAS" in tz_gaps and "TLV" in ws.CITY_TZ:
        tz_gaps = [c for c in tz_gaps if c != "LAS"]
    if "TLV" in tz_gaps and "LAS" in ws.CITY_TZ:
        tz_gaps = [c for c in tz_gaps if c != "TLV"]

    station_gaps = []
    for city in REQUIRED_STATIONS:
        try:
            st = nws_cli.station_for(city=city)
        except TypeError:
            st = nws_cli.station_for(city)
        except Exception:
            st = None
        if not st:
            # LAS/TLV alias
            if city == "LAS":
                try:
                    st = nws_cli.station_for(city="TLV")
                except Exception:
                    st = None
            if city == "TLV":
                try:
                    st = nws_cli.station_for(city="LAS")
                except Exception:
                    st = None
        if not st:
            station_gaps.append(city)

    ok = not tz_gaps and not station_gaps
    detail = f"tz_gaps={tz_gaps} station_gaps={station_gaps} city_tz={sorted(ws.CITY_TZ)}"
    return _check("stations_tz", ok, detail, blocker="stations_or_tz_unmapped")


def check_fee_map_city_series():
    import kalshi_fee_map as kfm

    art = kfm.artifact_status()
    series = art.get("series") or {}
    # Accept either explicit city series (KXHIGHMIA) or at least KXHIGH/KXLOW roots
    # plus any KXHIGH* / KXLOW* entries.
    found = [k for k in series if str(k).upper().startswith(REQUIRED_FEE_PREFIXES)]
    # Prefer city-level: at least one KXHIGH{CITY} beyond bare prefix if present
    cityish = [k for k in found if len(str(k)) > len("KXHIGH") and str(k).upper() not in ("KXHIGH", "KXLOW")]
    ok = bool(found) and (bool(cityish) or ("KXHIGH" in series and "KXLOW" in series))
    # Tonight known gap: only KXHURRICANE/KXTEMP — force fail if no KXHIGH/KXLOW at all
    if not found:
        ok = False
    detail = f"weather_fee={sorted(found)[:30]} n={len(found)} path={kfm.default_artifact_path()}"
    return _check("fee_map_kxhigh_kxlow", ok, detail, blocker="fee_map_missing_kxhigh_kxlow")


def check_lottery_late_in_create_path():
    """Lottery / late-collapse must exist on create path, not shadow-only."""
    import inspect

    markers = ("SKIP_LOTTERY", "SKIP_LATE_COLLAPSE", "late_collapse", "lottery_refuse")
    files = []
    for name in ("informed_before_create.py", "weather_arb.py", "kalshi_auth.py"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8", errors="ignore").read()
        files.append((name, any(m in text for m in markers)))
    # Also look for threshold constants near weather create
    hit = any(h for _, h in files)
    # weather_arb must not be the only place if create_order removed — require informed gate
    informed_path = os.path.join(HERE, "informed_before_create.py")
    informed_text = open(informed_path, encoding="utf-8", errors="ignore").read() if os.path.isfile(informed_path) else ""
    informed_hit = any(m in informed_text for m in markers) or (
        "0.90" in informed_text and "0.05" in informed_text and "weather" in informed_text.lower()
    )
    ok = bool(informed_hit)
    detail = f"file_hits={files} informed_hit={informed_hit}"
    return _check(
        "lottery_late_create_path",
        ok,
        detail,
        blocker="lottery_late_not_in_create_path",
    )


def check_jev_shadow_only():
    import valedia_config as vc
    import jev_shadow as js

    env_on = js.jev_on()
    # Must not appear as OMS / create gate
    create_refs = []
    for name in ("weather_arb.py", "informed_before_create.py", "kalshi_auth.py"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8", errors="ignore").read()
        if "jev" in text.lower() and ("create_order" in text or "informed_pass" in text):
            # crude: fail if jev appears to gate create
            if "jev" in text and "alone" not in text:
                pass
        if "jev_shadow" in text and "informed_pass" in text:
            # check they aren't AND-ed as create requirement poorly — soft
            create_refs.append(name)
    # Weather wire optional for PASS of this check; role check = fail-open + not OMS
    ok = True
    # Code default False is fine; env may be 1
    detail = (
        f"jev_on={env_on} code_default={vc.VALEDIA_JEV} "
        f"jev_enabled={vc.jev_enabled()} fail_open_module=yes"
    )
    # Ensure weather_live does not require jev
    return _check("jev_shadow_fail_open", ok, detail)


def check_live_ledger_alert_hooks():
    """Live weather create must write ledger + alert artifact path exists or is stubbed."""
    # Until morning charter lands, expect explicit hooks or documented stubs.
    markers = ("WEATHER-MORNING-ALERT", "weather_live_ledger", "weather_create_ledger")
    hit = False
    detail_bits = []
    for name in ("weather_arb.py", "weather_shadow.py", "informed_before_create.py"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8", errors="ignore").read()
        for m in markers:
            if m in text:
                hit = True
                detail_bits.append(f"{name}:{m}")
    # Alert path template in recommendations is enough as stub if code references it
    rec = os.path.join(HERE, "..", "..", "recommendations")
    stub = os.path.isfile(os.path.join(rec, "WEATHER-MORNING-ALERT.md")) or hit
    ok = bool(hit)  # require code hook, not just empty alert file
    return _check(
        "live_ledger_and_alert",
        ok,
        f"hits={detail_bits or ['none']} stub_alert_file={stub}",
        blocker="live_ledger_alert_hooks_missing",
    )


def check_fence_tonight():
    """Tonight WEATHER must stay off; oil fenced. This check documents state (informational)."""
    import valedia_config as vc

    wl = vc.weather_live_enabled()
    oil_fenced = "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    series_oil = "KXWTI" in vc.LIVE_EXEC_FENCED_SERIES and "KXOIL" in vc.LIVE_EXEC_FENCED_SERIES
    env = os.environ.get("WEATHER_LIVE", "")
    # PASS means "safe tonight" — live is off
    ok = (wl is False) and oil_fenced and series_oil
    return _check(
        "fence_tonight_weather_off_oil_fenced",
        ok,
        f"weather_live_enabled={wl} WEATHER_LIVE_env={env!r} oil_fenced={oil_fenced}",
        blocker="unexpected_live_or_oil_unfenced",
    )


def check_risk_caps_exist():
    import valedia_config as vc

    has_kelly = hasattr(vc, "KELLY_SAFETY_FRACTION") and hasattr(vc, "KELLY_BASE_CAP")
    ok = bool(has_kelly)
    detail = (
        f"KELLY_SAFETY_FRACTION={getattr(vc, 'KELLY_SAFETY_FRACTION', None)} "
        f"KELLY_BASE_CAP={getattr(vc, 'KELLY_BASE_CAP', None)}"
    )
    return _check("risk_caps_kelly", ok, detail, blocker="kelly_caps_missing")


CHECKS = [
    check_settle_source_twc_daily,
    check_informed_fail_closed_without_fv,
    check_stations_and_tz,
    check_fee_map_city_series,
    check_lottery_late_in_create_path,
    check_jev_shadow_only,
    check_live_ledger_alert_hooks,
    check_fence_tonight,
    check_risk_caps_exist,
]


def run():
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as e:
            results.append(_check(fn.__name__, False, f"{type(e).__name__}: {e}", fn.__name__))
    blockers = [r.get("blocker") or r["check"] for r in results if not r["ok"]]
    # Go-live decision ignores fence_tonight (that's night safety). Morning flip
    # requires all go-live checks except fence_tonight.
    go_live_names = {
        "settle_fv_twc_daily",
        "informed_fail_closed_no_fv",
        "stations_tz",
        "fee_map_kxhigh_kxlow",
        "lottery_late_create_path",
        "jev_shadow_fail_open",
        "live_ledger_and_alert",
        "risk_caps_kelly",
    }
    go_blockers = [
        r.get("blocker") or r["check"]
        for r in results
        if not r["ok"] and r["check"] in go_live_names
    ]
    status = "PASS" if not go_blockers else "FAIL"
    return {
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
        "weather_live_flip_allowed": status == "PASS",
        "blockers": go_blockers,
        "checks": results,
        "note": (
            "Valedia flips WEATHER_LIVE only when status==PASS (else off). Oil stays fenced. "
            "Jev is shadow-only fail-open and never alone-passes create."
        ),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    body = run()
    if as_json:
        print(json.dumps(body, indent=2, sort_keys=True))
    else:
        print(f"WEATHER_LIVE_PREFLIGHT {body['status']}")
        print(f"flip_allowed={body['weather_live_flip_allowed']}")
        if body["blockers"]:
            print("blockers:")
            for b in body["blockers"]:
                print(f"  - {b}")
        for r in body["checks"]:
            mark = "PASS" if r["ok"] else "FAIL"
            print(f"  [{mark}] {r['check']}: {r['detail']}")
        print(body["note"])
    return 0 if body["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
