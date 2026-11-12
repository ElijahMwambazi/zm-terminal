# ZM Terminal

Zambia-first market intelligence terminal: React/Vite web, FastAPI API, PostgreSQL, OpenBB V5 behind the API. Architecture and roadmap live in README.md; don't repeat them here.

## Working style
- Act, don't narrate. Report only blockers, failures (with output), significant design decisions, and anything you did not verify.
- Final message: what changed (diff-stat level) and how it was verified. Never reprint files or full diffs.
- One task per session and per branch (`feat/...`, `fix/...`, `chore/...`). If a request is unrelated to the current task, say so and suggest /clear.
- Grep/Glob first, then Read with offset/limit. No directory sweeps. Don't re-read a file you just edited or re-run a check that passed on unchanged code.
- No subagents for lookups that take 1-3 searches. Use the `verifier` agent when check output would be noisy.
- Commit and push only when asked. Open a PR only when asked.

## Commands
Run only the checks for the side you touched; pipe long output through `tail -30`.
- API: `cd apps/api && python -m pytest -q` (offline: needs no Postgres, OpenBB or BoZ)
- Web: `cd apps/web && npm install && npm run build` (runs `tsc -b`, then `vite build`)
- CI (`.github/workflows/ci.yml`) runs exactly these two.
- Don't run `docker compose up`, `make up` or `make api-test` unless asked: the OpenBB image build is slow, and `api-test` starts Postgres and OpenBB first.

## Layout
- `apps/api/app/api/router.py`: all HTTP routes under `/api/v1`
- `apps/api/app/services/`: one module per data provider (`boz.py`, `openbb.py`)
- `apps/api/app/schemas/`: response models (`fx.py`)
- `apps/api/app/config.py`, `db.py`: pydantic-settings config; lazy SQLAlchemy engine
- `apps/api/tests/`: pytest with mocked HTTP; fixtures in `tests/fixtures/`
- `apps/web/src/`: React UI (`App.tsx`, `api.ts`); talks only to the FastAPI API
- `db/init/`: Postgres bootstrap SQL. `docker/openbb/`: OpenBB API image.

## Rules
- Never display or fabricate prices. Unavailable data shows as "—" or an explicit error.
- Every price keeps `source` and `observed_at`. Money is `Decimal` (JSON decimal strings), never `float`.
- Provider details stay out of the frontend. OpenBB stays behind the API. Zambia-specific providers (BoZ, LuSE) are first-class adapters, not routed through OpenBB.
- New provider adapters fail loudly on malformed upstream data (see `services/boz.py`) and get offline tests with a small fixture. Tests never call the live network.

## Gotchas
- `db/init/*.sql` runs only on an empty Postgres volume. Edits and new seed rows don't reach an existing DB, and `make clean` wipes data.
- `npm install` creates untracked `apps/web/package-lock.json` and `*.tsbuildinfo` files; no lockfile is committed. Don't commit them unless asked.
- `compose.yml` publishes ports 5432, 6900, 8000 and 5173 on all interfaces with dev credentials. Keep that in mind for anything exposed beyond localhost.
