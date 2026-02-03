.PHONY: up down logs ps
up:
	docker compose -f compose.yml up -d --build
down:
	docker compose -f compose.yml down
logs:
	docker compose -f compose.yml logs -f --tail=200
ps:
	docker compose -f compose.yml ps
