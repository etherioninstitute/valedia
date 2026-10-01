# CE-2 — Qwen cron tick + MACD helpers (crypto paper)

**Burton go:** 2026-09-22 ~21:20 PT — “queue up CE-2 and the rest”  
**Depends on:** CE-1 DONE (`CE-1-CLAUDE-EXECUTE-RULES-ORNITH.md`, Valedia 0.62.0 working tree)  
**LLM Stack map:** snappy tick → Qwen3.8-27B; **safety stays Ornith** via LiteLLM alias `ornith-1.5-k-m` (Q4). Never fail-open live-shaped checks on Qwen-only.

## Goal
Wire a **paper-only scheduled tick** that:
1. Computes deterministic indicators including **MACD** (extend `crypto_paper_indicators.py`).
2. Uses **Qwen** (LiteLLM / Bionic snappy path) only for light tick glue / rule selection — **not** as the safety gate.
3. Still calls **Ornith** (`ornith-1.5-k-m`) fail-closed before any paper fill (reuse CE-1 `crypto_paper_safety.py`).
4. On PASS → `crypto_paper.apply_fill` would-have only. Never exchange execute.

## Scope (do)
1. Add MACD (+ any small helpers needed for rules) to `crypto_paper_indicators.py`; unit-test offline.
2. Extend `data/crypto-paper/rules.json` (or sibling) with MACD-based structured checks Ornith can mark pass/fail with numeric evidence.
3. Add a **cron/tick entrypoint** (script or `crypto_paper_run.py` subcommand, e.g. `--tick` / `--cron`) that:
   - loads bars (fixture default; optional public klines documented, not required for Done)
   - computes indicators (incl. MACD)
   - optional Qwen tick assist for ranking/watchlist only (fail-open OK for assist; must not bypass Ornith)
   - Ornith safety fail-closed
   - PASS → paper fill; FAIL → log refuse
4. Document how to schedule (launchd/cron/Hermes job) — **install schedule only if safe and reversible**; default Done = script + README + dry-run once. Prefer documenting the command over auto-installing a live agent cron if ambiguous.
5. Tests: MACD math; tick path with mocked Ornith PASS/FAIL; Qwen-down does not skip Ornith; no `create_order`.
6. Controls/docs: one line in `CRYPTO-PAPER-DESK.md`. Ponytail + `GROK-STATUS.md`. Version bump working tree if code ships.
7. **Next after exit:** CE-3 (parent/exit-ping launches). Do not start CE-3 inside this job.

## Explicit non-goals
- Real exchange execute / CCXT / BitGet / Railway / wallets / onchain
- TradingView MCP hard dependency
- Flipping Poly LIVE / Kalshi writers / weather/oil unfence
- Treating paper PnL as realized
- Replacing Ornith safety with Qwen

## Hard fence
CRYPTO-PAPER / PAPER/SHADOW only. Screen `valedia-grok` only; do not kill `ot-valedia-desk` / `valedia-glance`. No `--live`, no HALT bypass, no treasury auto.

## Tools
RuFlo `:8810` filtered. CBM for navigation. Ponytail after code. Prove tool use in `GROK-STATUS.md`.

## Done when
- MACD + tick path green in tests
- One dry-run tick (fixture) shows Ornith gate still required
- Audit written; `GROK_EXIT:0`; Next=CE-3

## Results

**2026-09-22. VALEDIA_VERSION 0.63.0** (working tree, not committed, not tagged).

MACD(12, 26, 9) in `crypto_paper_indicators.py` (SMA-seeded EMA, same as `ema`). Snapshot keys `macd_line`, `macd_signal`, `macd_hist`. `rules.json` entry checks `macd_line > 0` and `macd_hist > 0`. Safety model stays `ornith-1.5-k-m`. Tick assist alias `qwen3.8-27b` ranks the watchlist only.

`--tick` on `crypto_paper_run.py`. Qwen down, malformed, or a rank with no bars does not skip Ornith and cannot set `pass`. PASS is one `crypto_paper.apply_fill`. Cron not installed. Command is in `CRYPTO-PAPER-DESK.md`.

Tests `test_crypto_paper_safety.py` PASS (MACD math, mocked Ornith PASS/FAIL, Qwen-down still calls Ornith, Qwen `pass: true` ignored, dead-port `--tick --no-qwen`). `test_crypto_paper.py` PASS. `test_controls_readout.py` PASS. `test_fomo_radar.py` PASS. Control stays `CRYPTO_PAPER_SAFETY=PAPER/SHADOW+ORNITH`.

Dead-port fixture tick: `qwen_down`, then `ornith_down`, no fill, `create_order` false. Log `data/crypto-paper/tick-check-log.json`.

Live alias `ornith-1.5-k-m` (`--tick --no-qwen`, no mock): **PASS**, model `ornith-1.5-k-m`. Paper fill BTC-USDT buy qty 9.25388969 @ 108.11669833, fee 1.0005, source `paper`. Would-have PnL **-$1.5005** on $10,000. Not realized. `create_order` false, `live` false. `max_tokens` 2400 because 1200 stopped on length (1164 reasoning tokens, JSON cut).

Screens 783 `ot-valedia-desk`, 755 `valedia-glance`, 68743 `valedia-grok` still detached. Oil/weather fence unchanged. No Poly writer, no wallet, no venue order.

Commit skipped. `valedia_config.py` still holds uncommitted rows from earlier slices, so a CE-2-only commit would not check out clean.

**Tools:** RuFlo `hooks_route`, `hooks_pre-task`, `hooks_pre-edit`, `memory_search`, `memory_retrieve` (filtered `:8810`). CBM `list_projects`, `search_graph` (crypto paper indicators / `run_safety`), `check_index_coverage` on the paper modules (no recorded gap; best-effort). Ponytail `PONYTAIL-AUDIT-CE-2-QWEN-CRON-MACD.md`.

**Next = CE-3.** Parent launches it. This job did not. Do not unfence oil/weather. Do not treat the paper fill as realized profit.

`GROK_EXIT:0`
