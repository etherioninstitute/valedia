# Design: event-contract orders through the Events account (paper first)

2026-11-13. Roadmap 4.7. Status: design, written by the lead after the observations in `docs/WEBULL.md`
("Event contracts"). Nothing here is built. Nothing here can send a real-money order.

## What is known (seen in Webull's test environment today)

- Webull's event contracts are Kalshi's markets under Kalshi's tickers. Prices, books, history and results come
  from Kalshi's public feed; Webull's API is used for orders and account reads only (roadmap 5.8).
- An event order is a limit order: ticker, `event_outcome` yes or no, whole contracts, a price in dollars with
  two decimals, time in force DAY, in the account of class `EVENTS_CASH`.
- The paper side fills at the real book's price. A holding is one row per ticker and outcome. Each order carries
  one exchange fee entry (0.01 dollars for one contract at 0.71).
- The balance answer has buying power, no cash line.
- Not known yet: what settlement looks like (one contract is being held to tonight's result to see), partial
  fills, the fee on larger orders, order-size caps, Webull's real fee schedule, the live side.

## Goal of this step

Prove the order path for event contracts in the paper Events account with the same guarantees stocks have:
an order is sent at most once, an unknown outcome blocks, every fill is journaled once, positions reconcile.
No strategy is wired to it: none has passed a test. The path is driven by one practice command.

## Parts

### 1. The adapter learns event orders (`venues/webull.py`)

- A position and an order are named `TICKER:yes` or `TICKER:no` (the ticker in Kalshi's shape, letters first).
  Units are whole contracts. This lets the order ledger, the reconcile and the journal work unchanged.
- With the account class `EVENTS_CASH` the adapter accepts only such names; with any other class it refuses
  them (as now). An order that does not fit the account's class is never sent.
- `submit` requires a limit price between 0.01 and 0.99 with at most two decimals, a whole number of contracts,
  session ignored, time in force DAY. A sale is sent only for contracts held of that ticker and outcome and not
  already in open sales (the long-only check, on holdings read at most 5 seconds earlier). Buying `no` is a
  purchase of the no contract, never a sale of yes.
- `positions()` in an Events account returns `{TICKER:outcome: contracts}` from rows of type EVENT; a row of
  another type, or an EVENT row in a non-Events account, goes to `other_positions()` as now.
- `cash()` in an Events account returns buying power as `cash` and says `settled_cash` is not given.
- `order_status`, `open_orders`, `cancel`, `cancel_all_open`: as for stocks, with the outcome read back from the
  order row; an order row whose outcome differs from what was sent is an error, not a fill.
- The venue's name carries the account class, so the Events ledger and the Cash ledger are separate files and
  can never be reconciled against each other's positions.
- Live: refused. The live confirmation file does not cover event orders, and this design adds no way to make it.

### 2. One practice command (`research_loop/cli.py`: `event-practice`)

`event-practice --ticker T --outcome yes|no --contracts N --side buy|sell [--limit P]`

- Test environment only, by the order step's whole-label host rule, checked before anything is read.
- Caps fixed in code: at most 5 contracts and 5 dollars an order, 10 orders a day.
- The price: Kalshi's public book read at most 20 seconds earlier. A purchase needs an ask, a sale a bid; the
  limit defaults to the ask plus one cent (the bid less one cent for a sale) and a given limit may not cross
  the book by more than 2 cents. No two-sided quote, or a spread above 10 cents: refused.
- The order goes through the order ledger exactly like the order step's: reconcile, `intend`,
  `submit_once(not_sent=(NotTransmitted, OrderRejected))`, reconcile, each fill journaled once with its fee.
- A baseline of the account's positions is recorded at the first run, as in the order step.

### 3. Settlement

**What was seen (2026-11-17, paper Events account).** A contract held to its result leaves the account with no
order and no fill. The hand-bought yes contract of `KXNBAGAME-26NOV13ATLCHI-ATL`, which lost, vanished at 0.
The reconcile then stopped, because it expected 1 and the account showed 0. Kalshi's public record:
`market_type` "binary", `status` "finalized", `result` "no", `settlement_value_dollars` "0.0000",
`settlement_ts` "2026-11-14T03:49:44.800032Z". A winning settlement has not been seen. Neither has a void, a
fraction, or a holding that vanishes before Kalshi says `finalized`.

**What is built (T45-1, `event_practice.py`, `KalshiData.market`).**

- *First witness: Kalshi's record.* For each holding the ledger expects (baseline or bought here) that the
  account shows at exactly 0, all of the following must hold:
  - Kalshi's record for exactly that ticker (`/markets/<ticker>`, or `/historical/markets/<ticker>` on a 404
    only) has, as plain strings, `ticker` the same, `market_type` "binary", `status` "finalized", `result` "yes"
    or "no", and `settlement_value_dollars` exactly "1.0000" for yes or "0.0000" for no.
  - Its `settlement_ts` is a well-formed UTC time in the past and later than every `sent_at` of the ledger's
    orders on that name that filled. A fill cannot precede its order; baseline holdings have no order.
  - No order of the ledger on the holding is open or of unknown outcome, and no fill on it is unapplied.
  - No other name differs from baseline plus fills.

  If any of this fails, nothing is settled and the run stops as before, naming the fact. The holdings gone in one
  run (K) are judged together; if one fails, none is settled.
- *Second witness: the account's cash.* Each run reads buying power once, only with no order of the ledger open,
  and stores it with the ledger's known cash in whole cents. That is, order by order, a purchase's price times its
  contracts plus its fees taken away, a sale's price times its contracts less its fees added, plus each
  settlement's payout. The ledger stores a sale's contracts as a negative number and the contracts its price
  covers as a positive one. An order whose filled contracts are not all priced, a fill with contracts and no fee,
  or a figure not in whole cents (beyond the ledger's float rounding, a millionth of a cent) makes the known cash
  unknown. The stop at `settlement` gives the figures: both readings, the known cash between them, the payouts
  expected and the difference in cents, or "no earlier clean reading". `prev` is the latest reading taken after a clean
  reconcile with both figures read; a reading taken while a holding is missing never counts. It need not have shown
  K: a holding bought after it is sound, because every fill between the two readings is inside the known cash. K is
  settled only if, exactly, `now - prev == (known_now - known_prev) + payouts(K)`.
- *Order of steps:*
  1. Journal the rows that are not yet journaled.
  2. Reconcile, cancel, reconcile.
  3. Find K and judge it against Kalshi's record.
  4. Take the reading `now`, before anything of K is written.
  5. If the equation holds, write the settlements and reconcile a third time with the new book; that reconcile
     must be clean, so the ledger's latest reconcile stays usable for the next order.
  6. If the equation does not hold, write one pending record per name and stop at stage `settlement` with the
     figures.
- *Pending.* While any record is pending, nothing is sent. A later run never settles a pending record by itself.
  Two person's commands close one, each with `--who NAME --note TEXT` (trimmed, not empty, no control characters).
  Each checks the environment first, as every run does. Then, under the lock, it refuses while anything of the
  ledger is open or not yet journaled (a `--reconcile-only` run comes first), and runs the same reconcile a
  `--reconcile-only` run makes. A refusal journals nothing, changes no practice state and says why; the record
  stays open.
  - `--confirm-settlement ID` needs the account to show the pending name at exactly 0, with nothing else different
    (other names pending with it, also at 0, aside: holdings gone together are confirmed one by one). It then reads
    Kalshi's record again and requires the first witness again. It journals the settlement with `witnesses`
    "kalshi_record+person", who and the note, and reconciles for a fresh reading. It is refused when the pending
    record's contracts differ from what the ledger now expects of that name (a fill arrived since).
  - `--withdraw-pending ID` is for a holding that is back in the account in full. It needs a clean reconcile and
    closes the record without a settlement, with a `settlement_withdrawn` line carrying who and the note. If the
    holding goes again later, a new pending record is made.
- *The entry.* A `settlement` journal entry, once per name: the whole expected holding closed at 1.00 a contract
  when its outcome won (yes holding: Kalshi's YES value; no holding: one minus it), else 0.00. It carries the
  baseline and program parts, the fields read, the two readings, `witnesses`, and `fee` null with `fee_seen`
  "none seen".
- *Writes.* Every settlement, pending record, withdrawal and reading is a row with an integer id. The journal line is keyed
  by that id and synced, then the row is marked journaled. A line whose write failed is written at the next run,
  exactly once.
- *After a settlement.* The expected book becomes baseline plus applied fills minus settled units. Any later
  order on a settled ticker is refused, and the sale check subtracts settled units, so the baseline is never
  sold.
- *What a loss is confirmed as.* A loss is confirmed by three facts: Kalshi's record says it lost, the account
  shows none of it, and the account's cash moved by exactly what the ledger knows, no more and no less. A
  transfer out, or a hand sale netting exactly zero after fees, would be recorded as a settlement with the money
  still right. The account's own order history is the third witness a later ticket adds. For a win the equation
  is real evidence. `win_cash_observed` turns true on the first win settled on it and is shown in every summary.
- *State file.* Layout 4 adds the `settlements`, `pending`, `readings` and `withdrawals` tables. A layout-1 file
  holding exactly its three tables is migrated in place, and its `meta`, `baseline` and `sends` are left byte for
  byte. Any other layout is refused. That includes a layout-1 file missing a table, the layout 2 of the first T45-1
  build (with its `holds` table) and the layout 3 of the second, neither ever deployed. The old hold and
  `--clear-hold` are gone.

**What Webull's help pages say (read by the lead 2026-11-17).** Every opening and every closing trade costs 0.01
dollars commission plus 0.01 dollars exchange fee a contract. Holding to settlement costs nothing, and neither
does a cancelled or unfilled order ("Event Contract Fees", help/faq/11174). The paper account's 0.01 a contract
is therefore not the real charge, and the code keeps journaling whatever fee the venue reports. In a real
account, winning proceeds go to the balance of the linked brokerage account and losing positions are simply
removed. Event buying power is derived from that linked account's settled cash (help/faq/11172 and 11052).

**Limits.**

- In a real account buying power also moves with the linked brokerage account, so there the equation will often
  not hold, and a person (or, later, the account's own order history) confirms. The exact equation is meaningful
  as built only in the paper Events account. A `win_cash_observed` set there does not prove the live cash path.
- A transfer out, or a hand sale netting exactly zero after fees, of a holding Kalshi says lost is recorded as a
  settlement, with the money still right.
- Any cash move from outside the ledger that exactly cancels a difference passes the equation.
- A correction by Kalshi after `finalized` is not handled and would show only as a cash difference.
- A holding Webull drops before Kalshi finalizes (for example while `determined`) stops the run. That blocks the
  whole Events path until Kalshi finalizes. A multi-market strategy will need a per-name waiting state for that
  next.
- Settlement entries are read by no page and no tax figure yet (`research_loop/owner_page.py`,
  `execution_and_risk/tax.py`).

## What is not in this step

A strategy; sizing; risk limits for event contracts (the owner's to set in `config/risk.json`); netting
several strategies; market or amount orders; anything live.

## Checks before it is accepted

Tests first. Then a blind verification by a fresh verifier, with attention to: the outcome never flips (a
`no` purchase never becomes a `yes` sale); a name of the wrong shape never reaches Webull; nothing is sent in a
non-Events account; the caps; the same secret-hygiene gates as the rest of the adapter; the stock path
byte-identical to before for the same inputs. Then the lead's run in the paper account: one resting order
cancelled, one purchase and sale of one contract, a second run that journals nothing new.
