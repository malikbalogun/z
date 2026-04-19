"""Tests for Phase 1 market and wallet event ingestion."""

from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base
from bot.phase1.market_ingest import (
    classify_and_persist,
    get_active_markets,
    get_market,
    ingest_markets_batch,
    upsert_market_from_gamma,
)
from bot.phase1.models import P1Market, P1MarketClassification, P1WalletEvent
from bot.phase1.wallet_ingest import (
    build_wallet_event,
    get_wallet_events,
    ingest_wallet_event,
    ingest_wallet_events_batch,
)


def _session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _gamma_market(**overrides):
    m = {
        "condition_id": "cond_001",
        "question": "Will BTC reach 100k by end of year?",
        "slug": "btc-100k",
        "clobTokenIds": json.dumps(["tok_yes", "tok_no"]),
        "outcomes": '["Yes","No"]',
        "outcomePrices": "[0.35,0.65]",
        "liquidityClob": 5000,
        "volume": 20000,
        "active": True,
        "enableOrderBook": True,
    }
    m.update(overrides)
    return m


class TestMarketIngest(unittest.TestCase):
    def test_upsert_new(self):
        s = _session()
        raw = _gamma_market()
        m = upsert_market_from_gamma(s, raw)
        s.commit()
        self.assertIsNotNone(m)
        self.assertEqual(m.condition_id, "cond_001")
        self.assertEqual(m.liquidity, 5000)
        s.close()

    def test_upsert_update(self):
        s = _session()
        upsert_market_from_gamma(s, _gamma_market())
        s.commit()
        upsert_market_from_gamma(s, _gamma_market(liquidityClob=9000))
        s.commit()
        self.assertEqual(s.query(P1Market).count(), 1)
        m = get_market(s, "cond_001")
        self.assertEqual(m.liquidity, 9000)
        s.close()

    def test_batch_ingest(self):
        s = _session()
        markets = [
            _gamma_market(condition_id="c1"),
            _gamma_market(condition_id="c2"),
            _gamma_market(condition_id=""),  # invalid, skip
        ]
        count = ingest_markets_batch(s, markets)
        s.commit()
        self.assertEqual(count, 2)
        self.assertEqual(s.query(P1Market).count(), 2)
        s.close()

    def test_skip_no_tokens(self):
        s = _session()
        raw = _gamma_market(clobTokenIds="[]")
        m = upsert_market_from_gamma(s, raw)
        self.assertIsNone(m)
        s.close()

    def test_classify_and_persist(self):
        s = _session()
        raw = _gamma_market(question="Will Bitcoin reach 100k?")
        cat = classify_and_persist(s, "cond_001", raw)
        s.commit()
        self.assertIn(cat, ("crypto_other", "crypto_short"))
        self.assertEqual(s.query(P1MarketClassification).count(), 1)
        s.close()

    def test_get_active_markets(self):
        s = _session()
        ingest_markets_batch(s, [
            _gamma_market(condition_id="c1", liquidityClob=1000),
            _gamma_market(condition_id="c2", liquidityClob=5000),
        ])
        s.commit()
        active = get_active_markets(s, min_liquidity=2000)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].condition_id, "c2")
        s.close()


def _wallet_entry(**overrides):
    e = {
        "type": "TRADE",
        "side": "BUY",
        "token_id": "tok_" + "a" * 40,
        "transactionHash": "txhash_001",
        "price": 0.45,
        "size": 10.0,
        "usdcSize": 4.5,
        "outcome": "Yes",
        "title": "Will ETH merge succeed?",
        "conditionId": "cond_eth",
    }
    e.update(overrides)
    return e


class TestWalletIngest(unittest.TestCase):
    def test_build_event(self):
        fields = build_wallet_event(_wallet_entry(), "0xabc123")
        self.assertIsNotNone(fields)
        self.assertEqual(fields["wallet"], "0xabc123")
        self.assertEqual(fields["side"], "BUY")
        self.assertAlmostEqual(fields["price"], 0.45)

    def test_build_event_missing_token(self):
        entry = _wallet_entry(token_id="")
        fields = build_wallet_event(entry, "0xabc")
        self.assertIsNone(fields)

    def test_ingest_dedup(self):
        s = _session()
        e = _wallet_entry()
        r1 = ingest_wallet_event(s, e, "0xwallet1")
        s.commit()
        self.assertIsNotNone(r1)
        r2 = ingest_wallet_event(s, e, "0xwallet1")
        self.assertIsNone(r2)  # dedup
        s.close()

    def test_batch_ingest(self):
        s = _session()
        entries = [
            _wallet_entry(transactionHash="tx1"),
            _wallet_entry(transactionHash="tx2"),
            _wallet_entry(transactionHash="tx1"),  # duplicate
        ]
        count = ingest_wallet_events_batch(s, entries, "0xwallet")
        s.commit()
        self.assertEqual(count, 2)
        s.close()

    def test_get_events_filter(self):
        s = _session()
        ingest_wallet_events_batch(s, [
            _wallet_entry(transactionHash="tx1", side="BUY"),
            _wallet_entry(transactionHash="tx2", side="SELL"),
        ], "0xwallet")
        s.commit()
        buys = get_wallet_events(s, "0xwallet", side="BUY")
        self.assertEqual(len(buys), 1)
        s.close()


if __name__ == "__main__":
    unittest.main()
