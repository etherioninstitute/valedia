# HERMES-TCC-APPDATA-FIX — 2026-09-30

Stop macOS **“python would like to access data from other apps”** (`kTCCServiceSystemPolicyAppData`) at the source. Hermes agents must not run unbounded `find` / file search into `~/Library/Containers`, `~/Library/Group Containers`, or other apps’ Application Support.

**GROK_EXIT:0**

## Root cause

TCC attribution 2026-09-30 ~13:50 PT:

| Field | Value |
| --- | --- |
| Dialog | “python” would like to access data from other apps |
| Service | `kTCCServiceSystemPolicyAppData` |
| Responsible | `com.nousresearch.hermes.managed-python` |
| Path | `/Users/burtonchatelain/.hermes/hermes-agent/venv/bin/python` (adhoc-signed) |
| Child | `/usr/bin/find` walking protected app data |
| Gateway | `ai.hermes.gateway-valedia-cto` pid **73539** (confirmed still that pid at fence time) |

FDA on Terminal / Hermes.app / Python.app does not cover this identity. The LaunchAgent runs the venv python **by path**. Hermes.app bundle AppData allow ≠ venv path.

**Which tool spawned `find`:** the CTO gateway has **no `rg` on its LaunchAgent PATH** (`/opt/homebrew/bin` is present; `rg` is not installed there). Hermes `search_files` therefore falls back to `/usr/bin/find` for filename walks. Hermes already refuses that fallback when the root is `$HOME` or `/`, and already prunes `Library` from home-rooted **ripgrep** searches — but:

1. **`terminal` `find ~` / `find /Users/burtonchatelain` / `find ~/Library…`** has no prune. Overnight Paperclip heartbeats on valedia-cto can issue those commands. This is the dialog’s child.
2. **`search_files` rooted inside `~/Library`** is not classified as a “broad home” root, so the find fallback still runs and walks Containers.
3. MemPalace on CTO uses Frameworks `python3.13` (different TCC identity). Cronjob toolset is already disabled on CTO. Heartbeats are Paperclip API wakes, not Hermes cron.

Today’s Allow on the venv path stuck (`AppData` auth_value=5 @ 13:50:58). Recurrence is a **new find into Containers**, or a Hermes update rewriting the adhoc binary.

## Fence applied (config/profile; no hermes-agent core patch)

Shared hook (blocks **before** `/usr/bin/find` is spawned):

- `~/.hermes/agent-hooks/block-tcc-appdata.py`
- `pre_tool_call` matcher `terminal|search_files|read_file|write_file|patch`
- `fail_closed: true`, `hooks_auto_accept: true`

On **valedia-cto** and **valedia-trading-{scout,decider,auditor}**:

- `approvals.deny` — 17 fnmatch globs covering `find ~`, `find $HOME`, `find /Users/burtonchatelain`, `~/Library`, Containers / Group Containers (deny beats yolo / allowlist)
- `approvals.smart_policy` — guardian text for the same paths
- `terminal.cwd` → `/Users/burtonchatelain/.openclaw/workspace-v2` (relative `find .` stays in workspace)
- `terminal.home_mode` left `auto` so git/ssh and `~/.openclaw` tilde expansion keep working
- SOUL.md hard rule: never `find ~` or into Library/Containers

Backups: `config.yaml.bak-20260930-tcc-appdata` next to each profile yaml.

Kickstarted only those four LaunchAgents (not life Matrix/Signal, not full stack). CTO 8645 had a 20s bind race then came back.

## Proof (no find into Containers was executed)

Hook `--self-test` exit 0.

Live `HERMES_HOME=…/valedia-cto` `check_dangerous_command`:

| Command | approved | user_deny |
| --- | --- | --- |
| `find ~ -name '*.py'` | False | True (`find ~ *`) |
| `/usr/bin/find …/Library/Containers -name '*'` | False | True (`*library/containers*`) |
| `find …/workspace-v2 -name '*.md'` | True | — |
| `find . -name '*.py'` | True | — |
| `curl -sS http://127.0.0.1:3100/api/issues` | True | — |

`hermes hooks test pre_tool_call` (production `args` → `tool_input` wire):

- `find ~` → parsed `action=block`
- `/usr/bin/find …/Library/Containers` → parsed `action=block`
- `search_files path=~` → parsed `action=block`
- `find …/workspace-v2` → no-op `{}` (allowed)

CTO logs: `shell hook auto-approved` + `registered … fail_closed=True` @ 14:08:56–14:09:22 PT. Same register line on scout/decider/auditor.

`hermes hooks doctor`: script executable, allowlisted, valid JSON.

Post-kickstart listeners: 8642 scout, 8643 decider, 8644 auditor, 8645 CTO (python pids 79797 / 79891 / 79929 / 80417). 8645 `/v1/capabilities` returns 401 without `API_SERVER_KEY` (server up).

## Files changed

- `~/.hermes/agent-hooks/block-tcc-appdata.py` (new)
- `~/.hermes/profiles/valedia-cto/config.yaml` + `SOUL.md`
- `~/.hermes/profiles/valedia-trading-scout/config.yaml` + `SOUL.md`
- `~/.hermes/profiles/valedia-trading-decider/config.yaml` + `SOUL.md`
- `~/.hermes/profiles/valedia-trading-auditor/config.yaml` + `SOUL.md`
- four `config.yaml.bak-20260930-tcc-appdata`
- `recommendations/HERMES-TCC-APPDATA-FIX-2026-09-30.md` (this stamp)
- `recommendations/GROK-STATUS.md`, `memory/2026-09-30.md`, `tasks/lessons.md`

Not changed: hermes-agent source, root MoA / life Matrix, `--live`, LMS, OKX.

## If a prompt still appears

Click **Allow once** on the **venv python path** (`com.nousresearch.hermes.managed-python`). Do not SIP-break or sudo-edit TCC.db. Re-grant after a Hermes update that rewrites `~/.hermes/hermes-agent/venv/bin/python`.

Optional later: install `rg` onto the LaunchAgent PATH so `search_files` stops using find. The terminal deny + hook still required.

## GROK_EXIT:0
