# Valedia trade-status accuracy (Slice 21 Part B)

**Scope:** User-facing trade status — Signal (`signal_send` / `openclaw message send`), brain cycle summaries, allocator/sports/weather/quant/arb banners, settle, watchdog, kalshi alerts/audit, `pnl_tracker` / `pnl_run`, shadow vs fill wording, cron labels.
**Fence:** No `--live`, no venue orders, no wallet/spend, no key changes, no VENUE_WRITER / REBALANCER_AUTOEXEC / SPOT_RECONCILE_HTTP / NAUTILUS_LIVE flips.
**Worktree:** `~/.openclaw/workspace-v2`. Live crontab inspected (not rewritten).
**Slice 21 code:** messaging/label only (+ settle_run loss fee to match `sports_engine.check_settlements`). Tests: `scripts/integrations/test_slice21.py`.

Signal protocol (`skills/valedia/signal-protocol.md`) still wants exactly `Invested:` and `Profit:`. Live already violates that (morning brief, pnl_run, watchdog). Label fixes keep the `Profit:` prefix.

---

## Ranked findings

| # | Source | Claim | Truth | Severity | Fix |
|---|---|---|---|---|---|
| 1 | `unified_brain.stage_ere_safety` → WATCH `ere={status}` | `status: "OK"`, `safe: True` | Exception (missing `cumulative_extremes`) no-ops; multiplier stays 1.0. Fail-**open** for size, fail-**closed** for the safety check. | **lie** | **Done (v2):** status `FAIL_CLOSED` / `INSUFFICIENT_DATA`. `safe`/`multiplier` unchanged (sizing path). Slice 22: actually bind ERE. |
| 2 | `unified_brain` summary / BUY/SELL lines | `EXECUTED: N trades` / `>> BUY` after `spot_ack_ok` | `spot_ack_ok` = venue **accepted** (`forward_test_ledger.py:137`), not fill qty+px. Incomplete → FTL `UNRECONCILED`. | **lie** | **Done (v2):** `ACKED: N orders (POST-ack, not fill-confirmed)` / `>> BUY ACK` / `>> SELL ACK`. |
| 3 | `settle_run` Signal | `Profit: {pnl} … settled` as realized | Sports JSON book, not FTL. Losses were `-cost` **without fee** (sports_engine loss path **includes** fee). | **lie** | **Done (v2):** keep `Profit:` prefix; body `sports-log not FTL`; loss `-(cost+fee)`. Slice 22: read FTL. |
| 4 | `settle_run` arb Signal | `Arb profit: …` | `latency_arb` log; execute path sets `EXECUTED` on POST-ack (`latency_arb.py:360`) with no fill extract. Live cron is dry-run so this is usually empty. | **misleading** | **Done (v2):** `Profit: … arb-log not FTL`. |
| 5 | `master_allocator.run` banner + EXECUTION | `[LIVE]` + ✅ without status text | `allocator_run.py:11` `dry_run=False` — Kalshi **is** live. UNRECONCILED was painted ❌ with no status. Coinbase `place_market_order` missing → FAILED. Weather/oil `SKIP_LIVE_FENCE` under a LIVE banner. | **misleading** | **Done (v2):** print `status` (`EXECUTED`/`UNRECONCILED`/`DRY_RUN`/`SKIP_*`). Banner stays `[LIVE]` when `dry_run=False` (true for Kalshi). |
| 6 | `pnl_tracker.simple_pnl_message` / `pnl_run` Signal | `Realized P&L: … (Kalshi, net of fees)` | Broker `realized_pnl_dollars` when nonzero; **fallback** `exp if won else -exp` is not fee-true. Not FTL. Extra Signal type vs protocol. | **lie** (fallback) / **misleading** (header) | **Done (v2):** `Kalshi broker P&L: … (pnl_tracker.json, not FTL)`. |
| 7 | `kalshi_executor.opportunity_report` / `kalshi_alert.py` (live 07:00 MORNING BRIEF) | `💰 VALEDIA PROFIT SCAN` + `Estimated profit` / `Deposit $X → ~$Y expected profit` | Scan + modeled EV. `execute_trade(..., dry_run=True)` inside the report. Can Signal if “hot”. | **misleading** | **Done (v2):** `OPPORTUNITY SCAN [not fills / not realized P&L]`. Slice 22: stop sending EV to Signal or prefix `SCAN:`. |
| 8 | `kalshi_executor.audit` / live `kalshi_audit.py` 23:30 | `{n} trades … ${profit} profit` | `potential_profit` summed for `status==EXECUTED` (which may be POST-ack). Not settled, not FTL. | **lie** | **Done (v2):** `expected (not realized)`. |
| 9 | `quant_engine.run` / live `quant_run.py` 6h | `Kelly: quarter` + `Kalshi: ACTIVE` | Uses `KELLY_SAFETY_FRACTION` (half). Module does **not** place orders. | **lie** | **Done (v2):** `[SCAN-ONLY]` / `Kelly: half (display, no orders)` / `Kalshi: SCAN`. |
| 10 | `sports_engine.run` / `sports_run.py` | `[LIVE]` + `EXECUTED` + Signal `Invested:` | Cron `dry_run=False`. `EXECUTED` only if `extract_kalshi_fill.complete`. `Invested:` only on EXECUTED, not DRY_RUN/UNRECONCILED. RECORD line is **sports JSON**, not FTL. | **ok** (fill/Invested) / **misleading** (RECORD) | Slice 22: RECORD from FTL. No Slice 21 banner change (it is live). |
| 11 | `weather_arb.run` | `[DRY]` even if `--mode execute` | Forced `dry_run=True`; never `create_order`. | **ok** | None. Tests lock it. |
| 12 | Shadow Intents (`nautilus_shadow_*`, `intel_opportunity_gatherer`, allocator INTEL/VENUE SHADOW lines) | `never-dispatchable` / `[shadow-…]` | `live_flag=False`, `dispatchable=False`, `ledger_source=shadow`. No `filled` string. | **ok** | None. Test: shadow files must not say `filled`. |
| 13 | `rebalancer.py` | shadow only unless AUTOEXEC | Default `REBALANCER_AUTOEXEC=False`; lines say `(shadow only; …)`. | **ok** | None. |
| 14 | Brain ALPACA banner | `ALPACA (LIVE)` vs `(PAPER)` | `alpaca_executor.IS_LIVE = True` hardcoded. `portfolio_monitor` uses `ALPACA_PAPER` default **true**. | **lie** (one of the two) | Slice 22: one flag. Do not flip keys this slice. |
| 15 | `fund_pipeline` | `Coinbase: $cash, ~$total total` | BTC marked `* 70000`. | **misleading** | Slice 22: drop fake mark or use last price. |
| 16 | Cron labels vs files | Installer would announce EMERGING/SENTIMENT/ALPHA INTEL | **Live crontab does not.** Those `.py` names are gone (`.DEPRECATED` only). Live **does** announce LATENCY ARB (dry), QUANT ENGINE (scan), MORNING BRIEF (EV), MARKET SCANNER. | **ok** today / **lie** if installer run | Do not run `install_cron.sh`. Slice 22 live-crontab edit (Burton). |
| 17 | `watchdog.py` Signal | `WATCHDOG ALERT` / cron log stale | Points at **live** runner/log. Comment “30 min from latency arb” matches live arb cron. Extra Signal type. | **ok** (when stale is real) | None. |
| 18 | `cron_runner_mac.sh` | Would prefix `[LABEL] $OUTPUT` | `should_send` always `return 1`. No Signal from runner. | **ok** | None. |
| 19 | `latency_arb` execute mode | `EXECUTED` + docstring `LIVE trading` | Default cron dry-run. Execute path POSTs then labels EXECUTED without fill fields. | **lie** if execute used | Slice 22: `UNRECONCILED` until fill (don’t break dry-run settle filter). |
| 20 | Allocator Alpaca/Coinbase `status = "EXECUTED"` | Fill | Sets EXECUTED on `place_order` / missing `place_market_order` catch. No `extract_spot_fill.complete` check (Kalshi path **does** check). | **lie** (Alpaca) | Slice 22 messaging: UNRECONCILED unless fill complete. |

---

## Concrete inaccurate strings (quoted)

Pre-Slice-21 (still on **live** until fenced deploy):

- `"status": "OK"` with `"safe": True` on ERE exception (`unified_brain.stage_ere_safety`)
- `"EXECUTED: {n} trades (Alpaca ${…}, Coinbase ${…})"`
- `"  >> BUY  ${…}"` after POST-ack
- `"Profit: {pnl:+.2f} ({w}W/{l}L settled)\nTotal: …"` from sports log, fee-less losses
- `"Arb profit: {net_pnl:+.2f} ({wins}W/{losses}L)"`
- `"💰 VALEDIA PROFIT SCAN ({ts})"`
- `"  💰 Deposit ${extra} → ~${extra_profit:.2f} expected profit …"`
- `"{s}: {n} trades, ${cost} deployed, ${profit} profit ({roi}% ROI)"` (potential)
- `"Kelly: quarter"` / `"Kalshi: ACTIVE"`
- `"Realized P&L: ${net:+,.2f}  (Kalshi, net of fees)"`
- Allocator EXECUTION line with no status (UNRECONCILED looked like hard fail)

Post-Slice-21 (v2 only; live not rewritten this slice):

- ERE no-op → `FAIL_CLOSED` (not `OK`)
- Brain → `ACKED: … (POST-ack, not fill-confirmed)`
- Settle Signal → `Profit: … sports-log not FTL` (prefix kept)
- Quant → `[SCAN-ONLY]` / `Kalshi: SCAN`
- P&L card → `Kalshi broker P&L: … (pnl_tracker.json, not FTL)`
- Opportunity header → `OPPORTUNITY SCAN [not fills / not realized P&L]`

Still true / still live:

- `🏀 SPORTS ENGINE … [LIVE]` and `🧠 MASTER ALLOCATOR … [LIVE]` when cron `dry_run=False` — **Kalshi writes are live**. Not a banner lie.
- `🌡️ WEATHER ARB … [DRY]` — forced.
- `Invested: {cost} on {n} games` — only fill-complete sports.

---

## Slice 22 plan (messaging/label unless a bug blocks truth)

Still no fence lifts. **Shipped in v2** (`test_slice22.py`). Live crontab/scripts not rewritten this slice.

1. **FTL settlement Signal.** **Done.** `settle_run` `Profit:` body from fee-true FTL (`fee_true_settlements` / `ftl_profit_signal`). Sports JSON = print view only. UNRECONCILED/paper/backfill/shadow cannot appear as P&L.
2. **Allocator spot status.** **Done.** Coinbase/Alpaca: `spot_fill_status` → `UNRECONCILED` unless `extract_spot_fill.complete`. Missing `place_market_order` stays FAILED (writer not added). Alpaca `place_order` still missing → FAILED.
3. **Live crontab (Burton).** **Documented only:** `recommendations/CRONTAB-STAGGER-S22.patch` (allocator `:20`). Do not execute `install_cron.sh`. Optional morning brief log-only noted in the patch.
4. **Alpaca one flag.** **Done.** `alpaca_is_live()` from env `ALPACA_PAPER` (unset → live, executor default). Brain + portfolio_monitor + `_base()` share it. No key edits.
5. **`latency_arb` POST-ack** → **Done.** `ack_status` → `UNRECONCILED` without fill. `arb_run.py` stays `dry-run`.
6. **ERE bind** — **deferred.** Porting `cumulative_extremes` is small but would silently change fail-open sizing (`multiplier` 1.0 → live ERE cuts). Status stays `FAIL_CLOSED`. Later slice, not messaging.

---

## Tests that prove labels (`test_slice21.py`)

- `dry_run` ternary is `[DRY]` not bare `[LIVE]`; weather `execute_weather_trades(dry_run=False)` still `DRY_RUN`.
- Shadow modules contain no `filled`.
- `format_settled_signal` / `format_arb_signal` start with `Profit:` and contain `not FTL`, never `realized`.
- `simple_pnl_message` does not say `Realized P&L`.
- ERE long series → `FAIL_CLOSED` not `OK`; short series → `INSUFFICIENT_DATA`; `safe`/`multiplier` unchanged.
- Brain source has `ACKED:` / `POST-ack, not fill-confirmed`, not `EXECUTED: {len(actions)} trades`.
- Allocator `run` prints `UNRECONCILED`.
- Quant `run` is `[SCAN-ONLY]`, not `Kelly: quarter` / `Kalshi: ACTIVE`.
- Opportunity report is not `VALEDIA PROFIT SCAN`; audit says `expected (not realized)`.

Run: `python3 scripts/integrations/test_slice21.py`

---

## What this slice did / did not do

**Did:** label/status strings + settle loss fee to match sports_engine; tests; two reports.
**Did not:** delete modules, flip flags, change Kelly/fee/ERE multiplier behavior, rewrite live crontab, deploy to live scripts.
