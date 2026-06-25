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
  Columns2,
  Download,
  FileText,
  Paperclip,
  PencilLine,
  Plus,
  Search,
  User,
  X,
} from "lucide-react";
import {
  fetchEmployeeMatcher,
  pipelineManualFix,
  pipelineRawUrl,
  MONTHS_LONG,
  type Employee,
  type PipelineFile,
} from "../api/client";
import { isEml, isPdf, isPreviewable } from "../lib/filePreview";
import { EmlPreviewPane } from "./FilePreview";
import { Button, Input, Select, Spinner } from "./ui";
import { cn, formatBytes } from "../lib/utils";
import { useToast } from "./toast";

// ---------------------------------------------------------------------------
// Leave bucket definitions
// ---------------------------------------------------------------------------
const BUCKETS = [
  { key: "annual",         label: "Annual leave",   tone: "bg-indigo-50 text-indigo-700 ring-indigo-200" },
  { key: "remote",         label: "Remote / WFH",   tone: "bg-sky-50 text-sky-700 ring-sky-200" },
  { key: "sick",           label: "Sick leave",     tone: "bg-rose-50 text-rose-700 ring-rose-200" },
  { key: "unpaid",         label: "Unpaid leave",   tone: "bg-slate-100 text-slate-700 ring-slate-200" },
  { key: "absent",         label: "Absent",         tone: "bg-amber-50 text-amber-700 ring-amber-200" },
  { key: "public_holiday", label: "Public holiday", tone: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
] as const;

const FILE_RE = /\.(pdf|docx|xlsx|png|jpe?g|eml)$/i;

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
// Raw file preview pane (right side)
// ---------------------------------------------------------------------------
function RawFilePreview({ file }: { file: PipelineFile }) {
  const url = pipelineRawUrl(file.id);
  const name = file.filename ?? "file";
  const ct = file.content_type ?? "";

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

// ---------------------------------------------------------------------------
// Main modal
// ---------------------------------------------------------------------------
export default function PipelineCompareFixModal({
  file,
  onClose,
  onSaved,
}: {
  file: PipelineFile | null;
  onClose: () => void;
  onSaved: () => void;
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
  const [newDate, setNewDate] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<File[]>([]);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);

  const { data: employees, isLoading } = useQuery({
    queryKey: ["employee-matcher"],
    queryFn: fetchEmployeeMatcher,
    enabled: !!file,
  });

  // Reset when a new file is opened.
  useEffect(() => {
    if (!file || file.id === prevFileId.current) return;
    prevFileId.current = file.id;
    setEmployeeQ(file.employee_id ?? file.employee_name ?? "");
    setEmployeePk("");
    setMonth(file.month ?? new Date().getMonth() + 1);
    setYear(file.year ?? new Date().getFullYear());
    setDates({});
    setNewDate({});
    setAttachments([]);
    setNote("");
    setPending(false);
  }, [file]);

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

  const addDate = (key: string) => {
    const v = newDate[key];
    if (!v) return;
    setDates((d) => ({ ...d, [key]: [...new Set([...(d[key] ?? []), v])].sort() }));
    setNewDate((n) => ({ ...n, [key]: "" }));
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
            Compare &amp; Fix — {file.filename}
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

            {/* Error banner */}
            <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm leading-5 text-rose-800">
              <p className="font-semibold">{file.failure_label ?? "Failed"}</p>
              <p className="mt-0.5">{file.failure_detail}</p>
            </div>

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
                    <span className="inline-flex items-center gap-1">
                      <input
                        type="date"
                        value={newDate[b.key] ?? ""}
                        onChange={(e) => setNewDate((n) => ({ ...n, [b.key]: e.target.value }))}
                        className="rounded-md border border-slate-300 px-2 py-1 text-[11px] focus:border-brand-500 focus:outline-none"
                      />
                      <button
                        type="button"
                        onClick={() => addDate(b.key)}
                        className="rounded-md bg-brand-600 p-1 text-white hover:bg-brand-700"
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  </div>
                </div>
              ))}
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
              <Button variant="secondary" onClick={onClose} disabled={pending}>
                Cancel
              </Button>
              <Button disabled={!canSave} onClick={handleSave}>
                {pending ? (
                  <Spinner className="border-white/40 border-t-white" />
                ) : (
                  <PencilLine className="h-4 w-4" />
                )}
                Save &amp; file record
              </Button>
            </div>
          </div>

          {/* RIGHT — original file preview */}
          <div className="flex min-h-0 flex-col bg-slate-100">
            <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2">
              <FileText className="h-3.5 w-3.5 text-slate-400" />
              <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-600">
                {file.filename}
              </span>
              <a
                href={pipelineRawUrl(file.id)}
                download={file.filename ?? "file"}
                className="rounded p-1 text-slate-400 hover:text-brand-600"
                title="Download original"
              >
                <Download className="h-3.5 w-3.5" />
              </a>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-2">
              <RawFilePreview file={file} />
            </div>
          </div>

        </div>
      </div>
    </div>,
    document.body
  );
}
