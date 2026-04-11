"""Persistent rejection logging: query and summary utilities."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from bot.phase1.models import P1RejectionLog

log = logging.getLogger("polymarket.phase1.rejection_log")


def log_rejection(
    session: Session,
    *,
    candidate_id: int | None = None,
    wallet: str = "",
    condition_id: str = "",
    stage: str = "unknown",
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> P1RejectionLog:
    """Persist a rejection event."""
    entry = P1RejectionLog(
        candidate_id=candidate_id,
        wallet=wallet,
        condition_id=condition_id,
        stage=stage,
        reason=reason,
        details_json=json.dumps(details or {}, default=str),
    )
    session.add(entry)
    session.flush()
    return entry


def get_recent_rejections(
    session: Session,
    *,
    limit: int = 50,
    stage: str | None = None,
    wallet: str | None = None,
    hours: float | None = None,
) -> list[P1RejectionLog]:
    """Query recent rejections with optional filters."""
    q = session.query(P1RejectionLog)
    if stage:
        q = q.filter(P1RejectionLog.stage == stage)
    if wallet:
        q = q.filter(P1RejectionLog.wallet == wallet.strip().lower())
    if hours and hours > 0:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
        q = q.filter(P1RejectionLog.created_at >= cutoff)
    return list(q.order_by(P1RejectionLog.created_at.desc()).limit(limit).all())


def rejection_summary(
    session: Session,
    *,
    hours: float = 24.0,
) -> dict[str, Any]:
    """Summary of rejections by stage and reason over the given window."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    rows = session.query(P1RejectionLog).filter(
        P1RejectionLog.created_at >= cutoff
    ).all()

    by_stage: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_wallet: dict[str, int] = {}

    for r in rows:
        by_stage[r.stage] = by_stage.get(r.stage, 0) + 1
        reason_key = r.reason.split(";")[0].strip() if r.reason else "unknown"
        by_reason[reason_key] = by_reason.get(reason_key, 0) + 1
        if r.wallet:
            by_wallet[r.wallet] = by_wallet.get(r.wallet, 0) + 1

    return {
        "total": len(rows),
        "window_hours": hours,
        "by_stage": dict(sorted(by_stage.items(), key=lambda x: -x[1])),
        "by_reason": dict(sorted(by_reason.items(), key=lambda x: -x[1])[:20]),
        "by_wallet": dict(sorted(by_wallet.items(), key=lambda x: -x[1])[:10]),
    }
