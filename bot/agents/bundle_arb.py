"""
Binary complete-set arb: best YES ask + best NO ask < 1 (minus buffer) → BUY both legs.
Uses order-book asks (conservative vs mid-only).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, Set

from bot.models import TradeIntent
from bot.orderbook import best_bid_ask

log = logging.getLogger("polymarket.agent.bundle")


class BundleArbAgent:
    name = "bundle_arb"
    priority = 72

    def __init__(self, settings: Any):
        self.settings = settings

    async def propose(
        self,
        clob: Any,
        markets: list[dict],
        position_tokens: Set[str],
        rate_limit: Callable[[], Awaitable[None]],
    ) -> list[TradeIntent]:
        if not getattr(self.settings, "agent_bundle", False):
            return []
        max_pair = float(getattr(self.settings, "bundle_max_pair_cost", 0.994))
        liq_floor = max(
            float(self.settings.min_clob_liquidity_usd),
            float(getattr(self.settings, "bundle_min_liquidity_usd", 1500.0)),
        )

        out: list[TradeIntent] = []
        for m in markets:
            tokens = m.get("tokens", [])
            outcomes = m.get("outcomes", ["Yes", "No"])
            if len(tokens) < 2:
                continue
            if any(t in position_tokens for t in tokens[:2]):
                continue

            liq = float(m.get("liquidity", 0))
            if liq < liq_floor:
                continue

            cat = m["category"]
            cid = m.get("condition_id", "")
            q = m.get("question", "")

            await rate_limit()
            _, ask0 = best_bid_ask(clob, tokens[0])
            await rate_limit()
            _, ask1 = best_bid_ask(clob, tokens[1])
            if ask0 is None or ask1 is None:
                continue
            if ask0 <= 0.01 or ask1 <= 0.01 or ask0 + ask1 > max_pair:
                continue

            leg_usd = max(
                float(self.settings.min_bet_usd),
                float(self.settings.default_bet_usd) * 0.5,
            )
            bid = str(uuid.uuid4())
            pad = round(min(ask0 * 1.01, 0.99), 4)
            pad1 = round(min(ask1 * 1.01, 0.99), 4)

            out.append(
                TradeIntent(
                    agent=self.name,
                    priority=self.priority,
                    token_id=tokens[0],
                    condition_id=cid,
                    question=q,
                    outcome=str(outcomes[0] if outcomes else "Yes"),
                    side="BUY",
                    max_price=pad,
                    size_usd=leg_usd,
                    category=cat,
                    strategy="bundle_yes_leg",
                    reason=f"ask_yes+ask_no={ask0:.4f}+{ask1:.4f}={ask0 + ask1:.4f}<={max_pair}",
                    reference_price=None,
                    bundle_id=bid,
                )
            )
            out.append(
                TradeIntent(
                    agent=self.name,
                    priority=self.priority,
                    token_id=tokens[1],
                    condition_id=cid,
                    question=q,
                    outcome=str(outcomes[1] if len(outcomes) > 1 else "No"),
                    side="BUY",
                    max_price=pad1,
                    size_usd=leg_usd,
                    category=cat,
                    strategy="bundle_no_leg",
                    reason=f"ask_yes+ask_no={ask0:.4f}+{ask1:.4f}={ask0 + ask1:.4f}<={max_pair}",
                    reference_price=None,
                    bundle_id=bid,
                )
            )

        log.info("BundleArbAgent: %d leg intents", len(out))
        return out
