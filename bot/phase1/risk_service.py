"""Centralized risk service: unified risk checks with full explanations.

All risk checks return structured results. Nothing is silently dropped —
every rejection gets a reason and the full context is returned.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from bot.phase1.models import (
    P1CollisionLock,
    P1PaperTrade,
    P1RejectionLog,
    P1TradeCandidate,
    PaperTradeStatus,
)

log = logging.getLogger("polymarket.phase1.risk_service")


@dataclass
class RiskCheckResult:
    passed: bool
    check_name: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskConfig:
    max_position_per_market_usd: float = 50.0
    max_total_exposure_usd: float = 500.0
    max_daily_notional_usd: float = 200.0
    max_open_paper_trades: int = 20
    max_per_wallet_per_day: int = 5
    min_bet_usd: float = 1.0
    max_bet_usd: float = 25.0
    category_flags: dict[str, bool] | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> RiskConfig:
        return cls(
            max_position_per_market_usd=float(
                getattr(settings, "max_condition_exposure_usd", 50.0) or 50.0
            ),
            max_total_exposure_usd=float(
                getattr(settings, "max_daily_notional_usd", 500.0) or 500.0
            ),
            max_daily_notional_usd=float(
                getattr(settings, "max_daily_notional_usd", 200.0) or 200.0
            ),
            min_bet_usd=float(getattr(settings, "min_bet_usd", 1.0) or 1.0),
            max_bet_usd=float(getattr(settings, "max_bet_usd", 25.0) or 25.0),
            category_flags=dict(getattr(settings, "category_flags", {}) or {}),
        )


def check_size_bounds(size_usd: float, config: RiskConfig) -> RiskCheckResult:
    if size_usd < config.min_bet_usd:
        return RiskCheckResult(False, "size_bounds", f"below_min_{config.min_bet_usd}")
    if size_usd > config.max_bet_usd:
        return RiskCheckResult(False, "size_bounds", f"above_max_{config.max_bet_usd}")
    return RiskCheckResult(True, "size_bounds", "ok")


def check_price_bounds(price: float) -> RiskCheckResult:
    if price <= 0.01 or price >= 0.99:
        return RiskCheckResult(False, "price_bounds", f"price_{price:.4f}_out_of_range")
    return RiskCheckResult(True, "price_bounds", "ok")


def check_category_enabled(category: str, flags: dict[str, bool] | None) -> RiskCheckResult:
    if not flags:
        return RiskCheckResult(True, "category", "ok_no_flags")
    key = f"ENABLE_{category.upper()}"
    if key in flags and not flags[key]:
        return RiskCheckResult(False, "category", f"category_disabled:{category}")
    return RiskCheckResult(True, "category", "ok")


def check_market_exposure(
    session: Session,
    condition_id: str,
    new_usd: float,
    max_usd: float,
) -> RiskCheckResult:
    """Check that total paper exposure on this market doesn't exceed cap."""
    if max_usd <= 0:
        return RiskCheckResult(True, "market_exposure", "ok_no_cap")

    total = sum(
        pt.size_usd
        for pt in session.query(P1PaperTrade)
        .filter(
            P1PaperTrade.condition_id == condition_id,
            P1PaperTrade.status.in_([
                PaperTradeStatus.OPEN.value,
                PaperTradeStatus.FILLED.value,
            ]),
        )
        .all()
    )

    if total + new_usd > max_usd:
        return RiskCheckResult(
            False,
            "market_exposure",
            f"exposure_{total:.2f}_plus_{new_usd:.2f}_gt_{max_usd:.2f}",
            {"current": total, "new": new_usd, "max": max_usd},
        )
    return RiskCheckResult(True, "market_exposure", "ok")


def check_total_exposure(
    session: Session,
    new_usd: float,
    max_usd: float,
) -> RiskCheckResult:
    """Check total open paper trade exposure."""
    if max_usd <= 0:
        return RiskCheckResult(True, "total_exposure", "ok_no_cap")

    total = sum(
        pt.size_usd
        for pt in session.query(P1PaperTrade)
        .filter(P1PaperTrade.status == PaperTradeStatus.OPEN.value)
        .all()
    )

    if total + new_usd > max_usd:
        return RiskCheckResult(
            False,
            "total_exposure",
            f"total_{total:.2f}_plus_{new_usd:.2f}_gt_{max_usd:.2f}",
            {"current": total, "new": new_usd, "max": max_usd},
        )
    return RiskCheckResult(True, "total_exposure", "ok")


def check_daily_notional(
    session: Session,
    new_usd: float,
    max_daily_usd: float,
    window_hours: float = 24.0,
) -> RiskCheckResult:
    """Rolling notional cap over paper trades."""
    if max_daily_usd <= 0:
        return RiskCheckResult(True, "daily_notional", "ok_no_cap")

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=window_hours)
    total = sum(
        pt.size_usd
        for pt in session.query(P1PaperTrade)
        .filter(P1PaperTrade.placed_at >= cutoff)
        .all()
    )

    if total + new_usd > max_daily_usd:
        return RiskCheckResult(
            False,
            "daily_notional",
            f"daily_{total:.2f}_plus_{new_usd:.2f}_gt_{max_daily_usd:.2f}",
        )
    return RiskCheckResult(True, "daily_notional", "ok")


def check_open_trades_limit(session: Session, max_open: int) -> RiskCheckResult:
    if max_open <= 0:
        return RiskCheckResult(True, "open_trades", "ok_no_cap")

    count = session.query(P1PaperTrade).filter(
        P1PaperTrade.status == PaperTradeStatus.OPEN.value
    ).count()

    if count >= max_open:
        return RiskCheckResult(
            False,
            "open_trades",
            f"open_count_{count}_gte_{max_open}",
        )
    return RiskCheckResult(True, "open_trades", "ok")


def check_per_wallet_daily(
    session: Session,
    wallet: str,
    max_per_day: int,
    window_hours: float = 24.0,
) -> RiskCheckResult:
    if max_per_day <= 0:
        return RiskCheckResult(True, "wallet_daily", "ok_no_cap")

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=window_hours)
    count = session.query(P1TradeCandidate).filter(
        P1TradeCandidate.source_wallet == wallet.strip().lower(),
        P1TradeCandidate.status.in_(["approved", "paper_executed"]),
        P1TradeCandidate.created_at >= cutoff,
    ).count()

    if count >= max_per_day:
        return RiskCheckResult(
            False,
            "wallet_daily",
            f"wallet_daily_{count}_gte_{max_per_day}",
        )
    return RiskCheckResult(True, "wallet_daily", "ok")


def run_risk_checks(
    session: Session,
    *,
    condition_id: str,
    wallet: str,
    size_usd: float,
    price: float,
    category: str,
    config: RiskConfig,
) -> tuple[bool, list[RiskCheckResult]]:
    """Run all risk checks. Returns (all_passed, results)."""
    results: list[RiskCheckResult] = [
        check_size_bounds(size_usd, config),
        check_price_bounds(price),
        check_category_enabled(category, config.category_flags),
        check_market_exposure(session, condition_id, size_usd, config.max_position_per_market_usd),
        check_total_exposure(session, size_usd, config.max_total_exposure_usd),
        check_daily_notional(session, size_usd, config.max_daily_notional_usd),
        check_open_trades_limit(session, config.max_open_paper_trades),
        check_per_wallet_daily(session, wallet, config.max_per_wallet_per_day),
    ]

    all_passed = all(r.passed for r in results)
    return all_passed, results
