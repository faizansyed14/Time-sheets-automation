/**
 * Vault download with year + month + "pick specific employees" controls, and a
 * real-time progress popup.
 *
 * Why the filters: the whole vault can grow to tens of GB, and a manager can
 * have a large team. Narrowing by year/month keeps each ZIP bounded, and
 * picking 1 or a few employees means you're never forced to download an
 * entire team just to get one or two people's files.
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
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Loader2, CheckCircle2, XCircle, Users, ChevronDown, Search, X } from "lucide-react";
import {
  fetchVaultYears,
  fetchDownloadSize,
  listFileEmployees,
  scopedZipUrl,
} from "../api/client";
import { Button, Modal, Select } from "./ui";
import { formatBytes } from "../lib/utils";

type Phase = "idle" | "preparing" | "downloading" | "done" | "error" | "native";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function hasFileSystemAccess(): boolean {
  return typeof (window as any).showSaveFilePicker === "function";
}

/** Compact "pick employees" popover — only meaningful once a manager is
 * chosen (employee names are manager-scoped). Empty selection = everyone. */
function EmployeePicker({
  names,
  selected,
  onChange,
}: {
  names: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = useMemo(
    () => names.filter((n) => n.toLowerCase().includes(q.toLowerCase())),
    [names, q]
  );
  const label =
    selected.length === 0
      ? "All employees"
      : selected.length === 1
        ? selected[0]
        : `${selected.length} employees`;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 hover:bg-slate-50"
        title="Choose one or a few employees instead of the whole team"
      >
        <Users className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        <span className="max-w-[140px] truncate">{label}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 w-64 rounded-lg border border-slate-200 bg-white p-2 shadow-pop">
          <div className="relative mb-1.5">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search employees…"
              autoFocus
              className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-2 text-xs focus:border-brand-400 focus:bg-white focus:outline-none"
            />
          </div>
          <div className="mb-1.5 flex items-center justify-between px-0.5 text-[11px]">
            <button type="button" className="font-medium text-brand-600 hover:underline" onClick={() => onChange(names)}>
              Select all
            </button>
            <span className="text-slate-400">{selected.length} of {names.length} selected</span>
            <button type="button" className="font-medium text-slate-500 hover:underline" onClick={() => onChange([])}>
              Clear
            </button>
          </div>
          <div className="max-h-48 space-y-0.5 overflow-y-auto">
            {filtered.length === 0 ? (
              <p className="px-2 py-3 text-center text-xs text-slate-400">No match.</p>
            ) : (
              filtered.map((n) => {
                const checked = selected.includes(n);
                return (
                  <label
                    key={n}
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        onChange(checked ? selected.filter((s) => s !== n) : [...selected, n])
                      }
                      className="h-3.5 w-3.5 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                    />
                    <span className="truncate">{n}</span>
                  </label>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function VaultDownload({ manager }: { manager: string | null }) {
  const { data: years } = useQuery({ queryKey: ["vault-years"], queryFn: fetchVaultYears });
  // Shares the cache Files.tsx's own employee list already populates for this
  // manager — no extra network round trip in the common case.
  const { data: employeeFolders } = useQuery({
    queryKey: ["files", "employees", manager],
    queryFn: () => listFileEmployees(manager!),
    enabled: !!manager,
  });
  const employeeNames = useMemo(
    () => (employeeFolders ?? []).map((e) => e.name).sort((a, b) => a.localeCompare(b)),
    [employeeFolders]
  );

  const [year, setYear] = useState<string>("all"); // "all" | "<year>"
  const [month, setMonth] = useState<string>("all"); // "all" | month name
  const [selectedEmployees, setSelectedEmployees] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [received, setReceived] = useState(0);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState<string>("");

  // Employee picks are manager-specific — drop them when the manager changes
  // (or clears) so a stale selection can't silently scope a later download.
  useEffect(() => setSelectedEmployees([]), [manager]);

  const scope = {
    manager: manager ?? undefined,
    year: year === "all" ? undefined : Number(year),
    month: month === "all" ? undefined : month,
    employees: selectedEmployees.length ? selectedEmployees : undefined,
  };

  const scopeBits = [
    manager,
    selectedEmployees.length === 1
      ? selectedEmployees[0]
      : selectedEmployees.length > 1
        ? `${selectedEmployees.length} employees`
        : null,
    month === "all" ? null : month,
    year === "all" ? (month === "all" ? "all years" : null) : year,
  ].filter(Boolean);
  const scopeLabel = scopeBits.length ? scopeBits.join(" · ") : "Everything in the vault";
  const suggestedName =
    [
      manager,
      selectedEmployees.length === 1 ? selectedEmployees[0] : selectedEmployees.length > 1 ? `${selectedEmployees.length}employees` : null,
      month === "all" ? null : month,
      year === "all" ? "all" : year,
      "timesheets",
    ]
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
      <div className="flex flex-wrap items-center gap-2">
        {manager && employeeNames.length > 0 && (
          <EmployeePicker names={employeeNames} selected={selectedEmployees} onChange={setSelectedEmployees} />
        )}
        <Select
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="h-9"
          title="Narrow to one month (every year, unless a year is also picked)"
        >
          <option value="all">All months</option>
          {MONTHS.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </Select>
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
        {selectedEmployees.length > 0 && (
          <button
            type="button"
            onClick={() => setSelectedEmployees([])}
            className="flex items-center gap-1 rounded-md bg-brand-50 px-2 py-1 text-[11px] font-semibold text-brand-700 hover:bg-brand-100"
            title="Clear employee selection"
          >
            <X className="h-3 w-3" /> {selectedEmployees.length} picked
          </button>
        )}
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
