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

| KXNBASPREAD | 2026-11-18 | probe, test environment | 396 of 396 open Kalshi markets listed as tradable (68 more on Webull only, not tradable: the day before's games) |
| KXPREMIERLEAGUE | 2026-11-18 | probe, test environment | 20 of 20 |
| KXNBA | 2026-11-18 | probe, test environment | 30 of 30 |
| KXSB | 2026-11-18 | probe, test environment | 32 of 32 |
| KXNBAWCFQUAL | 2026-11-18 | probe, test environment | 15 of 15 |
| KXBTCMINY | 2026-11-18 | probe, test environment | 4 of 4 (a NEAR family: information only) |
| KXETHMINY | 2026-11-18 | probe, test environment | 4 of 4 (a NEAR family: information only) |

Source of the first five rows: `docs/WEBULL.md`, "Event contracts: the Events account trades Kalshi's markets". The
rows of 2026-11-18: four runs of the probe by the lead, 15:21 to 15:26 PST (52 requests to Webull's test system,
28 to Kalshi's public list; output kept outside the repository). "Tradable" is Webull's own status word for the
market (OC); a market it lists as NT is not tradable.

In the rule map AND in this table (so a copy may be made): KXNBAGAME, KXNBASPREAD, KXPREMIERLEAGUE.

## Seen listed but NOT tradable (2026-11-18, test environment): no copy

Every market of the series was listed under Kalshi's ticker with Webull's status "not tradable":
**KXPRESNOMD** (61 of 61; it is in the rule map, and still gets no copy while this stands), KXBTCMAXMON (7),
KXBTCMINMON (2), KXETHMINMON (6), KXSOLMAXMON (8), KXSOLMINMON (3), KXSOLMINY (7), KXXRPMAXMON (7), KXXRPMINMON (4),
KXDOGEMAXMON (8), KXDOGEMINMON (5), KXDOGEMINY (5), KXHYPEMAXMON (8), KXHYPEMINMON (8).

## Seen only in part

KXBTCD, KXETHD, KXSOLD (price on a date; NEAR families): the series is listed; of the one page read (100 markets
each) 50 were Kalshi's open markets, tradable, and 50 were on Webull only, not tradable; Kalshi had 318, 390 and
425 open.

## Not listed (2026-11-18, test environment)

KXBTCHIT90K, KXBTCHIT95K, KXETHABOVE3000BY.

## Not yet seen

Every other series.
