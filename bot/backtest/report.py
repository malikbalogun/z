"""JSON + markdown report writers for backtest results.

The JSON shape is the source of truth (machine-readable); the markdown is a
human-friendly summary derived from the same dict so they cannot drift.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from pathlib import Path
from typing import Any

from bot.backtest.replay import (
    CategoryAttribution,
    DailyPoint,
    ReplayConfig,
    ReplayResult,
    WalletAttribution,
)

REPORT_SCHEMA_VERSION = "1"


def _serialize(obj: Any) -> Any:
    """Convert dataclasses + dates to plain Python so json.dump is happy."""
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj):
        out = {}
        for f in dataclasses.fields(obj):
            out[f.name] = _serialize(getattr(obj, f.name))
        return out
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


def to_dict(result: ReplayResult) -> dict[str, Any]:
    """Turn a ReplayResult into the canonical JSON-shaped dict."""
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "start_date": result.start_date.isoformat() if result.start_date else None,
        "end_date": result.end_date.isoformat() if result.end_date else None,
        "config": _serialize(result.config),
        "totals": {
            "source_rows": result.total_source_rows,
            "evaluated": result.total_evaluated,
            "accepted": result.total_accepted,
            "rejected_by_score": result.total_rejected_by_score,
            "rejected_by_filter": result.total_rejected_by_filter,
            "unresolved": result.total_unresolved,
        },
        "pnl": {
            "gross": result.gross_pnl,
            "final_balance": result.final_balance,
            "hit_rate": result.hit_rate,
            "max_drawdown": result.max_drawdown,
        },
        "rejection_reasons": dict(result.rejection_reasons),
        "per_wallet": [_serialize(w) for w in result.per_wallet],
        "per_category": [_serialize(c) for c in result.per_category],
        "daily": [_serialize(d) for d in result.daily],
    }
    return payload


def write_json(result: ReplayResult, path: str | Path) -> dict[str, Any]:
    """Write the JSON report; returns the dict that was written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = to_dict(result)
    p.write_text(json.dumps(payload, indent=2, sort_keys=False))
    return payload


def write_markdown(result: ReplayResult, path: str | Path) -> str:
    """Write a small markdown summary; returns the rendered string."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = render_markdown(result)
    p.write_text(text)
    return text


def render_markdown(result: ReplayResult) -> str:
    """Pure formatter — no IO. Used by tests + write_markdown."""
    cfg = result.config
    lines: list[str] = []
    lines.append("# Backtest Report")
    lines.append("")
    if result.start_date and result.end_date:
        lines.append(f"**Window**: {result.start_date} \u2192 {result.end_date}")
    lines.append("")
    lines.append("## Settings")
    lines.append(f"- Initial balance: ${cfg.initial_balance:,.2f}")
    lines.append(f"- Follower size per trade: ${cfg.follower_size_usd:,.2f}")
    lines.append(f"- Min wallet WR: {cfg.min_win_rate:.0%}")
    lines.append(f"- Min total resolved trades: {cfg.min_total_trades}")
    lines.append(f"- Min wallet score: {cfg.min_wallet_score}")
    if cfg.copy_allowed_categories:
        lines.append(f"- Allowed categories: {', '.join(cfg.copy_allowed_categories)}")
    if cfg.manual_wallets:
        lines.append(f"- Manual pinned wallets: {len(cfg.manual_wallets)}")
    lines.append("")
    lines.append("## Outcomes")
    lines.append(f"- Source rows: {result.total_source_rows:,}")
    lines.append(f"- Evaluated: {result.total_evaluated:,}")
    lines.append(f"- Accepted (would have copied): **{result.total_accepted:,}**")
    lines.append(f"- Rejected by wallet score/quality: {result.total_rejected_by_score:,}")
    lines.append(f"- Rejected by per-trade filter: {result.total_rejected_by_filter:,}")
    lines.append(f"- Unresolved at snapshot time: {result.total_unresolved:,}")
    lines.append("")
    lines.append("## P&L")
    lines.append(f"- Gross PnL: **${result.gross_pnl:,.2f}**")
    lines.append(f"- Final balance: ${result.final_balance:,.2f}")
    lines.append(f"- Hit rate: {result.hit_rate:.1%}")
    lines.append(f"- Max drawdown: ${result.max_drawdown:,.2f}")
    lines.append("")
    if result.per_wallet:
        lines.append("## Top wallets (by our PnL)")
        lines.append("")
        lines.append("| wallet | trades | wins | losses | notional | pnl |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for w in result.per_wallet[:20]:
            lines.append(
                f"| `{w.wallet[:14]}\u2026` | {w.trades} | {w.wins} | {w.losses} "
                f"| ${w.notional:,.2f} | ${w.pnl:,.2f} |"
            )
        lines.append("")
    if result.per_category:
        lines.append("## P&L by category")
        lines.append("")
        lines.append("| category | trades | pnl |")
        lines.append("|---|---:|---:|")
        for c in result.per_category:
            lines.append(f"| {c.category} | {c.trades} | ${c.pnl:,.2f} |")
        lines.append("")
    if result.rejection_reasons:
        lines.append("## Rejection reasons")
        lines.append("")
        lines.append("| reason | count |")
        lines.append("|---|---:|")
        for reason, n in sorted(
            result.rejection_reasons.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"| {reason} | {n} |")
        lines.append("")
    return "\n".join(lines) + "\n"
