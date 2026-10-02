# SkillifyMe Portal — developer commands. Run `make help` for the list.
#
# Recipes use bash. On Windows, GNU make is pointed at Git Bash (the `bash` on PATH is often WSL's).
ifeq ($(OS),Windows_NT)
GIT_BASH ?= C:/Progra~1/Git/bin/bash.exe
SHELL := $(GIT_BASH)
else
SHELL := bash
endif
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

# Ports and other settings come from .env (created from .env.example on first run).
-include .env

API := apps/api
WEB := apps/web
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------------------------------ setup
.env:
	cp .env.example .env
	@echo "Created .env from .env.example"

.PHONY: install
install: .env ## Install host toolchains (API venv, web deps, Playwright browser, pre-commit hooks)
	cd $(API) && uv sync
	cd $(WEB) && pnpm install --frozen-lockfile
	cd $(WEB) && pnpm exec playwright install chromium
	uvx pre-commit install

# ------------------------------------------------------------------------------------------ run
.PHONY: dev
dev: .env ## Start the full local stack in docker and wait until every service is healthy
	$(COMPOSE) up -d --build --wait
	@echo ""
	@echo "  Web            http://localhost:$(WEB_PORT)"
	@echo "  API            http://localhost:$(API_PORT)/docs    (ready: /health/ready)"
	@echo "  Keycloak       http://localhost:$(KEYCLOAK_PORT)    (realm: $(KEYCLOAK_REALM))"
	@echo "  MinIO console  http://localhost:$(MINIO_CONSOLE_PORT)"
	@echo "  Redpanda       http://localhost:$(REDPANDA_CONSOLE_PORT)"
	@echo "  Mailpit        http://localhost:$(MAILPIT_UI_PORT)"
	@echo ""
	@echo "  Follow logs with: make logs"

# Fallback for slow or missing file watching in the web container (Windows bind mounts under
# /mnt/c or a Windows drive): everything else runs in Docker, the web app on the host. The web
# settings mirror the compose `web` service, pointed at the host-published ports. They are exported
# only to this target's recipe, so secrets never appear on a command line.
dev-web-host: export API_INTERNAL_URL = http://localhost:$(API_PORT)
dev-web-host: export KEYCLOAK_PORT := $(KEYCLOAK_PORT)
dev-web-host: export KEYCLOAK_REALM := $(KEYCLOAK_REALM)
dev-web-host: export KEYCLOAK_INTERNAL_URL = http://localhost:$(KEYCLOAK_PORT)
dev-web-host: export OIDC_WEB_CLIENT_ID := $(OIDC_WEB_CLIENT_ID)
dev-web-host: export KC_WEB_CLIENT_SECRET := $(KC_WEB_CLIENT_SECRET)
dev-web-host: export WEB_ORIGIN = http://localhost:$(WEB_PORT)
dev-web-host: export SESSION_SECRET := $(SESSION_SECRET)
dev-web-host: export REVALIDATE_SECRET := $(REVALIDATE_SECRET)
dev-web-host: export REDIS_URL = redis://localhost:$(REDIS_PORT)/1
dev-web-host: export AUTH_RATE_LIMIT_PER_MINUTE := $(or $(AUTH_RATE_LIMIT_PER_MINUTE),20)
dev-web-host: export NEXT_TELEMETRY_DISABLED = 1

.PHONY: dev-web-host
dev-web-host: .env ## Run the stack in Docker but the web app on the host (fast reload on Windows)
	$(COMPOSE) up -d --wait --scale web=0
	$(COMPOSE) stop web
	cd $(WEB) && pnpm install --frozen-lockfile
	@echo ""
	@echo "  Web (host)     http://localhost:$(WEB_PORT)    API http://localhost:$(API_PORT)"
	@echo ""
	cd $(WEB) && pnpm dev --port $(WEB_PORT)

.PHONY: down
down: ## Stop the stack (keeps data volumes)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and DELETE all local data volumes
	$(COMPOSE) down -v --remove-orphans

.PHONY: logs
logs: ## Follow logs (optionally: make logs s=api)
	$(COMPOSE) logs -f --tail=200 $(s)

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

# ------------------------------------------------------------------------------------------ database
.PHONY: migrate
migrate: .env ## Apply database migrations (alembic upgrade head)
	$(COMPOSE) run --rm migrate

.PHONY: seed
seed: .env ## Load dev orgs, batches and memberships for the dev-realm users (idempotent)
	$(COMPOSE) run --rm seed

.PHONY: migration
migration: ## Create a migration: make migration m="add courses"
	@test -n "$(m)" || (echo 'usage: make migration m="message"' && exit 1)
	cd $(API) && uv run alembic revision --autogenerate -m "$(m)"

# ------------------------------------------------------------------------------------------ quality
.PHONY: lint
lint: lint-api lint-web ## Lint, format-check and type-check both apps

.PHONY: lint-api
lint-api:
	cd $(API) && uv run ruff check .
	cd $(API) && uv run ruff format --check .
	cd $(API) && uv run mypy

.PHONY: lint-web
lint-web:
	cd $(WEB) && pnpm lint
	cd $(WEB) && pnpm format:check
	cd $(WEB) && pnpm typecheck

.PHONY: fmt
fmt: ## Auto-format and auto-fix both apps
	cd $(API) && uv run ruff check --fix . && uv run ruff format .
	cd $(WEB) && pnpm lint --fix && pnpm format

# ------------------------------------------------------------------------------------------ tests
.PHONY: test
test: test-api test-web test-e2e ## Run all tests (needs `make dev` running for Postgres/Redis/API)

.PHONY: test-api
test-api: ## API tests (pytest against real Postgres + Redis)
	cd $(API) && uv run pytest

.PHONY: test-web
test-web: ## Web unit/component tests (Vitest)
	cd $(WEB) && pnpm test

.PHONY: test-e2e
test-e2e: ## Playwright E2E against the running stack
	cd $(WEB) && pnpm test:e2e

# ------------------------------------------------------------------------------------------ codegen
.PHONY: gen-api
gen-api: ## Regenerate web API types (OpenAPI) and the notes code stylesheet
	cd $(API) && uv run python -m app.cli.export_openapi openapi.json
	cd $(WEB) && pnpm gen:api
	cd $(API) && uv run python -m app.cli.export_notes_css ../web/src/styles/notes-code.css

.PHONY: check-api-schema
check-api-schema: gen-api ## Fail if the committed web API types are stale
	git diff --exit-code -- $(WEB)/src/lib/api/schema.ts || \
		(echo "schema.ts is out of date: run 'make gen-api' and commit the result" && exit 1)

# ------------------------------------------------------------------------------------------ images
.PHONY: docker-build
docker-build: ## Build production images for api and web
	docker build --target runtime -t skillifyme-api:local $(API)
	docker build --target runtime -t skillifyme-web:local $(WEB)
