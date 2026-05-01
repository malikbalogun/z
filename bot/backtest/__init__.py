"""Offline backtest harness for the copy-trading pipeline.

This subpackage lets you point ``scripts/run_backtest.py`` at a historical
trade dataset (vendored separately via ``scripts/download_backtest_data.sh``)
and ask: "if we had used these settings on the last N days, what would have
happened?"

Public surface (kept small on purpose):

    bot.backtest.dataset.load_trades(path)       -> list[TradeRow]
    bot.backtest.dataset.verify_sha256(path, sha)
    bot.backtest.replay.run_replay(trades, cfg)  -> ReplayResult
    bot.backtest.report.write_json(result, path) -> dict
    bot.backtest.report.write_markdown(result, path) -> str

Everything is plain stdlib + dataclasses + the existing
``bot.wallet_scoring.wallet_score_v2`` and ``bot.copy_rules.passes_filters``
so the backtest exercises the *same code path* the live bot uses for copy
decisions. No new top-level dep; we deliberately did NOT add Polars in the
end because the slim CSV reader plus pure-Python scoring is fast enough on
realistic datasets (tens of thousands of rows) and avoids a heavy install.
"""

from bot.backtest.dataset import (
    DatasetError,
    SCHEMA_VERSION,
    TradeRow,
    load_trades,
    verify_sha256,
)
from bot.backtest.replay import (
    ReplayConfig,
    ReplayResult,
    run_replay,
)
from bot.backtest.report import (
    write_json,
    write_markdown,
)

__all__ = [
    "DatasetError",
    "SCHEMA_VERSION",
    "TradeRow",
    "load_trades",
    "verify_sha256",
    "ReplayConfig",
    "ReplayResult",
    "run_replay",
    "write_json",
    "write_markdown",
]
