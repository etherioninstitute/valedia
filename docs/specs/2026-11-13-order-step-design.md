# The order step: from the strategies' decisions to orders at the broker

2026-11-13. Roadmap 4.8 (the missing piece), with 1.15 (netting) and parts of 5.7. Test environment only.

## Why

Today `paper-step --webull` hands each strategy's order straight to the broker adapter at the moment it is
decided, after the close. That cannot work and must not go live:

- Webull refuses an ordinary order outside regular hours (observed 2026-11-13), and the paper step runs after
  the close.
- It bypasses the order ledger, so nothing guarantees an order is sent once, and nothing reconciles.
- Two strategies trading the same fund send two orders, one of which may be a sale that the other's purchase
  turns into a wash sale (1.15).
- The paper book fills by simulation; nothing records what the broker really filled.

## What it is

One command, `order-step`, run once per trading day inside the regular session. It does not decide anything.
It reads what the paper book wants to hold, compares that with what the broker account holds because of this
program, and sends one order per symbol for the difference, through the order ledger.

Three books are kept apart:

1. **The paper book** (`PaperTrader`): simulated, unchanged, the research record.
2. **The broker book**: units per symbol that this program owns at the broker. It is not stored: it is the sum
   of the fills in the order ledger. Nothing else can change it.
3. **The baseline**: what the account held before the first order step (the owner's own holdings). Recorded
   once, never sold, never counted as owned.

## One run, in order

Any failure in steps 1 to 6 means no order is sent in this run; the reason is journaled and printed.

1. **Environment.** The venue must be the test environment. Anything else is refused before any venue call.
   (Live comes later, with approval by ticket; see "Not in this build".)
2. **Session.** Refuse outside the regular session of a trading day (default window 09:35 to 15:45 New York
   time, from the exchange calendar; half-days end earlier).
3. **Baseline.** If no baseline is recorded and the ledger has no orders, record the account's positions as
   the baseline. If no baseline is recorded and the ledger has orders, stop.
4. **Reconcile.** `ledger.reconcile(venue, baseline + broker book)`. Then cancel every order of this ledger
   that is still open at the broker, and reconcile again. Journal every new fill once (by fill id) and mark it
   applied. If `ledger.blocked()` still gives a reason, stop.
5. **Targets.** For each symbol: paper units wanted (each strategy's holding plus its pending orders, summed)
   times `capital / paper equity`. `capital` is a setting; in the test environment it defaults to the paper
   book's equity, so the scale is 1.
6. **Prices.** A quote for every symbol with a difference, from an injected quote function, with its time.
   A quote older than 20 minutes, missing, or not a positive finite number: no order for that symbol.
7. **Differences.** `delta = target - broker book`. Rounded toward zero to whole shares (fractional units are
   an option, off by default until the test environment has shown how Webull takes them). A sale never
   exceeds the broker book's units. A difference worth less than the paper trader's smallest trade is dropped.
8. **Risk gate, again.** Each order passes `risk_gate.check_order` against the broker account as it is now:
   equity (cash plus holdings at the quotes), the day's loss against the equity recorded at the last run, the
   fall from the recorded peak, positions held. The latch and halt files are honoured.
9. **Order of sending and caps.** Sales first, then purchases, largest value first. Purchases stop when cash
   (as the venue reports it, less 1%) would be exceeded. No more than `max_orders_per_day` orders are sent in
   one day across runs (from the risk file if present, else 40); what is left over is journaled, not queued.
10. **Send.** Each order is a day limit order: purchases at the quote plus 1%, sales at the quote less 1%
    (less 2% when the sale reduces risk), rounded to the cent on the cautious side. `ledger.intend` with the
    strategies' parts, then `ledger.submit_once` with `not_sent=(NotTransmitted, OrderRejected)`. A refusal is
    journaled and the run goes on; an unknown outcome stops the run (the ledger blocks).
11. **After.** Reconcile once more, journal fills, write a summary: orders sent, refused, filled so far,
    broker equity, paper equity, and the difference between the broker book and the targets.

A second run on the same day is safe: it reconciles, cancels what is open, and sends only what is still
missing.

## What goes where

- `execution_and_risk/order_step.py` (new): the run above as one function with everything injected (ledger,
  venue, paper targets, quote function, clock, calendar, journal, limits), plus small pure functions for the
  broker book, targets, differences and limit prices.
- `research_loop/paper.py`: one read-only method giving, per strategy and symbol, the units held plus pending.
- `research_loop/cli.py`: the `order-step` command; `paper-step --webull` no longer sends anything.
- State kept by the order step (baseline, equity at the last run, peak, orders sent per day): one small
  sqlite file beside the order ledger, one per venue and environment.

## Failure handling

- The venue raises during a read: stop before sending.
- An unknown outcome at sending: the ledger blocks; the next run's reconcile settles it or a person does.
- Positions at the broker differ from baseline plus broker book: the reconcile is not clean, nothing is sent,
  and it stays that way until a person has looked (someone traded in the account by hand, or a fill was missed).
- A crash at any point: the next run starts from the ledger; no order can be sent twice.

## Not in this build

- Live trading. It needs: approval by ticket number (the plan is shown, the owner approves, the price is
  checked again against the plan and the request expires), the owner's capital figure, and everything the
  roadmap lists under 4.2, 4.9 and 5.4.
- Settlement rules of a cash account, day-trade counts (3.5); tax lots from broker fills (3.3, 3.8).
- Crypto and event contracts: stocks and funds only.
- A schedule. The owner starts it by hand until it has run clean for a week.

## Tests

Stand-in venue, ledger on a temporary file, fake clock and quotes. Among them: two strategies with opposite
orders in one fund give one net order or none; a partial fill today is completed tomorrow; baseline units are
never sold; a stale quote sends nothing for that symbol; the day's cap; cash running out; outside the session;
production refused before any call; a crash between `intend` and `submit_once`, and between a fill and its
journal entry, loses and doubles nothing; an unclean reconcile sends nothing; a refusal does not stop the run
and an unknown outcome does.
