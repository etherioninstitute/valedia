# POLY LIVE preflight — 2026-09-22 ~01:12 PT

**Scope:** readiness check only. Did **not** set `POLYMARKET_US_LIVE=1`. Writer `polymarket` stays `none`.

## Results

| Check | Result | Evidence |
|-------|--------|----------|
| Keys (`POLYMARKET_KEY_ID` + `POLYMARKET_SECRET_KEY`) | **GREEN** | present in `~/.openclaw/.env` |
| `POLYMARKET_US_HTTP` persistent | **YELLOW** | unset in `.env`; smoke forces `=1` for the run |
| `POLYMARKET_US_LIVE` | **GREEN (still off)** | unset / False |
| Retail identity / balances | **GREEN** | `api.polymarket.us` USD buyingPower **75.552**, positions 0 |
| `preview_order` qty=1 | **GREEN** | slug `aec-nfl-lac-ten-2025-11-02`, kind=preview, filled=false |
| `create_order` while LIVE=0 | **GREEN** | raises `LiveWriteBlocked` |
| Informed-before-create | **GREEN** | const True (env cannot disable) |
| Halt | **GREEN** | False |
| Writer polymarket | **GREEN** | `none` |
| Cockpit Poly cash card | **YELLOW** | UNREAD / file missing until HTTP+refresh path writes it |
| POLY-LOOP paper freshness | **YELLOW** | last_run stale (`no_resolution`×20 @ 2026-09-21); fresh `run()` hit `ModuleNotFoundError: requests` in default `python3` |

## Smoke command

```bash
cd ~/.openclaw/workspace-v2/scripts/integrations
python3 poly_us_smoke.py   # forces HTTP=1 LIVE=0
```

PASS at 2026-09-22 PT.

## Before Burton flips LIVE

1. Optionally add `POLYMARKET_US_HTTP=1` to `~/.openclaw/.env` (reads only; not LIVE).
2. Refresh POLY-LOOP with an interpreter that has `requests` (or install) so paper proposals are current.
3. Then env `POLYMARKET_US_LIVE=1` for qty=1. Do **not** commit True. Do **not** flip writer.

## North star note

Max-smarts = researched / official FV before create on **every** prediction class (weather NOAA, sports attestation, rates/book, Poly rules FV). MiroFish seasons; it does not replace source FV. Live unfence stays Burton-only after fee-true sample.

## Runtime flips (2026-09-22 ~01:14 PT) — Burton: whatever best for profit

Wrote to `~/.openclaw/.env` only (not git):

- `POLYMARKET_US_HTTP=1`
- `POLYMARKET_US_LIVE=1` (qty=1 ladder; writer polymarket stays `none`; informed stays on)
- `KALSHI_EV_PREFLIGHT_GATE=1`
- `KALSHI_MAKER_FIRST=1`

Backup: `~/.openclaw/.env.bak-pre-poly-live-*`
Verified: `polymarket_us_live_enabled() True`, client.live True. No create_order placed by this flip.
