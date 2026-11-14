# The working stop, the stake ladder, fractional live orders and the unattended switch

Date: 2026-11-14. Status: approved to build by the owner ("Yes, all four, and build the caps design"). Nothing here sends a real order; nothing here is cleared for real money.

## 1. What the owner approved, in his terms

- **His wall, in files only he writes:** the daily loss halt (2%) and the drawdown pause (25%) in `config/risk.json`; the money ceiling, the largest order and a monthly renewal in `config/webull_live.json`; the checklist in `config/go_live.json`. No code writes these files.
- **A working daily stop** the system sets for itself each day: 2.5 times the book's daily swing over the last 25 sessions, never under 1%, never over his daily halt.
- **A ladder** that moves the stake at work under his ceiling, never the ceiling:
  - step 0, 20 sessions: every send needs his ticket approval, as built today;
  - step 1, 40 sessions: unattended at the same stake;
  - after that the stake doubles after every 60 clean sessions, up to his ceiling;
  - a fault halves the stake;
  - a 2% market day only pauses that day;
  - the 25% pause stops everything until he reviews, then resumes one step down.
- **Fractional live orders**, so a small first stake can hold the whole book.
- **The unattended switch**, his own.

Still his, no number approved: the money ceiling, the first stake, orders a day, the largest order, the checklist's figures.

## 2. Decisions made by the lead inside that approval (each is a reading, flagged to the owner)

**D1. One new owner file, `config/ladder.json`.** Read only through `venues.webull.read_owner_file` (same owner, permission, link and folder rules as the other owner files). Exactly these keys: `confirmed_by` (a name), `first_stake` (a finite number above 0), `unattended` (true or false), `fractional` (true or false), `valid_until` (`YYYY-MM-DD`, not past, at most 31 days ahead of the New York date: the same rule as the confirmation). Any other key, a missing key or a wrong type is a refusal in one line. `first_stake` above the confirmation's `max_capital` is a refusal. **With no such file, live mode behaves exactly as it does today** (tickets, `--capital` by hand). No program creates or edits this file. Its bytes join the digest a live ticket is bound to.

**D2. The ladder is computed, never stored.** Its position is a pure function of the journal's entries for the live venue, the exchange calendar and the two owner numbers (`first_stake`, `max_capital`). There is no ladder state file to damage or to edit. The same journal a person can read explains every step.

**D3. What a session is.** A New York date that is an exchange session (XNYS) on which the live venue has at least one `order_step` summary. Each session is exactly one of:
- **fault**: any of its summaries stopped at a stage in `FAULT_STAGES = {"state", "reconcile", "send", "after", "journal", "baseline", "unexpected"}`, or any of its summaries carries a non-empty `faults` list (kinds in D8);
- **clean**: not a fault, and at least one summary has status `done`;
- **skipped**: everything else (only ordinary or data stops: `session`, `ticket`, `lock`, `live`, `clock`, `environment`, `targets`, `follow`, `prices`, `marks`). A skipped session neither counts nor resets the count.

This reads the owner's "a stopped run" as "a run the system's own error or a records mismatch stopped". A run stopped because a price could not be confirmed, or because his own file lapsed, is a skipped day: halving the stake on a data outage would sell half the book for no fault of the book.

**D4. The position.** `(mode, stake, clean, needed)`, starting at `("ticket", first_stake, 0, 20)`.
- a clean session adds 1 to `clean`. When `clean == needed`: in mode `ticket` the mode becomes `unattended` with the same stake and `needed = 40`; in mode `unattended` the stake becomes `min(2 * stake, max_capital)` and `needed = 60`; `clean` restarts at 0. A stake already at the ceiling stays there.
- a fault session: `stake = stake / 2`, `clean = 0`; the mode and `needed` are unchanged. (Literal: no floor. The order ledger's own blocks, not the ladder, are what stops sending after a fault.)
- a drawdown pause followed by the owner's reset (D7): **one step down**, `clean = 0`: mode `unattended` with `stake > first_stake` halves the stake (`needed` stays 60, or 40 when the halved stake is `<= first_stake`); mode `unattended` with `stake <= first_stake` becomes mode `ticket`, same stake, `needed = 20`; mode `ticket` stays. Between the pause and the reset no session counts.
- at every read `stake = min(stake, max_capital)`: a lowered ceiling lowers the stake at once; a raised ceiling raises nothing.
- a session whose day stop tripped (D6) is clean or fault by D3 like any other: a market day is no fault.
The function also returns the list of changes, each with its day and its reason in words.

**D5. The working stop.** `working_stop_pct = min(wall, max(1.0, 2.5 * s))` where `wall` is `max_daily_loss_pct` and `s` is the sample standard deviation (n - 1), in percent, of the last 25 **exact** daily returns of the followed strategies taken together in the paper book (the record the decay monitor already reads; a bridged observation is not exact). Fewer than 25 exact returns: `min(wall, 1.0)`. The result is never above the wall and is reported with its basis ("25 returns ending D" or "fewer than 25 returns: the floor").

**D6. A stop that lasts the day.** When the day's loss at any look is at or above the working stop, the run's summary carries `day_stop` (`{"pct": loss, "limit": working stop, "wall": true or false}`) and non-reducing orders are refused for the rest of that New York day, by this run and by any later run that day (read back from the journal). Reducing orders still pass. Nothing carries to the next day. The base of "the day's loss" is unchanged (the last run of an earlier day).

**D7. The 25% pause for the live venue.** Today only the paper trader creates a latch. Live gets its own: `journal/kill_switch-webull-production.json`, created by the order step when the live drawdown reaches `max_drawdown_pct`, journaled as an entry of type `drawdown_pause`. While it exists every live run stops before any order (the paper latch keeps its present effect). Only the owner clears it: `reset-kill-switch --venue webull-production --who NAME` removes the file, journals `latch_reset` and restarts the live peak from the next recorded equity. The ladder reads those two entries.

**D8. Fault kinds a done run can carry** (`faults` in its summary; written by the order step in the second ticket): `reconcile` (the broker's book and the records differ at the end of the run), `attention:<kind>` (a blocking attention item was opened in this run), `cap` (a fill found outside `max_order_value` or the book above `max_capital` after the run), `drift` (D10).

**D9. Unattended sending.** A live run with neither propose nor approve is allowed only when: `config/ladder.json` is valid with `unattended: true`; the position's mode is `unattended`; every lock is met. It is done as a propose and an approve inside one process (the ticket is made, bound to the same digests, and used at once with the fate `used (unattended)`), so the ticket book stays the single record of what was sent and why. Every other check of a live run is unchanged.

**D10. Live against paper ("drift").** Each live summary records `book_return` (the live book's return for the day from the order ledger's fills and marks, fees included; immune to deposits and withdrawals) and `paper_return` (the followed strategies' paper return for the same day). **The drift fault ships switched off** (`DRIFT_ACTS = False`): its limits cannot be honestly set before the practice venue has produced at least 20 sessions of the same two numbers. Until then drift is measured and shown, not acted on. The owner is told that this part of "clean" is not yet live.

**D11. Capital and fractions under the ladder.** With a valid `config/ladder.json`, the capital of a live run is the ladder's stake; `--capital` must be absent or equal to it. Fractional live units are allowed only when the file says `fractional: true` AND the journal holds at least one reconciled fill of a non-whole quantity on the PAPER venue in a done batch (the broker's handling proven in its test environment first). The ticket binds the fractional flag as today. Production's own handling of fractions stays unknown until the first approved ticket: a refusal there is an ordinary refused order.

**D12. Not in this build.** A working number of orders a day under his cap; the bots' veto or shrink; both key pairs side by side; any change to the paper trader.

## 3. Build order

1. **T34a, a pure module** `valedia_trader/execution_and_risk/stake_ladder.py` with its tests: the ladder file's reader (D1), session facts from journal entries (D3), the position (D4), the working stop (D5), "is today stopped" (D6, the reading half), "is a fractional fill proven" (D11, the reading half). No order-path file changes; the practice gate does not move.
2. **T34b, the order path**: the order step writes `faults`, `day_stop`, `book_return`, `paper_return`; obeys the day stop; creates the live latch; the reset command; capital from the ladder; unattended runs; fractional live units. Order-path files change: blind check, the lead's run, the gate moved and tested both ways, practice behaviour unchanged (the practice comparison set must stay equal).
3. Before real money: the drift limits calibrated on the practice record; an off-family review of T34b needs the owner's yes for that run.

## 4. What the owner will see and do

- Until he writes `config/ladder.json`, nothing changes.
- With it: `order-step --live --check` prints the position (mode, stake, clean sessions of how many, the next change), the working stop and its basis, and whether fractions are allowed and why.
- A fault, a day stop, a pause and a reset each appear on the alerts page with the day and the reason.
