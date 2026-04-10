"""CLOB execution: strict GTD limits with robust polling and cancel."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional, Tuple

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.exceptions import PolyApiException
from py_clob_client.order_builder.constants import BUY, SELL

from bot.clob_utils import is_filled_status, is_open_status, is_terminal_status, normalize_order_payload

log = logging.getLogger("polymarket.execution")


def _extract_post_order_id(resp: Any) -> Optional[str]:
    if not isinstance(resp, dict):
        return None
    for k in ("orderID", "order_id", "orderId", "id"):
        v = resp.get(k)
        if v:
            return str(v)
    o = resp.get("order")
    if isinstance(o, dict):
        for k in ("orderID", "order_id", "id"):
            v = o.get(k)
            if v:
                return str(v)
    return None


def _poll_order_state(client: Any, oid: str) -> tuple[str, str]:
    """
    Returns (kind, detail) where kind is:
      filled | terminal | open | poll_error
    """
    try:
        info = client.get_order(oid)
    except Exception as e:
        log.warning("get_order %s: %s", oid, e)
        return "poll_error", str(e)

    norm = normalize_order_payload(info)
    st = norm["status"]
    sm, osz = norm["size_matched"], norm["original_size"]

    if is_filled_status(st, sm, osz):
        return "filled", st or "FILLED"
    if is_terminal_status(st) and not is_filled_status(st, sm, osz):
        return "terminal", st or "DONE"
    if st and not is_open_status(st) and not is_filled_status(st, sm, osz):
        return "terminal", st or "DONE"
    return "open", st or "LIVE"


async def place_limit_gtd_then_wait(
    client: Any,
    *,
    token_id: str,
    side: str,
    price: float,
    size: float,
    ttl_seconds: int,
    poll_seconds: float,
    dry_run: bool,
) -> Tuple[Optional[str], str]:
    """
    Post GTD limit; poll until filled / terminal / TTL; cancel if still open.
    Returns (order_id_or_none, note).
    """
    if dry_run:
        log.info(
            "[DRY RUN] limit BUY size=%.4f @ %.4f token=%s…",
            size,
            price,
            token_id[:12],
        )
        return f"dry_{int(time.time())}", "dry_run"

    fee_bps = 0
    try:
        fee_bps = int(client.get_fee_rate_bps(token_id))
    except Exception:
        pass

    exp = int(time.time()) + max(15, int(ttl_seconds))
    order_side = BUY if side.upper() == "BUY" else SELL

    args = OrderArgs(
        token_id=token_id,
        price=price,
        size=size,
        side=order_side,
        fee_rate_bps=fee_bps,
        expiration=exp,
    )

    try:
        signed = client.create_order(args)
    except PolyApiException as e:
        log.warning("create_order PolyApiException: %s", e)
        return None, f"create_failed:poly_api:{e.status_code}:{e.error_msg}"
    except Exception as e:
        log.exception("create_order failed")
        return None, f"create_failed:{e}"

    try:
        resp = client.post_order(signed, OrderType.GTD)
    except PolyApiException as e:
        log.warning("post_order PolyApiException: %s", e)
        return None, f"post_failed:poly_api:{e.status_code}:{e.error_msg}"
    except Exception as e:
        log.exception("post_order failed")
        return None, f"post_failed:{e}"

    oid = _extract_post_order_id(resp)
    if not oid:
        log.error("post_order missing id: %s", resp)
        return None, "post_failed:no_order_id"

    kind0, detail0 = await asyncio.to_thread(_poll_order_state, client, oid)
    if kind0 == "filled":
        return oid, f"filled:{detail0}"
    if kind0 == "terminal":
        return oid, f"closed:{detail0}"

    deadline = time.monotonic() + float(ttl_seconds)
    poll_s = max(0.25, float(poll_seconds))

    while time.monotonic() < deadline:
        kind, detail = await asyncio.to_thread(_poll_order_state, client, oid)
        if kind == "filled":
            return oid, f"filled:{detail}"
        if kind == "terminal":
            return oid, f"closed:{detail}"
        if kind == "poll_error":
            await asyncio.sleep(poll_s)
            continue
        await asyncio.sleep(poll_s)

    try:
        await asyncio.to_thread(client.cancel, oid)
        log.info("Cancelled order %s after TTL", oid)
    except Exception as e:
        log.warning("cancel failed %s: %s", oid, e)
        # verify whether it filled or expired while we tried
        kind, detail = await asyncio.to_thread(_poll_order_state, client, oid)
        if kind == "filled":
            return oid, f"filled:{detail}"
        return oid, f"cancel_error:{e}"

    kind, detail = await asyncio.to_thread(_poll_order_state, client, oid)
    if kind == "filled":
        return oid, f"filled:{detail}"
    if kind == "terminal":
        return oid, f"cancelled_or_terminal:{detail}"
    return oid, "cancelled_ttl"


async def place_market_fok_fallback(
    client: Any,
    *,
    token_id: str,
    side: str,
    amount_usd: float,
    dry_run: bool,
) -> Tuple[Optional[str], str]:
    if dry_run:
        return f"dry_mkt_{int(time.time())}", "dry_run"

    from py_clob_client.clob_types import MarketOrderArgs

    order_side = BUY if side.upper() == "BUY" else SELL
    mo = MarketOrderArgs(token_id=token_id, amount=amount_usd, side=order_side)
    try:
        signed = client.create_market_order(mo)
        resp = client.post_order(signed, OrderType.FOK)
    except PolyApiException as e:
        return None, f"market_fok_failed:poly_api:{e.status_code}:{e.error_msg}"
    except Exception as e:
        return None, f"market_fok_failed:{e}"
    oid = _extract_post_order_id(resp)
    return oid, "market_fok"
