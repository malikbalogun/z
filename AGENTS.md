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
