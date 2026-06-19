#!/usr/bin/env python3
"""Generate the Electroziles traffic Grafana dashboard JSON.

Single source of truth for the `vsa-electroziles` dashboard — a per-tenant view
of electroziles.ch / electroziles.com web traffic. Re-run to regenerate
`dashboards/electroziles.json`, then POST it to Grafana:

    python3 build_electroziles_dashboard.py > dashboards/electroziles.json
    curl -s -X POST "$GRAFANA/api/dashboards/db" -H 'Content-Type: application/json' \
         -d "{\"dashboard\": $(cat dashboards/electroziles.json), \"overwrite\": true}"

Data source: Loki uid=ffl21vk4eobuoe, job="nginx-domain-access" — the per-domain
NGINX JSON access logs (labels: domain, method, status, vps_id). Both
electroziles.ch and electroziles.com are served by one vhost (and one log file)
but carry distinct `domain` labels, so they split cleanly.

NOTES / gotchas baked in here:
- Loki retention is 30 days → this is a rolling ~30-day live view. Longer history
  lives only in the on-disk log
  (/srv/flowbiz/reverse-proxy/logs/domains/electroziles.ch.access.json).
- "Unique visitors" has no native LogQL distinct-count. We approximate it with
  count(sum by (client-ip) (...)) after extracting the real client IP (Cloudflare
  puts it in http_x_forwarded_for; remote_addr is the CF edge). It's an estimate
  and gets heavier over long ranges.
- Line-filter regexes use BACKTICKS, not double quotes: LogQL parses `\.` inside
  "..." as an invalid escape (HTTP 400); inside `...` it's a literal.
"""
from __future__ import annotations

import json

LOKI = "ffl21vk4eobuoe"

# Base stream selector — every panel scopes to the electroziles domains via the
# $domain template variable (whose "All" value is the .*electroziles.* regex).
SEL = '{job="nginx-domain-access", domain=~"$domain"}'
# Line filter approximating real page views: drop static assets and common
# bot/scanner probes so "page views" reflect humans. BACKTICK string in LogQL.
NOISE = r'(?i)/_next/|/static/|wp-login|xmlrpc|\.php|\.env|\.git|\.(js|css|png|jpe?g|gif|svg|ico|woff2?|ttf|map|webp|json|xml|txt)'
# Extract the real client IP into label `cip` (XFF first, else remote_addr).
CIP = ('| json | label_format '
       'cip="{{ if .http_x_forwarded_for }}{{ .http_x_forwarded_for }}'
       '{{ else }}{{ .remote_addr }}{{ end }}"')
# Approx unique visitors = number of distinct client IPs over the range.
UV_TOTAL = f'count(sum by (cip) (count_over_time({SEL} {CIP} [$__range])))'
UV_DOMAIN = f'count by (domain) (sum by (domain, cip) (count_over_time({SEL} {CIP} [$__range])))'

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def gp(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def loki_target(expr, legend="", instant=False):
    return {
        "datasource": {"type": "loki", "uid": LOKI},
        "expr": expr,
        "legendFormat": legend,
        "instant": instant,
        "queryType": "instant" if instant else "range",
        "refId": "A",
    }


def stat(title, target, gridpos, unit="short", thresholds=None, decimals=0,
         color_mode="value", graph=True):
    steps = thresholds or [{"color": "blue", "value": None}]
    return {
        "id": nid(),
        "type": "stat",
        "title": title,
        "gridPos": gridpos,
        "targets": [target],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "thresholds": {"mode": "absolute", "steps": steps},
                "color": {"mode": "thresholds"},
            },
            "overrides": [],
        },
        "options": {
            "colorMode": color_mode,
            "graphMode": "area" if graph else "none",
            "justifyMode": "auto",
            "textMode": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def timeseries(title, targets, gridpos, unit="short", stack=False, fill=10,
               legend_table=False, decimals=None):
    return {
        "id": nid(),
        "type": "timeseries",
        "title": title,
        "gridPos": gridpos,
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 1,
                    "fillOpacity": fill,
                    "showPoints": "never",
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                    "axisPlacement": "auto",
                },
                "color": {"mode": "palette-classic"},
            },
            "overrides": [],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {
                "displayMode": "table" if legend_table else "list",
                "placement": "right" if legend_table else "bottom",
                "calcs": ["mean", "max", "sum"] if legend_table else [],
            },
        },
    }


def bargauge(title, target, gridpos, unit="short"):
    return {
        "id": nid(),
        "type": "bargauge",
        "title": title,
        "gridPos": gridpos,
        "targets": [target],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "continuous-GrYlRd"},
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
            },
            "overrides": [],
        },
        "options": {
            "displayMode": "gradient",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def piechart(title, target, gridpos, unit="short"):
    return {
        "id": nid(),
        "type": "piechart",
        "title": title,
        "gridPos": gridpos,
        "targets": [target],
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"}}, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "pieType": "donut",
            "legend": {"displayMode": "table", "placement": "right", "values": ["value", "percent"]},
        },
    }


def logs(title, expr, gridpos):
    return {
        "id": nid(),
        "type": "logs",
        "title": title,
        "gridPos": gridpos,
        "targets": [loki_target(expr)],
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "prettifyLogMessage": True,
            "enableLogDetails": True,
            "sortOrder": "Descending",
            "dedupStrategy": "none",
        },
    }


def row(title, y):
    return {
        "id": nid(),
        "type": "row",
        "title": title,
        "gridPos": gp(0, y, 24, 1),
        "collapsed": False,
        "panels": [],
    }


panels = []

# ── Row: Aperçu (rolling range) ─────────────────────────────────────────────
panels.append(row("Aperçu — electroziles", 0))
panels.append(stat("Visiteurs uniques (approx, période)", loki_target(
    UV_TOTAL, "visiteurs", instant=True), gp(0, 1, 4, 5), unit="short",
    color_mode="value", thresholds=[{"color": "purple", "value": None}]))
panels.append(stat("Total requêtes (période)", loki_target(
    f'sum(count_over_time({SEL}[$__range]))', "requêtes", instant=True),
    gp(4, 1, 4, 5), unit="short"))
panels.append(stat("Pages vues (période, filtré bots)", loki_target(
    f'sum(count_over_time({{job="nginx-domain-access", domain=~"$domain", method="GET", status=~"200|304"}} !~ `{NOISE}` [$__range]))',
    "pages", instant=True), gp(8, 1, 4, 5), unit="short",
    thresholds=[{"color": "green", "value": None}]))
panels.append(stat("Requêtes / s", loki_target(
    f'sum(rate({SEL}[5m]))', "req/s"), gp(12, 1, 4, 5), unit="reqps", decimals=2,
    thresholds=[{"color": "green", "value": None}]))
panels.append(stat("Erreurs 4xx (période)", loki_target(
    f'sum(count_over_time({{job="nginx-domain-access", domain=~"$domain", status=~"4.."}}[$__range]))', "4xx", instant=True),
    gp(16, 1, 4, 5), unit="short", color_mode="background",
    thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 1}]))
panels.append(stat("Erreurs 5xx (période)", loki_target(
    f'sum(count_over_time({{job="nginx-domain-access", domain=~"$domain", status=~"5.."}}[$__range]))', "5xx", instant=True),
    gp(20, 1, 4, 5), unit="short", color_mode="background",
    thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 50}]))

# ── Row: Trafic ─────────────────────────────────────────────────────────────
panels.append(row("Trafic", 6))
panels.append(timeseries("Requêtes par domaine (.ch / .com)", [loki_target(
    f'sum by (domain) (rate({SEL}[5m]))', "{{domain}}")],
    gp(0, 7, 12, 8), unit="reqps", stack=True))
panels.append(timeseries("Requêtes par statut HTTP", [loki_target(
    f'sum by (status) (rate({SEL}[5m]))', "{{status}}")],
    gp(12, 7, 12, 8), unit="reqps", stack=True))
panels.append(piechart("Répartition requêtes .ch vs .com", loki_target(
    f'sum by (domain) (count_over_time({SEL}[$__range]))', "{{domain}}", instant=True),
    gp(0, 15, 8, 8)))
panels.append(bargauge("Visiteurs uniques par domaine (approx)", loki_target(
    UV_DOMAIN, "{{domain}}", instant=True), gp(8, 15, 8, 8), unit="short"))
panels.append(piechart("Méthodes HTTP", loki_target(
    f'sum by (method) (count_over_time({SEL}[$__range]))', "{{method}}", instant=True),
    gp(16, 15, 8, 8)))

# ── Row: Pages & erreurs ────────────────────────────────────────────────────
panels.append(row("Pages & erreurs", 23))
# Top pages: restrict to successfully-served pages (200/304) and group by PATH
# (query string stripped via `| regexp`). Both matter for the 500-series query
# limit: bot 404s and ?_rsc=... query strings would each blow past it over a
# multi-day range. This stays well under 500 even at 30d.
panels.append(bargauge("Top 15 pages servies (200/304, période)", loki_target(
    f'topk(15, sum by (path) (count_over_time('
    f'{{job="nginx-domain-access", domain=~"$domain", method="GET", status=~"200|304"}} '
    f'!~ `{NOISE}` | regexp `"uri":"(?P<path>[^?"]*)` [$__range])))',
    "{{path}}", instant=True), gp(0, 24, 12, 11), unit="short"))
panels.append(timeseries("Pages vues / s par domaine (filtré bots & assets)", [loki_target(
    f'sum by (domain) (rate({{job="nginx-domain-access", domain=~"$domain", method="GET", status=~"200|304"}} !~ `{NOISE}` [5m]))',
    "{{domain}}")], gp(12, 24, 12, 11), unit="reqps", stack=True))

# ── Row: Logs ───────────────────────────────────────────────────────────────
panels.append(row("Logs", 35))
panels.append(logs("Requêtes récentes", SEL, gp(0, 36, 12, 12)))
panels.append(logs("Erreurs 4xx / 5xx", '{job="nginx-domain-access", domain=~"$domain", status=~"[45].."}',
                   gp(12, 36, 12, 12)))

# Pin each panel's datasource to its first target (all Loki here) so Grafana
# doesn't fall back to a wrong default — mirrors the fleet dashboard generator.
for _p in panels:
    if _p.get("type") == "row":
        continue
    _tgts = _p.get("targets") or []
    if _tgts:
        _p["datasource"] = _tgts[0]["datasource"]

# ── Templating ──────────────────────────────────────────────────────────────
templating = {
    "list": [
        {
            "name": "domain",
            "type": "query",
            "label": "Domaine",
            "datasource": {"type": "loki", "uid": LOKI},
            "query": {"label": "domain", "stream": '{job="nginx-domain-access"}', "type": 1},
            "definition": 'label_values({job="nginx-domain-access"}, domain)',
            "regex": "/electroziles/",
            "refresh": 2,
            "includeAll": True,
            "allValue": ".*electroziles.*",
            "multi": True,
            "current": {"text": "All", "value": "$__all"},
        }
    ]
}

dashboard = {
    "uid": "vsa-electroziles",
    "title": "Electroziles — Trafic web",
    "tags": ["vsa", "electroziles", "traffic", "flowbiz"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 0,
    "refresh": "5m",
    "time": {"from": "now-7d", "to": "now"},
    "templating": templating,
    "panels": panels,
    "editable": True,
}

print(json.dumps(dashboard, indent=2))
