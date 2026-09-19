# H1 dossier — kalshi-agent

**Path:** `/Users/burtonchatelain/MemPalace/sources/repos/github/kalshi-agent`  
**License:** **UNLICENSED** (`package.json` `"license": "UNLICENSED"`, private Electron app). No `LICENSE` file.  
**Burton override (2026-09-18):** personal-use **rewrite OK**. Prefer attributed rewrite into Valedia modules. Still no reckless paste of secrets.  
**Slice:** H3 — fill-true / scan / settle ideas. This H1 does not code it.

---

## Identity

“Kalshi Agent v7 — hardened trading workstation” for Kalshi + Polymarket: scan loop, bull/bear debate (Claude), Kelly+fees, Decimal money, calibration (Brier), execution policy, Electron dashboard. **`dry_run` is always true from config; only CLI `--live` enables orders.** Credentials via `kalshi-config.json` (gitignored) + `SECRET_FIELDS`.

Already leaked into Valedia: `kelly_sizing.py` comments that Bayesian shrink is adapted from `scoring.bayesian_kelly_prob`. Do not import a second copy.

---

## Architecture

```
kalshi-agent.py  (scan, debate, execute)
modules/
  config.py        DEFAULTS → file → env; --live is the only live key
  precision.py     Decimal cents; VenueFees
  scoring.py       category Kelly caps, Thorp f/√N, Bayesian p, debate-spread mult, dynamic_min_edge
  calibration.py   Brier / log loss / reliability bins
  execution.py     taker vs maker vs no_trade from book quality
  market_state.py  BookState + staleness
  apis.py          Kalshi REST + PM CLOB
  arbitrage.py     cross-platform arb
  debate.py        Claude Sonnet bull vs bear
```

---

## STEAL (rewrite into *our* Kalshi path)

Ranked by realized-P&L (charter: Kalshi POST-ack=fill is still a leak):

1. **`--live` as the only live escalator** — their README says config cannot override `dry_run`. **Hole:** `KALSHI_DRY_RUN=false` in env still enables live (`modules/config.py` `load_config`). Do **not** copy that bypass. Valedia: POST-ack is not a fill.  
2. **`precision.py` Decimal / cents** — money types only. **Leave their fee table.** `VenueFees` hardcodes flat `$0.07/contract`; real Kalshi is nonlinear `ceil(0.07·C·P·(1−P))` — already canonical in `fee_engine`. Copying their Kelly/fee would reject cheap contracts and overcharge extremes.  
3. **`execution.py` book quality → maker/taker/no_trade** — Valedia `execution_policy.py` already does this for crypto; **port the Kalshi-tick version** so sports doesn’t market-cross a stale book.  
4. **`calibration.py` Brier vs market price** — category reliability for sports/weather/fed. Feed **FTL settlements**, not debate vibes.  
5. **`dynamic_min_edge(price, fee)`** — fee-drag floor: `2 * fee / price + base`. Sports currently sizes off an edge score; this is the missing fee gate twin.  
6. **Stale `BookState` → not tradeable** — `market_state.py` pattern.  
7. **SECRET_FIELDS allowlist** — never log keys (already a Valedia red line).  
8. **Category Kelly *caps*** as **comments into existing `KellySizer` / category gate** — weather 0.25 vs sports 0.12 is *advisory*. Do not instantiate `scoring.kelly`. Weather live path stays dead (`LIVE_EXEC_FENCED`).
9. **ESPN public scores** (`data_fetcher._fetch_espn_scores`) as sports *intel* (status/final/odds), not a second sizer. `injury_checker.py` already uses ESPN JSON.
10. **Events API + skip `KXMVE`/`MULTIGAME`/`CROSSCATEGORY`** — `kalshi_scanner` already `mve_filter=exclude`; still worth event-first merge so parlays don’t drown singles.
11. **Maker `check_fills` remaining_count delta** — right idea; their `KalshiAPI` **has no `get_order`**. Valedia `kalshi_auth.get_fills()` / `get_orders()` already exist — wire poll, don’t paste `apis.py`.

**Already stolen (do not redo):** `execution_policy.py`, `market_state.py`, `kelly_sizing.bayesian_shrink_prob`, `extract_kalshi_fill` / `book_kalshi_fill`, FTL net-loss category gate, nonlinear `fee_engine`.

---

## LEAVE

| Leave | Why |
|-------|-----|
| Electron/React dashboard as OMS | OpenTerminal is the cockpit (H4); don’t ship a second UI that can `--live` |
| `debate.py` + Anthropic key | Cloud spend; MiroFish is the local swarm |
| Whole `kalshi-agent.py` orchestrator | Second cron, second Kelly, second fill logger |
| `scoring.py` Kelly as live sizer | Canonical is `kelly_sizing.KellySizer` |
| Polymarket CLOB live client | Writer `none`; shadow only (Slice 11) |
| `kalshi-config.json` / private keys | Never copy into workspace |
| UNLICENSED file paste | Rewrite; don’t vendor the tree |

---

## Valedia touchpoints

| Valedia | What to improve in H3 |
|---------|------------------------|
| `sports_engine.py` / `sports_run.py` | Fill-true (get fill, not POST-ack); fee via `ftl.kalshi_fee`; unproven throttle |
| `master_allocator.py` / `kalshi_executor.py` | Same fill-true; executor currently inert vs allocator live |
| `fee_engine.py` | Single Kalshi nonlinear table |
| `forward_test_ledger.py` | Settlements score calibration |
| `execution_policy.py` | Maker path already records on get_fills — **copy that habit to Kalshi taker** |
| `kelly_sizing.py` | Already has Bayesian + Thorp; don’t fork |

---

## Risks

- **UNLICENSED:** personal-use rewrite only; no publish, no npm, no “Valedia Agent v7” rebrand of their Electron app.  
- **Secrets in clone:** `kalshi-config.json` must not be committed; scan before any copy.  
- **`--live` in their CLI** — never run it from Grok Build.  
- **Second Kelly** if someone “just imports scoring.py”.  
- Weather category cap in *their* table must not re-arm Slice 8 `create_order`.

---

## Seamless hook

Kalshi stays **one writer (Valedia)** with **one fee table, one Kelly, one FTL**. kalshi-agent is a bag of *hardening tricks* poured into that writer:

```
scan (our scanner/sports)
  → edge net of Kalshi fee (dynamic_min_edge + fee_engine)
  → KellySizer (existing)
  → execution_policy (maker/taker/no_trade from book)
  → POST
  → fill GET / settle → FTL   ← the hole to close
calibration (Brier) reads FTL, never debate scores
```

---

## H3-ready

Highest EV: **fill-true Kalshi** (sports + allocator) using their dry_run/live split + Decimal + stale-book no_trade. Second: Brier calibration file next to FTL. Not in H3: Electron, debate, PM live, weather live.
