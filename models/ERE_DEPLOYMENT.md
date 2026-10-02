# ERE v3 (ere/2) — deployment decisions

## `all_risk_increasing_readers_migrated: false` — INTENTIONAL, kept false

The release contract (see `README.md` → "Remaining integration obligations" and
`REPORT.md`) requires the acknowledgment flag to stay `false` until:

1. **KALSHI scope closes**: a joint-event scenario adapter and exact venue fees
   are supplied. Until then, an unmodelled held binary inventory closes the book;
   new binary orders must explicitly request KALSHI and do NOT inherit generic
   BOOK health.
2. **All remaining direct readers/executors are migrated.** Verified migrated as
   of 2026-10-01 (commit 90fdcd3 + follow-ups): every risk-increasing consumer
   reads the canonical fail-closed gate —
   - `valedia_config._risk_factor` → `read_gate` (Kelly cap via
     `dynamic_per_trade_cap`; never bypassed, never squared, malformed → 0)
   - `nimble_decide.phi_multiplier` (phi = 0.5 + 2.5·(1−rf); strictest 3.0 on
     HALT/reader-closed, never loose on a fault)
   - direct `read_gate`: `unified_brain`, `nautilus_shadow_venues`,
     `nautilus_shadow_coinbase`, `intel_layer`, `intel_opportunity_gatherer`,
     `quant_engine`, `sports_engine`, `informed_facts`,
     `adversarial_paper_fight`
   - Zero direct reads of the retired `latest_etherion_risk.json` remain in
     risk-increasing paths (survivors: hermetic test fixture, archive,
     doc-comment mentions only).

Flipping the flag is a separate Burton decision after the KALSHI adapter lands.
It is NOT a deployment blocker for the paper BOOK path.

## Producer

- Cron: `20 * * * *` (hourly at :20) —
  `~/.openclaw/workspace/.venv-ere/bin/python ~/.openclaw/workspace-v3/scripts/integrations/ere_runtime.py --models ~/.openclaw/workspace/models >> ~/.openclaw/logs/ere_state.log 2>&1`
- Dedicated venv (`.venv-ere`): numpy 2.3.5, scipy 1.17.0, pandas 2.2.3,
  exchange_calendars 4.13.1, yfinance (network dep unpinned per the release:
  "not live-network tested in this work").
- `mode: PAPER_LOCKED` — no statistical live mode; a future statistical release
  needs new frozen evidence (REPORT.md acceptance checklist).
- Until a real `ere_positions.json` account export exists, the producer
  publishes a **diagnostic HALT (multiplier 0.0)** naming the missing positions
  file in its heartbeat. This is the designed fail-closed behavior: the book
  cannot admit new risk while position truth is unknown. Do NOT fabricate a
  positions export; wire the real account reconciliation when available.
- A live target-overlay controller and order-race protection are NOT provided by
  the release and remain future work.

## Retired with this release

- `Etherion_Risk_Engine.py` (network/physics-alias heuristics) → archive
- `backtest_etherion.py` → archive
- `latest_etherion_risk.json` file contract (all readers)