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
  UserCheck,
  AlertCircle,
  Check,
  Brain,
} from "lucide-react";
import {
  attachmentUrl,
  bodyImagePreviewUrl,
  decideEmail,
  fetchEmail,
  fetchInbox,
  rerunExtraction,
  restoreEmail,
  type EmailListItem,
  type IngestSelection,
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

function applyAiSelection(detail: { ai_check?: { recommended_timesheet_ids: string[]; recommended_approval_id: string | null } | null }) {
  const ai = detail.ai_check;
  if (!ai) return { timesheets: new Set<string>(), approval: null as string | null };
  return {
    timesheets: new Set(ai.recommended_timesheet_ids || []),
    approval: ai.recommended_approval_id,
  };
}

export default function InboxPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [selectedTimesheetIds, setSelectedTimesheetIds] = useState<Set<string>>(new Set());
  const [approvalAttachmentId, setApprovalAttachmentId] = useState<string | null>(null);
  const [extractBodyEnabled, setExtractBodyEnabled] = useState(false);
  const [aiRunning, setAiRunning] = useState(false);

  useEffect(() => setPreview(null), [selected]);
  useEffect(() => {
    setExtractBodyEnabled(false);
    setSelectedTimesheetIds(new Set());
    setApprovalAttachmentId(null);
  }, [selected]);

  const buildSelection = (): IngestSelection => ({
    attachment_ids: [...selectedTimesheetIds],
    approval_attachment_id: approvalAttachmentId,
    extract_body: extractBodyEnabled,
  });

  const extractCount = selectedTimesheetIds.size + (extractBodyEnabled ? 1 : 0);
  const canExtract = extractCount > 0;

  const dq = useDebounced(q, 350);
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["inbox", dq, status],
    queryFn: ({ pageParam }) => fetchInbox(dq, status, pageParam as number),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
  });
  const emails = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage,
    scrollRef
  );

  const { data: detail, isLoading: loadingDetail, isFetching: fetchingDetail } = useQuery({
    queryKey: ["email", selected],
    queryFn: () => fetchEmail(selected!),
    enabled: !!selected,
  });

  useEffect(() => {
    if (!detail) return;
    const sel = applyAiSelection(detail);
    if (detail.ai_check) {
      setSelectedTimesheetIds(sel.timesheets);
      setApprovalAttachmentId(sel.approval);
      setExtractBodyEnabled(!!detail.ai_check.extract_body);
    } else {
      const timesheetIds = detail.attachments
        .filter((a) => a.kind === "timesheet")
        .map((a) => a.attachment_id);
      setSelectedTimesheetIds(new Set(timesheetIds));
      setApprovalAttachmentId(null);
      setExtractBodyEnabled(false);
    }
  }, [detail?.provider_message_id, detail?.ai_check?.checked_at]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["email", selected] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const decide = useMutation({
    mutationFn: ({ id, accepted, selection }: { id: string; accepted: boolean; selection?: IngestSelection }) =>
      decideEmail(id, accepted, selection),
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
    mutationFn: ({ id, selection }: { id: string; selection: IngestSelection }) =>
      rerunExtraction(id, selection),
    onSuccess: (res: any) => {
      toast("success", "Re-ran extraction", `${res.records_count} record(s) refreshed.`);
      invalidate();
    },
    onError: (e: any) => toast("error", "Re-run failed", e?.response?.data?.detail ?? String(e)),
  });

  const runAiCheck = async () => {
    if (!selected) return;
    setAiRunning(true);
    try {
      const data = await fetchEmail(selected, true);
      qc.setQueryData(["email", selected], data);
    } finally {
      setAiRunning(false);
    }
  };

  const ai = detail?.ai_check;
  const aiAtt = (id: string) => ai?.attachments.find((a) => a.attachment_id === id);

  return (
    <div className="flex h-full animate-fade-up flex-col">
      <PageHeader
        title="Email Inbox"
        subtitle="Select an email, run AI Check, then Accept to extract."
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 xl:grid-cols-[400px_1fr]">
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
                    selected === m.provider_message_id ? "bg-brand-50/70" : "hover:bg-slate-50"
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
                      <span className="truncate text-sm font-semibold text-slate-800">{m.sender_name}</span>
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
                      {m.has_approval_screenshot && <BadgeCheck className="h-3.5 w-3.5 text-emerald-500" />}
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
          </div>
        </Card>

        <Card className="flex min-h-0 flex-col">
          {!selected ? (
            <EmptyState
              icon={<Mail className="h-6 w-6" />}
              title="Select an email"
              detail="Click AI Check to classify attachments and detect inline timesheets."
            />
          ) : loadingDetail || !detail ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3">
              <Spinner className="h-6 w-6" />
              <p className="text-sm text-slate-500">Loading email…</p>
            </div>
          ) : (
            <>
              <div className="border-b border-slate-100 p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-base font-bold text-slate-900">{detail.subject}</h2>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {detail.sender_name} &lt;{detail.sender_email}&gt; · {formatDateTime(detail.received_at)}
                    </p>
                    {ai?.matched_employee && (
                      <span className="mt-1.5 inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                        <UserCheck className="h-3 w-3" />
                        {ai.matched_employee.employee_name} · {ai.matched_employee.employee_id}
                        {ai.matched_employee.location ? ` · ${ai.matched_employee.location}` : ""}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {fetchingDetail && <Spinner className="h-4 w-4" />}
                    <StatusBadge status={detail.status} />
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button variant="secondary" disabled={aiRunning || loadingDetail} onClick={runAiCheck} title="Run AI check">
                    {aiRunning ? <Spinner className="h-4 w-4" /> : <Brain className="h-4 w-4" />}
                    AI Check
                  </Button>
                  {detail.status === "new" && (
                    <>
                      <Button
                        variant="success"
                        disabled={decide.isPending || !canExtract}
                        onClick={() =>
                          decide.mutate({
                            id: detail.provider_message_id,
                            accepted: true,
                            selection: buildSelection(),
                          })
                        }
                      >
                        {decide.isPending ? <Spinner className="border-white/40 border-t-white" /> : <CheckCircle2 className="h-4 w-4" />}
                        Accept · Extract ({extractCount})
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: detail.provider_message_id, accepted: false })}
                      >
                        <Archive className="h-4 w-4" /> Archive
                      </Button>
                    </>
                  )}
                  {detail.status === "archived" && (
                    <Button variant="secondary" onClick={() => restore.mutate(detail.provider_message_id)}>
                      <Undo2 className="h-4 w-4" /> Restore
                    </Button>
                  )}
                  {detail.status === "ingested" && (
                    <Button
                      variant="secondary"
                      disabled={rerun.isPending || !canExtract}
                      onClick={() =>
                        rerun.mutate({ id: detail.provider_message_id, selection: buildSelection() })
                      }
                    >
                      <RotateCcw className={cn("h-4 w-4", rerun.isPending && "animate-spin")} />
                      Re-run extraction
                    </Button>
                  )}
                </div>
              </div>

              {(ai || aiRunning) && (
                <div className="border-b border-slate-100 bg-slate-50/80 px-5 py-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Brain className="h-4 w-4 text-brand-600" />
                    <span className="text-sm font-semibold text-slate-800">AI analysis</span>
                    {aiRunning && <Spinner className="h-4 w-4" />}
                    {ai?.used_llm && ai.model && <Badge tone="slate">{ai.model}</Badge>}
                    {ai && !ai.used_llm && <Badge tone="slate">Rules only</Badge>}
                  </div>
                  {ai && (
                    <>
                      <p className="mt-1 text-sm text-slate-600">{ai.summary}</p>
                      <div className="mt-3 grid gap-3 md:grid-cols-2">
                        {ai.found.length > 0 && (
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-wide text-emerald-600">Found</p>
                            <ul className="mt-1 space-y-1">
                              {ai.found.map((line) => (
                                <li key={line} className="flex items-start gap-1.5 text-xs text-slate-700">
                                  <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                                  {line}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {ai.missing.length > 0 && (
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-wide text-amber-600">Notes</p>
                            <ul className="mt-1 space-y-1">
                              {ai.missing.map((line) => (
                                <li key={line} className="flex items-start gap-1.5 text-xs text-slate-700">
                                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                                  {line}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-y-auto p-5">
                {ai?.extract_body && (detail.status === "new" || detail.status === "ingested") && (
                  <div className="mb-4 rounded-lg border border-brand-200 bg-brand-50/70 p-4">
                    <label className="flex cursor-pointer items-start gap-3">
                      <input
                        type="checkbox"
                        checked={extractBodyEnabled}
                        onChange={(e) => setExtractBodyEnabled(e.target.checked)}
                        className="mt-1 rounded border-slate-300 text-brand-600"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="text-sm font-semibold text-slate-800">Convert email to image</span>
                        <p className="mt-0.5 text-xs text-slate-600">
                          Timesheet table is in the message body — renders subject + body to JPEG for pipeline extraction.
                        </p>
                        <Button
                          type="button"
                          variant="secondary"
                          className="mt-2"
                          onClick={(e) => {
                            e.preventDefault();
                            setPreview({
                              url: bodyImagePreviewUrl(detail.provider_message_id),
                              filename: "email_body.jpg",
                              contentType: "image/jpeg",
                            });
                          }}
                        >
                          <ImageIcon className="h-4 w-4" /> Preview image
                        </Button>
                      </span>
                    </label>
                  </div>
                )}

                <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-4 font-sans text-sm leading-6 text-slate-700">
                  {detail.body_text}
                </pre>

                <div className="mb-2 mt-5">
                  <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500">
                    Attachments ({detail.attachments.length})
                  </h3>
                </div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {detail.attachments.map((a) => {
                    const analysis = aiAtt(a.attachment_id);
                    const isTimesheet = a.kind === "timesheet" || analysis?.category === "timesheet";
                    const isApproval = a.kind === "approval_screenshot" || analysis?.category === "approval";
                    const extractable = isTimesheet || isApproval || !!analysis;
                    const timesheetChecked = selectedTimesheetIds.has(a.attachment_id);
                    const approvalChecked = approvalAttachmentId === a.attachment_id;
                    return (
                      <div
                        key={a.attachment_id}
                        className={cn(
                          "flex items-stretch gap-2 rounded-lg border border-slate-200 bg-white",
                          extractable && (timesheetChecked || approvalChecked) && "border-brand-300 ring-1 ring-brand-100"
                        )}
                      >
                        {extractable && (detail.status === "new" || detail.status === "ingested") && (
                          <div className="flex shrink-0 flex-col justify-center gap-2 border-r border-slate-100 px-2 py-2">
                            {isTimesheet && (
                              <label className="flex cursor-pointer items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                                <input
                                  type="checkbox"
                                  checked={timesheetChecked}
                                  onChange={(e) => {
                                    setSelectedTimesheetIds((prev) => {
                                      const next = new Set(prev);
                                      if (e.target.checked) next.add(a.attachment_id);
                                      else next.delete(a.attachment_id);
                                      return next;
                                    });
                                  }}
                                  className="rounded border-slate-300 text-brand-600"
                                />
                                Extract
                              </label>
                            )}
                            {isApproval && (
                              <label className="flex cursor-pointer items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-600">
                                <input
                                  type="checkbox"
                                  checked={approvalChecked}
                                  onChange={(e) => {
                                    if (e.target.checked) setApprovalAttachmentId(a.attachment_id);
                                    else setApprovalAttachmentId((c) => (c === a.attachment_id ? null : c));
                                  }}
                                  className="rounded border-slate-300 text-emerald-600"
                                />
                                Approval
                              </label>
                            )}
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <PreviewableFileRow
                            file={{
                              url: attachmentUrl(detail.provider_message_id, a.attachment_id),
                              filename: a.filename,
                              contentType: a.content_type,
                            }}
                            onPreview={setPreview}
                            className="border-0 hover:border-0"
                            icon={
                              isApproval ? (
                                <ImageIcon className="h-5 w-5 shrink-0 text-emerald-500" />
                              ) : isTimesheet ? (
                                <FileText className="h-5 w-5 shrink-0 text-brand-500" />
                              ) : (
                                <Paperclip className="h-5 w-5 shrink-0 text-slate-400" />
                              )
                            }
                            subtitle={
                              analysis
                                ? `${analysis.category} — ${analysis.reason}${
                                    analysis.used_ocr ? " · OCR" : ""
                                  }${
                                    analysis.text_chars != null && analysis.text_chars > 0
                                      ? ` · ${analysis.text_chars} chars`
                                      : ""
                                  }`
                                : isTimesheet
                                  ? "Timesheet document"
                                  : isApproval
                                    ? "Approval screenshot"
                                    : "Waiting for AI check"
                            }
                          />
                        </div>
                      </div>
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
