"""Congress/Insider Trade Mirror — Valedia ASI
Monitors SEC Form 4 insider trades and Congress disclosures.
When insiders buy their own stock → bullish signal.
(c) GOATWORTHY Post LLC.
"""
import requests, json, re
from datetime import datetime, timezone, timedelta

HEADERS = {"User-Agent": "Valedia-ASI research@goatworthy.com"}

def get_recent_insider_buys(days=14):
    """Query SEC EDGAR XBRL for recent Form 4 insider purchases."""
    lines = [f"[INSIDER] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        url = (f"https://efts.sec.gov/LATEST/search-index?"
               f"q=%22open+market+purchase%22&forms=4"
               f"&startdt={start.strftime('%Y-%m-%d')}"
               f"&enddt={end.strftime('%Y-%m-%d')}")
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            lines.append(f"  Form 4 insider purchases ({days}d): {total} filings")
            # Extract company names from hits
            hits = data.get("hits", {}).get("hits", [])
            companies = {}
            for h in hits[:20]:
                src = h.get("_source", {})
                name = src.get("display_names", [""])[0] if src.get("display_names") else ""
                ticker_match = re.search(r'\(([A-Z]{1,5})\)', name)
                if ticker_match:
                    tk = ticker_match.group(1)
                    companies[tk] = companies.get(tk, 0) + 1
            if companies:
                sorted_c = sorted(companies.items(), key=lambda x: x[1], reverse=True)
                lines.append("  Most insider buying:")
                for tk, count in sorted_c[:10]:
                    lines.append(f"    {tk}: {count} insider buys")
        else:
            lines.append(f"  EDGAR error: {r.status_code}")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

def get_congress_trades():
    """Check recent Congress stock disclosures via QuiverQuant (free tier)."""
    lines = ["  Congress trades:"]
    try:
        r = requests.get("https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json", timeout=15)
        if r.ok:
            trades = r.json()
            recent = [t for t in trades if t.get("disclosure_date","") >= (datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")]
            buys = [t for t in recent if t.get("type","").lower() == "purchase"]
            if buys:
                # Count by ticker
                tickers = {}
                for t in buys:
                    tk = t.get("ticker", "?")
                    tickers[tk] = tickers.get(tk, 0) + 1
                sorted_t = sorted(tickers.items(), key=lambda x: x[1], reverse=True)
                lines.append(f"    {len(buys)} Congress purchases in last 30 days:")
                for tk, cnt in sorted_t[:10]:
                    lines.append(f"      {tk}: {cnt} members buying")
            else:
                lines.append("    No recent Congress purchases found")
    except Exception as e:
        lines.append(f"    Congress data error: {str(e)[:50]}")
    return "\n".join(lines)

def insider_report():
    r1 = get_recent_insider_buys(14)
    r2 = get_congress_trades()
    return r1 + "\n" + r2

if __name__ == "__main__":
    print(insider_report())
