import axios from "axios";

export const api = axios.create({ baseURL: "/api/v1" });

// ---- types ----
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
}

export interface EmailDetail extends EmailListItem {
  body_text: string | null;
  attachments: Attachment[];
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
}

export interface DashboardRow {
  employee_pk: string | null;
  employee_id: string | null;
  employee_name: string | null;
  account_manager: string | null;
  dco_number: string | null;
  status: "green" | "yellow";
  record_count: number;
  needs_review_count: number;
  pending_approval_count: number;
  years: number[];
}

// ---- inbox ----
export const fetchInbox = (q: string, status: string) =>
  api.get<EmailListItem[]>("/inbox", { params: { q: q || undefined, status: status || undefined } }).then((r) => r.data);

export const fetchEmail = (id: string) =>
  api.get<EmailDetail>(`/inbox/${id}`).then((r) => r.data);

export const decideEmail = (id: string, accepted: boolean) =>
  api.post(`/inbox/${id}/decision`, { accepted }).then((r) => r.data);

export const restoreEmail = (id: string) =>
  api.post(`/inbox/${id}/restore`).then((r) => r.data);

export const rerunExtraction = (id: string) =>
  api.post(`/inbox/${id}/rerun`).then((r) => r.data);

export const attachmentUrl = (msgId: string, attId: string) =>
  `/api/v1/inbox/${msgId}/attachments/${attId}`;

// ---- dashboard / employees ----
export const fetchDashboard = (year?: number) =>
  api.get<DashboardRow[]>("/employees", { params: { year } }).then((r) => r.data);

export const fetchEmployeeRecords = (pk: string, year?: number) =>
  api.get<TimesheetRecord[]>(`/employees/${pk}/records`, { params: { year } }).then((r) => r.data);

// ---- timesheets ----
export const approveRecord = (id: string, approved: boolean) =>
  api.post<TimesheetRecord>(`/timesheets/${id}/approve`, { approved }).then((r) => r.data);

export const verifyRecord = (id: string) =>
  api.post<TimesheetRecord>(`/timesheets/${id}/verify`).then((r) => r.data);

export const deleteRecord = (id: string) =>
  api.delete(`/timesheets/${id}`).then((r) => r.data);

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

// ---- files / folders (3-level: Manager → Employee → Month) ----
export interface ManagerFolder { name: string; rel_path: string; employee_count: number; }
export interface EmployeeFolder { name: string; rel_path: string; month_count: number; }
export interface MonthFolder { name: string; rel_path: string; file_count: number; }
export interface FileItem { name: string; rel_path: string; size: number; content_type: string; }

export const listFileManagers = () => api.get<ManagerFolder[]>("/files/managers").then((r) => r.data);
export const listFileEmployees = (manager: string) =>
  api.get<EmployeeFolder[]>(`/files/managers/${encodeURIComponent(manager)}/employees`).then((r) => r.data);
export const listFileMonths = (manager: string, emp: string) =>
  api.get<MonthFolder[]>(`/files/managers/${encodeURIComponent(manager)}/employees/${encodeURIComponent(emp)}/months`).then((r) => r.data);
export const listFileItems = (manager: string, emp: string, month: string) =>
  api.get<FileItem[]>(`/files/managers/${encodeURIComponent(manager)}/employees/${encodeURIComponent(emp)}/months/${encodeURIComponent(month)}/items`).then((r) => r.data);

export const fileContentUrl = (relPath: string) => `/api/v1/files/content?rel_path=${encodeURIComponent(relPath)}`;
export const downloadZipUrl = (manager?: string) =>
  `/api/v1/files/download-zip${manager ? `?manager=${encodeURIComponent(manager)}` : ""}`;

export const createFileManager = (name: string) => api.post("/files/managers", { name }).then((r) => r.data);
export const createFileEmployee = (manager: string, name: string) =>
  api.post(`/files/managers/${encodeURIComponent(manager)}/employees`, { name }).then((r) => r.data);
export const createFileMonth = (manager: string, emp: string, month_label: string) =>
  api.post(`/files/managers/${encodeURIComponent(manager)}/employees/${encodeURIComponent(emp)}/months`, { month_label }).then((r) => r.data);
export const renameFolder = (rel_path: string, new_name: string) =>
  api.patch("/files/folder", { rel_path, new_name }).then((r) => r.data);
export const deleteFolder = (relPath: string) =>
  api.delete("/files/folder", { params: { rel_path: relPath } }).then((r) => r.data);

// ---- employee_matcher (all_employee_data) ----
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
export const createEmployee = (e: EmployeeInput) => api.post<Employee>("/employee-matcher", e).then((r) => r.data);
export const updateEmployee = (id: string, e: EmployeeInput) => api.put<Employee>(`/employee-matcher/${id}`, e).then((r) => r.data);
export const deleteEmployee = (id: string) => api.delete(`/employee-matcher/${id}`).then((r) => r.data);

export interface ImportSummary { inserted: number; updated: number; skipped: number; }
export const importEmployees = (file: File) => {
  const form = new FormData();
  form.append("file", file, file.name);
  return api.post<ImportSummary>("/employee-matcher/import", form, { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data);
};

// ---- upload ----
export interface UploadResult {
  record_id: string;
  employee_name: string | null;
  employee_id: string | null;
  month: number;
  year: number;
  validation_status: "verified" | "manual_review";
  llm_summary: string | null;
  match_note: string | null;
}
export const uploadTimesheets = (files: File[]) => {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  return api.post<UploadResult[]>("/upload", form, { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data);
};

export const MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const MONTHS_LONG = ["", "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];
