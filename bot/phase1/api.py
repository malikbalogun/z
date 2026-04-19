"""Phase 1 operator-facing API endpoints.

Mounted alongside the existing admin API to provide pipeline visibility
without requiring the full trading bot to be running.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from bot.db.models import session_scope
from bot.phase1.models import (
    CandidateStatus,
    P1Market,
    P1PaperTrade,
    P1TradeCandidate,
    P1WalletEvent,
    P1WalletProfile,
    PaperTradeStatus,
)
from bot.phase1.rejection_log import get_recent_rejections, rejection_summary
from bot.phase1.visibility import (
    market_stats,
    pipeline_summary,
    recent_candidates,
    recent_paper_trades,
    wallet_profiles_summary,
)

router = APIRouter(prefix="/api/p1", tags=["phase1"])


def _get_session():
    with session_scope() as s:
        yield s


@router.get("/status")
def p1_status(db: Annotated[Session, Depends(_get_session)]):
    """Pipeline summary: counts by status, markets, wallets, etc."""
    return pipeline_summary(db)


@router.get("/candidates")
def p1_candidates(
    db: Annotated[Session, Depends(_get_session)],
    status: str | None = None,
    limit: int = Query(default=20, le=100),
):
    """Recent trade candidates."""
    return recent_candidates(db, limit=limit, status=status)


@router.get("/paper-trades")
def p1_paper_trades(
    db: Annotated[Session, Depends(_get_session)],
    limit: int = Query(default=20, le=100),
):
    """Recent paper trades."""
    return recent_paper_trades(db, limit=limit)


@router.get("/wallets")
def p1_wallets(db: Annotated[Session, Depends(_get_session)]):
    """Wallet profiles and scores."""
    return wallet_profiles_summary(db)


@router.get("/markets")
def p1_markets(
    db: Annotated[Session, Depends(_get_session)],
    top_n: int = Query(default=20, le=100),
):
    """Top markets by liquidity."""
    return market_stats(db, top_n=top_n)


@router.get("/rejections")
def p1_rejections(
    db: Annotated[Session, Depends(_get_session)],
    limit: int = Query(default=50, le=200),
    stage: str | None = None,
    wallet: str | None = None,
    hours: float | None = None,
):
    """Recent rejection log entries."""
    rows = get_recent_rejections(db, limit=limit, stage=stage, wallet=wallet, hours=hours)
    return [
        {
            "id": r.id,
            "candidate_id": r.candidate_id,
            "wallet": r.wallet,
            "condition_id": r.condition_id[:16] + "…" if len(r.condition_id) > 16 else r.condition_id,
            "stage": r.stage,
            "reason": r.reason,
            "details": json.loads(r.details_json) if r.details_json else {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/rejections/summary")
def p1_rejection_summary(
    db: Annotated[Session, Depends(_get_session)],
    hours: float = Query(default=24.0, ge=0.1, le=168.0),
):
    """Rejection summary by stage and reason."""
    return rejection_summary(db, hours=hours)
