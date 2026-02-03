"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";

export default function AuditPage() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);

  const params = new URLSearchParams();
  if (actor) params.set("actor", actor);
  if (action) params.set("action", action);
  params.set("page", String(page));
  params.set("per_page", "50");

  const { data, isLoading } = useQuery({
    queryKey: ["audit-logs", actor, action, page],
    queryFn: () => api.getAuditLogs(params.toString()),
  });

  const exportUrl = `/api/audit-logs/export?format=csv${actor ? `&actor=${actor}` : ""}${action ? `&action=${action}` : ""}`;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Audit Logs</h1>
        <a
          href={exportUrl}
          className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm transition-colors"
        >
          Export CSV
        </a>
      </div>

      <div className="flex gap-3 mb-4">
        <input
          type="text"
          placeholder="Filter by actor..."
          value={actor}
          onChange={(e) => { setActor(e.target.value); setPage(1); }}
          className="px-3 py-1.5 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500"
        />
        <input
          type="text"
          placeholder="Filter by action..."
          value={action}
          onChange={(e) => { setAction(e.target.value); setPage(1); }}
          className="px-3 py-1.5 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500"
        />
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">Timestamp</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Actor</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Action</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Target</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Result</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Duration</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.items.map((log) => (
              <tr key={log.id} className="hover:bg-zinc-800/30">
                <td className="p-3 text-zinc-400 text-xs font-mono">
                  {log.timestamp ? new Date(log.timestamp).toLocaleString() : "-"}
                </td>
                <td className="p-3 text-white">{log.actor}</td>
                <td className="p-3 text-zinc-300 font-mono text-xs">{log.action}</td>
                <td className="p-3 text-zinc-400 font-mono text-xs">{log.target}</td>
                <td className="p-3">
                  <StatusBadge status={log.result} />
                </td>
                <td className="p-3 text-zinc-500 text-xs">
                  {log.duration_ms ? `${log.duration_ms}ms` : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
      </div>

      {data && data.total > data.per_page && (
        <div className="flex justify-center gap-2 mt-4">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1 bg-zinc-800 rounded text-sm text-zinc-400 disabled:opacity-50"
          >
            Previous
          </button>
          <span className="text-zinc-500 text-sm py-1">
            Page {page} of {Math.ceil(data.total / data.per_page)}
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={page >= Math.ceil(data.total / data.per_page)}
            className="px-3 py-1 bg-zinc-800 rounded text-sm text-zinc-400 disabled:opacity-50"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
