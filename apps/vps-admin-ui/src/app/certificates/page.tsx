"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { clsx } from "clsx";

function CertStatusBadge({ status, days }: { status: string; days: number | null }) {
  const color =
    status === "valid"
      ? "bg-green-500/20 text-green-400"
      : status === "warning"
        ? "bg-yellow-500/20 text-yellow-400"
        : status === "critical"
          ? "bg-red-500/20 text-red-300"
          : status === "expired"
            ? "bg-red-500/30 text-red-400"
            : "bg-zinc-500/20 text-zinc-400";

  const label =
    status === "expired"
      ? "Expired"
      : status === "critical"
        ? `${days}d - Critical`
        : status === "warning"
          ? `${days}d - Renew soon`
          : days !== null
            ? `${days}d`
            : "Unknown";

  return (
    <span className={clsx("px-2 py-0.5 rounded-full text-xs font-medium", color)}>
      {label}
    </span>
  );
}

export default function CertificatesPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["certs"],
    queryFn: api.getCerts,
    refetchInterval: 60000,
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">SSL Certificates</h1>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50">
            <tr>
              <th className="text-left p-3 text-zinc-400 font-medium">Domain</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Issuer</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Expiry</th>
              <th className="text-right p-3 text-zinc-400 font-medium">Days Left</th>
              <th className="text-left p-3 text-zinc-400 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.map((c) => (
              <tr key={c.domain} className="hover:bg-zinc-800/30">
                <td className="p-3 text-white font-mono">{c.domain}</td>
                <td className="p-3 text-zinc-400">{c.issuer}</td>
                <td className="p-3 text-zinc-400">
                  {c.expiry ? new Date(c.expiry).toLocaleDateString() : "-"}
                </td>
                <td
                  className={clsx(
                    "p-3 text-right font-mono",
                    c.days_remaining !== null && c.days_remaining < 0
                      ? "text-red-400"
                      : c.days_remaining !== null && c.days_remaining <= 14
                        ? "text-red-300"
                        : c.days_remaining !== null && c.days_remaining <= 30
                          ? "text-yellow-400"
                          : "text-zinc-300"
                  )}
                >
                  {c.days_remaining !== null ? c.days_remaining : "-"}
                </td>
                <td className="p-3">
                  <CertStatusBadge status={c.status} days={c.days_remaining} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
        {isError && <p className="text-red-400 p-4">Failed to load certificates.</p>}
        {data?.length === 0 && <p className="text-zinc-500 p-4">No certificates found.</p>}
      </div>
    </div>
  );
}
