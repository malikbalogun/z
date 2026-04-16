"""Polymarket leaderboard: auto-discover top wallets by PnL for copy-trading."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot.http_retry import get_json_retry

log = logging.getLogger("polymarket.leaderboard")

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"

CATEGORIES = ("OVERALL", "POLITICS", "SPORTS", "CRYPTO", "FINANCE")
TIME_PERIODS = ("DAY", "WEEK", "MONTH", "ALL")


async def fetch_leaderboard(
    http: httpx.AsyncClient,
    *,
    category: str = "OVERALL",
    time_period: str = "MONTH",
    sort_by: str = "PNL",
    limit: int = 25,
) -> list[dict[str, Any]]:
    cat = category.upper()
    if cat not in CATEGORIES:
        cat = "OVERALL"
    tp = time_period.upper()
    if tp not in TIME_PERIODS:
        tp = "MONTH"
    sb = sort_by.upper()
    if sb not in ("PNL", "VOL"):
        sb = "PNL"
    lim = max(1, min(50, limit))

    try:
        data = await get_json_retry(
            http,
            LEADERBOARD_URL,
            params={
                "category": cat,
                "timePeriod": tp,
                "sortBy": sb,
                "limit": str(lim),
            },
        )
        if not isinstance(data, list):
            log.warning("leaderboard returned non-list: %s", type(data))
            return []
        return data
    except Exception as e:
        log.warning("leaderboard fetch failed: %s", e)
        return []


async def discover_top_wallets(
    http: httpx.AsyncClient,
    *,
    categories: list[str] | None = None,
    time_period: str = "MONTH",
    limit_per_category: int = 10,
    min_pnl: float = 0.0,
) -> list[dict[str, Any]]:
    """Fetch top wallets across one or more categories, deduplicated.

    Returns a list of dicts with: wallet, rank, pnl, vol, userName, category.
    """
    cats = categories or ["OVERALL"]
    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    for cat in cats:
        entries = await fetch_leaderboard(
            http,
            category=cat,
            time_period=time_period,
            limit=limit_per_category,
        )
        for entry in entries:
            wallet = (entry.get("proxyWallet") or "").strip().lower()
            if not wallet or not wallet.startswith("0x") or len(wallet) != 42:
                continue
            pnl = float(entry.get("pnl") or 0)
            if pnl < min_pnl:
                continue
            if wallet in seen:
                continue
            seen.add(wallet)
            results.append({
                "wallet": wallet,
                "rank": int(entry.get("rank") or 0),
                "pnl": pnl,
                "vol": float(entry.get("vol") or 0),
                "userName": entry.get("userName") or "",
                "category": cat,
            })

    results.sort(key=lambda x: -x["pnl"])
    return results
