# Ponytail audit — CE-2 Qwen tick + MACD

**Mode:** `/ponytail` full. Crypto paper desk only.
**Fence:** `PAPER/SHADOW`. Ornith still the fill gate. Oil/weather still fenced. Screens `ot-valedia-desk` (783), `valedia-glance` (755), `valedia-grok` (68743) not killed.

## Applied

`reuse:` `crypto_paper_safety.run_safety` / `apply_fill` / SMA-seeded `ema`. MACD is that EMA difference plus an EMA of the line. **Shipped** in `crypto_paper_indicators.macd`. No second ledger.

`reuse:` rules checks (`op`, `left`, `right`) so Ornith already knows how to mark them. Entry adds `macd_line > 0` and `macd_hist > 0`. **Shipped** in `data/crypto-paper/rules.json`. Pairs stay `BTC-USDT` / `ETH-USDT`. Safety model stays `ornith-1.5-k-m`.

`reuse:` LiteLLM `:4000`. Tick assist alias `qwen3.8-27b` ranks only. Fail-open. **Shipped** as `--tick` on `crypto_paper_run.py`. A Qwen `pass: true` is ignored.

## Skipped

`yagni:` installing launchd/cron. The command is in `CRYPTO-PAPER-DESK.md`. **Not installed.**

`yagni:` TradingView MACD seed, a second HTTP client, Qwen as the safety model, a new desk module. **Not shipped.**

`yagni:` a partial git commit. `valedia_config.py` on this branch still carries uncommitted rows from earlier slices (fee-map, clocks, Poly HTTP). Committing CE-2 alone would not check out clean. **Not committed.**

## Findings

`shrink:` histogram `> 0` is the cross. A separate `macd_line > macd_signal` check would be the same fact. One check.

`shrink:` Ornith `max_tokens` 1200 finished on `length` (1164 reasoning tokens, JSON cut). 2400 completes the object (`finish: stop`, 1421 completion tokens). Still a refuse if the content is empty or cut.

**net:** MACD in the indicator module, two entry rows, `--tick` on the existing CLI. Version `0.63.0` working tree, not tagged. Control stays `CRYPTO_PAPER_SAFETY=PAPER/SHADOW+ORNITH`.
