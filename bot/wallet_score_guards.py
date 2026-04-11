"""
Wallet score guard layer: degradation detection, hysteresis / anti-flapping,
provisional caps for sparse data, and suspicious-wallet heuristics.

Sits between the raw wallet_score_v2 output and the final gating decision so
the existing scoring pipeline stays untouched.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScoreSnapshot:
    """Point-in-time wallet score with metadata for history tracking."""
    score: float
    sample_count: int
    epoch: float = field(default_factory=time.time)


@dataclass
class GuardVerdict:
    """Output of the guard layer — wraps the original score with guard flags."""
    original_score: float
    guarded_score: float
    tier: str = "unknown"
    provisional_cap_applied: bool = False
    degradation_flag: bool = False
    degradation_pct: float = 0.0
    hysteresis_held: bool = False
    suspicious: bool = False
    suspicious_reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1) Score tiering
# ---------------------------------------------------------------------------

_DEFAULT_TIER_THRESHOLDS: dict[str, tuple[float, float]] = {
    "elite":  (0.75, 1.0),
    "good":   (0.55, 0.75),
    "medium": (0.35, 0.55),
    "low":    (0.0,  0.35),
}


def score_tier(score: float, thresholds: Optional[dict[str, tuple[float, float]]] = None) -> str:
    t = thresholds or _DEFAULT_TIER_THRESHOLDS
    for tier_name, (lo, hi) in t.items():
        if lo <= score < hi or (tier_name == "elite" and score >= hi - 0.001):
            return tier_name
    return "low"


# ---------------------------------------------------------------------------
# 2) Degradation detection
# ---------------------------------------------------------------------------

def detect_degradation(
    current_score: float,
    history: list[ScoreSnapshot],
    *,
    lookback_window_s: float = 7 * 86400,
    min_drop_pct: float = 20.0,
    now_epoch: Optional[float] = None,
) -> tuple[bool, float]:
    """
    Compare *current_score* against recent historical average.
    Returns (is_degraded, drop_percent).  drop_percent > 0 means score fell.
    """
    if not history:
        return False, 0.0
    now = now_epoch or time.time()
    cutoff = now - lookback_window_s
    recent = [s for s in history if s.epoch >= cutoff]
    if not recent:
        return False, 0.0
    avg_recent = sum(s.score for s in recent) / len(recent)
    if avg_recent <= 0.0:
        return False, 0.0
    drop = (avg_recent - current_score) / avg_recent * 100.0
    return drop >= min_drop_pct, drop


# ---------------------------------------------------------------------------
# 3) Hysteresis / anti-flapping
# ---------------------------------------------------------------------------

def apply_hysteresis(
    current_score: float,
    previous_tier: str,
    *,
    promote_margin: float = 0.05,
    demote_margin: float = 0.05,
    thresholds: Optional[dict[str, tuple[float, float]]] = None,
) -> tuple[str, bool]:
    """
    Tier transition requires score to exceed boundary by *margin*.
    Returns (effective_tier, was_held_back).
    """
    t = thresholds or _DEFAULT_TIER_THRESHOLDS

    natural_tier = score_tier(current_score, t)
    if previous_tier == "unknown" or previous_tier not in t:
        return natural_tier, False

    prev_lo, prev_hi = t[previous_tier]
    nat_lo, nat_hi = t.get(natural_tier, (0.0, 1.0))

    if natural_tier == previous_tier:
        return natural_tier, False

    tier_rank = {name: i for i, name in enumerate(t)}
    prev_rank = tier_rank.get(previous_tier, 99)
    nat_rank = tier_rank.get(natural_tier, 99)

    promoting = nat_rank < prev_rank
    if promoting:
        boundary = prev_hi
        if current_score < boundary + promote_margin:
            return previous_tier, True
    else:
        boundary = prev_lo
        if current_score > boundary - demote_margin:
            return previous_tier, True

    return natural_tier, False


# ---------------------------------------------------------------------------
# 4) Provisional cap for sparse data
# ---------------------------------------------------------------------------

def provisional_score_cap(
    score: float,
    sample_count: int,
    *,
    sparse_threshold: int = 8,
    cap_at_sparse: float = 0.60,
    very_sparse_threshold: int = 4,
    cap_at_very_sparse: float = 0.45,
) -> tuple[float, bool]:
    """
    Prevent new / low-data wallets from receiving high scores.
    Returns (capped_score, was_capped).
    """
    if sample_count <= very_sparse_threshold:
        if score > cap_at_very_sparse:
            return cap_at_very_sparse, True
        return score, False
    if sample_count <= sparse_threshold:
        if score > cap_at_sparse:
            return cap_at_sparse, True
        return score, False
    return score, False


# ---------------------------------------------------------------------------
# 5) Suspicious wallet heuristics
# ---------------------------------------------------------------------------

def check_suspicious(
    candidates: list[Any],
    *,
    min_trades: int = 3,
    wash_trade_price_tolerance: float = 0.005,
    wash_trade_ratio_threshold: float = 0.70,
    concentration_threshold: float = 0.85,
    rapid_fire_window_s: float = 30.0,
    rapid_fire_count: int = 5,
) -> tuple[bool, list[str]]:
    """
    Flag wallets exhibiting suspicious patterns:
      - Wash-trading (many trades at near-identical prices on same token)
      - Over-concentration in single market
      - Rapid-fire micro-bursts
    Returns (is_suspicious, [reason_tags]).
    """
    reasons: list[str] = []
    n = len(candidates)
    if n < min_trades:
        return False, reasons

    # --- Wash-trade detection ---
    from collections import defaultdict
    token_prices: dict[str, list[float]] = defaultdict(list)
    for c in candidates:
        tid = getattr(c, "token_id", None) or ""
        px = getattr(c, "price", 0.0)
        if tid:
            token_prices[tid].append(px)

    wash_pairs = 0
    total_token_trades = 0
    for tid, prices in token_prices.items():
        total_token_trades += len(prices)
        for i in range(len(prices)):
            for j in range(i + 1, len(prices)):
                if abs(prices[i] - prices[j]) <= wash_trade_price_tolerance:
                    wash_pairs += 1

    possible_pairs = max(n * (n - 1) // 2, 1)
    if n >= min_trades and wash_pairs / possible_pairs >= wash_trade_ratio_threshold:
        reasons.append("wash_trade_pattern")

    # --- Over-concentration ---
    market_counts: dict[str, int] = defaultdict(int)
    for c in candidates:
        title = getattr(c, "title", "")[:60]
        market_counts[title] += 1
    if market_counts:
        max_share = max(market_counts.values()) / n
        if max_share >= concentration_threshold and n >= min_trades:
            reasons.append("single_market_concentration")

    # --- Rapid-fire bursts ---
    timestamps = []
    for c in candidates:
        ts = getattr(c, "_epoch", None)
        if ts is not None:
            timestamps.append(ts)
    if len(timestamps) >= rapid_fire_count:
        timestamps.sort()
        for i in range(len(timestamps) - rapid_fire_count + 1):
            window = timestamps[i + rapid_fire_count - 1] - timestamps[i]
            if window <= rapid_fire_window_s:
                reasons.append("rapid_fire_burst")
                break

    return len(reasons) > 0, reasons


# ---------------------------------------------------------------------------
# 6) Combined guard pipeline
# ---------------------------------------------------------------------------

def run_guards(
    score: float,
    sample_count: int,
    candidates: list[Any],
    *,
    history: Optional[list[ScoreSnapshot]] = None,
    previous_tier: str = "unknown",
    settings: Any = None,
    now_epoch: Optional[float] = None,
) -> GuardVerdict:
    """
    Run all wallet-score guard checks and return a single verdict.
    All guards are opt-in via settings flags; if a flag is missing
    or falsy the guard is skipped.
    """
    v = GuardVerdict(original_score=score, guarded_score=score)

    # Read settings (defensive)
    def _flag(name: str, default: bool = False) -> bool:
        val = getattr(settings, name, None)
        if val is None:
            return default
        return bool(val)

    def _fval(name: str, default: float) -> float:
        val = getattr(settings, name, None)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _ival(name: str, default: int) -> int:
        val = getattr(settings, name, None)
        if val is None:
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    guarded = score

    # -- Provisional cap --
    if _flag("wallet_provisional_cap_enabled", False):
        sparse_th = _ival("wallet_sparse_threshold", 8)
        very_sparse_th = _ival("wallet_very_sparse_threshold", 4)
        cap_sparse = _fval("wallet_cap_at_sparse", 0.60)
        cap_very_sparse = _fval("wallet_cap_at_very_sparse", 0.45)
        guarded, was_capped = provisional_score_cap(
            guarded, sample_count,
            sparse_threshold=sparse_th,
            very_sparse_threshold=very_sparse_th,
            cap_at_sparse=cap_sparse,
            cap_at_very_sparse=cap_very_sparse,
        )
        v.provisional_cap_applied = was_capped

    # -- Degradation detection --
    if _flag("wallet_degradation_enabled", False) and history:
        lookback_s = _fval("wallet_degradation_lookback_hours", 168.0) * 3600.0
        min_drop = _fval("wallet_degradation_min_drop_pct", 20.0)
        is_degraded, drop_pct = detect_degradation(
            guarded, history,
            lookback_window_s=lookback_s,
            min_drop_pct=min_drop,
            now_epoch=now_epoch,
        )
        v.degradation_flag = is_degraded
        v.degradation_pct = drop_pct

    # -- Suspicious wallet check --
    if _flag("wallet_suspicious_check_enabled", False):
        is_sus, sus_reasons = check_suspicious(candidates)
        v.suspicious = is_sus
        v.suspicious_reasons = sus_reasons
        if is_sus:
            sus_penalty = _fval("wallet_suspicious_penalty", 0.30)
            guarded = max(guarded - sus_penalty, 0.0)

    # -- Tier assignment --
    raw_tier = score_tier(guarded)

    # -- Hysteresis / anti-flapping --
    if _flag("wallet_hysteresis_enabled", False) and previous_tier != "unknown":
        promote_m = _fval("wallet_hysteresis_promote_margin", 0.05)
        demote_m = _fval("wallet_hysteresis_demote_margin", 0.05)
        effective_tier, held = apply_hysteresis(
            guarded, previous_tier,
            promote_margin=promote_m,
            demote_margin=demote_m,
        )
        v.hysteresis_held = held
        v.tier = effective_tier
    else:
        v.tier = raw_tier

    v.guarded_score = max(min(guarded, 1.0), 0.0)
    v.details = {
        "raw_tier": raw_tier,
        "sample_count": sample_count,
    }
    return v
