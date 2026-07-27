import { useState } from "react";

import { Sparkles, ChevronDown, ChevronRight, CheckCircle2, X } from "lucide-react";

import { type ThreadSummary } from "../api/client";
import { cn, formatDateTime } from "../lib/utils";

const SUMMARY_TONE: Record<ThreadSummary["status"], { label: string; cls: string }> = {
  approved: { label: "Approved", cls: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  awaiting_approval: { label: "Awaiting approval", cls: "border-amber-200 bg-amber-50 text-amber-700" },
  sheet_submitted: { label: "Sheet submitted", cls: "border-brand-200 bg-brand-50 text-brand-700" },
  correction_requested: { label: "Correction requested", cls: "border-rose-200 bg-rose-50 text-rose-700" },
  chasing: { label: "Chasing", cls: "border-amber-200 bg-amber-50 text-amber-700" },
  other: { label: "Other", cls: "border-slate-200 bg-slate-50 text-slate-600" },
};

/** Collapsible plain-English read of the conversation, so a reviewer does not
 *  have to open eight replies to learn whether the sheet arrived and whether
 *  anyone actually approved it. Produced by pass 1 of Extract Email — shown
 *  wherever that run's result is reviewed (Inbox thread view, Pipeline record). */
export function ThreadSummaryBox({ summary }: { summary: ThreadSummary }) {
  const [open, setOpen] = useState(false);
  const tone = SUMMARY_TONE[summary.status] ?? SUMMARY_TONE.other;
  const facts: [string, boolean][] = [
    ["Timesheet sent", summary.timesheet_sent],
    ["Approval requested", summary.approval_requested],
    ["Approval given", summary.approval_given],
  ];

  return (
    <div className="mb-3 overflow-hidden rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left hover:bg-slate-50/80"
      >
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className={cn(
              "rounded-full border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide",
              tone.cls)}>
              {tone.label}
            </span>
            {summary.period && (
              <span className="text-[11px] text-slate-500">{summary.period}</span>
            )}
            {summary.employee && (
              <span className="text-[11px] text-slate-500">· {summary.employee}</span>
            )}
          </span>
          <span className="mt-1 block text-sm text-slate-800">{summary.headline}</span>
        </span>
        {open
          ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
          : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />}
      </button>

      {open && (
        <div className="space-y-2.5 border-t border-slate-100 px-3 py-2.5">
          <p className="text-sm leading-relaxed text-slate-700">{summary.narrative}</p>

          <div className="flex flex-wrap gap-1.5">
            {facts.map(([label, yes]) => (
              <span
                key={label}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
                  yes ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : "border-slate-200 bg-slate-50 text-slate-500")}
              >
                {yes ? <CheckCircle2 className="h-3 w-3" /> : <X className="h-3 w-3" />}
                {label}
              </span>
            ))}
          </div>

          {summary.action_needed && (
            <p className="rounded-md bg-slate-50 px-2.5 py-2 text-xs text-slate-700">
              <span className="font-semibold">Next: </span>{summary.action_needed}
            </p>
          )}
          <p className="text-[10px] text-slate-400">
            {summary.message_count} message(s) · {summary.model} · {formatDateTime(summary.at)}
            {" · from the last Extract Email run"}
          </p>
        </div>
      )}
    </div>
  );
}
