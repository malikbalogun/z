"""CLOB reconciliation: open orders snapshot + refresh recent trade rows from get_order."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from bot.clob_utils import is_filled_status, is_open_status, is_terminal_status, normalize_order_payload

log = logging.getLogger("polymarket.reconcile")


def normalize_open_order(raw: Any) -> dict[str, Any]:
    """Flatten get_orders row for dashboard / storage."""
    if not isinstance(raw, dict):
        return {
            "order_id": None,
            "token_id": None,
            "side": "",
            "price": None,
            "original_size": None,
            "size_matched": None,
            "status": "",
        }
    oid = raw.get("id") or raw.get("orderID") or raw.get("order_id")
    tok = raw.get("asset_id") or raw.get("token_id") or raw.get("tokenId")

    def _f(x: Any) -> Optional[float]:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    st = str(raw.get("status") or raw.get("state") or "").upper()
    return {
        "order_id": str(oid) if oid else None,
        "token_id": str(tok) if tok else None,
        "side": str(raw.get("side") or "").upper(),
        "price": _f(raw.get("price")),
        "original_size": _f(raw.get("original_size") or raw.get("size")),
        "size_matched": _f(raw.get("size_matched") or raw.get("filled_size") or raw.get("filledSize")),
        "status": st,
    }


def canonical_status_from_order_payload(raw: Any) -> str:
    """Map normalized CLOB order to a stable TradeRecord.status vocabulary."""
    norm = normalize_order_payload(raw)
    st = norm["status"]
    sm, osz = norm["size_matched"], norm["original_size"]
    if is_filled_status(st, sm, osz):
        return "filled"
    if is_open_status(st):
        return "open"
    if is_terminal_status(st):
        if st in ("CANCELED", "CANCELLED", "EXPIRED"):
            return "cancelled"
        return "closed"
    return "unknown"


def _rank_status(s: str) -> int:
    """Higher = more terminal / informative for conflict resolution."""
    return {
        "unknown": 0,
        "open": 1,
        "submitted": 2,
        "market_fok": 3,
        "closed": 4,
        "cancelled": 5,
        "filled": 6,
        "dry_run": 7,
    }.get(s, 0)


def merge_trade_status(previous: str, api_status: str) -> Optional[str]:
    """
    If API gives a strictly better-resolved status, return the new one.
    Never downgrade filled -> cancelled from stale local state.
    """
    if api_status == "unknown":
        return None
    if previous == "dry_run":
        return None
    if previous == "filled" and api_status != "filled":
        return None
    if _rank_status(api_status) > _rank_status(previous):
        return api_status
    if api_status == "filled" and previous != "filled":
        return "filled"
    if api_status == "cancelled" and previous in ("open", "submitted", "unknown"):
        return "cancelled"
    if api_status == "open" and previous == "submitted":
        return "open"
    return None


def snapshot_open_orders(clob: Any, *, display_limit: int = 40) -> list[dict[str, Any]]:
    """Blocking: fetch all open orders via client, return newest-first slice for UI."""
    rows = clob.get_orders()
    if not isinstance(rows, list):
        return []
    norm = [normalize_open_order(r) for r in rows]
    norm = [x for x in norm if x.get("order_id")]
    norm.sort(key=lambda x: str(x.get("order_id") or ""), reverse=True)
    return norm[:display_limit]


def reconcile_trade_records_inplace(
    clob: Any,
    records: list[Any],
    *,
    depth: int = 15,
    sleep_between_s: float = 0.06,
) -> int:
    """
    Blocking: poll get_order for last `depth` records; mutates .status and .reconcile_note.
    Skips dry-run ids. Returns count of rows updated.
    """
    if depth <= 0:
        return 0
    slice_ = records[-depth:] if len(records) > depth else records
    updated = 0
    for rec in slice_:
        oid = getattr(rec, "order_id", "") or ""
        if not oid or oid == "none" or oid.startswith("dry_"):
            continue
        st0 = getattr(rec, "status", "")
        if st0 == "dry_run":
            continue
        try:
            raw = clob.get_order(oid)
        except Exception as e:
            log.debug("get_order %s: %s", oid[:16], e)
            time.sleep(sleep_between_s)
            continue
        api_st = canonical_status_from_order_payload(raw)
        merged = merge_trade_status(st0, api_st)
        if merged and merged != st0:
            rec.status = merged
            rec.reconcile_note = f"clob:{api_st}"
            updated += 1
        time.sleep(sleep_between_s)
    return updated
