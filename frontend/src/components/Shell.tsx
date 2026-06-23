import { useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
  Mail,
  UploadCloud,
  Activity,
  Users,
  FolderOpen,
  Zap,
  CircleDot,
  Settings,
  ShieldCheck,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
} from "lucide-react";
import { fetchHealth, fetchPipelineStats } from "../api/client";
import { cn, avatarColor, initials } from "../lib/utils";
import { useAuth } from "../lib/auth";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/inbox", label: "Email Inbox", icon: Mail },
  { to: "/upload", label: "Upload", icon: UploadCloud },
  { to: "/pipeline", label: "Pipeline", icon: Activity },
  { to: "/employees", label: "Employees", icon: Users },
  { to: "/files", label: "File Vault", icon: FolderOpen },
];

const ADMIN_NAV = [
  { to: "/admin/users", label: "Users & access", icon: ShieldCheck },
  { to: "/admin/settings", label: "AI Settings", icon: Settings },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/inbox": "Email Inbox",
  "/upload": "Upload timesheets",
  "/pipeline": "Pipeline tracker",
  "/employees": "Employee matcher",
  "/files": "File vault",
};

const COLLAPSE_KEY = "nav_collapsed";

export default function Shell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const qc = useQueryClient();
  const { user, isAdmin, canWrite, logout } = useAuth();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 60_000 });
  const { data: stats } = useQuery({
    queryKey: ["pipeline-stats"],
    queryFn: fetchPipelineStats,
    refetchInterval: 15_000,
  });
  const attention = (stats?.failed ?? 0) + (stats?.needs_review ?? 0);

  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === "1");
  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  };

  // Global "reload data": refetch every active query on the current page.
  const [refreshing, setRefreshing] = useState(false);
  const reload = async () => {
    setRefreshing(true);
    try {
      await qc.invalidateQueries();
    } finally {
      setRefreshing(false);
    }
  };

  const section = "/" + (location.pathname.split("/")[1] || "");
  const title = TITLES[section] ?? "Timesheets Automation";

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      "group relative flex items-center rounded-lg py-2 text-sm font-medium transition-colors",
      collapsed ? "justify-center px-2" : "gap-3 px-3",
      isActive ? "bg-brand-600 text-white shadow-xs" : "text-slate-400 hover:bg-slate-800/70 hover:text-white"
    );

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ------------------------- Sidebar ------------------------- */}
      <aside
        className={cn(
          "flex shrink-0 flex-col border-r border-slate-800/60 bg-slate-950 text-slate-300 transition-[width] duration-200",
          collapsed ? "w-16" : "w-60"
        )}
      >
        <div className={cn("flex items-center pb-5 pt-6", collapsed ? "justify-center px-2" : "gap-2.5 px-5")}>
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-600 ring-1 ring-brand-400/20">
            <Zap className="h-5 w-5 text-white" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="truncate text-[15px] font-semibold leading-tight tracking-tight text-white">Timesheets</p>
              <p className="text-[11px] leading-tight text-slate-500">Intelligence</p>
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV.filter((n) => canWrite || n.to !== "/upload").map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} title={collapsed ? label : undefined} className={navLinkClass}>
              <Icon className="h-[18px] w-[18px] shrink-0" />
              {!collapsed && <span className="flex-1">{label}</span>}
              {to === "/pipeline" && attention > 0 && (
                <span
                  className={cn(
                    "rounded-full bg-rose-500 font-bold text-white",
                    collapsed ? "absolute right-1 top-1 h-2 w-2" : "px-1.5 py-0.5 text-[10px]"
                  )}
                >
                  {collapsed ? "" : attention}
                </span>
              )}
            </NavLink>
          ))}

          {isAdmin && (
            <>
              {!collapsed && (
                <p className="px-3 pb-1 pt-4 text-[10px] font-bold uppercase tracking-wider text-slate-600">Admin</p>
              )}
              {collapsed && <div className="my-2 border-t border-slate-800/70" />}
              {ADMIN_NAV.map(({ to, label, icon: Icon }) => (
                <NavLink key={to} to={to} title={collapsed ? label : undefined} className={navLinkClass}>
                  <Icon className="h-[18px] w-[18px] shrink-0" />
                  {!collapsed && <span className="flex-1">{label}</span>}
                </NavLink>
              ))}
            </>
          )}
        </nav>

        {!collapsed && (
          <div className="mx-3 mb-4 rounded-xl bg-slate-900 p-3 ring-1 ring-slate-800/80">
            <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">System</p>
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
        )}
      </aside>

      {/* ------------------------- Main ------------------------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              onClick={toggleCollapsed}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {collapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-5 w-5" />}
            </button>
            <p className="text-sm font-semibold text-slate-500">
              <span className="hidden text-slate-400 sm:inline">Timesheets Automation / </span>
              <span className="text-slate-800">{title}</span>
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            {stats && (
              <span className="hidden lg:inline">
                {stats.total} files tracked · {stats.success} ok ·{" "}
                <span className={attention ? "font-semibold text-rose-600" : ""}>{attention} need attention</span>
              </span>
            )}
            <button
              onClick={reload}
              disabled={refreshing}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 font-medium text-slate-600 shadow-xs hover:bg-slate-50 disabled:opacity-60"
              title="Reload data on this page"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
              <span className="hidden sm:inline">Reload</span>
            </button>
            {user && (
              <div className="flex items-center gap-2">
                {!canWrite && (
                  <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
                    Read-only
                  </span>
                )}
                <span className={cn("flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-bold", avatarColor(user.username))}>
                  {initials(user.username)}
                </span>
                <div className="hidden text-right sm:block">
                  <p className="text-xs font-semibold leading-tight text-slate-700">{user.username}</p>
                  <p className="text-[10px] capitalize leading-tight text-slate-400">{user.role}</p>
                </div>
                <button
                  onClick={logout}
                  className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-rose-500"
                  title="Sign out"
                >
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
