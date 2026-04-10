"""Bot settings from database KV (primary). Optional env merge for CI/tests."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from bot.strategy_profiles import apply_profile
from bot.validate import is_valid_polygon_address


def _b(val: str | None, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    return str(val).lower() in ("1", "true", "yes", "on")


def _f(val: str | None, default: float) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _i(val: str | None, default: int) -> int:
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except ValueError:
        return default


def _s(val: str | None, default: str = "") -> str:
    return (val if val is not None else default).strip()


def default_kv_seed() -> dict[str, str]:
    """Default rows for first DB init (all string values)."""
    p = apply_profile("balanced")
    cats = [
        "ENABLE_CRYPTO_SHORT",
        "ENABLE_CRYPTO_OTHER",
        "ENABLE_SPORTS",
        "ENABLE_POLITICS",
        "ENABLE_MACRO",
        "ENABLE_WEATHER",
        "ENABLE_SCIENCE_TECH",
        "ENABLE_ENTERTAINMENT",
        "ENABLE_GEOPOLITICS",
        "ENABLE_OTHER",
    ]
    out: dict[str, str] = {
        "polymarket_private_key": "",
        "wallet_address": "",
        "polymarket_signature_type": "0",
        "dry_run": "true",
        "host": "0.0.0.0",
        "port": "5002",
        "dashboard_secret": "",
        "default_bet_usd": "5",
        "min_bet_usd": "1",
        "max_bet_usd": "25",
        "scan_interval_seconds": "120",
        "min_clob_liquidity_usd": "500",
        "min_gamma_volume": "1000",
        "max_trades_per_cycle": "1",
        "strict_execution": "true",
        "order_ttl_seconds": "45",
        "order_poll_seconds": "2.0",
        "allow_market_fallback": "false",
        "trading_paused": "false",
        "balance_buffer_usd": "0",
        "cex_require_dispersion": "false",
        "structured_log": "true",
        "reconcile_enabled": "true",
        "reconcile_history_depth": "15",
        "reconcile_poll_sleep_s": "0.06",
        "open_orders_display_limit": "40",
        "agent_value": "true",
        "agent_copy": "false",
        "agent_latency": "false",
        "agent_bundle": "false",
        "agent_zscore": "false",
        "latency_min_dislocation_bps": "120",
        "bundle_max_pair_cost": "0.994",
        "bundle_min_liquidity_usd": "1500",
        "zscore_window": "24",
        "zscore_entry_abs": "2.2",
        "zscore_min_samples": "12",
        "copy_watch_wallets": "[]",
        "copy_poll_seconds": "15",
        "cex_gate_crypto": "true",
        "max_cex_dispersion_bps": "25",
        "ui_mode": "both",
        "strategy_profile": p["strategy_profile"],
        "value_yes_low": str(p["value_yes_low"]),
        "value_yes_high": str(p["value_yes_high"]),
        "value_no_yes_min": str(p["value_no_yes_min"]),
        "value_no_no_max": str(p["value_no_no_max"]),
        "value_liq_floor_usd": str(p["value_liq_floor_usd"]),
        "signals_enabled": "true",
        "pnl_sizing_enabled": "true",
        "pnl_sizing_window": "28",
        "min_edge_bps": "0",
        "orderbook_gate_enabled": "false",
        "orderbook_min_bid_share": "0.38",
        "ws_allow_anonymous": "false",
    }
    for ck in cats:
        out[ck] = "true"
    return out


@dataclass
class Settings:
    polymarket_private_key: str = ""
    wallet_address: str = ""
    polymarket_signature_type: int = 0
    dry_run: bool = True
    host: str = "0.0.0.0"
    port: int = 5002
    dashboard_secret: str = ""

    default_bet_usd: float = 5.0
    min_bet_usd: float = 1.0
    max_bet_usd: float = 25.0
    scan_interval_seconds: int = 120
    min_clob_liquidity_usd: float = 500.0
    min_gamma_volume: float = 1000.0
    max_trades_per_cycle: int = 1

    strict_execution: bool = True
    order_ttl_seconds: int = 45
    order_poll_seconds: float = 2.0
    allow_market_fallback: bool = False

    trading_paused: bool = False
    balance_buffer_usd: float = 0.0
    cex_require_dispersion: bool = False

    structured_log: bool = True
    reconcile_enabled: bool = True
    reconcile_history_depth: int = 15
    reconcile_poll_sleep_s: float = 0.06
    open_orders_display_limit: int = 40

    agent_value: bool = True
    agent_copy: bool = False
    agent_latency: bool = False
    agent_bundle: bool = False
    agent_zscore: bool = False
    latency_min_dislocation_bps: float = 120.0
    bundle_max_pair_cost: float = 0.994
    bundle_min_liquidity_usd: float = 1500.0
    zscore_window: int = 24
    zscore_entry_abs: float = 2.2
    zscore_min_samples: int = 12
    copy_watch_wallets: list[str] = field(default_factory=list)
    copy_poll_seconds: int = 15

    cex_gate_crypto: bool = True
    max_cex_dispersion_bps: float = 25.0

    category_flags: dict[str, bool] = field(default_factory=dict)
    ui_mode: str = "both"

    strategy_profile: str = "balanced"
    value_yes_low: float = 0.20
    value_yes_high: float = 0.45
    value_no_yes_min: float = 0.65
    value_no_no_max: float = 0.45
    value_liq_floor_usd: float = 1000.0

    signals_enabled: bool = True
    pnl_sizing_enabled: bool = True
    pnl_sizing_window: int = 28

    # Minimum (mid - limit) / mid in bps for BUY when reference_price is set (0 = off).
    min_edge_bps: int = 0
    # Order-book: bid notional share must exceed this for BUY (0 = off via flag).
    orderbook_gate_enabled: bool = False
    orderbook_min_bid_share: float = 0.38
    # WebSocket /dashboard stream: if false, require valid session cookie.
    ws_allow_anonymous: bool = False

    @classmethod
    def from_kv(cls, kv: dict[str, str], *, merge_os_environ: bool = False) -> Settings:
        def g(key: str, default: str = "") -> str:
            v = kv.get(key)
            if merge_os_environ:
                ev = os.environ.get(key)
                if ev is not None and str(ev).strip() != "":
                    v = str(ev).strip()
            if v is None:
                return default
            return str(v)

        cat_keys = [
            "ENABLE_CRYPTO_SHORT",
            "ENABLE_CRYPTO_OTHER",
            "ENABLE_SPORTS",
            "ENABLE_POLITICS",
            "ENABLE_MACRO",
            "ENABLE_WEATHER",
            "ENABLE_SCIENCE_TECH",
            "ENABLE_ENTERTAINMENT",
            "ENABLE_GEOPOLITICS",
            "ENABLE_OTHER",
        ]
        flags = {k: _b(g(k, "true"), True) for k in cat_keys}

        wallets_raw = g("copy_watch_wallets", "[]")
        try:
            wl = json.loads(wallets_raw) if wallets_raw else []
        except json.JSONDecodeError:
            wl = []
        wallets = []
        if isinstance(wl, list):
            for w in wl:
                w = str(w).strip().lower()
                if w and is_valid_polygon_address(w):
                    wallets.append(w)

        ui = g("ui_mode", "both").lower()
        if ui not in ("both", "dashboard", "terminal"):
            ui = "both"

        prof_name = g("strategy_profile", "balanced")
        prof = apply_profile(prof_name)

        bind_host = g("host", "0.0.0.0")
        if merge_os_environ:
            ev_bh = os.environ.get("PM_BIND_HOST")
            if ev_bh is not None and str(ev_bh).strip() != "":
                bind_host = str(ev_bh).strip()

        return cls(
            polymarket_private_key=g("polymarket_private_key", ""),
            wallet_address=g("wallet_address", ""),
            polymarket_signature_type=_i(g("polymarket_signature_type", "0"), 0),
            dry_run=_b(g("dry_run", "true"), True),
            host=bind_host,
            port=_i(g("port", "5002"), 5002),
            dashboard_secret=g("dashboard_secret", ""),
            default_bet_usd=_f(g("default_bet_usd", "5"), 5.0),
            min_bet_usd=_f(g("min_bet_usd", "1"), 1.0),
            max_bet_usd=_f(g("max_bet_usd", "25"), 25.0),
            scan_interval_seconds=_i(g("scan_interval_seconds", "120"), 120),
            min_clob_liquidity_usd=_f(g("min_clob_liquidity_usd", "500"), 500.0),
            min_gamma_volume=_f(g("min_gamma_volume", "1000"), 1000.0),
            max_trades_per_cycle=_i(g("max_trades_per_cycle", "1"), 1),
            strict_execution=_b(g("strict_execution", "true"), True),
            order_ttl_seconds=_i(g("order_ttl_seconds", "45"), 45),
            order_poll_seconds=_f(g("order_poll_seconds", "2.0"), 2.0),
            allow_market_fallback=_b(g("allow_market_fallback", "false"), False),
            trading_paused=_b(g("trading_paused", "false"), False),
            balance_buffer_usd=_f(g("balance_buffer_usd", "0"), 0.0),
            cex_require_dispersion=_b(g("cex_require_dispersion", "false"), False),
            structured_log=_b(g("structured_log", "true"), True),
            reconcile_enabled=_b(g("reconcile_enabled", "true"), True),
            reconcile_history_depth=_i(g("reconcile_history_depth", "15"), 15),
            reconcile_poll_sleep_s=_f(g("reconcile_poll_sleep_s", "0.06"), 0.06),
            open_orders_display_limit=_i(g("open_orders_display_limit", "40"), 40),
            agent_value=_b(g("agent_value", "true"), True),
            agent_copy=_b(g("agent_copy", "false"), False),
            agent_latency=_b(g("agent_latency", "false"), False),
            agent_bundle=_b(g("agent_bundle", "false"), False),
            agent_zscore=_b(g("agent_zscore", "false"), False),
            latency_min_dislocation_bps=_f(g("latency_min_dislocation_bps", "120"), 120.0),
            bundle_max_pair_cost=_f(g("bundle_max_pair_cost", "0.994"), 0.994),
            bundle_min_liquidity_usd=_f(g("bundle_min_liquidity_usd", "1500"), 1500.0),
            zscore_window=_i(g("zscore_window", "24"), 24),
            zscore_entry_abs=_f(g("zscore_entry_abs", "2.2"), 2.2),
            zscore_min_samples=_i(g("zscore_min_samples", "12"), 12),
            copy_watch_wallets=wallets,
            copy_poll_seconds=_i(g("copy_poll_seconds", "15"), 15),
            cex_gate_crypto=_b(g("cex_gate_crypto", "true"), True),
            max_cex_dispersion_bps=_f(g("max_cex_dispersion_bps", "25"), 25.0),
            category_flags=flags,
            ui_mode=ui,
            strategy_profile=str(prof.get("strategy_profile", prof_name)),
            value_yes_low=_f(g("value_yes_low", str(prof["value_yes_low"])), float(prof["value_yes_low"])),
            value_yes_high=_f(g("value_yes_high", str(prof["value_yes_high"])), float(prof["value_yes_high"])),
            value_no_yes_min=_f(g("value_no_yes_min", str(prof["value_no_yes_min"])), float(prof["value_no_yes_min"])),
            value_no_no_max=_f(g("value_no_no_max", str(prof["value_no_no_max"])), float(prof["value_no_no_max"])),
            value_liq_floor_usd=_f(g("value_liq_floor_usd", str(prof["value_liq_floor_usd"])), float(prof["value_liq_floor_usd"])),
            signals_enabled=_b(g("signals_enabled", "true"), True),
            pnl_sizing_enabled=_b(g("pnl_sizing_enabled", "true"), True),
            pnl_sizing_window=_i(g("pnl_sizing_window", "28"), 28),
            min_edge_bps=_i(g("min_edge_bps", "0"), 0),
            orderbook_gate_enabled=_b(g("orderbook_gate_enabled", "false"), False),
            orderbook_min_bid_share=_f(g("orderbook_min_bid_share", "0.38"), 0.38),
            ws_allow_anonymous=_b(g("ws_allow_anonymous", "false"), False),
        )

    @classmethod
    def load(cls) -> Settings:
        """Load from DB; falls back to env merge if DB empty (e.g. tests without init)."""
        try:
            from bot.db.kv import load_all_kv

            kv = load_all_kv()
            if kv:
                return cls.from_kv(kv, merge_os_environ=True)
        except Exception:
            pass
        return cls.from_kv(default_kv_seed(), merge_os_environ=True)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ui_mode": self.ui_mode,
            "dry_run": self.dry_run,
            "host": self.host,
            "port": self.port,
            "strict_execution": self.strict_execution,
            "agent_value": self.agent_value,
            "agent_copy": self.agent_copy,
            "agent_latency": self.agent_latency,
            "agent_bundle": self.agent_bundle,
            "agent_zscore": self.agent_zscore,
            "latency_min_dislocation_bps": self.latency_min_dislocation_bps,
            "bundle_max_pair_cost": self.bundle_max_pair_cost,
            "zscore_window": self.zscore_window,
            "zscore_entry_abs": self.zscore_entry_abs,
            "copy_wallets_n": len(self.copy_watch_wallets),
            "cex_gate_crypto": self.cex_gate_crypto,
            "max_cex_dispersion_bps": self.max_cex_dispersion_bps,
            "trading_paused": self.trading_paused,
            "balance_buffer_usd": self.balance_buffer_usd,
            "cex_require_dispersion": self.cex_require_dispersion,
            "structured_log": self.structured_log,
            "reconcile_enabled": self.reconcile_enabled,
            "reconcile_history_depth": self.reconcile_history_depth,
            "categories": {k: v for k, v in self.category_flags.items()},
            "strategy_profile": self.strategy_profile,
            "signals_enabled": self.signals_enabled,
            "pnl_sizing_enabled": self.pnl_sizing_enabled,
            "pnl_sizing_window": self.pnl_sizing_window,
            "min_edge_bps": self.min_edge_bps,
            "orderbook_gate_enabled": self.orderbook_gate_enabled,
            "orderbook_min_bid_share": self.orderbook_min_bid_share,
            "ws_allow_anonymous": self.ws_allow_anonymous,
        }
