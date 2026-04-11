"""Tests for Phase 2 paper execution realism."""

from __future__ import annotations

import unittest

from bot.paper_realism import (
    estimate_follower_fill_probability,
    estimate_slippage_bps,
    orderbook_survivability_score,
    simulate_paper_fill,
)


class TestFollowerFillProbability(unittest.TestCase):
    def test_generous_buffer(self):
        prob = estimate_follower_fill_probability(
            limit_price=0.55, observed_price=0.50,
            latency_ms=200, size_usd=5,
        )
        self.assertGreater(prob, 0.3)

    def test_no_buffer(self):
        prob = estimate_follower_fill_probability(
            limit_price=0.50, observed_price=0.50,
            latency_ms=500, size_usd=5,
        )
        self.assertLess(prob, 0.5)

    def test_high_latency_reduces_prob(self):
        prob_fast = estimate_follower_fill_probability(
            limit_price=0.52, observed_price=0.50,
            latency_ms=100, size_usd=5,
        )
        prob_slow = estimate_follower_fill_probability(
            limit_price=0.52, observed_price=0.50,
            latency_ms=3000, size_usd=5,
        )
        self.assertGreater(prob_fast, prob_slow)

    def test_large_size_reduces_prob(self):
        prob_small = estimate_follower_fill_probability(
            limit_price=0.52, observed_price=0.50,
            latency_ms=500, size_usd=5,
        )
        prob_large = estimate_follower_fill_probability(
            limit_price=0.52, observed_price=0.50,
            latency_ms=500, size_usd=200,
        )
        self.assertGreater(prob_small, prob_large)

    def test_bounded(self):
        prob = estimate_follower_fill_probability(
            limit_price=0.99, observed_price=0.01,
            latency_ms=1, size_usd=1,
        )
        self.assertLessEqual(prob, 1.0)
        self.assertGreaterEqual(prob, 0.0)

    def test_invalid_inputs(self):
        prob = estimate_follower_fill_probability(
            limit_price=0, observed_price=0.5,
            latency_ms=500, size_usd=5,
        )
        self.assertEqual(prob, 0.0)


class TestSimulatePaperFill(unittest.TestCase):
    def test_deterministic_with_seed(self):
        r1 = simulate_paper_fill(
            limit_price=0.55, observed_price=0.50,
            size_usd=10, seed=42,
        )
        r2 = simulate_paper_fill(
            limit_price=0.55, observed_price=0.50,
            size_usd=10, seed=42,
        )
        self.assertEqual(r1.filled, r2.filled)
        self.assertEqual(r1.fill_price, r2.fill_price)

    def test_filled_has_price(self):
        for seed in range(100):
            r = simulate_paper_fill(
                limit_price=0.70, observed_price=0.50,
                size_usd=5, seed=seed, latency_ms=100,
            )
            if r.filled:
                self.assertGreater(r.fill_price, 0)
                self.assertLessEqual(r.fill_price, 0.70)
                break
        else:
            self.fail("No fill in 100 seeds with generous buffer")

    def test_slippage_present(self):
        fills = []
        for seed in range(200):
            r = simulate_paper_fill(
                limit_price=0.60, observed_price=0.50,
                size_usd=5, seed=seed,
            )
            if r.filled:
                fills.append(r)
        if fills:
            avg_slip = sum(f.slippage_bps for f in fills) / len(fills)
            self.assertGreater(avg_slip, 0)


class TestEstimateSlippage(unittest.TestCase):
    def test_base_slippage(self):
        slip = estimate_slippage_bps(size_usd=5.0)
        self.assertGreater(slip, 0)

    def test_larger_size_more_slip(self):
        s1 = estimate_slippage_bps(size_usd=5.0)
        s2 = estimate_slippage_bps(size_usd=100.0)
        self.assertGreater(s2, s1)

    def test_spread_increases_slip(self):
        s1 = estimate_slippage_bps(size_usd=10, spread_bps=100)
        s2 = estimate_slippage_bps(size_usd=10, spread_bps=500)
        self.assertGreater(s2, s1)


class TestOrderbookSurvivability(unittest.TestCase):
    def test_empty_book(self):
        score, reason = orderbook_survivability_score(
            bid_notional=0, ask_notional=0, our_size_usd=5,
        )
        self.assertEqual(score, 0.0)

    def test_deep_book(self):
        score, reason = orderbook_survivability_score(
            bid_notional=10000, ask_notional=10000, our_size_usd=5,
        )
        self.assertGreater(score, 0.7)

    def test_thin_book_penalized(self):
        score, reason = orderbook_survivability_score(
            bid_notional=5, ask_notional=5, our_size_usd=10,
        )
        self.assertLess(score, 0.7)

    def test_bounded(self):
        score, _ = orderbook_survivability_score(
            bid_notional=100, ask_notional=100, our_size_usd=5,
        )
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()
