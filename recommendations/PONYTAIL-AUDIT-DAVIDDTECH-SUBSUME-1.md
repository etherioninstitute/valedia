# Ponytail audit — DAVIDDTECH-SUBSUME-1

**Mode:** `/ponytail` full. TradingView DaviddTech paper/shadow only.
**Fence:** `DAVIDD_LIVE=0`. `create_order` false. No CCXT, no `--live`, no wallet, no public bind. Cloudflare/tunnel OUT. Screens not touched.

## Applied

`reuse:` paper decision shape from eToro/Vaughan/crypto-paper (`data/*/decisions.jsonl`, flock + 400-row tail, `counts_toward_kelly=false`, source contains `paper`). **Shipped** as `daviddtech_paper` / `daviddtech-paper`. Live FTL is not written.

`reuse:` halt-as-ears from `KILL-SWITCH.md`. **Shipped** `VALEDIA_TRADING_HALT` still appends jsonl with `band=review_only` / `reason=trading_halt_shadow`.

`fix:` jsonl stored `"passphrase": "[redacted]"`. Charter forbids storing the field. **Shipped** omit + recursive strip + refuse write if `"passphrase":` serializes.

`fix:` `==` on the webhook secret. **Shipped** `hmac.compare_digest` (stdlib).

`fix:` host was a constant with a comment. **Shipped** `bind_host()` raises unless `127.0.0.1`; uvicorn/stdlib both use it.

## Skipped

`yagni:` FastAPI install on default `python3` 3.9.6. Stdlib `http.server` already serves the two routes. Add FastAPI when Burton wants the TestClient path.

`yagni:` Pine compile / alert JSON on TradingView. Operator checklist stays in the charter. Add when Burton runs Phase 1.

`yagni:` Cloudflare / nginx / public tunnel. **Not started.** Grok Build owns that lane when Burton unlocks it.

`yagni:` CCXT, Qwen approve gate, `DAVIDD_LIVE=1`, `--live`, wallet. **Not added.**

`yagni:` a git commit of this slice on a large dirty tree. **Not committed. Not tagged.**

## Findings

`shrink:` one JSONL plus one flock. Retention is a tail rewrite at 400 rows (`ponytail:` on `DECISION_KEEP`).

`shrink:` strategy map is a dict. Fine at three engines.

**net:** localhost webhook, passphrase never in jsonl, `create_order` always false, halt still logs paper. Default python listens via stdlib on `127.0.0.1`. Version working tree `0.74.1`, not tagged. `DAVIDD_LIVE=0`.
