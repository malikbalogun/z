"""Candidate -> Approval -> Paper Execution pipeline.

State machine for trade candidates:
  NEW -> SCORED -> FILTERED -> APPROVED -> PAPER_EXECUTED
                           |-> REJECTED (with reason)
                 |-> REJECTED (with reason)

All transitions are logged. Paper execution simulates realistic fills.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from bot.phase1.collision import acquire_lock, release_lock
from bot.phase1.copyability import CopyabilityInput, compute_copyability
from bot.phase1.models import (
    CandidateStatus,
    P1Market,
    P1PaperTrade,
    P1RejectionLog,
    P1TradeCandidate,
    P1WalletEvent,
    P1WalletProfile,
    PaperTradeStatus,
)
from bot.phase1.risk_service import RiskConfig, run_risk_checks
from bot.phase1.trade_filter import (
    FilterConfig,
    compute_trade_worthiness,
    run_filter_pipeline,
)

log = logging.getLogger("polymarket.phase1.pipeline")

DEFAULT_PAPER_TTL_SECONDS = 300  # 5 min paper trade TTL
DEFAULT_SLIPPAGE_BPS = 50  # 50 bps simulated slippage


def _log_rejection(
    session: Session,
    *,
    candidate_id: int | None,
    wallet: str,
    condition_id: str,
    stage: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(P1RejectionLog(
        candidate_id=candidate_id,
        wallet=wallet,
        condition_id=condition_id,
        stage=stage,
        reason=reason,
        details_json=json.dumps(details or {}, default=str),
    ))


def create_candidate_from_event(
    session: Session,
    event: P1WalletEvent,
    *,
    market: P1Market | None = None,
    wallet_profile: P1WalletProfile | None = None,
    limit_price_buffer_bps: float = 300.0,
    default_size_usd: float = 5.0,
) -> P1TradeCandidate:
    """Create a NEW candidate from a wallet event."""
    source_price = event.price
    buffer_mult = 1.0 + limit_price_buffer_bps / 10000.0
    our_limit = round(min(source_price * buffer_mult, 0.99), 4)
    size_usd = event.usdc_value if event.usdc_value > 0 else default_size_usd
    category = market.category if market else "other"
    ws = wallet_profile.score if wallet_profile else 0.0

    candidate = P1TradeCandidate(
        source_wallet=event.wallet,
        wallet_event_id=event.id,
        condition_id=event.condition_id,
        token_id=event.token_id,
        question=event.title,
        outcome=event.outcome,
        side=event.side,
        source_price=source_price,
        our_limit_price=our_limit,
        size_usd=size_usd,
        category=category,
        wallet_score=ws,
        status=CandidateStatus.NEW.value,
    )
    session.add(candidate)
    session.flush()
    return candidate


def score_candidate(
    session: Session,
    candidate: P1TradeCandidate,
    *,
    market: P1Market | None = None,
) -> P1TradeCandidate:
    """Score the candidate (copyability + trade-worthiness). Transition NEW -> SCORED."""
    if candidate.status != CandidateStatus.NEW.value:
        return candidate

    liq = market.liquidity if market else 0.0
    vol = market.volume if market else 0.0

    copyability = compute_copyability(CopyabilityInput(
        wallet_score=candidate.wallet_score,
        source_price=candidate.source_price,
        market_liquidity=liq,
        market_volume=vol,
        usdc_value=candidate.size_usd,
        outcome=candidate.outcome,
        category=candidate.category,
    ))
    candidate.copyability_score = copyability.score
    candidate.status = CandidateStatus.SCORED.value
    candidate.status_reason = copyability.explanation
    session.flush()
    return candidate


def filter_candidate(
    session: Session,
    candidate: P1TradeCandidate,
    config: FilterConfig,
    *,
    market: P1Market | None = None,
) -> P1TradeCandidate:
    """Run filter pipeline. Transition SCORED -> FILTERED or REJECTED."""
    if candidate.status != CandidateStatus.SCORED.value:
        return candidate

    market_liq = market.liquidity if market else 0.0

    passed, results = run_filter_pipeline(
        wallet_score=candidate.wallet_score,
        copyability_score=candidate.copyability_score,
        source_price=candidate.source_price,
        size_usd=candidate.size_usd,
        market_liquidity=market_liq,
        category=candidate.category,
        outcome=candidate.outcome,
        question_text=candidate.question,
        config=config,
    )

    failed_filters = [r for r in results if not r.passed]
    pass_ratio = sum(1 for r in results if r.passed) / max(len(results), 1)

    candidate.trade_worthiness = compute_trade_worthiness(
        copyability_score=candidate.copyability_score,
        wallet_score=candidate.wallet_score,
        filter_pass_ratio=pass_ratio,
    )

    if passed:
        candidate.status = CandidateStatus.FILTERED.value
        candidate.status_reason = f"all_filters_passed tw={candidate.trade_worthiness:.4f}"
    else:
        candidate.status = CandidateStatus.REJECTED.value
        reasons = "; ".join(f"{r.filter_name}:{r.reason}" for r in failed_filters)
        candidate.status_reason = reasons
        _log_rejection(
            session,
            candidate_id=candidate.id,
            wallet=candidate.source_wallet,
            condition_id=candidate.condition_id,
            stage="filter",
            reason=reasons,
            details={
                "filters": [
                    {"name": r.filter_name, "passed": r.passed, "reason": r.reason}
                    for r in results
                ]
            },
        )

    session.flush()
    return candidate


def approve_candidate(
    session: Session,
    candidate: P1TradeCandidate,
    risk_config: RiskConfig,
    *,
    lock_ttl_seconds: int = 300,
) -> P1TradeCandidate:
    """Run risk checks and collision lock. Transition FILTERED -> APPROVED or REJECTED."""
    if candidate.status != CandidateStatus.FILTERED.value:
        return candidate

    # Risk checks
    risk_passed, risk_results = run_risk_checks(
        session,
        condition_id=candidate.condition_id,
        wallet=candidate.source_wallet,
        size_usd=candidate.size_usd,
        price=candidate.our_limit_price,
        category=candidate.category,
        config=risk_config,
    )

    candidate.risk_checks_json = json.dumps(
        [{"name": r.check_name, "passed": r.passed, "reason": r.reason} for r in risk_results],
        default=str,
    )

    if not risk_passed:
        candidate.status = CandidateStatus.REJECTED.value
        failed = [r for r in risk_results if not r.passed]
        reasons = "; ".join(f"{r.check_name}:{r.reason}" for r in failed)
        candidate.status_reason = f"risk_rejected: {reasons}"
        _log_rejection(
            session,
            candidate_id=candidate.id,
            wallet=candidate.source_wallet,
            condition_id=candidate.condition_id,
            stage="risk",
            reason=reasons,
            details={
                "checks": [
                    {"name": r.check_name, "passed": r.passed, "reason": r.reason, "details": r.details}
                    for r in risk_results
                ]
            },
        )
        session.flush()
        return candidate

    # Anti-collision lock
    locked = acquire_lock(
        session,
        candidate.condition_id,
        candidate.token_id,
        locked_by=f"candidate_{candidate.id}",
        ttl_seconds=lock_ttl_seconds,
    )
    if not locked:
        candidate.status = CandidateStatus.REJECTED.value
        candidate.status_reason = "collision: market already locked"
        _log_rejection(
            session,
            candidate_id=candidate.id,
            wallet=candidate.source_wallet,
            condition_id=candidate.condition_id,
            stage="collision",
            reason="market_locked",
        )
        session.flush()
        return candidate

    candidate.status = CandidateStatus.APPROVED.value
    candidate.status_reason = "risk_passed, lock_acquired"
    session.flush()
    return candidate


def paper_execute(
    session: Session,
    candidate: P1TradeCandidate,
    *,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    ttl_seconds: int = DEFAULT_PAPER_TTL_SECONDS,
) -> P1PaperTrade | None:
    """Create a paper trade for an approved candidate."""
    if candidate.status != CandidateStatus.APPROVED.value:
        return None

    now = dt.datetime.now(dt.timezone.utc)
    slip_mult = 1.0 + slippage_bps / 10000.0
    fill_price = round(min(candidate.our_limit_price * slip_mult, 0.99), 4)

    paper_trade = P1PaperTrade(
        candidate_id=candidate.id,
        condition_id=candidate.condition_id,
        token_id=candidate.token_id,
        side=candidate.side,
        limit_price=candidate.our_limit_price,
        fill_price=fill_price,
        size_usd=candidate.size_usd,
        simulated_slippage_bps=slippage_bps,
        status=PaperTradeStatus.FILLED.value,
        fill_reason=f"paper_fill slippage={slippage_bps}bps",
        placed_at=now,
        filled_at=now,
        expires_at=now + dt.timedelta(seconds=ttl_seconds),
    )
    session.add(paper_trade)

    candidate.status = CandidateStatus.PAPER_EXECUTED.value
    candidate.status_reason = f"paper_executed fill={fill_price:.4f} slip={slippage_bps}bps"

    release_lock(session, candidate.condition_id)
    session.flush()
    return paper_trade


def process_candidate_full(
    session: Session,
    event: P1WalletEvent,
    *,
    market: P1Market | None = None,
    wallet_profile: P1WalletProfile | None = None,
    filter_config: FilterConfig | None = None,
    risk_config: RiskConfig | None = None,
    limit_price_buffer_bps: float = 300.0,
    default_size_usd: float = 5.0,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    paper_ttl_seconds: int = DEFAULT_PAPER_TTL_SECONDS,
) -> tuple[P1TradeCandidate, P1PaperTrade | None]:
    """End-to-end pipeline: event -> candidate -> score -> filter -> approve -> paper-execute.

    Returns (candidate, paper_trade_or_none).
    """
    if filter_config is None:
        filter_config = FilterConfig()
    if risk_config is None:
        risk_config = RiskConfig()

    candidate = create_candidate_from_event(
        session, event,
        market=market,
        wallet_profile=wallet_profile,
        limit_price_buffer_bps=limit_price_buffer_bps,
        default_size_usd=default_size_usd,
    )

    candidate = score_candidate(session, candidate, market=market)

    candidate = filter_candidate(session, candidate, filter_config, market=market)
    if candidate.status == CandidateStatus.REJECTED.value:
        return candidate, None

    candidate = approve_candidate(session, candidate, risk_config)
    if candidate.status == CandidateStatus.REJECTED.value:
        return candidate, None

    paper_trade = paper_execute(
        session, candidate,
        slippage_bps=slippage_bps,
        ttl_seconds=paper_ttl_seconds,
    )
    return candidate, paper_trade
