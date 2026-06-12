import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Users,
  FileCheck2,
  AlertTriangle,
  XCircle,
  ChevronDown,
  ChevronRight,
  Eye,
  MapPin,
} from "lucide-react";
import {
  fetchDashboard,
  fetchEmployeeRecords,
  fetchPipelineStats,
  MONTHS_LONG,
  type DashboardRow,
} from "../api/client";
import { avatarColor, cn, initials } from "../lib/utils";
import { Badge, Card, EmptyState, PageHeader, Select, Skeleton } from "../components/ui";
import { ApprovalBadge, ValidationBadge } from "../components/status";

function Kpi({
  label,
  value,
  icon,
  tone,
  hint,
}: {
  label: string;
  value: number | string;
  icon: React.ReactNode;
  tone: string;
  hint?: string;
}) {
  return (
    <Card className="flex items-center gap-4 p-5">
      <div className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-xl", tone)}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-2xl font-bold leading-7 text-slate-900">{value}</p>
        <p className="truncate text-xs font-medium text-slate-500">{label}</p>
        {hint && <p className="truncate text-[11px] text-slate-400">{hint}</p>}
      </div>
    </Card>
  );
}

function EmployeeRecords({ pk, year }: { pk: string; year?: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["employee-records", pk, year],
    queryFn: () => fetchEmployeeRecords(pk, year),
  });
  if (isLoading) return <Skeleton className="m-4 h-16" />;
  if (!data?.length)
    return <p className="px-6 py-4 text-sm text-slate-500">No records for this selection.</p>;
  return (
    <div className="divide-y divide-slate-100">
      {data.map((r) => (
        <Link
          key={r.id}
          to={`/records/${r.id}`}
          className="flex items-center gap-4 px-6 py-3 transition-colors hover:bg-brand-50/50"
        >
          <p className="w-40 shrink-0 text-sm font-semibold text-slate-700">
            {MONTHS_LONG[r.month] ?? r.month} {r.year}
          </p>
          <div className="flex flex-1 flex-wrap items-center gap-1.5">
            <ValidationBadge status={r.validation_status} />
            <ApprovalBadge status={r.approval_status} />
            {r.source_file_count > 1 && (
              <Badge tone="violet">{r.source_file_count} files merged</Badge>
            )}
          </div>
          <p className="hidden max-w-md truncate text-xs text-slate-400 lg:block">{r.llm_summary}</p>
          <Eye className="h-4 w-4 shrink-0 text-slate-400" />
        </Link>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [year, setYear] = useState<number | undefined>(undefined);
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const { data: rows, isLoading } = useQuery({
    queryKey: ["dashboard", year],
    queryFn: () => fetchDashboard(year),
  });
  const { data: stats } = useQuery({ queryKey: ["pipeline-stats"], queryFn: fetchPipelineStats });

  const years = useMemo(() => {
    const ys = new Set<number>();
    rows?.forEach((r) => r.years.forEach((y) => ys.add(y)));
    return [...ys].sort();
  }, [rows]);

  const filtered = useMemo(
    () =>
      (rows ?? []).filter(
        (r) =>
          !q ||
          (r.employee_name ?? "").toLowerCase().includes(q.toLowerCase()) ||
          (r.employee_id ?? "").toLowerCase().includes(q.toLowerCase())
      ),
    [rows, q]
  );

  const yellow = (rows ?? []).filter((r) => r.status === "yellow").length;
  const totalRecords = (rows ?? []).reduce((a, r) => a + r.record_count, 0);

  const rowKey = (r: DashboardRow) =>
    r.employee_pk ?? `unmatched::${(r.employee_name ?? "Unknown").toLowerCase()}`;

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Dashboard"
        subtitle="Per-employee roll-up of extracted timesheets — green is clear, yellow needs your eyes."
        actions={
          <Select
            value={year ?? ""}
            onChange={(e) => setYear(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">All years</option>
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </Select>
        }
      />

      <div className="mb-6 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Kpi
          label="Employees with records"
          value={rows?.length ?? "—"}
          icon={<Users className="h-5 w-5 text-brand-600" />}
          tone="bg-brand-50"
        />
        <Kpi
          label="Monthly records"
          value={totalRecords}
          icon={<FileCheck2 className="h-5 w-5 text-emerald-600" />}
          tone="bg-emerald-50"
        />
        <Kpi
          label="Employees needing review"
          value={yellow}
          icon={<AlertTriangle className="h-5 w-5 text-amber-600" />}
          tone="bg-amber-50"
        />
        <Kpi
          label="Failed pipeline files"
          value={stats?.failed ?? "—"}
          icon={<XCircle className="h-5 w-5 text-rose-600" />}
          tone="bg-rose-50"
          hint={stats?.failed ? "Open the Pipeline page to resolve" : "All clear"}
        />
      </div>

      <Card>
        <div className="flex items-center justify-between gap-4 border-b border-slate-100 px-6 py-4">
          <h2 className="text-sm font-bold text-slate-800">Employees</h2>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name or ID…"
            className="w-64 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none"
          />
        </div>

        {isLoading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No employees yet"
            detail="Accept an email from the Inbox or upload a timesheet to see employees here."
          />
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((r) => {
              const key = rowKey(r);
              const expanded = open === key;
              return (
                <div key={key}>
                  <button
                    onClick={() => setOpen(expanded ? null : key)}
                    className="flex w-full items-center gap-4 px-6 py-3.5 text-left transition-colors hover:bg-slate-50"
                  >
                    {expanded ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
                    )}
                    <span
                      className={cn(
                        "flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                        avatarColor(r.employee_name)
                      )}
                    >
                      {initials(r.employee_name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold text-slate-800">
                          {r.employee_name ?? "Unknown"}
                        </span>
                        <span className="font-mono text-[11px] text-slate-400">{r.employee_id}</span>
                      </span>
                      <span className="flex items-center gap-1 text-xs text-slate-400">
                        {r.account_manager && (
                          <>
                            <MapPin className="h-3 w-3" /> {r.account_manager}
                          </>
                        )}
                      </span>
                    </span>
                    <span className="hidden gap-1.5 md:flex">
                      {r.needs_review_count > 0 && (
                        <Badge tone="amber">{r.needs_review_count} to review</Badge>
                      )}
                      {r.pending_approval_count > 0 && (
                        <Badge tone="slate">{r.pending_approval_count} unapproved</Badge>
                      )}
                      <Badge tone="indigo">{r.record_count} month{r.record_count !== 1 && "s"}</Badge>
                    </span>
                    <span
                      className={cn(
                        "h-2.5 w-2.5 shrink-0 rounded-full",
                        r.status === "green" ? "bg-emerald-500" : "bg-amber-400"
                      )}
                      title={r.status === "green" ? "All clear" : "Needs attention"}
                    />
                  </button>
                  {expanded && (
                    <div className="border-t border-slate-100 bg-slate-50/60">
                      <EmployeeRecords pk={key} year={year} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
