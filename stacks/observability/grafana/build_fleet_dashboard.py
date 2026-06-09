#!/usr/bin/env python3
"""Generate the VSA Fleet Overview Grafana dashboard JSON.

Single source of truth for the `vsa-fleet-overview` dashboard. Re-run to
regenerate `dashboards/fleet-overview.json`, then POST it to Grafana:

    python3 build_fleet_dashboard.py > dashboards/fleet-overview.json
    curl -s -X POST "$GRAFANA/api/dashboards/db" -H 'Content-Type: application/json' \
         -d "{\"dashboard\": $(cat dashboards/fleet-overview.json), \"overwrite\": true}"

Datasources (created via the Grafana API, see stacks/observability/README):
  - Prometheus  uid=vsa-prometheus  (node-exporter + cAdvisor, vps-01 hub only)
  - Loki        uid=ffl21vk4eobuoe  (nginx access/error, journald, vsa-audit; all 3 VPS)
"""
from __future__ import annotations

import json

PROM = "vsa-prometheus"
LOKI = "ffl21vk4eobuoe"

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def gp(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def prom_target(expr, legend="", instant=False):
    return {
        "datasource": {"type": "prometheus", "uid": PROM},
        "expr": expr,
        "legendFormat": legend,
        "instant": instant,
        "range": not instant,
        "refId": "A",
    }


def loki_target(expr, legend="", instant=False):
    return {
        "datasource": {"type": "loki", "uid": LOKI},
        "expr": expr,
        "legendFormat": legend,
        "instant": instant,
        "queryType": "instant" if instant else "range",
        "refId": "A",
    }


def stat(title, target, gridpos, unit="percent", thresholds=None, decimals=1,
         color_mode="background", graph=False):
    steps = thresholds or [
        {"color": "green", "value": None},
        {"color": "yellow", "value": 75},
        {"color": "red", "value": 90},
    ]
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


def gauge(title, target, gridpos, unit="percent", max_=100, thresholds=None):
    steps = thresholds or [
        {"color": "green", "value": None},
        {"color": "yellow", "value": 75},
        {"color": "red", "value": 90},
    ]
    return {
        "id": nid(),
        "type": "gauge",
        "title": title,
        "gridPos": gridpos,
        "targets": [target],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "min": 0,
                "max": max_,
                "thresholds": {"mode": "absolute", "steps": steps},
                "color": {"mode": "thresholds"},
            },
            "overrides": [],
        },
        "options": {
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def timeseries(title, targets, gridpos, unit="short", stack=False, fill=10,
               legend_table=False, decimals=None, ds="prometheus"):
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
                "placement": "bottom" if not legend_table else "right",
                "calcs": ["mean", "max", "lastNotNull"] if legend_table else [],
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
            "prettifyLogMessage": False,
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

# ── Row: Host — fleet (node-exporter) ───────────────────────────────────────
# vps-01 metrics are stamped vps_id=vps-01 in prometheus.yml; vps-02/03 push via
# remote-write with vps_id in external_labels. So every series is filterable by
# the `$vps` template variable, and legends carry {{vps_id}}.
panels.append(row("Host — fleet", 0))

DISK = '100 * (1 - node_filesystem_avail_bytes{{mountpoint="{m}",fstype="ext4",vps_id=~"$vps"}} / node_filesystem_size_bytes{{mountpoint="{m}",fstype="ext4",vps_id=~"$vps"}})'
panels.append(stat("Root disk / used", prom_target(DISK.format(m="/"), "{{vps_id}}"), gp(0, 1, 4, 4),
                   thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 80}, {"color": "red", "value": 90}]))
panels.append(stat("Docker disk used", prom_target(DISK.format(m="/var/lib/docker"), "{{vps_id}}"), gp(4, 1, 4, 4),
                   thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 80}, {"color": "red", "value": 90}]))
panels.append(stat("Memory used", prom_target(
    '100 * (1 - node_memory_MemAvailable_bytes{vps_id=~"$vps"} / node_memory_MemTotal_bytes{vps_id=~"$vps"})',
    "{{vps_id}}"), gp(8, 1, 4, 4)))
panels.append(stat("CPU busy", prom_target(
    '100 - (avg by (vps_id) (rate(node_cpu_seconds_total{mode="idle",vps_id=~"$vps"}[5m])) * 100)', "{{vps_id}}"),
    gp(12, 1, 4, 4),
    thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 90}]))
panels.append(stat("Load (1m)", prom_target('node_load1{vps_id=~"$vps"}', "{{vps_id}}"), gp(16, 1, 4, 4), unit="short",
                   thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 2}, {"color": "red", "value": 4}]))
panels.append(stat("Uptime", prom_target('time() - node_boot_time_seconds{vps_id=~"$vps"}', "{{vps_id}}"), gp(20, 1, 4, 4),
                   unit="s", decimals=0, color_mode="value",
                   thresholds=[{"color": "blue", "value": None}]))

panels.append(gauge("Disk used % per mount", prom_target(
    '100 * (1 - node_filesystem_avail_bytes{fstype="ext4",vps_id=~"$vps"} / node_filesystem_size_bytes{fstype="ext4",vps_id=~"$vps"})',
    "{{vps_id}} {{mountpoint}}"), gp(0, 5, 8, 8),
    thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 80}, {"color": "red", "value": 90}]))
panels.append(timeseries("CPU busy % per VPS", [prom_target(
    '100 - (avg by (vps_id) (rate(node_cpu_seconds_total{mode="idle",vps_id=~"$vps"}[5m])) * 100)', "{{vps_id}}")],
    gp(8, 5, 8, 8), unit="percent"))
panels.append(timeseries("Memory used % per VPS", [prom_target(
    '100 * (1 - node_memory_MemAvailable_bytes{vps_id=~"$vps"} / node_memory_MemTotal_bytes{vps_id=~"$vps"})',
    "{{vps_id}}")], gp(16, 5, 8, 8), unit="percent"))

panels.append(timeseries("Disk free per mount", [prom_target(
    'node_filesystem_avail_bytes{fstype="ext4",vps_id=~"$vps"}', "{{vps_id}} {{mountpoint}}")],
    gp(0, 13, 8, 8), unit="bytes"))
panels.append(timeseries("Network I/O (ens3)", [
    prom_target('rate(node_network_receive_bytes_total{device="ens3",vps_id=~"$vps"}[5m])', "{{vps_id}} rx"),
    prom_target('-rate(node_network_transmit_bytes_total{device="ens3",vps_id=~"$vps"}[5m])', "{{vps_id}} tx"),
], gp(8, 13, 8, 8), unit="Bps"))
panels.append(timeseries("Load (1m) per VPS", [
    prom_target('node_load1{vps_id=~"$vps"}', "{{vps_id}}"),
], gp(16, 13, 8, 8), unit="short", fill=0))

# ── Row: Containers — fleet (cAdvisor) ──────────────────────────────────────
panels.append(row("Containers — fleet (cAdvisor)", 21))
panels.append(stat("Running containers", prom_target(
    'count by (vps_id) (container_last_seen{name!="",vps_id=~"$vps"})', "{{vps_id}}", instant=True),
    gp(0, 22, 4, 8), unit="short", decimals=0, color_mode="value",
    thresholds=[{"color": "blue", "value": None}]))
panels.append(timeseries("Top 10 containers — CPU", [prom_target(
    'topk(10, sum by (vps_id, name) (rate(container_cpu_usage_seconds_total{name!="",vps_id=~"$vps"}[5m])) * 100)',
    "{{vps_id}}/{{name}}")], gp(4, 22, 10, 8), unit="percent", legend_table=True))
panels.append(timeseries("Top 10 containers — Memory", [prom_target(
    'topk(10, sum by (vps_id, name) (container_memory_usage_bytes{name!="",vps_id=~"$vps"}))',
    "{{vps_id}}/{{name}}")], gp(14, 22, 10, 8), unit="bytes", legend_table=True))

panels.append(bargauge("Memory by container (top 15)", prom_target(
    'topk(15, sum by (vps_id, name) (container_memory_usage_bytes{name!="",vps_id=~"$vps"}))',
    "{{vps_id}}/{{name}}", instant=True), gp(0, 30, 12, 9), unit="bytes"))
panels.append(timeseries("Container network RX", [prom_target(
    'topk(10, sum by (vps_id, name) (rate(container_network_receive_bytes_total{name!="",vps_id=~"$vps"}[5m])))',
    "{{vps_id}}/{{name}}")], gp(12, 30, 12, 9), unit="Bps", legend_table=True))

# ── Row: Web traffic — fleet (Loki nginx-domain-access) ─────────────────────
panels.append(row("Web traffic — fleet", 39))
panels.append(stat("Requests / s", loki_target(
    'sum(rate({job="nginx-domain-access", vps_id=~"$vps"}[5m]))', "req/s"),
    gp(0, 40, 4, 8), unit="reqps", color_mode="value",
    thresholds=[{"color": "green", "value": None}], graph=True))
panels.append(stat("5xx / s", loki_target(
    'sum(rate({job="nginx-domain-access", vps_id=~"$vps", status=~"5.."}[5m]))', "5xx/s"),
    gp(4, 40, 4, 8), unit="reqps", color_mode="background", graph=True,
    thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.1}, {"color": "red", "value": 1}]))
panels.append(timeseries("Requests by status", [loki_target(
    'sum by (status) (rate({job="nginx-domain-access", vps_id=~"$vps"}[5m]))', "{{status}}")],
    gp(8, 40, 16, 8), unit="reqps", stack=True, ds="loki"))

panels.append(bargauge("Top domains by requests", loki_target(
    'topk(12, sum by (domain) (count_over_time({job="nginx-domain-access", vps_id=~"$vps"}[$__range])))',
    "{{domain}}", instant=True), gp(0, 48, 8, 9), unit="short"))
panels.append(timeseries("Requests by domain", [loki_target(
    'sum by (domain) (rate({job="nginx-domain-access", vps_id=~"$vps"}[5m]))', "{{domain}}")],
    gp(8, 48, 10, 9), unit="reqps", stack=True, ds="loki"))
panels.append(piechart("Requests by method", loki_target(
    'sum by (method) (count_over_time({job="nginx-domain-access", vps_id=~"$vps"}[$__range]))',
    "{{method}}", instant=True), gp(18, 48, 6, 9)))

# ── Row: Logs & audit — fleet (Loki) ────────────────────────────────────────
panels.append(row("Logs & audit — fleet", 57))
panels.append(timeseries("Log volume by VPS", [loki_target(
    'sum by (vps_id) (rate({vps_id=~"$vps", job=~"nginx-domain-access|nginx-error|systemd-journal"}[5m]))',
    "{{vps_id}}")], gp(0, 58, 24, 7), unit="logs/s" if False else "short", stack=True, ds="loki"))
panels.append(logs("NGINX errors", '{job="nginx-error", vps_id=~"$vps"}', gp(0, 65, 12, 10)))
panels.append(logs("VSA audit events", '{job="vsa-audit", vps_id=~"$vps"}', gp(12, 65, 12, 10)))

# Set the panel-level datasource from each panel's first target. Without this,
# Grafana falls back to the default datasource (Loki) to interpret the panel, so
# Prometheus panels send PromQL to Loki and render "No data" with a query error.
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
            "name": "vps",
            "type": "query",
            "label": "VPS",
            "datasource": {"type": "loki", "uid": LOKI},
            "query": {"label": "vps_id", "stream": "", "type": 1},
            "definition": "label_values(vps_id)",
            "refresh": 2,
            "includeAll": True,
            "allValue": ".+",
            "multi": True,
            "current": {"text": "All", "value": "$__all"},
        }
    ]
}

dashboard = {
    "uid": "vsa-fleet-overview",
    "title": "VSA — Fleet Overview",
    "tags": ["vsa", "fleet", "flowbiz"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 0,
    "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"},
    "templating": templating,
    "panels": panels,
    "editable": True,
}

print(json.dumps(dashboard, indent=2))
