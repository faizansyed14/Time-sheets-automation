import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveRecord, deleteRecord, fileContentUrl, MONTHS_LONG, recordSources,
  updateRecord, verifyRecord, type SourceFile, type TimesheetRecord,
} from "../api/client";
import { Pill } from "./ui";
import { ConfirmDialog, FilePreview, Modal, fileKindLabel } from "./Modal";

type Buckets = {
  annual_leave_dates: string[]; remote_work_dates: string[]; sick_leave_dates: string[];
  unpaid_leave_dates: string[]; absent_dates: string[]; public_holiday_dates: string[];
};
const BUCKET_META: { key: keyof Buckets; label: string; tone: string }[] = [
  { key: "annual_leave_dates", label: "Annual", tone: "text-petrol-600" },
  { key: "remote_work_dates", label: "WFH / Remote", tone: "text-sky-600" },
  { key: "sick_leave_dates", label: "Sick", tone: "text-orange-600" },
  { key: "unpaid_leave_dates", label: "Unpaid", tone: "text-rose-600" },
  { key: "absent_dates", label: "Absent", tone: "text-red-600" },
  { key: "public_holiday_dates", label: "Public Hol.", tone: "text-violet-600" },
];

export default function RecordDetail({ rec, onDeleted }: { rec: TimesheetRecord; onDeleted?: () => void }) {
  const qc = useQueryClient();
  const review = rec.validation_status === "manual_review";
  const [editing, setEditing] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const invalidate = () => qc.invalidateQueries();

  const approve = useMutation({ mutationFn: (a: boolean) => approveRecord(rec.id, a), onSuccess: invalidate });
  const verify = useMutation({ mutationFn: () => verifyRecord(rec.id), onSuccess: invalidate });
  const del = useMutation({ mutationFn: () => deleteRecord(rec.id), onSuccess: () => { invalidate(); onDeleted?.(); } });

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-panel">
      {/* header */}
      <div className={`flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3.5 ${review ? "border-amber-100 bg-amber-50/60" : "border-emerald-100 bg-emerald-50/50"}`}>
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-semibold text-ink">{MONTHS_LONG[rec.month] ?? rec.month} {rec.year}</span>
          {review ? <Pill tone="amber">Needs review</Pill> : <Pill tone="emerald">Verified</Pill>}
          {rec.calendar_days ? <span className="text-xs text-slate-400">{rec.calendar_days} calendar days</span> : null}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowSources(true)} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:border-petrol-300 hover:text-petrol-700">
            View timesheets
          </button>
          {rec.approval_status === "approved" && <Pill tone="emerald">✓ Approved</Pill>}
          {rec.approval_status === "not_approved" && <Pill tone="rose">✕ Not approved</Pill>}
          {rec.approval_status === "pending" && <Pill tone="slate">Approval pending</Pill>}
        </div>
      </div>

      <div className="grid gap-5 p-5 lg:grid-cols-[1.1fr_1fr]">
        {/* LEFT: leaves */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Leave summary</div>
            {!editing ? (
              <button onClick={() => setEditing(true)} className="text-xs font-medium text-petrol-700 hover:underline">Edit</button>
            ) : null}
          </div>
          {editing ? (
            <LeaveEditor rec={rec} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); invalidate(); }} />
          ) : (
            <LeaveView rec={rec} />
          )}
        </div>

        {/* RIGHT: analysis + approval */}
        <div className="space-y-4">
          <div className={`rounded-xl border px-4 py-3 ${review ? "border-amber-200 bg-amber-50/50" : "border-slate-200"}`}>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">LLM analysis</span>
              {review && (
                <button onClick={() => verify.mutate()} disabled={verify.isPending}
                  className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50">
                  {verify.isPending ? "…" : "Mark verified"}
                </button>
              )}
            </div>
            <p className="text-sm text-slate-700">
  {rec.hr_flags.length > 0
    ? `${rec.hr_flags.length} issue${rec.hr_flags.length > 1 ? "s" : ""} need review:`
    : (rec.llm_summary || "—")}
</p>
{rec.hr_flags.length > 0 && (
  <ul className="mt-2 space-y-1.5">
                {rec.hr_flags.map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-amber-800">
                    <span className="mt-0.5 text-amber-500">▲</span><span>{f}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 px-4 py-3">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Identity match</div>
            <div className="space-y-1 text-sm">
              <Row k="Employee" v={`${rec.employee_name ?? "—"}  (${rec.employee_id ?? "no id"})`} />
              <Row k="DCO" v={rec.dco_number ?? "—"} />
              <Row k="Account Mgr" v={rec.account_manager ?? "—"} />
              <Row k="Match" v={rec.match_note ?? "—"} />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3">
            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Manager approval</div>
            <div className="mb-2 text-sm">
              {rec.approval_detected
                ? <span className="font-medium text-emerald-700">✓ Detected in screenshot</span>
                : <span className="font-medium text-slate-500">— Not detected</span>}
            </div>
            {rec.approval_detail && <p className="mb-3 text-xs italic text-slate-500">“{rec.approval_detail}”</p>}
            <div className="flex gap-2">
              <button onClick={() => approve.mutate(true)} disabled={approve.isPending}
                className={`flex-1 rounded-lg px-3 py-2 text-sm font-semibold ${rec.approval_status === "approved" ? "bg-emerald-600 text-white" : "border border-emerald-200 bg-white text-emerald-700 hover:bg-emerald-50"}`}>
                Approve
              </button>
              <button onClick={() => approve.mutate(false)} disabled={approve.isPending}
                className={`flex-1 rounded-lg px-3 py-2 text-sm font-semibold ${rec.approval_status === "not_approved" ? "bg-rose-600 text-white" : "border border-rose-200 bg-white text-rose-700 hover:bg-rose-50"}`}>
                Not approved
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between">
            {rec.storage_folder && <span className="font-mono text-[11px] text-slate-400">📁 {rec.storage_folder}</span>}
            <button onClick={() => setConfirmDelete(true)} className="text-xs font-medium text-rose-600 hover:underline">Delete record</button>
          </div>
        </div>
      </div>

      <SourcesModal open={showSources} recordId={rec.id} onClose={() => setShowSources(false)} />
      <ConfirmDialog open={confirmDelete} title="Delete record?" danger confirmLabel="Delete"
        message={`This removes the ${MONTHS_LONG[rec.month]} ${rec.year} record for ${rec.employee_name}. Stored files are not deleted.`}
        onConfirm={() => del.mutate()} onClose={() => setConfirmDelete(false)} />
    </div>
  );
}

function LeaveView({ rec }: { rec: TimesheetRecord }) {
  const has = BUCKET_META.some((b) => (rec[b.key] as string[]).length);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        {BUCKET_META.map((b) => (
          <div key={b.key} className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <span className="text-xs font-medium text-slate-500">{b.label}</span>
            <span className={`tabular font-mono text-lg font-semibold ${(rec[b.key] as string[]).length ? b.tone : "text-slate-300"}`}>
              {(rec[b.key] as string[]).length}
            </span>
          </div>
        ))}
      </div>
      <div className="rounded-xl border border-slate-200 px-4 py-3">
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Dates</div>
        {BUCKET_META.map((b) => (rec[b.key] as string[]).length ? (
          <div key={b.key} className="flex flex-wrap items-start gap-2 py-1.5">
            <span className="w-28 shrink-0 text-xs font-medium text-slate-500">{b.label}</span>
            <div className="flex flex-wrap gap-1.5">
              {(rec[b.key] as string[]).map((d) => (
                <span key={d} className="rounded-md bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-700">{d}</span>
              ))}
            </div>
          </div>
        ) : null)}
        {!has && <div className="py-1.5 text-sm text-slate-400">No leave recorded.</div>}
      </div>
    </div>
  );
}

function LeaveEditor({ rec, onClose, onSaved }: { rec: TimesheetRecord; onClose: () => void; onSaved: () => void }) {
  const [state, setState] = useState<Buckets>(() => ({
    annual_leave_dates: [...rec.annual_leave_dates],
    remote_work_dates: [...rec.remote_work_dates],
    sick_leave_dates: [...rec.sick_leave_dates],
    unpaid_leave_dates: [...rec.unpaid_leave_dates],
    absent_dates: [...rec.absent_dates],
    public_holiday_dates: [...rec.public_holiday_dates],
  }));
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => updateRecord(rec.id, { ...state, month: rec.month, year: rec.year }),
    onSuccess: onSaved,
    onError: (e: any) => setErr(e?.response?.data?.detail ?? "Save failed"),
  });

  const addDate = (key: keyof Buckets, val: string) => {
    if (!val) return;
    setState((s) => (s[key].includes(val) ? s : { ...s, [key]: [...s[key], val].sort() }));
  };
  const removeDate = (key: keyof Buckets, val: string) =>
    setState((s) => ({ ...s, [key]: s[key].filter((d) => d !== val) }));

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-petrol-200 bg-petrol-50/40 px-3 py-2 text-xs text-petrol-700">
        Edit dates, then Save. Validation re-runs automatically — if all issues clear, the record becomes Verified.
      </div>
      {BUCKET_META.map((b) => (
        <div key={b.key} className="rounded-xl border border-slate-200 px-3 py-2.5">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-600">{b.label}</span>
            <input
              type="date"
              className="rounded-md border border-slate-200 px-2 py-1 text-xs focus:border-petrol-500 focus:outline-none"
              onChange={(e) => { addDate(b.key, e.target.value); e.currentTarget.value = ""; }}
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {state[b.key].length === 0 && <span className="text-xs text-slate-300">none</span>}
            {state[b.key].map((d) => (
              <span key={d} className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-700">
                {d}
                <button onClick={() => removeDate(b.key, d)} className="text-slate-400 hover:text-rose-600">×</button>
              </span>
            ))}
          </div>
        </div>
      ))}
      {err && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{err}</div>}
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
        <button onClick={() => save.mutate()} disabled={save.isPending}
          className="rounded-lg bg-petrol-600 px-4 py-2 text-sm font-semibold text-white hover:bg-petrol-700 disabled:opacity-50">
          {save.isPending ? "Saving…" : "Save & re-validate"}
        </button>
      </div>
    </div>
  );
}

function SourcesModal({ open, recordId, onClose }: { open: boolean; recordId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["sources", recordId], queryFn: () => recordSources(recordId), enabled: open });
  const [active, setActive] = useState<SourceFile | null>(null);
  useEffect(() => { if (data && data.length) setActive(data[0]); }, [data]);

  return (
    <Modal open={open} title="Stored timesheets & files" onClose={onClose} width="max-w-3xl">
      {isLoading ? (
        <div className="text-sm text-slate-500">Loading…</div>
      ) : !data || data.length === 0 ? (
        <div className="text-sm text-slate-400">No stored files for this record.</div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {data.map((f) => (
              <button key={f.rel_path} onClick={() => setActive(f)}
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${active?.rel_path === f.rel_path ? "border-petrol-300 bg-petrol-50 text-petrol-700" : "border-slate-200 text-slate-600 hover:border-slate-300"}`}>
                <span className="max-w-[200px] truncate">{f.name}</span>
                <Pill tone={fileKindLabel(f) === "approval" ? "petrol" : "slate"}>{fileKindLabel(f)}</Pill>
              </button>
            ))}
          </div>
          {active && <FilePreview url={fileContentUrl(active.rel_path)} name={active.name} contentType={active.content_type} />}
        </div>
      )}
    </Modal>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <span className="w-24 shrink-0 text-slate-400">{k}</span>
      <span className="text-slate-700">{v}</span>
    </div>
  );
}
