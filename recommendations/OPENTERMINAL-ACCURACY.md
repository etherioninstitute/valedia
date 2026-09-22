# OPENTERMINAL-ACCURACY — glance must match live Valedia

**Queued after FOMO-RADAR-DOSSIER.** GET-only. No order buttons. No live flips from UI.

## Gaps (2026-09-21 audit)

1. **Server down:** nothing on `:8765` — stale `openterminal-*` screens; restart `valedia_cockpit.py --listen`.
2. **Treasury stale:** panel reads cached `treasury_status.json` only — no live Kalshi/Poly GET (cash can lie).
3. **Controls card:** must render *all* `controls_snapshot()` rows (incl. `VALEDIA_JEV`, `POLY_LOOP_*`, fee-map env) with ON/OFF + source badge (`env` vs `const`).
4. **POLY-LOOP desk:** show `data/poly-loop/last_run.json` summary (n proposals, skip_reasons, live flag) — not just static consts.
5. **Version identity:** stamp version+sha+branch+dirty + truthful LIVE/DRY/SHADOW/FENCED from halt/writers/live flags.
6. **Connection strip (optional thin):** LiteLLM/Bionic/RuFlo/Kalshi ping OK/FAIL — alert-only, no auto-heal from UI.
7. **Tests:** extend `test_openterminal_valedia.py` / controls HTML fixtures so missing snapshot keys fail CI.

## Listen

Not cron. The page does not restart anything.

```bash
cd ~/.openclaw/workspace-v2
python3 scripts/integrations/valedia_cockpit.py --listen
# http://127.0.0.1:8765/     JSON: /status
```

If nothing answers on `:8765`, that command is the restart.

## Honesty (0.56.0)

- Kalshi cash: live GET when `KALSHI_API_KEY_ID` is set (Kalshi has no separate HTTP flag; the key is the existing read gate). Poly cash: live GET only when `POLYMARKET_US_HTTP` is already on. Otherwise the cache is `CACHED` or `STALE` with age. Never ACH, never a wallet move.
- Controls card: every `controls_snapshot()` row, ON/OFF for booleans, source badge `env` / `const` / `file`.
- POLY-LOOP card: `data/poly-loop/last_run.json` (n, skip_reasons, live).
- Identity: version + sha + branch + dirty. Mode is one of LIVE / DRY / SHADOW / FENCED from halt, live flags, and writers.
- Connection strip: LiteLLM `:4000`, Bionic `:1234`, RuFlo `:8810`, Kalshi `:443` — OK/FAIL ping only.

## Success

Burton opens `http://127.0.0.1:8765/` and sees the same truth as `valedia_controls.py` + current Poly last_run + fresh-enough balances (opt-in live GET behind existing HTTP flags, never create_order).
