---
description: >
  Valedia's memory system. Self-improving on NIMROD + VPS, Cerebro topics,
  Engram on NIMROD_SSD, daily VPS sync. Updated March 30, 2026.
---

# valedia memory

## Three tiers

### Tier 1: Always loaded (SOUL.md + USER.md + self-improving/memory.md)
Identity, preferences, hot memory. ~50 lines. In context every session.

### Tier 2: Trading skill graph (this graph)
~/.openclaw/workspace/skills/ — structured knowledge.
Traversed on demand via [[wikilinks]]. Zero duplication.

### Tier 3: Episodic memory (Engram + self-improving)
NIMROD_SSD: /Volumes/NIMROD_SSD/valedia_memory/
Self-improving: ~/self-improving/memory.md (hot memory, 25 lines)

## Self-improving memory (new)

Location: ~/self-improving/ on both NIMROD and VPS
Structure:
- memory.md — hot memory (proven strategies, killed strategies, lessons)
- projects/ — per-project learnings
- domains/ — domain-specific knowledge
- archive/ — decayed patterns
- corrections.md — last 50 corrections

Key contents: account state, proven edge (sports 87.5%), killed strategies
(weather arb, oil brackets), intelligence stack status, key lessons.

## Cerebro topics (VPS Command Center)

Location: /root/.openclaw/workspace/cerebro/topics/ (VPS)
5 active topics (directory + topic.md format):
- sports-engine/ — 87.5% WR, 14 live bets, intelligence stack
- latency-arb/ — BTC/ETH spot arbitrage, dry-run mode
- market-scanner/ — 600+ markets, 185 sports props found
- settlement-tracking/ — hourly checker, Signal profit alerts
- openclaw-stack/ — 39 skills, Command Center dashboard

Visible in Command Center: http://100.99.195.47:3333

## VPS memory files

Location: /root/.openclaw/workspace/memory/ (VPS)
3 files: memory.md, strategies.md, infrastructure.md
Visible in Command Center Memory panel.

## Engram plugin (NIMROD_SSD)

Local-first using Qwen3 32B for extraction (no API costs).
- Auto-capture: extracts memories after each turn
- Auto-recall: injects relevant context before response
- Hybrid search: semantic + keyword + temporal decay
Stored at: /Volumes/NIMROD_SSD/valedia_memory/

## VPS sync (daily 3 AM UTC)

rsync -az /Volumes/NIMROD_SSD/valedia_memory/ root@85.239.234.32:/root/valedia_memory/
One-way: Mac -> VPS only.

## Key principles

- Write everything to files. Mental notes die.
- Daily logs are raw. memory.md is distilled.
- Old memories decay (temporal decay).
- Kill bad approaches honestly, document why.
