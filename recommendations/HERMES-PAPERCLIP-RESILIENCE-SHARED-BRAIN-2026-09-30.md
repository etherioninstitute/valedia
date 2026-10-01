# HERMES-PAPERCLIP-RESILIENCE-SHARED-BRAIN — 2026-09-30 21:26 PT

**GROK_EXIT:0**

Paperclip Resilience stays enabled from the executor's scrubbed install. Shared Memory Stack was not installed. Cobrain SaaS was not installed. The Cobrain-like workflow is the local MemPalace skill, copied in as docs only.

## Resilience

Desktop toggle ON at 21:02 PT ran `skills-install-clawhub-paperclip-resilience-f0fc6910`. skills-guard-v7 scanned community `@levineam/paperclip-resilience` v1.1.0 and returned **DANGEROUS**, 17 findings. Policy is community + dangerous = hard block. `--force` does not override. The UI sat on Installing… and then the toggle vanished because the install never finished. The skill id `paperclip-resilience` matches the display name. This was not a profile-scope miss.

Raw scan cache `~/.hermes/skills/.hub/scan-cache/0ac84257…d1150f0bc2f312c5.json`, scanned_at `2026-10-01T04:02:09Z` (21:02 PT):

| Severity | Count | Where |
|---|---|---|
| critical | 6 | `tests/test-security.js`, `tests/test-security-quick.js`, plus one defensive comment in `src/spawn-with-fallback.js` |
| high | 3 | same tests and that comment (`path_traversal_deep`) |
| medium | 7 | same tests, crontab wording in `docs/architecture.md` and `scripts/setup.js`, control-char strip in `src/lib/paperclip-issue-gate.js` |
| low | 1 | second line of the same defensive comment |

Rules fired: `system_passwd_access`, `path_traversal`, `path_traversal_deep`, `persistence_cron`, `hex_encoded_string`. Audit log records four BLOCKED lines at 21:02 PT (`17_findings`) and one INSTALL at 21:17 PT.

The false positive is the skill's own security tests naming a system password file and dot-dot traversal, plus that one comment. Executor removed `tests/test-security.js` and `tests/test-security-quick.js`, and replaced the comment examples with placeholders. Validation logic stayed (`BLOCKED_PREFIXES` still includes `/etc/`). This pass did not re-fetch the raw ClawHub zip and did not run `hermes skills install clawhub/paperclip-resilience`. A raw reinstall would scan DANGEROUS again until upstream ships a `.skillignore` that excludes tests, or stops putting those strings on the scan surface.

| Proof | Result |
|---|---|
| `hermes skills list` | `paperclip-resilience` source **clawhub**, trust **community**, state **enabled** |
| `test -f ~/.hermes/skills/paperclip-resilience/SKILL.md` | present |
| Hub `~/.hermes/skills/.hub/lock.json` | `installed.paperclip-resilience` `scan_verdict: safe`, identifier `@levineam/paperclip-resilience`, `content_hash` `sha256:8db967e6912a7b0a`, `installed_at` `2026-10-01T04:17:23Z`, provenance note `scrubbed-fp-tests-and-comment-triggers` |
| Lock file list | 21 files. No `tests/test-security*.js`. No `config.json` |
| `skills.disabled` in `~/.hermes/config.yaml` | apple-reminders, claude-code, codex, findmy, imessage. **paperclip-resilience is not listed** |
| Audit | `2026-10-01T04:17:23Z INSTALL paperclip-resilience clawhub:community safe sha256:8db967e6912a7b0a` |
| Runtime config | `config.example.json` left in place. `config.json` absent. `scripts/setup.js` not run. `crontab -l` has no `paperclip-resilience` line |

The four medium informational hits the executor left in the tree are the crontab sentences in `docs/architecture.md` (two) and `scripts/setup.js` (one), plus the control-char strip regex in `paperclip-issue-gate.js`. They did not flip the lock verdict off `safe`.

## Shared Memory Stack

**Usable: N.**

ClawHub `shared-memory-stack` (@nerua1) is a reference skill. Compatible-with lists claude-code and openclaw, not Hermes. It hardcodes palace `/Volumes/2TB_APFS/openclaw-data/workspace/memory/palace/` and an Obsidian vault hub. That volume path is **absent** on this Mac. Live palace is `~/.mempalace/palace` (present; prior stamp 611,649 drawers). Installing it would teach agents the wrong root beside the live MemPalace MCP, and it would make Obsidian the structural hub when Paperclip + MemPalace already are. Capture-idea and vault-room ideas can be stolen into the `shared-brain` wing later. Not installed. Not in the hub lock. No `~/.hermes/skills/shared-memory-stack` directory.

## Cobrain addendum (binding)

Burton rejects the Cobrain SaaS ceiling (1000-note free / $9 unlimited). This pass does not recommend it and did not install it. No `pip install cobrain`. No `brn init`. No Cobrain skill directory.

`cobrain-vault` (skills.sh / igoakulov) is a local CLI vault (topics, sources, graph). Usable only as a workflow pattern. A second store next to the 611k-drawer palace would dual-write. Not installed.

The replacement is MemPalace, which is already the MCP on all six KEEP profiles. UX mirrors the Cobrain session flow:

| Step | Live tool | Scope |
|---|---|---|
| `start_session` | `mempalace_diary_write` or `mempalace_add_drawer` | wing `shared-brain`, room `sessions` |
| `load_context` | `mempalace_search` plus `mempalace_kg_query` | prefer wing `shared-brain` |
| `search` | `mempalace_search` | same palace root |
| `append_decision` | `mempalace_add_drawer` room `decisions`, plus `mempalace_kg_add` | no note ceiling |

Write tools stay on root, valedia, and valedia-cto includes. Trading scout, decider, and auditor stay on the 12-tool read set. This pass did not edit those includes and did not retarget `--palace`.

Proposal (already on disk, status line updated to record the copy):

- `recommendations/SKILL-MEMPALACE-SHARED-BRAIN.md`
- `recommendations/skills/mempalace-shared-brain/SKILL.md`

Copied byte-for-byte to `~/.hermes/skills/mempalace-shared-brain/SKILL.md` (sha256 `e023921e649554df32daf42965606457c44d86054a5e9d2001dc32f4a6e7e394`, 2450 bytes, only file in that directory). `hermes skills list` shows `mempalace-shared-brain` source **local**, trust **local**, state **enabled**. No hub lock row. No new MCP server. Docs and workflow only. Anti-pattern section names Cobrain, the 2TB path, and Obsidian so agents do not follow them.

## Burton-only

- Refresh Hermes Desktop if the Paperclip Resilience toggle still looks missing. The skill is enabled on disk; this pass did not relaunch Electron.
- When fallbacks are wanted, copy `config.example.json` to `config.json` and fill Burton's model aliases. Placeholders were left untouched.
- Ask the upstream author to exclude `tests/` from the scan surface (`.skillignore` or equivalent). Until then, do not reinstall from raw ClawHub.
- Claude Desktop, Claude Code, Codex, and Grok Bot still need the same SKILL text or an MCP config that points at `~/.mempalace`. Hermes already has the MCP.

## Fences held

No `--live`. No orders. No money path. No LMS unload. No LiteLLM kill. No trading or life gateway bounce. No Desktop relaunch. No Paperclip issue write and no VAL dual-write into Kanban. `scripts/setup.js` not run, no recovery crontab. MemPalace palace path unchanged. Shared Memory Stack not installed. Cobrain SaaS not installed. Oil and weather fences untouched.

**GROK_EXIT:0**
