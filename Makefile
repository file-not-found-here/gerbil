.PHONY: sync test lint type-check format format-check cli-help clean \
       integration integration-gerbil integration-statistics

sync:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check src/gerbil tests

type-check:
	uv run mypy

format:
	uv run black src/gerbil tests

format-check:
	uv run black --check src/gerbil tests

clean:
	rm -rf .venv
	uv sync

cli-help:
	uv run gerbil --help

# ── Integration tests ────────────────────────────────────────────────
# Regenerate gerbil.json for each project (CLDK analysis + property checks)
integration-gerbil:
	uv run pytest tests/integration/ --run-integration \
		--ignore=tests/integration/test_statistics_on_real_outputs.py

# Regenerate statistics outputs from existing gerbil.json files
integration-statistics:
	uv run pytest tests/integration/test_statistics_on_real_outputs.py --run-integration

# Run all integration tests (gerbil first, then statistics)
integration:
	uv run pytest tests/integration/ --run-integration
