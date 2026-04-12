"""Tests for state propagation, mode syncing, dry-run execution settings wiring."""

from __future__ import annotations

import unittest

from bot.agents.registry import agents_status
from bot.execution import _simulate_paper_fill
from bot.models import BotState
from bot.settings import Settings


class TestModeSyncsWithDryRun(unittest.TestCase):
    """BotState.mode must stay in sync with settings.dry_run after reloads."""

    def test_initial_mode_dry(self):
        s = Settings(dry_run=True)
        state = BotState(mode="dry_run" if s.dry_run else "live")
        self.assertEqual(state.mode, "dry_run")

    def test_initial_mode_live(self):
        s = Settings(dry_run=False)
        state = BotState(mode="dry_run" if s.dry_run else "live")
        self.assertEqual(state.mode, "live")

    def test_mode_update_on_toggle(self):
        """Simulate what _reload_settings_async now does: sync state.mode."""
        state = BotState(mode="dry_run")
        s = Settings(dry_run=False)
        state.mode = "dry_run" if s.dry_run else "live"
        self.assertEqual(state.mode, "live")

        s2 = Settings(dry_run=True)
        state.mode = "dry_run" if s2.dry_run else "live"
        self.assertEqual(state.mode, "dry_run")


class TestCopyAgentStatus(unittest.TestCase):
    """Copy signal card shows ON/OFF based on agent_copy AND copy_watch_wallets."""

    def test_copy_off_when_disabled(self):
        s = Settings(agent_copy=False, copy_watch_wallets=[])
        statuses = agents_status(s)
        copy = next(a for a in statuses if a["id"] == "copy_signal")
        self.assertFalse(copy["enabled"])

    def test_copy_off_when_enabled_but_no_wallets(self):
        s = Settings(agent_copy=True, copy_watch_wallets=[])
        statuses = agents_status(s)
        copy = next(a for a in statuses if a["id"] == "copy_signal")
        self.assertFalse(copy["enabled"])

    def test_copy_on_when_enabled_and_wallets(self):
        s = Settings(agent_copy=True, copy_watch_wallets=["0x" + "a" * 40])
        statuses = agents_status(s)
        copy = next(a for a in statuses if a["id"] == "copy_signal")
        self.assertTrue(copy["enabled"])


class TestPaperFillSettings(unittest.TestCase):
    """_simulate_paper_fill respects paper_realism_enabled and settings params."""

    def test_realism_disabled_returns_plain_dry_run(self):
        result = _simulate_paper_fill(
            token_id="t" * 42,
            side="BUY",
            price=0.5,
            size=10.0,
            paper_realism_enabled=False,
            slippage_model_bps=100.0,
            latency_ms=1000.0,
        )
        self.assertEqual(result, "dry_run")

    def test_realism_enabled_returns_paper_result(self):
        result = _simulate_paper_fill(
            token_id="t" * 42,
            side="BUY",
            price=0.5,
            size=10.0,
            paper_realism_enabled=True,
            slippage_model_bps=50.0,
            latency_ms=500.0,
        )
        self.assertTrue(
            result.startswith("dry_run"),
            f"Expected dry_run prefix, got: {result}",
        )
        self.assertNotEqual(result, "dry_run")

    def test_default_realism_enabled(self):
        result = _simulate_paper_fill(
            token_id="t" * 42,
            side="BUY",
            price=0.5,
            size=10.0,
        )
        self.assertTrue(result.startswith("dry_run"))


class TestSettingsFromKv(unittest.TestCase):
    """Settings.from_kv correctly round-trips copy_watch_wallets and agent flags."""

    def test_wallets_from_kv(self):
        addr = "0x" + "ab" * 20
        kv = {"copy_watch_wallets": f'["{addr}"]', "agent_copy": "true"}
        s = Settings.from_kv(kv)
        self.assertTrue(s.agent_copy)
        self.assertEqual(s.copy_watch_wallets, [addr])

    def test_empty_wallets_from_kv(self):
        kv = {"copy_watch_wallets": "[]", "agent_copy": "true"}
        s = Settings.from_kv(kv)
        self.assertTrue(s.agent_copy)
        self.assertEqual(s.copy_watch_wallets, [])

    def test_invalid_wallet_filtered(self):
        kv = {"copy_watch_wallets": '["not_valid", "0x' + "ab" * 20 + '"]'}
        s = Settings.from_kv(kv)
        self.assertEqual(len(s.copy_watch_wallets), 1)

    def test_dry_run_defaults_true(self):
        s = Settings.from_kv({})
        self.assertTrue(s.dry_run)

    def test_dry_run_false(self):
        s = Settings.from_kv({"dry_run": "false"})
        self.assertFalse(s.dry_run)


class TestPaperRealismSettingsWiring(unittest.TestCase):
    """Settings correctly loads paper realism fields from KV."""

    def test_paper_realism_fields_from_kv(self):
        kv = {
            "paper_realism_enabled": "true",
            "paper_slippage_model_bps": "75",
            "follower_latency_ms": "300",
        }
        s = Settings.from_kv(kv)
        self.assertTrue(s.paper_realism_enabled)
        self.assertAlmostEqual(s.paper_slippage_model_bps, 75.0)
        self.assertAlmostEqual(s.follower_latency_ms, 300.0)

    def test_paper_realism_disabled(self):
        kv = {"paper_realism_enabled": "false"}
        s = Settings.from_kv(kv)
        self.assertFalse(s.paper_realism_enabled)


if __name__ == "__main__":
    unittest.main()
