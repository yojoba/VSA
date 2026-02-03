import { clsx } from "clsx";

const statusColors: Record<string, string> = {
  running: "bg-green-500/20 text-green-400",
  exited: "bg-red-500/20 text-red-400",
  active: "bg-green-500/20 text-green-400",
  valid: "bg-green-500/20 text-green-400",
  expiring: "bg-yellow-500/20 text-yellow-400",
  expired: "bg-red-500/20 text-red-400",
  success: "bg-green-500/20 text-green-400",
  failure: "bg-red-500/20 text-red-400",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={clsx(
        "px-2 py-0.5 rounded-full text-xs font-medium",
        statusColors[status] || "bg-zinc-700 text-zinc-300"
      )}
    >
      {status}
    </span>
  );
}
