#!/usr/bin/env bash
set -Eeuo pipefail

# sync_vhosts.sh
# Synchronizes NGINX vhost files from repo to mounted directory
# This prevents orphaned vhost files from causing SSL errors

REPO_VHOST_DIR="/home/fgrosal/dev/github/VSA/stacks/reverse-proxy/nginx/conf.d"
MOUNT_VHOST_DIR="/srv/flowbiz/reverse-proxy/nginx/conf.d"

REPO_SNIPPETS_DIR="/home/fgrosal/dev/github/VSA/stacks/reverse-proxy/nginx/snippets"
MOUNT_SNIPPETS_DIR="/srv/flowbiz/reverse-proxy/nginx/snippets"

echo "[1/4] Syncing vhost files from repo to mounted directory"
sudo rsync -av --delete "$REPO_VHOST_DIR/" "$MOUNT_VHOST_DIR/"

echo "[2/4] Syncing snippet files from repo to mounted directory"
sudo rsync -av --delete "$REPO_SNIPPETS_DIR/" "$MOUNT_SNIPPETS_DIR/"

echo "[3/4] Reloading NGINX"
cd /home/fgrosal/dev/github/VSA/stacks/reverse-proxy
docker compose exec nginx nginx -s reload

echo "[4/4] Done! Vhosts+snippets synced and NGINX reloaded"

