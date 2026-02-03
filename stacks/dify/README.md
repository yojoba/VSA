# Dify Stack

Includes services: web, api, worker, sandbox, Postgres, Redis.

- Volumes:
  - /srv/flowbiz/dify/data -> /app/data (api)
  - /srv/flowbiz/dify/data/postgres -> /var/lib/postgresql/data
  - /srv/flowbiz/dify/data/redis -> /data
- Networks: `dify-net` (internal), `flowbiz_ext` (external)
- Healthchecks: api health at http://api:5001/health; web at http://web:3000/

Deploy:
1. mkdir -p /srv/flowbiz/dify/{data,env,logs} /srv/flowbiz/dify/data/{postgres,redis}
2. cp ./.env.example /srv/flowbiz/dify/env/.env
3. docker compose -f compose.yml up -d

NGINX routing:
- / -> web:3000
- /v1/ -> api:5001
