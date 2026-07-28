import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, CheckCircle2, AlertTriangle, Square, PlayCircle } from "lucide-react";

import { fetchAutoExtractStatus, startAutoExtract, stopAutoExtract } from "../api/client";
import { cn } from "../lib/utils";
import { useAuth } from "../lib/auth";
import { useToast } from "./toast";

/** Compact Auto Extract control in the top nav (right). Idle → small Start;
 * running/done → progress pill with Stop. */
export default function AutoExtractWidget() {
  const qc = useQueryClient();
  const { canWrite } = useAuth();
  const { toast } = useToast();

  const { data: status } = useQuery({
    queryKey: ["auto-extract-status"],
    queryFn: fetchAutoExtractStatus,
    refetchInterval: (query) => {
      const s = query.state.data?.state;
      return s === "running" || s === "stopping" ? 2000 : 8000;
    },
  });

  const startMut = useMutation({
    mutationFn: startAutoExtract,
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ["auto-extract-status"] });
      toast("info", "Auto Extract started", `Processing ${s.total} thread(s) in the background.`);
    },
    onError: (e: any) =>
      toast("error", "Couldn't start Auto Extract", e?.response?.data?.detail ?? String(e)),
  });

  const idle = !status || status.state === "idle";
  const isRunning = status?.state === "running" || status?.state === "stopping";

  if (idle) {
    if (!canWrite) return null;
    return (
      <button
        type="button"
        onClick={() => startMut.mutate()}
        disabled={startMut.isPending}
        title="Extract every inbox thread in the background"
        className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2 py-1 text-[11px] font-semibold text-brand-700 transition-colors hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <PlayCircle className="h-3 w-3" />
        {startMut.isPending ? "…" : "Auto Extract"}
      </button>
    );
  }

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
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]",
        tone
      )}
    >
      {isRunning ? (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-brand-500" />
      ) : status.state === "completed" ? (
        <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-500" />
      ) : (
        <AlertTriangle className="h-3 w-3 shrink-0 text-amber-500" />
      )}
      <span className="hidden font-semibold text-slate-700 sm:inline">{label}</span>
      <span className="font-semibold text-slate-700">
        {status.processed}/{status.total}
      </span>
      <span className="hidden text-emerald-600 sm:inline">{status.succeeded} ok</span>
      <span className="hidden text-amber-600 md:inline">{remaining} left</span>
      {isRunning && (
        <button
          type="button"
          onClick={handleStop}
          disabled={status.state === "stopping"}
          title="Stop after the current thread finishes"
          className="shrink-0 rounded p-0.5 text-rose-500 transition-colors hover:bg-rose-100 disabled:opacity-50"
        >
          <Square className="h-2.5 w-2.5" />
        </button>
      )}
    </span>
  );
}
