# Grok Build Executor Ack

**Date:** 2026-09-14  
**Agent:** Grok Build (this session)  
**Worktree:** `/Users/burtonchatelain/.openclaw/workspace-v2`  
**Charter:** `recommendations/GROK-BUILD-LANE.md` (EXECUTOR CHARTER)  
**Plan:** `recommendations/VALEDIA-SUBSUME-NAUTILUS.md`

This is acknowledgment only. No build, merge, install, or live action in this turn.

## Role

Grok Build is the **executor**. I code, test, and merge toward a perfectly operating Valedia.

Valedia (Grok Bot chat) **architects, audits, and relays**. I do not own architecture from this lane. I implement the charter and the subsume plan as written.

## North star

Maximum **realized**, fee-inclusive, **risk-adjusted compounding** P&L under Valedia gates (ERE, Kelly/fuses, fee-gate, category gate, daily budget, blacklist). Not research elegance. Not paper purity. Not Nautilus-native strategies.

If a change does not raise expected realized compounding — or does not stop a P&L lie that would size the book wrong — I do not do it.

## Nautilus: sidecar, not brain

NautilusTrader is subsumed as an **execution + market-data sidecar**. Valedia stays the brain.

- Valedia owns *whether*, *what*, *how much*, *when*, ledger truth, Signal, Kalshi (until a proven custom adapter), Alpaca, Mercury, MiroFish, UGFT/ERE/Kelly.
- Nautilus owns *how the order lives on the wire* for assigned venues: adapters, WS books, order state, reconciliation.
- The seam is a gated **Intent**. Thin **IntentConsumer** only. Never enlarge size. Never invent symbols. Never bypass gates.
- NT `RiskEngine` is a **stricter-or-equal floor**, never a second brain.
- Exclusive writer: one live writer per venue — Valedia script **or** sidecar, never both.
- First live NT venue (later, Valedia-gated): Coinbase. Then Hyperliquid. Then Polymarket. Kalshi last. Kraken read-only. Mercury never NT.
- Next engineering slice (when Burton says go): Intent schema + `VENUE_WRITER` lock + fill sink that cannot write `source="live"` unless Valedia flags say so. No `pip install nautilus_trader`. No `LiveNode`.
- Parallel non-NT profit work must not block: sports_engine ledger-truth (handoff B1).

Option D (rewrite kernel as NT `Strategy`) is rejected.

## Worktree

This cwd: `~/.openclaw/workspace-v2`. Live charter/docs also exist at `~/.openclaw/workspace`. I work here unless Burton redirects.

## Hard fence

I may write gated code, tests, and merges. I will **not**:

- Place live orders
- Run or enable `--live`
- Touch wallet/spend
- Change trading API keys
- Bypass Valedia as sole live risk authority

Live enablement = **Valedia runtime + Burton**. Coding agents stop at the flag.

## Standing lesson

No accuracy claim or sizing change is admissible until the fee-inclusive, fill-reconciled `forward_test_ledger` shows it. Paper/sandbox/NT-up is not edge.

---

**Ack:** Role, north star, Nautilus sidecar plan, worktree, and fence are understood. Stopped here.

**Ack (2026-09-14 21:57 PT):** RuFlo MCP required and verified (`mcp_servers.ruflo` → `http://127.0.0.1:8810/mcp`; 53 ruflo tools + 353 claude-flow tools visible; `memory_stats`/`mcp_status` read-only OK).
