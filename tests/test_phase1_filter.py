"""Tests for Phase 1 trade-worthiness filter pipeline."""

from __future__ import annotations

import unittest

from bot.phase1.trade_filter import (
    FilterConfig,
    FilterResult,
    compute_trade_worthiness,
    filter_category,
    filter_copyability,
    filter_keywords,
    filter_liquidity,
    filter_outcome,
    filter_price_range,
    filter_size_usd,
    filter_wallet_score,
    run_filter_pipeline,
)


class TestIndividualFilters(unittest.TestCase):
    def test_wallet_score_pass(self):
        r = filter_wallet_score(0.5, 0.3)
        self.assertTrue(r.passed)

    def test_wallet_score_fail(self):
        r = filter_wallet_score(0.2, 0.5)
        self.assertFalse(r.passed)
        self.assertIn("wallet_score", r.reason)

    def test_wallet_score_zero_threshold(self):
        r = filter_wallet_score(0.0, 0.0)
        self.assertTrue(r.passed)

    def test_copyability_pass(self):
        r = filter_copyability(0.6, 0.4)
        self.assertTrue(r.passed)

    def test_copyability_fail(self):
        r = filter_copyability(0.2, 0.5)
        self.assertFalse(r.passed)

    def test_price_range_ok(self):
        r = filter_price_range(0.5, 0.02, 0.98)
        self.assertTrue(r.passed)

    def test_price_range_too_low(self):
        r = filter_price_range(0.01, 0.02, 0.98)
        self.assertFalse(r.passed)

    def test_price_range_too_high(self):
        r = filter_price_range(0.99, 0.02, 0.98)
        self.assertFalse(r.passed)

    def test_size_usd_ok(self):
        r = filter_size_usd(10.0, 1.0, 100.0)
        self.assertTrue(r.passed)

    def test_size_usd_too_small(self):
        r = filter_size_usd(0.5, 1.0, 100.0)
        self.assertFalse(r.passed)

    def test_size_usd_too_large(self):
        r = filter_size_usd(200.0, 1.0, 100.0)
        self.assertFalse(r.passed)

    def test_liquidity_ok(self):
        r = filter_liquidity(5000, 500)
        self.assertTrue(r.passed)

    def test_liquidity_fail(self):
        r = filter_liquidity(100, 500)
        self.assertFalse(r.passed)

    def test_category_allowed(self):
        r = filter_category("crypto_other", ["crypto_other", "politics"], None)
        self.assertTrue(r.passed)

    def test_category_not_allowed(self):
        r = filter_category("sports", ["crypto_other", "politics"], None)
        self.assertFalse(r.passed)

    def test_category_no_filter(self):
        r = filter_category("anything", None, None)
        self.assertTrue(r.passed)

    def test_outcome_allowed(self):
        r = filter_outcome("yes", ["yes", "no"])
        self.assertTrue(r.passed)

    def test_outcome_not_allowed(self):
        r = filter_outcome("unknown", ["yes", "no"])
        self.assertFalse(r.passed)

    def test_keywords_required_hit(self):
        r = filter_keywords("Will BTC reach 100k?", ["btc"], None)
        self.assertTrue(r.passed)

    def test_keywords_required_miss(self):
        r = filter_keywords("Will ETH reach 10k?", ["btc"], None)
        self.assertFalse(r.passed)

    def test_keywords_blocked_hit(self):
        r = filter_keywords("Trump election odds", None, ["trump"])
        self.assertFalse(r.passed)

    def test_keywords_no_filter(self):
        r = filter_keywords("anything here", None, None)
        self.assertTrue(r.passed)


class TestFilterPipeline(unittest.TestCase):
    def test_all_pass(self):
        config = FilterConfig(
            min_wallet_score=0.2,
            min_copyability_score=0.1,
            min_price=0.02,
            max_price=0.98,
            min_size_usd=1.0,
            max_size_usd=100.0,
            min_liquidity_usd=500,
        )
        passed, results = run_filter_pipeline(
            wallet_score=0.5,
            copyability_score=0.4,
            source_price=0.40,
            size_usd=10.0,
            market_liquidity=5000,
            category="crypto_other",
            outcome="yes",
            question_text="Will BTC reach 100k?",
            config=config,
        )
        self.assertTrue(passed)
        self.assertTrue(all(r.passed for r in results))

    def test_one_fails(self):
        config = FilterConfig(min_wallet_score=0.5)
        passed, results = run_filter_pipeline(
            wallet_score=0.2,
            copyability_score=0.4,
            source_price=0.40,
            size_usd=10.0,
            market_liquidity=5000,
            category="crypto_other",
            outcome="yes",
            question_text="test",
            config=config,
        )
        self.assertFalse(passed)
        failed = [r for r in results if not r.passed]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].filter_name, "wallet_score")


class TestTradeWorthiness(unittest.TestCase):
    def test_bounds(self):
        tw = compute_trade_worthiness(
            copyability_score=1.0,
            wallet_score=1.0,
            filter_pass_ratio=1.0,
        )
        self.assertAlmostEqual(tw, 1.0, places=3)

    def test_zero(self):
        tw = compute_trade_worthiness(
            copyability_score=0.0,
            wallet_score=0.0,
            filter_pass_ratio=0.0,
        )
        self.assertAlmostEqual(tw, 0.0, places=3)

    def test_clipped(self):
        tw = compute_trade_worthiness(
            copyability_score=2.0,
            wallet_score=2.0,
            filter_pass_ratio=2.0,
        )
        self.assertAlmostEqual(tw, 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
