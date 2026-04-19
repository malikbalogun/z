"""Minimal operator-facing visibility: status queries for the Phase 1 pipeline."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from bot.phase1.models import (
    CandidateStatus,
    P1CollisionLock,
    P1Market,
    P1PaperTrade,
    P1RejectionLog,
    P1TradeCandidate,
    P1WalletEvent,
    P1WalletProfile,
    PaperTradeStatus,
)

log = logging.getLogger("polymarket.phase1.visibility")


def pipeline_summary(session: Session) -> dict[str, Any]:
    """High-level pipeline status for operator dashboard."""
    candidate_counts: dict[str, int] = {}
    for status in CandidateStatus:
        count = session.query(P1TradeCandidate).filter(
            P1TradeCandidate.status == status.value
        ).count()
        candidate_counts[status.value] = count

    paper_counts: dict[str, int] = {}
    for status in PaperTradeStatus:
        count = session.query(P1PaperTrade).filter(
            P1PaperTrade.status == status.value
        ).count()
        paper_counts[status.value] = count

    now = dt.datetime.now(dt.timezone.utc)
    active_locks = session.query(P1CollisionLock).filter(
        P1CollisionLock.released == False,  # noqa: E712
        P1CollisionLock.expires_at > now,
    ).count()

    total_markets = session.query(P1Market).count()
    active_markets = session.query(P1Market).filter(P1Market.active == True).count()  # noqa: E712
    total_wallets = session.query(P1WalletProfile).count()
    total_events = session.query(P1WalletEvent).count()
    total_rejections = session.query(P1RejectionLog).count()

    return {
        "candidates": candidate_counts,
        "paper_trades": paper_counts,
        "active_locks": active_locks,
        "markets": {"total": total_markets, "active": active_markets},
        "wallets_tracked": total_wallets,
        "wallet_events": total_events,
        "rejections_total": total_rejections,
    }


def recent_candidates(
    session: Session,
    *,
    limit: int = 20,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Recent candidates with full detail."""
    q = session.query(P1TradeCandidate)
    if status:
        q = q.filter(P1TradeCandidate.status == status)
    rows = q.order_by(P1TradeCandidate.created_at.desc()).limit(limit).all()

    return [
        {
            "id": c.id,
            "source_wallet": c.source_wallet,
            "condition_id": c.condition_id[:16] + "…" if len(c.condition_id) > 16 else c.condition_id,
            "question": c.question[:80],
            "outcome": c.outcome,
            "side": c.side,
            "source_price": c.source_price,
            "our_limit_price": c.our_limit_price,
            "size_usd": c.size_usd,
            "category": c.category,
            "wallet_score": c.wallet_score,
            "copyability_score": c.copyability_score,
            "trade_worthiness": c.trade_worthiness,
            "status": c.status,
            "status_reason": c.status_reason[:120],
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in rows
    ]


def recent_paper_trades(
    session: Session,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent paper trades."""
    rows = session.query(P1PaperTrade).order_by(
        P1PaperTrade.placed_at.desc()
    ).limit(limit).all()

    return [
        {
            "id": pt.id,
            "candidate_id": pt.candidate_id,
            "condition_id": pt.condition_id[:16] + "…" if len(pt.condition_id) > 16 else pt.condition_id,
            "side": pt.side,
            "limit_price": pt.limit_price,
            "fill_price": pt.fill_price,
            "size_usd": pt.size_usd,
            "slippage_bps": pt.simulated_slippage_bps,
            "status": pt.status,
            "fill_reason": pt.fill_reason,
            "placed_at": pt.placed_at.isoformat() if pt.placed_at else None,
            "filled_at": pt.filled_at.isoformat() if pt.filled_at else None,
        }
        for pt in rows
    ]


def wallet_profiles_summary(session: Session) -> list[dict[str, Any]]:
    """All tracked wallets with scores."""
    rows = session.query(P1WalletProfile).order_by(
        P1WalletProfile.score.desc()
    ).all()

    return [
        {
            "wallet": wp.wallet,
            "score": wp.score,
            "trade_count": wp.trade_count,
            "known_outcome_ratio": wp.known_outcome_ratio,
            "sane_price_ratio": wp.sane_price_ratio,
            "median_usd": wp.median_usd,
            "is_active": wp.is_active,
            "updated_at": wp.updated_at.isoformat() if wp.updated_at else None,
        }
        for wp in rows
    ]


def market_stats(session: Session, *, top_n: int = 20) -> list[dict[str, Any]]:
    """Top markets by liquidity."""
    rows = session.query(P1Market).filter(
        P1Market.active == True  # noqa: E712
    ).order_by(P1Market.liquidity.desc()).limit(top_n).all()

    return [
        {
            "condition_id": m.condition_id[:16] + "…" if len(m.condition_id) > 16 else m.condition_id,
            "question": m.question[:80],
            "category": m.category,
            "liquidity": m.liquidity,
            "volume": m.volume,
            "active": m.active,
        }
        for m in rows
    ]
