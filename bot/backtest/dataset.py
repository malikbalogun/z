"""Backtest dataset: schema + CSV loader + sha256 verification.

We intentionally use only the standard library (``csv``, ``hashlib``, ``gzip``)
so the backtest harness adds **zero new pip dependencies**. The original plan
allowed for Polars, but stdlib handles realistic snapshot sizes (tens to
hundreds of thousands of rows) in a few seconds and keeps the supply chain
audit-trivial — important given the malware story that motivated phase D
in the first place.

Schema (CSV header — the loader **fails fast** if any of these are missing):

    timestamp        ISO-8601 or epoch-seconds (parsed both ways).
    wallet           Polygon address, lowercased before storage.
    market_slug      Polymarket slug (e.g. ``btc-up-or-down-mar-26-2026``).
    condition_id     Polymarket condition id (kept opaque).
    token_id         CLOB token id for the side BUY-ed.
    category         Bot-side market category (must parse to MarketCategory).
    outcome          ``yes`` | ``no`` | ``unknown`` (lowercased on load).
    side             ``BUY`` only — SELLs are filtered out at load time
                     because the live copy agent only mirrors BUYs.
    price            entry probability, 0..1
    usdc             trade notional in USDC
    realized_pnl     final realized PnL of the wallet's *position*
                     (``''`` or unset = unresolved at snapshot time).
    title            free text question, used for keyword filters.

Optional columns are tolerated and ignored. Unknown rows (bad address,
unparseable price, side != BUY, missing token_id) are skipped with a
counter so the CLI can surface "skipped X of Y rows".

The dataset itself is **not** redistributed in this repo — see
``scripts/download_backtest_data.sh`` for the (license-checked, hash-pinned)
fetcher. ``tests/fixtures/backtest_mini.csv`` is a small synthetic dataset
checked in for offline tests only.
"""

from __future__ import annotations

import csv
import datetime as _dt
import gzip
import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger("polymarket.backtest.dataset")

SCHEMA_VERSION = "1"

# Required columns: loader fails if any are missing from the CSV header.
REQUIRED_COLUMNS = (
    "timestamp",
    "wallet",
    "market_slug",
    "condition_id",
    "token_id",
    "category",
    "outcome",
    "side",
    "price",
    "usdc",
    "realized_pnl",
    "title",
)


class DatasetError(Exception):
    """Raised when the dataset is malformed or fails schema/hash validation."""


@dataclass(frozen=True)
class TradeRow:
    """One historical trade, normalized for replay.

    Frozen so we can shove these in dicts/sets cheaply during simulation.
    """
    ts_epoch: float
    wallet: str
    market_slug: str
    condition_id: str
    token_id: str
    category: str
    outcome: str
    side: str
    price: float
    usdc: float
    realized_pnl: float | None
    title: str

    @property
    def date(self) -> _dt.date:
        return _dt.datetime.fromtimestamp(self.ts_epoch, _dt.timezone.utc).date()


@dataclass
class LoadStats:
    """Counters returned alongside parsed rows for visibility into skips."""
    total_rows: int = 0
    kept: int = 0
    skipped_bad_wallet: int = 0
    skipped_bad_price: int = 0
    skipped_bad_size: int = 0
    skipped_not_buy: int = 0
    skipped_missing_token: int = 0
    skipped_bad_timestamp: int = 0

    def as_dict(self) -> dict[str, int]:
        return {k: int(v) for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# Hash verification
# ---------------------------------------------------------------------------


def sha256_of_file(path: str | Path, *, chunk: int = 1 << 20) -> str:
    """Compute the SHA-256 of a file as a lowercase hex string."""
    h = hashlib.sha256()
    p = Path(path)
    with p.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def verify_sha256(path: str | Path, expected_sha256: str) -> None:
    """Raise DatasetError if the file's sha256 does not match `expected_sha256`.

    Comparison is case-insensitive and tolerates ``sha256:`` prefixes.
    """
    expected = (expected_sha256 or "").strip().lower()
    if expected.startswith("sha256:"):
        expected = expected.split(":", 1)[1]
    if not expected:
        raise DatasetError("expected_sha256 is empty")
    actual = sha256_of_file(path)
    if actual != expected:
        raise DatasetError(
            f"sha256 mismatch for {path}: expected={expected} actual={actual}"
        )


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def _open_csv(path: str | Path) -> io.TextIOBase:
    """Open the CSV for reading, handling both plain .csv and .csv.gz."""
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8", newline="")
    return p.open("r", encoding="utf-8", newline="")


def _parse_timestamp(raw: str) -> float | None:
    """Best-effort timestamp parser. Returns epoch seconds or None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Numeric epoch (seconds or millis).
    try:
        f = float(s)
        if f > 1e12:  # millis
            return f / 1000.0
        return f
    except ValueError:
        pass
    # ISO-8601, with or without trailing Z.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def _is_valid_polygon_address(s: str) -> bool:
    """Match the live validator without importing it (avoids a hard cycle)."""
    if not isinstance(s, str):
        return False
    s = s.strip().lower()
    if len(s) != 42 or not s.startswith("0x"):
        return False
    return all(c in "0123456789abcdef" for c in s[2:])


def load_trades(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> tuple[list[TradeRow], LoadStats]:
    """Load and normalize a backtest CSV.

    If `expected_sha256` is provided, the file is verified BEFORE parsing.

    Returns ``(rows_sorted_by_ts, stats)``. Bad/non-BUY rows are skipped and
    counted in ``stats`` so the CLI can warn.
    """
    p = Path(path)
    if not p.exists():
        raise DatasetError(f"dataset not found: {p}")
    if expected_sha256:
        verify_sha256(p, expected_sha256)

    stats = LoadStats()
    rows: list[TradeRow] = []
    with _open_csv(p) as f:
        reader = csv.DictReader(f)
        # Header gate
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise DatasetError(
                f"dataset {p} missing required columns: {', '.join(missing)}"
            )
        for raw in reader:
            stats.total_rows += 1
            # side gate first (cheapest)
            side = str(raw.get("side") or "").strip().upper()
            if side and side != "BUY":
                stats.skipped_not_buy += 1
                continue
            # timestamp
            ts = _parse_timestamp(raw.get("timestamp", ""))
            if ts is None:
                stats.skipped_bad_timestamp += 1
                continue
            # wallet
            w = str(raw.get("wallet") or "").strip().lower()
            if not _is_valid_polygon_address(w):
                stats.skipped_bad_wallet += 1
                continue
            # token_id (we need it to settle the position)
            token_id = str(raw.get("token_id") or "").strip()
            if not token_id:
                stats.skipped_missing_token += 1
                continue
            # numeric coercions
            try:
                price = float(raw.get("price") or 0)
            except (TypeError, ValueError):
                stats.skipped_bad_price += 1
                continue
            if not (0.0 < price < 1.0):
                stats.skipped_bad_price += 1
                continue
            try:
                usdc = float(raw.get("usdc") or 0)
            except (TypeError, ValueError):
                stats.skipped_bad_size += 1
                continue
            if usdc <= 0:
                stats.skipped_bad_size += 1
                continue
            # realized_pnl is optional ('' = unresolved)
            rp_raw = str(raw.get("realized_pnl") or "").strip()
            try:
                realized_pnl: float | None = float(rp_raw) if rp_raw else None
            except ValueError:
                realized_pnl = None
            row = TradeRow(
                ts_epoch=float(ts),
                wallet=w,
                market_slug=str(raw.get("market_slug") or "").strip().lower(),
                condition_id=str(raw.get("condition_id") or "").strip(),
                token_id=token_id,
                category=str(raw.get("category") or "OTHER").strip().lower(),
                outcome=str(raw.get("outcome") or "unknown").strip().lower(),
                side="BUY",
                price=price,
                usdc=usdc,
                realized_pnl=realized_pnl,
                title=str(raw.get("title") or ""),
            )
            rows.append(row)
            stats.kept += 1

    rows.sort(key=lambda r: r.ts_epoch)
    log.info(
        "backtest: loaded %d/%d rows (skipped %s)",
        stats.kept, stats.total_rows,
        ", ".join(f"{k}={v}" for k, v in stats.as_dict().items() if k.startswith("skipped_") and v),
    )
    return rows, stats


def iter_by_day(rows: Iterable[TradeRow]) -> Iterator[tuple[_dt.date, list[TradeRow]]]:
    """Yield ``(date, rows_for_that_date)`` pairs in chronological order."""
    bucket_date: _dt.date | None = None
    bucket: list[TradeRow] = []
    for r in rows:
        d = r.date
        if bucket_date is None:
            bucket_date = d
        if d != bucket_date:
            yield bucket_date, bucket
            bucket = []
            bucket_date = d
        bucket.append(r)
    if bucket_date is not None:
        yield bucket_date, bucket
