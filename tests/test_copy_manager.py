"""Tests for CopyManager — manual wallet preservation and prune behavior."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock


class TestCopyManagerManualPreservation(unittest.TestCase):
    def _make_mgr(self):
        from bot.copy_manager import CopyManager

        settings = SimpleNamespace(
            copy_refresh_interval_hours=6.0,
            copy_min_win_rate=0.60,
            copy_min_win_streak=3,
            copy_min_total_trades=5,
            copy_max_watched_wallets=50,
            copy_auto_manage=True,
            copy_discover_categories=["OVERALL"],
        )
        return CopyManager(settings)

    def test_ingest_external_wallets_adds_manual_entries(self):
        mgr = self._make_mgr()
        manual = "0x" + "a" * 40
        with mock.patch(
            "bot.copy_manager.load_all_kv",
            return_value={"copy_watch_wallets": json.dumps([manual])},
        ):
            mgr._ingest_external_wallets()
        self.assertIn(manual, mgr.state.wallet_stats)
        self.assertEqual(mgr.state.wallet_stats[manual].status, "manual")

    def test_persist_preserves_manual_wallets(self):
        mgr = self._make_mgr()
        manual = "0x" + "b" * 40
        with mock.patch(
            "bot.copy_manager.load_all_kv",
            return_value={"copy_watch_wallets": json.dumps([manual])},
        ):
            mgr._ingest_external_wallets()

        captured = {}

        def fake_upsert(kv):
            captured.update(kv)

        with mock.patch("bot.copy_manager.upsert_many_kv", side_effect=fake_upsert):
            mgr._persist_wallets()

        persisted = json.loads(captured["copy_watch_wallets"])
        self.assertIn(manual, persisted)

    def test_invalid_wallets_are_ignored(self):
        mgr = self._make_mgr()
        # "banana" is not a 0x-prefixed 42-char hex string.
        with mock.patch(
            "bot.copy_manager.load_all_kv",
            return_value={"copy_watch_wallets": json.dumps(["banana", ""])},
        ):
            mgr._ingest_external_wallets()
        self.assertEqual(mgr.state.wallet_stats, {})


if __name__ == "__main__":
    unittest.main()
