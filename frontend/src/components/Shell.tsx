import { useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
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
  CalendarClock,
  Bug,
  LogOut,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { fetchHealth, fetchPipelineStats } from "../api/client";
import { cn, avatarColor, initials } from "../lib/utils";
import { useAuth } from "../lib/auth";
import AutoExtractWidget from "./AutoExtractWidget";
import ExtractQueueWidget from "./ExtractQueueWidget";
import ExtractQueueBridge from "./ExtractQueueBridge";
import { useExtractQueue } from "../lib/extractQueue";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/inbox", label: "Inbox", icon: Mail },
  // Every record that needs a human — review, fix, or just watch it happen —
  // lives here now, with a Review button right on each row. Keeps the
  // attention badge that used to live on the standalone Review page.
  { to: "/pipeline", label: "Activity log", icon: Activity, attention: true },
];

const TOOLS_NAV = [
  { to: "/chat", label: "Ask AI", icon: MessagesSquare },
  { to: "/upload", label: "Upload", icon: UploadCloud },
  { to: "/employees", label: "Employee matcher", icon: Users },
  { to: "/export", label: "Export", icon: FileSpreadsheet },
  { to: "/files", label: "File Vault", icon: FolderOpen },
  // Read by every extraction run and relevant to normal timesheet review, not
  // just admin config — every role can view it (backend: require_write, same
  // as the routes above), so it lives here rather than under Admin.
  { to: "/admin/calendars", label: "Month calendars", icon: CalendarClock },
];

const ADMIN_NAV = [
  { to: "/admin/users", label: "Users & access", icon: ShieldCheck },
  { to: "/admin/settings", label: "AI Settings", icon: Settings },
  { to: "/admin/debug", label: "Extraction debug", icon: Bug },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/inbox": "Inbox",
  "/chat": "Ask AI",
  "/upload": "Upload timesheets",
  "/pipeline": "Activity log",
  "/employees": "Employee matcher",
  "/export": "Export",
  "/files": "File vault",
};

/** Short blurb shown beside "Workspace" — replaces long page subtitles. */
const WORKSPACE_BLURBS: Record<string, string> = {
  "/inbox": "Select a thread, then Extract Email",
  "/pipeline": "Review, fix or view each pipeline file",
  "/chat": "Ask or edit timesheets in this database",
  "/employees": "Match list — same ID OK in AUH & DXB",
};

const COLLAPSE_KEY = "nav_collapsed";

export default function Shell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const qc = useQueryClient();
  const { user, isAdmin, canWrite, isVaultMatcherOnly, logout } = useAuth();
  // While a run is actually working, its step bar takes over the whole
  // header — left corner to right corner, right up against the stats pill —
  // instead of squeezing in beside the page title.
  const { current: extracting } = useExtractQueue();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 60_000 });
  const { data: stats } = useQuery({
    queryKey: ["pipeline-stats"],
    queryFn: fetchPipelineStats,
    refetchInterval: 15_000,
    // The restricted vault_matcher role has no access to /pipeline at all
    // (403 server-side) — this page's stats/attention badge are irrelevant
    // to it anyway, so skip the call entirely.
    enabled: !isVaultMatcherOnly,
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

  // The restricted vault_matcher role only ever sees Employee matcher + File
  // Vault (server-side enforced too — see api/deps.require_full_access).
  const visibleNav = isVaultMatcherOnly ? [] : NAV;
  const visibleToolsNav = isVaultMatcherOnly
    ? TOOLS_NAV.filter((n) => n.to === "/employees" || n.to === "/files")
    : TOOLS_NAV.filter((n) => canWrite || n.to !== "/upload");

  const section = "/" + (location.pathname.split("/")[1] || "");
  const title = TITLES[section] ?? "Timesheets Automation";
  const workspaceBlurb = WORKSPACE_BLURBS[section];

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      "group relative flex items-center rounded-md py-2 text-xs font-medium transition-colors duration-150",
      collapsed ? "justify-center px-1.5" : "gap-2 px-2",
      isActive
        ? "bg-brand-600/10 text-brand-600 font-semibold"
        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
    );

  return (
    <div className="flex h-screen overflow-hidden app-canvas">
      <ExtractQueueBridge />
      <aside
        className={cn(
          "relative flex shrink-0 flex-col border-r border-slate-200/80 bg-white/70 text-slate-700 backdrop-blur-md transition-[width] duration-200",
          collapsed ? "w-14" : "w-48"
        )}
      >
        <div className={cn("flex items-center border-b border-slate-200/70 py-3", collapsed ? "justify-center px-1.5" : "gap-2 px-2.5")}>
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-600 shadow-sm">
            <Zap className="h-4 w-4 text-white" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="truncate font-serif text-xs font-bold leading-tight text-slate-900">Timesheets</p>
              <p className="truncate text-[10px] font-medium text-slate-500">Intelligence Portal</p>
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto px-1.5 py-3">
          {visibleNav.map(({ to, label, icon: Icon, end, attention: showAttention }) => (
            <NavLink key={to} to={to} end={end} title={collapsed ? label : undefined} className={navLinkClass}>
              {({ isActive }) => (
                <>
                  <Icon className={cn("h-4 w-4 shrink-0", isActive ? "text-brand-600" : "text-slate-400 group-hover:text-slate-700")} />
                  {!collapsed && <span className="min-w-0 flex-1 truncate leading-tight">{label}</span>}
                  {showAttention && attention > 0 && (
                    <span
                      className={cn(
                        "rounded-full bg-amber-500 font-bold text-white",
                        collapsed ? "absolute right-1 top-1 h-1.5 w-1.5" : "px-1 py-px text-[9px]"
                      )}
                    >
                      {collapsed ? "" : attention}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}

          {!collapsed && visibleNav.length > 0 && (
            <p className="px-2 pb-1 pt-4 text-[9px] font-semibold uppercase tracking-widest text-slate-500">Tools</p>
          )}
          {collapsed && visibleNav.length > 0 && <div className="my-3 border-t border-slate-200/70" />}
          {visibleToolsNav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} title={collapsed ? label : undefined} className={navLinkClass}>
              {({ isActive }) => (
                <>
                  <Icon className={cn("h-4 w-4 shrink-0", isActive ? "text-brand-600" : "text-slate-400 group-hover:text-slate-700")} />
                  {!collapsed && <span className="min-w-0 flex-1 truncate leading-tight">{label}</span>}
                </>
              )}
            </NavLink>
          ))}

          {isAdmin && (
            <>
              {!collapsed && (
                <p className="px-2 pb-1 pt-4 text-[9px] font-semibold uppercase tracking-widest text-slate-500">Admin</p>
              )}
              {collapsed && <div className="my-3 border-t border-slate-200/70" />}
              {ADMIN_NAV.map(({ to, label, icon: Icon }) => (
                <NavLink key={to} to={to} title={collapsed ? label : undefined} className={navLinkClass}>
                  {({ isActive }) => (
                    <>
                      <Icon className={cn("h-4 w-4 shrink-0", isActive ? "text-brand-600" : "text-slate-400 group-hover:text-slate-700")} />
                      {!collapsed && <span className="min-w-0 flex-1 truncate leading-tight">{label}</span>}
                    </>
                  )}
                </NavLink>
              ))}
            </>
          )}
        </nav>

        {!collapsed && (
          <div className="mx-1.5 mb-3 rounded-md border border-slate-200/70 bg-slate-100/50 p-2">
            <p className="mb-1.5 text-[9px] font-semibold uppercase tracking-widest text-slate-500">System</p>
            <div className="space-y-1.5 text-[10px]">
              <div className="flex items-center justify-between gap-1">
                <span className="text-slate-500">Email</span>
                <span className="flex min-w-0 items-center gap-1 truncate font-medium text-slate-700">
                  <CircleDot className="h-2.5 w-2.5 shrink-0 text-emerald-500" />
                  <span className="truncate">{health?.email_provider ?? "…"}</span>
                </span>
              </div>
              <div className="flex items-center justify-between gap-1">
                <span className="text-slate-500">Extract</span>
                <span className="flex min-w-0 items-center gap-1 truncate font-medium text-slate-700">
                  <CircleDot className="h-2.5 w-2.5 shrink-0 text-emerald-500" />
                  <span className="truncate">{health?.extraction_engine ?? "…"}</span>
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
        <header className="relative z-20 flex h-14 shrink-0 items-center gap-4 surface-glass px-5 sm:px-6">
          {/* Page title steps aside while a run is actually in progress so
              the step bar can run edge to edge instead of squeezing beside it. */}
          {!extracting && (
            <div className="min-w-0 shrink-0">
              <p className="flex flex-wrap items-baseline gap-x-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
                <span>Workspace</span>
                {workspaceBlurb && (
                  <span className="normal-case tracking-normal font-medium text-slate-500">
                    · {workspaceBlurb}
                  </span>
                )}
              </p>
              <p className="font-serif text-base font-bold tracking-tight text-slate-900">{title}</p>
            </div>
          )}
          {/* Extract Email progress — full-width 3-step bar, renders nothing
              when no run is active/recently finished. Irrelevant (and would
              403) for the restricted vault_matcher role — it has no /inbox
              or /pipeline access. */}
          {!isVaultMatcherOnly && <ExtractQueueWidget />}
          <div className="ml-auto flex shrink-0 items-center gap-2.5 text-xs text-slate-500">
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
            {!isVaultMatcherOnly && <AutoExtractWidget />}
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
