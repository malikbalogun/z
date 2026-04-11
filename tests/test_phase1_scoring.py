"""Tests for Phase 1 wallet scoring and copyability."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base
from bot.phase1.copyability import CopyabilityInput, CopyabilityResult, compute_copyability
from bot.phase1.models import P1WalletEvent, P1WalletProfile
from bot.phase1.wallet_scoring import (
    compute_wallet_score,
    get_wallet_profile,
    score_and_persist_wallet,
)


def _session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _make_events(session, wallet="0xtest", count=10, price=0.45, outcome="yes"):
    for i in range(count):
        session.add(P1WalletEvent(
            wallet=wallet,
            tx_hash=f"tx_{i}",
            token_id=f"tok_{'a' * 40}_{i}",
            side="BUY",
            price=price,
            usdc_value=25.0,
            outcome=outcome,
        ))
    session.commit()


class TestWalletScoring(unittest.TestCase):
    def test_empty_events(self):
        score, details = compute_wallet_score([])
        self.assertEqual(score, 0.0)
        self.assertEqual(details["n"], 0)

    def test_compute_from_events(self):
        s = _session()
        _make_events(s, count=20)
        events = list(s.query(P1WalletEvent).all())
        score, details = compute_wallet_score(events)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertEqual(details["n"], 20.0)
        self.assertAlmostEqual(details["outcome"], 1.0)  # all "yes"
        self.assertAlmostEqual(details["price"], 1.0)  # 0.45 is sane
        s.close()

    def test_score_bounds(self):
        s = _session()
        _make_events(s, count=80)
        events = list(s.query(P1WalletEvent).all())
        score, _ = compute_wallet_score(events)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        s.close()

    def test_overrides(self):
        s = _session()
        _make_events(s, wallet="0xoverride", count=5)
        events = list(s.query(P1WalletEvent).filter(P1WalletEvent.wallet == "0xoverride").all())
        score_no_ov, _ = compute_wallet_score(events, wallet="0xoverride")
        score_ov, _ = compute_wallet_score(
            events, wallet="0xoverride", overrides={"0xoverride": 0.2}
        )
        self.assertGreater(score_ov, score_no_ov)
        s.close()

    def test_persist(self):
        s = _session()
        _make_events(s, wallet="0xpersist", count=10)
        score, details = score_and_persist_wallet(s, "0xpersist")
        s.commit()
        profile = get_wallet_profile(s, "0xpersist")
        self.assertIsNotNone(profile)
        self.assertAlmostEqual(profile.score, score, places=4)
        self.assertEqual(profile.trade_count, 10)
        s.close()

    def test_update_on_re_score(self):
        s = _session()
        _make_events(s, wallet="0xrescore", count=5)
        score1, _ = score_and_persist_wallet(s, "0xrescore")
        s.commit()
        # Add more events with unique keys (offset the index)
        for i in range(5, 20):
            s.add(P1WalletEvent(
                wallet="0xrescore",
                tx_hash=f"tx_{i}",
                token_id=f"tok_{'b' * 40}_{i}",
                side="BUY",
                price=0.45,
                usdc_value=25.0,
                outcome="yes",
            ))
        s.commit()
        score2, _ = score_and_persist_wallet(s, "0xrescore")
        s.commit()
        self.assertEqual(s.query(P1WalletProfile).count(), 1)
        s.close()


class TestCopyability(unittest.TestCase):
    def test_basic(self):
        result = compute_copyability(CopyabilityInput(
            wallet_score=0.8,
            source_price=0.35,
            market_liquidity=5000,
            market_volume=20000,
            usdc_value=25.0,
            outcome="yes",
            category="crypto_other",
        ))
        self.assertIsInstance(result, CopyabilityResult)
        self.assertGreater(result.score, 0.0)
        self.assertLessEqual(result.score, 1.0)
        self.assertIn("wallet_quality", result.components)

    def test_zero_wallet_score(self):
        result = compute_copyability(CopyabilityInput(
            wallet_score=0.0,
            source_price=0.5,
            market_liquidity=1000,
            market_volume=5000,
            usdc_value=10.0,
            outcome="unknown",
            category="other",
        ))
        self.assertGreaterEqual(result.score, 0.0)

    def test_extreme_price_penalty(self):
        r_good = compute_copyability(CopyabilityInput(
            wallet_score=0.5,
            source_price=0.35,
            market_liquidity=5000,
            market_volume=10000,
            usdc_value=20.0,
            outcome="yes",
            category="other",
        ))
        r_bad = compute_copyability(CopyabilityInput(
            wallet_score=0.5,
            source_price=0.01,
            market_liquidity=5000,
            market_volume=10000,
            usdc_value=20.0,
            outcome="yes",
            category="other",
        ))
        self.assertGreater(r_good.score, r_bad.score)

    def test_explanation_format(self):
        result = compute_copyability(CopyabilityInput(
            wallet_score=0.5,
            source_price=0.4,
            market_liquidity=3000,
            market_volume=8000,
            usdc_value=15.0,
            outcome="no",
            category="politics",
        ))
        self.assertIn("copyability=", result.explanation)


if __name__ == "__main__":
    unittest.main()
