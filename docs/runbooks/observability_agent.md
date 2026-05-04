# Observability Agent Runbook

How to deploy `stacks/observability-agent/` on a new VPS so its container,
nginx, and audit logs ship to the central Loki at `https://loki.flowbiz.ai`.

## When to use

- A new VPS joins the fleet and you want its logs visible in Grafana.
- The Loki basic-auth password got rotated and you need to update agents.
- The `observability-agent-promtail` container is broken or missing.

For the **hub** (flowbiz-1), use the regular `stacks/observability/` stack
instead — it includes Loki + Grafana + Prometheus, plus its own Promtail.

## Prerequisites

- VSA repo cloned at `~/dev/github/VSA` on the target VPS, on `master`.
- `vsa` CLI installed (`uv tool install .` from `apps/vps-admin-cli/`).
- VSA agent already registered with the hub (`/etc/vsa/agent.env` exists
  with `VSA_VPS_ID=vps-XX` and `VSA_AGENT_TOKEN=...`).
- Reverse-proxy stack already running and writing JSON logs to
  `/srv/flowbiz/reverse-proxy/logs/domains/*.access.json`. (If it's not, the
  stack still works — Promtail just won't have nginx logs to ship.)
- The new VPS's public IP must be in the `allow ...;` list of
  `stacks/reverse-proxy/nginx/conf.d/loki.flowbiz.ai.conf` on flowbiz-1
  (otherwise pushes return 403).

## Procedure (per VPS)

```bash
# 1. Make sure the repo is up to date
cd ~/dev/github/VSA && git pull --ff-only origin master

# 2. Configure
sudo mkdir -p /srv/flowbiz/observability-agent/env
sudo chown $USER:$USER /srv/flowbiz/observability-agent/env
cp stacks/observability-agent/.env.example /srv/flowbiz/observability-agent/env/.env
chmod 640 /srv/flowbiz/observability-agent/env/.env
$EDITOR /srv/flowbiz/observability-agent/env/.env
# Set: VSA_VPS_ID  (must match /etc/vsa/agent.env)
#      LOKI_URL    (default https://loki.flowbiz.ai/loki/api/v1/push is fine)
#      LOKI_BASIC_AUTH_USER  (default "promtail")
#      LOKI_BASIC_AUTH_PASSWORD  (ask the hub admin — stored in
#                                  /srv/flowbiz/reverse-proxy/nginx/auth/loki.flowbiz.ai.htpasswd)

# 3. Symlink so docker-compose finds the .env without a custom path
ln -sf /srv/flowbiz/observability-agent/env/.env stacks/observability-agent/.env

# 4. Preflight: can we reach Loki?
curl -sS -u "promtail:<password>" -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST -H "Content-Type: application/json" -d "{}" \
  https://loki.flowbiz.ai/loki/api/v1/push
# Expect HTTP 204 (auth ok, empty payload accepted).
# 401 → wrong creds. 403 → IP not in allow-list. 000 → DNS/firewall.

# 5. Bring it up
vsa stack up observability-agent

# 6. Verify push within ~5s
docker logs --tail 30 observability-agent-promtail
# Look for: "Successfully sent batch", no auth/network errors.

# 7. Confirm on the hub via Grafana
# Open https://grafana.flowbiz.ai → Explore → Loki → query:
#   {vps_id="vps-XX"} |= ""
# Logs should appear within ~30s.
```

There's also `infra/scripts/setup_observability_agent.sh` which does steps
2-6 interactively, with prompts and a built-in preflight.

## Adding a new VPS to the Loki allow-list

If you're standing up `vps-04`, before running the procedure above, do this
on the hub:

```bash
ssh flowbiz-1
cd ~/dev/github/VSA
# Edit the vhost — add a new "allow X.X.X.X;  # vps-04" line in the server block.
$EDITOR stacks/reverse-proxy/nginx/conf.d/loki.flowbiz.ai.conf
git add stacks/reverse-proxy/nginx/conf.d/loki.flowbiz.ai.conf
git commit -m "feat(reverse-proxy): allow vps-04 to push to Loki"
git push origin master

# Sync to the live nginx mount + reload
cp stacks/reverse-proxy/nginx/conf.d/loki.flowbiz.ai.conf /srv/flowbiz/reverse-proxy/nginx/conf.d/loki.flowbiz.ai.conf
docker exec reverse-proxy-nginx nginx -t
docker exec reverse-proxy-nginx nginx -s reload
```

## Rotating the Loki basic-auth password

```bash
ssh flowbiz-1
NEW_PWD=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
docker run --rm httpd:2.4-alpine htpasswd -nbB promtail "$NEW_PWD" \
  > /srv/flowbiz/reverse-proxy/nginx/auth/loki.flowbiz.ai.htpasswd
chmod 644 /srv/flowbiz/reverse-proxy/nginx/auth/loki.flowbiz.ai.htpasswd
docker exec reverse-proxy-nginx nginx -s reload
echo "New password: $NEW_PWD"

# Then on each agent VPS:
ssh flowbiz-X
$EDITOR /srv/flowbiz/observability-agent/env/.env  # set LOKI_BASIC_AUTH_PASSWORD
vsa stack up observability-agent
```

Note: chmod 644 (not 640) on the htpasswd file is needed because nginx in
the container runs as UID 101 and can't read 640 files owned by the
operator user.

## Common issues

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `tcp: lookup loki.flowbiz.ai: no such host` | DNS A record missing or stale | `dig +short A loki.flowbiz.ai` should return `84.234.20.142` |
| HTTP 401 on push | basic-auth creds wrong | Check `/srv/flowbiz/observability-agent/env/.env` matches the hub's `.htpasswd` |
| HTTP 403 on push | this VPS public IP not in allow-list | Add it to `loki.flowbiz.ai.conf` on the hub (see above) |
| Empty Grafana queries for this `vps_id` | nginx not in JSON format on this host | Check `access_log` directives in `/srv/flowbiz/reverse-proxy/nginx/conf.d/*.conf` use `json_detailed` |
| Container shows `(unhealthy)` | Healthcheck uses `wget` which isn't in the promtail image | Cosmetic only — logs ship correctly. Replace with `nc -z localhost 9080` to fix |
| `${VSA_VPS_ID}` shown as literal label | `-config.expand-env=true` not passed | Check `compose.yml` `command:` has the flag |
