# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-18 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** H5 + DYNAMIC-BAND SHIPPED. VALEDIA_VERSION `0.30.0` tag `valedia-v0.30.0` tip `a7801d3`. Next = STOP for Burton.

**Tests:** `test_h5.py` PASS. `test_dynamic_band.py` PASS. `test_slice10` / `test_slice11` / `test_slice13` / `test_slice18` / `test_h2` / `test_h3` / `test_h4` / `test_nt1` / `test_nt2` / `test_slice23` / `test_adversarial_paper_fight` PASS.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-H5.md` + `recommendations/PONYTAIL-AUDIT-DYNAMIC-BAND.md`

**H5:** Desk lifecycle = labels on shadow Intents (`research|risk|exec|review`). Public HL `/info` → `models/hl_public_info.json` (stdlib urllib, HTTP off, injectable). Gatherer `source_feed=hl_public`. Ticket twin; `parse_approve` grammar only (no send). `VENUE_WRITER["hyperliquid"]=none`. No Exchange.

**DYNAMIC-BAND:** `VALEDIA_SELF_ADJUST=False` (do not True in live config). Flip off: AUTO_RAISE False, 10/15 caps unchanged. Flip on: in-band raise 0.25 / decay 0.05 / enrich +0.02 / ceiling 0.30. Never opens writers.

**Fence:** No `--live`, no venue orders, no API-key edits, no `VENUE_WRITER` flips, no `SPOT_RECONCILE_HTTP`, no `NAUTILUS_LIVE`, no LN/Arena funding, no crontab for ADV.

**Next:** STOP for Burton. Do not start H6 / Arena funding / NT live.
