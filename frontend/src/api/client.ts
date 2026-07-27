import axios from "axios";
import { b64ToBytes } from "../lib/filePreview";

export const api = axios.create({ baseURL: "/api/v1" });

// ---------------------------------------------------------------------------
// Auth token + device fingerprint
// ---------------------------------------------------------------------------
const TOKEN_KEY = "ts_token";
const FP_KEY = "ts_fp";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}
export function deviceFingerprint(): string {
  let fp = localStorage.getItem(FP_KEY);
  if (!fp) {
    fp = (crypto.randomUUID?.() ?? Math.random().toString(36).slice(2)) + "-" + (navigator.language || "");
    localStorage.setItem(FP_KEY, fp);
  }
  return fp;
}

/** Append the access token as a query param. Used for URLs the BROWSER loads
 *  directly (PDF/image previews, file downloads) where headers can't be set. */
export function withAuthParam(url: string): string {
  const t = getToken();
  if (!t) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

// Attach the bearer token + fingerprint to every request.
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  config.headers["X-Fingerprint"] = deviceFingerprint();
  return config;
});

// On 401 (expired/invalid session) drop the token and bounce to /login.
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}
api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401 && getToken()) {
      setToken(null);
      onUnauthorized?.();
    }
    return Promise.reject(error);
  }
);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export interface EmailListItem {
  id: string;
  provider_message_id: string;
  sender_name: string | null;
  sender_email: string | null;
  subject: string | null;
  received_at: string | null;
  status: "new" | "archived" | "ingested";
  attachment_count: number;
  has_approval_screenshot: boolean;
  extract_email_at: string | null;
  no_sheets_found_at: string | null;
  no_sheets_note: string | null;
  attachments: Attachment[];
  // Outlook-style conversation grouping. thread_id is the row's own id when
  // the provider gave no conversation_id (a singleton thread of 1 message).
  conversation_id: string | null;
  thread_id: string | null;
  thread_message_count: number;
}

/** One row per Outlook-style conversation in the threaded inbox list —
 *  same shape as EmailListItem, showing the newest message's summary. */
export type ThreadListItem = EmailListItem;

export interface Attachment {
  attachment_id: string;
  filename: string;
  content_type: string;
  kind: "timesheet" | "approval_screenshot" | "other";
  cid?: string | null;
  is_inline?: boolean | null;
  size?: number | null;
}

export interface EmailRecipient {
  name: string | null;
  email: string;
}

export interface EmailDetail extends EmailListItem {
  body_text: string | null;
  body_html: string | null;
  to_recipients?: EmailRecipient[];
  cc_recipients?: EmailRecipient[];
  attachments: Attachment[];
  inline_attachment_ids: string[];
  /** Filenames on THIS message Extract Email has already read — for the
   *  Extracted/New badge. Every run re-reads everything; this is a record of
   *  what has been looked at, not a cache of answers. */
  extracted_filenames?: string[];
}

/** Every message in a conversation, oldest first — the Outlook-style
 *  "see the full history" view. */
/** Plain-English read of what a conversation is about. Produced by PASS 1 of
 *  Extract Email — the same call that decides which items are timesheets also
 *  says what the thread is about, so there is no separate summarisation call. */
export interface ThreadSummary {
  headline: string;
  status: "sheet_submitted" | "awaiting_approval" | "approved"
        | "correction_requested" | "chasing" | "other";
  narrative: string;
  timesheet_sent: boolean;
  approval_requested: boolean;
  approval_given: boolean;
  period: string;
  employee: string;
  action_needed: string;
  message_count: number;
  model: string;
  at: string;
}

export interface ThreadDetail {
  thread_id: string;
  messages: EmailDetail[];
  summary?: ThreadSummary | null;
  /** Sheets already read by a previous Extract Email run on this thread. */
  extracted_sheets?: string[];
  /** When this conversation was last extracted — null if never. Extraction
   *  always sends the WHOLE thread, so any message that arrived before this
   *  was included in that run. */
  extracted_at?: string | null;
}

export interface MatchedEmployee {
  employee_pk: string;
  employee_id: string;
  employee_name: string;
  account_manager: string | null;
  location: string | null;
  matched_email: string | null;
  is_sender: boolean;
  source: string | null;
}

export interface SourceFileEntry {
  key: string | null;
  filename: string | null;
  source_id: string | null;
  attachment_id: string | null;
  ingested_at: string | null;
  buckets: Record<string, string[]>;
}

export interface TimesheetRecord {
  id: string;
  matched_employee_pk: string | null;
  employee_id: string | null;
  employee_name: string | null;
  account_manager: string | null;
  dco_number: string | null;
  match_note: string | null;
  month: number;
  year: number;
  calendar_days: number | null;
  annual_leave_dates: string[];
  remote_work_dates: string[];
  sick_leave_dates: string[];
  maternity_leave_dates: string[];
  unpaid_leave_dates: string[];
  absent_dates: string[];
  public_holiday_dates: string[];
  annual_leave_count: number;
  remote_work_count: number;
  sick_leave_count: number;
  maternity_leave_count: number;
  unpaid_leave_count: number;
  absent_count: number;
  public_holiday_count: number;
  validation_status: "verified" | "manual_review";
  llm_summary: string | null;
  hr_flags: string[];
  approval_detected: boolean;
  approval_detail: string | null;
  approval_status: "pending" | "approved" | "not_approved";
  source_email_id: string | null;
  storage_folder: string | null;
  source_files: SourceFileEntry[];
  source_file_count: number;
}

export interface TimesheetExportRow extends Omit<TimesheetRecord, "validation_status" | "approval_status"> {
  validation_status: TimesheetRecord["validation_status"] | "";
  approval_status: TimesheetRecord["approval_status"] | "";
  location: string | null;
  project: string | null;
  employee_email: string | null;
  contact_no: string | null;
  has_record: boolean;
}

export interface DashboardRow {
  employee_pk: string | null;
  employee_id: string | null;
  employee_name: string | null;
  account_manager: string | null;
  dco_number: string | null;
  location: string | null;
  status: "green" | "yellow";
  record_count: number;
  needs_review_count: number;
  pending_approval_count: number;
  years: number[];
  submitted_months: number[];
  in_matcher: boolean;
  has_records: boolean;
  focus_record_id: string | null;
  focus_validation_status: "verified" | "manual_review" | null;
  focus_approval_status: "pending" | "approved" | "not_approved" | null;
}

export interface DashboardSummary {
  year: number;
  month: number;
  total_employees: number;
  submitted_this_month: number;
  missing_this_month: number;
  needs_review: number;
  pending_approval: number;
  missing_employees: string[];
  rows: DashboardRow[];
  filtered_total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

// ---- pipeline tracker ----
export type PipelineStatus = "processing" | "success" | "needs_review" | "failed" | "resolved";

export interface PipelineEvent {
  stage: string;
  status: "ok" | "warn" | "fail";
  detail: string;
  at: string;
}

export interface PipelineFile {
  id: string;
  filename: string;
  content_type: string | null;
  size_bytes: number | null;
  source_kind: "upload" | "email" | "manual";
  source_id: string | null;
  attachment_id: string | null;
  status: PipelineStatus;
  stage: string;
  failure_code: string | null;
  failure_label: string | null;
  failure_detail: string | null;
  events: PipelineEvent[];
  employee_id: string | null;
  employee_name: string | null;
  month: number | null;
  year: number | null;
  record_id: string | null;
  extraction_model: string | null;
  extraction_method: string | null;
  used_ocr: boolean;
  extraction_meta: Record<string, unknown> | null;
  // True when AI recommends accept (all checks passed). Record not filed until Review.
  auto_accepted: boolean;
  can_retry: boolean;
  can_resolve_assign: boolean;
  resolved_at: string | null;
  resolution_note: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PipelineStats {
  total: number;
  processing: number;
  success: number;
  needs_review: number;
  failed: number;
  resolved: number;
  by_failure_code: Record<string, number>;
  failure_labels: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}
export const PAGE_SIZE = 200;

// ---------------------------------------------------------------------------
// Inbox
// ---------------------------------------------------------------------------
/** Outlook-style conversation list: one row per thread (grouped by Graph
 *  conversationId), newest message shown, with a message count. */
export const fetchThreads = (
  q: string,
  status: string,
  offset = 0,
  limit = PAGE_SIZE
) =>
  api
    .get<Page<ThreadListItem>>("/inbox/threads", {
      params: { q: q || undefined, status: status || undefined, offset, limit },
    })
    .then((r) => r.data);

/** Every message in this email's conversation, oldest first. */
export const fetchThread = (msgId: string) =>
  api.get<ThreadDetail>(`/inbox/${encodeURIComponent(msgId)}/thread`).then((r) => r.data);

export const fetchEmail = (id: string) =>
  api.get<EmailDetail>(`/inbox/${id}`).then((r) => r.data);

// Archive (accepted=false). Direct accept-and-ingest was removed — every
// extraction goes through Extract Email + Compare & Fix review.
export const decideEmail = (id: string, accepted: boolean) =>
  api.post(`/inbox/${id}/decision`, { accepted }).then((r) => r.data);

export const restoreEmail = (id: string) => api.post(`/inbox/${id}/restore`).then((r) => r.data);

export const attachmentUrl = (msgId: string, attId: string) =>
  withAuthParam(`/api/v1/inbox/${msgId}/attachments/${encodeURIComponent(attId)}`);

// Server-side page-image render for DOCX/XLSX/PDF (previews in any browser).
export const attachmentRenderUrl = (msgId: string, attId: string) =>
  withAuthParam(`/api/v1/inbox/${msgId}/attachments/${encodeURIComponent(attId)}/render`);

export const pipelineRawRenderUrl = (pipelineId: string) =>
  withAuthParam(`/api/v1/pipeline/${pipelineId}/raw-render`);

// ---- full-email .eml export (3-dot menu) ----
export const emlUrl = (msgId: string) =>
  withAuthParam(`/api/v1/inbox/${encodeURIComponent(msgId)}/as-eml`);

export interface LlmEgressPart {
  name: string;
  file_type: string;
  bytes: number;
  sha256: string;
  jpeg_b64?: string;
}

/** Exactly what Extract Email sends to OpenAI, built the same way the real
 *  run builds it — so this is a record of what leaves, not a description. */
export interface LlmEgressPreview {
  flow: string;
  model: string;
  pii_redaction: boolean;
  scope: string;
  steps: { n: number; title: string; detail: string; items: string[] }[];
  thread_messages: string[];
  /** Non-empty when the mailbox fetch degraded — e.g. a long thread got
   * truncated to its newest messages, or the conversation couldn't be fully
   * fetched — so fewer messages were sent than the reviewer would assume. */
  warnings?: string[];
  subject_sent: string;
  body_sent: string;
  files_sent: LlmEgressPart[];
  images_sent: LlmEgressPart[];
  not_sent: string[];
  formats_detected: string[];
  system_prompt: string;
  user_prompt: string;
  call_count: { inference: number; file_uploads: number; file_deletes: number };
  redacted: string[];
  not_redacted: string[];
  policy: string;
}

/** Audit: what Extract Email would send to the vision model after PII scrub. */
export const fetchLlmPreview = (msgId: string) =>
  api
    .get<LlmEgressPreview>(`/inbox/${encodeURIComponent(msgId)}/llm-preview`)
    .then((r) => r.data);

export const saveEmlToVault = (
  msgId: string, body: { manager: string; employee: string; month: number; year: number },
) =>
  api.post<{ saved: boolean; path: string; filename: string }>(
    `/inbox/${encodeURIComponent(msgId)}/as-eml/save-to-vault`, body).then((r) => r.data);

// ---------------------------------------------------------------------------
// Live extraction progress (Server-Sent Events)
// ---------------------------------------------------------------------------
/** One progress frame emitted by the streaming extraction endpoints. */
export interface ExtractionEvent {
  stage: "start" | "plan" | "agent" | "unpack" | "format" | "extract" | "approval"
       // The two model calls Extract Email makes: pass1 understands the whole
       // conversation, pass2 transcribes only the sheets pass1 confirmed.
       | "pass1" | "pass2"
       | "group" | "autoaccept" | "file" | "done" | "error";
  status: "start" | "spin" | "ok" | "warn" | "fail" | "skip";
  message: string;
  llm_calls: number;
  elapsed_ms: number;
  data: Record<string, unknown>;
}

/** POST to an SSE endpoint and invoke `onEvent` for every progress frame.
 *  Resolves with the final `done` event's `data.result` (the same payload the
 *  non-streamed endpoint returns), or rejects on an `error` frame. Uses fetch
 *  (not axios) so we can read the streamed body incrementally. */
export async function streamExtraction(
  path: string,
  body: BodyInit | undefined,
  onEvent: (ev: ExtractionEvent) => void,
): Promise<any> {
  const token = getToken();
  const resp = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`Extraction failed (${resp.status})`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: any = undefined;
  let errored: string | null = null;

  const handle = (frame: string) => {
    const line = frame.split("\n").find((l) => l.startsWith("data:"));
    if (!line) return;
    let ev: ExtractionEvent;
    try {
      ev = JSON.parse(line.slice(5).trim());
    } catch {
      return;
    }
    onEvent(ev);
    if (ev.stage === "done") result = ev.data?.result;
    if (ev.stage === "error") errored = ev.message || "Extraction error";
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      handle(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 2);
    }
  }
  if (buffer.trim()) handle(buffer);
  if (errored) throw new Error(errored);
  return result;
}

/** Extract Email with live progress. */
export const extractFullEmailStream = (
  msgId: string, onEvent: (ev: ExtractionEvent) => void,
) => streamExtraction(
  `/inbox/${encodeURIComponent(msgId)}/extract-full/stream`, undefined, onEvent);

/** Upload page extraction with live progress. */
export const uploadTimesheetsStream = (
  files: File[], onEvent: (ev: ExtractionEvent) => void,
) => {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  return streamExtraction("/upload/stream", fd, onEvent);
};

// ---------------------------------------------------------------------------
// Agentic chat (timesheet assistant)
// ---------------------------------------------------------------------------
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatChange {
  record_id: string;
  employee_name: string | null;
  month: number;
  year: number;
  month_name: string | null;
  leave_type: string;
  action: "add" | "set" | "clear";
  before: string[];
  after: string[];
  added: string[];
  removed: string[];
}

export interface ChatPromptGroup {
  group: string;
  prompts: string[];
}

export interface ChatSuggestions {
  suggestions: string[];
  prompt_book: ChatPromptGroup[];
  enabled: boolean;
  model: string | null;
}

export const fetchChatSuggestions = () =>
  api.get<ChatSuggestions>("/agentic-chat/suggestions").then((r) => r.data);

// ---- streaming chat (Server-Sent Events) ----------------------------------
// A structured result card the assistant streams back (rendered visually).
export interface ChatCard {
  type:
    | "leave_change" | "approval_change" | "draft_email" | "dashboard"
    | "missing" | "submitted" | "pending" | "team" | "compare" | "anomalies";
  // union payload — fields depend on `type`; read defensively in the UI.
  [k: string]: unknown;
}

export type ChatStreamEvent =
  | { type: "token"; text: string }
  | { type: "tool"; phase: "start" | "end"; name: string; label?: string; write?: boolean; ok?: boolean }
  | { type: "card"; card: ChatCard }
  | { type: "suggestions"; items: string[] }
  | { type: "done"; tools_used?: string[]; changes?: ChatChange[]; error?: string | null };

/** POST the conversation and stream SSE events back through `onEvent`.
 *  Resolves when the stream ends. Uses fetch (axios can't stream in-browser). */
export async function sendChatStream(
  messages: ChatMessage[],
  onEvent: (ev: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const res = await fetch("/api/v1/agentic-chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Fingerprint": deviceFingerprint(),
    },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok || !res.body) {
    if (res.status === 401) { setToken(null); onUnauthorized?.(); }
    throw new Error(`Chat stream failed (${res.status})`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const data = line.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      try { onEvent(JSON.parse(data) as ChatStreamEvent); } catch { /* ignore partial */ }
    }
  }
}

// ---------------------------------------------------------------------------
// Dashboard / employees
// ---------------------------------------------------------------------------
export type CoverageStatus =
  | "submitted"
  | "missing"
  | "needs_review"
  | "approved"
  | "not_approved"
  | "pending_approval";

export const fetchCoverage = (params: {
  year?: number;
  month?: number;
  q?: string;
  location?: string;
  status?: CoverageStatus | "";
  only_missing?: boolean;
  offset?: number;
  limit?: number;
}) =>
  api
    .get<DashboardSummary>("/employees/coverage", {
      params: {
        year: params.year,
        month: params.month,
        q: params.q || undefined,
        location: params.location || undefined,
        status: params.status || undefined,
        only_missing: params.only_missing || undefined,
        offset: params.offset ?? 0,
        limit: params.limit ?? PAGE_SIZE,
      },
    })
    .then((r) => r.data);

export const fetchEmployeeRecords = (pk: string, year?: number) =>
  api
    .get<TimesheetRecord[]>(`/employees/${encodeURIComponent(pk)}/records`, { params: { year } })
    .then((r) => r.data);

// ---------------------------------------------------------------------------
// Timesheet records
// ---------------------------------------------------------------------------
export const fetchRecord = (id: string) =>
  api.get<TimesheetRecord>(`/timesheets/${id}`).then((r) => r.data);

export const fetchExportByPeriod = (month: number, year: number) =>
  api
    .get<TimesheetExportRow[]>("/timesheets/by-period", { params: { month, year } })
    .then((r) => r.data);

export const timesheetExportUrl = (month: number, year: number) =>
  withAuthParam(`/api/v1/timesheets/export?month=${month}&year=${year}`);

export const approveRecord = (id: string, approved: boolean) =>
  api.post<TimesheetRecord>(`/timesheets/${id}/approve`, { approved }).then((r) => r.data);

export const verifyRecord = (id: string) =>
  api.post<TimesheetRecord>(`/timesheets/${id}/verify`).then((r) => r.data);

export const deleteRecord = (id: string) => api.delete(`/timesheets/${id}`).then((r) => r.data);

export interface TimesheetUpdate {
  annual_leave_dates?: string[];
  remote_work_dates?: string[];
  sick_leave_dates?: string[];
  maternity_leave_dates?: string[];
  unpaid_leave_dates?: string[];
  absent_dates?: string[];
  public_holiday_dates?: string[];
  month?: number;
  year?: number;
}
export const updateRecord = (id: string, body: TimesheetUpdate) =>
  api.patch<TimesheetRecord>(`/timesheets/${id}`, body).then((r) => r.data);

export interface SourceFile {
  name: string;
  rel_path: string;
  content_type: string;
  size: number;
}
export const recordSources = (id: string) =>
  api.get<SourceFile[]>(`/timesheets/${id}/sources`).then((r) => r.data);

// ---------------------------------------------------------------------------
// Pipeline tracker
// ---------------------------------------------------------------------------
export const fetchPipeline = (params?: {
  status?: string;
  failure_code?: string;
  source_kind?: string;
  source_id?: string;
  /** Every record staged from this email thread (conversation) — however
   * many employee+month groups it produced. */
  thread_key?: string;
  /** true = AI recommends accept (staged, not filed yet). */
  auto_accepted?: boolean;
  q?: string;
  offset?: number;
  limit?: number;
}) =>
  api
    .get<Page<PipelineFile>>("/pipeline", {
      params: {
        status: params?.status || undefined,
        failure_code: params?.failure_code || undefined,
        source_kind: params?.source_kind || undefined,
        source_id: params?.source_id || undefined,
        thread_key: params?.thread_key || undefined,
        auto_accepted: params?.auto_accepted ?? undefined,
        q: params?.q || undefined,
        offset: params?.offset ?? 0,
        limit: params?.limit ?? PAGE_SIZE,
      },
    })
    .then((r) => r.data);

export const fetchPipelineStats = () =>
  api.get<PipelineStats>("/pipeline/stats").then((r) => r.data);

export const retryPipelineFile = (id: string) =>
  api.post<PipelineFile>(`/pipeline/${id}/retry`).then((r) => r.data);

export const deletePipelineFile = (id: string) =>
  api.delete(`/pipeline/${id}`).then((r) => r.data);

// ---------------------------------------------------------------------------
// Files / folders (3-level: Manager → Employee → Month)
// ---------------------------------------------------------------------------
export interface ManagerFolder { name: string; rel_path: string; employee_count: number; }
export interface EmployeeFolder { name: string; rel_path: string; month_count: number; }
export interface MonthFolder { name: string; rel_path: string; file_count: number; }
export interface FileItem { name: string; rel_path: string; size: number; content_type: string; }

export const listFileManagers = () => api.get<ManagerFolder[]>("/files/managers").then((r) => r.data);
export const listFileEmployees = (manager: string) =>
  api.get<EmployeeFolder[]>(`/files/managers/${encodeURIComponent(manager)}/employees`).then((r) => r.data);
export const listFileMonths = (manager: string, emp: string) =>
  api
    .get<MonthFolder[]>(
      `/files/managers/${encodeURIComponent(manager)}/employees/${encodeURIComponent(emp)}/months`
    )
    .then((r) => r.data);
export const listFileItems = (manager: string, emp: string, month: string) =>
  api
    .get<FileItem[]>(
      `/files/managers/${encodeURIComponent(manager)}/employees/${encodeURIComponent(emp)}/months/${encodeURIComponent(month)}/items`
    )
    .then((r) => r.data);

export const fileContentUrl = (relPath: string) =>
  withAuthParam(`/api/v1/files/content?rel_path=${encodeURIComponent(relPath)}`);
export const fileRenderUrl = (relPath: string) =>
  withAuthParam(`/api/v1/files/render?rel_path=${encodeURIComponent(relPath)}`);
// Scoped ZIP of any subtree (one employee or one month) by vault-relative path.
export const downloadScopedZipUrl = (relPath: string) =>
  withAuthParam(`/api/v1/files/download-zip?rel_path=${encodeURIComponent(relPath)}`);

export type VaultYear = { year: number; files: number; bytes: number };
export const fetchVaultYears = () =>
  api.get<VaultYear[]>("/files/years").then((r) => r.data);

type ZipScope = { manager?: string; relPath?: string; year?: number };
function zipScopeQuery(scope: ZipScope): string {
  const p = new URLSearchParams();
  if (scope.manager) p.set("manager", scope.manager);
  if (scope.relPath) p.set("rel_path", scope.relPath);
  if (scope.year) p.set("year", String(scope.year));
  const q = p.toString();
  return q ? `?${q}` : "";
}
// Total {files, bytes} of a download scope — drives the progress bar.
export const fetchDownloadSize = (scope: ZipScope) =>
  api.get<{ files: number; bytes: number }>(`/files/download-size${zipScopeQuery(scope)}`)
    .then((r) => r.data);
// Authed URL for a scoped ZIP (year / manager / subtree), for native downloads.
export const scopedZipUrl = (scope: ZipScope) =>
  withAuthParam(`/api/v1/files/download-zip${zipScopeQuery(scope)}`);

// Delete a single file from the vault.
export const deleteVaultFile = (relPath: string) =>
  api.delete("/files/file", { params: { rel_path: relPath } }).then((r) => r.data);

// Upload one or more files straight into an employee's month folder.
export const uploadFilesToMonth = (
  manager: string, emp: string, month: string, files: File[],
) => {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  return api
    .post(
      `/files/managers/${encodeURIComponent(manager)}/employees/${encodeURIComponent(emp)}/months/${encodeURIComponent(month)}/files`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } },
    )
    .then((r) => r.data);
};

export type EmlParsed = {
  subject: string;
  from_: string;
  to: string;
  date: string;
  body_text: string;
  body_html: string;
  attachments: { filename: string; content_type: string; size: number; data_b64?: string }[];
  /** Non-empty when this is the full-thread export and the mailbox fetch
   * degraded — e.g. a long conversation got truncated to its newest
   * messages — so a thin-looking bundle can explain itself. */
  warnings?: string[];
};

/**
 * Fetch parsed EML content for a file identified by its existing content URL.
 * Handles file-vault, inbox-attachment, and pipeline raw-preview URL shapes.
 */
export function fetchEmlPreview(fileUrl: string): Promise<EmlParsed> {
  try {
    const u = new URL(fileUrl, window.location.origin);
    // File vault: /api/v1/files/content?rel_path=...
    if (u.pathname.includes("/files/content")) {
      const rel = u.searchParams.get("rel_path");
      if (rel) return api.get<EmlParsed>(`/files/eml-preview?rel_path=${encodeURIComponent(rel)}`).then((r) => r.data);
    }
    // Inbox attachment: /api/v1/inbox/{msgId}/attachments/{attId}
    const att = u.pathname.match(/\/inbox\/([^/]+)\/attachments\/([^/]+)$/);
    if (att) return api.get<EmlParsed>(`/inbox/${att[1]}/attachments/${encodeURIComponent(att[2])}/eml-preview`).then((r) => r.data);
    // Pipeline raw copy: /api/v1/pipeline/{id}/raw-preview
    const pip = u.pathname.match(/\/pipeline\/([^/]+)\/raw-preview$/);
    if (pip) return api.get<EmlParsed>(`/pipeline/${pip[1]}/raw-eml-preview`).then((r) => r.data);
    // Full-email export: /api/v1/inbox/{msgId}/as-eml
    const full = u.pathname.match(/\/inbox\/([^/]+)\/as-eml$/);
    if (full) return api.get<EmlParsed>(`/inbox/${full[1]}/as-eml/preview`).then((r) => r.data);
  } catch { /* fall through */ }
  return Promise.reject(new Error("Cannot derive EML preview URL from: " + fileUrl));
}

/** Parse EML bytes we already hold in the page — an email attached INSIDE
 *  another email has no URL of its own, so its bytes are posted back to be
 *  parsed. Lets forwarded mail be opened at any nesting depth. */
export function fetchEmlPreviewFromBytes(
  filename: string, dataB64: string,
): Promise<EmlParsed> {
  const form = new FormData();
  form.append(
    "file",
    new Blob([b64ToBytes(dataB64)], { type: "message/rfc822" }),
    filename || "message.eml",
  );
  return api.post<EmlParsed>("/files/eml-preview-upload", form).then((r) => r.data);
}

/** Authenticated URL for the stored raw pipeline file (for inline preview). */
export const pipelineRawUrl = (id: string): string =>
  withAuthParam(`/api/v1/pipeline/${id}/raw-preview`);

/**
 * Resolve a failed/needs-review pipeline file via manual leave entry.
 * Same data shape as uploadManual but updates the existing tracker and
 * purges the S3 raw copy on success.
 */
export const pipelineManualFix = (
  id: string,
  body: {
    employee_pk: string;
    month: number;
    year: number;
    buckets: Record<string, string[]>;
    note?: string;
    approval_status?: "approved" | "not_approved";
    approval_detail?: string;
    files?: File[];
  }
) => {
  const form = new FormData();
  form.append("employee_pk", body.employee_pk);
  form.append("month", String(body.month));
  form.append("year", String(body.year));
  form.append("buckets", JSON.stringify(body.buckets));
  if (body.note) form.append("note", body.note);
  if (body.approval_status) form.append("approval_status", body.approval_status);
  if (body.approval_detail) form.append("approval_detail", body.approval_detail);
  (body.files ?? []).forEach((f) => form.append("files", f, f.name));
  return api
    .post<PipelineFile>(`/pipeline/${id}/manual-fix`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};

export const createFileManager = (name: string) =>
  api.post("/files/managers", { name }).then((r) => r.data);
export const createFileEmployee = (manager: string, name: string) =>
  api.post(`/files/managers/${encodeURIComponent(manager)}/employees`, { name }).then((r) => r.data);
export const createFileMonth = (manager: string, emp: string, month_label: string) =>
  api
    .post(`/files/managers/${encodeURIComponent(manager)}/employees/${encodeURIComponent(emp)}/months`, {
      month_label,
    })
    .then((r) => r.data);
export const renameFolder = (rel_path: string, new_name: string) =>
  api.patch("/files/folder", { rel_path, new_name }).then((r) => r.data);
export const deleteFolder = (relPath: string) =>
  api.delete("/files/folder", { params: { rel_path: relPath } }).then((r) => r.data);

// ---------------------------------------------------------------------------
// Employee matcher (all_employee_data)
// ---------------------------------------------------------------------------
export interface Employee {
  id: string;
  employee_id: string;
  name: string;
  dco_number: string | null;
  account_manager: string | null;
  employee_email_id: string | null;
  project: string | null;
  contact_no: string | null;
  location: string | null;
  all_emails: string | null;
}
export type EmployeeInput = Omit<Employee, "id">;

export const fetchEmployeeMatcher = () => api.get<Employee[]>("/employee-matcher").then((r) => r.data);
export const createEmployee = (e: EmployeeInput) =>
  api.post<Employee>("/employee-matcher", e).then((r) => r.data);
export const updateEmployee = (id: string, e: EmployeeInput) =>
  api.put<Employee>(`/employee-matcher/${id}`, e).then((r) => r.data);
export const deleteEmployee = (id: string) =>
  api.delete(`/employee-matcher/${id}`).then((r) => r.data);

export interface SkipDetail { sheet: string; row: number; id: string; name: string; reason: string; }
export interface ImportSummary {
  inserted: number;
  updated: number;
  skipped: number;
  skipped_details?: SkipDetail[];
}
export const importEmployees = (file: File) => {
  const form = new FormData();
  form.append("file", file, file.name);
  return api
    .post<ImportSummary>("/employee-matcher/import", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 600_000, // large Excel + remote RDS can take several minutes
    })
    .then((r) => r.data);
};

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------
export interface UploadResult {
  pipeline_id: string;
  filename: string;
  status: PipelineStatus;
  failure_code: string | null;
  failure_detail: string | null;
  record_id: string | null;
  employee_name: string | null;
  employee_id: string | null;
  month: number | null;
  year: number | null;
  validation_status: "verified" | "manual_review" | null;
  llm_summary: string | null;
  match_note: string | null;
}
export const uploadManual = (body: {
  employee_pk: string;
  month: number;
  year: number;
  buckets: Record<string, string[]>;
  note?: string;
  files: File[];
}) => {
  const form = new FormData();
  form.append("employee_pk", body.employee_pk);
  form.append("month", String(body.month));
  form.append("year", String(body.year));
  form.append("buckets", JSON.stringify(body.buckets));
  if (body.note) form.append("note", body.note);
  body.files.forEach((f) => form.append("files", f, f.name));
  return api
    .post<UploadResult>("/upload/manual", form, { headers: { "Content-Type": "multipart/form-data" } })
    .then((r) => r.data);
};

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------
export interface Health { status: string; email_provider: string; extraction_engine: string; }
export const fetchHealth = () => axios.get<Health>("/health").then((r) => r.data);

export const MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const MONTHS_LONG = ["", "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

// ===========================================================================
// Auth
// ===========================================================================
export type AuthRole = "admin" | "user" | "viewer";
export type AuthModeT = "otp" | "totp" | "captcha";

export interface AuthUser {
  id: string;
  username: string;
  email: string | null;
  role: AuthRole;
  auth_mode: AuthModeT;
  is_active: boolean;
  last_login_at: string | null;
}

export interface LoginResult {
  status: "authenticated" | "captcha_required" | "otp_required" | "totp_required" | "totp_enrollment_required";
  access_token?: string | null;
  login_token?: string | null;
  captcha_id?: string | null;
  user?: AuthUser | null;
  message?: string | null;
  debug_otp?: string | null;
  totp_uri?: string | null;
  totp_qr_png?: string | null;
}
export interface TokenResult {
  status: string;
  access_token: string;
  user: AuthUser;
}

export interface TotpSetupResult {
  uri: string;
  qr_png: string;
  manual_secret: string;
  enrolled: boolean;
}

const fp = () => deviceFingerprint();

// Login is username + password only; the backend replies with which single
// challenge (captcha / otp / totp) finishes the sign-in for this user.
export const authLogin = (username: string, password: string) =>
  api.post<LoginResult>("/auth/login", { username, password, fingerprint: fp() }).then((r) => r.data);

export const authVerifyOtp = (login_token: string, code: string) =>
  api.post<TokenResult>("/auth/verify-otp", { login_token, code, fingerprint: fp() }).then((r) => r.data);

export const authVerifyTotp = (login_token: string, code: string) =>
  api.post<TokenResult>("/auth/verify-totp", { login_token, code, fingerprint: fp() }).then((r) => r.data);

export const authResendOtp = (login_token: string) =>
  api.post<LoginResult>("/auth/resend-otp", { login_token, fingerprint: fp() }).then((r) => r.data);

export const authVerifyCaptcha = (login_token: string, captcha_id: string, answer: string) =>
  api.post<TokenResult>("/auth/verify-captcha", { login_token, captcha_id, answer, fingerprint: fp() }).then((r) => r.data);

export const authMe = () => api.get<AuthUser>("/auth/me").then((r) => r.data);
export const authLogout = () => api.post("/auth/logout").then((r) => r.data);
export const captchaUrl = () => `/api/v1/auth/captcha?t=${Date.now()}`;

// ===========================================================================
// Admin — users
// ===========================================================================
export const adminListUsers = () => api.get<AuthUser[]>("/admin/users").then((r) => r.data);
export const adminCreateUser = (body: {
  username: string; password: string; email?: string | null; role: AuthRole; auth_mode: AuthModeT;
}) => api.post<AuthUser>("/admin/users", body).then((r) => r.data);
export const adminUpdateUser = (id: string, body: Partial<{
  email: string | null; role: AuthRole; auth_mode: AuthModeT; is_active: boolean; password: string;
}>) => api.patch<AuthUser>(`/admin/users/${id}`, body).then((r) => r.data);
export const adminTotpSetup = (id: string) =>
  api.post<TotpSetupResult>(`/admin/users/${id}/totp-setup`).then((r) => r.data);
export const adminDeleteUser = (id: string) => api.delete(`/admin/users/${id}`).then((r) => r.data);

// ===========================================================================
// Admin — read-only AI status (.env source of truth)
// ===========================================================================
export interface AiStatusItem {
  kind: "extraction" | "agent";
  label: string;
  provider: string;
  model: string;
  has_key: boolean;
  note: string | null;
}
export const adminConfigStatus = () =>
  api.get<AiStatusItem[]>("/admin/config/status").then((r) => r.data);

// ===========================================================================
// Auto Extract — bulk Extract Email, one thread at a time, in the background
// ===========================================================================
export interface AutoExtractStatus {
  state: "idle" | "running" | "stopping" | "stopped" | "completed";
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  current: { thread_id: string; subject: string } | null;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
}
export const startAutoExtract = () =>
  api.post<AutoExtractStatus>("/inbox/auto-extract/start").then((r) => r.data);
export const stopAutoExtract = () =>
  api.post<AutoExtractStatus>("/inbox/auto-extract/stop").then((r) => r.data);
export const fetchAutoExtractStatus = () =>
  api.get<AutoExtractStatus>("/inbox/auto-extract/status").then((r) => r.data);
