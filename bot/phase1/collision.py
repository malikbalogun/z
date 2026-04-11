"""Coordination / anti-collision: prevent duplicate trades on the same market.

Uses a DB-backed lock table with TTL-based expiry. Before approving a
candidate, the pipeline must acquire a lock for the condition_id.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from bot.phase1.models import P1CollisionLock

log = logging.getLogger("polymarket.phase1.collision")

DEFAULT_LOCK_TTL_SECONDS = 300  # 5 minutes


def acquire_lock(
    session: Session,
    condition_id: str,
    token_id: str,
    *,
    locked_by: str = "pipeline",
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> bool:
    """Try to acquire a lock for a condition_id. Returns True if acquired."""
    now = dt.datetime.now(dt.timezone.utc)

    existing = session.query(P1CollisionLock).filter(
        P1CollisionLock.condition_id == condition_id,
        P1CollisionLock.released == False,  # noqa: E712
        P1CollisionLock.expires_at > now,
    ).first()

    if existing:
        log.info(
            "collision: lock exists for %s (by=%s, expires=%s)",
            condition_id[:16],
            existing.locked_by,
            existing.expires_at.isoformat(),
        )
        return False

    # Release any expired locks for cleanup
    session.query(P1CollisionLock).filter(
        P1CollisionLock.condition_id == condition_id,
        P1CollisionLock.released == False,  # noqa: E712
        P1CollisionLock.expires_at <= now,
    ).update({"released": True})

    lock = P1CollisionLock(
        condition_id=condition_id,
        token_id=token_id,
        locked_by=locked_by,
        expires_at=now + dt.timedelta(seconds=ttl_seconds),
    )
    session.add(lock)
    session.flush()
    return True


def release_lock(session: Session, condition_id: str) -> bool:
    """Release all active locks for a condition_id."""
    now = dt.datetime.now(dt.timezone.utc)
    updated = session.query(P1CollisionLock).filter(
        P1CollisionLock.condition_id == condition_id,
        P1CollisionLock.released == False,  # noqa: E712
    ).update({"released": True})
    session.flush()
    return updated > 0


def is_locked(session: Session, condition_id: str) -> bool:
    """Check if a condition_id currently has an active lock."""
    now = dt.datetime.now(dt.timezone.utc)
    return session.query(P1CollisionLock).filter(
        P1CollisionLock.condition_id == condition_id,
        P1CollisionLock.released == False,  # noqa: E712
        P1CollisionLock.expires_at > now,
    ).first() is not None


def cleanup_expired(session: Session) -> int:
    """Release all expired locks. Returns count released."""
    now = dt.datetime.now(dt.timezone.utc)
    count = session.query(P1CollisionLock).filter(
        P1CollisionLock.released == False,  # noqa: E712
        P1CollisionLock.expires_at <= now,
    ).update({"released": True})
    session.flush()
    return count
