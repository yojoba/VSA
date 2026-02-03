"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";

export default function CertificatesPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["certs"],
    queryFn: api.getCerts,
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
              <th className="text-left p-3 text-zinc-400 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {data?.map((c) => (
              <tr key={c.id} className="hover:bg-zinc-800/30">
                <td className="p-3 text-white font-mono">{c.domain}</td>
                <td className="p-3 text-zinc-400">{c.issuer}</td>
                <td className="p-3 text-zinc-400">
                  {c.expiry ? new Date(c.expiry).toLocaleDateString() : "-"}
                </td>
                <td className="p-3">
                  <StatusBadge status={c.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="text-zinc-500 p-4">Loading...</p>}
        {data?.length === 0 && <p className="text-zinc-500 p-4">No certificates tracked yet.</p>}
      </div>
    </div>
  );
}
