# The leaders' record (T52, first build, repaired by addendum 2)

What ten fixed Polymarket wallets hold, read in passes, and how their holdings change. Code:
`valedia_trader/research_loop/leaders_monitor.py`. It reads Polymarket's public data service through
`valedia_trader/venues/polymarket_data.py`, with no key and no account, GET only, a generic client name, at most
three requests a second, and the reader's allow-list (one host, `/v2/` paths only).

## Commands

```
python -m valedia_trader.research_loop.leaders_monitor fix-leaders [--store PATH]
python -m valedia_trader.research_loop.leaders_monitor pass        [--store PATH] [--max-memory-mb 500]
python -m valedia_trader.research_loop.leaders_monitor report      [--store PATH] [--days 30]
```

The default store is `artifacts/leaders_monitor/leaders.sqlite`, with its lock file `leaders.sqlite.lock` beside it.
Exit codes: 0 done, 2 another run holds the lock, 3 refused (the words say why), 5 the pass stopped for memory,
6 the pass finished but at least one wallet's read ended in `internal_error` (see Failed reads).

`pass` is one command: it reads every leader once and ends. Nothing in this build creates, installs or describes a
repeating job, a loop, a daemon or a watch mode. Whether `pass` runs again, and how often, is the owner's decision
for each job.

## The leaders (`fix-leaders`, once)

- It reads the all-time PNL boards of two categories, `sports` and `crypto`, five rows each.
- A board with fewer good rows, or fewer distinct wallets, than asked is refused, and nothing is stored. A category
  the service does not know answers 200 with an empty board, which is why the rows are counted.
- It stores each leader's board, rank, profit, volume, the read time and the moment of fixing, in one transaction.
- The claim is an INSERT of the meta key `leaders_fixed_at` inside `BEGIN IMMEDIATE`. Of two runs at once, exactly
  one fixes. Every later run refuses before it makes any request.
- A wallet on both boards is one leader with both ranks. The list is not filled up to ten.
- Nothing ever re-ranks, replaces or drops a leader. A leader that holds nothing, or that cannot be read, stays in.
- `pass` and `report` refuse, in words, until `fix-leaders` has run.

## A pass

- **Lock.** It takes the lock exclusively, without waiting. A second pass prints one line and exits with code 2.
- **A pass that died.** Holding the lock, a pass first marks every earlier pass still `running` as `died` (none can
  be running). `report` counts the window's passes by status.
- **One wallet at a time.** Each leader's open positions are read at most 20 pages deep (20,000 positions), and
  memory is read at each wallet. Above `--max-memory-mb` the pass stops cleanly between wallets.
- **Reader checks.** The reader validates every row:
  - The ids and the money fields decide whether a row is rejected. Rejected rows are stored and counted.
  - Exact repeats, and rows alike in wallet (folded to lower case) and token but different elsewhere, are counted
    as rejects. No row of a conflicting pair is kept.
  - `title`, `outcome`, `end_date` (a real date, not the service's 1970-01-01 for none) and `current_price` (from 0
    to 1) are kept for information only. A missing or malformed one is stored as None and counted, never a reject.
  - A `first_entry_at` of 0 is None.
  - No value of any size or type stops the reader. A whole number beyond a float's range is a malformed row with
    the too-large reason where that rule applies, else its field's own reason; in a position's money field it is
    that field's reject, in `current_price` it is None. A string holding a NUL, or a lone surrogate that UTF-8
    cannot encode, is malformed: None for an information field, the field's reject for an id or a status.
- **Baseline.** A wallet's first good read is a baseline. It creates no change, and its holdings are kept as a
  snapshot.
- **Changes.** A later good read is compared with that wallet's LAST GOOD read, not with the last pass:
  - NEW: a token not held then. It carries `flip` when its opposite token was held.
  - INCREASE or DECREASE: a size that moved by more than 1e-6. Sizes are compared as the numbers the service
    sends (floats); a move within float rounding of 1e-6 may fall on either side of the rule.
  - EXIT: a token held then and gone now, written only by a CLEAN read (no rejected and no conflicting row; a true
    repeat does not count).
  - CARRIED, not an EXIT: in a good read with at least one rejected or conflicting row, a held token missing from
    the good rows may only be unreadable. Its holding stays as it was, marked `carried` with the read that last saw
    it (`seen_read_id`, `seen_read_ts`), and the read counts its carried holdings. A later size is compared with
    the carried one; the first clean read without the token writes its EXIT.
  - Each change stores the size before and after, this read's time, the previous good read's time and the gap
    between them, and (INCREASE, DECREASE, EXIT) `last_seen_ts`, the time of the read that last saw the token: for
    a carried token's EXIT the exit lies between `last_seen_ts` and this read. A missed pass shows as a longer gap.
  - Not covered: a token whose row was unreadable at the first read that served it (its wallet's baseline, or the
    read after it was bought) is not in the record, so the first read that reads it well records it as NEW (at a
    baseline: a NEW for a token already held then; otherwise a NEW bounded one read too late).
- **Failed reads.** A failed read records only the failure and infers nothing. Holdings stay as they were and no
  exit is recorded. The reasons:
  - `read_error`: an error after the reader's retries.
  - `page_limit`: more than 20 pages.
  - `rows_shrank`: a page shorter than the first while more were promised.
  - `internal_error`: an error of any other kind while that wallet was read or written (not a memory error, not an
    interrupt). Only the error's class name is stored, never a row's content. The pass goes on to the next wallet
    and ends with code 6; its summary line counts such wallets.
- **One write per wallet.** A wallet's read, its holdings, its changes, its snapshot and its rejects are committed
  together or not at all. A kill at any point leaves either the old holding, or the new one with its changes.
  `report` checks this on every run and says whether the store is consistent.
- **Snapshots.** A full copy of a wallet's holdings (carried ones marked) is kept at its baseline and once per UTC
  day, also when it holds nothing: the read that took the day's snapshot is marked (`wallet_reads.snapshot_day`).
- **Retention.** At the end of each pass, rows of `changes`, `snapshots`, `rejects`, `wallet_reads` and `passes`
  dated more than 365 days before the pass are removed. Removal goes at most 5,000 rows a statement and at most 20
  statements a pass. `holdings_current`, `leaders` and each wallet's last good read are never aged out.

## The lines

- `pass` prints one line per wallet: its first ten characters, good or failed, rows, rejects, the count of each
  kind of change, carried holdings, seconds and memory. It ends with one summary line (with `internal_error=N`).
- `report` prints, over a stated window:
  - counts per leader and per kind of change, and per leader its carried holdings and the share of its good reads
    that carried any;
  - the largest and the median gap between good reads;
  - the share of failed reads, by reason;
  - the passes by status (done, died, stopped_memory).
- No line names a title, an outcome, a token, a market or a price, nor a wallet beyond its first ten characters.
  Whoever needs what a leader holds reads it from the store. No position's result is computed here.

## Tables

| Table | What it holds |
| --- | --- |
| `meta` | The claim `leaders_fixed_at`. |
| `leaders` | The wallet; rank, profit, volume and read time on each board; when the leaders were fixed. |
| `passes` | One row per pass: start, end, status (`running`, `done`, `stopped_memory`, `died`), good, failed, rows removed. |
| `wallet_reads` | One row per wallet read: good or failed with its reason, rows, pages, rejects and unread fields as JSON counts, baseline, the previous good read and the gap, the count of each kind of change, the carried holdings, and `snapshot_day` when it took the day's snapshot. |
| `holdings_current` | Each wallet's holdings as of its last good read (`read_id`): its good rows, and its carried holdings (`carried` 1) with the read that last saw each (`seen_read_id`, `seen_read_ts`). |
| `snapshots` | Full copies of a wallet's holdings: the baseline, and one a UTC day (carried ones marked). |
| `changes` | NEW, INCREASE, DECREASE and EXIT rows, with `last_seen_ts`. |
| `rejects` | The rows the reader refused, truncated to 2,000 characters, with their reasons. |

## What it does not do

- It makes no match to another venue, calls no model, reads no price from another venue and makes no paper trade.
  Those are later slices, after the forward document is written and committed. Their tables are not created here.
- It neither trades nor needs a credential.
- It does not run itself.
