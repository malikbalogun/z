"""Offline smoke tests for helpers (no network, no keys)."""

from __future__ import annotations

import unittest

from bot import risk
from bot.categories import MarketCategory
from bot.clob_utils import (
    is_filled_status,
    is_open_status,
    is_terminal_status,
    normalize_order_payload,
    parse_midpoint,
)
from bot.models import TradeIntent
from bot.settings import Settings
from bot.validate import is_valid_polygon_address, is_valid_private_key_hex


def _intent(**kw) -> TradeIntent:
    base = dict(
        agent="t",
        priority=1,
        token_id="1" * 42,
        condition_id="c",
        question="q",
        outcome="Yes",
        side="BUY",
        max_price=0.5,
        size_usd=5.0,
        category=MarketCategory.OTHER,
        strategy="s",
        reason="r",
        reference_price=None,
        bundle_id=None,
    )
    base.update(kw)
    return TradeIntent(**base)


class TestValidate(unittest.TestCase):
    def test_address(self):
        self.assertTrue(is_valid_polygon_address("0x" + "a" * 40))
        self.assertFalse(is_valid_polygon_address("0x123"))
        self.assertFalse(is_valid_polygon_address(""))

    def test_key(self):
        self.assertTrue(is_valid_private_key_hex("0x" + "b" * 64))
        self.assertTrue(is_valid_private_key_hex("c" * 64))
        self.assertFalse(is_valid_private_key_hex(""))
        self.assertFalse(is_valid_private_key_hex("0x****"))


class TestClobUtils(unittest.TestCase):
    def test_parse_midpoint(self):
        self.assertAlmostEqual(parse_midpoint({"mid": "0.42"}), 0.42)
        self.assertAlmostEqual(parse_midpoint(0.33), 0.33)
        self.assertIsNone(parse_midpoint(None))
        self.assertIsNone(parse_midpoint("bad"))

    def test_normalize_order(self):
        d = normalize_order_payload(
            {
                "order": {
                    "status": "live",
                    "size_matched": "1",
                    "original_size": "10",
                    "orderID": "abc",
                }
            }
        )
        self.assertEqual(d["status"], "LIVE")
        self.assertEqual(d["size_matched"], 1.0)
        self.assertEqual(d["original_size"], 10.0)
        self.assertEqual(d["order_id"], "abc")

    def test_status_flags(self):
        self.assertTrue(is_open_status("LIVE"))
        self.assertTrue(is_filled_status("FILLED", 1.0, 1.0))
        self.assertTrue(is_filled_status("PARTIAL", 10.0, 10.0))
        self.assertTrue(is_terminal_status("CANCELED"))


class TestMinEdge(unittest.TestCase):
    def test_edge_gate_blocks_overpay(self):
        s = Settings()
        s.min_edge_bps = 50
        ok, reason = risk.gate_intent(_intent(max_price=0.52, reference_price=0.50), s, None)
        self.assertFalse(ok)
        self.assertIn("edge", reason)

    def test_edge_gate_allows_cheap_vs_mid(self):
        s = Settings()
        s.min_edge_bps = 50
        ok, _ = risk.gate_intent(_intent(max_price=0.48, reference_price=0.50), s, None)
        self.assertTrue(ok)


class TestRisk(unittest.TestCase):
    def test_token_id(self):
        s = Settings()
        ok, _ = risk.gate_intent(_intent(token_id="short"), s, None)
        self.assertFalse(ok)

    def test_cex_require_dispersion(self):
        s = Settings()
        s.cex_gate_crypto = True
        s.cex_require_dispersion = True
        ok, reason = risk.gate_intent(
            _intent(category=MarketCategory.CRYPTO_SHORT),
            s,
            None,
        )
        self.assertFalse(ok)
        self.assertIn("cex_no", reason)

    def test_cex_dispersion_ok(self):
        s = Settings()
        s.cex_gate_crypto = True
        s.max_cex_dispersion_bps = 30.0
        ok, _ = risk.gate_intent(
            _intent(category=MarketCategory.CRYPTO_SHORT),
            s,
            10.0,
        )
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
