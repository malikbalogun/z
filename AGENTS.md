# AGENTS.md

## Cursor Cloud specific instructions

### Overview

Polymarket multi-agent automated trading bot — a single Python 3.12 application with a FastAPI web dashboard (port 5002), SQLite database, and Rich terminal UI. No Docker, no external databases, no JS build step required.

### Running services

- **Start the bot + web dashboard:** `source .venv/bin/activate && python main.py --ui dashboard`
  - Runs on `http://0.0.0.0:5002` by default
  - The bot will log CLOB key errors at startup — this is expected without a `POLYMARKET_PRIVATE_KEY`. The web UI still works.
- **Default admin credentials** (created on first run): username `admin`, password from `config.json` → `initial_admin_password` (default: `change-me-immediately`)

### Testing

- **Run all tests:** `source .venv/bin/activate && python -m pytest tests/ -v`
  - Tests are offline smoke tests — no network or API keys needed.

### Linting

- No linting configuration is committed. `ruff check .` can be used; there are a few pre-existing unused-import warnings in `server.py`.

### Key caveats

- `config.json` is auto-created from `config.json.example` on first run if missing. No manual copy required.
- The `data/` directory (SQLite DB, uploads) is created automatically.
- The bot's trading settings and API keys are managed via the Admin UI (`/admin`), not `.env`. The `.env.example` is legacy/optional.
- The bot runs in `DRY_RUN=true` mode by default — no real trades are placed without valid Polymarket keys.
