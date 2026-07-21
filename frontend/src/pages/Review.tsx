/**
 * Review — THE one place for everything waiting on a human.
 *
 * Every staged extraction (Extract Email / Run Extraction) and every failed
 * file lands here as a simple card. One click opens Compare & Fix with the
 * AI-extracted leaves pre-filled; Accept files the record + vault and the
 * queue advances automatically. No hunting through the Pipeline.
 *
 * The full Pipeline page remains as the detailed activity log.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowRight, CalendarDays, CheckCircle2, ClipboardCheck,
  FileText, PartyPopper, Sparkles, Trash2, User, Wand2,
} from "lucide-react";
import { deletePipelineFile, fetchPipeline, MONTHS_LONG, type PipelineFile } from "../api/client";
import PipelineCompareFixModal from "../components/PipelineCompareFixModal";
import { Badge, Button, Card, PageHeader, Skeleton } from "../components/ui";
import { useToast } from "../components/toast";
import { useSentinel } from "../lib/useInfinite";
import { cn, formatBytes, formatDateTime } from "../lib/utils";

function autoAcceptMeta(f: PipelineFile) {
  const auto = ((f.extraction_meta ?? {}) as Record<string, unknown>).auto_accept as
    | { confidence?: string; reasons?: string[] }
    | undefined;
  return { confidence: auto?.confidence ?? "high", reasons: auto?.reasons ?? [] };
}

/** Records the AI filed on its own. Nothing to do here — this is the audit
 *  trail beside the queue, so it stays compact enough for the sidebar. */
function AutoAcceptedCard({ f }: { f: PipelineFile }) {
  const [open, setOpen] = useState(false);
  const { leaves } = stagedMeta(f);
  const { reasons } = autoAcceptMeta(f);
  const period = f.month && f.year ? `${MONTHS_LONG[f.month]} ${f.year}` : "period unknown";

  return (
    <Card className="border-emerald-200/70 bg-emerald-50/30 p-3">
      <div className="flex items-start gap-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-600">
          <Sparkles className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-800">
            {f.employee_name ?? f.employee_id ?? "Employee not matched"}
          </p>
          <p className="truncate text-[11px] text-slate-500" title={f.filename}>
            {f.filename}
          </p>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-slate-500">
        <span className="inline-flex items-center gap-1">
          <CalendarDays className="h-3 w-3" /> {period}
        </span>
        <span className="font-semibold text-emerald-700">{leaves} day(s) filed</span>
        {f.created_at && <span>{formatDateTime(f.created_at)}</span>}
      </div>

      <div className="mt-2 flex items-center gap-2">
        {f.record_id && (
          <Link
            to={`/records/${f.record_id}`}
            className="inline-flex items-center gap-1 rounded-md border border-emerald-300 bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700 transition-colors hover:bg-emerald-50"
          >
            <FileText className="h-3 w-3" /> Record
          </Link>
        )}
        {reasons.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[11px] font-semibold text-slate-500 hover:text-slate-700"
          >
            {open ? "Hide why" : "Why?"}
          </button>
        )}
      </div>

      {open && reasons.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-emerald-200/70 pt-2 text-[11px] leading-relaxed text-slate-600">
          {reasons.slice(0, 6).map((r, i) => (
            <li key={i} className="flex gap-1.5">
              <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function stagedMeta(f: PipelineFile) {
  const staged = ((f.extraction_meta ?? {}) as Record<string, unknown>).staged as
    | { buckets?: Record<string, string[]>; month?: number | null; year?: number | null }
    | undefined;
  const buckets = staged?.buckets ?? {};
  const leaves = Object.values(buckets).reduce((a, v) => a + (v?.length ?? 0), 0);
  return { leaves, buckets };
}

function ReviewCard({
  f,
  onReview,
  onDelete,
  deleting,
}: {
  f: PipelineFile;
  onReview: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const isFailed = f.status === "failed";
  const { leaves, buckets } = stagedMeta(f);
  const period = f.month && f.year ? `${MONTHS_LONG[f.month]} ${f.year}` : "period unknown";

  return (
    <Card className="flex flex-col gap-3 p-4 transition-all hover:shadow-card-hover sm:flex-row sm:items-center">
      <div
        className={cn(
          "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
          isFailed ? "bg-rose-50 text-rose-500" : "bg-brand-50 text-brand-600"
        )}
      >
        {isFailed ? <AlertTriangle className="h-5 w-5" /> : <FileText className="h-5 w-5" />}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-semibold text-slate-800">{f.filename}</p>
          {isFailed ? (
            <Badge tone="danger">Failed — {f.failure_label ?? "needs fixing"}</Badge>
          ) : (
            <Badge tone="warning">Awaiting your review</Badge>
          )}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
          <span className="inline-flex items-center gap-1">
            <User className="h-3 w-3" /> {f.employee_name ?? f.employee_id ?? "employee not matched"}
          </span>
          <span className="inline-flex items-center gap-1">
            <CalendarDays className="h-3 w-3" /> {period}
          </span>
          {!isFailed && (
            <span className="font-semibold text-brand-600">{leaves} leave day(s) extracted</span>
          )}
          {f.size_bytes != null && <span>{formatBytes(f.size_bytes)}</span>}
          {f.created_at && <span>{formatDateTime(f.created_at)}</span>}
        </div>
        {!isFailed && leaves > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {Object.entries(buckets).filter(([, v]) => v?.length).map(([k, v]) => (
              <span key={k} className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                {k.replace("_", " ")} · {v.length}
              </span>
            ))}
          </div>
        )}
        {isFailed && f.failure_detail && (
          <p className="mt-1 line-clamp-2 text-xs text-rose-600">{f.failure_detail}</p>
        )}
      </div>

      <div className="flex shrink-0 flex-col gap-2 sm:flex-row sm:items-center">
        <Button onClick={onReview} className="shrink-0">
          <ClipboardCheck className="h-4 w-4" />
          {isFailed ? "Fix & file" : "Review & accept"}
        </Button>
        <Button
          variant="ghost"
          className="shrink-0 text-rose-500 hover:bg-rose-50"
          disabled={deleting}
          onClick={onDelete}
        >
          <Trash2 className="h-4 w-4" />
          Delete
        </Button>
      </div>
    </Card>
  );
}

export default function ReviewPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [reviewing, setReviewing] = useState<PipelineFile | null>(null);

  const [sp] = useSearchParams();
  const deepSourceId = sp.get("source_id");
  const didAutoOpenRef = useRef(false);

  const { data: needsReview, isLoading: l1, fetchNextPage: fetchNextReview, hasNextPage: hasMoreReview, isFetchingNextPage: fetchingReview } = useInfiniteQuery({
    queryKey: ["review", "needs_review"],
    queryFn: ({ pageParam }) =>
      fetchPipeline({
        status: "needs_review",
        source_kind: deepSourceId ? "email" : undefined,
        source_id: deepSourceId || undefined,
        offset: pageParam as number,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
    refetchInterval: 10_000,
  });
  const { data: failed, isLoading: l2, fetchNextPage: fetchNextFailed, hasNextPage: hasMoreFailed, isFetchingNextPage: fetchingFailed } = useInfiniteQuery({
    queryKey: ["review", "failed"],
    queryFn: ({ pageParam }) =>
      fetchPipeline({
        status: "failed",
        source_kind: deepSourceId ? "email" : undefined,
        source_id: deepSourceId || undefined,
        offset: pageParam as number,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
    refetchInterval: 10_000,
  });

  // Filed by the AI with no human review — shown as an audit trail so it is
  // visible WHAT the AI decided on its own, not just what it held back.
  const { data: autoAccepted, fetchNextPage: fetchNextAuto, hasNextPage: hasMoreAuto, isFetchingNextPage: fetchingAuto } = useInfiniteQuery({
    queryKey: ["review", "auto_accepted"],
    queryFn: ({ pageParam }) =>
      fetchPipeline({ status: "success", auto_accepted: true, offset: pageParam as number }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
    refetchInterval: 10_000,
  });
  const autoItems = autoAccepted?.pages.flatMap((p) => p.items) ?? [];
  const autoTotal = autoAccepted?.pages[0]?.total ?? 0;

  const needsReviewItems = needsReview?.pages.flatMap((p) => p.items) ?? [];
  const failedItems = failed?.pages.flatMap((p) => p.items) ?? [];
  const needsReviewTotal = needsReview?.pages[0]?.total ?? 0;
  const failedTotal = failed?.pages[0]?.total ?? 0;

  const items = useMemo(() => {
    const sort = (x: PipelineFile[]) =>
      [...x].sort((p, q) => (q.created_at ?? "").localeCompare(p.created_at ?? ""));
    return [...sort(needsReviewItems), ...sort(failedItems)];
  }, [needsReviewItems, failedItems]);

  // Deep-link: open Compare & Fix immediately for the email we came from.
  useEffect(() => {
    if (!deepSourceId) return;
    if (didAutoOpenRef.current) return;
    if (reviewing) return;
    if (!items.length) return;
    didAutoOpenRef.current = true;
    setReviewing(items[0]);
  }, [deepSourceId, items, reviewing]);

  const loading = l1 || l2;
  const hasNextPage = hasMoreReview || hasMoreFailed;
  const isFetchingNextPage = fetchingReview || fetchingFailed;
  const fetchNextPage = () => {
    if (hasMoreReview && !fetchingReview) fetchNextReview();
    if (hasMoreFailed && !fetchingFailed) fetchNextFailed();
  };
  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage
  );

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["review"] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
    qc.invalidateQueries({ queryKey: ["coverage"] });
    qc.invalidateQueries({ queryKey: ["inbox"] });
  };

  const deleteMut = useMutation({
    mutationFn: deletePipelineFile,
    onSuccess: (_data, id) => {
      toast("info", "Review item removed");
      if (reviewing?.id === id) setReviewing(null);
      invalidate();
    },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  const onSaved = () => {
    toast("success", "Record filed", "Saved to the records and File Vault.");
    invalidate();
    // Auto-advance: open the next item in the queue, if any.
    const next = items.find((i) => i.id !== reviewing?.id);
    setReviewing(next ?? null);
  };

  return (
    <div className="mx-auto max-w-7xl animate-fade-up">
      <PageHeader
        title="Review"
        subtitle="Everything waiting for you, in one place. Check the extracted leaves against the original file, fix anything, and Accept — that files the record."
      />

      {/* Two lanes: what still needs a human on the left, what the AI already
          filed on the right. Side by side so the queue never buries the
          audit trail (and vice-versa). Stacks on narrow screens. */}
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <div className="min-w-0 flex-1">
          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
            </div>
          ) : items.length === 0 ? (
            <Card className="flex flex-col items-center gap-3 py-16 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-500">
                <PartyPopper className="h-7 w-7" />
              </div>
              <p className="text-base font-bold text-slate-800">All caught up!</p>
              <p className="max-w-sm text-sm text-slate-500">
                Nothing needs your attention. New emails land in the Inbox — click
                <span className="mx-1 inline-flex items-center gap-1 font-semibold text-brand-600"><Wand2 className="h-3.5 w-3.5" />Extract Email</span>
                and the result appears here for a one-click review.
              </p>
              <Link
                to="/inbox"
                className="mt-1 inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-700"
              >
                Open Inbox <ArrowRight className="h-4 w-4" />
              </Link>
            </Card>
          ) : (
            <>
              <div className="mb-4 flex items-center gap-2 text-sm text-slate-600">
                <CheckCircle2 className="h-4 w-4 text-brand-600" />
                <span className="font-semibold">{needsReviewTotal + failedTotal}</span> item(s) waiting — accepting files the record and its documents automatically.
              </div>
              <div className="space-y-3">
                {items.map((f) => (
                  <ReviewCard
                    key={f.id}
                    f={f}
                    onReview={() => setReviewing(f)}
                    onDelete={() => deleteMut.mutate(f.id)}
                    deleting={deleteMut.isPending && deleteMut.variables === f.id}
                  />
                ))}
                {hasNextPage && (
                  <div ref={sentinelRef} className="py-4 text-center text-xs text-slate-400">
                    {isFetchingNextPage ? "Loading more…" : ""}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        <aside className="w-full shrink-0 lg:sticky lg:top-4 lg:w-[340px] xl:w-[380px]">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles className="h-4 w-4 shrink-0 text-emerald-600" />
            <span className="text-sm font-semibold text-slate-800">
              Auto-accepted by AI
            </span>
            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-bold text-emerald-700">
              {autoTotal}
            </span>
          </div>

          {autoItems.length === 0 ? (
            <Card className="px-4 py-6 text-center">
              <p className="text-xs text-slate-500">
                Nothing filed automatically yet. Fully verified sheets are filed
                without review and appear here.
              </p>
            </Card>
          ) : (
            <>
              <p className="mb-3 text-xs text-slate-500">
                Filed automatically because everything checked out — no action needed.
              </p>
              <div className="max-h-[calc(100vh-13rem)] space-y-2.5 overflow-y-auto pr-1">
                {autoItems.map((f) => (
                  <AutoAcceptedCard key={f.id} f={f} />
                ))}
                {hasMoreAuto && (
                  <div className="pt-1 text-center">
                    <Button
                      variant="secondary"
                      onClick={() => fetchNextAuto()}
                      disabled={fetchingAuto}
                    >
                      {fetchingAuto ? "Loading…" : "Show more"}
                    </Button>
                  </div>
                )}
              </div>
            </>
          )}
        </aside>
      </div>

      <PipelineCompareFixModal
        file={reviewing}
        onClose={() => setReviewing(null)}
        onSaved={onSaved}
        onDiscarded={invalidate}
      />
    </div>
  );
}
