"""Read/write bot_settings key-value store."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from bot.db.models import BotSetting, TradeLog, session_scope


def load_all_kv() -> dict[str, str]:
    with session_scope() as s:
        rows = list(s.scalars(select(BotSetting)).all())
        return {r.key: r.value for r in rows}


def upsert_kv(key: str, value: str) -> None:
    with session_scope() as s:
        row = s.get(BotSetting, key)
        if row is None:
            s.add(BotSetting(key=key, value=value))
        else:
            row.value = value
        s.commit()


def upsert_many_kv(data: dict[str, Any]) -> None:
    with session_scope() as s:
        for k, v in data.items():
            val = v if isinstance(v, str) else json.dumps(v)
            row = s.get(BotSetting, str(k))
            if row is None:
                s.add(BotSetting(key=str(k), value=val))
            else:
                row.value = val
        s.commit()


def append_trade_log(
    *,
    order_id: str,
    market_question: str,
    condition_id: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    cost_usd: float,
    status: str,
    strategy: str,
    outcome: str,
    reconcile_note: str | None = None,
) -> None:
    with session_scope() as s:
        s.add(
            TradeLog(
                order_id=order_id,
                market_question=market_question,
                condition_id=condition_id,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                cost_usd=cost_usd,
                status=status,
                strategy=strategy,
                outcome=outcome,
                reconcile_note=reconcile_note,
            )
        )
        s.commit()


def recent_trade_statuses(limit: int = 40) -> list[str]:
    with session_scope() as s:
        q = select(TradeLog.status).order_by(TradeLog.id.desc()).limit(limit)
        return list(s.scalars(q).all())
