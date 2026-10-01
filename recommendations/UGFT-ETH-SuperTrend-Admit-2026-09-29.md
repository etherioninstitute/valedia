# UGFT-ETH-SuperTrend-Admit

**Path name:** UGFT-ETH-SuperTrend-Admit  
**Stamped:** 2026-09-29 20:29:26 PDT  
**Machine:** Burtons-Mac-Studio-M1 (`b3f456da-e4fc-42d6-8147-a47ad76a8982`)  
**Fence:** Fee-true **paper** sketch only. **No orders. No `--live`. No venue sends.**

## One path

SuperTrend **paper** on **COINBASE:ETHUSD** and **KRAKEN:ETHUSD** (1D) is the only candidate. UGFT stack is the admit / veto / size gate **before** a fee-true paper row. Not a live strategy. Not a Jev comb.

TV UI-proof ranks (2026-09-29, `tv data strategy` only — **not** Profiler): COINBASE:ETHUSD PF 2.930, n=77; KRAKEN:ETHUSD PF 2.882, n=83. Those numbers are Strategy Tester summary, **not** expectancy and **not** the fee map.

## Gate (paper)

| Piece | Role on this path | What was found |
|-------|-------------------|----------------|
| `kernel_predictor_v16e.py` | **Admit / veto / size.** Admit only if kernel `direction` matches the SuperTrend side **and** size clears hysteresis (BUY `size>0.10`, SELL `size<-0.06`) **and** noise/regime vetoes are off (`se>0.88` damp, `gap_s<0.05` damp, cascade / product-health halt). Size = kernel `size` clipped to [-1,1], equity long-only if ever used. **Not Kelly.** | Proton VPS: `…/VPS/kernel_predictor_v16e.py`. Its own backtest fee is 8 bps — **not** the Valedia paper table. |
| Etherion | Manuscript pin only. **Veto** any soak that treats Etherion (`D`, superconnection, or ψ-substrate) as a numeric alpha. Three formalizations in the vault are not one object. | `~/vault/concepts/etherion.md`. No trading module. |
| `MANUSCRIPT_MANIFEST` | Would pin which manuscript revision the gate cites. **Missing this pass** — gate is not revision-locked until that file is named. Do not invent a path. | `mdfind` / `find` under UGFT + vault: no file. |

Fee-true book (if a later paper tick logs a row): CE-3 table `CRYPTO_FEE_PCT` 0.001 + `CRYPTO_SLIPPAGE_PCT` 0.0005 → `data/crypto-paper/decisions.jsonl`. Not `forward_test_ledger`. Not `real_track_record`. TV commission 0.1% stays UI-proof only.

## Split

- **Jev** soaks the comb. This path is one named candidate, not the search.
- **TV UI proof:** prior pass did **not** use Profiler. Next proof **must**.
- **Laya:** later. Still `VALEDIA-LAYA-FENCED.md`. Not on this path.
- **Live:** Valedia runtime only. This note does not arm it.

## Profiler — honest gap (required next UI-proof)

Burton confirm: use TradingView **Profiler mode**. Linked article: https://www.tradingview.com/support/solutions/43000725216/ — that page is the **Pine Profiler** (Pine Editor: % runtime, executions, flame icons on the three hottest regions). It is **not** the Strategy Tester performance summary.

**2026-09-29 ~20:22 PDT multi-venue pass did not enable it.** Source was `tv data strategy` (PF, net%, win%, max DD, trades, Sharpe) after Strategy Tester open. No Pine Editor profiler margin, no line-time, no per-line executions.

**Next UI-proof (required, still paper):** open `SUPERTREND-STRATEGY-PAPER.pine` in the Pine Editor, script on the chart, turn **Profiler mode** on, capture the three flame regions (line or block, time, executions). Do that on COINBASE:ETHUSD and KRAKEN:ETHUSD. No orders, no `--live`, no Buy/Sell/Submit.

## Fences

No orders. No `--live`. No Coinbase/Kraken send. No Kelly. No Etherion-as-alpha. Live stays Valedia runtime.
