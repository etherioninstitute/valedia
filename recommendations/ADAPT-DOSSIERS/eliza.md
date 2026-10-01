# ADAPT: eliza (elizaOS)

**Source:** `~/MemPalace/sources/repos/github/eliza` @ `0d04c5be`  
**Upstream:** https://github.com/elizaOS/eliza  
**License:** MIT  
**Stack:** TypeScript monorepo (Bun), AgentRuntime, plugins, app UI, optional wallets  
**Audited:** 2026-09-19 (docs-only)

## 1) Summary
Agent OS: chat/memory/plugins, workflows, browser automation, **non-custodial EVM/Solana wallet ops with approval boundaries**.

## 2) Pipeline fit
`research-agent` / orchestration — not Valedia market execution.

## 3) Overlap
RuFlo/OpenClaw/Hermes already cover agent orchestration; Valedia is the trading brain.

## 4) Steal
- Plugin contract (actions/providers/evaluators)
- Approval-boundary UX patterns (for human gates on live flips)
- Memory/knowledge plugin shapes (compare to MemPalace / ECC memory — do not replace)

## 5) Hard exclusions
- **HARD NO** wallet / onchain spend / signing in Valedia path
- No Eliza as OMS or venue writer
- No auto-approving chain txs

## 6) Valedia shape
**research only** — agent UX/plugin ideas; never subsume wallet stack.

## 7) Effort / deps
**S–M** for ideas; **forbidden** for wallet integration.
