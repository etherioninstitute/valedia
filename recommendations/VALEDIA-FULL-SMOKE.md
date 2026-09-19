# VALEDIA-FULL-SMOKE — minutes-scale whole-brain smoke (0.38.0)

**Updated:** 2026-09-19 PT
**Harness:** `python3 scripts/integrations/valedia_full_smoke.py`
**Version:** `0.38.0` tag `valedia-v0.38.0` tip `cdbec1b`
**JSON:** `models/valedia_full_smoke_report.json`
**Verdict:** **PASS**
**Wall:** **3.763s** / 600s
**Exit:** `0`

Hard fence held: no `POLYMARKET_US_LIVE`, no `VENUE_WRITER` changes, no `REBALANCER_AUTOEXEC`, no `NAUTILUS_LIVE`, no `VALEDIA_SELF_ADJUST`/`MIROFISH_AUTO_RAISE`, no treasury auto, no live orders, no ACH.

## Sections

| # | Section | Result | Reason |
|---|---------|--------|--------|
| 1 | fence (critical) | **PASS** | v0.38.0 writers held, live flags off |
| 2 | treasury (critical) | **PASS** | report-only; auto refused; no ACH |
| 3 | mirofish (critical) | **PASS** | fixture PASS; live-lite PASS |
| 4 | kalshi | **PASS** | GET `/portfolio/balance` → **$5.00** |
| 5 | poly_us | **PASS** | whoami_retail/balances + preview; create blocked |
| 6 | shadow_adversarial (critical) | **PASS** | queue intents=74 dispatchable=0; adversarial paper import ok |
| 7 | cockpit (critical) | **PASS** | `panel()` FENCED; GET `:8765` SKIP (connection refused) |
| 8 | pnl (critical) | **PASS** | broker summary present; label broker≠FTL; FTL n=0 |

Optional live SKIP is not a fail. This run had no optional FAIL; cockpit HTTP is SKIP because the GET server is not listening.

## Fence stamps

| Flag / stamp | Value |
|--------------|--------|
| `VALEDIA_VERSION` | `0.38.0` |
| `VENUE_WRITER` | kalshi/coinbase/alpaca=`valedia`; polymarket/hyperliquid/kraken=`none` |
| `POLYMARKET_US_LIVE` | False |
| `VALEDIA_TREASURY_AUTO` | False (auto refused even if flipped) |
| `REBALANCER_AUTOEXEC` | False |
| `NAUTILUS_LIVE` | False |
| `VALEDIA_SELF_ADJUST` | False |
| `MIROFISH_AUTO_RAISE` | False |
| `SPOT_RECONCILE_HTTP` | False |
| env live list | empty |

## Measured (this run)

**Treasury (GET, no moves):** Kalshi $5.00 **WARN** (floor $25); Poly US $30.55 **OK**; Coinbase $3.49 **WARN** (floor $20); Alpaca $0.31 **WARN** (floor $10); HL/Kraken **DOCUMENT**. `moves_money=false`, auto `refused=true`. Floor WARN is not a smoke fail.

**MiroFish:** fixture PASS; live-lite PASS (0.455s inner). Production accuracy ledger not appended.

**Poly US:** `whoami` source=`balances` (not institutional `/v1/whoami`). Preview `kind=preview` `filled=false` qty=1 @ 0.01 on `aec-nfl-lac-ten-2025-11-02`. Create stayed blocked (`POLYMARKET_US_LIVE` off).

**Shadow:** 74 intents, 0 dispatchable. Adversarial `build_round` PAPER/SHADOW, `no_orders=true`.

**P&L:** broker (pnl_tracker.json, **not FTL**): 10W / 28L, net **-$12.62**, $48.79 deployed / $5.00 cash. FTL `n=0` `net=0` (fee-true ledger still empty — do not treat broker net as FTL).

## Tests

```bash
python3 scripts/integrations/test_valedia_full_smoke.py   # offline PASS
python3 scripts/integrations/valedia_full_smoke.py        # this report; ~minutes, 3.8s here
python3 scripts/integrations/valedia_full_smoke.py --offline
```

`test_valedia_full_smoke.py` PASS. Also re-ran version-bumped: slice23, h3, h4, treasury, mirofish_smoke, poly_us, h2, h5–h8, nt1/nt2, poly_e, adversarial. No crontab. No production ledger writes. No live flips.

## Not run

- live orders / Kalshi writes / Poly create (blocked)
- ACH / wire / card / treasury auto top-up
- kill-switch (`VALEDIA_TRADING_HALT`) — shipped 0.39.0, see `KILL-SWITCH.md` (default off)
- OpenTerminal Valedia UI
- cockpit HTTP server (`valedia_cockpit.py --listen`)

**Next = OPENTERMINAL-VALEDIA.** Do not auto-start. Do not flip live.
