"""Weather.com JSON. Research by default. Hourly settlement only if observed.

The HAR routes are forecasts. A forecast array is never settlement_fv.
WEATHER_COM_API_KEY stays in the environment. URLs we return are redacted.
No browser. No key is read from the repo.
"""
import os
import re

from weather_sources._get import get_text

_KEY_RE = re.compile(r"(?i)(apiKey=)[^&\s]+")
# Station geocodes for research URLs. Not a general geocoder.
GEOCODE = {
    "NYC": "40.713,-74.006",
    "MIA": "25.791,-80.316",
    "LAS": "36.072,-115.163",
}


def redact(url):
    return _KEY_RE.sub(r"\1{redacted}", url or "")


def _kind(payload, kind):
    if kind:
        return kind
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("forecast"), dict) and "temperature" not in payload:
        return "wwir"
    if payload.get("calendarDayTemperatureMax") is not None or payload.get("temperatureMax") is not None:
        return "daily"
    if payload.get("temperature") is not None:
        return "hourly"
    return None


def _aligned(payload, keys):
    times = payload.get("validTimeLocal")
    if not isinstance(times, list) or not times:
        return None, "no_valid_time"
    n = len(times)
    cols = {}
    for key in keys:
        arr = payload.get(key)
        if not isinstance(arr, list) or len(arr) != n:
            return None, "misaligned"
        cols[key] = arr
    return cols, None


def _index(times, observe_date):
    if not observe_date:
        return None
    day = str(observe_date)
    hits = [i for i, raw in enumerate(times) if str(raw)[:10] == day]
    return hits[0] if hits else None


def _tag(source_endpoint, valid_time, summary, provenance, kind_label):
    return {
        "source": "weather_com",
        "endpoint": source_endpoint,
        "fetched_at": None,
        "valid_time": valid_time,
        "forecast_or_observation": kind_label,
        "value_summary": summary,
        "provenance": provenance,
    }


def published_url(family, geocode):
    geo = geocode or "{lat},{lon}"
    return redact(
        "https://api.weather.com/v3/wx/forecast/%s?geocode=%s&units=e"
        "&language=en-US&format=json&apiKey={redacted}" % (family, geo)
    )


def _obs_temp(val):
    if isinstance(val, float) and val.is_integer():
        return int(val)
    return val


def parse(payload, observe_date, kind=None, geocode=None, raw_fixture_ref=None, finality=None, metric=None):
    """Align one fixture. A forecast is never settlement_fv.

    Hourly settlement requires observation=true. Daily high/low settlement
    requires the same honesty (observation or finality final/observation).
    """
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "bad_payload", "research_tag": None, "settlement_fv": None}
    kind = _kind(payload, kind)
    ref = raw_fixture_ref or "fixture"
    if kind == "wwir":
        fc = payload.get("forecast") or {}
        phrase = fc.get("phrase") or fc.get("terse_phrase")
        tag = _tag("wwir", fc.get("fcst_valid_local"), phrase, ref, "forecast") if phrase else None
        return {"ok": bool(tag), "reason": None if tag else "no_phrase",
                "research_tag": tag, "settlement_fv": None}
    if kind == "daily":
        cols, why = _aligned(payload, ("validTimeLocal", "calendarDayTemperatureMax", "calendarDayTemperatureMin"))
        family = "daily/15day"
        if why:
            return {"ok": False, "reason": why, "research_tag": None, "settlement_fv": None}
        i = _index(payload["validTimeLocal"], observe_date)
        if i is None:
            return {"ok": False, "reason": "date_absent", "research_tag": None, "settlement_fv": None}
        hi, lo = cols["calendarDayTemperatureMax"][i], cols["calendarDayTemperatureMin"][i]
        observed = payload.get("observation") is True or finality in ("final", "observation")
        if finality in ("preliminary", "forecast"):
            observed = False
        label = "observation" if observed else "forecast"
        tag = _tag(family, payload["validTimeLocal"][i],
                   "max %s F min %s F" % (hi, lo), ref, label)
        settlement = None
        reason = None
        if finality == "preliminary":
            reason = "preliminary"
        elif observed:
            met = metric if metric in ("maximum", "minimum") else "maximum"
            val = lo if met == "minimum" else hi
            val = _obs_temp(val)
            if val is None:
                reason = "forecast_not_final"
            else:
                conf = "observation" if payload.get("observation") is True else "final"
                settlement = {
                    "value": val,
                    "unit": "F",
                    "metric": met,
                    "observe_date": str(observe_date),
                    "observed_at": payload["validTimeLocal"][i],
                    "source": "weather_com",
                    "source_url": published_url(family, geocode),
                    "station_or_geocode": geocode,
                    "confidence": conf,
                    "raw_fixture_ref": raw_fixture_ref,
                }
        return {"ok": True, "reason": reason, "research_tag": tag, "settlement_fv": settlement,
                "source_url": published_url(family, geocode)}
    if kind == "hourly":
        cols, why = _aligned(payload, ("validTimeLocal", "temperature"))
        if why:
            return {"ok": False, "reason": why, "research_tag": None, "settlement_fv": None}
        i = _index(payload["validTimeLocal"], observe_date)
        if i is None:
            return {"ok": False, "reason": "date_absent", "research_tag": None, "settlement_fv": None}
        temp = cols["temperature"][i]
        observed = payload.get("observation") is True and finality != "preliminary"
        label = "observation" if observed else "forecast"
        tag = _tag("hourly/2day", payload["validTimeLocal"][i],
                   "%s F" % temp, ref, label)
        settlement = None
        reason = None
        if observed and temp is not None and finality != "forecast":
            if isinstance(temp, float) and temp.is_integer():
                temp = int(temp)
            settlement = {
                "value": temp,
                "unit": "F",
                "metric": "hourly_temp",
                "observe_date": str(observe_date),
                "observed_at": payload["validTimeLocal"][i],
                "source": "weather_com",
                "source_url": published_url("hourly/2day", geocode),
                "station_or_geocode": geocode,
                "confidence": "observation",
                "raw_fixture_ref": raw_fixture_ref,
            }
        elif payload.get("observation") is True and finality == "preliminary":
            reason = "preliminary"
            settlement = None
        else:
            reason = "forecast_not_final"
        return {"ok": settlement is not None, "reason": reason,
                "research_tag": tag, "settlement_fv": settlement}
    return {"ok": False, "reason": "unknown_family", "research_tag": None, "settlement_fv": None}


def fetch(family, geocode, timeout=12):
    """JSON GET only when a key is in the environment. Never logs the key."""
    key = os.environ.get("WEATHER_COM_API_KEY")
    if not key:
        return {"ok": False, "reason": "no_api_key", "client": None}
    geo = str(geocode or "")
    if family == "wwir":
        lat, lon = geo.split(",", 1)
        url = ("https://api.weather.com/v1/geocode/%s/%s/forecast/wwir.json"
               "?units=e&language=en-US&apiKey=%s") % (lat, lon, key)
    else:
        url = ("https://api.weather.com/v3/wx/forecast/%s?geocode=%s&units=e"
               "&language=en-US&format=json&apiKey=%s") % (family, geo, key)
    try:
        body, client = get_text(url, timeout=timeout, accept="application/json")
    except Exception:
        return {"ok": False, "reason": "fetch_failed", "client": None}
    return {"ok": True, "body": body, "client": client, "source_url": redact(url)}
