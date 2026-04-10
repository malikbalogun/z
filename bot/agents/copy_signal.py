"""
Lightweight copy-trade signals from Polymarket Data API activity stream.
Requires COPY_WATCH_WALLETS and AGENT_COPY=true.
"""

from __future__ import annotations

import logging
from typing import Any, Set

import httpx

from bot.categories import MarketCategory
from bot.copy_rules import build_candidate, limit_price_with_buffer, passes_filters, wallet_score
from bot.http_retry import get_json_retry
from bot.models import TradeIntent

log = logging.getLogger("polymarket.agent.copy")

ACTIVITY_URL = "https://data-api.polymarket.com/activity"


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

            score, parts = wallet_score(
                rows if isinstance(rows, list) else [],
                wallet=wallet,
                default_bet_usd=float(self.settings.default_bet_usd),
                settings=self.settings,
            )
            min_score = float(getattr(self.settings, "copy_min_wallet_score", 0.0) or 0.0)
            if score < min_score:
                log.info(
                    "copy skip wallet=%s score=%.3f < %.3f parts=%s",
                    wallet[:10],
                    score,
                    min_score,
                    {k: round(v, 3) for k, v in parts.items()},
                )
                continue

            for entry in rows:
                c = build_candidate(entry, wallet, float(self.settings.default_bet_usd))
                if c is None:
                    continue

                if c.tx_key in self._seen:
                    continue
                self._seen.add(c.tx_key)
                if self._cold_start:
                    continue

                ok, _reason = passes_filters(self.settings, c)
                if not ok:
                    continue
                max_px = limit_price_with_buffer(self.settings, c.price)
                usdc = max(self.settings.min_bet_usd, min(self.settings.max_bet_usd, c.usdc))
                cond = str(entry.get("conditionId") or entry.get("condition_id") or "")
                try:
                    cat = MarketCategory(c.category)
                except ValueError:
                    cat = MarketCategory.OTHER

                intents.append(
                    TradeIntent(
                        agent=self.name,
                        priority=self.priority,
                        token_id=c.token_id,
                        condition_id=cond or c.token_id[:16],
                        question=c.title[:500],
                        outcome=str(entry.get("outcome") or c.outcome or "unknown"),
                        side="BUY",
                        max_price=max_px,
                        size_usd=usdc,
                        category=cat,
                        strategy="copy_trade",
                        reason=f"wallet={wallet[:10]}… px~{c.price:.3f}",
                        reference_price=c.price,
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
