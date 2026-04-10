"""Copy-trading candidate filters and preview behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from bot.copy_rules import build_candidate, passes_filters, wallet_score


def _settings(**kw):
    base = dict(
        copy_allowed_categories=[],
        copy_allowed_outcomes=[],
        copy_required_keywords=[],
        copy_blocked_keywords=[],
        copy_min_usd=0.0,
        copy_max_usd=0.0,
        copy_min_price=0.0,
        copy_max_price=1.0,
        copy_allow_unknown_outcome=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestCopyRules(unittest.TestCase):
    def test_build_candidate_buy_only(self):
        e = {"type": "TRADE", "side": "BUY", "token_id": "x" * 40, "question": "Will BTC rise?", "price": 0.44}
        c = build_candidate(e, "0xabc", 5.0)
        self.assertIsNotNone(c)
        e2 = dict(e)
        e2["side"] = "SELL"
        self.assertIsNone(build_candidate(e2, "0xabc", 5.0))

    def test_keyword_and_outcome_filter(self):
        e = {
            "type": "TRADE",
            "side": "BUY",
            "token_id": "x" * 40,
            "question": "Will BTC rise this week?",
            "price": 0.44,
            "outcome": "Yes",
        }
        c = build_candidate(e, "0xabc", 5.0)
        assert c is not None
        ok, _ = passes_filters(_settings(copy_required_keywords=["btc"], copy_allowed_outcomes=["yes"]), c)
        self.assertTrue(ok)
        ok2, reason2 = passes_filters(_settings(copy_required_keywords=["ethereum"]), c)
        self.assertFalse(ok2)
        self.assertIn("required", reason2)

    def test_usd_price_ranges(self):
        e = {
            "type": "TRADE",
            "side": "BUY",
            "token_id": "x" * 40,
            "question": "Will team A win?",
            "price": 0.82,
            "amount": 40,
            "outcome": "Yes",
        }
        c = build_candidate(e, "0xabc", 5.0)
        assert c is not None
        ok, _ = passes_filters(_settings(copy_max_usd=20), c)
        self.assertFalse(ok)
        ok2, _ = passes_filters(_settings(copy_max_price=0.8), c)
        self.assertFalse(ok2)

    def test_wallet_score_bounds(self):
        rows = [
            {"type": "TRADE", "side": "BUY", "token_id": "x" * 40, "question": "Will BTC rise?", "price": 0.44, "amount": 30, "outcome": "Yes"},
            {"type": "TRADE", "side": "BUY", "token_id": "y" * 40, "question": "Will ETH rise?", "price": 0.55, "amount": 15, "outcome": "No"},
        ]
        s = _settings(copy_wallet_score_overrides={})
        score, parts = wallet_score(rows, wallet="0xabc", default_bet_usd=5.0, settings=s)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertGreater(parts.get("n", 0), 0)


if __name__ == "__main__":
    unittest.main()
