.DEFAULT_GOAL := help
VENV ?= .venv
PY := $(VENV)/bin/python
TS := sdk/typescript

.PHONY: help venv install dev test cov lint fmt sdk-ts docker-up docker-down clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Create virtualenv
	test -d $(VENV) || python3 -m venv $(VENV)

install: venv ## Install service, Python SDK and TypeScript SDK dependencies
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"
	$(VENV)/bin/pip install -e sdk/python
	cd $(TS) && npm ci

dev: ## API with reload on :8000
	$(VENV)/bin/uvicorn feature_flags.main:app --reload --port 8000

test: ## Python tests (service + Python SDK)
	$(PY) -m pytest

cov: ## Python tests with coverage
	$(PY) -m pytest --cov=feature_flags --cov=flagclient --cov-report=term-missing

sdk-ts: ## Lint, typecheck, test and build the TypeScript SDK
	cd $(TS) && npm run lint && npm run typecheck && npm test && npm run build

lint: ## Ruff check + format check
	$(VENV)/bin/ruff check feature_flags tests sdk/python
	$(VENV)/bin/ruff format --check feature_flags tests sdk/python

fmt: ## Format + autofix
	$(VENV)/bin/ruff format feature_flags tests sdk/python
	$(VENV)/bin/ruff check --fix feature_flags tests sdk/python

docker-up: ## api + postgres + redis
	docker compose up --build -d

docker-down: ## Stop stack
	docker compose down

clean: ## Remove caches and local DB
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage flags.db $(TS)/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
