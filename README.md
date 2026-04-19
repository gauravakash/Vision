# Vision — X Agent

Multi-account X (Twitter) content management platform. FastAPI backend + React/Vite dashboard + Telegram command bot, powered by xAI Grok for trend fetching and draft generation.

## Architecture

```
┌─────────────┐      ┌─────────────┐      ┌──────────────┐
│  Vite 5173  │──/api┤ FastAPI 8000│──────│ SQLite (aio) │
│  React UI   │ prox │ Uvicorn     │      │ ./xagent.db  │
└─────────────┘      │  • routers  │      └──────────────┘
                     │  • scheduler│
                     │  • agent    │      ┌──────────────┐
                     │  • notifier │──────│ Telegram bot │
                     └─────────────┘      │  (polling)   │
                            │             └──────────────┘
                            └─── xAI Grok (trends + drafts)
```

- **[backend/](backend/)** — FastAPI app. Entry [backend/main.py](backend/main.py). Routers under [backend/routers/](backend/routers/). Config via [backend/config.py](backend/config.py) (pydantic-settings, reads `.env`).
- **[frontend/](frontend/)** — Vite + React. Pages in [frontend/src/pages/](frontend/src/pages/): Home, Review, Accounts, Desks, History, Engagement, Threads, Settings.
- **Telegram bot** — [backend/telegram_bot.py](backend/telegram_bot.py). Inline-keyboard menus replace the web UI for mobile operation. Commands: `/start /r /run /trending /stats /pause /resume /help`.

## Prerequisites

- Python 3.13 (repo ships a venv at `.venv-mac/`)
- Node.js 24+
- `.env` at repo root with at minimum:
  ```
  XAI_API_KEY=...
  SECRET_KEY=...
  TELEGRAM_BOT_TOKEN=...        # optional
  TELEGRAM_CHAT_ID=...          # optional
  DATABASE_URL=sqlite+aiosqlite:///./xagent.db
  ```

## Running locally

Two processes. Run in separate terminals.

```bash
# Backend (port 8000)
.venv-mac/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Frontend (port 5173, proxies /api → :8000)
cd frontend && npm run dev
```

Then open http://localhost:5173. Swagger at http://localhost:8000/docs, route index at http://localhost:8000/.

### Docker

`docker-compose.local.yml` brings up backend, frontend (nginx-served build), and an nginx gateway on port 8080.

## Running the Telegram bot

The bot starts automatically inside the FastAPI lifespan when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set. It uses long-polling — no webhook or public URL needed. Verify on startup:

```
INFO backend.telegram_bot  Telegram bot started: @yourbot
```

Menu flow verified end-to-end (see `/tmp/test_tg.py` for the mock-driven harness that exercises every command + callback without hitting Telegram).

## Known quirks / gotchas

- **`xai` import shim.** The code uses `import xai` but the real PyPI package is `xai-sdk` (module `xai_sdk`). `requirements.txt` pins `xai>=0.1.0,<0.2.0`, which is an unrelated scientific library that also fails to build on Python 3.13. Workaround in place: `xai-sdk` installed, plus a one-line shim at [.venv-mac/lib/python3.13/site-packages/xai.py](.venv-mac/lib/python3.13/site-packages/xai.py) that aliases `xai` → `xai_sdk`. Permanent fix: change `requirements.txt` to `xai-sdk` and rewrite `import xai` → `import xai_sdk as xai` throughout the backend, then delete the shim.
- **Silent router-import failures.** [backend/main.py](backend/main.py) catches `ImportError` during router registration at DEBUG level. If `/api/drafts`, `/api/agent`, `/api/threads`, or `/api/lingo` return 404, a router failed to import — check the log or run:
  ```bash
  .venv-mac/bin/python -c "from backend.routers import drafts, agent, threads, lingo"
  ```
- **`vite` bin missing executable bit after npm install in some cases.** Symptom: `sh: .../node_modules/.bin/vite: Permission denied` or a truncated shim (`../vite/bin/vite.js` as file contents). Fix: `rm -rf frontend/node_modules frontend/package-lock.json && (cd frontend && npm install)`.
- **Docker-based Playwright can't reach `localhost`.** If driving the UI from a containerised browser (e.g. the Playwright MCP), bind Vite to all interfaces and whitelist the docker hostname:
  ```js
  // frontend/vite.config.js
  server: {
    port: 5173,
    allowedHosts: ['host.docker.internal', 'localhost', '127.0.0.1'],
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  }
  ```
  Start with `npm run dev -- --host 0.0.0.0` and browse `http://host.docker.internal:5173`.
- **Double `@` in account handles.** [backend/telegram_bot.py](backend/telegram_bot.py) formats handles as `f"@{account.handle}"` but the `accounts.handle` column already stores the `@` prefix. Cosmetic only.
- **Trend staleness.** `/trending` uses a 2-hour cutoff; Home uses 7-day. "No recent trends" means nothing in the last 2h, not an error.

## Useful endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | Dev dashboard (HTML route index) |
| `GET /health` | DB ping for load-balancer probes |
| `GET /api/admin/health` | Full subsystem health |
| `GET /docs` · `GET /redoc` | Swagger / ReDoc |
| `/api/desks` · `/api/accounts` · `/api/drafts` · `/api/agent` · `/api/threads` · `/api/engagement` · `/api/scheduler` · `/api/lingo` · `/api/admin` | Feature routers |

## Directory layout

```
backend/           FastAPI app
  routers/         HTTP route groups
  models.py        SQLAlchemy 2.0 ORM
  agent.py         Grok-backed draft generator
  scheduler.py     APScheduler jobs (trend fetch, spike check, briefings)
  telegram_bot.py  Command-center bot
  notifier.py      Outbound Telegram alerts
frontend/          Vite + React dashboard
  src/pages/       One file per route
data/              SQLite file lives here under Docker
logs/              App logs
requirements.txt   Python deps (note: `xai` pin is wrong — see quirks)
docker-compose.local.yml  Local full-stack compose
```
