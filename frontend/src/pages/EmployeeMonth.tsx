import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchEmployeeRecords } from "../api/client";
import RecordDetail from "../components/RecordDetail";
import { Spinner } from "../components/ui";

export default function EmployeeMonth() {
  const { pk = "" } = useParams();
  const nav = useNavigate();
  const [year, setYear] = useState<number | undefined>(undefined);

  const { data, isLoading } = useQuery({
    queryKey: ["employee-records", pk],
    queryFn: () => fetchEmployeeRecords(decodeURIComponent(pk)),
  });

  const years = useMemo(
    () => Array.from(new Set((data ?? []).map((r) => r.year))).sort((a, b) => b - a),
    [data]
  );
  const records = useMemo(
    () => (data ?? []).filter((r) => year === undefined || r.year === year)
      .sort((a, b) => b.year - a.year || b.month - a.month),
    [data, year]
  );

  const emp = data?.[0];

  return (
    <div className="space-y-6">
      <button
        onClick={() => nav("/")}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-ink"
      >
        <BackIcon /> Back to dashboard
      </button>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-petrol-100 font-mono text-lg font-semibold text-petrol-700">
            {(emp?.employee_name ?? "E").split(" ").slice(0, 2).map((s) => s[0]).join("").toUpperCase()}
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">{emp?.employee_name ?? "Employee"}</h1>
            <div className="mt-0.5 flex flex-wrap gap-x-4 text-sm text-slate-500">
              <span className="font-mono">{emp?.employee_id ?? "—"}</span>
              <span>DCO: {emp?.dco_number ?? "—"}</span>
              <span>Mgr: {emp?.account_manager ?? "—"}</span>
            </div>
          </div>
        </div>
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

      {isLoading ? (
        <Spinner />
      ) : records.length === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white px-5 py-10 text-center text-sm text-slate-400 shadow-panel">
          No records for this selection.
        </div>
      ) : (
        <div className="space-y-5">
          {records.map((r) => (
            <RecordDetail key={r.id} rec={r} />
          ))}
        </div>
      )}
    </div>
  );
}

function BackIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}
