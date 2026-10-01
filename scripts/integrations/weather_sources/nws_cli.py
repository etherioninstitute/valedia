"""NWS CLI daily climate report. Settlement primary. httpx when installed.

Explicit station config only. NYC Kalshi city NY is OKX/NYC (Central Park).
A wrong date or a PRELIMINARY page returns no value.
"""
import re
from html import unescape

from weather_sources._get import get_text

# Rows confirmed against forecast.weather.gov CLI pages (not inferred from the name).
# MIA = MFL/MIA (CLIMIA). LAS = VEF/LAS (CLILAS). Kalshi Vegas low is TLV.
STATIONS = {
    "NYC": {
        "wfo": "OKX", "issuedby": "NYC", "name": "Central Park NY",
        "phrase": "CENTRAL PARK", "tz": "America/New_York", "cli_product": "CLINYC",
    },
    "MIA": {
        "wfo": "MFL", "issuedby": "MIA", "name": "Miami FL",
        "phrase": "MIAMI", "tz": "America/New_York", "cli_product": "CLIMIA",
    },
    "LAS": {
        "wfo": "VEF", "issuedby": "LAS", "name": "Las Vegas NV",
        "phrase": "LAS VEGAS", "tz": "America/Los_Angeles", "cli_product": "CLILAS",
    },
    "AUS": {
        "wfo": "EWX", "issuedby": "AUS", "name": "Austin TX",
        "phrase": "AUSTIN", "tz": "America/Chicago", "cli_product": "CLIAUS",
    },
    "MDW": {
        "wfo": "LOT", "issuedby": "MDW", "name": "Chicago Midway IL",
        "phrase": "CHICAGO-MIDWAY", "tz": "America/Chicago", "cli_product": "CLIMDW",
    },
    "DEN": {
        "wfo": "BOU", "issuedby": "DEN", "name": "Denver CO",
        "phrase": "DENVER", "tz": "America/Denver", "cli_product": "CLIDEN",
    },
    "LAX": {
        "wfo": "LOX", "issuedby": "LAX", "name": "Los Angeles CA",
        "phrase": "LOS ANGELES", "tz": "America/Los_Angeles", "cli_product": "CLILAX",
    },
    "PHL": {
        "wfo": "PHI", "issuedby": "PHL", "name": "Philadelphia PA",
        "phrase": "PHILADELPHIA", "tz": "America/New_York", "cli_product": "CLIPHL",
    },
    "ATL": {
        "wfo": "FFC", "issuedby": "ATL", "name": "Atlanta GA",
        "phrase": "ATLANTA", "tz": "America/New_York", "cli_product": "CLIATL",
    },
    "BOS": {
        "wfo": "BOX", "issuedby": "BOS", "name": "Boston MA",
        "phrase": "BOSTON", "tz": "America/New_York", "cli_product": "CLIBOS",
    },
    "DFW": {
        "wfo": "FWD", "issuedby": "DFW", "name": "Dallas Fort Worth TX",
        "phrase": "DALLAS/FORT WORTH", "tz": "America/Chicago", "cli_product": "CLIDFW",
    },
    "DCA": {
        "wfo": "LWX", "issuedby": "DCA", "name": "Washington DC",
        "phrase": "WASHINGTON", "tz": "America/New_York", "cli_product": "CLIDCA",
    },
    "MSY": {
        "wfo": "LIX", "issuedby": "MSY", "name": "New Orleans LA",
        "phrase": "NEW ORLEANS", "tz": "America/Chicago", "cli_product": "CLIMSY",
    },
    "PHX": {
        "wfo": "PSR", "issuedby": "PHX", "name": "Phoenix AZ",
        "phrase": "PHOENIX", "tz": "America/Phoenix", "cli_product": "CLIPHX",
    },
    "SEA": {
        "wfo": "SEW", "issuedby": "SEA", "name": "Seattle WA",
        "phrase": "SEATTLE", "tz": "America/Los_Angeles", "cli_product": "CLISEA",
    },
    "HOU": {
        "wfo": "HGX", "issuedby": "HOU", "name": "Houston TX",
        "phrase": "HOUSTON", "tz": "America/Chicago", "cli_product": "CLIHOU",
    },
}
KALSHI_CITY = {
    "NY": "NYC", "NYC": "NYC",
    "MIA": "MIA", "MIAMI": "MIA",
    "LAS": "LAS", "TLV": "LAS", "VEGAS": "LAS",
    "AUS": "AUS", "AUSTIN": "AUS",
    "CHI": "MDW", "MDW": "MDW", "CHICAGO": "MDW",
    "DEN": "DEN", "DENVER": "DEN",
    "LAX": "LAX", "LOSANGELES": "LAX",
    "PHIL": "PHL", "PHL": "PHL", "PHILADELPHIA": "PHL",
    "ATL": "ATL", "TATL": "ATL", "ATLANTA": "ATL",
    "BOS": "BOS", "TBOS": "BOS", "BOSTON": "BOS",
    "DAL": "DFW", "TDAL": "DFW", "DFW": "DFW", "DALLAS": "DFW",
    "DC": "DCA", "TDC": "DCA", "DCA": "DCA", "WASHINGTON": "DCA",
    "NOLA": "MSY", "TNOLA": "MSY", "MSY": "MSY", "NEWORLEANS": "MSY",
    "PHX": "PHX", "TPHX": "PHX", "PHOENIX": "PHX",
    "SEA": "SEA", "TSEA": "SEA", "SEATTLE": "SEA",
    "HOU": "HOU", "THOU": "HOU", "HOUSTON": "HOU",
}

_PRE = re.compile(
    r'<pre[^>]*class="[^"]*glossaryProduct[^"]*"[^>]*>(.*?)</pre>',
    re.I | re.S,
)
_SUMMARY = re.compile(
    r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})", re.I)
_ISSUE = re.compile(
    r"(\d{3,4}\s+(?:AM|PM)\s+[A-Z]{2,4}\s+\w+\s+[A-Z]{3}\s+\d{1,2}\s+\d{4})")
_MON = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11,
    "DECEMBER": 12, "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_LABEL = {
    "maximum": "MAXIMUM",
    "minimum": "MINIMUM",
    "precip_today": "TODAY",
}
_UNIT = {"maximum": "F", "minimum": "F", "precip_today": "in"}


def product_url(wfo, issuedby, version=1):
    return (
        "https://forecast.weather.gov/product.php?site=%s&issuedby=%s"
        "&product=CLI&format=CI&version=%s&glossary=0"
        % (wfo, issuedby, int(version))
    )


def station_for(city=None, wfo=None, issuedby=None):
    """Return a station dict, or None. A bare city name is not a WFO."""
    if wfo and issuedby:
        wfo, issuedby = str(wfo).upper(), str(issuedby).upper()
        for key, row in STATIONS.items():
            if row["wfo"] == wfo and row["issuedby"] == issuedby:
                out = dict(row)
                out["key"] = key
                return out
        return {"wfo": wfo, "issuedby": issuedby, "key": "%s/%s" % (wfo, issuedby),
                "phrase": None, "tz": None, "name": None}
    token = str(city or "").upper().replace(" ", "")
    key = KALSHI_CITY.get(token)
    if not key:
        return None
    out = dict(STATIONS[key])
    out["key"] = key
    return out


def fetch(wfo, issuedby, version=1, timeout=12):
    """Read-only GET of one CLI page. Does not place orders."""
    url = product_url(wfo, issuedby, version)
    body, client = get_text(url, timeout=timeout, accept="text/html")
    return {"html": body, "source_url": url, "client": client}


def extract_pre(html):
    m = _PRE.search(html or "")
    if not m:
        return None
    return unescape(m.group(1))


def _iso(month, day, year):
    mon = _MON.get(str(month).upper())
    if not mon:
        return None
    try:
        return "%04d-%02d-%02d" % (int(year), mon, int(day))
    except ValueError:
        return None


def _section(text, start, ends):
    up = text.upper()
    i = up.find(start.upper())
    if i < 0:
        return ""
    rest = text[i + len(start):]
    rup = rest.upper()
    cut = len(rest)
    for lab in ends:
        j = rup.find(lab.upper())
        if j >= 0:
            cut = min(cut, j)
    return rest[:cut]


def _today_lines(section):
    """Values for the climate-summary day. Morning products put them under YESTERDAY."""
    lines = section.splitlines()
    for label in ("TODAY", "YESTERDAY"):
        start = None
        for i, line in enumerate(lines):
            if line.strip().upper() == label:
                start = i + 1
                break
        if start is None:
            continue
        kept = []
        for line in lines[start:]:
            if line.strip().upper() in ("TODAY", "YESTERDAY", "TOMORROW"):
                break
            kept.append(line)
        if kept:
            return "\n".join(kept)
    return ""


def _number(block, label):
    rx = re.compile(
        r"^\s*" + label + r"\s+(-?\d+(?:\.\d+)?|MM|T)\b", re.I | re.M)
    m = rx.search(block or "")
    if not m:
        return None, "field_missing"
    tok = m.group(1).upper()
    if tok == "MM":
        return None, "missing_data"
    if tok == "T":
        return None, "trace"
    num = float(tok)
    if num.is_integer():
        num = int(num)
    return num, "ok"


def parse(html, observe_date, metric, station, source_url="", raw_fixture_ref=None):
    """Parse one glossaryProduct. Wrong date and PRELIMINARY return no value."""
    text = extract_pre(html)
    if text is None:
        return {"ok": False, "reason": "no_glossary"}
    if re.search(r"\bPRELIMINARY\b", text, re.I):
        return {"ok": False, "reason": "preliminary"}
    found = _SUMMARY.search(text)
    report_date = _iso(found.group(1), found.group(2), found.group(3)) if found else None
    if not report_date:
        return {"ok": False, "reason": "report_date_missing"}
    if not observe_date:
        return {"ok": False, "reason": "observe_date_missing"}
    if str(observe_date) != report_date:
        return {"ok": False, "reason": "wrong_date"}
    metric = str(metric or "")
    if metric not in _LABEL:
        return {"ok": False, "reason": "metric_unresolved"}
    phrase = (station or {}).get("phrase")
    cli_tok = (station or {}).get("cli_product")
    if phrase and phrase.upper() not in text.upper():
        if not (cli_tok and str(cli_tok).upper() in text.upper()):
            return {"ok": False, "reason": "station_mismatch"}
    if metric == "precip_today":
        block = _section(text, "PRECIPITATION (IN)", ("SNOWFALL", "DEGREE DAYS", "WIND"))
        value, why = _number(block, "TODAY")
        if why != "ok":
            value, why = _number(block, "YESTERDAY")
    else:
        block = _today_lines(_section(text, "TEMPERATURE (F)", ("PRECIPITATION",)))
        if not block:
            return {"ok": False, "reason": "no_today"}
        value, why = _number(block, _LABEL[metric])
    if why != "ok":
        return {"ok": False, "reason": why}
    issued = _ISSUE.search(text)
    wfo = (station or {}).get("wfo")
    issuedby = (station or {}).get("issuedby")
    ident = "%s/%s" % (wfo, issuedby) if wfo and issuedby else (station or {}).get("key")
    return {
        "ok": True,
        "reason": None,
        "settlement_fv": {
            "value": value,
            "unit": _UNIT[metric],
            "metric": metric,
            "observe_date": report_date,
            "observed_at": issued.group(1) if issued else None,
            "source": "nws_cli",
            "source_url": source_url or product_url(wfo or "", issuedby or ""),
            "station_or_geocode": ident,
            "confidence": "final",
            "raw_fixture_ref": raw_fixture_ref,
        },
    }
