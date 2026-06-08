import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { uploadTimesheets, MONTHS_LONG, type UploadResult } from "../api/client";
import { Pill } from "../components/ui";

export default function Upload() {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);

  const add = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
  };

  const run = async () => {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    setResults(null);
    try {
      const res = await uploadTimesheets(files);
      setResults(res);
      setFiles([]);
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Upload failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Upload Timesheets</h1>
        <p className="mt-1 text-sm text-slate-500">
          Manually upload sheets (PDF / DOCX / XLSX / image). They run the same pipeline as
          accepting an email — extract → validate → match → file → record.
        </p>
      </div>

      {/* dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          add(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-2xl border-2 border-dashed px-6 py-12 text-center transition ${
          drag ? "border-petrol-400 bg-petrol-50/60" : "border-slate-300 bg-white hover:border-slate-400"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.eml"
          className="hidden"
          onChange={(e) => add(e.target.files)}
        />
        <UploadIcon />
        <div className="mt-3 text-sm font-medium text-ink">Drop files here or click to browse</div>
        <div className="mt-1 text-xs text-slate-400">PDF, DOCX, XLSX, PNG, JPG, EML</div>
      </div>

      {files.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-panel">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Queued ({files.length})
          </div>
          <div className="space-y-1.5">
            {files.map((f, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <span className="truncate text-slate-700">{f.name}</span>
                <button
                  onClick={() => setFiles((p) => p.filter((_, idx) => idx !== i))}
                  className="ml-3 text-xs text-slate-400 hover:text-rose-600"
                >
                  remove
                </button>
              </div>
            ))}
          </div>
          <div className="mt-3 flex justify-end">
            <button
              onClick={run}
              disabled={busy}
              className="rounded-lg bg-petrol-600 px-5 py-2 text-sm font-semibold text-white transition hover:bg-petrol-700 disabled:opacity-50"
            >
              {busy ? "Processing…" : `Extract ${files.length} file(s)`}
            </button>
          </div>
        </div>
      )}

      {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}

      {results && (
        <div className="rounded-2xl border border-slate-200 bg-white shadow-panel">
          <div className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-ink">
            Results — {results.length} record(s) created
          </div>
          <div className="divide-y divide-slate-100">
            {results.map((r) => (
              <div key={r.record_id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3">
                <div>
                  <div className="text-sm font-medium text-ink">
                    {r.employee_name ?? "Unknown"}{" "}
                    <span className="font-mono text-xs text-slate-400">{r.employee_id ?? ""}</span>
                    {r.month > 0 && (
                      <span className="ml-2 text-xs text-slate-400">
                        {MONTHS_LONG[r.month]} {r.year}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">{r.llm_summary}</div>
                  {r.match_note && <div className="text-xs text-slate-400">{r.match_note}</div>}
                </div>
                {r.validation_status === "verified" ? <Pill tone="emerald">Verified</Pill> : <Pill tone="amber">Needs review</Pill>}
              </div>
            ))}
          </div>
          <div className="border-t border-slate-100 px-5 py-3 text-sm">
            <Link to="/" className="font-medium text-petrol-700 hover:underline">
              View on the dashboard →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

function UploadIcon() {
  return (
    <svg className="mx-auto text-slate-400" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5" /><path d="M12 3v12" />
    </svg>
  );
}
