import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, CheckCircle2, AlertTriangle, Square } from "lucide-react";

import { fetchAutoExtractStatus, stopAutoExtract } from "../api/client";
import { cn } from "../lib/utils";

/** Compact status pill for the top nav bar, right beside the existing
 * total/ok/review stats — not a floating panel. Lives in Shell so it shows
 * from any page while a bulk Extract Email run works through the inbox in
 * the background. */
export default function AutoExtractWidget() {
  const qc = useQueryClient();

  const { data: status } = useQuery({
    queryKey: ["auto-extract-status"],
    queryFn: fetchAutoExtractStatus,
    refetchInterval: (query) => {
      const s = query.state.data?.state;
      return s === "running" || s === "stopping" ? 2000 : 8000;
    },
  });

  if (!status || status.state === "idle") return null;

  const isRunning = status.state === "running" || status.state === "stopping";
  const remaining = Math.max(0, (status.total ?? 0) - (status.processed ?? 0));

  const label =
    status.state === "running"
      ? "Auto-extracting"
      : status.state === "stopping"
        ? "Stopping…"
        : status.state === "stopped"
          ? "Stopped"
          : "Done";

  const tone = isRunning
    ? "border-brand-200 bg-brand-50"
    : status.state === "completed"
      ? "border-emerald-200 bg-emerald-50"
      : "border-amber-200 bg-amber-50";

  const tooltip = status.current
    ? status.current.subject
    : status.last_error
      ? `Last error: ${status.last_error}`
      : undefined;

  const handleStop = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await stopAutoExtract();
    qc.invalidateQueries({ queryKey: ["auto-extract-status"] });
  };

  return (
    <span
      title={tooltip}
      className={cn(
        "hidden items-center gap-2 rounded-lg border px-3 py-1.5 lg:inline-flex",
        tone
      )}
    >
      {isRunning ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-brand-500" />
      ) : status.state === "completed" ? (
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
      ) : (
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
      )}
      <span className="font-semibold text-slate-700">{label}</span>
      <span className="text-slate-300">·</span>
      <span className="font-semibold text-slate-700">
        {status.processed}/{status.total}
      </span>
      <span className="text-slate-300">·</span>
      <span className="font-semibold text-emerald-600">{status.succeeded} ok</span>
      <span className="text-slate-300">·</span>
      <span className="font-semibold text-amber-600">{remaining} queued</span>
      {isRunning && (
        <button
          type="button"
          onClick={handleStop}
          disabled={status.state === "stopping"}
          title="Stop after the current thread finishes"
          className="ml-0.5 shrink-0 rounded p-0.5 text-rose-500 transition-colors hover:bg-rose-100 disabled:opacity-50"
        >
          <Square className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}
