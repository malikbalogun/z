"""Order book imbalance helper."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from bot.orderbook import best_bid_ask, orderbook_buy_depth_ok


class FakeBook:
    def __init__(self, bids, asks):
        self.bids = bids
        self.asks = asks


class FakeClob:
    def __init__(self, book):
        self._book = book

    def get_order_book(self, token_id):
        return self._book


class TestOrderbook(unittest.TestCase):
    def test_skew_fails(self):
        b = FakeBook(
            [SimpleNamespace(price="0.4", size="10")],
            [SimpleNamespace(price="0.6", size="100")],
        )
        self.assertFalse(orderbook_buy_depth_ok(FakeClob(b), "t", 0.45))

    def test_skew_passes(self):
        b = FakeBook(
            [SimpleNamespace(price="0.4", size="100")],
            [SimpleNamespace(price="0.6", size="10")],
        )
        self.assertTrue(orderbook_buy_depth_ok(FakeClob(b), "t", 0.45))

    def test_best_bid_ask(self):
        b = FakeBook(
            [
                SimpleNamespace(price="0.41", size="5"),
                SimpleNamespace(price="0.40", size="10"),
            ],
            [
                SimpleNamespace(price="0.59", size="1"),
                SimpleNamespace(price="0.60", size="2"),
            ],
        )
        bb, ba = best_bid_ask(FakeClob(b), "tid")
        self.assertAlmostEqual(bb, 0.41)
        self.assertAlmostEqual(ba, 0.59)


if __name__ == "__main__":
    unittest.main()
