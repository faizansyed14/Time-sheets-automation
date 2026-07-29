import { Check, X, Trash2, FileSearch, Cpu } from "lucide-react";
import type { ExtractionEvent } from "../api/client";
import { cn } from "../lib/utils";

/** The 3-step Extract Email pipeline visual — Reduce junk → Pass 1 → Pass 2
 * — shared by every place that shows a live extraction in progress: the
 * manual Extract Email queue widget AND Auto Extract's currently-processing
 * thread. One component so both read identically and any future tweak
 * (a new stage, different batch wording) only has to happen once. */

export type StepOverall = "running" | "done" | "error";
type StepStatus = "pending" | "active" | "done" | "error";

export const STEPS: { stage: string; label: string; icon: typeof Trash2 }[] = [
  { stage: "unpack", label: "Reduce junk", icon: Trash2 },
  { stage: "pass1", label: "Pass 1", icon: FileSearch },
  { stage: "pass2", label: "Pass 2", icon: Cpu },
];

export function stepStatuses(events: ExtractionEvent[], overall: StepOverall): StepStatus[] {
  const isDoneStage = (stage: string) => events.some((e) => e.stage === stage && e.status === "ok");
  const isSpinning = (stage: string) => events.some((e) => e.stage === stage && e.status === "spin");
  const lastStage = events[events.length - 1]?.stage;
  const erroredStage = overall === "error" ? lastStage : null;

  const statuses = STEPS.map(({ stage }): StepStatus => {
    if (erroredStage === stage) return "error";
    if (isDoneStage(stage)) return "done";
    if (isSpinning(stage)) return "active";
    return "pending";
  });
  // Finished cleanly (e.g. pass 2 skipped — nothing confirmed) — treat every
  // not-yet-touched step as done rather than stuck "pending".
  if (overall === "done") return statuses.map((s) => (s === "pending" ? "done" : s));
  return statuses;
}

type BatchPhase = "sent" | "received" | "retry";
interface BatchInfo {
  index: number;
  total: number;
  phase: BatchPhase;
}

/** The most recent per-batch progress for one stage (pass1/pass2) — each
 * batch call brackets itself with a "sent" event, then a "received" event
 * once the model actually answers, so the UI can show a real waiting state
 * for however long that call takes instead of a step that looks frozen. */
export function latestBatchInfo(events: ExtractionEvent[], stage: string): BatchInfo | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.stage !== stage) continue;
    const index = e.data?.batch_index as number | undefined;
    const total = e.data?.batch_total as number | undefined;
    if (typeof index === "number" && typeof total === "number") {
      return { index, total, phase: (e.data?.batch_phase as BatchPhase | undefined) ?? "sent" };
    }
  }
  return null;
}

/** "Sending…" (pulsing) / "retrying…" (amber, pulsing) while waiting on the
 * network; nothing once the response is actually back — so a batch that's
 * genuinely in flight reads differently from one that just finished. */
export function BatchPhaseTag({ phase }: { phase: BatchPhase }) {
  if (phase === "received") return null;
  const isRetry = phase === "retry";
  return (
    <span
      className={cn(
        "flex items-center gap-1 whitespace-nowrap",
        isRetry ? "text-amber-600" : "text-brand-600"
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 shrink-0 animate-pulse rounded-full",
          isRetry ? "bg-amber-500" : "bg-brand-500"
        )}
      />
      {isRetry ? "retrying…" : "waiting for response…"}
    </span>
  );
}

export function StepNode({
  step,
  status,
  batch,
}: {
  step: (typeof STEPS)[number];
  status: StepStatus;
  batch?: BatchInfo | null;
}) {
  const Icon = step.icon;
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <span
        className={cn(
          "flex h-5 w-5 shrink-0 items-center justify-center rounded-full ring-2 ring-white transition-colors duration-300",
          status === "done"
            ? "bg-emerald-500 text-white"
            : status === "active"
              ? "bg-brand-600 text-white"
              : status === "error"
                ? "bg-rose-500 text-white"
                : "bg-slate-200 text-slate-400"
        )}
      >
        {status === "done" ? (
          <Check className="h-3 w-3" />
        ) : status === "error" ? (
          <X className="h-3 w-3" />
        ) : (
          <Icon className="h-2.5 w-2.5" />
        )}
      </span>
      <span
        className={cn(
          "hidden items-baseline gap-1.5 whitespace-nowrap text-[11px] font-semibold sm:inline-flex",
          status === "pending" ? "text-slate-400" : "text-slate-700"
        )}
      >
        {step.label}
        {status === "active" && batch && batch.total > 1 && (
          <span className="font-normal tabular-nums text-slate-400">
            batch {batch.index}/{batch.total}
          </span>
        )}
        {status === "active" && batch && (
          <span className="hidden text-[10px] font-medium normal-case lg:inline">
            <BatchPhaseTag phase={batch.phase} />
          </span>
        )}
      </span>
    </div>
  );
}

export function Connector({ status }: { status: StepStatus }) {
  return (
    <span className="mx-1.5 h-0.5 min-w-[16px] flex-1 overflow-hidden rounded-full bg-slate-200">
      <span
        className={cn(
          "block h-full rounded-full transition-[width] duration-500 ease-out",
          status === "done"
            ? "w-full bg-emerald-500"
            : status === "active"
              ? "w-full animate-shimmer bg-[linear-gradient(90deg,theme(colors.brand.200),theme(colors.brand.600),theme(colors.brand.200))] bg-[length:200%_100%]"
              : status === "error"
                ? "w-full bg-rose-400"
                : "w-0 bg-slate-200"
        )}
      />
    </span>
  );
}

/** The full step row — icons + labels + connectors, one call. */
export function ExtractStepRow({ events, overall }: { events: ExtractionEvent[]; overall: StepOverall }) {
  const statuses = stepStatuses(events, overall);
  return (
    <div className="flex min-w-0 flex-1 items-center">
      {STEPS.map((step, i) => (
        <div key={step.stage} className="flex min-w-0 flex-1 items-center last:flex-none">
          <StepNode step={step} status={statuses[i]} batch={latestBatchInfo(events, step.stage)} />
          {i < STEPS.length - 1 && <Connector status={statuses[i]} />}
        </div>
      ))}
    </div>
  );
}
