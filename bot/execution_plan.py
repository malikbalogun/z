"""Group multi-leg intents (same bundle_id) for sequential execution."""

from __future__ import annotations

from bot.models import TradeIntent


def plan_execution_units(intents: list[TradeIntent]) -> list[tuple[TradeIntent, ...]]:
    """
    Build ordered execution units: each unit is one intent or exactly two bundle legs.
    Incomplete bundle groups are treated as separate single-intent units.
    """
    srt = sorted(intents, key=lambda x: (-x.priority, str(x.bundle_id or ""), x.token_id))
    by_b: dict[str, list[TradeIntent]] = {}
    no_b: list[TradeIntent] = []
    for it in srt:
        bid = it.bundle_id
        if bid:
            by_b.setdefault(str(bid), []).append(it)
        else:
            no_b.append(it)

    units: list[tuple[TradeIntent, ...]] = []
    for bid, legs in by_b.items():
        if len(legs) == 2:
            a, b = sorted(legs, key=lambda x: x.token_id)
            units.append((a, b))
        else:
            for it in legs:
                no_b.append(it)

    for it in no_b:
        units.append((it,))

    units.sort(key=lambda u: (-u[0].priority, u[0].token_id))
    return units
