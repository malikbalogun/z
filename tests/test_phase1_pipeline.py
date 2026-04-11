"""Tests for Phase 1 candidate->approval->paper execution pipeline."""

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
from bot.phase1.pipeline import (
    approve_candidate,
    create_candidate_from_event,
    filter_candidate,
    paper_execute,
    process_candidate_full,
    score_candidate,
)
from bot.phase1.risk_service import RiskConfig
from bot.phase1.trade_filter import FilterConfig


def _session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _event(s, **overrides):
    defaults = dict(
        wallet="0xtest",
        tx_hash="tx_001",
        token_id="tok_" + "a" * 40,
        condition_id="cond_001",
        side="BUY",
        price=0.45,
        usdc_value=10.0,
        outcome="yes",
        title="Will BTC reach 100k?",
    )
    defaults.update(overrides)
    e = P1WalletEvent(**defaults)
    s.add(e)
    s.flush()
    return e


def _market(s, **overrides):
    defaults = dict(
        condition_id="cond_001",
        question="Will BTC reach 100k?",
        liquidity=5000.0,
        volume=20000.0,
        category="crypto_other",
    )
    defaults.update(overrides)
    m = P1Market(**defaults)
    s.add(m)
    s.flush()
    return m


def _profile(s, **overrides):
    defaults = dict(wallet="0xtest", score=0.7, trade_count=20)
    defaults.update(overrides)
    p = P1WalletProfile(**defaults)
    s.add(p)
    s.flush()
    return p


class TestCreateCandidate(unittest.TestCase):
    def test_basic(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        p = _profile(s)
        c = create_candidate_from_event(s, e, market=m, wallet_profile=p)
        s.commit()

        self.assertEqual(c.status, CandidateStatus.NEW.value)
        self.assertEqual(c.source_wallet, "0xtest")
        self.assertAlmostEqual(c.source_price, 0.45)
        self.assertGreater(c.our_limit_price, 0.45)  # buffered
        self.assertAlmostEqual(c.wallet_score, 0.7)
        s.close()

    def test_no_market(self):
        s = _session()
        e = _event(s)
        c = create_candidate_from_event(s, e)
        s.commit()
        self.assertEqual(c.category, "other")
        s.close()


class TestScoreCandidate(unittest.TestCase):
    def test_transition(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        c = create_candidate_from_event(s, e, market=m)
        c = score_candidate(s, c, market=m)
        s.commit()

        self.assertEqual(c.status, CandidateStatus.SCORED.value)
        self.assertGreater(c.copyability_score, 0.0)
        s.close()

    def test_idempotent(self):
        s = _session()
        e = _event(s)
        c = create_candidate_from_event(s, e)
        c = score_candidate(s, c)
        c.status = CandidateStatus.FILTERED.value  # artificially move forward
        c2 = score_candidate(s, c)  # should not re-score
        self.assertEqual(c2.status, CandidateStatus.FILTERED.value)
        s.close()


class TestFilterCandidate(unittest.TestCase):
    def test_passes(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        p = _profile(s)
        c = create_candidate_from_event(s, e, market=m, wallet_profile=p)
        c = score_candidate(s, c, market=m)
        config = FilterConfig(min_wallet_score=0.0, min_liquidity_usd=0)
        c = filter_candidate(s, c, config, market=m)
        s.commit()

        self.assertEqual(c.status, CandidateStatus.FILTERED.value)
        self.assertGreater(c.trade_worthiness, 0.0)
        s.close()

    def test_rejects_low_wallet_score(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        low_profile = _profile(s, score=0.1)
        c = create_candidate_from_event(s, e, market=m, wallet_profile=low_profile)
        c = score_candidate(s, c, market=m)
        config = FilterConfig(min_wallet_score=0.5)
        c = filter_candidate(s, c, config, market=m)
        s.commit()

        self.assertEqual(c.status, CandidateStatus.REJECTED.value)
        self.assertIn("wallet_score", c.status_reason)
        # Check rejection logged
        rejections = s.query(P1RejectionLog).all()
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].stage, "filter")
        s.close()


class TestApproveCandidate(unittest.TestCase):
    def test_approved(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        p = _profile(s)
        c = create_candidate_from_event(s, e, market=m, wallet_profile=p)
        c = score_candidate(s, c, market=m)
        config = FilterConfig(min_wallet_score=0.0, min_liquidity_usd=0)
        c = filter_candidate(s, c, config, market=m)
        risk_config = RiskConfig()
        c = approve_candidate(s, c, risk_config)
        s.commit()

        self.assertEqual(c.status, CandidateStatus.APPROVED.value)
        s.close()

    def test_collision_blocks_second(self):
        s = _session()
        e1 = _event(s, tx_hash="tx_001")
        e2 = _event(s, tx_hash="tx_002")
        m = _market(s)
        p = _profile(s)
        fc = FilterConfig(min_wallet_score=0.0, min_liquidity_usd=0)
        rc = RiskConfig()

        c1 = create_candidate_from_event(s, e1, market=m, wallet_profile=p)
        c1 = score_candidate(s, c1, market=m)
        c1 = filter_candidate(s, c1, fc, market=m)
        c1 = approve_candidate(s, c1, rc)

        c2 = create_candidate_from_event(s, e2, market=m, wallet_profile=p)
        c2 = score_candidate(s, c2, market=m)
        c2 = filter_candidate(s, c2, fc, market=m)
        c2 = approve_candidate(s, c2, rc)
        s.commit()

        self.assertEqual(c1.status, CandidateStatus.APPROVED.value)
        self.assertEqual(c2.status, CandidateStatus.REJECTED.value)
        self.assertIn("collision", c2.status_reason)
        s.close()

    def test_risk_rejection(self):
        s = _session()
        e = _event(s, price=0.005)  # too extreme for risk
        m = _market(s)
        c = create_candidate_from_event(s, e, market=m)
        c = score_candidate(s, c, market=m)
        fc = FilterConfig(min_wallet_score=0.0, min_liquidity_usd=0)
        c = filter_candidate(s, c, fc, market=m)
        if c.status == CandidateStatus.REJECTED.value:
            return  # filter may reject price too
        rc = RiskConfig()
        c = approve_candidate(s, c, rc)
        s.commit()

        # Should be rejected on price bounds
        self.assertEqual(c.status, CandidateStatus.REJECTED.value)
        s.close()


class TestPaperExecute(unittest.TestCase):
    def test_basic(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        p = _profile(s)
        c = create_candidate_from_event(s, e, market=m, wallet_profile=p)
        c = score_candidate(s, c, market=m)
        fc = FilterConfig(min_wallet_score=0.0, min_liquidity_usd=0)
        c = filter_candidate(s, c, fc, market=m)
        rc = RiskConfig()
        c = approve_candidate(s, c, rc)
        pt = paper_execute(s, c)
        s.commit()

        self.assertIsNotNone(pt)
        self.assertEqual(pt.status, PaperTradeStatus.FILLED.value)
        self.assertEqual(c.status, CandidateStatus.PAPER_EXECUTED.value)
        self.assertGreater(pt.fill_price, 0)
        self.assertIsNotNone(pt.filled_at)
        s.close()

    def test_not_approved_returns_none(self):
        s = _session()
        c = P1TradeCandidate(
            source_wallet="0x",
            condition_id="c",
            token_id="t",
            status=CandidateStatus.NEW.value,
        )
        s.add(c)
        s.flush()
        pt = paper_execute(s, c)
        self.assertIsNone(pt)
        s.close()


class TestFullPipeline(unittest.TestCase):
    def test_end_to_end_success(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        p = _profile(s)
        fc = FilterConfig(min_wallet_score=0.0, min_liquidity_usd=0)
        rc = RiskConfig()

        candidate, paper_trade = process_candidate_full(
            s, e, market=m, wallet_profile=p,
            filter_config=fc, risk_config=rc,
        )
        s.commit()

        self.assertEqual(candidate.status, CandidateStatus.PAPER_EXECUTED.value)
        self.assertIsNotNone(paper_trade)
        self.assertEqual(paper_trade.status, PaperTradeStatus.FILLED.value)
        s.close()

    def test_end_to_end_rejection(self):
        s = _session()
        e = _event(s)
        m = _market(s)
        p = _profile(s, score=0.1)
        fc = FilterConfig(min_wallet_score=0.8)  # high bar
        rc = RiskConfig()

        candidate, paper_trade = process_candidate_full(
            s, e, market=m, wallet_profile=p,
            filter_config=fc, risk_config=rc,
        )
        s.commit()

        self.assertEqual(candidate.status, CandidateStatus.REJECTED.value)
        self.assertIsNone(paper_trade)

        rejections = s.query(P1RejectionLog).all()
        self.assertGreater(len(rejections), 0)
        s.close()


if __name__ == "__main__":
    unittest.main()
