"""Paper portfolio: tracks simulated positions from dry-run trades with live P&L."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from bot.http_retry import get_json_retry
from bot.clob_utils import parse_midpoint

log = logging.getLogger("polymarket.paper_portfolio")

GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def _match_outcome_index(outcome: str, outcomes: list) -> Optional[int]:
    """Match an outcome string to its index in the outcomes list, tolerant of case
    and common aliases. Returns None when no clear match exists."""
    if not outcomes:
        return None
    target = (outcome or "").strip().lower()
    names = [str(o or "").strip().lower() for o in outcomes]
    if not target:
        return None
    if target in names:
        return names.index(target)
    # Binary Yes/No aliases
    if len(names) == 2 and set(names) == {"yes", "no"}:
        if target in ("yes", "true", "y", "1"):
            return names.index("yes")
        if target in ("no", "false", "n", "0"):
            return names.index("no")
    # Numeric index fallback (e.g. "0"/"1")
    if target.isdigit():
        i = int(target)
        if 0 <= i < len(names):
            return i
    return None


@dataclass
class PaperPosition:
    """A simulated position from paper trades."""
    token_id: str
    condition_id: str
    market: str
    outcome: str
    side: str
    shares: float = 0.0
    avg_price: float = 0.0
    total_cost: float = 0.0
    current_price: float = 0.0
    trades: int = 0
    last_trade_at: str = ""
    strategy: str = ""


class PaperPortfolio:
    """Aggregates dry-run trades into positions and computes live P&L."""

    def __init__(self):
        self._positions: dict[str, PaperPosition] = {}
        self._starting_balance: float = 10000.0
        self._spent: float = 0.0
        self._realized_pnl: float = 0.0
        self._last_price_refresh: float = 0.0

    def record_fill(
        self,
        token_id: str,
        condition_id: str,
        market: str,
        outcome: str,
        side: str,
        price: float,
        shares: float,
        cost_usd: float,
        timestamp: str,
        strategy: str = "",
    ) -> None:
        """Record a paper trade fill into the portfolio."""
        if side.upper() != "BUY":
            return
        key = token_id
        if key in self._positions:
            pos = self._positions[key]
            old_total = pos.avg_price * pos.shares
            pos.shares += shares
            pos.total_cost += cost_usd
            pos.avg_price = pos.total_cost / pos.shares if pos.shares > 0 else price
            pos.trades += 1
            pos.last_trade_at = timestamp
        else:
            self._positions[key] = PaperPosition(
                token_id=token_id,
                condition_id=condition_id,
                market=market,
                outcome=outcome,
                side=side,
                shares=shares,
                avg_price=price,
                total_cost=cost_usd,
                current_price=price,
                trades=1,
                last_trade_at=timestamp,
                strategy=strategy,
            )
        self._spent += cost_usd

    async def refresh_prices(self, http: httpx.AsyncClient, clob: Any = None) -> None:
        """Fetch current prices for all paper positions."""
        if not self._positions:
            return
        now = time.time()
        if now - self._last_price_refresh < 15:
            return
        self._last_price_refresh = now

        for key, pos in self._positions.items():
            if clob:
                try:
                    mid = clob.get_midpoint(token_id=pos.token_id)
                    parsed = parse_midpoint(mid)
                    if parsed is not None:
                        pos.current_price = float(parsed)
                        continue
                except Exception:
                    pass
            try:
                if not pos.condition_id:
                    continue
                data = await get_json_retry(
                    http,
                    GAMMA_URL,
                    params={"condition_id": pos.condition_id, "limit": "1"},
                )
                if not (isinstance(data, list) and data):
                    continue
                m = data[0]
                prices = m.get("outcomePrices", m.get("outcome_prices", ""))
                outcomes = m.get("outcomes", "")
                if isinstance(prices, str):
                    import json
                    try:
                        prices = json.loads(prices)
                    except Exception:
                        prices = []
                if isinstance(outcomes, str):
                    import json
                    try:
                        outcomes = json.loads(outcomes)
                    except Exception:
                        outcomes = []
                if not (isinstance(prices, list) and isinstance(outcomes, list) and len(prices) == len(outcomes) and len(prices) >= 1):
                    continue
                idx = _match_outcome_index(pos.outcome, outcomes)
                if idx is None or idx >= len(prices):
                    continue
                try:
                    pos.current_price = float(prices[idx])
                except (ValueError, TypeError):
                    pass
            except Exception as e:
                log.debug("price refresh for %s: %s", pos.token_id[:12], e)

    def get_positions(self) -> list[dict[str, Any]]:
        """Return all paper positions with P&L."""
        out = []
        for pos in self._positions.values():
            if pos.shares < 0.01:
                continue
            value = pos.current_price * pos.shares
            pnl = (pos.current_price - pos.avg_price) * pos.shares
            pnl_pct = ((pos.current_price - pos.avg_price) / pos.avg_price * 100) if pos.avg_price > 0 else 0
            out.append({
                "token_id": pos.token_id,
                "condition_id": pos.condition_id,
                "market": pos.market,
                "outcome": pos.outcome,
                "side": pos.side,
                "size": round(pos.shares, 2),
                "avg_price": round(pos.avg_price, 4),
                "current_price": round(pos.current_price, 4),
                "value": round(value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "cost": round(pos.total_cost, 2),
                "trades": pos.trades,
                "last_trade_at": pos.last_trade_at,
                "strategy": pos.strategy,
                "paper": True,
            })
        out.sort(key=lambda x: abs(x["pnl"]), reverse=True)
        return out

    def get_summary(self) -> dict[str, Any]:
        """Return portfolio-level summary."""
        positions = self.get_positions()
        total_value = sum(p["value"] for p in positions)
        total_pnl = sum(p["pnl"] for p in positions)
        total_cost = sum(p["cost"] for p in positions)
        paper_balance = self._starting_balance - self._spent
        winners = sum(1 for p in positions if p["pnl"] > 0)
        losers = sum(1 for p in positions if p["pnl"] < 0)
        return {
            "paper_balance": round(paper_balance, 2),
            "total_invested": round(total_cost, 2),
            "portfolio_value": round(total_value, 2),
            "unrealized_pnl": round(total_pnl, 2),
            "unrealized_pnl_pct": round((total_pnl / total_cost * 100) if total_cost > 0 else 0, 2),
            "realized_pnl": round(self._realized_pnl, 2),
            "total_pnl": round(total_pnl + self._realized_pnl, 2),
            "positions_count": len(positions),
            "winners": winners,
            "losers": losers,
            "win_rate": round(winners / (winners + losers) * 100, 1) if (winners + losers) > 0 else 0,
            "starting_balance": self._starting_balance,
        }
