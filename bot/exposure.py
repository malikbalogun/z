"""Rolling notional and per-condition exposure for risk caps."""

from __future__ import annotations

import datetime as dt
from typing import Any


def rolling_notional_usd(trades: list[Any], *, hours: float = 24.0) -> float:
    """Sum cost_usd for recent trades (excludes obvious cancels)."""
    if hours <= 0:
        return 0.0
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    total = 0.0
    for t in trades:
        try:
            ts_raw = str(getattr(t, "timestamp", "") or "")
            if ts_raw.endswith("Z"):
                ts_raw = ts_raw[:-1] + "+00:00"
            ts = dt.datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
        if ts < cutoff:
            continue
        st = str(getattr(t, "status", "") or "").lower()
        if st in ("cancelled", "canceled"):
            continue
        try:
            total += float(getattr(t, "cost_usd", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def condition_exposure_usd(
    condition_id: str,
    *,
    positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
) -> float:
    """Mark-to-market position value + open BUY notionals for this condition."""
    cid = (condition_id or "").strip()
    if not cid:
        return 0.0
    u = 0.0
    for p in positions:
        if str(p.get("condition_id") or "") != cid:
            continue
        try:
            u += float(p.get("value", 0) or 0)
        except (TypeError, ValueError):
            continue
    for o in open_orders:
        if str(o.get("condition_id") or "") != cid:
            continue
        try:
            px = float(o.get("price") or 0)
            sz = float(o.get("original_size") or 0)
            rem = sz - float(o.get("size_matched") or 0)
            if rem < 0:
                rem = 0.0
            side = str(o.get("side") or "").upper()
            if side == "BUY":
                u += px * rem
        except (TypeError, ValueError):
            continue
    return u


def category_exposure_usd(
    category: str,
    *,
    positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    categories_by_condition: dict[str, str],
) -> float:
    """Exposure for a market category using condition_id -> category mapping."""
    cat = (category or "").strip().lower()
    if not cat:
        return 0.0
    u = 0.0
    for p in positions:
        cid = str(p.get("condition_id") or "")
        if categories_by_condition.get(cid, "").lower() != cat:
            continue
        try:
            u += float(p.get("value", 0) or 0)
        except (TypeError, ValueError):
            continue
    for o in open_orders:
        cid = str(o.get("condition_id") or "")
        if categories_by_condition.get(cid, "").lower() != cat:
            continue
        try:
            px = float(o.get("price") or 0)
            sz = float(o.get("original_size") or 0)
            rem = sz - float(o.get("size_matched") or 0)
            if rem < 0:
                rem = 0.0
            side = str(o.get("side") or "").upper()
            if side == "BUY":
                u += px * rem
        except (TypeError, ValueError):
            continue
    return u
