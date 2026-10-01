# VALEDIA-SELF-KNOWLEDGE — know herself + smarter every day

**Burton 2026-09-23:** Smarter every day, max profit every day; Valedia must know herself through and through (codebase-memory?).

## Is CBM updated every Valedia iteration?

**No — not automatically.**

| Surface | State (2026-09-23 ~19:16 PT) |
|---------|------------------------------|
| **Grok Build CBM** (Mac stdio `~/.openclaw/tools/cbm/codebase-memory-mcp`) | Project `Users-burtonchatelain-.openclaw-workspace-v2` existed as of CBM-SMOKE **2026-09-18** (~41k nodes). Tonight’s weather_sources / weather_lookup (Build **0.73.0**) are **newer than that smoke** — index may miss them until reindex. GROK-BUILD-LANE requires CBM use but **hooks do not auto-inject**; reindex is agent discipline / optional `auto_index`, not a hard Done gate today. |
| **Grok Bot / Valedia Trader CBM** (box MCP `user-codebase-memory`) | **`list_projects` = empty.** Mac tree is not on the box FS, so Trader cannot graph-query Valedia via this connector until we index a synced copy or point MCP at Mac. |
| **Cron** | No `index_repository` / CBM refresh in crontab. |

## Fix (self-knowledge loop)

1. **Every Grok Build Done gate:** `index_repository` on `~/.openclaw/workspace-v2` (fast or moderate) + `check_index_coverage` on files this slice touched. Fail Done if coverage gaps on those paths. Write into GROK-BUILD-LANE + each charter.
2. **After WEATHER-JEV-MORNING-1 exits:** force reindex so TWC/Jev/preflight symbols are queryable.
3. **Trader visibility (optional follow-on):** sync or MCP-bridge so Valedia Trader’s CBM is not empty — or Trader always asks Build/status files when graph needed.
4. **Daily smarter (max profit under gates):** 7:04 AM weather first-drop Scout → INFORMED fee-true → FTL/shadow accuracy rollups tighten thresholds → alert Burton on broken feeds. Paper desks deprioritized (thin bankroll).
5. **Trading council pack** remains for high-stakes lane/rule calls only (`recommendations/trading-council/`).

## Status
Proposal + facts. Reindex not run this minute (Build WEATHER-JEV-MORNING-1 still Active). Next exit → reindex as Done gate.
