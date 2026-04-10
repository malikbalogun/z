"""Admin + auth JSON API (mounted from server)."""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.auth_session import issue_token
from bot.categories import MarketCategory
from bot.config_file import load_config, project_root
from bot.copy_rules import build_candidate, limit_price_with_buffer, passes_filters, wallet_score
from bot.db.bootstrap import hash_password
from bot.db.kv import load_all_kv, upsert_many_kv
from bot.db.models import ArticleSignal, User
from bot.exposure import rolling_notional_usd
from bot.http_retry import get_json_retry
from bot.orderbook import orderbook_buy_depth_ok
from bot.risk import gate_intent
from bot.settings import Settings, default_kv_seed
from bot.settings_validation import validate_and_normalize_settings_patch
from bot.wallet_trades import fetch_wallet_trades
from bot.web.deps import get_current_user, get_db, require_admin, verify_user_password

router = APIRouter(tags=["auth-admin"])
ACTIVITY_URL = "https://data-api.polymarket.com/activity"


def _trader(request: Request):
    t = getattr(request.app.state, "trader", None)
    return t


def _require_webhook_token(x_hook_token: str | None) -> None:
    cfg = load_config()
    expected = str(cfg.get("webhook_token") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="webhook_token not configured")
    provided = (x_hook_token or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook token")


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
    kv = load_all_kv()
    # Mask private key for response (show prefix only)
    out = dict(kv)
    pk = out.get("polymarket_private_key") or ""
    if len(pk) > 12:
        out["polymarket_private_key"] = pk[:6] + "…" + pk[-4:]
    return {
        "settings": out,
        "defaults": default_kv_seed(),
        "meta": {
            "categories": [c.value for c in MarketCategory],
        },
    }


class SettingsPatch(BaseModel):
    settings: dict[str, Any]


@router.post("/api/admin/settings")
def admin_save_settings(
    body: SettingsPatch,
    _: Annotated[User, Depends(require_admin)],
):
    normalized, errors = validate_and_normalize_settings_patch(body.settings)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "invalid_settings", "errors": errors})
    upsert_many_kv(normalized)
    return {"ok": True}


@router.get("/api/admin/settings/export")
def admin_export_settings(
    _: Annotated[User, Depends(require_admin)],
    include_secrets: bool = False,
):
    kv = load_all_kv()
    out = dict(kv)
    if not include_secrets:
        if out.get("polymarket_private_key"):
            out["polymarket_private_key"] = ""
    return {"ok": True, "settings": out}


@router.post("/api/admin/settings/import")
def admin_import_settings(
    body: SettingsPatch,
    _: Annotated[User, Depends(require_admin)],
):
    normalized, errors = validate_and_normalize_settings_patch(body.settings)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "invalid_settings", "errors": errors})
    upsert_many_kv(normalized)
    return {"ok": True, "updated": len(normalized)}


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


class SignalPatchBody(BaseModel):
    active: bool | None = None
    sentiment: float | None = None
    weight: float | None = None
    keywords: list[str] | None = None


@router.patch("/api/admin/signals/{signal_id}")
def admin_patch_signal(
    signal_id: int,
    body: SignalPatchBody,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    sig = db.get(ArticleSignal, signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")
    if body.active is not None:
        sig.active = bool(body.active)
    if body.sentiment is not None:
        sig.sentiment = float(body.sentiment)
    if body.weight is not None:
        sig.weight = float(body.weight)
    if body.keywords is not None:
        sig.keywords = json.dumps([str(x) for x in body.keywords])
    db.commit()
    return {"ok": True}


@router.delete("/api/admin/signals/{signal_id}")
def admin_delete_signal(
    signal_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    sig = db.get(ArticleSignal, signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")
    db.delete(sig)
    db.commit()
    return {"ok": True}


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


@router.get("/api/admin/copy-preview")
async def admin_copy_preview(
    request: Request,
    _: Annotated[User, Depends(require_admin)],
    wallet: str | None = None,
    limit: int = 40,
):
    """
    Preview which recent trades pass copy filters and why.
    Uses current running settings if trader exists, else Settings.load().
    """
    bot = _trader(request)
    if not bot or not getattr(bot, "_http", None):
        raise HTTPException(status_code=503, detail="Trader HTTP client not ready")
    s = getattr(bot, "settings", None) or Settings.load()
    watch = [wallet.strip().lower()] if wallet else list(getattr(s, "copy_watch_wallets", []) or [])
    watch = [w for w in watch if w]
    if not watch:
        return {"ok": True, "wallets": [], "items": [], "note": "no_wallets_configured"}
    lim = max(1, min(int(limit or 40), 120))
    items: list[dict[str, Any]] = []
    for w in watch[:5]:
        try:
            rows = await get_json_retry(bot._http, ACTIVITY_URL, params={"user": w, "limit": str(lim)})
        except Exception as e:
            items.append({"wallet": w, "error": str(e), "rows": []})
            continue
        score, parts = wallet_score(
            rows if isinstance(rows, list) else [],
            wallet=w,
            default_bet_usd=float(getattr(s, "default_bet_usd", 5.0)),
            settings=s,
        )
        out: list[dict[str, Any]] = []
        for entry in rows if isinstance(rows, list) else []:
            c = build_candidate(entry, w, float(getattr(s, "default_bet_usd", 5.0)))
            if c is None:
                continue
            ok, reason = passes_filters(s, c)
            out.append(
                {
                    "wallet": w,
                    "pass": bool(ok),
                    "reason": reason,
                    "title": c.title[:140],
                    "category": c.category,
                    "outcome": c.outcome,
                    "price": c.price,
                    "usd": c.usdc,
                    "token_id": c.token_id,
                    "limit_price": limit_price_with_buffer(s, c.price),
                }
            )
        items.append({"wallet": w, "score": score, "score_parts": parts, "count": len(out), "rows": out[:80]})
    items.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return {"ok": True, "wallets": watch, "items": items, "min_score": float(getattr(s, "copy_min_wallet_score", 0.0) or 0.0)}


async def _intents_preview_core(bot: Any, agent: str, limit: int) -> dict[str, Any]:
    """
    Preview current agent intents and gate decisions without placing orders.
    agent: all|value_edge|copy_signal|latency_arb|bundle_arb|zscore_edge
    """
    allowed_agents = {"all", "value_edge", "copy_signal", "latency_arb", "bundle_arb", "zscore_edge"}
    if agent not in allowed_agents:
        raise HTTPException(status_code=400, detail=f"agent must be one of: {sorted(allowed_agents)}")

    await bot._reload_settings_async()
    await bot.refresh_positions()
    await bot.refresh_open_orders()
    markets = await bot._gamma_scan()
    pos_tokens = {p.get("token_id") for p in bot.state.positions if p.get("token_id")}

    tasks = []
    labels: list[str] = []
    if agent in ("all", "value_edge") and bot.settings.agent_value:
        labels.append("value_edge")
        tasks.append(bot._value_agent.propose(bot.clob, markets, pos_tokens, bot._rate_limit))
    if agent in ("all", "copy_signal") and bot.settings.agent_copy and bot.settings.copy_watch_wallets:
        labels.append("copy_signal")
        tasks.append(bot._copy_agent.propose(bot._http))
    if agent in ("all", "latency_arb") and bot.settings.agent_latency:
        labels.append("latency_arb")
        tasks.append(bot._latency_agent.propose(bot.clob, markets, pos_tokens, bot._rate_limit))
    if agent in ("all", "bundle_arb") and bot.settings.agent_bundle:
        labels.append("bundle_arb")
        tasks.append(bot._bundle_agent.propose(bot.clob, markets, pos_tokens, bot._rate_limit))
    if agent in ("all", "zscore_edge") and bot.settings.agent_zscore:
        labels.append("zscore_edge")
        tasks.append(bot._zscore_agent.propose(bot.clob, markets, pos_tokens, bot._rate_limit))

    if not tasks:
        return {"ok": True, "agent": agent, "enabled_agents": [], "items": [], "note": "no_enabled_agents"}

    raw = await asyncio.gather(*tasks, return_exceptions=True)  # type: ignore[name-defined]
    intents = []
    errors: list[dict[str, str]] = []
    for i, r in enumerate(raw):
        if isinstance(r, Exception):
            errors.append({"agent": labels[i], "error": str(r)})
            continue
        intents.extend(r)
    intents.sort(key=lambda x: -x.priority)

    cex_map = await bot._cex_map_for_intents(intents) if intents else {}
    markets_by_cid = {str(m.get("condition_id") or ""): m for m in markets if m.get("condition_id")}
    rolling_n = rolling_notional_usd(
        bot.state.trade_history,
        hours=float(bot.settings.daily_notional_window_hours or 24.0),
    )

    out: list[dict[str, Any]] = []
    lim = max(1, min(int(limit or 80), 250))
    for it in intents[:lim]:
        await bot._apply_intent_multipliers(it)
        disp = bot._dispersion_for_intent(it, cex_map)

        ok_risk, risk_reason = gate_intent(it, bot.settings, disp)
        if not ok_risk:
            out.append(
                {
                    "agent": it.agent,
                    "strategy": it.strategy,
                    "question": it.question[:140],
                    "token_id": it.token_id,
                    "usd": round(float(it.size_usd), 4),
                    "max_price": it.max_price,
                    "pass": False,
                    "reason": risk_reason,
                }
            )
            continue

        if bot.settings.orderbook_gate_enabled and it.side.upper() == "BUY":
            ok_book = await asyncio.to_thread(
                orderbook_buy_depth_ok,
                bot.clob,
                it.token_id,
                float(bot.settings.orderbook_min_bid_share),
            )
            if not ok_book:
                out.append(
                    {
                        "agent": it.agent,
                        "strategy": it.strategy,
                        "question": it.question[:140],
                        "token_id": it.token_id,
                        "usd": round(float(it.size_usd), 4),
                        "max_price": it.max_price,
                        "pass": False,
                        "reason": "orderbook_imbalance",
                    }
                )
                continue

        ok_adv, adv_reason = await bot._advanced_gates_ok(
            [it],
            markets_by_cid=markets_by_cid,
            rolling_notional=rolling_n,
        )
        out.append(
            {
                "agent": it.agent,
                "strategy": it.strategy,
                "question": it.question[:140],
                "token_id": it.token_id,
                "category": it.category.value,
                "usd": round(float(it.size_usd), 4),
                "max_price": it.max_price,
                "pass": bool(ok_adv),
                "reason": "ok" if ok_adv else adv_reason,
                "cex_dispersion_bps": disp,
                "bundle_id": it.bundle_id,
            }
        )

    return {
        "ok": True,
        "agent": agent,
        "enabled_agents": labels,
        "errors": errors,
        "count": len(out),
        "items": out,
    }


@router.get("/api/admin/intents-preview")
async def admin_intents_preview(
    request: Request,
    _: Annotated[User, Depends(require_admin)],
    agent: str = "all",
    limit: int = 80,
):
    """
    Preview current agent intents and gate decisions without placing orders.
    agent: all|value_edge|copy_signal|latency_arb|bundle_arb|zscore_edge
    """
    bot = _trader(request)
    if not bot or not getattr(bot, "clob", None) or not getattr(bot, "_http", None):
        raise HTTPException(status_code=503, detail="Trader not initialized")
    return await _intents_preview_core(bot, agent, limit)


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
        bot._latency_agent.settings = bot.settings  # type: ignore[attr-defined]
        bot._bundle_agent.settings = bot.settings  # type: ignore[attr-defined]
        bot._zscore_agent.settings = bot.settings  # type: ignore[attr-defined]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


class HookPauseBody(BaseModel):
    paused: bool


@router.post("/api/hook/pause")
async def hook_pause(
    request: Request,
    body: HookPauseBody,
    x_hook_token: Annotated[str | None, Header(alias="X-Hook-Token")] = None,
):
    _require_webhook_token(x_hook_token)
    upsert_many_kv({"trading_paused": "true" if body.paused else "false"})
    bot = _trader(request)
    if bot:
        await bot._reload_settings_async()
    return {"ok": True, "trading_paused": bool(body.paused)}


@router.post("/api/hook/reload-settings")
async def hook_reload_settings(
    request: Request,
    x_hook_token: Annotated[str | None, Header(alias="X-Hook-Token")] = None,
):
    _require_webhook_token(x_hook_token)
    bot = _trader(request)
    if not bot:
        raise HTTPException(status_code=503, detail="Trader not initialized")
    await bot._reload_settings_async()
    return {"ok": True}


@router.get("/api/hook/intents-preview")
async def hook_intents_preview(
    request: Request,
    agent: str = "all",
    limit: int = 80,
    x_hook_token: Annotated[str | None, Header(alias="X-Hook-Token")] = None,
):
    _require_webhook_token(x_hook_token)
    bot = _trader(request)
    if not bot or not getattr(bot, "clob", None) or not getattr(bot, "_http", None):
        raise HTTPException(status_code=503, detail="Trader not initialized")
    return await _intents_preview_core(bot, agent, limit)


@router.get("/api/hook/telemetry")
async def hook_telemetry(
    request: Request,
    x_hook_token: Annotated[str | None, Header(alias="X-Hook-Token")] = None,
):
    _require_webhook_token(x_hook_token)
    bot = _trader(request)
    if not bot:
        raise HTTPException(status_code=503, detail="Trader not initialized")
    st = bot.get_state_dict()
    statuses: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    for t in st.get("trade_history", []):
        s = str(t.get("status") or "unknown")
        statuses[s] = statuses.get(s, 0) + 1
        strat = str(t.get("strategy") or "").split(":", 1)[0]
        by_strategy[strat] = by_strategy.get(strat, 0) + 1
    return {
        "ok": True,
        "running": st.get("running", False),
        "mode": st.get("mode"),
        "started_at": st.get("started_at"),
        "last_scan": st.get("last_scan"),
        "last_trade": st.get("last_trade"),
        "balance_usdc": st.get("usdc_balance"),
        "portfolio_value": st.get("portfolio_value"),
        "open_orders_count": st.get("open_orders_count"),
        "markets_scanned": st.get("markets_scanned"),
        "trades_placed": st.get("trades_placed"),
        "trades_filled": st.get("trades_filled"),
        "consecutive_exec_failures": st.get("consecutive_exec_failures"),
        "rolling_notional_window_usd": st.get("rolling_notional_window_usd"),
        "status_counts": statuses,
        "strategy_counts": by_strategy,
        "agents_fired": st.get("agents_fired", []),
        "errors": st.get("errors", []),
    }


@router.get("/api/admin/telemetry")
async def admin_telemetry(
    request: Request,
    _: Annotated[User, Depends(require_admin)],
):
    bot = _trader(request)
    if not bot:
        raise HTTPException(status_code=503, detail="Trader not initialized")
    st = bot.get_state_dict()
    return {
        "ok": True,
        "running": st.get("running", False),
        "mode": st.get("mode"),
        "started_at": st.get("started_at"),
        "last_scan": st.get("last_scan"),
        "last_trade": st.get("last_trade"),
        "balance_usdc": st.get("usdc_balance"),
        "portfolio_value": st.get("portfolio_value"),
        "open_orders_count": st.get("open_orders_count"),
        "markets_scanned": st.get("markets_scanned"),
        "trades_placed": st.get("trades_placed"),
        "trades_filled": st.get("trades_filled"),
        "consecutive_exec_failures": st.get("consecutive_exec_failures"),
        "rolling_notional_window_usd": st.get("rolling_notional_window_usd"),
        "agents_fired": st.get("agents_fired", []),
        "errors": st.get("errors", []),
    }
