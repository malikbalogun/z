"""
FastAPI dashboard + API.
Auth: cookie session (config.json session_secret). Admin-only mutating bot routes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from bot.auth_session import safe_parse
from bot.config_file import load_config, project_root
from bot.web.admin_api import router as admin_router
from bot.phase1.api import router as p1_router
from bot.web.deps import get_current_user, require_admin
from bot.db.models import User

logger = logging.getLogger("polymarket.server")

app = FastAPI(title="Polymarket Trading Bot", version="4.0.0")
app.include_router(admin_router)
app.include_router(p1_router)

trader = None

_cfg = load_config()
_upload = Path(_cfg.get("upload_dir") or "./data/uploads")
if not _upload.is_absolute():
    _upload = project_root() / _upload
if _upload.exists():
    app.mount("/uploads", StaticFiles(directory=str(_upload)), name="uploads")


@app.get("/api/health")
async def health():
    ok = trader is not None and getattr(trader, "clob", None) is not None
    out: dict = {"ok": ok, "trader_ready": ok}
    if trader and ok:
        st = getattr(trader, "state", None)
        if st is not None:
            out["open_orders"] = len(getattr(st, "open_orders", []) or [])
            out["last_reconcile_at"] = getattr(st, "last_reconcile_at", None)
    return out


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    p = Path(__file__).parent / "templates" / "login.html"
    if p.exists():
        return HTMLResponse(p.read_text())
    return HTMLResponse("<html><body><h1>Login</h1><p>templates/login.html missing</p></body></html>")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    p = Path(__file__).parent / "templates" / "admin.html"
    if p.exists():
        return HTMLResponse(p.read_text())
    return HTMLResponse("<html><body><h1>Admin</h1><p>templates/admin.html missing</p></body></html>")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        return HTMLResponse(content=template_path.read_text())
    return HTMLResponse(content="<h1>Polymarket Bot</h1><p>Dashboard template not found.</p>")


@app.get("/api/state")
async def get_state(user: User = Depends(get_current_user)):
    if not trader:
        return JSONResponse({"error": "Trader not initialized"}, status_code=503)
    try:
        return trader.get_state_dict()
    except Exception as e:
        logger.error("State error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/errors")
async def get_errors(user: User = Depends(get_current_user)):
    if not trader:
        return JSONResponse({"error": "Trader not initialized"}, status_code=503)
    return {"errors": list(trader.state.errors)}


@app.get("/api/positions")
async def get_positions(user: User = Depends(get_current_user)):
    if not trader:
        return JSONResponse({"error": "Trader not initialized"}, status_code=503)
    return {"positions": trader.state.positions}


@app.get("/api/balance")
async def get_balance(user: User = Depends(get_current_user)):
    if not trader:
        return JSONResponse({"error": "Trader not initialized"}, status_code=503)
    return {
        "usdc_balance": trader.state.usdc_balance,
        "portfolio_value": trader.state.portfolio_value,
        "total_pnl": trader.state.total_pnl,
    }


@app.get("/api/trades")
async def get_trades(user: User = Depends(get_current_user)):
    if not trader:
        return JSONResponse({"error": "Trader not initialized"}, status_code=503)
    return {
        "trades": [
            {
                "order_id": t.order_id,
                "market": t.market_question,
                "side": t.side,
                "outcome": t.outcome,
                "price": t.price,
                "size": t.size,
                "cost": t.cost_usd,
                "status": t.status,
                "timestamp": t.timestamp,
                "strategy": t.strategy,
            }
            for t in trader.state.trade_history[-50:]
        ],
        "total_placed": trader.state.trades_placed,
        "total_filled": trader.state.trades_filled,
    }


@app.post("/api/reconcile")
async def api_reconcile(user: User = Depends(require_admin)):
    if not trader:
        return JSONResponse({"error": "Trader not initialized"}, status_code=503)
    try:
        return await trader.force_reconcile()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/kill")
async def kill_switch(user: User = Depends(require_admin)):
    if trader:
        trader.stop()
        return {"success": True, "message": "Bot stopped"}
    return JSONResponse({"success": False, "message": "No trader running"}, status_code=503)


@app.post("/api/scan")
async def force_scan(user: User = Depends(require_admin)):
    if not trader:
        return JSONResponse({"error": "Trader not initialized"}, status_code=503)
    try:
        await trader.run_cycle()
        return {"success": True, "markets_scanned": trader.state.markets_scanned}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


active_connections = []


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    cfg = load_config()
    secret = str(cfg.get("session_secret") or "")
    allow_anon = False
    if trader is not None and getattr(trader, "settings", None):
        allow_anon = bool(getattr(trader.settings, "ws_allow_anonymous", False))
    token = websocket.cookies.get("pm_session")
    if not allow_anon:
        if len(secret) < 16 or not safe_parse(secret, token):
            await websocket.close(code=4401)
            return
    active_connections.append(websocket)
    try:
        while True:
            if trader:
                await websocket.send_json(trader.get_state_dict())
            await asyncio.sleep(5)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if websocket in active_connections:
            active_connections.remove(websocket)
