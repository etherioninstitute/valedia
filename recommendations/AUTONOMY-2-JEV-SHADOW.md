# AUTONOMY-2 — MiroFish Jev shadow

Version `0.50.0`. Optional TypeSafe Jev typed gates on the **MiroFish report path**. Default **off**. Valedia runs fully without TypeSafe.

Module: `scripts/integrations/jev_shadow.py`

## Fail-open contract

| `VALEDIA_JEV` | Behavior |
|---------------|----------|
| **False** (git default) | Pass-through immediately. **Zero TypeSafe calls.** No HTTP. Signal JSON unchanged. |
| **True** (env only) | POST `https://api.typesafe.ai/v1/systemone` with a **tiny** JSON state + CIO questions. Stamp `jev` on the MiroFish signal. Does **not** change score / direction / bias / weight / Kelly. |
| On + timeout / HTTP error / missing key / bad body | Log `jev fail-open: …` and continue. Stamp `jev.fail_open=True`. Live Kalshi is **not** blocked. |

Live arm (consume `action` / `veto` / `resize` as a hard gate) stays behind existing fences. This slice never wires Jev into `kalshi_executor` / `master_allocator` execute.

## Flip on / off

Committed constant stays False. Do **not** commit True.

```bash
# on (LaunchAgent / shell only)
export VALEDIA_JEV=1
# keys already in ~/.openclaw/.env
# TYPESAFE_API_KEY or TYPESAFE_AI_API_KEY

# off
unset VALEDIA_JEV
# or VALEDIA_JEV=0
```

Readout: `python3 scripts/integrations/valedia_controls.py` (row `VALEDIA_JEV`) or cockpit `/status.controls`.

Optional env: `VALEDIA_JEV_MODEL` (default `jev-1.13.0`; `jev-latest` allowed), `VALEDIA_JEV_TIMEOUT` (default `3` seconds).

## Call site

One: `mirofish_signal_extractor.process_mirofish_report` after neural + persona attach. Same try/except fail-open pattern.

State sent to Jev is compressed (`overall_bias`, `directions`, `events`, consensus ratio). No markdown, no GraphRAG, no keys.

CIO questions (parallel): `action` (choice: annotate / veto / resize / stand_down), `stand_down`, `report_usable`, `we_are_the_exit` (noul).

## Fence (unchanged)

- `VALEDIA_JEV` stays False in git
- No `POLYMARKET_US_LIVE`, no fee-map gate flip, no wallets, no halt bypass
- No CB/Alpaca writer restore, no weather/oil unfence
- Jev does not place orders and does not compute Kelly

## Tests

```bash
python3 scripts/integrations/test_jev_shadow.py
python3 scripts/integrations/test_controls_readout.py
python3 scripts/integrations/test_h2.py
python3 scripts/integrations/test_nt2.py
```

## Next

Parent: `KALSHI_FEE_MAP_GATE` **runtime** flip per `KALSHI-FEE-MAP-GATE-CHECKLIST.md` (env / LaunchAgent only; do not commit True).
