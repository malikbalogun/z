"""Phase A: tests for Copy Manager pin/unpin/remove + refresh log + endpoints.

These tests are pure unit tests against the CopyManager class (no DB or HTTP)
plus a small set of admin-endpoint tests that mock out the trader so we can
exercise the FastAPI routes without booting the full bot.

All tests run offline.
"""

from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

from bot.copy_manager import CopyManager, WalletStats
from bot.settings import Settings


def _silence_persist():
    """Replace upsert_many_kv with a no-op so tests don't touch the DB.

    Returns a `patch` object that callers should use as a context manager.
    """
    return patch("bot.copy_manager.upsert_many_kv")


class TestCopyManagerPinUnpin(unittest.TestCase):
    def test_pin_existing_active_wallet(self):
        wallet = "0x" + "11" * 20
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        mgr.state.wallet_stats[wallet] = WalletStats(wallet=wallet, status="active")
        with _silence_persist():
            res = mgr.pin(wallet)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "manual")
        self.assertEqual(res["prev_status"], "active")
        self.assertEqual(mgr.state.wallet_stats[wallet].status, "manual")

    def test_pin_new_wallet_creates_manual_entry(self):
        wallet = "0x" + "22" * 20
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        with _silence_persist() as upsert:
            res = mgr.pin(wallet)
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("created"))
        self.assertEqual(mgr.state.wallet_stats[wallet].status, "manual")
        # Persisted: copy_watch_wallets KV should now include the wallet.
        payload = upsert.call_args.args[0]
        saved = json.loads(payload["copy_watch_wallets"])
        self.assertIn(wallet, saved)

    def test_unpin_manual_wallet_becomes_active(self):
        wallet = "0x" + "33" * 20
        s = Settings(copy_watch_wallets=[wallet])
        mgr = CopyManager(s)
        # Seeded as manual
        self.assertEqual(mgr.state.wallet_stats[wallet].status, "manual")
        with _silence_persist():
            res = mgr.unpin(wallet)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "active")
        self.assertEqual(mgr.state.wallet_stats[wallet].status, "active")

    def test_unpin_nontracked_wallet_returns_error(self):
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        res = mgr.unpin("0x" + "44" * 20)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_tracked")

    def test_unpin_active_wallet_is_rejected(self):
        wallet = "0x" + "55" * 20
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        mgr.state.wallet_stats[wallet] = WalletStats(wallet=wallet, status="active")
        res = mgr.unpin(wallet)
        self.assertFalse(res["ok"])
        self.assertIn("not_manual", res["reason"])

    def test_pin_then_unpin_round_trip_preserves_wallet(self):
        wallet = "0x" + "66" * 20
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        with _silence_persist():
            mgr.pin(wallet)
            mgr.unpin(wallet)
        self.assertEqual(mgr.state.wallet_stats[wallet].status, "active")


class TestCopyManagerRemove(unittest.TestCase):
    def test_remove_active_wallet(self):
        wallet = "0x" + "77" * 20
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        mgr.state.wallet_stats[wallet] = WalletStats(wallet=wallet, status="active")
        with _silence_persist() as upsert:
            res = mgr.remove(wallet)
        self.assertTrue(res["ok"])
        self.assertNotIn(wallet, mgr.state.wallet_stats)
        # Persisted list should no longer include the wallet.
        payload = upsert.call_args.args[0]
        saved = json.loads(payload["copy_watch_wallets"])
        self.assertNotIn(wallet, saved)

    def test_remove_nontracked_wallet_returns_error(self):
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        res = mgr.remove("0x" + "88" * 20)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_tracked")


class TestCopyManagerRefreshLog(unittest.TestCase):
    def test_refresh_log_starts_empty(self):
        s = Settings()
        mgr = CopyManager(s)
        self.assertEqual(list(mgr.state.refresh_log), [])
        self.assertEqual(mgr.get_refresh_log(50), [])

    def test_refresh_log_caps_at_50_entries(self):
        s = Settings()
        mgr = CopyManager(s)
        for i in range(75):
            mgr.state.refresh_log.append(
                {"ts": float(i), "added": 0, "pruned": 0,
                 "active_after": 0, "duration_ms": 1.0, "error": None}
            )
        # Ring buffer must enforce its cap regardless of how many we append.
        self.assertEqual(len(mgr.state.refresh_log), 50)
        # Entries are kept FIFO -> the first 25 were dropped.
        first_kept = list(mgr.state.refresh_log)[0]
        self.assertEqual(first_kept["ts"], 25.0)
        # get_refresh_log(limit) returns the most recent `limit` entries.
        recent = mgr.get_refresh_log(10)
        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[-1]["ts"], 74.0)

    def test_get_summary_includes_max_watched_and_interval(self):
        # The dashboard relies on these fields to render the countdown.
        s = Settings(copy_max_watched_wallets=42, copy_refresh_interval_hours=2.5)
        mgr = CopyManager(s)
        sm = mgr.get_summary()
        self.assertEqual(sm["max_watched_wallets"], 42)
        self.assertAlmostEqual(sm["refresh_interval_s"], 2.5 * 3600)


class TestCopyManagerGetManagedWallets(unittest.TestCase):
    def test_source_and_is_manual_fields(self):
        s = Settings(copy_watch_wallets=[])
        mgr = CopyManager(s)
        manual = "0x" + "aa" * 20
        active = "0x" + "bb" * 20
        pruned = "0x" + "cc" * 20
        mgr.state.wallet_stats[manual] = WalletStats(
            wallet=manual, status="manual", win_rate=0.0
        )
        mgr.state.wallet_stats[active] = WalletStats(
            wallet=active, status="active", win_rate=0.95
        )
        mgr.state.wallet_stats[pruned] = WalletStats(
            wallet=pruned, status="pruned", win_rate=0.30
        )
        rows = mgr.get_managed_wallets()
        # Active first, then manual, then pruned.
        self.assertEqual([r["status"] for r in rows], ["active", "manual", "pruned"])
        # Each row has the new is_manual + source fields.
        for r in rows:
            self.assertIn("is_manual", r)
            self.assertIn("source", r)
        manual_row = next(r for r in rows if r["wallet"] == manual)
        self.assertTrue(manual_row["is_manual"])
        self.assertEqual(manual_row["source"], "manual")
        active_row = next(r for r in rows if r["wallet"] == active)
        self.assertFalse(active_row["is_manual"])
        self.assertEqual(active_row["source"], "auto")


class TestAdminCopyManagerEndpoints(unittest.TestCase):
    """Exercise the new FastAPI endpoints with a minimal mocked trader.

    Uses a TEMP SQLite DB so we never touch the real data/app.db (which would
    pollute other tests and the developer's local state).
    """

    _tmp_db_path = None
    _orig_engine = None
    _orig_session_local = None

    @classmethod
    def setUpClass(cls):
        import tempfile
        from bot.db import models as db_models
        from bot.db.bootstrap import init_database

        # Save originals so tearDownClass can restore them.
        cls._orig_engine = db_models._engine
        cls._orig_session_local = db_models.SessionLocal

        # Spin up an isolated SQLite DB just for this test class.
        fd, cls._tmp_db_path = tempfile.mkstemp(prefix="cm_admin_test_", suffix=".db")
        import os
        os.close(fd)
        url = f"sqlite:///{cls._tmp_db_path}"
        cls.cfg = init_database(url)

    @classmethod
    def tearDownClass(cls):
        from bot.db import models as db_models
        # Dispose the test engine and restore whatever was there before.
        try:
            if db_models._engine is not None:
                db_models._engine.dispose()
        except Exception:
            pass
        db_models._engine = cls._orig_engine
        db_models.SessionLocal = cls._orig_session_local
        # Re-bind the FastAPI deps' SessionLocal capture too.
        import bot.web.deps as deps_mod
        deps_mod.SessionLocal = cls._orig_session_local
        # Best-effort: delete temp file.
        if cls._tmp_db_path:
            import os
            try:
                os.unlink(cls._tmp_db_path)
            except OSError:
                pass

    def _client_with_bot(self, bot):
        # Re-bind the deps' SessionLocal in case it was captured before init.
        import bot.web.deps as deps_mod
        from bot.db import models as db_models
        deps_mod.SessionLocal = db_models.SessionLocal
        import server
        server.app.state.trader = bot
        server.trader = bot
        from fastapi.testclient import TestClient
        c = TestClient(server.app)
        r = c.post("/api/auth/login", json={
            "username": self.cfg["initial_admin_username"],
            "password": self.cfg["initial_admin_password"],
        })
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def _make_bot(self):
        """Tiny fake trader that exposes only what the endpoints touch."""
        import httpx

        class _FakeBot:
            def __init__(self):
                self.settings = Settings(copy_watch_wallets=[])
                self._copy_manager = CopyManager(self.settings)
                self._http = httpx.AsyncClient(timeout=5.0)
                # _copy_agent is touched by _reload_settings_after_copy_change
                class _FakeAgent:
                    settings = None
                self._copy_agent = _FakeAgent()
        return _FakeBot()

    def test_status_returns_summary_wallets_and_log(self):
        bot = self._make_bot()
        c = self._client_with_bot(bot)
        r = c.get("/api/admin/copy-manager")
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j["ok"])
        for k in ("active_wallets", "manual_wallets", "pruned_wallets",
                  "max_watched_wallets", "refresh_interval_s"):
            self.assertIn(k, j["summary"])
        self.assertEqual(j["wallets"], [])
        self.assertEqual(j["refresh_log"], [])

    def test_pin_unpin_remove_round_trip(self):
        bot = self._make_bot()
        c = self._client_with_bot(bot)
        wallet = "0x" + "ab" * 20
        # Pin -> creates manual entry
        with patch("bot.copy_manager.upsert_many_kv"):
            r = c.post("/api/admin/copy-manager/pin", json={"wallet": wallet})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "manual")
        # Unpin -> active
        with patch("bot.copy_manager.upsert_many_kv"):
            r = c.post("/api/admin/copy-manager/unpin", json={"wallet": wallet})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "active")
        # Remove -> gone
        with patch("bot.copy_manager.upsert_many_kv"):
            r = c.post("/api/admin/copy-manager/remove", json={"wallet": wallet})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(wallet, bot._copy_manager.state.wallet_stats)

    def test_pin_invalid_wallet_returns_400(self):
        bot = self._make_bot()
        c = self._client_with_bot(bot)
        r = c.post("/api/admin/copy-manager/pin", json={"wallet": "not-a-wallet"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "invalid_wallet_address")

    def test_unpin_active_wallet_returns_400_with_reason(self):
        bot = self._make_bot()
        # Pre-seed an active wallet directly so unpin has something to reject.
        wallet = "0x" + "ef" * 20
        bot._copy_manager.state.wallet_stats[wallet] = WalletStats(
            wallet=wallet, status="active"
        )
        c = self._client_with_bot(bot)
        r = c.post("/api/admin/copy-manager/unpin", json={"wallet": wallet})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not_manual", r.json()["detail"])

    def test_history_endpoint_returns_log_entries(self):
        bot = self._make_bot()
        # Seed two entries via the ring buffer directly.
        bot._copy_manager.state.refresh_log.append({
            "ts": time.time(), "added": 5, "pruned": 1,
            "active_after": 30, "duration_ms": 1234.5, "error": None,
        })
        bot._copy_manager.state.refresh_log.append({
            "ts": time.time(), "added": 0, "pruned": 0,
            "active_after": 30, "duration_ms": 12.0, "error": "boom",
        })
        c = self._client_with_bot(bot)
        r = c.get("/api/admin/copy-manager/history?limit=50")
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual(len(j["history"]), 2)
        self.assertEqual(j["history"][-1]["error"], "boom")

    def test_endpoints_503_when_trader_missing(self):
        # Re-bind deps + clear app.state.trader.
        import bot.web.deps as deps_mod
        from bot.db import models as db_models
        deps_mod.SessionLocal = db_models.SessionLocal
        import server
        server.app.state.trader = None
        server.trader = None
        from fastapi.testclient import TestClient
        c = TestClient(server.app)
        r = c.post("/api/auth/login", json={
            "username": self.cfg["initial_admin_username"],
            "password": self.cfg["initial_admin_password"],
        })
        self.assertEqual(r.status_code, 200)
        for path, method, body in [
            ("/api/admin/copy-manager", "GET", None),
            ("/api/admin/copy-manager/history", "GET", None),
            ("/api/admin/copy-manager/refresh", "POST", {}),
            ("/api/admin/copy-manager/pin", "POST", {"wallet": "0x" + "1" * 40}),
            ("/api/admin/copy-manager/unpin", "POST", {"wallet": "0x" + "1" * 40}),
            ("/api/admin/copy-manager/remove", "POST", {"wallet": "0x" + "1" * 40}),
        ]:
            if method == "GET":
                rr = c.get(path)
            else:
                rr = c.post(path, json=body)
            self.assertEqual(rr.status_code, 503, f"{method} {path}: {rr.text}")


if __name__ == "__main__":
    unittest.main()
