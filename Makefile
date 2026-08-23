# The targets CI runs are the targets you run. If they diverge, CI is lying.

.DEFAULT_GOAL := help
PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help
help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

.venv:
	python3.12 -m venv .venv
	$(PIP) install -U pip

.PHONY: install
install: .venv  ## Install the package and dev dependencies
	$(PIP) install -e ".[dev,server]"
	$(PY) -m pre_commit install || true

.PHONY: up
up:  ## Start Postgres, both Redis instances and PgBouncer
	docker compose up -d
	@echo "waiting for postgres..." && sleep 3

.PHONY: down
down:  ## Stop and remove local infrastructure
	docker compose down -v

.PHONY: migrate
migrate:  ## Apply migrations (runs as the owner role, not the app role)
	$(PY) manage.py migrate

.PHONY: doctor
doctor:  ## Verify grants, audit trigger, Redis, clock skew (§6.2)
	$(PY) manage.py disputeshield_doctor --strict

.PHONY: fmt
fmt:  ## Format
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

.PHONY: lint
lint:  ## Lint, format check, no-dangerous-html grep
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	scripts/no-dangerous-html.sh

.PHONY: test
test:  ## Full suite with the coverage gate
	$(PY) -m pytest --cov=disputeshield --cov-fail-under=85

.PHONY: test-fast
test-fast:  ## Everything except the slow suites
	$(PY) -m pytest -m "not slow" -q

.PHONY: gates
gates:  ## The blocking gates, alone. These never go yellow.
	$(PY) -m pytest -m "isolation or immutability or leakage" -q

.PHONY: migrations-check
migrations-check:  ## Fail if a model change has no migration
	$(PY) manage.py makemigrations --check --dry-run

.PHONY: security
security:  ## SAST, dependency audit, secret scan
	$(PY) -m bandit -r disputeshield/ -q
	$(PY) -m pip_audit
	gitleaks detect --no-banner

.PHONY: hello
hello:  ## Empty database to a filed dispute with a computed deadline
	scripts/hello-world.sh

.PHONY: ci
ci: lint migrations-check test gates security  ## Everything CI runs
