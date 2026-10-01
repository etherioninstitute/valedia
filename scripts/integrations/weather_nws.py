"""Official NWS forecast extract. Network only when the caller asks.

A period whose calendar date is not the contract observe-day is not today's FV.
Stale cache titles (March observe days) stay stale.
"""
import json
from datetime import datetime
from urllib.request import Request, urlopen

UA = "valedia-weather-shadow/research (contact.etherion@protonmail.com)"


def _date(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _temp_f(period):
    temp = float(period["temperature"])
    unit = str(period.get("temperatureUnit") or period.get("temperature_unit") or "F").upper()
    if unit == "C":
        temp = temp * 9.0 / 5.0 + 32.0
    return temp


def nws_day_temp(payload, observe_date):
    """(temp_f, valid_date, why). why is ok | stale | no_period | bad_payload."""
    if not isinstance(payload, dict):
        return None, None, "bad_payload"
    props = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    periods = props.get("periods") or payload.get("periods")
    if not isinstance(periods, list) or not periods:
        return None, None, "bad_payload"
    daytime = [
        p for p in periods
        if isinstance(p, dict) and p.get("temperature") is not None and p.get("isDaytime", True)
    ]
    if not daytime:
        return None, None, "no_period"
    if not observe_date:
        d0 = _date(daytime[0].get("startTime") or daytime[0].get("start_time"))
        return None, d0, "stale"
    for p in daytime:
        d = _date(p.get("startTime") or p.get("start_time"))
        if d == observe_date:
            return _temp_f(p), d, "ok"
    d0 = _date(daytime[0].get("startTime") or daytime[0].get("start_time"))
    return None, d0, "stale"


def official_from(candidate, observe_date, source, forecast):
    """NWS payload replaces a caller forecast. Stale payload blocks FV.

    Returns (forecast, source, blocked_tag or None).
    """
    c = candidate if isinstance(candidate, dict) else {}
    payload = c.get("nws")
    if not isinstance(payload, dict):
        return forecast, source, None
    temp, _valid, why = nws_day_temp(payload, observe_date)
    if why != "ok":
        return None, "nws", "stale_observe" if why == "stale" else "no_fv"
    return temp, "nws", None


def fetch_nws(lat, lon, timeout=8):
    """Live GET api.weather.gov. Tests do not call this."""
    points = "https://api.weather.gov/points/%.4f,%.4f" % (float(lat), float(lon))
    req = Request(points, headers={"User-Agent": UA, "Accept": "application/geo+json"})
    with urlopen(req, timeout=timeout) as resp:
        meta = json.load(resp)
    url = ((meta.get("properties") or {}).get("forecast"))
    if not url:
        raise RuntimeError("nws_no_forecast_url")
    req2 = Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json"})
    with urlopen(req2, timeout=timeout) as resp:
        return json.load(resp)
