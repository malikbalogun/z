"""FastAPI dependencies: DB session, current user, admin guard."""

from __future__ import annotations

from typing import Annotated, Generator

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from bot.auth_session import safe_parse
from bot.config_file import load_config
from bot.db.bootstrap import verify_password
from bot.db.models import SessionLocal, User


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    pm_session: str | None = Cookie(default=None),
) -> User:
    cfg = load_config()
    secret = str(cfg.get("session_secret") or "")
    if len(secret) < 16:
        raise HTTPException(status_code=503, detail="session_secret too short in config.json")
    data = safe_parse(secret, pm_session)
    if not data or "uid" not in data:
        raise HTTPException(status_code=401, detail="Not authenticated")
    u = db.get(User, int(data["uid"]))
    if not u or not u.is_active:
        raise HTTPException(status_code=401, detail="Invalid session")
    return u


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if (user.role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def verify_user_password(db: Session, username: str, password: str) -> User | None:
    from sqlalchemy import select

    row = db.execute(select(User).where(User.username == username.lower())).scalar_one_or_none()
    if not row or not row.is_active:
        return None
    if verify_password(password, row.password_hash):
        return row
    return None
