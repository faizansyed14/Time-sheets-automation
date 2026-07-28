import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  Users,
  CalendarCheck,
  CalendarX,
  AlertTriangle,
  Search,
  CheckCircle2,
  Clock,
  Eye,
  Check,
} from "lucide-react";
import {
  fetchCoverage,
  fetchEmployeeRecords,
  fetchPipelineStats,
  MONTHS,
  MONTHS_LONG,
  type CoverageStatus,
  type DashboardRow,
  type TimesheetRecord,
} from "../api/client";
import { locationBadgeTone, type BadgeTone } from "../lib/theme";
import { avatarColor, cn, initials } from "../lib/utils";
import { Badge, Card, EmptyState, Input, Select, Skeleton, Spinner } from "../components/ui";
import { ApprovalBadge, ValidationBadge } from "../components/status";
import { useDebounced, useSentinel } from "../lib/useInfinite";

const NOW = new Date();
const CUR_YEAR = NOW.getFullYear();
const CUR_MONTH = NOW.getMonth() + 1;

type QuickFilter = "all" | CoverageStatus;

function StatCard({
  label,
  value,
  icon,
  accent = "brand",
}: {
  label: string;
  value: number | string;
  icon: React.ReactNode;
  accent?: "brand" | "success" | "danger" | "slate";
}) {
  const accents = {
    brand: "border-slate-200/80 bg-white",
    success: "border-emerald-200/60 bg-emerald-50/40",
    danger: "border-rose-200/60 bg-rose-50/40",
    slate: "border-slate-200/80 bg-slate-50/50",
  };
  const iconBg = {
    brand: "bg-brand-50 text-brand-600 ring-brand-100",
    success: "bg-emerald-50 text-emerald-600 ring-emerald-100",
    danger: "bg-rose-50 text-rose-600 ring-rose-100",
    slate: "bg-slate-100 text-slate-600 ring-slate-200",
  };
  return (
    <div className={cn("flex items-center gap-3.5 rounded-xl border p-4 shadow-card", accents[accent])}>
      <div className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset", iconBg[accent])}>
        {icon}
      </div>
      <div>
        <p className="text-2xl font-bold leading-none tracking-tight text-slate-900">{value}</p>
        <p className="mt-1 text-xs font-medium text-slate-500">{label}</p>
      </div>
    </div>
  );
}

function monthStatus(row: DashboardRow, month: number, year: number) {
  const future = year > CUR_YEAR || (year === CUR_YEAR && month > CUR_MONTH);
  const submitted = row.submitted_months.includes(month);
  if (submitted) return { label: "Submitted", tone: "success" as const };
  if (future) return { label: "Not due", tone: "slate" as const };
  if (row.in_matcher) return { label: "Missing", tone: "danger" as const };
  return { label: "Unmatched", tone: "warning" as const };
}

function MonthStrip({
  submitted,
  year,
  focusMonth,
}: {
  submitted: number[];
  year: number;
  focusMonth: number;
}) {
  return (
    <div className="flex gap-px">
      {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => {
        const done = submitted.includes(m);
        const future = year > CUR_YEAR || (year === CUR_YEAR && m > CUR_MONTH);
        const isFocus = m === focusMonth;
        const state = done ? "submitted" : future ? "not due yet" : "missing";
        return (
          <span
            key={m}
            title={`${MONTHS_LONG[m]} ${year}: ${state}`}
            className={cn(
              "flex h-4 w-4 items-center justify-center rounded-sm text-[8px] font-bold transition-colors",
              done
                ? "bg-emerald-500 text-white"
                : future
                  ? "bg-slate-50 text-slate-300"
                  : isFocus
                    ? "bg-rose-100 text-rose-600 ring-1 ring-rose-300"
                    : "bg-slate-100 text-slate-400",
              isFocus && !done && !future && "ring-1 ring-rose-300"
            )}
          >
            {done ? <Check className="h-2.5 w-2.5" /> : MONTHS[m][0]}
          </span>
        );
      })}
    </div>
  );
}

function resolveFocusRecord(
  records: TimesheetRecord[] | undefined,
  opts: { recordId?: string | null; month: number; year: number }
) {
  if (opts.recordId) {
    const byId = records?.find((r) => r.id === opts.recordId);
    if (byId) return byId;
  }
  return records?.find((r) => r.month === opts.month && r.year === opts.year) ?? null;
}

function ReviewStatCard({ count }: { count: number }) {
  return (
    <Link
      to="/pipeline?status=needs_review"
      title="Open the Activity log to accept or fix extracted timesheets."
      className="group flex items-center gap-3 rounded-xl border border-amber-200/80 bg-amber-50/30 p-4 shadow-card transition hover:border-amber-300 hover:bg-amber-50/50"
    >
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/80 ring-1 ring-black/[0.04]">
        <AlertTriangle className="h-5 w-5 text-amber-600" />
      </div>
      <div>
        <p className="text-2xl font-bold leading-none text-slate-900">{count}</p>
        <p className="mt-1 text-xs font-medium text-slate-500">Waiting for review</p>
      </div>
    </Link>
  );
}

function ViewRecordButton({
  pk,
  year,
  month,
  recordId,
  needsReview,
}: {
  pk: string;
  year: number;
  month: number;
  recordId: string | null;
  needsReview: boolean;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["employee-records", pk, year],
    queryFn: () => fetchEmployeeRecords(pk, year),
    enabled: !!pk,
  });
  const rec = resolveFocusRecord(data, { recordId, month, year });
  if (isLoading && !recordId) {
    return <span className="text-xs text-slate-400">…</span>;
  }
  if (!rec) return null;
  return (
    <Link
      to={`/records/${rec.id}`}
      title="View record"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-md px-1.5 py-1 text-[11px] font-semibold text-white shadow-sm",
        needsReview ? "bg-amber-500 hover:bg-amber-600" : "bg-brand-600 hover:bg-brand-700"
      )}
    >
      <Eye className="h-3 w-3" />
      View
    </Link>
  );
}

export default function Dashboard() {
  const [year, setYear] = useState(CUR_YEAR);
  const [month, setMonth] = useState(CUR_MONTH);
  const [q, setQ] = useState("");
  const [loc, setLoc] = useState("");
  const [quickFilter, setQuickFilter] = useState<QuickFilter>("all");

  const statusFilter: CoverageStatus | "" =
    quickFilter === "all" ? "" : quickFilter;
  const onlyMissing = quickFilter === "missing";

  const dq = useDebounced(q, 300);
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["coverage", year, month, dq, loc, statusFilter, onlyMissing],
    queryFn: ({ pageParam }) =>
      fetchCoverage({
        year,
        month,
        q: dq || undefined,
        location: loc || undefined,
        status: statusFilter || undefined,
        only_missing: onlyMissing,
        offset: pageParam as number,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.rows.length : undefined),
  });
  const { data: stats } = useQuery({ queryKey: ["pipeline-stats"], queryFn: fetchPipelineStats });

  const cov = data?.pages[0];
  const rows = data?.pages.flatMap((p) => p.rows) ?? [];
  const filteredTotal = cov?.filtered_total ?? rows.length;
  const reviewCount = (stats?.needs_review ?? 0) + (stats?.failed ?? 0);

  const years = useMemo(() => {
    const ys = new Set<number>([CUR_YEAR, CUR_YEAR - 1, CUR_YEAR - 2, year]);
    rows.forEach((r) => r.years.forEach((y) => ys.add(y)));
    return [...ys].sort((a, b) => b - a);
  }, [rows, year]);

  const rowKey = (r: DashboardRow) =>
    r.employee_pk ?? `unmatched::${(r.employee_name ?? "Unknown").toLowerCase()}`;

  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage
  );

  const focusFuture = year > CUR_YEAR || (year === CUR_YEAR && month > CUR_MONTH);
  const periodLabel = `${MONTHS_LONG[month]} ${year}`;

  const chips: { id: QuickFilter; label: string }[] = [
    { id: "all", label: "All" },
    { id: "submitted", label: "Submitted" },
    { id: "missing", label: "Missing" },
    { id: "needs_review", label: "Needs review" },
  ];

  return (
    <div className="animate-fade-up space-y-5">
      <div
        className={cn(
          "grid gap-3",
          reviewCount > 0 ? "sm:grid-cols-2 lg:grid-cols-4" : "sm:grid-cols-3"
        )}
      >
          <StatCard
            label="Employees in matcher"
            value={cov?.total_employees ?? "—"}
            icon={<Users className="h-5 w-5" />}
            accent="brand"
          />
          <StatCard
            label={`Submitted · ${MONTHS[month]}`}
            value={cov?.submitted_this_month ?? "—"}
            icon={<CalendarCheck className="h-5 w-5" />}
            accent="success"
          />
          <StatCard
            label={focusFuture ? `Missing · ${MONTHS[month]} (not due)` : `Missing · ${MONTHS[month]}`}
            value={focusFuture ? "—" : cov?.missing_this_month ?? "—"}
            icon={<CalendarX className="h-5 w-5" />}
            accent="danger"
          />
          {reviewCount > 0 && <ReviewStatCard count={reviewCount} />}
        </div>

      <Card className="overflow-hidden p-0">
        <div className="flex flex-col gap-3 border-b border-slate-100 bg-slate-50/40 px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs font-bold uppercase tracking-wide text-slate-400">Period</span>
            <Select
              value={month}
              onChange={(e) => setMonth(Number(e.target.value))}
              className="min-w-[140px] font-semibold"
            >
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                <option key={m} value={m}>{MONTHS_LONG[m]}</option>
              ))}
            </Select>
            <Select
              value={year}
              onChange={(e) => setYear(Number(e.target.value))}
              className="min-w-[100px] font-semibold"
            >
              {years.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </Select>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap gap-1.5">
              {chips.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => setQuickFilter(c.id)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors",
                  quickFilter === c.id
                    ? "bg-brand-600 text-white shadow-sm"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                )}
              >
                {c.label}
              </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex rounded-lg border border-slate-200 p-0.5">
                {(["", "DXB", "AUH"] as const).map((l) => (
                <button
                  key={l || "all"}
                  type="button"
                  onClick={() => setLoc(l)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-xs font-semibold transition-colors",
                    loc === l ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
                  )}
                >
                  {l || "All"}
                </button>
                ))}
              </div>
              <div className="relative min-w-[200px] flex-1 sm:w-56 sm:flex-none">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <Input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search name or ID…"
                  className="pl-9 py-1.5 text-sm"
                />
              </div>
            </div>
          </div>
        </div>

        {isLoading ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-11" />
            <Skeleton className="h-11" />
            <Skeleton className="h-11" />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title={quickFilter === "missing" ? "Nobody missing" : "No employees found"}
            detail={
              quickFilter === "missing"
                ? `Everyone submitted for ${periodLabel}.`
                : "Try a different search or filter."
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-left text-xs">
              <colgroup>
                <col className="w-[28%]" />
                <col className="hidden w-[10%] sm:table-column" />
                <col className="hidden w-[7%] md:table-column" />
                <col className="hidden w-[12%] lg:table-column" />
                <col className="w-[14%]" />
                <col className="w-[18%]" />
                <col className="w-[8%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/80 text-[10px] font-bold uppercase tracking-wide text-slate-400">
                  <th className="px-2.5 py-2">Employee</th>
                  <th className="hidden px-2 py-2 sm:table-cell">ID</th>
                  <th className="hidden px-2 py-2 md:table-cell">Loc</th>
                  <th className="hidden px-2 py-2 lg:table-cell">Manager</th>
                  <th className="px-2 py-2">{MONTHS[month]} {year}</th>
                  <th className="px-2 py-2">{year}</th>
                  <th className="px-2 py-2 text-right">View</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((r) => {
                  const key = rowKey(r);
                  const status = monthStatus(r, month, year);
                  const hasSubmittedMonth = r.submitted_months.includes(month);
                  const needsReview =
                    r.focus_validation_status === "manual_review" ||
                    r.needs_review_count > 0;
                  const showView = !!r.employee_pk && (hasSubmittedMonth || !!r.focus_record_id);
                  return (
                    <tr key={key} className="group hover:bg-slate-50/80">
                      <td className="px-2.5 py-1.5">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <span
                            className={cn(
                              "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[9px] font-bold",
                              avatarColor(r.employee_name)
                            )}
                          >
                            {initials(r.employee_name)}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p
                              className="truncate text-[11px] font-semibold leading-tight text-slate-800"
                              title={r.employee_name ?? "Unknown"}
                            >
                              {r.employee_name ?? "Unknown"}
                            </p>
                            <p className="truncate text-[10px] text-slate-400 sm:hidden">{r.employee_id}</p>
                          </div>
                        </div>
                      </td>
                      <td className="hidden truncate px-2 py-1.5 font-mono text-[10px] text-slate-500 sm:table-cell">
                        {r.employee_id ?? "—"}
                      </td>
                      <td className="hidden px-2 py-1.5 md:table-cell">
                        {r.location ? (
                          <Badge tone={locationBadgeTone(r.location)} className="px-1.5 py-0 text-[10px]">
                            {r.location}
                          </Badge>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                      <td
                        className="hidden truncate px-2 py-1.5 text-[11px] text-slate-600 lg:table-cell"
                        title={r.account_manager ?? undefined}
                      >
                        {r.account_manager ?? "—"}
                      </td>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-wrap items-center gap-1">
                          <Badge tone={status.tone as BadgeTone} className="px-1.5 py-0 text-[10px]">
                            {status.tone === "success" && <CheckCircle2 className="h-2.5 w-2.5" />}
                            {status.tone === "danger" && <CalendarX className="h-2.5 w-2.5" />}
                            {status.tone === "slate" && <Clock className="h-2.5 w-2.5" />}
                            {status.label}
                          </Badge>
                          {hasSubmittedMonth && r.focus_validation_status && (
                            <ValidationBadge status={r.focus_validation_status} />
                          )}
                          {hasSubmittedMonth && r.focus_approval_status && (
                            <ApprovalBadge status={r.focus_approval_status} />
                          )}
                        </div>
                      </td>
                      <td className="px-2 py-1.5">
                        <MonthStrip
                          submitted={r.submitted_months}
                          year={year}
                          focusMonth={month}
                        />
                      </td>
                      <td className="px-2 py-1.5 text-right">
                        {showView ? (
                          <ViewRecordButton
                            pk={r.employee_pk!}
                            year={year}
                            month={month}
                            recordId={r.focus_record_id}
                            needsReview={needsReview}
                          />
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div ref={sentinelRef} />
            {isFetchingNextPage && (
              <div className="flex items-center justify-center gap-2 py-4 text-xs text-slate-400">
                <Spinner className="h-4 w-4" /> Loading more…
              </div>
            )}
            <p className="border-t border-slate-100 px-4 py-2 text-center text-[11px] text-slate-400">
              {rows.length} of {filteredTotal} employees
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
