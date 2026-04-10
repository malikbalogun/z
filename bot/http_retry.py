"""Small httpx retry helpers for flaky public / semi-public APIs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger("polymarket.http")

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


async def get_json_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    attempts: int = 3,
) -> Any:
    """GET and return JSON; retries on 429/5xx and transient transport errors."""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            r = await client.get(url, params=params)
            if r.status_code in _RETRYABLE_STATUS:
                last_err = httpx.HTTPStatusError(
                    f"retryable {r.status_code}",
                    request=r.request,
                    response=r,
                )
                if i + 1 < attempts:
                    await asyncio.sleep(0.35 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException as e:
            last_err = e
            if i + 1 < attempts:
                await asyncio.sleep(0.35 * (i + 1))
                continue
        except httpx.TransportError as e:
            last_err = e
            if i + 1 < attempts:
                await asyncio.sleep(0.35 * (i + 1))
                continue
        except httpx.HTTPStatusError as e:
            sc = e.response.status_code if e.response is not None else 0
            if sc in _RETRYABLE_STATUS and i + 1 < attempts:
                last_err = e
                await asyncio.sleep(0.35 * (i + 1))
                continue
            raise
    log.debug("get_json_retry exhausted %s", url[:72])
    if last_err:
        raise last_err
    raise RuntimeError("get_json_retry: no response")
