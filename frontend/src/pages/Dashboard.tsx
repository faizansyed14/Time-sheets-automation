import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchDashboard, type DashboardRow } from "../api/client";
import { Spinner, StatusDot } from "../components/ui";

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
      review: all.filter((r) => r.status === "yellow").length,
      pending: all.reduce((a, r) => a + r.pending_approval_count, 0),
    };
  }, [data]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Employee Dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">
            Monthly leave records pulled from email, extracted and matched to the employee matcher.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={year ?? ""}
            onChange={(e) => setYear(e.target.value ? Number(e.target.value) : undefined)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-ink shadow-panel focus:border-petrol-500 focus:outline-none"
          >
            <option value="">All years</option>
            {years.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </div>

      {/* stat cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Employees" value={stats.total} />
        <Stat label="Clear" value={stats.clear} tone="text-emerald-600" />
        <Stat label="Need review" value={stats.review} tone="text-amber-600" />
        <Stat label="Approvals pending" value={stats.pending} tone="text-petrol-600" />
      </div>

      {/* search */}
      <div className="relative max-w-sm">
        <SearchIcon />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name, ID, or manager…"
          className="w-full rounded-lg border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm shadow-panel focus:border-petrol-500 focus:outline-none"
        />
      </div>

      {isLoading ? (
        <Spinner />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-panel">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/80 text-xs uppercase tracking-wide text-slate-500">
                <th className="px-5 py-3 font-semibold">Status</th>
                <th className="px-5 py-3 font-semibold">Employee</th>
                <th className="px-5 py-3 font-semibold">ID</th>
                <th className="px-5 py-3 font-semibold">Account Manager</th>
                <th className="px-5 py-3 text-right font-semibold">Months</th>
                <th className="px-5 py-3 text-right font-semibold">Review</th>
                <th className="px-5 py-3 text-right font-semibold">Pending</th>
                <th className="px-5 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <DashRow key={(r.employee_pk ?? r.employee_name) ?? "x"} r={r} onOpen={() => open(nav, r)} />
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-5 py-10 text-center text-sm text-slate-400">
                    No records yet. Go to the Email Inbox and accept a timesheet to get started.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function open(nav: ReturnType<typeof useNavigate>, r: DashboardRow) {
  const pk = r.employee_pk ?? `unmatched::${(r.employee_name ?? "").toLowerCase()}`;
  nav(`/employee/${encodeURIComponent(pk)}`);
}

function DashRow({ r, onOpen }: { r: DashboardRow; onOpen: () => void }) {
  return (
    <tr className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
      <td className="px-5 py-3.5"><StatusDot status={r.status} /></td>
      <td className="px-5 py-3.5 font-medium text-ink">{r.employee_name ?? "Unknown"}</td>
      <td className="px-5 py-3.5 font-mono text-xs text-slate-500">{r.employee_id ?? "—"}</td>
      <td className="px-5 py-3.5 text-slate-600">{r.account_manager ?? "—"}</td>
      <td className="px-5 py-3.5 text-right font-mono tabular text-slate-700">{r.record_count}</td>
      <td className="px-5 py-3.5 text-right font-mono tabular">
        {r.needs_review_count > 0 ? <span className="text-amber-600">{r.needs_review_count}</span> : <span className="text-slate-300">0</span>}
      </td>
      <td className="px-5 py-3.5 text-right font-mono tabular">
        {r.pending_approval_count > 0 ? <span className="text-petrol-600">{r.pending_approval_count}</span> : <span className="text-slate-300">0</span>}
      </td>
      <td className="px-5 py-3.5 text-right">
        <button
          onClick={onOpen}
          title="View monthly details"
          className="inline-grid h-8 w-8 place-items-center rounded-lg border border-slate-200 text-slate-500 transition hover:border-petrol-300 hover:bg-petrol-50 hover:text-petrol-700"
        >
          <EyeIcon />
        </button>
      </td>
    </tr>
  );
}

function Stat({ label, value, tone = "text-ink" }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3.5 shadow-panel">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold tabular ${tone}`}>{value}</div>
    </div>
  );
}

function SearchIcon() {
  return (
    <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
    </svg>
  );
}
function EyeIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" />
    </svg>
  );
}
