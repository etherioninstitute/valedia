# Ponytail audit — FOMO-RADAR-DOSSIER (0.55.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** enrich tags only. `POLYMARKET_US_LIVE` stays False. No `VENUE_WRITER` edit. No `VALEDIA_TRADING_HALT` flip. No treasury auto. No wallets. No cron. No FOMOAPI.

## Applied

`reuse:` CRYPTO-PAPER equity doc and MiroFish `signals[ticker]` stamp (same idea as `attach_to_signal`: copy, add a sibling key, leave score/qty alone). Stdlib `json`. Conviction formula is the one-line upstream sum, not a new scorer.

`yagni:` port of resolve/RPC, FOMOAPI client, Claude `score.py`, `hot.py` sliding window, `exit_rule.py` candles, browser/extension, wiring into `crypto_paper.run` or the allocator. A second paper book. Any size field derived from conviction.

`ponytail:` burst/exit arrive pre-reduced on the fixture. The window and the receipt check stay upstream until a saved tape exists. Seeded rows zero conviction instead of carrying a number a caller could size from.

## Findings

One module, one fixture, one test. 0 deps. 0 live flag flips. Radar score is a tag. Lean enough. Ship.
