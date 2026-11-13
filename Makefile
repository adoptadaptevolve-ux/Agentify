.PHONY: help install test lint typecheck demo docs check

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install the platform and dev tooling into the active environment
	python -m pip install -e ".[dev]"

test:  ## Run the invariant and capability test suites
	python -m pytest -q

lint:  ## Lint
	python -m ruff check src tests capabilities examples

typecheck:  ## Type-check the platform source
	python -m mypy

demo:  ## Run the sparring partner golden path end to end against the offline judge
	python examples/run_sparring_partner.py

check: lint typecheck test  ## Everything CI runs

docs:  ## List the documentation tree
	@find docs -name '*.md' | sort
