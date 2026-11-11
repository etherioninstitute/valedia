"""NOAA Weather Arbitrage Engine v2 — Valedia ASI
═══════════════════════════════════════════════════════════════════════════
AUTO-DISCOVERS all Kalshi weather markets across ALL cities.
Fetches NOAA/NWS forecasts → compares to Kalshi bracket prices →
auto-trades when edge ≥ 12%. No hardcoded city list — finds everything.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, re, requests, time
from datetime import datetime, timezone, timedelta
from math import erf, sqrt
from fee_engine import kalshi_fee_per_contract  # canonical real Kalshi fee (single source)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _load_env():
    if str(os.environ.get("OPENCLAW_SKIP_ENV", "")).strip().lower() in ("1", "true", "yes", "on"):
        return
    env_path = os.path.expanduser("~/.hermes/secrets/trading.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()
from kalshi_auth import get, get_balance  # create_order removed — weather live killed

MIN_EDGE_PCT = 12
MAX_PER_TRADE = 10
KALSHI_FEE = 0.02
NOAA_HEADERS = {"User-Agent": "(Valedia ASI, contact.etherion@protonmail.com)", "Accept": "application/geo+json"}
CACHE_PATH = os.path.expanduser("~/.openclaw/workspace/models/weather_city_cache.json")

# Known NOAA grid points (grows automatically via auto-discovery)
KNOWN_GRIDS = {
    "NY":  {"name":"New York",  "lat":40.7128,"lon":-74.0060,"office":"OKX","gx":33,"gy":37},
    "CHI": {"name":"Chicago",   "lat":41.8781,"lon":-87.6298,"office":"LOT","gx":75,"gy":72},
    "MIA": {"name":"Miami",     "lat":25.7617,"lon":-80.1918,"office":"MFL","gx":110,"gy":50},
    "LA":  {"name":"Los Angeles","lat":34.0522,"lon":-118.2437,"office":"LOX","gx":154,"gy":44},
    "ATL": {"name":"Atlanta",   "lat":33.7490,"lon":-84.3880,"office":"FFC","gx":51,"gy":88},
    "DAL": {"name":"Dallas",    "lat":32.7767,"lon":-96.7970,"office":"FWD","gx":80,"gy":103},
    "SEA": {"name":"Seattle",   "lat":47.6062,"lon":-122.3321,"office":"SEW","gx":124,"gy":67},
    "DEN": {"name":"Denver",    "lat":39.7392,"lon":-104.9903,"office":"BOU","gx":63,"gy":62},
    "AUS": {"name":"Austin",    "lat":30.2672,"lon":-97.7431,"office":"EWX","gx":156,"gy":91},
    "HOU": {"name":"Houston",   "lat":29.7604,"lon":-95.3698},
    "PHX": {"name":"Phoenix",   "lat":33.4484,"lon":-112.0740},
    "SF":  {"name":"San Francisco","lat":37.7749,"lon":-122.4194},
    "BOS": {"name":"Boston",    "lat":42.3601,"lon":-71.0589},
    "DC":  {"name":"Washington DC","lat":38.9072,"lon":-77.0369},
}

# ═══ AUTO-DISCOVERY ═══

def discover_weather_series():
    """Auto-discover ALL active weather series on Kalshi. No hardcoded list."""
    found = {}
    prefixes = ['KXHIGH','KXLOW','KXRAIN','KXSNOW']
    # Try every combination — Kalshi uses 2-3 letter city codes
    city_codes = list(KNOWN_GRIDS.keys()) + [
        'MIN','DET','STL','PHL','CLE','PIT','ORL','TB','SAN','POR','LV',
        'SLC','CIN','IND','KC','MEM','NAS','JAX','SA','OKC','RAL','CLT',
        'MIL','ABQ','TUC','MSP','TPA','BAL','RNO','BUF','RIC','HAR',
    ]
    for prefix in prefixes:
        for code in city_codes:
            series = f'{prefix}{code}'
            try:
                r = get(f'/markets?limit=1&series_ticker={series}&status=open')
                mkts = r.get('markets',[])
                if mkts:
                    title = mkts[0].get('title','')
                    found[series] = {'code': code, 'type': prefix.replace('KX','').lower(), 'title': title[:60]}
            except: pass
    # Cache results
    try:
        with open(CACHE_PATH, 'w') as f:
            json.dump({"ts": datetime.now(timezone.utc).isoformat(), "series": found}, f, indent=2)
    except: pass
    return found

def load_cached_series():
    """Load cached series discovery (refreshes daily)."""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                cache = json.load(f)
            ts = cache.get("ts","")
            if ts[:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
                return cache.get("series", {})
        except: pass
    return None

def resolve_noaa_grid(city_code):
    """Auto-resolve NOAA grid point for a city. Caches result."""
    if city_code in KNOWN_GRIDS and "office" in KNOWN_GRIDS[city_code]:
        g = KNOWN_GRIDS[city_code]
        return g["office"], g["gx"], g["gy"]
    # Try lat/lon lookup
    if city_code in KNOWN_GRIDS:
        g = KNOWN_GRIDS[city_code]
        try:
            r = requests.get(f'https://api.weather.gov/points/{g["lat"]},{g["lon"]}', headers=NOAA_HEADERS, timeout=10)
            if r.ok:
                p = r.json().get("properties",{})
                KNOWN_GRIDS[city_code]["office"] = p["gridId"]
                KNOWN_GRIDS[city_code]["gx"] = p["gridX"]
                KNOWN_GRIDS[city_code]["gy"] = p["gridY"]
                return p["gridId"], p["gridX"], p["gridY"]
        except: pass
    return None, None, None

def fetch_noaa_forecast(city_code):
    """Fetch NOAA forecast. Returns list of {date, high, low}."""
    office, gx, gy = resolve_noaa_grid(city_code)
    if not office: return []
    try:
        url = f"https://api.weather.gov/gridpoints/{office}/{gx},{gy}/forecast"
        r = requests.get(url, headers=NOAA_HEADERS, timeout=15)
        if not r.ok: return []
        forecasts = []
        for p in r.json().get("properties",{}).get("periods",[]):
            if p.get("isDaytime", True):
                forecasts.append({"date": p.get("startTime","")[:10], "high": p.get("temperature"), "name": p.get("name","")})
        return forecasts
    except: return []

def parse_bracket(title):
    """Extract temp bracket from title. Returns (type, low, high)."""
    m = re.search(r'(\d+)-(\d+)°', title)
    if m: return ("range", int(m.group(1)), int(m.group(2)))
    m = re.search(r'>(\d+)°', title)
    if m: return ("above", int(m.group(1)), None)
    m = re.search(r'<(\d+)°', title)
    if m: return ("below", None, int(m.group(1)))
    return (None, None, None)

def estimate_prob(forecast_high, btype, blow, bhigh, sigma=3.0):
    """P(actual temp in bracket) using Gaussian ±σ uncertainty."""
    if forecast_high is None or btype is None: return 0.5
    if btype == "range":
        return max(0.01, min(0.99, 0.5*(erf((bhigh-forecast_high)/(sigma*sqrt(2))) - erf((blow-forecast_high)/(sigma*sqrt(2))))))
    elif btype == "above":
        return max(0.01, min(0.99, 0.5*(1-erf((blow-forecast_high)/(sigma*sqrt(2))))))
    elif btype == "below":
        return max(0.01, min(0.99, 0.5*(1+erf((bhigh-forecast_high)/(sigma*sqrt(2))))))
    return 0.5

# ═══ EDGE DETECTION ═══

def find_edges():
    """Auto-discover all weather series → fetch NOAA → find edges ≥ 12%."""
    # Load or discover series
    series_map = load_cached_series()
    if not series_map:
        print("  Discovering weather series (first run of day)...")
        series_map = discover_weather_series()
    
    print(f"  {len(series_map)} weather series active")
    edges = []
    
    for series_ticker, info in series_map.items():
        city_code = info["code"]
        forecasts = fetch_noaa_forecast(city_code)
        if not forecasts: continue
        
        try:
            mkts = get(f'/markets?limit=30&series_ticker={series_ticker}&status=open').get('markets',[])
        except: continue
        
        for m in mkts:
            title = m.get("title","")
            ticker = m.get("ticker","")
            ya = float(m.get("yes_ask_dollars","0") or "0")
            na = float(m.get("no_ask_dollars","0") or "0")
            exp = m.get("expected_expiration_time","")[:10]
            btype, blow, bhigh = parse_bracket(title)
            if btype is None: continue
            
            # Match forecast to market date
            fc_high = None
            for fc in forecasts:
                if fc["date"] == exp: fc_high = fc["high"]; break
            if fc_high is None and forecasts: fc_high = forecasts[0]["high"]
            if fc_high is None: continue
            
            noaa_prob = estimate_prob(fc_high, btype, blow, bhigh)
            
            # YES edge
            if ya > 0.01 and ya < 0.95:
                edge = (noaa_prob - ya) / ya * 100
                if edge >= MIN_EDGE_PCT:
                    edges.append({"city": city_code, "ticker": ticker, "title": title[:55],
                        "side":"yes", "price":ya, "noaa_prob":round(noaa_prob,3),
                        "market_prob":round(ya,3), "edge_pct":round(edge,1),
                        "forecast_high":fc_high, "bracket":f"{btype}({blow},{bhigh})",
                        "profit_per":round(1-ya-kalshi_fee_per_contract(ya),3), "exp":exp, "series":series_ticker})
            
            # NO edge
            if na > 0.01 and na < 0.95:
                noaa_no = 1.0 - noaa_prob
                mkt_no = 1.0 - ya if ya > 0 else na
                edge = (noaa_no - mkt_no) / (mkt_no+0.01) * 100
                if edge >= MIN_EDGE_PCT:
                    edges.append({"city": city_code, "ticker": ticker, "title": title[:55],
                        "side":"no", "price":na, "noaa_prob":round(noaa_no,3),
                        "market_prob":round(mkt_no,3), "edge_pct":round(edge,1),
                        "forecast_high":fc_high, "bracket":f"{btype}({blow},{bhigh})",
                        "profit_per":round(1-na-kalshi_fee_per_contract(na),3), "exp":exp, "series":series_ticker})
    
    edges.sort(key=lambda x: x["edge_pct"], reverse=True)
    return edges

# ═══ EXECUTION ═══

def execute_weather_trades(edges, dry_run=True, max_trades=5):
    """WEATHER_ARB live exec fenced (Slice 8). Never places orders.

    Learning pause/TTL (Slice 9) may re-enable evaluation/sizing elsewhere.
    This function still never calls create_order. dry_run is forced True.
    """
    dry_run = True
    results = []
    for edge in (edges or [])[:max_trades]:
        cost_per = float(edge.get("price") or 0) or 1.0
        n = min(int(MAX_PER_TRADE / cost_per), 50)
        if n < 1:
            continue
        total_cost = n * cost_per
        profit = n * float(edge.get("profit_per") or 0)
        r = {"ticker": edge.get("ticker"), "city": edge.get("city"),
             "side": edge.get("side"), "contracts": n,
             "cost": round(total_cost, 2), "profit": round(profit, 2),
             "edge": edge.get("edge_pct"),
             "noaa_fc": edge.get("forecast_high"),
             "bracket": edge.get("bracket"),
             "status": "DRY_RUN",
             "reason": "WEATHER_ARB_KILLED"}
        results.append(r)
    return results

# ═══ MAIN ═══

def run(dry_run=True, max_trades=5):
    dry_run = True  # Slice 8: live weather is dead
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🌡️ WEATHER ARB v2 ({ts}) {'[DRY]' if dry_run else '[LIVE]'}", ""]
    edges = find_edges()
    
    # Group by city for reporting
    cities_seen = set(e["city"] for e in edges)
    lines.append(f"Scanned {len(cities_seen)} cities, found {len(edges)} edges ≥{MIN_EDGE_PCT}%")
    
    if not edges:
        lines.append("No weather edges this cycle.")
        report = "\n".join(lines)
        print(report)
        return report
    
    for e in edges[:10]:
        lines.append(f"  {e['city']:3} {e['side']:3} @${e['price']:.2f} NOAA:{e['noaa_prob']:.0%} mkt:{e['market_prob']:.0%} edge:{e['edge_pct']:+.0f}% fc:{e['forecast_high']}°F | {e['title'][:40]}")
    
    results = execute_weather_trades(edges, dry_run=dry_run, max_trades=max_trades)
    lines.append("")
    for r in results:
        icon = "✅" if r["status"]=="EXECUTED" else "🔶" if r["status"]=="DRY_RUN" else "❌"
        lines.append(f"  {icon} {r['city']} {r['side']} x{r['contracts']} ${r['cost']:.2f}→+${r['profit']:.2f} edge:{r['edge']:.0f}%")
        if r.get("error"): lines.append(f"    ERR: {r['error']}")
    
    report = "\n".join(lines)
    print(report)
    return report

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["scan","discover","dry-run","execute"], default="dry-run")
    p.add_argument("--max-trades", type=int, default=5)
    args = p.parse_args()
    if args.mode == "discover":
        s = discover_weather_series()
        for k,v in s.items(): print(f"  {k:15} | {v['title'][:55]}")
        print(f"\n{len(s)} series found")
    elif args.mode == "scan":
        for e in find_edges()[:15]:
            print(f"  {e['city']:3} {e['side']:3} @${e['price']:.2f} NOAA:{e['noaa_prob']:.0%} edge:{e['edge_pct']:+.0f}% fc:{e['forecast_high']}°F | {e['title'][:45]}")
    elif args.mode == "dry-run":
        run(dry_run=True, max_trades=args.max_trades)
    elif args.mode == "execute":
        run(dry_run=True, max_trades=args.max_trades)  # live disabled
