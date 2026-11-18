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

## 1. In flight today (2026-11-17)

- **DONE 2026-11-17 20:30 PST** T51 fractional orders, build A (practice path only; live fractions stay
  refused): four slices, one blind check, merged (main `5d944d4`), 2,601 tests pass on main, the order-step
  job gate moved. **NEXT** the practice proof in market hours, then build B (section 2).
- **NOW** T47 copying Polymarket's leaders (historical test): phase 1 of the data pull is running; two wallets
  with more than five million trades each are left out by name, as the locked rule says. Then phase 2
  (prices), then the first-stage test. Lead's locked expectation: it fails. Roadmap 1.26.
- **NOW** T47c (the analysis in bounded memory): passed its blind check and is on main (`0172b3f`,
  2026-11-17 15:21 PST).
- **DONE 2026-11-17 20:38 PST** T52 the leaders' record: merged (main `bc4e8eb`) after three blind checks; the
  ten leaders FIXED (five Sports, five Crypto, none on both) and the first pass taken as the baseline in
  `artifacts/leaders_monitor/leaders.sqlite`: 9 wallets read, 1,942 open positions; two of the Sports leaders
  hold nothing open; Sports #2 holds more than 20,000 open positions and is past the page limit (it stays in
  the list as unread; a wallet of that size is a market maker). Passes are run BY HAND until the owner allows a
  job; each later pass records what is new, grown, shrunk or gone. **QUEUED** T52b (three follow-ups) before
  any job; the forward document before any paper trade.
- **WATCH** the order-book collector (roadmap 1.16): clean since 05:48 today. From 2026-11-19 00:00 UTC its
  scored period runs: at most ONE test-running agent beside it.

## 2. Path to the first real order of the fund book (in order)

The owner said yes on 2026-11-17 to preparing the fund-holding book (fifteen funds, inverse-volatility sizes,
reset every 21 sessions) at a stake smaller than whole shares. Webull has approved his app for live API
trading (his word, 2026-11-17).

1. **DONE 2026-11-17** T51 build A: blind check passed with notes, merged, the order-path job gate moved. The
   first scheduled practice run on it (06:55 PST) upgrades the practice ledger's layout; both real ledgers are
   backed up in the deploy folder.
2. **NEXT** The practice proof, run by the lead in market hours: a fractional market purchase accepted; the
   decimals Webull reports for a fill and a position; a fractional sale; a closing sale under 5 dollars.
3. **NEXT** T51 build B: the proof reading (only fills of this code path count; one purchase and one sale in
   clean batches), the owner's consent field for live fractions, the live opening, and the FIRST-CONTACT RULE
   (in production one fractional order per run until one has filled and reconciled cleanly: practice is looser
   than production, so practice cannot prove what production refuses).
4. **QUEUED** T51c book-level accounting in a SHARED account (ticket not yet written). The owner trades the
   same cash account by hand and holds event positions in it. Today's code would report a fault on every run
   after any trade of his, count his shares against Valedia's position limit, read his withdrawals as her
   losses and let a deposit hide one. Capital, exposure, day loss and reconcile must be judged on her own
   book. PRECONDITION of the first real order.
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
- **WATCH** order-book loop with Elephant-1 (1.16): first fit not before 2026-11-19; the lead owes a copy of
  the rows before that moment into a fresh file on 19 Nov, and the leakage checker before 22 Nov.
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
- **OWNER** a repeating job for the leaders record (asked, unanswered). Until then passes are run by hand.
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
- 2026-11-17 TMF6 the MiroFish operator: merged (never run for real); TMF7 the acknowledged-revisions list
  (the owner's "Yes, accept them" for 22 files).
- 2026-11-17 T45-1 event settlement by two witnesses: merged.
- 2026-11-17 T39-2b the gate change (claims of edge or exposure): merged; ten old findings marked pre-split.
- 2026-11-17 T44-3 the decision chain moved to Elephant-1: merged.
- 2026-11-17 the ask-bands test on Webull's prediction markets: failed, as predicted (1.25).
- 2026-11-13 Kronos, the weather contracts and the sports lines: each failed its locked test.
