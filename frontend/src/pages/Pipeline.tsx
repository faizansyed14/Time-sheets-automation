import { useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  BadgeCheck,
  Columns2,
  RotateCcw,
  ChevronDown,
  ChevronRight,
  Mail,
  UploadCloud,
  ExternalLink,
  Lock,
  Trash2,
  Cpu,
  ScanLine,
} from "lucide-react";
import {
  deletePipelineFile,
  fetchPipeline,
  fetchPipelineStats,
  resolvePipelineFile,
  retryPipelineFile,
  MONTHS,
  type PipelineFile,
} from "../api/client";
import PipelineCompareFixModal from "../components/PipelineCompareFixModal";
import StoredFilesPreview from "../components/StoredFilesPreview";
import { cn, formatBytes, formatDateTime } from "../lib/utils";
import { Button, Card, EmptyState, Modal, PageHeader, Select, Skeleton } from "../components/ui";
import { FailureChip, PipelineStatusBadge, StageTimeline } from "../components/status";
import { useToast } from "../components/toast";
import { useDebounced, useSentinel } from "../lib/useInfinite";
import { Spinner } from "../components/ui";

type Filter = "" | "failed" | "needs_review" | "success" | "resolved" | "processing";

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

/** Per-file cost/provenance badge: which GPT model handled the file (gpt-4o vs
 * the cheaper gpt-4o-mini), or whether a no-LLM path read it, plus an OCR chip.
 * Lets reviewers see — and control — extraction cost at a glance. */
function ModelBadge({ file }: { file: PipelineFile }) {
  const method = file.extraction_method;
  if (!method) return null;

  let label: string;
  let tone: string;
  if (method === "vision-llm") {
    const model = file.extraction_model ?? "vision model";
    label = model;
    // gpt-4o-mini (and other "mini"/"nano") = cheap → green; full gpt-4o = amber.
    const cheap = /mini|nano|small|haiku|flash/i.test(model);
    tone = cheap
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : "border-amber-200 bg-amber-50 text-amber-700";
  } else if (method === "deterministic-text") {
    label = "No LLM · text";
    tone = "border-emerald-200 bg-emerald-50 text-emerald-700";
  } else if (method === "mock") {
    label = "Mock engine";
    tone = "border-slate-200 bg-slate-50 text-slate-500";
  } else if (method === "manual") {
    label = "Manual entry";
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
          className="inline-flex items-center gap-1 rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700"
        >
          <ScanLine className="h-3 w-3" />
          OCR
        </span>
      )}
    </span>
  );
}

/** Collapsible "Extraction details" panel: the full metadata for a file —
 * GPT model, method, OCR provider/status, render DPI, image detail, pages,
 * text-layer presence, validation model, embedded .eml attachment, etc. Lets a
 * reviewer audit exactly how (and how expensively) each file was read. */
function ExtractionDetails({ file }: { file: PipelineFile }) {
  const [open, setOpen] = useState(false);
  const meta = (file.extraction_meta ?? {}) as Record<string, unknown>;

  const methodLabel: Record<string, string> = {
    "vision-llm": "Vision LLM",
    "deterministic-text": "Deterministic (no LLM)",
    mock: "Mock engine",
    manual: "Manual entry",
    unsupported: "Unsupported file",
  };
  const fmt = (v: unknown): string => {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "boolean") return v ? "Yes" : "No";
    return String(v);
  };

  // Curated, human-labelled rows (in priority order); anything we don't
  // explicitly name is shown afterwards so nothing is hidden.
  const known: [string, string, unknown][] = [
    ["GPT model", "model", file.extraction_model ?? meta["model"]],
    ["Method", "method", methodLabel[file.extraction_method ?? ""] ?? file.extraction_method ?? meta["method"]],
    ["OCR (Tesseract) used", "used_ocr", file.used_ocr],
    ["OCR provider", "ocr_provider", meta["ocr_provider"]],
    ["OCR status", "ocr_status", meta["ocr_status"]],
    ["Render DPI", "render_dpi", meta["render_dpi"]],
    ["Image detail", "image_detail", meta["image_detail"]],
    ["Pages rendered", "page_count", meta["page_count"]],
    ["Has text layer", "has_text_layer", meta["has_text_layer"]],
    ["Document text (chars)", "doc_text_chars", meta["doc_text_chars"]],
    ["Validation model", "validation_model", meta["validation_model"]],
    ["File type", "file_type", meta["file_type"]],
    ["Source", "source_kind", meta["source_kind"]],
    ["Content type", "content_type", meta["content_type"]],
    ["Size (bytes)", "size_bytes", meta["size_bytes"]],
    ["Embedded attachment", "embedded_attachment", meta["embedded_attachment"]],
    ["Embedded type", "embedded_type", meta["embedded_type"]],
  ];
  const shownKeys = new Set(known.map(([, k]) => k));
  const extras = Object.entries(meta).filter(([k]) => !shownKeys.has(k));
  const rows = known.filter(([, , v]) => v !== undefined && v !== null && v !== "");

  return (
    <div className="mb-4 overflow-hidden rounded-lg border border-slate-200 bg-white">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50"
      >
        {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        <Cpu className="h-4 w-4 text-slate-400" />
        Extraction details
        <span className="ml-2"><ModelBadge file={file} /></span>
      </button>
      {open && (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 border-t border-slate-100 px-5 py-3 text-sm sm:grid-cols-2">
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
      )}
    </div>
  );
}

export default function PipelinePage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [statusFilter, setStatusFilter] = useState<Filter>("");
  const [codeFilter, setCodeFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [resolving, setResolving] = useState<PipelineFile | null>(null);
  const [assigning, setAssigning] = useState<PipelineFile | null>(null);
  const [note, setNote] = useState("");

  const { data: stats } = useQuery({
    queryKey: ["pipeline-stats"],
    queryFn: fetchPipelineStats,
    refetchInterval: 15_000,
  });
  const dq = useDebounced(q, 350);
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["pipeline", statusFilter, codeFilter, sourceFilter, dq],
    queryFn: ({ pageParam }) =>
      fetchPipeline({
        status: statusFilter || undefined,
        failure_code: codeFilter || undefined,
        source_kind: sourceFilter || undefined,
        q: dq || undefined,
        offset: pageParam as number,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
    refetchInterval: 15_000,
  });
  const items = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;
  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage
  );

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const resolveMut = useMutation({
    mutationFn: ({ id, n }: { id: string; n: string }) => resolvePipelineFile(id, n),
    onSuccess: (t) => {
      toast("success", "Marked as resolved", `${t.filename} moved to Resolved.`);
      setResolving(null);
      setNote("");
      invalidate();
    },
    onError: (e: any) =>
      toast("error", "Could not resolve", e?.response?.data?.detail ?? String(e)),
  });

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
      qc.invalidateQueries({ queryKey: ["review"] });
    },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  const failureCodes = Object.entries(stats?.by_failure_code ?? {});

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Activity log"
        subtitle="Every file that entered the extraction pipeline — where it is, where it failed and exactly why."
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
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
          label="Resolved"
          value={stats?.resolved ?? 0}
          icon={<BadgeCheck className="h-5 w-5 text-sky-600" />}
          tone="bg-sky-50"
          active={statusFilter === "resolved"}
          onClick={() => setStatusFilter(statusFilter === "resolved" ? "" : "resolved")}
        />
      </div>

      {failureCodes.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Failure reasons
          </span>
          {failureCodes.map(([code, count]) => (
            <button
              key={code}
              onClick={() => setCodeFilter(codeFilter === code ? "" : code)}
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                codeFilter === code
                  ? "border-rose-300 bg-rose-50 text-rose-700"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
              )}
            >
              {code === "protected_pdf" && <Lock className="h-3 w-3" />}
              {stats?.failure_labels[code] ?? code}
              <span className="rounded-full bg-slate-100 px-1.5 font-bold text-slate-500">{count}</span>
            </button>
          ))}
        </div>
      )}

      <Card>
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
          <p className="ml-auto text-xs text-slate-400">
            {items.length} of {total} file{total !== 1 && "s"}
          </p>
        </div>

        {isLoading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Activity className="h-6 w-6" />}
            title="Nothing here"
            detail="Files appear the moment they enter the pipeline — from an accepted email or an upload."
          />
        ) : (
          <div className="divide-y divide-slate-100">
            {items.map((f) => {
              const expanded = open === f.id;
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
                      <span className="block truncate text-xs text-slate-400">
                        {f.employee_name ?? "Unidentified"}
                        {f.month ? ` · ${MONTHS[f.month]} ${f.year}` : ""} ·{" "}
                        {formatBytes(f.size_bytes)} · {formatDateTime(f.created_at)}
                      </span>
                    </span>
                    <span className="hidden md:block">
                      <FailureChip code={f.failure_code} label={f.failure_label} />
                    </span>
                    <PipelineStatusBadge status={f.status} />
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
                    <div className="border-t border-slate-100 bg-slate-50/60 px-6 py-5">
                      <ExtractionDetails file={f} />
                      {f.failure_detail && (
                        <div
                          className={cn(
                            "mb-4 rounded-lg border p-3 text-sm",
                            f.status === "failed"
                              ? "border-rose-200 bg-rose-50 text-rose-700"
                              : "border-amber-200 bg-amber-50 text-amber-800"
                          )}
                        >
                          <p className="font-semibold">
                            {f.failure_label ?? "Issue"}
                          </p>
                          <p className="mt-0.5 leading-5">{f.failure_detail}</p>
                        </div>
                      )}
                      {f.resolution_note && (
                        <div className="mb-4 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-800">
                          <p className="font-semibold">Resolution</p>
                          <p className="mt-0.5 leading-5">{f.resolution_note}</p>
                        </div>
                      )}

                      <StageTimeline file={f} />

                      {f.record_id && (f.status === "success" || f.status === "needs_review") && (
                        <StoredFilesPreview recordId={f.record_id} />
                      )}

                      <div className="mt-5 flex flex-wrap items-center gap-2">
                        {(f.status === "failed" || f.status === "needs_review") && (
                          <>
                            {f.can_resolve_assign && (
                              <Button
                                size="sm"
                                onClick={() => setAssigning(f)}
                              >
                                <Columns2 className="h-4 w-4" /> Compare &amp; Fix
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant={f.can_resolve_assign ? "secondary" : "primary"}
                              onClick={() => {
                                setResolving(f);
                                setNote("");
                              }}
                            >
                              <BadgeCheck className="h-4 w-4" />
                              {f.can_resolve_assign ? "Mark resolved" : "Resolve"}
                            </Button>
                          </>
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
                        <Button
                          size="sm"
                          variant="ghost"
                          className="ml-auto text-rose-500 hover:bg-rose-50"
                          disabled={deleteMut.isPending}
                          onClick={() => deleteMut.mutate(f.id)}
                        >
                          <Trash2 className="h-4 w-4" /> Delete
                        </Button>
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

      <Modal
        open={!!resolving}
        onClose={() => setResolving(null)}
        title="Resolve this file"
        subtitle={resolving ? `${resolving.filename} — ${resolving.failure_label ?? resolving.failure_detail ?? ""}` : undefined}
      >
        <p className="mb-3 text-sm leading-6 text-slate-600">
          Resolving marks this file as handled and moves it out of the failed queue. Add a short
          note so the team knows what was done (e.g. “asked sender for unprotected copy”,
          “processed manually”).
        </p>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder="Resolution note (optional)…"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setResolving(null)}>
            Cancel
          </Button>
          <Button
            disabled={resolveMut.isPending}
            onClick={() => resolving && resolveMut.mutate({ id: resolving.id, n: note })}
          >
            <BadgeCheck className="h-4 w-4" /> Mark resolved
          </Button>
        </div>
      </Modal>
    </div>
  );
}
