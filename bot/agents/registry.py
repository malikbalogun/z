"""Registered strategy agents — single source of truth for UI and docs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentInfo:
    id: str
    title: str
    short: str
    priority: int


AGENTS: tuple[AgentInfo, ...] = (
    AgentInfo(
        id="value_edge",
        title="Value edge",
        short="Scans Gamma tradeables; compares CLOB mid vs bands; proposes BUY on Yes/No value setups.",
        priority=50,
    ),
    AgentInfo(
        id="copy_signal",
        title="Copy signal",
        short="Watches COPY_WATCH_WALLETS activity on Data API; proposes mirroring new BUYs (cold-start dedupes history).",
        priority=100,
    ),
    AgentInfo(
        id="latency_arb",
        title="Latency arb",
        short="Gamma outcomePrices vs CLOB mid; BUY when the slower quote is richer than the book (cross-feed lag).",
        priority=65,
    ),
    AgentInfo(
        id="bundle_arb",
        title="Bundle arb",
        short="When best YES ask + best NO ask is below 1 (net of buffer), BUY both legs as one execution unit.",
        priority=72,
    ),
    AgentInfo(
        id="zscore_edge",
        title="Z-score edge",
        short="Rolling z-score on YES mid per market; mean-reversion BUY on stretched YES or NO.",
        priority=48,
    ),
)


def agents_status(settings: Any) -> list[dict[str, Any]]:
    """For dashboard / terminal: which agents exist and whether they are on."""
    enabled = {
        "value_edge": bool(getattr(settings, "agent_value", True)),
        "copy_signal": bool(getattr(settings, "agent_copy", False))
        and bool(getattr(settings, "copy_watch_wallets", [])),
        "latency_arb": bool(getattr(settings, "agent_latency", False)),
        "bundle_arb": bool(getattr(settings, "agent_bundle", False)),
        "zscore_edge": bool(getattr(settings, "agent_zscore", False)),
    }
    out = []
    for a in AGENTS:
        out.append(
            {
                "id": a.id,
                "title": a.title,
                "description": a.short,
                "priority": a.priority,
                "enabled": enabled.get(a.id, False),
            }
        )
    return out
