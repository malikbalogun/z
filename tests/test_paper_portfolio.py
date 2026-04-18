"""Tests for the paper portfolio outcome index mapping and basic accounting."""

from __future__ import annotations

import unittest

from bot.paper_portfolio import PaperPortfolio, _match_outcome_index


class TestMatchOutcomeIndex(unittest.TestCase):
    def test_exact_case_insensitive(self):
        self.assertEqual(_match_outcome_index("Yes", ["Yes", "No"]), 0)
        self.assertEqual(_match_outcome_index("NO", ["Yes", "No"]), 1)
        self.assertEqual(_match_outcome_index("over", ["Over", "Under"]), 0)
        self.assertEqual(_match_outcome_index("UNDER", ["Over", "Under"]), 1)

    def test_yes_no_aliases(self):
        self.assertEqual(_match_outcome_index("true", ["Yes", "No"]), 0)
        self.assertEqual(_match_outcome_index("false", ["Yes", "No"]), 1)
        self.assertEqual(_match_outcome_index("1", ["Yes", "No"]), 0)
        # Previously buggy: "0" used to get mapped to YES.
        self.assertEqual(_match_outcome_index("0", ["Yes", "No"]), 1)

    def test_numeric_index_fallback(self):
        self.assertEqual(_match_outcome_index("0", ["Over", "Under"]), 0)
        self.assertEqual(_match_outcome_index("1", ["Over", "Under"]), 1)

    def test_no_match(self):
        self.assertIsNone(_match_outcome_index("Maybe", ["Yes", "No"]))
        self.assertIsNone(_match_outcome_index("", ["Yes", "No"]))
        self.assertIsNone(_match_outcome_index("Yes", []))


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
        # avg_price = (5.00 + 6.00) / 20 = 0.55
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
