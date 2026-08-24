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
migrate:  ## Apply migrations
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
test:  ## The suite with the coverage gate, minus the wall-clock gates
	$(PY) -m pytest -m "not slow" --cov=disputeshield --cov-fail-under=85

.PHONY: slow
slow:  ## The wall-clock gates, each in its own session
	@# Run separately because they are timing assertions. Sharing a session with
	@# fixtures that build 35,000 rows means measuring autovacuum alongside the
	@# queue, and a gate that cries wolf is one people re-run until it passes.
	$(PY) -m pytest tests/test_queue_performance.py -q
	$(PY) -m pytest tests/test_sweep_at_load.py -q
	$(PY) -m pytest tests/test_mass_event_at_scale.py -q

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
	$(PY) -m pip_audit --skip-editable
	$(PY) -m semgrep --config p/python --config p/django --error --quiet .
	gitleaks detect --no-banner

.PHONY: widget
widget:  ## Build the loader and the widget, and publish the bundle
	npm ci --prefix loader && npm run build --prefix loader
	npm ci --prefix widget && npm run build --prefix widget
	mkdir -p static/widget && cp widget/dist/widget.js widget/dist/widget.css static/widget/
	scripts/check-loader-size.sh
	npm test --prefix loader

.PHONY: browser
browser: widget  ## Isolation, keyboard and axe gates in a real browser
	npx --prefix widget playwright install chromium
	npm run test:e2e --prefix widget
	$(MAKE) stop-servers

.PHONY: stop-servers
stop-servers:  ## Stop servers Playwright left running
	@# `reuseExistingServer` keeps the Django and fixture servers alive between
	@# local runs, which is convenient until they hold database connections
	@# during the p95 gate and it fails for reasons that have nothing to do with
	@# the code. This has happened three times; hence the target.
	@pkill -f "manage.py runserver 127.0.0.1:8011" 2>/dev/null || true
	@pkill -f "tests/serve-host.mjs" 2>/dev/null || true
	@echo "stopped any lingering test servers"

.PHONY: packaging
packaging:  ## Build a wheel, install it into a bare project, file a dispute
	scripts/packaging-gate.sh

.PHONY: hello
hello:  ## Empty database to a filed dispute with a computed deadline
	scripts/hello-world.sh

.PHONY: ci
ci: lint migrations-check test gates slow security widget  ## Everything CI runs
