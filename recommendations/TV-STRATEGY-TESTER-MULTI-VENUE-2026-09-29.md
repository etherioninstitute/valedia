# TV Strategy Tester multi-venue (UI proof) + install shortlist

**Stamped:** 2026-09-29 20:22:06 PDT  
**Machine:** Burtons-Mac-Studio-M1 (`b3f456da-e4fc-42d6-8147-a47ad76a8982`)  
**Actor:** Grok Build via Jackson CDP `:9333`  
**Fence:** Paper / intel only. **No orders. No `--live`. No venue sends.**

---

## Split of labor (Burton 2026-09-29 — do not confuse)

| Role | Owner | What it does | What it does NOT |
|------|-------|--------------|------------------|
| **Massive profitable-strategy search** | **Jev** (TypeSafe; `VALEDIA_JEV` shadow) + **Laya** when operational | Comb large typed/state search space; annotate gates; fail-open | Size Kelly; place orders; invent P&L as live expectancy |
| **TV Strategy Tester** | **Grok + Jackson CDP (Home A)** | UI proof on open chart: attach paper strategy, switch symbols, capture `tv data strategy` metrics + screenshots | Authoritative profit ranking; substitute for Jev comb; live OMS |
| **Orchestration** | **Grok** | Dual-home recs, GROK-STATUS, fence, recommend installs | Invent P&L; claim TV numbers = production edge |

**This deliverable's numbers are TV Strategy Tester UI proof only** (same paper SuperTrend strategy, Daily TF, TV commission 0.1%, initial capital 10k). They are **not** invented and **not** a claim of live expectancy. Hand any "most profitable" hunt to **Jev** (Laya when operational). Paper first.

Laya status: MemPalace clone / research inventory — **not** on Valedia critical path yet (`VALEDIA-LAYA-FENCED.md`).

---

## Dual-home

| Home | Status this session |
|------|---------------------|
| **A — Jackson CDP** `~/tradingview-mcp-jackson` / `tv` CLI `TV_CDP_PORT=9333` | **Green.** Strategy `GROK SuperTrend Strategy paper` attached; Strategy Tester opened; 11-symbol sweep; screenshots. |
| **B — Official** `https://mcp.tradingview.com/mcp` | **Not wired / not called.** |

---

## Top installs (recommend only — do not mass-install)

Human chart overlays. **Do not** auto-wire to Coinbase/Alpaca/Webull/Kraken/OKX/FOREX.com orders.

1. **SuperTrend** (KivancOzbilgic) — trend regime eyeball. Paper strategy twin used for Tester: `SUPERTREND-STRATEGY-PAPER.pine`.
2. **Squeeze Momentum Indicator [LazyBear]** — compression → expansion.
3. **One structure tool (pick exactly one):** Market Structure Break & Order Blocks [EmreKb] **or** Structure-Anchored VWAP [WillyAlgoTrader]. Prefer **MSB/OB EmreKb** if you want classic BOS/OB language; prefer Structure-Anchored VWAP if you want VWAP-at-structure without LuxAlgo lock-in.
4. **Tradevisor — shadow only.** Already on chart (`Chart Patterns [Tradevisor]`). Triggers stay non-authority; do not treat as Valedia OMS / Decider input. No compile-to-alert webhook.

Built-ins still fine alongside: Volume, EMA 21/55, session VWAP, RSI(14).

---

## Strategy Tester UI proof — `GROK SuperTrend Strategy paper` @ **1D**

Source: `tv data strategy` internal_api on each symbol after `tv symbol` switch. Raw: `tv-strategy-tester-metrics-2026-09-29/` + `TV-STRATEGY-TESTER-RANKED-2026-09-29.json`.

### Best backtest hits (PF desc, n≥10) — UI proof only

| Rank | Symbol | Venue label | Asset | PF | Net% | Win% | MaxDD% | Trades | Sharpe |
|------|--------|-------------|-------|-----|------|------|--------|--------|--------|
| 1 | COINBASE:ETHUSD | Coinbase | crypto | 2.930 | 604.4% | 51% | 8.4% | 77 | 0.111 |
| 2 | KRAKEN:ETHUSD | Kraken | crypto | 2.882 | 1082.1% | 51% | 8.8% | 83 | 0.125 |
| 3 | OKX:ETHUSDT | OKX | crypto | 1.899 | 58.7% | 49% | 8.2% | 63 | 0.118 |
| 4 | NASDAQ:AAPL | Alpaca/Webull chart proxy | equity | 1.649 | 165.1% | 36% | 9.5% | 341 | 0.015 |
| 5 | KRAKEN:BTCUSD | Kraken | crypto | 1.516 | 55.5% | 41% | 12.6% | 103 | 0.070 |
| 6 | OKX:BTCUSDT | OKX | crypto | 1.460 | 30.6% | 36% | 10.3% | 67 | 0.061 |
| 7 | COINBASE:BTCUSD | Coinbase | crypto | 1.314 | 32.1% | 41% | 16.9% | 101 | 0.031 |
| 8 | NASDAQ:TSLA | Alpaca/Webull chart proxy | equity | 1.290 | 26.4% | 33% | 17.6% | 121 | −0.008 |
| 9 | FOREXCOM:EURUSD | FOREX.com | fx | 0.917 | −1.4% | 33% | 3.3% | 134 | −0.665 |
| 10 | AMEX:SPY | Alpaca/Webull chart proxy | equity | 0.811 | −9.2% | 31% | 11.1% | 264 | −0.464 |
| 11 | FOREXCOM:GBPUSD | FOREX.com | fx | 0.632 | −8.0% | 30% | 9.2% | 152 | −0.795 |

**Caveats (required):** TV default history length differs by symbol (ETH net% >> BTC is lookback/regime, not proven edge). Commission model is TV paper 0.1% — not venue fee map. Equity symbols are **exchange chart data** (NASDAQ/AMEX); Alpaca/Webull were **not** connected as TV brokers this session (prior profit-mine: only Coinbase Advanced panel visible). FX used FOREXCOM:EURUSD/GBPUSD. OKX symbols resolved as OKX:BTCUSDT / OKX:ETHUSDT. **Do not book these as live P&L.**

### Coverage vs request

| Ask | Covered? |
|-----|----------|
| Crypto Coinbase / Kraken / OKX | Yes — BTC+ETH each |
| Equities Alpaca / Webull | Chart proxies NASDAQ:AAPL, AMEX:SPY, NASDAQ:TSLA (broker panels absent) |
| FX FOREX.com | Yes — EURUSD, GBPUSD |
| Not BTC-only | Yes — ETH, equities, FX included |

---

## Screenshots

- Strategy Tester: `tv-st-coinbase-ethusd-2026-09-29.png`
- Chart: `tv-st-chart-coinbase-ethusd-2026-09-29.png`
- Chart left on **COINBASE:ETHUSD** 1D with paper SuperTrend strategy + Tradevisor shadow + Volume + Overlay.

---

## Next (paper / scout — not live)

1. **Jev** — run typed comb / shadow annotation for strategy families (not this TV UI table). Laya when Burton marks operational.
2. Scout — fee-true crypto-paper vs Coinbase/Kraken/OKX prints using Valedia fee map; still no `create_order`.
3. Burton — optional install of the three overlays + keep Tradevisor shadow.
4. Home B official MCP — only after Essential+ OAuth.

## Fences held

No orders. No `--live`. No Coinbase/Alpaca/Webull/Kraken/OKX/FOREX.com send. No Trade/Buy/Sell/Submit click. No alert webhook. No layout Save forced. Valedia runtime owns live.
