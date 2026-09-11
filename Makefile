UV ?= uv

.DEFAULT_GOAL := check

.PHONY: install fmt lint typecheck imports jscheck test check portal bench live-providers

install:
	$(UV) sync
	$(UV) run pre-commit install

fmt:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .

typecheck:
	$(UV) run mypy

imports:
	$(UV) run lint-imports

jscheck:
	@echo "make jscheck: available from Phase 1"

test:
	$(UV) run pytest --cov --cov-report=term-missing
	$(UV) run coverage report --include="*/mendwork/engine/*" --fail-under=90
	$(UV) run coverage report --fail-under=85

check: lint typecheck imports jscheck test

portal:
	@echo "make portal: available from Phase 1"

bench:
	@echo "make bench: available from Phase 9"

live-providers:
	@echo "make live-providers: available from Phase 6"
