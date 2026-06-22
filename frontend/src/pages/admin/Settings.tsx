import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Save, FlaskConical, RotateCcw, Loader2, CheckCircle2, XCircle, Cpu, KeySquare, FileText } from "lucide-react";
import {
  adminGetConfig,
  adminPromptDefaults,
  adminTestConfig,
  adminUpdateConfig,
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
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);

  useEffect(() => {
    if (data) {
      const f: Record<string, unknown> = {};
      data.forEach((c) => (f[c.key] = c.value));
      setForm(f);
      setDirty({});
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

  if (isLoading) return <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>;

  const SecretInput = ({ k, label }: { k: string; label: string }) => (
    <Field label={label}>
      <Input
        type="password"
        value={String(form[k] ?? "")}
        onChange={(e) => set(k, e.target.value)}
        onFocus={(e) => { if (e.target.value === SECRET_MASK) set(k, ""); }}
        placeholder={byKey[k]?.value ? "saved — type to replace" : "not set"}
      />
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

      {/* providers */}
      <Card className="mb-5 p-5">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-800"><KeySquare className="h-4 w-4 text-slate-400" /> Provider &amp; API keys</h2>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Active provider">
            <Select className="w-full" value={String(form["ai_provider"] ?? "openai")} onChange={(e) => set("ai_provider", e.target.value)}>
              <option value="openai">OpenAI</option>
              <option value="deepseek">DeepSeek</option>
            </Select>
          </Field>
          <div />
          <SecretInput k="openai_api_key" label="OpenAI API key" />
          <Field label="OpenAI base URL"><Input value={String(form["openai_base_url"] ?? "")} onChange={(e) => set("openai_base_url", e.target.value)} /></Field>
          <SecretInput k="deepseek_api_key" label="DeepSeek API key" />
          <Field label="DeepSeek base URL"><Input value={String(form["deepseek_base_url"] ?? "")} onChange={(e) => set("deepseek_base_url", e.target.value)} /></Field>
        </div>
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
          <Field label="Extraction model"><Input value={String(form["extraction_model"] ?? "")} onChange={(e) => set("extraction_model", e.target.value)} /></Field>
          <Field label="Vision image detail">
            <Select className="w-full" value={String(form["vision_image_detail"] ?? "high")} onChange={(e) => set("vision_image_detail", e.target.value)}>
              <option value="high">high</option>
              <option value="low">low</option>
            </Select>
          </Field>
          <Field label="Validation model"><Input value={String(form["validation_model"] ?? "")} onChange={(e) => set("validation_model", e.target.value)} /></Field>
          <Field label="Text cross-validation">
            <label className="flex items-center gap-2 pt-2 text-sm text-slate-600">
              <input type="checkbox" checked={Boolean(form["enable_text_validation"])} onChange={(e) => set("enable_text_validation", e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-brand-600" />
              ENABLE_TEXT_VALIDATION
            </label>
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
