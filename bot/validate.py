"""Wallet / key sanity checks before live trading."""

from __future__ import annotations

import re
from typing import Optional

_ADDR = re.compile(r"^0x[a-fA-F0-9]{40}$")
_KEY = re.compile(r"^(0x)?[a-fA-F0-9]{64}$")


def is_valid_polygon_address(addr: str) -> bool:
    return bool(addr and _ADDR.match(addr.strip()))


def is_valid_private_key_hex(key: str) -> bool:
    if not key or "****" in key:
        return False
    return bool(_KEY.match(key.strip()))


def normalize_address(addr: str) -> str:
    a = (addr or "").strip()
    return a.lower() if a.startswith("0x") else a
