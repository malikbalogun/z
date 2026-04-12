# Agent / automation notes

This repo is meant to be **drivable from scripts** (local, CI, Codespaces, or an external bot) without guessing paths.

## One-command workspace

From repo root:

- `make setup` — create `.venv`, install `requirements.txt`, `mkdir data`, copy `config.json.example` → `config.json` if missing.
- `make test` — run unit tests (requires `make setup` first).
- `make run` — start the bot (`python main.py`).
- `make package` — build `polymarket_real_bot-YYYYMMDD-HHMM.zip` without `.venv`, `config.json`, or `data/*.db` (safe for VPS upload).

Shell equivalents: `bash scripts/setup_workspace.sh`, `bash scripts/package_for_vps.sh`.

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
- **Tests are fully offline**: `make test` runs 131 unit tests with no network or API keys needed.
