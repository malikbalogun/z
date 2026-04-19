"""Tests for the paper portfolio outcome matching and basic accounting."""

from __future__ import annotations

import json
import unittest

from bot.paper_portfolio import PaperPortfolio, _best_price_for_outcome


class TestBestPriceForOutcome(unittest.TestCase):
    def _market(self, **overrides) -> dict:
        m = {
            "clobTokenIds": ["tok_yes", "tok_no"],
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.61", "0.39"],
        }
        m.update(overrides)
        return m

    def test_token_id_match_takes_precedence(self):
        m = self._market()
        # Even when outcome name disagrees, the explicit token_id wins.
        self.assertAlmostEqual(_best_price_for_outcome(m, "tok_no", "Yes"), 0.39)
        self.assertAlmostEqual(_best_price_for_outcome(m, "tok_yes", "No"), 0.61)

    def test_outcome_name_fallback_case_insensitive(self):
        m = self._market(clobTokenIds=[])
        self.assertAlmostEqual(_best_price_for_outcome(m, "", "yes"), 0.61)
        self.assertAlmostEqual(_best_price_for_outcome(m, "", "NO"), 0.39)

    def test_handles_json_encoded_string_arrays(self):
        m = {
            "clobTokenIds": json.dumps(["a", "b"]),
            "outcomes": json.dumps(["Over", "Under"]),
            "outcomePrices": json.dumps(["0.21", "0.79"]),
        }
        self.assertAlmostEqual(_best_price_for_outcome(m, "b", "Over"), 0.79)
        self.assertAlmostEqual(_best_price_for_outcome(m, "", "Under"), 0.79)

    def test_returns_none_when_no_match(self):
        m = self._market()
        self.assertIsNone(_best_price_for_outcome(m, "unknown", "Maybe"))
        self.assertIsNone(_best_price_for_outcome({}, "tok", "Yes"))


class TestPaperPortfolioAccounting(unittest.TestCase):
    def test_buy_only_and_averaging(self):
        pp = PaperPortfolio()
        pp.record_fill(
            token_id="TOK1", condition_id="cid1", market="m1", outcome="Yes",
            side="BUY", price=0.50, shares=10.0, cost_usd=5.00,
            timestamp="2024-01-01T00:00:00Z",
        )
        pp.record_fill(
            token_id="TOK1", condition_id="cid1", market="m1", outcome="Yes",
            side="BUY", price=0.60, shares=10.0, cost_usd=6.00,
            timestamp="2024-01-01T01:00:00Z",
        )
        pos = pp.get_positions()
        self.assertEqual(len(pos), 1)
        p = pos[0]
        self.assertEqual(p["size"], 20.0)
        self.assertAlmostEqual(p["avg_price"], 0.55, places=4)
        self.assertEqual(p["trades"], 2)

    def test_sell_ignored(self):
        pp = PaperPortfolio()
        pp.record_fill(
            token_id="TOK1", condition_id="cid1", market="m1", outcome="Yes",
            side="SELL", price=0.50, shares=10.0, cost_usd=5.00,
            timestamp="2024-01-01T00:00:00Z",
        )
        self.assertEqual(pp.get_positions(), [])


if __name__ == "__main__":
    unittest.main()
