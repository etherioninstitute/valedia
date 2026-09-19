# H1 dossier — hypergrok-trading-desk

**Path:** `/Users/burtonchatelain/MemPalace/sources/repos/github/hypergrok-trading-desk`  
**Upstream:** Galleon Labs / `galleonlabs/hypergrok-trading-desk` (v1.4.3 template)  
**License:** MIT (Copyright 2026 Galleon Labs)  
**Slice:** H5 — desk **roles inside Valedia**, HL **public info** first. No HL live.

---

## Identity

Grok Bot “7-agent Hyperliquid trading desk”: Desk Lead, Market Analyst, Research Analyst, Strategist, Risk Manager, Execution Trader, Trade Reviewer. Skills are version-pinned markdown. First demo is **zero-key** `scripts/opening_bell.py` against public `/info`. Execution path is signed `/exchange` — **Execution Trader only**, after Risk PASS + user `approve HG-…` phrase.

---

## Architecture

```
idea → evidence → risk sign-off → user approval → one send → reconcile → review
 DL      MA/RA         RM              user           ET          ET         TR
```

- **Public:** `opening_bell.py` — mid/mark/oracle, 24h, funding, OI, spread, depth at 5/10/25 bps, `nSigFigs` pagination so wide bands are measured not guessed. Stdlib urllib. No env, no wallet.
- **Write:** `skills/hyperliquid-orders/SKILL.md` — `Exchange.order`, IOC-as-market, `normalTpsl`, cloid, round **size down**. This is a live writer skill.
- **Risk:** `desk-risk-limits` sizes from **live account** + **stressed stop % of equity**. Grep for `kelly` in the repo is **empty**. If imported as a sizer it is a *second allocator*, not a second Kelly formula. Use as **REJECT if Kelly size overshoots stop budget**, never as qty.

Valedia already has HL **shadow** POST bodies (`hyperliquid_order_body.py`, Slice 11 `nautilus_shadow_venues`) and `LIVE_CLIENT_HOOK=None`. Writer for `hyperliquid` is `none`.

---

## STEAL

1. **Seven-stage lifecycle as labels on shadow Intents** (not seven bots). Map: research=evidence, risk=ERE+fee+category, exec=Valedia writer (still fenced), review=FTL settle.
2. **`opening_bell.py` public book/funding snapshot** → intel file `models/hl_public_info.json` (H5). Feeds shorts/funding context; no keys.
3. **Ticket format** (instrument, side, size, stop, sources, UTC) as the human-readable twin of `nautilus_intent.Intent`.
4. **Approval grammar** — Burton must type an id; “yes” is not approval. Useful if a UI ever appears; OpenTerminal stays read-only so this is future.
5. **Round size down / never up** — already Intent rule (`nautilus_intent`: size never rounds up). Keep.
6. **Reconcile by cloid, not by POST-ack** — same lesson as Slice 22 UNRECONCILED. Steal the *habit* for HL when a writer exists.
7. **Process vs outcome review** (`desk-post-trade-review`) — FTL already judges money; review skill is journal quality.

---

## LEAVE

| Leave | Why |
|-------|-----|
| `hyperliquid-orders` + API wallet key | `/exchange` writer. `VENUE_WRITER["hyperliquid"]="none"` |
| Risk Manager live `clearinghouseState` sizing | Second sizer; Valedia `KellySizer` is canonical |
| 7 Grok Bots / Grok Bot template | Valedia is one brain; RuFlo swarm is coding, not live risk |
| Mainnet “approve HG-” send path | User-approval OMS. Burton’s live risk is Valedia cron under gates, not a chat approve |
| Leverage 2–5x / grouped TP/SL as default | SOUL: no leverage without approval; NT leverage 0 |
| Watching WS to auto-trade | Routines that ping then send |

---

## Valedia touchpoints

| Valedia | Hook |
|---------|------|
| `nautilus_shadow_venues.py` | HL short + PM arb never-dispatchable Intents (Slice 11) |
| `hyperliquid_order_body.py` | POST-diff vs stub `/exchange` Ioc-limit |
| `intel_opportunity_gatherer.py` | Optional `source_feed=hl_public` from opening-bell JSON |
| `nautilus_shadow_parity.py` | Fail-closed on side/size/venue/leverage/dispatchable |
| `valedia_config.VENUE_WRITER` | hyperliquid stays `none` |

---

## Risks

- Copying the orders skill “for completeness” creates a writer. **Do not import Exchange.**
- Desk Lead as a chat persona that can be sweet-talked into send — Valedia gates are code, not a bot.
- Public `/info` is fine; treating it as fill truth is not.
- MIT — attribute Galleon Labs on any rewritten `opening_bell` helper.

---

## Seamless hook

Desk roles collapse into **stages of the existing Intent pipeline**, HL public info is one more intel file:

```
opening_bell (public /info)
    → models/hl_public_info.json
    → gatherer / shadow HL Intent (dispatchable=False, leverage=0)
    → parity harness
    → Valedia brain (Kelly/FTL) still owns size
Execution Trader role ≡ venue_writer == valedia|nautilus (today: none for HL)
```

---

## H5-ready

1. Port `opening_bell.py` ideas into `scripts/integrations/hl_public_info.py` (stdlib, optional network behind a flag default off in tests).  
2. Tag shadow Intents with `desk_stage=evidence|risk|review`.  
3. Do **not** spawn 7 agents. Do **not** touch `VENUE_WRITER` or `NAUTILUS_LIVE`.
