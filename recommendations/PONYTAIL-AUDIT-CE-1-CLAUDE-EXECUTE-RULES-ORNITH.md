# Ponytail audit — CE-1 claude-execute rules + Ornith

**Mode:** `/ponytail` full. Crypto paper desk only.
**Fence:** `PAPER/SHADOW`. No venue order. Oil/weather still fenced. Screens `ot-valedia-desk` (783) and `valedia-glance` (755) not killed. `valedia-grok` (4991) left up.

## Applied

`reuse:` `crypto_paper.apply_fill` / `book` / `sma` / allowlist. One paper buy on PASS. **Shipped.** No second ledger.

`reuse:` claude-execute field names (`watchlist`, `bias_criteria`, `entry_rules`, `exit_rules`, `risk_rules`, `safety-check-log.json` `trades[]`). Checks are numeric. **Shipped** in `data/crypto-paper/rules.json`. Pairs stay `BTC-USDT` / `ETH-USDT`.

`reuse:` LiteLLM alias `ornith-1.5-k-m` at `:4000` (Q4 backend). **Shipped.** Live fixture PASS, then one paper fill. Would-have PnL **-$1.5005** on a $10,000 book (slip + $1.0005 fee, marked at mid). Not realized profit.

## Skipped

`yagni:` vendoring `bot.js`, Railway, BitGet, TradingView MCP, tax CSV. **Not shipped.**

`yagni:` Qwen fallback, cron, CE-2 tick glue. **Not shipped.**

`yagni:` a second HTTP client to Bionic `:1234`. The alias is the route. **Not shipped.**

## Findings

`shrink:` LiteLLM `drop_params: true` strips `reasoning_effort`. Ornith still thinks, then emits JSON. `max_tokens` is 1200 so the JSON fits. Empty content stays a refuse. Do not point this gate at Qwen to "finish faster."

**net:** two modules, one rules file, one CLI flag, one control row `CRYPTO_PAPER_SAFETY=PAPER/SHADOW+ORNITH`. Version `0.62.0` working tree, not tagged.
