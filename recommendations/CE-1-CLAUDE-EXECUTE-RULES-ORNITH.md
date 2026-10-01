# CE-1 — claude-execute → Valedia: rules.json + Ornith safety (crypto paper)

**Burton go:** 2026-09-22 ~16:46 PT — “start CE-1”  
**Upstream:** https://github.com/vaughanf1/claude-execute (rules.json + fail-closed safety checklist pattern)  
**LLM Stack map (locked):** safety+bias → Ornith-1.5 Q5 `ornith-1.5-k-m`; snappy tick → Qwen3.8-27B (not this slice’s decider); dual-load OK; never fail-open live-shaped checks on Qwen-only.

## Goal
Subsume the **rules.json + fail-closed safety checklist** idea from claude-execute into Valedia’s **CRYPTO-PAPER** desk only:

1. Ship a Valedia `rules.json` (or equivalent under `data/crypto-paper/` / `config/`) that encodes entry/exit/risk conditions as machine-checkable fields (not free-prose-only).
2. Compute indicators **deterministically in Python** from allowlisted bars (fixture default; optional public klines) — no TradingView MCP hard dep, no Claude Code OMS.
3. Call **Ornith-1.5** (`ornith-1.5-k-m` via LiteLLM `:4000` or Bionic) as the **fail-closed safety / bias evaluator**: every entry condition must PASS with evidence values, or refuse. Write a decision log (claude-execute’s `safety-check-log.json` shape adapted).
4. On PASS, apply fills only through existing `crypto_paper.apply_fill` / paper ledger — **would-have** only. Never exchange execute.

## Existing Valedia anchors (reuse, do not fork a second desk)
- Ledger: `scripts/integrations/crypto_paper.py` + `crypto_paper_run.py`
- Docs: `recommendations/CRYPTO-PAPER-DESK.md` (`PAPER/SHADOW`, `CRYPTO_PAPER_MODE`)
- FOMO enrich (annotate only): `fomo_radar_enrich.py` — do not size from conviction
- Controls: `CRYPTO_PAPER_MODE` stays `PAPER/SHADOW`
- Allowlist pairs: keep `BTC-USDT` / `ETH-USDT` unless extending the allowlist with a one-line note

## Scope (do)
1. **Clone / read upstream once** (MemPalace or `/tmp`) for shape reference only — `rules.json`, safety-check log fields. Do **not** vendor `bot.js` exchange paths, Railway, or BitGet keys.
2. **Author Valedia rules artifact** e.g. `data/crypto-paper/rules.json` (or `config/crypto_paper_rules.json`) with:
   - watchlist / timeframe
   - indicator specs (e.g. EMA, RSI, VWAP or Valedia’s existing SMA hook — prefer indicators already computable offline)
   - `bias_criteria`, `entry_rules`, `exit_rules`, `risk_rules` as **structured checks** the evaluator can mark pass/fail with numeric evidence
3. **Deterministic indicator module** in `scripts/integrations/` (e.g. `crypto_paper_indicators.py`) — pure functions over OHLCV; unit-tested; no LLM inside indicator math.
4. **Ornith safety evaluator** (e.g. `crypto_paper_safety.py`):
   - Input: rules + indicator snapshot + proposed side/size
   - Model: `ornith-1.5-k-m` (LiteLLM alias preferred; document exact route)
   - Output: JSON `{pass: bool, bias, failed_conditions[], evidence{}, model, ts}` — **fail-closed** if model down, malformed JSON, or any required condition missing
   - Never treat missing Ornith as soft-pass; never fall through to Qwen for this gate in CE-1
5. **Wire into paper path:** CLI flag or subcommand on `crypto_paper_run.py` (e.g. `--rules` / `--safety`) that:
   - loads bars (fixture default)
   - computes indicators
   - runs Ornith safety
   - on PASS → paper fill via existing ledger; on FAIL → log refuse, no fill
   - appends `data/crypto-paper/safety-check-log.json` (+ optional CSV mirror of would-have decisions — not live tax accounting)
6. **Tests:** fixture-only proofs — PASS path with mocked Ornith JSON; FAIL path; Ornith-down → refuse; no network create_order. Extend `test_crypto_paper.py` or add `test_crypto_paper_safety.py`.
7. **Controls / docs:** one Controls or desk doc line that CE safety is `PAPER/SHADOW` + Ornith-gated. Update `CRYPTO-PAPER-DESK.md` briefly. Version bump workspace-v2 if code ships (e.g. `0.61.0` working tree — tag only if you already tag other slices; otherwise note tip).
8. Ponytail audit + Results + `GROK-STATUS.md`. **Next=Burton** (stop). Do not auto-start CE-2/CE-3.

## Explicit non-goals (CE-2/CE-3 later)
- Qwen cron tick glue / MACD helpers as scheduled job
- Real exchange execute / CCXT create_order / BitGet / Railway
- TradingView MCP as hard dependency
- Wallet keys, signing, onchain
- Flipping `POLYMARKET_US_LIVE` / Kalshi writers / `WEATHER_LIVE` / oil unfence
- Replacing MiroFish or using Ornith unload during MiroFish report if both contend — if GPU lease conflicts, document and use LiteLLM route that shares the loaded Ornith; do not spin a third heavy
- Treating paper PnL as realized profit in Signal/status

## Hard fence
- Crypto stays **paper/shadow** until Burton enables real wallets
- Valedia runtime sole live risk — this slice never places venue orders
- Use screen `valedia-grok` only; do **not** kill `ot-valedia-desk` / `valedia-glance`
- No `--live` venue paths; no HALT bypass; no treasury auto

## Tools
RuFlo MCP `http://127.0.0.1:8810/mcp` (filtered ADR-035). CBM for navigation. Ponytail after code. Prove tool use in `GROK-STATUS.md`.

## Deliverables
1. rules artifact + indicator module + Ornith safety evaluator + paper CLI wiring
2. Tests green (fixture + mocked Ornith)
3. Example `safety-check-log.json` from a fixture run
4. `PONYTAIL-AUDIT-CE-1-CLAUDE-EXECUTE-RULES-ORNITH.md`
5. Results appended here + `GROK-STATUS.md`; **Next=Burton**

## Done when
- Fixture run shows fail-closed refuse without Ornith mock/pass
- Fixture run with mocked Ornith PASS applies **paper** fill only
- No exchange credentials required; no create_order in tree for this path
- Audit written; `GROK_EXIT:0`

## Results

**2026-09-22 ~19:40 PT. VALEDIA_VERSION 0.62.0** (working tree, not committed, not tagged).

Shipped `data/crypto-paper/rules.json`, `crypto_paper_indicators.py` (EMA/RSI/VWAP + existing SMA), `crypto_paper_safety.py`, `--safety` on `crypto_paper_run.py`. Control `CRYPTO_PAPER_SAFETY=PAPER/SHADOW+ORNITH`. `CRYPTO_PAPER_MODE` stays `PAPER/SHADOW`.

Tests `test_crypto_paper_safety.py` PASS: mocked Ornith PASS → one paper fill; Ornith refuse; local disagree; Ornith down; malformed; Qwen model name; missing condition; no-entry does not call the model. `test_crypto_paper.py` PASS. `test_controls_readout.py` PASS. `test_fomo_radar.py` PASS.

Live fixture (alias `ornith-1.5-k-m` at `:4000`, no mock): **PASS**, bias long, model `ornith-1.5-k-m`. Paper fill BTC-USDT buy qty 9.21802218 @ 108.53738257, fee 1.0005, source `paper`. Book `data/crypto-paper/safety-equity.json`: would-have PnL **-$1.5005** vs $10,000 start. `create_order` false, `live` false, `orderPlaced` false. Log `data/crypto-paper/safety-check-log.json`. Dead-port run refuses `ornith_down` with no fill.

Upstream read for shape only (rules.json + safety-check `trades[]`). `bot.js` / Railway / BitGet not vendored.

Screens 783 `ot-valedia-desk`, 755 `valedia-glance`, 4991 `valedia-grok` still detached. Oil/weather fence unchanged. No mass `create_order`.

**Tools:** RuFlo `hooks_route` / `hooks_pre-task` / `hooks_pre-edit` / `memory_search` (filtered `:8810`). CBM `list_projects`, `search_graph` (`crypto_paper.apply_fill`), `check_index_coverage` on the paper modules (no recorded gap; best-effort). Ponytail `PONYTAIL-AUDIT-CE-1-CLAUDE-EXECUTE-RULES-ORNITH.md`.

**Next = Burton.** Stop. Do not auto-start CE-2 or CE-3. Do not unfence oil/weather. Do not treat the paper fill as realized profit.

`GROK_EXIT:0`
