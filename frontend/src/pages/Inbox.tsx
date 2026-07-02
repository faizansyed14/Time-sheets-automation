import { useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
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
  ChevronDown,
  ChevronRight,
  Forward,
  Maximize2,
} from "lucide-react";
import {
  attachmentUrl,
  bodyImagePreviewUrl,
  decideEmail,
  fetchEmail,
  fetchInbox,
  rerunExtraction,
  restoreEmail,
  stageExtraction,
  type Attachment,
  type EmailAiCheck,
  type EmailListItem,
  type IngestSelection,
  type PipelineFile,
} from "../api/client";
import { cn, formatDateTime, initials, avatarColor } from "../lib/utils";
import { FilePreviewModal } from "../components/FilePreview";
import PipelineCompareFixModal from "../components/PipelineCompareFixModal";
import { sanitizeEmailHtml } from "../lib/filePreview";
import { Badge, Button, Card, EmptyState, Select, Skeleton, Spinner } from "../components/ui";
import { useToast } from "../components/toast";
import { useDebounced, useSentinel } from "../lib/useInfinite";
import type { PreviewFile } from "../lib/filePreview";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Forwarded-email body parser
// Detects common divider patterns used by Outlook / Gmail / mobile clients.
// ---------------------------------------------------------------------------

const FWD_DIVIDERS = [
  // Outlook Windows / Mac
  /^_{3,}[\r\n]+From:/m,
  // Outlook web "Original Message"
  /^-{3,}\s*Original Message\s*-{3,}/im,
  // Gmail
  /^-{5,}\s*Forwarded message\s*-{5,}/im,
  // Generic dash line followed by From:
  /^-{3,}[\r\n]+From:/m,
];

interface EmailParts {
  outer: string;      // text written by the forwarder
  forwarded: string;  // the nested original message
  isForwarded: boolean;
}

function splitForwardedBody(body: string): EmailParts {
  for (const re of FWD_DIVIDERS) {
    const match = body.match(re);
    if (match && match.index !== undefined) {
      return {
        outer: body.slice(0, match.index).trim(),
        forwarded: body.slice(match.index).trim(),
        isForwarded: true,
      };
    }
  }
  return { outer: body, forwarded: "", isForwarded: false };
}

function isForwardedSubject(subject: string | null): boolean {
  if (!subject) return false;
  return /^(fw|fwd):/i.test(subject.trim());
}

// ---------------------------------------------------------------------------
// CID (inline image) helpers
// Matches [cid:filename@domain] or [cid:content-id] references in plain-text
// bodies and maps them to their attachment download URLs.
// ---------------------------------------------------------------------------

const CID_RE = /\[cid:([^\]]+)\]/g;
// Matches src="cid:..." and src='cid:...' inside HTML bodies
const HTML_CID_RE = /src\s*=\s*["']cid:([^"']+)["']/gi;

/**
 * Returns a map of raw CID token (e.g. "[cid:image001.jpg@xxx]") → attachment URL.
 * Uses filename match (case-insensitive, part before the first "@").
 */
function buildCidMap(
  bodyText: string,
  attachments: Attachment[],
  providerId: string,
): Map<string, string> {
  const map = new Map<string, string>();
  for (const match of bodyText.matchAll(CID_RE)) {
    const att = findByCid(match[1], attachments);
    if (att) map.set(match[0], attachmentUrl(providerId, att.attachment_id));
  }
  return map;
}

/**
 * Returns the set of attachment IDs that are referenced inline via:
 *   [cid:...] in plain-text bodies, or
 *   src="cid:..." in HTML bodies.
 * These are rendered in the body and hidden from the chip list.
 */
/** Find attachment by CID ref: match on attachment.cid first, then filename fallback. */
function findByCid(cidRef: string, attachments: Attachment[]): Attachment | undefined {
  const cidLower = cidRef.toLowerCase();
  const cidName = cidRef.split("@")[0].toLowerCase();
  // Exact cid match (strip angle brackets Graph sometimes adds)
  let att = attachments.find((a) => {
    const ac = (a.cid ?? "").replace(/^<|>$/g, "").toLowerCase();
    return ac === cidLower || ac === cidName;
  });
  // Filename fallback
  if (!att) att = attachments.find((a) => a.filename.toLowerCase() === cidName);
  return att;
}

function inlineAttachmentIds(
  bodyText: string,
  bodyHtml: string | null,
  attachments: Attachment[],
): Set<string> {
  const ids = new Set<string>();

  // Plain-text [cid:...] references
  for (const match of bodyText.matchAll(CID_RE)) {
    const att = findByCid(match[1], attachments);
    if (att) ids.add(att.attachment_id);
  }

  // HTML src="cid:..." references (Graph emails)
  if (bodyHtml) {
    for (const match of bodyHtml.matchAll(HTML_CID_RE)) {
      const att = findByCid(match[1], attachments);
      if (att) ids.add(att.attachment_id);
    }
  }

  return ids;
}

// ---------------------------------------------------------------------------
// Plain-text segment renderer — replaces [cid:...] with <img> tags inline
// ---------------------------------------------------------------------------

function TextWithInlineImages({
  text,
  cidMap,
  className,
}: {
  text: string;
  cidMap: Map<string, string>;
  className?: string;
}) {
  if (cidMap.size === 0) {
    return <pre className={cn("whitespace-pre-wrap font-sans text-sm leading-6 text-slate-700", className)}>{text}</pre>;
  }

  // Split on every [cid:...] token and interleave <img> elements.
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(CID_RE)) {
    const token = match[0];
    const url = cidMap.get(token);
    if (match.index !== undefined && match.index > last) {
      parts.push(
        <span key={`t-${last}`} className="whitespace-pre-wrap">
          {text.slice(last, match.index)}
        </span>
      );
    }
    if (url) {
      parts.push(
        <img
          key={`img-${match.index}`}
          src={url}
          alt={match[1].split("@")[0]}
          className="my-1 inline-block max-h-12 max-w-[120px] object-contain align-middle"
        />
      );
    } else if (match.index !== undefined) {
      // Unknown CID — keep the token as text (hidden to reduce noise)
      parts.push(
        <span key={`cid-${match.index}`} className="hidden">
          {token}
        </span>
      );
    }
    if (match.index !== undefined) last = match.index + token.length;
  }
  if (last < text.length) {
    parts.push(
      <span key={`t-end`} className="whitespace-pre-wrap">
        {text.slice(last)}
      </span>
    );
  }

  return (
    <p className={cn("font-sans text-sm leading-6 text-slate-700", className)}>
      {parts}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Email body renderer — HTML iframe when body_html available, text fallback
// ---------------------------------------------------------------------------

/**
 * Replaces `cid:filename@...` in an HTML string with actual attachment URLs
 * so inline images (logos, signatures, tables) render correctly in the iframe.
 */
function resolveCidsInHtml(html: string, attachments: Attachment[], providerId: string): string {
  return html.replace(/cid:([^"'\s>)]+)/gi, (_, cidRef: string) => {
    const att = findByCid(cidRef, attachments);
    return att ? attachmentUrl(providerId, att.attachment_id) : "#";
  });
}

function EmailBodyRenderer({
  bodyText,
  bodyHtml,
  subject,
  attachments,
  providerId,
}: {
  bodyText: string | null;
  bodyHtml: string | null;
  subject: string | null;
  attachments: Attachment[];
  providerId: string;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [frameHeight, setFrameHeight] = useState(320);

  const text = bodyText ?? "";
  const cidMap = useMemo(() => buildCidMap(text, attachments, providerId), [text, attachments, providerId]);
  const { outer, forwarded, isForwarded } = useMemo(() => splitForwardedBody(text), [text]);
  const detectedFwd = isForwarded || isForwardedSubject(subject);

  useEffect(() => {
    if (!bodyHtml) {
      setBlobUrl(null);
      return;
    }
    const resolved = resolveCidsInHtml(bodyHtml, attachments, providerId);
    const safe = sanitizeEmailHtml(resolved);
    const doc =
      `<!doctype html><html><head><meta charset="utf-8">` +
      `<base target="_blank">` +
      `<style>html,body{margin:0;padding:12px;font-family:Calibri,Segoe UI,Arial,sans-serif;` +
      `color:#1f2937;font-size:14px;line-height:1.5;word-wrap:break-word}` +
      `img{max-width:min(100%,220px);max-height:96px;height:auto;object-fit:contain;display:block;margin:4px 0}` +
      `table{max-width:100%}</style></head>` +
      `<body>${safe}</body></html>`;
    const blob = URL.createObjectURL(new Blob([doc], { type: "text/html" }));
    setBlobUrl(blob);
    return () => URL.revokeObjectURL(blob);
  }, [bodyHtml, attachments, providerId]);

  const sizeToContent = (e: React.SyntheticEvent<HTMLIFrameElement>) => {
    try {
      const body = e.currentTarget.contentWindow?.document?.body;
      if (body) setFrameHeight(Math.max(120, body.scrollHeight + 24));
    } catch {
      /* cross-origin guard — keep default height */
    }
  };

  if (bodyHtml) {
    if (!blobUrl) return null;
    return (
      <iframe
        key={blobUrl}
        src={blobUrl}
        title="Email body"
        onLoad={sizeToContent}
        sandbox="allow-same-origin allow-popups"
        style={{ height: frameHeight }}
        className="w-full border-0"
      />
    );
  }

  if (!text.trim()) {
    return (
      <p className="py-6 text-center text-sm italic text-slate-400">(no message body)</p>
    );
  }

  if (detectedFwd && forwarded) {
    return (
      <div className="space-y-3">
        {outer && <TextWithInlineImages text={outer} cidMap={cidMap} />}
        <div className="rounded-lg border border-slate-200 bg-slate-50/60">
          <div className="flex items-center gap-1.5 border-b border-slate-200 px-3 py-2">
            <Forward className="h-3.5 w-3.5 text-slate-400" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              Forwarded message
            </span>
          </div>
          <div className="p-3">
            <TextWithInlineImages text={forwarded} cidMap={cidMap} className="text-slate-600" />
          </div>
        </div>
      </div>
    );
  }

  return <TextWithInlineImages text={text} cidMap={cidMap} />;
}

// ---------------------------------------------------------------------------
// AI analysis panel — collapsible, collapsed by default
// ---------------------------------------------------------------------------

function AiAnalysisPanel({
  ai,
  aiRunning,
}: {
  ai: EmailAiCheck | null;
  aiRunning: boolean;
}) {
  const [open, setOpen] = useState(true);

  // Auto-expand while the backend runs the first-time AI check on open.
  useEffect(() => {
    if (aiRunning) setOpen(true);
  }, [aiRunning]);
  useEffect(() => {
    if (ai?.checked_at) setOpen(true);
  }, [ai?.checked_at]);

  if (!ai && !aiRunning) return null;

  return (
    <div className="border-b border-slate-100 bg-slate-50/80">
      {/* Collapsible header */}
      <button
        type="button"
        onClick={() => setOpen((v: boolean) => !v)}
        className="flex w-full items-center gap-2 px-5 py-2.5 text-left"
      >
        <Brain className="h-4 w-4 shrink-0 text-brand-600" />
        <span className="flex-1 text-sm font-semibold text-slate-800">AI analysis</span>
        {aiRunning && <Spinner className="h-4 w-4" />}
        {ai?.used_llm && ai.model && <Badge tone="slate">{ai.model}</Badge>}
        {ai && !ai.used_llm && <Badge tone="slate">Rules only</Badge>}
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
        )}
      </button>

      {/* Expandable body */}
      {open && ai && (
        <div className="px-5 pb-4">
          <p className="text-sm text-slate-600">{ai.summary}</p>
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
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outlook-style attachment chips rendered at the top of the email
// ---------------------------------------------------------------------------

// Detect document vs image attachments.
// Documents (pdf / docx / xlsx / eml) go in the top chip strip.
// Images (png / jpg / gif / etc.) that are NOT inline go in a separate section with approval checkbox.
const DOC_TYPES = new Set(["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "message/rfc822"]);
const DOC_EXTS = new Set(["pdf", "docx", "xlsx", "eml"]);

function isDocAttachment(a: Attachment): boolean {
  if (DOC_TYPES.has(a.content_type)) return true;
  const ext = a.filename.split(".").pop()?.toLowerCase() ?? "";
  return DOC_EXTS.has(ext);
}

function isImageAttachment(a: Attachment): boolean {
  return a.content_type.startsWith("image/") || /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(a.filename);
}

function AttachmentChip({
  a,
  providerId,
  selectedTimesheetIds,
  setSelectedTimesheetIds,
  status,
  setPreview,
  aiAtt,
}: {
  a: Attachment;
  providerId: string;
  ai?: any;
  selectedTimesheetIds: Set<string>;
  setSelectedTimesheetIds: Dispatch<SetStateAction<Set<string>>>;
  status: string;
  setPreview: (f: PreviewFile) => void;
  aiAtt: (id: string) => any;
}) {
  const analysis = aiAtt(a.attachment_id);
  const isTimesheet = a.kind === "timesheet" || analysis?.category === "timesheet";
  const timesheetChecked = selectedTimesheetIds.has(a.attachment_id);
  const canExtract = isTimesheet && (status === "new" || status === "ingested");

  const chipColor = timesheetChecked
    ? "border-brand-300 bg-brand-50 ring-1 ring-brand-100"
    : "border-slate-200 bg-white hover:border-brand-200";

  return (
    <div className={cn("flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs transition-colors", chipColor)}>
      <FileText className="h-4 w-4 shrink-0 text-brand-500" />
      <span className="min-w-0">
        <span className="block max-w-[200px] truncate font-medium text-slate-700">{a.filename}</span>
        {analysis && (
          <span className="block text-[10px] text-slate-400">
            {analysis.category}{analysis.used_ocr ? " · OCR" : ""}
          </span>
        )}
      </span>
      {canExtract && (
        <label className="flex cursor-pointer items-center gap-1 text-[10px] font-semibold text-slate-500">
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
      <button
        type="button"
        onClick={() => setPreview({ url: attachmentUrl(providerId, a.attachment_id), filename: a.filename, contentType: a.content_type })}
        className="ml-1 shrink-0 text-[10px] font-semibold uppercase tracking-wide text-brand-500 hover:text-brand-700"
      >
        Preview
      </button>
    </div>
  );
}

// Collapsible block — images default closed so the email body stays visible.
function CollapsibleAttachmentSection({
  title,
  icon,
  defaultOpen = false,
  hint,
  children,
}: {
  title: string;
  icon: ReactNode;
  defaultOpen?: boolean;
  hint?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50/50">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[11px] font-bold uppercase tracking-wide text-slate-500 hover:bg-slate-50"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        )}
        {icon}
        <span className="flex-1">{title}</span>
      </button>
      {open && (
        <div className="border-t border-slate-100 px-3 pb-3 pt-2">
          {children}
          {hint && <p className="mt-2 text-[11px] text-slate-400">{hint}</p>}
        </div>
      )}
    </div>
  );
}

// Image card — compact thumbnail; click → lightbox. Extract / Approval checkboxes.
function ImageCard({
  a, providerId, selectedTimesheetIds, setSelectedTimesheetIds,
  approvalAttachmentId, setApprovalAttachmentId, status, setPreview, aiAtt,
}: {
  a: Attachment;
  providerId: string;
  selectedTimesheetIds: Set<string>;
  setSelectedTimesheetIds: Dispatch<SetStateAction<Set<string>>>;
  approvalAttachmentId: string | null;
  setApprovalAttachmentId: Dispatch<SetStateAction<string | null>>;
  status: string;
  setPreview: (f: PreviewFile) => void;
  aiAtt: (id: string) => any;
}) {
  const url = attachmentUrl(providerId, a.attachment_id);
  const analysis = aiAtt(a.attachment_id);
  const approvalChecked = approvalAttachmentId === a.attachment_id;
  const extractChecked = selectedTimesheetIds.has(a.attachment_id);
  const canDecide = status === "new" || status === "ingested";

  return (
    <div
      className={cn(
        "group flex w-[108px] flex-col overflow-hidden rounded-lg border text-[10px] transition-colors",
        extractChecked
          ? "border-brand-300 ring-1 ring-brand-100"
          : approvalChecked
            ? "border-emerald-300 ring-1 ring-emerald-100"
            : "border-slate-200 hover:border-brand-200",
      )}
    >
      <button
        type="button"
        title="Click to enlarge"
        onClick={() => setPreview({ url, filename: a.filename, contentType: a.content_type })}
        className="relative block h-14 w-full overflow-hidden bg-slate-100"
      >
        <img src={url} alt={a.filename} className="h-full w-full object-contain p-0.5" />
        <span className="absolute inset-0 flex items-center justify-center bg-slate-900/0 opacity-0 transition-all group-hover:bg-slate-900/20 group-hover:opacity-100">
          <span className="inline-flex items-center gap-0.5 rounded bg-white/90 px-1.5 py-0.5 text-[9px] font-semibold text-slate-700">
            <Maximize2 className="h-2.5 w-2.5" />
          </span>
        </span>
      </button>
      <div className="flex flex-col gap-1 p-1.5">
        <span className="truncate font-medium text-slate-700" title={a.filename}>{a.filename}</span>
        {analysis?.category && (
          <span className="text-[9px] text-slate-400">{analysis.category}{analysis.used_ocr ? " · OCR" : ""}</span>
        )}
        {canDecide && (
          <div className="flex flex-wrap gap-1.5">
            <label className="flex cursor-pointer items-center gap-0.5 text-[9px] font-semibold text-brand-600">
              <input
                type="checkbox"
                checked={extractChecked}
                onChange={(e) =>
                  setSelectedTimesheetIds((prev) => {
                    const next = new Set(prev);
                    if (e.target.checked) next.add(a.attachment_id);
                    else next.delete(a.attachment_id);
                    return next;
                  })
                }
                className="rounded border-slate-300 text-brand-600"
              />
              Extract
            </label>
            <label className="flex cursor-pointer items-center gap-0.5 text-[9px] font-semibold text-emerald-600">
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
          </div>
        )}
      </div>
    </div>
  );
}

function AttachmentChips({
  attachments,
  providerId,
  ai,
  selectedTimesheetIds,
  setSelectedTimesheetIds,
  approvalAttachmentId,
  setApprovalAttachmentId,
  status,
  setPreview,
  aiAtt,
  inlineIds,
}: {
  attachments: Attachment[];
  providerId: string;
  ai: any;
  selectedTimesheetIds: Set<string>;
  setSelectedTimesheetIds: Dispatch<SetStateAction<Set<string>>>;
  approvalAttachmentId: string | null;
  setApprovalAttachmentId: Dispatch<SetStateAction<string | null>>;
  status: string;
  setPreview: (f: PreviewFile) => void;
  aiAtt: (id: string) => any;
  inlineIds: Set<string>;
}) {
  // Documents (PDF/DOCX/EML) → top chip strip
  const docs = attachments.filter((a) => !inlineIds.has(a.attachment_id) && isDocAttachment(a));
  // Images attached normally (approval screenshots, standalone pictures).
  const attachedImages = attachments.filter((a) => !inlineIds.has(a.attachment_id) && isImageAttachment(a));
  const inlineImages = attachments.filter((a) => inlineIds.has(a.attachment_id) && isImageAttachment(a));

  // Dedupe — same file must not appear twice (attached + inline).
  const imageById = new Map<string, Attachment>();
  for (const a of [...attachedImages, ...inlineImages]) {
    imageById.set(a.attachment_id, a);
  }
  const allImages = [...imageById.values()];
  const inlineIdSet = new Set(inlineImages.map((a) => a.attachment_id));

  if (!docs.length && !allImages.length) return null;

  const imageProps = {
    providerId, selectedTimesheetIds, setSelectedTimesheetIds,
    approvalAttachmentId, setApprovalAttachmentId, status, setPreview, aiAtt,
  };

  const imageHint = (
    <>
      Tick <span className="font-semibold text-brand-600">Extract</span> on a pasted timesheet image to run it
      through extraction.
    </>
  );

  return (
    <div className="border-b border-slate-100 px-5 py-3 space-y-3">
      {docs.length > 0 && (
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-400">
            <Paperclip className="h-3 w-3" />
            {docs.length} attachment{docs.length !== 1 ? "s" : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            {docs.map((a) => (
              <div key={a.attachment_id}>
                <AttachmentChip
                  a={a}
                  providerId={providerId}
                  ai={ai}
                  selectedTimesheetIds={selectedTimesheetIds}
                  setSelectedTimesheetIds={setSelectedTimesheetIds}
                  status={status}
                  setPreview={setPreview}
                  aiAtt={aiAtt}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {allImages.length > 0 && (
        <CollapsibleAttachmentSection
          key={providerId}
          title={`${allImages.length} image${allImages.length !== 1 ? "s" : ""}`}
          icon={<ImageIcon className="h-3 w-3" />}
          defaultOpen={false}
          hint={inlineIdSet.size > 0 ? imageHint : undefined}
        >
          <div className="space-y-2">
            {attachedImages.length > 0 && inlineImages.length > 0 && (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Attached</p>
                <div className="flex flex-wrap gap-2">
                  {attachedImages.map((a) => (
                    <ImageCard key={a.attachment_id} a={a} {...imageProps} />
                  ))}
                </div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">In email body</p>
                <div className="flex flex-wrap gap-2">
                  {inlineImages.map((a) => (
                    <ImageCard key={a.attachment_id} a={a} {...imageProps} />
                  ))}
                </div>
              </>
            )}
            {(attachedImages.length === 0 || inlineImages.length === 0) && (
              <div className="flex flex-wrap gap-2">
                {allImages.map((a) => (
                  <ImageCard key={a.attachment_id} a={a} {...imageProps} />
                ))}
              </div>
            )}
          </div>
        </CollapsibleAttachmentSection>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

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
  const [aiRerunning, setAiRerunning] = useState(false);
  const [stagedQueue, setStagedQueue] = useState<PipelineFile[]>([]);

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

  const selectedItem = emails.find((e) => e.provider_message_id === selected);
  const aiRunning = (!!selected && loadingDetail && !selectedItem?.ai_checked) || aiRerunning;

  useEffect(() => {
    if (detail?.ai_check?.checked_at) {
      qc.invalidateQueries({ queryKey: ["inbox"] });
    }
  }, [detail?.ai_check?.checked_at, qc]);

  useEffect(() => {
    if (!detail) return;
    // Only real documents (pdf/docx/xlsx/eml) are ever auto-selected for
    // extraction — images, logos and approval screenshots never are.
    const docIds = new Set(detail.attachments.filter(isDocAttachment).map((a) => a.attachment_id));
    const sel = applyAiSelection(detail);
    if (detail.ai_check) {
      setSelectedTimesheetIds(new Set([...sel.timesheets].filter((id) => docIds.has(id))));
      setApprovalAttachmentId(sel.approval);
      setExtractBodyEnabled(!!detail.ai_check.extract_body);
    } else {
      const timesheetIds = detail.attachments
        .filter((a) => a.kind === "timesheet" && docIds.has(a.attachment_id))
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

  // Run Extraction → stage selected sources into the pipeline, then review &
  // accept each via the Compare & Fix overlay.
  const stage = useMutation({
    mutationFn: ({ id, selection }: { id: string; selection: IngestSelection }) =>
      stageExtraction(id, { attachment_ids: selection.attachment_ids, extract_body: selection.extract_body }),
    onSuccess: (files: PipelineFile[]) => {
      qc.invalidateQueries({ queryKey: ["pipeline"] });
      qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
      if (!files.length) {
        toast("warning", "Nothing to extract", "No timesheet could be extracted from the selection.");
        return;
      }
      setStagedQueue(files);
    },
    onError: (e: any) => toast("error", "Extraction failed", e?.response?.data?.detail ?? String(e)),
  });

  // Advance the review queue (after Accept/file or Reject/cancel).
  const advanceQueue = () => setStagedQueue((q) => q.slice(1));
  const onStagedSaved = () => {
    toast("success", "Record filed", "Saved to the pipeline and File Vault.");
    invalidate();
    advanceQueue();
  };

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

  const rerunAi = async () => {
    if (!selected) return;
    setAiRerunning(true);
    try {
      const data = await fetchEmail(selected, true);
      qc.setQueryData(["email", selected], data);
      qc.invalidateQueries({ queryKey: ["inbox"] });
    } catch (e: any) {
      toast("error", "AI rerun failed", e?.response?.data?.detail ?? String(e));
    } finally {
      setAiRerunning(false);
    }
  };

  const ai = detail?.ai_check;
  const aiAtt = (id: string) => ai?.attachments.find((a) => a.attachment_id === id);
  const isForwarded = isForwardedSubject(detail?.subject ?? null);

  // Inline-image attachments to hide from the chip list. The backend already
  // resolves cid: images to data URIs and reports which attachments it inlined;
  // we union that with a client-side scan as a fallback for any unresolved cids.
  const inlineIds = useMemo(() => {
    if (!detail) return new Set<string>();
    const ids = inlineAttachmentIds(detail.body_text ?? "", detail.body_html ?? null, detail.attachments);
    for (const id of detail.inline_attachment_ids ?? []) ids.add(id);
    return ids;
  }, [detail?.body_text, detail?.body_html, detail?.attachments, detail?.inline_attachment_ids]);

  // Full-screen email detail mode
  const [fullscreen, setFullscreen] = useState(false);

  return (
    <div className="flex h-full animate-fade-up flex-col">
     

      <div className={cn(
        "grid min-h-0 flex-1 gap-5",
        fullscreen
          ? "grid-cols-1"
          : "grid-cols-1 xl:grid-cols-[300px_1fr]",
      )}>
        {/* ── Left: email list (hidden in fullscreen) ───────────── */}
        <Card className={cn("flex min-h-0 flex-col", fullscreen && "hidden xl:hidden")}>
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
                    <span className="mt-1 flex flex-wrap items-center gap-2">
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

        {/* ── Right: email detail ───────────────────────────────── */}
        <Card className="flex min-h-0 flex-col">
          {!selected ? (
            <EmptyState
              icon={<Mail className="h-6 w-6" />}
              title="Select an email"
              detail="AI analysis runs automatically when emails arrive."
            />
          ) : loadingDetail || !detail ? (
            (() => {
              // While the email loads, the backend auto-runs the AI check for
              // sheets — surface that so the few-second wait is understood.
              const selItem = emails.find((e) => e.provider_message_id === selected);
              const needsAi = selItem && !selItem.ai_checked;
              return (
                <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
                  <Spinner className="h-6 w-6" />
                  {needsAi ? (
                    <div className="flex flex-col items-center gap-1.5 animate-fade-up">
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700 ring-1 ring-inset ring-brand-200">
                        <Brain className="h-3.5 w-3.5" /> AI analysis
                      </span>
                      <p className="text-sm font-medium text-slate-600">
                        Running first-time check — this might take a few seconds…
                      </p>
                    </div>
                  ) : (
                    <p className="text-sm text-slate-500">Loading email…</p>
                  )}
                </div>
              );
            })()
          ) : (
            <>
              {fullscreen ? (
                /* ── Fullscreen: minimal collapse bar only ─────── */
                <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-4 py-2">
                  <span className="truncate text-xs font-semibold text-slate-500">{detail.subject || "(no subject)"}</span>
                  <button
                    type="button"
                    title="Collapse"
                    onClick={() => setFullscreen(false)}
                    className="ml-3 flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  >
                    <ChevronRight className="h-3.5 w-3.5 rotate-180" /> Collapse
                  </button>
                </div>
              ) : (
                <>
                  {/* ── Email header — Outlook style ─────────────── */}
                  <div className="shrink-0 border-b border-slate-100 px-5 py-3">
                    <div className="flex items-start gap-3">
                      {/* Left: subject + meta */}
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 className="text-sm font-bold leading-snug text-slate-900">
                            {detail.subject || "(no subject)"}
                          </h2>
                          {isForwarded && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-600">
                              <Forward className="h-3 w-3" /> Forwarded
                            </span>
                          )}
                        </div>

                        {/* From / Date metadata */}
                        <div className="mt-1.5 space-y-0.5 text-xs text-slate-600">
                          <div className="flex gap-2">
                            <span className="w-10 shrink-0 font-semibold text-slate-400">From</span>
                            <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                              <span
                                className={cn(
                                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[8px] font-bold",
                                  avatarColor(detail.sender_name)
                                )}
                              >
                                {initials(detail.sender_name)}
                              </span>
                              <span className="font-medium text-slate-800">{detail.sender_name}</span>
                              {detail.sender_email && (
                                <span className="text-slate-400 truncate">&lt;{detail.sender_email}&gt;</span>
                              )}
                            </span>
                          </div>
                          <div className="flex gap-2">
                            <span className="w-10 shrink-0 font-semibold text-slate-400">Date</span>
                            <span>{formatDateTime(detail.received_at)}</span>
                          </div>
                          {ai?.matched_employee && (
                            <div className="flex gap-2">
                              <span className="w-10 shrink-0 font-semibold text-slate-400">Match</span>
                              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                                <UserCheck className="h-3 w-3" />
                                {ai.matched_employee.employee_name} · {ai.matched_employee.employee_id}
                                {ai.matched_employee.location ? ` · ${ai.matched_employee.location}` : ""}
                              </span>
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Right: status badge + expand + action buttons */}
                      <div className="flex shrink-0 flex-col items-end gap-2">
                        <div className="flex items-center gap-1.5">
                          {fetchingDetail && <Spinner className="h-3.5 w-3.5" />}
                          <StatusBadge status={detail.status} />
                          <button
                            type="button"
                            title="Expand to full screen"
                            onClick={() => setFullscreen(true)}
                            className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                          >
                            <Maximize2 className="h-3.5 w-3.5" />
                          </button>
                        </div>

                        {/* Action buttons */}
                        <div className="flex flex-wrap justify-end gap-1.5">
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={aiRerunning || loadingDetail}
                            onClick={rerunAi}
                            title="Re-run AI analysis on this email"
                          >
                            {aiRerunning ? <Spinner className="h-3 w-3" /> : <Brain className="h-3 w-3" />}
                            Rerun AI
                          </Button>
                          {detail.status === "new" && (
                            <>
                              <Button
                                size="sm"
                                variant="success"
                                disabled={stage.isPending || !canExtract}
                                onClick={() => stage.mutate({ id: detail.provider_message_id, selection: buildSelection() })}
                                title="Extract and review before filing"
                              >
                                {stage.isPending ? (
                                  <Spinner className="border-white/40 border-t-white h-3 w-3" />
                                ) : (
                                  <CheckCircle2 className="h-3 w-3" />
                                )}
                                Run Extraction ({extractCount})
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={decide.isPending}
                                onClick={() => decide.mutate({ id: detail.provider_message_id, accepted: false })}
                              >
                                <Archive className="h-3 w-3" /> Archive
                              </Button>
                            </>
                          )}
                          {detail.status === "archived" && (
                            <Button size="sm" variant="secondary" onClick={() => restore.mutate(detail.provider_message_id)}>
                              <Undo2 className="h-3 w-3" /> Restore
                            </Button>
                          )}
                          {detail.status === "ingested" && (
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled={rerun.isPending || !canExtract}
                              onClick={() =>
                                rerun.mutate({ id: detail.provider_message_id, selection: buildSelection() })
                              }
                            >
                              <RotateCcw className={cn("h-3 w-3", rerun.isPending && "animate-spin")} />
                              Re-run
                            </Button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* ── Attachments — Outlook chips, above body ──── */}
                  <AttachmentChips
                    attachments={detail.attachments}
                    providerId={detail.provider_message_id}
                    ai={ai}
                    selectedTimesheetIds={selectedTimesheetIds}
                    setSelectedTimesheetIds={setSelectedTimesheetIds}
                    approvalAttachmentId={approvalAttachmentId}
                    setApprovalAttachmentId={setApprovalAttachmentId}
                    status={detail.status}
                    setPreview={setPreview}
                    aiAtt={aiAtt}
                    inlineIds={inlineIds}
                  />

                  {/* ── AI analysis — collapsed by default ────────── */}
                  <AiAnalysisPanel ai={ai ?? null} aiRunning={aiRunning} />
                </>
              )}

              {/* ── Body ─────────────────────────────────────────── */}
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
                {/* Convert-body-to-image option (AI suggestion) */}
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
                          Timesheet table is in the message body — renders subject + body to JPEG for pipeline
                          extraction.
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

                {/* Email body — HTML iframe when available, plain-text fallback */}
                <EmailBodyRenderer
                  bodyText={detail.body_text}
                  bodyHtml={detail.body_html}
                  subject={detail.subject}
                  attachments={detail.attachments}
                  providerId={detail.provider_message_id}
                />
              </div>
            </>
          )}
        </Card>
      </div>

      <FilePreviewModal file={preview} onClose={() => setPreview(null)} />

      {/* Run Extraction → review each staged file in the Compare & Fix overlay:
          edit the extracted leaves, Accept (file record + vault) or Reject
          (leave it in the pipeline). */}
      <PipelineCompareFixModal
        file={stagedQueue[0] ?? null}
        onClose={advanceQueue}
        onSaved={onStagedSaved}
      />
    </div>
  );
}
