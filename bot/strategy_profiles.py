"""Risk/edge bands similar to common Polymarket bot repos (tiered liquidity + price bands)."""

from __future__ import annotations

from typing import Any


def apply_profile(name: str) -> dict[str, Any]:
    """
    Returns overrides for value-edge thresholds and liquidity multiplier.
    Profiles: conservative | balanced | aggressive
    """
    n = (name or "balanced").lower().strip()
    if n == "conservative":
        return {
            "strategy_profile": "conservative",
            "value_yes_low": 0.15,
            "value_yes_high": 0.40,
            "value_no_yes_min": 0.68,
            "value_no_no_max": 0.42,
            "value_liq_floor_usd": 1500.0,
        }
    if n == "aggressive":
        return {
            "strategy_profile": "aggressive",
            "value_yes_low": 0.22,
            "value_yes_high": 0.48,
            "value_no_yes_min": 0.62,
            "value_no_no_max": 0.48,
            "value_liq_floor_usd": 700.0,
        }
    return {
        "strategy_profile": "balanced",
        "value_yes_low": 0.20,
        "value_yes_high": 0.45,
        "value_no_yes_min": 0.65,
        "value_no_no_max": 0.45,
        "value_liq_floor_usd": 1000.0,
    }
