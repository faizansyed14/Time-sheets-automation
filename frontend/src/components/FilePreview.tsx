import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Download, ExternalLink, FileText, Mail, Paperclip, X } from "lucide-react";
import { cn, formatBytes } from "../lib/utils";
import { downloadFile, isEml, isPdf, isPreviewable, type PreviewFile } from "../lib/filePreview";
import { fetchEmlPreview, type EmlParsed } from "../api/client";
import { Spinner } from "./ui";

// ---------------------------------------------------------------------------
// EML viewer — Outlook-style rendering of .eml files
// ---------------------------------------------------------------------------

type AttachmentBlob = { filename: string; contentType: string; size: number; url: string };

export function EmlPreviewPane({ fileUrl, filename }: { fileUrl: string; filename: string }) {
  const [parsed, setParsed] = useState<EmlParsed | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [attPreview, setAttPreview] = useState<PreviewFile | null>(null);

  // All blob URLs created for this render — revoked together on cleanup.
  const blobsRef = useRef<string[]>([]);
  const bodyBlobRef = useRef<string | null>(null);
  const [attBlobs, setAttBlobs] = useState<AttachmentBlob[]>([]);

  useEffect(() => {
    let alive = true;
    setParsed(null);
    setErr(null);

    fetchEmlPreview(fileUrl)
      .then((data) => {
        if (!alive) return;
        setParsed(data);

        // Build HTML body blob URL.
        if (data.body_html) {
          if (bodyBlobRef.current) URL.revokeObjectURL(bodyBlobRef.current);
          const b = URL.createObjectURL(new Blob([data.body_html], { type: "text/html" }));
          bodyBlobRef.current = b;
          blobsRef.current.push(b);
        }

        // Build blob URLs for each attachment so they can be previewed/downloaded.
        const blobs: AttachmentBlob[] = data.attachments
          .filter((a) => a.data_b64)
          .map((a) => {
            const binary = atob(a.data_b64!);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            const u = URL.createObjectURL(new Blob([bytes], { type: a.content_type }));
            blobsRef.current.push(u);
            return { filename: a.filename, contentType: a.content_type, size: a.size, url: u };
          });
        setAttBlobs(blobs);
      })
      .catch(() => alive && setErr("Could not parse email file."));

    return () => {
      alive = false;
    };
  }, [fileUrl]);

  // Revoke all blob URLs when the component unmounts or fileUrl changes.
  useEffect(() => {
    return () => {
      blobsRef.current.forEach((u) => URL.revokeObjectURL(u));
      blobsRef.current = [];
      bodyBlobRef.current = null;
    };
  }, [fileUrl]);

  if (err) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-rose-500">{err}</div>
    );
  }

  if (!parsed) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  return (
    <>
      <div className="flex h-full flex-col overflow-hidden">
        {/* Email header — From / To / Subject / Date */}
        <div className="shrink-0 space-y-1.5 border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-700">
          <p className="flex gap-2">
            <span className="w-12 shrink-0 font-semibold text-slate-400">From</span>
            <span className="min-w-0 break-words">{parsed.from_ || "—"}</span>
          </p>
          {parsed.to && (
            <p className="flex gap-2">
              <span className="w-12 shrink-0 font-semibold text-slate-400">To</span>
              <span className="min-w-0 break-words">{parsed.to}</span>
            </p>
          )}
          <p className="flex gap-2">
            <span className="w-12 shrink-0 font-semibold text-slate-400">Subject</span>
            <span className="min-w-0 font-medium text-slate-800 break-words">
              {parsed.subject || "(no subject)"}
            </span>
          </p>
          <p className="flex gap-2">
            <span className="w-12 shrink-0 font-semibold text-slate-400">Date</span>
            <span>{parsed.date || "—"}</span>
          </p>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-hidden bg-white">
          {bodyBlobRef.current ? (
            <iframe
              key={bodyBlobRef.current}
              src={bodyBlobRef.current}
              title={filename}
              sandbox="allow-same-origin"
              className="h-full w-full border-0"
            />
          ) : (
            <pre className="h-full overflow-auto whitespace-pre-wrap p-4 font-sans text-sm leading-6 text-slate-700">
              {parsed.body_text || "(empty body)"}
            </pre>
          )}
        </div>

        {/* Attachments — clickable chips that open a full preview */}
        {attBlobs.length > 0 && (
          <div className="shrink-0 border-t border-slate-200 bg-slate-50 px-4 py-2.5">
            <p className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-400">
              <Paperclip className="h-3 w-3" />
              Attachments ({attBlobs.length})
            </p>
            <div className="flex flex-wrap gap-2">
              {attBlobs.map((a, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setAttPreview({ url: a.url, filename: a.filename, contentType: a.contentType })}
                  className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] text-slate-700 transition-colors hover:border-brand-300 hover:bg-brand-50/40"
                >
                  <FileText className="h-3.5 w-3.5 shrink-0 text-brand-500" />
                  <span className="max-w-[200px] truncate font-medium">{a.filename}</span>
                  <span className="text-slate-400">{formatBytes(a.size)}</span>
                  <span className="ml-0.5 font-semibold uppercase tracking-wide text-brand-500">
                    Preview
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Nested preview modal for attachments inside the EML */}
      <FilePreviewModal file={attPreview} onClose={() => setAttPreview(null)} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Generic file preview modal (PDF / image / EML)
// ---------------------------------------------------------------------------

export function FilePreviewModal({
  file,
  onClose,
}: {
  file: PreviewFile | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!file) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [file, onClose]);

  if (!file) return null;

  const pdf = isPdf(file.filename, file.contentType);
  const eml = isEml(file.filename, file.contentType);

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col p-3 sm:p-5">
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl bg-white shadow-pop">
        {/* Header bar */}
        <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 bg-slate-50 px-4 py-3">
          {eml ? (
            <Mail className="h-4 w-4 shrink-0 text-slate-400" />
          ) : (
            <FileText className="h-4 w-4 shrink-0 text-slate-400" />
          )}
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700">
            {file.filename}
          </span>
          <a
            href={file.url}
            download={file.filename}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-white hover:text-brand-600"
            title="Download"
          >
            <Download className="h-4 w-4" />
          </a>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close preview"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-white hover:text-slate-600"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className={cn("min-h-0 flex-1 overflow-auto", eml ? "bg-white" : "bg-slate-100 p-2")}>
          {eml ? (
            <EmlPreviewPane fileUrl={file.url} filename={file.filename} />
          ) : pdf ? (
            <iframe
              src={file.url}
              title={file.filename}
              className="h-full min-h-[70vh] w-full rounded-lg bg-white"
            />
          ) : (
            <img
              src={file.url}
              alt={file.filename}
              className="mx-auto block max-h-full min-h-[70vh] max-w-full rounded-lg object-contain"
            />
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

// ---------------------------------------------------------------------------
// File row with click-to-preview / click-to-download
// ---------------------------------------------------------------------------

export function PreviewableFileRow({
  file,
  onPreview,
  icon,
  subtitle,
  meta,
  className,
}: {
  file: PreviewFile;
  onPreview: (file: PreviewFile) => void;
  icon?: ReactNode;
  subtitle?: string;
  meta?: ReactNode;
  className?: string;
}) {
  const previewable = isPreviewable(file.filename, file.contentType);
  const eml = isEml(file.filename, file.contentType);

  return (
    <button
      type="button"
      onClick={() => (previewable ? onPreview(file) : downloadFile(file.url, file.filename))}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 text-left transition-colors hover:border-brand-300 hover:bg-brand-50/40",
        className
      )}
    >
      {icon ?? (eml
        ? <Mail className="h-5 w-5 shrink-0 text-violet-500" />
        : <FileText className="h-5 w-5 shrink-0 text-brand-500" />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-slate-700">{file.filename}</span>
        {subtitle && <span className="block text-[11px] text-slate-400">{subtitle}</span>}
      </span>
      {meta}
      {previewable ? (
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-brand-500">
          Preview
        </span>
      ) : (
        <ExternalLink className="h-4 w-4 shrink-0 text-slate-300" />
      )}
    </button>
  );
}
