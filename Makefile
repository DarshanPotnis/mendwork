UV ?= uv
NPM ?= npm

.DEFAULT_GOAL := check

.PHONY: install fmt lint typecheck imports jscheck test test-all check check-all schema portal chaos-pairs recording-golden bench live-providers

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

# Coverage gates (ADR 0007). The full suite's gates are the contract, enforced by CI through
# check-all. The fast suite's gates sit just below what it measures, as an early warning.
FULL_SUITE := full suite, make check-all: the contract
FAST_SUITE := fast suite, make check: an early warning, not the contract

# $(1) suite label, $(2) engine gate, $(3) engine/domain gate, $(4) overall gate
define coverage_gates
	@echo "Coverage gates for the $(1)"
	@printf '  engine         gate %s%%, measured ' '$(2)'; $(UV) run coverage report --include="*/mendwork/engine/*" --format=total --precision=2 --fail-under=$(2)
	@printf '  engine/domain  gate %s%%, measured ' '$(3)'; $(UV) run coverage report --include="*/mendwork/engine/domain/*" --format=total --precision=2 --fail-under=$(3)
	@printf '  overall        gate %s%%, measured ' '$(4)'; $(UV) run coverage report --format=total --precision=2 --fail-under=$(4)
endef

# Parallel test execution (ADR 0012): four pytest-xdist workers, each test file kept whole on one
# worker, so module fixtures and in-file order are exactly as in a serial run. Four was measured on
# an 8-core, 8 GB M2 (more workers only swapped) and matches CI's 4 vCPUs. PYTEST_WORKERS=0 runs
# serially, for debugging. Kept out of pyproject's addopts: the coverage ratchet runs pytest itself.
PYTEST_WORKERS ?= 4
PYTEST_PARALLEL := -n $(PYTEST_WORKERS) --dist loadfile

# Everything except tests marked slow: CLI runs that launch their own Chromium, the full heal
# pair sweep, the in-process portal replays, the heal fixture suite, recordings in Chromium, and
# the chaos portal's determinism checks.
test:
	$(UV) run pytest -m "not slow" $(PYTEST_PARALLEL) --cov --cov-report=term-missing:skip-covered
	$(call coverage_gates,$(FAST_SUITE),97,98,90)

test-all:
	$(UV) run pytest $(PYTEST_PARALLEL) --cov --cov-report=term-missing:skip-covered
	$(call coverage_gates,$(FULL_SUITE),90,95,85)

check: lint typecheck imports jscheck test

check-all: lint typecheck imports jscheck test-all

# Regenerates the workflow JSON Schema from the domain models; a test fails when it is stale.
schema:
	$(UV) run mendwork schema --output schemas/workflow.schema.json

portal:
	$(UV) run python -m mendwork.apps.portal --root chaos-portal

# Rewrites benchmarks/chaos/heal_pairs.json from the portal's own declarations and selection.
chaos-pairs:
	$(UV) run python -m benchmarks.chaos.heal_pairs

# Rewrites tests/fixtures/recordings/download_report.yaml from a fresh scripted recording.
recording-golden:
	$(UV) run python -m tests.integration.regenerate_recording_golden

bench:
	@echo "make bench: available from Phase 9"

# Calls the model provider MENDWORK_MODEL_* configures, for real. Local only, never in CI.
live-providers:
	$(UV) run pytest tests/live --live-providers -p no:cacheprovider -q
