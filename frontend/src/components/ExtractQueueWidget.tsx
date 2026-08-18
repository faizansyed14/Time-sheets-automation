import { useEffect, useState } from "react";
import { useExtractQueue } from "../lib/extractQueue";
import { ExtractStepRow, type StepOverall } from "./ExtractSteps";

const FLASH_MS = 6000;

/** Full-width 3-step Extract Email progress bar for the top nav — Reduce
 * junk → Pass 1 → Pass 2 — visible on every page so a background run stays
 * in view no matter where the user navigates. Renders nothing when idle.
 *
 * Manual Extract Email only — Auto Extract's own progress lives entirely in
 * AutoExtractWidget's compact pill, not here. */
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

  const isQueuedOnly = visible.status === "queued";
  const overall: StepOverall = visible.status === "error" ? "error" : visible.status === "done" ? "done" : "running";
  // While actually working, the page title steps aside (see Shell) and this
  // bar runs bare, left corner of the header to right corner beside the
  // stats pill. The brief done/failed flash afterwards is a small chip
  // instead, since the title is back by then.
  const isLive = !!current;

  const body = isQueuedOnly ? (
    <span className="text-[11px] font-semibold text-slate-500">Queued — waiting to start…</span>
  ) : (
    <ExtractStepRow events={visible.events} overall={overall} />
  );

  // Live, human-readable detail of exactly what's happening right now — the
  // same message the backend just emitted (e.g. "Pass 2 — sending batch 2 of
  // 3 (4 sheet(s)) to gpt-4o…") — shown as the hover tooltip and, while live,
  // as a small line under the steps so it's visible without hovering.
  const latestMessage = visible.events[visible.events.length - 1]?.message;
  const tooltip = latestMessage ? `${visible.subject} — ${latestMessage}` : visible.subject;

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
      <div title={tooltip} className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          {body}
          {trailer}
        </div>
        {latestMessage && (
          <p className="hidden min-w-0 truncate pl-0.5 text-[10px] text-slate-400 xl:block">
            {latestMessage}
          </p>
        )}
      </div>
    );
  }

  return (
    <div
      title={tooltip}
      className="flex min-w-0 max-w-fit items-center gap-3 rounded-lg border border-slate-200/70 bg-white/70 px-3 py-1.5"
    >
      {body}
      {trailer}
    </div>
  );
}
