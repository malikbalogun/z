"""Tests for Phase 2 trade worthiness assessment."""

from __future__ import annotations

import unittest

from bot.trade_worthiness import assess_trade_worthiness


class TestTradeWorthiness(unittest.TestCase):
    def test_worthy_trade(self):
        r = assess_trade_worthiness(
            entry_price=0.40,
            fair_price=0.55,
            size_usd=10.0,
            bid_notional=5000,
            ask_notional=5000,
            spread_bps=100,
        )
        self.assertTrue(r.worthy)
        self.assertGreater(r.composite_score, 0)

    def test_negative_ev_fails(self):
        r = assess_trade_worthiness(
            entry_price=0.60,
            fair_price=0.50,
            size_usd=10.0,
        )
        self.assertFalse(r.worthy)

    def test_slippage_gate(self):
        r = assess_trade_worthiness(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            max_slippage_bps=10,
        )
        self.assertFalse(r.worthy)
        self.assertIn("slippage", r.reason)

    def test_survivability_gate(self):
        r = assess_trade_worthiness(
            entry_price=0.40,
            fair_price=0.55,
            size_usd=10.0,
            bid_notional=2,
            ask_notional=2,
            min_survivability=0.9,
        )
        self.assertFalse(r.worthy)
        self.assertIn("survivability", r.reason)

    def test_latency_penalty(self):
        r_fast = assess_trade_worthiness(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            latency_ms=100,
        )
        r_slow = assess_trade_worthiness(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            latency_ms=2000,
        )
        self.assertGreater(r_fast.latency_penalty_bps, 0)
        self.assertGreater(r_slow.latency_penalty_bps, r_fast.latency_penalty_bps)

    def test_post_entry_drift(self):
        r = assess_trade_worthiness(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            post_entry_drift_bps_estimate=500,
        )
        self.assertGreater(r.post_entry_drift_bps, 0)

    def test_min_profit_gate(self):
        r = assess_trade_worthiness(
            entry_price=0.495,
            fair_price=0.50,
            size_usd=5.0,
            min_profit_usd=5.0,
        )
        self.assertFalse(r.worthy)

    def test_unknown_book_still_works(self):
        r = assess_trade_worthiness(
            entry_price=0.40,
            fair_price=0.55,
            size_usd=10.0,
        )
        self.assertTrue(r.worthy)
        self.assertAlmostEqual(r.survivability, 0.5)


if __name__ == "__main__":
    unittest.main()
