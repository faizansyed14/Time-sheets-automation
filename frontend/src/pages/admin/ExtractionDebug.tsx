import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bug, ChevronRight, Image as ImageIcon, Trash2 } from "lucide-react";
import {
  adminClearDebugRuns,
  adminDebugImageUrl,
  adminGetDebugRun,
  adminListDebugRuns,
  type DebugLlmCall,
} from "../../api/client";
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton } from "../../components/ui";
import { cn, formatDateTime } from "../../lib/utils";
import { useToast } from "../../components/toast";

type Tab = "pass1" | "pass2" | "dropped" | "sheets";

function CallCard({ call, index }: { call: DebugLlmCall; index: number }) {
  const [open, setOpen] = useState(index === 0);
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-semibold text-slate-700 hover:bg-slate-50"
      >
        <ChevronRight className={cn("h-4 w-4 shrink-0 transition-transform", open && "rotate-90")} />
        {call.label || `call ${index + 1}`}
        <span className="ml-auto text-xs font-normal text-slate-400">
          {call.model} · {call.image_count} image(s)
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-slate-100 p-3 text-xs">
          <div>
            <p className="mb-1 font-semibold uppercase tracking-wide text-slate-400">System prompt</p>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-2 font-mono text-[11px] text-slate-700">
              {call.system_prompt}
            </pre>
          </div>
          <div>
            <p className="mb-1 font-semibold uppercase tracking-wide text-slate-400">User prompt (text parts)</p>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-2 font-mono text-[11px] text-slate-700">
              {call.user_text}
            </pre>
          </div>
          <div>
            <p className="mb-1 font-semibold uppercase tracking-wide text-slate-400">Response JSON</p>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-slate-900 p-2 font-mono text-[11px] text-emerald-300">
              {JSON.stringify(call.response_json, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AdminExtractionDebug() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: runs, isLoading } = useQuery({ queryKey: ["admin-debug-runs"], queryFn: () => adminListDebugRuns() });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("pass1");
  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["admin-debug-run", selectedId],
    queryFn: () => adminGetDebugRun(selectedId!),
    enabled: !!selectedId,
  });

  const clearMut = useMutation({
    mutationFn: adminClearDebugRuns,
    onSuccess: (r) => {
      toast("info", `Cleared ${r.deleted} debug run(s)`);
      setSelectedId(null);
      qc.invalidateQueries({ queryKey: ["admin-debug-runs"] });
    },
    onError: (e: any) => toast("error", "Clear failed", e?.response?.data?.detail ?? String(e)),
  });

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Extraction debug"
        subtitle="Every Pass 1 / Pass 2 prompt and response, and every dropped item, for the runs captured so far — a testing aid, not a permanent log."
        actions={
          <Button
            variant="secondary"
            onClick={() => { if (confirm("Delete every captured debug run and its images? This cannot be undone.")) clearMut.mutate(); }}
            disabled={clearMut.isPending}
          >
            <Trash2 className="h-4 w-4" /> Clear all
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[340px_1fr]">
        <Card className="max-h-[75vh] overflow-auto">
          {isLoading ? (
            <div className="space-y-2 p-4"><Skeleton className="h-14" /><Skeleton className="h-14" /></div>
          ) : !runs?.length ? (
            <EmptyState icon={<Bug className="h-6 w-6" />} title="No runs captured yet"
              detail="Extract Email or Upload something — every run is captured automatically." />
          ) : (
            <ul className="divide-y divide-slate-100">
              {runs.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => { setSelectedId(r.id); setTab("pass1"); }}
                    className={cn(
                      "block w-full px-4 py-3 text-left transition-colors hover:bg-slate-50",
                      selectedId === r.id && "bg-brand-50/60"
                    )}
                  >
                    <p className="truncate text-sm font-semibold text-slate-700">{r.subject || r.source_id || r.id}</p>
                    <p className="mt-0.5 text-[11px] text-slate-400">{formatDateTime(r.created_at)}</p>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      <Badge tone="slate">{r.calls} call(s)</Badge>
                      {r.reused_sheets > 0 && <Badge tone="success">{r.reused_sheets} reused</Badge>}
                      {r.n_dropped > 0 && <Badge tone="warning">{r.n_dropped} dropped</Badge>}
                      {r.n_errors > 0 && <Badge tone="danger">{r.n_errors} error(s)</Badge>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="min-h-[400px]">
          {!selectedId ? (
            <EmptyState icon={<Bug className="h-6 w-6" />} title="Pick a run" detail="Select a run on the left to see its full trace." />
          ) : detailLoading || !detail ? (
            <div className="space-y-2 p-4"><Skeleton className="h-8" /><Skeleton className="h-40" /></div>
          ) : (
            <div className="flex h-full flex-col">
              <div className="flex shrink-0 gap-1 border-b border-slate-200 px-3 py-2">
                {([
                  ["pass1", `Pass 1 (${detail.pass1_calls.length})`],
                  ["pass2", `Pass 2 (${detail.pass2_calls.length})`],
                  ["dropped", `Dropped (${detail.dropped_items.length})`],
                  ["sheets", `Sheets (${detail.sheets.length})`],
                ] as [Tab, string][]).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setTab(key)}
                    className={cn(
                      "rounded-md px-2.5 py-1 text-xs font-semibold transition-colors",
                      tab === key ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-100"
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <div className="min-h-0 flex-1 overflow-auto p-3">
                {tab === "pass1" && (
                  <div className="space-y-2">
                    {detail.pass1_calls.length === 0
                      ? <p className="p-4 text-sm text-slate-400">No Pass 1 calls (fully served from cache).</p>
                      : detail.pass1_calls.map((c, i) => <CallCard key={i} call={c} index={i} />)}
                  </div>
                )}
                {tab === "pass2" && (
                  <div className="space-y-2">
                    {detail.pass2_calls.length === 0
                      ? <p className="p-4 text-sm text-slate-400">No Pass 2 calls (nothing new confirmed, or fully served from cache).</p>
                      : detail.pass2_calls.map((c, i) => <CallCard key={i} call={c} index={i} />)}
                  </div>
                )}
                {tab === "dropped" && (
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    {detail.dropped_items.length === 0 && <p className="p-4 text-sm text-slate-400">Nothing was dropped this run.</p>}
                    {detail.dropped_items.map((d, i) => (
                      <div key={i} className="rounded-lg border border-slate-200 p-2 text-xs">
                        {d.image_path ? (
                          <img src={adminDebugImageUrl(d.image_path)} alt={d.name}
                               className="mb-1.5 h-24 w-full rounded object-contain bg-slate-50" />
                        ) : (
                          <div className="mb-1.5 flex h-24 w-full items-center justify-center rounded bg-slate-50 text-slate-300">
                            <ImageIcon className="h-6 w-6" />
                          </div>
                        )}
                        <p className="truncate font-semibold text-slate-700">{d.name}</p>
                        <Badge tone="warning" className="mt-1">{d.filter}</Badge>
                        <p className="mt-1 text-slate-500">{d.reason}</p>
                      </div>
                    ))}
                  </div>
                )}
                {tab === "sheets" && (
                  <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md bg-slate-900 p-3 font-mono text-[11px] text-emerald-300">
                    {JSON.stringify(detail.sheets, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
