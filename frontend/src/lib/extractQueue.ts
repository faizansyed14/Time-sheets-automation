/**
 * Global Extract Email queue — a module-level singleton (not React context),
 * so it keeps running and stays visible in the top nav bar no matter which
 * page the user navigates to. Clicking "Extract Email" on several threads in
 * a row queues them; they run one at a time so pass1/pass2 events from
 * different runs never interleave into the same live-activity panel.
 */
import { useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { extractFullEmailStream, type ExtractionEvent, type PipelineFile } from "../api/client";

export type ExtractTaskStatus = "queued" | "running" | "done" | "error";

export interface ExtractTask {
  id: string;
  msgId: string;
  subject: string;
  forceFull: boolean;
  status: ExtractTaskStatus;
  events: ExtractionEvent[];
  error: string | null;
  result: { staged: PipelineFile[]; groups: number; message: string } | null;
  queuedAt: number;
  startedAt: number | null;
  finishedAt: number | null;
}

type ToastAction = { label: string; onClick: () => void };
type ToastFn = (
  kind: "success" | "error" | "warning" | "info",
  title: string,
  detail?: string,
  action?: ToastAction
) => void;

const MAX_FINISHED_KEPT = 5;

class ExtractQueueStore {
  private tasks: ExtractTask[] = [];
  private listeners = new Set<() => void>();
  private processing = false;
  private qc: QueryClient | null = null;
  private toastFn: ToastFn | null = null;
  private navigateFn: ((path: string) => void) | null = null;
  private seq = 0;

  init(qc: QueryClient, toastFn: ToastFn, navigateFn: (path: string) => void) {
    this.qc = qc;
    this.toastFn = toastFn;
    this.navigateFn = navigateFn;
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.tasks;

  private emit() {
    for (const l of this.listeners) l();
  }

  private setTasks(next: ExtractTask[]) {
    this.tasks = next;
    this.emit();
  }

  private patch(id: string, patch: Partial<ExtractTask>) {
    this.setTasks(this.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  /** Queued or currently running for this email thread. */
  isBusy = (msgId: string) =>
    this.tasks.some((t) => t.msgId === msgId && (t.status === "queued" || t.status === "running"));

  taskFor = (msgId: string) =>
    this.tasks.find((t) => t.msgId === msgId && (t.status === "queued" || t.status === "running")) ?? null;

  /** Remove a task from the visible list (a queued task is effectively
   *  cancelled; a running one keeps working, it just stops being shown). */
  dismiss = (id: string) => {
    this.setTasks(this.tasks.filter((t) => t.id !== id));
  };

  enqueue = (msgId: string, subject: string, forceFull = false): string => {
    const finishedIds = this.tasks
      .filter((t) => t.status === "done" || t.status === "error")
      .slice(0, -MAX_FINISHED_KEPT)
      .map((t) => t.id);
    const kept = finishedIds.length ? this.tasks.filter((t) => !finishedIds.includes(t.id)) : this.tasks;

    const task: ExtractTask = {
      id: `x${++this.seq}`,
      msgId,
      subject: subject || "(no subject)",
      forceFull,
      status: "queued",
      events: [],
      error: null,
      result: null,
      queuedAt: Date.now(),
      startedAt: null,
      finishedAt: null,
    };
    this.setTasks([...kept, task]);
    void this.processNext();
    return task.id;
  };

  private async processNext() {
    if (this.processing) return;
    const next = this.tasks.find((t) => t.status === "queued");
    if (!next) return;
    this.processing = true;
    this.patch(next.id, { status: "running", startedAt: Date.now() });
    try {
      const result = (await extractFullEmailStream(
        next.msgId,
        (ev: ExtractionEvent) => {
          const cur = this.tasks.find((t) => t.id === next.id);
          if (cur) this.patch(next.id, { events: [...cur.events, ev] });
        },
        next.forceFull
      )) as { staged: PipelineFile[]; groups: number; message: string };

      this.patch(next.id, { status: "done", result, finishedAt: Date.now() });

      const review = (result?.staged ?? []).filter((f) => f.status === "needs_review");
      this.toastFn?.(
        review.length ? "info" : "success",
        review.length ? "Extract Email — needs review" : "Extract Email complete",
        `"${next.subject}" · ${result?.groups ?? 0} record(s)${
          review.length ? ` · ${review.length} to review` : ""
        }`,
        review.length
          ? { label: "Review now", onClick: () => this.navigateFn?.("/pipeline?status=needs_review") }
          : undefined
      );

      this.qc?.invalidateQueries({ queryKey: ["inbox-threads"] });
      this.qc?.invalidateQueries({ queryKey: ["email", next.msgId] });
      this.qc?.invalidateQueries({ queryKey: ["email-thread", next.msgId] });
      this.qc?.invalidateQueries({ queryKey: ["pipeline"] });
      this.qc?.invalidateQueries({ queryKey: ["pipeline-stats"] });
      this.qc?.invalidateQueries({ queryKey: ["coverage"] });
    } catch (e: any) {
      const message = e?.message ?? String(e);
      this.patch(next.id, { status: "error", error: message, finishedAt: Date.now() });
      this.toastFn?.("error", "Extract Email failed", `"${next.subject}" · ${message}`);
    } finally {
      this.processing = false;
      void this.processNext();
    }
  }
}

export const extractQueueStore = new ExtractQueueStore();

export function useExtractQueue() {
  const tasks = useSyncExternalStore(extractQueueStore.subscribe, extractQueueStore.getSnapshot);
  const current = tasks.find((t) => t.status === "running") ?? null;
  const queuedCount = tasks.filter((t) => t.status === "queued").length;
  return {
    tasks,
    current,
    queuedCount,
    enqueue: extractQueueStore.enqueue,
    isBusy: extractQueueStore.isBusy,
    taskFor: extractQueueStore.taskFor,
    dismiss: extractQueueStore.dismiss,
  };
}
