"""
Phase 2 trade-worthiness: composite gate combining EV math, slippage,
orderbook survivability, post-entry drift estimate, and latency penalty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from bot.ev_math import EVResult, compute_ev
from bot.paper_realism import estimate_slippage_bps, orderbook_survivability_score

log = logging.getLogger("polymarket.trade_worthiness")


@dataclass
class WorthinessResult:
    """Full trade-worthiness assessment."""
    worthy: bool = False
    ev: Optional[EVResult] = None
    survivability: float = 0.0
    estimated_slippage_bps: float = 0.0
    post_entry_drift_bps: float = 0.0
    latency_penalty_bps: float = 0.0
    composite_score: float = 0.0
    reason: str = ""


def assess_trade_worthiness(
    *,
    entry_price: float,
    fair_price: float,
    size_usd: float,
    spread_bps: Optional[float] = None,
    bid_notional: Optional[float] = None,
    ask_notional: Optional[float] = None,
    hours_to_resolution: Optional[float] = None,
    latency_ms: float = 500.0,
    fee_bps: float = 0.0,
    # Gate thresholds from settings
    min_ev_bps: float = 0.0,
    min_profit_usd: float = 0.0,
    min_survivability: float = 0.0,
    time_discount_rate: float = 0.0,
    max_slippage_bps: float = 0.0,
    post_entry_drift_bps_estimate: float = 0.0,
) -> WorthinessResult:
    """
    Comprehensive trade-worthiness check combining all Phase 2 signals.
    """
    result = WorthinessResult()

    # 1) Estimate slippage
    slippage = estimate_slippage_bps(
        size_usd=size_usd,
        spread_bps=spread_bps,
        book_bid_notional=bid_notional,
    )
    result.estimated_slippage_bps = slippage

    if max_slippage_bps > 0 and slippage > max_slippage_bps:
        result.reason = f"slippage_{slippage:.0f}bps_gt_{max_slippage_bps:.0f}"
        return result

    # 2) Latency penalty: ~2bps per 100ms of expected latency
    lat_pen = latency_ms / 100.0 * 2.0
    result.latency_penalty_bps = lat_pen

    # 3) Post-entry drift estimate: expected adverse move after entry
    result.post_entry_drift_bps = post_entry_drift_bps_estimate

    # 4) Total cost basis
    total_cost_bps = slippage + fee_bps + lat_pen + post_entry_drift_bps_estimate

    # 5) EV computation with costs baked in
    ev = compute_ev(
        entry_price=entry_price,
        fair_price=fair_price,
        size_usd=size_usd,
        slippage_bps=total_cost_bps,
        fee_bps=0.0,  # already included in total_cost_bps
        hours_to_resolution=hours_to_resolution,
        min_ev_bps=min_ev_bps,
        min_profit_usd=min_profit_usd,
        time_discount_rate=time_discount_rate,
    )
    result.ev = ev

    if not ev.passes:
        result.reason = ev.reason
        return result

    # 6) Orderbook survivability
    if bid_notional is not None and ask_notional is not None:
        surv, surv_reason = orderbook_survivability_score(
            bid_notional=bid_notional,
            ask_notional=ask_notional,
            our_size_usd=size_usd,
            spread_bps=spread_bps,
        )
        result.survivability = surv
        if min_survivability > 0 and surv < min_survivability:
            result.reason = f"survivability_{surv:.2f}_lt_{min_survivability:.2f}:{surv_reason}"
            return result
    else:
        result.survivability = 0.5  # unknown

    # 7) Composite score for ranking
    ev_score = min(max(ev.slippage_adjusted_ev * 100, 0.0), 1.0)
    result.composite_score = (
        0.50 * ev_score
        + 0.25 * result.survivability
        + 0.25 * max(0.0, 1.0 - total_cost_bps / 500.0)
    )

    result.worthy = True
    result.reason = f"worthy_ev={ev.slippage_adjusted_ev:.4f}_surv={result.survivability:.2f}"
    return result
