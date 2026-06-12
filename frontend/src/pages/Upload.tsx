import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  UploadCloud,
  FileText,
  X,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  ExternalLink,
  Layers,
} from "lucide-react";
import { uploadTimesheets, MONTHS_LONG, type UploadResult } from "../api/client";
import { cn, formatBytes } from "../lib/utils";
import { Button, Card, PageHeader } from "../components/ui";
import StoredFilesPreview from "../components/StoredFilesPreview";
import { FailureChip } from "../components/status";
import { useToast } from "../components/toast";

export default function UploadPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [queue, setQueue] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [results, setResults] = useState<UploadResult[]>([]);

  const addFiles = useCallback((list: FileList | File[]) => {
    const files = Array.from(list).filter((f) =>
      /\.(pdf|docx|xlsx|png|jpe?g|eml)$/i.test(f.name)
    );
    setQueue((q) => {
      const names = new Set(q.map((f) => f.name));
      return [...q, ...files.filter((f) => !names.has(f.name))];
    });
  }, []);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  };

  const run = async () => {
    if (!queue.length) return;
    setBusy(true);
    setProgress(0);
    try {
      const res = await uploadTimesheets(queue, setProgress);
      setResults((r) => [...res, ...r]);
      setQueue([]);
      const ok = res.filter((r) => r.status === "success").length;
      const review = res.filter((r) => r.status === "needs_review").length;
      const failed = res.filter((r) => r.status === "failed").length;
      if (failed) toast("error", `${failed} file(s) failed`, "Open the Pipeline page for the exact reason — nothing was lost.");
      if (review) toast("warning", `${review} file(s) need review`);
      if (ok && !failed && !review) toast("success", `${ok} file(s) processed cleanly`);
      qc.invalidateQueries({ queryKey: ["pipeline"] });
      qc.invalidateQueries({ queryKey: ["pipeline-stats"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e: any) {
      toast("error", "Upload failed", e?.response?.data?.detail ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl animate-fade-up">
      <PageHeader
        title="Upload timesheets"
        subtitle="Runs the exact same pipeline as accepting an email. Weekly or 15-day sheets for the same month merge automatically into one record."
      />

      <Card className="p-6">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 transition-colors",
            dragging
              ? "border-brand-500 bg-brand-50"
              : "border-slate-300 bg-slate-50/60 hover:border-brand-400 hover:bg-brand-50/40"
          )}
        >
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-100">
            <UploadCloud className="h-7 w-7 text-brand-600" />
          </div>
          <div className="text-center">
            <p className="text-sm font-semibold text-slate-700">
              Drop timesheets here, or <span className="text-brand-600">browse</span>
            </p>
            <p className="mt-1 text-xs text-slate-400">
              PDF · DOCX · XLSX · PNG/JPG · EML — multiple files per month welcome (weekly / 15-day sheets)
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.eml"
            className="hidden"
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {queue.length > 0 && (
          <div className="mt-5">
            <div className="space-y-2">
              {queue.map((f) => (
                <div
                  key={f.name}
                  className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2"
                >
                  <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{f.name}</span>
                  <span className="text-xs text-slate-400">{formatBytes(f.size)}</span>
                  <button
                    onClick={() => setQueue((q) => q.filter((x) => x.name !== f.name))}
                    className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-500"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
            {busy && (
              <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-slate-200">
                <div
                  className="h-full rounded-full bg-brand-600 transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setQueue([])} disabled={busy}>
                Clear
              </Button>
              <Button onClick={run} disabled={busy}>
                <UploadCloud className="h-4 w-4" />
                {busy ? "Processing…" : `Process ${queue.length} file${queue.length > 1 ? "s" : ""}`}
              </Button>
            </div>
          </div>
        )}
      </Card>

      {results.length > 0 && (
        <Card className="mt-6">
          <div className="border-b border-slate-100 px-5 py-3.5">
            <h2 className="text-sm font-bold text-slate-800">Results</h2>
          </div>
          <div className="divide-y divide-slate-100">
            {results.map((r) => (
              <div key={r.pipeline_id} className="flex items-start gap-3 px-5 py-3.5">
                {r.status === "success" ? (
                  <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />
                ) : r.status === "needs_review" ? (
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                ) : (
                  <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-500" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold text-slate-800">{r.filename}</p>
                    <FailureChip code={r.failure_code} label={r.failure_code} />
                  </div>
                  <p className="mt-0.5 text-xs leading-5 text-slate-500">
                    {r.employee_name && (
                      <span className="font-medium text-slate-600">
                        {r.employee_name}
                        {r.month ? ` — ${MONTHS_LONG[r.month]} ${r.year}` : ""} ·{" "}
                      </span>
                    )}
                    {r.failure_detail ?? r.llm_summary ?? ""}
                  </p>
                  {r.record_id && (r.status === "success" || r.status === "needs_review") && (
                    <StoredFilesPreview recordId={r.record_id} />
                  )}
                </div>
                {r.record_id ? (
                  <Link
                    to={`/records/${r.record_id}`}
                    className="flex shrink-0 items-center gap-1 text-xs font-semibold text-brand-600 hover:text-brand-700"
                  >
                    View record <ExternalLink className="h-3.5 w-3.5" />
                  </Link>
                ) : (
                  <Link
                    to="/pipeline"
                    className="flex shrink-0 items-center gap-1 text-xs font-semibold text-rose-500 hover:text-rose-600"
                  >
                    See in pipeline <Layers className="h-3.5 w-3.5" />
                  </Link>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
