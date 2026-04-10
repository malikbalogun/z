"""Admin + auth JSON API (mounted from server)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.auth_session import issue_token
from bot.config_file import load_config, project_root
from bot.db.bootstrap import hash_password
from bot.db.kv import upsert_many_kv
from bot.db.models import ArticleSignal, User
from bot.settings import Settings, default_kv_seed
from bot.wallet_trades import fetch_wallet_trades
from bot.web.deps import get_current_user, get_db, require_admin, verify_user_password

router = APIRouter(tags=["auth-admin"])


def _trader(request: Request):
    t = getattr(request.app.state, "trader", None)
    return t


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
def api_login(body: LoginBody, request: Request, db: Annotated[Session, Depends(get_db)]):
    u = verify_user_password(db, body.username.strip(), body.password)
    if not u:
        raise HTTPException(status_code=401, detail="Bad credentials")
    cfg = load_config()
    secret = str(cfg.get("session_secret") or "")
    token = issue_token(secret, {"uid": u.id, "role": u.role})
    resp = {"ok": True, "username": u.username, "role": u.role}
    from fastapi.responses import JSONResponse

    r = JSONResponse(resp)
    r.set_cookie(
        key="pm_session",
        value=token,
        httponly=True,
        max_age=86400 * 14,
        samesite="lax",
        path="/",
    )
    return r


@router.post("/api/auth/logout")
def api_logout():
    from fastapi.responses import JSONResponse

    r = JSONResponse({"ok": True})
    r.delete_cookie("pm_session", path="/")
    return r


@router.get("/api/me")
def api_me(user: Annotated[User, Depends(get_current_user)]):
    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/api/admin/users")
def admin_list_users(_: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(select(User).order_by(User.id)).scalars().all()
    return [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active} for u in rows]


class CreateUserBody(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: str = "user"


@router.post("/api/admin/users")
def admin_create_user(
    body: CreateUserBody,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    un = body.username.strip().lower()
    if db.scalars(select(User).where(User.username == un)).first():
        raise HTTPException(status_code=400, detail="Username exists")
    role = body.role if body.role in ("admin", "user") else "user"
    u = User(username=un, password_hash=hash_password(body.password), role=role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"id": u.id, "username": u.username, "role": u.role}


class PatchUserBody(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


@router.patch("/api/admin/users/{user_id}")
def admin_patch_user(
    user_id: int,
    body: PatchUserBody,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if user_id == admin.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404)
    if body.role in ("admin", "user"):
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.password:
        u.password_hash = hash_password(body.password)
    db.commit()
    return {"ok": True}


@router.get("/api/admin/settings")
def admin_get_settings(_: Annotated[User, Depends(require_admin)]):
    from bot.db.kv import load_all_kv

    kv = load_all_kv()
    # Mask private key for response (show prefix only)
    out = dict(kv)
    pk = out.get("polymarket_private_key") or ""
    if len(pk) > 12:
        out["polymarket_private_key"] = pk[:6] + "…" + pk[-4:]
    return {"settings": out, "defaults": default_kv_seed()}


class SettingsPatch(BaseModel):
    settings: dict[str, Any]


@router.post("/api/admin/settings")
def admin_save_settings(
    body: SettingsPatch,
    _: Annotated[User, Depends(require_admin)],
):
    upsert_many_kv(body.settings)
    return {"ok": True}


@router.get("/api/admin/signals")
def admin_list_signals(_: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(select(ArticleSignal).order_by(ArticleSignal.id.desc()).limit(200)).scalars().all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "summary": r.summary,
            "source_url": r.source_url,
            "image_path": r.image_path,
            "keywords": json.loads(r.keywords or "[]"),
            "sentiment": r.sentiment,
            "weight": r.weight,
            "active": r.active,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rows
    ]


@router.post("/api/admin/signals")
async def admin_create_signal(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    title: str = Form(...),
    summary: str = Form(""),
    source_url: str = Form(""),
    keywords: str = Form("[]"),
    sentiment: float = Form(0.0),
    weight: float = Form(1.0),
    image: UploadFile | None = File(None),
):
    cfg = load_config()
    upload_dir = Path(cfg.get("upload_dir") or "./data/uploads")
    if not upload_dir.is_absolute():
        upload_dir = project_root() / upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    img_path = ""
    if image and image.filename:
        safe = "".join(c for c in image.filename if c.isalnum() or c in "._-")[:120]
        dest = upload_dir / safe
        with dest.open("wb") as f:
            shutil.copyfileobj(image.file, f)
        img_path = str(dest.relative_to(project_root()))

    try:
        json.loads(keywords)
    except json.JSONDecodeError:
        keywords = "[]"

    sig = ArticleSignal(
        title=title[:512],
        summary=summary,
        source_url=source_url[:1024],
        image_path=img_path[:1024],
        keywords=keywords,
        sentiment=float(sentiment),
        weight=float(weight),
        active=True,
    )
    db.add(sig)
    db.commit()
    db.refresh(sig)
    return {"id": sig.id}


@router.get("/api/admin/wallet-trades")
async def admin_wallet_trades(
    wallet: str,
    request: Request,
    _: Annotated[User, Depends(require_admin)],
):
    """Recent trades for any wallet (collectmarkets2-style raw pull)."""
    bot = _trader(request)
    if not bot or not getattr(bot, "_http", None):
        raise HTTPException(status_code=503, detail="Trader HTTP client not ready")
    rows = await fetch_wallet_trades(bot._http, wallet, limit=120)
    return {"wallet": wallet.lower().strip(), "count": len(rows), "trades": rows[:40]}


@router.post("/api/admin/reload-settings")
def admin_reload_settings(
    request: Request,
    _: Annotated[User, Depends(require_admin)],
):
    """Hot-reload in-memory Settings on the bot (next cycle also reloads)."""
    bot = _trader(request)
    if not bot:
        raise HTTPException(status_code=503)
    try:
        bot.settings = Settings.load()
        bot._value_agent.settings = bot.settings  # type: ignore[attr-defined]
        bot._copy_agent.settings = bot.settings  # type: ignore[attr-defined]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}
