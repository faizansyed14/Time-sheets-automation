import { type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  Mail,
  UploadCloud,
  Activity,
  Users,
  FolderOpen,
  Zap,
  CircleDot,
} from "lucide-react";
import { fetchHealth, fetchPipelineStats } from "../api/client";
import { cn } from "../lib/utils";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/inbox", label: "Email Inbox", icon: Mail },
  { to: "/upload", label: "Upload", icon: UploadCloud },
  { to: "/pipeline", label: "Pipeline", icon: Activity },
  { to: "/employees", label: "Employees", icon: Users },
  { to: "/files", label: "File Vault", icon: FolderOpen },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/inbox": "Email Inbox",
  "/upload": "Upload timesheets",
  "/pipeline": "Pipeline tracker",
  "/employees": "Employee matcher",
  "/files": "File vault",
};

export default function Shell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 60_000 });
  const { data: stats } = useQuery({
    queryKey: ["pipeline-stats"],
    queryFn: fetchPipelineStats,
    refetchInterval: 15_000,
  });
  const attention = (stats?.failed ?? 0) + (stats?.needs_review ?? 0);

  const section = "/" + (location.pathname.split("/")[1] || "");
  const title = TITLES[section] ?? "Timesheets Automation";

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ------------------------- Sidebar ------------------------- */}
      <aside className="flex w-60 shrink-0 flex-col bg-slate-900 text-slate-300">
        <div className="flex items-center gap-2.5 px-5 pb-5 pt-6">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-violet-600 shadow-lg shadow-brand-900/40">
            <Zap className="h-5 w-5 text-white" />
          </div>
          <div>
            <p className="text-[15px] font-bold leading-tight text-white">Timesheets</p>
            <p className="text-[11px] leading-tight text-slate-400">Intelligence</p>
          </div>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-brand-600/90 text-white shadow-sm"
                    : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                )
              }
            >
              <Icon className="h-[18px] w-[18px]" />
              <span className="flex-1">{label}</span>
              {to === "/pipeline" && attention > 0 && (
                <span className="rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] font-bold text-white">
                  {attention}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="mx-3 mb-4 rounded-xl bg-slate-800/70 p-3">
          <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">
            System
          </p>
          <div className="space-y-1.5 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Email</span>
              <span className="flex items-center gap-1 font-medium text-slate-200">
                <CircleDot className="h-3 w-3 text-emerald-400" />
                {health?.email_provider ?? "…"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Extraction</span>
              <span className="flex items-center gap-1 font-medium text-slate-200">
                <CircleDot className="h-3 w-3 text-emerald-400" />
                {health?.extraction_engine ?? "…"}
              </span>
            </div>
          </div>
        </div>
      </aside>

      {/* ------------------------- Main ------------------------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6">
          <p className="text-sm font-semibold text-slate-500">
            <span className="text-slate-400">Timesheets Automation / </span>
            <span className="text-slate-800">{title}</span>
          </p>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            {stats && (
              <span>
                {stats.total} files tracked · {stats.success} ok ·{" "}
                <span className={attention ? "font-semibold text-rose-600" : ""}>
                  {attention} need attention
                </span>
              </span>
            )}
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
