/**
 * Vault download with a year-wise dropdown + a real-time progress popup.
 *
 * Why a year picker: the whole vault can grow to tens of GB. Downloading a
 * single calendar year (all managers/employees/months filed in that year) keeps
 * each ZIP bounded (≈ ≤5 GB at ~600 employees) and lets people grab exactly the
 * period they need.
 *
 * How the download runs:
 *  - The ZIP is STREAMED from the backend (it starts immediately — no waiting
 *    for the server to build the whole archive).
 *  - Where the browser supports the File System Access API (Chromium/Edge), we
 *    stream straight to the chosen file on disk and show a live progress bar —
 *    memory stays flat no matter how big the archive is.
 *  - Otherwise we fall back to the browser's native download (its own progress
 *    indicator), so it still works everywhere.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Loader2, CheckCircle2, XCircle } from "lucide-react";
import {
  fetchVaultYears,
  fetchDownloadSize,
  scopedZipUrl,
} from "../api/client";
import { Button, Modal, Select } from "./ui";
import { formatBytes } from "../lib/utils";

type Phase = "idle" | "preparing" | "downloading" | "done" | "error" | "native";

function hasFileSystemAccess(): boolean {
  return typeof (window as any).showSaveFilePicker === "function";
}

export function VaultDownload({ manager }: { manager: string | null }) {
  const { data: years } = useQuery({ queryKey: ["vault-years"], queryFn: fetchVaultYears });
  const [year, setYear] = useState<string>("all"); // "all" | "<year>"
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [received, setReceived] = useState(0);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState<string>("");

  const scope = {
    manager: manager ?? undefined,
    year: year === "all" ? undefined : Number(year),
  };

  const scopeLabel =
    (manager ? `${manager} · ` : "") + (year === "all" ? "all years" : year);
  const suggestedName =
    [manager, year === "all" ? "all" : year, "timesheets"]
      .filter(Boolean)
      .join("_")
      .replace(/[^\w.-]+/g, "_") + ".zip";

  const pct = total > 0 ? Math.min(99, Math.round((received / total) * 100)) : 0;

  async function start() {
    // No File System Access API → native browser download (its own progress).
    if (!hasFileSystemAccess()) {
      setPhase("native");
      setOpen(true);
      window.location.href = scopedZipUrl(scope);
      return;
    }

    let handle: any;
    try {
      handle = await (window as any).showSaveFilePicker({
        suggestedName,
        types: [{ description: "ZIP archive", accept: { "application/zip": [".zip"] } }],
      });
    } catch {
      return; // user cancelled the save dialog
    }

    setOpen(true);
    setPhase("preparing");
    setReceived(0);
    setTotal(0);
    setErr("");

    // Pre-fetch the total size so the bar is accurate (best-effort).
    try {
      const s = await fetchDownloadSize(scope);
      setTotal(s.bytes);
    } catch {
      /* unknown total — bar shows bytes transferred only */
    }

    const writable = await handle.createWritable();
    try {
      setPhase("downloading");
      const resp = await fetch(scopedZipUrl(scope));
      if (!resp.ok || !resp.body) throw new Error(`Server returned ${resp.status}`);
      const reader = resp.body.getReader();
      let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        await writable.write(value);
        got += value.byteLength;
        setReceived(got);
      }
      await writable.close();
      setPhase("done");
    } catch (e: any) {
      try { await writable.abort(); } catch { /* ignore */ }
      setErr(e?.message ?? String(e));
      setPhase("error");
    }
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <Select
          value={year}
          onChange={(e) => setYear(e.target.value)}
          className="h-9"
          title="Choose a year to keep each download bounded"
        >
          <option value="all">All years</option>
          {(years ?? []).map((y) => (
            <option key={y.year} value={String(y.year)}>
              {y.year} · {formatBytes(y.bytes)}
            </option>
          ))}
        </Select>
        <Button variant="secondary" onClick={start}>
          <Download className="h-4 w-4" />
          Download ZIP
        </Button>
      </div>

      <Modal open={open} onClose={() => phase !== "downloading" && setOpen(false)}
             title="Download timesheets" subtitle={scopeLabel}>
        {phase === "native" ? (
          <div className="flex items-start gap-3 py-2 text-sm text-slate-600">
            <Download className="mt-0.5 h-5 w-5 text-brand-500" />
            <p>
              Your download has started in the browser. Large archives may take a
              while — you can watch progress in your browser’s downloads bar.
            </p>
          </div>
        ) : (
          <div className="py-2">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 font-medium text-slate-700">
                {phase === "done" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                ) : phase === "error" ? (
                  <XCircle className="h-4 w-4 text-rose-500" />
                ) : (
                  <Loader2 className="h-4 w-4 animate-spin text-brand-500" />
                )}
                {phase === "preparing" && "Preparing…"}
                {phase === "downloading" && "Downloading…"}
                {phase === "done" && "Saved to your device"}
                {phase === "error" && "Download failed"}
              </span>
              <span className="tabular-nums text-slate-500">
                {formatBytes(received)}
                {total > 0 && ` / ${formatBytes(total)}`}
              </span>
            </div>

            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className={
                  "h-full rounded-full transition-all " +
                  (phase === "error" ? "bg-rose-400" : "bg-brand-500")
                }
                style={{ width: `${phase === "done" ? 100 : pct}%` }}
              />
            </div>

            {phase === "error" && (
              <p className="mt-3 text-sm text-rose-600">{err}</p>
            )}
            {(phase === "done" || phase === "error") && (
              <div className="mt-4 flex justify-end">
                <Button size="sm" variant="secondary" onClick={() => setOpen(false)}>
                  Close
                </Button>
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
