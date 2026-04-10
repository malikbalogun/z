"""Scans Gamma tradeables; uses CLOB mid vs simple value bands (book-aware via midpoint API)."""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable, Set

from bot.categories import MarketCategory
from bot.clob_utils import parse_midpoint
from bot.models import TradeIntent

log = logging.getLogger("polymarket.agent.value")


class ValueEdgeAgent:
    name = "value_edge"
    priority = 50

    def __init__(self, settings: Any):
        self.settings = settings

    async def propose(
        self,
        clob: Any,
        markets: list[dict],
        position_tokens: Set[str],
        rate_limit: Callable[[], Awaitable[None]],
    ) -> list[TradeIntent]:
        out: list[TradeIntent] = []
        for m in markets:
            if any(t in position_tokens for t in m.get("tokens", [])):
                continue

            cat: MarketCategory = m["category"]
            tokens = m["tokens"]
            outcomes = m["outcomes"]
            prices = m["prices"]
            if len(tokens) < 2 or len(prices) < 2:
                continue

            await rate_limit()
            try:
                mid0 = clob.get_midpoint(token_id=tokens[0])
                parsed = parse_midpoint(mid0)
                p0 = float(parsed) if parsed is not None else float(prices[0])
            except Exception:
                p0 = float(prices[0])
            await rate_limit()
            try:
                mid1 = clob.get_midpoint(token_id=tokens[1])
                parsed = parse_midpoint(mid1)
                p1 = float(parsed) if parsed is not None else float(prices[1])
            except Exception:
                p1 = float(prices[1])

            liq = float(m.get("liquidity", 0))
            if liq < self.settings.min_clob_liquidity_usd:
                continue

            liq_need = max(float(self.settings.value_liq_floor_usd), float(self.settings.min_clob_liquidity_usd))
            y_lo = float(self.settings.value_yes_low)
            y_hi = float(self.settings.value_yes_high)
            yn_min = float(self.settings.value_no_yes_min)
            yn_max = float(self.settings.value_no_no_max)

            # Value YES (token 0)
            if y_lo <= p0 <= y_hi and liq >= liq_need:
                if p0 <= 0.01 or p0 >= 0.99:
                    continue
                shares = self.settings.default_bet_usd / p0
                out.append(
                    TradeIntent(
                        agent=self.name,
                        priority=self.priority,
                        token_id=tokens[0],
                        condition_id=m["condition_id"],
                        question=m["question"],
                        outcome=str(outcomes[0]),
                        side="BUY",
                        max_price=round(min(p0 * 1.01, 0.99), 4),
                        size_usd=self.settings.default_bet_usd,
                        category=cat,
                        strategy="value_yes",
                        reason=f"mid_yes={p0:.3f} liq={liq:.0f}",
                        reference_price=p0,
                    )
                )

            # Value NO (token 1) when YES rich
            yes_p = p0
            no_p = p1
            if yes_p >= yn_min and no_p <= yn_max and liq >= liq_need:
                if no_p <= 0.01 or no_p >= 0.99:
                    continue
                out.append(
                    TradeIntent(
                        agent=self.name,
                        priority=self.priority,
                        token_id=tokens[1],
                        condition_id=m["condition_id"],
                        question=m["question"],
                        outcome=str(outcomes[1] if len(outcomes) > 1 else "No"),
                        side="BUY",
                        max_price=round(min(no_p * 1.01, 0.99), 4),
                        size_usd=self.settings.default_bet_usd,
                        category=cat,
                        strategy="value_no",
                        reason=f"mid_yes={yes_p:.3f} mid_no={no_p:.3f} liq={liq:.0f}",
                        reference_price=no_p,
                    )
                )

        out.sort(key=lambda x: -x.priority)
        log.info("ValueEdgeAgent: %d candidate intents", len(out))
        return out
