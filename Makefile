# ABOUTME: Local quality gates: `make check` (lint, format, types, unit tests) and `make test-e2e`.
# ABOUTME: test-e2e starts the docker compose Postgres and fails (not skips) if it is unreachable.

.PHONY: check lint fmt types test test-e2e

check: lint fmt types test

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format --check src tests

types:
	uvx ty check src/

test:
	uv run pytest -q -m "not integration"

test-e2e:
	docker compose up -d --wait db
	FEED_BRAIN_REQUIRE_PG=1 uv run pytest -q -m integration
