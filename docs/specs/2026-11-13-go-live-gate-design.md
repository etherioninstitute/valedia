# Design: the go-live gate (what stands between the paper order path and a real-money order)

2026-11-13. Roadmap 4.2, 4.8, 4.9. Status: design, written by the lead. Nothing here is built. Nothing in it
lets the trader place a real-money order on its own: every lock below is a file or a number that only the owner
writes, and the code never creates any of them.

## What exists today

- The order step turns the paper book into netted limit orders, through the risk gate and the order ledger, and
  runs only in Webull's test environment. It ran end to end there on 2026-11-13.
- The adapter sends a production order only with the owner's file `config/webull_live.json` (the account, a cap
  per order, a signed sentence), a known price, and a value under the cap.
- Missing: a way for the owner to approve each batch, a check that the paper record is long enough, a cap on the
  money deployed, and a live mode of the order step itself.

## The four locks, all the owner's

**1. The confirmation file** `config/webull_live.json`, written by hand:

    {"account_class": "INDIVIDUAL_CASH", "account_ends": "<last 4 characters, as `webull check` prints them>",
     "confirmed_by": "<your name>", "max_order_value": 250, "max_capital": 2000,
     "valid_until": "2026-12-31", "i_understand_this_trades_real_money": true}

The account is named by its class and by the last four characters that the read-only `check` command prints
for it, because the id Webull's API uses is not shown in Webull's app. `max_capital` is the most money the
order step may put to work in all. `valid_until` is at most 31 days ahead: the confirmation lapses unless the
owner renews it. The old form (the whole account id) is no longer accepted.

**2. The checklist** `config/go_live.json`, written by hand, with the owner's own numbers (roadmap 4.9):

    {"min_paper_broker_days": <n>, "min_reconciled_fills": <n>, "min_days_since_a_stopped_run": <n>,
     "fills_checked_by_hand": <n>, "min_fills_checked_by_hand": <n>, "confirmed_by": "<your name>"}

The order step computes the first three from its own paper record (days on which the paper order step ended
"done", fills journaled with a clean reconcile, days since the last stopped run) and refuses live mode while
any is unmet, saying which and by how much. A stopped run is any paper run of the order step, unattended or
practice, that stopped, except a stop outside market hours (stage `session`) and a refused approval (stage
`ticket`); `min_days_since_a_stopped_run` is met when no stopped run lies within that many days. The
hand-check count is the owner's statement. There is no default:
without the file there is no live mode.

**3. The limits** `config/risk.json`: in live mode `max_orders_per_day` must be present (the test environment's
fallback of 40 does not apply), and a second, independent price source must be configured and agree with the
first within its tolerance for every order (roadmap 5.2; built separately).

**4. Approval of every batch by ticket number.** In live mode the order step never sends in the run that
decides. It works in two runs:

- **Propose.** Everything the order step does today up to the moment before `intend`: reconcile, targets,
  quotes, the risk gate. Then it writes a ticket instead of sending: a six-digit number, the orders (fund,
  side, units, the quote, the limit, the value), the account's equity and cash as read, the time, and an expiry
  15 minutes later. It tells the owner (the alert file and a notification on the Mac; a phone message once the
  owner has chosen a channel) and ends. Nothing has been sent or intended.
- **Approve.** The owner runs the same command with `--approve <number>`. The step reads the account again,
  asks every quote again and runs the risk gate again on the fresh quote. An order is sent only if it is still
  wanted with the same side and the same units, its fresh quote is within 0.5% of the ticket's quote, and the
  gate passes; otherwise that order is dropped and reported. What is sent goes through the order ledger
  exactly as today (one send, an unknown outcome blocks, each fill journaled once).
- A ticket can be used once. A ticket past its expiry, a wrong number, or a ticket made for another account,
  day or environment does nothing. Tickets are kept append-only with what became of each.
- Settled while building (2026-11-13): the full number is printed once, on its own line, and is kept in no
  file but the ticket file; summaries and the journal hold only its last two digits. Only the latest ticket is
  live: a new proposal supersedes every earlier unused one. Three refused approvals in a day lock approval until
  the next proposal. Each ticket order ends as exactly one of: sent (with its tag), not sent (with the reason),
  left over (the run ended before it was reached), and until the order ledger knows, unknown; every later run
  completes what a killed run left open, so the record always adds up against what the broker holds.

Running without approval in live mode is not built. Whether the trader may ever send live orders unattended is
a later decision of the owner's, after a live record exists.

## Live mode of the order step

`order-step --live` exists only together with `--propose` or `--approve <number>` and `--capital <dollars>`
(at most `max_capital`). In live mode the venue must be the production environment by the same plain-attribute
checks the test environment gets today, and the host must be exactly Webull's production host. All four locks
are checked before anything is read from the account. Whole shares only. The account must be of the class the
confirmation names (stocks and funds: Individual Cash; event contracts have no live mode).

The paper mode is unchanged byte for byte: the scheduled paper job keeps running without tickets. The ticket
flow can also be switched on in the test environment (`--propose` / `--approve` without `--live`), so the owner
can practise it on paper before any real order.

## First real order

After the locks are in place: one propose-and-approve run with `max_order_value` and `max_capital` set small
by the owner. The owner compares the fill in Webull's app with the journal. Only then are the caps raised.

## What is not in this design

Unattended live orders; event contracts live; short selling; options; a switch that sells everything (the
halt file stops new orders today; closing positions on a signal is a separate decision); the phone channel.

## Order of building

1. The ticket flow in the test environment (propose, approve, the re-check, expiry, the ticket record).
2. Live mode and its locks (the new confirmation form in the adapter and the order step, the checklist, the
   required limits, the capital cap).
3. The second price source and its cross-check.

Each step: tests first, a blind verification by a fresh verifier, then the lead's own run in the paper account
where the step can be run there. Nothing in live mode is cleared for real money until a reviewer from another
model family has looked or the owner waives that (roadmap 5.4), and the owner says go.
