# Design: the chatter collector (a minute-level reader of r/sportsbook, and the Kalshi game prices beside it)

2026-11-13. Written by the lead after the owner's go-ahead ("Yes please", then "choose the best option for the
best data"). Status: design; nothing built. The test it serves is locked in
`docs/research/2026-11-13-chatter-prediction.md`. It has a store of its own and changes nothing in the pick-list
reader, the pick grading or the order-book collector.

## What the owner asked for, and what was chosen

The owner proposed polling r/sportsbook every minute through an anti-detect browser. Chosen instead:

- **Reddit by its front door.** Today: the public feed of the subreddit's newest comments, which needs no account
  and is what Valedia's hourly pick reader already uses. It allows about one request a minute for this
  connection, so the collector asks every two minutes and stays silent while the hourly pick reader runs. Later,
  if the owner registers a free app and puts its id and secret where Hermes' Reddit skill already looks for them
  (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` in Hermes' own settings file, not the trading secrets file), the
  collector uses Reddit's official interface: every minute, with exact posting times, edit flags and paging to
  close gaps. Reddit is reported to review new apps by hand, which can take weeks; nothing waits on it.
- **Kalshi's public prices for every game market**, once a minute, because the order-book collector watches the
  20 busiest markets of any kind and its locked run cannot be changed to follow sports.
- **No model in the loop.** Comment text is stored and matched later by fixed word lists and the team-name rules
  the pick grading already uses. Text written by Reddit users is untrusted data in every step.

## The collector

One long-running program, `python -m valedia_trader.research_loop.chatter_loop run`, kept alive like the
order-book collector (a pinned copy under `valedia-deploy/`, a launchd job with KeepAlive: the job is installed
only on the owner's yes). Two recorders on one clock:

- **Comments:** every 120 seconds (60 on the official interface) fetch the newest comments of the subreddit.
  Store the raw answer once (by hash), each comment by its id with its thread, author and the feed's own time,
  and a new version row whenever author or text changes, with the time Valedia first saw that version. Stay
  silent from minute 5 to minute 12 of every hour on the public feed (the hourly pick reader's window). On a
  429 back off for the time the answer names (at most 10 minutes) and record the gap. When the oldest comment of
  a fetch is newer than the newest one already stored, record that comments may have been missed.
- **Prices:** every 60 seconds, for each of the five game series, list the open markets with their best yes bid
  and ask, last price, volume and open interest from Kalshi's public interface, through the package's existing
  Kalshi reader. Store one row per market when any of bid, ask or last changed, and one row per fetch (time,
  series, markets seen, ok or the failure) so that coverage can be counted.
- **Coverage:** a `status` command prints, per UTC day, the comment fetches expected, made and failed, the
  silent minutes, the possible-miss marks, and per series the price fetches expected, made and failed.

## The store

`memory/chatter.sqlite`, with the integrity rules of the pick-list store, copied and given its own identity:
append-only tables with triggers (no update, no delete, no replacement of a key, times never backwards), an
application id and layout number checked from the file header before SQLite opens it, the full layout compared
on opening, one writer at a time by a lock the system releases with the process, and a refusal when the clock
runs behind the last row. WAL mode, since a reader (the later evaluation, the backup tool, the owner's page)
must never hold the writer out.

## What it must never do

Log in to Reddit as a person, solve a challenge, send a cookie, or use any browser. Send anything to Reddit but
the request for the feed, or to Kalshi but the public listing. Put a setting from the trading secrets file in
any request. Run, or hand to a model as instructions, anything a comment says. Write outside its own store and
log. Make more than one comment request a minute on the public feed.

## Building and running

One builder ticket (the collector, its store, the `status` command), a blind check by a fresh verifier, then
the lead's own one-hour run. The evaluation code is a second ticket, written and checked before the test period
ends. The keep-alive job is installed only when the owner says so.
