"""Tests for wallet score guard layer: degradation, hysteresis, provisional caps, suspicious checks."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from bot.wallet_score_guards import (
    ScoreSnapshot,
    GuardVerdict,
    apply_hysteresis,
    check_suspicious,
    detect_degradation,
    provisional_score_cap,
    run_guards,
    score_tier,
)
from bot.copy_rules import CopyCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**kw):
    base = dict(
        copy_wallet_score_overrides={},
        wallet_score_decay_half_life_hours=168.0,
        wallet_provisional_cap_enabled=False,
        wallet_sparse_threshold=8,
        wallet_very_sparse_threshold=4,
        wallet_cap_at_sparse=0.60,
        wallet_cap_at_very_sparse=0.45,
        wallet_degradation_enabled=False,
        wallet_degradation_lookback_hours=168.0,
        wallet_degradation_min_drop_pct=20.0,
        wallet_suspicious_check_enabled=False,
        wallet_suspicious_penalty=0.30,
        wallet_hysteresis_enabled=False,
        wallet_hysteresis_promote_margin=0.05,
        wallet_hysteresis_demote_margin=0.05,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cand(title="Q", token_id="t" * 40, price=0.50, usdc=20.0, category="other", outcome="yes"):
    return CopyCandidate(
        wallet="w", token_id=token_id, tx_key="k",
        title=title, slug="", tags_text="", category=category,
        outcome=outcome, price=price, usdc=usdc,
    )


# ---------------------------------------------------------------------------
# Score tiering
# ---------------------------------------------------------------------------

class TestScoreTier(unittest.TestCase):
    def test_elite(self):
        self.assertEqual(score_tier(0.80), "elite")

    def test_good(self):
        self.assertEqual(score_tier(0.60), "good")

    def test_medium(self):
        self.assertEqual(score_tier(0.40), "medium")

    def test_low(self):
        self.assertEqual(score_tier(0.20), "low")

    def test_boundary_elite(self):
        self.assertEqual(score_tier(0.75), "elite")

    def test_boundary_good(self):
        self.assertEqual(score_tier(0.55), "good")

    def test_boundary_medium(self):
        self.assertEqual(score_tier(0.35), "medium")

    def test_zero(self):
        self.assertEqual(score_tier(0.0), "low")


# ---------------------------------------------------------------------------
# Degradation detection
# ---------------------------------------------------------------------------

class TestDegradation(unittest.TestCase):
    def test_no_history(self):
        degraded, pct = detect_degradation(0.5, [])
        self.assertFalse(degraded)
        self.assertEqual(pct, 0.0)

    def test_no_drop(self):
        now = time.time()
        history = [
            ScoreSnapshot(score=0.50, sample_count=20, epoch=now - 3600),
            ScoreSnapshot(score=0.52, sample_count=22, epoch=now - 7200),
        ]
        degraded, pct = detect_degradation(0.51, history, now_epoch=now)
        self.assertFalse(degraded)

    def test_significant_drop(self):
        now = time.time()
        history = [
            ScoreSnapshot(score=0.80, sample_count=30, epoch=now - 3600),
            ScoreSnapshot(score=0.78, sample_count=28, epoch=now - 7200),
        ]
        degraded, pct = detect_degradation(0.50, history, now_epoch=now)
        self.assertTrue(degraded)
        self.assertGreater(pct, 20.0)

    def test_stale_history_ignored(self):
        now = time.time()
        history = [
            ScoreSnapshot(score=0.90, sample_count=40, epoch=now - 30 * 86400),
        ]
        degraded, pct = detect_degradation(0.30, history, lookback_window_s=7 * 86400, now_epoch=now)
        self.assertFalse(degraded)

    def test_marginal_drop_below_threshold(self):
        now = time.time()
        history = [
            ScoreSnapshot(score=0.60, sample_count=20, epoch=now - 3600),
        ]
        degraded, pct = detect_degradation(0.55, history, min_drop_pct=20.0, now_epoch=now)
        self.assertFalse(degraded)


# ---------------------------------------------------------------------------
# Hysteresis / anti-flapping
# ---------------------------------------------------------------------------

class TestHysteresis(unittest.TestCase):
    def test_no_previous_tier(self):
        tier, held = apply_hysteresis(0.60, "unknown")
        self.assertEqual(tier, "good")
        self.assertFalse(held)

    def test_stays_in_tier_when_within_margin(self):
        tier, held = apply_hysteresis(0.77, "good", promote_margin=0.05)
        self.assertEqual(tier, "good")
        self.assertTrue(held)

    def test_promotes_when_exceeds_margin(self):
        tier, held = apply_hysteresis(0.82, "good", promote_margin=0.05)
        self.assertEqual(tier, "elite")
        self.assertFalse(held)

    def test_stays_in_tier_on_demotion_margin(self):
        tier, held = apply_hysteresis(0.53, "good", demote_margin=0.05)
        self.assertEqual(tier, "good")
        self.assertTrue(held)

    def test_demotes_when_below_margin(self):
        tier, held = apply_hysteresis(0.40, "good", demote_margin=0.05)
        self.assertEqual(tier, "medium")
        self.assertFalse(held)

    def test_same_tier_no_hold(self):
        tier, held = apply_hysteresis(0.60, "good")
        self.assertEqual(tier, "good")
        self.assertFalse(held)


# ---------------------------------------------------------------------------
# Provisional cap
# ---------------------------------------------------------------------------

class TestProvisionalCap(unittest.TestCase):
    def test_no_cap_sufficient_data(self):
        score, capped = provisional_score_cap(0.80, sample_count=20)
        self.assertEqual(score, 0.80)
        self.assertFalse(capped)

    def test_sparse_cap(self):
        score, capped = provisional_score_cap(0.80, sample_count=6)
        self.assertEqual(score, 0.60)
        self.assertTrue(capped)

    def test_very_sparse_cap(self):
        score, capped = provisional_score_cap(0.80, sample_count=3)
        self.assertEqual(score, 0.45)
        self.assertTrue(capped)

    def test_low_score_not_capped(self):
        score, capped = provisional_score_cap(0.30, sample_count=3)
        self.assertEqual(score, 0.30)
        self.assertFalse(capped)

    def test_boundary_sparse(self):
        score, capped = provisional_score_cap(0.60, sample_count=8)
        self.assertEqual(score, 0.60)
        self.assertFalse(capped)


# ---------------------------------------------------------------------------
# Suspicious wallet checks
# ---------------------------------------------------------------------------

class TestSuspiciousWallet(unittest.TestCase):
    def test_normal_wallet(self):
        cands = [_cand(title=f"Market {i}", token_id=f"t{i}" + "x" * 38, price=0.30 + i * 0.1) for i in range(5)]
        sus, reasons = check_suspicious(cands)
        self.assertFalse(sus)
        self.assertEqual(reasons, [])

    def test_wash_trading_detected(self):
        cands = [_cand(token_id="t" * 40, price=0.5000 + i * 0.001) for i in range(8)]
        sus, reasons = check_suspicious(cands, wash_trade_price_tolerance=0.01)
        self.assertTrue(sus)
        self.assertIn("wash_trade_pattern", reasons)

    def test_single_market_concentration(self):
        cands = [_cand(title="Same Market Question", token_id=f"t{i}" + "x" * 38, price=0.3 + i * 0.05) for i in range(6)]
        sus, reasons = check_suspicious(cands)
        self.assertTrue(sus)
        self.assertIn("single_market_concentration", reasons)

    def test_too_few_trades_not_suspicious(self):
        cands = [_cand()]
        sus, reasons = check_suspicious(cands)
        self.assertFalse(sus)


# ---------------------------------------------------------------------------
# Combined guard pipeline
# ---------------------------------------------------------------------------

class TestRunGuards(unittest.TestCase):
    def test_all_guards_off(self):
        v = run_guards(0.70, sample_count=20, candidates=[], settings=_settings())
        self.assertAlmostEqual(v.guarded_score, 0.70, places=4)
        self.assertEqual(v.tier, "good")
        self.assertFalse(v.provisional_cap_applied)
        self.assertFalse(v.degradation_flag)
        self.assertFalse(v.suspicious)
        self.assertFalse(v.hysteresis_held)

    def test_provisional_cap_active(self):
        s = _settings(wallet_provisional_cap_enabled=True)
        v = run_guards(0.80, sample_count=5, candidates=[], settings=s)
        self.assertEqual(v.guarded_score, 0.60)
        self.assertTrue(v.provisional_cap_applied)

    def test_degradation_detected(self):
        now = time.time()
        history = [
            ScoreSnapshot(score=0.80, sample_count=30, epoch=now - 3600),
        ]
        s = _settings(wallet_degradation_enabled=True)
        v = run_guards(0.50, sample_count=30, candidates=[], history=history, settings=s, now_epoch=now)
        self.assertTrue(v.degradation_flag)
        self.assertGreater(v.degradation_pct, 20.0)

    def test_suspicious_penalty_applied(self):
        cands = [_cand(title="Same Q", token_id="t" * 40, price=0.50) for _ in range(10)]
        s = _settings(wallet_suspicious_check_enabled=True, wallet_suspicious_penalty=0.30)
        v = run_guards(0.70, sample_count=10, candidates=cands, settings=s)
        self.assertTrue(v.suspicious)
        self.assertLess(v.guarded_score, 0.70)

    def test_hysteresis_holds_tier(self):
        s = _settings(wallet_hysteresis_enabled=True, wallet_hysteresis_promote_margin=0.05)
        v = run_guards(0.77, sample_count=20, candidates=[], previous_tier="good", settings=s)
        self.assertEqual(v.tier, "good")
        self.assertTrue(v.hysteresis_held)

    def test_combined_cap_and_suspicious(self):
        cands = [_cand(title="Same Q", token_id="t" * 40, price=0.50) for _ in range(5)]
        s = _settings(
            wallet_provisional_cap_enabled=True,
            wallet_suspicious_check_enabled=True,
            wallet_suspicious_penalty=0.20,
        )
        v = run_guards(0.80, sample_count=5, candidates=cands, settings=s)
        self.assertTrue(v.provisional_cap_applied)
        self.assertLessEqual(v.guarded_score, 0.60)


# ---------------------------------------------------------------------------
# Integration: wallet_score_v2 with guards
# ---------------------------------------------------------------------------

class TestWalletScoreV2WithGuards(unittest.TestCase):
    def _make_rows(self, n, **overrides):
        rows = []
        now = time.time()
        for i in range(n):
            row = {
                "type": "TRADE",
                "side": "BUY",
                "token_id": f"tok{'x' * 38}",
                "question": f"Will event {i % 5} happen?",
                "price": 0.30 + (i % 10) * 0.05,
                "amount": 20 + i,
                "outcome": "Yes" if i % 3 != 0 else "No",
                "timestamp": str(now - i * 3600),
            }
            row.update(overrides)
            rows.append(row)
        return rows

    def test_v2_returns_tier(self):
        from bot.wallet_scoring import wallet_score_v2
        rows = self._make_rows(20)
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=_settings(),
        )
        self.assertIn(result.tier, ("elite", "good", "medium", "low", "unknown"))

    def test_v2_guarded_score_matches_when_guards_off(self):
        from bot.wallet_scoring import wallet_score_v2
        rows = self._make_rows(20)
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=_settings(),
        )
        self.assertAlmostEqual(result.guarded_score, result.total_score, places=4)

    def test_v2_with_provisional_cap(self):
        from bot.wallet_scoring import wallet_score_v2
        rows = self._make_rows(5)
        s = _settings(
            wallet_provisional_cap_enabled=True,
            wallet_cap_at_sparse=0.60,
            wallet_cap_at_very_sparse=0.45,
            wallet_sparse_threshold=8,
            wallet_very_sparse_threshold=4,
        )
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=s,
        )
        # With 5 trades (sparse but not very-sparse), cap is 0.60
        self.assertLessEqual(score, 0.60)
        # Verify the guarded_score reflects the cap if raw was above it
        if result.total_score > 0.60:
            self.assertTrue(result.provisional_cap_applied)

    def test_v2_components_include_guard_info(self):
        from bot.wallet_scoring import wallet_score_v2
        rows = self._make_rows(20)
        score, result = wallet_score_v2(
            rows, wallet="0xabc", default_bet_usd=5.0, settings=_settings(),
        )
        self.assertIn("guarded", result.components)
        self.assertIn("tier", result.components)


if __name__ == "__main__":
    unittest.main()
