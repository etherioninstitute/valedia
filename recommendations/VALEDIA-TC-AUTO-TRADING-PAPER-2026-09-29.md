# Valedia TC auto-trading — paper path

**When:** 2026-09-29 23:02 PT  
**Source:** `recommendations/inbox/AUTO-TRADING-TC-2026-09-29.txt` (copy of `~/Downloads/AUTO TRADING.txt`)  
**Fence:** paper / shadow / simulated fills only. No `--live`. No venue `create_order` / `place_order`. No AlphaInsider → Webull/IBKR OAuth. No Robinhood Instant/Wire and no move of the real ~$504. No Kelly as live size. Oil stays `NO_HARVEST`. Live cutover is Valedia runtime unlock plus Burton go, and it is out of scope here.

Valedia owns the loop. AlphaInsider is not the brain. A pasted `api_token` is stripped and never written.

## TC step → Valedia

| TC step | Valedia |
|---|---|
| 1. Strategy | Existing SuperTrend paper Pine, `//@version=6`: `recommendations/SUPERTREND-STRATEGY-PAPER.pine`. UGFT admit stays the kernel context in `recommendations/UGFT-ETH-SuperTrend-Admit-2026-09-29.md`. This receiver does not install community scripts and does not treat UGFT as a live order gate. |
| 2. Backtest | Fee-true books below. Strategy Tester notes stay in `recommendations/TV-STRATEGY-TESTER-MULTI-VENUE-2026-09-29.md` and `recommendations/UGFT-KERNEL-ETHERION-BACKTEST-2026-09-29.md`. |
| 3. Forward test | This paper book. Not AlphaInsider. Not `forward_test_ledger`. `counts_toward_kelly` is false. |
| 4. Alert | JSON below, straight quotes, shaped for Valedia. |
| 5. Webhook | `127.0.0.1` only. `POST /valedia-tv-alert`. ngrok is not started. |
| 6. Broker | Simulated fill + fee-true ledger. OMS attach is documented and unwired. |
| 7. Journal | `fills.jsonl` plus `recommendations/TC-AUTO-PAPER-SMOKE-2026-09-29.md`. TradeZella is a later option, not connected. |

## Files

| Path | Role |
|---|---|
| `scripts/integrations/tc_auto_paper.py` | Paper book, 10% clip, fee-true fills, Intent rows |
| `scripts/integrations/tc_auto_webhook_server.py` | Localhost receiver + `--smoke` |
| `scripts/integrations/test_tc_auto_paper.py` | Offline tests plus one localhost HTTP smoke |
| `recommendations/TC-AUTO-PAPER-SMOKE-2026-09-29.json` | Smoke receipt |
| `recommendations/TC-AUTO-PAPER-SMOKE-2026-09-29.md` | Smoke table |
| `data/tc-auto-paper/` | Runtime book when the server is left up. Smoke uses a temp dir and does not write here. |

Sibling, not a fork of the DaviddTech desk: `scripts/integrations/daviddtech_webhook_server.py` stays on its own payload and port.

## Payload

Passphrase comes from the environment `VALEDIA_TV_WEBHOOK_PASSPHRASE`. It is checked with `hmac.compare_digest`. It is not stored. Empty env fails closed (401).

```json
{
  "passphrase": "YOUR_VALEDIA_TV_PASSPHRASE",
  "action": "{{strategy.order.action}}",
  "ticker": "{{ticker}}",
  "contracts": "{{strategy.order.contracts}}",
  "price": "{{close}}",
  "strategy": "supertrend_paper"
}
```

Use straight quotes. Curly quotes return 422 `curly_quotes`.

| Field | Rule |
|---|---|
| `action` | `buy` / `sell` (also `long`/`entry` → buy, `exit`/`close`/`flat` → sell). `short` is refused. |
| `ticker` | `COINBASE:` / `KRAKEN:` / `BINANCE:` or a BTC/ETH/SOL/XRP/DOGE/ADA base → CE-3. A 1–5 letter equity symbol → alpaca static. `USO`, `CL`, `CL1!`, `WTI`, `BRENT`, `OIL` → 422 `oil_no_harvest`. |
| `contracts` | Optional. On a buy, quantity is the minimum of the requested size and 10% of paper equity (`crypto_paper.CLIP_FRAC`, same fraction as the Pine `default_qty_value`). Rounded down. A sell closes the paper position (or the requested size, whichever is smaller). No short open. |
| `price` | Alert mid. Fill price is `crypto_paper.fill_px` (buy up, sell down by slippage). |
| `strategy` | Allowlist: `supertrend_paper`, `ugft_supertrend_admit`. Omitted defaults to `supertrend_paper`. Anything else is 422 `unknown_strategy` and is not filled. |
| `api_token`, `token` | Stripped. Never journaled. Not authentication. |

## Fee books

| Class | Book | Fee | Slippage |
|---|---|---|---|
| Crypto | CE-3 | `valedia_config.CRYPTO_FEE_PCT` 0.001 | `CRYPTO_SLIPPAGE_PCT` 0.0005 |
| Equity | `fee_engine.STATIC_FEES["alpaca"]` | taker 0 | `EQUITY_SLIPPAGE_PCT` 0.0001 |

Paper bankroll is exactly `$504`. That number is the sizing reference for this book. It is not a transfer.

Size rule on every row: `supertrend_pine_10pct_of_paper_equity`. Kelly is not consulted.

## Run

Listen (stdlib; FastAPI is used only if uvicorn imports). Default port **8791**. 8787 is Hermes. 8788 is DaviddTech. Bind is `127.0.0.1` only.

```bash
cd ~/.openclaw/workspace-v2
VALEDIA_TV_WEBHOOK_PASSPHRASE='set-locally' \
  python3 scripts/integrations/tc_auto_webhook_server.py --stdlib
```

Health: `GET http://127.0.0.1:8791/health` → `create_order: false`, `live: false`, `bankroll: 504`, `ngrok: false`.

Synthetic alert:

```bash
curl -sS -X POST http://127.0.0.1:8791/valedia-tv-alert \
  -H 'Content-Type: application/json' \
  -d '{"passphrase":"set-locally","action":"buy","ticker":"COINBASE:BTCUSD","price":"83360.03","strategy":"supertrend_paper"}'
```

One-shot smoke (ephemeral port, temp book, then exit — nothing left listening):

```bash
python3 scripts/integrations/tc_auto_webhook_server.py --smoke
python3 scripts/integrations/test_tc_auto_paper.py
```

`VALEDIA_TV_LIVE=1` or `TC_AUTO_LIVE=1` is ignored. The fill stays paper and `create_order` stays false. `VALEDIA_TRADING_HALT=1` journals a shadow row and does not fill.

## Smoke result ($504, flat mids)

Mids are the 2026-09-29 chart references (BTC 83360.03, AAPL 329.4), not a forecast. Flat prices make the P&L fee and slippage drag.

| | |
|---|---|
| Cash left | 503.8387246 |
| Fees paid | 0.10079895 |
| Realized P&L | **-0.1612754** |
| Fills | 4, flat afterward |
| create_order / live / Kelly | false / false / false |
| Oil | NO_HARVEST |

| ticker | side | qty | px | fee | book | realized |
|---|---|---:|---:|---:|---|---:|
| COINBASE:BTCUSD | buy | 0.0006046 | 83401.710015 | 0.05042467 | CE-3 | |
| COINBASE:BTCUSD | sell | 0.0006046 | 83318.349985 | 0.05037427 | CE-3 | -0.15119842 |
| AAPL | buy | 0.15295956 | 329.43294 | 0 | alpaca_static | |
| AAPL | sell | 0.15295956 | 329.36706 | 0 | alpaca_static | -0.01007698 |

Receipt: `recommendations/TC-AUTO-PAPER-SMOKE-2026-09-29.json`. Tests: 8 passed (`test_tc_auto_paper.py`).

## Gates on each row

`create_order=false`, `live=false`, `dispatchable=false`, `paperTrading=true`, `counts_toward_kelly=false`, `oil=NO_HARVEST`, `ugft_admit=context_only_not_a_live_gate`, Intent `status=dry_run`, `ledger_source=paper`, `venue=valedia-paper`.

## Live attach (not this pass)

A later unlock would be a different process that Burton starts after the Valedia runtime gate opens. It would read only rows that are already fee-true and still refuse until that gate is on. The existing OMS modules are `scripts/integrations/coinbase_trader.py` (Coinbase Advanced) and `scripts/integrations/alpaca_executor.py` (Alpaca). This receiver does not import them. Do not point TradingView at a public URL until that unlock exists.

## Public tunnel

ngrok (or any tunnel) is how TradingView's cloud would reach a Mac. It is also how a paper route becomes reachable by anyone who has the URL. This slice does not install, start, or document a live command for it. Leave the receiver on `127.0.0.1`.

## Tests

`python3 scripts/integrations/test_tc_auto_paper.py` — 8 passed on 2026-09-29. Covers CE-3 round-trip drag, alpaca $0 commission, clip vs oversized contracts, passphrase 401, secret strip, oil / short / unknown strategy / curly quotes, live-flag ignore, halt shadow, localhost bind, and the HTTP smoke.
