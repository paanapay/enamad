# Architecture Guide

This repository is currently a modular monolith in transition.

## Current State

- Runtime components exist mostly at repository root (`webapp.py`, `db.py`, `extract_enamad.py`, bots, scheduler).
- CRM is multi-project capable, while domains remain shared globally.
- Flask templates/static are already grouped under `templates/` and `static/`.

## Target Structure (Incremental)

The package scaffold now exists at `src/enamad/`:

- `src/enamad/core/` shared cross-cutting wrappers (config/logging)
- `src/enamad/web/` app factory and web composition
- `src/enamad/cli.py` package entrypoint

Future migrations should move logic by domain:

- `scraper/` scraping and refresh workflows
- `bots/` platform adapters + shared handlers
- `data/` repositories/query modules
- `scheduler/` job definitions and runner

## Rules for New Changes

1. Keep behavior-preserving refactors separate from feature work.
2. Prefer adding new code under `src/enamad/*` and call legacy modules through thin wrappers first.
3. Add tests for each refactor slice to avoid regressions.
4. Avoid adding more top-level runtime files unless absolutely required.

## Migration Phases

1. **Stabilize:** wrappers + smoke tests + package entrypoints.
2. **Extract:** split large modules into domain packages without changing APIs.
3. **Harden:** expand integration tests and enforce boundaries.
