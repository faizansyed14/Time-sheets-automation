/**
 * Live extraction activity — a panel docked bottom-right, like a chat widget,
 * showing each stage as it happens (streamed via Server-Sent Events).
 *
 * Docked rather than centred on purpose: this runs FROM the inbox, and a
 * full-screen modal covered the very email the run is about. It collapses to
 * a one-line status bar and expands to the detail.
 *
 * The headline content is the two model calls: what pass 1 decided about each
 * attachment (timesheet / certificate / noise, whose it is, approval), then
 * what pass 2 actually pulled out per sheet.
 *
 * Usage:
 *   const run = useExtractionStream();
 *   run.start((onEvent) => extractFullEmailStream(id, onEvent));
 *   <ExtractionActivityModal run={run} title="Extract Email" onDone={...} />
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  CheckCircle2, CircleAlert, XCircle, FileSearch, ScanText, Cpu, BadgeCheck,
  Users, Sparkles, FolderCheck, X, Circle, MinusCircle, CopyCheck, ShieldCheck,
  ChevronDown, ChevronUp, ChevronRight,
} from "lucide-react";
import type { ExtractionEvent } from "../api/client";
import { Spinner } from "./ui";
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
  pass1: FileSearch, pass2: Cpu,
  approval: BadgeCheck, group: Users, autoaccept: Sparkles, file: FolderCheck,
  done: CheckCircle2, error: XCircle,
};

/** Per-agent icon, keyed by the agent's machine name from the backend. */
const AGENT_ICON: Record<string, typeof Cpu> = {
  thread: Cpu, email: FileSearch, attachment: ScanText, vision: Cpu,
  approval: BadgeCheck, employee: Users, conversation: Users, duplicate: CopyCheck,
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

/** Live pass results — what pass 1 decided, what pass 2 pulled out. */
interface PassItem {
  source: string;
  kind: string;
  employee?: string | null;
  employee_id?: string | null;
  format_id?: string | null;
  period?: string | null;
  signature?: boolean;
}
interface PassResult {
  source: string;
  employee?: string | null;
  employee_id?: string | null;
  month?: number | null;
  year?: number | null;
  days_covered?: number;
  period_type?: string;
  leaves?: Record<string, number>;
  total_days?: number;
}

const KIND_TONE: Record<string, string> = {
  timesheet: "border-brand-200 bg-brand-50 text-brand-700",
  leave_certificate: "border-violet-200 bg-violet-50 text-violet-700",
  approval: "border-emerald-200 bg-emerald-50 text-emerald-700",
  other: "border-slate-200 bg-slate-100 text-slate-500",
};

/** The two model calls, rendered as they happen. This is the part a reviewer
 *  actually watches: what went up, what came back, per sheet. */
function PassPanel({ events }: { events: ExtractionEvent[] }) {
  const p1Start = events.find((e) => e.stage === "pass1" && e.status === "spin");
  const p1Done = events.find((e) => e.stage === "pass1" && e.status === "ok");
  const p2Start = events.find((e) => e.stage === "pass2" && e.status === "spin");
  const p2Done = events.find((e) => e.stage === "pass2" && e.status === "ok");
  if (!p1Start) return null;

  const d1 = (p1Done?.data ?? {}) as {
    items?: PassItem[]; noise?: string[];
    approval?: { detected?: boolean; evidence?: string; where?: string };
    summary?: { headline?: string; status?: string; action_needed?: string };
  };
  const s1 = (p1Start.data ?? {}) as {
    files?: string[]; images?: string[]; message_count?: number;
  };
  const d2 = (p2Done?.data ?? {}) as { results?: PassResult[] };
  const s2 = (p2Start?.data ?? {}) as { sheets?: string[] };

  const Row = ({
    n, title, running, done, children,
  }: {
    n: number; title: string; running: boolean; done: boolean; children?: ReactNode;
  }) => (
    <div className={cn(
      "rounded-lg border p-2.5",
      running ? "border-brand-200 bg-brand-50/60"
        : done ? "border-emerald-100 bg-emerald-50/30"
        : "border-slate-100 bg-white opacity-60")}>
      <div className="flex items-center gap-2">
        <span className={cn(
          "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white",
          done ? "bg-emerald-500" : running ? "bg-brand-600" : "bg-slate-300")}>
          {n}
        </span>
        <span className="flex-1 text-xs font-semibold text-slate-800">{title}</span>
        {running && <Spinner className="h-3.5 w-3.5" />}
        {done && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />}
      </div>
      {children && <div className="mt-2 space-y-1.5">{children}</div>}
    </div>
  );

  return (
    <div className="space-y-2">
      <Row n={1} title="Understanding the conversation"
           running={!!p1Start && !p1Done} done={!!p1Done}>
        {!p1Done && (
          <p className="text-[11px] text-slate-500">
            Sent {s1.message_count ?? 0} message(s)
            {(s1.files?.length ?? 0) > 0 && `, ${s1.files!.length} file(s)`}
            {(s1.images?.length ?? 0) > 0 && `, ${s1.images!.length} image(s)`}
            {" "}— finding timesheets, employees and approval…
          </p>
        )}
        {p1Done && (
          <>
            {(d1.items ?? []).map((it, i) => (
              <div key={i} className="flex items-center gap-1.5 text-[11px]">
                <span className={cn(
                  "shrink-0 rounded border px-1 py-0.5 text-[9px] font-bold uppercase",
                  KIND_TONE[it.kind] ?? KIND_TONE.other)}>
                  {it.kind === "leave_certificate" ? "cert" : it.kind}
                </span>
                <span className="min-w-0 flex-1 truncate text-slate-700" title={it.source}>
                  {it.source}
                </span>
                {it.employee && (
                  <span className="shrink-0 font-medium text-slate-600">{it.employee}</span>
                )}
                {it.signature && (
                  <BadgeCheck className="h-3 w-3 shrink-0 text-emerald-500" aria-label="signed" />
                )}
              </div>
            ))}
            {(d1.noise?.length ?? 0) > 0 && (
              <p className="text-[10px] text-slate-400">
                Ignored as noise: {d1.noise!.join(", ")}
              </p>
            )}
            {d1.approval && (
              <p className={cn("text-[11px] font-medium",
                d1.approval.detected ? "text-emerald-700" : "text-amber-700")}>
                {d1.approval.detected
                  ? `Approved (${d1.approval.where}) — ${d1.approval.evidence || "signature found"}`
                  : "No manager approval found"}
              </p>
            )}
            {d1.summary?.headline && (
              <p className="rounded bg-white/70 px-2 py-1.5 text-[11px] leading-relaxed text-slate-600">
                {d1.summary.headline}
              </p>
            )}
          </>
        )}
      </Row>

      <Row n={2} title="Extracting leave from the confirmed sheets"
           running={!!p2Start && !p2Done} done={!!p2Done}>
        {p2Start && !p2Done && (
          <p className="text-[11px] text-slate-500">
            Reading {(s2.sheets ?? []).length} sheet(s)…
          </p>
        )}
        {p2Done && (d2.results ?? []).map((r, i) => (
          <div key={i} className="rounded border border-slate-100 bg-white px-2 py-1.5">
            <div className="flex items-center gap-1.5 text-[11px]">
              <span className="min-w-0 flex-1 truncate font-medium text-slate-800">
                {r.employee || r.source}
              </span>
              {r.month && (
                <span className="shrink-0 text-slate-500">{r.month}/{r.year}</span>
              )}
              <span className="shrink-0 font-semibold text-brand-600">
                {r.total_days ?? 0} day(s)
              </span>
            </div>
            {Object.keys(r.leaves ?? {}).length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {Object.entries(r.leaves!).map(([k, n]) => (
                  <span key={k} className="rounded bg-slate-100 px-1 py-0.5 text-[9px] font-semibold text-slate-600">
                    {k.replace("_", " ")} {n}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </Row>
    </div>
  );
}

export function ExtractionActivityModal({
  run, title, onDone,
}: {
  run: ExtractionRun;
  title: string;
  onDone?: () => void;
}) {
  // Collapsed by default once finished; expanded while working so the passes
  // are visible as they happen.
  const [expanded, setExpanded] = useState(true);
  const [showLog, setShowLog] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (showLog) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [run.events.length, showLog]);

  if (!run.open) return null;

  const agentRows = buildAgentRows(run.events);
  const detailEvents = run.events.filter(
    (e) => e.stage !== "start" && e.stage !== "plan" && e.stage !== "agent");
  const outcomes = run.events.filter((e) => e.stage === "autoaccept");
  const filed = outcomes.filter((e) => e.status === "ok").length;
  const held = outcomes.filter((e) => e.status !== "ok").length;
  const last = run.events[run.events.length - 1];

  // Docked bottom-right like a chat widget, so the page underneath stays
  // usable — a centred modal blocked the very inbox the run is about.
  return createPortal(
    <div className="fixed bottom-4 right-4 z-50 flex w-[min(26rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-pop">
      {/* Header — always visible, click to expand/collapse */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2.5 text-left hover:bg-slate-100"
      >
        <span className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-lg",
          run.running ? "bg-brand-600" : run.error ? "bg-rose-500" : "bg-emerald-500")}>
          {run.running
            ? <Spinner className="h-3.5 w-3.5 border-white/40 border-t-white" />
            : run.error
            ? <XCircle className="h-3.5 w-3.5 text-white" />
            : <CheckCircle2 className="h-3.5 w-3.5 text-white" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-bold text-slate-800">
            {title} {run.running ? "— working…" : run.error ? "— failed" : "— done"}
          </span>
          <span className="block truncate text-[11px] text-slate-500">
            {run.running && last ? last.message : `${run.llmCalls} AI call(s) · ${(run.elapsedMs / 1000).toFixed(1)}s`}
          </span>
        </span>
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
          : <ChevronUp className="h-4 w-4 shrink-0 text-slate-400" />}
        {!run.running && (
          <span
            role="button"
            tabIndex={0}
            aria-label="Close"
            onClick={(e) => { e.stopPropagation(); run.close(); onDone?.(); }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.stopPropagation(); run.close(); onDone?.();
              }
            }}
            className="shrink-0 rounded p-1 text-slate-400 hover:bg-white hover:text-slate-700"
          >
            <X className="h-3.5 w-3.5" />
          </span>
        )}
      </button>

      {expanded && (
        <div className="max-h-[60vh] space-y-2.5 overflow-y-auto p-3">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 font-semibold text-slate-600">
              <Cpu className="h-3 w-3 text-slate-400" /> {run.llmCalls} AI call(s)
            </span>
            <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 font-semibold text-slate-600">
              {(run.elapsedMs / 1000).toFixed(1)}s
            </span>
            {filed > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700">
                <Sparkles className="h-3 w-3" /> {filed} AI recommends
              </span>
            )}
            {held > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 font-semibold text-amber-700">
                <CircleAlert className="h-3 w-3" /> {held} to review
              </span>
            )}
          </div>

          {/* The two model calls, live */}
          <PassPanel events={run.events} />

          {/* Everything after the model: matching, duplicates, filing */}
          {agentRows.length > 0 && <AgentChecklist rows={agentRows} />}

          {run.error && (
            <div className="flex items-start gap-1.5 rounded-lg bg-rose-50 px-2 py-1.5 text-[11px] text-rose-700">
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {run.error}
            </div>
          )}

          {detailEvents.length > 0 && (
            <div>
              <button
                type="button"
                onClick={() => setShowLog((v) => !v)}
                className="flex w-full items-center gap-1 rounded-lg border border-slate-200 px-2 py-1.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
              >
                {showLog ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                Full activity log ({detailEvents.length})
              </button>
              {showLog && (
                <div className="mt-1 max-h-52 space-y-0.5 overflow-y-auto rounded-lg bg-slate-50/70 p-1.5">
                  {detailEvents.map((e, i) => {
                    const Icon = STAGE_ICON[e.stage] ?? Cpu;
                    return (
                      <div key={i} className={cn(
                        "flex items-start gap-1.5 rounded px-1.5 py-1 text-[11px]",
                        e.status === "warn" && "bg-amber-50/70",
                        e.status === "fail" && "bg-rose-50/70",
                      )}>
                        <Icon className="mt-0.5 h-3 w-3 shrink-0 text-slate-400" />
                        <span className="min-w-0 flex-1 text-slate-700">{e.message}</span>
                        <span className="w-9 shrink-0 text-right text-[10px] tabular-nums text-slate-400">
                          {(e.elapsed_ms / 1000).toFixed(1)}s
                        </span>
                      </div>
                    );
                  })}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>,
    document.body
  );
}
