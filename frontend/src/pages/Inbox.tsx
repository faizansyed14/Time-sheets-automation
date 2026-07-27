import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Mail,
  Paperclip,
  BadgeCheck,
  Archive,
  CheckCircle2,
  FileText,
  FileX,
  FolderInput,
  Download,
  Eye,
  Search,
  Undo2,
  ChevronRight,
  ChevronDown,
  Forward,
  Maximize2,
  MoreVertical,
  Shield,
  Wand2,
  PlayCircle,
  AlertTriangle,
  ExternalLink,
} from "lucide-react";
import {
  attachmentRenderUrl,
  attachmentUrl,
  decideEmail,
  emlUrl,
  extractFullEmailStream,
  fetchAutoExtractStatus,
  fetchEmail,
  fetchEmployeeMatcher,
  fetchThread,
  fetchThreads,
  fetchLlmPreview,
  MONTHS_LONG,
  restoreEmail,
  saveEmlToVault,
  startAutoExtract,
  type Attachment,
  type Employee,
  type EmailDetail,
  type EmailListItem,
  type EmailRecipient,
  type LlmEgressPart,
  type LlmEgressPreview,
  type PipelineFile,
  type ThreadListItem,
} from "../api/client";
import { cn, formatBytes, formatDateTime, formatOutlookDateTime, emailSnippet, initials, avatarColor } from "../lib/utils";
import { isBodyJunkImage, isImageAttachment } from "../lib/attachmentFilters";
import { FilePreviewModal } from "../components/FilePreview";
import PipelineCompareFixModal from "../components/PipelineCompareFixModal";
import { ThreadSummaryBox } from "../components/ThreadSummaryBox";
import { attachmentRenderUrlIfSupported, buildEmailHtmlDocument, downloadFile } from "../lib/filePreview";
import { Badge, Button, Card, EmptyState, Modal, Select, Skeleton, Spinner } from "../components/ui";
import { useToast } from "../components/toast";
import { ExtractionActivityModal, useExtractionStream } from "../components/ExtractionActivity";
import { useDebounced, useSentinel } from "../lib/useInfinite";
import type { PreviewFile } from "../lib/filePreview";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: EmailListItem["status"] }) {
  if (status === "ingested")
    return (
      <Badge tone="success">
        <CheckCircle2 className="h-3 w-3" /> Ingested
      </Badge>
    );
  if (status === "archived")
    return (
      <Badge tone="slate">
        <Archive className="h-3 w-3" /> Archived
      </Badge>
    );
  return <Badge tone="brand">New</Badge>;
}

function ExtractedBadge({ at }: { at: string | null | undefined }) {
  if (!at) return null;
  return (
    <span title={`Extract Email last run ${formatDateTime(at)}`}>
      <Badge tone="success">
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
function LlmEgressPreviewModal({
  open,
  onClose,
  preview,
  loading,
  error,
}: {
  open: boolean;
  onClose: () => void;
  preview: LlmEgressPreview | null;
  loading: boolean;
  error: string | null;
}) {
  const [showPrompt, setShowPrompt] = useState(false);
  const [showBody, setShowBody] = useState(false);

  const PartList = ({ parts, tone }: { parts: LlmEgressPart[]; tone: string }) => (
    <div className="space-y-1">
      {parts.map((f) => (
        <div key={f.name + f.sha256} className={cn(
          "flex items-center gap-2 rounded-md border px-2 py-1.5 text-[11px]", tone)}>
          <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" />
          <span className="min-w-0 flex-1 truncate font-medium">{f.name}</span>
          <span className="shrink-0 opacity-70">{f.file_type}</span>
          <span className="shrink-0 opacity-70">{Math.round(f.bytes / 1024)} KB</span>
          <span className="shrink-0 font-mono opacity-50">{f.sha256}</span>
        </div>
      ))}
    </div>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="What is sent to OpenAI"
      subtitle="Built exactly the way a real Extract Email run builds it — this is what leaves, not a description of it."
      wide
    >
      {loading && (
        <div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-500">
          <Spinner /> Building the preview…
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}
      {preview && !loading && (
        <div className="space-y-4 text-sm">
          {/* Headline facts */}
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="brand">{preview.model}</Badge>
            <Badge tone={preview.pii_redaction ? "success" : "danger"}>
              Body redaction {preview.pii_redaction ? "on" : "OFF"}
            </Badge>
            <Badge tone="slate">
              {preview.call_count.inference} model call
              {preview.call_count.file_uploads > 0
                ? ` + ${preview.call_count.file_uploads} file upload(s)`
                : ""}
            </Badge>
            <Badge tone="slate">{preview.thread_messages.length} message(s) in thread</Badge>
          </div>

          {(preview.warnings?.length ?? 0) > 0 && (
            <div className="space-y-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
              <p className="flex items-center gap-1.5 font-semibold">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                This conversation was not sent in full
              </p>
              {preview.warnings!.map((w, i) => (
                <p key={i} className="leading-5">{w}</p>
              ))}
            </div>
          )}

          {/* The steps, in order */}
          <ol className="space-y-2">
            {preview.steps.map((s) => (
              <li key={s.n} className="flex gap-2.5 rounded-lg border border-slate-200 bg-white p-2.5">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-600 text-[10px] font-bold text-white">
                  {s.n}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-semibold text-slate-800">{s.title}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-600">{s.detail}</span>
                  {s.items.length > 0 && (
                    <ul className="mt-1.5 space-y-0.5">
                      {s.items.map((it, i) => (
                        <li key={i} className="truncate font-mono text-[10px] text-slate-500">
                          {it}
                        </li>
                      ))}
                    </ul>
                  )}
                </span>
              </li>
            ))}
          </ol>

          {/* The exact parts attached */}
          {preview.files_sent.length > 0 && (
            <div>
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                Files uploaded — raw bytes, NOT redacted
              </p>
              <PartList parts={preview.files_sent} tone="border-amber-200 bg-amber-50 text-amber-800" />
            </div>
          )}
          {preview.images_sent.length > 0 && (
            <div>
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                Images inlined (base64)
              </p>
              <PartList parts={preview.images_sent} tone="border-slate-200 bg-slate-50 text-slate-700" />
              <div className="mt-2 flex flex-wrap gap-2">
                {preview.images_sent.filter((i) => i.jpeg_b64).map((i) => (
                  <img
                    key={i.sha256}
                    src={`data:image/jpeg;base64,${i.jpeg_b64}`}
                    alt={i.name}
                    className="max-h-40 rounded-md border border-slate-200"
                  />
                ))}
              </div>
            </div>
          )}
          {/* The redaction boundary, stated both ways on purpose */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-2.5">
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-emerald-700">
                Redacted before sending
              </p>
              <ul className="list-inside list-disc space-y-0.5 text-[11px] text-slate-700">
                {preview.redacted.map((o) => <li key={o}>{o}</li>)}
              </ul>
            </div>
            <div className="rounded-lg border border-amber-200 bg-amber-50/50 p-2.5">
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-amber-700">
                NOT redacted
              </p>
              <ul className="list-inside list-disc space-y-0.5 text-[11px] text-slate-700">
                {preview.not_redacted.map((o) => <li key={o}>{o}</li>)}
              </ul>
            </div>
          </div>

          <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">
            {preview.policy}
          </p>

          {/* Verbatim payloads, collapsed by default */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => setShowBody((v) => !v)}
              className="flex w-full items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              {showBody ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              Scrubbed message text ({preview.body_sent.length} chars)
            </button>
            {showBody && (
              <pre className="max-h-72 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-[11px] text-slate-800 whitespace-pre-wrap">
                {preview.body_sent || "(no body text)"}
              </pre>
            )}

            <button
              type="button"
              onClick={() => setShowPrompt((v) => !v)}
              className="flex w-full items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              {showPrompt ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              The exact prompt sent
            </button>
            {showPrompt && (
              <>
                <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">System</p>
                <pre className="max-h-40 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-[11px] text-slate-800 whitespace-pre-wrap">
                  {preview.system_prompt}
                </pre>
                <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">User</p>
                <pre className="max-h-96 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-[11px] text-slate-800 whitespace-pre-wrap">
                  {preview.user_prompt}
                </pre>
              </>
            )}
          </div>
        </div>
      )}
    </Modal>
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
  const [employeeQuery, setEmployeeQuery] = useState("");
  const [employee, setEmployee] = useState<Employee | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const now = new Date();
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [year, setYear] = useState(now.getFullYear());
  const [saving, setSaving] = useState(false);

  const { data: employees } = useQuery({
    queryKey: ["employee-matcher"],
    queryFn: fetchEmployeeMatcher,
    enabled: !!emailId,
  });

  const matches = useMemo(() => {
    const q = employeeQuery.trim().toLowerCase();
    const list = employees ?? [];
    if (!q) return list.slice(0, 25);
    return list.filter((e) =>
      e.name.toLowerCase().includes(q)
      || e.employee_id.toLowerCase().includes(q)
      || (e.location ?? "").toLowerCase().includes(q)
      || (e.account_manager ?? "").toLowerCase().includes(q)
    ).slice(0, 25);
  }, [employees, employeeQuery]);

  useEffect(() => {
    if (!pickerOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [pickerOpen]);

  useEffect(() => {   // reset when opened for a new email
    setEmployee(null); setEmployeeQuery(""); setPickerOpen(false); setSaving(false);
  }, [emailId]);

  const years = [year + 1, year, year - 1, year - 2].filter((v, i, a) => a.indexOf(v) === i);

  const save = async () => {
    if (!emailId || !employee?.account_manager || !employee?.name) return;
    setSaving(true);
    try {
      const manager = employee.account_manager;
      const employeeName = employee.name;
      const res = await saveEmlToVault(emailId, { manager, employee: employeeName, month, year });
      toast("success", "Saved to File Vault", `${res.filename} → ${manager} / ${employeeName} / ${MONTHS_LONG[month]} ${year}`);
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
        <div className="block" ref={pickerRef}>
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Employee</span>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input
              value={employeeQuery}
              onChange={(e) => {
                setEmployeeQuery(e.target.value);
                setEmployee(null);
                setPickerOpen(true);
              }}
              onFocus={() => setPickerOpen(true)}
              placeholder="Search employee name or ID…"
              className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:outline-none"
            />
            {pickerOpen && (
              <div className="absolute z-30 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-pop">
                {matches.length === 0 ? (
                  <p className="px-3 py-3 text-sm text-slate-400">No employees match.</p>
                ) : (
                  matches.map((e) => (
                    <button
                      key={e.id}
                      type="button"
                      onMouseDown={(ev) => ev.preventDefault()}
                      onClick={() => {
                        setEmployee(e);
                        setEmployeeQuery(`${e.name} (${e.employee_id})`);
                        setPickerOpen(false);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-brand-50"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-slate-800">{e.name}</span>
                        <span className="block truncate text-xs text-slate-500">
                          {e.employee_id}{e.location ? ` · ${e.location}` : ""}{e.account_manager ? ` · ${e.account_manager}` : ""}
                        </span>
                      </span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Manager</span>
          <input
            value={employee?.account_manager ?? ""}
            readOnly
            placeholder="Auto-filled from employee"
            className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 px-3 text-sm text-slate-700 placeholder:text-slate-400"
          />
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
          <Button onClick={save} disabled={!employee?.account_manager || !employee?.name || saving}>
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
const UUID_RE =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

function normCid(value: string | null | undefined): string {
  return (value ?? "").replace(/^<|>$/g, "").toLowerCase();
}

function uuids(value: string): Set<string> {
  const out = new Set<string>();
  for (const m of value.matchAll(UUID_RE)) out.add(m[0].toLowerCase());
  return out;
}

function cidRefMatches(
  cidRef: string,
  att: { cid?: string | null; filename: string },
): boolean {
  const refFull = normCid(cidRef);
  const refName = refFull.split("@")[0];
  const refUuids = uuids(refFull);
  const ac = normCid(att.cid);
  if (ac && (ac === refFull || ac === refName || refName.includes(ac) || ac.includes(refFull))) {
    return true;
  }
  const fn = att.filename.toLowerCase();
  if (fn && (fn === refName || fn.includes(refName) || refFull.includes(fn))) {
    return true;
  }
  const fnStem = fn.replace(/\.[^.]+$/, "");
  if (fnStem && (refFull.includes(fnStem) || refName.includes(fnStem))) {
    return true;
  }
  for (const blob of [ac, fn, refFull, refName]) {
    if (!blob) continue;
    const shared = [...refUuids].filter((u) => blob.includes(u));
    if (shared.length) return true;
  }
  return false;
}

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

/** Find attachment by CID ref (content-id, filename, or shared UUID). */
function findByCid(cidRef: string, attachments: Attachment[]): Attachment | undefined {
  return attachments.find((a) => cidRefMatches(cidRef, a));
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
          className="my-2 block max-h-48 max-w-full object-contain"
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
    const doc = buildEmailHtmlDocument(bodyHtml);
    const blob = URL.createObjectURL(new Blob([doc], { type: "text/html" }));
    setBlobUrl(blob);
    return () => URL.revokeObjectURL(blob);
  }, [bodyHtml]);

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

// Attachment type helpers — shared by thread view and legacy chips.
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

// ---------------------------------------------------------------------------
// Outlook-style helpers — recipients, attachment strip, thread message card
// ---------------------------------------------------------------------------

function formatRecipient(r: EmailRecipient): string {
  return r.name ? `${r.name} <${r.email}>` : r.email;
}

function RecipientRows({ to, cc }: { to: EmailRecipient[]; cc: EmailRecipient[] }) {
  if (!to.length && !cc.length) return null;
  return (
    <div className="mt-2 space-y-0.5 text-xs text-slate-600">
      {to.length > 0 && (
        <div className="flex gap-2">
          <span className="w-7 shrink-0 font-medium text-slate-500">To</span>
          <span className="min-w-0 break-words">{to.map(formatRecipient).join("; ")}</span>
        </div>
      )}
      {cc.length > 0 && (
        <div className="flex gap-2">
          <span className="w-7 shrink-0 font-medium text-slate-500">Cc</span>
          <span className="min-w-0 break-words">{cc.map(formatRecipient).join("; ")}</span>
        </div>
      )}
    </div>
  );
}

function visibleFileAttachments(attachments: Attachment[], inlineIds: string[]): Attachment[] {
  const images = attachments.filter(
    (a) => isImageAttachment(a) && !isBodyJunkImage(a, inlineIds));
  const docs = attachments.filter(isDocAttachment);
  return [...docs, ...images];
}

function attachmentPreviewFile(providerId: string, a: Attachment): PreviewFile {
  return {
    url: attachmentUrl(providerId, a.attachment_id),
    filename: a.filename,
    contentType: a.content_type,
    renderUrl: attachmentRenderUrlIfSupported(
      a.filename, a.content_type, attachmentRenderUrl(providerId, a.attachment_id)),
  };
}

/** Per-message extraction state within a thread.
 *
 *  Extract Email always sends the WHOLE conversation, so a message was part of
 *  a run exactly when it arrived before that run. A message that landed after
 *  it has never been read — which is precisely when the thread needs
 *  re-extracting. */
type MsgExtractState = "extracted" | "new" | null;

function messageExtractState(
  receivedAt: string | null | undefined,
  threadExtractedAt: string | null | undefined,
): MsgExtractState {
  if (!threadExtractedAt) return null;          // thread never extracted — no badge
  if (!receivedAt) return null;
  return new Date(receivedAt) <= new Date(threadExtractedAt) ? "extracted" : "new";
}

function MessageExtractedBadge({ state }: { state: MsgExtractState }) {
  if (!state) return null;
  const extracted = state === "extracted";
  return (
    <span
      title={extracted
        ? "This message was included in the last Extract Email run."
        : "Arrived after the last run — re-extract to include it."}
      className={cn(
        "mb-1 inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide",
        extracted
          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
          : "border-amber-200 bg-amber-50 text-amber-700")}
    >
      {extracted ? <CheckCircle2 className="h-2.5 w-2.5" /> : <Wand2 className="h-2.5 w-2.5" />}
      {extracted ? "Extracted" : "Not extracted"}
    </span>
  );
}

function OutlookAttachmentStrip({
  attachments,
  inlineIds,
  providerId,
  setPreview,
  extractedNames,
}: {
  attachments: Attachment[];
  inlineIds: string[];
  providerId: string;
  setPreview: (f: PreviewFile) => void;
  /** Filenames already read by a previous run. Undefined = never extracted,
   *  so no per-file badge is shown at all. */
  extractedNames?: string[];
}) {
  const files = visibleFileAttachments(attachments, inlineIds);
  const [showAll, setShowAll] = useState(files.length <= 2);
  if (!files.length) return null;

  const visible = showAll ? files : files.slice(0, 2);
  const hiddenCount = files.length - visible.length;
  const totalBytes = files.reduce((n, a) => n + (a.size ?? 0), 0);

  const downloadAll = () => {
    for (const a of files) {
      downloadFile(attachmentUrl(providerId, a.attachment_id), a.filename);
    }
  };

  return (
    <div className="border-b border-slate-100 px-4 py-3">
      <div className="flex flex-col gap-2">
        {visible.map((a) => (
            <div
              key={a.attachment_id}
              className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 hover:border-slate-300"
            >
              <button
                type="button"
                onClick={() => setPreview(attachmentPreviewFile(providerId, a))}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <FileText className="h-5 w-5 shrink-0 text-red-500" />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5">
                    <span className="min-w-0 truncate text-sm font-medium text-slate-800">
                      {a.filename}
                    </span>
                    {/* Extraction reuses attachments it has already read, so a
                        re-run only pays for what is genuinely new. */}
                    {extractedNames?.includes(a.filename) ? (
                      <span
                        title="Already read by Extract Email — a re-run reuses this result instead of reading it again."
                        className="shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-emerald-700"
                      >
                        Extracted
                      </span>
                    ) : extractedNames && extractedNames.length > 0 ? (
                      <span
                        title="Not read yet — the next Extract Email run will read this one."
                        className="shrink-0 rounded-full border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-brand-700"
                      >
                        New
                      </span>
                    ) : null}
                  </span>
                  {a.size != null && a.size > 0 && (
                    <span className="text-xs text-slate-500">{formatBytes(a.size)}</span>
                  )}
                </span>
              </button>
              <button
                type="button"
                title="Download"
                onClick={() => downloadFile(attachmentUrl(providerId, a.attachment_id), a.filename)}
                className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              >
                <Download className="h-4 w-4" />
              </button>
            </div>
          ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-brand-600">
        {!showAll && hiddenCount > 0 && (
          <button
            type="button"
            onClick={() => setShowAll(true)}
            className="inline-flex items-center gap-1 font-medium hover:underline"
          >
            <ChevronDown className="h-3.5 w-3.5" />
            Show all {files.length} attachments
            {totalBytes > 0 ? ` (${formatBytes(totalBytes)})` : ""}
          </button>
        )}
        {files.length > 1 && (
          <button
            type="button"
            onClick={downloadAll}
            className="inline-flex items-center gap-1 font-medium hover:underline"
          >
            <Download className="h-3.5 w-3.5" />
            Download all
          </button>
        )}
      </div>
    </div>
  );
}

function ThreadMessageCard({
  msg,
  open,
  onToggle,
  setPreview,
  threadExtractedAt,
}: {
  msg: EmailDetail;
  open: boolean;
  onToggle: () => void;
  setPreview: (f: PreviewFile) => void;
  /** When this conversation was last extracted — null if it never was. */
  threadExtractedAt?: string | null;
}) {
  const inlineIds = msg.inline_attachment_ids ?? [];
  const to = msg.to_recipients ?? [];
  const cc = msg.cc_recipients ?? [];
  const fileCount = visibleFileAttachments(msg.attachments, inlineIds).length;
  const extractState = messageExtractState(msg.received_at, threadExtractedAt);

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full gap-3 px-3 py-3 text-left transition-colors hover:bg-slate-50/80"
      >
        <div className="relative shrink-0">
          <span
            className={cn(
              "flex h-10 w-10 items-center justify-center rounded-full text-xs font-bold",
              avatarColor(msg.sender_name)
            )}
          >
            {initials(msg.sender_name)}
          </span>
          {open && (
            <span className="absolute -bottom-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full border border-white bg-slate-400 text-white shadow-sm">
              <ChevronDown className="h-2.5 w-2.5" />
            </span>
          )}
        </div>

        <span className="min-w-0 flex-1">
          {open ? (
            <span className="flex items-start justify-between gap-3">
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-slate-900">
                  {msg.sender_name}
                  {msg.sender_email && (
                    <span className="font-normal text-slate-500">
                      {" "}&lt;{msg.sender_email}&gt;
                    </span>
                  )}
                </span>
                <RecipientRows to={to} cc={cc} />
              </span>
              <span className="shrink-0 text-right">
                {fileCount > 0 && (
                  <Paperclip className="mb-1 ml-auto h-4 w-4 text-slate-400" aria-label="Has attachments" />
                )}
                <MessageExtractedBadge state={extractState} />
                <span className="block text-xs text-slate-500 whitespace-nowrap">
                  {formatOutlookDateTime(msg.received_at)}
                </span>
              </span>
            </span>
          ) : (
            <>
              <span className="flex items-start justify-between gap-2">
                <span className="truncate text-sm font-semibold text-slate-900">{msg.sender_name}</span>
                {fileCount > 0 && (
                  <Paperclip className="h-4 w-4 shrink-0 text-slate-400" aria-label="Has attachments" />
                )}
              </span>
              <span className="mt-0.5 block truncate text-sm text-slate-500">
                {emailSnippet(msg.body_text)}
              </span>
              <span className="mt-1 flex items-center justify-end gap-1.5">
                <MessageExtractedBadge state={extractState} />
                <span className="text-xs text-slate-400">
                  {formatOutlookDateTime(msg.received_at)}
                </span>
              </span>
            </>
          )}
        </span>
      </button>

      {open && (
        <div className="border-t border-slate-100">
          <OutlookAttachmentStrip
            attachments={msg.attachments}
            inlineIds={inlineIds}
            providerId={msg.provider_message_id}
            setPreview={setPreview}
            extractedNames={msg.extracted_filenames}
          />
          <div className="px-4 py-3">
            <EmailBodyRenderer
              bodyText={msg.body_text}
              bodyHtml={msg.body_html}
              subject={msg.subject}
              attachments={msg.attachments}
              providerId={msg.provider_message_id}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function InboxListAttachmentPreview({
  email,
}: {
  email: EmailListItem;
}) {
  const [expanded, setExpanded] = useState(false);
  const atts = email.attachments ?? [];
  if (!atts.length) return null;
  const shown = expanded ? atts : atts.slice(0, 2);
  const extra = atts.length - shown.length;
  return (
    <span className="mt-2 flex flex-wrap items-end gap-1.5">
      {shown.map((a) => {
        const url = attachmentUrl(email.provider_message_id, a.attachment_id);
        const isImg = isImageAttachment(a);
        return isImg ? (
          <span
            key={a.attachment_id}
            className="flex h-[44px] w-[96px] items-center justify-center overflow-hidden rounded border border-slate-200 bg-slate-50"
            title={a.filename}
          >
            <img src={url} alt={a.filename} className="max-h-full max-w-full object-contain" loading="lazy" />
          </span>
        ) : (
          <span
            key={a.attachment_id}
            className="inline-flex h-[44px] max-w-[122px] items-center gap-1.5 rounded border border-slate-200 bg-white px-2 text-[10px] text-slate-600"
            title={a.filename}
          >
            <FileText className="h-3.5 w-3.5 shrink-0 text-brand-500" />
            <span className="truncate font-medium">{a.filename}</span>
          </span>
        );
      })}
      {extra > 0 && (
        <span
          role="button"
          tabIndex={0}
          title={`Show ${extra} more attachment${extra !== 1 ? "s" : ""}`}
          onClick={(e) => { e.stopPropagation(); setExpanded(true); }}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); setExpanded(true); } }}
          className="inline-flex h-[44px] items-center rounded border border-slate-200 bg-white px-3 text-sm font-medium text-slate-500 hover:bg-slate-50 hover:text-brand-600 cursor-pointer"
        >
          +{extra} more
        </span>
      )}
      {expanded && atts.length > 2 && (
        <span
          role="button"
          tabIndex={0}
          onClick={(e) => { e.stopPropagation(); setExpanded(false); }}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); setExpanded(false); } }}
          className="inline-flex h-[44px] items-center rounded border border-slate-200 bg-white px-3 text-[11px] font-medium text-slate-400 hover:bg-slate-50 hover:text-slate-600 cursor-pointer"
        >
          Show less
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function InboxPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [stagedQueue, setStagedQueue] = useState<PipelineFile[]>([]);
  // Live Extract Email activity + the review queue to open once it closes.
  const extractRun = useExtractionStream();
  const [pendingReview, setPendingReview] = useState<PipelineFile[]>([]);
  const [llmPreviewOpen, setLlmPreviewOpen] = useState(false);
  const [llmPreview, setLlmPreview] = useState<LlmEgressPreview | null>(null);
  const [llmPreviewLoading, setLlmPreviewLoading] = useState(false);
  const [llmPreviewError, setLlmPreviewError] = useState<string | null>(null);

  // Bulk Extract Email — every thread, one at a time, in the background.
  // Live progress + Stop live in the app-wide AutoExtractWidget (Shell); this
  // page only needs to know whether a run is already active, to disable the
  // trigger and avoid starting a second overlapping one.
  const { data: autoExtractStatus } = useQuery({
    queryKey: ["auto-extract-status"],
    queryFn: fetchAutoExtractStatus,
    refetchInterval: (query) => {
      const s = query.state.data?.state;
      return s === "running" || s === "stopping" ? 2000 : 8000;
    },
  });
  const autoExtractRunning =
    autoExtractStatus?.state === "running" || autoExtractStatus?.state === "stopping";
  const autoExtractStart = useMutation({
    mutationFn: startAutoExtract,
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ["auto-extract-status"] });
      toast("info", "Auto Extract started", `Processing ${s.total} thread(s) in the background.`);
    },
    onError: (e: any) =>
      toast("error", "Couldn't start Auto Extract", e?.response?.data?.detail ?? String(e)),
  });

  useEffect(() => setPreview(null), [selected]);

  const openLlmPreview = async (msgId: string) => {
    setLlmPreviewOpen(true);
    setLlmPreview(null);
    setLlmPreviewError(null);
    setLlmPreviewLoading(true);
    try {
      // Same scope as the main Extract Email button (whole email).
      const data = await fetchLlmPreview(msgId);
      setLlmPreview(data);
    } catch (e: any) {
      setLlmPreviewError(e?.response?.data?.detail ?? String(e));
    } finally {
      setLlmPreviewLoading(false);
    }
  };

  const dq = useDebounced(q, 350);
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["inbox-threads", dq, status],
    queryFn: ({ pageParam }) => fetchThreads(dq, status, pageParam as number),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.items.length : undefined),
  });
  const emails: ThreadListItem[] = data?.pages.flatMap((p) => p.items) ?? [];
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

  // Outlook-style "see the full history": every OTHER message in this
  // email's conversation (the selected row is always the thread's newest —
  // see backend _to_list_item — so this is the earlier history below it).
  const { data: thread, isLoading: loadingThread } = useQuery({
    queryKey: ["email-thread", selected],
    queryFn: () => fetchThread(selected!),
    enabled: !!selected,
  });

  const threadMessages: EmailDetail[] = useMemo(() => {
    const msgs = thread?.messages?.length
      ? [...thread.messages]
      : detail
        ? [detail]
        : [];
    if (!detail || !msgs.length) return msgs;
    return msgs.map((m) =>
      m.provider_message_id === detail.provider_message_id ? detail : m);
  }, [thread, detail]);

  const [expandedThreadIds, setExpandedThreadIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (selected) setExpandedThreadIds(new Set([selected]));
  }, [selected]);

  const toggleThreadMessage = (id: string) => {
    setExpandedThreadIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  useEffect(() => {
    if (selected) {
      qc.invalidateQueries({ queryKey: ["email", selected] });
      qc.invalidateQueries({ queryKey: ["email-thread", selected] });
    }
  }, [selected, qc]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["email", selected] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
    qc.invalidateQueries({ queryKey: ["coverage"] });
  };

  const decide = useMutation({
    mutationFn: ({ id, accepted }: { id: string; accepted: boolean }) =>
      decideEmail(id, accepted),
    onSuccess: () => {
      toast("info", "Email archived");
      invalidate();
    },
    onError: (e: any) => toast("error", "Action failed", e?.response?.data?.detail ?? String(e)),
  });

  // Extract Email — whole .eml through the ONE extraction pipeline.
  const extractEmail = useMutation({
    // Streams live pipeline activity into the ExtractionActivityModal; the
    // resolved value is the same {staged, groups, message} payload as before.
    mutationFn: (id: string) =>
      extractRun.start((onEvent) => extractFullEmailStream(id, onEvent)) as Promise<{
        staged: PipelineFile[]; groups: number; message: string;
      }>,
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["email", selected] });
      qc.invalidateQueries({ queryKey: ["pipeline"] });
      qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
      // Auto-accepted items are already filed (status "success"); only the
      // held-for-review ones need Compare & Fix — opened when the user closes
      // the live activity panel.
      const review = (res.staged ?? []).filter((t) => t.status === "needs_review");
      setPendingReview(review);
    },
    onError: (e: any) => toast("error", "Extract Email failed", e?.message ?? String(e)),
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
            <button
              type="button"
              onClick={() => autoExtractStart.mutate()}
              disabled={autoExtractRunning || autoExtractStart.isPending}
              title="Extract every thread in the inbox, one at a time, in the background — watch progress and Stop from the widget bottom-right"
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1.5 text-xs font-semibold text-brand-700 transition-colors hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <PlayCircle className="h-3.5 w-3.5" />
              {autoExtractRunning ? "Running…" : "Auto Extract"}
            </button>
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
                      <span className="flex min-w-0 items-baseline gap-1.5">
                        <span className="truncate text-sm font-semibold text-slate-800">{m.sender_name}</span>
                        {m.thread_message_count > 1 && (
                          <span
                            title={`${m.thread_message_count} messages in this thread`}
                            className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500"
                          >
                            {m.thread_message_count}
                          </span>
                        )}
                      </span>
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
                    <InboxListAttachmentPreview email={m} />
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
                  {/* ── Email header — subject + actions ───────────── */}
                  <div className="shrink-0 border-b border-slate-100 px-5 py-3">
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 className="text-sm font-bold leading-snug text-slate-900">
                            {detail.subject || "(no subject)"}
                          </h2>
                          {isForwarded && (
                            <span className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-2 py-0.5 text-[10px] font-semibold text-brand-700 ring-1 ring-inset ring-brand-200">
                              <Forward className="h-3 w-3" /> Forwarded
                            </span>
                          )}
                          {threadMessages.length > 1 && (
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                              {threadMessages.length} messages
                            </span>
                          )}
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
                          {/* Extract Email reads the whole conversation, so the
                              badge is thread-level. This message still needs a
                              run if it arrived AFTER the last one — otherwise a
                              reply carrying the approval would never be read. */}
                          {(() => {
                            const at = detail.extract_email_at;
                            const isNewer = !!at && !!detail.received_at
                              && new Date(detail.received_at) > new Date(at);
                            if (at && !isNewer) return null;
                            return (
                              <Button
                                size="sm"
                                variant={isNewer ? "secondary" : undefined}
                                disabled={extractEmail.isPending || loadingDetail}
                                onClick={() => extractEmail.mutate(detail.provider_message_id)}
                                title={isNewer
                                  ? "This reply arrived after the last run — re-read the thread so its content (and any approval) is included."
                                  : "Whole conversation to the model in one call — every attachment, approval detected, grouped per employee/month for Compare & Fix"}
                              >
                                {extractEmail.isPending ? (
                                  <Spinner className="border-white/40 border-t-white h-3 w-3" />
                                ) : (
                                  <Wand2 className="h-3 w-3" />
                                )}
                                {extractEmail.isPending
                                  ? "Extracting…"
                                  : isNewer ? "Re-extract (new reply)" : "Extract Email"}
                              </Button>
                            );
                          })()}
                          {detail.extract_email_at && (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() =>
                                navigate(
                                  `/pipeline?thread_key=${encodeURIComponent(
                                    detail.thread_id || detail.provider_message_id
                                  )}`
                                )
                              }
                              title="Open the exact pipeline record(s) this conversation was staged into — not just the thread, the genuinely extracted result"
                            >
                              <ExternalLink className="h-3 w-3" />
                              View in Pipeline
                            </Button>
                          )}
                          <EmailMenu
                            busy={extractEmail.isPending || decide.isPending}
                            manualActions={[
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
                                label: "EML sent to LLM",
                                icon: Shield,
                                onClick: () => openLlmPreview(detail.provider_message_id),
                              },
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

                </>
              )}

              {/* ── Outlook-style conversation thread ─────────────── */}
              <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50/70 px-4 py-4">
                {loadingThread && !!detail.conversation_id && threadMessages.length <= 1 && (
                  <div className="mb-3 flex items-center gap-2 text-xs text-slate-400">
                    <Spinner className="h-3.5 w-3.5" /> Loading conversation…
                  </div>
                )}
                {thread?.summary && <ThreadSummaryBox summary={thread.summary} />}
                <div className="space-y-2">
                  {threadMessages.map((m) => (
                    <ThreadMessageCard
                      key={m.provider_message_id}
                      msg={m}
                      open={expandedThreadIds.has(m.provider_message_id)}
                      onToggle={() => toggleThreadMessage(m.provider_message_id)}
                      setPreview={setPreview}
                      threadExtractedAt={thread?.extracted_at}
                    />
                  ))}
                </div>
              </div>
            </>
          )}
        </Card>
      </div>

      <FilePreviewModal file={preview} onClose={() => setPreview(null)} />

      {/* Live Extract Email activity — stages, LLM-call count, auto-accept
          outcome. On close, open Compare & Fix for any held-for-review items. */}
      <ExtractionActivityModal
        run={extractRun}
        title="Extract Email"
        onDone={() => {
          if (pendingReview.length) {
            setStagedQueue(pendingReview);
            setPendingReview([]);
          } else {
            toast("success", "Extract Email complete",
              "No items need review — see the Activity log for AI recommendations.");
          }
        }}
      />

      <SaveEmlToVaultModal
        emailId={vaultEmailId}
        subject={detail?.subject ?? null}
        onClose={() => setVaultEmailId(null)}
      />
      <LlmEgressPreviewModal
        open={llmPreviewOpen}
        onClose={() => setLlmPreviewOpen(false)}
        preview={llmPreview}
        loading={llmPreviewLoading}
        error={llmPreviewError}
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
