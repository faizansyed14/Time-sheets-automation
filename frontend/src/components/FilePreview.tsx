import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle, Download, ExternalLink, FileText, Mail,
  Maximize2, Minimize2, Paperclip, X, ZoomIn, ZoomOut, Scan,
} from "lucide-react";
import { cn, formatBytes } from "../lib/utils";
import { isTinyImage } from "../lib/attachmentFilters";
import { b64ToBytes, buildEmailHtmlDocument, downloadFile, isDocx, isEml, isPdf, isPreviewable, isXlsx, mimeFor, type PreviewFile } from "../lib/filePreview";
import { api, fetchEmlPreview, fetchEmlPreviewFromBytes, type EmlParsed } from "../api/client";
import { Spinner } from "./ui";
import { XlsxPreviewPane } from "./XlsxPreview";

// ---------------------------------------------------------------------------
// EML viewer — Outlook-style rendering of .eml files
// ---------------------------------------------------------------------------

type AttachmentBlob = { filename: string; contentType: string; size: number; url: string; dataB64?: string };

export function EmlPreviewPane({
  fileUrl, filename, dataB64,
}: {
  fileUrl: string;
  filename: string;
  /** Raw bytes for an email nested INSIDE another email, which has no URL of
   *  its own. When present it is parsed directly, so forwarded mail opens at
   *  any depth. */
  dataB64?: string | null;
}) {
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

    (dataB64
      ? fetchEmlPreviewFromBytes(filename, dataB64)
      : fetchEmlPreview(fileUrl))
      .then((data) => {
        if (!alive) return;
        setParsed(data);

        // Build HTML body blob URL (data: URIs from the server — no cid: refs).
        if (data.body_html) {
          if (bodyBlobRef.current) URL.revokeObjectURL(bodyBlobRef.current);
          const doc = buildEmailHtmlDocument(data.body_html);
          const b = URL.createObjectURL(new Blob([doc], { type: "text/html" }));
          bodyBlobRef.current = b;
          blobsRef.current.push(b);
        }

        // Build blob URLs for each attachment so they can be previewed/downloaded.
        // Tiny images (< MIN_IMAGE_KB) are signature logos/icons — hidden here
        // like everywhere else; documents of any size always show.
        const blobs: AttachmentBlob[] = data.attachments
          .filter((a) => a.data_b64 && !isTinyImage(a.content_type, a.size))
          .map((a) => {
            const bytes = b64ToBytes(a.data_b64!);
            const mime = mimeFor(a.filename, a.content_type);
            const u = URL.createObjectURL(new Blob([bytes], { type: mime }));
            blobsRef.current.push(u);
            return {
              filename: a.filename,
              contentType: mime,
              size: a.size,
              url: u,
              dataB64: a.data_b64,
            };
          });
        setAttBlobs(blobs);
      })
      .catch(() => alive && setErr("Could not parse email file."));

    return () => {
      alive = false;
    };
  }, [fileUrl, filename, dataB64]);

  // Revoke all blob URLs when the component unmounts or the source changes.
  useEffect(() => {
    return () => {
      blobsRef.current.forEach((u) => URL.revokeObjectURL(u));
      blobsRef.current = [];
      bodyBlobRef.current = null;
    };
  }, [fileUrl, dataB64]);

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

        {(parsed.warnings?.length ?? 0) > 0 && (
          <div className="shrink-0 space-y-1 border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
            <p className="flex items-center gap-1.5 font-semibold">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              This is not the full conversation
            </p>
            {parsed.warnings!.map((w, i) => (
              <p key={i} className="leading-5">{w}</p>
            ))}
          </div>
        )}

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
// Server render pane — DOCX/XLSX rendered to page images on the backend.
// Works in every browser; the original file stays downloadable. Pages via
// ?page=N with the total in the X-Page-Count response header.
// ---------------------------------------------------------------------------

export function ServerRenderPane({
  renderUrl,
  renderUpload,
  sourceUrl,
  filename,
  contentType,
}: {
  renderUrl?: string | null;
  renderUpload?: { filename: string; contentType: string; dataB64: string } | null;
  /** When no renderUrl/renderUpload — fetch this URL and POST /files/render-upload. */
  sourceUrl?: string | null;
  filename?: string;
  contentType?: string | null;
}) {
  const blobsRef = useRef<string[]>([]);
  const [imgs, setImgs] = useState<string[]>([]);
  const [pageCount, setPageCount] = useState<number>(1);
  const [err, setErr] = useState<string | null>(null);
  // Page images are rasterised server-side (browsers can't render DOCX/XLSX
  // natively), so zoom is applied here as a width scale — 100% = fit width.
  const [zoom, setZoom] = useState<number>(100);
  const ZOOM_MIN = 40;
  const ZOOM_MAX = 400;
  const step = (delta: number) =>
    setZoom((z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z + delta)));

  useEffect(() => {
    let alive = true;
    setImgs([]);
    setPageCount(1);
    setErr(null);

    // Revoke any old blobs from the previous render.
    blobsRef.current.forEach((u) => URL.revokeObjectURL(u));
    blobsRef.current = [];

    const uploadBytes = renderUpload ? b64ToBytes(renderUpload.dataB64) : null;
    const uploadBlob = uploadBytes
      ? new Blob([uploadBytes], { type: renderUpload?.contentType || "application/octet-stream" })
      : null;
    let sourcePreparedBlob: Blob | null = null;

    const fetchPage = async (p: number) => {
      if (renderUrl) {
        const sep = renderUrl.includes("?") ? "&" : "?";
        const r = await fetch(`${renderUrl}${sep}page=${p}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const blob = await r.blob();
        const c = p === 1 ? Number(r.headers.get("X-Page-Count") || "1") : null;
        return { blobUrl: URL.createObjectURL(blob), count: c };
      }

      const postForm = async (form: FormData) => {
        const r = await api.post("/files/render-upload", form, {
          params: { page: p },
          responseType: "blob",
        });
        const c = p === 1 ? Number(r.headers["x-page-count"] || "1") : null;
        return { blobUrl: URL.createObjectURL(r.data), count: c };
      };

      if (renderUpload) {
        const form = new FormData();
        form.append("file", uploadBlob || new Blob([], { type: renderUpload.contentType || "application/octet-stream" }), renderUpload.filename);
        return postForm(form);
      }

      if (sourceUrl) {
        if (!sourcePreparedBlob) {
          const r = await fetch(sourceUrl);
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const blob = await r.blob();
          // Prefer declared content type when the blob type is empty/octet-stream.
          sourcePreparedBlob = (contentType && (!blob.type || blob.type === "application/octet-stream"))
            ? new Blob([blob], { type: contentType })
            : blob;
        }
        const form = new FormData();
        form.append("file", sourcePreparedBlob, filename || "file");
        return postForm(form);
      }

      throw new Error("No render source");
    };

    const run = async () => {
      const first = await fetchPage(1);
      if (!alive) return;

      const count = Number.isFinite(first.count as number) && (first.count as number) > 0
        ? (first.count as number)
        : 1;
      setPageCount(count);

      const out: string[] = [];
      blobsRef.current.push(first.blobUrl);
      out.push(first.blobUrl);
      setImgs([...out]);

      for (let p = 2; p <= count; p++) {
        const res = await fetchPage(p);
        if (!alive) return;
        blobsRef.current.push(res.blobUrl);
        out.push(res.blobUrl);
        // Progressive render while pages arrive.
        setImgs([...out]);
      }
    };

    run().catch(() => alive && setErr("Could not render this file. Use Download to open the original."));

    return () => {
      alive = false;
      blobsRef.current.forEach((u) => URL.revokeObjectURL(u));
      blobsRef.current = [];
    };
  }, [renderUrl, renderUpload, sourceUrl, filename, contentType]);

  if (err) {
    return <div className="flex h-full items-center justify-center px-6 text-center text-sm text-rose-500">{err}</div>;
  }

  return (
    <div className="flex h-full flex-col">
      {/* Zoom toolbar — DOCX/XLSX page images have no native viewer, so this
          gives the zoom in/out the browser's own PDF viewer provides. */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-1.5">
        <span className="text-[11px] font-medium text-slate-400">
          {pageCount > 1 ? `${pageCount} pages` : "1 page"}
        </span>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => step(-20)}
            disabled={zoom <= ZOOM_MIN}
            title="Zoom out"
            className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 disabled:opacity-40"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setZoom(100)}
            title="Fit width (reset)"
            className="min-w-[3.25rem] rounded-md px-2 py-1 text-xs font-semibold text-slate-600 transition-colors hover:bg-slate-100"
          >
            {zoom}%
          </button>
          <button
            type="button"
            onClick={() => step(20)}
            disabled={zoom >= ZOOM_MAX}
            title="Zoom in"
            className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 disabled:opacity-40"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setZoom(100)}
            title="Fit to width"
            className="ml-0.5 rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
          >
            <Scan className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-slate-100 p-3">
        {imgs.length ? (
          <div className="space-y-3">
            {imgs.map((u, i) => (
              <img
                key={u}
                src={u}
                alt={`page ${i + 1} / ${pageCount}`}
                style={{ width: `${zoom}%` }}
                className="mx-auto block rounded-lg bg-white shadow-sm object-contain"
              />
            ))}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center"><Spinner className="h-6 w-6" /></div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generic preview-by-type: EML pane, PDF iframe, DOCX/XLSX server page
// images, plain <img> for pictures, download card otherwise.
//
// Shared by every "preview a file I already have a source for" call site —
// Inbox attachments, the vault, pipeline raw copies, and a File picked in a
// form but not yet uploaded (Manual Entry / Compare & Fix's own attach
// picker). Those call sites used to carry two near-identical
// implementations that only differed in how the DOCX/XLSX bytes reached the
// server (a hosted renderUrl vs local File bytes) and in what happens for an
// EML that has no server-side parser to call yet.
// ---------------------------------------------------------------------------

export function SourcePreview({
  url, renderUrl, renderUpload, name, ct, emlUnavailable,
}: {
  url: string;
  renderUrl?: string | null;
  /** DOCX/XLSX bytes not yet uploaded anywhere (e.g. a File picked in a form) —
   *  passed straight through to ServerRenderPane. */
  renderUpload?: { filename: string; contentType: string; dataB64: string } | null;
  name: string;
  ct: string;
  /** True for a local file with no server copy yet — .eml can't be parsed
   *  until it exists somewhere the server can read it, so show a
   *  "preview after save" placeholder instead of trying EmlPreviewPane. */
  emlUnavailable?: boolean;
}) {
  if (isEml(name, ct)) {
    if (emlUnavailable) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 rounded-lg border border-slate-200 bg-white p-8 text-center">
          <FileText className="h-10 w-10 text-slate-300" />
          <p className="font-medium text-slate-700">{name}</p>
          <p className="text-xs text-slate-400">
            EML preview is available after the record is saved.
          </p>
        </div>
      );
    }
    return (
      <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
        <EmlPreviewPane fileUrl={url} filename={name} />
      </div>
    );
  }
  if (isPdf(name, ct)) {
    // Prefer the browser's own PDF viewer (zoom, search, print) — the URL
    // carries its own auth token, so the iframe loads it directly. Server
    // page-images are only the fallback when there is no raw-bytes URL.
    if (url) {
      return (
        <iframe
          src={url}
          title={name}
          className="h-full w-full rounded-lg border border-slate-200 bg-white"
        />
      );
    }
    return (
      <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
        <ServerRenderPane
          renderUrl={renderUrl}
          renderUpload={renderUpload}
          filename={name}
          contentType={ct}
        />
      </div>
    );
  }
  if (isXlsx(name, ct)) {
    const useServerPages = !!renderUrl || !!renderUpload;
    if (useServerPages) {
      return (
        <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
          <ServerRenderPane
            renderUrl={renderUrl}
            renderUpload={renderUpload}
            sourceUrl={!renderUrl && !renderUpload ? url : null}
            filename={name}
            contentType={ct}
          />
        </div>
      );
    }
    // Fallback: real spreadsheet grid (only when server render source missing).
    return (
      <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
        <XlsxPreviewPane url={url} />
      </div>
    );
  }
  if (isDocx(name, ct)) {
    return (
      <div className="h-full overflow-hidden rounded-lg border border-slate-200 bg-white">
        <ServerRenderPane
          renderUrl={renderUrl}
          renderUpload={renderUpload}
          sourceUrl={!renderUrl && !renderUpload ? url : null}
          filename={name}
          contentType={ct}
        />
      </div>
    );
  }
  if (isPreviewable(name, ct)) {
    return (
      <img
        src={url}
        alt={name}
        className="mx-auto block max-h-full max-w-full rounded-lg object-contain"
      />
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-500">
      <FileText className="h-10 w-10 text-slate-300" />
      <p className="text-sm">{name} cannot be previewed inline.</p>
      <a
        href={url}
        download={name}
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
      >
        <Download className="h-4 w-4" /> Download
      </a>
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
  const xlsx = isXlsx(file.filename, file.contentType);
  // DOCX only — XLSX has its own dedicated grid renderer below, not the
  // server page-image path (which is what stitched a whole workbook into one
  // unreadable strip before it got its own component).
  const office = isDocx(file.filename, file.contentType);
  const image = !pdf && !eml && !xlsx && !office && isPreviewable(file.filename, file.contentType);
  // PDF → the browser's own viewer (zoom, search, page nav, print) whenever we
  // have the raw-bytes URL. The token rides in the query string, so the iframe
  // can load it; only fall back to server page-images when there is no URL.
  const usePdfIframe = pdf && !!file.url;
  // DOCX/XLSX (and a URL-less PDF) → server page images.
  const useServerPages = !usePdfIframe
    && (office || xlsx || pdf) && (!!file.renderUrl || !!file.renderUpload);
  // DOCX without a dedicated render source: fetch blob/url → render-upload.
  const useOfficeFetch = office && !useServerPages && !!file.url;

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
            onClick={(e) => e.stopPropagation()}
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
          eml || xlsx || useServerPages || useOfficeFetch ? "bg-white" : "bg-slate-100 p-2",
        )}>
          {eml ? (
            <EmlPreviewPane
              fileUrl={file.url}
              filename={file.filename}
              dataB64={file.renderUpload?.dataB64}
            />
          ) : useServerPages || useOfficeFetch ? (
            <ServerRenderPane
              renderUrl={file.renderUrl}
              renderUpload={file.renderUpload}
              sourceUrl={!file.renderUrl && !file.renderUpload ? file.url : null}
              filename={file.filename}
              contentType={mimeFor(file.filename, file.contentType)}
            />
          ) : usePdfIframe ? (
            <iframe
              src={file.url}
              title={file.filename}
              className="h-full min-h-[70vh] w-full rounded-lg bg-white"
            />
          ) : xlsx ? (
            <XlsxPreviewPane url={file.url} />
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
