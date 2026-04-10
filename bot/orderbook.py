"""Order-book depth check (assistant-tool style): bid vs ask notional imbalance for BUY."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("polymarket.orderbook")


def best_bid_ask(clob: Any, token_id: str) -> tuple[float | None, float | None]:
    """
    Best bid (highest) and best ask (lowest) from CLOB book. On failure returns (None, None).
    """
    try:
        book = clob.get_order_book(token_id)
    except Exception as e:
        log.debug("get_order_book %s: %s", token_id[:12], e)
        return None, None
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    best_b: float | None = None
    best_a: float | None = None
    for lv in bids:
        try:
            p = float(getattr(lv, "price", None) or 0)
            if p <= 0:
                continue
            best_b = p if best_b is None else max(best_b, p)
        except (TypeError, ValueError):
            continue
    for lv in asks:
        try:
            p = float(getattr(lv, "price", None) or 0)
            if p <= 0:
                continue
            best_a = p if best_a is None else min(best_a, p)
        except (TypeError, ValueError):
            continue
    return best_b, best_a


def spread_mid_bps(clob: Any, token_id: str) -> float | None:
    """
    (ask - bid) / mid in bps where mid = (bid+ask)/2. None if book incomplete.
    """
    bid, ask = best_bid_ask(clob, token_id)
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 1e-9:
        return None
    return (ask - bid) / mid * 10000.0


def _sum_notional(levels: list[Any] | None) -> float:
    if not levels:
        return 0.0
    s = 0.0
    for lv in levels:
        try:
            p = float(getattr(lv, "price", None) or 0)
            sz = float(getattr(lv, "size", None) or 0)
            s += abs(p * sz)
        except (TypeError, ValueError):
            continue
    return s


def orderbook_buy_depth_ok(clob: Any, token_id: str, min_bid_share: float) -> bool:
    """
    For BUY support: require bid notional / (bid+ask) >= min_bid_share.
    If the book is empty or the call fails, return True (do not block on API flake).
    """
    try:
        book = clob.get_order_book(token_id)
    except Exception as e:
        log.debug("get_order_book %s: %s", token_id[:12], e)
        return True
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    b = _sum_notional(bids)
    a = _sum_notional(asks)
    if b + a < 1e-9:
        return True
    share = b / (b + a)
    return share >= float(min_bid_share)
