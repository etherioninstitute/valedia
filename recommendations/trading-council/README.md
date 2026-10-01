# Valedia Trading Council — prompt pack

Adapted from Grok Bot Scout / Decider / Auditor (`scout-decider-auditor` skill + agent profiles) for **prediction-market trading only**.  
Orchestrator = **Valedia Trader** (not life CoS, not Valedia AI Assistant).

**Seeded:** 2026-09-23 from CoS + Valedia AI Assistant consults.  
**OMS fence:** These roles never create/amend/cancel. Valedia runtime sole live risk. INFORMED + fee-true hard create gate; Jev fail-open shadow only.

Files:
- `SCOUT.md` — Findings List (settle FV, research, book, clocks)
- `DECIDER.md` — one INFORMED create/skip/size conclusion
- `AUDITOR.md` — HOLDS/FAILS attack on the edge claim
- `ORCHESTRATOR.md` — when Trader dispatches full SDA vs fast Scout path

Do not create Grok Bot agents until Burton says. Can run as prompt pack inside Trader / Grok Build / RuVocal first.

## Hermes profiles (created 2026-09-23)
| Profile | Wrapper | Model at create |
|---------|---------|-----------------|
| valedia-trading-scout | `valedia-trading-scout` | qwen3.8-27b-obliterated:q6_K |
| valedia-trading-decider | `valedia-trading-decider` | ornith:9b (align to Ornith Q4/LiteLLM later if needed) |
| valedia-trading-auditor | `valedia-trading-auditor` | deepseek-r1:32b |
Skill: `~/.hermes/skills/valedia-council/SKILL.md` updated to prefer these for trading.

## Live Hermes models (2026-09-23 evening)
| Profile | Model | Provider |
|---------|-------|----------|
| valedia-trading-scout | `qwen/qwen3.8-27b` (regular Bionic Qwen 3.8) | custom:lms-local :1234 |
| valedia-trading-decider | `ornith-1.5-k-m` | custom:litellm :4000 |
| valedia-trading-auditor | `ornith-1.5-k-m` | custom:litellm :4000 |
Flash Next UD = optional heavy Scout when Ornith is unloaded; not used while Ornith-only is loaded.
