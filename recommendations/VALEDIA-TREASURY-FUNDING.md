# Valedia treasury / multi-venue funding (TREASURY-1)

**North star:** max profit under gates. Burton does the **first fill** on each venue. After that, **compound in place**. Floors alert. Optional auto top-up is a later master flip (`VALEDIA_TREASURY_AUTO`, default **False**). This slice **does not fund anything**.

**Fence:** code/audit only. No ACH/wire/card charges from Valedia. No wallet transfers. No venue withdrawals. No `--live` spend. Do not flip `REBALANCER_AUTOEXEC`, `POLYMARKET_US_LIVE`, `VENUE_WRITER`, `SPOT_RECONCILE_HTTP`, `NAUTILUS_LIVE`, `VALEDIA_SELF_ADJUST`, `MIROFISH_AUTO_RAISE`.

**First human fund is still required.** A flag cannot deposit.

## How to think about it

Each venue is its own cash pile. Winning trades leave cash **on that venue**. Moving money between venues costs time and fees (ACH 1–4 business days, wires, debit holds). That is usually −EV versus leaving the winner where it earned.

`scripts/integrations/treasury_status.py` is **report-only**. It reads cash where a helper already exists, compares to floors, writes `models/treasury_status.json`, and prints WARN strings. It never moves money. `REBALANCER_AUTOEXEC` stays False (Coinbase allocation shadows only — not a treasury pump).

## Initial fill (Burton, by hand)

Do this in the venue’s own app/web. Not via this repo. Not via API.

| Venue | Writer now | Suggested first fill | Floor (WARN below) | How you fund |
|-------|------------|----------------------|--------------------|--------------|
| **Polymarket US** | `none` (US_LIVE False) | enough to sit **above $25** (account already ~$30.55) | **$25** | App: debit / ACH / wire — [docs.polymarket.us fund account](https://docs.polymarket.us/learn/get-started/fund-account) |
| **Kalshi** | `valedia` | enough to sit **above $25** (stub until you pick a real number) | **$25 stub** | Kalshi deposit in-app. Status uses `kalshi_auth.get_balance` (cents → USD) when you run with `live_read` |
| **Coinbase** | `valedia` | cash above **$20** so a trade can clear fees | **$20** | Bank → Coinbase (you click). Hook: `coinbase_trader.get_balances` USD |
| **Alpaca** | `valedia` | cash above **$10** | **$10** | Bank → Alpaca (you click). Hook: `alpaca_executor.get_account` `cash` / `buying_power` |
| **Hyperliquid** | `none` | **do not fund this slice** | none | No writer. Status = DOCUMENT. No cash read. |
| **Kraken** | `none` | **do not fund this slice** | none | Pending / no writer. Status = DOCUMENT. No cash read. |

Treasury bank for a **later** optional auto top-up is Mercury GOATWORTHY checking. That path is **not built**. You still initiate ACH yourself.

### Polymarket US deposit methods (pointer only)

From [Fund Your Account](https://docs.polymarket.us/learn/get-started/fund-account):

- **Debit card** — daily limit $50,000; ~3–4 business days to fully clear; small deposits
- **Bank transfer (ACH)** — daily limit $50,000; ~3–4 business days; medium deposits
- **Wire transfer** — $1,000 minimum, no max; ~1 business day; large deposits

Debit/ACH may show **instant buying power** while the deposit is processing. Withdrawals wait until the deposit has fully cleared. Valedia does **not** call Aeropay/Checkout deposit APIs.

## Compound in place

After the first fill:

1. Leave settled cash on the venue that earned it.
2. Let floors WARN if a venue is too thin to take the next sized trade.
3. You top up **that** venue from the bank if you want more size.
4. Do **not** withdraw venue A to fund venue B unless the fee + delay is obviously worth it (it almost never is at this scale).

Rebalancer is **not** a cross-venue funder. It only proposes Coinbase target-weight shadows. Leave `REBALANCER_AUTOEXEC=False`.

## Cash floors and alerts

Defaults in `valedia_config.py` (`TREASURY_FLOOR_*` / `TREASURY_FLOORS`):

- Polymarket US **25**
- Kalshi **25** (stub)
- Coinbase **20**
- Alpaca **10**
- HL / Kraken **None** (no alert)

`python3 scripts/integrations/treasury_status.py` (optional live GET, fail-closed) writes `models/treasury_status.json`. Below floor → `status=WARN` and an alert string. Unreadable → `UNREAD` (no fake WARN). HL/Kraken → `DOCUMENT`.

`TREASURY_ALERT_WEBHOOK` exists as an unused stub. Nothing is POSTed. Signal/human reads the JSON or stdout.

## When NOT to move money across venues

- Any ACH/wire/debit that takes days while the edge is hours.
- Withdraw-then-deposit just to “rebalance” cash (fees + FIFO/source rules on Poly US).
- Funding HL or Kraken while writer is `none`.
- Using the rebalancer AUTOEXEC path as a treasury (it is Coinbase trades, still gated off).
- Anything that needs `VALEDIA_TREASURY_AUTO=True` — **leave it False**.

## Optional later auto top-up (not this slice)

Name: **`VALEDIA_TREASURY_AUTO`** default **False**.

Later, Burton-only: one treasury bank (Mercury) could top up a venue that is below floor. That still needs:

1. You flip the flag on **runtime**, not in this coding lane.
2. Real transfer code that does not exist here (`attempt_auto_topup` **raises** even if the flag is on).
3. The first human fund on that venue already done.

Until then: floors alert, you fund.

## What this slice does not do

- Charge a card, start ACH, send a wire, or touch a wallet
- Withdraw from any venue
- Flip live writers or `POLYMARKET_US_LIVE`
- Edit trading API keys in git
- Put Mercury routing/account numbers in this file
- Cron-install `treasury_status.py`
