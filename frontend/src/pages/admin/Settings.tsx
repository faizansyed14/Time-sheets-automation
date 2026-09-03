/**
 * Admin → AI Settings.
 *
 * Models, keys, providers and prompts come from .env / built-in code only —
 * restart the backend after changing .env. The one thing actually
 * configurable here is Ask AI's access toggle (a DB-backed setting, not env).
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Cpu, HeartPulse, HelpCircle, Users, XCircle } from "lucide-react";
import {
  adminConfigStatus, checkSystemHealthNow, fetchChatAccess, fetchSystemHealth, updateChatAccess,
  type HealthStatusValue, type SystemHealthRow,
} from "../../api/client";
import { Badge, Button, Card, PageHeader, Skeleton } from "../../components/ui";
import { useToast } from "../../components/toast";
import { formatUaeDateTime } from "../../lib/utils";

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

/* --------------------------- health status pill --------------------------- */
const HEALTH_TONE: Record<HealthStatusValue, "success" | "warning" | "danger" | "slate"> = {
  ok: "success", degraded: "warning", down: "danger", unknown: "slate",
};
const HEALTH_ICON: Record<HealthStatusValue, typeof CheckCircle2> = {
  ok: CheckCircle2, degraded: AlertTriangle, down: XCircle, unknown: HelpCircle,
};
const HEALTH_LABEL: Record<string, string> = { llm: "LLM API", graph: "Microsoft Graph" };

function HealthRow({ row }: { row: SystemHealthRow }) {
  const Icon = HEALTH_ICON[row.status];
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold text-slate-800">{HEALTH_LABEL[row.component] ?? row.component}</p>
          <Badge tone={HEALTH_TONE[row.status]}><Icon className="h-3 w-3" />{row.status}</Badge>
        </div>
        {row.detail && <p className="mt-1 text-xs text-slate-500">{row.detail}</p>}
        <p className="mt-1 text-[11px] text-slate-400">
          {row.last_checked_at ? `Last checked ${formatUaeDateTime(row.last_checked_at)}` : "Never checked yet"}
        </p>
      </div>
    </div>
  );
}

export default function AdminSettings() {
  const { toast } = useToast();
  const qc = useQueryClient();

  const { data: status, isLoading } = useQuery({
    queryKey: ["admin-config-status"],
    queryFn: adminConfigStatus,
    refetchInterval: 15000,
  });

  const { data: access, isLoading: accessLoading } = useQuery({
    queryKey: ["chat-access"],
    queryFn: fetchChatAccess,
  });

  const toggleAccess = useMutation({
    mutationFn: updateChatAccess,
    onSuccess: (row) => {
      qc.setQueryData(["chat-access"], row);
      toast("success", row.enabled_for_others ? "Ask AI opened up" : "Ask AI restricted to admin",
        row.enabled_for_others
          ? "Every signed-in user (except viewer, who is read-only everywhere) can now use Ask AI."
          : "Only admin accounts can use Ask AI now.");
    },
    onError: (e: any) => toast("error", "Couldn't update", e?.response?.data?.detail ?? String(e)),
  });

  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ["system-health"],
    queryFn: fetchSystemHealth,
    refetchInterval: 60000,
  });

  const checkNow = useMutation({
    mutationFn: checkSystemHealthNow,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["system-health"] });
      const broken = Object.values(result).filter((r: any) => r.status === "down" || r.status === "degraded");
      toast(broken.length ? "error" : "success",
        broken.length ? "Something needs attention" : "All checked components look healthy",
        broken.length ? broken.map((r: any) => r.detail).join(" ") : undefined);
    },
    onError: (e: any) => toast("error", "Check failed", e?.response?.data?.detail ?? String(e)),
  });

  return (
    <div className="mx-auto max-w-3xl animate-fade-up">
      <PageHeader
        title="AI Settings"
        subtitle="OpenAI only. All models, keys and tuning live in .env — edit the file and restart the backend."
      />

      <Card className="mb-5 p-5">
        <h2 className="mb-1 flex items-center gap-2 text-sm font-bold text-slate-800">
          <Cpu className="h-4 w-4 text-slate-400" /> Active configuration
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          Resolved from the running process (loaded from <code className="rounded bg-slate-100 px-1 font-mono text-[11px]">.env</code> at startup).
        </p>
        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && (
          <div className="space-y-2">
            {(status ?? []).map((svc) => (
              <div
                key={svc.kind}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-slate-800">{svc.label}</p>
                  {svc.note && <p className="mt-1 text-[11px] text-brand-700">{svc.note}</p>}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  <Badge tone="brand">OpenAI</Badge>
                  <code className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-700">
                    {svc.model || "—"}
                  </code>
                  {!svc.has_key && (
                    <Badge tone="warning">no API key in .env</Badge>
                  )}
                </div>
              </div>
            ))}
            {!status?.length && (
              <p className="text-sm text-slate-500">No AI services configured.</p>
            )}
          </div>
        )}
      </Card>

      <Card className="p-5">
        <h2 className="mb-1 flex items-center gap-2 text-sm font-bold text-slate-800">
          <Users className="h-4 w-4 text-slate-400" /> Ask AI access
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          Admin can always use Ask AI. Turn this on to open it up to everyone else too
          (viewer stays read-only, same as the rest of the app).
        </p>
        {accessLoading ? (
          <Skeleton className="h-14 w-full" />
        ) : (
          <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-slate-800">Open Ask AI to non-admin users</p>
              <p className="mt-0.5 text-xs text-slate-500">
                {access?.enabled_for_others
                  ? "Everyone (except viewer) can use Ask AI right now."
                  : "Only admin can use Ask AI right now."}
              </p>
            </div>
            <Switch
              on={!!access?.enabled_for_others}
              disabled={toggleAccess.isPending}
              onChange={() => toggleAccess.mutate(!access?.enabled_for_others)}
            />
          </div>
        )}
      </Card>

      <Card className="mt-5 p-5">
        <div className="mb-1 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-bold text-slate-800">
            <HeartPulse className="h-4 w-4 text-slate-400" /> System health
          </h2>
          <Button variant="secondary" onClick={() => checkNow.mutate()} disabled={checkNow.isPending}>
            {checkNow.isPending ? "Checking…" : "Check now"}
          </Button>
        </div>
        <p className="mb-3 text-xs text-slate-500">
          Checked automatically every few hours — an expired key/credit exhaustion or a broken
          Microsoft Graph credential emails an admin the moment either happens, instead of
          extractions/OTP/reminders silently failing one by one.
        </p>
        {healthLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <div className="space-y-2">
            {(health ?? []).map((row) => <HealthRow key={row.component} row={row} />)}
            {!health?.length && (
              <p className="text-sm text-slate-500">Not checked yet — click "Check now".</p>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
