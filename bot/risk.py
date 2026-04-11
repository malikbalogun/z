"""Pre-trade gates: category toggles, crypto CEX dispersion, size caps, EV-aware gating."""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from bot.categories import MarketCategory, category_enabled
from bot.models import TradeIntent

if TYPE_CHECKING:
    from bot.settings import Settings

log = logging.getLogger("polymarket.risk")


def gate_intent(
    intent: TradeIntent,
    settings: Settings,
    cex_dispersion_bps: Optional[float],
) -> tuple[bool, str]:
    tid = (intent.token_id or "").strip()
    if len(tid) < 20:
        return False, "invalid_token_id"

    if not category_enabled(intent.category, settings.category_flags):
        return False, f"category_disabled:{intent.category.value}"

    if intent.category in (MarketCategory.CRYPTO_SHORT, MarketCategory.CRYPTO_OTHER):
        if settings.cex_gate_crypto:
            if cex_dispersion_bps is None and settings.cex_require_dispersion:
                return False, "cex_no_dispersion"
            if cex_dispersion_bps is not None:
                if cex_dispersion_bps > settings.max_cex_dispersion_bps:
                    return (
                        False,
                        f"cex_dispersion_{cex_dispersion_bps:.1f}_bps>{settings.max_cex_dispersion_bps}",
                    )

    if intent.size_usd < settings.min_bet_usd:
        return False, "below_min_bet"
    if intent.size_usd > settings.max_bet_usd:
        return False, "above_max_bet"
    if intent.max_price <= 0.01 or intent.max_price >= 0.99:
        return False, "bad_limit_price"

    # mlmodelpoly-style: require positive edge vs reference mid (bps) when agents set reference_price.
    if intent.side.upper() == "BUY" and settings.min_edge_bps > 0:
        r = intent.reference_price
        if r is not None and 0 < r < 0.999:
            edge_bps = (r - intent.max_price) / r * 10000.0
            if edge_bps < float(settings.min_edge_bps):
                return False, f"edge_{edge_bps:.1f}_bps_lt_{settings.min_edge_bps}"

    # Phase 2: EV-aware gating
    ev_gate_enabled = bool(getattr(settings, "ev_gate_enabled", False))
    if ev_gate_enabled and intent.side.upper() == "BUY":
        ev_ok, ev_reason = _ev_gate(intent, settings)
        if not ev_ok:
            return False, ev_reason

    return True, "ok"


def _ev_gate(intent: TradeIntent, settings: "Settings") -> tuple[bool, str]:
    """Phase 2 EV gate: minimum expected profit and slippage-adjusted EV check."""
    try:
        from bot.ev_math import compute_ev
    except ImportError:
        return True, "ok"

    fair = intent.reference_price
    if fair is None or fair <= 0.001 or fair >= 0.999:
        return True, "ok"

    min_ev_bps = float(getattr(settings, "ev_min_edge_bps", 0.0) or 0.0)
    min_profit = float(getattr(settings, "ev_min_profit_usd", 0.0) or 0.0)
    slippage_est = float(getattr(settings, "ev_slippage_estimate_bps", 25.0) or 25.0)
    fee_bps = float(getattr(settings, "ev_fee_bps", 0.0) or 0.0)
    time_discount = float(getattr(settings, "ev_time_discount_rate", 0.0) or 0.0)
    hours_to_res = getattr(intent, "hours_to_resolution", None)

    result = compute_ev(
        entry_price=intent.max_price,
        fair_price=fair,
        size_usd=intent.size_usd,
        slippage_bps=slippage_est,
        fee_bps=fee_bps,
        hours_to_resolution=hours_to_res,
        min_ev_bps=min_ev_bps,
        min_profit_usd=min_profit,
        time_discount_rate=time_discount,
    )
    if not result.passes:
        return False, f"ev_gate:{result.reason}"
    return True, "ok"
