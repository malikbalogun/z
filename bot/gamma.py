"""Gamma API: list tradeable binary markets with CLOB tokens."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from bot.categories import classify_market
from bot.http_retry import get_json_retry

log = logging.getLogger("polymarket.gamma")

GAMMA = "https://gamma-api.polymarket.com/markets"


async def scan_tradeable_markets(
    http: httpx.AsyncClient,
    rate_limit_cb,
    max_pages: int = 2,
    min_liquidity: float = 500.0,
    min_volume: float = 1000.0,
) -> tuple[list[dict[str, Any]], dict[str, dict]]:
    """
    Returns (normalized_markets, condition_id -> raw_gamma_market cache).
    """
    markets: list[dict] = []
    cache: dict[str, dict] = {}

    for page in range(max_pages):
        await rate_limit_cb()
        offset = page * 100
        try:
            batch = await get_json_retry(
                http,
                GAMMA,
                params={
                    "limit": 100,
                    "offset": offset,
                    "active": "true",
                    "closed": "false",
                    "order": "liquidityClob",
                    "ascending": "false",
                },
            )
        except Exception as e:
            log.warning("Gamma page %s: %s", page, e)
            break
        if not batch:
            break
        if not isinstance(batch, list):
            break
        markets.extend(batch)

    for m in markets:
        cid = m.get("condition_id") or m.get("conditionId") or ""
        if cid:
            cache[cid] = m

    tradeable: list[dict[str, Any]] = []
    for m in markets:
        tokens = m.get("clobTokenIds", m.get("clob_token_ids", ""))
        if not tokens:
            continue
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens) if tokens.startswith("[") else [tokens]
            except json.JSONDecodeError:
                continue
        if len(tokens) < 2:
            continue

        if not m.get("enableOrderBook", m.get("enable_order_book", True)):
            continue

        prices_raw = m.get("outcomePrices", m.get("outcome_prices", ""))
        if isinstance(prices_raw, str):
            try:
                prices = json.loads(prices_raw) if prices_raw else [0.5, 0.5]
            except json.JSONDecodeError:
                prices = [0.5, 0.5]
        else:
            prices = prices_raw or [0.5, 0.5]
        prices = [float(p) for p in prices]

        liq = float(m.get("liquidityClob", m.get("liquidity_clob", 0)) or 0)
        vol = float(m.get("volume", 0) or 0)
        if liq < min_liquidity or vol < min_volume:
            continue

        cid = m.get("condition_id", m.get("conditionId", ""))
        outcomes = m.get("outcomes", '["Yes","No"]')
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = ["Yes", "No"]

        nm = {
            "condition_id": cid,
            "question": m.get("question", "Unknown"),
            "tokens": tokens,
            "prices": prices,
            "outcomes": outcomes,
            "liquidity": liq,
            "volume": vol,
            "slug": m.get("slug", ""),
            "category": classify_market(m),
            "raw": m,
        }
        tradeable.append(nm)

    log.info("Gamma: %d raw -> %d tradeable", len(markets), len(tradeable))
    return tradeable, cache
