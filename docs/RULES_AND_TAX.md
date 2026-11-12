# Tax and platform rules the trader must respect

Compiled 2026-11-12 from primary pages (IRS, FINRA, Webull's and Kalshi's own documentation) by a research
pass, then spot-checked: the FINRA day-trading change below was confirmed on finra.org the same day, and the
wash-sale and holding-period items match IRS Publication 550. This is a reference for software, not tax or
legal advice. V = read on the linked page that day. U = from a search summary, a secondary source or
memory, and not to be relied on until read. Items marked NOT FOUND or UNSETTLED are open.

Two things were added after the pass:
- Selling part of one purchase at a loss is not a wash sale by reason of the rest of that same purchase;
  it becomes one only if additional shares are bought inside the window (Rev. Rul. 56-602). U: the
  ruling's own text could not be opened (the page refused); this rests on a search extract of the ruling
  and on The Tax Adviser, March 2012.
- Nothing here has been checked for state taxes.

The legal parameters the tax ledger actually uses live in `config/tax_rules.json`, each with its source
and the date it was verified, or null.


## 1. Wash sales (stocks and securities)
- V Window: 30 days before to 30 days after the loss sale (61 days total). IRC 1091 and Pub 550 "Wash Sales".
  Pub 550 (2025), page updated 30-Apr-2026: https://www.irs.gov/publications/p550
  Statute text: https://www.law.cornell.edu/uscode/text/26/1091 (no currency date shown)
- V Counts as a wash if you buy, acquire in a fully taxable trade, or acquire a contract or option to buy substantially identical stock/securities, or acquire it for your IRA/Roth IRA (Pub 550).
- V Spouse, or a corporation you control, buying substantially identical stock also makes a wash sale (Pub 550).
- V Statute: "stock or securities" includes "contracts or options to acquire or sell" them (Cornell 1091).
- V Pub 550: rules apply to losses on contracts/options to acquire or sell stock; they do not apply to commodity futures or foreign currencies.
- V Disallowed loss is added to basis of the replacement; the replacement's holding period includes the holding period of the shares sold (Pub 550).
- V Share-matching: if more or fewer shares are bought than sold, match in the order bought, first shares first (Pub 550 examples).
- V "Substantially identical" is facts-and-circumstances; stock of different corporations is ordinarily not identical (Pub 550).
- V Short sales: also covered, with a special "complete" date rule (Pub 550).
- V Broker 1099-B box 1g is MANDATORY only when the loss security was a covered security AND the replacement had the same CUSIP AND was bought in the same account.
  Brokers MAY report other disallowed losses (e.g. repurchase in a different account). Basis is raised by the box 1g amount.
  Source: Instructions for Form 1099-B (2026), page updated 30-Apr-2026: https://www.irs.gov/instructions/i1099b
- V Pub 550: the loss is still not deductible "even if it is not reported on Form 1099-B". Report on Form 8949 with code W in column (f).
- V 1099-B instructions list cases where a broker need not apply wash rules: replacement moved to another account before the wash sale, bought in another account and later transferred in, held as separate accounts, or a section 475 election notice.
- So: not caught by a broker report (but still disallowed by law): other brokerage accounts, IRA purchases, spouse purchases, non-identical-CUSIP but substantially identical securities, options/contracts to acquire. (Derived from the V facts above, not a separate IRS sentence.)

## 2. Wash sales and crypto / Form 1099-DA
- V Statute text covers "stock or securities" only; digital assets are "property" for tax (Pub 550 ch. 4 "Digital assets"). I found no sentence in Pub 550 extending wash-sale rules to digital assets (keyword search of the full page text).
- V Form 1099-DA box 1i (wash sale loss disallowed) is for TOKENIZED SECURITIES treated as stock/securities under 1091, same account and same CUSIP.
  Instructions for Form 1099-DA (2026), updated 30-Apr-2026: https://www.irs.gov/instructions/i1099da
- V Bills, not law: House Ways and Means approved H.R. 10357 (Digital Asset Tax Certainty Act) 38-5 on 16 Sep 2026; it would apply wash-sale rules to digital assets.
  https://waysandmeans.house.gov/2026/09/16/historic-digital-asset-tax-legislation-advances-to-keep-america-the-crypto-capital-of-the-world/ (page reports committee approval only)
- U Bill details (digital assets other than qualified USD stablecoins; exclusions) come from a search summary of the introduced text and JCT description; I could not read the PDFs' wording myself. https://waysandmeans.house.gov/wp-content/uploads/2026/09/JCT-Description-of-HR-10357.pdf
- U No floor passage or enactment found as of searches in Sept-Nov 2026. congress.gov returned 403, so the Nov 2026 status is NOT confirmed from the primary tracker. A separate bill H.R. 9172 (June 2026) appeared only in a search summary.
- Conclusion for tax year 2026: no enacted law found that applies wash-sale rules to ordinary crypto. Status of "none enacted" rests on absence of evidence plus the committee page; treat as not independently confirmed after 16 Sep 2026.
- V 1099-DA for 2025 sales: gross proceeds only; basis reporting starts for certain transactions on/after 1 Jan 2026; penalty relief for good-faith 2025 filings. https://www.irs.gov/filing/digital-assets (page 02-Sep-2026)
- V 1099-DA instructions: for sales after 2025, basis (box 1g) is mandatory only for COVERED securities = digital assets acquired after 2025 in a custodial account held there until sold. Earlier-acquired or transferred-in assets are noncovered (box 9).
- V De minimis (1099-DA instructions): $10,000/yr designated stablecoin sales; $600/yr for PDAP sales and for specified NFTs.
- V Digital assets get the same holding-period principles as other property (Pub 550).

## 3. Holding period
- V More than 1 year = long-term; 1 year or less = short-term (Pub 550).
- V Count begins the day AFTER acquisition; the day of disposition is part of the period. Example: bought 31 Jan 2024, sold 29 Jan 2025 = short-term; sold 6 Feb 2025 = long-term.
- V Securities on an established market: starts the day after the TRADE date of purchase, ends on the trade date of sale; the page warns not to confuse trade date with settlement date.
- V Wash-sale replacement shares tack on the holding period of the shares sold (Pub 550).
- V Section 1256 gains/losses are 60/40 "regardless of how long" held (Pub 550).

## 4. Capital losses and NIIT
- V Annual deduction = lesser of $3,000 ($1,500 married filing separately) or net loss (Pub 550 "Limit on deduction").
- V Unused net loss carries to later years until used up and keeps its long/short character; long-term carryover offsets long-term gains first.
- V NIIT: 3.8% of the lesser of net investment income or MAGI over threshold. Thresholds: $250,000 MFJ/qualifying surviving spouse, $125,000 MFS, $200,000 single/head of household (Pub 550 "NIIT", 2025 edition).
- U Whether these dollar figures are indexed or changed for tax year 2026 was not checked; the page is the 2025 edition.

## 5. Section 1256 and event contracts
- V Section 1256 contract = regulated futures contract, foreign currency contract, nonequity option, dealer equity option, dealer securities futures contract. Excludes swaps and "similar agreements" (Pub 550; statute https://www.law.cornell.edu/uscode/text/26/1256 (b)(2)).
- V Year-end: open 1256 contracts are treated as sold at fair market value on the last business day of the tax year; gain/loss is then 60% long-term, 40% short-term; reported via Form 6781.
- V Regulated futures contract requires trading on a "qualified board of exchange" (a CFTC-designated contract market qualifies, Pub 550).
- V No IRS guidance on prediction-market contracts as of 16 Jun 2026 per Thomson Reuters Tax: "has not addressed prediction market contracts"; it says CFTC registration alone does not confer 1256 status and the swap exclusion could apply.
  https://tax.thomsonreuters.com/news/irs-silence-on-prediction-market-winnings-to-cause-confusion-as-world-cup-begins/ (dated 16 Jun 2026; secondary source, but read)
- U Candidate treatments named by commentators: 1256 (60/40), capital gain, gambling income. I found no IRS notice, ruling or form instruction on event contracts (IRS news-release index URL returned 404; not searched further).
- UNSETTLED: tax character of Kalshi/Webull event contracts. Cited as unsettled by Thomson Reuters Tax (above), Baker Tilly, White & Case (named inside that article).
- V Webull docs label some event contracts "Cleared Swaps" (sports). Kalshi help page says it cannot give tax advice (below).

## 6. Day trading, margin, settlement
- V FINRA Regulatory Notice 26-10 (20 Apr 2026): new intraday margin standards replace the day trading margin requirements "in their entirety", including the pattern-day-trader day-count designation and the $25,000 minimum.
  Effective 4 Jun 2026; members may phase in until 20 Oct 2027. https://www.finra.org/rules-guidance/notices/26-10 (text read from the PDF https://www.finra.org/sites/default/files/2026-04/Regulatory-Notice-26-10.pdf)
- V Mechanism: member computes an "intraday margin deficit" per margin account (not good-faith or portfolio-margin accounts) on any day with an IML-reducing transaction (e.g. a purchase). Regular maintenance margin is unchanged. Real-time blocking is allowed, not required.
- V FINRA investor page (20 Apr 2026): $2,000 needed for leveraged trading; below that, unleveraged only; no PDT label; "Your firm has the authority to impose higher requirements". https://www.finra.org/investors/insights/intraday-margin-requirements
- U SEC approval date 14 Apr 2026 (from Webull help text and search summaries; the SEC order PDF https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf was not opened).
- V Webull help "Day Trading Rules" (https://www.webull.com/help/faq/10954-Day-Trading-Rules; no page date, footer 2026): still describes $25,000 / 3 day trades per rolling 5 business days / PDT flag at 4+ / EM call, says new standard effective 4 Jun 2026 "until then" old rules apply.
  Sibling page 10954-What-do-I-need-to-know says Webull "has not yet announced implementation". Neither page is dated.
- NOT SETTLED: whether Webull has switched its own account logic. Webull press release (15 Apr 2026, via https://technode.global/prnasia/webull-unlocks-active-trading-for-all-eliminating-the-pattern-day-trade-25k-minimum-balance-and-trade-count-restrictions/) was conditional on regulator approval. I found no dated post-June Webull statement.
- V Webull API doc: event contracts "are not subject to Pattern Day Trader rules" (https://developer.webull.com/apis/docs/trade-api/event-contract.md, no date).
- V Settlement: SEC/FINRA T+1 since 28 May 2024. FINRA page dated 07 May 2026: "next business day after a trade". https://www.finra.org/investors/insights/understanding-settlement-cycles
- V Webull "Cash Buying Power" (https://www.webull.com/help/faq/11159-Cash-Buying-Power, no date): stocks/ETFs settle next business day if traded before 8:00 PM ET; crypto settles immediately; day trading with unsettled funds can trigger a Good Faith Violation; a free ride violation excludes unsettled funds from buying power for 90 calendar days.
- U Webull GFV escalation (3rd violation = settled-funds-only 90 days; 4th = liquidation only 90 days; expiry about 13 months) and a conflicting "T+2" older page appear only in a search summary; the GFV page itself (help/faq/10774) rendered no article text to me.
- NOT FOUND from a primary page: Reg T text for good-faith and freeriding (investor.gov returned 403; not read).

## 7. Webull OpenAPI (https://developer.webull.com/apis/docs/trade-api/overview.md ; changelog latest entry 2026-11-07)
- V Products: stocks (US, fractional, short), options, futures, crypto spot, event contracts.
- V Stock order types: MARKET, LIMIT, STOP_LOSS, STOP_LOSS_LIMIT, TRAILING_STOP_LOSS (DAY only). MOO/MOC/LOO are institutional only. TIF: DAY, GTC. OTO/OCO/OTOCO for equity only. https://developer.webull.com/apis/docs/trade-api/stock.md
- V Extended hours: field support_trading_session = CORE (9:30-16:00 ET) | ALL (pre + after hours) | NIGHT. Required on stock orders. Feature matrix marks extended-hours and overnight supported for stocks only.
- V Fractional: stocks accept decimal quantity (QTY) or AMOUNT (total_cash_amount must be less than one share's price). Crypto and event contracts also fractional per the matrix.
- V Crypto via API: needs a crypto account opened in the app; MARKET, LIMIT, STOP_LOSS_LIMIT; TIF DAY/GTC/IOC; 24/7; max $100,000 per trade, $200,000 total pending buys; minimum $2.00 ($1.00 NY/Guam/N. Mariana); sell must leave position at least $2. https://developer.webull.com/apis/docs/trade-api/crypto.md
- V Event contracts via API: LIMIT only (best-available price through event_trade_mode); TIF DAY, GTC, IOC, GTD, FOK; needs an Event account and signed agreements; up to 2 decimals in quantity; hours vary by type (e.g. crypto events Mon-Fri 08:00-18:00 ET). https://developer.webull.com/apis/docs/trade-api/event-contract.md
- V Rate limits (Production, per app_key per endpoint): place order 600/60s; replace 600/60s; cancel 600/60s; batch place 150/60s; preview 150/10s; account list 10/30s; balance 2/2s; positions 2/2s; open orders, order history, order detail 2/2s; instrument lookups 60/60s. Exceed = HTTP 429; repeated = possible temporary IP block. https://developer.webull.com/apis/docs/rate-limits.md (no date; added to changelog 2026-08-14)
- U Cancel/replace work only for orders placed via API: from a search summary of a regional Webull FAQ, not read on the US page.
- V client_order_id: max 32 chars, unique per account. No extra fee for API trading (FAQ). https://developer.webull.com/apis/docs/trade-api/faq.md
- V Tax forms: Webull help (https://www.webull.com/help/faq/10989-Tax-Document-Availability, year not named, footer 2026): consolidated 1099 first run est. 31 January, first correction run late Feb-early Mar; 1099-DA est. 17 February; all "estimated".
- U Two 1099-DAs possible (Webull Pay and/or Bakkt) comes from a search summary only.

## 8. Kalshi
- V Rate limits (https://docs.kalshi.com/getting_started/rate_limits, no date): per-second tokens, Basic 200 read/100 write; Advanced 300/300; Expert 600/600; Premier 1,200/1,200; Paragon 2,400/2,400; Prime 4,800/4,800; Prestige 12,000/9,600. Advanced is requested via API; Expert+ is earned by trailing 30-day volume share. Batch create costs 10 tokens per order, batch cancel 2 per order.
- V Create Order V2 (https://docs.kalshi.com/api-reference/orders/create-order-v2.md): ticker, side bid/ask (YES leg), count string (0.01 step), price dollar string; time_in_force required: fill_or_kill | good_till_canceled | immediate_or_cancel; self_trade_prevention_type required: taker_at_cross | maker; post_only, reduce_only (needs IOC), cancel_order_on_pause, expiration_time (GTC only), client_order_id optional. Default cost 10 tokens/request. Page shows no limit/market field.
- V Changelog (https://docs.kalshi.com/changelog): entries dated 12 Nov 2026 and also 19 Nov 2026 (a future date as of today; read as listed). 19 Nov: deprecated market field expiration_time removed (use latest_expiration_time).
- V Fee rounding (https://docs.kalshi.com/getting_started/fee_rounding, no date): trade fee = model fee rounded up to $0.000001; rounding fee and rebate accumulate per order.
- U Fee formula: July 2026 schedule ("7.7.26 update") is reported as 0.07 x contracts x price x (1 - price), rounded up, multiplier can differ by series (0.0175 variant); maker fees apply to resting orders when filled on some products. Only from a search summary: https://kalshi.com/docs/kalshi-fee-schedule.pdf returned HTTP 429 to me, so NOT READ. Treat the formula as unconfirmed.
- Position limits: NOT FOUND on a current Kalshi page. V history (via CFTC filings found in search, U for current rulebook): 2021 rulebook had a $25,000 cap; later versions moved limits to each contract's Terms and Conditions, with "Position Accountability Levels". A Sep 2026 filing listed "$25,000 per strike" for one contract (search summary, U). Per-market limits must be read from each market's rules.
- V Tax forms (https://help.kalshi.com/en/articles/13823849-what-tax-documentation-does-kalshi-provide, dated 22 Jun 2026): forms go to users who reach IRS thresholds (no dollar amounts); lists 1099-INT, 1099-MISC, 1099-B (crypto transfers), 1099-DA (via ZeroHash); FIFO used for P&L. The page does not say which form covers event-contract trading gains and does not state tax treatment ("cannot give members advice").

## Not searched / not read
- State taxes; Form 8949/6781 instructions; straddle rules; 475 mark-to-market election detail; constructive sales.
- Regulation T / FINRA text on good-faith and freeriding (primary); SEC approval order.
- Options, futures and margin interest rules; Webull fees; Webull margin FAQ beyond day trading.
- Kalshi live rulebook text and per-market limits; Kalshi fee PDF (429); Kalshi/Webull relationship; congress.gov status (403).
