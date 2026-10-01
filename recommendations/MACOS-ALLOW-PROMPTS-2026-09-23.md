# macOS Allow prompts that do not stick — 2026-09-23

## Two different dialogs (do not conflate)

1. **Crash Reporter** — “Python quit unexpectedly”
   - Confirmed source: LATENCY ARB `arb_run.py` SIGSEGV (OpenSSL) every ~30m dry-run.
   - Fix path: ARB-OPENSSL-HARDEN (Grok Build). Optional: silence Crash Reporter for background only after harden.

2. **Privacy / Allow** — TCC “Allow” / “Don’t Allow”
   - These are remembered **per client identity**. A venv `python` binary path is a different client from `Hermes.app`, from Frameworks `python3.13`, from miniconda, from uv cpython.
   - Allowing one does **not** cover the others → feels like “not remembering.”

## Hottest client on this Mac (user TCC.db)

| Client | Rows | Notes |
|--------|------|--------|
| `~/.hermes/hermes-agent/venv/bin/python` | 14 | Files/Folders, App Data, File Provider (Dropbox), AppleEvents, Photos, Media… Latest touch **today 1:03 PM PT** (`SystemPolicyAppData`) |
| uv `cpython-3.11.16.../python3.11` | 5 | Separate identity |
| Frameworks `python3.13` | 2 | Downloads + Removable — Valedia cron uses **Python.app** bundle (org.python.python), fewer TCC rows |
| miniconda `python3.13` | 1 | File Provider |

Denied (auth_value=0) that can re-prompt: Cursor (`com.todesktop.230313…`) File Provider + Media Library; Hermes venv App Bundles; Dropbox removable volumes (old).

## Why it feels broken

- Agents (Hermes, Claude Code, Cursor/sand, MemPalace mine) touch Desktop/Documents/Downloads/NAS/Dropbox via **naked python paths**.
- Recreating a venv or upgrading the interpreter can invalidate or orphan path-based grants.
- File Provider (Dropbox) prompts stack when many tools scan cloud folders.
- Valedia LATENCY ARB mainly drives **Crash Reporter**, not Allow — but both can fire in the same afternoon.

## Fix plan (safe, no TCC reset)

### A. One-time System Settings grants (prefer APP BUNDLES)

System Settings → Privacy & Security:

1. **Files and Folders** + **Full Disk Access** (if needed): enable **Hermes**, **Claude**, **Cursor**, **Terminal** / **iTerm**, **Python** (org.python.python) — not every venv path.
2. **Automation**: allow Hermes / Claude / Cursor → System Events / Finder if those dialogs appear.
3. **Local Network** (if Sequoia/Tahoe list exists): allow the same app bundles once.
4. For Dropbox File Provider nagging: prefer granting the **host app** (Hermes/Cursor) rather than clicking Allow on a brief python helper.

### B. Reduce path-based Python sprawl

- Run agent tools under the **app bundle** identity when possible (Hermes.app, Claude.app).
- Avoid recreating `hermes-agent/venv` casually; if you must, re-grant once afterward.
- Point cron/`cron_runner` at one stable Python (already Frameworks 3.13) — do not add more interpreters to crons.

### C. After ARB harden ships

- Confirm no more Python Crash Reporter every :15/:45.
- If Allow storms continue, capture a screenshot of the exact dialog (title + app name) for a second pass.

## Do not

- `tccutil reset All` (wipes everything; worse for a week)
- sudo-edit TCC.db
- Disable SIP
