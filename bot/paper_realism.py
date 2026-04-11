"""
Phase 2 paper execution realism: conservative follower fill simulation,
latency penalty, slippage modelling, and orderbook survivability.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional


@dataclass
class PaperFillResult:
    """Simulated fill result for a dry-run / paper trade."""
    filled: bool = False
    fill_price: float = 0.0
    fill_size: float = 0.0
    slippage_bps: float = 0.0
    latency_penalty_ms: float = 0.0
    fill_probability: float = 0.0
    reason: str = ""


def estimate_follower_fill_probability(
    *,
    limit_price: float,
    observed_price: float,
    spread_bps: Optional[float] = None,
    book_bid_share: Optional[float] = None,
    latency_ms: float = 500.0,
    size_usd: float = 5.0,
) -> float:
    """
    Conservative estimate of how likely a follower's limit order fills
    after observing a whale's trade, accounting for the fact that:
    - The whale's trade moved the book
    - Other followers are also trying to copy
    - There's latency between observation and order placement
    - Small books get eaten quickly

    Returns probability in [0, 1].
    """
    if limit_price <= 0 or observed_price <= 0:
        return 0.0

    buffer_pct = (limit_price - observed_price) / max(observed_price, 0.01)

    # Base fill probability depends on how much buffer above market price
    if buffer_pct >= 0.05:
        base = 0.85
    elif buffer_pct >= 0.03:
        base = 0.70
    elif buffer_pct >= 0.01:
        base = 0.50
    elif buffer_pct >= 0.0:
        base = 0.30
    else:
        base = 0.10

    # Latency penalty: every 500ms of delay reduces fill chance
    latency_factor = max(0.3, 1.0 - (latency_ms / 5000.0))

    # Spread penalty: wide spreads = thin books = harder to fill
    if spread_bps is not None and spread_bps > 0:
        spread_factor = max(0.5, 1.0 - (spread_bps - 200) / 2000.0)
    else:
        spread_factor = 0.8

    # Book depth penalty: low bid share = weak buy side
    if book_bid_share is not None:
        depth_factor = max(0.4, min(1.0, book_bid_share / 0.5))
    else:
        depth_factor = 0.7

    # Size penalty: large orders relative to typical book are harder to fill
    size_factor = max(0.5, 1.0 - max(0.0, size_usd - 20.0) / 200.0)

    prob = base * latency_factor * spread_factor * depth_factor * size_factor
    return min(max(prob, 0.0), 1.0)


def simulate_paper_fill(
    *,
    limit_price: float,
    observed_price: float,
    size_usd: float,
    spread_bps: Optional[float] = None,
    book_bid_share: Optional[float] = None,
    latency_ms: float = 500.0,
    slippage_model_bps: float = 50.0,
    seed: Optional[int] = None,
) -> PaperFillResult:
    """
    Simulate whether a paper trade would realistically fill,
    and at what effective price (including slippage).

    Uses conservative assumptions:
    - Follower is slower than the whale by latency_ms
    - Market moves against follower after whale's trade
    - Fill probability depends on buffer, spread, book depth
    """
    result = PaperFillResult()
    result.latency_penalty_ms = latency_ms

    fill_prob = estimate_follower_fill_probability(
        limit_price=limit_price,
        observed_price=observed_price,
        spread_bps=spread_bps,
        book_bid_share=book_bid_share,
        latency_ms=latency_ms,
        size_usd=size_usd,
    )
    result.fill_probability = fill_prob

    rng = random.Random(seed) if seed is not None else random.Random()

    if rng.random() > fill_prob:
        result.filled = False
        result.reason = f"no_fill_prob={fill_prob:.2f}"
        return result

    # Simulate slippage: market impact + latency-driven adverse selection
    base_slip_bps = slippage_model_bps
    latency_slip = latency_ms / 1000.0 * 20.0  # ~20bps per second of latency
    noise = rng.gauss(0, base_slip_bps * 0.3)
    total_slip_bps = max(0.0, base_slip_bps + latency_slip + noise)
    result.slippage_bps = total_slip_bps

    effective_price = observed_price * (1.0 + total_slip_bps / 10000.0)

    if effective_price > limit_price:
        result.filled = False
        result.reason = f"slipped_past_limit_{effective_price:.4f}_gt_{limit_price:.4f}"
        return result

    result.filled = True
    result.fill_price = round(effective_price, 6)
    result.fill_size = round(size_usd / max(effective_price, 0.01), 2)
    result.reason = f"paper_fill_slip={total_slip_bps:.0f}bps"
    return result


def estimate_slippage_bps(
    *,
    size_usd: float,
    spread_bps: Optional[float] = None,
    book_bid_notional: Optional[float] = None,
) -> float:
    """
    Estimate expected slippage in bps for a given trade size.
    Used as input to EV calculations.
    """
    base = 25.0

    if spread_bps is not None:
        base = max(base, spread_bps * 0.3)

    if book_bid_notional is not None and book_bid_notional > 0:
        impact = (size_usd / book_bid_notional) * 500.0
        base += impact

    size_impact = max(0.0, (size_usd - 10.0) / 100.0) * 15.0
    base += size_impact

    return max(5.0, base)


def orderbook_survivability_score(
    *,
    bid_notional: float,
    ask_notional: float,
    our_size_usd: float,
    spread_bps: Optional[float] = None,
) -> tuple[float, str]:
    """
    Score how likely our order survives in the book without adverse selection.
    Returns (score_0_to_1, reason).

    Low score = the book is thin and our order is large relative to depth,
    meaning we're likely to get picked off or not filled favorably.
    """
    total_depth = bid_notional + ask_notional
    if total_depth < 1.0:
        return 0.0, "empty_book"

    size_to_depth = our_size_usd / total_depth
    if size_to_depth > 0.5:
        depth_score = 0.1
    elif size_to_depth > 0.2:
        depth_score = 0.3
    elif size_to_depth > 0.1:
        depth_score = 0.6
    elif size_to_depth > 0.05:
        depth_score = 0.8
    else:
        depth_score = 1.0

    if bid_notional + ask_notional > 0:
        balance = bid_notional / (bid_notional + ask_notional)
        balance_score = 1.0 - abs(balance - 0.5) * 2.0
    else:
        balance_score = 0.0

    spread_score = 1.0
    if spread_bps is not None:
        if spread_bps > 500:
            spread_score = 0.2
        elif spread_bps > 300:
            spread_score = 0.5
        elif spread_bps > 150:
            spread_score = 0.7
        else:
            spread_score = 1.0

    score = 0.50 * depth_score + 0.25 * balance_score + 0.25 * spread_score
    reason = f"depth={depth_score:.2f}_bal={balance_score:.2f}_spread={spread_score:.2f}"
    return min(max(score, 0.0), 1.0), reason
