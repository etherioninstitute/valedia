---
description: >
  Entry point for Valedia's trading knowledge graph. Maps all trading concepts,
  methodology, platforms, and rules. Start here for any trading question.
---

# trading — skill graph index

Valedia's complete trading knowledge, structured as a traversable graph.
Each node is one complete concept. Follow [[wikilinks]] when they are relevant
to the current question or task.

## Core Engine

The mathematical backbone of every trade decision:

- [[core/kernel-v16e]] — 11-framework prediction engine, adaptive SVD, regime detection (MOM/REV/VOL)
- [[core/filter-v3]] — two-filter system: equity vs crypto gates, day-of-week discovery, booster multipliers
- [[core/ere-safety]] — Etherion Risk Engine: NS-mapped safety, cascade detection, halt conditions
- [[core/kelly-sizing]] — half-Kelly position sizing, confidence multiplier, self-improvement feedback loop
- [[core/fee-gate]] — fee schedules per platform, minimum profitability enforcement

## Intelligence Layer

What informs each decision before the kernel runs:

- [[intelligence/signal-fusion]] — how kernel + ML + MiroFish + Fear/Greed combine into fused score
- [[intelligence/mirofish]] — swarm simulation, agent types, 10% weight justification, seed-to-signal pipeline
- [[intelligence/discovery]] — dynamic universe scanner, buzz scoring, CoinGecko + Reddit + StockTwits, FOMO protection
- [[intelligence/intel-sources]] — Fear/Greed index, insider SEC filings, hedge fund 13F, whale monitoring

## Platforms

Where trades actually execute:

- [[platforms/coinbase]] — EC key auth, SOCKS proxy requirement, crypto pairs available
- [[platforms/alpaca]] — live vs paper, SSL cert fix, equity universe, fractional shares
- [[platforms/universe]] — all 37 core assets + extended universe, asset class tags

## Rules

Hard constraints that never bend:

- [[rules/fund-rules]] — 5% daily risk cap, 4% per trade, 70% profit lock, no leverage without approval
- [[rules/backtesting]] — OOS results table, hit rates by version, RenTech comparison (51% baseline)
- [[rules/day-of-week]] — empirical day-of-week hit rates, Wed/Thu booster, Mon/Tue skip logic

## Cross-Domain Links

- [[valedia/architecture]] — full system architecture, Mac primary + VPS backup, heartbeat failover
- [[valedia/memory]] — memory system: Engram on NIMROD_SSD, VPS sync, skill graph traversal
