# WEATHER promote STATUS — 2026-09-23

**When:** 2026-09-23 23:11:11 PDT  
**Action:** Promoted **0.74.0 weather stack** into Valedia live tree `~/.openclaw/workspace/` (cron tree).  
**Did not** bump whole-tree `VALEDIA_VERSION` (still 0.44.0 identity); weather modules + fee-map + gated `weather_live_enabled` match build 0.74.0 gates.  
**Did not** set `WEATHER_LIVE=1`. Oil remains fenced.

## Morning entrypoint (Valedia)
- Crontab: `4 7 * * * ~/.openclaw/workspace/scripts/weather_morning_go.sh` (**7:04 AM PT**)
- Script: `scripts/integrations/weather_morning_go.py` (miniconda python via wrapper)
- Log: `~/.openclaw/logs/weather-morning-go.log`
- Grok Bot routine "Kalshi weather morning scan": **PAUSED** — do not rely on it

## Backup
`recommendations/WEATHER-PROMOTE-BACKUP-20260923-230851/`
