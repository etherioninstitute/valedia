# MiroFish dynamic weight + self-readjustment (Burton 2026-09-18 Option B)

**NORTH STAR (Burton):** Valedia = world-class auto-trader — smartest + highest-profiting under gates. Every slice serves that. Seamless one-brain. Self-adjust when flipped on. Live risk gated until Burton widens fences.

**Plain English:** MiroFish is Valedia’s “world/news” ear. 10% today is training wheels, not forever. When Burton flips Valedia into smart/live-learning mode, **dormant switches must wake and re-tune themselves** from fee-true evidence — they must not stay off out of habit.

## Principles

1. **Evidence before size.** Weight only moves on fee-true accuracy (and, after NT-2, enriched vs plain tags). No vibes.
2. **Bands, not a hard religion.** Unproven: low cap. Proven: Burton-set band (proposed default **5–25%**, hard ceiling **30%** until Burton raises it).
3. **Dormant ≠ dead.** Flags like `MIROFISH_AUTO_RAISE=False`, strategy pauses, neural enrichment off — are **gates**, not graves. A single Burton master enable (see below) turns learning loops on; Valedia then readjusts within bands.
4. **One live risk owner.** Dynamic weight never opens venue writers or bypasses Kelly/FTL.

## Proposed controls (implement later; design now)

| Knob | Default now | After Burton “smart learning” flip |
|------|-------------|-------------------------------------|
| `MIROFISH_UNPROVEN_CAP` | 0.10 | still 0.10 until `MIROFISH_MIN_SCORED` |
| `MIROFISH_PROVEN_CAP` | 0.15 | raise to **0.25** (band top) when flip + proven |
| `MIROFISH_FLOOR` | (implicit 0) | **0.05** once flip on (don’t starve a working ear) |
| `MIROFISH_AUTO_RAISE` | False | **True only under master flip** — step up/down inside band from ledger |
| `MIROFISH_ACCURACY_BAR` | 0.55 | keep; below bar → decay toward floor |
| Enrichment | side JSON (NT-1) | NT-2 wires tags; optional small bonus *inside* band if enriched accuracy > plain |

### Master flip (name TBD — e.g. `VALEDIA_SELF_ADJUST=1`)

When Burton enables it (env or config, explicit yes):

- Temporary strategy pauses may re-open on fee-true edge (existing blacklist policy).
- `MIROFISH_AUTO_RAISE` becomes allowed **inside the band**.
- Neural enrichment cron/path may run if NT-2+ shipped.
- Shadow→live still needs separate venue gates (SPOT GET, writers, etc.) — self-adjust ≠ all fences open.

When master flip is **off**: current seatbelts (auto-raise off, 10/15 caps) stay.

## Implemented (0.30.0 — DYNAMIC-BAND)

Knobs live in `valedia_config`: `VALEDIA_SELF_ADJUST=False` (env `VALEDIA_SELF_ADJUST=1` wins). Seatbelted path unchanged (AUTO_RAISE False, 10/15 caps). Flip on: `_band_weight` from fee-true ledger — unproven 0.10, proven raise to 0.25, n≥min and below bar decay to 0.05, enrich bonus 0.02 inside band, hard ceiling 0.30. **Do not True the constant in live config.** Pause TTL re-open and neural enrich may run under the flip; writers / SPOT GET / NAUTILUS_LIVE stay separate fences.

## NT-2 scope (this Build session)

**Do:** wire enrichment into MiroFish seed/extractor; accuracy ledger `neural_enriched` tag; tests; no weight math change yet.  
**Do not:** set `MIROFISH_AUTO_RAISE=True`; do not widen caps in live config until Burton accepts this doc + master flip.

## Success later (post NT-2, when flip on)

Valedia raises MiroFish weight when enriched+plain accuracy clears the bar, cuts it when it doesn’t, never exceeds the band, and never leaves “auto-raise off” as a forgotten fossil after Burton asked her to be smart.
