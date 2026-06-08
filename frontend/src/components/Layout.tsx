import { NavLink, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

const nav = [
  { to: "/", label: "Dashboard", icon: GridIcon, end: true },
  { to: "/inbox", label: "Email Inbox", icon: MailIcon, end: false },
  { to: "/upload", label: "Upload", icon: UploadIcon, end: false },
  { to: "/files", label: "Files", icon: FolderIcon, end: false },
  { to: "/employee-matcher", label: "Employee Matcher", icon: UsersIcon, end: false },
];

export default function Layout({ children }: { children: ReactNode }) {
  const loc = useLocation();
  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 flex-col border-r border-ink/10 bg-ink text-slate-200 md:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-petrol-500 font-mono text-sm font-semibold text-white">
            TS
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-white">Timesheet</div>
            <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Intelligence</div>
          </div>
        </div>

        <nav className="mt-2 flex-1 space-y-1 px-3">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                  isActive
                    ? "bg-white/10 text-white"
                    : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                }`
              }
            >
              <n.icon />
              {n.label}
            </NavLink>
          ))}
        </nav>

        <div className="px-5 py-4 text-[11px] text-slate-500">
          <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2.5">
            <div className="font-medium text-slate-300">Mock mode</div>
            <div className="mt-0.5 text-slate-500">Email + LLM are mocked. Swap to Graph + Vision in config.</div>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-canvas/80 px-6 py-3.5 backdrop-blur">
          <Breadcrumb path={loc.pathname} />
          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-slate-400 sm:block">HR Admin</span>
            <div className="grid h-8 w-8 place-items-center rounded-full bg-petrol-100 font-mono text-xs font-semibold text-petrol-700">
              HR
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}

function Breadcrumb({ path }: { path: string }) {
  const label = path.startsWith("/inbox")
    ? "Email Inbox"
    : path.startsWith("/upload")
    ? "Upload"
    : path.startsWith("/files")
    ? "Files"
    : path.startsWith("/employee-matcher")
    ? "Employee Matcher"
    : path.startsWith("/employee")
    ? "Employee Record"
    : "Dashboard";
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-slate-400">Portal</span>
      <span className="text-slate-300">/</span>
      <span className="font-medium text-ink">{label}</span>
    </div>
  );
}

function GridIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}
function MailIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" />
    </svg>
  );
}
function UploadIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5" /><path d="M12 3v12" />
    </svg>
  );
}
function UsersIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}
function FolderIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}
