"""Phase 1 SQLAlchemy models — Postgres-ready, SQLite default.

All tables use the ``p1_`` prefix to coexist with the existing schema.
Column types stay portable: String/Text/Float/Integer/Boolean/DateTime.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.models import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Enums (stored as string columns for portability)
# ---------------------------------------------------------------------------

class CandidateStatus(str, enum.Enum):
    NEW = "new"
    SCORED = "scored"
    FILTERED = "filtered"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAPER_EXECUTED = "paper_executed"
    EXPIRED = "expired"


class PaperTradeStatus(str, enum.Enum):
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Market snapshot (Gamma ingest)
# ---------------------------------------------------------------------------

class P1Market(Base):
    __tablename__ = "p1_markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tokens_json: Mapped[str] = mapped_column(Text, default="[]")
    outcomes_json: Mapped[str] = mapped_column(Text, default='["Yes","No"]')
    prices_json: Mapped[str] = mapped_column(Text, default="[0.5,0.5]")
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(64), default="other")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    end_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ---------------------------------------------------------------------------
# Wallet events (Data-API ingest)
# ---------------------------------------------------------------------------

class P1WalletEvent(Base):
    __tablename__ = "p1_wallet_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String(128), index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), default="")
    token_id: Mapped[str] = mapped_column(String(256), default="")
    condition_id: Mapped[str] = mapped_column(String(256), default="")
    side: Mapped[str] = mapped_column(String(8), default="BUY")
    price: Mapped[float] = mapped_column(Float, default=0.0)
    size: Mapped[float] = mapped_column(Float, default=0.0)
    usdc_value: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(32), default="unknown")
    title: Mapped[str] = mapped_column(Text, default="")
    event_type: Mapped[str] = mapped_column(String(32), default="trade")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    event_time: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("wallet", "tx_hash", "token_id", name="uq_wallet_event"),
        Index("ix_wallet_event_time", "wallet", "event_time"),
    )


# ---------------------------------------------------------------------------
# Wallet profile / score
# ---------------------------------------------------------------------------

class P1WalletProfile(Base):
    __tablename__ = "p1_wallet_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    known_outcome_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    sane_price_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    median_usd: Mapped[float] = mapped_column(Float, default=0.0)
    score_details_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ---------------------------------------------------------------------------
# Market classification (cached)
# ---------------------------------------------------------------------------

class P1MarketClassification(Base):
    __tablename__ = "p1_market_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(64), default="other")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    rule_matched: Mapped[str] = mapped_column(String(128), default="regex")
    classified_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Trade candidate (pipeline entity)
# ---------------------------------------------------------------------------

class P1TradeCandidate(Base):
    __tablename__ = "p1_trade_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_wallet: Mapped[str] = mapped_column(String(128), index=True)
    wallet_event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    condition_id: Mapped[str] = mapped_column(String(256), index=True)
    token_id: Mapped[str] = mapped_column(String(256))
    question: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(32), default="unknown")
    side: Mapped[str] = mapped_column(String(8), default="BUY")
    source_price: Mapped[float] = mapped_column(Float, default=0.0)
    our_limit_price: Mapped[float] = mapped_column(Float, default=0.0)
    size_usd: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(64), default="other")
    wallet_score: Mapped[float] = mapped_column(Float, default=0.0)
    copyability_score: Mapped[float] = mapped_column(Float, default=0.0)
    trade_worthiness: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(String(32), default=CandidateStatus.NEW.value, index=True)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    risk_checks_json: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_candidate_status_created", "status", "created_at"),
    )


# ---------------------------------------------------------------------------
# Paper trade (simulated execution)
# ---------------------------------------------------------------------------

class P1PaperTrade(Base):
    __tablename__ = "p1_paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(Integer, index=True)
    condition_id: Mapped[str] = mapped_column(String(256), index=True)
    token_id: Mapped[str] = mapped_column(String(256))
    side: Mapped[str] = mapped_column(String(8), default="BUY")
    limit_price: Mapped[float] = mapped_column(Float, default=0.0)
    fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    size_usd: Mapped[float] = mapped_column(Float, default=0.0)
    simulated_slippage_bps: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default=PaperTradeStatus.OPEN.value, index=True)
    fill_reason: Mapped[str] = mapped_column(Text, default="")
    placed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    filled_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Rejection log
# ---------------------------------------------------------------------------

class P1RejectionLog(Base):
    __tablename__ = "p1_rejection_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    wallet: Mapped[str] = mapped_column(String(128), default="")
    condition_id: Mapped[str] = mapped_column(String(256), default="")
    stage: Mapped[str] = mapped_column(String(64), default="unknown")
    reason: Mapped[str] = mapped_column(Text, default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_rejection_stage_time", "stage", "created_at"),
    )


# ---------------------------------------------------------------------------
# Anti-collision lock
# ---------------------------------------------------------------------------

class P1CollisionLock(Base):
    __tablename__ = "p1_collision_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(256), index=True)
    token_id: Mapped[str] = mapped_column(String(256))
    locked_by: Mapped[str] = mapped_column(String(128), default="pipeline")
    locked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    released: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_lock_condition_active", "condition_id", "released"),
    )
