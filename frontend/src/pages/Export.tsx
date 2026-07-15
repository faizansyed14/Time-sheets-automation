import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Search } from "lucide-react";
import {
  fetchExportByPeriod,
  MONTHS_LONG,
  timesheetExportUrl,
  type TimesheetExportRow,
} from "../api/client";
import { downloadFile } from "../lib/filePreview";
import { useDebounced } from "../lib/useInfinite";
import { cn } from "../lib/utils";
import { Button, Card, Input, PageHeader, Select, Spinner } from "../components/ui";

const CUR_YEAR = new Date().getFullYear();
const CUR_MONTH = new Date().getMonth() + 1;

type Col = {
  key: string;
  header: string;
  width: string;
  sticky?: boolean;
  get: (row: TimesheetExportRow) => string | number;
};

const LEAVE_COLS: Col[] = [
  {
    key: "annual_count",
    header: "Annual Leave Count",
    width: "min-w-[88px]",
    get: (r) => r.annual_leave_count,
  },
  {
    key: "annual_dates",
    header: "Annual Leave Dates",
    width: "min-w-[220px]",
    get: (r) => r.annual_leave_dates.join(", "),
  },
  {
    key: "remote_count",
    header: "Remote / WFH Count",
    width: "min-w-[88px]",
    get: (r) => r.remote_work_count,
  },
  {
    key: "remote_dates",
    header: "Remote / WFH Dates",
    width: "min-w-[220px]",
    get: (r) => r.remote_work_dates.join(", "),
  },
  {
    key: "sick_count",
    header: "Sick Leave Count",
    width: "min-w-[88px]",
    get: (r) => r.sick_leave_count,
  },
  {
    key: "sick_dates",
    header: "Sick Leave Dates",
    width: "min-w-[220px]",
    get: (r) => r.sick_leave_dates.join(", "),
  },
  {
    key: "maternity_count",
    header: "Maternity Leave Count",
    width: "min-w-[96px]",
    get: (r) => r.maternity_leave_count,
  },
  {
    key: "maternity_dates",
    header: "Maternity Leave Dates",
    width: "min-w-[220px]",
    get: (r) => r.maternity_leave_dates.join(", "),
  },
  {
    key: "unpaid_count",
    header: "Unpaid Leave Count",
    width: "min-w-[88px]",
    get: (r) => r.unpaid_leave_count,
  },
  {
    key: "unpaid_dates",
    header: "Unpaid Leave Dates",
    width: "min-w-[220px]",
    get: (r) => r.unpaid_leave_dates.join(", "),
  },
  {
    key: "absent_count",
    header: "Absent Count",
    width: "min-w-[72px]",
    get: (r) => r.absent_count,
  },
  {
    key: "absent_dates",
    header: "Absent Dates",
    width: "min-w-[220px]",
    get: (r) => r.absent_dates.join(", "),
  },
  {
    key: "holiday_count",
    header: "Public Holiday Count",
    width: "min-w-[96px]",
    get: (r) => r.public_holiday_count,
  },
  {
    key: "holiday_dates",
    header: "Public Holiday Dates",
    width: "min-w-[220px]",
    get: (r) => r.public_holiday_dates.join(", "),
  },
];

const COLUMNS: Col[] = [
  {
    key: "row",
    header: "#",
    width: "min-w-[44px] w-[44px]",
    sticky: true,
    get: () => "",
  },
  {
    key: "employee_id",
    header: "Employee ID",
    width: "min-w-[110px]",
    sticky: true,
    get: (r) => r.employee_id ?? "",
  },
  {
    key: "employee_name",
    header: "Employee Name",
    width: "min-w-[180px]",
    sticky: true,
    get: (r) => r.employee_name ?? "",
  },
  {
    key: "dco_number",
    header: "DCO Number",
    width: "min-w-[100px]",
    get: (r) => r.dco_number ?? "",
  },
  {
    key: "account_manager",
    header: "Account Manager",
    width: "min-w-[140px]",
    get: (r) => r.account_manager ?? "",
  },
  {
    key: "location",
    header: "Location",
    width: "min-w-[72px]",
    get: (r) => r.location ?? "",
  },
  {
    key: "project",
    header: "Project",
    width: "min-w-[120px]",
    get: (r) => r.project ?? "",
  },
  {
    key: "employee_email",
    header: "Email",
    width: "min-w-[180px]",
    get: (r) => r.employee_email ?? "",
  },
  {
    key: "contact_no",
    header: "Contact",
    width: "min-w-[110px]",
    get: (r) => r.contact_no ?? "",
  },
  {
    key: "validation_status",
    header: "Validation",
    width: "min-w-[100px]",
    get: (r) => r.validation_status,
  },
  {
    key: "approval_status",
    header: "Approval",
    width: "min-w-[100px]",
    get: (r) => r.approval_status,
  },
  ...LEAVE_COLS,
];

const STICKY_LEFT: Record<string, number> = (() => {
  let left = 0;
  const map: Record<string, number> = {};
  for (const col of COLUMNS) {
    if (col.sticky) {
      map[col.key] = left;
      left += col.key === "row" ? 44 : col.key === "employee_id" ? 110 : 180;
    }
  }
  return map;
})();

function cellValue(row: TimesheetExportRow, col: Col, rowIndex: number): string | number {
  if (col.key === "row") return rowIndex + 1;
  return col.get(row);
}

function matchesName(name: string | null, query: string): boolean {
  const n = (name ?? "").toLowerCase();
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  return terms.every((t) => n.includes(t));
}

export default function ExportPage() {
  const [year, setYear] = useState(CUR_YEAR);
  const [month, setMonth] = useState(CUR_MONTH);
  const [nameQ, setNameQ] = useState("");
  const debouncedNameQ = useDebounced(nameQ, 250);

  useEffect(() => setNameQ(""), [month, year]);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["export-by-period", month, year],
    queryFn: () => fetchExportByPeriod(month, year),
  });

  const years = useMemo(() => {
    const ys = new Set<number>([CUR_YEAR, CUR_YEAR - 1, CUR_YEAR - 2, year]);
    (data ?? []).forEach((r) => ys.add(r.year));
    return [...ys].sort((a, b) => b - a);
  }, [data, year]);

  const rows = data ?? [];
  const submittedCount = rows.filter((r) => r.has_record).length;
  const filtered = useMemo(
    () => rows.filter((r) => matchesName(r.employee_name, debouncedNameQ)),
    [rows, debouncedNameQ],
  );
  const exportName = `timesheets_${year}-${String(month).padStart(2, "0")}.xlsx`;
  const exportXlsx = () => downloadFile(timesheetExportUrl(month, year), exportName);
  const subtitle =
    debouncedNameQ.trim() && filtered.length !== rows.length
      ? `${MONTHS_LONG[month]} ${year} — showing ${filtered.length} of ${rows.length} · ${submittedCount} submitted`
      : `${MONTHS_LONG[month]} ${year} — ${submittedCount} submitted · ${rows.length} employees`;

  return (
    <div className="flex h-[calc(100vh-4rem)] animate-fade-up flex-col">
      <PageHeader
        title="Export"
        subtitle={subtitle}
        actions={
          <div className="flex flex-wrap items-center gap-2">
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
            <Button onClick={exportXlsx}>
              <Download className="h-4 w-4" />
              Export XLSX
            </Button>
          </div>
        }
      />

      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden p-0">
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center py-16">
            <Spinner className="h-7 w-7" />
          </div>
        ) : (
          <>
            {isFetching && (
              <p className="border-b border-slate-100 px-3 py-1.5 text-[11px] text-slate-400">Refreshing…</p>
            )}
            <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2">
              <div className="relative min-w-[220px] flex-1 max-w-md">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <Input
                  value={nameQ}
                  onChange={(e) => setNameQ(e.target.value)}
                  placeholder="Search by employee name…"
                  className="pl-9"
                />
              </div>
              {nameQ.trim() && (
                <button
                  type="button"
                  onClick={() => setNameQ("")}
                  className="text-xs font-semibold text-slate-500 hover:text-slate-700"
                >
                  Clear
                </button>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              <table className="w-max min-w-full border-collapse text-left text-xs">
                <thead className="sticky top-0 z-30">
                  <tr className="bg-[#1E3A5F] text-[11px] font-bold uppercase tracking-wide text-white">
                    {COLUMNS.map((col) => (
                      <th
                        key={col.key}
                        className={cn(
                          "border border-[#2a4a73] px-2.5 py-2 align-bottom whitespace-nowrap",
                          col.width,
                          col.sticky && "sticky z-40 bg-[#1E3A5F]"
                        )}
                        style={col.sticky ? { left: STICKY_LEFT[col.key] } : undefined}
                      >
                        {col.header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td
                        colSpan={COLUMNS.length}
                        className="border border-slate-200 px-4 py-16 text-center text-sm text-slate-500"
                      >
                        No employees in the matcher list.
                      </td>
                    </tr>
                  ) : filtered.length === 0 ? (
                    <tr>
                      <td
                        colSpan={COLUMNS.length}
                        className="border border-slate-200 px-4 py-16 text-center text-sm text-slate-500"
                      >
                        No employees match &ldquo;{debouncedNameQ.trim()}&rdquo;.
                      </td>
                    </tr>
                  ) : (
                    filtered.map((row, idx) => (
                      <tr
                        key={row.id}
                        className={cn(
                          "group",
                          idx % 2 === 0 ? "bg-white" : "bg-slate-50/90",
                          !row.has_record && "opacity-80",
                          "hover:bg-sky-50/60"
                        )}
                      >
                        {COLUMNS.map((col) => {
                          const val = cellValue(row, col, idx);
                          const isCount = col.key.endsWith("_count");
                          const isDates = col.key.endsWith("_dates");
                          const empty = val === "" || val === 0;
                          const rowBg = idx % 2 === 0 ? "bg-white" : "bg-slate-50";
                          return (
                            <td
                              key={col.key}
                              className={cn(
                                "border border-slate-200 px-2.5 py-1.5 align-top text-slate-800",
                                col.width,
                                isCount && "text-center font-semibold tabular-nums",
                                isDates && "font-mono text-[11px] leading-relaxed whitespace-normal",
                                col.sticky && cn("sticky z-20", rowBg, "group-hover:bg-sky-50"),
                                empty && !col.sticky && col.key !== "row" && "text-slate-300"
                              )}
                              style={col.sticky ? { left: STICKY_LEFT[col.key] } : undefined}
                              title={typeof val === "string" && val.length > 40 ? val : undefined}
                            >
                              {empty && !col.sticky && col.key !== "row" ? "—" : val}
                            </td>
                          );
                        })}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
