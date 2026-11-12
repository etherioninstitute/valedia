# Data sources and venues beyond the funds (researched 2026-11-12)

A digest of a web research pass. V = read on the linked page or by a live call that day. U = from a search
snippet, a secondary source or memory, to be checked before relying on it.

## Webull OpenAPI (US)
- V: the Trading API supports stocks, options, futures, crypto and event contracts; fractional shares and short selling for stocks. https://developer.webull.com/apis/docs/trade-api/overview
- V: a test environment exists (host api.sandbox.webull.com); it can be used while the API application is under review (about 1-2 business days). No extra charge for API access; market-data subscriptions are separate. https://developer.webull.com/apis/docs/faq
- Not found: whether the test environment simulates fills, and for which asset classes.

## Prediction markets
- V: Kalshi's market data needs no key. Base https://api.elections.kalshi.com/trade-api/v2 ; GET /markets, /historical/markets, /historical/trades, /historical/markets/{ticker}/candlesticks all answered without one. About 20 reads a second on the basic tier. https://docs.kalshi.com/getting_started/rate_limits.md
- V: Kalshi has an official trading API and a demo environment. https://docs.kalshi.com/welcome
- U: Kalshi taker fee = 0.07 x contracts x price x (1 - price), rounded up (schedule of 7 Jul 2026; the page itself returned 429).
- V: Polymarket resolved markets: https://gamma-api.polymarket.com/markets?closed=true ; prices: https://clob.polymarket.com/prices-history (coarse for old markets; ask in short windows).
- V: Polymarket's main site is close-only for the US; a separate US site and API exist (docs.polymarket.us). Eligibility and fees not verified.
- V: bulk dumps: Hugging Face SII-WANGZJ/Polymarket_data (279 GB of trades, 2022-11 to 2026-10); GitHub Jon-Becker/prediction-market-analysis (36 GiB, Kalshi and Polymarket).

## US stocks including companies that died
- V: EODHD: delisted US tickers in every plan, mostly from 2000; 19.99 dollars a month. Index membership add-on only from 2022. https://eodhd.com/pricing
- V: Norgate Platinum: 630 dollars a year, from 1990, delisted names and index membership by date. https://norgatedata.com/stockmarketpackages.php
- V: Polygon/Massive keeps delisted tickers; 20+ years needs the 199 dollars a month plan. https://massive.com/pricing
- V: Sharadar covers active and delisted companies back to the 1990s; price not found.
- V: free S&P 500 membership by date from 1996 (reliable from 2001): https://github.com/fja05680/sp500 . It has no prices.
- U: Tiingo (30 dollars a month) has delisted data only for tickers not reused.

## Crypto
- V: Binance public dumps include delisted pairs, with checksums: https://data.binance.vision
- V: Coinbase candles need no key (300 a call): https://api.exchange.coinbase.com/products/BTC-USD/candles
- V: CoinMarketCap's weekly historical pages give the top coins by date: https://coinmarketcap.com/historical/YYYYMMDD/ (scraping terms not checked).

## Do language models forecast better than markets?
- V: ForecastBench (Jan 2026): superforecasters first; the best models trail them by about 0.017 in adjusted Brier score. https://forecastingresearch.substack.com/p/llms-are-closing-the-gap-on-human
- V: Metaculus spring 2026: professional forecasters ahead of the best bots, not significantly (p = 0.25).
- V: Prophet Arena (Kalshi questions): models show "promising market returns" but take in news more slowly than the market near resolution. https://arxiv.org/abs/2510.17638
- U: a live-money study (arXiv 2604.07355) had six frontier models lose 16% to 31% on Kalshi in 57 days.
- Not found: any peer-reviewed evidence that model-driven trading is profitable after fees.
