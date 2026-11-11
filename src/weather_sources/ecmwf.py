"""ECMWF catalogue stub and the CPC 403 note. Research only. No guessed API.

CPC (https://www.cpc.ncep.noaa.gov/products/predictions/) returned 403 in the
2026-09-23 capture. This module does not request it.
"""

CPC_BLOCKED = {
    "source": "cpc",
    "http": 403,
    "client": None,
    "note": "403 in the capture. No client. Do not bypass.",
}


def research_tag(products=None):
    names = []
    for item in products or []:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return {
        "source": "ecmwf",
        "endpoint": "https://charts.ecmwf.int/",
        "fetched_at": None,
        "valid_time": None,
        "forecast_or_observation": "catalogue",
        "value_summary": None,
        "provenance": "no HAR product API; names only",
        "products": names,
    }
