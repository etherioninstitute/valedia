# Qwen 8B's ranking did not hold up (result of the replication, 2026-11-13)

The prediction was locked in `2026-11-13-forecaster-replication-prediction.md` before any fresh question was
drawn. The rule: Qwen 8B's ranking counts as replicated only if its 95% range clears 0.5 on at least two of
three fresh blind batches. It cleared 0.5 on none.

| Batch (blind, 5-day direction) | Questions | Qwen 8B ranking (95% range) | Nimble v3 ranking (95% range) |
| --- | --- | --- | --- |
| Funds, first batch (where it was noticed) | 293 | 0.586 (0.532 to 0.650) | 0.534 (0.469 to 0.601) |
| Stocks, fresh | 564 | 0.516 (0.475 to 0.559) | 0.512 (0.461 to 0.557) |
| Crypto, fresh | 371 | 0.550 (0.489 to 0.601) | 0.517 (0.452 to 0.576) |
| Funds, fresh draw | 565 | 0.514 (0.472 to 0.560) | 0.489 (0.443 to 0.535) |

- Predicted for Qwen 8B: about 0.54 on each fresh batch, one chance in three of replicating. Seen: 0.516,
  0.550 and 0.514, not replicated.
- Rescaled on the earlier half and scored on the later half, Qwen 8B was no better than the base rate on
  any fresh batch (skill -0.001, -0.016, -0.018).
- Nimble v3 and both one-line rules were indistinguishable from chance on every batch, as predicted.
- Not part of the locked rule: pooling the 1,500 fresh questions gives Qwen 8B 0.538 (0.509 to 0.566). That
  is a faint signal at most, pooled across markets with different base rates, and far too weak to trade.

What it means: shown only price-derived facts, neither forecaster can tell which assets will rise over the
next five days, on funds, stocks or crypto (1,793 blind questions). The first batch's result was luck of
the draw. Whether they add anything when given information that prices do not carry (news, filings,
event questions) has not been tested.
