"""Copyability scoring: rate how copy-worthy a trade candidate is.

Combines wallet quality, market quality, price attractiveness, and size
into a single [0,1] score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("polymarket.phase1.copyability")


@dataclass
class CopyabilityInput:
    """All inputs needed to compute a copyability score."""
    wallet_score: float  # [0,1]
    source_price: float  # price the wallet traded at
    market_liquidity: float  # USD
    market_volume: float  # USD
    usdc_value: float  # size of wallet's trade in USD
    outcome: str  # yes/no/unknown
    category: str
    hours_to_resolution: float | None = None


@dataclass
class CopyabilityResult:
    score: float
    components: dict[str, float]
    explanation: str


def compute_copyability(inp: CopyabilityInput) -> CopyabilityResult:
    """Deterministic copyability score.

    Components (weights sum to 1.0):
      - wallet_quality (0.35): direct from wallet score
      - price_attractiveness (0.25): prefer mid-range prices (0.15-0.70)
      - liquidity_factor (0.20): log-scaled market liquidity
      - size_factor (0.10): prefer non-trivial but not extreme sizes
      - outcome_clarity (0.10): known outcome (yes/no) preferred
    """
    import math

    # Wallet quality
    wq = min(max(inp.wallet_score, 0.0), 1.0)

    # Price attractiveness: bell curve centered around 0.35, penalize extremes
    px = inp.source_price
    if px <= 0.02 or px >= 0.98:
        pa = 0.0
    elif 0.10 <= px <= 0.70:
        pa = 1.0 - abs(px - 0.35) / 0.35
        pa = max(pa, 0.3)
    else:
        pa = max(0.0, 1.0 - abs(px - 0.5) / 0.5)

    # Liquidity factor
    liq = max(inp.market_liquidity, 0.0)
    lf = min(math.log1p(liq) / math.log1p(50000), 1.0)

    # Size factor: prefer $5-$100 range
    usd = max(inp.usdc_value, 0.0)
    if usd < 1.0:
        sf = 0.1
    elif usd <= 100:
        sf = min(usd / 50.0, 1.0)
    else:
        sf = max(1.0 - (usd - 100) / 500.0, 0.3)

    # Outcome clarity
    oc = 1.0 if inp.outcome in ("yes", "no") else 0.4

    score = 0.35 * wq + 0.25 * pa + 0.20 * lf + 0.10 * sf + 0.10 * oc
    score = min(max(score, 0.0), 1.0)

    components = {
        "wallet_quality": round(wq, 4),
        "price_attractiveness": round(pa, 4),
        "liquidity_factor": round(lf, 4),
        "size_factor": round(sf, 4),
        "outcome_clarity": round(oc, 4),
    }

    explanation = (
        f"copyability={score:.3f} "
        f"[wq={wq:.2f} pa={pa:.2f} lf={lf:.2f} sf={sf:.2f} oc={oc:.2f}]"
    )

    return CopyabilityResult(score=round(score, 4), components=components, explanation=explanation)
