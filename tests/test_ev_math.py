"""Tests for Phase 2 EV math: slippage-adjusted EV, profit gates, time discount."""

from __future__ import annotations

import unittest

from bot.ev_math import compute_ev, resolution_time_gate


class TestComputeEV(unittest.TestCase):
    def test_positive_edge(self):
        r = compute_ev(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
        )
        self.assertTrue(r.passes)
        self.assertGreater(r.raw_ev, 0)
        self.assertGreater(r.absolute_expected_profit_usd, 0)

    def test_negative_edge_fails(self):
        r = compute_ev(
            entry_price=0.60,
            fair_price=0.50,
            size_usd=10.0,
        )
        self.assertFalse(r.passes)
        self.assertLess(r.raw_ev, 0)

    def test_slippage_reduces_ev(self):
        r_clean = compute_ev(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            slippage_bps=0,
        )
        r_slip = compute_ev(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            slippage_bps=200,
        )
        self.assertGreater(r_clean.slippage_adjusted_ev, r_slip.slippage_adjusted_ev)

    def test_min_ev_bps_gate(self):
        r = compute_ev(
            entry_price=0.495,
            fair_price=0.50,
            size_usd=10.0,
            min_ev_bps=200,
        )
        self.assertFalse(r.passes)
        self.assertIn("ev_", r.reason)

    def test_min_profit_gate(self):
        r = compute_ev(
            entry_price=0.49,
            fair_price=0.50,
            size_usd=5.0,
            min_profit_usd=5.0,
        )
        self.assertFalse(r.passes)
        self.assertIn("profit_", r.reason)

    def test_time_discount(self):
        r_short = compute_ev(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            hours_to_resolution=24.0,
            time_discount_rate=0.10,
        )
        r_long = compute_ev(
            entry_price=0.40,
            fair_price=0.50,
            size_usd=10.0,
            hours_to_resolution=8760.0,
            time_discount_rate=0.10,
        )
        self.assertGreater(
            r_short.absolute_expected_profit_usd,
            r_long.absolute_expected_profit_usd,
        )

    def test_extreme_prices_return_early(self):
        r = compute_ev(entry_price=0.0, fair_price=0.5, size_usd=10.0)
        self.assertFalse(r.passes)
        r2 = compute_ev(entry_price=0.5, fair_price=1.0, size_usd=10.0)
        self.assertFalse(r2.passes)

    def test_fees_subtracted(self):
        r_no_fee = compute_ev(
            entry_price=0.40, fair_price=0.50, size_usd=10.0, fee_bps=0,
        )
        r_fee = compute_ev(
            entry_price=0.40, fair_price=0.50, size_usd=10.0, fee_bps=100,
        )
        self.assertGreater(r_no_fee.slippage_adjusted_ev, r_fee.slippage_adjusted_ev)


class TestResolutionTimeGate(unittest.TestCase):
    def test_too_soon(self):
        ok, _, reason = resolution_time_gate(1.0, min_hours=2.0)
        self.assertFalse(ok)
        self.assertIn("resolves_in", reason)

    def test_too_late(self):
        ok, _, reason = resolution_time_gate(1000, max_hours=500)
        self.assertFalse(ok)

    def test_unknown_passes(self):
        ok, factor, _ = resolution_time_gate(None)
        self.assertTrue(ok)
        self.assertEqual(factor, 1.0)

    def test_normal_passes(self):
        ok, factor, _ = resolution_time_gate(48.0, min_hours=2.0)
        self.assertTrue(ok)

    def test_discount_factor(self):
        ok, factor, _ = resolution_time_gate(
            8760.0, min_hours=1.0, discount_rate=0.10,
        )
        self.assertTrue(ok)
        self.assertLess(factor, 1.0)


if __name__ == "__main__":
    unittest.main()
