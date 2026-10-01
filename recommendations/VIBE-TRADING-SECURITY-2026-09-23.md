# VIBE-TRADING security scrub — 2026-09-23

Clone `~/MemPalace/sources/repos/github/HKUDS-Vibe-Trading` HEAD `a65427a3d587e43cd5c7541948b36524e8fd2f12` (main, shallow). Not modified. `vaughanf1-vibe-trader` left at `f9dbdae8766d48589c46f12730f0d3916eb61b16`.

**Decision:** inventory plus one Valedia paper module. Do not vendor the app, do not `pip install vibe-trading-ai`, do not start MCP or FastAPI, do not copy Alpha Zoo. Repo LICENSE is MIT. `agent/src/factors/zoo/qlib158/NOTICE` is Apache-2.0 attribution for a from-scratch Alpha158 rewrite (154 modules on disk, folder name qlib158). Wholesale copy is the wrong size either way.

## Findings

| Surface | Evidence | Fence |
|---|---|---|
| Placeholder keys | `agent/.env.example` sets provider key names (`OPENROUTER_API_KEY`, `OPENAI_API_KEY`, peers) and `TUSHARE_TOKEN` to documentation placeholders. No live secret in that file. | Not copied. Not loaded. |
| Generated-code exec | `agent/src/core/runner.py` runs generated strategy Python in a subprocess (`runpy`). `SECURITY.md` says the child is still network-capable. Allowlist keeps proxy/cert vars plus loader hosts (`FUTU_HOST`, market-data tokens). | Not imported. `exec_generated` raises `vibe_codegen`. |
| Shadow codegen | `agent/src/shadow_account/codegen.py` renders `signal_engine.py` from a Jinja template. Literals go through `ast.literal_eval`. Callers write the file and launch `run_backtest`. | Pattern only: one numeric rule, no template, no file of generated code. |
| `shell=True` | `agent/src/tools/bash_tool.py:131` `subprocess.Popen(..., shell=True)`. MCP registers it only with `--enable-shell-tools` or `VIBE_TRADING_ENABLE_SHELL_TOOLS` (`agent/mcp_server.py`, GHSA-6wjh-cc6v-xfrx / GHSA-m768-22r9-h4x7). Default off. Opt-in is still RCE. | `shell()` raises `vibe_shell`. MCP not registered. |
| `pickle.loads` | `agent/src/tools/alpha_bench_tool.py:249` loads `~/.vibe-trading/cache` after HMAC. Comment calls the unauthenticated case local-write RCE. | Not imported. |
| Broker writes | `place_order` in 18 connectors under `agent/src/trading/connectors/`: alpaca, binance, dhan, etoro, futu, ibkr, kis, longbridge, mt5, okx, robinhood, scalable, shoonya, tiger, toss, trading212, upbit, zerodha. `agent/src/trading/service.py` `place_order`. `agent/src/live/sdk_order_gate.py` can ALLOW then call `connector.place_order`. eToro copy tools are agent-side, not MCP, and still write. | `place_order` / `create_order` raise `vibe_broker_write` before any socket. `live_enabled()` is False and ignores env. |
| FastAPI | `agent/api_server.py` refuses a non-loopback bind when no key is set. `agent/src/api/security.py` trusts loopback when `API_AUTH_KEY` is empty (`LOOPBACK_SUBJECT`). GHSA-7wgj: a configured key now gates loopback too. Empty key does not. | Server not started. |
| MCP network | `agent/mcp_server.py` HTTP/SSE Host allow-list defaults to loopback (GHSA-p3c9). Docstring: no order tool; `refresh_strategy_evidence` writes a local cache; shell tools are the RCE hole. | Not installed. Not on the Kalshi/Poly create path. |
| Supply chain | `requirements-lock.txt` 4613 lines, `frontend/package-lock.json` 5979 lines, `requirements-channels-lock.txt` 336 lines. | No install. |
| Wallet scam | `SECURITY.md` — wallet connect / airdrop prompts are impersonation. Discord impostor note 2026-06-18. | No wallet action. |
| Site analytics | `wiki/wrangler.toml` Cloudflare D1 `vibetrading-analytics`. Docs site, not the agent. | Not wired. |

## What Valedia absorbed

`scripts/integrations/vibe_shadow_paper.py` logs fixture roundtrips to `data/vibe-shadow/decisions.jsonl`. Source `vibe_shadow_paper` (contains `paper`, so `forward_test_ledger._is_non_real_source` ignores it). `create_order` false. `broker_write` false. Fee gate: missing fee refuses; `gross_pnl - fee_usd <= 0` refuses `fee_true`. Dip rule is the one Shadow Account feature `prior_5d_return <= -0.08`, reimplemented, not generated.

Controls: `VIBE_SHADOW_MODE`, `VIBE_SHADOW_LIVE` (const false), `VIBE_BROKER_WRITE` (const false).

Alpha Zoo inventory inside `inventory()`: academic 12, alpha101 101, fundamental 4, gtja191 191, qlib158 154. Total 462. Paths only.
