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
  name: string;
  image: string;
  status: string;
  ports: Record<string, unknown>;
  labels: Record<string, string>;
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
};
