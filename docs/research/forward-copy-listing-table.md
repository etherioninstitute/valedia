# Forward copy test: the listing table (which Kalshi series Webull is seen to list)

Kept beside `2026-11-18-forward-copy-on-webull-prediction.md`. A series not in this table gets no copy. Each row
says when and how the listing was seen. "Test environment" means Webull's practice system, read with the
project's own probe (`python -m valedia_trader.venues.webull_events probe`: instrument lists only; no price,
order or account call); the live account may list differently, which only the owner's own app can show.

| Kalshi series | Seen | How | What was seen |
| --- | --- | --- | --- |
| KXNBAGAME | 2026-11-13 | probe, test environment | every open Kalshi market listed as tradable under the same ticker (64 of 64) |
| KXNFLGAME | 2026-11-13 | probe, test environment | 52 of 52 |
| KXNHLGAME | 2026-11-13 | probe, test environment | 98 of 98 |
| KXNCAAFGAME | 2026-11-13 | probe, test environment | 506 of 506 |
| KXNCAAMBGAME | 2026-11-13 | probe, test environment | 354 of 354 |

Source of these five rows: `docs/WEBULL.md`, "Event contracts: the Events account trades Kalshi's markets".
Only KXNBAGAME of them is in the rule map so far.

## Not yet seen

KXNBASPREAD, KXPREMIERLEAGUE, KXPRESNOMD (in the rule map, so first), then the series of the NEAR book.
