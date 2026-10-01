# eToro paper — CopyTrader / Smart Portfolio research desk

Intel for Valedia. Mode `PAPER/SHADOW`. `ETORO_LIVE` stays off. No copy allocation, no order, no withdraw.

```bash
python3 scripts/integrations/etoro_copy_paper.py          # fixture book, no network
python3 scripts/integrations/etoro_copy_paper.py --read   # GET rankings, search, market data
```

Client: `scripts/integrations/etoro_client.py`. Every call sends `x-api-key`, `x-user-key`, and a new `x-request-id`. Keys load from the environment or `~/.openclaw/.env` (`ETORO_API_KEY` / `ETORO_PUBLIC_KEY`, `ETORO_USER_KEY` / `ETORO_PRIVATE_KEY`). Values are not logged. Shared rate gate is 60 requests / 60 seconds. User-Agent is `Valedia-ASI research@goatworthy.com` (Cloudflare rejects the default Python agent).

Reads: rankings `GET /api/v2/portfolios/rankings`, portfolio search `GET /api/v1/portfolios/search` (`type=trader|smart-portfolio`; a 400 retries `Type`), instrument types, instrument search, `GET /api/v1/me`, people, and copier stats.

Score uses gain (decimal), riskScore (2–6 allow), copiers (≥100 allow), peakToValley (percent values become a fraction; magnitude), and winRatio (0–100). Bands: `allowlist`, `review_only`, `refuse`. Search hits that lack gain and risk stay `review_only`.

Would-copy size is $25, daily notional cap $100, minimum $5, kill switch on. A would-copy does not allocate. `create_order` and `start_copy` are false. Source `etoro_copy_paper` (contains `paper`). Kelly ignores it. The live forward-test ledger is not written.

Log: `data/etoro-paper/decisions.jsonl`. Tail retention 400.

Write stubs (`start_copy`, `adjust_copy`, `close_copy`, real and demo `create_order` / `cancel_order`, `money_withdraw`) raise before any socket:

| Code | When |
|---|---|
| `copy_start` | copy start while `ETORO_LIVE!=1` |
| `etoro_live` | any other write while live is off, or unlock is missing |
| `half_live` | `ETORO_LIVE=1` while mode is still research/paper/shadow, or `ETORO_HEDGE` is set |
| `write_endpoint` | live flag, `ETORO_UNLOCK=burton-documented`, and mode is not paper. This slice still does not send |

This slice does not set `ETORO_LIVE` or `ETORO_UNLOCK`.

Controls: `ETORO_MODE`, `ETORO_LIVE`, `ETORO_KEYS` (present/missing only), `ETORO_TICK_AGE_S`, `ETORO_LAST_DECISION`.

Tests: `python3 scripts/integrations/test_etoro_copy_paper.py`. No cron. No OpenTerminal copy button.
