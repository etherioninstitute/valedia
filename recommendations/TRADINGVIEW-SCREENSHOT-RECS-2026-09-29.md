# TradingView screenshot audit + recommendations

**Updated:** 2026-09-29 19:37 PT  
**Sources:** Burton screenshots (Indicators → Community Top + Trending; Coinbase Advanced trading panel on AAPL daily); TradingView broker/docs pages; local Mac Studio Jackson CDP MCP install note; Valedia fence/Davidd paper charters.  
**Hard fence:** Chart / Pine / paper intel only. No `--live`, no Coinbase/venue orders, no wallet/spend, no alert→public webhook until Burton unlocks. Valedia runtime remains sole live risk.

---

## 0) MCP availability — FACT (do not invent tool results)

| Surface | TradingView MCP? | Notes |
|---------|------------------|-------|
| **Grok Bot / this Cursor host** | **None** | Parent `GetDynamicTools` found no `tradingview` namespace. `GetMcpTools` pattern search for trading/view/broker/chart returned no TradingView server. **No MCP tool calls were made against a TV connector.** |
| **Cursor marketplace plugin** | **Not installed / not found as TV** | Steering: search installable plugins — no TradingView plugin to `AuthenticateMcp`. Nearby marketplace trading connector seen elsewhere: Capital.com (different broker; not used here). |
| **Official TradingView MCP (public beta, Sep 16 2026)** | **Exists upstream, not wired here** | URL: `https://mcp.tradingview.com/mcp`. Essential+ plans. OAuth to TradingView account. Tools: quotes, history, screener, fundamentals, news/filings, watchlists/alerts. Beta: scoped tools, **market data may be delayed**, request limits. Connect as a **custom MCP connector** in the client — not a Cursor Featured plugin id on this host. |
| **Mac Studio Jackson CDP MCP** | **Configured, CDP currently down** | Clone `~/tradingview-mcp-jackson`; stdio entry `src/server.js`; wired in Claude / Hermes / workspace `.mcp.json` with `TV_CDP_PORT=9333`. Doc: `TRADINGVIEW-MCP-INSTALL.md`. As of this audit: TradingView.app **is running**, but **port 9333 is not listening** → `node src/cli/index.js status` → `CDP connection failed`. Last green verify was 2026-09-27 (~chart `BATS:AAPL` D). |

**Implication:** This recommendations file is built from **docs + broker pages + screenshot intel + local install notes**, not from live MCP chart/indicator API returns.

**Optional (Burton only):** (1) Relaunch Desktop with CDP: `open -a TradingView --args --remote-debugging-port=9333` then re-run Jackson status. (2) Separately add official `https://mcp.tradingview.com/mcp` as a custom connector if Essential+ and you want agent-side quotes/screener — that does **not** replace Jackson for Pine editor / CDP chart ops.

---

## 1) Screenshot intel (what the UI actually showed)

### Chart / session
- **Symbol:** AAPL (NASDAQ), ~**329.40 (−2.66%)**, timeframe **1D**, candles + volume histogram.
- **Toolbar:** Indicators modal open; Alert / Replay present; Buy/Sell quote strip visible (~Sell 330.11 / Buy 330.30).
- **Right rail:** TRADEVISOR widget; secondary **BTCUSD** context.
- **Background ticker:** Bitcoin / USD (FOREX.com) also visible — multi-symbol workspace.
- Prior Davidd Phase 1b note: chart had been AAPL daily with **Volume only**; community indicators in the modal were **browsing**, not confirmed applied.

### Trading panel (bottom-left)
- Broker: **Coinbase Advanced** connected.
- Tabs: **Positions** (empty), Orders, Order history.
- No open positions in the screenshot.

### Indicators modal — Community **Top** (boosts as shown)
1. Smart Money Concepts (SMC) [LuxAlgo] — LuxAlgo — ~170K  
2. Squeeze Momentum Indicator [LazyBear] — LazyBear — ~116.6K  
3. SuperTrend — KivancOzbilgic — ~82.5K  
4. MacD Custom Indicator-Multiple Time F… — ChrisMoody — ~82K  
5. CM_Williams_Vix_Fix … — ChrisMoody — ~65.6K  
6. Support and Resistance Levels with Bre… — LuxAlgo — ~65K  
7. Market Structure Break & Order Blo… — EmreKb — ~62.3K (EP)  
8. WaveTrend Oscillator [WT] — LazyBear — ~60.9K  
9. UT Bot Alerts — QuantNomad — ~59K  
10. ICT Killzones + Pivots [TFO] — tradeforopp — ~57.1K (EP)  
11. Trendlines with Breaks [LuxAlgo] — LuxAlgo — ~52.5K  

### Indicators modal — Community **Trending** (boosts as shown)
1. Wyckoff [theUltimator5] — ~4.7K (EP)  
2. Reversal Probability Profile [AlgoAlpha] — ~3.6K  
3. Structure-Anchored VWAP [WillyAlgoTrader] — ~3.2K  
4. Trend Target Ribbon [BOSWaves] — ~3.2K (EP)  
5. Williams Variable A/D Pressure [MarkitTick] — ~2.9K  
6. Sweep Reversal Map+ [Herman] — helmans13 — ~2.8K  
7. Trinity Reversal Pattern [AlgoAlpha] — ~2.8K  
8. Pivot Channel TrendLines [BigBeluga] — ~2.4K  
9. Coppock Curve Multi-Filter [MarkitTick] — ~2.1K  
10. RSI Signals Entries — Michael_Fx_T… — ~2.1K  
11. Auto Range Detector [QuantAlgo] — ~2K  
12. Volume Delta Pivot Matrix [BigBeluga] — ~1.9K  
13. Buyers & Sellers Profile + Dynamic S/R — Zeiierman — ~1.6K  
14. AMD Po3 with Live Edge Stats [WillyAlgo…] — ~8.3K (listed in view; popularity ≠ proof)

Sidebar also exposes: Favorites, My scripts, Invite-only, Purchased, Technicals, Fundamentals, Editors’ picks, Marketplace.

---

## 2) Broker fit — Coinbase Advanced vs this chart

**FACT (TradingView broker page + Apr 2025 Coinbase-on-TV blog):** Coinbase Advanced on TradingView trades **Crypto + Futures** (spot pairs 500+/550+, USDC pairs, 100+ perps). Order types: market / limit / stop-limit; brackets supported. Regulators cited include CFTC / FinCEN context on the broker page. **Not** a US-equities OMS on the TradingView broker integration.

| What you see | Fit? | Recommendation |
|--------------|------|----------------|
| Coinbase Advanced panel + empty Positions | Good for **crypto** chart-trading when you intend it | Keep for BTC/ETH/alts practice; still **human-only** until Valedia fence unlocks (agents never place CB orders). |
| **AAPL** chart with Sell/Buy strip while CB Advanced is the labeled broker | **Misfit for execution** | Coinbase Advanced **cannot** fill NASDAQ AAPL via that broker integration. Those Buy/Sell controls are almost certainly **Paper Trading** and/or a non-CB execution path. Do not treat AAPL fills as Coinbase Advanced activity. |
| BTCUSD / FOREX.com Bitcoin ticker in the same workspace | Better CB Advanced alignment | Prefer **Coinbase / exchange crypto symbols** (e.g. BTC-USD on Coinbase data) when rehearsing the connected broker. |
| Valedia `CRYPTO-PAPER-DESK` (Binance.US public klines, paper ledger) | Orthogonal intel path | Keep paper would-have on Valedia; TV+CB Advanced is optional **human chart** / later alert source — not a second live brain. |
| DaviddTech paper webhook (`127.0.0.1:8788`, `DAVIDD_LIVE=0`) | Compatible as **shadow** | Pine alerts → localhost paper only when Burton unlocks. No Cloudflare tunnel. |
| Equity practice (AAPL) | Use **TradingView Paper Trading** or an equity broker (e.g. IBKR) if you need real stock routing | Coinbase **Capital Markets** stocks product is separate from Advanced’s TV crypto integration — do not assume it is wired into this panel. |

**User-reported CB Advanced UX caveats (broker reviews, not verified here):** SL/TP drag often forced 1:1; occasional price mismatch complaints vs Coinbase native. Plan brackets carefully if you ever trade live yourself.

---

## 3) Indicator recommendations (for this desk stack)

Goals: readable structure for crypto paper + Davidd shadow engines; avoid Marketplace invite spam; do not auto-wire community scripts to venue orders.

### Keep as primary (already Valedia-owned)
- `DAVIDDTECH-PINE-PAPER-STUB.pine` — `Waddah_1H_Anchor`
- `DAVIDDTECH-PINE-TRENDHOO-45M-PAPER.pine` — `Trendhoo_45M`
- `DAVIDDTECH-PINE-TOPG-5M-PAPER.pine` — `TopG_5M_Scalp`  
These stay the **paper strategy IDs**. Community scripts are **overlays / human confirmation**, not replacements.

### Built-ins to add first (low drama)
On the **crypto** chart you will actually trade/rehearse with CB Advanced (or paper):
1. **Volume** (already used on AAPL)  
2. **VWAP** (session) — pairs with Structure-Anchored VWAP idea without LuxAlgo lock-in  
3. **EMA 21 / 55** — matches Davidd Trendhoo stand-in lengths  
4. **RSI (14)** — matches crypto-paper safety indicator set spirit  
5. Optional: **MACD** — already in CE-2 tick thinking  

### Community — shortlist from Top (human chart only)
| Script | Why | Caution |
|--------|-----|---------|
| **SuperTrend** (KivancOzbilgic) | Simple trend regime; easy to eyeball vs paper SMA/EMA | Laggy in chop; not a signal to live-order |
| **Squeeze Momentum [LazyBear]** | Volatility expansion / compression — useful next to crypto paper ticks | Confirm with volume; ignore alone |
| **WaveTrend [LazyBear]** | Oscillator for mean-reversion / timing | Overbought/oversold ≠ entry mandate |
| **UT Bot Alerts** | Alert-friendly; later map to Davidd paper webhook **if** Burton unlocks | Do **not** create webhook alerts yet |
| **ICT Killzones + Pivots [TFO]** (EP) | Session boxes help crypto RTH/Asia narrative | ICT folklore — treat as session map, not prophecy |
| **Market Structure Break & Order Blocks** (EP) / LuxAlgo SMC | Structure language matches “smart money” browsing | LuxAlgo stack gets noisy/paywalled; pick **one** structure tool max |

### Community — shortlist from Trending (newer signal)
| Script | Why | Caution |
|--------|-----|---------|
| **Structure-Anchored VWAP** | Anchors VWAP to structure — good with pullback desks | Author scripts churn; pin version |
| **Volume Delta Pivot Matrix [BigBeluga]** | Volume/delta framing for FEED-style notes | Needs liquid crypto pairs; junk on thin alts |
| **Sweep Reversal Map+** | Liquidity sweep framing | Easy to overfit; paper-only |
| **Wyckoff [theUltimator5]** (EP) | Phase labels for swing context | Narrative-heavy; not for scalp TopG |
| **AMD Po3 … [WillyAlgoTrader]** | High boosts in the Trending pane | Popularity ≠ expectancy; leave off default layout |

### Explicitly deprioritize for now
- Stacking multiple **LuxAlgo** Marketplace / invite-only scripts (cost + chart clutter).  
- Auto-trading strategies from Community **Strategies** tab into Coinbase Advanced.  
- Treating TRADEVISOR (third-party widget) as Valedia authority — ignore for fence decisions.  
- Using AAPL + CB Advanced Buy/Sell as a live test of the Coinbase integration.

### Suggested default layouts
1. **Crypto paper / CB Advanced rehearsal:** BTC or ETH on Coinbase (or CB data), 15m–1H + daily; overlays: EMA 21/55, VWAP, Squeeze or SuperTrend, Volume; Davidd Pine only when testing that engine’s TF.  
2. **Equity research (AAPL etc.):** Paper Trading broker; Volume + EMA + RSI; no CB Advanced expectation of fills.  
3. **Davidd shadow:** Keep Phase 1 Pine as source of truth; community scripts optional; alerts/webhook still **OUT**.

---

## 4) Action checklist (Burton / Valedia)

| # | Action | Who | Status |
|---|--------|-----|--------|
| 1 | Accept: **no TradingView MCP on Grok Bot host** — recommendations from docs/screenshots only | — | Done this note |
| 2 | Relaunch TradingView Desktop with `--remote-debugging-port=9333`; verify Jackson `cli status` | Burton | Open (CDP down now) |
| 3 | Optional: add official `https://mcp.tradingview.com/mcp` custom connector (Essential+) for quotes/screener | Burton | Open — do not confuse with Jackson |
| 4 | When using Coinbase Advanced panel, switch chart to a **crypto** symbol | Burton | Open |
| 5 | For AAPL practice, use **Paper Trading** (or equity broker), not CB Advanced | Burton | Open |
| 6 | Add built-in EMA/VWAP/RSI; at most 1–2 community overlays from shortlist | Burton | Open |
| 7 | Leave Davidd alert create / tunnel / `DAVIDD_LIVE` **OFF** | Agents | Held |
| 8 | Do not place Coinbase Advanced orders from any agent | Agents | Fence |

---

## 5) Related paths
- `recommendations/TRADINGVIEW-MCP-INSTALL.md` — Jackson CDP install  
- `recommendations/DAVIDDTECH-PAPER.md` + Pine stubs — paper shadow desk  
- `recommendations/CRYPTO-PAPER-DESK.md` — Valedia crypto would-have ledger  
- `recommendations/LOCAL-MCP.md` — keep TV MCP advertised filtered for local LLMs; don’t disable to save tokens  
- Official MCP blog: https://www.tradingview.com/blog/en/tradingview-mcp-server-public-beta-60864/  
- Broker page: https://www.tradingview.com/broker/Coinbase_Advanced/

---

## Label legend
- **FACT** — verified from docs, local config, or screenshot contents.  
- **ASSUMPTION** — AAPL Buy/Sell while CB Advanced is selected is non-CB execution (paper or other); not proven via MCP.  
- **MODEL** — indicator shortlist is judgment for Valedia paper desks, not backtested expectancy.
