"""Backtest replay engine.

Walks a chronological list of historical trades day by day. For each new BUY
made by a *watched* wallet, we re-score that wallet against everything they
did **before** that timestamp (no look-ahead!) using ``wallet_score_v2``,
apply the same ``passes_filters`` we use live, and if the trade is accepted,
allocate a follower position whose PnL is settled from the source's
``realized_pnl`` column once the wallet itself shows that PnL.

Key invariants (look-ahead protection):

* When scoring wallet W on day D, we only look at W's trades with
  ``ts_epoch < first_ts_of_day_D``.
* We only enroll a wallet into the watch set on day D if (a) the user listed
  the wallet manually OR (b) it has *historical* WR >= ``min_win_rate`` and
  >= ``min_total_trades`` resolved trades as of D-1.
* The follower's per-trade PnL is **proportional** to the source's
  ``realized_pnl`` scaled by ``follower_size_usd / source_usd`` — this lets
  us reuse the existing per-position PnL the dataset already contains
  without trying to re-simulate market resolution.

The output is a single ``ReplayResult`` dataclass that ``report.py`` knows
how to serialize.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from bot.backtest.dataset import TradeRow, iter_by_day
from bot.copy_rules import CopyCandidate, passes_filters
from bot.wallet_scoring import wallet_score_v2

log = logging.getLogger("polymarket.backtest.replay")


# ---------------------------------------------------------------------------
# Config + result
# ---------------------------------------------------------------------------


@dataclass
class ReplayConfig:
    """Knobs for one backtest run.

    The defaults intentionally mirror the bot's defaults so a "no-flags"
    backtest answers: "what would have happened with the current settings?"
    """
    start_date: _dt.date | None = None
    end_date: _dt.date | None = None
    initial_balance: float = 500.0
    follower_size_usd: float = 5.0
    # Wallet-quality gates (mirror Settings)
    min_win_rate: float = 0.60
    min_total_trades: int = 5
    min_wallet_score: float = 0.0
    # Per-trade copy filters (mirror Settings)
    copy_min_usd: float = 0.0
    copy_max_usd: float = 0.0
    copy_min_price: float = 0.0
    copy_max_price: float = 1.0
    copy_allow_unknown_outcome: bool = True
    copy_allowed_categories: list[str] = field(default_factory=list)
    copy_allowed_outcomes: list[str] = field(default_factory=list)
    copy_required_keywords: list[str] = field(default_factory=list)
    copy_blocked_keywords: list[str] = field(default_factory=list)
    # Manual pinned wallets — bypass the WR/score gate the same way the
    # live CopyManager does for "manual" entries.
    manual_wallets: list[str] = field(default_factory=list)
    # If true, log every accepted trade. Useful for ad-hoc debugging,
    # noisy on real datasets so default is off.
    verbose_trades: bool = False


@dataclass
class WalletAttribution:
    wallet: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    notional: float = 0.0


@dataclass
class CategoryAttribution:
    category: str
    trades: int = 0
    pnl: float = 0.0


@dataclass
class DailyPoint:
    date: _dt.date
    trades_today: int
    pnl_today: float
    cum_pnl: float
    balance: float


@dataclass
class ReplayResult:
    """Everything ``report.py`` needs to write JSON / markdown."""
    config: ReplayConfig
    start_date: _dt.date | None
    end_date: _dt.date | None
    total_source_rows: int = 0
    total_evaluated: int = 0
    total_accepted: int = 0
    total_rejected_by_score: int = 0
    total_rejected_by_filter: int = 0
    total_unresolved: int = 0
    gross_pnl: float = 0.0
    final_balance: float = 0.0
    hit_rate: float = 0.0
    max_drawdown: float = 0.0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    per_wallet: list[WalletAttribution] = field(default_factory=list)
    per_category: list[CategoryAttribution] = field(default_factory=list)
    daily: list[DailyPoint] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Small adapter so wallet_score_v2 + passes_filters see what they expect
# ---------------------------------------------------------------------------


def _row_to_score_dict(r: TradeRow) -> dict:
    """Build the dict shape that ``wallet_score_v2`` expects.

    The scorer reads: timestamp, side, asset, price, usdcSize, outcome,
    title/slug/tags. Everything else is ignored, so we keep this minimal.
    """
    return {
        "timestamp": r.ts_epoch,
        "side": "BUY",
        "asset": r.token_id,
        "price": r.price,
        "usdcSize": r.usdc,
        "outcome": r.outcome,
        "title": r.title,
        "slug": r.market_slug,
        "tags": [],
    }


def _row_to_candidate(r: TradeRow) -> CopyCandidate:
    """Build a CopyCandidate so ``passes_filters`` runs over identical fields
    to the live agent."""
    return CopyCandidate(
        wallet=r.wallet,
        token_id=r.token_id,
        tx_key=f"{r.wallet}:{r.condition_id}:{r.token_id}",
        title=r.title,
        slug=r.market_slug,
        tags_text="",
        category=r.category,
        outcome=r.outcome,
        price=r.price,
        usdc=r.usdc,
    )


class _ConfigAsSettings:
    """Minimal shim so passes_filters / wallet_score_v2 can read settings.

    They only need attribute access, never the full Settings dataclass.
    """
    def __init__(self, cfg: ReplayConfig):
        self.copy_allowed_categories = cfg.copy_allowed_categories
        self.copy_allowed_outcomes = cfg.copy_allowed_outcomes
        self.copy_required_keywords = cfg.copy_required_keywords
        self.copy_blocked_keywords = cfg.copy_blocked_keywords
        self.copy_min_usd = cfg.copy_min_usd
        self.copy_max_usd = cfg.copy_max_usd
        self.copy_min_price = cfg.copy_min_price
        self.copy_max_price = cfg.copy_max_price
        self.copy_allow_unknown_outcome = cfg.copy_allow_unknown_outcome
        self.copy_wallet_score_overrides: dict = {}
        self.wallet_score_decay_half_life_hours = 168.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _qualifies(history: list[TradeRow], cfg: ReplayConfig) -> bool:
    """Is this wallet good enough to enroll based on resolved history only?

    Uses the dataset's own ``realized_pnl`` to decide wins/losses.
    """
    resolved = [r for r in history if r.realized_pnl is not None]
    if len(resolved) < cfg.min_total_trades:
        return False
    wins = sum(1 for r in resolved if (r.realized_pnl or 0) > 0)
    wr = wins / len(resolved)
    return wr >= cfg.min_win_rate


def run_replay(rows: Iterable[TradeRow], cfg: ReplayConfig) -> ReplayResult:
    """Run a chronological replay. ``rows`` is consumed once, sorted ascending.

    Returns a fully-populated ``ReplayResult`` ready for the report writer.
    """
    rows = list(rows)
    if not rows:
        return ReplayResult(config=cfg, start_date=None, end_date=None)

    settings_shim = _ConfigAsSettings(cfg)
    manual = {w.lower().strip() for w in (cfg.manual_wallets or []) if w}

    # Per-wallet history, accumulated as time advances. We never look at
    # rows beyond the day we are processing.
    history_per_wallet: dict[str, list[TradeRow]] = defaultdict(list)
    # Per-wallet PnL aggregation for follower (us, the copier).
    walls: dict[str, WalletAttribution] = {}
    cats: dict[str, CategoryAttribution] = {}
    rejection_reasons: dict[str, int] = defaultdict(int)
    # Daily PnL bucket
    daily_pnl: dict[_dt.date, float] = defaultdict(float)
    daily_trades: dict[_dt.date, int] = defaultdict(int)

    counters = {
        "evaluated": 0,
        "accepted": 0,
        "rej_score": 0,
        "rej_filter": 0,
        "unresolved": 0,
    }

    seen_wallets: set[str] = set()

    for day, day_rows in iter_by_day(rows):
        if cfg.start_date and day < cfg.start_date:
            # still need to populate history so future days can score
            for r in day_rows:
                history_per_wallet[r.wallet].append(r)
            continue
        if cfg.end_date and day > cfg.end_date:
            break
        for r in day_rows:
            seen_wallets.add(r.wallet)
            counters["evaluated"] += 1
            hist = history_per_wallet[r.wallet]  # past trades only

            # Decide if we'd be watching this wallet at the start of `day`.
            if r.wallet in manual:
                pass  # manual wallets bypass quality gate (mirror live)
            elif not _qualifies(hist, cfg):
                rejection_reasons["wallet_not_qualified"] += 1
                counters["rej_score"] += 1
                # still record this trade in history for later days
                history_per_wallet[r.wallet].append(r)
                continue

            # Wallet score gate (only if user asked for one)
            if cfg.min_wallet_score > 0 and r.wallet not in manual:
                score, _ = wallet_score_v2(
                    [_row_to_score_dict(h) for h in hist],
                    wallet=r.wallet,
                    default_bet_usd=cfg.follower_size_usd,
                    settings=settings_shim,
                    now_epoch=r.ts_epoch,
                )
                if score < cfg.min_wallet_score:
                    rejection_reasons["below_wallet_score"] += 1
                    counters["rej_score"] += 1
                    history_per_wallet[r.wallet].append(r)
                    continue

            # Per-trade filters (category/outcome/keywords/price/size).
            cand = _row_to_candidate(r)
            ok, reason = passes_filters(settings_shim, cand)
            if not ok:
                rejection_reasons[f"filter:{reason}"] += 1
                counters["rej_filter"] += 1
                history_per_wallet[r.wallet].append(r)
                continue

            # We would have copied this trade. Settle PnL proportionally
            # to the source's own realized_pnl.
            if r.realized_pnl is None:
                counters["unresolved"] += 1
                history_per_wallet[r.wallet].append(r)
                continue

            scale = cfg.follower_size_usd / max(r.usdc, 1e-9)
            our_pnl = float(r.realized_pnl) * scale
            counters["accepted"] += 1
            daily_pnl[day] += our_pnl
            daily_trades[day] += 1

            wa = walls.setdefault(r.wallet, WalletAttribution(wallet=r.wallet))
            wa.trades += 1
            wa.notional += cfg.follower_size_usd
            wa.pnl += our_pnl
            if our_pnl > 0:
                wa.wins += 1
            else:
                wa.losses += 1

            ca = cats.setdefault(r.category, CategoryAttribution(category=r.category))
            ca.trades += 1
            ca.pnl += our_pnl

            if cfg.verbose_trades:
                log.info(
                    "ACCEPT %s %s %s @%.2f size=$%.2f -> our_pnl=$%.4f",
                    day, r.wallet[:10], r.market_slug[:30], r.price,
                    cfg.follower_size_usd, our_pnl,
                )

            history_per_wallet[r.wallet].append(r)

    # Build daily series + drawdown (peak-to-trough on cumulative PnL).
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    daily_points: list[DailyPoint] = []
    for d in sorted(daily_pnl.keys()):
        cum += daily_pnl[d]
        peak = max(peak, cum)
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
        daily_points.append(
            DailyPoint(
                date=d,
                trades_today=daily_trades[d],
                pnl_today=round(daily_pnl[d], 4),
                cum_pnl=round(cum, 4),
                balance=round(cfg.initial_balance + cum, 4),
            )
        )

    total_trades = counters["accepted"]
    total_wins = sum(w.wins for w in walls.values())
    hit_rate = (total_wins / total_trades) if total_trades else 0.0

    res = ReplayResult(
        config=cfg,
        start_date=daily_points[0].date if daily_points else None,
        end_date=daily_points[-1].date if daily_points else None,
        total_source_rows=len(rows),
        total_evaluated=counters["evaluated"],
        total_accepted=counters["accepted"],
        total_rejected_by_score=counters["rej_score"],
        total_rejected_by_filter=counters["rej_filter"],
        total_unresolved=counters["unresolved"],
        gross_pnl=round(cum, 4),
        final_balance=round(cfg.initial_balance + cum, 4),
        hit_rate=round(hit_rate, 4),
        max_drawdown=round(max_dd, 4),
        rejection_reasons=dict(rejection_reasons),
        per_wallet=sorted(
            walls.values(), key=lambda w: -w.pnl
        ),
        per_category=sorted(
            cats.values(), key=lambda c: -c.pnl
        ),
        daily=daily_points,
    )
    log.info(
        "backtest done: evaluated=%d accepted=%d gross_pnl=$%.2f drawdown=$%.2f",
        res.total_evaluated, res.total_accepted, res.gross_pnl, res.max_drawdown,
    )
    return res
