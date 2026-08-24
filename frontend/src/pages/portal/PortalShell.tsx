import { type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { LogOut, FolderLock } from "lucide-react";
import { usePortalAuth } from "../../lib/portalAuth";
import { cn } from "../../lib/utils";

const navLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
    isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100"
  );

export default function PortalShell({ children }: { children: ReactNode }) {
  const { user, logout } = usePortalAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <FolderLock className="h-5 w-5 text-brand-600" />
            <span className="font-bold text-slate-900">Timesheet Portal</span>
          </div>
          <nav className="flex items-center gap-1">
            <NavLink to="/portal/employee" end className={navLink}>
              Submit
            </NavLink>
            <NavLink to="/portal/employee/history" className={navLink}>
              My submissions
            </NavLink>
          </nav>
          <div className="flex items-center gap-3">
            <span className="text-sm text-slate-500">
              {user?.employee_name || user?.username}
            </span>
            <button
              onClick={() => {
                logout();
                navigate("/portal/login", { replace: true });
              }}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-slate-500 hover:bg-slate-100"
            >
              <LogOut className="h-4 w-4" />
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-8">{children}</main>
    </div>
  );
}
