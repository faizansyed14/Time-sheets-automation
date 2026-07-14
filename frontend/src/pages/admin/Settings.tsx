import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Save, FlaskConical, RotateCcw, Loader2, CheckCircle2, XCircle, Cpu, KeySquare, FileText, Eye, EyeOff, AlertTriangle } from "lucide-react";
import {
  adminConfigStatus,
  adminGetConfig,
  adminPromptDefaults,
  adminRevealSecret,
  adminTestConfig,
  adminUpdateConfig,
  AI_PROVIDERS,
  type AiProviderId,
  type ConfigItem,
  type ProviderTestResult,
} from "../../api/client";
import { Button, Card, Input, PageHeader, Select, Skeleton } from "../../components/ui";
import { useToast } from "../../components/toast";

const SECRET_MASK = "••••••••";

interface ServiceDef {
  key: "vision" | "validation" | "agent";
  statusKind: "extraction" | "validation" | "agent";
  providerField: string;
  modelField: string;
  label: string;
  desc: string;
  providers: readonly AiProviderId[];
  lockNote?: string;
}

export default function AdminSettings() {
  const { toast } = useToast();
  const { data, isLoading, refetch } = useQuery({ queryKey: ["admin-config"], queryFn: adminGetConfig });
  const { data: status } = useQuery({
    queryKey: ["admin-config-status"], queryFn: adminConfigStatus,
    refetchInterval: 15000, // stays live while the page is open, e.g. after a Save
  });
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);

  useEffect(() => {
    if (data) {
      const f: Record<string, unknown> = {};
      data.forEach((c) => (f[c.key] = c.value));
      setForm(f);
      setDirty({});
      setShown({});
    }
  }, [data]);

  const byKey = useMemo(() => {
    const m: Record<string, ConfigItem> = {};
    (data ?? []).forEach((c) => (m[c.key] = c));
    return m;
  }, [data]);

  const set = (key: string, value: unknown) => {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty((d) => ({ ...d, [key]: true }));
  };

  const save = async () => {
    setSaving(true);
    try {
      // only send changed keys; for secrets, skip if still the mask
      const payload: Record<string, unknown> = {};
      Object.keys(dirty).forEach((k) => {
        if (byKey[k]?.is_secret && form[k] === SECRET_MASK) return;
        payload[k] = form[k];
      });
      await adminUpdateConfig(payload);
      toast("success", "Settings saved", "Changes take effect immediately.");
      refetch();
    } catch (e: any) {
      toast("error", "Save failed", e?.response?.data?.detail ?? String(e));
    } finally {
      setSaving(false);
    }
  };

  const [testingProvider, setTestingProvider] = useState<string | null>(null);
  const runTest = async (providerId: string) => {
    setTesting(true);
    setTestingProvider(providerId);
    setTestResult(null);
    try {
      // Save any unsaved credential edits first so the test hits the new values.
      if (Object.keys(dirty).length) await save();
      const res = await adminTestConfig(providerId);
      setTestResult(res);
    } catch (e: any) {
      setTestResult({ ok: false, provider: providerId, model: "", error: e?.response?.data?.detail ?? String(e) });
    } finally {
      setTesting(false);
      setTestingProvider(null);
    }
  };

  const loadPromptDefaults = async () => {
    const d = await adminPromptDefaults();
    set("system_prompt", d.system_prompt);
    set("extraction_prompt", d.extraction_prompt);
    set("summary_prompt", d.summary_prompt);
    toast("info", "Loaded built-in prompts", "Review and Save to apply.");
  };

  // Reveal a stored secret: fetch the plaintext (admin-only) on first show, and
  // re-mask on hide if it wasn't edited — so the admin can confirm the live key.
  const toggleReveal = async (k: string) => {
    const next = !shown[k];
    if (next && form[k] === SECRET_MASK) {
      try {
        const v = await adminRevealSecret(k);
        setForm((f) => ({ ...f, [k]: v }));
      } catch (e: any) {
        toast("error", "Could not reveal key", e?.response?.data?.detail ?? String(e));
        return;
      }
    } else if (!next && !dirty[k] && byKey[k]?.value) {
      setForm((f) => ({ ...f, [k]: SECRET_MASK })); // restore mask if untouched
    }
    setShown((s) => ({ ...s, [k]: next }));
  };

  if (isLoading) return <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>;

  // Each AI service picks its OWN provider + model. The provider decides the
  // endpoint/key (set once in "Provider credentials"); the model is sent as-is.
  const SERVICES: ServiceDef[] = [
    { key: "vision", statusKind: "extraction", providerField: "vision_provider", modelField: "extraction_model",
      label: "Vision extraction", desc: "Reads timesheets & approvals from page images (Extract Email, per-file).",
      providers: ["openai", "vllm"] },
    { key: "validation", statusKind: "validation", providerField: "validation_provider", modelField: "validation_model",
      label: "Validation & cross-check", desc: "A second, text-only read that flags mismatches and writes summaries.",
      providers: ["openai", "deepseek", "vllm"] },
    { key: "agent", statusKind: "agent", providerField: "ai_provider", modelField: "agent_chat_model",
      label: "Agentic chat", desc: "The assistant that queries and edits leaves via tools.",
      providers: ["openai"], lockNote: "OpenAI only — the chat needs OpenAI-style tool calling." },
  ];
  const modelsFor = (serviceKey: ServiceDef["key"], providerId: string): readonly string[] => {
    const p = AI_PROVIDERS[providerId as AiProviderId] ?? AI_PROVIDERS.openai;
    return serviceKey === "vision" ? p.extractionModels : p.validationModels;
  };
  // Switching provider must move the model to one that actually exists for
  // it — otherwise the field silently keeps showing the OLD provider's model
  // (e.g. "qwen3-vl-32b" after switching to OpenAI), which is exactly wrong.
  const setServiceProvider = (svc: ServiceDef, newProvider: string) => {
    set(svc.providerField, newProvider);
    const models = modelsFor(svc.key, newProvider);
    const current = String(form[svc.modelField] ?? "");
    if (!models.includes(current)) set(svc.modelField, models[0] ?? "");
  };

  // Rendered as plain functions (not <Components/>) so the inputs keep focus
  // across re-renders while typing.
  const secretField = (k: string, label: string) => {
    const isShown = !!shown[k];
    return (
      <Field label={label}>
        <div className="relative">
          <Input
            type={isShown ? "text" : "password"}
            className="pr-10"
            value={String(form[k] ?? "")}
            onChange={(e) => set(k, e.target.value)}
            onFocus={(e) => { if (e.target.value === SECRET_MASK) set(k, ""); }}
            placeholder={byKey[k]?.value ? "saved — type to replace, or show to view" : "not set"}
          />
          <button
            type="button"
            onClick={() => toggleReveal(k)}
            title={isShown ? "Hide key" : "Show key"}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            {isShown ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          {byKey[k]?.value ? "A key is saved." : "No key saved yet."}
        </p>
      </Field>
    );
  };


  return (
    <div className="mx-auto max-w-3xl animate-fade-up">
      <PageHeader
        title="AI Settings"
        subtitle="Pick a provider and model for each service. Changes apply live — no redeploy."
        actions={
          <Button onClick={save} disabled={saving || Object.keys(dirty).length === 0}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save
          </Button>
        }
      />

      {testResult && (
        <Card className={`mb-5 border p-4 ${testResult.ok ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50"}`}>
          <div className="flex items-start gap-2 text-sm">
            {testResult.ok ? <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-600" /> : <XCircle className="mt-0.5 h-5 w-5 text-rose-600" />}
            <div>
              <p className="font-semibold text-slate-800">
                {testResult.ok ? "Provider reachable" : "Provider test failed"} · {testResult.provider} / {testResult.model}
              </p>
              <p className="text-xs text-slate-600">
                {testResult.ok ? `Replied "${testResult.reply}" in ${testResult.latency_ms}ms` : testResult.error}
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* per-service provider + model */}
      <Card className="mb-5 p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-bold text-slate-800"><Cpu className="h-4 w-4 text-slate-400" /> Model per service</h2>
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            Extraction engine
            <Select value={String(form["extraction_engine"] ?? "mock")} onChange={(e) => set("extraction_engine", e.target.value)}>
              <option value="mock">mock (deterministic)</option>
              <option value="vision">vision (LLM)</option>
            </Select>
          </div>
        </div>
        <div className="space-y-3">
          {SERVICES.map((svc) => {
            const prov = String(form[svc.providerField] ?? "openai");
            const models = modelsFor(svc.key, prov);
            const provKey = AI_PROVIDERS[prov as AiProviderId]?.keyField;
            const noKey = provKey ? !byKey[provKey]?.value : false;
            // What's ACTUALLY resolved right now, from the backend — reflects
            // the last SAVED config, so it also catches "you edited this but
            // haven't saved yet" (badge lags the form until you hit Save).
            const live = status?.find((s) => s.kind === svc.statusKind);
            return (
              <div key={svc.key} className="rounded-xl border border-slate-200 p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-slate-800">{svc.label}</span>
                  {live && (
                    <span
                      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${PROVIDER_TONE[live.provider] ?? "border-slate-200 bg-slate-50 text-slate-500"}`}
                      title="What's actually in use right now (last saved config)"
                    >
                      Active: {PROVIDER_LABEL[live.provider] ?? live.provider} · <code className="font-mono">{live.model || "—"}</code>
                    </span>
                  )}
                  {noKey && (
                    <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700" title="This provider has no API key set below">
                      <AlertTriangle className="h-3 w-3" /> no key for {PROVIDER_LABEL[prov] ?? prov}
                    </span>
                  )}
                </div>
                <p className="mb-3 text-xs text-slate-500">{svc.desc}</p>
                {live?.note && (
                  <p className="mb-3 flex items-start gap-1.5 rounded-lg border border-sky-200 bg-sky-50 px-2.5 py-1.5 text-[11px] text-sky-700">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {live.note}
                  </p>
                )}
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Provider">
                    <Select className="w-full" value={prov} disabled={svc.providers.length === 1}
                            onChange={(e) => setServiceProvider(svc, e.target.value)}>
                      {svc.providers.map((id) => (
                        <option key={id} value={id}>{PROVIDER_LABEL[id] ?? id}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Model">
                    <Input
                      list={`models-${svc.key}`}
                      value={String(form[svc.modelField] ?? "")}
                      onChange={(e) => set(svc.modelField, e.target.value)}
                      placeholder={models[0] ?? "model name"}
                    />
                    <datalist id={`models-${svc.key}`}>
                      {models.map((m) => <option key={m} value={m} />)}
                    </datalist>
                  </Field>
                </div>
                {svc.key === "vision" && (
                  <div className="mt-3">
                    <Field label="Vision image detail (scans/photos)">
                      <Select className="w-full sm:w-1/2" value={String(form["vision_image_detail"] ?? "high")} onChange={(e) => set("vision_image_detail", e.target.value)}>
                        <option value="high">high</option>
                        <option value="low">low</option>
                      </Select>
                    </Field>
                  </div>
                )}
                {svc.key === "validation" && (
                  <label className="mt-3 flex items-center gap-2 text-sm text-slate-600">
                    <input type="checkbox" checked={Boolean(form["enable_text_validation"])} onChange={(e) => set("enable_text_validation", e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-brand-600" />
                    Run the validation cross-check
                  </label>
                )}
                {svc.lockNote && <p className="mt-2 text-[11px] text-slate-400">{svc.lockNote}</p>}
              </div>
            );
          })}
        </div>
      </Card>

      {/* provider credentials — set once, shared by whichever services use them */}
      <Card className="mb-5 p-5">
        <h2 className="mb-1 flex items-center gap-2 text-sm font-bold text-slate-800"><KeySquare className="h-4 w-4 text-slate-400" /> Provider credentials</h2>
        <p className="mb-3 text-xs text-slate-500">One key + base URL per provider, shared by every service that selects it above.</p>
        <div className="space-y-4">
          {(Object.entries(AI_PROVIDERS) as [AiProviderId, typeof AI_PROVIDERS[AiProviderId]][]).map(([id, meta]) => (
            <div key={id} className="rounded-xl border border-slate-200 p-4">
              <div className="mb-3 flex items-center justify-between">
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${PROVIDER_TONE[id] ?? ""}`}>{meta.label}</span>
                <Button size="sm" variant="secondary" onClick={() => runTest(id)} disabled={testing}>
                  {testing && testingProvider === id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FlaskConical className="h-3.5 w-3.5" />}
                  Test
                </Button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {secretField(meta.keyField, "API key")}
                <Field label="Base URL">
                  <Input value={String(form[meta.baseField] ?? "")} onChange={(e) => set(meta.baseField, e.target.value)} placeholder="https://…" />
                </Field>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* cost & accuracy tuning */}
      <Card className="mb-5 p-5">
        <h2 className="mb-1 flex items-center gap-2 text-sm font-bold text-slate-800"><Cpu className="h-4 w-4 text-slate-400" /> Cost &amp; accuracy</h2>
        <p className="mb-3 text-xs text-slate-500">Born-digital sheets are read cheaply from their text layer; scans use the vision model. These knobs cut cost without losing accuracy.</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Adaptive image detail">
            <label className="flex items-center gap-2 pt-2 text-sm text-slate-600">
              <input type="checkbox" checked={form["vision_adaptive_detail"] !== false} onChange={(e) => set("vision_adaptive_detail", e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-brand-600" />
              low detail for digital (cheaper)
            </label>
          </Field>
          <Field label="PDF render DPI">
            <Select className="w-full" value={String(form["pdf_render_dpi"] ?? 150)} onChange={(e) => set("pdf_render_dpi", Number(e.target.value))}>
              <option value="120">120 (cheapest)</option>
              <option value="150">150 (recommended)</option>
              <option value="200">200</option>
              <option value="300">300 (highest)</option>
            </Select>
          </Field>
          <Field label="Strict JSON output">
            <label className="flex items-center gap-2 pt-2 text-sm text-slate-600">
              <input type="checkbox" checked={form["vision_json_mode"] !== false} onChange={(e) => set("vision_json_mode", e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-brand-600" />
              guarantee parseable JSON
            </label>
          </Field>
          <Field label="Deterministic-first">
            <label className="flex items-center gap-2 pt-2 text-sm text-slate-600">
              <input type="checkbox" checked={Boolean(form["extraction_prefer_deterministic"])} onChange={(e) => set("extraction_prefer_deterministic", e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-brand-600" />
              skip LLM for clean digital sheets
            </label>
          </Field>
          <Field label="OCR reader (scans)">
            <Select className="w-full" value={String(form["ocr_provider"] ?? "none")} onChange={(e) => set("ocr_provider", e.target.value)}>
              <option value="none">none</option>
              <option value="tesseract">Tesseract (local, free)</option>
            </Select>
          </Field>
        </div>
      </Card>

      {/* prompts */}
      <Card className="p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-bold text-slate-800"><FileText className="h-4 w-4 text-slate-400" /> Prompts</h2>
          <Button size="sm" variant="ghost" onClick={loadPromptDefaults}><RotateCcw className="h-3.5 w-3.5" /> Load built-in defaults</Button>
        </div>
        <div className="space-y-3">
          {(["system_prompt", "extraction_prompt", "summary_prompt"] as const).map((k) => (
            <Field key={k} label={k.replace("_", " ")}>
              <textarea
                value={String(form[k] ?? "")}
                onChange={(e) => set(k, e.target.value)}
                rows={k === "extraction_prompt" ? 8 : 4}
                placeholder="(empty = use built-in default)"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs leading-5 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
              />
            </Field>
          ))}
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}
