SHELL := /bin/sh
ENV_FILE ?= .env
COMPOSE := docker compose --env-file $(ENV_FILE)

.PHONY: bootstrap check python-check protocol-check panel-check secret-check compose-config images up status logs migrate db-revision walking-skeleton-check simulator-lab-check simulation-gate down reset-dev-data

bootstrap:
	@test -f $(ENV_FILE) || cp .env.example $(ENV_FILE)
	@$(MAKE) check ENV_FILE=$(ENV_FILE)

check: python-check panel-check secret-check compose-config

python-check:
	@docker build --target quality --tag skid2-python-quality:local .
	@docker run --rm --network host --env SKID2_TEST_DATABASE_URL skid2-python-quality:local

protocol-check:
	@docker build --target quality --tag skid2-python-quality:local .
	@docker run --rm skid2-python-quality:local uv run --locked python -m skid2.protocol check-drift

panel-check:
	@docker build --target quality --tag skid2-panel-quality:local panel
	@docker run --rm skid2-panel-quality:local

secret-check:
	@docker run --rm --volume "$(CURDIR):/workspace:ro" --workdir /workspace skid2-python-quality:local uv run --locked pytest tests/test_secrets.py --no-cov

compose-config:
	@$(COMPOSE) config --quiet

images:
	@docker build --target runtime --tag skid2-python:local .
	@docker build --target production --tag skid2-panel:local panel

up:
	@$(COMPOSE) up --build --detach --wait --wait-timeout 240 --remove-orphans

status:
	@$(COMPOSE) ps

logs:
	@$(COMPOSE) logs --tail=200

migrate:
	@$(COMPOSE) run --rm backend migrate

db-revision:
	@$(COMPOSE) run --rm backend python -m skid2.cli show-revision

walking-skeleton-check:
	@$(MAKE) simulator-lab-check

simulator-lab-check: verification-image
	@test -n "$$SKID2_TEST_DATABASE_URL" || { echo "Set SKID2_TEST_DATABASE_URL to a separate test database."; exit 1; }
	@docker run --rm --network host --env SKID2_TEST_DATABASE_URL skid2-python-quality:local uv run --locked pytest tests/integration/test_configuration.py --no-cov

simulation-gate: check protocol-check simulator-lab-check

down:
	@$(COMPOSE) down --remove-orphans

reset-dev-data:
	@ENV_FILE=$(ENV_FILE) ./scripts/reset-dev-data.sh

.PHONY: walking-skeleton-unit-check configuration-check verification-image
verification-image:
	@docker build --target quality --tag skid2-python-quality:local .

walking-skeleton-unit-check: verification-image
	@docker run --rm skid2-python-quality:local uv run --locked pytest tests/test_current_state.py tests/test_runtime.py tests/test_app.py tests/test_verification.py --no-cov

configuration-check: simulator-lab-check
