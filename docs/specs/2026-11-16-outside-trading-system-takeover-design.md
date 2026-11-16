# Taking over what an outside trading project does: design (2026-11-16)

The owner asked that Valedia take over, completely and cleanly, everything an outside project does (a public
repository that calls itself "Elite-Bot", read at commit `24a97ac`), leaving out its Indian market parts, with paper
and live as one switch as everywhere else in Valedia.

## The rule that shapes this

The repository has **no licence**, so there is no right to copy any of it. Nothing from it is copied, run or loaded
(its two saved "brain" files are pickles, which can run code when opened: they are never opened). Each function is
rebuilt in Valedia's own code from a written inventory of behaviour (`.foreman/tickets/S38-*`, four read-only scouts),
under Valedia's rules: orders only through the reviewed order step, market data from sources Valedia already uses,
no model with a route to a broker, nothing that reports usage anywhere, tests first.

## What the outside project is

Seven separate bot programs, each with its own copy of the same idea and its own order code:

| Part | What it does |
| --- | --- |
| Pairs signal | price ratio A/B, 20-bar mean and deviation, z-score; open at 1.5 (1.8 in two bots), close inside 0.5 |
| Universes | 8 US stock pairs, 6 crypto pairs, 8 forex pairs, 31 US single stocks, 16 single currencies (and Indian lists) |
| Quant library | GARCH(1,1) volatility tiers, a 3-state hidden Markov regime, a Kalman hedge ratio, half-Kelly sizing, keyword sentiment on news headlines, a scraped fear-and-greed number |
| AI review | four agents on a hosted Llama model (through Groq) that answer BUY, SELL or WAIT before a trade |
| Learning | a table that learns "trade or skip" from past results, saved as a pickle |
| Single stocks | RSI(14) below 30 buys, above 70 sells |
| Exits | fixed stop and target, a stop that trails total profit, a daily goal and a daily stop in dollars |
| Orders | Alpaca for stocks and crypto, "simulated" for forex (Alpaca has no spot forex), Upstox for India |
| Dashboard | a web page with account, positions, orders, a chart, a pair scanner, start and stop buttons, a backtest form, key entry, a frozen-symbol list |

### What it gets wrong, which the rebuild must not reproduce

- **Failure approves.** When data is missing or any step raises, its quant check returns "approved, 10% size".
- **Its paper switch is not a switch.** Every bot hard-codes the paper account; the page's live toggle only changes
  what the page reads and what its close-all button hits.
- **No test anyone could trust.** Its backtest fills at the same bar that made the signal, charges no fees, never
  closes the last position, and measures a ratio change as if it were dollars. Its learning table is trained and
  "tested" on the same two years, on a different spread formula from the one used live, and is never saved in live use.
- **Unseeded and fitted on the day it predicts.** The regime model has no seed and labels the last bar from a fit
  that includes it.
- **The AI can turn the trade around.** In its main bot the model's BUY or SELL replaces the side the mathematics
  chose.
- **Half-open pairs.** Two market orders with no check between them; when the second fails, the first stays.
- **Open to anyone.** No sign-in on any route, including "set keys", "go live", "start a bot" and "close everything";
  keys kept in the browser; a real-looking broker key pair written into a source file (noted by kind only, never used).
- Its sentiment and fear-and-greed numbers are collected and never used in a decision; the keyword match is by
  substring ("win" matches "window").

## Decisions

**D1. One engine, many strategies.** The seven bots become strategy definitions in Valedia's one shape. They are
backtested by the one engine, tested by the one gauntlet (prediction first, a sealed period, walk-forward, doubled
costs, a rising bar per attempt), paper-traded by the one paper book and sent by the one order step. No bot has its
own order code, so there is nothing separate to switch.

**D2. Pairs are a second kind of strategy.** Today a strategy is one score per symbol, and each position is opened
and closed on its own. A pair is one position with two legs that must open and close together, with the stop on the
pair's result. So: a new module `research_loop/pairs.py` with `PairsStrategy`, written as a JSON spec with
`"kind": "pairs"` (a closed set of parameters with bounds: nothing a model writes is executed). Two small hooks in
`strategy.py` let every existing consumer use it unchanged: `load_strategy` builds it from the spec, and `run_rules`
hands over to a strategy's own `walk`. A pair trade counts as one trade in the statistics.

**D3. Both legs in research, the long leg where shorts are not possible.** `"legs": "both"` is the market-neutral
pair. `"legs": "long"` holds only the leg the signal says is cheap. Three facts make the second one necessary:
the live account class is a cash account (no shorts); the order step refuses a negative position; spot crypto is not
shorted in this system. Both forms are tested; only the long form can reach the broker today. Before any strategy
that holds a short is promoted to the paper book, the tax ledger must learn short sales or that strategy gets a book
of its own (today one short fill stops the tax notes for the whole account).

**D4. The hedge ratio is a choice in the spec, tested like any other:** `ratio` (the outside project's own), `log_ratio`,
a rolling regression, and a Kalman filter (new, `quant_engine/kalman.py`, with the textbook covariance update and
noise that follows the data's scale; the outside one tracks noise because its noise terms are fixed numbers).

**D5. Its quant checks become gates that fail closed, each kept only if it earns its place.** Valedia already has
the mathematics (GARCH, a walk-forward seeded regime model, Kelly with a floor of zero). What is new is using them
as optional gates in a pairs spec: a volatility gate, a regime gate, and a Kelly cap computed from the strategy's own
out-of-sample trades (zero when the edge is not positive: the outside floor of 5% is not kept). Annualisation follows
the bar length and the market's hours, not one constant. Each gate is tested as with and without; a gate that does
not help out of sample is recorded as failed and left off. Missing data means no trade.

**D6. The AI review is Valedia's own panel, and it can only say no.** The paper trader already consults the decision
panel on risk-adding orders (`panel_mode` off, shadow or veto) and scores every verdict against what happened. That is the
home for the outside project's four agents: local models, facts computed in code, a veto that never originates a
trade and never changes its side. No hosted Llama, no CrewAI. A model review cannot be backtested honestly, so it
earns trust only from its forward record, with and without.

**D7. The learning table is rebuilt as an honest one.** A small table over (z bucket, regime, volatility tier) that
learns "take or skip", trained walk-forward only on trades that had already closed, stored as JSON, seeded. It has
to beat the plain rule out of sample or it stays off.

**D8. Paper and live.** One command, one flag: `order-step` is practice by default and `--live` is real money. The
flag opens only when the owner's own files are in place (the confirmation, the go-live numbers, an orders-per-day
cap), and live sends only an approved ticket (`--propose`, then `--approve`). `order-step --live --check` prints each
lock and what is still missing. Every strategy in this document reaches a broker only through that step, so each
inherits the switch with no code of its own. What can actually be live on day one is set by venues (D9).

**D9. Markets.**

| Market | History for tests | Practice | Live today | What live needs |
| --- | --- | --- | --- | --- |
| US stocks | daily, the source already used | paper book, then the Webull test account | yes, long leg only, through the existing step | nothing new for long; a margin account and short support for both legs |
| Crypto | daily for 8 coins, the source already used; exchange history with dead coins is roadmap 1.7 | paper book | no | the order step taught crypto (all-hours session, fractional units, Webull's crypto spread sourced), and the owner's choice of venue |
| Forex | daily rates, the source already used; no volume, so costs are assumptions labelled as such | paper book | no | a broker that trades spot forex: the owner's choice; none is connected |

What the first download showed (2026-11-16, daily closes into `artifacts/data_pairs_us`, `data_pairs_crypto`,
`data_pairs_fx`; counts only, no strategy was run):

- US stocks: 16 symbols from 2000 (or their listing), no gaps, nothing implausible.
- Crypto: 8 coins. SHIB has 217 days stored as a zero price (too small for the source's precision) and AVAX and
  SHIB have gaps; pairs that include them start late and need the zero days treated as missing.
- Forex: the free series has **bad prints**. Nine of the fifteen series show a one-day move above 8%, among them
  17% in EUR/USD, 18% in USD/JPY and 52% in USD/NOK, none of which happened (the Swiss franc's 15 January 2015 is
  the one real case). A pairs rule would "profit" from every one of them. So forex tests use the European Central
  Bank's daily reference rates (one official snapshot a day since 1999, every cross computed from the euro rates),
  with the free series kept only as a cross-check. The bank publishes them for information and discourages their
  use for transactions: they are a research series, not a price anyone traded at, and results on them are labelled so.

**D10. Daily bars first, hourly second.** The outside bots act on hourly bars polled every minute or two. Valedia's
engine, paper book and order step work on daily closes. The daily form is built and tested first because every part
already exists for it. Hourly needs its own bar store (the owner's read-only Alpaca data keys cover stocks and
crypto), an hourly backtest and an intraday paper step, and has to show it beats daily after costs.

**D11. The dashboard becomes a section of the owner page.** A "Pairs" section: each pair's z-score, hedge ratio,
half-life, state and last result, read-only, in the one self-contained page that makes no outside request. Live
viewing is the Live page in Hermes. Not rebuilt: key entry in a browser, start and stop routes, a close-everything
button, a public deployment.

**D12. A frozen-symbol list** the owner can edit, honoured by the paper trader and the order step, replaces the
outside `blacklist.json`.

## What is deliberately not rebuilt

The Indian market (adapter, symbols, hours, Upstox, the India-clock session router); Alpaca as a route for orders;
the hosted model and CrewAI; the scraped fear-and-greed number (one source is a third-party address with a token in
it, the other is a page scrape); pickle files; the web deployment; the hard-coded key pair; reset scripts that cancel
and close everything when merely imported.

## Slices, in order

| # | Slice | Done looks like |
| --- | --- | --- |
| T38-1 | Pairs as a strategy kind | a pairs spec loads, backtests through the gauntlet's own calls and steps in the paper trader; causal by test; a pair counts as one trade |
| T38-2 | Kalman hedge filter | matches recursive least squares when the state does not move; tracks a step change; causal by test |
| T38-3 | Kalman hedge in pairs; volatility, regime and Kelly gates; RSI as a rule score | each gate has a with-and-without test and fails closed |
| T38-4 | Prices for the three universes, then the first tests | predictions written first; results in the idea ledger, pass or fail |
| T38-5 | The learning table | beats the plain rule out of sample, or is recorded as failed |
| T38-6 | Sentiment from the stored company news (local only) | a token-matched score by the date learned; a feature first, a gate only if it helps |
| T38-7 | Owner page "Pairs" section and the frozen-symbol list | shown read-only; a frozen symbol opens nothing new |
| T38-8 | Shorts in the paper book and the tax ledger | a short fill no longer stops the tax notes |
| T38-9 | Crypto in the order step; a forex venue | after the owner's choices |
| T38-10 | Hourly bars | an hourly store, backtest and paper step; compared with daily after costs |

## The owner's decisions (none blocks T38-1 to T38-7)

1. **Crypto live:** Webull's crypto account (the adapter already knows the account class) or another venue.
2. **Forex live:** which broker, if any. Until then forex is research and paper only.
3. **Both legs live:** a margin account, or stay with the long leg.

## What to expect, said plainly

The outside pair list is sixteen of today's largest companies chosen with hindsight, and its own evidence is not
evidence. Each rebuilt strategy faces the same tests as every other idea here, and several ideas a week fail them.
"Taken over" means Valedia can do all of it and judges it honestly; it does not mean any of it is traded before it
passes, has a paper record and the owner says go.
