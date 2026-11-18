# The leaders' record (T52, first build, repaired by addendum 2)

What ten fixed Polymarket wallets hold, read in passes, and how their holdings change. Code:
`valedia_trader/research_loop/leaders_monitor.py`. It reads Polymarket's public data service through
`valedia_trader/venues/polymarket_data.py`, with no key and no account, GET only, a generic client name, at most
three requests a second, and the reader's allow-list (one host, `/v2/` paths only).

## Commands

```
python -m valedia_trader.research_loop.leaders_monitor fix-leaders [--store PATH]
python -m valedia_trader.research_loop.leaders_monitor fix-leaders --wallets-file FILE --store PATH
python -m valedia_trader.research_loop.leaders_monitor pass        [--store PATH] [--max-memory-mb 500]
python -m valedia_trader.research_loop.leaders_monitor report      [--store PATH] [--days 30]
```

The default store is `artifacts/leaders_monitor/leaders.sqlite`, with its lock file `leaders.sqlite.lock` beside it.
The last line of every `pass` starts with one word for how it ended, and the exit code says the same:
DONE 0, every wallet was read well or is past the page limit (a known state; the line counts such wallets);
LOCKED 2, another pass holds the lock, nothing done (`report` answers code 2 too while a pass holds the lock, with
"a pass is writing; try again"); REFUSED 3, nothing done because there is no store or no leaders are fixed yet
(run `fix-leaders` first), or the lock file or the store cannot be opened or written (the words say which); FAILED 4, the pass
ran but at least one wallet's read failed (`read_error`, `rows_shrank`); STOPPED 5, the pass stopped for memory
between wallets; INTERNAL 6, the pass ran but at least one wallet's read ended in `internal_error` (see Failed
reads); SHAPE 7, the store was made by another version of the record or is not its store, nothing done;
UNREADABLE 8, the file cannot be read as a store, nothing done; BUSY 9, another connection holds SQLite's lock on
the store ("a pass is writing; try again"), nothing done. How long each waits for that lock: the shape check of
every command, `report` and `fix-leaders` at most five seconds (WAIT_SECONDS); a pass, under its own lock file,
up to thirty seconds (BUSY_SECONDS) for another connection's write lock, so that a short reader or writer does not
cost it its day. When several hold, STOPPED comes before INTERNAL and
INTERNAL before FAILED. `fix-leaders` and `report` answer SHAPE, UNREADABLE and BUSY with the same codes and words.
Only `fix-leaders` makes a store: `pass` and `report` where there is no store (no folder, no file, an empty file)
refuse with code 3 and make nothing. A store's `user_version` is part of its shape (this version's is 0).

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
- The meta key `leaders_source` says `boards`. A store fixed before T52c has no such key; it reads as boards and no
  command writes the key into it.

## The leaders from a file (`fix-leaders --wallets-file FILE --store PATH`, once)

The forward test of the owner's copy method fixes its own set of up to 20 wallets by a screen of its own; the record
is then pointed at exactly that set with this form of the command.

- `--store` is required, and it may not name the default store (the same path, also in another case, or the same
  file). The default store stays the boards' ten and is never made from a file.
- The file: one wallet address a line, `0x` and 40 hex digits (either case) and nothing else on the line; a line may
  end in CR LF. Blank lines (empty or spaces only) and lines whose first character is `#` are skipped. Addresses are
  folded to lower case for the key, as a board's are; the wallet is kept as written.
- Refused in words naming the line, with code 3: a malformed line (a leading space, a trailing comment, `0X`, a byte
  that is not ASCII are malformed), a repeat after folding (the words name both lines), more than 50 wallets (the
  words name the line of wallet 51). Refused too: a file that names no wallet, or that cannot be read. Every refusal
  is made before anything is created: no folder, no store, no lock file.
- It makes no request of any kind: no client is built and no board is read.
- What is stored, in the claim's one transaction, with the schema and `user_version` unchanged:
  - `leaders`: `wallet_key` (folded), `wallet` (as written), every board column (`sports_rank`, `sports_pnl`,
    `sports_volume`, `sports_read_ts` and the same four for crypto) NULL, as the other board's columns of a leader
    on one board only are; `fixed_ts` the moment of fixing. `pass` reads leaders by `wallet_key` (ordered as rankless,
    by key), `report` prints a NULL rank as `-`, and the consistency check reads `wallet_key` only.
  - `meta`: `leaders_fixed_at` (the claim), `leaders_source` `file`, `leaders_file_sha256` (the file's SHA-256, of
    its bytes as read), `leaders_file_wallets` (its count of wallets) and `leaders_file_read_at` (when it was read).
- A pass and a report work on such a store as on any other. `report` shows each such wallet as `sports#- crypto#-`
  and its first line says `from a file (N wallets, sha256 ...)`; on a store from the boards it says
  `from the boards`.
- **The record never mixes sources: one store, one source.** A store that already holds leaders refuses either form
  of `fix-leaders`, whatever their source, and no command adds a wallet to a fixed set, removes one or changes where
  the set came from.

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
  - VOUCHED. Every change row carries `vouched` (1 or 0). A NEW says "at the previous good read this wallet did
    not hold the token", and only a CLEAN previous good read can say that: a NEW is vouched only when that wallet's
    previous good read was clean, else unvouched (the token may have been held and its row unreadable, as at a
    baseline with a rejected row). INCREASE, DECREASE and EXIT are always vouched. ONLY A VOUCHED NEW IS A POSITION
    SEEN TO OPEN; an unvouched NEW is a holding first read well, opened at some unknown time.
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
  - counts per leader and per kind of change (NEWs as vouched and unvouched), and per leader its carried holdings and the share of its good reads
    that carried any;
  - the largest and the median gap between good reads;
  - the share of failed reads, by reason;
  - the passes by status (done, died, stopped_memory).
- No line names a title, an outcome, a token, a market or a price, nor a wallet beyond its first ten characters.
  Whoever needs what a leader holds reads it from the store. No position's result is computed here.

## Tables

| Table | What it holds |
| --- | --- |
| `meta` | The claim `leaders_fixed_at`; `leaders_source` (`boards` or `file`); for a file, `leaders_file_sha256`, `leaders_file_wallets`, `leaders_file_read_at`. |
| `leaders` | The wallet; rank, profit, volume and read time on each board (all NULL for a wallet from a file); when the leaders were fixed. |
| `passes` | One row per pass: start, end, status (`running`, `done`, `stopped_memory`, `died`), good, failed, rows removed. |
| `wallet_reads` | One row per wallet read: good or failed with its reason, rows, pages, rejects and unread fields as JSON counts, baseline, the previous good read and the gap, the count of each kind of change, the carried holdings, and `snapshot_day` when it took the day's snapshot. |
| `holdings_current` | Each wallet's holdings as of its last good read (`read_id`): its good rows, and its carried holdings (`carried` 1) with the read that last saw each (`seen_read_id`, `seen_read_ts`). |
| `snapshots` | Full copies of a wallet's holdings: the baseline, and one a UTC day (carried ones marked). |
| `changes` | NEW, INCREASE, DECREASE and EXIT rows, with `last_seen_ts` and `vouched`. |
| `rejects` | The rows the reader refused, truncated to 2,000 characters, with their reasons. |

## What it does not do

- It makes no match to another venue, calls no model, reads no price from another venue and makes no paper trade.
  Those are later slices, after the forward document is written and committed. Their tables are not created here.
- It neither trades nor needs a credential.
- It does not run itself.
