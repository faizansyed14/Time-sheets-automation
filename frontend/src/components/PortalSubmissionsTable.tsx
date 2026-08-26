import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Eye, Loader2, Users, CheckCircle2, Clock, XCircle, UserX } from "lucide-react";
import {
  fetchPipeline,
  fetchPortalRosterAdmin,
  portalAdminFileUrl,
  portalAdminRenderUrl,
  MONTHS_LONG,
  type PipelineFile,
  type PortalFileKind,
  type PortalRosterMemberAdmin,
  type PortalSubmissionFile,
} from "../api/client";
import { Badge, EmptyState, Select, Skeleton } from "./ui";
import PortalFilePreview from "./PortalFilePreview";
import { cn } from "../lib/utils";

const KIND_LABEL: Record<PortalFileKind, string> = {
  timesheet: "Timesheet",
  sick_leave: "Sick leave",
  other: "Other",
};

type FilterT = "" | "missing" | "pending" | "accepted" | "not_approved";

/** "Accepted" isn't a manager_decision value — it's the computed
 *  review_state (see backend's _portal_submission_admin_rows /
 *  portal_employee.py's _review_state), which is the ONLY source of truth
 *  for "has this been filed yet" since Accept never writes back to
 *  PortalSubmission. Checked ahead of manager_decision so an accepted row
 *  never gets shown as "sent back" even if it was, before being fixed and
 *  accepted anyway. */
function bucketOf(s: PortalRosterMemberAdmin["submission"]): Exclude<FilterT, ""> {
  if (!s) return "missing";
  if (s.review_state === "accepted") return "accepted";
  if (s.manager_decision === "not_approved") return "not_approved";
  return "pending";
}

function RosterStat({
  label,
  value,
  icon,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  tone: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-2.5 rounded-lg border bg-white px-3 py-2.5 text-left transition-all",
        active ? "border-brand-500 ring-2 ring-brand-100" : "border-slate-200 hover:border-slate-300"
      )}
    >
      <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", tone)}>{icon}</div>
      <div>
        <p className="text-base font-bold leading-5 text-slate-900">{value}</p>
        <p className="text-[11px] font-medium text-slate-500">{label}</p>
      </div>
    </button>
  );
}

/** The internal "Portal Submissions" view of the Pipeline page — a
 *  month/year-scoped roster of every portal-enabled employee (submitted or
 *  not), so "who's missing" is as visible as "who's already sent it in."
 *  Clicking a linked file opens the SAME PipelineCompareFixModal every other
 *  source uses — no second review UI. */
export default function PortalSubmissionsTable({
  onOpenPipelineFile,
}: {
  onOpenPipelineFile: (file: PipelineFile) => void;
}) {
  const now = new Date();
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [year, setYear] = useState(now.getFullYear());
  const years = [now.getFullYear(), now.getFullYear() - 1];
  const [filter, setFilter] = useState<FilterT>("");
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ submissionId: string; file: PortalSubmissionFile } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["portal-roster-admin", month, year],
    queryFn: () => fetchPortalRosterAdmin(month, year),
  });

  const counts = useMemo(() => {
    const rows = data || [];
    return {
      total: rows.length,
      missing: rows.filter((m) => bucketOf(m.submission) === "missing").length,
      pending: rows.filter((m) => bucketOf(m.submission) === "pending").length,
      accepted: rows.filter((m) => bucketOf(m.submission) === "accepted").length,
      not_approved: rows.filter((m) => bucketOf(m.submission) === "not_approved").length,
    };
  }, [data]);

  const visible = useMemo(() => {
    const rows = data || [];
    if (!filter) return rows;
    return rows.filter((m) => bucketOf(m.submission) === filter);
  }, [data, filter]);

  const openFile = async (pipelineFileId: string, sourceIdGuess: string) => {
    setOpeningId(pipelineFileId);
    try {
      const page = await fetchPipeline({ source_id: sourceIdGuess, limit: 1 });
      const file = page.items.find((f) => f.id === pipelineFileId) || page.items[0];
      if (file) onOpenPipelineFile(file);
    } finally {
      setOpeningId(null);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3.5">
        <Select value={month} onChange={(e) => setMonth(Number(e.target.value))} className="py-1.5 text-xs">
          {MONTHS_LONG.slice(1).map((m, i) => (
            <option key={i + 1} value={i + 1}>
              {m}
            </option>
          ))}
        </Select>
        <Select value={year} onChange={(e) => setYear(Number(e.target.value))} className="py-1.5 text-xs">
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </Select>
        <p className="ml-auto text-xs text-slate-400">
          {visible.length} of {counts.total} portal employee{counts.total !== 1 && "s"}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2.5 p-4 sm:grid-cols-5">
        <RosterStat
          label="Portal employees"
          value={counts.total}
          icon={<Users className="h-4 w-4 text-slate-600" />}
          tone="bg-slate-100"
          active={filter === ""}
          onClick={() => setFilter("")}
        />
        <RosterStat
          label="Missing"
          value={counts.missing}
          icon={<UserX className="h-4 w-4 text-rose-600" />}
          tone="bg-rose-50"
          active={filter === "missing"}
          onClick={() => setFilter(filter === "missing" ? "" : "missing")}
        />
        <RosterStat
          label="Awaiting review"
          value={counts.pending}
          icon={<Clock className="h-4 w-4 text-amber-600" />}
          tone="bg-amber-50"
          active={filter === "pending"}
          onClick={() => setFilter(filter === "pending" ? "" : "pending")}
        />
        <RosterStat
          label="Accepted"
          value={counts.accepted}
          icon={<CheckCircle2 className="h-4 w-4 text-emerald-600" />}
          tone="bg-emerald-50"
          active={filter === "accepted"}
          onClick={() => setFilter(filter === "accepted" ? "" : "accepted")}
        />
        <RosterStat
          label="Sent back"
          value={counts.not_approved}
          icon={<XCircle className="h-4 w-4 text-rose-600" />}
          tone="bg-rose-50"
          active={filter === "not_approved"}
          onClick={() => setFilter(filter === "not_approved" ? "" : "not_approved")}
        />
      </div>

      {isLoading ? (
        <div className="space-y-2 p-6">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      ) : !visible.length ? (
        <EmptyState
          title={counts.total ? "Nothing matches this filter" : "No portal accounts yet"}
          detail={
            counts.total
              ? "Try a different filter or period."
              : "Create employee portal accounts under Admin → Portal accounts."
          }
        />
      ) : (
        <div className="divide-y divide-slate-100">
          {visible.map((m) => {
            const s = m.submission;
            const bucket = bucketOf(s);
            return (
              <div key={m.employee_pk} className="px-5 py-3.5">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-sm font-semibold text-slate-800">
                    {m.employee_name} <span className="font-normal text-slate-400">({m.employee_id})</span>
                  </span>
                  {s ? (
                    <Badge
                      tone={
                        bucket === "accepted" ? "success" : bucket === "not_approved" ? "danger" : "warning"
                      }
                    >
                      {bucket === "accepted"
                        ? "Accepted"
                        : bucket === "not_approved"
                        ? "Sent back"
                        : "Awaiting review"}
                    </Badge>
                  ) : (
                    <Badge tone="slate">Not submitted</Badge>
                  )}
                  {s?.extraction_state === "running" && (
                    <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                      <Loader2 className="h-3 w-3 animate-spin" /> reading…
                    </span>
                  )}
                  {s?.extraction_state === "failed" && (
                    <span className="text-xs text-rose-500" title={s.extraction_error ?? undefined}>
                      extraction failed
                    </span>
                  )}
                  {s?.manager_note && <span className="text-xs italic text-slate-400">"{s.manager_note}"</span>}
                </div>
                {s && s.files.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {s.files.map((f) => (
                      <button
                        key={f.id}
                        onClick={() => setPreview({ submissionId: s.id, file: f })}
                        className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
                      >
                        <Eye className="h-3 w-3" />
                        {KIND_LABEL[f.kind]}
                      </button>
                    ))}
                  </div>
                )}
                {s && s.pipeline_files.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {s.pipeline_files.map((pf) =>
                      // Already accepted into a filed record — nothing left
                      // to review, so no Compare & Fix link for this file.
                      pf.record_id ? (
                        <span
                          key={pf.kind}
                          className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700"
                        >
                          <CheckCircle2 className="h-3 w-3" />
                          {pf.kind} accepted
                        </span>
                      ) : (
                        <button
                          key={pf.kind}
                          disabled={!pf.pipeline_file_id || openingId === pf.pipeline_file_id}
                          onClick={() => pf.pipeline_file_id && openFile(pf.pipeline_file_id, `portal:${s.id}:${pf.kind}`)}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50",
                            openingId === pf.pipeline_file_id && "opacity-50"
                          )}
                        >
                          <ExternalLink className="h-3 w-3" />
                          Review in Compare &amp; Fix — {pf.kind} · {pf.pipeline_status}
                        </button>
                      )
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {preview && (
        <PortalFilePreview
          open
          onClose={() => setPreview(null)}
          url={portalAdminFileUrl(preview.submissionId, preview.file.kind)}
          renderUrl={portalAdminRenderUrl(preview.submissionId, preview.file.kind)}
          filename={preview.file.filename}
          contentType={preview.file.content_type}
        />
      )}
    </div>
  );
}
