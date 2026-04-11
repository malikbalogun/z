"""Tests for Phase 1 visibility and rejection log utilities."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base
from bot.phase1.models import (
    CandidateStatus,
    P1Market,
    P1PaperTrade,
    P1RejectionLog,
    P1TradeCandidate,
    P1WalletEvent,
    P1WalletProfile,
    PaperTradeStatus,
)
from bot.phase1.rejection_log import (
    get_recent_rejections,
    log_rejection,
    rejection_summary,
)
from bot.phase1.visibility import (
    market_stats,
    pipeline_summary,
    recent_candidates,
    recent_paper_trades,
    wallet_profiles_summary,
)


def _session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed(s):
    """Add some sample data."""
    s.add(P1Market(condition_id="c1", question="Q1", liquidity=5000, category="crypto_other", active=True))
    s.add(P1Market(condition_id="c2", question="Q2", liquidity=1000, category="politics", active=True))
    s.add(P1WalletProfile(wallet="0xw1", score=0.7, trade_count=20))
    s.add(P1WalletProfile(wallet="0xw2", score=0.4, trade_count=5))
    s.add(P1WalletEvent(wallet="0xw1", tx_hash="t1", token_id="tok1"))
    s.add(P1WalletEvent(wallet="0xw1", tx_hash="t2", token_id="tok2"))
    s.add(P1TradeCandidate(
        source_wallet="0xw1", condition_id="c1", token_id="tok1",
        status=CandidateStatus.PAPER_EXECUTED.value, size_usd=10.0,
    ))
    s.add(P1TradeCandidate(
        source_wallet="0xw2", condition_id="c2", token_id="tok2",
        status=CandidateStatus.REJECTED.value, size_usd=5.0, status_reason="test",
    ))
    s.add(P1PaperTrade(
        candidate_id=1, condition_id="c1", token_id="tok1",
        limit_price=0.5, fill_price=0.52, size_usd=10.0,
        status=PaperTradeStatus.FILLED.value,
    ))
    s.commit()


class TestPipelineSummary(unittest.TestCase):
    def test_empty(self):
        s = _session()
        summary = pipeline_summary(s)
        self.assertIn("candidates", summary)
        self.assertIn("paper_trades", summary)
        self.assertEqual(summary["markets"]["total"], 0)
        s.close()

    def test_with_data(self):
        s = _session()
        _seed(s)
        summary = pipeline_summary(s)
        self.assertEqual(summary["markets"]["total"], 2)
        self.assertEqual(summary["markets"]["active"], 2)
        self.assertEqual(summary["wallets_tracked"], 2)
        self.assertEqual(summary["wallet_events"], 2)
        self.assertEqual(summary["candidates"]["paper_executed"], 1)
        self.assertEqual(summary["candidates"]["rejected"], 1)
        self.assertEqual(summary["paper_trades"]["filled"], 1)
        s.close()


class TestRecentQueries(unittest.TestCase):
    def test_candidates(self):
        s = _session()
        _seed(s)
        rows = recent_candidates(s, limit=10)
        self.assertEqual(len(rows), 2)
        self.assertIn("status", rows[0])
        s.close()

    def test_candidates_filtered(self):
        s = _session()
        _seed(s)
        rows = recent_candidates(s, status="rejected")
        self.assertEqual(len(rows), 1)
        s.close()

    def test_paper_trades(self):
        s = _session()
        _seed(s)
        rows = recent_paper_trades(s, limit=10)
        self.assertEqual(len(rows), 1)
        s.close()

    def test_wallet_profiles(self):
        s = _session()
        _seed(s)
        rows = wallet_profiles_summary(s)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["wallet"], "0xw1")  # higher score first
        s.close()

    def test_market_stats(self):
        s = _session()
        _seed(s)
        rows = market_stats(s, top_n=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["liquidity"], 5000)  # sorted by liquidity
        s.close()


class TestRejectionLog(unittest.TestCase):
    def test_log_and_query(self):
        s = _session()
        log_rejection(s, wallet="0xtest", stage="filter", reason="price_too_high")
        log_rejection(s, wallet="0xtest", stage="risk", reason="exposure_exceeded")
        log_rejection(s, wallet="0xother", stage="filter", reason="wallet_score_low")
        s.commit()

        all_rej = get_recent_rejections(s, limit=10)
        self.assertEqual(len(all_rej), 3)

        filter_rej = get_recent_rejections(s, stage="filter")
        self.assertEqual(len(filter_rej), 2)

        wallet_rej = get_recent_rejections(s, wallet="0xtest")
        self.assertEqual(len(wallet_rej), 2)
        s.close()

    def test_summary(self):
        s = _session()
        log_rejection(s, stage="filter", reason="price_too_high", wallet="0xa")
        log_rejection(s, stage="filter", reason="price_too_high", wallet="0xa")
        log_rejection(s, stage="risk", reason="exposure", wallet="0xb")
        s.commit()

        summary = rejection_summary(s, hours=24.0)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_stage"]["filter"], 2)
        self.assertEqual(summary["by_stage"]["risk"], 1)
        self.assertIn("price_too_high", summary["by_reason"])
        s.close()


if __name__ == "__main__":
    unittest.main()
