"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";

export default function VpsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["vps"],
    queryFn: api.getVpsNodes,
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">VPS Nodes</h1>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">VPS ID</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Hostname</th>
              <th className="text-left p-3 text-zinc-400 font-medium">IP Address</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Status</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Last Seen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.map((n) => (
              <tr key={n.id} className="hover:bg-zinc-800/30">
                <td className="p-3 text-white font-mono">{n.vps_id}</td>
                <td className="p-3 text-zinc-400">{n.hostname || "-"}</td>
                <td className="p-3 text-zinc-400 font-mono">{n.ip_address || "-"}</td>
                <td className="p-3">
                  <StatusBadge status={n.status} />
                </td>
                <td className="p-3 text-zinc-400 text-xs">
                  {n.last_seen ? new Date(n.last_seen).toLocaleString() : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
        {data?.length === 0 && (
          <p className="text-zinc-500 p-4">
            No VPS nodes registered. Use <code className="text-zinc-300">vsa agent register</code> to add nodes.
          </p>
        )}
      </div>
    </div>
  );
}
