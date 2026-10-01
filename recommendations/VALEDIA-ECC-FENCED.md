# Valedia × ECC (fenced) — Burton 2026-09-19

**Source:** https://github.com/affaan-m/ECC (MIT)  
**MemPalace clone:** `~/MemPalace/sources/repos/github/ecc` @ `934195f` (depth-1; refresh with `git -C … pull`)  
**npm (optional, reviewed pin only):** `ecc-universal` / `ecc-agentshield` — never unofficial mirrors

## NOT

- NOT a full install into Valedia `workspace` / `workspace-v2` (`./install.sh --profile full`, stacked Claude+manual, or dumping ECC MCP catalogs).
- NOT a replacement for RuFlo MCP (`:8810`), Ponytail, or codebase-memory on Grok Build.
- NOT a live OMS / order path — ECC has no venue writers and must never be wired to `--live`, wallets, ACH, or API key changes.
- NOT automatic hook runtime on Grok Build (conflicts with Build lane; context bloat).

## NOT (yet)

- NOT wholesale Cursor `.cursor/` adapter install until a dry-run collision check vs existing Valedia/Grok files.
- NOT Hermes/OpenClaw ECC import until a separate fence pass (finance tools stay Valedia-runtime-only).
- NOT enabling ECC Memory Vault as authority over MemPalace / Valedia recommendations — vault = unreviewed context only if used later.

## IS

- **IS** a MemPalace reference corpus (skills/agents/docs) like RuFlo clone.
- **IS** selective SKILL.md cherry-picks for Grok Build / Claude review lane (copy skill bodies only; no hooks), preferred starters:
  - `tdd-workflow`
  - `security-review` / AgentShield **report-only**
  - `verification-loop`
  - `search-first`
- **IS** optional Claude Code `ecc@ecc` as **review** lane (LOCAL policy: RuVocal/RuFlo primary coder; Claude optional review) — label UPSTREAM-INTENDED vs LOCAL.
- **IS** compatible with profit north star only as **engineering quality** for Valedia code — strategies/edges still implemented behind Valedia fences by Grok Build.

## Grok Build use

1. Read skills from MemPalace clone path; do not npm-install into Build by default.
2. If importing: copy into `workspace-v2/recommendations/ecc-skills/<name>/SKILL.md` and cite path in slice notes.
3. AgentShield: `agentshield scan --path .` report-only; no `--fix` unless Burton says yes.
4. Keep `[mcp_servers.ruflo]` + `codebase-memory` + Ponytail; do not add ECC MCP bundle without trifecta review.

## Refresh

```bash
git -C ~/MemPalace/sources/repos/github/ecc fetch --depth 1 origin main && git -C ~/MemPalace/sources/repos/github/ecc reset --hard origin/main
```

Stamp new SHA into this file when refreshing.
