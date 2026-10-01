# JEV-ON proof — 2026-09-30 ~00:47 PT (Mac Studio M1)

## Goal
Make Valedia/Hermes/scans see `VALEDIA_JEV=1` from `~/.openclaw/.env` without a manual `export` every time. Paper/ops only.

## What was wrong
`~/.openclaw/.env` already had `VALEDIA_JEV=1` (MAX-ENV-FLIP), but:
- `valedia_config.jev_enabled()` / `_env_flag` only read `os.environ`
- LaunchAgents did not set `VALEDIA_JEV`
- Bare `python3 .../all_knowing_movers_scan.py` therefore stamped `jev.reason=VALEDIA_JEV_off`
- Cron was already fine: `cron_runner_mac.sh` sources the same `.env`

`jev_shadow._load_openclaw_env()` only ran inside `api_key()`, **after** `jev_on()` had already decided off.

## Durable wire (prefer this)
**One Python wire:** `valedia_config.ensure_openclaw_env()` loads `~/.openclaw/.env` via `os.environ.setdefault` (process env wins). Called from `_env_flag` / `_src`, so every flag consumer — including `jev_enabled()` and movers scan — sees Burton's file flips.

- Path default: `~/.openclaw/.env`
- Override: `OPENCLAW_ENV_FILE`
- Unit-test opt-out: `OPENCLAW_SKIP_ENV=1` (git constant stays `VALEDIA_JEV = False`)
- Same file cron already sources; no second source of truth

**Belt for Hermes:** `VALEDIA_JEV=1` added under `EnvironmentVariables` on:
- `~/Library/LaunchAgents/ai.hermes.gateway.plist`
- `~/Library/LaunchAgents/ai.hermes.gateway-valedia-*.plist` (cto, trading-auditor, trading-decider, trading-scout)

Backups: `*.bak-pre-jev-on-20260930`. **No `launchctl kickstart` this turn** — running gateway PIDs keep prior env until next restart; Valedia Python paths are live immediately via `ensure_openclaw_env`.

## Paths changed
| Path | Change |
|------|--------|
| `~/.openclaw/workspace-v2/scripts/integrations/valedia_config.py` | `ensure_openclaw_env`; `_env_flag`/`_src` call it; `jev_enabled` docstring |
| `~/.openclaw/workspace-v2/scripts/integrations/jev_shadow.py` | `_load_openclaw_env` delegates to `ensure_openclaw_env` |
| `~/.openclaw/workspace-v2/scripts/integrations/test_*.py` (many) | `OPENCLAW_SKIP_ENV=1` before imports so unit tests keep git-default OFF |
| `~/Library/LaunchAgents/ai.hermes.gateway*.plist` | `EnvironmentVariables.VALEDIA_JEV=1` |
| `~/.openclaw/.env` | unchanged (`VALEDIA_JEV=1` already present) |

## Proof command
```bash
env -u OPENCLAW_SKIP_ENV -u VALEDIA_JEV \
  python3 ~/.openclaw/workspace-v2/scripts/integrations/all_knowing_movers_scan.py \
  --fixture ~/.openclaw/workspace-v2/data/all-knowing/fixture_movers.json \
  --out ~/.openclaw/workspace-v2/data/all-knowing/movers_scan_jev_on_proof_2026-09-30.json \
  --watch ~/.openclaw/workspace-v2/data/all-knowing/paper_watch_jev_on_proof_2026-09-30.json
```

## Proof result (fixture, real TypeSafe annotate)
- `jev.enabled=true`, `fail_open=false`, `model=jev-1.13.0`
- `seasoning_only=true`, `changes_rank=false` (rank untouched)
- Artifact: `~/.openclaw/workspace-v2/data/all-knowing/movers_scan_jev_on_proof_2026-09-30.json`
- Fresh import (no export): `jev_enabled()=True`, controls row `VALEDIA_JEV` source=`env`
- Units: `test_jev_shadow.py` PASS; `test_all_knowing_movers_scan.py` PASS

## Fences held
- no `--live`, no venue orders, no LMS unload
- `CRYPTO_PAPER_SAFETY` still `PAPER/SHADOW+ORNITH` (not disabled)
- oil `NO_HARVEST`; weather `WATCH`; `create_order=false`; `live=false`
- git constant `VALEDIA_JEV = False` unchanged (flip stays env / `.env` only)

## Is Jev on by default for movers scan now?
**Yes** on this machine: any process that imports `valedia_config` (including `all_knowing_movers_scan.py`) loads `~/.openclaw/.env` and sees `VALEDIA_JEV=1` without a manual export. Cron already sourced it. Hermes LaunchAgent plists carry the flag for the next gateway restart.
