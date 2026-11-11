"""Where each legacy flat module (src/*.py) belongs in the domain architecture.

The legacy modules stay byte-stable in src/ for now: several are pinned by content hash, the trading
halt file is located relative to them, and 75 tests read their source by path. This map is the
contract for moving a module into its domain package later, one family at a time, with its tests."""
from __future__ import annotations

import re
from pathlib import Path

__all__ = ["DOMAINS", "domain_of", "domain_map", "SRC"]

SRC = Path(__file__).resolve().parents[2] / "src"
DOMAINS = ("core", "quant_engine", "agents", "venues", "data_and_fees", "execution_and_risk", "backtest")

# First matching rule wins.
_RULES: tuple[tuple[str, str], ...] = (
    (r"^(valedia_config|UNIFIED_STACK_ANALYSIS|recovery_state|recovery_runtime|shadow_common|valedia_controls)$", "core"),
    (r"^(fee_engine|kalshi_fee_map|quantdinger_virtual_costs|session_clocks)$", "data_and_fees"),
    (r"^(kalshi_(auth|executor|quote|trade|audit|alert)|polymarket_(client|order_body|us_client)|poly_us_smoke|"
     r"coinbase_|alpaca_|okx_|kraken_|etoro_client|hyperliquid_|hl_public_info|mercury_client|vps_proxy|"
     r"spot_order_get|daviddtech_webhook_server|tc_auto_webhook_server)", "venues"),
    (r"^(mirofish_|atlas_|claude_bridge|ornith_|systemone_|persona_lenses|desk_roles|fund_|asi_scan|self_audit|"
     r"valedia_memory|valedia_cockpit|valedia_full_smoke|signal_send|signal_verify|btc_day_reminder|"
     r"research_desk_cli|ta_research_cli|unified_brain)", "agents"),
    (r"^(kelly_sizing|etherion_risk|Etherion_Risk_Engine|ere_|execution_policy|maker_routing|master_allocator|"
     r"allocator_|rebalancer|valedia_trading_halt|crypto_paper_safety|reconcile_run|spot_reconcile|settle_|"
     r"treasury_status|portfolio_monitor|watchdog|neural_trader_fence|dca_)", "execution_and_risk"),
    (r"^(backtest_|forward_test_ledger|calibration_|pnl_|strategy_pnl|ledger_view|spot_ledger|validate_|"
     r"ugft_kernel_etherion_backtest|parity_run|nautilus_|adversarial_|arena_stress|crypto_paper|daviddtech_paper|"
     r"etoro_copy_paper|tc_auto_paper|vaughan_arb_paper|vibe_shadow_paper|poly_guide_paper|oil_shadow|finance_evidence_review|"
     r"oil_weather_shadow_tick|weather_shadow|latency_arb_evidence|sports_evidence)", "backtest"),
    (r"^(market_data|market_state|fred_macro|dynamic_universe|intel_|hedge_fund_intel|insider_mirror|injury_checker|"
     r"fomo_radar_enrich|weather_nws|weather_lookup|nimble_|informed_|all_knowing_movers_scan|whale_copier|"
     r"poly_research_(capture|sources|intake)|local_research|kronos_research)", "data_and_fees"),
    (r"^(quant_engine|quant_run|modeled_prob|kernel_predictor|ml_|signal_fusion|combinatorial_arb|latency_arb|"
     r"arb_|strategy_modules|sports_engine|sports_run|weather_|scanner_run|kalshi_scanner|kalshi_profit|poly_|"
     r"polymarket_entertainment)", "quant_engine"),
)


def domain_of(module: str) -> str:
    """Target domain package for a legacy module name (without .py). Raises KeyError if unmapped."""
    for pattern, domain in _RULES:
        if re.match(pattern, module):
            return domain
    raise KeyError(f"legacy module '{module}' has no domain rule")


def domain_map(src: Path = SRC) -> dict[str, str]:
    """Domain for every non-test legacy module currently in src/."""
    return {p.stem: domain_of(p.stem) for p in sorted(src.glob("*.py")) if not p.name.startswith("test_")}
