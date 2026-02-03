import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: "VSA Dashboard",
  description: "FlowBiz VPS Admin Suite — centralized management dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="flex min-h-screen">
        <Providers>
          <Sidebar />
          <main className="flex-1 p-6 overflow-auto">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
