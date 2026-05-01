#!/usr/bin/env python3
"""Run the offline copy-trading backtest.

Usage:

    python scripts/run_backtest.py \
        --dataset data/backtest/trades.csv.gz \
        --start 2026-01-01 --end 2026-04-01 \
        --min-wallet-score 0.4 \
        --report data/backtest/report.json

Notes:

* The dataset is **not** vendored in this repo. Use
  ``bash scripts/download_backtest_data.sh`` first (or pass ``--dataset``
  to a CSV/CSV.gz of your own).
* For unit tests we run against ``tests/fixtures/backtest_mini.csv`` so
  CI never hits the network.
* Output: writes both ``REPORT.json`` and ``REPORT.md`` next to each other,
  using the path from ``--report`` (extension is normalized).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
from pathlib import Path

# Allow running both from repo root (python scripts/run_backtest.py)
# and as a module (python -m scripts.run_backtest) without sys.path tweaks.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bot.backtest.dataset import DatasetError, load_trades
from bot.backtest.replay import ReplayConfig, run_replay
from bot.backtest.report import render_markdown, to_dict, write_json, write_markdown


def _parse_date(s: str | None) -> _dt.date | None:
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s)
    except ValueError as e:
        raise SystemExit(f"--start/--end: invalid YYYY-MM-DD value: {s!r} ({e})")


def _split_csv_arg(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip().lower() for x in s.split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_backtest",
        description="Replay historical Polymarket trades through the copy-trading pipeline.",
    )
    p.add_argument("--dataset", default="tests/fixtures/backtest_mini.csv",
                   help="Path to .csv or .csv.gz (default: mini fixture)")
    p.add_argument("--sha256", default=None,
                   help="If set, dataset must match this sha256 before loading")
    p.add_argument("--start", default=None, help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--end", default=None, help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--initial-balance", type=float, default=500.0)
    p.add_argument("--follower-size-usd", type=float, default=5.0)
    p.add_argument("--min-win-rate", type=float, default=0.60)
    p.add_argument("--min-total-trades", type=int, default=5)
    p.add_argument("--min-wallet-score", type=float, default=0.0)
    p.add_argument("--copy-min-usd", type=float, default=0.0)
    p.add_argument("--copy-max-usd", type=float, default=0.0)
    p.add_argument("--copy-min-price", type=float, default=0.0)
    p.add_argument("--copy-max-price", type=float, default=1.0)
    p.add_argument("--allowed-categories", default="",
                   help="Comma-separated whitelist (default: all categories)")
    p.add_argument("--blocked-keywords", default="",
                   help="Comma-separated blocked title keywords")
    p.add_argument("--manual-wallet", action="append", default=[],
                   help="Pin a wallet (repeatable). Bypasses WR/score gates.")
    p.add_argument("--report", default="data/backtest/report.json",
                   help="Output path for the JSON report (markdown is written next to it)")
    p.add_argument("--verbose-trades", action="store_true",
                   help="Log every accepted trade. Noisy on real datasets.")
    p.add_argument("--print-summary", action="store_true",
                   help="Echo the markdown summary to stdout when done")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("polymarket.backtest.cli")

    try:
        rows, stats = load_trades(args.dataset, expected_sha256=args.sha256)
    except DatasetError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    log.info("loaded %d/%d rows from %s", stats.kept, stats.total_rows, args.dataset)

    cfg = ReplayConfig(
        start_date=_parse_date(args.start),
        end_date=_parse_date(args.end),
        initial_balance=args.initial_balance,
        follower_size_usd=args.follower_size_usd,
        min_win_rate=args.min_win_rate,
        min_total_trades=args.min_total_trades,
        min_wallet_score=args.min_wallet_score,
        copy_min_usd=args.copy_min_usd,
        copy_max_usd=args.copy_max_usd,
        copy_min_price=args.copy_min_price,
        copy_max_price=args.copy_max_price,
        copy_allowed_categories=_split_csv_arg(args.allowed_categories),
        copy_blocked_keywords=_split_csv_arg(args.blocked_keywords),
        manual_wallets=[w.lower() for w in args.manual_wallet],
        verbose_trades=args.verbose_trades,
    )
    result = run_replay(rows, cfg)

    json_path = Path(args.report)
    if json_path.suffix.lower() != ".json":
        json_path = json_path.with_suffix(".json")
    md_path = json_path.with_suffix(".md")

    payload = write_json(result, json_path)
    md_text = write_markdown(result, md_path)

    log.info(
        "wrote %s (%d bytes) and %s (%d bytes)",
        json_path, len(json.dumps(payload)),
        md_path, len(md_text),
    )

    print(f"\nReport: {json_path}\nMarkdown: {md_path}")
    print(
        f"Accepted {result.total_accepted:,} of {result.total_evaluated:,} trades "
        f"-> gross PnL ${result.gross_pnl:,.2f} on ${cfg.initial_balance:,.2f} "
        f"(hit rate {result.hit_rate:.1%}, max drawdown ${result.max_drawdown:,.2f})"
    )

    if args.print_summary:
        print()
        print(md_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
