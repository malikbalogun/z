"""
Lightweight copy-trade signals from Polymarket Data API activity stream.
Requires COPY_WATCH_WALLETS and AGENT_COPY=true.
"""

from __future__ import annotations

import logging
from typing import Any, Set

import httpx

from bot.categories import classify_market
from bot.http_retry import get_json_retry
from bot.models import TradeIntent

log = logging.getLogger("polymarket.agent.copy")

ACTIVITY_URL = "https://data-api.polymarket.com/activity"


def _extract_token_id(entry: dict) -> str | None:
    asset = entry.get("asset") or entry.get("asset_id")
    if isinstance(asset, str) and len(asset) > 30:
        return asset
    if isinstance(asset, dict):
        for k in ("token_id", "tokenId", "id"):
            v = asset.get(k)
            if isinstance(v, str) and len(v) > 20:
                return v
    for k in ("clobTokenId", "tokenId", "token_id", "asset_id"):
        v = entry.get(k)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None


def _extract_price(entry: dict) -> float | None:
    for k in ("price", "avgPrice", "avg_price"):
        v = entry.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


class CopySignalAgent:
    name = "copy_signal"
    priority = 100

    def __init__(self, settings: Any):
        self.settings = settings
        self._seen: Set[str] = set()
        self._cold_start = True

    async def propose(self, http: httpx.AsyncClient) -> list[TradeIntent]:
        if not self.settings.agent_copy or not self.settings.copy_watch_wallets:
            return []

        intents: list[TradeIntent] = []
        for wallet in self.settings.copy_watch_wallets:
            try:
                rows = await get_json_retry(
                    http,
                    ACTIVITY_URL,
                    params={"user": wallet, "limit": "40"},
                )
                if not isinstance(rows, list):
                    continue
            except Exception as e:
                log.warning("copy poll %s…: %s", wallet[:10], e)
                continue

            for entry in rows:
                if entry.get("type") and str(entry["type"]).upper() != "TRADE":
                    continue
                side = str(entry.get("side", "")).upper()
                if side and side != "BUY":
                    continue

                tid = _extract_token_id(entry)
                if not tid:
                    continue

                txh = str(entry.get("transactionHash") or entry.get("id") or "")
                dedupe = f"{wallet}:{txh}:{tid}"
                if dedupe in self._seen:
                    continue
                self._seen.add(dedupe)
                if self._cold_start:
                    continue

                px = _extract_price(entry) or 0.5
                px = max(0.02, min(0.98, px))
                max_px = round(min(px * 1.03, 0.99), 4)

                title = str(entry.get("title") or entry.get("question") or "unknown")
                cond = str(entry.get("conditionId") or entry.get("condition_id") or "")
                pseudo = {
                    "question": title,
                    "slug": str(entry.get("slug") or ""),
                    "tags": entry.get("tags"),
                }
                cat = classify_market(pseudo)

                usdc = float(entry.get("usdcSize") or entry.get("amount") or self.settings.default_bet_usd)
                usdc = max(self.settings.min_bet_usd, min(self.settings.max_bet_usd, usdc))

                intents.append(
                    TradeIntent(
                        agent=self.name,
                        priority=self.priority,
                        token_id=tid,
                        condition_id=cond or tid[:16],
                        question=title[:500],
                        outcome=str(entry.get("outcome") or "unknown"),
                        side="BUY",
                        max_price=max_px,
                        size_usd=usdc,
                        category=cat,
                        strategy="copy_trade",
                        reason=f"wallet={wallet[:10]}… px~{px:.3f}",
                        reference_price=px,
                    )
                )

        if self._cold_start:
            self._cold_start = False
            log.info(
                "CopySignalAgent: cold start done — %d activity keys seeded (no replay)",
                len(self._seen),
            )

        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2500:])

        log.info("CopySignalAgent: %d new signals", len(intents))
        return intents
