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
(8), a busy one (9). The last line is `DONE step N` (code 0) or `STOPPED step N at the cap ...` (code 5). `counts`
refuses where there is no book (code 3) and makes none.

`counts` prints COUNTS ONLY: copies opened, open and closed (settled, exit), Kalshi events (all, and with a closed
copy), copies from baseline holdings and from new ones, wallets noted, skips by reason, the NEAR book by family, flags
by kind, and the days since the first copy. No line of either command names a price of a copy.

## Which holdings, and the conditions

At each wallet's first good read (its baseline) each holding of that read; at each later good read each NEW the record
vouches for (`vouched` = 1). Each is considered ONCE: the book's `considered` table remembers (wallet, read, token), and
a second `step` on the same record considers nothing again. Holdings are taken in the order of the pass's read time,
then wallet, then token, so which wallet opens a copy and which is noted on it is fixed.

The skip reasons, in the order applied (each skip has exactly one; the first that fails decides):

| # | reason | when |
| --- | --- | --- |
| 1 | `holding_not_in_record` | the holding's row at that read is no longer in the record (only when two reads of one wallet fall on one UTC day and the step did not run between them); its value is unknown |
| 2 | `value_under_1000` | its value at that read is under 1,000 dollars |
| 3 | `price_outside` | Polymarket's price for it is outside 0.10 to 0.90, or unknown |
| 4 | `no_same` | no counterpart of class SAME in the map; a holding of a NEAR family also gets a row in the NEAR book |
| 5 | `not_listed` | its series is not in the listing table (today only KXNBAGAME of the map's four is listed) |
| 6 | `name_not_in_table` | a team, club or person not in the map's tables, or the title, slug and outcome do not agree |
| 7 | `leaders_disagree` | another wallet of the set holds another outcome of that market at that pass |
| 8 | `late` | the match read would fall later than 6 hours after the pass's read of that wallet (tested before any request, again before the book read, and on the match read's own stamp) |
| 9 | `read_failed` | Kalshi's list of the series' open events, Polymarket's description, or Kalshi's book could not be read |
| 10 | `no_unique_market` | not exactly one Kalshi market for it in the series' open events |
| 11 | `market_not_open` | the market is not `active` with a close time after the moment (the list's status, and its close time against the match read) |
| 12 | `text_departs` | either venue's text, names and dates aside, departs from the map's template |
| 13 | `already_open` | a copy of that Kalshi market and side is open: the wallet is noted on it (`copy_wallets`, role `noted`) |
| 14 | `ask_outside` | the ask of the copy's side is outside 0.05 to 0.95, or there is no ask |

Why the order cannot change which copies are made: a copy is made only when every condition holds, and each
condition is a fact about the record, the services' answers and the clock, none of which depends on the order in which
they are tested. The order decides only which reason is written and which requests are spent. Two things could
depend on order: the clock at the match read (more requests before it make it later), and the open copies (an earlier
holding's copy makes a later one `already_open`). The first is handled by testing every request-free condition (1 to
8) before any request and making no request for a holding they reject, so no order makes a match read earlier; the
second is fixed by the fixed order of holdings, not by the order of the conditions.

## The match read and the paper purchase

ONE read of Kalshi's public order book for that market after the pass, stamped with this program's own clock (the
wall time when the answer is in). The copy's side: Yes for a team's game market (the map's side mapping for the other
series). Kalshi sends bids only: the ask of Yes is one less the best No bid, the ask of No one less the best Yes bid;
the contracts on offer at that ask are that bid's size. The copy is ONE contract at that ask, with the read's time,
the ask, the contracts at it, the raw book, and both venues' texts and their SHA-256 hashes (Polymarket's
`description` from its market-description service, asked once per market and kept by condition id; Kalshi's
`rules_primary` and `rules_secondary`).

## What closes a copy

- **(a) settled**: Kalshi's market record shows `status` exactly `finalized`, a binary market, a settlement value from
  0 to 1 and a settlement time in the past (a `yes` or `no` result must agree with the value). The copy closes at what
  one contract of its side is settled at, at Kalshi's settlement time. A value strictly between 0 and 1 is Kalshi's
  "fair price" for a game not played: kept and flagged `kalshi_fair_price`.
- **(b) exit**: the record shows the opening wallet's EXIT of that token in a read after the copy's read, and the copy
  is not held: Polymarket's market is read first (see (c)); then ONE read of Kalshi's book, and the copy closes at the
  bid of its side, stamped. No bid: flagged `exit_no_bid` and held to settlement.
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
delete, and a flag or a noted wallet on a closed copy.

## Pacing and requests

EVERY Kalshi request goes through ONE pacer, installed as the `KalshiData` client's own pacer, so every try of every
call waits on it: `--kalshi-pause-seconds` between requests (default 10.0, never under 5.0: a collector on this machine
reads the same host) and `--kalshi-cap` requests a step (default 60, at least 2). The client is built with one try a
call: a 429 or a server error is a failed read, never a retry. At the cap the step stops between holdings (the holding
being worked on is written not at all), says so and ends with code 5; the next step carries on. Settlement checks are
the last stage of a step: when the cap is reached there, every new holding and exit is already done, so the step ends
DONE, says how many checks are left, and the next step starts with those (the place is kept in the book).

Requests of one step: `R = sum over series of P(s) + M + X + S`, where `P(s)` is the pages of series `s`'s list of open
events (one page holds 200 events), counted only for a series reached by a holding that passes conditions 1 to 8 (so
today at most KXNBAGAME); `M` the holdings that reach the match read (one book each); `X` the exits that reach the bid
read (one book each); `S` the open copies' settlement checks (one market read each; two when Kalshi's live tier
answers 404 and the historical tier is asked). It is the fewest this design can make: the market cannot be known
without the series' list, which is read once a step whatever the number of holdings; the price is a book read and is
read once per copy and once per exit close; a failed list is not asked again in that step. Settlement checks are NOT
batched: Kalshi's `/markets` may take a list of tickers, but no answer saved here or call in this repo confirms it, and
a batch the server ignored would miss settlements without a word; one market read a copy is what `KalshiData.market`
already does and is checked. Batching is the lead's call once that parameter is confirmed.

Polymarket's description service: GET only, one host (`gamma-api.polymarket.com`), one path (`/markets`), the
parameters `condition_ids` and `closed`, the readers' generic client name, no key, about three requests a second, a
redirect never followed. A market's text is asked with `closed=false` and, only when that answers none, `closed=true`;
it is kept in the book, so it is asked once. The part checks of (c) ask again (one market read each), because they need
the market's state at that moment.

## The book's tables

| Table | What it holds |
| --- | --- |
| `meta` | where the settlement checks carry on |
| `steps` | each step: start, end, status (`running`, `done`, `capped`, `died`), Kalshi and Polymarket requests, considered, opened, closed |
| `considered` | each holding considered: wallet, read, token, baseline or new (with its change), the pass and its read time, the step, `copy` or `skip` |
| `skips` | one row per skipped holding with its one reason and a short detail |
| `copies` | wallet, Polymarket condition, token and outcome, series, Kalshi event and market, side, the pass's read time, the match read's time, the ask, the contracts at it, the raw book, both texts' hashes, open or closed, how and when closed, the close price or settlement |
| `copy_wallets` | the wallet that opened each copy and the wallets noted on it |
| `copy_flags` | the flags of (b) and (c) |
| `texts` | both venues' texts by hash |
| `pm_descriptions` | Polymarket's description of each market asked, by condition id |
| `near_book` | the holdings whose counterpart is NEAR: family, title, outcome, read; for information, never in the rule |

One holding's rows are written together or not at all; a step that dies leaves a book the next step continues from
(a step still `running` is marked `died` by the next).

## Where THE RULE was silent or could not be carried out as written (the reading chosen makes fewer copies)

- **The value at that read** is known only while the record keeps the holding's row at that read (its snapshot or its
  current holdings); otherwise the holding is skipped (`holding_not_in_record`), a reason the ticket's list lacked.
- **"At that pass"** for the leaders' disagreement: another wallet counts as holding the opposite outcome when its
  current holdings (carried ones too) or a snapshot taken in that pass show it.
- **"Open at the match read"**: Kalshi's book carries no status, so the market's status is the step's list read
  (minutes before), and its close time is checked against the match read's stamp.
- **"Departs from the template"**: the map quotes fragments; the templates here are the whole texts read on
  2026-11-18 with only names, numbers, dates and times as slots, white space collapsed, and the game's no-result date
  required to be two days after the game. Any other change is a departure.
- **The NEAR book** is a separate table, not a kind of copy (the document: "recorded in a separate book"); it makes no
  Kalshi request.
- **An exit with no bid** is held to settlement and flagged; an exit after Polymarket closed is the venues parting.
- **The wallets of the set** are the record's `leaders` table: pointing the record at the screened set is the lead's
  step before the first `step`.

## What this slice does NOT do

The final read (the mean result and its p-value); any schedule, loop, daemon or watch mode; any order, preview of an
order, key or account; anything to Webull. Whether and when `step` runs is the owner's decision.
