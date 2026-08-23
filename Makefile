PYTHON ?= python

.PHONY: install test coverage lint format typecheck security build check

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

coverage:
	$(PYTHON) -m pytest --cov=aruodas_scraper --cov-report=term-missing --cov-fail-under=80

lint:
	$(PYTHON) -m black --check src tests
	$(PYTHON) -m isort --check-only src tests

format:
	$(PYTHON) -m black src tests
	$(PYTHON) -m isort src tests

typecheck:
	$(PYTHON) -m mypy src

security:
	$(PYTHON) -m bandit -r src

build:
	$(PYTHON) -m build

check: lint typecheck security coverage build
