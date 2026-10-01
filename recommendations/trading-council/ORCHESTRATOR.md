# Trading Council — Orchestrator (Valedia Trader)

## Fast path (default, thin bankroll)
Scout alone (or weather morning scan / Poly Desk): detect markets juste dropped → Findings → runtime INFORMED fee-true gates → alert or create. **No** Decider/Auditor wake.

## Full SDA (ask Burton yes first, or explicit Trader go on high-stakes)
Use when the call will be acted on: trust a new FV source, change a standing gate, size up a lane, kill a desk. Skip for "list new Kalshi weather markets."

1. One-line question to Scout only.
2. Pass Findings List only to Decider + one-line decision request (e.g. "INFORMED fee-true create vs skip for this series today?").
3. Pass Findings + Conclusion only to Auditor.
4. Present to Burton: Conclusion → VERDICT → drivers → defects if FAILS.
5. If HOLDS and Burton/runtime gates allow → Valedia **runtime** executes; council does not OMS.

## Collapse modes (refuse)
- Same prompt to all three with role-line swap
- Decider/Auditor with browse or OMS tools
- Scout ranking or concluding
- Auditor "also suggest a better trade"
