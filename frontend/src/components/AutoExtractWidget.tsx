import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Square, PlayCircle, Info } from "lucide-react";

import {
  fetchAutoExtractStatus, fetchAutoExtractCoverage, startAutoExtract, stopAutoExtract,
} from "../api/client";
import { cn } from "../lib/utils";
import { useAuth } from "../lib/auth";
import { useToast } from "./toast";

/** Compact Auto Extract control in the top nav (right). Start and Stop are
 * BOTH always visible — this is an on/off mode, not a one-shot button that
 * disappears once pressed: Start extracts the current backlog once, then
 * leaves the mode on so a new background sync tick (~60s) re-triggers a run
 * on its own whenever mail arrives, with nobody needing to click again.
 * Stop turns that watching off. A small Details button shows the live
 * "X of Y threads extracted" coverage, mailbox-wide.
 *
 * The live pass1/pass2 animation for whichever thread is currently being
 * processed lives in ExtractQueueWidget (the same central nav slot manual
 * Extract Email uses), not here — this stays a compact counts-only pill. */
export default function AutoExtractWidget() {
  const qc = useQueryClient();
  const { canWrite } = useAuth();
  const { toast } = useToast();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsRef = useRef<HTMLDivElement>(null);

  const { data: status } = useQuery({
    queryKey: ["auto-extract-status"],
    queryFn: fetchAutoExtractStatus,
    refetchInterval: (query) => {
      const s = query.state.data?.state;
      return s === "running" || s === "stopping" ? 2000 : 8000;
    },
  });

  const { data: coverage } = useQuery({
    queryKey: ["auto-extract-coverage"],
    queryFn: fetchAutoExtractCoverage,
    enabled: detailsOpen,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (!detailsOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (detailsRef.current && !detailsRef.current.contains(e.target as Node)) setDetailsOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [detailsOpen]);

  const startMut = useMutation({
    mutationFn: startAutoExtract,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["auto-extract-status"] });
      toast("info", "Auto Extract is on",
        "Already-extracted threads are skipped instantly (no model cost). New mail is extracted next, newest first.");
    },
    onError: (e: any) =>
      toast("error", "Couldn't start Auto Extract", e?.response?.data?.detail ?? String(e)),
  });

  const stopMut = useMutation({
    mutationFn: stopAutoExtract,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["auto-extract-status"] });
      toast("info", "Auto Extract is off", "New mail will no longer be extracted automatically.");
    },
    onError: (e: any) =>
      toast("error", "Couldn't stop Auto Extract", e?.response?.data?.detail ?? String(e)),
  });

  if (!status) return null;

  const enabled = status.enabled;
  const isRunning = status.state === "running" || status.state === "stopping";
  const showRun = status.total > 0 && (isRunning || status.state === "completed" || status.state === "stopped");

  // Read-only roles see the widget only while there's something to see —
  // an active run, or the watch mode being on. Nothing to show otherwise.
  if (!canWrite && !isRunning && !enabled) return null;

  const remaining = Math.max(0, status.total - status.processed);

  const tone = isRunning
    ? "border-brand-200 bg-brand-50"
    : enabled
      ? "border-emerald-200 bg-emerald-50"
      : "border-slate-200 bg-slate-50";

  const tooltip = status.current
    ? status.current.subject
    : status.last_error
      ? `Last error: ${status.last_error}`
      : undefined;

  const coverageLabel = coverage
    ? `${coverage.extracted_threads} of ${coverage.total_threads} threads extracted`
    : "Loading…";
  const coveragePct = coverage && coverage.total_threads > 0
    ? Math.round((coverage.extracted_threads / coverage.total_threads) * 100)
    : 0;

  const processing = isRunning ? status.current : null;

  return (
    <span
      title={tooltip}
      className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-1 text-[11px]", tone)}
    >
      {canWrite && (
        <button
          type="button"
          onClick={() => startMut.mutate()}
          disabled={startMut.isPending || enabled}
          title={enabled ? "Auto Extract is already on" : "Turn on: extract the current backlog now, then keep watching for new mail"}
          className={cn(
            "flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-semibold transition-colors",
            enabled
              ? "cursor-default text-emerald-600"
              : "text-brand-700 hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-60"
          )}
        >
          <PlayCircle className="h-3 w-3 shrink-0" />
          <span className="hidden sm:inline">{startMut.isPending ? "…" : "Auto Extract"}</span>
        </button>
      )}

      {isRunning ? (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-brand-500" />
      ) : enabled ? (
        <span className="relative flex h-2 w-2 shrink-0" title="Watching for new mail">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
        </span>
      ) : null}

      {showRun ? (
        <>
          <span className="font-semibold text-slate-700">{status.processed}/{status.total}</span>
          <span className="hidden text-emerald-600 sm:inline">{status.succeeded} ok</span>
          {status.skipped > 0 && (
            <span
              className="hidden text-slate-500 md:inline"
              title="Already extracted, nothing new since — no model call made"
            >
              {status.skipped} already done
            </span>
          )}
          {isRunning && <span className="hidden text-amber-600 md:inline">{remaining} left</span>}
          {isRunning && status.current && (
            <span
              className="hidden max-w-[120px] min-w-0 truncate text-slate-400 xl:inline"
              title={status.current.subject}
            >
              — {status.current.subject}
            </span>
          )}
        </>
      ) : enabled ? (
        <span className="hidden text-emerald-700 sm:inline">Watching for new mail</span>
      ) : null}

        {canWrite && (
          <button
            type="button"
            onClick={() => stopMut.mutate()}
            disabled={stopMut.isPending || !enabled}
            title={enabled ? "Turn off Auto Extract" : "Auto Extract is already off"}
            className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold text-rose-600 transition-colors hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <Square className="h-2.5 w-2.5 fill-current" />
            {stopMut.isPending ? "…" : "Stop"}
          </button>
        )}

      <div className="relative" ref={detailsRef}>
        <button
          type="button"
          onClick={() => setDetailsOpen((v) => !v)}
          title="How many threads have been extracted so far"
          className="shrink-0 rounded p-0.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-600"
        >
          <Info className="h-3 w-3" />
        </button>
        {detailsOpen && (
          <div className="absolute right-0 top-full z-30 mt-1 w-56 rounded-lg border border-slate-200 bg-white p-3 shadow-pop">
            <div className="mb-1 text-[11px] font-semibold text-slate-700">Extraction coverage</div>
            <div className="mb-1.5 text-[11px] text-slate-500">{coverageLabel}</div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-emerald-500" style={{ width: `${coveragePct}%` }} />
            </div>
            <div className="mt-2 text-[11px] text-slate-500">
              Auto Extract is{" "}
              <span className={enabled ? "font-semibold text-emerald-600" : "font-semibold text-slate-500"}>
                {enabled ? "on" : "off"}
              </span>
              {enabled && " — new mail is extracted automatically."}
            </div>
            {processing && (
              <div className="mt-1.5 truncate text-[11px] text-slate-500" title={processing.subject}>
                Processing: <span className="text-slate-700">{processing.subject}</span>
              </div>
            )}
            {status.finished_at && (
              <div className="mt-1 text-[11px] text-slate-400">
                Last run: {status.succeeded} ok, {status.skipped} skipped, {status.failed} failed
              </div>
            )}
          </div>
        )}
      </div>
    </span>
  );
}
