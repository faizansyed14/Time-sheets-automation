/**
 * Full-screen compare & fix overlay for failed / needs-review pipeline files.
 *
 * Layout:
 *   LEFT  — full manual entry form (employee, period, all 6 leave buckets,
 *            optional file attachments, note)
 *   RIGHT — original file preview (EML viewer, PDF iframe, or image)
 *
 * On "Save & file record" → calls pipelineManualFix which:
 *   1. Creates / merges the monthly record with the entered dates (no LLM)
 *   2. Updates the original pipeline tracker to SUCCESS / resolved
 *   3. Purges the S3 raw copy (_pipeline-raw)
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Columns2,
  Download,
  FileText,
  Paperclip,
  PencilLine,
  Plus,
  Search,
  Trash2,
  User,
  X,
} from "lucide-react";
import {
  attachmentRenderUrl,
  attachmentUrl,
  fetchEmail,
  fetchEmployeeMatcher,
  deletePipelineFile,
  pipelineManualFix,
  pipelineRawRenderUrl,
  pipelineRawUrl,
  MONTHS_LONG,
  type Employee,
  type PipelineFile,
} from "../api/client";
import { isDocx, isEml, isPdf, isPreviewable, isXlsx } from "../lib/filePreview";
import { isBodyJunkImage } from "../lib/attachmentFilters";
import { DocxPreviewPane, EmlPreviewPane, ServerRenderPane } from "./FilePreview";
import { Button, Input, Select, Spinner } from "./ui";
import { cn, formatBytes } from "../lib/utils";
import { useToast } from "./toast";

// ---------------------------------------------------------------------------
// Leave bucket definitions
// ---------------------------------------------------------------------------
import { leaveBucketDefs } from "../lib/theme";

const BUCKETS = leaveBucketDefs() as readonly { key: string; label: string; tone: string }[];

const FILE_RE = /\.(pdf|docx|xlsx|png|jpe?g|eml)$/i;

// ---------------------------------------------------------------------------
// Month day-picker — a calendar that shows ONLY the record's month, so a date
// from another month can never be added by mistake. Click a day to toggle it.
// ---------------------------------------------------------------------------
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"] as const;
const pad2 = (n: number) => String(n).padStart(2, "0");

function MonthDayPicker({
  year,
  month,
  selected,
  tone,
  onToggle,
}: {
  year: number;
  month: number; // 1-12
  selected: string[];
  tone: string;
  onToggle: (iso: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const daysInMonth = new Date(year, month, 0).getDate();
  const firstDow = new Date(year, month - 1, 1).getDay(); // 0 = Sunday
  const iso = (d: number) => `${year}-${pad2(month)}-${pad2(d)}`;
  const selSet = new Set(selected);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-md bg-brand-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-brand-700"
      >
        <Plus className="h-3.5 w-3.5" /> Add day
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 w-[230px] rounded-lg border border-slate-200 bg-white p-2.5 shadow-pop">
          <p className="mb-1.5 text-center text-[11px] font-semibold text-slate-600">
            {MONTHS_LONG[month]} {year}
          </p>
          <div className="grid grid-cols-7 gap-0.5 text-center text-[9px] font-bold uppercase text-slate-400">
            {WEEKDAYS.map((d, i) => (
              <div key={i}>{d}</div>
            ))}
          </div>
          <div className="mt-0.5 grid grid-cols-7 gap-0.5">
            {Array.from({ length: firstDow }).map((_, i) => (
              <div key={`blank-${i}`} />
            ))}
            {Array.from({ length: daysInMonth }).map((_, i) => {
              const day = i + 1;
              const on = selSet.has(iso(day));
              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => onToggle(iso(day))}
                  className={cn(
                    "flex h-7 items-center justify-center rounded text-[11px] font-medium transition-colors",
                    on
                      ? cn("ring-1 ring-inset", tone)
                      : "text-slate-700 hover:bg-slate-100"
                  )}
                >
                  {day}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Employee search (inline, no modal wrapper)
// ---------------------------------------------------------------------------
function EmployeePicker({
  employees,
  isLoading,
  value,
  valuePk,
  onChange,
  onPick,
}: {
  employees: Employee[] | undefined;
  isLoading: boolean;
  value: string;
  valuePk: string;
  onChange: (q: string) => void;
  onPick: (e: Employee) => void;
}) {
  const [open, setOpen] = useState(false);

  const matches = useMemo(() => {
    const q = value.toLowerCase().trim();
    const list = employees ?? [];
    const filtered = q
      ? list.filter(
          (e) =>
            e.name.toLowerCase().includes(q) ||
            e.employee_id.toLowerCase().includes(q) ||
            (e.location ?? "").toLowerCase().includes(q) ||
            (e.account_manager ?? "").toLowerCase().includes(q)
        )
      : list;
    return filtered.slice(0, 25);
  }, [employees, value]);

  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
      <Input
        value={value}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder="Search name or ID…"
        className="pl-9"
        autoComplete="off"
      />
      {open && (
        <div className="absolute z-20 mt-1 max-h-52 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-pop">
          {isLoading ? (
            <div className="flex justify-center py-6"><Spinner /></div>
          ) : matches.length === 0 ? (
            <p className="px-3 py-4 text-sm text-slate-400">No employees match.</p>
          ) : (
            matches.map((e) => (
              <button
                key={e.id}
                type="button"
                onMouseDown={(ev) => ev.preventDefault()}
                onClick={() => { onPick(e); setOpen(false); }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-brand-50",
                  valuePk === e.id && "bg-brand-50"
                )}
              >
                <User className="h-4 w-4 shrink-0 text-slate-400" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-slate-800">{e.name}</span>
                  <span className="block truncate text-xs text-slate-500">
                    {e.employee_id}{e.location ? ` · ${e.location}` : ""}{e.account_manager ? ` · ${e.account_manager}` : ""}
                  </span>
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right-side preview panes
// ---------------------------------------------------------------------------
// Generic preview by type: EML pane, PDF iframe, DOCX/XLSX server-rendered
// page images, plain <img> for pictures, download card otherwise.
function SourcePreview({ url, renderUrl, name, ct }: {
  url: string; renderUrl?: string | null; name: string; ct: string;
}) {
  if (isEml(name, ct)) {
    return (
      <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
        <EmlPreviewPane fileUrl={url} filename={name} />
      </div>
    );
  }
  if (isPdf(name, ct)) {
    return <iframe src={url} title={name} className="h-full w-full rounded-lg bg-white" />;
  }
  if ((isDocx(name, ct) || isXlsx(name, ct)) && renderUrl) {
    // Server render-to-image — same preview path for DOCX and XLSX.
    return (
      <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
        <ServerRenderPane renderUrl={renderUrl} />
      </div>
    );
  }
  if (isDocx(name, ct)) {
    return (
      <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
        <DocxPreviewPane fileUrl={url} />
      </div>
    );
  }
  if (isPreviewable(name, ct)) {
    return (
      <img
        src={url}
        alt={name}
        className="mx-auto block max-h-full max-w-full rounded-lg object-contain"
      />
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-500">
      <FileText className="h-10 w-10 text-slate-300" />
      <p className="text-sm">{name} cannot be previewed inline.</p>
      <a
        href={url}
        download={name}
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
      >
        <Download className="h-4 w-4" /> Download
      </a>
    </div>
  );
}

function RawFilePreview({ file }: { file: PipelineFile }) {
  return (
    <SourcePreview
      url={pipelineRawUrl(file.id)}
      renderUrl={pipelineRawRenderUrl(file.id)}
      name={file.filename ?? "file"}
      ct={file.content_type ?? ""}
    />
  );
}

// ---------------------------------------------------------------------------
// Main modal
// ---------------------------------------------------------------------------
export default function PipelineCompareFixModal({
  file,
  onClose,
  onSaved,
  onDiscarded,
}: {
  file: PipelineFile | null;
  onClose: () => void;
  onSaved: () => void;
  onDiscarded?: () => void;
}) {
  const { toast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const prevFileId = useRef<string | null>(null);

  // Form state
  const [employeeQ, setEmployeeQ] = useState("");
  const [employeePk, setEmployeePk] = useState("");
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [year, setYear] = useState(new Date().getFullYear());
  const [dates, setDates] = useState<Record<string, string[]>>({});
  const [attachments, setAttachments] = useState<File[]>([]);
  const [note, setNote] = useState("");
  const [approved, setApproved] = useState(false);
  const [approvalDetail, setApprovalDetail] = useState("");
  const [pending, setPending] = useState(false);

  const { data: employees, isLoading } = useQuery({
    queryKey: ["employee-matcher"],
    queryFn: fetchEmployeeMatcher,
    enabled: !!file,
  });

  // Related sources: the OTHER attachments of the same email (approval
  // screenshots, extra timesheets) — switchable in the right panel so the
  // reviewer can cross-check without leaving Compare & Fix.
  const [activeSourceId, setActiveSourceId] = useState<string | null>(null);
  const emailSourceId = file?.source_kind === "email" ? file.source_id : null;
  const { data: sourceEmail } = useQuery({
    queryKey: ["email", emailSourceId],
    queryFn: () => fetchEmail(emailSourceId!),
    enabled: !!emailSourceId,
    staleTime: 60_000,
  });
  const relatedSources = useMemo(() => {
    if (!sourceEmail || !emailSourceId) return [] as {
      id: string; filename: string; ct: string; url: string; renderUrl: string;
    }[];
    const inlineIds = sourceEmail.inline_attachment_ids ?? [];
    return (sourceEmail.attachments ?? [])
      .filter((a) => a.attachment_id !== file?.attachment_id)   // skip the staged file itself
      .filter((a) => !isBodyJunkImage(a, inlineIds))            // skip signature/logo images
      .map((a) => ({
        id: a.attachment_id,
        filename: a.filename,
        ct: a.content_type,
        url: attachmentUrl(emailSourceId, a.attachment_id),
        renderUrl: attachmentRenderUrl(emailSourceId, a.attachment_id),
      }));
  }, [sourceEmail, emailSourceId, file?.attachment_id]);
  const activeSource = relatedSources.find((s) => s.id === activeSourceId) ?? null;

  // Reset when a new file is opened. Pre-fill from the AI-staged extraction
  // (extraction_meta.staged) so a "Run Extraction" review starts populated.
  useEffect(() => {
    if (!file || file.id === prevFileId.current) return;
    prevFileId.current = file.id;
    const staged = (file.extraction_meta?.staged ?? null) as {
      employee_pk?: string | null; matched_name?: string | null;
      matched_employee_id?: string | null; month?: number | null; year?: number | null;
      buckets?: Record<string, string[]>;
    } | null;
    if (staged?.employee_pk) {
      setEmployeePk(staged.employee_pk);
      setEmployeeQ(`${staged.matched_employee_id ?? ""} · ${staged.matched_name ?? ""}`.trim());
    } else {
      setEmployeePk("");
      setEmployeeQ(file.employee_id ?? file.employee_name ?? "");
    }
    setMonth(staged?.month ?? file.month ?? new Date().getMonth() + 1);
    setYear(staged?.year ?? file.year ?? new Date().getFullYear());
    setDates(staged?.buckets ?? {});
    setAttachments([]);
    setNote("");
    // Manager approval — pre-filled from what Extract Email found (signature
    // on a sheet, approval screenshot, approval wording); reviewer can flip it.
    const foundApproval = (file.extraction_meta?.full_email_extract ?? null) as {
      approval?: { detected: boolean; detail: string };
    } | null;
    setApproved(!!foundApproval?.approval?.detected);
    setApprovalDetail(foundApproval?.approval?.detected ? (foundApproval.approval?.detail ?? "") : "");
    setPending(false);
    setActiveSourceId(null);
  }, [file]);

  const isStaged = file?.failure_code === "pending_review";
  // Present only on items staged by the one-button "Extract Email" flow.
  const fullEmail = (file?.extraction_meta?.full_email_extract ?? null) as {
    sheets?: {
      filename: string; kind: string; leave_days?: number;
      manager_signature?: boolean;
    }[];
    approval?: { detected: boolean; detail: string };
  } | null;

  // Lock scroll + Escape key.
  useEffect(() => {
    if (!file) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !pending && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [file, pending, onClose]);

  const selected = useMemo(
    () => employees?.find((e) => e.id === employeePk) ?? null,
    [employees, employeePk]
  );

  // Toggle a day in a bucket. The picker only offers days inside the record's
  // month, so out-of-month dates can't be entered.
  const toggleDate = (key: string, iso: string) => {
    setDates((d) => {
      const cur = d[key] ?? [];
      const next = cur.includes(iso)
        ? cur.filter((x) => x !== iso)
        : [...cur, iso].sort();
      return { ...d, [key]: next };
    });
  };

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const valid = Array.from(list).filter((f) => FILE_RE.test(f.name));
    setAttachments((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...valid.filter((f) => !names.has(f.name))];
    });
  };

  const totalDays = BUCKETS.reduce((a, b) => a + (dates[b.key]?.length ?? 0), 0);
  const canSave = !!employeePk && month >= 1 && month <= 12 && year >= 2000 && !pending;

  const currentYear = new Date().getFullYear();
  const years = [currentYear + 1, currentYear, currentYear - 1, currentYear - 2].filter(
    (v, i, a) => a.indexOf(v) === i
  );

  const handleDiscard = async () => {
    if (!file || pending) return;
    if (!isStaged) {
      onClose();
      return;
    }
    setPending(true);
    try {
      await deletePipelineFile(file.id);
      toast("info", "Review item removed");
      onDiscarded?.();
      onClose();
    } catch (e: any) {
      toast("error", "Delete failed", e?.response?.data?.detail ?? String(e));
      setPending(false);
    }
  };

  const handleSave = async () => {
    if (!file || !canSave) return;
    setPending(true);
    try {
      const result = await pipelineManualFix(file.id, {
        employee_pk: employeePk,
        month,
        year,
        buckets: dates,
        note: note || undefined,
        approval_status: approved ? "approved" : "not_approved",
        approval_detail: approvalDetail || undefined,
        files: attachments,
      });
      const ok = result.status === "success";
      toast(
        ok ? "success" : "warning",
        ok ? "Record saved & filed" : "Saved — needs review",
        `${result.employee_name ?? selected?.name} — ${MONTHS_LONG[month]} ${year}`
      );
      onSaved();
      onClose();
    } catch (e: any) {
      toast("error", "Could not save", e?.response?.data?.detail ?? String(e));
      setPending(false);
    }
  };

  if (!file) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col p-2 sm:p-4">
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" onClick={() => !pending && onClose()} />
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl bg-white shadow-pop">

        {/* Header */}
        <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 bg-slate-50 px-4 py-3">
          <Columns2 className="h-4 w-4 text-slate-400" />
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-700">
            {isStaged ? "Review extraction" : "Compare & Fix"} — {file.filename}
          </span>
          {file.failure_detail && (
            <span className="hidden truncate text-xs text-rose-500 sm:block max-w-sm">
              {file.failure_label ?? "Error"}: {file.failure_detail}
            </span>
          )}
          <button
            onClick={() => !pending && onClose()}
            aria-label="Close"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-white hover:text-slate-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Two-pane body */}
        <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-2">

          {/* LEFT — manual entry form */}
          <div className="flex min-h-0 flex-col overflow-y-auto border-b border-slate-100 p-5 lg:border-b-0 lg:border-r">
            <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-slate-500">
              Manual entry
            </h3>

            {/* Banner — info for AI-staged review, error for a real failure */}
            {isStaged ? (
              <div className="mb-3 rounded-lg border border-brand-200 bg-brand-50 p-3 text-sm leading-5 text-brand-800">
                <p className="font-semibold">AI-extracted — review &amp; accept</p>
                <p className="mt-0.5 text-slate-600">
                  The leaves below were read from the document. Check them against the preview, edit if
                  needed, then Accept to file the record. Closing leaves it in the pipeline.
                </p>
              </div>
            ) : (
              <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm leading-5 text-rose-800">
                <p className="font-semibold">{file.failure_label ?? "Failed"}</p>
                <p className="mt-0.5">{file.failure_detail}</p>
              </div>
            )}

            {/* Extract Email breakdown — which sheets inside the email were
                read, their kind, and where the approval evidence came from. */}
            {fullEmail && (
              <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-700">
                <p className="font-bold uppercase tracking-wide text-slate-500">
                  Read from this email
                </p>
                <ul className="mt-1 space-y-0.5">
                  {(fullEmail.sheets ?? []).map((s, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                      <span className="min-w-0">
                        <span className="font-semibold text-slate-800">{s.filename}</span>
                        {" — "}
                        {s.kind === "leave_certificate" ? "leave certificate" : s.kind}
                        {s.leave_days ? `, ${s.leave_days} leave day(s)` : ""}
                        {s.manager_signature ? " · manager signature ✓" : ""}
                      </span>
                    </li>
                  ))}
                </ul>
                {fullEmail.approval && (
                  <p className={cn("mt-1.5 font-semibold",
                    fullEmail.approval.detected ? "text-emerald-700" : "text-amber-700")}>
                    {fullEmail.approval.detail}
                  </p>
                )}
              </div>
            )}

            {/* Employee */}
            <label className="mb-3 block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Employee (from matcher)
              </span>
              <EmployeePicker
                employees={employees}
                isLoading={isLoading}
                value={employeeQ}
                valuePk={employeePk}
                onChange={(q) => { setEmployeeQ(q); setEmployeePk(""); }}
                onPick={(e) => {
                  setEmployeePk(e.id);
                  setEmployeeQ(`${e.employee_id} · ${e.name}${e.location ? ` [${e.location}]` : ""}`);
                }}
              />
              {selected && (
                <p className="mt-1 text-xs text-emerald-700">
                  {selected.name} ({selected.employee_id}
                  {selected.location ? ` · ${selected.location}` : ""})
                  {selected.account_manager ? ` — ${selected.account_manager}` : ""}
                </p>
              )}
            </label>

            {/* Month + Year */}
            <div className="mb-3 grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Month</span>
                <Select value={String(month)} onChange={(e) => setMonth(Number(e.target.value))}>
                  {MONTHS_LONG.map((m, i) =>
                    i === 0 ? null : <option key={i} value={i}>{m}</option>
                  )}
                </Select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Year</span>
                <Select value={String(year)} onChange={(e) => setYear(Number(e.target.value))}>
                  {years.map((y) => <option key={y} value={y}>{y}</option>)}
                </Select>
              </label>
            </div>

            {/* Leave buckets */}
            <div className="mb-3 space-y-3">
              {BUCKETS.map((b) => (
                <div key={b.key}>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {b.label} <span className="text-slate-400">· {dates[b.key]?.length ?? 0}</span>
                  </p>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {(dates[b.key] ?? []).map((d) => (
                      <span
                        key={d}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-md px-2 py-1 font-mono text-[11px] font-medium ring-1 ring-inset",
                          b.tone
                        )}
                      >
                        {d}
                        <button
                          type="button"
                          onClick={() =>
                            setDates((dr) => ({
                              ...dr,
                              [b.key]: (dr[b.key] ?? []).filter((x) => x !== d),
                            }))
                          }
                          className="opacity-60 hover:opacity-100"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                    <MonthDayPicker
                      year={year}
                      month={month}
                      selected={dates[b.key] ?? []}
                      tone={b.tone}
                      onToggle={(iso) => toggleDate(b.key, iso)}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Manager approval — the reviewer's explicit verdict, filed on
                the record on Accept. Pre-filled from what Extract Email found. */}
            <div className="mb-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Manager approval
              </p>
              <div className="inline-flex rounded-lg border border-slate-300 p-0.5">
                <button
                  type="button"
                  onClick={() => setApproved(true)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
                    approved
                      ? "bg-emerald-600 text-white shadow-sm"
                      : "text-slate-600 hover:bg-slate-100"
                  )}
                >
                  <CheckCircle2 className="h-3.5 w-3.5" /> Approved
                </button>
                <button
                  type="button"
                  onClick={() => setApproved(false)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
                    !approved
                      ? "bg-slate-700 text-white shadow-sm"
                      : "text-slate-600 hover:bg-slate-100"
                  )}
                >
                  <X className="h-3.5 w-3.5" /> Not approved
                </button>
              </div>
              <input
                value={approvalDetail}
                onChange={(e) => setApprovalDetail(e.target.value)}
                placeholder={approved
                  ? 'Evidence, e.g. "Approved — Sylvia Noronha, 2 Jul 2026"'
                  : "Why not approved (optional)"}
                className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/10"
              />
            </div>

            {/* File attachments */}
            <div className="mb-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Attach files (optional)
              </p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.eml"
                className="hidden"
                onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
              />
              <Button variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()}>
                <Paperclip className="h-4 w-4" /> Add files
              </Button>
              {attachments.length > 0 && (
                <div className="mt-2 space-y-1">
                  {attachments.map((f) => (
                    <div key={f.name} className="flex items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5">
                      <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                      <span className="min-w-0 flex-1 truncate text-xs text-slate-700">{f.name}</span>
                      <span className="text-[11px] text-slate-400">{formatBytes(f.size)}</span>
                      <button
                        type="button"
                        onClick={() => setAttachments((a) => a.filter((x) => x.name !== f.name))}
                        className="rounded p-0.5 text-slate-400 hover:text-rose-500"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Note */}
            <div className="mb-4">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Note (optional)
              </p>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. 'Confirmed with manager — Danial Gohar May 2026'"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/10"
              />
            </div>

            {/* Actions */}
            <div className="flex items-center gap-3 border-t border-slate-100 pt-4">
              <span className="flex-1 text-xs text-slate-400">{totalDays} day(s) entered</span>
              <Button variant="secondary" onClick={handleDiscard} disabled={pending}>
                {isStaged ? (
                  <>
                    <Trash2 className="h-4 w-4" />
                    Delete
                  </>
                ) : (
                  "Cancel"
                )}
              </Button>
              <Button disabled={!canSave} onClick={handleSave}>
                {pending ? (
                  <Spinner className="border-white/40 border-t-white" />
                ) : (
                  <PencilLine className="h-4 w-4" />
                )}
                {isStaged ? "Accept & file record" : "Save & file record"}
              </Button>
            </div>
          </div>

          {/* RIGHT — source preview, switchable between the staged file and
              the other attachments of the same email (approvals, extra
              timesheets) so everything can be cross-checked in one place. */}
          <div className="flex min-h-0 flex-col bg-slate-100">
            <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2">
              <FileText className="h-3.5 w-3.5 text-slate-400" />
              <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-600">
                {activeSource ? activeSource.filename : file.filename}
              </span>
              <a
                href={activeSource ? activeSource.url : pipelineRawUrl(file.id)}
                download={(activeSource ? activeSource.filename : file.filename) ?? "file"}
                className="rounded p-1 text-slate-400 hover:text-brand-600"
                title="Download original"
              >
                <Download className="h-3.5 w-3.5" />
              </a>
            </div>
            {relatedSources.length > 0 && (
              <div className="flex max-h-24 shrink-0 flex-wrap items-center gap-1.5 overflow-y-auto border-b border-slate-200 bg-white/70 px-3 py-2">
                <button
                  type="button"
                  onClick={() => setActiveSourceId(null)}
                  className={cn(
                    "rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ring-inset transition-colors",
                    activeSourceId === null
                      ? "bg-brand-600 text-white ring-brand-600"
                      : "bg-white text-slate-600 ring-slate-200 hover:bg-brand-50"
                  )}
                >
                  Staged file
                </button>
                {relatedSources.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setActiveSourceId(s.id)}
                    title={s.filename}
                    className={cn(
                      "max-w-[180px] truncate rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ring-inset transition-colors",
                      activeSourceId === s.id
                        ? "bg-brand-600 text-white ring-brand-600"
                        : "bg-white text-slate-600 ring-slate-200 hover:bg-brand-50"
                    )}
                  >
                    {s.filename}
                  </button>
                ))}
              </div>
            )}
            <div className="min-h-0 flex-1 overflow-auto p-2">
              {activeSource ? (
                <SourcePreview
                  url={activeSource.url}
                  renderUrl={activeSource.renderUrl}
                  name={activeSource.filename}
                  ct={activeSource.ct}
                />
              ) : (
                <RawFilePreview file={file} />
              )}
            </div>
          </div>

        </div>
      </div>
    </div>,
    document.body
  );
}
