#!/usr/bin/env python3
"""Génère le tableau de bord Grafana « LokalFlash — Production K8s ».

Source de vérité unique du dashboard `lokalflash-k8s`. Régénérer puis publier :

    python3 build_lokalflash_dashboard.py > dashboards/lokalflash.json
    curl -s -X POST "$GRAFANA/api/dashboards/db" -H 'Content-Type: application/json' \
         -d "{\"dashboard\": $(cat dashboards/lokalflash.json), \"overwrite\": true}"

CE QUE CE TABLEAU MONTRE, ET CE QU'IL NE PEUT PAS MONTRER
---------------------------------------------------------
Deux sources, et deux seulement :

  * `lokalflash-k8s` — les métriques applicatives exposées par les pods api
    (`/_/metrics`). Elles décrivent le CLUSTER, jamais un pod : le scrape passe
    par l'ingress public et atterrit sur un pod au hasard.
  * `blackbox` — les sondes externes depuis ce hub, hors cluster. C'est la seule
    mesure de ce que voit RÉELLEMENT un utilisateur, latence réseau comprise.

🔴 CE QUI MANQUE, ET IL FAUT LE SAVOIR EN LISANT : le CPU des NŒUDS Kubernetes
n'est pas dans Prometheus (il vit dans metrics-server, interrogé par `kubectl
top`). Or c'est précisément lui qui borne la capacité — mesuré le 2026-08-18,
la saturation arrive à ~700 req/s avec un nœud à 99 % pendant que les pods api
restent à 13 % de leur limite. Le tableau contourne ce trou en rapportant le
débit au plafond MESURÉ plutôt qu'à une utilisation processeur. Les journaux du
cluster ne sont pas non plus dans Loki (il ne collecte que vps-01).

POURQUOI LA CAPACITÉ EST LA PREMIÈRE CHOSE APRÈS L'ÉTAT
--------------------------------------------------------
Le troisième nœud est volontairement reporté (coût, jusqu'à l'extension à la
Suisse entière). Tant qu'il l'est, « à quelle distance du plafond sommes-nous »
devient la question récurrente — d'où une jauge dédiée, calée sur une valeur
mesurée et non supposée.
"""
from __future__ import annotations

import json

PROM = "vsa-prometheus"

# 🔴 Plafond MESURÉ le 2026-08-18, pas estimé : k6 depuis flowbiz-1 (donc hors
# cluster, chemin public complet LB→ingress→api). À 1200 req/s demandés, 703
# atteints, un nœud à 99 % de CPU et l'autre à 72 %. Les pods api n'étaient qu'à
# 13 % de leur limite : ajouter des réplicas ne déplacerait pas ce plafond, seul
# un nœud le ferait. Refaire la mesure après tout changement de nœuds.
CAPACITY_RPS = 700


# 🔴 LE DATASOURCE SE DÉCLARE AU NIVEAU DU PANNEAU, PAS SEULEMENT DE LA REQUÊTE.
#
# Vu à l'écran le 2026-08-18 : les seize panneaux affichaient « No data » avec
# « parse error at line 1, col 1: syntax error: unexpected IDENTIFIER » — la
# signature d'une erreur LogQL. Cause : **Loki est le datasource PAR DÉFAUT** de
# ce Grafana, et un panneau sans `datasource` propre y retombe, quel que soit ce
# que déclarent ses requêtes. Du PromQL était donc envoyé à Loki.
#
# Rien ne l'avait montré avant : les vingt expressions avaient été validées
# directement contre Prometheus (elles rendaient toutes des données), le
# provisionnement s'était fait sans erreur, et le tableau était bien en base.
# Seul l'affichage réel pouvait l'attraper.
DS = {"type": "prometheus", "uid": PROM}

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def t(expr, legend="", instant=False):
    return {
        "datasource": {"type": "prometheus", "uid": PROM},
        "expr": expr,
        "legendFormat": legend,
        "instant": instant,
        "range": not instant,
        "refId": "A",
    }


def stat(title, expr, gp, *, unit="short", decimals=0, steps=None, legend="",
         desc="", mappings=None, color_mode="background", graph=False):
    return {
        "id": nid(), "type": "stat", "title": title, "gridPos": gp, "datasource": DS,
        "description": desc,
        "targets": [t(expr, legend, instant=True)],
        "fieldConfig": {"defaults": {
            "unit": unit, "decimals": decimals,
            "mappings": mappings or [],
            "thresholds": {"mode": "absolute", "steps": steps or [{"color": "green", "value": None}]},
            "color": {"mode": "thresholds"},
        }, "overrides": []},
        "options": {
            "colorMode": color_mode, "graphMode": "area" if graph else "none",
            "justifyMode": "auto", "textMode": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def gauge(title, expr, gp, *, unit="percent", max_=100, steps=None, desc=""):
    return {
        "id": nid(), "type": "gauge", "title": title, "gridPos": gp, "datasource": DS,
        "description": desc,
        "targets": [t(expr, instant=True)],
        "fieldConfig": {"defaults": {
            "unit": unit, "min": 0, "max": max_, "decimals": 1,
            "thresholds": {"mode": "absolute", "steps": steps or [
                {"color": "green", "value": None},
                {"color": "yellow", "value": 50},
                {"color": "orange", "value": 75},
                {"color": "red", "value": 90},
            ]},
            "color": {"mode": "thresholds"},
        }, "overrides": []},
        "options": {"showThresholdLabels": False, "showThresholdMarkers": True,
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
    }


def ts(title, targets, gp, *, unit="short", desc="", stack=False, fill=12,
       thresholds=None, legend_mode="list", decimals=None, minimum=0):
    tg = []
    for i, (expr, legend) in enumerate(targets):
        x = t(expr, legend)
        x["refId"] = chr(ord("A") + i)
        tg.append(x)
    defaults = {
        "unit": unit,
        "custom": {
            "drawStyle": "line", "lineWidth": 2, "fillOpacity": fill,
            "showPoints": "never", "spanNulls": True,
            "stacking": {"mode": "normal" if stack else "none", "group": "A"},
            "gradientMode": "opacity",
        },
        "color": {"mode": "palette-classic"},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    if minimum is not None:
        defaults["min"] = minimum
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
        defaults["custom"]["thresholdsStyle"] = {"mode": "line"}
    return {
        "id": nid(), "type": "timeseries", "title": title, "gridPos": gp, "datasource": DS,
        "description": desc, "targets": tg,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {"displayMode": legend_mode, "placement": "bottom", "showLegend": True,
                       "calcs": ["mean", "max"] if legend_mode == "table" else []},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def table(title, expr, gp, *, desc="", unit="s", steps=None):
    return {
        "id": nid(), "type": "table", "title": title, "gridPos": gp, "datasource": DS,
        "description": desc,
        # 🔴 `format: "table"` — SANS LUI, RIEN NE S'AFFICHE COMME UN TABLEAU.
        # Une requête instantanée qui rend plusieurs séries produit un FRAME PAR
        # SÉRIE : Grafana n'affiche alors qu'une seule valeur, avec un menu
        # déroulant pour choisir la série. Vu à l'écran le 2026-08-18. En mode
        # `table`, Prometheus rend un frame unique où chaque étiquette devient
        # une colonne — c'est ce que le panneau attend.
        "targets": [dict(t(expr, "", instant=True), format="table")],
        "transformations": [
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "job": True, "instance": True,
                                  "vps_id": True, "cluster": True, "__name__": True},
                "renameByName": {"name": "Tâche de fond", "Value": "Depuis le dernier passage"},
            }},
        ],
        "fieldConfig": {"defaults": {
            "unit": unit,
            "custom": {"align": "auto", "cellOptions": {"type": "color-text"}},
            # 🔴 UN SEUL SEUIL, ET IL EST HAUT — parce que les cadences DIFFÈRENT.
            # Première version : orange à 15 min, rouge à 90 min. Résultat vu à
            # l'écran le 2026-08-18 : `push-subs-cleanup` en ROUGE à 7 h et deux
            # autres en orange, alors que ce sont des tâches JOURNALIÈRES et que
            # c'est parfaitement normal. Un tableau dont trois lignes sont
            # perpétuellement rouges apprend à ignorer le rouge.
            # 26 h est le seul seuil qui soit anormal pour TOUTES les tâches,
            # journalières comprises. Les tâches rapides, elles, ont leur propre
            # voyant en rangée ① (seuil 15 min, aligné sur l'alerte par e-mail).
            "thresholds": {"mode": "absolute", "steps": steps or [
                {"color": "text", "value": None},
                {"color": "red", "value": 93600},
            ]},
            "color": {"mode": "thresholds"},
        }, "overrides": []},
        "options": {"showHeader": True, "sortBy": [{"desc": True, "displayName": "Depuis le dernier passage"}]},
    }


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
            "collapsed": False, "panels": []}


UP_DOWN = [
    {"options": {"0": {"text": "HORS SERVICE", "color": "red", "index": 0}}, "type": "value"},
    {"options": {"1": {"text": "en ligne", "color": "green", "index": 1}}, "type": "value"},
]

panels = []
y = 0

# ── RANGÉE 1 : l'état, lisible en dix secondes ───────────────────────────────
panels.append(row("① État — la vue de dix secondes", y)); y += 1
panels.append(stat(
    "Application", 'probe_success{instance="https://app.lokalflash.ch/api/health"}',
    {"h": 4, "w": 4, "x": 0, "y": y}, mappings=UP_DOWN,
    steps=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
    desc="Sonde externe depuis ce hub, hors cluster : la seule qui voie une panne de bord "
         "(ingress, DNS, TLS). Sentry, lui, ne peut rien voir d'un bord éteint."))
panels.append(stat(
    "Site vitrine", 'probe_success{instance="https://www.lokalflash.ch/"}',
    {"h": 4, "w": 4, "x": 4, "y": y}, mappings=UP_DOWN,
    steps=[{"color": "red", "value": None}, {"color": "green", "value": 1}]))
panels.append(stat(
    "Erreurs serveur (1 h)", 'sum(sum_over_time(lf_requests_last_minute{class="server_error"}[1h])) or vector(0)',
    {"h": 4, "w": 4, "x": 8, "y": y},
    steps=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 10}],
    desc="Des utilisateurs ont reçu un échec. L'alerte se déclenche à 10 sur 5 min — "
         "seuil ABSOLU et non un taux : à faible trafic, 1 erreur sur 2 requêtes ferait 50 %."))
panels.append(stat(
    "Réplicas d'API", "lf_cluster_pods", {"h": 4, "w": 4, "x": 12, "y": y},
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 2}, {"color": "green", "value": 3},
           {"color": "orange", "value": 12}],
    desc="3 au repos, jusqu'à 12 par l'autoscaler. Sous 2 : plus de tolérance de panne. "
         "À 12 : le plafond de l'autoscaler est atteint — mais le vrai goulot est le CPU des nœuds."))
panels.append(stat(
    "Certificat TLS", '(min(probe_ssl_earliest_cert_expiry) - time()) / 86400',
    {"h": 4, "w": 4, "x": 16, "y": y}, unit="d",
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 3}, {"color": "green", "value": 14}],
    desc="Filet de sécurité : cert-manager renouvelle ~30 jours avant. Ne descend que si le "
         "renouvellement est réellement cassé."))
# 🔴 CE VOYANT NE REGARDE QUE LES TÂCHES À CADENCE RAPIDE, ET C'EST LE MÊME
# SOUS-ENSEMBLE QUE L'ALERTE. Première version : `max()` sur TOUTES les tâches —
# donc sur les journalières, qui affichent normalement plusieurs heures. Le
# voyant restait orange en permanence (vu à l'écran : « 7 hours »), et un voyant
# perpétuellement orange apprend à être ignoré. Pire, il aurait CONTREDIT
# l'alerte, qui n'a jamais surveillé ces tâches-là. Le seuil de 900 s est
# exactement celui de `_LF_CRON_MAX_AGE_S` côté alerteur : l'écran et le mail
# disent désormais la même chose.
panels.append(stat(
    "Tâches rapides — dernier passage",
    'max(lf_cron_last_run_age_seconds{name=~"recurring-push|device-presence"})',
    {"h": 4, "w": 4, "x": 20, "y": y}, unit="s",
    steps=[{"color": "green", "value": None}, {"color": "red", "value": 900}],
    desc="Les deux tâches qui tournent toutes les 60 secondes, dont **recurring-push** — "
         "celle qui envoie les notifications de flash deals. Rouge au-delà de 15 min, le "
         "seuil exact de l'alerte par e-mail. Les tâches journalières ne sont PAS ici : "
         "elles affichent normalement plusieurs heures, et les mêler rendrait ce voyant "
         "orange en permanence. Elles figurent dans le tableau détaillé plus bas."))
y += 4

# ── RANGÉE 2 : la capacité — la question du 3ᵉ nœud ──────────────────────────
panels.append(row("② Capacité — à quelle distance du plafond ?", y)); y += 1
panels.append(gauge(
    "Part de la capacité mesurée", f"lf_cluster_requests_per_second / {CAPACITY_RPS} * 100",
    {"h": 7, "w": 6, "x": 0, "y": y},
    desc=f"Débit rapporté au plafond MESURÉ de ~{CAPACITY_RPS} req/s (k6 depuis ce hub, chemin "
         "public complet, 2026-08-18). Le plafond est le CPU des NŒUDS : à saturation un nœud "
         "était à 99 % pendant que les pods api restaient à 13 % de leur limite. **Ajouter des "
         "réplicas ne le déplacerait pas — seul un troisième nœud le ferait.** Au-delà de 75 % "
         "durablement, c'est le moment d'en reparler."))
panels.append(ts(
    "Requêtes par seconde", [("lf_cluster_requests_per_second", "cluster")],
    {"h": 7, "w": 12, "x": 6, "y": y}, unit="reqps", decimals=2,
    thresholds=[{"color": "green", "value": None}, {"color": "red", "value": CAPACITY_RPS}],
    desc=f"La ligne rouge marque le plafond mesuré ({CAPACITY_RPS} req/s). "
         "Instantané, sommé sur tous les réplicas via Valkey."))
panels.append(stat(
    "Pic sur la période", "max_over_time(lf_cluster_requests_per_second[$__range])",
    {"h": 7, "w": 6, "x": 18, "y": y}, unit="reqps", decimals=1, graph=True,
    steps=[{"color": "green", "value": None},
           {"color": "orange", "value": CAPACITY_RPS * 0.5},
           {"color": "red", "value": CAPACITY_RPS * 0.75}],
    desc="C'est le PIC qui dimensionne, pas la moyenne. Le scénario à surveiller est "
         "l'envoi d'une notification de flash deal : tout le monde ouvre l'app en même temps."))
y += 7

# ── RANGÉE 3 : latence ────────────────────────────────────────────────────────
panels.append(row("③ Latence — ce que ressent l'utilisateur", y)); y += 1
panels.append(ts(
    "Latence perçue de l'extérieur", [
        ('probe_duration_seconds{instance="https://app.lokalflash.ch/api/health"} * 1000', "application"),
        ('probe_duration_seconds{instance="https://www.lokalflash.ch/"} * 1000', "site vitrine"),
    ], {"h": 7, "w": 12, "x": 0, "y": y}, unit="ms",
    desc="Mesure de bout en bout depuis ce hub : réseau, LoadBalancer, TLS et application compris. "
         "C'est la seule courbe qui dise ce que vit vraiment quelqu'un qui ouvre l'app."))
panels.append(ts(
    "Latence côté serveur", [
        ("lf_request_duration_ms_p95", "p95 (palier)"),
        ("avg(lf_request_duration_ms_avg)", "moyenne, toutes routes"),
    ], {"h": 7, "w": 12, "x": 12, "y": y}, unit="ms",
    thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 2500}],
    desc="🔴 Le p95 est un PALIER d'histogramme, pas un centile exact : il saute de 250 à 500 à "
         "1000 ms. La valeur 10000 signifie « au-delà du dernier palier », soit plus de 5 s. "
         "Le flux temps réel (SSE) est EXCLU de ces deux courbes : ses connexions durent des "
         "minutes et rendraient la moyenne illisible pour toujours."))
y += 7

# ── RANGÉE 4 : trafic ─────────────────────────────────────────────────────────
panels.append(row("④ Trafic — d'où vient la charge", y)); y += 1
panels.append(ts(
    "Requêtes par famille de route", [('sum by (route) (lf_requests_last_minute) / 60', "{{route}}")],
    {"h": 8, "w": 12, "x": 0, "y": y}, unit="reqps", stack=True, legend_mode="table", decimals=2,
    desc="Les chemins sont regroupés en familles FERMÉES : stocker les URL brutes créerait une "
         "série par identifiant d'offre. « sonde » domine normalement (santé Kubernetes + sonde "
         "externe toutes les 15 s) — ce n'est pas du trafic d'utilisateurs."))
panels.append(ts(
    "Erreurs", [
        ('sum(lf_requests_last_minute{class="server_error"})', "serveur (5xx)"),
        ('sum(lf_requests_last_minute{class="client_error"})', "refus / non trouvés (4xx)"),
    ], {"h": 8, "w": 6, "x": 12, "y": y}, unit="short",
    desc="Les 4xx sont ATTENDUS (droits refusés, ressource absente, pré-vols CORS). "
         "Seuls les 5xx sont un incident."))
panels.append(ts(
    "Connexions temps réel", [("lf_cluster_realtime_connections", "flux SSE ouverts")],
    {"h": 8, "w": 6, "x": 18, "y": y}, unit="short",
    desc="Flux SSE ouverts sur tout le cluster — proche du nombre d'applications ouvertes sur un "
         "écran. ⚠️ Anonyme : le flux n'exige aucune authentification, donc trois onglets comptent "
         "trois. Pour savoir QUI est là, voir l'onglet Audience de /_/stats."))
y += 8

# ── RANGÉE 5 : tâches de fond et base ────────────────────────────────────────
panels.append(row("⑤ Tâches de fond et base de données", y)); y += 1
panels.append(table(
    "Dernier passage de chaque tâche de fond", "lf_cron_last_run_age_seconds",
    {"h": 8, "w": 12, "x": 0, "y": y},
    desc="🔴 SEULES TROIS TÂCHES SONT SURVEILLÉES PAR L'ÂGE, et c'est délibéré : sur les dix "
         "boucles, sept testent leur porte (heure visée, interrupteur de réglage, IMAP_HOST) "
         "AVANT de prendre leur verrou — elles n'horodatent donc que lorsqu'elles travaillent, "
         "et leur âge est irrégulier PAR CONSTRUCTION. Les trois fiables : recurring-push et "
         "device-presence (cadence 1 min), bounce-check (15 min). "
         "**recurring-push est la plus importante : figée, les notifications de flash deals "
         "n'atteignent plus personne, sans erreur nulle part.** Les autres lignes sont "
         "informatives ; une valeur de plusieurs heures y est normale."))
panels.append(ts(
    "Pool de connexions Postgres", [
        ('lf_pod_db_pool_connections{state="acquired"}', "en cours d'utilisation"),
        ('lf_pod_db_pool_connections{state="max"}', "maximum"),
    ], {"h": 8, "w": 12, "x": 12, "y": y}, unit="short",
    desc="⚠️ Seule mesure LOCALE du tableau : elle décrit le pod qui a répondu au scrape, pas le "
         "cluster. Elle saute donc d'un pod à l'autre — utile pour repérer une saturation "
         "durable, pas pour lire une valeur exacte. Le pool traverse PgBouncer (mode transaction)."))
y += 8

dashboard = {
    "uid": "lokalflash-k8s",
    "title": "LokalFlash — Production K8s",
    "tags": ["lokalflash", "k8s", "production"],
    "timezone": "Europe/Zurich",
    "schemaVersion": 39,
    "version": 0,
    "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"},
    "editable": True,
    "graphTooltip": 1,
    "description": (
        "Production LokalFlash sur Kubernetes (Infomaniak DC3, cluster pck-vpe3ary). "
        "Deux sources : les métriques applicatives des pods api (/_/metrics, décrivant le "
        "CLUSTER et non un pod) et les sondes externes depuis ce hub. "
        "🔴 NON VISIBLE ICI : le CPU des nœuds Kubernetes (il vit dans metrics-server, pas dans "
        "Prometheus) — or c'est lui qui borne la capacité. La rangée ② contourne ce trou en "
        "rapportant le débit au plafond MESURÉ. Les journaux du cluster ne sont pas dans Loki non "
        "plus (il ne collecte que vps-01). Généré par build_lokalflash_dashboard.py — ne pas "
        "éditer à la main, les modifications seraient perdues à la prochaine génération."
    ),
    "panels": panels,
}
print(json.dumps(dashboard, indent=2, ensure_ascii=False))
