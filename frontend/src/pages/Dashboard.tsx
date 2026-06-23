import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  Users,
  CalendarCheck,
  CalendarX,
  AlertTriangle,
  XCircle,
  ChevronDown,
  ChevronRight,
  Eye,
  MapPin,
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
} from "../api/client";
import { avatarColor, cn, initials } from "../lib/utils";
import { Badge, Card, EmptyState, PageHeader, Select, Skeleton, Spinner } from "../components/ui";
import { ApprovalBadge, ValidationBadge } from "../components/status";
import { useDebounced, useSentinel } from "../lib/useInfinite";

const NOW = new Date();
const CUR_YEAR = NOW.getFullYear();
const CUR_MONTH = NOW.getMonth() + 1;

function Kpi({
  label,
  value,
  icon,
  tone,
  hint,
  accent,
}: {
  label: string;
  value: number | string;
  icon: React.ReactNode;
  tone: string;
  hint?: string;
  accent?: string;
}) {
  return (
    <Card className={cn("flex items-center gap-3.5 p-4", accent)}>
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

/** 12-month submission strip for one employee in the focus year. */
function CoverageStrip({
  submitted,
  year,
  focusMonth,
}: {
  submitted: number[];
  year: number;
  focusMonth: number;
}) {
  return (
    <div className="flex gap-[3px]">
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
              "flex h-5 w-5 items-center justify-center rounded text-[9px] font-bold transition-colors",
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
            {done ? <Check className="h-3 w-3" /> : MONTHS[m][0]}
          </span>
        );
      })}
    </div>
  );
}

function EmployeeRecords({ pk, year }: { pk: string; year: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["employee-records", pk, year],
    queryFn: () => fetchEmployeeRecords(pk, year),
  });
  if (isLoading) return <Skeleton className="m-4 h-16" />;
  if (!data?.length)
    return (
      <p className="px-6 py-4 text-sm text-slate-500">
        No timesheet records for {year}. This employee is showing as missing for every month.
      </p>
    );
  return (
    <div className="divide-y divide-slate-100">
      {data.map((r) => (
        <Link
          key={r.id}
          to={`/records/${r.id}`}
          className="flex items-center gap-4 px-6 py-3 transition-colors hover:bg-brand-50/50"
        >
          <p className="w-36 shrink-0 text-sm font-semibold text-slate-700">
            {MONTHS_LONG[r.month] ?? r.month} {r.year}
          </p>
          <div className="flex flex-1 flex-wrap items-center gap-1.5">
            <ValidationBadge status={r.validation_status} />
            <ApprovalBadge status={r.approval_status} />
            {r.source_file_count > 1 && <Badge tone="violet">{r.source_file_count} files merged</Badge>}
          </div>
          <p className="hidden max-w-md truncate text-xs text-slate-400 lg:block">{r.llm_summary}</p>
          <Eye className="h-4 w-4 shrink-0 text-slate-400" />
        </Link>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [year, setYear] = useState<number>(CUR_YEAR);
  const [month, setMonth] = useState<number>(CUR_MONTH);
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [loc, setLoc] = useState("");
  const [statusFilter, setStatusFilter] = useState<CoverageStatus | "">("");
  const [onlyMissing, setOnlyMissing] = useState(false);

  const dq = useDebounced(q, 350);
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

  // Headline counts come from the first page (computed globally on the server);
  // rows accumulate across pages as you scroll.
  const cov = data?.pages[0];
  const filtered = data?.pages.flatMap((p) => p.rows) ?? [];
  const filteredTotal = cov?.filtered_total ?? filtered.length;

  const years = useMemo(() => {
    const ys = new Set<number>([CUR_YEAR, CUR_YEAR - 1, CUR_YEAR - 2, year]);
    filtered.forEach((r) => r.years.forEach((y) => ys.add(y)));
    return [...ys].sort((a, b) => b - a);
  }, [filtered, year]);

  const isMissing = (r: DashboardRow) =>
    r.in_matcher && !r.submitted_months.includes(month) && !(year > CUR_YEAR || (year === CUR_YEAR && month > CUR_MONTH));

  const rowKey = (r: DashboardRow) =>
    r.employee_pk ?? `unmatched::${(r.employee_name ?? "Unknown").toLowerCase()}`;

  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage
  );

  const focusFuture = year > CUR_YEAR || (year === CUR_YEAR && month > CUR_MONTH);

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Dashboard"
        subtitle="Who has submitted, who is missing, and what still needs review — at a glance."
        actions={
          <div className="flex items-center gap-2">
            <Select value={month} onChange={(e) => setMonth(Number(e.target.value))}>
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                <option key={m} value={m}>
                  {MONTHS_LONG[m]}
                </option>
              ))}
            </Select>
            <Select value={year} onChange={(e) => setYear(Number(e.target.value))}>
              {years.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </Select>
          </div>
        }
      />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Kpi
          label="Employees"
          value={cov?.total_employees ?? "—"}
          icon={<Users className="h-5 w-5 text-brand-600" />}
          tone="bg-brand-50"
          hint="in the matcher list"
        />
        <Kpi
          label={`Submitted · ${MONTHS[month]}`}
          value={cov?.submitted_this_month ?? "—"}
          icon={<CalendarCheck className="h-5 w-5 text-emerald-600" />}
          tone="bg-emerald-50"
        />
        <button onClick={() => setOnlyMissing((v) => !v)} className="text-left">
          <Kpi
            label={`Missing · ${MONTHS[month]} ${year}`}
            value={focusFuture ? "—" : cov?.missing_this_month ?? "—"}
            icon={<CalendarX className="h-5 w-5 text-rose-600" />}
            tone="bg-rose-50"
            accent={onlyMissing ? "ring-2 ring-rose-300" : ""}
            hint={focusFuture ? "month not due yet" : onlyMissing ? "filtering · click to clear" : "click to filter"}
          />
        </button>
        <Kpi
          label="Need review"
          value={cov?.needs_review ?? "—"}
          icon={<AlertTriangle className="h-5 w-5 text-amber-600" />}
          tone="bg-amber-50"
          hint="employees with flags"
        />
        <Kpi
          label="Failed pipeline"
          value={stats?.failed ?? "—"}
          icon={<XCircle className="h-5 w-5 text-rose-600" />}
          tone="bg-rose-50"
          hint={stats?.failed ? "open Pipeline to resolve" : "all clear"}
        />
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3.5">
          <h2 className="text-sm font-bold text-slate-800">Employees</h2>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <label className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-slate-600">
              <input
                type="checkbox"
                checked={onlyMissing}
                onChange={(e) => setOnlyMissing(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-slate-300 text-rose-600 focus:ring-rose-400"
              />
              Only missing {MONTHS[month]}
            </label>
            <Select value={loc} onChange={(e) => setLoc(e.target.value)} className="py-1.5 text-xs">
              <option value="">All locations</option>
              <option value="DXB">DXB</option>
              <option value="AUH">AUH</option>
            </Select>
            <Select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as CoverageStatus | "")}
              className="py-1.5 text-xs"
              title={`Status for ${MONTHS_LONG[month]} ${year}`}
            >
              <option value="">All statuses</option>
              <option value="submitted">Submitted</option>
              <option value="missing">Missing</option>
              <option value="needs_review">Needs review</option>
              <option value="approved">Approved</option>
              <option value="not_approved">Not approved</option>
              <option value="pending_approval">Pending approval</option>
            </Select>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search name or ID…"
              className="w-56 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none"
            />
          </div>
        </div>

        <div className="flex items-center gap-4 border-b border-slate-100 bg-slate-50/60 px-6 py-2 text-[11px] font-medium text-slate-400">
          <span className="flex items-center gap-1">
            <span className="flex h-3.5 w-3.5 items-center justify-center rounded bg-emerald-500" />
            submitted
          </span>
          <span className="flex items-center gap-1">
            <span className="h-3.5 w-3.5 rounded bg-rose-100 ring-1 ring-rose-300" /> missing
          </span>
          <span className="flex items-center gap-1">
            <span className="h-3.5 w-3.5 rounded bg-slate-50" /> not due yet
          </span>
          <span className="ml-auto hidden md:block">Jan → Dec {year}</span>
        </div>

        {isLoading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title={onlyMissing ? "Nobody is missing 🎉" : "No employees"}
            detail={
              onlyMissing
                ? `Everyone submitted their ${MONTHS_LONG[month]} ${year} timesheet.`
                : "Import your employee matcher list, then accept emails or upload timesheets."
            }
          />
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((r) => {
              const key = rowKey(r);
              const expanded = open === key;
              const missing = isMissing(r);
              return (
                <div key={key}>
                  <button
                    onClick={() => setOpen(expanded ? null : key)}
                    className="flex w-full items-center gap-4 px-6 py-3 text-left transition-colors hover:bg-slate-50"
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
                        {!r.in_matcher && <Badge tone="rose">unmatched</Badge>}
                      </span>
                      <span className="flex items-center gap-2 text-xs text-slate-400">
                        {r.location && (
                          <span className={cn("rounded px-1 text-[10px] font-semibold", r.location === "AUH" ? "bg-violet-50 text-violet-600" : "bg-sky-50 text-sky-600")}>
                            {r.location}
                          </span>
                        )}
                        {r.account_manager && (
                          <span className="flex items-center gap-0.5">
                            <MapPin className="h-3 w-3" /> {r.account_manager}
                          </span>
                        )}
                      </span>
                    </span>

                    <span className="hidden lg:block">
                      <CoverageStrip submitted={r.submitted_months} year={year} focusMonth={month} />
                    </span>

                    <span className="flex w-44 shrink-0 justify-end gap-1.5">
                      {missing ? (
                        <Badge tone="rose">
                          <CalendarX className="h-3 w-3" /> Missing {MONTHS[month]}
                        </Badge>
                      ) : r.in_matcher && r.submitted_months.includes(month) ? (
                        <Badge tone="green">
                          <Check className="h-3 w-3" /> Submitted
                        </Badge>
                      ) : null}
                      {r.needs_review_count > 0 && (
                        <Badge tone="amber">{r.needs_review_count} review</Badge>
                      )}
                    </span>
                  </button>
                  {expanded && (
                    <div className="border-t border-slate-100 bg-slate-50/60">
                      <EmployeeRecords pk={key} year={year} />
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={sentinelRef} />
            {isFetchingNextPage && (
              <div className="flex items-center justify-center gap-2 py-4 text-xs text-slate-400">
                <Spinner className="h-4 w-4" /> Loading more…
              </div>
            )}
            <p className="px-6 py-2 text-center text-[11px] text-slate-400">
              Showing {filtered.length} of {filteredTotal}
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
