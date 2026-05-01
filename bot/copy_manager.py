"""Self-managing copy-trade wallet pool.

Handles:
- Periodic leaderboard re-scan across all categories
- Quality gate enforcement (win rate, streak, min trades)
- Automatic pruning of wallets that drop below thresholds
- Per-wallet performance tracking with live stats
- Category-aware discovery (maps leaderboard categories to bot categories)
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque

import httpx

from bot.db.kv import upsert_many_kv
from bot.leaderboard import (
    analyze_wallet_quality,
    discover_qualified_wallets,
    CATEGORIES as LB_CATEGORIES,
)

log = logging.getLogger("polymarket.copy_manager")

# How often to re-scan leaderboard (seconds). Default: every 6 hours.
_DEFAULT_REFRESH_INTERVAL = 6 * 3600

# Cap for the in-memory refresh history ring buffer.
_REFRESH_LOG_CAP = 50


@dataclass
class WalletStats:
    """Live tracking for a watched wallet."""
    wallet: str
    added_at: float = 0.0
    last_checked: float = 0.0
    win_rate: float = 0.0
    wins: int = 0
    losses: int = 0
    max_streak: int = 0
    current_streak: int = 0
    total_pnl: float = 0.0
    source_category: str = ""
    user_name: str = ""
    leaderboard_pnl: float = 0.0
    status: str = "active"  # active | probation | pruned | manual


@dataclass
class CopyManagerState:
    """Persistent (in-memory) state for the copy manager.

    `refresh_log` is a ring buffer of recent refresh outcomes for the dashboard.
    Each entry: {ts, added, pruned, active_after, duration_ms, error}.
    """
    wallet_stats: dict[str, WalletStats] = field(default_factory=dict)
    last_refresh: float = 0.0
    last_prune: float = 0.0
    refresh_count: int = 0
    total_added: int = 0
    total_pruned: int = 0
    refresh_log: Deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=_REFRESH_LOG_CAP)
    )


class CopyManager:
    """Runs inside the trading loop to keep copy_watch_wallets fresh and qualified."""

    def __init__(self, settings: Any):
        self.settings = settings
        self.state = CopyManagerState()
        self._http: httpx.AsyncClient | None = None
        self._seed_manual_wallets()

    def _seed_manual_wallets(self) -> None:
        """Seed configured wallets as manual so auto-manage cannot wipe them."""
        for wallet in list(getattr(self.settings, "copy_watch_wallets", []) or []):
            w = str(wallet).strip().lower()
            if not w:
                continue
            if w in self.state.wallet_stats:
                continue
            self.state.wallet_stats[w] = WalletStats(
                wallet=w,
                added_at=time.time(),
                last_checked=0.0,
                status="manual",
            )

    def sync_settings(self, settings: Any) -> None:
        """Apply reloaded settings and keep manual wallets pinned."""
        self.settings = settings
        self._seed_manual_wallets()

    def _refresh_interval(self) -> float:
        return float(getattr(self.settings, "copy_refresh_interval_hours", 6.0) or 6.0) * 3600

    def _min_win_rate(self) -> float:
        return float(getattr(self.settings, "copy_min_win_rate", 0.60) or 0.60)

    def _min_win_streak(self) -> int:
        return int(getattr(self.settings, "copy_min_win_streak", 3) or 3)

    def _min_total_trades(self) -> int:
        return int(getattr(self.settings, "copy_min_total_trades", 5) or 5)

    def _max_wallets(self) -> int:
        return int(getattr(self.settings, "copy_max_watched_wallets", 50) or 50)

    def _auto_manage(self) -> bool:
        return bool(getattr(self.settings, "copy_auto_manage", True))

    def _prune_below_win_rate(self) -> float:
        """Wallets that drop below this get pruned. Slightly lower than discovery threshold."""
        return max(self._min_win_rate() - 0.10, 0.0)

    def needs_refresh(self) -> bool:
        if not self._auto_manage():
            return False
        if self.state.last_refresh == 0:
            return True
        return (time.time() - self.state.last_refresh) >= self._refresh_interval()

    async def refresh(self, http: httpx.AsyncClient) -> dict[str, Any]:
        """Full refresh cycle: discover new wallets + prune underperformers.

        Records a single entry in `state.refresh_log` for the dashboard
        (capped ring buffer) so the UI can show a recent history.
        """
        self._http = http
        self._seed_manual_wallets()
        started = time.monotonic()
        started_wall = time.time()
        result: dict[str, Any] = {"added": 0, "pruned": 0, "checked": 0, "active": 0}
        error: str | None = None

        try:
            # 1. Discover new qualified wallets from all leaderboard categories
            new_count = await self._discover_and_add(http)
            result["added"] = new_count

            # 2. Re-check existing wallets and prune underperformers
            prune_count = await self._check_and_prune(http)
            result["pruned"] = prune_count

            # 3. Persist updated wallet list to DB
            self._persist_wallets()

            result["active"] = sum(1 for s in self.state.wallet_stats.values() if s.status == "active")
            result["checked"] = len(self.state.wallet_stats)

            self.state.last_refresh = time.time()
            self.state.refresh_count += 1

            log.info(
                "CopyManager refresh #%d: +%d added, -%d pruned, %d active",
                self.state.refresh_count, new_count, prune_count, result["active"],
            )
        except Exception as e:
            log.error("CopyManager refresh failed: %s", e)
            error = str(e)
            result["error"] = error

        duration_ms = round((time.monotonic() - started) * 1000.0, 2)
        result["duration_ms"] = duration_ms
        self.state.refresh_log.append(
            {
                "ts": started_wall,
                "added": int(result.get("added", 0)),
                "pruned": int(result.get("pruned", 0)),
                "active_after": int(result.get("active", 0)),
                "duration_ms": duration_ms,
                "error": error,
            }
        )
        return result

    async def _discover_and_add(self, http: httpx.AsyncClient) -> int:
        """Discover qualified wallets from all leaderboard categories."""
        lb_cats = list(getattr(self.settings, "copy_discover_categories", None) or list(LB_CATEGORIES))

        qualified = await discover_qualified_wallets(
            http,
            categories=lb_cats,
            time_period="MONTH",
            limit_per_category=25,
            min_pnl=0,
            min_win_rate=self._min_win_rate(),
            min_win_streak=self._min_win_streak(),
            min_total_trades=self._min_total_trades(),
        )

        added = 0
        max_w = self._max_wallets()
        active_count = sum(1 for s in self.state.wallet_stats.values() if s.status == "active")

        for q in qualified:
            w = q["wallet"]
            if w in self.state.wallet_stats:
                # Update stats for existing wallet
                st = self.state.wallet_stats[w]
                st.win_rate = q.get("win_rate", st.win_rate)
                st.wins = q.get("wins", st.wins)
                st.losses = q.get("losses", st.losses)
                st.max_streak = q.get("max_streak", st.max_streak)
                st.current_streak = q.get("current_streak", st.current_streak)
                st.total_pnl = q.get("total_pnl", st.total_pnl)
                st.last_checked = time.time()
                if st.status == "pruned":
                    st.status = "active"
                    added += 1
                continue

            if active_count + added >= max_w:
                break

            self.state.wallet_stats[w] = WalletStats(
                wallet=w,
                added_at=time.time(),
                last_checked=time.time(),
                win_rate=q.get("win_rate", 0),
                wins=q.get("wins", 0),
                losses=q.get("losses", 0),
                max_streak=q.get("max_streak", 0),
                current_streak=q.get("current_streak", 0),
                total_pnl=q.get("total_pnl", 0),
                source_category=q.get("category", "OVERALL"),
                user_name=q.get("userName", ""),
                leaderboard_pnl=q.get("pnl", 0),
                status="active",
            )
            added += 1

        self.state.total_added += added
        return added

    async def _check_and_prune(self, http: httpx.AsyncClient) -> int:
        """Re-check all active wallets and prune those below threshold.
        Wallets that were freshly analyzed during _discover_and_add in this same
        cycle re-use cached stats to avoid an N+1 round of /closed-positions calls."""
        prune_threshold = self._prune_below_win_rate()
        min_trades = self._min_total_trades()
        pruned = 0
        now = time.time()
        skip_if_checked_within_s = max(60.0, 0.9 * self._refresh_interval())

        for w, st in list(self.state.wallet_stats.items()):
            if st.status != "active":
                continue

            if st.last_checked and (now - st.last_checked) < skip_if_checked_within_s:
                total = int(st.wins + st.losses)
                if total >= min_trades and st.win_rate < prune_threshold:
                    st.status = "pruned"
                    pruned += 1
                    log.info(
                        "CopyManager PRUNED %s (cached): WR=%.0f%% < %.0f%% threshold",
                        w[:12], st.win_rate * 100, prune_threshold * 100,
                    )
                continue

            try:
                quality = await analyze_wallet_quality(http, w, limit=100)
            except Exception as e:
                log.warning("quality check failed for %s: %s", w[:12], e)
                continue

            st.win_rate = quality["win_rate"]
            st.wins = quality["wins"]
            st.losses = quality["losses"]
            st.max_streak = quality["max_streak"]
            st.current_streak = quality["current_streak"]
            st.total_pnl = quality["total_pnl"]
            st.last_checked = time.time()

            if quality["total"] >= min_trades and quality["win_rate"] < prune_threshold:
                st.status = "pruned"
                pruned += 1
                log.info(
                    "CopyManager PRUNED %s: WR=%.0f%% < %.0f%% threshold",
                    w[:12], quality["win_rate"] * 100, prune_threshold * 100,
                )

        self.state.total_pruned += pruned
        return pruned

    def _persist_wallets(self) -> None:
        """Write the active wallet list back to copy_watch_wallets in DB."""
        watch = [
            w
            for w, st in self.state.wallet_stats.items()
            if st.status in {"active", "manual"}
        ]
        upsert_many_kv({"copy_watch_wallets": json.dumps(watch)})

    def get_managed_wallets(self) -> list[dict[str, Any]]:
        """Return all tracked wallets with their stats for the dashboard."""
        out = []
        for w, st in self.state.wallet_stats.items():
            out.append({
                "wallet": w,
                "status": st.status,
                # Stable boolean + label fields so the UI can show MANUAL vs AUTO
                # without recomputing from `status`. Manual wallets keep a
                # source_category if we know it; auto wallets always have one.
                "is_manual": st.status == "manual",
                "source": "manual" if st.status == "manual" else "auto",
                "win_rate": st.win_rate,
                "wins": st.wins,
                "losses": st.losses,
                "max_streak": st.max_streak,
                "current_streak": st.current_streak,
                "total_pnl": st.total_pnl,
                "source_category": st.source_category,
                "user_name": st.user_name,
                "leaderboard_pnl": st.leaderboard_pnl,
                "added_at": st.added_at,
                "last_checked": st.last_checked,
            })
        # Sort: active first, then manual, then pruned; within each group, by win_rate desc.
        _order = {"active": 0, "manual": 1, "pruned": 2}
        out.sort(key=lambda x: (_order.get(x["status"], 9), -float(x["win_rate"] or 0)))
        return out

    def get_refresh_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent refresh events (newest last)."""
        n = max(1, min(_REFRESH_LOG_CAP, int(limit or _REFRESH_LOG_CAP)))
        return list(self.state.refresh_log)[-n:]

    def get_summary(self) -> dict[str, Any]:
        stats = self.state.wallet_stats
        active = sum(1 for s in stats.values() if s.status == "active")
        manual = sum(1 for s in stats.values() if s.status == "manual")
        pruned = sum(1 for s in stats.values() if s.status == "pruned")
        return {
            "active_wallets": active,
            "manual_wallets": manual,
            "pruned_wallets": pruned,
            "total_tracked": len(stats),
            "refresh_count": self.state.refresh_count,
            "total_added": self.state.total_added,
            "total_pruned": self.state.total_pruned,
            "last_refresh": self.state.last_refresh,
            "next_refresh_in_s": max(0, self._refresh_interval() - (time.time() - self.state.last_refresh)) if self.state.last_refresh else 0,
            "auto_manage": self._auto_manage(),
            "min_win_rate": self._min_win_rate(),
            "prune_threshold": self._prune_below_win_rate(),
            "refresh_interval_s": self._refresh_interval(),
            "max_watched_wallets": self._max_wallets(),
        }

    # ----- Manual wallet actions (UI-driven) ------------------------------------

    def pin(self, wallet: str) -> dict[str, Any]:
        """Convert an existing wallet to MANUAL status, persist.

        Manual wallets are protected from auto-prune. Returns the new state for
        the wallet (or {"ok": False, "reason": ...} if not found).
        """
        w = str(wallet).strip().lower()
        if not w:
            return {"ok": False, "reason": "empty_wallet"}
        st = self.state.wallet_stats.get(w)
        now = time.time()
        if st is None:
            # Pinning a wallet we don't track yet: create as manual.
            self.state.wallet_stats[w] = WalletStats(
                wallet=w,
                added_at=now,
                last_checked=0.0,
                status="manual",
            )
            self._persist_wallets()
            log.info("CopyManager PIN %s (new manual wallet)", w[:12])
            return {"ok": True, "wallet": w, "status": "manual", "created": True}
        prev = st.status
        st.status = "manual"
        self._persist_wallets()
        log.info("CopyManager PIN %s (was %s -> manual)", w[:12], prev)
        return {"ok": True, "wallet": w, "status": "manual", "prev_status": prev}

    def unpin(self, wallet: str) -> dict[str, Any]:
        """Convert a MANUAL wallet back to ACTIVE so it can be auto-pruned."""
        w = str(wallet).strip().lower()
        st = self.state.wallet_stats.get(w)
        if st is None:
            return {"ok": False, "reason": "not_tracked"}
        if st.status != "manual":
            return {"ok": False, "reason": f"not_manual ({st.status})"}
        st.status = "active"
        self._persist_wallets()
        log.info("CopyManager UNPIN %s (manual -> active)", w[:12])
        return {"ok": True, "wallet": w, "status": "active", "prev_status": "manual"}

    def remove(self, wallet: str) -> dict[str, Any]:
        """Remove a wallet entirely from tracking and persist the new list."""
        w = str(wallet).strip().lower()
        st = self.state.wallet_stats.pop(w, None)
        if st is None:
            return {"ok": False, "reason": "not_tracked"}
        self._persist_wallets()
        log.info("CopyManager REMOVE %s (was %s)", w[:12], st.status)
        return {"ok": True, "wallet": w, "removed_from_status": st.status}
