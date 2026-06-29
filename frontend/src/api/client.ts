import axios from "axios";

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
}

export interface Attachment {
  attachment_id: string;
  filename: string;
  content_type: string;
  kind: "timesheet" | "approval_screenshot" | "other";
  cid?: string | null;
}

export interface EmailDetail extends EmailListItem {
  body_text: string | null;
  body_html: string | null;
  attachments: Attachment[];
  ai_check: EmailAiCheck | null;
  ai_check_running: boolean;
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

export interface EmailAiCheck {
  summary: string;
  model: string | null;
  used_llm: boolean;
  checked_at: string | null;
  attachments: {
    attachment_id: string;
    filename: string;
    category: string;
    reason: string;
    source_kind?: string;
    used_ocr?: boolean;
    text_chars?: number;
    nested?: { filename?: string; file_type?: string; text_chars?: number; used_ocr?: boolean }[];
  }[];
  body_category: string;
  body_reason: string;
  recommended_timesheet_ids: string[];
  recommended_approval_id: string | null;
  extract_body: boolean;
  matched_employee: MatchedEmployee | null;
  missing: string[];
  found: string[];
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
  unpaid_leave_dates: string[];
  absent_dates: string[];
  public_holiday_dates: string[];
  annual_leave_count: number;
  remote_work_count: number;
  sick_leave_count: number;
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
export const fetchInbox = (
  q: string,
  status: string,
  offset = 0,
  limit = PAGE_SIZE
) =>
  api
    .get<Page<EmailListItem>>("/inbox", {
      params: { q: q || undefined, status: status || undefined, offset, limit },
    })
    .then((r) => r.data);

export interface IngestSelection {
  attachment_ids: string[];
  approval_attachment_id?: string | null;
  extract_body?: boolean;
}

export const fetchEmail = (id: string, refreshAi = false) =>
  api
    .get<EmailDetail>(`/inbox/${id}`, { params: refreshAi ? { refresh_ai: true } : undefined })
    .then((r) => r.data);

export const decideEmail = (id: string, accepted: boolean, selection?: IngestSelection) =>
  api.post(`/inbox/${id}/decision`, {
    accepted,
    attachment_ids: selection?.attachment_ids,
    approval_attachment_id: selection?.approval_attachment_id ?? null,
    extract_body: selection?.extract_body ?? false,
  }).then((r) => r.data);

export const restoreEmail = (id: string) => api.post(`/inbox/${id}/restore`).then((r) => r.data);

export const rerunExtraction = (id: string, selection: IngestSelection) =>
  api.post(`/inbox/${id}/rerun`, selection).then((r) => r.data);

export const bodyImagePreviewUrl = (msgId: string) =>
  withAuthParam(`/api/v1/inbox/${msgId}/body-image-preview`);

export const attachmentUrl = (msgId: string, attId: string) =>
  withAuthParam(`/api/v1/inbox/${msgId}/attachments/${encodeURIComponent(attId)}`);

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

export const approveRecord = (id: string, approved: boolean) =>
  api.post<TimesheetRecord>(`/timesheets/${id}/approve`, { approved }).then((r) => r.data);

export const verifyRecord = (id: string) =>
  api.post<TimesheetRecord>(`/timesheets/${id}/verify`).then((r) => r.data);

export const deleteRecord = (id: string) => api.delete(`/timesheets/${id}`).then((r) => r.data);

export interface TimesheetUpdate {
  annual_leave_dates?: string[];
  remote_work_dates?: string[];
  sick_leave_dates?: string[];
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
        q: params?.q || undefined,
        offset: params?.offset ?? 0,
        limit: params?.limit ?? PAGE_SIZE,
      },
    })
    .then((r) => r.data);

export const fetchPipelineStats = () =>
  api.get<PipelineStats>("/pipeline/stats").then((r) => r.data);

export const resolvePipelineFile = (id: string, note: string) =>
  api.post<PipelineFile>(`/pipeline/${id}/resolve`, { note }).then((r) => r.data);

export const resolvePipelineAssign = (
  id: string,
  body: { employee_pk: string; month: number; year: number; note?: string }
) => api.post<PipelineFile>(`/pipeline/${id}/resolve-assign`, body).then((r) => r.data);

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
export const downloadZipUrl = (manager?: string) =>
  withAuthParam(`/api/v1/files/download-zip${manager ? `?manager=${encodeURIComponent(manager)}` : ""}`);
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
  } catch { /* fall through */ }
  return Promise.reject(new Error("Cannot derive EML preview URL from: " + fileUrl));
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
    files?: File[];
  }
) => {
  const form = new FormData();
  form.append("employee_pk", body.employee_pk);
  form.append("month", String(body.month));
  form.append("year", String(body.year));
  form.append("buckets", JSON.stringify(body.buckets));
  if (body.note) form.append("note", body.note);
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
export const uploadTimesheets = (files: File[], onProgress?: (pct: number) => void) => {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  return api
    .post<UploadResult[]>("/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) =>
        onProgress && e.total ? onProgress(Math.round((e.loaded / e.total) * 100)) : undefined,
    })
    .then((r) => r.data);
};

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
  status: "authenticated" | "otp_required" | "totp_required" | "totp_enrollment_required";
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

export const authLogin = (username: string, password: string, captcha_id: string, captcha_answer: string) =>
  api.post<LoginResult>("/auth/login", { username, password, captcha_id, captcha_answer, fingerprint: fp() }).then((r) => r.data);

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
export const adminSwitchAuthMode = (id: string, mode: AuthModeT) =>
  api.post<AuthUser>(`/admin/users/${id}/auth-mode`, null, { params: { mode } }).then((r) => r.data);
export const adminTotpSetup = (id: string) =>
  api.post<TotpSetupResult>(`/admin/users/${id}/totp-setup`).then((r) => r.data);
export const adminDeleteUser = (id: string) => api.delete(`/admin/users/${id}`).then((r) => r.data);

// ===========================================================================
// Admin — config
// ===========================================================================
export interface ConfigItem {
  key: string;
  value: unknown;
  category: "provider" | "model" | "prompt" | "general";
  is_secret: boolean;
}
export interface ProviderTestResult {
  ok: boolean; provider: string; model: string;
  latency_ms?: number | null; reply?: string | null; error?: string | null;
}
export const adminGetConfig = () => api.get<ConfigItem[]>("/admin/config").then((r) => r.data);
export const adminUpdateConfig = (values: Record<string, unknown>) =>
  api.put<ConfigItem[]>("/admin/config", { values }).then((r) => r.data);
export const adminRevealSecret = (key: string) =>
  api.get<{ key: string; value: string }>(`/admin/config/reveal/${key}`).then((r) => r.data.value);
export const adminTestConfig = (provider?: string, prompt?: string) =>
  api.post<ProviderTestResult>("/admin/config/test", { provider, prompt: prompt || "Reply with the single word: OK" }).then((r) => r.data);
export const adminPromptDefaults = () =>
  api.get<Record<string, string>>("/admin/config/prompts/defaults").then((r) => r.data);

/** Provider → the secret/base-URL config keys + common model choices, so the
 *  AI Settings page only shows the fields for the active provider. Model lists
 *  are suggestions — the inputs remain editable for anything not listed. */
export const AI_PROVIDERS = {
  openai: {
    label: "OpenAI",
    keyField: "openai_api_key",
    baseField: "openai_base_url",
    extractionModels: ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
    validationModels: ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
  },
  deepseek: {
    label: "DeepSeek",
    keyField: "deepseek_api_key",
    baseField: "deepseek_base_url",
    extractionModels: ["deepseek-chat", "deepseek-reasoner"],
    validationModels: ["deepseek-chat", "deepseek-reasoner"],
  },
} as const;
export type AiProviderId = keyof typeof AI_PROVIDERS;
