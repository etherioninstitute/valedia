# WEATHER-FIRST-LIVE-ADMIT-1

**Owner:** Grok Build (opt-in) + Valedia runtime for any live create
**Goal:** Take the first real NWS CLI-final / evening INFORMED weather admit under a hard 1-create cap, now that WEATHER-INFORMED-ADMIT-BRIDGE-1 cleared the five INFORMED refuses.
**Fence:** Oil fenced. Build never Kalshi POST / wallet / --live. Live create only via weather_live.admit under existing WEATHER_LIVE ownership. Max 1 create total for this charter.

## Context
Bridge PASS 2026-09-25: stub admit on KXHIGHDEN-26SEP25-T72 → reasons=[] create_order=True. Shadow/dry stay non-creating. Lottery 0.05 / late 0.90 unchanged.
Burton authorized one live create if gates pass (2026-09-25 ~20:02 PT via Valedia Trader).

## Acceptance criteria
1. Identify one NWS-settled official alert that is CLI-final (or observation) on the correct observe-date — prefer evening CLI-final window over same-morning non-final.
2. Preflight: WEATHER_LIVE env + weather_live_enabled path; oil still fenced; document candidate ticker, fv, price, fee_true_ev, informed payload, projected size (≤1 contract unless kelly/would_have_size forces smaller — hard cap count=1).
3. Build proof path: stubbed post still returns create_order=True / reasons=[] for that candidate (regression on bridge).
4. Live path (Valedia only, after Build proof): one admit attempt with real post allowed ONLY if Burton already authorized this charter; append weather_live_ledger with empty reasons or explicit fail reasons; never more than 1 create.
5. If no CLI-final alert is available in-window, exit PASS with diagnosis (why-no-trade) and do not force a lottery/late/wrong_date book.
6. Update WEATHER-MORNING-DIAGNOSIS / LAST JSON with admit reason counts + this first-live result.
7. Promote only if code changes were needed for evening CLI-final timing / 1-cap enforcement; otherwise ops-only with ledger proof.
8. GROK-STATUS Next=Burton stop after exit. No auto-chain. Preserve ot-valedia-desk + valedia-glance.

## Out of scope
Oil unfence, mass creates, loosening lottery/late, paper desks, flipping WEATHER_LIVE from Build.

## Done when
GROK_EXIT:0 with either (a) one documented live admit ≤1 contract + ledger proof, or (b) clean no-trade diagnosis that no CLI-final INFORMED alert existed in window.
