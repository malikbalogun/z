"""Signed session cookies (itsdangerous) — no JWT dependency."""

from __future__ import annotations

from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def issue_token(secret: str, payload: dict[str, Any], salt: str = "pm-auth") -> str:
    return URLSafeTimedSerializer(secret, salt=salt).dumps(payload)


def parse_token(secret: str, token: str, max_age: int = 86400 * 14, salt: str = "pm-auth") -> dict[str, Any]:
    return URLSafeTimedSerializer(secret, salt=salt).loads(token, max_age=max_age)


def safe_parse(secret: str, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return parse_token(secret, token)
    except (BadSignature, SignatureExpired):
        return None
