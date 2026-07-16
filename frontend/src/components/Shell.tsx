import { useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
  ClipboardCheck,
  Mail,
  MessagesSquare,
  UploadCloud,
  Activity,
  Users,
  FolderOpen,
  FileSpreadsheet,
  Zap,
  CircleDot,
  Settings,
  ShieldCheck,
  LogOut,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { fetchHealth, fetchPipelineStats } from "../api/client";
import { cn, avatarColor, initials } from "../lib/utils";
import { useAuth } from "../lib/auth";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/inbox", label: "Inbox", icon: Mail },
  { to: "/review", label: "Review", icon: ClipboardCheck, attention: true },
];

const TOOLS_NAV = [
  { to: "/chat", label: "Ask AI", icon: MessagesSquare },
  { to: "/upload", label: "Upload", icon: UploadCloud },
  { to: "/pipeline", label: "Activity log", icon: Activity },
  { to: "/employees", label: "Employees", icon: Users },
  { to: "/export", label: "Export", icon: FileSpreadsheet },
  { to: "/files", label: "File Vault", icon: FolderOpen },
];

const ADMIN_NAV = [
  { to: "/admin/users", label: "Users & access", icon: ShieldCheck },
  { to: "/admin/settings", label: "AI Settings", icon: Settings },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/inbox": "Inbox",
  "/review": "Review",
  "/chat": "Ask AI",
  "/upload": "Upload timesheets",
  "/pipeline": "Activity log",
  "/employees": "Employee matcher",
  "/export": "Export",
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
      "group relative flex items-center rounded-lg py-2.5 text-sm font-medium transition-colors duration-150",
      collapsed ? "justify-center px-2" : "gap-3 px-3",
      isActive
        ? "bg-brand-600 text-white shadow-sm"
        : "text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-text"
    );

  return (
    <div className="flex h-screen overflow-hidden app-canvas">
      <aside
        className={cn(
          "relative flex shrink-0 flex-col bg-sidebar text-sidebar-text shadow-sidebar transition-[width] duration-200",
          collapsed ? "w-[72px]" : "w-60"
        )}
      >
        <div className={cn("flex items-center border-b border-sidebar-border/60 py-5", collapsed ? "justify-center px-2" : "gap-3 px-4")}>
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-600 shadow-sm">
            <Zap className="h-5 w-5 text-white" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="truncate text-sm font-bold leading-tight text-white">Timesheets</p>
              <p className="text-[11px] font-medium text-sidebar-muted">Intelligence Portal</p>
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-4">
          {NAV.map(({ to, label, icon: Icon, end, attention: showAttention }) => (
            <NavLink key={to} to={to} end={end} title={collapsed ? label : undefined} className={navLinkClass}>
              {({ isActive }) => (
                <>
                  <Icon className={cn("h-[18px] w-[18px] shrink-0", isActive ? "text-white" : "text-sidebar-muted group-hover:text-sidebar-text")} />
                  {!collapsed && <span className="flex-1">{label}</span>}
                  {showAttention && attention > 0 && (
                    <span
                      className={cn(
                        "rounded-full bg-amber-500 font-bold text-white",
                        collapsed ? "absolute right-1.5 top-1.5 h-2 w-2" : "px-1.5 py-0.5 text-[10px]"
                      )}
                    >
                      {collapsed ? "" : attention}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}

          {!collapsed && (
            <p className="px-3 pb-1.5 pt-6 text-[10px] font-semibold uppercase tracking-widest text-slate-500">Tools</p>
          )}
          {collapsed && <div className="my-3 border-t border-sidebar-border/60" />}
          {TOOLS_NAV.filter((n) => canWrite || n.to !== "/upload").map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} title={collapsed ? label : undefined} className={navLinkClass}>
              {({ isActive }) => (
                <>
                  <Icon className={cn("h-[18px] w-[18px] shrink-0", isActive ? "text-white" : "text-sidebar-muted group-hover:text-sidebar-text")} />
                  {!collapsed && <span className="flex-1">{label}</span>}
                </>
              )}
            </NavLink>
          ))}

          {isAdmin && (
            <>
              {!collapsed && (
                <p className="px-3 pb-1.5 pt-6 text-[10px] font-semibold uppercase tracking-widest text-slate-500">Admin</p>
              )}
              {collapsed && <div className="my-3 border-t border-sidebar-border/60" />}
              {ADMIN_NAV.map(({ to, label, icon: Icon }) => (
                <NavLink key={to} to={to} title={collapsed ? label : undefined} className={navLinkClass}>
                  {({ isActive }) => (
                    <>
                      <Icon className={cn("h-[18px] w-[18px] shrink-0", isActive ? "text-white" : "text-sidebar-muted group-hover:text-sidebar-text")} />
                      {!collapsed && <span className="flex-1">{label}</span>}
                    </>
                  )}
                </NavLink>
              ))}
            </>
          )}
        </nav>

        {!collapsed && (
          <div className="mx-2 mb-4 rounded-lg border border-sidebar-border/60 bg-sidebar-hover/50 p-3">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-500">System</p>
            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-sidebar-muted">Email</span>
                <span className="flex items-center gap-1.5 font-medium text-sidebar-text">
                  <CircleDot className="h-3 w-3 text-emerald-400" />
                  {health?.email_provider ?? "…"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sidebar-muted">Extraction</span>
                <span className="flex items-center gap-1.5 font-medium text-sidebar-text">
                  <CircleDot className="h-3 w-3 text-emerald-400" />
                  {health?.extraction_engine ?? "…"}
                </span>
              </div>
            </div>
          </div>
        )}

        <button
          type="button"
          onClick={toggleCollapsed}
          className="absolute right-0 top-1/2 z-30 flex h-12 w-4 -translate-y-1/2 translate-x-1/2 items-center justify-center rounded-r-md border border-slate-200 bg-white text-slate-500 shadow-card transition-colors hover:border-slate-300 hover:text-slate-800"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5 shrink-0" strokeWidth={2.5} />
          ) : (
            <ChevronLeft className="h-3.5 w-3.5 shrink-0" strokeWidth={2.5} />
          )}
        </button>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between surface-glass px-5 sm:px-6">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">Workspace</p>
            <p className="text-base font-bold tracking-tight text-slate-900">{title}</p>
          </div>
          <div className="flex items-center gap-2.5 text-xs text-slate-500">
            {stats && (
              <span className="hidden items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 lg:inline-flex">
                <span className="font-semibold text-slate-700">{stats.total}</span>
                <span className="text-slate-300">·</span>
                <span className="font-semibold text-emerald-600">{stats.success} ok</span>
                <span className="text-slate-300">·</span>
                <span className={attention ? "font-semibold text-amber-600" : "font-semibold text-slate-500"}>
                  {attention} review
                </span>
              </span>
            )}
            <button
              onClick={reload}
              disabled={refreshing}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 font-medium text-slate-600 shadow-xs transition-colors hover:bg-slate-50 disabled:opacity-60"
              title="Reload data on this page"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
              <span className="hidden sm:inline">Reload</span>
            </button>
            {user && (
              <div className="flex items-center gap-2 border-l border-slate-200 pl-2.5">
                {!canWrite && (
                  <span className="rounded-md bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-inset ring-amber-200">
                    Read-only
                  </span>
                )}
                <span className={cn("flex h-8 w-8 items-center justify-center rounded-full text-[10px] font-bold ring-2 ring-white", avatarColor(user.username))}>
                  {initials(user.username)}
                </span>
                <div className="hidden text-right sm:block">
                  <p className="text-xs font-semibold leading-tight text-slate-700">{user.username}</p>
                  <p className="text-[10px] capitalize leading-tight text-slate-400">{user.role}</p>
                </div>
                <button
                  onClick={logout}
                  className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-rose-50 hover:text-rose-600"
                  title="Sign out"
                >
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
