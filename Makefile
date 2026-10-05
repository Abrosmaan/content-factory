.PHONY: help install hooks-install test test-cov lint dry-run migrate build up down deploy push logs

PYTHON ?= python3
PIP ?= pip3

help:
	@echo "Targets:"
	@echo "  install         install dev deps locally"
	@echo "  hooks-install   install git hooks (post-commit auto-push)"
	@echo "  test            run pytest"
	@echo "  test-cov        run pytest with coverage"
	@echo "  migrate         apply DB migrations"
	@echo "  dry-run         run e2e dry-run pipeline (Epic 2+)"
	@echo "  build           docker compose build"
	@echo "  up              docker compose up -d"
	@echo "  down            docker compose down"
	@echo "  logs            docker compose logs -f"
	@echo "  push            git push origin HEAD (manual fallback)"
	@echo "  deploy          push + deploy to VPS via SSH (Epic 1.8)"

hooks-install:
	@bash scripts/install-hooks.sh

install:
	$(PIP) install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest

test-cov:
	$(PYTHON) -m pytest --cov=src --cov-report=term-missing --cov-report=xml

migrate:
	$(PYTHON) -m src.cli.migrate

dry-run:
	@bash scripts/dry_run_remote.sh

dry-run-local:
	$(PYTHON) -m src.cli.dry_run

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

push:
	@git push origin HEAD

deploy: push
	bash scripts/deploy.sh
