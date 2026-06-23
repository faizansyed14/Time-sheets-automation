import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Mail,
  Paperclip,
  BadgeCheck,
  Archive,
  CheckCircle2,
  RotateCcw,
  Image as ImageIcon,
  FileText,
  Search,
  Undo2,
} from "lucide-react";
import {
  attachmentUrl,
  decideEmail,
  fetchEmail,
  fetchInbox,
  rerunExtraction,
  restoreEmail,
  type EmailListItem,
} from "../api/client";
import { cn, formatDateTime, initials, avatarColor } from "../lib/utils";
import { FilePreviewModal, PreviewableFileRow } from "../components/FilePreview";
import { Badge, Button, Card, EmptyState, PageHeader, Select, Skeleton, Spinner } from "../components/ui";
import { useToast } from "../components/toast";
import { useDebounced, useSentinel } from "../lib/useInfinite";
import type { PreviewFile } from "../lib/filePreview";

function StatusBadge({ status }: { status: EmailListItem["status"] }) {
  if (status === "ingested")
    return (
      <Badge tone="green">
        <CheckCircle2 className="h-3 w-3" /> Ingested
      </Badge>
    );
  if (status === "archived")
    return (
      <Badge tone="slate">
        <Archive className="h-3 w-3" /> Archived
      </Badge>
    );
  return <Badge tone="indigo">New</Badge>;
}

export default function InboxPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewFile | null>(null);

  useEffect(() => setPreview(null), [selected]);

  const dq = useDebounced(q, 350);
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["inbox", dq, status],
    queryFn: ({ pageParam }) => fetchInbox(dq, status, pageParam as number),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
  });
  const emails = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;
  // The list scrolls inside its own panel, so the sentinel must observe that
  // container (not the viewport) for auto-loading to fire as you scroll.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage,
    scrollRef
  );

  const { data: detail, isLoading: loadingDetail } = useQuery({
    queryKey: ["email", selected],
    queryFn: () => fetchEmail(selected!),
    enabled: !!selected,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["email", selected] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const decide = useMutation({
    mutationFn: ({ id, accepted }: { id: string; accepted: boolean }) => decideEmail(id, accepted),
    onSuccess: (res: any, { accepted }) => {
      if (accepted)
        toast(
          res.records_created > 0 ? "success" : "warning",
          res.records_created > 0
            ? `Extraction complete — ${res.records_created} record(s)`
            : "Extraction ran — no records created",
          res.records_created > 0
            ? "Weekly files for the same month are merged into one record."
            : "Check the Pipeline page: each file's failure reason is listed there."
        );
      else toast("info", "Email archived");
      invalidate();
    },
    onError: (e: any) => toast("error", "Action failed", e?.response?.data?.detail ?? String(e)),
  });

  const restore = useMutation({
    mutationFn: restoreEmail,
    onSuccess: () => {
      toast("info", "Email restored to New");
      invalidate();
    },
  });

  const rerun = useMutation({
    mutationFn: rerunExtraction,
    onSuccess: (res: any) => {
      toast("success", "Re-ran extraction", `${res.records_count} record(s) refreshed.`);
      invalidate();
    },
    onError: (e: any) => toast("error", "Re-run failed", e?.response?.data?.detail ?? String(e)),
  });

  return (
    <div className="flex h-full animate-fade-up flex-col">
      <PageHeader
        title="Email Inbox"
        subtitle="Review each timesheet email — Accept runs the extraction pipeline, Reject archives it."
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 xl:grid-cols-[400px_1fr]">
        {/* ---------------- list ---------------- */}
        <Card className="flex min-h-0 flex-col">
          <div className="flex items-center gap-2 border-b border-slate-100 p-3">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2 h-4 w-4 text-slate-400" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search emails…"
                className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none"
              />
            </div>
            <Select value={status} onChange={(e) => setStatus(e.target.value)} className="py-1.5 text-xs">
              <option value="">All</option>
              <option value="new">New</option>
              <option value="ingested">Ingested</option>
              <option value="archived">Archived</option>
            </Select>
          </div>
          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
            {isLoading ? (
              <div className="space-y-2 p-4">
                <Skeleton className="h-16" />
                <Skeleton className="h-16" />
                <Skeleton className="h-16" />
              </div>
            ) : !emails.length ? (
              <EmptyState icon={<Mail className="h-6 w-6" />} title="No emails found" />
            ) : (
              emails.map((m) => (
                <button
                  key={m.provider_message_id}
                  onClick={() => setSelected(m.provider_message_id)}
                  className={cn(
                    "flex w-full items-start gap-3 border-b border-slate-100 px-4 py-3 text-left transition-colors",
                    selected === m.provider_message_id
                      ? "bg-brand-50/70"
                      : "hover:bg-slate-50"
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                      avatarColor(m.sender_name)
                    )}
                  >
                    {initials(m.sender_name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-slate-800">
                        {m.sender_name}
                      </span>
                      <span className="shrink-0 text-[11px] text-slate-400">
                        {formatDateTime(m.received_at).split(",")[0]}
                      </span>
                    </span>
                    <span className="block truncate text-xs text-slate-500">{m.subject}</span>
                    <span className="mt-1 flex items-center gap-2">
                      <StatusBadge status={m.status} />
                      <span className="flex items-center gap-0.5 text-[11px] text-slate-400">
                        <Paperclip className="h-3 w-3" />
                        {m.attachment_count}
                      </span>
                      {m.has_approval_screenshot && (
                        <BadgeCheck className="h-3.5 w-3.5 text-emerald-500" />
                      )}
                    </span>
                  </span>
                </button>
              ))
            )}
            <div ref={sentinelRef} />
            {isFetchingNextPage && (
              <div className="flex items-center justify-center gap-2 py-3 text-xs text-slate-400">
                <Spinner className="h-4 w-4" /> Loading more…
              </div>
            )}
            {!isLoading && emails.length > 0 && (
              <p className="px-4 py-2 text-center text-[11px] text-slate-400">
                Showing {emails.length} of {total}
              </p>
            )}
          </div>
        </Card>

        {/* ---------------- detail ---------------- */}
        <Card className="flex min-h-0 flex-col">
          {!selected ? (
            <EmptyState
              icon={<Mail className="h-6 w-6" />}
              title="Select an email"
              detail="Pick a message on the left to preview its body and attachments."
            />
          ) : loadingDetail || !detail ? (
            <div className="flex flex-1 items-center justify-center">
              <Spinner className="h-6 w-6" />
            </div>
          ) : (
            <>
              <div className="border-b border-slate-100 p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-base font-bold text-slate-900">{detail.subject}</h2>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {detail.sender_name} &lt;{detail.sender_email}&gt; ·{" "}
                      {formatDateTime(detail.received_at)}
                    </p>
                  </div>
                  <StatusBadge status={detail.status} />
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {detail.status === "new" && (
                    <>
                      <Button
                        variant="success"
                        disabled={decide.isPending}
                        onClick={() =>
                          decide.mutate({ id: detail.provider_message_id, accepted: true })
                        }
                      >
                        {decide.isPending ? <Spinner className="border-white/40 border-t-white" /> : <CheckCircle2 className="h-4 w-4" />}
                        Accept · Run extraction
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={decide.isPending}
                        onClick={() =>
                          decide.mutate({ id: detail.provider_message_id, accepted: false })
                        }
                      >
                        <Archive className="h-4 w-4" /> Reject · Archive
                      </Button>
                    </>
                  )}
                  {detail.status === "archived" && (
                    <Button
                      variant="secondary"
                      onClick={() => restore.mutate(detail.provider_message_id)}
                    >
                      <Undo2 className="h-4 w-4" /> Restore to New
                    </Button>
                  )}
                  {detail.status === "ingested" && (
                    <Button
                      variant="secondary"
                      disabled={rerun.isPending}
                      onClick={() => rerun.mutate(detail.provider_message_id)}
                    >
                      <RotateCcw className={cn("h-4 w-4", rerun.isPending && "animate-spin")} />
                      Re-run extraction
                    </Button>
                  )}
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-5">
                <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-4 font-sans text-sm leading-6 text-slate-700">
                  {detail.body_text}
                </pre>

                <h3 className="mb-2 mt-5 text-xs font-bold uppercase tracking-wide text-slate-500">
                  Attachments ({detail.attachments.length})
                </h3>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {detail.attachments.map((a) => {
                    const file: PreviewFile = {
                      url: attachmentUrl(detail.provider_message_id, a.attachment_id),
                      filename: a.filename,
                      contentType: a.content_type,
                    };
                    return (
                      <PreviewableFileRow
                        key={a.attachment_id}
                        file={file}
                        onPreview={setPreview}
                        icon={
                          a.kind === "approval_screenshot" ? (
                            <ImageIcon className="h-5 w-5 shrink-0 text-emerald-500" />
                          ) : (
                            <FileText className="h-5 w-5 shrink-0 text-brand-500" />
                          )
                        }
                        subtitle={a.kind === "approval_screenshot" ? "Manager approval" : "Timesheet"}
                      />
                    );
                  })}
                </div>
              </div>
            </>
          )}
        </Card>
      </div>

      <FilePreviewModal file={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
