# Hand-written Vhosts Runbook

Some domains need an NGINX vhost that `vsa site provision` cannot generate.
Those files live in `stacks/reverse-proxy/nginx/conf.d/` like any other, are
deployed by `vsa vhost sync`, and are **hand-maintained**: the CLI must never
regenerate them.

## Why a vhost can't be generated

`templates/vhost_https.conf.j2` renders one `server` block with:

- exactly **one** upstream (`set $upstream {{ container }}:{{ port }}`), so a
  domain that routes `/` to one container and `/api/` to another can't be
  expressed;
- a hard-coded `include /etc/nginx/snippets/security_headers.conf`, which sets
  `Permissions-Policy: microphone=(), camera=(), geolocation=()` — fatal for
  any page that calls `getUserMedia`.

The `--route` multipoint syntax shown in the README is **not implemented** in
the CLI (no `route` anywhere in `apps/vps-admin-cli/src/`). Until it is, a
multi-backend domain is a hand-written file.

## Current hand-written vhosts

| Vhost | App | Why it can't be generated |
| --- | --- | --- |
| `sia.flowbiz.ai.conf` | VoiceNotes — LokalFlash Sàrl | two upstreams (`voicenotes-web`, `voicenotes-api` under `/api/`) + microphone allowed |
| `impact.flowbiz.ai.conf` | VoiceNotes — Bureau d'Etudes Impact SA | idem |
| `osom.flowbiz.ai.conf` | VoiceNotes — OSOM | idem |
| `prospect.flowbiz.ai.conf` | Prospection B2B — flowbiz.ai (compte plateforme) | two upstreams (`prospection-web`, `prospection-api` under `/api/`) |
| `c-living.flowbiz.ai.conf` | Prospection B2B — Californian Living & Kooala | idem |

The three VoiceNotes domains share **one** docker compose stack (containers
`voicenotes-web` and `voicenotes-api`); the Go API picks the tenant from the
`Host` header. Application code: `github.com/yojoba/voicenotes`.

## Rules

1. **Never** run `vsa site provision --domain <one of the above>` — it
   overwrites the file in the repo and in the mount, dropping `/api/` and the
   microphone permission. The damage is silent: `nginx -t` passes.
2. Each hand-written file carries a header comment saying so. Keep it.
3. Edit the file in this repo, never in `/srv/flowbiz/reverse-proxy/nginx/conf.d/`.
   Deploy with `vsa vhost sync` (git pull on the VPS first).
4. Register the domain in the fleet so drift detection and the dashboard cover
   it: `vsa fleet assign --domain X --primary vps-01 --notes "..."`.

## 🔴 `vsa vhost sync` deletes what the repo doesn't have

`vhost sync` is `sudo rsync -av --delete repo/ mount/` on both `conf.d/` and
`snippets/`. Any file present on the mount but absent from this repo is
**removed**, and NGINX then serves those domains from `00-default.conf`
(HTTP 444). `nginx -t` passes, so nothing warns you.

This actually happened in waiting: between 2026-09-11 and 2026-09-14 the three
VoiceNotes vhosts were placed straight onto the mount by a script in the
voicenotes repo and were never added here. A single `vsa vhost sync` would have
taken `sia.flowbiz.ai` and `impact.flowbiz.ai` offline. They were moved into
this repo on 2026-09-14.

Before a sync on a host you are not sure about, list what would be deleted:

```bash
sudo rsync -an --delete --itemize-changes \
  ~/dev/github/VSA/stacks/reverse-proxy/nginx/conf.d/ \
  /srv/flowbiz/reverse-proxy/nginx/conf.d/ | grep '^\*deleting'
# same for snippets/
```

Empty output means the sync is safe. Anything listed must be committed here
first (or deleted deliberately).

## A vhost whose certificate does not exist yet: `.conf.pending`

`vsa vhost sync` copies **every** `*.conf` of the repo to the mount, then runs
`nginx -t`. A vhost that references `/etc/letsencrypt/live/<domain>/…` before
that certificate exists makes the whole test fail and blocks the reload — for
every domain on the host. Measured on 2026-09-14 with `c-living.flowbiz.ai`
(no DNS record yet, hence no certificate): `prospect.flowbiz.ai` could not go
live until the file was removed from the mount by hand.

Rule: a hand-written vhost enters this repo as `<domain>.conf.pending` (nginx
only includes `*.conf`) and is renamed to `.conf` **after** its certificate
exists. `prospection/deploy/flowbiz-1/add-host.sh` understands `.pending`:
first run issues the certificate and stops; rename, commit, push, pull; second
run syncs the real vhost.

## Issuing the first certificate for a hand-written vhost

The repo vhost references `ssl_certificate /etc/letsencrypt/live/<domain>/…`,
so it cannot be synced before the certificate exists — `nginx -t` would fail
*after* rsync has already written the file. Order matters:

1. write a temporary HTTP-only vhost straight to the mount, reload;
2. issue the certificate (`certbot certonly --webroot -w /var/www/certbot`);
3. `vsa vhost sync` — replaces the temporary file with the repo one;
4. `vsa fleet assign`.

`voicenotes/deploy/flowbiz-1/add-host.sh` does exactly that, with the deletion
guard above as its first step.
