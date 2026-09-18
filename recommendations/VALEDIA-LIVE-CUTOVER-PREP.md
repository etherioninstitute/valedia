# NT / Hyperliquid / Polymarket live-cutover prep (Slice 19)

**This lane does not enable anything.** No `pip install nautilus_trader`. No `NAUTILUS_LIVE`. No `VENUE_WRITER` flip. No HL `/exchange` POST. No Polymarket CLOB POST. No wallet/spend. STOP for Burton after reading.

Order (already in `VENUE_NT_CUTOVER_ORDER`): **coinbase → hyperliquid → polymarket → kalshi**. Kalshi stays Valedia writer. Kraken read-only. Mercury never. Alpaca stays Valedia until an adapter.

## Current fence (must still be true)

| Control | Now | Cutover would need (Burton only) |
|---------|-----|----------------------------------|
| `NAUTILUS_ENABLED` | False | True **and** writer=nautilus on that venue |
| `NAUTILUS_LIVE` | False | True before `source=live` NT fills |
| `VENUE_WRITER["coinbase"]` | `valedia` | `nautilus` **last**, after parity |
| `VENUE_WRITER["hyperliquid"]` | `none` | `nautilus` (never `valedia` — no HL writer today) |
| `VENUE_WRITER["polymarket"]` | `none` | `nautilus` |
| `LIVE_CLIENT_HOOK` HL/PM | `None` | NT adapter object (not archived Info client) |
| Leverage | 0 | stays 0 until Burton |
| `SPOT_RECONCILE_HTTP` | False | Coinbase fill-GET **before** writer cut (Slice 17) |
| `REBALANCER_AUTOEXEC` | False | unrelated; leave False |
| `nautilus_trader` wheel | not this lane | isolated venv only, no keys in that venv |

`nautilus_may_dispatch(venue)` is False unless ENABLED **and** writer==nautilus. Dual-write is −EV.

## Shadow → live gaps

### Coinbase (first, later)

- Live writer today: `unified_brain` + `coinbase_trader.market_buy/sell`.
- Shadow Intents exist (`NAUTILUS_SHADOW_COINBASE=True`), never-dispatchable.
- **Gap:** 7 calendar days of `shadow_parity_report.json` `cutover_ready.coinbase=true` with **no** dangerous diffs (side/instrument/size/signed/hook/dispatchable/live_flag).
- **Gap:** spot GET still mock. Burton may flip `SPOT_RECONCILE_HTTP` (Slice 17) and prove UNRECONCILED upgrades **before** any writer cut.
- Allocator Coinbase is **not** evidence (dead execute branch; do not implement a second live writer).

### Hyperliquid (second)

- No Valedia writer. Archived client is Info-only.
- Shadow shorts: Ioc-limit at `bound_px`, `LIVE_CLIENT_HOOK=None`, leverage 0, reduce-only flag on the Intent.
- **Gap:** NT Hyperliquid adapter + keys + signing. Do **not** un-archive the old client as a writer.
- **Gap:** `cutover_ready.hyperliquid` needs 7d matching shadow vs stub `/exchange` body.
- **Gap:** no live account/policy in this lane. Leverage stays 0.

### Polymarket (third)

- Scan/arb shadow only. CLOB FOK body is a stub. `LIVE_CLIENT_HOOK=None`.
- **Gap:** wallet + CLOB credentials + NT CLOB adapter. Kalshi leg of any arb stays Valedia.
- **Gap:** `cutover_ready.polymarket` 7d matching evidence.
- **Gap:** no `ENABLED` live Polymarket exec today.

### Shared

- Live `models/nautilus_intent_queue.jsonl` may be empty until brain/intel/HL/PM shadows have run on live (Slice 15 just deployed the emitters).
- Cron `parity_run.py` is **not** in the live crontab until Burton pastes the Slice 16 line.
- `cutover_ready` is **report-only**. It does not flip flags.
- Fill sink default-denies `source=live` unless `nautilus_live_fills_allowed`.

## Burton go / no-go (in order)

1. Watch `models/shadow_parity_report.json` for ≥7d `ok` + `cutover_ready.coinbase` with empty `dangerous`.
2. Optional: Slice 17 GET HTTP one-shot, then cron. Revert if upgrades look wrong.
3. Isolated NT venv (engineering). No keys. No `VENUE_WRITER` change.
4. Only then: `NAUTILUS_ENABLED`, `NAUTILUS_LIVE`, **one** venue writer to `nautilus`. Never two writers.
5. HL/PM only after Coinbase is stable **and** their own 7d ready bits.
6. This coding lane never performs steps 2–5.

## Verify (read-only)

```
python3 -c "import valedia_config as vc; print(vc.NAUTILUS_ENABLED, vc.NAUTILUS_LIVE, vc.VENUE_WRITER, vc.spot_reconcile_http_enabled(), vc.rebalancer_autoexec_enabled())"
```

Expect: `False False {...coinbase: valedia, hyperliquid: none, polymarket: none...} False False`
