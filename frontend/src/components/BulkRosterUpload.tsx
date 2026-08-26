/**
 * Bulk roster upload — one sheet listing many employees.
 *
 * Two deliberate steps rather than one:
 *   Analyse  reads the sheet and shows exactly who was found and which rows
 *            disagree with the sheet's own Leave/Billing totals. Nothing is
 *            written, so a mis-read roster costs a click, not a cleanup.
 *   Stage    commits one review item per employee, each carrying the whole
 *            roster as its source file.
 *
 * That preview matters most at scale: committing 100 review items you
 * haven't looked at is exactly the thing that's painful to undo.
 */
import { Fragment, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileSpreadsheet,
  Loader2,
  Sparkles,
  UploadCloud,
  UserCheck,
  UserX,
  X,
} from "lucide-react";
import { bulkRosterPreview, bulkRosterUpload, MONTHS_LONG } from "../api/client";
import { cn, formatBytes } from "../lib/utils";
import { Button, Card, Field, Select } from "./ui";
import { useToast } from "./toast";
import { useUploadSession } from "../lib/uploadSession";

const ACCEPT = ".pdf,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.docx";

function StatPill({
  icon, label, value, tone,
}: {
  icon: React.ReactNode; label: string; value: number | string; tone: string;
}) {
  return (
    <div className={cn("flex items-center gap-2 rounded-lg px-3 py-2", tone)}>
      {icon}
      <div>
        <p className="text-base font-bold leading-5">{value}</p>
        <p className="text-[11px] font-medium opacity-80">{label}</p>
      </div>
    </div>
  );
}

export default function BulkRosterUpload({ onStaged }: { onStaged: () => void }) {
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const now = new Date();

  // All of this lives in UploadSessionProvider (mounted above the router),
  // not local component state — so navigating away mid-analysis and coming
  // back keeps the picked file and its (possibly slow) analysis result
  // instead of throwing them away. See lib/uploadSession.tsx.
  const {
    bulkFile: file, setBulkFile: setFile,
    bulkMonth: month, setBulkMonth: setMonth,
    bulkYear: year, setBulkYear: setYear,
    bulkPreview: preview, setBulkPreview: setPreview,
    bulkResult: result, setBulkResult: setResult,
    bulkAnalysing: analysing, setBulkAnalysing: setAnalysing,
    bulkStaging: staging, setBulkStaging: setStaging,
    clearBulkRoster,
  } = useUploadSession();
  const [dragging, setDragging] = useState(false);
  // Which row's issue list is expanded open — the note text used to be
  // hidden behind a hover-only browser tooltip, easy to miss and invisible
  // on touch. Clicking now shows it directly in the table.
  const [expandedRow, setExpandedRow] = useState<number | null>(null);

  const pick = (f: File | undefined) => {
    if (!f) return;
    setFile(f);
    setPreview(null);
    setResult(null);
  };

  const analyse = async () => {
    if (!file) return;
    setAnalysing(true);
    setResult(null);
    try {
      setPreview(await bulkRosterPreview(file, month || undefined, year || undefined));
    } catch (e: any) {
      toast("error", "Could not read this roster", e?.response?.data?.detail ?? String(e));
    } finally {
      setAnalysing(false);
    }
  };

  const stage = async () => {
    if (!file) return;
    setStaging(true);
    try {
      const r = await bulkRosterUpload(file, month || undefined, year || undefined);
      setResult(r);
      setPreview(null);
      onStaged();
      toast(
        "success",
        `${r.staged} employee${r.staged === 1 ? "" : "s"} staged for review`,
        "Open the Activity log to review and accept them.",
      );
    } catch (e: any) {
      toast("error", "Could not stage this roster", e?.response?.data?.detail ?? String(e));
    } finally {
      setStaging(false);
    }
  };

  const years = [now.getFullYear() + 1, now.getFullYear(), now.getFullYear() - 1];

  return (
    <Card className="p-6">
      <div className="mb-4 flex items-start gap-2 rounded-lg bg-brand-50/70 p-3 text-sm text-brand-900">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" />
        <p>
          For a <strong>single sheet that lists many employees</strong> — one row per person,
          one column per day. Every employee gets their own review item, and the whole
          roster is filed into each of their vault folders.
          <span className="block text-brand-800/80">
            A spreadsheet (.xlsx/.csv) is read cell-by-cell with no AI at all — the most
            accurate option. PDFs and images are read page-by-page instead.
          </span>
        </p>
      </div>

      {!file ? (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files[0]); }}
          onClick={() => inputRef.current?.click()}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 transition-colors",
            dragging
              ? "border-brand-500 bg-brand-50"
              : "border-slate-300 bg-slate-50/60 hover:border-brand-400 hover:bg-brand-50/40",
          )}
        >
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-100">
            <FileSpreadsheet className="h-7 w-7 text-brand-600" />
          </div>
          <div className="text-center">
            <p className="text-sm font-semibold text-slate-700">
              Drop the roster here, or <span className="text-brand-600">browse</span>
            </p>
            <p className="mt-1 text-xs text-slate-400">
              XLSX · CSV · PDF · PNG/JPG · DOCX — one sheet at a time
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }}
          />
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5">
          <FileSpreadsheet className="h-4 w-4 shrink-0 text-brand-500" />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700">
            {file.name}
          </span>
          <span className="text-xs text-slate-400">{formatBytes(file.size)}</span>
          <button
            onClick={clearBulkRoster}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-500"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {file && (
        <>
          <div className="mt-4 flex flex-wrap items-end gap-4">
            <Field label="Month (optional)">
              <Select value={month} onChange={(e) => setMonth(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Read from the sheet</option>
                {MONTHS_LONG.slice(1).map((m, i) => (
                  <option key={i + 1} value={i + 1}>{m}</option>
                ))}
              </Select>
            </Field>
            <Field label="Year (optional)">
              <Select value={year} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Read from the sheet</option>
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </Select>
            </Field>
            <div className="ml-auto flex gap-2">
              <Button variant="secondary" onClick={analyse} disabled={analysing || staging}>
                {analysing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                {analysing ? "Reading…" : "Analyse roster"}
              </Button>
              <Button onClick={stage} disabled={staging || analysing || !preview}>
                {staging ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                {preview ? `Stage ${preview.headcount} employee${preview.headcount === 1 ? "" : "s"}` : "Stage for review"}
              </Button>
            </div>
          </div>
          {!preview && !result && (
            <p className="mt-2 text-xs text-slate-400">
              Analyse first — you'll see exactly who was found before anything is staged.
            </p>
          )}
        </>
      )}

      {preview && (
        <div className="mt-5">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatPill
              icon={<UserCheck className="h-4 w-4" />} label="employees found"
              value={preview.headcount} tone="bg-slate-100 text-slate-700"
            />
            <StatPill
              icon={<CheckCircle2 className="h-4 w-4" />} label="matched to the matcher"
              value={preview.matched} tone="bg-emerald-50 text-emerald-700"
            />
            {preview.unmatched > 0 && (
              <StatPill
                icon={<UserX className="h-4 w-4" />} label="need picking in Review"
                value={preview.unmatched} tone="bg-amber-50 text-amber-700"
              />
            )}
            {preview.flagged > 0 && (
              <StatPill
                icon={<AlertTriangle className="h-4 w-4" />} label="rows to check"
                value={preview.flagged} tone="bg-rose-50 text-rose-700"
              />
            )}
            <p className="ml-auto text-xs text-slate-400">
              {preview.month ? `${MONTHS_LONG[preview.month]} ${preview.year}` : "period unknown"}
              {preview.calendar_days ? ` · ${preview.calendar_days} days` : ""} ·{" "}
              {preview.method === "xlsx-cells"
                ? "read from cells (no AI)"
                : `read as images (${preview.llm_calls} AI call${preview.llm_calls === 1 ? "" : "s"})`}
            </p>
          </div>

          {preview.issues.length > 0 && (
            <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              <p className="mb-1 font-semibold">Worth checking before you stage this</p>
              <ul className="list-inside list-disc space-y-0.5">
                {preview.issues.map((i, n) => <li key={n}>{i}</li>)}
              </ul>
            </div>
          )}

          <div className="max-h-[420px] overflow-auto rounded-lg border border-slate-200">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-2 py-2">#</th>
                  <th className="px-2 py-2">Employee</th>
                  <th className="px-2 py-2">Matched</th>
                  <th className="px-2 py-2 text-right">Leave<br />(sheet)</th>
                  <th className="px-2 py-2 text-right">Leave<br />(read)</th>
                  <th className="px-2 py-2 text-right">Worked</th>
                  <th className="px-2 py-2">Checks</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {preview.rows.map((r) => {
                  const mismatch = r.issues.some((i) => i.includes("mismatch") || i.includes("add up"));
                  const isOpen = expandedRow === r.sr_no;
                  return (
                    <Fragment key={r.sr_no}>
                      <tr className={cn(mismatch && "bg-rose-50/60")}>
                        <td className="px-2 py-1.5 text-slate-400">{r.sr_no}</td>
                        <td className="px-2 py-1.5">
                          <span className="block font-medium text-slate-800">{r.name}</span>
                          {r.title && <span className="block text-[10px] text-slate-400">{r.title}</span>}
                        </td>
                        <td className="px-2 py-1.5">
                          {r.matched_name ? (
                            <span className="text-emerald-700">
                              {r.matched_name}
                              {r.matched_employee_id && (
                                <span className="text-slate-400"> ({r.matched_employee_id})</span>
                              )}
                            </span>
                          ) : (
                            <span className="text-amber-600">pick in Review</span>
                          )}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">
                          {r.stated_leave_days ?? "—"}
                        </td>
                        <td className={cn(
                          "px-2 py-1.5 text-right tabular-nums font-semibold",
                          mismatch ? "text-rose-600" : "text-slate-700",
                        )}>
                          {r.leave_days_read}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">
                          {r.working_days_read}
                        </td>
                        <td className="px-2 py-1.5">
                          {r.issues.length === 0 ? (
                            <span className="inline-flex items-center gap-1 text-emerald-600">
                              <CheckCircle2 className="h-3 w-3" /> verified
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setExpandedRow(isOpen ? null : r.sr_no)}
                              className="inline-flex items-center gap-1 font-semibold text-amber-700 hover:text-amber-900 hover:underline"
                            >
                              {isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                              {r.issues.length} note{r.issues.length === 1 ? "" : "s"}
                            </button>
                          )}
                        </td>
                      </tr>
                      {isOpen && r.issues.length > 0 && (
                        <tr className="bg-amber-50/60">
                          <td colSpan={7} className="px-4 py-2.5">
                            <ul className="list-inside list-disc space-y-1 text-xs leading-relaxed text-amber-900">
                              {r.issues.map((issue, i) => <li key={i}>{issue}</li>)}
                            </ul>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-slate-400">
            "Leave (sheet)" is what the roster itself prints; "Leave (read)" is what was
            extracted. Rows where they disagree are highlighted and will be held for review
            rather than auto-accepted.
          </p>
        </div>
      )}

      {result && (
        <div className="mt-5 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
          <p className="flex items-center gap-2 font-semibold">
            <CheckCircle2 className="h-4 w-4" />
            {result.staged} employee{result.staged === 1 ? "" : "s"} staged from {result.filename}
          </p>
          <p className="mt-1 text-emerald-800/90">
            {result.matched} matched automatically
            {result.unmatched.length > 0 && `, ${result.unmatched.length} need an employee picked`}
            {result.flagged > 0 && `, ${result.flagged} flagged for a closer look`}. Review and
            accept them in the Activity log.
          </p>
        </div>
      )}
    </Card>
  );
}
