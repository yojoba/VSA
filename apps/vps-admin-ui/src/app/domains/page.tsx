"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";

export default function DomainsPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["domains"],
    queryFn: api.getDomains,
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Domains</h1>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">Domain</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Container</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Port</th>
              <th className="text-left p-3 text-zinc-400 font-medium">VPS</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.map((d) => (
              <tr key={d.id} className="hover:bg-zinc-800/30">
                <td className="p-3 text-white font-mono">{d.domain}</td>
                <td className="p-3 text-zinc-400 font-mono">{d.container || "-"}</td>
                <td className="p-3 text-zinc-400">{d.port}</td>
                <td className="p-3 text-zinc-400">{d.vps_id}</td>
                <td className="p-3">
                  <StatusBadge status={d.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
        {isError && <p className="text-red-400 p-4">Failed to load domains.</p>}
        {data?.length === 0 && <p className="text-zinc-500 p-4">No domains registered yet.</p>}
      </div>
    </div>
  );
}
