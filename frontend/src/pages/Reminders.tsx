import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, BellRing, Send, FlaskConical, FileDown, PlayCircle, Search,
  CheckCircle2, XCircle, MinusCircle, History,
} from "lucide-react";
import {
  fetchReminderConfig, updateReminderConfig,
  fetchReminderEmployees, reminderExportUrl, sendReminderNow, sendTestReminder,
  runReminderBatch, fetchReminderRuns, fetchReminderRun,
  MONTHS_LONG, type EmailPreference, type ReminderEmployeeRow, type ReminderRun,
} from "../api/client";
import { Badge, Button, Card, ComingSoon, EmptyState, Field, Input, Modal, PageHeader, Select, Skeleton, Spinner } from "../components/ui";
import { useToast } from "../components/toast";
import { useAuth } from "../lib/auth";
import { downloadFile } from "../lib/filePreview";
import { cn, formatUaeDateTime } from "../lib/utils";

const now = new Date();

/* ------------------------------- toggle switch ------------------------------- */
function Switch({ on, onChange, disabled }: { on: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={onChange}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${
        on ? "bg-brand-600" : "bg-slate-300"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          on ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

/* ---------------------------- last-reminder status pill ---------------------------- */
function LastStatusPill({ row }: { row: ReminderEmployeeRow }) {
  if (!row.last_status) return <span className="text-xs text-slate-300">Never sent</span>;
  if (row.last_status === "sent") {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700">
        <CheckCircle2 className="h-3.5 w-3.5" /> Sent {formatUaeDateTime(row.last_sent_at)}
      </span>
    );
  }
  if (row.last_status === "skipped") {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-500" title={row.last_error ?? ""}>
        <MinusCircle className="h-3.5 w-3.5" /> Skipped (already sent)
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-rose-600" title={row.last_error ?? ""}>
      <XCircle className="h-3.5 w-3.5" /> Failed
    </span>
  );
}

/* --------------------------------- send-now modal --------------------------------- */
function SendNowModal({
  row, month, year, onClose, onDone,
}: {
  row: ReminderEmployeeRow; month: number; year: number; onClose: () => void; onDone: () => void;
}) {
  const { toast } = useToast();
  const [m, setM] = useState(month);
  const [y, setY] = useState(year);
  const [alreadySentAt, setAlreadySentAt] = useState<string | null>(null);

  useEffect(() => setAlreadySentAt(null), [m, y]);

  const mut = useMutation({
    mutationFn: (force: boolean) => sendReminderNow(row.employee_pk, m, y, force),
    onSuccess: (log) => {
      if (log.status === "sent") {
        toast("success", "Reminder sent", `${row.name} — ${MONTHS_LONG[m]} ${y}`);
      } else {
        toast("error", "Could not send", log.error ?? "The send failed.");
      }
      onDone();
    },
    onError: (e: any) => {
      if (e?.response?.status === 409) {
        setAlreadySentAt(e.response.data?.detail?.sent_at ?? null);
      } else {
        toast("error", "Send failed", e?.response?.data?.detail ?? String(e));
      }
    },
  });

  return (
    <Modal open onClose={onClose} title={`Send reminder to ${row.name}`} subtitle={row.email ?? "No email address on file — this will fail."}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Month">
          <Select value={m} onChange={(e) => setM(Number(e.target.value))}>
            {MONTHS_LONG.slice(1).map((label, i) => <option key={label} value={i + 1}>{label}</option>)}
          </Select>
        </Field>
        <Field label="Year">
          <Input type="number" value={y} onChange={(e) => setY(Number(e.target.value))} />
        </Field>
      </div>

      {alreadySentAt && (
        <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
          Already sent to {row.name} on {formatUaeDateTime(alreadySentAt)}. Send again?
        </div>
      )}

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button disabled={!row.email || mut.isPending} onClick={() => mut.mutate(!!alreadySentAt)}>
          <Send className="h-4 w-4" /> {alreadySentAt ? "Send anyway" : "Send"}
        </Button>
      </div>
    </Modal>
  );
}

/* ----------------------------------- run detail ----------------------------------- */
function RunDetailModal({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run, isLoading } = useQuery({
    queryKey: ["reminder-run", runId],
    queryFn: () => fetchReminderRun(runId),
  });

  return (
    <Modal open onClose={onClose} wide
      title="Run details"
      subtitle={run ? `${MONTHS_LONG[run.month]} ${run.year} · ${run.trigger === "scheduled" ? "Automatic 28th run" : "Manual run"}` : undefined}
    >
      {isLoading || !run ? (
        <Skeleton className="h-40" />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-4 gap-3 text-center">
            <div className="rounded-lg bg-slate-50 p-3"><p className="text-lg font-bold text-slate-800">{run.total}</p><p className="text-[11px] text-slate-500">Total</p></div>
            <div className="rounded-lg bg-emerald-50 p-3"><p className="text-lg font-bold text-emerald-700">{run.sent_count}</p><p className="text-[11px] text-emerald-700/80">Sent</p></div>
            <div className="rounded-lg bg-rose-50 p-3"><p className="text-lg font-bold text-rose-700">{run.failed_count}</p><p className="text-[11px] text-rose-700/80">Failed</p></div>
            <div className="rounded-lg bg-slate-100 p-3"><p className="text-lg font-bold text-slate-600">{run.skipped_count}</p><p className="text-[11px] text-slate-500">Skipped</p></div>
          </div>
          <div className="max-h-96 overflow-y-auto rounded-lg border border-slate-100">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-white">
                <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wide text-slate-400">
                  <th className="px-3 py-2 font-semibold">Employee</th>
                  <th className="px-3 py-2 font-semibold">Email</th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                  <th className="px-3 py-2 font-semibold">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {run.logs.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-6 text-center text-slate-400">No one was missing this month — nothing to send.</td></tr>
                )}
                {run.logs.map((l) => (
                  <tr key={l.id}>
                    <td className="px-3 py-2 font-medium text-slate-800">{l.employee_name}</td>
                    <td className="px-3 py-2 text-slate-500">{l.recipient_email ?? "—"}</td>
                    <td className="px-3 py-2">
                      <Badge tone={l.status === "sent" ? "success" : l.status === "failed" ? "danger" : "slate"}>
                        {l.status}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">{l.error ?? (l.sent_at ? formatUaeDateTime(l.sent_at) : "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}

/* ------------------------------------- page ------------------------------------- */
export default function RemindersPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { canWrite, isAdmin } = useAuth();

  const [month, setMonth] = useState(now.getMonth() + 1);
  const [year, setYear] = useState(now.getFullYear());
  const [q, setQ] = useState("");
  const [sendRow, setSendRow] = useState<ReminderEmployeeRow | null>(null);
  const [viewRunId, setViewRunId] = useState<string | null>(null);
  const [pollingRunId, setPollingRunId] = useState<string | null>(null);

  const [testEmail, setTestEmail] = useState("");
  const [testName, setTestName] = useState("");

  const { data: config } = useQuery({ queryKey: ["reminder-config"], queryFn: fetchReminderConfig, enabled: isAdmin });

  const toggleMut = useMutation({
    mutationFn: (enabled: boolean) => updateReminderConfig({ auto_send_enabled: enabled }),
    onSuccess: (cfg) => {
      toast(cfg.auto_send_enabled ? "success" : "info",
        cfg.auto_send_enabled ? "Automatic reminders turned on" : "Automatic reminders turned off",
        cfg.auto_send_enabled
          ? `Every ${cfg.send_day}th of the month at ${cfg.send_hour_uae}:00 AM (UAE), anyone missing that month's timesheet will be emailed.`
          : "The 28th/9am UAE run will not send anything until this is turned back on.");
      qc.invalidateQueries({ queryKey: ["reminder-config"] });
    },
    onError: (e: any) => toast("error", "Could not update", e?.response?.data?.detail ?? String(e)),
  });

  const emailPrefMut = useMutation({
    mutationFn: (email_preference: EmailPreference) => updateReminderConfig({ email_preference }),
    onSuccess: (cfg) => {
      toast("success", `Now sending to ${cfg.email_preference} email`,
        "Falls back to the other address for anyone missing their preferred one.");
      qc.invalidateQueries({ queryKey: ["reminder-config"] });
      qc.invalidateQueries({ queryKey: ["reminder-employees"] });
    },
    onError: (e: any) => toast("error", "Could not update", e?.response?.data?.detail ?? String(e)),
  });

  const { data: employees, isLoading: employeesLoading } = useQuery({
    queryKey: ["reminder-employees", month, year, q],
    // Reminders only ever needs the missing set — that's the whole point of
    // the page (who to send to) — so this is never toggled off.
    queryFn: () => fetchReminderEmployees({ month, year, q: q || undefined, only_missing: true, limit: 500 }),
    enabled: isAdmin,
  });

  const invalidateAfterSend = () => {
    qc.invalidateQueries({ queryKey: ["reminder-employees"] });
    qc.invalidateQueries({ queryKey: ["reminder-runs"] });
  };

  const testMut = useMutation({
    mutationFn: () => sendTestReminder(testEmail.trim(), month, year, testName.trim() || "Test Employee"),
    onSuccess: (log) => {
      if (log.status === "sent") {
        toast("success", "Test email sent", `Sent to ${log.recipient_email} using the ${MONTHS_LONG[month]} ${year} reminder design.`);
        setTestEmail("");
      } else {
        toast("error", "Test send failed", log.error ?? "Unknown error");
      }
    },
    onError: (e: any) => toast("error", "Test send failed", e?.response?.data?.detail ?? String(e)),
  });

  const runBatchMut = useMutation({
    mutationFn: () => runReminderBatch(month, year),
    onSuccess: ({ run_id }) => setPollingRunId(run_id),
    onError: (e: any) => toast("error", "Could not start run", e?.response?.data?.detail ?? String(e)),
  });

  const { data: polling } = useQuery({
    queryKey: ["reminder-run", pollingRunId],
    queryFn: () => fetchReminderRun(pollingRunId!),
    enabled: isAdmin && !!pollingRunId,
    refetchInterval: (q) => (q.state.data?.finished_at ? false : 1200),
  });

  useEffect(() => {
    if (polling?.finished_at && pollingRunId) {
      toast("success", "Run finished",
        `${polling.sent_count} sent, ${polling.failed_count} failed, ${polling.skipped_count} already sent — ${MONTHS_LONG[polling.month]} ${polling.year}.`);
      setPollingRunId(null);
      invalidateAfterSend();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polling?.finished_at]);

  const { data: runs } = useQuery({ queryKey: ["reminder-runs"], queryFn: () => fetchReminderRuns(10), enabled: isAdmin });

  const rows = employees?.rows ?? [];

  if (!isAdmin) {
    return (
      <div className="animate-fade-up">
        <PageHeader
          title="Reminders"
          subtitle="Sends real emails to real employees, so this stays admin-only."
        />
        <ComingSoon feature="Reminders" />
      </div>
    );
  }

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Reminders"
        subtitle="An automatic nudge on the 28th of every month at 9:00 AM (UAE) to anyone missing that month's timesheet, plus on-demand sends."
        actions={
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2 shadow-xs">
              <div className="text-right">
                <p className="text-xs font-semibold text-slate-700">Send to</p>
                <p className="text-[11px] text-slate-400">falls back if blank</p>
              </div>
              <div className="flex rounded-lg bg-slate-100 p-0.5">
                {(["work", "personal"] as EmailPreference[]).map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    disabled={!canWrite || emailPrefMut.isPending}
                    onClick={() => emailPrefMut.mutate(opt)}
                    className={cn(
                      "rounded-md px-2.5 py-1 text-xs font-semibold capitalize transition-colors disabled:cursor-not-allowed",
                      config?.email_preference === opt
                        ? "bg-white text-brand-700 shadow-xs"
                        : "text-slate-500 hover:text-slate-700"
                    )}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2 shadow-xs">
              <div className="text-right">
                <p className="text-xs font-semibold text-slate-700">Automatic reminders</p>
                <p className="text-[11px] text-slate-400">28th · 9:00 AM UAE</p>
              </div>
              <Switch on={!!config?.auto_send_enabled} disabled={!canWrite || toggleMut.isPending}
                onChange={() => toggleMut.mutate(!config?.auto_send_enabled)} />
            </div>
          </div>
        }
      />

      {config && !config.sending_enabled && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <span className="font-semibold">Sending is disabled by the backend</span> (REMINDER_SENDING_ENABLED=false
            in .env). Nothing on this page can actually email right now — not the scheduled run, not "Send now",
            not "Run for all now", not "Test email" — regardless of the toggle above. An admin edits .env and
            restarts the backend to change this.
          </span>
        </div>
      )}
      {config && config.sending_enabled && !config.scheduled_check_enabled && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <span className="font-semibold">The automatic scheduler is disabled by the backend</span>
            (REMINDER_SCHEDULED_CHECK_ENABLED=false in .env) — the 28th/9am UAE run will never fire on this box,
            no matter what the "Automatic reminders" toggle above says. Manual sends ("Send now", "Run for all now",
            "Test email") are unaffected and will still send.
          </span>
        </div>
      )}

      <Card className="mb-5 p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Month">
              <Select value={month} onChange={(e) => setMonth(Number(e.target.value))}>
                {MONTHS_LONG.slice(1).map((label, i) => <option key={label} value={i + 1}>{label}</option>)}
              </Select>
            </Field>
            <Field label="Year">
              <Input type="number" className="w-24" value={year} onChange={(e) => setYear(Number(e.target.value))} />
            </Field>
            <Field label="Search">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <Input className="w-56 pl-8" placeholder="Name, ID or manager…" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
            </Field>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => downloadFile(
                reminderExportUrl(month, year),
                `missing_timesheets_${year}-${String(month).padStart(2, "0")}.xlsx`,
              )}
            >
              <FileDown className="h-4 w-4" />
              Export missing
            </Button>
            {canWrite && (
              <Button
                disabled={runBatchMut.isPending || !!pollingRunId}
                onClick={() => {
                  if (!confirm(`Send the ${MONTHS_LONG[month]} ${year} reminder email to every active employee who hasn't submitted yet (and hasn't already been reminded)?`)) return;
                  runBatchMut.mutate();
                }}
              >
                {pollingRunId ? <Spinner className="h-4 w-4 border-t-brand-600" /> : <PlayCircle className="h-4 w-4" />}
                {pollingRunId ? `Sending… ${polling ? polling.sent_count + polling.failed_count + polling.skipped_count : 0}/${polling?.total ?? "?"}` : "Run for all now"}
              </Button>
            )}
          </div>
        </div>
      </Card>

      <Card className="mb-5">
        {!employeesLoading && rows.length > 0 && (
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-2.5">
            <p className="text-xs text-slate-500">
              {rows.length} missing employee{rows.length === 1 ? "" : "s"} · {MONTHS_LONG[month]} {year}
            </p>
          </div>
        )}
        {employeesLoading ? (
          <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : rows.length === 0 ? (
          <EmptyState icon={<BellRing className="h-6 w-6" />} title="Nobody matches this view"
            detail="No active employee is missing this month's timesheet." />
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wide text-slate-400">
                <th className="px-5 py-2.5 font-semibold">Employee</th>
                <th className="px-3 py-2.5 font-semibold">Manager</th>
                <th className="px-3 py-2.5 font-semibold">
                  Email <span className="normal-case text-slate-300">({config?.email_preference ?? "work"} preferred)</span>
                </th>
                <th className="px-3 py-2.5 font-semibold">{MONTHS_LONG[month]} {year}</th>
                <th className="px-3 py-2.5 font-semibold">Last reminder</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => (
                <tr key={row.employee_pk} className="hover:bg-slate-50">
                  <td className="px-5 py-2.5">
                    <p className="font-semibold text-slate-800">{row.name}</p>
                    <p className="text-xs text-slate-400">{row.employee_id}</p>
                  </td>
                  <td className="px-3 py-2.5 text-slate-600">{row.account_manager ?? "—"}</td>
                  <td className="px-3 py-2.5 text-slate-600">
                    {row.email ? (
                      <>
                        {row.email}
                        {row.email_source !== config?.email_preference && (
                          <span
                            className="ml-1.5 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-700 ring-1 ring-inset ring-amber-200"
                            title={
                              row.email_source === "legacy"
                                ? "No work/personal split on file yet — showing the older resolved address."
                                : `No ${config?.email_preference} email on file — showing ${row.email_source} instead.`
                            }
                          >
                            {row.email_source}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-rose-500">No email on file</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge tone={row.missing ? "warning" : "success"}>{row.missing ? "Missing" : "Received"}</Badge>
                  </td>
                  <td className="px-3 py-2.5"><LastStatusPill row={row} /></td>
                  <td className="px-3 py-2.5 text-right">
                    {canWrite && (
                      <Button size="sm" variant="secondary" onClick={() => setSendRow(row)}>
                        <Send className="h-3.5 w-3.5" /> Send now
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {canWrite && (
          <Card className="p-5">
            <div className="mb-3 flex items-center gap-2">
              <FlaskConical className="h-4 w-4 text-brand-600" />
              <h3 className="text-sm font-bold text-slate-800">Send a test email</h3>
            </div>
            <p className="mb-4 text-xs text-slate-500">
              Sends the exact reminder design (for {MONTHS_LONG[month]} {year}, the month/year selected above) to any
              address — no employee record, no duplicate-prevention, always sends.
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Test email address">
                <Input className="w-64" type="email" placeholder="you@example.com" value={testEmail}
                  onChange={(e) => setTestEmail(e.target.value)} />
              </Field>
              <Field label="Display name (optional)">
                <Input className="w-48" placeholder="Test Employee" value={testName}
                  onChange={(e) => setTestName(e.target.value)} />
              </Field>
              <Button disabled={!testEmail.trim() || testMut.isPending} onClick={() => testMut.mutate()}>
                <Send className="h-4 w-4" /> Send test
              </Button>
            </div>
          </Card>
        )}

        <Card className="p-5">
          <div className="mb-3 flex items-center gap-2">
            <History className="h-4 w-4 text-brand-600" />
            <h3 className="text-sm font-bold text-slate-800">Recent runs</h3>
          </div>
          {!runs?.length ? (
            <p className="text-xs text-slate-400">No automatic or "run for all" batch has happened yet.</p>
          ) : (
            <div className="space-y-1.5">
              {runs.map((run: ReminderRun) => (
                <button key={run.id} onClick={() => setViewRunId(run.id)}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs hover:bg-slate-50">
                  <span className="font-medium text-slate-700">
                    {MONTHS_LONG[run.month]} {run.year} · {run.trigger === "scheduled" ? "Automatic" : `Manual (${run.triggered_by ?? "—"})`}
                  </span>
                  <span className="flex items-center gap-2 text-slate-400">
                    {run.finished_at ? (
                      <>
                        <span className="text-emerald-600">{run.sent_count} sent</span>
                        {run.failed_count > 0 && <span className="text-rose-500">{run.failed_count} failed</span>}
                        {run.skipped_count > 0 && <span>{run.skipped_count} skipped</span>}
                      </>
                    ) : (
                      <Spinner className="h-3.5 w-3.5" />
                    )}
                    <span>{formatUaeDateTime(run.started_at)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </Card>
      </div>

      {sendRow && (
        <SendNowModal row={sendRow} month={month} year={year}
          onClose={() => setSendRow(null)}
          onDone={() => { setSendRow(null); invalidateAfterSend(); }} />
      )}
      {viewRunId && <RunDetailModal runId={viewRunId} onClose={() => setViewRunId(null)} />}
    </div>
  );
}
