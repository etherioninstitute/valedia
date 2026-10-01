# GROK-CHARTER — Stop Hermes macOS “python App Data” prompts

**Date:** 2026-09-30 ~13:56 PT  
**Executor:** Grok Build via `~/bin/grok-valedia`  
**Worktree:** `~/.openclaw/workspace-v2`  
**Stamp out:** `recommendations/HERMES-TCC-APPDATA-FIX-2026-09-30.md`  
**GROK_EXIT required**

## Problem (FACT)

Burton gets repeated macOS dialogs: **“python” would like to access data from other apps** (`kTCCServiceSystemPolicyAppData`).

TCC attribution (2026-09-30 ~13:50 PT):
- Responsible: `com.nousresearch.hermes.managed-python`
- Path: `/Users/burtonchatelain/.hermes/hermes-agent/venv/bin/python`
- Child: `/usr/bin/find` walking protected app data (Containers / other apps)
- Seen under **valedia-cto** gateway (LaunchAgent `ai.hermes.gateway-valedia-cto`, pid pattern 73539)

Burton already granted Full Disk Access to Terminal, Hermes.app, and Python.app — **that does not stop this**. The LaunchAgent runs the **adhoc-signed venv python by path**, which has a separate App Data identity. Hermes.app bundle AppData allow ≠ venv path until Allow is clicked; overnight heartbeats re-trigger when agents run broad `find`.

Today’s Allow on the venv path did stick (`AppData` auth_value=5 @ 13:50:58). Recurrence risk: Hermes updates rewriting the binary, or other python shims (Xcode `/usr/bin/python3`), or new `find` into Containers.

## Goal

Stop the prompts at the **source**: Hermes must not run unbounded `find` / file search into `~/Library/Containers`, `~/Library/Group Containers`, or other apps’ Application Support. Prefer config/profile fences + docs in Valedia recommendations; only patch Hermes agent code if a safe, local, reversible change is clearly required and documented.

## Constraints / fences

- NO `--live`, NO venue orders, NO OKX unlock
- NO LMS load/unload / Ornith eviction
- NO `hermes gateway --replace` on life Matrix/Signal
- NO touch root MoA / life Matrix
- Trading profiles may be edited; CTO profile may be edited for terminal/search fences
- Backup any yaml before edit (`*.bak-20260930-tcc-appdata`)
- Bounce only LaunchAgents you must (prefer kickstart trading/CTO if config changes require it) — not full stack restart
- Do not grant TCC via SIP-breaking hacks; explain Allow-once if still needed after fence

## Deliverables

1. Root-cause writeup in the stamp (which tool: `terminal` / `search_files` / cron / MemPalace / etc.)
2. Concrete fence applied, e.g. one or more of:
   - Tighten `approvals.command_allowlist` / deny `find` into Containers
   - Restrict `terminal.home_mode` / cwd so agents stay in HERMES_HOME / workspace
   - Disable or bound `search_files` / broad find on CTO + trading profiles
   - SOUL / profile tip: never `find ~` or into Library/Containers
3. Proof: what would have triggered the dialog no longer runs (test or log argument); list files changed
4. Stamp ends with **GROK_EXIT:0** or **GROK_EXIT:1** with blockers

## Context paths

- CTO plist: `~/Library/LaunchAgents/ai.hermes.gateway-valedia-cto.plist`
- CTO config: `~/.hermes/profiles/valedia-cto/config.yaml`
- Trading: `~/.hermes/profiles/valedia-trading-{scout,decider,auditor}/config.yaml`
- Managed python: `~/.hermes/hermes-agent/venv/bin/python` (identifier `com.nousresearch.hermes.managed-python`)
