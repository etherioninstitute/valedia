# Ponytail audit — DAVIDDTECH Phase 1

**Mode:** `/ponytail` full. Paper path only.
**Fence:** `DAVIDD_LIVE=0`. `create_order` false. No CCXT, no wallet, no public webhook, no tunnel.

## Applied

`reuse:` `daviddtech_paper` + stdlib webhook from SUBSUME-1. No second server, no FastAPI install.

`reuse:` payload keys already enforced by `validate_payload`. The stub's `ALERT_ENTRY` is that object for `Waddah_1H_Anchor` / `paper`. One `str.replace` flips `entry` to `exit`.

`check:` `test_pine_stub_alert_json` substitutes TradingView placeholders and runs `validate_payload`. Live POST on `127.0.0.1:8788` appended jsonl with `create_order=false` and no passphrase field.

## Skipped

`yagni:` killing Hermes or moving the code default off 8787. Hermes owns `*:8787`. Posting the secret there would hit the wrong process. Smoke used 8788. Add a port move when Burton picks which listener keeps 8787.

Phase 1b: Monaco was open, so `pine_set_source` only. `pine_check` compiled again (0 errors). Skipped `pine_smart_compile` / `pine_compile` (they click Save and add to chart or Add to chart), Save, and Publish script. Add to chart when Burton asks.

`yagni:` TradingView alerts, webhook URLs, Cloudflare. **Not created.**

`yagni:` Boilerplate v2.9.4 and the real Waddah engine. EMA 9/21 is marked `ponytail:` in the stub. Swap the condition, keep `ALERT_ENTRY`.

`yagni:` a repo-wide CBM reindex (index date 2026-09-23; daviddtech files `not_tracked`). Source was read. Reindex when the graph needs those symbols.

`yagni:` git commit. **Not committed.**

## Findings

`shrink:` one Pine file, one alert string, one test. Three-engine checklist stays operator work.

**net:** localhost paper POST proven; alert JSON matches the desk; passphrase stays out of Pine and jsonl. Phase 1b left the stub in the editor buffer, not on the chart, alerts still 0. `DAVIDD_LIVE=0`.

## Phase 1c

`reuse:` the same `ALERT_ENTRY` string and `str.replace` exit. `test_pine_stub_alert_json` still passes.

`shipped:` paper gates from the PDF (inverse flip, MDD 15, session default off, RiskMTip 1.5, kill, ATR(22)×3 stop, 5-bar swing buffer) and the PDF Waddah numbers (Hull 21, EMA 20/40, deadzone TR 100×3.7, ADX 14>25). QQE and McGinley are the public formulas. McGinley length 14 is an input because the PDF does not set it. WAE sensitivity 150 is the public constant.

`yagni:` `Trendhoo_45M`, `TopG_5M_Scalp`, trail, `tp1`, `tp2`, `breakeven` alerts. Add those in another paper script when asked.

`yagni:` `pine_smart_compile`, Save, Publish, Add to chart. `pine_set_source` did not click Add to chart.

`yagni:` a v6 rewrite (Monaco hint only) and a CBM reindex. Not committed.

**net:** one paper script, same JSON, editor buffer only, alerts 0. `DAVIDD_LIVE=0`.

## Phase 1d

`reuse:` the Waddah gate block, `f_alert`, and `str.replace`. Two files because the charter lists two `strategy_id`s. `portfolio_risk` 0.3 and 0.2 are the PDF sleeves.

`shipped:` Trendhoo EMA 21/55 marked as a public stand-in (PDF has stats, no lengths). TopG uses the PDF long/short sentence on a public G-Channel (length 100) and a public Holt volume flow. Fixed stop 1.0% is a labeled placeholder. Limit uses the PDF RnR floor 1.3 and still sends `action` `exit`.

`yagni:` Boilerplate v2.9.4 source, `tp1`/`tp2`/`breakeven` strings, a v6 rewrite, Add to chart, Save, Publish, `alert_create`, and moving `DEFAULT_PORT` off 8787. Docs tell the operator to set `DAVIDDTECH_WEBHOOK_PORT=8788`. Hermes stays up.

`yagni:` a CBM reindex (index 2026-09-23, daviddtech files `not_tracked`) and a swarm. One editor, one writer.

**net:** two paper scripts on disk, Waddah back in the editor, alerts 0, chart unchanged. `DAVIDD_LIVE=0`.

## Phase 1e

`reuse:` `daviddtech_webhook_server.py --stdlib`. The operator only loads the passphrase, forces `DAVIDD_LIVE=0`, refuses 8787, and prints the health URL. The smoke is one shell script plus stdlib `urllib`.

`check:` one live run. posts=6, http_200=6, create_order_false=6, jsonl 2→8, passphrase_hits=0. A second start/stop did not POST and left the jsonl at 8.

`yagni:` a new server, FastAPI, a port move off the 8787 constant, killing Hermes, CDP, `alert_create`, and a tunnel. Add those when Burton asks.

**net:** one bin wrapper, one smoke, desk stopped, Hermes still on 8787. `DAVIDD_LIVE=0`.
