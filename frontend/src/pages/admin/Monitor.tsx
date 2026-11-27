import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { fetchMonitor, type MonitorEvent, type MonitorRow, type MonitorSnapshot } from "../../api/client";
import { Badge, Card, PageHeader, Select, Skeleton } from "../../components/ui";
import { cn } from "../../lib/utils";

const WINDOWS = [
  { m: 15, label: "Last 15 min" },
  { m: 60, label: "Last hour" },
  { m: 360, label: "Last 6 hours" },
  { m: 1440, label: "Last 24 hours" },
  { m: 10080, label: "Last 7 days" },
];

const fmtMs = (ms: number | null | undefined) =>
  ms == null ? "-" : ms >= 60000 ? `${(ms / 60000).toFixed(1)} min` : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
const fmtNum = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString());
const fmtUsd = (n: number) => (n > 0 ? `$${n < 1 ? n.toFixed(4) : n.toFixed(2)}` : "-");
const fmtAge = (s: number) => (s < 120 ? `${Math.round(s)}s` : s < 7200 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`);
const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** What each LLM step is, in plain words. */
const STEP_NAMES: Record<string, string> = {
  pass1: "Pass 1 · classify items",
  "pass1-rescue": "Pass 1 · second look",
  pass2: "Pass 2 · read the days",
  "pass2-recover": "Pass 2 · recover a sheet",
  alternates: "Compare duplicate copies",
  roster: "Bulk roster",
  "roster-census": "Bulk roster · headcount",
  "roster-single": "Bulk roster · single call",
  "attendance-report": "Attendance report",
  portal: "Employee portal",
};

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "bad" | "warn" | "good" }) {
  return (
    <Card className="p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div
        className={cn(
          "mt-1 text-2xl font-bold tabular-nums text-slate-900",
          tone === "bad" && "text-rose-700",
          tone === "warn" && "text-amber-700"
        )}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </Card>
  );
}

/** One series, one axis: a thin bar per period, hover shows the exact value. */
function BarStrip({
  title,
  points,
  color,
  fmt,
}: {
  title: string;
  points: { t: number; v: number }[];
  color: string;
  fmt: (v: number) => string;
}) {
  const max = Math.max(1, ...points.map((p) => p.v));
  const total = points.reduce((a, p) => a + p.v, 0);
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between text-xs">
        <span className="font-semibold text-slate-700">{title}</span>
        <span className="tabular-nums text-slate-500">max {fmt(max === 1 && total === 0 ? 0 : max)} per bar</span>
      </div>
      <div className="flex h-16 items-end gap-[2px] border-b border-slate-200" role="img" aria-label={`${title}: ${fmt(total)} in total`}>
        {points.map((p) => (
          <div key={p.t} className="group relative flex h-full flex-1 items-end" title={`${fmtTime(p.t)} · ${fmt(p.v)}`}>
            <div
              className={cn("w-full rounded-t-[3px]", color)}
              style={{ height: p.v ? `${Math.max(4, (p.v / max) * 100)}%` : 0 }}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] tabular-nums text-slate-400">
        <span>{points[0] ? fmtTime(points[0].t) : ""}</span>
        <span>now</span>
      </div>
    </div>
  );
}

function RowsTable({ rows, nameOf, cost, empty }: { rows: MonitorRow[]; nameOf: (r: MonitorRow) => string; cost?: boolean; empty: string }) {
  if (!rows.length) return <p className="px-4 py-6 text-center text-sm text-slate-400">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-slate-100 text-left text-[11px] uppercase tracking-wide text-slate-400">
            <th className="px-4 py-2 font-semibold">Name</th>
            <th className="px-2 py-2 text-right font-semibold">Calls</th>
            <th className="px-2 py-2 text-right font-semibold">Failed</th>
            <th className="px-2 py-2 text-right font-semibold">Typical</th>
            <th className="px-2 py-2 text-right font-semibold">Slowest 5%</th>
            {cost && <th className="px-2 py-2 text-right font-semibold">Tokens in / out</th>}
            {cost && <th className="px-4 py-2 text-right font-semibold">Cost</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.role}|${r.model}`} className="border-b border-slate-50 last:border-0">
              <td className="px-4 py-1.5 text-slate-700">
                {nameOf(r)}
                {r.model && r.model !== "*" && r.model !== "-" && r.model !== "celery" && r.model !== "graph" && (
                  <span className="ml-1.5 text-[10px] text-slate-400">{r.model}</span>
                )}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(r.calls)}</td>
              <td className={cn("px-2 py-1.5 text-right tabular-nums", r.errors ? "font-semibold text-rose-700" : "text-slate-400")}>
                {r.errors || "0"}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums">{fmtMs(r.p50_ms)}</td>
              <td className="px-2 py-1.5 text-right tabular-nums">{fmtMs(r.p95_ms)}</td>
              {cost && (
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">
                  {fmtNum(r.tokens_in)} / {fmtNum(r.tokens_out)}
                </td>
              )}
              {cost && <td className="px-4 py-1.5 text-right tabular-nums">{fmtUsd(r.cost_usd)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EventList({ events, empty }: { events: MonitorEvent[]; empty: string }) {
  if (!events.length) return <p className="px-4 py-6 text-center text-sm text-slate-400">{empty}</p>;
  return (
    <ul className="max-h-80 divide-y divide-slate-50 overflow-y-auto text-xs">
      {events.map((e, i) => {
        const label = String((e.detail as { label?: string })?.label ?? e.role);
        const err = (e.detail as { error?: string; http?: number }) ?? {};
        return (
          <li key={`${e.ts}-${i}`} className="flex items-start gap-2 px-4 py-1.5">
            <span className="w-16 shrink-0 tabular-nums text-slate-400">{fmtTime(e.ts)}</span>
            <Badge tone={e.status === "ok" ? "success" : e.status === "retry" ? "warning" : "danger"} className="shrink-0 px-1.5 py-0 text-[10px]">
              {e.status}
            </Badge>
            <span className="min-w-0 flex-1 truncate text-slate-700" title={label}>
              <span className="text-slate-400">{e.kind} · </span>
              {label}
              {(err.error || err.http) && (
                <span className="block truncate text-rose-700" title={String(err.error ?? "")}>
                  {err.http ? `HTTP ${err.http} ` : ""}
                  {err.error ?? ""}
                </span>
              )}
            </span>
            <span className="shrink-0 tabular-nums text-slate-500">{fmtMs(e.ms)}</span>
            {e.kind === "llm" && <span className="w-14 shrink-0 text-right tabular-nums text-slate-400">{fmtUsd(e.cost)}</span>}
          </li>
        );
      })}
    </ul>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-100 px-4 py-2.5">
        <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
        {hint && <p className="text-[11px] text-slate-500">{hint}</p>}
      </div>
      {children}
    </Card>
  );
}

const PROCESS_NAMES: Record<string, string> = {
  api: "Website / API",
  worker: "Background worker (mail sync, schedules)",
  "worker-extract": "Extraction worker",
};

function Body({ d }: { d: MonitorSnapshot }) {
  const llm = d.llm.totals;
  const online = d.processes.filter((p) => p.online);
  const kinds = new Set(online.map((p) => p.kind));
  const syncAge = d.sync.last_sync_at ? (Date.now() - new Date(d.sync.last_sync_at).getTime()) / 1000 : null;
  const failRate = llm.calls ? llm.errors / llm.calls : 0;

  return (
    <div className="space-y-4">
      {/* Alerts first: what needs attention */}
      {d.alerts.length ? (
        <Card className="divide-y divide-slate-100">
          {d.alerts.map((a, i) => (
            <div key={i} className="flex items-start gap-2 px-4 py-2.5 text-sm">
              {a.level === "critical" ? (
                <AlertOctagon className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />
              ) : (
                <AlertTriangle className={cn("mt-0.5 h-4 w-4 shrink-0", a.level === "serious" ? "text-orange-600" : "text-amber-600")} />
              )}
              <Badge tone={a.level === "warning" ? "warning" : "danger"} className="shrink-0 capitalize">{a.level}</Badge>
              <span className="text-slate-700">{a.text}</span>
            </div>
          ))}
        </Card>
      ) : (
        <Card className="flex items-center gap-2 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4" /> Everything looks healthy.
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="LLM calls" value={fmtNum(llm.calls)} sub={`${llm.errors} failed`} tone={failRate > 0.2 ? "bad" : failRate > 0.05 ? "warn" : undefined} />
        <Stat label="LLM cost" value={fmtUsd(llm.cost_usd)} sub={`${fmtNum(llm.tokens_in + llm.tokens_out)} tokens`} />
        <Stat label="LLM call time" value={fmtMs(llm.p50_ms)} sub={`slowest 5%: ${fmtMs(llm.p95_ms)}`} />
        <Stat
          label="Waiting to extract"
          value={d.queues.available ? fmtNum(d.queues.extract) : "-"}
          sub={d.queues.available ? `${d.queues.in_progress ?? 0} running now` : "queue unavailable"}
          tone={(d.queues.extract ?? 0) > 200 ? "warn" : undefined}
        />
        <Stat
          label="Workers online"
          value={`${kinds.has("worker") ? "Yes" : "No"} · ${kinds.has("worker-extract") ? "Yes" : "No"}`}
          sub="background · extraction"
          tone={!kinds.has("worker-extract") || !kinds.has("worker") ? "bad" : "good"}
        />
        <Stat
          label="Mail sync"
          value={d.sync.sync_error ? "Failing" : syncAge == null ? "Not yet" : `${fmtAge(syncAge)} ago`}
          sub={d.sync.sync_error ?? (d.sync.backfill_in_progress ? "first full download running" : "last good sync")}
          tone={d.sync.sync_error ? "bad" : syncAge != null && syncAge > 1800 ? "warn" : undefined}
        />
        <Stat
          label="Auto Extract"
          value={d.auto_extract.enabled ? "On" : "Off"}
          sub={`${d.auto_extract.state} · ${d.auto_extract.succeeded} ok · ${d.auto_extract.failed} failed`}
          tone={d.auto_extract.failed ? "warn" : undefined}
        />
        <Stat
          label="Server disk"
          value={d.disk.used_pct != null ? `${d.disk.used_pct}%` : "-"}
          sub={d.disk.free_gb != null ? `${d.disk.free_gb} GB free of ${d.disk.total_gb}` : undefined}
          tone={(d.disk.used_pct ?? 0) > 93 ? "bad" : (d.disk.used_pct ?? 0) > 85 ? "warn" : undefined}
        />
      </div>

      <Section title="LLM activity over time" hint="Each bar is one period of the window. Hover a bar for its time and value.">
        <div className="grid gap-5 p-4 md:grid-cols-2">
          <BarStrip title="Calls" points={d.llm.series.map((p) => ({ t: p.t, v: p.calls }))} color="bg-brand-500" fmt={(v) => `${v} call${v === 1 ? "" : "s"}`} />
          <BarStrip title="Failed calls" points={d.llm.series.map((p) => ({ t: p.t, v: p.errors }))} color="bg-rose-500" fmt={(v) => `${v} failed`} />
        </div>
      </Section>

      <Section title="LLM calls by extraction step" hint="Cost is what OpenRouter reports for each call. Typical = median time.">
        <RowsTable rows={d.llm.by_step} cost nameOf={(r) => STEP_NAMES[r.role] ?? r.role} empty="No LLM calls in this window." />
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Recent LLM calls">
          <EventList events={d.recent_llm} empty="No LLM calls yet." />
        </Section>
        <Section title="Recent failures" hint="LLM, Graph, background jobs and server errors.">
          <EventList events={d.failures} empty="No failures recorded." />
        </Section>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Background jobs">
          <RowsTable rows={d.jobs.by_task} nameOf={(r) => r.role} empty="No background jobs in this window." />
        </Section>
        <Section title="Microsoft Graph (mailbox) calls">
          <RowsTable
            rows={d.graph.by_call}
            nameOf={(r) => ({ list: "List messages", thread: "Read a thread", mime: "Download an email", attachment: "Download an attachment" }[r.role] ?? r.role)}
            empty="No Graph calls in this window."
          />
        </Section>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Slowest pages / API calls">
          <RowsTable rows={d.api.slowest} nameOf={(r) => r.role} empty="No API requests in this window." />
        </Section>
        <Section title="Processes" hint="Each running process reports every few seconds.">
          {d.processes.length ? (
            <ul className="divide-y divide-slate-50 text-xs">
              {d.processes.map((p) => (
                <li key={p.id} className="flex items-center gap-2 px-4 py-1.5">
                  <Badge tone={p.online ? "success" : "danger"} className="px-1.5 py-0 text-[10px]">
                    {p.online ? "online" : "silent"}
                  </Badge>
                  <span className="flex-1 text-slate-700">{PROCESS_NAMES[p.kind] ?? p.kind}</span>
                  <span className="tabular-nums text-slate-400">pid {p.pid}</span>
                  <span className="w-20 text-right tabular-nums text-slate-500">up {fmtAge(p.uptime_s)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="flex items-center gap-1.5 px-4 py-6 text-sm text-slate-400">
              <Info className="h-4 w-4" /> No process has reported yet.
            </p>
          )}
          <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-500">
            Redis:{" "}
            {d.redis.available
              ? `${d.redis.used_mb} MB used${d.redis.max_mb ? ` of ${d.redis.max_mb} MB` : " (no cap)"} · ${d.redis.evicted_keys ?? 0} evicted · up ${d.redis.uptime_h} h`
              : "unavailable"}
          </div>
        </Section>
      </div>
    </div>
  );
}

export default function AdminMonitor() {
  const [minutes, setMinutes] = useState(60);
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-monitor", minutes],
    queryFn: () => fetchMonitor(minutes),
    refetchInterval: 15_000,
  });
  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Monitor"
        subtitle="LLM calls and cost, mail sync, workers, queues and failures, refreshes every 15 seconds."
        actions={
          <Select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Time window">
            {WINDOWS.map((w) => (
              <option key={w.m} value={w.m}>{w.label}</option>
            ))}
          </Select>
        }
      />
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-12" />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-20" />)}
          </div>
          <Skeleton className="h-40" />
        </div>
      ) : error || !data ? (
        <Card className="p-4 text-sm text-rose-700">Could not load the monitor: {String((error as Error)?.message ?? "no data")}</Card>
      ) : (
        <Body d={data} />
      )}
    </div>
  );
}
