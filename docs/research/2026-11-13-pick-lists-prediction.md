# Prediction, written before any pick is graded: do public pick lists beat the price?

2026-11-13. Roadmap 1.19, at the owner's request. Locked by the commit that adds this file. Nothing below has
been looked at: no stored pick has been compared with a result, a score or a later price.

## The idea

A Reddit bot posts a "Pick of the Day" thread every day and people write one pick each under it. If some of
those picks know something the price does not, buying the picked side should pay. The base rate says no:
public picks, on average, lose the bookmaker's margin. This test decides whether the lists earn any weight.

## What counts as a pick

- A comment in a thread collected by `data_and_fees/pick_lists.py`, not by a moderator, not deleted, read out
  by the local model under the prompt in force (hash `5c33f300...3e47`) as a pick, with its fields checked
  against the comment's own words.
- **Only text first seen before the game began.** The version used is `version_before(comment, game start)`.
  A comment first seen after the start is not counted. An edit seen after the start is ignored. A collection
  run that was missed costs picks, never honesty.
- One pick an author a thread: the first one seen.
- Markets graded: moneyline, spread, total. Player props, parlays and everything else are counted and
  reported as not graded.
- Leagues: NFL, NBA, NHL, college football, men's college basketball.
- **Matching a pick to a game:** by code. The schedule is The Odds API's free events list for the five
  leagues. A pick is matched when its team (for a total, both teams or one) names exactly one game that starts
  after the comment was first seen and before 06:00 US-Eastern on the day after the thread's date. No match,
  or more than one, means not graded, with the reason counted. The local 27B model may propose a team for a
  pick the code could not match; a proposal is accepted only when the same uniqueness rule then holds and
  the team's name, as the schedule spells its city or nickname, stands in the comment as whole words.
- Before any result is read, the lead reads 40 matched picks beside their comments (as data) and counts wrong
  matches. More than 2 wrong of 40: the matching rule is repaired and re-checked, still before any result.
  After the first result is read, the matching rule does not change.

## Question A: can the picks be traded on Kalshi?

This is the question that matters for trading, because the Events account trades Kalshi's contracts.

For each counted moneyline pick: buy one contract of the picked team in Kalshi's single-game series at the
ask of the first one-minute candle that ends at least 60 seconds after the comment was first seen, if that
candle ends within 10 minutes and before the game's start, the ask is at most 0.95 and the spread at most
5 cents. Hold to the result. Fee: Kalshi's taker fee, 0.07 x price x (1 - price) a contract, rounded up to
the cent on an order of 100. Profit a contract: result minus ask minus fee. A team picked by several authors
in one thread is bought once, at the first of them.

Three versions, chosen between in development only:
1. every counted pick;
2. only a team picked by at least 2 authors in the thread before the start (bought when the second is seen);
3. only a team picked by at least 3 (bought when the third is seen).

## Question B: do the picks win at the odds they quote?

Information only: it cannot be traded as it stands. For each counted moneyline, spread or total pick whose
comment gives odds (decimal odds between 1.30 and 4.00 after conversion) and, for a spread or total, a line:
return a unit staked at the odds as written. Win: odds minus 1. Loss: minus 1. Push: 0. Final scores include
overtime. Scores come from The Odds API's scores list; the winner must agree with Kalshi's result where
Kalshi lists the game, and a disagreement means not graded and is counted. A total is graded only when the
comment's selection says over or under in whole words or as `o`/`u` joined to the line.

## Periods

- **Development:** threads dated 2026-11-13 to 2026-12-13. Here, and only here, the version for Question A
  is chosen (1, 2 or 3). The choice is committed in a note under this file before any result of a test-period
  game is read.
- **Test:** threads dated from 2026-12-14 until the counts below are reached, at the latest 2027-03-31.
  Scored once.

## Pass lines (test period only)

- **A passes** when at least 150 contracts were bought on at least 20 days, the mean profit a contract is
  above 0, and the lower end of its 95% interval (whole days resampled) is above 0.
- **B passes** when at least 300 picks were graded, the mean return a unit is above 0, and the lower end of
  its 95% interval (whole days resampled) is above 0.
- Counts not reached by 2027-03-31: not shown, which is a fail.

A pass on A sends the rule to paper trading on the Events account with the same rule, nothing more. A pass
on B alone gives the lists a place as an input to forecasts, with no orders. A fail gives them no weight:
the wiki stays as reading matter.

## Stopping early

At the end of development the test may be called off as futile if the picks counted a day make the test
counts unreachable by the end date, or if every version of A has a mean profit below minus 2 cents a contract
with an upper interval end below 0. Stopping can only turn a possible pass into a fail.

## What I expect

A fail on both. A: about minus 1 to minus 2 cents a contract (the fee and half the spread), chance of a pass
about 1 in 15. B: about minus 3% to minus 5% a unit, chance of a pass about 1 in 15. The likeliest way to
"not shown" is too few picks: the first two threads held about 3 gradable moneylines and 8 gradable single
picks a day.

## Later, not part of this test

Whether an author's own graded record predicts their later picks (the step that could give single authors a
weight) needs several hundred graded picks. It gets its own locked prediction then. Records that authors
claim for themselves are shown as claimed and never used.

## Limits known in advance

- Collection runs only when started: an hourly schedule waits for the owner's yes. Picks first seen after
  the start are lost.
- The odds a comment quotes may never have been on offer; Question B takes them as written.
- One source, one community, one season.
- Code is checked blind by the same model family that builds it.

## Clarifications (2026-11-13, written before any pick has been compared with a result)

Raised by the builder of the grading code; none was chosen with a result in sight.

1. **When a pick counts as seen.** For Question A the purchase clock starts when the pick's text was first
   seen: the earliest stored version from which every later version before the start names the same team. A
   comment first seen as something else and edited into a pick counts from the edit's first sighting.
2. **Agreement (versions 2 and 3)** counts moneyline picks only.
3. **A tied game** in Question B is a push for a moneyline pick.
4. **A model's proposal** for an unmatched pick is accepted only when the name standing in the comment belongs
   to no other team with a game in the window.
5. **The matching rule is frozen** from the first result read; the schedule keeps growing, so a pick's match is
   recomputed with the frozen rule on each run and the latest row counts.
6. **Fewer than 40 matched picks:** the reading of 40 waits until there are 40; no result is read before it.
7. **The test period is scored once, at the end:** when the counts are reached or on 2027-03-31, not before.

## Clarifications after the first check of the code (2026-11-13, still before any pick has been compared with a result)

A blind check of the grading code found ways the rules above could be bent. Each is closed here, on the
strict side: every one can only remove picks, never add them.

8. **A game's start** is the earliest start time that any stored schedule list gives for it. Text and purchases
   count only before that moment. A game counts only if a list fetched before that moment held it.
9. **Naming a team.** A comment's words are compared with every team known from the stored lists of the five
   leagues, not only the teams playing in the window. A pick is matched only when one team is named more
   fully than any other; a tie is not graded. Kalshi's own team codes, written in capitals, count as names.
   No model proposes matches: the sentence above that allowed it is withdrawn.
10. **One reading per rule.** If the reading of 40 matched picks finds more than 2 wrong, that matching rule is
    closed for good; a new reading needs a changed rule. The reader does not choose which 40 are drawn.
11. **The test period is scored once:** when both counts are reached or on 2027-03-31, never earlier, and
    never again.
12. **A minute with no bid** on Kalshi is not a quote: nothing is bought in it.
13. **One record.** The real pick store is graded into one grading store only.
14. **A game with no final score three days after its start** is not graded.

## Clarifications after the second check of the code (2026-11-13, still before any pick has been compared with a result)

15. **Ending the test with a margin.** Some picks turn out not gradable only when results are read (the two
    sources disagree on the winner). So the test period ends on the first thread date on which the counts that
    can be known without reading a result reach 158 contracts on 20 days and 315 picks (5% above the pass
    lines), or on 2027-03-31. The pass lines stay 150 and 300.
16. **Team codes.** A code names a team only from complete tables of all five leagues' codes, and only when
    it belongs to one team alone (across the five leagues, or within the league the comment writes). A capital
    word that is also an ordinary English word counts only where a pick puts its team, directly before the bet;
    in a selection written wholly in capitals no word counts as a code.
17. **The scoring code is pinned** when the version for Question A is chosen. If a fault in it has to be
    repaired afterwards, the repair, its reason and both versions are written into the result.
18. **A pick against a team** ("fade", "against") and a selection naming teams of two different games are not
    graded.
19. **What the guards are.** One reading per rule and one scoring are kept by the code against slips and are
    recorded so that every reading ever made is printed with the result. They cannot stop someone who changes
    the code; the record is what makes that visible.
20. **A selection in capitals (a narrowing of 16, same day, before any result).** The rule "in a selection
    written wholly in capitals no word counts as a code" applies only when the selection holds at least one
    word besides team codes and the bet's own markers (ML, a line, over or under, numbers). "LAL ML" and
    "BOS -4.5" are codes; "IND STRONG ML" is not.
21. **When the test is scored (same day, before any result).** If the test ends by date, threads dated up to
    2027-03-31 count and the scoring is made on or after 2027-04-04, when their games have results. If it ends
    by the counts, the scoring is made three days or more after the last counted thread's date. A league's
    table of team codes is complete only if every listed game gave two readable codes.
22. **The boundary between development and test, by the clock (same day, before any result).** Until the
    version for Question A is chosen, no result is read of any game that starts on or after 2026-12-13 00:00
    US-Eastern, whether or not a test-period pick has been matched to it yet. So the version is chosen on the
    games before that moment; picks of the 12/13 thread on later games are shown after the choice and decide
    nothing.
