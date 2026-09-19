# Valedia seamless one-brain architecture

**Date:** 2026-09-18  
**Status:** H1 design (post NT-2 `71a39f9` / `valedia-v0.25.0`)  
**NORTH STAR:** world-smartest, highest-profiting **dynamic** auto-trader. One brain. Self-adjust when Burton flips smart mode. Live risk gated until he widens fences.

Hard fence (this doc does not lift): no venue `--live` writers, wallet/spend, `VENUE_WRITER` flips, `REBALANCER_AUTOEXEC`, `SPOT_RECONCILE_HTTP`, `NAUTILUS_LIVE`. `MIROFISH_AUTO_RAISE=False` (band design exists; do not implement the flip this slice).

Dossiers: `recommendations/ADAPT-DOSSIERS/`.

---

## One sentence

Everything we steal from disk becomes **intel, shadow, UI, or OMS-event practice**. Nothing becomes a second Kelly, second ledger, or second writer.

---

## Diagram

```
                    ┌─────────────────────────────────────────┐
                    │  OpenTerminal  (H4)  READ-ONLY GET      │
                    │  version · mode · FTL · shadows         │
                    │  MiroFish weight/acc · neural · personas│
                    └────────────────────▲────────────────────┘
                                         │ files only
┌────────────────────────────────────────┴─────────────────────────────────────┐
│                         VALEDIA BRAIN  (sole live risk)                       │
│  Kernel v16e → ERE → fusion (FUSION_WEIGHTS) → KellySizer → fee-gate          │
│       → category/pause → allocator / sports  (writers behind flags)           │
│  Canonical money truth: forward_test_ledger  (fee-true; paper never sizes)    │
│  Config: valedia_config.py  (one weight, one Kelly, one writer map)           │
└──────────▲──────────────────────────────────────────────▲────────────────────┘
           │ intel + shadow                               │ fills (later NT)
           │                                              │
┌──────────┴───────────────┐                 ┌────────────┴──────────────┐
│  MiroFish swarm (:11435) │                 │  Nautilus sidecar (H6)    │
│  seed ← neural (NT-2)    │                 │  IntentConsumer only      │
│       ← personas (H2)    │                 │  NAUTILUS_LIVE=False      │
│       ← arena tags (H7)  │                 │  event timeline in SHADOW │
│  extractor → signal JSON │                 └───────────────────────────┘
│  accuracy ledger (capped)│
└──────────▲───────────────┘
           │
┌──────────┴───────────────────────────────────────────────────────────────┐
│  intel_opportunity_gatherer  →  never-dispatchable Intents (paper)        │
│  feeds: MiroFish · fusion · hot_list · hedge · scanner · arb · (H5 HL /info)│
│  desk roles (H5) = labeled passes: research | risk-veto | review          │
│  risk-veto may ZERO size; may not compute a competing kelly_frac          │
└──────────▲───────────────────────────────────────────────────────────────┘
           │
     venues behind exclusive VENUE_WRITER
     kalshi/coinbase/alpaca = valedia
     hyperliquid/polymarket/kraken = none
     Kalshi live path: sports + allocator (H3 = fill-true, not a new writer)
     fortress (H8) only on intel HTTP fail, flag default off
```

Self-adjust (design only — `VALEDIA-MIROFISH-DYNAMIC-WEIGHT.md`):

```
Burton VALEDIA_SELF_ADJUST=1
    → MIROFISH_AUTO_RAISE allowed INSIDE band (floor 5% / proven cap 25% proposed)
    → dormant pauses may re-open on fee-true edge
    → neural + persona tags can earn a bonus *inside* the band
    ✕ does not open venue writers, SPOT GET, NAUTILUS_LIVE, or a second Kelly
```

---

## Shared contracts (do not fork)

| Contract | Owner |
|----------|--------|
| `VALEDIA_VERSION` + git sha | `valedia_config.version_stamp()` |
| Fusion weights | `FUSION_WEIGHTS` → `unified_brain.stage_fusion` (`signal_fusion.LIVE_PATH=False`) |
| MiroFish cap | `effective_mirofish_weight()`; unproven 0.10 / proven 0.15; `AUTO_RAISE=False` |
| Sizing | `kelly_sizing.KellySizer` only |
| P&L | `forward_test_ledger` fee-true; UNRECONCILED ≠ Profit |
| Writer lock | `VENUE_WRITER` + `assert_exclusive_writer` |
| Intent | `nautilus_intent` — size never rounds up; shadow never dispatchable |
| Status labels | exactly one of LIVE / DRY / SHADOW / FENCED; ACK ≠ filled |

---

## How each repo plugs in (one row)

| Repo | Becomes | Never becomes |
|------|---------|----------------|
| **ai-fund** | H2 persona lenses on MiroFish/intel JSON | 45 MCP agents, Thorp Kelly, `place_order` |
| **OpenTerminal** | H4 GET cockpit | BUY/SELL form, Claude assistant, venue POST |
| **hypergrok** | H5 desk *passes* + public `/info` snapshot | `/exchange`, stop-% sizer, 7 Grok Bots |
| **nautilus_trader** | H6 shadow **event** model (Ambiguous ≠ denied) | LiveNode, LGPL in-process, Kalshi-via-Betfair |
| **kalshi-agent** | H3 fill-true / scan / Brier-from-FTL | UNLICENSED tree, flat $0.07 Kelly, Electron `--live` |
| **lf-game-theory** | H7 paper-sats stress / primes | Lightning faucet, fusion weight |
| **fortress** | H8 fail-gated allowlisted fetch | WAF-beating default scraper, venue UIs |
| **neural-trader** | **Done NT-0…2** — enrich JSON only | `trader live`, second portfolio |

---

## Data flow (allowed arrows)

```
public APIs / RSS ──► seed ──► MiroFish ──► signal JSON
neural-trader CLI ──► enrichment JSON ──► attach (scores unchanged)
persona checklists ──► lens JSON ──► attach (scores unchanged)
gatherer ──► shadow Intents ──► queue (dispatchable=False)
FTL settlements ──► MiroFish accuracy ──► capped weight
KellySizer ◄── FTL real track only
allocator/sports ──► venue POST ──► fill GET ──► FTL
OpenTerminal GET ──► all of the above (display)
```

**Forbidden arrows:** persona/neural/arena/NT/OpenTerminal → `create_order` / `VENUE_WRITER` / `KellySizer.set_*`.  
**Forbidden:** `FUSION_WEIGHTS["arena"]`, `FUSION_WEIGHTS["buffett"]`, second `kalshi-trades.json`.

---

## Self-adjust vs fences (two knobs)

| Knob | Wakes on master flip | Still needs a **named** Burton go |
|------|----------------------|-----------------------------------|
| MiroFish auto-raise inside band | yes | band widen past 0.25 / 0.30 |
| Strategy pause TTL | yes (already policy) | — |
| Neural enrich cron | yes if NT-2+ shipped | — |
| Persona lenses | yes as intel | new fusion key |
| Spot GET / writers / NT live / AUTOEXEC | **no** | each flag separately |
| Arena funded sats / fortress engine | **no** | H7/H8 Burton go |

Dormant ≠ dead. Forgotten `False` after Burton asked for smart mode is the failure this architecture forbids — **inside intel/weight only**.

---

## Slice order (unchanged charter)

| Slice | One-brain job |
|-------|----------------|
| **H1** | this file + dossiers |
| **H2** | Buffett/Lynch/Hayes lenses → MiroFish attach |
| **H3** | Kalshi fill-true + scan hygiene into existing writer |
| **H4** | OpenTerminal Valedia GET panel |
| **H5** | desk passes + HL public info file |
| **H6** | shadow OrderEvent + Ambiguous |
| **H7** | paper Arena stress JSON |
| **H8** | fortress fallback flag, default off |

STOP between slices. Ponytail audit after every coding slice.

---

## Success test

A stranger can point at any adapted piece and answer “where does this enter the brain, and what is it **not** allowed to do?” If the answer is “it has its own Kelly / ledger / cron / send button,” it is not seamless — delete or demote to advisory JSON.
