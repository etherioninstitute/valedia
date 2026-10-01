"""Informational red-flag booleans. NOT financial advice and NOT used to gate buys.

Gating is done by gates.py on the model's risk answer and hard facts. These flags are simply
extra facts handed to the model (and shown in the decision log). They carry no thresholds.

  flag_no_bytecode          token address has no contract code
  flag_owner_active         owner() returned a non-zero address
  flag_liquidity_unknown    liquidity_usd could not be computed (unknown quote or failed read)
  flag_supply_unusable      total supply unreadable or zero
  flag_price_unreadable     pool price (slot0) unreadable
  flag_symbol_unreadable    symbol() unreadable
  flag_high_fee_tier        pool fee tier >= 1.00%
"""
from __future__ import annotations


def risk_flags(state: dict) -> dict:
    return {
        "flag_no_bytecode": not state.get("contract_has_code"),
        "flag_owner_active": str(state.get("owner_status", "")).startswith("owned:"),
        "flag_liquidity_unknown": state.get("liquidity_usd") is None,
        "flag_supply_unusable": not state.get("total_supply"),
        "flag_price_unreadable": state.get("price_in_quote") is None,
        "flag_symbol_unreadable": not state.get("symbol"),
        "flag_high_fee_tier": (state.get("fee_tier") or 0) >= 10000,
    }
