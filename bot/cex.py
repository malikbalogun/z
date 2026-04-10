"""
Multi-exchange spot mid prices for gating crypto strategies.
Uses public REST only (no keys). Median + dispersion in basis points.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

log = logging.getLogger("polymarket.cex")


async def _with_retry(coro, *, attempts: int = 3, base_delay: float = 0.2):
    """Run async callable with small backoff (handles transient HTTP / rate limits)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await coro()
        except Exception as e:
            last = e
            if i + 1 < attempts:
                await asyncio.sleep(base_delay * (i + 1))
    if last:
        log.debug("retry exhausted: %s", last)
    return None


# symbol -> Binance, Coinbase pair, Kraken pair, OKX instId
SYMBOL_MAP = {
    "BTC": ("BTCUSDT", "BTC-USD", "XBTUSD", "BTC-USDT"),
    "ETH": ("ETHUSDT", "ETH-USD", "ETHUSD", "ETH-USDT"),
    "SOL": ("SOLUSDT", "SOL-USD", "SOLUSD", "SOL-USDT"),
    "XRP": ("XRPUSDT", "XRP-USD", "XRPUSD", "XRP-USDT"),
}


async def _binance_mid(client: httpx.AsyncClient, sym: str) -> Optional[float]:
    try:
        r = await client.get(
            "https://api.binance.com/api/v3/ticker/bookTicker", params={"symbol": sym}
        )
        r.raise_for_status()
        j = r.json()
        bid, ask = float(j["bidPrice"]), float(j["askPrice"])
        return (bid + ask) / 2
    except Exception as e:
        log.debug("Binance %s: %s", sym, e)
        return None


async def _coinbase_mid(client: httpx.AsyncClient, pair: str) -> Optional[float]:
    try:
        r = await client.get(f"https://api.coinbase.com/v2/prices/{pair}/spot")
        r.raise_for_status()
        return float(r.json()["data"]["amount"])
    except Exception as e:
        log.debug("Coinbase %s: %s", pair, e)
        return None


async def _kraken_mid(client: httpx.AsyncClient, pair: str) -> Optional[float]:
    try:
        r = await client.get(
            "https://api.kraken.com/0/public/Ticker", params={"pair": pair}
        )
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            return None
        result = j["result"]
        key = next(iter(result))
        c = result[key]["c"]
        return float(c[0])
    except Exception as e:
        log.debug("Kraken %s: %s", pair, e)
        return None


async def _okx_mid(client: httpx.AsyncClient, inst: str) -> Optional[float]:
    try:
        r = await client.get(
            "https://www.okx.com/api/v5/market/ticker", params={"instId": inst}
        )
        r.raise_for_status()
        data = r.json().get("data") or []
        if not data:
            return None
        return float(data[0]["last"])
    except Exception as e:
        log.debug("OKX %s: %s", inst, e)
        return None


async def fetch_cex_bundle(asset: str) -> dict:
    """
    Returns {median, mids: {venue: price}, dispersion_bps, ok_count}.
    asset: BTC | ETH | SOL | XRP
    """
    asset = asset.upper()
    if asset not in SYMBOL_MAP:
        return {"median": None, "mids": {}, "dispersion_bps": None, "ok_count": 0, "error": "unknown_asset"}

    bn, cb, kr, ok = SYMBOL_MAP[asset]
    async with httpx.AsyncClient(timeout=12.0) as client:
        mids_t = await asyncio.gather(
            _with_retry(lambda: _binance_mid(client, bn)),
            _with_retry(lambda: _coinbase_mid(client, cb)),
            _with_retry(lambda: _kraken_mid(client, kr)),
            _with_retry(lambda: _okx_mid(client, ok)),
        )
    labels = ("binance", "coinbase", "kraken", "okx")
    mids = {labels[i]: p for i, p in enumerate(mids_t) if p is not None}
    vals = list(mids.values())
    if len(vals) < 2:
        return {
            "median": vals[0] if vals else None,
            "mids": mids,
            "dispersion_bps": None,
            "ok_count": len(vals),
            "error": "insufficient_venues",
        }

    vals_sorted = sorted(vals)
    med = vals_sorted[len(vals_sorted) // 2]
    lo, hi = min(vals), max(vals)
    disp_bps = ((hi - lo) / med) * 10000.0 if med else None
    return {
        "median": med,
        "mids": mids,
        "dispersion_bps": disp_bps,
        "ok_count": len(vals),
        "error": None,
    }


def infer_crypto_asset_from_text(text: str) -> Optional[str]:
    t = text.lower()
    if "bitcoin" in t or "btc" in t:
        return "BTC"
    if "ethereum" in t or "eth" in t:
        return "ETH"
    if "solana" in t or "sol" in t:
        return "SOL"
    if "xrp" in t or "ripple" in t:
        return "XRP"
    return None
