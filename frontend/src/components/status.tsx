import { CheckCircle2, AlertTriangle, XCircle, BadgeCheck, Loader2, Lock, ShieldQuestion } from "lucide-react";
import { type PipelineFile, type PipelineStatus } from "../api/client";
import { cn, formatDateTime } from "../lib/utils";
import { Badge } from "./ui";

/* ---------------- pipeline status pill ---------------- */
export function PipelineStatusBadge({ status }: { status: PipelineStatus }) {
  switch (status) {
    case "success":
      return (
        <Badge tone="success">
          <CheckCircle2 className="h-3 w-3" /> Success
        </Badge>
      );
    case "needs_review":
      return (
        <Badge tone="warning">
          <AlertTriangle className="h-3 w-3" /> Needs review
        </Badge>
      );
    case "failed":
      return (
        <Badge tone="danger">
          <XCircle className="h-3 w-3" /> Failed
        </Badge>
      );
    case "resolved":
      return (
        <Badge tone="brand">
          <BadgeCheck className="h-3 w-3" /> Resolved
        </Badge>
      );
    default:
      return (
        <Badge tone="slate">
          <Loader2 className="h-3 w-3 animate-spin" /> Processing
        </Badge>
      );
  }
}

export function FailureChip({ code, label }: { code: string | null; label: string | null }) {
  if (!code) return null;
  const icon =
    code === "protected_pdf" ? (
      <Lock className="h-3 w-3" />
    ) : code === "ambiguous_id" || code === "id_name_mismatch" ? (
      <ShieldQuestion className="h-3 w-3" />
    ) : (
      <XCircle className="h-3 w-3" />
    );
  return (
    <Badge tone={code === "validation_mismatch" || code === "id_name_mismatch" || code === "storage_error" ? "warning" : "danger"}>
      {icon}
      {label && label !== code ? label : code.replace(/_/g, " ")}
    </Badge>
  );
}

/* ---------------- validation / approval pills ---------------- */
export function ValidationBadge({ status }: { status: "verified" | "manual_review" }) {
  return status === "verified" ? (
    <Badge tone="success">
      <CheckCircle2 className="h-3 w-3" /> Verified
    </Badge>
  ) : (
    <Badge tone="warning">
      <AlertTriangle className="h-3 w-3" /> Needs review
    </Badge>
  );
}

export function ApprovalBadge({ status }: { status: "pending" | "approved" | "not_approved" }) {
  if (status === "approved")
    return (
      <Badge tone="success">
        <BadgeCheck className="h-3 w-3" /> Approved
      </Badge>
    );
  if (status === "not_approved")
    return (
      <Badge tone="danger">
        <XCircle className="h-3 w-3" /> Not approved
      </Badge>
    );
  return <Badge tone="slate">Pending sign-off</Badge>;
}

/* ---------------- stage timeline ---------------- */
const STAGE_LABELS: Record<string, string> = {
  received: "Received",
  protection_check: "Protection check",
  extraction: "LLM extraction",
  identification: "Identification",
  matching: "Employee match",
  validation: "Validation",
  filing: "Filing",
  recorded: "Recorded",
};

export function StageTimeline({ file }: { file: PipelineFile }) {
  const events = file.events ?? [];
  return (
    <ol className="relative ml-2 space-y-0 border-l-2 border-slate-200 pl-5">
      {events.map((e, i) => {
        const Icon =
          e.status === "fail" ? XCircle : e.status === "warn" ? AlertTriangle : CheckCircle2;
        const color =
          e.status === "fail"
            ? "text-rose-500 ring-rose-100"
            : e.status === "warn"
              ? "text-amber-500 ring-amber-100"
              : "text-emerald-500 ring-emerald-100";
        return (
          <li key={i} className="relative pb-4 last:pb-0">
            <span
              className={cn(
                "absolute -left-[27px] top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white ring-4",
                color
              )}
            >
              <Icon className="h-4 w-4" />
            </span>
            <div className="flex flex-wrap items-baseline gap-x-2">
              <p className="text-xs font-bold uppercase tracking-wide text-slate-600">
                {STAGE_LABELS[e.stage] ?? e.stage}
              </p>
              <p className="text-[11px] text-slate-400">{formatDateTime(e.at)}</p>
            </div>
            <p className="mt-0.5 text-[13px] leading-5 text-slate-600">{e.detail}</p>
          </li>
        );
      })}
      {events.length === 0 && (
        <li className="py-2 text-sm text-slate-400">No pipeline events recorded.</li>
      )}
    </ol>
  );
}
