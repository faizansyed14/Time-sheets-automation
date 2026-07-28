/**
 * Live extraction activity — docked bottom-right, showing the two-pass thread
 * pipeline as it runs (SSE):
 *
 *   1. Auto-dropped attachments (size / OCR noise filter)
 *   2. Pass 1 triage result (every classified item)
 *   3. Confirmed timesheets / leave certificates sent to pass 2
 *   4. Pass 2 real JSON (model response + normalised sheets)
 *
 * No agent checklist — Decision / Approval / Parser agents are gone; matching
 * and staging are silent server steps after the two model calls.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  CheckCircle2, CircleAlert, XCircle, FileSearch, Cpu, BadgeCheck,
  Sparkles, FolderCheck, X, ChevronDown, ChevronUp, ChevronRight,
  Trash2, FileSpreadsheet, Braces,
} from "lucide-react";
import type { ExtractionEvent, ThreadSummary } from "../api/client";
import { Spinner } from "./ui";
import { cn } from "../lib/utils";
import { ThreadSummaryBox } from "./ThreadSummaryBox";

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
  start: FileSearch, unpack: FileSearch, extract: Cpu,
  pass1: FileSearch, pass2: Cpu,
  autoaccept: Sparkles, file: FolderCheck,
  done: CheckCircle2, error: XCircle,
};

interface DroppedItem {
  name?: string;
  reason?: string;
  size?: number;
  mime?: string;
  filter?: "size" | "ocr" | "dup" | string;
  ocr_chars?: number;
  thumb?: string | null;
  kept_key?: string | null;
  kept_name?: string | null;
}
interface PassItem {
  source: string;
  /** The real attachment/body name — shown instead of the internal "[A#]"
   *  source label, which means nothing to a reviewer watching the run. */
  name?: string;
  kind: string;
  employee?: string | null;
  employee_id?: string | null;
  period?: string | null;
  signature?: boolean;
  notes?: string;
  thumb?: string | null;
  key?: string | null;
}
interface KeptItem {
  key?: string;
  name?: string;
  mime?: string;
  size?: number;
  thumb?: string | null;
}

const KIND_TONE: Record<string, string> = {
  timesheet: "border-brand-200 bg-brand-50 text-brand-700",
  leave_certificate: "border-violet-200 bg-violet-50 text-violet-700",
  approval: "border-emerald-200 bg-emerald-50 text-emerald-700",
  other: "border-slate-200 bg-slate-100 text-slate-500",
  noise: "border-slate-200 bg-slate-50 text-slate-400",
};

function Section({
  n, title, icon: Icon, running, done, idle, children,
}: {
  n: number | string;
  title: string;
  icon: typeof Cpu;
  running?: boolean;
  done?: boolean;
  idle?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={cn(
      "rounded-lg border p-2.5",
      running ? "border-brand-200 bg-brand-50/60"
        : done ? "border-emerald-100 bg-emerald-50/30"
        : idle ? "border-slate-100 bg-white opacity-55"
        : "border-slate-100 bg-white")}>
      <div className="flex items-center gap-2">
        <span className={cn(
          "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white",
          done ? "bg-emerald-500" : running ? "bg-brand-600" : "bg-slate-300")}>
          {n}
        </span>
        <Icon className={cn("h-3.5 w-3.5 shrink-0",
          running ? "text-brand-600" : done ? "text-emerald-600" : "text-slate-400")} />
        <span className="flex-1 text-xs font-semibold text-slate-800">{title}</span>
        {running && <Spinner className="h-3.5 w-3.5" />}
        {done && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />}
      </div>
      {children && <div className="mt-2 space-y-1.5">{children}</div>}
    </div>
  );
}

function JsonBlock({ value, label }: { value: unknown; label: string }) {
  const [open, setOpen] = useState(true);
  const text = JSON.stringify(value, null, 2);
  return (
    <div className="rounded border border-slate-200 bg-slate-950/95">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-[10px] font-semibold uppercase tracking-wide text-slate-300 hover:bg-white/5"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Braces className="h-3 w-3" />
        {label}
      </button>
      {open && (
        <pre className="max-h-56 overflow-auto border-t border-white/10 px-2 py-1.5 text-[10px] leading-relaxed text-emerald-200/90 whitespace-pre-wrap break-all">
          {text}
        </pre>
      )}
    </div>
  );
}

function Thumb({ src, alt }: { src?: string | null; alt: string }) {
  if (!src) {
    return (
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded border border-dashed border-slate-200 bg-slate-50 text-[9px] text-slate-400">
        —
      </span>
    );
  }
  return (
    <a href={src} target="_blank" rel="noreferrer" className="shrink-0" title="Open preview">
      <img
        src={src}
        alt={alt}
        className="h-10 w-10 rounded border border-slate-200 object-cover bg-slate-100"
      />
    </a>
  );
}

function DroppedRow({ d, tone }: { d: DroppedItem; tone: "size" | "ocr" | "dup" }) {
  const toneCls =
    tone === "ocr" ? "border-violet-100 bg-violet-50/60"
    : tone === "dup" ? "border-sky-100 bg-sky-50/60"
    : "border-amber-100 bg-amber-50/50";
  const reasonCls =
    tone === "ocr" ? "text-violet-800"
    : tone === "dup" ? "text-sky-800"
    : "text-amber-800";
  return (
    <div className={cn(
      "flex items-start gap-1.5 rounded border px-2 py-1 text-[11px]",
      toneCls)}>
      <Thumb src={d.thumb} alt={d.name || "dropped"} />
      <span className="min-w-0 flex-1">
        <span className="font-medium text-slate-700">{d.name || "(unnamed)"}</span>
        {d.kept_key && (
          <span className="mt-0.5 block text-[10px] text-sky-700">
            same as [{d.kept_key}]{d.kept_name ? ` ${d.kept_name}` : ""}
          </span>
        )}
        {d.reason && (
          <span className={cn("mt-0.5 block text-[10px]", reasonCls)}>
            {d.reason}
          </span>
        )}
      </span>
      {typeof d.size === "number" && (
        <span className="shrink-0 text-[10px] tabular-nums text-slate-400">
          {d.size < 1024 ? `${d.size} B` : `${Math.round(d.size / 1024)} KB`}
        </span>
      )}
    </div>
  );
}

function ItemRow({ it }: { it: PassItem }) {
  const label = it.name || it.source;
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      <Thumb src={it.thumb} alt={label} />
      <span className={cn(
        "shrink-0 rounded border px-1 py-0.5 text-[9px] font-bold uppercase",
        KIND_TONE[it.kind] ?? KIND_TONE.other)}>
        {it.kind === "leave_certificate" ? "cert" : it.kind}
      </span>
      <span className="min-w-0 flex-1 truncate text-slate-700" title={label}>
        {label}
      </span>
      {it.employee && (
        <span className="shrink-0 font-medium text-slate-600" title={it.employee_id || undefined}>
          {it.employee}
        </span>
      )}
      {it.period && (
        <span className="shrink-0 text-[10px] text-slate-400">{it.period}</span>
      )}
      {it.signature && (
        <BadgeCheck className="h-3 w-3 shrink-0 text-emerald-500" aria-label="signed" />
      )}
    </div>
  );
}

/** Live two-pass view: dropped → pass1 → confirmed sheets → pass2 JSON. */
function TwoPassPanel({ events }: { events: ExtractionEvent[] }) {
  const unpackOk = events.find((e) => e.stage === "unpack" && e.status === "ok");
  const unpackSpin = events.find((e) => e.stage === "unpack" && e.status === "spin");
  const p1Start = events.find((e) => e.stage === "pass1" && e.status === "spin");
  const p1Done = events.find((e) => e.stage === "pass1" && e.status === "ok");
  const p2Start = events.find((e) => e.stage === "pass2" && e.status === "spin");
  const p2Done = events.find((e) => e.stage === "pass2" && e.status === "ok");

  if (!unpackSpin && !p1Start) return null;

  const dropped = ((unpackOk?.data?.dropped ?? []) as DroppedItem[]);
  const isOcrDrop = (d: DroppedItem) =>
    d.filter === "ocr" || /OCR/i.test(d.reason || "");
  const isDupDrop = (d: DroppedItem) =>
    d.filter === "dup" || /duplicate of/i.test(d.reason || "");
  const ocrDropped = dropped.filter(isOcrDrop);
  const dupDropped = dropped.filter((d) => isDupDrop(d) && !isOcrDrop(d));
  const sizeDropped = dropped.filter((d) => !isOcrDrop(d) && !isDupDrop(d));
  const kept = ((unpackOk?.data?.items ?? []) as KeptItem[]);
  const d1 = (p1Done?.data ?? {}) as {
    items?: PassItem[];
    confirmed?: PassItem[];
    noise?: string[];
    approval?: { detected?: boolean; evidence?: string; where?: string; detail?: string };
    summary?: ThreadSummary;
    thread_summary?: ThreadSummary;
  };
  const threadSummary = d1.thread_summary || d1.summary || null;
  const confirmed = d1.confirmed
    ?? (d1.items ?? []).filter((it) => it.kind === "timesheet" || it.kind === "leave_certificate");
  const s2 = (p2Start?.data ?? {}) as { sheets?: string[]; model?: string };
  const d2 = (p2Done?.data ?? {}) as {
    raw?: { sheets?: unknown[] };
    sheets?: unknown[];
    results?: {
      source: string; employee?: string | null; month?: number | null; year?: number | null;
      total_days?: number; leaves?: Record<string, number>; period_type?: string;
    }[];
  };

  return (
    <div className="space-y-2">
      {/* 0 — Collect / auto-drop */}
      <Section
        n={0}
        title="Collect thread"
        icon={Trash2}
        running={!!unpackSpin && !unpackOk}
        done={!!unpackOk}
      >
        {unpackOk && (
          <>
            <p className="text-[11px] text-slate-500">
              Kept {kept.length} item(s)
              {sizeDropped.length > 0 && <> · size-dropped {sizeDropped.length}</>}
              {ocrDropped.length > 0 && <> · OCR-removed {ocrDropped.length}</>}
              {dupDropped.length > 0 && <> · duplicate-dropped {dupDropped.length}</>}
            </p>
            {kept.some((k) => k.thumb) && (
              <div className="flex flex-wrap gap-1.5">
                {kept.map((k, i) => (
                  <div key={i} className="flex w-[4.5rem] flex-col items-center gap-0.5">
                    <Thumb src={k.thumb} alt={k.name || "item"} />
                    <span className="w-full truncate text-center text-[9px] text-slate-500" title={k.name}>
                      {k.name || k.key || "—"}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {dropped.length === 0 && (
              <p className="text-[10px] text-slate-400">Nothing auto-dropped.</p>
            )}
            {sizeDropped.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] font-bold uppercase tracking-wide text-amber-700">
                  Size filter (tiny images / logos)
                </p>
                {sizeDropped.map((d, i) => <DroppedRow key={`s-${i}`} d={d} tone="size" />)}
              </div>
            )}
            {ocrDropped.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] font-bold uppercase tracking-wide text-violet-700">
                  OCR removed (too little text — logo / icon)
                </p>
                {ocrDropped.map((d, i) => <DroppedRow key={`o-${i}`} d={d} tone="ocr" />)}
              </div>
            )}
            {dupDropped.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] font-bold uppercase tracking-wide text-sky-700">
                  Duplicate sheets (same parse / same sheet + approval)
                </p>
                {dupDropped.map((d, i) => <DroppedRow key={`d-${i}`} d={d} tone="dup" />)}
              </div>
            )}
          </>
        )}
      </Section>

      {/* 1 — Pass 1 triage */}
      <Section
        n={1}
        title="Pass 1 — classify"
        icon={FileSearch}
        running={!!p1Start && !p1Done}
        done={!!p1Done}
        idle={!p1Start}
      >
        {p1Start && !p1Done && (
          <p className="text-[11px] text-slate-500">
            {p1Start.message || "Reading the conversation…"}
          </p>
        )}
        {p1Done && (
          <>
            {(d1.items ?? []).length === 0 && (
              <p className="text-[11px] text-slate-500">No items classified.</p>
            )}
            {(d1.items ?? []).map((it, i) => <ItemRow key={i} it={it} />)}
            {(d1.noise?.length ?? 0) > 0 && (
              <p className="text-[10px] text-slate-400">
                Model noise: {d1.noise!.join(", ")}
              </p>
            )}
            {threadSummary && threadSummary.headline && (
              <ThreadSummaryBox
                summary={threadSummary}
                defaultOpen
                className="mb-0"
              />
            )}
          </>
        )}
      </Section>

      {/* 2 — Confirmed timesheets */}
      <Section
        n={2}
        title="Timesheets for Pass 2"
        icon={FileSpreadsheet}
        running={!!p1Done && !p2Start && !p2Done && confirmed.length > 0}
        done={!!p1Done}
        idle={!p1Done}
      >
        {p1Done && confirmed.length === 0 && (
          <p className="text-[11px] text-amber-700">
            No timesheet / leave certificate confirmed — Pass 2 skipped.
          </p>
        )}
        {confirmed.map((it, i) => <ItemRow key={i} it={it} />)}
        {p2Start && !p2Done && (
          <p className="text-[11px] text-slate-500">
            Sending {(s2.sheets ?? confirmed.map((c) => c.source)).length} sheet(s) to Pass 2…
          </p>
        )}
      </Section>

      {/* 3 — Pass 2 JSON */}
      <Section
        n={3}
        title="Pass 2 — extract JSON"
        icon={Braces}
        running={!!p2Start && !p2Done}
        done={!!p2Done}
        idle={!p2Start}
      >
        {p2Start && !p2Done && (
          <p className="text-[11px] text-slate-500">{p2Start.message}</p>
        )}
        {p2Done && (
          <>
            {(d2.results ?? []).map((r, i) => (
              <div key={i} className="rounded border border-slate-100 bg-white px-2 py-1.5">
                <div className="flex items-center gap-1.5 text-[11px]">
                  <span className="min-w-0 flex-1 truncate font-medium text-slate-800">
                    {r.employee || r.source}
                  </span>
                  {r.month && (
                    <span className="shrink-0 text-slate-500">{r.month}/{r.year}</span>
                  )}
                  {r.period_type && (
                    <span className="shrink-0 rounded bg-slate-100 px-1 text-[9px] font-semibold text-slate-500">
                      {r.period_type}
                    </span>
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
            {d2.raw != null && (
              <JsonBlock value={d2.raw} label="Model raw JSON (pass 2)" />
            )}
            {d2.sheets != null && (
              <JsonBlock value={d2.sheets} label="Normalised sheets JSON" />
            )}
            {d2.raw == null && d2.sheets == null && (
              <p className="text-[10px] text-slate-400">
                No JSON payload on this frame (older backend?).
              </p>
            )}
          </>
        )}
      </Section>
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
  const [expanded, setExpanded] = useState(true);
  const [showLog, setShowLog] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (showLog) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [run.events.length, showLog]);

  if (!run.open) return null;

  // Hide plan/agent frames — those are leftover orchestrator checklist noise.
  const detailEvents = run.events.filter(
    (e) => e.stage !== "start" && e.stage !== "plan" && e.stage !== "agent");
  const outcomes = run.events.filter((e) => e.stage === "autoaccept");
  const filed = outcomes.filter((e) => e.status === "ok").length;
  const held = outcomes.filter((e) => e.status !== "ok").length;
  const last = run.events[run.events.length - 1];

  return createPortal(
    <div className="fixed bottom-4 right-4 z-50 flex w-[min(28rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-pop">
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
        <div className="max-h-[70vh] space-y-2.5 overflow-y-auto p-3">
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

          <TwoPassPanel events={run.events} />

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
