# Restore Runbook

## Full VPS Restore

1. Provision new VPS and bootstrap:
   ```bash
   vsa bootstrap
   ```
2. Restore data from backups into `/srv/<tenant>/<app>/data`.
3. Recreate `.env` from secure store into `/srv/<tenant>/<app>/env/.env` (chmod 640).
4. Bring up stacks:
   ```bash
   vsa stack up reverse-proxy
   vsa stack up dify
   vsa stack up observability
   ```
5. Sync vhost configs:
   ```bash
   vsa vhost sync
   ```
6. Verify health at `/healthz` and application endpoints.
7. Check certificate status:
   ```bash
   vsa cert status
   vsa cert renew   # If needed
   ```

## Single Stack Restore

```bash
# Stop stack
vsa stack down <name>

# Restore data
rsync -av backup:/srv/flowbiz/<name>/data/ /srv/flowbiz/<name>/data/

# Restore env
cp backup.env /srv/flowbiz/<name>/env/.env
chmod 640 /srv/flowbiz/<name>/env/.env

# Restart
vsa stack up <name>
```
