import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Eye } from "lucide-react";
import {
  portalEmployeeFileUrl,
  portalEmployeeRenderUrl,
  portalListMySubmissions,
  MONTHS_LONG,
  type PortalFileKind,
  type PortalManagerDecision,
  type PortalSubmission,
  type PortalSubmissionFile,
} from "../../api/client";
import { Badge, Card, EmptyState, PageHeader, Spinner } from "../../components/ui";
import PortalFilePreview from "../../components/PortalFilePreview";
import PortalShell from "./PortalShell";

// "Accepted" isn't a manager_decision value — it's the computed review_state
// (see backend's _review_state) — checked separately below, ahead of these.
const DECISION_TONE: Record<PortalManagerDecision, "success" | "warning" | "danger"> = {
  pending: "warning",
  not_approved: "danger",
};
const DECISION_LABEL: Record<PortalManagerDecision, string> = {
  pending: "Awaiting review",
  not_approved: "Sent back",
};
const KIND_LABEL: Record<PortalFileKind, string> = {
  timesheet: "Timesheet",
  sick_leave: "Sick leave",
  other: "Other",
};

export default function PortalEmployeeHistory() {
  const { data, isLoading } = useQuery({
    queryKey: ["portal-submissions"],
    queryFn: portalListMySubmissions,
  });
  // Drafts aren't history yet — nothing was sent to anyone. An abandoned
  // draft (or one still being assembled) would otherwise sit here reading
  // as "queued"/"awaiting manager" when actually nothing has happened. Use
  // the Submit page for those; discard an unwanted one from there too.
  const submitted = (data || []).filter((s) => s.status !== "draft");
  const [preview, setPreview] = useState<{ submissionId: string; file: PortalSubmissionFile } | null>(null);

  // Grouped by year (newest first), each year's months newest first — a
  // year or two of history reads as a scattered list otherwise, since the
  // API has no inherent ordering guarantee.
  const byYear = useMemo(() => {
    const groups = new Map<number, PortalSubmission[]>();
    for (const s of submitted) {
      if (!groups.has(s.year)) groups.set(s.year, []);
      groups.get(s.year)!.push(s);
    }
    return Array.from(groups.entries())
      .sort(([a], [b]) => b - a)
      .map(([year, subs]) => [year, subs.slice().sort((a, b) => b.month - a.month)] as const);
  }, [submitted]);

  return (
    <PortalShell>
      <PageHeader title="My submissions" subtitle="Status of everything you've sent in." />
      <Card className="p-2">
        {isLoading ? (
          <div className="flex justify-center p-10">
            <Spinner className="h-6 w-6" />
          </div>
        ) : !submitted.length ? (
          <EmptyState title="No submissions yet" detail="Submit your first month from the Submit page." />
        ) : (
          <div className="space-y-4">
            {byYear.map(([year, subs]) => (
              <div key={year}>
                <p className="px-2 pb-1.5 text-xs font-bold uppercase tracking-wide text-slate-400">{year}</p>
                <div className="divide-y divide-slate-100 rounded-lg border border-slate-100">
                  {subs.map((s) => (
                    <div key={s.id} className="px-4 py-3">
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <p className="text-sm font-semibold text-slate-800">{MONTHS_LONG[s.month]}</p>
                          <p className="mt-0.5 text-xs text-slate-500">
                            {s.files.length} file{s.files.length === 1 ? "" : "s"} ·{" "}
                            {s.submitted_at ? `submitted ${new Date(s.submitted_at).toLocaleDateString()}` : "draft"}
                          </p>
                          {s.manager_note && <p className="mt-1 text-xs italic text-slate-400">"{s.manager_note}"</p>}
                        </div>
                        <div className="flex flex-col items-end gap-1.5">
                          {s.review_state === "accepted" ? (
                            <Badge tone="success">Accepted</Badge>
                          ) : (
                            <Badge tone={DECISION_TONE[s.manager_decision]}>{DECISION_LABEL[s.manager_decision]}</Badge>
                          )}
                          <span className="text-[11px] text-slate-400">
                            {s.extraction_state === "running" && "Reading your files…"}
                            {s.extraction_state === "done" &&
                              s.review_state !== "accepted" &&
                              "Read — awaiting the timesheet team's review"}
                            {s.extraction_state === "failed" && "Couldn't read one of your files"}
                            {s.extraction_state === "not_started" && "Queued"}
                          </span>
                        </div>
                      </div>
                      {s.files.length > 0 && (
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
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {preview && (
        <PortalFilePreview
          open
          onClose={() => setPreview(null)}
          url={portalEmployeeFileUrl(preview.submissionId, preview.file.kind)}
          renderUrl={portalEmployeeRenderUrl(preview.submissionId, preview.file.kind)}
          filename={preview.file.filename}
          contentType={preview.file.content_type}
        />
      )}
    </PortalShell>
  );
}
