"""AccuWeather 10-day page. Research tags only. Never settlement.

The HAR was an HTML document. This parser accepts one embedded JSON island
(`script#accu-daily`). Anything else is `embedded_unstable` and yields no
number. No browser.
"""
import json
import re

from weather_sources._get import get_text

_ISLAND = re.compile(
    r'<script[^>]*id="accu-daily"[^>]*>(.*?)</script>', re.I | re.S)
SAMPLE_URL = (
    "https://www.accuweather.com/en/us/new-york/10021/10-day-weather-forecast/349727"
)


def parse(html, observe_date, raw_fixture_ref=None):
    m = _ISLAND.search(html or "")
    if not m:
        return {"ok": False, "reason": "embedded_unstable", "research_tag": None}
    try:
        rows = json.loads(m.group(1))
    except ValueError:
        return {"ok": False, "reason": "embedded_unstable", "research_tag": None}
    if not isinstance(rows, list):
        return {"ok": False, "reason": "embedded_unstable", "research_tag": None}
    hit = None
    for row in rows:
        if isinstance(row, dict) and str(row.get("date")) == str(observe_date):
            hit = row
            break
    if hit is None:
        return {"ok": False, "reason": "date_absent", "research_tag": None}
    hi, lo = hit.get("high"), hit.get("low")
    if hi is None or lo is None:
        return {"ok": False, "reason": "field_missing", "research_tag": None}
    return {
        "ok": True,
        "reason": None,
        "research_tag": {
            "source": "accuweather",
            "endpoint": SAMPLE_URL,
            "fetched_at": None,
            "valid_time": str(observe_date),
            "forecast_or_observation": "forecast",
            "value_summary": "high %s F low %s F" % (hi, lo),
            "provenance": raw_fixture_ref or "fixture",
        },
        "settlement_fv": None,
    }


def fetch(url=SAMPLE_URL, timeout=12):
    """Read-only GET. A page without the island does not invent a temperature."""
    try:
        body, client = get_text(url, timeout=timeout, accept="text/html")
    except Exception:
        return {"ok": False, "reason": "fetch_failed", "html": None, "client": None}
    return {"ok": True, "html": body, "client": client, "reason": None}
