import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  UploadCloud,
  FileText,
  X,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Loader2,
  Info,
  Trash2,
} from "lucide-react";
import {
  MONTHS_LONG,
  portalDeleteFile,
  portalDiscardDraft,
  portalPrecheck,
  portalSubmit,
  portalUploadFile,
  portalUpsertSubmission,
  type PortalFileKind,
  type PortalSubmission,
} from "../../api/client";
import { Button, Card, PageHeader, Select, Field } from "../../components/ui";
import { useToast } from "../../components/toast";
import { cn, formatBytes } from "../../lib/utils";
import PortalShell from "./PortalShell";

// No slot is individually mandatory — a sick-leave certificate or other
// document filed on its own (no timesheet at all) is a complete,
// submittable submission. Only having zero files at all blocks Submit.
const SLOTS: { kind: PortalFileKind; label: string; required: boolean }[] = [
  { kind: "timesheet", label: "Timesheet", required: false },
  { kind: "sick_leave", label: "Sick leave certificate", required: false },
  { kind: "other", label: "Other document", required: false },
];

function UploadSlot({
  kind,
  label,
  required,
  submissionId,
  file,
  disabled,
  onChanged,
}: {
  kind: PortalFileKind;
  label: string;
  required: boolean;
  submissionId: string;
  file: { filename: string; size_bytes: number | null } | undefined;
  disabled: boolean;
  onChanged: (result: PortalSubmission) => void;
}) {
  const { toast } = useToast();
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);

  const upload = async (f: File) => {
    setBusy(true);
    try {
      onChanged(await portalUploadFile(submissionId, kind, f));
    } catch (e: any) {
      toast("error", "Upload failed", e?.response?.data?.detail || String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      onChanged(await portalDeleteFile(submissionId, kind));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (disabled) return;
        const f = e.dataTransfer.files[0];
        if (f) upload(f);
      }}
      className={cn(
        "rounded-lg border-2 border-dashed p-4 transition-colors",
        dragging ? "border-brand-400 bg-brand-50" : "border-slate-200",
        disabled && "opacity-60"
      )}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-800">
            {label} {required && <span className="text-rose-500">*</span>}
          </p>
          {file ? (
            <p className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-500">
              <FileText className="h-3.5 w-3.5" />
              {file.filename} {file.size_bytes != null && `· ${formatBytes(file.size_bytes)}`}
            </p>
          ) : (
            <p className="mt-0.5 text-xs text-slate-400">Drag a file here, or click to choose one.</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {busy && <Loader2 className="h-4 w-4 animate-spin text-slate-400" />}
          {file ? (
            <button
              onClick={remove}
              disabled={disabled || busy}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-rose-600 disabled:opacity-50"
            >
              <X className="h-4 w-4" />
            </button>
          ) : (
            <label
              className={cn(
                "cursor-pointer rounded-lg px-2.5 py-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-50",
                disabled && "pointer-events-none opacity-50"
              )}
            >
              <UploadCloud className="mr-1 inline h-3.5 w-3.5" />
              Choose file
              <input
                type="file"
                className="hidden"
                accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload(f);
                  e.target.value = "";
                }}
              />
            </label>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PortalEmployeeUpload() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const now = new Date();
  // Deliberately NO default period — a pre-filled dropdown is too easy to
  // submit under without noticing, and the wrong month is hard to undo.
  // The employee must actively pick both before anything else is possible.
  const [month, setMonth] = useState<number | "">("");
  const [year, setYear] = useState<number | "">("");
  const [note, setNote] = useState("");
  // The mandatory approval self-attestation — null means "not yet answered",
  // which blocks Submit. Reset whenever we land on a different submission
  // row (new draft, or a fresh visit) so it's never silently pre-answered.
  const [approvalAnswer, setApprovalAnswer] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const periodChosen = month !== "" && year !== "";

  const { data: precheck, isLoading } = useQuery({
    queryKey: ["portal-precheck", month, year],
    queryFn: () => portalPrecheck(month as number, year as number),
    enabled: periodChosen,
  });

  useEffect(() => {
    setNote(precheck?.submission?.employee_note || "");
    setApprovalAnswer(null);
  }, [precheck?.submission?.id]);

  const submission = precheck?.submission;
  // Files are ALWAYS editable once a submission row exists — a certificate
  // that arrives days after the timesheet itself is the normal case, not an
  // edge case. Adding/removing a file after the first send automatically
  // re-queues it for the manager (see the backend's _requeue_for_review) —
  // there's no locked state to work around here.
  const isDraft = submission?.status === "draft";
  const isRejected = submission?.status === "rejected";
  // Only the FIRST send needs an explicit Submit click; every later file
  // change re-queues itself.
  const needsExplicitSubmit = isDraft || isRejected;
  const filesByKind = Object.fromEntries((submission?.files || []).map((f) => [f.kind, f]));
  // No slot is individually mandatory — one file of any kind is a complete
  // submission (e.g. a sick-leave certificate on its own, no timesheet).
  const hasAnyFile = (submission?.files || []).length > 0;

  const ensureDraft = async () => {
    if (submission) return submission.id;
    const created = await portalUpsertSubmission({ month: month as number, year: year as number, employee_note: note });
    await qc.invalidateQueries({ queryKey: ["portal-precheck", month, year] });
    return created.id;
  };

  const [pendingSubmissionId, setPendingSubmissionId] = useState<string | null>(null);

  useEffect(() => {
    setPendingSubmissionId(null);
  }, [month, year]);

  const submissionId = submission?.id || pendingSubmissionId;

  const refreshPrecheck = () => qc.invalidateQueries({ queryKey: ["portal-precheck", month, year] });
  const onSlotChanged = (result: PortalSubmission) => {
    refreshPrecheck();
    qc.invalidateQueries({ queryKey: ["portal-submissions"] });
    // A change on a submission already past "draft" auto-requeues it — worth
    // saying so explicitly, since nothing else on screen makes that obvious.
    if (result.status !== "draft") {
      toast("info", "Sent for review again", "Your manager and the timesheet team will see this update.");
    }
  };

  const doSubmit = async () => {
    if (!submissionId || approvalAnswer === null) return;
    setSubmitting(true);
    try {
      await portalSubmit(submissionId, approvalAnswer);
      toast("success", "Submitted", "Your timesheet is being read now — check My submissions for status.");
      refreshPrecheck();
      qc.invalidateQueries({ queryKey: ["portal-submissions"] });
    } catch (e: any) {
      toast("error", "Couldn't submit", e?.response?.data?.detail || String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const doDiscard = async () => {
    if (!submissionId) return;
    if (!confirm(`Discard this draft for ${MONTHS_LONG[month as number]} ${year}? Any files you've attached will be removed.`)) return;
    setDiscarding(true);
    try {
      await portalDiscardDraft(submissionId);
      toast("info", "Draft discarded");
      setPendingSubmissionId(null);
      refreshPrecheck();
      qc.invalidateQueries({ queryKey: ["portal-submissions"] });
    } catch (e: any) {
      toast("error", "Couldn't discard", e?.response?.data?.detail || String(e));
    } finally {
      setDiscarding(false);
    }
  };

  const years = [now.getFullYear(), now.getFullYear() - 1];

  return (
    <PortalShell>
      <PageHeader title="Submit your timesheet" subtitle="Pick a month, attach your files, and send it in." />

      <Card className="p-6">
        <div className="flex flex-wrap gap-4">
          <Field label="Month">
            <Select value={month} onChange={(e) => setMonth(e.target.value ? Number(e.target.value) : "")}>
              <option value="">Select month…</option>
              {MONTHS_LONG.slice(1).map((m, i) => (
                <option key={i + 1} value={i + 1}>
                  {m}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Year">
            <Select value={year} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : "")}>
              <option value="">Select year…</option>
              {years.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {!periodChosen ? (
          <p className="mt-4 text-sm text-slate-400">Pick a month and year to continue.</p>
        ) : (
          <>
            {!isLoading && precheck?.already_filed_elsewhere && (
              <div className="mt-4 flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                <Info className="mt-0.5 h-4 w-4 shrink-0" />
                <p>{precheck.filed_source_note}</p>
              </div>
            )}

            {isRejected && (
              <div className="mt-4 flex items-start gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <p className="font-semibold">The timesheet team sent this back.</p>
                  {submission?.manager_note && <p className="mt-0.5">"{submission.manager_note}"</p>}
                  <p className="mt-1 text-rose-700/80">Fix or replace the files below, then submit again.</p>
                </div>
              </div>
            )}

            {submission?.status === "submitted" && submission.review_state !== "accepted" && (
              <div className="mt-4 flex items-center gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                <Clock className="h-4 w-4 shrink-0" />
                <p>
                  Sent on {submission.submitted_at ? new Date(submission.submitted_at).toLocaleDateString() : ""} —
                  awaiting review. You can still add or replace files below (e.g. a certificate that arrives
                  later) — doing so puts it back in front of the timesheet team for a fresh look.
                </p>
              </div>
            )}

            {submission?.review_state === "accepted" && (
              <div className="mt-4 flex items-center gap-2 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <p>
                  This submission has been accepted. You can still add or replace files below — doing so sends it
                  back for a fresh look.
                </p>
              </div>
            )}

            <div className="mt-6 space-y-3">
              {SLOTS.map((s) => (
                <UploadSlot
                  key={s.kind}
                  kind={s.kind}
                  label={s.label}
                  required={s.required}
                  submissionId={submissionId || ""}
                  file={filesByKind[s.kind]}
                  disabled={!submissionId}
                  onChanged={onSlotChanged}
                />
              ))}
            </div>

            {!submissionId && (
              <Button
                variant="secondary"
                className="mt-4"
                onClick={async () => {
                  const id = await ensureDraft();
                  setPendingSubmissionId(id);
                }}
              >
                Start this month's submission
              </Button>
            )}

            {submissionId && (
              <>
                <Field label="Note (optional)">
                  <textarea
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-xs focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
                    rows={2}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    onBlur={() => portalUpsertSubmission({ month: month as number, year: year as number, employee_note: note })}
                  />
                </Field>

                {needsExplicitSubmit && (
                  <div className="mt-4">
                    <p className="text-sm font-semibold text-slate-800">
                      Does this timesheet already show manager approval? <span className="text-rose-500">*</span>
                    </p>
                    <p className="mt-0.5 text-xs text-slate-400">
                      For example, a manager's signature or a written approval note on the document itself.
                    </p>
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        onClick={() => setApprovalAnswer(true)}
                        className={cn(
                          "rounded-lg border px-4 py-1.5 text-sm font-medium transition-colors",
                          approvalAnswer === true
                            ? "border-brand-500 bg-brand-50 text-brand-700"
                            : "border-slate-200 text-slate-600 hover:bg-slate-50"
                        )}
                      >
                        Yes
                      </button>
                      <button
                        type="button"
                        onClick={() => setApprovalAnswer(false)}
                        className={cn(
                          "rounded-lg border px-4 py-1.5 text-sm font-medium transition-colors",
                          approvalAnswer === false
                            ? "border-brand-500 bg-brand-50 text-brand-700"
                            : "border-slate-200 text-slate-600 hover:bg-slate-50"
                        )}
                      >
                        No
                      </button>
                    </div>
                  </div>
                )}

                <div className="mt-4 flex items-center gap-2">
                  {needsExplicitSubmit && (
                    <Button disabled={!hasAnyFile || approvalAnswer === null || submitting} onClick={doSubmit}>
                      {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                      {isRejected ? "Resubmit" : "Submit"}
                    </Button>
                  )}
                  {isDraft && (
                    <Button variant="ghost" className="text-rose-600 hover:bg-rose-50" disabled={discarding} onClick={doDiscard}>
                      <Trash2 className="h-3.5 w-3.5" />
                      Discard draft
                    </Button>
                  )}
                </div>
                {needsExplicitSubmit && !hasAnyFile && (
                  <p className="mt-2 text-xs text-slate-400">Attach at least one file before submitting.</p>
                )}
                {needsExplicitSubmit && hasAnyFile && approvalAnswer === null && (
                  <p className="mt-2 text-xs text-slate-400">Answer the question above before submitting.</p>
                )}
              </>
            )}
          </>
        )}
      </Card>
    </PortalShell>
  );
}
