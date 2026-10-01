# Hermes Matrix / mautrix delivery fix — 2026-09-30

**GROK_EXIT:0**

Charter `recommendations/GROK-CHARTER-HERMES-MATRIX-MAUTRIX-FIX-2026-09-30.md`. Life/root runtime only. No trading yaml, no LMS command, no LiteLLM stop, no Paperclip change, no `--live`.

## Root cause

The life gateway process imports from tools Python 3.14, not from `hermes-agent/venv` (Python 3.11).

| Runtime | Interpreter | mautrix before |
|---|---|---|
| Life gateway pid **34663** (parent osascript **30269**, started 16:22:29 PT; python started 16:24:53 PT) | `~/.hermes/tools/python-3.14.7+20260901-darwin-arm64/bin/python3` | **missing** |
| `PYTHONPATH` on that process | `hermes-agent` + `installs/e9a8c1e0488a29ad/environments/5d8db6e4843647a885bd7efdd16a4f58/venv/lib/python3.14/site-packages` | no `mautrix/` |
| Committed generation in `facts.json` (newer, 16:26 PT) | `environments/e6da9587677641d19147c8fea29277e2/.../python3.14/site-packages` | no `mautrix/` |
| Already installed, wrong interpreter | `~/.hermes/hermes-agent/venv` Python **3.11.16** | `mautrix==0.21.1` plus `olm` (`python_olm` 3.2.16) |

`pm.extras` matrix anchors: `mautrix`, `asyncpg`, `aiosqlite`, `markdown`, `aiohttp_socks`. On the live 3.14 tree, only `markdown` 3.10.2 was present. `aiohttp` 3.14.3 was already present and was not upgraded.

Before, the same binary with that site-packages on `sys.path`:

```
IMPORT_FAIL ModuleNotFoundError No module named 'mautrix'
```

Gateway log at 17:59:16 PT (in pid 34663): `Job '8bbbc09fff56': delivery error: Matrix send failed: No module named 'mautrix'`.

## Install

Sanctioned path first:

```
hermes pm install --extra matrix
✗ venv: extras ['matrix'] are not supported by this Python/platform — choose a supported provider; no dependency environment was changed
```

`pyproject.toml` gates the whole extra with `matrix = "sys_platform == 'linux'"` because `mautrix[encryption]` pulls `python-olm`, and PyPI ships linux wheels plus an sdist only (no darwin wheel). An explicit install refuses before it takes the venv lock. `facts.json` was not rewritten.

Fallback, that env's own uv, both trees the life runtime actually uses:

```
uv pip install --python <venv>/bin/python \
  mautrix==0.21.1 aiosqlite==0.22.1 asyncpg==0.31.0 aiohttp-socks==0.11.0
```

Dry-run on the live env: install those four plus `python-socks==2.8.2`. No upgrades. Same set landed in `5d8db6e…` (the directory pid 34663 has on `PYTHONPATH`) and in committed `e6da9587…` (what a new Hermes process selects). `python-olm` was not installed. `MATRIX_E2EE_MODE` is `optional`, so the adapter continues without encrypted-room support.

After, same tools Python 3.14.7:

| Tree | `import mautrix` |
|---|---|
| live `5d8db6e…` | **0.21.1** `.../5d8db6e4843647a885bd7efdd16a4f58/venv/lib/python3.14/site-packages/mautrix/__init__.py` |
| committed `e6da9587…` | **0.21.1** `.../e6da9587677641d19147c8fea29277e2/venv/lib/python3.14/site-packages/mautrix/__init__.py` |

`asyncpg` 0.31.0, `aiosqlite` 0.22.1, `markdown` 3.10.2, and `aiohttp_socks` import on both. `from mautrix.types import EventID, RoomID` succeeds.

A later `hermes update` / `pm` sync that publishes a **new** generation will not carry these packages: the matrix extra stays gated off darwin, and they are not in the extras ledger. Re-apply the same `uv pip install` into that generation. The two generations that exist now both have them, including the one a life restart would select.

## Bounce

**None.**

| Surface | pid | Since |
|---|---|---|
| `ai.hermes.gateway` | 30269 | 16:22:29 PT |
| gateway python | 34663 | 16:24:53 PT |
| `com.burton.hermes-signal` | 908 | 2026-09-29 14:48:36 PT |
| `com.burton.matrix-tunnel` | 925 | 2026-09-29 14:48:36 PT |
| trading scout / decider / auditor | 76364 / 76365 / 76348 | 14:58 PT |
| CTO | 76349 | 14:58:20 PT |
| LiteLLM `:4000` | 943 | 2026-09-29 14:48:36 PT |
| Paperclip `:3100` | 55745 | 2026-09-29 16:19:21 PT |

No `hermes gateway --replace`. No Signal or matrix-tunnel kickstart. No life kickstart. Delivery imports `mautrix` per send; the new files are on the site-packages directory the running gateway already has on `sys.path`, and a fresh interpreter on that path imports them. Screen `valedia-grok` was left detached. `ot-valedia-desk` and `valedia-glance` were not in `screen -ls`.

## Delivery proof

Important-mail `8bbbc09fff56`, `hermes cron run` at 18:16:41 PT, finished 18:18:18 PT (CLI pid 44088):

- execution `a2c251be306048e682414da7365012ee`, `status=completed`, `error` empty, `delivery_outcome=suppressed`
- output ends `[SILENT]` (nothing cleared the mail bar)
- job record after: `last_run_at=2026-09-30T18:18:18-07:00`, `last_status=ok`, `last_error` empty, `last_delivery_error` empty
- no `No module named 'mautrix'` in `errors.log` after 18:00

That tick did not open the Matrix sender. The same room and adapter were exercised by chief-of-staff `b0f826c750c9` (`no_agent`, script stdout, deliver `matrix:!L1H9bZOwdqKwBWt_mnD7dlbMG_bruf7rQWW9JVRqgf4`), `hermes cron run` at 18:19:20 PT, finished 18:19:27 PT (CLI pid 50249):

- `delivery_outcome=delivered`
- `last_status=ok`, `last_error` empty, `last_delivery_error` empty
- log: `Matrix: E2EE optional but dependencies are missing. Continuing without encrypted-room support.`
- log: `Matrix: sent event $bNPNqFUC6pKhyky1ih9TVqx4bxFH-ZLFN4VPTU2O2eE to !L1H9bZOwdqKwBWt_mnD7dlbMG_bruf7rQWW9JVRqgf4`
- log: `Job 'b0f826c750c9': delivered to matrix:!L1H9bZOwdqKwBWt_mnD7dlbMG_bruf7rQWW9JVRqgf4`

That error is the missing **libolm / python-olm** extra, not a missing `mautrix` module. The send still returned an event id.

| Job | Disposition |
|---|---|
| Important-mail | Fresh run ok / suppressed. Stored error does not mention `mautrix`. Next `*/30` tick uses this runtime. |
| chief-of-staff-daily | Delivered on this proof fire. Next schedule still `20:00` PT today, so the digest will send again then. |
| morning-brief | Not re-fired (full agent). Stored `last_delivery_error` is still the pre-fix `Matrix connect failed` from 06:38 PT. Next run `2026-10-01 06:30` PT. |

## Doctor — Matrix credential

`hermes doctor` still warns:

> Profiles `default` and `valedia` both hold the same matrix credential (`MATRIX_ACCESS_TOKEN` / `MATRIX_PASSWORD`). One token can serve only one gateway.

**Deferred, needs Burton.** Doctor's remedy is a second bot token, or remove the token from the profile that should not own Matrix, then `hermes gateway migrate --multiplex`. That migrate is outside the no-bounce fence. The valedia profile env was left unchanged so Desktop is not edited on a guess. Life/default still holds the credential the gateway already uses. No new token was created.

## Fences

Held. No trading CHANGE-set edit. No TCC change. No `lms` load or unload (no `lms` command). LiteLLM pid 943 still listening on `:4000`. Paperclip pid 55745 still listening on `:3100`. No `hermes -p valedia gateway`. No `--live`, no venue orders, no wallets. No Paperclip dual-write.

No config or env file was edited, so there is no backup. Rollback is uninstalling `mautrix`, `asyncpg`, `aiosqlite`, `aiohttp-socks`, and `python-socks` from the two 3.14 site-packages above.

**GROK_EXIT:0**
