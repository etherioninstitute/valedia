# H1 dossier — nautilus_trader (gaps vs subsume charter)

**Path:** `/Users/burtonchatelain/MemPalace/sources/repos/github/nautilus_trader`  
**License:** **LGPL-3.0-only**  
**Version:** `version.json` = **v2.0.0rc5**; workspace `Cargo.toml` ~0.64.0  
**Charter:** `recommendations/VALEDIA-SUBSUME-NAUTILUS.md` (already). This dossier is **gaps only**. Do not redo NT-0/1/2 (those are neural-trader, not Nautilus).

---

## Identity

Rust-native event-driven OMS: research, sim, and live share one architecture; Python control plane. Official adapters include **Coinbase, Hyperliquid, Polymarket, Kraken**. **No Kalshi adapter.** Betfair exists (betting), not Kalshi.

Valedia already built the **Intent seam** without importing the package: `nautilus_intent.py`, exclusive `VENUE_WRITER`, `nautilus_fill_sink.py` (default deny `source=live`), shadow Coinbase/HL/PM, parity harness. `NAUTILUS_ENABLED=False`, `NAUTILUS_LIVE=False`. **No `pip install nautilus_trader` in this tree.**

---

## Architecture (what we’d actually use)

```
Valedia 7 gates → Intent (max_qty never rounds up)
                      │
                      ▼
              NT LiveNode sidecar   ← not started
                      │  OrderFilled / OrderAccepted / Reject
                      ▼
              nautilus_fill_sink → FTL  (live_source_allowed fail-closed)
```

Steal-grade internals (concepts, not a Strategy rewrite):

- **Event bus** — `OrderFilled` with `info` metadata (v2); activation_price; trade ids  
- **FillModel / FeeModel / LatencyModel** — shadow parity should compare against *fill events*, not POST bodies only  
- **Child orders** — OCO/OUO/OTO, GTD, post-only, reduce-only (HL shadow already sketches IOC)  
- **RiskEngine** — **stricter-or-equal floor**, never a second brain (charter)

Official adapters of interest: Coinbase (cutover #1; adapter also has **CFM perps** — Valedia first cutover remains **spot**), Hyperliquid (shorts/futures; builder attribution exists — opt out), Polymarket (CLOB + Relayer — Relayer is custody, leave). **Kraken listed Data/Execution but crate README still WIP** — keep writer `none`. Kalshi last / never until a custom adapter (`examples/live/_template` is the later clean-room pattern). Alpaca: absent — stays Valedia. Mercury: never. Betfair ≠ Kalshi.

---

## STEAL (H6 deepen shadow — still no LiveNode)

1. **Event-sourced order states** — `initialized → submitted → accepted → partially_filled* → filled | denied | rejected | canceled | expired | voided`, plus `inflight_unknown`. **`submitted` is not a fill; “not open” is not terminal.**
2. **`CommandFailure`:** `NotSent` (never transmitted) vs `VenueRejected` vs **`Ambiguous`** (unknown live outcome — leave inflight, **never invent a terminal event**, recon by `client_order_id`). Highest H6 value vs POST-body-only parity.
3. **Fill-event schema** — qty, px, fee, `trade_id` dedup, ts, liquidity; inferred fills **labeled non-live**.
4. **Child-order vocab on Intent (optional, default off)** — `post_only`, `reduce_only`, `expire_time` (GTD is already a TIF without expiry), contingency OCO/OTO; children sum ≤ parent `max_notional`.
5. **Book/WS *shape*** as fixtures for latency_arb tests (no sockets). NT adapters have no SOCKS story — Coinbase sidecar later needs VPS or proven proxy.
6. **RiskEngine as deny-only floor** from ERE JSON — never `calculate_fixed_risk_position_size`.

---

## LEAVE

| Leave | Why |
|-------|-----|
| `pip install` / running `LiveNode` | `NAUTILUS_LIVE` fence; LGPL wraps the process |
| NT `Strategy.on_bar` that computes size | UGFT/ERE/Kelly stay Valedia |
| Wrapping kernel/ERE inside NT | Copyleft + second brain |
| Enabling Coinbase/HL/PM adapters | Writer flip is a Burton go |
| Kalshi-via-Betfair hacks | Kalshi is the live money path; don’t break it |
| Redis persistence / Docker deploy | Ops sprawl before a sidecar exists |

---

## Valedia touchpoints

| Module | Status |
|--------|--------|
| `nautilus_intent.py` | Phase 0 seam; enqueue only |
| `valedia_config.VENUE_WRITER` | all valedia or none |
| `nautilus_fill_sink.py` | live deny unless flags **and** writer=nautilus |
| `nautilus_shadow_coinbase.py` / `_venues.py` / `_parity.py` | Slice 10–12 |
| `forward_test_ledger.py` | paper/sandbox/UNRECONCILED excluded from Kelly |
| Kalshi sports/allocator | **Keep Valedia writer** until a real adapter + Burton cutover |

---

## Risks

- **LGPL-3.0:** linking a sidecar is the intended architecture (process boundary). In-process import of NT into `unified_brain` is how we accidentally copyleft the firm. Stay sidecar.  
- **Dual-write −EV** if `VENUE_WRITER` is flipped while Valedia cron still POSTs.  
- **No Kalshi adapter** — cutting Kalshi to NT today is a money-printer outage.  
- **v2.0.0rc5** — migration notes (`MIGRATION_V2.md`) will bite if we pin late. Pin exactly when we install, not now.  
- **Trademark** — don’t name a Valedia strategy “Nautilus.”

---

## Seamless hook

Nautilus is the **how-the-order-lives-on-the-wire** chamber of the same nautilus shell — not a second mollusc:

```
Brain decides WHETHER / WHAT / HOW MUCH
Intent is the contract
NT (later) is the only writer on assigned venues
Fills return to ONE FTL
```

Shadow parity is how the brain *practices* that contract. `NAUTILUS_LIVE` stays False until Burton.

---

## H6-ready

Deepen `nautilus_shadow_parity.py` toward **event** diffs (accepted/filled/canceled/fee), still mockable 7d window, still fail-closed on cutover-dangerous mismatches. Optional: document `OrderFilled` field map next to `extract_spot_fill`. **No pip, no LiveNode, no writer flip.**
