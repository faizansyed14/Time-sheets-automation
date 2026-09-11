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
   *  Extracted/New badge. Nested .eml containers are included once unwrapped.
   *  The Inbox also trusts the thread extraction watermark per message. */
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
  other_leave_dates: string[];
  working_dates: string[];
  weekend_dates: string[];
  annual_leave_count: number;
  remote_work_count: number;
  sick_leave_count: number;
  maternity_leave_count: number;
  unpaid_leave_count: number;
  absent_count: number;
  public_holiday_count: number;
  other_leave_count: number;
  working_dates_count: number;
  weekend_dates_count: number;
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

export type ExportStatus = "Received & Stored" | "Received & Not Stored" | "Not Received";

export interface TimesheetExportRow extends Omit<TimesheetRecord, "validation_status" | "approval_status"> {
  validation_status: TimesheetRecord["validation_status"] | "";
  approval_status: TimesheetRecord["approval_status"] | "";
  location: string | null;
  project: string | null;
  personal_email: string | null;
  work_email: string | null;
  contact_no: string | null;
  has_record: boolean;
  status: ExportStatus;
  // Pre-formatted ("YYYY-MM-DD HH:MM") — when the pipeline first received
  // something for this employee this period, and when it was actually
  // filed. A "Received & Not Stored" row has the first without the second.
  received_at: string | null;
  stored_at: string | null;
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
  awaiting_review_this_month: boolean;
}

export interface DashboardSummary {
  year: number;
  month: number;
  total_employees: number;
  submitted_this_month: number;
  missing_this_month: number;
  awaiting_review_this_month: number;
  submitted_pct: number;
  missing_pct: number;
  awaiting_review_pct: number;
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
  source_kind: "upload" | "email" | "manual" | "portal";
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
  success: number;         // all-time — feeds "Open files" = total - success - resolved
  success_recent: number;  // last 30 days — what the "Success" stat card shows
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
  msgId: string,
  body: {
    manager: string;
    employee: string;
    month: number;
    year: number;
    employee_pk?: string;
  },
) =>
  api.post<{ saved: boolean; path: string; filename: string; employee_folder?: string }>(
    `/inbox/${encodeURIComponent(msgId)}/as-eml/save-to-vault`, body).then((r) => r.data);

// ---------------------------------------------------------------------------
// Live extraction progress (Server-Sent Events)
// ---------------------------------------------------------------------------
/** One progress frame emitted by the streaming extraction endpoints. */
export interface ExtractionEvent {
  stage: "start" | "plan" | "agent" | "unpack" | "format" | "extract" | "approval"
       // The two model calls: pass1 classifies the thread, pass2 extracts confirmed sheets.
       | "pass1" | "pass2"
       | "group" | "autoaccept" | "file" | "done" | "error";
  status: "start" | "spin" | "ok" | "warn" | "fail" | "skip";
  message: string;
  llm_calls: number;
  elapsed_ms: number;
  /** Stage-specific payload — e.g. unpack.dropped, pass1.confirmed, pass2.raw */
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

/** Extract Email with live progress. `forceFull` bypasses incremental
 *  windowing/attachment-cache reuse and re-reads the whole thread fresh —
 *  the "Re-read entire thread" escape hatch for a suspected stale read. */
export const extractFullEmailStream = (
  msgId: string, onEvent: (ev: ExtractionEvent) => void, forceFull = false,
) => streamExtraction(
  `/inbox/${encodeURIComponent(msgId)}/extract-full/stream${forceFull ? "?force_full=true" : ""}`,
  undefined, onEvent);

/** Upload page extraction with live progress. */
export const uploadTimesheetsStream = (
  files: File[], onEvent: (ev: ExtractionEvent) => void,
) => {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  return streamExtraction("/upload/stream", fd, onEvent);
};

// ---------------------------------------------------------------------------
// AI chat — generic assistant, no database access. Can read an attached file
// (PDF/DOCX/XLSX/TXT as text, an image as an image) the same way ChatGPT/
// Claude's own chat does.
// ---------------------------------------------------------------------------
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatSuggestions {
  enabled: boolean;
  model: string | null;
}

export const fetchChatSuggestions = () =>
  api.get<ChatSuggestions>("/agentic-chat/suggestions").then((r) => r.data);

// Whether "others" (non-admin) can see/use Ask AI — admin always can
// regardless of this flag. Read by every role that can reach the chat page
// (to decide whether to lock it); written by admin only, from AI Settings.
export interface ChatAccess {
  enabled_for_others: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export const fetchChatAccess = () =>
  api.get<ChatAccess>("/agentic-chat/access").then((r) => r.data);

export const updateChatAccess = (enabled_for_others: boolean) =>
  api.put<ChatAccess>("/agentic-chat/access", { enabled_for_others }).then((r) => r.data);

// ===========================================================================
// System health — LLM key/credits + Microsoft Graph credential, checked on a
// schedule (see backend services/system_health). Entirely separate feature
// from reminders/chat access above — its own table, its own mailer.
// ===========================================================================
export type HealthStatusValue = "ok" | "degraded" | "down" | "unknown";
export interface SystemHealthRow {
  component: "llm" | "graph";
  status: HealthStatusValue;
  detail: string | null;
  last_checked_at: string | null;
  last_ok_at: string | null;
  last_alert_sent_at: string | null;
}

export const fetchSystemHealth = () =>
  api.get<SystemHealthRow[]>("/system-health").then((r) => r.data);

export const checkSystemHealthNow = () =>
  api.post<Record<string, any>>("/system-health/check-now").then((r) => r.data);

export type ChatStreamEvent =
  | { type: "token"; text: string }
  | { type: "done"; error?: string | null };

/** POST the conversation (+ optional attached file) and stream SSE events
 *  back through `onEvent`. Resolves when the stream ends. Uses fetch (axios
 *  can't stream in-browser); multipart so a File can ride alongside the
 *  JSON-encoded message history in one request. */
export async function sendChatStream(
  messages: ChatMessage[],
  onEvent: (ev: ChatStreamEvent) => void,
  signal?: AbortSignal,
  file?: File | null,
): Promise<void> {
  const token = getToken();
  const form = new FormData();
  form.set("messages", JSON.stringify(messages));
  if (file) form.set("file", file);
  const res = await fetch("/api/v1/agentic-chat/stream", {
    method: "POST",
    headers: {
      // No Content-Type here — the browser sets the multipart boundary itself.
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Fingerprint": deviceFingerprint(),
    },
    body: form,
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
  | "awaiting_review"
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
  other_leave_dates?: string[];
  working_dates?: string[];
  weekend_dates?: string[];
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
  /** Hide one status from the results — the Activity log hides "success". */
  exclude_status?: string;
  failure_code?: string;
  source_kind?: string;
  source_id?: string;
  /** Every record staged from this email thread (conversation) — however
   * many employee+month groups it produced. */
  thread_key?: string;
  /** true = AI recommends accept (staged, not filed yet). */
  auto_accepted?: boolean;
  /** Filter by PipelineFile.month/.year — e.g. the Dashboard's "View in
   *  Pipeline" deep link for a specific awaiting-review period. */
  month?: number;
  year?: number;
  /** ISO instant — only rows updated at/after this (backs the "Success (last
   *  30 days)" stat card's drill-down, so the list matches what it counted). */
  updated_after?: string;
  q?: string;
  offset?: number;
  limit?: number;
}) =>
  api
    .get<Page<PipelineFile>>("/pipeline", {
      params: {
        status: params?.status || undefined,
        exclude_status: params?.exclude_status || undefined,
        failure_code: params?.failure_code || undefined,
        source_kind: params?.source_kind || undefined,
        source_id: params?.source_id || undefined,
        thread_key: params?.thread_key || undefined,
        auto_accepted: params?.auto_accepted ?? undefined,
        month: params?.month || undefined,
        year: params?.year || undefined,
        updated_after: params?.updated_after || undefined,
        q: params?.q || undefined,
        offset: params?.offset ?? 0,
        limit: params?.limit ?? PAGE_SIZE,
      },
    })
    .then((r) => r.data);

export const fetchPipelineStats = (params?: { success_window_days?: number }) =>
  api
    .get<PipelineStats>("/pipeline/stats", {
      params: { success_window_days: params?.success_window_days || undefined },
    })
    .then((r) => r.data);

export const retryPipelineFile = (id: string) =>
  api.post<PipelineFile>(`/pipeline/${id}/retry`).then((r) => r.data);

export const deletePipelineFile = (id: string) =>
  api.delete(`/pipeline/${id}`).then((r) => r.data);

export interface RematchResultRow {
  id: string;
  filename: string;
  name: string | null;
  employee_id?: string;
  month: number | null;
  year: number | null;
}
export interface RematchResult {
  checked: number;
  rematched_count: number;
  rematched: RematchResultRow[];
  still_unmatched_count: number;
  still_unmatched: RematchResultRow[];
}
/** Re-check employee identity for every still-under-review pipeline item
 *  whose employee wasn't found at staging time — catches a roster/email
 *  staged BEFORE that employee existed in the Employee Matcher. No
 *  re-extraction, no LLM call — safe to run over the whole backlog. */
export const rematchUnmatchedPipelineFiles = () =>
  api.post<RematchResult>("/pipeline/rematch-unmatched").then((r) => r.data);

// ---------------------------------------------------------------------------
// Files / folders (3-level: Manager → Employee → Month)
// ---------------------------------------------------------------------------
export interface ManagerFolder { name: string; rel_path: string; employee_count: number; }
export interface EmployeeFolder { name: string; rel_path: string; month_count: number; }
export interface MonthFolder { name: string; rel_path: string; file_count: number; }
export interface FileItem {
  name: string;
  rel_path: string;
  size: number;
  content_type: string;
  /** When this file was written into the vault. */
  stored_at: string | null;
}

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

// ---- alternate (project-wise / location-wise / search) navigation onto the
// SAME vault files ----
// Second, read-oriented lenses onto the identical Manager/Employee/Month
// files: grouped by the Employee Matcher's own `project` or `location`
// field, or found directly by a name/ID/project search. All resolve down to
// the same rel_path-bearing FileItem rows the Manager view uses, so
// preview/delete/upload/zip-download below all keep working unchanged.
export interface ProjectFolder { name: string; employee_count: number; }
export interface ProjectEmployee {
  employee_pk: string;
  employee_id: string;
  name: string;
  project: string | null;
  location: string | null;
  account_manager: string;
  employee_folder: string;
  month_count: number;
}

export const listFileProjects = () => api.get<ProjectFolder[]>("/files/projects").then((r) => r.data);
export const listProjectEmployees = (project: string) =>
  api.get<ProjectEmployee[]>(`/files/projects/${encodeURIComponent(project)}/employees`).then((r) => r.data);
export const listFileLocations = () => api.get<ProjectFolder[]>("/files/locations").then((r) => r.data);
export const listLocationEmployees = (location: string) =>
  api.get<ProjectEmployee[]>(`/files/locations/${encodeURIComponent(location)}/employees`).then((r) => r.data);
/** Employee name, employee ID, or project ("client name") — one search
 *  across the whole Employee Matcher, used by each view's search bar to
 *  jump straight into an employee's vault. */
export const searchVaultEmployees = (q: string) =>
  api.get<ProjectEmployee[]>("/files/search-employees", { params: { q } }).then((r) => r.data);
export const listEmployeeVaultMonths = (employeePk: string) =>
  api.get<MonthFolder[]>(`/files/employee-vault/${encodeURIComponent(employeePk)}/months`).then((r) => r.data);
export const listEmployeeVaultItems = (employeePk: string, month: string) =>
  api
    .get<FileItem[]>(`/files/employee-vault/${encodeURIComponent(employeePk)}/months/${encodeURIComponent(month)}/items`)
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

type ZipScope = {
  manager?: string;
  relPath?: string;
  year?: number;
  /** Pick just these employees under `manager` (1 or a few, not the whole team). */
  employees?: string[];
  /** Bare month name, e.g. "March" — every year unless `year` also narrows it. */
  month?: string;
};
function zipScopeQuery(scope: ZipScope): string {
  const p = new URLSearchParams();
  if (scope.manager) p.set("manager", scope.manager);
  if (scope.relPath) p.set("rel_path", scope.relPath);
  if (scope.year) p.set("year", String(scope.year));
  if (scope.month) p.set("month", scope.month);
  (scope.employees ?? []).forEach((e) => p.append("employee", e));
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

// ---------------------------------------------------------------------------
// Bulk roster upload — ONE sheet listing MANY employees.
// Separate from the per-employee /upload endpoints above: a roster is read,
// previewed, and then staged as one review item per person on it.
// ---------------------------------------------------------------------------
export interface RosterPreviewRow {
  sr_no: number;
  name: string;
  title: string | null;
  location: string | null;
  confirmation: string | null;
  /** What the sheet itself prints in its Leave/Billing Days columns. */
  stated_leave_days: number | null;
  stated_billing_days: number | null;
  /** What the reader actually got out of the day grid. */
  leave_days_read: number;
  working_days_read: number;
  weekend_days_read: number;
  uncertain_days: number;
  /** Non-null when this person resolves to someone in the Employee Matcher. */
  matched_name: string | null;
  matched_employee_id: string | null;
  issues: string[];
}
export interface RosterPreview {
  filename: string;
  /** "xlsx-cells" (read straight from the spreadsheet, no AI) or
   *  "vision-roster" (census + chunked day-grid reads). */
  method: string;
  month: number | null;
  year: number | null;
  calendar_days: number | null;
  agency: string | null;
  headcount: number;
  matched: number;
  unmatched: number;
  flagged: number;
  llm_calls: number;
  issues: string[];
  rows: RosterPreviewRow[];
}
export interface BulkStageResult {
  filename: string;
  headcount: number;
  staged: number;
  matched: number;
  unmatched: string[];
  flagged: number;
  month: number | null;
  year: number | null;
  method: string;
  issues: string[];
}

function rosterForm(file: File, month?: number, year?: number): FormData {
  const form = new FormData();
  form.append("file", file, file.name);
  if (month) form.append("month", String(month));
  if (year) form.append("year", String(year));
  return form;
}

/** Read the roster and report what WOULD be staged — writes nothing. */
export const bulkRosterPreview = (file: File, month?: number, year?: number) =>
  api
    .post<RosterPreview>("/bulk-upload/preview", rosterForm(file, month, year), {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);

/** Stage one review item per employee on the roster. */
export const bulkRosterUpload = (file: File, month?: number, year?: number) =>
  api
    .post<BulkStageResult>("/bulk-upload", rosterForm(file, month, year), {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);

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
/** Move (or, with asCopy, copy) a sheet or a whole employee/month folder to
 *  a different vault location — e.g. re-filing a sheet under the right
 *  employee, or reassigning an employee's whole folder to another manager.
 *  dstRelPath is the FULL destination path (including the filename, for a
 *  file). Returns the actual path written (deduped if a same-named file
 *  already existed there). */
export const moveVaultPath = (srcRelPath: string, dstRelPath: string, asCopy = false) =>
  api
    .post<{ rel_path: string }>("/files/move-path", {
      src_rel_path: srcRelPath, dst_rel_path: dstRelPath, as_copy: asCopy,
    })
    .then((r) => r.data);

/** The Vault's "View extracted data" action — resolves a month folder to the
 *  TimesheetRecord Compare & Fix filed for it, so the Vault can link
 *  straight into /records/{id} (the same page Dashboard/Pipeline/Chat use).
 *  Pass employee_pk when known (Project/Location view); manager view has
 *  only folder names, so pass manager + employeeFolder instead. */
export const fetchVaultRecordFor = (month: string, by: { employeePk: string } | { manager: string; employeeFolder: string }) =>
  api
    .get<{ record_id: string }>("/files/record-for", {
      params: "employeePk" in by
        ? { month, employee_pk: by.employeePk }
        : { month, manager: by.manager, employee_folder: by.employeeFolder },
    })
    .then((r) => r.data);

// ---------------------------------------------------------------------------
// Employee matcher (all_employee_data)
// ---------------------------------------------------------------------------
export interface Employee {
  id: string;
  employee_id: string;
  name: string;
  aco_number: string | null;
  dco_number: string | null;
  account_manager: string | null;
  employee_email_id: string | null;
  project: string | null;
  contact_no: string | null;
  location: string | null;
  work_email: string | null;
  personal_email: string | null;
  active: boolean;
}
export type EmployeeInput = Omit<Employee, "id">;

export const fetchEmployeeMatcher = () => api.get<Employee[]>("/employee-matcher").then((r) => r.data);
export const createEmployee = (e: EmployeeInput) =>
  api.post<Employee>("/employee-matcher", e).then((r) => r.data);
export const updateEmployee = (id: string, e: EmployeeInput) =>
  api.put<Employee>(`/employee-matcher/${id}`, e).then((r) => r.data);
export const setEmployeeStatus = (id: string, active: boolean) =>
  api.patch<Employee>(`/employee-matcher/${id}/status`, { active }).then((r) => r.data);

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

/** Dry run of an employee import — what would change, before anything is written. */
export interface ImportFieldChange { field: string; old: string | null; new: string | null; }
export interface ImportPlanAdd {
  employee_id: string;
  name: string;
  location: string | null;
  project: string | null;
  account_manager: string | null;
  employee_email_id: string | null;
  work_email: string | null;
  personal_email: string | null;
  contact_no: string | null;
  aco_number: string | null;
  dco_number: string | null;
  sheet: string | null;
  row: number | null;
  possible_rename_of: string | null;
}
export interface ImportPlanUpdate {
  id: string;
  employee_id: string;
  name: string;
  location: string | null;
  changes: ImportFieldChange[];
}
export interface ImportPlanExisting {
  id: string;
  employee_id: string;
  name: string;
  location: string | null;
  account_manager: string | null;
  employee_email_id: string | null;
  work_email: string | null;
  personal_email: string | null;
  active: boolean;
}
export interface ImportPlan {
  to_add: ImportPlanAdd[];
  to_update: ImportPlanUpdate[];
  unchanged: ImportPlanExisting[];
  missing_from_file: ImportPlanExisting[];
  skipped: SkipDetail[];
}
export const previewEmployeeImport = (file: File) => {
  const form = new FormData();
  form.append("file", file, file.name);
  return api
    .post<ImportPlan>("/employee-matcher/import/preview", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 600_000,
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
export type AuthRole = "admin" | "user" | "viewer" | "vault_matcher";
export type AuthModeT = "otp" | "totp" | "captcha";

export interface AuthUser {
  id: string;
  username: string;
  email: string | null;
  role: AuthRole;
  auth_mode: AuthModeT;
  is_active: boolean;
  last_login_at: string | null;
  // Bumped (throttled) on every authenticated request; `online` is computed
  // server-side from it (see api/deps.ONLINE_THRESHOLD) — never stored as its
  // own column, so it can't go stale.
  last_seen_at: string | null;
  online: boolean;
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
// Admin — month calendars (weekends + public holidays fed into Pass 2)
// ===========================================================================
export interface PublicHoliday {
  date: string;
  name: string;
}
export interface MonthCalendar {
  id: string;
  month: number;
  year: number;
  weekend_weekdays: string[];
  public_holidays: PublicHoliday[];
  created_at: string;
  updated_at: string;
}
export const adminListCalendars = () =>
  api.get<MonthCalendar[]>("/admin/calendars").then((r) => r.data);
export const adminUpsertCalendar = (body: {
  month: number; year: number; weekend_weekdays: string[]; public_holidays: PublicHoliday[];
}) => api.put<MonthCalendar>("/admin/calendars", body).then((r) => r.data);
export const adminDeleteCalendar = (id: string) =>
  api.delete(`/admin/calendars/${id}`).then((r) => r.data);

// ===========================================================================
// Reminders — automatic 28th/9am UAE nudge + per-employee "Send now" + tests
// ===========================================================================
export type EmailPreference = "work" | "personal";
export interface ReminderConfig {
  auto_send_enabled: boolean;
  email_preference: EmailPreference;
  send_day: number;
  send_hour_uae: number;
  updated_at: string | null;
  updated_by: string | null;
  // Read-only, set from .env — this page can't change these, only explain
  // them. scheduled_check_enabled false means the automatic 28th/9am job
  // doesn't exist at all, regardless of auto_send_enabled above.
  // sending_enabled false means NOTHING on this page can actually email —
  // scheduled, Send now, Run for all now, or Test — all blocked alike.
  scheduled_check_enabled: boolean;
  sending_enabled: boolean;
}
export const fetchReminderConfig = () =>
  api.get<ReminderConfig>("/reminders/config").then((r) => r.data);
export const updateReminderConfig = (patch: { auto_send_enabled?: boolean; email_preference?: EmailPreference }) =>
  api.put<ReminderConfig>("/reminders/config", patch).then((r) => r.data);

export interface ReminderEmployeeRow {
  employee_pk: string;
  employee_id: string;
  name: string;
  account_manager: string | null;
  location: string | null;
  email: string | null;
  /** Which address `email` actually is: the preferred type, the OTHER type
   *  (fallback — the preferred one was blank for this person), "legacy" (an
   *  older row with no work/personal split yet, falling back to the
   *  resolved employee_email_id), or "none" (nothing on file at all). */
  email_source: "work" | "personal" | "legacy" | "none";
  missing: boolean;
  last_status: "sent" | "failed" | "skipped" | null;
  last_sent_at: string | null;
  last_trigger: string | null;
  last_error: string | null;
}
export const fetchReminderEmployees = (params: {
  month: number; year: number; q?: string; only_missing?: boolean; limit?: number; offset?: number;
}) =>
  api
    .get<{ total: number; rows: ReminderEmployeeRow[]; email_preference: EmailPreference }>(
      "/reminders/employees", { params })
    .then((r) => r.data);

/** XLSX of everyone missing this month's timesheet (Employee ID, Name,
 *  Manager Name, Client, Personal Email, Work Email) — a plain browser
 *  download, same pattern as timesheetExportUrl. */
export const reminderExportUrl = (month: number, year: number) =>
  withAuthParam(`/api/v1/reminders/export?month=${month}&year=${year}`);

export interface ReminderLog {
  id: string;
  run_id: string | null;
  employee_pk: string | null;
  employee_id: string | null;
  employee_name: string | null;
  recipient_email: string | null;
  month: number;
  year: number;
  trigger: "scheduled" | "manual_batch" | "manual" | "test";
  status: "sent" | "failed" | "skipped";
  error: string | null;
  sent_at: string | null;
  created_at: string | null;
}
/** Throws with response.status 409 (existing send's timestamp in
 *  err.response.data.detail.sent_at) when a reminder was already sent for
 *  this employee/month and force wasn't passed. */
export const sendReminderNow = (employee_pk: string, month: number, year: number, force = false) =>
  api.post<ReminderLog>("/reminders/send-now", { employee_pk, month, year, force }).then((r) => r.data);

export const sendTestReminder = (email: string, month: number, year: number, employee_name = "Test Employee") =>
  api.post<ReminderLog>("/reminders/test", { email, month, year, employee_name }).then((r) => r.data);

export interface ReminderRun {
  id: string;
  trigger: "scheduled" | "manual_batch";
  month: number;
  year: number;
  started_at: string | null;
  finished_at: string | null;
  total: number;
  sent_count: number;
  failed_count: number;
  skipped_count: number;
  triggered_by: string | null;
}
export const runReminderBatch = (month?: number, year?: number) =>
  api.post<{ run_id: string }>("/reminders/run-batch", { month, year }).then((r) => r.data);
export const fetchReminderRuns = (limit = 20) =>
  api.get<ReminderRun[]>("/reminders/runs", { params: { limit } }).then((r) => r.data);
export const fetchReminderRun = (id: string) =>
  api.get<ReminderRun & { logs: ReminderLog[] }>(`/reminders/runs/${id}`).then((r) => r.data);

// ===========================================================================
// System notice — one admin-authored sentence shown as a popup to every
// logged-in role (including viewer/vault_matcher) when enabled.
// ===========================================================================
export interface SystemNotice {
  message: string;
  enabled: boolean;
  updated_at: string | null;
  updated_by: string | null;
}
export const fetchSystemNotice = () =>
  api.get<SystemNotice>("/notice").then((r) => r.data);
export const updateSystemNotice = (patch: { message?: string; enabled?: boolean }) =>
  api.put<SystemNotice>("/notice", patch).then((r) => r.data);

// ===========================================================================
// Admin — extraction debug runs (temporary, purgeable full LLM trace)
// ===========================================================================
export interface DebugRunSummary {
  id: string;
  created_at: string;
  source_kind: string | null;
  source_id: string | null;
  thread_key: string | null;
  subject: string | null;
  model: string | null;
  calls: number;
  reused_sheets: number;
  n_pass1_calls: number;
  n_pass2_calls: number;
  n_dropped: number;
  n_sheets: number;
  n_errors: number;
}
export interface DebugLlmCall {
  label: string;
  model: string;
  system_prompt: string;
  user_text: string;
  image_count: number;
  response_json: unknown;
}
export interface DebugDroppedItem {
  name: string;
  reason: string;
  filter: string;
  size: number;
  mime: string;
  msg_index: number;
  thumb: string | null;
  image_path: string | null;
}
export interface DebugRunOut extends DebugRunSummary {
  pass1_calls: DebugLlmCall[];
  pass2_calls: DebugLlmCall[];
  dropped_items: DebugDroppedItem[];
  triage: Record<string, unknown>[];
  sheets: Record<string, unknown>[];
  errors: string[];
}
export const adminListDebugRuns = (limit = 50, offset = 0) =>
  api.get<DebugRunSummary[]>("/admin/debug/runs", { params: { limit, offset } }).then((r) => r.data);
export const adminGetDebugRun = (id: string) =>
  api.get<DebugRunOut>(`/admin/debug/runs/${id}`).then((r) => r.data);
export const adminDebugImageUrl = (relPath: string) =>
  withAuthParam(`/api/v1/admin/debug/image?rel_path=${encodeURIComponent(relPath)}`);
export const adminClearDebugRuns = () =>
  api.delete<{ deleted: number }>("/admin/debug/runs").then((r) => r.data);

// ===========================================================================
// Auto Extract — bulk Extract Email, one thread at a time, in the background
// ===========================================================================
export interface AutoExtractStatus {
  state: "idle" | "running" | "stopping" | "stopped" | "completed";
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  /** Already extracted, nothing new since — no model call was made for these. */
  skipped: number;
  /** `events` mirrors the SAME progress.emit() stream the manual Extract
   * Email SSE flow shows (unpack/pass1/pass2/etc) — lets the nav widget
   * render the identical live step animation for whichever thread Auto
   * Extract is currently working on. */
  current: { thread_id: string; subject: string; events: ExtractionEvent[] } | null;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  /** Watch-for-new-mail mode: stays true across a run's own completion —
   * turned on by Start, off by Stop — independent of `state` above. While
   * true, a new background sync tick that finds mail re-triggers a run on
   * its own, with nobody needing to click Auto Extract again. */
  enabled: boolean;
}
export interface AutoExtractCoverage {
  extracted_threads: number;
  total_threads: number;
}
export const startAutoExtract = () =>
  api.post<AutoExtractStatus>("/inbox/auto-extract/start").then((r) => r.data);
export const stopAutoExtract = () =>
  api.post<AutoExtractStatus>("/inbox/auto-extract/stop").then((r) => r.data);
export const fetchAutoExtractStatus = () =>
  api.get<AutoExtractStatus>("/inbox/auto-extract/status").then((r) => r.data);
export const fetchAutoExtractCoverage = () =>
  api.get<AutoExtractCoverage>("/inbox/auto-extract/coverage").then((r) => r.data);

// ===========================================================================
// Portal — employee/manager self-service (fully separate JWT namespace from
// the internal `api` instance above: its own token key, its own interceptor,
// its own 401 handling. A portal token is rejected by every internal route
// and vice versa — see backend api/portal_deps.py.)
// ===========================================================================
export const portalApi = axios.create({ baseURL: "/api/v1" });

const PORTAL_TOKEN_KEY = "portal_token";
export function getPortalToken(): string | null {
  return localStorage.getItem(PORTAL_TOKEN_KEY);
}
export function setPortalToken(token: string | null) {
  if (token) localStorage.setItem(PORTAL_TOKEN_KEY, token);
  else localStorage.removeItem(PORTAL_TOKEN_KEY);
}
portalApi.interceptors.request.use((config) => {
  const token = getPortalToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
let onPortalUnauthorized: (() => void) | null = null;
export function setPortalUnauthorizedHandler(fn: () => void) {
  onPortalUnauthorized = fn;
}
portalApi.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401 && getPortalToken()) {
      setPortalToken(null);
      onPortalUnauthorized?.();
    }
    return Promise.reject(error);
  }
);

export type PortalRole = "employee";

export interface PortalUser {
  id: string;
  username: string;
  role: PortalRole;
  employee_pk: string | null;
  employee_name: string | null;
  employee_id: string | null;
  is_active: boolean;
  last_login_at: string | null;
}
export interface PortalTokenResult {
  access_token: string;
  user: PortalUser;
}

export const portalLogin = (username: string, password: string) =>
  portalApi.post<PortalTokenResult>("/portal/auth/login", { username, password }).then((r) => r.data);
export const portalMe = () => portalApi.get<PortalUser>("/portal/auth/me").then((r) => r.data);
export const portalLogout = () => portalApi.post("/portal/auth/logout").then((r) => r.data);

export type PortalSubmissionStatusT = "draft" | "submitted" | "rejected";
export type PortalManagerDecision = "pending" | "not_approved";
export type PortalExtractionState = "not_started" | "running" | "done" | "failed";
export type PortalFileKind = "timesheet" | "sick_leave" | "other";

export interface PortalSubmissionFile {
  id: string;
  kind: PortalFileKind;
  filename: string;
  content_type: string | null;
  size_bytes: number | null;
  created_at: string;
}
export interface PortalSubmission {
  id: string;
  employee_pk: string;
  employee_name: string | null;
  employee_id: string | null;
  month: number;
  year: number;
  status: PortalSubmissionStatusT;
  manager_decision: PortalManagerDecision;
  manager_note: string | null;
  decided_at: string | null;
  approval_claimed: boolean;
  employee_note: string | null;
  extraction_state: PortalExtractionState;
  extraction_error: string | null;
  review_state: string | null;
  record_id: string | null;
  submitted_at: string | null;
  created_at: string;
  updated_at: string;
  files: PortalSubmissionFile[];
}
export interface PortalSubmissionPrecheck {
  submission: PortalSubmission | null;
  already_filed_elsewhere: boolean;
  filed_source_note: string | null;
}

// ---- employee ----
export const portalPrecheck = (month: number, year: number) =>
  portalApi.get<PortalSubmissionPrecheck>(`/portal/employee/submissions/${month}/${year}`).then((r) => r.data);
export const portalListMySubmissions = () =>
  portalApi.get<PortalSubmission[]>("/portal/employee/submissions").then((r) => r.data);
export const portalUpsertSubmission = (body: { month: number; year: number; employee_note?: string }) =>
  portalApi.post<PortalSubmission>("/portal/employee/submissions", body).then((r) => r.data);
export const portalUploadFile = (submissionId: string, kind: PortalFileKind, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return portalApi
    .post<PortalSubmission>(`/portal/employee/submissions/${submissionId}/files`, form, {
      params: { kind },
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};
export const portalDeleteFile = (submissionId: string, kind: PortalFileKind) =>
  portalApi.delete<PortalSubmission>(`/portal/employee/submissions/${submissionId}/files/${kind}`).then((r) => r.data);
/** `approval_claimed` is the employee's mandatory yes/no answer to "does
 *  this timesheet already carry manager approval evidence?" — required, no
 *  default, so the submit call 422s if it's omitted. */
export const portalSubmit = (submissionId: string, approvalClaimed: boolean) =>
  portalApi
    .post<PortalSubmission>(`/portal/employee/submissions/${submissionId}/submit`, {
      approval_claimed: approvalClaimed,
    })
    .then((r) => r.data);
/** Discard an abandoned draft (never a submitted one) entirely. */
export const portalDiscardDraft = (submissionId: string) =>
  portalApi.delete(`/portal/employee/submissions/${submissionId}`).then((r) => r.data);

// ---- internal admin: portal user accounts ----
export const adminListPortalUsers = () => api.get<PortalUser[]>("/admin/portal-users").then((r) => r.data);
export const adminCreatePortalUser = (body: { username: string; password: string; employee_pk: string }) =>
  api.post<PortalUser>("/admin/portal-users", body).then((r) => r.data);
export const adminUpdatePortalUser = (id: string, body: Partial<{ is_active: boolean; password: string }>) =>
  api.patch<PortalUser>(`/admin/portal-users/${id}`, body).then((r) => r.data);
export const adminDeletePortalUser = (id: string) => api.delete(`/admin/portal-users/${id}`).then((r) => r.data);

// ---- internal: the Pipeline page's "Portal Submissions" tab ----
export interface PortalSubmissionPipelineFileRef {
  kind: string;
  pipeline_file_id: string | null;
  pipeline_status: string | null;
  /** Set once THIS file has been accepted into a filed TimesheetRecord —
   *  nothing left to review for it. */
  record_id: string | null;
}
export interface PortalSubmissionAdminRow extends PortalSubmission {
  pipeline_files: PortalSubmissionPipelineFileRef[];
}
export const fetchPortalSubmissionsAdmin = (params?: {
  managerDecision?: PortalManagerDecision;
  month?: number;
  year?: number;
}) =>
  api
    .get<PortalSubmissionAdminRow[]>("/pipeline/portal-submissions", {
      params: {
        manager_decision: params?.managerDecision || undefined,
        month: params?.month || undefined,
        year: params?.year || undefined,
      },
    })
    .then((r) => r.data);
/** One submission's LIVE state — used by Compare & Fix so a portal item's
 *  "Manager approval" reflects the employee's self-attestation
 *  (`approval_claimed`) and any internal send-back decision, not a
 *  staging-time snapshot (extraction runs on Submit, independent of
 *  whether/when a reviewer has sent it back; see portal_extract.py). */
export const fetchPortalSubmissionAdmin = (submissionId: string) =>
  api.get<PortalSubmissionAdminRow>(`/pipeline/portal-submissions/${submissionId}`).then((r) => r.data);
/** Send a portal submission back to the employee with a note explaining what
 *  to fix — the internal reviewer's alternative to Accept in Compare & Fix.
 *  Gated by internal auth (require_full_access), not the portal token. */
export const portalSendBack = (submissionId: string, note: string) =>
  api
    .post<PortalSubmissionAdminRow>(`/pipeline/portal-submissions/${submissionId}/send-back`, { note })
    .then((r) => r.data);

/** Preview/download URL for one submission file's raw bytes — the browser
 *  loads these directly (<iframe>/<img> src), so the token rides in the
 *  query string, same pattern as withAuthParam but for the portal's own
 *  token namespace. */
export function withPortalAuthParam(url: string): string {
  const t = getPortalToken();
  if (!t) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}
export const portalEmployeeFileUrl = (submissionId: string, kind: PortalFileKind) =>
  withPortalAuthParam(`/api/v1/portal/employee/submissions/${submissionId}/files/${kind}/content`);
export const portalEmployeeRenderUrl = (submissionId: string, kind: PortalFileKind) =>
  withPortalAuthParam(`/api/v1/portal/employee/submissions/${submissionId}/files/${kind}/render`);
/** Internal (Pipeline page) preview — uses the internal `api` instance's
 *  own token namespace, not the portal's. */
export const portalAdminFileUrl = (submissionId: string, kind: string) =>
  withAuthParam(`/api/v1/pipeline/portal-submissions/${submissionId}/files/${kind}/content`);
export const portalAdminRenderUrl = (submissionId: string, kind: string) =>
  withAuthParam(`/api/v1/pipeline/portal-submissions/${submissionId}/files/${kind}/render`);

/** Internal (Pipeline page) roster — every portal-enabled employee
 *  system-wide, not scoped to one manager. Carries pipeline_files (the
 *  manager-facing roster doesn't) so a reviewer can jump straight into
 *  Compare & Fix from a roster row. */
export interface PortalRosterMemberAdmin {
  employee_pk: string;
  employee_id: string;
  employee_name: string;
  has_portal_account: boolean;
  submission: PortalSubmissionAdminRow | null;
}
export const fetchPortalRosterAdmin = (month: number, year: number) =>
  api.get<PortalRosterMemberAdmin[]>("/pipeline/portal-roster", { params: { month, year } }).then((r) => r.data);

