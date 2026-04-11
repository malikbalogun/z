"""Tests for Phase 1 risk service."""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base
from bot.phase1.models import P1PaperTrade, P1TradeCandidate, PaperTradeStatus
from bot.phase1.risk_service import (
    RiskConfig,
    check_category_enabled,
    check_daily_notional,
    check_market_exposure,
    check_open_trades_limit,
    check_per_wallet_daily,
    check_price_bounds,
    check_size_bounds,
    check_total_exposure,
    run_risk_checks,
)


def _session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class TestSizeBounds(unittest.TestCase):
    def test_ok(self):
        r = check_size_bounds(10.0, RiskConfig(min_bet_usd=1.0, max_bet_usd=25.0))
        self.assertTrue(r.passed)

    def test_too_small(self):
        r = check_size_bounds(0.5, RiskConfig(min_bet_usd=1.0, max_bet_usd=25.0))
        self.assertFalse(r.passed)
        self.assertIn("below_min", r.reason)

    def test_too_large(self):
        r = check_size_bounds(30.0, RiskConfig(min_bet_usd=1.0, max_bet_usd=25.0))
        self.assertFalse(r.passed)
        self.assertIn("above_max", r.reason)


class TestPriceBounds(unittest.TestCase):
    def test_ok(self):
        r = check_price_bounds(0.5)
        self.assertTrue(r.passed)

    def test_too_low(self):
        r = check_price_bounds(0.005)
        self.assertFalse(r.passed)

    def test_too_high(self):
        r = check_price_bounds(0.995)
        self.assertFalse(r.passed)


class TestCategoryEnabled(unittest.TestCase):
    def test_no_flags(self):
        r = check_category_enabled("crypto_other", None)
        self.assertTrue(r.passed)

    def test_enabled(self):
        r = check_category_enabled("crypto_other", {"ENABLE_CRYPTO_OTHER": True})
        self.assertTrue(r.passed)

    def test_disabled(self):
        r = check_category_enabled("crypto_other", {"ENABLE_CRYPTO_OTHER": False})
        self.assertFalse(r.passed)


class TestMarketExposure(unittest.TestCase):
    def test_no_cap(self):
        s = _session()
        r = check_market_exposure(s, "cond_1", 10.0, 0.0)
        self.assertTrue(r.passed)
        s.close()

    def test_within_cap(self):
        s = _session()
        r = check_market_exposure(s, "cond_1", 10.0, 50.0)
        self.assertTrue(r.passed)
        s.close()

    def test_exceeds_cap(self):
        s = _session()
        s.add(P1PaperTrade(
            candidate_id=1,
            condition_id="cond_1",
            token_id="tok_1",
            limit_price=0.5,
            size_usd=45.0,
            status=PaperTradeStatus.OPEN.value,
        ))
        s.commit()
        r = check_market_exposure(s, "cond_1", 10.0, 50.0)
        self.assertFalse(r.passed)
        s.close()


class TestOpenTradesLimit(unittest.TestCase):
    def test_under_limit(self):
        s = _session()
        r = check_open_trades_limit(s, 10)
        self.assertTrue(r.passed)
        s.close()

    def test_at_limit(self):
        s = _session()
        for i in range(5):
            s.add(P1PaperTrade(
                candidate_id=i,
                condition_id=f"c{i}",
                token_id=f"t{i}",
                limit_price=0.5,
                size_usd=5.0,
                status=PaperTradeStatus.OPEN.value,
            ))
        s.commit()
        r = check_open_trades_limit(s, 5)
        self.assertFalse(r.passed)
        s.close()


class TestRunRiskChecks(unittest.TestCase):
    def test_all_pass(self):
        s = _session()
        passed, results = run_risk_checks(
            s,
            condition_id="cond_1",
            wallet="0xtest",
            size_usd=10.0,
            price=0.5,
            category="other",
            config=RiskConfig(),
        )
        self.assertTrue(passed)
        s.close()

    def test_one_fails(self):
        s = _session()
        passed, results = run_risk_checks(
            s,
            condition_id="cond_1",
            wallet="0xtest",
            size_usd=10.0,
            price=0.005,  # bad price
            category="other",
            config=RiskConfig(),
        )
        self.assertFalse(passed)
        failed = [r for r in results if not r.passed]
        self.assertTrue(any(r.check_name == "price_bounds" for r in failed))
        s.close()


if __name__ == "__main__":
    unittest.main()
