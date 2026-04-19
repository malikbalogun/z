"""Market data ingestion: fetch from Gamma API and persist to p1_markets."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from bot.categories import classify_market
from bot.phase1.models import P1Market, P1MarketClassification

log = logging.getLogger("polymarket.phase1.market_ingest")


def _parse_tokens(m: dict[str, Any]) -> list[str]:
    tokens = m.get("clobTokenIds", m.get("clob_token_ids", ""))
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens) if tokens.startswith("[") else [tokens]
        except json.JSONDecodeError:
            tokens = []
    return tokens if isinstance(tokens, list) else []


def _parse_outcomes(m: dict[str, Any]) -> list[str]:
    outcomes = m.get("outcomes", '["Yes","No"]')
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            outcomes = ["Yes", "No"]
    return outcomes if isinstance(outcomes, list) else ["Yes", "No"]


def _parse_prices(m: dict[str, Any]) -> list[float]:
    prices_raw = m.get("outcomePrices", m.get("outcome_prices", ""))
    if isinstance(prices_raw, str):
        try:
            prices = json.loads(prices_raw) if prices_raw else [0.5, 0.5]
        except json.JSONDecodeError:
            prices = [0.5, 0.5]
    else:
        prices = prices_raw or [0.5, 0.5]
    return [float(p) for p in prices]


def upsert_market_from_gamma(session: Session, raw: dict[str, Any]) -> P1Market | None:
    """Insert or update a single market from Gamma API response."""
    cid = str(raw.get("condition_id") or raw.get("conditionId") or "").strip()
    if not cid:
        return None

    tokens = _parse_tokens(raw)
    if len(tokens) < 2:
        return None

    question = str(raw.get("question", "Unknown"))
    slug = str(raw.get("slug", ""))
    description = str(raw.get("description", ""))
    outcomes = _parse_outcomes(raw)
    prices = _parse_prices(raw)
    liq = float(raw.get("liquidityClob", raw.get("liquidity_clob", 0)) or 0)
    vol = float(raw.get("volume", 0) or 0)
    end_date = str(raw.get("endDate") or raw.get("end_date_iso") or "")
    category = classify_market(raw).value
    active = bool(raw.get("active", True))

    existing = session.query(P1Market).filter(P1Market.condition_id == cid).first()
    if existing:
        existing.question = question
        existing.slug = slug
        existing.description = description
        existing.tokens_json = json.dumps(tokens)
        existing.outcomes_json = json.dumps(outcomes)
        existing.prices_json = json.dumps(prices)
        existing.liquidity = liq
        existing.volume = vol
        existing.category = category
        existing.active = active
        existing.end_date = end_date or existing.end_date
        existing.raw_json = json.dumps(raw, default=str)
        return existing

    market = P1Market(
        condition_id=cid,
        question=question,
        slug=slug,
        description=description,
        tokens_json=json.dumps(tokens),
        outcomes_json=json.dumps(outcomes),
        prices_json=json.dumps(prices),
        liquidity=liq,
        volume=vol,
        category=category,
        active=active,
        end_date=end_date or None,
        raw_json=json.dumps(raw, default=str),
    )
    session.add(market)
    return market


def ingest_markets_batch(session: Session, raw_markets: list[dict[str, Any]]) -> int:
    """Upsert a batch of Gamma markets. Returns count of markets processed."""
    count = 0
    for raw in raw_markets:
        result = upsert_market_from_gamma(session, raw)
        if result is not None:
            count += 1
    session.flush()
    return count


def classify_and_persist(session: Session, condition_id: str, market_data: dict[str, Any]) -> str:
    """Classify a market and persist the classification."""
    category = classify_market(market_data).value
    existing = session.query(P1MarketClassification).filter(
        P1MarketClassification.condition_id == condition_id
    ).first()
    if existing:
        existing.category = category
        return category

    session.add(P1MarketClassification(
        condition_id=condition_id,
        category=category,
        confidence=1.0,
        rule_matched="regex",
    ))
    return category


def get_market(session: Session, condition_id: str) -> P1Market | None:
    return session.query(P1Market).filter(P1Market.condition_id == condition_id).first()


def get_active_markets(session: Session, min_liquidity: float = 0.0) -> list[P1Market]:
    q = session.query(P1Market).filter(P1Market.active == True)  # noqa: E712
    if min_liquidity > 0:
        q = q.filter(P1Market.liquidity >= min_liquidity)
    return list(q.order_by(P1Market.liquidity.desc()).all())
