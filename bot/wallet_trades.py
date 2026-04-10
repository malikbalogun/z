"""Wallet trade history from Polymarket Data API (collectmarkets2-style analysis input)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot.http_retry import get_json_retry

log = logging.getLogger("polymarket.wallet_trades")

TRADES_URL = "https://data-api.polymarket.com/trades"


async def fetch_wallet_trades(
    http: httpx.AsyncClient,
    wallet: str,
    *,
    limit: int = 80,
) -> list[dict[str, Any]]:
    """Public Data API: recent trades for a wallet (lowercase 0x…)."""
    w = (wallet or "").strip().lower()
    if not w.startswith("0x"):
        return []
    try:
        data = await get_json_retry(
            http,
            TRADES_URL,
            params={"user": w, "limit": str(min(500, max(1, limit)))},
        )
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("wallet trades %s…: %s", w[:12], e)
        return []
