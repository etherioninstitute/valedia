"""Unified weather lookup. Settlement FV or an explicit refuse.

Daily Kalshi city high/low (KXHIGH / KXLOW) settles on The Weather Company
when the rules name Weather Company, weather.com, TWC, or a CLIxxx product.
NWS CLI is the settlement source only when the rules name NWS, and otherwise
a research cross-check. Hourly settles on Weather.com only when the payload
is an observation. Rain without an NWS precip CLI and hurricane without NHC
refuse with need_fv_precip / need_fv_nhc. Research tags never set
informed_pass, create_order, or dispatchable. Temperature is not a probability.
"""
import json
import re

from weather_sources import accuweather, ecmwf, nws_cli, weather_com

_CLI_CODE = re.compile(r"\bcli[a-z]{3}\b")

_SERIES = (
    ("KXHIGH", "maximum"),
    ("KXLOW", "minimum"),
    ("KXRAIN", "precip_today"),
)
_RESEARCH_NAMED = frozenset({
    "accuweather", "accu", "ecmwf", "cpc", "mirofish", "muse", "gdelt",
})


def _series_metric(ticker):
    head = str(ticker or "").upper().split("-", 1)[0]
    for prefix, metric in _SERIES:
        if head.startswith(prefix):
            return metric
    return None


def _twc(text):
    return any(s in text for s in (
        "weather company", "weather channel", "weather.com", " twc", "twc ",
    )) or text.strip() == "twc"


def _nws(text):
    return any(s in text for s in (
        "nws", "national weather service", "climate report", "climatological",
    ))


def _metric_from_rules(text):
    if "precipitation" in text or "precip" in text:
        return "precip_today"
    if "daily low" in text or "minimum temperature" in text or "low temp" in text:
        return "minimum"
    if "daily high" in text or "maximum temperature" in text or "high temp" in text:
        return "maximum"
    return None


def _cli_code(text):
    return _CLI_CODE.search(text or "") is not None


def _daily_city(series_metric, hourly):
    return (not hourly) and series_metric in ("maximum", "minimum")


def resolve_source(venue, ticker, rules, settle_source=None, metric=None):
    """(family, metric, reason). reason is set when the source cannot be named."""
    text = " ".join(str(rules or "").lower().split())
    named = str(settle_source or "").strip().lower().replace("-", "_")
    head = str(ticker or "").upper().split("-", 1)[0]
    if head.startswith("KXHURRICANE"):
        return None, None, "need_fv_nhc"
    if named in _RESEARCH_NAMED:
        return None, None, "research_not_settlement"
    series_metric = _series_metric(ticker)
    hourly = "hourly" in text
    if series_metric == "precip_today" and not hourly and not _nws(text) and (
            _twc(text) or _cli_code(text) or named in ("weather_com", "twc", "weather_company")):
        return None, None, "need_fv_precip"
    if named in ("nws", "nws_cli", "noaa", "climate", "climate_data"):
        chosen = metric or series_metric or _metric_from_rules(text)
        if not chosen:
            return None, None, "metric_unresolved"
        return "nws_cli", chosen, None
    if named in ("weather_com", "twc", "weather_company"):
        if _daily_city(series_metric, hourly):
            return "weather_com", metric or series_metric, None
        return "weather_com", "hourly_temp", None
    if hourly and _twc(text):
        return "weather_com", "hourly_temp", None
    # Free stack: Kalshi rules name The Weather Company + CLIxxx climate product.
    # CLIxxx is the NOAA/NWS climate report on weather.gov — use it when no TWC key.
    if _daily_city(series_metric, hourly) and (_twc(text) or _cli_code(text)):
        import os as _os
        twc_key = (_os.environ.get("WEATHER_COM_API_KEY") or "").strip()
        free = (_os.environ.get("WEATHER_SETTLE_FREE", "1") or "1").strip() != "0"
        if _cli_code(text) and (free or not twc_key):
            return "nws_cli", metric or series_metric, None
        return "weather_com", metric or series_metric, None
    if "accuweather" in text and not _nws(text):
        return None, None, "research_not_settlement"
    if series_metric and not hourly:
        return "nws_cli", metric or series_metric, None
    if _nws(text) and not hourly:
        chosen = metric or series_metric or _metric_from_rules(text)
        if not chosen:
            return None, None, "metric_unresolved"
        return "nws_cli", chosen, None
    return None, None, "settle_source_unresolved"


def _city(ticker, city):
    if city:
        return str(city).upper().replace(" ", "")
    import weather_shadow as ws
    _prefix, code = ws.split_series(ticker)
    return code


def _base(venue, ticker, unresolved, settlement, tags, family):
    return {
        "ok": settlement is not None,
        "venue": venue,
        "ticker": ticker,
        "unresolved": unresolved,
        "settlement_fv": settlement,
        "research_tags": tags,
        "settle_source": family,
        "create_order": False,
        "dispatchable": 0,
        "informed_pass": False,
    }


def informed_view(result):
    """Create-gate view. Research numbers are not fv. Always fail-closed here."""
    result = result if isinstance(result, dict) else {}
    tags = result.get("research_tags") or []
    sf = result.get("settlement_fv") if isinstance(result.get("settlement_fv"), dict) else None
    if sf:
        src = sf.get("source") or ""
    elif tags:
        src = "research"
    else:
        src = ""
    return {
        "fv": None,
        "fv_source": src,
        "fv_confidence": None,
        "strategy": "WEATHER_ARB",
        "create_order": False,
        "dispatchable": 0,
        "informed_pass": False,
    }


def _research(observe_date, fixtures, geocode, metric=None):
    tags = []
    fx = fixtures if isinstance(fixtures, dict) else {}
    wx = fx.get("wx_json")
    if isinstance(wx, str):
        try:
            wx = json.loads(wx)
        except ValueError:
            wx = None
    if isinstance(wx, dict):
        parsed = weather_com.parse(
            wx, observe_date, kind=fx.get("wx_kind"), geocode=geocode,
            raw_fixture_ref=fx.get("wx_ref") or fx.get("raw_fixture_ref"),
            finality=fx.get("finality"), metric=metric)
        if parsed.get("research_tag"):
            tags.append(parsed["research_tag"])
        wx_parsed = parsed
    else:
        wx_parsed = None
    if fx.get("accu_html"):
        acc = accuweather.parse(
            fx.get("accu_html"), observe_date, raw_fixture_ref=fx.get("accu_ref"))
        if acc.get("research_tag"):
            tags.append(acc["research_tag"])
    if fx.get("ecmwf_products") is not None:
        tags.append(ecmwf.research_tag(fx.get("ecmwf_products")))
    return tags, wx_parsed


def lookup(venue, ticker_or_slug, city=None, settle_source=None, observe_date=None,
           metric=None, rules=None, tz=None, geocode=None, live=False, fixtures=None):
    """lookup(trade) -> settlement_fv + research_tags, or unresolved."""
    venue = str(venue or "").lower()
    ticker = ticker_or_slug
    family, metric, why = resolve_source(venue, ticker, rules, settle_source, metric)
    fx = fixtures if isinstance(fixtures, dict) else {}
    station = nws_cli.station_for(city=_city(ticker, city), wfo=fx.get("wfo"), issuedby=fx.get("issuedby"))
    if family == "nws_cli" and station is None:
        tags, _wx = _research(observe_date, fx, geocode, metric)
        return _base(venue, ticker, "station_unconfigured", None, tags, family)
    geo = geocode
    if not geo and station and station.get("key") in weather_com.GEOCODE:
        geo = weather_com.GEOCODE[station["key"]]
    tags, wx_parsed = _research(observe_date, fx, geo, metric)
    if family == "weather_com" and fx.get("cli_html") and station:
        url = nws_cli.product_url(station["wfo"], station["issuedby"])
        cross = nws_cli.parse(
            fx.get("cli_html"), observe_date, metric if metric in ("maximum", "minimum", "precip_today") else "maximum",
            station, source_url=url, raw_fixture_ref=fx.get("raw_fixture_ref"))
        sf = cross.get("settlement_fv") if cross.get("ok") else None
        if isinstance(sf, dict):
            tags.append({
                "source": "nws_cli",
                "endpoint": "cli",
                "fetched_at": None,
                "valid_time": sf.get("observe_date"),
                "forecast_or_observation": "observation",
                "value_summary": "%s %s" % (sf.get("value"), sf.get("unit")),
                "provenance": "cross_check",
                "note": "not_settlement",
            })
    if why:
        return _base(venue, ticker, why, None, tags, family)
    if not observe_date:
        return _base(venue, ticker, "observe_date_missing", None, tags, family)
    settlement = None
    unresolved = None
    if family == "nws_cli":
        html = fx.get("cli_html")
        url = nws_cli.product_url(station["wfo"], station["issuedby"])
        ref = fx.get("raw_fixture_ref")
        if not html and live:
            try:
                got = nws_cli.fetch(station["wfo"], station["issuedby"])
            except Exception:
                return _base(venue, ticker, "fetch_failed", None, tags, family)
            html = got.get("html")
            url = got.get("source_url") or url
            ref = ref or "live"
        if not html:
            unresolved = "missing_settlement_fv"
        else:
            parsed = nws_cli.parse(html, observe_date, metric, station, source_url=url, raw_fixture_ref=ref)
            if parsed.get("ok"):
                settlement = parsed["settlement_fv"]
            else:
                unresolved = parsed.get("reason") or "missing_settlement_fv"
    elif family == "weather_com":
        # Live daily pull when no fixture body. Needs WEATHER_COM_API_KEY.
        # Forecast payloads stay research-only until observation/final.
        if wx_parsed is None and live and geo:
            got = weather_com.fetch("daily/15day", geo)
            if not got.get("ok"):
                unresolved = got.get("reason") or "fetch_failed"
            else:
                try:
                    payload = json.loads(got.get("body") or "")
                except ValueError:
                    payload = None
                    unresolved = "bad_payload"
                if payload is not None:
                    parsed = weather_com.parse(
                        payload, observe_date, kind="daily", geocode=geo,
                        raw_fixture_ref="live", finality=fx.get("finality"),
                        metric=metric)
                    if parsed.get("research_tag"):
                        tags.append(parsed["research_tag"])
                    wx_parsed = parsed
        if unresolved:
            return _base(venue, ticker, unresolved, None, tags, family)
        sf = (wx_parsed or {}).get("settlement_fv")
        if isinstance(sf, dict) and (not metric or sf.get("metric") == metric):
            settlement = sf
        else:
            unresolved = (wx_parsed or {}).get("reason") or "forecast_not_final"
    else:
        unresolved = why or "settle_source_unresolved"
    if settlement is None and not unresolved:
        unresolved = "missing_settlement_fv"
    return _base(venue, ticker, None if settlement else unresolved, settlement, tags, family)


def attach(row, looked):
    """Stamp lookup fields. Does not change dispatchable or create_order."""
    if looked is None:
        return row
    row["settlement_fv"] = looked.get("settlement_fv")
    row["research_tags"] = looked.get("research_tags") or []
    row["lookup_unresolved"] = looked.get("unresolved")
    return row


def _wants(candidate):
    c = candidate if isinstance(candidate, dict) else {}
    return any(c.get(k) for k in (
        "cli_html", "wx_json", "accu_html", "ecmwf_products", "use_lookup", "live",
    ))


def shadow_forecast(candidate, parsed, source, forecast, official_from):
    """(forecast, source, blocked, lookup_or_None). None lookup keeps the old path."""
    c = candidate if isinstance(candidate, dict) else {}
    parsed = parsed if isinstance(parsed, dict) else {}
    if not _wants(c):
        f, s, b = official_from(c, parsed.get("observe_date"), source, forecast)
        return f, s, b, None
    market = c.get("market") if isinstance(c.get("market"), dict) else {}
    result = lookup(
        c.get("venue") or "kalshi",
        c.get("ticker") or c.get("slug") or c.get("ticker_or_slug"),
        city=c.get("city") or parsed.get("city"),
        settle_source=c.get("settle_source"),
        observe_date=c.get("observe_date") or parsed.get("observe_date"),
        metric=c.get("metric"),
        rules=c.get("rules") or c.get("rules_primary") or market.get("rules_primary"),
        tz=parsed.get("tz"),
        geocode=c.get("geocode"),
        live=c.get("live") is True,
        fixtures={
            "cli_html": c.get("cli_html"),
            "wx_json": c.get("wx_json"),
            "wx_kind": c.get("wx_kind"),
            "wx_ref": c.get("wx_ref"),
            "accu_html": c.get("accu_html"),
            "accu_ref": c.get("accu_ref"),
            "ecmwf_products": c.get("ecmwf_products"),
            "wfo": c.get("wfo"),
            "issuedby": c.get("issuedby"),
            "raw_fixture_ref": c.get("raw_fixture_ref"),
            "finality": c.get("finality"),
        },
    )
    sf = result.get("settlement_fv")
    if not isinstance(sf, dict) or sf.get("value") is None:
        return None, source, (result.get("unresolved") or "missing_settlement_fv"), result
    if sf.get("metric") not in ("maximum", "minimum", "hourly_temp"):
        return None, sf.get("source") or source, "no_bracket", result
    if sf.get("source") not in ("nws_cli", "weather_com"):
        return None, source, "missing_settlement_fv", result
    if sf.get("confidence") not in ("final", "observation"):
        return None, sf.get("source"), "not_final", result
    return float(sf["value"]), sf["source"], None, result
