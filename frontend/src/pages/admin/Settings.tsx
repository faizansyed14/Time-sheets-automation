/**
 * Admin → AI Settings.
 *
 * The admin can do exactly two things here: pick the PROVIDER (OpenAI / vLLM)
 * each service uses, and view/override prompts. API keys, base URLs, model
 * names and tuning knobs are .env ONLY — the backend API neither returns nor
 * accepts them, so key material never reaches the browser.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Save, RotateCcw, Loader2, Cpu, FileText } from "lucide-react";
import {
  adminConfigStatus,
  adminGetConfig,
  adminPromptDefaults,
  adminPromptsAll,
  adminUpdateConfig,
  type ConfigItem,
  type PromptInfo,
} from "../../api/client";
import { Button, Card, PageHeader, Select, Skeleton } from "../../components/ui";
import { useToast } from "../../components/toast";

export default function AdminSettings() {
  const { toast } = useToast();
  const { data, isLoading, refetch } = useQuery({ queryKey: ["admin-config"], queryFn: adminGetConfig });
  const { data: status } = useQuery({
    queryKey: ["admin-config-status"], queryFn: adminConfigStatus,
    refetchInterval: 15000, // stays live while the page is open
  });
  const { data: prompts } = useQuery({ queryKey: ["admin-prompts-all"], queryFn: adminPromptsAll });

  const [form, setForm] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data) {
      const f: Record<string, unknown> = {};
      data.forEach((c: ConfigItem) => (f[c.key] = c.value));
      setForm(f);
      setDirty({});
    }
  }, [data]);

  const set = (key: string, value: unknown) => {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty((d) => ({ ...d, [key]: true }));
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      Object.keys(dirty).forEach((k) => (payload[k] = form[k]));
      await adminUpdateConfig(payload);
      toast("success", "Prompts saved", "Changes take effect immediately.");
      refetch();
    } catch (e: any) {
      toast("error", "Save failed", e?.response?.data?.detail ?? String(e));
    } finally {
      setSaving(false);
    }
  };

  const loadPromptDefaults = async () => {
    const d = await adminPromptDefaults();
    set("system_prompt", d.system_prompt);
    set("extraction_prompt", d.extraction_prompt);
    set("summary_prompt", d.summary_prompt);
    toast("info", "Loaded built-in prompts", "Review and Save to apply.");
  };

  const SERVICES = useMemo(() => ([
    { kind: "extraction", field: "vision_provider",
      label: "Vision extraction",
      desc: "Reads timesheets & approvals from page images — Extract Email, Upload, Run Extraction." },
    { kind: "validation", field: "validation_provider",
      label: "Validation & summaries (text)",
      desc: "Second, text-only read that cross-checks the vision result and writes review notes." },
    { kind: "agent", field: "ai_provider",
      label: "Agentic chat",
      desc: "The assistant on the chat page. Needs OpenAI-style tool calling — vLLM only works if the server enables it." },
  ] as const), []);

  if (isLoading) return <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>;

  return (
    <div className="mx-auto max-w-3xl animate-fade-up">
      <PageHeader
        title="AI Settings"
        subtitle="Pick a provider per service. Models, keys and URLs live in .env only — they never pass through the browser."
        actions={
          <Button onClick={save} disabled={saving || Object.keys(dirty).length === 0}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save
          </Button>
        }
      />

      {/* provider per service (editable) + live resolved model (read-only) */}
      <Card className="mb-5 p-5">
        <h2 className="mb-1 flex items-center gap-2 text-sm font-bold text-slate-800">
          <Cpu className="h-4 w-4 text-slate-400" /> Provider per service
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          The model shown is what that provider actually uses right now, from
          <code className="mx-1 rounded bg-slate-100 px-1 font-mono text-[11px]">.env</code>
          — to change a model or key, edit .env and restart the backend and worker.
        </p>
        <div className="space-y-2">
          {SERVICES.map((svc) => {
            const live = status?.find((s) => s.kind === svc.kind);
            return (
              <div key={svc.kind} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-slate-800">{svc.label}</p>
                  <p className="mt-0.5 text-xs text-slate-500">{svc.desc}</p>
                  {live?.note && <p className="mt-1 text-[11px] text-sky-700">{live.note}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Select
                    value={String(form[svc.field] ?? "openai")}
                    onChange={(e) => set(svc.field, e.target.value)}
                  >
                    <option value="openai">OpenAI</option>
                    <option value="vllm">vLLM (self-hosted)</option>
                  </Select>
                  {live && (
                    <span
                      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${PROVIDER_TONE[live.provider] ?? "border-slate-200 bg-slate-50 text-slate-600"}`}
                      title="Actually in use right now (last saved)"
                    >
                      Active: {PROVIDER_LABEL[live.provider] ?? live.provider} ·{" "}
                      <code className="font-mono">{live.model || "—"}</code>
                    </span>
                  )}
                  {live && !live.has_key && (
                    <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-700">no key in .env</span>
                  )}
                </div>
              </div>
            );
          })}
          {!status && <Skeleton className="h-20 w-full" />}
        </div>
      </Card>

      {/* prompts: full inventory; only the engine trio is editable */}
      <Card className="p-5">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-bold text-slate-800"><FileText className="h-4 w-4 text-slate-400" /> Prompts</h2>
          <Button size="sm" variant="ghost" onClick={loadPromptDefaults}><RotateCcw className="h-3.5 w-3.5" /> Load built-in defaults</Button>
        </div>
        <p className="mb-3 text-xs text-slate-500">
          Every LLM prompt in the backend, with the exact flows that use it — all of them are
          in active use. The three marked <span className="font-semibold">Editable</span> can be
          overridden here (empty = built-in default); the rest are shown read-only.
        </p>
        <div className="space-y-2">
          {(prompts ?? []).map((p) => (
            <PromptRow
              key={p.key}
              info={p}
              value={p.override_key ? String(form[p.override_key] ?? "") : undefined}
              onChange={p.override_key ? (v) => set(p.override_key!, v) : undefined}
            />
          ))}
          {!prompts && <Skeleton className="h-24 w-full" />}
        </div>
      </Card>
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = { openai: "OpenAI", vllm: "vLLM (self-hosted)", deepseek: "DeepSeek" };
const PROVIDER_TONE: Record<string, string> = {
  openai: "border-emerald-200 bg-emerald-50 text-emerald-700",
  vllm: "border-sky-200 bg-sky-50 text-sky-700",
  deepseek: "border-violet-200 bg-violet-50 text-violet-700",
};

/** One prompt in the inventory: header row with badges + expandable body.
 *  Editable prompts render the override textarea (bound to admin config);
 *  the rest show the live prompt text read-only. */
function PromptRow({
  info,
  value,
  onChange,
}: {
  info: PromptInfo;
  value?: string;
  onChange?: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const badge = "rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide";
  return (
    <div className="rounded-xl border border-slate-200">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start justify-between gap-3 px-3 py-2.5 text-left hover:bg-slate-50/60"
      >
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-800">{info.title}</p>
          <p className="mt-0.5 text-xs text-slate-500">{info.used_by}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 pt-0.5">
          <span className={`${badge} border-emerald-200 bg-emerald-50 text-emerald-700`}>In use</span>
          {info.editable && <span className={`${badge} border-amber-200 bg-amber-50 text-amber-700`}>Editable</span>}
          {info.dynamic && <span className={`${badge} border-slate-200 bg-slate-50 text-slate-500`}>Dynamic</span>}
        </div>
      </button>
      {open && (
        <div className="border-t border-slate-100 p-3">
          {info.editable && onChange ? (
            <>
              <textarea
                value={value ?? ""}
                onChange={(e) => onChange(e.target.value)}
                rows={info.override_key === "extraction_prompt" ? 10 : 5}
                placeholder="(empty = use built-in default shown below)"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs leading-5 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
              />
              {!value && (
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-4 text-slate-500">
                  {info.content}
                </pre>
              )}
            </>
          ) : (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-4 text-slate-600">
              {info.content}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
