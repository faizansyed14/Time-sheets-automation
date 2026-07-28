import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Columns2,
  Cpu,
  ExternalLink,
  FileText,
  Mail,
  RotateCcw,
  ScanLine,
  ShieldQuestion,
  Sparkles,
  Trash2,
  UploadCloud,
  XCircle,
} from "lucide-react";
import {
  deletePipelineFile,
  fetchPipeline,
  fetchPipelineStats,
  retryPipelineFile,
  MONTHS,
  type PipelineFile,
  type ThreadSummary,
} from "../api/client";
import PipelineCompareFixModal from "../components/PipelineCompareFixModal";
import StoredFilesPreview from "../components/StoredFilesPreview";
import { ThreadSummaryBox } from "../components/ThreadSummaryBox";
import { cn, formatBytes, formatDateTime } from "../lib/utils";
import { leaveBucketDefs } from "../lib/theme";
import { Button, Card, EmptyState, Select, Skeleton, Spinner } from "../components/ui";
import { FailureChip, PipelineStatusBadge, StageTimeline } from "../components/status";
import { useToast } from "../components/toast";
import { useDebounced, useSentinel } from "../lib/useInfinite";

type Filter = "" | "failed" | "needs_review" | "success" | "processing";
type OutcomeFilter = "" | "auto_accepted" | "held";

/** Engineering diagnostics (model, OCR, DPI, stage timeline) are off by
 *  default — a reviewer reading this page wants the extracted result, not the
 *  bookkeeping. Remembered per browser so the choice sticks. */
const TECH_KEY = "pipeline_show_tech";

function StatCard({
  label,
  value,
  icon,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  tone: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-3 rounded-xl border bg-white p-4 text-left shadow-card transition-all",
        active ? "border-brand-500 ring-2 ring-brand-100" : "border-slate-200 hover:border-slate-300"
      )}
    >
      <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", tone)}>
        {icon}
      </div>
      <div>
        <p className="text-xl font-bold leading-6 text-slate-900">{value}</p>
        <p className="text-xs font-medium text-slate-500">{label}</p>
      </div>
    </button>
  );
}

/** Per-file cost/provenance badge: which model handled the file, or whether a
 *  no-LLM path read it, plus an OCR chip. Technical — only shown inside the
 *  technical section. */
function ModelBadge({ file }: { file: PipelineFile }) {
  const method = file.extraction_method;
  if (!method) return null;

  let label: string;
  let tone: string;
  if (method === "vision-llm") {
    const model = file.extraction_model ?? "vision model";
    label = model;
    const cheap = /mini|nano|small|haiku|flash/i.test(model);
    tone = cheap
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : "border-amber-200 bg-amber-50 text-amber-700";
  } else if (method === "deterministic-text") {
    label = "No LLM · text";
    tone = "border-emerald-200 bg-emerald-50 text-emerald-700";
  } else if (method === "manual") {
    label = "Manual entry";
    tone = "border-slate-200 bg-slate-50 text-slate-500";
  } else if (method === "mock") {
    // Legacy rows only — the mock engine is gone, but old records keep the tag.
    label = "Mock engine";
    tone = "border-slate-200 bg-slate-50 text-slate-500";
  } else {
    label = method;
    tone = "border-slate-200 bg-slate-50 text-slate-500";
  }

  return (
    <span className="inline-flex items-center gap-1">
      <span
        title={`Extraction: ${label}${file.used_ocr ? " (OCR text layer used)" : ""}`}
        className={cn(
          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
          tone
        )}
      >
        <Cpu className="h-3 w-3" />
        {label}
      </span>
      {file.used_ocr && (
        <span
          title="Local OCR (Tesseract) produced the text layer for this scan/photo"
          className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2 py-0.5 text-[11px] font-semibold text-brand-700"
        >
          <ScanLine className="h-3 w-3" />
          OCR
        </span>
      )}
    </span>
  );
}

type StagedMeta = {
  matched_name?: string | null;
  matched_employee_id?: string | null;
  month?: number | null;
  year?: number | null;
  buckets?: Record<string, string[]>;
  flags?: string[];
  summary?: string;
};

type FeeSheet = {
  filename: string;
  kind: string;
  employee_name?: string | null;
  employee_id?: string | null;
  manager_signature?: boolean;
  leave_days?: number;
  incomplete_sheet?: boolean;
  missing_days?: number[];
};

type FeeMeta = {
  approval?: { detected?: boolean; detail?: string } | null;
  summary?: string;
  sheets?: FeeSheet[];
  /** Pass 1's plain-English read of the conversation — the SAME object the
   * Inbox thread view shows, carried here so a reviewer sees the "what's
   * going on in this email" without leaving the Pipeline page. */
  thread_summary?: ThreadSummary;
};

const KIND_LABELS: Record<string, string> = {
  timesheet: "Timesheet",
  leave_certificate: "Leave certificate",
  approval: "Approval",
  other: "Other",
};

const KIND_TONE: Record<string, string> = {
  timesheet: "bg-brand-50 text-brand-700",
  leave_certificate: "bg-amber-50 text-amber-700",
  approval: "bg-emerald-50 text-emerald-700",
  other: "bg-slate-100 text-slate-500",
};

/** The real, human-readable result of the run: who/period/leave days, per-sheet
 * evidence and the approval finding — the thing a reviewer actually needs. */
function ExtractionSummary({ staged, fee }: { staged: StagedMeta | null; fee: FeeMeta | null }) {
  if (!staged && !fee) return null;

  const buckets = staged?.buckets ?? {};
  const bucketChips = leaveBucketDefs()
    .map((b) => ({ ...b, count: (buckets[b.key] ?? []).length }))
    .filter((b) => b.count > 0);
  const sheets = fee?.sheets ?? [];

  return (
    <div className="rounded-lg border border-slate-200 bg-white px-5 py-4 text-sm">
      <p className="mb-2.5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-slate-400">
        <FileText className="h-3.5 w-3.5" /> What was extracted
      </p>

      <div className="mb-2.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-semibold text-slate-800">
          {staged?.matched_name || "Employee not matched"}
          {staged?.matched_employee_id ? ` (${staged.matched_employee_id})` : ""}
        </span>
        {staged?.month && staged?.year && (
          <span className="text-slate-500">{MONTHS[staged.month]} {staged.year}</span>
        )}
      </div>

      {bucketChips.length > 0 ? (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {bucketChips.map((b) => (
            <span
              key={b.key}
              className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ring-1", b.tone)}
            >
              {b.label} · {b.count}
            </span>
          ))}
        </div>
      ) : (
        <p className="mb-3 text-xs text-slate-400">No leave days were extracted from these sheets.</p>
      )}

      {fee?.approval?.detail && (
        <p className="mb-2.5 flex items-start gap-1.5 text-[13px] text-slate-600">
          {fee.approval.detected ? (
            <BadgeCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
          ) : (
            <ShieldQuestion className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
          )}
          {fee.approval.detail}
        </p>
      )}

      {fee?.thread_summary ? (
        <div className="mb-3">
          <ThreadSummaryBox summary={fee.thread_summary} />
        </div>
      ) : fee?.summary && (
        <p className="mb-3 rounded-lg bg-slate-50 px-3 py-2 text-[13px] italic text-slate-600">
          "{fee.summary}"
        </p>
      )}

      {sheets.length > 0 && (
        <ul className="space-y-1.5">
          {sheets.map((s, i) => (
            <li
              key={i}
              className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-slate-100 bg-white px-3 py-1.5 text-[13px]"
            >
              <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide", KIND_TONE[s.kind] ?? KIND_TONE.other)}>
                {KIND_LABELS[s.kind] ?? s.kind}
              </span>
              <span className="min-w-0 truncate font-medium text-slate-700">{s.filename}</span>
              {typeof s.leave_days === "number" && (
                <span className="text-slate-400">
                  {s.leave_days} leave day{s.leave_days === 1 ? "" : "s"}
                </span>
              )}
              {s.manager_signature && (
                <span className="inline-flex items-center gap-0.5 text-emerald-600" title="Manager signature detected on this sheet">
                  <BadgeCheck className="h-3 w-3" /> signed
                </span>
              )}
              {s.incomplete_sheet && (
                <span
                  className="text-amber-600"
                  title={(s.missing_days?.length ?? 0) > 0 ? `Missing days: ${s.missing_days!.join(", ")}` : "Incomplete coverage"}
                >
                  incomplete coverage
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {(staged?.flags?.length ?? 0) > 0 && (
        <ul className="mt-3 list-inside list-disc space-y-0.5 text-xs text-slate-500">
          {staged!.flags!.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
    </div>
  );
}

/** The AI's verdict in plain words — why it was filed or held. User-facing. */
function AiVerdict({ auto }: {
  auto: { accepted?: boolean; confidence?: string; reasons?: string[]; blockers?: string[] } | null;
}) {
  if (!auto) return null;
  return (
    <div className={cn(
      "rounded-lg border px-5 py-3 text-sm",
      auto.accepted ? "border-emerald-200 bg-emerald-50/60" : "border-amber-200 bg-amber-50/60",
    )}>
      <p className={cn("mb-1.5 flex items-center gap-1.5 font-semibold",
        auto.accepted ? "text-emerald-700" : "text-amber-700")}>
        <Sparkles className="h-4 w-4" />
        {auto.accepted
          ? "AI recommends accepting — review the leaves and accept to file"
          : "Held for human review — here's why"}
      </p>
      {auto.accepted && (auto.reasons?.length ?? 0) > 0 && (
        <ul className="list-inside list-disc space-y-0.5 text-xs text-emerald-800/90">
          {auto.reasons!.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      {!auto.accepted && (auto.blockers?.length ?? 0) > 0 && (
        <ul className="list-inside list-disc space-y-0.5 text-xs text-amber-800/90">
          {auto.blockers!.map((b, i) => <li key={i}>{b}</li>)}
        </ul>
      )}
    </div>
  );
}

/** Raw run bookkeeping — model, OCR, DPI, page counts, plus the stage
 *  timeline. Only rendered when the reviewer turns technical details on. */
function TechnicalPanel({ file }: { file: PipelineFile }) {
  const meta = (file.extraction_meta ?? {}) as Record<string, unknown>;

  const methodLabel: Record<string, string> = {
    "vision-llm": "Vision LLM",
    "deterministic-text": "Deterministic (no LLM)",
    manual: "Manual entry",
    mock: "Mock engine",
    unsupported: "Unsupported file",
  };
  const fmt = (v: unknown): string => {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "boolean") return v ? "Yes" : "No";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  };

  const known: [string, string, unknown][] = [
    ["Model", "model", file.extraction_model ?? meta["model"]],
    ["Method", "method", methodLabel[file.extraction_method ?? ""] ?? file.extraction_method ?? meta["method"]],
    ["OCR (Tesseract) used", "used_ocr", file.used_ocr],
    ["OCR provider", "ocr_provider", meta["ocr_provider"]],
    ["OCR status", "ocr_status", meta["ocr_status"]],
    ["Render DPI", "render_dpi", meta["render_dpi"]],
    ["Image detail", "image_detail", meta["image_detail"]],
    ["Pages rendered", "page_count", meta["page_count"]],
    ["Has text layer", "has_text_layer", meta["has_text_layer"]],
    ["Document text (chars)", "doc_text_chars", meta["doc_text_chars"]],
    ["File type", "file_type", meta["file_type"]],
    ["Source", "source_kind", meta["source_kind"]],
    ["Content type", "content_type", meta["content_type"]],
    ["File size", "size_bytes", formatBytes(file.size_bytes)],
    ["Embedded attachment", "embedded_attachment", meta["embedded_attachment"]],
    ["Embedded type", "embedded_type", meta["embedded_type"]],
  ];
  const shownKeys = new Set([...known.map(([, k]) => k), "auto_accept", "staged", "full_email_extract"]);
  const extras = Object.entries(meta).filter(([k]) => !shownKeys.has(k));
  const rows = known.filter(([, , v]) => v !== undefined && v !== null && v !== "");

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-2.5 text-xs font-bold uppercase tracking-wide text-slate-400">
        <Cpu className="h-3.5 w-3.5" />
        Technical details
        <span className="ml-1"><ModelBadge file={file} /></span>
      </div>

      <div className="px-5 py-3">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
          {rows.map(([label, key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-3 border-b border-slate-50 py-1">
              <dt className="text-slate-500">{label}</dt>
              <dd className="text-right font-medium text-slate-800">{fmt(value)}</dd>
            </div>
          ))}
          {extras.map(([key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-3 border-b border-slate-50 py-1">
              <dt className="text-slate-500">{key}</dt>
              <dd className="text-right font-medium text-slate-800">{fmt(value)}</dd>
            </div>
          ))}
          {rows.length === 0 && extras.length === 0 && (
            <p className="text-slate-400">No extraction metadata recorded for this file.</p>
          )}
        </dl>
      </div>

      <div className="border-t border-slate-100 px-5 py-4">
        <p className="mb-3 text-xs font-bold uppercase tracking-wide text-slate-400">
          Pipeline stages
        </p>
        <StageTimeline file={file} />
      </div>
    </div>
  );
}

export default function PipelinePage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();

  // Deep link from Inbox's "View in Pipeline" — every record staged from one
  // email thread, however many employee+month groups it produced.
  const [threadKeyFilter, setThreadKeyFilter] = useState<string>(
    () => sp.get("thread_key") || ""
  );
  const clearThreadKeyFilter = () => {
    setThreadKeyFilter("");
    const next = new URLSearchParams(sp);
    next.delete("thread_key");
    setSp(next, { replace: true });
  };

  const [statusFilter, setStatusFilter] = useState<Filter>(
    () => (sp.get("status") as Filter) || ""
  );
  const [outcomeFilter, setOutcomeFilter] = useState<OutcomeFilter>("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [assigning, setAssigning] = useState<PipelineFile | null>(null);

  const [showTech, setShowTech] = useState(() => localStorage.getItem(TECH_KEY) === "1");
  const toggleTech = () => {
    setShowTech((v) => {
      const next = !v;
      localStorage.setItem(TECH_KEY, next ? "1" : "0");
      return next;
    });
  };

  const { data: stats } = useQuery({
    queryKey: ["pipeline-stats"],
    queryFn: fetchPipelineStats,
    refetchInterval: 15_000,
  });

  const dq = useDebounced(q, 350);
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["pipeline", statusFilter, sourceFilter, dq, threadKeyFilter],
    queryFn: ({ pageParam }) =>
      fetchPipeline({
        status: statusFilter || undefined,
        source_kind: sourceFilter || undefined,
        thread_key: threadKeyFilter || undefined,
        q: dq || undefined,
        offset: pageParam as number,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
    refetchInterval: 15_000,
  });

  const items = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;

  const outcomeCounts = {
    // "AI recommends" spans both awaiting-review and already-filed records
    // (see the chip's own title text) — "Held" is narrower: needs_review
    // rows the AI did NOT recommend, i.e. genuinely need a human's judgment,
    // not just their click.
    auto_accepted: items.filter((f) => f.auto_accepted).length,
    held: items.filter((f) => f.status === "needs_review" && !f.auto_accepted).length,
  };

  const visibleItems = items.filter((f) => {
    if (outcomeFilter === "auto_accepted") return f.auto_accepted;
    if (outcomeFilter === "held") return f.status === "needs_review" && !f.auto_accepted;
    return true;
  });

  // Arrived via "View in Pipeline" from a specific email thread — jump
  // straight to the record instead of making them find it in the list.
  useEffect(() => {
    if (threadKeyFilter && visibleItems.length === 1) {
      setOpen(visibleItems[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadKeyFilter, visibleItems.length]);

  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage
  );

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
    qc.invalidateQueries({ queryKey: ["coverage"] });
    qc.invalidateQueries({ queryKey: ["record"] });
  };

  const onAssignSaved = () => {
    setAssigning(null);
    invalidate();
    qc.invalidateQueries({ queryKey: ["files"] });
  };

  const retryMut = useMutation({
    mutationFn: retryPipelineFile,
    onSuccess: (t) => {
      if (t.status === "success")
        toast("success", "Retry succeeded", `${t.filename} processed cleanly.`);
      else if (t.status === "needs_review")
        toast("warning", "Retry finished with flags", t.failure_detail ?? "");
      else toast("error", "Retry failed again", t.failure_detail ?? "");
      invalidate();
    },
    onError: (e: any) => toast("error", "Retry failed", e?.response?.data?.detail ?? String(e)),
  });

  const deleteMut = useMutation({
    mutationFn: deletePipelineFile,
    onSuccess: () => {
      toast("info", "Tracker entry removed");
      invalidate();
      qc.invalidateQueries({ queryKey: ["inbox"] });
    },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  return (
    <div className="animate-fade-up">
      {threadKeyFilter && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm text-brand-800">
          <Columns2 className="h-4 w-4 shrink-0" />
          <span className="flex-1">Showing only records from that email thread.</span>
          <button
            type="button"
            onClick={clearThreadKeyFilter}
            className="font-semibold text-brand-700 hover:text-brand-900"
          >
            Show all records
          </button>
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard
          label="All files"
          value={stats?.total ?? 0}
          icon={<Activity className="h-5 w-5 text-slate-600" />}
          tone="bg-slate-100"
          active={statusFilter === ""}
          onClick={() => setStatusFilter("")}
        />
        <StatCard
          label="Success"
          value={stats?.success ?? 0}
          icon={<CheckCircle2 className="h-5 w-5 text-emerald-600" />}
          tone="bg-emerald-50"
          active={statusFilter === "success"}
          onClick={() => setStatusFilter(statusFilter === "success" ? "" : "success")}
        />
        <StatCard
          label="Needs review"
          value={stats?.needs_review ?? 0}
          icon={<AlertTriangle className="h-5 w-5 text-amber-600" />}
          tone="bg-amber-50"
          active={statusFilter === "needs_review"}
          onClick={() => setStatusFilter(statusFilter === "needs_review" ? "" : "needs_review")}
        />
        <StatCard
          label="Failed"
          value={stats?.failed ?? 0}
          icon={<XCircle className="h-5 w-5 text-rose-600" />}
          tone="bg-rose-50"
          active={statusFilter === "failed"}
          onClick={() => setStatusFilter(statusFilter === "failed" ? "" : "failed")}
        />
        <StatCard
          label="AI recommendations"
          value={outcomeCounts.auto_accepted}
          icon={<Sparkles className="h-5 w-5 text-emerald-600" />}
          tone="bg-emerald-50"
          active={outcomeFilter === "auto_accepted"}
          onClick={() => setOutcomeFilter(outcomeFilter === "auto_accepted" ? "" : "auto_accepted")}
        />
        <StatCard
          label="Held"
          value={outcomeCounts.held}
          icon={<AlertTriangle className="h-5 w-5 text-amber-600" />}
          tone="bg-amber-50"
          active={outcomeFilter === "held"}
          onClick={() => setOutcomeFilter(outcomeFilter === "held" ? "" : "held")}
        />
      </div>

      <Card>
        {/* Toolbar — search, source, and the "hide the noise" technical-details toggle. */}
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3.5">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search file or employee…"
            className="w-64 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none"
          />

          <Select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} className="py-1.5 text-xs">
            <option value="">All sources</option>
            <option value="email">Email</option>
            <option value="upload">Upload</option>
          </Select>

          <button
            type="button"
            onClick={toggleTech}
            title="Show model, OCR and pipeline-stage diagnostics inside each expanded row"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors",
              showTech
                ? "border-brand-200 bg-brand-50 text-brand-700"
                : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50"
            )}
          >
            <Cpu className="h-3.5 w-3.5" />
            Technical details
            <span className={cn(
              "rounded-full px-1.5 text-[10px] font-bold",
              showTech ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-500"
            )}>
              {showTech ? "ON" : "OFF"}
            </span>
          </button>

          <p className="ml-auto text-xs text-slate-400">
            {visibleItems.length} of {total} file{total !== 1 && "s"}
            {outcomeFilter ? " (filtered)" : ""}
          </p>
        </div>

        {isLoading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : visibleItems.length === 0 ? (
          <EmptyState
            icon={<Activity className="h-6 w-6" />}
            title="Nothing here"
            detail={outcomeFilter
              ? "No files match this AI outcome filter in the loaded page — try clearing the filter or load more."
              : "Files appear the moment they enter the pipeline — from an accepted email or an upload."}
          />
        ) : (
          <div className="divide-y divide-slate-100">
            {visibleItems.map((f) => {
              const expanded = open === f.id;
              const meta = (f.extraction_meta ?? {}) as Record<string, unknown>;
              const staged = (meta["staged"] ?? null) as StagedMeta | null;
              const fee = (meta["full_email_extract"] ?? null) as FeeMeta | null;
              const auto = (meta["auto_accept"] ?? null) as
                | { accepted?: boolean; confidence?: string; reasons?: string[]; blockers?: string[] }
                | null;

              return (
                <div key={f.id}>
                  <button
                    onClick={() => setOpen(expanded ? null : f.id)}
                    className="flex w-full items-center gap-3 px-5 py-3 text-left transition-colors hover:bg-slate-50"
                  >
                    {expanded ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
                    )}
                    {f.source_kind === "email" ? (
                      <Mail className="h-4 w-4 shrink-0 text-slate-400" />
                    ) : (
                      <UploadCloud className="h-4 w-4 shrink-0 text-slate-400" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-slate-800">
                        {f.filename}
                      </span>
                      {/* Employee and period are what identify a row; byte size
                          is bookkeeping and now lives in Technical details. */}
                      <span className="block truncate text-xs text-slate-400">
                        {f.employee_name ?? "Unidentified"}
                        {f.month ? ` · ${MONTHS[f.month]} ${f.year}` : ""} ·{" "}
                        {formatDateTime(f.created_at)}
                      </span>
                    </span>

                    {/* One outcome badge per row — status, AI verdict and failure
                        reason used to render as three separate, overlapping
                        chips saying the same thing. */}
                    {f.status === "needs_review" ? (
                      f.auto_accepted ? (
                        <span
                          title="AI recommends accepting — review and press Accept to file"
                          className="hidden shrink-0 items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-emerald-700 sm:inline-flex"
                        >
                          <Sparkles className="h-3 w-3" /> AI recommends
                        </span>
                      ) : (
                        <span
                          title="AI held this for human review — open Review"
                          className="hidden shrink-0 items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700 sm:inline-flex"
                        >
                          <AlertTriangle className="h-3 w-3" /> Held
                        </span>
                      )
                    ) : f.status === "failed" ? (
                      <span className="hidden md:block">
                        <FailureChip code={f.failure_code} label={f.failure_label} />
                      </span>
                    ) : (
                      <PipelineStatusBadge status={f.status} />
                    )}

                    {f.status === "success" && f.record_id && (
                      <button
                        type="button"
                        title="View the filed record"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/records/${f.record_id}`);
                        }}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-100"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">View record</span>
                      </button>
                    )}
                    {f.can_resolve_assign && (
                      <button
                        type="button"
                        title={f.status === "success" ? "Review and correct this filing" : "Open Review"}
                        onClick={(e) => {
                          e.stopPropagation();
                          setAssigning(f);
                        }}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-brand-200 bg-brand-50 px-2 py-1 text-[11px] font-semibold text-brand-700 hover:bg-brand-100"
                      >
                        <Columns2 className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Review</span>
                      </button>
                    )}
                    {(f.status === "needs_review" || f.status === "failed") && (
                      <button
                        type="button"
                        title="Delete"
                        disabled={deleteMut.isPending}
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteMut.mutate(f.id);
                        }}
                        className="shrink-0 rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </button>

                  {expanded && (
                    /* Expanded detail, ordered by what a reviewer needs first:
                       the AI's verdict, then the extracted result, then any
                       problem, and only then (opt-in) the diagnostics. */
                    <div className="space-y-4 border-t border-slate-100 bg-slate-50/60 px-6 py-5">
                      <AiVerdict auto={auto} />

                      <ExtractionSummary staged={staged} fee={fee} />

                      {f.failure_detail && (
                        <div
                          className={cn(
                            "rounded-lg border p-3 text-sm",
                            f.status === "failed"
                              ? "border-rose-200 bg-rose-50 text-rose-700"
                              : "border-amber-200 bg-amber-50 text-amber-800"
                          )}
                        >
                          <p className="font-semibold">{f.failure_label ?? "Issue"}</p>
                          <p className="mt-0.5 leading-5">{f.failure_detail}</p>
                        </div>
                      )}

                      {f.resolution_note && (
                        <div className="rounded-lg border border-brand-200 bg-brand-50 p-3 text-sm text-brand-800">
                          <p className="font-semibold">Last update</p>
                          <p className="mt-0.5 leading-5">{f.resolution_note}</p>
                        </div>
                      )}

                      {f.record_id && (f.status === "success" || f.status === "needs_review") && (
                        <StoredFilesPreview recordId={f.record_id} />
                      )}

                      {showTech && <TechnicalPanel file={f} />}

                      <div className="flex flex-wrap items-center gap-2 pt-1">
                        {f.can_resolve_assign && (
                          <Button size="sm" onClick={() => setAssigning(f)}>
                            <Columns2 className="h-4 w-4" /> Review
                          </Button>
                        )}
                        {f.can_retry && f.status !== "processing" && (
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={retryMut.isPending}
                            onClick={() => retryMut.mutate(f.id)}
                          >
                            <RotateCcw className={cn("h-4 w-4", retryMut.isPending && "animate-spin")} />
                            Retry extraction
                          </Button>
                        )}
                        {f.record_id && (
                          <Link to={`/records/${f.record_id}`}>
                            <Button size="sm" variant="secondary">
                              <ExternalLink className="h-4 w-4" /> View record
                            </Button>
                          </Link>
                        )}
                        {(f.status === "needs_review" || f.status === "failed") && (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="ml-auto text-rose-500 hover:bg-rose-50"
                            disabled={deleteMut.isPending}
                            onClick={() => deleteMut.mutate(f.id)}
                          >
                            <Trash2 className="h-4 w-4" /> Delete
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}

            <div ref={sentinelRef} />
            {isFetchingNextPage && (
              <div className="flex items-center justify-center gap-2 py-4 text-xs text-slate-400">
                <Spinner className="h-4 w-4" /> Loading more…
              </div>
            )}
          </div>
        )}
      </Card>

      <PipelineCompareFixModal
        file={assigning}
        onClose={() => setAssigning(null)}
        onSaved={onAssignSaved}
        onDiscarded={invalidate}
      />
    </div>
  );
}
