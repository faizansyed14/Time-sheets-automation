import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BadgeCheck,
  CalendarDays,
  CheckCircle2,
  Columns2,
  Download,
  FileText,
  Layers,
  Pencil,
  Plus,
  Save,
  ShieldCheck,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { isDocx, isEml, isPdf, isPreviewable, isXlsx } from "../lib/filePreview";
import {
  approveRecord,
  deleteRecord,
  fetchEmployeeRecords,
  fetchRecord,
  fileContentUrl,
  fileRenderUrl,
  recordSources,
  updateRecord,
  verifyRecord,
  MONTHS_LONG,
  type TimesheetRecord,
} from "../api/client";
import { cn, formatBytes, formatDateTime } from "../lib/utils";
import { EmlPreviewPane, FilePreviewModal, PreviewableFileRow, ServerRenderPane } from "../components/FilePreview";
import { Badge, Button, Card, PageHeader, Spinner } from "../components/ui";
import type { PreviewFile } from "../lib/filePreview";
import { ApprovalBadge, ValidationBadge } from "../components/status";
import { useToast } from "../components/toast";

import { LEAVE_BUCKET_LABELS, LEAVE_BUCKET_TONE } from "../lib/theme";

const BUCKETS: { key: keyof TimesheetRecord & string; field: string; label: string; tone: string }[] = [
  { key: "annual_leave_dates", field: "annual_leave_dates", label: LEAVE_BUCKET_LABELS.annual!, tone: LEAVE_BUCKET_TONE.annual! },
  { key: "remote_work_dates", field: "remote_work_dates", label: LEAVE_BUCKET_LABELS.remote!, tone: LEAVE_BUCKET_TONE.remote! },
  { key: "sick_leave_dates", field: "sick_leave_dates", label: LEAVE_BUCKET_LABELS.sick!, tone: LEAVE_BUCKET_TONE.sick! },
  { key: "maternity_leave_dates", field: "maternity_leave_dates", label: LEAVE_BUCKET_LABELS.maternity!, tone: LEAVE_BUCKET_TONE.maternity! },
  { key: "unpaid_leave_dates", field: "unpaid_leave_dates", label: LEAVE_BUCKET_LABELS.unpaid!, tone: LEAVE_BUCKET_TONE.unpaid! },
  { key: "absent_dates", field: "absent_dates", label: LEAVE_BUCKET_LABELS.absent!, tone: LEAVE_BUCKET_TONE.absent! },
  { key: "public_holiday_dates", field: "public_holiday_dates", label: LEAVE_BUCKET_LABELS.public_holiday!, tone: LEAVE_BUCKET_TONE.public_holiday! },
];

export default function RecordPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();

  const { data: rec, isLoading } = useQuery({
    queryKey: ["record", id],
    queryFn: () => fetchRecord(id!),
    enabled: !!id,
  });
  const { data: sources } = useQuery({
    queryKey: ["record-sources", id],
    queryFn: () => recordSources(id!),
    enabled: !!id,
  });
  const employeePk = rec?.matched_employee_pk
    || (rec?.employee_name ? `unmatched::${rec.employee_name.toLowerCase()}` : null);
  const { data: siblingRecords } = useQuery({
    queryKey: ["employee-records", employeePk],
    queryFn: () => fetchEmployeeRecords(employeePk!),
    enabled: !!employeePk,
  });

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  const [newDate, setNewDate] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);
  const [activeFile, setActiveFile] = useState(0);

  useEffect(() => {
    if (rec) {
      setDraft(Object.fromEntries(BUCKETS.map((b) => [b.field, [...(rec as any)[b.key]]])));
    }
  }, [rec, editing, compareOpen]);

  // lock background scroll while the compare overlay is open
  useEffect(() => {
    if (!compareOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setCompareOpen(false);
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [compareOpen]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["record", id] });
    qc.invalidateQueries({ queryKey: ["coverage"] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
  };

  const saveMut = useMutation({
    mutationFn: () => updateRecord(id!, draft as any),
    onSuccess: (r) => {
      toast(
        r.validation_status === "verified" ? "success" : "warning",
        r.validation_status === "verified" ? "Saved — validation clean" : "Saved — still flagged",
        r.llm_summary ?? undefined
      );
      setEditing(false);
      invalidate();
    },
    onError: (e: any) => toast("error", "Save failed", e?.response?.data?.detail ?? String(e)),
  });

  const verifyMut = useMutation({
    mutationFn: () => verifyRecord(id!),
    onSuccess: () => {
      toast("success", "Marked verified");
      invalidate();
    },
  });

  const approveMut = useMutation({
    mutationFn: (approved: boolean) => approveRecord(id!, approved),
    onSuccess: (r) => {
      toast("success", r.approval_status === "approved" ? "Approved" : "Marked not approved");
      invalidate();
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteRecord(id!),
    onSuccess: () => {
      toast("info", "Record deleted");
      qc.invalidateQueries({ queryKey: ["coverage"] });
      navigate("/");
    },
  });

  if (isLoading || !rec)
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );

  const totalDays = BUCKETS.reduce((a, b) => a + ((rec as any)[b.key]?.length ?? 0), 0);

  return (
    <div className="mx-auto max-w-5xl animate-fade-up">
      <Link
        to="/"
        className="mb-3 inline-flex items-center gap-1 text-xs font-semibold text-slate-500 hover:text-brand-600"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to dashboard
      </Link>

      <PageHeader
        title={`${rec.employee_name ?? "Unknown"} — ${MONTHS_LONG[rec.month]} ${rec.year}`}
        subtitle={`${rec.employee_id ?? "no ID"} · ${rec.account_manager ?? "no manager"} · ${totalDays} leave/holiday day(s) · ${rec.calendar_days ?? "?"} calendar days`}
        actions={
          <>
            <Button variant="secondary" onClick={() => setCompareOpen(true)}>
              <Columns2 className="h-4 w-4" /> Compare
            </Button>
            {rec.validation_status === "manual_review" && (
              <Button variant="success" onClick={() => verifyMut.mutate()}>
                <ShieldCheck className="h-4 w-4" /> Mark verified
              </Button>
            )}
            <Button
              variant="ghost"
              className="text-rose-500 hover:bg-rose-50"
              onClick={() => {
                if (confirm("Delete this monthly record?")) deleteMut.mutate();
              }}
            >
              <Trash2 className="h-4 w-4" /> Delete
            </Button>
          </>
        }
      />

      {!!siblingRecords?.length && (
        <div className="mb-4 flex flex-wrap gap-2">
          {siblingRecords.map((r) => {
            const active = r.id === rec.id;
            return (
              <button
                key={r.id}
                type="button"
                onClick={() => navigate(`/records/${r.id}`)}
                className={cn(
                  "rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors",
                  active
                    ? "border-brand-300 bg-brand-50 text-brand-700"
                    : "border-slate-200 bg-white text-slate-600 hover:border-brand-200 hover:bg-brand-50/40"
                )}
                title={`${MONTHS_LONG[r.month]} ${r.year}`}
              >
                {MONTHS_LONG[r.month].slice(0, 3)} {r.year}
              </button>
            );
          })}
        </div>
      )}

      <div className="mb-5 flex flex-wrap items-center gap-2">
        <ValidationBadge status={rec.validation_status} />
        <ApprovalBadge status={rec.approval_status} />
        {rec.source_file_count > 1 && (
          <Badge tone="brand">
            <Layers className="h-3 w-3" /> {rec.source_file_count} files merged into this month
          </Badge>
        )}
        {rec.approval_detected && (
          <Badge tone="success">
            <BadgeCheck className="h-3 w-3" /> Approval screenshot detected
          </Badge>
        )}
      </div>

      {(rec.hr_flags?.length ?? 0) > 0 && (
        <Card className="mb-5 border-amber-200 bg-amber-50/70 p-4">
          <p className="mb-1 text-xs font-bold uppercase tracking-wide text-amber-700">
            Review flags
          </p>
          <ul className="list-inside list-disc space-y-1 text-sm text-amber-800">
            {rec.hr_flags.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* ---------------- buckets ---------------- */}
        <Card className="lg:col-span-2">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
            <h2 className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <CalendarDays className="h-4 w-4 text-slate-400" /> Leave buckets
            </h2>
            {editing ? (
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
                <Button size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
                  Save &amp; re-validate
                </Button>
              </div>
            ) : (
              <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
                <Pencil className="h-3.5 w-3.5" /> Edit dates
              </Button>
            )}
          </div>
          <div className="space-y-4 p-5">
            {BUCKETS.map((b) => {
              const dates: string[] = editing ? (draft[b.field] ?? []) : ((rec as any)[b.key] ?? []);
              return (
                <div key={b.key}>
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {b.label} <span className="text-slate-300">·</span>{" "}
                    <span className="text-slate-400">{dates.length}</span>
                  </p>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {dates.length === 0 && !editing && (
                      <span className="text-xs text-slate-300">none</span>
                    )}
                    {dates.map((d) => (
                      <span
                        key={d}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-md px-2 py-1 font-mono text-[11px] font-medium ring-1 ring-inset",
                          b.tone
                        )}
                      >
                        {d}
                        {editing && (
                          <button
                            onClick={() =>
                              setDraft((dr) => ({
                                ...dr,
                                [b.field]: (dr[b.field] ?? []).filter((x) => x !== d),
                              }))
                            }
                            className="opacity-60 hover:opacity-100"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        )}
                      </span>
                    ))}
                    {editing && (
                      <span className="inline-flex items-center gap-1">
                        <input
                          type="date"
                          value={newDate[b.field] ?? ""}
                          onChange={(e) => setNewDate((n) => ({ ...n, [b.field]: e.target.value }))}
                          className="rounded-md border border-slate-300 px-2 py-1 text-[11px] focus:border-brand-500 focus:outline-none"
                        />
                        <button
                          onClick={() => {
                            const v = newDate[b.field];
                            if (!v) return;
                            setDraft((dr) => ({
                              ...dr,
                              [b.field]: [...new Set([...(dr[b.field] ?? []), v])].sort(),
                            }));
                            setNewDate((n) => ({ ...n, [b.field]: "" }));
                          }}
                          className="rounded-md bg-brand-600 p-1 text-white hover:bg-brand-700"
                        >
                          <Plus className="h-3.5 w-3.5" />
                        </button>
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        {/* ---------------- side panel ---------------- */}
        <div className="space-y-5">
          <Card className="p-5">
            <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-slate-500">
              Manager sign-off
            </h3>
            <p className="mb-3 text-xs leading-5 text-slate-500">{rec.approval_detail}</p>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={rec.approval_status === "approved" ? "success" : "secondary"}
                onClick={() => approveMut.mutate(true)}
              >
                <CheckCircle2 className="h-4 w-4" /> Approve
              </Button>
              <Button
                size="sm"
                variant={rec.approval_status === "not_approved" ? "danger" : "secondary"}
                onClick={() => approveMut.mutate(false)}
              >
                <XCircle className="h-4 w-4" /> Not approved
              </Button>
            </div>
          </Card>

          <Card className="p-5">
            <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-slate-500">
              Identity match
            </h3>
            <p className="text-sm leading-6 text-slate-600">{rec.match_note}</p>
            {rec.llm_summary && (
              <p className="mt-3 border-t border-slate-100 pt-3 text-xs leading-5 text-slate-500">
                {rec.llm_summary}
              </p>
            )}
          </Card>

          <Card className="p-5">
            <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-slate-500">
              Contributing files ({rec.source_file_count})
            </h3>
            <div className="space-y-2.5">
              {(rec.source_files ?? []).map((e, i) => (
                <div key={e.key ?? i} className="rounded-lg border border-slate-200 p-2.5">
                  <p className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                    <span className="truncate">{e.filename}</span>
                  </p>
                  <p className="mt-0.5 text-[11px] text-slate-400">
                    {formatDateTime(e.ingested_at)} ·{" "}
                    {Object.values(e.buckets ?? {}).reduce((a, v) => a + v.length, 0)} day(s)
                  </p>
                </div>
              ))}
              {rec.source_file_count === 0 && (
                <p className="text-xs text-slate-400">No file breakdown stored.</p>
              )}
            </div>
          </Card>

          <Card className="p-5">
            <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-slate-500">
              Stored files
            </h3>
            <div className="space-y-1.5">
              {(sources ?? []).map((s) => {
                const file: PreviewFile = {
                  url: fileContentUrl(s.rel_path),
                  filename: s.name,
                  contentType: s.content_type,
                  renderUrl: fileRenderUrl(s.rel_path),
                };
                return (
                  <PreviewableFileRow
                    key={s.rel_path}
                    file={file}
                    onPreview={setPreview}
                    icon={<FileText className="h-4 w-4 shrink-0 text-slate-400" />}
                    meta={<span className="text-[11px] text-slate-400">{formatBytes(s.size)}</span>}
                    className="border-transparent px-2 py-1.5 hover:border-transparent"
                  />
                );
              })}
              {!sources?.length && <p className="text-xs text-slate-400">Nothing filed on disk.</p>}
            </div>
          </Card>
        </div>
      </div>

      <FilePreviewModal file={preview} onClose={() => setPreview(null)} />

      {compareOpen &&
        createPortal(
          <div className="fixed inset-0 z-50 flex flex-col p-2 sm:p-4">
            <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm animate-overlay-in" onClick={() => setCompareOpen(false)} />
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl bg-white shadow-pop animate-scale-in">
              {/* header */}
              <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 bg-slate-50 px-4 py-3">
                <Columns2 className="h-4 w-4 text-slate-400" />
                <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-700">
                  Compare — {rec.employee_name} · {MONTHS_LONG[rec.month]} {rec.year}
                </span>
                <Button size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
                  <Save className="h-3.5 w-3.5" /> Save &amp; re-validate
                </Button>
                <button onClick={() => setCompareOpen(false)} aria-label="Close" className="rounded-lg p-1.5 text-slate-400 hover:bg-white hover:text-slate-600">
                  <X className="h-5 w-5" />
                </button>
              </div>

              {/* two panes */}
              <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-2">
                {/* LEFT — editable extracted data + flags */}
                <div className="min-h-0 overflow-y-auto border-b border-slate-100 p-5 lg:border-b-0 lg:border-r">
                  <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-slate-500">Extracted data (editable)</h3>
                  {(rec.hr_flags?.length ?? 0) > 0 && (
                    <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50/70 p-3">
                      <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-amber-700">Review flags</p>
                      <ul className="list-inside list-disc space-y-1 text-xs text-amber-800">
                        {rec.hr_flags.map((f, i) => <li key={i}>{f}</li>)}
                      </ul>
                    </div>
                  )}
                  <div className="space-y-4">
                    {BUCKETS.map((b) => {
                      const ds: string[] = draft[b.field] ?? [];
                      return (
                        <div key={b.key}>
                          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                            {b.label} <span className="text-slate-400">· {ds.length}</span>
                          </p>
                          <div className="flex flex-wrap items-center gap-1.5">
                            {ds.map((d) => (
                              <span key={d} className={cn("inline-flex items-center gap-1 rounded-md px-2 py-1 font-mono text-[11px] font-medium ring-1 ring-inset", b.tone)}>
                                {d}
                                <button onClick={() => setDraft((dr) => ({ ...dr, [b.field]: (dr[b.field] ?? []).filter((x) => x !== d) }))} className="opacity-60 hover:opacity-100">
                                  <X className="h-3 w-3" />
                                </button>
                              </span>
                            ))}
                            <span className="inline-flex items-center gap-1">
                              <input type="date" value={newDate[b.field] ?? ""} onChange={(e) => setNewDate((n) => ({ ...n, [b.field]: e.target.value }))}
                                className="rounded-md border border-slate-300 px-2 py-1 text-[11px] focus:border-brand-500 focus:outline-none" />
                              <button
                                onClick={() => {
                                  const v = newDate[b.field];
                                  if (!v) return;
                                  setDraft((dr) => ({ ...dr, [b.field]: [...new Set([...(dr[b.field] ?? []), v])].sort() }));
                                  setNewDate((n) => ({ ...n, [b.field]: "" }));
                                }}
                                className="rounded-md bg-brand-600 p-1 text-white hover:bg-brand-700">
                                <Plus className="h-3.5 w-3.5" />
                              </button>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* RIGHT — source file previews */}
                <div className="flex min-h-0 flex-col bg-slate-100">
                  {!sources?.length ? (
                    <div className="flex flex-1 items-center justify-center text-sm text-slate-400">No files filed for this record.</div>
                  ) : (
                    <>
                      <div className="flex shrink-0 flex-wrap gap-1.5 border-b border-slate-200 bg-white p-2">
                        {sources.map((s, i) => (
                          <button key={s.rel_path} onClick={() => setActiveFile(i)}
                            className={cn("flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium", i === activeFile ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200")}>
                            <FileText className="h-3.5 w-3.5" />
                            <span className="max-w-[160px] truncate">{s.name}</span>
                          </button>
                        ))}
                      </div>
                      {(() => {
                        const s = sources[Math.min(activeFile, sources.length - 1)];
                        const url = fileContentUrl(s.rel_path);
                        const renderUrl = fileRenderUrl(s.rel_path);
                        return (
                          <div className="min-h-0 flex-1 overflow-auto p-2">
                            {isEml(s.name, s.content_type) ? (
                              <div className="h-full min-h-[60vh] overflow-hidden rounded-lg border border-slate-200 bg-white">
                                <EmlPreviewPane fileUrl={url} filename={s.name} />
                              </div>
                            ) : isPdf(s.name, s.content_type) ? (
                              // Native browser PDF viewer — zoom, search, print.
                              <iframe
                                src={url}
                                title={s.name}
                                className="h-full min-h-[60vh] w-full rounded-lg border border-slate-200 bg-white"
                              />
                            ) : isDocx(s.name, s.content_type) || isXlsx(s.name, s.content_type) ? (
                              <div className="h-full min-h-[60vh] overflow-hidden rounded-lg border border-slate-200 bg-white">
                                <ServerRenderPane
                                  renderUrl={renderUrl}
                                  sourceUrl={url}
                                  filename={s.name}
                                  contentType={s.content_type}
                                />
                              </div>
                            ) : isPreviewable(s.name, s.content_type) ? (
                              <img src={url} alt={s.name} className="mx-auto block max-h-full min-h-[40vh] max-w-full rounded-lg object-contain" />
                            ) : (
                              <div className="flex h-full min-h-[40vh] flex-col items-center justify-center gap-3 text-slate-500">
                                <FileText className="h-10 w-10 text-slate-300" />
                                <p className="text-sm">{s.name} can't be previewed inline.</p>
                                <a href={url} download={s.name} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50">
                                  <Download className="h-4 w-4" /> Download
                                </a>
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
