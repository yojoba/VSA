"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export default function AssignmentsPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["assignments"],
    queryFn: api.getAssignments,
    refetchInterval: 60000,
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">Domain Assignments</h1>
      <p className="text-zinc-400 text-sm mb-6">
        Intended primary + warm-standby VPS for each domain. Edited via{" "}
        <code className="text-zinc-300 bg-zinc-800 px-1.5 py-0.5 rounded">
          vsa fleet assign
        </code>{" "}
        on the hub. Distinct from agent-observed state shown on Domains and
        Certificates pages.
      </p>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">Domain</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Primary</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Standbys</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Notes</th>
              <th className="text-right p-3 text-zinc-400 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.map((a) => (
              <tr key={a.id} className="hover:bg-zinc-800/30">
                <td className="p-3 text-white font-mono">{a.domain}</td>
                <td className="p-3">
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-500/20 text-green-400 font-mono">
                    {a.primary_vps_id}
                  </span>
                </td>
                <td className="p-3">
                  {a.standby_vps_ids.length === 0 ? (
                    <span className="text-zinc-500 text-xs">none</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {a.standby_vps_ids.map((s) => (
                        <span
                          key={s}
                          className="px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400 font-mono"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="p-3 text-zinc-400 text-xs max-w-md truncate" title={a.notes}>
                  {a.notes || "-"}
                </td>
                <td className="p-3 text-zinc-500 text-xs text-right">
                  {a.updated_at ? new Date(a.updated_at).toLocaleString() : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
        {isError && <p className="text-red-400 p-4">Failed to load assignments.</p>}
        {data?.length === 0 && (
          <p className="text-zinc-500 p-4">
            No assignments yet. On the hub, run{" "}
            <code className="text-zinc-300 bg-zinc-800 px-1.5 py-0.5 rounded">
              vsa fleet backfill
            </code>{" "}
            to create defaults from observed state, or{" "}
            <code className="text-zinc-300 bg-zinc-800 px-1.5 py-0.5 rounded">
              vsa fleet assign --domain X --primary vps-Y --standbys vps-Z
            </code>{" "}
            to add explicit ones.
          </p>
        )}
      </div>
    </div>
  );
}
