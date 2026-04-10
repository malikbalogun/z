"""Normalize CLOB API payloads — Polymarket responses vary by endpoint/version."""

from __future__ import annotations

from typing import Any, Optional


def parse_midpoint(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        for k in ("mid", "price", "p"):
            v = raw.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
    return None


def normalize_order_payload(raw: Any) -> dict[str, Any]:
    """
    Flatten get_order / post_order response into:
      status (upper str), size_matched, original_size, order_id
    """
    if not isinstance(raw, dict):
        return {"status": "", "size_matched": None, "original_size": None, "order_id": None}

    order = raw.get("order")
    src = order if isinstance(order, dict) else raw

    status = (
        src.get("status")
        or src.get("state")
        or raw.get("status")
        or raw.get("state")
        or ""
    )
    status = str(status).upper()

    def _f(x: Any) -> Optional[float]:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    sm = _f(
        src.get("size_matched")
        or src.get("filled_size")
        or src.get("filledSize")
        or src.get("matched")
        or raw.get("size_matched")
    )
    osz = _f(
        src.get("original_size")
        or src.get("size")
        or src.get("originalSize")
        or raw.get("original_size")
        or raw.get("size")
    )

    oid = (
        src.get("orderID")
        or src.get("order_id")
        or src.get("id")
        or raw.get("orderID")
        or raw.get("order_id")
    )

    return {
        "status": status,
        "size_matched": sm,
        "original_size": osz,
        "order_id": str(oid) if oid else None,
    }


def is_terminal_status(status: str) -> bool:
    s = status.upper()
    return s in (
        "FILLED",
        "MATCHED",
        "EXECUTED",
        "CANCELED",
        "CANCELLED",
        "EXPIRED",
        "REJECTED",
        "FAILED",
    )


def is_filled_status(status: str, size_matched: Optional[float], original_size: Optional[float]) -> bool:
    s = status.upper()
    if s in ("FILLED", "MATCHED", "EXECUTED"):
        return True
    if size_matched is not None and original_size is not None and original_size > 0:
        if size_matched >= original_size * 0.999:
            return True
    return False


def is_open_status(status: str) -> bool:
    s = status.upper()
    return s in ("LIVE", "OPEN", "PENDING", "ACTIVE", "UNMATCHED", "PARTIAL")
