#!/usr/bin/env bash
set -Eeuo pipefail

name=${1:-${name:-}}
if [[ -z "$name" ]]; then
  echo "Usage: $0 <stack-name>" >&2
  exit 1
fi

stack_dir="stacks/$name"
echo "[new-stack] Creating skeleton at $stack_dir"
mkdir -p "$stack_dir"

if [[ ! -f "$stack_dir/compose.yml" ]]; then
  cat >"$stack_dir/compose.yml" <<'YAML'
services:
  app:
    image: nginx:1.25-alpine
    restart: unless-stopped
    networks:
      - ${STACK_NAME:-default}-${NAME:-app}-net
      - flowbiz_ext
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s

networks:
  flowbiz_ext:
    external: true
  ${STACK_NAME:-default}-${NAME:-app}-net: {}
YAML
fi

if [[ ! -f "$stack_dir/Makefile" ]]; then
  cat >"$stack_dir/Makefile" <<'MK'
.PHONY: up down logs ps prune
up:
	docker compose -f compose.yml up -d --build
down:
	docker compose -f compose.yml down
logs:
	docker compose -f compose.yml logs -f --tail=200
ps:
	docker compose -f compose.yml ps
prune:
	docker system prune -af --volumes
MK
fi

touch "$stack_dir/.env.example"

if [[ ! -f "$stack_dir/README.md" ]]; then
  cat >"$stack_dir/README.md" <<'MD'
# Stack Skeleton

This is a generated stack skeleton. Update `compose.yml` and `.env.example` for your service.

## Deploy

1. mkdir -p /srv/<tenant>/<app>/{data,env,logs}
2. cp ./.env.example /srv/<tenant>/<app>/env/.env
3. docker compose -f compose.yml up -d
MD
fi

echo "[new-stack] Done"


