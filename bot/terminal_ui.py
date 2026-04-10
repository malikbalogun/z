"""Rich live terminal dashboard (UI_MODE=terminal or both)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("polymarket.terminal")


def _build_renderable(bot: Any):
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    d = bot.get_state_dict()
    st = d.get("settings") or {}

    title = Text()
    title.append(" POLYMARKET BOT ", style="bold black on green")
    if d.get("dry_run"):
        title.append(" DRY RUN ", style="bold black on yellow")
    else:
        title.append(" LIVE ", style="bold white on red")

    head = Table.grid(expand=True)
    head.add_column(justify="left")
    head.add_column(justify="right")
    head.add_row(
        f"[cyan]Wallet[/] {str(d.get('wallet') or '')[:16]}…",
        f"[dim]{d.get('started_at') or '—'}[/]",
    )

    stats = Table(title="Portfolio", box=box.ROUNDED, expand=True)
    stats.add_column("Metric", style="dim")
    stats.add_column("Value", justify="right")
    stats.add_row("USDC balance", f"[green]${d.get('usdc_balance', 0):.2f}[/]")
    stats.add_row("Portfolio", f"${d.get('portfolio_value', 0):.2f}")
    pnl = d.get("total_pnl", 0) or 0
    ps = f"[green]+${pnl:.2f}[/]" if pnl >= 0 else f"[red]${pnl:.2f}[/]"
    stats.add_row("Unrealized P&L", ps)
    stats.add_row("Markets scanned", str(d.get("markets_scanned", 0)))
    stats.add_row("Trades placed / filled", f"{d.get('trades_placed', 0)} / {d.get('trades_filled', 0)}")
    stats.add_row("Open CLOB orders", str(d.get("open_orders_count", 0)))
    stats.add_row("Last reconcile", str(d.get("last_reconcile_at") or "—")[:22])
    stats.add_row("Running", "[green]yes[/]" if d.get("running") else "[red]no[/]")

    agents = Table(title="Agents", box=box.ROUNDED, expand=True)
    agents.add_column("Agent", style="cyan")
    agents.add_column("P", justify="right", width=4)
    agents.add_column("On", justify="center", width=6)
    agents.add_column("Role", style="dim")
    for row in d.get("agents_detail") or []:
        on = "[green]ON[/]" if row.get("enabled") else "[dim]off[/]"
        agents.add_row(
            row.get("id", "?"),
            str(row.get("priority", "")),
            on,
            (row.get("description") or "")[:52] + ("…" if len(row.get("description") or "") > 52 else ""),
        )

    intents = Table(title="Recent intents (sample)", box=box.SIMPLE, expand=True)
    intents.add_column("Agent", style="yellow", width=12)
    intents.add_column("Strategy", width=14)
    intents.add_column("Cat", width=14)
    intents.add_column("Question", style="dim")
    for it in (d.get("last_intents") or [])[:8]:
        intents.add_row(
            str(it.get("agent", "")),
            str(it.get("strategy", ""))[:12],
            str(it.get("category", ""))[:12],
            str(it.get("question", ""))[:40],
        )
    if not (d.get("last_intents") or []):
        intents.add_row("—", "—", "—", "[dim]none this cycle[/]")

    errs = d.get("errors") or []
    err_txt = "\n".join(f"• {e}" for e in errs[-6:]) if errs else "[dim]no recent errors[/]"
    err_panel = Panel(err_txt, title="[red]Errors[/]", border_style="red", expand=True)

    cat = st.get("categories") or {}
    off = [k.replace("ENABLE_", "").lower() for k, v in cat.items() if not v]
    filt = ", ".join(off) if off else "[dim]all categories enabled[/]"

    foot = Text(f"UI_MODE={st.get('ui_mode', '?')}  │  Categories off: {filt}", style="dim")

    return Panel(
        Group(title, head, stats, agents, intents, err_panel, foot),
        title="[bold white]Terminal dashboard[/]",
        subtitle="[dim]Ctrl+C to stop  ·  set UI_MODE=dashboard for web-only[/]",
        border_style="bright_blue",
        padding=(1, 2),
    )


async def run_terminal_dashboard(bot: Any, refresh_seconds: float = 3.0) -> None:
    try:
        from rich.live import Live
        from rich.console import Console
    except ImportError:
        log.error("Install `rich` for terminal UI: pip install rich")
        while bot._running:
            print("\033[2J\033[H", bot.get_state_dict())
            await asyncio.sleep(refresh_seconds)
        return

    console = Console()
    with Live(
        _build_renderable(bot),
        console=console,
        refresh_per_second=min(8.0, max(0.5, 1.0 / max(refresh_seconds, 0.25))),
        screen=True,
    ) as live:
        while bot._running:
            live.update(_build_renderable(bot))
            await asyncio.sleep(refresh_seconds)
