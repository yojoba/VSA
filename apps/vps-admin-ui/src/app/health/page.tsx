"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { clsx } from "clsx";

const LEVEL_STYLES: Record<string, string> = {
  critical: "bg-red-500/20 text-red-300 border-red-500/40",
  warning: "bg-yellow-500/20 text-yellow-300 border-yellow-500/40",
  info: "bg-zinc-700/40 text-zinc-300 border-zinc-600/40",
};

export default function HealthPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["fleet-drift"],
    queryFn: api.getFleetDrift,
    refetchInterval: 60000,
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">Fleet Health</h1>
      <p className="text-zinc-400 text-sm mb-6">
        Cross-checks the intended layout (Assignments) against what each
        agent has actually reported. Re-runs every minute.
      </p>

      {data && (
        <div className="grid grid-cols-4 gap-3 mb-6">
          {(["critical", "warning", "info", "total"] as const).map((k) => (
            <div
              key={k}
              className="bg-zinc-900 border border-zinc-800 rounded-xl p-4"
            >
              <div className="text-zinc-400 text-xs uppercase tracking-wider">
                {k}
              </div>
              <div
                className={clsx(
                  "text-3xl font-bold mt-1",
                  k === "critical" && data.summary.critical > 0 && "text-red-400",
                  k === "warning" && data.summary.warning > 0 && "text-yellow-400",
                  (k === "info" || k === "total") && "text-white",
                )}
              >
                {data.summary[k]}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">Level</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Kind</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Domain</th>
              <th className="text-left p-3 text-zinc-400 font-medium">VPS</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.findings.map((f, i) => (
              <tr key={i} className="hover:bg-zinc-800/30">
                <td className="p-3">
                  <span
                    className={clsx(
                      "px-2 py-0.5 rounded-full text-xs font-medium border",
                      LEVEL_STYLES[f.level] ?? LEVEL_STYLES.info,
                    )}
                  >
                    {f.level}
                  </span>
                </td>
                <td className="p-3 text-zinc-300 font-mono text-xs">{f.kind}</td>
                <td className="p-3 text-white font-mono">{f.domain ?? "-"}</td>
                <td className="p-3 text-zinc-400 font-mono">{f.vps_id ?? "-"}</td>
                <td className="p-3 text-zinc-400">{f.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
        {isError && <p className="text-red-400 p-4">Failed to load fleet drift report.</p>}
        {data && data.findings.length === 0 && (
          <p className="text-green-400 p-4">
            ✓ Fleet is healthy — no drift detected between intent and observed state.
          </p>
        )}
      </div>
    </div>
  );
}
