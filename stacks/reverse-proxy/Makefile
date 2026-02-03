.PHONY: up down logs ps reload issue-cert renew
up:
	docker compose -f compose.yml up -d --build
down:
	docker compose -f compose.yml down
logs:
	docker compose -f compose.yml logs -f --tail=200
ps:
	docker compose -f compose.yml ps
reload:
	docker compose -f compose.yml exec nginx nginx -s reload || true
issue-cert:
	docker compose -f compose.yml run --rm certbot certonly --webroot -w /var/www/certbot -d $(domain) --email $(email) --agree-tos --no-eff-email
renew:
	docker compose -f compose.yml run --rm certbot renew --quiet --deploy-hook "nginx -s reload"

