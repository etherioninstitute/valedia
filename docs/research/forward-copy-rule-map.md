# Forward copy test: the rule map (which Kalshi series ask the SAME question as Polymarket's market)

Kept beside `2026-11-18-forward-copy-on-webull-prediction.md`, whose rule it serves. A series enters this map by a
dated commit made BEFORE any copy in it is made. Classes: **SAME** (the two texts pay alike in every outcome but
the rare exceptions named here), **NEAR** (they can pay differently in ordinary outcomes; recorded for
information, never in the rule), **NONE**. Decided by the lead from both venues' rule texts.

How each entry was read: ONE market of the series on each venue, both texts in full (Polymarket's `description`
from its public market-description service; Kalshi's `rules_primary` and `rules_secondary` from its public market
data). The texts are templates filled with names and dates. The matcher must keep both texts with every copy
and must NOT copy where a market's text, names and dates aside, departs from the template quoted here.

## Entries of 2026-11-18 (read about 14:10 PST; the map was empty before)

| Kalshi series | Polymarket market | Class | Side mapping | Rare exceptions (a copy that meets one is held to Kalshi's settlement and flagged) |
| --- | --- | --- | --- | --- |
| KXNBAGAME | an NBA game's winner ("Jazz vs. Clippers", outcomes the two teams) | SAME | the team's own market, Yes | the game cancelled, abandoned with no official result, or not started inside the venue's window |
| KXNBASPREAD | an NBA game's spread ("Spread: Lakers (-7.5)", outcomes the two teams) | SAME for half-point lines only | favourite's outcome = Yes on the market "wins by more than N.5"; the other outcome = No | as above |
| KXPREMIERLEAGUE | "Will X win the 2026-27 English Premier League Championship?" | SAME | Yes = Yes, No = No | the season cancelled or no champion declared by Polymarket's date; a title changed after the fact |
| KXPRESNOMD | "Will X win the 2028 Democratic presidential nomination?" | SAME | Yes = Yes, No = No | a nominee replaced after accepting |

What was read, in the texts' own words (names and dates aside):

- **NBA game winner.** Polymarket: "If the Jazz win, the market will resolve to 'Jazz' ... The result will be
  determined based on the final score including any overtime periods"; "If the game is canceled, does not start
  by [two days after the scheduled day, 11:59 PM ET], or is abandoned with no official result by that time, this
  market will resolve 50-50". Kalshi: "If [team] wins the [game] originally scheduled for [date], then the market
  resolves to Yes"; "If the game is postponed but begins within 48 hours of its originally scheduled start time,
  the market will remain open and resolve based on the official final result. If the game is cancelled or not
  started within 48 hours of its originally scheduled start, all markets will resolve to a fair price." A played
  game pays alike. A game not played pays one half on Polymarket and a "fair price" on Kalshi, and the two
  windows differ by up to about two hours.
- **NBA spread.** Polymarket: "will resolve to 'Lakers' if the Lakers win the game by 8 or more points.
  Otherwise ... 'Hornets'"; the same no-result words. Kalshi: "If [team] wins by more than 7.5 points ..."; the
  same 48-hour words. For a line ending in one half the two are the same event. A whole-number line is NOT in
  this entry.
- **Premier League champion.** Polymarket: "will resolve according to the team that wins"; impossible for a
  team: "No"; "cancelled, postponed after June 14, 2027, 11:59 PM ET or ... no winner declared within that
  timeframe: 'Other'". Kalshi: "If [club] is the 2026-27 English Premier League champion, then the market
  resolves to Yes" and nothing on a season cut short.
- **Democratic nomination 2028.** Polymarket: "wins and accepts the 2028 nomination ... Any replacement of the
  democratic nominee before election day will not change the resolution". Kalshi: "wins and accepts the
  nomination". (The study T53 called this pair different from Polymarket's TITLE; its text says the same.)

## Known NEAR families (from the study T53; no entry needed for the rule, listed so that nobody maps them SAME)

Crypto price levels "by a date" (Kalshi: a trimmed mean of an index, strictly above; Polymarket: "reach", another
source): three such pairs had already settled differently on 2026-11-18. Crypto price "on a date" (another
moment and source; a tie pays opposite ways).

## Not yet read (no copy until an entry exists)

Every other series: the other sports' game winners and spreads (a tie or a shoot-out changes the answer and must
be read for each sport), totals, the NBA and Super Bowl season markets, and everything else.
