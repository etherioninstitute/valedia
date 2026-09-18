# NT-0 smoke — pin + deny live (2026-09-18)

Charter: `VALEDIA-NEURAL-TRADER-FENCED.md`. Worktree pin only. Not an OMS.

## Pin

| Item | Value |
|------|--------|
| Path | `tools/neural-trader/` (workspace-v2, not global) |
| Spec | `neural-trader@^2.8.11` |
| Locked | `2.8.11` (`package-lock.json` → `neural-trader-2.8.11.tgz`) |
| Install | `cd tools/neural-trader && npm install --ignore-scripts --no-audit --no-fund` |
| Defense | `.npmrc` `ignore-scripts=true` (plain `npm install` still skips hooks) |
| `node_modules/` | gitignored |

Older than 2.8.11 is refused (`neural_trader_fence.parse_semver` / `pin_ok`).

## Allowlist (research CLI only)

`tools/neural-trader/allowlist.json` + `scripts/integrations/neural_trader_fence.py`

**Allow:** `backtest`, `signal`, `regime`, `risk`, `portfolio`, `train`, `explain`

**Deny:** `live`, `--live`, `broker`, `--broker`, `mcp`, `cloud`, `--swarm`, plus `opus`/`sonnet`/`claude-*` (plugin agents are not LMS)

`assert_research_argv` raises `FenceError` before any spawn. CLI-first: `npx --no-install neural-trader --<verb>`. Never run ruflo-neural-trader agents as opus/sonnet.

## Live / broker unreachable from Valedia

| Check | Result |
|-------|--------|
| `scripts/install_cron.sh`, `cron_runner*.sh`, `valedia_config.py` | no `neural-trader` / `trader live` / `ruflo-neural-trader` |
| `.mcp.json` + `.grok/config.toml` | no neural-trader server |
| Live crontab (`crontab -l`) | no `neural-trader`, `npx neural`, `trader live`, `--broker` |
| RuFlo MCP `:8810` `tools/list` | **57 tools**; **0** names matching neural/trader/broker/live/order |
| `NAUTILUS_LIVE` | False |
| `NAUTILUS_ENABLED` | False |
| `SPOT_RECONCILE_HTTP` | False |
| `REBALANCER_AUTOEXEC` | False |
| `MIROFISH_AUTO_RAISE` | False |
| `MIROFISH_WEIGHT` | 0.10 (unchanged) |
| `VENUE_WRITER` | kalshi/coinbase/alpaca=`valedia`; kraken/polymarket/hyperliquid=`none` |

No arrow from neural-trader to venue writers. `trader live --broker` is not on cron, not in Valedia config, not advertised on filtered MCP.

`--help` on the pin (`neural-trader 2.8.11`) was invoked; **`trader live` was not run**.

## Test

```
python3 scripts/integrations/test_nt0.py
# nt0 pin+allowlist+unreachable: PASS
```

No network orders. No fusion weight change. No VALEDIA_VERSION bump (pin/docs/fence only).

## Next

NT-1: regime/signal → `models/mirofish_neural_enrichment.json` only.
