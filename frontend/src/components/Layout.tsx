import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { ReactNode, useState } from "react";
import { TopProgressBar, useGlobalProgress } from "./ui";

const navItems = [
  { to: "/", label: "Dashboard", icon: GridIcon, end: true },
  { to: "/inbox", label: "Email Inbox", icon: MailIcon },
  { to: "/upload", label: "Direct Upload", icon: UploadIcon },
  { to: "/files", label: "File Archive", icon: FolderIcon },
  { to: "/employee-matcher", label: "Employee Matcher", icon: UsersIcon },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { isProcessing } = useGlobalProgress();
  const loc = useLocation();
  const [isCollapsed, setIsCollapsed] = useState(false);

  return (
    <div className="flex min-h-screen bg-canvas font-sans selection:bg-petrol-100">
      <TopProgressBar active={isProcessing} />

      {/* Sidebar */}
      <aside className={`sticky top-0 hidden h-screen ${isCollapsed ? 'w-20' : 'w-64'} flex-col bg-ink text-white md:flex shadow-2xl z-20 transition-all duration-300 overflow-hidden`}>
        <div className={`flex items-center gap-3 px-6 py-10 ${isCollapsed ? 'justify-center' : ''}`}>
          <div className="relative group shrink-0">
            <div className="absolute -inset-1 rounded-xl bg-gradient-to-r from-petrol-400 to-sky-500 opacity-75 blur transition duration-300 group-hover:opacity-100" />
            <div className="relative grid h-10 w-10 place-items-center rounded-xl bg-ink font-mono text-base font-bold text-petrol-400 ring-1 ring-white/10">
              TS
            </div>
          </div>
          {!isCollapsed && (
            <div className="animate-in fade-in duration-500">
              <div className="text-sm font-bold tracking-tight whitespace-nowrap">Timesheet Portal</div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500 font-semibold">Intelligence v2.0</div>
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-1.5 px-3 mt-2">
          {navItems.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              title={isCollapsed ? n.label : undefined}
              className={({ isActive }) =>
                `group flex items-center ${isCollapsed ? 'justify-center' : 'gap-3.5'} rounded-2xl px-4 py-3.5 text-sm font-medium transition-all duration-200 ${isActive
                  ? "bg-white/10 text-white shadow-inner ring-1 ring-white/10"
                  : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
                }`
              }
            >
              <n.icon className={`transition-colors group-hover:text-petrol-400 shrink-0 ${isCollapsed ? 'w-5 h-5' : ''}`} />
              {!isCollapsed && <span className="whitespace-nowrap animate-in fade-in duration-300">{n.label}</span>}
            </NavLink>
          ))}
        </nav>

        {!isCollapsed && (
          <div className="px-5 py-8 animate-in fade-in duration-500">
            <div className="rounded-3xl bg-white/5 p-4 border border-white/10">
              <div className="flex items-center gap-2 mb-2">
                <div className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">System Status</span>
              </div>
              <p className="text-[11px] leading-relaxed text-slate-500">
                Running in <span className="text-slate-300 font-medium font-mono">MOCK</span>.
              </p>
            </div>
          </div>
        )}

        {/* Floating Toggle Button centered on the edge - Black circle with White arrow */}
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className="absolute -right-3.5 top-1/2 -translate-y-1/2 z-50 h-7 w-7 grid place-items-center rounded-full bg-ink text-white border border-white/20 shadow-lift hover:scale-110 active:scale-95 transition-all cursor-pointer"
          title={isCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
        >
          <SidebarIcon className={`w-3.5 h-3.5 transition-transform duration-300 ${isCollapsed ? 'rotate-180' : ''}`} />
        </button>
      </aside>

      {/* Main Content */}
      <div className="flex min-w-0 flex-1 flex-col relative">
        <header className="sticky top-0 z-10 flex h-20 items-center justify-between px-10 glass-panel border-b-0 border-transparent">
          <div className="flex items-center gap-6">
            <Breadcrumb path={loc.pathname} />
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-xs font-bold text-ink">Admin</div>
              <div className="text-[10px] font-medium text-slate-400 uppercase tracking-widest">Administrator</div>
            </div>
            <div className="relative h-10 w-10 overflow-hidden rounded-2xl bg-petrol-100 ring-2 ring-white shadow-soft">
              <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Faizan" alt="User" className="h-full w-full object-cover" />
            </div>
          </div>
        </header>

        <main className="flex-1 px-10 py-10">
          <div className={`mx-auto ${isCollapsed ? 'max-w-[1400px]' : 'max-w-[1200px]'} animate-in fade-in slide-in-from-bottom-4 duration-500 transition-all`}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function Breadcrumb({ path }: { path: string }) {
  const navigate = useNavigate();
  const label = path === "/" ? "Dashboard" :
    path.includes("/inbox") ? "Email Inbox" :
      path.includes("/upload") ? "Direct Upload" :
        path.includes("/files") ? "File Archive" :
          path.includes("/employee-matcher") ? "Employee Matcher" :
            path.includes("/employee") ? "Employee Detailed Record" : "Page";

  return (
    <div className="flex items-center gap-3 text-sm font-medium">
      <button 
        onClick={() => navigate(-1)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-ink transition-all border border-slate-100"
      >
        <SidebarIcon className="w-3.5 h-3.5" />
        <span>Back</span>
      </button>
      <ChevronRight className="w-3 h-3 text-slate-300" />
      <span className="text-ink">{label}</span>
    </div>
  );
}

// Icons (reusing path data with refined styling)
function SidebarIcon({ className }: { className?: string }) {
  return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
}
function ChevronRight({ className }: { className?: string }) {
  return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6" /></svg>
}
function GridIcon({ className }: { className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>
}
function MailIcon({ className }: { className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></svg>
}
function UploadIcon({ className }: { className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5" /><path d="M12 3v12" /></svg>
}
function UsersIcon({ className }: { className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
}
function FolderIcon({ className }: { className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
}
