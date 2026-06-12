import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Download, ExternalLink, FileText, X } from "lucide-react";
import { cn } from "../lib/utils";
import { downloadFile, isPdf, isPreviewable, type PreviewFile } from "../lib/filePreview";

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

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col p-3 sm:p-5">
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl bg-white shadow-pop">
        <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 bg-slate-50 px-4 py-3">
          <FileText className="h-4 w-4 shrink-0 text-slate-400" />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700">{file.filename}</span>
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
        <div className="min-h-0 flex-1 overflow-auto bg-slate-100 p-2">
          {pdf ? (
            <iframe src={file.url} title={file.filename} className="h-full min-h-[70vh] w-full rounded-lg bg-white" />
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

  return (
    <button
      type="button"
      onClick={() => (previewable ? onPreview(file) : downloadFile(file.url, file.filename))}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 text-left transition-colors hover:border-brand-300 hover:bg-brand-50/40",
        className
      )}
    >
      {icon ?? <FileText className="h-5 w-5 shrink-0 text-brand-500" />}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-slate-700">{file.filename}</span>
        {subtitle && <span className="block text-[11px] text-slate-400">{subtitle}</span>}
      </span>
      {meta}
      {previewable ? (
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-brand-500">Preview</span>
      ) : (
        <ExternalLink className="h-4 w-4 shrink-0 text-slate-300" />
      )}
    </button>
  );
}
