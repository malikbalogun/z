"""Exposure / notional caps."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bot.exposure import category_exposure_usd, condition_exposure_usd, rolling_notional_usd
from bot.models import TradeRecord


class TestRollingNotional(unittest.TestCase):
    def test_sums_recent(self):
        now = (datetime.now(timezone.utc)).isoformat()
        old = "2020-01-01T00:00:00+00:00"
        trades = [
            TradeRecord("1", "q", "c", "t", "BUY", 0.5, 10, 5.0, "filled", now, "Yes", "s"),
            TradeRecord("2", "q", "c", "t", "BUY", 0.5, 10, 3.0, "cancelled", now, "Yes", "s"),
            TradeRecord("3", "q", "c", "t", "BUY", 0.5, 10, 99.0, "filled", old, "Yes", "s"),
        ]
        self.assertAlmostEqual(rolling_notional_usd(trades, hours=24.0), 5.0)


class TestConditionExposure(unittest.TestCase):
    def test_positions_and_orders(self):
        pos = [{"condition_id": "c1", "value": 10.0}]
        oo = [
            {"condition_id": "c1", "price": 0.5, "original_size": 20, "size_matched": 0, "side": "BUY"},
        ]
        self.assertAlmostEqual(condition_exposure_usd("c1", positions=pos, open_orders=oo), 10.0 + 10.0)

    def test_category_exposure(self):
        pos = [{"condition_id": "c1", "value": 12.0}]
        oo = [{"condition_id": "c1", "price": 0.5, "original_size": 8, "size_matched": 0, "side": "BUY"}]
        m = {"c1": "politics"}
        self.assertAlmostEqual(category_exposure_usd("politics", positions=pos, open_orders=oo, categories_by_condition=m), 16.0)


if __name__ == "__main__":
    unittest.main()
