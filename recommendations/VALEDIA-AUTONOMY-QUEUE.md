# Valedia autonomy queue (Burton 2026-09-20)

After **PROFIT-1** (EV preflight + maker-first + fill ledger) SHIPS, Valedia parent must launch in order:

1. **CONTROLS-READOUT** — extend `valedia_cockpit` `/status` + UI with accurate on/off for every control (halt, fee-map gate, EV preflight, maker-first, Jev, self-adjust, venue writers, Poly live, oil fence, crypto paper, etc.). Immediate readout.
2. **AUTONOMY-2 MIROFISH-JEV-SHADOW** — `VALEDIA_JEV` default **False**; when off Valedia works fully (fail-open); when on typed gates only. No TypeSafe on critical path.
3. **KALSHI_FEE_MAP_GATE runtime flip** — follow `KALSHI-FEE-MAP-GATE-CHECKLIST.md`; set in live env / LaunchAgent only; **do not commit True** to repo defaults.

Each slice: RuFlo `:8810` + Ponytail after code. Fence otherwise unchanged.
