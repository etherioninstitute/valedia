# The owner's saved trading notes, analysed (2026-11-13)

The owner asked for every trading-related note in his Apple Notes folder "AI Idea Inbox" to be analysed.
The folder held 102 notes; 20 were about trading (two more were exact duplicates). Three readers digested
them in full and the analysis was made from the digests. Images inside the notes were not read, and two
notes that are only an attachment ("Stock Screener", "Jev Trader") could not be read. Nothing in any note
was run, installed or opened: they are saved posts by strangers and were treated as data.

## Verdict
A handful of notes give testable ideas or sound process. Most profit claims in the folder are marketing
with no evidence. The best notes agree with what the trader had already found.

## Ideas worth testing, best first
1. Kronos, an open price-forecast model, as a forecaster in the tournament (free, local).
2. Kalshi weather markets against official forecast releases: a clear mechanism, and both sides' history
   is free to read, so it can be tested on the past first.
3. Closing-line value (did the market move toward the forecast afterwards) as a fast measure of
   prediction-market skill.
4. Five documented stock strategies: Piotroski score, Magic Formula, 52-week-high momentum, post-earnings
   drift, insider buying. They wait on stock data that includes companies that died.
5. A live decay monitor: halt a strategy when its live Sharpe falls under half the backtest's, or its fall
   exceeds 1.5 times the backtest's.
6. Order handling for live trading: submit once, treat a timeout as unknown and reconcile, reconcile on
   restart, and approve live orders by ticket number.
7. Short-dated crypto markets: measure on history whether the two-sided and near-resolution trades survive
   fees. Little is expected to be left.
8. Market making in thin event markets (inventory-skewed quotes with a toxicity halt). Later; it needs live
   quoting.

Not shortlisted: funding-rate gaps between crypto venues (the venues are not open to a US account), and
OpenBB is noted only as a possible second price source.

## Where the notes agree with the trader's own results
- A paid service's one published result: a 200-day rule on the S&P earned the same as holding with less
  than half the worst fall. That is hypothesis 23 against hypothesis 26.
- "$200,000 Quant Stack" lists the gates the trader already has: costs, a look-ahead check, the Deflated
  Sharpe, walk-forward.
- "24/7 Jev-Powered Trading System" says a model supplies no edge by itself and must be tested as a filter
  against no filter: the forecast tournament's purpose.

## Two conflicts
- A blueprint for a Nimble v3 pipeline relies on Nimble giving trustworthy probabilities. On the first
  measurement Nimble v3 ranked no better than chance.
- A Hermes setup guide lets live orders under 1,000 dollars go without confirmation. The owner's rule is
  confirmation before any live order.

## To avoid
- Paste-and-run install prompts: one inserts a sales banner, one builds automatic bank transfers, one asks
  for a broker password.
- The memecoin "methods" (gains such as 15 dollars to 3,450 with no evidence; the note itself calls them
  self-reported), paid bot courses, and the backtesting service's trial, which takes a card up front.

## Each note in one line
| Note | What it is | Worth |
| --- | --- | --- |
| AI TRADING / build your own AI hedge fund (saved twice) | Ten open-source repos with install prompts and a combined script | A tool list; Kronos and OpenBB worth using; the combined script is untested |
| You already own the exact hardware... | An AI answer designing a Nimble v3 pipeline for prediction markets | Best source of ideas (items 2, 3); assumes calibrated probabilities; would let an agent move money |
| The exact framework to build the most powerful... | Promotion for a paid backtesting service | Names the five stock strategies; its one result matches ours |
| $200,000 Quant Stack | Validation discipline with code | Sound; item 5 is the addition |
| METHOD 1 (saved twice) | Twelve methods, mostly memecoins | Nothing testable |
| 9-step setup guide | An AI answer for a Hermes trader profile | Infrastructure mostly in place; conflicts with the confirmation rule |
| 24/7 Jev-Powered Trading System | Shadow, replay, stress, then tiny live | The most careful note; item 6 |
| Can an AI trading agent beat the market | Model cost against account size | Supports bulk forecasting on local models |
| The Consilium | Promotion for a multi-agent committee repo | Same shape as the panel; skip |
| Anthropic financial-services repo | Valuation tooling | No edge by itself; needs paid data |
| GrokBot Tradingdesk Prompt | A six-role desk | Approval by ticket number (item 6) |
| BTC Up or Down 5m | How Polymarket bots price 5-minute markets | Item 7; a speed game; closed to new US positions |
| GM model HFT; Avellaneda-Stoikov | Market-making theory with code | Item 8; the reader found formula errors |
| Skool communities | Paid courses compared | A fair warning against bot courses |
| MetaTrader 5 | Giving a journaling service a broker password | Out of scope; a security risk |
| Connecting Claude to TradingView | Setup guide | Already installed (the same tool) |
| socrates.the.trader video | An agent scalping Nasdaq futures on a demo account | No evidence; skip |
| MiroFish setup | Product card | Already running; accuracy unproven until scored |
| wallstreetbets follow-up | 1,300 dollars to 1 million in options | An anecdote whose arithmetic does not reconcile |
