STATUS: DRAFT, NOT LOCKED. It locks when Step 0 has filled every SLOT and the lead has ruled on every item under 'Not adopted yet'. Nothing is run before 2026-12-04.

# BTC 15-minute Up/Down on Kalshi: a forward paper test, rule written before the first window (2026-11-24)

Sources are quoted by tag: `ML` = `docs/MASTER-LIST.md` line (line numbers as of the commit this document was cut from), `R50` = `~/Valedia_Claude_Reply50.md` section (the lead's reply 50; the master list summarizes it at ML 639-650), `C51` = Carbon's round 51 file (`~/Downloads/Valedia_Trading/Valedia_Carbon_Response50.md`) section 1, adopted at ML 634-635. A number appears in this document only where a master-list line carries it.

Format model: `docs/research/2026-11-18-forward-copy-on-webull-prediction.md` (the newest forward, paper, pre-registration style document).

Written 2026-11-24, before any window of this test has been observed. Nothing in it is a result.

## What this is, and what it is not

- PAPER ONLY. No order is sent to any venue. No real event order until a rule passes (ML 669: "no real event order until a rule passes").
- FORWARD ONLY. Every window used is observed live after the log starts. No historical window is used to fit, tune or test (ML 669: "forward shadow log ONLY"; SEAL_EPOCH_1 2025-01-01..2026-12-31 confirmed, "no dev history").
- NOTHING BEFORE 12-04. No request to any exchange or to Kalshi, and no logged window, before 2026-12-04 (ML 669 and 677: the forward log runs from 12-04; ML 347-348: Kalshi reads only after 12-03; the collector's scored test runs to 12-03). SLOT start-instant: the exact instant and its time zone. Lead ruling for the build: 2026-12-04 00:00 America/Los_Angeles, which is 2026-12-04T08:00:00+00:00, held in the logger as the constant `ENABLED_FROM`; the lead confirms it at lock.
- NO LLM IN THE DECISION PATH. Every trigger, gate and size is arithmetic in code (ML 344-345: "arithmetic roles in code, no LLM in the decision path"; the Hermes CoS team audits the log only).
- Venue for the data: Kalshi's public endpoints with no account (R50 section 2, last paragraph). Whether a real order would go through Webull or a direct Kalshi account is the owner's decision and not part of this document (ML 650).
- Carbon's two unsourced figures are NOT used anywhere in this document: the "$4.45" composite strike error (Carbon's own estimate, dropped: ML 638 and 650) and the "1 in 8" near-target share (a practitioner writeup, background only: ML 638 and 650). We measure both ourselves on the training slice and cite only our own numbers.
- Outside sources (arXiv 2606.31675, botforkalshi, SpectralEdge) are background and never evidence for this test (ML 638, 649-650).

## What was looked at before writing

- Nothing about BTC windows: no price, strike, book or outcome of any 15-minute window has been read. KXBTC15M was never probed (ML 673).
- Kronos's AUC (`docs/research/2026-11-13-kronos-result.md`) is a different model and is not evidence here; ROC3 on 15-minute BTC has never been tested here (ML 675 lists the outside "momentum AUC" claim as not on the closed list).
- Step 0 (SLOT step0-results: the lead pastes, from Step 0's own record, what was read and on what date: the series rules page and its settlement text, `floor_strike` on a market record, the series' `fee_type` and multiplier, whether Webull lists KXBTC15M and its fee on that series). Until Step 0 fills these, the document is not locked.

## The market and how a window settles (read at Step 0, not assumed)

- One window = one 15-minute KXBTC15M market. Windows per day: 96 (ML 675).
- Settlement as Carbon stated it: YES if the simple average of the BRTI values over the last minute before the close is at least the target; ties pay YES; the target is known at the open and is `floor_strike`. The master list carries the BRTI settlement rule as an UNVERIFIED Carbon claim (ML 674), so this sentence is read against Kalshi's own page at Step 0, not assumed. SLOT settlement-text: the series' own rules page text and the `floor_strike` field as read at Step 0; the lead pastes both here. If either differs from the sentence above, the document is not locked until the lead rules on it.
- The outcome of a window for scoring is Kalshi's own settled result for that market (SLOT outcome-source: the exact result field Step 0 records).

## The price feed: frozen before window 1 (Carbon pin 1, adopted at ML 634)

- The composite is built from BRTI's constituent exchanges, the list read from CF Benchmarks' current BRTI list at Step 0 (ML 647).
- SLOT constituent-subset: the subset of that list that has a free public USD spot endpoint. The lead fills it from Step 0's record (names, the endpoint each, the date each exchange's data terms were read). Frozen BEFORE window 1 of training and never changed during train or test. If an exchange is later withdrawn or its endpoint breaks, it stays in the frozen subset and simply does not respond (the window is logged by the rule below); the subset is never re-chosen mid-test. The logger takes the subset as configuration and refuses to run with an empty one.
- The price used is an outlier-trimmed median over the frozen subset's responding exchanges, taken at the window's one snapshot instant. At least 3 exchanges must respond; otherwise the window is logged `SKIP_FEED` and is not eligible (ML 634). The exact trim is under 'Not adopted yet'; it is fixed before window 1.
- Only free public endpoints, a generic User-Agent and no telemetry; each exchange's data terms are read before its first call (ML no-telemetry rule).
- The composite's own strike error at settlement is MEASURED on the training slice and reported. It is a reported figure, never an input.

## The model: fit once, on the training slice only

q-hat = sigma(w0 + w1 * logit(P_mid) + w2 * z_ROC3 + w3 * z_dist), with L2 regularization and the signs left free, so mean reversion is tested inside the same fit at no extra cost in K (ML 644, 671).

- P_mid: the Kalshi yes mid at t*.
- z_dist: distance of the composite price to `floor_strike`, standardized (ML 644: "z_dist vs floor_strike").
- SLOT zROC3-definition and SLOT L2-strength: see 'Not adopted yet'.
- One fit, on the first 1,500 ELIGIBLE windows (the training slice, ML 644). Calendar time is longer than 1,500 windows' worth whenever a window is not eligible. No refit, ever, in the test.

## Windows, snapshot, gates

- t* = the window's open + 5 minutes (ML 645). ONE snapshot per window, written before settlement, skips included (ML 669).
- A window is ELIGIBLE only if all gates hold at t* (ML 341-344):
  1. RANGE5 <= 0.45% (the owner's guide figure, ML 343). SLOT RANGE5-definition: see 'Not adopted yet'.
  2. Kalshi yes spread <= $0.03 (ML 343).
  3. Ask depth at the best ask >= the stake (the stake that applies is under 'Not adopted yet').
- Every skip is logged with its reason (ML 344: "every skip logged"): a gate failure, `SKIP_FEED`, or a missing Kalshi book.
- Thresholds are frozen by this document, before the first window (ML 344: "thresholds frozen first").
- Logging versus deciding: the forward logger records each window's raw inputs only (the per-exchange quotes it was given, the composite price, the Kalshi book it was given, `SKIP_FEED` and why). It computes no gate, trigger, fill, ROC3 or RANGE5; those are computed offline from the log once this document is locked.

## The paper clip (Carbon pin 2, adopted at ML 634-635)

- A fixed paper clip C_paper = 10 contracts per order (ML 635). "Flat sizing until a pass": every paper order is exactly C_paper contracts; there is no scaling. The owner's guide caps a stake at 1% (ML 343-344); how the clip meets that cap is under 'Not adopted yet' (cap check).
- A paper order that would need more than the visible depth does not occur.

## The two rules (ML 645-648)

Both rules use the one frozen q-hat, are evaluated at t*, and trade at most once per window per rule, on either side (yes or no).

**Rule 1 (primary): a taker.** Take the ask on a side when q-hat for that side, less the ask, less the fee, clears the Rule 1 threshold (ML 347: scored "q - ask - fee"; ML 645: "Rule 1 taker primary"), where `fee` is the fee the order WOULD REALLY PAY as rounded (below), at the venue Step 0 picks (ML 647: "fee charged as rounded per order"). The fill is assumed at the ask recorded at t*. The threshold is under 'Not adopted yet'.

**Rule 2 (the one sibling): a 60-second maker.** A post-only order joining the best bid, cancelled after 60 seconds, scored by our virtual-queue rule: the queue ahead is the visible size at t*, and the public trade tape over [t*, t* + 60 s] decides any fill (ML 645-646: "post-only join-bid 60 s TTL scored by the virtual-queue rule + public tape"). The trigger and the fill arithmetic are under 'Not adopted yet'.
- The tape reads count against the SAME Kalshi read budget as the snapshots, and they run only after 12-03, far below the collector's rate (ML 648: "tape reads inside the Kalshi read budget"; ML 347-348). The budget figure itself is not given in the master list and is set by the lead.

## Costs and fees (ML 647-648)

- The test charges the fee for the ORDER as actually rounded, never the per-contract rate: the rounding is applied to the whole order, in cents (ML 647: "fee charged as rounded per order (1-lot = 2c at 50c)"). The per-contract fee in the Rule 1 trigger is that rounded order fee divided by C_paper. SLOT taker-rate: the series' taker multiplier as read at Step 0 from Kalshi's own data. The lead fills; if Step 0 reads another multiplier the document records both. The arithmetic of the order fee is under 'Not adopted yet'.
- Maker fee: until Step 0 reads the series' `fee_type`, Rule 2 is scored at the maker rate 0.0175, the worse case (ML 648: "maker scored at 0.0175 until Step 0 reads the series fee_type"). The rounding is the same: whole order, in cents. SLOT maker-fee-after-step0: what Step 0 read; if it reads 0, Rule 2 is reported at 0.0175 AND at the read rate, and PASS is judged at 0.0175 only.
- Paper fills are priced at both venues' fee schedules (Kalshi direct and Webull) so the venue choice can wait for evidence (ML 650). SLOT webull-fee: Webull's fee on KXBTC15M as read at Step 0 (ML 673: the only figure seen is a round trip of $0.03 on other event contracts, "fee schedule unread"; nothing about KXBTC15M). Until read, only the Kalshi schedule is scored and the Webull column is blank.
- No other cost: no slippage model beyond the ask at t* (Rule 1) and the tape rule (Rule 2).

## The training gate (ML 645)

On the 1,500 training windows: a two-sided likelihood-ratio test of (w2, w3) != (0, 0), p < 0.05; AND 5-fold time-ordered cross-validation in which the full model beats logit(P_mid) alone on log-loss. If either fails, the test STOPS with 0 out-of-sample windows. This is a finding ("no signal beyond the price"), not a pass.

## The test slice: what is measured, and the pass and fail lines

The test slice is every eligible window AFTER the training slice, with the coefficients frozen (ML 669-672).

Measured, for each rule separately:
- the number of out-of-sample eligible windows, the number of paper orders, and the number filled (Rule 2);
- mean realized net per paper order: payoff (the contract's settlement value if the side wins, else 0; SLOT settlement-value: read at Step 0 from the series' own rules, no master-list line gives it), less the entry price, less the rounded order fee, per contract;
- the t statistic of that mean;
- the Deflated Sharpe (DSR);
- Brier score of q-hat against the outcome, and Brier score of the Kalshi mid, in the same windows;
- the count of `SKIP_FEED` windows and of every other skip reason;
- on the training slice: the composite's strike error at settlement (our own figure), and the share of windows that settle near the target (our own figure, reported beside, never in place of, the numbers this document refuses to cite).

Lines (round 49, adopted, ML 669-672). That round 50's change to a 1,500-window training slice leaves these lines unchanged is this document's reading of the master list, not a quoted line; the lead confirms it at the lock:
- FAST KILL at 500 out-of-sample windows: if the mean net is <= 0, the rule FAILS and stops (ML 671-672). What the 500 counts is under 'Not adopted yet'.
- PASS at 3,500 out-of-sample windows: t >= 3.00 AND DSR >= 0.95 AND Brier of q-hat beats Brier of the price (ML 672).
- Multiple rules: Holm across the two rules (ML 646). How Holm combines with the t line is under 'Not adopted yet'.
- The calendar: 96 windows a day (ML 675). The master list's own figures were "fast kill about 21 days, pass about 146 days" for r = 0.25 (ML 675-676), under round 49's 1,000-window plan (ML 671); they are not restated for the 1,500-window plan. SLOT calendar: the lead re-derives it from the final eligible rate once Step 0 and the first days of the log give it; no calendar figure is a promise.
- A pass would justify the next step only: a real order at the smallest stake after the owner's limits for event contracts and the owner's venue decision. It would not show a profit at larger size (paper fills at the ask or by the tape are assumed, never seen).

## What I expect

SLOT expectation: the lead writes one honest sentence before the first window (the shape of the forward-copy document's "What I expect"). It is the lead's own prediction and is not drafted here.

## Reported for information, never a way to pass

The same measures with the fee at zero; with the maker fee at the Step-0 read rate; Rule 1 by side; windows near the target apart from the rest; the composite against single exchanges. Any such cut is a description and cannot rescue a failed line.

## Limits, stated now

- Kalshi's public data is read slowly here, beside the collector that uses the same host; snapshots and tape pages stay far below the collector's rate (ML 347-348, 648).
- The composite is a stand-in for BRTI built from free endpoints; its strike error is measured, not assumed.
- Paper fills assume the ask at t* (Rule 1) and the virtual queue plus tape (Rule 2); neither is a real fill.
- The settlement text and fee data are read at Step 0 from Kalshi's own pages and may change; any change is dated and recorded, and it is not a change of this rule.

## Not adopted yet (the lead rules before the lock)

Each item below is a PROPOSAL, written by the drafter of this document. None is adopted, none has a master-list line unless one is cited, and none is used by any code until the lead rules. Each keeps the SLOT name it had in the draft.

1. SLOT trim-rule (the outlier trim in the composite). Proposal: with 3 or 4 responding exchanges, the plain median (it is already robust); with 5 or more, drop the single highest and the single lowest, then take the median. Adopted part: "outlier-trimmed median over the frozen subset, at least 3 responding, else SKIP_FEED" (ML 634). The logger takes the trim as a parameter and its default is this proposal.
2. SLOT zROC3-definition. What ROC3 is (the bar length and the price series it is computed on) and how z_ROC3 and z_dist are standardized (which training statistics, fixed from the training slice only). The master list has only the guide's threshold "ROC3 >= +0.10%" (ML 343), which is a threshold and not a definition. Proposal: none drafted beyond this: the logger carries each exchange's recent one-minute closes so a definition can be computed offline from the log; if an exchange's free endpoint cannot give one-minute closes, ROC3 and RANGE5 need another series, and that is the lead's decision.
3. SLOT RANGE5-definition. The price series and the 5-minute span the RANGE5 gate (<= 0.45%, ML 343) is computed on. Proposal: the same series as item 2, so one set of recorded closes feeds both.
4. SLOT L2-strength. The fixed regularization strength, or the rule by which it is chosen inside the training slice's own cross-validation. Fixed before the fit. Proposal: none drafted.
5. SLOT rule1-threshold. The edge that Rule 1 must clear (q-hat less ask less fee). The adopted text names only "q - ask - fee" (ML 347). Proposal: 1.5 cents, taken from the draft's reading of R50 section 2; it has no master-list line, so the lead confirms the figure or replaces it.
6. SLOT rule2-trigger. The condition under which Rule 2 posts. Proposal: the same edge test as Rule 1 with the bid in place of the ask and the maker fee in place of the taker fee, on the side with the larger q-hat edge.
7. SLOT rule2-fill-arithmetic. How the tape converts to filled contracts. Proposal: filled = max(0, min(C_paper, tape volume at or through the bid price in [t*, t* + 60 s] minus the queue ahead at t*)), counting trades by taker side at or through our bid price after t*; an unfilled quantity is no trade and scores 0.
8. SLOT fee-arithmetic. The order fee for C_paper contracts at price P. Proposal: the schedule's rate times C_paper times P(1 - P), rounded up to a whole cent for the order, and the per-contract fee in the Rule 1 test is that figure divided by C_paper. The adopted part is only "as rounded per order" (ML 647).
9. SLOT gate3-stake. What "ask depth >= stake" means for gate 3. Proposal: the stake is C_paper, so the gate is ask depth at the best ask >= C_paper.
10. SLOT cap-check. The lead confirms from the owner's limits that C_paper contracts at the highest ask the gates allow is inside 1% of the stake the paper book is deemed to hold (ML 343-344, 635), and writes that stake figure here. The figure is not in the master list and is not guessed.
11. SLOT passfail-details (three parts, none adopted in detail in round 50):
    - (a) whether the 500 and 3,500 counts are eligible windows or paper orders. Proposal: eligible windows (the unit ML 669-672 and its "96 windows/day" clock use); the per-order mean is then over the orders placed in those windows.
    - (b) how Holm combines with "t >= 3.00". Proposal: each rule must clear all three lines on its own, and Holm adjusts the one-sided p of the t statistic across the two rules; the stricter result governs.
    - (c) K, the trial count for the DSR. It counts every rule, every threshold and the training gate. The lead fills it from the ledger's own count; it is not guessed here.
12. SLOT skip-counting. Whether `SKIP_FEED` windows count toward the 1,500 training windows. Proposal: no ("not eligible", ML 634); they are logged, counted separately and never eligible.
13. SLOT snapshot-clock. Which clock defines the window's open for t*. Proposal: Kalshi's `open_time` on the market record, read after 12-03, with the wall-clock quarter-hour logged beside it as a cross-check.
14. SLOT kalshi-read-budget. The number of Kalshi reads per hour the daytime runner may use beside the collector (ML 347-348, 648). Not given in the master list; the lead states it.

## Locking

Locked when: every SLOT above is filled by the lead, every item under 'Not adopted yet' is ruled on, the document is committed, and its hash is recorded in the ledger BEFORE the logger's first window (2026-12-04 at the earliest). After lock it is never edited; a correction is a dated, committed amendment that cannot change a pass or fail line after any out-of-sample window has been read.
