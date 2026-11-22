# Valedia master list

Every open to-do, owed check, parked item, owner decision and uncommitted idea for Valedia as a whole, in one
place. Asked for by the owner on 2026-11-17 so that nothing is forgotten. `docs/ROADMAP.md` stays the plan of
record (what is built in what order); this list is the exhaustive backlog behind it.

**First written 2026-11-17 (PST afternoon).** Sources swept: the roadmap (all 60 rows), the work ledger, the
ticket folder, the docs, the lead's notes, and the code of the learning loop.
**Known gap in this first sweep:** about 190 very long ledger lines and most of the 445 ticket files were
matched by keyword only, not read one by one. Item H-1 closes that.

## How this list is kept

1. Read it at the start of every session, before proposing work.
2. Update it in the same turn whenever an item closes, the owner decides something, an idea comes up (his or
   the lead's, even if it will not be done soon), a test passes or fails, or a follow-up ticket is written.
3. Nothing is deleted silently: a closed, dropped or superseded item moves to the closed log with a date and
   one line of why.
4. A failed or passed test adds its lesson to section 7 here AND to the lesson store the idea-proposing agents
   read (see L-6: that second half is not automatic yet).
5. Status words: **NOW** (being worked today), **NEXT** (queued behind NOW, nothing else needed),
   **QUEUED** (accepted, not scheduled), **OWNER** (waits for the owner), **BLOCKED** (waits for something
   named), **WATCH** (running; check on the date given), **IDEA** (not committed to). A status taken from the
   roadmap without a fresh check says "(roadmap)": the roadmap's rows were last refreshed 2026-11-14.

---

## 0. Priority set by the owner (2026-11-21, "Yes, start")

1. The fund book's live path, section 2 (S4c, S4d, S4e, T51 build B, the stricter go-live gate), then the
   practice sightings in market hours from Mon 2026-11-23. Earliest first attended real order: the week of
   2026-11-30.
2. His items in parallel: stake, live key file, the three files, a yes for one off-family review.
3. Prediction markets: the forward copy test's wallet screen, so the 120-day clock starts; then T45-2.
   **DONE 2026-11-21 1:57 AM PST: the test does not start.** Screen (T55a, main 9749743, blind check PASS) run on
   the real store: cutoff end of 2026-10, W = 2025-11..2026-10, 189 wallets screened (3 not, page limit), **0
   eligible** (fewer than 5 = the test does not start, by the locked rule). Most-failed lines: 9 of 12 counted
   months (147), 20+ shares per dollar (87), both outcomes in over 10% (71), 100 closed (71). Report
   `~/valedia-data/polymarket/forward-copy-screen/copy_screen_report.json` (sha256 d8b93f16…). The rule is locked:
   no re-screen with looser lines. Copying wallets is closed as a route; the owner allowed more backtests.
   Fund book: **S4c MERGED** (main 8908c4d, blind check PASS_WITH_NOTES); rulings R32 to R34 in
   `.foreman/tickets/T51c-S4c2-go-live-counts-shortfall.md` (R34 goes to S4e). **S4c-2 MERGED** (main e375fc3,
   two blind checks). **Practice gate moved** to e375fc3; the lead wrote the practice book's opening entry (R30:
   cash_in 1,001,229.06, book value 996,749.85 = Friday's paper equity); the practice job passes `--shared` from
   **Mon 2026-11-23 6:55 AM PST**, the first shared practice run. **S4d MERGED** (main 51d2641 + docs 733137c, 2026-11-21; seven repair
   rounds, last blind check PASS_WITH_NOTES): live is always shared, lock 1 on the book's upper value, the
   state file's value history is checked against the journal before any account read, and
   `order-step --live --accept-history DAY` clears a lost summary only when the row tightens (a lower row is the
   lead's by hand). **S4e MERGED** (main 169d4d8..75a51f5, 2026-11-21; four repair rounds, last blind check
   PASS_WITH_NOTES): shared stop and shortfall alerts per venue and class (unattended critical), cleared only by a
   strictly later clean run; the owner's page "Shared book" section; R34's `mark` entry. **Practice gate moved** to
   75a51f5 (backup `.before-s4e`, tested both ways). OWNER: try a `mark` entry on a COPY of the practice state file
   first (the first one rebuilds a layout-2 file in place). Small ticket MERGED (main 3b83960,
   f5daa44, blind check PASS_WITH_NOTES): test_fractional_off_against_base re-pinned to 75a51f5 and green (it now
   guards build B: fractional off writes what the accepted order path writes); alerts.py drops its cached shared
   view in each watch. T51 build B MERGED 2026-11-21 (main 4db3192..1d60247; four blind checks, the last
   PASS_WITH_NOTES; the safety property held in every round). Live fractional remainder legs need: the practice
   proof (one remainder purchase and one sale, clean), the owner's `"fractional_live_consent": true` in his live
   confirmation file, `--fractional`, and a shared live run; one live remainder per run until one reconciles
   cleanly. With the opening shut, a live run is exactly the run without the flag. **Practice gate moved** to
   1d60247 (backup `.before-t51b`, tested both ways). Follow-ups in `.foreman/tickets/T51d-build-b-followups.md`
   (docs wording, three test gaps, the doctor's consent-key count). Practice proof CHECKED 2026-11-21 on a copy of
   the real record: 2 clean MARKET remainder purchases, 1 clean sale, ok (B1 met). NEXT: the stricter go-live gate. AFTER BUILD B (owner's yes 2026-11-21):
   move the MiroFish-Offline backend files the native pipeline pins (`src/mirofish_native_run.py` ARCHIVE, now in
   `/Volumes/NIMROD_SSD/backups/2026-10-22/home/mirofish-offline/backend`) into this repo with its AGPL-3.0 notice,
   re-hash the pins, re-run the qualification tests.
4. Hermes, Paperclip and Live-page work PARKED until 1 is done (each by name: elephant-code-mode follow-ups,
   Paperclip seats' daytime test VAL-19, the Mission tab's nightly refresh, the Engineer's key file wiring).

No prediction-market strategy has passed; no real event order until one does.

## 1. In flight today (2026-11-17)

- **DONE 2026-11-17 20:30 PST** T51 fractional orders, build A (practice path only; live fractions stay
  refused): four slices, one blind check, merged (main `5d944d4`), 2,601 tests pass on main, the order-step
  job gate moved. **NEXT** the practice proof in market hours, then build B (section 2).
- **CLOSED, FAILED 2026-11-18 9:01 PM PST** T47 copying Polymarket's leaders (historical test). The development
  stage was run once more on the finished data (the fifth amendment's one extra run; the first run had 0 trades):
  1,718 copied trades from 1,175 events; mean MINUS 1.86 cents a contract at the rule's cost (3 cents); one-sided
  p 0.9265; failed: mean not above zero, p above 0.05. By the locked document the idea FAILS at step 1 and the
  sealed months (July to October 2026) are NOT opened, now never by this test. Information tables, which decide
  nothing: cost 0 gives +1.14 cents, cost 2 gives -0.86, cost 4 gives -2.86; the leaders' own prices give +2.26
  (the follower's later price takes about 4 cents); sports -2.10 cents (1,536 trades), everything else +0.13 (182
  trades); first purchases over 10,000 dollars -0.26 (239 trades); a 6-hour delay -2.50, 24-hour -2.62; the 10-minute
  figure had no trades (the price grain is 3 hours). The lead's stated expectation (between -4 cents and +1) held.
  Ran 12:45 to 1:13 PM and 8:34 to 9:01 PM, main 312bfa4, the three page-limit wallets left out. History of the
  entry follows. (Was: **NOW**) phase 1 read 189 of 192 wallets (35.3 million
  trade rows, a 69.7 GB store) and ended INCOMPLETE on 2026-11-18 01:15 PST: 4,705 market ids in the trades
  are one byte short and the service refuses any request holding one. Fourth amendment (a refused id is a
  market without a known resolution, counted) and T47e (the pull narrows a refused request and goes on)
  merged 2026-11-18 03:05 PST (main `cdd5371`); the pull resumed 03:12, answered 119 of the 427 refused
  requests (every refused id so far is a short one) and was stopped at 03:41 by the nightly backup's lock on
  its store. THE STORE NOW LIVES ON THE INTERNAL DISK, `~/valedia-data/polymarket/copy_leaders.sqlite` (a
  proven copy; the phase-1 original is a cold spare at `/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-data/`):
  off the collector's SSD and out of the folders the nightly backup takes. Resumed again 07:38 under the
  lead's wrapper and a guard that stops it if the collector shows strain. Three wallets with more than five
  million trades each are left out by name, as the locked rule says. THE PULL IS COMPLETE (2026-11-18
  11:59 PST: 35.3 million trades, a resolution answer for every one of 962,935 markets, 11,228 price
  windows). FIRST STAGE RUN 13:13 PST: **FAIL by the letter, with ZERO trades**: no development month has a
  leader, because 467,532 of 958,230 resolution rows are in an OLDER SHAPE of the service with no resolution
  time and no payouts (everything resolved before about mid-2025), which the reader stores but does not
  interpret. The sealed months are NOT opened. This is NOT evidence about copy-trading either way; the lead
  did not measure that field before writing the rule. **DONE 2026-11-18 13:34 PST** the FIFTH amendment (main
  `f40bc41`), written before any trade was analysed (none has been): measured, those rows are the ORACLE'S OWN
  record (a price, a status, a last update time; present from 2022 to 2026, not only old ones), checked on 14
  known markets (the 2024 election and the 2025 Super Bowl among them): status `resolved` with a price of 1, 0
  or one half is a known resolution at its last update time; anything else is not. T47f MERGED 2026-11-18
  15:36 PST (main `174fb9f`; its blind check showed 22 worlds built in each shape, and half and half, give
  the same leaders, trades, prices and result). THE REAL STORE READ AGAIN 15:41 (12 seconds, no request):
  439,247 more markets have a known resolution (126,605 first outcome, 311,370 second, 1,272 half; 28,285
  not resolved by their status), every count equal to the lead's profile. **NOW** phase 2 with the plan made
  again: 7,670 positions (2,877 before), 15,546 price windows to read, about one a second, under a guard
  that stops it at the first collector decision not ok (it did, at 16:53, and was restarted 16:54: it plans
  again for half an hour, then reads on). **NEXT** the first stage ONCE more, stated beside the first run.
  The sealed months stay unopened unless that passes. The same reading feeds the
  consistency screen of the owner's method below. Roadmap 1.26. **QUEUED** T47d items 8 to 12 (among
  them: a reader of the store must not end the pull; the pull's start must not read the store at full speed
  beside the collector).
- **NOW** T47c (the analysis in bounded memory): passed its blind check and is on main (`0172b3f`,
  2026-11-17 15:21 PST).
- **DONE 2026-11-17 20:38 PST** T52 the leaders' record: merged (main `bc4e8eb`) after three blind checks; the
  ten leaders FIXED (five Sports, five Crypto, none on both) and the first pass taken as the baseline in
  `artifacts/leaders_monitor/leaders.sqlite`: 9 wallets read, 1,942 open positions; two of the Sports leaders
  hold nothing open; Sports #2 holds more than 20,000 open positions and is past the page limit (it stays in
  the list as unread; a wallet of that size is a market maker). Each later pass records what is new, grown,
  shrunk or gone. T52b (a pass takes its lock before it checks the store; the store's whole shape is checked;
  a wallet past the page limit no longer reads as a failed day; nothing is created where no record is)
  MERGED 2026-11-18 13:54 PST (main `ad07778`, two blind checks, 129 tests on main). THE DAILY JOB the owner
  allowed on 2026-11-18 ("Yes, daily job") IS INSTALLED (13:58 PST, its schedule told to him first): every
  day 05:20 local, one pass of about 20 seconds, `com.valedia.leaders.daily`, log
  `~/Library/Logs/valedia-leaders-daily.log`; skipped unless the six project files a pass loads equal the
  revision in `valedia-deploy/accepted-leaders-commit`. Pass 2 (the job's script by hand, 13:55): in 17
  hours 56 new positions, 53 grown, 33 shrunk, 52 gone. T52c (the record can be fixed from a FILE of
  wallets into a store of its own, with no request: the door for the forward test's screened set) MERGED
  2026-11-18 15:30 PST (main `0cac9ab`, one blind check, 147 tests on main; the job's gate moved to it).
  **WATCH** the first scheduled run on 19 Nov; after T47f merges (it changes two of the six files) the lead
  re-tests and moves that gate, or the job skips.
- **NEXT** THE OWNER'S COPY-TRADING METHOD (his video, 2026-11-18; analysis in `~/Downloads/Copytrade_analysis.md`):
  from Polymarket's board pick CONSISTENT, low-volatility leaders (not the top by profit), check their closed
  positions for wins, copy what they hold NOW, place it on Webull, repeat daily. What exists: the boards, 189
  wallets' full histories, the daily-able record of open positions. What is missing, as its own FORWARD
  paper test: (1) a consistency screen from the store (variability of monthly realized profit, drawdown,
  share of resolved positions won, a floor on resolved positions, machines out); (2) the record pointed at
  the wallets it picks; (3) each open position matched to a Webull contract with the same resolution rule;
  (4) paper copies under a rule locked BEFORE the first copy; (5) its result against a bar stated in that
  locked document. Real orders only after it passes, the event order step (T45-2) and his event limits.
  **DONE 2026-11-18 14:02 PST** T53 step 0, a study of ONE day (report
  `claude-tmp/valedia-trader1/t53-scratch/step0-report.md`; the lead re-checked its main counts). AS SHOWN,
  THE METHOD CARRIES LITTLE ONTO WEBULL: of the ten fixed leaders' 274 live positions (3.0 million dollars),
  165 have no Kalshi market at all (and Webull lists only Kalshi's); the 10 game positions had resolved
  before a read 17 hours later (a daily pass cannot copy a game); 85 crypto price positions have a NEAR
  market with another price source and tie rule, and 3 of those pairs have ALREADY resolved differently on
  the two venues; 14 (one wallet's season futures and one politics question) look like the same event.
  Verified same rule: none yet (Polymarket's rule texts sit on a host the reader may not ask). Webull's
  listing is verified for 1 of the 28 matched series. The leaders disagree: 45 of the 69 markets held by
  more than one of them have leaders on opposite sides. Kalshi's ask was at or below the leader's own entry
  for 37 of 91. Three of the ten fixed leaders are the multi-million-trade machines. Cost on Webull: 0.02 a
  contract a trade, so a copy held to the result needs a chance above its ask plus 0.02. One day's prices:
  not evidence of profit or loss. **DONE 2026-11-18 14:06 PST** THE FORWARD TEST IS LOCKED
  (`docs/research/2026-11-18-forward-copy-on-webull-prediction.md`, main `58754c7`, written before any
  wallet's results were read): up to 20 wallets picked ONCE by a fixed screen over the 189 histories
  (consistent months, machines out, wins above the prices paid); a copy only where the position has a
  SAME-rule Kalshi market in a series Webull lists as tradable and the picked wallets do not sit on both
  sides; one contract on paper at Kalshi's ask; closed at settlement or when the wallet leaves; 0.02 a
  contract a trade; ONE read at 200 closed copies from 100 events, no result computed before; ended as "not
  testable at this scale" after 120 days. **DONE** the rule map's first four series (both venues' rule
  texts read: `forward-copy-rule-map.md`): NBA game winner, NBA spread, Premier League champion, 2028
  Democratic nomination. **DONE 15:26 PST** Webull's listing probed (`forward-copy-listing-table.md`, main
  `e312446`): NBA spreads 396 of 396 tradable, Premier League 20 of 20, also the NBA champion, the Super
  Bowl and two yearly crypto series; the nomination series is listed but NOT tradable (no copy); so three
  series can be copied today: NBA winners, NBA spreads, the Premier League champion. **DONE** the record's
  door for a chosen list of wallets (T52c). **NOW** T54a the paper copier (the matcher and its book, a slow
  Kalshi reader): built; three blind checks (V217, V219, V220) failed on narrower and narrower points; the
  third repair (895ca7e) was PARKED unchecked on the owner's token instruction. 2026-11-18 about 7 PM PST:
  read by Grok 4.7 and ChatGPT/Codex through his own seats (scrubbed bundle, no Claude tokens; 43 findings);
  the lead read each and confirmed six with gate tests (a finalized market with an unreadable payout sold at
  the bid; a clock set back; a malformed nested list crashing the step; an infinite pause; no lock between
  two steps; one case of an unclear read of another wallet). Two repair rounds with a Sonnet builder (R61 to
  R76: 212K and 273K tokens, against millions for the Opus rounds) and a third off-family reading: **MERGED
  2026-11-18 about 8:12 PM, main 063e87d** (= the checked candidate 804fd55; 16 new files, nothing existing
  changed; 140 tests pass on main through the gate). Accepted on a stop rule: no rule violation reachable from
  real inputs confirmed; the rest is stated limits in docs/forward-copier.md. It still runs on nothing real until
  the wallet screen exists and the owner runs `step`. **QUEUED (cheap, fail closed, from the third reading):** a
  Polymarket answer of "no such market" at an exit must hold the sale and retry (today it sells at the bid); the
  token's outcome word must agree with the Gamma outcome at its index (also on a cached description); one guard per
  unit so no odd shape can stop a step; duplicate todo rows by (wallet, read, token); a book creation killed in
  the middle leaves a file `step` refuses (delete it); limits at a faster cadence than one pass a day: a deferred
  holding or an exit seen late can be priced from a later read than the first after its pass. **NEXT** the screen's program (T47f is merged: it can start), then the
  screen ONCE (T47 closed on 18 Nov, so the screen may run now; the historical test's FAIL is a prior against this forward test, which stays locked as written), the record pointed at its set, the
  copier beside each pass, more series read into the rule map (the other sports first).
  **OWNER** whether the leaders' pass runs more often than once a day (game positions resolve within the
  day; a change to his job's schedule is his to allow; asked 2026-11-18 14:15 PST).
- **WATCH** the order-book collector (roadmap 1.16). 2026-11-18, the last development day: 33 decisions lost,
  3 at 03:14 (the pull's start read its 70 GB store on the collector's SSD) and 30 from 07:20 to 07:33 (the
  LEAD's own copy of that store off the SSD at 100 MB/s made the collector overrun whole minutes). What is
  now known: sustained disk load on that SSD stalls the collector's cycle; a long test file alone or beside a
  slow backup read does not. RULES for the scored period (from 2026-11-19 00:00 UTC): ONE test activity at a
  time; no kill or race suites; no large file work on NIMROD_SSD without the lead's guard
  (`claude-tmp/valedia-trader1/lead-pull/guard.py`, which stops the load at the first late decision); big
  research data off that SSD. **DONE 2026-11-18 12:45 PST** the controlled comparison the owner approved: two
  test activities overlapping with their scratch on NIMROD_SSD cost 3 of 9 decisions inside three and a half
  minutes; the same overlap with scratch on the internal disk, four arms and 16 minutes with the kill suite
  among them, cost 0 of 39. THE RULE NOW: all test scratch on the internal disk, and every test run of every
  session through the machine-wide gate `~/valedia-data/bin/test-slot` (two activities at once since 13:25
  PST, after the owner asked for parallel work instead of time slots, THREE since 15:41 after an eight-minute
  arm of three at once cost 0 of 27 decisions; one run a name, 20-minute cap, a shared log at
  `~/valedia-data/locks/test-activity.log`; it passes a test's error output through since 14:16). Today's losses: 36 decisions (3 the pull's start, 30 the
  lead's copy, 3 the comparison's SSD arm) and one `model_unavailable` at 13:03 (cause not known), all in the
  development period. NEW 13:49 PST: the collector reads Kalshi's public data host, and a study worker's 86
  requests to that host in one minute (the project's own reader at its 3 a second) sat one cycle before the
  collector's cycle slipped 11 seconds; no decision lost, but the loop keeps the later phase for hours and a
  cycle that starts with under 12 seconds left loses its decision. One event, suspected. RULE: every
  ticket's network hosts are checked against the collector's; until the scored period ends a second Kalshi
  reader on this machine runs far below that pace, set by measurement under the guard (this binds the
  forward copy test's daily matching).
- **NEXT** the nightly backup (03:40) must not take very large research stores: with the 70 GB store in
  `artifacts/` its copy held a read lock for 21 minutes and its integrity check of the copy ran for hours.
  The store has left those folders; the lead checks that today's backup ends and tomorrow's is ordinary.

## 2. Path to the first real order of the fund book (in order)

The owner said yes on 2026-11-17 to preparing the fund-holding book (fifteen funds, inverse-volatility sizes,
reset every 21 sessions) at a stake smaller than whole shares. Webull has approved his app for live API
trading (his word, 2026-11-17).

1. **DONE 2026-11-17** T51 build A: blind check passed with notes, merged, the order-path job gate moved. The
   first scheduled practice run on it (06:55 PST) upgrades the practice ledger's layout; both real ledgers are
   backed up in the deploy folder.
2. **DONE 2026-11-18 (three of four seen)** The practice proof on the paper account, through the guarded
   order step: two fractional MARKET purchases accepted and filled inside their collars (QQQ 0.68938 at
   661.11, SPY 0.43859 at 732.64); fills and holdings come back with the five decimals sent; a fractional
   MARKET sale accepted (SPY 0.31095; its fill is read at the next reconcile); a whole-share sale filled
   (XLK). Not seen, by design: a closing sale under 5 dollars (the step holds it back). Also seen: no fee
   is reported on a purchase and 0.02 on each sale; the venue's holding changes before the order's status
   says filled, and the next reconcile settles it. MEASURED the same morning (375 readings of the order step's
   own quote for the fifteen funds over twenty minutes): 370 within the 120 s limit, median age 39 s; only
   DBC (thinly traded) missed, 5 times in 25. The remainder leg of the XLK sale was held back for another
   reason: it followed its own whole-share leg and was judged on the quote asked for before that leg went.
   **For build B:** the remainder leg asks once more for its own quote right before it is judged.
3. **MERGED 2026-11-21 (1d60247)** T51 build B: the proof reading (only fills of this code path count; one purchase and one sale in
   clean batches), the owner's consent field for live fractions, the live opening, and the FIRST-CONTACT RULE
   (in production one fractional order per run until one has filled and reconciled cleanly: practice is looser
   than production, so practice cannot prove what production refuses).
4. **NEXT** T51c book-level accounting in a SHARED account. The owner trades the same cash account by hand
   and holds event positions in it. STUDY DONE 2026-11-18 (`.foreman/tickets/T51c-shared-account-book-step0.md`,
   rulings in `T51c-addendum-1-rulings-and-slices.md`): today's code would STOP the book at every run after
   any trade of his (not merely log a fault), judge her position limit on the whole account's equity, read
   his withdrawals as her losses and let a deposit hide one. Decided shape: a shared mode of the order step,
   always on for live; her book is her own fills; her capital is the owner's stake entries plus her results;
   every limit on her book; positions checked one way (his surplus is his; an account holding LESS than her
   book stops her until he says he took the shares); she never spends the day's own sale proceeds or more
   than the account's settled cash. SLICES S1 TO S3 BUILT 2026-11-18 (the one-way position check as an
   option, her book's own figures, the record of the owner's entries with his command; nothing wired): four
   blind checks and a fifth on one last point (two failed on the record of entries and on refusals that
   wrote to disk, then three passed with notes). MERGED 2026-11-18 11:07 PST (main `46b398d`); sixteen test
   files pass on main (3,626 tests); the order-path gate moved to it; the real practice state files are
   backed up in the deploy folder (the first run on the new gate, 19 Nov 06:55, upgrades the state file's
   layout; code before this build then refuses it). The owner's command refuses a live venue's name until
   live entries open with `--live`. S4 in slices (rulings in
   `.foreman/tickets/T51c-addendum-7-S4-rulings.md`). S4a THE FEE RULE MERGED 2026-11-18 about 13:30 PST
   (main `599ae5b`; two blind checks; sixteen test files, 3,934 tests, pass on main; the order-path gate
   moved to it at 13:54): a purchase's fee the venue leaves empty is bounded by Webull's published rate
   while the order can still be rechecked and taken as none after, so the book can be valued after its
   first purchase. S4b THE OWNER'S COMMAND, WIRED, MERGED 2026-11-18 16:20 PST (main `c6e2007`; three blind
   checks): it takes its marks from the quote path, never typed; it shows a preview with a code and writes
   only on a confirm inside a band; it judges the entry again under the lock (the first check found a fill
   arriving in between was left out of the valuation); it can repair an entry whose journal line failed.
   R33a MERGED 16:28 PST (main `ff3e0bc`; one blind check, then two older tests taught the change): the
   adapter says when a fee could not be read and the ledger keeps it as a notice that blocks nothing. THE
   LEAD'S TWENTY FILES pass on main (5,421 tests) and THE ORDER-PATH GATE MOVED to `ff3e0bc` at 16:53.
   **WATCH 19 Nov 06:55** the first practice run on it: the state file moves to its new layout (code before
   T51c then refuses it; backups in the deploy folder), and the ledger is read for a `fee_unreadable` notice
   on an ordinary purchase (it must not open one; code before R33a would treat such a notice as blocking).
   **NEXT** S4c practice shared mode (the bound left open for an unread fee; the practice book's opening
   entry by the lead through the new command), S4d live, S4e alerts and the owner's page. What the record
   catches is written in `docs/WEBULL.md`: a change to the state file OR to the journal is found; both
   rewritten to match are not. FOR S4, found on the way: Webull reports no fee on a purchase (the book then
   calls the fee unknown: a rule is needed); the marks of an entry must come from the order step's quote
   path, not be typed; where the live book's entries go; no repair path yet when a journal line fails.
   **DECIDED by the owner 2026-11-18 ("Yes, hers only"):** her position limit and her count of positions are
   judged on her own book alone; his holdings of the same fund never refuse or allow an order of hers; a
   line on the owner's page shows the combined holding per fund as information (S4e). Known gaps of
   the first build: fund distributions on her shares; the venue's order history read (before unattended mode
   or any step up of the stake); a per-fund hold instead of a whole-book stop. PRECONDITION of the first
   real order. Until S4 runs on practice, a hand order in the PRACTICE account would stop the practice runs.
5. **OWNER** his live key pair in `~/.hermes/secrets/webull-live.env` (two lines, mode 600, placed by him).
6. **OWNER** his three files: the real-money confirmation (`config/webull_live.json`), the go-live figures
   (`config/go_live.json`), a daily order cap in `config/risk.json`. The numbers are his. A full rebalance is
   up to 30 orders.
7. **OWNER** his stake. Facts: every fund clears Webull's 5-dollar minimum from about 120 dollars; at 1,000
   dollars the split leaves 0.3% uninvested against 66% with whole shares only.
8. **OWNER** a review of the order path by another model family (Grok or Codex through Hermes): needs his yes
   for each run. Roadmap 5.4.
9. **QUEUED** go-live gate reads the paper record without looking at faults or order type: tighten it (part
   of build B). Paper record today: 2 completed paper broker days, 17 fills, none fractional.
10. **QUEUED** still to be SEEN on the practice account (roadmap 4.2, `docs/WEBULL.md`): a sale, a partial
    fill, an unfilled day order at the close, a cancel answered "not yet", one clean week.
11. **OWNER** the first order itself: his word at the time; live commands are run by him at a terminal.

After a first attended order:
- **QUEUED** T34b-4 the stake ladder in use (stake from the ladder, doubling after clean sessions, halving
  after a fault), unattended runs, the live kill switch for a venue and its reset (roadmap 5.9, 4.5).
- **OWNER** unattended from the first order, or after a proving run of ticket approvals (asked 2026-11-14).
- **QUEUED** T22d-1b: notes collected before real money (from checks V105 to V118), to be cut into a ticket.
- **QUEUED** T50b: what is left of the live-key separation (event practice's own refusal naming a live name).
- **QUEUED** daily reconcile joined to the trader's own book, cash compared (roadmap 4.3); splits and
  dividends (4.4).
- **QUEUED** second price source: its delay inside the session is unmeasured and one source's terms unknown;
  not yet fit to guard real orders alone (roadmap 5.2, T28).

## 3. Prediction markets on Webull (the owner's first wish)

No strategy has passed for prediction markets yet (section 7). The order side is being built so that a
passing idea can trade at once.

- **QUEUED** T45-2 the event order step: sizing, risk limits, wiring from a passed strategy (roadmap 4.7).
- **OWNER** risk limits for event contracts (his numbers).
- **QUEUED** T45-3 settlement follow-ups: a third witness, the ledger trace for an unreadable fee, a zero fee
  stored, a named refusal for a malformed status, settlements on the owner's page and in the tax readers.
- **NEXT** (lead) back up the real Events state file, then one reconcile run on the practice account.
- **OWNER** the ATL practice contract (sold by hand, or his confirmation of what happened to it).
- **QUEUED** `docs/WEBULL.md` line 276 still says event orders are not built: stale.
- **IDEA** the live path for event orders in the shared account needs the same book-level accounting as T51c.

## 4. Finding what makes money (tests and ideas)

In flight:
- **NOW** copying leaders, historical (T47, roadmap 1.26); then T47d follow-ups (the final test must refuse
  cleanly where it cannot write; a missing boundary test; where memory is read; count the real store's
  markets before the analysis).
- **WATCH** order-book loop with Elephant-1 (1.16): first fit not before 2026-11-19. **DONE 2026-11-19 1:33 AM
  PST:** the copy of the rows before 2026-11-19 00:00 UTC is in a fresh read-only file on the internal disk,
  `~/valedia-data/orderbook_loop_pre_scored_20261119.sqlite` (267 MB; sha256 beside it; counts equal to the
  source's per table: 17,923 decisions, 361,530 trades, 107,851 snapshots, 7,444 heartbeats, 300 markets, 286
  market drops, 87 events, 22 selections; no content read; paced in chunks under the collector's guard, 0 lost
  decisions). The lead still owes the leakage checker before 22 Nov. **Scored period so far (from 4 PM on 18
  Nov, read at 1:40 AM on 19 Nov): 1,626 decisions, 1,610 ok, 16 lost, all `model_unavailable`, 0 overruns:** 3
  at 4:53 and 4:56 PM (Bionic, the second LM Studio app, started at 4:52:47: "Local inference is changing
  ownership"; the collector reloaded `elephant-1` at 4:54:26), 2 at 7:08 PM (a burst of 4 network-change events;
  no ownership entry; cause unproven), 11 from 12:55 to 12:58 AM (the classic LM Studio app was started at
  12:54:51 and quit at 12:57:50, and local inference changed owner both times; the collector's own reload at
  12:59:18 ended it). The earlier blame on Tailscale and LM Link for the 4:53 loss was wrong or incomplete. The
  owner is asked to leave both LM Studio apps alone until 3 Dec.
  **Update 19 Nov, 9:10 PM:** the owner moved from Bionic to the classic LM Studio app at 8:49 PM; 33 more decisions were lost
  (23 `model_unavailable` while the app's server was on another port, 10 `tag_mismatch` until the collector's reload at 9:00:24);
  the scored period stands at 53 lost of 4,954; every OK decision carries the same model identity (elephant-1 q8_0, digest
  652c102967ef). The classic app runs with just-in-time loading ACTIVE: to be turned off.
  **Update 19 Nov, 9:25 PM:** the owner tried to give `elephant-1` 4 parallel slots (a second instance): 7 more decisions lost
  (about 60 in all). The collector reloads its own instance with parallel 1, context 8192 on any missing or mismatched model;
  those settings are the locked test's and stay until 3 Dec. He also loaded `ternary-bonsai-2-27b` locally (8.26 GB, 200K context).
  **Test 19 Nov, 9:31 PM (the owner asked for 4 parallel):** a separately named 4-parallel instance, loaded for about 30 s in the quiet part of
  a minute, cost the collector nothing and routes exactly, but gives NO speed-up (4 requests took 4 times one) and its probabilities differ
  from the 1-parallel instance's by up to 0.057 on identical input: it cannot replace the locked instance. Unloaded again.
  **Shared copy for other processes (owner, 20 Nov, 1:07 AM): `elephant-1-trader` is LOADED and verified.** A byte-identical copy (APFS clone,
  no extra disk) in LM Studio's library under `debackerl/elephant-1-trader-Q8_0-GGUF/`; the app indexed it after "Refresh model list" in
  Library, models-directory menu (its file watcher misses the symlinked models folder). Loaded by REST at second 39 of a minute with
  context 8192, parallel 1, physical and eval batch 2048 (9.7 s). On the same input `elephant-1` and `elephant-1-trader` give IDENTICAL
  answers (0.000000 probability difference) at 646, 1,159 and 5,605 input tokens, each deterministic run to run. The collector lost nothing
  during the test (5 of 5 ok, 3.82 to 3.87 s against a 3.86 s median, 0 overruns). Idle GPU 0 to 8 percent with the collector's usual spikes;
  system memory free fell from 55 to 32 percent. Other processes name `model: elephant-1-trader` (separate key: requests for `elephant-1`
  can never reach it); they own the speed they cause (a 5,605-token state takes about 5 s) and must not load a second `elephant-1`.
- **WATCH** public pick lists as a forecaster (1.19): grading waits for the hand audit of 40 picks; test line
  12 Dec.
- **WATCH** Reddit chatter against Kalshi prices (1.20): 7 days of hand audit, then 28 days; no evaluation
  code written yet.
- **WATCH** forecast tournament (1.1, 1.2): local models and Sonnet not better than the base rate so far;
  daily forward questions since 16 Nov, only three forecasters asked.

Queued (roadmap):
- 1.3 prediction-market questions in the tournament; 1.4 MiroFish and the Scout as forecasters; 1.5 the
  winning forecaster earns weight in decisions; 1.7 crypto history with dead coins; 1.8 a richer rule library
  (rank many assets, outside-data rules, shorts); 1.9 new idea families (carry, options premium, events,
  pairs, intraday); 1.10 capital split across passed strategies; 1.11 costs by venue; 1.14 closing-line value
  as a skill measure; 1.15 netting inside the paper book; 1.17 copy-trading insiders and politicians (needs
  1.6); 1.21 Finnhub company facts on the wide tier (T36b: about 3,000 stocks, dated backfill, event refresh).
- **OWNER** 1.6 stock data that includes dead companies (paid, 20 to 55 dollars a month).
- **OWNER** 1.22 the outside trading project (slices T38-3b to T38-10): his venue, forex broker and margin
  choices. T38-1 pairs has had no review by another model family (asked, unanswered).
- **QUEUED** 1.23 the openInvest methods (c) and (d); T39-2c (three readers that present a pass by its status
  alone; ticket not yet written); T39-2d (the gate's remaining unguarded figures; ticket written).
- **QUEUED** the Deflated Sharpe recount across all tries (owed since T22).
- **QUEUED** 1.13 leftovers: the "lock" rule and four other cities were never tested (weather).
- **QUEUED** shadow books for forward candidates, judged against the fund book by a paired test (agreed plan,
  not built).
- **QUEUED** T36c a facts test that fails about one run in four (same before and after T50).

Ideas not committed to:
- **IDEA** a leader's position as ONE input among others (the locked T47 document says a fail of plain
  copying says nothing about that).
- **IDEA** the gap between Kalshi's and Polymarket's price for the same question, once T52 stores both.
- **IDEA** a fee screen for event contracts: at 2 cents a contract, list which price ranges and holding
  periods could carry any edge at all before testing more ideas there.
- **IDEA** the price-range idea from the starred-repos audit as a swarm seed (blocked until MiroFish has one
  completed scored run).
- **IDEA** TimesFM 3.0 weights are non-commercial; the owner has not ruled on personal use of them.

## 5. Data collection

- **NEXT** T52 the leaders record (above). Later slices: rule match to Kalshi's public markets (Webull lists
  Kalshi's contracts: verified for five sports series only, never for crypto), the entry price at first
  sight, the forward document locked by the lead BEFORE any paper trade.
- **DONE 2026-11-18** the repeating job for the leaders' record: allowed by the owner, installed (section 1).
- **QUEUED** Elephant-1 as the judge of "the same question": not while the collector's test runs (it serves
  the collector with one slot). Rule survivors are stored and judged later in one batch.
- **QUEUED** the Odds API: 16,730 credits left; every use goes through the credit ledger. The sportsbook
  consensus test already failed (1.18): do not repeat it.
- **QUEUED** Finnhub: planner for the 3,000-symbol tier, four-counter pacing, a week's growth measurement.
- **QUEUED** one memory of every decision: the stores are not joined (roadmap 2.1).
- **OWNER** Reddit: the official app's id and secret stay unused until Reddit approves in writing; the public
  feed reader stays on. No anti-detection browser, ever.
- **QUEUED** T31 thread reader in the collector: ScrapeBadger is NOT used; rewrite or drop the ticket.
- **IDEA** a data-source register: each source with its terms, cost, what was measured about it by a real
  request, and the tests that already used it, so no finished test is proposed twice.

## 6. Analytics and decisions

- **QUEUED** forecaster weights from scores (1.5); MiroFish and Scout as forecasters (1.4).
- **QUEUED** T40-A2 model-name drift (decider, auditor and Valedia's chat run Sonnet, not local models);
  T40-A3, T40-B2, T40-C (calendar part).
- **QUEUED** the 27B panel member is disabled pending a memory map; weekly idea-proposing by it is unmeasured.
- **QUEUED** a labelled check set for any model used as a yes/no judge, before its answers count.
- **IDEA** a second small local model as the judge, so Elephant-1's single slot stays the collector's.
- **IDEA** every probability any model states is stored with its question and scored when it resolves (one
  calibration table across forecasters, panel members and judges).

## 7. The learning loop (recursive learning)

What loops today, found in code on 2026-11-17:
- result -> lesson: YES. A failed development or sealed test, a revision, the weekly critic and the
  skeptic's argument each write a lesson (`gauntlet.py`, `revision.py`, `critic.py`, `research.py`).
- lesson -> next idea: PARTLY. The proposing agents' prompt carries each tested idea with its status and the
  last 160 characters of its lesson (`research.py:282`). No summary by idea family; nothing stops a failing
  family from being proposed again.
- next idea -> test -> result: YES, by the `cycle` command (the weekly job, Sundays 18:00).
- a closed trade or a settled forecast -> lesson: NO code.
- a check that the critic's changes help: NO code.
- forecaster scores -> weight in decisions: NO code.

Items:
- **QUEUED** L-1 daily self-review (roadmap 2.2): each day's closed trades, settled forecasts, faults and
  held-back orders become lessons. The owner approved the daily job by name on 2026-11-14; not built. This is
  the "review and improvement agent" he asked about on 2026-11-17: a reviewer that PROPOSES (lessons and
  findings that become tickets), never one that patches guarded trading code by itself; code changes keep
  going through a builder, a blind check and the job gates.
- **IDEA** RuFlo (asked by the owner 2026-11-17 through the design session): not proven for coding here; its
  agents are Sonnet subagents and its prompts fetch a package at run time (to be blocked first). One
  supervised task end to end on something that is not the trader, then add only what that run shows missing.
  Trader coding stays on builder plus blind checker. Its `neural-trader` and `market-data` parts: a
  reading-only look for ideas, nothing run or copied.
- **DONE** L-0 (lead, read 2026-11-18 02:25 PST): the Sunday 18:00 research cycle asks the team file's
  proposer, skeptic and narrator, which are the Hermes profiles `valedia-trading-decider` and
  `valedia-trading-auditor`; both name Claude Sonnet on the subscription, not a local model. The cycle
  loads nothing beside the collector on 22 Nov. (The scout profile names the local 27B by its plain key; the
  cycle does not call it. Read again if the team file or a profile's model changes.)
- **QUEUED** L-11 a register of known coming events and expiries, read by the alerts check so that it warns
  days ahead (the end of the scored period, the fund book's 21-session resets, half-days, the real-money
  confirmation's 31-day expiry, fixed test dates, credit, credential and licence dates). No model. The first
  piece of "lookahead" (the lead's suggestion to the owner, 2026-11-17).
- **QUEUED** L-12 a pre-mortem with a prediction on every CHANGE: each ticket states what should be seen
  afterwards and when; the daily review checks it.
- **DECIDED by the owner (2026-11-17 late, as the design session relays his words "Yes, that split" and
  "Yes, build it"):** the master list is the one backlog; Hermes's Kanban is the work board; Paperclip is the
  approval desk; bots file proposals into a create-only inbox (one command, being built that night in the
  design session's repository: at most 5 proposals a bot and 20 in all a day, nothing it files can start a
  worker). Still his to decide: RuFlo's first supervised task; which bot and model do the daily review.
- The inbox command is built and blind-checked twice by that session (2026-11-17 23:30 PST, its report):
  `~/valedia-orchestration/bin/propose`, one JSON object with the keys bot, title, item, day, class, evidence,
  seen, proposed, prediction; `--dry-run` sends nothing; it files into Paperclip's backlog with no assignee.
  NOT USED YET: no real filing has been made. The daily review (L-1) is its first intended caller.
- **OWNER** a three-seat coding pipeline (planner, implementer, blind reviewer) built from Paperclip's own
  review and approval stages: a design in that session, with the lead's four changes; not built until he says.
- **UPDATE 2026-11-18 about 6:50 PM PST:** he said yes ("Yes, please route"; "i just want coding to happen ...
  mostly by local models ... checked by a good frontier model ... if I run out of Anthropic credits, it will
  just choose another") and set all four Paperclip agents to the Hermes harness himself. That session found
  they would all run as his default Hermes home (one model, approvals skipped, no walls): NOTHING assigned;
  it builds dedicated profiles, a launcher without `--yolo`, walls kept by the operating system, writer and
  checker of different families, failover that stops rather than merging unchecked. Paperclip Runner: its
  verdict so far is not to enable it (Hermes is not a Runner harness; its telemetry is unproven); report due.
  **Overnight run (owner asleep from about 2 AM, 20 Nov; lead's brief sent to the Paperclip session 1:55 AM):** baseline
  tag `pre-overnight-20261120` on the trader repo (af9ec61); branch-only (`overnight/<issue>`), no merges or pushes, nothing
  in the order path; implementer on `splash27` (the mini), planner and reviewer on gpt-6.2-sol, FreeLLMAPI `valedia-coder`
  for small tickets; at most 4 issues, 90 minutes each, one at a time; a collector watchdog (trip: any of the last 3
  decisions above 8 s, any not ok, or a new overrun or model_load event) pauses the agents and writes `run/overnight-STOP`
  until the lead clears it; the run starts only after the lead's "go" on the plan; report `run/overnight-20261120-report.md`.
  The lead reviews and merges by guarded cherry-pick; nothing from the night is accepted unseen.
  **RESULT (2:59 AM): HALTED, nothing completed, 0 model tokens, collector untouched.** Watchdog (rules a to f) built and proven both
  ways, still watching. The first Planner wake was refused by the session's own launcher (a SHA-256 revision digest looked like a key;
  fixed narrowly, merged `720e526`, blind-checked); then the harness DENIED re-assigning the issue (a peer's relay cannot stand in for
  the owner's yes to waking seats that talk to gpt-6.2-sol); the session did not work around it. **OWNER, one line each:** (1) yes to
  sending sandbox code (the pipeline's own tools and tests) to gpt-6.2-sol for the planner and reviewer seats, or a permission rule
  for it; (2) apply the `HERMES_HOME` sink (`cutover --apply`, merged in the orchestration repo, not applied) so Paperclip's skill
  sync stops linking `paperclip`, `complain`, `suggestion-box` into his default Hermes skills (the three links made 2:26 AM were
  removed 3:08 AM after a check, and come back on the next seat run until the sink is applied); the watchdog loop is stopped (no seat
  can run), restart with `bin/overnight-watchdog --every 30`; (3) leakage-checker definition is the lead's to write before a later
  night. Hand-over: `~/valedia-orchestration/run/overnight-20261120-report.md`.
  **UPDATE 10:30 AM, owner awake, audit asked:** the pipeline is running (VAL-10 re-assigned 10:17 AM; Planner wrote VAL-11; the Engineer
  runs on `splash27`; the `HERMES_HOME` sink is applied, his skills folder has no links). Models read from the Hermes session records
  (Paperclip itself shows "auto, unpriced"): Planner and Reviewer were both gpt-6.2-sol (ChatGPT plan), Engineer `splash27` on the mini.
  His wish is local except the Reviewer, so `seat-planner` was moved to `splash27` (same route as the implementer, no Grok fallback;
  backup `profiles/seat-planner/config.yaml.before-local-planner-20261120-102811`; `seat-hermes --check` 7 of 7; a probe plan took
  43 s and the collector stayed at 3.7 to 3.9 s). Open: the Reviewer's automatic fallback to Grok 4.7 (his call); the watchdog loop is
  stopped while seats run; 3 decisions lost 5:03 AM, cause unknown (nothing of the pipeline ran then).
- **OWNER** FreeLLMAPI as free hosted capacity for coding workhorses (his request of 2026-11-18 00:00 PST; a
  nested session on Opus 5.5 read the whole repository; nothing installed). Its picks, waiting for his
  answers there: a native run inside a file-access sandbox on 127.0.0.1, NOT Docker for now (the Mac is about
  11 GB into swap; a container VM would hold about 2 GB; Docker reconsidered after the scored test); the
  vendor's catalog feed OFF by default (when on it sends the address, the catalog version and the licence
  every 12 hours; never prompts or provider keys); a short list of providers whose free tiers do not keep
  prompts; a separate Hermes root for one chat-only coding profile with no memory, tools or fallback; a
  fail-closed filter in front of it; started by hand, no kept-alive job. He creates the accounts and enters
  the keys himself. Build window offered: 2026-11-18 09:45 to 11:00 PST, on the lead's word at the time.
  Whether Ollama's hosted service may be one of the providers is his to say (his "no Ollama" rule was made
  about local models).
- **UPDATE 2026-11-18 01:20 PST (that session's report):** the owner chose differently on the first point: he
  opened the vendor's signed, notarized menu-bar app himself (version 0.16.34, the commit that was read; it
  listens on 127.0.0.1:31415 only) and entered his premium licence himself. His providers: Groq, Cerebras,
  Cloudflare, and Ollama's hosted service by name, for FreeLLMAPI only. No build is needed any more. Left to
  do there (light, 09:45 to 11:00 PST): the guard code and its tests, the separate Hermes root for the one
  coding profile, one toy prompt. The app can read his files like any app he runs; that was his choice.
- **UPDATE 2026-11-18 about 6:57 PM PST (the lead's decision, on his word "decide and respond ... so it isnt
  waiting on my response"):** the simple wiring replaces the plan above. The existing `valedia-coder` profile
  is repointed to the fixed `coding` chain (zero-retention providers only; OVH and its no-key access out until
  its terms are read; no fallback either way); a key-leak hook only; trading code allowed on zero-retention
  providers (never keys, accounts, positions, tests' data, config, journal, memory, state); order-path or
  real-money code from a free model still gets one paid check; tool-enabled runs only through the
  operating-system wall the Paperclip session builds. His alone: the single key paste, the gateway restart, any
  download. Same evening he made etherion serve `hs27b` at 262144 context (2 parallel): a local Implementer seat
  is possible; first agent request to be watched against the collector's cycle.
  **DONE 7:38 PM:** he pasted the key and restarted the gateway; one `valedia-coder` request went through end to
  end (Cloudflare GLM-4.7-flash, first try). He kept OVH in the chain himself ("Gemini Carbon said OVH is ZDR");
  its terms are still unread by us (its public pages say no training, no storage; no contract seen).
- **QUEUED** L-2 lessons by idea family, and a stop on a family after N failures (roadmap 2.3).
- **QUEUED** L-3 a check on the critic itself (roadmap 2.5).
- **QUEUED** L-4 scores earn weight (roadmap 1.5).
- **QUEUED** L-5 the decay monitor acting on what it measures (roadmap 2.7; it cannot judge the fund book on
  so few days yet).
- **QUEUED** L-6 the locked tests' results (weather, sports lines, ask bands, Kronos, and each new one) are in
  `docs/research/` but not in the lesson store the proposing agents read: write each in, and make that a step
  of closing any locked test.
- **QUEUED** L-7 costs learned from fills: assumed cost against paid cost per venue, fed back into the cost
  model (with roadmap 1.11).
- **QUEUED** L-8 new models tried automatically against the tournament (roadmap 2.6).
- **QUEUED** L-9 this list reviewed and re-ranked weekly. As a job it needs the owner's yes; until then the
  lead does it at the start of a session.
- **IDEA** L-10 the builders' and checkers' own record (which kind of ticket failed its check and why) fed
  into how tickets are written; today it is kept in the crew record and read by hand.

Lessons so far (each is also a "do not repeat"):
- Weather contracts against the official forecast: failed (1.13). Sportsbook consensus against Kalshi on
  1,407 games: failed (1.18). Ask bands on Webull's prediction markets: failed (1.25). Kronos: failed (1.12).
- 21 early strategy ideas failed the gate; the fund-holding book (hypothesis 26) passed under the exposure
  rule: development Sharpe 0.75, sealed period +54.8%.
- The local models and Sonnet have not beaten the tournament's base rate.
- Process: more than two test-running agents beside the collector cost 24 late decisions; a finished test was
  nearly repeated because the research folder was not read first; a pull was started without a trial run and
  reached 6.9 GB.
- Process (2026-11-17 evening): inside a two-agent limit the collector still lost eleven decisions in an
  hour. The cause is NOT established. The three clusters share two test activities overlapping (in two of
  them one was a suite that kills processes at every database write); a 16-minute test file alone, and a loop
  of 130 short processes alone, cost none; two builders overlapping for hours earlier cost none. A candidate
  not yet tested: forced disk writes on the same SSD as the collector's store. **OWNER** a small controlled
  comparison before the scored period starts (2026-11-18 16:00 PST), to learn what rule the scored days need.
  Until it is known: one test activity at a time on this Mac during the scored test, no kill or race suites,
  and the collector's not-ok count read before and after every run.
- A 27B coding worker cannot share this GPU with the collector (section 11).

## 8. Autonomy

- **QUEUED** the stake ladder in use, unattended runs, the venue kill switch and its reset (section 2).
- **OWNER** the envelope: one ceiling, on or off, monthly renewal: his file. Inside it Valedia decides by rule.
- **QUEUED** phone alerts and the watchdog (roadmap 4.5, 5.3). **OWNER** the watchdog's schedule needs his yes.
- **QUEUED** both key pairs side by side so paper keeps running as the control beside live (the files are
  separated by T50; the switch itself is not built).

## 9. Rules, tax and safety

- **QUEUED** 3.3 per-order tax check and a wash-sale policy (a 21-session rebalance of fractional lots will
  create wash sales: the owner's and his adviser's to judge); 3.4 answers to tax questions on request; 3.5 day
  trade counts and position limits; 3.6 weekly re-read of rule sources; 3.7 tax-aware choices; 3.8 year-end
  reconciliation against broker forms. Four legal parameters of the tax-lot ledger are unverified (3.1).
- **QUEUED** 5.7 no closing kill switch; the daily order cap is the owner's number.
- **QUEUED** older `src/` scripts still send the owner's addresses to outside services (asked whether to
  change; no answer). The standing rule since: a generic client name only.

## 10. MiroFish

- **OWNER** the first load-only test of the operator (asked 2026-11-17): it unloads the resident 27B and does
  not reload it. Every launch needs him told first: start, length, each thing switched off, by name.
- **QUEUED** TMF6b operator follow-ups; the full staging input check has never been run.
- **QUEUED** run counts still to do: 10 runs at 9 participants and 3 at 48 (roadmap 1.24); a whole swarm has
  not yet been shown to complete beside the collector.
- **QUEUED** registry hardening of the native model module; the embedding refusal race (409); the 48-swarm
  budget; a 2,048-token profile defect; a real scoreable scenario; the qualification tool not unloading after
  a timeout; a silent fallback in the producer.
- **OWNER** two models by phase (needs his approval: pinned source); a proof run with the collector off; the
  LM Link guard (treats the Mac Mini's model as local); format qualification (loads a model); the old Zep
  key's revocation.

## 11. Valedia beyond the trader (Hermes, messaging, pages, skills)

- **2026-11-19 (read from the Hermes Apex session, 9:45 PM)** `elephant-code-mode`, a Hermes plugin built there (source `~/hermes-apex/plugins/elephant-code-mode`, installed in the DEFAULT profile only, enabled 6:40 PM on the owner's yes, NOT live until he restarts the gateway): a keyword router (no model) plus a risk gate that, in INTERACTIVE sessions only, asks the shared `elephant-1` (`/v1/systemone`, one question, about 600 tokens, one request in flight a process, 1.5 s budget) before a tool call that can change something; it checks `lms ps` for idle and queue 0 first and never loads or unloads. Config touched: plugins.enabled, tools.tool_search.defer, platform_toolsets.cli, one SOUL.md block (backups `.bak-elephant-20261119-184047`). The reducer plugin was never installed; that session built no email triage (whose it is: asked, unknown). It assumes parallel 1. Open: a race where an evicted instance meets a just-in-time-loading server (the classic app has it on). `/v1/systemone` was verified working on the classic app at 9:31 PM.
- **DONE 2026-11-19 2:41 AM PST** the "Mission" tab inside Hermes Desktop's Live page (the owner's ask: goals,
  to-dos, the path to live trading and the maps in one place, as a tab and not a new button). Built by the
  Paperclip session (page-only install, no gateway restart; reviewed by ChatGPT/Codex through the lead; hardened
  once). It shows the two tracks of the path to live trading, what waits on the owner, status figures, backlog
  counts, the seats' state and two generated maps (trading system; Hermes and orchestration, read from the config
  files by names only). REFRESH after any change of this list: `~/valedia-live/tools/refresh-mission.sh` (it runs
  `tools/mission_board_data.py`, `tools/hermes_topology.py`, `tools/mission_maps.py` of this repo first; the curated
  rows live in `tools/mission_board_data.py` and are edited by hand). A nightly refresh and a drift check (the map
  against the config, both ways) are NOT built: a repeating job needs his yes. Research report:
  `~/reports/Living architecture maps for Hermes.md` (no Hermes plugin draws a map from live config; our own
  generator is the route; no plugin installed).

Workers on one board (the owner's wish of 2026-11-17: Paperclip used cleanly with RuFlo, its work appearing
in Hermes's Kanban, the workers watchable on the Live page, coding done by the strongest local model that can
run beside MiroFish and Elephant-1):
- **OWNER** the design session's step 0 is done (2026-11-17, read-only, nothing installed). Its reported
  result, not yet checked by the trader's lead: Hermes's Kanban can be the one board AND the dispatcher, with
  a local-model profile as the coder and the Live page watching it; Paperclip and RuFlo are not needed for
  that. Ten decisions wait on him in that session.
- Later the same evening, by that session's own report (not checked by the trader's lead): 8 of his 10
  decisions done and checked there; a Paperclip plugin installed and working in Hermes Desktop, its "Send to
  coder" not pressed; the Ornith download and six fixes to its trial launcher on hold; nothing local loaded.
- Found on 2026-11-17: the Paperclip SERVER is not installed on this Mac (no checkout, no command, no data
  folder); only its agent skill sits in four Hermes profiles (scout, decider, auditor, CTO), talking to a
  control plane that is not running. RuFlo is here as Claude Code plugins (core 0.2.11) plus a source backup
  of 2026-10-22; its workers are Claude Code agents unless pointed elsewhere. Hermes's Kanban is a local
  store (`kanban.db`) with a board reader in the OMH plugin. The Live page's source is `~/valedia-live`.
- **QUEUED** one source of truth for tasks: Hermes's Kanban as THE board, the others mirrored into it; decided
  in that session's step 0, with each tool's telemetry read from its source before anything runs.
- **OWNER** the Paperclip Resilience skill (third party, written for OpenClaw, rotates models on failure) was
  switched on in Hermes. He is switching it off himself ("I'll switch it off", 2026-11-17). Not yet confirmed
  off by a look; it stays off until Paperclip runs and the skill has been read.
- **QUEUED** the coding model: Qwen3.8-27B (already on disk, 16 GB) first. None of the three Ornith 1.5
  builds is on disk (about 28, 20 and 10 GB by parameter count); a download needs his yes. Not beside the
  27B: instead of it, and only after a replay test on checkable coding tasks.
- **QUEUED** two measurements before any local coding swarm: what a second model generating does to
  Elephant-1's decision latency (the collector), and the memory a MiroFish run needs (its first load test has
  not run). The MiroFish operator unloads every model but Elephant-1 and the embedding model, so coding
  workers pause during a run whichever model is chosen.
- **WATCH** at 15:18 PST on 2026-11-17 the 27B was NOT loaded (only Elephant-1 and the embedding model) and
  11.9 of 13.3 GB of swap was in use.
- **QUEUED** the hourly pick job names the 27B by its plain key without checking it is loaded (found
  2026-11-17): LM Studio then loads it on demand, which is how the 27B comes and goes. A second 16 GB copy can
  load if the same weights are already loaded under another name. Make the job check what is loaded first, or
  give the model one name for every user of it. The job itself stays on (owner's rule).
- **DONE, result: AN EFFECT.** The trial of the 27B as a coding worker beside the collector (2026-11-17, on
  the owner's yes, the rule written beforehand) was aborted 43 seconds after the coding task started: the
  collector's first two decisions both hit their 10-second limit and were late (3.9 s before; back to 3.8 to
  3.9 s within a minute of the unload). The two decisions made while the model merely LOADED were normal.
  Lesson: a 27B with a 65,536 window working on a coding prompt cannot share this GPU with the collector. Not
  measured: a smaller model, a small window, short prompts (the hourly pick job's short requests to the same
  27B show no extra failed decisions in the record).
- **DECIDED by the owner (2026-11-17 evening):** local coding models stay OFF this Mac while the collector's
  scored test runs ("Yes, keep them off" here; in the design session: local coding only while the collector
  is paused). The design session's launcher refuses to load while its stop file is in place. No Ornith build
  is downloaded for now (its replay test would load a large model beside the collector too).
- **IDEA** when the scored test ends (or on another machine): measure a small model, a small window and short
  prompts beside the collector before any local coding worker is switched on again.
- Not measured by the trial: a 27B loaded and IDLE beside the collector (no decision fell in those 43
  seconds); the two decisions made while it was loading were normal.

- **OWNER, IT COSTS THE COLLECTOR** Tailscale on this Mac. On 2026-11-18 its app changed state at 14:25:30 PST
  and its session went up and down for a quarter of an hour; each change is a network change for the whole
  machine, and the collector's reads of Kalshi stalled: 34 decisions not ok from 14:27 to 14:42 (10 late, 24
  stale, 2 overruns; the system log's bursts of network changes match its bad minutes; that hour has 0 to 1
  on other days). It was before the scored period. Later bursts (14:55, 15:11 to 15:23, 15:50) cost nothing,
  and Tailscale reads Connected again, but its name for this Mac no longer resolves (git took the commit
  author from that name; commits now pass the same author by hand, no setting changed). During the 14
  scored days a repeat costs scored decisions: his to look at (was it restarted, updated or re-signed in?),
  and best left alone until 3 December. Hermes' Honcho and the phone's access to Hermes use Tailscale.
  IN THE SCORED PERIOD: at 16:52:45 the Tailscale app changed state again and at 16:53:12 two decisions
  failed with `model_unavailable` (the first not ok since 16:00); the day's one other `model_unavailable`
  (13:03:10) follows a burst of Tailscale status changes at 13:02:38 the same way. Two of two: each state
  change of Tailscale is followed within half a minute by a failed call to the local model.
- **WATCH** Telegram is on and has dropped its connection before; Signal is parked (possible relink about
  11 Dec; its job is not installed).
- **QUEUED** the Live page (its session's summary of 2026-11-17, the owner calling that work done): a model
  error is now shown (a turn whose process died reads as not signalled; reader 1.4.0, live since his restart
  at 21:19 PST). Still not seen in real use: a question to a person, a delegation, a failed or interrupted
  turn's end, and the new sentences on the page itself. Two sessions write to `~/valedia-live`: check its log
  and status first, one writer at a time. No review by another model family there either.
- **QUEUED** the skill system: skills not yet rewritten to the standard; per-skill checks, scorecard and run
  log not built.
- **QUEUED** Hermes: the `valedia-cto` profile has no config; panel bots still carry built-in tools pending
  his word; Hermes on the Ternary model is not proven end to end; re-run the token-usage tools.
- **BLOCKED** an app password for Valedia's own email: needs two-step verification switched on by him.
- **WATCH** 2026-12-13: check that December's developer credit lands on the right billing account.
- **QUEUED** the old Kalshi stack (September): two candidate strategies parked (one needs a FRED key), the
  council parked, a self-audit to retire or rewrite, two parallel brains not unified. **OWNER** whether to
  park the oil and weather strategies by hand.
- **OWNER** the research desk's waiting room (approve or reject); a cloud researcher seat.
- **OWNER** the `ssh openclaw` login is refused (his key to authorise).

## 12. Infrastructure and hygiene

- **NEXT** refresh `docs/ROADMAP.md` (rows are as of 2026-11-14) and add rows for T50, T51, T51c, T52.
- **QUEUED** `docs/GOALS_AND_MAP.md` is a stale snapshot of 2026-11-13.
- **QUEUED** about 70 old candidate and checker worktrees and their scratch folders to clear.
- **QUEUED** backups (5.1): the nightly job is installed; no scheduled run is recorded as checked.
- **QUEUED** the owner's page built on the real stores (5.3).
- **OWNER** Spotlight privacy for the scratch area.
- **QUEUED** H-1 a second sweep for this list: the 190 long ledger lines and the 445 ticket files one by one.
- **IDEA** the plugin that keeps tool output out of the session reports a newer version; upgrade when no work
  is in flight.

## 13. Waiting on the owner (each by name)

1. The stake for the fund book.
2. The live key pair, placed by him.
3. The three files: the confirmation, the go-live figures, the daily order cap.
4. A yes for a review of the order path by another model family.
5. Unattended from the first order, or after a proving run.
6. The MiroFish load-only test.
7. A repeating job for the leaders record.
8. The watchdog's schedule.
9. Risk limits for event contracts; the ATL practice contract.
10. Paid stock data with dead companies.
11. The outside trading project's venue, forex broker and margin choices.
12. MiroFish: two models by phase; a collector-off proof run; the LM Link guard; format qualification; the
    old Zep key.
13. The old Kalshi stack's oil and weather strategies; the research desk's waiting room; a cloud researcher
    seat.
14. Older scripts that send his addresses; the `ssh openclaw` login; two-step verification for Valedia's
    email; Spotlight privacy for the scratch area; TimesFM 3.0's licence.
15. Starting the design session for Paperclip, RuFlo, the Kanban and the Live page; a download of an Ornith
    model, if wanted.

## 14. Closed log (newest first)

- 2026-11-17 T52 the leaders' record: merged (main `bc4e8eb`); leaders fixed and the baseline pass taken.
- 2026-11-17 T51 build A, fractional orders on the practice path: merged (main `5d944d4`); live still refused.
- 2026-11-17 local coding models beside the collector: tried once (the 27B, aborted after 43 seconds, two
  late decisions), then decided by the owner: off while the scored test runs.
- 2026-11-17 the master list as a hosted page: not now. Owner: "the file is enough right now".
- 2026-11-17 the owner's cash account is shared with his own trading (his word); T51c follows from it.
- 2026-11-17 T47c the copying-leaders analysis in bounded memory: merged (main `0172b3f`).

- 2026-11-17 Webull approved the owner's app for live API trading (his word).
- 2026-11-17 T50 the live key pair kept apart from the practice jobs: merged (main `c2aeeb6`).
- 2026-11-17 T47 the copying-leaders program and its bounded-memory pull: merged (main `33ace6f`).
- 2026-11-18 T47e a refused market id no longer stops the pull: merged (main `cdd5371`), blind check passed
  with notes (three follow-ups in T47d).
- 2026-11-18 L-0 the Sunday research cycle's model read: Claude Sonnet through Hermes, nothing local.
- 2026-11-18 T51c step 0 (the shared account): study done, rulings written; the build has started.
- 2026-11-18 T51c S1 to S3 (the shared account's parts, nothing wired): merged (main `46b398d`).
- 2026-11-18 the position limit's basis in the shared account: her own book alone (owner: "Yes, hers only").
- 2026-11-18 fractional orders proven on the practice account (market orders filled inside their collars).
- 2026-11-18 test activities beside the collector, compared: scratch on the internal disk costs no decision;
  the one-at-a-time rule replaced by a two-slot gate (owner: no time slots).
- 2026-11-18 T51c S4a the fee rule: merged (main `599ae5b`); the order-path gate moved.
- 2026-11-18 T52b the leaders' record repaired: merged (main `ad07778`); the daily job installed (05:20).
- 2026-11-18 the copying-leaders test's fifth amendment (the oracle's own resolution rows): main `f40bc41`.
- 2026-11-18 T53 the owner's copy method on Webull, studied for one day: as shown it carries little over.
- 2026-11-18 the forward paper test of that method: locked (main `58754c7`); rule map and listing table begun.
- 2026-11-18 T52c the leaders' record from a list of wallets: merged (main `0cac9ab`).
- 2026-11-18 the test gate passes a test's error output through (it had been dropping it).
- 2026-11-17 TMF6 the MiroFish operator: merged (never run for real); TMF7 the acknowledged-revisions list
  (the owner's "Yes, accept them" for 22 files).
- 2026-11-17 T45-1 event settlement by two witnesses: merged.
- 2026-11-17 T39-2b the gate change (claims of edge or exposure): merged; ten old findings marked pre-split.
- 2026-11-17 T44-3 the decision chain moved to Elephant-1: merged.
- 2026-11-17 the ask-bands test on Webull's prediction markets: failed, as predicted (1.25).
- 2026-11-13 Kronos, the weather contracts and the sports lines: each failed its locked test.
