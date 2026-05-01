# Agent / automation notes

This repo is meant to be **drivable from scripts** (local, CI, Codespaces, or an external bot) without guessing paths.

## One-command workspace

From repo root:

- `make setup` — create `.venv`, install `requirements.txt`, `mkdir data`, copy `config.json.example` → `config.json` if missing.
- `make test` — run unit tests (requires `make setup` first).
- `make run` — start the bot (`python main.py`).
- `make package` — build `polymarket_real_bot-YYYYMMDD-HHMM.zip` without `.venv`, `config.json`, or `data/*.db` (safe for VPS upload).
- `make backtest` — run the offline copy-trading backtest harness against the vendored mini fixture (no network). Writes `data/backtest/report.{json,md}`. Override the dataset with `BACKTEST_DATASET=path/to/trades.csv.gz make backtest`.
- `make backtest-data` — fetch + sha256-verify a third-party trade snapshot. Requires `PM_BACKTEST_DATA_URL` and `PM_BACKTEST_DATA_SHA256` env vars (no default URL is baked in).

Shell equivalents: `bash scripts/setup_workspace.sh`, `bash scripts/package_for_vps.sh`, `bash scripts/download_backtest_data.sh`, `python scripts/run_backtest.py --help`.

## Secrets and config

- **Never commit** `config.json` (gitignored). It holds `session_secret` and bootstrap admin; create from `config.json.example`.
- Trading keys live in **SQLite** via Admin after first boot, not in `config.json`.
- Optional bind override for the HTTP server: env `PM_BIND_HOST` (e.g. `0.0.0.0` on a VPS).

## Remote editing “from anywhere”

- **GitHub Codespaces / VS Code Dev Containers**: open the repo in a Codespace; `.devcontainer/devcontainer.json` runs `scripts/setup_workspace.sh` on create and forwards port **5002**.
- **SSH + git on a VPS**: clone this repo on the server, run `make setup`, edit with `vim`/Remote-SSH, `git pull` to deploy updates.

## External automation (e.g. Clawbot)

Prefer invoking **fixed commands** above from the machine where the workspace lives (or from CI), rather than pasting SSH private keys into chat. If Clawbot can run shell on a host that has this repo cloned, wire it to: `make setup`, `make test`, `make package`, or `git pull && make setup && make test`.

## Cursor Cloud specific instructions

- **System dep**: `python3.12-venv` is not pre-installed in the Cloud Agent VM. The update script handles it via `apt-get install -y python3.12-venv`.
- **Lint**: No dedicated linter is configured in this repo (no `ruff`, `flake8`, or `mypy` in `requirements.txt`). Tests are the primary quality gate: `make test`.
- **Running the app**: `make run` (or `source .venv/bin/activate && python main.py --ui dashboard`). The web dashboard listens on port **5002**. Default bootstrap admin credentials are in `config.json` (`admin` / `change-me-immediately`).
- **No external services required for dev**: SQLite is embedded; `config.json` is auto-created from `config.json.example` by `make setup`. The bot starts in **DRY_RUN** mode and runs without Polymarket API keys (it just logs "waiting for valid keys" and retries every ~12 s).
- **Tests are fully offline**: `make test` runs 180 unit tests with no network or API keys needed.

## Backtest harness (Phase D)

- **Goal**: replay historical wallet trades through the *same* `wallet_score_v2` + `passes_filters` code path the live bot uses for copy decisions, so changes to scoring/heuristics can be measured before they ship.
- **Module**: `bot/backtest/` (pure stdlib — no Polars/pandas/numpy dep, deliberately, to keep the supply chain tight).
- **Fixture**: `tests/fixtures/backtest_mini.csv` is a 43-row synthetic dataset (1 winner wallet + 1 loser wallet across 4 days). Tests assert deterministic numbers against it.
- **Real dataset**: not vendored. Use `make backtest-data` with `PM_BACKTEST_DATA_URL` + `PM_BACKTEST_DATA_SHA256` env vars to fetch the third-party `poly_data` (warproxxx, MIT) snapshot. The script aborts on hash mismatch.
- **Outputs**: `data/backtest/report.json` (canonical, schema_version=1) + `data/backtest/report.md` (human summary). Both are gitignored.
- **CLI**: `python scripts/run_backtest.py --help` lists every Settings-mirrored knob (`--min-win-rate`, `--min-wallet-score`, `--allowed-categories`, `--manual-wallet`, …).
- **Look-ahead protection**: when scoring wallet W on day D the engine only sees W's trades with `ts < first_ts_of_day_D`. Tested via `TestReplayLookAheadProtection`.
