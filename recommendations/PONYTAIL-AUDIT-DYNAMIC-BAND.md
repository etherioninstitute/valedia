# Ponytail audit — DYNAMIC-BAND MiroFish self-adjust (0.30.0)

**Mode:** `/ponytail-audit` on the DYNAMIC-BAND diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `valedia_config` knobs, `mirofish_accuracy_ledger` band math, `test_dynamic_band.py`.
**Fence:** `VALEDIA_SELF_ADJUST=False` in live config. No writers, no SPOT GET, no NAUTILUS_LIVE, no crontab.

## Applied this session

`shrink:` no EMA / interpolation. Below bar → floor 0.05; above bar → top 0.25; enrich +0.02 then clamp to band+ceiling. [_band_weight]

`yagni:` second env for AUTO_RAISE. Flip is `VALEDIA_SELF_ADJUST` only; constant `MIROFISH_AUTO_RAISE` stays False. **Not shipped.**

## Findings (biggest cut first)

`yagni:` flipping `VALEDIA_SELF_ADJUST=True` in live `valedia_config`. Replacement: default False; tests toggle in-process then restore. **Not shipped.**

`yagni:` opening venue writers / SPOT GET / NAUTILUS_LIVE on the master flip. Replacement: comment + test that writers stay `none`. **Not shipped.**

`yagni:` writing `MIROFISH_WEIGHT` from the ledger. Replacement: runtime `effective_weight()` only; `recommend_weight` `applied=False`. **Not shipped.**

`yagni:` new fusion key / second Kelly. **Not shipped.**

## Not cut (spec)

- Seatbelted 10/15 path when flip is off (`test_slice13` / `test_slice18` still PASS).
- Caps UNPROVEN 0.10, band 0.05–0.25, ceiling 0.30, bar 0.55.
- Dormant≠dead note on pause TTL + neural enrich under flip.

**net:** ~40 lines band math in the existing ledger + knobs in config. 0 deps. Lean enough. Ship.
