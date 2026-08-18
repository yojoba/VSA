#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Génère le tableau de bord « LokalFlash — Infrastructure » (carte + drill-down).

    python3 build_infra_dashboard.py > dashboards/lokalflash-infra.json

CE QU'IL MONTRE
---------------
La CARTE de l'architecture, disposée dans le sens du trafic (Internet →
équilibreur → ingress → applicatif → données), chaque composant coloré par son
état réel ; puis les NŒUDS (le goulot mesuré) ; puis un drill-down complet
jusqu'au conteneur, piloté par des variables.

🔴 POURQUOI UNE GRILLE DISPOSÉE, ET NON UN PANNEAU « CANVAS ».
Grafana sait dessiner des topologies (Canvas, Node Graph), mais au prix de
coordonnées figées et d'un format de données que Prometheus ne rend pas
naturellement. Une grille dont la DISPOSITION porte le sens — une colonne par
étage du trafic — donne la même lecture d'un coup d'œil, se régénère sans
retouche, et chaque case reste un vrai panneau : cliquable, avec ses seuils et
son lien de drill-down. On perd les flèches, on gagne de ne jamais mentir.

SOURCES
-------
  kube-state-metrics  → l'inventaire (pods, déploiements, jobs, HPA)
  node-exporter       → CPU/mémoire/disque des NŒUDS
  cAdvisor            → CPU/mémoire PAR CONTENEUR (le dernier étage du drill-down)
Tous poussés vers le hub par l'agent du cluster (deploy/monitoring/).
"""
from __future__ import annotations
import json

PROM = "vsa-prometheus"
DS = {"type": "prometheus", "uid": PROM}
NS = "lokalflash"

_id = 0
def nid():
    global _id; _id += 1; return _id

def t(expr, legend="", instant=False, fmt=None):
    x = {"datasource": DS, "expr": expr, "legendFormat": legend,
         "instant": instant, "range": not instant, "refId": "A"}
    if fmt: x["format"] = fmt
    return x

def row(title, y):
    return {"id": nid(), "type": "row", "title": title,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "collapsed": False, "panels": []}

def comp(title, expr, gp, *, desc="", unit="short", steps=None, links=None, text_mode="auto"):
    """Une case de la CARTE : réplicas prêts, colorée par l'état."""
    return {
        "id": nid(), "type": "stat", "title": title, "gridPos": gp,
        "datasource": DS, "description": desc,
        "targets": [t(expr, "", instant=True)],
        "links": links or [],
        "fieldConfig": {"defaults": {
            "unit": unit, "decimals": 0,
            "thresholds": {"mode": "absolute", "steps": steps or [
                {"color": "red", "value": None}, {"color": "green", "value": 1}]},
            "color": {"mode": "thresholds"},
            "links": links or [],
        }, "overrides": []},
        "options": {"colorMode": "background", "graphMode": "none", "justifyMode": "center",
                    "textMode": text_mode,
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
    }

def ts(title, targets, gp, *, unit="short", desc="", stack=False, maximum=None,
       thresholds=None, legend_mode="list", decimals=None):
    tg = []
    for i, (e, l) in enumerate(targets):
        x = t(e, l); x["refId"] = chr(ord("A") + i); tg.append(x)
    d = {"unit": unit, "min": 0,
         "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 12,
                    "showPoints": "never", "spanNulls": True,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                    "gradientMode": "opacity"},
         "color": {"mode": "palette-classic"}}
    if maximum is not None: d["max"] = maximum
    if decimals is not None: d["decimals"] = decimals
    if thresholds:
        d["thresholds"] = {"mode": "absolute", "steps": thresholds}
        d["custom"]["thresholdsStyle"] = {"mode": "line"}
    return {"id": nid(), "type": "timeseries", "title": title, "gridPos": gp,
            "datasource": DS, "description": desc, "targets": tg,
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"legend": {"displayMode": legend_mode, "placement": "bottom",
                                   "showLegend": True,
                                   "calcs": ["mean", "max"] if legend_mode == "table" else []},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}

def tbl(title, targets, gp, *, desc="", excl=None, rename=None, overrides=None, steps=None):
    tg = []
    for i, (e, l) in enumerate(targets):
        x = t(e, l, instant=True, fmt="table"); x["refId"] = chr(ord("A") + i); tg.append(x)
    ex = {"Time": True, "job": True, "instance": True, "vps_id": True,
          "cluster": True, "__name__": True, "uid": True, "container": True,
          "endpoint": True, "service": True}
    ex.update(excl or {})
    return {"id": nid(), "type": "table", "title": title, "gridPos": gp,
            "datasource": DS, "description": desc, "targets": tg,
            "transformations": [
                {"id": "joinByField", "options": {"byField": "pod", "mode": "outer"}},
                {"id": "organize", "options": {"excludeByName": ex, "renameByName": rename or {}}},
            ] if len(tg) > 1 else [
                {"id": "organize", "options": {"excludeByName": ex, "renameByName": rename or {}}},
            ],
            "fieldConfig": {"defaults": {
                "custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True},
                "thresholds": {"mode": "absolute", "steps": steps or [{"color": "text", "value": None}]},
                "color": {"mode": "thresholds"}},
                "overrides": overrides or []},
            "options": {"showHeader": True, "footer": {"show": False}}}


panels, y = [], 0

# ══ RANGÉE 1 : LA CARTE ══════════════════════════════════════════════════════
panels.append(row("① Carte de l'infrastructure — disposée dans le sens du trafic", y)); y += 1

DRILL = [{"title": "Voir le détail des pods", "url": "/d/lokalflash-infra?viewPanel=&var-ns=" + NS}]

def ready(dep):
    return f'kube_deployment_status_replicas_ready{{namespace="{NS}",deployment="{dep}"}}'

# Étage 1 : le bord (ce qui reçoit le trafic public)
panels.append(comp("① Ingress ×2", 'kube_deployment_status_replicas_ready{namespace="ingress-nginx"}',
    {"h": 5, "w": 4, "x": 0, "y": y},
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 2}],
    desc="Les deux contrôleurs qui reçoivent tout le trafic public, un par nœud "
         "(anti-affinité souple). Un seul restant = le bord n'a plus de redondance."))
# Étage 2 : l'applicatif
panels.append(comp("② API", ready("api"), {"h": 5, "w": 4, "x": 4, "y": y},
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 2}, {"color": "green", "value": 3}],
    desc="Le cœur applicatif : offres, paiements, temps réel, tâches de fond. "
         "3 au repos, jusqu'à 12 par l'autoscaler."))
panels.append(comp("② Frontend", ready("frontend"), {"h": 5, "w": 4, "x": 8, "y": y},
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 2}],
    desc="L'application cliente (fichiers statiques servis par nginx)."))
panels.append(comp("② Site vitrine", ready("website"), {"h": 5, "w": 4, "x": 12, "y": y},
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 2}],
    desc="Le site marketing. ⚠️ Ses deux réplicas sont CO-LOCALISÉS sur un même "
         "nœud à dessein : son volume CMS est RWO et ne s'attache qu'à un nœud."))
# Étage 3 : les données
panels.append(comp("③ PgBouncer", 'kube_deployment_status_replicas_ready{namespace="' + NS + '",deployment=~"lokalflash-pg-pooler.*"}',
    {"h": 5, "w": 4, "x": 16, "y": y},
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 2}],
    desc="Le mutualiseur de connexions devant Postgres, en mode TRANSACTION — "
         "d'où la contrainte `simple_protocol` sur toutes les écritures jsonb."))
panels.append(comp("③ Postgres", 'count(kube_pod_info{namespace="' + NS + '",pod=~"lokalflash-pg-[0-9]+"})',
    {"h": 5, "w": 4, "x": 20, "y": y},
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 2}],
    desc="CNPG : un primaire et une réplique, sur deux nœuds et deux datacentres. "
         "Un seul restant = plus de haute disponibilité."))
y += 5

# Étage 4 : le soutien
for i, (label, sel, d) in enumerate([
    ("Valkey", 'deployment="valkey"', "Relais temps réel entre réplicas + verrous des tâches de fond. "
     "Sans lui : livraison SSE locale seulement, et les crons perdent leur élection."),
    ("MinIO", 'deployment="minio"', "Stockage des photos (commerces, offres, avatars). Sur volume persistant "
     "depuis la perte de 2026-06-12 — jamais sur emptyDir."),
    ("Push worker", 'deployment="push-worker"', "Envoi des notifications."),
    ("Scheduler", 'deployment="scheduler"', "Ordonnanceur des tâches planifiées."),
    ("Registre", 'deployment="registry"', "Registre d'images interne. ⚠️ Saturé = TOUT déploiement bloqué "
     "et image corrompue au push. Nettoyage hebdomadaire le dimanche à 04 h."),
    ("CloudBeaver", 'deployment="cloudbeaver"', "Interface SQL d'administration, jamais exposée publiquement."),
]):
    panels.append(comp(label, f'kube_deployment_status_replicas_ready{{namespace="{NS}",{sel}}}',
        {"h": 4, "w": 4, "x": (i % 6) * 4, "y": y}, desc=d,
        steps=[{"color": "red", "value": None}, {"color": "green", "value": 1}]))
y += 4

# ══ RANGÉE 2 : LES NŒUDS ═════════════════════════════════════════════════════
panels.append(row("② Nœuds — le goulot d'étranglement mesuré", y)); y += 1
panels.append(ts("CPU par nœud", [(
    '100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)', "{{node}}")],
    {"h": 8, "w": 12, "x": 0, "y": y}, unit="percent", maximum=100, decimals=1,
    thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 90}],
    desc="🔴 LE PLAFOND DE CAPACITÉ EST ICI, PAS DANS LES PODS. Mesuré le "
         "2026-08-18 : à saturation (~700 req/s) un nœud était à 99 % pendant que "
         "les pods api restaient à 13 % de leur limite. Ajouter des réplicas ne "
         "déplacerait pas ce plafond — seul un troisième nœud le ferait. "
         "⚠️ Les deux nœuds ne montent PAS ensemble : le primaire Postgres vit sur "
         "l'un d'eux, ce qui crée un déséquilibre structurel."))
panels.append(ts("Mémoire par nœud", [(
    '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100', "{{instance}}")],
    {"h": 8, "w": 12, "x": 12, "y": y}, unit="percent", maximum=100, decimals=1,
    thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 80}, {"color": "red", "value": 92}],
    desc="La mémoire est la ressource la plus chargée des deux (~45-50 % au repos). "
         "C'est elle qui limitera en premier le nombre de pods qu'on peut ajouter."))
y += 8

panels.append(ts("Disque des nœuds", [(
    '(1 - (node_filesystem_avail_bytes{cluster="pck-vpe3ary",mountpoint="/"} '
    '/ node_filesystem_size_bytes{cluster="pck-vpe3ary",mountpoint="/"})) * 100', "{{instance}}")],
    {"h": 7, "w": 12, "x": 0, "y": y}, unit="percent", maximum=100, decimals=1,
    thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 88}, {"color": "red", "value": 94}],
    desc="Le disque système de chaque nœud (21 Go). Plein, il empêche d'écrire les "
         "journaux et de tirer une image, et fait passer le nœud en pression disque — "
         "le kubelet se met alors à évincer des pods."))

# 🔴 LES VOLUMES PERSISTANTS SONT LES DISQUES QUI COMPTENT LE PLUS ICI, et leur
# occupation ne vient QUE du kubelet : kube-state-metrics les inventorie sans
# jamais dire ce qu'ils contiennent. Sans ce panneau, un volume qui se remplit
# n'apparaîtrait sur AUCUN écran — or MinIO a déjà perdu tous ses envois une fois
# (2026-06-12), et une saturation du registre bloque TOUT déploiement en
# corrompant l'image au push.
panels.append({
    "id": nid(), "type": "bargauge", "title": "Volumes persistants — occupation",
    "gridPos": {"h": 7, "w": 12, "x": 12, "y": y}, "datasource": DS,
    "description": "MinIO porte les photos de commerces et d'offres ; `website-cms` le "
                   "contenu du site ; `lokalflash-pg-*` les données. ⚠️ `registry-data` "
                   "mérite une attention particulière : saturé, il bloque TOUT déploiement "
                   "ET corrompt l'image au moment du push — d'où le nettoyage hebdomadaire "
                   "du dimanche 04 h.",
    "targets": [t('(kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) * 100',
                  "{{persistentvolumeclaim}}", instant=True)],
    "fieldConfig": {"defaults": {
        "unit": "percent", "min": 0, "max": 100, "decimals": 1,
        "thresholds": {"mode": "absolute", "steps": [
            {"color": "green", "value": None}, {"color": "orange", "value": 75},
            {"color": "red", "value": 88}]},
        "color": {"mode": "thresholds"}}, "overrides": []},
    "options": {"displayMode": "gradient", "orientation": "horizontal",
                "showUnfilled": True,
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
})
y += 7

# ══ RANGÉE 3 : DRILL-DOWN ════════════════════════════════════════════════════
panels.append(row("③ Drill-down — chaque pod, chaque conteneur", y)); y += 1
panels.append(tbl("Pods — état, redémarrages, nœud",
    [('kube_pod_info{namespace=~"$ns"}', ""),
     ('sum by (pod) (kube_pod_container_status_restarts_total{namespace=~"$ns"})', ""),
     ('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=~"$ns"}[5m])) * 1000', ""),
     ('sum by (pod) (container_memory_working_set_bytes{namespace=~"$ns"}) / 1024 / 1024', "")],
    {"h": 11, "w": 24, "x": 0, "y": y},
    desc="Un pod par ligne, filtrable par la variable en haut. Les colonnes de "
         "ressources viennent de cAdvisor : elles peuvent manquer quelques minutes "
         "après la création d'un pod, le temps du premier relevé. Cliquer un "
         "en-tête pour trier ; le champ de filtre de chaque colonne permet de "
         "chercher un pod par son nom.",
    # `host_network` était resté visible : une colonne qui vaut « false » sur
    # toutes les lignes n'apprend rien et pousse les colonnes utiles hors écran.
    excl={"created_by_kind": True, "created_by_name": True, "host_ip": True,
          "pod_ip": True, "priority_class": True, "uid": True,
          "host_network": True, "node_ip": True, "namespace": False},
    rename={"pod": "Pod", "namespace": "Espace", "node": "Nœud",
            "Value #A": "—", "Value #B": "Redémarrages",
            "Value #C": "CPU (m)", "Value #D": "Mémoire (Mio)"},
    overrides=[
        {"matcher": {"id": "byName", "options": "Redémarrages"},
         "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                            {"color": "green", "value": None}, {"color": "orange", "value": 1},
                            {"color": "red", "value": 5}]}}]},
        {"matcher": {"id": "byName", "options": "CPU (m)"},
         "properties": [{"id": "unit", "value": "short"}, {"id": "decimals", "value": 0}]},
        {"matcher": {"id": "byName", "options": "Mémoire (Mio)"},
         "properties": [{"id": "unit", "value": "short"}, {"id": "decimals", "value": 0}]},
        {"matcher": {"id": "byName", "options": "—"},
         "properties": [{"id": "custom.hidden", "value": True}]},
    ]))
y += 11

panels.append(ts("CPU par pod", [(
    'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=~"$ns",pod=~"$pod"}[5m])) * 1000',
    "{{pod}}")], {"h": 9, "w": 12, "x": 0, "y": y}, unit="short", legend_mode="table", decimals=0,
    desc="Millicœurs consommés par pod. Sélectionner un pod dans la variable du "
         "haut pour isoler. Rappel : la limite d'un pod api est de 2000 m."))
panels.append(ts("Mémoire par pod", [(
    'sum by (pod) (container_memory_working_set_bytes{namespace=~"$ns",pod=~"$pod"})',
    "{{pod}}")], {"h": 9, "w": 12, "x": 12, "y": y}, unit="bytes", legend_mode="table",
    desc="Mémoire réellement utilisée (working set), pas la mémoire réservée. "
         "C'est cette valeur qui déclenche une éviction quand elle atteint la limite."))
y += 9

# ══ RANGÉE 4 : ANOMALIES ═════════════════════════════════════════════════════
panels.append(row("④ Ce qui ne va pas — trié par anomalie", y)); y += 1
panels.append(tbl("Charges de travail incomplètes",
    [('kube_deployment_status_replicas_ready - kube_deployment_spec_replicas != 0', "")],
    {"h": 7, "w": 12, "x": 0, "y": y},
    desc="Un déploiement dont le nombre de réplicas PRÊTS diffère du nombre "
         "DEMANDÉ. Vide = tout est complet, et c'est l'état normal. 🔴 Ce panneau "
         "est conçu pour rester VIDE : une ligne qui apparaît est déjà une anomalie, "
         "pas une information à interpréter.",
    excl={"namespace": False},
    rename={"deployment": "Déploiement", "namespace": "Espace", "Value": "Écart"}))
panels.append(ts("Redémarrages de conteneurs", [(
    'sum by (pod) (increase(kube_pod_container_status_restarts_total[15m])) > 0', "{{pod}}")],
    {"h": 7, "w": 12, "x": 12, "y": y}, unit="short", legend_mode="table", decimals=0,
    desc="Redémarrages sur 15 minutes glissantes. Un pod qui redémarre en boucle "
         "y apparaît en escalier. Vide = aucun redémarrage, l'état normal."))

dash = {
    "uid": "lokalflash-infra",
    "title": "LokalFlash — Infrastructure",
    "tags": ["lokalflash", "k8s", "infrastructure"],
    "timezone": "Europe/Zurich",
    "schemaVersion": 39, "version": 0, "refresh": "1m",
    "time": {"from": "now-3h", "to": "now"},
    "editable": True, "graphTooltip": 1,
    "templating": {"list": [
        {"name": "ns", "label": "Espace de noms", "type": "query", "datasource": DS,
         "query": {"query": "label_values(kube_pod_info, namespace)", "refId": "A"},
         "refresh": 1, "includeAll": True, "multi": True,
         "current": {"text": [NS], "value": [NS]},
         "description": "Filtre les tableaux et courbes de drill-down."},
        {"name": "pod", "label": "Pod", "type": "query", "datasource": DS,
         "query": {"query": 'label_values(kube_pod_info{namespace=~"$ns"}, pod)', "refId": "A"},
         "refresh": 2, "includeAll": True, "multi": True,
         "current": {"text": ["All"], "value": ["$__all"]},
         "description": "Isoler un ou plusieurs pods dans les courbes de ressources."},
    ]},
    "description": (
        "Carte de l'infrastructure du cluster de production (pck-vpe3ary, Infomaniak DC3) "
        "et drill-down jusqu'au conteneur. La rangée ① est disposée dans le SENS DU TRAFIC : "
        "bord → applicatif → données, puis le soutien en dessous. "
        "Sources : kube-state-metrics (inventaire), node-exporter (nœuds), cAdvisor (conteneurs), "
        "poussées vers ce hub par l'agent du cluster — aucune surface publique nouvelle. "
        "🔴 Le plafond de capacité est le CPU des NŒUDS (rangée ②), pas les pods : à saturation "
        "(~700 req/s mesurés) un nœud était à 99 % pendant que les pods api restaient à 13 % de "
        "leur limite. Généré par build_infra_dashboard.py — ne pas éditer à la main."
    ),
    "panels": panels,
}
print(json.dumps(dash, indent=2, ensure_ascii=False))
