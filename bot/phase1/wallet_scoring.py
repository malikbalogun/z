"""Wallet scoring: compute and persist quality scores for watched wallets."""

from __future__ import annotations

import json
import logging
import math
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from bot.phase1.models import P1WalletEvent, P1WalletProfile

log = logging.getLogger("polymarket.phase1.wallet_scoring")


def compute_wallet_score(
    events: list[P1WalletEvent],
    *,
    overrides: dict[str, float] | None = None,
    wallet: str = "",
) -> tuple[float, dict[str, float]]:
    """Deterministic wallet quality score in [0,1].

    Uses only public activity features:
      - Activity factor (log-scaled count)
      - Known-outcome ratio (yes/no vs unknown)
      - Sane-price ratio (0.05-0.95 range)
      - Median USD size factor

    Returns (score, component_dict).
    """
    buy_events = [e for e in events if e.side == "BUY"]
    n = len(buy_events)
    if n == 0:
        return 0.0, {"n": 0.0, "outcome": 0.0, "price": 0.0, "size": 0.0}

    known_outcome = sum(1 for e in buy_events if e.outcome in ("yes", "no")) / n
    sane_price = sum(1 for e in buy_events if 0.05 <= e.price <= 0.95) / n
    usd_values = [e.usdc_value for e in buy_events if e.usdc_value > 0]
    med_usd = median(usd_values) if usd_values else 0.0
    size_factor = min(max(med_usd / 50.0, 0.0), 1.0)
    activity_factor = min(max(math.log1p(n) / math.log1p(80), 0.0), 1.0)

    score = (
        0.40 * activity_factor
        + 0.25 * known_outcome
        + 0.20 * sane_price
        + 0.15 * size_factor
    )

    if overrides:
        w_key = wallet.strip().lower()
        try:
            score += float(overrides.get(w_key, 0.0) or 0.0)
        except (TypeError, ValueError):
            pass

    score = min(max(score, 0.0), 1.0)
    return score, {
        "n": float(n),
        "outcome": round(known_outcome, 4),
        "price": round(sane_price, 4),
        "size": round(size_factor, 4),
        "activity": round(activity_factor, 4),
        "median_usd": round(med_usd, 2),
    }


def score_and_persist_wallet(
    session: Session,
    wallet: str,
    *,
    overrides: dict[str, float] | None = None,
    limit: int = 200,
) -> tuple[float, dict[str, float]]:
    """Recompute wallet score from DB events and persist to p1_wallet_profiles."""
    wallet = wallet.strip().lower()
    events = list(
        session.query(P1WalletEvent)
        .filter(P1WalletEvent.wallet == wallet)
        .order_by(P1WalletEvent.ingested_at.desc())
        .limit(limit)
        .all()
    )

    score, details = compute_wallet_score(events, overrides=overrides, wallet=wallet)

    profile = session.query(P1WalletProfile).filter(
        P1WalletProfile.wallet == wallet
    ).first()

    if profile:
        profile.score = score
        profile.trade_count = int(details["n"])
        profile.known_outcome_ratio = details["outcome"]
        profile.sane_price_ratio = details["price"]
        profile.median_usd = details["median_usd"]
        profile.score_details_json = json.dumps(details, default=str)
    else:
        session.add(P1WalletProfile(
            wallet=wallet,
            score=score,
            trade_count=int(details["n"]),
            known_outcome_ratio=details["outcome"],
            sane_price_ratio=details["price"],
            median_usd=details["median_usd"],
            score_details_json=json.dumps(details, default=str),
        ))

    session.flush()
    return score, details


def get_wallet_profile(session: Session, wallet: str) -> P1WalletProfile | None:
    return session.query(P1WalletProfile).filter(
        P1WalletProfile.wallet == wallet.strip().lower()
    ).first()
