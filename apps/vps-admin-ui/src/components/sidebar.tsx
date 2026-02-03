"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Box,
  Globe,
  ShieldCheck,
  ScrollText,
  Server,
} from "lucide-react";

const navItems = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/containers", label: "Containers", icon: Box },
  { href: "/domains", label: "Domains", icon: Globe },
  { href: "/certificates", label: "Certificates", icon: ShieldCheck },
  { href: "/audit", label: "Audit Logs", icon: ScrollText },
  { href: "/vps", label: "VPS Nodes", icon: Server },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <nav className="w-56 bg-zinc-900 border-r border-zinc-800 p-4 flex flex-col gap-1">
      <div className="text-lg font-bold text-white mb-6 px-2">VSA Dashboard</div>
      {navItems.map((item) => {
        const Icon = item.icon;
        const isActive = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
              isActive
                ? "bg-zinc-800 text-white"
                : "text-zinc-400 hover:text-white hover:bg-zinc-800/50"
            }`}
          >
            <Icon className="w-4 h-4" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
