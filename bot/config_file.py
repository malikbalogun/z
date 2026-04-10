"""Bootstrap config from `config.json` in project root (no .env required)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("polymarket.config")

_DEFAULTS: dict[str, Any] = {
    "database_url": "sqlite:///./data/app.db",
    "session_secret": "dev-only-change-me-32chars-minimum!!",
    "upload_dir": "./data/uploads",
    "initial_admin_username": "admin",
    "initial_admin_password": "changeme",
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return project_root() / "config.json"


def load_config() -> dict[str, Any]:
    p = config_path()
    out = dict(_DEFAULTS)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.update({k: v for k, v in data.items() if v is not None})
        except Exception as e:
            log.warning("config.json read failed: %s", e)
    return out


def ensure_upload_dir(cfg: dict[str, Any]) -> Path:
    d = Path(cfg.get("upload_dir") or "./data/uploads")
    if not d.is_absolute():
        d = project_root() / d
    d.mkdir(parents=True, exist_ok=True)
    return d
