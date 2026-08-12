# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**`AGENTS.md` in the repo root is the authoritative, detailed agent guide** (module boundaries, import bans, i18n, cross-platform test rules, release workflow). Read it before non-trivial changes; this file is the short version.

## What this is

**Octop** — self-hosted AI assistant platform (multi-user, multi-agent). One Python wheel containing a FastAPI backend, a React dashboard, and a Click CLI. Single process, no external queue; state rebuilds from `~/.octop/octop.db` on restart.

- Backend: Python 3.12+, FastAPI + uvicorn, asyncio (no threads except `run_in_executor`)
- DB: SQLite via sync `sqlite3` (WAL) **or** PostgreSQL via `psycopg`
- Agent runtime: `harness-agent` (LangGraph); IM bridge: `harness-gateway`
- Frontend: React 18 + TypeScript + Vite, source in `dashboard/`
- Package manager: **uv** — always `uv run pytest`, never bare `pytest`

## Commands

```bash
make install-hooks        # once per clone — pre-commit runs make all + dashboard build
make all                  # format-all + lint + typecheck + test  ← THE SHIP BAR
make lint                 # ruff check + format check
make typecheck            # mypy --strict src/octop
uv run pytest -m "not live"                      # full suite (no LLM calls)
uv run pytest tests/unit -x -q                   # unit only
uv run pytest tests/unit/<path>::<test_name>     # single test
cd dashboard && npx tsc --noEmit                 # frontend typecheck after UI changes
make build-frontend       # dashboard/ → src/octop/dashboard/ (build artifact)
make dev-frontend         # Vite dev server (:5173)
make dev-backend          # octop run
```

- Never bypass the pre-commit hook to land red tests; CI runs on Linux **and** Windows.
- Live LLM tests: `uv run pytest -m live`.

## Architecture

```
dashboard/ ──HTTP──► api/ ──► infra/ ──► infra/utils/, config.py
cli/ ──► launch.py ──► api/ + infra/
```

- `src/octop/launch.py` — composition root; the only module importing both `infra/server` and `api/app`.
- `src/octop/infra/` — domain core: `agents/`, `gateway/` (IM ingress), `cron/`, `db/` (repos, migrations, `SharedServices`), `users/`, `connectors/`, `backend/` (workspace storage), `setup/` (wizard, TLS), `server.py` (wires singletons).
- `src/octop/api/` — thin FastAPI routers: validate HTTP, call `infra/`, map errors. No business rules.
- `src/octop/cli/` — Click commands; three transport modes: **offline** (local `~/.octop` SQLite via repos), **embedded** (boots in-process `OctopServer` for runtime ops), **external** (OS/daemon directly).
- `dashboard/` — frontend source. `src/octop/dashboard/` is a build artifact — **never edit it directly**; run `make build-frontend` after UI changes.
- `src/octop/i18n/` — backend locale bundles (`en`, `zh`); server-produced user-facing text must use these, not hard-coded English.

**Dependency rule:** inward only. `infra/` must not import `api/`, `cli/`, or `launch.py`; `api/` must not import `cli/`; routers must not import repos directly (use `server.services` / `SharedServices`).

## Key invariants (see AGENTS.md for the rest)

- **Agent workspace I/O** goes through `HarnessAgent.workspace` (`BackendWorkspace`) with workspace-relative paths — never `Path.write_text` on `~/.octop/agents/<id>/`, never branch on backend type in Octop code.
- **DB migrations**: add `infra/db/migrations/00N_*.sql` **and** matching `00N_*.pg.sql` (PostgreSQL); bump the version assertion in `tests/unit/db/test_db_pool.py`.
- **i18n**: new user-facing strings go in both `en.json` and `zh.json` (+ matching dashboard `apiErrors` for error codes); verify with `uv run pytest tests/unit/i18n -q`.
- **Timezone**: user-facing datetimes use server `default_timezone` (via `useServerTimezone()` / `formatServerDateTime` on the frontend), never bare `toLocaleString()`.
- **Cross-platform tests**: guard POSIX-only cases with `pytest.mark.skipif(os.name != "posix", …)`; use `tmp_path` / `pathlib.Path`, never hard-coded `/` paths; set `OCTOP_HOME` to `tmp_path` in tests that materialize files.
- **Legacy imports are banned** (`octop.agents.*`, `octop.db.*`, `octop.utils.*`, …) — full mapping in AGENTS.md §8.

## Branching

Feature work branches from and PRs into **`develop`** (not `main`). `main` is production-only via `release/x.y.z` PRs; `v*` tags are created on `main` tip by CI after merge — never tag from a release/feature branch. Full release flow: `.cursor/skills/publish/SKILL.md`.

## Communication

Per AGENTS.md §11: default to **Chinese** when talking to the user, lead with the conclusion, cite code as `path:line`, and include verification evidence when marking work done.
