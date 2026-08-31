/**
 * Upload page session state — lifted OUT of the page component and into a
 * context mounted above the router's route-switch (see App.tsx), so
 * navigating to another page and back doesn't lose what you were doing.
 *
 * React Router only unmounts the ONE route component that stops matching —
 * everything wrapping the route switch (Shell, and now this provider) stays
 * mounted the whole session. A picked File, a roster analysis that just took
 * a real AI call to produce, an extraction run's live progress — none of
 * that should vanish just because the user clicked over to check the
 * Activity log and came back.
 *
 * This is in-memory only (by design — a File's bytes can't be serialised
 * into localStorage/sessionStorage, and re-selecting a file after a real
 * page reload is a one-click cost, not a lost-work one): a hard refresh or
 * closing the tab still clears it, same as before. What changes is that
 * ordinary in-app navigation no longer does.
 *
 * Every piece of state here already has an explicit way to clear it (the
 * queue's "Clear" button, the roster's "X" on the picked file, discarding a
 * pending review) — nothing new needed there, this just stops navigation
 * from being an accidental extra way to clear them.
 */
import { createContext, useContext, useState, type ReactNode } from "react";
import type { BulkStageResult, PipelineFile, RosterPreview, UploadResult } from "../api/client";
import { useExtractionStream, type ExtractionRun } from "../components/ExtractionActivity";

export type UploadMode = "files" | "bulk" | "manual";

interface UploadSessionValue {
  mode: UploadMode;
  setMode: (m: UploadMode) => void;

  // ---- "Upload files" (one file = one employee submission) ----
  queue: File[];
  setQueue: React.Dispatch<React.SetStateAction<File[]>>;
  busy: boolean;
  setBusy: (v: boolean) => void;
  manualResults: UploadResult[];
  setManualResults: React.Dispatch<React.SetStateAction<UploadResult[]>>;
  stagedQueue: PipelineFile[];
  setStagedQueue: React.Dispatch<React.SetStateAction<PipelineFile[]>>;
  pendingReview: PipelineFile[];
  setPendingReview: React.Dispatch<React.SetStateAction<PipelineFile[]>>;
  /** The live SSE progress panel's own state — lifted the same way, so a
   *  run started just before navigating away is still there (finished or
   *  still going) when the user comes back to /upload. */
  extractRun: ExtractionRun;

  // ---- "Bulk upload" (one roster sheet, many employees) ----
  bulkFile: File | null;
  setBulkFile: (f: File | null) => void;
  bulkMonth: number | "";
  setBulkMonth: (v: number | "") => void;
  bulkYear: number | "";
  setBulkYear: (v: number | "") => void;
  bulkPreview: RosterPreview | null;
  setBulkPreview: (p: RosterPreview | null) => void;
  bulkResult: BulkStageResult | null;
  setBulkResult: (r: BulkStageResult | null) => void;
  bulkAnalysing: boolean;
  setBulkAnalysing: (v: boolean) => void;
  bulkStaging: boolean;
  setBulkStaging: (v: boolean) => void;
  /** Reset just the bulk-roster picker — mirrors the file queue's own
   *  "Clear" button; the explicit way to drop a picked roster/analysis. */
  clearBulkRoster: () => void;
}

const Ctx = createContext<UploadSessionValue | null>(null);

export function UploadSessionProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<UploadMode>("files");

  const [queue, setQueue] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [manualResults, setManualResults] = useState<UploadResult[]>([]);
  const [stagedQueue, setStagedQueue] = useState<PipelineFile[]>([]);
  const [pendingReview, setPendingReview] = useState<PipelineFile[]>([]);
  const extractRun = useExtractionStream();

  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [bulkMonth, setBulkMonth] = useState<number | "">("");
  const [bulkYear, setBulkYear] = useState<number | "">("");
  const [bulkPreview, setBulkPreview] = useState<RosterPreview | null>(null);
  const [bulkResult, setBulkResult] = useState<BulkStageResult | null>(null);
  const [bulkAnalysing, setBulkAnalysing] = useState(false);
  const [bulkStaging, setBulkStaging] = useState(false);

  const clearBulkRoster = () => {
    setBulkFile(null);
    setBulkPreview(null);
    setBulkResult(null);
  };

  return (
    <Ctx.Provider
      value={{
        mode, setMode,
        queue, setQueue, busy, setBusy,
        manualResults, setManualResults,
        stagedQueue, setStagedQueue,
        pendingReview, setPendingReview,
        extractRun,
        bulkFile, setBulkFile,
        bulkMonth, setBulkMonth,
        bulkYear, setBulkYear,
        bulkPreview, setBulkPreview,
        bulkResult, setBulkResult,
        bulkAnalysing, setBulkAnalysing,
        bulkStaging, setBulkStaging,
        clearBulkRoster,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useUploadSession(): UploadSessionValue {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error("useUploadSession must be used within <UploadSessionProvider>");
  }
  return ctx;
}
