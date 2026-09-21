# Valedia autonomy queue (Burton 2026-09-20)

After **PROFIT-1** (EV preflight + maker-first + fill ledger) SHIPS, Valedia parent must launch in order:

1. **CONTROLS-READOUT** — done `0.49.0`. `controls_snapshot()` + `/status.controls` + HTML Controls card + CLI. See `CONTROLS-READOUT.md`.
2. **AUTONOMY-2 MIROFISH-JEV-SHADOW** — done `0.50.0`. `VALEDIA_JEV` default **False**; when off Valedia works fully (fail-open); when on typed gates only. See `AUTONOMY-2-JEV-SHADOW.md`.
3. **KALSHI_FEE_MAP_GATE runtime flip** — follow `KALSHI-FEE-MAP-GATE-CHECKLIST.md`; set in live env / LaunchAgent only; **do not commit True** to repo defaults.

Each slice: RuFlo `:8810` + Ponytail after code. Fence otherwise unchanged.
