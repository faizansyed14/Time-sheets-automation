/**
 * Admin → AI Settings (read-only).
 *
 * Models, keys, providers and prompts come from .env / built-in code only.
 * Restart the backend after changing .env.
 */
import { useQuery } from "@tanstack/react-query";
import { Cpu, FileKey2 } from "lucide-react";
import { adminConfigStatus } from "../../api/client";
import { Badge, Card, PageHeader, Skeleton } from "../../components/ui";

const ENV_VARS = [
  { key: "OPENAI_API_KEY", desc: "Required for vision extraction and agentic chat" },
  { key: "OPENAI_BASE_URL", desc: "OpenAI API root (default https://api.openai.com)" },
  { key: "OPENAI_VISION_MODEL", desc: "Vision model — Extract Email, Upload, chat uploads" },
  { key: "AGENT_CHAT_MODEL", desc: "Agentic chat assistant model" },
  { key: "EXTRACTION_ENGINE", desc: "mock | vision — mock skips LLM calls" },
  { key: "VISION_IMAGE_DETAIL", desc: "high | low — scan/photo image detail" },
  { key: "PDF_RENDER_DPI", desc: "DPI when rendering PDFs for the vision model" },
  { key: "VISION_JSON_MODE", desc: "true | false — JSON response format when supported" },
  { key: "OCR_PROVIDER", desc: "none | tesseract — optional text layer for scans" },
  { key: "PII_REDACTION", desc: "true | false — scrub emails/phones before LLM egress" },
];

export default function AdminSettings() {
  const { data: status, isLoading } = useQuery({
    queryKey: ["admin-config-status"],
    queryFn: adminConfigStatus,
    refetchInterval: 15000,
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
          <FileKey2 className="h-4 w-4 text-slate-400" /> .env variables
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          Extraction prompts are built into the backend — not editable here or via the API.
        </p>
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200">
          {ENV_VARS.map((v) => (
            <li key={v.key} className="flex flex-col gap-0.5 px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between">
              <code className="font-mono text-xs font-semibold text-slate-800">{v.key}</code>
              <span className="text-xs text-slate-500 sm:text-right">{v.desc}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
