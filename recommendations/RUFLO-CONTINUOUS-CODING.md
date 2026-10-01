# RuFlo continuous coding (Valedia builder)

## Who codes
**RuFlo is the primary coder** for Valedia — continuous improvement toward maximum long-run profit via a better harness. The workspace was snapshotted (`pre-ruflo-B1`) so RuFlo can change anything that improves the system.

**Claude Code is not the builder.** Burton may open Claude Code anytime to *inspect* diffs. That is optional review, not a gate and not the coding path.

**Goal UI `/agents`** (~40s ETA, Approve buttons) is a **demo UI**. Do not treat it as RuFlo coding. Real RuFlo work runs through RuVocal + local MCP (`:8810`) / `claude-flow` tools / terminal against `~/.openclaw/workspace`.

## Hard fence (only real constraint)
- No `--live`, no venue orders, no wallet/spend, no trading API key changes
- Valedia runtime = sole live risk authority
- No copying UNLICENSED `kalshi-agent` source (clean-room / ask-author / separate-run only)
- Claimed “wired” changes need **live-path proof** + **ponytail**

## What RuFlo may change
Anything in `~/.openclaw/workspace` that raises expected long-run profit or cuts silent P&L lies — including code Claude previously wired. **Re-verify, replace, or rewrite** if better. Backup exists for rollback.

## Loop
1. Highest-leverage unfinished profit lever (ledger truth → gates → sizing → edge quality → …)
2. Implement + tests + live-path proof
3. Optional: quarantine-clone GitHub (license check → smoke test → patterns/code only if license allows) → MemPalace `sources/repos/` catalog
4. Update `recommendations/RUFLO-STATUS.md` with evidence + one-liner: *how this increases profit*
5. Continue — no UI Approve, no “wait for Claude”

## Model
`qwen3.8-turbo-coder:q6_K-low` (or stronger local) via RuVocal; MCP tools bare names on `:8810`.
