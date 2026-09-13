"""FRED macro-data feed (St. Louis Fed) — a data source Valedia lacked.

Adapted from kalshi-agent's data_fetcher FRED slice (NWS/ESPN dropped — Valedia's
weather_arb / sports_engine / injury_checker already cover those). Pure data feed:
no money math, so nothing to route through fee_engine/kelly. Feeds macro context text
into intel_layer / unified_brain prompts for Fed/CPI/jobs/energy markets.

Fail-safe: no FRED_API_KEY (or any error) -> "" / {} ; never raises.
Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html and set FRED_API_KEY.
(c) GOATWORTHY Post LLC.
"""
import os, time, requests

# FRED series ids (from kalshi-agent config.py FRED_SERIES).
FRED_SERIES = {
    "fed_funds": "DFF", "cpi": "CPIAUCSL", "core_cpi": "CPILFESL",
    "unemployment": "UNRATE", "nonfarm": "PAYEMS", "jobless_claims": "ICSA",
    "gdp": "GDP", "gas_price": "GASREGW", "treasury_10y": "DGS10",
    "treasury_2y": "DGS2", "sp500": "SP500",
}
CATEGORY_SERIES = {
    "fed_rates": ["fed_funds", "treasury_2y", "treasury_10y"],
    "inflation": ["cpi", "core_cpi"],
    "employment": ["unemployment", "nonfarm", "jobless_claims"],
    "gdp_growth": ["gdp"], "energy": ["gas_price"],
    "markets": ["treasury_10y", "treasury_2y"],
}

_CACHE = {}          # series_name -> (fetched_at, {"value","date","series"})
_CACHE_TTL = 600     # 10 min

def _env_key():
    key = os.environ.get("FRED_API_KEY", "")
    if not key:  # Valedia keeps secrets in ~/.openclaw/.env
        try:
            for line in open(os.path.expanduser("~/.openclaw/.env")):
                line = line.strip()
                if line.startswith("FRED_API_KEY=") and "=" in line:
                    key = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
    return key


def fetch_fred(series_name):
    """Latest observation for a series name -> {"value","date","series"} or None."""
    now = time.time()
    hit = _CACHE.get(series_name)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    sid = FRED_SERIES.get(series_name)
    key = _env_key()
    if not sid or not key:
        return None
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": sid, "api_key": key, "file_type": "json",
                                 "sort_order": "desc", "limit": "3"}, timeout=10)
        if r.status_code != 200:
            return None
        for o in r.json().get("observations", []):
            if o.get("value", ".") != ".":
                result = {"value": o["value"], "date": o["date"], "series": sid}
                _CACHE[series_name] = (now, result)
                return result
    except Exception:
        pass
    return None


def get_fred_for_category(category):
    """Macro context string for a market category (e.g. 'fed_rates') or "" if unavailable."""
    lines = []
    for key in CATEGORY_SERIES.get(category, []):
        d = fetch_fred(key)
        if d:
            lines.append(f"{key}: {d['value']} (as of {d['date']})")
    return " | ".join(lines)


if __name__ == "__main__":
    # Self-check: must fail safe with no key (never raise), and shape must be right with one.
    if not _env_key():
        assert fetch_fred("fed_funds") is None
        assert get_fred_for_category("fed_rates") == ""
        print("fred_macro self-check: PASS (no key -> fail-safe empty)")
    else:
        d = fetch_fred("fed_funds")
        assert d is None or {"value", "date", "series"} <= set(d), d
        print("fred_macro self-check: PASS", get_fred_for_category("fed_rates") or "(no data)")
