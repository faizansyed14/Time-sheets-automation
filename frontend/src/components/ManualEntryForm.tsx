import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  FileText,
  Paperclip,
  PencilLine,
  Plus,
  Search,
  UploadCloud,
  UserCheck,
  X,
} from "lucide-react";
import {
  fetchEmployeeMatcher,
  uploadManual,
  MONTHS_LONG,
  type Employee,
  type UploadResult,
} from "../api/client";
import { isPdf, isPreviewable } from "../lib/filePreview";
import { cn, formatBytes, initials, avatarColor } from "../lib/utils";
import { Button, Card, Field, Select } from "./ui";
import { useToast } from "./toast";

const BUCKETS: { key: string; label: string; tone: string }[] = [
  { key: "annual",         label: "Annual leave",   tone: "bg-indigo-50 text-indigo-700 ring-indigo-200" },
  { key: "remote",         label: "Remote / WFH",   tone: "bg-sky-50 text-sky-700 ring-sky-200" },
  { key: "sick",           label: "Sick leave",     tone: "bg-rose-50 text-rose-700 ring-rose-200" },
  { key: "maternity",      label: "Maternity leave", tone: "bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200" },
  { key: "unpaid",         label: "Unpaid leave",   tone: "bg-slate-100 text-slate-700 ring-slate-200" },
  { key: "absent",         label: "Absent",         tone: "bg-amber-50 text-amber-700 ring-amber-200" },
  { key: "public_holiday", label: "Public holiday", tone: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
];

const NOW = new Date();
const FILE_RE = /\.(pdf|docx|xlsx|png|jpe?g|eml)$/i;

// ---------------------------------------------------------------------------
// Local-file preview pane (no upload — pure object URL)
// ---------------------------------------------------------------------------
function LocalFilePreview({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    const u = URL.createObjectURL(file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);

  if (!url) return null;

  if (isPdf(file.name)) {
    return (
      <iframe
        src={url}
        title={file.name}
        className="h-full w-full rounded-lg border border-slate-200 bg-white"
      />
    );
  }

  if (isPreviewable(file.name)) {
    return (
      <img
        src={url}
        alt={file.name}
        className="mx-auto block max-h-full max-w-full rounded-lg object-contain"
      />
    );
  }

  // EML or unsupported — show a friendly card
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 rounded-lg border border-slate-200 bg-white p-8 text-center">
      <FileText className="h-10 w-10 text-slate-300" />
      <p className="font-medium text-slate-700">{file.name}</p>
      <p className="text-sm text-slate-400">{formatBytes(file.size)}</p>
      <p className="text-xs text-slate-400">
        Preview unavailable — file will be attached when the record is saved.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main form
// ---------------------------------------------------------------------------
export default function ManualEntryForm({ onResult }: { onResult: (r: UploadResult) => void }) {
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: employees } = useQuery({ queryKey: ["employee-matcher"], queryFn: fetchEmployeeMatcher });
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Employee | null>(null);
  const [month, setMonth] = useState(NOW.getMonth() + 1);
  const [year, setYear] = useState(NOW.getFullYear());
  const [dates, setDates] = useState<Record<string, string[]>>({});
  const [newDate, setNewDate] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<File[]>([]);
  const [previewIdx, setPreviewIdx] = useState(0);
  const [note, setNote] = useState("");

  // Keep preview index in bounds when files change.
  useEffect(() => {
    setPreviewIdx((i) => (files.length ? Math.min(i, files.length - 1) : 0));
  }, [files]);

  const matches = useMemo(() => {
    if (!picked && q.trim()) {
      const t = q.toLowerCase();
      return (employees ?? [])
        .filter((e) => e.name.toLowerCase().includes(t) || e.employee_id.toLowerCase().includes(t))
        .slice(0, 8);
    }
    return [];
  }, [employees, q, picked]);

  const totalDays = BUCKETS.reduce((a, b) => a + (dates[b.key]?.length ?? 0), 0);

  const addDate = (key: string) => {
    const v = newDate[key];
    if (!v) return;
    setDates((d) => ({ ...d, [key]: [...new Set([...(d[key] ?? []), v])].sort() }));
    setNewDate((n) => ({ ...n, [key]: "" }));
  };

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const valid = Array.from(list).filter((f) => FILE_RE.test(f.name));
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...valid.filter((f) => !names.has(f.name))];
    });
  };

  const removeFile = (name: string) => {
    setFiles((prev) => {
      const next = prev.filter((f) => f.name !== name);
      return next;
    });
  };

  const submit = useMutation({
    mutationFn: () =>
      uploadManual({ employee_pk: picked!.id, month, year, buckets: dates, note, files }),
    onSuccess: (r) => {
      toast(
        r.status === "success" ? "success" : "warning",
        r.status === "success" ? "Record created" : "Record created — needs review",
        `${r.employee_name} — ${MONTHS_LONG[r.month ?? 0]} ${r.year}`
      );
      onResult(r);
      setPicked(null); setQ(""); setDates({}); setFiles([]); setNote("");
      setPreviewIdx(0);
    },
    onError: (e: any) =>
      toast("error", "Could not create record", e?.response?.data?.detail ?? String(e)),
  });

  const years = [year, NOW.getFullYear(), NOW.getFullYear() - 1, NOW.getFullYear() - 2].filter(
    (v, i, a) => a.indexOf(v) === i
  );

  const previewFile = files[previewIdx] ?? null;

  return (
    <Card className="overflow-hidden p-0">
      <div className="grid min-h-[520px] grid-cols-1 lg:grid-cols-2">

        {/* LEFT — full form */}
        <div className="overflow-y-auto p-6 lg:border-r lg:border-slate-100">

          {/* Employee */}
          <Field label="Employee (from matcher)">
            {picked ? (
              <div className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <span className={cn("flex h-8 w-8 items-center justify-center rounded-full text-[11px] font-bold", avatarColor(picked.name))}>
                  {initials(picked.name)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-slate-800">{picked.name}</p>
                  <p className="text-xs text-slate-500">
                    {picked.employee_id}{picked.location ? ` · ${picked.location}` : ""}
                  </p>
                </div>
                <button
                  onClick={() => setPicked(null)}
                  className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-500"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search name or ID…"
                  className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-8 pr-3 text-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/10"
                />
                {matches.length > 0 && (
                  <div className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-pop">
                    {matches.map((e) => (
                      <button
                        key={e.id}
                        onClick={() => { setPicked(e); setQ(""); }}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50"
                      >
                        <UserCheck className="h-4 w-4 shrink-0 text-slate-400" />
                        <span className="min-w-0 flex-1 truncate">{e.name}</span>
                        <span className="shrink-0 font-mono text-xs text-slate-400">
                          {e.employee_id}{e.location ? ` · ${e.location}` : ""}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </Field>

          {/* Period */}
          <div className="mt-3 grid grid-cols-2 gap-3">
            <Field label="Month">
              <Select className="w-full" value={month} onChange={(e) => setMonth(Number(e.target.value))}>
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                  <option key={m} value={m}>{MONTHS_LONG[m]}</option>
                ))}
              </Select>
            </Field>
            <Field label="Year">
              <Select className="w-full" value={year} onChange={(e) => setYear(Number(e.target.value))}>
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </Select>
            </Field>
          </div>

          {/* Leave buckets */}
          <div className="mt-4 space-y-3">
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
          <div className="mt-4">
            <p className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
              Attach files (optional)
            </p>
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.eml"
              className="hidden"
              onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
            />
            <Button variant="secondary" size="sm" onClick={() => fileRef.current?.click()}>
              <Paperclip className="h-4 w-4" /> Add files
            </Button>
            {files.length > 0 && (
              <div className="mt-2 space-y-1">
                {files.map((f, i) => (
                  <button
                    key={f.name}
                    type="button"
                    onClick={() => setPreviewIdx(i)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors",
                      i === previewIdx
                        ? "border-brand-300 bg-brand-50"
                        : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"
                    )}
                  >
                    <FileText className={cn("h-4 w-4 shrink-0", i === previewIdx ? "text-brand-600" : "text-slate-400")} />
                    <span className="min-w-0 flex-1 truncate text-xs text-slate-700">{f.name}</span>
                    <span className="text-[11px] text-slate-400">{formatBytes(f.size)}</span>
                    <span
                      role="button"
                      onClick={(e) => { e.stopPropagation(); removeFile(f.name); }}
                      className="rounded p-0.5 text-slate-400 hover:text-rose-500"
                    >
                      <X className="h-3.5 w-3.5" />
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Note + submit */}
          <div className="mt-5 border-t border-slate-100 pt-4">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Note (optional)…"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/10"
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <span className="text-xs text-slate-400">{totalDays} day(s)</span>
              <Button disabled={!picked || submit.isPending} onClick={() => submit.mutate()}>
                <PencilLine className="h-4 w-4" />
                {submit.isPending ? "Creating…" : "Create record"}
              </Button>
            </div>
          </div>
        </div>

        {/* RIGHT — file preview */}
        <div className="hidden min-h-0 flex-col bg-slate-100 lg:flex">
          {previewFile ? (
            <>
              {/* file tab bar */}
              {files.length > 1 && (
                <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-200 bg-white px-2 pt-2">
                  {files.map((f, i) => (
                    <button
                      key={f.name}
                      onClick={() => setPreviewIdx(i)}
                      className={cn(
                        "shrink-0 rounded-t-md px-3 py-1.5 text-xs font-medium",
                        i === previewIdx
                          ? "border border-b-white border-slate-200 bg-white text-brand-700"
                          : "text-slate-500 hover:text-slate-700"
                      )}
                    >
                      {f.name.length > 22 ? f.name.slice(0, 20) + "…" : f.name}
                    </button>
                  ))}
                </div>
              )}
              <div className="min-h-0 flex-1 overflow-hidden p-2">
                <LocalFilePreview file={previewFile} />
              </div>
            </>
          ) : (
            /* empty state */
            <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-400">
              <UploadCloud className="h-10 w-10 text-slate-300" />
              <p className="text-sm font-medium">Attach a file to preview it here</p>
              <p className="text-xs">PDF, image, EML, DOCX or XLSX</p>
            </div>
          )}
        </div>

      </div>
    </Card>
  );
}
