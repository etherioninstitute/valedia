"""Injury Checker — Valedia ASI
Checks injury reports before sports bets are placed.
Uses ESPN/CBS Sports public injury pages.
Falls back gracefully if scraping fails.

(c) GOATWORTHY Post LLC
"""
import requests
import json
import os
import re
from datetime import datetime, timezone

INJURY_CACHE = os.path.expanduser("~/.openclaw/workspace/models/injury_cache.json")

# NBA team name → ESPN team slug
NBA_TEAMS = {
    'ATL': 'atlanta-hawks', 'BOS': 'boston-celtics', 'BKN': 'brooklyn-nets',
    'CHA': 'charlotte-hornets', 'CHI': 'chicago-bulls', 'CLE': 'cleveland-cavaliers',
    'DAL': 'dallas-mavericks', 'DEN': 'denver-nuggets', 'DET': 'detroit-pistons',
    'GSW': 'golden-state-warriors', 'HOU': 'houston-rockets', 'IND': 'indiana-pacers',
    'LAC': 'la-clippers', 'LAL': 'los-angeles-lakers', 'MEM': 'memphis-grizzlies',
    'MIA': 'miami-heat', 'MIL': 'milwaukee-bucks', 'MIN': 'minnesota-timberwolves',
    'NOP': 'new-orleans-pelicans', 'NYK': 'new-york-knicks', 'OKC': 'oklahoma-city-thunder',
    'ORL': 'orlando-magic', 'PHI': 'philadelphia-76ers', 'PHX': 'phoenix-suns',
    'POR': 'portland-trail-blazers', 'SAC': 'sacramento-kings', 'SAS': 'san-antonio-spurs',
    'TOR': 'toronto-raptors', 'UTA': 'utah-jazz', 'WAS': 'washington-wizards',
}

def load_cache():
    try:
        with open(INJURY_CACHE) as f: return json.load(f)
    except: return {"teams": {}, "updated": ""}

def save_cache(cache):
    cache["updated"] = datetime.now(timezone.utc).isoformat()
    with open(INJURY_CACHE, 'w') as f: json.dump(cache, f, indent=2)

def fetch_injuries_espn(team_abbrev, league='nba'):
    """Fetch injury info from ESPN's public API (no scraping needed).
    ESPN has a public injuries endpoint that returns JSON."""
    try:
        # ESPN public API for injuries
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/teams"
        r = requests.get(url, timeout=5)
        if not r.ok:
            return []
        
        teams = r.json().get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
        for t in teams:
            team = t.get('team', {})
            abbrev = team.get('abbreviation', '')
            if abbrev.upper() == team_abbrev.upper():
                # Found the team - get injuries from team detail
                team_id = team.get('id', '')
                if team_id:
                    inj_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/teams/{team_id}/injuries"
                    r2 = requests.get(inj_url, timeout=5)
                    if r2.ok:
                        items = r2.json().get('items', [])
                        injuries = []
                        for item in items:
                            athlete = item.get('athlete', {})
                            status = item.get('status', '')
                            desc = item.get('longComment', item.get('shortComment', ''))
                            injuries.append({
                                'player': athlete.get('displayName', '?'),
                                'position': athlete.get('position', {}).get('abbreviation', '?'),
                                'status': status,
                                'detail': desc[:100],
                            })
                        return injuries
        return []
    except Exception as e:
        return []

def check_team_injuries(team_abbrev, league='nba'):
    """Check if a team has significant injuries.
    Returns: (risk_level, details)
    risk_level: 'OK' | 'CAUTION' | 'DANGER'
    """
    cache = load_cache()
    cache_key = f"{league}_{team_abbrev}"
    
    # Use cache if less than 2 hours old
    cached = cache.get("teams", {}).get(cache_key, {})
    if cached.get("ts"):
        age_sec = (datetime.now(timezone.utc) - 
                   datetime.fromisoformat(cached["ts"])).total_seconds()
        if age_sec < 7200:
            return cached.get("risk", "OK"), cached.get("injuries", [])
    
    injuries = fetch_injuries_espn(team_abbrev, league)
    
    # Classify risk
    out_count = sum(1 for i in injuries if 'out' in i.get('status', '').lower())
    star_out = any(i.get('status', '').lower() == 'out' for i in injuries 
                   if i.get('position', '') in ('PG', 'SG', 'SF', 'PF', 'C'))
    
    if out_count >= 3 or star_out:
        risk = 'DANGER'
    elif out_count >= 1:
        risk = 'CAUTION'
    else:
        risk = 'OK'
    
    # Update cache
    cache.setdefault("teams", {})[cache_key] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "risk": risk,
        "injuries": injuries,
        "out_count": out_count,
    }
    save_cache(cache)
    
    return risk, injuries


def get_injury_edge_adjustment(team_abbrev, league='nba'):
    """Returns an edge score adjustment based on injuries.
    Positive = our favorite is healthy (good).
    Negative = our favorite has injuries (bad)."""
    risk, injuries = check_team_injuries(team_abbrev, league)
    if risk == 'DANGER':
        return -20  # Major injuries — reduce or skip
    elif risk == 'CAUTION':
        return -5   # Some injuries — slight reduction
    else:
        return 0    # Healthy — no adjustment


if __name__ == "__main__":
    import sys
    team = sys.argv[1] if len(sys.argv) > 1 else 'DEN'
    league = sys.argv[2] if len(sys.argv) > 2 else 'nba'
    risk, injuries = check_team_injuries(team, league)
    print(f"{team} ({league}): {risk}")
    for i in injuries:
        print(f"  {i['player']} ({i['position']}): {i['status']} — {i['detail'][:60]}")
