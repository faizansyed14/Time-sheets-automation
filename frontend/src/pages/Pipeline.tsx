import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BadgeCheck,
  ChevronRight,
  Columns2,
  Cpu,
  MoreHorizontal,
  Search,
  FileText,
  FolderOpen,
  Mail,
  RefreshCw,
  ScanLine,
  ShieldQuestion,
  Sparkles,
  UploadCloud,
  UserCheck,
} from "lucide-react";
import {
  deletePipelineFile,
  extractVaultFile,
  fetchPipeline,
  fetchPipelineStats,
  rematchUnmatchedPipelineFiles,
  MONTHS,
  type PipelineFile,
  type RematchResult,
  type ThreadSummary,
} from "../api/client";
import BulkAccept from "../components/BulkAccept";
import { useAuth } from "../lib/auth";
import PortalSubmissionsTable from "../components/PortalSubmissionsTable";
import PipelineCompareFixModal from "../components/PipelineCompareFixModal";
import StoredFilesPreview from "../components/StoredFilesPreview";
import { ThreadSummaryBox } from "../components/ThreadSummaryBox";
import { cn, formatBytes, formatDateTime } from "../lib/utils";
import { leaveBucketDefs } from "../lib/theme";
import { Badge, Button, Card, EmptyState, Select } from "../components/ui";
import { StageTimeline } from "../components/status";
import { useToast } from "../components/toast";
import { useDebounced, useSentinel } from "../lib/useInfinite";

import { ListSkeleton, MoreRowsSkeleton } from "../components/skeletons";
type Filter = "" | "failed" | "needs_review" | "ai_recommended" | "success" | "processing" | "unmatched";

/** Engineering diagnostics (model, OCR, DPI, stage timeline) are off by
 *  default — a reviewer reading this page wants the extracted result, not the
 *  bookkeeping. Remembered per browser so the choice sticks. */
const TECH_KEY = "pipeline_show_tech";

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
  approval_evidence?: string;
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
                <span className="inline-flex items-center gap-0.5 text-emerald-600" title={s.approval_evidence || "Manager signature detected on this sheet"}>
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
          {staged!.flags!.map((f, i) =>
            /^EXTRACTION INCOMPLETE/i.test(f) ? (
              // A run that could not read part of the thread: not a normal
              // note, so it must not look like one. It is retried automatically.
              <li key={i} className="font-semibold text-amber-700" title="Part of this email could not be read. It is retried automatically; check the items before accepting.">
                {f}
              </li>
            ) : (
              <li key={i}>{f}</li>
            ))}
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
          ? "AI recommends accepting, review the leaves and accept to file"
          : "Held for human review, here's why"}
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
    if (v === null || v === undefined || v === "") return "-";
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

/** Stored in the File Vault without being extracted (backend: vault_pending.py). */
const isVaultPending = (f: PipelineFile) => f.failure_code === "vault_not_extracted";

export default function PipelinePage() {
  const qc = useQueryClient();
  const { canWrite } = useAuth();
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

  // Deep link from the Dashboard's "View in Pipeline" (Awaiting review for a
  // specific month/year) — narrows the list to that one period.
  const [periodFilter, setPeriodFilter] = useState<{ month: number; year: number } | null>(() => {
    const m = Number(sp.get("month"));
    const y = Number(sp.get("year"));
    return m >= 1 && m <= 12 && y >= 2000 ? { month: m, year: y } : null;
  });
  const clearPeriodFilter = () => {
    setPeriodFilter(null);
    const next = new URLSearchParams(sp);
    next.delete("month");
    next.delete("year");
    setSp(next, { replace: true });
  };

  const [statusFilter, setStatusFilter] = useState<Filter>(
    () => (sp.get("unmatched") === "1" ? "unmatched" : (sp.get("status") as Filter) || "")
  );
  const [sourceFilter, setSourceFilter] = useState("");
  // "Pipeline" is the existing per-file table (unchanged); "Portal
  // Submissions" is a submission-centric view filterable by the employee's
  // manager's decision — a dimension PipelineFile rows don't carry.
  const [tab, setTab] = useState<"pipeline" | "portal">("pipeline");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [assigning, setAssigning] = useState<PipelineFile | null>(null);

  const [showTech, setShowTech] = useState(() => localStorage.getItem(TECH_KEY) === "1");
  const toggleTech = () => {
    setShowTech((v) => {
      const next = !v;
      localStorage.setItem(TECH_KEY, next ? "1" : "0");
      return next;
    });
  };

  // How far back the "Success" stat card counts — user-selectable (60/90/120
  // days) rather than a single fixed window.
  const [successWindowDays, setSuccessWindowDays] = useState(60);

  const { data: stats } = useQuery({
    queryKey: ["pipeline-stats", successWindowDays],
    queryFn: () => fetchPipelineStats({ success_window_days: successWindowDays }),
    refetchInterval: 15_000,
  });

  const dq = useDebounced(q, 350);
  // The "Success" stat card shows a windowed count (see fetchPipelineStats'
  // success_recent) — its drill-down must list exactly those rows, not
  // every success ever, or the number and the list below it would disagree.
  // Memoized on [statusFilter, successWindowDays] (NOT recomputed from
  // Date.now() on every render) — computing it inline made it a new value,
  // and therefore a new queryKey, on every single render, which made React
  // Query treat every render as "a new query" and refetch in an infinite
  // loop the moment the Success filter was active. This is the confirmed
  // root cause of that; it only recomputes now when the filter toggles on/
  // off or the window is actually changed.
  const successSince = useMemo(
    () => (statusFilter === "success"
      ? new Date(Date.now() - successWindowDays * 24 * 60 * 60 * 1000).toISOString()
      : undefined),
    [statusFilter, successWindowDays],
  );
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["pipeline", statusFilter, sourceFilter, dq, threadKeyFilter, periodFilter, successSince],
    queryFn: ({ pageParam }) =>
      fetchPipeline({
        status: statusFilter === "ai_recommended" ? "needs_review"
          : statusFilter && statusFilter !== "unmatched" ? statusFilter : undefined,
        unmatched: statusFilter === "unmatched" || undefined,
        auto_accepted: statusFilter === "ai_recommended" ? true : undefined,
        // Succeeded and resolved files are finished work — they'd bury the
        // rows that actually need someone. Hidden unless their own card is
        // picked. ("Resolved" also covers backfilled backlog threads that
        // were deliberately marked done without ever being extracted.)
        exclude_status: statusFilter && statusFilter !== "unmatched" && statusFilter !== "ai_recommended"
          ? undefined : "success,resolved",
        updated_after: successSince,
        source_kind: sourceFilter || undefined,
        thread_key: threadKeyFilter || undefined,
        month: periodFilter?.month,
        year: periodFilter?.year,
        q: dq || undefined,
        offset: pageParam as number,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
    refetchInterval: 15_000,
  });

  const items = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;

  const visibleItems = items;

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

  // A file stored in the File Vault without being read: Extract reads it now.
  const extractMut = useMutation({
    mutationFn: (relPath: string) => extractVaultFile(relPath),
    onSuccess: (r) => {
      if (r.staged) toast("success", "Extracted", `${r.staged} item(s) are ready to review.`);
      else toast("warning", "Nothing extracted", r.message || "No timesheet or leave record was found in this file.");
      invalidate();
    },
    onError: (e: any) => toast("error", "Extraction failed", e?.response?.data?.detail ?? String(e)),
  });

  const deleteMut = useMutation({
    mutationFn: deletePipelineFile,
    onSuccess: () => {
      toast("info", "Tracker entry removed");
      invalidate();
      qc.invalidateQueries({ queryKey: ["inbox-threads"] });
    },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  // Re-checks employee identity for every still-unreviewed item whose
  // employee wasn't found at staging time — catches a roster/email staged
  // BEFORE that employee existed in the Employee Matcher. No LLM call, no
  // re-extraction, so safe to run over the whole backlog at once.
  const rematchMut = useMutation({
    mutationFn: rematchUnmatchedPipelineFiles,
    onSuccess: (r: RematchResult) => {
      if (r.rematched_count > 0) {
        toast("success", `${r.rematched_count} employee(s) now matched`,
          r.still_unmatched_count > 0
            ? `${r.still_unmatched_count} still have nobody to match, add them to the Employee Matcher, or assign manually in Compare & Fix.`
            : "Everything that was waiting on this is now resolved.");
      } else if (r.still_unmatched_count > 0) {
        toast("info", "Nothing new to match",
          `${r.still_unmatched_count} item(s) still have nobody in the Employee Matcher to match against.`);
      } else {
        toast("info", "Nothing to check", "No pipeline items are currently waiting on an employee match.");
      }
      invalidate();
    },
    onError: (e: any) => toast("error", "Could not re-check matches", e?.response?.data?.detail ?? String(e)),
  });

  // One plain-language line under each row: why it is where it is.
  const reasonFor = (f: PipelineFile): { text: string; tone: "ok" | "warn" | "bad" | "muted" } => {
    const meta = (f.extraction_meta ?? {}) as Record<string, any>;
    const blockers: string[] = meta?.auto_accept?.blockers ?? [];
    if (isVaultPending(f)) return { text: "In vault, not extracted", tone: "warn" };
    if (f.status === "failed") return { text: f.failure_label ?? "Could not be processed", tone: "bad" };
    if (f.status === "processing") return { text: "Being read now", tone: "muted" };
    if (f.status === "success") return { text: "Filed to the record", tone: "ok" };
    if (f.status === "resolved") return { text: "Closed", tone: "muted" };
    if (!meta?.staged?.employee_pk) return { text: "Not matched to an employee", tone: "bad" };
    if (f.auto_accepted) return { text: "Ready to accept", tone: "ok" };
    const b = (blockers[0] ?? "").replace(/^validation flags:\s*/i, "");
    if (/no manager approval/i.test(b)) return { text: "No manager approval found", tone: "warn" };
    return { text: b ? b.charAt(0).toUpperCase() + b.slice(1) : "Needs a check", tone: "warn" };
  };

  const statusChip = (f: PipelineFile) => {
    if (isVaultPending(f)) return <Badge tone="brand">In vault</Badge>;
    if (f.status === "needs_review")
      return f.auto_accepted
        ? <Badge tone="success">Ready</Badge>
        : <Badge tone="warning">Needs review</Badge>;
    if (f.status === "failed") return <Badge tone="danger">Failed</Badge>;
    if (f.status === "success") return <Badge tone="success">Filed</Badge>;
    if (f.status === "processing") return <Badge tone="brand">Reading</Badge>;
    return <Badge>Closed</Badge>;
  };

  const FILTERS: { key: Filter; label: string; count?: number }[] = [
    { key: "", label: "Open", count: Math.max(0, (stats?.total ?? 0) - (stats?.success ?? 0) - (stats?.resolved ?? 0)) },
    { key: "needs_review", label: "Needs review", count: stats?.needs_review },
    { key: "ai_recommended", label: "AI recommended", count: stats?.ai_recommended },
    { key: "failed", label: "Failed", count: stats?.failed },
    { key: "unmatched", label: "Not matched", count: stats?.unmatched },
    { key: "success", label: "Filed", count: stats?.success_recent },
  ];

  return (
    <div className="animate-fade-up">
      {/* View switch + the two page-wide actions */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-lg bg-slate-200/60 p-0.5 text-sm">
          {(["pipeline", "portal"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={cn(
                "rounded-md px-3.5 py-1.5 font-semibold transition-all",
                tab === t ? "bg-white text-slate-900 shadow-xs" : "text-slate-500 hover:text-slate-800"
              )}
            >
              {t === "pipeline" ? "Activity" : "Portal submissions"}
            </button>
          ))}
        </div>
        {tab === "pipeline" && (
          <div className="flex flex-wrap items-center gap-2">
            {canWrite && <BulkAccept period={periodFilter} onDone={invalidate} />}
            <Button
              variant="secondary"
              size="sm"
              disabled={rematchMut.isPending}
              onClick={() => rematchMut.mutate()}
              title="Look again for the employee of items that were not matched (for example after adding someone to the Employee Matcher). No AI call."
            >
              <RefreshCw className={cn("h-3.5 w-3.5", rematchMut.isPending && "animate-spin")} />
              Re-check matches
            </Button>
          </div>
        )}
      </div>

      {tab === "portal" ? (
        <Card>
          <PortalSubmissionsTable onOpenPipelineFile={setAssigning} />
        </Card>
      ) : (
        <>
          {(threadKeyFilter || periodFilter) && (
            <div className="mb-3 flex items-center gap-2 rounded-lg border border-brand-200/70 bg-brand-50 px-3.5 py-2 text-sm text-brand-800">
              <Columns2 className="h-4 w-4 shrink-0" />
              <span className="flex-1">
                {threadKeyFilter
                  ? "Showing the records from one email thread."
                  : `Showing ${MONTHS[periodFilter!.month]} ${periodFilter!.year} only.`}
              </span>
              <button
                type="button"
                onClick={threadKeyFilter ? clearThreadKeyFilter : clearPeriodFilter}
                className="font-semibold text-brand-700 hover:text-brand-900"
              >
                Show all
              </button>
            </div>
          )}

          <Card className="overflow-hidden">
            {/* Filters: status tabs, then search / source / AI chips */}
            <div className="border-b border-slate-100 px-4 pt-3">
              <div className="-mb-px flex gap-1 overflow-x-auto">
                {FILTERS.map((x) => {
                  const active = statusFilter === x.key;
                  return (
                    <button
                      key={x.key || "open"}
                      type="button"
                      onClick={() => setStatusFilter(x.key)}
                      className={cn(
                        "flex shrink-0 items-center gap-2 border-b-2 px-3 pb-2.5 pt-1 text-sm font-semibold transition-colors",
                        active ? "border-brand-700 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"
                      )}
                    >
                      {x.label}
                      <span className={cn(
                        "rounded-full px-1.5 text-[11px] tabular-nums",
                        active ? "bg-brand-700 text-white" : x.key === "unmatched" && (x.count ?? 0) > 0
                          ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-600"
                      )}>
                        {x.count ?? 0}
                      </span>
                    </button>
                  );
                })}
                {statusFilter === "success" && (
                  <select
                    value={successWindowDays}
                    onChange={(e) => setSuccessWindowDays(Number(e.target.value))}
                    title="How far back Filed counts"
                    className="mb-2 ml-1 self-center rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-semibold text-slate-600"
                  >
                    <option value={60}>last 60 days</option>
                    <option value={90}>last 90 days</option>
                    <option value={120}>last 120 days</option>
                  </select>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-2.5">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search employee or file"
                  className="h-8 w-60 rounded-lg border border-slate-300/80 bg-white pl-8 pr-3 text-sm placeholder:text-slate-400 focus:border-brand-600 focus:outline-none focus:ring-[3px] focus:ring-brand-600/15"
                />
              </div>
              <Select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} className="h-8 py-0 text-xs">
                <option value="">All sources</option>
                <option value="email">Email</option>
                <option value="upload">Upload</option>
                <option value="portal">Portal</option>
              </Select>
              <p className="ml-auto text-xs tabular-nums text-slate-500">
                {visibleItems.length} of {total}
              </p>
            </div>

            {isLoading ? (
              <ListSkeleton label="Loading activity" columns={["Employee", "Period", "Status", "Received"]} widths={["w-56", "w-20", "w-24", "w-24"]} />
            ) : visibleItems.length === 0 ? (
              <EmptyState
                icon={<Activity className="h-6 w-6" />}
                title={statusFilter === "unmatched" ? "Every item is matched to an employee" : "Nothing here"}
                detail={statusFilter === "unmatched"
                  ? "Sheets that cannot be matched to someone in the Employee Matcher would appear here."
                  : "Items appear as soon as a sheet arrives by email, upload or the employee portal."}
              />
            ) : (
              <div>
                <div className="hidden grid-cols-[minmax(0,1fr)_110px_minmax(0,220px)_120px_150px] gap-4 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400 md:grid">
                  <span>Employee</span><span>Period</span><span>Status</span><span>Received</span><span />
                </div>
                <ul className="divide-y divide-slate-100">
                  {visibleItems.map((f) => {
                    const expanded = open === f.id;
                    const meta = (f.extraction_meta ?? {}) as Record<string, unknown>;
                    const staged = (meta["staged"] ?? null) as StagedMeta | null;
                    const fee = (meta["full_email_extract"] ?? null) as FeeMeta | null;
                    const auto = (meta["auto_accept"] ?? null) as
                      | { accepted?: boolean; confidence?: string; reasons?: string[]; blockers?: string[] }
                      | null;
                    const reason = reasonFor(f);
                    const SourceIcon = f.source_kind === "email" ? Mail : f.source_kind === "portal" ? UserCheck
                      : f.source_kind === "vault" ? FolderOpen : UploadCloud;
                    return (
                      <li key={f.id} className={cn(expanded && "bg-slate-50/50")}>
                        <div
                          role="button"
                          tabIndex={0}
                          onClick={() => setOpen(expanded ? null : f.id)}
                          onKeyDown={(e) => { if (e.key === "Enter") setOpen(expanded ? null : f.id); }}
                          className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-4 py-3 transition-colors hover:bg-slate-50 md:grid-cols-[minmax(0,1fr)_110px_minmax(0,220px)_120px_150px]"
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <ChevronRight className={cn("h-4 w-4 shrink-0 text-slate-300 transition-transform", expanded && "rotate-90 text-slate-500")} />
                            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500" title={f.source_kind}>
                              <SourceIcon className="h-4 w-4" />
                            </span>
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-semibold text-slate-900">
                                {f.employee_name ?? "Not identified"}
                              </span>
                              <span className="block truncate text-xs text-slate-500" title={f.filename}>{f.filename}</span>
                            </span>
                          </div>
                          <span className="hidden text-sm tabular-nums text-slate-700 md:block">
                            {f.month ? `${MONTHS[f.month]} ${f.year}` : "-"}
                          </span>
                          <span className="hidden min-w-0 md:block">
                            {statusChip(f)}
                            <span className={cn(
                              "mt-1 block truncate text-xs",
                              reason.tone === "bad" ? "text-rose-700" : reason.tone === "warn" ? "text-amber-700"
                                : reason.tone === "ok" ? "text-emerald-700" : "text-slate-500"
                            )} title={reason.text}>
                              {reason.text}
                            </span>
                          </span>
                          <span className="hidden text-xs tabular-nums text-slate-500 md:block">{formatDateTime(f.created_at)}</span>
                          <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                            <span className="md:hidden">{statusChip(f)}</span>
                            {isVaultPending(f) && canWrite && (
                              <Button
                                size="sm"
                                disabled={extractMut.isPending}
                                onClick={() => {
                                  if (confirm(`Extract "${f.filename}"? It is read and the result is sent to review.`))
                                    extractMut.mutate((f.source_id ?? "").replace(/^vault:/, ""));
                                }}
                              >
                                {extractMut.isPending && extractMut.variables === (f.source_id ?? "").replace(/^vault:/, "") ? "Extracting…" : "Extract"}
                              </Button>
                            )}
                            {f.can_resolve_assign && (
                              <Button size="sm" variant={f.status === "success" ? "secondary" : "primary"} onClick={() => setAssigning(f)}>
                                {f.status === "success" ? "Correct" : "Review"}
                              </Button>
                            )}
                            <RowMenu
                              open={menuFor === f.id}
                              onToggle={() => setMenuFor(menuFor === f.id ? null : f.id)}
                              onClose={() => setMenuFor(null)}
                              items={[
                                ...(f.record_id ? [{ label: "View record", onClick: () => navigate(`/records/${f.record_id}`) }] : []),
                                ...((f.status === "needs_review" || f.status === "failed") ? [{
                                  label: "Delete item",
                                  danger: true,
                                  onClick: () => {
                                    if (confirm(`Delete "${f.filename}" from the activity list? The email itself is not touched.`))
                                      deleteMut.mutate(f.id);
                                  },
                                }] : []),
                              ]}
                            />
                          </div>
                        </div>

                        {expanded && (
                          <div className="space-y-4 border-t border-slate-100 px-4 py-5 sm:px-14">
                            <AiVerdict auto={auto} />
                            <ExtractionSummary staged={staged} fee={fee} />
                            {f.failure_detail && f.status === "failed" && (
                              <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
                                <p className="font-semibold">{f.failure_label ?? "Issue"}</p>
                                <p className="mt-0.5 whitespace-pre-wrap leading-5">{f.failure_detail}</p>
                              </div>
                            )}
                            {f.resolution_note && (
                              <div className="rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-700">
                                <p className="font-semibold text-slate-900">Last update</p>
                                <p className="mt-0.5 leading-5">{f.resolution_note}</p>
                              </div>
                            )}
                            {f.record_id && (f.status === "success" || f.status === "needs_review") && (
                              <StoredFilesPreview recordId={f.record_id} />
                            )}
                            <button
                              type="button"
                              onClick={toggleTech}
                              className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-800"
                            >
                              <Cpu className="h-3.5 w-3.5" />
                              {showTech ? "Hide technical details" : "Show technical details"}
                            </button>
                            {showTech && <TechnicalPanel file={f} />}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
                <div ref={sentinelRef} />
                {isFetchingNextPage && <MoreRowsSkeleton />}
              </div>
            )}
          </Card>
        </>
      )}

      <PipelineCompareFixModal
        file={assigning}
        onClose={() => setAssigning(null)}
        onSaved={onAssignSaved}
        onDiscarded={invalidate}
      />
    </div>
  );
}

/** A small "more actions" menu: keeps rare actions out of the row. */
function RowMenu({ open, onToggle, onClose, items }: {
  open: boolean; onToggle: () => void; onClose: () => void;
  items: { label: string; onClick: () => void; danger?: boolean }[];
}) {
  if (!items.length) return <span className="w-8" />;
  return (
    <div className="relative">
      <button
        type="button"
        aria-label="More actions"
        onClick={onToggle}
        className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={onClose} />
          <div className="absolute right-0 top-9 z-40 w-44 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-pop animate-scale-in">
            {items.map((it) => (
              <button
                key={it.label}
                type="button"
                onClick={() => { onClose(); it.onClick(); }}
                className={cn(
                  "block w-full px-3 py-2 text-left text-sm hover:bg-slate-50",
                  it.danger ? "text-rose-600" : "text-slate-700"
                )}
              >
                {it.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
