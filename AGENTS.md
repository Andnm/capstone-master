# AGENTS.md

Guidance for AI coding assistants and human contributors. Read this before changing code.

## Project

Hotel price monitoring and short-term forecasting for Vietnam (Master's thesis). A crawler collects public Booking.com listing prices every day for a fixed cohort of hotels in five cities; the data is merged into a versioned warehouse, analysed, and used to forecast price movement 1, 3, 7 and 14 days ahead. Regression is the core deliverable; classification is a stretch goal.

Scope: Booking.com only; Ho Chi Minh City, Hanoi, Vung Tau, Da Lat, Phu Quoc; one night, 2 adults, 0 children, 1 room, VND, anonymous session. Public data only, for academic use. No bookings, no accounts, no personal data.

## Repository map

```
hotel-price-intelligence/
  backend/
    main.py                  FastAPI app (router under /api/scraper)
    app/api/                 HTTP endpoints (upload, preflight, runs, items, export, worker health)
    app/core/                settings (pydantic-settings, reads .env) and DB connection
    app/database/            setup.sql (schema), migrations/, durable queue + repositories,
                             anomaly_registry.json (append-only decision log)
    app/scraper/             crawler: booking_scraper, parser, transform, reference (room/rate keys),
                             worker + worker_supervisor, network circuit breaker, errors taxonomy
    app/warehouse/           versioned warehouse build: staging, importer, canonicalize, curated keys,
                             reference_builder, validation, registry, promote
    scripts/                 CLI entry points (crawl, quality monitor, anomaly, warehouse build)
    tests/                   pytest suite
  eda/
    src/                     EDA library (read-only SQL, metrics, plots, report, contracts)
    notebooks/               notebooks, executed only through run_wave_a.py
    src/tests/               EDA tests
  scraper-frontend/          Next.js operations console (has its own AGENTS.md: read it first)
  dashboard-frontend/        placeholder, not built yet
```

The layers are strictly ordered: **operational DB -> warehouse -> EDA -> features/labels -> models**. Each layer reads the one before it and never writes back.

## Commands

Backend, from `hotel-price-intelligence/backend` (use the project virtualenv):

```bash
python main.py                                      # API on :8000
python scripts/run_worker.py                        # crawler worker (supervisor + child process)
python -m pytest tests -q                           # unit tests; no database needed
WAREHOUSE_SMOKE=1 python -m pytest tests -q         # also runs MySQL-backed warehouse tests (throwaway databases)
python scripts/daily_quality_monitor.py --source-code <name> --latest   # health check of one crawl run
```

Warehouse (each step is its own script; all verify inputs and fail closed):

```bash
python scripts/init_warehouse_db.py --database warehouse_<name> [--dry-run]
python scripts/build_warehouse.py --database ... --source-manifest ... --cohort-manifest ... --ownership-manifest ...
python scripts/validate_warehouse.py --database ... --batch-id ...
python scripts/promote_warehouse.py --database ... --batch-id ...
```

EDA, from `hotel-price-intelligence/eda` with its own environment (`requirements-eda.txt`):

```bash
python run_wave_a.py                                # the only supported way to execute the notebooks
python -m pytest src/tests -q -m "not integration"
EDA_SMOKE=1 python -m pytest src/tests -q           # includes MySQL-backed tests
```

Frontend: `npm install && npm run dev` in `scraper-frontend/`.

## Core invariants

1. **Two time axes.** Every observation has `observed_at` (UTC) and `checkin_date`. `lead_time = checkin_date - observation date in Asia/Ho_Chi_Minh`. Never collapse them.
2. **Time zone.** The database stores UTC (use `utc_now_naive()`); display and "which day was this crawled" use Asia/Ho_Chi_Minh. Ad-hoc queries that read run timestamps must pin the session time zone to `+00:00`.
3. **Lag/rolling features hold `checkin_date` fixed** and only move `observed_at`. Pooling check-in dates in one history is leakage.
4. **Splits are chronological**, with a purge gap equal to the largest horizon (14 days) at each boundary. No random `train_test_split`. Fit preprocessing on train only.
5. **Sold out is not price 0.** Record `availability_status = sold_out` with a NULL price.
6. **Comparable series only.** Compare prices within the same hotel, check-in date and room/rate-plan identity. `room_identity_key` and `rate_plan_key` come from stable attributes (see `app/scraper/reference.py`), never from price or DOM order. A series is trainable only after it is approved from repeated, complete crawls.
7. **Raw data is never deleted to fix a problem.** Flag or exclude; keep the record. `is_anomaly` is a projection of reviewed decisions (`anomaly_registry.json`, append-only), not the output of a rule. Rules only produce candidate signals.
8. **Crawl completeness and reference availability are independent.** A parser-complete item is not downgraded because a reference is unavailable.
9. **Fail closed.** In warehouse code any inconsistency raises a `WarehouseError`; do not guess or auto-repair. The warehouse reads sources read-only and verifies manifests/checksums on every use. Same inputs must rebuild to the same checksums.
10. **MySQL booleans come back as `int` (0/1), not `bool`.** Hashing or comparing raw driver values silently changes keys. In EDA use the boolean coercion in `eda/src/contracts.py`; do not sprinkle `.astype(bool)`.
11. **Crawler courtesy.** Randomised delays, public pages only, no logins, no CAPTCHA bypass.

## Conventions

- Scripts that write data are dry-run by default and need an explicit `--apply`. Keep it that way.
- Schema changes go in a new file under `app/database/migrations/`; never edit an applied migration. Keep `setup.sql` in sync.
- Bump `SCRAPER_VERSION` / `SELECTOR_VERSION` in `app/core/config.py` when parsing or selector behaviour changes, so data stays traceable to the code that produced it.
- Put decisions in pure, unit-testable functions; scripts are thin CLI wrappers. Tests use fakes or fixtures, not the live database. MySQL-backed tests are opt-in (`WAREHOUSE_SMOKE=1`, `EDA_SMOKE=1`).
- Add or update tests with every behavioural change. The baseline is a green `pytest tests`.
- Fix random seeds in ML experiments; record hyperparameters in config files, not code.
- Notebooks are for exploration; code that runs for real lives in `.py` modules. Notebooks are executed through `run_wave_a.py`, which writes outputs to a new directory and never overwrites a previous analysis.
- Comments and docs are a mix of Vietnamese and English; match the file you are editing. Default to few comments, only for non-obvious constraints.
- Prefer the simplest solution that works. Do not add features, abstractions or error handling the task does not need.

## Working with AI on this repository

The project is built with AI assistants under a separation of roles:

- **Builder** implements a change in small steps and states how it was verified.
- **Reviewer** is an independent model with no shared context. It reviews the design and the diff and is expected to check claims against the real data and tests rather than trust the builder's summary.
- **A change is done only when** the reviewer agrees *and* it has run correctly against real data and the test suite. A claim that something "passes" is not evidence; re-run it.
- **The maintainer decides.** Assistants propose and challenge; scope, trade-offs and what is accepted are the maintainer's call. If a requirement is ambiguous, ask instead of guessing.

Do not commit, push, or rewrite history unless the maintainer asks. Do not add attribution trailers to commits.

## Do not

- Commit raw data, database dumps, spreadsheets of collected prices, uploaded files, crawl artifacts, secrets or `.env`.
- Run destructive database or git commands unprompted.
- Write to a source database from warehouse or EDA code.
- Change the sampling protocol, cohort, or label definitions as a side effect of another task.
