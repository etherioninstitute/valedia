# ADAPT: ai-hedge-fund

**Source:** `~/MemPalace/sources/repos/github/ai-hedge-fund` @ `154a8b2`  
**Upstream:** https://github.com/virattt/ai-hedge-fund  
**License:** MIT  
**Stack:** Python (Poetry), CLI `aihf`, investor-agent personas, mandates under `~/.hedge-fund/`  
**Audited:** 2026-09-19 (README/VISION; docs-only)

## 1) Summary
Educational multi-agent "desk": pluggable investor agents as alpha models, fund mandates, backtest/paper cycle. Explicitly educational; README says it does not actually make trades. Evolving toward always-on fund entity. Supports TypeSafe **Jev** as a model provider (`--model jev-1.13.0`).

## 2) Pipeline fit
`strategy / research-agent` (multi-angle analysis) → optional `backtest` — **not** Valedia execution.

## 3) Overlap with Valedia
- Persona lenses (Buffett/Lynch/Hayes) + adversarial paper fights already exist
- Finance Scout advisory lane
- Jev already wired for Valedia shadow use

## 4) Steal (research/paper only)
- Mandate file shape (strategies, staff, risk, capital, cadence separate from tickers)
- Multi-agent cycle → JSON cycle record pattern
- Agent-as-alpha-model plugin idea for MiroFish / persona scoring

## 5) Hard exclusions
- Do not adopt as parallel OMS
- Do not enable any live path from this repo
- Do not copy Financial Datasets keys into Valedia live env without Burton go

## 6) Valedia shape
**research / paper** — deepen multi-agent debate on prediction-market theses; optional Jev as one voter (shadow).

## 7) Effort / deps
**M**. Depends on: existing persona fights, Finance Scout, optional MIROFISH-JEV-SHADOW.
