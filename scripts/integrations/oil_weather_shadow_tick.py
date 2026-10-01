"""Shadow-only oil + weather tick. Scores. Never creates.

# crontab — DO NOT INSTALL. This process has no live mode and cannot create.
# 20 8 * * 1-5  cd /Users/burtonchatelain/.openclaw/workspace-v2 && python3 scripts/integrations/oil_weather_shadow_tick.py --dry
"""
import sys

import oil_shadow as oil
import valedia_config as vc
import weather_shadow as ws


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--live" in argv or any(a.startswith("--create") for a in argv):
        print("refused: shadow tick has no live mode")
        return 2
    if "--dry" not in argv and argv:
        print("refused: only --dry (and optional --nws-smoke or --cli-smoke) is accepted")
        return 2
    wx = ws.write_ledger(fixtures=[_wx_fixture()])
    ol = oil.write_ledger(fixtures=[_oil_fixture()])
    smoke = "skipped"
    if "--nws-smoke" in argv:
        smoke = _nws_smoke()
    if "--cli-smoke" in argv:
        smoke = _cli_smoke()
    print(
        "OIL-WEATHER-SHADOW dry dispatchable=%s/%s create_order=%s/%s "
        "WEATHER_LIVE=%s fenced=%s nws=%s"
        % (
            wx["dispatchable"], ol["dispatchable"],
            wx["create_order"], ol["create_order"],
            vc.WEATHER_LIVE, ",".join(sorted(vc.LIVE_EXEC_FENCED)), smoke,
        )
    )
    return 0


def _wx_fixture():
    return {
        "ticker": "KXHIGHNY-26SEP22-T72",
        "title": "high temp >72°",
        "fv_source": "nws",
        "forecast_temp": 80,
        "price": 0.40,
        "side": "yes",
        "fixture": True,
        "settlement": "yes",
        "rules_primary": (
            "Last Trading Time is 11:59 PM ET on September 21, 2026. "
            "The observation day is September 22, 2026."
        ),
    }


def _oil_fixture():
    return {
        "ticker": "KXWTI-26SEP23-T72",
        "title": "WTI above $72",
        "fv_source": "nymex",
        "rules_cite": "nymex_cl",
        "print_kind": "settlement",
        "print_usd": 80,
        "price": 0.40,
        "side": "yes",
        "fixture": True,
        "settlement": "yes",
    }


def _cli_smoke():
    """One NYC CLI page. Read-only GET. No order. FV only if the report date matches."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("America/New_York")).date()
    text = "The observation day is %s %d, %d. Daily high settles on the NWS climate report." % (
        day.strftime("%B"), day.day, day.year)
    try:
        row = ws.score({
            "ticker": "KXHIGHNY",
            "title": "high temp >72°",
            "use_lookup": True,
            "live": True,
            "book": "cli_smoke",
            "rules_primary": text,
        })
    except Exception as exc:
        return "cli_smoke_fail:%s" % type(exc).__name__
    ws.append_scored([row])
    sf = row.get("settlement_fv") or {}
    return "cli_%s value=%s fv=%s size=%s" % (
        row["tag"], sf.get("value"), row.get("fv"), row.get("would_have_size"))


def _nws_smoke():
    """One NYC forecast. FV only if the period date is the observe-day. No order."""
    from datetime import datetime
    import weather_nws as wn
    try:
        payload = wn.fetch_nws(40.7128, -74.0060)
    except Exception as exc:
        return "smoke_fail:%s" % type(exc).__name__
    periods = ((payload.get("properties") or {}).get("periods") or payload.get("periods") or [])
    valid = None
    for p in periods:
        if isinstance(p, dict) and p.get("temperature") is not None and p.get("isDaytime", True):
            raw = p.get("startTime") or p.get("start_time")
            try:
                valid = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
            except ValueError:
                valid = None
            if valid:
                break
    if valid is None:
        return "smoke_fail:no_period"
    text = "The observation day is %s %d, %d." % (valid.strftime("%B"), valid.day, valid.year)
    row = ws.score({
        "ticker": "KXHIGHNY",
        "title": "high temp >72°",
        "fv_source": "nws",
        "nws": payload,
        "book": "nws_smoke",
        "rules_primary": text,
    })
    ws.append_scored([row])
    return "smoke_%s fv=%s size=%s" % (row["tag"], row.get("fv"), row.get("would_have_size"))


if __name__ == "__main__":
    sys.exit(main())
