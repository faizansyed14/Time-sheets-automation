/**
 * Live extraction activity — a modal that shows each pipeline stage as it
 * happens (streamed via Server-Sent Events), with a running LLM-call counter,
 * per-stage timing, and the final per-employee outcome (auto-accepted / held).
 *
 * Usage:
 *   const run = useExtractionStream();
 *   run.start((onEvent) => extractFullEmailStream(id, onEvent));
 *   <ExtractionActivityModal run={run} title="Extract Email" onDone={...} />
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2, CircleAlert, XCircle, FileSearch, ScanText, Cpu, BadgeCheck,
  Users, Sparkles, FolderCheck, X, Circle, MinusCircle, CopyCheck, ShieldCheck,
} from "lucide-react";
import type { ExtractionEvent } from "../api/client";
import { Modal, Spinner } from "./ui";
import { cn } from "../lib/utils";

type StartFn = (onEvent: (ev: ExtractionEvent) => void) => Promise<any>;

export interface ExtractionRun {
  events: ExtractionEvent[];
  running: boolean;
  open: boolean;
  llmCalls: number;
  elapsedMs: number;
  error: string | null;
  start: (fn: StartFn) => Promise<any>;
  close: () => void;
}

export function useExtractionStream(): ExtractionRun {
  const [events, setEvents] = useState<ExtractionEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const last = events[events.length - 1];

  const start = useCallback(async (fn: StartFn) => {
    setEvents([]); setError(null); setRunning(true); setOpen(true);
    try {
      const result = await fn((ev) => setEvents((prev) => [...prev, ev]));
      setRunning(false);
      return result;
    } catch (e: any) {
      setError(e?.message ?? String(e));
      setRunning(false);
      throw e;
    }
  }, []);

  const close = useCallback(() => setOpen(false), []);

  return {
    events, running, open, error, start, close,
    llmCalls: last?.llm_calls ?? 0,
    elapsedMs: last?.elapsed_ms ?? 0,
  };
}

const STAGE_ICON: Record<string, typeof Cpu> = {
  start: FileSearch, unpack: FileSearch, format: ScanText, extract: Cpu,
  approval: BadgeCheck, group: Users, autoaccept: Sparkles, file: FolderCheck,
  done: CheckCircle2, error: XCircle,
};

/** Per-agent icon, keyed by the agent's machine name from the backend. */
const AGENT_ICON: Record<string, typeof Cpu> = {
  email: FileSearch, attachment: ScanText, vision: Cpu, approval: BadgeCheck,
  employee: Users, conversation: Users, duplicate: CopyCheck,
  validation: ShieldCheck, decision: Sparkles,
};

type AgentState = "pending" | "running" | "done" | "warn" | "skipped";

interface AgentRow {
  name: string;
  label: string;
  description: string;
  uses_llm: boolean;
  state: AgentState;
  detail?: string;
  tookMs?: number;
}

/** Fold the raw event stream into the agent checklist: the `plan` frame gives
 *  the full line-up up-front, then each `agent` frame updates one row. */
function buildAgentRows(events: ExtractionEvent[]): AgentRow[] {
  const plan = events.find((e) => e.stage === "plan");
  const manifest = (plan?.data?.agents ?? []) as
    { name: string; label: string; description: string; uses_llm: boolean }[];
  const rows: AgentRow[] = manifest.map((a) => ({ ...a, state: "pending" }));
  const byName = new Map(rows.map((r) => [r.name, r]));

  for (const e of events) {
    if (e.stage !== "agent") continue;
    const name = String(e.data?.agent ?? "");
    const row = byName.get(name);
    if (!row) continue;
    if (e.status === "spin") row.state = "running";
    else if (e.status === "ok") row.state = "done";
    else if (e.status === "warn") row.state = "warn";
    else if (e.status === "skip") row.state = "skipped";
    // The orchestrator prefixes the label; strip it for a tighter row.
    row.detail = e.message.replace(new RegExp(`^${row.label}\\s*[—-]\\s*`), "");
    if (typeof e.data?.took_ms === "number") row.tookMs = e.data.took_ms as number;
  }
  return rows;
}

function AgentChecklist({ rows }: { rows: AgentRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="space-y-1">
      {rows.map((r) => {
        const Icon = AGENT_ICON[r.name] ?? Cpu;
        return (
          <div
            key={r.name}
            className={cn(
              "flex items-start gap-2.5 rounded-lg border px-3 py-2 transition-colors",
              r.state === "running" && "border-brand-200 bg-brand-50/70",
              r.state === "done" && "border-emerald-100 bg-emerald-50/40",
              r.state === "warn" && "border-amber-200 bg-amber-50/60",
              r.state === "skipped" && "border-slate-100 bg-slate-50/60 opacity-70",
              r.state === "pending" && "border-slate-100 bg-white opacity-60",
            )}
          >
            <Icon className={cn("mt-0.5 h-4 w-4 shrink-0",
              r.state === "running" ? "text-brand-600" : "text-slate-400")} />
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-1.5">
                <span className={cn("text-sm font-semibold",
                  r.state === "pending" ? "text-slate-500" : "text-slate-800")}>
                  {r.label}
                </span>
                {r.uses_llm && (
                  <span className="rounded-full border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-violet-700">
                    LLM
                  </span>
                )}
              </span>
              <span className="mt-0.5 block text-xs text-slate-500">
                {r.detail ?? r.description}
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-2 pt-0.5">
              {typeof r.tookMs === "number" && r.state !== "running" && (
                <span className="text-[10px] tabular-nums text-slate-400">
                  {(r.tookMs / 1000).toFixed(1)}s
                </span>
              )}
              {r.state === "running" && <Spinner className="h-4 w-4" />}
              {r.state === "done" && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
              {r.state === "warn" && <CircleAlert className="h-4 w-4 text-amber-500" />}
              {r.state === "skipped" && <MinusCircle className="h-4 w-4 text-slate-300" />}
              {r.state === "pending" && <Circle className="h-4 w-4 text-slate-200" />}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function StatusMark({ status }: { status: ExtractionEvent["status"] }) {
  if (status === "spin") return <Spinner className="h-4 w-4" />;
  if (status === "ok") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
  if (status === "warn") return <CircleAlert className="h-4 w-4 text-amber-500" />;
  if (status === "fail") return <XCircle className="h-4 w-4 text-rose-500" />;
  return <span className="h-4 w-4" />;
}

export function ExtractionActivityModal({
  run, title, onDone,
}: {
  run: ExtractionRun;
  title: string;
  onDone?: () => void;
}) {
  // Auto-scroll to the newest event.
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [run.events.length]);

  // The agent checklist, plus the non-agent frames for the detail log.
  const agentRows = buildAgentRows(run.events);
  const detailEvents = run.events.filter(
    (e) => e.stage !== "start" && e.stage !== "plan" && e.stage !== "agent");

  // Outcomes surfaced by the auto-accept stage.
  const outcomes = run.events.filter((e) => e.stage === "autoaccept");
  const filed = outcomes.filter((e) => e.status === "ok").length;
  const held = outcomes.filter((e) => e.status !== "ok").length;

  return (
    <Modal open={run.open} onClose={run.running ? () => {} : run.close}
      title={run.running ? `${title} — working…` : `${title} — done`}
      subtitle="Live pipeline activity" wide>
      <div className="space-y-3">
        {/* Live header */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 font-semibold text-slate-600">
            <Cpu className="h-3.5 w-3.5 text-slate-400" /> LLM calls: {run.llmCalls}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 font-semibold text-slate-600">
            {(run.elapsedMs / 1000).toFixed(1)}s
          </span>
          {filed > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 font-semibold text-emerald-700">
              <Sparkles className="h-3.5 w-3.5" /> {filed} auto-accepted
            </span>
          )}
          {held > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 font-semibold text-amber-700">
              <CircleAlert className="h-3.5 w-3.5" /> {held} held for review
            </span>
          )}
        </div>

        {/* Agent checklist — which agent is working right now */}
        <AgentChecklist rows={agentRows} />

        {/* Raw event log (stages + per-employee outcomes) */}
        {detailEvents.length > 0 && (
          <details className="rounded-xl border border-slate-200 bg-slate-50/60" open={!agentRows.length}>
            <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-500">
              Detailed activity log ({detailEvents.length})
            </summary>
            <div className="max-h-[32vh] space-y-1 overflow-y-auto p-2 pt-0">
              {detailEvents.map((e, i) => {
                const Icon = STAGE_ICON[e.stage] ?? Cpu;
                return (
                  <div key={i} className={cn(
                    "flex items-start gap-2.5 rounded-lg px-2.5 py-1.5 text-sm",
                    e.status === "warn" && "bg-amber-50/70",
                    e.status === "fail" && "bg-rose-50/70",
                    e.stage === "autoaccept" && e.status === "ok" && "bg-emerald-50/70",
                  )}>
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                    <span className="min-w-0 flex-1 text-slate-700">{e.message}</span>
                    <span className="shrink-0 pt-0.5"><StatusMark status={e.status} /></span>
                    <span className="w-12 shrink-0 pt-0.5 text-right text-[10px] tabular-nums text-slate-400">
                      {(e.elapsed_ms / 1000).toFixed(1)}s
                    </span>
                  </div>
                );
              })}
              <div ref={bottomRef} />
            </div>
          </details>
        )}

        {run.error && (
          <div className="flex items-center gap-2 rounded-lg bg-rose-50 px-2.5 py-2 text-sm text-rose-700">
            <XCircle className="h-4 w-4" /> {run.error}
          </div>
        )}

        {!run.running && (
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => { run.close(); onDone?.(); }}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700"
            >
              <X className="h-4 w-4" /> Close
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}
