"""
Cross-feed latency: Gamma outcomePrices vs fresh CLOB mid.
When the slower feed implies a richer price than the book, BUY on the book (cheap vs stale quote).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Set

from bot.clob_utils import parse_midpoint
from bot.models import TradeIntent

log = logging.getLogger("polymarket.agent.latency")


class LatencyArbAgent:
    name = "latency_arb"
    priority = 65

    def __init__(self, settings: Any):
        self.settings = settings

    async def propose(
        self,
        clob: Any,
        markets: list[dict],
        position_tokens: Set[str],
        rate_limit: Callable[[], Awaitable[None]],
    ) -> list[TradeIntent]:
        if not getattr(self.settings, "agent_latency", False):
            return []
        min_bps = float(getattr(self.settings, "latency_min_dislocation_bps", 120.0))

        out: list[TradeIntent] = []
        for m in markets:
            tokens = m.get("tokens", [])
            prices = m.get("prices", [])
            outcomes = m.get("outcomes", ["Yes", "No"])
            if len(tokens) < 2 or len(prices) < 2:
                continue

            cat = m["category"]
            cid = m.get("condition_id", "")
            q = m.get("question", "")

            gamma_yes = float(prices[0])
            gamma_no = float(prices[1])

            await rate_limit()
            try:
                raw0 = clob.get_midpoint(token_id=tokens[0])
                p0 = float(parse_midpoint(raw0)) if parse_midpoint(raw0) is not None else gamma_yes
            except Exception:
                p0 = gamma_yes
            await rate_limit()
            try:
                raw1 = clob.get_midpoint(token_id=tokens[1])
                p1 = float(parse_midpoint(raw1)) if parse_midpoint(raw1) is not None else gamma_no
            except Exception:
                p1 = gamma_no

            if p0 <= 0.01 or p0 >= 0.99 or p1 <= 0.01 or p1 >= 0.99:
                continue

            def disloc_bps(slow: float, fast: float) -> float:
                if fast <= 1e-9:
                    return 0.0
                return (slow - fast) / fast * 10000.0

            # Stale slow quote above CLOB → book is cheap → BUY that outcome token.
            d_yes = disloc_bps(gamma_yes, p0)
            if d_yes >= min_bps and tokens[0] not in position_tokens:
                out.append(
                    TradeIntent(
                        agent=self.name,
                        priority=self.priority,
                        token_id=tokens[0],
                        condition_id=cid,
                        question=q,
                        outcome=str(outcomes[0] if outcomes else "Yes"),
                        side="BUY",
                        max_price=round(min(p0 * 1.02, 0.99), 4),
                        size_usd=float(self.settings.default_bet_usd),
                        category=cat,
                        strategy="latency_gamma_vs_clob_yes",
                        reason=f"gamma_yes={gamma_yes:.4f} clob={p0:.4f} disloc_bps={d_yes:.0f}",
                        reference_price=p0,
                    )
                )

            d_no = disloc_bps(gamma_no, p1)
            if d_no >= min_bps and tokens[1] not in position_tokens:
                out.append(
                    TradeIntent(
                        agent=self.name,
                        priority=self.priority,
                        token_id=tokens[1],
                        condition_id=cid,
                        question=q,
                        outcome=str(outcomes[1] if len(outcomes) > 1 else "No"),
                        side="BUY",
                        max_price=round(min(p1 * 1.02, 0.99), 4),
                        size_usd=float(self.settings.default_bet_usd),
                        category=cat,
                        strategy="latency_gamma_vs_clob_no",
                        reason=f"gamma_no={gamma_no:.4f} clob={p1:.4f} disloc_bps={d_no:.0f}",
                        reference_price=p1,
                    )
                )

        log.info("LatencyArbAgent: %d intents (min_bps=%.0f)", len(out), min_bps)
        return out
