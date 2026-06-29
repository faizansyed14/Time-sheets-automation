import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Save, FlaskConical, RotateCcw, Loader2, CheckCircle2, XCircle, Cpu, KeySquare, FileText, Eye, EyeOff } from "lucide-react";
import {
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

export default function AdminSettings() {
  const { toast } = useToast();
  const { data, isLoading, refetch } = useQuery({ queryKey: ["admin-config"], queryFn: adminGetConfig });
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

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await adminTestConfig(String(form["ai_provider"] ?? ""));
      setTestResult(res);
    } catch (e: any) {
      setTestResult({ ok: false, provider: String(form["ai_provider"]), model: "", error: e?.response?.data?.detail ?? String(e) });
    } finally {
      setTesting(false);
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

  const provider = (String(form["ai_provider"] ?? "openai") as AiProviderId);
  const pmeta = AI_PROVIDERS[provider] ?? AI_PROVIDERS.openai;

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

  const modelField = (k: string, label: string, options: readonly string[]) => (
    <Field label={label}>
      <Input
        list={`models-${k}`}
        value={String(form[k] ?? "")}
        onChange={(e) => set(k, e.target.value)}
        placeholder="e.g. gpt-4o"
      />
      <datalist id={`models-${k}`}>
        {options.map((m) => <option key={m} value={m} />)}
      </datalist>
    </Field>
  );

  return (
    <div className="mx-auto max-w-3xl animate-fade-up">
      <PageHeader
        title="AI Settings"
        subtitle="Configure providers, models and prompts. Changes apply live — no redeploy."
        actions={
          <>
            <Button variant="secondary" onClick={runTest} disabled={testing}>
              {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
              Test provider
            </Button>
            <Button onClick={save} disabled={saving || Object.keys(dirty).length === 0}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </Button>
          </>
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

      {/* providers — only the ACTIVE provider's fields are shown */}
      <Card className="mb-5 p-5">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-800"><KeySquare className="h-4 w-4 text-slate-400" /> Provider &amp; API keys</h2>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Active provider">
            <Select className="w-full" value={provider} onChange={(e) => set("ai_provider", e.target.value)}>
              {Object.entries(AI_PROVIDERS).map(([id, m]) => (
                <option key={id} value={id}>{m.label}</option>
              ))}
            </Select>
          </Field>
          <div />
          {secretField(pmeta.keyField, `${pmeta.label} API key`)}
          <Field label={`${pmeta.label} base URL`}>
            <Input value={String(form[pmeta.baseField] ?? "")} onChange={(e) => set(pmeta.baseField, e.target.value)} />
          </Field>
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Showing settings for <span className="font-semibold text-slate-600">{pmeta.label}</span> only. Switch the active provider to edit another.
        </p>
      </Card>

      {/* model controls */}
      <Card className="mb-5 p-5">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-800"><Cpu className="h-4 w-4 text-slate-400" /> Extraction controls</h2>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Extraction engine">
            <Select className="w-full" value={String(form["extraction_engine"] ?? "mock")} onChange={(e) => set("extraction_engine", e.target.value)}>
              <option value="mock">mock (deterministic)</option>
              <option value="vision">vision (LLM)</option>
            </Select>
          </Field>
          {modelField("extraction_model", "Extraction model", pmeta.extractionModels)}
          <Field label="Vision image detail">
            <Select className="w-full" value={String(form["vision_image_detail"] ?? "high")} onChange={(e) => set("vision_image_detail", e.target.value)}>
              <option value="high">high</option>
              <option value="low">low</option>
            </Select>
          </Field>
          {modelField("validation_model", "Validation model", pmeta.validationModels)}
          {modelField("ai_check_model", "AI Check model (inbox triage)", pmeta.validationModels)}
          {modelField("agent_chat_model", "Agent Chat model", pmeta.extractionModels)}
          <Field label="Text cross-validation">
            <label className="flex items-center gap-2 pt-2 text-sm text-slate-600">
              <input type="checkbox" checked={Boolean(form["enable_text_validation"])} onChange={(e) => set("enable_text_validation", e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-brand-600" />
              ENABLE_TEXT_VALIDATION
            </label>
          </Field>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}
