"""Kalshi Scanner — Cron Wrapper
Scans 600+ markets for macro opportunities and feeds results to master allocator.
Runs every 6 hours alongside whale_copier and dynamic_universe.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kalshi_scanner import scan_markets
import json
from datetime import datetime, timezone

MODELS = os.path.expanduser("~/.openclaw/workspace/models")

def run():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"KALSHI MARKET SCANNER ({ts})", ""]
    
    # Scan macro markets
    macro_top, macro_total, macro_passed = scan_markets(include_sports=False, top_n=5)
    lines.append(f"MACRO: {macro_total} scanned, {macro_passed} passed → {len(macro_top)} opportunities")
    for m in macro_top:
        lines.append(f"  [{m['edge_score']:>5.1f}] {m['title']}")
        lines.append(f"         {m['yes_bid']}c/{m['yes_ask']}c spread:{m['spread']}c | {m['ticker']}")
    
    # Scan sports markets (includes player props)
    sports_top, sports_total, sports_passed = scan_markets(include_sports=True, top_n=5)
    lines.append(f"\nSPORTS: {sports_total} scanned, {sports_passed} passed → {len(sports_top)} opportunities")
    for m in sports_top:
        lines.append(f"  [{m['edge_score']:>5.1f}] {m['title']}")
        lines.append(f"         {m['yes_bid']}c/{m['yes_ask']}c spread:{m['spread']}c | {m['ticker']}")
    
    # Save results for master allocator
    results = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "macro": macro_top,
        "sports": sports_top,
        "macro_total": macro_total,
        "sports_total": sports_total,
    }
    with open(os.path.join(MODELS, "scanner_results.json"), 'w') as f:
        json.dump(results, f, indent=2)
    
    report = "\n".join(lines)
    print(report)
    return report

if __name__ == "__main__":
    run()
