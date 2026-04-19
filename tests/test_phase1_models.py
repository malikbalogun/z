"""Tests for Phase 1 SQLAlchemy models — schema creation and basic CRUD."""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base
from bot.phase1.models import (
    CandidateStatus,
    P1CollisionLock,
    P1Market,
    P1MarketClassification,
    P1PaperTrade,
    P1RejectionLog,
    P1TradeCandidate,
    P1WalletEvent,
    P1WalletProfile,
    PaperTradeStatus,
)


def _session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class TestP1Schema(unittest.TestCase):
    def test_tables_created(self):
        s = _session()
        self.assertEqual(s.query(P1Market).count(), 0)
        self.assertEqual(s.query(P1WalletEvent).count(), 0)
        self.assertEqual(s.query(P1WalletProfile).count(), 0)
        self.assertEqual(s.query(P1TradeCandidate).count(), 0)
        self.assertEqual(s.query(P1PaperTrade).count(), 0)
        self.assertEqual(s.query(P1RejectionLog).count(), 0)
        self.assertEqual(s.query(P1CollisionLock).count(), 0)
        self.assertEqual(s.query(P1MarketClassification).count(), 0)
        s.close()

    def test_market_crud(self):
        s = _session()
        m = P1Market(
            condition_id="cond_abc",
            question="Will BTC reach 100k?",
            liquidity=5000.0,
            volume=10000.0,
            category="crypto_other",
        )
        s.add(m)
        s.commit()
        self.assertEqual(s.query(P1Market).count(), 1)
        fetched = s.query(P1Market).filter(P1Market.condition_id == "cond_abc").first()
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.question, "Will BTC reach 100k?")
        s.close()

    def test_wallet_event_dedup(self):
        s = _session()
        e1 = P1WalletEvent(wallet="0xabc", tx_hash="tx1", token_id="tok1", side="BUY")
        s.add(e1)
        s.commit()
        # Duplicate should raise due to unique constraint
        e2 = P1WalletEvent(wallet="0xabc", tx_hash="tx1", token_id="tok1", side="BUY")
        s.add(e2)
        with self.assertRaises(Exception):
            s.commit()
        s.close()

    def test_candidate_status_enum(self):
        self.assertEqual(CandidateStatus.NEW.value, "new")
        self.assertEqual(CandidateStatus.PAPER_EXECUTED.value, "paper_executed")

    def test_paper_trade_status(self):
        self.assertEqual(PaperTradeStatus.OPEN.value, "open")
        self.assertEqual(PaperTradeStatus.FILLED.value, "filled")

    def test_rejection_log(self):
        s = _session()
        r = P1RejectionLog(
            wallet="0xdef",
            condition_id="cond_xyz",
            stage="filter",
            reason="price_out_of_range",
        )
        s.add(r)
        s.commit()
        self.assertEqual(s.query(P1RejectionLog).count(), 1)
        fetched = s.query(P1RejectionLog).first()
        self.assertEqual(fetched.stage, "filter")
        s.close()


if __name__ == "__main__":
    unittest.main()
