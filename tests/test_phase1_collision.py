"""Tests for Phase 1 anti-collision lock service."""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base
from bot.phase1.collision import (
    acquire_lock,
    cleanup_expired,
    is_locked,
    release_lock,
)
from bot.phase1.models import P1CollisionLock


def _session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class TestCollisionLock(unittest.TestCase):
    def test_acquire_and_check(self):
        s = _session()
        ok = acquire_lock(s, "cond_1", "tok_1", ttl_seconds=60)
        s.commit()
        self.assertTrue(ok)
        self.assertTrue(is_locked(s, "cond_1"))
        s.close()

    def test_double_acquire_blocked(self):
        s = _session()
        ok1 = acquire_lock(s, "cond_1", "tok_1", ttl_seconds=60)
        s.commit()
        ok2 = acquire_lock(s, "cond_1", "tok_1", ttl_seconds=60)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        s.close()

    def test_release(self):
        s = _session()
        acquire_lock(s, "cond_1", "tok_1", ttl_seconds=60)
        s.commit()
        released = release_lock(s, "cond_1")
        s.commit()
        self.assertTrue(released)
        self.assertFalse(is_locked(s, "cond_1"))
        ok = acquire_lock(s, "cond_1", "tok_1", ttl_seconds=60)
        self.assertTrue(ok)
        s.close()

    def test_expired_lock_allows_reacquire(self):
        s = _session()
        now = dt.datetime.now(dt.timezone.utc)
        expired_lock = P1CollisionLock(
            condition_id="cond_exp",
            token_id="tok_exp",
            locked_by="test",
            locked_at=now - dt.timedelta(hours=1),
            expires_at=now - dt.timedelta(seconds=1),
            released=False,
        )
        s.add(expired_lock)
        s.commit()
        self.assertFalse(is_locked(s, "cond_exp"))
        ok = acquire_lock(s, "cond_exp", "tok_exp", ttl_seconds=60)
        self.assertTrue(ok)
        s.close()

    def test_cleanup_expired(self):
        s = _session()
        now = dt.datetime.now(dt.timezone.utc)
        s.add(P1CollisionLock(
            condition_id="cond_old",
            token_id="tok_old",
            locked_by="test",
            locked_at=now - dt.timedelta(hours=1),
            expires_at=now - dt.timedelta(seconds=1),
            released=False,
        ))
        s.commit()
        count = cleanup_expired(s)
        s.commit()
        self.assertEqual(count, 1)
        s.close()

    def test_different_conditions_independent(self):
        s = _session()
        ok1 = acquire_lock(s, "cond_a", "tok_a", ttl_seconds=60)
        s.commit()
        ok2 = acquire_lock(s, "cond_b", "tok_b", ttl_seconds=60)
        s.commit()
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        s.close()


if __name__ == "__main__":
    unittest.main()
