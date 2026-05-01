"""Phase D: tests for the offline backtest harness.

All tests run **fully offline** against the synthetic mini fixture in
``tests/fixtures/backtest_mini.csv``. No network calls. Deterministic.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from bot.backtest.dataset import (
    DatasetError,
    SCHEMA_VERSION,
    LoadStats,
    iter_by_day,
    load_trades,
    sha256_of_file,
    verify_sha256,
)
from bot.backtest.replay import (
    ReplayConfig,
    run_replay,
)
from bot.backtest.report import (
    REPORT_SCHEMA_VERSION,
    render_markdown,
    to_dict,
    write_json,
    write_markdown,
)


_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "backtest_mini.csv"


class TestDatasetLoader(unittest.TestCase):
    def test_loads_mini_fixture(self):
        rows, stats = load_trades(_FIXTURE)
        self.assertGreater(stats.kept, 0)
        self.assertEqual(stats.kept, len(rows))
        # Schema version is exposed.
        self.assertEqual(SCHEMA_VERSION, "1")
        # Rows are sorted by timestamp ascending.
        ts = [r.ts_epoch for r in rows]
        self.assertEqual(ts, sorted(ts))

    def test_missing_required_column_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "broken.csv"
            # Drop the `wallet` column on purpose.
            p.write_text(
                "timestamp,market_slug,condition_id,token_id,category,outcome,side,price,usdc,realized_pnl,title\n"
                "2026-01-01T00:00:00Z,m,c,t,crypto_short,yes,BUY,0.5,5,1,t\n"
            )
            with self.assertRaises(DatasetError) as cm:
                load_trades(p)
            self.assertIn("missing required columns", str(cm.exception))

    def test_skips_sells_and_bad_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mixed.csv"
            p.write_text(
                "timestamp,wallet,market_slug,condition_id,token_id,category,outcome,side,price,usdc,realized_pnl,title\n"
                "2026-01-01T00:00:00Z,0x" + "a" * 40 + ",m,c,t,crypto_short,yes,BUY,0.5,5,1,t\n"
                # Wrong side -> skipped
                "2026-01-01T00:01:00Z,0x" + "a" * 40 + ",m,c,t,crypto_short,yes,SELL,0.5,5,1,t\n"
                # Bad address -> skipped
                "2026-01-01T00:02:00Z,nope,m,c,t,crypto_short,yes,BUY,0.5,5,1,t\n"
                # Missing token -> skipped
                "2026-01-01T00:03:00Z,0x" + "a" * 40 + ",m,c,,crypto_short,yes,BUY,0.5,5,1,t\n"
                # Out-of-range price -> skipped
                "2026-01-01T00:04:00Z,0x" + "a" * 40 + ",m,c,t,crypto_short,yes,BUY,1.5,5,1,t\n"
                # Bad timestamp -> skipped
                "not-a-date,0x" + "a" * 40 + ",m,c,t,crypto_short,yes,BUY,0.5,5,1,t\n"
            )
            rows, stats = load_trades(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(stats.skipped_not_buy, 1)
            self.assertEqual(stats.skipped_bad_wallet, 1)
            self.assertEqual(stats.skipped_missing_token, 1)
            self.assertEqual(stats.skipped_bad_price, 1)
            self.assertEqual(stats.skipped_bad_timestamp, 1)

    def test_csv_gz_supported(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tiny.csv.gz"
            content = (
                "timestamp,wallet,market_slug,condition_id,token_id,category,outcome,side,price,usdc,realized_pnl,title\n"
                "2026-01-01T00:00:00Z,0x" + "a" * 40 + ",m,c,t,crypto_short,yes,BUY,0.5,5,1,t\n"
            )
            with gzip.open(p, "wt", encoding="utf-8") as f:
                f.write(content)
            rows, stats = load_trades(p)
            self.assertEqual(stats.kept, 1)


class TestSha256Verification(unittest.TestCase):
    def test_sha256_round_trip(self):
        actual = sha256_of_file(_FIXTURE)
        # Recompute manually to make sure our chunked hasher matches.
        with _FIXTURE.open("rb") as f:
            ref = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual, ref)

    def test_verify_sha256_accepts_match(self):
        actual = sha256_of_file(_FIXTURE)
        verify_sha256(_FIXTURE, actual)
        # Also tolerates "sha256:" prefix and uppercase.
        verify_sha256(_FIXTURE, "sha256:" + actual.upper())

    def test_verify_sha256_rejects_mismatch(self):
        with self.assertRaises(DatasetError):
            verify_sha256(_FIXTURE, "0" * 64)


class TestIterByDay(unittest.TestCase):
    def test_groups_chronologically(self):
        rows, _ = load_trades(_FIXTURE)
        days = list(iter_by_day(rows))
        # Exactly the 4 days the fixture covers (Mar 1-4 2026).
        self.assertEqual(
            [d for d, _ in days],
            [
                _dt.date(2026, 3, 1),
                _dt.date(2026, 3, 2),
                _dt.date(2026, 3, 3),
                _dt.date(2026, 3, 4),
            ],
        )
        # All bucketed rows have the matching date.
        for d, bucket in days:
            for r in bucket:
                self.assertEqual(r.date, d)


class TestReplayDeterministic(unittest.TestCase):
    """Same input + config -> same output, twice in a row."""

    def _default_run(self):
        rows, _ = load_trades(_FIXTURE)
        cfg = ReplayConfig(
            initial_balance=500.0,
            follower_size_usd=5.0,
            min_win_rate=0.60,
            min_total_trades=5,
        )
        return run_replay(rows, cfg)

    def test_deterministic_pnl(self):
        a = self._default_run()
        b = self._default_run()
        self.assertEqual(a.gross_pnl, b.gross_pnl)
        self.assertEqual(a.total_accepted, b.total_accepted)
        self.assertEqual(a.hit_rate, b.hit_rate)

    def test_known_pnl_on_mini_fixture(self):
        """Frozen number — if this changes, the change is intentional and
        the assertion should be updated together with the explanation.
        """
        a = self._default_run()
        # The "winning" wallet 0xaa qualifies after 5 resolved trades on Mar 1.
        # Of its 28 trades total, the first 5 are pre-qualification so
        # we only copy the remaining 23. Each copy is sized $5 / source_usd
        # of the source's realized_pnl. Sum across all 23 copies = 30.9167.
        self.assertEqual(a.total_accepted, 23)
        self.assertAlmostEqual(a.gross_pnl, 30.9167, places=3)
        self.assertEqual(a.hit_rate, 1.0)
        self.assertEqual(a.max_drawdown, 0.0)
        # Loser wallet's 15 trades are all rejected.
        self.assertGreaterEqual(a.rejection_reasons.get("wallet_not_qualified", 0), 15)


class TestReplayBoundaries(unittest.TestCase):
    def _run_with(self, **kw):
        rows, _ = load_trades(_FIXTURE)
        cfg = ReplayConfig(
            initial_balance=500.0,
            follower_size_usd=5.0,
            min_win_rate=0.60,
            min_total_trades=5,
            **kw,
        )
        return run_replay(rows, cfg)

    def test_min_wallet_score_zero_keeps_qualifying_wallet(self):
        # Bottom boundary: with score=0 the gate is effectively off,
        # we copy every trade the qualifying wallet makes.
        r = self._run_with(min_wallet_score=0.0)
        self.assertGreater(r.total_accepted, 0)

    def test_min_wallet_score_above_one_filters_everything(self):
        # Top boundary: score gate at 1.1 (impossible) -> zero copies.
        r = self._run_with(min_wallet_score=1.1)
        self.assertEqual(r.total_accepted, 0)
        self.assertEqual(r.gross_pnl, 0.0)

    def test_manual_wallet_bypasses_quality_gate(self):
        # Force-pin the loser wallet -> we WOULD copy and accumulate losses.
        loser = "0x" + "b" * 40
        r = self._run_with(manual_wallets=[loser])
        # Loser's trades are now accepted.
        loser_attrib = next((w for w in r.per_wallet if w.wallet == loser), None)
        self.assertIsNotNone(loser_attrib, "loser should now be in attribution")
        self.assertGreater(loser_attrib.trades, 0)
        self.assertLess(loser_attrib.pnl, 0)

    def test_category_filter_narrows_accepted(self):
        all_cats = self._run_with()
        crypto_only = self._run_with(copy_allowed_categories=["crypto_short"])
        self.assertLess(crypto_only.total_accepted, all_cats.total_accepted)
        # Every accepted trade in the narrowed run must be in the allowlist.
        self.assertTrue(all(
            c.category == "crypto_short" for c in crypto_only.per_category
        ))

    def test_blocked_keyword_filters_matching_titles(self):
        # All "BTC up" titles get blocked.
        r = self._run_with(copy_blocked_keywords=["btc up"])
        # No accepted trade has 'btc up' in the title.
        # (Confirmed indirectly: per-category crypto_short trade count drops.)
        baseline = self._run_with()
        self.assertLess(r.total_accepted, baseline.total_accepted)

    def test_date_window_clamps(self):
        # Restrict to Mar 2 only -> no trades qualify yet on Mar 1
        # would have meant Mar 2 is the first day. Wallet-aa has 10 historical
        # trades on Mar 1, so by Mar 2 it qualifies and we copy on Mar 2.
        r = self._run_with(start_date=_dt.date(2026, 3, 2),
                           end_date=_dt.date(2026, 3, 2))
        self.assertEqual(r.start_date, _dt.date(2026, 3, 2))
        self.assertEqual(r.end_date, _dt.date(2026, 3, 2))
        self.assertGreater(r.total_accepted, 0)


class TestReplayLookAheadProtection(unittest.TestCase):
    """Make sure scoring at time T never sees data with ts >= T."""

    def test_first_trades_are_never_self_qualifying(self):
        # In the fixture, wallet-aa makes 5 BUYs in the first hour of Mar 1.
        # Before the 5th of those, it does NOT have 5 resolved trades, so
        # the first 5 should all be rejected with wallet_not_qualified.
        rows, _ = load_trades(_FIXTURE)
        cfg = ReplayConfig(initial_balance=500.0, follower_size_usd=5.0,
                           min_win_rate=0.60, min_total_trades=5)
        r = run_replay(rows, cfg)
        # 5 first-time-rejections from the winner wallet
        # + 15 from the loser (all of its 15 trades) -> at least 20 score rejects.
        self.assertGreaterEqual(r.total_rejected_by_score, 20)


class TestReportWriters(unittest.TestCase):
    def _result(self):
        rows, _ = load_trades(_FIXTURE)
        cfg = ReplayConfig(initial_balance=500.0, follower_size_usd=5.0)
        return run_replay(rows, cfg)

    def test_to_dict_shape(self):
        d = to_dict(self._result())
        self.assertEqual(d["schema_version"], REPORT_SCHEMA_VERSION)
        for k in ("config", "totals", "pnl", "rejection_reasons",
                  "per_wallet", "per_category", "daily"):
            self.assertIn(k, d)
        for k in ("source_rows", "evaluated", "accepted", "rejected_by_score",
                  "rejected_by_filter", "unresolved"):
            self.assertIn(k, d["totals"])

    def test_write_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.json"
            payload = write_json(self._result(), p)
            self.assertTrue(p.exists())
            on_disk = json.loads(p.read_text())
            self.assertEqual(on_disk, payload)

    def test_write_markdown_human_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.md"
            text = write_markdown(self._result(), p)
            self.assertEqual(p.read_text(), text)
            self.assertIn("# Backtest Report", text)
            self.assertIn("Gross PnL", text)


class TestNoNetworkContract(unittest.TestCase):
    """Belt-and-suspenders: nothing in bot/backtest/* should reach the net
    when only the loader+replay+report APIs are used.

    We can't intercept all syscalls, but we can confirm the modules don't
    even import any HTTP client, which is the most common silent footgun.
    """

    def test_no_http_imports_in_backtest_pkg(self):
        for fname in ("dataset.py", "replay.py", "report.py", "__init__.py"):
            text = (Path(__file__).resolve().parent.parent
                    / "bot" / "backtest" / fname).read_text()
            for forbidden in ("import httpx", "import requests", "from httpx", "from requests"):
                self.assertNotIn(forbidden, text,
                                 f"{fname} unexpectedly imports {forbidden}")


if __name__ == "__main__":
    unittest.main()
