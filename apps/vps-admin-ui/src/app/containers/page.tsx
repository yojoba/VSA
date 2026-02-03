"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";

export default function ContainersPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["containers"],
    queryFn: api.getContainers,
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Containers</h1>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">Name</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Image</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Status</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Project</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.map((c) => (
              <tr key={c.name} className="hover:bg-zinc-800/30">
                <td className="p-3 text-white font-mono">{c.name}</td>
                <td className="p-3 text-zinc-400 font-mono text-xs">{c.image}</td>
                <td className="p-3">
                  <StatusBadge status={c.status} />
                </td>
                <td className="p-3 text-zinc-400">
                  {c.labels["com.docker.compose.project"] || "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
        {data?.length === 0 && <p className="text-zinc-500 p-4">No containers found.</p>}
      </div>
    </div>
  );
}
