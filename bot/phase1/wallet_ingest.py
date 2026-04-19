"""Wallet event ingestion: fetch from Data API and persist to p1_wallet_events."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from bot.copy_rules import extract_price, extract_token_id
from bot.phase1.models import P1WalletEvent

log = logging.getLogger("polymarket.phase1.wallet_ingest")


def _parse_event_time(entry: dict[str, Any]):
    """Best-effort parse of event timestamp."""
    import datetime as dt
    for k in ("timestamp", "createdAt", "created_at", "time"):
        v = entry.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            try:
                return dt.datetime.fromtimestamp(v, tz=dt.timezone.utc)
            except (ValueError, OSError):
                continue
        sv = str(v).strip()
        if not sv:
            continue
        if sv.endswith("Z"):
            sv = sv[:-1] + "+00:00"
        try:
            return dt.datetime.fromisoformat(sv)
        except ValueError:
            continue
    return None


def build_wallet_event(entry: dict[str, Any], wallet: str) -> dict[str, Any] | None:
    """Parse a single activity/trade entry into event fields.

    Returns a dict of column values or None if the entry is not usable.
    """
    wallet = wallet.strip().lower()
    if not wallet.startswith("0x"):
        return None

    token_id = extract_token_id(entry) or ""
    if not token_id:
        return None

    tx_hash = str(entry.get("transactionHash") or entry.get("id") or "").strip()
    side = str(entry.get("side", "BUY")).upper()
    price = extract_price(entry) or 0.0
    size = float(entry.get("size", 0) or 0)
    usdc_value = float(entry.get("usdcSize") or entry.get("amount") or 0)
    outcome = str(entry.get("outcome", "unknown")).strip().lower()
    title = str(entry.get("title") or entry.get("question") or "")
    condition_id = str(entry.get("conditionId") or entry.get("condition_id") or "")
    event_type = str(entry.get("type", "trade")).lower()
    event_time = _parse_event_time(entry)

    return {
        "wallet": wallet,
        "tx_hash": tx_hash,
        "token_id": token_id,
        "condition_id": condition_id,
        "side": side,
        "price": price,
        "size": size,
        "usdc_value": usdc_value,
        "outcome": outcome,
        "title": title,
        "event_type": event_type,
        "raw_json": json.dumps(entry, default=str),
        "event_time": event_time,
    }


def ingest_wallet_event(session: Session, entry: dict[str, Any], wallet: str) -> P1WalletEvent | None:
    """Persist a single wallet event (dedup by wallet+tx_hash+token_id)."""
    fields = build_wallet_event(entry, wallet)
    if fields is None:
        return None

    existing = session.query(P1WalletEvent).filter(
        P1WalletEvent.wallet == fields["wallet"],
        P1WalletEvent.tx_hash == fields["tx_hash"],
        P1WalletEvent.token_id == fields["token_id"],
    ).first()

    if existing:
        return None  # already ingested

    event = P1WalletEvent(**fields)
    session.add(event)
    return event


def ingest_wallet_events_batch(
    session: Session,
    entries: list[dict[str, Any]],
    wallet: str,
) -> int:
    """Ingest a batch of wallet events. Returns count of new events inserted."""
    count = 0
    for entry in entries:
        result = ingest_wallet_event(session, entry, wallet)
        if result is not None:
            count += 1
    session.flush()
    return count


def get_wallet_events(
    session: Session,
    wallet: str,
    *,
    limit: int = 100,
    side: str | None = None,
) -> list[P1WalletEvent]:
    q = session.query(P1WalletEvent).filter(
        P1WalletEvent.wallet == wallet.strip().lower()
    )
    if side:
        q = q.filter(P1WalletEvent.side == side.upper())
    return list(q.order_by(P1WalletEvent.ingested_at.desc()).limit(limit).all())
