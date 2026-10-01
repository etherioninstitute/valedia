# Ponytail audit — VAUGHAN-SUBSUME-2

**Mode:** `/ponytail` on the INTEL slice. Display and offline tags only.
**Fence:** no `ETORO_LIVE`, no Poly writer, no wallet, no oil/weather unfence. Screens `ot-valedia-desk` (783), `valedia-glance` (755), `valedia-grok` (98551) not killed.

## Applied

`reuse:` BB-Terminal `tally` (bull − bear, Sparse under 3 informative tags). **Shipped** in `web/lib/intelSignals.ts`. Equity fundamental helpers were not copied.

`reuse:` FOMO thresholds from `fomo_radar_enrich.py` (trusted 60, burst 4 / 3 wallets / 180s, exit 0.5 / 2 sellers, seed 3). **Shipped** so the panel tags `burst`, `exit`, and `seeded` without a Python subprocess.

`reuse:` paper JSONL already on disk. **Shipped** as a GET of the last 6 lines. The route does not append.

## Skipped

`yagni:` OpenBB quote, profile, metrics, and analyst feeds. **Not added.**

`yagni:` order, copy, and wallet buttons. **Not added.** Cached `create_order` / `start_copy` true is shown as an alarm and forced false on the view.

`yagni:` a live FX price vendor. EUR/USD, GBP/USD, and USD/JPY are offline labels.

`yagni:` restarting glance so `:8765` prints 0.68.0. **Not restarted.**

`yagni:` a git commit. **Not committed. Not tagged.**

## Findings

`shrink:` one scorecard module plus one GET. The TypeScript tagger can drift from `fomo_radar_enrich.py` if those numbers change. Leave the duplicate until a third caller exists.

`shrink:` three ways onto the panel (default desk, sidebar, command `INTEL`). The desk needs one visible panel. The extra doors are the existing add-widget path.

**net:** one read-only INTEL panel, one GET, version `0.68.0` working tree. `create_order` false. `start_copy` false.
