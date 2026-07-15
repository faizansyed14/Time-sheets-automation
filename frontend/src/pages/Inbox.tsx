import { useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Mail,
  Paperclip,
  BadgeCheck,
  Archive,
  CheckCircle2,
  RotateCcw,
  FileText,
  FileX,
  FolderInput,
  Download,
  Eye,
  Search,
  Undo2,
  ChevronRight,
  Forward,
  Maximize2,
  MoreVertical,
  Wand2,
} from "lucide-react";
import {
  attachmentRenderUrl,
  attachmentUrl,
  decideEmail,
  emlUrl,
  extractFullEmail,
  fetchEmail,
  fetchEmployeeMatcher,
  fetchInbox,
  MONTHS_LONG,
  rerunExtraction,
  restoreEmail,
  saveEmlToVault,
  stageExtraction,
  type Attachment,
  type EmailListItem,
  type IngestSelection,
  type PipelineFile,
} from "../api/client";
import { cn, formatDateTime, initials, avatarColor } from "../lib/utils";
import { isBodyJunkImage, isImageAttachment } from "../lib/attachmentFilters";
import { FilePreviewModal } from "../components/FilePreview";
import PipelineCompareFixModal from "../components/PipelineCompareFixModal";
import { downloadFile, sanitizeEmailHtml } from "../lib/filePreview";
import { Badge, Button, Card, EmptyState, Modal, Select, Skeleton, Spinner } from "../components/ui";
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

function ExtractedBadge({ at }: { at: string | null | undefined }) {
  if (!at) return null;
  return (
    <span title={`Extract Email last run ${formatDateTime(at)}`}>
      <Badge tone="green">
        <Wand2 className="h-3 w-3" /> Extracted
      </Badge>
    </span>
  );
}

// Persisted so this email is never re-processed by hand just to rediscover
// the same empty result — see EmailMessage.no_sheets_found_at.
function NoSheetsBadge({ at, note }: { at: string | null | undefined; note: string | null | undefined }) {
  if (!at) return null;
  return (
    <span title={`Extract Email found nothing to stage ${formatDateTime(at)}${note ? ` — ${note}` : ""}`}>
      <Badge tone="slate">
        <FileX className="h-3 w-3" /> No sheets found
      </Badge>
    </span>
  );
}

// ---------------------------------------------------------------------------
// 3-dot email menu — everything around the full .eml export
// ---------------------------------------------------------------------------

type MenuAction = { label: string; icon: typeof Eye; onClick: () => void };

function EmailMenu({
  manualActions,
  emlActions,
  busy,
}: {
  manualActions: MenuAction[];
  emlActions: MenuAction[];
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const item = "flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-slate-700 hover:bg-brand-50/60";
  const act = (fn: () => void) => () => { setOpen(false); fn(); };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        title="More actions"
        onClick={() => setOpen((v) => !v)}
        className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 shadow-xs hover:bg-slate-50"
      >
        {busy ? <Spinner className="h-4 w-4" /> : <MoreVertical className="h-4 w-4" />}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-64 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-pop animate-scale-in">
          {manualActions.length > 0 && (
            <>
              <p className="px-3 pb-1 pt-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                Manual tools
              </p>
              {manualActions.map((a) => (
                <button key={a.label} type="button" className={item} onClick={act(a.onClick)}>
                  <a.icon className="h-3.5 w-3.5 text-slate-400" /> {a.label}
                </button>
              ))}
              <div className="my-1 border-t border-slate-100" />
            </>
          )}
          <p className="px-3 pb-1 pt-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
            Full email (.eml — includes every attachment)
          </p>
          {emlActions.map((a) => (
            <button key={a.label} type="button" className={item} onClick={act(a.onClick)}>
              <a.icon className="h-3.5 w-3.5 text-slate-400" /> {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Vault picker — choose Manager / Employee / Month for the .eml.
function SaveEmlToVaultModal({
  emailId,
  subject,
  onClose,
}: {
  emailId: string | null;
  subject: string | null;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const [manager, setManager] = useState("");
  const [employee, setEmployee] = useState("");
  const now = new Date();
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [year, setYear] = useState(now.getFullYear());
  const [saving, setSaving] = useState(false);

  const { data: employees } = useQuery({
    queryKey: ["employee-matcher"],
    queryFn: fetchEmployeeMatcher,
    enabled: !!emailId,
  });

  const managers = useMemo(
    () => [...new Set((employees ?? []).map((e) => e.account_manager).filter(Boolean))].sort() as string[],
    [employees]
  );
  const empOptions = useMemo(
    () => (employees ?? [])
      .filter((e) => !manager || e.account_manager === manager)
      .map((e) => e.name)
      .sort(),
    [employees, manager]
  );

  useEffect(() => {   // reset when opened for a new email
    setManager(""); setEmployee(""); setSaving(false);
  }, [emailId]);

  const years = [year + 1, year, year - 1, year - 2].filter((v, i, a) => a.indexOf(v) === i);

  const save = async () => {
    if (!emailId || !manager || !employee) return;
    setSaving(true);
    try {
      const res = await saveEmlToVault(emailId, { manager, employee, month, year });
      toast("success", "Saved to File Vault", `${res.filename} → ${manager} / ${employee} / ${MONTHS_LONG[month]} ${year}`);
      onClose();
    } catch (e: any) {
      toast("error", "Could not save", e?.response?.data?.detail ?? String(e));
      setSaving(false);
    }
  };

  return (
    <Modal open={!!emailId} onClose={onClose} title="Save .eml to File Vault"
      subtitle={subject ? `“${subject}” — pick where to file the full email.` : undefined}>
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Manager</span>
          <Select value={manager} onChange={(e) => { setManager(e.target.value); setEmployee(""); }} className="w-full">
            <option value="">Select manager…</option>
            {managers.map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Employee</span>
          <Select value={employee} onChange={(e) => setEmployee(e.target.value)} className="w-full" disabled={!manager}>
            <option value="">Select employee…</option>
            {empOptions.map((n) => <option key={n} value={n}>{n}</option>)}
          </Select>
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Month</span>
            <Select value={String(month)} onChange={(e) => setMonth(Number(e.target.value))} className="w-full">
              {MONTHS_LONG.map((m, i) => (i === 0 ? null : <option key={i} value={i}>{m}</option>))}
            </Select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Year</span>
            <Select value={String(year)} onChange={(e) => setYear(Number(e.target.value))} className="w-full">
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </Select>
          </label>
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-3">
          <Button variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={save} disabled={!manager || !employee || saving}>
            {saving ? <Spinner className="border-white/40 border-t-white h-4 w-4" /> : <FolderInput className="h-4 w-4" />}
            Save to Vault
          </Button>
        </div>
      </div>
    </Modal>
  );
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
  return html.replace(/cid:([^"'\s>)]+)/gi, (match, cidRef: string) => {
    const att = findByCid(cidRef, attachments);
    return att ? attachmentUrl(providerId, att.attachment_id) : match;
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
      `img{max-width:min(100%,520px);max-height:420px;height:auto;object-fit:contain;display:block;margin:6px 0}` +
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
// Outlook-style attachment chips rendered at the top of the email
// ---------------------------------------------------------------------------

// Attachment chips: image preview cards + document chips (pdf/docx/xlsx/eml).
const DOC_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "message/rfc822",
]);
const DOC_EXTS = new Set(["pdf", "docx", "xlsx", "eml"]);

function isDocAttachment(a: Attachment): boolean {
  if (a.is_inline) return false;
  if ((a.content_type || "").toLowerCase().startsWith("image/")) return false;
  if (DOC_TYPES.has(a.content_type)) return true;
  const ext = a.filename.split(".").pop()?.toLowerCase() ?? "";
  return DOC_EXTS.has(ext);
}


function ImageAttachmentCard({
  a,
  providerId,
  selectedTimesheetIds,
  setSelectedTimesheetIds,
  status,
  setPreview,
}: {
  a: Attachment;
  providerId: string;
  selectedTimesheetIds: Set<string>;
  setSelectedTimesheetIds: Dispatch<SetStateAction<Set<string>>>;
  status: string;
  setPreview: (f: PreviewFile) => void;
}) {
  const url = attachmentUrl(providerId, a.attachment_id);
  const timesheetChecked = selectedTimesheetIds.has(a.attachment_id);
  const canExtract = (status === "new" || status === "ingested")
    && (a.kind === "timesheet" || a.kind === "approval_screenshot");

  return (
    <div className="flex w-[148px] flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setPreview({ url, filename: a.filename, contentType: a.content_type })}
        className="group relative flex h-[108px] items-center justify-center bg-slate-50 p-1"
      >
        <img
          src={url}
          alt={a.filename}
          className="max-h-full max-w-full object-contain"
          loading="lazy"
        />
        <span className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition group-hover:bg-black/10 group-hover:opacity-100">
          <Maximize2 className="h-5 w-5 text-white drop-shadow" />
        </span>
      </button>
      <div className="border-t border-slate-100 px-2 py-1.5">
        <p className="truncate text-[11px] font-medium text-slate-700" title={a.filename}>
          {a.filename}
        </p>
        {canExtract && (
          <label className="mt-1 flex cursor-pointer items-center gap-1 text-[10px] font-semibold text-slate-500">
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
      </div>
    </div>
  );
}

function AttachmentChip({
  a,
  providerId,
  selectedTimesheetIds,
  setSelectedTimesheetIds,
  status,
  setPreview,
}: {
  a: Attachment;
  providerId: string;
  selectedTimesheetIds: Set<string>;
  setSelectedTimesheetIds: Dispatch<SetStateAction<Set<string>>>;
  status: string;
  setPreview: (f: PreviewFile) => void;
}) {
  const timesheetChecked = selectedTimesheetIds.has(a.attachment_id);
  const canExtract = status === "new" || status === "ingested";

  const chipColor = timesheetChecked
    ? "border-brand-300 bg-brand-50 ring-1 ring-brand-100"
    : "border-slate-200 bg-white hover:border-brand-200";

  return (
    <div className={cn("flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs transition-colors", chipColor)}>
      <FileText className="h-4 w-4 shrink-0 text-brand-500" />
      <span className="min-w-0">
        <span className="block max-w-[200px] truncate font-medium text-slate-700">{a.filename}</span>
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
        onClick={() => setPreview({
          url: attachmentUrl(providerId, a.attachment_id),
          filename: a.filename,
          contentType: a.content_type,
          // DOCX/XLSX preview = server-rendered page images (works everywhere).
          renderUrl: attachmentRenderUrl(providerId, a.attachment_id),
        })}
        className="ml-1 shrink-0 text-[10px] font-semibold uppercase tracking-wide text-brand-500 hover:text-brand-700"
      >
        Preview
      </button>
    </div>
  );
}

function AttachmentChips({
  attachments,
  inlineIds,
  providerId,
  selectedTimesheetIds,
  setSelectedTimesheetIds,
  status,
  setPreview,
}: {
  attachments: Attachment[];
  inlineIds: string[];
  providerId: string;
  selectedTimesheetIds: Set<string>;
  setSelectedTimesheetIds: Dispatch<SetStateAction<Set<string>>>;
  status: string;
  setPreview: (f: PreviewFile) => void;
}) {
  const images = attachments.filter(
    (a) => isImageAttachment(a) && !isBodyJunkImage(a, inlineIds));
  const docs = attachments.filter(isDocAttachment);
  const total = images.length + docs.length;

  if (!total) return null;

  return (
    <div className="border-b border-slate-100 px-5 py-3">
      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-400">
        <Paperclip className="h-3 w-3" />
        {total} attachment{total !== 1 ? "s" : ""}
      </p>
      {images.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {images.map((a) => (
            <ImageAttachmentCard
              key={a.attachment_id}
              a={a}
              providerId={providerId}
              selectedTimesheetIds={selectedTimesheetIds}
              setSelectedTimesheetIds={setSelectedTimesheetIds}
              status={status}
              setPreview={setPreview}
            />
          ))}
        </div>
      )}
      {docs.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {docs.map((a) => (
            <div key={a.attachment_id}>
              <AttachmentChip
                a={a}
                providerId={providerId}
                selectedTimesheetIds={selectedTimesheetIds}
                setSelectedTimesheetIds={setSelectedTimesheetIds}
                status={status}
                setPreview={setPreview}
              />
            </div>
          ))}
        </div>
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
  const [extractBodyEnabled, setExtractBodyEnabled] = useState(false);
  const [stagedQueue, setStagedQueue] = useState<PipelineFile[]>([]);

  useEffect(() => setPreview(null), [selected]);
  useEffect(() => {
    setExtractBodyEnabled(false);
    setSelectedTimesheetIds(new Set());
  }, [selected]);

  const buildSelection = (): IngestSelection => ({
    attachment_ids: [...selectedTimesheetIds],
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
  const inboxTotal = data?.pages[0]?.total ?? 0;

  const [scrollRoot, setScrollRoot] = useState<HTMLDivElement | null>(null);
  const sentinelRef = useSentinel(
    () => hasNextPage && !isFetchingNextPage && fetchNextPage(),
    !!hasNextPage,
    scrollRoot
  );

  const { data: detail, isLoading: loadingDetail, isFetching: fetchingDetail } = useQuery({
    queryKey: ["email", selected],
    queryFn: () => fetchEmail(selected!),
    enabled: !!selected,
  });

  useEffect(() => {
    if (!detail) return;
    // Default selection: timesheet-classified docs and image attachments —
    // never body-embedded signature/logo images.
    const inlineIds = detail.inline_attachment_ids ?? [];
    const timesheetIds = detail.attachments
      .filter((a) => a.kind === "timesheet" && (isDocAttachment(a) || isImageAttachment(a))
        && !isBodyJunkImage(a, inlineIds))
      .map((a) => a.attachment_id);
    setSelectedTimesheetIds(new Set(timesheetIds));
    setExtractBodyEnabled(false);
  }, [detail?.provider_message_id]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["email", selected] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
    qc.invalidateQueries({ queryKey: ["coverage"] });
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

  // Extract Email — whole .eml to vision, grouped per employee/month.
  const extractEmail = useMutation({
    mutationFn: (id: string) => extractFullEmail(id),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["email", selected] });
      qc.invalidateQueries({ queryKey: ["pipeline"] });
      qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
      if (!res.staged.length) {
        toast("warning", "Nothing to review", res.message);
        return;
      }
      toast("success",
        res.groups === 1 ? "1 item ready to review" : `${res.groups} items ready to review`,
        res.message);
      setStagedQueue(res.staged);
    },
    onError: (e: any) => toast("error", "Extract Email failed", e?.response?.data?.detail ?? String(e)),
  });

  const [vaultEmailId, setVaultEmailId] = useState<string | null>(null);

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

  const isForwarded = isForwardedSubject(detail?.subject ?? null);

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
                placeholder="Search sender name or email…"
                className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none"
              />
            </div>
            <Select value={status} onChange={(e) => setStatus(e.target.value)} className="py-1.5 text-xs">
              <option value="">All</option>
              <option value="new">New</option>
              <option value="extracted">Extracted</option>
              <option value="no_sheets">No sheets found</option>
              <option value="ingested">Ingested</option>
              <option value="archived">Archived</option>
            </Select>
          </div>
          {inboxTotal > 0 && (
            <p className="border-b border-slate-100 px-4 py-1.5 text-[10px] text-slate-400">
              Showing {emails.length} of {inboxTotal}
              {hasNextPage ? " — scroll for more" : ""}
            </p>
          )}
          <div ref={setScrollRoot} className="min-h-0 flex-1 overflow-y-auto">
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
                      <ExtractedBadge at={m.extract_email_at} />
                      <NoSheetsBadge at={m.no_sheets_found_at} note={m.no_sheets_note} />
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
              detail="Open an email and click “Extract Email” to process it."
            />
          ) : loadingDetail || !detail ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
              <Spinner className="h-6 w-6" />
              <p className="text-sm text-slate-500">Loading email…</p>
            </div>
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
                        </div>
                      </div>

                      {/* Right: status badge + expand + action buttons */}
                      <div className="flex shrink-0 flex-col items-end gap-2">
                        <div className="flex items-center gap-1.5">
                          {fetchingDetail && <Spinner className="h-3.5 w-3.5" />}
                          <StatusBadge status={detail.status} />
                          <ExtractedBadge at={detail.extract_email_at} />
                          <NoSheetsBadge at={detail.no_sheets_found_at} note={detail.no_sheets_note} />
                          <button
                            type="button"
                            title="Expand to full screen"
                            onClick={() => setFullscreen(true)}
                            className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                          >
                            <Maximize2 className="h-3.5 w-3.5" />
                          </button>
                        </div>

                        <div className="flex flex-wrap justify-end gap-1.5">
                          <Button
                            size="sm"
                            disabled={extractEmail.isPending || loadingDetail}
                            onClick={() => extractEmail.mutate(detail.provider_message_id)}
                            title={detail.extract_email_at
                              ? "Re-run Extract Email — replaces staged review items from the last run"
                              : "Whole .eml to vision — all attachments, approvals detected, grouped per employee/month for Compare & Fix"}
                          >
                            {extractEmail.isPending ? (
                              <Spinner className="border-white/40 border-t-white h-3 w-3" />
                            ) : (
                              <Wand2 className="h-3 w-3" />
                            )}
                            {extractEmail.isPending
                              ? "Extracting…"
                              : detail.extract_email_at
                                ? "Re-extract"
                                : "Extract Email"}
                          </Button>
                          <EmailMenu
                            busy={stage.isPending || rerun.isPending || decide.isPending}
                            manualActions={[
                              ...(detail.status === "new" && canExtract ? [{
                                label: `Run Extraction (${extractCount} selected)`,
                                icon: CheckCircle2,
                                onClick: () => stage.mutate({ id: detail.provider_message_id, selection: buildSelection() }),
                              }] : []),
                              ...(detail.status === "ingested" && canExtract ? [{
                                label: "Re-run extraction",
                                icon: RotateCcw,
                                onClick: () => rerun.mutate({ id: detail.provider_message_id, selection: buildSelection() }),
                              }] : []),
                              ...(detail.status === "new" ? [{
                                label: "Archive email",
                                icon: Archive,
                                onClick: () => decide.mutate({ id: detail.provider_message_id, accepted: false }),
                              }] : []),
                              ...(detail.status === "archived" ? [{
                                label: "Restore to inbox",
                                icon: Undo2,
                                onClick: () => restore.mutate(detail.provider_message_id),
                              }] : []),
                            ]}
                            emlActions={[
                              {
                                label: "Preview .eml",
                                icon: Eye,
                                onClick: () => setPreview({
                                  url: emlUrl(detail.provider_message_id),
                                  filename: `${detail.subject || "email"}.eml`,
                                  contentType: "message/rfc822",
                                }),
                              },
                              {
                                label: "Download .eml",
                                icon: Download,
                                onClick: () => downloadFile(emlUrl(detail.provider_message_id), `${detail.subject || "email"}.eml`),
                              },
                              {
                                label: "Save .eml to File Vault…",
                                icon: FolderInput,
                                onClick: () => setVaultEmailId(detail.provider_message_id),
                              },
                            ]}
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* ── Attachments — Outlook chips, above body ──── */}
                  <AttachmentChips
                    attachments={detail.attachments}
                    inlineIds={detail.inline_attachment_ids ?? []}
                    providerId={detail.provider_message_id}
                    selectedTimesheetIds={selectedTimesheetIds}
                    setSelectedTimesheetIds={setSelectedTimesheetIds}
                    status={detail.status}
                    setPreview={setPreview}
                  />

                </>
              )}

              {/* ── Body ─────────────────────────────────────────── */}
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
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

      <SaveEmlToVaultModal
        emailId={vaultEmailId}
        subject={detail?.subject ?? null}
        onClose={() => setVaultEmailId(null)}
      />

      {/* Run Extraction → review each staged file in the Compare & Fix overlay:
          edit the extracted leaves, Accept (file record + vault) or Delete. */}
      <PipelineCompareFixModal
        file={stagedQueue[0] ?? null}
        onClose={advanceQueue}
        onSaved={onStagedSaved}
        onDiscarded={invalidate}
      />
    </div>
  );
}
