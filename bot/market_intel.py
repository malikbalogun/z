"""Resolution timing and other market metadata from Gamma payloads."""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional


def _raw_dict(market: dict[str, Any]) -> dict[str, Any]:
    r = market.get("raw")
    return r if isinstance(r, dict) else market


def hours_until_resolution_end(market: dict[str, Any]) -> Optional[float]:
    """
    Hours until market end / resolution, if parseable from Gamma-style fields.
    Returns None if unknown (gate should not block).
    """
    raw = _raw_dict(market)
    candidates: list[Any] = []
    for k in ("endDate", "end_date_iso", "umaEndDate"):
        v = raw.get(k)
        if v is not None and v != "":
            candidates.append(v)
    now = dt.datetime.now(dt.timezone.utc)
    for v in candidates:
        if isinstance(v, (int, float)) and v > 1e12:
            try:
                end = dt.datetime.fromtimestamp(float(v) / 1000.0, tz=dt.timezone.utc)
                return (end - now).total_seconds() / 3600.0
            except (OSError, OverflowError, ValueError):
                continue
        s = str(v).strip()
        if not s or s.lower() in ("null", "none"):
            continue
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            end = dt.datetime.fromisoformat(s.replace(" ", "T"))
            if end.tzinfo is None:
                end = end.replace(tzinfo=dt.timezone.utc)
            return (end - now).total_seconds() / 3600.0
        except (TypeError, ValueError):
            continue
    return None
