"""Trade-worthiness filter: deterministic filter pipeline for candidates.

Each filter returns (pass: bool, reason: str). A candidate must pass ALL
filters to be considered trade-worthy. Every rejection is logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("polymarket.phase1.trade_filter")


@dataclass
class FilterConfig:
    """Configuration for the filter pipeline (loaded from settings)."""
    min_wallet_score: float = 0.0
    min_copyability_score: float = 0.0
    min_price: float = 0.02
    max_price: float = 0.98
    min_size_usd: float = 1.0
    max_size_usd: float = 500.0
    min_liquidity_usd: float = 500.0
    allowed_categories: list[str] | None = None
    blocked_categories: list[str] | None = None
    allowed_outcomes: list[str] | None = None
    blocked_keywords: list[str] | None = None
    required_keywords: list[str] | None = None
    min_hours_to_resolution: float = 0.0

    @classmethod
    def from_settings(cls, settings: Any) -> FilterConfig:
        return cls(
            min_wallet_score=float(getattr(settings, "copy_min_wallet_score", 0.0) or 0.0),
            min_copyability_score=float(getattr(settings, "min_copyability_score", 0.0) or 0.0)
            if hasattr(settings, "min_copyability_score") else 0.0,
            min_price=float(getattr(settings, "copy_min_price", 0.02) or 0.02),
            max_price=float(getattr(settings, "copy_max_price", 0.98) or 0.98),
            min_size_usd=float(getattr(settings, "copy_min_usd", 1.0) or 1.0),
            max_size_usd=float(getattr(settings, "copy_max_usd", 500.0) or 500.0)
            if float(getattr(settings, "copy_max_usd", 0.0) or 0.0) > 0 else 500.0,
            min_liquidity_usd=float(getattr(settings, "min_clob_liquidity_usd", 500.0) or 500.0),
            allowed_categories=list(getattr(settings, "copy_allowed_categories", []) or []) or None,
            blocked_categories=None,
            allowed_outcomes=list(getattr(settings, "copy_allowed_outcomes", []) or []) or None,
            blocked_keywords=list(getattr(settings, "copy_blocked_keywords", []) or []) or None,
            required_keywords=list(getattr(settings, "copy_required_keywords", []) or []) or None,
        )


@dataclass
class FilterResult:
    passed: bool
    reason: str
    filter_name: str


def _contains_any(text: str, terms: list[str]) -> bool:
    t = text.lower()
    for k in terms:
        s = str(k).strip().lower()
        if s and s in t:
            return True
    return False


def filter_wallet_score(wallet_score: float, min_score: float) -> FilterResult:
    if min_score > 0 and wallet_score < min_score:
        return FilterResult(False, f"wallet_score_{wallet_score:.3f}_lt_{min_score:.3f}", "wallet_score")
    return FilterResult(True, "ok", "wallet_score")


def filter_copyability(copyability_score: float, min_score: float) -> FilterResult:
    if min_score > 0 and copyability_score < min_score:
        return FilterResult(False, f"copyability_{copyability_score:.3f}_lt_{min_score:.3f}", "copyability")
    return FilterResult(True, "ok", "copyability")


def filter_price_range(price: float, min_price: float, max_price: float) -> FilterResult:
    if price < min_price:
        return FilterResult(False, f"price_{price:.4f}_lt_min_{min_price:.4f}", "price_range")
    if price > max_price:
        return FilterResult(False, f"price_{price:.4f}_gt_max_{max_price:.4f}", "price_range")
    return FilterResult(True, "ok", "price_range")


def filter_size_usd(size_usd: float, min_usd: float, max_usd: float) -> FilterResult:
    if size_usd < min_usd:
        return FilterResult(False, f"size_{size_usd:.2f}_lt_min_{min_usd:.2f}", "size_usd")
    if max_usd > 0 and size_usd > max_usd:
        return FilterResult(False, f"size_{size_usd:.2f}_gt_max_{max_usd:.2f}", "size_usd")
    return FilterResult(True, "ok", "size_usd")


def filter_liquidity(market_liquidity: float, min_liquidity: float) -> FilterResult:
    if min_liquidity > 0 and market_liquidity < min_liquidity:
        return FilterResult(
            False,
            f"liquidity_{market_liquidity:.0f}_lt_{min_liquidity:.0f}",
            "liquidity",
        )
    return FilterResult(True, "ok", "liquidity")


def filter_category(category: str, allowed: list[str] | None, blocked: list[str] | None) -> FilterResult:
    cat = category.lower()
    if allowed:
        allowed_lower = [c.lower() for c in allowed]
        if cat not in allowed_lower:
            return FilterResult(False, f"category_{cat}_not_in_allowed", "category")
    if blocked:
        blocked_lower = [c.lower() for c in blocked]
        if cat in blocked_lower:
            return FilterResult(False, f"category_{cat}_blocked", "category")
    return FilterResult(True, "ok", "category")


def filter_outcome(outcome: str, allowed: list[str] | None) -> FilterResult:
    if allowed:
        allowed_lower = [o.lower() for o in allowed]
        if outcome.lower() not in allowed_lower:
            return FilterResult(False, f"outcome_{outcome}_not_allowed", "outcome")
    return FilterResult(True, "ok", "outcome")


def filter_keywords(
    text: str,
    required: list[str] | None,
    blocked: list[str] | None,
) -> FilterResult:
    if required and not _contains_any(text, required):
        return FilterResult(False, "required_keywords_miss", "keywords")
    if blocked and _contains_any(text, blocked):
        return FilterResult(False, "blocked_keyword_hit", "keywords")
    return FilterResult(True, "ok", "keywords")


def run_filter_pipeline(
    *,
    wallet_score: float,
    copyability_score: float,
    source_price: float,
    size_usd: float,
    market_liquidity: float,
    category: str,
    outcome: str,
    question_text: str,
    config: FilterConfig,
) -> tuple[bool, list[FilterResult]]:
    """Run all filters. Returns (all_passed, list_of_results)."""
    results: list[FilterResult] = []

    checks = [
        filter_wallet_score(wallet_score, config.min_wallet_score),
        filter_copyability(copyability_score, config.min_copyability_score),
        filter_price_range(source_price, config.min_price, config.max_price),
        filter_size_usd(size_usd, config.min_size_usd, config.max_size_usd),
        filter_liquidity(market_liquidity, config.min_liquidity_usd),
        filter_category(category, config.allowed_categories, config.blocked_categories),
        filter_outcome(outcome, config.allowed_outcomes),
        filter_keywords(question_text, config.required_keywords, config.blocked_keywords),
    ]

    results.extend(checks)
    all_passed = all(r.passed for r in results)
    return all_passed, results


def compute_trade_worthiness(
    *,
    copyability_score: float,
    wallet_score: float,
    filter_pass_ratio: float,
) -> float:
    """Composite trade-worthiness in [0,1].

    Simple weighted combination:
      - copyability (0.50)
      - wallet_score (0.30)
      - filter_pass_ratio (0.20)
    """
    tw = (
        0.50 * min(max(copyability_score, 0.0), 1.0)
        + 0.30 * min(max(wallet_score, 0.0), 1.0)
        + 0.20 * min(max(filter_pass_ratio, 0.0), 1.0)
    )
    return round(min(max(tw, 0.0), 1.0), 4)
