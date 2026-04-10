"""Offline tests for reconcile helpers."""

from __future__ import annotations

import unittest

from bot.reconcile import (
    canonical_status_from_order_payload,
    merge_trade_status,
    normalize_open_order,
)


class TestNormalizeOpenOrder(unittest.TestCase):
    def test_dict(self):
        d = normalize_open_order(
            {
                "id": "0xabc",
                "asset_id": "tok123",
                "side": "BUY",
                "price": "0.55",
                "original_size": "10",
                "size_matched": "2",
                "status": "LIVE",
            }
        )
        self.assertEqual(d["order_id"], "0xabc")
        self.assertEqual(d["token_id"], "tok123")
        self.assertEqual(d["side"], "BUY")
        self.assertAlmostEqual(d["price"], 0.55)
        self.assertEqual(d["status"], "LIVE")

    def test_non_dict(self):
        self.assertIsNone(normalize_open_order("x")["order_id"])


class TestCanonicalStatus(unittest.TestCase):
    def test_filled(self):
        st = canonical_status_from_order_payload(
            {"status": "MATCHED", "size_matched": "10", "original_size": "10"}
        )
        self.assertEqual(st, "filled")

    def test_open(self):
        st = canonical_status_from_order_payload({"status": "LIVE"})
        self.assertEqual(st, "open")


class TestMergeStatus(unittest.TestCase):
    def test_upgrade_to_filled(self):
        self.assertEqual(merge_trade_status("submitted", "filled"), "filled")

    def test_no_downgrade(self):
        self.assertIsNone(merge_trade_status("filled", "cancelled"))

    def test_open_from_submitted(self):
        self.assertEqual(merge_trade_status("submitted", "open"), "open")


if __name__ == "__main__":
    unittest.main()
