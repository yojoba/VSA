const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export interface Container {
  vps_id: string;
  name: string;
  image: string;
  status: string;
  ports: string;
  compose_project: string;
  snapshot_at: string | null;
}

export interface Domain {
  id: number;
  domain: string;
  vps_id: string;
  container: string;
  port: number;
  status: string;
  created_at: string | null;
}

export interface Certificate {
  vps_id: string;
  domain: string;
  issuer: string;
  expiry: string | null;
  days_remaining: number | null;
  status: string;
}

export interface AuditLogEntry {
  id: number;
  timestamp: string | null;
  vps_id: string;
  actor: string;
  action: string;
  target: string;
  params: Record<string, unknown>;
  result: string;
  error: string | null;
  duration_ms: number | null;
}

export interface PaginatedAuditLogs {
  total: number;
  page: number;
  per_page: number;
  items: AuditLogEntry[];
}

export interface VpsNode {
  id: number;
  vps_id: string;
  hostname: string;
  ip_address: string;
  status: string;
  last_seen: string | null;
}

export interface Stack {
  vps_id: string;
  name: string;
  containers: {
    name: string;
    service: string;
    status: string;
    image: string;
  }[];
}

export interface TrafficStat {
  domain: string;
  requests: number;
  status_2xx: number;
  status_3xx: number;
  status_4xx: number;
  status_5xx: number;
  bytes_sent: number;
  avg_request_time_ms: number;
  period_start: string | null;
  period_end: string | null;
}

export interface FleetFinding {
  level: "critical" | "warning" | "info";
  kind: string;
  domain: string | null;
  vps_id: string | null;
  message: string;
}

export interface FleetDriftReport {
  summary: { critical: number; warning: number; info: number; total: number };
  findings: FleetFinding[];
}

export interface DomainAssignment {
  id: number;
  domain: string;
  primary_vps_id: string;
  standby_vps_ids: string[];
  notes: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface TrafficLogEntry {
  time: string;
  domain: string;
  remote_addr: string;
  method: string;
  uri: string;
  status: number;
  body_bytes_sent: number;
  request_time: number;
  upstream_response_time: string;
  http_user_agent: string;
  http_referer: string;
  http_x_forwarded_for: string;
  server_protocol: string;
}

export const api = {
  getContainers: () => fetchApi<Container[]>("/containers"),
  getDomains: () => fetchApi<Domain[]>("/domains"),
  getCerts: () => fetchApi<Certificate[]>("/certs"),
  getAuditLogs: (params?: string) =>
    fetchApi<PaginatedAuditLogs>(`/audit-logs${params ? `?${params}` : ""}`),
  getStacks: () => fetchApi<Stack[]>("/stacks"),
  getVpsNodes: () => fetchApi<VpsNode[]>("/vps"),
  getTrafficStats: (params?: string) =>
    fetchApi<TrafficStat[]>(`/traffic/stats${params ? `?${params}` : ""}`),
  getTrafficLogs: (params: string) =>
    fetchApi<TrafficLogEntry[]>(`/traffic/logs?${params}`),
  getAssignments: () => fetchApi<DomainAssignment[]>("/assignments"),
  getFleetDrift: () => fetchApi<FleetDriftReport>("/fleet/drift"),
};
