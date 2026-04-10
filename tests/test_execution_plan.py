"""Execution grouping for bundle legs."""

from __future__ import annotations

import unittest

from bot.categories import MarketCategory
from bot.execution_plan import plan_execution_units
from bot.models import TradeIntent


def _it(token: str, pri: int, bundle: str | None = None) -> TradeIntent:
    return TradeIntent(
        agent="a",
        priority=pri,
        token_id=token,
        condition_id="c",
        question="q",
        outcome="Yes",
        side="BUY",
        max_price=0.5,
        size_usd=5.0,
        category=MarketCategory.OTHER,
        strategy="s",
        reason="r",
        bundle_id=bundle,
    )


class TestExecutionPlan(unittest.TestCase):
    def test_bundle_pair_one_unit(self):
        uid = "b1"
        a = _it("aaa", 70, uid)
        b = _it("bbb", 70, uid)
        units = plan_execution_units([b, a])
        self.assertEqual(len(units), 1)
        self.assertEqual(len(units[0]), 2)
        self.assertEqual({units[0][0].token_id, units[0][1].token_id}, {"aaa", "bbb"})

    def test_single_and_bundle(self):
        s = _it("solo", 50, None)
        uid = "x"
        u1 = _it("t1", 80, uid)
        u2 = _it("t2", 80, uid)
        units = plan_execution_units([s, u1, u2])
        self.assertEqual(len(units), 2)
        lens = sorted(len(u) for u in units)
        self.assertEqual(lens, [1, 2])

    def test_orphan_bundle_becomes_singles(self):
        uid = "orph"
        units = plan_execution_units([_it("a", 60, uid)])
        self.assertEqual(len(units), 1)
        self.assertEqual(len(units[0]), 1)


if __name__ == "__main__":
    unittest.main()
