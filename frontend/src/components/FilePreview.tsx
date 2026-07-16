import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  ChevronLeft, ChevronRight, Download, ExternalLink, FileText, Mail,
  Maximize2, Minimize2, Paperclip, X,
} from "lucide-react";
import { cn, formatBytes } from "../lib/utils";
import { isTinyImage } from "../lib/attachmentFilters";
import { downloadFile, isDocx, isEml, isPdf, isPreviewable, isXlsx, sanitizeEmailHtml, type PreviewFile } from "../lib/filePreview";
import { api, fetchEmlPreview, type EmlParsed } from "../api/client";
import { Spinner } from "./ui";

// ---------------------------------------------------------------------------
// EML viewer — Outlook-style rendering of .eml files
// ---------------------------------------------------------------------------

type AttachmentBlob = { filename: string; contentType: string; size: number; url: string; dataB64?: string };

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
          const safe = sanitizeEmailHtml(data.body_html);
          const b = URL.createObjectURL(new Blob([safe], { type: "text/html" }));
          bodyBlobRef.current = b;
          blobsRef.current.push(b);
        }

        // Build blob URLs for each attachment so they can be previewed/downloaded.
        // Tiny images (< MIN_IMAGE_KB) are signature logos/icons — hidden here
        // like everywhere else; documents of any size always show.
        const blobs: AttachmentBlob[] = data.attachments
          .filter((a) => a.data_b64 && !isTinyImage(a.content_type, a.size))
          .map((a) => {
            const binary = atob(a.data_b64!);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            const u = URL.createObjectURL(new Blob([bytes], { type: a.content_type }));
            blobsRef.current.push(u);
            return { filename: a.filename, contentType: a.content_type, size: a.size, url: u, dataB64: a.data_b64 };
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
                  onClick={() => setAttPreview({
                    url: a.url,
                    filename: a.filename,
                    contentType: a.contentType,
                    renderUpload: a.dataB64 ? {
                      filename: a.filename,
                      contentType: a.contentType,
                      dataB64: a.dataB64,
                    } : null,
                  })}
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
// DOCX viewer — renders .docx in-browser via docx-preview
// ---------------------------------------------------------------------------

export function DocxPreviewPane({ fileUrl }: { fileUrl: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setErr(null);
    setLoading(true);
    if (!containerRef.current) return;
    const target = containerRef.current;

    Promise.all([
      import("docx-preview"),
      fetch(fileUrl).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.blob();
      }),
    ])
      .then(([{ renderAsync }, blob]) => {
        if (!alive) return;
        return renderAsync(blob, target, undefined, {
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          useBase64URL: true,
        });
      })
      .then(() => { if (alive) setLoading(false); })
      .catch(() => { if (alive) setErr("Could not render DOCX file."); });

    return () => { alive = false; };
  }, [fileUrl]);

  if (err) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-rose-500">{err}</div>
    );
  }

  return (
    <div className="relative h-full overflow-auto bg-slate-100">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-100">
          <Spinner className="h-6 w-6" />
        </div>
      )}
      <div
        ref={containerRef}
        className="docx-preview-container mx-auto max-w-4xl bg-white shadow-sm"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Server render pane — DOCX/XLSX rendered to page images on the backend.
// Works in every browser; the original file stays downloadable. Pages via
// ?page=N with the total in the X-Page-Count response header.
// ---------------------------------------------------------------------------

export function ServerRenderPane({
  renderUrl,
  renderUpload,
}: {
  renderUrl?: string | null;
  renderUpload?: { filename: string; contentType: string; dataB64: string } | null;
}) {
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [img, setImg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { setPage(1); }, [renderUrl, renderUpload?.filename]);

  useEffect(() => {
    let alive = true;
    let blobUrl: string | null = null;
    setImg(null);
    setErr(null);
    const run = async () => {
      if (renderUrl) {
        const sep = renderUrl.includes("?") ? "&" : "?";
        const r = await fetch(`${renderUrl}${sep}page=${page}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const count = Number(r.headers.get("X-Page-Count") || "1");
        const blob = await r.blob();
        if (!alive) return;
        setPages(Number.isFinite(count) && count > 0 ? count : 1);
        blobUrl = URL.createObjectURL(blob);
        setImg(blobUrl);
        return;
      }
      if (renderUpload) {
        const binary = atob(renderUpload.dataB64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        const form = new FormData();
        form.append(
          "file",
          new Blob([bytes], { type: renderUpload.contentType || "application/octet-stream" }),
          renderUpload.filename
        );
        const r = await api.post("/files/render-upload", form, {
          params: { page },
          responseType: "blob",
        });
        const count = Number(r.headers["x-page-count"] || "1");
        if (!alive) return;
        setPages(Number.isFinite(count) && count > 0 ? count : 1);
        blobUrl = URL.createObjectURL(r.data);
        setImg(blobUrl);
        return;
      }
      throw new Error("No render source");
    };
    run().catch(() => alive && setErr("Could not render this file. Use Download to open the original."));
    return () => {
      alive = false;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [renderUrl, renderUpload, page]);

  if (err) {
    return <div className="flex h-full items-center justify-center px-6 text-center text-sm text-rose-500">{err}</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-auto bg-slate-100 p-3">
        {img ? (
          <img src={img} alt={`page ${page}`} className="mx-auto block max-w-full rounded-lg bg-white shadow-sm" />
        ) : (
          <div className="flex h-full items-center justify-center"><Spinner className="h-6 w-6" /></div>
        )}
      </div>
      {pages > 1 && (
        <div className="flex shrink-0 items-center justify-center gap-3 border-t border-slate-200 bg-white py-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-xs font-semibold text-slate-600">Page {page} / {pages}</span>
          <button
            type="button"
            disabled={page >= pages}
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Image lightbox — true full-screen viewer (Outlook style). Dark backdrop,
// centered image, click to toggle fit ↔ actual size, Esc / click-out to close.
// ---------------------------------------------------------------------------

function ImageLightbox({ file, onClose }: { file: PreviewFile; onClose: () => void }) {
  const [zoomed, setZoomed] = useState(false);

  return createPortal(
    <div className="fixed inset-0 z-[60] flex flex-col bg-slate-950/95 animate-overlay-in">
      {/* Floating top bar */}
      <div className="absolute inset-x-0 top-0 z-10 flex items-center gap-2 bg-gradient-to-b from-black/60 to-transparent px-5 py-3">
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-white/90">
          {file.filename}
        </span>
        <button
          type="button"
          onClick={() => setZoomed((z) => !z)}
          className="rounded-lg p-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          title={zoomed ? "Fit to screen" : "Actual size"}
        >
          {zoomed ? <Minimize2 className="h-5 w-5" /> : <Maximize2 className="h-5 w-5" />}
        </button>
        <a
          href={file.url}
          download={file.filename}
          className="rounded-lg p-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          title="Download"
        >
          <Download className="h-5 w-5" />
        </a>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-lg p-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Image stage — click backdrop closes; click image toggles zoom */}
      <div
        className={cn(
          "flex min-h-0 flex-1 items-center justify-center p-6",
          zoomed ? "overflow-auto" : "overflow-hidden"
        )}
        onClick={onClose}
      >
        <img
          src={file.url}
          alt={file.filename}
          onClick={(e) => {
            e.stopPropagation();
            setZoomed((z) => !z);
          }}
          className={cn(
            "animate-scale-in select-none rounded-lg shadow-2xl transition-transform duration-200",
            zoomed
              ? "max-w-none cursor-zoom-out"
              : "max-h-full max-w-full cursor-zoom-in object-contain"
          )}
        />
      </div>
    </div>,
    document.body
  );
}

// ---------------------------------------------------------------------------
// Generic file preview modal (PDF / image / EML / DOCX)
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
  const docx = isDocx(file.filename, file.contentType);
  const xlsx = isXlsx(file.filename, file.contentType);
  const image = !pdf && !eml && !docx && !xlsx && isPreviewable(file.filename, file.contentType);
  // Server render-to-image for office docs when a render URL is available
  // (inbox attachments, pipeline raw copies). Original stays downloadable.
  const effectiveRenderUrl = file.renderUrl ?? null;
  const serverRender = !!effectiveRenderUrl && (docx || xlsx);

  // Images get a dedicated full-screen lightbox instead of the framed card.
  if (image) return <ImageLightbox file={file} onClose={onClose} />;

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
        <div className={cn(
          "min-h-0 flex-1 overflow-auto",
          eml || docx || serverRender ? "bg-white" : "bg-slate-100 p-2",
        )}>
          {eml ? (
            <EmlPreviewPane fileUrl={file.url} filename={file.filename} />
          ) : serverRender ? (
            <ServerRenderPane renderUrl={effectiveRenderUrl} renderUpload={file.renderUpload} />
          ) : docx ? (
            <DocxPreviewPane fileUrl={file.url} />
          ) : xlsx ? (
            <div className="flex h-full min-h-[40vh] flex-col items-center justify-center gap-3 text-slate-500">
              <FileText className="h-10 w-10 text-slate-300" />
              <p className="text-sm">No inline preview for this spreadsheet here.</p>
              <a
                href={file.url}
                download={file.filename}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                <Download className="h-4 w-4" /> Download original
              </a>
            </div>
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
        ? <Mail className="h-5 w-5 shrink-0 text-brand-500" />
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
