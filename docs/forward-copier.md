# The forward copier, first slice (T54a): the matcher and the paper book

Carries out "The copy" of `docs/research/2026-11-18-forward-copy-on-webull-prediction.md` (THE RULE) after each pass
of the leaders' record (`docs/leaders-monitor.md`). Code: `valedia_trader/research_loop/forward_copy.py`; the rule map
and listing table as data: `valedia_trader/research_loop/forward_copy_map.json` (a test fails when that file and the two
tables disagree on a series, its class or its listing). It computes **no result**: no profit, no mean, no sum of
prices of copies. The one read of the decision rule is a later slice.

## Commands

```
python -m valedia_trader.research_loop.forward_copy step   --record PATH --book PATH \
                                                           [--kalshi-pause-seconds 10.0] [--kalshi-cap 60]
python -m valedia_trader.research_loop.forward_copy counts --book PATH
```

`step` opens the record READ ONLY (after the record's own shape check) and never writes it. It makes the book at its
first run; later runs check the book's whole schema and `user_version` and refuse another (code 7), an unreadable file
(8), a busy one (9), another step running on the same book (75). The last line is `DONE step N` (code 0) or `STOPPED step N at the cap ...` (code 5: work is left,
wherever the cap was reached, in the new holdings or in the settlement checks and their exits, and the line says
what; a step that could do no unit of work at all also says `NO PROGRESS`). `counts`
refuses where there is no book (code 3) and makes none.

Exit codes of `step`: 0 done; 3 refused (no record, the book is the record, a record or lock that cannot be used); 5
stopped at the cap; 7 another version's book or record; 8 unreadable; 9 busy; **75 another step is running on this
book**. One step at a time on a book: before it reads or sends anything `step` takes an exclusive, non-blocking
`flock` on `<book>.step.lock` (made if missing; a file beside the book, not part of its bytes; **the lock follows the real path**: every spelling of
the book, relative or through a symlink, takes the one lock) and holds it to the end
of the step (the operating system drops it if the process dies). A second step finds it taken, says "another step is
running on this book", and exits 75 with no request, no write and no `died` marking of the first. (Two steps at once
could read the same time of the last Kalshi request and send with no pause between, beside the collector on the same
host.) A book that a process left with a hot journal (it died in the middle of a write) is recovered by `step`, which
opens it read-write once to let SQLite roll the journal back and then checks the shape as before; `counts` only says
"needs a step to recover". A pause or cap that is not a finite number (`inf`, `nan`) is refused at the command line. Creating a book is
ONE transaction with its `user_version`: a creation that stops part way leaves no file content, and the next step
makes the book again.

`counts` prints COUNTS ONLY: copies opened, open and closed (settled, exit), Kalshi events (all, and with a closed
copy), copies from baseline holdings and from new ones, wallets noted, skips by reason, the NEAR book by family, flags
by kind, and the days since the first copy. No line of either command names a price of a copy.

## Which holdings, and the conditions

At each wallet's first good read (its baseline) each holding of that read; at each later good read each NEW the record
vouches for (`vouched` = 1); an unvouched NEW is never considered, so never copied. A holding of a pass the record
still shows `running` is left for a later step (the match read is the first read AFTER the pass); the pass's status is
read again when the holding is considered, so a pass that started after the running ones were collected is left too
(counted `pass_still_running`, no row). A NEW comes only from a good read (`wallet_reads.good` = 1). Each is considered ONCE: the book's `considered` table remembers (wallet, read, token), and
a second `step` on the same record considers nothing again. Holdings are taken in the order of the pass's read time,
then wallet, then token, so which wallet opens a copy and which is noted on it is fixed.

The skip reasons, in the order applied (each skip has exactly one; the first that fails decides):

| # | reason | when |
| --- | --- | --- |
| 1 | `holding_not_in_record` | the holding's row at that read is no longer in the record (only when two reads of one wallet fall on one UTC day and the step did not run between them); its value is unknown |
| 2 | `value_under_1000` | its value at that read is under 1,000 dollars |
| 3 | `price_outside` | Polymarket's price for it is outside 0.10 to 0.90, or unknown |
| 4 | `no_same` | no counterpart of class SAME in the map (the class is read from the map by the program); a holding of a NEAR family also gets a row in the NEAR book |
| 5 | `not_listed` | its series is not in the listing table's rows of series seen listed as tradable (of the map's four: KXNBAGAME, KXNBASPREAD and KXPREMIERLEAGUE are; KXPRESNOMD, listed but NOT tradable on 2026-11-18, is not, so it gets no copy) |
| 6 | `name_not_in_table` | a team, club or person not in the map's tables, or the title, slug and outcome do not agree |
| 7 | `opposite_unknown` | the holding's `opposite_token_id` is empty in the record: the other outcome's token is not known, so whether the leaders disagree cannot be told (the record's changes carry no market id); not copied |
| 8 | `other_wallet_read_unclear` | another wallet of the set has no CLEAN read in that pass: the rule copies only where no other wallet holds the opposite outcome at that pass, and with such a wallet nobody can say it does not. A clean read is the record's own (the one that lets an EXIT be recorded): in that pass, `wallet_reads.good` = 1, `carried` = 0 and no rejected rows (`rejects` holds nothing but `repeated_row`). A failed read, a carried or rejecting one, and no read in the pass are all unclear; fail closed |
| 9 | `leaders_disagree` | another wallet of the set held another outcome of that market at that pass (rebuilt from the record's history, never from its holdings now: see "At that pass" below) |
| 10 | `clock_before_read` | this host's clock stands before the wallet's read: tested before any request, and on a match read's own stamp (also a kept one judging a later holding); no request, no copy |
| 11 | `late` | the match read falls later than 6 hours after the pass's read of that wallet: when a match read of that market and pass is already kept, judged on THAT read's stamp only; otherwise tested before any request (the clock decides only whether a read may still be made) and on the new read's own stamp |
| 12 | `read_failed` | the pass's ONE match read of that market could not be read (a failed BOOK read is the answer for every holding it serves in that pass, even in a later step; a match read begun and never kept counts as failed). A failed series list or description read is NOT a skip: it writes no `considered` row and no skip, the holding is counted `deferred_list` or `deferred_description` and the next step considers it again, until the six hours end it (`late`); a list with a nested `markets` that is not a list of objects is such a failed list |
| 13 | `no_unique_market` | not exactly one Kalshi market for it in the series' open events |
| 14 | `market_not_open` | the market is not `active` with a close time after the moment (the list's status, and its close time against the match read) |
| 15 | `token_not_in_market` | Polymarket's description of the market (asked by condition id; an answer for another condition id is dropped by the reader, so it is no description) does not list the holding's token among its `clobTokenIds` (a JSON list), or that field is not a list. Checked when the description is first read; a description kept from an earlier holding is not checked again |
| 16 | `text_departs` | either venue's text, names and dates aside, departs from the map's template |
| 17 | `already_open` | a copy of that Kalshi market and side is open: the wallet is noted on it (`copy_wallets`, role `noted`) |
| 18 | `ask_outside` | the ask of the copy's side is outside 0.05 to 0.95, or there is no ask |

Why the order cannot change which copies are made: a copy is made only when every condition holds, and each
condition is a fact about the record, the services' answers and the clock, none of which depends on the order in which
they are tested. The order decides only which reason is written and which requests are spent. Two things could
depend on order: the clock at the match read (more requests before it make it later), and the open copies (an earlier
holding's copy makes a later one `already_open`). The first is handled by testing every request-free condition (1 to
11) before any request and making no request for a holding they reject, so no order makes a match read earlier; the
second is fixed by the fixed order of holdings, not by the order of the conditions.

## The match read and the paper purchase

ONE read of Kalshi's public order book for that market after the pass, stamped with this program's own clock (the
wall time when the answer is in). For ONE Kalshi market and ONE pass the program reads the book ONCE: the first time a
holding of that pass needs it. **That pass** is the record's pass of the read that showed the holding
(`wallet_reads.pass_id`; a read with no pass is its own). The read is kept in the book (`match_reads`, written in its
own transaction before anything else is done, with the event and market it was found by), and every holding of that
pass mapping to that market, in that step or a later one (after a cap), is judged by it: its own 6-hour window against
that stamp, its own side's ask from that book, and "open" as of that stamp (the market as found for that read). The
kept read is looked up FIRST, from the holding's names alone, before anything is judged against the clock: a step that
runs late still judges such a holding by the kept stamp. A first read that failed is the answer too: every such holding
is skipped `read_failed`, with no second try in that pass. **Begun before it is sent**: before the book request is
sent, the book holds that this market and pass is being read (`match_begun`, its own committed transaction, as the
pacer keeps the request's time before sending; when the step's cap leaves no room for the request, nothing is marked).
A later step that finds a read begun and never kept (the step stopped between the two writes) takes it as the pass's
first read having FAILED: every holding of that pass and market is `read_failed`, and no second request is ever sent.
**One book read a market and pass, whatever it is for**: the match read and the exit read go through ONE function
(`Step.book_read`), keyed by the market and the pass, with the same begun mark and the same kept read; so a market's
book is requested at most once for a pass, ever, whether a match read or an exit needs it first. A holding skipped before the book read (conditions 1 to 11 before any request) causes no read. When two passes are considered in one step, each has its own first read. The copy's side: Yes for a team's game market (the map's side mapping for the other
series). Kalshi sends bids only: the ask of Yes is one less the best No bid, the ask of No one less the best Yes bid;
the contracts on offer at that ask are that bid's size. The copy is ONE contract at that ask, with the read's time,
the ask, the contracts at it, the raw book, and both venues' texts and their SHA-256 hashes (Polymarket's
`description` from its market-description service, asked once per market and kept by condition id; Kalshi's
`rules_primary` and `rules_secondary`).

## What closes a copy

- **(a) settled**: Kalshi's market record shows `status` exactly `finalized`, a binary market, a settlement value from
  0 to 1 and a settlement time in the past (a `yes` or `no` result must agree with the value). The copy closes at what
  one contract of its side is settled at, at Kalshi's settlement time. A value strictly between 0 and 1 is Kalshi's
  "fair price" for a game not played: kept and flagged `kalshi_fair_price`. **A market Kalshi shows `finalized` whose
  payout this parser cannot read** (a value or time in another shape, a result that disagrees with the value) is never
  closed by an exit: the copy stays open and takes the flag `settlement_unreadable` (once; the step counts
  `settle_unreadable` each time), no book is read and no exit is made for it, and every later step reads the market
  again; a payout that reads later closes the copy at settlement, and the flag stays. The flag holds the copy to
  settlement like the others.
- **(b) exit**, checked AFTER (a) for the same copy in each step ("before that"): a market already final is closed at
  its settlement, never by an exit read afterwards. Each open copy is one unit of work: its settlement check, then, if
  it is NOT final (open in its series' list, or read and not `finalized`), its exit check; only such a copy can close by
  an exit. The record shows the opening wallet's EXIT of that token in a read after the copy's
  read (of a pass no longer running), and the copy is not held: Polymarket's market is read first (see (c)); then THE
  book read of that market for the pass of the read that showed the EXIT ("Kalshi's bid at the first Kalshi read after
  that pass", the same read a match read of that market and pass is): a read already kept is used with no request,
  else it is made and kept. The copy closes at the bid of its side in that read, at that read's stamp. No bid: flagged
  `exit_no_bid` and held to settlement. That read failed, or was begun and never kept: there is no bid for that pass,
  flagged `exit_read_failed` (its own word: the bid is not known to be absent, the read is missing) and held to
  settlement like a copy with no bid; the exit was that pass's, so a later pass asks nothing more. A close whose own
  write fails is written by the next step from the same kept read: never another read.
- **(c) the venues part**, as far as the two services show it, flagged and held to Kalshi's settlement:
  - at an exit, Polymarket's market is already `closed` while Kalshi's is not settled: `parted_pm_closed_first`, held
    (the exit is Polymarket's settlement, not the leader's decision);
  - at Kalshi's settlement, Polymarket's market still trades: `parted_pm_trading_at_settlement`;
  - both settled, and Polymarket's payout for the copied token (`outcomePrices` at its place in `clobTokenIds`) is not
    Kalshi's settlement of the copy's side: `parted_settled_differently`;
  - Polymarket closed but not `resolved`: `pm_unresolved_at_settlement`; its read failed: `part_check_failed`.

  What cannot be detected: anything between two steps (only the moments of a step are seen); a Polymarket resolution
  changed after it was read (a dispute after `resolved`); whether Polymarket's 50-50 and Kalshi's "fair price" are the
  same amount before both are settled; Polymarket's closing time against Kalshi's (only "closed or not" at the read
  is used); a market that closes on Kalshi between the step's list read and the book read (its book is then empty, so
  it is skipped for no ask).

A copy row is never updated except to close it, and a closed copy never changes: triggers refuse any other update, a
delete, and a flag or a noted wallet on a closed copy. Flags (`copy_flags`), the wallets on a copy (`copy_wallets`) and
the book reads (`match_reads`) and their begun marks are append-only: triggers refuse any update or delete. **INSERT OR REPLACE**
deletes the old row without firing a delete trigger, so every append-only table (`considered`, `skips`, `copies`,
`copy_wallets`, `copy_flags`, `match_reads`, `match_begun`, `near_book`) also has a trigger that refuses an insert
meeting an existing row by its key or its rowid; `texts` and `pm_descriptions`, kept by hash and condition id and
written with INSERT OR IGNORE, quietly ignore such an insert instead. The triggers bind anyone writing through SQLite
with the book's schema; a person with the file can still drop a trigger and then rewrite rows, edit the file's bytes,
or delete or replace the whole file. The schema check at the next `step` refuses a book whose triggers were dropped
(its schema differs), but rows rewritten and the triggers put back cannot be told apart: the book is a record kept by
the program, not proof against its owner. Stated plainly: `meta`, `steps`, `texts`, `pm_descriptions` and `near_book`
can be changed by a person with the file (an UPDATE or DELETE is not refused there; `near_book` is for information,
never in the rule); the seven tables that hold copies, flags, notes, reads and what was considered (`copies`,
`copy_flags`, `copy_wallets`, `match_reads`, `match_begun`, `considered`, `skips`) cannot, through SQLite, while their
triggers stand.

## Pacing and requests

EVERY Kalshi request goes through ONE pacer, installed as the `KalshiData` client's own pacer, so every try of every
call waits on it: `--kalshi-pause-seconds` between requests (default 10.0, never under 5.0: a collector on this machine
reads the same host) and `--kalshi-cap` requests a step (default 60, at least 6; a smaller cap is refused in words). The client is built with one try a
call: a 429 or a server error is a failed read, never a retry. **The pause holds across steps**: the wall time of the
last Kalshi request is kept in the book (`meta`, written before each request is sent), and the first request of a step
waits until the pause has passed since it (a clock set back waits the full pause), so capped steps run one after
another never ask faster than the pause. At the cap the step stops (the holding being worked on is written not at all),
says what is left and ends with code 5, whether the cap came in the new holdings or in the settlement checks and
their exits; the next step carries on (the place of the settlement checks is kept in the book). A wall clock set
FORWARD between steps makes the gap look longer, so the first request of the next step may wait less than the pause or
not at all: that is accepted as it is (the pause between the requests inside a step is measured on the monotonic clock
and holds whatever the wall clock does).

**The smallest cap, and progress.** The smallest cap is `MIN_CAP = L + 3 = 6`: `L = 3` the lists a step can need (one
for each listed SAME series of the map: KXNBAGAME, KXNBASPREAD, KXPREMIERLEAGUE), then the largest unit of work, a copy
whose settlement check costs a market read twice (a live 404, then the historical tier) and whose exit then costs one
book read. A unit is one new holding (at most one book read after its list) or one open copy's settlement check with
its exit check (at most 3). A cap reached inside a unit stops before the request, writes nothing of that unit, and the
next step does the unit again whole; the settlement checks start after the copy whose paid unit was done last. So with
any accepted cap and one page per list, every step finishes at least one unit, and on an unchanged record capped steps
reach the same book as one uncapped step within **H + N + 1 steps**, where H is the holdings not yet considered and N
the copies open at the start (each step considers at least one holding until none is left, then does at least one
copy's checks, going round the copies from where the last stopped; the last step finds nothing left). A list of more
pages than the cap allows can still spend a whole step: the step then says `NO PROGRESS` and that a larger cap is
needed.

**Kalshi's requests go to Kalshi only.** The copier hands the client its own fetch (`kalshi_urllib_get`), not the
collector's: GET only, https, the host `api.elections.kalshi.com` and exactly the `/trade-api/v2/` addresses the copier
asks (a series' open events with nested markets, a market's order book, a market's record, live or historical) with a
ticker in Kalshi's shape; any other address is refused before anything is sent. No redirect is followed (a redirect is
a failed read), no proxy is used, the generic client name is sent, an answer over 8 MB or slower than 60 s is a failed
read. Polymarket's description reader has the same refusals.

Requests of one step: `R = sum over s in S of P(s) + M + C + X`, where `S` is the series reached in the step by a
holding that passes conditions 1 to 11 OR holding an open copy, `P(s)` the pages of series `s`'s list of open events (one
page holds 200 events; the list is read once a step and serves both the new holdings and the settlement checks); `M`
the (market, pass) pairs whose first match read is made in the step (one book each; a later holding of that pass for
that market reads nothing); `C` the open copies whose market is NOT in its series' open list, or is shown not open
there (one market read each; two when Kalshi's live tier answers 404 and the historical tier is asked); `X` the exits whose market has no book read kept for the EXIT's pass
that reach the bid read (one book each). For N open copies in S series with C of them out of the list (and nothing
new): `S + C` requests (one page a series), not N. It is the fewest this design can make: the market cannot be known
without the series' list; the price is the pass's one book read; an open copy shown open in the list needs no read; a
failed read is not asked again (the list and the description in that step, the match read in that pass). A market
that leaves the list is still settled: it gets its one market read. Settlement checks are NOT batched further:
Kalshi's `/markets` may take a list of tickers, but no answer saved here confirms it.

Polymarket's description service: GET only, one host (`gamma-api.polymarket.com`), one path (`/markets`), the
parameters `condition_ids` and `closed`, the readers' generic client name, no key, about three requests a second, a
redirect never followed. A market's text is asked with `closed=false` and, only when that answers none, `closed=true`;
it is kept in the book, so it is asked once. The part checks of (c) ask again (one market read each), because they need
the market's state at that moment.

## The book's tables

| Table | What it holds |
| --- | --- |
| `meta` | where the settlement checks carry on; the time of the last Kalshi request |
| `match_reads` | THE book read of each Kalshi market for each pass, made for a match read or an exit, whichever came first: its stamp, whether it was read, the raw book, the event and market it was found by (an exit's read keeps them only when the market was in the step's list) |
| `match_begun` | each match read begun (written before its request is sent): market, pass, time, step |
| `steps` | each step: start, end, status (`running`, `done`, `capped`, `died`), Kalshi and Polymarket requests, considered, opened, closed |
| `considered` | each holding considered: wallet, read, token, baseline or new (with its change), the pass and its read time, the step, `copy` or `skip` |
| `skips` | one row per skipped holding with its one reason and a short detail |
| `copies` | wallet, Polymarket condition, token and outcome, series, Kalshi event and market, side, the pass's read time, the match read's time, the ask, the contracts at it, the raw book, both texts' hashes, open or closed, how and when closed, the close price or settlement |
| `copy_wallets` | the wallet that opened each copy and the wallets noted on it |
| `copy_flags` | the flags of (a), (b) and (c) |
| `texts` | both venues' texts by hash |
| `pm_descriptions` | Polymarket's description of each market asked, by condition id |
| `near_book` | the holdings whose counterpart is NEAR: family, title, outcome, read; for information, never in the rule |

One holding's rows are written together or not at all; a step that dies leaves a book the next step continues from
(a step still `running` is marked `died` by the next).

`deferred_list`, `deferred_description` and `pass_still_running` are counts of the step (on its last line, "left for a
later step"), not skips: nothing is written for those holdings.

## Where THE RULE was silent or could not be carried out as written (the reading chosen makes fewer copies)

- **The value at that read** is known only while the record keeps the holding's row at that read (its snapshot or its
  current holdings); otherwise the holding is skipped (`holding_not_in_record`), a reason the ticket's list lacked.
  Stated plainly: a NEW seen in a read that took no snapshot keeps its row only until that wallet's next read replaces
  it; the step is meant to run after EVERY pass, and a step that runs later skips such holdings (fewer copies).
- **"At that pass"** for the leaders' disagreement is rebuilt from the record's history, never from its holdings now:
  for each other wallet of the set, its last snapshot (baseline or daily, carried rows too) in a read of that pass or an
  earlier one, then its NEW (opened) and EXIT (left) changes after that snapshot up to and including that pass. The
  other outcomes of the market are the tokens of the holding's condition seen in the record, and its opposite token.
  Where the record cannot say (that wallet's read in that pass failed, was carried or rejected rows, or is absent) the
  holding is not copied at all: `other_wallet_read_unclear`, tested ahead of the disagreement (the rule copies only
  where no other wallet holds the opposite outcome at that pass, and nobody can say it for such a wallet; this
  replaces an earlier reading that let the wallet count as holding what it held at its last good read). A wallet
  with no good read yet up to that pass, whose read in that pass was clean, is not known to hold anything. A wallet that opened the opposite outcome only
  AFTER the holding's pass did not hold it at that pass: no disagreement for that holding (the document's words, "at
  that pass"); its own holding, at its own later pass, meets the first wallet's and is skipped `leaders_disagree`.
  The record removes snapshots (and changes) older than 365 days (`RETENTION_DAYS` of the leaders' record); a wallet
  with no good read for that long has no snapshot to rebuild from and counts as holding nothing known. The other
  outcome is named by the holding's own `opposite_token_id`; where that is empty the record cannot say whether the
  leaders disagree (its changes carry no market id), so the holding is not copied (`opposite_unknown`; the saved
  fixture record has 0 such rows of 114).
- **A match read kept by an exit** (the exit came first for that market and pass) holds the event and market only when
  the market was in the step's list; a holding judged by it then takes the market from its step's list, with the kept
  stamp (a market gone from the list since: `no_unique_market`, fewer copies).
- **"After the pass"**: a pass the record still shows `running` is not over, so its holdings and exits are left for a
  later step (they may then be late: fewer copies).
- **A parting of the venues is flagged when a step sees it** at Kalshi's settlement (or at an exit); a divergence
  that begins and ends between two steps with equal final payouts leaves no flag.
- **`step` has no end date**: the test's 120-day stop is an outside supervisor's (the owner's), and `step` buys on
  day 121 if run.
- **The record's roster is the fixed set**; swapping the record's `leaders` table in a running test changes who can veto
  a copy: never done.
- **"Open at the match read"** means open in the step's series list, read seconds to minutes before the book read; a
  market that closes between the two can still be copied (the book then still quotes it).
- **An EXIT seen while Polymarket shows the market `closed`** (resolved or not) counts as the venues parting: a wallet
  cannot sell on a closed market, so its token left by settlement or by removal. The copy is flagged
  `parted_pm_closed_first` and held to Kalshi's settlement (a ruling; the rule's wording is "one settles while the
  other still trades").
- **A Polymarket description** is read once a market and kept; a change of its text after that is not seen.
- **A new `--book` path starts a new test**: nothing links it to an older book.
- **The step is meant to run after EVERY pass**; a step that finds two finished passes of one market reads its book
  once for each pass key, so the second read prices an exit that the first read already followed.
- **The 60 s limit is per wait on the socket**, not a total deadline for a slow body.
- **The EPL names** are the 16 clubs seen on both venues on 2026-11-18 (Leeds United and others are absent by
  design); any other club is never copied.
- **"Open at the match read"** for a holding judged by a match read kept from an earlier step: the market as it was
  found for that read must have a close time after the kept stamp; the step's clock and list are not asked.
- **"Open at the match read"**: Kalshi's book carries no status, so the market's status is the step's list read
  (minutes before), and its close time is checked against the match read's stamp.
- **"Departs from the template"**: the map quotes fragments; the templates here are the whole texts read on
  2026-11-18 with only names, numbers, dates and times as slots, white space collapsed, and the game's no-result date
  required to be two days after the game. Any other change is a departure.
- **The NEAR book** is a separate table, not a kind of copy (the document: "recorded in a separate book"); it makes no
  Kalshi request.
- **An exit with no bid** is held to settlement and flagged; an exit after Polymarket closed is the venues parting.
- **An exit whose copy's settlement check failed or was not reached in the step** waits: only a copy shown not final in
  the same unit closes by an exit. A series whose list read failed has neither its settlement checks nor its exits
  done in that step.
- **The wallets of the set** are the record's `leaders` table: pointing the record at the screened set is the lead's
  step before the first `step`.

## What this slice does NOT do

The final read (the mean result and its p-value); any schedule, loop, daemon or watch mode; any order, preview of an
order, key or account; anything to Webull. Whether and when `step` runs is the owner's decision.
