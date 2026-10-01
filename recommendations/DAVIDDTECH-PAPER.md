# DaviddTech paper — TradingView shadow desk

Intel for Valedia. Mode `PAPER/SHADOW`. `DAVIDD_LIVE` stays off. No CCXT, no Cloudflare, no create_order.

```bash
python3 scripts/integrations/daviddtech_paper.py            # fixture decision → jsonl
python3 scripts/integrations/daviddtech_paper.py --validate # schema check sample
DAVIDDTECH_WEBHOOK_PORT=8788 python3 scripts/integrations/daviddtech_webhook_server.py   # 127.0.0.1:8788
```

Passphrase: set `DAVIDDTECH_WEBHOOK_PASSPHRASE` (or `TRADINGVIEW_WEBHOOK_PASSPHRASE`) in `~/.openclaw/.env`. Never commit or echo the value. Never a jsonl key (omitted, not redacted).

Listen: default `python3` (3.9.6) has no FastAPI; server falls back to stdlib `http.server` on `127.0.0.1`. `bind_host()` refuses anything else.

`127.0.0.1:8787` is Hermes WebUI (`*:8787`). Do not POST this passphrase there. Do not kill Hermes. Operator default while Hermes holds 8787 is `DAVIDDTECH_WEBHOOK_PORT=8788`. The code constant stays 8787.

Pine: `recommendations/DAVIDDTECH-PINE-PAPER-STUB.pine` (`Waddah_1H_Anchor`, `portfolio_risk` 0.5). Sister paper engines: `DAVIDDTECH-PINE-TRENDHOO-45M-PAPER.pine` (`Trendhoo_45M`, 0.3) and `DAVIDDTECH-PINE-TOPG-5M-PAPER.pine` (`TopG_5M_Scalp`, 0.2). Same JSON keys, subaccount `paper`, passphrase sentinel `PASTE_PASSPHRASE_IN_TV_ALERT`. No webhook URL in the scripts.

Payload: passphrase, timestamp, strategy_id, ticker, action (`entry|exit|tp1|tp2|breakeven`), entry_price, volume; optional subaccount, portfolio_risk.

Map: `Waddah_1H_Anchor`, `Trendhoo_45M`, `TopG_5M_Scalp`. TV `volume` = contracts for research only (`volume_tv`); `size_usd` stays null/hypothetical.

Log: `data/daviddtech-paper/decisions.jsonl`. Every row forces `create_order=false`, `live=false`, `paperTrading=true`, `counts_toward_kelly=false`.

Halt: `VALEDIA_TRADING_HALT` still accepts and logs; band/reason notes halt.

Tests: `python3 scripts/integrations/test_daviddtech_paper.py`.

Charter: `DAVIDDTECH-SUBSUME-1.md`. Fence: `KILL-SWITCH.md`. CCXT research only: `ADAPT-DOSSIERS/ccxt.md`.
