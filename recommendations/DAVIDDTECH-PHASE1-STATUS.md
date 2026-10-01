# DAVIDDTECH Phase 1 — TradingView paper path

**When:** 2026-09-27 PT (smoke `2026-09-28T03:16:34Z`)
**Executor:** Grok Build, `workspace-v2`
**GROK_EXIT:** 0
**Fence:** `DAVIDD_LIVE=0`. `create_order` false. No CCXT, no wallet, no Cloudflare/public tunnel. Webhook bind `127.0.0.1` only. Passphrase not printed, not in Pine, not in jsonl.

## Smoke

`GET http://127.0.0.1:8787/health` is **Hermes WebUI** (`Server: HermesWebUI`, bind `*:8787`). The passphrase was not posted there.

The paper desk was started with `DAVIDDTECH_WEBHOOK_PORT=8788` and `--stdlib`. `lsof` showed `TCP 127.0.0.1:8788 (LISTEN)` only. Process was stopped after the POST.

| Check | Result |
|---|---|
| `GET /health` | `mode=PAPER/SHADOW`, `create_order=false`, `live=false`, `venue=daviddtech-paper` |
| `POST /tradingview-to-webhook-order` | 200, `create_order=false`, `strategy_id=Waddah_1H_Anchor`, `subaccount=paper`, `size_usd=null` |
| jsonl | `data/daviddtech-paper/decisions.jsonl` 1 → 2 lines |
| last row | `create_order=false`, `live=false`, `paperTrading=true`, `counts_toward_kelly=false` |
| passphrase | no `passphrase` key, secret not in the file, not in the HTTP body, not in server stdout |

## Pine stub

`recommendations/DAVIDDTECH-PINE-PAPER-STUB.pine` (same file in `~/.openclaw/workspace`).

Exact entry alert JSON (`Waddah_1H_Anchor`, paper subaccount). `{{...}}` is filled by TradingView when an alert fires. `entry_price` and `volume` stay numbers.

```json
{"passphrase":"PASTE_PASSPHRASE_IN_TV_ALERT","timestamp":"{{timenow}}","strategy_id":"Waddah_1H_Anchor","ticker":"{{ticker}}","action":"entry","subaccount":"paper","entry_price":{{close}},"volume":{{strategy.order.contracts}},"portfolio_risk":0.5}
```

Exit uses the same object with `"action":"exit"`. The script is an EMA 9/21 stand-in so it compiles. It is not Boilerplate v2.9.4 and not a Waddah engine.

`pine_check` (TradingView translate, no chart write): compiled, 0 errors, 0 warnings.

Phase 1 stopped before inject. CDP `127.0.0.1:9333` was up and the chart page `tq0FHPAY` was open (`BATS:AAPL`, `D`, Volume only), but the Pine editor Monaco did not open. `pine_set_source` replaces the editor buffer. `pine_smart_compile` clicks Save and add to chart. Neither was called then. No alert was created. No webhook URL was set. TradingView was not quit.

## Phase 1b — editor inject (2026-09-27 PT)

CDP 9333 was already listening (`TradingView` pid 36047). No relaunch. TradingView not quit. Chrome on 9222 not touched.

`ui_open_panel` `pine-editor` `open`. Monaco `.monaco-editor.pine-editor-monaco` was mounted (bottom widget bar height was 0; the editor is its own panel, 712×773).

`pine_set_source` wrote the stub (1181 chars). Read-back matches the file. `pine_check` compiled with 0 errors and 0 warnings. That call is TradingView `translate_light` as Guest. It does not save the script and does not add it to the chart.

Monaco also reported one marker, severity 4: Pine v5 is outdated and v6 is recommended. The server compile did not warn. The stub stays `//@version=5`.

Not called: `pine_compile`, `pine_smart_compile`, `pine_save`, `alert_create`. The header showed Add to chart, Save, and Publish script. None of those were clicked. No dialog opened.

After inject the chart is still `BATS:AAPL` `D` with Volume only (`9YEhg5`). `alert_list` was 0 before and 0 after. The editor source has no `http` URL and no `alert()` call. The buffer is unsaved and the strategy is not on the chart.

## Phase 1c — Boilerplate-shaped paper (2026-09-27 PT)

EMA 9/21 is gone. The same file is a paper strategy named `DAVIDDTECH Waddah_1H_Anchor paper`. No DaviddTech Boilerplate source was on disk or in MemPalace. The shape comes from the charter PDF: inverse flip, `maxPercDdTip` 15, session filter (default off), `RiskMTip` 1.5, kill switch, `atp_stop_mult` 3.0 on ATR(22), plus a 5-bar swing buffer on the stop. Entry condition uses the PDF Waddah numbers (Hull 21, EMA 20/40, deadzone TR 100 × 3.7, ADX 14 > 25) with public QQE bands and a McGinley baseline (length 14; the PDF does not set that length). WAE sensitivity 150 is the public constant. `strategy.entry` / `strategy.close` / `strategy.exit` send `alert_message` only. There is no `alert()` call and no webhook URL.

`ALERT_ENTRY` is unchanged: `Waddah_1H_Anchor`, subaccount `paper`, passphrase sentinel `PASTE_PASSPHRASE_IN_TV_ALERT`, `portfolio_risk` 0.5. Exit is the same object with `"action":"exit"`. `Trendhoo_45M` and `TopG_5M_Scalp` are not in this file. `tp1`, `tp2`, and `breakeven` are not emitted.

CDP 9333 was already up (`TradingView` pid 36047). TradingView was not quit. Chrome pid 61515 still owns 9222. The Pine editor was closed; `ui_open_panel` opened it. `pine_set_source` wrote 4697 chars (read-back matches). It did not require Add to chart.

`pine_check` compiled with 0 errors and 0 warnings after `ta.crossunder`, `ta.lowest`, and `ta.highest` were moved out of branches. An earlier check had those three consistency warnings and no errors. Monaco still has one severity-4 hint that Pine v5 is outdated. The script stays `//@version=5`.

Not called: `pine_compile`, `pine_smart_compile`, `alert_create`. Add to chart, Save, and Publish script were not clicked. Chart is still `BATS:AAPL` `D` with Volume only (`9YEhg5`). `alert_list` was 0 before and 0 after.

## Tests

`python3 scripts/integrations/test_daviddtech_paper.py` — 16 pass, 1 skip (FastAPI TestClient). The new test substitutes the stub JSON and checks `validate_payload` plus `create_order=false`.

## Operator steps (localhost only)

Tunnel stays out. TradingView cloud webhooks cannot reach `127.0.0.1`, and a public tunnel is not this slice.

1. Leave Hermes on 8787. Do not POST the passphrase to `http://127.0.0.1:8787`. A Hermes `Server` header means the wrong process.
2. Start the desk with `~/bin/daviddtech-paper-webhook`. It loads `DAVIDDTECH_WEBHOOK_PASSPHRASE` from the workspace `.env` without printing it, forces `DAVIDD_LIVE=0`, defaults `DAVIDDTECH_WEBHOOK_PORT` to 8788, and runs `daviddtech_webhook_server.py --stdlib` on `127.0.0.1`. Stdout is the health URL only. It refuses port 8787.
3. `curl -sS http://127.0.0.1:8788/health` must show `PAPER/SHADOW` and `"create_order": false`.
4. CDP is already on 9333. If it is down: `open -a TradingView --args --remote-debugging-port=9333`. A running app ignores new args; quit only when losing that session is acceptable. Do not touch Chrome on 9222.
5. The Pine editor on `tq0FHPAY` holds the Boilerplate-shaped paper script (unsaved). Do not click Add to chart, Save, or Publish script unless you want it on the chart or in the script library. Do not create an alert. Session filter defaults off. Kill defaults off. MDD halt is 15%.
6. Do not set a webhook URL (not a public host, not Cloudflare, not `127.0.0.1` from TradingView's cloud).
7. The real passphrase is replaced in the alert dialog later, over `PASTE_PASSPHRASE_IN_TV_ALERT`. Do not put it in the `.pine` file. `action` stays `entry|exit|tp1|tp2|breakeven`.
8. A local `curl` POST to `127.0.0.1:8788` is the delivery path until a tunnel is unlocked. Confirm the new jsonl row has `create_order=false` and no `passphrase` key.

## Phase 1d — sister paper engines (2026-09-27 PT)

Two scripts, dual-homed (`recommendations/` here and `~/.openclaw/workspace/recommendations/`). Same JSON keys as the Waddah stub. `subaccount` is `paper`. Passphrase sentinel is `PASTE_PASSPHRASE_IN_TV_ALERT`. No `http` URL. No `alert()` call. `tp1`, `tp2`, and `breakeven` are not emitted.

| File | strategy_id | portfolio_risk | pine_check | chars |
|---|---|---|---|---|
| `DAVIDDTECH-PINE-TRENDHOO-45M-PAPER.pine` | `Trendhoo_45M` | 0.3 (PDF 30% sleeve) | compiled, 0 errors, 0 warnings | 3336 |
| `DAVIDDTECH-PINE-TOPG-5M-PAPER.pine` | `TopG_5M_Scalp` | 0.2 (PDF 20% sleeve) | compiled, 0 errors, 0 warnings | 4658 |

Trendhoo: the PDF publishes the SOL 45MIN stats (win rate 50.54%, profit factor 1.109, 368 trades, net 154.69%) and no indicator lengths. EMA 21/55 is marked as a public stand-in. The stop reuses the Waddah example, ATR(22) × 3 with a 5-bar swing buffer, because the PDF does not publish a Trendhoo stop. Shared gates are unchanged (inverse flip, MDD 15, session default off, RiskMTip 1.5, kill).

TopG: the PDF long is G-Channel green plus HEMA volume flow positive and above its baseline. The short is G-Channel red plus flow declining and under that baseline. Lengths are not in the PDF. G-Channel length 100 is Alex Grover's public default. Holt alpha 0.8, beta 0.2, and the EMA 9 baseline are marked public stand-ins. The fixed stop default 1.0% is a placeholder (the PDF says tight fixed-percent and gives no percent). The limit is that distance × 1.3, the PDF `RnRTip` floor of 1.3–2.05, and the alert action on that exit is still `exit`.

CDP 9333 was already up (`TradingView` pid 36047). TradingView was not quit. Chrome pid 61515 still owns 9222. The Pine editor was closed; `ui_open_panel` opened it. Each file was `pine_set_source` then `pine_check`, one at a time. Read-back matched both files. Monaco's only marker is the severity-4 Pine v5 hint. Scripts stay `//@version=5`.

The editor was then set back to `DAVIDDTECH-PINE-PAPER-STUB.pine`. Read-back is 4697 chars and the strategy name is `DAVIDDTECH Waddah_1H_Anchor paper`.

Not called: `pine_compile`, `pine_smart_compile`, `alert_create`. Add to chart, Save, and Publish script were not clicked. Chart is still `BATS:AAPL` `D` with Volume only (`9YEhg5`). `alert_list` was 0 before and 0 after.

`python3 scripts/integrations/test_daviddtech_paper.py` — 17 pass, 1 skip. The new test checks both sister JSON strings through `validate_payload` and `create_order=false`.

Desk help: operator default while Hermes holds 8787 is `DAVIDDTECH_WEBHOOK_PORT=8788` (`DAVIDDTECH-PAPER.md` and the server docstring). The code constant stays 8787. Hermes was not killed.

## Phase 1e — paper desk operator (2026-09-27 PT)

Smoke `2026-09-28T04:13:08Z`. `~/bin/daviddtech-paper-webhook` started the stdlib server. Stdout was `http://127.0.0.1:8788/health` and nothing else. `lsof` showed `127.0.0.1:8788`. The script stopped that process after the posts. Hermes pid 3499 stayed on `*:8787`. 8788 was closed at the end. `DAVIDD_LIVE=0`. No tunnel.

`scripts/integrations/daviddtech_paper_smoke.sh` POSTed entry and exit for `Waddah_1H_Anchor` (BTCUSDT, portfolio_risk 0.5), `Trendhoo_45M` (SOLUSDT, 0.3), and `TopG_5M_Scalp` (ETHUSDT, 0.2). Subaccount `paper`.

| Check | Result |
|---|---|
| `GET /health` | `mode=PAPER/SHADOW`, `create_order=false`, `live=false` |
| posts | 6 |
| HTTP 200 | 6 |
| `create_order=false` | 6 responses, 6 new jsonl rows |
| jsonl | `data/daviddtech-paper/decisions.jsonl` 2 → 8 |
| passphrase | 0 hits in jsonl, 0 hits in operator stdout/stderr |
| rows | `live=false`, `size_usd=null`, `subaccount=paper`, reason `mapped_strategy` |

## Phase 1f — paper decisions CLI (2026-09-27 PT)

`~/bin/daviddtech-paper-status` reads `data/daviddtech-paper/decisions.jsonl` (workspace-v2 if that file exists, otherwise `~/.openclaw/workspace`). It prints counts by `strategy_id` and `action`, then the last 10 rows as `timestamp strategy_id ticker action create_order live`. Exit 1 if any row has `create_order` true, `live` true, or a passphrase key. The passphrase row is not echoed. The command does not load `DAVIDDTECH_WEBHOOK_PASSPHRASE`. A canary value in that env var did not appear in stdout.

The webhook was not started. 8788 stayed closed. Hermes pid 3499 stayed on `*:8787`. jsonl stayed at 8 lines. `DAVIDD_LIVE=0`. No tunnel.

`scripts/integrations/daviddtech_paper_smoke.sh` is the same bytes in workspace-v2 and `~/.openclaw/workspace`. The status shell is the same bytes at `~/bin/daviddtech-paper-status`, `scripts/integrations/daviddtech_paper_status.sh` (both homes). `daviddtech_paper.py` gained `--status` and was copied to the workspace tree because the two files matched before this slice.

`python3 scripts/integrations/test_daviddtech_paper.py` — 18 pass, 1 skip. The new test covers clean counts, a `live` true row, a passphrase key (value absent from the report), and a missing file.

Sample stdout, exit 0:

```
rows=8 fence=0 path=/Users/burtonchatelain/.openclaw/workspace-v2/data/daviddtech-paper/decisions.jsonl
TopG_5M_Scalp entry 1
TopG_5M_Scalp exit 1
Trendhoo_45M entry 1
Trendhoo_45M exit 1
Waddah_1H_Anchor entry 3
Waddah_1H_Anchor exit 1
2026-09-28T02:03:44.403868+00:00 Waddah_1H_Anchor BTCUSDT entry create_order=false live=false
2026-09-28T03:16:34.227178+00:00 Waddah_1H_Anchor BTCUSDT entry create_order=false live=false
2026-09-28T04:13:08.482288+00:00 Waddah_1H_Anchor BTCUSDT entry create_order=false live=false
2026-09-28T04:13:08.484582+00:00 Waddah_1H_Anchor BTCUSDT exit create_order=false live=false
2026-09-28T04:13:08.485941+00:00 Trendhoo_45M SOLUSDT entry create_order=false live=false
2026-09-28T04:13:08.487502+00:00 Trendhoo_45M SOLUSDT exit create_order=false live=false
2026-09-28T04:13:08.489431+00:00 TopG_5M_Scalp ETHUSDT entry create_order=false live=false
2026-09-28T04:13:08.491250+00:00 TopG_5M_Scalp ETHUSDT exit create_order=false live=false
```

## Next

The open Pine editor holds the Waddah paper stub. The two sister scripts are on disk only. They are not saved to the TradingView library and not on the chart. Burton decides Add to chart. Do not create an alert. Do not set a webhook URL. Start the desk with `~/bin/daviddtech-paper-webhook`. Read the book with `~/bin/daviddtech-paper-status`. Hermes still owns 8787. Tunnel stays OUT. No live order rules in this slice.
