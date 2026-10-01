# TV profit mine — Jackson CDP (paper intel)

**Updated:** 2026-09-29 20:09:00 PDT
**GROK_EXIT:** 0
**Machine:** M1 `b3f456da-e4fc-42d6-8147-a47ad76a8982`
**Actor:** Grok Build
**Chart left on:** `COINBASE:BTCUSD` · 1D · candles · layout `tq0FHPAY` (live pane). Saved layout record `Unnamed` (id 205929620) still says `NASDAQ:AAPL` because this slice did not press Save.
**Fence:** paper intel + chart/Pine inventory. No orders, no `--live`, no venue send. Trade / Buy / Sell / Submit / Confirm were not clicked. No order ticket was opened.

Prior dual-home note: `~/tradingview-mcp-jackson/DUAL-HOME-TV-MCP-2026-09-29.md`.

CLI that actually ran: `TV_CDP_PORT=9333` + `~/.npm-global/bin/tv` → `MemPalace/sources/repos/github/tradingview-mcp/src/cli/index.js`. Jackson repo remains the documented Home A tree. CDP target matched the Jackson note (port 9333, chart `tq0FHPAY`).

---

## 1. CDP proof

```json
{
  "success": true,
  "cdp_connected": true,
  "target_id": "1028DA53D6E457E449358597C54DBC62",
  "target_url": "https://www.tradingview.com/chart/tq0FHPAY/",
  "chart_symbol": "NASDAQ:AAPL",
  "chart_resolution": "D",
  "chart_type": 1,
  "api_available": true
}
```

End-of-slice `tv status` is the same target with `chart_symbol: COINBASE:BTCUSD`.

`tv discover`: 4/6 API paths up (`chartApi`, `chartWidgetCollection`, `bottomWidgetBar`, `replayApi`). `chartApiInstance` and `alertService` unavailable. Replay was not started.

## 2. What the desktop was showing

Logged in as **valedia**. Active layout name on the button: **Unnamed**. One chart tab, one pane (`s` / 1 chart).

### AAPL was the default (read before the symbol change)

| Field | Value |
|---|---|
| Symbol | `NASDAQ:AAPL` Apple Inc. stock |
| Bar | 1D candles. Bar time 2026-09-29 06:30 PDT |
| OHLC | 336.965 / 337.085 / 328.7 / **329.4** |
| Last / volume | **329.4** / 38,478,037 (study Volume **38.48 M**) |
| Day change vs open | **−2.25%** |

Studies on the chart (same three after the switch):

| Study | On AAPL |
|---|---|
| Volume | 38.48 M |
| Overlay input | `FOREXCOM:BTCUSD` (compare). Plotted Close **−9.20%** (study scale, not a cross price) |
| Chart Patterns [Tradevisor] `PUB;9885635715a043afb3863956a8b12df7` | Bull/bear trigger **−100%**. EXP_Entry **12.44%**. EXP_RR **−99.26%**. EXP_TargetHitRate **−98.67%** |

### Left on Coinbase BTC (read after `tv symbol Coinbase:BTCUSD`)

`tv search BTCUSD` resolves the Coinbase spot as `Coinbase:BTCUSD` / `COINBASE:BTCUSD` (type spot, typespecs crypto, defi). A search for the string `COINBASE:BTCUSD` did not return that symbol; it returned LunarCrush “Coinbase Wrapped BTC” fundamentals. The chart command used `Coinbase:BTCUSD`. `chart_ready` came back false on the first ack; the follow-up state/quote/status all show the chart on `COINBASE:BTCUSD`.

| Field | Value |
|---|---|
| Symbol | `COINBASE:BTCUSD` Bitcoin / US Dollar, exchange Coinbase |
| Bar | 1D candles. Bar time 2026-09-29 17:00 PDT (00:00 UTC 2026-09-30) |
| OHLC | 83,638.42 / 83,700 / 83,240.02 / moving |
| Last at 20:07:06 PDT | **83,360.03** (volume **344.46** BTC; study Volume **343** on the prior read) |
| First post-switch last | 83,373.28 |
| Day change vs open | **−0.33%** at 83,360.03 |
| Overlay input | still `FOREXCOM:BTCUSD`. Plotted Close **+7.72%** (legend on the screenshot ~**+7.74%**). Study scale. |
| Tradevisor on BTC | Bull/bear trigger **−100%**. EXP_Entry **0.00%**. EXP_RR **−100%**. EXP_Target **33.39%**. EXP_Stop **−16.69%**. On-chart table: live pattern **none**, best pattern **warming**. |

Screenshot: `recommendations/tv-profit-mine-coinbase-btcusd-2026-09-29.png` (full CDP capture, 560,023 bytes). Source file also at `MemPalace/.../tradingview-mcp/screenshots/`.

### Watchlist (17 rows, list id 348646001, read while chart was still AAPL)

Indices: SPX 7,670.84 (−0.17%), NDQ 30,339.33 (+0.21%), DJI 51,355.13 (−0.26%), VIX 16.04 (−0.19%), DXY 101.346 (−0.03%).

Crypto prints on that read:

| Symbol | Last | Change |
|---|---|---|
| `BITSTAMP:BTCUSD` | 83,324 | −0.36% |
| `BINANCE:BTCUSDT` | 83,368.68 | −0.35% |
| `BITSTAMP:ETHUSD` | 2,672.5 | −0.18% |

Also on the list: AAPL, TSLA, NFLX (JSON last was null; later DOM rows showed TSLA 352.84 −1.29% and NFLX 70.30 +1.55%), `TVC:USOIL` 89.65 (+0.81%), GOLD, SILVER, EURUSD, GBPUSD, USDJPY. USOIL is a watchlist row. Oil stays fenced.

These crypto lasts are **not simultaneous** with the 20:07 Coinbase last. Gaps below are a ceiling on how large the discrepancy looked, not a locked spread.

| Pair of prints | Gap | vs paper fee |
|---|---|---|
| Coinbase 83,373.28 vs Binance USDT 83,368.68 | **$4.60 · 0.55 bps** | inside 10 bps |
| Coinbase 83,360.03 (20:07) vs that same Binance print | **$8.65 · 1.0 bps** | inside 10 bps |
| Coinbase 83,373.28 vs Bitstamp 83,324 | **$49 · 5.9 bps** | inside 10 bps |

Paper fee assumption in `scripts/integrations/valedia_config.py`: `CRYPTO_FEE_PCT = 0.001` (10 bps, used as maker and taker), `CRYPTO_SLIPPAGE_PCT = 0.0005` (5 bps). A round trip that pays fee + slip both ways is **30 bps**. Every gap above is smaller than one fee.

Depth labels (read-only `tv ui find`, not a click): one text node contained `83,378.09` Sell, token `0.01`, `83,378.10` Buy. If those two prices are the visible book, the shown gap is **$0.01** (~0.01 bps). `tv ui find Submit` count **0**.

### Pine

`tv pine list`: **0** saved scripts (`internal_api`). Pine editor **closed** (`ui-state`). No `pine compile`, no `pine analyze`, no `pine open`. The Tradevisor study is an on-chart published script, not an open editor buffer.

### Alerts, layouts, panes, tabs

- Alerts: **0** (`internal_api`). None created.
- Layouts: 1, `Unnamed`, metadata symbol still `NASDAQ:AAPL` after the live pane moved.
- Panes after the switch: 1, active `COINBASE:BTCUSD` D.
- Tabs: 1, chart `tq0FHPAY`.

## 3. Broker surface (visible labels only)

Bottom account manager was already open. Nothing in this slice clicked Trade, Buy, Sell, the broker name, Positions, or Orders.

| Name | Visible? | Where |
|---|---|---|
| **Coinbase Advanced** | Yes | Account-manager header. Button aria-label `Close account manager`. Account row text: **Default** / **USD**. |
| Balance | Yes | DOM innerText **`25.51729553068945 USD`** |
| Futures PnL | Yes | **0.00 USD** |
| Perpetuals PnL | Yes | **0.00 USD** |
| Positions | Yes, empty | “There are no open positions in your trading account yet” |
| Orders / Order history / Account summary / Notifications log | Tab labels visible | Not opened |
| **FOREX.com** | Yes, as a **chart overlay legend** | `FOREXCOM:BTCUSD` compare study. Not the account manager. |
| Alpaca | No | `tv ui find` count 0 |
| Webull | No | count 0 |
| Kraken | No | count 0 as a panel. `Kraken:BTCUSD` exists in symbol search only. |
| OKX | No | count 0 as a panel. `OKX:BTCUSD` exists in symbol search only. |

Top bar still shows a **Trade** button and **Trading options**. Not clicked. The panel is labeled Coinbase Advanced, not “Paper”. Treat the $25.52 as a brokerage balance displayed inside TradingView. It is not a Valedia writer and it was not used to send an order.

Cash vs clip: 0.01 BTC at 83,360 is about **$834**. Visible USD **$25.52** is about **3%** of that clip. This balance cannot fund a 0.01 BTC spot ticket. This slice does not ask for a deposit.

`tv search BTCUSD` also lists Binance, Bitstamp, CRYPTO, FOREXCOM, Kraken, OKX, OANDA, and others. Those are symbol-search hits. The open broker panel is Coinbase Advanced only.

## 4. Lane map (workspace-v2, twin on accel-ornith)

| Lane | What this chart changes | Cite | Fence that still holds |
|---|---|---|---|
| **crypto-paper** | Primary consumer. Allowlist is `BTC-USDT` / `ETH-USDT` on public Binance klines. Chart left on **Coinbase USD**, watchlist also has **Binance USDT**. Same asset, different quote currency and venue. | `recommendations/CRYPTO-PAPER-DESK.md`, `PROFIT-PATHS-INVENTORY.md` (crypto paper desk = SHADOW, no wallets, no `create_order`) | `--live-data` is a public GET. Do not copy paper fills into the forward-test ledger as live. Do not restore the Coinbase writer from this desk. |
| **Coinbase / Alpaca / Kraken** | TV can *show* Coinbase Advanced. Valedia writers stay `none`. | `PROFIT-PATHS-INVENTORY.md` Coinbase row (FENCED, `place_market_order` missing). `STAGED-ON-KALSHI.md`: CB/Alpaca `SKIP_WRITER`, Kraken `none`. | No writer restore. No `--live`. |
| **OKX / eToro / oil** | OKX is a search hit. USOIL is a watchlist row. | `OKX-PHASE1-STATUS.md`, `ETORO-PAPER.md`, `KILL-SWITCH.md` | No OKX_LIVE, no eToro flip, no oil unfence. |
| **POLY** | No Polymarket surface on this chart. | `POLY-LIVE-PREFLIGHT.md` (2026-09-22: `POLYMARKET_US_LIVE` off, writer `none`). `PROFIT-PATHS-INVENTORY.md` Poly row DRY/SHADOW. | Do not pipe a TV pattern into `create_order`. |
| **Kalshi** | Not on this chart. Staged live book is Kalshi-only. | `STAGED-ON-KALSHI.md`, `KILL-SWITCH.md` | This slice does not stage or send Kalshi orders. |
| **Ornith / ACCEL** | No model load. Twin copy of this note is the handoff Scout/Decider can read. | `GROK-BUILD-LANE.md`, `ORNITH-LOAD-POLICY.md` | Inference/config only. No trading-lever flip. |

## 5. Dual-home — what each owns for Scout / Decider

| | Home A — Jackson CDP | Home B — official `mcp.tradingview.com` |
|---|---|---|
| Status this session | **Proved.** `tv status` on `127.0.0.1:9333`, chart `tq0FHPAY`. | **Not wired.** Not called. |
| This Grok host | CLI did the inventory. Catalog also lists a `tradingview` MCP whose tools are Desktop-chart tools (`chart_get_state`, `tv_health_check`). That server is the CDP family. | No official Streamable-HTTP server in the catalog. Do not claim OAuth or Essential+ tools. |
| Scout should read | The one open chart: symbol, TF, study values, watchlist, visible broker labels, screenshot. | Later, after Burton completes Essential+ OAuth: quotes/history, screener, news, calendars across BTC/ETH/SOL. |
| Decider should treat it as | Evidence of what the desktop shows. The Trade button is a UI control, not an authorization. | Absent until the connector is green and tools are enumerated. |
| Dies when | TradingView Desktop or port 9333 drops. Cloud agents cannot see `127.0.0.1:9333` without a tunnel that does not exist here. | It has no chart layout, no Pine editor, no on-chart broker panel (per the 19:50 dual-home note and the 2026-09-16 TV blog). |
| Orders | None in this CLI pass. | Official server has no order-execution tools, per the dual-home note. Still not a reason to flip Valedia live. |

Install reference already on disk: `recommendations/TRADINGVIEW-MCP-INSTALL.md` (Jackson, `TV_CDP_PORT=9333`, no venue orders).

## 6. Top 5 actions

Ranked for fee-true paper profit. None of these send an order.

1. **Fee-gate the crypto-paper tape against this Coinbase print.** Owner: **Scout**. Home: **A** for the quote; the sim is `scripts/integrations/crypto_paper_run.py --live-data --pair BTC-USDT` (public klines). Fence: no wallet, no `create_order`, Coinbase writer stays `none`. Lever: paper fee is 10 bps + 5 bps slip (30 bps round trip). The Coinbase vs Binance/Bitstamp gaps seen here are 0.6–5.9 bps. Intents that do not clear the fee belong in the would-have log, not in a ticket.

2. **Label the venue on every BTC number before Decider reads it.** Owner: **Scout** logs it, **Decider** rejects a mismatched book. Home: **A**. Fence: read-only. Lever: the chart is `COINBASE:BTCUSD`, the overlay input is `FOREXCOM:BTCUSD`, the watchlist also has Bitstamp USD and Binance USDT, and the account manager is Coinbase Advanced. A +7.7% overlay plot is the study scale. It is not a FOREX.com premium over Coinbase.

3. **Keep Tradevisor in shadow.** Owner: **Decider**. Home: **A**. Fence: no compile, no alert create, no replay trade. Lever: on this BTC daily bar the bull/bear triggers are −100%, EXP_Entry is 0, live pattern is none. Paying 10 bps to act on “warming” has no expectancy in the study’s own plots.

4. **Leave the $25.52 Coinbase Advanced balance untouched.** Owner: **Burton** (only Burton funds an account or restores a writer). **Decider** refuses the Trade button. Home: **A** is the display; the fence is Valedia runtime (`KILL-SWITCH.md`, `STAGED-ON-KALSHI.md`). Lever: visible cash cannot fund a 0.01 BTC clip (~$834). Quick profit on this balance is a paper expectancy, not a live Coinbase send. Kalshi remains the staged live book and is a different surface.

5. **Home B stays a Burton connect, then a Scout screener.** Owner: **Burton** to OAuth Essential+ on `https://mcp.tradingview.com/mcp`; **Scout** uses it only after tools enumerate. Home: **B**. Fence: no live flip, no invented tool calls. Lever: one CDP chart cannot screen SOL/ETH/BTC together. Official MCP would. It is not connected tonight. POLY stays on `POLY-LIVE-PREFLIGHT.md` (LIVE off, writer `none`); this TV pass does not feed it.

## 7. Fences held

No orders. No `--live`. No Coinbase Advanced / Alpaca / Webull / Kraken / FOREX.com / OKX send. No OKX_LIVE, oil, or eToro flip. No Poly LIVE. No writer / wallet / trading-lever change. No click on Trade, Buy, Sell, Submit, or Confirm. No Pine compile. No alert create. No layout save. Valedia runtime still owns any future live order.

## 8. Residual

- Reload of layout `Unnamed` can put the chart back on `NASDAQ:AAPL`. Live pane is `COINBASE:BTCUSD` until then. Saving the layout is a Burton/desktop choice; this slice did not save.
- Watchlist crypto lasts and the Coinbase last are minutes apart. Do not book the $4–$49 gaps as an arb.
- `alertService` API down; alert list still returned 0 via `internal_api`.
- Official TradingView MCP is still unwired.
- Raw CLI JSON from this pass is under `/tmp/tv-profit-mine/` (ephemeral). Durable proof is this file plus the PNG.
