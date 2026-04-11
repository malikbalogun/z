"""Tests for Phase 2 wallet scoring: category-aware, timing, consistency, decay."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from bot.wallet_scoring import (
    _category_skill_scores,
    _consistency_score,
    _exponential_decay_weight,
    _sample_size_penalty,
    _timing_quality_score,
    wallet_score_v2,
)
from bot.copy_rules import build_candidate, CopyCandidate


def _settings(**kw):
    base = dict(
        copy_wallet_score_overrides={},
        wallet_score_decay_half_life_hours=168.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _make_rows(n: int, **overrides):
    """Generate n synthetic activity rows."""
    rows = []
    for i in range(n):
        row = {
            "type": "TRADE",
            "side": "BUY",
            "token_id": f"tok{'x' * 38}",
            "question": f"Will event {i % 5} happen?",
            "price": 0.30 + (i % 10) * 0.05,
            "amount": 20 + i,
            "outcome": "Yes" if i % 3 != 0 else "No",
            "timestamp": str(time.time() - i * 3600),
        }
        row.update(overrides)
        rows.append(row)
    return rows


class TestExponentialDecay(unittest.TestCase):
    def test_zero_age(self):
        self.assertAlmostEqual(_exponential_decay_weight(0, 168), 1.0, places=4)

    def test_half_life(self):
        w = _exponential_decay_weight(168, 168)
        self.assertAlmostEqual(w, 0.5, places=2)

    def test_double_half_life(self):
        w = _exponential_decay_weight(336, 168)
        self.assertAlmostEqual(w, 0.25, places=2)

    def test_zero_half_life_returns_one(self):
        self.assertEqual(_exponential_decay_weight(100, 0), 1.0)


class TestSampleSizePenalty(unittest.TestCase):
    def test_zero_trades(self):
        self.assertEqual(_sample_size_penalty(0), 0.0)

    def test_large_sample(self):
        pen = _sample_size_penalty(100)
        self.assertGreater(pen, 0.9)

    def test_small_sample(self):
        pen = _sample_size_penalty(3)
        self.assertLess(pen, 0.5)

    def test_monotonic(self):
        vals = [_sample_size_penalty(n) for n in range(0, 30)]
        for i in range(1, len(vals)):
            self.assertGreaterEqual(vals[i], vals[i - 1])


class TestTimingQuality(unittest.TestCase):
    def test_sweet_spot_prices(self):
        cands = [
            CopyCandidate("w", "t", "k", "q", "", "", "other", "yes", 0.35, 10),
            CopyCandidate("w", "t", "k", "q", "", "", "other", "yes", 0.45, 10),
            CopyCandidate("w", "t", "k", "q", "", "", "other", "yes", 0.55, 10),
        ]
        score = _timing_quality_score(cands)
        self.assertGreater(score, 0.5)

    def test_extreme_prices_penalized(self):
        cands = [
            CopyCandidate("w", "t", "k", "q", "", "", "other", "yes", 0.95, 10),
            CopyCandidate("w", "t", "k", "q", "", "", "other", "yes", 0.97, 10),
        ]
        score = _timing_quality_score(cands)
        self.assertLess(score, 0.3)

    def test_empty(self):
        self.assertEqual(_timing_quality_score([]), 0.0)


class TestConsistency(unittest.TestCase):
    def test_diverse_categories(self):
        cands = [
            CopyCandidate("w", "t", "k", "Market A", "", "", "sports", "yes", 0.5, 10),
            CopyCandidate("w", "t", "k", "Market B", "", "", "politics", "yes", 0.5, 10),
            CopyCandidate("w", "t", "k", "Market C", "", "", "crypto_short", "yes", 0.5, 10),
            CopyCandidate("w", "t", "k", "Market D", "", "", "macro", "yes", 0.5, 10),
        ]
        score = _consistency_score(cands)
        self.assertGreater(score, 0.3)

    def test_single_trade(self):
        cands = [CopyCandidate("w", "t", "k", "q", "", "", "other", "yes", 0.5, 10)]
        self.assertEqual(_consistency_score(cands), 0.0)


class TestCategorySkill(unittest.TestCase):
    def test_single_category(self):
        cands = [
            CopyCandidate("w", "t", "k", "q", "", "", "sports", "yes", 0.50, 20),
            CopyCandidate("w", "t", "k", "q", "", "", "sports", "no", 0.60, 30),
        ]
        scores = _category_skill_scores(cands)
        self.assertIn("sports", scores)
        self.assertGreater(scores["sports"], 0.0)

    def test_multi_category(self):
        cands = [
            CopyCandidate("w", "t", "k", "q", "", "", "sports", "yes", 0.50, 20),
            CopyCandidate("w", "t", "k", "q", "", "", "politics", "yes", 0.40, 25),
        ]
        scores = _category_skill_scores(cands)
        self.assertEqual(len(scores), 2)


class TestWalletScoreV2(unittest.TestCase):
    def test_no_trades_returns_zero(self):
        score, result = wallet_score_v2(
            [], wallet="0xabc", default_bet_usd=5.0, settings=_settings(),
        )
        self.assertEqual(score, 0.0)

    def test_few_trades_below_threshold(self):
        rows = _make_rows(2)
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=_settings(),
        )
        self.assertEqual(score, 0.0)

    def test_sufficient_trades_positive(self):
        rows = _make_rows(20)
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=_settings(),
        )
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertIn("cat_skill", result.components)
        self.assertIn("timing", result.components)
        self.assertIn("consistency", result.components)
        self.assertIn("decay", result.components)

    def test_score_bounded(self):
        rows = _make_rows(50)
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=_settings(),
        )
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_override_additive(self):
        rows = _make_rows(20)
        s1 = _settings(copy_wallet_score_overrides={})
        s2 = _settings(copy_wallet_score_overrides={"0xabc": 0.1})
        score1, _ = wallet_score_v2(rows, wallet="0xabc", default_bet_usd=5.0, settings=s1)
        score2, _ = wallet_score_v2(rows, wallet="0xabc", default_bet_usd=5.0, settings=s2)
        self.assertGreater(score2, score1)

    def test_old_trades_decayed(self):
        now = time.time()
        recent_rows = _make_rows(15)
        for i, r in enumerate(recent_rows):
            r["timestamp"] = str(now - 3600 - i * 100)  # ~1h ago

        old_rows = _make_rows(15)
        for i, r in enumerate(old_rows):
            r["timestamp"] = str(now - 60 * 24 * 3600 - i * 100)  # 60 days ago

        score_recent, res_recent = wallet_score_v2(
            recent_rows, wallet="0xabc", default_bet_usd=5.0,
            settings=_settings(wallet_score_decay_half_life_hours=168.0), now_epoch=now,
        )
        score_old, res_old = wallet_score_v2(
            old_rows, wallet="0xabc", default_bet_usd=5.0,
            settings=_settings(wallet_score_decay_half_life_hours=168.0), now_epoch=now,
        )
        self.assertGreater(res_recent.decay_factor, res_old.decay_factor)
        self.assertGreater(score_recent, score_old)

    def test_decay_disabled(self):
        rows = _make_rows(15)
        s = _settings(wallet_score_decay_half_life_hours=0)
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=s,
        )
        self.assertAlmostEqual(result.decay_factor, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
