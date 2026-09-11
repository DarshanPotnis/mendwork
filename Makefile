UV ?= uv
NPM ?= npm

.DEFAULT_GOAL := check

.PHONY: install fmt lint typecheck imports jscheck test check portal chaos-pairs bench live-providers

# Run `nvm use` first: .npmrc sets engine-strict, so npm ci fails loudly on the wrong Node.
install:
	$(UV) sync
	$(NPM) ci
	$(UV) run playwright install chromium
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
	$(NPM) exec --no -- tsc --project tsconfig.json

test:
	$(UV) run pytest --cov --cov-report=term-missing
	$(UV) run coverage report --include="*/mendwork/engine/*" --fail-under=90
	$(UV) run coverage report --fail-under=85

check: lint typecheck imports jscheck test

portal:
	$(UV) run python -m mendwork.apps.portal --root chaos-portal

# Rewrites benchmarks/chaos/heal_pairs.json from the portal's own declarations and selection.
chaos-pairs:
	$(UV) run python -m benchmarks.chaos.heal_pairs

bench:
	@echo "make bench: available from Phase 9"

live-providers:
	@echo "make live-providers: available from Phase 6"
