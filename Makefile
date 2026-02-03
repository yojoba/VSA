.PHONY: bootstrap new-stack up down logs ps prune lint format test release

# ── VSA CLI wrappers ──────────────────────────────────────────────
# These targets delegate to the 'vsa' CLI. Install it with:
#   cd apps/vps-admin-cli && uv tool install .

bootstrap: ## Install base tools on a fresh VPS
	vsa bootstrap

new-stack: ## Create a new stack from template
	vsa stack new $(name)

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

prune:
	docker system prune -af --volumes

lint:
	hadolint **/Dockerfile || true
	cd apps/vps-admin-cli && uv run ruff check .
	cd packages/python/vsa-common && uv run ruff check .
	npx eslint apps/vps-admin-ui/src/**/*.{ts,tsx} || true

format:
	cd apps/vps-admin-cli && uv run ruff format .
	cd packages/python/vsa-common && uv run ruff format .
	npx prettier -w "apps/vps-admin-ui/src/**/*.{ts,tsx,js,jsx,json}"

test: ## Run all tests
	cd apps/vps-admin-cli && uv run pytest -q

release:
	./infra/scripts/release.sh

# ── Site provisioning (delegates to vsa CLI) ──────────────────────

.PHONY: provision-site provision-container
provision-site: ## Provision a site behind the reverse proxy (manual container name)
	vsa site provision --domain $(domain) --container $(container) --port $(port)

provision-container: ## Auto-provision a site by detecting container from external port
	@if [ "$(nowww)" = "true" ]; then \
		vsa site provision --domain $(domain) --port $(port) --detect --external-port $(port) --no-www; \
	else \
		vsa site provision --domain $(domain) --port $(port) --detect --external-port $(port); \
	fi

.PHONY: check-certs install-cert-monitoring
check-certs: ## Check SSL certificates and renew if needed
	vsa cert renew

install-cert-monitoring: ## Install daily SSL certificate monitoring cron job
	vsa cert install-cron

.PHONY: add-basic-auth
add-basic-auth: ## Add HTTP Basic Auth to a domain
	vsa auth add --domain $(domain) --user $(user) --password $(password)

.PHONY: remove-basic-auth
remove-basic-auth: ## Remove HTTP Basic Auth from a domain
	vsa auth remove --domain $(domain)

.PHONY: unprovision-container
unprovision-container: ## Unprovision a container from the reverse proxy
	vsa site unprovision --domain $(domain)

.PHONY: sync-vhosts
sync-vhosts: ## Sync NGINX vhost files from repo to mounted directory and reload
	vsa vhost sync
