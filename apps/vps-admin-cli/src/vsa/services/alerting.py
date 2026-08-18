"""Fleet alerting — collect problems and email a digest.

Self-contained, env-driven config (read from ``/etc/vsa/alert.env`` via the
systemd unit, or the shell). Covers two problem families:

* **certs / drift** — every finding from the hub's ``/fleet/drift`` report.
* **systems** — agents that stopped reporting (stale ``last_seen``) and
  containers that are down or unhealthy.
* **disk** — per-VPS/mountpoint usage over threshold (from Prometheus).
* **external** — synthetic blackbox probes of the public LokalFlash endpoints
  (uptime + TLS-cert expiry), an off-cluster check that catches edge outages
  in-cluster error tracking can't see.

A small state file (``alert-state.json``) records the set of currently-firing
problems so we only email on *changes* (new/escalated problems, or a
recovery) instead of every run — a mini Alertmanager.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any

import httpx

from vsa.services import hub_client

_LEVELS = {"info": 0, "warning": 1, "critical": 2}
_LEVEL_EMOJI = {"info": "🔵", "warning": "🟡", "critical": "🔴"}
_DEFAULT_STATE_PATH = Path("/var/lib/vsa/alert-state.json")


def _read_file(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""



# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class AlertConfig:
    smtp_host: str = "mail.infomaniak.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    recipients: list[str] = field(default_factory=list)
    min_level: str = "warning"
    agent_stale_minutes: int = 10
    ignore_containers: list[str] = field(default_factory=list)
    state_path: Path = _DEFAULT_STATE_PATH
    subject_prefix: str = "[VSA]"
    # Disk-usage alarms (queried from the hub's Prometheus, which holds
    # node_filesystem metrics for every VPS via remote-write).
    prometheus_url: str = "http://localhost:9090"
    disk_warn_percent: float = 85.0
    disk_crit_percent: float = 92.0
    disk_mounts: str = "/|/var/lib/docker"  # PromQL regex of mountpoints to watch
    # External synthetic probes (blackbox_exporter "blackbox" job) — TLS-expiry
    # thresholds in days. probe_success failures are always critical (no
    # threshold); these only gate the cert-expiry backstop.
    cert_warn_days: float = 14.0
    cert_crit_days: float = 3.0
    # K8s prod backup monitoring (read-only SA against the cluster API).
    k8s_api: str = ""
    k8s_token: str = ""
    k8s_ca_file: str = ""
    k8s_namespace: str = "lokalflash"
    k8s_pg_cluster: str = "lokalflash-pg"
    k8s_config_cronjob: str = "config-backup"
    db_backup_max_hours: float = 26.0
    config_backup_max_hours: float = 26.0

    @classmethod
    def from_env(cls) -> "AlertConfig":
        def _split(val: str) -> list[str]:
            return [p.strip() for p in val.replace(";", ",").split(",") if p.strip()]

        user = os.environ.get("VSA_ALERT_SMTP_USER", "")
        return cls(
            smtp_host=os.environ.get("VSA_ALERT_SMTP_HOST", "mail.infomaniak.com"),
            smtp_port=int(os.environ.get("VSA_ALERT_SMTP_PORT", "587")),
            smtp_user=user,
            smtp_password=os.environ.get("VSA_ALERT_SMTP_PASSWORD", ""),
            mail_from=os.environ.get("VSA_ALERT_FROM", user),
            recipients=_split(os.environ.get("VSA_ALERT_TO", "")),
            min_level=os.environ.get("VSA_ALERT_MIN_LEVEL", "warning").lower(),
            agent_stale_minutes=int(os.environ.get("VSA_ALERT_AGENT_STALE_MINUTES", "10")),
            ignore_containers=_split(os.environ.get("VSA_ALERT_IGNORE_CONTAINERS", "")),
            state_path=Path(os.environ.get("VSA_ALERT_STATE_PATH", str(_DEFAULT_STATE_PATH))),
            subject_prefix=os.environ.get("VSA_ALERT_SUBJECT_PREFIX", "[VSA]"),
            prometheus_url=os.environ.get("VSA_ALERT_PROMETHEUS_URL", "http://localhost:9090"),
            disk_warn_percent=float(os.environ.get("VSA_ALERT_DISK_WARN_PERCENT", "85")),
            disk_crit_percent=float(os.environ.get("VSA_ALERT_DISK_CRIT_PERCENT", "92")),
            disk_mounts=os.environ.get("VSA_ALERT_DISK_MOUNTS", "/|/var/lib/docker"),
            cert_warn_days=float(os.environ.get("VSA_ALERT_CERT_WARN_DAYS", "14")),
            cert_crit_days=float(os.environ.get("VSA_ALERT_CERT_CRIT_DAYS", "3")),
            k8s_api=os.environ.get("VSA_ALERT_K8S_API", ""),
            k8s_token=_read_file(os.environ.get("VSA_ALERT_K8S_TOKEN_FILE", "")),
            k8s_ca_file=os.environ.get("VSA_ALERT_K8S_CA_FILE", ""),
            k8s_namespace=os.environ.get("VSA_ALERT_K8S_NAMESPACE", "lokalflash"),
            k8s_pg_cluster=os.environ.get("VSA_ALERT_K8S_PG_CLUSTER", "lokalflash-pg"),
            k8s_config_cronjob=os.environ.get("VSA_ALERT_K8S_CONFIG_CRONJOB", "config-backup"),
            db_backup_max_hours=float(os.environ.get("VSA_ALERT_DB_BACKUP_MAX_HOURS", "26")),
            config_backup_max_hours=float(os.environ.get("VSA_ALERT_CONFIG_BACKUP_MAX_HOURS", "26")),
        )

    def validate(self) -> list[str]:
        errs = []
        if not self.smtp_user or not self.smtp_password:
            errs.append("VSA_ALERT_SMTP_USER / VSA_ALERT_SMTP_PASSWORD are required")
        if not self.mail_from:
            errs.append("VSA_ALERT_FROM (or VSA_ALERT_SMTP_USER) is required")
        if not self.recipients:
            errs.append("VSA_ALERT_TO must list at least one recipient")
        if self.min_level not in _LEVELS:
            errs.append(f"VSA_ALERT_MIN_LEVEL must be one of {sorted(_LEVELS)}")
        return errs


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    level: str       # critical | warning | info
    category: str    # cert | drift | agent | container | disk | endpoint
    vps: str
    target: str      # domain / vps_id / container name / probe URL
    detail: str

    @property
    def key(self) -> str:
        return f"{self.category}|{self.vps}|{self.target}|{self.level}"

    def line(self) -> str:
        emoji = _LEVEL_EMOJI.get(self.level, "•")
        return f"{emoji} [{self.level.upper()}] {self.category}: {self.target} ({self.vps}) — {self.detail}"


def _age_minutes(iso_ts: str, *, now: datetime) -> float | None:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 60.0


def problems_from_drift(report: dict[str, Any]) -> list[Problem]:
    out: list[Problem] = []
    for f in report.get("findings", []):
        kind = f.get("kind", "")
        category = "cert" if "cert" in kind else "drift"
        out.append(
            Problem(
                level=f.get("level", "warning"),
                category=category,
                vps=f.get("vps_id") or f.get("vps") or "—",
                target=f.get("domain") or kind,
                detail=f"{kind}: {f.get('detail', '')}".strip(": "),
            )
        )
    return out


def problems_from_vps(vps_nodes: list[dict[str, Any]], *, stale_minutes: int, now: datetime) -> list[Problem]:
    out: list[Problem] = []
    for v in vps_nodes:
        vid = v.get("vps_id", "?")
        age = _age_minutes(v.get("last_seen", ""), now=now)
        if age is None:
            out.append(Problem("warning", "agent", vid, vid, "agent never reported (no last_seen)"))
        elif age > stale_minutes:
            out.append(
                Problem(
                    "critical", "agent", vid, vid,
                    f"agent not reporting — last seen {int(age)} min ago (threshold {stale_minutes})",
                )
            )
    return out


def _container_problem(c: dict[str, Any], ignore: list[str]) -> Problem | None:
    name = c.get("name", "?")
    if any(pat and pat in name for pat in ignore):
        return None
    status = c.get("status", "") or ""
    vps = c.get("vps_id", "—")
    s = status.lower()
    # Down states (real crashes). Clean one-shot exits (Exited (0)) and the
    # transient "Created" state are NOT alerted — they're normal for agents.
    if s.startswith(("dead", "restarting")) or (s.startswith("exited") and "exited (0)" not in s):
        return Problem("critical", "container", vps, name, f"container down: {status}")
    if "(unhealthy)" in s:
        return Problem("warning", "container", vps, name, f"container unhealthy: {status}")
    return None


def problems_from_containers(containers: list[dict[str, Any]], *, ignore: list[str]) -> list[Problem]:
    out: list[Problem] = []
    for c in containers:
        p = _container_problem(c, ignore)
        if p is not None:
            out.append(p)
    return out


def problems_from_disk(
    prometheus_url: str,
    *,
    warn_percent: float,
    crit_percent: float,
    mounts: str,
    timeout: float = 10.0,
) -> list[Problem]:
    """Disk-usage alarms per VPS/mountpoint, queried from Prometheus.

    Reads node_filesystem_* for the watched mountpoints (all VPS, since vps-02/03
    remote-write their node-exporter metrics). Emits a warning at ``warn_percent``
    and a critical at ``crit_percent``. If Prometheus is unreachable, returns an
    empty list rather than blocking the rest of the alert run — a down Prometheus
    is itself caught by the container-down check.
    """
    expr = (
        '100 * (1 - '
        f'node_filesystem_avail_bytes{{fstype="ext4",mountpoint=~"{mounts}"}} / '
        f'node_filesystem_size_bytes{{fstype="ext4",mountpoint=~"{mounts}"}})'
    )
    try:
        resp = httpx.get(
            f"{prometheus_url.rstrip('/')}/api/v1/query",
            params={"query": expr},
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
    except (httpx.HTTPError, ValueError):
        return []

    out: list[Problem] = []
    for series in result:
        metric = series.get("metric", {})
        vps = metric.get("vps_id", "—")
        mount = metric.get("mountpoint", "?")
        try:
            pct = float(series["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if pct >= crit_percent:
            out.append(Problem(
                "critical", "disk", vps, mount,
                f"disk {mount} {pct:.0f}% full (threshold {crit_percent:.0f}%)",
            ))
        elif pct >= warn_percent:
            out.append(Problem(
                "warning", "disk", vps, mount,
                f"disk {mount} {pct:.0f}% full (threshold {warn_percent:.0f}%)",
            ))
    return out


def problems_from_blackbox(
    prometheus_url: str,
    *,
    cert_warn_days: float,
    cert_crit_days: float,
    timeout: float = 10.0,
) -> list[Problem]:
    """External synthetic-probe alarms for the public LokalFlash endpoints.

    Reads the ``blackbox`` job (blackbox_exporter, scraped by the hub Prometheus)
    which probes the K8s app + website FROM the hub — an off-cluster vantage. Two
    checks:

    * **endpoint down** — ``probe_success`` stayed 0 across a full 3-min window
      (``max_over_time(... [3m]) == 0`` debounces single flaky scrapes). Always
      critical — a public endpoint is unreachable/erroring.
    * **cert expiry** — ``probe_ssl_earliest_cert_expiry`` is close. A backstop
      for cert-manager silently failing to renew; warns at ``cert_warn_days``,
      critical at ``cert_crit_days``. In healthy operation cert-manager renews
      ~30 days out, so this never fires unless renewal actually broke.

    Prometheus unreachable → empty list (that path is itself caught by the
    container-down check), same as ``problems_from_disk``.
    """
    def _query(expr: str) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(
                f"{prometheus_url.rstrip('/')}/api/v1/query",
                params={"query": expr},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("result", [])
        except (httpx.HTTPError, ValueError):
            return []

    out: list[Problem] = []

    # (1) endpoint down — sustained for a full 3-min window (all scrapes failed).
    for series in _query('max_over_time(probe_success{job="blackbox"}[3m])'):
        try:
            up = float(series["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if up < 1:
            target = series.get("metric", {}).get("instance", "?")
            out.append(Problem(
                "critical", "endpoint", "ext", target,
                "endpoint unreachable — external probe failed for ≥3 min",
            ))

    # (2) TLS cert-expiry backstop (days until earliest cert in the chain expires).
    for series in _query('(probe_ssl_earliest_cert_expiry{job="blackbox"} - time()) / 86400'):
        try:
            days = float(series["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        target = series.get("metric", {}).get("instance", "?")
        if days < cert_crit_days:
            out.append(Problem(
                "critical", "cert", "ext", target,
                f"TLS cert expires in {days:.0f}d — cert-manager auto-renew may have stalled",
            ))
        elif days < cert_warn_days:
            out.append(Problem(
                "warning", "cert", "ext", target,
                f"TLS cert expires in {days:.0f}d",
            ))
    return out


def problems_from_k8s_backups(cfg: "AlertConfig", *, now: datetime) -> list["Problem"]:
    """Backup-freshness alarms read from the prod K8s API (read-only SA).

    Off-cluster proxy for the S3 backup destination: CNPG only sets
    ``status.lastSuccessfulBackup`` AFTER the barman upload to object storage
    completes, so it faithfully tracks "a base backup landed in S3". Also checks
    WAL continuous-archiving (PITR health), last-backup success, and the
    config-backup CronJob's last successful run.

    API unreachable -> empty list: a cluster/API outage is already caught by the
    blackbox endpoint probe, so we don't double-alarm here.
    """
    if not cfg.k8s_api or not cfg.k8s_token:
        return []
    headers = {"Authorization": f"Bearer {cfg.k8s_token}"}
    verify: Any = cfg.k8s_ca_file or True
    base = cfg.k8s_api.rstrip("/")
    ns = cfg.k8s_namespace

    def _get(path: str) -> dict[str, Any] | None:
        try:
            r = httpx.get(base + path, headers=headers, verify=verify, timeout=15.0)
            if r.status_code == 404:
                return {"__status__": 404}
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError):
            return None

    out: list[Problem] = []

    # (1) CNPG cluster: last-successful-backup age + WAL archiving + backup success
    cl = _get(f"/apis/postgresql.cnpg.io/v1/namespaces/{ns}/clusters/{cfg.k8s_pg_cluster}")
    if cl is None:
        return []  # API unreachable — blackbox covers cluster-down
    if cl.get("__status__") == 404:
        out.append(Problem("critical", "backup", "k8s", cfg.k8s_pg_cluster,
                           "CNPG cluster not found — DB backups cannot be verified"))
    else:
        st = cl.get("status", {})
        age = _age_minutes(st.get("lastSuccessfulBackup", ""), now=now)
        if age is None:
            out.append(Problem("critical", "backup", "k8s", "db-base",
                               "no successful DB backup recorded"))
        elif age / 60.0 > cfg.db_backup_max_hours:
            out.append(Problem("critical", "backup", "k8s", "db-base",
                               f"DB base backup stale — last {age / 60:.0f}h ago "
                               f"(threshold {cfg.db_backup_max_hours:.0f}h)"))
        conds = {c.get("type"): c for c in st.get("conditions", [])}
        ca = conds.get("ContinuousArchiving")
        if ca is not None and ca.get("status") == "False":
            out.append(Problem("critical", "backup", "k8s", "wal-archiving",
                               f"WAL archiving failing ({ca.get('reason', '')}) — PITR at risk"))
        lb = conds.get("LastBackupSucceeded")
        if lb is not None and lb.get("status") == "False":
            out.append(Problem("critical", "backup", "k8s", "db-base",
                               f"last DB backup did not succeed ({lb.get('reason', '')})"))

    # (2) config-backup CronJob: last-successful-run age
    if cfg.k8s_config_cronjob:
        cj = _get(f"/apis/batch/v1/namespaces/{ns}/cronjobs/{cfg.k8s_config_cronjob}")
        if cj is not None and cj.get("__status__") == 404:
            out.append(Problem("warning", "backup", "k8s", "config-backup",
                               "config-backup CronJob not found"))
        elif cj is not None:
            age = _age_minutes(cj.get("status", {}).get("lastSuccessfulTime", ""), now=now)
            if age is None:
                out.append(Problem("warning", "backup", "k8s", "config-backup",
                                   "config backup has never completed"))
            elif age / 60.0 > cfg.config_backup_max_hours:
                out.append(Problem("critical", "backup", "k8s", "config-backup",
                                   f"config backup stale — last {age / 60:.0f}h ago "
                                   f"(threshold {cfg.config_backup_max_hours:.0f}h)"))
    return out


# --- LokalFlash K8s application metrics -------------------------------------

# 🔴 SEULS LES CRONS À CADENCE FIABLE SONT SURVEILLÉS PAR L'ÂGE.
#
# Vérifié dans le code du backend le 2026-08-18 : sur les dix boucles de fond,
# SEPT testent leur porte (heure visée, interrupteur de réglage, IMAP_HOST)
# *avant* d'appeler `tryCronLock`. Elles n'horodatent donc que lorsqu'elles
# travaillent vraiment — leur âge est irrégulier PAR CONSTRUCTION, et alerter
# dessus crierait au loup en permanence (mesuré : `prospect-tunnel` avait 2
# passages là où `bounce-check`, même cadence de 15 min, en avait 29).
#
# Les trois retenues prennent leur verrou à CHAQUE tick, donc leur silence est
# un vrai signal. `recurring-push` est la plus importante des trois : c'est elle
# qui envoie les notifications de flash deals — figée, les offres des
# commerçants n'atteignent plus personne, sans aucune erreur nulle part.
# Les deux autres servent de témoins : si elles se taisent, ce sont les
# goroutines de fond qui sont mortes, pas une tâche en particulier.
_LF_CRON_MAX_AGE_S = {
    "recurring-push": 900,     # cadence 60 s
    "device-presence": 900,    # cadence 60 s
    "bounce-check": 5400,      # cadence 15 min (porte IMAP_HOST, stable si configurée)
}


def problems_from_lokalflash(prometheus_url: str, *, timeout: float = 10.0) -> list[Problem]:
    """Alarmes applicatives du cluster K8s LokalFlash (job ``lokalflash-k8s``).

    Complète ``problems_from_blackbox`` : celui-là dit « le site répond-il vu du
    dehors », celui-ci dit ce qui se passe DEDANS — erreurs serveur, latence,
    réplicas, saturation, tâches de fond figées.

    Toutes les séries décrivent le CLUSTER et non un pod : le scrape passe par
    l'ingress public et atterrit sur un pod au hasard, donc des compteurs par pod
    y seraient non monotones (cf. metrics.go côté backend).

    Prometheus injoignable → liste vide, comme les autres familles (ce chemin est
    couvert par la vérification des conteneurs).
    """
    def _q(expr: str) -> list[dict[str, Any]]:
        try:
            r = httpx.get(f"{prometheus_url.rstrip('/')}/api/v1/query",
                          params={"query": expr}, timeout=timeout)
            r.raise_for_status()
            return r.json().get("data", {}).get("result", [])
        except (httpx.HTTPError, ValueError):
            return []

    def _val(series: dict[str, Any]) -> float | None:
        try:
            return float(series["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            return None

    out: list[Problem] = []

    # (0) MÉTA-ALARME : la cible ne répond plus. Sans elle, une surveillance qui
    #     tombe est indiscernable d'un système en bonne santé — le pire des deux
    #     mondes. Fenêtre de 5 min pour absorber un déploiement (rollout ~60 s).
    seen = _q('max_over_time(up{job="lokalflash-k8s"}[5m])')
    if seen and all((_val(s) or 0) < 1 for s in seen):
        out.append(Problem(
            "warning", "lokalflash", "k8s", "metrics",
            "métriques applicatives injoignables depuis ≥5 min — surveillance aveugle",
        ))
        return out  # inutile d'évaluer le reste : les séries sont périmées.

    # (1) ERREURS SERVEUR — le seul symptôme que l'utilisateur voit vraiment.
    #     Seuil ABSOLU et non un taux : à faible trafic, 1 erreur sur 2 requêtes
    #     ferait 50 % et réveillerait pour rien. 10 erreurs en 5 min, c'est ~2 par
    #     minute soutenues — un vrai incident à ce niveau de trafic.
    for s_ in _q('sum(sum_over_time(lf_requests_last_minute{class="server_error"}[5m]))'):
        n = _val(s_)
        if n is not None and n >= 10:
            out.append(Problem(
                "critical", "lokalflash", "k8s", "api-5xx",
                f"{int(n)} erreurs serveur en 5 min — des utilisateurs reçoivent des échecs",
            ))

    # (2) LATENCE — palier d'histogramme, pas un centile exact. 2500 signifie que
    #     5 % des requêtes dépassent 1 s ; 10000 est la valeur convenue pour
    #     « au-delà du dernier palier », soit plus de 5 s.
    for s_ in _q('min_over_time(lf_request_duration_ms_p95[5m])'):
        ms = _val(s_)
        if ms is None:
            continue
        if ms >= 10000:
            out.append(Problem("critical", "lokalflash", "k8s", "api-latence",
                               "p95 au-delà de 5 s pendant ≥5 min"))
        elif ms >= 2500:
            out.append(Problem("warning", "lokalflash", "k8s", "api-latence",
                               f"p95 ≥ {int(ms)} ms pendant ≥5 min"))

    # (3) RÉSILIENCE PERDUE — un seul réplica, plus de tolérance de panne. `max`
    #     sur 5 min : pendant un déploiement le compte oscille, seul un creux
    #     DURABLE compte.
    for s_ in _q('max_over_time(lf_cluster_pods[5m])'):
        n = _val(s_)
        if n is not None and n < 2:
            out.append(Problem(
                "critical", "lokalflash", "k8s", "api-replicas",
                f"{int(n)} réplica d'API — plus aucune tolérance de panne",
            ))

    # (4) SATURATION — l'autoscaler est à son plafond. Mesuré le 2026-08-18 : le
    #     plafond réel est le CPU des NŒUDS (~700 req/s), pas les pods, qui
    #     étaient à 13 % de leur limite. Cette alarme dit donc « ajoutez un
    #     nœud », pas « augmentez maxReplicas ». `min` sur 10 min pour ne pas
    #     réveiller sur une pointe de trafic passagère.
    for s_ in _q('min_over_time(lf_cluster_pods[10m])'):
        n = _val(s_)
        if n is not None and n >= 12:
            out.append(Problem(
                "warning", "lokalflash", "k8s", "api-saturation",
                "autoscaler au plafond (12 réplicas) depuis ≥10 min — "
                "le goulot est le CPU des nœuds, pas le nombre de pods",
            ))

    # (5) TÂCHES DE FOND FIGÉES — ce que la console ne sait pas faire.
    for s_ in _q("lf_cron_last_run_age_seconds"):
        name = s_.get("metric", {}).get("name", "")
        limit = _LF_CRON_MAX_AGE_S.get(name)
        if limit is None:
            continue  # cadence non fiable : volontairement non surveillé (voir en-tête)
        age = _val(s_)
        if age is not None and age > limit:
            out.append(Problem(
                "critical", "lokalflash", "k8s", f"cron:{name}",
                f"aucun passage depuis {int(age // 60)} min "
                f"(cadence attendue ≤ {limit // 60} min)",
            ))

    return out




# --- LokalFlash K8s cluster infrastructure ----------------------------------

# 🔴 CE FILTRE N'EST PAS DÉCORATIF. Le hub fait tourner SON PROPRE node-exporter
# et SON PROPRE cAdvisor, qui produisent EXACTEMENT les mêmes noms de métriques
# (`node_cpu_seconds_total`, `container_memory_working_set_bytes`…). Sans ce
# sélecteur, chaque règle ci-dessous alerterait aussi sur flowbiz-1 lui-même, et
# on ne saurait pas lequel des deux parle. Vérifié le 2026-08-18 : 32 séries CPU
# sans étiquette `cluster` (le hub) contre 64 avec (nos deux nœuds).
_K8S = 'cluster="pck-vpe3ary"'

# 🔴 Expressions construites par CONCATÉNATION et non par f-string. PromQL est
# fait d'accolades ; en f-string il faut toutes les doubler et échapper les
# guillemets, ce qui rend les requêtes illisibles et casse au moindre oubli
# (trois erreurs de suite en les écrivant ainsi le 2026-08-18). Ici, ce qu'on lit
# est exactement ce qui part à Prometheus.
_RESTARTS = 'sum by (pod) (increase(kube_pod_container_status_restarts_total{' + _K8S + '}[30m])) >= 3'
_WORKLOAD = ('min_over_time((kube_deployment_status_replicas_ready{' + _K8S + '}'
             ' - kube_deployment_spec_replicas{' + _K8S + '})[10m:1m]) < 0')
_PENDING = ('min_over_time((kube_pod_status_phase{' + _K8S + ',phase="Pending"} == 1)'
            '[15m:1m]) == 1')
_NODE_CPU = ('avg_over_time((100 - (avg by (instance) (rate(node_cpu_seconds_total{'
             + _K8S + ',mode="idle"}[5m])) * 100))[15m:1m]) > 90')
_NODE_MEM = ('avg_over_time(((1 - (node_memory_MemAvailable_bytes{' + _K8S + '}'
             ' / node_memory_MemTotal_bytes{' + _K8S + '})) * 100)[10m:1m]) > 88')
_NODE_DISK = ('(1 - (node_filesystem_avail_bytes{' + _K8S + ',mountpoint="/"}'
              ' / node_filesystem_size_bytes{' + _K8S + ',mountpoint="/"})) * 100 > 88')
# 🔴 UN VOLUME PERSISTANT PLEIN EST PLUS GRAVE QU'UN DISQUE DE NŒUD PLEIN, ici.
# Le disque d'un nœud se libère (journaux, images) ; un volume plein, lui, fait
# ÉCHOUER DES ÉCRITURES MÉTIER — une photo de commerce qu'on ne peut plus
# enregistrer, une page du site qu'on ne peut plus publier, une transaction
# Postgres qui s'arrête. Seuil plus bas (80 %) parce qu'un volume ne se vide pas
# tout seul : il faut agrandir ou nettoyer, et ça prend du temps qu'il vaut mieux
# avoir devant soi. `registry-data` est le cas le plus vicieux : saturé, il
# bloque TOUT déploiement en corrompant l'image au push.
_PVC_FULL = ('(kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes)'
             ' * 100 > 80')


def problems_from_k8s_cluster(prometheus_url: str, *, timeout: float = 10.0) -> list[Problem]:
    """Alarmes d'INFRASTRUCTURE du cluster K8s (kube-state-metrics, node-exporter).

    Complète les deux familles voisines : ``problems_from_blackbox`` dit « le site
    répond-il vu du dehors », ``problems_from_lokalflash`` dit « l'application
    va-t-elle bien », celle-ci dit « la machine en dessous tient-elle debout ».

    Les données arrivent par ``remote_write`` depuis un agent qui tourne DANS le
    cluster (deploy/monitoring/) — le hub ne scrape rien à l'intérieur.

    Prometheus injoignable → liste vide, comme les autres familles.
    """
    def _q(expr):
        try:
            r = httpx.get(prometheus_url.rstrip("/") + "/api/v1/query",
                          params={"query": expr}, timeout=timeout)
            r.raise_for_status()
            return r.json().get("data", {}).get("result", [])
        except (httpx.HTTPError, ValueError):
            return []

    def _val(item):
        try:
            return float(item["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            return None

    out = []

    # (0) MÉTA-ALARME. Si l'agent du cluster cesse de pousser, toutes les règles
    #     ci-dessous deviennent muettes — et le silence ressemblerait à la santé.
    #     `absent()` ne rend une série que lorsqu'il n'y a AUCUN échantillon.
    if _q("absent(kube_pod_info{" + _K8S + "})"):
        out.append(Problem(
            "warning", "k8s", "cluster", "inventaire",
            "aucune métrique d'infrastructure reçue — l'agent du cluster ne pousse plus, "
            "la surveillance de la machine est aveugle",
        ))
        return out  # les autres règles porteraient sur des données périmées.

    # (1) REDÉMARRAGES EN BOUCLE — le défaut qui ne lève aucune erreur ailleurs.
    #     Seuil à 3 en 30 min : un redémarrage isolé est normal (déploiement,
    #     dépassement mémoire ponctuel), trois est une boucle.
    for item in _q(_RESTARTS):
        pod = item.get("metric", {}).get("pod", "?")
        out.append(Problem("critical", "k8s", "cluster", "pod:" + pod,
                           "%d redémarrages en 30 min — le conteneur boucle" % int(_val(item) or 0)))

    # (2) CHARGE DE TRAVAIL INCOMPLÈTE. `min_over_time` sur 10 min : pendant un
    #     déploiement l'écart est normal et transitoire ; seul un écart PERMANENT
    #     signale des réplicas qui ne démarrent pas.
    for item in _q(_WORKLOAD):
        m = item.get("metric", {})
        out.append(Problem("critical", "k8s", "cluster",
                           "%s/%s" % (m.get("namespace", "?"), m.get("deployment", "?")),
                           "réplicas manquants depuis ≥10 min (écart %d)" % int(_val(item) or 0)))

    # (3) POD QUI NE DÉMARRE PAS. `Pending` durable = rien ne peut le placer :
    #     plus de ressources sur les nœuds, ou un volume qui ne s'attache pas.
    for item in _q(_PENDING):
        pod = item.get("metric", {}).get("pod", "?")
        out.append(Problem("warning", "k8s", "cluster", "pod:" + pod,
                           "en attente de placement depuis ≥15 min — ressources ou volume"))

    # (4) CPU DES NŒUDS — LE GOULOT MESURÉ. Moyenne sur 15 min et non valeur
    #     instantanée : une pointe à 99 % pendant une montée en charge de
    #     l'autoscaler est normale, quinze minutes au-dessus de 90 % ne l'est pas.
    #     🔴 Le message dit « ajoutez un nœud » et non « augmentez maxReplicas » :
    #     à saturation (~700 req/s mesurés) les pods api étaient à 13 % de leur
    #     limite — ajouter des réplicas ne déplacerait pas ce plafond.
    for item in _q(_NODE_CPU):
        node = item.get("metric", {}).get("instance", "?")
        out.append(Problem("warning", "k8s", "cluster", "noeud-cpu:" + node,
                           "CPU à %d %% en moyenne sur 15 min — le plafond de capacité est le "
                           "CPU des nœuds, pas le nombre de pods (ajouter un nœud, pas des "
                           "réplicas)" % int(_val(item) or 0)))

    # (5) MÉMOIRE DES NŒUDS. Plus grave que le CPU : une saturation mémoire fait
    #     ÉVINCER des pods par le kubelet, alors qu'un CPU saturé ne fait que
    #     ralentir. D'où le niveau critique et un seuil un peu plus bas.
    for item in _q(_NODE_MEM):
        node = item.get("metric", {}).get("instance", "?")
        out.append(Problem("critical", "k8s", "cluster", "noeud-memoire:" + node,
                           "mémoire à %d %% — risque d'éviction de pods" % int(_val(item) or 0)))

    # (6) DISQUE DES NŒUDS. Un disque plein empêche d'écrire les journaux, de
    #     tirer une image, et fait passer le nœud en pression disque.
    for item in _q(_NODE_DISK):
        node = item.get("metric", {}).get("instance", "?")
        pct = _val(item) or 0
        out.append(Problem("critical" if pct > 94 else "warning", "k8s", "cluster",
                           "noeud-disque:" + node, "disque à %d %%" % int(pct)))

    # (7) VOLUMES PERSISTANTS. Ce sont les disques qui portent le métier :
    #     photos de commerces (MinIO), contenu du site (CMS), données Postgres,
    #     images du registre. Leur occupation ne vient QUE du kubelet.
    for item in _q(_PVC_FULL):
        m = item.get("metric", {})
        pvc = m.get("persistentvolumeclaim", "?")
        pct = _val(item) or 0
        out.append(Problem("critical" if pct > 90 else "warning", "k8s", "cluster",
                           "volume:" + pvc,
                           "volume occupé à %d %% — un volume plein fait échouer des "
                           "écritures, et ne se vide pas tout seul" % int(pct)))

    return out




def collect_problems(cfg: AlertConfig, *, now: datetime | None = None) -> list[Problem]:
    """Query the hub and return all problems at/above ``cfg.min_level``."""
    now = now or datetime.now(timezone.utc)
    problems: list[Problem] = []
    problems += problems_from_drift(hub_client.fleet_drift())
    problems += problems_from_vps(
        hub_client.list_vps(), stale_minutes=cfg.agent_stale_minutes, now=now
    )
    problems += problems_from_containers(
        hub_client.list_containers(), ignore=cfg.ignore_containers
    )
    problems += problems_from_disk(
        cfg.prometheus_url,
        warn_percent=cfg.disk_warn_percent,
        crit_percent=cfg.disk_crit_percent,
        mounts=cfg.disk_mounts,
    )
    problems += problems_from_blackbox(
        cfg.prometheus_url,
        cert_warn_days=cfg.cert_warn_days,
        cert_crit_days=cfg.cert_crit_days,
    )
    problems += problems_from_k8s_backups(cfg, now=now)
    problems += problems_from_lokalflash(cfg.prometheus_url)
    problems += problems_from_k8s_cluster(cfg.prometheus_url)
    threshold = _LEVELS.get(cfg.min_level, 1)
    kept = [p for p in problems if _LEVELS.get(p.level, 0) >= threshold]
    kept.sort(key=lambda p: (-_LEVELS.get(p.level, 0), p.category, p.target))
    return kept


# ---------------------------------------------------------------------------
# State (dedup so we only email on change)
# ---------------------------------------------------------------------------


def load_state(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text())
        return set(data.get("active", []))
    except (OSError, ValueError):
        return set()


def save_state(path: Path, problems: list[Problem], *, now: datetime) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"updated": now.isoformat(), "active": sorted(p.key for p in problems)},
                indent=2,
            )
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Email rendering + send
# ---------------------------------------------------------------------------


def _by_level(problems: list[Problem]) -> dict[str, list[Problem]]:
    grouped: dict[str, list[Problem]] = {"critical": [], "warning": [], "info": []}
    for p in problems:
        grouped.setdefault(p.level, []).append(p)
    return grouped


def render_subject(cfg: AlertConfig, current: list[Problem], resolved: bool) -> str:
    if resolved:
        return f"{cfg.subject_prefix} ✅ Tout est revenu à la normale"
    g = _by_level(current)
    nc, nw = len(g["critical"]), len(g["warning"])
    bits = []
    if nc:
        bits.append(f"{nc} critique{'s' if nc > 1 else ''}")
    if nw:
        bits.append(f"{nw} warning{'s' if nw > 1 else ''}")
    summary = ", ".join(bits) or f"{len(current)} problème(s)"
    return f"{cfg.subject_prefix} 🔴 {summary}"


def render_bodies(
    cfg: AlertConfig,
    current: list[Problem],
    new: list[Problem],
    resolved_keys: set[str],
    *,
    now: datetime,
) -> tuple[str, str]:
    """Return (plain_text, html)."""
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    new_keys = {p.key for p in new}

    # --- plain text ---
    lines = [f"VSA fleet alert — {ts}", ""]
    if not current:
        lines.append("✅ Tous les problèmes précédents sont résolus.")
    else:
        lines.append(f"{len(current)} problème(s) actif(s) (seuil: {cfg.min_level}):")
        lines.append("")
        for p in current:
            tag = "  >>> NOUVEAU " if p.key in new_keys else "      "
            lines.append(f"{tag}{p.line()}")
    if resolved_keys:
        lines += ["", f"Résolus depuis la dernière alerte: {len(resolved_keys)}"]
        for k in sorted(resolved_keys):
            lines.append(f"      ✅ {k}")
    lines += ["", "—", "dashboard.flowbiz.ai/health  ·  alerte automatique VSA"]
    text = "\n".join(lines)

    # --- html ---
    def row(p: Problem) -> str:
        color = {"critical": "#c0392b", "warning": "#d68910", "info": "#2471a3"}.get(p.level, "#555")
        badge = "NOUVEAU" if p.key in new_keys else ""
        badge_html = f'<span style="background:#c0392b;color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;margin-left:6px">{badge}</span>' if badge else ""
        return (
            f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">'
            f'<b style="color:{color}">{p.level.upper()}</b>{badge_html}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{p.category}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee"><code>{p.target}</code></td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{p.vps}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{p.detail}</td></tr>'
        )

    if current:
        table = (
            '<table style="border-collapse:collapse;font-family:system-ui,sans-serif;font-size:14px;width:100%">'
            '<tr style="text-align:left;color:#888;font-size:12px">'
            "<th style='padding:6px 10px'>Niveau</th><th style='padding:6px 10px'>Type</th>"
            "<th style='padding:6px 10px'>Cible</th><th style='padding:6px 10px'>VPS</th>"
            "<th style='padding:6px 10px'>Détail</th></tr>"
            + "".join(row(p) for p in current)
            + "</table>"
        )
        header = f"<h2 style='font-family:system-ui,sans-serif'>🔴 {len(current)} problème(s) actif(s)</h2>"
    else:
        table = ""
        header = "<h2 style='font-family:system-ui,sans-serif;color:#27ae60'>✅ Tout est revenu à la normale</h2>"

    resolved_html = ""
    if resolved_keys:
        items = "".join(f"<li><code>{k}</code></li>" for k in sorted(resolved_keys))
        resolved_html = f"<p style='color:#27ae60'>Résolus: {len(resolved_keys)}</p><ul>{items}</ul>"

    html = (
        f"<div style='font-family:system-ui,sans-serif;color:#222'>{header}"
        f"<p style='color:#888;font-size:12px'>{ts} · seuil <b>{cfg.min_level}</b></p>"
        f"{table}{resolved_html}"
        "<p style='color:#888;font-size:12px;margin-top:18px'>"
        "<a href='https://dashboard.flowbiz.ai/health'>dashboard.flowbiz.ai/health</a> · alerte automatique VSA</p></div>"
    )
    return text, html


def send_email(cfg: AlertConfig, subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = ", ".join(cfg.recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(cfg.smtp_user, cfg.smtp_password)
        server.send_message(msg)
