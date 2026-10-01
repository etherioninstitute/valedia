# Vibe shadow paper

HKUDS Vibe-Trading stays a research catalog. Valedia logs a paper shadow of one dip rule. It does not place orders.

- Module: `scripts/integrations/vibe_shadow_paper.py`
- Log: `data/vibe-shadow/decisions.jsonl`
- Source: `vibe_shadow_paper` (`paper` keeps Kelly off)
- Rule: `prior_5d_return <= -0.08` and `gross_pnl - fee_usd > 0` → `shadow_log` at $25, $100 daily cap
- Missing fee, non-positive net, missing return, or a return above the dip → no size
- `create_order` false. `broker_write` false. `live_enabled()` false
- `place_order`, `create_order`, `exec_generated`, and `shell` raise
- Alpha Zoo and the 18 connectors are names and paths in `inventory()`, not imports
- Clone: `~/MemPalace/sources/repos/github/HKUDS-Vibe-Trading` @ `a65427a3`
- Security: `recommendations/VIBE-TRADING-SECURITY-2026-09-23.md`

```
python3 scripts/integrations/vibe_shadow_paper.py
python3 scripts/integrations/test_vibe_shadow_paper.py
```
