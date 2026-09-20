# KALSHI_FEE_MAP_GATE — Burton flip checklist

**Slice:** AUTONOMY-1 (2026-09-19 PT). **Do not commit `KALSHI_FEE_MAP_GATE = True`.** This lane leaves the constant **False**. Env flip is live-runtime only.

**This slice already ran GET.** Artifact: `recommendations/kalshi-fee-map.json`  
`as_of=2026-09-20T06:13:49Z` `mode=get`  
**Targets:** n=79  **maker_viable=57**  **taker_taxed=0**  **unknown=5**  
**Sports (NBA/NHL/NCAAMB/MLB):** `sports_unknown=0` `sports_taker_taxed=0` `sports_maker_viable=0`  
Sports games are `quadratic_with_maker_fees` (NBA/NHL/NCAAMB M=1, MLB M=0.5). Maker is **not** free. Gate does **not** drop them (drop = unknown OR taker_multiplier>1 only).

Unknown after GET (HTTPError — retired/renamed tickers). Gate-on **will drop** these five:

- `KXFEDRATE` `KXPCE` `KXNONFARM` `KXRECESSION` `KXSHUTDOWN`

---

## 0. Preconditions (all must be true)

- [ ] `valedia_config.KALSHI_FEE_MAP_GATE` is still `False` in git
- [ ] `VALEDIA_TRADING_HALT` is whatever you want (halt is independent; gate is not a halt bypass)
- [ ] `POLYMARKET_US_LIVE` unset/false
- [ ] `WEATHER_LIVE` False; oil ticker fence still on (`KXWTI`/`KXOIL`)
- [ ] Coinbase/Alpaca writers still `none`
- [ ] Latest GET artifact `sports_unknown=0` (re-run step 2 if stale)

## 1. Dry (no network)

```bash
cd ~/.openclaw/workspace-v2
python3 scripts/integrations/kalshi_fee_map.py --dry --out /tmp/kalshi-fee-map-dry.json
```

Expect `mode=dry`, `gate_constant=false`, sports still unknown. Does **not** flip the gate.

## 2. GET refresh (read-only)

```bash
python3 scripts/integrations/kalshi_fee_map.py --get
# writes recommendations/kalshi-fee-map.json
```

Pass bar:

- `mode=get`
- `sports_unknown=0`
- `kalshi_fee_map_gate_constant: false`
- no `create_order` / no wallet / no halt change

Re-run this after Kalshi publishes a fee PDF or sports multipliers look wrong.

## 3. Read the numbers

```bash
python3 -c "import json; d=json.load(open('recommendations/kalshi-fee-map.json')); print(d['as_of'], d['counts'])"
```

Decide:

- **Flip now** if you accept dropping the five unknown econ tickers (fail-closed).
- **Wait** if you still need `KXFEDRATE`/`KXPCE`/`KXNONFARM`/`KXRECESSION`/`KXSHUTDOWN` live — fix tickers, `--get` again, then flip.

Sports maker fees (M=1) are **not** a reason to hold the gate. `taker_taxed` is.

## 4. Flip (LIVE runtime only — not this repo constant)

On the **live** tree (`~/.openclaw/workspace`), not Build:

```bash
# persist for cron/other processes
echo 'KALSHI_FEE_MAP_GATE=1' >> ~/.openclaw/.env
# this shell:
export KALSHI_FEE_MAP_GATE=1
```

Truthy: `1` / `true` / `yes` / `on`.  
Do **not** edit `KALSHI_FEE_MAP_GATE = False` in `valedia_config.py`.

## 5. Confirm effect

Gate **on**: allocator/executor/sports **drop** `status!=ok` and `taker_taxed`. Consult still annotates `fee_map`.  
Gate **off** (today): annotate only, never drop.

Smoke (no new strategy): one sports cycle + one allocator cycle. Unknown five should skip. Sports games should still size (not taxed, not unknown).

## 6. Rollback

```bash
unset KALSHI_FEE_MAP_GATE
# or:
export KALSHI_FEE_MAP_GATE=0
# remove the .env line if you added it
```

Halt is unchanged: `python3 scripts/integrations/valedia_trading_halt.py on|off|status`

## 7. Still fenced after flip

No Poly live, no CB/Alpaca writers, no weather/oil unfence, no wallet, no `VALEDIA_TRADING_HALT` bypass, no new live strategies.

**Next after flip:** AUTONOMY-2 MIROFISH-JEV-SHADOW (or hold if the five unknown econ series still matter).
