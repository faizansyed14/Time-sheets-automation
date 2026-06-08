import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchDashboard, type DashboardRow } from "../api/client";
import { Spinner, StatusDot, Badge } from "../components/ui";

export default function Dashboard() {
  const nav = useNavigate();
  const [year, setYear] = useState<number | undefined>(undefined);
  const [q, setQ] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["dashboard", year],
    queryFn: () => fetchDashboard(year),
  });

  const years = useMemo(() => {
    const s = new Set<number>();
    (data ?? []).forEach((r) => r.years.forEach((y) => s.add(y)));
    return Array.from(s).sort((a, b) => b - a);
  }, [data]);

  const rows = useMemo(() => {
    let r = data ?? [];
    if (q.trim()) {
      const t = q.toLowerCase();
      r = r.filter(
        (x) =>
          (x.employee_name ?? "").toLowerCase().includes(t) ||
          (x.employee_id ?? "").toLowerCase().includes(t) ||
          (x.account_manager ?? "").toLowerCase().includes(t)
      );
    }
    return r;
  }, [data, q]);

  const stats = useMemo(() => {
    const all = data ?? [];
    return {
      total: all.length,
      clear: all.filter((r) => r.status === "green").length,
      review: all.filter((r) => r.needs_review_count > 0).length,
      pending: all.filter((r) => r.pending_approval_count > 0).length,
    };
  }, [data]);

  const [statusFilter, setStatusFilter] = useState("all");

  const filteredRows = useMemo(() => {
    let r = rows;
    if (statusFilter === "verified") r = r.filter(x => x.status === "green");
    if (statusFilter === "review") r = r.filter(x => x.needs_review_count > 0);
    if (statusFilter === "pending") r = r.filter(x => x.pending_approval_count > 0);
    return r;
  }, [rows, statusFilter]);

  return (
    <div className="space-y-10">
      <header className="flex flex-wrap items-center justify-between gap-6">
        <div>
          <h1 className="text-4xl font-bold tracking-tight text-ink">Dashboard</h1>
          <p className="mt-2 text-slate-500 font-medium">
            Monitoring monthly timesheet extraction and validation status.
          </p>
        </div>
        <div className="flex items-center gap-3 bg-white p-1.5 rounded-2xl border border-slate-200 shadow-sm">
          <span className="pl-3 text-xs font-bold text-slate-400 uppercase tracking-wider">Fiscal Year</span>
          <select
            value={year ?? ""}
            onChange={(e) => setYear(e.target.value ? Number(e.target.value) : undefined)}
            className="rounded-xl border-none bg-slate-50 px-4 py-2 text-sm font-bold text-ink focus:ring-2 focus:ring-petrol-500/20 outline-none cursor-pointer"
          >
            <option value="">All years</option>
            {years.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </header>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Total Employees" value={stats.total} icon={<UsersIcon />} />
        <StatCard label="Clear Status" value={stats.clear} tone="emerald" icon={<CheckIcon />} />
        <StatCard label="Needs Review" value={stats.review} tone="amber" icon={<AlertIcon />} />
        <StatCard label="Pending Approval" value={stats.pending} tone="rose" icon={<ExclamationIcon />} />
      </div>

      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="relative w-full max-w-md group">
            <div className="absolute inset-y-0 left-0 flex items-center pl-4 pointer-events-none">
              <SearchIcon className="w-4 h-4 text-slate-400 group-focus-within:text-petrol-500 transition-colors" />
            </div>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter by name, ID or manager..."
              className="w-full rounded-2xl border border-slate-200 bg-white py-3 pl-11 pr-4 text-sm font-medium shadow-sm transition-all focus:border-petrol-300 focus:ring-4 focus:ring-petrol-500/5 outline-none"
            />
          </div>
          <div className="flex items-center gap-3 bg-white p-1.5 rounded-2xl border border-slate-200 shadow-sm">
            <span className="pl-3 text-[10px] font-black text-slate-400 uppercase tracking-widest">Show Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-xl border-none bg-slate-50 px-4 py-2 text-xs font-bold text-ink focus:ring-2 focus:ring-petrol-500/20 outline-none cursor-pointer"
            >
              <option value="all">All Records</option>
              <option value="verified">Verified (Green)</option>
              <option value="review">Needs Review</option>
              <option value="pending">Approval Pending</option>
            </select>
          </div>
          <Badge tone="slate">{filteredRows.length} employees</Badge>
        </div>

        {isLoading ? (
          <Spinner />
        ) : (
          <div className="overflow-hidden rounded-3xl border border-slate-200/60 bg-white shadow-soft">
            <table className="w-full text-left text-sm border-separate border-spacing-0">
              <thead>
                <tr className="bg-slate-50/50 text-[11px] font-bold uppercase tracking-[0.1em] text-slate-500">
                  <th className="px-8 py-5 border-b border-slate-100">Status</th>
                  <th className="px-8 py-5 border-b border-slate-100">Employee Details</th>
                  <th className="px-8 py-5 border-b border-slate-100">Account Manager</th>
                  <th className="px-8 py-5 text-right border-b border-slate-100">Activity</th>
                  <th className="px-8 py-5 border-b border-slate-100"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {filteredRows.map((r) => (
                  <tr key={(r.employee_pk ?? r.employee_name) ?? "x"} className="group hover:bg-slate-50/50 transition-colors">
                    <td className="px-8 py-5">
                      <StatusDot 
                        status={
                          r.pending_approval_count > 0 ? "rose" :
                          r.needs_review_count > 0 ? "yellow" :
                          "green"
                        } 
                        labelOverride={
                          r.pending_approval_count > 0 ? "Pending Approval" :
                          r.needs_review_count > 0 ? "Needs Review" :
                          "Verified"
                        }
                      />
                    </td>
                    <td className="px-8 py-5">
                      <div className="flex flex-col">
                        <span className="font-bold text-ink group-hover:text-petrol-600 transition-colors">
                          {r.employee_name ?? "Unknown"}
                        </span>
                        <span className="text-xs font-medium text-slate-400 font-mono mt-0.5 uppercase tracking-wider">
                          {r.employee_id ?? "No matching ID"}
                        </span>
                      </div>
                    </td>
                    <td className="px-8 py-5">
                      <span className="inline-flex items-center gap-2 text-slate-600 font-medium">
                        <div className="w-1.5 h-1.5 rounded-full bg-slate-300" />
                        {r.account_manager ?? "Unassigned"}
                      </span>
                    </td>
                    <td className="px-8 py-5 text-right">
                      <div className="flex items-center justify-end gap-6 font-mono text-xs font-bold">
                        <div className="flex flex-col items-end">
                          <span className="text-slate-400 text-[10px] uppercase tracking-widest mb-1 font-sans">Months</span>
                          <span className="text-ink">{r.record_count}</span>
                        </div>
                        <div className="flex flex-col items-end">
                          <span className="text-slate-400 text-[10px] uppercase tracking-widest mb-1 font-sans">Issues</span>
                          <span className={r.needs_review_count > 0 ? "text-amber-600" : "text-slate-300"}>
                            {r.needs_review_count}
                          </span>
                        </div>
                   </div>
                    </td>
                    <td className="px-8 py-5 text-right">
                      <button
                        onClick={() => open(nav, r)}
                        className="h-10 w-10 inline-grid place-items-center rounded-xl bg-slate-50 text-slate-400 hover:bg-petrol-600 hover:text-white transition-all duration-200 active:scale-90"
                      >
                        <ArrowIcon className="w-5 h-5" />
                      </button>
                    </td>
                  </tr>
                ))}
                {filteredRows.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-8 py-20 text-center">
                      <div className="flex flex-col items-center">
                        <div className="h-12 w-12 rounded-full bg-slate-50 flex items-center justify-center mb-4">
                          <SearchIcon className="w-6 h-6 text-slate-300" />
                        </div>
                        <p className="text-sm font-bold text-slate-400">No records found matching your criteria.</p>
                        <p className="text-xs text-slate-300 mt-1 uppercase tracking-widest">Try adjusting your filters</p>
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function open(nav: any, r: DashboardRow) {
  const pk = r.employee_pk ?? `unmatched::${(r.employee_name ?? "").toLowerCase()}`;
  nav(`/employee/${encodeURIComponent(pk)}`);
}

function StatCard({ label, value, tone = "slate", icon }: { label: string; value: number; tone?: string; icon: any }) {
  const tones: any = {
    slate: "text-ink bg-slate-50",
    emerald: "text-emerald-600 bg-emerald-50",
    amber: "text-amber-600 bg-amber-50",
    petrol: "text-petrol-600 bg-petrol-50",
    rose: "text-rose-600 bg-rose-50",
  };
  return (
    <div className={`premium-card p-6 flex flex-col gap-4`}>
       <div className="flex items-center justify-between">
         <span className="text-[11px] font-bold text-slate-400 uppercase tracking-[0.15em]">{label}</span>
         <div className={`h-10 w-10 flex items-center justify-center rounded-xl ${tones[tone].split(' ')[1]}`}>
           {icon}
         </div>
       </div>
       <div className={`text-4xl font-bold tracking-tight ${tones[tone].split(' ')[0]}`}>{value}</div>
    </div>
  );
}

// Icons
function UsersIcon() { return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg> }
function CheckIcon() { return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6 9 17l-5-5"/></svg> }
function AlertIcon() { return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> }
function ClockIcon() { return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> }
function ExclamationIcon() { return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M12 8v4"/><path d="M12 16h.01"/><circle cx="12" cy="12" r="10"/></svg> }
function SearchIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg> }
function ArrowIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="m9 18 6-6-6-6"/></svg> }
