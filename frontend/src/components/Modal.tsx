import { type ReactNode, useEffect } from "react";
import type { SourceFile, FileItem } from "../api/client";

export function Modal({
  open,
  title,
  onClose,
  children,
  width = "max-w-2xl",
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={`w-full ${width} max-h-[88vh] overflow-hidden rounded-2xl bg-white shadow-lift`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
          <h3 className="text-sm font-semibold text-ink">{title}</h3>
          <button onClick={onClose} className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-ink">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="max-h-[calc(88vh-56px)] overflow-auto p-5">{children}</div>
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal open={open} title={title} onClose={onClose} width="max-w-md">
      <p className="text-sm text-slate-600">{message}</p>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
          Cancel
        </button>
        <button
          onClick={() => {
            onConfirm();
            onClose();
          }}
          className={`rounded-lg px-4 py-2 text-sm font-semibold text-white ${danger ? "bg-rose-600 hover:bg-rose-700" : "bg-petrol-600 hover:bg-petrol-700"}`}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

export function FilePreview({ url, name, contentType }: { url: string; name: string; contentType: string }) {
  if (contentType.startsWith("image/")) {
    return (
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
        <img src={url} alt={name} className="mx-auto max-h-[60vh] w-full object-contain" />
      </div>
    );
  }
  if (contentType === "application/pdf") {
    return <object data={url} type="application/pdf" className="h-[60vh] w-full rounded-xl border border-slate-200" />;
  }
  if (contentType.startsWith("text/") || contentType.includes("json")) {
    return <iframe src={url} title={name} className="h-[55vh] w-full rounded-xl border border-slate-200 bg-white" />;
  }
  return (
    <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50 px-4 py-5 text-sm">
      <span className="text-slate-600">{name} — no inline preview.</span>
      <a href={url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-100">
        Open / download
      </a>
    </div>
  );
}

export function fileKindLabel(f: SourceFile | FileItem): string {
  if (f.name.endsWith(".json")) return "result";
  if (f.name.toLowerCase().includes("approval")) return "approval";
  return "timesheet";
}
