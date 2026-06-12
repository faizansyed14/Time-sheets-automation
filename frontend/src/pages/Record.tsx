import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BadgeCheck,
  CalendarDays,
  CheckCircle2,
  FileText,
  Layers,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import {
  approveRecord,
  deleteRecord,
  fetchRecord,
  fileContentUrl,
  recordSources,
  updateRecord,
  verifyRecord,
  MONTHS_LONG,
  type TimesheetRecord,
} from "../api/client";
import { cn, formatBytes, formatDateTime } from "../lib/utils";
import { FilePreviewModal, PreviewableFileRow } from "../components/FilePreview";
import { Badge, Button, Card, PageHeader, Spinner } from "../components/ui";
import type { PreviewFile } from "../lib/filePreview";
import { ApprovalBadge, ValidationBadge } from "../components/status";
import { useToast } from "../components/toast";

const BUCKETS: { key: keyof TimesheetRecord & string; field: string; label: string; tone: string }[] = [
  { key: "annual_leave_dates", field: "annual_leave_dates", label: "Annual leave", tone: "bg-indigo-50 text-indigo-700 ring-indigo-200" },
  { key: "remote_work_dates", field: "remote_work_dates", label: "Remote / WFH", tone: "bg-sky-50 text-sky-700 ring-sky-200" },
  { key: "sick_leave_dates", field: "sick_leave_dates", label: "Sick leave", tone: "bg-rose-50 text-rose-700 ring-rose-200" },
  { key: "unpaid_leave_dates", field: "unpaid_leave_dates", label: "Unpaid leave", tone: "bg-slate-100 text-slate-700 ring-slate-200" },
  { key: "absent_dates", field: "absent_dates", label: "Absent", tone: "bg-amber-50 text-amber-700 ring-amber-200" },
  { key: "public_holiday_dates", field: "public_holiday_dates", label: "Public holiday", tone: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
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

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  const [newDate, setNewDate] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<PreviewFile | null>(null);

  useEffect(() => {
    if (rec) {
      setDraft(Object.fromEntries(BUCKETS.map((b) => [b.field, [...(rec as any)[b.key]]])));
    }
  }, [rec, editing]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["record", id] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
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
      qc.invalidateQueries({ queryKey: ["dashboard"] });
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

      <div className="mb-5 flex flex-wrap items-center gap-2">
        <ValidationBadge status={rec.validation_status} />
        <ApprovalBadge status={rec.approval_status} />
        {rec.source_file_count > 1 && (
          <Badge tone="violet">
            <Layers className="h-3 w-3" /> {rec.source_file_count} files merged into this month
          </Badge>
        )}
        {rec.approval_detected && (
          <Badge tone="green">
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
    </div>
  );
}
