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

A contract held to the result leaves the account without an order. Until that has been seen, the reconcile
treats a vanished event holding as not clean and blocks: a person looks. After tonight's observation this
section is rewritten with what Webull shows, and settlement becomes a journaled closing entry at 1 or 0
dollars, checked against Kalshi's public result for the ticker.

## What is not in this step

A strategy; sizing; risk limits for event contracts (the owner's to set in `config/risk.json`); netting
several strategies; market or amount orders; anything live.

## Checks before it is accepted

Tests first. Then a blind verification by a fresh verifier, with attention to: the outcome never flips (a
`no` purchase never becomes a `yes` sale); a name of the wrong shape never reaches Webull; nothing is sent in a
non-Events account; the caps; the same secret-hygiene gates as the rest of the adapter; the stock path
byte-identical to before for the same inputs. Then the lead's run in the paper account: one resting order
cancelled, one purchase and sale of one contract, a second run that journals nothing new.
