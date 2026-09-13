# Manuscript → Code Dependency Manifest

**Purpose:** make a manuscript update's blast radius explicit, so updating the UGFT/Etherion
math never silently drifts from the code that implements it (the v9/v16e mismatch class).

**How to update the manuscript safely:**
1. Edit the **one** canonical module for the affected math (below).
2. Bump its `MANUSCRIPT_VERSION` stamp (and `valedia_config.MANUSCRIPT_VERSION` if the framework version changed).
3. Run `python3 ~/.openclaw/workspace/scripts/valedia_consistency_check.py` — it fails if a second
   divergent copy of any functional appears, or a shared constant disagrees. It also runs nightly (07:30 cron).
4. Consumers pick up the change automatically via import — no per-file edits needed.

---

## Market math — canonical: `integrations/kernel_predictor_v16e.py`
- `MANUSCRIPT_VERSION = "UGFT-v32.9 (market functionals, impl v16e)"`
- **Functionals (defined here ONCE):** ns_trend_noise, ns_cascade, rh_D, rh_se, rh_conv, rh_prod,
  bsd_S2, bsd_flow, ym_gap, hodge_coh, rosetta_stab, adaptive_svd, clustering_horizon, lyapunov_dom
- **Consumers (import `KernelPredictor`):** `dca_module.py`, `dynamic_universe.py`, `ml_retrain.py`,
  `unified_brain.py` (kernel stage)
- History: superseded `kernel_predictor_v9.py` + suffixless `kernel_predictor.py` archived 2026-09-11.

## Risk math — canonical: `integrations/Etherion_Risk_Engine.py`
- `MANUSCRIPT_VERSION = "UGFT-v32.9 / NS-v11.0 / YM-v38.5"`
- **Functionals (defined here ONCE):** compute_sobolev_ladder, kotecky_preiss_criterion,
  energy_ladder_check, compute_spectral_gap, helicity_conservation, ford_roman_bound, trace_correspondence
- **Consumers:** `unified_brain.py` (ERE safety stage), `backtest_etherion.py`, `test_etherion.py`
- Note (from the module): these are HEURISTIC physics→market analogies to be calibrated, not proven constants.

## Shared constants — canonical: `integrations/valedia_config.py`
- `MIROFISH_WEIGHT` → `unified_brain.py`, `quant_engine.py`
- `CRYPTO_FEE_PCT`, `CRYPTO_SLIPPAGE_PCT` → (wire remaining consumers in the behavioral pass)
- **Known divergences tracked for the behavioral pass:** Kelly fraction (0.5 vs 0.25), Kalshi fee
  (6 representations; `fee_engine.py` canonical but bypassed), fusion weights (signal_fusion vs unified_brain).

## The guard
- `scripts/valedia_consistency_check.py` — nightly 07:30 (log-only, no Signal). Fails on: non-canonical
  kernel imports, a functional defined in >1 file, shared-constant disagreement, stale ML artifact, backups in the live tree.
