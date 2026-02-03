"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";
import { Box, Globe, ShieldCheck, Server } from "lucide-react";

function StatCard({
  title,
  value,
  icon: Icon,
}: {
  title: string;
  value: string | number;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
      <div className="flex items-center gap-3 mb-2">
        <Icon className="w-5 h-5 text-zinc-400" />
        <span className="text-sm text-zinc-400">{title}</span>
      </div>
      <div className="text-3xl font-bold text-white">{value}</div>
    </div>
  );
}

export default function OverviewPage() {
  const containers = useQuery({ queryKey: ["containers"], queryFn: api.getContainers });
  const stacks = useQuery({ queryKey: ["stacks"], queryFn: api.getStacks });
  const domains = useQuery({ queryKey: ["domains"], queryFn: api.getDomains });
  const certs = useQuery({ queryKey: ["certs"], queryFn: api.getCerts });
  const vpsNodes = useQuery({ queryKey: ["vps"], queryFn: api.getVpsNodes });

  const runningCount = containers.data?.filter((c) => c.status === "running").length ?? 0;

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Overview</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard title="Running Containers" value={runningCount} icon={Box} />
        <StatCard title="Domains" value={domains.data?.length ?? "..."} icon={Globe} />
        <StatCard title="Certificates" value={certs.data?.length ?? "..."} icon={ShieldCheck} />
        <StatCard title="VPS Nodes" value={vpsNodes.data?.length ?? "..."} icon={Server} />
      </div>

      <h2 className="text-lg font-semibold text-white mb-4">Stacks</h2>
      <div className="space-y-3">
        {stacks.data?.map((stack) => (
          <div key={stack.name} className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <h3 className="font-medium text-white mb-2">{stack.name}</h3>
            <div className="flex flex-wrap gap-2">
              {stack.containers.map((c) => (
                <div key={c.name} className="flex items-center gap-2 text-sm text-zinc-400">
                  <StatusBadge status={c.status} />
                  <span>{c.service}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
        {stacks.isLoading && <p className="text-zinc-500">Loading stacks...</p>}
      </div>
    </div>
  );
}
