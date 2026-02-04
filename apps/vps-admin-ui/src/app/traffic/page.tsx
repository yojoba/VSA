"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, TrafficStat, TrafficLogEntry } from "@/lib/api";
import { clsx } from "clsx";

const TIME_RANGES = ["1h", "6h", "24h", "7d"] as const;

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

function StatusCodeBadge({ status }: { status: number }) {
  const color =
    status < 300
      ? "bg-green-500/20 text-green-400"
      : status < 400
        ? "bg-blue-500/20 text-blue-400"
        : status < 500
          ? "bg-yellow-500/20 text-yellow-400"
          : "bg-red-500/20 text-red-400";
  return (
    <span className={clsx("px-2 py-0.5 rounded-full text-xs font-medium", color)}>
      {status}
    </span>
  );
}

function MethodBadge({ method }: { method: string }) {
  const color =
    method === "GET"
      ? "text-green-400"
      : method === "POST"
        ? "text-blue-400"
        : method === "PUT"
          ? "text-yellow-400"
          : method === "DELETE"
            ? "text-red-400"
            : "text-zinc-400";
  return <span className={clsx("font-mono text-xs font-medium", color)}>{method}</span>;
}

function StatsCards({ stats }: { stats: TrafficStat[] }) {
  const totals = stats.reduce(
    (acc, s) => ({
      requests: acc.requests + s.requests,
      status_2xx: acc.status_2xx + s.status_2xx,
      status_3xx: acc.status_3xx + s.status_3xx,
      status_4xx: acc.status_4xx + s.status_4xx,
      status_5xx: acc.status_5xx + s.status_5xx,
      bytes_sent: acc.bytes_sent + s.bytes_sent,
      avg_ms:
        acc.requests + s.requests > 0
          ? Math.round(
              (acc.avg_ms * acc.requests + s.avg_request_time_ms * s.requests) /
                (acc.requests + s.requests)
            )
          : 0,
    }),
    { requests: 0, status_2xx: 0, status_3xx: 0, status_4xx: 0, status_5xx: 0, bytes_sent: 0, avg_ms: 0 }
  );

  const cards = [
    { label: "Total Requests", value: totals.requests.toLocaleString(), color: "text-white" },
    { label: "2xx Success", value: totals.status_2xx.toLocaleString(), color: "text-green-400" },
    { label: "3xx Redirect", value: totals.status_3xx.toLocaleString(), color: "text-blue-400" },
    { label: "4xx Client Error", value: totals.status_4xx.toLocaleString(), color: "text-yellow-400" },
    { label: "5xx Server Error", value: totals.status_5xx.toLocaleString(), color: "text-red-400" },
    { label: "Bandwidth", value: formatBytes(totals.bytes_sent), color: "text-white" },
    { label: "Avg Response", value: `${totals.avg_ms}ms`, color: "text-white" },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
          <div className="text-xs text-zinc-500 mb-1">{c.label}</div>
          <div className={clsx("text-xl font-bold", c.color)}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}

export default function TrafficPage() {
  const [period, setPeriod] = useState<string>("24h");
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [logDomain, setLogDomain] = useState<string>("");

  const statsQuery = useQuery({
    queryKey: ["traffic-stats", period, selectedDomain],
    queryFn: () => {
      const params = new URLSearchParams({ period });
      if (selectedDomain) params.set("domain", selectedDomain);
      return api.getTrafficStats(params.toString());
    },
    refetchInterval: 30000,
  });

  const domainsQuery = useQuery({
    queryKey: ["domains"],
    queryFn: api.getDomains,
  });

  const logsQuery = useQuery({
    queryKey: ["traffic-logs", logDomain, period],
    queryFn: () =>
      api.getTrafficLogs(
        new URLSearchParams({ domain: logDomain, limit: "100", since: period }).toString()
      ),
    enabled: !!logDomain,
    refetchInterval: 10000,
  });

  const stats = statsQuery.data || [];
  const domains = domainsQuery.data || [];
  const logs = logsQuery.data || [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Traffic</h1>
        <div className="flex items-center gap-3">
          <select
            value={selectedDomain}
            onChange={(e) => {
              setSelectedDomain(e.target.value);
              if (e.target.value && !logDomain) setLogDomain(e.target.value);
            }}
            className="bg-zinc-800 text-zinc-300 text-sm rounded-lg px-3 py-1.5 border border-zinc-700"
          >
            <option value="">All domains</option>
            {domains.map((d) => (
              <option key={d.domain} value={d.domain}>
                {d.domain}
              </option>
            ))}
          </select>
          <div className="flex bg-zinc-800 rounded-lg border border-zinc-700">
            {TIME_RANGES.map((t) => (
              <button
                key={t}
                onClick={() => setPeriod(t)}
                className={clsx(
                  "px-3 py-1.5 text-sm transition-colors",
                  period === t
                    ? "bg-zinc-700 text-white rounded-lg"
                    : "text-zinc-400 hover:text-white"
                )}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      </div>

      {statsQuery.isLoading && <p className="text-zinc-500">Loading stats...</p>}
      {statsQuery.isError && <p className="text-red-400">Failed to load traffic stats.</p>}

      {stats.length > 0 && <StatsCards stats={stats} />}

      {/* Per-domain stats table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <div className="p-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-400">Per-Domain Breakdown</h2>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">Domain</th>
              <th className="text-right p-3 text-zinc-400 font-medium">Requests</th>
              <th className="text-right p-3 text-zinc-400 font-medium">2xx</th>
              <th className="text-right p-3 text-zinc-400 font-medium">3xx</th>
              <th className="text-right p-3 text-zinc-400 font-medium">4xx</th>
              <th className="text-right p-3 text-zinc-400 font-medium">5xx</th>
              <th className="text-right p-3 text-zinc-400 font-medium">Bandwidth</th>
              <th className="text-right p-3 text-zinc-400 font-medium">Avg Response</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {stats.map((s) => (
              <tr
                key={s.domain}
                className={clsx(
                  "hover:bg-zinc-800/30 cursor-pointer",
                  logDomain === s.domain && "bg-zinc-800/50"
                )}
                onClick={() => setLogDomain(s.domain)}
              >
                <td className="p-3 text-white font-mono">{s.domain}</td>
                <td className="p-3 text-right text-zinc-300">{s.requests.toLocaleString()}</td>
                <td className="p-3 text-right text-green-400">{s.status_2xx.toLocaleString()}</td>
                <td className="p-3 text-right text-blue-400">{s.status_3xx.toLocaleString()}</td>
                <td className="p-3 text-right text-yellow-400">{s.status_4xx.toLocaleString()}</td>
                <td className="p-3 text-right text-red-400">{s.status_5xx.toLocaleString()}</td>
                <td className="p-3 text-right text-zinc-300">{formatBytes(s.bytes_sent)}</td>
                <td className="p-3 text-right text-zinc-300">{s.avg_request_time_ms}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
        {stats.length === 0 && !statsQuery.isLoading && (
          <p className="text-zinc-500 p-4">No traffic data for this period.</p>
        )}
      </div>

      {/* Raw logs table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <div className="p-3 border-b border-zinc-800 flex items-center justify-between">
          <h2 className="text-sm font-medium text-zinc-400">
            Raw Logs{logDomain ? ` — ${logDomain}` : ""}
          </h2>
          {!logDomain && (
            <p className="text-xs text-zinc-500">Click a domain above to view logs</p>
          )}
          {logDomain && logsQuery.isFetching && (
            <span className="text-xs text-zinc-500">Refreshing...</span>
          )}
        </div>
        {logDomain && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-zinc-800/50">
                <tr>
                  <th className="text-left p-2 text-zinc-400 font-medium whitespace-nowrap">Time</th>
                  <th className="text-left p-2 text-zinc-400 font-medium">IP</th>
                  <th className="text-left p-2 text-zinc-400 font-medium">Method</th>
                  <th className="text-left p-2 text-zinc-400 font-medium">Path</th>
                  <th className="text-left p-2 text-zinc-400 font-medium">Status</th>
                  <th className="text-right p-2 text-zinc-400 font-medium whitespace-nowrap">Time (s)</th>
                  <th className="text-right p-2 text-zinc-400 font-medium">Bytes</th>
                  <th className="text-left p-2 text-zinc-400 font-medium">User Agent</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800">
                {logs.map((log, i) => (
                  <tr key={i} className="hover:bg-zinc-800/30">
                    <td className="p-2 text-zinc-400 font-mono whitespace-nowrap">
                      {log.time ? new Date(log.time).toLocaleTimeString() : "-"}
                    </td>
                    <td className="p-2 text-zinc-400 font-mono">{log.remote_addr}</td>
                    <td className="p-2">
                      <MethodBadge method={log.method} />
                    </td>
                    <td className="p-2 text-zinc-300 font-mono max-w-xs truncate" title={log.uri}>
                      {log.uri}
                    </td>
                    <td className="p-2">
                      <StatusCodeBadge status={log.status} />
                    </td>
                    <td className="p-2 text-right text-zinc-400 font-mono">{log.request_time}</td>
                    <td className="p-2 text-right text-zinc-400">{formatBytes(log.body_bytes_sent)}</td>
                    <td className="p-2 text-zinc-500 max-w-xs truncate" title={log.http_user_agent}>
                      {log.http_user_agent}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {logs.length === 0 && !logsQuery.isLoading && (
              <p className="text-zinc-500 p-4">No log entries found.</p>
            )}
            {logsQuery.isLoading && <p className="text-zinc-500 p-4">Loading logs...</p>}
            {logsQuery.isError && (
              <p className="text-red-400 p-4">Failed to load logs. Loki may be unavailable.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
