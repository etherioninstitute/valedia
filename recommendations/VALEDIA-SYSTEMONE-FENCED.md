# VALEDIA-SYSTEMONE-FENCED — Nimble 2 typed decisions (shadow, opt-in)

**Updated:** 2026-10-01  
**Status:** shadow opt-in. Default **off** (`VALEDIA_SYSTEMONE` unset).  
**HTTP:** `127.0.0.1:11434` only. `POST /v1/systemone`.  
**Model pin:** Nimble 2 (`Bespoke-Nimble-9B-v2`, 8,192 ctx). Hugging Face id `bespokelabs/Bespoke-Nimble-9B-v2`. Default Ollama tag `nimble:latest`, override with `VALEDIA_SYSTEMONE_MODEL`.  
**Digest pin (2026-10-01):** Burton confirmed his Nimble 2 is `nimble:latest`, digest `24e550a16a70` (`ollama list`: 9.5 GB). A floating `latest` can change on the next pull, so `nimble:latest` (and bare `nimble`, which Ollama resolves to `nimble:latest`) is accepted ONLY while `GET /api/tags` shows that model with a `digest` starting with `NIMBLE2_DIGEST = "24e550a16a70"`. `health()` / `warmup()` / the first `ask()` verify it and cache the result per process (a failed check is retried after 30 s; a passed check expires after `DIGEST_OK_TTL_S` = 300 s, env `VALEDIA_SYSTEMONE_DIGEST_TTL_S`, then is re-verified, so a re-pull with another digest stops POSTing within 5 min). No digest, no `/api/tags`, or a different digest: refused, never POSTed, fail-open stub, and the decision log records `error` = `digest_unverified` or `digest_mismatch`. Deliberate re-pin after a new pull: `VALEDIA_SYSTEMONE_DIGEST=<hex prefix, 12+ chars>`. Explicit v2 names below need no digest.  
**Confirm the tag:** the Ollama tag is not verified from the sandbox. Run `ollama list` on Burton's Mac and set `VALEDIA_SYSTEMONE_MODEL` if the installed name differs.  
**Accepted tags (full match, case-insensitive):** `nimble2`, `nimble2:<tag>`, `nimble:2`, `nimble:v2`, `nimble:9b-v2` (each optionally `-<quant>`, e.g. `-q4_k_m`, `-q8_0`, `-fp16`), and the release name `bespoke-nimble-9b-v2[:<tag>]`, bare or under `bespokelabs/` or `hf.co/bespokelabs/`. Digest-gated (above): `nimble:latest` and bare `nimble`. Refused and never posted: explicit Nimble v1 tags (`nimble:9b`, `nimble:1`), anything containing `v1`, `tev` or `0.8`, loose forms like `nimble:2b` / `nimble:20b`, and any other namespace or host (no provenance).  
**Receipt:** `recommendations/NIMBLE-SYSTEMONE-REPLACE-LAYA-2026-09-30.md`  
**Replaced:** Laya (retired 2026-09-30). `VALEDIA-LAYA-FENCED.md` is retired.

## What it is

Local typed shadow on the paper movers scan. Same three judgments as pack `valedia-paper-v1`: `admit_paper`, `edge_quality`, `venue_class`. Fail-open. `mode=shadow`. `enforcement=none`. `judge=dont_trade`.

## What it is not

- Not live authority. Code owns the fee-true label and the would-have size. `create_order` stays false.
- Not Kelly / OMS. A high `admit_paper` does not size.
- Not the desk chat model. Desk stays LMS Ornith. Do not unload LMS to make room.
- Not Nimble v1 and not Tev1. v1 tags and pins containing `tev` or `0.8` are refused and are not posted. Tev1-4B is not the backup on this path.
- Not Mirofish Ollama. Do not set `OLLAMA_HOST` to `:11435`. Do not restart `com.burton.ollama-mirofish`.
- Not a bind on glance `:8765`.

## Fence

- Enable for one process with `VALEDIA_SYSTEMONE=1`. Leave it unset on crons so a scan does not cold-load 9 GB beside resident Ornith.
- Jev (TypeSafe cloud) is retired and deleted from code (2026-10-01): no cloud URL, no key, no `VALEDIA_JEV`. Nimble 2 is the only decision model.
- No `--live`. No venue flips. No wallet or keys in the state. Oil `NO_HARVEST`. Weather watch. ALLOWLIST unchanged.
- Health is `GET /api/version` and `GET /api/tags` only. `model_present` is true only when the configured Nimble 2 tag is listed (for `nimble:latest`, also with the pinned digest: otherwise `error` is `digest_mismatch` / `digest_unverified`); otherwise the scan fails open to the same fee-true label. Do not stack a decide in front of a scan.
- The scan row label is `label` (fee-true, code-owned). The scan JSON carries a `systemone` block (backend, model, health) and no per-backend retired block.
- Non-finite prints are null. Ollama rejects JSON `NaN`.

## Gates live in code (principle)

Nimble 2 is weak at arithmetic and counting. It gets one narrow question per call; every hard number and comparison is enforced by deterministic code around it. `facts()` (and `nimble_annotate`'s report / weather / conviction / scan facts) compute booleans such as `edge_positive`, `clears_size_bar`, `thin_tape`, `is_oil`, `fv_vs_book` from the scan's own constants (`LIMITS`). The questions refer to those facts in words and contain no digits (asserted in tests). The model's answer never sets a label, size, rank, or order.

- **Decision log:** every call, yes and no, appends one JSON line to `data/systemone/decisions.jsonl` (git-ignored; `VALEDIA_SYSTEMONE_LOG` overrides): ts, source, model, full state, raw response or error, parsed answers (confidence, probabilities), latency, `within_budget`, and the code's own `label`. Covers the movers scan, warmup, `nimble_decide` and `nimble_annotate:<kind>:<caller>` notes. No keys or headers.
- **Latency:** warm target under 500 ms (`VALEDIA_SYSTEMONE_BUDGET_MS`). `warmup()` runs once at the start of an enabled scan; the 120 s timeout stays for cold loads. `summary()` reports warm p50/p95, `cold_n`, `within_budget_n`.
- **Notes (`nimble_annotate`, replaces Jev):** with `VALEDIA_SYSTEMONE=1`, Kalshi CONVICTION at the unwired `kalshi_executor` (`conviction_note`: prediction_facts on the INFORMED dict + informed_facts), weather_shadow rows (agree / status_honesty), MiroFish signal docs (report_usable / stand_down / we_are_the_exit) and the movers scan get a `nimble_note` built from code facts. Annotate only: never gates, sizes, or changes score, direction, bias, weight, or Kelly. Sites with `nimble_wiring.gate` use that verdict, no second call.

Nimble stamps. It does not size and it does not send.

## Quick decisions for every asset class (`nimble_decide`)

`scripts/integrations/nimble_decide.py` gives stocks, crypto and prediction markets one entry point: `decide_candidate(asset_class, candidate, *, side, code_facts, informed=None, source, label=None, post=None, get=None)`. The model answers one narrow question (`action`: buy / hold / skip, plus a `risk` label from the first to the last of ten); the questions contain no digits except the risk labels. Code builds the facts: `stock_facts` / `crypto_facts` (fused_strong, filter_passed, fee_ok, already_held, trend_up_5d, momentum_dominant, regime_risk_off) and `prediction_facts` (edge_positive, fee_true_edge_positive, fv_present, fv_source_allowed (`ALLOWED_FV`), official_resolution, clock_ok, depth_ok, spread_tight, side_yes). Numbers are dropped from the state, so only booleans and identifiers reach the model. Unknown inputs become `None`. Nothing is guessed.

- **Informed facts** (`informed_facts.py`, offline, no network): MiroFish only via `mirofish_intel_archive.load_for_fv` (under 24 h, `mirofish_swarm`, not neutral; a lightweight `intel_refresh` doc is ignored), Finnhub buzz passed in by the caller plus the cached `prediction_log.json`, and ERE via `ere_runtime.read_gate("BOOK")` (ERE v3 canonical gate, fail-closed; risk-off levels only, FADE conflict retired with v2). `intel_layer` is skipped because it fetches Fear/Greed. `sources` (provenance and ages) goes to the log, not to the model. `systemone_hotpath_client.load_informed` now uses the same freshness loader.
- **Modes:** `VALEDIA_NIMBLE_MODE_STOCK` / `_CRYPTO` / `_PREDICTION`, else `VALEDIA_NIMBLE_MODE`, else **shadow**. `off` makes no call and allows. `shadow` calls (only with `VALEDIA_SYSTEMONE=1`), logs and **always allows**. `veto` allows only when the profit-consistent veto rule passes (see Confidence semantics). Engine off, error, timeout, refusal or digest mismatch → block (fail-closed).
- **Veto only removes orders.** It never adds an order, sizes, flips a side or changes a price. Exits (sell / exit / close) are never blocked and never sent.
- **Speed:** warmup once per process; per-call timeout `VALEDIA_NIMBLE_TIMEOUT_S` (default 10 s); the target stays 500 ms. `decide_many` stops calling after `VALEDIA_NIMBLE_MANY_CAP_S` (default 30 s); the remaining items get the mode default (shadow allows, veto blocks entries).
- **Log:** one `decisions.jsonl` line per call, `source = nimble_decide:<source>`, `label = {code, mode, rule, informed_sources}` (rule = the veto inputs, local log only, never posted), so a review can compare the model with the code.
- **Wired** (`nimble_wiring.gate`, last gate after the code admits): the prediction points are wired: `master_allocator.execute_allocations` (live Kalshi), `sports_evidence` (live sports; MiroFish team/series facts as the underlying), `weather_live.admit` (live weather), `poly_loop` (paper) and `vaughan_arb_paper` (paper). Mode default **shadow**; unset `VALEDIA_SYSTEMONE` on crons: zero behavior change.
- Informed facts also carry offline FOMO-radar facts for crypto (`fomo_present`, `fomo_wallet_conviction_high`, `fomo_exit_signal`, `fomo_provenance_ok`). Annotate only; they never size.

### Confidence semantics

Nimble's `confidence` is 1 - normalized entropy of `probabilities` (sharpness), not P(chosen action). Verified on Burton's Mac: buy 0.4765 / skip 0.5235 gives skip with confidence 0.00159. Verdicts carry `p_action` and `p_buy` (from `probabilities`, None when absent).

Veto rule (entries only; exits never blocked; shadow computes and stamps it but never blocks):
allow iff action == buy AND confidence >= `VALEDIA_NIMBLE_MIN_SHARPNESS` (0.05, a not-a-coin-flip floor; the old `VALEDIA_NIMBLE_MIN_CONF` is ignored, with a one-line deprecation note) AND `p_cal` >= `required_p`.
- `p_cal` = `calibration_ledger.calibrate("nimble_decide:<point>", p_buy)`: identity under 30 scorable decisions, then Laplace-smoothed isotonic (PAV) fit in `calibration_map.py` (`calibration` = identity | isotonic).
- `required_p` = max(`VALEDIA_NIMBLE_P_FLOOR` 0.60, `break_even_p` + margin); floor alone when break-even is unknown.
- `break_even_p` (code only, never in the model state): prediction = bought side's price (a no buy needs an explicit NO price — no `no_ask`/marked NO price → None, floor applies) + fee per contract (+ half spread / slip); spot = 0.5 + round-trip fee % / 200 (approximation for a symmetric outcome; understates for real moves).
- margin = `VALEDIA_NIMBLE_BASE_MARGIN` 0.02 x phi_multiplier = 0.5 + 2.5 x (1 - `valedia_config._risk_factor()`), the Etherion scalar the dynamic Kelly cap uses (NORMAL 0.5 .. HALT 3.0; missing/stale → 1.0).
Reasons: `veto_not_sharp`, `veto_no_p_buy`, `veto_p_below_required`, `veto_model_<hold|skip>`; allow = `veto_model_buy`.
