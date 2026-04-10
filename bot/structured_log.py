"""One-line JSON-ish events for grep / log pipelines (optional)."""

from __future__ import annotations

import json
import logging
from typing import Any


def slog(logger: logging.Logger, enabled: bool, event: str, **fields: Any) -> None:
    if not enabled:
        if fields:
            logger.info("%s %s", event, fields)
        else:
            logger.info("%s", event)
        return
    payload: dict[str, Any] = {"event": event}
    for k, v in fields.items():
        if v is not None:
            payload[k] = v
    logger.info("SLOG %s", json.dumps(payload, default=str, ensure_ascii=False))
