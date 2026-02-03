# Reverse Proxy Stack

Containerized NGINX reverse proxy with Certbot-managed Let's Encrypt certificates.

- Volumes:
  - /srv/flowbiz/reverse-proxy/letsencrypt -> /etc/letsencrypt
  - /srv/flowbiz/reverse-proxy/certbot-www -> /var/www/certbot
  - /srv/flowbiz/reverse-proxy/nginx/conf.d -> /etc/nginx/conf.d
  - /srv/flowbiz/reverse-proxy/nginx/snippets -> /etc/nginx/snippets
  - /srv/flowbiz/reverse-proxy/logs -> /var/log/nginx
- Network: `flowbiz_ext` (external)
- Healthcheck: responds "ok" at /healthz when vhost is configured.

Deploy:
1. mkdir -p /srv/flowbiz/reverse-proxy/{letsencrypt,certbot-www,logs} /srv/flowbiz/reverse-proxy/nginx/{conf.d,snippets}
2. cp ./nginx/snippets/security_headers.conf /srv/flowbiz/reverse-proxy/nginx/snippets/security_headers.conf
3. docker compose -f compose.yml up -d

Quick site provisioning (recommended):

Auto-detect container from published port:
```bash
# For apex domains (includes www subdomain)
make provision-container domain=example.com port=3005
```
Or:
```bash
./infra/scripts/provision_container.sh --domain example.com --external-port 3005
```

For subdomains (without www):
```bash
make provision-container domain=app.example.com port=3002 nowww=true
```docker ps

Or:
```bash
./infra/scripts/provision_container.sh --domain app.example.com --external-port 3002 --no-www
```

Add / remove HTTP Basic Auth protection:

After provisioning a site, you can add password protection:
```bash
make add-basic-auth domain=staging.example.com user=admin password=secret123
```
Or:
```bash
./infra/scripts/add_basic_auth.sh --domain staging.example.com --user admin --password secret123
```

Example (protect naturalpes-pharma):
```bash
make add-basic-auth domain=naturalpes-pharma.flowbiz.ai user=admin password=MySecurePass
```

To remove basic auth for a domain:
```bash
make remove-basic-auth domain=naturalpes-pharma.flowbiz.ai
make sync-vhosts    # applies changes and reloads NGINX
```

Or directly via script:
```bash
./infra/scripts/remove_basic_auth.sh --domain naturalpes-pharma.flowbiz.ai
sudo ./infra/scripts/sync_vhosts.sh
```

**Sync vhost files after making changes:**

When you modify or delete vhost files (or snippets) in the repo, sync them to the mounted directory to prevent SSL errors:
```bash
make sync-vhosts
```
Or:
```bash
./infra/scripts/sync_vhosts.sh
```

This will:
- Copy all vhost files from repo to `/srv/flowbiz/reverse-proxy/nginx/conf.d/`
- Copy all snippet files from repo to `/srv/flowbiz/reverse-proxy/nginx/snippets/`
- Delete any orphaned files (like removed sites)
- Reload NGINX automatically

**Important:** Always run `make sync-vhosts` after:
- Deleting a site's vhost file
- Adding/modifying vhost configurations
- Git pull that changes vhost files

Manual container name:
```bash
make provision-site domain=example.com container=my-app-container port=3000
```
Or:
```bash
./infra/scripts/provision_site.sh --domain example.com --container my-app-container --port 3000
```

What it does:
- Detects/attaches the container to `flowbiz_ext`
- Generates and deploys vhost for `example.com` and `www.example.com`
- Issues a Let's Encrypt certificate
- Reloads NGINX

Unprovision a site (remove vhost + auth, keep container running):

```bash
make unprovision-container domain=example.com
make sync-vhosts    # applies changes and reloads NGINX
```

Or:
```bash
./infra/scripts/unprovision_site.sh --domain example.com
sudo ./infra/scripts/sync_vhosts.sh
```

SSL Certificate Monitoring & Auto-Renewal:

Automated daily monitoring (recommended):
```bash
# Install daily cron job (runs at 3 AM)
make install-cert-monitoring
```

Manual certificate check:
```bash
# Check all certificates and renew if expiring within 30 days
make check-certs
```

View logs:
```bash
tail -f /var/log/cert-renewal.log
```

The monitoring script:
- Checks all Let's Encrypt certificates daily
- Auto-renews any expiring within 30 days
- Reloads NGINX after renewal
- Logs all operations
- Optional Slack notifications (set SLACK_WEBHOOK_URL env var)

Issue a certificate (manual):
- make issue-cert domain=example.com email=ops@flowbiz.ai

Vhost guidance:
- Create `/srv/flowbiz/reverse-proxy/nginx/conf.d/<domain>.conf` following the repository rule template.

Special configurations for specific apps:

n8n (requires WebSocket and SSE support):
```nginx
server {
  listen 443 ssl http2;
  server_name n8n.example.com;
  
  ssl_certificate     /etc/letsencrypt/live/n8n.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/n8n.example.com/privkey.pem;
  
  include /etc/nginx/snippets/security_headers.conf;
  
  # Disable gzip for debugging
  gzip off;
  gunzip on;
  proxy_set_header Accept-Encoding "";
  
  resolver 127.0.0.11 ipv6=off;
  set $upstream n8n-container:443;
  
  location / {
    proxy_pass http://$upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    
    # WebSocket support (required for n8n UI real-time updates)
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    
    # SSE support (required for MCP servers)
    proxy_buffering off;
    proxy_cache off;
    proxy_set_header X-Accel-Buffering no;
    
    # Extended timeouts for long-lived connections
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_connect_timeout 120s;
  }
}
```
