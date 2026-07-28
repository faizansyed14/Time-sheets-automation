import { useEffect, useState } from "react";
import { Check, X, Trash2, FileSearch, Cpu } from "lucide-react";
import { useExtractQueue } from "../lib/extractQueue";
import type { ExtractTask } from "../lib/extractQueue";
import { cn } from "../lib/utils";

const FLASH_MS = 6000;

type StepStatus = "pending" | "active" | "done" | "error";

const STEPS: { stage: string; label: string; icon: typeof Trash2 }[] = [
  { stage: "unpack", label: "Reduce junk", icon: Trash2 },
  { stage: "pass1", label: "Pass 1", icon: FileSearch },
  { stage: "pass2", label: "Pass 2", icon: Cpu },
];

function stepStatuses(task: ExtractTask): StepStatus[] {
  const isDone = (stage: string) => task.events.some((e) => e.stage === stage && e.status === "ok");
  const isSpinning = (stage: string) => task.events.some((e) => e.stage === stage && e.status === "spin");
  const lastStage = task.events[task.events.length - 1]?.stage;
  const erroredStage = task.status === "error" ? lastStage : null;

  const statuses = STEPS.map(({ stage }): StepStatus => {
    if (erroredStage === stage) return "error";
    if (isDone(stage)) return "done";
    if (isSpinning(stage)) return "active";
    return "pending";
  });
  // The run finished cleanly (e.g. pass 2 skipped — nothing confirmed) —
  // treat every not-yet-touched step as done rather than stuck "pending".
  if (task.status === "done") return statuses.map((s) => (s === "pending" ? "done" : s));
  return statuses;
}

function StepNode({ step, status }: { step: (typeof STEPS)[number]; status: StepStatus }) {
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
          "hidden whitespace-nowrap text-[11px] font-semibold sm:inline",
          status === "pending" ? "text-slate-400" : "text-slate-700"
        )}
      >
        {step.label}
      </span>
    </div>
  );
}

function Connector({ status }: { status: StepStatus }) {
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

/** Full-width 3-step Extract Email progress bar for the top nav — Reduce
 * junk → Pass 1 → Pass 2 — visible on every page so a background run stays
 * in view no matter where the user navigates. Renders nothing when idle. */
export default function ExtractQueueWidget() {
  const { tasks, current, queuedCount } = useExtractQueue();
  const [, tick] = useState(0);

  const lastFinished = [...tasks].reverse().find((t) => t.status === "done" || t.status === "error") ?? null;
  const finishedAgoMs = lastFinished?.finishedAt ? Date.now() - lastFinished.finishedAt : Infinity;
  const showFlash = !current && !!lastFinished && finishedAgoMs < FLASH_MS;
  const visible = current ?? (showFlash ? lastFinished : null);

  useEffect(() => {
    if (!showFlash || !lastFinished?.finishedAt) return;
    const remain = Math.max(200, FLASH_MS - (Date.now() - lastFinished.finishedAt));
    const timer = window.setTimeout(() => tick((n) => n + 1), remain);
    return () => window.clearTimeout(timer);
  }, [showFlash, lastFinished?.id, lastFinished?.finishedAt]);

  if (!visible) return null;

  const statuses = stepStatuses(visible);
  const isQueuedOnly = visible.status === "queued";
  // While actually working, the page title steps aside (see Shell) and this
  // bar runs bare, left corner of the header to right corner beside the
  // stats pill. The brief done/failed flash afterwards is a small chip
  // instead, since the title is back by then.
  const isLive = !!current;

  const body = isQueuedOnly ? (
    <span className="text-[11px] font-semibold text-slate-500">Queued — waiting to start…</span>
  ) : (
    <div className="flex min-w-0 flex-1 items-center">
      {STEPS.map((step, i) => (
        <div key={step.stage} className="flex min-w-0 flex-1 items-center last:flex-none">
          <StepNode step={step} status={statuses[i]} />
          {i < STEPS.length - 1 && <Connector status={statuses[i]} />}
        </div>
      ))}
    </div>
  );

  const trailer = (
    <>
      <span className="hidden min-w-0 max-w-[220px] truncate text-[11px] text-slate-400 md:inline">
        {visible.subject}
      </span>
      {queuedCount > 0 && (
        <span
          title={`${queuedCount} more thread(s) waiting`}
          className="shrink-0 rounded-full bg-brand-600 px-1.5 py-px text-[9px] font-bold text-white"
        >
          +{queuedCount}
        </span>
      )}
    </>
  );

  if (isLive) {
    return (
      <div title={visible.subject} className="flex min-w-0 flex-1 items-center gap-3">
        {body}
        {trailer}
      </div>
    );
  }

  return (
    <div
      title={visible.subject}
      className="flex min-w-0 max-w-fit items-center gap-3 rounded-lg border border-slate-200/70 bg-white/70 px-3 py-1.5"
    >
      {body}
      {trailer}
    </div>
  );
}
