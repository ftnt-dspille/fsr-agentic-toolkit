# fsr-agentic-toolkit -- dev tasks. Mirrors CI (.github/workflows/tests.yml):
# `make test` runs the same pytest CI runs, from a local .venv.
VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: help install test lint fix clean
help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-10s %s\n",$$1,$$2}'

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip

install: $(VENV) ## Editable install with dev deps (pytest + ruff) into .venv
	$(PIP) install --quiet -e ".[dev]"

test: install ## Run the full test suite (what CI runs)
	$(PY) -m pytest -q

lint: install ## ruff check (also a CI gate)
	$(PY) -m ruff check .

fix: install ## ruff check --fix (safe fixes only)
	$(PY) -m ruff check --fix .

clean: ## Remove the .venv and test/lint caches
	rm -rf $(VENV) .pytest_cache .ruff_cache
